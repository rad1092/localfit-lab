# -*- coding: utf-8 -*-
"""
교통 승하차량 접근성 후보 gold 백테스트.

목적:
  - 31번에서 만든 full-history 교통 접근성 후보 gold를 기존 백테스트 row에 붙인다.
  - 기존 접근성 축을 바로 교체하지 않고, 후보 점수의 방향성과 성능 개선 여부만 측정한다.
  - 성능·누수·직접점수 금지 조건을 모두 통과하기 전까지 엔진 승격은 하지 않는다.

근거:
  - research/알고리즘_스펙_v1_20260703.md: 백테스트, 시간누수 금지, 점수축 승격 전 검증.
  - research/algorithm_evidence_sources/: 접근성은 거리/유입 프록시이며 방문확률로 표현 금지.
  - research/rule_validation/31~32, 42~43, 55, 58: 교통 승하차량은 full-history 후보 gold까지 준비됐지만 직접점수는 보류.
"""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import build_rule_based_location_scores as engine  # noqa: E402


GOLD = ROOT / "datacorpus" / "_gold"
BACKTEST = ROOT / "datacorpus" / "_score_backtest_gold"
RULE = ROOT / "datacorpus" / "_rule_validation"
DOC = ROOT / "research" / "rule_validation"

LABELS_PATH = BACKTEST / "gold_engine_backtest_labeled_rows.csv"
TRANSIT_PATH = GOLD / "gold_accessibility_transit_q_area_candidate.csv"
OVERALL_METRICS_PATH = BACKTEST / "gold_engine_backtest_overall_metrics.csv"

OUT_ATTACHED = BACKTEST / "gold_engine_backtest_transit_accessibility_candidate_attached_rows.csv"
OUT_QUARTER_FEATURES = RULE / "59_transit_accessibility_candidate_quarter_features.csv"
OUT_METRICS = RULE / "59_transit_accessibility_candidate_backtest_metrics.csv"
OUT_MIX_GRID = RULE / "59_transit_accessibility_candidate_mix_sensitivity.csv"
OUT_DECILES = RULE / "59_transit_accessibility_candidate_backtest_deciles.csv"
OUT_BLOCKS = RULE / "59_transit_accessibility_candidate_block_stability.csv"
OUT_VALIDATION = RULE / "59_transit_accessibility_candidate_backtest_validation.csv"
OUT_SUMMARY = RULE / "59_transit_accessibility_candidate_backtest_summary.json"
OUT_DOC = DOC / "59_transit_accessibility_candidate_backtest_validation_20260707.md"

VERSION = "transit_accessibility_candidate_backtest.v0.2-20260707"
KEY_AREA_Q = ["기준_년분기_코드", "상권_코드"]
KEYS = ["기준_년분기_코드", "상권_코드", "서비스_업종_코드"]
TARGET_SALES_PCT = "next_sales_pct_same_industry"
TARGET_SALES_LOG = "next_sales_log"
TARGET_EXCESS_GROWTH = "excess_log_growth_vs_industry"
REQUIRED_BACKTEST_QUARTERS = [f"{year}{quarter}" for year in range(2021, 2026) for quarter in range(1, 5)]


PASSENGER_SUM_COLS = [
    "버스_월승하차_inside",
    "지하철_월승하차_inside",
    "버스_월승하차_100m",
    "지하철_월승하차_100m",
    "버스_월승하차_250m",
    "지하철_월승하차_250m",
    "버스_월승하차_500m",
    "지하철_월승하차_500m",
    "버스_낮승하차_500m",
    "지하철_낮승하차_500m",
    "버스_출근오전승하차_500m",
    "지하철_출근오전승하차_500m",
    "버스_퇴근저녁승하차_500m",
    "지하철_퇴근저녁승하차_500m",
    "버스_야간승하차_500m",
    "지하철_야간승하차_500m",
    "버스_심야새벽승하차_500m",
    "지하철_심야새벽승하차_500m",
]

COUNT_MEAN_COLS = [
    "버스_정류소수_inside",
    "지하철_역수_inside",
    "버스_정류소수_100m",
    "지하철_역수_100m",
    "버스_정류소수_250m",
    "지하철_역수_250m",
    "버스_정류소수_500m",
    "지하철_역수_500m",
]

SCORE_COLS = [
    "transit_total_500m_score",
    "transit_total_250m_score",
    "transit_total_100m_score",
    "transit_inside_score",
    "transit_commute_500m_score",
    "transit_stop_station_500m_score",
    "transit_accessibility_blend_score",
    "accessibility_axis_existing_transit_50_50",
    "accessibility_axis_existing_transit_70_30",
    "current_score_transit_accessibility_50_50",
    "current_score_transit_accessibility_70_30",
]


def read_csv(path: Path, **kwargs) -> pd.DataFrame:
    return pd.read_csv(path, encoding="utf-8-sig", **kwargs)


def write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")


def safe_corr(df: pd.DataFrame, x: str, y: str) -> float | None:
    if x == y:
        return 1.0
    sub = df[[x, y]].replace([np.inf, -np.inf], np.nan).dropna()
    if len(sub) < 30:
        return None
    return float(sub[x].rank(method="average").corr(sub[y].rank(method="average")))


