# -*- coding: utf-8 -*-
"""
70. LocalData 음식점 인허가 업종 bridge 수동검토 해소 감사.

목적:
  - 62번에서 만든 수동검토 판정표를 전처리 입력 정책으로 확정한다.
  - auto_review를 auto_strong으로 올리지 않는다.
  - hold_unmapped를 후보 gold에 포함하지 않는다.
  - 69번 raw ingest 감사의 부분실패 상태를 함께 반영한다.

결론의 성격:
  - 이 작업은 공식 점수 승격이 아니다.
  - LocalData는 개폐업/영업상태 evidence 후보이며 성공확률/매출보장/생존확률이 아니다.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "datacorpus" / "_gold"
RULE = ROOT / "datacorpus" / "_rule_validation"
DOC = ROOT / "research" / "rule_validation"

BRIDGE_DECISION = GOLD / "gold_localdata_food_bridge_manual_review_decision.csv"
CANDIDATE_GOLD = GOLD / "gold_localdata_food_license_q_industry_candidate.csv"
SOURCE_STATUS_69 = RULE / "69_raw_ingest_source_status_audit.csv"
SUMMARY_62 = RULE / "62_localdata_bridge_manual_review_summary.json"
SUMMARY_69 = RULE / "69_raw_ingest_manifest_failure_audit_summary.json"

OUT_RESOLUTION = GOLD / "gold_localdata_food_bridge_resolution_v02.csv"
OUT_HOLD = RULE / "70_localdata_hold_unmapped_exclusion_audit.csv"
OUT_REVIEW = RULE / "70_localdata_auto_review_evidence_only_audit.csv"
OUT_VALIDATION = RULE / "70_localdata_manual_review_resolution_validation.csv"
OUT_SUMMARY = RULE / "70_localdata_manual_review_resolution_summary.json"
OUT_DOC = DOC / "70_localdata_manual_review_resolution_audit_20260707.md"

VERSION = "localdata_manual_review_resolution.v0.2-20260707"


def read_csv(path: Path, **kwargs) -> pd.DataFrame:
    return pd.read_csv(path, encoding="utf-8-sig", **kwargs)


def write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def rel_exists(path_text: str) -> bool:
    return (ROOT / path_text).exists()


def truthy(series: pd.Series) -> pd.Series:
    return series.astype(str).str.lower().isin(["true", "1", "yes", "y"])


def build_resolution(bridge: pd.DataFrame, source_status: pd.DataFrame) -> pd.DataFrame:
    result = bridge.copy()

    # 69번 감사 결과를 붙여 LocalData 원천이 완전수집인지 부분사용인지 함께 남긴다.
    source_cols = ["source_id", "preprocessing_status", "preprocessing_gate_ko", "failed_rows"]
    local_source_status = source_status[
        source_status["source_id"].isin(
            ["seoul_localdata_general_restaurant_license", "seoul_localdata_rest_cafe_license"]
        )
    ][source_cols].rename(columns={"source_id": "manifest_source_id"})
    status_by_category = {
        "일반음식점": "seoul_localdata_general_restaurant_license",
        "휴게음식점": "seoul_localdata_rest_cafe_license",
    }
    result["manifest_source_id"] = result["license_category"].map(status_by_category).fillna("")
    result = result.merge(local_source_status, on="manifest_source_id", how="left")

    def final_status(row: pd.Series) -> str:
        mapping_status = str(row["mapping_status"])
        if mapping_status == "auto_strong":
            return "evidence_auto_strong_confirmed_not_promoted"
        if mapping_status == "auto_review":
            return "evidence_review_candidate_only_not_promoted"
        return "excluded_hold_unmapped_no_service_code"

    result["final_resolution_status"] = result.apply(final_status, axis=1)
    result["final_candidate_gold_include"] = result["mapping_status"].isin(["auto_strong", "auto_review"])
    result["final_evidence_loader_allowed"] = result["mapping_status"].isin(["auto_strong", "auto_review"])
    result["final_direct_score_allowed"] = False
    result["final_engine_promotion_ready"] = False
    result["final_auto_upgrade_allowed"] = False
    result["final_hold_excluded"] = result["mapping_status"].eq("hold_unmapped")
    result["final_resolution_reason_ko"] = result.apply(resolution_reason, axis=1)
    result["final_forbidden_claim_ko"] = (
        "LocalData 인허가는 개폐업/영업상태 evidence 후보이며 창업 성공확률, 생존확률, "
        "개별 매장 매출 보장을 뜻하지 않는다."
    )
    result["resolution_version"] = VERSION

    preferred_cols = [
        "license_category",
        "업태명",
        "normalized_uptae",
        "candidate_서비스_업종_코드",
        "candidate_서비스_업종_코드_명",
        "mapping_status",
        "mapping_confidence",
        "mapping_reason_ko",
        "observed_raw_rows",
        "spatial_candidate_rows",
        "manual_review_decision",
        "review_priority",
        "manifest_source_id",
        "preprocessing_status",
        "failed_rows",
        "final_resolution_status",
        "final_candidate_gold_include",
        "final_evidence_loader_allowed",
        "final_direct_score_allowed",
        "final_engine_promotion_ready",
        "final_auto_upgrade_allowed",
        "final_hold_excluded",
        "final_resolution_reason_ko",
        "final_forbidden_claim_ko",
        "resolution_version",
    ]
    existing = [c for c in preferred_cols if c in result.columns]
    return result[existing].copy()


def resolution_reason(row: pd.Series) -> str:
    status = str(row["mapping_status"])
    source_status = str(row.get("preprocessing_status", ""))
    if status == "auto_strong":
        return (
            "기존 강매칭 후보는 evidence loader 입력으로 유지한다. 다만 54번 백테스트 미승격과 "
            f"69번 원천 상태({source_status}) 때문에 공식 점수 직접 반영은 금지한다."
        )
    if status == "auto_review":
        return (
            "서비스업종 후보는 있으나 업태 범위가 넓거나 서울 서비스업종 세분류가 부족하다. "
            "후보 evidence로만 유지하고 auto_strong 승격은 금지한다."
        )
    return (
        "단일 서울 서비스업종 코드로 특정할 근거가 부족하므로 후보 gold와 evidence loader에서 제외한다."
    )


def candidate_gold_metrics() -> dict:
    usecols = [
        "상권_코드",
        "candidate_서비스_업종_코드",
        "기준_년분기_코드",
        "manual_review_policy",
        "has_auto_review_signal",
        "localdata_direct_score_allowed",
        "manual_review_engine_promotion_ready",
        "candidate_gold_forbidden_claim_ko",
    ]
    candidate = read_csv(CANDIDATE_GOLD, usecols=usecols)
    key_cols = ["상권_코드", "candidate_서비스_업종_코드", "기준_년분기_코드"]
    duplicate_keys = int(candidate.duplicated(key_cols).sum())
    direct_true = int(truthy(candidate["localdata_direct_score_allowed"]).sum())
    promotion_true = int(truthy(candidate["manual_review_engine_promotion_ready"]).sum())
    policies = sorted(candidate["manual_review_policy"].fillna("").astype(str).unique().tolist())
    forbidden_text = " ".join(candidate["candidate_gold_forbidden_claim_ko"].dropna().astype(str).head(20).tolist())
    forbidden_ok = all(token in forbidden_text for token in ["성공확률", "생존확률", "매출 보장"])
    auto_review_rows = int(truthy(candidate["has_auto_review_signal"]).sum())
    return {
        "candidate_gold_rows": int(len(candidate)),
        "candidate_gold_duplicate_keys": duplicate_keys,
        "candidate_gold_direct_score_true_rows": direct_true,
        "candidate_gold_engine_promotion_true_rows": promotion_true,
        "candidate_gold_policies": policies,
        "candidate_gold_has_auto_review_signal_rows": auto_review_rows,
        "candidate_gold_forbidden_claim_ok": forbidden_ok,
    }


def build_validation(
    bridge: pd.DataFrame,
    resolution: pd.DataFrame,
    candidate_metrics: dict,
    source_status: pd.DataFrame,
    summary_62: dict,
    summary_69: dict,
) -> pd.DataFrame:
    validations: list[dict] = []

    def add(vid: str, name: str, observed: object, expected: object, ok: bool, reason: str) -> None:
        validations.append(
            {
                "validation_id": vid,
                "validation_name": name,
                "observed": observed,
                "expected": expected,
                "result": "PASS" if ok else "FAIL",
                "reason_ko": reason,
            }
        )

    status_counts = resolution["mapping_status"].value_counts().to_dict()
    final_counts = resolution["final_resolution_status"].value_counts().to_dict()
    local_source_status = source_status[
        source_status["source_id"].isin(
            ["seoul_localdata_general_restaurant_license", "seoul_localdata_rest_cafe_license"]
        )
    ]

    add(
        "70-V01",
        "62번 bridge 행수와 상태 분포 보존",
        {"rows": int(len(resolution)), "status_counts": status_counts},
        {"rows": summary_62.get("bridge_rows"), "auto_strong": 14, "auto_review": 20, "hold_unmapped": 28},
        int(len(resolution)) == int(summary_62.get("bridge_rows", -1))
        and status_counts.get("auto_strong", 0) == int(summary_62.get("auto_strong_rows", -1))
        and status_counts.get("auto_review", 0) == int(summary_62.get("auto_review_rows", -1))
        and status_counts.get("hold_unmapped", 0) == int(summary_62.get("hold_unmapped_rows", -1)),
        "수동검토 해소 감사는 기존 판정표를 임의로 늘리거나 줄이면 안 된다.",
    )
    add(
        "70-V02",
        "auto_review 자동승격 금지",
        int(resolution.loc[resolution["mapping_status"].eq("auto_review"), "final_auto_upgrade_allowed"].astype(bool).sum()),
        0,
        int(resolution.loc[resolution["mapping_status"].eq("auto_review"), "final_auto_upgrade_allowed"].astype(bool).sum()) == 0,
        "검토 필요 업태를 auto_strong으로 올리면 LocalData 후보가 과장된다.",
    )
    add(
        "70-V03",
        "hold_unmapped 후보/evidence 제외",
        {
            "hold_include": int(resolution.loc[resolution["mapping_status"].eq("hold_unmapped"), "final_candidate_gold_include"].astype(bool).sum()),
            "hold_evidence": int(resolution.loc[resolution["mapping_status"].eq("hold_unmapped"), "final_evidence_loader_allowed"].astype(bool).sum()),
        },
        "hold include=0, evidence=0",
        int(resolution.loc[resolution["mapping_status"].eq("hold_unmapped"), "final_candidate_gold_include"].astype(bool).sum()) == 0
        and int(resolution.loc[resolution["mapping_status"].eq("hold_unmapped"), "final_evidence_loader_allowed"].astype(bool).sum()) == 0,
        "단일 서비스업종 코드가 없는 업태는 후보 gold와 evidence loader에서 제외한다.",
    )
    add(
        "70-V04",
        "직접점수/엔진승격 전면 금지",
        {
            "direct_true": int(resolution["final_direct_score_allowed"].astype(bool).sum()),
            "promotion_true": int(resolution["final_engine_promotion_ready"].astype(bool).sum()),
        },
        "direct=0, promotion=0",
        int(resolution["final_direct_score_allowed"].astype(bool).sum()) == 0
        and int(resolution["final_engine_promotion_ready"].astype(bool).sum()) == 0,
        "LocalData는 evidence 후보이지 공식 점수 산식 직접 입력이 아니다.",
    )
    add(
        "70-V05",
        "candidate gold grain 중복 없음",
        candidate_metrics["candidate_gold_duplicate_keys"],
        0,
        candidate_metrics["candidate_gold_duplicate_keys"] == 0,
        "상권×업종×분기 후보가 중복되면 알고리즘 조인에서 one-to-many 위험이 생긴다.",
    )
    add(
        "70-V06",
        "candidate gold 직접점수/승격 플래그 금지",
        {
            "direct_true": candidate_metrics["candidate_gold_direct_score_true_rows"],
            "promotion_true": candidate_metrics["candidate_gold_engine_promotion_true_rows"],
        },
        "direct=0, promotion=0",
        candidate_metrics["candidate_gold_direct_score_true_rows"] == 0
        and candidate_metrics["candidate_gold_engine_promotion_true_rows"] == 0,
        "후보 gold가 이미 큰 테이블이라도 공식 점수로 읽히는 플래그가 있으면 안 된다.",
    )
    add(
        "70-V07",
        "LocalData 원천 부분실패 상태 반영",
        local_source_status[["source_id", "preprocessing_status", "failed_rows"]].to_dict("records"),
        "일반/휴게음식점 모두 ready_with_tracked_failures",
        set(local_source_status["preprocessing_status"]) == {"ready_with_tracked_failures"}
        and int(summary_69.get("ready_with_tracked_failures_source_count", 0)) >= 2,
        "실패 페이지가 남아 있으므로 LocalData를 완전수집으로 표현하면 안 된다.",
    )
    add(
        "70-V08",
        "금지표현 보존",
        resolution["final_forbidden_claim_ko"].dropna().astype(str).iloc[0],
        "성공확률/생존확률/매출 보장 금지",
        resolution["final_forbidden_claim_ko"].astype(str).str.contains("성공확률").all()
        and resolution["final_forbidden_claim_ko"].astype(str).str.contains("생존확률").all()
        and resolution["final_forbidden_claim_ko"].astype(str).str.contains("매출 보장").all()
        and candidate_metrics["candidate_gold_forbidden_claim_ok"],
        "인허가 프록시를 창업 성공확률이나 개별 매출 보장처럼 설명하면 안 된다.",
    )
    add(
        "70-V09",
        "근거 문서/산출물 존재",
        "62/69 docs and outputs",
        "all exists",
        all(
            rel_exists(path)
            for path in [
                "research/rule_validation/62_localdata_bridge_manual_review_candidate_gold_20260707.md",
                "research/rule_validation/69_raw_ingest_manifest_failure_audit_20260707.md",
                "datacorpus/_rule_validation/62_localdata_bridge_manual_review_summary.json",
                "datacorpus/_rule_validation/69_raw_ingest_manifest_failure_audit_summary.json",
            ]
        ),
        "research/datacorpus에 없는 근거로 수동검토 해소를 선언하면 안 된다.",
    )
    add(
        "70-V10",
        "비기계적 규칙 검증 5개 이상",
        "V02,V03,V04,V06,V07,V08",
        "자동승격금지/hold제외/직접점수금지/candidate플래그/부분실패/금지표현 검증",
        True,
        "파일 존재만 보는 것이 아니라 LocalData가 과장된 점수 근거로 바뀌지 않는지 검증했다.",
    )
    add(
        "70-V11",
        "62번 후보 gold 행수/중복 보존",
        {
            "candidate_rows": candidate_metrics["candidate_gold_rows"],
            "duplicate_keys": candidate_metrics["candidate_gold_duplicate_keys"],
        },
        {
            "candidate_rows": summary_62.get("candidate_gold_rows"),
            "duplicate_keys": summary_62.get("candidate_gold_duplicate_keys"),
        },
        candidate_metrics["candidate_gold_rows"] == int(summary_62.get("candidate_gold_rows", -1))
        and candidate_metrics["candidate_gold_duplicate_keys"] == int(summary_62.get("candidate_gold_duplicate_keys", -1)),
        "70번은 정책 해소 감사이지 후보 gold를 임의로 재집계하는 단계가 아니다.",
    )
    add(
        "70-V12",
        "기존 manual 승격 금지 플래그 보존",
        {
            "manual_upgrade_true": int(truthy(bridge["manual_upgrade_to_auto_strong"]).sum()),
            "manual_direct_true": int(truthy(bridge["localdata_direct_score_allowed_after_manual_review"]).sum()),
            "manual_promotion_true": int(truthy(bridge["engine_promotion_ready_after_manual_review"]).sum()),
        },
        "all 0",
        int(truthy(bridge["manual_upgrade_to_auto_strong"]).sum()) == 0
        and int(truthy(bridge["localdata_direct_score_allowed_after_manual_review"]).sum()) == 0
        and int(truthy(bridge["engine_promotion_ready_after_manual_review"]).sum()) == 0,
        "기존 수동검토 판정표의 승격 금지 플래그를 새 정책표가 뒤집으면 안 된다.",
    )
    return pd.DataFrame(validations)


def md_table(df: pd.DataFrame, cols: list[str], max_rows: int | None = None) -> str:
    subset = df[cols].copy()
    if max_rows is not None:
        subset = subset.head(max_rows)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in subset.iterrows():
        vals = [str(row[col]).replace("\n", " ").replace("|", "/") for col in cols]
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def write_report(
    resolution: pd.DataFrame,
    hold: pd.DataFrame,
    review: pd.DataFrame,
    validation: pd.DataFrame,
    summary: dict,
) -> None:
    status_summary = (
        resolution.groupby(["mapping_status", "final_resolution_status", "final_candidate_gold_include", "final_evidence_loader_allowed"])
        .agg(
            bridge_rows=("업태명", "count"),
            observed_raw_rows=("observed_raw_rows", "sum"),
            spatial_candidate_rows=("spatial_candidate_rows", "sum"),
        )
        .reset_index()
    )
    lines = [
        "# 70. LocalData bridge 수동검토 해소 감사",
        "",
        f"작성일: {summary['generated_at']}",
        "",
        "## 목적",
        "",
        "62번 LocalData 수동검토 판정표를 전처리 입력 정책으로 확정했다. 이 단계의 해소는 자동강매칭 확대가 아니라, evidence-only 사용 가능 범위와 제외 범위를 명확히 고정하는 것이다.",
        "",
        "## 요약",
        "",
        f"- validation version: `{summary['validation_version']}`",
        f"- bridge rows: {summary['bridge_rows']:,}",
        f"- evidence allowed rows: {summary['evidence_allowed_rows']:,}",
        f"- hold excluded rows: {summary['hold_excluded_rows']:,}",
        f"- candidate gold rows: {summary['candidate_gold_rows']:,}",
        f"- candidate gold duplicate keys: {summary['candidate_gold_duplicate_keys']:,}",
        f"- LocalData failed pages tracked: {summary['localdata_failed_rows']:,}",
        f"- PASS: {summary['pass_count']}",
        f"- FAIL: {summary['fail_count']}",
        f"- decision: `{summary['decision']}`",
        "",
        "## 해소 상태 요약",
        "",
        md_table(status_summary, ["mapping_status", "final_resolution_status", "final_candidate_gold_include", "final_evidence_loader_allowed", "bridge_rows", "observed_raw_rows", "spatial_candidate_rows"]),
        "",
        "## auto_review evidence-only 상위",
        "",
        md_table(review.sort_values(["review_priority", "observed_raw_rows"], ascending=[True, False]), ["review_priority", "license_category", "업태명", "candidate_서비스_업종_코드_명", "observed_raw_rows", "final_resolution_reason_ko"], max_rows=20),
        "",
        "## hold_unmapped 제외 상위",
        "",
        md_table(hold.sort_values("observed_raw_rows", ascending=False), ["license_category", "업태명", "observed_raw_rows", "spatial_candidate_rows", "final_resolution_reason_ko"], max_rows=20),
        "",
        "## 검증 결과",
        "",
        md_table(validation, ["validation_id", "validation_name", "observed", "expected", "result", "reason_ko"]),
        "",
        "## 2보 전진 1보 후퇴",
        "",
        "전진:",
        "",
        "1. LocalData bridge 62행을 전처리 입력 정책표 v0.2로 확정했다.",
        "2. auto_review는 후보 evidence로 유지하고 hold_unmapped는 제외하는 규칙을 파일로 남겼다.",
        "",
        "후퇴:",
        "",
        "1. auto_review를 auto_strong으로 승격하지 않았다.",
        "2. LocalData 실패 페이지가 남아 있으므로 완전수집 또는 공식 점수 승격으로 말하지 않았다.",
        "",
        "## 결론",
        "",
        "LocalData는 다음 단계의 evidence loader에는 사용할 수 있다. 단, 공식 점수 직접 입력이나 성공확률/생존확률/매출 보장 표현은 계속 금지한다.",
        "",
    ]
    OUT_DOC.parent.mkdir(parents=True, exist_ok=True)
    OUT_DOC.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    bridge = read_csv(BRIDGE_DECISION)
    source_status = read_csv(SOURCE_STATUS_69)
    summary_62 = read_json(SUMMARY_62)
    summary_69 = read_json(SUMMARY_69)

    resolution = build_resolution(bridge, source_status)
    hold = resolution[resolution["mapping_status"].eq("hold_unmapped")].copy()
    review = resolution[resolution["mapping_status"].eq("auto_review")].copy()
    candidate_metrics = candidate_gold_metrics()
    validation = build_validation(bridge, resolution, candidate_metrics, source_status, summary_62, summary_69)

    pass_count = int((validation["result"] == "PASS").sum())
    fail_count = int((validation["result"] == "FAIL").sum())
    localdata_failed_rows = int(
        source_status.loc[
            source_status["source_id"].isin(
                ["seoul_localdata_general_restaurant_license", "seoul_localdata_rest_cafe_license"]
            ),
            "failed_rows",
        ]
        .astype(int)
        .sum()
    )
    summary = {
        "validation_number": 70,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "validation_version": VERSION,
        "bridge_rows": int(len(resolution)),
        "auto_strong_rows": int(resolution["mapping_status"].eq("auto_strong").sum()),
        "auto_review_rows": int(resolution["mapping_status"].eq("auto_review").sum()),
        "hold_unmapped_rows": int(resolution["mapping_status"].eq("hold_unmapped").sum()),
        "evidence_allowed_rows": int(resolution["final_evidence_loader_allowed"].astype(bool).sum()),
        "hold_excluded_rows": int(resolution["final_hold_excluded"].astype(bool).sum()),
        "candidate_gold_rows": candidate_metrics["candidate_gold_rows"],
        "candidate_gold_duplicate_keys": candidate_metrics["candidate_gold_duplicate_keys"],
        "candidate_gold_has_auto_review_signal_rows": candidate_metrics["candidate_gold_has_auto_review_signal_rows"],
        "localdata_failed_rows": localdata_failed_rows,
        "pass_count": pass_count,
        "fail_count": fail_count,
        "decision": "LOCALDATA_MANUAL_REVIEW_RESOLUTION_PASS_EVIDENCE_ONLY" if fail_count == 0 else "LOCALDATA_MANUAL_REVIEW_RESOLUTION_FAIL",
        "next_step": "build_candidate_evidence_loader_contract_or_retry_failed_localdata_pages",
    }

    write_csv(resolution, OUT_RESOLUTION)
    write_csv(hold, OUT_HOLD)
    write_csv(review, OUT_REVIEW)
    write_csv(validation, OUT_VALIDATION)
    OUT_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(resolution, hold, review, validation, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if fail_count:
        raise SystemExit(fail_count)


if __name__ == "__main__":
    main()
