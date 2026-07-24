from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.services.interpretive_report import (
    SingleInterpretation,
    _anchor_verified_facts,
    _anchor_news_context,
    _build_claim_source_map,
    _fallback_gap_fields,
    _field_targets_for_violations,
    _generation_mode_for,
    _is_advisory_issue,
    _merge_sanitized_cached_interpretation,
    _replace_fields_with_fallback,
    _repair_invalid_fields,
    _sales_trend_fallback_text,
    _sanitize_claims,
    _single_markdown,
    _write_cache,
)
from app.schemas.commercial_area import AxisInterpretation
from app.services.indicator_pack import (
    _budget_fit_display,
    _display_only,
    _metric,
    public_coverage_context,
    public_coverage_header,
    public_coverage_reason,
)
from app.services.report_critic import validate_report_draft
from app.services.single_report import SingleReportService


def _draft() -> dict:
    axes = []
    for axis in ("시장성", "경쟁 구조", "수요 기반", "접근·유입"):
        axes.append(
            {
                "axis": axis,
                "meaning": "관측 지표가 실제 운영 조건에 주는 의미를 비교해 설명합니다.",
                "evidence": "검증된 관측값",
                "evidence_metrics": ["검증된 관측값"],
                "risk": "조건이 달라지면 판단도 달라질 수 있습니다.",
                "action": "현장에서 동선을 확인합니다.",
                "next_check": "피크 시간대를 확인합니다.",
                "frame_citations": [],
            }
        )
    return {
        "narrative_title": "입지 리서치",
        "thesis": ["시장과 경쟁 구조를 함께 봐야 합니다."],
        "executive_interpretation": "입력 조건을 기준으로 후보를 검토합니다.",
        "score_interpretation": "등급은 비교 기준 안에서 해석합니다.",
        "summary": "근거를 바탕으로 판단한 결과입니다.",
        "axis_interpretations": axes,
        "trend_analysis": "최근 흐름은 변화를 비교해 읽습니다.",
        "alternatives": [],
        "user_fit": "입력 조건을 기준으로 적합성을 해석합니다.",
        "action_plan": ["현장 동선을 확인합니다."],
        "onsite_checklist": ["피크 시간대를 확인합니다."],
        "risk_factors": ["조건 변화가 판단을 바꿀 수 있습니다."],
        "strengths": ["비교 근거가 있습니다."],
        "weaknesses": ["현장 확인이 필요합니다."],
        "source_citations": [],
    }


def _validate(
    draft: dict,
    facts: dict | None = None,
    user_condition: dict | None = None,
) -> list[str]:
    return validate_report_draft(
        draft,
        facts_pack_display=facts or {},
        user_condition=user_condition or {},
        evidence_frames=[],
        markdown_body="",
    )


def _interpretation(**overrides) -> SingleInterpretation:
    data = {
        "narrative_title": "입지 리서치",
        "thesis": ["기본 논거"],
        "executive_interpretation": "기본 해석",
        "score_interpretation": "기본 등급 해석",
        "summary": "기본 요약",
        "axis_interpretations": [],
        "trend_analysis": "기본 추이",
        "alternatives": [],
        "user_fit": "기본 조건 해석",
        "action_plan": ["기본 행동"],
        "onsite_checklist": ["기본 확인"],
        "risk_factors": ["기본 위험"],
    }
    data.update(overrides)
    return SingleInterpretation(**data)


