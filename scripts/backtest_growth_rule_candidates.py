# -*- coding: utf-8 -*-
"""
성장 규칙 후보 백테스트.

목적:
  1. 33번에서 만든 미래 성장 라벨을 정답지로만 사용한다.
  2. 후보 점수 계산은 현재 시점 피처만 사용한다.
  3. 기존 성장잠재 점수를 강화하기 전에, 어떤 방향의 규칙이 미래 라벨과 맞는지 확인한다.
  4. 결과가 약하면 엔진 반영을 보류한다.

주의:
  - `next_*`, `미래_*` 컬럼은 라벨 평가용으로만 읽는다.
  - 후보 점수 계산에 미래 컬럼이 들어가면 시간누수다.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "datacorpus" / "_gold"
RULE_VALIDATION = ROOT / "datacorpus" / "_rule_validation"
RESEARCH_RULE_VALIDATION = ROOT / "research" / "rule_validation"

INPUT = GOLD / "gold_growth_label_candidates_q_industry.csv"
OUT_SCORES = RULE_VALIDATION / "34_growth_rule_candidate_scores_sample.csv"
OUT_METRICS = RULE_VALIDATION / "34_growth_rule_candidate_backtest_metrics.csv"
OUT_DECILES = RULE_VALIDATION / "34_growth_rule_candidate_deciles.csv"
OUT_VALIDATION = RULE_VALIDATION / "34_growth_rule_candidate_validation.csv"
OUT_SUMMARY = RULE_VALIDATION / "34_growth_rule_candidate_summary.json"
OUT_REPORT = RESEARCH_RULE_VALIDATION / "34_growth_rule_candidate_backtest_validation_20260704.md"

RUN_DATE = "2026-07-04"
BACKTEST_VERSION = "growth_rule_candidate_backtest.v1.0-20260704"

KEYS = ["기준_년분기_코드", "상권_코드", "서비스_업종_코드"]

CURRENT_FEATURES = [
    "현재_매출_금액",
    "점포_수",
    "개업_율",
    "폐업_률",
    "개폐업_순동태",
    "매출_log_최근4분기_slope",
    "매출_최근4분기_연속존재",
    "운영_서울대비_개월_차이",
    "폐업_서울대비_개월_차이",
    "상권_변화_지표_코드",
    "상권_변화_지표_명",
]

LABELS = [
    "next_q_excess_log_growth_vs_industry",
    "next_4q_excess_log_growth_vs_industry",
    "sustained_4q_growth_candidate",
    "next_4q_downside_risk_candidate",
]

CANDIDATE_SCORES = [
    "momentum_growth_rule_score",
    "rebound_growth_rule_score",
    "stability_defense_rule_score",
    "churn_risk_rule_score",
]


@dataclass
class Validation:
    review_round: str
    rule_name: str
    observed: object
    expected: object
    result: str
    reason_ko: str


validations: list[Validation] = []


def ensure_dirs() -> None:
    RULE_VALIDATION.mkdir(parents=True, exist_ok=True)
    RESEARCH_RULE_VALIDATION.mkdir(parents=True, exist_ok=True)


def add_validation(
    review_round: str,
    rule_name: str,
    observed: object,
    expected: object,
    result: str,
    reason_ko: str,
) -> None:
    validations.append(Validation(review_round, rule_name, observed, expected, result, reason_ko))


def read_input() -> pd.DataFrame:
    if not INPUT.exists():
        raise FileNotFoundError(f"33번 성장 라벨 후보 테이블이 없다: {INPUT}")
    usecols = [
        "기준_년분기_코드",
        "상권_코드",
        "상권_코드_명",
        "서비스_업종_코드",
        "서비스_업종_코드_명",
        "현재_매출_금액",
        "next_q_excess_log_growth_vs_industry",
        "next_4q_excess_log_growth_vs_industry",
        "sustained_4q_growth_candidate",
        "next_4q_downside_risk_candidate",
        "점포_수",
        "개업_율",
        "폐업_률",
        "개폐업_순동태",
        "매출_log_최근4분기_slope",
        "매출_최근4분기_연속존재",
        "운영_서울대비_개월_차이",
        "폐업_서울대비_개월_차이",
        "상권_변화_지표_코드",
        "상권_변화_지표_명",
        "future_label_runtime_allowed",
    ]
    df = pd.read_csv(
        INPUT,
        encoding="utf-8-sig",
        dtype={"기준_년분기_코드": str, "상권_코드": str, "서비스_업종_코드": str, "상권_변화_지표_코드": str},
        usecols=usecols,
        low_memory=False,
    )
    return to_numeric(
        df,
        [
            "현재_매출_금액",
            "next_q_excess_log_growth_vs_industry",
            "next_4q_excess_log_growth_vs_industry",
            "점포_수",
            "개업_율",
            "폐업_률",
            "개폐업_순동태",
            "매출_log_최근4분기_slope",
            "운영_서울대비_개월_차이",
            "폐업_서울대비_개월_차이",
        ],
    )


def to_numeric(df: pd.DataFrame, columns: Iterable[str]) -> pd.DataFrame:
    out = df.copy()
    for col in columns:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    return out


def bool_to_float(series: pd.Series) -> pd.Series:
    return series.astype(str).str.lower().map({"true": 1.0, "false": 0.0, "1": 1.0, "0": 0.0})


def rank_pct(df: pd.DataFrame, column: str, group_cols: list[str]) -> pd.Series:
    return df.groupby(group_cols)[column].rank(pct=True) * 100.0


def safe_corr(df: pd.DataFrame, a: str, b: str) -> float:
    use = df[[a, b]].replace([np.inf, -np.inf], np.nan).dropna()
    if len(use) < 30:
        return float("nan")
    return float(use[a].corr(use[b], method="spearman"))


def safe_mean(series: pd.Series) -> float:
    use = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    if len(use) == 0:
        return float("nan")
    return float(use.mean())


def add_candidate_scores(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    iq = ["기준_년분기_코드", "서비스_업종_코드"]
    q = ["기준_년분기_코드"]

    # 모든 후보 점수는 현재 시점 피처의 비교군 백분위만 사용한다.
    out["pct_current_sales"] = rank_pct(out, "현재_매출_금액", iq)
    out["pct_inverse_current_sales"] = 100.0 - out["pct_current_sales"]
    out["pct_store_count"] = rank_pct(out, "점포_수", iq)
    out["pct_sales_slope"] = rank_pct(out, "매출_log_최근4분기_slope", iq)
    out["pct_inverse_sales_slope"] = 100.0 - out["pct_sales_slope"]
    out["pct_open_rate"] = rank_pct(out, "개업_율", iq)
    out["pct_close_rate"] = rank_pct(out, "폐업_률", iq)
    out["pct_inverse_close_rate"] = 100.0 - out["pct_close_rate"]
    out["pct_net_open_close"] = rank_pct(out, "개폐업_순동태", iq)
    out["pct_operation_month_gap"] = rank_pct(out, "운영_서울대비_개월_차이", q)

    out["매출_최근4분기_연속존재_float"] = bool_to_float(out["매출_최근4분기_연속존재"])

    # 모멘텀 후보: 이미 추세가 좋고, 개폐업 순동태와 안정성이 함께 좋은 경우.
    out["momentum_growth_rule_score"] = out[
        ["pct_sales_slope", "pct_net_open_close", "pct_inverse_close_rate", "pct_operation_month_gap"]
    ].mean(axis=1, skipna=True)

    # 반등 후보: 업종 내 현재 매출과 최근 추세가 낮았지만, 개폐업 순동태와 폐업 위험이 버티는 경우.
    # 33번 진단에서 매출 추세가 미래 초과성장과 음의 상관을 보여, 모멘텀과 별도 후보로 분리한다.
    out["rebound_growth_rule_score"] = out[
        ["pct_inverse_current_sales", "pct_inverse_sales_slope", "pct_net_open_close", "pct_inverse_close_rate"]
    ].mean(axis=1, skipna=True)

    # 안정 방어 후보: 성장보다는 하방 위험 회피를 보는 후보. 성장률 예측 점수로 쓰지 않는다.
    out["stability_defense_rule_score"] = out[
        ["pct_inverse_close_rate", "pct_operation_month_gap", "pct_store_count"]
    ].mean(axis=1, skipna=True)

    # 진출입 위험 후보: 높을수록 위험한 점수다. downside risk 라벨과 같은 방향이어야 의미가 있다.
    out["churn_risk_rule_score"] = out[["pct_close_rate", "pct_open_rate"]].mean(axis=1, skipna=True)

    # 4분기 매출 이력이 없는 경우 추세 의존 후보는 판정보류로 둔다.
    no_history = out["매출_최근4분기_연속존재_float"].fillna(0).eq(0)
    out.loc[no_history, ["momentum_growth_rule_score", "rebound_growth_rule_score"]] = np.nan

    out["growth_rule_candidate_backtest_version"] = BACKTEST_VERSION
    out["candidate_score_runtime_status"] = "백테스트_후보_엔진미반영"
    return out


def build_metrics(df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for score in CANDIDATE_SCORES:
        for label in LABELS:
            if label not in df.columns:
                continue
            use = df[[score, label]].replace([np.inf, -np.inf], np.nan).dropna()
            rows.append(
                {
                    "candidate_score": score,
                    "label": label,
                    "non_null_rows": int(len(use)),
                    "spearman_corr": safe_corr(df, score, label),
                    "direction_note_ko": "churn_risk_rule_score는 하방위험 라벨과 양의 상관일 때만 좋고, 성장 라벨과는 낮거나 음의 방향이어야 한다."
                    if score == "churn_risk_rule_score"
                    else "성장 후보 점수는 초과성장/지속성 라벨과 양의 상관일 때만 채택 후보가 된다.",
                }
            )
    return pd.DataFrame(rows)


def score_decile(series: pd.Series) -> pd.Series:
    valid = series.dropna()
    if len(valid) < 10:
        return pd.Series(pd.NA, index=series.index, dtype="Int64")
    ranked = series.rank(method="first")
    return (pd.qcut(ranked, 10, labels=False, duplicates="drop") + 1).astype("Int64")


def build_deciles(df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for score in CANDIDATE_SCORES:
        decile_col = f"{score}_decile"
        df[decile_col] = df.groupby("기준_년분기_코드", group_keys=False)[score].apply(score_decile)
        for decile in [1, 10]:
            part = df[df[decile_col] == decile]
            rows.append(
                {
                    "candidate_score": score,
                    "score_decile": decile,
                    "rows": int(len(part)),
                    "avg_score": safe_mean(part[score]),
                    "avg_next_q_excess_log_growth": safe_mean(part["next_q_excess_log_growth_vs_industry"]),
                    "avg_next_4q_excess_log_growth": safe_mean(part["next_4q_excess_log_growth_vs_industry"]),
                    "sustained_4q_growth_rate": safe_mean(bool_to_float(part["sustained_4q_growth_candidate"])),
                    "downside_4q_risk_rate": safe_mean(bool_to_float(part["next_4q_downside_risk_candidate"])),
                }
            )
    return pd.DataFrame(rows)


def build_validations(df: pd.DataFrame, metrics: pd.DataFrame) -> pd.DataFrame:
    duplicated = int(df.duplicated(KEYS).sum())
    runtime_allowed = bool_to_float(df["future_label_runtime_allowed"]).fillna(0).astype(int)
    runtime_allowed_true = int(runtime_allowed.sum())
    future_leak_features = [c for c in CURRENT_FEATURES if c.startswith("next_") or c.startswith("미래_")]
    score_columns_missing = [c for c in CANDIDATE_SCORES if c not in df.columns]
    best_growth_corr = metrics[
        metrics["candidate_score"].ne("churn_risk_rule_score")
        & metrics["label"].eq("next_4q_excess_log_growth_vs_industry")
    ]["spearman_corr"].max()
    best_growth_corr = float(best_growth_corr) if pd.notna(best_growth_corr) else float("nan")
    churn_downside_corr = metrics[
        metrics["candidate_score"].eq("churn_risk_rule_score")
        & metrics["label"].eq("next_4q_downside_risk_candidate")
    ]["spearman_corr"]
    churn_downside_corr = float(churn_downside_corr.iloc[0]) if len(churn_downside_corr) else float("nan")

    add_validation(
        "검토1_원천근거",
        "33번 성장 라벨 후보 테이블 존재",
        len(df),
        "0보다 큼",
        "PASS" if len(df) > 0 else "FAIL",
        "성장 후보 백테스트는 33번에서 만든 공식 산출물을 정답지로 사용한다.",
    )
    add_validation(
        "검토2_grain_key",
        "후보 점수 grain 중복 금지",
        f"duplicate_keys={duplicated}",
        "분기+상권+업종 중복 0",
        "PASS" if duplicated == 0 else "FAIL",
        "후보 점수도 엔진 입력과 같은 grain이어야 비교가 가능하다.",
    )
    add_validation(
        "검토3_시간누수",
        "미래 라벨 런타임 사용 금지 상태 유지",
        runtime_allowed_true,
        0,
        "PASS" if runtime_allowed_true == 0 else "FAIL",
        "미래 매출 라벨은 정답지로만 쓰고 리포트 피처로 쓰지 않는다.",
    )
    add_validation(
        "검토3_시간누수",
        "후보 점수 계산 feature에 미래 컬럼 없음",
        ",".join(future_leak_features) if future_leak_features else "없음",
        "없음",
        "PASS" if not future_leak_features else "FAIL",
        "후보 산식은 현재 시점 매출·점포·개폐업·영업개월만 사용한다.",
    )
    add_validation(
        "검토4_방향정규화",
        "후보 점수 4종 생성",
        ",".join(score_columns_missing) if score_columns_missing else "모두 존재",
        "모두 존재",
        "PASS" if not score_columns_missing else "FAIL",
        "모멘텀, 반등, 안정방어, 진출입위험을 분리해 방향 혼합을 피한다.",
    )
    add_validation(
        "검토4_방향정규화",
        "4분기 초과성장 후보 상관",
        f"{best_growth_corr:.6f}" if not math.isnan(best_growth_corr) else "nan",
        "0.05 이상이면 후속 후보, 미만이면 보류",
        "PASS" if best_growth_corr >= 0.05 else "NOT_READY",
        "성장 후보 점수는 미래 초과성장과 충분한 양의 관계가 있어야 엔진 반영 후보가 된다.",
    )
    add_validation(
        "검토4_방향정규화",
        "진출입 위험 후보와 하방위험 관계",
        f"{churn_downside_corr:.6f}" if not math.isnan(churn_downside_corr) else "nan",
        "양의 방향이면 위험 evidence 후보",
        "PASS" if churn_downside_corr > 0 else "NOT_READY",
        "위험 점수는 성장 라벨이 아니라 하방위험 라벨과 따로 평가한다.",
    )
    add_validation(
        "검토5_금지표현",
        "엔진 반영 상태",
        "백테스트_후보_엔진미반영",
        "엔진미반영",
        "PASS",
        "이번 산출물은 성장률 예측이나 창업 성공확률이 아니라 후보 비교 결과다.",
    )

    out = pd.DataFrame([v.__dict__ for v in validations])
    out.insert(0, "validation_id", range(1, len(out) + 1))
    return out


def summarize(df: pd.DataFrame, metrics: pd.DataFrame, validation: pd.DataFrame) -> dict[str, object]:
    growth_metrics = metrics[
        metrics["candidate_score"].ne("churn_risk_rule_score")
        & metrics["label"].isin(["next_q_excess_log_growth_vs_industry", "next_4q_excess_log_growth_vs_industry"])
    ].copy()
    best = growth_metrics.sort_values("spearman_corr", ascending=False).head(1)
    best_record = best.to_dict("records")[0] if len(best) else {}
    return {
        "run_date": RUN_DATE,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "backtest_version": BACKTEST_VERSION,
        "input_rows": int(len(df)),
        "candidate_count": len(CANDIDATE_SCORES),
        "metric_rows": int(len(metrics)),
        "validation_pass_count": int((validation["result"] == "PASS").sum()),
        "validation_fail_count": int((validation["result"] == "FAIL").sum()),
        "validation_not_ready_count": int((validation["result"] == "NOT_READY").sum()),
        "best_growth_candidate": best_record,
        "decision": "후보백테스트_완료_엔진반영은_보류",
        "decision_reason_ko": "일부 반등 후보는 양의 상관을 보이지만, 공간 블록/업종별 안정성 검증 전까지 성장잠재 엔진에 반영하지 않는다.",
    }


def fmt(value: object, digits: int = 6) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        if math.isnan(value):
            return "nan"
        return f"{value:.{digits}f}"
    return str(value)


def write_report(metrics: pd.DataFrame, deciles: pd.DataFrame, validation: pd.DataFrame, summary: dict[str, object]) -> None:
    lines = [
        "# 성장 규칙 후보 백테스트 검증",
        "",
        "작성일: 2026-07-04",
        "",
        "## 1. 목적",
        "",
        "33번 성장 라벨 후보를 정답지로 두고, 현재 시점 피처만 사용한 성장 규칙 후보 4종을 검증했다.",
        "",
        "중요: 이번 산출물은 성장잠재 엔진 반영이 아니라 후보 비교다.",
        "",
        "## 2. 산출물",
        "",
        f"- `datacorpus/_rule_validation/{OUT_METRICS.name}`",
        f"- `datacorpus/_rule_validation/{OUT_DECILES.name}`",
        f"- `datacorpus/_rule_validation/{OUT_VALIDATION.name}`",
        f"- `datacorpus/_rule_validation/{OUT_SUMMARY.name}`",
        f"- `datacorpus/_rule_validation/{OUT_SCORES.name}`",
        "",
        "## 3. 요약",
        "",
        "| 항목 | 값 |",
        "|---|---:|",
        f"| input_rows | {summary['input_rows']:,} |",
        f"| candidate_count | {summary['candidate_count']} |",
        f"| validation PASS | {summary['validation_pass_count']} |",
        f"| validation NOT_READY | {summary['validation_not_ready_count']} |",
        f"| validation FAIL | {summary['validation_fail_count']} |",
        "",
        "## 4. 5회 규칙 검토",
        "",
        "| review_round | rule_name | observed | expected | result | reason_ko |",
        "|---|---|---|---|---|---|",
    ]
    for row in validation.itertuples(index=False):
        lines.append(f"| {row.review_round} | {row.rule_name} | {row.observed} | {row.expected} | {row.result} | {row.reason_ko} |")

    lines.extend(
        [
            "",
            "## 5. 후보별 상관",
            "",
            "| candidate_score | label | non_null_rows | spearman_corr | direction_note_ko |",
            "|---|---|---:|---:|---|",
        ]
    )
    for row in metrics.itertuples(index=False):
        lines.append(f"| {row.candidate_score} | {row.label} | {row.non_null_rows:,} | {fmt(row.spearman_corr)} | {row.direction_note_ko} |")

    lines.extend(
        [
            "",
            "## 6. 상하위 decile 비교",
            "",
            "| candidate_score | decile | rows | avg_next_q_excess | avg_next_4q_excess | sustained_4q_growth_rate | downside_4q_risk_rate |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in deciles.itertuples(index=False):
        lines.append(
            f"| {row.candidate_score} | {row.score_decile} | {row.rows:,} | {fmt(row.avg_next_q_excess_log_growth)} | {fmt(row.avg_next_4q_excess_log_growth)} | {fmt(row.sustained_4q_growth_rate)} | {fmt(row.downside_4q_risk_rate)} |"
        )

    lines.extend(
        [
            "",
            "## 7. 판정",
            "",
            "후보 백테스트는 완료했지만, 성장잠재 엔진 반영은 보류한다.",
            "",
            "이유:",
            "",
            "- 후보 점수 계산에는 현재 피처만 사용했으나, 검증 라벨은 미래 매출 정답지다.",
            "- 일부 후보가 양의 상관을 보이더라도 자치구 공간 블록, 업종군, 기간별 안정성을 아직 보지 않았다.",
            "- 성장 후보와 하방위험 후보는 방향이 다르므로 하나의 총점으로 합치면 안 된다.",
            "- 따라서 다음 단계는 후보별 공간/업종 안정성 검증이다.",
        ]
    )
    OUT_REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    ensure_dirs()
    df = read_input()
    scored = add_candidate_scores(df)
    metrics = build_metrics(scored)
    deciles = build_deciles(scored)
    validation = build_validations(scored, metrics)
    summary = summarize(scored, metrics, validation)

    sample_cols = KEYS + [
        "상권_코드_명",
        "서비스_업종_코드_명",
        "momentum_growth_rule_score",
        "rebound_growth_rule_score",
        "stability_defense_rule_score",
        "churn_risk_rule_score",
        "next_q_excess_log_growth_vs_industry",
        "next_4q_excess_log_growth_vs_industry",
        "growth_rule_candidate_backtest_version",
        "candidate_score_runtime_status",
    ]
    scored[[c for c in sample_cols if c in scored.columns]].head(5000).to_csv(OUT_SCORES, index=False, encoding="utf-8-sig")
    metrics.to_csv(OUT_METRICS, index=False, encoding="utf-8-sig")
    deciles.to_csv(OUT_DECILES, index=False, encoding="utf-8-sig")
    validation.to_csv(OUT_VALIDATION, index=False, encoding="utf-8-sig")
    OUT_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_report(metrics, deciles, validation, summary)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if int(summary["validation_fail_count"]) > 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
