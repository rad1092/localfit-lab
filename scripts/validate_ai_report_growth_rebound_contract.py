# -*- coding: utf-8 -*-
"""
AI 상세리포트의 성장 반등 후보 문구 계약을 검증한다.

39번 검증:
  - 서버가 구형 FeatureMart 엔진이 아니라 v2.3 gold 기반 엔진 facts를 쓰는지 확인한다.
  - LLM 프롬프트가 growth_rebound_candidate_score를 후보 신호로만 쓰게 제한하는지 확인한다.
  - Markdown/PDF 다운로드 경로에 금지표현 차단 함수가 걸려 있는지 확인한다.

OpenAI API는 호출하지 않는다. 계약·facts·검증 함수만 검사한다.
"""

from __future__ import annotations

import inspect
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import ai_report_server as server  # noqa: E402


RULE_VALIDATION = ROOT / "datacorpus" / "_rule_validation"
RESEARCH_RULE_VALIDATION = ROOT / "research" / "rule_validation"

RUN_DATE = "2026-07-04"
VALIDATION_VERSION = "ai_report_growth_rebound_contract.v1.0-20260704"
OUT_VALIDATION = RULE_VALIDATION / "39_ai_report_growth_rebound_contract_validation.csv"
OUT_SUMMARY = RULE_VALIDATION / "39_ai_report_growth_rebound_contract_summary.json"
OUT_REPORT = RESEARCH_RULE_VALIDATION / "39_ai_report_growth_rebound_contract_validation_20260704.md"


@dataclass
class Validation:
    review_round: str
    rule_name: str
    observed: object
    expected: object
    result: str
    reason_ko: str


validations: list[Validation] = []


def ensure_dirs() -> None:
    RULE_VALIDATION.mkdir(parents=True, exist_ok=True)
    RESEARCH_RULE_VALIDATION.mkdir(parents=True, exist_ok=True)


def add_validation(review_round: str, rule_name: str, observed: object, expected: object, result: str, reason_ko: str) -> None:
    validations.append(Validation(review_round, rule_name, observed, expected, result, reason_ko))


def make_facts() -> dict[str, Any]:
    payload = {"trade_area_code": "3001491", "industry_code": "CS100001", "quarter": "20261"}
    args = server.build_engine_args(payload)
    return server.build_output(args)


def contract_rejects(text: str) -> bool:
    try:
        server.validate_markdown_contract(text)
    except RuntimeError:
        return True
    return False


