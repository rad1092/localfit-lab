from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.responses import FileResponse

from app.database import get_db
from app.dependencies import get_commercial_area_service, get_current_user, get_optional_user
from app.models.commercial_area import ChatbotHistory, SavedReport, User, TokenUsageLog
from app.schemas.commercial_area import (
    ChatReplyResponse,
    ChatRequest,
    ChatState,
    ChatbotAction,
    ChatbotHistoryResponse,
    ChatOption,
    ChatbotRequest,
    ChatbotResponse,
    CompactResponse,
    UserBusinessCondition,
)
from app.ai.recursive_layer import track_token_usage, calculate_token_cost, get_openai_model

from app.services.chatbot_compact import answer_conversation, compact_template, generate_compact_response
from app.services.chatbot_parser import (
    build_missing_slot_text,
    find_area_candidates,
    find_industry_candidates,
    is_help_intent,
    merge_state,
    starter_options,
)
from app.services.commercial_area import AXIS_SUBJECT_MAP, CommercialAreaService
from app.services.indicator_pack import _score_grade
from app.services.interpretive_report import interpret_single_report
from app.services.report_publisher import (
    REPORTS_OUT,
    normalize_public_report_data,
    publish_report_artifacts,
    report_artifacts_are_current,
)


router = APIRouter(prefix="/chatbot", tags=["chatbot"])
DISPLAY_GRADE_PATTERN = re.compile(r"^([A-E])\s*(\+)?(?:\s*등급)?$")


def _optional_float(value) -> float | None:
    return float(value) if value is not None else None


def _display_grade(value) -> str | None:
    match = DISPLAY_GRADE_PATTERN.fullmatch(str(value or "").strip().upper())
    return f"{match.group(1)}{match.group(2) or ''}" if match else None


