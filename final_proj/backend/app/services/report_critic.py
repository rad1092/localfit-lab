from __future__ import annotations

import re
from typing import Any

from app.services.korean import josa


FORBIDDEN_PATTERNS = [
    r"창업\s*성공\s*확률",
    r"성공확률",
    r"매출(?:을)?\s*보장",
    r"성장률?(?:을)?\s*보장",
    r"방문\s*확률",
    r"월세(?:가|는)?\s*확정",
    r"권리금(?:이|은)?\s*확정",
    r"수익성(?:을)?\s*보장",
]

RAW_FLOAT_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])\d+\.(?:\d{3,}(?![A-Za-z0-9])|\d{2}(?![%A-Za-z0-9]))"
)
NUMERIC_DISPLAY_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])\d[\d,]*(?:\.\d+)?(?:천|만)?\s*"
    r"(?:개월|(?:개\s*)?분기|점|억원|만원|억|원|개|명|건|%|위|분|㎡)(?![A-Za-z0-9])"
)
DECISION_SENSITIVE_UNIT_PATTERN = re.compile(r"(?:점|억원|만원|억|원|명|건|%|위)$")

# ---- 가독성/해석 품질 검사용 상수 (튜닝 지점) ----
# 내부 테이블·파일 경로 토큰: 본문(원천 데이터 섹션 제외)에 나오면 안 된다.
PATH_TOKEN_PATTERN = re.compile(r"(?:DB\.|gold\.|FeatureMart|commercial\.db|\.csv\b|district_[a-z_]+|rule_location_score)")
# meaning이 evidence를 그대로 복붙했는지 판정하는 3-gram 포함률 상한.
REDUNDANCY_MAX_CONTAINMENT = 0.55
# 동일 수치 표시 토큰이 본문에서 반복 인용될 수 있는 최대 횟수.
METRIC_MAX_OCCURRENCES = 4
# 숫자·지표명을 뺀 해석 잔여 텍스트의 최소 길이(공백 제외).
INFERENCE_MIN_RESIDUE = 20
# 해석 문장에 최소 1개는 있어야 하는 인과/비교 연결어.
INFERENCE_CONNECTIVES = [
    "때문", "므로", "보다", "대비", "반면", "뜻", "의미", "시사", "우위", "열위",
    "부담", "여지", "구조", "가깝", "달리", "덕분", "탓", "만큼", "수준이라", "셈",
]
# 본문(체크리스트·한계 제외)에 허용되는 헤징 문장 수 상한.
HEDGE_MAX_SENTENCES = 1
HEDGE_PATTERN = re.compile(r"(현장\s*(?:확인|대조|검증|관찰)|보장하지\s*않|단정하지\s*않|별도\s*확인|믿지\s*마)")
COMPETITION_LOW_COUNT_PATTERN = re.compile(
    r"(?:동업종(?:\s*점포)?|점포|매장|가게|업소)\s*수(?:가|는)?"
    r"[^.!?\n]{0,12}?(?:많지\s*않|적(?:은|다))"
)
ACCESS_OTHER_AXIS_EVIDENCE_PATTERN = re.compile(
    r"(?:상주|직장|유동)\s*인구|(?:추정\s*)?매출|동업종\s*점포\s*수"
)
CAUSAL_SCOPE_OVERCLAIM_PATTERN = re.compile(
    r"실수요(?:를|가|는)?\s*(?:기대할\s*수\s*있는\s*구조|확보|충분|형성|존재|뒷받침)|"
    r"반복\s*접점(?:이|은|을)?\s*(?:충분|확보|형성|보장)|"
    r"수요와\s*유입(?:이|은)?\s*(?:충분히\s*)?(?:강|충분)|"
    r"실제\s*(?:방문|구매|수요|고객|유입|전환|매출)[^.!?\n]{0,20}"
    r"(?:충분|강하|확보|형성|보장|이어진|기대|확인)"
)
BUDGET_SCOPE_OVERCLAIM_PATTERN = re.compile(
    r"(?:예산|입력\s*금액)[^.!?\n]{0,60}"
    r"(?:검토(?:를)?\s*(?:가능|여지|출발선)|진입\s*(?:가능|검토)|가능한\s*범위|"
    r"충분(?:하|한)|적합|감당\s*가능|소화\s*가능|제외할\s*수준(?:은|이)?\s*아니)"
)
OFFICIAL_SCOPE_OVERCLAIM_PATTERN = re.compile(
    r"(?:유망|상위|추천)\s*(?:후보(?:군)?|상권|입지)|"
    r"(?:입지|업종)\s*적합성(?:이|은|도)?\s*(?:높|좋|우수)"
)
INTERNAL_LABEL_PATTERN = re.compile(
    r"\[(?:NEWS:\d+|근거\s*\d+)\]|"
    r"(?<![A-Za-z0-9가-힣])근거\s*\d+"
    r"(?![\d,.]|\s*(?:천|만)?(?:억원|만원|개월|분기|원|억|개|명|건|분|년|월|일|%|㎡))|"
    r"(?<![A-Za-z0-9_])(?:sales|competition|demand|accessibility)(?::\d+/\d+)?(?![A-Za-z0-9_])|"
    r"\b(?:context_only(?:_[a-z0-9_]+)?|full_4axis|taxonomy)\b|"
    r"(?<![A-Za-z0-9])C[1-5](?![A-Za-z0-9])",
    re.IGNORECASE,
)
GRADE_REFERENCE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])([A-E](?:\+)?)(?=\s*(?:등급|보다|에|와|과|로|은|는|이|가|을|를|$|[,.):!?]))"
)
AXIS_GRADE_CONTEXT_PATTERN = re.compile(
    r"(시장성|매출|경쟁\s*구조|수요\s*기반|접근(?:·|\s)*유입)[^.!?\n]{0,30}?"
    r"(?<![A-Za-z0-9])([A-E](?:\+)?)(?=\s*(?:등급|보다|에|와|과|로|은|는|이|가|을|를|$|[,.):!?]))"
)
# 본문에서 제외하는 마크다운 섹션 (경로·헤징·수치 스냅샷이 합법인 곳).
# 판단 헤더는 증권리포트식 지표 스냅샷 표라 수치 반복 카운트 대상이 아니다.
PROSE_EXCLUDED_SECTIONS = (
    "원천 데이터",
    "원천 테이블/파일",
    "사용한 데이터",
    "데이터 출처 및 산정 기준",
    "산정 기준",
    "해석 범위",
    "해석 참고문헌",
    "한계",
    "현장 체크리스트",
    "현장 검증 순서",
    "방법론",
    "판단 헤더",
    "핵심 판단",
)
# 조사 뒤가 공백/문장부호/끝일 때만 조사로 간주한다 ("명동가게"의 "가" 오탐 방지).
JOSA_PARTICLE_PATTERN = r"(은|는|이|가|을|를|과|와)(?=[\s,.):!?]|$)"
_JOSA_PAIR_OF = {"은": "은는", "는": "은는", "이": "이가", "가": "이가", "을": "을를", "를": "을를", "과": "과와", "와": "과와"}


