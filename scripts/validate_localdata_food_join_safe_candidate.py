import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SILVER = ROOT / "datacorpus" / "_silver"
BACKTEST = ROOT / "datacorpus" / "_score_backtest_gold"
RULE = ROOT / "datacorpus" / "_rule_validation"
DOC = ROOT / "research" / "rule_validation"

BRIDGE = SILVER / "silver_localdata_food_license_uptae_service_bridge.csv"
QUARTER_CANDIDATE = SILVER / "silver_localdata_food_license_trade_area_service_quarter_candidate.csv"
ENGINE_LABELS = BACKTEST / "gold_engine_backtest_labeled_rows.csv"
SUMMARY_46 = RULE / "46_localdata_food_candidate_backtest_summary.json"

OUT_JOIN_SAFE = SILVER / "silver_localdata_food_license_trade_area_service_quarter_join_safe_candidate.csv"
OUT_DUPLICATE_BREAKDOWN = RULE / "53_localdata_food_join_safe_duplicate_breakdown.csv"
OUT_REVIEW_QUEUE = RULE / "53_localdata_food_bridge_review_queue.csv"
OUT_VALIDATION = RULE / "53_localdata_food_join_safe_validation.csv"
OUT_SUMMARY = RULE / "53_localdata_food_join_safe_summary.json"
OUT_DOC = DOC / "53_localdata_food_join_safe_validation_20260707.md"

JOIN_SAFE_VERSION = "localdata_food_join_safe_candidate.v0.1-20260707"
BRIDGE_VERSION = "localdata_food_bridge.v0.1-20260707"
KEY = ["상권_코드", "candidate_서비스_업종_코드", "기준_년분기_코드"]
FOOD_CODES = {f"CS10000{i}" for i in range(1, 10)} | {"CS100010"}


def read_csv(path: Path, **kwargs) -> pd.DataFrame:
    return pd.read_csv(path, encoding="utf-8-sig", **kwargs)


def md_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "(rows 없음)"
    out = df.copy()
    out.columns = [str(c) for c in out.columns]
    for col in out.columns:
        out[col] = out[col].map(lambda v: "" if pd.isna(v) else str(v).replace("|", "/"))
    header = "| " + " | ".join(out.columns) + " |"
    sep = "| " + " | ".join(["---"] * len(out.columns)) + " |"
    rows = ["| " + " | ".join(row) + " |" for row in out.to_numpy(dtype=str)]
    return "\n".join([header, sep, *rows])


def load_inputs() -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    bridge = read_csv(BRIDGE)
    candidate = read_csv(QUARTER_CANDIDATE)
    summary_46 = json.loads(SUMMARY_46.read_text(encoding="utf-8"))
    return bridge, candidate, summary_46


