# -*- coding: utf-8 -*-
"""
74. AI 리포트 서버의 후보 evidence loader 통합 검증.

목적:
  - 서버가 build_output 이후 후보 evidence를 부착하는지 확인한다.
  - 후보 evidence가 붙어도 공식 점수, 등급, 판단 라벨, 산식 근거는 바뀌지 않는지 확인한다.
  - 후보 evidence 금지표현 validator가 서버 경로에서도 계속 작동하는지 확인한다.

주의:
  - 이 검증은 점수 산식을 바꾸는 작업이 아니다.
  - 후보 evidence는 설명 보조 payload이며 공식 점수 근거로 승격하지 않는다.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from ai_report_server import attach_optional_candidate_evidence, validate_markdown_contract  # noqa: E402
from attach_candidate_evidence_loader import official_snapshot  # noqa: E402
from build_rule_based_location_scores import build_output  # noqa: E402
from validate_ai_report_candidate_claims import safe_sample, unsafe_sample, validate_markdown_text  # noqa: E402


RULE = ROOT / "datacorpus" / "_rule_validation"
DOC = ROOT / "research" / "rule_validation"

OUT_VALIDATION = RULE / "74_ai_report_server_candidate_evidence_integration_validation.csv"
OUT_SUMMARY = RULE / "74_ai_report_server_candidate_evidence_integration_summary.json"
OUT_SAMPLE = RULE / "74_ai_report_server_candidate_evidence_integration_sample.json"
OUT_DOC = DOC / "74_ai_report_server_candidate_evidence_integration_20260707.md"

VERSION = "ai_report_server_candidate_evidence_integration.v0.1-20260707"


def json_default(value: Any) -> Any:
    if hasattr(value, "item"):
        return value.item()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if pd.isna(value):
        return None
    return str(value)


def write_json(data: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=json_default), encoding="utf-8")


def write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")


def build_sample_args() -> SimpleNamespace:
    return SimpleNamespace(
        trade_area_code="3001491",
        trade_area_name=None,
        industry_code="CS100001",
        industry_name=None,
        budget_krw=None,
        lat=None,
        lng=None,
        quarter=20261,
        output=None,
    )


def section_payload(facts: dict[str, Any]) -> dict[str, Any]:
    return (
        facts.get("score_result", {})
        .get("candidate_signals", {})
        .get("registry_candidate_evidence_v01", {})
    )


def add_validation(rows: list[dict[str, Any]], check_id: str, item: str, observed: Any, expected: Any, passed: bool, reason_ko: str) -> None:
    rows.append(
        {
            "check_id": check_id,
            "item": item,
            "observed": json.dumps(observed, ensure_ascii=False, default=json_default) if isinstance(observed, (dict, list)) else observed,
            "expected": json.dumps(expected, ensure_ascii=False, default=json_default) if isinstance(expected, (dict, list)) else expected,
            "pass": bool(passed),
            "reason_ko": reason_ko,
        }
    )


def build_report(summary: dict[str, Any], validations: pd.DataFrame) -> str:
    lines: list[str] = [
        "# 74. AI 리포트 서버 후보 evidence 통합 검증",
        "",
        f"- 작성일: {datetime.now().strftime('%Y-%m-%d')}",
        f"- 버전: `{VERSION}`",
        f"- 샘플: 상권 `3001491`, 업종 `CS100001`, 분기 `20261`",
        "",
        "## 목적",
        "",
        "AI 상세리포트 서버가 판단엔진 결과를 만든 뒤 후보 evidence loader를 붙이는지 확인했다. 이 작업은 공식 점수 산식을 바꾸는 것이 아니라, 리포트 설명용 후보 payload를 서버 응답 facts에 포함시키는 통합 검증이다.",
        "",
        "## 검증 결과",
        "",
        f"- PASS: {summary['pass_count']}",
        f"- FAIL: {summary['fail_count']}",
        f"- registry sections: {summary['registry_section_count']}",
        f"- attached sections: {summary['attached_section_count']}",
        f"- total score before: {summary['before_snapshot']['total_score']}",
        f"- total score after: {summary['after_snapshot']['total_score']}",
        f"- grade after: {summary['after_snapshot']['grade']}",
        f"- decision: `{summary['decision']}`",
        "",
        "## 고정한 규칙",
        "",
        "- 서버는 `build_output` 이후에만 후보 evidence를 붙인다.",
        "- 후보 evidence가 붙어도 `total_score`, `grade`, `decision_label`, `score_version`은 바뀌면 안 된다.",
        "- 공식 `components`, `scores`, `matched_target` hash는 바뀌면 안 된다.",
        "- 후보 section은 `score_result.candidate_signals.registry_candidate_evidence_v01` 아래에만 붙는다.",
        "- 후보 payload의 `score_formula_mutation_allowed`는 false여야 한다.",
        "- 후보별 `direct_score_allowed`, `engine_promotion_ready`, `candidate_engine_active`는 true가 되면 안 된다.",
        "- AI 리포트 validator는 후보 payload가 붙은 facts에서도 금지표현을 차단해야 한다.",
        "- 안전한 한계 표현은 통과해야 한다.",
        "",
        "## 검사표",
        "",
        "| check | 항목 | 결과 | 이유 |",
        "|---|---|---|---|",
    ]
    for _, row in validations.iterrows():
        result = "PASS" if bool(row["pass"]) else "FAIL"
        lines.append(f"| {row['check_id']} | {row['item']} | {result} | {row['reason_ko']} |")
    lines.extend(
        [
            "",
            "## 해석",
            "",
            "서버 통합은 후보 evidence를 리포트 facts로 전달하는 단계까지 가능하다고 본다. 다만 이 검증은 후보를 공식 점수에 승격했다는 뜻이 아니다. 후보 evidence는 계속 설명 보조이고, 점수 산식과 판단 라벨은 기존 공식 엔진 결과를 유지한다.",
            "",
            "## 2보 전진 1보 후퇴",
            "",
            "1. 전진: 서버 응답 facts에 후보 evidence loader를 연결했다.",
            "2. 전진: 후보 payload가 붙은 상태에서도 validator가 과장 표현을 차단하는지 확인했다.",
            "3. 후퇴: 공식 점수와 components/scores/matched_target은 전혀 바꾸지 않았다.",
            "4. 후퇴: 후보 evidence를 추천, 적합, 성공확률, 월세/권리금 직접값으로 말하지 못하게 막았다.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    base_facts = build_output(build_sample_args())
    attached_facts = attach_optional_candidate_evidence(base_facts)
    before = official_snapshot(base_facts)
    after = official_snapshot(attached_facts)
    registry = section_payload(attached_facts)
    sections = registry.get("sections", {})
    attached_sections = [name for name, section in sections.items() if section.get("status") == "attached"]

    safe_violations = validate_markdown_text(safe_sample(), facts=attached_facts)
    unsafe_violations = validate_markdown_text(unsafe_sample(), facts=attached_facts)
    server_safe_passed = True
    server_unsafe_blocked = False
    server_unsafe_error = ""
    try:
        validate_markdown_contract(safe_sample(), facts=attached_facts)
    except Exception:
        server_safe_passed = False
    try:
        validate_markdown_contract(unsafe_sample(), facts=attached_facts)
    except Exception as exc:
        server_unsafe_blocked = True
        server_unsafe_error = str(exc)

    rows: list[dict[str, Any]] = []
    add_validation(rows, "74-V01", "서버 부착 contract 존재", bool(attached_facts.get("candidate_evidence_loader_contract")), True, bool(attached_facts.get("candidate_evidence_loader_contract")), "서버 facts에 후보 evidence 계약이 있어야 한다.")
    add_validation(rows, "74-V02", "registry payload 존재", bool(registry), True, bool(registry), "후보 evidence는 candidate_signals 아래 registry payload로 붙어야 한다.")
    add_validation(rows, "74-V03", "registry section 수", len(sections), 7, len(sections) == 7, "71번 registry의 7개 후보 section이 모두 보여야 한다.")
    add_validation(rows, "74-V04", "attached section 수", len(attached_sections), "5개 이상", len(attached_sections) >= 5, "샘플에서 LocalData는 exact 분기 lookup으로 없을 수 있으나 나머지 후보는 붙어야 한다.")
    add_validation(rows, "74-V05", "공식 점수 snapshot 불변", after, before, after == before, "후보 evidence 부착은 공식 점수 snapshot을 바꾸면 안 된다.")
    add_validation(rows, "74-V06", "score_formula_mutation_allowed", registry.get("score_formula_mutation_allowed"), False, registry.get("score_formula_mutation_allowed") is False, "후보 payload가 점수 산식 변경을 허용하면 안 된다.")
    add_validation(rows, "74-V07", "기존 growth 후보 보존", "growth_rebound_candidate" in attached_facts.get("score_result", {}).get("candidate_signals", {}), True, "growth_rebound_candidate" in attached_facts.get("score_result", {}).get("candidate_signals", {}), "기존 후보 신호를 덮어쓰면 안 된다.")
    disallowed_flags: list[dict[str, Any]] = []
    for name, section in sections.items():
        for key in ["direct_score_allowed", "engine_promotion_ready", "candidate_engine_active"]:
            if section.get(key) is True:
                disallowed_flags.append({"section": name, "flag": key})
    add_validation(rows, "74-V08", "후보 승격 금지 플래그", disallowed_flags, [], len(disallowed_flags) == 0, "후보 evidence가 서버 통합 중 공식 점수 후보로 승격되면 안 된다.")
    add_validation(rows, "74-V09", "안전 샘플 violations", len(safe_violations), 0, len(safe_violations) == 0 and server_safe_passed, "신중한 한계 표현은 서버 validator를 통과해야 한다.")
    add_validation(rows, "74-V10", "위반 샘플 차단", {"violations": [v["term"] for v in unsafe_violations], "server_error": server_unsafe_error}, "4개 이상 탐지 및 서버 차단", len(unsafe_violations) >= 4 and server_unsafe_blocked, "과장 표현은 후보 payload가 붙은 서버 검증에서도 차단되어야 한다.")

    validations = pd.DataFrame(rows)
    pass_count = int(validations["pass"].sum())
    fail_count = int((~validations["pass"]).sum())
    summary = {
        "version": VERSION,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "sample_trade_area_code": "3001491",
        "sample_industry_code": "CS100001",
        "sample_quarter": 20261,
        "before_snapshot": before,
        "after_snapshot": after,
        "registry_section_count": int(len(sections)),
        "attached_section_count": int(len(attached_sections)),
        "safe_sample_violation_count": int(len(safe_violations)),
        "unsafe_sample_violation_count": int(len(unsafe_violations)),
        "server_unsafe_blocked": bool(server_unsafe_blocked),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "decision": "AI_REPORT_SERVER_CANDIDATE_EVIDENCE_INTEGRATION_PASS" if fail_count == 0 else "AI_REPORT_SERVER_CANDIDATE_EVIDENCE_INTEGRATION_FAIL",
    }

    sample = {
        "matched_target": attached_facts.get("matched_target"),
        "score_snapshot": after,
        "candidate_evidence_loader_contract": attached_facts.get("candidate_evidence_loader_contract"),
        "registry_candidate_evidence_v01": registry,
    }

    write_csv(validations, OUT_VALIDATION)
    write_json(summary, OUT_SUMMARY)
    write_json(sample, OUT_SAMPLE)
    OUT_DOC.parent.mkdir(parents=True, exist_ok=True)
    OUT_DOC.write_text(build_report(summary, validations), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2, default=json_default))
    if fail_count:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
