from __future__ import annotations

import json
import math
import time
import urllib.parse
import argparse
from datetime import datetime
from pathlib import Path

from ingest_common import (
    RAW_ROOT,
    atomic_write_text,
    classify_seoul_probe,
    data_period_bounds,
    emit_progress,
    http_get,
    log_failure,
    mark_manifest_run_complete,
    page_digest_set_fingerprint,
    parse_key_file,
    redact_url,
    run_date,
    run_id,
    sha256_bytes,
    update_collection_change_report,
    update_source_state_catalog,
    validate_paged_collection_response,
    write_raw,
)


RUN_DATE = run_date()
PROVIDER = "서울열린데이터광장"
SOURCE_ID = "seoul_sales_trade_area"
SERVICE = "VwsmTrdarSelngQq"
DATASET_NAME = "서울 상권분석서비스 추정매출-상권 전체 원응답"
PAGE_SIZE = 1000
SLEEP_SECONDS = 0.05


def parse_response(body: bytes) -> tuple[str, str, int, int]:
    data = json.loads(body.decode("utf-8", errors="replace"))
    if "RESULT" in data:
        result = data["RESULT"]
        return str(result.get("CODE", "")), str(result.get("MESSAGE", "")), 0, 0
    payload = data.get(SERVICE, {})
    result = payload.get("RESULT", {}) if isinstance(payload, dict) else {}
    rows = payload.get("row", []) if isinstance(payload, dict) else []
    total = payload.get("list_total_count", 0) if isinstance(payload, dict) else 0
    return str(result.get("CODE", "")), str(result.get("MESSAGE", "")), int(total or 0), len(rows or [])


def fetch_page_with_retries(url: str, attempts: int = 3) -> tuple[int, bytes, str, str, int, int]:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            status, body, _headers = http_get(url, timeout=90)
            result_code, result_message, total_count, row_count = parse_response(body)
            if result_code != "INFO-000":
                raise RuntimeError(f"서울 OpenAPI 결과 오류 {result_code}: {result_message}")
            return status, body, result_code, result_message, total_count, row_count
        except Exception as exc:
            last_error = exc
            if attempt == attempts:
                raise
            time.sleep(min(2 * attempt, 5))
    raise RuntimeError(last_error or "서울 매출 원천 호출 재시도 실패")


def build_url(key: str, start: int, end: int) -> str:
    return f"http://openapi.seoul.go.kr:8088/{urllib.parse.quote(key)}/json/{SERVICE}/{start}/{end}/"


