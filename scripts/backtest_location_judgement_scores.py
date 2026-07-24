from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
FEATURE_MART = ROOT / "datacorpus" / "_final" / "model_ready" / "서울상권_최종공간OD_FeatureMart.parquet"
OUT_DIR = ROOT / "datacorpus" / "_score_backtest"


C = {
    "quarter": "기준_년분기_코드",
    "area_code": "상권_코드",
    "area_name": "상권_코드_명",
    "industry_code": "서비스_업종_코드",
    "industry_name": "서비스_업종_코드_명",
    "district": "자치구_코드_명",
    "current_sales": "당월_매출_금액",
    "next_sales": "다음분기_매출",
    "sales_growth_prev": "매출_전분기_증감률",
    "store_count": "점포_수",
    "similar_store_count": "유사_업종_점포_수",
    "open_rate": "개업_율",
    "close_rate": "폐업_률",
    "avg_operation_months": "운영_영업_개월_평균",
    "floating_population": "총_유동인구_수",
    "resident_population": "총_상주인구_수",
    "worker_population": "총_직장_인구_수",
    "spending_total": "지출_총금액",
    "sales_per_store": "점포당_매출",
    "avg_ticket": "평균_객단가",
    "subway_count": "지하철_역_수",
    "bus_count": "버스_정거장_수",
    "anchor_facility_count": "집객시설_수",
    "spatial_facility_count": "공간시설_총수",
    "spatial_poi_count": "공간POI_총점포수",
    "mobility_inflow": "생활이동_유입_이동인구_합계",
    "real_estate_count": "실거래_상업업무_거래건수",
    "real_estate_avg_10k": "실거래_상업업무_거래금액_만원_평균",
}

NEEDED_COLUMNS = list(dict.fromkeys(C.values()))


# Historical first-pass weights used as the comparison baseline in this evaluator.
# The live judgement engine can be updated to the recommended weights after review.
BASE_WEIGHTS = {
    "demand": 0.22,
    "sales": 0.22,
    "competition": 0.17,
    "accessibility": 0.15,
    "growth_stability": 0.12,
    "budget_risk": 0.04,
    "data_reliability": 0.08,
}

INDUSTRY_WEIGHT_OVERRIDES = {
    "CS1": {
        "demand": 0.24,
        "sales": 0.20,
        "competition": 0.18,
        "accessibility": 0.18,
        "growth_stability": 0.10,
        "budget_risk": 0.04,
        "data_reliability": 0.06,
    },
    "CS2": {
        "demand": 0.20,
        "sales": 0.20,
        "competition": 0.16,
        "accessibility": 0.14,
        "growth_stability": 0.16,
        "budget_risk": 0.06,
        "data_reliability": 0.08,
    },
    "CS3": {
        "demand": 0.21,
        "sales": 0.23,
        "competition": 0.18,
        "accessibility": 0.15,
        "growth_stability": 0.12,
        "budget_risk": 0.04,
        "data_reliability": 0.07,
    },
}

COMPONENT_KEYS = [
    "demand",
    "sales",
    "competition",
    "accessibility",
    "growth_stability",
    "budget_risk",
    "data_reliability",
]


def smape(y_true: pd.Series, y_pred: pd.Series) -> float:
    y_true = pd.to_numeric(y_true, errors="coerce")
    y_pred = pd.to_numeric(y_pred, errors="coerce")
    denom = (y_true.abs() + y_pred.abs()) / 2
    mask = denom > 0
    if not mask.any():
        return float("nan")
    return float(((y_true[mask] - y_pred[mask]).abs() / denom[mask]).mean() * 100)


def safe_corr(df: pd.DataFrame, a: str, b: str, method: str = "spearman") -> float:
    use = df[[a, b]].replace([np.inf, -np.inf], np.nan).dropna()
    if len(use) < 30:
        return float("nan")
    return float(use[a].corr(use[b], method=method))


def weighted_average_frame(df: pd.DataFrame, specs: list[tuple[str, float]], default: float = 50.0) -> pd.Series:
    numerator = pd.Series(0.0, index=df.index)
    denominator = pd.Series(0.0, index=df.index)
    for col, weight in specs:
        values = pd.to_numeric(df[col], errors="coerce")
        mask = values.notna()
        numerator.loc[mask] += values.loc[mask] * weight
        denominator.loc[mask] += weight
    out = numerator / denominator.replace(0, np.nan)
    return out.fillna(default).clip(0, 100).round(4)


def percentile_by_group(
    df: pd.DataFrame,
    col: str,
    group_cols: list[str],
    *,
    higher_is_better: bool = True,
    fallback: pd.Series | None = None,
    min_count: int = 20,
) -> pd.Series:
    values = pd.to_numeric(df[col], errors="coerce")
    grouped = values.groupby([df[g] for g in group_cols], dropna=False)
    count = grouped.transform("count")
    pct = grouped.rank(method="max", pct=True) * 100
    if not higher_is_better:
        pct = 100 - pct
    pct = pct.where(values.notna())
    if fallback is not None:
        pct = pct.where(count >= min_count, fallback)
    return pct.clip(0, 100)


