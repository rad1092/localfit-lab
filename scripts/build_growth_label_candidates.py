# -*- coding: utf-8 -*-
"""
성장 라벨 후보 전처리.

목적:
  1. 현재 `growth_potential_score`를 운영 라벨처럼 쓰지 않기 위해 미래 성장 라벨 후보를 별도 테이블로 분리한다.
  2. 다음분기/향후 4분기 성장 라벨은 백테스트 검증 전용임을 명시한다.
  3. 상권변화지표 HH/HL/LH/LL 코드는 숫자 점수로 바로 바꾸지 않고 원문 evidence로 보존한다.
  4. 전처리 결과를 5회 규칙 검토(원천, grain, 시간누수, 방향, 금지표현)로 검증한다.

근거:
  - research/rule_validation/00_검증프로토콜_20260703.md
  - research/rule_validation/05_change_index_silver_validation_20260703.md
  - research/알고리즘_명세_v2_20260704.md
  - datacorpus/_score_backtest_gold/gold_engine_backtest_component_metrics.csv
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
SCORE_BACKTEST = Path(
    os.getenv("LOCALFIT_SCORE_BACKTEST_DIR", ROOT / "datacorpus" / "_score_backtest_gold")
).resolve()

RUN_DATE = datetime.now().strftime("%Y-%m-%d")
LABEL_VERSION = "growth_label_candidates.v1.0-20260704"

KEYS = ["기준_년분기_코드", "상권_코드", "서비스_업종_코드"]

OUT_LABELS = GOLD / "gold_growth_label_candidates_q_industry.csv"
OUT_VALIDATION = RULE_VALIDATION / "33_growth_label_candidate_validation.csv"
OUT_DIAGNOSTICS = RULE_VALIDATION / "33_growth_label_candidate_feature_diagnostics.csv"
OUT_SUMMARY = RULE_VALIDATION / "33_growth_label_candidate_summary.json"
OUT_REPORT = RESEARCH_RULE_VALIDATION / "33_growth_label_candidate_validation_20260704.md"


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


def add_validation(
    review_round: str,
    rule_name: str,
    observed: object,
    expected: object,
    passed: bool,
    reason_ko: str,
    conditional: bool = False,
) -> None:
    result = "PASS" if passed else ("CONDITIONAL_PASS" if conditional else "FAIL")
    validations.append(Validation(review_round, rule_name, observed, expected, result, reason_ko))


def quarter_add(q: int | str, n: int) -> int:
    """YYYYQ(예: 20261)에 n분기를 더한다."""
    q = int(q)
    year, quarter = divmod(q, 10)
    base = year * 4 + (quarter - 1)
    moved = base + n
    new_year, new_offset = divmod(moved, 4)
    return new_year * 10 + (new_offset + 1)


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


def safe_corr(df: pd.DataFrame, a: str, b: str) -> float:
    use = df[[a, b]].replace([np.inf, -np.inf], np.nan).dropna()
    if len(use) < 30:
        return float("nan")
    ranks = use[[a, b]].rank(method="average")
    return float(ranks[a].corr(ranks[b], method="pearson"))


def safe_rate(series: pd.Series) -> float:
    if len(series) == 0:
        return float("nan")
    return float(series.mean())


def load_sales() -> pd.DataFrame:
    sales = read_csv(
        GOLD / "gold_sales_strength_q_industry.csv",
        dtype={"기준_년분기_코드": str, "상권_코드": str, "서비스_업종_코드": str},
        usecols=[
            "기준_년분기_코드",
            "상권_코드",
            "상권_코드_명",
            "서비스_업종_코드",
            "서비스_업종_코드_명",
            "당월_매출_금액",
        ],
    )
    sales = to_numeric(sales, ["당월_매출_금액"])
    sales["기준_년분기_코드"] = sales["기준_년분기_코드"].astype(str)
    sales["상권_코드"] = sales["상권_코드"].astype(str)
    sales["서비스_업종_코드"] = sales["서비스_업종_코드"].astype(str)
    sales = sales.rename(columns={"당월_매출_금액": "현재_매출_금액"})
    return sales


def load_growth_features() -> pd.DataFrame:
    usecols = [
        "기준_년분기_코드",
        "상권_코드",
        "서비스_업종_코드",
        "점포_수",
        "개업_율",
        "폐업_률",
        "상권_변화_지표_코드",
        "상권_변화_지표_명",
        "운영_서울대비_개월_차이",
        "폐업_서울대비_개월_차이",
        "매출_log_최근4분기_slope",
        "매출_최근4분기_연속존재",
        "growth_score_status",
    ]
    growth = read_csv(
        GOLD / "gold_growth_stability_q_industry.csv",
        dtype={
            "기준_년분기_코드": str,
            "상권_코드": str,
            "서비스_업종_코드": str,
            "상권_변화_지표_코드": str,
        },
        usecols=usecols,
    )
    growth["기준_년분기_코드"] = growth["기준_년분기_코드"].astype(str)
    growth["상권_코드"] = growth["상권_코드"].astype(str)
    growth["서비스_업종_코드"] = growth["서비스_업종_코드"].astype(str)
    growth = to_numeric(
        growth,
        ["점포_수", "개업_율", "폐업_률", "운영_서울대비_개월_차이", "폐업_서울대비_개월_차이", "매출_log_최근4분기_slope"],
    )
    if "매출_최근4분기_연속존재" in growth.columns:
        growth["매출_최근4분기_연속존재"] = growth["매출_최근4분기_연속존재"].astype(str).str.lower().isin(["true", "1"])
    growth["개폐업_순동태"] = growth["개업_율"] - growth["폐업_률"]
    return growth


def add_future_sales(base: pd.DataFrame, horizon: int) -> pd.DataFrame:
    future = base[KEYS + ["현재_매출_금액"]].copy()
    future["기준_년분기_코드"] = future["기준_년분기_코드"].map(lambda q: str(quarter_add(q, -horizon)))
    future = future.rename(columns={"현재_매출_금액": f"미래_{horizon}분기_매출_금액"})
    return future


def build_labels(sales: pd.DataFrame, growth: pd.DataFrame) -> pd.DataFrame:
    out = sales.copy()
    for horizon in [1, 2, 3, 4]:
        out = out.merge(add_future_sales(sales, horizon), on=KEYS, how="left", validate="one_to_one")

    out["현재_매출_log"] = np.log1p(out["현재_매출_금액"])
    for horizon in [1, 2, 3, 4]:
        out[f"미래_{horizon}분기_매출_log"] = np.log1p(out[f"미래_{horizon}분기_매출_금액"])

    out["next_q_log_growth"] = out["미래_1분기_매출_log"] - out["현재_매출_log"]
    out["next_q_growth_rate"] = np.where(
        out["현재_매출_금액"] > 0,
        (out["미래_1분기_매출_금액"] - out["현재_매출_금액"]) / out["현재_매출_금액"],
        np.nan,
    )
    out["next_4q_cumulative_log_growth"] = out["미래_4분기_매출_log"] - out["현재_매출_log"]

    iq = ["기준_년분기_코드", "서비스_업종_코드"]
    out["industry_median_next_q_log_growth"] = out.groupby(iq)["next_q_log_growth"].transform("median")
    out["next_q_excess_log_growth_vs_industry"] = out["next_q_log_growth"] - out["industry_median_next_q_log_growth"]
    out["next_q_beats_industry_median_growth"] = np.where(
        out["next_q_excess_log_growth_vs_industry"].notna(),
        out["next_q_excess_log_growth_vs_industry"].gt(0),
        pd.NA,
    )
    out["next_q_growth_pct_same_industry"] = out.groupby(iq)["next_q_log_growth"].rank(pct=True) * 100.0

    out["future_4q_complete"] = out[[f"미래_{h}분기_매출_금액" for h in [1, 2, 3, 4]]].notna().all(axis=1)
    out["industry_median_next_4q_log_growth"] = out.groupby(iq)["next_4q_cumulative_log_growth"].transform("median")
    out["industry_p25_next_4q_log_growth"] = out.groupby(iq)["next_4q_cumulative_log_growth"].transform(lambda s: s.quantile(0.25))
    out["next_4q_excess_log_growth_vs_industry"] = out["next_4q_cumulative_log_growth"] - out["industry_median_next_4q_log_growth"]
    out["next_4q_beats_industry_median_growth"] = np.where(
        out["next_4q_excess_log_growth_vs_industry"].notna(),
        out["next_4q_excess_log_growth_vs_industry"].gt(0),
        pd.NA,
    )

    q_growths = [
        out["미래_1분기_매출_log"] - out["현재_매출_log"],
        out["미래_2분기_매출_log"] - out["미래_1분기_매출_log"],
        out["미래_3분기_매출_log"] - out["미래_2분기_매출_log"],
        out["미래_4분기_매출_log"] - out["미래_3분기_매출_log"],
    ]
    positive_count = sum(g.gt(0).astype(float) for g in q_growths)
    out["future_4q_positive_quarter_count"] = np.where(out["future_4q_complete"], positive_count, np.nan)
    out["sustained_4q_growth_candidate"] = np.where(
        out["future_4q_complete"],
        (out["future_4q_positive_quarter_count"] >= 3) & (out["next_4q_excess_log_growth_vs_industry"] > 0),
        pd.NA,
    )
    out["next_4q_downside_risk_candidate"] = np.where(
        out["future_4q_complete"],
        out["next_4q_cumulative_log_growth"] <= out["industry_p25_next_4q_log_growth"],
        pd.NA,
    )

    out = out.merge(growth, on=KEYS, how="left", validate="one_to_one")
    out = add_current_diagnostic_signals(out)

    out["growth_label_version"] = LABEL_VERSION
    out["future_label_use_scope"] = "백테스트_검증_전용"
    out["future_label_runtime_allowed"] = False
    out["current_signal_use_scope"] = "성장_알고리즘_재설계_후보"
    out["forbidden_claim_ko"] = "창업 성공확률, 성장률 예측/보장, 개별 매장 매출 보장으로 표현 금지"
    out["label_note_ko"] = "미래 라벨은 백테스트 검증용이다. 리포트 실행 시점 입력 피처로 사용하면 시간누수다."

    return out


def percentile_by_group(df: pd.DataFrame, column: str, group_cols: list[str]) -> pd.Series:
    if column not in df.columns:
        return pd.Series(np.nan, index=df.index)
    return df.groupby(group_cols)[column].rank(pct=True) * 100.0


def add_current_diagnostic_signals(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    iq = ["기준_년분기_코드", "서비스_업종_코드"]
    q = ["기준_년분기_코드"]

    out["pct_매출_추세_기울기"] = percentile_by_group(out, "매출_log_최근4분기_slope", iq)
    out["pct_개폐업_순동태"] = percentile_by_group(out, "개폐업_순동태", iq)
    out["pct_폐업_률"] = percentile_by_group(out, "폐업_률", iq)
    out["pct_운영_서울대비_개월_차이"] = percentile_by_group(out, "운영_서울대비_개월_차이", q)
    out["pct_폐업_위험_역방향"] = 100.0 - out["pct_폐업_률"]

    score_parts = out[["pct_매출_추세_기울기", "pct_개폐업_순동태", "pct_폐업_위험_역방향", "pct_운영_서울대비_개월_차이"]]
    out["current_growth_stability_signal_0_100"] = score_parts.mean(axis=1, skipna=True)
    out.loc[score_parts.notna().sum(axis=1) < 2, "current_growth_stability_signal_0_100"] = np.nan

    strong_growth = out["pct_매출_추세_기울기"] >= 60
    weak_growth = out["pct_매출_추세_기울기"] < 40
    stable = (out["pct_폐업_률"] <= 50) & (out["pct_운영_서울대비_개월_차이"] >= 50)
    unstable = (out["pct_폐업_률"] >= 70) | (out["pct_운영_서울대비_개월_차이"] < 30)
    active_churn = (out["pct_개폐업_순동태"] >= 70) & (out["pct_폐업_률"] >= 70)

    segment = pd.Series("판정보류_이력부족", index=out.index, dtype="object")
    has_enough = out["매출_최근4분기_연속존재"].fillna(False) & out["current_growth_stability_signal_0_100"].notna()
    segment.loc[has_enough & active_churn] = "진출입_활발_변동성높음"
    segment.loc[has_enough & strong_growth & stable] = "성장_안정_후보"
    segment.loc[has_enough & strong_growth & unstable] = "성장_불안정_후보"
    segment.loc[has_enough & weak_growth & stable] = "저성장_안정_후보"
    segment.loc[has_enough & weak_growth & unstable] = "저성장_주의_후보"
    segment.loc[has_enough & segment.eq("판정보류_이력부족")] = "중립_추가검토"
    out["growth_stability_quadrant_candidate"] = segment
    return out


def select_output_columns(df: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "기준_년분기_코드",
        "상권_코드",
        "상권_코드_명",
        "서비스_업종_코드",
        "서비스_업종_코드_명",
        "현재_매출_금액",
        "미래_1분기_매출_금액",
        "미래_4분기_매출_금액",
        "next_q_log_growth",
        "next_q_growth_rate",
        "industry_median_next_q_log_growth",
        "next_q_excess_log_growth_vs_industry",
        "next_q_beats_industry_median_growth",
        "next_q_growth_pct_same_industry",
        "future_4q_complete",
        "next_4q_cumulative_log_growth",
        "industry_median_next_4q_log_growth",
        "next_4q_excess_log_growth_vs_industry",
        "next_4q_beats_industry_median_growth",
        "future_4q_positive_quarter_count",
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
        "current_growth_stability_signal_0_100",
        "growth_stability_quadrant_candidate",
        "growth_score_status",
        "growth_label_version",
        "future_label_use_scope",
        "future_label_runtime_allowed",
        "current_signal_use_scope",
        "forbidden_claim_ko",
        "label_note_ko",
    ]
    return df[[c for c in columns if c in df.columns]].copy()


def build_diagnostics(df: pd.DataFrame) -> pd.DataFrame:
    pairs = [
        ("매출_log_최근4분기_slope", "next_q_excess_log_growth_vs_industry"),
        ("개폐업_순동태", "next_q_excess_log_growth_vs_industry"),
        ("폐업_률", "next_q_excess_log_growth_vs_industry"),
        ("운영_서울대비_개월_차이", "next_q_excess_log_growth_vs_industry"),
        ("current_growth_stability_signal_0_100", "next_q_excess_log_growth_vs_industry"),
        ("매출_log_최근4분기_slope", "next_4q_excess_log_growth_vs_industry"),
        ("개폐업_순동태", "next_4q_excess_log_growth_vs_industry"),
        ("폐업_률", "next_4q_excess_log_growth_vs_industry"),
        ("운영_서울대비_개월_차이", "next_4q_excess_log_growth_vs_industry"),
        ("current_growth_stability_signal_0_100", "next_4q_excess_log_growth_vs_industry"),
    ]
    rows = []
    for feature, label in pairs:
        if feature not in df.columns or label not in df.columns:
            continue
        use = df[[feature, label]].replace([np.inf, -np.inf], np.nan).dropna()
        rows.append(
            {
                "feature": feature,
                "label": label,
                "non_null_rows": int(len(use)),
                "spearman_corr": safe_corr(df, feature, label),
                "diagnostic_note_ko": "상관은 후보 진단값이다. 이 값만으로 성장 알고리즘을 확정하지 않는다.",
            }
        )
    return pd.DataFrame(rows)


def build_validations(df: pd.DataFrame, diagnostics: pd.DataFrame) -> pd.DataFrame:
    duplicated = int(df.duplicated(KEYS).sum())
    key_null = int(df[KEYS].isna().any(axis=1).sum())
    next_q_available = int(df["next_q_log_growth"].notna().sum())
    next_4q_available = int(df["future_4q_complete"].sum())
    runtime_false_count = int((df["future_label_runtime_allowed"] == False).sum())  # noqa: E712
    change_score_cols = [c for c in df.columns if "상권_변화" in c and "점수" in c]
    forbidden_cols = [c for c in df.columns if any(word in c.lower() for word in ["success", "probability", "보장", "성공확률"])]

    add_validation(
        "검토1_원천근거",
        "서울시 상권 매출 gold 존재",
        len(df),
        "0보다 큼",
        len(df) > 0,
        "성장 라벨은 서울시 상권분석서비스 추정매출의 분기별 시계열에서만 만든다.",
    )
    add_validation(
        "검토1_원천근거",
        "현재 growth_potential은 운영 라벨로 승격하지 않음",
        read_growth_component_corr(),
        "진단값 기록, 운영 라벨 사용 금지",
        True,
        "기존 growth_potential_score는 초과성장과 음의 상관으로 확인되어 별도 후보 점수로만 둔다.",
    )
    add_validation(
        "검토2_grain_key",
        "성장 라벨 grain 중복 금지",
        f"duplicate_keys={duplicated}, key_null={key_null}",
        "분기+상권+업종 중복 0, key_null 0",
        duplicated == 0 and key_null == 0,
        "라벨 후보도 엔진 입력과 같은 분기×상권×업종 키를 유지해야 한다.",
    )
    add_validation(
        "검토3_시간누수",
        "다음분기 라벨 존재",
        next_q_available,
        "0보다 큼",
        next_q_available > 0,
        "다음분기 라벨은 백테스트 검증용 미래값이다.",
    )
    add_validation(
        "검토3_시간누수",
        "4분기 미래 라벨은 완전한 미래 4분기만 허용",
        next_4q_available,
        "0보다 큼",
        next_4q_available > 0,
        "향후 4분기 라벨은 중간 분기 누락이 없을 때만 쓴다.",
    )
    add_validation(
        "검토3_시간누수",
        "미래 라벨은 런타임 사용 금지",
        runtime_false_count,
        len(df),
        runtime_false_count == len(df),
        "미래 매출을 리포트 실행 시점 피처로 넣으면 시간누수다.",
    )
    add_validation(
        "검토4_방향정규화",
        "상권변화지표 코드 직접 점수화 금지",
        ",".join(change_score_cols) if change_score_cols else "없음",
        "상권_변화_지표_점수 컬럼 없음",
        len(change_score_cols) == 0,
        "HH/HL/LH/LL 코드명만으로 성장 순위를 만들지 않는다는 05번 검증을 따른다.",
    )
    add_validation(
        "검토4_방향정규화",
        "진단 상관은 후보값으로만 기록",
        int(len(diagnostics)),
        "1개 이상",
        len(diagnostics) > 0,
        "피처와 미래 라벨의 상관은 알고리즘 재설계 후보 판단용이며 산식 확정 근거가 아니다.",
    )
    add_validation(
        "검토5_금지표현",
        "금지표현 컬럼명 없음",
        ",".join(forbidden_cols) if forbidden_cols else "없음",
        "success/probability/보장/성공확률 컬럼 없음",
        len(forbidden_cols) == 0,
        "라벨 후보 산출물이 창업 성공확률이나 성장 보장으로 오해되면 안 된다.",
    )

    validation_df = pd.DataFrame([v.__dict__ for v in validations])
    validation_df.insert(0, "validation_id", range(1, len(validation_df) + 1))
    return validation_df


def read_growth_component_corr() -> str:
    path = SCORE_BACKTEST / "gold_engine_backtest_component_metrics.csv"
    if not path.exists():
        return "component_metrics_missing"
    comp = read_csv(path)
    row = comp[comp["component"].eq("growth_potential")]
    if row.empty:
        return "growth_potential_row_missing"
    value = row["spearman_excess_log_growth_vs_industry"].iloc[0]
    if pd.isna(value):
        return "nan"
    return f"{float(value):.6f}"


def summarize(df: pd.DataFrame, diagnostics: pd.DataFrame, validation: pd.DataFrame) -> dict[str, object]:
    quarters = sorted(df["기준_년분기_코드"].dropna().astype(str).unique())
    segment_counts = {str(k): int(v) for k, v in df["growth_stability_quadrant_candidate"].value_counts(dropna=False).head(20).items()}
    return {
        "run_date": RUN_DATE,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "label_version": LABEL_VERSION,
        "row_count": int(len(df)),
        "quarter_count": int(len(quarters)),
        "quarter_min": quarters[0] if quarters else None,
        "quarter_max": quarters[-1] if quarters else None,
        "next_q_label_available_rows": int(df["next_q_log_growth"].notna().sum()),
        "next_q_label_available_rate": safe_rate(df["next_q_log_growth"].notna()),
        "next_4q_complete_rows": int(df["future_4q_complete"].sum()),
        "next_4q_complete_rate": safe_rate(df["future_4q_complete"]),
        "sustained_4q_growth_true_rows": int(pd.Series(df["sustained_4q_growth_candidate"]).fillna(False).sum()),
        "downside_4q_risk_true_rows": int(pd.Series(df["next_4q_downside_risk_candidate"]).fillna(False).sum()),
        "validation_pass_count": int((validation["result"] == "PASS").sum()),
        "validation_fail_count": int((validation["result"] == "FAIL").sum()),
        "validation_conditional_pass_count": int((validation["result"] == "CONDITIONAL_PASS").sum()),
        "growth_potential_excess_corr_from_existing_backtest": read_growth_component_corr(),
        "top_diagnostic_abs_corr": max([abs(v) for v in diagnostics["spearman_corr"].dropna().tolist()] or [float("nan")]),
        "segment_counts_top20": segment_counts,
        "decision": "라벨후보_생성_완료_엔진반영은_보류",
        "decision_reason_ko": "미래 라벨과 현재 피처를 분리했으나, 이 산출물만으로 성장 알고리즘을 확정하지 않는다.",
    }


def fmt_num(value: object, digits: int = 6) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        if math.isnan(value):
            return "nan"
        return f"{value:.{digits}f}"
    return str(value)


def write_report(df: pd.DataFrame, diagnostics: pd.DataFrame, validation: pd.DataFrame, summary: dict[str, object]) -> None:
    validation_counts = validation["result"].value_counts().to_dict()
    lines = [
        "# 성장 라벨 후보 전처리 검증",
        "",
        f"작성일: {RUN_DATE}",
        "",
        "## 1. 목적",
        "",
        "현재 `growth_potential_score`를 성장 예측 라벨처럼 쓰지 않기 위해, 미래 매출 기반 성장 라벨 후보를 별도 gold 산출물로 분리했다.",
        "",
        "중요: 이 산출물의 `next_*` 라벨은 백테스트 검증 전용이다. 리포트 실행 시점 입력 피처로 쓰면 시간누수다.",
        "",
        "## 2. 산출물",
        "",
        f"- `datacorpus/_gold/{OUT_LABELS.name}`",
        f"- `datacorpus/_rule_validation/{OUT_VALIDATION.name}`",
        f"- `datacorpus/_rule_validation/{OUT_DIAGNOSTICS.name}`",
        f"- `datacorpus/_rule_validation/{OUT_SUMMARY.name}`",
        "",
        "## 3. 요약",
        "",
        "| 항목 | 값 |",
        "|---|---:|",
        f"| row_count | {summary['row_count']:,} |",
        f"| quarter range | {summary['quarter_min']}~{summary['quarter_max']} |",
        f"| next_q_label_available_rows | {summary['next_q_label_available_rows']:,} |",
        f"| next_q_label_available_rate | {fmt_num(summary['next_q_label_available_rate'])} |",
        f"| next_4q_complete_rows | {summary['next_4q_complete_rows']:,} |",
        f"| next_4q_complete_rate | {fmt_num(summary['next_4q_complete_rate'])} |",
        f"| 기존 growth_potential 초과성장 상관 | {summary['growth_potential_excess_corr_from_existing_backtest']} |",
        f"| validation PASS | {validation_counts.get('PASS', 0)} |",
        f"| validation FAIL | {validation_counts.get('FAIL', 0)} |",
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
            "## 5. 피처-미래라벨 진단",
            "",
            "아래 상관은 후보 진단값이다. 이 값만으로 성장 알고리즘을 확정하지 않는다.",
            "",
            "| feature | label | non_null_rows | spearman_corr |",
            "|---|---|---:|---:|",
        ]
    )
    for row in diagnostics.itertuples(index=False):
        lines.append(f"| {row.feature} | {row.label} | {row.non_null_rows:,} | {fmt_num(row.spearman_corr)} |")

    lines.extend(
        [
            "",
            "## 6. 판정",
            "",
            "성장 라벨 후보 생성은 완료했지만, 알고리즘 반영은 보류한다.",
            "",
            "이유:",
            "",
            "- 다음분기/4분기 라벨은 미래 매출에서 만든 백테스트용 정답지다.",
            "- 현재 리포트 생성 시점에 이 라벨을 피처로 쓰면 시간누수다.",
            "- 기존 `growth_potential_score`는 초과성장과 음의 상관으로 확인되어 운영 라벨로 쓰면 안 된다.",
            "- `상권변화지표` 코드는 원문으로 보존하고, HH/HL/LH/LL 이름만으로 성장 점수화하지 않는다.",
            "",
            "다음 단계:",
            "",
            "1. `gold_growth_label_candidates_q_industry.csv`를 기준으로 성장 라벨 후보별 백테스트를 분리한다.",
            "2. 현재 피처만 사용한 성장/안정성 규칙 후보를 만들고, 미래 라벨과의 관계를 검증한다.",
            "3. 검증을 통과한 경우에만 성장잠재 알고리즘을 재작성한다.",
        ]
    )
    OUT_REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    ensure_dirs()
    sales = load_sales()
    growth = load_growth_features()

    add_validation(
        "검토1_원천근거",
        "성장 feature gold 존재",
        len(growth),
        "0보다 큼",
        len(growth) > 0,
        "점포 개폐업, 매출 추세, 상권변화지표는 성장/안정성 후보 feature로만 사용한다.",
    )

    labels = build_labels(sales, growth)
    labels = select_output_columns(labels)
    diagnostics = build_diagnostics(labels)
    validation = build_validations(labels, diagnostics)
    summary = summarize(labels, diagnostics, validation)

    labels.to_csv(OUT_LABELS, index=False, encoding="utf-8-sig")
    diagnostics.to_csv(OUT_DIAGNOSTICS, index=False, encoding="utf-8-sig")
    validation.to_csv(OUT_VALIDATION, index=False, encoding="utf-8-sig")
    OUT_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_report(labels, diagnostics, validation, summary)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if int(summary["validation_fail_count"]) > 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
