from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from ingest_common import latest_raw_path, raw_run_date, raw_snapshot_date


ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "datacorpus" / "_raw_ingest"
SILVER_DIR = ROOT / "datacorpus" / "_silver"
DATA_VALIDATION_DIR = ROOT / "datacorpus" / "_rule_validation"
RESEARCH_VALIDATION_DIR = ROOT / "research" / "rule_validation"

TRADE_AREA_RAW_DIR = latest_raw_path(
    "seoul_open_data", "full", "TbgisTrdarRelm", required_glob="TbgisTrdarRelm_*.json"
)
TRADE_AREA_SHP_DIR = ROOT / "datacorpus" / "_unzipped" / "서울시 상권분석서비스(영역-상권)"
SBDC_HIERARCHY_CSV = ROOT / "datacorpus" / "_final" / "spatial_od" / "SBDC_업종분류표_247.csv"
SEOUL_SBDC_BRIDGE_CSV = ROOT / "datacorpus" / "_final" / "spatial_od" / "업종코드_서울_SBDC_매핑검증.csv"

RAW_RUN_DATE = raw_run_date(TRADE_AREA_RAW_DIR)
RAW_SNAPSHOT_DATE = raw_snapshot_date(TRADE_AREA_RAW_DIR)
REFERENCE_SNAPSHOT_DATE = "2026-07-03"
RAW_BOUNDARY_VERSION = f"seoul_open_data_{RAW_RUN_DATE}_TbgisTrdarRelm"
REFERENCE_BOUNDARY_VERSION = "seoul_open_data_20260703_TbgisTrdarRelm"


def ensure_dirs() -> None:
    for path in [SILVER_DIR, DATA_VALIDATION_DIR, RESEARCH_VALIDATION_DIR]:
        path.mkdir(parents=True, exist_ok=True)


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def write_csv(df: pd.DataFrame, path: Path) -> None:
    # 엑셀 확인까지 고려해 utf-8-sig로 저장한다.
    df.to_csv(path, index=False, encoding="utf-8-sig")


def load_trade_area_rows() -> tuple[pd.DataFrame, list[Path], int | None]:
    rows: list[dict[str, Any]] = []
    page_paths = sorted(TRADE_AREA_RAW_DIR.glob("TbgisTrdarRelm_*.json"))
    list_total_count: int | None = None
    for path in page_paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        root = payload.get("TbgisTrdarRelm", {})
        list_total_count = int(root.get("list_total_count", list_total_count or 0))
        for row in root.get("row", []):
            item = dict(row)
            item["_raw_path"] = str(path.relative_to(ROOT))
            rows.append(item)
    return pd.DataFrame(rows), page_paths, list_total_count


def build_trade_area_master() -> pd.DataFrame:
    raw, page_paths, list_total_count = load_trade_area_rows()
    if raw.empty:
        raise RuntimeError("상권 영역 OpenAPI 원응답에서 행을 읽지 못했습니다.")

    df = pd.DataFrame(
        {
            "상권_코드": raw["TRDAR_CD"].astype(str),
            "상권_코드_명": raw["TRDAR_CD_NM"].astype(str),
            "상권_구분_코드": raw["TRDAR_SE_CD"].astype(str),
            "상권_구분_코드_명": raw["TRDAR_SE_CD_NM"].astype(str),
            "자치구_코드": raw["SIGNGU_CD"].astype(str),
            "자치구_코드_명": raw["SIGNGU_CD_NM"].astype(str),
            "행정동_코드": raw["ADSTRD_CD"].astype(str),
            "행정동_코드_명": raw["ADSTRD_CD_NM"].astype(str),
            "중심_X": pd.to_numeric(raw["XCNTS_VALUE"], errors="coerce"),
            "중심_Y": pd.to_numeric(raw["YDNTS_VALUE"], errors="coerce"),
            "면적_제곱미터": pd.to_numeric(raw["RELM_AR"], errors="coerce"),
            "source_id": "seoul_trade_area_boundary",
            "provider": "서울열린데이터광장",
            "source_service": "TbgisTrdarRelm",
            "snapshot_date": RAW_SNAPSHOT_DATE,
            "boundary_version": RAW_BOUNDARY_VERSION,
            # 숫자 EPSG는 공간 라이브러리로 재확인 전까지 단정하지 않는다.
            # SHP의 .prj 원문은 silver_trade_area_boundary_manifest에 보존한다.
            "source_crs_recorded": "Korea_2000_Korea_Central_Belt_PRJ",
            "raw_page_count": len(page_paths),
            "api_list_total_count": list_total_count,
            "geometry_source_status": "SHP 원본은 boundary_manifest로 별도 보존",
            "notes_ko": "상권명은 표시용이고 조인은 상권_코드를 사용한다.",
        }
    )
    return df.sort_values("상권_코드").reset_index(drop=True)


