from __future__ import annotations

import re
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pypdf import PdfReader

from scripts.evaluate_detailed_report_grounding import (
    PROTOCOL_VERSION,
    _alternative_area_codes,
    _audit_news_rows,
    _budget_caveat_found,
    _driver_by_source,
    _expected_display_grade,
    _expected_score_rank_text,
    _extract_report,
    _external_evidence_layout_status,
    _infer_budget_manwon,
    _korean_particle_mismatches,
    _manual_pdf_review_status,
    _render_pdf_pages,
    _render_result_is_complete,
    _resolve_report_json_path,
    _resolve_request,
    _report_section_path,
    _trend_direction_from_text,
    _weight_set_for_industry,
)
from app.services.report_chart_catalog import CHART_TITLES
from app.services.report_charts import _quarter_label, render_report_charts
from app.services.korean import with_josa
from app.services.report_publisher import (
    PUBLIC_PRESENTATION_VERSION,
    _embed_chart_links,
    _pdf_bytes_from_markdown,
    _sanitize_public_line,
    _sanitize_public_markdown,
    report_artifacts_are_current,
)


class ReportPresentationTests(unittest.TestCase):
    def test_detailed_report_protocol_is_artifact_driven_version(self):
        self.assertEqual(
            PROTOCOL_VERSION,
            "detailed-report-grounding.v1.6.0-batch-contract-repair",
        )

    def test_evaluator_applies_plus_threshold_to_every_base_grade(self):
        self.assertEqual(_expected_display_grade("A", 90.1), "A+")
        self.assertEqual(_expected_display_grade("B", 71.4), "B+")
        self.assertEqual(_expected_display_grade("C", 51.8), "C+")
        self.assertEqual(_expected_display_grade("D", 32.2), "D+")
        self.assertEqual(_expected_display_grade("E", 9.7), "E")

    def test_evaluator_rank_text_matches_public_thousands_separators(self):
        self.assertEqual(
            _expected_score_rank_text(1333, 1294),
            "서울 1,333개 후보 중 1,294위",
        )
        self.assertEqual(
            _expected_score_rank_text(1022, 494),
            "서울 1,022개 후보 중 494위",
        )

    def test_evaluator_selects_weight_set_from_industry_prefix(self):
        self.assertEqual(_weight_set_for_industry("CS100001"), "CS1")
        self.assertEqual(_weight_set_for_industry("CS200031"), "CS2")
        self.assertEqual(_weight_set_for_industry("CS300001"), "CS3")
        self.assertEqual(_weight_set_for_industry("custom"), "BASE")

    def test_evaluator_unwraps_batch_report_and_infers_dynamic_request(self):
        report = {
            "indicator_pack": {
                "target": {
                    "area_code": "A-17",
                    "area_name": "임의 상권",
                    "industry_code": "CS100001",
                    "industry_name": "한식",
                    "quarter": "20261",
                },
                "supporting_indicators": {
                    "budget_fit": {"budget_manwon": 12345}
                },
                "facts_pack": {
                    "alternatives": [
                        {"area_code": "ALT-2", "area_name": "대안 둘"},
                        {"area_code": "ALT-1", "area_name": "대안 하나"},
                    ]
                },
            },
            "alternatives": [
                {"area_name": "대안 하나"},
                {"area_name": "대안 둘"},
            ],
        }
        payload = {
            "metrics": {"case": {"budget": 12345}},
            "report": report,
        }
        source_path = Path("batch-case.json")

        extracted = _extract_report(payload, source_path=source_path)
        request = _resolve_request(
            report=extracted,
            payload=payload,
            area_code=None,
            industry_code=None,
            budget_manwon=None,
        )

        self.assertIs(extracted, report)
        self.assertEqual(_infer_budget_manwon(payload, report), 12345)
        self.assertEqual(request["area_code"], "A-17")
        self.assertEqual(request["industry_code"], "CS100001")
        self.assertEqual(request["budget_manwon"], 12345)
        self.assertEqual(_alternative_area_codes(report), ["ALT-1", "ALT-2"])

    def test_evaluator_report_json_resolution_prefers_artifact_generated_file(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            artifact_dir = root / "artifact"
            output_dir = root / "output"
            artifact_dir.mkdir()
            output_dir.mkdir()
            generated = artifact_dir / "report_response.generated.json"
            previous = output_dir / "report_response.raw.json"
            generated.write_text("{}", encoding="utf-8")
            previous.write_text("{}", encoding="utf-8")

            resolved = _resolve_report_json_path(
                explicit_path=None,
                artifact_dir=artifact_dir,
                output_dir=output_dir,
            )

        self.assertEqual(resolved, generated.resolve())

    def test_chart_ids_remain_internal_and_titles_are_public_language(self):
        self.assertEqual(set(CHART_TITLES), {"C1", "C2", "C3", "C4", "C5"})
        self.assertTrue(all(not re.match(r"^C[1-5]\b", title) for title in CHART_TITLES.values()))
        self.assertIn("합산 추정매출", CHART_TITLES["C2"])

    def test_internal_quarter_code_is_reader_friendly(self):
        self.assertEqual(_quarter_label("20261"), "2026년 1분기")
        self.assertEqual(_quarter_label("unknown"), "unknown")

    def test_publisher_consumes_all_chart_markers(self):
        markdown = "# 리포트\n\n[CHART:C1]\n\n문장 [CHART:C2] 뒤"

        public = _embed_chart_links(markdown)

        self.assertNotIn("[CHART:", public)
        for chart_id in CHART_TITLES:
            self.assertIn(f"charts/{chart_id}.png", public)

    def test_publisher_compacts_unavailable_chart_instead_of_embedding_blank_axes(self):
        markdown = "# 리포트\n\n[CHART:C1]\n\n[CHART:C2]"

        public = _embed_chart_links(markdown, {"C1"})

        self.assertIn("charts/C1.png", public)
        self.assertNotIn("charts/C2.png", public)
        self.assertIn(f"{CHART_TITLES['C2']}: 표시 가능한 데이터 없음", public)
        self.assertNotIn("[CHART:", public)

    def test_presentation_version_covers_public_copy_units_and_pagination(self):
        self.assertEqual(
            PUBLIC_PRESENTATION_VERSION,
            "public-copy-units-pagination.v16.20260723-preserve-score-grade-scope",
        )

    def test_public_markdown_names_aggregate_sales_scope(self):
        public = _sanitize_public_markdown(
            "### 핵심 지표\n"
            "| 지표 | 값 | 해석 메모 |\n"
            "|---|---:|---|\n"
            "| 최근 분기 매출액 | 5.9억원 | 20261 기준 |\n"
        )

        self.assertIn("최근 분기 상권×업종 합산 추정매출", public)
        self.assertNotIn("| 최근 분기 매출액 |", public)

    def test_public_quarter_formatting_preserves_url_values(self):
        url = "https://example.com/archive/20261?previous=20254"

        public = _sanitize_public_markdown(f"20261 기준, 20242의 매출을 확인합니다. {url}\n")

        self.assertIn("2026년 1분기 기준", public)
        self.assertIn("2024년 2분기의 매출", public)
        self.assertIn(url, public)

    def test_legacy_duplicate_ordered_prefix_is_removed(self):
        public = _sanitize_public_markdown("1. 1) 후보 점포 확인\n2. 2. 임대 조건 확인\n")

        self.assertEqual(public, "1. 후보 점포 확인\n2. 임대 조건 확인\n")

    def test_score_noun_replacement_keeps_korean_particle(self):
        self.assertEqual(_sanitize_public_line("입지 점수가 높습니다."), "입지 등급이 높습니다.")

    def test_score_grade_scope_contract_is_not_collapsed_to_duplicate_grade(self):
        self.assertEqual(
            _sanitize_public_line(
                "정형 점수·등급과 분리하고 점수·등급·추천 판단에 사용하지 않습니다."
            ),
            "정형 점수·등급과 분리하고 점수·등급·추천 판단에 사용하지 않습니다.",
        )

    def test_particle_uses_last_pronounced_syllable_before_closing_parenthesis(self):
        self.assertEqual(with_josa("교대역(법원.검찰청)", "은는"), "교대역(법원.검찰청)은")

    def test_evaluator_particle_rule_distinguishes_correct_and_incorrect_parenthesized_names(self):
        markdown = (
            "교대역(법원.검찰청)은 비교 대상입니다.\n"
            "건대입구역(건대)는 비교 대상입니다.\n"
            "교대역(법원.검찰청)는 잘못된 조사입니다.\n"
        )

        mismatches = _korean_particle_mismatches(markdown)

        self.assertEqual(len(mismatches), 1)
        self.assertEqual(mismatches[0]["line"], 3)
        self.assertEqual(mismatches[0]["expected_particle"], "은")

    def test_evaluator_budget_caveat_accepts_reader_equivalent_wording(self):
        self.assertTrue(
            _budget_caveat_found(
                "비용 관련 공식 적합도는 보류 상태입니다. "
                "예산 5,000만원은 상한선으로만 둡니다."
            )
        )
        self.assertTrue(
            _budget_caveat_found(
                "예산 5,000만원만으로 충분하다고 말할 수는 없습니다."
            )
        )
        self.assertFalse(
            _budget_caveat_found(
                "예산 5,000만원이면 진입 가능합니다."
            )
        )

    def test_evaluator_trend_direction_handles_inflected_korean(self):
        self.assertEqual(
            _trend_direction_from_text(
                "매출은 비슷한 수준을 유지하다 최근 분기에 내려왔습니다."
            ),
            "down",
        )
        self.assertEqual(
            _trend_direction_from_text(
                "초기에는 감소했지만 최근에는 다시 상승했습니다."
            ),
            "up_or_flat",
        )
        self.assertEqual(_trend_direction_from_text("비슷한 수준을 유지했습니다."), "up_or_flat")
        self.assertEqual(_trend_direction_from_text("여덟 분기를 비교했습니다."), "other")

    def test_evaluator_finds_sales_driver_by_stable_source_not_display_label(self):
        report = {
            "indicator_pack": {
                "axis_indicator_pack": {
                    "sales": {
                        "score_drivers": [
                            {
                                "label": "독자용 라벨은 바뀔 수 있음",
                                "raw": 591_009_483,
                                "source": "DB.district_sales.sales_amount",
                            }
                        ]
                    }
                }
            }
        }

        driver = _driver_by_source(
            report,
            "sales",
            "DB.district_sales.sales_amount",
        )

        self.assertEqual(driver["raw"], 591_009_483)

    def test_evaluator_report_paths_follow_current_markdown_sections(self):
        markdown = (
            "# 보고서\n"
            "## 대안 상권 비교\n"
            "| 상권 | 판단 |\n"
            "| A | B |\n"
            "\n"
            "## 실행 우선순위\n"
            "1. 첫 번째\n"
            "2. 두 번째\n"
            "\n"
            "## 다음 섹션\n"
            "본문\n"
        )

        self.assertEqual(
            _report_section_path(markdown, "대안 상권 비교"),
            "report.md:2-4",
        )
        self.assertEqual(
            _report_section_path(markdown, "실행 우선순위"),
            "report.md:6-8",
        )
        self.assertEqual(
            _report_section_path(markdown, "없는 섹션"),
            "report.md:section-not-found(없는 섹션)",
        )

    def test_render_gate_requires_success_exact_page_count_and_existing_pngs(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            page_one = root / "page-1.png"
            page_two = root / "page-2.png"
            page_one.write_bytes(b"one")
            page_two.write_bytes(b"two")
            complete = {
                "returncode": 0,
                "rendered_pages": [
                    {"path": str(page_one), "exists": True},
                    {"path": str(page_two), "exists": True},
                ],
            }

            self.assertTrue(_render_result_is_complete(complete, pdf_page_count=2))
            self.assertFalse(_render_result_is_complete(complete, pdf_page_count=3))
            self.assertFalse(
                _render_result_is_complete(
                    {
                        "returncode": 1,
                        "rendered_pages": complete["rendered_pages"],
                    },
                    pdf_page_count=2,
                )
            )
            self.assertFalse(
                _render_result_is_complete(
                    {
                        "returncode": 0,
                        "rendered_pages": [
                            {"path": str(page_one), "exists": True},
                            {"path": str(page_two), "exists": False},
                        ],
                    },
                    pdf_page_count=2,
                )
            )
            self.assertFalse(
                _render_result_is_complete(
                    {
                        "returncode": 0,
                        "rendered_pages": [
                            {"path": str(page_one), "exists": True},
                            {
                                "path": str(root / "missing-page.png"),
                                "exists": True,
                            },
                        ],
                    },
                    pdf_page_count=2,
                )
            )

    def test_pdf_renderer_removes_stale_pages_before_single_render(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output_dir = root / "pages"
            output_dir.mkdir()
            stale_page = output_dir / "page-99.png"
            stale_page.write_bytes(b"stale")
            pdf_path = root / "report.pdf"
            pdf_path.write_bytes(b"pdf")
            executable = root / "tools" / "bin" / "pdftoppm.exe"

            def fake_run(*_args, **_kwargs):
                (output_dir / "page-1.png").write_bytes(b"fresh")
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            with (
                patch(
                    "scripts.evaluate_detailed_report_grounding.shutil.which",
                    return_value=str(executable),
                ),
                patch(
                    "scripts.evaluate_detailed_report_grounding.subprocess.run",
                    side_effect=fake_run,
                ) as run,
            ):
                result = _render_pdf_pages(pdf_path, output_dir)
                self.assertFalse(stale_page.exists())

        self.assertEqual(run.call_count, 1)
        self.assertEqual(result["returncode"], 0)
        self.assertEqual(len(result["rendered_pages"]), 1)
        self.assertTrue(result["rendered_pages"][0]["path"].endswith("page-1.png"))

    def test_manual_pdf_review_requires_matching_hash_and_q051_pass(self):
        review = {
            "artifact_sha256": {"report.pdf": "abc123"},
            "questions": {"Q051": {"decision": "PASS"}},
        }

        self.assertTrue(
            _manual_pdf_review_status(
                review,
                current_pdf_sha256="ABC123",
            )["passed"]
        )
        self.assertFalse(
            _manual_pdf_review_status(
                review,
                current_pdf_sha256="different",
            )["passed"]
        )
        review["questions"]["Q051"]["decision"] = "FAIL"
        self.assertFalse(
            _manual_pdf_review_status(
                review,
                current_pdf_sha256="abc123",
            )["passed"]
        )

    def test_external_evidence_layout_is_explicitly_not_applicable_without_news(self):
        status = _external_evidence_layout_status(
            ["일반 보고서 페이지"],
            news_present=False,
        )

        self.assertTrue(status["passed"])
        self.assertEqual(status["applicability"], "not_applicable")

    def test_external_evidence_layout_requires_each_tier_context_on_same_page(self):
        heading_and_intro = (
            "두 단계 외부 자료\n"
            "모든 외부 자료는 정형 점수·등급과 분리합니다. "
            "참고·모니터링 자료는 점수·등급·추천 판단에 사용하지 않습니다."
        )
        decision_headers = "1단계 · 판단 근거\n조건 적합성\n판단에 사용한 방식"
        monitoring_headers = (
            "2단계 · 참고·모니터링\n선정 이유\n참고할 내용\n판단 제외 사유"
        )

        missing_heading = _external_evidence_layout_status(
            ["다른 섹션"],
            news_present=True,
            decision_present=True,
            monitoring_present=True,
        )
        split_across_pages = _external_evidence_layout_status(
            [heading_and_intro, decision_headers, monitoring_headers],
            news_present=True,
            decision_present=True,
            monitoring_present=True,
        )
        broken_tier = _external_evidence_layout_status(
            [
                heading_and_intro,
                "1단계 · 판단 근거",
                "조건 적합성\n판단에 사용한 방식",
                monitoring_headers,
            ],
            news_present=True,
            decision_present=True,
            monitoring_present=True,
        )
        complete = _external_evidence_layout_status(
            [f"{heading_and_intro}\n{decision_headers}\n{monitoring_headers}"],
            news_present=True,
            decision_present=True,
            monitoring_present=True,
        )

        self.assertFalse(missing_heading["passed"])
        self.assertTrue(split_across_pages["passed"])
        self.assertFalse(broken_tier["passed"])
        self.assertTrue(complete["passed"])

    def test_news_row_audit_checks_title_persistent_signal_and_decision_terms(self):
        valid = {
            "title": "서초구 도로 교통 개선 공사",
            "summary": "보행 동선 개선 구간을 설명합니다.",
            "matched_location": "서초구",
            "location_scope": "district",
            "industry_match": False,
            "signal_types": "transport",
            "decision_use": "기사에 명시된 교통·보행 변화의 대상 구간을 확인",
        }
        invalid = {
            "title": "도로 사업 공고",
            "summary": "",
            "matched_location": "서초구",
            "location_scope": "district",
            "industry_match": False,
            "signal_types": "commercial",
            "decision_use": "기사에 명시된 임대·공실 변화를 확인",
        }
        partly_supported = {
            "title": "서초구 도로 교통 개선 공사",
            "summary": "",
            "matched_location": "서초구",
            "location_scope": "district",
            "industry_match": False,
            "signal_types": "transport",
            "decision_use": "기사에 명시된 교통·임대 변화의 대상 구간을 확인",
        }
        industry_matched = {
            "title": "서초구 세탁소 지원 사업",
            "summary": "세탁소 점포의 신청 조건을 설명합니다.",
            "matched_location": "서초구",
            "location_scope": "district",
            "industry_match": True,
            "signal_types": "commercial",
            "decision_use": "기사에 명시된 지원·신청 조건을 확인",
        }
        broad_without_industry = {
            "title": "서울 도로 교통 개선 공사",
            "summary": "보행 동선 개선 구간을 설명합니다.",
            "matched_location": "서울",
            "location_scope": "seoul",
            "industry_match": False,
            "signal_types": "transport",
            "decision_use": "기사에 명시된 교통·보행 변화의 대상 구간을 확인",
        }
        for candidate in (
            valid,
            invalid,
            partly_supported,
            industry_matched,
            broad_without_industry,
        ):
            candidate.update(
                {
                    "evidence_tier": "decision_support",
                    "eligible_for_decision": True,
                    "score_role": "context_only",
                    "structured_score_impact": "none",
                }
            )

        valid_audit = _audit_news_rows([valid])[0]
        invalid_audit = _audit_news_rows([invalid])[0]
        partly_supported_audit = _audit_news_rows([partly_supported])[0]
        industry_matched_audit = _audit_news_rows([industry_matched])[0]
        broad_without_industry_audit = _audit_news_rows([broad_without_industry])[0]

        self.assertTrue(valid_audit["passed"])
        self.assertFalse(invalid_audit["passed"])
        self.assertFalse(partly_supported_audit["passed"])
        self.assertTrue(industry_matched_audit["passed"])
        self.assertFalse(broad_without_industry_audit["passed"])
        self.assertIn("matched_location_not_in_title", invalid_audit["violations"])
        self.assertIn(
            "non_industry_without_persistent_signal_and_content",
            invalid_audit["violations"],
        )
        self.assertIn(
            "decision_use_domain_term_not_in_title_or_summary",
            invalid_audit["violations"],
        )
        self.assertIn(
            "decision_use_domain_term_not_in_title_or_summary",
            partly_supported_audit["violations"],
        )
        self.assertIn(
            "broad_scope_without_industry_match",
            broad_without_industry_audit["violations"],
        )

    def test_monitoring_news_audit_requires_explicit_non_decision_contract(self):
        valid = {
            "title": "우면~용산 지하도로 사업 공고",
            "summary": "",
            "matched_location": "서초구",
            "location_scope": "district",
            "source_group": "seoul_district_official",
            "source_grade": "A",
            "provider": "서초구청",
            "industry_match": False,
            "signal_types": "transport",
            "evidence_tier": "reference_monitoring",
            "monitoring_location_basis": "official_jurisdiction",
            "reference_use": "대상 구간을 추가 확인",
            "applicability_limit": "직접 연결이 없어 점수·등급·추천 판단에는 사용하지 않음",
            "decision_use": "",
            "eligible_for_decision": False,
            "score_role": "reference_only",
            "structured_score_impact": "none",
        }
        invalid = {
            **valid,
            "decision_use": "교통 영향 판단",
            "eligible_for_decision": True,
            "score_role": "context_only",
            "applicability_limit": "추가 확인",
        }

        self.assertTrue(_audit_news_rows([valid])[0]["passed"])
        invalid_audit = _audit_news_rows([invalid])[0]
        self.assertFalse(invalid_audit["passed"])
        self.assertIn("monitoring_has_decision_use", invalid_audit["violations"])
        self.assertIn("monitoring_marked_decision_eligible", invalid_audit["violations"])
        self.assertIn("monitoring_score_role_mismatch", invalid_audit["violations"])
        self.assertIn("monitoring_exclusion_reason_missing", invalid_audit["violations"])

    def test_monitoring_news_audit_accepts_verified_broad_business_policy(self):
        row = {
            "title": "2026년 서울시 소상공인 교육생 모집",
            "summary": "",
            "matched_location": "서울특별시",
            "location_scope": "seoul",
            "source_group": "seoul_district_official",
            "source_grade": "A",
            "provider": "강서구청",
            "industry_match": False,
            "signal_types": "commercial;small_business_policy",
            "evidence_tier": "reference_monitoring",
            "monitoring_location_basis": "broad_business_policy",
            "reference_use": "대상 업종과 신청 조건을 원문에서 추가 확인",
            "applicability_limit": "업종 직접 일치가 없어 점수·등급·추천 판단에는 사용하지 않음",
            "decision_use": "",
            "eligible_for_decision": False,
            "score_role": "reference_only",
            "structured_score_impact": "none",
        }

        self.assertTrue(_audit_news_rows([row])[0]["passed"])

    def test_legacy_inline_evidence_markers_are_removed(self):
        self.assertEqual(
            _sanitize_public_line("최근 변화를 확인합니다. [NEWS:1] [근거 2]"),
            "최근 변화를 확인합니다.",
        )

    def test_reader_facing_chart_code_is_replaced_with_title(self):
        self.assertEqual(
            _sanitize_public_line("C1 차트에서 비교합니다."),
            f"{CHART_TITLES['C1']} 차트에서 비교합니다.",
        )
        self.assertEqual(_sanitize_public_line("[CHART:C1]"), "[CHART:C1]")

    def test_artifact_freshness_requires_only_referenced_charts(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            report_dir = root / "42"
            charts_dir = report_dir / "charts"
            charts_dir.mkdir(parents=True)
            (report_dir / ".public-presentation-version").write_text(
                PUBLIC_PRESENTATION_VERSION,
                encoding="utf-8",
            )
            (report_dir / "report.md").write_text(
                "![대안 비교](charts/C4.png)\nC1 데이터는 표시할 수 없습니다.\n",
                encoding="utf-8",
            )
            (report_dir / "report.pdf").write_bytes(b"pdf")
            (charts_dir / "C4.png").write_bytes(b"png")

            with patch("app.services.report_publisher.REPORTS_OUT", root):
                (report_dir / ".public-presentation-version").write_text(
                    "public-copy-units-pagination.v9.20260723",
                    encoding="utf-8",
                )
                self.assertFalse(report_artifacts_are_current(42))
                (report_dir / ".public-presentation-version").write_text(
                    PUBLIC_PRESENTATION_VERSION,
                    encoding="utf-8",
                )
                self.assertTrue(report_artifacts_are_current(42))
                (charts_dir / "C4.png").unlink()
                self.assertFalse(report_artifacts_are_current(42))

    def test_c3_and_c5_bar_charts_declare_axis_and_value_units(self):
        report_data = {
            "indicator_pack": {
                "facts_pack": {
                    "sales_block": {
                        "area_top_industries": [
                            {
                                "industry_name": "일반의원",
                                "sales_amount": {"raw": 148_840_000_000},
                            }
                        ]
                    },
                    "demand_block": {
                        "resident_population": {"raw": 6_248},
                        "worker_population": {"raw": 87_191},
                        "floating_population_daily_average": {"raw": 85_667},
                    },
                }
            }
        }
        with tempfile.TemporaryDirectory() as temporary_directory:
            with (
                patch("app.services.report_charts._save_barh") as save_barh,
                patch("app.services.report_charts._save_grade_grouped"),
            ):
                render_report_charts(report_data, Path(temporary_directory))

        calls = {Path(call.args[0]).name: call for call in save_barh.call_args_list}
        self.assertEqual(calls["C3.png"].kwargs["xlabel"], "매출액(억원)")
        self.assertEqual(calls["C3.png"].kwargs["value_suffix"], "억원")
        self.assertEqual(calls["C5.png"].kwargs["xlabel"], "인구(만 명)")
        self.assertEqual(calls["C5.png"].kwargs["value_suffix"], "만 명")

    def test_external_evidence_heading_is_not_orphaned_from_table(self):
        filler = "\n".join(
            f"본문 {index}: 페이지 경계 회귀를 재현하기 위한 충분히 긴 설명 문장입니다."
            for index in range(46)
        )
        markdown = (
            "# 페이지 경계 테스트\n\n"
            f"{filler}\n"
            "### 조건 맞춤 외부 자료\n"
            "외부 자료는 등급 계산과 분리해 사용하며, 원문과 적용 범위를 확인합니다.\n"
            "| 자료 | 조건 적합성 | 판단에 사용한 방식 |\n"
            "|---|---|---|\n"
            "| 자료 A | 선택 상권 직접 일치 | 현장 조건 확인 |\n"
            "| 자료 B | 자치구 범위 일치 | 운영 여부 확인 |\n"
        )

        pdf = _pdf_bytes_from_markdown(markdown, Path(tempfile.gettempdir()))
        pages = [page.extract_text() or "" for page in PdfReader(BytesIO(pdf)).pages]
        heading_pages = [index for index, text in enumerate(pages) if "조건 맞춤 외부 자료" in text]

        self.assertGreater(len(pages), 1)
        self.assertEqual(len(heading_pages), 1)
        self.assertGreater(heading_pages[0], 0)
        heading_page = pages[heading_pages[0]]
        self.assertIn("조건 적합성", heading_page)
        self.assertIn("판단에 사용한 방식", heading_page)

    def test_checklist_heading_and_all_items_stay_on_one_page(self):
        filler = "\n".join(
            f"본문 {index}: 체크리스트 페이지 경계를 재현하기 위한 설명 문장입니다."
            for index in range(52)
        )
        checklist = "\n".join(f"- [ ] 현장 확인 항목 {index}" for index in range(1, 7))
        markdown = (
            "# 체크리스트 경계 테스트\n\n"
            f"{filler}\n"
            "## 현장 확인 항목\n"
            f"{checklist}\n"
            "## 다음 섹션\n"
            "다음 내용입니다.\n"
        )

        pdf = _pdf_bytes_from_markdown(markdown, Path(tempfile.gettempdir()))
        pages = [page.extract_text() or "" for page in PdfReader(BytesIO(pdf)).pages]
        heading_pages = [index for index, text in enumerate(pages) if "현장 확인 항목" in text]

        self.assertEqual(len(heading_pages), 1)
        heading_page = pages[heading_pages[0]]
        for index in range(1, 7):
            self.assertIn(f"현장 확인 항목 {index}", heading_page)


if __name__ == "__main__":
    unittest.main()
