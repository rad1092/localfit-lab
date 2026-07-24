from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from ingest_common import (
    MANIFEST_FIELDS,
    RAW_ROOT,
    classify_seoul_probe,
    data_period_bounds,
    emit_progress,
    ensure_csv,
    log_failure,
    mark_manifest_run_complete,
    now_utc,
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
from ingest_seoul_transport_accessibility_sources import (
    PROVIDER,
    fetch_api_with_retries,
    parse_seoul_response,
    seoul_api_url,
)


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = RAW_ROOT / "source_registry.csv"
RUN_DATE = run_date()


CORE_SERVICES = [
    {
        "source_id": "seoul_trade_area_boundary",
        "service": "TbgisTrdarRelm",
        "dataset_name": "서울 상권분석서비스 영역-상권 전체 원응답",
        "relative_dir": "trade_area_boundary",
        "spatial_unit": "상권 폴리곤/상권",
        "time_unit": "기준연도/버전",
        "area_code_type": "상권코드",
        "quality_notes_ko": "후보지 좌표를 공식 상권에 연결하기 위한 상권 경계/속성 원응답이다.",
    },
    {
        "source_id": "seoul_floating_population_trade_area",
        "service": "VwsmTrdarFlpopQq",
        "dataset_name": "서울 상권분석서비스 길단위인구-상권 전체 원응답",
        "relative_dir": "floating_population_trade_area",
        "spatial_unit": "상권",
        "time_unit": "분기/시간대",
        "area_code_type": "상권코드",
        "quality_notes_ko": "상권별 시간대·성별·연령대 유동 수요를 설명하는 핵심 원응답이다.",
    },
    {
        "source_id": "seoul_resident_worker_population_trade_area",
        "service": "VwsmTrdarRepopQq",
        "dataset_name": "서울 상권분석서비스 상주인구-상권 전체 원응답",
        "relative_dir": "resident_population_trade_area",
        "spatial_unit": "상권",
        "time_unit": "분기/연",
        "area_code_type": "상권코드",
        "quality_notes_ko": "상권 주변 거주 수요와 생활권 성격을 설명하는 상주인구 원응답이다.",
    },
    {
        "source_id": "seoul_resident_worker_population_trade_area",
        "service": "VwsmTrdarWrcPopltnQq",
        "dataset_name": "서울 상권분석서비스 직장인구-상권 전체 원응답",
        "relative_dir": "worker_population_trade_area",
        "spatial_unit": "상권",
        "time_unit": "분기/연",
        "area_code_type": "상권코드",
        "quality_notes_ko": "오피스형 수요와 평일 주간 유입 해석을 보강하는 직장인구 원응답이다.",
    },
    {
        "source_id": "seoul_trade_area_change_index",
        "service": "VwsmTrdarIxQq",
        "dataset_name": "서울 상권분석서비스 상권변화지표 전체 원응답",
        "relative_dir": "trade_area_change_index",
        "spatial_unit": "상권",
        "time_unit": "분기/연",
        "area_code_type": "상권코드",
        "quality_notes_ko": "상권의 성장·정체·쇠퇴와 평균 영업/폐업기간을 해석하기 위한 변화지표 원응답이다.",
    },
    {
        "source_id": "seoul_facility_trade_area",
        "service": "VwsmTrdarFcltyQq",
        "dataset_name": "서울 상권분석서비스 집객시설-상권 전체 원응답",
        "relative_dir": "facility_trade_area",
        "spatial_unit": "상권",
        "time_unit": "기준연도",
        "area_code_type": "상권코드",
        "quality_notes_ko": "학교·병원·교통시설·관공서 등 앵커시설 접근성을 보강하는 집객시설 원응답이다.",
    },
]

STORE_SERVICE = {
    "source_id": "seoul_store_trade_area",
    "service": "VwsmTrdarStorQq",
    "dataset_name": "서울 상권분석서비스 점포-상권 전체 원응답",
    "relative_dir": "store_trade_area",
    "spatial_unit": "상권",
    "time_unit": "분기/연",
    "area_code_type": "상권코드+서비스업종코드",
    "quality_notes_ko": "상권별 점포수, 유사업종, 프랜차이즈, 개폐업률을 설명하는 경쟁/안정성 핵심 원응답이다.",
}


def _manifest_raw_path(row: dict[str, Any]) -> Path | None:
    value = str(row.get("raw_path") or "").strip()
    if not value:
        return None
    path = Path(value)
    return (path if path.is_absolute() else ROOT / path).resolve()


def _validated_same_day_resume(
    *,
    service_name: str,
    source_id: str,
    page_size: int,
    total_count: int,
    live_first_body: bytes,
) -> dict[str, Any]:
    """Load only a manifest-backed, consecutive partial snapshot for RUN_DATE.

    A resume is deliberately fail-closed.  Existing pages must be the exact
    numeric prefix for the current collection date, must still match their
    manifest digests and provider metadata, and page 1 must match the live
    probe.  This prevents a long refresh from silently combining two provider
    snapshots or adopting an older date's pages.
    """
    raw_directory = (
        RAW_ROOT / RUN_DATE / "seoul_open_data" / "full" / service_name
    ).resolve()
    empty = {
        "page_count": 0,
        "collected_rows": 0,
        "page_digests": {},
        "period_starts": [],
        "period_ends": [],
        "manifest_rows": [],
    }
    if not raw_directory.exists():
        return empty
    if not raw_directory.is_dir():
        raise RuntimeError(f"resume path is not a directory: {raw_directory}")

    expected_parent = (
        RAW_ROOT / RUN_DATE / "seoul_open_data" / "full"
    ).resolve()
    try:
        relative_directory = raw_directory.relative_to(expected_parent)
    except ValueError as exc:
        raise RuntimeError(
            f"resume directory is outside the current RUN_DATE: {raw_directory}"
        ) from exc
    if relative_directory.parts != (service_name,):
        raise RuntimeError(f"unexpected resume directory: {raw_directory}")

    pattern = re.compile(
        rf"^{re.escape(service_name)}_(\d+)_(\d+)\.json$"
    )
    page_files: list[tuple[int, int, Path]] = []
    for path in raw_directory.glob(f"{service_name}_*.json"):
        resolved = path.resolve()
        if resolved.parent != raw_directory:
            raise RuntimeError(f"resume page escaped its service directory: {path}")
        match = pattern.fullmatch(path.name)
        if not match:
            raise RuntimeError(f"invalid resume page filename: {path.name}")
        page_files.append((int(match.group(1)), int(match.group(2)), resolved))
    if not page_files:
        return empty
    page_files.sort(key=lambda item: (item[0], item[1]))

    pages = math.ceil(total_count / page_size)
    expected_ranges = [
        (
            (page_no - 1) * page_size + 1,
            page_size
            if page_no == 1
            else min(page_no * page_size, total_count),
        )
        for page_no in range(1, pages + 1)
    ]
    actual_ranges = [(start, end) for start, end, _path in page_files]
    if actual_ranges != expected_ranges[: len(actual_ranges)]:
        raise RuntimeError(
            "same-day resume files are not one consecutive page prefix: "
            f"actual_tail={actual_ranges[-3:]}, expected_tail="
            f"{expected_ranges[:len(actual_ranges)][-3:]}"
        )
    if len(page_files) > pages:
        raise RuntimeError(
            f"resume has more pages than the live snapshot: {len(page_files)} > {pages}"
        )

    manifest_path = RAW_ROOT / "ingest_manifest.csv"
    if not manifest_path.exists():
        raise RuntimeError(
            f"same-day resume pages exist without an ingest manifest: {raw_directory}"
        )
    with manifest_path.open(encoding="utf-8-sig", newline="") as handle:
        manifest_rows = [dict(row) for row in csv.DictReader(handle)]

    snapshot_date = f"{RUN_DATE[:4]}-{RUN_DATE[4:6]}-{RUN_DATE[6:]}"
    directory_rows: list[dict[str, Any]] = []
    rows_by_path: dict[Path, list[dict[str, Any]]] = {}
    for row in manifest_rows:
        if str(row.get("snapshot_date") or "") != snapshot_date:
            continue
        raw_path_value = str(row.get("raw_path") or "").replace("\\", "/")
        if not raw_path_value.endswith(".json") or (
            f"/{RUN_DATE}/seoul_open_data/full/{service_name}/"
            not in f"/{raw_path_value.lstrip('/')}"
        ):
            continue
        raw_path = _manifest_raw_path(row)
        if (
            raw_path is None
            or raw_path.parent != raw_directory
        ):
            continue
        directory_rows.append(row)
        rows_by_path.setdefault(raw_path, []).append(row)
    if any(
        str(row.get("full_collection_status") or "") == "complete"
        for row in directory_rows
    ):
        raise RuntimeError(
            "the current RUN_DATE snapshot is already manifest-complete; "
            "it cannot be reused as a partial resume"
        )

    selected_manifest_rows: list[dict[str, Any]] = []
    page_digests: dict[tuple[int, int], str] = {}
    period_starts: list[str] = []
    period_ends: list[str] = []
    collected_rows = 0
    live_first_digest = sha256_bytes(live_first_body)
    live_first_period = data_period_bounds(live_first_body, service_name)

    for page_number, (start, end, path) in enumerate(page_files, start=1):
        candidates = [
            row
            for row in rows_by_path.get(path, [])
            if str(row.get("source_id") or "") == source_id
            and str(row.get("collection_status") or "") == "success"
        ]
        if not candidates:
            raise RuntimeError(f"resume page has no successful manifest row: {path}")
        manifest_row = max(candidates, key=lambda row: str(row.get("collected_at") or ""))
        try:
            request_params = json.loads(
                str(manifest_row.get("request_params_json") or "{}")
            )
        except ValueError as exc:
            raise RuntimeError(f"invalid manifest request params for {path}") from exc
        if not isinstance(request_params, dict) or (
            str(request_params.get("service") or "") != service_name
            or int(request_params.get("start") or 0) != start
            or int(request_params.get("end") or 0) != end
        ):
            raise RuntimeError(
                f"manifest request range does not match resume file {path.name}"
            )
        if str(manifest_row.get("provider_result_code") or "") != "INFO-000":
            raise RuntimeError(
                f"resume manifest provider result is not INFO-000: {path.name}"
            )

        body = path.read_bytes()
        digest = sha256_bytes(body)
        if digest != str(manifest_row.get("sha256") or ""):
            raise RuntimeError(f"resume page digest does not match manifest: {path.name}")
        code, message, page_total, rows = parse_seoul_response(service_name, body)
        if code != "INFO-000":
            raise RuntimeError(
                f"resume page provider result is not INFO-000: {path.name}: {code} {message}"
            )
        validate_paged_collection_response(
            initial_total_count=total_count,
            page_total_count=page_total,
            start=start,
            end=end,
            row_count=len(rows),
        )
        page_period_start, page_period_end = data_period_bounds(body, service_name)
        manifest_period_start = str(manifest_row.get("data_period_start") or "")
        manifest_period_end = str(manifest_row.get("data_period_end") or "")
        if manifest_period_start and manifest_period_start != (page_period_start or ""):
            raise RuntimeError(f"resume page period start drifted: {path.name}")
        if manifest_period_end and manifest_period_end != (page_period_end or ""):
            raise RuntimeError(f"resume page period end drifted: {path.name}")
        if page_number == 1 and (
            digest != live_first_digest
            or (page_period_start, page_period_end) != live_first_period
        ):
            raise RuntimeError(
                "same-day partial page 1 differs from the live first-page probe; "
                "refusing to combine provider snapshots"
            )

        collected_rows += len(rows)
        page_digests[(start, end)] = digest
        if page_period_start:
            period_starts.append(page_period_start)
        if page_period_end:
            period_ends.append(page_period_end)
        selected_manifest_rows.append(manifest_row)

    return {
        "page_count": len(page_files),
        "collected_rows": collected_rows,
        "page_digests": page_digests,
        "period_starts": period_starts,
        "period_ends": period_ends,
        "manifest_rows": selected_manifest_rows,
    }


def _adopt_resumed_manifest_rows(
    *,
    run_id_value: str,
    manifest_rows: list[dict[str, Any]],
) -> None:
    """Register validated existing pages under the new run before completion."""
    if not manifest_rows:
        return
    path = RAW_ROOT / "ingest_manifest.csv"
    ensure_csv(path, MANIFEST_FIELDS)
    adopted_at = now_utc()
    with path.open("a", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_FIELDS)
        for existing in manifest_rows:
            adopted = {field: existing.get(field, "") for field in MANIFEST_FIELDS}
            adopted.update(
                {
                    "run_id": run_id_value,
                    "full_collection_status": "",
                    "full_collection_completed_at": "",
                    "collected_at": adopted_at,
                }
            )
            writer.writerow(adopted)
        handle.flush()
        os.fsync(handle.fileno())


def update_registry_status(source_ids: set[str]) -> None:
    if not REGISTRY.exists():
        return
    with REGISTRY.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames or []
        rows = list(reader)
    for row in rows:
        if row.get("source_id") in source_ids:
            row["current_status"] = "collected_raw"
            note = row.get("notes_ko", "")
            display_run_date = (
                f"{RUN_DATE[:4]}-{RUN_DATE[4:6]}-{RUN_DATE[6:8]}"
                if len(RUN_DATE) == 8 and RUN_DATE.isdigit()
                else RUN_DATE
            )
            registry_note = f"{display_run_date} 서울 OpenAPI 전체 원응답 수집 완료."
            if registry_note not in note:
                row["notes_ko"] = (note + " " + registry_note).strip()
    with REGISTRY.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def collect_full_service(
    *,
    rid: str,
    key: str,
    page_size: int = 1000,
    skip_unchanged: bool = False,
    **service: Any,
) -> dict[str, Any]:
    api_service = service["service"]
    source_id = service["source_id"]
    dataset_name = service["dataset_name"]
    first_url = seoul_api_url(key, api_service, 1, page_size)
    redacted_first = redact_url(first_url, extra_values=[key])
    started = time.monotonic()
    try:
        status, body, code, msg, total, rows = fetch_api_with_retries(first_url, api_service, attempts=4)
        if code != "INFO-000":
            log_failure(
                run_id_value=rid,
                source_id=source_id,
                provider=PROVIDER,
                dataset_name=dataset_name,
                failure_type=code or "provider_error",
                failure_reason_ko=f"{api_service} 첫 페이지가 정상 응답이 아님: {msg}",
                next_action_ko="서울 OpenAPI 서비스명과 데이터 적재 상태를 확인하고 재시도한다.",
                request_url_redacted=redacted_first,
            )
            return {"source_id": source_id, "service": api_service, "status": "failed", "code": code, "message": msg}

        if total <= 0 or not rows:
            raise RuntimeError(f"{api_service} 원천이 비어 있습니다: total_count={total}, first_page_rows={len(rows)}")
        validate_paged_collection_response(
            initial_total_count=total,
            page_total_count=total,
            start=1,
            end=page_size,
            row_count=len(rows),
        )
        pages = math.ceil(total / page_size) if total else 1
        probe_bodies: dict[tuple[int, int], bytes] = {(1, page_size): body}
        if skip_unchanged and pages > 1:
            sample_page_numbers = sorted({max(2, (pages + 1) // 2), pages})
            for sample_page in sample_page_numbers:
                start = (sample_page - 1) * page_size + 1
                end = min(sample_page * page_size, total)
                sample_url = seoul_api_url(key, api_service, start, end)
                _, sample_body, sample_code, sample_message, sample_total, sample_rows = fetch_api_with_retries(
                    sample_url, api_service, attempts=4
                )
                if sample_code != "INFO-000":
                    raise RuntimeError(f"{sample_code}: {sample_message}")
                validate_paged_collection_response(
                    initial_total_count=total,
                    page_total_count=sample_total,
                    start=start,
                    end=end,
                    row_count=len(sample_rows),
                )
                probe_bodies[(start, end)] = sample_body
        probe = classify_seoul_probe(
            source_id=source_id,
            service_name=api_service,
            total_count=total,
            sample_bodies=probe_bodies,
        )
        emit_progress(
            label=f"{dataset_name} 변경 확인",
            current_units=len(probe_bodies),
            total_units=len(probe_bodies),
            unit="표본 페이지",
            data_period_start=probe.get("data_period_start"),
            data_period_end=probe.get("data_period_end"),
            message=str(probe.get("status")),
        )
        if skip_unchanged and probe["status"] == "unchanged_sampled":
            return {
                **probe,
                "source_id": source_id,
                "service": api_service,
                "status": "success",
                "change_status": probe["status"],
                "probe_status": probe["status"],
                "skipped": True,
                "total_count": total,
                "collected_rows": 0,
                "pages": pages,
                "failures": 0,
                "full_content_fingerprint": probe.get("previous_full_fingerprint"),
            }

        period_start, period_end = data_period_bounds(body, api_service)
        resume = _validated_same_day_resume(
            service_name=api_service,
            source_id=source_id,
            page_size=page_size,
            total_count=total,
            live_first_body=body,
        )
        resumed_pages = int(resume["page_count"])
        downloaded_pages = 0
        if resumed_pages:
            _adopt_resumed_manifest_rows(
                run_id_value=rid,
                manifest_rows=resume["manifest_rows"],
            )
            collected_rows = int(resume["collected_rows"])
            full_page_digests = dict(resume["page_digests"])
            observed_period_starts = list(resume["period_starts"])
            observed_period_ends = list(resume["period_ends"])
        else:
            collected_rows = len(rows)
            full_page_digests = {(1, page_size): sha256_bytes(body)}
            observed_period_starts = [period_start] if period_start else []
            observed_period_ends = [period_end] if period_end else []
            write_raw(
            run_id_value=rid,
            source_id=source_id,
            provider=PROVIDER,
            dataset_name=dataset_name,
            body=body,
            relative_path=f"{RUN_DATE}/seoul_open_data/full/{api_service}/{api_service}_1_{page_size}.json",
            request_url_redacted=redacted_first,
            request_params={"service": api_service, "start": 1, "end": page_size, "key": "<redacted>"},
            http_status=status,
            provider_result_code=code,
            provider_result_message=msg,
            spatial_unit=service["spatial_unit"],
            time_unit=service["time_unit"],
            source_period=period_end or "버전형 원천",
            area_code_type=service["area_code_type"],
            quality_notes_ko=f"{service['quality_notes_ko']} list_total_count={total}, page_rows={len(rows)}.",
            data_period_start=period_start,
            data_period_end=period_end,
            change_status=str(probe["status"]),
            )
            downloaded_pages = 1
        emit_progress(
            label=dataset_name,
            current_units=max(1, resumed_pages),
            total_units=pages,
            unit="페이지",
            eta_seconds=(time.monotonic() - started)
            * max(0, pages - max(1, resumed_pages)),
            data_period_start=min(observed_period_starts)
            if observed_period_starts
            else period_start,
            data_period_end=max(observed_period_ends)
            if observed_period_ends
            else period_end,
            message=f"resumed_pages={resumed_pages}",
        )

        failures = 0
        first_download_page = resumed_pages + 1 if resumed_pages else 2
        for page_no in range(first_download_page, pages + 1):
            start = (page_no - 1) * page_size + 1
            end = min(page_no * page_size, total)
            url = seoul_api_url(key, api_service, start, end)
            redacted = redact_url(url, extra_values=[key])
            try:
                page_status, page_body, page_code, page_msg, page_total, page_rows = fetch_api_with_retries(url, api_service, attempts=4)
                if page_code != "INFO-000":
                    raise RuntimeError(f"{page_code}: {page_msg}")
                validate_paged_collection_response(
                    initial_total_count=total,
                    page_total_count=page_total,
                    start=start,
                    end=end,
                    row_count=len(page_rows),
                )
                collected_rows += len(page_rows)
                downloaded_pages += 1
                full_page_digests[(start, end)] = sha256_bytes(page_body)
                page_period_start, page_period_end = data_period_bounds(page_body, api_service)
                if page_period_start:
                    observed_period_starts.append(page_period_start)
                if page_period_end:
                    observed_period_ends.append(page_period_end)
                write_raw(
                    run_id_value=rid,
                    source_id=source_id,
                    provider=PROVIDER,
                    dataset_name=dataset_name,
                    body=page_body,
                    relative_path=f"{RUN_DATE}/seoul_open_data/full/{api_service}/{api_service}_{start}_{end}.json",
                    request_url_redacted=redacted,
                    request_params={"service": api_service, "start": start, "end": end, "key": "<redacted>"},
                    http_status=page_status,
                    provider_result_code=page_code,
                    provider_result_message=page_msg,
                    spatial_unit=service["spatial_unit"],
                    time_unit=service["time_unit"],
                    source_period=page_period_end or period_end or "버전형 원천",
                    area_code_type=service["area_code_type"],
                    quality_notes_ko=f"{service['quality_notes_ko']} list_total_count={total}, page_rows={len(page_rows)}.",
                    data_period_start=page_period_start,
                    data_period_end=page_period_end,
                    change_status=str(probe["status"]),
                )
                elapsed = time.monotonic() - started
                emit_progress(
                    label=dataset_name,
                    current_units=page_no,
                    total_units=pages,
                    unit="페이지",
                    eta_seconds=(elapsed / page_no) * max(0, pages - page_no),
                    data_period_start=probe.get("data_period_start"),
                    data_period_end=probe.get("data_period_end"),
                )
            except Exception as exc:
                failures += 1
                log_failure(
                    run_id_value=rid,
                    source_id=source_id,
                    provider=PROVIDER,
                    dataset_name=dataset_name,
                    failure_type=type(exc).__name__,
                    failure_reason_ko=f"{api_service} {start}-{end} 페이지 수집 실패: {exc}",
                    next_action_ko="실패 페이지만 재시도하고 동일 오류가 반복되면 서울 OpenAPI 상태를 확인한다.",
                    request_url_redacted=redacted,
                )
                # Preserve a consecutive prefix so a future invocation can
                # resume exactly this failed page without adopting later gaps.
                break

        complete = failures == 0 and collected_rows == total
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
                source_id=source_id,
                service_name=api_service,
            )
            if complete
            else None
        )
        return {
            "source_id": source_id,
            "service": api_service,
            "status": "success" if complete else "partial",
            "total_count": total,
            "collected_rows": collected_rows,
            "pages": pages,
            "resumed_pages": resumed_pages,
            "downloaded_pages": downloaded_pages,
            "failures": failures,
            "change_status": final_change_status,
            "probe_status": probe["status"],
            "skipped": False,
            "data_period_start": min(observed_period_starts) if observed_period_starts else None,
            "data_period_end": max(observed_period_ends) if observed_period_ends else None,
            "content_fingerprint": current_full_fingerprint or probe.get("content_fingerprint"),
            "full_content_fingerprint": current_full_fingerprint,
            "previous_snapshot_date": probe.get("previous_snapshot_date"),
            "full_collection_completed_at": completed_at,
        }
    except Exception as exc:
        log_failure(
            run_id_value=rid,
            source_id=source_id,
            provider=PROVIDER,
            dataset_name=dataset_name,
            failure_type=type(exc).__name__,
            failure_reason_ko=f"{api_service} 전체 수집 실패: {exc}",
            next_action_ko="서비스명과 네트워크 상태를 확인하고 재시도한다.",
            request_url_redacted=redacted_first,
        )
        return {"source_id": source_id, "service": api_service, "status": "failed", "error": str(exc)}


def write_log(rid: str, results: list[dict[str, Any]], include_store: bool, store_only: bool = False) -> None:
    if store_only:
        path = RAW_ROOT / "run_logs" / f"{RUN_DATE}_seoul_core_p0_store_only_ko.md"
    else:
        path = RAW_ROOT / "run_logs" / (f"{RUN_DATE}_seoul_core_p0_full_with_store_ko.md" if include_store else f"{RUN_DATE}_seoul_core_p0_full_ko.md")
    lines = [
        f"# {RUN_DATE} 서울 상권분석서비스 P0 전체 원응답 수집 기록",
        "",
        f"- 실행 ID: `{rid}`",
        f"- 점포-상권 포함 여부: {'점포만 수집' if store_only else ('포함' if include_store else '제외')}",
        "- 목적: 기존 CSV 또는 1,000건 샘플 수준으로만 남아 있던 핵심 상권분석 원천을 서울 OpenAPI 원응답 단위로 보존한다.",
        "",
        "## 결과",
    ]
    for item in results:
        lines.append(f"- `{item.get('service')}` / `{item.get('source_id')}`: {json.dumps(item, ensure_ascii=False)}")
    if not include_store and not store_only:
        lines.extend(
            [
                "",
                "## 점포-상권 대용량 서비스 분리 사유",
                "- `VwsmTrdarStorQq`는 첫 페이지 기준 전체 2,212,672행이다.",
                "- 이미 기존 CSV 원천과 점포 중복/대표 파일 감사가 있으므로, API 전체 수집은 별도 장시간 실행으로 분리한다.",
                "- 알고리즘 강화 단계에서는 기존 CSV canonical 원천을 우선 사용하고, API 원응답 전체가 필요하면 `--include-store`로 별도 실행한다.",
            ]
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--include-store", action="store_true", help="2.2M rows store-trade-area service까지 수집")
    parser.add_argument("--store-only", action="store_true", help="점포-상권 대용량 서비스만 수집")
    parser.add_argument(
        "--skip-unchanged",
        action="store_true",
        help="첫·중간·마지막 페이지와 전체 건수/기간이 동일하면 전체 다운로드를 생략",
    )
    args = parser.parse_args()

    key = parse_key_file()["seoul_key"]
    if not key:
        raise RuntimeError("서울 열린데이터광장 키를 key.md에서 찾지 못했다.")
    if args.store_only:
        services = [STORE_SERVICE]
    else:
        services = list(CORE_SERVICES)
    if args.include_store and not args.store_only:
        services.append(STORE_SERVICE)

    rid = run_id("seoul_core_p0_store_only" if args.store_only else ("seoul_core_p0_full_store" if args.include_store else "seoul_core_p0_full"))
    results = [
        collect_full_service(rid=rid, key=key, skip_unchanged=args.skip_unchanged, **service)
        for service in services
    ]
    change_report = update_collection_change_report(results)
    update_source_state_catalog(
        [
            {
                **{
                    key: result.get(key)
                    for key in (
                        "skipped",
                        "samples_match",
                        "sample_count",
                        "sampled_skip_allowed",
                        "sampled_skip_ttl_hours",
                        "last_full_collection_at",
                        "last_full_collection_age_hours",
                        "full_collection_completed_at",
                        "probe_status",
                        "previous_snapshot_date",
                        "previous_total_count",
                        "full_content_fingerprint",
                    )
                    if result.get(key) is not None
                },
                "source_id": result.get("source_id"),
                "service": result.get("service"),
                "snapshot_date": (
                    result.get("previous_snapshot_date")
                    if result.get("skipped")
                    else f"{RUN_DATE[:4]}-{RUN_DATE[4:6]}-{RUN_DATE[6:]}"
                ),
                "total_count": result.get("total_count"),
                "data_period_start": result.get("data_period_start"),
                "data_period_end": result.get("data_period_end"),
                "content_fingerprint": result.get("full_content_fingerprint")
                or result.get("content_fingerprint"),
                "change_status": result.get("change_status"),
            }
            for result in results
            if result.get("status") == "success"
        ]
    )
    successful_source_ids = {
        r["source_id"]
        for r in results
        if r.get("status") == "success" and not r.get("skipped")
    }
    update_registry_status(successful_source_ids)
    summary = {
        "run_id": rid,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "collection_date": f"{RUN_DATE[:4]}-{RUN_DATE[4:6]}-{RUN_DATE[6:]}",
        "include_store": args.include_store,
        "store_only": args.store_only,
        "skip_unchanged": args.skip_unchanged,
        "change_report": str(change_report),
        "results": results,
    }
    (RAW_ROOT / "run_logs" / f"{rid}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_log(rid, results, args.include_store, args.store_only)
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    incomplete = [result for result in results if result.get("status") != "success"]
    if incomplete:
        details = ", ".join(
            f"{result.get('service')}={result.get('status')}"
            for result in incomplete
        )
        raise RuntimeError(f"서울 핵심 원천 수집이 완전하지 않습니다: {details}")


if __name__ == "__main__":
    main()