def _walk_text(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        texts: list[str] = []
        for item in value:
            texts.extend(_walk_text(item))
        return texts
    if isinstance(value, dict):
        texts = []
        for item in value.values():
            texts.extend(_walk_text(item))
        return texts
    return []


def _collect_strings(value: Any) -> list[str]:
    return _walk_text(value)


def _budget_text(user_condition: dict[str, Any]) -> str | None:
    budget = user_condition.get("budget")
    try:
        amount = int(budget)
    except (TypeError, ValueError):
        return None
    if amount <= 0:
        return None
    if amount >= 10000:
        return f"{amount / 10000:g}억"
    if amount >= 1000 and amount % 1000 == 0:
        return f"{amount // 1000}천"
    return f"{amount:,}만원"


def _allowed_numeric_strings(facts_pack_display: dict[str, Any], user_condition: dict[str, Any]) -> set[str]:
    allowed: set[str] = set()
    for text in _collect_strings(facts_pack_display):
        allowed.add(text)
        for token in NUMERIC_DISPLAY_PATTERN.findall(text):
            allowed.add(token.strip())
    budget = user_condition.get("budget")
    try:
        amount = int(budget)
    except (TypeError, ValueError):
        amount = 0
    if amount > 0:
        allowed.add(f"{amount:,}만원")
        if amount % 10000 == 0:
            allowed.add(f"{amount // 10000}억")
            allowed.add(f"{amount // 10000}억원")
        elif amount >= 10000:
            allowed.add(f"{amount / 10000:g}억")
            allowed.add(f"{amount / 10000:g}억원")
    return allowed


def _markdown_prose_scope(markdown_body: str) -> str:
    """마크다운 본문에서 원천/한계/체크리스트 섹션을 뺀 사용자 읽기 영역만 남긴다."""
    if not markdown_body:
        return ""
    kept: list[str] = []
    skipping = False
    for line in markdown_body.splitlines():
        if line.startswith("## "):
            heading = line[3:].strip()
            skipping = any(name in heading for name in PROSE_EXCLUDED_SECTIONS)
            continue
        if not skipping:
            kept.append(line)
    return "\n".join(kept)


def _draft_prose_fields(draft: dict[str, Any]) -> list[tuple[str, str]]:
    """(필드 경로, 텍스트) 목록 — 사용자에게 읽히는 산문 필드만."""
    fields: list[tuple[str, str]] = []
    for name in [
        "thesis",
        "executive_interpretation",
        "score_interpretation",
        "summary",
        "trend_analysis",
        "user_fit",
        "action_plan",
        "onsite_checklist",
        "risk_factors",
        "strengths",
        "weaknesses",
    ]:
        value = draft.get(name)
        if isinstance(value, str) and value:
            fields.append((name, value))
        elif isinstance(value, list):
            for idx, item in enumerate(value):
                if isinstance(item, str) and item:
                    fields.append((f"{name}[{idx}]", item))
    for idx, axis in enumerate(draft.get("axis_interpretations") or []):
        for key in ["interpretation_level", "meaning", "risk", "action", "next_check"]:
            value = axis.get(key)
            if isinstance(value, str) and value:
                fields.append((f"axis_interpretations[{idx}].{key}", value))
    return fields


def _draft_factual_fields(draft: dict[str, Any]) -> list[tuple[str, str]]:
    """Fields that make descriptive claims, excluding procedural check instructions."""
    fields: list[tuple[str, str]] = []
    for name in [
        "thesis",
        "executive_interpretation",
        "score_interpretation",
        "summary",
        "trend_analysis",
        "user_fit",
        "risk_factors",
        "strengths",
        "weaknesses",
    ]:
        value = draft.get(name)
        if isinstance(value, str) and value:
            fields.append((name, value))
        elif isinstance(value, list):
            fields.extend(
                (f"{name}[{index}]", item)
                for index, item in enumerate(value)
                if isinstance(item, str) and item
            )
    for index, axis in enumerate(draft.get("axis_interpretations") or []):
        for key in ("meaning", "risk"):
            value = axis.get(key)
            if isinstance(value, str) and value:
                fields.append((f"axis_interpretations[{index}].{key}", value))
    return fields


def _draft_procedural_fields(draft: dict[str, Any]) -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = []
    for name in ("action_plan", "onsite_checklist"):
        fields.extend(
            (f"{name}[{index}]", item)
            for index, item in enumerate(draft.get(name) or [])
            if isinstance(item, str) and item
        )
    for index, axis in enumerate(draft.get("axis_interpretations") or []):
        for key in ("action", "next_check"):
            value = axis.get(key)
            if isinstance(value, str) and value:
                fields.append((f"axis_interpretations[{index}].{key}", value))
    return fields


def _axis_fact_scope(facts_pack_display: dict[str, Any], axis_label: str) -> dict[str, Any]:
    label = str(axis_label or "")
    if "시장" in label or "매출" in label:
        return facts_pack_display.get("sales_block") or {}
    if "경쟁" in label:
        return facts_pack_display.get("competition_block") or {}
    if "수요" in label:
        return facts_pack_display.get("demand_block") or {}
    if "접근" in label or "유입" in label:
        return facts_pack_display.get("accessibility_block") or {}
    return {}


def _axis_key(axis_label: str) -> str:
    label = re.sub(r"\s+", "", str(axis_label or ""))
    if "시장" in label or "매출" in label:
        return "sales"
    if "경쟁" in label:
        return "competition"
    if "수요" in label:
        return "demand"
    if "접근" in label or "유입" in label:
        return "accessibility"
    return ""


def _competition_position_is_high(facts_pack_display: dict[str, Any]) -> bool:
    metrics = (facts_pack_display.get("competition_block") or {}).get("metrics") or []
    for metric in metrics:
        if re.sub(r"\s+", "", str((metric or {}).get("label") or "")) != "동업종점포수위치":
            continue
        match = re.search(r"상위\s*(\d+(?:\.\d+)?)\s*%", str((metric or {}).get("display") or ""))
        if match and float(match.group(1)) <= 10:
            return True
    return False


def _sentence_for_match(text: str, match: re.Match[str]) -> str:
    start = max(text.rfind(separator, 0, match.start()) for separator in ".!?\n") + 1
    ends = [
        position
        for separator in ".!?\n"
        if (position := text.find(separator, match.end())) >= 0
    ]
    end = min(ends) + 1 if ends else len(text)
    return text[start:end]


def _is_qualified_competition_direction(text: str, match: re.Match[str]) -> bool:
    return bool(
        re.search(
            r"(?:다고|다는?\s*것으로)\s*(?:볼|말할|판단할)\s*수(?:는)?\s*없|"
            r"(?:다는?|라고)\s*(?:해석|단정|판단)하지\s*않",
            _sentence_for_match(text, match),
        )
    )


def _is_qualified_access_reference(text: str, match: re.Match[str]) -> bool:
    return bool(
        re.search(
            r"(?:접근(?:·|\s)*유입\s*)?(?:근거|원인)(?:으)?로\s*(?:재)?사용하지\s*않|"
            r"(?:접근(?:·|\s)*유입을?\s*)?설명하지\s*않|"
            r"직접\s*(?:지표|근거)(?:가|는)?\s*아니|대신하지\s*못",
            _sentence_for_match(text, match),
        )
    )


def _is_qualified_causal_scope(text: str, match: re.Match[str]) -> bool:
    return bool(
        re.search(
            r"(?:충분|강하|실수요|반복\s*접점)[^.!?\n]{0,20}"
            r"(?:단정|확정|판단)할\s*수(?:는)?\s*없|"
            r"(?:충분|강한)한?지[^.!?\n]{0,20}(?:확인|검증)|"
            r"(?:충분|전환|방문|구매)[^.!?\n]{0,20}여부[^.!?\n]{0,20}(?:확인|검증)|"
            r"(?:같지\s*않|뜻하지\s*않|의미하지\s*않)",
            _sentence_for_match(text, match),
        )
    )


def _verified_axis_grades(draft: dict[str, Any]) -> tuple[dict[int, str], dict[str, str]]:
    by_index: dict[int, str] = {}
    by_key: dict[str, str] = {}
    for index, axis in enumerate(draft.get("axis_interpretations") or []):
        grade = str(axis.get("display_grade") or axis.get("score_display") or "").strip().upper()
        if not re.fullmatch(r"[A-E](?:\+)?", grade):
            grade = "등급 보류"
        by_index[index] = grade
        key = _axis_key(str(axis.get("axis") or ""))
        if key:
            by_key[key] = grade
    return by_index, by_key


def _is_negated_forbidden_match(text: str, match: re.Match[str]) -> bool:
    tail = text[match.end() : match.end() + 50]
    return bool(
        re.search(
            r"(?:하지\s*않|하지\s*못|되지\s*않|될\s*수\s*없|"
            r"(?:이|은|가|값이|값은)\s*아니|보류|금지)",
            tail,
        )
    )


def _is_negated_scope_match(text: str, match: re.Match[str]) -> bool:
    tail = text[match.end() : match.end() + 60]
    return bool(
        re.search(
            r"(?:로\s*)?(?:보지|평가하지|판단하지|분류하지)\s*않|"
            r"(?:이|은|가)?\s*아니|보류|단정할\s*수(?:는)?\s*없|보기\s*어렵",
            tail,
        )
    )


def _is_verified_trend_window(
    token: str,
    text: str,
    facts_pack_display: dict[str, Any],
) -> bool:
    match = re.fullmatch(r"(\d+)\s*(?:개\s*)?분기", token)
    if not match:
        return False
    count = int(match.group(1))
    if not re.search(rf"(?<!\d){count}\s*(?:개\s*)?분기", text):
        return False
    windows = [
        (facts_pack_display.get("sales_block") or {}).get("sales_trend") or [],
        (facts_pack_display.get("competition_block") or {}).get("store_trend") or [],
    ]
    return any(len(window) == count for window in windows)


def _display_amount_value(value: Any) -> float | None:
    text = re.sub(r"\s+", "", str(value or ""))
    match = re.fullmatch(r"(-?\d[\d,]*(?:\.\d+)?)(억원|만원|억|원)", text)
    if not match:
        return None
    number = float(match.group(1).replace(",", ""))
    multiplier = {
        "억원": 100_000_000,
        "억": 100_000_000,
        "만원": 10_000,
        "원": 1,
    }[match.group(2)]
    return number * multiplier


def _expected_sales_trend_direction(facts_pack_display: dict[str, Any]) -> str | None:
    rows = (facts_pack_display.get("sales_block") or {}).get("sales_trend") or []
    points: list[tuple[int, float]] = []
    for row in rows:
        value = _display_amount_value(row.get("sales_amount"))
        try:
            timestamp = int(row.get("timestamp"))
        except (TypeError, ValueError):
            continue
        if value is not None:
            points.append((timestamp, value))
    points.sort(key=lambda item: item[0])
    if len(points) < 2:
        return None
    if points[-1][1] < points[0][1]:
        return "down"
    return "up_or_flat"


def _trend_direction_from_text(text: str) -> str:
    down_matches = list(
        re.finditer(r"내려왔|하락|감소|낮아졌|낮아져|줄었|약해졌|둔화", str(text or ""))
    )
    up_matches = list(
        re.finditer(r"올라왔|상승|증가|높아졌|높아져|늘었|강해졌", str(text or ""))
    )
    if down_matches and not up_matches:
        return "down"
    if up_matches and not down_matches:
        return "up_or_flat"
    if down_matches and up_matches:
        return (
            "down"
            if down_matches[-1].start() > up_matches[-1].start()
            else "up_or_flat"
        )
    if re.search(r"유지|보합|비슷한\s*수준", str(text or "")):
        return "up_or_flat"
    return "other"


def _is_qualified_budget_statement(text: str, match: re.Match[str]) -> bool:
    sentence_end = re.search(r"[.!?\n]", text[match.end() :])
    end = match.end() + (sentence_end.start() if sentence_end else 80)
    statement = text[match.start() : min(len(text), end + 1)]
    return bool(
        re.search(
            r"(?:공식[^.!?\n]{0,20}보류|적합도[^.!?\n]{0,20}보류|"
            r"단정할\s*수\s*없|판단하면\s*안|충분하다고[^.!?\n]{0,15}(?:않|없)|"
            r"가능하다고[^.!?\n]{0,15}(?:않|없))",
            statement,
        )
    )


def _issue(code: str, message: str, field_path: str | None = None) -> str:
    field_marker = f"[field={field_path}] " if field_path else ""
    return f"[{code}] {field_marker}{message}"


def _char_ngrams(text: str, n: int = 3) -> set[str]:
    compact = re.sub(r"\s+", "", text)
    if len(compact) < n:
        return {compact} if compact else set()
    return {compact[i : i + n] for i in range(len(compact) - n + 1)}


def _containment(inner: str, outer: str) -> float:
    grams = _char_ngrams(inner)
    if not grams:
        return 0.0
    outer_grams = _char_ngrams(outer)
    return len(grams & outer_grams) / len(grams)


def _sentences(text: str) -> list[str]:
    return [chunk.strip() for chunk in re.split(r"(?<=[.!?다요])\s+", text) if chunk.strip()]


def validate_report_draft(
    draft: dict[str, Any],
    *,
    facts_pack_display: dict[str, Any],
    user_condition: dict[str, Any],
    evidence_frames: list[dict[str, Any]] | None = None,
    markdown_body: str = "",
) -> list[str]:
    """리포트 발행 전 결정적 검증. 사실 정합 + 가독성/해석 품질."""

    violations: list[str] = []
    prose_fields = _draft_prose_fields(draft)

    for field_name, text in prose_fields:
        for pattern in FORBIDDEN_PATTERNS:
            for match in re.finditer(pattern, text):
                if not _is_negated_forbidden_match(text, match):
                    violations.append(_issue("FORBIDDEN", f"금지 주장 패턴 감지: {pattern}", field_name))
                    break

        raw_hits = RAW_FLOAT_PATTERN.findall(text)
        if raw_hits:
            sample = ", ".join(sorted(set(raw_hits))[:5])
            violations.append(
                _issue("FORMAT", f"raw float 또는 소수 2자리 초과 노출: {sample}", field_name)
            )

    allowed = _allowed_numeric_strings(facts_pack_display, user_condition)
    for field_name, text in _draft_factual_fields(draft):
        fact_mismatches = [
            token.strip()
            for token in sorted(set(NUMERIC_DISPLAY_PATTERN.findall(text)))
            if token.strip() not in allowed
            and not (
                field_name == "trend_analysis"
                and _is_verified_trend_window(token.strip(), text, facts_pack_display)
            )
        ]
        if fact_mismatches:
            violations.append(
                _issue(
                    "FACT_MISMATCH",
                    f"facts pack display에 없는 숫자: {', '.join(fact_mismatches[:8])}",
                    field_name,
                )
            )

    # Procedural counts and observation durations (for example, three candidates or
    # thirty minutes) are recommendations, not claims about observed data. Financial
    # or statistical numbers in those fields still require a verified source value.
    for field_name, text in _draft_procedural_fields(draft):
        fact_mismatches = [
            token.strip()
            for token in sorted(set(NUMERIC_DISPLAY_PATTERN.findall(text)))
            if DECISION_SENSITIVE_UNIT_PATTERN.search(token.strip()) and token.strip() not in allowed
        ]
        if fact_mismatches:
            violations.append(
                _issue(
                    "FACT_MISMATCH",
                    f"facts pack display에 없는 의사결정 수치: {', '.join(fact_mismatches[:8])}",
                    field_name,
                )
            )

    expected_trend_direction = _expected_sales_trend_direction(facts_pack_display)
    if expected_trend_direction is not None:
        actual_trend_direction = _trend_direction_from_text(
            str(draft.get("trend_analysis") or "")
        )
        if actual_trend_direction != expected_trend_direction:
            violations.append(
                _issue(
                    "TREND_DIRECTION_MISMATCH",
                    "시작 분기와 최근 분기 매출 원값의 방향을 추이 문장이 명시하지 않거나 반대로 해석함",
                    "trend_analysis",
                )
            )

    # A number may exist elsewhere in the report and still be wrong for this axis.
    # Keep the axis-to-source boundary hard while leaving prose form flexible.
    for axis_index, axis in enumerate(draft.get("axis_interpretations") or []):
        meaning = str(axis.get("meaning") or "")
        axis_scope = _axis_fact_scope(facts_pack_display, str(axis.get("axis") or ""))
        if not meaning or not axis_scope:
            continue
        axis_allowed = _allowed_numeric_strings(axis_scope, {})
        mismatches = [
            token.strip()
            for token in sorted(set(NUMERIC_DISPLAY_PATTERN.findall(meaning)))
            if token.strip() not in axis_allowed
        ]
        if mismatches:
            violations.append(
                _issue(
                    "AXIS_FACT_MISMATCH",
                    f"해당 판단 영역 근거에 없는 숫자: {', '.join(mismatches[:8])}",
                    f"axis_interpretations[{axis_index}].meaning",
                )
            )

    # Semantic direction and evidence-scope checks remain hard even when the prose
    # is fluent and all displayed numbers individually exist.
    competition_position_is_high = _competition_position_is_high(facts_pack_display)
    for axis_index, axis in enumerate(draft.get("axis_interpretations") or []):
        axis_key = _axis_key(str(axis.get("axis") or ""))
        meaning = str(axis.get("meaning") or "")
        field_path = f"axis_interpretations[{axis_index}].meaning"
        if axis_key == "competition" and competition_position_is_high:
            low_count_patterns = [COMPETITION_LOW_COUNT_PATTERN]
            industry_name = str(
                ((facts_pack_display.get("target") or {}).get("industry_name") or "")
            ).strip()
            if industry_name:
                low_count_patterns.append(
                    re.compile(
                        rf"{re.escape(industry_name)}\s*수(?:가|는)?"
                        r"[^.!?\n]{0,12}?(?:많지\s*않|적(?:은|다))"
                    )
                )
            direction_mismatch = next(
                (
                    match
                    for pattern in low_count_patterns
                    for match in pattern.finditer(meaning)
                    if not _is_qualified_competition_direction(meaning, match)
                ),
                None,
            )
            if direction_mismatch:
                violations.append(
                    _issue(
                        "COMPETITION_DIRECTION_MISMATCH",
                        "동업종 점포 수가 상위권인데 점포 수가 적거나 많지 않다고 반대로 해석함",
                        field_path,
                    )
                )
        if axis_key == "accessibility":
            access_metrics = (facts_pack_display.get("accessibility_block") or {}).get("metrics") or []
            if not access_metrics:
                for match in ACCESS_OTHER_AXIS_EVIDENCE_PATTERN.finditer(meaning):
                    if not _is_qualified_access_reference(meaning, match):
                        violations.append(
                            _issue(
                                "ACCESS_EVIDENCE_SCOPE_MISMATCH",
                                "접근·유입 세부 지표가 없는데 다른 판단 영역의 지표를 원인 근거로 재사용함",
                                field_path,
                            )
                        )
                        break

    for field_name, text in _draft_factual_fields(draft):
        for match in CAUSAL_SCOPE_OVERCLAIM_PATTERN.finditer(text):
            if not _is_qualified_causal_scope(text, match):
                violations.append(
                    _issue(
                        "CAUSAL_SCOPE_OVERCLAIM",
                        "집계 지표를 실제 수요·방문·구매 전환 또는 반복 접점의 충분성으로 단정함",
                        field_name,
                    )
                )
                break

    # Grades are backend-owned facts. Check both prose inside a specific axis and
    # named axis/grade pairs elsewhere so fluent comparative wording cannot turn a
    # verified B into an A (or invent a grade for a withheld axis).
    axis_grades_by_index, axis_grades_by_key = _verified_axis_grades(draft)
    for field_name, text in prose_fields:
        mismatches: set[str] = set()
        axis_field = re.fullmatch(r"axis_interpretations\[(\d+)\]\.[a-z_]+", field_name)
        if axis_field:
            index = int(axis_field.group(1))
            expected = axis_grades_by_index.get(index, "등급 보류")
            for claimed in GRADE_REFERENCE_PATTERN.findall(text):
                if claimed != expected:
                    mismatches.add(f"{claimed}(공식 {expected})")
        for axis_label, claimed in AXIS_GRADE_CONTEXT_PATTERN.findall(text):
            expected = axis_grades_by_key.get(_axis_key(axis_label), "등급 보류")
            if claimed != expected:
                mismatches.add(f"{axis_label} {claimed}(공식 {expected})")
        if mismatches:
            violations.append(
                _issue(
                    "GRADE_MISMATCH",
                    f"검증된 판단 영역 등급과 모순되는 표현: {', '.join(sorted(mismatches))}",
                    field_name,
                )
            )

    budget_fit = ((facts_pack_display.get("cost_block") or {}).get("budget_fit") or {})
    budget_fit_withheld = (
        budget_fit.get("budget_fit_score") is None
        or str(budget_fit.get("official_budget_fit_status") or "").startswith("withheld")
    )
    if user_condition.get("budget") and budget_fit_withheld:
        for field_name, text in prose_fields:
            for match in BUDGET_SCOPE_OVERCLAIM_PATTERN.finditer(text):
                if _is_qualified_budget_statement(text, match):
                    continue
                violations.append(
                    _issue(
                        "BUDGET_SCOPE_OVERCLAIM",
                        "공식 예산 적합도 보류 상태에서 진입 가능·충분성을 단정함",
                        field_name,
                    )
                )
                break

    coverage = ((facts_pack_display.get("score_block") or {}).get("coverage") or {})
    if not bool(coverage.get("official_rank_eligible")):
        for field_name, text in prose_fields:
            for match in OFFICIAL_SCOPE_OVERCLAIM_PATTERN.finditer(text):
                if _is_negated_scope_match(text, match):
                    continue
                violations.append(
                    _issue(
                        "OFFICIAL_SCOPE_OVERCLAIM",
                        "공식 종합 판단 보류 상태에서 유망·상위·추천 후보로 단정함",
                        field_name,
                    )
                )
                break

    for field_name, text in prose_fields:
        internal_match = INTERNAL_LABEL_PATTERN.search(text)
        if internal_match:
            violations.append(
                _issue(
                    "INTERNAL_LABEL",
                    f"사용자 문장에 내부 필드 코드 노출: {internal_match.group(0)}",
                    field_name,
                )
            )

    # --- 경로 토큰: 산문 필드와 마크다운 본문(원천 섹션 제외) 어디에도 내부 경로가 없어야 한다.
    path_hits: list[str] = []
    for field_name, text in prose_fields:
        found = PATH_TOKEN_PATTERN.findall(text)
        if found:
            path_hits.append(f"{field_name}: {found[0]}")
    for path_hit in path_hits:
        field_name, token = path_hit.split(": ", 1)
        violations.append(
            _issue("PATH_TOKEN", f"내부 테이블/파일 경로가 본문에 노출: {token}", field_name)
        )

    # --- 중복: meaning이 evidence를 복붙하면 해석이 아니다.
    for axis_index, axis in enumerate(draft.get("axis_interpretations") or []):
        meaning = str(axis.get("meaning") or "")
        evidence_text = str(axis.get("evidence") or "") + " " + " ".join(axis.get("evidence_metrics") or [])
        if meaning and evidence_text.strip():
            ratio = _containment(meaning, evidence_text)
            if ratio > REDUNDANCY_MAX_CONTAINMENT:
                violations.append(
                    _issue(
                        "REDUNDANT_SECTION",
                        f"{axis.get('axis') or 'unknown'} 해석이 근거 지표 나열과 {ratio:.0%} 중복 — 해석 문장으로 다시 써야 함",
                        f"axis_interpretations[{axis_index}].meaning",
                    )
                )

    # --- 동일 수치 반복: 서사 문장에서 같은 표시 토큰이 과도하게 반복되면 낭독이다.
    # 표 행(|로 시작)은 구조화된 제시라 카운트에서 제외한다 (서로 다른 지표의 동일 값 오탐 방지).
    for field_name, narration in prose_fields:
        counts: dict[str, int] = {}
        for token in NUMERIC_DISPLAY_PATTERN.findall(narration):
            clean = token.strip()
            counts[clean] = counts.get(clean, 0) + 1
        repeated = [f"{token}({count}회)" for token, count in counts.items() if count > METRIC_MAX_OCCURRENCES]
        if repeated:
            violations.append(
                _issue(
                    "REDUNDANT_METRIC",
                    f"동일 수치 반복 인용: {', '.join(sorted(repeated)[:5])}",
                    field_name,
                )
            )

    # --- 해석 부재: 숫자·라벨을 지운 뒤에도 인과/비교 서술이 남아야 해석이다.
    for axis_index, axis in enumerate(draft.get("axis_interpretations") or []):
        meaning = str(axis.get("meaning") or "")
        if not meaning:
            continue
        residue = NUMERIC_DISPLAY_PATTERN.sub(" ", meaning)
        residue = re.sub(r"\d[\d,\.]*", " ", residue)
        for metric_text in axis.get("evidence_metrics") or []:
            label = str(metric_text).split(" ")[0]
            if label:
                residue = residue.replace(label, " ")
        compact = re.sub(r"\s+", "", residue)
        has_connective = any(word in meaning for word in INFERENCE_CONNECTIVES)
        if len(compact) < INFERENCE_MIN_RESIDUE and not has_connective:
            violations.append(
                _issue(
                    "NO_INFERENCE",
                    f"{axis.get('axis') or 'unknown'} 해석에 수치 낭독 외 추론 서술이 부족 — 왜/그래서를 설명해야 함",
                    f"axis_interpretations[{axis_index}].meaning",
                )
            )

    # --- 헤징 도배: 서사 문장에 확인/면책 문장이 상한을 넘으면 안 된다 (체크리스트·한계·표는 제외).
    for field_name, text in prose_fields:
        hedge_count = sum(1 for sentence in _sentences(text) if HEDGE_PATTERN.search(sentence))
        if hedge_count > HEDGE_MAX_SENTENCES:
            violations.append(
                _issue(
                    "HEDGE_SPREAD",
                    f"한 필드의 헤징 문장 {hedge_count}개 (허용 {HEDGE_MAX_SENTENCES}) — 확인·면책은 체크리스트/한계 섹션으로 이동",
                    field_name,
                )
            )

    # --- 조사 검사: 알려진 엔티티명 뒤의 조사가 받침과 맞아야 한다.
    entities: set[str] = set()
    target = facts_pack_display.get("target") if isinstance(facts_pack_display, dict) else {}
    target = target if isinstance(target, dict) else {}
    for key in ["area_name", "industry_name"]:
        value = target.get(key) or (
            facts_pack_display.get(key) if isinstance(facts_pack_display, dict) else None
        ) or draft.get(key)
        if isinstance(value, str) and len(value) >= 2:
            entities.add(value)
    for alt in draft.get("alternatives") or []:
        name = (alt or {}).get("area_name")
        if isinstance(name, str) and len(name) >= 2:
            entities.add(name)
    for axis in draft.get("axis_interpretations") or []:
        name = axis.get("axis")
        if isinstance(name, str) and len(name) >= 2:
            entities.add(name)
    for field_name, text in prose_fields:
        josa_errors: list[str] = []
        for entity in entities:
            for match in re.finditer(re.escape(entity) + JOSA_PARTICLE_PATTERN, text):
                particle = match.group(1)
                expected = josa(entity, _JOSA_PAIR_OF[particle])
                if particle != expected:
                    josa_errors.append(f"{entity}{particle}→{entity}{expected}")
        if josa_errors:
            violations.append(
                _issue("JOSA", f"조사 오류: {', '.join(sorted(set(josa_errors))[:5])}", field_name)
            )

    axes = draft.get("axis_interpretations") or []
    if len(axes) < 4:
        violations.append(_issue("AXIS_NO_EVIDENCE", "축별 해석 4개 미만", "axis_interpretations"))
    max_frame_id = len(evidence_frames or [])
    for axis_index, axis in enumerate(axes):
        evidence = axis.get("evidence_metrics") or axis.get("evidence")
        if not evidence:
            violations.append(
                _issue(
                    "AXIS_NO_EVIDENCE",
                    f"{axis.get('axis') or 'unknown'} 근거 지표 누락",
                    f"axis_interpretations[{axis_index}]",
                )
            )
        citations = axis.get("frame_citations") or []
        if evidence_frames is not None and max_frame_id > 0 and not citations:
            violations.append(
                _issue(
                    "FAKE_CITATION",
                    f"{axis.get('axis') or 'unknown'} 해석 프레임 각주 누락",
                    f"axis_interpretations[{axis_index}].frame_citations",
                )
            )
        for citation in citations:
            try:
                citation_id = int(citation)
            except (TypeError, ValueError):
                violations.append(
                    _issue(
                        "FAKE_CITATION",
                        f"잘못된 각주 번호: {citation}",
                        f"axis_interpretations[{axis_index}].frame_citations",
                    )
                )
                continue
            if citation_id < 1 or citation_id > max_frame_id:
                violations.append(
                    _issue(
                        "FAKE_CITATION",
                        f"존재하지 않는 각주 번호: {citation_id}",
                        f"axis_interpretations[{axis_index}].frame_citations",
                    )
                )

    alternatives = draft.get("alternatives") or []
    available_alternatives = facts_pack_display.get("alternatives") or alternatives
    expected_alternatives = min(2, len(available_alternatives))
    if len(alternatives) < expected_alternatives:
        violations.append(
            _issue(
                "MISSING_ALTERNATIVES",
                f"대안 상권 {expected_alternatives}개 미만",
                "alternatives",
            )
        )

    condition_field_names = {
        "executive_interpretation",
        "score_interpretation",
        "summary",
        "user_fit",
    }
    condition_body = "\n".join(
        text
        for field_name, text in prose_fields
        if field_name in condition_field_names or field_name.startswith("thesis[")
    )

    budget = _budget_text(user_condition)
    if budget and budget not in condition_body and f"{user_condition.get('budget'):,}만원" not in condition_body:
        violations.append(_issue("MISSING_USER_COND", f"예산 조건 미등장: {budget}", "user_fit"))

    business_type = user_condition.get("business_type")
    if business_type and business_type not in condition_body:
        violations.append(_issue("MISSING_USER_COND", f"업종 조건 미등장: {business_type}", "user_fit"))

    return violations


def validate_comparison_draft(draft: dict[str, Any], payload: dict[str, Any]) -> list[str]:
    """비교 리포트용 경량 검증: 금지 주장, raw float, 경로 토큰, 조사, 숫자 정합."""
    texts = _walk_text(draft)
    body = "\n".join(texts)
    violations: list[str] = []

    for pattern in FORBIDDEN_PATTERNS:
        if re.search(pattern, body):
            violations.append(f"[FORBIDDEN] 금지 주장 패턴 감지: {pattern}")

    raw_hits = RAW_FLOAT_PATTERN.findall(body)
    if raw_hits:
        violations.append(f"[FORMAT] raw float 노출: {', '.join(sorted(set(raw_hits))[:5])}")

    prose = "\n".join(
        [str(draft.get("executive_interpretation") or ""), str(draft.get("summary") or "")]
        + [str((row or {}).get("interpretation") or "") for row in draft.get("comparison_matrix") or []]
    )
    if PATH_TOKEN_PATTERN.search(prose):
        violations.append("[PATH_TOKEN] 내부 테이블/파일 경로가 비교 본문에 노출")

    # 숫자 정합: 비교 payload의 점수 값에서 허용 토큰을 구성한다.
    allowed: set[str] = set()
    for area in payload.get("areas") or []:
        score_value = area.get("score")
        if score_value is not None:
            try:
                score = float(score_value)
                allowed.add(f"{score:g}점")
                allowed.add(f"{round(score, 1):g}점")
                allowed.add(f"{round(score)}점")
            except (TypeError, ValueError):
                pass
        for value in (area.get("axes") or {}).values():
            if value is None:
                continue
            try:
                number = float(value)
                allowed.add(f"{round(number, 1):g}점")
                allowed.add(f"{round(number)}점")
            except (TypeError, ValueError):
                pass
    mismatches = []
    for token in set(NUMERIC_DISPLAY_PATTERN.findall(prose)):
        clean = token.strip()
        if clean.endswith("점") and clean not in allowed:
            mismatches.append(clean)
    if mismatches:
        violations.append(f"[FACT_MISMATCH] 비교 payload에 없는 점수: {', '.join(sorted(mismatches)[:5])}")

    # 조사 검사 (상권명 한정)
    josa_errors = []
    for area in payload.get("areas") or []:
        name = str(area.get("area_name") or "")
        if len(name) < 2:
            continue
        for match in re.finditer(re.escape(name) + JOSA_PARTICLE_PATTERN, prose):
            particle = match.group(1)
            expected = josa(name, _JOSA_PAIR_OF[particle])
            if particle != expected:
                josa_errors.append(f"{name}{particle}→{name}{expected}")
    if josa_errors:
        violations.append(f"[JOSA] 조사 오류: {', '.join(sorted(set(josa_errors))[:5])}")

    return violations