def build_boundary_manifest() -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    prj_text = ""
    prj_path = next(TRADE_AREA_SHP_DIR.glob("*.prj"), None)
    if prj_path and prj_path.exists():
        prj_text = prj_path.read_text(encoding="utf-8", errors="ignore").strip()

    for path in sorted(TRADE_AREA_SHP_DIR.glob("*")):
        if not path.is_file():
            continue
        rows.append(
            {
                "source_id": "seoul_trade_area_boundary",
                "provider": "서울열린데이터광장",
                "dataset_name": "서울시 상권분석서비스(영역-상권) SHP",
                "boundary_version": REFERENCE_BOUNDARY_VERSION,
                "component_ext": path.suffix.lower().lstrip("."),
                "relative_path": str(path.relative_to(ROOT)),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
                "snapshot_date": REFERENCE_SNAPSHOT_DATE,
                "geometry_parse_status": "원본 보존 완료, point-in-polygon 파싱은 공간 라이브러리 확정 후 수행",
                "source_prj": prj_text if path.suffix.lower() == ".prj" else "",
                "notes_ko": "polygon 원본은 삭제하지 않고 해시로 고정한다.",
            }
        )
    return pd.DataFrame(rows)


def build_industry_hierarchy() -> pd.DataFrame:
    df = pd.read_csv(SBDC_HIERARCHY_CSV, encoding="utf-8-sig", dtype=str).fillna("")
    out = df.copy()
    out["source_id"] = "sbdc_store_info"
    out["provider"] = "소상공인시장진흥공단"
    out["snapshot_date"] = REFERENCE_SNAPSHOT_DATE
    out["usage_role"] = "업종 대/중/소 계층 선택과 서울 서비스업종 매핑 보조"
    out["notes_ko"] = "서울 상권분석 서비스업종 코드의 상위 UI 계층을 만들 때 사용한다."
    return out.sort_values(["대분류코드", "중분류코드", "소분류코드"]).reset_index(drop=True)


def build_industry_bridge() -> pd.DataFrame:
    df = pd.read_csv(SEOUL_SBDC_BRIDGE_CSV, encoding="utf-8-sig", dtype=str).fillna("")
    out = df.copy()
    out["source_id"] = "seoul_sales_trade_area;sbdc_store_info"
    out["provider"] = "서울열린데이터광장;소상공인시장진흥공단"
    out["snapshot_date"] = REFERENCE_SNAPSHOT_DATE
    out["mapping_review_required"] = out["업종매핑_검토상태"].ne("자동매칭_강함")
    out["score_use_status"] = out["mapping_review_required"].map({True: "보류_수동검토필요", False: "사용가능_자동강매칭"})
    out["notes_ko"] = "서울 서비스업종 코드는 점수 주키이고 SBDC 계층은 UI/반경경쟁 보조 매핑이다."
    return out.sort_values("서비스_업종_코드").reset_index(drop=True)


def null_count(df: pd.DataFrame, col: str) -> int:
    if col not in df.columns:
        return len(df)
    return int(df[col].isna().sum() + (df[col].astype(str).str.len() == 0).sum())


def duplicate_count(df: pd.DataFrame, key_cols: list[str]) -> int:
    missing = [col for col in key_cols if col not in df.columns]
    if missing:
        return len(df)
    return int(df.duplicated(key_cols).sum())


