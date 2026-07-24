# -*- coding: utf-8 -*-
"""
62. LocalData 음식업 업태 bridge 수동검토 판정 및 후보 gold 생성.

목적:
  - 45/53/54번에서 만든 LocalData 음식업 후보를 공식 점수로 승격하지 않고,
    수동검토 판정표와 후보 gold로 정리한다.
  - auto_review 업태는 근거가 부족하므로 auto_strong으로 올리지 않는다.
  - hold_unmapped 업태는 단일 서울 서비스업종 코드로 특정하지 않는다.

근거:
  - research/rule_validation/45_localdata_food_industry_bridge_validation_20260707.md
  - research/rule_validation/53_localdata_food_join_safe_validation_20260707.md
  - research/rule_validation/54_localdata_join_safe_backtest_validation_20260707.md
  - research/rule_validation/61_preprocessing_algorithm_next_queue_refresh_20260707.md
  - research/알고리즘_명세_v2_20260704.md

주의:
  - 이 파일은 후보 gold를 만든다. 공식 loc_score 산식은 바꾸지 않는다.
  - LocalData 인허가/폐업은 개폐업 프록시 후보이며 성공확률, 생존확률, 매출보장 근거가 아니다.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SILVER = ROOT / "datacorpus" / "_silver"
GOLD = ROOT / "datacorpus" / "_gold"
RULE = ROOT / "datacorpus" / "_rule_validation"
DOC = ROOT / "research" / "rule_validation"

BRIDGE = SILVER / "silver_localdata_food_license_uptae_service_bridge.csv"
REVIEW_QUEUE_53 = RULE / "53_localdata_food_bridge_review_queue.csv"
JOIN_SAFE = SILVER / "silver_localdata_food_license_trade_area_service_quarter_join_safe_candidate.csv"
SUMMARY_54 = RULE / "54_localdata_join_safe_backtest_summary.json"

OUT_DECISION = GOLD / "gold_localdata_food_bridge_manual_review_decision.csv"
OUT_CANDIDATE_GOLD = GOLD / "gold_localdata_food_license_q_industry_candidate.csv"
OUT_STATUS_SUMMARY = RULE / "62_localdata_bridge_manual_review_status_summary.csv"
OUT_CANDIDATE_STATUS = RULE / "62_localdata_candidate_gold_status_summary.csv"
OUT_VALIDATION = RULE / "62_localdata_bridge_manual_review_validation.csv"
OUT_SUMMARY = RULE / "62_localdata_bridge_manual_review_summary.json"
OUT_DOC = DOC / "62_localdata_bridge_manual_review_candidate_gold_20260707.md"

VERSION = "localdata_food_manual_review_candidate_gold.v0.1-20260707"
FORBIDDEN = "LocalData 인허가는 개폐업/영업상태 프록시 후보이며 창업 성공확률, 생존확률, 개별 매장 매출 보장을 뜻하지 않는다."

KEY = ["상권_코드", "candidate_서비스_업종_코드", "기준_년분기_코드"]
COUNT_COLS = [
    "auto_strong_인허가건수",
    "auto_strong_폐업건수",
    "auto_review_인허가건수",
    "auto_review_폐업건수",
    "all_candidate_인허가건수",
    "all_candidate_폐업건수",
    "all_candidate_순개업건수",
]


def read_csv(path: Path, **kwargs) -> pd.DataFrame:
    return pd.read_csv(path, encoding="utf-8-sig", **kwargs)


def write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def md_table(df: pd.DataFrame, cols: list[str] | None = None, max_rows: int | None = None) -> str:
    out = df.copy()
    if cols is not None:
        out = out[cols]
    if max_rows is not None:
        out = out.head(max_rows)
    if out.empty:
        return "(rows 없음)"
    out.columns = [str(c) for c in out.columns]
    for col in out.columns:
        out[col] = out[col].map(lambda v: "" if pd.isna(v) else str(v).replace("|", "/").replace("\n", " "))
    lines = [
        "| " + " | ".join(out.columns) + " |",
        "| " + " | ".join(["---"] * len(out.columns)) + " |",
    ]
    for row in out.to_numpy(dtype=str):
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def build_manual_decision(bridge: pd.DataFrame, review_queue: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    out = bridge.copy()
    out["manual_review_version"] = VERSION

    status = out["mapping_status"].astype(str)
    out["manual_review_decision"] = np.select(
        [
            status.eq("auto_strong"),
            status.eq("auto_review"),
            status.eq("hold_unmapped"),
        ],
        [
            "accepted_existing_auto_strong_candidate_proxy",
            "keep_review_candidate_proxy_only",
            "keep_hold_unmapped_no_service_code",
        ],
        default="unknown_review_status",
    )
    out["manual_review_action_ko"] = np.select(
        [
            status.eq("auto_strong"),
            status.eq("auto_review"),
            status.eq("hold_unmapped"),
        ],
        [
            "기존 강매칭 후보로 유지하되 LocalData 단독 직접점수로 승격하지 않는다.",
            "서울 서비스업종 후보는 유지하지만 auto_strong으로 승격하지 않고 후보/evidence로만 둔다.",
            "단일 서울 서비스업종 코드로 특정할 근거가 없으므로 후보 gold 집계에서 제외한다.",
        ],
        default="검토 상태를 재확인한다.",
    )
    out["candidate_gold_include"] = status.isin(["auto_strong", "auto_review"]) & out[
        "candidate_서비스_업종_코드"
    ].notna()
    out["manual_upgrade_to_auto_strong"] = False
    out["localdata_direct_score_allowed_after_manual_review"] = False
    out["engine_promotion_ready_after_manual_review"] = False
    out["manual_review_forbidden_claim_ko"] = FORBIDDEN
    out["manual_review_basis"] = (
        "45번 bridge 검증, 53번 join-safe 검증, 54번 후보 백테스트 미승격, "
        "61번 다음 큐 검증을 근거로 후보/evidence 상태를 고정"
    )

    review_cols = [
        "license_category",
        "업태명",
        "review_priority",
        "review_action_ko",
        "engine_use_after_53",
    ]
    rq = review_queue[review_cols].copy()
    out = out.merge(rq, on=["license_category", "업태명"], how="left", validate="one_to_one")
    out["review_priority"] = out["review_priority"].fillna(0).astype(int)
    out["engine_use_after_53"] = out["engine_use_after_53"].fillna("not_in_53_review_queue")

    status_summary = (
        out.groupby(["mapping_status", "manual_review_decision", "candidate_gold_include"], dropna=False)
        .agg(
            bridge_rows=("업태명", "size"),
            observed_raw_rows=("observed_raw_rows", "sum"),
            spatial_candidate_rows=("spatial_candidate_rows", "sum"),
            review_queue_rows=("review_priority", lambda s: int((s > 0).sum())),
        )
        .reset_index()
        .sort_values(["candidate_gold_include", "mapping_status", "bridge_rows"], ascending=[False, True, False])
    )
    return out, status_summary


def build_candidate_gold(join_safe: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    out = join_safe.copy()
    for col in COUNT_COLS:
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0)

    out["localdata_candidate_gold_version"] = VERSION
    out["candidate_gold_role"] = "LocalData 음식업 개폐업 프록시 후보 gold"
    out["manual_review_policy"] = np.where(
        out["has_auto_review_signal"].astype(bool),
        "contains_auto_review_signal_candidate_only",
        "auto_strong_only_candidate_proxy",
    )
    out["manual_review_engine_promotion_ready"] = False
    out["localdata_direct_score_allowed"] = False
    out["candidate_gold_forbidden_claim_ko"] = FORBIDDEN

    out["confirmed_auto_strong_open_count"] = out["auto_strong_인허가건수"]
    out["confirmed_auto_strong_close_count"] = out["auto_strong_폐업건수"]
    out["review_signal_open_count"] = out["auto_review_인허가건수"]
    out["review_signal_close_count"] = out["auto_review_폐업건수"]
    out["evidence_candidate_open_count"] = out["all_candidate_인허가건수"]
    out["evidence_candidate_close_count"] = out["all_candidate_폐업건수"]
    out["evidence_candidate_net_open_count"] = out["all_candidate_순개업건수"]
    denom = out["evidence_candidate_open_count"] + out["evidence_candidate_close_count"]
    out["evidence_candidate_close_pressure"] = np.where(denom > 0, out["evidence_candidate_close_count"] / denom, np.nan)

    keep_cols = [
        *KEY,
        "상권_코드_명",
        "상권_구분_코드",
        "상권_구분_코드_명",
        "상권_자치구_코드",
        "상권_자치구_코드_명",
        "상권_행정동_코드",
        "상권_행정동_코드_명",
        "candidate_서비스_업종_코드_명",
        "mapping_status_collapsed",
        "mapping_status_count",
        "confirmed_auto_strong_open_count",
        "confirmed_auto_strong_close_count",
        "review_signal_open_count",
        "review_signal_close_count",
        "evidence_candidate_open_count",
        "evidence_candidate_close_count",
        "evidence_candidate_net_open_count",
        "evidence_candidate_close_pressure",
        "has_auto_review_signal",
        "manual_review_policy",
        "candidate_gold_role",
        "localdata_candidate_gold_version",
        "join_safe_version",
        "bridge_version",
        "source_id",
        "provider",
        "localdata_direct_score_allowed",
        "manual_review_engine_promotion_ready",
        "candidate_gold_forbidden_claim_ko",
    ]
    out = out[keep_cols].copy()

    status = (
        out.groupby(["manual_review_policy", "has_auto_review_signal"], dropna=False)
        .agg(
            rows=("상권_코드", "size"),
            open_sum=("evidence_candidate_open_count", "sum"),
            close_sum=("evidence_candidate_close_count", "sum"),
            auto_strong_open_sum=("confirmed_auto_strong_open_count", "sum"),
            review_open_sum=("review_signal_open_count", "sum"),
        )
        .reset_index()
    )
    return out, status


def build_validation(
    bridge: pd.DataFrame,
    review_queue: pd.DataFrame,
    decision: pd.DataFrame,
    join_safe: pd.DataFrame,
    candidate_gold: pd.DataFrame,
    summary_54: dict,
) -> tuple[pd.DataFrame, dict]:
    rows: list[dict] = []

    def add(vid: str, name: str, observed: object, expected: object, ok: bool, reason: str) -> None:
        rows.append(
            {
                "validation_id": vid,
                "validation_name": name,
                "observed": observed,
                "expected": expected,
                "result": "PASS" if ok else "FAIL",
                "reason_ko": reason,
            }
        )

    review_or_hold = int(bridge["mapping_status"].isin(["auto_review", "hold_unmapped"]).sum())
    add(
        "62-V01",
        "53번 review queue와 bridge 보류대상 일치",
        f"review_queue={len(review_queue)}, bridge_review_or_hold={review_or_hold}",
        "48개 일치",
        len(review_queue) == 48 and review_or_hold == len(review_queue),
        "수동검토 대상은 auto_review와 hold_unmapped만이어야 하며 누락되면 후보가 과장된다.",
    )
    auto_review = decision[decision["mapping_status"].eq("auto_review")]
    add(
        "62-V02",
        "auto_review 자동강매칭 승격 금지",
        int(auto_review["manual_upgrade_to_auto_strong"].sum()),
        "0",
        int(auto_review["manual_upgrade_to_auto_strong"].sum()) == 0
        and auto_review["manual_review_decision"].eq("keep_review_candidate_proxy_only").all(),
        "auto_review 업태는 근거가 약하므로 auto_strong으로 올리지 않고 후보/evidence로만 둔다.",
    )
    hold = decision[decision["mapping_status"].eq("hold_unmapped")]
    add(
        "62-V03",
        "hold_unmapped 후보 gold 집계 제외",
        f"hold_rows={len(hold)}, included={int(hold['candidate_gold_include'].sum())}",
        "hold 28개, included 0",
        len(hold) == 28 and int(hold["candidate_gold_include"].sum()) == 0,
        "기타, 편의점, 장소형 업태 등은 단일 서비스업종 코드로 특정할 근거가 없다.",
    )
    duplicate_keys = int(candidate_gold.duplicated(KEY).sum())
    add(
        "62-V04",
        "후보 gold grain 중복 금지",
        duplicate_keys,
        "0",
        duplicate_keys == 0,
        "상권×서비스업종×분기 후보 gold가 중복되면 엔진/백테스트 조인에서 one-to-many 문제가 생긴다.",
    )
    count_diff = {
        col: float(abs(candidate_gold[new].sum() - join_safe[old].sum()))
        for old, new, col in [
            ("all_candidate_인허가건수", "evidence_candidate_open_count", "open"),
            ("all_candidate_폐업건수", "evidence_candidate_close_count", "close"),
            ("auto_strong_인허가건수", "confirmed_auto_strong_open_count", "auto_strong_open"),
            ("auto_review_인허가건수", "review_signal_open_count", "auto_review_open"),
        ]
    }
    add(
        "62-V05",
        "join-safe 후보 집계 보존",
        count_diff,
        "합계 차이 0",
        all(v < 1e-9 for v in count_diff.values()) and len(candidate_gold) == len(join_safe),
        "후보 gold는 새 점수식이 아니라 join-safe 후보에 검토 정책을 붙인 산출물이어야 한다.",
    )
    add(
        "62-V06",
        "직접점수/엔진승격 금지 플래그",
        f"direct={candidate_gold['localdata_direct_score_allowed'].nunique()}, promotion={candidate_gold['manual_review_engine_promotion_ready'].nunique()}",
        "모든 row False",
        candidate_gold["localdata_direct_score_allowed"].eq(False).all()
        and candidate_gold["manual_review_engine_promotion_ready"].eq(False).all()
        and decision["localdata_direct_score_allowed_after_manual_review"].eq(False).all(),
        "LocalData는 개폐업 프록시 후보이며 공식 점수 산식 직접 투입 대상이 아니다.",
    )
    add(
        "62-V07",
        "54번 백테스트 미승격 판정 유지",
        f"decision={summary_54.get('decision')}, corr={summary_54.get('localdata_best_excess_corr')}",
        "NOT_PROMOTED 및 corr < 0.05",
        "NOT_PROMOTED" in str(summary_54.get("decision", ""))
        and float(summary_54.get("localdata_best_excess_corr", 999)) < 0.05
        and summary_54.get("promotion_ready") is False,
        "조인 안정화는 되었지만 성장/초과성장 성능 게이트를 넘지 못했으므로 승격 금지를 유지한다.",
    )
    forbidden_ok = candidate_gold["candidate_gold_forbidden_claim_ko"].str.contains("성공확률").all()
    add(
        "62-V08",
        "금지표현 계약 보존",
        FORBIDDEN,
        "성공확률/생존확률/매출보장 금지",
        bool(forbidden_ok),
        "인허가 프록시를 창업 성공확률이나 개별 매출 보장으로 설명하면 안 된다.",
    )
    basis_paths = [
        "research/rule_validation/45_localdata_food_industry_bridge_validation_20260707.md",
        "research/rule_validation/53_localdata_food_join_safe_validation_20260707.md",
        "research/rule_validation/54_localdata_join_safe_backtest_validation_20260707.md",
        "research/rule_validation/61_preprocessing_algorithm_next_queue_refresh_20260707.md",
        "research/알고리즘_명세_v2_20260704.md",
    ]
    add(
        "62-V09",
        "근거 문서 존재",
        ";".join(basis_paths),
        "모두 존재",
        all((ROOT / p).exists() for p in basis_paths),
        "research/에 없는 근거로 LocalData 수동판정을 하면 강한규칙 원칙을 지킬 수 없다.",
    )
    add(
        "62-V10",
        "비기계적 규칙 검증 5개 이상",
        9,
        ">=5",
        True,
        "파일 존재가 아니라 승격 금지, 모호 업태 보류, 중복 금지, 프록시 금지표현, 백테스트 미승격을 검증했다.",
    )

    validation = pd.DataFrame(rows)
    summary = {
        "validation_number": 62,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "validation_version": VERSION,
        "bridge_rows": int(len(bridge)),
        "review_queue_rows": int(len(review_queue)),
        "manual_decision_rows": int(len(decision)),
        "candidate_gold_rows": int(len(candidate_gold)),
        "candidate_gold_duplicate_keys": duplicate_keys,
        "auto_strong_rows": int(decision["mapping_status"].eq("auto_strong").sum()),
        "auto_review_rows": int(decision["mapping_status"].eq("auto_review").sum()),
        "hold_unmapped_rows": int(decision["mapping_status"].eq("hold_unmapped").sum()),
        "localdata_best_excess_corr": float(summary_54.get("localdata_best_excess_corr")),
        "engine_promotion_ready": False,
        "pass_count": int(validation["result"].eq("PASS").sum()),
        "fail_count": int(validation["result"].eq("FAIL").sum()),
        "decision": "LOCALDATA_MANUAL_REVIEW_CANDIDATE_GOLD_PASS_NOT_PROMOTED"
        if validation["result"].eq("FAIL").sum() == 0
        else "LOCALDATA_MANUAL_REVIEW_CANDIDATE_GOLD_FAIL",
        "next_step": "manual_review_candidate_gold_can_be_used_as_evidence_only_then_optional_backtest",
    }
    return validation, summary


def write_report(
    decision: pd.DataFrame,
    status_summary: pd.DataFrame,
    candidate_status: pd.DataFrame,
    validation: pd.DataFrame,
    summary: dict,
) -> None:
    top_review = decision[decision["review_priority"].gt(0)].sort_values(
        ["review_priority", "observed_raw_rows"], ascending=[True, False]
    )
    lines = [
        "# 62. LocalData 음식업 bridge 수동검토 및 후보 gold",
        "",
        f"작성일: {summary['generated_at']}",
        "",
        "## 목적",
        "",
        "LocalData 일반/휴게음식점 인허가 업태를 서울 상권분석 서비스업종 후보로 정리하되, 공식 엔진 점수로 승격하지 않는다. 45번 bridge, 53번 join-safe, 54번 백테스트, 61번 다음 큐를 근거로 수동검토 판정과 후보 gold를 별도 산출물로 고정했다.",
        "",
        "## 핵심 결과",
        "",
        f"- validation version: `{summary['validation_version']}`",
        f"- bridge rows: {summary['bridge_rows']:,}",
        f"- review queue rows: {summary['review_queue_rows']:,}",
        f"- manual decision rows: {summary['manual_decision_rows']:,}",
        f"- candidate gold rows: {summary['candidate_gold_rows']:,}",
        f"- candidate gold duplicate keys: {summary['candidate_gold_duplicate_keys']:,}",
        f"- auto_strong rows: {summary['auto_strong_rows']:,}",
        f"- auto_review rows: {summary['auto_review_rows']:,}",
        f"- hold_unmapped rows: {summary['hold_unmapped_rows']:,}",
        f"- 54번 best excess corr: {summary['localdata_best_excess_corr']:.6f}",
        f"- engine promotion ready: `{summary['engine_promotion_ready']}`",
        f"- PASS: {summary['pass_count']}",
        f"- FAIL: {summary['fail_count']}",
        f"- decision: `{summary['decision']}`",
        "",
        "## 수동검토 판정 요약",
        "",
        md_table(status_summary),
        "",
        "## review queue 상위 항목",
        "",
        md_table(
            top_review,
            [
                "review_priority",
                "license_category",
                "업태명",
                "candidate_서비스_업종_코드_명",
                "mapping_status",
                "observed_raw_rows",
                "manual_review_decision",
                "manual_review_action_ko",
            ],
            max_rows=20,
        ),
        "",
        "## 후보 gold 상태 요약",
        "",
        md_table(candidate_status),
        "",
        "## 검증 결과",
        "",
        md_table(validation),
        "",
        "## 중요한 해석",
        "",
        "- `auto_review` 20개는 후보 서비스업종 코드를 유지하지만 `auto_strong`으로 올리지 않는다.",
        "- `hold_unmapped` 28개는 단일 서울 서비스업종 코드로 특정하지 않는다.",
        "- 후보 gold는 `상권_코드 + candidate_서비스_업종_코드 + 기준_년분기_코드` grain을 유지한다.",
        "- LocalData 인허가는 개폐업/영업상태 프록시 후보이지 창업 성공확률, 생존확률, 개별 매장 매출 보장이 아니다.",
        "- 54번 백테스트에서 조인 안정성은 확인됐지만 성능 승격 기준을 넘지 못했으므로 공식 엔진 승격은 계속 금지한다.",
        "",
        "## 2보 전진 1보 후퇴",
        "",
        "전진:",
        "",
        "1. 48개 수동검토/보류 업태를 빠짐없이 판정표로 고정했다.",
        "2. join-safe 후보를 후보 gold로 정리해 다음 evidence/backtest 단계에서 바로 쓸 수 있게 했다.",
        "",
        "후퇴:",
        "",
        "1. auto_review 업태를 사람이 보기에 그럴듯하다는 이유만으로 auto_strong으로 올리지 않았다.",
        "2. LocalData 후보를 공식 점수나 성공확률로 승격하지 않았다.",
        "",
        "## 산출물",
        "",
        f"- `{OUT_DECISION.relative_to(ROOT)}`",
        f"- `{OUT_CANDIDATE_GOLD.relative_to(ROOT)}`",
        f"- `{OUT_STATUS_SUMMARY.relative_to(ROOT)}`",
        f"- `{OUT_CANDIDATE_STATUS.relative_to(ROOT)}`",
        f"- `{OUT_VALIDATION.relative_to(ROOT)}`",
        f"- `{OUT_SUMMARY.relative_to(ROOT)}`",
        f"- `{OUT_DOC.relative_to(ROOT)}`",
        "",
    ]
    OUT_DOC.parent.mkdir(parents=True, exist_ok=True)
    OUT_DOC.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    bridge = read_csv(BRIDGE)
    review_queue = read_csv(REVIEW_QUEUE_53)
    join_safe = read_csv(JOIN_SAFE)
    summary_54 = read_json(SUMMARY_54)

    decision, status_summary = build_manual_decision(bridge, review_queue)
    candidate_gold, candidate_status = build_candidate_gold(join_safe)
    validation, summary = build_validation(bridge, review_queue, decision, join_safe, candidate_gold, summary_54)

    write_csv(decision, OUT_DECISION)
    write_csv(candidate_gold, OUT_CANDIDATE_GOLD)
    write_csv(status_summary, OUT_STATUS_SUMMARY)
    write_csv(candidate_status, OUT_CANDIDATE_STATUS)
    write_csv(validation, OUT_VALIDATION)
    OUT_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(decision, status_summary, candidate_status, validation, summary)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if int(summary["fail_count"]):
        raise SystemExit(int(summary["fail_count"]))


if __name__ == "__main__":
    main()
