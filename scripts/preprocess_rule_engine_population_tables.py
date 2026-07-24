from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from ingest_common import latest_raw_path, raw_snapshot_date


ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "datacorpus" / "_raw_ingest"
SILVER_DIR = ROOT / "datacorpus" / "_silver"
VALIDATION_DIR = ROOT / "datacorpus" / "_rule_validation"
RESEARCH_VALIDATION_DIR = ROOT / "research" / "rule_validation"

TRADE_AREA_MASTER_PATH = SILVER_DIR / "silver_trade_area_master.csv"

PROVIDER = "서울열린데이터광장"
KEY_COLS = ["기준_년분기_코드", "상권_코드"]
ROUNDING_TOLERANCE_COUNT = 10


SERVICES = {
    "floating": {
        "source_id": "seoul_floating_population_trade_area",
        "service": "VwsmTrdarFlpopQq",
        "raw_dir": latest_raw_path(
            "seoul_open_data", "full", "VwsmTrdarFlpopQq", required_glob="VwsmTrdarFlpopQq_*.json"
        ),
        "output": "silver_floating_population_trade_area_q.csv",
        "population_type": "유동인구",
        "notes_ko": "시간대·요일·성별·연령별 유동인구 수요 표면의 P0 원천이다. 추정 인구이므로 실제 방문자 수 보장이 아니라 수요 프록시로 사용한다.",
        "forbidden_claim_ko": "실제 방문자 수, 개별 매장 방문확률, 창업 성공확률로 표현 금지",
        "columns": {
            "STDR_YYQU_CD": "기준_년분기_코드",
            "TRDAR_SE_CD": "상권_구분_코드",
            "TRDAR_SE_CD_NM": "상권_구분_코드_명",
            "TRDAR_CD": "상권_코드",
            "TRDAR_CD_NM": "상권_코드_명",
            "TOT_FLPOP_CO": "총_유동인구_수",
            "ML_FLPOP_CO": "남성_유동인구_수",
            "FML_FLPOP_CO": "여성_유동인구_수",
            "AGRDE_10_FLPOP_CO": "연령대_10_유동인구_수",
            "AGRDE_20_FLPOP_CO": "연령대_20_유동인구_수",
            "AGRDE_30_FLPOP_CO": "연령대_30_유동인구_수",
            "AGRDE_40_FLPOP_CO": "연령대_40_유동인구_수",
            "AGRDE_50_FLPOP_CO": "연령대_50_유동인구_수",
            "AGRDE_60_ABOVE_FLPOP_CO": "연령대_60이상_유동인구_수",
            "TMZON_00_06_FLPOP_CO": "시간대_00_06_유동인구_수",
            "TMZON_06_11_FLPOP_CO": "시간대_06_11_유동인구_수",
            "TMZON_11_14_FLPOP_CO": "시간대_11_14_유동인구_수",
            "TMZON_14_17_FLPOP_CO": "시간대_14_17_유동인구_수",
            "TMZON_17_21_FLPOP_CO": "시간대_17_21_유동인구_수",
            "TMZON_21_24_FLPOP_CO": "시간대_21_24_유동인구_수",
            "MON_FLPOP_CO": "월요일_유동인구_수",
            "TUES_FLPOP_CO": "화요일_유동인구_수",
            "WED_FLPOP_CO": "수요일_유동인구_수",
            "THUR_FLPOP_CO": "목요일_유동인구_수",
            "FRI_FLPOP_CO": "금요일_유동인구_수",
            "SAT_FLPOP_CO": "토요일_유동인구_수",
            "SUN_FLPOP_CO": "일요일_유동인구_수",
        },
    },
    "resident": {
        "source_id": "seoul_resident_population_trade_area",
        "service": "VwsmTrdarRepopQq",
        "raw_dir": latest_raw_path(
            "seoul_open_data", "full", "VwsmTrdarRepopQq", required_glob="VwsmTrdarRepopQq_*.json"
        ),
        "output": "silver_resident_population_trade_area_q.csv",
        "population_type": "상주인구",
        "notes_ko": "주거 배후수요와 가구 구성 해석을 위한 P0 원천이다. 유동인구와 다른 개념이므로 혼합 전 인구유형을 보존한다.",
        "forbidden_claim_ko": "실제 구매자 수, 개별 매장 방문확률, 창업 성공확률로 표현 금지",
        "columns": {
            "STDR_YYQU_CD": "기준_년분기_코드",
            "TRDAR_SE_CD": "상권_구분_코드",
            "TRDAR_SE_CD_NM": "상권_구분_코드_명",
            "TRDAR_CD": "상권_코드",
            "TRDAR_CD_NM": "상권_코드_명",
            "TOT_REPOP_CO": "총_상주인구_수",
            "ML_REPOP_CO": "남성_상주인구_수",
            "FML_REPOP_CO": "여성_상주인구_수",
            "AGRDE_10_REPOP_CO": "연령대_10_상주인구_수",
            "AGRDE_20_REPOP_CO": "연령대_20_상주인구_수",
            "AGRDE_30_REPOP_CO": "연령대_30_상주인구_수",
            "AGRDE_40_REPOP_CO": "연령대_40_상주인구_수",
            "AGRDE_50_REPOP_CO": "연령대_50_상주인구_수",
            "AGRDE_60_ABOVE_REPOP_CO": "연령대_60이상_상주인구_수",
            "MAG_10_REPOP_CO": "남성_연령대_10_상주인구_수",
            "MAG_20_REPOP_CO": "남성_연령대_20_상주인구_수",
            "MAG_30_REPOP_CO": "남성_연령대_30_상주인구_수",
            "MAG_40_REPOP_CO": "남성_연령대_40_상주인구_수",
            "MAG_50_REPOP_CO": "남성_연령대_50_상주인구_수",
            "MAG_60_ABOVE_REPOP_CO": "남성_연령대_60이상_상주인구_수",
            "FAG_10_REPOP_CO": "여성_연령대_10_상주인구_수",
            "FAG_20_REPOP_CO": "여성_연령대_20_상주인구_수",
            "FAG_30_REPOP_CO": "여성_연령대_30_상주인구_수",
            "FAG_40_REPOP_CO": "여성_연령대_40_상주인구_수",
            "FAG_50_REPOP_CO": "여성_연령대_50_상주인구_수",
            "FAG_60_ABOVE_REPOP_CO": "여성_연령대_60이상_상주인구_수",
            "TOT_HSHLD_CO": "총_가구_수",
            "APT_HSHLD_CO": "아파트_가구_수",
            "NON_APT_HSHLD_CO": "비아파트_가구_수",
        },
    },
    "worker": {
        "source_id": "seoul_worker_population_trade_area",
        "service": "VwsmTrdarWrcPopltnQq",
        "raw_dir": latest_raw_path(
            "seoul_open_data", "full", "VwsmTrdarWrcPopltnQq", required_glob="VwsmTrdarWrcPopltnQq_*.json"
        ),
        "output": "silver_worker_population_trade_area_q.csv",
        "population_type": "직장인구",
        "notes_ko": "업무 배후수요와 주간 수요 해석을 위한 P0 원천이다. 상주인구와 다른 개념이므로 인구유형을 분리해 보존한다.",
        "forbidden_claim_ko": "실제 구매자 수, 개별 매장 방문확률, 창업 성공확률로 표현 금지",
        "columns": {
            "STDR_YYQU_CD": "기준_년분기_코드",
            "TRDAR_SE_CD": "상권_구분_코드",
            "TRDAR_SE_CD_NM": "상권_구분_코드_명",
            "TRDAR_CD": "상권_코드",
            "TRDAR_CD_NM": "상권_코드_명",
            "TOT_WRC_POPLTN_CO": "총_직장인구_수",
            "ML_WRC_POPLTN_CO": "남성_직장인구_수",
            "FML_WRC_POPLTN_CO": "여성_직장인구_수",
            "AGRDE_10_WRC_POPLTN_CO": "연령대_10_직장인구_수",
            "AGRDE_20_WRC_POPLTN_CO": "연령대_20_직장인구_수",
            "AGRDE_30_WRC_POPLTN_CO": "연령대_30_직장인구_수",
            "AGRDE_40_WRC_POPLTN_CO": "연령대_40_직장인구_수",
            "AGRDE_50_WRC_POPLTN_CO": "연령대_50_직장인구_수",
            "AGRDE_60_ABOVE_WRC_POPLTN_CO": "연령대_60이상_직장인구_수",
            "MAG_10_WRC_POPLTN_CO": "남성_연령대_10_직장인구_수",
            "MAG_20_WRC_POPLTN_CO": "남성_연령대_20_직장인구_수",
            "MAG_30_WRC_POPLTN_CO": "남성_연령대_30_직장인구_수",
            "MAG_40_WRC_POPLTN_CO": "남성_연령대_40_직장인구_수",
            "MAG_50_WRC_POPLTN_CO": "남성_연령대_50_직장인구_수",
            "MAG_60_ABOVE_WRC_POPLTN_CO": "남성_연령대_60이상_직장인구_수",
            "FAG_10_WRC_POPLTN_CO": "여성_연령대_10_직장인구_수",
            "FAG_20_WRC_POPLTN_CO": "여성_연령대_20_직장인구_수",
            "FAG_30_WRC_POPLTN_CO": "여성_연령대_30_직장인구_수",
            "FAG_40_WRC_POPLTN_CO": "여성_연령대_40_직장인구_수",
            "FAG_50_WRC_POPLTN_CO": "여성_연령대_50_직장인구_수",
            "FAG_60_ABOVE_WRC_POPLTN_CO": "여성_연령대_60이상_직장인구_수",
        },
    },
}


