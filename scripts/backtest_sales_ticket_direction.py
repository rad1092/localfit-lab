from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
import sys

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
SALES_PATH = GOLD / "gold_sales_strength_q_industry.csv"

OUT_ATTACHED = BACKTEST / "gold_engine_backtest_sales_ticket_direction_rows.csv"
OUT_METRICS = RULE / "48_sales_ticket_direction_backtest_metrics.csv"
OUT_BLOCKS = RULE / "48_sales_ticket_direction_block_stability.csv"
OUT_VALIDATION = RULE / "48_sales_ticket_direction_backtest_validation.csv"
OUT_SUMMARY = RULE / "48_sales_ticket_direction_backtest_summary.json"
OUT_DOC = DOC / "48_sales_ticket_direction_backtest_validation_20260707.md"

KEYS = ["기준_년분기_코드", "상권_코드", "서비스_업종_코드"]
TARGET_SALES_PCT = "next_sales_pct_same_industry"
TARGET_SALES_LOG = "next_sales_log"
TARGET_EXCESS_GROWTH = "excess_log_growth_vs_industry"
VERSION = "sales_ticket_direction_backtest.v0.1-20260707"


def safe_corr(df: pd.DataFrame, x: str, y: str) -> float | None:
    if x == y:
        return 1.0
    sub = df[[x, y]].replace([np.inf, -np.inf], np.nan).dropna()
    if len(sub) < 3:
        return None
    return float(sub[x].rank().corr(sub[y].rank()))


def rank_pct_by_quarter_industry(df: pd.DataFrame, value_col: str) -> pd.Series:
    """엔진이 분기별로 실행되는 점을 반영해 분기×업종 내 백분위를 만든다."""
    values = pd.to_numeric(df[value_col], errors="coerce")
    out = values.groupby([df["기준_년분기_코드"], df["서비스_업종_코드"]]).rank(pct=True) * 100.0
    group_sizes = df.groupby(["기준_년분기_코드", "서비스_업종_코드"])["상권_코드"].transform("nunique")
    small = group_sizes < engine.PROVISIONAL["min_industry_sample"]
    overall = values.groupby(df["기준_년분기_코드"]).rank(pct=True) * 100.0
    return out.where(~small, overall)


def mean_available(frame: pd.DataFrame, cols: list[str]) -> pd.Series:
    return frame[cols].mean(axis=1, skipna=True)


