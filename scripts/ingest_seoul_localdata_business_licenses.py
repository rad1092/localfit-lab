from __future__ import annotations

import argparse
import csv
import io
import json
import math
import re
import time
import urllib.parse
from pathlib import Path
from typing import Any

from ingest_common import (
    RAW_ROOT,
    ROOT,
    atomic_write_text,
    http_get,
    latest_complete_service_directory,
    log_failure,
    mark_manifest_run_complete,
    parse_key_file,
    redact_url,
    run_date,
    run_id,
    write_raw,
)


PROVIDER = "서울 열린데이터광장"
DEFAULT_PAGE_SIZE = 1000
CONFIG_PATH = ROOT / "config" / "seoul_localdata_business_services.json"

# This is a discovery list, not the runtime registry.  --probe-only writes only
# services that return a valid Seoul-wide payload to CONFIG_PATH.
CANDIDATE_SERVICES: list[dict[str, Any]] = [
    {
        "service": "LOCALDATA_072404",
        "source_id": "seoul_localdata_general_restaurant_license",
        "service_name_ko": "일반음식점 인허가",
        "industry_group": "음식점",
        "official_url": "https://data.seoul.go.kr/dataList/OA-16094/S/1/datasetView.do",
        "reuse_existing": True,
    },
    {
        "service": "LOCALDATA_072405",
        "source_id": "seoul_localdata_rest_cafe_license",
        "service_name_ko": "휴게음식점 인허가",
        "industry_group": "음식점",
        "official_url": "https://data.seoul.go.kr/",
        "reuse_existing": True,
    },
    {
        "service": "LOCALDATA_051801",
        "source_id": "seoul_localdata_beauty_license",
        "service_name_ko": "미용업 인허가",
        "industry_group": "생활서비스",
        "official_url": "https://data.seoul.go.kr/dataList/OA-16063/S/1/datasetView.do",
    },
    {
        "service": "LOCALDATA_051901",
        "source_id": "seoul_localdata_barber_license",
        "service_name_ko": "이용업 인허가",
        "industry_group": "생활서비스",
        "official_url": "https://data.seoul.go.kr/dataList/OA-16064/S/1/datasetView.do",
    },
    {
        "service": "LOCALDATA_062001",
        "source_id": "seoul_localdata_laundry_license",
        "service_name_ko": "세탁업 인허가",
        "industry_group": "생활서비스",
        "official_url": "https://data.seoul.go.kr/dataList/OA-16065/S/1/datasetView.do",
    },
    {
        "service": "LOCALDATA_114401",
        "source_id": "seoul_localdata_public_bath_license",
        "service_name_ko": "목욕장업 인허가",
        "industry_group": "생활서비스",
        "official_url": "https://data.seoul.go.kr/dataList/OA-16146/S/1/datasetView.do",
    },
    {
        "service": "LOCALDATA_031101",
        "source_id": "seoul_localdata_lodging_license",
        "service_name_ko": "숙박업 인허가",
        "industry_group": "숙박",
        "official_url": "https://data.seoul.go.kr/",
    },
    {
        "service": "LOCALDATA_030901",
        "source_id": "seoul_localdata_singing_practice_license",
        "service_name_ko": "노래연습장업 인허가",
        "industry_group": "여가서비스",
        "official_url": "https://data.seoul.go.kr/dataList/OA-16037/S/1/datasetView.do",
    },
    {
        "service": "LOCALDATA_031201",
        "source_id": "seoul_localdata_domestic_travel_license",
        "service_name_ko": "국내여행업 인허가",
        "industry_group": "여행",
        "official_url": "https://data.seoul.go.kr/",
    },
    {
        "service": "LOCALDATA_031202",
        "source_id": "seoul_localdata_overseas_travel_license",
        "service_name_ko": "국외여행업 인허가",
        "industry_group": "여행",
        "official_url": "https://data.seoul.go.kr/",
    },
    {
        "service": "LOCALDATA_031203",
        "source_id": "seoul_localdata_general_travel_license",
        "service_name_ko": "일반여행업 인허가",
        "industry_group": "여행",
        "official_url": "https://data.seoul.go.kr/dataList/OA-16051/S/1/datasetView.do",
    },
    {
        "service": "LOCALDATA_103101",
        "source_id": "seoul_localdata_golf_practice_license",
        "service_name_ko": "골프연습장업 인허가",
        "industry_group": "체육",
        "official_url": "https://data.seoul.go.kr/",
    },
    {
        "service": "LOCALDATA_103201",
        "source_id": "seoul_localdata_billiards_license",
        "service_name_ko": "당구장업 인허가",
        "industry_group": "체육",
        "official_url": "https://data.seoul.go.kr/dataList/OA-16133/S/1/datasetView.do",
    },
    {
        "service": "LOCALDATA_104101",
        "source_id": "seoul_localdata_sports_dojo_license",
        "service_name_ko": "체육도장업 인허가",
        "industry_group": "체육",
        "official_url": "https://data.seoul.go.kr/",
    },
    {
        "service": "LOCALDATA_104201",
        "source_id": "seoul_localdata_fitness_license",
        "service_name_ko": "체력단련장업 인허가",
        "industry_group": "체육",
        "official_url": "https://data.seoul.go.kr/dataList/OA-16142/S/1/datasetView.do",
    },
]


