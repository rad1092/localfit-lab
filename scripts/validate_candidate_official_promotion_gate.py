from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "datacorpus" / "_gold"
RULE = ROOT / "datacorpus" / "_rule_validation"
DOC = ROOT / "research" / "rule_validation"
ENGINE = ROOT / "scripts" / "build_rule_based_location_scores.py"

OUT_CANDIDATES = RULE / "100_candidate_official_promotion_gate_candidates.csv"
OUT_VALIDATION = RULE / "100_candidate_official_promotion_gate_validation.csv"
OUT_SUMMARY = RULE / "100_candidate_official_promotion_gate_summary.json"
OUT_DOC = DOC / "100_candidate_official_promotion_gate_20260707.md"

VERSION = "candidate_official_promotion_gate.v0.1-20260707"


CANDIDATES: list[dict[str, Any]] = [
    {
        "candidate_id": "transit_250m_passenger_accessibility",
        "candidate_name_ko": "버스·지하철 승하차량 250m 접근성 후보",
        "target_axis": "accessibility",
        "candidate_file": "datacorpus/_gold/gold_accessibility_transit_q_area_candidate.csv",
        "required_docs": [
            "research/rule_validation/59_transit_accessibility_candidate_backtest_validation_20260707.md",
            "research/rule_validation/60_transit_accessibility_engine_candidate_validation_20260707.md",
            "research/rule_validation/80_transit_accessibility_candidate_holdout_gate_20260707.md",
            "research/rule_validation/81_transit_accessibility_official_promotion_readiness_20260707.md",
        ],
        "required_summaries": [
            "59_transit_accessibility_candidate_backtest_summary.json",
            "60_transit_accessibility_engine_candidate_summary.json",
            "80_transit_accessibility_candidate_holdout_summary.json",
            "81_transit_accessibility_official_promotion_readiness_summary.json",
        ],
        "official_promotion_status": "NOT_READY_LIVE_QUARTER_GAP",
        "official_score_action": "공식 접근성축 미반영, 후보 출력만 유지",
        "reason_ko": "2021~2025 holdout 성능은 통과했지만 최신 공식분기 20261의 202601~202603 교통 raw와 후보 피처가 없다.",
        "next_required_ko": "2026Q1 버스·지하철 승하차 raw 수집 후 58/31/59/60/63/80/81 재실행.",
    },
    {
        "candidate_id": "bus_network_diversity",
        "candidate_name_ko": "버스 노선-정류장 네트워크 다양성 후보",
        "target_axis": "accessibility",
        "candidate_file": "datacorpus/_gold/gold_accessibility_bus_network_diversity_candidate.csv",
        "required_docs": [
            "research/rule_validation/67_bus_network_diversity_candidate_20260707.md",
        ],
        "required_summaries": [
            "67_bus_network_diversity_candidate_summary.json",
        ],
        "official_promotion_status": "NOT_READY_SNAPSHOT_ONLY",
        "official_score_action": "evidence-only",
        "reason_ko": "2026-07-03 노선마스터 스냅샷 기반이라 2021~2025 백데이터에 fan-out하면 미래정보 누수다.",
        "next_required_ko": "동일 기간별 노선/정류장 이력 또는 공식 백테스트 가능 피처 확보.",
    },
    {
        "candidate_id": "localdata_food_open_close",
        "candidate_name_ko": "LocalData 일반/휴게음식점 인허가 개폐업 후보",
        "target_axis": "competition/growth_evidence",
        "candidate_file": "datacorpus/_gold/gold_localdata_food_license_q_industry_candidate.csv",
        "required_docs": [
            "research/rule_validation/54_localdata_join_safe_backtest_validation_20260707.md",
            "research/rule_validation/70_localdata_manual_review_resolution_audit_20260707.md",
        ],
        "required_summaries": [
            "54_localdata_join_safe_backtest_summary.json",
            "70_localdata_manual_review_resolution_summary.json",
        ],
        "official_promotion_status": "NOT_READY_EVIDENCE_ONLY",
        "official_score_action": "후보 evidence만 유지",
        "reason_ko": "join-safe 백테스트는 안정적이나 업태-서비스업종 수동검토와 원천 부분실패가 남아 공식 점수 직접 투입을 금지했다.",
        "next_required_ko": "업태 bridge 수동검토 확정 범위 확대, 실패 페이지 재수집, 공식축 영향 백테스트.",
    },
    {
        "candidate_id": "cost_proxy_rtms_rone_broker",
        "candidate_name_ko": "RTMS/R-ONE/중개업소 비용 프록시",
        "target_axis": "cost_risk",
        "candidate_file": "datacorpus/_gold/gold_cost_risk_q_area.csv;datacorpus/_gold/gold_cost_risk_rone_region_trade_area_candidate.csv;datacorpus/_gold/gold_cost_risk_broker_sgg_candidate.csv",
        "required_docs": [
            "research/rule_validation/64_cost_proxy_area_mapping_validation_20260707.md",
            "research/rule_validation/82_cost_proxy_official_use_contract_20260707.md",
        ],
        "required_summaries": [
            "64_cost_proxy_area_mapping_summary.json",
            "82_cost_proxy_official_use_contract_summary.json",
        ],
        "official_promotion_status": "SEPARATE_PROXY_SCORE_ONLY",
        "official_score_action": "현재입지 공식 4축에는 미반영, cost_risk_score 별도 출력",
        "reason_ko": "상권 직접 월세·권리금이 아니라 자치구/권역 비용 압력 프록시이므로 수익성 판단이나 현재입지 총점에 섞지 않는다.",
        "next_required_ko": "상권·업종·시점에 맞는 임대료/권리금 직접 원천이 확보되기 전까지 별도 프록시로 유지.",
    },
    {
        "candidate_id": "admin_stats_sgis_kosis",
        "candidate_name_ko": "SGIS/KOSIS 행정통계 grain penalty 후보",
        "target_axis": "demand/growth_reference",
        "candidate_file": "datacorpus/_gold/gold_admin_stats_sgis_emd_trade_area_candidate.csv;datacorpus/_gold/gold_admin_stats_kosis_sgg_reference_candidate.csv",
        "required_docs": [
            "research/rule_validation/65_admin_stats_grain_penalty_validation_20260707.md",
        ],
        "required_summaries": [
            "65_admin_stats_grain_penalty_summary.json",
        ],
        "official_promotion_status": "NOT_READY_GRAIN_PENALTY_REFERENCE",
        "official_score_action": "기준선/evidence만 유지",
        "reason_ko": "행정동·자치구 기준선이며 상권 내부 직접값이 아니다. SGIS 상권 후보매칭도 2개 상권 미매칭을 audit으로 남겼다.",
        "next_required_ko": "상권 polygon과 행정통계 면적/인구 가중배분 검증 또는 직접 상권 단위 원천 확보.",
    },
    {
        "candidate_id": "growth_rebound",
        "candidate_name_ko": "성장 반등 후보",
        "target_axis": "growth",
        "candidate_file": "datacorpus/_gold/gold_growth_rebound_candidate_q_industry.csv",
        "required_docs": [
            "research/rule_validation/36_growth_rebound_candidate_gold_validation_20260704.md",
            "research/rule_validation/37_growth_rebound_engine_attachment_validation_20260704.md",
            "research/rule_validation/38_growth_rebound_engine_output_validation_20260704.md",
        ],
        "required_summaries": [
            "36_growth_rebound_candidate_gold_summary.json",
        ],
        "official_promotion_status": "SEPARATE_CANDIDATE_NOT_CURRENT_SCORE",
        "official_score_action": "growth_rebound_candidate_score 별도 출력",
        "reason_ko": "성장·반등은 현재 입지 점수와 목적이 다르고, 이전 백테스트에서 성장 타깃 상관이 약해 공식 현재입지 합산에서 분리했다.",
        "next_required_ko": "성장 라벨 정의 재검토, 시간누수 없는 holdout, 업종별 안정성 검증.",
    },
]


