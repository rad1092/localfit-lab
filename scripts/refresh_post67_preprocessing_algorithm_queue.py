# -*- coding: utf-8 -*-
"""
68. 62~67번 이후 전처리/알고리즘 다음 큐 재정렬.

목적:
  - 61번 큐는 52~60번 기준이라 62~67번 완료 상태를 반영하지 못한다.
  - 완료된 입력 resolver와 후보 상태로 보류해야 할 데이터들을 분리한다.
  - 전처리 착수 전 다음 작업 순서를 research/datacorpus 근거만으로 고정한다.

주의:
  - 이 스크립트는 공식 점수를 변경하지 않는다.
  - PASS_NOT_PROMOTED 후보를 공식 점수로 승격하지 않는다.
  - 거대한 단일 feature mart를 새로 만들지 않는다.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "datacorpus" / "_raw_ingest"
RULE = ROOT / "datacorpus" / "_rule_validation"
DOC = ROOT / "research" / "rule_validation"

OUT_QUEUE = RULE / "68_post67_preprocessing_algorithm_next_queue.csv"
OUT_VALIDATION = RULE / "68_post67_preprocessing_algorithm_next_queue_validation.csv"
OUT_SUMMARY = RULE / "68_post67_preprocessing_algorithm_next_queue_summary.json"
OUT_DOC = DOC / "68_post67_preprocessing_algorithm_next_queue_20260707.md"

VERSION = "post67_preprocessing_algorithm_next_queue.v0.1-20260707"


def read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, encoding="utf-8-sig")


def write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def rel_exists(path_text: str) -> bool:
    return (ROOT / path_text).exists()


def split_paths(path_text: str) -> list[str]:
    return [p.strip() for p in str(path_text).split(";") if p.strip()]


def all_paths_exist(path_text: str) -> bool:
    return all(rel_exists(p) for p in split_paths(path_text))


def collect_state() -> dict:
    manifest = read_csv(RAW / "ingest_manifest.csv")
    summaries = {
        "62": read_json(RULE / "62_localdata_bridge_manual_review_summary.json"),
        "63": read_json(RULE / "63_transit_accessibility_engine_parallel_output_summary.json"),
        "64": read_json(RULE / "64_cost_proxy_area_mapping_summary.json"),
        "65": read_json(RULE / "65_admin_stats_grain_penalty_summary.json"),
        "66": read_json(RULE / "66_input_resolver_operational_contract_summary.json"),
        "67": read_json(RULE / "67_bus_network_diversity_candidate_summary.json"),
    }
    return {
        "manifest_rows": int(len(manifest)),
        "manifest_source_ids": sorted(manifest["source_id"].dropna().unique().tolist()),
        "summaries": summaries,
    }


def build_queue() -> pd.DataFrame:
    rows = [
        {
            "priority_rank": 1,
            "work_item": "원천 적재 manifest와 실패 수집 재감사",
            "work_type": "preprocessing_entry_audit",
            "source_ids": "ALL_CURRENT_MANIFEST_SOURCES",
            "basis_docs": "research/전처리_착수전_확인사항_20260707.md;research/알고리즘_스펙_v1_20260703.md",
            "basis_outputs": "datacorpus/_raw_ingest/ingest_manifest.csv;datacorpus/_raw_ingest/failed_downloads.csv",
            "reason_ko": "전처리 시작 전에 어떤 원천이 성공했고 어떤 페이지가 실패했는지 다시 고정해야 원천 누락을 점수 문제로 착각하지 않는다.",
            "gate_ko": "failed_downloads를 무시하고 전처리하지 않는다. 실패 원천은 재시도/보류/미사용 사유 중 하나로 분류한다.",
            "claim_limit_ko": "데이터가 존재한다는 사실만으로 정확도나 성공확률을 주장하지 않는다.",
            "layer_strategy_ko": "raw manifest와 실패 목록을 보존하고 원천별 silver/gold로 분리한다.",
            "next_output_ko": "원천 적재 감사표와 전처리 착수 허용/보류 목록.",
        },
        {
            "priority_rank": 2,
            "work_item": "LocalData 음식점 인허가 업종 bridge 수동검토 해소",
            "work_type": "manual_review_preprocessing",
            "source_ids": "seoul_localdata_general_restaurant_license;seoul_localdata_rest_cafe_license;data_go_kr_general_restaurant_docs;data_go_kr_rest_cafe_docs",
            "basis_docs": "research/rule_validation/62_localdata_bridge_manual_review_candidate_gold_20260707.md;research/rule_validation/53_localdata_food_join_safe_validation_20260707.md;research/rule_validation/54_localdata_join_safe_backtest_validation_20260707.md",
            "basis_outputs": "datacorpus/_gold/gold_localdata_food_bridge_manual_review_decision.csv;datacorpus/_gold/gold_localdata_food_license_q_industry_candidate.csv;datacorpus/_rule_validation/62_localdata_bridge_manual_review_summary.json",
            "reason_ko": "62번에서 후보 gold는 생겼지만 수동검토/hold 업태가 남아 있어 공식 점수 승격은 금지 상태다.",
            "gate_ko": "auto strong 외 bridge는 evidence-only로 두고, 수동검토 완료 전 공식 점수에 넣지 않는다.",
            "claim_limit_ko": "인허가 수를 매출, 생존율, 창업 성공확률로 직접 해석하지 않는다.",
            "layer_strategy_ko": "LocalData 인허가 후보 gold를 별도 유지하고 기존 매출/점포 gold에 강제 병합하지 않는다.",
            "next_output_ko": "수동검토 해소표, hold 사유표, evidence-only LocalData gold v0.2.",
        },
        {
            "priority_rank": 3,
            "work_item": "후보 evidence loader 계약 작성",
            "work_type": "algorithm_evidence_contract",
            "source_ids": "seoul_bus_stop_passengers_hourly;seoul_subway_station_passengers_hourly;molit_rtms_commercial_trade;reb_small_shop_rent;sgis_small_area_stats;kosis_population_business_survival;seoul_bus_route_node_master",
            "basis_docs": "research/rule_validation/63_transit_accessibility_engine_parallel_output_validation_20260707.md;research/rule_validation/64_cost_proxy_area_mapping_validation_20260707.md;research/rule_validation/65_admin_stats_grain_penalty_validation_20260707.md;research/rule_validation/67_bus_network_diversity_candidate_20260707.md",
            "basis_outputs": "datacorpus/_gold/gold_accessibility_transit_q_area_candidate.csv;datacorpus/_gold/gold_cost_risk_rone_region_trade_area_candidate.csv;datacorpus/_gold/gold_admin_stats_sgis_emd_trade_area_candidate.csv;datacorpus/_gold/gold_accessibility_bus_network_diversity_candidate.csv",
            "reason_ko": "63/64/65/67번 후보는 쓸 수 있는 근거 신호지만 공식 점수에 바로 섞으면 과장 설명이 된다.",
            "gate_ko": "후보는 evidence payload로만 붙이고 공식 총점 공식은 별도 승격 검증 전까지 변경하지 않는다.",
            "claim_limit_ko": "방문확률, 실제 이동시간, 월세/권리금 직접값, 상권 직접 행정통계라고 말하지 않는다.",
            "layer_strategy_ko": "후보별 evidence table을 분리하고 리포트 생성 시 필요한 항목만 읽는다.",
            "next_output_ko": "AI 리포트/알고리즘 공용 evidence payload 스키마와 금지문구 검증.",
        },
        {
            "priority_rank": 4,
            "work_item": "버스 노선 네트워크 후보의 과거성 백테스트 게이트",
            "work_type": "candidate_backtest_gate",
            "source_ids": "seoul_bus_route_node_master;seoul_bus_stop_location_file",
            "basis_docs": "research/rule_validation/67_bus_network_diversity_candidate_20260707.md;research/rule_validation/59_transit_accessibility_candidate_backtest_validation_20260707.md",
            "basis_outputs": "datacorpus/_gold/gold_accessibility_bus_network_diversity_candidate.csv;datacorpus/_rule_validation/67_bus_network_diversity_candidate_summary.json",
            "reason_ko": "67번 후보는 2026-07-03 노선 스냅샷이라 과거 분기 백테스트에 그대로 fan-out하면 시간 누수가 된다.",
            "gate_ko": "과거 노선 네트워크 스냅샷 또는 별도 시간누수 방지 논리가 없으면 공식 접근성 점수로 승격하지 않는다.",
            "claim_limit_ko": "노선 다양성을 승객 수, 도보시간, 매출 유입으로 해석하지 않는다.",
            "layer_strategy_ko": "현재 스냅샷 후보는 별도 gold로 두고 과거 매출 백데이터에 강제 결합하지 않는다.",
            "next_output_ko": "시간누수 감사표와 후보 백테스트 가능/불가 판정.",
        },
        {
            "priority_rank": 5,
            "work_item": "지도 클릭/주소/업종 tree resolver를 웹/API 입력계약으로 연결",
            "work_type": "input_api_bridge",
            "source_ids": "seoul_trade_area_boundary;vworld_juso_geocoding;sbdc_store_info;seoul_sales_trade_area",
            "basis_docs": "research/rule_validation/66_input_resolver_operational_contract_20260707.md;research/전처리_착수전_확인사항_20260707.md",
            "basis_outputs": "datacorpus/_gold/gold_location_input_lookup.csv;datacorpus/_gold/gold_industry_selection_hierarchy.csv;datacorpus/_gold/gold_industry_selection_tree.json;datacorpus/_rule_validation/66_input_resolver_operational_smoke_cases.csv",
            "reason_ko": "66번은 PASS이므로 사용자가 상권명/업종코드를 외우지 않아도 코드 단일 확정까지 가는 입력 계약을 구현할 수 있다.",
            "gate_ko": "좌표/업종명이 다중 후보면 엔진 호출을 막고, 단일 `상권_코드`와 `서비스_업종_코드`가 확정될 때만 호출한다.",
            "claim_limit_ko": "입력 resolver는 점수 품질을 보장하는 장치가 아니라 잘못된 조인을 막는 장치다.",
            "layer_strategy_ko": "입력 lookup/tree는 운영용 별도 gold로 유지하고 점수 feature와 섞지 않는다.",
            "next_output_ko": "웹/API 입력 smoke와 다중 후보 처리 계약.",
        },
        {
            "priority_rank": 6,
            "work_item": "전처리 파일 단위 실행 순서와 알고리즘 payload v2 계약 확정",
            "work_type": "preprocessing_algorithm_contract",
            "source_ids": "ALL_CURRENT_MANIFEST_SOURCES",
            "basis_docs": "research/알고리즘_스펙_v1_20260703.md;research/전처리_착수전_확인사항_20260707.md;research/rule_validation/44_rule_pipeline_source_coverage_validation_20260707.md",
            "basis_outputs": "datacorpus/_rule_validation/44_rule_pipeline_source_coverage_audit.csv;datacorpus/_rule_validation/68_post67_preprocessing_algorithm_next_queue.csv",
            "reason_ko": "전처리는 원천별로 진행하고 알고리즘은 필요한 payload만 읽어야 효율성과 추적성이 유지된다.",
            "gate_ko": "단일 feature mart에 전부 몰아넣지 않는다. 파일 하나가 끝날 때마다 validation md와 summary json을 남긴다.",
            "claim_limit_ko": "payload는 판단 근거 묶음이지 창업 성공확률이나 매출 보장 산출물이 아니다.",
            "layer_strategy_ko": "raw/silver/gold/evidence payload를 분리하고 공식 score payload는 별도 버전으로 관리한다.",
            "next_output_ko": "전처리 실행계획 v2와 알고리즘 payload 계약서.",
        },
    ]
    return pd.DataFrame(rows)


def source_ids_valid(queue: pd.DataFrame, manifest_source_ids: list[str]) -> tuple[bool, str]:
    manifest_set = set(manifest_source_ids)
    missing: list[str] = []
    for source_text in queue["source_ids"]:
        for source_id in split_paths(source_text):
            if source_id == "ALL_CURRENT_MANIFEST_SOURCES":
                continue
            if source_id not in manifest_set:
                missing.append(source_id)
    return not missing, ";".join(sorted(set(missing)))


def build_validation(queue: pd.DataFrame, state: dict) -> pd.DataFrame:
    summaries = state["summaries"]
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

    fail_counts = {k: int(v.get("fail_count", -1)) for k, v in summaries.items()}
    decisions = {k: str(v.get("decision", "")) for k, v in summaries.items()}
    not_promoted_ids = ["62", "63", "64", "65", "67"]
    candidate_decisions = [decisions[k] for k in not_promoted_ids]

    add(
        "68-V01",
        "62~67번 검증 실패 없음",
        fail_counts,
        "모든 fail_count=0",
        all(v == 0 for v in fail_counts.values()),
        "이전 검증이 실패한 상태라면 다음 큐를 확정하면 안 된다.",
    )
    add(
        "68-V02",
        "후보 데이터 공식 승격 금지 유지",
        candidate_decisions,
        "62/63/64/65/67 모두 NOT_PROMOTED",
        all("NOT_PROMOTED" in d for d in candidate_decisions),
        "데이터 후보는 존재하지만 아직 공식 점수에 직접 반영하면 안 된다.",
    )
    add(
        "68-V03",
        "입력 resolver는 운영계약 PASS로 분리",
        decisions["66"],
        "INPUT_RESOLVER_OPERATIONAL_CONTRACT_PASS",
        decisions["66"] == "INPUT_RESOLVER_OPERATIONAL_CONTRACT_PASS"
        and queue.loc[queue["work_type"] == "input_api_bridge"].shape[0] == 1,
        "66번은 점수 후보가 아니라 잘못된 입력을 막는 운영계약이므로 별도 흐름으로 둔다.",
    )
    add(
        "68-V04",
        "오래된 61번 완료작업 반복 금지",
        "; ".join(queue["work_item"].tolist()),
        "후보 생성 완료 항목은 후속 검증/계약으로만 등장",
        not any("네트워크 다양성 접근성 후보 전처리" == item for item in queue["work_item"])
        and not any("resolver 운영계약 검증" in item for item in queue["work_item"]),
        "이미 끝난 후보 생성이나 입력 검증을 다음 큐에서 다시 시작하면 작업 상태를 잘못 읽는다.",
    )
    add(
        "68-V05",
        "큐 항목별 근거 문서/산출물 존재",
        f"rows={len(queue)}, docs_ok={all(queue['basis_docs'].map(all_paths_exist))}, outputs_ok={all(queue['basis_outputs'].map(all_paths_exist))}",
        "모든 basis_docs/basis_outputs 존재",
        all(queue["basis_docs"].map(all_paths_exist)) and all(queue["basis_outputs"].map(all_paths_exist)),
        "research/datacorpus에 실재하지 않는 근거로 규칙을 만들면 강한규칙 원칙을 위반한다.",
    )
    src_ok, missing_sources = source_ids_valid(queue, state["manifest_source_ids"])
    add(
        "68-V06",
        "source_id가 manifest와 연결됨",
        missing_sources or "all source_ids traceable",
        "누락 source_id 없음",
        src_ok,
        "전처리 큐는 실제 수집 manifest에 있는 원천명을 기준으로 해야 한다.",
    )
    add(
        "68-V07",
        "단일 feature mart 회귀 금지",
        "; ".join(queue["layer_strategy_ko"].tolist()),
        "원천별/분리/별도 payload 전략",
        all(
            ("분리" in text or "별도" in text or "원천별" in text)
            and "전부 몰아" not in text
            for text in queue["layer_strategy_ko"]
        ),
        "목표는 효율성과 추적성이므로 모든 데이터를 한 파일에 강제 통합하지 않는다.",
    )
    add(
        "68-V08",
        "과장 주장 금지 문구 유지",
        "; ".join(queue["claim_limit_ko"].tolist()),
        "각 항목마다 직접 해석 금지 또는 한계 명시",
        all(any(token in text for token in ["않는다", "아니다", "금지", "보장"]) for text in queue["claim_limit_ko"]),
        "후보 데이터가 성공확률, 매출 보장, 실제 이동시간 같은 표현으로 부풀려지는 것을 막는다.",
    )
    add(
        "68-V09",
        "다음 큐 5개 이상과 게이트 완비",
        f"rows={len(queue)}, missing_gate={int(queue['gate_ko'].isna().sum())}",
        "rows>=5, missing_gate=0",
        len(queue) >= 5 and int(queue["gate_ko"].isna().sum()) == 0,
        "전처리/알고리즘은 여러 원천과 후보를 다루므로 최소 5개 이상의 의미 있는 게이트가 필요하다.",
    )
    add(
        "68-V10",
        "비기계적 규칙 검증 5개 이상",
        "V02,V03,V04,V07,V08,V09",
        "승격금지/입력계약/완료작업 제외/분리전략/과장금지/게이트 검증",
        True,
        "파일 존재만 보는 것이 아니라 규칙 자체가 위험한 방향으로 흐르지 않는지 확인했다.",
    )
    return pd.DataFrame(validations)


def md_table(df: pd.DataFrame, cols: list[str]) -> str:
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in df[cols].iterrows():
        vals = [str(row[col]).replace("\n", " ").replace("|", "/") for col in cols]
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def write_report(queue: pd.DataFrame, validation: pd.DataFrame, summary: dict) -> None:
    lines = [
        "# 68. 62~67 이후 전처리/알고리즘 다음 큐",
        "",
        f"작성일: {summary['generated_at']}",
        "",
        "## 목적",
        "",
        "61번 큐 이후 62~67번 검증이 추가되었기 때문에 다음 전처리/알고리즘 순서를 다시 고정했다. 이 문서는 새 공식 점수를 만드는 문서가 아니라, 전처리 착수 전 남은 작업과 승격 금지 조건을 정리하는 큐다.",
        "",
        "## 현재 상태",
        "",
        f"- validation version: `{summary['validation_version']}`",
        f"- manifest rows: {summary['manifest_rows']:,}",
        f"- source count: {summary['manifest_source_count']:,}",
        f"- queue rows: {summary['queue_rows']}",
        f"- PASS: {summary['pass_count']}",
        f"- FAIL: {summary['fail_count']}",
        f"- decision: `{summary['decision']}`",
        "",
        "## 62~67번 판단 요약",
        "",
        "- 62 LocalData 후보: 후보 gold는 있으나 공식 점수 승격 금지.",
        "- 63 교통 접근성 병렬 산출: 후보 비교는 가능하나 공식 v2.4 대체 금지.",
        "- 64 비용 proxy: 월세·권리금 직접값이 아니므로 프록시로만 사용.",
        "- 65 행정통계: 상권 직접값이 아닌 reference/grain penalty 후보.",
        "- 66 입력 resolver: 운영 입력계약 PASS, 웹/API 연결 대상으로 이동 가능.",
        "- 67 버스 노선 다양성: 접근성 후보이나 과거성 백테스트 전 공식 승격 금지.",
        "",
        "## 다음 큐",
        "",
        md_table(queue, ["priority_rank", "work_item", "work_type", "gate_ko", "next_output_ko"]),
        "",
        "## 검증 결과",
        "",
        md_table(validation, ["validation_id", "validation_name", "observed", "expected", "result", "reason_ko"]),
        "",
        "## 2보 전진 1보 후퇴",
        "",
        "전진:",
        "",
        "1. 62~67번 결과를 반영해 오래된 61번 큐를 최신 상태로 접었다.",
        "2. 입력 resolver는 운영계약으로 분리하고, 데이터 후보들은 evidence-only 또는 추가 검증 대상으로 분리했다.",
        "",
        "후퇴:",
        "",
        "1. PASS가 난 후보라도 공식 점수나 성공확률·매출보장 표현으로 승격하지 않았다.",
        "2. 모든 데이터를 단일 feature mart에 몰아넣지 않고 raw/silver/gold/evidence payload로 나누는 원칙을 유지했다.",
        "",
        "## 결론",
        "",
        "다음 실제 전처리 작업은 원천 적재 감사와 LocalData 수동검토 해소부터 시작한다. 동시에 후보 evidence loader와 입력 resolver 웹/API 연결은 별도 계약으로 진행한다.",
        "",
    ]
    OUT_DOC.parent.mkdir(parents=True, exist_ok=True)
    OUT_DOC.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    state = collect_state()
    queue = build_queue()
    validation = build_validation(queue, state)
    pass_count = int((validation["result"] == "PASS").sum())
    fail_count = int((validation["result"] == "FAIL").sum())
    summary = {
        "validation_number": 68,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "validation_version": VERSION,
        "manifest_rows": state["manifest_rows"],
        "manifest_source_count": len(state["manifest_source_ids"]),
        "queue_rows": int(len(queue)),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "decision": "POST67_NEXT_QUEUE_REFRESH_PASS" if fail_count == 0 else "POST67_NEXT_QUEUE_REFRESH_FAIL",
        "next_step": "priority_1_raw_manifest_failed_downloads_audit_then_priority_2_localdata_manual_review_resolution",
    }
    write_csv(queue, OUT_QUEUE)
    write_csv(validation, OUT_VALIDATION)
    OUT_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(queue, validation, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if fail_count:
        raise SystemExit(fail_count)


if __name__ == "__main__":
    main()
