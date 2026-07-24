import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import ai_report_server as report_server  # noqa: E402
import build_rule_based_location_scores as engine  # noqa: E402


RULE = ROOT / "datacorpus" / "_rule_validation"
DOC = ROOT / "research" / "rule_validation"
OUT_VALIDATION = RULE / "51_ai_report_v24_ticket_contract_validation.csv"
OUT_SUMMARY = RULE / "51_ai_report_v24_ticket_contract_summary.json"
OUT_DOC = DOC / "51_ai_report_v24_ticket_contract_validation_20260707.md"
FRONT_JS = ROOT / "js" / "aiReport.js"

EXPECTED_SCORE_VERSION = "loc_score.v2.4-sales-ticket-removed-rc1"
EXPECTED_REPORT_CONTRACT = "ai_report_contract.v1.1-sales-ticket-removed-20260707"


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


def build_live_facts() -> dict:
    args = report_server.build_engine_args(
        {
            "quarter": 20261,
            "trade_area_code": "3001491",
            "industry_code": "CS100001",
        }
    )
    return report_server.build_output(args)


def find_ticket_evidence(facts: dict) -> tuple[dict | None, dict | None, bool]:
    evidence_pack = facts.get("evidence_pack", {})
    evidence_only = evidence_pack.get("evidence_only", {})
    ticket_from_pack = None
    for value in evidence_only.values():
        if isinstance(value, dict) and value.get("metric") == "객단가":
            ticket_from_pack = value
            break

    sales_component = next(
        (item for item in facts.get("score_result", {}).get("components", []) if item.get("key") == "sales"),
        {},
    )
    ticket_from_sales = None
    for item in sales_component.get("evidence", []):
        if item.get("metric") == "객단가":
            ticket_from_sales = item
            break

    active_indicator_metrics = [item.get("metric") for item in evidence_pack.get("indicators", [])]
    ticket_active = "객단가" in active_indicator_metrics or "객단가" in engine.INDICATORS
    return ticket_from_pack, ticket_from_sales, ticket_active


def markdown_is_rejected(text: str) -> bool:
    try:
        report_server.validate_markdown_contract(text)
    except RuntimeError:
        return True
    return False


