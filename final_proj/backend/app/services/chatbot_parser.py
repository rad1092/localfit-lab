from __future__ import annotations

from typing import Any, Literal

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.ai.recursive_layer import get_llm
from app.schemas.commercial_area import ChatOption, ChatState
from app.services.commercial_area import CommercialAreaService


class SlotExtractionResult(BaseModel):
    area_text: str | None = Field(default=None, description="상권명, 지역명, 또는 상권 코드")
    industry_text: str | None = Field(default=None, description="업종명 또는 업종 코드")
    budget: int | None = Field(default=None, description="만원 단위 창업 예산")
    intent: Literal["analyze", "compare", "question", "smalltalk", "chat"] = "chat"
    wants_report: bool = Field(default=False, description="상세 리포트 생성 의도가 명확한지 여부")
    reply: str = Field(default="", description="리포트를 만들지 않을 때 사용할 자연스러운 한국어 답변")


HELP_WORDS = {"help", "도움말", "사용법", "뭐 할 수 있어", "무엇을 할 수 있어"}
INDUSTRY_SYNONYMS = {
    "카페": "커피",
    "커피숍": "커피",
    "커피전문점": "커피",
}
REPORT_WORDS = ("리포트", "보고서", "상세 보고", "상세분석", "상세 분석", "pdf", "PDF")
REPORT_NEGATIONS = (
    "리포트 말고",
    "보고서 말고",
    "리포트 없이",
    "보고서 없이",
    "리포트는 필요 없",
    "보고서는 필요 없",
    "상세 분석 말고",
    "분석하지 말고",
)
ANALYSIS_WORDS = ("어때", "어떤", "설명", "궁금", "봐줘", "볼래", "분석", "추천", "상담", "괜찮")


def is_help_intent(message: str) -> bool:
    normalized = " ".join(message.strip().lower().split())
    return normalized in HELP_WORDS


def _has_report_intent(message: str) -> bool:
    if any(phrase in message for phrase in REPORT_NEGATIONS):
        return False
    return any(word in message for word in REPORT_WORDS)


def extract_slots_llm(message: str, state: ChatState | None = None) -> SlotExtractionResult:
    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                (
                    "너는 서울 상권 입지 상담 챗봇의 대화 관리자다. 사용자의 말에서 상권, 업종, 예산, 의도만 추출한다. "
                    "명확한 단어는 그대로 값을 채우되, 조건이 모두 찼다고 해서 상세 리포트를 바로 만들지는 않는다. "
                    "wants_report는 사용자가 '리포트', '보고서', '상세 분석'처럼 산출물을 명시적으로 요청할 때만 true다. "
                    "그 외에는 먼저 대화형 설명을 하도록 wants_report=false로 둔다. 예산은 만원 단위 정수로 변환한다. "
                    "데이터 수치나 점수는 여기서 꾸며내지 않는다."
                ),
            ),
            (
                "user",
                (
                    "현재 대화 상태:\n{state}\n\n"
                    "사용자 메시지:\n{message}\n\n"
                    "판단 예시:\n"
                    "- '명동 카페 예산 1억으로 볼래' -> area_text=명동, industry_text=카페, budget=10000, intent=analyze, wants_report=false\n"
                    "- '서울역 양식 5억' -> area_text=서울역, industry_text=양식, budget=50000, intent=analyze, wants_report=false\n"
                    "- '명동 카페 1억으로 리포트 만들어줘' -> area_text=명동, industry_text=카페, budget=10000, intent=analyze, wants_report=true\n"
                    "- '이태원 관광특구가 어떤 상권이야?' -> area_text=이태원 관광특구, intent=question, wants_report=false\n"
                    "- '유동인구는 어때?' -> area_text=null, industry_text=null, budget=null, intent=question, wants_report=false\n"
                    "- '안녕', '고마워' -> intent=smalltalk, wants_report=false"
                ),
            ),
        ]
    )
    chain = prompt | get_llm().with_structured_output(SlotExtractionResult, method="function_calling")
    try:
        return chain.invoke({"state": state.model_dump() if state else {}, "message": message})
    except Exception:
        return SlotExtractionResult(
            intent="question" if "?" in message or _has_analysis_intent(message) else "chat",
            wants_report=False,
            reply="",
        )


def _clean_query(query: str | None) -> str:
    return " ".join(str(query or "").strip().split())


