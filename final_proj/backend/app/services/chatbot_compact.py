from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from app.ai.recursive_layer import get_llm


class CompactLLMResponse(BaseModel):
    answer: str = Field(description="사용자에게 보여줄 자연스러운 한국어 요약 답변")


class ConversationLLMResponse(BaseModel):
    answer: str = Field(description="사용자의 실제 질문에 먼저 답하는 자연스러운 한국어 상담 답변")
    suggested_questions: list[str] = Field(
        default_factory=list,
        description="방금 답변에서 자연스럽게 이어갈 수 있는 짧은 후속 질문 0~3개",
    )


def _display(metric: Any) -> str:
    if isinstance(metric, dict):
        return str(metric.get("display_grade") or metric.get("grade") or metric.get("display") or "")
    return str(metric or "")


def _grade_display(metric: Any) -> str:
    if isinstance(metric, dict):
        candidates = (metric.get("display_grade"), metric.get("grade"))
    else:
        candidates = (metric,)
    for candidate in candidates:
        match = re.fullmatch(r"([A-E])\s*(\+)?(?:\s*등급)?", str(candidate or "").strip().upper())
        if match:
            return f"{match.group(1)}{match.group(2) or ''}"
    return ""


def compact_template(facts_lite_display: dict[str, Any]) -> str:
    grade = _grade_display(facts_lite_display.get("score")) or _grade_display(facts_lite_display.get("display_grade")) or "등급 확인 중"
    percentile = _display(facts_lite_display.get("percentile")) or "동일 업종 내 위치 확인 중"
    sales = _display(facts_lite_display.get("sales")) or "매출 근거 없음"
    stores = _display(facts_lite_display.get("same_industry_store_count")) or "경쟁 점포 수 확인 중"
    cost = _display(facts_lite_display.get("cost_indicator")) or "비용 지표 없음"
    alt = facts_lite_display.get("top_alternative") or {}
    alt_name = alt.get("area_name") if isinstance(alt, dict) else ""
    alt_grade = _grade_display((alt.get("current_location_score") or {}) if isinstance(alt, dict) else {})
    alternative = f"비교 후보로는 {alt_name}({alt_grade or '등급 확인 중'})도 같이 볼 만합니다." if alt_name else "비교 후보는 상세 리포트에서 다시 확인할 수 있어요."
    return (
        f"현재 조건의 입지 등급은 {grade}이고, {percentile}입니다. "
        f"매출 근거는 {sales}, 경쟁 근거는 {stores}로 잡혔어요. "
        f"비용 쪽은 {cost}라서 실제 임대료와 권리금은 현장에서 따로 확인해야 합니다. "
        f"{alternative}"
    )


def _conversation_fallback(question: str) -> tuple[str, list[str]]:
    compact = "".join(question.lower().split())
    if any(keyword in compact for keyword in ("돈", "예산", "자본", "소자본", "투자금")):
        return (
            "자본이 적다면 점포부터 계약하기보다 작게 시험할 수 있는 업종부터 보는 편이 맞아요. "
            "초기 시설비와 재고 부담이 낮고, 혼자 운영할 수 있으며, 주문을 받은 뒤 생산하는 서비스형·예약형 모델이 상대적으로 유리합니다. "
            "반대로 큰 매장, 많은 장비, 상시 재고가 필요한 업종은 매출이 나기 전부터 현금이 묶이기 쉬워요. "
            "먼저 내가 이미 할 수 있는 일과 감당 가능한 월 고정비를 정한 뒤, 온라인 판매나 공유공간·팝업처럼 작은 방식으로 수요를 확인해보는 게 현실적입니다.",
            ["내가 가진 기술로 시작할 수 있는 업종은 뭐가 있을까?", "월 고정비는 어느 정도까지 잡아야 할까?"],
        )
    if any(keyword in compact for keyword in ("안녕", "반가워", "고마워")):
        return (
            "반가워요. 아직 조건을 정하지 않았어도 괜찮아요. 지금 떠오르는 고민부터 말해주면 업종, 비용, 상권 중 필요한 부분만 같이 풀어볼게요.",
            ["적은 돈으로 시작하려면 뭘 먼저 봐야 해?", "내 경험에 맞는 업종은 어떻게 찾을까?"],
        )
    return (
        "좋아요. 조건을 다 정한 뒤에 묻지 않아도 괜찮아요. 지금 질문에서 가장 중요한 선택 기준부터 나누고, 필요한 경우에만 상권이나 업종 데이터를 더해 구체화해볼게요.",
        ["선택지를 비교할 때 가장 먼저 볼 기준은 뭐야?", "내 상황에 맞는 업종부터 같이 좁혀볼까?"],
    )


