# -*- coding: utf-8 -*-
"""
gold 기반 규칙 엔진 백데이터 검증.

검증 목표:
  1. gold 입력을 쓰는 현재 `engine.SCORE_VERSION`이 과거 다음분기 매출 수준을 선별하는지 확인한다.
  2. 성장잠재 점수는 별도 후보 점수로만 평가하고, 현재입지 점수와 섞지 않는다.
  3. SBDC 202603 스냅샷 같은 미래 프록시가 과거 백테스트에 들어가지 않는지 확인한다.
  4. 가중치 ±10%, ±20% OAT 민감도와 등급 안정성을 확인한다.
  5. 자치구 공간 블록별 성능을 확인해 특정 지역 편향만으로 성능이 난 것인지 점검한다.

근거:
  - research/알고리즘_스펙_v1_20260703.md §4: 백테스트, 공간 블록 CV, 가중치 민감도.
  - research/rule_validation/00_검증프로토콜_20260703.md: 누수·오조인·프록시 과장 금지.
  - research/methodology_validation_sources/: MV-CV1..4, MV-SA1..3.
  - research/rule_validation/23_gold_preprocessing_validation_20260704.md.
  - research/rule_validation/24_gold_based_score_engine_validation_20260704.md.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import build_rule_based_location_scores as engine  # noqa: E402


GOLD = ROOT / "datacorpus" / "_gold"
OUT_DIR = ROOT / "datacorpus" / "_score_backtest_gold"
QUARTER_DIR = OUT_DIR / "quarter_scores"
RESEARCH_RULE_VALIDATION = ROOT / "research" / "rule_validation"

RUN_DATE = "2026-07-04"
BACKTEST_VERSION = "gold_backtest.v1.0-20260704"
SCORE_VERSION = engine.SCORE_VERSION

KEYS = ["기준_년분기_코드", "상권_코드", "서비스_업종_코드"]
CURRENT_SCORE = "current_location_score"
GROWTH_SCORE = "growth_potential_score"
GROWTH_REBOUND_SCORE = "growth_rebound_candidate_score"
TARGET_NEXT_SALES = "next_sales"
TARGET_NEXT_SALES_PCT = "next_sales_pct_same_industry"
TARGET_NEXT_LOG_GROWTH = "next_log_growth"
TARGET_EXCESS_LOG_GROWTH = "excess_log_growth_vs_industry"


def ensure_dirs() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    QUARTER_DIR.mkdir(parents=True, exist_ok=True)
    RESEARCH_RULE_VALIDATION.mkdir(parents=True, exist_ok=True)


def prev_quarter(q: int) -> int:
    y, qq = divmod(int(q), 10)
    qq -= 1
    if qq == 0:
        y -= 1
        qq = 4
    return y * 10 + qq


def next_quarter(q: int) -> int:
    y, qq = divmod(int(q), 10)
    if qq < 4:
        return y * 10 + qq + 1
    return (y + 1) * 10 + 1


def safe_corr(df: pd.DataFrame, a: str, b: str, method: str = "spearman") -> float:
    use = df[[a, b]].replace([np.inf, -np.inf], np.nan).dropna()
    if len(use) < 30:
        return float("nan")
    return float(use[a].corr(use[b], method=method))


def score_decile_by_quarter(df: pd.DataFrame, score_col: str) -> pd.Series:
    def cut(group: pd.Series) -> pd.Series:
        valid = group.dropna()
        if len(valid) < 10:
            return pd.Series(pd.NA, index=group.index, dtype="Int64")
        ranked = group.rank(method="first")
        return (pd.qcut(ranked, q=10, labels=False, duplicates="drop") + 1).astype("Int64")

    return df.groupby("기준_년분기_코드", group_keys=False)[score_col].apply(cut).astype("Int64")


def grade_by_industry(df: pd.DataFrame, score_col: str) -> pd.Series:
    labels = ["E", "D", "C", "B", "A"]

    def grade(group: pd.Series) -> pd.Series:
        valid = group.dropna()
        if len(valid) < 5:
            return pd.Series(pd.NA, index=group.index, dtype="object")
        pct = valid.rank(pct=True, method="first")
        cats = pd.cut(pct, [0, 0.2, 0.4, 0.6, 0.8, 1.0], labels=labels, include_lowest=True)
        return cats.reindex(group.index).astype("object")

    return df.groupby("서비스_업종_코드", group_keys=False)[score_col].apply(grade)


def load_sales_labels() -> pd.DataFrame:
    sales = pd.read_csv(
        GOLD / "gold_sales_strength_q_industry.csv",
        encoding="utf-8-sig",
        dtype={"기준_년분기_코드": str, "상권_코드": str, "서비스_업종_코드": str},
        usecols=["기준_년분기_코드", "상권_코드", "서비스_업종_코드", "당월_매출_금액"],
        low_memory=False,
    )
    sales["당월_매출_금액"] = pd.to_numeric(sales["당월_매출_금액"], errors="coerce")

    current = sales.rename(columns={"당월_매출_금액": "current_sales"})
    nxt = sales.copy()
    nxt["기준_년분기_코드"] = nxt["기준_년분기_코드"].astype(int).map(prev_quarter).astype(str)
    nxt = nxt.rename(columns={"당월_매출_금액": TARGET_NEXT_SALES})
    labels = current.merge(nxt, on=KEYS, how="left", validate="one_to_one")
    labels = labels[labels[TARGET_NEXT_SALES].notna()].copy()

    labels["current_sales_log"] = np.log1p(labels["current_sales"])
    labels["next_sales_log"] = np.log1p(labels[TARGET_NEXT_SALES])
    labels[TARGET_NEXT_LOG_GROWTH] = labels["next_sales_log"] - labels["current_sales_log"]
    labels["next_growth_rate"] = np.where(
        labels["current_sales"] > 0,
        (labels[TARGET_NEXT_SALES] - labels["current_sales"]) / labels["current_sales"],
        np.nan,
    )
    iq = ["기준_년분기_코드", "서비스_업종_코드"]
    labels["industry_quarter_median_log_growth"] = labels.groupby(iq)[TARGET_NEXT_LOG_GROWTH].transform("median")
    labels[TARGET_EXCESS_LOG_GROWTH] = labels[TARGET_NEXT_LOG_GROWTH] - labels["industry_quarter_median_log_growth"]
    labels[TARGET_NEXT_SALES_PCT] = labels.groupby(iq)[TARGET_NEXT_SALES].rank(pct=True) * 100.0
    labels["next_sales_top_quartile_same_industry"] = (labels[TARGET_NEXT_SALES_PCT] >= 75).astype(float)
    labels["next_growth_positive"] = (labels["next_growth_rate"] > 0).astype(float)
    labels["beats_industry_median_log_growth"] = (labels[TARGET_EXCESS_LOG_GROWTH] > 0).astype(float)
    return labels


def valid_backtest_quarters(labels: pd.DataFrame) -> list[int]:
    demand_q = pd.read_csv(GOLD / "gold_demand_q_area.csv", encoding="utf-8-sig", usecols=["기준_년분기_코드"], dtype=str)
    competition_q = pd.read_csv(GOLD / "gold_competition_q_industry.csv", encoding="utf-8-sig", usecols=["기준_년분기_코드"], dtype=str)
    qset = set(labels["기준_년분기_코드"].astype(int))
    qset &= set(demand_q["기준_년분기_코드"].astype(int).unique())
    qset &= set(competition_q["기준_년분기_코드"].astype(int).unique())
    return sorted(qset)


def score_one_quarter(q: int, labels: pd.DataFrame, refresh: bool = False) -> Path:
    out = QUARTER_DIR / f"gold_engine_labeled_scores_{q}.csv"
    if out.exists() and not refresh:
        header = pd.read_csv(out, encoding="utf-8-sig", nrows=0).columns.tolist()
        use_cache = GROWTH_REBOUND_SCORE in header and "score_version" in header
        if use_cache:
            version_sample = pd.read_csv(out, encoding="utf-8-sig", usecols=["score_version"], nrows=20, dtype=str)
            use_cache = set(version_sample["score_version"].dropna().unique()) == {SCORE_VERSION}
        if use_cache:
            print(f"[cache] {q}: {out}", flush=True)
            return out
        print(f"[cache stale] {q}: score_version/성장반등 컬럼 불일치로 재계산", flush=True)

    print(f"[score] {q}: build_indicator_frame + score_frame", flush=True)
    base = engine.build_indicator_frame(q)
    scored = engine.score_frame(base)
    scored["기준_년분기_코드"] = scored["기준_년분기_코드"].astype(str)
    for c in ["상권_코드", "서비스_업종_코드"]:
        scored[c] = scored[c].astype(str)

    q_labels = labels[labels["기준_년분기_코드"] == str(q)]
    merged = scored.merge(q_labels, on=KEYS, how="inner", validate="one_to_one")
    merged["score_decile"] = score_decile_by_quarter(merged, CURRENT_SCORE)
    keep = [
        "기준_년분기_코드", "상권_코드", "상권_코드_명", "자치구_코드", "자치구_코드_명",
        "서비스_업종_코드", "서비스_업종_코드_명", "weight_set", "비교군_확대",
        CURRENT_SCORE, GROWTH_SCORE, "growth_gate_reason", "cost_risk_score", "data_reliability_score",
        GROWTH_REBOUND_SCORE, "growth_rebound_candidate_grade", "growth_rebound_gate_reason",
        "growth_rebound_candidate_status", "growth_rebound_runtime_feature_safe",
        "growth_rebound_score_engine_active", "growth_rebound_activation_required_ko",
        "conservative_score_owa", "axis__sales", "axis__competition", "axis__demand", "axis__accessibility",
        "rel__완전성", "rel__최신성", "rel__공간해상도", "rel__원천성", "rel__품질플래그",
        "grade", "decision_label", "score_version", "score_decile",
        "current_sales", TARGET_NEXT_SALES, "current_sales_log", "next_sales_log", TARGET_NEXT_LOG_GROWTH,
        "next_growth_rate", TARGET_EXCESS_LOG_GROWTH, TARGET_NEXT_SALES_PCT,
        "next_sales_top_quartile_same_industry", "next_growth_positive", "beats_industry_median_log_growth",
    ]
    keep = [c for c in keep if c in merged.columns]
    merged[keep].to_csv(out, index=False, encoding="utf-8-sig")
    print(f"[score] {q}: wrote {len(merged):,} labeled rows", flush=True)
    return out


def load_or_build_labeled_scores(quarters: list[int], labels: pd.DataFrame, refresh: bool = False) -> pd.DataFrame:
    paths = [score_one_quarter(q, labels, refresh=refresh) for q in quarters]
    frames = [
        pd.read_csv(
            p,
            encoding="utf-8-sig",
            dtype={"기준_년분기_코드": str, "상권_코드": str, "서비스_업종_코드": str},
            low_memory=False,
        )
        for p in paths
    ]
    df = pd.concat(frames, ignore_index=True)
    df.to_csv(OUT_DIR / "gold_engine_backtest_labeled_rows.csv", index=False, encoding="utf-8-sig")
    return df


def summarize_overall(df: pd.DataFrame) -> pd.DataFrame:
    top = df[df["score_decile"] == 10]
    bottom = df[df["score_decile"] == 1]
    metrics = {
        "score_version": SCORE_VERSION,
        "rows": len(df),
        "quarters": df["기준_년분기_코드"].nunique(),
        "districts": df["자치구_코드_명"].nunique(),
        "industries": df["서비스_업종_코드"].nunique(),
        "score_spearman_next_sales_log": safe_corr(df, CURRENT_SCORE, "next_sales_log"),
        "score_spearman_next_sales_pct_same_industry": safe_corr(df, CURRENT_SCORE, TARGET_NEXT_SALES_PCT),
        "score_spearman_next_log_growth": safe_corr(df, CURRENT_SCORE, TARGET_NEXT_LOG_GROWTH),
        "score_spearman_excess_log_growth_vs_industry": safe_corr(df, CURRENT_SCORE, TARGET_EXCESS_LOG_GROWTH),
        "growth_score_spearman_next_log_growth": safe_corr(df, GROWTH_SCORE, TARGET_NEXT_LOG_GROWTH),
        "growth_score_spearman_excess_log_growth": safe_corr(df, GROWTH_SCORE, TARGET_EXCESS_LOG_GROWTH),
        "top_decile_rows": len(top),
        "bottom_decile_rows": len(bottom),
        "top_decile_avg_next_sales": top[TARGET_NEXT_SALES].mean(),
        "bottom_decile_avg_next_sales": bottom[TARGET_NEXT_SALES].mean(),
        "top_vs_bottom_avg_next_sales_ratio": top[TARGET_NEXT_SALES].mean() / bottom[TARGET_NEXT_SALES].mean(),
        "top_decile_next_sales_top_quartile_rate": top["next_sales_top_quartile_same_industry"].mean(),
        "bottom_decile_next_sales_top_quartile_rate": bottom["next_sales_top_quartile_same_industry"].mean(),
        "top_decile_positive_growth_rate": top["next_growth_positive"].mean(),
        "bottom_decile_positive_growth_rate": bottom["next_growth_positive"].mean(),
        "growth_nonnull_rate": df[GROWTH_SCORE].notna().mean(),
        "reliability_min": pd.to_numeric(df["data_reliability_score"], errors="coerce").min(),
        "reliability_below_gate_rows": int((pd.to_numeric(df["data_reliability_score"], errors="coerce") < engine.PROVISIONAL["reliability_gate"]).sum()),
    }
    return pd.DataFrame([metrics]).round(6)


def summarize_components(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    components = {
        "sales": "axis__sales",
        "competition": "axis__competition",
        "demand": "axis__demand",
        "accessibility": "axis__accessibility",
        "cost_risk": "cost_risk_score",
        "data_reliability": "data_reliability_score",
        "growth_potential": GROWTH_SCORE,
        "growth_rebound_candidate": GROWTH_REBOUND_SCORE,
    }
    for name, col in components.items():
        if col not in df.columns:
            continue
        rows.append(
            {
                "component": name,
                "non_null_rows": int(pd.to_numeric(df[col], errors="coerce").notna().sum()),
                "spearman_next_sales_log": safe_corr(df, col, "next_sales_log"),
                "spearman_next_sales_pct_same_industry": safe_corr(df, col, TARGET_NEXT_SALES_PCT),
                "spearman_next_log_growth": safe_corr(df, col, TARGET_NEXT_LOG_GROWTH),
                "spearman_excess_log_growth_vs_industry": safe_corr(df, col, TARGET_EXCESS_LOG_GROWTH),
                "mean_score": pd.to_numeric(df[col], errors="coerce").mean(),
                "median_score": pd.to_numeric(df[col], errors="coerce").median(),
            }
        )
    return pd.DataFrame(rows).round(6)


def summarize_deciles(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby("score_decile", dropna=False)
    out = g.agg(
        rows=("상권_코드", "size"),
        avg_current_score=(CURRENT_SCORE, "mean"),
        avg_next_sales=(TARGET_NEXT_SALES, "mean"),
        median_next_sales=(TARGET_NEXT_SALES, "median"),
        avg_next_sales_pct=(TARGET_NEXT_SALES_PCT, "mean"),
        avg_next_log_growth=(TARGET_NEXT_LOG_GROWTH, "mean"),
        avg_excess_log_growth=(TARGET_EXCESS_LOG_GROWTH, "mean"),
        top_quartile_rate=("next_sales_top_quartile_same_industry", "mean"),
        positive_growth_rate=("next_growth_positive", "mean"),
        beats_industry_median_log_growth_rate=("beats_industry_median_log_growth", "mean"),
    ).reset_index()
    return out.round(6)


def summarize_spatial_blocks(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = []
    for district, g in df.groupby("자치구_코드_명", dropna=False):
        top = g[g["score_decile"] == 10]
        bottom = g[g["score_decile"] == 1]
        rows.append(
            {
                "spatial_block": district,
                "rows": len(g),
                "industries": g["서비스_업종_코드"].nunique(),
                "quarters": g["기준_년분기_코드"].nunique(),
                "spearman_next_sales_pct_same_industry": safe_corr(g, CURRENT_SCORE, TARGET_NEXT_SALES_PCT),
                "spearman_next_sales_log": safe_corr(g, CURRENT_SCORE, "next_sales_log"),
                "spearman_next_log_growth": safe_corr(g, CURRENT_SCORE, TARGET_NEXT_LOG_GROWTH),
                "top_vs_bottom_avg_next_sales_ratio": top[TARGET_NEXT_SALES].mean() / bottom[TARGET_NEXT_SALES].mean()
                if len(top) and len(bottom) and bottom[TARGET_NEXT_SALES].mean() else np.nan,
            }
        )
    block = pd.DataFrame(rows).round(6)
    summary = pd.DataFrame(
        [
            {
                "block_count": len(block),
                "median_spearman_next_sales_pct": block["spearman_next_sales_pct_same_industry"].median(),
                "min_spearman_next_sales_pct": block["spearman_next_sales_pct_same_industry"].min(),
                "max_spearman_next_sales_pct": block["spearman_next_sales_pct_same_industry"].max(),
                "blocks_with_positive_sales_pct_corr": int((block["spearman_next_sales_pct_same_industry"] > 0).sum()),
                "blocks_with_rows_ge_1000": int((block["rows"] >= 1000).sum()),
            }
        ]
    ).round(6)
    return block, summary


def scenario_score(df: pd.DataFrame, weights_by_set: dict[str, dict[str, float]], target_axis: str, pct: float, direction: int) -> pd.Series:
    axis_cols = {ax: f"axis__{ax}" for ax in engine.CURRENT_AXES}
    out = pd.Series(np.nan, index=df.index, dtype=float)
    for weight_set, idx in df.groupby("weight_set").groups.items():
        weights = weights_by_set.get(str(weight_set), weights_by_set.get("BASE"))
        if not weights:
            continue
        w = dict(weights)
        w[target_axis] = w[target_axis] * (1.0 + direction * pct)
        sub = df.loc[idx]
        values = sub[[axis_cols[ax] for ax in engine.CURRENT_AXES]].apply(pd.to_numeric, errors="coerce")
        for ax in engine.CURRENT_AXES:
            values[f"w__{ax}"] = w[ax]
        numerator = pd.Series(0.0, index=sub.index)
        denominator = pd.Series(0.0, index=sub.index)
        for ax in engine.CURRENT_AXES:
            v = values[axis_cols[ax]]
            mask = v.notna()
            numerator.loc[mask] += v.loc[mask] * w[ax]
            denominator.loc[mask] += w[ax]
        out.loc[idx] = numerator / denominator.replace(0, np.nan)
    return out.clip(0, 100)


def summarize_sensitivity(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    weights_by_set = engine.load_axis_weights()
    baseline_grade = grade_by_industry(df, CURRENT_SCORE)
    baseline_decile = score_decile_by_quarter(df, CURRENT_SCORE)
    rows = []
    scenario_frames = []
    for axis in engine.CURRENT_AXES:
        for pct in [0.10, 0.20]:
            for direction in [-1, 1]:
                scenario = f"{axis}_{'plus' if direction > 0 else 'minus'}_{int(pct * 100)}pct"
                sc = scenario_score(df, weights_by_set, axis, pct, direction)
                grade = grade_by_industry(df.assign(_scenario_score=sc), "_scenario_score")
                decile = score_decile_by_quarter(df.assign(_scenario_score=sc), "_scenario_score")
                rows.append(
                    {
                        "scenario": scenario,
                        "target_axis": axis,
                        "weight_delta_pct": direction * pct,
                        "spearman_next_sales_pct_same_industry": safe_corr(pd.DataFrame({"s": sc, "t": df[TARGET_NEXT_SALES_PCT]}), "s", "t"),
                        "spearman_next_sales_log": safe_corr(pd.DataFrame({"s": sc, "t": df["next_sales_log"]}), "s", "t"),
                        "rank_corr_with_baseline": safe_corr(pd.DataFrame({"s": sc, "b": df[CURRENT_SCORE]}), "s", "b"),
                        "mean_abs_score_shift": float((sc - pd.to_numeric(df[CURRENT_SCORE], errors="coerce")).abs().mean()),
                        "p95_abs_score_shift": float((sc - pd.to_numeric(df[CURRENT_SCORE], errors="coerce")).abs().quantile(0.95)),
                        "grade_change_rate": float((grade != baseline_grade).mean()),
                        "decile_change_rate": float((decile != baseline_decile).mean()),
                    }
                )
                scenario_frames.append(pd.DataFrame({"scenario": scenario, "score": sc}))
    detail = pd.DataFrame(rows).round(6)
    summary = pd.DataFrame(
        [
            {
                "scenario_count": len(detail),
                "min_rank_corr_with_baseline": detail["rank_corr_with_baseline"].min(),
                "max_grade_change_rate": detail["grade_change_rate"].max(),
                "max_decile_change_rate": detail["decile_change_rate"].max(),
                "max_p95_abs_score_shift": detail["p95_abs_score_shift"].max(),
                "min_spearman_next_sales_pct": detail["spearman_next_sales_pct_same_industry"].min(),
                "max_spearman_next_sales_pct": detail["spearman_next_sales_pct_same_industry"].max(),
            }
        ]
    ).round(6)
    return detail, summary


def build_rule_validations(df: pd.DataFrame, overall: pd.DataFrame, spatial_summary: pd.DataFrame, sensitivity_summary: pd.DataFrame, quarters: list[int]) -> pd.DataFrame:
    checks: list[dict] = []

    def add(rule: str, observed: object, expected: object, passed: bool, reason: str, status_if_fail: str = "FAIL") -> None:
        checks.append(
            {
                "rule_name": rule,
                "observed": observed,
                "expected": expected,
                "result": "PASS" if passed else status_if_fail,
                "reason_ko": reason,
            }
        )

    add(
        "백테스트 분기는 다음분기 라벨이 존재하는 분기만 사용",
        f"{min(quarters)}~{max(quarters)} / {len(quarters)}개",
        "20211~20254 / 20개",
        min(quarters) >= 20211 and max(quarters) <= 20254 and len(quarters) == 20,
        "20261처럼 다음분기 매출 라벨이 없는 분기는 평가에서 제외해야 한다.",
    )
    add(
        "SBDC 미래 스냅샷 누수 금지",
        int(engine.load_sbdc(20254).shape[0]),
        0,
        int(engine.load_sbdc(20254).shape[0]) == 0,
        "SBDC 202603 스냅샷은 과거 2025Q4 이하 백테스트에 투입하면 미래 정보 누수다.",
    )
    add(
        "점수 범위 0~100 유지",
        f"{df[CURRENT_SCORE].min():.2f}~{df[CURRENT_SCORE].max():.2f}",
        "0~100",
        df[CURRENT_SCORE].between(0, 100).all(),
        "WLC/백분위 정규화 점수는 해석 가능한 0~100 범위에 있어야 한다.",
    )
    add(
        "현재입지 점수는 다음분기 동일업종 매출 백분위와 양의 상관",
        float(overall.loc[0, "score_spearman_next_sales_pct_same_industry"]),
        "> 0",
        float(overall.loc[0, "score_spearman_next_sales_pct_same_industry"]) > 0,
        "현재입지 점수의 주 목적은 성장률 보장이 아니라 다음분기 매출 수준 후보 선별이다.",
    )
    add(
        "성장잠재 점수는 후보로 분리",
        float(overall.loc[0, "growth_nonnull_rate"]),
        "< 1",
        float(overall.loc[0, "growth_nonnull_rate"]) < 1,
        "4분기 연속 매출 이력이 없는 경우 성장잠재를 억지로 산출하지 않는다.",
    )
    add(
        "공간 블록 25개 자치구 검증",
        int(spatial_summary.loc[0, "block_count"]),
        25,
        int(spatial_summary.loc[0, "block_count"]) == 25,
        "공간 자기상관 과대평가를 피하기 위해 자치구 블록별 성능을 따로 본다.",
    )
    add(
        "가중치 민감도 시나리오 16개 실행",
        int(sensitivity_summary.loc[0, "scenario_count"]),
        16,
        int(sensitivity_summary.loc[0, "scenario_count"]) == 16,
        "4개 현재입지 축에 대해 ±10%, ±20% OAT 민감도를 모두 계산한다.",
    )
    add(
        "민감도 순위 안정성",
        float(sensitivity_summary.loc[0, "min_rank_corr_with_baseline"]),
        ">= 0.95",
        float(sensitivity_summary.loc[0, "min_rank_corr_with_baseline"]) >= 0.95,
        "가중치 섭동에도 후보 순위가 크게 흔들리면 가중치 확정 전 운영 사용을 보류해야 한다.",
        status_if_fail="CONDITIONAL_REVIEW",
    )
    add(
        "신뢰도 게이트 미만 행 없음",
        int(overall.loc[0, "reliability_below_gate_rows"]),
        0,
        int(overall.loc[0, "reliability_below_gate_rows"]) == 0,
        "신뢰도 게이트 미만 행은 판단 보류 라벨로 떨어져야 한다.",
    )
    return pd.DataFrame(checks)


def write_contract_audits(df: pd.DataFrame, labels: pd.DataFrame, quarters: list[int], sensitivity: pd.DataFrame) -> dict[str, Path]:
    """규칙 검증을 설명 가능한 감사 CSV로 분리한다.

    사용자 요구의 "기계적 검증이 아니라 규칙이 제대로 짜였는지"를 남기기 위한 보조 산출물이다.
    """
    outputs: dict[str, Path] = {}

    temporal = pd.DataFrame(
        [
            {
                "audit_item": "백테스트_분기범위",
                "observed": f"{min(quarters)}~{max(quarters)} / {len(quarters)}개",
                "expected": "다음분기 라벨이 있는 20211~20254 / 20개",
                "result": "PASS" if min(quarters) >= 20211 and max(quarters) <= 20254 and len(quarters) == 20 else "FAIL",
                "reason_ko": "20261은 라이브 최신분기지만 다음분기 매출 라벨이 없으므로 백테스트에서 제외한다.",
            },
            {
                "audit_item": "SBDC_202603_미래스냅샷_과거투입금지",
                "observed": int(engine.load_sbdc(20254).shape[0]),
                "expected": 0,
                "result": "PASS" if int(engine.load_sbdc(20254).shape[0]) == 0 else "FAIL",
                "reason_ko": "2026년 3월 SBDC 공간매칭 프록시는 2025년 이하 과거 평가에 넣으면 미래 정보 누수다.",
            },
            {
                "audit_item": "라벨_self_join_키",
                "observed": "+".join(KEYS),
                "expected": "기준분기+상권_코드+서비스_업종_코드",
                "result": "PASS",
                "reason_ko": "다음분기 매출 라벨은 같은 상권·업종의 다음 분기 매출만 사용한다.",
            },
        ]
    )
    path = OUT_DIR / "gold_engine_temporal_leakage_audit.csv"
    temporal.to_csv(path, index=False, encoding="utf-8-sig")
    outputs[path.name] = path

    forbidden_texts = [item["금지"] for item in engine.FORBIDDEN_CLAIMS]
    label_text = " ".join(str(x) for x in df["decision_label"].dropna().unique())
    forbidden = pd.DataFrame(
        [
            {
                "audit_item": "decision_label_금지표현_검사",
                "forbidden_terms": ";".join(forbidden_texts),
                "violation_count": sum(term in label_text for term in forbidden_texts),
                "result": "PASS" if not any(term in label_text for term in forbidden_texts) else "FAIL",
                "reason_ko": "등급 문구는 성공확률, 매출보장, 월세/권리금 반영 같은 표현을 쓰지 않는다.",
            },
            {
                "audit_item": "FORBIDDEN_CLAIMS_계약_존재",
                "forbidden_terms": ";".join(forbidden_texts),
                "violation_count": 0 if len(engine.FORBIDDEN_CLAIMS) >= 6 else 1,
                "result": "PASS" if len(engine.FORBIDDEN_CLAIMS) >= 6 else "FAIL",
                "reason_ko": "LLM 리포트와 UI가 따라야 하는 금지표현 계약을 evidence pack에 전달한다.",
            },
        ]
    )
    path = OUT_DIR / "gold_engine_forbidden_claim_audit.csv"
    forbidden.to_csv(path, index=False, encoding="utf-8-sig")
    outputs[path.name] = path

    join = pd.DataFrame(
        [
            {
                "audit_item": "백테스트_labeled_rows_키결측",
                "observed": int(df[KEYS].isna().any(axis=1).sum()),
                "expected": 0,
                "result": "PASS" if int(df[KEYS].isna().any(axis=1).sum()) == 0 else "FAIL",
                "reason_ko": "점수 입력은 이름이 아니라 코드 키로만 결합한다.",
            },
            {
                "audit_item": "백테스트_labeled_rows_grain중복",
                "observed": int(df.duplicated(KEYS).sum()),
                "expected": 0,
                "result": "PASS" if int(df.duplicated(KEYS).sum()) == 0 else "FAIL",
                "reason_ko": "분기+상권+업종이 중복되면 점수와 라벨이 부풀려진다.",
            },
            {
                "audit_item": "자치구_프록시_컬럼_존재",
                "observed": "자치구_코드" in df.columns,
                "expected": True,
                "result": "PASS" if "자치구_코드" in df.columns else "FAIL",
                "reason_ko": "생활이동·비용 프록시는 자치구 grain임을 표시해야 한다.",
            },
        ]
    )
    path = OUT_DIR / "gold_engine_join_key_audit.csv"
    join.to_csv(path, index=False, encoding="utf-8-sig")
    outputs[path.name] = path

    access = pd.read_csv(GOLD / "gold_accessibility_q_area.csv", encoding="utf-8-sig", usecols=["facility_missing_not_imputed"])
    missing = pd.DataFrame(
        [
            {
                "audit_item": "시설_미관측_0대체금지",
                "observed": int(access["facility_missing_not_imputed"].astype(str).str.lower().isin(["true", "1"]).sum()),
                "expected": "0보다 큼",
                "result": "PASS" if int(access["facility_missing_not_imputed"].astype(str).str.lower().isin(["true", "1"]).sum()) > 0 else "FAIL",
                "reason_ko": "집객시설 원천에 없는 상권-분기를 0시설로 단정하지 않고 결측 플래그로 남긴다.",
            },
            {
                "audit_item": "성장점수_이력부족_gate",
                "observed": int(df["growth_gate_reason"].notna().sum()),
                "expected": "0보다 큼",
                "result": "PASS" if int(df["growth_gate_reason"].notna().sum()) > 0 else "FAIL",
                "reason_ko": "4분기 연속 매출 이력이 없으면 성장잠재를 억지로 만들지 않는다.",
            },
            {
                "audit_item": "신뢰도_완전성_컬럼_존재",
                "observed": "rel__완전성" in df.columns,
                "expected": True,
                "result": "PASS" if "rel__완전성" in df.columns else "FAIL",
                "reason_ko": "결측은 임의 0점이 아니라 축 제외와 신뢰도 완전성 차원으로 처리한다.",
            },
        ]
    )
    path = OUT_DIR / "gold_engine_missing_handling_audit.csv"
    missing.to_csv(path, index=False, encoding="utf-8-sig")
    outputs[path.name] = path

    direction_matrix = RESEARCH_RULE_VALIDATION / "05_direction_normalization_matrix.csv"
    direction = pd.read_csv(direction_matrix, encoding="utf-8-sig")
    metric_column = direction.columns[0]
    active_direction = direction[direction[metric_column].astype(str).isin(engine.INDICATORS)].copy()
    evidence_only_direction = direction[~direction[metric_column].astype(str).isin(engine.INDICATORS)].copy()
    direction_audit = pd.DataFrame(
        [
            {
                "audit_item": "방향행렬_지표수",
                "observed": len(active_direction),
                "expected": len(engine.INDICATORS),
                "result": "PASS" if len(active_direction) == len(engine.INDICATORS) else "FAIL",
                "reason_ko": "점수에 들어가는 모든 지표는 방향·grain·근거ID를 가져야 한다.",
            },
            {
                "audit_item": "evidence_only_방향행렬_분리",
                "observed": int(len(evidence_only_direction)),
                "expected": "객단가 evidence-only 1행",
                "result": "PASS" if "객단가" in set(evidence_only_direction[metric_column].astype(str)) else "FAIL",
                "reason_ko": "객단가는 v2.4에서 점수 산식에서 제외됐지만 리포트 설명 근거로 보존하므로 active 지표 수 감사와 분리한다.",
            },
            {
                "audit_item": "비용형_방향_존재",
                "observed": int((direction["방향"] == "cost").sum()) if "방향" in direction.columns else 0,
                "expected": "0보다 큼",
                "result": "PASS" if "방향" in direction.columns and int((direction["방향"] == "cost").sum()) > 0 else "FAIL",
                "reason_ko": "비용형 지표는 100-백분위 반전 규칙을 적용해야 한다.",
            },
            {
                "audit_item": "민감도_시나리오_성능범위",
                "observed": f"{sensitivity['spearman_next_sales_pct_same_industry'].min():.6f}~{sensitivity['spearman_next_sales_pct_same_industry'].max():.6f}",
                "expected": "양의 상관 유지",
                "result": "PASS" if sensitivity["spearman_next_sales_pct_same_industry"].min() > 0 else "FAIL",
                "reason_ko": "가중치 섭동 후에도 현재입지 점수는 다음분기 동업종 매출 백분위와 양의 상관을 유지해야 한다.",
            },
        ]
    )
    path = OUT_DIR / "gold_engine_direction_effect_audit.csv"
    direction_audit.to_csv(path, index=False, encoding="utf-8-sig")
    outputs[path.name] = path

    label_contract = pd.DataFrame(
        [
            {
                "audit_item": "현재입지_성장잠재_분리",
                "observed": "current_location_score,growth_potential_score,growth_rebound_candidate_score",
                "expected": "별도 컬럼",
                "result": "PASS" if CURRENT_SCORE in df.columns and GROWTH_SCORE in df.columns and GROWTH_REBOUND_SCORE in df.columns else "FAIL",
                "reason_ko": "현재입지 점수, 레거시 성장잠재 점수, 성장 반등 후보 점수를 단일 성공확률로 합치지 않는다.",
            },
            {
                "audit_item": "성장잠재_후보성_상관진단",
                "observed": safe_corr(df, GROWTH_SCORE, TARGET_EXCESS_LOG_GROWTH),
                "expected": "진단값 기록",
                "result": "PASS",
                "reason_ko": "성장잠재는 검증 전 후보 점수이므로 성능 수치를 별도 기록하고 과장하지 않는다.",
            },
            {
                "audit_item": "의사결정라벨_성공확률아님",
                "observed": ";".join(sorted(str(x) for x in df["decision_label"].dropna().unique())),
                "expected": "허용 문구만",
                "result": "PASS" if not any(term in label_text for term in forbidden_texts) else "FAIL",
                "reason_ko": "decision_label은 상위 후보군/신중 검토 같은 허용 문구만 사용한다.",
            },
        ]
    )
    path = OUT_DIR / "gold_engine_label_contract_audit.csv"
    label_contract.to_csv(path, index=False, encoding="utf-8-sig")
    outputs[path.name] = path
    return outputs


def write_markdown(
    overall: pd.DataFrame,
    components: pd.DataFrame,
    deciles: pd.DataFrame,
    spatial_summary: pd.DataFrame,
    sensitivity_summary: pd.DataFrame,
    validations: pd.DataFrame,
    audit_outputs: dict[str, Path],
) -> None:
    o = overall.iloc[0]
    ss = sensitivity_summary.iloc[0]
    sp = spatial_summary.iloc[0]
    lines = [
        "# 25차 gold 기반 백데이터·민감도·공간블록 검증",
        "",
        f"작성일: {RUN_DATE}",
        f"백테스트 버전: `{BACKTEST_VERSION}`",
        f"점수 버전: `{SCORE_VERSION}`",
        "",
        "## 1. 목적",
        "",
        "gold 입력으로 전환한 규칙 기반 점수 엔진을 과거 다음분기 매출 라벨로 검증했다. 검증은 단순 실행 성공이 아니라 누수 방지, 프록시 과장 금지, 가중치 민감도, 공간 블록 안정성까지 포함한다.",
        "",
        "## 2. 전체 성능",
        "",
        "| 항목 | 값 |",
        "|---|---:|",
        f"| rows | {int(o['rows']):,} |",
        f"| quarters | {int(o['quarters'])} |",
        f"| districts | {int(o['districts'])} |",
        f"| industries | {int(o['industries'])} |",
        f"| Spearman: current score vs next sales pct same industry | {o['score_spearman_next_sales_pct_same_industry']:.6f} |",
        f"| Spearman: current score vs next sales log | {o['score_spearman_next_sales_log']:.6f} |",
        f"| Spearman: current score vs next log growth | {o['score_spearman_next_log_growth']:.6f} |",
        f"| Spearman: growth score vs excess log growth | {o['growth_score_spearman_excess_log_growth']:.6f} |",
        f"| top/bottom decile avg next sales ratio | {o['top_vs_bottom_avg_next_sales_ratio']:.6f} |",
        f"| top decile next-sales top-quartile rate | {o['top_decile_next_sales_top_quartile_rate']:.6f} |",
        f"| bottom decile next-sales top-quartile rate | {o['bottom_decile_next_sales_top_quartile_rate']:.6f} |",
        f"| growth score non-null rate | {o['growth_nonnull_rate']:.6f} |",
        f"| reliability below gate rows | {int(o['reliability_below_gate_rows'])} |",
        "",
        "## 3. 규칙 검증",
        "",
        "| rule | observed | expected | result |",
        "|---|---:|---:|---|",
    ]
    for _, row in validations.iterrows():
        lines.append(f"| {row['rule_name']} | {row['observed']} | {row['expected']} | {row['result']} |")

    lines.extend(
        [
            "",
            "## 4. 컴포넌트별 상관",
            "",
            "| component | non_null_rows | next_sales_pct_corr | next_sales_log_corr | next_log_growth_corr |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for _, row in components.iterrows():
        lines.append(
            f"| {row['component']} | {int(row['non_null_rows']):,} | {row['spearman_next_sales_pct_same_industry']:.6f} | {row['spearman_next_sales_log']:.6f} | {row['spearman_next_log_growth']:.6f} |"
        )

    lines.extend(
        [
            "",
            "## 5. 민감도 요약",
            "",
            "| 항목 | 값 |",
            "|---|---:|",
            f"| scenario_count | {int(ss['scenario_count'])} |",
            f"| min_rank_corr_with_baseline | {ss['min_rank_corr_with_baseline']:.6f} |",
            f"| max_grade_change_rate | {ss['max_grade_change_rate']:.6f} |",
            f"| max_decile_change_rate | {ss['max_decile_change_rate']:.6f} |",
            f"| max_p95_abs_score_shift | {ss['max_p95_abs_score_shift']:.6f} |",
            f"| min_spearman_next_sales_pct | {ss['min_spearman_next_sales_pct']:.6f} |",
            f"| max_spearman_next_sales_pct | {ss['max_spearman_next_sales_pct']:.6f} |",
            "",
            "## 6. 공간 블록 요약",
            "",
            "| 항목 | 값 |",
            "|---|---:|",
            f"| block_count | {int(sp['block_count'])} |",
            f"| median_spearman_next_sales_pct | {sp['median_spearman_next_sales_pct']:.6f} |",
            f"| min_spearman_next_sales_pct | {sp['min_spearman_next_sales_pct']:.6f} |",
            f"| max_spearman_next_sales_pct | {sp['max_spearman_next_sales_pct']:.6f} |",
            f"| blocks_with_positive_sales_pct_corr | {int(sp['blocks_with_positive_sales_pct_corr'])} |",
            "",
            "## 7. 2보 전진 1보 후퇴 검토",
            "",
            "1. 전진: gold 기반 엔진으로 20개 과거 분기 전체 백데이터를 다시 채점했다.",
            "2. 전진: 다음분기 동일업종 매출 백분위와 현재입지 점수의 상관을 핵심 라벨로 검증했다.",
            "3. 후퇴: SBDC 202603 스냅샷은 과거 백테스트에서 제거했다. 이를 넣으면 미래 정보 누수다.",
            "4. 재검토: 성장잠재 점수는 여전히 후보 점수다. 성장률/초과성장 상관을 별도 확인하고 현재입지와 섞지 않는다.",
            "5. 재검토: 공간 블록과 민감도 결과가 운영 확정의 최종 게이트다. PASS가 아닌 항목은 알고리즘 보강 대상이다.",
            "",
            "## 8. 산출 파일",
            "",
            "- `datacorpus/_score_backtest_gold/gold_engine_backtest_labeled_rows.csv`",
            "- `datacorpus/_score_backtest_gold/gold_engine_backtest_overall_metrics.csv`",
            "- `datacorpus/_score_backtest_gold/gold_engine_backtest_component_metrics.csv`",
            "- `datacorpus/_score_backtest_gold/gold_engine_backtest_deciles.csv`",
            "- `datacorpus/_score_backtest_gold/gold_engine_backtest_spatial_blocks.csv`",
            "- `datacorpus/_score_backtest_gold/gold_engine_backtest_sensitivity.csv`",
            "- `datacorpus/_score_backtest_gold/gold_engine_backtest_rule_validations.csv`",
            "",
            "## 9. 추가 규칙 감사 CSV",
            "",
            "| 파일 | 의미 |",
            "|---|---|",
        ]
    )
    for name in sorted(audit_outputs):
        lines.append(f"| `{name}` | 누수·금지문구·조인키·결측·방향·라벨 계약 중 하나를 별도 감사 |")
    (RESEARCH_RULE_VALIDATION / "25_gold_backtest_sensitivity_spatial_validation_20260704.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="gold 기반 규칙 엔진 백데이터/민감도/공간블록 검증")
    ap.add_argument("--refresh", action="store_true", help="분기별 캐시를 무시하고 다시 계산")
    ap.add_argument("--quarters", type=str, help="쉼표 구분 분기 목록. 생략 시 유효 전체 분기")
    args = ap.parse_args()

    ensure_dirs()
    labels = load_sales_labels()
    if args.quarters:
        quarters = [int(x.strip()) for x in args.quarters.split(",") if x.strip()]
    else:
        quarters = valid_backtest_quarters(labels)
    print(f"[backtest] quarters={quarters}", flush=True)

    df = load_or_build_labeled_scores(quarters, labels, refresh=args.refresh)
    overall = summarize_overall(df)
    components = summarize_components(df)
    deciles = summarize_deciles(df)
    spatial_blocks, spatial_summary = summarize_spatial_blocks(df)
    sensitivity, sensitivity_summary = summarize_sensitivity(df)
    validations = build_rule_validations(df, overall, spatial_summary, sensitivity_summary, quarters)
    audit_outputs = write_contract_audits(df, labels, quarters, sensitivity)

    overall.to_csv(OUT_DIR / "gold_engine_backtest_overall_metrics.csv", index=False, encoding="utf-8-sig")
    components.to_csv(OUT_DIR / "gold_engine_backtest_component_metrics.csv", index=False, encoding="utf-8-sig")
    deciles.to_csv(OUT_DIR / "gold_engine_backtest_deciles.csv", index=False, encoding="utf-8-sig")
    spatial_blocks.to_csv(OUT_DIR / "gold_engine_backtest_spatial_blocks.csv", index=False, encoding="utf-8-sig")
    spatial_summary.to_csv(OUT_DIR / "gold_engine_backtest_spatial_summary.csv", index=False, encoding="utf-8-sig")
    sensitivity.to_csv(OUT_DIR / "gold_engine_backtest_sensitivity.csv", index=False, encoding="utf-8-sig")
    sensitivity_summary.to_csv(OUT_DIR / "gold_engine_backtest_sensitivity_summary.csv", index=False, encoding="utf-8-sig")
    validations.to_csv(OUT_DIR / "gold_engine_backtest_rule_validations.csv", index=False, encoding="utf-8-sig")

    summary = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "backtest_version": BACKTEST_VERSION,
        "score_version": SCORE_VERSION,
        "quarters": quarters,
        "row_count": int(len(df)),
        "overall_metrics": overall.iloc[0].to_dict(),
        "sensitivity_summary": sensitivity_summary.iloc[0].to_dict(),
        "spatial_summary": spatial_summary.iloc[0].to_dict(),
        "rule_validation_counts": validations["result"].value_counts(dropna=False).to_dict(),
        "contract_audit_files": sorted(audit_outputs.keys()),
    }
    (OUT_DIR / "gold_engine_backtest_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    write_markdown(overall, components, deciles, spatial_summary, sensitivity_summary, validations, audit_outputs)

    print("[backtest] overall")
    print(overall.to_string(index=False))
    print("[backtest] rule validations")
    print(validations.to_string(index=False))
    return 0 if not (validations["result"] == "FAIL").any() else 1


if __name__ == "__main__":
    raise SystemExit(main())
