from __future__ import annotations

import json
import math
import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ingest_common import (
    atomic_write_text,
    classify_seoul_probe,
    data_period_bounds,
    emit_progress,
    http_get,
    parse_key_file,
    validate_paged_collection_response,
)
from ingest_seoul_core_p0_full import CORE_SERVICES, STORE_SERVICE
from ingest_seoul_sales_trade_area_full import (
    DATASET_NAME as SALES_DATASET_NAME,
    PAGE_SIZE as SALES_PAGE_SIZE,
    SERVICE as SALES_SERVICE,
    SOURCE_ID as SALES_SOURCE_ID,
    build_url as build_sales_url,
    parse_response as parse_sales_response,
)
from ingest_seoul_transport_accessibility_sources import (
    fetch_api_with_retries,
    seoul_api_url,
)


ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "final_proj" / "runtime" / "admin" / "core_source_freshness_report.json"
DATABASE_PATH = ROOT / "final_proj" / "runtime" / "db" / "commercial.db"
PAGE_SIZE = 1000
REQUEST_TIMEOUT_SECONDS = 12
MAX_WORKERS = 4

SOURCE_LABELS = {
    "seoul_trade_area_boundary": "상권 경계",
    "seoul_floating_population_trade_area": "유동인구",
    "seoul_resident_worker_population_trade_area": "상주·직장인구",
    "seoul_trade_area_change_index": "상권변화지표",
    "seoul_facility_trade_area": "집객시설",
    "seoul_store_trade_area": "점포",
    "seoul_sales_trade_area": "추정매출",
}

STATUS_PRIORITY = {
    "up_to_date": 0,
    "refresh_needed": 1,
    "reconnect_needed": 2,
}


