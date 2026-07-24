from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sqlite3
import tempfile
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

from ingest_common import ROOT, atomic_write_text, latest_complete_service_directory, parse_key_file, run_date


RAW_ROOT = ROOT / "datacorpus" / "_raw_ingest"
CONFIG_PATH = ROOT / "config" / "seoul_localdata_business_services.json"
TAXONOMY_PATH = ROOT / "datacorpus" / "_silver" / "silver_industry_master_seoul_open_data.csv"
FOOD_BRIDGE_PATH = (
    ROOT / "datacorpus" / "_silver" / "silver_localdata_food_license_uptae_service_bridge.csv"
)
OUTPUT_PATH = ROOT / "datacorpus" / "_silver" / "silver_localdata_business_license.csv"
VALIDATION_PATH = (
    ROOT / "datacorpus" / "_rule_validation" / "105_localdata_business_license_validation.json"
)

OUTPUT_FIELDS = [
    "service_code",
    "service_name_ko",
    "industry_group",
    "source_industry_name",
    "industry_code",
    "industry_name",
    "mapping_tier",
    "licensing_agency_code",
    "management_no",
    "open_date",
    "close_date",
    "trade_status_code",
    "trade_status_name",
    "detail_status_code",
    "detail_status_name",
    "active_flag",
    "closed_flag",
    "lifecycle_days",
    "address_sgg_name",
    "address_present",
    "address_seoul_valid",
    "x_epsg5174",
    "y_epsg5174",
    "coordinate_valid",
    "coordinate_in_seoul_bbox",
    "source_updated_at",
    "source_snapshot_date",
    "source_row_hash",
]

PII_SOURCE_FIELDS_EXCLUDED = [
    "BPLCNM",
    "SITETEL",
    "SITEWHLADDR",
    "RDNWHLADDR",
    "RDNPOSTNO",
    "SITEPOSTNO",
]

SEOUL_TM_X_MIN = 160000.0
SEOUL_TM_X_MAX = 230000.0
SEOUL_TM_Y_MIN = 410000.0
SEOUL_TM_Y_MAX = 480000.0

SERVICE_FILE_PATTERN = re.compile(r"^(LOCALDATA_\d+)_(\d+)_(\d+)\.json$")
SEOUL_SGG_PATTERN = re.compile(r"(?:서울특별시|서울시|서울)\s*([가-힣]+구)(?:\s|$)")

BEAUTY_TARGET_BY_SOURCE_NAME = {
    "일반미용업": "미용실",
    "피부미용업": "피부관리실",
    "네일아트업": "네일숍",
}

SERVICE_TARGET_NAME = {
    "LOCALDATA_062001": "세탁소",
    "LOCALDATA_030901": "노래방",
    "LOCALDATA_031201": "여행사",
    "LOCALDATA_031202": "여행사",
    "LOCALDATA_031203": "여행사",
    "LOCALDATA_103101": "골프연습장",
    "LOCALDATA_103201": "당구장",
    "LOCALDATA_104101": "스포츠 강습",
    "LOCALDATA_104201": "스포츠클럽",
}


def load_services() -> list[dict[str, Any]]:
    if not CONFIG_PATH.exists():
        raise RuntimeError("라이브 서비스 설정이 없습니다. 먼저 수집기 --probe-only --write-config를 실행하세요.")
    payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    services = payload.get("services") or []
    if not services:
        raise RuntimeError("라이브 서비스 설정에 서비스가 없습니다.")
    return [dict(item) for item in services if isinstance(item, dict)]


