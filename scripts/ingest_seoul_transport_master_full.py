from __future__ import annotations

import csv
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

from ingest_common import RAW_ROOT, parse_key_file, redact_url, run_id, write_raw, log_failure
from ingest_seoul_transport_accessibility_sources import (
    PROVIDER,
    RUN_DATE,
    append_caution_log,
    append_or_update_registry,
    fetch_api_with_retries,
    seoul_api_url,
)


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = RAW_ROOT / "source_registry.csv"


MASTER_SOURCES = [
    {
        "source_id": "seoul_bus_stop_location_file",
        "service": "busStopLocationXyInfo",
        "dataset_name": "서울시 버스정류소 위치정보 OpenAPI 전체 원응답",
        "relative_dir": "bus_stop_location_api",
        "spatial_unit": "버스정류장 좌표",
        "time_unit": "수시/파일 기준일",
        "area_code_type": "정류소ID+ARS-ID",
        "quality_notes_ko": "서울시 버스정류소 위치정보 API 전체 원응답이다. 파일 원본과 함께 보존해 좌표/정류소ID 검증에 사용한다.",
    },
    {
        "source_id": "seoul_subway_station_master",
        "service": "subwayStationMaster",
        "dataset_name": "서울시 역사마스터 정보 OpenAPI 전체 원응답",
        "relative_dir": "subway_station_master",
        "spatial_unit": "지하철역 좌표",
        "time_unit": "수시/마스터 기준일",
        "area_code_type": "역사ID+역명+호선",
        "quality_notes_ko": "서울시 역사마스터 전체 원응답이다. 지하철 승하차량을 공간 좌표와 결합하기 위한 기준 파일이다.",
    },
    {
        "source_id": "seoul_bus_route_node_master",
        "service": "masterRouteNode",
        "dataset_name": "서울시 노선 정류장마스터 정보 OpenAPI 전체 원응답",
        "relative_dir": "bus_route_node_master",
        "spatial_unit": "노선-정류장",
        "time_unit": "수시/마스터 기준일",
        "area_code_type": "노선ID+정류소ID+순서",
        "quality_notes_ko": "서울시 노선별 정류장 순서와 링크 거리 전체 원응답이다. 노선 다양성과 네트워크 연결성 보강 시 사용한다.",
    },
]


