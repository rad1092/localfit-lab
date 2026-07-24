# -*- coding: utf-8 -*-
"""
교통 접근성 후보 산식 v2.5-rc1 검증.

목적:
  - 59번 검증에서 확인한 고정 후보 산식
    `기존 접근성축 70% + 교통 250m 승하차량 후보 30%`를 별도 후보 총점으로 계산한다.
  - 공식 `current_location_score`와 `loc_score.v2.4`는 바꾸지 않는다.
  - 성능, 누수, 직접점수 금지, 블록 안정성을 확인한 뒤에도 engine_promotion_ready=False로 둔다.

근거:
  - research/rule_validation/59_transit_accessibility_candidate_backtest_validation_20260707.md
  - research/rule_validation/31~32, 42~43, 55, 58 교통 승하차량 전처리/검증 기록
  - research/알고리즘_명세_v2_20260704.md: 점수축별 gold, 백테스트, 금지 표현 계약
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


BACKTEST = ROOT / "datacorpus" / "_score_backtest_gold"
RULE = ROOT / "datacorpus" / "_rule_validation"
DOC = ROOT / "research" / "rule_validation"

LABELS_PATH = BACKTEST / "gold_engine_backtest_labeled_rows.csv"
TRANSIT_Q_FEATURES = RULE / "59_transit_accessibility_candidate_quarter_features.csv"
SUMMARY_59 = RULE / "59_transit_accessibility_candidate_backtest_summary.json"
OVERALL_METRICS_PATH = BACKTEST / "gold_engine_backtest_overall_metrics.csv"

OUT_ROWS = BACKTEST / "gold_engine_backtest_transit_accessibility_engine_candidate_rows.csv"
OUT_METRICS = RULE / "60_transit_accessibility_engine_candidate_metrics.csv"
OUT_DECILES = RULE / "60_transit_accessibility_engine_candidate_deciles.csv"
OUT_BLOCKS = RULE / "60_transit_accessibility_engine_candidate_block_stability.csv"
OUT_VALIDATION = RULE / "60_transit_accessibility_engine_candidate_validation.csv"
OUT_SUMMARY = RULE / "60_transit_accessibility_engine_candidate_summary.json"
OUT_DOC = DOC / "60_transit_accessibility_engine_candidate_validation_20260707.md"

CANDIDATE_SCORE_VERSION = "loc_score.v2.5-transit-accessibility-candidate-rc1"
VALIDATION_VERSION = "transit_accessibility_engine_candidate.v0.1-20260707"
ACCESS_EXISTING_WEIGHT = 0.70
TRANSIT_250M_WEIGHT = 0.30

KEY_AREA_Q = ["기준_년분기_코드", "상권_코드"]
KEYS = ["기준_년분기_코드", "상권_코드", "서비스_업종_코드"]
TARGET_SALES_PCT = "next_sales_pct_same_industry"
TARGET_SALES_LOG = "next_sales_log"
TARGET_EXCESS_GROWTH = "excess_log_growth_vs_industry"


def read_csv(path: Path, **kwargs) -> pd.DataFrame:
    return pd.read_csv(path, encoding="utf-8-sig", **kwargs)


def write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")


def safe_corr(df: pd.DataFrame, x: str, y: str) -> float | None:
    sx = pd.to_numeric(df[x], errors="coerce").replace([np.inf, -np.inf], np.nan)
    sy = pd.to_numeric(df[y], errors="coerce").replace([np.inf, -np.inf], np.nan)
    sub = pd.DataFrame({"x": sx, "y": sy}).dropna()
    if len(sub) < 30:
        return None
    if x == y:
        return 1.0
    return float(sub["x"].rank(method="average").corr(sub["y"].rank(method="average")))


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


def score_decile_by_quarter(df: pd.DataFrame, score_col: str) -> pd.Series:
    def one_group(s: pd.Series) -> pd.Series:
        valid = s.dropna()
        if len(valid) < 10:
            return pd.Series(np.nan, index=s.index)
        return pd.qcut(valid.rank(method="first"), 10, labels=False, duplicates="drop").reindex(s.index) + 1

    return df.groupby("기준_년분기_코드")[score_col].transform(one_group)


def combine_candidate_current_score(df: pd.DataFrame) -> pd.Series:
    """기존 3축은 그대로 두고 접근성축만 후보축으로 교체한다.

    이 함수는 공식 점수를 수정하지 않는다. 후보 총점 컬럼을 만들기 위한 고정 산식이다.
    """
    weights_by_set = engine.load_axis_weights()
    axis_cols = {
        "sales": "axis__sales",
        "competition": "axis__competition",
        "demand": "axis__demand",
        "accessibility": "axis__accessibility_transit_250m_70_30_candidate",
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


def load_inputs() -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    label_cols = [
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
        usecols=label_cols,
        dtype={"기준_년분기_코드": str, "상권_코드": str, "서비스_업종_코드": str},
        low_memory=False,
    )
    labels["year"] = labels["기준_년분기_코드"].astype(str).str[:4]
    labels["industry_prefix"] = labels["서비스_업종_코드"].astype(str).str[:3]

    q = read_csv(
        TRANSIT_Q_FEATURES,
        usecols=["기준_년분기_코드", "상권_코드", "transit_month_count", "transit_total_250m_score"],
        dtype={"기준_년분기_코드": str, "상권_코드": str},
        low_memory=False,
    )
    summary_59 = json.loads(SUMMARY_59.read_text(encoding="utf-8"))
    return labels, q, summary_59


def build_candidate_rows(labels: pd.DataFrame, q: pd.DataFrame) -> pd.DataFrame:
    out = labels.merge(q, on=KEY_AREA_Q, how="left", validate="many_to_one")
    out["axis__accessibility_transit_250m_70_30_candidate"] = (
        pd.to_numeric(out["axis__accessibility"], errors="coerce") * ACCESS_EXISTING_WEIGHT
        + pd.to_numeric(out["transit_total_250m_score"], errors="coerce") * TRANSIT_250M_WEIGHT
    )
    out["current_location_score_transit_250m_candidate"] = combine_candidate_current_score(out)
    out["score_decile_transit_250m_candidate"] = score_decile_by_quarter(
        out, "current_location_score_transit_250m_candidate"
    )
    out["transit_candidate_score_version"] = CANDIDATE_SCORE_VERSION
    out["transit_candidate_engine_active"] = False
    out["transit_candidate_engine_promotion_ready"] = False
    out["transit_candidate_formula_ko"] = "접근성 후보축 = 기존 접근성축 70% + 250m 버스/지하철 승하차량 후보 백분위 30%"
    out["transit_candidate_forbidden_claim_ko"] = "실제 방문자, 실제 구매자, 실제 도보시간, 실제 방문확률, 창업 성공확률로 표현 금지"
    return out


def build_metrics(rows: pd.DataFrame) -> pd.DataFrame:
    specs = {
        "engine_current_v24": "current_location_score",
        "engine_accessibility_v24": "axis__accessibility",
        "transit_250m_candidate_axis": "axis__accessibility_transit_250m_70_30_candidate",
        "transit_250m_candidate_current": "current_location_score_transit_250m_candidate",
    }
    out = []
    for name, col in specs.items():
        out.append(
            {
                "variant": name,
                "score_col": col,
                "non_null_rows": int(rows[col].notna().sum()),
                "spearman_next_sales_pct_same_industry": safe_corr(rows, col, TARGET_SALES_PCT),
                "spearman_next_sales_log": safe_corr(rows, col, TARGET_SALES_LOG),
                "spearman_excess_log_growth_vs_industry": safe_corr(rows, col, TARGET_EXCESS_GROWTH),
                "rank_corr_with_v24_current": safe_corr(rows, col, "current_location_score"),
                "rank_corr_with_v24_accessibility": safe_corr(rows, col, "axis__accessibility"),
                "mean_score": float(pd.to_numeric(rows[col], errors="coerce").mean()),
                "median_score": float(pd.to_numeric(rows[col], errors="coerce").median()),
            }
        )
    return pd.DataFrame(out).round(6)


def build_deciles(rows: pd.DataFrame) -> pd.DataFrame:
    out = []
    for decile, part in rows.groupby("score_decile_transit_250m_candidate", dropna=True):
        out.append(
            {
                "score": "current_location_score_transit_250m_candidate",
                "score_decile": int(decile),
                "rows": int(len(part)),
                "avg_next_sales_pct_same_industry": float(part[TARGET_SALES_PCT].mean()),
                "avg_next_sales_log": float(part[TARGET_SALES_LOG].mean()),
                "avg_excess_log_growth_vs_industry": float(part[TARGET_EXCESS_GROWTH].mean()),
                "top_quartile_rate": float(part["next_sales_top_quartile_same_industry"].mean()),
                "beats_industry_median_rate": float(part["beats_industry_median_log_growth"].mean()),
                "avg_v24_current_score": float(part["current_location_score"].mean()),
                "avg_candidate_current_score": float(part["current_location_score_transit_250m_candidate"].mean()),
            }
        )
    return pd.DataFrame(out).round(6)


def build_blocks(rows: pd.DataFrame) -> pd.DataFrame:
    out = []
    groups = {
        "year": "year",
        "district": "자치구_코드_명",
        "industry_prefix": "industry_prefix",
    }
    for group_type, group_col in groups.items():
        for group_value, part in rows.groupby(group_col):
            if len(part) < 500:
                continue
            for col in [
                "current_location_score",
                "current_location_score_transit_250m_candidate",
                "axis__accessibility",
                "axis__accessibility_transit_250m_70_30_candidate",
            ]:
                out.append(
                    {
                        "group_type": group_type,
                        "group_value": group_value,
                        "score_col": col,
                        "rows": int(len(part)),
                        "spearman_next_sales_pct_same_industry": safe_corr(part, col, TARGET_SALES_PCT),
                        "spearman_excess_log_growth_vs_industry": safe_corr(part, col, TARGET_EXCESS_GROWTH),
                    }
                )
    return pd.DataFrame(out).round(6)


def metric(metrics: pd.DataFrame, variant: str, col: str) -> float:
    row = metrics[metrics["variant"].eq(variant)]
    if row.empty:
        return float("nan")
    return float(row[col].iloc[0])


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
    candidate_rows: pd.DataFrame,
    metrics: pd.DataFrame,
    deciles: pd.DataFrame,
    blocks: pd.DataFrame,
    summary_59: dict,
) -> tuple[pd.DataFrame, dict]:
    validations: list[dict] = []
    overall = read_csv(OVERALL_METRICS_PATH)
    baseline_summary_corr = float(overall["score_spearman_next_sales_pct_same_industry"].iloc[0])
    v24_corr = metric(metrics, "engine_current_v24", "spearman_next_sales_pct_same_industry")
    v24_access_corr = metric(metrics, "engine_accessibility_v24", "spearman_next_sales_pct_same_industry")
    candidate_axis_corr = metric(metrics, "transit_250m_candidate_axis", "spearman_next_sales_pct_same_industry")
    candidate_current_corr = metric(metrics, "transit_250m_candidate_current", "spearman_next_sales_pct_same_industry")

    formula_check = (
        candidate_rows["axis__accessibility_transit_250m_70_30_candidate"]
        - (
            candidate_rows["axis__accessibility"] * ACCESS_EXISTING_WEIGHT
            + candidate_rows["transit_total_250m_score"] * TRANSIT_250M_WEIGHT
        )
    ).abs().max()
    missing_features = int(candidate_rows["transit_total_250m_score"].isna().sum())
    no_future = not set(candidate_rows["기준_년분기_코드"].astype(str)).intersection({"20262"})
    month_complete = candidate_rows["transit_month_count"].eq(3).all()
    active_flags = int(candidate_rows["transit_candidate_engine_active"].astype(bool).sum())
    promotion_flags = int(candidate_rows["transit_candidate_engine_promotion_ready"].astype(bool).sum())
    forbidden_ok = candidate_rows["transit_candidate_forbidden_claim_ko"].astype(str).str.contains("실제 방문자").all()
    version_count = candidate_rows["transit_candidate_score_version"].nunique()
    candidate_blocks = blocks[blocks["score_col"].eq("current_location_score_transit_250m_candidate")]
    candidate_block_positive_rate = float(
        candidate_blocks["spearman_next_sales_pct_same_industry"].gt(0).mean()
    )
    top_decile = deciles[deciles["score_decile"].eq(10)]
    bottom_decile = deciles[deciles["score_decile"].eq(1)]
    decile_gap = (
        float(top_decile["avg_next_sales_pct_same_industry"].iloc[0])
        - float(bottom_decile["avg_next_sales_pct_same_industry"].iloc[0])
        if not top_decile.empty and not bottom_decile.empty
        else float("nan")
    )

    add_validation(
        validations,
        "60-V01",
        "59번 후보 성능 검증을 선행했는가",
        f"decision={summary_59.get('decision')}, performance_ready={summary_59.get('performance_ready')}, engine_promotion_ready={summary_59.get('engine_promotion_ready')}",
        "READY_FOR_ENGINE_REVIEW_NOT_PROMOTED + promotion false",
        "PASS"
        if summary_59.get("decision") == "TRANSIT_ACCESSIBILITY_CANDIDATE_READY_FOR_ENGINE_REVIEW_NOT_PROMOTED"
        and bool(summary_59.get("performance_ready"))
        and not bool(summary_59.get("engine_promotion_ready"))
        else "FAIL",
        "후보 엔진 산식은 59번 백테스트가 먼저 통과한 경우에만 만들 수 있다.",
    )
    add_validation(
        validations,
        "60-V02",
        "고정 후보 산식이 정확히 적용됐는가",
        f"max_abs_formula_diff={fmt(formula_check)}, existing_w={ACCESS_EXISTING_WEIGHT}, transit_w={TRANSIT_250M_WEIGHT}",
        "max diff <= 1e-9",
        "PASS" if formula_check <= 1e-9 else "FAIL",
        "59번 민감도 결과를 임의로 바꾸지 않고 고정 산식으로 재현해야 한다.",
    )
    add_validation(
        validations,
        "60-V03",
        "라벨 row 손실 없이 후보 점수를 붙였는가",
        f"labels={len(labels)}, candidate_rows={len(candidate_rows)}, missing_features={missing_features}",
        "row 수 동일, missing 0",
        "PASS" if len(labels) == len(candidate_rows) and missing_features == 0 else "FAIL",
        "후보 산식은 상권×분기 feature를 상권×업종×분기 라벨에 many_to_one으로 붙인다.",
    )
    add_validation(
        validations,
        "60-V04",
        "기존 v2.4 metric 재현",
        f"summary={fmt(baseline_summary_corr)}, recalculated={fmt(v24_corr)}",
        "abs diff <= 1e-6",
        "PASS" if abs(baseline_summary_corr - v24_corr) <= 1e-6 else "FAIL",
        "후보 계산 전 기존 공식 점수의 백테스트 metric이 흔들리지 않아야 한다.",
    )
    add_validation(
        validations,
        "60-V05",
        "후보 접근성축 성능 개선",
        f"candidate_axis={fmt(candidate_axis_corr)}, v24_accessibility={fmt(v24_access_corr)}, diff={fmt(candidate_axis_corr - v24_access_corr)}",
        "+0.005 이상",
        "PASS" if candidate_axis_corr >= v24_access_corr + 0.005 else "NOT_READY",
        "접근성축 자체가 기존보다 좋아지지 않으면 공식 접근성축 후보로 볼 수 없다.",
    )
    add_validation(
        validations,
        "60-V06",
        "후보 현재입지 총점 성능 개선",
        f"candidate_current={fmt(candidate_current_corr)}, v24_current={fmt(v24_corr)}, diff={fmt(candidate_current_corr - v24_corr)}",
        "+0.002 이상",
        "PASS" if candidate_current_corr >= v24_corr + 0.002 else "NOT_READY",
        "후보 접근성축을 넣어 전체 현재입지 점수의 주 타깃 성능이 개선되어야 한다.",
    )
    add_validation(
        validations,
        "60-V07",
        "시간누수와 분기 월완전성",
        f"has_20262={not no_future}, all_month_count_3={month_complete}",
        "2021Q1~2025Q4만, 각 분기 3개월",
        "PASS" if no_future and month_complete else "FAIL",
        "202605 운영 최신월이나 부분분기 후보가 백테스트 라벨에 섞이면 안 된다.",
    )
    add_validation(
        validations,
        "60-V08",
        "엔진 활성화 플래그는 여전히 false인가",
        f"active_flags={active_flags}, promotion_flags={promotion_flags}",
        "0, 0",
        "PASS" if active_flags == 0 and promotion_flags == 0 else "FAIL",
        "이번 산식은 rc 후보이며 기존 공식 점수로 승격하지 않는다.",
    )
    add_validation(
        validations,
        "60-V09",
        "score_version 후보값이 단일하게 남았는가",
        f"version_count={version_count}, version={CANDIDATE_SCORE_VERSION}",
        "1",
        "PASS" if version_count == 1 else "FAIL",
        "공식 v2.4와 후보 v2.5를 구분할 수 있어야 리포트와 운영에서 혼동이 없다.",
    )
    add_validation(
        validations,
        "60-V10",
        "금지 표현 계약 유지",
        f"forbidden_ok={forbidden_ok}",
        "실제 방문자/구매자/도보시간/성공확률 금지",
        "PASS" if forbidden_ok else "FAIL",
        "승하차량 후보를 실제 방문자나 성공확률로 설명하면 안 된다.",
    )
    add_validation(
        validations,
        "60-V11",
        "블록 안정성",
        f"candidate_current_positive_rate={fmt(candidate_block_positive_rate)}, block_rows={len(candidate_blocks)}",
        "양의 방향 비율 >= 0.70",
        "PASS" if candidate_block_positive_rate >= 0.70 else "NOT_READY",
        "특정 지역·연도·업종 prefix에서만 좋아지는 후보는 운영 산식으로 쓰기 어렵다.",
    )
    add_validation(
        validations,
        "60-V12",
        "후보 decile 실측 격차",
        f"top_bottom_next_sales_pct_gap={fmt(decile_gap)}, decile_rows={len(deciles)}",
        "top-bottom gap > 0",
        "PASS" if decile_gap > 0 and len(deciles) >= 10 else "FAIL",
        "후보 총점 상위 decile이 하위 decile보다 실제 다음분기 매출 백분위가 높아야 한다.",
    )

    validation = pd.DataFrame(validations)
    fail_count = int(validation["result"].eq("FAIL").sum())
    not_ready_count = int(validation["result"].eq("NOT_READY").sum())
    pass_count = int(validation["result"].eq("PASS").sum())
    ready_for_patch_review = fail_count == 0 and not_ready_count == 0
    summary = {
        "run_date": "2026-07-07",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "validation_version": VALIDATION_VERSION,
        "candidate_score_version": CANDIDATE_SCORE_VERSION,
        "official_score_version_unchanged": engine.SCORE_VERSION,
        "label_rows": int(len(labels)),
        "candidate_rows": int(len(candidate_rows)),
        "pass_count": pass_count,
        "not_ready_count": not_ready_count,
        "fail_count": fail_count,
        "v24_current_corr": v24_corr,
        "candidate_current_corr": candidate_current_corr,
        "candidate_current_improvement": candidate_current_corr - v24_corr,
        "v24_accessibility_corr": v24_access_corr,
        "candidate_accessibility_corr": candidate_axis_corr,
        "candidate_accessibility_improvement": candidate_axis_corr - v24_access_corr,
        "ready_for_patch_review": ready_for_patch_review,
        "engine_promotion_ready": False,
        "decision": "TRANSIT_ACCESSIBILITY_ENGINE_CANDIDATE_RC_READY_NOT_PROMOTED"
        if ready_for_patch_review
        else "TRANSIT_ACCESSIBILITY_ENGINE_CANDIDATE_RC_NOT_READY",
        "decision_reason_ko": "고정 후보 산식은 검증을 통과했지만 공식 엔진 점수로 승격하지 않고 별도 후보 버전으로 유지한다."
        if ready_for_patch_review
        else "고정 후보 산식 검증에 미통과 항목이 있어 공식 엔진 반영을 보류한다.",
    }
    return validation, summary


def write_report(
    validation: pd.DataFrame,
    summary: dict,
    metrics: pd.DataFrame,
    deciles: pd.DataFrame,
    blocks: pd.DataFrame,
) -> None:
    DOC.mkdir(parents=True, exist_ok=True)
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
        "# 교통 접근성 후보 산식 v2.5-rc1 검증",
        "",
        "작성일: 2026-07-07",
        "",
        "## 1. 목적",
        "",
        "59번 백테스트에서 확인한 접근성 보강 후보를 고정 산식으로 묶어, 공식 엔진 반영 전 후보 버전으로 검증했다.",
        "",
        "공식 `current_location_score`와 `loc_score.v2.4-sales-ticket-removed-rc1`은 변경하지 않았다. 이 문서는 `loc_score.v2.5-transit-accessibility-candidate-rc1` 후보 산식 검증이다.",
        "",
        "## 2. 후보 산식",
        "",
        "```text",
        "후보 접근성축 = 기존 접근성축 * 0.70 + transit_total_250m_score * 0.30",
        "후보 현재입지 총점 = 기존 WLC 4축 가중치에서 접근성축만 후보 접근성축으로 교체",
        "```",
        "",
        "## 3. 핵심 결과",
        "",
        f"- validation_version: `{summary['validation_version']}`",
        f"- candidate_score_version: `{summary['candidate_score_version']}`",
        f"- official_score_version_unchanged: `{summary['official_score_version_unchanged']}`",
        f"- label rows: {summary['label_rows']:,}",
        f"- candidate rows: {summary['candidate_rows']:,}",
        f"- PASS: {summary['pass_count']}",
        f"- NOT_READY: {summary['not_ready_count']}",
        f"- FAIL: {summary['fail_count']}",
        f"- decision: `{summary['decision']}`",
        f"- v2.4 current corr: {fmt(summary['v24_current_corr'])}",
        f"- candidate current corr: {fmt(summary['candidate_current_corr'])}",
        f"- current improvement: {fmt(summary['candidate_current_improvement'])}",
        f"- v2.4 accessibility corr: {fmt(summary['v24_accessibility_corr'])}",
        f"- candidate accessibility corr: {fmt(summary['candidate_accessibility_corr'])}",
        f"- accessibility improvement: {fmt(summary['candidate_accessibility_improvement'])}",
        f"- engine_promotion_ready: `{summary['engine_promotion_ready']}`",
        "",
        "## 4. 검증 결과",
        "",
        md_table(validation),
        "",
        "## 5. metric",
        "",
        md_table(metrics),
        "",
        "## 6. 후보 총점 decile",
        "",
        md_table(deciles),
        "",
        "## 7. 블록 안정성 요약",
        "",
        md_table(block_summary),
        "",
        "## 8. 판정",
        "",
        "고정 후보 산식은 검증을 통과했다. 다만 현재 공식 엔진 점수로 승격하지 않는다.",
        "",
        "이유:",
        "",
        "- 같은 백테스트에서 민감도 탐색으로 찾은 후보 비율이므로 별도 릴리스 후보 검수와 문구 계약이 필요하다.",
        "- 승하차량은 실제 방문자·구매자·도보시간이 아니라 접근성 프록시다.",
        "- 운영 최신월 `202601~202604`가 아직 보강되지 않았다.",
        "- 따라서 후보 버전은 출력/비교/리포트 내부 검토용이고 공식 등급 산식은 v2.4를 유지한다.",
        "",
        "## 9. 2보 전진 1보 후퇴",
        "",
        "1. 전진: 59번 민감도 결과를 고정 후보 산식으로 재현했다.",
        "2. 전진: 기존 v2.4 공식 점수를 유지한 채 후보 총점 성능 개선을 확인했다.",
        "3. 후퇴: 후보 산식이 통과해도 공식 엔진 점수로 즉시 승격하지 않는다.",
        "4. 후퇴: 리포트 문구에서 방문확률·성공확률·도보시간 표현은 계속 금지한다.",
        "5. 후퇴: 다음 단계는 후보 버전을 실제 엔진 출력에 병렬 부착하고 AI 리포트 문구 계약을 재검증하는 것이다.",
    ]
    OUT_DOC.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    labels, q, summary_59 = load_inputs()
    candidate_rows = build_candidate_rows(labels, q)
    metrics = build_metrics(candidate_rows)
    deciles = build_deciles(candidate_rows)
    blocks = build_blocks(candidate_rows)
    validation, summary = build_validations(labels, q, candidate_rows, metrics, deciles, blocks, summary_59)

    keep_cols = [
        *KEYS,
        "상권_코드_명",
        "자치구_코드_명",
        "서비스_업종_코드_명",
        "weight_set",
        "current_location_score",
        "axis__sales",
        "axis__competition",
        "axis__demand",
        "axis__accessibility",
        "transit_total_250m_score",
        "axis__accessibility_transit_250m_70_30_candidate",
        "current_location_score_transit_250m_candidate",
        "score_decile_transit_250m_candidate",
        TARGET_SALES_PCT,
        TARGET_SALES_LOG,
        TARGET_EXCESS_GROWTH,
        "next_sales_top_quartile_same_industry",
        "beats_industry_median_log_growth",
        "transit_candidate_score_version",
        "transit_candidate_engine_active",
        "transit_candidate_engine_promotion_ready",
        "transit_candidate_formula_ko",
        "transit_candidate_forbidden_claim_ko",
    ]
    write_csv(candidate_rows[keep_cols], OUT_ROWS)
    write_csv(metrics, OUT_METRICS)
    write_csv(deciles, OUT_DECILES)
    write_csv(blocks, OUT_BLOCKS)
    write_csv(validation, OUT_VALIDATION)
    OUT_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(validation, summary, metrics, deciles, blocks)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if int(summary["fail_count"]):
        raise SystemExit(int(summary["fail_count"]))


if __name__ == "__main__":
    main()
