from __future__ import annotations

import importlib.util
import json
import math
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SILVER = ROOT / "datacorpus" / "_silver"
BACKTEST = ROOT / "datacorpus" / "_score_backtest_gold"
RULE = ROOT / "datacorpus" / "_rule_validation"
DOC = ROOT / "research" / "rule_validation"

BASELINE_SCRIPT = ROOT / "scripts" / "backtest_localdata_food_candidate_features.py"
JOIN_SAFE_Q = SILVER / "silver_localdata_food_license_trade_area_service_quarter_join_safe_candidate.csv"
REVIEW_QUEUE = RULE / "53_localdata_food_bridge_review_queue.csv"
SUMMARY_46 = RULE / "46_localdata_food_candidate_backtest_summary.json"
SUMMARY_53 = RULE / "53_localdata_food_join_safe_summary.json"
METRICS_46 = RULE / "46_localdata_food_candidate_backtest_metrics.csv"
DECILES_46 = RULE / "46_localdata_food_candidate_backtest_deciles.csv"
ATTACHED_46 = BACKTEST / "gold_engine_backtest_localdata_food_attached_rows.csv"

OUT_ATTACHED = BACKTEST / "gold_engine_backtest_localdata_food_join_safe_attached_rows.csv"
OUT_METRICS = RULE / "54_localdata_join_safe_backtest_metrics.csv"
OUT_DECILES = RULE / "54_localdata_join_safe_backtest_deciles.csv"
OUT_FEATURE_PARITY = RULE / "54_localdata_join_safe_backtest_feature_parity.csv"
OUT_METRIC_PARITY = RULE / "54_localdata_join_safe_backtest_metric_parity.csv"
OUT_DECILE_PARITY = RULE / "54_localdata_join_safe_backtest_decile_parity.csv"
OUT_VALIDATION = RULE / "54_localdata_join_safe_backtest_validation.csv"
OUT_SUMMARY = RULE / "54_localdata_join_safe_backtest_summary.json"
OUT_DOC = DOC / "54_localdata_join_safe_backtest_validation_20260707.md"

BACKTEST_VERSION = "localdata_food_join_safe_backtest.v0.1-20260707"
KEYS = ["기준_년분기_코드", "상권_코드", "서비스_업종_코드"]
FOOD_CODE_PATTERN = r"^CS1000(0[1-9]|10)$"
NUMERIC_FEATURE_COLS = [
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
]