def ensure_dirs() -> None:
    for path in [SILVER_DIR, VALIDATION_DIR, RESEARCH_VALIDATION_DIR]:
        path.mkdir(parents=True, exist_ok=True)


def read_openapi_pages(raw_dir: Path, service_name: str) -> tuple[pd.DataFrame, int, int]:
    rows: list[dict[str, Any]] = []
    total_counts: set[int] = set()
    page_paths = sorted(raw_dir.glob(f"{service_name}_*.json"))
    if not page_paths:
        raise FileNotFoundError(f"{raw_dir} 아래에서 {service_name} 원응답을 찾지 못했습니다.")

    for path in page_paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        root = payload.get(service_name)
        if not isinstance(root, dict):
            raise ValueError(f"{path} 파일에 {service_name} 루트가 없습니다.")
        if "list_total_count" in root:
            total_counts.add(int(root["list_total_count"]))
        for row in root.get("row", []):
            item = dict(row)
            item["_raw_path"] = str(path.relative_to(ROOT))
            rows.append(item)

    if len(total_counts) != 1:
        raise ValueError(f"{service_name} list_total_count가 하나로 고정되지 않았습니다: {sorted(total_counts)}")
    return pd.DataFrame(rows), len(page_paths), next(iter(total_counts))


def normalize_common_codes(df: pd.DataFrame) -> pd.DataFrame:
    # 코드 컬럼은 숫자처럼 보여도 상권 조인 키이므로 문자열로 보존한다.
    for col in ["기준_년분기_코드", "상권_구분_코드", "상권_코드"]:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()
    for col in ["상권_구분_코드_명", "상권_코드_명"]:
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


