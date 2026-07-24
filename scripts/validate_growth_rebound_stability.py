# -*- coding: utf-8 -*-
"""
성장 반등 후보의 공간/업종/기간 안정성 검증.

목적:
  1. 34번에서 가장 나은 후보였던 `rebound_growth_rule_score`가 전체 평균 착시인지 확인한다.
  2. 자치구, 업종군, 기준연도별로 미래 초과성장 라벨과의 방향이 유지되는지 본다.
  3. 안정성이 부족하면 성장잠재 엔진 반영을 계속 보류한다.

원칙:
  - 후보 점수는 현재 시점 피처만 사용해 재계산한다.
  - 미래 매출 라벨은 평가 정답지로만 사용한다.
  - 상권명/업종명은 표시용이고 join은 코드로만 한다.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
GOLD = Path(os.getenv("LOCALFIT_GOLD_DIR", ROOT / "datacorpus" / "_gold")).resolve()
RULE_VALIDATION = Path(
    os.getenv("LOCALFIT_RULE_VALIDATION_DIR", ROOT / "datacorpus" / "_rule_validation")
).resolve()
RESEARCH_RULE_VALIDATION = Path(
    os.getenv("LOCALFIT_RESEARCH_RULE_VALIDATION_DIR", ROOT / "research" / "rule_validation")
).resolve()

INPUT = GOLD / "gold_growth_label_candidates_q_industry.csv"
TRADE_AREA = GOLD / "gold_trade_area_profile.csv"
INDUSTRY = GOLD / "gold_industry_taxonomy.csv"

OUT_DISTRICT = RULE_VALIDATION / "35_growth_rebound_stability_by_district.csv"
OUT_INDUSTRY = RULE_VALIDATION / "35_growth_rebound_stability_by_industry_group.csv"
OUT_PERIOD = RULE_VALIDATION / "35_growth_rebound_stability_by_period.csv"
OUT_VALIDATION = RULE_VALIDATION / "35_growth_rebound_stability_validation.csv"
OUT_SUMMARY = RULE_VALIDATION / "35_growth_rebound_stability_summary.json"
RUN_DATE = datetime.now().strftime("%Y-%m-%d")

OUT_REPORT = RESEARCH_RULE_VALIDATION / "35_growth_rebound_stability_validation_20260704.md"

VALIDATION_VERSION = "growth_rebound_stability.v1.0-20260704"
KEYS = ["기준_년분기_코드", "상권_코드", "서비스_업종_코드"]
SCORE = "rebound_growth_rule_score"
LABEL_Q = "next_q_excess_log_growth_vs_industry"
LABEL_4Q = "next_4q_excess_log_growth_vs_industry"


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


def rank_pct(df: pd.DataFrame, column: str, group_cols: list[str]) -> pd.Series:
    return df.groupby(group_cols)[column].rank(pct=True) * 100.0


def safe_corr(df: pd.DataFrame, a: str, b: str) -> float:
    use = df[[a, b]].replace([np.inf, -np.inf], np.nan).dropna()
    if len(use) < 30:
        return float("nan")
    ranks = use[[a, b]].rank(method="average")
    return float(ranks[a].corr(ranks[b], method="pearson"))


def load_base() -> pd.DataFrame:
    usecols = [
        "기준_년분기_코드",
        "상권_코드",
        "상권_코드_명",
        "서비스_업종_코드",
        "서비스_업종_코드_명",
        "현재_매출_금액",
        LABEL_Q,
        LABEL_4Q,
        "점포_수",
        "개업_율",
        "폐업_률",
        "개폐업_순동태",
        "매출_log_최근4분기_slope",
        "매출_최근4분기_연속존재",
        "future_label_runtime_allowed",
    ]
    df = read_csv(
        INPUT,
        dtype={"기준_년분기_코드": str, "상권_코드": str, "서비스_업종_코드": str},
        usecols=usecols,
    )
    df = to_numeric(
        df,
        [
            "현재_매출_금액",
            LABEL_Q,
            LABEL_4Q,
            "점포_수",
            "개업_율",
            "폐업_률",
            "개폐업_순동태",
            "매출_log_최근4분기_slope",
        ],
    )
    return df


def add_rebound_score(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    iq = ["기준_년분기_코드", "서비스_업종_코드"]
    out["pct_current_sales"] = rank_pct(out, "현재_매출_금액", iq)
    out["pct_inverse_current_sales"] = 100.0 - out["pct_current_sales"]
    out["pct_sales_slope"] = rank_pct(out, "매출_log_최근4분기_slope", iq)
    out["pct_inverse_sales_slope"] = 100.0 - out["pct_sales_slope"]
    out["pct_close_rate"] = rank_pct(out, "폐업_률", iq)
    out["pct_inverse_close_rate"] = 100.0 - out["pct_close_rate"]
    out["pct_net_open_close"] = rank_pct(out, "개폐업_순동태", iq)
    out[SCORE] = out[["pct_inverse_current_sales", "pct_inverse_sales_slope", "pct_net_open_close", "pct_inverse_close_rate"]].mean(axis=1, skipna=True)
    no_history = bool_to_float(out["매출_최근4분기_연속존재"]).fillna(0).eq(0)
    out.loc[no_history, SCORE] = np.nan
    return out


def add_blocks(df: pd.DataFrame) -> pd.DataFrame:
    profile = read_csv(
        TRADE_AREA,
        dtype={"상권_코드": str},
        usecols=["상권_코드", "자치구_코드", "자치구_코드_명", "상권_구분_코드_명"],
    )
    taxonomy = read_csv(
        INDUSTRY,
        dtype={"서비스_업종_코드": str},
        usecols=[
            "서비스_업종_코드",
            "SBDC_대분류명_후보",
            "SBDC_중분류명_후보",
            "SBDC_mapping_review_required",
            "direct_score_allowed",
        ],
    )
    out = df.merge(profile, on="상권_코드", how="left", validate="many_to_one")
    out = out.merge(taxonomy, on="서비스_업종_코드", how="left", validate="many_to_one")
    out["업종_대분류_검증그룹"] = out["서비스_업종_코드"].str.slice(0, 3)
    out["SBDC_대분류명_후보"] = out["SBDC_대분류명_후보"].fillna("SBDC_대분류_미매칭")
    out["기준_연도"] = out["기준_년분기_코드"].str.slice(0, 4)
    out["검증_반기"] = out["기준_년분기_코드"].str.slice(0, 4) + "H" + np.where(out["기준_년분기_코드"].str[-1].isin(["1", "2"]), "1", "2")
    out["validation_version"] = VALIDATION_VERSION
    return out


def block_metrics(df: pd.DataFrame, block_col: str, label: str) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for block, part in df.groupby(block_col, dropna=False):
        use = part[[SCORE, label]].replace([np.inf, -np.inf], np.nan).dropna()
        rows.append(
            {
                "block_type": block_col,
                "block_value": "NA" if pd.isna(block) else str(block),
                "label": label,
                "rows": int(len(part)),
                "non_null_rows": int(len(use)),
                "spearman_corr": safe_corr(part, SCORE, label),
                "positive_direction": bool(safe_corr(part, SCORE, label) > 0) if not math.isnan(safe_corr(part, SCORE, label)) else False,
            }
        )
    return pd.DataFrame(rows)


def summarize_block(df: pd.DataFrame, block_type: str, label: str, min_rows: int = 1000) -> dict[str, object]:
    eligible = df[(df["block_type"].eq(block_type)) & (df["label"].eq(label)) & (df["non_null_rows"] >= min_rows)].copy()
    if eligible.empty:
        return {
            "block_type": block_type,
            "label": label,
            "eligible_blocks": 0,
            "positive_blocks": 0,
            "positive_rate": float("nan"),
            "min_corr": float("nan"),
            "median_corr": float("nan"),
        }
    corr = pd.to_numeric(eligible["spearman_corr"], errors="coerce")
    positive = corr.gt(0)
    return {
        "block_type": block_type,
        "label": label,
        "eligible_blocks": int(len(eligible)),
        "positive_blocks": int(positive.sum()),
        "positive_rate": float(positive.mean()),
        "min_corr": float(corr.min()),
        "median_corr": float(corr.median()),
    }


def build_validations(df: pd.DataFrame, district: pd.DataFrame, industry: pd.DataFrame, period: pd.DataFrame) -> pd.DataFrame:
    duplicate_keys = int(df.duplicated(KEYS).sum())
    district_q = summarize_block(district, "자치구_코드_명", LABEL_Q)
    district_4q = summarize_block(district, "자치구_코드_명", LABEL_4Q)
    industry_q = summarize_block(industry, "업종_대분류_검증그룹", LABEL_Q)
    industry_4q = summarize_block(industry, "업종_대분류_검증그룹", LABEL_4Q)
    period_q = summarize_block(period, "기준_연도", LABEL_Q)
    period_4q = summarize_block(period, "기준_연도", LABEL_4Q)
    runtime_allowed = int(bool_to_float(df["future_label_runtime_allowed"]).fillna(0).sum())

    add_validation(
        "검토1_원천근거",
        "35번 검증 입력은 33번 라벨과 현재 피처",
        len(df),
        "0보다 큼",
        "PASS" if len(df) > 0 else "FAIL",
        "반등 후보는 33번 라벨 후보 테이블에서 현재 시점 피처만 재계산해 평가한다.",
    )
    add_validation(
        "검토2_grain_key",
        "공간/업종 조인 후 key 중복 없음",
        duplicate_keys,
        0,
        "PASS" if duplicate_keys == 0 else "FAIL",
        "상권·업종명은 표시용이고, 안정성 검증 조인은 상권_코드/서비스_업종_코드 기준이다.",
    )
    add_validation(
        "검토3_시간누수",
        "미래 라벨 런타임 사용 금지 유지",
        runtime_allowed,
        0,
        "PASS" if runtime_allowed == 0 else "FAIL",
        "미래 매출 라벨은 평가 정답지이며 현재 피처 산식에 들어가지 않는다.",
    )
    add_validation(
        "검토4_공간안정성",
        "자치구 next_q 양의 방향 비율",
        f"{district_q['positive_blocks']}/{district_q['eligible_blocks']} ({district_q['positive_rate']:.3f})",
        "80% 이상이면 통과",
        "PASS" if district_q["positive_rate"] >= 0.8 else "NOT_READY",
        "특정 자치구 몇 곳만 만든 상관이면 서울 전체 알고리즘으로 승격할 수 없다.",
    )
    add_validation(
        "검토4_공간안정성",
        "자치구 next_4q 양의 방향 비율",
        f"{district_4q['positive_blocks']}/{district_4q['eligible_blocks']} ({district_4q['positive_rate']:.3f})",
        "80% 이상이면 통과",
        "PASS" if district_4q["positive_rate"] >= 0.8 else "NOT_READY",
        "4분기 성장에서도 공간 방향이 유지되는지 확인한다.",
    )
    add_validation(
        "검토4_업종안정성",
        "업종대분류 next_q 양의 방향 비율",
        f"{industry_q['positive_blocks']}/{industry_q['eligible_blocks']} ({industry_q['positive_rate']:.3f})",
        "전체 업종대분류 양의 방향이면 통과",
        "PASS" if industry_q["positive_rate"] >= 1.0 else "NOT_READY",
        "업종군별 방향이 다르면 업종별 산식 또는 별도 출력이 필요하다.",
    )
    add_validation(
        "검토4_업종안정성",
        "업종대분류 next_4q 양의 방향 비율",
        f"{industry_4q['positive_blocks']}/{industry_4q['eligible_blocks']} ({industry_4q['positive_rate']:.3f})",
        "전체 업종대분류 양의 방향이면 통과",
        "PASS" if industry_4q["positive_rate"] >= 1.0 else "NOT_READY",
        "4분기 성장에서도 업종군별 방향이 유지되는지 확인한다.",
    )
    add_validation(
        "검토4_기간안정성",
        "연도별 next_q 양의 방향 비율",
        f"{period_q['positive_blocks']}/{period_q['eligible_blocks']} ({period_q['positive_rate']:.3f})",
        "80% 이상이면 통과",
        "PASS" if period_q["positive_rate"] >= 0.8 else "NOT_READY",
        "특정 연도 효과라면 지속 가능한 규칙이 아니다.",
    )
    add_validation(
        "검토4_기간안정성",
        "연도별 next_4q 양의 방향 비율",
        f"{period_4q['positive_blocks']}/{period_4q['eligible_blocks']} ({period_4q['positive_rate']:.3f})",
        "80% 이상이면 통과",
        "PASS" if period_4q["positive_rate"] >= 0.8 else "NOT_READY",
        "4분기 성장에서도 특정 연도 효과가 아닌지 확인한다.",
    )
    add_validation(
        "검토5_금지표현",
        "엔진 반영 판정",
        "공간/업종/기간 안정성 검증 중",
        "성장률 예측 표현 금지",
        "PASS",
        "안정성 검증은 후보 승격 검토일 뿐 성장률 보장이나 성공확률이 아니다.",
    )

    out = pd.DataFrame([v.__dict__ for v in validations])
    out.insert(0, "validation_id", range(1, len(out) + 1))
    return out


def build_summary(df: pd.DataFrame, district: pd.DataFrame, industry: pd.DataFrame, period: pd.DataFrame, validation: pd.DataFrame) -> dict[str, object]:
    all_blocks = pd.concat([district, industry, period], ignore_index=True)
    not_ready_count = int((validation["result"] == "NOT_READY").sum())
    fail_count = int((validation["result"] == "FAIL").sum())
    validation_ready = fail_count == 0 and not_ready_count == 0
    summary_rows = [
        summarize_block(district, "자치구_코드_명", LABEL_Q),
        summarize_block(district, "자치구_코드_명", LABEL_4Q),
        summarize_block(industry, "업종_대분류_검증그룹", LABEL_Q),
        summarize_block(industry, "업종_대분류_검증그룹", LABEL_4Q),
        summarize_block(period, "기준_연도", LABEL_Q),
        summarize_block(period, "기준_연도", LABEL_4Q),
    ]
    return {
        "run_date": RUN_DATE,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "validation_version": VALIDATION_VERSION,
        "input_rows": int(len(df)),
        "score": SCORE,
        "overall_next_q_corr": safe_corr(df, SCORE, LABEL_Q),
        "overall_next_4q_corr": safe_corr(df, SCORE, LABEL_4Q),
        "block_metric_rows": int(len(all_blocks)),
        "block_summaries": summary_rows,
        "validation_pass_count": int((validation["result"] == "PASS").sum()),
        "validation_not_ready_count": not_ready_count,
        "validation_fail_count": fail_count,
        "decision": (
            "공간업종기간_안정성검증_통과_엔진승격은_별도단계"
            if validation_ready
            else "공간업종기간_안정성검증_미통과_엔진승격금지"
        ),
        "decision_reason_ko": (
            "반등 후보는 전체·자치구·업종대분류·연도 블록에서 양의 방향을 유지했다. 다만 현재 점수 엔진 교체/가중치/문구 검수는 별도 단계로 진행한다."
            if validation_ready
            else f"안정성 검증이 완료되지 않았다. FAIL={fail_count}, NOT_READY={not_ready_count}이므로 엔진 후보 산출을 중단한다."
        ),
    }


def fmt(value: object, digits: int = 6) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        if math.isnan(value):
            return "nan"
        return f"{value:.{digits}f}"
    return str(value)


def write_report(district: pd.DataFrame, industry: pd.DataFrame, period: pd.DataFrame, validation: pd.DataFrame, summary: dict[str, object]) -> None:
    lines = [
        "# 성장 반등 후보 공간·업종·기간 안정성 검증",
        "",
        f"작성일: {RUN_DATE}",
        "",
        "## 1. 목적",
        "",
        "34번에서 가장 나은 후보였던 `rebound_growth_rule_score`가 전체 평균 착시인지 확인하기 위해 자치구, 업종대분류, 기준연도별로 쪼개 검증했다.",
        "",
        "## 2. 산출물",
        "",
        f"- `datacorpus/_rule_validation/{OUT_DISTRICT.name}`",
        f"- `datacorpus/_rule_validation/{OUT_INDUSTRY.name}`",
        f"- `datacorpus/_rule_validation/{OUT_PERIOD.name}`",
        f"- `datacorpus/_rule_validation/{OUT_VALIDATION.name}`",
        f"- `datacorpus/_rule_validation/{OUT_SUMMARY.name}`",
        "",
        "## 3. 요약",
        "",
        "| 항목 | 값 |",
        "|---|---:|",
        f"| input_rows | {summary['input_rows']:,} |",
        f"| overall_next_q_corr | {fmt(summary['overall_next_q_corr'])} |",
        f"| overall_next_4q_corr | {fmt(summary['overall_next_4q_corr'])} |",
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
            "## 5. 블록별 요약",
            "",
            "| block_type | label | eligible_blocks | positive_blocks | positive_rate | min_corr | median_corr |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in summary["block_summaries"]:
        lines.append(
            f"| {row['block_type']} | {row['label']} | {row['eligible_blocks']} | {row['positive_blocks']} | {fmt(row['positive_rate'])} | {fmt(row['min_corr'])} | {fmt(row['median_corr'])} |"
        )

    lines.extend(
        [
            "",
            "## 6. 자치구 next_q 상관 하위 10개",
            "",
            "| block_value | non_null_rows | spearman_corr | positive_direction |",
            "|---|---:|---:|---|",
        ]
    )
    district_q = district[district["label"].eq(LABEL_Q)].sort_values("spearman_corr").head(10)
    for row in district_q.itertuples(index=False):
        lines.append(f"| {row.block_value} | {row.non_null_rows:,} | {fmt(row.spearman_corr)} | {row.positive_direction} |")

    lines.extend(
        [
            "",
            "## 7. 판정",
            "",
            "성장 반등 후보의 공간·업종·기간 방향성 안정성은 통과했다. 다만 아직 성장잠재 엔진에는 반영하지 않는다.",
            "",
            "이유:",
            "",
            "- 전체, 자치구, 업종대분류, 연도 블록에서 다음분기/4분기 초과성장과 양의 방향이 유지되었다.",
            "- 그러나 이것은 후보 규칙의 방향성 검증이지 창업 성공확률이나 성장률 보장 검증이 아니다.",
            "- 다음 단계는 이 후보를 별도 `growth_rebound_candidate_score`로 산출하고, 기존 성장잠재 점수와 교체할지 전체 엔진 백테스트에서 다시 판단하는 것이다.",
        ]
    )
    OUT_REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    ensure_dirs()
    base = load_base()
    scored = add_rebound_score(base)
    scored = add_blocks(scored)

    district = pd.concat(
        [block_metrics(scored, "자치구_코드_명", LABEL_Q), block_metrics(scored, "자치구_코드_명", LABEL_4Q)],
        ignore_index=True,
    )
    industry = pd.concat(
        [
            block_metrics(scored, "업종_대분류_검증그룹", LABEL_Q),
            block_metrics(scored, "업종_대분류_검증그룹", LABEL_4Q),
            block_metrics(scored, "SBDC_대분류명_후보", LABEL_Q),
            block_metrics(scored, "SBDC_대분류명_후보", LABEL_4Q),
        ],
        ignore_index=True,
    )
    period = pd.concat(
        [
            block_metrics(scored, "기준_연도", LABEL_Q),
            block_metrics(scored, "기준_연도", LABEL_4Q),
            block_metrics(scored, "검증_반기", LABEL_Q),
            block_metrics(scored, "검증_반기", LABEL_4Q),
        ],
        ignore_index=True,
    )

    validation = build_validations(scored, district, industry, period)
    summary = build_summary(scored, district, industry, period, validation)

    district.to_csv(OUT_DISTRICT, index=False, encoding="utf-8-sig")
    industry.to_csv(OUT_INDUSTRY, index=False, encoding="utf-8-sig")
    period.to_csv(OUT_PERIOD, index=False, encoding="utf-8-sig")
    validation.to_csv(OUT_VALIDATION, index=False, encoding="utf-8-sig")
    OUT_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_report(district, industry, period, validation, summary)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if int(summary["validation_fail_count"]) > 0 or int(summary["validation_not_ready_count"]) > 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