def answer_conversation(
    question: str,
    history_tail: list[dict[str, str]],
    *,
    state: dict[str, Any] | None = None,
    area_facts: dict[str, Any] | None = None,
    facts_lite_display: dict[str, Any] | None = None,
    facts_pack_display: dict[str, Any] | None = None,
) -> tuple[str, list[str], list[str], bool]:
    known_state = {
        key: value
        for key, value in (state or {}).items()
        if value not in (None, "", "미입력") and key != "last_report_id"
    }
    context = {
        "known_condition": known_state,
        "selected_area_facts": area_facts or {},
        "last_report_facts": {
            "summary": facts_lite_display or {},
            "detail": facts_pack_display or {},
        },
    }
    history_text = "\n".join(f"{item.get('role', 'user')}: {item.get('content', '')}" for item in history_tail[-8:])

    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                (
                    "너는 대화를 통해 창업 고민을 함께 풀어가는 '입지봇'이다. 가장 중요한 규칙은 사용자가 실제로 물은 질문에 먼저 답하는 것이다. "
                    "상권·업종·예산이 비어 있어도 일반적인 창업 원칙, 선택지, 현실적인 실행 순서는 충분히 설명한다. 입력 폼처럼 조건부터 요구하지 않는다. "
                    "사용자가 리포트나 보고서를 명시적으로 요청하지 않았다면 리포트 생성, 리포트 부재, 보고서 필요성을 먼저 언급하지 않는다. "
                    "최근 대화를 읽고 '그거', '그러면', '나는' 같은 표현의 맥락을 이어간다. "
                    "답변은 항상 친근한 존댓말인 해요체를 사용한다. 사용자가 반말로 말해도 '너', '네가' 같은 호칭이나 반말로 따라 하지 않는다. "
                    "선택 상권 DB나 직전 리포트 facts가 있으면 질문과 직접 관련된 값만 사용한다. 숫자·순위·출처는 제공된 facts에 있는 것만 말하고, "
                    "입지 판단값은 제공된 A+~E 등급만 말하며 내부 숫자 점수를 만들거나 노출하지 않는다. "
                    "일반적인 조언과 데이터에 근거한 판단을 섞어 단정하지 않는다. 내부 코드, 테이블명, 모델명은 노출하지 않는다. "
                    "소자본 질문에서는 어떤 업종도 무조건 쉽거나 싸다고 단정하지 않는다. 보증금·시설비·재고·인건비·필요 기술·허가를 나눠 설명하고, "
                    "무인점포도 보증금과 장비·재고·관리비가 드는 점을 고려한다. 가능하면 점포 계약 전 예약판매·출장·공유공간·온라인 판매처럼 작은 검증 방법을 먼저 제시한다. "
                    "답변은 첫 문장에서 방향을 분명히 말하고, 이유와 현실적인 다음 행동을 3~6문장, 최대 두 개의 짧은 단락으로 설명한다. "
                    "작은 채팅창에서 읽기 좋도록 700자 안팎을 넘기지 않고, 필요한 경우에만 3개 이하의 짧은 목록을 쓴다. "
                    "마지막에는 답변을 반복하지 않는 실제 후속 질문을 0~3개 제안한다. 각 질문은 사용자가 버튼을 눌러 그대로 말할 수 있는 사용자 관점의 의문문이어야 한다. "
                    "'궁금해?', '해줄게', '말해줘도 돼'처럼 상담자가 사용자에게 되묻거나 약속하는 말투는 쓰지 않는다. 사용자가 묻지 않은 리포트 생성 문구도 제안하지 않는다."
                ),
            ),
            (
                "user",
                "최근 대화:\n{history}\n\n현재 사용할 수 있는 선택적 컨텍스트:\n{context}\n\n사용자 질문:\n{question}",
            ),
        ]
    )
    chain = prompt | get_llm().with_structured_output(ConversationLLMResponse, method="function_calling")
    try:
        response = chain.invoke(
            {
                "history": history_text or "(없음)",
                "context": json.dumps(context, ensure_ascii=False, default=str, indent=2),
                "question": question,
            }
        )
        suggestions: list[str] = []
        for item in response.suggested_questions:
            value = " ".join(str(item).split()).strip()
            if value and value not in suggestions and value != question.strip():
                suggestions.append(value)
        return response.answer.strip(), suggestions[:3], [], True
    except Exception as exc:
        fallback, suggestions = _conversation_fallback(question)
        return fallback, suggestions, [str(exc)], False


def generate_compact_response(facts_lite_display: dict[str, Any]) -> tuple[str, list[str], bool]:
    fallback = compact_template(facts_lite_display)
    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                (
                    "너는 창업 입지 상담 챗봇이다. 제공된 facts_lite 값만 근거로 3~5문장의 자연스러운 한국어 요약을 만든다. "
                    "입지 판단은 제공된 A+~E 등급만 말하고 내부 숫자 점수는 노출하지 않는다. "
                    "성공 보장, 매출 보장, 확률 단정은 금지한다. facts에 없는 수치는 쓰지 않는다."
                ),
            ),
            ("user", "facts_lite_display:\n{facts_lite_display}"),
        ]
    )
    chain = prompt | get_llm().with_structured_output(CompactLLMResponse, method="function_calling")
    try:
        response = chain.invoke({"facts_lite_display": facts_lite_display})
        return response.answer.strip(), [], True
    except Exception as exc:
        return fallback, [str(exc)], False