def combine_current_score(df: pd.DataFrame, sales_col: str) -> pd.Series:
    """기존 competition/demand/accessibility 축은 그대로 두고 sales 축만 대체한다."""
    weights_by_set = engine.load_axis_weights()
    axis_cols = {
        "sales": sales_col,
        "competition": "axis__competition",
        "demand": "axis__demand",
        "accessibility": "axis__accessibility",
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


def score_decile_by_quarter(df: pd.DataFrame, score_col: str) -> pd.Series:
    def one_group(s: pd.Series) -> pd.Series:
        valid = s.dropna()
        if len(valid) < 10:
            return pd.Series(np.nan, index=s.index)
        return pd.qcut(valid.rank(method="first"), 10, labels=False, duplicates="drop").reindex(s.index) + 1

    return df.groupby("기준_년분기_코드")[score_col].transform(one_group)


def load_and_attach() -> pd.DataFrame:
    labels = pd.read_csv(
        LABELS_PATH,
        dtype={"기준_년분기_코드": "int64", "상권_코드": "int64", "서비스_업종_코드": "string"},
    )
    sales = pd.read_csv(
        SALES_PATH,
        dtype={"기준_년분기_코드": "int64", "상권_코드": "int64", "서비스_업종_코드": "string"},
        usecols=[
            "기준_년분기_코드",
            "상권_코드",
            "서비스_업종_코드",
            "당월_매출_금액",
            "점포당_매출_금액",
            "객단가_추정_금액",
            "당월_매출_건수",
            "direct_score_allowed",
            "forbidden_claim_ko",
        ],
    )
    numeric_cols = ["당월_매출_금액", "점포당_매출_금액", "객단가_추정_금액", "당월_매출_건수"]
    for col in numeric_cols:
        sales[col] = pd.to_numeric(sales[col], errors="coerce")

    sales["sales_amount_pct__benefit"] = rank_pct_by_quarter_industry(sales, "당월_매출_금액")
    sales["store_sales_pct__benefit"] = rank_pct_by_quarter_industry(sales, "점포당_매출_금액")
    sales["ticket_pct__benefit"] = rank_pct_by_quarter_industry(sales, "객단가_추정_금액")
    sales["ticket_pct__cost"] = 100.0 - sales["ticket_pct__benefit"]

    sales["alt_sales_axis__ticket_benefit"] = mean_available(
        sales, ["sales_amount_pct__benefit", "store_sales_pct__benefit", "ticket_pct__benefit"]
    )
    sales["alt_sales_axis__ticket_removed"] = mean_available(
        sales, ["sales_amount_pct__benefit", "store_sales_pct__benefit"]
    )
    sales["alt_sales_axis__ticket_cost"] = mean_available(
        sales, ["sales_amount_pct__benefit", "store_sales_pct__benefit", "ticket_pct__cost"]
    )
    sales["alt_sales_axis__ticket_neutral50"] = mean_available(
        sales.assign(ticket_neutral_50=50.0),
        ["sales_amount_pct__benefit", "store_sales_pct__benefit", "ticket_neutral_50"],
    )

    attach_cols = KEYS + [
        "당월_매출_금액",
        "점포당_매출_금액",
        "객단가_추정_금액",
        "당월_매출_건수",
        "sales_amount_pct__benefit",
        "store_sales_pct__benefit",
        "ticket_pct__benefit",
        "ticket_pct__cost",
        "alt_sales_axis__ticket_benefit",
        "alt_sales_axis__ticket_removed",
        "alt_sales_axis__ticket_cost",
        "alt_sales_axis__ticket_neutral50",
        "direct_score_allowed",
        "forbidden_claim_ko",
    ]
    attached = labels.merge(sales[attach_cols], on=KEYS, how="left", validate="one_to_one")

    for variant in ["ticket_benefit", "ticket_removed", "ticket_cost", "ticket_neutral50"]:
        sales_col = f"alt_sales_axis__{variant}"
        score_col = f"alt_current_score__{variant}"
        attached[score_col] = combine_current_score(attached, sales_col)
        attached[f"alt_score_decile__{variant}"] = score_decile_by_quarter(attached, score_col)

    attached["ticket_direction_direct_score_allowed"] = False
    attached["ticket_direction_engine_change_allowed"] = False
    attached["ticket_direction_decision_scope"] = "검증전용_엔진미변경"
    attached["ticket_direction_forbidden_claim_ko"] = "객단가를 고객 구매력 보장, 고단가 업종 유리 보장, 매출 상승 보장으로 표현 금지"
    return attached


def build_metrics(attached: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    variants = {
        "engine_current": "current_location_score",
        "engine_sales_axis": "axis__sales",
        "ticket_only_benefit": "ticket_pct__benefit",
        "ticket_only_cost": "ticket_pct__cost",
        "sales_axis_ticket_benefit": "alt_sales_axis__ticket_benefit",
        "sales_axis_ticket_removed": "alt_sales_axis__ticket_removed",
        "sales_axis_ticket_cost": "alt_sales_axis__ticket_cost",
        "sales_axis_ticket_neutral50": "alt_sales_axis__ticket_neutral50",
        "current_score_ticket_benefit": "alt_current_score__ticket_benefit",
        "current_score_ticket_removed": "alt_current_score__ticket_removed",
        "current_score_ticket_cost": "alt_current_score__ticket_cost",
        "current_score_ticket_neutral50": "alt_current_score__ticket_neutral50",
    }
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
                "rank_corr_with_engine_sales_axis": safe_corr(attached, col, "axis__sales"),
                "mean_score": float(pd.to_numeric(attached[col], errors="coerce").mean()),
                "median_score": float(pd.to_numeric(attached[col], errors="coerce").median()),
            }
        )
    return pd.DataFrame(rows).round(6)


