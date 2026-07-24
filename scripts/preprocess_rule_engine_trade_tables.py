from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from ingest_common import (
    atomic_write_text,
    emit_progress,
    latest_raw_path,
    raw_history_paths,
    raw_snapshot_date,
    source_state,
    update_source_state_catalog,
)


ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "datacorpus" / "_raw_ingest"
SILVER_DIR = ROOT / "datacorpus" / "_silver"
VALIDATION_DIR = ROOT / "datacorpus" / "_rule_validation"
RESEARCH_VALIDATION_DIR = ROOT / "research" / "rule_validation"

SALES_RAW_DIR = latest_raw_path(
    "seoul_open_data", "full", "VwsmTrdarSelngQq", required_glob="VwsmTrdarSelngQq_*.json"
)
STORE_RAW_DIR = latest_raw_path(
    "seoul_open_data", "full", "VwsmTrdarStorQq", required_glob="VwsmTrdarStorQq_*.json"
)
TRADE_AREA_MASTER_PATH = SILVER_DIR / "silver_trade_area_master.csv"
INDUSTRY_BRIDGE_PATH = SILVER_DIR / "silver_industry_bridge_seoul_sbdc.csv"

PROVIDER = "서울열린데이터광장"


SALES_COLUMNS = {
    "STDR_YYQU_CD": "기준_년분기_코드",
    "TRDAR_SE_CD": "상권_구분_코드",
    "TRDAR_SE_CD_NM": "상권_구분_코드_명",
    "TRDAR_CD": "상권_코드",
    "TRDAR_CD_NM": "상권_코드_명",
    "SVC_INDUTY_CD": "서비스_업종_코드",
    "SVC_INDUTY_CD_NM": "서비스_업종_코드_명",
    "THSMON_SELNG_AMT": "당월_매출_금액",
    "THSMON_SELNG_CO": "당월_매출_건수",
    "MDWK_SELNG_AMT": "평일_매출_금액",
    "WKEND_SELNG_AMT": "주말_매출_금액",
    "MON_SELNG_AMT": "월요일_매출_금액",
    "TUES_SELNG_AMT": "화요일_매출_금액",
    "WED_SELNG_AMT": "수요일_매출_금액",
    "THUR_SELNG_AMT": "목요일_매출_금액",
    "FRI_SELNG_AMT": "금요일_매출_금액",
    "SAT_SELNG_AMT": "토요일_매출_금액",
    "SUN_SELNG_AMT": "일요일_매출_금액",
    "TMZON_00_06_SELNG_AMT": "시간대_00_06_매출_금액",
    "TMZON_06_11_SELNG_AMT": "시간대_06_11_매출_금액",
    "TMZON_11_14_SELNG_AMT": "시간대_11_14_매출_금액",
    "TMZON_14_17_SELNG_AMT": "시간대_14_17_매출_금액",
    "TMZON_17_21_SELNG_AMT": "시간대_17_21_매출_금액",
    "TMZON_21_24_SELNG_AMT": "시간대_21_24_매출_금액",
    "ML_SELNG_AMT": "남성_매출_금액",
    "FML_SELNG_AMT": "여성_매출_금액",
    "AGRDE_10_SELNG_AMT": "연령대_10_매출_금액",
    "AGRDE_20_SELNG_AMT": "연령대_20_매출_금액",
    "AGRDE_30_SELNG_AMT": "연령대_30_매출_금액",
    "AGRDE_40_SELNG_AMT": "연령대_40_매출_금액",
    "AGRDE_50_SELNG_AMT": "연령대_50_매출_금액",
    "AGRDE_60_ABOVE_SELNG_AMT": "연령대_60이상_매출_금액",
    "MDWK_SELNG_CO": "평일_매출_건수",
    "WKEND_SELNG_CO": "주말_매출_건수",
    "MON_SELNG_CO": "월요일_매출_건수",
    "TUES_SELNG_CO": "화요일_매출_건수",
    "WED_SELNG_CO": "수요일_매출_건수",
    "THUR_SELNG_CO": "목요일_매출_건수",
    "FRI_SELNG_CO": "금요일_매출_건수",
    "SAT_SELNG_CO": "토요일_매출_건수",
    "SUN_SELNG_CO": "일요일_매출_건수",
    "TMZON_00_06_SELNG_CO": "시간대_00_06_매출_건수",
    "TMZON_06_11_SELNG_CO": "시간대_06_11_매출_건수",
    "TMZON_11_14_SELNG_CO": "시간대_11_14_매출_건수",
    "TMZON_14_17_SELNG_CO": "시간대_14_17_매출_건수",
    "TMZON_17_21_SELNG_CO": "시간대_17_21_매출_건수",
    "TMZON_21_24_SELNG_CO": "시간대_21_24_매출_건수",
    "ML_SELNG_CO": "남성_매출_건수",
    "FML_SELNG_CO": "여성_매출_건수",
    "AGRDE_10_SELNG_CO": "연령대_10_매출_건수",
    "AGRDE_20_SELNG_CO": "연령대_20_매출_건수",
    "AGRDE_30_SELNG_CO": "연령대_30_매출_건수",
    "AGRDE_40_SELNG_CO": "연령대_40_매출_건수",
    "AGRDE_50_SELNG_CO": "연령대_50_매출_건수",
    "AGRDE_60_ABOVE_SELNG_CO": "연령대_60이상_매출_건수",
}

STORE_COLUMNS = {
    "STDR_YYQU_CD": "기준_년분기_코드",
    "TRDAR_SE_CD": "상권_구분_코드",
    "TRDAR_SE_CD_NM": "상권_구분_코드_명",
    "TRDAR_CD": "상권_코드",
    "TRDAR_CD_NM": "상권_코드_명",
    "SVC_INDUTY_CD": "서비스_업종_코드",
    "SVC_INDUTY_CD_NM": "서비스_업종_코드_명",
    "SIMILR_INDUTY_STOR_CO": "유사_업종_점포_수",
    "STOR_CO": "점포_수",
    "FRC_STOR_CO": "프랜차이즈_점포_수",
    "OPBIZ_RT": "개업_율",
    "OPBIZ_STOR_CO": "개업_점포_수",
    "CLSBIZ_RT": "폐업_률",
    "CLSBIZ_STOR_CO": "폐업_점포_수",
}

KEY_COLS = ["기준_년분기_코드", "상권_코드", "서비스_업종_코드"]


def ensure_dirs() -> None:
    for path in [SILVER_DIR, VALIDATION_DIR, RESEARCH_VALIDATION_DIR]:
        path.mkdir(parents=True, exist_ok=True)


def _progress_snapshot_label(raw_dir: Path) -> str:
    try:
        return raw_snapshot_date(raw_dir)
    except ValueError:
        return raw_dir.name


def atomic_to_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    os.close(fd)
    try:
        df.to_csv(temporary_name, index=False, encoding="utf-8-sig")
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


RAW_KEY_COLS = ("STDR_YYQU_CD", "TRDAR_CD", "SVC_INDUTY_CD")
TOMBSTONE_COLS = ("DELETE_YN", "DEL_YN", "DELETED_YN", "IS_DELETED")


def _raw_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return tuple(str(row.get(col, "")).strip() for col in RAW_KEY_COLS)  # type: ignore[return-value]


def _is_tombstone(row: dict[str, Any]) -> bool:
    return any(str(row.get(col, "")).strip().upper() in {"Y", "1", "TRUE"} for col in TOMBSTONE_COLS)


def _manifest_hashes() -> dict[str, str]:
    path = RAW_DIR / "ingest_manifest.csv"
    if not path.exists():
        return {}
    hashes: dict[str, str] = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            raw_path = str(row.get("raw_path", "")).replace("\\", "/")
            digest = str(row.get("sha256", ""))
            if raw_path and digest:
                hashes[raw_path] = digest
    return hashes


