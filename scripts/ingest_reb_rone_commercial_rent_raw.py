from __future__ import annotations

import csv
import hashlib
import json
import math
import urllib.parse
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ingest_common import (
    MANIFEST_FIELDS,
    RAW_ROOT,
    append_csv,
    http_get,
    latest_complete_full_collection,
    log_failure,
    mark_manifest_run_complete,
    parse_key_file,
    redact_url,
    run_date,
    run_id,
    sha256_bytes,
    now_utc,
    write_raw,
)


RUN_DATE = run_date()
SOURCE_ID = "reb_small_shop_rent"
SERVICE_NAME = "reb_rone_seoul_commercial_market"
PROVIDER = "한국부동산원 R-ONE"
DATA_URL = "https://www.reb.or.kr/r-one/openapi/SttsApiTblData.do"
P_SIZE = 1000
SEOUL_NAME = "서울"
KEY_MONEY_CLASS_NAMES = {
    "500001": "전체",
    "500002": "도매 및 소매",
    "500003": "숙박 및 음식점업",
    "500004": "부동산 및 임대업",
    "500005": "예술,스포츠 및, 여가 관련 서비스업",
    "500006": "협회 및 단체,수리 및  기타 개인 서비스업",
}
KEY_MONEY_ITEM_CONTRACT = {
    "100001": ("권리금 유 비율", "%"),
    "100002": ("권리금 수준_평균", "만원"),
    "100003": ("권리금 수준_중위수", "만원"),
    "100004": ("권리금 수준_㎡당 평균", "만원/㎡"),
}


@dataclass(frozen=True)
class TableSpec:
    statbl_id: str
    name: str
    group: str
    purpose: str
    cycle: str
    cls_id: str
    itm_id: str | None
    expected_item_name: str | None
    allowed_item_ids: frozenset[str] = frozenset()
    region_dimension: str = "CLS"


# These identifiers were checked against SttsApiTblItm and SttsApiTblData on
# 2026-07-17.  Two legacy tables use different dimension identifiers, so the
# identifiers must remain table-specific instead of being derived globally.
TABLE_SPECS: tuple[TableSpec, ...] = (
    TableSpec("T244363134858603", "중대형상가 임대료", "임대료", "서울 임대비용 기준", "QY", "500002", "100001", "임대료"),
    TableSpec("T248223134698125", "소규모상가 임대료", "임대료", "서울 임대비용 기준", "QY", "500002", "100001", "임대료"),
    TableSpec("T244913134948657", "집합상가 임대료", "임대료", "서울 임대비용 기준", "QY", "500002", "100001", "임대료"),
    TableSpec("T249633134845544", "중대형상가 공실률", "공실률", "서울 상가 수요 위험 기준", "QY", "500002", "100001", "공실률"),
    TableSpec("T241833134686576", "소규모상가 공실률", "공실률", "서울 상가 수요 위험 기준", "QY", "500002", "100001", "공실률"),
    TableSpec("T243283134931290", "집합상가 공실률", "공실률", "서울 상가 수요 위험 기준", "QY", "500002", "100001", "공실률"),
    TableSpec("T249863134832916", "중대형상가 임대가격지수", "임대가격지수", "서울 임대가격 변화율 기준", "QY", "500002", "100001", "지수"),
    TableSpec("T241273134677393", "소규모상가 임대가격지수", "임대가격지수", "서울 임대가격 변화율 기준", "QY", "500002", "100001", "지수"),
    TableSpec("T242433134965708", "집합상가 임대가격지수", "임대가격지수", "서울 임대가격 변화율 기준", "QY", "500002", "100001", "지수"),
    TableSpec("T242963134993964", "통합상가 임대가격지수", "임대가격지수", "서울 상가유형 통합 추세", "QY", "50002", "10001", "지수"),
    TableSpec("T241883134877452", "중대형상가 전환율", "전환율", "보증금의 월 임대료 환산", "QY", "500002", "100002", "전환율"),
    TableSpec("T246253134905233", "소규모상가 전환율", "전환율", "보증금의 월 임대료 환산", "QY", "500002", "100001", "전환율"),
    TableSpec("T243133134985812", "집합상가 전환율", "전환율", "보증금의 월 임대료 환산", "QY", "500002", "100001", "전환율"),
    TableSpec(
        "A_2024_00445",
        "시도별·업종별 상가권리금",
        "권리금",
        "서울 업종별 권리금 수준과 유비율 기준",
        "YY",
        "900002",
        None,
        None,
        frozenset({"100001", "100002", "100003", "100004"}),
        "GRP",
    ),
)


