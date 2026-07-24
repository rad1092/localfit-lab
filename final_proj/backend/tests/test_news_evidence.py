from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

from app.services.news_evidence import (
    LocationContext,
    RETRIEVAL_VERSION,
    merge_news_evidence_rows,
    news_evidence_for_prompt,
    news_evidence_version,
    retrieve_news_evidence,
    retrieve_news_evidence_tiers,
)
from app.services.interpretive_report import SingleInterpretation, _anchor_news_context


def row(
    evidence_id: str,
    title: str,
    *,
    summary: str,
    source_group: str = "news_search",
    source_grade: str = "B",
    provider: str = "example.com",
    region_hints: str = "서울특별시",
    industry_hints: str = "",
    signal_types: str = "commercial",
    published_date: str | None = None,
    query_text: str = "",
) -> dict[str, str]:
    return {
        "evidence_id": evidence_id,
        "source_id": f"source-{evidence_id}",
        "source_group": source_group,
        "source_grade": source_grade,
        "provider": provider,
        "dataset_name": "검증 표본",
        "title": title,
        "summary": summary,
        "original_url": f"https://example.com/{evidence_id}",
        "published_date": published_date or date.today().isoformat(),
        "region_hints": region_hints,
        "industry_hints": industry_hints,
        "signal_types": signal_types,
        "query_text": query_text,
        "content_sha256": evidence_id * 4,
    }