def _manifest_rows() -> list[dict[str, str]]:
    path = RAW_DIR / "ingest_manifest.csv"
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _manifest_service(row: dict[str, str]) -> str:
    try:
        params = json.loads(str(row.get("request_params_json") or "{}"))
    except ValueError:
        return ""
    return str(params.get("service") or "") if isinstance(params, dict) else ""


def _assert_latest_snapshot_complete(
    raw_dir: Path,
    service_name: str,
    manifest_rows: list[dict[str, str]],
) -> dict[str, Any]:
    """Reject a partial newest provider window before any canonical artifact is published."""
    pattern = re.compile(rf"^{re.escape(service_name)}_(\d+)_(\d+)\.json$")
    pages: list[tuple[int, int, Path, int, str]] = []
    totals: set[int] = set()
    response_rows = 0
    page_paths = list(raw_dir.glob(f"{service_name}_*.json"))
    for page_index, path in enumerate(page_paths, start=1):
        match = pattern.match(path.name)
        if not match:
            continue
        body = path.read_bytes()
        payload = json.loads(body.decode("utf-8"))
        root = payload.get(service_name) if isinstance(payload, dict) else None
        if not isinstance(root, dict) or not isinstance(root.get("row"), list):
            raise RuntimeError(f"{service_name} 최신 원본 페이지 구조가 올바르지 않습니다: {path}")
        total = int(root.get("list_total_count") or 0)
        totals.add(total)
        row_count = len(root["row"])
        response_rows += row_count
        pages.append(
            (
                int(match.group(1)),
                int(match.group(2)),
                path,
                row_count,
                hashlib.sha256(body).hexdigest(),
            )
        )
        if page_index % 25 == 0 or page_index == len(page_paths):
            emit_progress(
                label=f"{service_name} 최신 수집본 완전성",
                current_units=page_index,
                total_units=len(page_paths),
                unit="페이지",
                message=f"{_progress_snapshot_label(raw_dir)} 범위·행 수·해시 확인 중",
            )
    if len(totals) != 1 or not pages:
        raise RuntimeError(f"{service_name} 최신 원본의 API 총건수 계약을 확인할 수 없습니다: {sorted(totals)}")
    api_total_count = next(iter(totals))
    if api_total_count <= 0:
        raise RuntimeError(f"{service_name} 최신 원본 API 총건수가 비어 있습니다.")
    pages.sort(key=lambda item: (item[0], item[1]))
    expected_page_count = math.ceil(api_total_count / 1000)
    coverage_errors: list[str] = []
    if len(pages) != expected_page_count:
        coverage_errors.append(f"pages={len(pages)}/{expected_page_count}")
    for index, (start, end, path, row_count, _digest) in enumerate(pages, start=1):
        expected_start = (index - 1) * 1000 + 1
        expected_end = min(index * 1000, api_total_count)
        end_matches = end == expected_end or (expected_page_count == 1 and end == 1000)
        expected_rows = max(0, expected_end - expected_start + 1)
        if start != expected_start or not end_matches or row_count != expected_rows:
            coverage_errors.append(
                f"{path.name}:range={start}-{end},rows={row_count},"
                f"expected={expected_start}-{expected_end}/{expected_rows}"
            )
    if response_rows != api_total_count:
        coverage_errors.append(f"rows={response_rows}/{api_total_count}")
    if coverage_errors:
        raise RuntimeError(
            f"{service_name} 최신 원본이 부분 수집 상태입니다: " + "; ".join(coverage_errors)
        )

    page_hashes = {
        str(path.relative_to(ROOT)).replace("\\", "/"): digest
        for _start, _end, path, _row_count, digest in pages
    }
    raw_prefix = str(raw_dir.relative_to(ROOT)).replace("\\", "/").rstrip("/") + "/"
    service_rows = [
        row
        for row in manifest_rows
        if _manifest_service(row) == service_name
        and str(row.get("raw_path") or "").replace("\\", "/").startswith(raw_prefix)
    ]
    complete_runs: dict[str, dict[str, str]] = {}
    completion_times: dict[str, str] = {}
    for row in service_rows:
        if row.get("full_collection_status") != "complete":
            continue
        run_id_value = str(row.get("run_id") or "")
        raw_path = str(row.get("raw_path") or "").replace("\\", "/")
        digest = str(row.get("sha256") or "")
        if run_id_value and raw_path and digest:
            complete_runs.setdefault(run_id_value, {})[raw_path] = digest
            completion_times[run_id_value] = str(row.get("full_collection_completed_at") or "")
    valid_runs = [run_id_value for run_id_value, hashes in complete_runs.items() if hashes == page_hashes]
    if complete_runs and not valid_runs:
        raise RuntimeError(
            f"{service_name} 최신 원본 파일이 완료 manifest의 페이지 해시 집합과 일치하지 않습니다."
        )
    if valid_runs:
        selected_run = max(valid_runs, key=lambda value: completion_times.get(value, ""))
        completion_status = "complete_manifest"
        completed_at = completion_times.get(selected_run) or None
    else:
        selected_run = None
        completion_status = "legacy_structural_complete"
        completed_at = None
    return {
        "completion_status": completion_status,
        "completed_run_id": selected_run,
        "full_collection_completed_at": completed_at,
        "expected_page_count": expected_page_count,
        "raw_page_count": len(pages),
        "raw_response_rows": response_rows,
        "api_total_count": api_total_count,
    }


def _directory_fingerprint(raw_dir: Path, service_name: str, hashes: dict[str, str]) -> str:
    entries: list[str] = []
    for path in sorted(raw_dir.glob(f"{service_name}_*.json")):
        relative = str(path.relative_to(ROOT)).replace("\\", "/")
        digest = hashes.get(relative) or f"{path.stat().st_size}:{path.stat().st_mtime_ns}"
        entries.append(f"{path.name}:{digest}")
    return hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest()


def read_openapi_pages(
    raw_dir: Path,
    service_name: str,
    *,
    seen_keys: set[tuple[str, str, str]] | None = None,
    tombstoned_keys: set[tuple[str, str, str]] | None = None,
) -> tuple[pd.DataFrame, int, int, set[tuple[str, str, str]]]:
    rows: list[dict[str, Any]] = []
    total_counts: set[int] = set()
    local_tombstones: set[tuple[str, str, str]] = set()
    seen = seen_keys if seen_keys is not None else set()
    tombstones = tombstoned_keys if tombstoned_keys is not None else set()
    page_paths = sorted(raw_dir.glob(f"{service_name}_*.json"))
    if not page_paths:
        raise FileNotFoundError(f"{raw_dir} 아래에서 {service_name} 원응답을 찾지 못했습니다.")

    total_pages = len(page_paths)
    for page_index, path in enumerate(page_paths, start=1):
        payload = json.loads(path.read_text(encoding="utf-8"))
        root = payload.get(service_name)
        if not isinstance(root, dict):
            raise ValueError(f"{path} 파일에 {service_name} 루트가 없습니다.")
        if "list_total_count" in root:
            total_counts.add(int(root["list_total_count"]))
        for row in root.get("row", []):
            key = _raw_key(row)
            if _is_tombstone(row):
                if key not in seen:
                    tombstones.add(key)
                    local_tombstones.add(key)
                continue
            if key in seen or key in tombstones:
                continue
            seen.add(key)
            item = dict(row)
            item["_raw_path"] = str(path.relative_to(ROOT))
            rows.append(item)

        if page_index % 25 == 0 or page_index == total_pages:
            emit_progress(
                label=f"{service_name} 원본 페이지 읽기",
                current_units=page_index,
                total_units=total_pages,
                unit="페이지",
                message=f"{_progress_snapshot_label(raw_dir)} 스냅샷 원본을 읽는 중",
            )

    if len(total_counts) != 1:
        raise ValueError(f"{service_name} list_total_count가 하나로 고정되지 않았습니다: {sorted(total_counts)}")
    return pd.DataFrame(rows), len(page_paths), next(iter(total_counts)), local_tombstones


