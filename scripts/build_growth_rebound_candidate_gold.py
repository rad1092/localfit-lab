# -*- coding: utf-8 -*-
"""
성장 반등 후보 gold 산출.

목적:
  1. 34~35번 검증에서 방향성이 확인된 `rebound_growth_rule_score`를 런타임 안전 후보 gold로 만든다.
  2. 미래 라벨(`next_*`, `미래_*`)은 산출물에서 완전히 제거한다.
  3. 아직 현재 점수 엔진에 자동 반영하지 않고, 별도 후보 점수로만 보존한다.

근거:
  - 33번: 성장 라벨 후보와 시간누수 금지 검증
  - 34번: 현재 피처만 사용한 성장 규칙 후보 백테스트
  - 35번: 반등 후보의 공간·업종·기간 방향성 안정성 검증
"""

from __future__ import annotations

import json
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
STABILITY_SUMMARY = RULE_VALIDATION / "35_growth_rebound_stability_summary.json"

OUT_GOLD = GOLD / "gold_growth_rebound_candidate_q_industry.csv"
OUT_VALIDATION = RULE_VALIDATION / "36_growth_rebound_candidate_gold_validation.csv"
OUT_SUMMARY = RULE_VALIDATION / "36_growth_rebound_candidate_gold_summary.json"
RUN_DATE = datetime.now().strftime("%Y-%m-%d")

OUT_REPORT = RESEARCH_RULE_VALIDATION / "36_growth_rebound_candidate_gold_validation_20260704.md"

GOLD_VERSION = "growth_rebound_candidate_gold.v1.0-20260704"
KEYS = ["기준_년분기_코드", "상권_코드", "서비스_업종_코드"]

FORBIDDEN_OUTPUT_PREFIXES = ("next_", "미래_")
FORBIDDEN_OUTPUT_SUBSTRINGS = ("label", "라벨", "성공확률", "보장")


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
    GOLD.mkdir(parents=True, exist_ok=True)
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


def score_decile_by_quarter(df: pd.DataFrame, score_col: str) -> pd.Series:
    def decile(group: pd.Series) -> pd.Series:
        valid = group.dropna()
        if len(valid) < 10:
            return pd.Series(pd.NA, index=group.index, dtype="Int64")
        ranked = group.rank(method="first")
        return (pd.qcut(ranked, q=10, labels=False, duplicates="drop") + 1).astype("Int64")

    return df.groupby("기준_년분기_코드", group_keys=False)[score_col].apply(decile).astype("Int64")


def score_grade(decile: pd.Series) -> pd.Series:
    bins = pd.Series(pd.NA, index=decile.index, dtype="object")
    bins.loc[decile.isin([9, 10])] = "A_반등후보상위"
    bins.loc[decile.isin([7, 8])] = "B_반등후보양호"
    bins.loc[decile.isin([5, 6])] = "C_중립"
    bins.loc[decile.isin([3, 4])] = "D_약함"
    bins.loc[decile.isin([1, 2])] = "E_낮음"
    return bins