class ConditionEvidenceTest(unittest.TestCase):
    context = LocationContext(
        area_name="이태원 관광특구",
        district="용산구",
        exact_terms=("이태원관광특구", "이태원"),
        nearby_terms=("이태원1동",),
    )
    payload = {
        "area_code": "3001491",
        "area_name": "이태원 관광특구",
        "industry_name": "한식음식점",
        "user_condition": {"budget": 10000},
    }

    def retrieve(self, rows: list[dict[str, str]], **payload_updates):
        payload = {**self.payload, **payload_updates}
        with patch("app.services.news_evidence._rows", return_value=tuple(rows)), patch(
            "app.services.news_evidence._location_context", return_value=self.context
        ):
            return retrieve_news_evidence(payload)

    def test_strict_filter_keeps_only_condition_applicable_evidence(self):
        rows = [
            row(
                "local",
                "이태원 보행로 공사로 일부 동선 변경",
                summary="이태원1동 보행 구간의 공사 일정과 우회 동선을 안내한다.",
                signal_types="development;risk",
            ),
            row(
                "policy",
                "서울시 한식음식점 예비창업 지원",
                summary="한식음식점 예비창업가에게 정책자금과 자부담 조건을 안내한다.",
                source_group="seoul_official",
                source_grade="A",
                provider="서울특별시",
                industry_hints="외식",
                signal_types="small_business_policy",
            ),
            row(
                "welfare",
                "서울시 임산부 식품 지원",
                summary="임산부 대상 식품 꾸러미와 자부담 조건을 안내한다.",
                source_group="seoul_official",
                source_grade="A",
                provider="서울특별시 복지부서",
                industry_hints="외식",
                signal_types="small_business_policy",
            ),
            row(
                "industry-only",
                "강남 카페 신메뉴 출시",
                summary="강남구 카페 업계의 신메뉴 소식이다.",
                region_hints="강남구;서울특별시",
                industry_hints="카페·음료",
                signal_types="commercial",
            ),
            row(
                "stale",
                "이태원 상권 정비 계획",
                summary="이태원 관광특구 정비 계획을 발표했다.",
                signal_types="development",
                published_date="2020-01-01",
            ),
        ]

        selected = self.retrieve(rows)
        self.assertEqual({item["evidence_id"] for item in selected}, {"local", "policy"})
        self.assertTrue(all(item["score_role"] == "context_only" for item in selected))
        self.assertTrue(all(item["structured_score_impact"] == "none" for item in selected))
        self.assertTrue(all(item["condition_fit"] for item in selected))
        self.assertEqual([item["citation_index"] for item in selected], list(range(1, len(selected) + 1)))
        policy = next(item for item in selected if item["evidence_id"] == "policy")
        self.assertIn("비용·자금 계획 직접 연관", policy["condition_fit"])
        self.assertIn("금액 적합성은 별도 확인", policy["condition_fit"])
        self.assertNotIn("10,000만원 직접 연관", policy["condition_fit"])
        self.assertIn("입력 예산 금액의 적합성을 증명하지 않음", policy["usage_limit"])

    def test_industry_mismatch_keeps_persistent_area_changes_only(self):
        rows = [
            row(
                "road-work",
                "이태원 도로 공사 일정",
                summary="이태원 일대 도로 공사 구간과 시행 일정을 안내한다.",
                signal_types="development;transport",
            ),
            row(
                "official-walkway",
                "용산구 보행환경 개선 사업",
                summary="보행환경 개선 대상 구간과 시행 일정을 안내한다.",
                source_group="seoul_district_official",
                source_grade="A",
                provider="용산구청",
                region_hints="용산구;서울특별시",
                signal_types="transport",
            ),
            row(
                "other-industry-opening",
                "이태원 족발집 신규 오픈",
                summary="이태원에 족발 매장이 문을 연다.",
                industry_hints="족발",
                signal_types="commercial",
            ),
            row(
                "single-closing",
                "이태원 책방 폐업",
                summary="이태원의 한 책방이 영업을 종료한다.",
                industry_hints="서점",
                signal_types="commercial;risk",
            ),
            row(
                "rally-no-stop",
                "이태원 집회 관련 버스 정류소 무정차",
                summary="집회 시간 동안 일부 버스가 무정차 운행한다.",
                signal_types="transport;risk",
            ),
        ]

        selected = self.retrieve(rows)
        with patch("app.services.news_evidence._rows", return_value=tuple(rows)), patch(
            "app.services.news_evidence._location_context", return_value=self.context
        ):
            tiers = retrieve_news_evidence_tiers(self.payload)

        self.assertEqual(
            {item["evidence_id"] for item in selected},
            {"road-work"},
        )
        self.assertEqual(
            {item["evidence_id"] for item in tiers["reference_monitoring"]},
            {"official-walkway"},
        )
        self.assertTrue(all(item["industry_match"] is False for item in selected))
        self.assertTrue(
            all(
                not any(term in item["decision_use"] for term in {"신규 점포", "공실", "임대", "신청", "개통"})
                for item in selected
            )
        )

    def test_decision_use_does_not_expand_beyond_article_signal(self):
        selected = self.retrieve(
            [
                row(
                    "matching-opening",
                    "이태원 한식당 개점",
                    summary="이태원에 새 한식당이 문을 연다.",
                    industry_hints="한식",
                    signal_types="commercial",
                )
            ]
        )

        self.assertEqual([item["evidence_id"] for item in selected], ["matching-opening"])
        self.assertEqual(
            selected[0]["decision_use"],
            "기사에 명시된 개점의 대상 범위와 시점을 확인",
        )
        self.assertNotIn("공실", selected[0]["decision_use"])
        self.assertNotIn("임대", selected[0]["decision_use"])

    def test_decision_use_mentions_only_change_terms_present_in_source(self):
        selected = self.retrieve(
            [
                row(
                    "road-only",
                    "이태원 지하도로 공사 일정",
                    summary="이태원 지하도로의 공사 일정을 안내한다.",
                    signal_types="transport",
                )
            ]
        )

        self.assertEqual([item["evidence_id"] for item in selected], ["road-only"])
        self.assertIn("지하도로", selected[0]["decision_use"])
        self.assertNotIn("보행", selected[0]["decision_use"])
        self.assertNotIn("교통", selected[0]["decision_use"])

    def test_summary_only_district_mention_does_not_make_unrelated_news_local(self):
        rows = [
            row(
                "unrelated-market",
                "성수와 북촌 상권 지각변동",
                summary="기사 말미의 참석자 이력에 용산구 의원과 부동산 개발 자문 경력이 적혀 있다.",
                region_hints="용산구;서울특별시",
                signal_types="development;commercial",
            ),
            row(
                "public-reit",
                "공공리츠 재도약 방안",
                summary="공공 개발 자금조달 사례 중 용산구의 유휴 부지를 짧게 언급한다.",
                region_hints="용산구;서울특별시",
                signal_types="development;cost_policy",
            ),
            row(
                "provider-only",
                "보행환경 개선 사업 공고",
                summary="대상 구간과 공사 일정을 안내한다.",
                source_group="seoul_district_official",
                source_grade="A",
                provider="성동구청",
                region_hints="성동구;서울특별시",
                signal_types="transport;development",
            ),
            row(
                "official-local",
                "용산구 보행환경 개선 사업 공고",
                summary="대상 구간과 공사 일정을 안내한다.",
                source_group="seoul_district_official",
                source_grade="A",
                provider="용산구청",
                region_hints="용산구;서울특별시",
                signal_types="transport;development",
            ),
        ]

        selected = self.retrieve(rows)
        with patch("app.services.news_evidence._rows", return_value=tuple(rows)), patch(
            "app.services.news_evidence._location_context", return_value=self.context
        ):
            tiers = retrieve_news_evidence_tiers(self.payload)

        self.assertEqual(selected, [])
        self.assertEqual(
            [item["evidence_id"] for item in tiers["reference_monitoring"]],
            ["official-local"],
        )

    def test_gangnam_laundry_excludes_woomyeon_yongsan_road_notice(self):
        gangnam_context = LocationContext(
            area_name="강남역",
            district="서초구",
            exact_terms=("강남역",),
            nearby_terms=(),
        )
        notice = row(
            "woomyeon-yongsan",
            "우면~용산 지하도로 민간투자사업 공람 및 설명회 개최 공고 안내",
            summary="",
            source_group="seoul_district_official",
            source_grade="A",
            provider="서초구청",
            region_hints="서초구",
            signal_types="transport",
        )
        payload = {
            "area_code": "3120189",
            "area_name": "강남역",
            "industry_name": "세탁소",
            "user_condition": {"budget": 5000},
        }

        with patch("app.services.news_evidence._location_context", return_value=gangnam_context):
            selected = retrieve_news_evidence(payload, rows=[notice])

        self.assertEqual(selected, [])

    def test_gangnam_road_notice_is_restored_as_monitoring_not_decision_support(self):
        gangnam_context = LocationContext(
            area_name="강남역",
            district="서초구",
            exact_terms=("강남역",),
            nearby_terms=(),
        )
        notice = row(
            "woomyeon-yongsan-monitoring",
            "우면~용산 지하도로 민간투자사업 공람 및 설명회 개최 공고 안내",
            summary="",
            source_group="seoul_district_official",
            source_grade="A",
            provider="서초구청",
            region_hints="서초구",
            signal_types="transport",
        )
        payload = {
            "area_code": "3120189",
            "area_name": "강남역",
            "industry_name": "세탁소",
            "user_condition": {"budget": 5000},
        }

        with patch("app.services.news_evidence._location_context", return_value=gangnam_context):
            tiers = retrieve_news_evidence_tiers(payload, rows=[notice])

        self.assertEqual(tiers["decision_support"], [])
        self.assertEqual(
            [item["evidence_id"] for item in tiers["reference_monitoring"]],
            ["woomyeon-yongsan-monitoring"],
        )
        monitoring = tiers["reference_monitoring"][0]
        self.assertEqual(monitoring["monitoring_location_basis"], "official_jurisdiction")
        self.assertFalse(monitoring["eligible_for_decision"])
        self.assertEqual(monitoring["decision_use"], "")
        self.assertEqual(monitoring["score_role"], "reference_only")
        self.assertEqual(monitoring["structured_score_impact"], "none")
        self.assertIn("사용하지 않음", monitoring["applicability_limit"])

    def test_monitoring_rows_are_never_sent_as_decision_prompt_evidence(self):
        prompt = news_evidence_for_prompt(
            [
                {
                    "evidence_tier": "reference_monitoring",
                    "title": "강남역 일시 교통 안내",
                    "summary": "일시 우회 안내",
                }
            ]
        )

        self.assertEqual(prompt, "사용자 조건을 모두 통과한 최근 외부 근거 없음")

    def test_empty_news_version_is_bound_to_retrieval_version(self):
        version = news_evidence_version([])

        self.assertEqual(version, f"{RETRIEVAL_VERSION}:no-news")
        self.assertNotEqual(version, "no-news")

    def test_other_district_official_post_does_not_match_summary_mentions(self):
        rows = [
            row(
                "wrong-district",
                "주간 지역 소식",
                summary="용산구 음식점 구인 정보가 일부 포함되어 있다.",
                source_group="seoul_district_official",
                source_grade="A",
                provider="동작구청",
                region_hints="동작구;용산구;서울특별시",
                industry_hints="외식",
                signal_types="commercial",
            ),
            row(
                "target-district",
                "용산구 보행 동선 개선 사업",
                summary="보행 동선 개선 공사의 시행 일정을 안내한다.",
                source_group="seoul_district_official",
                source_grade="A",
                provider="용산구청",
                region_hints="용산구;서울특별시",
                signal_types="transport",
            ),
        ]

        selected = self.retrieve(rows)
        with patch("app.services.news_evidence._rows", return_value=tuple(rows)), patch(
            "app.services.news_evidence._location_context", return_value=self.context
        ):
            tiers = retrieve_news_evidence_tiers(self.payload)
        self.assertEqual(selected, [])
        self.assertEqual(
            [item["evidence_id"] for item in tiers["reference_monitoring"]],
            ["target-district"],
        )
        self.assertEqual(tiers["reference_monitoring"][0]["location_scope"], "district")

    def test_other_district_policy_is_not_promoted_to_seoul_wide(self):
        rows = [
            row(
                "district-only",
                "지역밀착 특별보증 신청 안내",
                summary="중구 소재 소상공인에게 사업자금 보증과 저금리 대출을 지원한다.",
                source_group="seoul_district_official",
                source_grade="A",
                provider="중구청",
                region_hints="중구;서울특별시",
                industry_hints="한식",
                signal_types="small_business_policy",
            ),
            row(
                "citywide",
                "서울시 한식음식점 예비창업 보증 지원",
                summary="서울시 거주 한식음식점 예비창업자에게 보증 지원 조건을 안내한다.",
                source_group="seoul_district_official",
                source_grade="A",
                provider="중구청",
                region_hints="중구;서울특별시",
                industry_hints="한식",
                signal_types="small_business_policy",
            ),
        ]

        selected = self.retrieve(rows)

        self.assertEqual([item["evidence_id"] for item in selected], ["citywide"])
        self.assertEqual(selected[0]["location_scope"], "seoul")

    def test_budgetless_report_does_not_claim_budget_fit(self):
        rows = [
            row(
                "local",
                "이태원 야간 보행로 공사 계획",
                summary="이태원 일대의 보행로 공사 구간과 일정을 안내한다.",
                signal_types="transport",
            ),
            row(
                "policy",
                "서울시 한식음식점 예비창업 지원",
                summary="한식음식점 예비창업가에게 정책자금과 자부담 조건을 안내한다.",
                source_group="seoul_official",
                source_grade="A",
                provider="서울특별시",
                industry_hints="외식",
                signal_types="small_business_policy",
            ),
        ]

        selected = self.retrieve(rows, user_condition={"budget": None})
        self.assertEqual({item["evidence_id"] for item in selected}, {"local", "policy"})
        policy = next(item for item in selected if item["evidence_id"] == "policy")
        self.assertEqual(policy["budget_relevance"], "not_provided")
        self.assertNotIn("예산", policy["condition_fit"])

    def test_other_station_story_is_not_promoted_by_target_station_in_summary(self):
        station_context = LocationContext(
            area_name="양재역",
            district="서초구",
            exact_terms=("양재역",),
            nearby_terms=(),
        )
        misleading = row(
            "other-station",
            "상도역 장기전세 청약 시작",
            summary="서울 서초구 양재역 인근에 분양 홍보관을 개관한다.",
            region_hints="서초구;서울특별시",
            signal_types="transport;commercial",
        )
        payload = {
            "area_code": "3120179",
            "area_name": "양재역",
            "industry_name": "커피-음료",
            "user_condition": {"budget": 10000},
        }

        with patch("app.services.news_evidence._location_context", return_value=station_context):
            selected = retrieve_news_evidence(payload, rows=[misleading])

        self.assertEqual(selected, [])

    def test_two_tier_filter_blocks_campaign_welfare_and_non_seoul_false_positives(self):
        rows = [
            row(
                "exact-work",
                "이태원 보행로 공사 고시",
                summary="이태원 보행로 공사 구간과 시행 일정을 고시한다.",
                signal_types="development;transport",
            ),
            row(
                "district-work",
                "용산구 보행로 공사 고시",
                summary="용산구 내 대상 구간과 시행 일정을 고시한다.",
                signal_types="development;transport",
            ),
            row(
                "campaign",
                "용산구청장 후보 도시계획 정비사업 공약",
                summary="후보가 선거 공약으로 재개발 추진을 약속했다.",
                signal_types="development",
            ),
            row(
                "welfare",
                "용산구 평생교육이용권 지원사업",
                summary="주민 대상 평생교육 수강료 지원을 안내한다.",
                source_group="seoul_district_official",
                source_grade="A",
                provider="용산구청",
                signal_types="small_business_policy",
            ),
            row(
                "other-region",
                "용산구 부산항 연결도로 착공",
                summary="부산 지역 연결도로 공사를 다룬 기사다.",
                signal_types="development;transport",
            ),
        ]

        with patch("app.services.news_evidence._rows", return_value=tuple(rows)), patch(
            "app.services.news_evidence._location_context", return_value=self.context
        ):
            tiers = retrieve_news_evidence_tiers(self.payload)

        self.assertEqual(
            [item["evidence_id"] for item in tiers["decision_support"]],
            ["exact-work"],
        )
        self.assertEqual(
            [item["evidence_id"] for item in tiers["reference_monitoring"]],
            ["district-work"],
        )

    def test_prompt_is_compact_and_traceable(self):
        selected = self.retrieve(
            [
                row(
                    "local",
                    "이태원 보행로 공사 일정",
                    summary="이태원1동 우회 동선과 공사 기간을 안내한다.",
                    signal_types="development;risk",
                )
            ]
        )
        prompt = news_evidence_for_prompt(selected)
        self.assertIn("[NEWS:1]", prompt)
        self.assertIn("발행", prompt)
        self.assertIn("조건", prompt)
        self.assertIn("확인:", prompt)
        self.assertLess(len(prompt), 1200)

    def test_live_rows_are_merged_with_silver_and_official_duplicate_wins(self):
        silver_rows = [
            row(
                "official",
                "이태원 외식업 보행환경 개선 보도자료",
                summary="용산구가 이태원 외식업 밀집 구간의 보행환경 개선 일정을 발표했다.",
                source_group="seoul_district_official",
                source_grade="A",
                provider="용산구청",
                region_hints="용산구;서울특별시",
                industry_hints="외식",
                signal_types="transport;commercial",
            ),
            row(
                "duplicate",
                "이태원 상권 정비 계획",
                summary="공식 원문 요약",
                source_group="seoul_district_official",
                source_grade="A",
                provider="용산구청",
                signal_types="development",
            ),
        ]
        live_rows = [
            row(
                "article",
                "이태원 상권 보행로 공사",
                summary="이태원 보행로 공사와 우회 동선을 다룬 기사다.",
                signal_types="development;risk",
            ),
            {
                **row(
                    "duplicate-live",
                    "이태원 상권 정비 계획",
                    summary="검색 API 요약",
                    source_grade="B",
                    signal_types="development",
                ),
                "original_url": "https://example.com/duplicate",
            },
        ]

        with patch("app.services.news_evidence._rows", return_value=tuple(silver_rows)):
            merged = merge_news_evidence_rows(live_rows)

        self.assertEqual(len(merged), 3)
        duplicate = next(item for item in merged if item["original_url"].endswith("/duplicate"))
        self.assertEqual(duplicate["source_grade"], "A")
        self.assertEqual({item["source_group"] for item in merged}, {"news_search", "seoul_district_official"})

    def test_report_context_keeps_grounding_without_public_evidence_markers(self):
        selected = self.retrieve(
            [
                row(
                    "local",
                    "이태원 보행로 공사 일정",
                    summary="이태원1동 우회 동선과 공사 기간을 안내한다.",
                    signal_types="development;risk",
                )
            ]
        )
        report = SingleInterpretation(
            narrative_title="검증 리포트",
            executive_interpretation="정형 지표 해석입니다.",
            score_interpretation="정형 점수는 유지합니다.",
            trend_analysis="기존 시장 추이입니다. [NEWS:99]",
            user_fit="예산 조건을 검토합니다.",
            summary="요약입니다.",
        )

        anchored = _anchor_news_context(report, selected, {"budget": 10000})
        joined = " ".join([anchored.trend_analysis, anchored.user_fit, *anchored.action_plan, *anchored.risk_factors])
        self.assertNotIn("[NEWS:99]", joined)
        self.assertNotIn("[NEWS:", joined)
        self.assertEqual("기존 시장 추이입니다.", anchored.trend_analysis)
        self.assertEqual([], anchored.action_plan)
        self.assertEqual([], anchored.risk_factors)


if __name__ == "__main__":
    unittest.main()
