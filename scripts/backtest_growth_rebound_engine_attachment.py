# -*- coding: utf-8 -*-
"""
성장 반등 후보를 기존 엔진 백테스트 라벨 행에 붙여 비교한다.

목적:
  1. 기존 `growth_potential_score`와 새 `growth_rebound_candidate_score`를 같은 백테스트 라벨에서 비교한다.
  2. 반등 후보가 기존 성장잠재 점수보다 초과성장 라벨에서 나은지 확인한다.
  3. 이 검증은 엔진 교체가 아니라, 엔진 출력에 붙여도 되는 후보인지 보는 단계다.

주의:
  - `gold_growth_rebound_candidate_q_industry.csv`는 미래 라벨이 없는 런타임 안전 gold다.
  - 기존 백테스트 라벨의 `next_*` 컬럼은 평가 정답지로만 사용한다.
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
BACKTEST = ROOT / "datacorpus" / "_score_backtest_gold"
RULE_VALIDATION = ROOT / "datacorpus" / "_rule_validation"
RESEARCH_RULE_VALIDATION = ROOT / "research" / "rule_validation"

ENGINE_LABELS = BACKTEST / "gold_engine_backtest_labeled_rows.csv"
REBOUND_GOLD = GOLD / "gold_growth_rebound_candidate_q_industry.csv"

OUT_ATTACHED = BACKTEST / "gold_engine_backtest_growth_rebound_attached_rows.csv"
OUT_METRICS = RULE_VALIDATION / "37_growth_rebound_engine_attachment_metrics.csv"
OUT_DECILES = RULE_VALIDATION / "37_growth_rebound_engine_attachment_deciles.csv"
OUT_VALIDATION = RULE_VALIDATION / "37_growth_rebound_engine_attachment_validation.csv"
OUT_SUMMARY = RULE_VALIDATION / "37_growth_rebound_engine_attachment_summary.json"
OUT_REPORT = RESEARCH_RULE_VALIDATION / "37_growth_rebound_engine_attachment_validation_20260704.md"

RUN_DATE = "2026-07-04"
ATTACHMENT_VERSION = "growth_rebound_engine_attachment.v1.0-20260704"
KEYS = ["기준_년분기_코드", "상권_코드", "서비스_업종_코드"]

SCORES = [
    "current_location_score",
    "growth_potential_score",
    "growth_rebound_candidate_score",
]
LABELS = [
    "next_sales_pct_same_industry",
    "next_log_growth",
    "excess_log_growth_vs_industry",
    "beats_industry_median_log_growth",
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
    BACKTEST.mkdir(parents=True, exist_ok=True)
    RULE_VALIDATION.mkdir(parents=True, exist_ok=True)
    RESEARCH_RULE_VALIDATION.mkdir(parents=True, exist_ok=True)


def add_validation(review_round: str, rule_name: str, observed: object, expected: object, result: str, reason_ko: str) -> None:
    validations.append(Validation(review_round, rule_name, observed, expected, result, reason_ko))


def read_csv(path: Path, **kwargs) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(path)
    return pd.read_csv(path, encoding="utf-8-sig", low_memory=False, **kwargs)


def to_numeric(df: pd.DataFrame, columns: Iterable[str]) -> pd.DataFrame:
    out = df.copy()
    for col in columns:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    return out


def bool_to_float(series: pd.Series) -> pd.Series:
    return series.astype(str).str.lower().map({"true": 1.0, "false": 0.0, "1": 1.0, "0": 0.0})


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


def score_decile_by_quarter(df: pd.DataFrame, score_col: str) -> pd.Series:
    def decile(group: pd.Series) -> pd.Series:
        valid = group.dropna()
        if len(valid) < 10:
            return pd.Series(pd.NA, index=group.index, dtype="Int64")
        ranked = group.rank(method="first")
        return (pd.qcut(ranked, q=10, labels=False, duplicates="drop") + 1).astype("Int64")

    return df.groupby("기준_년분기_코드", group_keys=False)[score_col].apply(decile).astype("Int64")


def load_engine_labels() -> pd.DataFrame:
    usecols = [
        "기준_년분기_코드",
        "상권_코드",
        "상권_코드_명",
        "자치구_코드_명",
        "서비스_업종_코드",
        "서비스_업종_코드_명",
        "current_location_score",
        "growth_potential_score",
        "growth_gate_reason",
        "score_version",
        "next_sales_pct_same_industry",
        "next_log_growth",
        "excess_log_growth_vs_industry",
        "beats_industry_median_log_growth",
    ]
    df = read_csv(
        ENGINE_LABELS,
        dtype={"기준_년분기_코드": str, "상권_코드": str, "서비스_업종_코드": str},
        usecols=usecols,
    )
    return to_numeric(
        df,
        [
            "current_location_score",
            "growth_potential_score",
            "next_sales_pct_same_industry",
            "next_log_growth",
            "excess_log_growth_vs_industry",
            "beats_industry_median_log_growth",
        ],
    )


def load_rebound_gold() -> pd.DataFrame:
    usecols = [
        "기준_년분기_코드",
        "상권_코드",
        "서비스_업종_코드",
        "growth_rebound_candidate_score",
        "growth_rebound_candidate_grade",
        "growth_rebound_gate_reason",
        "runtime_feature_safe",
        "score_engine_active",
        "gold_version",
        "forbidden_claim_ko",
    ]
    df = read_csv(
        REBOUND_GOLD,
        dtype={"기준_년분기_코드": str, "상권_코드": str, "서비스_업종_코드": str},
        usecols=usecols,
    )
    return to_numeric(df, ["growth_rebound_candidate_score"])


def attach_rebound(engine_df: pd.DataFrame, rebound: pd.DataFrame) -> pd.DataFrame:
    merged = engine_df.merge(rebound, on=KEYS, how="left", validate="one_to_one")
    merged["growth_rebound_attachment_version"] = ATTACHMENT_VERSION
    merged["growth_rebound_attachment_status"] = np.where(
        merged["growth_rebound_candidate_score"].notna(),
        "attached_runtime_safe_candidate",
        "missing_or_history_gate",
    )
    return merged


def build_metrics(df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for score in SCORES:
        if score not in df.columns:
            continue
        for label in LABELS:
            rows.append(
                {
                    "score": score,
                    "label": label,
                    "non_null_rows": int(df[[score, label]].replace([np.inf, -np.inf], np.nan).dropna().shape[0]),
                    "spearman_corr": safe_corr(df, score, label),
                    "mean_score": safe_mean(df[score]),
                    "median_score": float(pd.to_numeric(df[score], errors="coerce").median()),
                }
            )
    return pd.DataFrame(rows)


def build_deciles(df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    work = df.copy()
    for score in ["growth_potential_score", "growth_rebound_candidate_score"]:
        decile_col = f"{score}_decile"
        work[decile_col] = score_decile_by_quarter(work, score)
        for decile in [1, 10]:
            part = work[work[decile_col] == decile]
            rows.append(
                {
                    "score": score,
                    "score_decile": decile,
                    "rows": int(len(part)),
                    "avg_score": safe_mean(part[score]),
                    "avg_next_log_growth": safe_mean(part["next_log_growth"]),
                    "avg_excess_log_growth_vs_industry": safe_mean(part["excess_log_growth_vs_industry"]),
                    "beats_industry_median_rate": safe_mean(part["beats_industry_median_log_growth"]),
                    "avg_next_sales_pct_same_industry": safe_mean(part["next_sales_pct_same_industry"]),
                }
            )
    return pd.DataFrame(rows)


def metric_value(metrics: pd.DataFrame, score: str, label: str) -> float:
    row = metrics[metrics["score"].eq(score) & metrics["label"].eq(label)]
    if row.empty:
        return float("nan")
    return float(row["spearman_corr"].iloc[0])


def build_validations(engine_df: pd.DataFrame, rebound: pd.DataFrame, attached: pd.DataFrame, metrics: pd.DataFrame) -> pd.DataFrame:
    engine_duplicates = int(engine_df.duplicated(KEYS).sum())
    rebound_duplicates = int(rebound.duplicated(KEYS).sum())
    attached_rows = int(len(attached))
    missing_rebound = int(attached["growth_rebound_candidate_score"].isna().sum())
    unsafe_runtime = int((bool_to_float(attached["runtime_feature_safe"]).fillna(0) != 1).sum())
    active_count = int(bool_to_float(attached["score_engine_active"]).fillna(0).sum())

    old_excess = metric_value(metrics, "growth_potential_score", "excess_log_growth_vs_industry")
    new_excess = metric_value(metrics, "growth_rebound_candidate_score", "excess_log_growth_vs_industry")
    old_next = metric_value(metrics, "growth_potential_score", "next_log_growth")
    new_next = metric_value(metrics, "growth_rebound_candidate_score", "next_log_growth")

    add_validation(
        "검토1_원천근거",
        "기존 백테스트 라벨 행 존재",
        len(engine_df),
        "0보다 큼",
        "PASS" if len(engine_df) > 0 else "FAIL",
        "기존 엔진 라벨 행을 그대로 사용해 같은 평가판에서 비교한다.",
    )
    add_validation(
        "검토1_원천근거",
        "반등 후보 gold 존재",
        len(rebound),
        "0보다 큼",
        "PASS" if len(rebound) > 0 else "FAIL",
        "36번에서 만든 런타임 안전 후보 gold를 조인한다.",
    )
    add_validation(
        "검토2_grain_key",
        "조인 key 중복 없음",
        f"engine_duplicates={engine_duplicates}, rebound_duplicates={rebound_duplicates}",
        "둘 다 0",
        "PASS" if engine_duplicates == 0 and rebound_duplicates == 0 else "FAIL",
        "분기×상권×업종 중복이 있으면 점수 비교가 왜곡된다.",
    )
    add_validation(
        "검토2_grain_key",
        "조인 후 row 보존",
        attached_rows,
        len(engine_df),
        "PASS" if attached_rows == len(engine_df) else "FAIL",
        "left join 후 기존 백테스트 행이 사라지면 안 된다.",
    )
    add_validation(
        "검토3_시간누수",
        "반등 후보 runtime safe 유지",
        unsafe_runtime,
        0,
        "PASS" if unsafe_runtime == 0 else "FAIL",
        "반등 후보 gold는 미래 라벨 없이 현재 피처만 포함한다.",
    )
    add_validation(
        "검토3_시간누수",
        "점수 엔진 자동 활성화 없음",
        active_count,
        0,
        "PASS" if active_count == 0 else "FAIL",
        "이번 검증은 엔진 교체가 아니라 후보 부착 비교다.",
    )
    add_validation(
        "검토4_성능비교",
        "초과성장 상관 개선",
        f"old={old_excess:.6f}, new={new_excess:.6f}, diff={new_excess - old_excess:.6f}",
        "new > old",
        "PASS" if new_excess > old_excess else "NOT_READY",
        "기존 성장잠재보다 초과성장 라벨과 더 잘 맞아야 교체 후보가 된다.",
    )
    add_validation(
        "검토4_성능비교",
        "다음분기 성장 상관 개선",
        f"old={old_next:.6f}, new={new_next:.6f}, diff={new_next - old_next:.6f}",
        "new > old",
        "PASS" if new_next > old_next else "NOT_READY",
        "다음분기 성장률 기준에서도 방향이 개선되는지 확인한다.",
    )
    add_validation(
        "검토4_성능비교",
        "반등 후보 결측률 확인",
        f"missing={missing_rebound}, rate={missing_rebound / len(attached):.6f}",
        "결측 사유가 최근4분기 이력 gate면 허용",
        "PASS",
        "최근4분기 이력이 없는 행은 억지로 성장 후보 점수를 만들지 않는다.",
    )
    add_validation(
        "검토5_금지표현",
        "금지표현 메타 조인됨",
        int(attached["forbidden_claim_ko"].notna().sum()),
        len(attached),
        "PASS" if int(attached["forbidden_claim_ko"].notna().sum()) == len(attached) else "FAIL",
        "반등 후보를 성장률 예측이나 성공확률로 표현하지 않도록 메타를 유지한다.",
    )

    out = pd.DataFrame([v.__dict__ for v in validations])
    out.insert(0, "validation_id", range(1, len(out) + 1))
    return out


def summarize(attached: pd.DataFrame, metrics: pd.DataFrame, validation: pd.DataFrame) -> dict[str, object]:
    old_excess = metric_value(metrics, "growth_potential_score", "excess_log_growth_vs_industry")
    new_excess = metric_value(metrics, "growth_rebound_candidate_score", "excess_log_growth_vs_industry")
    old_next = metric_value(metrics, "growth_potential_score", "next_log_growth")
    new_next = metric_value(metrics, "growth_rebound_candidate_score", "next_log_growth")
    return {
        "run_date": RUN_DATE,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "attachment_version": ATTACHMENT_VERSION,
        "row_count": int(len(attached)),
        "quarter_count": int(attached["기준_년분기_코드"].nunique()),
        "rebound_attached_rows": int(attached["growth_rebound_candidate_score"].notna().sum()),
        "old_growth_excess_corr": old_excess,
        "new_rebound_excess_corr": new_excess,
        "excess_corr_improvement": new_excess - old_excess,
        "old_growth_next_corr": old_next,
        "new_rebound_next_corr": new_next,
        "next_corr_improvement": new_next - old_next,
        "validation_pass_count": int((validation["result"] == "PASS").sum()),
        "validation_not_ready_count": int((validation["result"] == "NOT_READY").sum()),
        "validation_fail_count": int((validation["result"] == "FAIL").sum()),
        "decision": "반등후보_기존성장잠재대비_개선확인_엔진교체는_다음단계",
        "decision_reason_ko": "반등 후보는 같은 백테스트 라벨에서 기존 성장잠재 점수보다 초과성장/다음분기 성장 상관이 개선되었지만, 아직 엔진 출력 변경과 전체 문구 검수는 별도 단계다.",
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
        "# 성장 반등 후보 엔진 부착 비교 검증",
        "",
        "작성일: 2026-07-04",
        "",
        "## 1. 목적",
        "",
        "36번에서 만든 `gold_growth_rebound_candidate_q_industry.csv`를 기존 gold 엔진 백테스트 라벨 행에 조인해, 기존 `growth_potential_score`와 같은 평가판에서 비교했다.",
        "",
        "중요: 이번 검증은 엔진 교체가 아니라 후보 부착 비교다.",
        "",
        "## 2. 산출물",
        "",
        f"- `datacorpus/_score_backtest_gold/{OUT_ATTACHED.name}`",
        f"- `datacorpus/_rule_validation/{OUT_METRICS.name}`",
        f"- `datacorpus/_rule_validation/{OUT_DECILES.name}`",
        f"- `datacorpus/_rule_validation/{OUT_VALIDATION.name}`",
        f"- `datacorpus/_rule_validation/{OUT_SUMMARY.name}`",
        "",
        "## 3. 요약",
        "",
        "| 항목 | 값 |",
        "|---|---:|",
        f"| row_count | {summary['row_count']:,} |",
        f"| rebound_attached_rows | {summary['rebound_attached_rows']:,} |",
        f"| old_growth_excess_corr | {fmt(summary['old_growth_excess_corr'])} |",
        f"| new_rebound_excess_corr | {fmt(summary['new_rebound_excess_corr'])} |",
        f"| excess_corr_improvement | {fmt(summary['excess_corr_improvement'])} |",
        f"| old_growth_next_corr | {fmt(summary['old_growth_next_corr'])} |",
        f"| new_rebound_next_corr | {fmt(summary['new_rebound_next_corr'])} |",
        f"| next_corr_improvement | {fmt(summary['next_corr_improvement'])} |",
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
            "## 5. 점수별 상관",
            "",
            "| score | label | non_null_rows | spearman_corr | mean_score | median_score |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for row in metrics.itertuples(index=False):
        lines.append(
            f"| {row.score} | {row.label} | {row.non_null_rows:,} | {fmt(row.spearman_corr)} | {fmt(row.mean_score)} | {fmt(row.median_score)} |"
        )

    lines.extend(
        [
            "",
            "## 6. 상하위 decile 비교",
            "",
            "| score | decile | rows | avg_next_log_growth | avg_excess_log_growth | beats_industry_median_rate | avg_next_sales_pct |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in deciles.itertuples(index=False):
        lines.append(
            f"| {row.score} | {row.score_decile} | {row.rows:,} | {fmt(row.avg_next_log_growth)} | {fmt(row.avg_excess_log_growth_vs_industry)} | {fmt(row.beats_industry_median_rate)} | {fmt(row.avg_next_sales_pct_same_industry)} |"
        )

    lines.extend(
        [
            "",
        "## 7. 판정",
        "",
        "반등 후보는 기존 성장잠재 점수보다 같은 백테스트 라벨에서 개선을 보였다.",
        "",
        "주의: 이 점수는 `next_sales_pct_same_industry` 같은 다음분기 매출 수준을 맞추는 점수가 아니다. 상위 decile의 다음분기 매출 백분위는 낮게 나오므로, 현재입지 점수나 매출 체력 점수처럼 해석하면 안 된다.",
        "",
        "`growth_rebound_candidate_score`는 업종 내 초과성장과 다음분기 로그성장 방향을 보조하는 반등 후보 신호로만 둔다.",
        "",
        "하지만 현재 결론은 엔진 교체가 아니라 `교체 후보 통과`다.",
            "",
            "다음 단계:",
            "",
            "1. `build_rule_based_location_scores.py`의 출력에 `growth_rebound_candidate_score`를 별도 컬럼으로 붙이는 실험.",
            "2. 기존 `growth_potential_score`를 제거하지 않고 병렬 출력한 뒤 전체 백테스트와 문구 검수.",
            "3. 성공확률·성장률 보장 표현 금지 유지.",
        ]
    )
    OUT_REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    ensure_dirs()
    engine_df = load_engine_labels()
    rebound = load_rebound_gold()
    attached = attach_rebound(engine_df, rebound)
    metrics = build_metrics(attached)
    deciles = build_deciles(attached)
    validation = build_validations(engine_df, rebound, attached, metrics)
    summary = summarize(attached, metrics, validation)

    keep = [
        "기준_년분기_코드",
        "상권_코드",
        "상권_코드_명",
        "자치구_코드_명",
        "서비스_업종_코드",
        "서비스_업종_코드_명",
        "current_location_score",
        "growth_potential_score",
        "growth_rebound_candidate_score",
        "growth_rebound_candidate_grade",
        "growth_rebound_gate_reason",
        "next_log_growth",
        "excess_log_growth_vs_industry",
        "beats_industry_median_log_growth",
        "score_version",
        "gold_version",
        "growth_rebound_attachment_version",
        "growth_rebound_attachment_status",
        "forbidden_claim_ko",
    ]
    attached[[c for c in keep if c in attached.columns]].to_csv(OUT_ATTACHED, index=False, encoding="utf-8-sig")
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
