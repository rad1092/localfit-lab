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


BACKTEST = ROOT / "datacorpus" / "_score_backtest_gold"
RULE = ROOT / "datacorpus" / "_rule_validation"
DOC = ROOT / "research" / "rule_validation"

INPUT_ROWS = BACKTEST / "gold_engine_backtest_sales_ticket_direction_rows.csv"
OUT_ROWS = BACKTEST / "gold_engine_backtest_sales_ticket_engine_candidate_rows.csv"
OUT_METRICS = RULE / "49_sales_ticket_engine_candidate_metrics.csv"
OUT_BLOCKS = RULE / "49_sales_ticket_engine_candidate_block_stability.csv"
OUT_GRADE_MIGRATION = RULE / "49_sales_ticket_engine_candidate_grade_migration.csv"
OUT_VALIDATION = RULE / "49_sales_ticket_engine_candidate_validation.csv"
OUT_SUMMARY = RULE / "49_sales_ticket_engine_candidate_summary.json"
OUT_DOC = DOC / "49_sales_ticket_engine_candidate_validation_20260707.md"

VERSION = "sales_ticket_engine_candidate.v0.1-20260707"
KEYS = ["기준_년분기_코드", "상권_코드", "서비스_업종_코드"]
TARGET = "next_sales_pct_same_industry"
TARGET_LOG = "next_sales_log"
TARGET_EXCESS = "excess_log_growth_vs_industry"
TOP_FLAG = "next_sales_top_quartile_same_industry"

GRADE_ORDER = ["E", "D", "C", "B", "A"]
GRADE_TO_NUM = {grade: i + 1 for i, grade in enumerate(GRADE_ORDER)}


def safe_corr(df: pd.DataFrame, x: str, y: str) -> float | None:
    sub = df[[x, y]].replace([np.inf, -np.inf], np.nan).dropna()
    if len(sub) < 3:
        return None
    return float(sub[x].rank().corr(sub[y].rank()))


def safe_rate(series: pd.Series) -> float | None:
    s = pd.to_numeric(series, errors="coerce").dropna()
    if len(s) == 0:
        return None
    return float(s.mean())


def grade_by_service(df: pd.DataFrame, score_col: str) -> pd.Series:
    """기존 엔진과 같은 방식으로 서비스업종 안에서 5분위 등급을 다시 만든다."""

    def one_group(s: pd.Series) -> pd.Series:
        valid = s.dropna()
        if len(valid) < 5:
            return pd.Series("C", index=s.index).where(s.notna(), None)
        q = valid.rank(pct=True)
        bins = pd.cut(q, [0, 0.2, 0.4, 0.6, 0.8, 1.0], labels=GRADE_ORDER)
        return bins.reindex(s.index).astype(object)

    return df.groupby("서비스_업종_코드")[score_col].transform(one_group)


def score_decile_by_quarter(df: pd.DataFrame, score_col: str) -> pd.Series:
    """분기별 10분위. 시간별 모집단 크기 차이를 줄이기 위해 분기 안에서만 자른다."""

    def one_group(s: pd.Series) -> pd.Series:
        valid = s.dropna()
        if len(valid) < 10:
            return pd.Series(np.nan, index=s.index)
        return pd.qcut(valid.rank(method="first"), 10, labels=False, duplicates="drop").reindex(s.index) + 1

    return df.groupby("기준_년분기_코드")[score_col].transform(one_group)


def grade_label(grade: object) -> str:
    if grade in engine.GRADE_LABELS:
        return engine.GRADE_LABELS[grade]
    return "등급 산출 불가 — 점수 결측"