class ReportValidationContractTests(unittest.TestCase):
    def test_competition_meaning_keeps_deterministic_direction_and_denominator_boundary(self):
        generated = _interpretation(
            axis_interpretations=[
                {
                    "axis": "경쟁 구조",
                    "score": 82.0,
                    "score_display": "A",
                    "display_grade": "A",
                    "interpretation_level": "A등급",
                    "evidence_metrics": ["동업종 점포수 위치 상위 1.0%"],
                    "meaning": "상권 전체 점포 대비 비중이 낮아 경쟁 압박은 크지 않습니다.",
                    "evidence": "동업종 점포수 13개",
                    "risk": "현장 확인 필요",
                    "action": "경쟁점을 확인",
                }
            ]
        )
        fallback = _interpretation(
            axis_interpretations=[
                {
                    "axis": "경쟁 구조",
                    "score": 82.0,
                    "score_display": "A",
                    "display_grade": "A",
                    "interpretation_level": "A등급",
                    "evidence_metrics": ["동업종 점포수 위치 상위 1.0%"],
                    "meaning": (
                        "동업종 점포수는 13개이고 서울 비교군에서 상위 1.0%이므로 "
                        "점포 수 자체를 적다고 볼 수 없습니다. 비중과 점포 수는 분모가 다릅니다."
                    ),
                    "evidence": "동업종 점포수 13개",
                    "risk": "현장 확인 필요",
                    "action": "경쟁점을 확인",
                }
            ]
        )

        anchored = _anchor_verified_facts(generated, fallback)

        self.assertEqual(
            anchored.axis_interpretations[0].meaning,
            fallback.axis_interpretations[0].meaning,
        )

    def test_news_title_number_is_not_checked_as_llm_narrative(self):
        draft = _draft()
        draft["source_citations"] = [{"title": "갈현동 등 6개 지역 정비계획"}]

        issues = _validate(draft)

        self.assertFalse(any("FACT_MISMATCH" in issue for issue in issues))

    def test_numeric_gate_uses_exact_tokens_not_substrings(self):
        draft = _draft()
        draft["trend_analysis"] = "점포는 5개 수준입니다."

        issues = _validate(draft, {"store_count": "점포 15개"})

        mismatch = next(issue for issue in issues if "FACT_MISMATCH" in issue)
        self.assertIn("[field=trend_analysis]", mismatch)
        self.assertIn("5개", mismatch)

    def test_budget_equivalent_in_eokwon_is_allowed(self):
        draft = _draft()
        draft["trend_analysis"] = "입력 예산 1.5억원을 기준으로 비용 조건을 봅니다."

        issues = _validate(
            draft,
            user_condition={"budget": 15000},
        )

        self.assertFalse(any("FACT_MISMATCH" in issue for issue in issues))

    def test_procedural_counts_and_duration_are_not_treated_as_observed_facts(self):
        draft = _draft()
        draft["action_plan"] = ["후보 3개를 30분씩 관찰합니다."]
        draft["axis_interpretations"][0]["action"] = "피크 시간대를 30분 관찰합니다."

        issues = _validate(draft)

        self.assertFalse(any("FACT_MISMATCH" in issue for issue in issues))

    def test_unverified_financial_number_in_action_remains_hard(self):
        draft = _draft()
        draft["action_plan"] = ["운영자금 9,999만원을 따로 배정합니다."]

        issues = _validate(draft)

        mismatch = next(issue for issue in issues if "FACT_MISMATCH" in issue)
        self.assertIn("action_plan[0]", mismatch)

    def test_verified_trend_window_wording_is_allowed(self):
        draft = _draft()
        facts = {"sales_block": {"sales_trend": [{"timestamp": str(index)} for index in range(8)]}}

        for text in ["최근 8개 분기의 흐름을 비교합니다.", "최근 8분기 매출 추이를 비교합니다."]:
            with self.subTest(text=text):
                draft["trend_analysis"] = text
                issues = _validate(draft, facts)

                self.assertFalse(any("FACT_MISMATCH" in issue for issue in issues))

    def test_wrong_trend_window_wording_remains_hard(self):
        draft = _draft()
        draft["trend_analysis"] = "최근 7분기 매출 추이를 비교합니다."
        facts = {"sales_block": {"sales_trend": [{"timestamp": str(index)} for index in range(8)]}}

        issues = _validate(draft, facts)

        self.assertTrue(any("FACT_MISMATCH" in issue for issue in issues))

    def test_sales_trend_direction_is_a_hard_contract(self):
        facts = {
            "sales_block": {
                "sales_trend": [
                    {"timestamp": "20242", "sales_amount": "8.6억원"},
                    {"timestamp": "20261", "sales_amount": "5.9억원"},
                ]
            }
        }
        draft = _draft()
        draft["trend_analysis"] = "최근 분기별 변동 폭을 함께 확인합니다."

        issues = _validate(draft, facts)

        mismatch = next(issue for issue in issues if "TREND_DIRECTION_MISMATCH" in issue)
        self.assertEqual(_field_targets_for_violations([mismatch]), ["trend_analysis"])

        draft["trend_analysis"] = "시작 분기보다 최근 분기 매출이 낮아져 전체 방향은 하락입니다."
        clean_issues = _validate(draft, facts)
        self.assertFalse(
            any("TREND_DIRECTION_MISMATCH" in issue for issue in clean_issues)
        )

    def test_deterministic_sales_trend_fallback_uses_raw_start_and_latest_values(self):
        facts = {
            "sales_block": {
                "sales_trend": [
                    {
                        "timestamp": "20261",
                        "sales_amount": {"raw": 591_009_483},
                    },
                    {
                        "timestamp": "20242",
                        "sales_amount": {"raw": 856_678_645},
                    },
                ]
            }
        }

        text = _sales_trend_fallback_text(facts)

        self.assertIn("전체 방향은 하락", text)
        self.assertNotIn("591", text)
        self.assertNotIn("856", text)

    def test_forbidden_claim_negation_is_not_a_positive_assertion(self):
        draft = _draft()
        draft["summary"] = "이 지표는 수익성을 보장하지 않습니다."
        clean_issues = _validate(draft)
        draft["summary"] = "이 조건은 수익성을 보장합니다."
        asserted_issues = _validate(draft)

        self.assertFalse(any("FORBIDDEN" in issue for issue in clean_issues))
        self.assertTrue(any("FORBIDDEN" in issue for issue in asserted_issues))

    def test_verified_tiny_percent_is_not_a_raw_float(self):
        draft = _draft()
        draft["summary"] = "상주인구 비율은 0.03%입니다."
        facts = {"demand_block": {"metrics": [{"display": "0.03%"}]}}

        issues = _validate(draft, facts)

        self.assertFalse(any("FORMAT" in issue for issue in issues))
        self.assertFalse(any("FACT_MISMATCH" in issue for issue in issues))

    def test_natural_korean_numeric_forms_are_still_fact_checked(self):
        cases = [
            "매출9억원입니다.",
            "약5억원입니다.",
            "상주인구34명입니다.",
            "점포는 56개입니다.",
            "예상 비용은 6억입니다.",
            "예상 비용은 7천만원입니다.",
        ]

        for text in cases:
            with self.subTest(text=text):
                draft = _draft()
                draft["summary"] = text
                issues = _validate(draft)
                self.assertTrue(any("FACT_MISMATCH" in issue for issue in issues))

    def test_withheld_budget_fit_rejects_budget_sufficiency_claim(self):
        draft = _draft()
        draft["user_fit"] = "예산 1억원이면 진입 검토가 가능합니다."
        facts = {
            "cost_block": {
                "budget_fit": {
                    "budget_fit_score": None,
                    "official_budget_fit_status": "withheld_evidence_only",
                }
            }
        }

        issues = _validate(draft, facts, {"budget": 10000})

        overclaim = next(issue for issue in issues if "BUDGET_SCOPE_OVERCLAIM" in issue)
        self.assertIn("user_fit", overclaim)

    def test_withheld_budget_fit_allows_explicitly_qualified_statement(self):
        draft = _draft()
        draft["user_fit"] = (
            "예산 1억원은 검토 상한으로 의미가 있지만, 공식 비용 적합도는 보류 상태이므로 "
            "충분하다고 단정할 수 없습니다."
        )
        facts = {
            "cost_block": {
                "budget_fit": {
                    "budget_fit_score": None,
                    "official_budget_fit_status": "withheld_evidence_only",
                }
            }
        }

        issues = _validate(draft, facts, {"budget": 10000})

        self.assertFalse(any("BUDGET_SCOPE_OVERCLAIM" in issue for issue in issues))

    def test_withheld_budget_fit_checks_later_and_procedural_claims(self):
        facts = {
            "cost_block": {
                "budget_fit": {
                    "budget_fit_score": None,
                    "official_budget_fit_status": "withheld_evidence_only",
                }
            }
        }
        draft = _draft()
        draft["user_fit"] = (
            "예산으로 진입 가능하다고 단정할 수 없습니다. "
            "하지만 입력 금액이면 진입 가능합니다."
        )
        draft["action_plan"] = ["예산 1억원이면 진입 가능하니 계약합니다."]

        issues = _validate(draft, facts, {"budget": 10000})

        affected = [issue for issue in issues if "BUDGET_SCOPE_OVERCLAIM" in issue]
        self.assertTrue(any("user_fit" in issue for issue in affected))
        self.assertTrue(any("action_plan[0]" in issue for issue in affected))

    def test_withheld_official_score_rejects_promotional_candidate_label(self):
        draft = _draft()
        draft["score_interpretation"] = "현재 조건에서 유망 후보로 볼 수 있습니다."
        facts = {"score_block": {"coverage": {"official_rank_eligible": False}}}

        issues = _validate(draft, facts)

        overclaim = next(issue for issue in issues if "OFFICIAL_SCOPE_OVERCLAIM" in issue)
        self.assertIn("score_interpretation", overclaim)

    def test_withheld_official_score_allows_negated_candidate_label(self):
        draft = _draft()
        draft["score_interpretation"] = "공식 판단이 보류되어 유망 후보나 추천 상권으로 단정할 수는 없습니다."
        facts = {"score_block": {"coverage": {"official_rank_eligible": False}}}

        issues = _validate(draft, facts)

        self.assertFalse(any("OFFICIAL_SCOPE_OVERCLAIM" in issue for issue in issues))

    def test_withheld_official_score_checks_later_and_procedural_claims(self):
        facts = {"score_block": {"coverage": {"official_rank_eligible": False}}}
        draft = _draft()
        draft["score_interpretation"] = (
            "유망 후보로 단정할 수는 없습니다. 하지만 추천 상권입니다."
        )
        draft["action_plan"] = ["추천 상권이므로 바로 계약합니다."]

        issues = _validate(draft, facts)

        affected = [issue for issue in issues if "OFFICIAL_SCOPE_OVERCLAIM" in issue]
        self.assertTrue(any("score_interpretation" in issue for issue in affected))
        self.assertTrue(any("action_plan[0]" in issue for issue in affected))

    def test_internal_axis_code_is_rejected_in_reader_prose(self):
        draft = _draft()
        draft["executive_interpretation"] = "수요 지표가 부분 관측(demand:3/5) 상태입니다."

        issues = _validate(draft)

        internal = next(issue for issue in issues if "INTERNAL_LABEL" in issue)
        self.assertIn("executive_interpretation", internal)

    def test_internal_chart_code_is_rejected_in_reader_prose(self):
        draft = _draft()
        draft["executive_interpretation"] = "C1 차트에서 핵심 등급을 확인합니다."

        issues = _validate(draft)

        internal = next(issue for issue in issues if "INTERNAL_LABEL" in issue)
        self.assertIn("executive_interpretation", internal)

    def test_axis_grade_claim_must_match_backend_grade(self):
        draft = _draft()
        draft["axis_interpretations"][1]["display_grade"] = "B"
        draft["axis_interpretations"][1]["score_display"] = "B"
        draft["thesis"] = ["경쟁 구조는 A보다 한 단계 낮다고 볼 수 있습니다."]
        draft["axis_interpretations"][1]["interpretation_level"] = "A등급"

        issues = _validate(draft)

        mismatches = [issue for issue in issues if "GRADE_MISMATCH" in issue]
        self.assertTrue(any("thesis[0]" in issue for issue in mismatches))
        self.assertTrue(any("axis_interpretations[1].interpretation_level" in issue for issue in mismatches))

    def test_axis_number_must_come_from_the_same_axis_block(self):
        draft = _draft()
        draft["axis_interpretations"][0]["meaning"] = "시장성 근거는 10명으로 확인됩니다."
        facts = {
            "sales_block": {"metrics": [{"display": "10억원"}]},
            "demand_block": {"metrics": [{"display": "10명"}]},
        }

        issues = _validate(draft, facts)

        mismatch = next(issue for issue in issues if "AXIS_FACT_MISMATCH" in issue)
        self.assertIn("axis_interpretations[0].meaning", mismatch)

    def test_high_competition_position_rejects_low_store_count_interpretation(self):
        draft = _draft()
        draft["axis_interpretations"][1]["meaning"] = (
            "세탁소 수가 아주 많지 않아 직접 경쟁은 낮습니다."
        )
        facts = {
            "target": {"industry_name": "세탁소"},
            "competition_block": {
                "metrics": [{"label": "동업종 점포수 위치", "display": "상위 1.8%"}]
            }
        }

        issues = _validate(draft, facts)

        issue = next(issue for issue in issues if "COMPETITION_DIRECTION_MISMATCH" in issue)
        self.assertFalse(_is_advisory_issue(issue))
        self.assertEqual(
            _field_targets_for_violations([issue]),
            ["axis_interpretations[1].meaning"],
        )

    def test_high_competition_position_allows_directionally_consistent_interpretation(self):
        draft = _draft()
        draft["axis_interpretations"][1]["meaning"] = (
            "동업종 점포 수는 서울 비교 후보 중 많은 편이므로 경쟁이 낮다고 볼 수 없습니다."
        )
        facts = {
            "competition_block": {
                "metrics": [{"label": "동업종 점포수 위치", "display": "상위 1.8%"}]
            }
        }

        issues = _validate(draft, facts)

        self.assertFalse(any("COMPETITION_DIRECTION_MISMATCH" in issue for issue in issues))

    def test_access_without_metrics_rejects_other_axis_evidence(self):
        draft = _draft()
        draft["axis_interpretations"][3]["evidence_metrics"] = []
        draft["axis_interpretations"][3]["meaning"] = (
            "직장인구와 유동인구가 많아 접근·유입 등급이 높습니다."
        )
        facts = {"accessibility_block": {"metrics": []}}

        issues = _validate(draft, facts)

        issue = next(issue for issue in issues if "ACCESS_EVIDENCE_SCOPE_MISMATCH" in issue)
        self.assertFalse(_is_advisory_issue(issue))
        self.assertEqual(
            _field_targets_for_violations([issue]),
            ["axis_interpretations[3].meaning"],
        )

    def test_access_without_metrics_allows_grade_only_qualified_interpretation(self):
        draft = _draft()
        draft["axis_interpretations"][3]["evidence_metrics"] = []
        draft["axis_interpretations"][3]["meaning"] = (
            "게시 등급은 확인되지만 세부 원천 지표가 표시되지 않아 원인은 설명하지 않습니다."
        )
        facts = {"accessibility_block": {"metrics": []}}

        issues = _validate(draft, facts)

        self.assertFalse(any("ACCESS_EVIDENCE_SCOPE_MISMATCH" in issue for issue in issues))

    def test_aggregate_signals_reject_actual_demand_visit_and_repeat_contact_overclaims(self):
        cases = [
            "직장인구와 유동인구가 커서 실수요를 기대할 수 있는 구조입니다.",
            "유동인구가 많아 반복 접점이 충분합니다.",
            "수요와 유입이 충분히 강합니다.",
        ]

        for text in cases:
            with self.subTest(text=text):
                draft = _draft()
                draft["summary"] = text

                issues = _validate(draft)

                issue = next(issue for issue in issues if "CAUSAL_SCOPE_OVERCLAIM" in issue)
                self.assertFalse(_is_advisory_issue(issue))
                self.assertEqual(_field_targets_for_violations([issue]), ["summary"])

    def test_aggregate_signals_allow_potential_and_explicitly_qualified_language(self):
        cases = [
            "직장인구와 유동인구는 잠재 수요를 보여주지만 실제 방문·구매 고객 수와 같지 않습니다.",
            "반복 접점이 충분한지는 현장에서 확인해야 합니다.",
            "수요와 유입이 충분히 강하다고 단정할 수 없습니다.",
        ]

        for text in cases:
            with self.subTest(text=text):
                draft = _draft()
                draft["summary"] = text

                issues = _validate(draft)

                self.assertFalse(any("CAUSAL_SCOPE_OVERCLAIM" in issue for issue in issues))

    def test_style_issue_is_advisory_and_has_exact_field(self):
        draft = _draft()
        draft["axis_interpretations"][0]["meaning"] = "짧음"

        issues = _validate(draft)

        issue = next(issue for issue in issues if "NO_INFERENCE" in issue)
        self.assertTrue(_is_advisory_issue(issue))
        self.assertEqual(
            _field_targets_for_violations([issue]),
            ["axis_interpretations[0].meaning"],
        )

    def test_fallback_replaces_only_named_field(self):
        fallback = _interpretation()
        current = _interpretation(
            executive_interpretation="보존할 AI 해석",
            trend_analysis="잘못된 숫자 999개",
        )

        replaced, fields = _replace_fields_with_fallback(
            current,
            fallback,
            ["trend_analysis"],
        )

        self.assertEqual(fields, ["trend_analysis"])
        self.assertEqual(replaced.trend_analysis, fallback.trend_analysis)
        self.assertEqual(replaced.executive_interpretation, "보존할 AI 해석")

    def test_extra_invalid_list_item_is_removed_locally_when_fallback_has_no_index(self):
        fallback = _interpretation(thesis=["검증된 논지"])
        current = _interpretation(
            thesis=["보존할 논지 1", "보존할 논지 2", "보존할 논지 3", "잘못된 추가 논지"]
        )

        replaced, fields = _replace_fields_with_fallback(current, fallback, ["thesis[3]"])

        self.assertEqual(fields, ["thesis[3]"])
        self.assertEqual(replaced.thesis, ["보존할 논지 1", "보존할 논지 2", "보존할 논지 3"])

    def test_multiple_extra_invalid_list_items_are_removed_from_highest_index(self):
        fallback = _interpretation(thesis=["검증된 논지"])
        current = _interpretation(thesis=["검증된 논지", "잘못 1", "잘못 2", "잘못 3"])

        replaced, fields = _replace_fields_with_fallback(
            current,
            fallback,
            ["thesis[1]", "thesis[2]", "thesis[3]"],
        )

        self.assertEqual(fields, ["thesis[1]", "thesis[2]", "thesis[3]"])
        self.assertEqual(replaced.thesis, ["검증된 논지"])

    def test_field_repair_removes_multiple_extras_without_deleting_unrelated_tail(self):
        fallback = _interpretation(thesis=["base"])
        current = _interpretation(thesis=["base", "bad1", "bad2", "good"])
        violations = [
            "[FACT_MISMATCH] [field=thesis[1]] bad1",
            "[FACT_MISMATCH] [field=thesis[2]] bad2",
        ]

        with patch("app.services.interpretive_report.get_llm", side_effect=RuntimeError("offline")):
            repaired, fallback_fields, llm_fields = _repair_invalid_fields(
                current=current,
                fallback=fallback,
                validation_issues=violations,
                facts_display={},
                user_condition={},
                usage_handler=SimpleNamespace(),
                reasoning_effort="none",
            )

        self.assertEqual(repaired.thesis, ["base", "good"])
        self.assertEqual(fallback_fields, ["thesis[1]", "thesis[2]"])
        self.assertEqual(llm_fields, [])

    def test_generation_modes_follow_surviving_narrative(self):
        fallback = _interpretation()
        llm_result = fallback.model_copy(deep=True)
        llm_result.executive_interpretation = "AI가 작성한 해석"

        self.assertEqual(_generation_mode_for(llm_result, fallback, []), "llm")
        self.assertEqual(
            _generation_mode_for(llm_result, fallback, ["trend_analysis"]),
            "partial_fallback",
        )
        self.assertEqual(_generation_mode_for(fallback, fallback, ["trend_analysis"]), "deterministic")

    def test_generation_mode_counts_surviving_strengths_and_weaknesses_as_ai(self):
        fallback = _interpretation(strengths=["검증된 강점"], weaknesses=["검증된 약점"])
        result = fallback.model_copy(deep=True)
        result.strengths = ["AI가 작성한 강점"]

        self.assertEqual(_generation_mode_for(result, fallback, []), "llm")

    def test_cached_sanitization_preserves_exact_provenance_strings(self):
        cached = _interpretation().model_dump()
        cached["original_validation_issues"] = [
            "[INTERNAL_LABEL] [field=summary] 노출: 근거 1",
            "[FACT_MISMATCH] [field=summary] 없는 숫자: 87점",
        ]
        cached["validation_issues"] = ["[FACT_MISMATCH] [field=summary] 없는 숫자: 87점"]
        cached["section_repair_log"] = [
            {"attempt": 1, "mode": "field_llm", "message": "근거 1 / 87점"}
        ]
        cleaned = SingleInterpretation(**cached)

        merged = _merge_sanitized_cached_interpretation(cached, cleaned)

        self.assertEqual(merged["original_validation_issues"], cached["original_validation_issues"])
        self.assertEqual(merged["validation_issues"], cached["validation_issues"])
        self.assertEqual(merged["section_repair_log"], cached["section_repair_log"])

    def test_sanitizer_only_change_does_not_turn_all_fallback_into_ai(self):
        fallback = _interpretation(thesis=["경쟁 구조은 원천 기준으로 확인합니다."])
        sanitized_result = fallback.model_copy(deep=True)
        sanitized_result.thesis = ["경쟁 구조는 원천 기준으로 확인합니다."]

        self.assertEqual(
            _generation_mode_for(sanitized_result, fallback, ["thesis"]),
            "deterministic",
        )

    def test_gap_provenance_is_rooted_and_not_based_on_equal_text(self):
        fallback = _interpretation(
            axis_interpretations=[
                {
                    "axis": axis,
                    "interpretation_level": "기본 등급 해석",
                    "meaning": "기본 의미",
                    "evidence": "검증 근거",
                    "risk": "기본 위험",
                    "action": "기본 행동",
                    "next_check": "기본 확인",
                }
                for axis in ("시장성", "경쟁 구조", "수요 기반", "접근·유입")
            ]
        )
        no_axes = _interpretation(axis_interpretations=[])
        same_generated_text = fallback.model_copy(deep=True)

        no_axis_gaps = _fallback_gap_fields(no_axes, fallback)
        equal_text_gaps = _fallback_gap_fields(same_generated_text, fallback)

        self.assertIn("axis_interpretations", no_axis_gaps)
        self.assertFalse(any(field.startswith("axis_interpretations[") for field in no_axis_gaps))
        self.assertNotIn("axis_interpretations", equal_text_gaps)
        self.assertFalse(any(field.startswith("axis_interpretations[") for field in equal_text_gaps))

        action_owned = fallback.model_copy(deep=True)
        for axis in action_owned.axis_interpretations:
            axis.next_check = ""
        action_owned_gaps = _fallback_gap_fields(action_owned, fallback)
        self.assertFalse(any(field.endswith(".next_check") for field in action_owned_gaps))

    def test_deterministic_report_is_not_written_to_cache(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            cache_db = Path(temporary_directory) / "cache.db"
            with patch("app.services.interpretive_report.DB_PATH", cache_db):
                _write_cache(
                    {"area_code": "A"},
                    {
                        "generation_mode": "deterministic",
                        "ai_generated": False,
                        "quality_status": "pass",
                        "validation_issues": [],
                    },
                )
            self.assertFalse(cache_db.exists())

    def test_rule_payload_contains_only_real_ui_conditions(self):
        service = object.__new__(SingleReportService)
        service.area_service = SimpleNamespace(
            latest_quarter=lambda: "20261",
            _budget_fit_overlay=lambda area_code, budget: {},
            _industry_display_grade=lambda rule: "A",
        )

        payload = service._rule_payload(
            area_code="A",
            area_name="양재역",
            resolved={"industry_code": "I", "industry_name": "치킨전문점"},
            rule=None,
            summary=None,
            axes={},
            score=None,
            score_source="context",
            top_industries=[],
            budget=50000,
        )

        self.assertEqual(
            set(payload["user_condition"]),
            {"area_name", "business_type", "budget"},
        )
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("experience_level", serialized)
        self.assertNotIn("경험 미입력", serialized)

    def test_external_evidence_is_not_forced_into_stock_narrative(self):
        result = _interpretation(trend_analysis="시장 흐름을 봅니다. [NEWS:1]")
        anchored = _anchor_news_context(
            result,
            [
                {
                    "decision_summary": "강제로 붙으면 안 되는 문장",
                    "decision_use": "원문을 확인",
                    "decision_role": "risk",
                }
            ],
            {"budget": 5000},
        )

        self.assertEqual(anchored.trend_analysis, "시장 흐름을 봅니다.")
        self.assertNotIn("강제로 붙으면 안 되는 문장", json.dumps(anchored.model_dump(), ensure_ascii=False))

    def test_numbered_evidence_markers_are_hard_and_sanitized(self):
        for text in [
            "판단 근거 1을 확인합니다.",
            "근거 1입니다.",
            "근거1로 판단합니다.",
            "[근거 2]를 확인합니다.",
            "[NEWS:3]을 확인합니다.",
        ]:
            with self.subTest(text=text):
                draft = _draft()
                draft["summary"] = text

                issues = _validate(draft)
                sanitized = _sanitize_claims(draft["summary"])

                self.assertTrue(any("INTERNAL_LABEL" in issue for issue in issues))
                self.assertNotRegex(sanitized, r"근거\s*\d+|NEWS:\d+")

    def test_evidence_word_before_verified_numeric_fact_is_not_a_badge(self):
        for text in [
            "예산 판단의 근거 50,000만원은 입력 상한입니다.",
            "비교 근거 2024년 자료를 확인합니다.",
            "추세 근거 8분기 자료를 확인합니다.",
        ]:
            with self.subTest(text=text):
                draft = _draft()
                draft["summary"] = text

                issues = _validate(draft, user_condition={"budget": 50000})
                sanitized = _sanitize_claims(text)

                self.assertFalse(any("INTERNAL_LABEL" in issue for issue in issues))
                self.assertEqual(sanitized, text)

    def test_claim_source_map_keeps_field_trace_without_public_badges(self):
        mapping = _build_claim_source_map(
            {
                "source_citations": [
                    {
                        "title": "서울 상권분석서비스 추정매출",
                        "provider": "서울특별시",
                        "dataset_name": "추정매출-상권",
                        "source_url": "https://example.test/sales",
                        "theme": "시장성",
                        "used_for": "매출 규모와 추이",
                    },
                    {
                        "title": "상업용 부동산 실거래",
                        "provider": "국토교통부",
                        "dataset_name": "상업용 부동산 실거래",
                        "source_url": "https://example.test/cost",
                        "theme": "비용 부담",
                        "used_for": "비용 압력 방향",
                    },
                    {
                        "title": "양재역 개발 기사",
                        "provider": "언론사",
                        "dataset_name": "양재역 개발 기사",
                        "source_url": "https://example.test/news",
                        "theme": "최근 정책·지역 이슈",
                        "used_for": "공사 단계 확인",
                    }
                ],
                "axis_interpretations": [
                    {
                        "axis": "시장성",
                        "meaning": "매출 흐름이 운영 조건에 주는 의미를 설명합니다.",
                        "evidence_metrics": ["최근 분기 매출액 6.7억원"],
                    }
                ],
                "news_evidence": [
                    {
                        "title": "양재역 개발 기사",
                        "original_url": "https://example.test/news",
                        "condition_fit": "선택 상권 직접 일치",
                        "decision_use": "공사 단계 확인",
                        "decision_area_label": "개발·공사 일정",
                    }
                ],
            }
        )

        axis_mapping = next(
            item
            for item in mapping
            if item["field_path"] == "axis_interpretations[0].meaning"
        )
        self.assertEqual(axis_mapping["supporting_evidence"], ["최근 분기 매출액 6.7억원"])
        self.assertEqual(axis_mapping["sources"][0]["dataset_name"], "추정매출-상권")
        self.assertFalse(axis_mapping["public_inline_marker"])
        self.assertTrue(any(item["field_path"] == "user_fit" for item in mapping))
        news_mapping = next(
            item
            for item in mapping
            if item["field_path"] == "news_evidence[0].decision_summary"
        )
        self.assertEqual(news_mapping["sources"][0]["source_url"], "https://example.test/news")
        self.assertFalse(news_mapping["public_inline_marker"])

    def test_context_only_axis_allows_pending_grade_label(self):
        axis = AxisInterpretation(
            axis="시장성",
            score=None,
            score_display="등급 보류",
            display_grade="등급 보류",
            interpretation_level="등급 보류",
            meaning="현재 분기 원천이 없어 등급을 보류합니다.",
            evidence="원천 없음",
            risk="추가 자료가 필요합니다.",
            action="최신 자료를 확인합니다.",
        )

        self.assertEqual(axis.display_grade, "등급 보류")

    def test_coverage_codes_are_converted_to_reader_language(self):
        missing_sales = {
            "official_rank_eligible": False,
            "missing_axes": ["sales"],
            "reason": "공식 축 결측(sales); 3축 참고점수만 제공",
        }
        partial_demand = {
            "official_rank_eligible": False,
            "missing_axes": [],
            "reason": "4개 공식 축은 산출 가능하지만 축내 필수 지표가 부분 관측(demand:3/5); 참고점수만 제공",
        }

        self.assertEqual(
            public_coverage_reason(missing_sales),
            "시장성 지표가 없어 공식 종합 판단을 보류합니다.",
        )
        self.assertEqual(
            public_coverage_context(missing_sales),
            "현재는 확인 가능한 경쟁 구조·수요 기반·접근·유입 지표만 참고합니다.",
        )
        self.assertEqual(
            public_coverage_header(partial_demand),
            "수요 기반 일부 지표 없음 · 공식 판단 보류",
        )
        self.assertNotIn("demand", public_coverage_reason(partial_demand))

    def test_display_pack_preserves_boolean_contract_fields(self):
        display = _display_only(
            {
                "official_rank_eligible": False,
                "official_indicator_complete": True,
                "count": 4,
            }
        )

        self.assertIs(display["official_rank_eligible"], False)
        self.assertIs(display["official_indicator_complete"], True)
        self.assertEqual(display["count"], "4")

    def test_budget_reference_display_uses_the_same_units_as_fallback_prose(self):
        display = _budget_fit_display(
            {
                "budget_manwon": 20000,
                "reference_area_m2": 33,
                "reference_months": 12,
                "standardized_12m_reference_manwon": 2232,
                "reference_to_input_budget_ratio": 0.1116,
            },
            {},
        )

        self.assertEqual(display["budget_manwon"], "20,000만원")
        self.assertEqual(display["reference_area_m2"], "33㎡")
        self.assertEqual(display["reference_months"], "12개월")
        self.assertEqual(display["standardized_12m_reference_manwon"], "2,232만원")
        self.assertEqual(display["reference_to_input_budget_ratio"], "11.2%")

    def test_fraction_percent_is_not_scaled_twice(self):
        tiny_ratio = _metric(
            "상주/직장 비율",
            34 / 102032,
            unit="fraction_percent",
            source="test",
        )
        normal_ratio = _metric(
            "동업종 점포 비중",
            3 / 23,
            unit="fraction_percent",
            source="test",
        )
        already_percent = _metric("공실률", 6.3, unit="%", source="test")

        self.assertEqual(tiny_ratio["display"], "0.03%")
        self.assertEqual(tiny_ratio["unit"], "%")
        self.assertEqual(normal_ratio["display"], "13.0%")
        self.assertEqual(already_percent["display"], "6.3%")

    def test_percentile_display_uses_readable_tail_direction(self):
        weak = _metric("동업종 내 매출 위치", 3.0, unit="percentile", source="test")
        strong = _metric("동업종 내 매출 위치", 97.7, unit="percentile", source="test")

        self.assertEqual(weak["display"], "하위 3.0%")
        self.assertEqual(strong["display"], "상위 2.3%")

    def test_markdown_keeps_action_priority_and_onsite_checks(self):
        result = _interpretation(
            action_plan=["계약 조건을 먼저 검토합니다."],
            onsite_checklist=["출입구 가시성을 확인합니다."],
        )

        markdown = _single_markdown({}, result, None, [])

        self.assertIn("## 실행 우선순위", markdown)
        self.assertIn("계약 조건을 먼저 검토합니다.", markdown)
        self.assertIn("## 현장 확인 항목", markdown)
        self.assertIn("출입구 가시성을 확인합니다.", markdown)


if __name__ == "__main__":
    unittest.main()