def build_url(key: str, service: str, start: int, end: int) -> str:
    encoded_key = urllib.parse.quote(key, safe="")
    return f"http://openapi.seoul.go.kr:8088/{encoded_key}/json/{service}/{start}/{end}/"


def parse_payload(body: bytes, service: str) -> tuple[str, str, int, list[dict[str, Any]]]:
    data = json.loads(body.decode("utf-8", errors="replace"))
    if not isinstance(data, dict):
        return "INVALID", "응답 최상위가 객체가 아님", 0, []
    if "RESULT" in data:
        result = data.get("RESULT") or {}
        return str(result.get("CODE") or ""), str(result.get("MESSAGE") or ""), 0, []
    payload = data.get(service)
    if not isinstance(payload, dict):
        return "INVALID", "서비스 객체가 응답에 없음", 0, []
    result = payload.get("RESULT") or {}
    rows = payload.get("row") or []
    if not isinstance(rows, list):
        rows = []
    return (
        str(result.get("CODE") or ""),
        str(result.get("MESSAGE") or ""),
        int(payload.get("list_total_count") or 0),
        [row for row in rows if isinstance(row, dict)],
    )


def safe_error(exc: Exception, key: str) -> str:
    value = str(exc)
    if key:
        value = value.replace(key, "<redacted>")
        value = value.replace(urllib.parse.quote(key, safe=""), "<redacted>")
    return value[:500]


def probe_service(key: str, service_info: dict[str, Any]) -> dict[str, Any]:
    service = str(service_info["service"])
    url = build_url(key, service, 1, 5)
    try:
        status, body, _headers = http_get(url, timeout=45)
        code, message, total, rows = parse_payload(body, service)
        first = rows[0] if rows else {}
        required = {"MGTNO", "TRDSTATEGBN", "TRDSTATENM"}
        missing_required = sorted(required - set(first)) if first else sorted(required)
        live = status == 200 and code == "INFO-000" and total > 0 and not missing_required
        return {
            **service_info,
            "live": live,
            "http_status": status,
            "provider_result_code": code,
            "provider_result_message": message,
            "total_count": total,
            "probe_row_count": len(rows),
            "missing_required_fields": missing_required,
        }
    except Exception as exc:
        return {
            **service_info,
            "live": False,
            "http_status": None,
            "provider_result_code": type(exc).__name__,
            "provider_result_message": safe_error(exc, key),
            "total_count": 0,
            "probe_row_count": 0,
            "missing_required_fields": [],
        }


def public_probe_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        key: result.get(key)
        for key in (
            "service",
            "source_id",
            "service_name_ko",
            "industry_group",
            "official_url",
            "live",
            "http_status",
            "provider_result_code",
            "provider_result_message",
            "total_count",
            "probe_row_count",
            "missing_required_fields",
            "reuse_existing",
        )
        if key in result
    }