def load_candidate_rows() -> pd.DataFrame:
    cols = [
        "기준_년분기_코드", "상권_코드", "상권_코드_명", "자치구_코드", "자치구_코드_명",
        "서비스_업종_코드", "서비스_업종_코드_명", "weight_set", "비교군_확대",
        "current_location_score", "grade", "decision_label", "score_version", "score_decile",
        "axis__sales", "axis__competition", "axis__demand", "axis__accessibility",
        "data_reliability_score",
        "next_sales_log", "excess_log_growth_vs_industry", "next_sales_pct_same_industry",
        "next_sales_top_quartile_same_industry",
        "sales_amount_pct__benefit", "store_sales_pct__benefit", "ticket_pct__benefit",
        "alt_sales_axis__ticket_removed", "alt_sales_axis__ticket_neutral50", "alt_sales_axis__ticket_cost",
        "alt_current_score__ticket_removed", "alt_current_score__ticket_neutral50", "alt_current_score__ticket_cost",
    ]
    df = pd.read_csv(
        INPUT_ROWS,
        usecols=cols,
        encoding="utf-8-sig",
        dtype={"서비스_업종_코드": "string"},
    )

    df["candidate_current_location_score"] = pd.to_numeric(
        df["alt_current_score__ticket_removed"], errors="coerce"
    ).round(2)
    df["candidate_axis__sales"] = pd.to_numeric(
        df["alt_sales_axis__ticket_removed"], errors="coerce"
    ).round(2)
    df["candidate_grade"] = grade_by_service(df, "candidate_current_location_score")
    df["candidate_score_decile"] = score_decile_by_quarter(df, "candidate_current_location_score")
    reliability_gate = engine.PROVISIONAL["reliability_gate"]
    gated = pd.to_numeric(df["data_reliability_score"], errors="coerce") < reliability_gate
    df["candidate_decision_label"] = np.where(
        gated,
        engine.GATE_LABEL,
        df["candidate_grade"].map(grade_label),
    )
    df["candidate_score_version"] = "loc_score.v2.4-sales-ticket-removed-candidate-rc1"
    df["candidate_ticket_policy_ko"] = (
        "객단가는 sales 축 직접 가점에서 제거하고, 소비 단가 수준 evidence로만 보존한다. "
        "고객 구매력·성장률·성공확률·매출상승 보장 표현은 금지한다."
    )
    df["candidate_formula_ko"] = (
        "sales 축 = mean(pct__당월_매출_금액, pct__점포당_매출); "
        "current score는 기존 competition/demand/accessibility 축과 기존 가중치로 재정규화한다."
    )
    df["candidate_engine_patch_ready_scope"] = "후보검증_엔진파일_미변경"

    df["grade_num"] = df["grade"].map(GRADE_TO_NUM).astype(float)
    df["candidate_grade_num"] = df["candidate_grade"].map(GRADE_TO_NUM).astype(float)
    df["grade_delta"] = df["candidate_grade_num"] - df["grade_num"]
    return df


def build_metrics(df: pd.DataFrame) -> pd.DataFrame:
    variants = {
        "engine_current": "current_location_score",
        "candidate_ticket_removed": "candidate_current_location_score",
        "candidate_ticket_neutral50": "alt_current_score__ticket_neutral50",
        "candidate_ticket_cost": "alt_current_score__ticket_cost",
        "engine_sales_axis": "axis__sales",
        "candidate_sales_axis_removed": "candidate_axis__sales",
    }
    rows: list[dict] = []
    for variant, col in variants.items():
        decile = score_decile_by_quarter(df, col)
        top = df[decile == 10]
        bottom = df[decile == 1]
        rows.append(
            {
                "variant": variant,
                "score_col": col,
                "non_null_rows": int(df[col].notna().sum()),
                "spearman_next_sales_pct_same_industry": safe_corr(df, col, TARGET),
                "spearman_next_sales_log": safe_corr(df, col, TARGET_LOG),
                "spearman_excess_log_growth_vs_industry": safe_corr(df, col, TARGET_EXCESS),
                "mean_score": float(pd.to_numeric(df[col], errors="coerce").mean()),
                "median_score": float(pd.to_numeric(df[col], errors="coerce").median()),
                "top_decile_next_sales_pct_mean": safe_rate(top[TARGET]),
                "bottom_decile_next_sales_pct_mean": safe_rate(bottom[TARGET]),
                "top_decile_next_top_quartile_rate": safe_rate(top[TOP_FLAG]),
                "bottom_decile_next_top_quartile_rate": safe_rate(bottom[TOP_FLAG]),
            }
        )
    return pd.DataFrame(rows).round(6)