def update_registry_status() -> None:
    if not REGISTRY.exists():
        return
    with REGISTRY.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
        fields = f.reader.fieldnames if hasattr(f, "reader") else None
    # csv.DictReader does not expose fieldnames through the wrapped file object.
    with REGISTRY.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames or []
        rows = list(reader)
    for row in rows:
        if row.get("source_id") == "seoul_subway_station_master":
            row["current_status"] = "collected_raw"
            row["notes_ko"] = "2026-07-03 재시도에서 subwayStationMaster API 전체 수집이 성공했다. 역명/호선 중복과 좌표계는 후처리에서 검증한다."
        if row.get("source_id") == "seoul_bus_route_node_master":
            row["current_status"] = "collected_raw"
            row["notes_ko"] = "2026-07-03 masterRouteNode API 전체 수집이 성공했다. 1차 입지 점수에는 보조 지표로만 사용한다."
    with REGISTRY.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def collect_full_api(
    *,
    rid: str,
    key: str,
    source_id: str,
    service: str,
    dataset_name: str,
    relative_dir: str,
    spatial_unit: str,
    time_unit: str,
    area_code_type: str,
    quality_notes_ko: str,
    page_size: int = 1000,
) -> dict[str, Any]:
    first_url = seoul_api_url(key, service, 1, page_size)
    redacted_first = redact_url(first_url, extra_values=[key])
    try:
        status, body, code, msg, total, rows = fetch_api_with_retries(first_url, service, attempts=4)
        if code != "INFO-000":
            log_failure(
                run_id_value=rid,
                source_id=source_id,
                provider=PROVIDER,
                dataset_name=dataset_name,
                failure_type=code or "provider_error",
                failure_reason_ko=f"{service} 첫 페이지가 정상 응답이 아님: {msg}",
                next_action_ko="서울 열린데이터광장 서비스 상태와 서비스명을 확인한 뒤 재시도한다.",
                request_url_redacted=redacted_first,
            )
            return {"source_id": source_id, "service": service, "status": "failed", "code": code, "message": msg}

        pages = math.ceil(total / page_size) if total else 1
        collected_rows = len(rows)
        write_raw(
            run_id_value=rid,
            source_id=source_id,
            provider=PROVIDER,
            dataset_name=dataset_name,
            body=body,
            relative_path=f"{RUN_DATE}/seoul_open_data/transport/{relative_dir}/{service}_1_{page_size}.json",
            request_url_redacted=redacted_first,
            request_params={"service": service, "start": 1, "end": page_size, "key": "<redacted>"},
            http_status=status,
            provider_result_code=code,
            provider_result_message=msg,
            spatial_unit=spatial_unit,
            time_unit=time_unit,
            source_period=RUN_DATE,
            area_code_type=area_code_type,
            quality_notes_ko=f"{quality_notes_ko} list_total_count={total}, page_rows={len(rows)}.",
        )

        failures = 0
        for page_no in range(2, pages + 1):
            start = (page_no - 1) * page_size + 1
            end = min(page_no * page_size, total)
            url = seoul_api_url(key, service, start, end)
            redacted = redact_url(url, extra_values=[key])
            try:
                page_status, page_body, page_code, page_msg, _total, page_rows = fetch_api_with_retries(url, service, attempts=4)
                if page_code != "INFO-000":
                    raise RuntimeError(f"{page_code}: {page_msg}")
                collected_rows += len(page_rows)
                write_raw(
                    run_id_value=rid,
                    source_id=source_id,
                    provider=PROVIDER,
                    dataset_name=dataset_name,
                    body=page_body,
                    relative_path=f"{RUN_DATE}/seoul_open_data/transport/{relative_dir}/{service}_{start}_{end}.json",
                    request_url_redacted=redacted,
                    request_params={"service": service, "start": start, "end": end, "key": "<redacted>"},
                    http_status=page_status,
                    provider_result_code=page_code,
                    provider_result_message=page_msg,
                    spatial_unit=spatial_unit,
                    time_unit=time_unit,
                    source_period=RUN_DATE,
                    area_code_type=area_code_type,
                    quality_notes_ko=f"{quality_notes_ko} list_total_count={total}, page_rows={len(page_rows)}.",
                )
            except Exception as exc:
                failures += 1
                log_failure(
                    run_id_value=rid,
                    source_id=source_id,
                    provider=PROVIDER,
                    dataset_name=dataset_name,
                    failure_type=type(exc).__name__,
                    failure_reason_ko=f"{service} {start}-{end} 페이지 수집 실패: {exc}",
                    next_action_ko="실패 페이지만 재시도하고 동일 오류가 반복되면 서울 열린데이터광장 서비스 상태를 확인한다.",
                    request_url_redacted=redacted,
                )

        return {
            "source_id": source_id,
            "service": service,
            "status": "success" if failures == 0 else "partial",
            "total_count": total,
            "collected_rows": collected_rows,
            "pages": pages,
            "failures": failures,
        }
    except Exception as exc:
        log_failure(
            run_id_value=rid,
            source_id=source_id,
            provider=PROVIDER,
            dataset_name=dataset_name,
            failure_type=type(exc).__name__,
            failure_reason_ko=f"{service} 전체 수집 실패: {exc}",
            next_action_ko="서비스명과 일시 장애 여부를 확인하고 재시도한다.",
            request_url_redacted=redacted_first,
        )
        return {"source_id": source_id, "service": service, "status": "failed", "error": str(exc)}


def write_log(rid: str, results: list[dict[str, Any]]) -> None:
    path = RAW_ROOT / "run_logs" / "20260703_transport_master_full_ko.md"
    lines = [
        "# 2026-07-03 교통 마스터 원응답 전체 수집 기록",
        "",
        f"- 실행 ID: `{rid}`",
        "- 목적: 접근성 점수를 정류장/역 개수 수준이 아니라 좌표·승하차량·노선 연결성으로 계산할 수 있도록 마스터 원천을 보강한다.",
        "",
        "## 결과",
    ]
    for item in results:
        lines.append(f"- `{item.get('source_id')}` / `{item.get('service')}`: {json.dumps(item, ensure_ascii=False)}")
    lines.extend(
        [
            "",
            "## 판단",
            "- 버스정류소 위치정보는 파일 원본과 API 원응답을 둘 다 보존했다. 파일은 기준일이 명확하고, API는 최신 서비스 구조 검증에 유리하다.",
            "- 역사마스터 API가 재시도에서 정상화되어 지하철 승하차량과 역 좌표를 결합할 수 있는 원천이 확보되었다.",
            "- 노선 정류장마스터는 전체 89,159건 규모라 1차 점수식에는 필수는 아니지만, 향후 노선 다양성·연결성 보강 근거로 남긴다.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    keys = parse_key_file()
    key = keys["seoul_key"]
    if not key:
        raise RuntimeError("서울 열린데이터광장 키를 key.md에서 찾지 못했다.")
    rid = run_id("seoul_transport_master_full")
    results = [collect_full_api(rid=rid, key=key, **source) for source in MASTER_SOURCES]
    update_registry_status()
    append_caution_log()
    summary = {"run_id": rid, "created_at": datetime.now().isoformat(timespec="seconds"), "results": results}
    (RAW_ROOT / "run_logs" / f"{rid}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_log(rid, results)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