def _loads_result(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


@router.get("/history", response_model=list[ChatbotHistoryResponse])
def get_chatbot_history(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    history = (
        db.query(ChatbotHistory)
        .filter(ChatbotHistory.user_id == current_user.id)
        .order_by(ChatbotHistory.created_at.desc())
        .all()
    )
    return [
        ChatbotHistoryResponse(
            id=item.id,
            area_name=item.area_name,
            business_type=item.business_type,
            budget=item.budget,
            result_data=normalize_public_report_data(_loads_result(item.result_data)),
            created_at=str(item.created_at) if item.created_at else None,
        )
        for item in history
    ]


@router.get("/history/{history_id}", response_model=ChatbotHistoryResponse)
def get_chatbot_history_by_id(
    history_id: int,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    item = db.query(ChatbotHistory).filter(ChatbotHistory.id == history_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="report not found")
    if item.user_id is not None and (current_user is None or item.user_id != current_user.id):
        raise HTTPException(status_code=404, detail="report not found")
    return ChatbotHistoryResponse(
        id=item.id,
        area_name=item.area_name,
        business_type=item.business_type,
        budget=item.budget,
        result_data=normalize_public_report_data(_loads_result(item.result_data)),
        created_at=str(item.created_at) if item.created_at else None,
    )


def _owned_history_or_404(db: Session, history_id: int, current_user: Optional[User]) -> ChatbotHistory:
    item = db.query(ChatbotHistory).filter(ChatbotHistory.id == history_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="report not found")
    if item.user_id is not None and (current_user is None or item.user_id != current_user.id):
        raise HTTPException(status_code=404, detail="report not found")
    return item


def _history_artifacts(item: ChatbotHistory) -> dict:
    report_dir = REPORTS_OUT / f"chat_{item.id}"
    if not report_artifacts_are_current(f"chat_{item.id}"):
        data = _loads_result(item.result_data)
        if not data:
            raise HTTPException(status_code=404, detail="report data not available")
        publish_report_artifacts(f"chat_{item.id}", data)
    return {"dir": report_dir}


@router.get("/history/{history_id}/charts/{chart_id}")
def get_chatbot_history_chart(
    history_id: int,
    chart_id: str,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    if not re.fullmatch(r"C[1-5]", chart_id):
        raise HTTPException(status_code=400, detail="chart_id must be C1~C5")
    item = _owned_history_or_404(db, history_id, current_user)
    report_dir = _history_artifacts(item)["dir"]
    chart_path = report_dir / "charts" / f"{chart_id}.png"
    if not chart_path.exists():
        raise HTTPException(status_code=404, detail="chart not available for this report")
    return FileResponse(str(chart_path), media_type="image/png")


@router.get("/history/{history_id}/download")
def download_chatbot_history_report(
    history_id: int,
    format: str = "pdf",
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    item = _owned_history_or_404(db, history_id, current_user)
    report_dir = _history_artifacts(item)["dir"]
    fmt = format.lower()
    if fmt == "pdf":
        path = report_dir / "report.pdf"
        media_type = "application/pdf"
    else:
        raise HTTPException(status_code=400, detail="only pdf download is supported")
    if not path.exists():
        raise HTTPException(status_code=404, detail="artifact not available")
    return FileResponse(str(path), media_type=media_type, filename=f"ai_report_{history_id}.{fmt}")


def _industry_option(row: dict) -> dict:
    path = str(row.get("selection_path") or "")
    parts = [part.strip() for part in path.split(">") if part.strip()]
    return {
        "industry_code": row.get("industry_code"),
        "industry_name": row.get("industry_name"),
        "display_label": row.get("display_label") or row.get("industry_name"),
        "selection_path": row.get("selection_path"),
        "major": row.get("ui_major_name") or (parts[0] if len(parts) > 0 else None),
        "middle": row.get("ui_middle_name") or (parts[1] if len(parts) > 1 else None),
        "detail": row.get("ui_detail_name") or (parts[2] if len(parts) > 2 else None),
    }


@router.get("/area-options")
def get_area_options(
    q: str = "",
    limit: int = 12,
    commercial_area_service: CommercialAreaService = Depends(get_commercial_area_service),
):
    query = (q or "").strip()
    safe_limit = max(1, min(int(limit or 12), 30))
    if query:
        return find_area_candidates(query, commercial_area_service, limit=safe_limit)

    rows = commercial_area_service.db.execute(
        text(
            """
            WITH area_context AS (
                SELECT
                    area_code,
                    (MAX(axis_demand) + MAX(axis_accessibility)) / 2.0 AS score
                FROM rule_location_score
                WHERE quarter = :quarter
                GROUP BY area_code
                HAVING MAX(axis_demand) IS NOT NULL
                   AND MAX(axis_accessibility) IS NOT NULL
            )
            SELECT l.area_code, l.area_name, l.display_label
            FROM location_lookup AS l
            LEFT JOIN area_context AS s ON s.area_code = l.area_code
            ORDER BY COALESCE(s.score, 0) DESC, l.area_name ASC
            LIMIT :limit
            """
        ),
        {"quarter": commercial_area_service.latest_quarter(), "limit": safe_limit},
    ).mappings().all()
    return [dict(row) for row in rows]


@router.get("/industry-options")
def get_industry_options(
    q: str = "",
    major: str = "",
    middle: str = "",
    detail: str = "",
    limit: int = 300,
    commercial_area_service: CommercialAreaService = Depends(get_commercial_area_service),
):
    query = (q or "").strip()
    safe_limit = max(1, min(int(limit or 300), 500))
    if query:
        candidates = find_industry_candidates(query, commercial_area_service, limit=safe_limit)
        if candidates:
            return [_industry_option(dict(item)) for item in candidates]

    rows = commercial_area_service.db.execute(
        text(
            """
            SELECT
              industry_code,
              industry_name,
              ui_major_name,
              ui_middle_name,
              ui_detail_name,
              display_label,
              selection_path
            FROM industry_hierarchy
            WHERE (:query = '' OR industry_name LIKE :kw OR search_text LIKE :kw)
              AND (:major = '' OR ui_major_name = :major)
              AND (:middle = '' OR ui_middle_name = :middle)
              AND (:detail = '' OR ui_detail_name = :detail)
            ORDER BY ui_major_name ASC, ui_middle_name ASC, ui_detail_name ASC, industry_name ASC
            LIMIT :limit
            """
        ),
        {
            "query": query,
            "kw": f"%{query}%",
            "major": (major or "").strip(),
            "middle": (middle or "").strip(),
            "detail": (detail or "").strip(),
            "limit": safe_limit,
        },
    ).mappings().all()
    return [_industry_option(dict(row)) for row in rows]


@router.delete("/history/{history_id}", status_code=204)
def delete_chatbot_history(
    history_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    item = (
        db.query(ChatbotHistory)
        .filter(ChatbotHistory.id == history_id, ChatbotHistory.user_id == current_user.id)
        .first()
    )
    if not item:
        raise HTTPException(status_code=404, detail="report not found or not owned by current user")
    db.delete(item)
    db.commit()


def _state_to_condition(state: ChatState) -> UserBusinessCondition:
    if not state.area_name:
        raise ValueError("state is incomplete")
    return UserBusinessCondition(
        area_name=state.area_name,
        business_type=state.business_type or None,
        budget=state.budget or None,
    )


def _budget_band(value: int | None) -> str:
    budget = int(value or 0)
    if budget <= 0:
        return "예산 미입력"
    if budget < 5000:
        return "5천만원 미만"
    if budget < 10000:
        return "5천만원-1억원"
    if budget < 20000:
        return "1억-2억원"
    return "2억원 이상"


def _risk_summary(fit_stats: dict, axes: dict) -> list[str]:
    risks = [
        "정량 평가는 입지 판단의 출발점이므로 현장 방문과 실제 견적으로 보완해야 합니다.",
        "비용 항목은 계약 조건, 권리금, 관리비를 별도로 확인해야 합니다.",
    ]
    if axes.get("cost_risk") is not None and float(axes["cost_risk"]) < 40:
        risks.append("비용 여건 등급이 낮아 임대 조건과 초기 고정비를 더 보수적으로 검토해야 합니다.")
    if axes.get("data_reliability") is not None and float(axes["data_reliability"]) < 60:
        risks.append("데이터 신뢰도 등급이 낮아 근거가 충분한 항목과 보조 항목을 구분해 봐야 합니다.")
    if fit_stats.get("decision_label"):
        risks.append(f"내부 판단 라벨: {fit_stats['decision_label']}")
    return risks


def _compact_alternatives(alternatives: list[dict]) -> list[dict]:
    return [
        {
            "area_code": item.get("area_code"),
            "area_name": item.get("area_name"),
            "reason": item.get("reason") or "같은 업종 조건에서 함께 비교할 후보입니다.",
            "interpretation_level": "대안 후보",
        }
        for item in alternatives
    ]


def _compact_text_from_report(report: dict) -> str:
    return compact_template(report.get("facts_lite_display") or {})


def _filled_report_header(raw_header: dict | None, fit_stats: dict, axes: dict, industry_name: str) -> dict:
    header = dict(raw_header or {})

    def usable(value) -> bool:
        return value not in (None, "", "-")

    is_area_summary = industry_name in {"상권 종합", "상권 맥락"}
    display_grade = (
        _display_grade(fit_stats.get("display_grade"))
        or _display_grade(header.get("display_grade"))
        or _display_grade(header.get("score"))
        or _display_grade(header.get("grade"))
    )
    unavailable_grade = (
        "상권 맥락 등급 확인 중"
        if is_area_summary
        else "등급 보류"
        if not fit_stats.get("official_rank_eligible")
        else "등급 확인 중"
    )
    if is_area_summary:
        header["judgement_line"] = "상권 수요·접근성 맥락 기준 검토"
    elif not usable(header.get("judgement_line")):
        header["judgement_line"] = header.get("decision_label") or fit_stats.get("decision_label") or "입지 조건 검토"
    header["score_label"] = "상권 맥락 등급" if is_area_summary else "입지 등급"
    header["display_grade"] = display_grade
    header["score"] = display_grade or unavailable_grade
    header["grade"] = display_grade or unavailable_grade
    if not usable(header.get("percentile")):
        header["percentile"] = "상권 수요·접근성 맥락 기준" if is_area_summary else (header.get("score_percentile") or "동일 업종 기준")

    if not header.get("key_metrics"):
        key_metrics = []
        for label, key in [
            ("매출", "sales"),
            ("경쟁", "competition"),
            ("수요", "demand"),
            ("접근성", "accessibility"),
        ]:
            value = axes.get(key)
            if value is None:
                continue
            key_metrics.append({"label": label, "display": _score_grade(value) or "등급 없음", "note": "공식 축 등급"})
        header["key_metrics"] = key_metrics
    return header


def _apply_header_to_markdown(markdown: str, header: dict) -> str:
    if not markdown:
        return markdown
    lines = markdown.splitlines()
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("| 판단 | 점수 | 등급") or stripped.startswith("| 종합 의견 | 점수 | 등급"):
            if idx + 2 < len(lines):
                first_column = "종합 의견" if stripped.startswith("| 종합 의견") else "판단"
                lines[idx] = f"| {first_column} | 입지 등급 | 동일 업종 내 위치 |"
                lines[idx + 1] = "|---|---|---|"
                lines[idx + 2] = (
                    f"| {header.get('judgement_line', '데이터 확인 중')} | "
                    f"{header.get('display_grade') or header.get('score', '등급 확인 중')} | "
                    f"{header.get('percentile', '데이터 확인 중')} |"
                )
            break

    metrics = header.get("key_metrics") or []
    if metrics:
        for idx, line in enumerate(lines):
            if line.strip().startswith("| 핵심 지표 | 값 | 메모") or line.strip().startswith("| 지표 | 값 | 해석 메모"):
                first_row = idx + 2
                if first_row < len(lines) and lines[first_row].strip() == "| - | - | - |":
                    rows = [
                        f"| {item.get('label', '-')} | {item.get('display', '-')} | {item.get('note', '')} |"
                        for item in metrics[:5]
                    ]
                    lines[first_row:first_row + 1] = rows
                break
    return "\n".join(lines) + ("\n" if markdown.endswith("\n") else "")


def _build_report_payload(
    service: CommercialAreaService,
    condition: UserBusinessCondition,
    area_code: str | None = None,
    industry_code: str | None = None,
):
    area = service.repository.get_by_code(area_code) if area_code else None
    if not area:
        matches = service.repository.search_by_name(condition.area_name)
        if not matches:
            raise HTTPException(status_code=404, detail=f"area not found: {condition.area_name}")
        exact_matches = [item for item in matches if item.area_name == condition.area_name]
        if len(exact_matches) == 1:
            area = exact_matches[0]
        elif len(matches) == 1:
            area = matches[0]
        else:
            raise HTTPException(
                status_code=400,
                detail={
                    "message": "area ambiguous",
                    "options": [
                        {"area_code": item.area_code, "area_name": item.area_name}
                        for item in matches[:5]
                    ],
                },
            )

    requested_industry = str(industry_code or condition.business_type or "").strip()
    industry = service.resolve_industry(requested_industry) if requested_industry else None
    if requested_industry and not industry:
        return None, area, service.industry_options(condition.business_type)

    fit_stats = service.calculate_user_fit_score(area, condition)
    alternatives = service.recommend_alternative_areas(condition, limit=3)
    axes = fit_stats.get("official_axes", {})
    risks = _risk_summary(fit_stats, axes)
    industry_code_value = industry["industry_code"] if industry else None
    industry_name_value = industry["industry_name"] if industry else "상권 맥락"

    interpretation = interpret_single_report(
        {
            "quarter": service.latest_quarter(),
            "area_code": area.area_code,
            "area_name": area.area_name,
            "industry_code": industry_code_value,
            "industry_name": industry_name_value,
            "score": fit_stats["score"],
            "context_location_score": fit_stats.get("context_score"),
            "grade": fit_stats.get("grade"),
            "display_grade": fit_stats.get("display_grade"),
            "decision_label": fit_stats.get("decision_label"),
            "score_source": fit_stats.get("score_source"),
            "score_version": None,
            "axes": {
                "axis_sales": _optional_float(axes.get("sales")),
                "axis_competition": _optional_float(axes.get("competition")),
                "axis_demand": _optional_float(axes.get("demand")),
                "axis_accessibility": _optional_float(axes.get("accessibility")),
            },
            "score_coverage_tier": fit_stats.get("score_coverage_tier"),
            "available_axis_count": fit_stats.get("available_axis_count"),
            "missing_axes": fit_stats.get("missing_axes"),
            "coverage_reason": fit_stats.get("coverage_reason"),
            "official_rank_eligible": fit_stats.get("official_rank_eligible"),
            "budget_fit": fit_stats.get("budget_fit"),
            "extra_signals": {
                "cost_risk_score": axes.get("cost_risk"),
                "data_reliability_score": axes.get("data_reliability"),
            },
            "user_condition": condition.model_dump(),
            "top_industries": [],
            "method_basis": [
                "Chatbot uses the official WLC/MCDA score only for full four-axis rows; three-axis rows are context-only.",
                "The AI explanation interprets criteria and evidence without recalculating scores.",
            ],
        }
    )
    if interpretation.get("quality_status") != "pass" or interpretation.get("validation_issues"):
        raise HTTPException(status_code=502, detail="리포트 검증을 완료하지 못했습니다.")
    header_block = _filled_report_header(interpretation.get("header_block"), fit_stats, axes, industry_name_value)
    markdown_body = _apply_header_to_markdown(interpretation.get("markdown_body", ""), header_block)

    target_area_analysis = {
        "area_code": area.area_code,
        "area_name": area.area_name,
        "industry_code": industry_code_value,
        "industry_name": industry_name_value,
        "score_source": fit_stats.get("score_source"),
        "decision_label": fit_stats.get("decision_label"),
        "official_axes": axes,
        "score_coverage_tier": fit_stats.get("score_coverage_tier"),
        "missing_axes": fit_stats.get("missing_axes"),
        "coverage_reason": fit_stats.get("coverage_reason"),
        "budget_fit": fit_stats.get("budget_fit"),
    }
    action_plan = interpretation.get("action_plan") or [
        "후보 매물을 볼 때 유동인구 시간대와 실제 보행 동선을 분리해서 확인하세요.",
        "같은 업종 경쟁점의 가격대, 회전율, 대기 여부를 후보 상권별로 비교하세요.",
        "임대 조건과 계약 구조는 마지막 단계에서 별도 견적으로 확인하세요.",
    ]
    radar_metrics = []
    for subject, axis_key in AXIS_SUBJECT_MAP.items():
        value = axes.get(axis_key.replace("axis_", ""))
        radar_metrics.append(
            {
                "subject": subject,
                # 기존 숫자 radar 소비자가 점수를 다시 노출하지 않도록 비운다.
                # 새 소비자는 backend가 산정한 grades만 사용한다.
                "scores": {area.area_name: None},
                "grades": {area.area_name: _score_grade(value) if value is not None else None},
            }
        )
    detailed_report = {
        "type": "single",
        "area_code": area.area_code,
        "area_name": area.area_name,
        "industry_code": industry_code_value,
        "industry_name": industry_name_value,
        "radar_metrics": radar_metrics,
        "user_condition": condition.model_dump(),
        "target_area_analysis": target_area_analysis,
        "fit_score": fit_stats["score"],
        "score_breakdown": {
            "budget_score": fit_stats.get("budget_score"),
            "business_score": fit_stats.get("business_score"),
            "operation_score": fit_stats.get("operation_score"),
            "competition_score": fit_stats.get("competition_score"),
            "rent_score": fit_stats.get("rent_score"),
            "budget_fit": fit_stats.get("budget_fit"),
        },
        "risk_summary": interpretation.get("risk_factors") or risks,
        "alternative_areas": _compact_alternatives(alternatives),
        "recommended_strategy": action_plan,
        "disclaimer": "",
        "narrative_title": interpretation.get("narrative_title", ""),
        "executive_interpretation": interpretation.get("executive_interpretation", ""),
        "score_interpretation": interpretation.get("score_interpretation", ""),
        "axis_interpretations": interpretation.get("axis_interpretations", []),
        "evidence_basis": interpretation.get("evidence_basis", []),
        "source_citations": interpretation.get("source_citations", []),
        "claim_source_map": interpretation.get("claim_source_map", []),
        "news_evidence": interpretation.get("news_evidence", []),
        "methodology_notes": interpretation.get("methodology_notes", []),
        "action_plan": action_plan,
        "onsite_checklist": interpretation.get("onsite_checklist", []),
        "limitations": interpretation.get("limitations", []),
        "visualization_data": interpretation.get("visualization_data", []),
        "markdown_body": markdown_body,
        "header_block": header_block,
        "thesis": interpretation.get("thesis", []),
        "trend_analysis": interpretation.get("trend_analysis", ""),
        "alternatives": interpretation.get("alternatives", []),
        "user_fit": interpretation.get("user_fit", ""),
        "chart_manifest": interpretation.get("chart_manifest", []),
        "original_validation_issues": interpretation.get("original_validation_issues", []),
        "validation_issues": interpretation.get("validation_issues", []),
        "quality_warnings": interpretation.get("quality_warnings", []),
        "quality_status": interpretation.get("quality_status", "unchecked"),
        "generation_mode": interpretation.get("generation_mode", "deterministic"),
        "fallback_fields": interpretation.get("fallback_fields", []),
        "facts_lite_display": interpretation.get("facts_lite_display", {}),
        "facts_pack_display": interpretation.get("facts_pack_display", {}),
        "indicator_pack": interpretation.get("indicator_pack", {}),
        "evidence_frames": interpretation.get("evidence_frames", []),
        "section_repair_log": interpretation.get("section_repair_log", []),
        "token_usage": interpretation.get("token_usage", {}),
        "cache_meta": interpretation.get("cache_meta", {}),
        "ai_model": interpretation.get("ai_model"),
        "ai_generated": bool(interpretation.get("ai_generated")),
        "overall_summary": interpretation.get("executive_interpretation", ""),
        "location_suitability": interpretation.get("score_interpretation", ""),
        "business_suitability": interpretation.get("summary", ""),
        "budget_adequacy": "예산은 실제 임대 조건과 견적을 기준으로 다시 확인해야 합니다.",
        "swot_pros": interpretation.get("strengths", []),
        "swot_cons": interpretation.get("weaknesses", []),
    }
    compact_text, compact_violations, compact_ai_generated = generate_compact_response(
        detailed_report.get("facts_lite_display") or {}
    )
    detailed_report["compact_response_text"] = compact_text
    detailed_report["compact_validation_issues"] = compact_violations
    detailed_report["compact_ai_generated"] = compact_ai_generated
    return detailed_report, area, []


def _save_report(
    db: Session,
    current_user: Optional[User],
    condition: UserBusinessCondition,
    report: dict,
) -> int | None:
    item = ChatbotHistory(
        user_id=current_user.id if current_user else None,
        area_name=condition.area_name,
        business_type=condition.business_type,
        budget=condition.budget,
        result_data=json.dumps(report, ensure_ascii=False),
        created_at=datetime.now().isoformat(),
    )
    db.add(item)
    db.commit()
    db.refresh(item)

    try:
        artifacts = publish_report_artifacts(f"chat_{item.id}", report)
        report["artifact_paths"] = artifacts
        report["chart_urls"] = {
            chart_id: f"/api/chatbot/history/{item.id}/charts/{chart_id}"
            for chart_id in ["C1", "C2", "C3", "C4", "C5"]
        }
        report["download_urls"] = {
            "pdf": f"/api/chatbot/history/{item.id}/download?format=pdf",
        }
        item.result_data = json.dumps(report, ensure_ascii=False)
        db.commit()
    except Exception as exc:
        report["artifact_error"] = str(exc)

    if current_user:
        saved = SavedReport(
            user_id=current_user.id,
            report_data=json.dumps(report, ensure_ascii=False),
            created_at=datetime.now().isoformat(),
        )
        db.add(saved)
        db.commit()
        db.refresh(saved)
        try:
            publish_report_artifacts(saved.id, report)
        except Exception as exc:
            report["artifact_error"] = str(exc)
    return item.id


@router.post("/analyze", response_model=ChatbotResponse)
def analyze_chatbot(
    request: ChatbotRequest,
    commercial_area_service: CommercialAreaService = Depends(get_commercial_area_service),
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    condition = UserBusinessCondition(
        area_name=request.area_name,
        business_type=request.business_type,
        budget=request.budget,
    )
    with track_token_usage() as usage:
        report, area, options = _build_report_payload(commercial_area_service, condition)
    if report is None:
        raise HTTPException(status_code=400, detail={"message": "industry unresolved", "options": options})
    report_id = _save_report(db, current_user, condition, report)

    if usage.total_tokens > 0:
        log_entry = TokenUsageLog(
            user_id=current_user.id if current_user else None,
            model_name=usage.model_name or get_openai_model(),
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            estimated_cost=calculate_token_cost(usage.model_name or get_openai_model(), usage.prompt_tokens, usage.completion_tokens),
            feature_name="chatbot_analysis",
            status="degraded" if report.get("generation_mode") == "deterministic" else "success",
            generation_mode=report.get("generation_mode"),
            quality_status=report.get("quality_status"),
            original_validation_issues_json=(
                json.dumps(report.get("original_validation_issues"), ensure_ascii=False)
                if report.get("original_validation_issues")
                else None
            ),
            created_at=datetime.now().isoformat()
        )
        db.add(log_entry)
        db.commit()

    return _chatbot_response(area, condition, report, report_id, current_user is None)



def _chatbot_response(
    area,
    condition: UserBusinessCondition,
    report: dict,
    report_id: int | None,
    is_guest: bool,
) -> ChatbotResponse:
    industry_label = condition.business_type or "상권 맥락"
    return ChatbotResponse(
        area_code=area.area_code,
        area_name=area.area_name,
        compact_response=CompactResponse(
            condition_summary=f"{condition.area_name} / {industry_label} / {_budget_band(condition.budget)}",
            quick_judgement=report.get("compact_response_text") or _compact_text_from_report(report),
            main_risks=(report.get("risk_summary") or [])[:3],
            alternative_areas=report["alternative_areas"],
            cta="상세 리포트에서 핵심 판단 근거, 데이터 출처, 현장 검증 순서를 확인할 수 있어요.",
            report_id=report_id,
            has_detailed_report=report_id is not None,
            ai_explanation=report.get("compact_response_text") or _compact_text_from_report(report),
            evidence_basis=report.get("evidence_basis", []),
            source_citations=report.get("source_citations", []),
            recommended_strategy=report.get("action_plan") or report.get("recommended_strategy", []),
        ),
        report_id=report_id,
        condition=condition,
        actions=[
            ChatbotAction(label="상권 상세 보기", type="link", target=f"/trade?area={area.area_code}"),
            ChatbotAction(label="상세 리포트 보기", type="link", target=f"/reports/{report_id}" if report_id else "#"),
        ],
        is_guest=is_guest,
        message="게스트 모드에서는 상담 기록이 임시 저장됩니다." if is_guest else None,
    )


def _last_report_facts(db: Session, current_user: Optional[User], state: ChatState) -> tuple[dict, dict]:
    if not state.last_report_id:
        return {}, {}
    last = db.query(ChatbotHistory).filter(ChatbotHistory.id == state.last_report_id).first()
    if not last:
        return {}, {}
    if last.user_id is not None and (current_user is None or last.user_id != current_user.id):
        return {}, {}
    last_data = _loads_result(last.result_data)
    return last_data.get("facts_lite_display") or {}, last_data.get("facts_pack_display") or {}


def _area_chat_facts(service: CommercialAreaService, state: ChatState) -> dict:
    if not state.area_code:
        return {}
    quarter = service.latest_quarter()
    area = service.repository.get_by_code(state.area_code)
    summary = service._area_summary(state.area_code) or {}
    industry_rule = service._rule_score(state.area_code, state.industry_code) if state.industry_code else None

    totals = service.db.execute(
        text(
            """
            SELECT
              (SELECT SUM(floating_population) FROM district_floating WHERE area_code = :area_code AND timestamp = :quarter) AS floating_population,
              (SELECT SUM(store_count) FROM district_store_count WHERE area_code = :area_code AND timestamp = :quarter) AS store_count,
              (SELECT SUM(sales_amount) FROM district_sales WHERE area_code = :area_code AND timestamp = :quarter) AS sales_amount
            """
        ),
        {"area_code": state.area_code, "quarter": quarter},
    ).mappings().first()

    top_sales = service.db.execute(
        text(
            """
            SELECT industry_name, sales_amount
            FROM district_sales
            WHERE area_code = :area_code AND timestamp = :quarter
            ORDER BY sales_amount DESC
            LIMIT 5
            """
        ),
        {"area_code": state.area_code, "quarter": quarter},
    ).mappings().all()

    top_stores = service.db.execute(
        text(
            """
            SELECT industry_name, store_count
            FROM district_store_count
            WHERE area_code = :area_code AND timestamp = :quarter
            ORDER BY store_count DESC
            LIMIT 5
            """
        ),
        {"area_code": state.area_code, "quarter": quarter},
    ).mappings().all()

    def grade_display(value) -> str | None:
        return _display_grade(value) or _score_grade(value)

    def count_display(value, unit: str) -> str | None:
        try:
            return f"{float(value):,.0f}{unit}"
        except (TypeError, ValueError):
            return None

    def sales_display(value) -> str | None:
        try:
            return f"{float(value) / 100_000_000:,.1f}억원"
        except (TypeError, ValueError):
            return None

    quarter_text = str(quarter or "")
    period = f"{quarter_text[:4]}년 {quarter_text[4:]}분기" if len(quarter_text) == 5 else quarter_text
    totals_dict = dict(totals or {})
    return {
        "기준 기간": period or "가용 최신 분기",
        "선택 상권": state.area_name or getattr(area, "area_name", ""),
        "선택 업종": state.business_type,
        "예산": f"{state.budget:,}만원" if state.budget else None,
        "상권 요약": {
            "수요·접근성 맥락 등급": _display_grade(summary.get("display_grade")),
            "대표 업종": None,
            "대표 업종 상태": summary.get("top_industry_status") or "교차업종 보정 전 보류",
        },
        "선택 업종 평가": {
            "공식 입지 등급": service._industry_display_grade(industry_rule) if industry_rule else None,
            "가용 축 맥락 등급": grade_display(industry_rule.get("context_location_score")) if industry_rule else None,
            "점수 범위": industry_rule.get("score_coverage_tier") if industry_rule else None,
            "결측 축": industry_rule.get("missing_axes") if industry_rule else None,
            "판단": industry_rule.get("decision_label") if industry_rule else None,
            "시장성": grade_display(industry_rule.get("axis_sales")) if industry_rule else None,
            "경쟁 구조": grade_display(industry_rule.get("axis_competition")) if industry_rule else None,
            "수요 기반": grade_display(industry_rule.get("axis_demand")) if industry_rule else None,
            "접근·유입": grade_display(industry_rule.get("axis_accessibility")) if industry_rule else None,
            "비용 여건 등급": grade_display(industry_rule.get("cost_risk_score")) if industry_rule else None,
        },
        "상권 전체 집계": {
            "유동인구": count_display(totals_dict.get("floating_population"), "명"),
            "점포 수": count_display(totals_dict.get("store_count"), "개"),
            "매출액": sales_display(totals_dict.get("sales_amount")),
        },
        "매출 상위 업종": [
            {"업종": row.get("industry_name"), "매출액": sales_display(row.get("sales_amount"))}
            for row in top_sales
        ],
        "점포 수 상위 업종": [
            {"업종": row.get("industry_name"), "점포 수": count_display(row.get("store_count"), "개")}
            for row in top_stores
        ],
        "해석 주의": "상권 단위 집계값이며 개별 점포의 실적이나 임대료를 뜻하지 않음",
    }


def _conversation_options(items: list[str]) -> list[ChatOption]:
    options: list[ChatOption] = []
    for item in items:
        value = " ".join(str(item).split()).strip()
        if not value or any(option.value == value for option in options):
            continue
        options.append(ChatOption(label=value, type="question", value=value))
    return options[:3]


@router.post("/chat", response_model=ChatReplyResponse)
def handle_chat(
    request: ChatRequest,
    commercial_area_service: CommercialAreaService = Depends(get_commercial_area_service),
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    with track_token_usage() as usage:
        result = _handle_chat_impl(request, commercial_area_service, db, current_user)
    if usage.total_tokens > 0:
        log_entry = TokenUsageLog(
            user_id=current_user.id if current_user else None,
            model_name=usage.model_name or get_openai_model(),
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            estimated_cost=calculate_token_cost(usage.model_name or get_openai_model(), usage.prompt_tokens, usage.completion_tokens),
            feature_name="chatbot_chat",
            created_at=datetime.now().isoformat()
        )
        db.add(log_entry)
        db.commit()
    return result


def _handle_chat_impl(
    request: ChatRequest,
    commercial_area_service: CommercialAreaService,
    db: Session,
    current_user: Optional[User],
) -> ChatReplyResponse:
    message = request.message.strip()
    if not message:
        options = starter_options()
        return ChatReplyResponse(
            type="text",
            text="궁금한 상권을 먼저 물어봐도 되고, 업종이나 예산까지 같이 말해도 돼요.",
            options=[item.label for item in options],
            option_payloads=options,
            state=request.state or ChatState(),
            pending_slot="area",
            missing_fields=["area", "industry", "budget"],
            is_guest=current_user is None,
        )

    state, missing, option_payloads, pending_slot, decision = merge_state(message, commercial_area_service, request.state)

    if is_help_intent(message) and not request.state:
        options = starter_options()
        return ChatReplyResponse(
            type="text",
            text="입지봇은 먼저 상권이 어떤 곳인지 대화로 풀어주고, 사용자가 원할 때만 상세 리포트를 만듭니다. 예를 들어 '이태원 관광특구는 어떤 상권이야?'처럼 물어보거나, '명동 카페 1억으로 리포트 만들어줘'처럼 요청할 수 있어요.",
            options=[item.label for item in options],
            option_payloads=options,
            state=state,
            pending_slot=pending_slot,
            missing_fields=missing,
            is_guest=current_user is None,
        )

    history_tail = [{"role": item.role, "content": item.content} for item in (request.history or [])[-10:]]
    if history_tail and history_tail[-1].get("role") == "user" and history_tail[-1].get("content", "").strip() == message:
        history_tail = history_tail[:-1]

    if not decision.wants_report:
        facts_lite, facts_pack = _last_report_facts(db, current_user, state)
        area_facts = _area_chat_facts(commercial_area_service, state) if state.area_code else {}
        conversation_state = state.model_dump()
        if option_payloads:
            conversation_state["unresolved_choices"] = [item.label for item in option_payloads]
        answer, suggested_questions, _issues, _ai_generated = answer_conversation(
            message,
            history_tail,
            state=conversation_state,
            area_facts=area_facts,
            facts_lite_display=facts_lite,
            facts_pack_display=facts_pack,
        )
        suggested = option_payloads or _conversation_options(suggested_questions)
        return ChatReplyResponse(
            type="text",
            text=answer,
            options=[item.label for item in suggested],
            option_payloads=suggested,
            state=state,
            missing_fields=missing,
            pending_slot=pending_slot,
            is_guest=current_user is None,
        )

    if option_payloads:
        return ChatReplyResponse(
            type="text",
            text="리포트에 사용할 대상을 하나로 확정해야 해요. 아래 후보 중 의도한 항목을 골라주세요.",
            options=[item.label for item in option_payloads],
            option_payloads=option_payloads,
            state=state,
            missing_fields=missing,
            pending_slot=pending_slot,
            is_guest=current_user is None,
        )

    if not state.area_code:
        return ChatReplyResponse(
            type="text",
            text=build_missing_slot_text(state, ["area"]),
            state=state,
            missing_fields=["area"],
            pending_slot="area",
            is_guest=current_user is None,
        )

    condition = _state_to_condition(state)
    report, area, options = _build_report_payload(
        commercial_area_service,
        condition,
        area_code=state.area_code,
        industry_code=state.industry_code,
    )
    if report is None:
        option_payloads = [
            {
                "label": f"{item['industry_name']} ({item['industry_code']})",
                "type": "industry",
                "value": item["industry_name"],
                "payload": {"industry_code": item["industry_code"], "business_type": item["industry_name"]},
            }
            for item in options
        ]
        return ChatReplyResponse(
            type="text",
            text="업종을 하나로 확정하지 못했어요. 아래 후보 중에서 골라주면 바로 이어서 분석할게요.",
            options=[item["label"] for item in option_payloads],
            option_payloads=option_payloads,
            state=state,
            missing_fields=["industry"],
            pending_slot="industry",
            is_guest=current_user is None,
        )

    report_id = _save_report(db, current_user, condition, report)
    state.last_report_id = report_id
    return ChatReplyResponse(
        type="report",
        report=_chatbot_response(area, condition, report, report_id, current_user is None),
        state=state,
        is_guest=current_user is None,
        message="게스트 모드에서는 상담 기록이 임시 저장됩니다." if current_user is None else None,
    )
