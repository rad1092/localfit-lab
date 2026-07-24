from __future__ import annotations

import json
import math
import re
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any

from ingest_common import RAW_ROOT, http_get, log_failure, parse_key_file, redact_url, run_id, write_raw


RUN_DATE = "20260703"
PROVIDER = "서울열린데이터광장"
PAGE_SIZE = 1000
MAX_ATTEMPTS = 8
BACKOFF_SECONDS = [2, 5, 10, 20, 30, 45, 60, 90]

SERVICES = [
    {
        "source_id": "seoul_localdata_general_restaurant_license",
        "service": "LOCALDATA_072404",
        "dataset_name": "서울시 일반음식점 인허가 정보 전체 원응답",
        "total_count": 534748,
        "spatial_unit": "인허가 사업장",
        "time_unit": "매일/3일전 자료",
        "source_period": "API 전체 응답 기준",
        "boundary_version": "서울 열린데이터광장 2026-07-03 수집 기준",
        "area_code_type": "MGTNO+OPNSFTEAMCODE+좌표(EPSG:5174)",
        "quality_notes_ko": "재시도 수집분이다. 영업상태, 폐업일자, 면적, 업태, EPSG:5174 좌표를 보존한다.",
    },
    {
        "source_id": "seoul_localdata_rest_cafe_license",
        "service": "LOCALDATA_072405",
        "dataset_name": "서울시 휴게음식점 인허가 정보 전체 원응답",
        "total_count": 145977,
        "spatial_unit": "인허가 사업장",
        "time_unit": "매일/3일전 자료",
        "source_period": "API 전체 응답 기준",
        "boundary_version": "서울 열린데이터광장 2026-07-03 수집 기준",
        "area_code_type": "MGTNO+OPNSFTEAMCODE+좌표(EPSG:5174)",
        "quality_notes_ko": "재시도 수집분이다. 커피숍/분식/휴게음식점 계열의 영업상태, 폐업일자, 면적, EPSG:5174 좌표를 보존한다.",
    },
]


def build_url(key: str, service: str, start: int, end: int) -> str:
    return f"http://openapi.seoul.go.kr:8088/{urllib.parse.quote(key)}/json/{service}/{start}/{end}/"


def expected_ranges(total_count: int) -> list[tuple[int, int]]:
    return [(start, min(start + PAGE_SIZE - 1, total_count)) for start in range(1, total_count + 1, PAGE_SIZE)]


def existing_ranges(service: str) -> set[tuple[int, int]]:
    path = RAW_ROOT / RUN_DATE / "seoul_open_data" / "full" / service
    pattern = re.compile(re.escape(service) + r"_(\d+)_(\d+)\.json$")
    ranges = set()
    if not path.exists():
        return ranges
    for file_path in path.glob("*.json"):
        match = pattern.match(file_path.name)
        if match:
            ranges.add((int(match.group(1)), int(match.group(2))))
    return ranges


def parse_openapi_response(body: bytes, service: str) -> tuple[str, str, int, int]:
    if not body.strip().startswith(b"{"):
        text = body.decode("utf-8", errors="replace")
        if "<CODE>" in text or "ERROR-" in text:
            code = re.search(r"<CODE>(.*?)</CODE>", text, flags=re.S)
            message = re.search(r"<MESSAGE><!\[CDATA\[(.*?)\]\]></MESSAGE>|<MESSAGE>(.*?)</MESSAGE>", text, flags=re.S)
            msg = ""
            if message:
                msg = (message.group(1) or message.group(2) or "").strip()
            return (code.group(1).strip() if code else "NON_JSON", msg or text[:300], 0, 0)
        return "NON_JSON", text[:300], 0, 0

    data = json.loads(body.decode("utf-8", errors="replace"))
    if "RESULT" in data:
        result = data["RESULT"]
        return str(result.get("CODE", "")), str(result.get("MESSAGE", "")), 0, 0
    payload = data.get(service, {})
    result = payload.get("RESULT", {}) if isinstance(payload, dict) else {}
    rows = payload.get("row", []) if isinstance(payload, dict) else []
    total = payload.get("list_total_count", 0) if isinstance(payload, dict) else 0
    return str(result.get("CODE", "")), str(result.get("MESSAGE", "")), int(total or 0), len(rows or [])