def load_taxonomy() -> tuple[dict[str, str], dict[str, str]]:
    if not TAXONOMY_PATH.exists():
        raise RuntimeError(f"제품 업종 taxonomy가 없습니다: {TAXONOMY_PATH}")
    by_name: dict[str, str] = {}
    by_code: dict[str, str] = {}
    with TAXONOMY_PATH.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            code = str(row.get("서비스_업종_코드") or "").strip()
            name = str(row.get("서비스_업종_코드_명") or "").strip()
            if not code or not name:
                continue
            if name in by_name and by_name[name] != code:
                raise RuntimeError(f"taxonomy 업종명이 여러 코드에 연결됨: {name}")
            by_name[name] = code
            by_code[code] = name
    if not by_name:
        raise RuntimeError("제품 업종 taxonomy가 비어 있습니다.")
    return by_name, by_code


def load_food_bridge(
    taxonomy_by_name: dict[str, str], taxonomy_by_code: dict[str, str]
) -> dict[tuple[str, str], tuple[str, str]]:
    if not FOOD_BRIDGE_PATH.exists():
        return {}
    mapping: dict[tuple[str, str], tuple[str, str]] = {}
    with FOOD_BRIDGE_PATH.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if str(row.get("mapping_status") or "") != "auto_strong":
                continue
            if str(row.get("mapping_review_required") or "").lower() != "false":
                continue
            category = str(row.get("license_category") or "").strip()
            source_name = str(row.get("업태명") or "").strip()
            code = str(row.get("candidate_서비스_업종_코드") or "").strip()
            name = str(row.get("candidate_서비스_업종_코드_명") or "").strip()
            if not category or not source_name or not code or not name:
                continue
            if taxonomy_by_code.get(code) != name or taxonomy_by_name.get(name) != code:
                continue
            mapping[(category, source_name)] = (code, name)
    return mapping


def map_product_industry(
    service: str,
    source_name: str,
    taxonomy_by_name: dict[str, str],
    food_bridge: dict[tuple[str, str], tuple[str, str]],
) -> tuple[str, str, str]:
    food_category = {
        "LOCALDATA_072404": "일반음식점",
        "LOCALDATA_072405": "휴게음식점",
    }.get(service)
    if food_category:
        matched = food_bridge.get((food_category, source_name))
        if matched:
            return matched[0], matched[1], "food_bridge_auto_strong"
        return "", "", "unmapped"

    if service == "LOCALDATA_051801":
        target_name = BEAUTY_TARGET_BY_SOURCE_NAME.get(source_name, "")
        tier = "source_subtype_curated_exact_target"
    else:
        target_name = SERVICE_TARGET_NAME.get(service, "")
        tier = "service_curated_exact_target"
    if not target_name:
        return "", "", "unmapped"
    code = taxonomy_by_name.get(target_name, "")
    if not code:
        # The target list is allowed only when the exact canonical name exists
        # in the live product taxonomy.  Never fuzzy-match a substitute.
        return "", "", "unmapped"
    return code, target_name, tier


def parse_service_payload(path: Path, service: str) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    payload = data.get(service) if isinstance(data, dict) else None
    rows = payload.get("row") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def complete_fallback_directory(service: str, maximum_run_date: str | None) -> Path | None:
    directories = sorted(
        RAW_ROOT.glob(f"[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]/seoul_open_data/full/{service}"),
        reverse=True,
    )
    for directory in directories:
        snapshot = directory.parents[2].name
        if maximum_run_date and snapshot > maximum_run_date:
            continue
        page_ranges: list[tuple[int, int, Path]] = []
        total: int | None = None
        valid = True
        for path in directory.glob(f"{service}_*.json"):
            match = SERVICE_FILE_PATTERN.match(path.name)
            if not match or match.group(1) != service:
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8-sig"))
                payload = data.get(service) if isinstance(data, dict) else None
                if not isinstance(payload, dict):
                    valid = False
                    break
                page_total = int(payload.get("list_total_count") or 0)
                if total is None:
                    total = page_total
                elif total != page_total:
                    valid = False
                    break
                page_ranges.append((int(match.group(2)), int(match.group(3)), path))
            except (OSError, ValueError, TypeError):
                valid = False
                break
        if not valid or not total or not page_ranges:
            continue
        page_ranges.sort()
        expected_start = 1
        for start, end, _path in page_ranges:
            if start != expected_start:
                valid = False
                break
            expected_start = end + 1
            if end >= total:
                break
        if valid and expected_start > total:
            return directory
    return None