def _number_before(text_value: str, marker: str) -> float | None:
    index = text_value.find(marker)
    if index <= 0:
        return None
    chars: list[str] = []
    for char in reversed(text_value[:index]):
        if char.isdigit() or char == ".":
            chars.append(char)
        elif chars:
            break
    if not chars:
        return None
    try:
        return float("".join(reversed(chars)))
    except ValueError:
        return None


def _parse_budget_hint(message: str) -> int | None:
    text_value = message.replace(",", "").replace(" ", "").lower()
    total = 0

    eok = _number_before(text_value, "억")
    if eok is not None:
        total += int(eok * 10000)

    cheon = _number_before(text_value, "천만원")
    if cheon is None:
        cheon = _number_before(text_value, "천만")
    if cheon is not None:
        total += int(cheon * 1000)

    man = _number_before(text_value, "만원")
    if man is not None and total == 0:
        total += int(man)

    if total > 0:
        return total

    compact_digits = "".join(char for char in text_value if char.isdigit())
    if compact_digits and len(compact_digits) >= 4:
        return int(compact_digits)

    return None


def _infer_area_text_from_db(message: str, service: CommercialAreaService) -> str | None:
    rows = service.db.execute(
        text(
            """
            SELECT area_name, display_label
            FROM location_lookup
            ORDER BY LENGTH(area_name) DESC
            """
        )
    ).mappings().all()
    for row in rows:
        area_name = row.get("area_name")
        display_label = row.get("display_label")
        if area_name and area_name in message:
            return str(area_name)
        if display_label and display_label in message:
            return str(display_label)
    return None


def _infer_industry_text_from_db(message: str, service: CommercialAreaService) -> str | None:
    for alias, query in INDUSTRY_SYNONYMS.items():
        if alias in message:
            return query

    rows = service.db.execute(
        text(
            """
            SELECT industry_name
            FROM industry_hierarchy
            ORDER BY LENGTH(industry_name) DESC
            """
        )
    ).mappings().all()
    for row in rows:
        industry_name = row.get("industry_name")
        if industry_name and industry_name in message:
            return str(industry_name)
    return None


def _has_analysis_intent(message: str) -> bool:
    return any(word in message for word in ANALYSIS_WORDS)


def _fill_lookup_hints(
    decision: SlotExtractionResult,
    message: str,
    service: CommercialAreaService,
) -> SlotExtractionResult:
    slot_found = False
    if not decision.area_text:
        decision.area_text = _infer_area_text_from_db(message, service)
        slot_found = bool(decision.area_text) or slot_found
    if not decision.industry_text:
        decision.industry_text = _infer_industry_text_from_db(message, service)
        slot_found = bool(decision.industry_text) or slot_found
    if not decision.budget:
        decision.budget = _parse_budget_hint(message)
        slot_found = bool(decision.budget) or slot_found

    if slot_found and (decision.intent in {"chat", "smalltalk"} or _has_analysis_intent(message)):
        decision.intent = "analyze"

    decision.wants_report = _has_report_intent(message)
    if not decision.wants_report and slot_found:
        decision.reply = ""
    return decision


def find_area_candidates(message: str, service: CommercialAreaService, limit: int = 5) -> list[dict[str, Any]]:
    query = _clean_query(message)
    if not query:
        return []

    if query.isdigit():
        rows = service.db.execute(
            text(
                """
                SELECT area_code, area_name, display_label
                FROM location_lookup
                WHERE area_code = :query
                LIMIT 1
                """
            ),
            {"query": query},
        ).mappings().all()
        if rows:
            return [dict(row) for row in rows]

    rows = service.db.execute(
        text(
            """
            SELECT area_code, area_name, display_label
            FROM location_lookup
            WHERE area_name = :query OR display_label = :query OR search_text = :query
            ORDER BY area_name ASC
            LIMIT :limit
            """
        ),
        {"query": query, "limit": limit},
    ).mappings().all()
    if rows:
        return [dict(row) for row in rows]

    rows = service.db.execute(
        text(
            """
            SELECT area_code, area_name, display_label
            FROM location_lookup
            WHERE area_name LIKE :kw OR display_label LIKE :kw OR search_text LIKE :kw
            ORDER BY
              CASE WHEN area_name LIKE :prefix THEN 0 ELSE 1 END,
              LENGTH(area_name) ASC,
              area_name ASC
            LIMIT :limit
            """
        ),
        {"kw": f"%{query}%", "prefix": f"{query}%", "limit": limit},
    ).mappings().all()
    return [dict(row) for row in rows]


