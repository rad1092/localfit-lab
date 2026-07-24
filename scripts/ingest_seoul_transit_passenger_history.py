# -*- coding: utf-8 -*-
"""
서울 버스/지하철 승하차량 과거 월이력 raw 적재 스크립트.

목적:
  - 55번 검증에서 확정한 202101~202512 백테스트 필수 월을 월별로 수집한다.
  - 기존 20260703 단월 수집 스크립트와 달리, 월 범위·서비스·dry-run·skip-existing을 명시적으로 제어한다.
  - raw 원본과 manifest/failed 로그만 남긴다. silver/gold 전처리는 별도 검증 후 수행한다.

주의:
  - API 키는 key.md의 서울 열린데이터광장 key를 사용한다.
  - 기본값은 dry-run이다. 실제 적재는 --execute를 붙여야 한다.
  - 기본은 기존 raw 월 폴더가 있으면 건너뛴다.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any

from ingest_common import (
    RAW_ROOT,
    parse_key_file,
    redact_url,
    write_raw,
    log_failure,
)
from ingest_seoul_transport_accessibility_sources import (
    PROVIDER,
    fetch_api_with_retries,
    seoul_api_url,
)


ROOT = Path(__file__).resolve().parents[1]
RUN_DATE = datetime.now().strftime("%Y%m%d")

SERVICES = {
    "bus": {
        "source_id": "seoul_bus_stop_passengers_hourly",
        "service": "CardBusTimeNew",
        "dataset_name_template": "서울시 버스 정류장별 시간대 승하차 인원 정보 {month} 원응답",
        "relative_dir": "bus_stop_passengers_hourly",
        "spatial_unit": "버스정류장",
        "area_code_type": "정류소ID+ARS-ID+노선번호",
        "quality_note": "버스 정류장별 시간대 승하차 원응답이다. 55번 수집계획의 백테스트 월이력 보강 대상이다.",
        "page_size": 1000,
    },
    "subway": {
        "source_id": "seoul_subway_station_passengers_hourly",
        "service": "CardSubwayTime",
        "dataset_name_template": "서울시 지하철 역별 시간대 승하차 인원 정보 {month} 원응답",
        "relative_dir": "subway_station_passengers_hourly",
        "spatial_unit": "지하철역/호선",
        "area_code_type": "역명+호선",
        "quality_note": "지하철 역별 시간대 승하차 원응답이다. 55번 수집계획의 백테스트 월이력 보강 대상이다.",
        "page_size": 1000,
    },
}


def month_range(start_month: str, end_month: str) -> list[str]:
    if not re.fullmatch(r"\d{6}", start_month) or not re.fullmatch(r"\d{6}", end_month):
        raise ValueError("월 범위는 YYYYMM 형식이어야 합니다.")
    start_year = int(start_month[:4])
    start_num = int(start_month[4:6])
    end_year = int(end_month[:4])
    end_num = int(end_month[4:6])
    months: list[str] = []
    year = start_year
    month = start_num
    while (year, month) <= (end_year, end_num):
        months.append(f"{year}{month:02d}")
        month += 1
        if month == 13:
            year += 1
            month = 1
    return months


def parse_months(values: list[str]) -> list[str]:
    months: list[str] = []
    for value in values:
        value = value.strip()
        if not value:
            continue
        if "-" in value:
            start, end = value.split("-", 1)
            months.extend(month_range(start.strip(), end.strip()))
        else:
            if not re.fullmatch(r"\d{6}", value):
                raise ValueError(f"월 값이 YYYYMM 형식이 아닙니다: {value}")
            months.append(value)
    return sorted(set(months))


def unique_run_id(prefix: str) -> str:
    return datetime.now().strftime(f"%Y%m%d_%H%M%S_%f_{prefix}")


def existing_month_dirs(relative_dir: str) -> set[str]:
    found: set[str] = set()
    for date_dir in RAW_ROOT.glob("20??????"):
        base = date_dir / "seoul_open_data" / "transport" / relative_dir
        if not base.exists():
            continue
        for month_dir in base.iterdir():
            if month_dir.is_dir() and re.fullmatch(r"\d{6}", month_dir.name):
                found.add(month_dir.name)
    return found


def raw_page_exists(*, ingest_date: str, relative_dir: str, month: str, service: str, start: int, end: int) -> bool:
    path = RAW_ROOT / ingest_date / "seoul_open_data" / "transport" / relative_dir / month / f"{service}_{start}_{end}_{month}.json"
    return path.exists()


def collect_month(
    *,
    rid: str,
    key: str,
    ingest_date: str,
    service_key: str,
    month: str,
    page_limit: int | None,
    overwrite: bool,
    sleep_seconds: float,
) -> dict[str, Any]:
    spec = SERVICES[service_key]
    source_id = str(spec["source_id"])
    service = str(spec["service"])
    relative_dir = str(spec["relative_dir"])
    page_size = int(spec["page_size"])
    dataset_name = str(spec["dataset_name_template"]).format(month=month)
    first_url = seoul_api_url(key, service, 1, page_size, month)
    redacted_first = redact_url(first_url, extra_values=[key])

    if not overwrite and raw_page_exists(
        ingest_date=ingest_date,
        relative_dir=relative_dir,
        month=month,
        service=service,
        start=1,
        end=page_size,
    ):
        return {
            "source_id": source_id,
            "service": service,
            "month": month,
            "status": "skipped_existing_first_page",
        }

    try:
        status, body, code, msg, total, rows = fetch_api_with_retries(first_url, service)
        if code != "INFO-000":
            log_failure(
                run_id_value=rid,
                source_id=source_id,
                provider=PROVIDER,
                dataset_name=dataset_name,
                failure_type=code or "provider_error",
                failure_reason_ko=f"{service} {month} 첫 페이지가 정상 응답이 아님: {msg}",
                next_action_ko="월 파라미터와 서울 열린데이터광장 적재 여부를 확인하고 재시도한다.",
                request_url_redacted=redacted_first,
            )
            return {"source_id": source_id, "service": service, "month": month, "status": "failed", "code": code, "message": msg}

        pages = math.ceil(total / page_size) if total else 1
        target_pages = min(pages, page_limit) if page_limit else pages
        collected_rows = len(rows)
        failures = 0

        if page_limit is not None:
            write_raw(
                run_id_value=rid,
                source_id=source_id,
                provider=PROVIDER,
                dataset_name=f"{dataset_name} 스모크 원응답",
                body=body,
                relative_path=f"{ingest_date}/seoul_open_data/transport/probes/transit_passenger_history/{service}_{month}_1_{page_size}_smoke.json",
                request_url_redacted=redacted_first,
                request_params={"service": service, "start": 1, "end": page_size, "month": month, "key": "<redacted>", "smoke_only": True},
                http_status=status,
                provider_result_code=code,
                provider_result_message=msg,
                spatial_unit=str(spec["spatial_unit"]),
                time_unit="월/시간대 스모크",
                source_period=month,
                area_code_type=str(spec["area_code_type"]),
                quality_notes_ko=f"{spec['quality_note']} page_limit 스모크라 월별 raw 본폴더에 넣지 않는다. list_total_count={total}, page_rows={len(rows)}.",
            )
            return {
                "source_id": source_id,
                "service": service,
                "month": month,
                "status": "smoke_success",
                "total_count": total,
                "collected_rows": collected_rows,
                "pages_expected": pages,
                "pages_requested": target_pages,
                "failures": 0,
                "raw_scope": "probe_only",
            }

        write_raw(
            run_id_value=rid,
            source_id=source_id,
            provider=PROVIDER,
            dataset_name=dataset_name,
            body=body,
            relative_path=f"{ingest_date}/seoul_open_data/transport/{relative_dir}/{month}/{service}_1_{page_size}_{month}.json",
            request_url_redacted=redacted_first,
            request_params={"service": service, "start": 1, "end": page_size, "month": month, "key": "<redacted>"},
            http_status=status,
            provider_result_code=code,
            provider_result_message=msg,
            spatial_unit=str(spec["spatial_unit"]),
            time_unit="월/시간대",
            source_period=month,
            area_code_type=str(spec["area_code_type"]),
            quality_notes_ko=f"{spec['quality_note']} list_total_count={total}, page_rows={len(rows)}.",
        )

        for page_no in range(2, target_pages + 1):
            start = (page_no - 1) * page_size + 1
            end = min(page_no * page_size, total)
            if not overwrite and raw_page_exists(
                ingest_date=ingest_date,
                relative_dir=relative_dir,
                month=month,
                service=service,
                start=start,
                end=end,
            ):
                continue
            url = seoul_api_url(key, service, start, end, month)
            redacted = redact_url(url, extra_values=[key])
            try:
                page_status, page_body, page_code, page_msg, _total, page_rows = fetch_api_with_retries(url, service)
                if page_code != "INFO-000":
                    raise RuntimeError(f"{page_code}: {page_msg}")
                collected_rows += len(page_rows)
                write_raw(
                    run_id_value=rid,
                    source_id=source_id,
                    provider=PROVIDER,
                    dataset_name=dataset_name,
                    body=page_body,
                    relative_path=f"{ingest_date}/seoul_open_data/transport/{relative_dir}/{month}/{service}_{start}_{end}_{month}.json",
                    request_url_redacted=redacted,
                    request_params={"service": service, "start": start, "end": end, "month": month, "key": "<redacted>"},
                    http_status=page_status,
                    provider_result_code=page_code,
                    provider_result_message=page_msg,
                    spatial_unit=str(spec["spatial_unit"]),
                    time_unit="월/시간대",
                    source_period=month,
                    area_code_type=str(spec["area_code_type"]),
                    quality_notes_ko=f"{spec['quality_note']} list_total_count={total}, page_rows={len(page_rows)}.",
                )
            except Exception as exc:
                failures += 1
                log_failure(
                    run_id_value=rid,
                    source_id=source_id,
                    provider=PROVIDER,
                    dataset_name=dataset_name,
                    failure_type=type(exc).__name__,
                    failure_reason_ko=f"{service} {month} {start}-{end} 페이지 수집 실패: {exc}",
                    next_action_ko="실패 페이지만 재시도하고 동일 오류가 반복되면 서울 열린데이터광장 월별 적재 상태를 확인한다.",
                    request_url_redacted=redacted,
                )
            if sleep_seconds:
                time.sleep(sleep_seconds)

        return {
            "source_id": source_id,
            "service": service,
            "month": month,
            "status": "success" if failures == 0 and target_pages == pages else ("partial" if failures else "partial_page_limit"),
            "total_count": total,
            "collected_rows": collected_rows,
            "pages_expected": pages,
            "pages_requested": target_pages,
            "failures": failures,
        }
    except Exception as exc:
        log_failure(
            run_id_value=rid,
            source_id=source_id,
            provider=PROVIDER,
            dataset_name=dataset_name,
            failure_type=type(exc).__name__,
            failure_reason_ko=f"{service} {month} 수집 실패: {exc}",
            next_action_ko="서비스명, 월 파라미터, 서울 OpenAPI 일시 장애 여부를 확인하고 재시도한다.",
            request_url_redacted=redacted_first,
        )
        return {"source_id": source_id, "service": service, "month": month, "status": "failed", "error": str(exc)}


def build_plan(months: list[str], service_keys: list[str], skip_existing: bool) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for service_key in service_keys:
        relative_dir = str(SERVICES[service_key]["relative_dir"])
        existing = existing_month_dirs(relative_dir)
        for month in months:
            rows.append(
                {
                    "service_key": service_key,
                    "source_id": SERVICES[service_key]["source_id"],
                    "service": SERVICES[service_key]["service"],
                    "month": month,
                    "already_has_any_raw_month": month in existing,
                    "action": "skip_existing" if skip_existing and month in existing else "collect",
                }
            )
    return rows


def write_run_log(rid: str, summary: dict[str, Any]) -> None:
    log_dir = RAW_ROOT / "run_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / f"{rid}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# 서울 교통 승하차량 과거 월이력 raw 적재 기록",
        "",
        f"- 실행 ID: `{rid}`",
        f"- 실행 모드: `{summary['mode']}`",
        f"- 적재 기준일 폴더: `{summary['ingest_date']}`",
        f"- 월 수: {summary['month_count']}",
        f"- 서비스: {', '.join(summary['services'])}",
        f"- 결과: {summary['status_counts']}",
        "",
        "## 2보 전진 1보 후퇴",
        "",
        "1. 전진: 55번 수집계획의 월 목록을 기계적으로 받지 않고, 실행 전 skip/collect 계획으로 다시 펼쳤다.",
        "2. 전진: raw 원본과 manifest/failed 로그만 남기고, silver/gold 승격은 후속 검증으로 분리했다.",
            "3. 후퇴: page limit 스모크는 probes 폴더에만 저장하고 월별 raw 본폴더에는 넣지 않는다.",
        "4. 후퇴: 기존 월 raw가 있으면 기본적으로 다시 받지 않아 중복 manifest와 불필요 호출을 줄인다.",
        "5. 후퇴: 실패 월은 silent skip하지 않고 failed_downloads.csv에 남긴다.",
        "",
        "## 결과",
        "",
        "```json",
        json.dumps(summary, ensure_ascii=False, indent=2),
        "```",
    ]
    (log_dir / f"{rid}_ko.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="서울 버스/지하철 승하차량 과거 월이력 raw 적재")
    parser.add_argument("--months", nargs="+", required=True, help="YYYYMM 또는 YYYYMM-YYYYMM 범위. 예: 202101-202512")
    parser.add_argument("--services", default="bus,subway", help="bus,subway 중 쉼표 구분")
    parser.add_argument("--ingest-date", default=RUN_DATE, help="raw 적재 날짜 폴더. 기본: 오늘 YYYYMMDD")
    parser.add_argument("--execute", action="store_true", help="실제 API 호출/저장 실행. 없으면 dry-run")
    parser.add_argument("--page-limit", type=int, default=None, help="서비스/월별 최대 페이지 수. 스모크는 1 권장")
    parser.add_argument("--no-skip-existing", action="store_true", help="기존 raw 월이 있어도 다시 수집")
    parser.add_argument("--sleep-seconds", type=float, default=0.05, help="페이지 호출 사이 대기 시간")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    months = parse_months(args.months)
    service_keys = [item.strip() for item in args.services.split(",") if item.strip()]
    unknown = sorted(set(service_keys) - set(SERVICES))
    if unknown:
        raise ValueError(f"알 수 없는 서비스 키: {unknown}")
    skip_existing = not args.no_skip_existing
    plan = build_plan(months, service_keys, skip_existing)
    rid = unique_run_id("seoul_transit_passenger_history")

    if not args.execute:
        summary = {
            "run_id": rid,
            "mode": "dry_run",
            "ingest_date": args.ingest_date,
            "month_count": len(months),
            "services": service_keys,
            "planned_rows": len(plan),
            "collect_count": sum(1 for row in plan if row["action"] == "collect"),
            "skip_existing_count": sum(1 for row in plan if row["action"] == "skip_existing"),
            "plan": plan,
            "status_counts": {"dry_run": len(plan)},
        }
        write_run_log(rid, summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    key = parse_key_file().get("seoul_key", "")
    if not key:
        raise RuntimeError("key.md에서 서울 열린데이터광장 key를 찾지 못했습니다.")

    results: list[dict[str, Any]] = []
    for row in plan:
        if row["action"] == "skip_existing":
            results.append({**row, "status": "skipped_existing"})
            continue
        result = collect_month(
            rid=rid,
            key=key,
            ingest_date=args.ingest_date,
            service_key=str(row["service_key"]),
            month=str(row["month"]),
            page_limit=args.page_limit,
            overwrite=args.no_skip_existing,
            sleep_seconds=args.sleep_seconds,
        )
        results.append({**row, **result})

    status_counts: dict[str, int] = {}
    for result in results:
        status = str(result.get("status", "unknown"))
        status_counts[status] = status_counts.get(status, 0) + 1

    summary = {
        "run_id": rid,
        "mode": "execute",
        "ingest_date": args.ingest_date,
        "month_count": len(months),
        "services": service_keys,
        "page_limit": args.page_limit,
        "skip_existing": skip_existing,
        "status_counts": status_counts,
        "results": results,
    }
    write_run_log(rid, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