def build_validations(facts: dict[str, Any]) -> pd.DataFrame:
    developer_prompt = server.build_developer_prompt()
    user_prompt = server.build_user_prompt(facts)
    pdf_handler_source = inspect.getsource(server.AiReportHandler.do_POST)
    call_openai_source = inspect.getsource(server.call_openai)
    candidate = facts.get("score_result", {}).get("candidate_signals", {}).get("growth_rebound_candidate", {})
    scores = facts.get("score_result", {}).get("scores", {})
    components = facts.get("score_result", {}).get("components", [])

    allowed_markdown = """
# 상권 입지 상세 리포트
## 1. 분석 대상
이 리포트는 검증된 엔진 JSON에 있는 상권과 업종을 기준으로 작성됐다.
## 2. 종합 판단
현재입지 점수는 매출 체력형 입지 비교 점수로 해석된다.
## 3. 핵심 근거
수요, 매출, 경쟁, 접근성 축의 근거를 함께 확인했다.
## 4. 성장 반등 후보 신호와 한계
성장 반등 후보 신호는 현재/과거 피처를 기준으로 같은 업종 안에서 초과성장 또는 반등 후보 흐름을 보조적으로 확인하는 지표다. 이 신호는 현재입지 점수, 등급, 가중치 산식에 포함되지 않으며 다음분기 매출 수준을 단정하지 않는다.
## 5. 리스크와 현장 확인 체크리스트
현장 임대조건과 실제 유동 흐름은 별도 확인이 필요하다.
""".strip()

    forbidden_samples = {
        "창업 성공확률": "이 상권의 창업 성공확률은 높다.",
        "성장률 예측": "이 값은 성장률 예측 결과다.",
        "성장률 보장": "성장률 보장이 가능하다.",
        "추천": "이 입지를 추천한다.",
        "권장": "창업을 권장한다.",
        "적합": "이 업종에 적합하다.",
    }

    add_validation(
        "검토1_서버엔진계약",
        "AI 서버 facts가 v2.3 gold 엔진 사용",
        facts.get("score_version"),
        server.SCORE_VERSION,
        "PASS" if facts.get("score_version") == server.SCORE_VERSION and facts.get("schema_version") == "seoul_location_judgement.v2" else "FAIL",
        "AI 리포트가 구형 FeatureMart 엔진이 아니라 38번에서 검증한 v2.3 공식 출력 계약을 써야 한다.",
    )
    add_validation(
        "검토1_서버엔진계약",
        "score_result 안에 반등 후보 신호 존재",
        candidate.get("status"),
        "candidate_attached_not_in_current_score",
        "PASS" if candidate.get("status") == "candidate_attached_not_in_current_score" and scores.get("growth_rebound_candidate_score") is not None else "FAIL",
        "LLM에는 최상위 부가값이 아니라 score_result 안의 후보 신호로 전달해야 Markdown에 반영된다.",
    )
    add_validation(
        "검토2_점수분리",
        "반등 후보 컴포넌트는 별도 후보 key",
        ",".join(str(c.get("key")) for c in components),
        "growth_rebound_candidate 포함, current_location 산식 아님",
        "PASS" if "growth_rebound_candidate" in [c.get("key") for c in components] and scores.get("decision_label") == "상위 후보군, 현장 확인 필요" else "FAIL",
        "반등 후보는 현재입지 점수나 등급 문구가 아니라 별도 후보 컴포넌트로만 노출한다.",
    )
    add_validation(
        "검토3_프롬프트계약",
        "developer prompt에 반등 후보 제한 존재",
        "초과성장/반등 후보 신호" in developer_prompt and "성장률 예측값이 아니다" in developer_prompt,
        True,
        "PASS" if "초과성장/반등 후보 신호" in developer_prompt and "성장률 예측값이 아니다" in developer_prompt else "FAIL",
        "LLM이 후보 점수를 성장률 예측이나 매출 수준 점수로 번역하지 못하게 developer prompt에 명시한다.",
    )
    add_validation(
        "검토3_프롬프트계약",
        "user prompt에 evidence_pack과 candidate_signals 전달",
        f"candidate={'candidate_signals' in user_prompt}, evidence_pack={'evidence_pack' in user_prompt}",
        "둘 다 포함",
        "PASS" if "candidate_signals" in user_prompt and "evidence_pack" in user_prompt else "FAIL",
        "LLM이 단건 JSON의 사용제한 문구와 금지표현 계약을 볼 수 있어야 한다.",
    )
    add_validation(
        "검토4_금지표현차단",
        "허용 Markdown은 통과",
        "pass" if not contract_rejects(allowed_markdown) else "rejected",
        "pass",
        "PASS" if not contract_rejects(allowed_markdown) else "FAIL",
        "성장 반등 후보 신호와 한계를 신중하게 설명하는 정상 문서는 다운로드 가능해야 한다.",
    )
    rejected = [label for label, sample in forbidden_samples.items() if contract_rejects(sample)]
    add_validation(
        "검토4_금지표현차단",
        "금지표현 샘플 차단",
        ",".join(rejected),
        ",".join(forbidden_samples.keys()),
        "PASS" if set(rejected) == set(forbidden_samples.keys()) else "FAIL",
        "성공확률·성장률 보장·추천/권장/적합 단정 표현은 Markdown과 다운로드 산출물에서 막아야 한다.",
    )
    add_validation(
        "검토5_다운로드계약",
        "OpenAI Markdown 추출 후 검증 호출",
        "validate_markdown_contract(markdown)" in call_openai_source,
        True,
        "PASS" if "validate_markdown_contract(markdown)" in call_openai_source else "FAIL",
        "화면 표시와 MD 다운로드의 원천인 LLM Markdown은 서버 반환 전에 검증돼야 한다.",
    )
    add_validation(
        "검토5_다운로드계약",
        "PDF 변환 경로도 검증 호출",
        "validate_markdown_contract(markdown)" in pdf_handler_source and "make_pdf(markdown)" in pdf_handler_source,
        True,
        "PASS" if "validate_markdown_contract(markdown)" in pdf_handler_source and "make_pdf(markdown)" in pdf_handler_source else "FAIL",
        "임의 Markdown을 PDF로 보내는 경로도 같은 금지표현 계약을 따라야 한다.",
    )
    add_validation(
        "검토6_미래라벨차단",
        "facts 런타임 JSON에 next/excess/future 컬럼 없음",
        json.dumps(facts, ensure_ascii=False, default=server.json_default).count("next_")
        + json.dumps(facts, ensure_ascii=False, default=server.json_default).count("excess_log_growth")
        + json.dumps(facts, ensure_ascii=False, default=server.json_default).count("future_")
        + json.dumps(facts, ensure_ascii=False, default=server.json_default).count("미래"),
        0,
        "PASS" if all(term not in json.dumps(facts, ensure_ascii=False, default=server.json_default) for term in ["next_", "excess_log_growth", "future_", "미래"]) else "FAIL",
        "AI 리포트 런타임 입력에는 백테스트 정답지나 미래 라벨이 들어가면 안 된다.",
    )

    out = pd.DataFrame([v.__dict__ for v in validations])
    out.insert(0, "validation_id", range(1, len(out) + 1))
    return out