def find_industry_candidates(message: str, service: CommercialAreaService, limit: int = 6) -> list[dict[str, Any]]:
    query = _clean_query(message)
    if not query:
        return []
    for alias, synonym in INDUSTRY_SYNONYMS.items():
        if alias in query:
            query = synonym
            break

    resolved = service.resolve_industry(query)
    if resolved:
        return [
            {
                "industry_code": resolved["industry_code"],
                "industry_name": resolved["industry_name"],
                "selection_path": resolved.get("selection_path"),
            }
        ]

    rows = service.db.execute(
        text(
            """
            SELECT industry_code, industry_name, selection_path
            FROM industry_hierarchy
            WHERE industry_name LIKE :kw OR search_text LIKE :kw
            ORDER BY
              CASE WHEN industry_name LIKE :prefix THEN 0 ELSE 1 END,
              industry_name ASC
            LIMIT :limit
            """
        ),
        {"kw": f"%{query}%", "prefix": f"{query}%", "limit": limit},
    ).mappings().all()
    return [dict(row) for row in rows]


def _missing_slots(state: ChatState) -> list[str]:
    missing = []
    if not state.area_code:
        missing.append("area")
    if not state.industry_code:
        missing.append("industry")
    if not state.budget:
        missing.append("budget")
    return missing


def merge_state(
    message: str,
    service: CommercialAreaService,
    previous: ChatState | None = None,
) -> tuple[ChatState, list[str], list[ChatOption], str | None, SlotExtractionResult]:
    state = previous.model_copy() if previous else ChatState()
    message = message.strip()
    decision = _fill_lookup_hints(extract_slots_llm(message, state), message, service)
    pending_options: list[ChatOption] = []
    pending_slot: str | None = None

    if decision.budget:
        state.budget = decision.budget

    if decision.area_text:
        area_candidates = find_area_candidates(decision.area_text, service)
        if len(area_candidates) == 1:
            selected = area_candidates[0]
            state.area_code = selected["area_code"]
            state.area_name = selected["area_name"]
        elif len(area_candidates) > 1:
            pending_options = [
                ChatOption(
                    label=row.get("display_label") or row["area_name"],
                    type="area",
                    value=row["area_name"],
                    payload={"area_code": row["area_code"], "area_name": row["area_name"]},
                )
                for row in area_candidates
            ]
            pending_slot = "area"

    if decision.industry_text:
        industry_candidates = find_industry_candidates(decision.industry_text, service)
        if len(industry_candidates) == 1:
            selected = industry_candidates[0]
            state.industry_code = selected["industry_code"]
            state.business_type = selected["industry_name"]
        elif len(industry_candidates) > 1:
            options = [
                ChatOption(
                    label=f"{row['industry_name']} ({row['industry_code']})",
                    type="industry",
                    value=row["industry_name"],
                    payload={"industry_code": row["industry_code"], "business_type": row["industry_name"]},
                )
                for row in industry_candidates
            ]
            return state, ["industry"], options, "industry", decision

    missing = _missing_slots(state)
    if pending_options:
        return state, ["area"], pending_options, pending_slot, decision
    return state, missing, [], missing[0] if missing else None, decision


def build_missing_slot_text(state: ChatState, missing: list[str]) -> str:
    known = []
    if state.area_name:
        known.append(f"상권: {state.area_name}")
    if state.business_type:
        known.append(f"업종: {state.business_type}")
    if state.budget:
        known.append(f"예산: {state.budget:,}만원")

    known_text = " / ".join(known) if known else "아직 확정된 조건이 없어요"
    asks = []
    if "area" in missing:
        asks.append("상권이나 지역명")
    if "industry" in missing:
        asks.append("업종")
    if "budget" in missing:
        asks.append("예산")
    return f"{known_text}\n{', '.join(asks)}을 알려주면 더 구체적으로 이어서 볼게요."


def starter_options() -> list[ChatOption]:
    return [
        ChatOption(label="이태원 관광특구는 어떤 상권이야?", type="example", value="이태원 관광특구는 어떤 상권이야?"),
        ChatOption(label="명동에서 카페는 어떤 점을 봐야 해?", type="example", value="명동에서 카페는 어떤 점을 봐야 해?"),
        ChatOption(label="서울역 양식 5억이면 먼저 뭘 봐야 해?", type="example", value="서울역 양식 5억이면 먼저 뭘 봐야 해?"),
    ]
