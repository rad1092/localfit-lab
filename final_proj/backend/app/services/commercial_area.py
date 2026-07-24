from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from sqlalchemy import text

from app.repositories.commercial_area import CommercialAreaRepository
from app.schemas.commercial_area import (
    CommercialAreaResponse,
    DashboardSummaryResponse,
    IndustryAnalysisResponse,
    IndustryAxisAnalysis,
    IndustryAxisMetric,
    IndustryQuarterHistory,
    RankingResponse,
)


AXIS_SUBJECT_MAP = {
    "매출": "axis_sales",
    "경쟁환경": "axis_competition",
    "수요": "axis_demand",
    "접근성": "axis_accessibility",
}
EXPECTED_COVERAGE_SCORE_VERSION = "loc_score.v2.6-coverage-contract-rc1"
AREA_CONTEXT_SCORE_VERSION = "area_context.demand_accessibility.v1"


def _nullable_float(value: Any) -> float | None:
    return float(value) if value is not None else None


def _axis_display_grade(value: Any) -> str | None:
    number = _nullable_float(value)
    if number is None or not 0.0 <= number <= 100.0:
        return None
    if number > 90.0:
        return "A+"
    if number > 80.0:
        return "A"
    if number > 70.0:
        return "B+"
    if number > 60.0:
        return "B"
    if number > 50.0:
        return "C+"
    if number > 40.0:
        return "C"
    if number > 30.0:
        return "D+"
    if number > 20.0:
        return "D"
    if number > 10.0:
        return "E+"
    return "E"