def add_decile_by_quarter(df: pd.DataFrame, score_col: str) -> pd.Series:
    def cut(group: pd.Series) -> pd.Series:
        ranked = group.rank(method="first")
        return pd.qcut(ranked, q=10, labels=False, duplicates="drop") + 1

    return df.groupby(C["quarter"], group_keys=False)[score_col].apply(cut).astype("Int64")


def industry_prefix(series: pd.Series) -> pd.Series:
    return series.astype(str).str.slice(0, 3)


def expected_next_quarter(series: pd.Series) -> pd.Series:
    quarter = pd.to_numeric(series, errors="coerce").astype("Int64")
    year = quarter // 10
    qtr = quarter % 10
    return pd.Series(np.where(qtr < 4, quarter + 1, (year + 1) * 10 + 1), index=series.index).astype("Int64")


def add_scores(df: pd.DataFrame) -> pd.DataFrame:
    q = C["quarter"]
    industry = C["industry_code"]
    district = C["district"]

    df = df.copy()
    for col in NEEDED_COLUMNS:
        if col not in [C["area_code"], C["area_name"], C["industry_code"], C["industry_name"], C["district"]]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    q_pct: dict[str, pd.Series] = {}
    for key in [
        "floating_population",
        "worker_population",
        "resident_population",
        "spending_total",
        "subway_count",
        "bus_count",
        "anchor_facility_count",
        "spatial_facility_count",
        "mobility_inflow",
        "real_estate_count",
    ]:
        q_pct[key] = percentile_by_group(df, C[key], [q], higher_is_better=True)
    q_pct["real_estate_avg_10k_low"] = percentile_by_group(
        df, C["real_estate_avg_10k"], [q], higher_is_better=False
    )

    ind_pct: dict[str, pd.Series] = {}
    for key in ["current_sales", "sales_per_store", "avg_ticket", "sales_growth_prev", "open_rate", "avg_operation_months"]:
        fallback = percentile_by_group(df, C[key], [q], higher_is_better=True)
        ind_pct[key] = percentile_by_group(df, C[key], [q, industry], higher_is_better=True, fallback=fallback)
    for key in ["similar_store_count", "close_rate"]:
        fallback = percentile_by_group(df, C[key], [q], higher_is_better=False)
        ind_pct[f"{key}_low"] = percentile_by_group(
            df, C[key], [q, industry], higher_is_better=False, fallback=fallback
        )

    store_count_q = percentile_by_group(df, C["store_count"], [q], higher_is_better=True)
    store_count_district = percentile_by_group(
        df, C["store_count"], [q, district], higher_is_better=True, fallback=store_count_q
    )

    score_frame = pd.DataFrame(index=df.index)
    score_frame["demand"] = weighted_average_frame(
        pd.DataFrame(
            {
                "floating_population": q_pct["floating_population"],
                "worker_population": q_pct["worker_population"],
                "resident_population": q_pct["resident_population"],
                "spending_total": q_pct["spending_total"],
            }
        ),
        [("floating_population", 1), ("worker_population", 1), ("resident_population", 1), ("spending_total", 1)],
    )
    score_frame["sales"] = weighted_average_frame(
        pd.DataFrame(
            {
                "current_sales": ind_pct["current_sales"],
                "sales_per_store": ind_pct["sales_per_store"],
                "avg_ticket": ind_pct["avg_ticket"],
                "sales_growth_prev": ind_pct["sales_growth_prev"],
            }
        ),
        [("current_sales", 1.2), ("sales_per_store", 1.2), ("avg_ticket", 0.6), ("sales_growth_prev", 0.8)],
    )
    score_frame["competition"] = weighted_average_frame(
        pd.DataFrame(
            {
                "sales_per_store": ind_pct["sales_per_store"],
                "similar_store_count_low": ind_pct["similar_store_count_low"],
                "store_count": store_count_district,
                "close_rate_low": ind_pct["close_rate_low"],
            }
        ),
        [("sales_per_store", 1.2), ("similar_store_count_low", 0.8), ("store_count", 0.5), ("close_rate_low", 1.0)],
    )
    score_frame["accessibility"] = weighted_average_frame(
        pd.DataFrame(
            {
                "subway_count": q_pct["subway_count"],
                "bus_count": q_pct["bus_count"],
                "anchor_facility_count": q_pct["anchor_facility_count"],
                "spatial_facility_count": q_pct["spatial_facility_count"],
                "mobility_inflow": q_pct["mobility_inflow"],
            }
        ),
        [
            ("subway_count", 1),
            ("bus_count", 1),
            ("anchor_facility_count", 1),
            ("spatial_facility_count", 1),
            ("mobility_inflow", 1),
        ],
    )
    score_frame["growth_stability"] = weighted_average_frame(
        pd.DataFrame(
            {
                "sales_growth_prev": ind_pct["sales_growth_prev"],
                "open_rate": ind_pct["open_rate"],
                "close_rate_low": ind_pct["close_rate_low"],
                "avg_operation_months": ind_pct["avg_operation_months"],
            }
        ),
        [("sales_growth_prev", 1.0), ("open_rate", 0.5), ("close_rate_low", 1.0), ("avg_operation_months", 0.7)],
    )

    # Backtest has no user-specific budget. Keep the current engine's no-budget behavior:
    # neutral 50, while preserving cost-proxy columns separately for diagnostics.
    score_frame["budget_risk"] = 50.0

    reliability_checks = [
        ("current_sales", 18),
        ("store_count", 12),
        ("floating_population", 12),
        ("worker_population", 8),
        ("spending_total", 8),
        ("spatial_facility_count", 6),
        ("spatial_poi_count", 6),
        ("real_estate_count", 4),
        ("mobility_inflow", 4),
    ]
    reliability = pd.Series(0.0, index=df.index)
    for key, weight in reliability_checks:
        reliability += df[C[key]].notna().astype(float) * weight
    score_frame["data_reliability"] = reliability.clip(0, 100)

    prefixes = industry_prefix(df[industry])
    for key in COMPONENT_KEYS:
        df[f"{key}_score"] = score_frame[key].round(4)
        df[f"{key}_weight"] = prefixes.map(
            lambda prefix, k=key: INDUSTRY_WEIGHT_OVERRIDES.get(prefix, BASE_WEIGHTS).get(k, BASE_WEIGHTS[k])
        )
        df[f"{key}_weighted"] = df[f"{key}_score"] * df[f"{key}_weight"]
    df["raw_total_score"] = df[[f"{key}_weighted" for key in COMPONENT_KEYS]].sum(axis=1)
    df["total_score"] = np.where(df["data_reliability_score"] < 60, np.minimum(df["raw_total_score"], 65), df["raw_total_score"])
    df["total_score"] = pd.to_numeric(df["total_score"], errors="coerce").round(4)
    df["score_decile"] = add_decile_by_quarter(df, "total_score")

    df["next_sales_log"] = np.log1p(df[C["next_sales"]])
    df["current_sales_log"] = np.log1p(df[C["current_sales"]])
    df["next_log_growth"] = df["next_sales_log"] - df["current_sales_log"]
    df["next_growth_rate"] = np.where(
        df[C["current_sales"]] > 0,
        (df[C["next_sales"]] - df[C["current_sales"]]) / df[C["current_sales"]],
        np.nan,
    )
    df["next_growth_positive"] = (df["next_growth_rate"] > 0).astype(float)
    df["industry_prefix"] = prefixes
    industry_quarter = [C["quarter"], C["industry_code"]]
    df["industry_quarter_median_growth"] = df.groupby(industry_quarter)["next_growth_rate"].transform("median")
    df["industry_quarter_median_log_growth"] = df.groupby(industry_quarter)["next_log_growth"].transform("median")
    df["excess_growth_vs_industry_median"] = df["next_growth_rate"] - df["industry_quarter_median_growth"]
    df["excess_log_growth_vs_industry_median"] = df["next_log_growth"] - df["industry_quarter_median_log_growth"]
    df["beats_industry_median_growth"] = (df["excess_growth_vs_industry_median"] > 0).astype(float)
    df["next_sales_pct_same_industry"] = percentile_by_group(
        df, C["next_sales"], industry_quarter, higher_is_better=True
    )
    df["next_sales_top_quartile_same_industry"] = (df["next_sales_pct_same_industry"] >= 75).astype(float)
    return df