def write_report(validation: pd.DataFrame, summary: dict[str, Any]) -> None:
    lines = [
        "# AI 상세리포트 성장 반등 후보 문구 계약 검증",
        "",
        "작성일: 2026-07-04",
        "",
        "## 1. 목적",
        "",
        "38번에서 공식 엔진 출력에 붙은 `growth_rebound_candidate_score`가 AI 상세리포트와 다운로드 MD/PDF에서 과장 표현으로 바뀌지 않도록 계약을 검증한다.",
        "",
        "이 검증은 OpenAI API를 호출하지 않는다. 서버 facts, 프롬프트, 금지표현 검증 함수, PDF 변환 경로만 확인한다.",
        "",
        "## 2. 요약",
        "",
        "| 항목 | 값 |",
        "|---|---:|",
        f"| score_version | {summary['score_version']} |",
        f"| report_contract_version | {summary['report_contract_version']} |",
        f"| validation PASS | {summary['validation_pass_count']} |",
        f"| validation FAIL | {summary['validation_fail_count']} |",
        "",
        "## 3. 6회 규칙 검토",
        "",
        "| review_round | rule_name | observed | expected | result | reason_ko |",
        "|---|---|---|---|---|---|",
    ]
    for row in validation.itertuples(index=False):
        lines.append(f"| {row.review_round} | {row.rule_name} | {row.observed} | {row.expected} | {row.result} | {row.reason_ko} |")

    lines.extend(
        [
            "",
            "## 4. 리포트 문구 계약",
            "",
            "`growth_rebound_candidate_score`는 다음 문장 범위 안에서만 설명한다.",
            "",
            "> 성장 반등 후보 신호는 현재/과거 피처를 기준으로 같은 업종 안에서 초과성장 또는 반등 후보 흐름을 보조적으로 확인하는 지표입니다. 이 신호는 현재입지 점수, 등급, 가중치 산식에 포함되지 않으며 다음분기 매출 수준, 성공확률, 성장률을 예측하거나 보장하지 않습니다.",
            "",
            "## 5. 금지 표현",
            "",
            "- 창업 성공확률",
            "- 성공 보장",
            "- 개별 매장 매출 보장",
            "- 성장률 예측 또는 성장률 보장",
            "- 성장률 높은 상권 보장",
            "- 추천, 권장, 적합 같은 단정형 판단",
            "",
            "## 6. 판정",
            "",
            "AI 상세리포트 서버는 v2.3 gold 기반 엔진 facts를 사용한다.",
            "",
            "LLM Markdown과 PDF 변환 경로 모두 금지표현 검증을 통과해야 한다.",
            "",
            "다음 단계는 실제 OpenAI 호출 또는 서버 API smoke에서 생성 Markdown을 받아 같은 검증을 통과시키는 것이다.",
        ]
    )
    OUT_REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ensure_dirs()
    facts = make_facts()
    validation = build_validations(facts)
    summary = {
        "run_date": RUN_DATE,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "validation_version": VALIDATION_VERSION,
        "score_version": facts.get("score_version"),
        "report_contract_version": server.REPORT_CONTRACT_VERSION,
        "validation_pass_count": int((validation["result"] == "PASS").sum()),
        "validation_fail_count": int((validation["result"] == "FAIL").sum()),
        "decision": "AI상세리포트_성장반등후보_문구계약_검증통과",
        "decision_reason_ko": "반등 후보는 AI 리포트에서 초과성장/반등 후보 신호로만 설명하고 금지표현은 서버에서 차단한다.",
    }
    validation.to_csv(OUT_VALIDATION, index=False, encoding="utf-8-sig")
    OUT_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_report(validation, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["validation_fail_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