def parse_result(body: bytes) -> dict[str, Any]:
    data = json.loads(body.decode("utf-8"))
    if "RESULT" in data:
        result = data["RESULT"]
        return {
            "ok": False,
            "code": str(result.get("CODE", "")),
            "message": str(result.get("MESSAGE", "")),
            "total_count": 0,
            "rows": [],
        }

    blocks = data.get("SttsApiTblData")
    if not isinstance(blocks, list) or not blocks:
        return {
            "ok": False,
            "code": "unexpected_shape",
            "message": type(data).__name__,
            "total_count": 0,
            "rows": [],
        }

    head = blocks[0].get("head", []) if isinstance(blocks[0], dict) else []
    total_count = 0
    result_code = ""
    result_message = ""
    for item in head:
        if not isinstance(item, dict):
            continue
        if "list_total_count" in item:
            total_count = int(item.get("list_total_count") or 0)
        if isinstance(item.get("RESULT"), dict):
            result_code = str(item["RESULT"].get("CODE", ""))
            result_message = str(item["RESULT"].get("MESSAGE", ""))

    rows: list[dict[str, Any]] = []
    if len(blocks) > 1 and isinstance(blocks[1], dict) and isinstance(blocks[1].get("row"), list):
        rows = [row for row in blocks[1]["row"] if isinstance(row, dict)]
    return {
        "ok": result_code in {"INFO-000", ""},
        "code": result_code,
        "message": result_message,
        "total_count": total_count,
        "rows": rows,
    }


def request_params(spec: TableSpec, page_index: int) -> dict[str, str]:
    params = {
        "Type": "json",
        "STATBL_ID": spec.statbl_id,
        "DTACYCLE_CD": spec.cycle,
        f"{spec.region_dimension}_ID": spec.cls_id,
        "pIndex": str(page_index),
        "pSize": str(P_SIZE),
    }
    if spec.itm_id:
        params["ITM_ID"] = spec.itm_id
    return params


def request_url(spec: TableSpec, key: str, page_index: int) -> tuple[str, dict[str, str]]:
    public_params = request_params(spec, page_index)
    url = DATA_URL + "?" + urllib.parse.urlencode({"KEY": key, **public_params})
    return url, public_params


def safe_exception_message(exc: Exception, key: str) -> str:
    message = str(exc)
    return message.replace(key, "<redacted>") if key else message


def natural_key(row: dict[str, Any]) -> tuple[str, ...]:
    return tuple(
        str(row.get(field) if row.get(field) is not None else "")
        for field in (
            "STATBL_ID",
            "DTACYCLE_CD",
            "WRTTIME_IDTFR_ID",
            "GRP_ID",
            "CLS_ID",
            "ITM_ID",
        )
    )


