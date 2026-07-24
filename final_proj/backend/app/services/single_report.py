from __future__ import annotations

from typing import Any

from app.repositories.commercial_area import CommercialAreaRepository
from app.schemas.commercial_area import AIAnalysisResponse
from app.services.commercial_area import AXIS_SUBJECT_MAP, CommercialAreaService
from app.services.interpretive_report import interpret_single_report


class SingleReportService:
    """Generate one area or area+industry report from rule facts, then interpret it."""

    def __init__(self, repository: CommercialAreaRepository):
        self.repository = repository
        self.area_service = CommercialAreaService(repository)

    def _top_industries(self, area_code: str, limit: int = 5) -> list[str]:
        # 업종별 백분위 점수는 업종 사이의 기대성과 척도가 아니다. 교차업종 보정 모델이
        # 준비되기 전에는 대표/추천 업종을 만들지 않는다.
        return []

    def _radar_metrics(self, area_name: str, axes: dict[str, Any]) -> list[dict]:
        return [
            {
                "subject": subject,
                "scores": {
                    area_name: round(float(axes[axis]), 1) if axes.get(axis) is not None else None
                },
            }
            for subject, axis in AXIS_SUBJECT_MAP.items()
        ]

    def _rule_payload(
        self,
        *,
        area_code: str,
        area_name: str,
        resolved: dict[str, Any] | None,
        rule: dict[str, Any] | None,
        summary: dict[str, Any] | None,
        axes: dict[str, Any],
        score: float | None,
        score_source: str,
        top_industries: list[str],
        budget: int | None = None,
    ) -> dict[str, Any]:
        return {
            "quarter": self.area_service.latest_quarter(),
            "area_code": area_code,
            "area_name": area_name,
            "industry_code": resolved["industry_code"] if resolved else None,
            "industry_name": resolved["industry_name"] if resolved else "상권 맥락",
            "score": round(float(score), 2) if score is not None else None,
            "context_location_score": rule.get("context_location_score") if rule else summary.get("score") if summary else None,
            "grade": rule.get("grade") if rule else summary.get("grade") if summary else None,
            "display_grade": (
                self.area_service._industry_display_grade(rule)
                if rule
                else summary.get("display_grade")
                if summary
                else None
            ),
            "decision_label": rule.get("decision_label") if rule else "상권 수요·접근성 맥락 등급" if summary else None,
            "score_source": score_source,
            "score_version": rule.get("score_version") if rule else summary.get("score_version") if summary else None,
            "axes": {
                "axis_sales": float(axes["axis_sales"]) if (axes or {}).get("axis_sales") is not None else None,
                "axis_competition": float(axes["axis_competition"]) if (axes or {}).get("axis_competition") is not None else None,
                "axis_demand": float(axes["axis_demand"]) if (axes or {}).get("axis_demand") is not None else None,
                "axis_accessibility": float(axes["axis_accessibility"]) if (axes or {}).get("axis_accessibility") is not None else None,
            },
            "score_coverage_tier": rule.get("score_coverage_tier") if rule else "area_context",
            "available_axis_count": rule.get("available_axis_count") if rule else 2,
            "missing_axes": rule.get("missing_axes") if rule else "sales,competition",
            "coverage_reason": rule.get("coverage_reason") if rule else summary.get("score_definition") if summary else None,
            "official_rank_eligible": bool(rule.get("official_rank_eligible")) if rule else False,
            "extra_signals": {
                "cost_risk_score": (axes or {}).get("cost_risk_score"),
                "data_reliability_score": (axes or {}).get("data_reliability_score"),
                "conservative_score_owa": (axes or {}).get("conservative_score_owa"),
                "growth_potential_score": (axes or {}).get("growth_potential_score"),
                "growth_rebound_candidate_score": (axes or {}).get("growth_rebound_candidate_score"),
            },
            "user_condition": {
                "area_name": area_name,
                "business_type": resolved["industry_name"] if resolved else "상권 맥락",
                "budget": budget or None,
            },
            "budget_fit": self.area_service._budget_fit_overlay(area_code, budget),
            "top_industries": top_industries,
            "method_basis": [
                "Official current-location scoring is available only when all four WLC/MCDA axes are observed and taxonomy permits direct scoring.",
                "Three-axis results remain context-only and do not receive an official total, grade, or rank.",
                "Cost, growth, rebound, candidate evidence, and data reliability are interpreted separately.",
                "The LLM explains the hidden rule score and must not compute a new score.",
            ],
        }

    def generate(self, area_code: str, business_type: str | None = None, budget: int | None = None) -> AIAnalysisResponse | None:
        db_item = self.repository.get_by_code(area_code)
        if not db_item:
            return None

        summary = self.area_service._area_summary(area_code)
        resolved = self.area_service.resolve_industry(business_type)
        rule = self.area_service._rule_score(area_code, resolved["industry_code"]) if resolved else None
        axes = rule or self.area_service._area_axis_summary(area_code)

        score_value = rule.get("current_location_score") if rule else summary.get("score") if summary else None
        score = float(score_value) if score_value is not None else None
        score_source = (
            "rule_location_score.full_4axis"
            if rule and score is not None
            else "rule_location_score.context_only"
            if rule
            else "rule_area_score_summary.area_context"
        )
        top_industries = self._top_industries(area_code)
        payload = self._rule_payload(
            area_code=area_code,
            area_name=db_item.area_name,
            resolved=resolved,
            rule=rule,
            summary=summary,
            axes=axes,
            score=score,
            score_source=score_source,
            top_industries=top_industries,
            budget=budget,
        )
        interpretation = interpret_single_report(payload)

        return AIAnalysisResponse(
            summary=interpretation.get("summary") or f"{db_item.area_name} 조건을 해석했습니다.",
            strengths=interpretation.get("strengths") or ["먼저 검토할 근거축을 중심으로 후보를 읽습니다."],
            weaknesses=interpretation.get("weaknesses") or ["현장 대조가 필요한 축을 따로 확인합니다."],
            recommended_businesses=top_industries,
            risk_factors=interpretation.get("risk_factors") or ["비용과 계약 조건은 별도 확인합니다."],
            opportunity_score=round(score, 1) if score is not None else None,
            radar_metrics=self._radar_metrics(db_item.area_name, axes),
            industry_code=resolved["industry_code"] if resolved else None,
            industry_name=resolved["industry_name"] if resolved else "상권 맥락",
            score_source=score_source,
            header_block=interpretation.get("header_block", {}),
            narrative_title=interpretation.get("narrative_title", ""),
            thesis=interpretation.get("thesis", []),
            executive_interpretation=interpretation.get("executive_interpretation", ""),
            score_interpretation=interpretation.get("score_interpretation", ""),
            axis_interpretations=interpretation.get("axis_interpretations", []),
            trend_analysis=interpretation.get("trend_analysis", ""),
            alternatives=interpretation.get("alternatives", []),
            user_fit=interpretation.get("user_fit", ""),
            evidence_basis=interpretation.get("evidence_basis", []),
            source_citations=interpretation.get("source_citations", []),
            claim_source_map=interpretation.get("claim_source_map", []),
            methodology_notes=interpretation.get("methodology_notes", []),
            action_plan=interpretation.get("action_plan", []),
            onsite_checklist=interpretation.get("onsite_checklist", []),
            limitations=interpretation.get("limitations", []),
            chart_manifest=interpretation.get("chart_manifest", []),
            original_validation_issues=interpretation.get("original_validation_issues", []),
            validation_issues=interpretation.get("validation_issues", []),
            quality_warnings=interpretation.get("quality_warnings", []),
            quality_status=interpretation.get("quality_status")
            or ("pass" if interpretation.get("ai_generated") else "unchecked"),
            generation_mode=interpretation.get("generation_mode")
            or ("llm" if interpretation.get("ai_generated") else "deterministic"),
            fallback_fields=interpretation.get("fallback_fields", []),
            facts_pack_display=interpretation.get("facts_pack_display", {}),
            facts_lite_display=interpretation.get("facts_lite_display", {}),
            indicator_pack=interpretation.get("indicator_pack", {}),
            evidence_frames=interpretation.get("evidence_frames", []),
            news_evidence=interpretation.get("news_evidence", []),
            section_repair_log=interpretation.get("section_repair_log", []),
            token_usage=interpretation.get("token_usage", {}),
            cache_meta=interpretation.get("cache_meta", {}),
            visualization_data=interpretation.get("visualization_data", []),
            markdown_body=interpretation.get("markdown_body", ""),
            ai_model=interpretation.get("ai_model"),
            ai_generated=bool(interpretation.get("ai_generated")),
        )

    def get_recommendations(self, code: str, business_type: str | None = None) -> AIAnalysisResponse | None:
        return self.generate(code, business_type=business_type)