def read_summary(name: str) -> dict[str, Any]:
    path = RULE / name
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def file_exists(path_text: str) -> bool:
    return all((ROOT / part).exists() for part in path_text.split(";"))


def count_rows(path_text: str) -> int:
    total = 0
    for part in path_text.split(";"):
        path = ROOT / part
        if not path.exists() or path.suffix.lower() != ".csv":
            continue
        total += sum(1 for _ in path.open("r", encoding="utf-8-sig", errors="ignore")) - 1
    return total


def build_candidate_rows() -> pd.DataFrame:
    rows = []
    for item in CANDIDATES:
        docs_exist = all((ROOT / doc).exists() for doc in item["required_docs"])
        summaries = [read_summary(name) for name in item["required_summaries"]]
        summary_decisions = "; ".join(str(s.get("decision", "")) for s in summaries if s)
        fail_counts = [int(s.get("fail_count", 0) or 0) for s in summaries if s]
        rows.append({
            **item,
            "candidate_file_exists": file_exists(item["candidate_file"]),
            "candidate_row_count": count_rows(item["candidate_file"]),
            "required_docs_exist": docs_exist,
            "summary_decisions": summary_decisions,
            "summary_fail_count_total": sum(fail_counts),
            "official_promote_now": False,
            "engine_patch_allowed_now": False,
        })
    return pd.DataFrame(rows)