def resolve_raw_directory(service: str, maximum_run_date: str | None) -> Path | None:
    try:
        trusted = latest_complete_service_directory(service, maximum_run_date=maximum_run_date)
    except RuntimeError:
        trusted = None
    return trusted or complete_fallback_directory(service, maximum_run_date)


def page_paths(directory: Path, service: str) -> list[Path]:
    def key(path: Path) -> tuple[int, int, str]:
        match = SERVICE_FILE_PATTERN.match(path.name)
        if not match:
            return (10**12, 10**12, path.name)
        return (int(match.group(2)), int(match.group(3)), path.name)

    return sorted(directory.glob(f"{service}_*.json"), key=key)


def first_text(row: dict[str, Any], names: Iterable[str]) -> str:
    for name in names:
        value = str(row.get(name) or "").strip()
        if value:
            return value
    return ""


def normalize_date(value: Any) -> tuple[str, bool]:
    text = str(value or "").strip()
    if not text:
        return "", True
    digits = re.sub(r"\D", "", text)
    if len(digits) < 8:
        return "", False
    digits = digits[:8]
    try:
        parsed = datetime.strptime(digits, "%Y%m%d").date()
    except ValueError:
        return "", False
    if not 1900 <= parsed.year <= 2100:
        return "", False
    return parsed.isoformat(), True


def normalize_timestamp(value: Any) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return (digits[:20]).ljust(20, "0")


def to_float(value: Any) -> float | None:
    text = str(value or "").strip().replace(",", "")
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return number


def extract_sgg(address: str) -> str:
    match = SEOUL_SGG_PATTERN.search(address)
    return match.group(1) if match else ""


def date_days(start: str, end: str) -> str:
    if not start or not end:
        return ""
    return str((date.fromisoformat(end) - date.fromisoformat(start)).days)


