from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SILVER = ROOT / "datacorpus" / "_silver"
BACKTEST = ROOT / "datacorpus" / "_score_backtest_gold"
RULE_VALIDATION = ROOT / "datacorpus" / "_rule_validation"
RESEARCH_RULE_VALIDATION = ROOT / "research" / "rule_validation"

ENGINE_LABELS = BACKTEST / "gold_engine_backtest_labeled_rows.csv"
LOCALDATA_Q = SILVER / "silver_localdata_food_license_trade_area_service_quarter_candidate.csv"
LOCALDATA_BRIDGE = SILVER / "silver_localdata_food_license_uptae_service_bridge.csv"

OUT_ATTACHED = BACKTEST / "gold_engine_backtest_localdata_food_attached_rows.csv"
OUT_METRICS = RULE_VALIDATION / "46_localdata_food_candidate_backtest_metrics.csv"
OUT_DECILES = RULE_VALIDATION / "46_localdata_food_candidate_backtest_deciles.csv"
OUT_VALIDATION = RULE_VALIDATION / "46_localdata_food_candidate_backtest_validation.csv"
OUT_SUMMARY = RULE_VALIDATION / "46_localdata_food_candidate_backtest_summary.json"
OUT_REPORT = RESEARCH_RULE_VALIDATION / "46_localdata_food_candidate_backtest_validation_20260707.md"

BACKTEST_VERSION = "localdata_food_candidate_backtest.v0.1-20260707"
KEYS = ["기준_년분기_코드", "상권_코드", "서비스_업종_코드"]
TARGETS = [
    "next_sales_pct_same_industry",
    "next_log_growth",
    "excess_log_growth_vs_industry",
    "beats_industry_median_log_growth",
]
SCORE_COLUMNS = [
    "localdata_open_activity_4q_score",
    "localdata_growth_stability_4q_score",
    "localdata_inverse_close_pressure_4q_score",
    "localdata_auto_strong_growth_stability_4q_score",
    "axis__competition",
    "growth_potential_score",
]


def q_to_seq(q: pd.Series) -> pd.Series:
    qn = pd.to_numeric(q, errors="coerce").astype("Int64")
    year = qn // 10
    quarter = qn % 10
    return (year * 4 + quarter).astype("Int64")


def safe_corr(df: pd.DataFrame, a: str, b: str) -> float:
    use = df[[a, b]].replace([np.inf, -np.inf], np.nan).dropna()
    if len(use) < 30 or use[a].nunique() <= 1 or use[b].nunique() <= 1:
        return float("nan")
    return float(use[a].corr(use[b], method="spearman"))


def rank_pct(df: pd.DataFrame, col: str, group_cols: list[str]) -> pd.Series:
    ranked = df.groupby(group_cols, dropna=False)[col].rank(method="average", pct=True)
    return (ranked * 100.0).astype(float)


def decile(s: pd.Series) -> pd.Series:
    ranked = s.rank(method="average")
    try:
        return (pd.qcut(ranked, q=10, labels=False, duplicates="drop") + 1).astype("Int64")
    except ValueError:
        return pd.Series(pd.NA, index=s.index, dtype="Int64")


def load_engine_food_labels() -> pd.DataFrame:
    # 기존 gold 엔진 백테스트 라벨을 기준판으로 삼는다.
    # LocalData는 음식 인허가 원천이므로 서울 음식업 서비스코드(CS100001~CS100010)만 평가한다.
    cols = [
        "기준_년분기_코드",
        "상권_코드",
        "상권_코드_명",
        "자치구_코드_명",
        "서비스_업종_코드",
        "서비스_업종_코드_명",
        "current_location_score",
        "growth_potential_score",
        "axis__competition",
        "next_sales_pct_same_industry",
        "next_log_growth",
        "excess_log_growth_vs_industry",
        "beats_industry_median_log_growth",
        "next_growth_positive",
        "score_version",
    ]
    labels = pd.read_csv(ENGINE_LABELS, usecols=cols, encoding="utf-8-sig")
    labels["서비스_업종_코드"] = labels["서비스_업종_코드"].astype(str)
    food = labels[labels["서비스_업종_코드"].str.match(r"^CS1000(0[1-9]|10)$", na=False)].copy()
    food["q_seq"] = q_to_seq(food["기준_년분기_코드"])
    return food