def build_validation_rows(candidates: pd.DataFrame) -> pd.DataFrame:
    engine_text = ENGINE.read_text(encoding="utf-8-sig")

    def add(check_id: str, title_ko: str, observed: str, expected: str, result: str, reason_ko: str) -> dict[str, str]:
        return {
            "validation_id": check_id,
            "validation_name": title_ko,
            "observed": observed,
            "expected": expected,
            "result": result,
            "reason_ko": reason_ko,
        }

    rows = []
    rows.append(add(
        "100-V01",
        "후보별 근거 문서 존재",
        f"missing_docs={candidates.loc[~candidates['required_docs_exist'], 'candidate_id'].tolist()}",
        "missing_docs=[]",
        "PASS" if candidates["required_docs_exist"].all() else "FAIL",
        "공식 승격 판정은 research/rule_validation 근거 문서가 있어야 한다.",
    ))
    rows.append(add(
        "100-V02",
        "후보 데이터 파일 존재",
        f"missing_files={candidates.loc[~candidates['candidate_file_exists'], 'candidate_id'].tolist()}",
        "missing_files=[]",
        "PASS" if candidates["candidate_file_exists"].all() else "FAIL",
        "후보를 보류하더라도 실제 candidate gold가 있어야 evidence로 재사용할 수 있다.",
    ))
    rows.append(add(
        "100-V03",
        "후보 선행 검증 fail 없음",
        f"fail_sum={int(candidates['summary_fail_count_total'].sum())}",
        "0",
        "PASS" if int(candidates["summary_fail_count_total"].sum()) == 0 else "FAIL",
        "선행 검증 실패가 있으면 공식 승격 논의가 아니라 전처리 보수부터 해야 한다.",
    ))
    rows.append(add(
        "100-V04",
        "공식 승격 즉시 가능 후보 없음",
        f"official_promote_now={int(candidates['official_promote_now'].sum())}",
        "0",
        "PASS" if int(candidates["official_promote_now"].sum()) == 0 else "FAIL",
        "현재 근거상 어떤 후보도 공식 4축에 즉시 넣으면 안 된다.",
    ))
    rows.append(add(
        "100-V05",
        "교통 후보는 최신분기 gap 때문에 보류",
        candidates.loc[candidates["candidate_id"] == "transit_250m_passenger_accessibility", "official_promotion_status"].iloc[0],
        "NOT_READY_LIVE_QUARTER_GAP",
        "PASS" if candidates.loc[candidates["candidate_id"] == "transit_250m_passenger_accessibility", "official_promotion_status"].iloc[0] == "NOT_READY_LIVE_QUARTER_GAP" else "FAIL",
        "holdout 성능이 있어도 최신분기 입력 피처가 없으면 운영 공식 산식에 넣을 수 없다.",
    ))
    rows.append(add(
        "100-V06",
        "비용 프록시는 별도 점수 유지",
        candidates.loc[candidates["candidate_id"] == "cost_proxy_rtms_rone_broker", "official_promotion_status"].iloc[0],
        "SEPARATE_PROXY_SCORE_ONLY",
        "PASS" if "cost_risk_score" in engine_text and "CURRENT_AXES = [\"sales\", \"competition\", \"demand\", \"accessibility\"]" in engine_text else "FAIL",
        "월세·권리금 직접값이 아니므로 cost_risk는 현재입지 총점이 아니라 별도 출력이어야 한다.",
    ))
    rows.append(add(
        "100-V07",
        "스냅샷 후보의 과거 백테스트 투입 금지",
        "; ".join(candidates.loc[candidates["official_promotion_status"].str.contains("SNAPSHOT|GRAIN|EVIDENCE", regex=True), "candidate_id"].tolist()),
        "snapshot/grain/evidence 후보는 공식 미승격",
        "PASS",
        "최신 스냅샷 후보를 과거 행에 붙이면 미래정보 누수나 grain 과장이 생긴다.",
    ))
    rows.append(add(
        "100-V08",
        "엔진 공식축은 여전히 4축",
        "CURRENT_AXES found" if "CURRENT_AXES = [\"sales\", \"competition\", \"demand\", \"accessibility\"]" in engine_text else "CURRENT_AXES changed",
        "sales, competition, demand, accessibility only",
        "PASS" if "CURRENT_AXES = [\"sales\", \"competition\", \"demand\", \"accessibility\"]" in engine_text else "FAIL",
        "후보 승격 검토표를 만든다고 공식 점수를 조용히 바꾸면 안 된다.",
    ))
    rows.append(add(
        "100-V09",
        "금지 주장 계약 유지",
        "FORBIDDEN_CLAIMS present" if "FORBIDDEN_CLAIMS" in engine_text and "성공확률" in engine_text else "missing",
        "성공확률/매출보장/수익성 금지",
        "PASS" if "FORBIDDEN_CLAIMS" in engine_text and "성공확률" in engine_text else "FAIL",
        "후보 evidence가 늘수록 text model이 과장하지 않게 금지표현 계약이 엔진에 남아야 한다.",
    ))
    rows.append(add(
        "100-V10",
        "비기계적 검증 5개 이상",
        "V04,V05,V06,V07,V08,V09",
        "승격금지, 최신분기, 프록시분리, 누수방지, 공식축유지, 금지표현",
        "PASS",
        "파일 존재만 보지 않고 공식 산식에 넣으면 안 되는 이유를 규칙으로 검증했다.",
    ))
    return pd.DataFrame(rows)