class CommercialAreaService:
    """Shared commercial-area lookup and rule-score access."""

    def __init__(self, repository: CommercialAreaRepository):
        self.repository = repository

    @property
    def db(self):
        return self.repository.db

    def latest_quarter(self) -> str:
        row = self.db.execute(text("SELECT MAX(quarter) AS quarter FROM rule_location_score")).mappings().first()
        return str(row["quarter"]) if row and row["quarter"] else "20261"

    def _area_summary(self, area_code: str) -> dict[str, Any] | None:
        quarter = self.latest_quarter()
        row = self.db.execute(
            text(
                """
                SELECT *
                FROM rule_area_score_summary
                WHERE quarter = :quarter AND area_code = :area_code
                """
            ),
            {"quarter": quarter, "area_code": str(area_code)},
        ).mappings().first()
        if not row:
            return None
        summary = dict(row)
        summary.update(self._area_context_grade_info(area_code, quarter=quarter))
        if summary.get("score_version") == AREA_CONTEXT_SCORE_VERSION:
            return summary

        # A reload server may briefly read a pre-v2.6 summary whose score was an
        # industry-score aggregate. Rebuild only the explicit two-axis context
        # value at the service boundary; never relabel the legacy aggregate.
        axes = self._area_axis_summary(area_code)
        demand = axes.get("axis_demand")
        accessibility = axes.get("axis_accessibility")
        summary["source_score_version"] = summary.get("score_version")
        summary["score"] = (
            round((float(demand) + float(accessibility)) / 2.0, 2)
            if demand is not None and accessibility is not None
            else None
        )
        summary["score_version"] = f"{AREA_CONTEXT_SCORE_VERSION}.runtime_legacy_bridge"
        summary["score_definition"] = "area_context_demand_accessibility_mean_v1_runtime_legacy_bridge"
        summary["top_industry_code"] = None
        summary["top_industry_name"] = None
        return summary

    def _area_context_grade_info(
        self,
        area_code: str,
        *,
        quarter: str | None = None,
    ) -> dict[str, str | None]:
        rows = self._area_context_score_rows(
            limit=1,
            include_area_code=str(area_code),
            quarter=quarter,
        )
        row = rows[0] if rows else None
        return {
            "grade": str(row["grade"]) if row and row.get("grade") else None,
            "display_grade": str(row["display_grade"]) if row and row.get("display_grade") else None,
        }

    def _area_context_grade(self, area_code: str) -> str | None:
        return self._area_context_grade_info(area_code).get("grade")

    def _area_context_display_grade(self, area_code: str) -> str | None:
        return self._area_context_grade_info(area_code).get("display_grade")

    def _area_axis_summary(self, area_code: str) -> dict[str, float | None]:
        row = self.db.execute(
            text(
                """
                SELECT
                    NULL AS axis_sales,
                    NULL AS axis_competition,
                    MAX(axis_demand) AS axis_demand,
                    MAX(axis_accessibility) AS axis_accessibility,
                    MAX(cost_risk_score) AS cost_risk_score,
                    NULL AS data_reliability_score
                FROM rule_location_score
                WHERE quarter = :quarter
                  AND area_code = :area_code
                """
            ),
            {"quarter": self.latest_quarter(), "area_code": str(area_code)},
        ).mappings().first()
        return {k: _nullable_float(v) for k, v in dict(row or {}).items()}

    def _area_context_score_rows(
        self,
        *,
        limit: int,
        exclude_area_code: str | None = None,
        include_area_code: str | None = None,
        quarter: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = self.db.execute(
            text(
                """
                WITH area_context AS (
                    SELECT
                        score.area_code,
                        COALESCE(MAX(NULLIF(score.area_name, '')), MAX(area.area_name)) AS area_name,
                        (MAX(score.axis_demand) + MAX(score.axis_accessibility)) / 2.0 AS score
                    FROM rule_location_score AS score
                    LEFT JOIN commercial_area AS area ON area.area_code = score.area_code
                    WHERE score.quarter = :quarter
                    GROUP BY score.area_code
                    HAVING MAX(score.axis_demand) IS NOT NULL
                       AND MAX(score.axis_accessibility) IS NOT NULL
                ), ranked AS (
                    SELECT
                        area_code,
                        area_name,
                        score,
                        CUME_DIST() OVER (ORDER BY score) AS score_percentile
                    FROM area_context
                )
                SELECT
                    area_code,
                    area_name,
                    score,
                    CASE
                        WHEN score_percentile > 0.8 THEN 'A'
                        WHEN score_percentile > 0.6 THEN 'B'
                        WHEN score_percentile > 0.4 THEN 'C'
                        WHEN score_percentile > 0.2 THEN 'D'
                        ELSE 'E'
                    END AS grade,
                    CASE
                        WHEN score_percentile > 0.9 THEN 'A+'
                        WHEN score_percentile > 0.8 THEN 'A'
                        WHEN score_percentile > 0.7 THEN 'B+'
                        WHEN score_percentile > 0.6 THEN 'B'
                        WHEN score_percentile > 0.5 THEN 'C+'
                        WHEN score_percentile > 0.4 THEN 'C'
                        WHEN score_percentile > 0.3 THEN 'D+'
                        WHEN score_percentile > 0.2 THEN 'D'
                        WHEN score_percentile > 0.1 THEN 'E+'
                        ELSE 'E'
                    END AS display_grade
                FROM ranked
                WHERE (:include_area_code IS NULL OR area_code = :include_area_code)
                  AND (:exclude_area_code IS NULL OR area_code != :exclude_area_code)
                ORDER BY score DESC, area_name ASC
                LIMIT :limit
                """
            ),
            {
                "quarter": quarter or self.latest_quarter(),
                "exclude_area_code": exclude_area_code,
                "include_area_code": include_area_code,
                "limit": max(1, int(limit)),
            },
        ).mappings().all()
        return [dict(row) for row in rows]

    def _coverage_score_contract_ready(self) -> bool:
        row = self.db.execute(
            text(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN score_version = :score_version THEN 1 ELSE 0 END) AS matching
                FROM rule_location_score
                WHERE quarter = :quarter
                """
            ),
            {
                "quarter": self.latest_quarter(),
                "score_version": EXPECTED_COVERAGE_SCORE_VERSION,
            },
        ).mappings().first()
        return bool(row and int(row["total"] or 0) > 0 and int(row["matching"] or 0) == int(row["total"]))

    def _cost_references(self, area_code: str) -> dict[str, Any]:
        sale = self.db.execute(
            text(
                """
                SELECT *
                FROM area_sale_price_proxy
                WHERE area_code = :area_code AND period <= :quarter
                ORDER BY period DESC
                LIMIT 1
                """
            ),
            {"area_code": str(area_code), "quarter": self.latest_quarter()},
        ).mappings().first()

        def rone_metric_options(metric_code: str) -> list[dict[str, Any]]:
            rows = self.db.execute(
                text(
                    """
                    SELECT ref.*
                    FROM area_rone_cost_reference AS ref
                    WHERE ref.area_code = :area_code
                      AND ref.metric_code = :metric_code
                      AND ref.period = (
                          SELECT MAX(candidate.period)
                          FROM area_rone_cost_reference AS candidate
                          WHERE candidate.area_code = :area_code
                            AND candidate.metric_code = :metric_code
                            AND candidate.period <= :quarter
                      )
                    ORDER BY
                        CASE ref.mapping_scope
                            WHEN 'rone_level3_name_match_candidate' THEN 0
                            WHEN 'seoul_baseline_reference' THEN 1
                            ELSE 2
                        END,
                        CASE ref.property_type
                            WHEN '중대형 상가' THEN 0
                            WHEN '집합 상가' THEN 1
                            WHEN '소규모 상가' THEN 2
                            WHEN '일반 상가' THEN 3
                            ELSE 4
                        END,
                        ref.source_region_name ASC
                    """
                ),
                {"area_code": str(area_code), "metric_code": metric_code, "quarter": self.latest_quarter()},
            ).mappings().all()
            return [dict(row) for row in rows]

        rent_options = rone_metric_options("rent")
        vacancy_options = rone_metric_options("vacancy")
        selected_reference = {
            "rent": rent_options[0] if rent_options else None,
            "vacancy": vacancy_options[0] if vacancy_options else None,
        }

        return {
            "sale_price_proxy": dict(sale) if sale else None,
            "rent_reference": selected_reference["rent"],
            "vacancy_reference": selected_reference["vacancy"],
            "selected_reference": selected_reference,
            "reference_options": {
                "rent": rent_options,
                "vacancy": vacancy_options,
            },
            "evidence_trace": {
                "contract_status": "evidence_loader_allowed_not_promoted",
                "selection_rule": "latest_period_then_candidate_scope_then_property_type",
                "selection_groups": sorted({
                    str(row.get("selection_group"))
                    for row in [*rent_options, *vacancy_options]
                    if row.get("selection_group")
                }),
                "option_count": len(rent_options) + len(vacancy_options),
                "score_fields_withheld": True,
            },
        }

    def _budget_fit_overlay(self, area_code: str, budget_manwon: int | None) -> dict[str, Any]:
        references = self._cost_references(area_code)
        rent = references.get("rent_reference")
        budget = int(budget_manwon or 0)
        mapping_scope = str((rent or {}).get("mapping_scope") or "")
        mapped_candidate = mapping_scope == "rone_level3_name_match_candidate"
        reference_status = (
            "mapped_reference_only"
            if mapped_candidate
            else "broad_seoul_reference_only"
            if rent
            else "unknown"
        )
        base = {
            "status": reference_status,
            "budget_manwon": budget if budget > 0 else None,
            "reference_area_m2": 33.0,
            "reference_months": 12,
            "rent_period_assumption": "R-ONE 천원/㎡ 임대료를 월 단가 참고값으로 가정한 단순 산술",
            "rone_rent_reference_thousand_won_per_m2": _nullable_float((rent or {}).get("metric_value")),
            "standardized_12m_reference_manwon": None,
            "reference_to_input_budget_ratio": None,
            "budget_fit_score": None,
            "official_budget_fit_status": "withheld_evidence_only",
            "label": (
                "지역명 후보 매핑 참고 산술"
                if mapped_candidate
                else "서울 기준선 참고 산술"
                if rent
                else "R-ONE 임대료 참고값 없음"
            ),
            "reason": (
                "R-ONE은 지역명 후보 매핑 evidence이며 engine_promotion_ready=False입니다. "
                "환산액과 비율은 참고 산술일 뿐 공식 예산 적합도나 점수가 아닙니다."
                if mapped_candidate
                else "R-ONE 서울 전체 기준선이며 해당 상권의 직접 임대료가 아닙니다. "
                "환산액과 비율은 참고 산술일 뿐 공식 예산 적합도나 점수가 아닙니다."
                if rent
                else "R-ONE 임대료 참고값이 없어 참고 산술을 제공하지 않으며 공식 예산 적합도도 보류합니다."
            ),
            "mapping_scope": mapping_scope or None,
            "source_region_name": (rent or {}).get("source_region_name"),
            "direct_value_allowed": bool((rent or {}).get("direct_value_allowed")),
            "proxy_score_allowed": bool((rent or {}).get("proxy_score_allowed")),
            "engine_promotion_ready": bool((rent or {}).get("engine_promotion_ready")),
            "forbidden_claim_ko": (rent or {}).get("forbidden_claim_ko"),
            "rent_reference": rent,
            "vacancy_reference": references.get("vacancy_reference"),
            "selected_reference": references.get("selected_reference"),
            "reference_options": references.get("reference_options"),
            "evidence_trace": references.get("evidence_trace"),
            "sale_price_proxy": references.get("sale_price_proxy"),
        }
        if not rent or rent.get("metric_value") is None:
            return base
        if str(rent.get("unit") or "").replace(" ", "") != "천원/㎡":
            base["reason"] = (
                f"지원하지 않는 R-ONE 임대료 단위({rent.get('unit') or '없음'})라 참고 산술을 생략합니다. "
                "공식 예산 적합도는 evidence-only 계약에 따라 보류합니다."
            )
            return base

        standardized_reference = float(rent["metric_value"]) * base["reference_area_m2"] * 12.0 / 10.0
        base["standardized_12m_reference_manwon"] = round(standardized_reference, 1)
        if budget > 0:
            base["reference_to_input_budget_ratio"] = round(standardized_reference / float(budget), 4)
        return base

    def resolve_industry(self, business_type: str | None) -> dict[str, Any] | None:
        if not business_type:
            return None
        query = str(business_type).strip()
        if not query:
            return None

        upper_query = query.upper()
        exact_code = self.db.execute(
            text(
                """
                SELECT *
                FROM industry_hierarchy
                WHERE UPPER(industry_code) = :code OR UPPER(final_algorithm_key) = :code
                LIMIT 1
                """
            ),
            {"code": upper_query},
        ).mappings().first()
        if exact_code:
            return dict(exact_code)

        exact_name = self.db.execute(
            text("SELECT * FROM industry_hierarchy WHERE industry_name = :name LIMIT 1"),
            {"name": query},
        ).mappings().first()
        if exact_name:
            return dict(exact_name)

        name_hits = self.db.execute(
            text("SELECT * FROM industry_hierarchy WHERE industry_name LIKE :kw ORDER BY industry_name"),
            {"kw": f"%{query}%"},
        ).mappings().all()
        if len(name_hits) == 1:
            return dict(name_hits[0])

        search_hits = self.db.execute(
            text("SELECT * FROM industry_hierarchy WHERE search_text LIKE :kw ORDER BY industry_name"),
            {"kw": f"%{query}%"},
        ).mappings().all()
        if len(search_hits) == 1:
            return dict(search_hits[0])

        return None

    def industry_options(self, business_type: str | None, limit: int = 5) -> list[dict[str, Any]]:
        if not business_type:
            return []
        query = str(business_type).strip()
        if not query:
            return []
        rows = self.db.execute(
            text(
                """
                SELECT industry_code, industry_name, selection_path
                FROM industry_hierarchy
                WHERE industry_name LIKE :kw OR search_text LIKE :kw
                ORDER BY industry_name
                LIMIT :limit
                """
            ),
            {"kw": f"%{query}%", "limit": limit},
        ).mappings().all()
        return [dict(row) for row in rows]

    def _rule_score(self, area_code: str, industry_code: str | None = None) -> dict[str, Any] | None:
        if not industry_code:
            return None
        row = self.db.execute(
            text(
                """
                SELECT *
                FROM rule_location_score
                WHERE quarter = :quarter
                  AND area_code = :area_code
                  AND industry_code = :industry_code
                LIMIT 1
                """
            ),
            {
                "quarter": self.latest_quarter(),
                "area_code": str(area_code),
                "industry_code": str(industry_code),
            },
        ).mappings().first()
        if not row:
            return None
        rule = dict(row)
        if rule.get("score_version") == EXPECTED_COVERAGE_SCORE_VERSION:
            return rule

        # Preserve the database value as a traceable context reference, while
        # withholding the official score/grade/rank until the v2.6 contract is
        # actually published. This is intentionally read-time only.
        legacy_score = rule.get("current_location_score")
        axis_names = ["sales", "competition", "demand", "accessibility"]
        available_axes = [
            axis for axis in axis_names if rule.get(f"axis_{axis}") is not None
        ]
        missing_axes = [axis for axis in axis_names if axis not in available_axes]
        rule["legacy_location_score_reference"] = legacy_score
        rule["context_location_score"] = rule.get("context_location_score") or legacy_score
        rule["current_location_score"] = None
        rule["grade"] = None
        rule["decision_label"] = "레거시 점수는 v2.6 coverage 검증 전 참고값으로만 유지"
        rule["score_coverage_tier"] = "legacy_reference"
        rule["available_axis_count"] = len(available_axes)
        rule["missing_axes"] = ",".join(missing_axes)
        rule["coverage_reason"] = "레거시 score_version으로 공식 점수·등급·순위를 보류"
        rule["taxonomy_direct_score_allowed"] = 0
        rule["official_rank_eligible"] = 0
        return rule

    def _industry_display_grade(self, rule: dict[str, Any] | None) -> str | None:
        if not rule or not rule.get("official_rank_eligible"):
            return None
        grade = str(rule.get("grade") or "").strip().upper()
        score = rule.get("current_location_score")
        industry_code = rule.get("industry_code")
        quarter = rule.get("quarter") or self.latest_quarter()
        if grade not in {"A", "B", "C", "D", "E"} or score is None or not industry_code:
            return None
        row = self.db.execute(
            text(
                """
                SELECT
                    100.0 * SUM(CASE WHEN current_location_score <= :score THEN 1 ELSE 0 END)
                    / COUNT(*) AS percentile
                FROM rule_location_score
                WHERE quarter = :quarter
                  AND industry_code = :industry_code
                  AND official_rank_eligible = 1
                  AND current_location_score IS NOT NULL
                """
            ),
            {
                "score": float(score),
                "quarter": str(quarter),
                "industry_code": str(industry_code),
            },
        ).mappings().first()
        percentile = float(row["percentile"]) if row and row.get("percentile") is not None else None
        plus_threshold = {"A": 90.0, "B": 70.0, "C": 50.0, "D": 30.0, "E": 10.0}
        return f"{grade}+" if percentile is not None and percentile > plus_threshold[grade] else grade

    def _industry_history(
        self,
        area_code: str,
        industry_code: str,
        *,
        limit: int = 12,
    ) -> list[IndustryQuarterHistory]:
        rows = self.db.execute(
            text(
                """
                WITH periods AS (
                    SELECT timestamp AS quarter
                    FROM district_sales
                    WHERE area_code = :area_code AND industry_code = :industry_code
                    UNION
                    SELECT timestamp AS quarter
                    FROM district_store_count
                    WHERE area_code = :area_code AND industry_code = :industry_code
                ), recent_periods AS (
                    SELECT quarter
                    FROM periods
                    ORDER BY quarter DESC
                    LIMIT :limit
                ), sales AS (
                    SELECT timestamp AS quarter, SUM(sales_amount) AS sales_amount
                    FROM district_sales
                    WHERE area_code = :area_code AND industry_code = :industry_code
                    GROUP BY timestamp
                ), stores AS (
                    SELECT timestamp AS quarter, SUM(store_count) AS store_count
                    FROM district_store_count
                    WHERE area_code = :area_code AND industry_code = :industry_code
                    GROUP BY timestamp
                )
                SELECT
                    recent_periods.quarter,
                    sales.sales_amount,
                    stores.store_count
                FROM recent_periods
                LEFT JOIN sales ON sales.quarter = recent_periods.quarter
                LEFT JOIN stores ON stores.quarter = recent_periods.quarter
                ORDER BY recent_periods.quarter ASC
                """
            ),
            {
                "area_code": str(area_code),
                "industry_code": str(industry_code),
                "limit": max(1, min(int(limit), 40)),
            },
        ).mappings().all()
        return [
            IndustryQuarterHistory(
                quarter=str(row["quarter"]),
                sales_amount=_nullable_float(row.get("sales_amount")),
                store_count=int(row["store_count"]) if row.get("store_count") is not None else None,
            )
            for row in rows
        ]

    def get_industry_analysis(
        self,
        area_code: str,
        industry: dict[str, Any],
    ) -> IndustryAnalysisResponse:
        industry_code = str(industry["industry_code"])
        industry_name = str(industry.get("industry_name") or industry_code)
        reference_quarter = self.latest_quarter()
        rule = self._rule_score(area_code, industry_code)
        history = self._industry_history(area_code, industry_code)
        current = next((item for item in history if item.quarter == reference_quarter), None)

        axis_values = {
            "sales": _nullable_float(rule.get("axis_sales")) if rule else None,
            "competition": _nullable_float(rule.get("axis_competition")) if rule else None,
            "demand": _nullable_float(rule.get("axis_demand")) if rule else None,
            "accessibility": _nullable_float(rule.get("axis_accessibility")) if rule else None,
        }
        axes = IndustryAxisAnalysis(
            **{
                name: IndustryAxisMetric(
                    internal_value=value,
                    display_grade=_axis_display_grade(value),
                )
                for name, value in axis_values.items()
            }
        )

        score_applicable = bool(
            rule
            and rule.get("score_version") == EXPECTED_COVERAGE_SCORE_VERSION
            and rule.get("official_rank_eligible")
            and rule.get("current_location_score") is not None
        )
        display_grade = self._industry_display_grade(rule) if score_applicable else None
        current_sales = current.sales_amount if current else None
        current_stores = current.store_count if current else None

        missing_data: list[str] = []
        if not rule:
            missing_data.append("rule_score")
        if current_sales is None:
            missing_data.append("current_sales")
        if current_stores is None:
            missing_data.append("current_store_count")
        missing_data.extend(
            f"axis_{name}" for name, value in axis_values.items() if value is None
        )

        has_any_data = bool(rule or history)
        has_complete_current_data = bool(
            score_applicable
            and current_sales is not None
            and current_stores is not None
            and all(value is not None for value in axis_values.values())
        )
        availability = (
            "available"
            if has_complete_current_data
            else "partial"
            if has_any_data
            else "unavailable"
        )

        if score_applicable:
            score_reason = str(rule.get("decision_label") or "v2.6 공식 입지 등급 사용 가능")
        elif rule:
            score_reason = str(rule.get("coverage_reason") or "공식 입지 등급을 산정할 수 없음")
        else:
            score_reason = str(
                industry.get("direct_score_blocker_ko")
                or "해당 상권·업종의 공식 입지 점수 자료가 없음"
            )

        return IndustryAnalysisResponse(
            industry_code=industry_code,
            industry_name=industry_name,
            reference_quarter=reference_quarter,
            availability=availability,
            display_grade=display_grade,
            score_applicable=score_applicable,
            score_version=str(rule.get("score_version")) if rule and rule.get("score_version") else None,
            score_reason=score_reason,
            current_sales_amount=current_sales,
            current_store_count=current_stores,
            history=history,
            axes=axes,
            missing_data=missing_data,
        )

    @staticmethod
    def _score_from_summary(summary: dict[str, Any] | None) -> int | None:
        if not summary or summary.get("score") is None:
            return None
        return int(round(float(summary["score"])))

    def _calculate_score(self, db_item) -> int | None:
        if not db_item:
            return None
        return self._score_from_summary(self._area_summary(db_item.area_code))

    def _simple_area_response(
        self,
        item,
        context: dict[str, Any] | None = None,
    ) -> CommercialAreaResponse:
        context = context or {}
        score_value = context.get("score")
        return CommercialAreaResponse(
            area_code=item.area_code,
            area_name=item.area_name,
            district_code=item.district_code,
            latitude=item.latitude,
            longitude=item.longitude,
            score=int(round(float(score_value))) if score_value is not None else None,
            grade=context.get("grade"),
            display_grade=context.get("display_grade"),
        )

    def _filter_latest_area_response(self, response: CommercialAreaResponse) -> CommercialAreaResponse:
        quarter = self.latest_quarter()
        response.district_populations = [p for p in response.district_populations if p.timestamp == quarter]
        response.district_floatings = [p for p in response.district_floatings if p.timestamp == quarter]
        response.district_sales = sorted(
            [s for s in response.district_sales if s.timestamp == quarter],
            key=lambda s: s.sales_amount,
            reverse=True,
        )
        response.district_store_counts = sorted(
            [s for s in response.district_store_counts if s.timestamp == quarter],
            key=lambda s: s.store_count,
            reverse=True,
        )
        sale_periods = [r.period for r in response.sale_price_proxies if r.period and r.period <= quarter]
        latest_sale_period = max(sale_periods) if sale_periods else None
        response.sale_price_proxies = [
            r for r in response.sale_price_proxies if latest_sale_period and r.period == latest_sale_period
        ]
        rone_periods = [r.period for r in response.rone_cost_references if r.period and r.period <= quarter]
        latest_rone_period = max(rone_periods) if rone_periods else None
        response.rone_cost_references = [
            r for r in response.rone_cost_references if latest_rone_period and r.period == latest_rone_period
        ]
        response.district_growth_histories = sorted(response.district_growth_histories, key=lambda h: h.timestamp)
        return response

    def get_area(self, code: str) -> CommercialAreaResponse | None:
        db_item = self.repository.get_by_code(code)
        if not db_item:
            return None
        summary = self._area_summary(db_item.area_code)
        response = CommercialAreaResponse.model_validate(db_item)
        response.score = self._score_from_summary(summary)
        response.grade = summary.get("grade") if summary else None
        response.display_grade = summary.get("display_grade") if summary else None
        return self._filter_latest_area_response(response)

    def get_all_areas(self) -> list[CommercialAreaResponse]:
        items = self.repository.get_all()
        context_rows = self._area_context_score_rows(limit=max(1, len(items))) if items else []
        context_by_area = {str(row["area_code"]): row for row in context_rows}
        return [
            self._simple_area_response(item, context_by_area.get(str(item.area_code)))
            for item in items
        ]

    def get_overview_stats(self) -> dict[str, Any]:
        quarter = self.latest_quarter()
        area_count = self.db.execute(text("SELECT COUNT(*) AS n FROM commercial_area")).mappings().first()
        store_points = self.db.execute(text("SELECT COUNT(*) AS n FROM spatial_store_point")).mappings().first()
        top_context = self._area_context_score_rows(limit=1)
        return {
            "latest_quarter": quarter,
            "area_count": int(area_count["n"]) if area_count else 0,
            "store_point_count": int(store_points["n"]) if store_points else 0,
            "top_score": (
                float(top_context[0]["score"])
                if top_context and top_context[0].get("score") is not None
                else None
            ),
            "top_grade": top_context[0].get("grade") if top_context else None,
            "top_display_grade": top_context[0].get("display_grade") if top_context else None,
            "score_type": "demand_accessibility_context",
            "score_label": "수요·접근성 맥락 등급",
            "official_rank_eligible": False,
        }

    def get_rankings(self) -> list[RankingResponse]:
        rows = self._area_context_score_rows(limit=100)
        return [
            RankingResponse(
                rank=idx + 1,
                area_code=row["area_code"],
                area_name=row["area_name"],
                score=int(round(float(row["score"] or 0))),
                grade=str(row["grade"]),
                display_grade=str(row["display_grade"]),
                trend="-",
            )
            for idx, row in enumerate(rows)
        ]

    def calculate_user_fit_score(self, area, condition) -> dict:
        resolved = self.resolve_industry(getattr(condition, "business_type", None))
        rule = self._rule_score(area.area_code, resolved["industry_code"]) if resolved else None
        summary = self._area_summary(area.area_code)
        axes = rule or self._area_axis_summary(area.area_code)

        score_value = rule.get("current_location_score") if rule else summary.get("score") if summary else None
        context_score_value = rule.get("context_location_score") if rule else summary.get("score") if summary else None
        score = int(round(float(score_value))) if score_value is not None else None
        context_score = int(round(float(context_score_value))) if context_score_value is not None else None
        budget_fit = self._budget_fit_overlay(area.area_code, getattr(condition, "budget", None))
        return {
            "area_name": area.area_name,
            "score": score,
            "grade": rule.get("grade") if rule and score is not None else summary.get("grade") if summary else None,
            "display_grade": self._industry_display_grade(rule) if rule and score is not None else summary.get("display_grade") if summary else None,
            "context_score": context_score,
            "budget_score": None,
            "business_score": score,
            "operation_score": _nullable_float((axes or {}).get("axis_accessibility")),
            "competition_score": _nullable_float((axes or {}).get("axis_competition")),
            "rent_score": None,
            "budget_fit": budget_fit,
            "industry_code": resolved["industry_code"] if resolved else None,
            "industry_name": resolved["industry_name"] if resolved else None,
            "score_source": "rule_location_score.full_4axis" if rule and score is not None else "rule_location_score.context_only" if rule else "rule_area_score_summary.area_context",
            "decision_label": rule.get("decision_label") if rule else "상권 수요·접근성 맥락 등급" if summary else None,
            "score_coverage_tier": rule.get("score_coverage_tier") if rule else "area_context",
            "available_axis_count": rule.get("available_axis_count") if rule else 2,
            "official_indicator_count": rule.get("official_indicator_count") if rule else None,
            "official_indicator_defined_count": rule.get("official_indicator_defined_count") if rule else None,
            "official_indicator_complete": bool(rule.get("official_indicator_complete")) if rule else False,
            "missing_axes": [axis for axis in str(rule.get("missing_axes") or "").split(",") if axis] if rule else ["sales", "competition"],
            "coverage_reason": rule.get("coverage_reason") if rule else summary.get("score_definition") if summary else None,
            "official_rank_eligible": bool(rule.get("official_rank_eligible")) if rule else False,
            "official_axes": {
                "sales": _nullable_float((axes or {}).get("axis_sales")),
                "competition": _nullable_float((axes or {}).get("axis_competition")),
                "demand": _nullable_float((axes or {}).get("axis_demand")),
                "accessibility": _nullable_float((axes or {}).get("axis_accessibility")),
                "cost_risk": _nullable_float((axes or {}).get("cost_risk_score")),
                "data_reliability": _nullable_float((axes or {}).get("data_reliability_score")),
            },
        }

    def calculate_area_stats(self, area, business_type: str, budget: int) -> dict:
        condition = SimpleNamespace(
            area_name=area.area_name,
            business_type=business_type,
            budget=budget,
        )
        return self.calculate_user_fit_score(area, condition)

    def recommend_alternative_areas(
        self,
        condition=None,
        limit: int = 3,
        target_area_name: str | None = None,
        business_type: str | None = None,
        budget: int | None = None,
    ) -> list[dict]:
        if condition is None:
            condition = SimpleNamespace(
                area_name=target_area_name or "",
                business_type=business_type or "",
                budget=budget or 0,
            )

        target_name = getattr(condition, "area_name", "")
        target_rows = self.repository.search_by_name(target_name) if target_name else []
        target_area_code = target_rows[0].area_code if target_rows else None
        resolved = self.resolve_industry(getattr(condition, "business_type", None))

        coverage_contract_ready = self._coverage_score_contract_ready()
        if resolved and coverage_contract_ready:
            rows = self.db.execute(
                text(
                    """
                    WITH scored AS (
                        SELECT area_code, area_name, current_location_score AS score, grade
                        FROM rule_location_score
                        WHERE quarter = :quarter
                          AND industry_code = :industry_code
                          AND official_rank_eligible = 1
                          AND current_location_score IS NOT NULL
                    ), graded AS (
                        SELECT
                            *,
                            CUME_DIST() OVER (ORDER BY score) AS score_percentile
                        FROM scored
                    )
                    SELECT
                        area_code,
                        area_name,
                        score,
                        grade,
                        CASE
                            WHEN grade = 'A' AND score_percentile > 0.9 THEN 'A+'
                            WHEN grade = 'B' AND score_percentile > 0.7 THEN 'B+'
                            WHEN grade = 'C' AND score_percentile > 0.5 THEN 'C+'
                            WHEN grade = 'D' AND score_percentile > 0.3 THEN 'D+'
                            WHEN grade = 'E' AND score_percentile > 0.1 THEN 'E+'
                            ELSE grade
                        END AS display_grade
                    FROM graded
                    WHERE (:target_area_code IS NULL OR area_code != :target_area_code)
                    ORDER BY score DESC, area_name ASC
                    LIMIT :limit
                    """
                ),
                {
                    "quarter": self.latest_quarter(),
                    "industry_code": resolved["industry_code"],
                    "target_area_code": target_area_code,
                    "limit": limit,
                },
            ).mappings().all()
            reason = f"동일 업종({resolved['industry_name']}) 입지 등급 기준"
        else:
            rows = self._area_context_score_rows(
                limit=limit,
                exclude_area_code=target_area_code,
            )
            reason = (
                "v2.6 공식 등급 게시 전 수요·접근성 맥락 기준"
                if resolved
                else "상권 수요·접근성 맥락 등급 기준"
            )

        return [
            {
                "area_code": row["area_code"],
                "area_name": row["area_name"],
                "score": int(round(float(row["score"] or 0))),
                "grade": row["grade"],
                "display_grade": row["display_grade"],
                "reason": reason,
            }
            for row in rows
        ]


class DashboardService:
    def __init__(self, repository: CommercialAreaRepository):
        self.repository = repository
        self.area_service = CommercialAreaService(repository)

    def get_dashboard_summary(self, code: str) -> DashboardSummaryResponse | None:
        db_item = self.repository.get_by_code(code)
        if not db_item:
            return None
        quarter = self.area_service.latest_quarter()
        total_stores = sum(s.store_count for s in db_item.district_store_counts if s.timestamp == quarter)
        floating_pop = sum(p.floating_population for p in db_item.district_floatings if p.timestamp == quarter)
        cost_references = self.area_service._cost_references(db_item.area_code)
        sale_proxy = cost_references.get("sale_price_proxy") or {}
        rent_reference = cost_references.get("rent_reference") or {}
        vacancy_reference = cost_references.get("vacancy_reference") or {}
        total_sales = sum(s.sales_amount for s in db_item.district_sales if s.timestamp == quarter)
        summary = self.area_service._area_summary(db_item.area_code)
        return DashboardSummaryResponse(
            area_code=db_item.area_code,
            area_name=db_item.area_name,
            total_stores=total_stores,
            floating_population=floating_pop,
            sale_price_proxy_manwon_per_m2=_nullable_float(
                sale_proxy.get("sale_price_proxy_manwon_per_m2")
            ),
            rent_reference_thousand_won_per_m2=_nullable_float(rent_reference.get("metric_value")),
            vacancy_reference_pct=_nullable_float(vacancy_reference.get("metric_value")),
            cost_reference_provenance={
                "rent": rent_reference or None,
                "vacancy": vacancy_reference or None,
                "selected_reference": cost_references.get("selected_reference"),
                "reference_options": cost_references.get("reference_options"),
                "evidence_trace": cost_references.get("evidence_trace"),
                "sale_price_proxy": sale_proxy or None,
            },
            total_sales=total_sales,
            score=self.area_service._score_from_summary(summary),
            grade=summary.get("grade") if summary else None,
            display_grade=summary.get("display_grade") if summary else None,
        )