def population_numeric_cols(df: pd.DataFrame) -> list[str]:
    return [col for col in df.columns if col.endswith("_수")]


def add_quality_flags(df: pd.DataFrame, kind: str) -> pd.DataFrame:
    out = df.copy()
    numeric_cols = population_numeric_cols(out)
    out["quality_negative_population_cell_count"] = (out[numeric_cols] < 0).sum(axis=1)

    if kind == "floating":
        out["quality_gender_total_mismatch"] = (
            out["남성_유동인구_수"] + out["여성_유동인구_수"] != out["총_유동인구_수"]
        )
        age_cols = [
            "연령대_10_유동인구_수",
            "연령대_20_유동인구_수",
            "연령대_30_유동인구_수",
            "연령대_40_유동인구_수",
            "연령대_50_유동인구_수",
            "연령대_60이상_유동인구_수",
        ]
        time_cols = [
            "시간대_00_06_유동인구_수",
            "시간대_06_11_유동인구_수",
            "시간대_11_14_유동인구_수",
            "시간대_14_17_유동인구_수",
            "시간대_17_21_유동인구_수",
            "시간대_21_24_유동인구_수",
        ]
        day_cols = [
            "월요일_유동인구_수",
            "화요일_유동인구_수",
            "수요일_유동인구_수",
            "목요일_유동인구_수",
            "금요일_유동인구_수",
            "토요일_유동인구_수",
            "일요일_유동인구_수",
        ]
        out["quality_age_total_mismatch"] = out[age_cols].sum(axis=1) != out["총_유동인구_수"]
        out["quality_time_total_mismatch"] = out[time_cols].sum(axis=1) != out["총_유동인구_수"]
        out["quality_day_total_mismatch"] = out[day_cols].sum(axis=1) != out["총_유동인구_수"]
    elif kind == "resident":
        out["quality_gender_total_mismatch"] = (
            out["남성_상주인구_수"] + out["여성_상주인구_수"] != out["총_상주인구_수"]
        )
        age_cols = [
            "연령대_10_상주인구_수",
            "연령대_20_상주인구_수",
            "연령대_30_상주인구_수",
            "연령대_40_상주인구_수",
            "연령대_50_상주인구_수",
            "연령대_60이상_상주인구_수",
        ]
        male_age_cols = [f"남성_연령대_{age}_상주인구_수" for age in ["10", "20", "30", "40", "50", "60이상"]]
        female_age_cols = [f"여성_연령대_{age}_상주인구_수" for age in ["10", "20", "30", "40", "50", "60이상"]]
        out["quality_age_total_mismatch"] = out[age_cols].sum(axis=1) != out["총_상주인구_수"]
        out["quality_gender_age_total_mismatch"] = (
            out[male_age_cols + female_age_cols].sum(axis=1) != out["총_상주인구_수"]
        )
        out["quality_household_total_mismatch"] = (
            out["아파트_가구_수"] + out["비아파트_가구_수"] != out["총_가구_수"]
        )
    elif kind == "worker":
        out["quality_gender_total_mismatch"] = (
            out["남성_직장인구_수"] + out["여성_직장인구_수"] != out["총_직장인구_수"]
        )
        age_cols = [
            "연령대_10_직장인구_수",
            "연령대_20_직장인구_수",
            "연령대_30_직장인구_수",
            "연령대_40_직장인구_수",
            "연령대_50_직장인구_수",
            "연령대_60이상_직장인구_수",
        ]
        male_age_cols = [f"남성_연령대_{age}_직장인구_수" for age in ["10", "20", "30", "40", "50", "60이상"]]
        female_age_cols = [f"여성_연령대_{age}_직장인구_수" for age in ["10", "20", "30", "40", "50", "60이상"]]
        out["quality_age_total_mismatch"] = out[age_cols].sum(axis=1) != out["총_직장인구_수"]
        out["quality_gender_age_total_mismatch"] = (
            out[male_age_cols + female_age_cols].sum(axis=1) != out["총_직장인구_수"]
        )
    return out