def _sample_ranges(total_count: int, page_size: int) -> list[tuple[int, int]]:
    page_count = max(1, math.ceil(total_count / page_size))
    pages = sorted({1, max(1, (page_count + 1) // 2), page_count})
    return [
        ((page - 1) * page_size + 1, min(page * page_size, total_count))
        for page in pages
    ]


def _decision(probe: dict[str, Any]) -> tuple[str, str]:
    probe_status = str(probe.get("status") or "")
    if probe_status in {"unchanged_sampled", "sample_match_full_refresh_due"} and probe.get("samples_match"):
        return "up_to_date", "외부 표본과 현재 완전수집본이 같습니다."
    if probe_status == "new_period":
        return "refresh_needed", "외부 제공 분기에 새 구간이 확인됐습니다."
    if probe_status == "provider_window_shrink":
        return "refresh_needed", "외부 제공 범위가 현재 보관본보다 줄었습니다."
    if probe_status == "new_source":
        return "refresh_needed", "비교할 완전수집본이 없습니다."
    return "refresh_needed", "외부 표본 또는 전체 건수가 현재 완전수집본과 다릅니다."


def _safe_error(error: Exception, key: str) -> str:
    message = str(error)
    if key:
        message = message.replace(key, "<redacted>")
    return message[:500]


def _probe_core_service(key: str, service: dict[str, Any]) -> dict[str, Any]:
    started = time.monotonic()
    source_id = str(service["source_id"])
    service_name = str(service["service"])
    try:
        first_url = seoul_api_url(key, service_name, 1, PAGE_SIZE)
        http_status, first_body, code, message, total_count, rows = fetch_api_with_retries(
            first_url,
            service_name,
            attempts=1,
            socket_timeout_seconds=REQUEST_TIMEOUT_SECONDS,
            hard_timeout_seconds=REQUEST_TIMEOUT_SECONDS,
        )
        if code != "INFO-000":
            raise RuntimeError(f"provider_result={code}: {message}")
        if total_count <= 0 or not rows:
            raise RuntimeError(f"empty_response: total_count={total_count}, rows={len(rows)}")
        validate_paged_collection_response(
            initial_total_count=total_count,
            page_total_count=total_count,
            start=1,
            end=PAGE_SIZE,
            row_count=len(rows),
        )
        sample_bodies: dict[tuple[int, int], bytes] = {(1, PAGE_SIZE): first_body}
        for start, end in _sample_ranges(total_count, PAGE_SIZE):
            if start == 1:
                continue
            sample_url = seoul_api_url(key, service_name, start, end)
            _, sample_body, sample_code, sample_message, sample_total, sample_rows = fetch_api_with_retries(
                sample_url,
                service_name,
                attempts=1,
                socket_timeout_seconds=REQUEST_TIMEOUT_SECONDS,
                hard_timeout_seconds=REQUEST_TIMEOUT_SECONDS,
            )
            if sample_code != "INFO-000":
                raise RuntimeError(f"provider_result={sample_code}: {sample_message}")
            validate_paged_collection_response(
                initial_total_count=total_count,
                page_total_count=sample_total,
                start=start,
                end=end,
                row_count=len(sample_rows),
            )
            sample_bodies[(start, end)] = sample_body
        probe = classify_seoul_probe(
            source_id=source_id,
            service_name=service_name,
            total_count=total_count,
            sample_bodies=sample_bodies,
            include_full_fingerprint=False,
        )
        status, reason = _decision(probe)
        return {
            "source_id": source_id,
            "source_label": SOURCE_LABELS[source_id],
            "service": service_name,
            "dataset_name": str(service.get("dataset_name") or SOURCE_LABELS[source_id]),
            "status": status,
            "reason": reason,
            "http_status": http_status,
            "provider_result_code": code,
            "probe_status": probe.get("status"),
            "samples_match": bool(probe.get("samples_match")),
            "sample_count": len(sample_bodies),
            "total_count": total_count,
            "data_period_start": probe.get("data_period_start"),
            "data_period_end": probe.get("data_period_end"),
            "last_full_collection_at": probe.get("last_full_collection_at"),
            "response_time_ms": round((time.monotonic() - started) * 1000),
        }
    except Exception as error:
        return {
            "source_id": source_id,
            "source_label": SOURCE_LABELS[source_id],
            "service": service_name,
            "dataset_name": str(service.get("dataset_name") or SOURCE_LABELS[source_id]),
            "status": "reconnect_needed",
            "reason": _safe_error(error, key),
            "http_status": None,
            "provider_result_code": None,
            "probe_status": "connection_failed",
            "samples_match": False,
            "sample_count": 0,
            "total_count": None,
            "data_period_start": None,
            "data_period_end": None,
            "last_full_collection_at": None,
            "response_time_ms": round((time.monotonic() - started) * 1000),
        }


def _probe_sales_service(key: str) -> dict[str, Any]:
    started = time.monotonic()
    try:
        first_url = build_sales_url(key, 1, SALES_PAGE_SIZE)
        http_status, first_body, _headers = http_get(first_url, timeout=REQUEST_TIMEOUT_SECONDS)
        code, message, total_count, row_count = parse_sales_response(first_body)
        if code != "INFO-000":
            raise RuntimeError(f"provider_result={code}: {message}")
        if total_count <= 0 or row_count <= 0:
            raise RuntimeError(f"empty_response: total_count={total_count}, rows={row_count}")
        validate_paged_collection_response(
            initial_total_count=total_count,
            page_total_count=total_count,
            start=1,
            end=SALES_PAGE_SIZE,
            row_count=row_count,
        )
        sample_bodies: dict[tuple[int, int], bytes] = {(1, SALES_PAGE_SIZE): first_body}
        for start, end in _sample_ranges(total_count, SALES_PAGE_SIZE):
            if start == 1:
                continue
            sample_url = build_sales_url(key, start, end)
            _, sample_body, _ = http_get(sample_url, timeout=REQUEST_TIMEOUT_SECONDS)
            sample_code, sample_message, sample_total, sample_rows = parse_sales_response(sample_body)
            if sample_code != "INFO-000":
                raise RuntimeError(f"provider_result={sample_code}: {sample_message}")
            validate_paged_collection_response(
                initial_total_count=total_count,
                page_total_count=sample_total,
                start=start,
                end=end,
                row_count=sample_rows,
            )
            sample_bodies[(start, end)] = sample_body
        probe = classify_seoul_probe(
            source_id=SALES_SOURCE_ID,
            service_name=SALES_SERVICE,
            total_count=total_count,
            sample_bodies=sample_bodies,
            include_full_fingerprint=False,
        )
        status, reason = _decision(probe)
        return {
            "source_id": SALES_SOURCE_ID,
            "source_label": SOURCE_LABELS[SALES_SOURCE_ID],
            "service": SALES_SERVICE,
            "dataset_name": SALES_DATASET_NAME,
            "status": status,
            "reason": reason,
            "http_status": http_status,
            "provider_result_code": code,
            "probe_status": probe.get("status"),
            "samples_match": bool(probe.get("samples_match")),
            "sample_count": len(sample_bodies),
            "total_count": total_count,
            "data_period_start": probe.get("data_period_start"),
            "data_period_end": probe.get("data_period_end"),
            "last_full_collection_at": probe.get("last_full_collection_at"),
            "response_time_ms": round((time.monotonic() - started) * 1000),
        }
    except Exception as error:
        return {
            "source_id": SALES_SOURCE_ID,
            "source_label": SOURCE_LABELS[SALES_SOURCE_ID],
            "service": SALES_SERVICE,
            "dataset_name": SALES_DATASET_NAME,
            "status": "reconnect_needed",
            "reason": _safe_error(error, key),
            "http_status": None,
            "provider_result_code": None,
            "probe_status": "connection_failed",
            "samples_match": False,
            "sample_count": 0,
            "total_count": None,
            "data_period_start": None,
            "data_period_end": None,
            "last_full_collection_at": None,
            "response_time_ms": round((time.monotonic() - started) * 1000),
        }


def _merge_sources(service_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for result in service_results:
        grouped.setdefault(str(result["source_id"]), []).append(result)
    sources: list[dict[str, Any]] = []
    for source_id in SOURCE_LABELS:
        services = sorted(grouped.get(source_id, []), key=lambda item: str(item["service"]))
        status = max(
            (str(item["status"]) for item in services),
            key=lambda value: STATUS_PRIORITY[value],
            default="reconnect_needed",
        )
        periods = [str(item["data_period_end"]) for item in services if item.get("data_period_end")]
        reasons = list(
            dict.fromkeys(
                str(item["reason"])
                for item in services
                if item.get("status") == status and item.get("reason")
            )
        )
        sources.append(
            {
                "source_id": source_id,
                "label": SOURCE_LABELS[source_id],
                "status": status,
                "reason": " / ".join(reasons),
                "data_period_end": min(periods) if periods else None,
                "services": services,
            }
        )
    return sources


def _database_status() -> dict[str, Any]:
    if not DATABASE_PATH.exists():
        return {
            "status": "missing",
            "quick_check": None,
            "quarter": None,
            "score_version": None,
            "table_counts": {},
            "reason": "제품 DB 파일이 없습니다.",
        }
    try:
        connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True, timeout=5)
        try:
            quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
            existing_tables = {
                str(row[0])
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            required_tables = ("commercial_area", "rule_location_score", "rule_area_score_summary")
            counts = {
                table: int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
                for table in required_tables
                if table in existing_tables
            }
            quarter_row = (
                connection.execute("SELECT MAX(quarter) FROM rule_location_score").fetchone()
                if "rule_location_score" in existing_tables
                else None
            )
            version_row = (
                connection.execute(
                    "SELECT score_version, COUNT(*) FROM rule_location_score "
                    "GROUP BY score_version ORDER BY COUNT(*) DESC LIMIT 1"
                ).fetchone()
                if "rule_location_score" in existing_tables
                else None
            )
        finally:
            connection.close()
        missing = [table for table in required_tables if table not in counts]
        empty = [table for table, count in counts.items() if count <= 0]
        healthy = quick_check == "ok" and not missing and not empty and bool(quarter_row and quarter_row[0])
        reason_parts = []
        if quick_check != "ok":
            reason_parts.append(f"quick_check={quick_check}")
        if missing:
            reason_parts.append("누락 테이블: " + ", ".join(missing))
        if empty:
            reason_parts.append("빈 테이블: " + ", ".join(empty))
        if not quarter_row or not quarter_row[0]:
            reason_parts.append("제품 기준 분기 없음")
        return {
            "status": "healthy" if healthy else "refresh_needed",
            "quick_check": quick_check,
            "quarter": str(quarter_row[0]) if quarter_row and quarter_row[0] else None,
            "score_version": str(version_row[0]) if version_row and version_row[0] else None,
            "table_counts": counts,
            "reason": "제품 DB 읽기·핵심 테이블이 정상입니다." if healthy else "; ".join(reason_parts),
        }
    except sqlite3.Error as error:
        return {
            "status": "refresh_needed",
            "quick_check": None,
            "quarter": None,
            "score_version": None,
            "table_counts": {},
            "reason": f"제품 DB 읽기 실패: {error}",
        }


def main() -> None:
    started = time.monotonic()
    job_id = os.getenv("LOCALFIT_FRESHNESS_JOB_ID", "").strip() or None
    key_error = ""
    try:
        key = str(parse_key_file().get("seoul_key") or "").strip()
    except (OSError, RuntimeError, ValueError) as error:
        key = ""
        key_error = _safe_error(error, "")

    tasks: list[tuple[str, dict[str, Any] | None]] = [
        ("core", service) for service in [*CORE_SERVICES, STORE_SERVICE]
    ]
    tasks.append(("sales", None))
    service_results: list[dict[str, Any]] = []
    if not key:
        reason = key_error or "서울 열린데이터광장 API 키가 설정되지 않았습니다."
        for kind, service in tasks:
            source_id = SALES_SOURCE_ID if kind == "sales" else str(service["source_id"])
            service_name = SALES_SERVICE if kind == "sales" else str(service["service"])
            dataset_name = SALES_DATASET_NAME if kind == "sales" else str(service.get("dataset_name") or SOURCE_LABELS[source_id])
            service_results.append(
                {
                    "source_id": source_id,
                    "source_label": SOURCE_LABELS[source_id],
                    "service": service_name,
                    "dataset_name": dataset_name,
                    "status": "reconnect_needed",
                    "reason": reason,
                    "http_status": None,
                    "provider_result_code": None,
                    "probe_status": "credential_missing",
                    "samples_match": False,
                    "sample_count": 0,
                    "total_count": None,
                    "data_period_start": None,
                    "data_period_end": None,
                    "last_full_collection_at": None,
                    "response_time_ms": 0,
                }
            )
        database = _database_status()
    else:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS + 1, thread_name_prefix="freshness-probe") as executor:
            database_future = executor.submit(_database_status)
            futures = {
                executor.submit(
                    _probe_sales_service if kind == "sales" else _probe_core_service,
                    key,
                    *(() if service is None else (service,)),
                ): (kind, service)
                for kind, service in tasks
            }
            for completed, future in enumerate(as_completed(futures), start=1):
                result = future.result()
                service_results.append(result)
                emit_progress(
                    label="외부 원천 빠른 점검",
                    current_units=completed,
                    total_units=len(tasks),
                    unit="서비스",
                    data_period_start=result.get("data_period_start"),
                    data_period_end=result.get("data_period_end"),
                    message=f"{result['source_label']} · {result['status']}",
                )
            database = database_future.result()

    service_results.sort(key=lambda item: (list(SOURCE_LABELS).index(str(item["source_id"])), str(item["service"])))
    sources = _merge_sources(service_results)
    reconnect_sources = [item["source_id"] for item in sources if item["status"] == "reconnect_needed"]
    refresh_sources = [item["source_id"] for item in sources if item["status"] == "refresh_needed"]
    periods = [str(item["data_period_end"]) for item in sources if item.get("data_period_end")]
    latest_provider_period = min(periods) if periods else None
    newest_provider_period = max(periods) if periods else None
    product_refresh_needed = (
        database["status"] != "healthy"
        or bool(latest_provider_period and database.get("quarter") != latest_provider_period)
    )
    if reconnect_sources:
        overall_status = "reconnect_needed"
        decision = f"연결 재설정 필요 · {len(reconnect_sources)}개 원천"
    elif refresh_sources or product_refresh_needed:
        overall_status = "refresh_needed"
        decision = (
            f"재수집 필요 · {len(refresh_sources)}개 원천"
            if refresh_sources
            else "제품 DB 반영 확인 필요"
        )
    else:
        overall_status = "up_to_date"
        decision = "최신 상태 · 재수집 불필요"
    report = {
        "schema_version": "core_source_freshness.v1",
        "job_id": int(job_id) if job_id and job_id.isdigit() else None,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": round(time.monotonic() - started, 3),
        "overall_status": overall_status,
        "decision": decision,
        "source_count": len(sources),
        "service_count": len(service_results),
        "connected_service_count": sum(item["status"] != "reconnect_needed" for item in service_results),
        "up_to_date_source_count": sum(item["status"] == "up_to_date" for item in sources),
        "refresh_needed_source_count": len(refresh_sources),
        "reconnect_needed_source_count": len(reconnect_sources),
        "refresh_needed_sources": refresh_sources,
        "reconnect_needed_sources": reconnect_sources,
        "latest_provider_period": latest_provider_period,
        "newest_provider_period": newest_provider_period,
        "product_refresh_needed": product_refresh_needed,
        "database": database,
        "sources": sources,
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(REPORT_PATH, json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    print(f"[freshness] {decision}")
    print(
        "[freshness] "
        f"sources={len(sources)} services={len(service_results)} "
        f"connected={report['connected_service_count']} latest_period={report['latest_provider_period']} "
        f"duration={report['duration_seconds']}s"
    )
    for source in sources:
        print(
            f"[freshness] {source['label']}: {source['status']} "
            f"period={source['data_period_end'] or '-'}"
        )
    print(f"[freshness] report={REPORT_PATH}")


if __name__ == "__main__":
    main()