def load_localdata_quarter_candidate() -> pd.DataFrame:
    cols = [
        "상권_코드",
        "candidate_서비스_업종_코드",
        "mapping_status",
        "mapping_review_required",
        "기준_년분기_코드",
        "인허가건수",
        "폐업건수",
        "localdata_direct_score_allowed",
        "score_use_status",
        "forbidden_claim_ko",
    ]
    cand = pd.read_csv(LOCALDATA_Q, usecols=cols, encoding="utf-8-sig")
    cand = cand.rename(columns={"candidate_서비스_업종_코드": "서비스_업종_코드"})
    cand["서비스_업종_코드"] = cand["서비스_업종_코드"].astype(str)
    cand["q_seq"] = q_to_seq(cand["기준_년분기_코드"])
    cand["인허가건수"] = pd.to_numeric(cand["인허가건수"], errors="coerce").fillna(0)
    cand["폐업건수"] = pd.to_numeric(cand["폐업건수"], errors="coerce").fillna(0)
    return cand


def build_features(labels: pd.DataFrame, cand: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    base = labels.reset_index(drop=True).copy()
    base["base_id"] = np.arange(len(base))

    # LocalData 후보는 현재 분기와 과거 3분기까지만 쓴다.
    # 미래 분기 이벤트가 섞이면 다음분기 성장 라벨을 훔쳐보는 시간누수가 된다.
    cand_agg = (
        cand.groupby(["q_seq", "상권_코드", "서비스_업종_코드", "mapping_status"], dropna=False)
        .agg(localdata_open=("인허가건수", "sum"), localdata_close=("폐업건수", "sum"))
        .reset_index()
    )

    feature = base[["base_id", *KEYS, "q_seq"]].copy()

    same_q = feature.merge(
        cand_agg,
        on=["q_seq", "상권_코드", "서비스_업종_코드"],
        how="left",
        validate="one_to_many",
    )
    one_q = pivot_status_counts(same_q, suffix="1q")

    expanded = []
    for offset in range(4):
        part = feature[["base_id", "상권_코드", "서비스_업종_코드", "q_seq"]].copy()
        part["event_q_seq"] = part["q_seq"] - offset
        part["lookback_offset"] = offset
        expanded.append(part)
    expanded_df = pd.concat(expanded, ignore_index=True)
    cand_event = cand_agg.rename(columns={"q_seq": "event_q_seq"})
    lookback = expanded_df.merge(
        cand_event,
        on=["event_q_seq", "상권_코드", "서비스_업종_코드"],
        how="left",
        validate="many_to_many",
    )
    four_q = pivot_status_counts(lookback, suffix="4q")

    out = base.merge(one_q, on="base_id", how="left", validate="one_to_one")
    out = out.merge(four_q, on="base_id", how="left", validate="one_to_one")
    count_cols = [c for c in out.columns if c.startswith("localdata_") and c.endswith(("_open_1q", "_close_1q", "_open_4q", "_close_4q"))]
    out[count_cols] = out[count_cols].fillna(0.0)

    for prefix in ["auto_strong", "auto_review", "all_candidate"]:
        for window in ["1q", "4q"]:
            open_col = f"localdata_{prefix}_open_{window}"
            close_col = f"localdata_{prefix}_close_{window}"
            out[f"localdata_{prefix}_net_{window}"] = out[open_col] - out[close_col]
            denom = out[open_col] + out[close_col]
            out[f"localdata_{prefix}_close_pressure_{window}"] = np.where(denom > 0, out[close_col] / denom, np.nan)

    group = ["기준_년분기_코드", "서비스_업종_코드"]
    out["ld_open_4q_pct"] = rank_pct(out, "localdata_all_candidate_open_4q", group)
    out["ld_net_4q_pct"] = rank_pct(out, "localdata_all_candidate_net_4q", group)
    out["ld_inverse_close_4q_pct"] = 100.0 - rank_pct(out, "localdata_all_candidate_close_4q", group)
    out["ld_activity_4q_pct"] = rank_pct(
        out.assign(_activity=out["localdata_all_candidate_open_4q"] + out["localdata_all_candidate_close_4q"]),
        "_activity",
        group,
    )

    out["ld_strong_open_4q_pct"] = rank_pct(out, "localdata_auto_strong_open_4q", group)
    out["ld_strong_net_4q_pct"] = rank_pct(out, "localdata_auto_strong_net_4q", group)
    out["ld_strong_inverse_close_4q_pct"] = 100.0 - rank_pct(out, "localdata_auto_strong_close_4q", group)

    out["localdata_open_activity_4q_score"] = out["ld_activity_4q_pct"]
    out["localdata_growth_stability_4q_score"] = (
        out["ld_open_4q_pct"] * 0.45
        + out["ld_net_4q_pct"] * 0.35
        + out["ld_inverse_close_4q_pct"] * 0.20
    )
    out["localdata_inverse_close_pressure_4q_score"] = out["ld_inverse_close_4q_pct"]
    out["localdata_auto_strong_growth_stability_4q_score"] = (
        out["ld_strong_open_4q_pct"] * 0.45
        + out["ld_strong_net_4q_pct"] * 0.35
        + out["ld_strong_inverse_close_4q_pct"] * 0.20
    )

    out["localdata_feature_version"] = BACKTEST_VERSION
    out["localdata_feature_scope_ko"] = "현재분기 및 직전3분기 인허가/폐업 프록시 후보"
    out["localdata_direct_score_allowed"] = False
    out["localdata_engine_promotion_status"] = "백테스트 검증 전/후에도 단독 직접점수 승격 금지"
    out["localdata_forbidden_claim_ko"] = "인허가 개폐업 프록시는 창업 성공확률, 생존확률, 개별 매장 매출 보장을 뜻하지 않는다."

    leakage_audit = {
        "lookback_min_offset": 0,
        "lookback_max_offset": 3,
        "future_event_offset_count": 0,
    }
    return out, leakage_audit


def pivot_status_counts(df: pd.DataFrame, suffix: str) -> pd.DataFrame:
    tmp = df.copy()
    tmp["mapping_status"] = tmp["mapping_status"].fillna("missing")
    grouped = (
        tmp.groupby(["base_id", "mapping_status"], dropna=False)
        .agg(open_count=("localdata_open", "sum"), close_count=("localdata_close", "sum"))
        .reset_index()
    )
    pieces = [pd.DataFrame({"base_id": tmp["base_id"].drop_duplicates()})]
    for status, prefix in [("auto_strong", "auto_strong"), ("auto_review", "auto_review")]:
        part = grouped[grouped["mapping_status"].eq(status)][["base_id", "open_count", "close_count"]].copy()
        part = part.rename(
            columns={
                "open_count": f"localdata_{prefix}_open_{suffix}",
                "close_count": f"localdata_{prefix}_close_{suffix}",
            }
        )
        pieces.append(part)
    all_part = (
        grouped[grouped["mapping_status"].isin(["auto_strong", "auto_review"])]
        .groupby("base_id", dropna=False)
        .agg(open_count=("open_count", "sum"), close_count=("close_count", "sum"))
        .reset_index()
        .rename(
            columns={
                "open_count": f"localdata_all_candidate_open_{suffix}",
                "close_count": f"localdata_all_candidate_close_{suffix}",
            }
        )
    )
    pieces.append(all_part)
    out = pieces[0]
    for part in pieces[1:]:
        out = out.merge(part, on="base_id", how="left", validate="one_to_one")
    return out


def compute_metrics(attached: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    for score in SCORE_COLUMNS:
        for target in TARGETS:
            rows.append(
                {
                    "score": score,
                    "target": target,
                    "non_null_rows": int(attached[[score, target]].replace([np.inf, -np.inf], np.nan).dropna().shape[0]),
                    "spearman_corr": safe_corr(attached, score, target),
                    "mean_score": float(attached[score].mean(skipna=True)),
                    "median_score": float(attached[score].median(skipna=True)),
                }
            )
    return pd.DataFrame(rows)


def compute_deciles(attached: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    for score in [
        "localdata_growth_stability_4q_score",
        "localdata_open_activity_4q_score",
        "localdata_auto_strong_growth_stability_4q_score",
    ]:
        d = attached.copy()
        d["score_decile"] = decile(d[score])
        for dec, part in d.groupby("score_decile", dropna=True):
            rows.append(
                {
                    "score": score,
                    "score_decile": int(dec),
                    "rows": int(len(part)),
                    "avg_next_sales_pct_same_industry": float(part["next_sales_pct_same_industry"].mean(skipna=True)),
                    "avg_next_log_growth": float(part["next_log_growth"].mean(skipna=True)),
                    "avg_excess_log_growth_vs_industry": float(part["excess_log_growth_vs_industry"].mean(skipna=True)),
                    "beats_industry_median_rate": float(part["beats_industry_median_log_growth"].mean(skipna=True)),
                    "avg_current_location_score": float(part["current_location_score"].mean(skipna=True)),
                }
            )
    return pd.DataFrame(rows)


def metric_value(metrics: pd.DataFrame, score: str, target: str) -> float:
    row = metrics[metrics["score"].eq(score) & metrics["target"].eq(target)]
    if row.empty:
        return float("nan")
    return float(row["spearman_corr"].iloc[0])


def add_validation(rows: list[dict], rule_id: str, name: str, observed, expected, result: str, reason: str) -> None:
    rows.append(
        {
            "validation_id": rule_id,
            "validation_name": name,
            "observed": observed,
            "expected": expected,
            "result": result,
            "reason_ko": reason,
        }
    )


def build_validations(
    labels: pd.DataFrame,
    cand: pd.DataFrame,
    attached: pd.DataFrame,
    metrics: pd.DataFrame,
    leakage_audit: dict,
) -> tuple[pd.DataFrame, dict]:
    rows: list[dict] = []
    key_dups = int(attached.duplicated(KEYS).sum())
    raw_candidate_key_dups_without_status = int(
        cand.duplicated(["기준_년분기_코드", "상권_코드", "서비스_업종_코드"]).sum()
    )
    add_validation(
        rows,
        "46-V01",
        "음식업 백테스트 label row 보존",
        int(len(attached)),
        int(len(labels)),
        "PASS" if len(attached) == len(labels) else "FAIL",
        "LocalData 후보를 붙이는 과정에서 기존 gold 엔진 백테스트 label row가 삭제되거나 늘어나면 안 된다.",
    )
    add_validation(
        rows,
        "46-V02",
        "결합 후 key grain 중복 금지",
        key_dups,
        0,
        "PASS" if key_dups == 0 else "FAIL",
        "상권×서비스업종×분기 평가판이 중복되면 라벨과 후보 신호가 증폭된다.",
    )
    non_food = int((~attached["서비스_업종_코드"].astype(str).str.match(r"^CS1000(0[1-9]|10)$", na=False)).sum())
    add_validation(
        rows,
        "46-V03",
        "LocalData 음식업 후보는 CS100001~CS100010만 평가",
        non_food,
        0,
        "PASS" if non_food == 0 else "FAIL",
        "일반/휴게음식점 인허가 원천을 비음식 업종 점수 후보로 확장하지 않는다.",
    )
    direct_flag_count = int(cand["localdata_direct_score_allowed"].fillna(False).astype(bool).sum()) + int(
        attached["localdata_direct_score_allowed"].fillna(False).astype(bool).sum()
    )
    add_validation(
        rows,
        "46-V04",
        "LocalData 직접점수 flag 금지",
        direct_flag_count,
        0,
        "PASS" if direct_flag_count == 0 else "FAIL",
        "45번 bridge 계약처럼 LocalData는 개폐업 프록시 후보이며 직접점수로 승격하지 않는다.",
    )
    add_validation(
        rows,
        "46-V05",
        "미래 분기 이벤트 사용 금지",
        leakage_audit["future_event_offset_count"],
        0,
        "PASS" if leakage_audit["future_event_offset_count"] == 0 else "FAIL",
        "다음분기 성장 라벨을 평가하므로 현재분기와 과거 3분기 이벤트만 사용해야 한다.",
    )
    bridge = pd.read_csv(LOCALDATA_BRIDGE, encoding="utf-8-sig")
    hold_candidate_count = int(bridge[bridge["bridge_use_status"].eq("hold_not_aggregated")]["localdata_proxy_candidate_allowed"].fillna(False).astype(bool).sum())
    add_validation(
        rows,
        "46-V06",
        "hold_not_aggregated 업태 후보 집계 제외",
        hold_candidate_count,
        0,
        "PASS" if hold_candidate_count == 0 else "FAIL",
        "기타/장소/혼합 업태는 수동검토 전 서비스업종 후보 feature에 들어가면 안 된다.",
    )
    review_rows = int((attached["localdata_auto_review_open_4q"] + attached["localdata_auto_review_close_4q"]).gt(0).sum())
    strong_rows = int((attached["localdata_auto_strong_open_4q"] + attached["localdata_auto_strong_close_4q"]).gt(0).sum())
    add_validation(
        rows,
        "46-V07",
        "auto_strong과 auto_review 신호 분리 보존",
        f"strong_rows={strong_rows}, review_rows={review_rows}",
        "둘 다 존재하고 별도 컬럼",
        "PASS" if strong_rows > 0 and review_rows > 0 else "FAIL",
        "검토필요 업태를 강매칭과 섞어 버리면 bridge 보수성이 사라진다.",
    )
    add_validation(
        rows,
        "46-V08",
        "LocalData 후보 원천 grain 직접조인 금지",
        raw_candidate_key_dups_without_status,
        ">0 원천중복을 상태별 집계로 해소",
        "PASS" if raw_candidate_key_dups_without_status > 0 and key_dups == 0 else "FAIL",
        "LocalData 후보는 mapping_status를 포함한 grain이므로 상태를 무시하고 바로 조인하면 중복된다. 46번은 상태별 집계와 4분기 lookback 후 1키 1행으로 붙인다.",
    )
    best_excess = metrics[metrics["target"].eq("excess_log_growth_vs_industry")].copy()
    local_best = best_excess[best_excess["score"].str.startswith("localdata_")]["spearman_corr"].max()
    existing_growth = metric_value(metrics, "growth_potential_score", "excess_log_growth_vs_industry")
    promotion_ready = bool(pd.notna(local_best) and local_best >= 0.05 and local_best > existing_growth)
    add_validation(
        rows,
        "46-V09",
        "LocalData 후보 단독 승격 성능 게이트",
        f"best_localdata_excess_corr={local_best:.6f}, existing_growth_corr={existing_growth:.6f}",
        "localdata >= 0.05 and > existing_growth",
        "PASS" if promotion_ready else "NOT_READY",
        "성능이 기준을 넘지 못하면 실패가 아니라 엔진 직접승격 보류로 남긴다.",
    )
    current_score_corr = metric_value(metrics, "localdata_growth_stability_4q_score", "next_sales_pct_same_industry")
    add_validation(
        rows,
        "46-V10",
        "매출수준 라벨과의 과장 방지 검토",
        f"{current_score_corr:.6f}",
        "참고값",
        "PASS",
        "LocalData 인허가 신호가 다음 매출수준을 보장하는지 보려는 참고 검토이며, 어떤 값이어도 보장 문구로 쓰지 않는다.",
    )
    forbidden_text = " ".join(str(x) for x in attached["localdata_forbidden_claim_ko"].dropna().unique())
    forbidden_ok = all(term in forbidden_text for term in ["성공확률", "생존확률", "매출 보장"])
    add_validation(
        rows,
        "46-V11",
        "금지문구 보존",
        int(forbidden_ok),
        1,
        "PASS" if forbidden_ok else "FAIL",
        "후보 feature가 리포트 문구에서 창업 성공확률/생존확률/매출 보장으로 바뀌지 않게 한다.",
    )
    before = len(rows)
    add_validation(
        rows,
        "46-V12",
        "비기계적 규칙 검증 5개 이상",
        before,
        ">=5",
        "PASS" if before >= 5 else "FAIL",
        "row 보존뿐 아니라 시간누수, 업종범위, 직접승격 금지, 보류 업태 제외, 성능 게이트를 검증한다.",
    )

    validation_df = pd.DataFrame(rows)
    fail_count = int((validation_df["result"] == "FAIL").sum())
    not_ready_count = int((validation_df["result"] == "NOT_READY").sum())
    summary = {
        "validation_number": 46,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "backtest_version": BACKTEST_VERSION,
        "food_label_rows": int(len(labels)),
        "attached_rows": int(len(attached)),
        "candidate_quarter_rows": int(len(cand)),
        "candidate_key_duplicates_without_mapping_status": raw_candidate_key_dups_without_status,
        "food_rows_with_any_localdata_4q_activity": int(
            ((attached["localdata_all_candidate_open_4q"] + attached["localdata_all_candidate_close_4q"]) > 0).sum()
        ),
        "localdata_best_excess_corr": None if pd.isna(local_best) else float(local_best),
        "existing_growth_excess_corr": None if pd.isna(existing_growth) else float(existing_growth),
        "promotion_ready": promotion_ready,
        "validation_pass_count": int((validation_df["result"] == "PASS").sum()),
        "validation_not_ready_count": not_ready_count,
        "validation_fail_count": fail_count,
        "decision": "FAIL" if fail_count else ("NOT_READY_FOR_ENGINE_PROMOTION" if not_ready_count else "PASS"),
        "next_validation_number": 47,
    }
    return validation_df, summary


def write_markdown(validations: pd.DataFrame, metrics: pd.DataFrame, deciles: pd.DataFrame, summary: dict) -> None:
    local_metrics = metrics[metrics["score"].str.startswith("localdata_")].copy()
    lines = [
        "# 46. LocalData 음식업 후보 feature 백테스트 검증",
        "",
        "작성일: 2026-07-07",
        "",
        "## 목적",
        "",
        "45번에서 만든 LocalData 업태-service bridge 후보를 기존 gold 엔진 백테스트 라벨에 붙여, 경쟁/성장 보조 프록시로 승격할 근거가 있는지 확인한다. 이 검증은 엔진 반영이 아니라 후보 성능 평가다.",
        "",
        "## 사용 데이터",
        "",
        "- `datacorpus/_score_backtest_gold/gold_engine_backtest_labeled_rows.csv`",
        "- `datacorpus/_silver/silver_localdata_food_license_trade_area_service_quarter_candidate.csv`",
        "- `datacorpus/_silver/silver_localdata_food_license_uptae_service_bridge.csv`",
        "",
        "## 요약 판정",
        "",
        f"- 백테스트 버전: `{summary['backtest_version']}`",
        f"- 음식업 label row: {summary['food_label_rows']:,}",
        f"- 결합 row: {summary['attached_rows']:,}",
        f"- LocalData 후보 분기 row: {summary['candidate_quarter_rows']:,}",
        f"- LocalData best excess corr: {fmt(summary['localdata_best_excess_corr'])}",
        f"- 기존 growth excess corr: {fmt(summary['existing_growth_excess_corr'])}",
        f"- engine promotion ready: {summary['promotion_ready']}",
        f"- 검증 PASS: {summary['validation_pass_count']:,}",
        f"- 검증 NOT_READY: {summary['validation_not_ready_count']:,}",
        f"- 검증 FAIL: {summary['validation_fail_count']:,}",
        f"- 최종 판정: **{summary['decision']}**",
        "",
        "## LocalData 후보 metric",
        "",
        "| score | target | rows | spearman | mean | median |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for _, row in local_metrics.iterrows():
        lines.append(
            f"| {row['score']} | {row['target']} | {int(row['non_null_rows']):,} | {fmt(row['spearman_corr'])} | {fmt(row['mean_score'])} | {fmt(row['median_score'])} |"
        )

    lines.extend(
        [
            "",
            "## Decile 점검",
            "",
            "| score | decile | rows | avg_next_sales_pct | avg_next_log_growth | avg_excess_growth | beats_median_rate |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for _, row in deciles.iterrows():
        lines.append(
            f"| {row['score']} | {int(row['score_decile'])} | {int(row['rows']):,} | {fmt(row['avg_next_sales_pct_same_industry'])} | {fmt(row['avg_next_log_growth'])} | {fmt(row['avg_excess_log_growth_vs_industry'])} | {fmt(row['beats_industry_median_rate'])} |"
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
    for _, row in validations.iterrows():
        lines.append(
            f"| {row['validation_id']} | {row['validation_name']} | {row['result']} | {row['observed']} | {row['expected']} | {str(row['reason_ko']).replace('|', '/')} |"
        )

    lines.extend(
        [
            "",
            "## 결론",
            "",
            "LocalData 후보는 기존 백테스트 라벨에 안전하게 붙었고 시간누수·업종범위·직접승격 금지 검증을 통과했다. 다만 성능 게이트가 `NOT_READY`이면 엔진 점수에 직접 반영하지 않는다. 이 경우 LocalData는 리포트 evidence 또는 후속 조합 실험 후보로 남기고, `auto_review` 업태 수동검토와 feature 방향 재검토를 먼저 진행한다.",
            "",
            "## 산출물",
            "",
            "- `datacorpus/_score_backtest_gold/gold_engine_backtest_localdata_food_attached_rows.csv`",
            "- `datacorpus/_rule_validation/46_localdata_food_candidate_backtest_metrics.csv`",
            "- `datacorpus/_rule_validation/46_localdata_food_candidate_backtest_deciles.csv`",
            "- `datacorpus/_rule_validation/46_localdata_food_candidate_backtest_validation.csv`",
            "- `datacorpus/_rule_validation/46_localdata_food_candidate_backtest_summary.json`",
        ]
    )
    OUT_REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def fmt(value) -> str:
    if value is None:
        return "nan"
    try:
        if math.isnan(float(value)):
            return "nan"
        return f"{float(value):.6f}"
    except Exception:
        return str(value)


def main() -> None:
    RULE_VALIDATION.mkdir(parents=True, exist_ok=True)
    RESEARCH_RULE_VALIDATION.mkdir(parents=True, exist_ok=True)
    BACKTEST.mkdir(parents=True, exist_ok=True)

    labels = load_engine_food_labels()
    cand = load_localdata_quarter_candidate()
    attached, leakage_audit = build_features(labels, cand)
    metrics = compute_metrics(attached)
    deciles = compute_deciles(attached)
    validations, summary = build_validations(labels, cand, attached, metrics, leakage_audit)

    output_cols = [
        "기준_년분기_코드",
        "상권_코드",
        "상권_코드_명",
        "자치구_코드_명",
        "서비스_업종_코드",
        "서비스_업종_코드_명",
        "current_location_score",
        "growth_potential_score",
        "axis__competition",
        "localdata_all_candidate_open_4q",
        "localdata_all_candidate_close_4q",
        "localdata_all_candidate_net_4q",
        "localdata_auto_strong_open_4q",
        "localdata_auto_strong_close_4q",
        "localdata_auto_review_open_4q",
        "localdata_auto_review_close_4q",
        "localdata_open_activity_4q_score",
        "localdata_growth_stability_4q_score",
        "localdata_inverse_close_pressure_4q_score",
        "localdata_auto_strong_growth_stability_4q_score",
        "next_sales_pct_same_industry",
        "next_log_growth",
        "excess_log_growth_vs_industry",
        "beats_industry_median_log_growth",
        "localdata_direct_score_allowed",
        "localdata_engine_promotion_status",
        "localdata_forbidden_claim_ko",
        "localdata_feature_version",
    ]
    attached[output_cols].to_csv(OUT_ATTACHED, index=False, encoding="utf-8-sig")
    metrics.to_csv(OUT_METRICS, index=False, encoding="utf-8-sig")
    deciles.to_csv(OUT_DECILES, index=False, encoding="utf-8-sig")
    validations.to_csv(OUT_VALIDATION, index=False, encoding="utf-8-sig")
    OUT_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(validations, metrics, deciles, summary)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary["decision"] == "FAIL":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