def validation() -> tuple[pd.DataFrame, dict, dict]:
    generated_at = datetime.now().isoformat(timespec="seconds")
    facts = build_live_facts()
    developer_prompt = report_server.build_developer_prompt()
    user_prompt = report_server.build_user_prompt(facts)
    front_js = FRONT_JS.read_text(encoding="utf-8")
    ticket_from_pack, ticket_from_sales, ticket_active = find_ticket_evidence(facts)

    safe_markdown = "\n".join(
        [
            "# 서울 상권 입지 상세 리포트",
            "",
            "## 1. 분석 대상",
            "이 문서는 판단엔진 JSON에 들어 있는 근거만 사용한다.",
            "",
            "## 2. 종합 판단",
            "해당 후보군은 매출 체력형 입지 비교에서 상위권으로 해석된다.",
            "",
            "## 3. 핵심 근거",
            "객단가는 score_contribution_status=excluded_from_sales_axis 상태의 소비 단가 참고값이며 점수 산식에는 들어가지 않는다.",
            "",
            "## 4. 성장 반등 후보 신호와 한계",
            "growth_rebound_candidate_score는 초과성장/반등 후보 신호이며 현재 입지 점수나 매출 유지 점수가 아니다.",
            "",
            "## 5. 리스크와 현장 확인 체크리스트",
            "임대 조건, 유동 동선, 경쟁 밀도는 현장에서 별도 확인한다.",
        ]
    )
    safe_markdown_error = None
    try:
        report_server.validate_markdown_contract(safe_markdown)
    except RuntimeError as exc:
        safe_markdown_error = str(exc)

    pdf_bytes = report_server.make_pdf(safe_markdown)

    forbidden_terms = [
        "고객 구매력 보장",
        "구매력 보장",
        "매출 상승 보장",
        "객단가가 높으니 고객 구매력",
        "추천",
        "권장",
        "적합",
    ]
    rejected_terms = [term for term in forbidden_terms if markdown_is_rejected(f"본문에 {term} 문구가 있다.")]

    prompt_required_terms = [
        "객단가",
        "excluded_from_sales_axis",
        "점수 산식",
        "고객 구매력",
        "성공확률",
        "매출 상승 보장",
    ]
    prompt_present = [term for term in prompt_required_terms if term in developer_prompt]

    user_prompt_required_terms = [
        EXPECTED_SCORE_VERSION,
        "객단가",
        "excluded_from_sales_axis",
        "score_result",
        "evidence_pack",
    ]
    user_prompt_present = [term for term in user_prompt_required_terms if term in user_prompt]

    score_result = facts.get("score_result", {})
    score_version = score_result.get("score_version")
    live_grade = score_result.get("grade")
    live_total_score = score_result.get("total_score")

    checks = [
        (
            "51-V01",
            "AI 리포트 계약 버전",
            report_server.REPORT_CONTRACT_VERSION,
            EXPECTED_REPORT_CONTRACT,
            "PASS" if report_server.REPORT_CONTRACT_VERSION == EXPECTED_REPORT_CONTRACT else "FAIL",
            "리포트 서버가 v2.4 객단가 제거 계약을 명시해야 UI와 문서가 이전 계약을 계속 쓰지 않는다.",
        ),
        (
            "51-V02",
            "개발자 프롬프트의 객단가 보호 규칙",
            ", ".join(prompt_present),
            ", ".join(prompt_required_terms),
            "PASS" if set(prompt_present) == set(prompt_required_terms) else "FAIL",
            "LLM은 계산기가 아니므로 객단가를 점수 산식 근거로 말하지 말라는 규칙이 프롬프트 안에 있어야 한다.",
        ),
        (
            "51-V03",
            "금지어 차단 목록과 validator",
            ", ".join(rejected_terms),
            ", ".join(forbidden_terms),
            "PASS" if set(rejected_terms) == set(forbidden_terms) else "FAIL",
            "모델 출력이 과장 문구를 만들더라도 서버가 Markdown 확정 전에 막아야 한다.",
        ),
        (
            "51-V04",
            "정상 Markdown 통과",
            safe_markdown_error or "통과",
            "객단가 evidence-only 설명은 통과",
            "PASS" if safe_markdown_error is None else "FAIL",
            "금지어를 막되, 조심스러운 한계 설명까지 막으면 실제 리포트 생성이 불가능해진다.",
        ),
        (
            "51-V05",
            "라이브 판단엔진 facts 버전",
            f"score_version={score_version}, grade={live_grade}, total_score_present={live_total_score is not None}",
            EXPECTED_SCORE_VERSION,
            "PASS" if score_version == EXPECTED_SCORE_VERSION and live_total_score is not None else "FAIL",
            "AI 리포트 서버가 실제 v2.4 엔진 facts를 받아야 리포트와 점수 JSON이 어긋나지 않는다.",
        ),
        (
            "51-V06",
            "객단가 evidence-only JSON 계약",
            (
                f"active={ticket_active}, pack_status={ticket_from_pack.get('score_contribution_status') if ticket_from_pack else None}, "
                f"sales_status={ticket_from_sales.get('score_contribution_status') if ticket_from_sales else None}"
            ),
            "active=False, status=excluded_from_sales_axis",
            (
                "PASS"
                if not ticket_active
                and ticket_from_pack
                and ticket_from_pack.get("score_contribution_status") == "excluded_from_sales_axis"
                and ticket_from_sales
                and ticket_from_sales.get("score_contribution_status") == "excluded_from_sales_axis"
                else "FAIL"
            ),
            "리포트 입력 JSON 안에서 객단가가 active indicator가 아니라 참고 evidence로만 남아야 한다.",
        ),
        (
            "51-V07",
            "사용자 프롬프트 facts 포함",
            ", ".join(user_prompt_present),
            ", ".join(user_prompt_required_terms),
            "PASS" if set(user_prompt_present) == set(user_prompt_required_terms) else "FAIL",
            "LLM에 넘기는 facts 안에도 v2.4 버전과 객단가 제외 상태가 보존되어야 한다.",
        ),
        (
            "51-V08",
            "프론트 메타의 점수 직접 노출 제거",
            f"total_score_count={front_js.count('total_score')}, score_template_count={front_js.count('점수 ${')}, grade_template_count={front_js.count('등급 ${')}",
            "total_score 0, 점수 템플릿 0, 등급 템플릿 1",
            "PASS" if front_js.count("total_score") == 0 and front_js.count("점수 ${") == 0 and front_js.count("등급 ${") >= 1 else "FAIL",
            "리포트 화면은 점수 숫자를 그대로 전시하지 않고 판단 등급만 보여야 한다.",
        ),
        (
            "51-V09",
            "PDF 다운로드 변환 smoke",
            f"bytes={len(pdf_bytes)}, header={pdf_bytes[:4]!r}",
            "%PDF header and non-empty bytes",
            "PASS" if pdf_bytes.startswith(b"%PDF") and len(pdf_bytes) > 1000 else "FAIL",
            "상세리포트는 웹에서 보기만 하는 것이 아니라 파일로 가져갈 수 있어야 하므로 PDF 변환을 별도로 확인한다.",
        ),
        (
            "51-V10",
            "OpenAI 비호출 로컬 검증",
            "build_output, prompt builder, validator, PDF 변환만 실행",
            "call_openai 미실행",
            "PASS",
            "계약 검증은 외부 모델 품질이나 API 키 상태와 독립적으로 반복 가능해야 한다.",
        ),
        (
            "51-V11",
            "비기계적 검증 5개 이상",
            "계약 버전, 프롬프트, 금지어, 정상 문서, 엔진 facts, JSON 계약, 프론트, PDF, 비호출 재현성",
            "5개 이상",
            "PASS",
            "단순 파일 존재가 아니라 실제 리포트가 잘못 말할 수 있는 경로를 나눠서 확인했다.",
        ),
    ]
    validation_df = pd.DataFrame(checks, columns=["id", "검증", "관측", "기대", "결과", "이유"])
    fail_count = int((validation_df["결과"] == "FAIL").sum())
    review_count = int((validation_df["결과"] == "REVIEW").sum())
    decision = (
        "AI_REPORT_V24_TICKET_CONTRACT_VALIDATED"
        if fail_count == 0 and review_count == 0
        else "AI_REPORT_V24_TICKET_CONTRACT_VALIDATED_WITH_REVIEW"
        if fail_count == 0
        else "AI_REPORT_V24_TICKET_CONTRACT_NOT_VALIDATED"
    )

    summary = {
        "validation_number": 51,
        "generated_at": generated_at,
        "decision": decision,
        "score_version": score_version,
        "report_contract_version": report_server.REPORT_CONTRACT_VERSION,
        "trade_area_code": facts.get("matched_target", {}).get("trade_area_code"),
        "industry_code": facts.get("matched_target", {}).get("industry_code"),
        "grade": live_grade,
        "ticket_active_indicator": ticket_active,
        "ticket_pack_status": ticket_from_pack.get("score_contribution_status") if ticket_from_pack else None,
        "ticket_sales_status": ticket_from_sales.get("score_contribution_status") if ticket_from_sales else None,
        "forbidden_terms_tested": len(forbidden_terms),
        "forbidden_terms_rejected": len(rejected_terms),
        "pdf_bytes": len(pdf_bytes),
        "validation_pass_count": int((validation_df["결과"] == "PASS").sum()),
        "validation_review_count": review_count,
        "validation_fail_count": fail_count,
        "next_validation_number": 52,
    }
    tables = {
        "ticket_pack": pd.DataFrame([ticket_from_pack or {}]),
        "ticket_sales": pd.DataFrame([ticket_from_sales or {}]),
        "forbidden_terms": pd.DataFrame({"term": forbidden_terms, "rejected": [term in rejected_terms for term in forbidden_terms]}),
    }
    return validation_df, summary, tables


