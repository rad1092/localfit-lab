from __future__ import annotations

import json
import re
import urllib.parse
from datetime import datetime

from ingest_common import RAW_ROOT, http_get, log_failure, parse_key_file, redact_url, run_id, write_raw


RUN_DATE = "20260703"
PROVIDER = "서울열린데이터광장"

SERVICES = [
    {
        "source_id": "seoul_trade_area_boundary",
        "service": "TbgisTrdarRelm",
        "dataset_name": "서울 상권분석서비스 영역-상권 API 샘플",
        "score_axis": "공간기준",
        "spatial_unit": "상권",
        "time_unit": "기준연도/버전",
        "area_code_type": "상권코드",
    },
    {
        "source_id": "seoul_sales_trade_area",
        "service": "VwsmTrdarSelngQq",
        "dataset_name": "서울 상권분석서비스 추정매출-상권 API 샘플",
        "score_axis": "매출",
        "spatial_unit": "상권",
        "time_unit": "분기",
        "area_code_type": "상권코드+서비스업종코드",
    },
    {
        "source_id": "seoul_store_trade_area",
        "service": "VwsmTrdarStorQq",
        "dataset_name": "서울 상권분석서비스 점포-상권 API 샘플",
        "score_axis": "경쟁/상권환경",
        "spatial_unit": "상권",
        "time_unit": "분기/연",
        "area_code_type": "상권코드+서비스업종코드",
    },
    {
        "source_id": "seoul_floating_population_trade_area",
        "service": "VwsmTrdarFlpopQq",
        "dataset_name": "서울 상권분석서비스 길단위인구-상권 API 샘플",
        "score_axis": "수요",
        "spatial_unit": "상권",
        "time_unit": "분기/시간대",
        "area_code_type": "상권코드",
    },
    {
        "source_id": "seoul_resident_worker_population_trade_area",
        "service": "VwsmTrdarRepopQq",
        "dataset_name": "서울 상권분석서비스 상주인구-상권 API 샘플",
        "score_axis": "수요",
        "spatial_unit": "상권",
        "time_unit": "분기/연",
        "area_code_type": "상권코드",
    },
    {
        "source_id": "seoul_resident_worker_population_trade_area",
        "service": "VwsmTrdarWrcPopltnQq",
        "dataset_name": "서울 상권분석서비스 직장인구-상권 API 샘플",
        "score_axis": "수요",
        "spatial_unit": "상권",
        "time_unit": "분기/연",
        "area_code_type": "상권코드",
    },
    {
        "source_id": "seoul_trade_area_change_index",
        "service": "VwsmTrdarIxQq",
        "dataset_name": "서울 상권분석서비스 상권변화지표 API 샘플",
        "score_axis": "성장/안정성",
        "spatial_unit": "상권",
        "time_unit": "분기/연",
        "area_code_type": "상권코드",
    },
    {
        "source_id": "seoul_facility_trade_area",
        "service": "VwsmTrdarFcltyQq",
        "dataset_name": "서울 상권분석서비스 집객시설-상권 API 샘플",
        "score_axis": "접근성/유입",
        "spatial_unit": "상권",
        "time_unit": "기준연도",
        "area_code_type": "상권코드",
    },
]


def safe_service_filename(service: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", service)


def parse_seoul_response(service: str, body: bytes) -> tuple[str, str, int, int]:
    text = body.decode("utf-8", errors="replace")
    data = json.loads(text)
    if "RESULT" in data:
        result = data["RESULT"]
        return str(result.get("CODE", "")), str(result.get("MESSAGE", "")), 0, 0
    payload = data.get(service, {})
    result = payload.get("RESULT", {}) if isinstance(payload, dict) else {}
    rows = payload.get("row", []) if isinstance(payload, dict) else []
    total = payload.get("list_total_count", 0) if isinstance(payload, dict) else 0
    return str(result.get("CODE", "")), str(result.get("MESSAGE", "")), int(total or 0), len(rows or [])


def main() -> None:
    keys = parse_key_file()
    key = keys["seoul_key"]
    rid = run_id("seoul_core_samples")
    results = []
    for service in SERVICES:
        service_name = service["service"]
        url = f"http://openapi.seoul.go.kr:8088/{urllib.parse.quote(key)}/json/{service_name}/1/1000/"
        redacted = redact_url(url, extra_values=[key])
        try:
            status, body, _headers = http_get(url, timeout=60)
            result_code, result_msg, total_count, row_count = parse_seoul_response(service_name, body)
            if result_code and result_code not in {"INFO-000", "INFO-1000"}:
                raise RuntimeError(f"서울 OpenAPI 결과 오류 {result_code}: {result_msg}")
            path = write_raw(
                run_id_value=rid,
                source_id=service["source_id"],
                provider=PROVIDER,
                dataset_name=service["dataset_name"],
                body=body,
                relative_path=f"{RUN_DATE}/seoul_open_data/core_samples/{safe_service_filename(service_name)}_1_1000.json",
                request_url_redacted=redacted,
                request_params={"service": service_name, "start": 1, "end": 1000, "key": "<redacted>"},
                http_status=status,
                provider_result_code=result_code,
                provider_result_message=result_msg,
                spatial_unit=service["spatial_unit"],
                time_unit=service["time_unit"],
                boundary_version="서울 상권분석서비스 2026-07-03 기준 변경 확인 필요",
                area_code_type=service["area_code_type"],
                quality_notes_ko=f"{service['score_axis']} 점수축 갱신 가능성 확인용 첫 페이지 원본이다. 전체 수집 시 list_total_count={total_count}, row_count={row_count} 기준으로 페이징한다.",
            )
            results.append(
                {
                    "service": service_name,
                    "status": "success",
                    "path": str(path),
                    "total_count": total_count,
                    "row_count": row_count,
                    "result_code": result_code,
                }
            )
        except Exception as exc:
            log_failure(
                run_id_value=rid,
                source_id=service["source_id"],
                provider=PROVIDER,
                dataset_name=service["dataset_name"],
                failure_type=type(exc).__name__,
                failure_reason_ko=f"{service_name} 첫 페이지 수집 실패: {exc}",
                next_action_ko="서울 열린데이터광장 서비스명 변경 여부와 2026-07-03 기준 변경 공지를 확인하고 서비스명을 보정한다.",
                request_url_redacted=redacted,
            )
            results.append({"service": service_name, "status": "failed", "error": type(exc).__name__})

    summary = {"run_id": rid, "results": results, "created_at": datetime.now().isoformat(timespec="seconds")}
    log_path = RAW_ROOT / "run_logs" / f"{rid}.json"
    log_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