def validate_rows(spec: TableSpec, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise RuntimeError(f"{spec.statbl_id} returned no Seoul rows")

    region_id_field = f"{spec.region_dimension}_ID"
    region_name_field = f"{spec.region_dimension}_NM"
    invalid_regions = {
        (str(row.get(region_id_field)), str(row.get(region_name_field)))
        for row in rows
        if str(row.get(region_id_field)) != spec.cls_id
        or str(row.get(region_name_field)) != SEOUL_NAME
    }
    if invalid_regions:
        raise RuntimeError(
            f"{spec.statbl_id} returned non-Seoul rows: {sorted(invalid_regions)[:5]}"
        )

    invalid_tables = {str(row.get("STATBL_ID")) for row in rows if str(row.get("STATBL_ID")) != spec.statbl_id}
    if invalid_tables:
        raise RuntimeError(f"{spec.statbl_id} response contained other tables: {sorted(invalid_tables)}")

    invalid_cycles = {str(row.get("DTACYCLE_CD")) for row in rows if str(row.get("DTACYCLE_CD")) != spec.cycle}
    if invalid_cycles:
        raise RuntimeError(f"{spec.statbl_id} response contained other cycles: {sorted(invalid_cycles)}")

    item_ids = {str(row.get("ITM_ID")) for row in rows}
    if spec.itm_id and item_ids != {spec.itm_id}:
        raise RuntimeError(f"{spec.statbl_id} item mismatch: {sorted(item_ids)}")
    if spec.allowed_item_ids and not item_ids.issubset(set(spec.allowed_item_ids)):
        raise RuntimeError(
            f"{spec.statbl_id} returned unknown items: allowed={sorted(spec.allowed_item_ids)}, actual={sorted(item_ids)}"
        )
    if spec.expected_item_name:
        item_names = {str(row.get("ITM_NM")) for row in rows}
        if item_names != {spec.expected_item_name}:
            raise RuntimeError(f"{spec.statbl_id} item name mismatch: {sorted(item_names)}")


def filtered_page_body(parsed: dict[str, Any], rows: list[dict[str, Any]]) -> bytes:
    payload = {
        "SttsApiTblData": [
            {
                "head": [
                    {"list_total_count": int(parsed["total_count"])},
                    {
                        "RESULT": {
                            "CODE": parsed["code"] or "INFO-000",
                            "MESSAGE": parsed["message"] or "정상 처리되었습니다.",
                        }
                    },
                ]
            },
            {"row": rows},
        ]
    }
    return (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def collect_table(key: str, spec: TableSpec) -> dict[str, Any]:
    first_url, first_public_params = request_url(spec, key, 1)
    status, body, _headers = http_get(first_url, timeout=90)
    parsed = parse_result(body)
    if not parsed["ok"]:
        raise RuntimeError(f"provider {parsed['code']}: {parsed['message']}")

    total_count = int(parsed["total_count"])
    if total_count <= 0:
        raise RuntimeError("provider returned an empty filtered result")
    total_pages = max(1, math.ceil(total_count / P_SIZE))
    pages: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []

    for page_index in range(1, total_pages + 1):
        if page_index == 1:
            page_url = first_url
            page_public_params = first_public_params
            page_status = status
            page_parsed = parsed
        else:
            page_url, page_public_params = request_url(spec, key, page_index)
            page_status, page_body, _headers = http_get(page_url, timeout=90)
            page_parsed = parse_result(page_body)
            if not page_parsed["ok"]:
                raise RuntimeError(
                    f"provider page {page_index} {page_parsed['code']}: {page_parsed['message']}"
                )
            if int(page_parsed["total_count"]) != total_count:
                raise RuntimeError(
                    f"total_count changed during collection: first={total_count}, page={page_parsed['total_count']}"
                )

        page_rows = page_parsed["rows"]
        expected_rows = min(P_SIZE, total_count - ((page_index - 1) * P_SIZE))
        if len(page_rows) != expected_rows:
            raise RuntimeError(
                f"page {page_index} row count mismatch: expected={expected_rows}, actual={len(page_rows)}"
            )
        validate_rows(spec, page_rows)
        all_rows.extend(page_rows)
        pages.append(
            {
                "page_index": page_index,
                "http_status": page_status,
                "provider_code": page_parsed["code"],
                "provider_message": page_parsed["message"],
                "request_url_redacted": redact_url(page_url, extra_values=[key]),
                "request_params": {
                    "service": SERVICE_NAME,
                    **page_public_params,
                    "KEY": "<redacted>",
                },
                "body": filtered_page_body(page_parsed, page_rows),
            }
        )

    if len(all_rows) != total_count:
        raise RuntimeError(f"full row count mismatch: expected={total_count}, actual={len(all_rows)}")
    all_item_ids = {str(row.get("ITM_ID")) for row in all_rows}
    if spec.allowed_item_ids and all_item_ids != set(spec.allowed_item_ids):
        raise RuntimeError(
            f"{spec.statbl_id} item set changed: expected={sorted(spec.allowed_item_ids)}, actual={sorted(all_item_ids)}"
        )
    if spec.statbl_id == "A_2024_00445":
        class_names = {
            str(row.get("CLS_ID")): str(row.get("CLS_NM") or "").strip()
            for row in all_rows
        }
        if class_names != KEY_MONEY_CLASS_NAMES:
            raise RuntimeError(
                f"{spec.statbl_id} class contract changed: expected={KEY_MONEY_CLASS_NAMES}, actual={class_names}"
            )
        item_contract = {
            str(row.get("ITM_ID")): (
                str(row.get("ITM_NM") or "").strip(),
                str(row.get("UI_NM") or "").strip(),
            )
            for row in all_rows
        }
        if item_contract != KEY_MONEY_ITEM_CONTRACT:
            raise RuntimeError(
                f"{spec.statbl_id} item/unit contract changed: "
                f"expected={KEY_MONEY_ITEM_CONTRACT}, actual={item_contract}"
            )
        period_ids = {
            str(row.get("WRTTIME_IDTFR_ID") or "") for row in all_rows
        }
        expected_grid = {
            (period, class_id, item_id)
            for period in period_ids
            for class_id in KEY_MONEY_CLASS_NAMES
            for item_id in KEY_MONEY_ITEM_CONTRACT
        }
        actual_grid = {
            (
                str(row.get("WRTTIME_IDTFR_ID") or ""),
                str(row.get("CLS_ID") or ""),
                str(row.get("ITM_ID") or ""),
            )
            for row in all_rows
        }
        if not period_ids or actual_grid != expected_grid:
            raise RuntimeError(
                f"{spec.statbl_id} period/class/item grid incomplete: "
                f"expected={len(expected_grid)}, actual={len(actual_grid)}"
            )
    missing_values = sum(not str(row.get("DTA_VAL") or "").strip() for row in all_rows)
    if missing_values:
        raise RuntimeError(f"{spec.statbl_id} contains {missing_values} empty values")
    keys = [natural_key(row) for row in all_rows]
    if len(keys) != len(set(keys)):
        raise RuntimeError(f"{spec.statbl_id} contains duplicate natural keys")

    periods = sorted({str(row.get("WRTTIME_IDTFR_ID") or "") for row in all_rows if row.get("WRTTIME_IDTFR_ID")})
    return {
        "spec": spec,
        "total_count": total_count,
        "pages": pages,
        "periods": periods,
        "latest_period": periods[-1] if periods else "",
        "item_ids": sorted({str(row.get("ITM_ID")) for row in all_rows}),
        "group_ids": sorted({str(row.get("GRP_ID")) for row in all_rows if row.get("GRP_ID") is not None}),
    }


def source_fingerprint(collected: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for table in collected:
        spec: TableSpec = table["spec"]
        for page in table["pages"]:
            lines.append(f"{spec.statbl_id}:{page['page_index']}:{sha256_bytes(page['body'])}")
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def record_unchanged_verification(
    rid: str,
    collected: list[dict[str, Any]],
    fingerprint: str,
    previous: dict[str, Any],
) -> None:
    raw_directory = Path(str(previous["raw_directory"]))
    representative = next(iter(sorted(raw_directory.glob("*_page_*.json"))), None)
    if representative is None:
        raise RuntimeError("last-good 대표 raw 파일을 찾지 못했습니다.")
    periods = sorted(
        period
        for table in collected
        for period in table["periods"]
        if period
    )
    collection_date = run_date()
    append_csv(
        RAW_ROOT / "ingest_manifest.csv",
        {
            "run_id": rid,
            "source_id": SOURCE_ID,
            "snapshot_date": f"{collection_date[:4]}-{collection_date[4:6]}-{collection_date[6:]}",
            "provider": PROVIDER,
            "dataset_name": "R-ONE 서울 14개 통계표 변경 없음 검증",
            "raw_path": str(representative.relative_to(RAW_ROOT.parent.parent)),
            "bytes": representative.stat().st_size,
            "sha256": sha256_bytes(representative.read_bytes()),
            "collection_status": "success",
            "request_url_redacted": DATA_URL,
            "request_params_json": json.dumps(
                {"service": SERVICE_NAME, "verification": "unchanged"},
                ensure_ascii=False,
                sort_keys=True,
            ),
            "http_status": 200,
            "provider_result_code": "INFO-000",
            "provider_result_message": (
                f"unchanged; tables={len(collected)}; rows="
                f"{sum(int(table['total_count']) for table in collected)}"
            ),
            "spatial_unit": "서울특별시",
            "time_unit": "QY/YY",
            "source_period": f"{periods[0] if periods else ''}~{periods[-1] if periods else ''}",
            "area_code_type": "R-ONE 서울 CLS/GRP",
            "quality_notes_ko": "실 API 14개 표를 재검증했고 last-good과 내용 해시가 같아 원본을 중복 저장하지 않았다.",
            "data_period_start": periods[0] if periods else "",
            "data_period_end": periods[-1] if periods else "",
            "content_fingerprint": fingerprint,
            "change_status": "unchanged_verified",
            "full_collection_status": "verified_unchanged",
            "full_collection_completed_at": now_utc(),
            "collected_at": now_utc(),
        },
        MANIFEST_FIELDS,
    )


def unchanged_from_latest_complete(collected: list[dict[str, Any]]) -> tuple[bool, dict[str, Any] | None]:
    latest = latest_complete_full_collection(SOURCE_ID, SERVICE_NAME)
    if not latest or not latest.get("raw_directory"):
        return False, latest
    directory = Path(str(latest["raw_directory"]))
    expected_names: set[str] = set()
    for table in collected:
        spec: TableSpec = table["spec"]
        for page in table["pages"]:
            filename = f"{spec.statbl_id}_page_{page['page_index']:03d}.json"
            expected_names.add(filename)
            candidate = directory / filename
            if not candidate.exists() or sha256_bytes(candidate.read_bytes()) != sha256_bytes(page["body"]):
                return False, latest
    existing_names = {path.name for path in directory.glob("*_page_*.json")}
    return existing_names == expected_names, latest


def write_selection_csv(rid: str, collected: list[dict[str, Any]]) -> Path:
    path = RAW_ROOT / RUN_DATE / "reb_rone" / "commercial_rent_data" / rid / "selected_tables.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "selection_group",
        "selection_reason_ko",
        "STATBL_ID",
        "STATBL_NM",
        "DTACYCLE_CD",
        "REGION_DIMENSION",
        "REGION_ID",
        "ITM_ID",
        "spatial_scope",
        "row_count",
        "source_period_start",
        "source_period_end",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for table in collected:
            spec: TableSpec = table["spec"]
            periods = table["periods"]
            writer.writerow(
                {
                    "selection_group": spec.group,
                    "selection_reason_ko": spec.purpose,
                    "STATBL_ID": spec.statbl_id,
                    "STATBL_NM": spec.name,
                    "DTACYCLE_CD": spec.cycle,
                    "REGION_DIMENSION": spec.region_dimension,
                    "REGION_ID": spec.cls_id,
                    "ITM_ID": spec.itm_id or "all",
                    "spatial_scope": SEOUL_NAME,
                    "row_count": table["total_count"],
                    "source_period_start": periods[0] if periods else "",
                    "source_period_end": periods[-1] if periods else "",
                }
            )
    return path


def commit_collection(rid: str, collected: list[dict[str, Any]], fingerprint: str) -> tuple[Path, str]:
    selection_path = write_selection_csv(rid, collected)
    for table in collected:
        spec: TableSpec = table["spec"]
        periods = table["periods"]
        for page in table["pages"]:
            page_index = int(page["page_index"])
            write_raw(
                run_id_value=rid,
                source_id=SOURCE_ID,
                provider=PROVIDER,
                dataset_name=f"R-ONE 서울 {spec.name} page {page_index}",
                body=page["body"],
                relative_path=(
                    f"{RUN_DATE}/reb_rone/commercial_rent_data/{rid}/"
                    f"{spec.statbl_id}_page_{page_index:03d}.json"
                ),
                request_url_redacted=page["request_url_redacted"],
                request_params=page["request_params"],
                http_status=page["http_status"],
                provider_result_code=page["provider_code"],
                provider_result_message=(
                    f"Seoul rows={len(json.loads(page['body'].decode('utf-8'))['SttsApiTblData'][1]['row'])} "
                    f"total={table['total_count']}"
                ),
                spatial_unit="서울특별시",
                time_unit=spec.cycle,
                source_period=f"{periods[0] if periods else ''}~{periods[-1] if periods else ''}",
                area_code_type=f"R-ONE {spec.region_dimension}_ID/ITM_ID",
                quality_notes_ko=(
                    f"서버에서 서울 {spec.region_dimension}_ID={spec.cls_id}로 제한하고 "
                    f"응답의 {spec.region_dimension}_NM=서울을 재검증했다. "
                    f"용도: {spec.purpose}"
                ),
                data_period_start=periods[0] if periods else None,
                data_period_end=periods[-1] if periods else None,
                content_fingerprint=fingerprint,
                change_status="collected",
            )
    completed_at = mark_manifest_run_complete(
        run_id_value=rid,
        source_id=SOURCE_ID,
        service_name=SERVICE_NAME,
    )
    return selection_path, completed_at


def sanitized_table_result(table: dict[str, Any]) -> dict[str, Any]:
    spec: TableSpec = table["spec"]
    return {
        "statbl_id": spec.statbl_id,
        "name": spec.name,
        "group": spec.group,
        "cycle": spec.cycle,
        "region_dimension": spec.region_dimension,
        "region_id": spec.cls_id,
        "itm_id": spec.itm_id or "all",
        "rows": table["total_count"],
        "pages": len(table["pages"]),
        "period_start": table["periods"][0] if table["periods"] else "",
        "period_end": table["periods"][-1] if table["periods"] else "",
        "item_ids": table["item_ids"],
        "group_count": len(table["group_ids"]),
        "seoul_only": True,
    }


def main() -> None:
    key = parse_key_file().get("reb_key", "").strip()
    if not key:
        raise RuntimeError("docs/90_private/key.md에 R-ONE 인증키가 없습니다.")

    rid = run_id("reb_commercial_rent")
    collected: list[dict[str, Any]] = []
    try:
        for spec in TABLE_SPECS:
            collected.append(collect_table(key, spec))
        fingerprint = source_fingerprint(collected)
        unchanged, previous = unchanged_from_latest_complete(collected)
        if unchanged:
            if not previous:
                raise RuntimeError("변경 없음 판정에 필요한 last-good 계보가 없습니다.")
            record_unchanged_verification(rid, collected, fingerprint, previous)
            selection_path: Path | None = None
            completed_at = str(previous.get("completed_at") or "") if previous else ""
            collection_status = "unchanged_last_good_preserved"
        else:
            selection_path, completed_at = commit_collection(rid, collected, fingerprint)
            collection_status = "complete"

        summary = {
            "run_id": rid,
            "source_id": SOURCE_ID,
            "service": SERVICE_NAME,
            "collection_status": collection_status,
            "spatial_scope": SEOUL_NAME,
            "selected_tables": len(collected),
            "expected_tables": len(TABLE_SPECS),
            "total_saved_rows": 0 if unchanged else sum(int(table["total_count"]) for table in collected),
            "validated_rows": sum(int(table["total_count"]) for table in collected),
            "source_fingerprint": fingerprint,
            "selection_path": str(selection_path) if selection_path else None,
            "last_good_completed_at": completed_at,
            "table_results": [sanitized_table_result(table) for table in collected],
            "failures": [],
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
    except Exception as exc:
        safe_message = safe_exception_message(exc, key)
        current_spec = TABLE_SPECS[min(len(collected), len(TABLE_SPECS) - 1)]
        last_good = latest_complete_full_collection(SOURCE_ID, SERVICE_NAME)
        log_failure(
            run_id_value=rid,
            source_id=SOURCE_ID,
            provider=PROVIDER,
            dataset_name=f"R-ONE 서울 {current_spec.name}",
            failure_type=type(exc).__name__,
            failure_reason_ko=f"서울 전용 R-ONE 수집 검증 실패: {safe_message}",
            next_action_ko="표 코드, 서울 분류 코드, 항목 코드와 제공 시점을 확인한 뒤 다시 실행한다.",
            request_url_redacted=DATA_URL,
        )
        summary = {
            "run_id": rid,
            "source_id": SOURCE_ID,
            "service": SERVICE_NAME,
            "collection_status": (
                "failed_last_good_preserved" if last_good else "failed_no_last_good"
            ),
            "last_good": last_good,
            "spatial_scope": SEOUL_NAME,
            "selected_tables": len(collected),
            "expected_tables": len(TABLE_SPECS),
            "validated_rows": sum(int(table["total_count"]) for table in collected),
            "failures": [{"type": type(exc).__name__, "message": safe_message}],
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        log_path = RAW_ROOT / "run_logs" / f"{rid}.json"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        raise SystemExit(1) from None

    log_path = RAW_ROOT / "run_logs" / f"{rid}.json"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    console_summary = {
        "run_id": summary["run_id"],
        "collection_status": summary["collection_status"],
        "selected_tables": summary["selected_tables"],
        "validated_rows": summary["validated_rows"],
        "total_saved_rows": summary["total_saved_rows"],
        "last_good_completed_at": summary["last_good_completed_at"],
        "source_fingerprint": summary["source_fingerprint"],
        "details_log": str(log_path),
    }
    print(json.dumps(console_summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