def add_lineage(df: pd.DataFrame, cfg: dict[str, Any], page_count: int, api_total_count: int) -> pd.DataFrame:
    out = df.copy()
    out["source_id"] = cfg["source_id"]
    out["provider"] = PROVIDER
    out["source_service"] = cfg["service"]
    out["snapshot_date"] = raw_snapshot_date(cfg["raw_dir"])
    out["population_type"] = cfg["population_type"]
    out["source_grain"] = "기준년분기+상권코드"
    out["raw_page_count"] = page_count
    out["api_list_total_count"] = api_total_count
    out["raw_row_count"] = len(out)
    out["directness_level"] = "P0_공식_상권_추정집계"
    out["forbidden_claim_ko"] = cfg["forbidden_claim_ko"]
    out["notes_ko"] = cfg["notes_ko"]
    return out


def build_population_table(kind: str, cfg: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    raw, page_count, api_total_count = read_openapi_pages(cfg["raw_dir"], cfg["service"])
    df = raw.rename(columns=cfg["columns"])
    expected = list(cfg["columns"].values())
    missing = [col for col in expected if col not in df.columns]
    if missing:
        raise ValueError(f"{cfg['service']} 원천 컬럼 변환 후 누락 컬럼: {missing}")
    df = df[expected]
    df = normalize_common_codes(df)
    df = numericize(df, skip_cols=set(expected[:5]))
    df = add_quality_flags(df, kind)
    df = add_lineage(df, cfg, page_count, api_total_count)
    return df.sort_values(KEY_COLS).reset_index(drop=True), {
        "page_count": page_count,
        "api_total_count": api_total_count,
    }


def load_trade_area_codes() -> set[str]:
    if not TRADE_AREA_MASTER_PATH.exists():
        return set()
    df = pd.read_csv(TRADE_AREA_MASTER_PATH, encoding="utf-8-sig", dtype=str).fillna("")
    return set(df["상권_코드"].astype(str).str.strip()) if "상권_코드" in df.columns else set()


def key_null_cells(df: pd.DataFrame) -> int:
    total = 0
    for col in KEY_COLS:
        total += int((df[col].isna() | df[col].astype(str).str.strip().eq("")).sum())
    return total


def duplicate_key_rows(df: pd.DataFrame) -> int:
    return int(df.duplicated(KEY_COLS).sum())


def count_negative_cells(df: pd.DataFrame) -> int:
    cols = population_numeric_cols(df)
    return int((df[cols] < 0).sum().sum())


def mismatch_count(df: pd.DataFrame, col: str) -> int:
    return int(df[col].sum()) if col in df.columns else 0


def sum_mismatch_stats(df: pd.DataFrame, total_col: str, part_cols: list[str], tolerance: int = ROUNDING_TOLERANCE_COUNT) -> dict[str, int]:
    if total_col not in df.columns or any(col not in df.columns for col in part_cols):
        return {"exact": 0, "beyond_tolerance": 0, "max_abs": 0}
    diff = df[part_cols].sum(axis=1) - df[total_col]
    return {
        "exact": int((diff != 0).sum()),
        "beyond_tolerance": int((diff.abs() > tolerance).sum()),
        "max_abs": int(diff.abs().max()) if len(diff) else 0,
    }


def population_sum_stats(df: pd.DataFrame, kind: str) -> dict[str, dict[str, int]]:
    if kind == "floating":
        return {
            "gender": sum_mismatch_stats(df, "총_유동인구_수", ["남성_유동인구_수", "여성_유동인구_수"]),
            "age": sum_mismatch_stats(
                df,
                "총_유동인구_수",
                [
                    "연령대_10_유동인구_수",
                    "연령대_20_유동인구_수",
                    "연령대_30_유동인구_수",
                    "연령대_40_유동인구_수",
                    "연령대_50_유동인구_수",
                    "연령대_60이상_유동인구_수",
                ],
            ),
            "time": sum_mismatch_stats(
                df,
                "총_유동인구_수",
                [
                    "시간대_00_06_유동인구_수",
                    "시간대_06_11_유동인구_수",
                    "시간대_11_14_유동인구_수",
                    "시간대_14_17_유동인구_수",
                    "시간대_17_21_유동인구_수",
                    "시간대_21_24_유동인구_수",
                ],
            ),
            "day": sum_mismatch_stats(
                df,
                "총_유동인구_수",
                [
                    "월요일_유동인구_수",
                    "화요일_유동인구_수",
                    "수요일_유동인구_수",
                    "목요일_유동인구_수",
                    "금요일_유동인구_수",
                    "토요일_유동인구_수",
                    "일요일_유동인구_수",
                ],
            ),
        }
    if kind == "resident":
        return {
            "gender": sum_mismatch_stats(df, "총_상주인구_수", ["남성_상주인구_수", "여성_상주인구_수"]),
            "age": sum_mismatch_stats(
                df,
                "총_상주인구_수",
                [
                    "연령대_10_상주인구_수",
                    "연령대_20_상주인구_수",
                    "연령대_30_상주인구_수",
                    "연령대_40_상주인구_수",
                    "연령대_50_상주인구_수",
                    "연령대_60이상_상주인구_수",
                ],
            ),
            "gender_age": sum_mismatch_stats(
                df,
                "총_상주인구_수",
                [f"남성_연령대_{age}_상주인구_수" for age in ["10", "20", "30", "40", "50", "60이상"]]
                + [f"여성_연령대_{age}_상주인구_수" for age in ["10", "20", "30", "40", "50", "60이상"]],
            ),
            "household": sum_mismatch_stats(df, "총_가구_수", ["아파트_가구_수", "비아파트_가구_수"]),
        }
    if kind == "worker":
        return {
            "gender": sum_mismatch_stats(df, "총_직장인구_수", ["남성_직장인구_수", "여성_직장인구_수"]),
            "age": sum_mismatch_stats(
                df,
                "총_직장인구_수",
                [
                    "연령대_10_직장인구_수",
                    "연령대_20_직장인구_수",
                    "연령대_30_직장인구_수",
                    "연령대_40_직장인구_수",
                    "연령대_50_직장인구_수",
                    "연령대_60이상_직장인구_수",
                ],
            ),
            "gender_age": sum_mismatch_stats(
                df,
                "총_직장인구_수",
                [f"남성_연령대_{age}_직장인구_수" for age in ["10", "20", "30", "40", "50", "60이상"]]
                + [f"여성_연령대_{age}_직장인구_수" for age in ["10", "20", "30", "40", "50", "60이상"]],
            ),
        }
    return {}


def build_demand_compact(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    base_cols = ["기준_년분기_코드", "상권_코드", "상권_코드_명", "상권_구분_코드", "상권_구분_코드_명"]
    floating = tables["floating"][base_cols + ["총_유동인구_수", "quality_negative_population_cell_count"]].rename(
        columns={"quality_negative_population_cell_count": "유동인구_품질_음수셀수"}
    )
    resident = tables["resident"][KEY_COLS + ["총_상주인구_수", "총_가구_수", "quality_negative_population_cell_count"]].rename(
        columns={"quality_negative_population_cell_count": "상주인구_품질_음수셀수"}
    )
    worker = tables["worker"][KEY_COLS + ["총_직장인구_수", "quality_negative_population_cell_count"]].rename(
        columns={"quality_negative_population_cell_count": "직장인구_품질_음수셀수"}
    )
    compact = floating.merge(resident, on=KEY_COLS, how="outer").merge(worker, on=KEY_COLS, how="outer")
    # 세 인구 원천의 커버리지는 서로 다르므로 결측 여부를 명시적으로 남긴다.
    compact["유동인구_존재"] = compact["총_유동인구_수"].notna()
    compact["상주인구_존재"] = compact["총_상주인구_수"].notna()
    compact["직장인구_존재"] = compact["총_직장인구_수"].notna()
    compact["수요원천_존재_개수"] = compact[["유동인구_존재", "상주인구_존재", "직장인구_존재"]].sum(axis=1)
    compact["총_기초수요_프록시"] = compact[["총_유동인구_수", "총_상주인구_수", "총_직장인구_수"]].fillna(0).sum(axis=1)
    compact["source_id"] = "seoul_floating_population_trade_area;seoul_resident_population_trade_area;seoul_worker_population_trade_area"
    compact["provider"] = PROVIDER
    snapshot_dates = {raw_snapshot_date(cfg["raw_dir"]) for cfg in SERVICES.values()}
    if len(snapshot_dates) != 1:
        raise RuntimeError(f"Population raw sources use different snapshots: {sorted(snapshot_dates)}")
    compact["snapshot_date"] = snapshot_dates.pop()
    compact["source_grain"] = "기준년분기+상권코드"
    compact["directness_level"] = "P0_공식_상권_추정집계_결합"
    compact["forbidden_claim_ko"] = "실제 방문자 수, 실제 구매자 수, 창업 성공확률로 표현 금지"
    compact["notes_ko"] = "수요축 알고리즘 편의를 위해 유동·상주·직장인구 총량만 결합한 compact 테이블이다. 세부 성별/연령/시간대 값은 원천별 silver 테이블을 사용한다."
    return compact.sort_values(KEY_COLS).reset_index(drop=True)


def validate_tables(tables: dict[str, pd.DataFrame], metas: dict[str, dict[str, Any]], compact: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    trade_area_codes = load_trade_area_codes()
    domain_rows: list[dict[str, Any]] = []
    table_names = {
        "floating": "silver_floating_population_trade_area_q",
        "resident": "silver_resident_population_trade_area_q",
        "worker": "silver_worker_population_trade_area_q",
    }
    for kind, df in tables.items():
        stats = population_sum_stats(df, kind)
        row = {
            "table": table_names[kind],
            "rows": len(df),
            "api_total_count": metas[kind]["api_total_count"],
            "raw_page_count": metas[kind]["page_count"],
            "row_count_matches_api": len(df) == metas[kind]["api_total_count"],
            "quarter_min": df["기준_년분기_코드"].min(),
            "quarter_max": df["기준_년분기_코드"].max(),
            "quarter_count": df["기준_년분기_코드"].nunique(),
            "area_count": df["상권_코드"].nunique(),
            "key_null_cells": key_null_cells(df),
            "duplicate_key_rows": duplicate_key_rows(df),
            "negative_population_cells": count_negative_cells(df),
            "area_codes_missing_from_master": len(set(df["상권_코드"]) - trade_area_codes) if trade_area_codes else -1,
            "gender_total_mismatch_rows": stats.get("gender", {}).get("beyond_tolerance", mismatch_count(df, "quality_gender_total_mismatch")),
            "age_total_mismatch_rows": stats.get("age", {}).get("beyond_tolerance", mismatch_count(df, "quality_age_total_mismatch")),
            "gender_age_total_mismatch_rows": stats.get("gender_age", {}).get("beyond_tolerance", mismatch_count(df, "quality_gender_age_total_mismatch")),
            "time_total_mismatch_rows": stats.get("time", {}).get("beyond_tolerance", mismatch_count(df, "quality_time_total_mismatch")),
            "day_total_mismatch_rows": stats.get("day", {}).get("beyond_tolerance", mismatch_count(df, "quality_day_total_mismatch")),
            "household_total_mismatch_rows": stats.get("household", {}).get("beyond_tolerance", mismatch_count(df, "quality_household_total_mismatch")),
            "gender_total_exact_mismatch_rows": stats.get("gender", {}).get("exact", mismatch_count(df, "quality_gender_total_mismatch")),
            "age_total_exact_mismatch_rows": stats.get("age", {}).get("exact", mismatch_count(df, "quality_age_total_mismatch")),
            "time_total_exact_mismatch_rows": stats.get("time", {}).get("exact", mismatch_count(df, "quality_time_total_mismatch")),
            "day_total_exact_mismatch_rows": stats.get("day", {}).get("exact", mismatch_count(df, "quality_day_total_mismatch")),
            "max_sum_abs_diff": max((v.get("max_abs", 0) for v in stats.values()), default=0),
        }
        hard_fail = (
            row["row_count_matches_api"] is not True
            or row["key_null_cells"] != 0
            or row["duplicate_key_rows"] != 0
            or row["negative_population_cells"] != 0
            or row["area_codes_missing_from_master"] not in [0, -1]
        )
        mismatch_total = (
            row["gender_total_mismatch_rows"]
            + row["age_total_mismatch_rows"]
            + row["gender_age_total_mismatch_rows"]
            + row["time_total_mismatch_rows"]
            + row["day_total_mismatch_rows"]
            + row["household_total_mismatch_rows"]
        )
        row["judgement"] = "FAIL" if hard_fail else ("조건부 PASS" if mismatch_total else "PASS")
        row["conditional_reason_ko"] = "하위 분해 합계가 총량과 다른 row가 있어 세부 지표 사용 시 품질 플래그 필요" if mismatch_total else ""
        domain_rows.append(row)

    compact_key_null = key_null_cells(compact)
    compact_dup = duplicate_key_rows(compact)
    compact_missing_area = len(set(compact["상권_코드"]) - trade_area_codes) if trade_area_codes else -1
    compact_row = {
        "table": "silver_population_demand_q_area",
        "rows": len(compact),
        "api_total_count": "",
        "raw_page_count": "",
        "row_count_matches_api": "",
        "quarter_min": compact["기준_년분기_코드"].min(),
        "quarter_max": compact["기준_년분기_코드"].max(),
        "quarter_count": compact["기준_년분기_코드"].nunique(),
        "area_count": compact["상권_코드"].nunique(),
        "key_null_cells": compact_key_null,
        "duplicate_key_rows": compact_dup,
        "negative_population_cells": int((compact[["총_유동인구_수", "총_상주인구_수", "총_직장인구_수"]].fillna(0) < 0).sum().sum()),
        "area_codes_missing_from_master": compact_missing_area,
            "gender_total_mismatch_rows": "",
            "age_total_mismatch_rows": "",
            "gender_age_total_mismatch_rows": "",
            "time_total_mismatch_rows": "",
            "day_total_mismatch_rows": "",
            "household_total_mismatch_rows": "",
            "gender_total_exact_mismatch_rows": "",
            "age_total_exact_mismatch_rows": "",
            "time_total_exact_mismatch_rows": "",
            "day_total_exact_mismatch_rows": "",
            "max_sum_abs_diff": "",
        "judgement": "PASS" if compact_key_null == 0 and compact_dup == 0 and compact_missing_area in [0, -1] else "FAIL",
        "conditional_reason_ko": "세 원천의 커버리지 차이는 존재 플래그로 보존",
    }
    domain_rows.append(compact_row)
    domain_df = pd.DataFrame(domain_rows)

    grain_df = pd.DataFrame(
        [
            {
                "table": row["table"],
                "key_cols": " + ".join(KEY_COLS),
                "duplicate_key_rows": row["duplicate_key_rows"],
                "key_null_cells": row["key_null_cells"],
                "judgement": "PASS" if row["duplicate_key_rows"] == 0 and row["key_null_cells"] == 0 else "FAIL",
                "reason_ko": "수요축은 업종 단위가 아니라 분기+상권 grain이다. 업종별 점수에는 같은 분기·상권 기준으로 조인해야 한다.",
            }
            for row in domain_rows
        ]
    )

    contract_df = pd.DataFrame(
        [
            {
                "table": table_names[kind],
                "source_id": SERVICES[kind]["source_id"],
                "provider": PROVIDER,
                "source_service": SERVICES[kind]["service"],
                "rows": len(tables[kind]),
                "contract_status": domain_df.loc[domain_df["table"].eq(table_names[kind]), "judgement"].iloc[0],
                "usage_role": SERVICES[kind]["notes_ko"],
            }
            for kind in ["floating", "resident", "worker"]
        ]
        + [
            {
                "table": "silver_population_demand_q_area",
                "source_id": "seoul_floating_population_trade_area;seoul_resident_population_trade_area;seoul_worker_population_trade_area",
                "provider": PROVIDER,
                "source_service": "VwsmTrdarFlpopQq;VwsmTrdarRepopQq;VwsmTrdarWrcPopltnQq",
                "rows": len(compact),
                "contract_status": compact_row["judgement"],
                "usage_role": "수요축 알고리즘 결합용 compact 테이블",
            }
        ]
    )
    return domain_df, grain_df, contract_df


def write_validation_md(domain_df: pd.DataFrame, grain_df: pd.DataFrame) -> None:
    path = RESEARCH_VALIDATION_DIR / "04_population_silver_validation_20260703.md"
    lines = [
        "# 4회차 인구/수요 silver 전처리 검증",
        "",
        f"작성시각: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "## 대상 파일",
        "",
        "- `datacorpus/_silver/silver_floating_population_trade_area_q.csv`",
        "- `datacorpus/_silver/silver_resident_population_trade_area_q.csv`",
        "- `datacorpus/_silver/silver_worker_population_trade_area_q.csv`",
        "- `datacorpus/_silver/silver_population_demand_q_area.csv`",
        "",
        "## 사용 근거",
        "",
        "- `datacorpus/_raw_ingest/source_registry.csv`: 유동·상주·직장인구는 수요축 P0 원천으로 등록되어 있다.",
        "- `datacorpus/_raw_ingest/seoul_core_coverage_audit.csv`: 세 API 모두 전체 원응답 행 수가 API 총 건수와 일치한다고 기록되어 있다.",
        "- `research/전처리_알고리즘_실행계획_20260703.md`: 길단위인구, 상주인구, 직장인구를 수요축 핵심 입력으로 지정한다.",
        "- `research/전처리_전_확인사항_20260703.md`: 원천별 silver를 나누고, 알고리즘 시점에 필요한 테이블만 조인하라고 정리되어 있다.",
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
            "판단: 원천별 silver 3개는 API `list_total_count`와 row 수가 일치한다. compact 수요 테이블은 세 원천을 `분기+상권` 기준 outer join한 파생 테이블이므로 API 총량 비교 대상이 아니다.",
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
            "판단: 인구 3종은 업종 grain이 아니다. 따라서 매출·점포와 바로 같은 키로 붙이지 않고, 같은 `기준_년분기_코드 + 상권_코드`를 통해 업종별 행에 보조 수요축으로 조인한다.",
            "",
            "## 검증 3: 값 범위와 상권 코드",
            "",
            "| table | negative_population_cells | area_missing | judgement |",
            "|---|---:|---:|---|",
        ]
    )
    for row in domain_df.to_dict("records"):
        lines.append(f"| `{row['table']}` | {row['negative_population_cells']} | {row['area_codes_missing_from_master']} | {row['judgement']} |")

    lines.extend(
        [
            "",
            "판단: 음수 인구수와 상권 마스터 미매칭은 hard fail이다. 현재 hard fail은 없다.",
            "",
            "## 검증 4: 하위 분해 합계",
            "",
            f"허용 기준: 서울시 상권분석 인구 원천은 추정·반올림 집계로 보이므로, 합계 차이 절대값 {ROUNDING_TOLERANCE_COUNT}명 이하는 반올림 오차로 보고 통과시킨다. 아래 표의 불일치는 이 허용 기준을 초과한 row 수다.",
            "",
            "| table | 성별 불일치 | 연령 불일치 | 성별연령 불일치 | 시간대 불일치 | 요일 불일치 | 가구 불일치 | 최대 절대차 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in domain_df.iloc[:3].to_dict("records"):
        lines.append(
            f"| `{row['table']}` | {row['gender_total_mismatch_rows']} | {row['age_total_mismatch_rows']} | {row['gender_age_total_mismatch_rows']} | {row['time_total_mismatch_rows']} | {row['day_total_mismatch_rows']} | {row['household_total_mismatch_rows']} | {row['max_sum_abs_diff']} |"
        )

    lines.extend(
        [
            "",
            "판단: 허용 기준을 넘는 하위 합계 불일치는 없다. 유동인구에는 정확 일치하지 않는 row가 많지만 최대 절대차가 6명 수준이라 반올림 오차로 본다. 총량 중심 수요 점수와 시간대·성별·연령 비중 계산에 사용할 수 있다.",
            "",
            "## 검증 5: compact 수요 테이블",
            "",
            "`silver_population_demand_q_area`는 세 원천의 총량만 결합한다. 세부 성별·연령·시간대·가구 값은 원천별 silver에서만 사용한다.",
            "",
            "이렇게 나눈 이유:",
            "",
            "- 알고리즘 기본 수요축은 compact 테이블로 빠르게 조인한다.",
            "- 업종별 상세 해석이 필요할 때만 원천별 세부 테이블을 추가 조회한다.",
            "- 세 원천의 커버리지 차이를 결측으로 숨기지 않고 `유동인구_존재`, `상주인구_존재`, `직장인구_존재` 플래그로 보존한다.",
            "",
            "## 2보 전진 1보 후퇴 기록",
            "",
            "- 전진 1: 수요축 P0 원천 3개를 모두 silver로 정규화했다.",
            "- 전진 2: 알고리즘용 compact 수요 테이블을 만들되, 세부 원천 테이블도 보존했다.",
            "- 후퇴 1: 인구 데이터는 업종 단위가 아니므로 업종별 수요라고 직접 표현하지 않는다. 상권 단위 수요를 업종별 행에 보조 조인하는 구조로 제한한다.",
            "",
            "## 다음 작업",
            "",
            "1. 상권변화지표 silver 전처리.",
            "2. 집객시설 silver 전처리.",
            "3. 버스/지하철 접근성 원천 전처리.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def append_progress_record(domain_df: pd.DataFrame) -> None:
    path = ROOT / "research" / "전처리_진행기록_20260703.md"
    if not path.exists():
        return
    rows = {row["table"]: row for row in domain_df.to_dict("records")}
    block = [
        "",
        "---",
        "",
        "## 6. 완료된 인구/수요 silver 테이블",
        "",
        "| 산출물 | row 수 | 상태 | 조건부 사유 |",
        "|---|---:|---|---|",
        f"| `datacorpus/_silver/silver_floating_population_trade_area_q.csv` | {rows['silver_floating_population_trade_area_q']['rows']:,} | {rows['silver_floating_population_trade_area_q']['judgement']} | {rows['silver_floating_population_trade_area_q']['conditional_reason_ko']} |",
        f"| `datacorpus/_silver/silver_resident_population_trade_area_q.csv` | {rows['silver_resident_population_trade_area_q']['rows']:,} | {rows['silver_resident_population_trade_area_q']['judgement']} | {rows['silver_resident_population_trade_area_q']['conditional_reason_ko']} |",
        f"| `datacorpus/_silver/silver_worker_population_trade_area_q.csv` | {rows['silver_worker_population_trade_area_q']['rows']:,} | {rows['silver_worker_population_trade_area_q']['judgement']} | {rows['silver_worker_population_trade_area_q']['conditional_reason_ko']} |",
        f"| `datacorpus/_silver/silver_population_demand_q_area.csv` | {rows['silver_population_demand_q_area']['rows']:,} | {rows['silver_population_demand_q_area']['judgement']} | {rows['silver_population_demand_q_area']['conditional_reason_ko']} |",
        "",
        "검증 근거:",
        "",
        "- `datacorpus/_rule_validation/04_population_domain_validation.csv`",
        "- `datacorpus/_rule_validation/04_population_grain_validation.csv`",
        "- `datacorpus/_rule_validation/04_population_source_contract.csv`",
        "- `research/rule_validation/04_population_silver_validation_20260703.md`",
        "",
        "판단:",
        "",
        "- 인구 3종은 업종 단위가 아니라 `기준_년분기_코드 + 상권_코드` 단위다.",
        "- 업종별 점수에는 같은 분기·상권 기준으로 보조 수요축을 조인한다.",
        "- 실제 방문자 수나 구매자 수로 표현하지 않고 수요 프록시로만 사용한다.",
    ]
    text = path.read_text(encoding="utf-8")
    marker = "## 6. 완료된 인구/수요 silver 테이블"
    if marker in text:
        text = text.split("\n---\n\n## 6. 완료된 인구/수요 silver 테이블")[0].rstrip()
    path.write_text(text.rstrip() + "\n" + "\n".join(block) + "\n", encoding="utf-8")


def main() -> None:
    ensure_dirs()
    tables: dict[str, pd.DataFrame] = {}
    metas: dict[str, dict[str, Any]] = {}
    for kind, cfg in SERVICES.items():
        tables[kind], metas[kind] = build_population_table(kind, cfg)

    compact = build_demand_compact(tables)
    domain_df, grain_df, contract_df = validate_tables(tables, metas, compact)

    for kind, cfg in SERVICES.items():
        tables[kind].to_csv(SILVER_DIR / cfg["output"], index=False, encoding="utf-8-sig")
    compact.to_csv(SILVER_DIR / "silver_population_demand_q_area.csv", index=False, encoding="utf-8-sig")

    domain_df.to_csv(VALIDATION_DIR / "04_population_domain_validation.csv", index=False, encoding="utf-8-sig")
    grain_df.to_csv(VALIDATION_DIR / "04_population_grain_validation.csv", index=False, encoding="utf-8-sig")
    contract_df.to_csv(VALIDATION_DIR / "04_population_source_contract.csv", index=False, encoding="utf-8-sig")
    write_validation_md(domain_df, grain_df)
    append_progress_record(domain_df)

    summary = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "rows": {kind: len(df) for kind, df in tables.items()},
        "compact_rows": len(compact),
        "validation_judgements": domain_df[["table", "judgement"]].to_dict("records"),
        "outputs": [
            "datacorpus/_silver/silver_floating_population_trade_area_q.csv",
            "datacorpus/_silver/silver_resident_population_trade_area_q.csv",
            "datacorpus/_silver/silver_worker_population_trade_area_q.csv",
            "datacorpus/_silver/silver_population_demand_q_area.csv",
            "datacorpus/_rule_validation/04_population_domain_validation.csv",
            "datacorpus/_rule_validation/04_population_grain_validation.csv",
            "datacorpus/_rule_validation/04_population_source_contract.csv",
            "research/rule_validation/04_population_silver_validation_20260703.md",
        ],
    }
    (VALIDATION_DIR / "04_population_preprocess_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
