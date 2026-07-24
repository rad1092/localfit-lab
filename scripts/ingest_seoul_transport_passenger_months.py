from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from typing import Any

import ingest_seoul_transport_accessibility_sources as transport
from ingest_common import parse_key_file, run_id


DEFAULT_LIVE_QUARTER_MONTHS = ["202601", "202602", "202603"]


def parse_months(text: str) -> list[str]:
    months = [part.strip() for part in re.split(r"[,;\s]+", text) if part.strip()]
    invalid = [month for month in months if not re.fullmatch(r"\d{6}", month)]
    if invalid:
        raise ValueError(f"YYYYMM 형식이 아닌 월이 있습니다: {invalid}")
    return months


def build_collection_plan(months: list[str], mode: str = "both") -> list[dict[str, Any]]:
    plan: list[dict[str, Any]] = []
    include_bus = mode in {"both", "bus"}
    include_subway = mode in {"both", "subway"}
    for month in months:
        if include_bus:
            plan.append(
                {
                    "mode": "bus",
                    "source_id": "seoul_bus_stop_passengers_hourly",
                    "service": "CardBusTimeNew",
                    "month": month,
                    "dataset_name": f"서울시 버스 정류장별 시간대 승하차 인원 정보 {month} 원응답",
                    "relative_dir": "bus_stop_passengers_hourly",
                    "spatial_unit": "버스정류장",
                    "area_code_type": "정류소ID+ARS-ID+노선번호",
                    "quality_note": f"{month} 버스 정류장별 시간대 승하차 원응답이다. 공식 접근성 후보 최신분기 보강용이며 실제 상권 방문자나 구매자 수로 표현하지 않는다.",
                }
            )
        if include_subway:
            plan.append(
                {
                    "mode": "subway",
                    "source_id": "seoul_subway_station_passengers_hourly",
                    "service": "CardSubwayTime",
                    "month": month,
                    "dataset_name": f"서울시 지하철 역별 시간대 승하차 인원 정보 {month} 원응답",
                    "relative_dir": "subway_station_passengers_hourly",
                    "spatial_unit": "지하철역/호선",
                    "area_code_type": "역명+호선",
                    "quality_note": f"{month} 지하철 역별 시간대 승하차 원응답이다. 공식 접근성 후보 최신분기 보강용이며 실제 상권 방문자나 구매자 수로 표현하지 않는다.",
                }
            )
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(
        description="서울시 버스/지하철 월별 승하차량을 지정 월만 수집한다. 기본값은 공식 최신분기 2026Q1 보강용 202601~202603이다."
    )
    parser.add_argument(
        "--months",
        default=",".join(DEFAULT_LIVE_QUARTER_MONTHS),
        help="수집할 YYYYMM 목록. 예: 202601,202602,202603",
    )
    parser.add_argument(
        "--mode",
        choices=["both", "bus", "subway"],
        default="both",
        help="수집 대상. 기본 both.",
    )
    parser.add_argument(
        "--run-date",
        default=datetime.now().strftime("%Y%m%d"),
        help="raw 저장 경로의 날짜 폴더. 기본 오늘 날짜.",
    )
    parser.add_argument("--page-size", type=int, default=1000)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="네트워크 호출 없이 수집 계획만 출력한다.",
    )
    args = parser.parse_args()

    months = parse_months(args.months)
    plan = build_collection_plan(months, args.mode)
    transport.RUN_DATE = args.run_date

    if args.dry_run:
        print(
            json.dumps(
                {
                    "decision": "DRY_RUN_NO_NETWORK",
                    "run_date": args.run_date,
                    "months": months,
                    "mode": args.mode,
                    "request_count": len(plan),
                    "plan": plan,
                    "next_after_success": [
                        "scripts/preprocess_rule_engine_transit_passenger_history_silver.py 재실행",
                        "scripts/preprocess_rule_engine_transit_accessibility_candidates.py 재실행",
                        "scripts/validate_transit_accessibility_official_promotion_readiness.py 재실행",
                        "scripts/validate_candidate_official_promotion_gate.py 재실행",
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    keys = parse_key_file()
    key = keys.get("seoul_key", "")
    if not key:
        raise RuntimeError("서울 열린데이터광장 키를 key.md에서 찾지 못했다.")

    transport.append_or_update_registry(transport.REGISTRY_ROWS)
    rid = run_id("seoul_transport_passenger_months")
    results = []
    for item in plan:
        result = transport.collect_seoul_month_api(
            rid=rid,
            key=key,
            source_id=item["source_id"],
            service=item["service"],
            month=item["month"],
            dataset_name=item["dataset_name"],
            relative_dir=item["relative_dir"],
            spatial_unit=item["spatial_unit"],
            area_code_type=item["area_code_type"],
            quality_note=item["quality_note"],
            page_size=args.page_size,
        )
        results.append(result)

    failed = [row for row in results if row.get("status") not in {"success", "partial"}]
    partial = [row for row in results if row.get("status") == "partial"]
    print(
        json.dumps(
            {
                "run_id": rid,
                "run_date": args.run_date,
                "months": months,
                "mode": args.mode,
                "result_count": len(results),
                "failed_count": len(failed),
                "partial_count": len(partial),
                "results": results,
                "next_after_success": [
                    "scripts/preprocess_rule_engine_transit_passenger_history_silver.py 재실행",
                    "scripts/preprocess_rule_engine_transit_accessibility_candidates.py 재실행",
                    "scripts/validate_transit_accessibility_official_promotion_readiness.py 재실행",
                    "scripts/validate_candidate_official_promotion_gate.py 재실행",
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