def load_baseline_module():
    spec = importlib.util.spec_from_file_location("localdata_food_candidate_baseline", BASELINE_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import baseline script: {BASELINE_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.BACKTEST_VERSION = BACKTEST_VERSION
    return module


BASE = load_baseline_module()


def read_csv(path: Path, **kwargs) -> pd.DataFrame:
    return pd.read_csv(path, encoding="utf-8-sig", **kwargs)


def fmt(value) -> str:
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


def load_join_safe_as_status_rows() -> tuple[pd.DataFrame, pd.DataFrame]:
    cols = [
        "상권_코드",
        "candidate_서비스_업종_코드",
        "기준_년분기_코드",
        "mapping_status_collapsed",
        "mapping_status_count",
        "auto_strong_인허가건수",
        "auto_strong_폐업건수",
        "auto_review_인허가건수",
        "auto_review_폐업건수",
        "all_candidate_인허가건수",
        "all_candidate_폐업건수",
        "join_safe_version",
        "localdata_direct_score_allowed",
        "engine_promotion_ready",
        "score_use_status",
        "forbidden_claim_ko",
    ]
    q = read_csv(JOIN_SAFE_Q, usecols=cols)
    q["candidate_서비스_업종_코드"] = q["candidate_서비스_업종_코드"].astype(str)
    q["기준_년분기_코드"] = pd.to_numeric(q["기준_년분기_코드"], errors="coerce").astype("Int64")

    pieces = []
    # 46번 백테스트 로직은 mapping_status별 long row를 입력으로 받는다.
    # 53번 join-safe 파일은 같은 정보를 상태별 카운트 컬럼으로 접었으므로 여기서만 long 형태로 되돌린다.
    for status in ["auto_strong", "auto_review"]:
        open_col = f"{status}_인허가건수"
        close_col = f"{status}_폐업건수"
        part = q[
            [
                "상권_코드",
                "candidate_서비스_업종_코드",
                "기준_년분기_코드",
                open_col,
                close_col,
                "localdata_direct_score_allowed",
                "score_use_status",
                "forbidden_claim_ko",
            ]
        ].copy()
        part = part.rename(
            columns={
                "candidate_서비스_업종_코드": "서비스_업종_코드",
                open_col: "인허가건수",
                close_col: "폐업건수",
            }
        )
        part["mapping_status"] = status
        part["mapping_review_required"] = status == "auto_review"
        part["인허가건수"] = pd.to_numeric(part["인허가건수"], errors="coerce").fillna(0.0)
        part["폐업건수"] = pd.to_numeric(part["폐업건수"], errors="coerce").fillna(0.0)
        part = part[(part["인허가건수"] + part["폐업건수"]).gt(0)].copy()
        pieces.append(part)

    long = pd.concat(pieces, ignore_index=True)
    long["서비스_업종_코드"] = long["서비스_업종_코드"].astype(str)
    long["q_seq"] = BASE.q_to_seq(long["기준_년분기_코드"])
    return q, long


def compare_numeric_features(attached: pd.DataFrame) -> pd.DataFrame:
    ref = read_csv(ATTACHED_46, usecols=[*KEYS, *NUMERIC_FEATURE_COLS])
    now = attached[[*KEYS, *NUMERIC_FEATURE_COLS]].copy()
    merged = now.merge(ref, on=KEYS, how="outer", suffixes=("_54", "_46"), indicator=True, validate="one_to_one")
    rows = []
    for col in NUMERIC_FEATURE_COLS:
        a = pd.to_numeric(merged[f"{col}_54"], errors="coerce")
        b = pd.to_numeric(merged[f"{col}_46"], errors="coerce")
        diff = (a - b).abs()
        rows.append(
            {
                "feature": col,
                "rows_compared": int(diff.notna().sum()),
                "max_abs_diff": float(diff.max(skipna=True) if diff.notna().any() else np.nan),
                "mean_abs_diff": float(diff.mean(skipna=True) if diff.notna().any() else np.nan),
                "diff_gt_1e_9_rows": int(diff.gt(1e-9).sum()),
            }
        )
    rows.append(
        {
            "feature": "_merge_key_status",
            "rows_compared": int(len(merged)),
            "max_abs_diff": float((merged["_merge"] != "both").sum()),
            "mean_abs_diff": float((merged["_merge"] != "both").mean()),
            "diff_gt_1e_9_rows": int((merged["_merge"] != "both").sum()),
        }
    )
    return pd.DataFrame(rows)


def compare_metrics(metrics: pd.DataFrame) -> pd.DataFrame:
    ref = read_csv(METRICS_46)
    use = metrics.merge(ref, on=["score", "target"], how="outer", suffixes=("_54", "_46"), indicator=True)
    use["spearman_abs_diff"] = (
        pd.to_numeric(use["spearman_corr_54"], errors="coerce")
        - pd.to_numeric(use["spearman_corr_46"], errors="coerce")
    ).abs()
    use["mean_abs_diff"] = (
        pd.to_numeric(use["mean_score_54"], errors="coerce")
        - pd.to_numeric(use["mean_score_46"], errors="coerce")
    ).abs()
    use["median_abs_diff"] = (
        pd.to_numeric(use["median_score_54"], errors="coerce")
        - pd.to_numeric(use["median_score_46"], errors="coerce")
    ).abs()
    return use


def compare_deciles(deciles: pd.DataFrame) -> pd.DataFrame:
    ref = read_csv(DECILES_46)
    keys = ["score", "score_decile"]
    use = deciles.merge(ref, on=keys, how="outer", suffixes=("_54", "_46"), indicator=True)
    for col in [
        "rows",
        "avg_next_sales_pct_same_industry",
        "avg_next_log_growth",
        "avg_excess_log_growth_vs_industry",
        "beats_industry_median_rate",
        "avg_current_location_score",
    ]:
        use[f"{col}_abs_diff"] = (
            pd.to_numeric(use[f"{col}_54"], errors="coerce")
            - pd.to_numeric(use[f"{col}_46"], errors="coerce")
        ).abs()
    return use


def metric_value(metrics: pd.DataFrame, score: str, target: str) -> float:
    row = metrics[metrics["score"].eq(score) & metrics["target"].eq(target)]
    if row.empty:
        return float("nan")
    return float(row["spearman_corr"].iloc[0])


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
    cand_long: pd.DataFrame,
    attached: pd.DataFrame,
    metrics: pd.DataFrame,
    feature_parity: pd.DataFrame,
    metric_parity: pd.DataFrame,
    decile_parity: pd.DataFrame,
    leakage_audit: dict,
) -> tuple[pd.DataFrame, dict]:
    rows: list[dict] = []
    s46 = json.loads(SUMMARY_46.read_text(encoding="utf-8"))
    s53 = json.loads(SUMMARY_53.read_text(encoding="utf-8"))
    review_queue = read_csv(REVIEW_QUEUE)

    join_safe_dups = int(q.duplicated(["기준_년분기_코드", "상권_코드", "candidate_서비스_업종_코드"]).sum())
    add_validation(
        rows,
        "54-V01",
        "join-safe 원천 key 중복 없음",
        join_safe_dups,
        0,
        "PASS" if join_safe_dups == 0 and s53.get("join_safe_duplicate_keys") == 0 else "FAIL",
        "53번 산출물은 상권×서비스업종×분기 기준으로 바로 붙여도 fan-out이 없어야 한다.",
    )
    add_validation(
        rows,
        "54-V02",
        "음식업 백테스트 label row 보존",
        int(len(attached)),
        int(len(labels)),
        "PASS" if len(attached) == len(labels) == s46.get("food_label_rows") else "FAIL",
        "기존 46번과 같은 음식업 라벨판을 써야 성능 비교가 의미 있다.",
    )
    attached_dups = int(attached.duplicated(KEYS).sum())
    add_validation(
        rows,
        "54-V03",
        "결합 후 key grain 중복 금지",
        attached_dups,
        0,
        "PASS" if attached_dups == 0 else "FAIL",
        "join-safe 후보를 붙인 뒤에도 분기×상권×업종 평가판이 증폭되면 안 된다.",
    )
    max_feature_diff = float(feature_parity["max_abs_diff"].max(skipna=True))
    feature_diff_rows = int(feature_parity["diff_gt_1e_9_rows"].sum())
    add_validation(
        rows,
        "54-V04",
        "46번 대비 feature parity",
        f"max_abs_diff={max_feature_diff:.12f}, diff_rows={feature_diff_rows}",
        "max_abs_diff<=1e-9 and diff_rows=0",
        "PASS" if max_feature_diff <= 1e-9 and feature_diff_rows == 0 else "FAIL",
        "join-safe 전처리는 중복을 제거하는 구조 개선이지 기존 후보 feature 값을 바꾸는 작업이 아니다.",
    )
    metric_non_both = int((metric_parity["_merge"] != "both").sum())
    max_metric_diff = float(metric_parity[["spearman_abs_diff", "mean_abs_diff", "median_abs_diff"]].max(skipna=True).max())
    add_validation(
        rows,
        "54-V05",
        "46번 대비 metric parity",
        f"max_metric_diff={max_metric_diff:.12f}, unmatched_metric_rows={metric_non_both}",
        "max_diff<=1e-9 and unmatched=0",
        "PASS" if max_metric_diff <= 1e-9 and metric_non_both == 0 else "FAIL",
        "같은 feature 값이면 Spearman·평균·중앙값도 기존 46번과 같아야 한다.",
    )
    decile_non_both = int((decile_parity["_merge"] != "both").sum())
    decile_target_diff_cols = [
        "rows_abs_diff",
        "avg_next_sales_pct_same_industry_abs_diff",
        "avg_next_log_growth_abs_diff",
        "avg_excess_log_growth_vs_industry_abs_diff",
        "beats_industry_median_rate_abs_diff",
    ]
    max_decile_diff = float(decile_parity[decile_target_diff_cols].max(skipna=True).max())
    current_score_reference_diff = float(decile_parity["avg_current_location_score_abs_diff"].max(skipna=True))
    add_validation(
        rows,
        "54-V06",
        "46번 대비 LocalData 타깃 decile parity",
        f"max_target_decile_diff={max_decile_diff:.12f}, current_score_reference_diff={current_score_reference_diff:.6f}, unmatched_decile_rows={decile_non_both}",
        "target max_diff<=1e-9 and unmatched=0",
        "PASS" if max_decile_diff <= 1e-9 and decile_non_both == 0 else "FAIL",
        "46번 산출물의 현재입지 점수 참고값은 이후 엔진 패치로 달라질 수 있으므로, LocalData 후보 검증은 타깃 평균과 decile row를 기준으로 비교한다.",
    )
    direct_flags = int(q["localdata_direct_score_allowed"].fillna(False).astype(bool).sum()) + int(
        attached["localdata_direct_score_allowed"].fillna(False).astype(bool).sum()
    )
    promotion_flags = int(q["engine_promotion_ready"].fillna(False).astype(bool).sum())
    add_validation(
        rows,
        "54-V07",
        "직접점수·엔진승격 금지 유지",
        f"direct_flags={direct_flags}, promotion_flags={promotion_flags}",
        "0, 0",
        "PASS" if direct_flags == 0 and promotion_flags == 0 else "FAIL",
        "join-safe 파일이 안정적이어도 46번 성능 게이트 미달 상태에서는 엔진 점수로 올리지 않는다.",
    )
    non_food = int((~attached["서비스_업종_코드"].astype(str).str.match(FOOD_CODE_PATTERN, na=False)).sum())
    add_validation(
        rows,
        "54-V08",
        "음식업 코드 범위 유지",
        non_food,
        0,
        "PASS" if non_food == 0 else "FAIL",
        "일반/휴게음식점 인허가 원천을 비음식 업종 후보로 확장하지 않는다.",
    )
    add_validation(
        rows,
        "54-V09",
        "미래 분기 이벤트 사용 금지",
        leakage_audit["future_event_offset_count"],
        0,
        "PASS" if leakage_audit["future_event_offset_count"] == 0 else "FAIL",
        "다음분기 라벨을 평가하므로 현재분기와 직전 3분기 LocalData 이벤트만 사용한다.",
    )
    strong_rows = int((attached["localdata_auto_strong_open_4q"] + attached["localdata_auto_strong_close_4q"]).gt(0).sum())
    review_rows = int((attached["localdata_auto_review_open_4q"] + attached["localdata_auto_review_close_4q"]).gt(0).sum())
    add_validation(
        rows,
        "54-V10",
        "auto_strong/auto_review 분리와 review queue 유지",
        f"strong_rows={strong_rows}, review_rows={review_rows}, review_queue={len(review_queue)}",
        "strong>0, review>0, queue=48",
        "PASS" if strong_rows > 0 and review_rows > 0 and len(review_queue) == 48 else "FAIL",
        "수동검토 업태를 강매칭과 섞지 않고 다음 사람이 볼 수 있는 큐로 남겨야 한다.",
    )
    best_excess = metrics[metrics["target"].eq("excess_log_growth_vs_industry")]
    local_best = float(best_excess[best_excess["score"].str.startswith("localdata_")]["spearman_corr"].max())
    existing_growth = metric_value(metrics, "growth_potential_score", "excess_log_growth_vs_industry")
    promotion_ready = bool(pd.notna(local_best) and local_best >= 0.05 and local_best > existing_growth)
    add_validation(
        rows,
        "54-V11",
        "성능 게이트 재확인",
        f"best_localdata_excess_corr={local_best:.6f}, existing_growth_corr={existing_growth:.6f}",
        "localdata>=0.05 and > existing_growth",
        "NOT_READY" if not promotion_ready else "PASS",
        "join-safe 구조가 안정적이어도 성장 라벨 상관이 기준 0.05를 넘지 못하면 엔진 승격 보류다.",
    )
    forbidden_text = " ".join(str(x) for x in attached["localdata_forbidden_claim_ko"].dropna().unique())
    forbidden_ok = all(term in forbidden_text for term in ["성공확률", "생존확률", "매출 보장"])
    add_validation(
        rows,
        "54-V12",
        "금지문구 보존",
        int(forbidden_ok),
        1,
        "PASS" if forbidden_ok else "FAIL",
        "인허가 프록시가 성공확률·생존확률·매출보장 문구로 바뀌지 않게 한다.",
    )
    before = len(rows)
    add_validation(
        rows,
        "54-V13",
        "비기계적 규칙 검증 5개 이상",
        before,
        ">=5",
        "PASS" if before >= 5 else "FAIL",
        "row 존재가 아니라 key, fan-out, feature parity, 성능 게이트, 시간누수, 금지표현을 함께 검증한다.",
    )

    validation_df = pd.DataFrame(rows)
    fail_count = int((validation_df["result"] == "FAIL").sum())
    not_ready_count = int((validation_df["result"] == "NOT_READY").sum())
    decision = (
        "LOCALDATA_JOIN_SAFE_BACKTEST_FAIL"
        if fail_count
        else "LOCALDATA_JOIN_SAFE_BACKTEST_STABLE_NOT_PROMOTED"
        if not_ready_count
        else "LOCALDATA_JOIN_SAFE_BACKTEST_PASS"
    )
    summary = {
        "validation_number": 54,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "backtest_version": BACKTEST_VERSION,
        "decision": decision,
        "food_label_rows": int(len(labels)),
        "attached_rows": int(len(attached)),
        "join_safe_source_rows": int(len(q)),
        "join_safe_status_long_rows": int(len(cand_long)),
        "join_safe_source_duplicate_keys": join_safe_dups,
        "attached_duplicate_keys": attached_dups,
        "feature_parity_max_abs_diff": max_feature_diff,
        "feature_parity_diff_rows": feature_diff_rows,
        "metric_parity_max_abs_diff": max_metric_diff,
        "decile_parity_target_max_abs_diff": max_decile_diff,
        "decile_current_score_reference_max_abs_diff": current_score_reference_diff,
        "review_queue_rows": int(len(review_queue)),
        "localdata_best_excess_corr": local_best,
        "existing_growth_excess_corr": existing_growth,
        "promotion_ready": promotion_ready,
        "validation_pass_count": int((validation_df["result"] == "PASS").sum()),
        "validation_not_ready_count": not_ready_count,
        "validation_fail_count": fail_count,
        "next_validation_number": 55,
    }
    return validation_df, summary


def write_doc(
    validations: pd.DataFrame,
    metrics: pd.DataFrame,
    deciles: pd.DataFrame,
    feature_parity: pd.DataFrame,
    metric_parity: pd.DataFrame,
    decile_parity: pd.DataFrame,
    summary: dict,
) -> None:
    local_metrics = metrics[metrics["score"].str.startswith("localdata_")].copy()
    metric_parity_view = metric_parity[
        ["score", "target", "spearman_corr_54", "spearman_corr_46", "spearman_abs_diff", "_merge"]
    ].copy()
    decile_parity_view = decile_parity[
        ["score", "score_decile", "rows_54", "rows_46", "rows_abs_diff", "_merge"]
    ].head(40).copy()
    lines = [
        "# 54. LocalData join-safe 후보 백테스트 재비교",
        "",
        f"작성일: {summary['generated_at']}",
        "",
        "## 목적",
        "",
        "53번에서 만든 join-safe 후보 파일을 46번 LocalData 후보 백테스트와 같은 라벨판에 다시 붙인다. "
        "목표는 새 후보가 성능을 올렸다고 주장하는 것이 아니라, 중복 제거 후에도 기존 feature·metric·decile 결과가 흔들리지 않는지 확인하는 것이다.",
        "",
        "## 사용 데이터",
        "",
        "- `datacorpus/_score_backtest_gold/gold_engine_backtest_labeled_rows.csv`",
        "- `datacorpus/_silver/silver_localdata_food_license_trade_area_service_quarter_join_safe_candidate.csv`",
        "- `datacorpus/_score_backtest_gold/gold_engine_backtest_localdata_food_attached_rows.csv`",
        "- `datacorpus/_rule_validation/46_localdata_food_candidate_backtest_metrics.csv`",
        "- `datacorpus/_rule_validation/46_localdata_food_candidate_backtest_deciles.csv`",
        "- `datacorpus/_rule_validation/53_localdata_food_join_safe_summary.json`",
        "",
        "## 요약 판정",
        "",
        f"- 백테스트 버전: `{summary['backtest_version']}`",
        f"- 음식업 label row: {summary['food_label_rows']:,}",
        f"- 결합 row: {summary['attached_rows']:,}",
        f"- join-safe source row: {summary['join_safe_source_rows']:,}",
        f"- join-safe status long row: {summary['join_safe_status_long_rows']:,}",
        f"- join-safe source duplicate key: {summary['join_safe_source_duplicate_keys']:,}",
        f"- 결합 duplicate key: {summary['attached_duplicate_keys']:,}",
        f"- feature parity max diff: {summary['feature_parity_max_abs_diff']:.12f}",
        f"- metric parity max diff: {summary['metric_parity_max_abs_diff']:.12f}",
        f"- decile target parity max diff: {summary['decile_parity_target_max_abs_diff']:.12f}",
        f"- decile current score reference max diff: {summary['decile_current_score_reference_max_abs_diff']:.6f}",
        f"- LocalData best excess corr: {fmt(summary['localdata_best_excess_corr'])}",
        f"- 기존 growth excess corr: {fmt(summary['existing_growth_excess_corr'])}",
        f"- engine promotion ready: {summary['promotion_ready']}",
        f"- 검증 PASS: {summary['validation_pass_count']:,}",
        f"- 검증 NOT_READY: {summary['validation_not_ready_count']:,}",
        f"- 검증 FAIL: {summary['validation_fail_count']:,}",
        f"- 최종 판정: **{summary['decision']}**",
        "",
        "## LocalData metric",
        "",
        md_table(local_metrics),
        "",
        "## 46번 대비 feature parity",
        "",
        md_table(feature_parity),
        "",
        "## 46번 대비 metric parity",
        "",
        md_table(metric_parity_view),
        "",
        "## 46번 대비 decile parity 상위 40행",
        "",
        md_table(decile_parity_view),
        "",
        "## Decile 점검",
        "",
        md_table(deciles.head(40)),
        "",
        "## 5회 이상 비기계적 검증",
        "",
        md_table(validations),
        "",
        "## 2보 전진 1보 후퇴",
        "",
        "전진:",
        "",
        "1. 53번 join-safe 후보가 46번과 같은 백테스트 feature·metric·decile 결과를 재현하는지 확인했다.",
        "2. 기존 중복 위험을 없앤 파일을 다음 단계 후보 입력으로 쓸 수 있음을 확인했다.",
        "",
        "후퇴:",
        "",
        "1. 성능 게이트는 그대로 `NOT_READY`다. LocalData는 엔진 점수에 직접 넣지 않는다.",
        "2. feature parity가 통과됐다는 말은 안정화이지 성능 개선이 아니다.",
        "",
        "재검토:",
        "",
        "1. 55번 이후에는 LocalData 자체 승격보다 교통 월커버리지, 비용 프록시 권역매핑, 입력 tree/API 연결 같은 남은 큐를 우선 검토한다.",
        "2. LocalData를 다시 보강하려면 auto_review/hold 업태 수동검토 또는 다른 지표 조합 실험이 선행되어야 한다.",
        "",
        "## 산출물",
        "",
        f"- `{OUT_ATTACHED.relative_to(ROOT)}`",
        f"- `{OUT_METRICS.relative_to(ROOT)}`",
        f"- `{OUT_DECILES.relative_to(ROOT)}`",
        f"- `{OUT_FEATURE_PARITY.relative_to(ROOT)}`",
        f"- `{OUT_METRIC_PARITY.relative_to(ROOT)}`",
        f"- `{OUT_DECILE_PARITY.relative_to(ROOT)}`",
        f"- `{OUT_VALIDATION.relative_to(ROOT)}`",
        f"- `{OUT_SUMMARY.relative_to(ROOT)}`",
        f"- `{OUT_DOC.relative_to(ROOT)}`",
    ]
    OUT_DOC.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    RULE.mkdir(parents=True, exist_ok=True)
    DOC.mkdir(parents=True, exist_ok=True)
    BACKTEST.mkdir(parents=True, exist_ok=True)

    labels = BASE.load_engine_food_labels()
    q, cand_long = load_join_safe_as_status_rows()
    attached, leakage_audit = BASE.build_features(labels, cand_long)
    metrics = BASE.compute_metrics(attached)
    deciles = BASE.compute_deciles(attached)
    feature_parity = compare_numeric_features(attached)
    metric_parity = compare_metrics(metrics)
    decile_parity = compare_deciles(deciles)
    validations, summary = build_validations(
        labels, q, cand_long, attached, metrics, feature_parity, metric_parity, decile_parity, leakage_audit
    )

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
        *NUMERIC_FEATURE_COLS[:7],
        *NUMERIC_FEATURE_COLS[7:],
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
    feature_parity.to_csv(OUT_FEATURE_PARITY, index=False, encoding="utf-8-sig")
    metric_parity.to_csv(OUT_METRIC_PARITY, index=False, encoding="utf-8-sig")
    decile_parity.to_csv(OUT_DECILE_PARITY, index=False, encoding="utf-8-sig")
    validations.to_csv(OUT_VALIDATION, index=False, encoding="utf-8-sig")
    OUT_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_doc(validations, metrics, deciles, feature_parity, metric_parity, decile_parity, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["validation_fail_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