def normalize_row(
    row: dict[str, Any],
    info: dict[str, Any],
    snapshot_date: str,
    sequence: int,
    taxonomy_by_name: dict[str, str],
    food_bridge: dict[tuple[str, str], tuple[str, str]],
) -> tuple[dict[str, str] | None, dict[str, int], str]:
    metrics: dict[str, int] = Counter()
    service = str(info["service"])
    management_no = str(row.get("MGTNO") or "").strip()
    licensing_agency_code = str(row.get("OPNSFTEAMCODE") or "").strip()
    if not management_no:
        metrics["missing_management_no"] += 1
        return None, metrics, ""
    if not licensing_agency_code:
        metrics["missing_licensing_agency_code"] += 1

    open_date, open_valid = normalize_date(row.get("APVPERMYMD"))
    close_raw = first_text(row, ("DCBYMD", "CLGENDDT"))
    close_date, close_valid = normalize_date(close_raw)
    if not open_valid:
        metrics["invalid_open_date"] += 1
    if not close_valid:
        metrics["invalid_close_date"] += 1
    if open_date and open_date > date.today().isoformat():
        metrics["future_open_date"] += 1
    if close_date and close_date > date.today().isoformat():
        metrics["future_close_date"] += 1
    lifecycle = date_days(open_date, close_date)
    if lifecycle and int(lifecycle) < 0:
        metrics["close_before_open"] += 1

    trade_code = str(row.get("TRDSTATEGBN") or "").strip()
    trade_name = str(row.get("TRDSTATENM") or "").strip()
    detail_code = str(row.get("DTLSTATEGBN") or "").strip()
    detail_name = str(row.get("DTLSTATENM") or "").strip()
    status_text = f"{trade_name} {detail_name}"
    closed = bool(close_date or trade_code == "03" or "폐업" in status_text)
    active = bool(not closed and (trade_code == "01" or "영업" in status_text or "정상" in status_text))
    if close_date and active:
        metrics["active_with_close_date"] += 1

    address = first_text(row, ("RDNWHLADDR", "SITEWHLADDR"))
    sgg = extract_sgg(address)
    address_present = bool(address)
    address_seoul_valid = bool(sgg or "서울" in address)
    if not address_present:
        metrics["missing_address"] += 1
    elif not address_seoul_valid:
        metrics["non_seoul_address"] += 1

    x = to_float(row.get("X"))
    y = to_float(row.get("Y"))
    coordinate_valid = x is not None and y is not None
    coordinate_in_bbox = bool(
        coordinate_valid
        and SEOUL_TM_X_MIN <= x <= SEOUL_TM_X_MAX
        and SEOUL_TM_Y_MIN <= y <= SEOUL_TM_Y_MAX
    )
    if not coordinate_valid:
        metrics["missing_or_invalid_coordinate"] += 1
    elif not coordinate_in_bbox:
        metrics["outside_seoul_tm_bbox"] += 1

    source_industry = first_text(row, ("UPTAENM", "SNTUPTAENM", "CULPHYEDCOBNM"))
    industry_code, industry_name, mapping_tier = map_product_industry(
        service, source_industry, taxonomy_by_name, food_bridge
    )
    if industry_code:
        metrics["mapped_industry"] += 1
        metrics[f"mapping_tier::{mapping_tier}"] += 1
    else:
        metrics["unmapped_industry"] += 1
        metrics["mapping_tier::unmapped"] += 1
    updated_at = first_text(row, ("LASTMODTS", "UPDATEDT"))
    sort_key = f"{normalize_timestamp(updated_at)}{sequence:012d}"
    normalized = {
        "service_code": service,
        "service_name_ko": str(info.get("service_name_ko") or service),
        "industry_group": str(info.get("industry_group") or "기타"),
        "source_industry_name": source_industry,
        "industry_code": industry_code,
        "industry_name": industry_name,
        "mapping_tier": mapping_tier,
        "licensing_agency_code": licensing_agency_code,
        "management_no": management_no,
        "open_date": open_date,
        "close_date": close_date,
        "trade_status_code": trade_code,
        "trade_status_name": trade_name,
        "detail_status_code": detail_code,
        "detail_status_name": detail_name,
        "active_flag": "1" if active else "0",
        "closed_flag": "1" if closed else "0",
        "lifecycle_days": lifecycle,
        "address_sgg_name": sgg,
        "address_present": "1" if address_present else "0",
        "address_seoul_valid": "1" if address_seoul_valid else "0",
        "x_epsg5174": "" if x is None else format(x, ".8g"),
        "y_epsg5174": "" if y is None else format(y, ".8g"),
        "coordinate_valid": "1" if coordinate_valid else "0",
        "coordinate_in_seoul_bbox": "1" if coordinate_in_bbox else "0",
        "source_updated_at": updated_at,
        "source_snapshot_date": snapshot_date,
    }
    digest_source = "\x1f".join(normalized.get(field, "") for field in OUTPUT_FIELDS if field != "source_row_hash")
    normalized["source_row_hash"] = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()
    return normalized, metrics, sort_key


def create_database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    columns = ",\n".join(f'"{field}" TEXT NOT NULL' for field in OUTPUT_FIELDS)
    connection.execute(
        f"""
        CREATE TABLE records (
            {columns},
            sort_key TEXT NOT NULL,
            PRIMARY KEY (service_code, licensing_agency_code, management_no)
        ) WITHOUT ROWID
        """
    )
    return connection