def write_live_config(results: list[dict[str, Any]]) -> Path:
    live = []
    for result in results:
        if not result.get("live"):
            continue
        item = {
            key: result[key]
            for key in (
                "service",
                "source_id",
                "service_name_ko",
                "industry_group",
                "official_url",
                "reuse_existing",
            )
            if key in result
        }
        item["verified_total_count"] = int(result.get("total_count") or 0)
        live.append(item)
    payload = {
        "provider": PROVIDER,
        "scope": "서울특별시 전체 LocalData 인허가",
        "coordinate_crs": "EPSG:5174",
        "probe_date": run_date(),
        "services": live,
    }
    atomic_write_text(CONFIG_PATH, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return CONFIG_PATH


def load_live_services() -> list[dict[str, Any]]:
    if not CONFIG_PATH.exists():
        raise RuntimeError("라이브 서비스 설정이 없습니다. 먼저 --probe-only --write-config를 실행하세요.")
    payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    services = payload.get("services") or []
    if not services:
        raise RuntimeError("라이브 서비스 설정이 비어 있습니다.")
    return [dict(item) for item in services if isinstance(item, dict)]


def complete_existing_directory(service: str) -> Path | None:
    trusted = latest_complete_service_directory(service)
    if trusted is not None:
        return trusted
    service_directories = sorted(
        RAW_ROOT.glob(f"[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]/seoul_open_data/full/{service}"),
        reverse=True,
    )
    pattern = re.compile(rf"^{re.escape(service)}_(\d+)_(\d+)\.json$")
    for directory in service_directories:
        ranges: list[tuple[int, int]] = []
        total = None
        for path in directory.glob(f"{service}_*.json"):
            match = pattern.match(path.name)
            if not match:
                continue
            try:
                _code, _message, page_total, _rows = parse_payload(path.read_bytes(), service)
            except Exception:
                ranges = []
                break
            total = total if total is not None else page_total
            ranges.append((int(match.group(1)), int(match.group(2))))
        ranges.sort()
        if not ranges or not total or ranges[0][0] != 1:
            continue
        expected_start = 1
        complete = True
        for start, end in ranges:
            if start != expected_start:
                complete = False
                break
            expected_start = end + 1
            if end >= total:
                break
        if complete and expected_start > total:
            return directory
    return None


def save_page(
    *,
    rid: str,
    key: str,
    info: dict[str, Any],
    start: int,
    end: int,
    status: int,
    body: bytes,
    code: str,
    message: str,
    total: int,
    row_count: int,
) -> Path:
    service = str(info["service"])
    url = build_url(key, service, start, end)
    return write_raw(
        run_id_value=rid,
        source_id=str(info["source_id"]),
        provider=PROVIDER,
        dataset_name=f"서울시 {info['service_name_ko']} 전체 응답",
        body=body,
        relative_path=f"{run_date()}/seoul_open_data/full/{service}/{service}_{start}_{end}.json",
        request_url_redacted=redact_url(url, extra_values=[key]),
        request_params={"service": service, "start": start, "end": end, "key": "<redacted>"},
        http_status=status,
        provider_result_code=code,
        provider_result_message=message,
        spatial_unit="인허가 사업장",
        time_unit="매일/약 3일 전 자료",
        source_period="API 전체 응답 기준",
        boundary_version=f"서울 열린데이터광장 {run_date()} 수집 기준",
        area_code_type="MGTNO+OPNSFTEAMCODE+좌표(EPSG:5174)",
        quality_notes_ko=f"서울 전체 서비스. total_count={total}, row_count={row_count}.",
    )


def collect_service(
    rid: str,
    key: str,
    info: dict[str, Any],
    *,
    page_size: int,
    max_pages: int | None,
    refresh: bool,
) -> dict[str, Any]:
    service = str(info["service"])
    existing = complete_existing_directory(service)
    existing_is_today = bool(
        existing
        and existing.relative_to(RAW_ROOT).parts
        and existing.relative_to(RAW_ROOT).parts[0] == run_date()
    )
    if existing is not None and (not refresh or existing_is_today):
        return {
            "service": service,
            "source_id": info["source_id"],
            "status": "reused_complete_snapshot",
            "raw_directory": str(existing.relative_to(ROOT)),
        }

    saved_rows = 0
    saved_pages = 0
    total = 0
    failure: str | None = None
    first_code = ""
    for page_index in range(1, (max_pages or 10**9) + 1):
        start = (page_index - 1) * page_size + 1
        end = page_index * page_size
        url = build_url(key, service, start, end)
        try:
            status, body, _headers = http_get(url, timeout=90)
            code, message, page_total, rows = parse_payload(body, service)
            first_code = first_code or code
            if status != 200 or code != "INFO-000":
                raise RuntimeError(f"provider_result={code}")
            total = page_total
            save_page(
                rid=rid,
                key=key,
                info=info,
                start=start,
                end=min(end, total),
                status=status,
                body=body,
                code=code,
                message=message,
                total=total,
                row_count=len(rows),
            )
            saved_rows += len(rows)
            saved_pages += 1
            expected_pages = max(1, math.ceil(total / page_size))
            if page_index >= expected_pages:
                break
            time.sleep(0.04)
        except Exception as exc:
            failure = f"{type(exc).__name__}: {safe_error(exc, key)}"
            log_failure(
                run_id_value=rid,
                source_id=str(info["source_id"]),
                provider=PROVIDER,
                dataset_name=f"서울시 {info['service_name_ko']} 전체 응답",
                failure_type=type(exc).__name__,
                failure_reason_ko=f"{service} {start}-{end} 페이지 수집 실패: {safe_error(exc, key)}",
                next_action_ko="서비스 상태와 호출 한도를 확인한 뒤 실패 서비스만 재실행한다.",
                request_url_redacted=redact_url(url, extra_values=[key]),
            )
            break

    expected_pages = max(1, math.ceil(total / page_size)) if total else 0
    complete = failure is None and total > 0 and saved_pages == expected_pages and saved_rows == total
    if max_pages is not None and expected_pages > max_pages:
        complete = False
    if complete:
        mark_manifest_run_complete(
            run_id_value=rid,
            source_id=str(info["source_id"]),
            service_name=service,
        )
    return {
        "service": service,
        "source_id": info["source_id"],
        "status": "collected_complete" if complete else "collection_incomplete",
        "provider_result_code": first_code,
        "total_count": total,
        "saved_rows": saved_rows,
        "saved_pages": saved_pages,
        "expected_pages": expected_pages,
        "failure": failure,
    }


def upsert_source_registry(services: list[dict[str, Any]], status: str) -> None:
    path = RAW_ROOT / "source_registry.csv"
    if not path.exists():
        return
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows = [dict(row) for row in reader]
    if not fields or "source_id" not in fields:
        return
    by_id = {str(row.get("source_id")): row for row in rows}
    for info in services:
        source_id = str(info["source_id"])
        row = {field: "" for field in fields}
        row.update(
            {
                "source_id": source_id,
                "priority": "P1",
                "provider": PROVIDER,
                "dataset_name": f"서울시 {info['service_name_ko']}",
                "method_axis": "인허가 개폐업·생존 이력",
                "score_axis": "경쟁·안정성·생존 가능성",
                "spatial_unit": "인허가 사업장",
                "time_unit": "매일/약 3일 전 자료",
                "collection_method": f"서울 OpenAPI {info['service']} 전체 페이지 수집",
                "credential_ref": "SEOUL_OPEN_DATA_KEY",
                "source_url": str(info.get("official_url") or "https://data.seoul.go.kr/"),
                "current_status": status,
                "duplicate_policy": "service+OPNSFTEAMCODE+MGTNO, LASTMODTS/UPDATEDT 최신 행",
                "reason_ko": "서울 소상공인 업종의 개업·폐업·영업상태를 성과 백테스트 정답 후보로 사용한다.",
                "notes_ko": "상호·전화번호·상세주소는 공통 Silver와 공개 로그에서 제외한다.",
            }
        )
        if source_id in by_id:
            by_id[source_id].update(row)
        else:
            rows.append(row)
            by_id[source_id] = row
    path.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    writer.writerows({field: row.get(field, "") for field in fields} for row in rows)
    atomic_write_text(path, "\ufeff" + buffer.getvalue())


def main() -> int:
    parser = argparse.ArgumentParser(description="서울 LocalData 소상공인 인허가 서비스 검증·전체 수집")
    parser.add_argument("--probe-only", action="store_true")
    parser.add_argument("--write-config", action="store_true")
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE)
    parser.add_argument("--max-pages", type=int)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--services", help="쉼표로 구분한 서비스 코드")
    args = parser.parse_args()
    if not 1 <= args.page_size <= 1000:
        parser.error("--page-size는 1~1000이어야 합니다.")
    if args.max_pages is not None and args.max_pages < 1:
        parser.error("--max-pages는 1 이상이어야 합니다.")

    key = parse_key_file().get("seoul_key", "").strip()
    if not key:
        raise RuntimeError("승인된 key.md에서 서울 OpenAPI 키를 찾지 못했습니다.")

    requested = {item.strip() for item in (args.services or "").split(",") if item.strip()}
    if args.probe_only:
        candidates = [item for item in CANDIDATE_SERVICES if not requested or item["service"] in requested]
        results = [probe_service(key, item) for item in candidates]
        live = [result for result in results if result.get("live")]
        config_path = write_live_config(results) if args.write_config else None
        probe_path = RAW_ROOT / run_date() / "seoul_open_data" / "probes" / "localdata_business_services_probe.json"
        payload = {
            "probe_date": run_date(),
            "candidate_count": len(results),
            "live_count": len(live),
            "results": [public_probe_result(result) for result in results],
            "config_path": str(config_path.relative_to(ROOT)) if config_path else None,
        }
        atomic_write_text(probe_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        if config_path:
            upsert_source_registry(live, "live_api_verified")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if live else 2

    services = load_live_services()
    services = [item for item in services if not requested or item["service"] in requested]
    rid = run_id("seoul-localdata-business")
    results = [
        collect_service(
            rid,
            key,
            item,
            page_size=args.page_size,
            max_pages=args.max_pages,
            refresh=args.refresh,
        )
        for item in services
    ]
    complete = [item for item in results if item["status"] in {"collected_complete", "reused_complete_snapshot"}]
    upsert_source_registry(
        [item for item in services if any(result["service"] == item["service"] for result in complete)],
        "collected_raw",
    )
    summary = {
        "run_id": rid,
        "service_count": len(services),
        "complete_count": len(complete),
        "results": results,
    }
    summary_path = RAW_ROOT / "run_logs" / f"{run_date()}_seoul_localdata_business_collection.json"
    atomic_write_text(summary_path, json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    summary["summary_path"] = str(summary_path.relative_to(ROOT))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if len(complete) == len(services) else 2


if __name__ == "__main__":
    raise SystemExit(main())