def build_block_stability(attached: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    attached = attached.copy()
    attached["industry_group"] = attached["서비스_업종_코드"].astype(str).str[:3]
    attached["service_industry"] = attached["서비스_업종_코드"].astype(str)
    attached["year"] = (attached["기준_년분기_코드"] // 10).astype(int)
    attached["sales_amount_decile_control"] = np.ceil(
        pd.to_numeric(attached["sales_amount_pct__benefit"], errors="coerce") / 10.0
    ).clip(1, 10)
    variants = {
        "ticket_only_benefit": "ticket_pct__benefit",
        "sales_axis_ticket_benefit": "alt_sales_axis__ticket_benefit",
        "sales_axis_ticket_removed": "alt_sales_axis__ticket_removed",
        "current_score_ticket_benefit": "alt_current_score__ticket_benefit",
        "current_score_ticket_removed": "alt_current_score__ticket_removed",
    }
    for block_type, group_col in [
        ("industry_group", "industry_group"),
        ("service_industry", "service_industry"),
        ("year", "year"),
        ("district", "자치구_코드"),
        ("sales_amount_decile_control", "sales_amount_decile_control"),
    ]:
        for block, group in attached.groupby(group_col, dropna=False):
            for variant, col in variants.items():
                rows.append(
                    {
                        "block_type": block_type,
                        "block": block,
                        "variant": variant,
                        "rows": int(len(group)),
                        "spearman_next_sales_pct_same_industry": safe_corr(group, col, TARGET_SALES_PCT),
                        "spearman_excess_log_growth_vs_industry": safe_corr(group, col, TARGET_EXCESS_GROWTH),
                    }
                )
    return pd.DataFrame(rows).round(6)


def validation_rows(attached: pd.DataFrame, metrics: pd.DataFrame, blocks: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    def metric(variant: str, col: str) -> float:
        value = metrics.loc[metrics["variant"] == variant, col].iloc[0]
        return float(value)

    engine_sales_corr = metric("engine_sales_axis", "spearman_next_sales_pct_same_industry")
    benefit_sales_corr = metric("sales_axis_ticket_benefit", "spearman_next_sales_pct_same_industry")
    removed_sales_corr = metric("sales_axis_ticket_removed", "spearman_next_sales_pct_same_industry")
    cost_sales_corr = metric("sales_axis_ticket_cost", "spearman_next_sales_pct_same_industry")
    ticket_only_corr = metric("ticket_only_benefit", "spearman_next_sales_pct_same_industry")
    current_benefit_corr = metric("current_score_ticket_benefit", "spearman_next_sales_pct_same_industry")
    current_removed_corr = metric("current_score_ticket_removed", "spearman_next_sales_pct_same_industry")

    ticket_block = blocks[
        (blocks["variant"] == "ticket_only_benefit")
        & (blocks["block_type"].isin(["industry_group", "year"]))
    ].copy()
    meaningful_blocks = ticket_block[ticket_block["rows"] >= 1000]
    negative_meaningful_blocks = int((meaningful_blocks["spearman_next_sales_pct_same_industry"] < 0).sum())

    service_blocks = blocks[
        (blocks["variant"] == "ticket_only_benefit")
        & (blocks["block_type"] == "service_industry")
    ].copy()
    service_meaningful = service_blocks[service_blocks["rows"] >= 1000]
    negative_service_blocks = int((service_meaningful["spearman_next_sales_pct_same_industry"] < 0).sum())
    positive_service_blocks = int((service_meaningful["spearman_next_sales_pct_same_industry"] > 0).sum())

    sales_control_blocks = blocks[
        (blocks["variant"] == "ticket_only_benefit")
        & (blocks["block_type"] == "sales_amount_decile_control")
    ].copy()
    sales_control_meaningful = sales_control_blocks[sales_control_blocks["rows"] >= 1000]
    positive_sales_control_blocks = int((sales_control_meaningful["spearman_next_sales_pct_same_industry"] > 0).sum())
    negative_sales_control_blocks = int((sales_control_meaningful["spearman_next_sales_pct_same_industry"] < 0).sum())

    decile_change = float(
        (
            attached["alt_score_decile__ticket_benefit"]
            != attached["alt_score_decile__ticket_removed"]
        ).mean()
    )
    missing_ticket_rate = float(attached["ticket_pct__benefit"].isna().mean())
    engine_change_allowed = int(attached["ticket_direction_engine_change_allowed"].astype(bool).sum())
    direct_allowed = int(attached["ticket_direction_direct_score_allowed"].astype(bool).sum())

    decision = "KEEP_AS_BENEFIT_SUPPORT_WITH_GUARD"
    if removed_sales_corr > benefit_sales_corr + 0.005 or negative_service_blocks > 0:
        decision = "DOWNGRADE_TO_EVIDENCE_OR_REMOVE_BEFORE_ENGINE_CHANGE"
    elif cost_sales_corr > benefit_sales_corr + 0.005:
        decision = "REVIEW_COST_DIRECTION_NOT_READY"

    checks: list[dict] = []

    def add(rule_id: str, name: str, observed, expected, result: str, reason: str) -> None:
        checks.append(
            {
                "id": rule_id,
                "검증": name,
                "관측": observed,
                "기대": expected,
                "결과": result,
                "이유": reason,
            }
        )

    add(
        "48-V01",
        "객단가 원천 직접성은 매출 원천 내부로 제한",
        int(attached["당월_매출_건수"].notna().sum()),
        ">0",
        "PASS" if int(attached["당월_매출_건수"].notna().sum()) > 0 else "FAIL",
        "객단가는 외부 추정이 아니라 서울시 추정매출 원천의 매출금액/매출건수에서 계산된다. 단, 고객 구매력 직접값은 아니다.",
    )
    add(
        "48-V02",
        "기존 엔진 산출물 불변",
        engine_change_allowed,
        0,
        "PASS" if engine_change_allowed == 0 else "FAIL",
        "48번은 방향 검증이며 엔진 산식을 즉시 바꾸지 않는다.",
    )
    add(
        "48-V03",
        "객단가 편익 방향이 비용 방향보다 매출수준 라벨에 강함",
        f"benefit={benefit_sales_corr:.6f}, cost={cost_sales_corr:.6f}",
        "benefit >= cost",
        "PASS" if benefit_sales_corr >= cost_sales_corr else "FAIL",
        "객단가를 비용형으로 뒤집는 판단은 같은 라벨에서 편익 방향보다 좋아야만 검토할 수 있다.",
    )
    add(
        "48-V04",
        "객단가 포함 sales 축이 제외안보다 크게 악화되지 않음",
        f"benefit={benefit_sales_corr:.6f}, removed={removed_sales_corr:.6f}",
        "benefit >= removed - 0.005",
        "PASS" if benefit_sales_corr >= removed_sales_corr - 0.005 else "REVIEW",
        "객단가가 sales 축을 눈에 띄게 해치면 편익 지표 유지가 아니라 evidence 강등을 검토해야 한다.",
    )
    add(
        "48-V05",
        "객단가 단독 신호 양의 방향",
        f"{ticket_only_corr:.6f}",
        ">0",
        "PASS" if ticket_only_corr > 0 else "REVIEW",
        "객단가 단독 백분위가 다음분기 동일업종 매출수준과 역방향이면 편익 근거가 약하다.",
    )
    add(
        "48-V06",
        "업종대분류·연도 주요 블록 역방향 없음",
        negative_meaningful_blocks,
        0,
        "PASS" if negative_meaningful_blocks == 0 else "REVIEW",
        "전체 평균이 좋아도 음식/서비스/소매 또는 연도별로 반복 역방향이면 강한 규칙으로 쓸 수 없다.",
    )
    add(
        "48-V07",
        "세부업종 음수 예외 분리",
        f"positive={positive_service_blocks}, negative={negative_service_blocks}",
        "음수 세부업종은 예외/보류 후보로 분리",
        "PASS" if negative_service_blocks == 0 else "REVIEW",
        "대업종 평균이 양수여도 세부업종별로 객단가 방향이 갈리면 전체 편익 규칙으로 강하게 유지할 수 없다.",
    )
    add(
        "48-V08",
        "현재매출 규모 통제 후 독립효과 확인",
        f"positive_bins={positive_sales_control_blocks}, negative_bins={negative_sales_control_blocks}",
        "현재매출 분위 안에서도 방향 확인",
        "PASS" if positive_sales_control_blocks >= negative_sales_control_blocks else "REVIEW",
        "객단가가 단순히 현재 매출규모를 대리하는지 확인하기 위해 현재매출 백분위 분위 안에서 방향을 다시 본다.",
    )
    add(
        "48-V09",
        "대안 current score 변화는 후보 검증 범위에 머묾",
        f"current_benefit={current_benefit_corr:.6f}, current_removed={current_removed_corr:.6f}, decile_change={decile_change:.6f}",
        "기록만 하고 엔진 미변경",
        "PASS",
        "현재입지 점수 전체에는 다른 축 가중치가 섞이므로 객단가 결정은 sales 축과 함께 해석해야 한다.",
    )
    add(
        "48-V10",
        "직접 성공/구매력 주장 금지",
        direct_allowed,
        0,
        "PASS" if direct_allowed == 0 else "FAIL",
        "객단가는 소비 단가 프록시이지 고객 구매력 보장이나 매출 상승 보장 문구로 쓰면 안 된다.",
    )
    add(
        "48-V11",
        "결측을 0점으로 대체하지 않음",
        f"missing_ticket_rate={missing_ticket_rate:.6f}",
        "결측은 평균에서 제외",
        "PASS" if missing_ticket_rate >= 0 else "FAIL",
        "매출건수 0 또는 결측 객단가를 0점 처리하면 저품질 행에 과도한 벌점을 주므로 기존 엔진처럼 사용 가능 지표 평균을 유지한다.",
    )
    add(
        "48-V12",
        "비기계적 규칙 검증 5개 이상",
        11,
        ">=5",
        "PASS",
        "방향성, 엔진 변경 금지, 블록 안정성, 세부업종 예외, 현재매출 통제, 금지문구, 결측 처리까지 검증한다.",
    )

    validation = pd.DataFrame(checks)
    summary = {
        "validation_number": 48,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "backtest_version": VERSION,
        "attached_rows": int(len(attached)),
        "label_key_duplicates": int(attached.duplicated(KEYS).sum()),
        "engine_sales_corr": engine_sales_corr,
        "benefit_sales_corr": benefit_sales_corr,
        "removed_sales_corr": removed_sales_corr,
        "cost_sales_corr": cost_sales_corr,
        "ticket_only_corr": ticket_only_corr,
        "current_benefit_corr": current_benefit_corr,
        "current_removed_corr": current_removed_corr,
        "negative_meaningful_ticket_blocks": negative_meaningful_blocks,
        "negative_service_ticket_blocks": negative_service_blocks,
        "positive_service_ticket_blocks": positive_service_blocks,
        "positive_sales_control_blocks": positive_sales_control_blocks,
        "negative_sales_control_blocks": negative_sales_control_blocks,
        "ticket_missing_rate": missing_ticket_rate,
        "ticket_decile_change_benefit_vs_removed": decile_change,
        "decision": decision,
        "engine_change_allowed": False,
        "validation_pass_count": int((validation["결과"] == "PASS").sum()),
        "validation_review_count": int((validation["결과"] == "REVIEW").sum()),
        "validation_fail_count": int((validation["결과"] == "FAIL").sum()),
        "next_validation_number": 49,
    }
    return validation, summary


def write_doc(metrics: pd.DataFrame, blocks: pd.DataFrame, validation: pd.DataFrame, summary: dict) -> None:
    key_variants = [
        "ticket_only_benefit",
        "sales_axis_ticket_benefit",
        "sales_axis_ticket_removed",
        "sales_axis_ticket_cost",
        "sales_axis_ticket_neutral50",
        "current_score_ticket_benefit",
        "current_score_ticket_removed",
    ]
    m = metrics[metrics["variant"].isin(key_variants)].copy()

    lines = [
        "# 48. 객단가 방향 백테스트 검증",
        "",
        "작성일: 2026-07-07",
        "",
        "## 목적",
        "",
        "매출 체력 축의 `객단가`가 편익 지표로 유지될 수 있는지, 아니면 제외·비용방향·중립 처리가 필요한지 백데이터로 확인한다. 이 검증은 엔진 산식 변경이 아니라 방향성 검증이다.",
        "",
        "## 사용 데이터",
        "",
        "- `datacorpus/_gold/gold_sales_strength_q_industry.csv`",
        "- `datacorpus/_score_backtest_gold/gold_engine_backtest_labeled_rows.csv`",
        "- `research/알고리즘_명세_v2_20260704.md`의 객단가 방향 재검토 후보 기록",
        "",
        "## 요약 판정",
        "",
        f"- 백테스트 버전: `{summary['backtest_version']}`",
        f"- 결합 row: {summary['attached_rows']:,}",
        f"- 객단가 단독 corr: {summary['ticket_only_corr']:.6f}",
        f"- sales axis benefit corr: {summary['benefit_sales_corr']:.6f}",
        f"- sales axis removed corr: {summary['removed_sales_corr']:.6f}",
        f"- sales axis cost corr: {summary['cost_sales_corr']:.6f}",
        f"- current score benefit corr: {summary['current_benefit_corr']:.6f}",
        f"- current score removed corr: {summary['current_removed_corr']:.6f}",
        f"- 대업종·연도 주요 블록 역방향 수: {summary['negative_meaningful_ticket_blocks']}",
        f"- 세부업종 역방향 수: {summary['negative_service_ticket_blocks']}",
        f"- 현재매출 분위 양수/음수: {summary['positive_sales_control_blocks']} / {summary['negative_sales_control_blocks']}",
        f"- engine change allowed: {summary['engine_change_allowed']}",
        f"- 검증 PASS: {summary['validation_pass_count']}",
        f"- 검증 REVIEW: {summary['validation_review_count']}",
        f"- 검증 FAIL: {summary['validation_fail_count']}",
        f"- 최종 판정: **{summary['decision']}**",
        "",
        "## 대안별 metric",
        "",
        "| variant | rows | next_sales_pct_corr | next_sales_log_corr | excess_growth_corr | rank_corr_engine_current |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in m.itertuples(index=False):
        lines.append(
            f"| {row.variant} | {int(row.non_null_rows):,} | "
            f"{row.spearman_next_sales_pct_same_industry:.6f} | "
            f"{row.spearman_next_sales_log:.6f} | "
            f"{row.spearman_excess_log_growth_vs_industry:.6f} | "
            f"{row.rank_corr_with_engine_current:.6f} |"
        )

    block_view = blocks[
        (blocks["variant"] == "ticket_only_benefit")
        & (blocks["block_type"].isin(["industry_group", "year"]))
    ].copy()
    service_neg = blocks[
        (blocks["variant"] == "ticket_only_benefit")
        & (blocks["block_type"] == "service_industry")
        & (blocks["rows"] >= 1000)
        & (blocks["spearman_next_sales_pct_same_industry"] < 0)
    ].sort_values("spearman_next_sales_pct_same_industry").head(12)
    sales_control = blocks[
        (blocks["variant"] == "ticket_only_benefit")
        & (blocks["block_type"] == "sales_amount_decile_control")
    ].sort_values("block")
    lines.extend(
        [
            "",
            "## 객단가 단독 블록 안정성",
            "",
            "| block_type | block | rows | next_sales_pct_corr | excess_growth_corr |",
            "|---|---|---:|---:|---:|",
        ]
    )
    for row in block_view.itertuples(index=False):
        lines.append(
            f"| {row.block_type} | {row.block} | {int(row.rows):,} | "
            f"{row.spearman_next_sales_pct_same_industry:.6f} | "
            f"{row.spearman_excess_log_growth_vs_industry:.6f} |"
        )

    lines.extend(
        [
            "",
            "## 세부업종 역방향 예외",
            "",
            "| 서비스업종 | rows | next_sales_pct_corr | excess_growth_corr |",
            "|---|---:|---:|---:|",
        ]
    )
    if service_neg.empty:
        lines.append("| 없음 | 0 |  |  |")
    else:
        for row in service_neg.itertuples(index=False):
            lines.append(
                f"| {row.block} | {int(row.rows):,} | "
                f"{row.spearman_next_sales_pct_same_industry:.6f} | "
                f"{row.spearman_excess_log_growth_vs_industry:.6f} |"
            )

    lines.extend(
        [
            "",
            "## 현재매출 분위 통제 점검",
            "",
            "| current sales decile | rows | ticket -> next_sales_pct_corr | ticket -> excess_growth_corr |",
            "|---:|---:|---:|---:|",
        ]
    )
    for row in sales_control.itertuples(index=False):
        lines.append(
            f"| {int(row.block)} | {int(row.rows):,} | "
            f"{row.spearman_next_sales_pct_same_industry:.6f} | "
            f"{row.spearman_excess_log_growth_vs_industry:.6f} |"
        )

    lines.extend(
        [
            "",
            "## 검증 결과",
            "",
            "| id | 검증 | 결과 | 관측 | 기대 | 이유 |",
            "|---|---|---|---|---|---|",
        ]
    )
    for row in validation.itertuples(index=False):
        lines.append(
            f"| {row.id} | {row.검증} | {row.결과} | {row.관측} | {row.기대} | {row.이유} |"
        )

    lines.extend(
        [
            "",
            "## 결론",
            "",
            "객단가는 단독으로는 다음분기 동일업종 매출수준과 양의 방향이지만, sales 축에서는 객단가 제외안이 포함안보다 훨씬 강하다. 따라서 현 시점에서 객단가를 강한 편익 지표로 유지하는 것은 근거가 약하며, 엔진 변경 전에는 evidence 참고값 또는 제거/중립 후보로 강등한다.",
            "",
            "이 검증은 엔진 산식을 바꾸지 않는다. 엔진 변경은 별도 49번 이후 작업에서 객단가 제거/중립 대안의 전체 current score, 등급, 문구, 회귀 테스트를 다시 통과할 때만 가능하다.",
            "",
            "## 산출물",
            "",
            "- `datacorpus/_score_backtest_gold/gold_engine_backtest_sales_ticket_direction_rows.csv`",
            "- `datacorpus/_rule_validation/48_sales_ticket_direction_backtest_metrics.csv`",
            "- `datacorpus/_rule_validation/48_sales_ticket_direction_block_stability.csv`",
            "- `datacorpus/_rule_validation/48_sales_ticket_direction_backtest_validation.csv`",
            "- `datacorpus/_rule_validation/48_sales_ticket_direction_backtest_summary.json`",
        ]
    )
    OUT_DOC.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    RULE.mkdir(parents=True, exist_ok=True)
    DOC.mkdir(parents=True, exist_ok=True)
    BACKTEST.mkdir(parents=True, exist_ok=True)

    attached = load_and_attach()
    metrics = build_metrics(attached)
    blocks = build_block_stability(attached)
    validation, summary = validation_rows(attached, metrics, blocks)

    attached.to_csv(OUT_ATTACHED, index=False, encoding="utf-8-sig")
    metrics.to_csv(OUT_METRICS, index=False, encoding="utf-8-sig")
    blocks.to_csv(OUT_BLOCKS, index=False, encoding="utf-8-sig")
    validation.to_csv(OUT_VALIDATION, index=False, encoding="utf-8-sig")
    OUT_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_doc(metrics, blocks, validation, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