def fmt(value: object) -> str:
    if value is None:
        return "nan"
    try:
        f = float(value)
        if math.isnan(f):
            return "nan"
        return f"{f:.6f}"
    except Exception:
        return str(value)


def md_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "(rows 없음)"
    out = df.copy()
    for col in out.columns:
        out[col] = out[col].map(lambda x: "" if pd.isna(x) else str(x).replace("|", "/"))
    header = "| " + " | ".join(out.columns.astype(str)) + " |"
    sep = "| " + " | ".join(["---"] * len(out.columns)) + " |"
    rows = ["| " + " | ".join(row) + " |" for row in out.to_numpy(dtype=str)]
    return "\n".join([header, sep, *rows])


def quarter_to_months(q: str) -> set[str]:
    year = int(str(q)[:4])
    quarter = int(str(q)[4])
    start = (quarter - 1) * 3 + 1
    return {f"{year}{month:02d}" for month in range(start, start + 3)}


def rank_pct_by_quarter(df: pd.DataFrame, value_col: str) -> pd.Series:
    values = pd.to_numeric(df[value_col], errors="coerce")
    return values.groupby(df["기준_년분기_코드"]).rank(pct=True, method="average") * 100.0


def score_decile_by_quarter(df: pd.DataFrame, score_col: str) -> pd.Series:
    def one_group(s: pd.Series) -> pd.Series:
        valid = s.dropna()
        if len(valid) < 10:
            return pd.Series(np.nan, index=s.index)
        return pd.qcut(valid.rank(method="first"), 10, labels=False, duplicates="drop").reindex(s.index) + 1

    return df.groupby("기준_년분기_코드")[score_col].transform(one_group)


def combine_current_score(df: pd.DataFrame, accessibility_col: str) -> pd.Series:
    """기존 매출/경쟁/수요 축은 그대로 두고 접근성 축만 후보값으로 대체한다."""
    weights_by_set = engine.load_axis_weights()
    axis_cols = {
        "sales": "axis__sales",
        "competition": "axis__competition",
        "demand": "axis__demand",
        "accessibility": accessibility_col,
    }
    out = pd.Series(np.nan, index=df.index, dtype=float)
    for weight_set, idx in df.groupby("weight_set").groups.items():
        weights = weights_by_set.get(str(weight_set), weights_by_set["BASE"])
        sub = df.loc[idx]
        numerator = pd.Series(0.0, index=sub.index, dtype=float)
        denominator = pd.Series(0.0, index=sub.index, dtype=float)
        for axis, col in axis_cols.items():
            value = pd.to_numeric(sub[col], errors="coerce")
            mask = value.notna()
            numerator.loc[mask] += value.loc[mask] * weights[axis]
            denominator.loc[mask] += weights[axis]
        out.loc[idx] = numerator / denominator.replace(0, np.nan)
    return out.clip(0, 100)


def load_labels() -> pd.DataFrame:
    usecols = [
        "기준_년분기_코드",
        "상권_코드",
        "상권_코드_명",
        "자치구_코드_명",
        "서비스_업종_코드",
        "서비스_업종_코드_명",
        "weight_set",
        "current_location_score",
        "axis__sales",
        "axis__competition",
        "axis__demand",
        "axis__accessibility",
        TARGET_SALES_PCT,
        TARGET_SALES_LOG,
        TARGET_EXCESS_GROWTH,
        "next_sales",
        "next_sales_top_quartile_same_industry",
        "beats_industry_median_log_growth",
    ]
    labels = read_csv(
        LABELS_PATH,
        usecols=usecols,
        dtype={"기준_년분기_코드": str, "상권_코드": str, "서비스_업종_코드": str},
        low_memory=False,
    )
    labels["industry_prefix"] = labels["서비스_업종_코드"].astype(str).str[:3]
    labels["year"] = labels["기준_년분기_코드"].astype(str).str[:4]
    return labels