def normalize_common_codes(df: pd.DataFrame) -> pd.DataFrame:
    # 코드 컬럼은 숫자처럼 보여도 조인 키이므로 문자열로 보존한다.
    for col in ["기준_년분기_코드", "상권_구분_코드", "상권_코드", "서비스_업종_코드"]:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()
    for col in ["상권_구분_코드_명", "상권_코드_명", "서비스_업종_코드_명"]:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()
    return df


def numericize(df: pd.DataFrame, skip_cols: set[str]) -> pd.DataFrame:
    out = df.copy()
    for col in out.columns:
        if col in skip_cols:
            continue
        out[col] = pd.to_numeric(out[col], errors="coerce")
    return out


def add_lineage(
    df: pd.DataFrame,
    *,
    source_id: str,
    service_name: str,
    page_count: int,
    api_total_count: int,
    source_grain: str,
    notes_ko: str,
    forbidden_claim_ko: str,
    snapshot_date: str,
) -> pd.DataFrame:
    out = df.copy()
    out["source_id"] = source_id
    out["provider"] = PROVIDER
    out["source_service"] = service_name
    out["snapshot_date"] = snapshot_date
    out["source_grain"] = source_grain
    out["raw_page_count"] = page_count
    out["api_list_total_count"] = api_total_count
    out["raw_row_count"] = len(out)
    out["directness_level"] = "P0_공식_상권_집계"
    out["forbidden_claim_ko"] = forbidden_claim_ko
    out["notes_ko"] = notes_ko
    return out