def load_current_features() -> pd.DataFrame:
    usecols = [
        "기준_년분기_코드",
        "상권_코드",
        "상권_코드_명",
        "서비스_업종_코드",
        "서비스_업종_코드_명",
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
    df = read_csv(
        INPUT,
        dtype={"기준_년분기_코드": str, "상권_코드": str, "서비스_업종_코드": str, "상권_변화_지표_코드": str},
        usecols=usecols,
    )
    return to_numeric(
        df,
        [
            "현재_매출_금액",
            "점포_수",
            "개업_율",
            "폐업_률",
            "개폐업_순동태",
            "매출_log_최근4분기_slope",
            "운영_서울대비_개월_차이",
            "폐업_서울대비_개월_차이",
        ],
    )


def build_gold(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    iq = ["기준_년분기_코드", "서비스_업종_코드"]

    # 현재 시점 피처만으로 반등 후보 점수를 만든다.
    out["pct_current_sales"] = rank_pct(out, "현재_매출_금액", iq)
    out["pct_inverse_current_sales"] = 100.0 - out["pct_current_sales"]
    out["pct_sales_slope"] = rank_pct(out, "매출_log_최근4분기_slope", iq)
    out["pct_inverse_sales_slope"] = 100.0 - out["pct_sales_slope"]
    out["pct_close_rate"] = rank_pct(out, "폐업_률", iq)
    out["pct_inverse_close_rate"] = 100.0 - out["pct_close_rate"]
    out["pct_net_open_close"] = rank_pct(out, "개폐업_순동태", iq)
    out["growth_rebound_candidate_score"] = out[
        ["pct_inverse_current_sales", "pct_inverse_sales_slope", "pct_net_open_close", "pct_inverse_close_rate"]
    ].mean(axis=1, skipna=True)

    no_history = bool_to_float(out["매출_최근4분기_연속존재"]).fillna(0).eq(0)
    out.loc[no_history, "growth_rebound_candidate_score"] = np.nan
    out["growth_rebound_decile_by_quarter"] = score_decile_by_quarter(out, "growth_rebound_candidate_score")
    out["growth_rebound_candidate_grade"] = score_grade(out["growth_rebound_decile_by_quarter"])
    out["growth_rebound_gate_reason"] = np.where(
        no_history,
        "최근4분기 매출 이력이 없어 반등 후보 점수 산출 보류",
        "현재 피처 기반 반등 후보 점수 산출",
    )

    out["gold_version"] = GOLD_VERSION
    out["gold_role"] = "성장반등후보_런타임안전_current_feature_only"
    out["source_validation_refs"] = "33_growth_label_candidate;34_growth_rule_candidate;35_growth_rebound_stability"
    out["runtime_feature_safe"] = True
    out["score_engine_active"] = False
    out["engine_activation_required_ko"] = "전체 점수 엔진 백테스트와 문구 검수 후 별도 반영"
    out["forbidden_claim_ko"] = "창업 성공확률, 성장률 예측/보장, 개별 매장 매출 보장으로 표현 금지"
    out["algorithm_use_note_ko"] = "현재 매출이 낮고 최근 추세가 낮았으나 개폐업 순동태와 폐업위험이 버티는 반등 후보 신호다. 현재입지 점수와 합산하지 않는다."

    keep = [
        "기준_년분기_코드",
        "상권_코드",
        "상권_코드_명",
        "서비스_업종_코드",
        "서비스_업종_코드_명",
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
        "pct_inverse_current_sales",
        "pct_inverse_sales_slope",
        "pct_net_open_close",
        "pct_inverse_close_rate",
        "growth_rebound_candidate_score",
        "growth_rebound_decile_by_quarter",
        "growth_rebound_candidate_grade",
        "growth_rebound_gate_reason",
        "gold_version",
        "gold_role",
        "source_validation_refs",
        "runtime_feature_safe",
        "score_engine_active",
        "engine_activation_required_ko",
        "forbidden_claim_ko",
        "algorithm_use_note_ko",
    ]
    return out[keep].copy()


def load_stability_summary() -> dict[str, object]:
    if not STABILITY_SUMMARY.exists():
        return {"decision": "missing"}
    return json.loads(STABILITY_SUMMARY.read_text(encoding="utf-8"))


def validate_gold(gold: pd.DataFrame, source_rows: int, stability_summary: dict[str, object]) -> pd.DataFrame:
    duplicate_keys = int(gold.duplicated(KEYS).sum())
    key_null = int(gold[KEYS].isna().any(axis=1).sum())
    forbidden_cols = [
        c for c in gold.columns
        if c.startswith(FORBIDDEN_OUTPUT_PREFIXES)
        or any(token in c for token in FORBIDDEN_OUTPUT_SUBSTRINGS)
    ]
    active_count = int(gold["score_engine_active"].fillna(False).astype(bool).sum())
    safe_count = int(gold["runtime_feature_safe"].fillna(False).astype(bool).sum())
    non_null_score = int(gold["growth_rebound_candidate_score"].notna().sum())
    stability_pass = (
        int(stability_summary.get("validation_fail_count", -1)) == 0
        and int(stability_summary.get("validation_not_ready_count", -1)) == 0
        and stability_summary.get("decision") == "공간업종기간_안정성검증_통과_엔진승격은_별도단계"
    )

    add_validation(
        "검토1_원천근거",
        "입력 row 보존",
        len(gold),
        source_rows,
        "PASS" if len(gold) == source_rows else "FAIL",
        "반등 후보 gold는 33번 라벨 후보 테이블에서 현재 피처만 가져와 같은 grain으로 보존한다.",
    )
    add_validation(
        "검토1_원천근거",
        "35번 안정성 검증 통과 참조",
        stability_summary.get("decision", "missing"),
        "공간업종기간_안정성검증_통과_엔진승격은_별도단계",
        "PASS" if stability_pass else "FAIL",
        "반등 후보 gold는 35번 안정성 검증을 근거로 생성한다.",
    )
    add_validation(
        "검토2_grain_key",
        "후보 gold key 중복 금지",
        f"duplicate_keys={duplicate_keys}, key_null={key_null}",
        "중복 0, key_null 0",
        "PASS" if duplicate_keys == 0 and key_null == 0 else "FAIL",
        "분기×상권×업종 grain이 깨지면 엔진 조인에서 중복 점수가 생긴다.",
    )
    add_validation(
        "검토3_시간누수",
        "미래 라벨 컬럼 제거",
        ",".join(forbidden_cols) if forbidden_cols else "없음",
        "next_/미래_/label/라벨 컬럼 없음",
        "PASS" if not forbidden_cols else "FAIL",
        "런타임 후보 gold에는 백테스트 정답지 컬럼이 들어가면 안 된다.",
    )
    add_validation(
        "검토3_시간누수",
        "runtime_feature_safe 전체 true",
        safe_count,
        len(gold),
        "PASS" if safe_count == len(gold) else "FAIL",
        "현재 피처만 쓰는 산출물임을 행 단위로 명시한다.",
    )
    add_validation(
        "검토4_방향정규화",
        "후보 점수 산출 row 존재",
        non_null_score,
        "0보다 큼",
        "PASS" if non_null_score > 0 else "FAIL",
        "최근4분기 이력이 있는 행에서만 반등 후보 점수를 산출한다.",
    )
    add_validation(
        "검토4_방향정규화",
        "점수 엔진 자동 반영 금지",
        active_count,
        0,
        "PASS" if active_count == 0 else "FAIL",
        "방향성 검증을 통과했더라도 현재입지 점수와 자동 합산하지 않는다.",
    )
    add_validation(
        "검토5_금지표현",
        "금지표현 메타 존재",
        int(gold["forbidden_claim_ko"].notna().sum()),
        len(gold),
        "PASS" if int(gold["forbidden_claim_ko"].notna().sum()) == len(gold) else "FAIL",
        "성장 반등 후보는 창업 성공확률이나 성장률 보장으로 표현하면 안 된다.",
    )

    out = pd.DataFrame([v.__dict__ for v in validations])
    out.insert(0, "validation_id", range(1, len(out) + 1))
    return out


def summarize(gold: pd.DataFrame, validation: pd.DataFrame, stability_summary: dict[str, object]) -> dict[str, object]:
    quarters = sorted(gold["기준_년분기_코드"].dropna().astype(str).unique())
    return {
        "run_date": RUN_DATE,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "gold_version": GOLD_VERSION,
        "row_count": int(len(gold)),
        "quarter_count": int(len(quarters)),
        "quarter_min": quarters[0] if quarters else None,
        "quarter_max": quarters[-1] if quarters else None,
        "score_non_null_rows": int(gold["growth_rebound_candidate_score"].notna().sum()),
        "score_non_null_rate": float(gold["growth_rebound_candidate_score"].notna().mean()),
        "top_grade_rows": int(gold["growth_rebound_candidate_grade"].eq("A_반등후보상위").sum()),
        "validation_pass_count": int((validation["result"] == "PASS").sum()),
        "validation_fail_count": int((validation["result"] == "FAIL").sum()),
        "stability_decision_ref": stability_summary.get("decision", "missing"),
        "decision": "반등후보_gold_생성완료_엔진미반영",
        "decision_reason_ko": "현재 피처만 포함한 런타임 안전 후보 gold를 만들었지만, 전체 엔진 백테스트와 문구 검수 전까지 score_engine_active=False로 둔다.",
    }


def write_report(gold: pd.DataFrame, validation: pd.DataFrame, summary: dict[str, object]) -> None:
    lines = [
        "# 성장 반등 후보 gold 검증",
        "",
        f"작성일: {RUN_DATE}",
        "",
        "## 1. 목적",
        "",
        "35번에서 방향성 안정성을 통과한 `rebound_growth_rule_score`를 런타임에서 읽을 수 있는 후보 gold로 분리했다.",
        "",
        "중요: 이 테이블에는 미래 라벨 컬럼을 넣지 않는다. 현재 점수 엔진에도 아직 자동 반영하지 않는다.",
        "",
        "## 2. 산출물",
        "",
        f"- `datacorpus/_gold/{OUT_GOLD.name}`",
        f"- `datacorpus/_rule_validation/{OUT_VALIDATION.name}`",
        f"- `datacorpus/_rule_validation/{OUT_SUMMARY.name}`",
        "",
        "## 3. 요약",
        "",
        "| 항목 | 값 |",
        "|---|---:|",
        f"| row_count | {summary['row_count']:,} |",
        f"| quarter range | {summary['quarter_min']}~{summary['quarter_max']} |",
        f"| score_non_null_rows | {summary['score_non_null_rows']:,} |",
        f"| score_non_null_rate | {summary['score_non_null_rate']:.6f} |",
        f"| top_grade_rows | {summary['top_grade_rows']:,} |",
        f"| validation PASS | {summary['validation_pass_count']} |",
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
            "## 5. 판정",
            "",
            "`gold_growth_rebound_candidate_q_industry.csv`는 런타임 안전 후보 gold로 생성 완료했다.",
            "",
            "다만 현재 상태는 `score_engine_active=False`다.",
            "",
            "다음 단계:",
            "",
            "1. 기존 `growth_potential_score`와 별도로 `growth_rebound_candidate_score`를 엔진 출력에 붙이는 실험.",
            "2. 전체 백테스트에서 기존 성장잠재 점수와 반등 후보 점수의 라벨 관계 비교.",
            "3. 문구 검수: 성장률 예측/보장 표현 금지 유지.",
        ]
    )
    OUT_REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    ensure_dirs()
    source = load_current_features()
    gold = build_gold(source)
    stability_summary = load_stability_summary()
    validation = validate_gold(gold, len(source), stability_summary)
    summary = summarize(gold, validation, stability_summary)

    gold.to_csv(OUT_GOLD, index=False, encoding="utf-8-sig")
    validation.to_csv(OUT_VALIDATION, index=False, encoding="utf-8-sig")
    OUT_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_report(gold, validation, summary)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if int(summary["validation_fail_count"]) > 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