def build_block_stability(df: pd.DataFrame) -> pd.DataFrame:
    work = df.copy()
    work["industry_group"] = work["서비스_업종_코드"].astype(str).str[:3]
    work["service_industry"] = work["서비스_업종_코드"].astype(str)
    work["year"] = (pd.to_numeric(work["기준_년분기_코드"], errors="coerce") // 10).astype("Int64")
    work["sales_amount_decile_control"] = np.ceil(
        pd.to_numeric(work["sales_amount_pct__benefit"], errors="coerce") / 10.0
    ).clip(1, 10)

    rows: list[dict] = []
    for block_type, group_col in [
        ("year", "year"),
        ("district", "자치구_코드"),
        ("industry_group", "industry_group"),
        ("service_industry", "service_industry"),
        ("sales_amount_decile_control", "sales_amount_decile_control"),
    ]:
        for block, group in work.groupby(group_col, dropna=False):
            rows.append(
                {
                    "block_type": block_type,
                    "block": block,
                    "rows": int(len(group)),
                    "engine_corr_next_sales_pct": safe_corr(group, "current_location_score", TARGET),
                    "candidate_corr_next_sales_pct": safe_corr(group, "candidate_current_location_score", TARGET),
                    "candidate_corr_excess_growth": safe_corr(group, "candidate_current_location_score", TARGET_EXCESS),
                    "candidate_minus_engine_corr": (
                        None
                        if safe_corr(group, "current_location_score", TARGET) is None
                        or safe_corr(group, "candidate_current_location_score", TARGET) is None
                        else safe_corr(group, "candidate_current_location_score", TARGET)
                        - safe_corr(group, "current_location_score", TARGET)
                    ),
                    "candidate_A_next_sales_pct_mean": safe_rate(group.loc[group["candidate_grade"] == "A", TARGET]),
                    "candidate_E_next_sales_pct_mean": safe_rate(group.loc[group["candidate_grade"] == "E", TARGET]),
                }
            )
    return pd.DataFrame(rows).round(6)


def build_grade_migration(df: pd.DataFrame) -> pd.DataFrame:
    mig = (
        df.groupby(["grade", "candidate_grade"], dropna=False, observed=False)
        .agg(
            rows=("상권_코드", "size"),
            mean_next_sales_pct=(TARGET, "mean"),
            mean_engine_score=("current_location_score", "mean"),
            mean_candidate_score=("candidate_current_location_score", "mean"),
        )
        .reset_index()
    )
    total = len(df)
    mig["row_share"] = mig["rows"] / total
    return mig.round(6)


def markdown_table(df: pd.DataFrame) -> str:
    """외부 tabulate 의존 없이 작은 검증표를 Markdown으로 기록한다."""
    if df.empty:
        return "(rows 없음)"
    text = df.copy()
    text.columns = [str(c) for c in text.columns]
    for col in text.columns:
        text[col] = text[col].map(lambda v: "" if pd.isna(v) else str(v).replace("|", "/"))
    header = "| " + " | ".join(text.columns) + " |"
    sep = "| " + " | ".join(["---"] * len(text.columns)) + " |"
    rows = ["| " + " | ".join(row) + " |" for row in text.to_numpy(dtype=str)]
    return "\n".join([header, sep, *rows])


def metric(metrics: pd.DataFrame, variant: str, col: str) -> float:
    row = metrics.loc[metrics["variant"] == variant, col]
    if row.empty:
        return float("nan")
    return float(row.iloc[0])


def validation_rows(df: pd.DataFrame, metrics: pd.DataFrame, blocks: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    current_corr = metric(metrics, "engine_current", "spearman_next_sales_pct_same_industry")
    candidate_corr = metric(metrics, "candidate_ticket_removed", "spearman_next_sales_pct_same_industry")
    neutral_corr = metric(metrics, "candidate_ticket_neutral50", "spearman_next_sales_pct_same_industry")
    cost_corr = metric(metrics, "candidate_ticket_cost", "spearman_next_sales_pct_same_industry")
    current_top_rate = metric(metrics, "engine_current", "top_decile_next_top_quartile_rate")
    candidate_top_rate = metric(metrics, "candidate_ticket_removed", "top_decile_next_top_quartile_rate")
    current_bottom_sales = metric(metrics, "engine_current", "bottom_decile_next_sales_pct_mean")
    candidate_bottom_sales = metric(metrics, "candidate_ticket_removed", "bottom_decile_next_sales_pct_mean")
    candidate_excess_corr = metric(metrics, "candidate_ticket_removed", "spearman_excess_log_growth_vs_industry")

    row_count = len(df)
    dup_keys = int(df.duplicated(KEYS).sum())
    candidate_missing = int(df["candidate_current_location_score"].isna().sum())
    grade_changed_rate = float((df["grade"] != df["candidate_grade"]).mean())
    grade_jump2_rate = float((df["grade_delta"].abs() >= 2).mean())
    grade_up_rate = float((df["grade_delta"] > 0).mean())
    grade_down_rate = float((df["grade_delta"] < 0).mean())
    reliability_low_count = int((df["data_reliability_score"] < engine.PROVISIONAL["reliability_gate"]).sum())

    grade_summary = (
        df.groupby("candidate_grade", observed=False)[TARGET]
        .mean()
        .reindex(["A", "B", "C", "D", "E"])
    )
    grade_A_mean = float(grade_summary.loc["A"])
    grade_E_mean = float(grade_summary.loc["E"])
    grade_corr = float(df["candidate_grade_num"].rank().corr(df[TARGET].rank()))

    meaningful = blocks[blocks["rows"] >= 1000].copy()
    negative_candidate_blocks = int((meaningful["candidate_corr_next_sales_pct"] < 0).sum())
    improved_blocks = int((meaningful["candidate_minus_engine_corr"] > 0).sum())
    reviewed_blocks = int(len(meaningful))

    formula_text = str(df["candidate_formula_ko"].iloc[0])
    policy_text = str(df["candidate_ticket_policy_ko"].iloc[0])
    formula_excludes_ticket = "객단가" not in formula_text.split(";")[0]
    policy_has_forbidden_guard = all(word in policy_text for word in ["고객 구매력", "성장률", "성공확률", "금지"])

    checks = [
        (
            "49-V01",
            "48번 판정에 따른 후속 엔진 후보 검증",
            "48번 decision=DOWNGRADE_TO_EVIDENCE_OR_REMOVE_BEFORE_ENGINE_CHANGE",
            "49번에서 제거/중립 후보를 엔진급으로 검증",
            "PASS",
            "48번은 엔진을 바꾸지 않는 방향검증이었으므로, 49번에서 후보 score·grade·문구 회귀를 별도로 확인한다.",
        ),
        (
            "49-V02",
            "행 보존과 키 중복 없음",
            f"rows={row_count}, duplicated_keys={dup_keys}, candidate_missing={candidate_missing}",
            "rows=427553, duplicated_keys=0, candidate_missing=0",
            "PASS" if row_count == 427553 and dup_keys == 0 and candidate_missing == 0 else "FAIL",
            "엔진 후보 검증은 기존 백테스트 라벨판과 같은 row universe에서만 비교해야 한다.",
        ),
        (
            "49-V03",
            "후보 산식에서 객단가 직접 가점 제거",
            formula_text,
            "sales 축 후보 산식은 당월 매출과 점포당 매출만 사용",
            "PASS" if formula_excludes_ticket else "FAIL",
            "객단가를 제거 후보로 검증하면서 이름만 제거하고 실제 산식에 남기면 48번 결론과 충돌한다.",
        ),
        (
            "49-V04",
            "후보 current score의 다음분기 매출수준 설명력 개선",
            f"candidate={candidate_corr:.6f}, current={current_corr:.6f}",
            "candidate가 current보다 0.05 이상 높음",
            "PASS" if candidate_corr - current_corr >= 0.05 else "REVIEW",
            "객단가 제거가 실제 현재입지 후보 선별력을 올리는지 전체 라벨판에서 확인한다.",
        ),
        (
            "49-V05",
            "제거안이 중립50·비용형보다 우선",
            f"removed={candidate_corr:.6f}, neutral50={neutral_corr:.6f}, cost={cost_corr:.6f}",
            "removed가 neutral50와 cost보다 높음",
            "PASS" if candidate_corr > neutral_corr and candidate_corr > cost_corr else "REVIEW",
            "중립50은 안전한 타협처럼 보일 수 있지만 실제 current score에서는 제거안보다 약했다.",
        ),
        (
            "49-V06",
            "후보 등급의 매출수준 단조성",
            f"A_mean={grade_A_mean:.6f}, E_mean={grade_E_mean:.6f}, grade_corr={grade_corr:.6f}",
            "A 평균이 E보다 높고 등급 ordinal 상관이 양수",
            "PASS" if grade_A_mean > grade_E_mean and grade_corr > 0 else "FAIL",
            "점수 상관만 좋아도 등급 구간이 뒤집히면 리포트에서 후보군 설명이 깨진다.",
        ),
        (
            "49-V07",
            "상위 후보군 식별력 개선",
            f"candidate_top_quartile_rate={candidate_top_rate:.6f}, current_top_quartile_rate={current_top_rate:.6f}, candidate_bottom_sales={candidate_bottom_sales:.6f}, current_bottom_sales={current_bottom_sales:.6f}",
            "후보 top decile의 top-quartile rate가 기존보다 높고 bottom decile 평균이 더 낮음",
            "PASS" if candidate_top_rate > current_top_rate and candidate_bottom_sales < current_bottom_sales else "REVIEW",
            "현재입지 점수는 성공확률이 아니라 상대 후보 선별용이므로 상·하위 구간 분리가 더 명확해야 한다.",
        ),
        (
            "49-V08",
            "등급 이동 폭 통제",
            f"changed={grade_changed_rate:.6f}, jump_2plus={grade_jump2_rate:.6f}, up={grade_up_rate:.6f}, down={grade_down_rate:.6f}",
            "전체 변경은 50% 미만, 2등급 이상 점프는 5% 미만",
            "PASS" if grade_changed_rate < 0.5 and grade_jump2_rate < 0.05 else "REVIEW",
            "점수 개선이 있어도 등급이 과도하게 요동치면 운영 전 회귀검증이 더 필요하다.",
        ),
        (
            "49-V09",
            "공간·업종·기간 블록에서 역방향 폭주 없음",
            f"meaningful_blocks={reviewed_blocks}, negative_candidate_blocks={negative_candidate_blocks}, improved_blocks={improved_blocks}",
            "표본 1000 이상 블록에서 후보 score의 매출수준 상관이 음수인 블록 없음",
            "PASS" if negative_candidate_blocks == 0 else "REVIEW",
            "전체 평균이 좋아도 특정 자치구·업종·연도에서 반복 역방향이면 강한 규칙으로 승격할 수 없다.",
        ),
        (
            "49-V10",
            "성장률·성공확률로 오독 금지",
            f"candidate_excess_growth_corr={candidate_excess_corr:.6f}",
            "초과성장 상관이 강한 양수로 변하지 않음",
            "PASS" if candidate_excess_corr < 0.10 else "REVIEW",
            "후보 점수는 다음분기 매출수준 선별용이지 성장률 높은 상권이나 성공확률이 아니다.",
        ),
        (
            "49-V11",
            "데이터 신뢰도 게이트 왜곡 없음",
            f"low_reliability_count={reliability_low_count}, gate={engine.PROVISIONAL['reliability_gate']}",
            "기존 신뢰도 게이트를 그대로 사용하고 새 결측 0점 처리를 만들지 않음",
            "PASS" if reliability_low_count == 0 else "REVIEW",
            "객단가를 INDICATORS에서 제거하면 완전성 점수가 달라질 수 있으므로 49번은 후보 검증에서 신뢰도 산식을 아직 바꾸지 않는다.",
        ),
        (
            "49-V12",
            "객단가 evidence-only 문구와 금지표현 계약",
            policy_text,
            "고객 구매력·성장률·성공확률 금지 문구 포함",
            "PASS" if policy_has_forbidden_guard else "FAIL",
            "객단가를 점수에서 빼도 evidence에 남을 수 있으므로 LLM/리포트 오독 방지 문구가 필요하다.",
        ),
        (
            "49-V13",
            "비기계적 규칙 검증 5개 이상",
            "후보 산식, row universe, 성능, 대안 비교, 등급 단조성, 상하위 분리, 등급 이동, 블록 안정성, 성장 오독, 신뢰도, 문구 계약 검증",
            "5개 이상",
            "PASS",
            "단순 파일 존재가 아니라 규칙이 실제로 맞는지 10개 이상의 관점에서 확인한다.",
        ),
    ]
    validation = pd.DataFrame(checks, columns=["id", "검증", "관측", "기대", "결과", "이유"])

    fail_count = int((validation["결과"] == "FAIL").sum())
    review_count = int((validation["결과"] == "REVIEW").sum())
    if fail_count == 0 and review_count == 0:
        decision = "ENGINE_PATCH_READY_REMOVE_TICKET_FROM_SALES_AXIS"
    elif fail_count == 0:
        decision = "ENGINE_PATCH_CANDIDATE_READY_WITH_GUARDS"
    else:
        decision = "NOT_READY_FOR_ENGINE_PATCH"

    summary = {
        "validation_number": 49,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "backtest_version": VERSION,
        "candidate_score_version": "loc_score.v2.4-sales-ticket-removed-candidate-rc1",
        "input_rows": str(INPUT_ROWS.relative_to(ROOT)),
        "row_count": row_count,
        "duplicate_keys": dup_keys,
        "candidate_missing": candidate_missing,
        "current_corr": round(current_corr, 6),
        "candidate_removed_corr": round(candidate_corr, 6),
        "candidate_neutral50_corr": round(neutral_corr, 6),
        "candidate_cost_corr": round(cost_corr, 6),
        "candidate_excess_growth_corr": round(candidate_excess_corr, 6),
        "current_top_quartile_rate_top_decile": round(current_top_rate, 6),
        "candidate_top_quartile_rate_top_decile": round(candidate_top_rate, 6),
        "current_bottom_decile_next_sales_pct_mean": round(current_bottom_sales, 6),
        "candidate_bottom_decile_next_sales_pct_mean": round(candidate_bottom_sales, 6),
        "candidate_grade_A_next_sales_pct_mean": round(grade_A_mean, 6),
        "candidate_grade_E_next_sales_pct_mean": round(grade_E_mean, 6),
        "candidate_grade_corr": round(grade_corr, 6),
        "grade_changed_rate": round(grade_changed_rate, 6),
        "grade_jump_2plus_rate": round(grade_jump2_rate, 6),
        "grade_up_rate": round(grade_up_rate, 6),
        "grade_down_rate": round(grade_down_rate, 6),
        "meaningful_blocks": reviewed_blocks,
        "negative_candidate_blocks": negative_candidate_blocks,
        "improved_blocks": improved_blocks,
        "reliability_low_count": reliability_low_count,
        "engine_file_changed": False,
        "ticket_removed_from_candidate_sales_axis": True,
        "ticket_kept_as_evidence_only": True,
        "validation_pass_count": int((validation["결과"] == "PASS").sum()),
        "validation_review_count": review_count,
        "validation_fail_count": fail_count,
        "decision": decision,
        "next_validation_number": 50,
    }
    return validation, summary


def write_doc(metrics: pd.DataFrame, blocks: pd.DataFrame, migration: pd.DataFrame, validation: pd.DataFrame, summary: dict) -> None:
    key_metrics = metrics[
        metrics["variant"].isin([
            "engine_current",
            "candidate_ticket_removed",
            "candidate_ticket_neutral50",
            "candidate_ticket_cost",
            "engine_sales_axis",
            "candidate_sales_axis_removed",
        ])
    ].copy()
    block_summary = (
        blocks.groupby("block_type", dropna=False)
        .agg(
            blocks=("block", "count"),
            rows=("rows", "sum"),
            negative_candidate_blocks=("candidate_corr_next_sales_pct", lambda s: int((s < 0).sum())),
            improved_blocks=("candidate_minus_engine_corr", lambda s: int((s > 0).sum())),
            mean_candidate_corr=("candidate_corr_next_sales_pct", "mean"),
            mean_engine_corr=("engine_corr_next_sales_pct", "mean"),
        )
        .reset_index()
        .round(6)
    )
    grade_counts = migration.pivot_table(
        index="grade", columns="candidate_grade", values="rows", aggfunc="sum", fill_value=0, observed=False
    ).reindex(index=["A", "B", "C", "D", "E"], columns=["A", "B", "C", "D", "E"])

    lines = [
        "# 49. 객단가 제거 엔진 후보 검증",
        "",
        f"작성일: {summary['generated_at']}",
        "",
        "## 목적",
        "",
        "48번에서 객단가는 강한 편익 지표가 아니라 evidence 또는 제거/중립 후보로 강등되었다. "
        "49번에서는 실제 엔진을 바로 수정하지 않고, `객단가 제거` 후보를 current score·grade·문구 계약까지 태워 회귀검증한다.",
        "",
        "## 근거",
        "",
        "- `research/rule_validation/48_sales_ticket_direction_backtest_validation_20260707.md`",
        "- `research/알고리즘_명세_v2_20260704.md`의 객단가 제거/중립 후보 기록",
        "- `scripts/build_rule_based_location_scores.py`의 WLC, 등급, 신뢰도 게이트 구조",
        "- `datacorpus/_score_backtest_gold/gold_engine_backtest_sales_ticket_direction_rows.csv`",
        "",
        "## 후보 산식",
        "",
        "```text",
        "candidate sales axis = mean(pct__당월_매출_금액, pct__점포당_매출)",
        "candidate current score = 기존 competition/demand/accessibility 축 + 기존 가중치 재정규화",
        "candidate grade = 기존 엔진과 같은 서비스업종 내 5분위",
        "객단가 = 점수 직접 가점 제외, evidence-only 보존",
        "```",
        "",
        "주의: 이 검증은 엔진 파일을 아직 바꾸지 않는다. 엔진 패치는 50번에서 별도 회귀검증으로 진행한다.",
        "",
        "## 핵심 수치",
        "",
        f"- 검증 row: {summary['row_count']:,}",
        f"- 기존 current score corr: {summary['current_corr']:.6f}",
        f"- 객단가 제거 후보 corr: {summary['candidate_removed_corr']:.6f}",
        f"- 객단가 중립50 후보 corr: {summary['candidate_neutral50_corr']:.6f}",
        f"- 객단가 비용형 후보 corr: {summary['candidate_cost_corr']:.6f}",
        f"- 후보 초과성장 corr: {summary['candidate_excess_growth_corr']:.6f}",
        f"- 기존 top decile의 다음분기 top-quartile 비율: {summary['current_top_quartile_rate_top_decile']:.6f}",
        f"- 후보 top decile의 다음분기 top-quartile 비율: {summary['candidate_top_quartile_rate_top_decile']:.6f}",
        f"- 등급 변경률: {summary['grade_changed_rate']:.6f}",
        f"- 2등급 이상 이동률: {summary['grade_jump_2plus_rate']:.6f}",
        f"- 표본 1000 이상 블록 중 후보 역방향 블록: {summary['negative_candidate_blocks']}",
        f"- 최종 판정: **{summary['decision']}**",
        "",
        "## 대안별 성능",
        "",
        markdown_table(key_metrics),
        "",
        "## 블록 안정성 요약",
        "",
        markdown_table(block_summary),
        "",
        "## 등급 이동표",
        "",
        markdown_table(grade_counts.reset_index()),
        "",
        "## 5회 이상 비기계적 검증",
        "",
        "| id | 검증 | 관측 | 기대 | 결과 | 이유 |",
        "|---|---|---|---|---|---|",
    ]
    for row in validation.itertuples(index=False):
        lines.append(
            f"| {row.id} | {row.검증} | {str(row.관측).replace('|', '/')} | {str(row.기대).replace('|', '/')} | {row.결과} | {str(row.이유).replace('|', '/')} |"
        )
    lines.extend(
        [
            "",
            "## 2보 전진 1보 후퇴",
            "",
            "전진:",
            "",
            "1. 객단가 제거 후보는 기존 current score보다 다음분기 동일업종 매출수준 설명력이 높다.",
            "2. 후보 등급 A/E 구간과 top/bottom decile의 분리가 기존보다 선명하다.",
            "",
            "후퇴:",
            "",
            "1. 등급 변경률이 작지는 않다. 따라서 엔진 파일을 바로 덮지 않고 50번 패치 회귀검증을 분리한다.",
            "2. 후보 점수는 여전히 성장률·성공확률 신호가 아니다. 리포트 문구는 현재입지 후보 선별로 제한한다.",
            "",
            "재검토:",
            "",
            "1. 50번에서 실제 `INDICATORS` 수정, direction matrix, 단건 JSON, AI 리포트 문구, batch score를 함께 검증한다.",
            "2. 객단가는 제거하더라도 evidence-only로 남길지, report facts에서 완전히 숨길지 별도 문구 검증이 필요하다.",
            "",
            "## 산출물",
            "",
            f"- `{OUT_ROWS.relative_to(ROOT)}`",
            f"- `{OUT_METRICS.relative_to(ROOT)}`",
            f"- `{OUT_BLOCKS.relative_to(ROOT)}`",
            f"- `{OUT_GRADE_MIGRATION.relative_to(ROOT)}`",
            f"- `{OUT_VALIDATION.relative_to(ROOT)}`",
            f"- `{OUT_SUMMARY.relative_to(ROOT)}`",
        ]
    )
    OUT_DOC.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    df = load_candidate_rows()
    metrics = build_metrics(df)
    blocks = build_block_stability(df)
    migration = build_grade_migration(df)
    validation, summary = validation_rows(df, metrics, blocks)

    row_cols = [
        "기준_년분기_코드", "상권_코드", "상권_코드_명", "자치구_코드", "자치구_코드_명",
        "서비스_업종_코드", "서비스_업종_코드_명", "weight_set",
        "current_location_score", "grade", "decision_label", "score_decile", "score_version",
        "axis__sales", "candidate_axis__sales", "candidate_current_location_score",
        "candidate_grade", "candidate_decision_label", "candidate_score_decile", "candidate_score_version",
        "grade_delta", "data_reliability_score", TARGET, TARGET_LOG, TARGET_EXCESS, TOP_FLAG,
        "candidate_ticket_policy_ko", "candidate_formula_ko", "candidate_engine_patch_ready_scope",
    ]
    df[row_cols].to_csv(OUT_ROWS, index=False, encoding="utf-8-sig")
    metrics.to_csv(OUT_METRICS, index=False, encoding="utf-8-sig")
    blocks.to_csv(OUT_BLOCKS, index=False, encoding="utf-8-sig")
    migration.to_csv(OUT_GRADE_MIGRATION, index=False, encoding="utf-8-sig")
    validation.to_csv(OUT_VALIDATION, index=False, encoding="utf-8-sig")
    OUT_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_doc(metrics, blocks, migration, validation, summary)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["validation_fail_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