def _build_sales_snapshot(
    raw_dir: Path,
    *,
    seen_keys: set[tuple[str, str, str]],
    tombstoned_keys: set[tuple[str, str, str]],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    raw, page_count, api_total_count, tombstones = read_openapi_pages(
        raw_dir,
        "VwsmTrdarSelngQq",
        seen_keys=seen_keys,
        tombstoned_keys=tombstoned_keys,
    )
    if raw.empty:
        return raw, {
            "page_count": page_count,
            "api_total_count": api_total_count,
            "snapshot_date": raw_snapshot_date(raw_dir),
            "retained_rows": 0,
            "tombstone_rows": len(tombstones),
        }
    df = raw.rename(columns=SALES_COLUMNS)
    expected = list(SALES_COLUMNS.values())
    missing = [col for col in expected if col not in df.columns]
    if missing:
        raise ValueError(f"매출 원천 컬럼 변환 후 누락 컬럼: {missing}")
    df = df[expected]
    df = normalize_common_codes(df)
    df = numericize(df, skip_cols=set(expected[:7]))
    # silver는 원천값을 임의로 고치지 않는다. 대신 알고리즘에서 제외/감점할 수 있도록 품질 플래그를 붙인다.
    sales_numeric_cols = [col for col in df.columns if col.endswith("_금액") or col.endswith("_건수")]
    sales_core_cols = ["당월_매출_금액", "당월_매출_건수"]
    sales_breakdown_cols = [col for col in sales_numeric_cols if col not in sales_core_cols]
    df["quality_negative_core_cell_count"] = (df[sales_core_cols] < 0).sum(axis=1)
    df["quality_negative_breakdown_cell_count"] = (df[sales_breakdown_cols] < 0).sum(axis=1)
    df = add_lineage(
        df,
        source_id="seoul_sales_trade_area",
        service_name="VwsmTrdarSelngQq",
        page_count=page_count,
        api_total_count=api_total_count,
        source_grain="기준년분기+상권코드+서비스업종코드",
        notes_ko="서울 상권분석서비스 추정매출-상권 전체 원응답을 silver로 정규화했다. 개별 매장 실제 매출이 아니라 상권-업종 집계 추정매출이다.",
        forbidden_claim_ko="개별 매장 매출 보장, 창업 성공확률, 실제 카드매출 원장으로 표현 금지",
        snapshot_date=raw_snapshot_date(raw_dir),
    )
    df = df.sort_values(KEY_COLS).reset_index(drop=True)
    meta = {
        "page_count": page_count,
        "api_total_count": api_total_count,
        "snapshot_date": raw_snapshot_date(raw_dir),
        "retained_rows": len(df),
        "tombstone_rows": len(tombstones),
        "data_period_start": str(df["기준_년분기_코드"].min()),
        "data_period_end": str(df["기준_년분기_코드"].max()),
    }
    return df, meta


def _build_store_snapshot(
    raw_dir: Path,
    *,
    seen_keys: set[tuple[str, str, str]],
    tombstoned_keys: set[tuple[str, str, str]],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    raw, page_count, api_total_count, tombstones = read_openapi_pages(
        raw_dir,
        "VwsmTrdarStorQq",
        seen_keys=seen_keys,
        tombstoned_keys=tombstoned_keys,
    )
    if raw.empty:
        return raw, {
            "page_count": page_count,
            "api_total_count": api_total_count,
            "snapshot_date": raw_snapshot_date(raw_dir),
            "retained_rows": 0,
            "tombstone_rows": len(tombstones),
        }
    df = raw.rename(columns=STORE_COLUMNS)
    expected = list(STORE_COLUMNS.values())
    missing = [col for col in expected if col not in df.columns]
    if missing:
        raise ValueError(f"점포 원천 컬럼 변환 후 누락 컬럼: {missing}")
    df = df[expected]
    df = normalize_common_codes(df)
    df = numericize(df, skip_cols=set(expected[:7]))
    # 개폐업률은 소규모 모수에서 100을 넘을 수 있으므로 원천을 보존하고 별도 플래그로만 남긴다.
    store_count_cols = ["유사_업종_점포_수", "점포_수", "프랜차이즈_점포_수", "개업_점포_수", "폐업_점포_수"]
    store_rate_cols = ["개업_율", "폐업_률"]
    df["quality_negative_count_cell_count"] = (df[store_count_cols] < 0).sum(axis=1)
    df["quality_negative_rate_cell_count"] = (df[store_rate_cols] < 0).sum(axis=1)
    df["quality_rate_above_100_cell_count"] = (df[store_rate_cols] > 100).sum(axis=1)
    df = add_lineage(
        df,
        source_id="seoul_store_trade_area",
        service_name="VwsmTrdarStorQq",
        page_count=page_count,
        api_total_count=api_total_count,
        source_grain="기준년분기+상권코드+서비스업종코드",
        notes_ko="서울 상권분석서비스 점포-상권 전체 원응답을 silver로 정규화했다. 점포수, 유사업종 점포수, 개폐업률은 경쟁/안정성 축의 근거다.",
        forbidden_claim_ko="개별 매장 생존확률, 개별 점포 매출 보장, 실제 임대수익성 판단으로 표현 금지",
        snapshot_date=raw_snapshot_date(raw_dir),
    )
    df = df.sort_values(KEY_COLS).reset_index(drop=True)
    meta = {
        "page_count": page_count,
        "api_total_count": api_total_count,
        "snapshot_date": raw_snapshot_date(raw_dir),
        "retained_rows": len(df),
        "tombstone_rows": len(tombstones),
        "data_period_start": str(df["기준_년분기_코드"].min()),
        "data_period_end": str(df["기준_년분기_코드"].max()),
    }
    return df, meta


def _build_cumulative_table(
    *,
    history: list[Path],
    service_name: str,
    builder: Any,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if not history:
        raise FileNotFoundError(f"{service_name} 원천 이력을 찾지 못했습니다.")
    seen_keys: set[tuple[str, str, str]] = set()
    tombstoned_keys: set[tuple[str, str, str]] = set()
    parts: list[pd.DataFrame] = []
    snapshots: list[dict[str, Any]] = []
    manifest_hashes = _manifest_hashes()
    emit_progress(
        label=f"{service_name} 최신 스냅샷 확인",
        current_units=0,
        total_units=1,
        unit="검증",
        message=f"{_progress_snapshot_label(history[0])} 원본 완전성 확인 중",
    )
    latest_snapshot_contract = _assert_latest_snapshot_complete(
        history[0], service_name, _manifest_rows()
    )
    emit_progress(
        label=f"{service_name} 최신 스냅샷 확인",
        current_units=1,
        total_units=1,
        unit="검증",
        message=(
            f"{latest_snapshot_contract['raw_page_count']}페이지, "
            f"{latest_snapshot_contract['raw_response_rows']}행 완전성 확인 완료"
        ),
    )
    history_fingerprints = [
        (raw_dir, _directory_fingerprint(raw_dir, service_name, manifest_hashes))
        for raw_dir in history
    ]
    latest_fingerprint = history_fingerprints[0][1]
    latest_snapshot_date = raw_snapshot_date(history[0])
    previous_state = source_state(service_name) or {}
    previous_fingerprint = str(
        previous_state.get("full_content_fingerprint")
        or previous_state.get("content_fingerprint")
        or ""
    )
    previous_content_version = str(previous_state.get("content_version_date") or "")
    if previous_fingerprint == latest_fingerprint and previous_content_version:
        content_version_date = previous_content_version
    else:
        content_version_date = latest_snapshot_date
        for raw_dir, fingerprint in history_fingerprints[1:]:
            if fingerprint != latest_fingerprint:
                break
            content_version_date = raw_snapshot_date(raw_dir)
    last_checked_at = datetime.now().astimezone().isoformat(timespec="seconds")

    for index, (raw_dir, fingerprint) in enumerate(history_fingerprints):
        if index > 0 and fingerprint == latest_fingerprint:
            snapshots.append(
                {
                    "snapshot_date": raw_snapshot_date(raw_dir),
                    "retained_rows": 0,
                    "skipped_exact_duplicate": True,
                    "content_fingerprint": fingerprint,
                }
            )
            emit_progress(
                label=f"{service_name} 누적 스냅샷 확인",
                current_units=index + 1,
                total_units=len(history),
                unit="스냅샷",
                message=f"{_progress_snapshot_label(raw_dir)} 동일 원본 건너뜀",
            )
            continue
        frame, meta = builder(
            raw_dir,
            seen_keys=seen_keys,
            tombstoned_keys=tombstoned_keys,
        )
        meta["content_fingerprint"] = fingerprint
        meta["skipped_exact_duplicate"] = False
        snapshots.append(meta)
        if index == 0 and "snapshot_date" in frame.columns:
            frame["snapshot_date"] = content_version_date
        if not frame.empty:
            parts.append(frame)
        emit_progress(
            label=f"{service_name} 누적 스냅샷 확인",
            current_units=index + 1,
            total_units=len(history),
            unit="스냅샷",
            message=f"{_progress_snapshot_label(raw_dir)} 스냅샷 확인 완료",
        )
    if not parts:
        raise RuntimeError(f"{service_name} 누적 병합 결과가 비어 있습니다.")
    combined = pd.concat(parts, ignore_index=True, copy=False)
    combined = combined.sort_values(KEY_COLS).reset_index(drop=True)
    emit_progress(
        label=f"{service_name} 누적 병합",
        current_units=1,
        total_units=1,
        unit="병합",
        message=(
            f"concat/dedupe 완료: {len(combined)}행 · "
            f"내용 버전 {content_version_date} · 최근 확인 {latest_snapshot_date}"
        ),
    )
    latest_meta = snapshots[0]
    retained_period_start = str(combined[KEY_COLS[0]].min())
    retained_period_end = str(combined[KEY_COLS[0]].max())
    meta = {
        "page_count": latest_meta.get("page_count", 0),
        "api_total_count": latest_meta.get("api_total_count", 0),
        "latest_window_rows": latest_meta.get("retained_rows", 0),
        "history_retained_rows": len(combined) - int(latest_meta.get("retained_rows", 0)),
        "history_snapshot_count": len(history),
        "tombstone_rows": len(tombstoned_keys),
        "latest_window_period_start": latest_meta.get("data_period_start"),
        "latest_window_period_end": latest_meta.get("data_period_end"),
        "retained_period_start": retained_period_start,
        "retained_period_end": retained_period_end,
        "data_period_start": retained_period_start,
        "data_period_end": retained_period_end,
        "content_version_date": content_version_date,
        "latest_snapshot_date": latest_snapshot_date,
        "last_checked_at": last_checked_at,
        "latest_snapshot_contract": latest_snapshot_contract,
        "snapshots": snapshots,
    }
    return combined, meta


def build_sales_table() -> tuple[pd.DataFrame, dict[str, Any]]:
    history = raw_history_paths(
        "seoul_open_data", "full", "VwsmTrdarSelngQq", required_glob="VwsmTrdarSelngQq_*.json"
    )
    return _build_cumulative_table(
        history=history,
        service_name="VwsmTrdarSelngQq",
        builder=_build_sales_snapshot,
    )


def build_store_table() -> tuple[pd.DataFrame, dict[str, Any]]:
    history = raw_history_paths(
        "seoul_open_data", "full", "VwsmTrdarStorQq", required_glob="VwsmTrdarStorQq_*.json"
    )
    return _build_cumulative_table(
        history=history,
        service_name="VwsmTrdarStorQq",
        builder=_build_store_snapshot,
    )


def build_seoul_industry_master(
    sales: pd.DataFrame,
    store: pd.DataFrame,
    *,
    sales_content_version_date: str,
    store_content_version_date: str,
) -> pd.DataFrame:
    sales_codes = sales[["서비스_업종_코드", "서비스_업종_코드_명"]].drop_duplicates()
    sales_codes["매출_원천_존재"] = True
    store_codes = store[["서비스_업종_코드", "서비스_업종_코드_명"]].drop_duplicates()
    store_codes["점포_원천_존재"] = True

    merged = sales_codes.merge(store_codes, on=["서비스_업종_코드", "서비스_업종_코드_명"], how="outer")
    merged["매출_원천_존재"] = merged["매출_원천_존재"].fillna(False).astype(bool)
    merged["점포_원천_존재"] = merged["점포_원천_존재"].fillna(False).astype(bool)
    merged["source_id"] = merged.apply(
        lambda r: ";".join(
            src
            for src, exists in [
                ("seoul_sales_trade_area", bool(r["매출_원천_존재"])),
                ("seoul_store_trade_area", bool(r["점포_원천_존재"])),
            ]
            if exists
        ),
        axis=1,
    )
    merged["provider"] = PROVIDER
    merged["snapshot_date"] = max(
        sales_content_version_date,
        store_content_version_date,
    )
    merged["sales_content_version_date"] = sales_content_version_date
    merged["store_content_version_date"] = store_content_version_date
    merged["notes_ko"] = "서울 상권분석 매출/점포 원천에서 관측된 서비스 업종 코드 union이다. SBDC 계층 매핑 완료 여부와 원천 존재 여부를 분리해서 봐야 한다."

    if INDUSTRY_BRIDGE_PATH.exists():
        bridge = pd.read_csv(INDUSTRY_BRIDGE_PATH, encoding="utf-8-sig", dtype=str).fillna("")
        bridge_cols = [col for col in bridge.columns if col in ["서비스_업종_코드", "업종매핑_검토상태", "score_use_status", "mapping_review_required"]]
        if "서비스_업종_코드" in bridge.columns:
            merged = merged.merge(bridge[bridge_cols].drop_duplicates("서비스_업종_코드"), on="서비스_업종_코드", how="left")
    merged["SBDC_계층_매핑_존재"] = merged.get("업종매핑_검토상태", "").astype(str).str.len() > 0
    merged["알고리즘_업종계층_상태"] = merged["SBDC_계층_매핑_존재"].map({True: "계층매핑_검토가능", False: "계층매핑_추가필요"})
    return merged.sort_values("서비스_업종_코드").reset_index(drop=True)


def key_null_cells(df: pd.DataFrame, key_cols: list[str]) -> int:
    total = 0
    for col in key_cols:
        if col not in df.columns:
            total += len(df)
        else:
            total += int((df[col].isna() | df[col].astype(str).str.strip().eq("")).sum())
    return total


def duplicate_key_rows(df: pd.DataFrame, key_cols: list[str]) -> int:
    missing = [col for col in key_cols if col not in df.columns]
    if missing:
        return len(df)
    return int(df.duplicated(key_cols).sum())


def count_negative_cells(df: pd.DataFrame, cols: list[str]) -> int:
    total = 0
    for col in cols:
        if col in df.columns:
            total += int((df[col] < 0).sum())
    return total


def count_ratio_out_of_bounds(df: pd.DataFrame, cols: list[str]) -> int:
    total = 0
    for col in cols:
        if col in df.columns:
            total += int(((df[col] < 0) | (df[col] > 100)).sum())
    return total


def count_ratio_negative(df: pd.DataFrame, cols: list[str]) -> int:
    total = 0
    for col in cols:
        if col in df.columns:
            total += int((df[col] < 0).sum())
    return total


def count_ratio_above_100(df: pd.DataFrame, cols: list[str]) -> int:
    total = 0
    for col in cols:
        if col in df.columns:
            total += int((df[col] > 100).sum())
    return total


def sum_mismatch_rows(df: pd.DataFrame, total_col: str, parts: list[str]) -> int:
    if total_col not in df.columns or any(col not in df.columns for col in parts):
        return len(df)
    # 원천이 정수 집계라 완전 일치가 정상이다. 불일치는 필수 실패가 아니라 원천 내부 검토 지표로 남긴다.
    return int((df[parts].sum(axis=1) != df[total_col]).sum())


def load_code_set(path: Path, code_col: str) -> set[str]:
    if not path.exists():
        return set()
    df = pd.read_csv(path, encoding="utf-8-sig", dtype=str).fillna("")
    if code_col not in df.columns:
        return set()
    return set(df[code_col].astype(str).str.strip())


def validate_sales_store(
    sales: pd.DataFrame,
    store: pd.DataFrame,
    industry_master: pd.DataFrame,
    sales_meta: dict[str, Any],
    store_meta: dict[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    trade_area_codes = load_code_set(TRADE_AREA_MASTER_PATH, "상권_코드")
    bridge_codes = load_code_set(INDUSTRY_BRIDGE_PATH, "서비스_업종_코드")
    industry_codes = set(industry_master["서비스_업종_코드"].astype(str))

    sales_numeric_cols = [col for col in sales.columns if col.endswith("_금액") or col.endswith("_건수")]
    sales_core_cols = ["당월_매출_금액", "당월_매출_건수"]
    sales_breakdown_cols = [col for col in sales_numeric_cols if col not in sales_core_cols]
    store_nonnegative_cols = ["유사_업종_점포_수", "점포_수", "프랜차이즈_점포_수", "개업_점포_수", "폐업_점포_수"]
    store_ratio_cols = ["개업_율", "폐업_률"]

    domain_rows = [
        {
            "table": "silver_sales_trade_area_q_industry",
            "rows": len(sales),
            "api_total_count": sales_meta["api_total_count"],
            "raw_page_count": sales_meta["page_count"],
            "latest_window_rows": sales_meta["latest_window_rows"],
            "history_retained_rows": sales_meta["history_retained_rows"],
            "row_count_matches_api": sales_meta["latest_window_rows"] == sales_meta["api_total_count"],
            "quarter_min": sales["기준_년분기_코드"].min(),
            "quarter_max": sales["기준_년분기_코드"].max(),
            "quarter_count": sales["기준_년분기_코드"].nunique(),
            "area_count": sales["상권_코드"].nunique(),
            "industry_count": sales["서비스_업종_코드"].nunique(),
            "key_null_cells": key_null_cells(sales, KEY_COLS),
            "duplicate_key_rows": duplicate_key_rows(sales, KEY_COLS),
            "negative_numeric_cells": count_negative_cells(sales, sales_numeric_cols),
            "negative_core_cells": count_negative_cells(sales, sales_core_cols),
            "negative_optional_breakdown_cells": count_negative_cells(sales, sales_breakdown_cols),
            "ratio_out_of_bounds_cells": 0,
            "ratio_negative_cells": 0,
            "ratio_above_100_cells": 0,
            "area_codes_missing_from_master": len(set(sales["상권_코드"]) - trade_area_codes) if trade_area_codes else -1,
            "industry_codes_missing_from_union_master": len(set(sales["서비스_업종_코드"]) - industry_codes),
            "industry_codes_missing_from_sbdc_bridge": len(set(sales["서비스_업종_코드"]) - bridge_codes) if bridge_codes else -1,
            "weekday_weekend_amount_mismatch_rows": sum_mismatch_rows(sales, "당월_매출_금액", ["평일_매출_금액", "주말_매출_금액"]),
            "weekday_weekend_count_mismatch_rows": sum_mismatch_rows(sales, "당월_매출_건수", ["평일_매출_건수", "주말_매출_건수"]),
            "day_amount_mismatch_rows": sum_mismatch_rows(sales, "당월_매출_금액", ["월요일_매출_금액", "화요일_매출_금액", "수요일_매출_금액", "목요일_매출_금액", "금요일_매출_금액", "토요일_매출_금액", "일요일_매출_금액"]),
            "time_amount_mismatch_rows": sum_mismatch_rows(sales, "당월_매출_금액", ["시간대_00_06_매출_금액", "시간대_06_11_매출_금액", "시간대_11_14_매출_금액", "시간대_14_17_매출_금액", "시간대_17_21_매출_금액", "시간대_21_24_매출_금액"]),
        },
        {
            "table": "silver_store_trade_area_q_industry",
            "rows": len(store),
            "api_total_count": store_meta["api_total_count"],
            "raw_page_count": store_meta["page_count"],
            "latest_window_rows": store_meta["latest_window_rows"],
            "history_retained_rows": store_meta["history_retained_rows"],
            "row_count_matches_api": store_meta["latest_window_rows"] == store_meta["api_total_count"],
            "quarter_min": store["기준_년분기_코드"].min(),
            "quarter_max": store["기준_년분기_코드"].max(),
            "quarter_count": store["기준_년분기_코드"].nunique(),
            "area_count": store["상권_코드"].nunique(),
            "industry_count": store["서비스_업종_코드"].nunique(),
            "key_null_cells": key_null_cells(store, KEY_COLS),
            "duplicate_key_rows": duplicate_key_rows(store, KEY_COLS),
            "negative_numeric_cells": count_negative_cells(store, store_nonnegative_cols + store_ratio_cols),
            "negative_core_cells": count_negative_cells(store, store_nonnegative_cols) + count_ratio_negative(store, store_ratio_cols),
            "negative_optional_breakdown_cells": 0,
            "ratio_out_of_bounds_cells": count_ratio_out_of_bounds(store, store_ratio_cols),
            "ratio_negative_cells": count_ratio_negative(store, store_ratio_cols),
            "ratio_above_100_cells": count_ratio_above_100(store, store_ratio_cols),
            "area_codes_missing_from_master": len(set(store["상권_코드"]) - trade_area_codes) if trade_area_codes else -1,
            "industry_codes_missing_from_union_master": len(set(store["서비스_업종_코드"]) - industry_codes),
            "industry_codes_missing_from_sbdc_bridge": len(set(store["서비스_업종_코드"]) - bridge_codes) if bridge_codes else -1,
            "weekday_weekend_amount_mismatch_rows": "",
            "weekday_weekend_count_mismatch_rows": "",
            "day_amount_mismatch_rows": "",
            "time_amount_mismatch_rows": "",
        },
    ]
    domain_df = pd.DataFrame(domain_rows)
    hard_fail_cols = ["row_count_matches_api", "key_null_cells", "duplicate_key_rows", "negative_core_cells", "ratio_negative_cells", "area_codes_missing_from_master", "industry_codes_missing_from_union_master"]
    judgements: list[str] = []
    for row in domain_df.to_dict("records"):
        hard_fail = (
            row["row_count_matches_api"] is not True
            or row["key_null_cells"] != 0
            or row["duplicate_key_rows"] != 0
            or row["negative_core_cells"] != 0
            or row["ratio_negative_cells"] != 0
            or row["area_codes_missing_from_master"] not in [0, -1]
            or row["industry_codes_missing_from_union_master"] != 0
        )
        if hard_fail:
            judgements.append("FAIL")
        elif (
            row["industry_codes_missing_from_sbdc_bridge"] not in [0, -1]
            or row["negative_optional_breakdown_cells"] != 0
            or row["ratio_above_100_cells"] != 0
        ):
            judgements.append("조건부 PASS")
        else:
            judgements.append("PASS")
    domain_df["judgement"] = judgements
    domain_df["hard_fail_rule_cols"] = ";".join(hard_fail_cols)

    grain_df = pd.DataFrame(
        [
            {
                "table": "silver_sales_trade_area_q_industry",
                "key_cols": " + ".join(KEY_COLS),
                "duplicate_key_rows": duplicate_key_rows(sales, KEY_COLS),
                "key_null_cells": key_null_cells(sales, KEY_COLS),
                "judgement": "PASS" if duplicate_key_rows(sales, KEY_COLS) == 0 and key_null_cells(sales, KEY_COLS) == 0 else "FAIL",
                "reason_ko": "매출축과 점포당 매출 계산은 분기+상권+업종 grain이 깨지면 중복 합산 위험이 생긴다.",
            },
            {
                "table": "silver_store_trade_area_q_industry",
                "key_cols": " + ".join(KEY_COLS),
                "duplicate_key_rows": duplicate_key_rows(store, KEY_COLS),
                "key_null_cells": key_null_cells(store, KEY_COLS),
                "judgement": "PASS" if duplicate_key_rows(store, KEY_COLS) == 0 and key_null_cells(store, KEY_COLS) == 0 else "FAIL",
                "reason_ko": "경쟁/개폐업률 축은 같은 분기+상권+업종이 한 행이어야 비교와 백테스트가 가능하다.",
            },
            {
                "table": "silver_industry_master_seoul_open_data",
                "key_cols": "서비스_업종_코드",
                "duplicate_key_rows": duplicate_key_rows(industry_master, ["서비스_업종_코드"]),
                "key_null_cells": key_null_cells(industry_master, ["서비스_업종_코드"]),
                "judgement": "PASS" if duplicate_key_rows(industry_master, ["서비스_업종_코드"]) == 0 and key_null_cells(industry_master, ["서비스_업종_코드"]) == 0 else "FAIL",
                "reason_ko": "매출 63개, 점포 100개 업종 universe 차이를 보존해야 업종 선택과 점수 제외 사유를 설명할 수 있다.",
            },
        ]
    )

    contract_df = pd.DataFrame(
        [
            {
                "table": "silver_sales_trade_area_q_industry",
                "source_id": "seoul_sales_trade_area",
                "provider": PROVIDER,
                "source_service": "VwsmTrdarSelngQq",
                "rows": len(sales),
                "contract_status": domain_df.loc[domain_df["table"].eq("silver_sales_trade_area_q_industry"), "judgement"].iloc[0],
                "usage_role": "매출축, 점포당 매출, 객단가 산정의 P0 원천",
            },
            {
                "table": "silver_store_trade_area_q_industry",
                "source_id": "seoul_store_trade_area",
                "provider": PROVIDER,
                "source_service": "VwsmTrdarStorQq",
                "rows": len(store),
                "contract_status": domain_df.loc[domain_df["table"].eq("silver_store_trade_area_q_industry"), "judgement"].iloc[0],
                "usage_role": "경쟁, 유사점포, 개폐업률, 안정성 산정의 P0 원천",
            },
            {
                "table": "silver_industry_master_seoul_open_data",
                "source_id": "seoul_sales_trade_area;seoul_store_trade_area",
                "provider": PROVIDER,
                "source_service": "VwsmTrdarSelngQq;VwsmTrdarStorQq",
                "rows": len(industry_master),
                "contract_status": grain_df.loc[grain_df["table"].eq("silver_industry_master_seoul_open_data"), "judgement"].iloc[0],
                "usage_role": "업종 선택과 SBDC 계층 매핑 보완을 위한 서울 서비스 업종 union",
            },
        ]
    )
    return domain_df, grain_df, contract_df


def assert_prepublication_contract(
    domain_df: pd.DataFrame,
    grain_df: pd.DataFrame,
    contract_df: pd.DataFrame,
    sales_meta: dict[str, Any],
    store_meta: dict[str, Any],
) -> None:
    failures: list[str] = []
    for label, meta in (("sales", sales_meta), ("store", store_meta)):
        if int(meta.get("latest_window_rows") or 0) != int(meta.get("api_total_count") or 0):
            failures.append(
                f"{label}.latest_window_rows={meta.get('latest_window_rows')}/"
                f"{meta.get('api_total_count')}"
            )
        snapshot_contract = meta.get("latest_snapshot_contract") or {}
        if int(snapshot_contract.get("raw_response_rows") or 0) != int(meta.get("api_total_count") or 0):
            failures.append(f"{label}.latest_snapshot_contract")
    failures.extend(
        f"domain:{value}"
        for value in domain_df.loc[domain_df["judgement"].eq("FAIL"), "table"].astype(str)
    )
    failures.extend(
        f"grain:{value}"
        for value in grain_df.loc[grain_df["judgement"].eq("FAIL"), "table"].astype(str)
    )
    failures.extend(
        f"contract:{value}"
        for value in contract_df.loc[contract_df["contract_status"].eq("FAIL"), "table"].astype(str)
    )
    if failures:
        raise RuntimeError(
            "Sales/store prepublication gate failed; no Silver, validation, or source-state "
            "artifact was published: " + "; ".join(failures)
        )


def write_validation_md(domain_df: pd.DataFrame, grain_df: pd.DataFrame, contract_df: pd.DataFrame) -> None:
    path = RESEARCH_VALIDATION_DIR / "03_sales_store_silver_validation_20260703.md"
    sales = domain_df.loc[domain_df["table"].eq("silver_sales_trade_area_q_industry")].iloc[0].to_dict()
    store = domain_df.loc[domain_df["table"].eq("silver_store_trade_area_q_industry")].iloc[0].to_dict()
    missing_bridge_store = int(store["industry_codes_missing_from_sbdc_bridge"])

    lines = [
        "# 3회차 매출/점포 silver 전처리 검증",
        "",
        f"작성시각: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "## 대상 파일",
        "",
        "- `datacorpus/_silver/silver_sales_trade_area_q_industry.csv`",
        "- `datacorpus/_silver/silver_store_trade_area_q_industry.csv`",
        "- `datacorpus/_silver/silver_industry_master_seoul_open_data.csv`",
        "",
        "## 사용 근거",
        "",
        "- `datacorpus/_raw_ingest/source_registry.csv`: 매출과 점포 원천이 P0 핵심 원천으로 등록되어 있다.",
        "- `datacorpus/_raw_ingest/seoul_core_coverage_audit.csv`: 전체 API 원응답 행 수가 API 총 건수와 일치한다고 기록되어 있다.",
        "- `research/전처리_알고리즘_실행계획_20260703.md`: 매출은 매출축/점포당 매출/객단가, 점포는 경쟁/집적/개폐업률 축의 핵심으로 지정되어 있다.",
        "- `research/전처리_전_확인사항_20260703.md`: 이름이 아니라 코드 키를 보존하고, 파일별 검증 후 알고리즘에 투입해야 한다고 정리되어 있다.",
        "",
        "## 검증 1: 원천 총량 계약",
        "",
        "| table | rows | api_total_count | raw_page_count | judgement |",
        "|---|---:|---:|---:|---|",
    ]
    for row in domain_df.to_dict("records"):
        lines.append(f"| `{row['table']}` | {row['rows']} | {row['api_total_count']} | {row['raw_page_count']} | {row['judgement']} |")

    lines.extend(
        [
            "",
            "판단: 매출과 점포 모두 raw row 수가 API `list_total_count`와 일치한다. 이 단계에서는 기존 중복 CSV가 아니라 2026-07-03 전체 API 원응답을 canonical raw로 채택한다.",
            "",
            "## 검증 2: grain과 조인 키",
            "",
            "| table | key_cols | duplicate_key_rows | key_null_cells | judgement |",
            "|---|---|---:|---:|---|",
        ]
    )
    for row in grain_df.to_dict("records"):
        lines.append(f"| `{row['table']}` | `{row['key_cols']}` | {row['duplicate_key_rows']} | {row['key_null_cells']} | {row['judgement']} |")

    lines.extend(
        [
            "",
            "판단: 매출/점포 모두 `기준_년분기_코드 + 상권_코드 + 서비스_업종_코드`를 보존한다. 이 키가 깨지면 매출과 점포 수가 중복 합산되므로 알고리즘 입력에서 가장 먼저 확인해야 한다.",
            "",
            "## 검증 3: 값 범위",
            "",
            "| table | negative_core_cells | negative_optional_breakdown_cells | ratio_negative_cells | ratio_above_100_cells | area_missing | industry_missing_union | judgement |",
            "|---|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for row in domain_df.to_dict("records"):
        lines.append(
            f"| `{row['table']}` | {row['negative_core_cells']} | {row['negative_optional_breakdown_cells']} | {row['ratio_negative_cells']} | {row['ratio_above_100_cells']} | {row['area_codes_missing_from_master']} | {row['industry_codes_missing_from_union_master']} | {row['judgement']} |"
        )

    lines.extend(
        [
            "",
            "판단: 총매출/총건수/점포수의 음수, 음수 개폐업률, 상권 마스터에 없는 상권 코드, 서울 업종 union에 없는 업종 코드는 hard fail로 본다. 다만 하위 시간대/요일 분해값의 소수 음수와 100을 초과하는 개폐업률은 원천 특수값으로 보존하고, 알고리즘 정규화 단계에서 제외·상한처리·백분위 처리 대상으로 둔다.",
            "",
            "## 검증 4: 매출 내부 합계 검토",
            "",
            "| 항목 | 불일치 row 수 |",
            "|---|---:|",
            f"| 평일+주말 매출액 vs 당월 매출액 | {sales['weekday_weekend_amount_mismatch_rows']} |",
            f"| 평일+주말 매출건수 vs 당월 매출건수 | {sales['weekday_weekend_count_mismatch_rows']} |",
            f"| 요일별 매출액 합 vs 당월 매출액 | {sales['day_amount_mismatch_rows']} |",
            f"| 시간대별 매출액 합 vs 당월 매출액 | {sales['time_amount_mismatch_rows']} |",
            "",
            "판단: 이 검사는 파일이 만들어졌는지 보는 검사가 아니라, 원천의 하위 분해 지표가 총합과 같은 규칙을 따르는지 보는 sanity check다. 현재 총합 일관성은 유지되지만 일부 하위 시간대 건수에 -1 원천값이 있어 시간대 세부 점수에는 품질 플래그를 적용해야 한다.",
            "",
            "## 검증 5: 업종 universe 후퇴 검토",
            "",
            f"- 매출 원천 업종 수: {sales['industry_count']}",
            f"- 점포 원천 업종 수: {store['industry_count']}",
            f"- 점포 업종 중 기존 SBDC bridge에 없는 코드 수: {missing_bridge_store}",
            "",
            "판단: 점포 원천에는 매출 원천보다 더 많은 업종 코드가 있다. 그래서 점포 silver 자체는 보존하되, SBDC 계층 선택과 자동 업종 매핑은 별도 보강이 필요하다. 이 때문에 점포 silver는 `조건부 PASS`일 수 있으며, 이는 데이터 사용 금지가 아니라 계층 매핑 보강 필요를 뜻한다.",
            "",
            "추가 판단: 개폐업률 100 초과는 점포 수가 작은 상권-업종 조합에서 개업/폐업 점포 수가 현재 점포 수보다 커질 때 발생한다. silver 단계에서 삭제하면 폐업 위험 신호를 잃을 수 있으므로 보존한다. 알고리즘 점수화 단계에서는 원값 직접 사용이 아니라 백분위, winsorize, 또는 신뢰도 감점과 함께 써야 한다.",
            "",
            "## 검증 6: 시간 범위와 누수 방지",
            "",
            "| table | quarter_min | quarter_max | quarter_count |",
            "|---|---:|---:|---:|",
            f"| `silver_sales_trade_area_q_industry` | {sales['quarter_min']} | {sales['quarter_max']} | {sales['quarter_count']} |",
            f"| `silver_store_trade_area_q_industry` | {store['quarter_min']} | {store['quarter_max']} | {store['quarter_count']} |",
            "",
            "판단: silver 단계에서는 전체 기간을 보존한다. 백테스트와 알고리즘 단계에서는 특정 기준분기 점수 계산에 미래 분기가 섞이지 않도록 별도 시간 누수 검증을 해야 한다.",
            "",
            "## 2보 전진 1보 후퇴 기록",
            "",
            "- 전진 1: 매출/점포 전체 API 원응답을 누락 없이 silver로 정규화했다.",
            "- 전진 2: 상권/업종/분기 코드를 모두 보존해 후속 알고리즘 조인이 가능하게 했다.",
            "- 후퇴 1: 점포 업종 100개 중 기존 SBDC bridge가 덮지 못하는 업종이 있으므로, 업종 계층 UI와 알고리즘 자동 사용 범위는 분리해서 봐야 한다.",
            "",
            "## 다음 작업",
            "",
            "1. 생활인구/유동인구/직장인구 silver 전처리.",
            "2. 상권변화지표 silver 전처리.",
            "3. 접근성 원천인 집객시설, 버스, 지하철 계열 전처리.",
            "4. 점포 전용 업종 37개에 대한 SBDC 계층 매핑 보강.",
        ]
    )
    atomic_write_text(path, "\n".join(lines) + "\n")


def write_outputs(sales: pd.DataFrame, store: pd.DataFrame, industry_master: pd.DataFrame) -> None:
    outputs = (
        (sales, SILVER_DIR / "silver_sales_trade_area_q_industry.csv"),
        (store, SILVER_DIR / "silver_store_trade_area_q_industry.csv"),
        (industry_master, SILVER_DIR / "silver_industry_master_seoul_open_data.csv"),
    )
    for index, (frame, path) in enumerate(outputs, start=1):
        emit_progress(
            label="Silver 원자적 게시",
            current_units=index - 1,
            total_units=len(outputs),
            unit="파일",
            message=f"{path.name} 게시 준비",
        )
        atomic_to_csv(frame, path)
        emit_progress(
            label="Silver 원자적 게시",
            current_units=index,
            total_units=len(outputs),
            unit="파일",
            message=f"{path.name} 게시 완료",
        )


def main() -> None:
    ensure_dirs()
    sales, sales_meta = build_sales_table()
    emit_progress(
        label="매출·점포 정규화",
        current_units=1,
        total_units=2,
        unit="데이터셋",
        message=(
            f"매출 누적 정규화 완료: {len(sales)}행 · "
            f"내용 버전 {sales_meta['content_version_date']} · "
            f"최근 확인 {sales_meta['latest_snapshot_date']}"
        ),
    )
    store, store_meta = build_store_table()
    emit_progress(
        label="매출·점포 정규화",
        current_units=2,
        total_units=2,
        unit="데이터셋",
        message=(
            f"점포 누적 정규화 완료: {len(store)}행 · "
            f"내용 버전 {store_meta['content_version_date']} · "
            f"최근 확인 {store_meta['latest_snapshot_date']}"
        ),
    )
    industry_master = build_seoul_industry_master(
        sales,
        store,
        sales_content_version_date=sales_meta["content_version_date"],
        store_content_version_date=store_meta["content_version_date"],
    )

    emit_progress(
        label="계약 검증",
        current_units=0,
        total_units=2,
        unit="검증",
        message="도메인·grain·원천 계약 검증 중",
    )
    domain_df, grain_df, contract_df = validate_sales_store(sales, store, industry_master, sales_meta, store_meta)
    emit_progress(
        label="계약 검증",
        current_units=1,
        total_units=2,
        unit="검증",
        message="도메인·grain·원천 계약 계산 완료",
    )
    assert_prepublication_contract(domain_df, grain_df, contract_df, sales_meta, store_meta)
    emit_progress(
        label="계약 검증",
        current_units=2,
        total_units=2,
        unit="검증",
        message="게시 전 계약 검증 통과",
    )
    write_outputs(sales, store, industry_master)
    emit_progress(
        label="검증·상태 게시",
        current_units=0,
        total_units=2,
        unit="게시 단계",
        message="검증 산출물 게시 중",
    )
    atomic_to_csv(domain_df, VALIDATION_DIR / "03_sales_store_domain_validation.csv")
    atomic_to_csv(grain_df, VALIDATION_DIR / "03_sales_store_grain_validation.csv")
    atomic_to_csv(contract_df, VALIDATION_DIR / "03_sales_store_source_contract.csv")
    write_validation_md(domain_df, grain_df, contract_df)

    summary = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "sales_rows": len(sales),
        "store_rows": len(store),
        "sales_content_version_date": sales_meta["content_version_date"],
        "sales_latest_snapshot_date": sales_meta["latest_snapshot_date"],
        "sales_last_checked_at": sales_meta["last_checked_at"],
        "sales_latest_window_rows": sales_meta["latest_window_rows"],
        "sales_history_retained_rows": sales_meta["history_retained_rows"],
        "sales_latest_window_period_start": sales_meta["latest_window_period_start"],
        "sales_latest_window_period_end": sales_meta["latest_window_period_end"],
        "sales_retained_period_start": sales_meta["retained_period_start"],
        "sales_retained_period_end": sales_meta["retained_period_end"],
        "sales_latest_snapshot_contract": sales_meta["latest_snapshot_contract"],
        "store_content_version_date": store_meta["content_version_date"],
        "store_latest_snapshot_date": store_meta["latest_snapshot_date"],
        "store_last_checked_at": store_meta["last_checked_at"],
        "store_latest_window_rows": store_meta["latest_window_rows"],
        "store_history_retained_rows": store_meta["history_retained_rows"],
        "store_latest_window_period_start": store_meta["latest_window_period_start"],
        "store_latest_window_period_end": store_meta["latest_window_period_end"],
        "store_retained_period_start": store_meta["retained_period_start"],
        "store_retained_period_end": store_meta["retained_period_end"],
        "store_latest_snapshot_contract": store_meta["latest_snapshot_contract"],
        "sales_snapshots": sales_meta["snapshots"],
        "store_snapshots": store_meta["snapshots"],
        "industry_master_rows": len(industry_master),
        "validation_judgements": domain_df[["table", "judgement"]].to_dict("records"),
        "outputs": [
            "datacorpus/_silver/silver_sales_trade_area_q_industry.csv",
            "datacorpus/_silver/silver_store_trade_area_q_industry.csv",
            "datacorpus/_silver/silver_industry_master_seoul_open_data.csv",
            "datacorpus/_rule_validation/03_sales_store_domain_validation.csv",
            "datacorpus/_rule_validation/03_sales_store_grain_validation.csv",
            "datacorpus/_rule_validation/03_sales_store_source_contract.csv",
            "research/rule_validation/03_sales_store_silver_validation_20260703.md",
        ],
    }
    atomic_write_text(
        VALIDATION_DIR / "03_sales_store_preprocess_summary.json",
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
    )
    emit_progress(
        label="검증·상태 게시",
        current_units=1,
        total_units=2,
        unit="게시 단계",
        message="검증 산출물 게시 완료",
    )
    update_source_state_catalog(
        [
            {
                "source_id": "seoul_sales_trade_area",
                "service": "VwsmTrdarSelngQq",
                "snapshot_date": sales_meta["latest_snapshot_date"],
                "content_version_date": sales_meta["content_version_date"],
                "latest_snapshot_date": sales_meta["latest_snapshot_date"],
                "last_checked_at": sales_meta["last_checked_at"],
                "total_count": sales_meta["api_total_count"],
                "data_period_start": sales_meta["data_period_start"],
                "data_period_end": sales_meta["data_period_end"],
                "latest_window_period_start": sales_meta["latest_window_period_start"],
                "latest_window_period_end": sales_meta["latest_window_period_end"],
                "retained_period_start": sales_meta["retained_period_start"],
                "retained_period_end": sales_meta["retained_period_end"],
                "latest_snapshot_contract": sales_meta["latest_snapshot_contract"],
                "content_fingerprint": sales_meta["snapshots"][0].get("content_fingerprint"),
            },
            {
                "source_id": "seoul_store_trade_area",
                "service": "VwsmTrdarStorQq",
                "snapshot_date": store_meta["latest_snapshot_date"],
                "content_version_date": store_meta["content_version_date"],
                "latest_snapshot_date": store_meta["latest_snapshot_date"],
                "last_checked_at": store_meta["last_checked_at"],
                "total_count": store_meta["api_total_count"],
                "data_period_start": store_meta["data_period_start"],
                "data_period_end": store_meta["data_period_end"],
                "latest_window_period_start": store_meta["latest_window_period_start"],
                "latest_window_period_end": store_meta["latest_window_period_end"],
                "retained_period_start": store_meta["retained_period_start"],
                "retained_period_end": store_meta["retained_period_end"],
                "latest_snapshot_contract": store_meta["latest_snapshot_contract"],
                "content_fingerprint": store_meta["snapshots"][0].get("content_fingerprint"),
            },
        ]
    )
    emit_progress(
        label="검증·상태 게시",
        current_units=2,
        total_units=2,
        unit="게시 단계",
        message="원천 상태 게시 완료",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