def summarize_deciles(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby("score_decile", dropna=False)
    summary = g.agg(
        rows=(C["area_code"], "size"),
        avg_total_score=("total_score", "mean"),
        median_total_score=("total_score", "median"),
        avg_current_sales=(C["current_sales"], "mean"),
        avg_next_sales=(C["next_sales"], "mean"),
        median_next_sales=(C["next_sales"], "median"),
        avg_next_growth_rate=("next_growth_rate", "mean"),
        median_next_growth_rate=("next_growth_rate", "median"),
        avg_next_log_growth=("next_log_growth", "mean"),
        median_next_log_growth=("next_log_growth", "median"),
        avg_excess_growth_vs_industry=("excess_growth_vs_industry_median", "mean"),
        avg_excess_log_growth_vs_industry=("excess_log_growth_vs_industry_median", "mean"),
        positive_growth_rate=("next_growth_positive", "mean"),
        beats_industry_median_growth_rate=("beats_industry_median_growth", "mean"),
        next_sales_top_quartile_same_industry_rate=("next_sales_top_quartile_same_industry", "mean"),
    ).reset_index()
    overall_next = df[C["next_sales"]].mean()
    overall_growth = df["next_growth_rate"].mean()
    summary["next_sales_lift_vs_all"] = summary["avg_next_sales"] / overall_next
    summary["growth_diff_vs_all"] = summary["avg_next_growth_rate"] - overall_growth
    return summary.round(6)


def summarize_quarter_deciles(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby([C["quarter"], "score_decile"], dropna=False)
    summary = g.agg(
        rows=(C["area_code"], "size"),
        avg_total_score=("total_score", "mean"),
        avg_next_sales=(C["next_sales"], "mean"),
        median_next_sales=(C["next_sales"], "median"),
        avg_next_growth_rate=("next_growth_rate", "mean"),
        avg_next_log_growth=("next_log_growth", "mean"),
        avg_excess_log_growth_vs_industry=("excess_log_growth_vs_industry_median", "mean"),
        positive_growth_rate=("next_growth_positive", "mean"),
        beats_industry_median_growth_rate=("beats_industry_median_growth", "mean"),
        next_sales_top_quartile_same_industry_rate=("next_sales_top_quartile_same_industry", "mean"),
    ).reset_index()
    return summary.round(6)


def summarize_overall_metrics(df: pd.DataFrame) -> pd.DataFrame:
    top = df[df["score_decile"] == 10]
    bottom = df[df["score_decile"] == 1]
    metrics = {
        "rows": len(df),
        "quarters": df[C["quarter"]].nunique(),
        "score_spearman_next_sales_log": safe_corr(df, "total_score", "next_sales_log", "spearman"),
        "score_spearman_next_growth_rate": safe_corr(df, "total_score", "next_growth_rate", "spearman"),
        "score_spearman_next_log_growth": safe_corr(df, "total_score", "next_log_growth", "spearman"),
        "score_spearman_excess_growth_vs_industry": safe_corr(df, "total_score", "excess_growth_vs_industry_median", "spearman"),
        "score_spearman_excess_log_growth_vs_industry": safe_corr(df, "total_score", "excess_log_growth_vs_industry_median", "spearman"),
        "score_spearman_next_sales_pct_same_industry": safe_corr(df, "total_score", "next_sales_pct_same_industry", "spearman"),
        "score_spearman_current_sales_log": safe_corr(df, "total_score", "current_sales_log", "spearman"),
        "top_decile_rows": len(top),
        "bottom_decile_rows": len(bottom),
        "top_decile_avg_next_sales": top[C["next_sales"]].mean(),
        "bottom_decile_avg_next_sales": bottom[C["next_sales"]].mean(),
        "top_vs_bottom_avg_next_sales_ratio": top[C["next_sales"]].mean() / bottom[C["next_sales"]].mean(),
        "top_decile_median_next_sales": top[C["next_sales"]].median(),
        "bottom_decile_median_next_sales": bottom[C["next_sales"]].median(),
        "top_vs_bottom_median_next_sales_ratio": top[C["next_sales"]].median() / bottom[C["next_sales"]].median(),
        "top_decile_avg_growth_rate": top["next_growth_rate"].mean(),
        "bottom_decile_avg_growth_rate": bottom["next_growth_rate"].mean(),
        "top_decile_avg_log_growth": top["next_log_growth"].mean(),
        "bottom_decile_avg_log_growth": bottom["next_log_growth"].mean(),
        "top_decile_avg_excess_log_growth_vs_industry": top["excess_log_growth_vs_industry_median"].mean(),
        "bottom_decile_avg_excess_log_growth_vs_industry": bottom["excess_log_growth_vs_industry_median"].mean(),
        "top_decile_positive_growth_rate": top["next_growth_positive"].mean(),
        "bottom_decile_positive_growth_rate": bottom["next_growth_positive"].mean(),
        "top_decile_beats_industry_median_growth_rate": top["beats_industry_median_growth"].mean(),
        "bottom_decile_beats_industry_median_growth_rate": bottom["beats_industry_median_growth"].mean(),
        "top_decile_next_sales_top_quartile_same_industry_rate": top["next_sales_top_quartile_same_industry"].mean(),
        "bottom_decile_next_sales_top_quartile_same_industry_rate": bottom["next_sales_top_quartile_same_industry"].mean(),
    }
    return pd.DataFrame([metrics]).round(6)


def summarize_by_industry(df: pd.DataFrame, group_col: str, min_rows: int = 500) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for key, group in df.groupby(group_col, dropna=False):
        if len(group) < min_rows:
            continue
        top = group[group["score_decile"] == 10]
        bottom = group[group["score_decile"] == 1]
        rows.append(
            {
                group_col: key,
                "rows": len(group),
                "quarters": group[C["quarter"]].nunique(),
                "score_spearman_next_sales_log": safe_corr(group, "total_score", "next_sales_log", "spearman"),
                "score_spearman_next_growth_rate": safe_corr(group, "total_score", "next_growth_rate", "spearman"),
                "score_spearman_next_log_growth": safe_corr(group, "total_score", "next_log_growth", "spearman"),
                "score_spearman_excess_log_growth_vs_industry": safe_corr(
                    group, "total_score", "excess_log_growth_vs_industry_median", "spearman"
                ),
                "score_spearman_next_sales_pct_same_industry": safe_corr(
                    group, "total_score", "next_sales_pct_same_industry", "spearman"
                ),
                "top_decile_avg_next_sales": top[C["next_sales"]].mean() if len(top) else np.nan,
                "bottom_decile_avg_next_sales": bottom[C["next_sales"]].mean() if len(bottom) else np.nan,
                "top_vs_bottom_avg_next_sales_ratio": (
                    top[C["next_sales"]].mean() / bottom[C["next_sales"]].mean()
                    if len(top) and len(bottom) and bottom[C["next_sales"]].mean() != 0
                    else np.nan
                ),
                "top_decile_positive_growth_rate": top["next_growth_positive"].mean() if len(top) else np.nan,
                "bottom_decile_positive_growth_rate": bottom["next_growth_positive"].mean() if len(bottom) else np.nan,
                "top_decile_beats_industry_median_growth_rate": top["beats_industry_median_growth"].mean()
                if len(top)
                else np.nan,
                "bottom_decile_beats_industry_median_growth_rate": bottom["beats_industry_median_growth"].mean()
                if len(bottom)
                else np.nan,
                "top_decile_next_sales_top_quartile_same_industry_rate": top[
                    "next_sales_top_quartile_same_industry"
                ].mean()
                if len(top)
                else np.nan,
            }
        )
    return pd.DataFrame(rows).sort_values(["rows"], ascending=False).round(6)


def component_metrics(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for key in COMPONENT_KEYS:
        col = f"{key}_score"
        rows.append(
            {
                "component": key,
                "spearman_next_sales_log": safe_corr(df, col, "next_sales_log", "spearman"),
                "spearman_next_growth_rate": safe_corr(df, col, "next_growth_rate", "spearman"),
                "spearman_next_log_growth": safe_corr(df, col, "next_log_growth", "spearman"),
                "spearman_excess_log_growth_vs_industry": safe_corr(
                    df, col, "excess_log_growth_vs_industry_median", "spearman"
                ),
                "spearman_next_sales_pct_same_industry": safe_corr(df, col, "next_sales_pct_same_industry", "spearman"),
                "spearman_current_sales_log": safe_corr(df, col, "current_sales_log", "spearman"),
                "mean_score": df[col].mean(),
                "median_score": df[col].median(),
            }
        )
    return pd.DataFrame(rows).round(6)


def recommend_weights(component_df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    floors = {
        "demand": 0.08,
        "sales": 0.12,
        "competition": 0.08,
        "accessibility": 0.06,
        "growth_stability": 0.06,
        "budget_risk": 0.03,
        "data_reliability": 0.05,
    }
    signals = {}
    for _, row in component_df.iterrows():
        key = row["component"]
        if key == "budget_risk":
            signals[key] = 0.0
            continue
        next_sales_signal = max(float(row["spearman_next_sales_log"]), 0.0) if not pd.isna(row["spearman_next_sales_log"]) else 0.0
        same_industry_signal = (
            max(float(row["spearman_next_sales_pct_same_industry"]), 0.0)
            if not pd.isna(row["spearman_next_sales_pct_same_industry"])
            else 0.0
        )
        log_growth_signal = max(float(row["spearman_next_log_growth"]), 0.0) if not pd.isna(row["spearman_next_log_growth"]) else 0.0
        excess_log_growth_signal = (
            max(float(row["spearman_excess_log_growth_vs_industry"]), 0.0)
            if not pd.isna(row["spearman_excess_log_growth_vs_industry"])
            else 0.0
        )
        signals[key] = (
            0.50 * next_sales_signal
            + 0.25 * same_industry_signal
            + 0.15 * log_growth_signal
            + 0.10 * excess_log_growth_signal
        )
    total_signal = sum(signals.values())
    available = 1.0 - sum(floors.values())
    empirical = {
        key: floors[key] + (available * (signals[key] / total_signal) if total_signal > 0 else available / len(COMPONENT_KEYS))
        for key in COMPONENT_KEYS
    }
    for prefix, current in {"BASE": BASE_WEIGHTS, **INDUSTRY_WEIGHT_OVERRIDES}.items():
        blended = {key: 0.55 * current.get(key, BASE_WEIGHTS[key]) + 0.45 * empirical[key] for key in COMPONENT_KEYS}
        total = sum(blended.values())
        for key in COMPONENT_KEYS:
            rows.append(
                {
                    "weight_set": prefix,
                    "component": key,
                    "current_weight": current.get(key, BASE_WEIGHTS[key]),
                    "empirical_signal_weight": empirical[key],
                    "recommended_weight": blended[key] / total,
                    "reason": "현재 운영 가중치 55%와 과거 백테스트 성과 신호 45%를 혼합. 예산은 월세/권리금 부재로 최저 가중치만 유지.",
                }
            )
    return pd.DataFrame(rows).round(6)


def apply_recommended_weights(df: pd.DataFrame, weights: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    pivot = weights.pivot(index="weight_set", columns="component", values="recommended_weight")
    for key in COMPONENT_KEYS:
        mapping = pivot[key].astype(float).to_dict()
        base_value = mapping["BASE"]
        df[f"{key}_recommended_weight"] = df["industry_prefix"].map(mapping).fillna(base_value).astype(float)
        df[f"{key}_recommended_weighted"] = df[f"{key}_score"] * df[f"{key}_recommended_weight"]
    df["recommended_raw_total_score"] = df[[f"{key}_recommended_weighted" for key in COMPONENT_KEYS]].sum(axis=1)
    df["recommended_total_score"] = np.where(
        df["data_reliability_score"] < 60,
        np.minimum(df["recommended_raw_total_score"], 65),
        df["recommended_raw_total_score"],
    )
    df["recommended_total_score"] = pd.to_numeric(df["recommended_total_score"], errors="coerce").round(4)
    df["recommended_score_decile"] = add_decile_by_quarter(df, "recommended_total_score")
    return df


def compare_score_versions(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    specs = [
        ("current", "total_score", "score_decile"),
        ("recommended", "recommended_total_score", "recommended_score_decile"),
    ]
    for label, score_col, decile_col in specs:
        top = df[df[decile_col] == 10]
        bottom = df[df[decile_col] == 1]
        rows.append(
            {
                "score_version": label,
                "score_spearman_next_sales_log": safe_corr(df, score_col, "next_sales_log", "spearman"),
                "score_spearman_next_sales_pct_same_industry": safe_corr(
                    df, score_col, "next_sales_pct_same_industry", "spearman"
                ),
                "score_spearman_next_log_growth": safe_corr(df, score_col, "next_log_growth", "spearman"),
                "score_spearman_excess_log_growth_vs_industry": safe_corr(
                    df, score_col, "excess_log_growth_vs_industry_median", "spearman"
                ),
                "top_vs_bottom_avg_next_sales_ratio": top[C["next_sales"]].mean() / bottom[C["next_sales"]].mean(),
                "top_decile_positive_growth_rate": top["next_growth_positive"].mean(),
                "bottom_decile_positive_growth_rate": bottom["next_growth_positive"].mean(),
                "top_decile_beats_industry_median_growth_rate": top["beats_industry_median_growth"].mean(),
                "bottom_decile_beats_industry_median_growth_rate": bottom["beats_industry_median_growth"].mean(),
                "top_decile_next_sales_top_quartile_same_industry_rate": top[
                    "next_sales_top_quartile_same_industry"
                ].mean(),
                "bottom_decile_next_sales_top_quartile_same_industry_rate": bottom[
                    "next_sales_top_quartile_same_industry"
                ].mean(),
            }
        )
    return pd.DataFrame(rows).round(6)


def write_markdown(
    *,
    row_count: int,
    valid_quarters: list[int],
    overall: pd.DataFrame,
    deciles: pd.DataFrame,
    component: pd.DataFrame,
    weights: pd.DataFrame,
) -> None:
    top_decile = deciles[deciles["score_decile"] == 10].iloc[0]
    bottom_decile = deciles[deciles["score_decile"] == 1].iloc[0]
    overall_row = overall.iloc[0].to_dict()
    best_components = component.sort_values("spearman_next_sales_log", ascending=False).head(3)
    weakest_growth = component.sort_values("spearman_excess_log_growth_vs_industry", ascending=True).head(3)

    lines = [
        "# 서울 상권 입지판단 점수 백테스트 및 가중치 재조정 기록",
        "",
        f"작성일: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "## 1. 왜 이 작업을 했나",
        "",
        "기존 입지판단 점수는 실제 데이터 백분위를 이용한 설명 가능한 1차 점수였다. 다만 그 점수가 다음분기 실제 매출이나 성장률과 맞는지는 별도로 검증되지 않았다. 이번 작업은 과거 모든 검증 가능 분기에 같은 점수식을 돌려서, 점수가 높은 그룹이 실제 다음분기 성과도 좋은지 확인하기 위해 수행했다.",
        "",
        "## 2. 사용한 데이터",
        "",
        f"- 원천 FeatureMart: `{FEATURE_MART.relative_to(ROOT)}`",
        f"- 검증 행 수: {row_count:,}행",
        f"- 검증 분기: {min(valid_quarters)} ~ {max(valid_quarters)}",
        "- 제외 분기: 최신분기 20261은 `다음분기_매출` 라벨이 없어서 검증에서 제외했다.",
        "- 예산 축: 사용자별 예산이 과거 전체 행에 존재하지 않으므로 백테스트에서는 현재 엔진의 무예산 처리와 같이 중립 50점으로 두었다.",
        "",
        "## 3. 검증 방법",
        "",
        "1. 각 과거 분기에서 현재 입지판단 점수식을 그대로 계산했다.",
        "2. 저장된 라벨을 그대로 믿지 않고, 같은 `상권_코드 + 서비스_업종_코드`의 바로 다음 분기 `당월_매출_금액`을 self-join으로 다시 붙였다.",
        "3. 분기별 점수 10분위(decile)를 만들었다.",
        "4. 상위 점수 그룹의 다음분기 매출 평균/중앙값이 하위 그룹보다 높은지 확인했다.",
        "5. 다음분기 성장률과 양의 성장 비율도 함께 봤다.",
        "6. 업종 대분류와 세부 업종별로 점수 성능이 흔들리는지 확인했다.",
        "7. 컴포넌트별 실제 성과 상관을 보고 가중치 재조정 후보를 만들었다.",
        "",
        "## 4. 전체 성능 요약",
        "",
        f"- 총점과 다음분기 매출 로그의 Spearman 상관: {overall_row['score_spearman_next_sales_log']:.4f}",
        f"- 총점과 다음분기 성장률의 Spearman 상관: {overall_row['score_spearman_next_growth_rate']:.4f}",
        f"- 총점과 현재 매출 로그의 Spearman 상관: {overall_row['score_spearman_current_sales_log']:.4f}",
        f"- 상위 10% 평균 다음분기 매출: {top_decile['avg_next_sales']:,.0f}원",
        f"- 하위 10% 평균 다음분기 매출: {bottom_decile['avg_next_sales']:,.0f}원",
        f"- 상위/하위 평균 다음분기 매출 비율: {overall_row['top_vs_bottom_avg_next_sales_ratio']:.2f}배",
        f"- 상위 10% 평균 성장률: {top_decile['avg_next_growth_rate']:.4f}",
        f"- 하위 10% 평균 성장률: {bottom_decile['avg_next_growth_rate']:.4f}",
        "",
        "해석: 점수는 다음분기 매출 규모와의 관계를 먼저 봐야 한다. 성장률은 작은 매출 모수에서 크게 튈 수 있으므로 보조 지표로 본다.",
        "",
        "## 5. 컴포넌트별 검증",
        "",
        "| 컴포넌트 | 다음분기 매출 로그 상관 | 다음분기 성장률 상관 | 현재 매출 로그 상관 |",
        "|---|---:|---:|---:|",
    ]
    for _, row in component.iterrows():
        lines.append(
            f"| {row['component']} | {row['spearman_next_sales_log']:.4f} | {row['spearman_next_growth_rate']:.4f} | {row['spearman_current_sales_log']:.4f} |"
        )
    lines.extend(
        [
            "",
            "다음분기 매출 규모와 가장 강하게 연결된 상위 컴포넌트:",
        ]
    )
    for _, row in best_components.iterrows():
        lines.append(f"- `{row['component']}`: {row['spearman_next_sales_log']:.4f}")
    lines.extend(["", "성장률 기준으로 약하거나 조심해야 할 컴포넌트:"])
    for _, row in weakest_growth.iterrows():
        lines.append(f"- `{row['component']}`: {row['spearman_next_growth_rate']:.4f}")
    lines.extend(
        [
            "",
            "## 6. 가중치 재조정 방식",
            "",
            "가중치는 한 번에 통계 신호만으로 바꾸지 않았다. 이유는 매출 규모와 성장률은 서로 다른 성격의 목표이고, 월세/권리금 같은 비용 타깃이 현재 없기 때문이다. 따라서 현재 운영 가중치 55%와 과거 백테스트 신호 45%를 혼합했다.",
            "",
            "- 다음분기 매출 로그 상관: 50%",
            "- 동업종 내 다음분기 매출 백분위 상관: 25%",
            "- 다음분기 로그성장 상관: 15%",
            "- 동업종 중앙값 대비 초과 로그성장 상관: 10%",
            "- 음수 상관은 좋은 신호로 보지 않고 0으로 처리",
            "- 예산/비용 축은 과거 사용자 예산이 없으므로 최저 가중치만 유지",
            "- 데이터 신뢰도는 예측축이라기보다 게이트 성격이므로 최저 가중치를 유지",
            "",
            "재조정 결과는 `location_score_backtest_recommended_weights.csv`에 저장했다. 운영 엔진에는 이 결과를 반영하되, 점수의 의미는 성장률 예측이 아니라 매출 체력형 입지 비교로 제한한다.",
            "",
            "## 7. 결론",
            "",
            "이번 백테스트의 목적은 현재 점수가 실제 과거 성과와 완전히 일치한다고 선언하는 것이 아니라, 어떤 축이 성과와 더 관련이 있는지 확인해 가중치를 더 근거 있게 조정하는 것이다. 운영 엔진에는 백테스트 반영 가중치를 적용했고, LLM 리포트에서는 이 점수를 성공확률이 아니라 매출 체력형 비교 점수로 설명해야 한다.",
        ]
    )
    (OUT_DIR / "서울상권_입지판단점수_백테스트_가중치재조정_보고서.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df_all = pd.read_parquet(FEATURE_MART, columns=NEEDED_COLUMNS)
    df_all["expected_next_quarter"] = expected_next_quarter(df_all[C["quarter"]])
    lookup = df_all[[C["quarter"], C["area_code"], C["industry_code"], C["current_sales"]]].rename(
        columns={C["quarter"]: "expected_next_quarter", C["current_sales"]: "strict_next_sales"}
    )
    df_all = df_all.merge(
        lookup,
        on=["expected_next_quarter", C["area_code"], C["industry_code"]],
        how="left",
        validate="many_to_one",
    )
    df_all["stored_next_sales"] = df_all[C["next_sales"]]
    df_all[C["next_sales"]] = df_all["strict_next_sales"]
    stored_label_nonnull = int(pd.to_numeric(df_all["stored_next_sales"], errors="coerce").notna().sum())
    strict_label_nonnull = int(pd.to_numeric(df_all["strict_next_sales"], errors="coerce").notna().sum())
    both_label_mask = df_all["stored_next_sales"].notna() & df_all["strict_next_sales"].notna()
    strict_label_mismatch = int(
        (pd.to_numeric(df_all.loc[both_label_mask, "stored_next_sales"], errors="coerce").round(0)
        != pd.to_numeric(df_all.loc[both_label_mask, "strict_next_sales"], errors="coerce").round(0)).sum()
    )
    df = df_all[pd.to_numeric(df_all[C["next_sales"]], errors="coerce").notna()].copy()
    df = df[pd.to_numeric(df[C["next_sales"]], errors="coerce") >= 0].copy()
    valid_quarters = sorted(int(q) for q in df[C["quarter"]].dropna().unique())

    scored = add_scores(df)
    component = component_metrics(scored)
    weights = recommend_weights(component)
    scored = apply_recommended_weights(scored, weights)
    score_versions = compare_score_versions(scored)
    keep_columns = [
        C["quarter"],
        "expected_next_quarter",
        C["area_code"],
        C["area_name"],
        C["industry_code"],
        C["industry_name"],
        C["district"],
        C["current_sales"],
        "stored_next_sales",
        "strict_next_sales",
        C["next_sales"],
        "next_sales_log",
        "next_growth_rate",
        "next_log_growth",
        "excess_growth_vs_industry_median",
        "excess_log_growth_vs_industry_median",
        "beats_industry_median_growth",
        "next_sales_pct_same_industry",
        "next_sales_top_quartile_same_industry",
        "next_growth_positive",
        "industry_prefix",
        "total_score",
        "raw_total_score",
        "score_decile",
        "recommended_total_score",
        "recommended_raw_total_score",
        "recommended_score_decile",
    ] + [f"{key}_score" for key in COMPONENT_KEYS] + [f"{key}_weight" for key in COMPONENT_KEYS]
    scored[keep_columns].to_parquet(OUT_DIR / "location_score_backtest_rows.parquet", index=False)
    scored[keep_columns].head(5000).to_csv(OUT_DIR / "location_score_backtest_rows_sample.csv", index=False, encoding="utf-8-sig")

    deciles = summarize_deciles(scored)
    quarter_deciles = summarize_quarter_deciles(scored)
    overall = summarize_overall_metrics(scored)
    by_prefix = summarize_by_industry(scored, "industry_prefix", min_rows=500)
    by_industry = summarize_by_industry(scored, C["industry_code"], min_rows=500)

    deciles.to_csv(OUT_DIR / "location_score_backtest_deciles.csv", index=False, encoding="utf-8-sig")
    quarter_deciles.to_csv(OUT_DIR / "location_score_backtest_quarter_deciles.csv", index=False, encoding="utf-8-sig")
    overall.to_csv(OUT_DIR / "location_score_backtest_overall_metrics.csv", index=False, encoding="utf-8-sig")
    by_prefix.to_csv(OUT_DIR / "location_score_backtest_industry_prefix_metrics.csv", index=False, encoding="utf-8-sig")
    by_industry.to_csv(OUT_DIR / "location_score_backtest_industry_metrics.csv", index=False, encoding="utf-8-sig")
    component.to_csv(OUT_DIR / "location_score_backtest_component_metrics.csv", index=False, encoding="utf-8-sig")
    weights.to_csv(OUT_DIR / "location_score_backtest_recommended_weights.csv", index=False, encoding="utf-8-sig")
    score_versions.to_csv(OUT_DIR / "location_score_backtest_score_version_comparison.csv", index=False, encoding="utf-8-sig")

    summary = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "row_count": int(len(scored)),
        "stored_label_nonnull": stored_label_nonnull,
        "strict_label_nonnull": strict_label_nonnull,
        "strict_label_mismatch_when_both_present": strict_label_mismatch,
        "valid_quarters": valid_quarters,
        "overall_metrics": overall.iloc[0].to_dict(),
        "output_dir": str(OUT_DIR.relative_to(ROOT)),
    }
    (OUT_DIR / "location_score_backtest_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_markdown(
        row_count=len(scored),
        valid_quarters=valid_quarters,
        overall=overall,
        deciles=deciles,
        component=component,
        weights=weights,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