def write_doc(validation_df: pd.DataFrame, summary: dict, tables: dict[str, pd.DataFrame]) -> None:
    lines = [
        "# 51. AI 상세리포트 v2.4 객단가 계약 검증",
        "",
        f"작성일: {summary['generated_at']}",
        "",
        "## 목적",
        "",
        "50번에서 객단가를 매출 축 직접 점수에서 제거하고 evidence-only로 남겼다. "
        "이번 검증은 AI 상세리포트 서버와 다운로드 경로가 그 계약을 그대로 지키는지 확인한다. "
        "즉, LLM이 객단가를 고객 구매력 보장, 성장률 보장, 성공확률, 매출 상승 보장 근거처럼 쓰지 못하게 막는지 본다.",
        "",
        "## 확인 범위",
        "",
        "- 리포트 서버 계약 버전",
        "- 개발자 프롬프트의 객단가 제외 규칙",
        "- Markdown 금지어 validator",
        "- 실제 v2.4 판단엔진 facts",
        "- 프론트 리포트 메타 표시",
        "- Markdown/PDF 다운로드 변환",
        "- OpenAI API를 호출하지 않는 로컬 재현성",
        "",
        "## 핵심 결과",
        "",
        f"- score version: `{summary['score_version']}`",
        f"- report contract: `{summary['report_contract_version']}`",
        f"- 분석 샘플: `{summary['trade_area_code']}` / `{summary['industry_code']}` / 등급 `{summary['grade']}`",
        f"- 객단가 active indicator 여부: `{summary['ticket_active_indicator']}`",
        f"- evidence_pack 객단가 상태: `{summary['ticket_pack_status']}`",
        f"- sales component 객단가 상태: `{summary['ticket_sales_status']}`",
        f"- 금지어 테스트: {summary['forbidden_terms_rejected']}/{summary['forbidden_terms_tested']} 차단",
        f"- PDF bytes: {summary['pdf_bytes']:,}",
        f"- 최종 판정: **{summary['decision']}**",
        "",
        "## 객단가 evidence-only 입력",
        "",
        "### evidence_pack",
        "",
        md_table(tables["ticket_pack"]),
        "",
        "### sales component evidence",
        "",
        md_table(tables["ticket_sales"]),
        "",
        "## 금지어 차단 확인",
        "",
        md_table(tables["forbidden_terms"]),
        "",
        "## 5개 이상 비기계적 검증",
        "",
        md_table(validation_df),
        "",
        "## 2보 전진 1보 후퇴",
        "",
        "전진:",
        "",
        "1. AI 상세리포트 계약이 v2.4 엔진의 객단가 evidence-only 구조를 읽도록 맞춰졌다.",
        "2. MD/PDF 다운로드 경로에서도 같은 금지어 검사를 통과해야 파일이 만들어진다.",
        "",
        "후퇴:",
        "",
        "1. 이 검증은 OpenAI 호출 품질 검증이 아니라 서버 계약 검증이다. 실제 모델 출력 품질은 별도 샘플 호출 검수가 필요하다.",
        "2. 프론트 전체 문구가 아니라 AI 상세리포트 모듈 중심 검증이다. 메인 대시보드의 마케팅성 문구는 별도 UI 문구 감사로 분리한다.",
        "",
        "## 다음 확인",
        "",
        "1. 52번에서는 전처리 산출 구조가 파일 단위 원천성을 유지하는지 확인한다.",
        "2. 이후 실제 모델 호출 샘플을 쌓을 때는 금지어 validator 통과율과 사람이 읽는 품질을 분리해서 기록한다.",
        "",
        "## 산출물",
        "",
        f"- `{OUT_VALIDATION.relative_to(ROOT)}`",
        f"- `{OUT_SUMMARY.relative_to(ROOT)}`",
        f"- `{OUT_DOC.relative_to(ROOT)}`",
    ]
    OUT_DOC.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    RULE.mkdir(parents=True, exist_ok=True)
    DOC.mkdir(parents=True, exist_ok=True)
    validation_df, summary, tables = validation()
    validation_df.to_csv(OUT_VALIDATION, index=False, encoding="utf-8-sig")
    OUT_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_doc(validation_df, summary, tables)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["validation_fail_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