def save_page(
    *,
    rid: str,
    key: str,
    start: int,
    end: int,
    status: int,
    body: bytes,
    result_code: str,
    result_message: str,
    total_count: int,
    row_count: int,
    change_status: str,
) -> Path:
    url = build_url(key, start, end)
    period_start, period_end = data_period_bounds(body, SERVICE)
    return write_raw(
        run_id_value=rid,
        source_id=SOURCE_ID,
        provider=PROVIDER,
        dataset_name=DATASET_NAME,
        body=body,
        relative_path=f"{RUN_DATE}/seoul_open_data/full/{SERVICE}/{SERVICE}_{start}_{end}.json",
        request_url_redacted=redact_url(url, extra_values=[key]),
        request_params={"service": SERVICE, "start": start, "end": end, "key": "<redacted>"},
        http_status=status,
        provider_result_code=result_code,
        provider_result_message=result_message,
        spatial_unit="상권",
        time_unit="분기",
        source_period=period_end or "API 전체 응답 기준",
        boundary_version=f"seoul_open_data_{RUN_DATE.replace('-', '')}_{SERVICE}",
        area_code_type="상권코드+서비스업종코드",
        quality_notes_ko=(
            "기존 datacorpus 후보 합계가 API list_total_count보다 적어 전체 페이지 원응답을 보존했다. "
            f"이 페이지의 total_count={total_count}, row_count={row_count}."
        ),
        data_period_start=period_start,
        data_period_end=period_end,
        change_status=change_status,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--skip-unchanged",
        action="store_true",
        help="첫·중간·마지막 페이지와 전체 건수/기간이 동일하면 전체 다운로드를 생략",
    )
    args = parser.parse_args()
    keys = parse_key_file()
    key = keys["seoul_key"]
    rid = run_id("seoul_sales_full")
    saved_pages: list[dict[str, object]] = []
    failed_pages: list[dict[str, object]] = []
    started = time.monotonic()

    first_start, first_end = 1, PAGE_SIZE
    first_url = build_url(key, first_start, first_end)
    try:
        status, body, result_code, result_message, total_count, row_count = fetch_page_with_retries(first_url)
        if total_count <= 0 or row_count <= 0:
            raise RuntimeError(
                f"서울 매출 원천이 비어 있습니다: total_count={total_count}, first_page_rows={row_count}"
            )
        validate_paged_collection_response(
            initial_total_count=total_count,
            page_total_count=total_count,
            start=first_start,
            end=first_end,
            row_count=row_count,
        )
        total_pages = math.ceil(total_count / PAGE_SIZE) if total_count else 1
        probe_bodies: dict[tuple[int, int], bytes] = {(first_start, first_end): body}
        if args.skip_unchanged and total_pages > 1:
            for sample_page in sorted({max(2, (total_pages + 1) // 2), total_pages}):
                sample_start = (sample_page - 1) * PAGE_SIZE + 1
                sample_end = min(sample_page * PAGE_SIZE, total_count)
                sample_url = build_url(key, sample_start, sample_end)
                _, sample_body, _, _, sample_total_count, sample_row_count = fetch_page_with_retries(sample_url)
                validate_paged_collection_response(
                    initial_total_count=total_count,
                    page_total_count=sample_total_count,
                    start=sample_start,
                    end=sample_end,
                    row_count=sample_row_count,
                )
                probe_bodies[(sample_start, sample_end)] = sample_body
        probe = classify_seoul_probe(
            source_id=SOURCE_ID,
            service_name=SERVICE,
            total_count=total_count,
            sample_bodies=probe_bodies,
        )
        emit_progress(
            label=f"{DATASET_NAME} 변경 확인",
            current_units=len(probe_bodies),
            total_units=len(probe_bodies),
            unit="표본 페이지",
            data_period_start=probe.get("data_period_start"),
            data_period_end=probe.get("data_period_end"),
            message=str(probe.get("status")),
        )
        change_entry = {
            **probe,
            "source_id": SOURCE_ID,
            "service": SERVICE,
            "change_status": probe["status"],
        }
        change_report = update_collection_change_report([change_entry])
        if args.skip_unchanged and probe["status"] == "unchanged_sampled":
            update_source_state_catalog(
                [
                    {
                        **change_entry,
                        "snapshot_date": probe.get("previous_snapshot_date"),
                        "content_fingerprint": probe.get("previous_full_fingerprint")
                        or probe.get("content_fingerprint"),
                        "full_content_fingerprint": probe.get("previous_full_fingerprint"),
                    }
                ]
            )
            summary = {
                "run_id": rid,
                "service": SERVICE,
                "dataset_name": DATASET_NAME,
                "collection_date": f"{RUN_DATE[:4]}-{RUN_DATE[4:6]}-{RUN_DATE[6:]}",
                "data_period_start": probe.get("data_period_start"),
                "data_period_end": probe.get("data_period_end"),
                "change_status": "unchanged_sampled",
                "total_count": total_count,
                "expected_pages": total_pages,
                "saved_pages": 0,
                "saved_rows": 0,
                "skipped": True,
                "change_report": str(change_report),
                "created_at": datetime.now().isoformat(timespec="seconds"),
            }
            log_path = RAW_ROOT / "run_logs" / f"{rid}.json"
            atomic_write_text(log_path, json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return

        first_path = save_page(
            rid=rid,
            key=key,
            start=first_start,
            end=first_end,
            status=status,
            body=body,
            result_code=result_code,
            result_message=result_message,
            total_count=total_count,
            row_count=row_count,
            change_status=str(probe["status"]),
        )
        saved_pages.append({"start": first_start, "end": first_end, "row_count": row_count, "path": str(first_path)})
        full_page_digests: dict[tuple[int, int], str] = {
            (first_start, first_end): sha256_bytes(body)
        }
        first_period_start, first_period_end = data_period_bounds(body, SERVICE)
        observed_period_starts = [first_period_start] if first_period_start else []
        observed_period_ends = [first_period_end] if first_period_end else []
    except Exception as exc:
        log_failure(
            run_id_value=rid,
            source_id=SOURCE_ID,
            provider=PROVIDER,
            dataset_name=DATASET_NAME,
            failure_type=type(exc).__name__,
            failure_reason_ko=f"{SERVICE} 전체 수집 첫 페이지 실패: {exc}",
            next_action_ko="서울 OpenAPI 키 상태, 일일 호출 제한, 서비스명 변경 여부를 확인하고 재시도한다.",
            request_url_redacted=redact_url(first_url, extra_values=[key]),
        )
        raise

    emit_progress(
        label=DATASET_NAME,
        current_units=1,
        total_units=total_pages,
        unit="페이지",
        eta_seconds=(time.monotonic() - started) * max(0, total_pages - 1),
        data_period_start=probe.get("data_period_start"),
        data_period_end=probe.get("data_period_end"),
    )
    for page_index in range(2, total_pages + 1):
        start = (page_index - 1) * PAGE_SIZE + 1
        end = min(page_index * PAGE_SIZE, total_count)
        url = build_url(key, start, end)
        try:
            status, body, result_code, result_message, page_total_count, row_count = fetch_page_with_retries(url)
            validate_paged_collection_response(
                initial_total_count=total_count,
                page_total_count=page_total_count,
                start=start,
                end=end,
                row_count=row_count,
            )
            path = save_page(
                rid=rid,
                key=key,
                start=start,
                end=end,
                status=status,
                body=body,
                result_code=result_code,
                result_message=result_message,
                total_count=page_total_count,
                row_count=row_count,
                change_status=str(probe["status"]),
            )
            saved_pages.append({"start": start, "end": end, "row_count": row_count, "path": str(path)})
            full_page_digests[(start, end)] = sha256_bytes(body)
            page_period_start, page_period_end = data_period_bounds(body, SERVICE)
            if page_period_start:
                observed_period_starts.append(page_period_start)
            if page_period_end:
                observed_period_ends.append(page_period_end)
            elapsed = time.monotonic() - started
            emit_progress(
                label=DATASET_NAME,
                current_units=page_index,
                total_units=total_pages,
                unit="페이지",
                eta_seconds=(elapsed / page_index) * max(0, total_pages - page_index),
                data_period_start=probe.get("data_period_start"),
                data_period_end=probe.get("data_period_end"),
            )
        except Exception as exc:
            log_failure(
                run_id_value=rid,
                source_id=SOURCE_ID,
                provider=PROVIDER,
                dataset_name=DATASET_NAME,
                failure_type=type(exc).__name__,
                failure_reason_ko=f"{SERVICE} {start}-{end} 페이지 수집 실패: {exc}",
                next_action_ko="실패 페이지만 재시도하고, 같은 오류가 반복되면 호출 제한 또는 서비스 상태를 확인한다.",
                request_url_redacted=redact_url(url, extra_values=[key]),
            )
            failed_pages.append({"start": start, "end": end, "error": type(exc).__name__})
        time.sleep(SLEEP_SECONDS)

    saved_rows = sum(int(page["row_count"]) for page in saved_pages)
    complete = not failed_pages and len(saved_pages) == total_pages and saved_rows == total_count
    current_full_fingerprint = (
        page_digest_set_fingerprint(full_page_digests)
        if complete
        else None
    )
    if complete and current_full_fingerprint == probe.get("previous_full_fingerprint"):
        final_change_status = "unchanged_full"
    elif probe.get("status") == "sample_match_full_refresh_due":
        final_change_status = "revised_full"
    else:
        final_change_status = str(probe.get("status"))
    completed_at = (
        mark_manifest_run_complete(
            run_id_value=rid,
            source_id=SOURCE_ID,
            service_name=SERVICE,
        )
        if complete
        else None
    )
    summary = {
        "run_id": rid,
        "service": SERVICE,
        "dataset_name": DATASET_NAME,
        "collection_date": f"{RUN_DATE[:4]}-{RUN_DATE[4:6]}-{RUN_DATE[6:]}",
        "data_period_start": min(observed_period_starts) if observed_period_starts else None,
        "data_period_end": max(observed_period_ends) if observed_period_ends else None,
        "change_status": final_change_status,
        "probe_status": probe.get("status"),
        "total_count": total_count,
        "page_size": PAGE_SIZE,
        "expected_pages": total_pages,
        "saved_pages": len(saved_pages),
        "saved_rows": saved_rows,
        "failed_pages": failed_pages,
        "content_fingerprint": current_full_fingerprint or probe.get("content_fingerprint"),
        "full_collection_completed_at": completed_at,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    log_path = RAW_ROOT / "run_logs" / f"{rid}.json"
    atomic_write_text(log_path, json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    final_change_entry = {
        **change_entry,
        "change_status": final_change_status,
        "snapshot_date": f"{RUN_DATE[:4]}-{RUN_DATE[4:6]}-{RUN_DATE[6:]}",
        "data_period_start": summary["data_period_start"],
        "data_period_end": summary["data_period_end"],
        "content_fingerprint": summary["content_fingerprint"],
        "full_content_fingerprint": current_full_fingerprint,
        "full_collection_completed_at": completed_at,
    }
    if complete:
        update_collection_change_report([final_change_entry])
        update_source_state_catalog([final_change_entry])
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if not complete:
        raise RuntimeError(
            "서울 매출 원천 수집이 완전하지 않습니다: "
            f"saved_pages={len(saved_pages)}/{total_pages}, "
            f"saved_rows={summary['saved_rows']}/{total_count}, "
            f"failed_pages={len(failed_pages)}"
        )


if __name__ == "__main__":
    main()