def save_page(
    *,
    rid: str,
    key: str,
    service_info: dict[str, Any],
    start: int,
    end: int,
    status: int,
    body: bytes,
    result_code: str,
    result_message: str,
    total_count: int,
    row_count: int,
) -> Path:
    service = service_info["service"]
    url = build_url(key, service, start, end)
    return write_raw(
        run_id_value=rid,
        source_id=service_info["source_id"],
        provider=PROVIDER,
        dataset_name=service_info["dataset_name"],
        body=body,
        relative_path=f"{RUN_DATE}/seoul_open_data/full/{service}/{service}_{start}_{end}.json",
        request_url_redacted=redact_url(url, extra_values=[key]),
        request_params={
            "service": service,
            "start": start,
            "end": end,
            "key": "<redacted>",
            "retry_missing": True,
        },
        http_status=status,
        provider_result_code=result_code,
        provider_result_message=result_message,
        spatial_unit=service_info["spatial_unit"],
        time_unit=service_info["time_unit"],
        source_period=service_info["source_period"],
        boundary_version=service_info["boundary_version"],
        area_code_type=service_info["area_code_type"],
        quality_notes_ko=(
            f"{service_info['quality_notes_ko']} 이 페이지의 total_count={total_count}, row_count={row_count}."
        ),
    )


def collect_range_with_retry(rid: str, key: str, service_info: dict[str, Any], start: int, end: int) -> dict[str, Any]:
    service = service_info["service"]
    url = build_url(key, service, start, end)
    last_error = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            status, body, _headers = http_get(url, timeout=120)
            result_code, result_message, total_count, row_count = parse_openapi_response(body, service)
            if result_code != "INFO-000":
                raise RuntimeError(f"{result_code}: {result_message}")
            path = save_page(
                rid=rid,
                key=key,
                service_info=service_info,
                start=start,
                end=end,
                status=status,
                body=body,
                result_code=result_code,
                result_message=result_message,
                total_count=total_count,
                row_count=row_count,
            )
            return {
                "start": start,
                "end": end,
                "status": "success",
                "attempts": attempt,
                "row_count": row_count,
                "path": str(path),
            }
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < MAX_ATTEMPTS:
                time.sleep(BACKOFF_SECONDS[min(attempt - 1, len(BACKOFF_SECONDS) - 1)])

    log_failure(
        run_id_value=rid,
        source_id=service_info["source_id"],
        provider=PROVIDER,
        dataset_name=service_info["dataset_name"],
        failure_type="RetryExhausted",
        failure_reason_ko=f"{service} {start}-{end} 누락 페이지 재시도 실패: {last_error}",
        next_action_ko="서울 OpenAPI 특정 페이지 503이 반복된다. 호출 간격을 더 늘리거나 단건/소구간 저장 전략으로 재시도한다.",
        request_url_redacted=redact_url(url, extra_values=[key]),
    )
    return {"start": start, "end": end, "status": "failed", "attempts": MAX_ATTEMPTS, "error": last_error}


def main() -> None:
    key = parse_key_file()["seoul_key"]
    if not key:
        raise RuntimeError("key.md에서 서울 열린데이터광장 키를 찾지 못했습니다.")

    rid = run_id("seoul_localdata_retry")
    summary: dict[str, Any] = {"run_id": rid, "services": [], "created_at": datetime.now().isoformat(timespec="seconds")}
    for service_info in SERVICES:
        service = service_info["service"]
        missing = sorted(set(expected_ranges(int(service_info["total_count"]))) - existing_ranges(service))
        service_summary = {
            "service": service,
            "missing_before": len(missing),
            "results": [],
        }
        for start, end in missing:
            result = collect_range_with_retry(rid, key, service_info, start, end)
            service_summary["results"].append(result)
            time.sleep(0.25)
        remaining = sorted(set(expected_ranges(int(service_info["total_count"]))) - existing_ranges(service))
        service_summary["missing_after"] = len(remaining)
        service_summary["success_count"] = sum(1 for r in service_summary["results"] if r["status"] == "success")
        service_summary["failed_count"] = sum(1 for r in service_summary["results"] if r["status"] != "success")
        summary["services"].append(service_summary)

    log_path = RAW_ROOT / "run_logs" / f"{rid}.json"
    log_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    md_lines = [
        "# 2026-07-03 서울 LocalData 누락 페이지 재시도 기록",
        "",
        f"- 실행 ID: `{rid}`",
        f"- 작성 시각: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "## 결과",
    ]
    for service_summary in summary["services"]:
        md_lines.append(
            f"- `{service_summary['service']}`: 누락 전 {service_summary['missing_before']}페이지, "
            f"성공 {service_summary['success_count']}페이지, 실패 {service_summary['failed_count']}페이지, "
            f"누락 후 {service_summary['missing_after']}페이지"
        )
    md_lines.extend(
        [
            "",
            "## 주의사항",
            "",
            "- 최초 전체 수집에서 일부 구간이 서울 OpenAPI 503 XML 오류를 반환했다.",
            "- 같은 구간도 재호출하면 성공하는 경우가 있어, 누락 페이지를 파일명 기준으로 계산한 뒤 재시도했다.",
            "- 그래도 남는 구간은 호출 제한 또는 원천 서비스 상태 문제일 수 있으므로 실패표에 남긴다.",
        ]
    )
    md_path = RAW_ROOT / "run_logs" / "20260703_seoul_localdata_retry_ko.md"
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    print(json.dumps({**summary, "korean_log": str(md_path)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