def build_grain_review(outputs: dict[str, tuple[pd.DataFrame, list[str], str]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for table, (df, key_cols, reason) in outputs.items():
        dup = duplicate_count(df, key_cols)
        key_nulls = sum(null_count(df, col) for col in key_cols)
        rows.append(
            {
                "table": table,
                "rows": len(df),
                "key_cols": " + ".join(key_cols),
                "duplicate_key_rows": dup,
                "key_null_cells": key_nulls,
                "judgement": "PASS" if dup == 0 and key_nulls == 0 else "FAIL",
                "reason_ko": reason,
            }
        )
    return pd.DataFrame(rows)


def build_source_contract(outputs: dict[str, tuple[pd.DataFrame, list[str], str]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for table, (df, key_cols, reason) in outputs.items():
        source_values = []
        if "source_id" in df.columns:
            source_values = sorted(set(str(v) for v in df["source_id"].dropna().unique()))
        rows.append(
            {
                "table": table,
                "rows": len(df),
                "source_id": ";".join(source_values),
                "key_cols": " + ".join(key_cols),
                "usage_role": reason,
                "contract_status": "PASS" if source_values else "FAIL",
            }
        )
    return pd.DataFrame(rows)


def write_source_contract_md(contract_df: pd.DataFrame, grain_df: pd.DataFrame) -> None:
    path = RESEARCH_VALIDATION_DIR / "01_source_rule_contract.md"
    lines = [
        "# 1회차 원천-규칙 계약 검증",
        "",
        f"작성시각: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "## 검증 대상",
        "",
        "- `silver_trade_area_master`",
        "- `silver_trade_area_boundary_manifest`",
        "- `silver_industry_hierarchy_sbdc`",
        "- `silver_industry_bridge_seoul_sbdc`",
        "",
        "## 원천 계약 결과",
        "",
        "| table | rows | source_id | contract_status |",
        "|---|---:|---|---|",
    ]
    for row in contract_df.to_dict("records"):
        lines.append(f"| `{row['table']}` | {row['rows']} | `{row['source_id']}` | {row['contract_status']} |")
    lines.extend(
        [
            "",
            "## grain 검증 요약",
            "",
            "| table | key_cols | duplicate_key_rows | key_null_cells | judgement |",
            "|---|---|---:|---:|---|",
        ]
    )
    for row in grain_df.to_dict("records"):
        lines.append(
            f"| `{row['table']}` | `{row['key_cols']}` | {row['duplicate_key_rows']} | {row['key_null_cells']} | {row['judgement']} |"
        )
    lines.extend(
        [
            "",
            "## 판단",
            "",
            "- 첫 seed silver 4개는 원천 source_id와 조인키를 보존했다.",
            "- 상권 polygon 자체의 point-in-polygon 파싱은 아직 수행하지 않았고, SHP 원본 묶음을 해시 manifest로 고정했다.",
            "- 업종 bridge의 수동매핑필요 행은 알고리즘 점수 입력 전 별도 검토가 필요하다.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    ensure_dirs()

    outputs: dict[str, tuple[pd.DataFrame, list[str], str]] = {}
    outputs["silver_trade_area_master"] = (
        build_trade_area_master(),
        ["상권_코드"],
        "상권코드 기반 위치 선택·공간조인 기준",
    )
    outputs["silver_trade_area_boundary_manifest"] = (
        build_boundary_manifest(),
        ["relative_path"],
        "상권 polygon 원본 파일 보존과 해시 고정",
    )
    outputs["silver_industry_hierarchy_sbdc"] = (
        build_industry_hierarchy(),
        ["소분류코드"],
        "업종 대/중/소 드릴다운 UI 기준",
    )
    outputs["silver_industry_bridge_seoul_sbdc"] = (
        build_industry_bridge(),
        ["서비스_업종_코드"],
        "서울 서비스업종과 SBDC 업종계층 연결",
    )

    for table, (df, _, _) in outputs.items():
        write_csv(df, SILVER_DIR / f"{table}.csv")

    grain_df = build_grain_review(outputs)
    contract_df = build_source_contract(outputs)
    write_csv(grain_df, DATA_VALIDATION_DIR / "02_grain_join_key_seed.csv")
    write_csv(contract_df, DATA_VALIDATION_DIR / "01_source_rule_contract_seed.csv")
    write_csv(grain_df, RESEARCH_VALIDATION_DIR / "02_grain_join_key_review.csv")
    write_source_contract_md(contract_df, grain_df)

    summary = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "silver_dir": str(SILVER_DIR.relative_to(ROOT)),
        "tables": {table: {"rows": len(df), "key_cols": key_cols} for table, (df, key_cols, _) in outputs.items()},
        "grain_review_path": str((DATA_VALIDATION_DIR / "02_grain_join_key_seed.csv").relative_to(ROOT)),
        "source_contract_path": str((DATA_VALIDATION_DIR / "01_source_rule_contract_seed.csv").relative_to(ROOT)),
    }
    (DATA_VALIDATION_DIR / "seed_preprocess_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