def load_transit_quarter_features(backtest_quarters: list[str]) -> tuple[pd.DataFrame, dict[str, object]]:
    usecols = [
        "상권_코드",
        "기준_월",
        "기준_년분기_코드",
        "direct_score_allowed",
        "proxy_score_allowed_after_validation",
        "temporal_coverage_status",
        *PASSENGER_SUM_COLS,
        *COUNT_MEAN_COLS,
    ]
    monthly = read_csv(
        TRANSIT_PATH,
        usecols=usecols,
        dtype={"기준_년분기_코드": str, "기준_월": str, "상권_코드": str},
        low_memory=False,
    )
    monthly = monthly[monthly["기준_년분기_코드"].isin(backtest_quarters)].copy()
    for col in PASSENGER_SUM_COLS + COUNT_MEAN_COLS:
        monthly[col] = pd.to_numeric(monthly[col], errors="coerce").fillna(0.0)

    direct_true = int(monthly["direct_score_allowed"].astype(str).str.lower().isin(["true", "1", "yes"]).sum())
    proxy_true = int(
        monthly["proxy_score_allowed_after_validation"].astype(str).str.lower().isin(["true", "1", "yes"]).sum()
    )
    coverage_status = sorted(monthly["temporal_coverage_status"].dropna().astype(str).unique().tolist())

    agg_spec: dict[str, tuple[str, str]] = {
        "transit_month_count": ("기준_월", "nunique"),
        **{f"qsum__{col}": (col, "sum") for col in PASSENGER_SUM_COLS},
        **{f"qmean__{col}": (col, "mean") for col in COUNT_MEAN_COLS},
    }
    q = monthly.groupby(KEY_AREA_Q, as_index=False).agg(**agg_spec)
    q["transit_total_inside"] = q["qsum__버스_월승하차_inside"] + q["qsum__지하철_월승하차_inside"]
    q["transit_total_100m"] = q["qsum__버스_월승하차_100m"] + q["qsum__지하철_월승하차_100m"]
    q["transit_total_250m"] = q["qsum__버스_월승하차_250m"] + q["qsum__지하철_월승하차_250m"]
    q["transit_total_500m"] = q["qsum__버스_월승하차_500m"] + q["qsum__지하철_월승하차_500m"]
    q["transit_commute_500m"] = (
        q["qsum__버스_출근오전승하차_500m"]
        + q["qsum__지하철_출근오전승하차_500m"]
        + q["qsum__버스_퇴근저녁승하차_500m"]
        + q["qsum__지하철_퇴근저녁승하차_500m"]
    )
    q["transit_night_500m"] = (
        q["qsum__버스_야간승하차_500m"]
        + q["qsum__지하철_야간승하차_500m"]
        + q["qsum__버스_심야새벽승하차_500m"]
        + q["qsum__지하철_심야새벽승하차_500m"]
    )
    q["transit_stop_station_500m"] = q["qmean__버스_정류소수_500m"] + q["qmean__지하철_역수_500m"]
    q["transit_stop_station_250m"] = q["qmean__버스_정류소수_250m"] + q["qmean__지하철_역수_250m"]

    raw_score_map = {
        "transit_total_500m_score": "transit_total_500m",
        "transit_total_250m_score": "transit_total_250m",
        "transit_total_100m_score": "transit_total_100m",
        "transit_inside_score": "transit_total_inside",
        "transit_commute_500m_score": "transit_commute_500m",
        "transit_stop_station_500m_score": "transit_stop_station_500m",
    }
    for score_col, value_col in raw_score_map.items():
        q[f"log1p__{value_col}"] = np.log1p(pd.to_numeric(q[value_col], errors="coerce").fillna(0.0))
        q[score_col] = rank_pct_by_quarter(q, f"log1p__{value_col}")

    q["transit_accessibility_blend_score"] = q[
        [
            "transit_total_500m_score",
            "transit_total_250m_score",
            "transit_commute_500m_score",
            "transit_stop_station_500m_score",
        ]
    ].mean(axis=1, skipna=True)

    quarter_month_counts = q.groupby("기준_년분기_코드")["transit_month_count"].agg(["min", "max"]).reset_index()
    audit = {
        "direct_true": direct_true,
        "proxy_true": proxy_true,
        "coverage_status": coverage_status,
        "monthly_rows": int(len(monthly)),
        "quarter_rows": int(len(q)),
        "quarter_month_min": int(q["transit_month_count"].min()) if not q.empty else 0,
        "quarter_month_max": int(q["transit_month_count"].max()) if not q.empty else 0,
        "quarter_month_counts": quarter_month_counts.to_dict(orient="records"),
    }
    return q, audit


def attach_features(labels: pd.DataFrame, q: pd.DataFrame) -> pd.DataFrame:
    attached = labels.merge(q, on=KEY_AREA_Q, how="left", validate="many_to_one")
    attached["accessibility_axis_existing_transit_50_50"] = (
        pd.to_numeric(attached["axis__accessibility"], errors="coerce") * 0.5
        + pd.to_numeric(attached["transit_accessibility_blend_score"], errors="coerce") * 0.5
    )
    attached["accessibility_axis_existing_transit_70_30"] = (
        pd.to_numeric(attached["axis__accessibility"], errors="coerce") * 0.7
        + pd.to_numeric(attached["transit_accessibility_blend_score"], errors="coerce") * 0.3
    )
    attached["current_score_transit_accessibility_50_50"] = combine_current_score(
        attached, "accessibility_axis_existing_transit_50_50"
    )
    attached["current_score_transit_accessibility_70_30"] = combine_current_score(
        attached, "accessibility_axis_existing_transit_70_30"
    )
    for col in SCORE_COLS:
        if col in attached.columns:
            attached[f"{col}__decile"] = score_decile_by_quarter(attached, col)
    attached["transit_candidate_direct_score_allowed"] = False
    attached["transit_candidate_engine_promotion_ready"] = False
    attached["transit_candidate_score_use_status"] = "백테스트_후보_엔진승격전"
    attached["transit_candidate_forbidden_claim_ko"] = "실제 상권 방문자, 실제 구매자, 실제 도보시간, 실제 방문확률, 창업 성공확률로 표현 금지"
    return attached


