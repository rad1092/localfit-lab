# -*- coding: utf-8 -*-
"""
75. 전처리 파일 단위 실행계약 생성.

목적:
  - raw ingest 감사표와 source coverage 표를 합쳐 전처리 착수 순서를 고정한다.
  - 원천별로 direct score, proxy, evidence, docs, input bridge, blocked 역할을 분리한다.
  - 모든 데이터를 한 feature mart에 몰아넣지 않고 원천별 silver/gold 경계를 유지한다.

주의:
  - 이 스크립트는 실제 점수 산식을 바꾸지 않는다.
  - 전처리 실행계약은 다음 전처리 파일을 고르기 위한 통제표다.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RULE = ROOT / "datacorpus" / "_rule_validation"
DOC = ROOT / "research" / "rule_validation"

SOURCE_AUDIT = RULE / "69_raw_ingest_source_status_audit.csv"
SOURCE_COVERAGE = RULE / "44_rule_pipeline_source_coverage_audit.csv"
QUEUE_68 = RULE / "68_post67_preprocessing_algorithm_next_queue.csv"

OUT_CONTRACT = RULE / "75_preprocessing_file_execution_contract.csv"
OUT_VALIDATION = RULE / "75_preprocessing_file_execution_contract_validation.csv"
OUT_SUMMARY = RULE / "75_preprocessing_file_execution_contract_summary.json"
OUT_DOC = DOC / "75_preprocessing_file_execution_contract_20260707.md"

VERSION = "preprocessing_file_execution_contract.v0.1-20260707"


DIRECT_SCORE_SOURCES = {
    "seoul_sales_trade_area",
    "seoul_store_trade_area",
    "seoul_floating_population_trade_area",
    "seoul_resident_worker_population_trade_area",
    "seoul_facility_trade_area",
    "seoul_trade_area_change_index",
}

INPUT_BRIDGE_SOURCES = {
    "seoul_trade_area_boundary",
    "vworld_juso_geocoding",
    "juso_address_normalization",
    "sbdc_store_info_docs",
}

DOC_SOURCE_KEYWORDS = {
    "docs",
    "notice",
    "manual",
}


def read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, encoding="utf-8-sig")


def write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")


def write_json(data: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=json_default), encoding="utf-8")


def json_default(value: Any) -> Any:
    if hasattr(value, "item"):
        return value.item()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if pd.isna(value):
        return None
    return str(value)


def clean_text(value: Any, fallback: str = "") -> str:
    if value is None or pd.isna(value):
        return fallback
    text = str(value).strip()
    return text if text and text.lower() != "nan" else fallback


def is_doc_source(source_id: str, coverage_row: pd.Series | None) -> bool:
    if any(keyword in source_id for keyword in DOC_SOURCE_KEYWORDS):
        return True
    if coverage_row is not None:
        return clean_text(coverage_row.get("score_use_level")) == "docs"
    return False


def classify_source(row: pd.Series, coverage_row: pd.Series | None) -> dict[str, Any]:
    source_id = str(row["source_id"])
    status = clean_text(row.get("preprocessing_status"))
    failed_rows = int(row.get("failed_rows", 0) or 0)
    coverage_score_use = clean_text(coverage_row.get("score_use_level")) if coverage_row is not None else ""
    coverage_pipeline_status = clean_text(coverage_row.get("pipeline_status")) if coverage_row is not None else ""
    current_gold = clean_text(coverage_row.get("current_gold_tables")) if coverage_row is not None else ""
    current_silver = clean_text(coverage_row.get("reliability_silver_tables")) if coverage_row is not None else ""
    coverage_reason = clean_text(coverage_row.get("reason_ko")) if coverage_row is not None else ""
    coverage_next = clean_text(coverage_row.get("next_action_ko")) if coverage_row is not None else ""
    coverage_guard = clean_text(coverage_row.get("forbidden_claim_guard_ko")) if coverage_row is not None else ""

    blocked = status.startswith("blocked")
    docs_only = is_doc_source(source_id, coverage_row)
    direct_score_allowed = source_id in DIRECT_SCORE_SOURCES and not blocked
    input_bridge = source_id in INPUT_BRIDGE_SOURCES and not blocked

    if blocked:
        engine_role = "blocked"
        layer_target = "blocked_until_retry_or_manual_decision"
        output_group = "none"
        reason = "성공 원천이 없거나 실패 전용 probe라 전처리 입력으로 쓰지 않는다."
        next_action = "재시도하거나 수동 보류 사유를 확정하기 전까지 silver/gold를 만들지 않는다."
        guard = "실패 원천을 근거값으로 사용하지 않는다."
    elif docs_only:
        engine_role = "docs_only"
        layer_target = "research_basis"
        output_group = "docs_contract"
        reason = coverage_reason or "문서 원천은 API/데이터 설명 근거로만 사용한다."
        next_action = coverage_next or "관련 실데이터 source contract가 바뀔 때 함께 갱신한다."
        guard = coverage_guard or "문서 자체를 점수 원천값으로 쓰지 않는다."
    elif direct_score_allowed:
        engine_role = "direct_score_input"
        layer_target = "silver_to_gold_to_engine"
        output_group = "official_score_gold"
        reason = coverage_reason or "서울 상권분석서비스 상권×분기 또는 상권×업종×분기 직접 원천이다."
        next_action = coverage_next or "기존 silver/gold 검증을 유지하고 알고리즘 payload에서 필요한 축만 읽는다."
        guard = coverage_guard or "점수는 상권 비교용이며 창업 성공확률이나 매출 보장을 의미하지 않는다."
    elif input_bridge:
        engine_role = "input_resolver"
        layer_target = "input_lookup_gold"
        output_group = "location_or_industry_input"
        reason = coverage_reason or "사용자 입력을 상권코드 또는 서비스업종코드로 확정하기 위한 브리지다."
        next_action = coverage_next or "다중 후보면 엔진 호출을 막고 단일 코드 확정 시에만 payload로 넘긴다."
        guard = coverage_guard or "입력 resolver는 점수 품질 보장이 아니라 잘못된 조인 방지 장치다."
    elif coverage_score_use in {"proxy", "reference", "hold", "bridge"} or "candidate" in current_gold or "후보" in coverage_pipeline_status:
        engine_role = "evidence_candidate"
        layer_target = "silver_to_candidate_gold"
        output_group = "candidate_evidence_payload"
        reason = coverage_reason or "공식 점수 직접값이 아니라 후보 evidence 또는 프록시로만 사용할 수 있다."
        next_action = coverage_next or "공식 점수 승격 전 별도 백테스트와 시간누수 검증을 거친다."
        guard = coverage_guard or "프록시를 실제 방문자, 실제 이동시간, 월세/권리금 직접값, 수익성 보장으로 말하지 않는다."
    else:
        engine_role = "reference_or_support"
        layer_target = "source_specific_silver_or_reference_gold"
        output_group = "supporting_reference"
        reason = coverage_reason or "보조 원천으로 원천별 silver/gold 경계를 유지해야 한다."
        next_action = coverage_next or "공식 점수 산식에 넣기 전에 역할과 grain을 별도 검증한다."
        guard = coverage_guard or "보조 원천을 직접 점수 근거처럼 단정하지 않는다."

    if failed_rows > 0 and not blocked:
        next_action = f"{next_action} 실패 기록 {failed_rows}건은 별도 감사표와 함께 유지한다."

    return {
        "source_id": source_id,
        "provider": clean_text(row.get("provider")),
        "preprocessing_status": status,
        "manifest_rows": int(row.get("manifest_rows", 0) or 0),
        "ready_rows": int(row.get("ready_rows", 0) or 0),
        "failed_rows": failed_rows,
        "source_kind": coverage_score_use or ("docs" if docs_only else "unclassified"),
        "engine_role": engine_role,
        "preprocessing_layer_target": layer_target,
        "planned_output_group": output_group,
        "direct_score_allowed": bool(direct_score_allowed),
        "evidence_only": bool(engine_role == "evidence_candidate"),
        "blocked": bool(blocked),
        "single_feature_mart_forbidden": True,
        "current_silver_tables": current_silver,
        "current_gold_tables": current_gold,
        "basis_pipeline_status": coverage_pipeline_status,
        "reason_ko": reason,
        "next_action_ko": next_action,
        "forbidden_claim_guard_ko": guard,
    }


def build_contract() -> pd.DataFrame:
    audit = read_csv(SOURCE_AUDIT).sort_values("source_id").reset_index(drop=True)
    coverage = read_csv(SOURCE_COVERAGE) if SOURCE_COVERAGE.exists() else pd.DataFrame()
    coverage_map = {str(row["source_id"]): row for _, row in coverage.iterrows()} if not coverage.empty else {}
    rows = [classify_source(row, coverage_map.get(str(row["source_id"]))) for _, row in audit.iterrows()]
    return pd.DataFrame(rows).sort_values(["blocked", "engine_role", "source_id"]).reset_index(drop=True)


def add_validation(rows: list[dict[str, Any]], check_id: str, item: str, observed: Any, expected: Any, passed: bool, reason_ko: str) -> None:
    rows.append(
        {
            "check_id": check_id,
            "item": item,
            "observed": json.dumps(observed, ensure_ascii=False, default=json_default) if isinstance(observed, (list, dict)) else observed,
            "expected": json.dumps(expected, ensure_ascii=False, default=json_default) if isinstance(expected, (list, dict)) else expected,
            "pass": bool(passed),
            "reason_ko": reason_ko,
        }
    )


def validate_contract(contract: pd.DataFrame) -> pd.DataFrame:
    audit = read_csv(SOURCE_AUDIT)
    queue = read_csv(QUEUE_68) if QUEUE_68.exists() else pd.DataFrame()
    rows: list[dict[str, Any]] = []

    audit_sources = set(audit["source_id"].astype(str))
    contract_sources = set(contract["source_id"].astype(str))
    add_validation(rows, "75-V01", "69번 원천 감사 source 전체 포함", sorted(audit_sources - contract_sources), [], audit_sources == contract_sources, "전처리 계약은 원천 감사표의 모든 source를 빠뜨리면 안 된다.")

    required_cols = ["engine_role", "preprocessing_layer_target", "planned_output_group", "reason_ko", "next_action_ko", "forbidden_claim_guard_ko"]
    missing_cells = {
        col: contract.loc[contract[col].astype(str).str.strip().eq("") | contract[col].isna(), "source_id"].tolist()
        for col in required_cols
    }
    add_validation(rows, "75-V02", "필수 설명 칸 누락 없음", missing_cells, "모든 필수 칸 채움", all(len(v) == 0 for v in missing_cells.values()), "계약표는 이유, 다음 행동, 금지 해석을 source별로 가져야 한다.")

    blocked_bad = contract[(contract["blocked"]) & ((contract["direct_score_allowed"]) | (contract["preprocessing_layer_target"] != "blocked_until_retry_or_manual_decision"))]["source_id"].tolist()
    add_validation(rows, "75-V03", "blocked source 사용 금지", blocked_bad, [], len(blocked_bad) == 0, "성공 원천이 없는 probe 실패는 전처리 입력이나 점수 근거로 쓰면 안 된다.")

    docs_bad = contract[(contract["engine_role"] == "docs_only") & (contract["direct_score_allowed"])]["source_id"].tolist()
    add_validation(rows, "75-V04", "문서 source 직접 점수 금지", docs_bad, [], len(docs_bad) == 0, "문서 원천은 방법론/계약 근거이지 수치 점수 입력이 아니다.")

    direct_sources = set(contract.loc[contract["direct_score_allowed"], "source_id"].astype(str))
    add_validation(rows, "75-V05", "직접 점수 허용 source 제한", sorted(direct_sources), sorted(DIRECT_SCORE_SOURCES), direct_sources == DIRECT_SCORE_SOURCES, "공식 직접 점수는 서울 상권분석 직접 원천으로만 제한한다.")

    evidence_bad = contract[(contract["evidence_only"]) & (contract["direct_score_allowed"])]["source_id"].tolist()
    add_validation(rows, "75-V06", "evidence-only와 direct 동시 true 금지", evidence_bad, [], len(evidence_bad) == 0, "후보 evidence는 공식 점수 입력과 같은 지위를 가지면 안 된다.")

    mart_bad = contract[~contract["single_feature_mart_forbidden"]]["source_id"].tolist()
    add_validation(rows, "75-V07", "단일 feature mart 회귀 금지", mart_bad, [], len(mart_bad) == 0, "모든 원천은 원천별 silver/gold 경계를 유지해야 한다.")

    failed_untracked = contract[(contract["failed_rows"] > 0) & (~contract["next_action_ko"].astype(str).str.contains("실패 기록|재시도|보류", regex=True))]["source_id"].tolist()
    add_validation(rows, "75-V08", "실패 기록 유지", failed_untracked, [], len(failed_untracked) == 0, "부분 실패 원천은 실패를 숨기지 않고 다음 행동에 남겨야 한다.")

    bridge_required = {"seoul_trade_area_boundary", "juso_address_normalization", "vworld_juso_geocoding"}
    bridge_sources = set(contract.loc[contract["engine_role"] == "input_resolver", "source_id"].astype(str))
    add_validation(rows, "75-V09", "입력 resolver source 포함", sorted(bridge_required - bridge_sources), [], bridge_required.issubset(bridge_sources), "지도 클릭/주소/상권 입력은 코드 확정 브리지를 가져야 한다.")

    queue_has_contract_item = False
    if not queue.empty:
        queue_has_contract_item = queue["work_item"].astype(str).str.contains("전처리 파일 단위 실행 순서와 알고리즘 payload v2 계약 확정", regex=False).any()
    add_validation(rows, "75-V10", "68번 큐 6순위 이행", queue_has_contract_item, True, queue_has_contract_item, "75번 계약은 68번 큐의 전처리 파일 단위 실행계약 항목을 실제 산출물로 만든다.")

    return pd.DataFrame(rows)


def build_report(contract: pd.DataFrame, validations: pd.DataFrame, summary: dict[str, Any]) -> str:
    role_counts = contract["engine_role"].value_counts().to_dict()
    status_counts = contract["preprocessing_status"].value_counts().to_dict()
    lines: list[str] = [
        "# 75. 전처리 파일 단위 실행계약",
        "",
        f"- 작성일: {datetime.now().strftime('%Y-%m-%d')}",
        f"- 버전: `{VERSION}`",
        "",
        "## 목적",
        "",
        "전처리 착수 전에 원천별 역할, 산출물 계층, 직접 점수 허용 여부, 후보 evidence 여부, 보류 여부를 한 표로 고정했다. 이 계약은 이후 silver/gold 파일을 하나씩 만들 때 기준표로 쓴다.",
        "",
        "## 입력 근거",
        "",
        "- `datacorpus/_rule_validation/69_raw_ingest_source_status_audit.csv`",
        "- `datacorpus/_rule_validation/44_rule_pipeline_source_coverage_audit.csv`",
        "- `datacorpus/_rule_validation/68_post67_preprocessing_algorithm_next_queue.csv`",
        "- `research/전처리_전_작업확인메모_20260707.md`",
        "",
        "## 요약",
        "",
        f"- contract rows: {summary['contract_rows']}",
        f"- direct score sources: {summary['direct_score_source_count']}",
        f"- evidence-only sources: {summary['evidence_only_source_count']}",
        f"- blocked sources: {summary['blocked_source_count']}",
        f"- PASS: {summary['pass_count']}",
        f"- FAIL: {summary['fail_count']}",
        f"- decision: `{summary['decision']}`",
        "",
        "## source 역할 분포",
        "",
    ]
    for key, value in sorted(role_counts.items()):
        lines.append(f"- `{key}`: {value}")
    lines.extend(["", "## 전처리 상태 분포", ""])
    for key, value in sorted(status_counts.items()):
        lines.append(f"- `{key}`: {value}")
    lines.extend(
        [
            "",
            "## 고정한 규칙",
            "",
            "- 서울 상권분석서비스의 직접 원천만 공식 direct score 입력으로 둔다.",
            "- RTMS, R-ONE, SGIS, KOSIS, SBDC, LocalData, 교통 후보는 별도 승격 전까지 evidence/proxy/reference로 둔다.",
            "- 문서 source는 방법론과 계약 근거이지 점수값이 아니다.",
            "- blocked source는 재시도 또는 수동 결정 전까지 silver/gold를 만들지 않는다.",
            "- 모든 source는 단일 feature mart로 합치지 않고 원천별 silver/gold 또는 evidence payload로 분리한다.",
            "- 실패 기록이 있는 source는 실패를 숨기지 않고 다음 행동에 남긴다.",
            "",
            "## 검증표",
            "",
            "| check | 항목 | 결과 | 이유 |",
            "|---|---|---|---|",
        ]
    )
    for _, row in validations.iterrows():
        result = "PASS" if bool(row["pass"]) else "FAIL"
        lines.append(f"| {row['check_id']} | {row['item']} | {result} | {row['reason_ko']} |")
    lines.extend(
        [
            "",
            "## 다음 전처리 사용법",
            "",
            "다음부터는 이 계약표에서 `engine_role=direct_score_input`인 source를 공식 점수축 gold로 먼저 검산하고, `engine_role=evidence_candidate`인 source는 별도 후보 payload로만 검산한다. blocked source와 docs source는 점수 산식에 들어가지 않는다.",
            "",
            "## 2보 전진 1보 후퇴",
            "",
            "1. 전진: 원천 감사표와 source coverage를 합쳐 원천별 실행계약을 만들었다.",
            "2. 전진: direct/evidence/docs/input/blocked 역할을 한 표에서 확인하게 만들었다.",
            "3. 후퇴: 후보 evidence와 proxy를 공식 점수 입력으로 승격하지 않았다.",
            "4. 후퇴: 모든 데이터를 한 파일에 몰아넣는 feature mart 방식으로 돌아가지 않았다.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    contract = build_contract()
    validations = validate_contract(contract)
    pass_count = int(validations["pass"].sum())
    fail_count = int((~validations["pass"]).sum())
    summary = {
        "version": VERSION,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "contract_rows": int(len(contract)),
        "direct_score_source_count": int(contract["direct_score_allowed"].sum()),
        "evidence_only_source_count": int(contract["evidence_only"].sum()),
        "blocked_source_count": int(contract["blocked"].sum()),
        "engine_role_counts": contract["engine_role"].value_counts().to_dict(),
        "preprocessing_status_counts": contract["preprocessing_status"].value_counts().to_dict(),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "decision": "PREPROCESSING_FILE_EXECUTION_CONTRACT_PASS" if fail_count == 0 else "PREPROCESSING_FILE_EXECUTION_CONTRACT_FAIL",
    }
    write_csv(contract, OUT_CONTRACT)
    write_csv(validations, OUT_VALIDATION)
    write_json(summary, OUT_SUMMARY)
    OUT_DOC.parent.mkdir(parents=True, exist_ok=True)
    OUT_DOC.write_text(build_report(contract, validations, summary), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=json_default))
    if fail_count:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