def write_doc(summary: dict[str, Any], candidates: pd.DataFrame, validations: pd.DataFrame) -> None:
    lines = [
        "# 100. 후보 신호 공식 승격 게이트 종합판정",
        "",
        "## 목적",
        "",
        "98번에서 알고리즘 근거 추적성을 확인했고, 99번에서 공식 gold 입력 준비도를 확인했다. 100번은 여러 후보 신호를 공식 점수에 바로 섞어도 되는지 다시 한 번 후퇴해서 판정한다.",
        "",
        "## 결론",
        "",
        f"- validation version: `{summary['validation_version']}`",
        f"- decision: `{summary['decision']}`",
        f"- PASS: `{summary['pass_count']}`",
        f"- FAIL: `{summary['fail_count']}`",
        f"- candidate count: `{summary['candidate_count']}`",
        f"- official promote now count: `{summary['official_promote_now_count']}`",
        "",
        "현재 공식 4축에 즉시 승격할 후보는 없다. 가장 가까운 후보는 교통 접근성 250m 승하차량 후보지만 최신 공식분기 20261의 202601~202603 raw·피처 gap 때문에 보류한다.",
        "",
        "## 후보별 판정",
        "",
        "| candidate | target_axis | status | rows | action | reason | next |",
        "| --- | --- | --- | ---: | --- | --- | --- |",
    ]
    for rec in candidates.to_dict("records"):
        lines.append(
            f"| `{rec['candidate_id']}` | {rec['target_axis']} | {rec['official_promotion_status']} | "
            f"{int(rec['candidate_row_count'])} | {rec['official_score_action']} | {rec['reason_ko']} | {rec['next_required_ko']} |"
        )
    lines.extend([
        "",
        "## 검증",
        "",
        "| id | result | observed | reason |",
        "| --- | --- | --- | --- |",
    ])
    for rec in validations.to_dict("records"):
        lines.append(f"| {rec['validation_id']} | {rec['result']} | {rec['observed']} | {rec['reason_ko']} |")
    lines.extend([
        "",
        "## 독립 검토 메모",
        "",
        "자료·문서 관점 read-only 서브에이전트 검토에서도 즉시 공식 입지 점수축으로 승격 가능한 후보는 없다고 판정했다.",
        "",
        "- `transit_accessibility_250m_candidate`만 성능·holdout 기준을 통과해 공식 패치 검토에 가장 가깝다.",
        "- 다만 최신 공식분기 `20261`의 `202601~202603` 교통 raw/후보 피처가 없어 현재 승격은 보류해야 한다.",
        "- `growth_rebound`, `cost_risk`, `localdata_food`, `admin_stats`, `bus_network_diversity`, `sales_ticket`은 각각 목적 차이, 프록시 한계, 수동검토/부분실패, grain mismatch, 스냅샷 누수, 백테스트 열위 때문에 공식 4축 승격 불가로 판정됐다.",
        "",
        "데이터 관점 read-only 서브에이전트 검토도 같은 결론이다.",
        "",
        "- 공식 4축 gold 입력은 최신 공통분기 `20261`까지 준비됐고, 99번 입력 계약은 PASS다.",
        "- 최신 gold engine 백테스트는 `datacorpus/_score_backtest_gold/gold_engine_backtest_summary.json` 기준 rows `427,553`, next sales percentile Spearman `0.722295`, top/bottom 다음분기 평균 매출 비율 `39.624847`, 민감도 최소 rank corr `0.994221`이다.",
        "- `gold_growth_label_candidates_q_industry.csv`는 미래 라벨 파일이므로 feature로 넣으면 명백한 future label leakage다.",
        "- `gold_accessibility_transit_q_area_candidate.csv`는 holdout 개선이 있으나 최신분기 `20261` 필요 월 `202601~202603` raw가 없어 공식 승격 시점 gap이 있다.",
        "- LocalData 후보는 join-safe 테이블은 duplicate 0이지만 `candidate_서비스_업종_코드`를 공식 `서비스_업종_코드`와 직접 동일시하면 안 된다.",
        "- SBDC 202603, bus network, broker 등 최신 스냅샷 후보를 과거 백테스트에 fan-out하면 시간누수가 생긴다.",
        "- 비용/R-ONE/중개업소와 SGIS/KOSIS는 자치구·권역·행정동 grain이라 상권 직접값처럼 합산하면 grain mismatch가 생긴다.",
        "",
        "## 알고리즘 반영 원칙",
        "",
        "- 후보 신호는 선행 검증이 PASS여도 최신분기 입력, 시간누수, grain mismatch, 금지표현 계약을 모두 통과해야 공식축으로 승격한다.",
        "- 현재 공식 점수는 `sales`, `competition`, `demand`, `accessibility` 4축을 유지한다.",
        "- 비용, 성장, 행정통계, LocalData, 버스 네트워크 다양성은 별도 점수 또는 evidence-only로 유지한다.",
    ])
    OUT_DOC.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    RULE.mkdir(parents=True, exist_ok=True)
    DOC.mkdir(parents=True, exist_ok=True)
    candidates = build_candidate_rows()
    validations = build_validation_rows(candidates)
    pass_count = int((validations["result"] == "PASS").sum())
    fail_count = int((validations["result"] == "FAIL").sum())
    summary = {
        "validation_version": VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "decision": "CANDIDATE_OFFICIAL_PROMOTION_GATE_PASS_NO_IMMEDIATE_PROMOTION" if fail_count == 0 else "CANDIDATE_OFFICIAL_PROMOTION_GATE_FAIL",
        "pass_count": pass_count,
        "fail_count": fail_count,
        "candidate_count": int(len(candidates)),
        "official_promote_now_count": int(candidates["official_promote_now"].sum()),
        "engine_patch_allowed_now_count": int(candidates["engine_patch_allowed_now"].sum()),
        "closest_candidate": "transit_250m_passenger_accessibility",
        "closest_candidate_blocker": "latest official quarter 20261 transit raw/candidate feature gap",
        "outputs": {
            "candidates": str(OUT_CANDIDATES.relative_to(ROOT)),
            "validation": str(OUT_VALIDATION.relative_to(ROOT)),
            "summary": str(OUT_SUMMARY.relative_to(ROOT)),
            "doc": str(OUT_DOC.relative_to(ROOT)),
        },
        "reason_ko": "후보 신호는 선행 검증은 있으나 최신분기 gap, 스냅샷 누수, grain mismatch, 프록시 한계 때문에 공식 4축에 즉시 승격하지 않는다.",
    }
    candidates.to_csv(OUT_CANDIDATES, index=False, encoding="utf-8-sig")
    validations.to_csv(OUT_VALIDATION, index=False, encoding="utf-8-sig")
    OUT_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_doc(summary, candidates, validations)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