def build_metrics(attached: pd.DataFrame) -> pd.DataFrame:
    variants = {
        "engine_current": "current_location_score",
        "engine_accessibility_axis": "axis__accessibility",
        "transit_total_500m": "transit_total_500m_score",
        "transit_total_250m": "transit_total_250m_score",
        "transit_total_100m": "transit_total_100m_score",
        "transit_inside": "transit_inside_score",
        "transit_commute_500m": "transit_commute_500m_score",
        "transit_stop_station_500m": "transit_stop_station_500m_score",
        "transit_blend": "transit_accessibility_blend_score",
        "accessibility_existing_transit_50_50": "accessibility_axis_existing_transit_50_50",
        "accessibility_existing_transit_70_30": "accessibility_axis_existing_transit_70_30",
        "current_score_transit_accessibility_50_50": "current_score_transit_accessibility_50_50",
        "current_score_transit_accessibility_70_30": "current_score_transit_accessibility_70_30",
    }
    rows = []
    for name, col in variants.items():
        rows.append(
            {
                "variant": name,
                "score_col": col,
                "non_null_rows": int(attached[col].notna().sum()),
                "spearman_next_sales_pct_same_industry": safe_corr(attached, col, TARGET_SALES_PCT),
                "spearman_next_sales_log": safe_corr(attached, col, TARGET_SALES_LOG),
                "spearman_excess_log_growth_vs_industry": safe_corr(attached, col, TARGET_EXCESS_GROWTH),
                "rank_corr_with_engine_current": safe_corr(attached, col, "current_location_score"),
                "rank_corr_with_engine_accessibility": safe_corr(attached, col, "axis__accessibility"),
                "mean_score": float(pd.to_numeric(attached[col], errors="coerce").mean()),
                "median_score": float(pd.to_numeric(attached[col], errors="coerce").median()),
            }
        )
    return pd.DataFrame(rows).round(6)


def build_mix_grid(attached: pd.DataFrame) -> pd.DataFrame:
    """기존 접근성축과 교통 후보축의 혼합비 민감도를 본다.

    이 결과는 엔진 산식이 아니라 후보 검토 자료다. 같은 백테스트에서 최적비율을 고르면
    과적합 위험이 있으므로, 성능이 좋아도 engine_promotion_ready는 False로 유지한다.
    """
    candidate_cols = [
        "transit_inside_score",
        "transit_total_100m_score",
        "transit_total_250m_score",
        "transit_total_500m_score",
        "transit_accessibility_blend_score",
    ]
    rows: list[dict[str, object]] = []
    base = pd.to_numeric(attached["axis__accessibility"], errors="coerce")
    for candidate_col in candidate_cols:
        cand = pd.to_numeric(attached[candidate_col], errors="coerce")
        for step in range(0, 21):
            existing_weight = step / 20.0
            candidate_weight = 1.0 - existing_weight
            mixed = base * existing_weight + cand * candidate_weight
            rows.append(
                {
                    "candidate_col": candidate_col,
                    "existing_accessibility_weight": existing_weight,
                    "transit_candidate_weight": candidate_weight,
                    "spearman_next_sales_pct_same_industry": safe_corr(
                        pd.DataFrame({"score": mixed, TARGET_SALES_PCT: attached[TARGET_SALES_PCT]}),
                        "score",
                        TARGET_SALES_PCT,
                    ),
                    "spearman_next_sales_log": safe_corr(
                        pd.DataFrame({"score": mixed, TARGET_SALES_LOG: attached[TARGET_SALES_LOG]}),
                        "score",
                        TARGET_SALES_LOG,
                    ),
                    "spearman_excess_log_growth_vs_industry": safe_corr(
                        pd.DataFrame({"score": mixed, TARGET_EXCESS_GROWTH: attached[TARGET_EXCESS_GROWTH]}),
                        "score",
                        TARGET_EXCESS_GROWTH,
                    ),
                }
            )
    return pd.DataFrame(rows).round(6)


