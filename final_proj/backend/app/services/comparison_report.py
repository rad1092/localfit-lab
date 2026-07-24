from __future__ import annotations

from app.repositories.commercial_area import CommercialAreaRepository
from app.schemas.commercial_area import AIComparisonResponse
from app.services.commercial_area import AXIS_SUBJECT_MAP, CommercialAreaService
from app.services.indicator_pack import _score_grade
from app.services.interpretive_report import interpret_comparison_report


class ComparisonReportService:
    """Generate comparison reports from rule-engine scores, then interpret them."""

    def __init__(self, repository: CommercialAreaRepository):
        self.repository = repository
        self.area_service = CommercialAreaService(repository)

    def generate(self, area_codes: list[str]) -> AIComparisonResponse | None:
        areas = []
        radar_scores = {subject: {} for subject in AXIS_SUBJECT_MAP}

        for code in area_codes:
            db_item = self.repository.get_by_code(code)
            if not db_item:
                continue
            summary = self.area_service._area_summary(code)
            axes = self.area_service._area_axis_summary(code)
            score_value = summary.get("score") if summary else None
            score = float(score_value) if score_value is not None else None
            axis_display_grades = {
                "sales": _score_grade(axes.get("axis_sales")),
                "competition": _score_grade(axes.get("axis_competition")),
                "demand": _score_grade(axes.get("axis_demand")),
                "accessibility": _score_grade(axes.get("axis_accessibility")),
            }
            areas.append(
                {
                    "area_code": db_item.area_code,
                    "area_name": db_item.area_name,
                    "score": score,
                    "grade": summary.get("grade") if summary else None,
                    "display_grade": summary.get("display_grade") if summary else None,
                    "axis_display_grades": axis_display_grades,
                    "axes": axes,
                    "score_source": "rule_area_score_summary.area_context_2axis",
                    "score_version": summary.get("score_version") if summary else None,
                    "available_axis_count": 2,
                    "missing_axes": ["sales", "competition"],
                }
            )

            for subject, axis in AXIS_SUBJECT_MAP.items():
                axis_value = axes.get(axis)
                radar_scores[subject][db_item.area_name] = (
                    round(float(axis_value), 2) if axis_value is not None else None
                )

        if not areas:
            return None

        areas.sort(key=lambda item: (item["score"] is not None, item["score"] or float("-inf")), reverse=True)
        top = next((item for item in areas if item["score"] is not None), None)

        swot = []
        for area in areas:
            swot.append(
                {
                    "area_name": area["area_name"],
                    "pros": ["먼저 볼 축과 현장 대조 축을 분리해서 비교합니다."],
                    "cons": ["임대 조건과 업종별 경쟁 상황은 별도 확인이 필요합니다."],
                }
            )

        payload = {
            "quarter": self.area_service.latest_quarter(),
            "areas": areas,
            "score_contract": "area_context_2axis",
            "method_basis": [
                "Comparison uses the area-only mean of demand and accessibility axes.",
                "Sales and competition remain null; they are not replaced with zero.",
                "This is an area-context comparison, not an official industry score, grade, or recommendation.",
            ],
        }
        interpretation = interpret_comparison_report(payload)

        return AIComparisonResponse(
            summary=interpretation.get("summary") or (
                f"{top['area_name']}을 수요·접근성 맥락 기준으로 먼저 비교합니다."
                if top
                else "수요·접근성 맥락 점수가 없어 우선순위를 보류합니다."
            ),
            top_recommendation_name=top["area_name"] if top else "우선 후보 보류",
            top_recommendation_reason=interpretation.get("top_recommendation_reason")
            or (
                f"{top['area_name']}은 수요·접근성 2축 맥락 기준에서 먼저 비교할 후보입니다."
                if top
                else "2축 맥락 점수가 없어 공식 추천을 만들지 않습니다."
            ),
            swot_analysis=swot,
            radar_metrics=[{"subject": subject, "scores": scores} for subject, scores in radar_scores.items()],
            narrative_title=interpretation.get("narrative_title", ""),
            executive_interpretation=interpretation.get("executive_interpretation", ""),
            comparison_matrix=interpretation.get("comparison_matrix", []),
            evidence_basis=interpretation.get("evidence_basis", []),
            source_citations=interpretation.get("source_citations", []),
            methodology_notes=interpretation.get("methodology_notes", []),
            action_plan=interpretation.get("action_plan", []),
            limitations=interpretation.get("limitations", []),
            visualization_data=interpretation.get("visualization_data", []),
            markdown_body=interpretation.get("markdown_body", ""),
            ai_model=interpretation.get("ai_model"),
            ai_generated=bool(interpretation.get("ai_generated")),
        )

    def get_comparison(self, area_codes: list[str]) -> AIComparisonResponse | None:
        return self.generate(area_codes)