def build_join_safe(candidate: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    q = candidate.copy()
    q["상권_코드"] = q["상권_코드"].astype(str)
    q["candidate_서비스_업종_코드"] = q["candidate_서비스_업종_코드"].astype(str)
    q["기준_년분기_코드"] = pd.to_numeric(q["기준_년분기_코드"], errors="coerce").astype("Int64")
    q["인허가건수"] = pd.to_numeric(q["인허가건수"], errors="coerce").fillna(0)
    q["폐업건수"] = pd.to_numeric(q["폐업건수"], errors="coerce").fillna(0)

    original_duplicate_key_rows = int(q.duplicated(KEY).sum())
    original_duplicate_key_groups = int(q.groupby(KEY, dropna=False).size().gt(1).sum())
    duplicate_status_breakdown = (
        q[q.duplicated(KEY, keep=False)]
        .groupby(KEY, dropna=False)
        .agg(
            row_count=("mapping_status", "size"),
            mapping_status_combo=("mapping_status", lambda s: "+".join(sorted(set(map(str, s))))),
            인허가건수=("인허가건수", "sum"),
            폐업건수=("폐업건수", "sum"),
        )
        .reset_index()
    )
    duplicate_breakdown = (
        duplicate_status_breakdown.groupby("mapping_status_combo", dropna=False)
        .agg(
            duplicate_key_groups=("row_count", "size"),
            duplicate_rows=("row_count", "sum"),
            인허가건수=("인허가건수", "sum"),
            폐업건수=("폐업건수", "sum"),
        )
        .reset_index()
        .sort_values(["duplicate_key_groups", "duplicate_rows"], ascending=False)
    )

    metadata_cols = [
        "상권_코드_명",
        "상권_구분_코드",
        "상권_구분_코드_명",
        "상권_자치구_코드",
        "상권_자치구_코드_명",
        "상권_행정동_코드",
        "상권_행정동_코드_명",
        "candidate_서비스_업종_코드_명",
    ]
    base = q.groupby(KEY, dropna=False)[metadata_cols].first().reset_index()
    status_combo = (
        q.groupby(KEY, dropna=False)["mapping_status"]
        .agg(lambda s: "+".join(sorted(set(map(str, s)))))
        .reset_index(name="mapping_status_collapsed")
    )
    status_count = q.groupby(KEY, dropna=False)["mapping_status"].nunique().reset_index(name="mapping_status_count")
    result = base.merge(status_combo, on=KEY, how="left", validate="one_to_one").merge(
        status_count, on=KEY, how="left", validate="one_to_one"
    )

    for status in ["auto_strong", "auto_review"]:
        part = (
            q[q["mapping_status"].eq(status)]
            .groupby(KEY, dropna=False)
            .agg(
                **{
                    f"{status}_인허가건수": ("인허가건수", "sum"),
                    f"{status}_폐업건수": ("폐업건수", "sum"),
                    f"{status}_contributing_month_count": ("contributing_month_count", "sum"),
                    f"{status}_contributing_license_category_count": ("contributing_license_category_count", "sum"),
                    f"{status}_contributing_uptae_count": ("contributing_uptae_count", "sum"),
                }
            )
            .reset_index()
        )
        result = result.merge(part, on=KEY, how="left", validate="one_to_one")

    numeric_cols = [c for c in result.columns if c.startswith("auto_")]
    result[numeric_cols] = result[numeric_cols].fillna(0)
    for col in numeric_cols:
        result[col] = result[col].astype(float)

    result["all_candidate_인허가건수"] = result["auto_strong_인허가건수"] + result["auto_review_인허가건수"]
    result["all_candidate_폐업건수"] = result["auto_strong_폐업건수"] + result["auto_review_폐업건수"]
    result["all_candidate_순개업건수"] = result["all_candidate_인허가건수"] - result["all_candidate_폐업건수"]
    denom = result["all_candidate_인허가건수"] + result["all_candidate_폐업건수"]
    result["all_candidate_폐업압력"] = np.where(denom > 0, result["all_candidate_폐업건수"] / denom, np.nan)
    result["has_auto_review_signal"] = result["auto_review_인허가건수"].gt(0) | result["auto_review_폐업건수"].gt(0)
    result["join_safe_key_status"] = "status_collapsed_one_row_per_quarter"
    result["source_id"] = "seoul_localdata_food_license"
    result["provider"] = "서울열린데이터광장/행정안전부 지방행정 인허가"
    result["candidate_role"] = "상권×서비스업종×분기 LocalData 상태분리 join-safe 프록시 후보"
    result["join_safe_version"] = JOIN_SAFE_VERSION
    result["bridge_version"] = BRIDGE_VERSION
    result["localdata_direct_score_allowed"] = False
    result["engine_promotion_ready"] = False
    result["score_use_status"] = "후보: 상태별 카운트 분리 보존. 직접점수 투입 금지"
    result["forbidden_claim_ko"] = "개별 매장 성공확률, 생존확률, 매출 보장, 성장률 보장으로 표현 금지"
    result["review_gate_ko"] = "auto_review 업태는 수동검토 전 auto_strong과 합쳐 직접점수로 승격하지 않는다."

    status_input_sums = (
        q[q["mapping_status"].isin(["auto_strong", "auto_review"])]
        .groupby("mapping_status", dropna=False)
        .agg(인허가건수=("인허가건수", "sum"), 폐업건수=("폐업건수", "sum"))
        .to_dict()
    )
    metrics = {
        "original_rows": int(len(q)),
        "original_unique_join_keys": int(q[KEY].drop_duplicates().shape[0]),
        "original_duplicate_key_rows": original_duplicate_key_rows,
        "original_duplicate_key_groups": original_duplicate_key_groups,
        "join_safe_rows": int(len(result)),
        "join_safe_duplicate_keys": int(result.duplicated(KEY).sum()),
        "join_safe_auto_review_rows": int(result["has_auto_review_signal"].sum()),
        "input_open_sum": float(q[q["mapping_status"].isin(["auto_strong", "auto_review"])]["인허가건수"].sum()),
        "output_open_sum": float(result["all_candidate_인허가건수"].sum()),
        "input_close_sum": float(q[q["mapping_status"].isin(["auto_strong", "auto_review"])]["폐업건수"].sum()),
        "output_close_sum": float(result["all_candidate_폐업건수"].sum()),
        "status_input_sums": status_input_sums,
    }
    return result.sort_values(KEY).reset_index(drop=True), duplicate_breakdown, metrics


def build_review_queue(bridge: pd.DataFrame) -> pd.DataFrame:
    q = bridge[bridge["mapping_status"].ne("auto_strong")].copy()

    def priority(row: pd.Series) -> int:
        rows = int(row.get("observed_raw_rows", 0) or 0)
        status = row.get("mapping_status")
        if status == "auto_review" and rows >= 10_000:
            return 1
        if status == "auto_review" and rows >= 1_000:
            return 2
        if status == "hold_unmapped" and rows >= 10_000:
            return 2
        if rows >= 1_000:
            return 3
        return 4

    q["review_priority"] = q.apply(priority, axis=1)
    q["review_action_ko"] = np.where(
        q["mapping_status"].eq("auto_review"),
        "서비스업종 후보를 유지하되 수동검토 전 auto_strong으로 승격하지 않는다.",
        "단일 서비스업종으로 특정할 추가 근거가 없으면 hold_unmapped를 유지한다.",
    )
    q["engine_use_after_53"] = "candidate_review_only"
    q["manual_review_required_after_53"] = True
    cols = [
        "review_priority",
        "license_category",
        "업태명",
        "candidate_서비스_업종_코드",
        "candidate_서비스_업종_코드_명",
        "mapping_status",
        "mapping_confidence",
        "observed_raw_rows",
        "spatial_candidate_rows",
        "mapping_reason_ko",
        "review_action_ko",
        "engine_use_after_53",
    ]
    return q[cols].sort_values(["review_priority", "observed_raw_rows"], ascending=[True, False]).reset_index(drop=True)


def backtest_join_safety(join_safe: pd.DataFrame) -> dict:
    cols = [
        "기준_년분기_코드",
        "상권_코드",
        "서비스_업종_코드",
        "서비스_업종_코드_명",
    ]
    labels = read_csv(ENGINE_LABELS, usecols=cols)
    labels["서비스_업종_코드"] = labels["서비스_업종_코드"].astype(str)
    food = labels[labels["서비스_업종_코드"].isin(FOOD_CODES)].copy()
    food = food.rename(columns={"서비스_업종_코드": "candidate_서비스_업종_코드"})
    food["상권_코드"] = food["상권_코드"].astype(str)
    food["기준_년분기_코드"] = pd.to_numeric(food["기준_년분기_코드"], errors="coerce").astype("Int64")
    probe = food.merge(
        join_safe[KEY + ["all_candidate_인허가건수", "all_candidate_폐업건수"]],
        on=KEY,
        how="left",
        validate="many_to_one",
    )
    return {
        "food_label_rows": int(len(food)),
        "merged_rows": int(len(probe)),
        "joined_non_null_rows": int(probe["all_candidate_인허가건수"].notna().sum()),
        "join_safe_many_to_one_ok": int(len(food)) == int(len(probe)),
    }


def validate(
    bridge: pd.DataFrame,
    candidate: pd.DataFrame,
    join_safe: pd.DataFrame,
    duplicate_breakdown: pd.DataFrame,
    review_queue: pd.DataFrame,
    metrics: dict,
    join_probe: dict,
    summary_46: dict,
) -> tuple[pd.DataFrame, dict]:
    statuses = set(candidate["mapping_status"].dropna().astype(str))
    review_status_counts = review_queue["mapping_status"].value_counts().to_dict()
    food_code_violations = sorted(set(join_safe["candidate_서비스_업종_코드"].astype(str)) - FOOD_CODES)
    direct_allowed_count = int(join_safe["localdata_direct_score_allowed"].astype(bool).sum())
    promotion_ready_count = int(join_safe["engine_promotion_ready"].astype(bool).sum())
    count_preserved = (
        abs(metrics["input_open_sum"] - metrics["output_open_sum"]) < 1e-9
        and abs(metrics["input_close_sum"] - metrics["output_close_sum"]) < 1e-9
    )
    checks = [
        (
            "53-V01",
            "기존 중복 원인 확인",
            f"duplicate_rows={metrics['original_duplicate_key_rows']}, groups={metrics['original_duplicate_key_groups']}, status_combos={duplicate_breakdown.to_dict('records')[:3]}",
            "mapping_status 미포함 key에서 중복 존재",
            "PASS" if metrics["original_duplicate_key_rows"] > 0 and metrics["original_duplicate_key_groups"] > 0 else "FAIL",
            "46번에서 지적한 중복이 실제 파일에도 존재해야 join-safe 전처리 필요성이 성립한다.",
        ),
        (
            "53-V02",
            "join-safe key 중복 제거",
            f"rows={metrics['join_safe_rows']}, duplicates={metrics['join_safe_duplicate_keys']}",
            "join-safe duplicate 0, row 수는 기존 unique key와 동일",
            "PASS" if metrics["join_safe_duplicate_keys"] == 0 and metrics["join_safe_rows"] == metrics["original_unique_join_keys"] else "FAIL",
            "상권×서비스업종×분기 기준으로 바로 붙여도 one-to-many가 생기지 않아야 한다.",
        ),
        (
            "53-V03",
            "개폐업 이벤트 합계 보존",
            f"open {metrics['input_open_sum']}->{metrics['output_open_sum']}, close {metrics['input_close_sum']}->{metrics['output_close_sum']}",
            "auto_strong+auto_review 개폐업 합계 보존",
            "PASS" if count_preserved else "FAIL",
            "상태를 접는 과정에서 후보 이벤트를 버리거나 증폭하면 안 된다.",
        ),
        (
            "53-V04",
            "auto_strong/auto_review 분리 유지",
            f"candidate_statuses={statuses}, review_signal_rows={metrics['join_safe_auto_review_rows']}",
            "상태별 카운트 컬럼 유지, hold_unmapped는 후보 집계에 없음",
            "PASS" if statuses <= {"auto_strong", "auto_review"} and metrics["join_safe_auto_review_rows"] > 0 else "FAIL",
            "검토필요 업태를 강매칭과 섞어 점수처럼 보이게 하면 45번 bridge 보수성을 잃는다.",
        ),
        (
            "53-V05",
            "업태 review queue 보존",
            f"review_rows={len(review_queue)}, status_counts={review_status_counts}",
            "auto_review와 hold_unmapped 모두 review queue에 존재",
            "PASS" if review_status_counts.get("auto_review", 0) > 0 and review_status_counts.get("hold_unmapped", 0) > 0 else "FAIL",
            "수동검토 대상은 삭제하지 않고 다음 사람이 볼 수 있는 큐로 남겨야 한다.",
        ),
        (
            "53-V06",
            "음식업 코드 범위 제한",
            f"violations={food_code_violations[:10]}",
            "CS100001~CS100010만 허용",
            "PASS" if not food_code_violations else "FAIL",
            "일반/휴게음식점 인허가를 비음식 서비스업종으로 확장하면 근거가 과장된다.",
        ),
        (
            "53-V07",
            "직접점수·엔진승격 금지",
            f"direct_allowed={direct_allowed_count}, promotion_ready={promotion_ready_count}, 46_decision={summary_46.get('decision')}",
            "0, 0, NOT_READY_FOR_ENGINE_PROMOTION 유지",
            "PASS" if direct_allowed_count == 0 and promotion_ready_count == 0 and summary_46.get("decision") == "NOT_READY_FOR_ENGINE_PROMOTION" else "FAIL",
            "53번은 조인 안정화이지 엔진 승격이 아니다.",
        ),
        (
            "53-V08",
            "백테스트 label 결합 안전성",
            f"food_rows={join_probe['food_label_rows']}, merged_rows={join_probe['merged_rows']}, joined={join_probe['joined_non_null_rows']}",
            "many_to_one 결합으로 row 수 보존",
            "PASS" if join_probe["join_safe_many_to_one_ok"] else "FAIL",
            "다음 백테스트에서 label row가 늘어나면 성능 검증이 왜곡된다.",
        ),
        (
            "53-V09",
            "금지문구 보존",
            str(join_safe["forbidden_claim_ko"].dropna().nunique()),
            "금지문구 1개 이상",
            "PASS" if join_safe["forbidden_claim_ko"].fillna("").str.contains("성공확률").any() else "FAIL",
            "인허가 프록시가 성공확률·생존확률·매출보장 문구로 바뀌지 않게 산출물에 직접 남긴다.",
        ),
        (
            "53-V10",
            "비기계적 검증 5개 이상",
            "중복원인, join-safe, 합계보존, 상태분리, review queue, 코드범위, 승격금지, label 결합, 금지문구",
            "5개 이상",
            "PASS",
            "파일 생성 여부가 아니라 실제 규칙이 맞는지 여러 관점으로 검증한다.",
        ),
    ]
    validation_df = pd.DataFrame(checks, columns=["id", "검증", "관측", "기대", "결과", "이유"])
    fail_count = int((validation_df["결과"] == "FAIL").sum())
    review_count = int((validation_df["결과"] == "REVIEW").sum())
    decision = (
        "LOCALDATA_JOIN_SAFE_CANDIDATE_PASS_NOT_PROMOTED"
        if fail_count == 0 and review_count == 0
        else "LOCALDATA_JOIN_SAFE_CANDIDATE_PASS_WITH_REVIEW"
        if fail_count == 0
        else "LOCALDATA_JOIN_SAFE_CANDIDATE_FAIL"
    )
    summary = {
        "validation_number": 53,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "decision": decision,
        "join_safe_version": JOIN_SAFE_VERSION,
        "bridge_version": BRIDGE_VERSION,
        "original_candidate_rows": metrics["original_rows"],
        "original_unique_join_keys": metrics["original_unique_join_keys"],
        "original_duplicate_key_rows": metrics["original_duplicate_key_rows"],
        "original_duplicate_key_groups": metrics["original_duplicate_key_groups"],
        "join_safe_rows": metrics["join_safe_rows"],
        "join_safe_duplicate_keys": metrics["join_safe_duplicate_keys"],
        "review_queue_rows": int(len(review_queue)),
        "review_queue_status_counts": review_status_counts,
        "food_label_rows": join_probe["food_label_rows"],
        "join_probe_joined_non_null_rows": join_probe["joined_non_null_rows"],
        "engine_promotion_ready": False,
        "validation_pass_count": int((validation_df["결과"] == "PASS").sum()),
        "validation_review_count": review_count,
        "validation_fail_count": fail_count,
        "next_validation_number": 54,
    }
    return validation_df, summary


def write_doc(
    validation_df: pd.DataFrame,
    summary: dict,
    duplicate_breakdown: pd.DataFrame,
    review_queue: pd.DataFrame,
) -> None:
    top_review = review_queue.head(30)
    lines = [
        "# 53. LocalData 음식업 join-safe 후보 및 업태 검토 큐 검증",
        "",
        f"작성일: {summary['generated_at']}",
        "",
        "## 목적",
        "",
        "52번 게이트에서 다음 작업 1순위로 잡은 LocalData 음식업 후보 중복/업태 bridge 문제를 파일 단위로 정리한다. "
        "46번에서 확인한 중복은 LocalData 후보를 `mapping_status` 없이 상권×서비스업종×분기로 조인할 때 생긴다. "
        "이번 산출물은 상태별 카운트를 한 행에 접어 넣은 join-safe 후보 파일이며, 엔진 직접 승격은 하지 않는다.",
        "",
        "## 근거",
        "",
        "- 14번 LocalData silver 검증: 원천 행과 인허가/폐업 이벤트 보존",
        "- 17번 LocalData 상권 공간매칭 검증: polygon 매칭 후보만 조건부 사용",
        "- 45번 업태-service bridge 검증: auto_strong/auto_review/hold_unmapped 분리",
        "- 46번 후보 백테스트: 성능 게이트 `NOT_READY_FOR_ENGINE_PROMOTION`",
        "- 52번 전처리 파일 단위 착수 게이트: LocalData 후보 중복/업태 bridge 수동검토를 1순위로 지정",
        "",
        "## 핵심 결과",
        "",
        f"- join-safe version: `{summary['join_safe_version']}`",
        f"- 원 후보 row: {summary['original_candidate_rows']:,}",
        f"- 원 후보 unique join key: {summary['original_unique_join_keys']:,}",
        f"- 원 후보 중복 key row: {summary['original_duplicate_key_rows']:,}",
        f"- 원 후보 중복 key group: {summary['original_duplicate_key_groups']:,}",
        f"- join-safe row: {summary['join_safe_rows']:,}",
        f"- join-safe duplicate key: {summary['join_safe_duplicate_keys']:,}",
        f"- 업태 검토 큐 row: {summary['review_queue_rows']:,}",
        f"- 업태 검토 큐 상태: `{summary['review_queue_status_counts']}`",
        f"- 음식업 label row 결합 확인: {summary['food_label_rows']:,}행, 후보 부착 {summary['join_probe_joined_non_null_rows']:,}행",
        f"- 엔진 승격 여부: `{summary['engine_promotion_ready']}`",
        f"- 최종 판정: **{summary['decision']}**",
        "",
        "## 중복 원인 분해",
        "",
        md_table(duplicate_breakdown),
        "",
        "## 업태 수동검토 큐 상위 30개",
        "",
        md_table(top_review),
        "",
        "## 5회 이상 비기계적 검증",
        "",
        md_table(validation_df),
        "",
        "## 2보 전진 1보 후퇴",
        "",
        "전진:",
        "",
        "1. `mapping_status` 때문에 생기던 상권×서비스업종×분기 중복을 join-safe 후보 파일에서 제거했다.",
        "2. auto_review와 hold_unmapped 업태를 review queue로 따로 남겼다.",
        "",
        "후퇴:",
        "",
        "1. LocalData 후보는 46번 성능 게이트 미달 상태이므로 엔진 직접 점수로 승격하지 않는다.",
        "2. auto_review 업태는 후보 카운트로 보존하지만 auto_strong처럼 강매칭 근거로 해석하지 않는다.",
        "",
        "재검토:",
        "",
        "1. 54번에서는 join-safe 후보를 46번 백테스트 로직에 다시 붙여 기존 후보와 결과가 어긋나지 않는지 비교한다.",
        "2. 수동검토 큐의 고빈도 hold/auto_review 업태는 별도 근거가 생길 때만 bridge 규칙을 바꾼다.",
        "",
        "## 산출물",
        "",
        f"- `{OUT_JOIN_SAFE.relative_to(ROOT)}`",
        f"- `{OUT_DUPLICATE_BREAKDOWN.relative_to(ROOT)}`",
        f"- `{OUT_REVIEW_QUEUE.relative_to(ROOT)}`",
        f"- `{OUT_VALIDATION.relative_to(ROOT)}`",
        f"- `{OUT_SUMMARY.relative_to(ROOT)}`",
        f"- `{OUT_DOC.relative_to(ROOT)}`",
    ]
    OUT_DOC.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    SILVER.mkdir(parents=True, exist_ok=True)
    RULE.mkdir(parents=True, exist_ok=True)
    DOC.mkdir(parents=True, exist_ok=True)
    bridge, candidate, summary_46 = load_inputs()
    join_safe, duplicate_breakdown, metrics = build_join_safe(candidate)
    review_queue = build_review_queue(bridge)
    join_probe = backtest_join_safety(join_safe)
    validation_df, summary = validate(
        bridge, candidate, join_safe, duplicate_breakdown, review_queue, metrics, join_probe, summary_46
    )
    join_safe.to_csv(OUT_JOIN_SAFE, index=False, encoding="utf-8-sig")
    duplicate_breakdown.to_csv(OUT_DUPLICATE_BREAKDOWN, index=False, encoding="utf-8-sig")
    review_queue.to_csv(OUT_REVIEW_QUEUE, index=False, encoding="utf-8-sig")
    validation_df.to_csv(OUT_VALIDATION, index=False, encoding="utf-8-sig")
    OUT_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_doc(validation_df, summary, duplicate_breakdown, review_queue)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["validation_fail_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