def build_deciles(attached: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for score_col in SCORE_COLS:
        decile_col = f"{score_col}__decile"
        if decile_col not in attached.columns:
            continue
        for decile, part in attached.groupby(decile_col, dropna=True):
            rows.append(
                {
                    "score": score_col,
                    "score_decile": int(decile),
                    "rows": int(len(part)),
                    "avg_next_sales_pct_same_industry": float(part[TARGET_SALES_PCT].mean()),
                    "avg_next_sales_log": float(part[TARGET_SALES_LOG].mean()),
                    "avg_excess_log_growth_vs_industry": float(part[TARGET_EXCESS_GROWTH].mean()),
                    "top_quartile_rate": float(part["next_sales_top_quartile_same_industry"].mean()),
                    "beats_industry_median_rate": float(part["beats_industry_median_log_growth"].mean()),
                    "avg_engine_current_score": float(part["current_location_score"].mean()),
                    "avg_engine_accessibility": float(part["axis__accessibility"].mean()),
                }
            )
    return pd.DataFrame(rows).round(6)


def build_block_stability(attached: pd.DataFrame) -> pd.DataFrame:
    rows = []
    groups = {
        "year": "year",
        "district": "자치구_코드_명",
        "industry_prefix": "industry_prefix",
    }
    for group_name, group_col in groups.items():
        for group_value, part in attached.groupby(group_col):
            if len(part) < 500:
                continue
            for score_col in [
                "axis__accessibility",
                "transit_accessibility_blend_score",
                "accessibility_axis_existing_transit_70_30",
                "current_score_transit_accessibility_70_30",
            ]:
                rows.append(
                    {
                        "group_type": group_name,
                        "group_value": group_value,
                        "score_col": score_col,
                        "rows": int(len(part)),
                        "spearman_next_sales_pct_same_industry": safe_corr(part, score_col, TARGET_SALES_PCT),
                        "spearman_excess_log_growth_vs_industry": safe_corr(part, score_col, TARGET_EXCESS_GROWTH),
                    }
                )
    return pd.DataFrame(rows).round(6)


def metric_value(metrics: pd.DataFrame, variant: str, metric_col: str) -> float:
    row = metrics[metrics["variant"].eq(variant)]
    if row.empty:
        return float("nan")
    return float(row[metric_col].iloc[0])


def add_validation(rows: list[dict], vid: str, name: str, observed, expected, result: str, reason: str) -> None:
    rows.append(
        {
            "validation_id": vid,
            "validation_name": name,
            "observed": observed,
            "expected": expected,
            "result": result,
            "reason_ko": reason,
        }
    )


def build_validations(
    labels: pd.DataFrame,
    q: pd.DataFrame,
    attached: pd.DataFrame,
    metrics: pd.DataFrame,
    mix_grid: pd.DataFrame,
    deciles: pd.DataFrame,
    blocks: pd.DataFrame,
    transit_audit: dict[str, object],
) -> tuple[pd.DataFrame, dict[str, object]]:
    rows: list[dict] = []
    overall = read_csv(OVERALL_METRICS_PATH)
    baseline_summary_corr = float(overall["score_spearman_next_sales_pct_same_industry"].iloc[0])
    baseline_current_corr = metric_value(metrics, "engine_current", "spearman_next_sales_pct_same_industry")
    existing_access_corr = metric_value(metrics, "engine_accessibility_axis", "spearman_next_sales_pct_same_industry")
    transit_blend_corr = metric_value(metrics, "transit_blend", "spearman_next_sales_pct_same_industry")
    alt_access_70_corr = metric_value(metrics, "accessibility_existing_transit_70_30", "spearman_next_sales_pct_same_industry")
    alt_current_70_corr = metric_value(metrics, "current_score_transit_accessibility_70_30", "spearman_next_sales_pct_same_industry")
    best_transit_row = metrics[
        metrics["variant"].astype(str).str.startswith("transit_")
    ].sort_values("spearman_next_sales_pct_same_industry", ascending=False).head(1)
    best_transit_variant = str(best_transit_row["variant"].iloc[0]) if not best_transit_row.empty else ""
    best_transit_corr = (
        float(best_transit_row["spearman_next_sales_pct_same_industry"].iloc[0]) if not best_transit_row.empty else float("nan")
    )
    best_alt_current_row = metrics[
        metrics["variant"].astype(str).str.startswith("current_score_transit_accessibility")
    ].sort_values("spearman_next_sales_pct_same_industry", ascending=False).head(1)
    best_alt_current_variant = str(best_alt_current_row["variant"].iloc[0]) if not best_alt_current_row.empty else ""
    best_alt_current_corr = (
        float(best_alt_current_row["spearman_next_sales_pct_same_industry"].iloc[0])
        if not best_alt_current_row.empty
        else float("nan")
    )
    best_mix_row = mix_grid.sort_values("spearman_next_sales_pct_same_industry", ascending=False).head(1)
    best_mix_candidate = str(best_mix_row["candidate_col"].iloc[0]) if not best_mix_row.empty else ""
    best_mix_existing_weight = float(best_mix_row["existing_accessibility_weight"].iloc[0]) if not best_mix_row.empty else float("nan")
    best_mix_transit_weight = float(best_mix_row["transit_candidate_weight"].iloc[0]) if not best_mix_row.empty else float("nan")
    best_mix_corr = (
        float(best_mix_row["spearman_next_sales_pct_same_industry"].iloc[0]) if not best_mix_row.empty else float("nan")
    )

    q_month_complete = q["transit_month_count"].eq(3).all()
    no_missing_attach = int(attached["transit_accessibility_blend_score"].isna().sum()) == 0
    required_quarters_present = set(REQUIRED_BACKTEST_QUARTERS).issubset(set(q["기준_년분기_코드"].astype(str)))
    no_future_quarter = not set(attached["기준_년분기_코드"].astype(str)).intersection({"20262"})
    direct_flags = int(attached["transit_candidate_direct_score_allowed"].astype(bool).sum())
    promotion_flags = int(attached["transit_candidate_engine_promotion_ready"].astype(bool).sum())
    block_positive_rate = float(
        blocks.loc[
            blocks["score_col"].eq("transit_accessibility_blend_score"),
            "spearman_next_sales_pct_same_industry",
        ].gt(0).mean()
    )

    add_validation(
        rows,
        "59-V01",
        "교통 후보 gold 직접점수 금지 상태 유지",
        f"source_direct_true={transit_audit['direct_true']}, attached_direct_flags={direct_flags}, promotion_flags={promotion_flags}",
        "0, 0, 0",
        "PASS" if transit_audit["direct_true"] == 0 and direct_flags == 0 and promotion_flags == 0 else "FAIL",
        "후보 gold와 attached row 모두 direct_score_allowed=False여야 엔진 승격 전 실험 계약을 지킨다.",
    )
    add_validation(
        rows,
        "59-V02",
        "백테스트 필수 분기와 월 완전성",
        f"required_quarters_present={required_quarters_present}, month_min={transit_audit['quarter_month_min']}, month_max={transit_audit['quarter_month_max']}",
        "2021Q1~2025Q4 포함, 각 상권×분기 3개월",
        "PASS" if required_quarters_present and q_month_complete else "FAIL",
        "분기 후보는 해당 분기의 3개월만 써야 하며, 빠진 월이 있으면 백테스트 후보로 볼 수 없다.",
    )
    add_validation(
        rows,
        "59-V03",
        "라벨 row 손실 없이 many_to_one attach",
        f"labels={len(labels)}, attached={len(attached)}, missing_features={int(attached['transit_accessibility_blend_score'].isna().sum())}",
        "row 수 동일, missing 0",
        "PASS" if len(labels) == len(attached) and no_missing_attach else "FAIL",
        "상권×분기 후보를 상권×업종×분기 라벨에 붙이므로 cardinality가 many_to_one이어야 한다.",
    )
    add_validation(
        rows,
        "59-V04",
        "시간누수 금지",
        f"attached_quarters={sorted(attached['기준_년분기_코드'].astype(str).unique())[:2]}...{sorted(attached['기준_년분기_코드'].astype(str).unique())[-2:]}, has_20262={not no_future_quarter}",
        "2021Q1~2025Q4만 attach",
        "PASS" if no_future_quarter else "FAIL",
        "202605 운영 최신월은 백테스트 라벨에 붙이지 않는다.",
    )
    add_validation(
        rows,
        "59-V05",
        "기존 엔진 metric 재현",
        f"summary={fmt(baseline_summary_corr)}, recalculated={fmt(baseline_current_corr)}",
        "abs diff <= 1e-6",
        "PASS" if abs(baseline_summary_corr - baseline_current_corr) <= 1e-6 else "FAIL",
        "후보 실험 전에 기존 current score metric이 기존 백테스트 요약과 일치해야 한다.",
    )
    add_validation(
        rows,
        "59-V06",
        "교통 후보 단독 방향성",
        f"best_transit={best_transit_variant}:{fmt(best_transit_corr)}, existing_accessibility={fmt(existing_access_corr)}",
        "best_transit > 0 and comparable",
        "PASS" if best_transit_corr > 0 else "NOT_READY",
        "승하차량 후보가 다음분기 동일업종 매출 백분위와 양의 방향을 보여야 접근성 후보로 유지할 수 있다.",
    )
    add_validation(
        rows,
        "59-V07",
        "기존 접근성축 보강 민감도 게이트",
        f"best_mix={best_mix_candidate}, existing_w={fmt(best_mix_existing_weight)}, transit_w={fmt(best_mix_transit_weight)}, corr={fmt(best_mix_corr)}, existing_accessibility={fmt(existing_access_corr)}, diff={fmt(best_mix_corr - existing_access_corr)}",
        "민감도 grid에서 기존 접근성축 대비 +0.005 이상",
        "PASS" if best_mix_corr >= existing_access_corr + 0.005 else "NOT_READY",
        "기존 접근성축보다 의미 있게 좋아지는 혼합 후보가 있어야 후속 산식 검토 대상으로 볼 수 있다. 단, 같은 백테스트에서 고른 비율은 과적합 위험이 있어 곧바로 승격하지 않는다.",
    )
    add_validation(
        rows,
        "59-V08",
        "현재입지 총점 보강 성능 게이트",
        f"best_alt_current={best_alt_current_variant}:{fmt(best_alt_current_corr)}, baseline_current={fmt(baseline_current_corr)}, diff={fmt(best_alt_current_corr - baseline_current_corr)}",
        "기존 current score 대비 +0.002 이상",
        "PASS" if best_alt_current_corr >= baseline_current_corr + 0.002 else "NOT_READY",
        "접근성 후보를 섞어도 전체 현재입지 점수의 주 타깃 성능이 개선되지 않으면 엔진 변경은 하지 않는다.",
    )
    add_validation(
        rows,
        "59-V09",
        "블록 안정성",
        f"positive_rate={fmt(block_positive_rate)}, block_rows={len(blocks)}",
        "transit blend 블록 양의 방향 비율 >= 0.70",
        "PASS" if block_positive_rate >= 0.70 else "NOT_READY",
        "일부 자치구/연도/업종 prefix에서만 우연히 좋은 신호면 정식 승격하면 안 된다.",
    )
    add_validation(
        rows,
        "59-V10",
        "decile 단조성 참고",
        f"decile_rows={len(deciles)}",
        "decile table 생성",
        "PASS" if not deciles.empty else "FAIL",
        "상위 decile이 실제 라벨에서 어떤 평균을 보이는지 사람이 검토할 수 있어야 한다.",
    )

    validation = pd.DataFrame(rows)
    fail_count = int(validation["result"].eq("FAIL").sum())
    not_ready_count = int(validation["result"].eq("NOT_READY").sum())
    performance_ready = fail_count == 0 and not_ready_count == 0
    summary = {
        "run_date": "2026-07-07",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "validation_version": VERSION,
        "label_rows": int(len(labels)),
        "quarter_feature_rows": int(len(q)),
        "attached_rows": int(len(attached)),
        "pass_count": int(validation["result"].eq("PASS").sum()),
        "not_ready_count": not_ready_count,
        "fail_count": fail_count,
        "baseline_current_corr": baseline_current_corr,
        "existing_accessibility_corr": existing_access_corr,
        "best_transit_variant": best_transit_variant,
        "best_transit_corr": best_transit_corr,
        "alt_accessibility_70_30_corr": alt_access_70_corr,
        "best_mix_candidate": best_mix_candidate,
        "best_mix_existing_weight": best_mix_existing_weight,
        "best_mix_transit_weight": best_mix_transit_weight,
        "best_mix_corr": best_mix_corr,
        "best_alt_current_variant": best_alt_current_variant,
        "best_alt_current_corr": best_alt_current_corr,
        "performance_ready": performance_ready,
        "engine_promotion_ready": False,
        "decision": "TRANSIT_ACCESSIBILITY_CANDIDATE_READY_FOR_ENGINE_REVIEW_NOT_PROMOTED"
        if performance_ready
        else "TRANSIT_ACCESSIBILITY_CANDIDATE_BACKTESTED_NOT_PROMOTED",
        "decision_reason_ko": "성능·누수·블록 안정성 게이트는 통과했지만 같은 백테스트 내 혼합비 민감도 결과이므로 별도 엔진 패치와 재검증 전까지 직접 승격 금지"
        if performance_ready
        else "성능·누수·블록 안정성 게이트 통과 전까지 교통 승하차량 접근성 후보는 엔진 직접 승격 금지",
    }
    return validation, summary


def write_report(
    validation: pd.DataFrame,
    summary: dict[str, object],
    metrics: pd.DataFrame,
    mix_grid: pd.DataFrame,
    deciles: pd.DataFrame,
    blocks: pd.DataFrame,
) -> None:
    DOC.mkdir(parents=True, exist_ok=True)
    metric_view = metrics[
        [
            "variant",
            "non_null_rows",
            "spearman_next_sales_pct_same_industry",
            "spearman_next_sales_log",
            "spearman_excess_log_growth_vs_industry",
            "rank_corr_with_engine_accessibility",
        ]
    ].copy()
    decile_view = deciles[
        deciles["score"].isin(
            [
                "transit_accessibility_blend_score",
                "accessibility_axis_existing_transit_70_30",
                "current_score_transit_accessibility_70_30",
            ]
        )
    ].copy()
    decile_view = decile_view[decile_view["score_decile"].isin([1, 5, 10])].copy()
    mix_view = mix_grid.sort_values("spearman_next_sales_pct_same_industry", ascending=False).head(15).copy()
    block_summary = (
        blocks.groupby("score_col", as_index=False)
        .agg(
            block_count=("rows", "size"),
            positive_sales_pct_blocks=("spearman_next_sales_pct_same_industry", lambda s: int(pd.to_numeric(s, errors="coerce").gt(0).sum())),
            positive_rate=("spearman_next_sales_pct_same_industry", lambda s: float(pd.to_numeric(s, errors="coerce").gt(0).mean())),
            min_sales_pct_corr=("spearman_next_sales_pct_same_industry", "min"),
            median_sales_pct_corr=("spearman_next_sales_pct_same_industry", "median"),
        )
        .round(6)
    )

    lines = [
        "# 교통 승하차량 접근성 후보 백테스트 검증",
        "",
        "작성일: 2026-07-07",
        "",
        "## 1. 목적",
        "",
        "31번 full-history 교통 접근성 후보 gold를 기존 현재입지 백테스트 row에 붙여, 정식 접근성 점수축으로 승격할 수 있는지 전 단계 검증을 수행했다.",
        "",
        "이 검증은 엔진 변경이 아니다. 교통 승하차량은 실제 방문자나 구매자가 아니라 접근성/유입 강도 프록시이므로 성능 게이트를 통과하기 전까지 `direct_score_allowed=False`를 유지한다.",
        "",
        "## 2. 핵심 결과",
        "",
        f"- validation_version: `{summary['validation_version']}`",
        f"- label rows: {summary['label_rows']:,}",
        f"- quarter feature rows: {summary['quarter_feature_rows']:,}",
        f"- attached rows: {summary['attached_rows']:,}",
        f"- PASS: {summary['pass_count']}",
        f"- NOT_READY: {summary['not_ready_count']}",
        f"- FAIL: {summary['fail_count']}",
        f"- decision: `{summary['decision']}`",
        f"- baseline current corr: {fmt(summary['baseline_current_corr'])}",
        f"- existing accessibility corr: {fmt(summary['existing_accessibility_corr'])}",
        f"- best transit candidate: `{summary['best_transit_variant']}` / {fmt(summary['best_transit_corr'])}",
        f"- best accessibility mix: `{summary['best_mix_candidate']}` existing {fmt(summary['best_mix_existing_weight'])} / transit {fmt(summary['best_mix_transit_weight'])} / {fmt(summary['best_mix_corr'])}",
        f"- best alt current: `{summary['best_alt_current_variant']}` / {fmt(summary['best_alt_current_corr'])}",
        f"- engine_promotion_ready: `{summary['engine_promotion_ready']}`",
        "",
        "## 3. 검증 결과",
        "",
        md_table(validation),
        "",
        "## 4. 성능 metric",
        "",
        md_table(metric_view),
        "",
        "## 5. 접근성 혼합 민감도 상위 후보",
        "",
        md_table(mix_view),
        "",
        "## 6. decile 참고",
        "",
        md_table(decile_view),
        "",
        "## 7. 블록 안정성 요약",
        "",
        md_table(block_summary),
        "",
        "## 8. 판정",
        "",
        "교통 접근성 후보는 백테스트에 붙여 검증했지만, 이 문서만으로 엔진에 승격하지 않는다.",
        "",
        "승격 조건:",
        "",
        "1. 기존 접근성축 대비 보강 성능이 충분해야 한다.",
        "2. 기존 현재입지 총점 대비 주 타깃 성능이 악화되지 않고 개선되어야 한다.",
        "3. 자치구/연도/업종 prefix 블록에서 한쪽으로만 좋은 신호가 아니어야 한다.",
        "4. 승하차량을 실제 방문자·구매자·도보시간·성공확률로 표현하지 않아야 한다.",
        "",
        "## 9. 2보 전진 1보 후퇴",
        "",
        "1. 전진: 31번 full-history gold를 기존 427,553개 백테스트 row에 붙였다.",
        "2. 전진: 후보 단독, 기존 접근성 보강, 현재입지 총점 보강을 분리해 성능을 측정했다.",
        "3. 후퇴: 혼합비 민감도에서 좋은 조합이 나와도 같은 백테스트 안에서 고른 비율이므로 과적합 위험이 있다.",
        "4. 후퇴: 202605 운영 최신월은 백테스트 attach에서 제외했다.",
        "5. 후퇴: 생활이동 OD와 승하차량은 서로 다른 접근성 프록시로 분리해 해석한다.",
    ]
    OUT_DOC.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    labels = load_labels()
    backtest_quarters = sorted(labels["기준_년분기_코드"].astype(str).unique().tolist())
    q, transit_audit = load_transit_quarter_features(backtest_quarters)
    attached = attach_features(labels, q)
    metrics = build_metrics(attached)
    mix_grid = build_mix_grid(attached)
    deciles = build_deciles(attached)
    blocks = build_block_stability(attached)
    validation, summary = build_validations(labels, q, attached, metrics, mix_grid, deciles, blocks, transit_audit)

    write_csv(q, OUT_QUARTER_FEATURES)
    keep_cols = [
        *KEYS,
        "상권_코드_명",
        "자치구_코드_명",
        "서비스_업종_코드_명",
        "weight_set",
        "current_location_score",
        "axis__accessibility",
        TARGET_SALES_PCT,
        TARGET_SALES_LOG,
        TARGET_EXCESS_GROWTH,
        "next_sales_top_quartile_same_industry",
        "beats_industry_median_log_growth",
        "transit_month_count",
        "transit_total_500m",
        "transit_total_250m",
        "transit_commute_500m",
        "transit_stop_station_500m",
        *SCORE_COLS,
        "transit_candidate_direct_score_allowed",
        "transit_candidate_engine_promotion_ready",
        "transit_candidate_score_use_status",
        "transit_candidate_forbidden_claim_ko",
    ]
    keep_cols = [col for col in keep_cols if col in attached.columns]
    write_csv(attached[keep_cols], OUT_ATTACHED)
    write_csv(metrics, OUT_METRICS)
    write_csv(mix_grid, OUT_MIX_GRID)
    write_csv(deciles, OUT_DECILES)
    write_csv(blocks, OUT_BLOCKS)
    write_csv(validation, OUT_VALIDATION)
    OUT_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(validation, summary, metrics, mix_grid, deciles, blocks)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if int(summary["fail_count"]):
        raise SystemExit(int(summary["fail_count"]))


if __name__ == "__main__":
    main()