def upsert_row(connection: sqlite3.Connection, row: dict[str, str], sort_key: str) -> None:
    columns = OUTPUT_FIELDS + ["sort_key"]
    placeholders = ",".join("?" for _ in columns)
    updates = ",".join(
        f'"{field}"=excluded."{field}"'
        for field in OUTPUT_FIELDS
        if field not in {"service_code", "licensing_agency_code", "management_no"}
    )
    connection.execute(
        f"""
        INSERT INTO records ({','.join(f'"{field}"' for field in columns)})
        VALUES ({placeholders})
        ON CONFLICT(service_code, licensing_agency_code, management_no) DO UPDATE SET
            {updates}, sort_key=excluded.sort_key
        WHERE excluded.sort_key >= records.sort_key
        """,
        [row[field] for field in OUTPUT_FIELDS] + [sort_key],
    )


def export_csv(connection: sqlite3.Connection, path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    os.close(file_descriptor)
    count = 0
    try:
        with open(temporary_name, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
            writer.writeheader()
            quoted_fields = ",".join('"' + field + '"' for field in OUTPUT_FIELDS)
            query = f"SELECT {quoted_fields} FROM records ORDER BY service_code, management_no"
            for values in connection.execute(query):
                writer.writerow(dict(zip(OUTPUT_FIELDS, values)))
                count += 1
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise
    return count


def output_contains_secret(path: Path) -> bool:
    secret = parse_key_file().get("seoul_key", "").strip()
    if not secret:
        return False
    secret_bytes = secret.encode("utf-8")
    overlap = max(0, len(secret_bytes) - 1)
    tail = b""
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            block = tail + chunk
            if secret_bytes in block:
                return True
            tail = block[-overlap:] if overlap else b""
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="서울 LocalData 소상공인 인허가 공통 Silver 생성")
    parser.add_argument("--maximum-run-date", help="YYYYMMDD 이하의 최신 완전 스냅샷 사용")
    args = parser.parse_args()
    maximum_run_date = args.maximum_run_date or run_date()
    if not re.fullmatch(r"\d{8}", maximum_run_date):
        parser.error("--maximum-run-date는 YYYYMMDD 형식이어야 합니다.")

    services = load_services()
    taxonomy_by_name, taxonomy_by_code = load_taxonomy()
    food_bridge = load_food_bridge(taxonomy_by_name, taxonomy_by_code)
    counters: Counter[str] = Counter()
    service_summaries: list[dict[str, Any]] = []
    missing_services: list[str] = []
    min_open_date = ""
    max_open_date = ""
    min_close_date = ""
    max_close_date = ""

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    database_handle = tempfile.NamedTemporaryFile(
        prefix="localdata-business-", suffix=".sqlite", dir=OUTPUT_PATH.parent, delete=False
    )
    database_path = Path(database_handle.name)
    database_handle.close()
    connection = create_database(database_path)
    duplicate_keys_after = 0
    cross_agency_management_no_collision_groups = 0
    try:
        sequence = 0
        for info in services:
            service = str(info["service"])
            directory = resolve_raw_directory(service, maximum_run_date)
            if directory is None:
                missing_services.append(service)
                service_summaries.append({"service": service, "status": "missing_complete_raw_snapshot"})
                continue
            snapshot_date = directory.parents[2].name
            before = int(connection.execute("SELECT COUNT(*) FROM records WHERE service_code=?", (service,)).fetchone()[0])
            service_raw_rows = 0
            service_key_rows = 0
            service_mapped_rows = 0
            for page in page_paths(directory, service):
                for source_row in parse_service_payload(page, service):
                    sequence += 1
                    service_raw_rows += 1
                    counters["raw_rows"] += 1
                    normalized, row_metrics, sort_key = normalize_row(
                        source_row,
                        info,
                        snapshot_date,
                        sequence,
                        taxonomy_by_name,
                        food_bridge,
                    )
                    counters.update(row_metrics)
                    if normalized is None:
                        continue
                    service_key_rows += 1
                    if normalized["industry_code"]:
                        service_mapped_rows += 1
                    counters["rows_with_management_no"] += 1
                    open_value = normalized["open_date"]
                    close_value = normalized["close_date"]
                    if open_value:
                        min_open_date = open_value if not min_open_date else min(min_open_date, open_value)
                        max_open_date = max(max_open_date, open_value)
                    if close_value:
                        min_close_date = close_value if not min_close_date else min(min_close_date, close_value)
                        max_close_date = max(max_close_date, close_value)
                    upsert_row(connection, normalized, sort_key)
                connection.commit()
            after = int(connection.execute("SELECT COUNT(*) FROM records WHERE service_code=?", (service,)).fetchone()[0])
            duplicates = max(0, service_key_rows - (after - before))
            counters["duplicates_removed"] += duplicates
            service_summaries.append(
                {
                    "service": service,
                    "service_name_ko": info.get("service_name_ko"),
                    "status": "processed",
                    "snapshot_date": snapshot_date,
                    "raw_rows": service_raw_rows,
                    "silver_rows": after - before,
                    "duplicates_removed": duplicates,
                    "mapped_source_rows": service_mapped_rows,
                    "unmapped_source_rows": service_key_rows - service_mapped_rows,
                    "raw_directory": str(directory.relative_to(ROOT)),
                }
            )
        duplicate_keys_after = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM (
                    SELECT service_code, licensing_agency_code, management_no
                    FROM records
                    GROUP BY service_code, licensing_agency_code, management_no
                    HAVING COUNT(*) > 1
                )
                """
            ).fetchone()[0]
        )
        cross_agency_management_no_collision_groups = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM (
                    SELECT service_code, management_no
                    FROM records
                    GROUP BY service_code, management_no
                    HAVING COUNT(DISTINCT licensing_agency_code) > 1
                )
                """
            ).fetchone()[0]
        )
        output_rows = export_csv(connection, OUTPUT_PATH)
    finally:
        connection.close()
        try:
            database_path.unlink()
        except OSError:
            pass

    header_has_pii = any(field in OUTPUT_FIELDS for field in PII_SOURCE_FIELDS_EXCLUDED)
    secret_leak = output_contains_secret(OUTPUT_PATH)
    failure_reasons = []
    if missing_services:
        failure_reasons.append("일부 라이브 서비스의 완전 원천 스냅샷이 없음")
    if output_rows == 0:
        failure_reasons.append("Silver 출력 행이 없음")
    if header_has_pii or secret_leak:
        failure_reasons.append("Silver 비밀값/PII 제외 규칙 위반")
    mapped_code_name_mismatch = 0
    silver_mapping_tiers: Counter[str] = Counter()
    silver_mapped_rows = 0
    silver_unmapped_rows = 0
    with OUTPUT_PATH.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            code = str(row.get("industry_code") or "")
            name = str(row.get("industry_name") or "")
            tier = str(row.get("mapping_tier") or "unmapped")
            silver_mapping_tiers[tier] += 1
            if code:
                silver_mapped_rows += 1
            else:
                silver_unmapped_rows += 1
            if code and taxonomy_by_code.get(code) != name:
                mapped_code_name_mismatch += 1
    if mapped_code_name_mismatch:
        failure_reasons.append("Silver 업종 코드/명이 제품 taxonomy와 불일치")
    hard_fail = bool(failure_reasons)
    warning_count = sum(
        counters[name]
        for name in (
            "missing_management_no",
            "invalid_open_date",
            "invalid_close_date",
            "future_open_date",
            "future_close_date",
            "close_before_open",
            "active_with_close_date",
            "non_seoul_address",
            "missing_or_invalid_coordinate",
            "outside_seoul_tm_bbox",
        )
    )
    overall_status = "FAIL" if hard_fail else ("PASS_WITH_SOURCE_WARNINGS" if warning_count else "PASS")
    validation = {
        "validation_date": datetime.now().isoformat(timespec="seconds"),
        "scope": "서울특별시 전체 LocalData 소상공인 인허가 공통 Silver",
        "grain": "service_code + licensing_agency_code + management_no 최신 1행",
        "intended_use": "업종별 개업·폐업·생존 성과 백테스트 정답 후보 및 관리자 파이프라인 상태",
        "overall_status": overall_status,
        "failure_reasons": failure_reasons,
        "source_service_count": len(services),
        "processed_service_count": len(services) - len(missing_services),
        "missing_services": missing_services,
        "raw_rows": counters["raw_rows"],
        "rows_with_management_no": counters["rows_with_management_no"],
        "silver_rows": output_rows,
        "duplicates_removed": counters["duplicates_removed"],
        "duplicate_keys_after": duplicate_keys_after,
        "cross_agency_management_no_collision_groups": cross_agency_management_no_collision_groups,
        "industry_mapping": {
            "taxonomy_path": str(TAXONOMY_PATH.relative_to(ROOT)),
            "food_bridge_path": str(FOOD_BRIDGE_PATH.relative_to(ROOT)),
            "mapped_rows": silver_mapped_rows,
            "unmapped_rows": silver_unmapped_rows,
            "mapped_rate": round(silver_mapped_rows / output_rows, 6) if output_rows else 0.0,
            "raw_mapped_rows_before_dedupe": counters["mapped_industry"],
            "raw_unmapped_rows_before_dedupe": counters["unmapped_industry"],
            "mapped_code_name_mismatch": mapped_code_name_mismatch,
            "tier_counts": dict(sorted(silver_mapping_tiers.items())),
            "validated_exact_target_codes": {
                name: taxonomy_by_name.get(name, "")
                for name in sorted(set(BEAUTY_TARGET_BY_SOURCE_NAME.values()) | set(SERVICE_TARGET_NAME.values()))
            },
            "rule": "제품 taxonomy의 exact target name/code만 허용하고 모호한 업태는 blank + unmapped",
        },
        "quality_metrics": {
            key: counters[key]
            for key in (
                "missing_management_no",
                "missing_licensing_agency_code",
                "invalid_open_date",
                "invalid_close_date",
                "future_open_date",
                "future_close_date",
                "close_before_open",
                "active_with_close_date",
                "missing_address",
                "non_seoul_address",
                "missing_or_invalid_coordinate",
                "outside_seoul_tm_bbox",
            )
        },
        "date_coverage": {
            "open_date_min": min_open_date,
            "open_date_max": max_open_date,
            "close_date_min": min_close_date,
            "close_date_max": max_close_date,
        },
        "coordinate_rule": {
            "source_crs": "EPSG:5174",
            "seoul_quality_bbox": [SEOUL_TM_X_MIN, SEOUL_TM_Y_MIN, SEOUL_TM_X_MAX, SEOUL_TM_Y_MAX],
            "note": "품질 이상치 분리용 넓은 범위이며 행정경계 판정 기준은 아님",
        },
        "privacy_and_secret_checks": {
            "excluded_source_fields": PII_SOURCE_FIELDS_EXCLUDED,
            "output_header_contains_excluded_field": header_has_pii,
            "seoul_api_key_found_in_output": secret_leak,
            "address_policy": "상세주소를 제외하고 서울 자치구명·주소 존재/서울 여부만 보존",
        },
        "service_results": service_summaries,
        "output_path": str(OUTPUT_PATH.relative_to(ROOT)),
    }
    atomic_write_text(VALIDATION_PATH, json.dumps(validation, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "overall_status": overall_status,
                "source_service_count": len(services),
                "processed_service_count": len(services) - len(missing_services),
                "raw_rows": counters["raw_rows"],
                "silver_rows": output_rows,
                "duplicates_removed": counters["duplicates_removed"],
                "output_path": str(OUTPUT_PATH.relative_to(ROOT)),
                "validation_path": str(VALIDATION_PATH.relative_to(ROOT)),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 2 if hard_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
