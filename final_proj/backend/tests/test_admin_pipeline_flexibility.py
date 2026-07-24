from __future__ import annotations

import csv
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pandas as pd


WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_ROOT = WORKSPACE_ROOT / "scripts"
BACKEND_ROOT = WORKSPACE_ROOT / "final_proj" / "backend"
for path in (SCRIPTS_ROOT, BACKEND_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import ingest_common
import ingest_seoul_core_p0_full as core_p0_ingest
import ingest_seoul_transport_accessibility_sources as seoul_transport
import preprocess_rule_engine_trade_tables as trade_preprocess
import build_rule_based_location_scores as score_engine
from app.services import admin_pipeline
from app.services.admin_pipeline import JOB_DEFINITIONS
from scripts import seed_rule_gold_db


class AdminPipelineFlexibilityTests(unittest.TestCase):
    @staticmethod
    def _small_score_input() -> pd.DataFrame:
        row = {
                    "기준_년분기_코드": "20261",
                    "상권_코드": "A1",
                    "상권_코드_명": "테스트 상권",
                    "자치구_코드": "D1",
                    "자치구_코드_명": "테스트구",
                    "서비스_업종_코드": "TEST001",
                    "서비스_업종_코드_명": "테스트업종",
                    "direct_score_allowed": True,
                    "당월_매출_금액": 100.0,
                    "동종_과밀도": 1.0,
                    "총_유동인구_수": 1000.0,
                    "총_집객시설_수": 10.0,
                    "source_id": "sales",
                    "directness_level": "P0_공식_상권_집계",
                    "source_id_store": "store",
                    "directness_level_store": "P0_공식_상권_집계",
                    "source_id_demand": "demand",
                    "directness_level_demand": "P0_공식_상권_추정집계",
                    "source_id_fac": "facility",
                    "directness_level_fac": "P0_공식_상권_집계_프록시",
        }
        # 공식 적격 회귀는 임의 최소치가 아니라 정의된 공식 필수 지표 전부 관측 계약을 쓴다.
        for name, spec in score_engine.INDICATORS.items():
            if spec["axis"] in score_engine.CURRENT_AXES:
                row.setdefault(name, 1.0)
        return pd.DataFrame([row])

    def test_score_validation_layer_separates_grounding_from_survival_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            grounding = root / "grounding.json"
            market = root / "market.json"
            survival = root / "survival.json"
            grounding.write_text(
                json.dumps(
                    {
                        "status": "pass",
                        "pass_count": 17,
                        "fail_count": 0,
                        "failed_check_ids": [],
                        "details": {
                            "methodology": {
                                "score_weight_training_cutoff_verified": False,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            market.write_text(
                json.dumps({"weight_decision": {"promotion_pass": True}}),
                encoding="utf-8",
            )
            survival.write_text(
                json.dumps({"status": "pass", "predictive_status": "not_supported"}),
                encoding="utf-8",
            )

            with (
                patch.object(admin_pipeline, "PRODUCT_SCORE_GROUNDING_SUMMARY_PATH", grounding),
                patch.object(admin_pipeline, "MARKET_SCORE_VALIDATION_SUMMARY_PATH", market),
                patch.object(admin_pipeline, "BUSINESS_SURVIVAL_VALIDATION_SUMMARY_PATH", survival),
            ):
                layer = admin_pipeline._score_validation_layer()

        self.assertEqual(layer["key"], "score_validation")
        self.assertEqual(layer["label"], "점수 근거 검증")
        self.assertEqual(layer["status"], "advisory")
        self.assertEqual(layer["count"], 17)
        self.assertEqual(layer["job_key"], "validate_pipeline")
        self.assertIn("Gold→DB→지도/리포트 일치", layer["note"])
        self.assertIn("개별 365일 생존확률로 해석 불가", layer["note"])
        self.assertEqual(layer["survival_predictive_status"], "not_supported")

    def test_score_validation_layer_handles_failed_and_missing_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            failed_grounding = root / "failed-grounding.json"
            failed_grounding.write_text(
                json.dumps(
                    {
                        "status": "fail",
                        "pass_count": 4,
                        "fail_count": 1,
                        "failed_check_ids": ["db.score_mismatch"],
                        "details": {},
                    }
                ),
                encoding="utf-8",
            )
            missing_market = root / "missing-market.json"
            missing_survival = root / "missing-survival.json"
            with (
                patch.object(
                    admin_pipeline,
                    "PRODUCT_SCORE_GROUNDING_SUMMARY_PATH",
                    failed_grounding,
                ),
                patch.object(admin_pipeline, "MARKET_SCORE_VALIDATION_SUMMARY_PATH", missing_market),
                patch.object(
                    admin_pipeline,
                    "BUSINESS_SURVIVAL_VALIDATION_SUMMARY_PATH",
                    missing_survival,
                ),
            ):
                failed_layer = admin_pipeline._score_validation_layer()

            missing_grounding = root / "missing-grounding.json"
            with (
                patch.object(
                    admin_pipeline,
                    "PRODUCT_SCORE_GROUNDING_SUMMARY_PATH",
                    missing_grounding,
                ),
                patch.object(admin_pipeline, "MARKET_SCORE_VALIDATION_SUMMARY_PATH", missing_market),
                patch.object(
                    admin_pipeline,
                    "BUSINESS_SURVIVAL_VALIDATION_SUMMARY_PATH",
                    missing_survival,
                ),
            ):
                missing_layer = admin_pipeline._score_validation_layer()

        self.assertEqual(failed_layer["status"], "error")
        self.assertEqual(failed_layer["count"], 4)
        self.assertIn("db.score_mismatch", failed_layer["note"])
        self.assertEqual(missing_layer["status"], "warning")
        self.assertEqual(missing_layer["count"], 0)
        self.assertIn("검증 결과가 없습니다", missing_layer["note"])

    def test_reliability_uses_only_official_axes_and_each_axis_provenance(self) -> None:
        row = pd.Series(
            {
                "기준_년분기_코드": "20261",
                "비교군_확대": False,
                "pct__당월_매출_금액": 50.0,
                "pct__동종_과밀도": 50.0,
                "pct__총_유동인구_수": 50.0,
                "pct__총_집객시설_수": 50.0,
                # 아래 성장/비용 후보는 공식 신뢰도 분모에 들어가면 안 된다.
                "pct__매출_추세_기울기": 50.0,
                "pct__자치구_상업실거래_단가": 50.0,
                "pct__SBDC_동종_점포수": 90.0,
                "pct__생활이동_외부유입": 10.0,
                "source_id": "sales",
                "directness_level": "P0_공식_상권_집계",
                "source_id_store": "store",
                "directness_level_store": "직접 관측",
                "source_id_demand": "demand",
                "directness_level_demand": "공식 추정 프록시",
                "source_id_fac": "facility",
                "directness_level_fac": "직접 관측",
            }
        )
        official = {axis: (1, 1) for axis in score_engine.CURRENT_AXES}
        with_candidates = {**official, "growth": (0, 4), "cost_risk": (0, 1)}
        without_candidates = {**official, "growth": (4, 4), "cost_risk": (1, 1)}

        score_a, dims_a = score_engine._reliability(row, with_candidates)
        score_b, dims_b = score_engine._reliability(row, without_candidates)
        row_without_optional = row.copy()
        row_without_optional["pct__SBDC_동종_점포수"] = None
        row_without_optional["pct__생활이동_외부유입"] = None
        score_c, dims_c = score_engine._reliability(row_without_optional, with_candidates)

        self.assertEqual(dims_a, dims_b)
        self.assertEqual(dims_a, dims_c)
        self.assertEqual(score_a, score_b)
        self.assertEqual(score_a, score_c)
        self.assertEqual(dims_a["완전성"], 100.0)
        self.assertEqual(dims_a["공간해상도"], 90.0)
        self.assertEqual(dims_a["원천성"], 80.0)

    def test_score_frame_reuses_percentiles_and_fails_closed_below_reliability_gate(self) -> None:
        district_ranked = score_engine.percentile_scores(
            pd.DataFrame(
                [
                    {"상권_코드": "A1", "자치구_코드": "D1", "서비스_업종_코드": "I", "자치구_상업실거래_단가": 10.0},
                    {"상권_코드": "A2", "자치구_코드": "D1", "서비스_업종_코드": "I", "자치구_상업실거래_단가": 10.0},
                    {"상권_코드": "A3", "자치구_코드": "D2", "서비스_업종_코드": "I", "자치구_상업실거래_단가": 20.0},
                ]
            )
        )
        self.assertEqual(
            district_ranked["pct__자치구_상업실거래_단가"].tolist(),
            [50.0, 50.0, 0.0],
        )

        raw = self._small_score_input()
        normalized = score_engine.percentile_scores(raw)
        weights = {
            "BASE": {axis: 0.25 for axis in score_engine.CURRENT_AXES},
        }
        with patch.object(score_engine, "load_axis_weights", return_value=weights):
            from_raw = score_engine.score_frame(raw)
            from_normalized = score_engine.score_frame(normalized)
        pd.testing.assert_frame_equal(from_raw, from_normalized)

        variant = raw.iloc[0].to_dict()
        variant["상권_코드"] = "A2"
        variant["상권_코드_명"] = "보조근거 상이 상권"
        variant["자치구_코드"] = "D2"
        variant["자치구_코드_명"] = "보조근거 상이 구"
        variant["SBDC_동종_점포수"] = 1000.0
        variant["생활이동_외부유입"] = 2000.0
        comparison_raw = pd.DataFrame([raw.iloc[0].to_dict(), variant])
        comparison_normalized = score_engine.percentile_scores(comparison_raw)
        with patch.object(score_engine, "load_axis_weights", return_value=weights):
            comparison = score_engine.score_frame(comparison_normalized)
        self.assertEqual(comparison["official_indicator_count"].tolist(), [12, 12])
        self.assertEqual(comparison["official_indicator_defined_count"].tolist(), [12, 12])
        self.assertAlmostEqual(comparison.loc[0, "axis__competition"], comparison.loc[1, "axis__competition"])
        self.assertAlmostEqual(comparison.loc[0, "axis__accessibility"], comparison.loc[1, "axis__accessibility"])
        self.assertAlmostEqual(comparison.loc[0, "current_location_score"], comparison.loc[1, "current_location_score"])
        self.assertNotEqual(
            comparison.loc[0, "context_evidence__sbdc_competition_percentile"],
            comparison.loc[1, "context_evidence__sbdc_competition_percentile"],
        )
        self.assertNotEqual(
            comparison.loc[0, "context_evidence__living_mobility_accessibility_percentile"],
            comparison.loc[1, "context_evidence__living_mobility_accessibility_percentile"],
        )
        for axis, indicator_names in score_engine.OFFICIAL_REQUIRED_BY_AXIS.items():
            expected = comparison_normalized[
                [f"pct__{name}" for name in indicator_names]
            ].mean(axis=1)
            for index in comparison.index:
                self.assertAlmostEqual(
                    comparison.loc[index, f"axis__{axis}"],
                    round(float(expected.loc[index]), 2),
                )

        rel_dims = {
            "완전성": 0.0,
            "최신성": 100.0,
            "공간해상도": 0.0,
            "원천성": 0.0,
            "품질플래그": 95.0,
        }
        with (
            patch.object(score_engine, "load_axis_weights", return_value=weights),
            patch.object(score_engine, "_reliability", return_value=(39.0, rel_dims)),
        ):
            withheld = score_engine.score_frame(normalized).iloc[0]

        self.assertFalse(bool(withheld["official_rank_eligible"]))
        self.assertTrue(pd.isna(withheld["current_location_score"]))
        self.assertFalse(pd.isna(withheld["context_location_score"]))
        self.assertTrue(pd.isna(withheld["grade"]))
        self.assertTrue(pd.isna(withheld["current_location_score_transit_250m_candidate"]))
        self.assertIn(score_engine.GATE_LABEL, withheld["decision_label"])

        three_axis = normalized.copy()
        for indicator_name in score_engine.OFFICIAL_REQUIRED_BY_AXIS["sales"]:
            three_axis.loc[:, f"pct__{indicator_name}"] = float("nan")
        two_axis = three_axis.copy()
        for indicator_name in score_engine.OFFICIAL_REQUIRED_BY_AXIS["competition"]:
            two_axis.loc[:, f"pct__{indicator_name}"] = float("nan")
        with patch.object(score_engine, "load_axis_weights", return_value=weights):
            three_axis_scored = score_engine.score_frame(three_axis).iloc[0]
            two_axis_scored = score_engine.score_frame(two_axis).iloc[0]

        self.assertEqual(int(three_axis_scored["available_axis_count"]), 3)
        self.assertEqual(three_axis_scored["score_coverage_tier"], "context_only_3axis")
        self.assertFalse(pd.isna(three_axis_scored["context_location_score"]))
        self.assertEqual(int(two_axis_scored["available_axis_count"]), 2)
        self.assertEqual(two_axis_scored["score_coverage_tier"], "insufficient_context")
        self.assertTrue(pd.isna(two_axis_scored["context_location_score"]))

    def test_seed_preserves_low_reliability_withholding_and_rejects_reactivation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            batch_path = Path(directory) / "score.csv"
            row = {
                "기준_년분기_코드": "20261",
                "상권_코드": "A1",
                "상권_코드_명": "테스트 상권",
                "자치구_코드": "D1",
                "자치구_코드_명": "테스트구",
                "서비스_업종_코드": "TEST001",
                "서비스_업종_코드_명": "테스트업종",
                "current_location_score": None,
                "context_location_score": 70.0,
                "grade": None,
                "decision_label": "데이터 부족, 판단 보류 — 데이터 신뢰도 39.00 < 40.00",
                "score_coverage_tier": "full_4axis",
                "available_axis_count": 4,
                "official_indicator_count": 12,
                "official_indicator_defined_count": 12,
                "official_indicator_complete": True,
                "missing_axes": "",
                "coverage_reason": "데이터 부족, 판단 보류 — 데이터 신뢰도 39.00 < 40.00",
                "taxonomy_direct_score_allowed": True,
                "official_rank_eligible": False,
                "cost_risk_score": 50.0,
                "data_reliability_score": 39.0,
                "conservative_score_owa": 60.0,
                "axis__sales": 70.0,
                "axis__competition": 70.0,
                "axis__demand": 70.0,
                "axis__accessibility": 70.0,
                "growth_potential_score": None,
                "growth_rebound_candidate_score": None,
                "score_version": seed_rule_gold_db.COVERAGE_SCORE_VERSION,
            }
            pd.DataFrame([row]).to_csv(batch_path, index=False, encoding="utf-8-sig")

            conn = sqlite3.connect(":memory:")
            seed_rule_gold_db.replace_tables(conn)
            conn.execute(
                "INSERT INTO industry_hierarchy(industry_code, industry_name, direct_score_allowed) "
                "VALUES ('TEST001', '테스트업종', 1)"
            )
            with patch.object(seed_rule_gold_db, "latest_score_batch", return_value=batch_path):
                seed_rule_gold_db.seed_rule_scores(conn)
            published = conn.execute(
                "SELECT current_location_score, context_location_score, grade, decision_label, "
                "official_rank_eligible FROM rule_location_score"
            ).fetchone()
            self.assertIsNone(published[0])
            self.assertEqual(published[1], 70.0)
            self.assertIsNone(published[2])
            self.assertIn("데이터 신뢰도 39.00", published[3])
            self.assertEqual(published[4], 0)
            conn.close()

            reactivated = dict(row)
            reactivated.update(
                current_location_score=70.0,
                grade="A",
                decision_label="상위 후보군",
                official_rank_eligible=True,
            )
            pd.DataFrame([reactivated]).to_csv(batch_path, index=False, encoding="utf-8-sig")
            conn = sqlite3.connect(":memory:")
            seed_rule_gold_db.replace_tables(conn)
            conn.execute(
                "INSERT INTO industry_hierarchy(industry_code, industry_name, direct_score_allowed) "
                "VALUES ('TEST001', '테스트업종', 1)"
            )
            with (
                patch.object(seed_rule_gold_db, "latest_score_batch", return_value=batch_path),
                self.assertRaisesRegex(ValueError, "official_rank_eligible"),
            ):
                seed_rule_gold_db.seed_rule_scores(conn)
            conn.close()

            invalid_two_axis_context = dict(row)
            invalid_two_axis_context.update(
                context_location_score=70.0,
                score_coverage_tier="insufficient_context",
                available_axis_count=2,
                official_indicator_count=7,
                official_indicator_complete=False,
                missing_axes="sales,accessibility",
                coverage_reason="only two official axes observed",
                axis__sales=None,
                axis__accessibility=None,
            )
            pd.DataFrame([invalid_two_axis_context]).to_csv(
                batch_path, index=False, encoding="utf-8-sig"
            )
            conn = sqlite3.connect(":memory:")
            seed_rule_gold_db.replace_tables(conn)
            conn.execute(
                "INSERT INTO industry_hierarchy(industry_code, industry_name, direct_score_allowed) "
                "VALUES ('TEST001', 'test industry', 1)"
            )
            with (
                patch.object(seed_rule_gold_db, "latest_score_batch", return_value=batch_path),
                self.assertRaisesRegex(ValueError, "3-axis fallback contract"),
            ):
                seed_rule_gold_db.seed_rule_scores(conn)
            conn.close()

    def test_seed_rejects_area_axis_drift_across_industries(self) -> None:
        rows = pd.DataFrame(
            [
                {
                    "quarter": "20261",
                    "area_code": "A1",
                    "area_name": "테스트 상권",
                    "district_code": "D1",
                    "district_name": "테스트구",
                    "axis_demand": 70.0,
                    "axis_accessibility": 80.0,
                },
                {
                    "quarter": "20261",
                    "area_code": "A1",
                    "area_name": "테스트 상권",
                    "district_code": "D1",
                    "district_name": "테스트구",
                    "axis_demand": 71.0,
                    "axis_accessibility": 80.0,
                },
            ]
        )
        with self.assertRaisesRegex(ValueError, "area-grain demand/accessibility axes differ"):
            seed_rule_gold_db._area_context_frame(rows)
        rows.loc[1, "axis_demand"] = 70.0
        common = seed_rule_gold_db._area_context_frame(rows)
        self.assertEqual(len(common), 1)
        self.assertEqual(common.loc[0, "axis_demand"], 70.0)
        self.assertEqual(common.loc[0, "axis_accessibility"], 80.0)

    def test_page_digest_fingerprint_matches_legacy_body_fingerprint(self) -> None:
        page_bodies = {
            (2001, 3000): b'{"page":3,"rows":[4,5]}',
            (1, 1000): b'{"page":1,"rows":[1]}',
            (1001, 2000): b'{"page":2,"rows":[2,3]}',
        }
        legacy_fingerprint = hashlib.sha256(
            "\n".join(
                f"{start}:{end}:{hashlib.sha256(body).hexdigest()}"
                for (start, end), body in sorted(page_bodies.items())
            ).encode("utf-8")
        ).hexdigest()
        page_digests = {
            page_range: hashlib.sha256(body).hexdigest()
            for page_range, body in page_bodies.items()
        }

        self.assertEqual(
            ingest_common.page_digest_set_fingerprint(page_digests),
            legacy_fingerprint,
        )
        self.assertEqual(
            ingest_common.page_set_fingerprint(page_bodies),
            legacy_fingerprint,
        )

    def test_core_and_sales_snapshots_share_numeric_full_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            raw_root = Path(directory)
            services = ("VwsmTrdarFlpopQq", "VwsmTrdarSelngQq")
            expected_by_service: dict[str, str] = {}

            with patch.object(ingest_common, "RAW_ROOT", raw_root):
                for service in services:
                    page_bodies: dict[tuple[int, int], bytes] = {}
                    snapshots: list[Path] = []
                    for snapshot_date in ("20260715", "20260716"):
                        snapshot = (
                            raw_root
                            / snapshot_date
                            / "seoul_open_data"
                            / "full"
                            / service
                        )
                        snapshot.mkdir(parents=True)
                        snapshots.append(snapshot)
                        for page in range(1, 13):
                            start = (page - 1) * 1_000 + 1
                            end = page * 1_000
                            body = f"{service}-page-{page}".encode("utf-8")
                            page_bodies[(start, end)] = body
                            (snapshot / f"{service}_{start}_{end}.json").write_bytes(body)

                    expected_by_service[service] = ingest_common.page_set_fingerprint(
                        page_bodies
                    )
                    fingerprints = [
                        ingest_common.raw_directory_full_fingerprint(snapshot, service)
                        for snapshot in snapshots
                    ]
                    self.assertEqual(fingerprints[0], fingerprints[1])
                    self.assertEqual(fingerprints[0], expected_by_service[service])

    def test_source_state_updates_preserve_collection_freshness_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "source_state_catalog.json"
            state_path.write_text(
                json.dumps(
                    {
                        "services": {
                            "ServiceA": {
                                "service": "ServiceA",
                                "source_id": "source_a",
                                "sampled_skip_ttl_hours": 24,
                                "probe_status": "unchanged_sampled",
                                "last_full_collection_at": "2026-07-16T00:00:00+00:00",
                                "full_content_fingerprint": "collector-full",
                                "content_fingerprint": "old",
                            },
                            "ServiceB": {"service": "ServiceB", "marker": "keep"},
                        }
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(ingest_common, "SOURCE_STATE_PATH", state_path):
                ingest_common.update_source_state_catalog(
                    [
                        {
                            "service": "ServiceA",
                            "source_id": "source_a",
                            "content_fingerprint": "new",
                            "retained_period_start": "20191",
                        }
                    ]
                )
            services = json.loads(state_path.read_text(encoding="utf-8"))["services"]

            self.assertEqual(services["ServiceA"]["sampled_skip_ttl_hours"], 24)
            self.assertEqual(services["ServiceA"]["probe_status"], "unchanged_sampled")
            self.assertEqual(services["ServiceA"]["last_full_collection_at"], "2026-07-16T00:00:00+00:00")
            self.assertEqual(services["ServiceA"]["content_fingerprint"], "new")
            self.assertEqual(services["ServiceA"]["full_content_fingerprint"], "collector-full")
            self.assertEqual(services["ServiceA"]["retained_period_start"], "20191")
            self.assertEqual(services["ServiceB"]["marker"], "keep")
            with patch.object(ingest_common, "SOURCE_STATE_PATH", state_path):
                ingest_common.update_source_state_catalog(
                    [
                        {
                            "service": "ServiceA",
                            "probe_status": "unchanged_sampled",
                            "full_content_fingerprint": "collector-full",
                        }
                    ]
                )
            second = json.loads(state_path.read_text(encoding="utf-8"))["services"]["ServiceA"]
            self.assertEqual(second["full_content_fingerprint"], "collector-full")
            self.assertEqual(second["retained_period_start"], "20191")

    def test_source_status_prefers_final_state_over_manifest_probe_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry_path = root / "source_registry.csv"
            registry_path.write_text(
                "source_id,provider,dataset_name,priority,current_status,collection_method,credential_ref\n"
                "seoul_sales_trade_area,Seoul,Sales,P0,collected,api,not_required\n",
                encoding="utf-8-sig",
            )
            manifest_path = root / "ingest_manifest.csv"
            manifest_path.write_text(
                "source_id,collection_status,collected_at,change_status,data_period_start,data_period_end\n"
                "seoul_sales_trade_area,success,2026-07-16T01:00:00+00:00,"
                "sample_match_full_refresh_due,20261,20261\n",
                encoding="utf-8-sig",
            )
            state_path = root / "source_state_catalog.json"
            state_path.write_text(
                json.dumps(
                    {
                        "services": {
                            "VwsmTrdarSelngQq": {
                                "service": "VwsmTrdarSelngQq",
                                "source_id": "seoul_sales_trade_area",
                                "change_status": "unchanged_full",
                                "probe_status": "sample_match_full_refresh_due",
                                "data_period_start": "20211",
                                "data_period_end": "20261",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            with (
                patch.object(admin_pipeline, "SOURCE_REGISTRY_PATH", registry_path),
                patch.object(admin_pipeline, "INGEST_MANIFEST_PATH", manifest_path),
                patch.object(admin_pipeline, "FAILED_DOWNLOADS_PATH", root / "missing_failures.csv"),
                patch.object(admin_pipeline, "SOURCE_STATE_PATH", state_path),
                patch.object(admin_pipeline, "EXECUTION_CONTRACT_PATH", root / "missing.csv"),
                patch.object(admin_pipeline, "_key_presence", return_value={}),
            ):
                source = admin_pipeline.source_statuses()[0]

            self.assertEqual(source["last_change_status"], "unchanged_full")
            self.assertEqual(source["probe_status"], "sample_match_full_refresh_due")

    def test_core_source_health_uses_weekly_operational_window(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry_path = root / "source_registry.csv"
            registry_path.write_text(
                "source_id,provider,dataset_name,priority,current_status,collection_method,credential_ref\n"
                "seoul_sales_trade_area,Seoul,Sales,P0,collected,api,not_required\n",
                encoding="utf-8-sig",
            )
            manifest_path = root / "ingest_manifest.csv"
            manifest_path.write_text(
                "source_id,collection_status,collected_at,change_status,data_period_start,data_period_end\n"
                "seoul_sales_trade_area,success,2026-07-21T08:01:25+00:00,"
                "unchanged_full,20261,20261\n",
                encoding="utf-8-sig",
            )
            state_path = root / "source_state_catalog.json"
            completed_at = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
            state_path.write_text(
                json.dumps(
                    {
                        "services": {
                            "VwsmTrdarSelngQq": {
                                "service": "VwsmTrdarSelngQq",
                                "source_id": "seoul_sales_trade_area",
                                "change_status": "unchanged_full",
                                "full_collection_completed_at": completed_at,
                                "sampled_skip_ttl_hours": 24,
                                "data_period_start": "20211",
                                "data_period_end": "20261",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            patches = (
                patch.object(admin_pipeline, "SOURCE_REGISTRY_PATH", registry_path),
                patch.object(admin_pipeline, "INGEST_MANIFEST_PATH", manifest_path),
                patch.object(admin_pipeline, "FAILED_DOWNLOADS_PATH", root / "missing_failures.csv"),
                patch.object(admin_pipeline, "SOURCE_STATE_PATH", state_path),
                patch.object(admin_pipeline, "EXECUTION_CONTRACT_PATH", root / "missing_contract.csv"),
                patch.object(admin_pipeline, "_key_presence", return_value={}),
            )
            with (
                patches[0],
                patches[1],
                patches[2],
                patches[3],
                patches[4],
                patches[5],
                patch.dict(
                    os.environ,
                    {"LOCALFIT_CORE_SOURCE_HEALTH_TTL_HOURS": "168"},
                ),
            ):
                weekly = admin_pipeline.source_statuses()[0]
                with patch.dict(
                    os.environ,
                    {"LOCALFIT_CORE_SOURCE_HEALTH_TTL_HOURS": "48"},
                ):
                    overdue = admin_pipeline.source_statuses()[0]

            self.assertEqual(weekly["health"], "healthy")
            self.assertEqual(weekly["sampled_skip_ttl_hours"], 24)
            self.assertEqual(weekly["health_ttl_hours"], 168)
            self.assertEqual(overdue["health"], "warning")
            self.assertEqual(overdue["health_ttl_hours"], 48)

    def test_source_status_uses_latest_failure_event_until_a_later_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry_path = root / "source_registry.csv"
            registry_path.write_text(
                "source_id,provider,dataset_name,priority,current_status,collection_method,credential_ref\n"
                "test_source,Provider,Dataset,P1,collected,api,not_required\n",
                encoding="utf-8-sig",
            )
            manifest_path = root / "ingest_manifest.csv"
            manifest_header = "source_id,collection_status,collected_at,http_status\n"
            manifest_path.write_text(
                manifest_header + "test_source,success,2026-07-16T01:00:00+00:00,200\n",
                encoding="utf-8-sig",
            )
            failed_path = root / "failed_downloads.csv"
            failed_path.write_text(
                "source_id,attempted_at,failure_type\n"
                "test_source,2026-07-16T02:00:00+00:00,TimeoutError\n",
                encoding="utf-8-sig",
            )
            patches = (
                patch.object(admin_pipeline, "SOURCE_REGISTRY_PATH", registry_path),
                patch.object(admin_pipeline, "INGEST_MANIFEST_PATH", manifest_path),
                patch.object(admin_pipeline, "FAILED_DOWNLOADS_PATH", failed_path),
                patch.object(admin_pipeline, "SOURCE_STATE_PATH", root / "missing_state.json"),
                patch.object(admin_pipeline, "EXECUTION_CONTRACT_PATH", root / "missing_contract.csv"),
                patch.object(admin_pipeline, "_key_presence", return_value={}),
            )
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
                failed = admin_pipeline.source_statuses()[0]
                manifest_path.write_text(
                    manifest_header
                    + "test_source,success,2026-07-16T01:00:00+00:00,200\n"
                    + "test_source,success,2026-07-16T03:00:00+00:00,200\n",
                    encoding="utf-8-sig",
                )
                recovered = admin_pipeline.source_statuses()[0]

            self.assertEqual(failed["health"], "error")
            self.assertEqual(failed["last_status"], "failed")
            self.assertEqual(failed["failure_rows"], 1)
            self.assertEqual(failed["last_collected_at"], "2026-07-16T01:00:00+00:00")
            self.assertEqual(failed["last_failure_at"], "2026-07-16T02:00:00+00:00")
            self.assertEqual(recovered["health"], "healthy")
            self.assertEqual(recovered["last_status"], "success")
            self.assertEqual(recovered["failure_rows"], 1)

    def test_paged_collection_contract_rejects_total_drift_and_short_pages(self) -> None:
        ingest_common.validate_paged_collection_response(
            initial_total_count=2_500,
            page_total_count=2_500,
            start=2_001,
            end=2_500,
            row_count=500,
        )
        with self.assertRaisesRegex(RuntimeError, "list_total_count changed"):
            ingest_common.validate_paged_collection_response(
                initial_total_count=2_500,
                page_total_count=2_501,
                start=1_001,
                end=2_000,
                row_count=1_000,
            )
        with self.assertRaisesRegex(RuntimeError, "page row count"):
            ingest_common.validate_paged_collection_response(
                initial_total_count=2_500,
                page_total_count=2_500,
                start=1_001,
                end=2_000,
                row_count=999,
            )

    def test_core_collection_resumes_only_valid_same_day_consecutive_pages(self) -> None:
        service = "ResumeService"
        source_id = "resume_source"
        run_date = "20300102"

        def body(rows: list[int]) -> bytes:
            return json.dumps(
                {
                    service: {
                        "list_total_count": 5,
                        "RESULT": {"CODE": "INFO-000", "MESSAGE": "ok"},
                        "row": [
                            {"id": value, "STDR_YYQU_CD": "20294"}
                            for value in rows
                        ],
                    }
                }
            ).encode("utf-8")

        with tempfile.TemporaryDirectory() as directory:
            raw_root = Path(directory)
            snapshot = (
                raw_root
                / run_date
                / "seoul_open_data"
                / "full"
                / service
            )
            snapshot.mkdir(parents=True)
            page_specs = ((1, 2, body([1, 2])), (3, 4, body([3, 4])))
            manifest = raw_root / "ingest_manifest.csv"
            with manifest.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=ingest_common.MANIFEST_FIELDS)
                writer.writeheader()
                for start, end, page_body in page_specs:
                    path = snapshot / f"{service}_{start}_{end}.json"
                    path.write_bytes(page_body)
                    writer.writerow(
                        {
                            "run_id": "partial_run",
                            "source_id": source_id,
                            "snapshot_date": "2030-01-02",
                            "raw_path": str(path),
                            "sha256": hashlib.sha256(page_body).hexdigest(),
                            "collection_status": "success",
                            "request_params_json": json.dumps(
                                {"service": service, "start": start, "end": end}
                            ),
                            "provider_result_code": "INFO-000",
                            "data_period_start": "20294",
                            "data_period_end": "20294",
                            "collected_at": f"2030-01-02T00:00:0{start}+00:00",
                        }
                    )

            with (
                patch.object(core_p0_ingest, "RAW_ROOT", raw_root),
                patch.object(core_p0_ingest, "RUN_DATE", run_date),
            ):
                resumed = core_p0_ingest._validated_same_day_resume(
                    service_name=service,
                    source_id=source_id,
                    page_size=2,
                    total_count=5,
                    live_first_body=page_specs[0][2],
                )
                self.assertEqual(resumed["page_count"], 2)
                self.assertEqual(resumed["collected_rows"], 4)
                self.assertEqual(len(resumed["page_digests"]), 2)
                final_page = body([5])
                complete_digests = dict(resumed["page_digests"])
                complete_digests[(5, 5)] = hashlib.sha256(final_page).hexdigest()
                self.assertEqual(
                    ingest_common.page_digest_set_fingerprint(complete_digests),
                    ingest_common.page_set_fingerprint(
                        {
                            (1, 2): page_specs[0][2],
                            (3, 4): page_specs[1][2],
                            (5, 5): final_page,
                        }
                    ),
                )

                # A later orphan page creates a gap and must never be silently mixed.
                orphan = snapshot / f"{service}_7_7.json"
                orphan.write_bytes(body([5]))
                with self.assertRaisesRegex(RuntimeError, "consecutive page prefix"):
                    core_p0_ingest._validated_same_day_resume(
                        service_name=service,
                        source_id=source_id,
                        page_size=2,
                        total_count=5,
                        live_first_body=page_specs[0][2],
                    )
                orphan.unlink()

                with self.assertRaisesRegex(RuntimeError, "live first-page probe"):
                    core_p0_ingest._validated_same_day_resume(
                        service_name=service,
                        source_id=source_id,
                        page_size=2,
                        total_count=5,
                        live_first_body=body([9, 10]),
                    )

                core_p0_ingest._adopt_resumed_manifest_rows(
                    run_id_value="resumed_run",
                    manifest_rows=resumed["manifest_rows"],
                )
                with patch.object(ingest_common, "RAW_ROOT", raw_root):
                    ingest_common.mark_manifest_run_complete(
                        run_id_value="resumed_run",
                        source_id=source_id,
                        service_name=service,
                    )

            with manifest.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            adopted = [row for row in rows if row["run_id"] == "resumed_run"]
            self.assertEqual(len(adopted), 2)
            self.assertTrue(
                all(row["full_collection_status"] == "complete" for row in adopted)
            )

            with (
                patch.object(core_p0_ingest, "RAW_ROOT", raw_root),
                patch.object(core_p0_ingest, "RUN_DATE", run_date),
                self.assertRaisesRegex(RuntimeError, "already manifest-complete"),
            ):
                core_p0_ingest._validated_same_day_resume(
                    service_name=service,
                    source_id=source_id,
                    page_size=2,
                    total_count=5,
                    live_first_body=page_specs[0][2],
                )

            with (
                patch.object(core_p0_ingest, "RAW_ROOT", raw_root),
                patch.object(core_p0_ingest, "RUN_DATE", "20300103"),
            ):
                other_day = core_p0_ingest._validated_same_day_resume(
                    service_name=service,
                    source_id=source_id,
                    page_size=2,
                    total_count=5,
                    live_first_body=page_specs[0][2],
                )
            self.assertEqual(other_day["page_count"], 0)

    def test_seoul_fetch_has_a_hard_per_attempt_deadline(self) -> None:
        import time as time_module

        def hung_get(*_args: object, **_kwargs: object) -> tuple[int, bytes, dict[str, str]]:
            time_module.sleep(0.1)
            return 200, b"{}", {}

        with (
            patch.object(seoul_transport, "http_get", side_effect=hung_get),
            self.assertRaisesRegex(TimeoutError, "hard deadline"),
        ):
            seoul_transport.fetch_api_with_retries(
                "http://example.invalid",
                "ServiceA",
                attempts=1,
                socket_timeout_seconds=1,
                hard_timeout_seconds=0.01,
            )

    def test_sampled_match_only_skips_with_recent_complete_full_collection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            raw_root = Path(directory)
            service = "ServiceA"
            source_id = "source_a"
            snapshot = raw_root / "20260715" / "seoul_open_data" / "full" / service
            snapshot.mkdir(parents=True)
            body = json.dumps(
                {
                    service: {
                        "list_total_count": 2,
                        "row": [{"STDR_YYQU_CD": "20261"}],
                    }
                }
            ).encode("utf-8")
            (snapshot / f"{service}_1_1000.json").write_bytes(body)
            manifest = raw_root / "ingest_manifest.csv"
            state_path = raw_root / "source_state_catalog.json"
            state_path.write_text(
                json.dumps(
                    {
                        "services": {
                            service: {
                                "total_count": 2,
                                "data_period_start": "20191",
                                "data_period_end": "20261",
                                "latest_window_period_start": "20211",
                                "latest_window_period_end": "20261",
                                "retained_period_start": "20191",
                                "retained_period_end": "20261",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            def write_completion(completed_at: datetime) -> None:
                with manifest.open("w", encoding="utf-8-sig", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=ingest_common.MANIFEST_FIELDS)
                    writer.writeheader()
                    writer.writerow(
                        {
                            "run_id": "run_a",
                            "source_id": source_id,
                            "snapshot_date": "2026-07-15",
                            "raw_path": str(snapshot / f"{service}_1_1000.json"),
                            "request_params_json": json.dumps({"service": service}),
                        }
                    )
                ingest_common.mark_manifest_run_complete(
                    run_id_value="run_a",
                    source_id=source_id,
                    service_name=service,
                    completed_at=completed_at.isoformat(),
                )

            with (
                patch.object(ingest_common, "RAW_ROOT", raw_root),
                patch.object(ingest_common, "SOURCE_STATE_PATH", state_path),
                patch.dict(os.environ, {}, clear=False),
            ):
                write_completion(datetime.now(timezone.utc) - timedelta(hours=1))
                recent = ingest_common.classify_seoul_probe(
                    source_id=source_id,
                    service_name=service,
                    total_count=2,
                    sample_bodies={(1, 1000): body},
                    ttl_hours=24,
                )
                with manifest.open("a", encoding="utf-8-sig", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=ingest_common.MANIFEST_FIELDS)
                    writer.writerow(
                        {
                            "run_id": "partial_run",
                            "source_id": source_id,
                            "raw_path": str(snapshot / f"{service}_1_1000.json"),
                            "request_params_json": json.dumps({"service": service}),
                            "collected_at": datetime.now(timezone.utc).isoformat(),
                        }
                    )
                retry_after_unlogged_partial = ingest_common.classify_seoul_probe(
                    source_id=source_id,
                    service_name=service,
                    total_count=2,
                    sample_bodies={(1, 1000): body},
                    ttl_hours=24,
                )
                failed_path = raw_root / "failed_downloads.csv"
                with failed_path.open("w", encoding="utf-8-sig", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=ingest_common.FAILED_FIELDS)
                    writer.writeheader()
                    writer.writerow(
                        {
                            "source_id": source_id,
                            "attempted_at": datetime.now(timezone.utc).isoformat(),
                            "failure_type": "TimeoutError",
                        }
                    )
                retry_after_partial = ingest_common.classify_seoul_probe(
                    source_id=source_id,
                    service_name=service,
                    total_count=2,
                    sample_bodies={(1, 1000): body},
                    ttl_hours=24,
                )
                write_completion(datetime.now(timezone.utc) - timedelta(hours=25))
                stale = ingest_common.classify_seoul_probe(
                    source_id=source_id,
                    service_name=service,
                    total_count=2,
                    sample_bodies={(1, 1000): body},
                    ttl_hours=24,
                )

            self.assertEqual(recent["status"], "unchanged_sampled")
            self.assertTrue(recent["sampled_skip_allowed"])
            self.assertEqual(recent["data_period_start"], "20211")
            self.assertEqual(recent["data_period_end"], "20261")
            self.assertEqual(retry_after_unlogged_partial["status"], "sample_match_full_refresh_due")
            self.assertFalse(retry_after_unlogged_partial["sampled_skip_allowed"])
            self.assertTrue(retry_after_unlogged_partial["incomplete_after_full_collection"])
            self.assertEqual(retry_after_partial["status"], "sample_match_full_refresh_due")
            self.assertFalse(retry_after_partial["sampled_skip_allowed"])
            self.assertTrue(retry_after_partial["failure_after_full_collection"])
            self.assertEqual(stale["status"], "sample_match_full_refresh_due")
            self.assertFalse(stale["sampled_skip_allowed"])

    def test_raw_signature_includes_older_cumulative_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_root = Path(directory)
            raw_root = data_root / "_raw_ingest"
            newest = raw_root / "20260716" / "seoul_open_data" / "full" / "VwsmTrdarSelngQq"
            newest.mkdir(parents=True)
            (newest / "VwsmTrdarSelngQq_1_1000.json").write_text("new", encoding="utf-8")
            with (
                patch.object(admin_pipeline, "DATA_ROOT", data_root),
                patch.object(admin_pipeline, "INGEST_MANIFEST_PATH", raw_root / "ingest_manifest.csv"),
                patch.object(admin_pipeline, "SOURCE_STATE_PATH", raw_root / "source_state_catalog.json"),
            ):
                before = admin_pipeline._raw_pipeline_signature()
                duplicate = raw_root / "20260717" / "seoul_open_data" / "full" / "VwsmTrdarSelngQq"
                duplicate.mkdir(parents=True)
                (duplicate / "VwsmTrdarSelngQq_1_1000.json").write_text("new", encoding="utf-8")
                duplicate_signature = admin_pipeline._raw_pipeline_signature()
                older = raw_root / "20260715" / "seoul_open_data" / "full" / "VwsmTrdarSelngQq"
                older.mkdir(parents=True)
                (older / "VwsmTrdarSelngQq_1_1000.json").write_text("old", encoding="utf-8")
                after = admin_pipeline._raw_pipeline_signature()
                reverted = raw_root / "20260718" / "seoul_open_data" / "full" / "VwsmTrdarSelngQq"
                reverted.mkdir(parents=True)
                (reverted / "VwsmTrdarSelngQq_1_1000.json").write_text("old", encoding="utf-8")
                reverted_signature = admin_pipeline._raw_pipeline_signature()

            self.assertEqual(before, duplicate_signature)
            self.assertNotEqual(before, after)
            self.assertNotEqual(after, reverted_signature)

    def test_raw_signature_ignores_state_timestamps_but_tracks_raw_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_root = Path(directory)
            raw_root = data_root / "_raw_ingest"
            raw_root.mkdir()
            raw_service = raw_root / "20260716" / "seoul_open_data" / "full" / "VwsmTrdarSelngQq"
            raw_service.mkdir(parents=True)
            raw_page = raw_service / "VwsmTrdarSelngQq_1_1000.json"
            raw_page.write_text("content-a", encoding="utf-8")
            state_path = raw_root / "source_state_catalog.json"

            def write_state(updated_at: str, fingerprint: str) -> None:
                state_path.write_text(
                    json.dumps(
                        {
                            "updated_at": updated_at,
                            "services": {
                                "VwsmTrdarSelngQq": {
                                    "updated_at": updated_at,
                                    "total_count": 1,
                                    "data_period_start": "20261",
                                    "data_period_end": "20261",
                                    "full_content_fingerprint": fingerprint,
                                }
                            },
                        }
                    ),
                    encoding="utf-8",
                )

            with (
                patch.object(admin_pipeline, "DATA_ROOT", data_root),
                patch.object(admin_pipeline, "INGEST_MANIFEST_PATH", raw_root / "ingest_manifest.csv"),
                patch.object(admin_pipeline, "SOURCE_STATE_PATH", state_path),
            ):
                write_state("2026-07-16T00:00:00Z", "content-a")
                first = admin_pipeline._raw_pipeline_signature()
                write_state("2026-07-16T01:00:00Z", "content-a")
                timestamp_changed = admin_pipeline._raw_pipeline_signature()
                write_state("2026-07-16T01:00:00Z", "content-b")
                state_fingerprint_changed = admin_pipeline._raw_pipeline_signature()
                raw_page.write_text("content-b", encoding="utf-8")
                fingerprint_changed = admin_pipeline._raw_pipeline_signature()

            self.assertEqual(first, timestamp_changed)
            self.assertEqual(timestamp_changed, state_fingerprint_changed)
            self.assertNotEqual(timestamp_changed, fingerprint_changed)

    def test_checkpoint_reuse_is_limited_to_full_refresh_transform_steps(self) -> None:
        self.assertFalse(admin_pipeline._checkpoint_reuse_allowed(JOB_DEFINITIONS["seoul_sales"], 1))
        self.assertFalse(
            admin_pipeline._checkpoint_reuse_allowed(JOB_DEFINITIONS["refresh_product_data"], 1)
        )
        self.assertTrue(
            admin_pipeline._checkpoint_reuse_allowed(JOB_DEFINITIONS["refresh_product_data"], 3)
        )
        self.assertTrue(
            admin_pipeline._checkpoint_reuse_allowed(JOB_DEFINITIONS["refresh_product_data"], 14)
        )
        self.assertFalse(
            admin_pipeline._checkpoint_reuse_allowed(JOB_DEFINITIONS["refresh_product_data"], 15)
        )
        self.assertFalse(
            admin_pipeline._checkpoint_reuse_allowed(JOB_DEFINITIONS["refresh_product_data"], 16)
        )
        self.assertFalse(
            admin_pipeline._checkpoint_reuse_allowed(JOB_DEFINITIONS["refresh_product_data"], 17)
        )
        self.assertFalse(admin_pipeline._checkpoint_reuse_allowed(JOB_DEFINITIONS["build_scores"], 1))

    def test_pipeline_checkpoint_chain_preserves_every_upstream_change(self) -> None:
        unchanged_output = "same-output"
        first_chain = admin_pipeline._advance_pipeline_signature("earlier-input-a", unchanged_output)
        changed_chain = admin_pipeline._advance_pipeline_signature("earlier-input-b", unchanged_output)

        self.assertNotEqual(first_chain, changed_chain)
        self.assertNotEqual(
            admin_pipeline._advance_pipeline_signature(first_chain, "next-output"),
            admin_pipeline._advance_pipeline_signature(changed_chain, "next-output"),
        )

    def test_partial_latest_snapshot_and_failed_domain_cannot_publish(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            raw_dir = Path(directory)
            service = "VwsmTrdarSelngQq"
            (raw_dir / f"{service}_1_1000.json").write_text(
                json.dumps(
                    {
                        service: {
                            "list_total_count": 2,
                            "row": [{"STDR_YYQU_CD": "20261"}],
                        }
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "부분 수집"):
                trade_preprocess._assert_latest_snapshot_complete(raw_dir, service, [])

        domain = trade_preprocess.pd.DataFrame([{"table": "sales", "judgement": "FAIL"}])
        grain = trade_preprocess.pd.DataFrame([{"table": "sales", "judgement": "PASS"}])
        contract = trade_preprocess.pd.DataFrame([{"table": "sales", "contract_status": "FAIL"}])
        meta = {
            "latest_window_rows": 1,
            "api_total_count": 2,
            "latest_snapshot_contract": {"raw_response_rows": 1},
        }
        with self.assertRaisesRegex(RuntimeError, "no Silver"):
            trade_preprocess.assert_prepublication_contract(domain, grain, contract, meta, meta)

    def test_refresh_postcondition_validates_product_database_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gold = root / "_gold"
            gold.mkdir()
            (gold / "gold_sales_strength_q_industry.csv").write_text(
                "기준_년분기_코드,value\n20254,a\n20261,b\n",
                encoding="utf-8-sig",
            )
            database = root / "product.db"
            connection = sqlite3.connect(database)
            connection.executescript(
                f"""
                CREATE TABLE commercial_area(area_code TEXT, area_name TEXT, district_code TEXT);
                INSERT INTO commercial_area VALUES ('1','a','d');
                CREATE TABLE district_population(area_code TEXT,resident_population INT,worker_population INT);
                INSERT INTO district_population VALUES ('1',1,1);
                CREATE TABLE district_floating(area_code TEXT,floating_population INT);
                INSERT INTO district_floating VALUES ('1',1);
                CREATE TABLE district_sales(area_code TEXT,industry_code TEXT,sales_amount REAL,timestamp TEXT);
                INSERT INTO district_sales VALUES ('1','i',1,'20261');
                CREATE TABLE district_store_count(area_code TEXT,industry_code TEXT,store_count INT,timestamp TEXT);
                INSERT INTO district_store_count VALUES ('1','i',1,'20261');
                CREATE TABLE district_growth_history(area_code TEXT,sales_amount REAL,floating_population INT,store_count INT,timestamp TEXT);
                INSERT INTO district_growth_history VALUES ('1',1,1,1,'20261');
                CREATE TABLE area_sale_price_proxy(
                    area_code TEXT,sale_price_proxy_manwon_per_m2 REAL,period TEXT,source_id TEXT,
                    direct_score_allowed INT,proxy_score_allowed INT
                );
                INSERT INTO area_sale_price_proxy VALUES ('1',1,'20261','sale',0,1);
                CREATE TABLE area_rone_cost_reference(
                    area_code TEXT,period TEXT,selection_group TEXT,metric_code TEXT,metric_value REAL,
                    direct_value_allowed INT,proxy_score_allowed INT,engine_promotion_ready INT,
                    forbidden_claim_ko TEXT
                );
                INSERT INTO area_rone_cost_reference VALUES (
                    '1','20261','최신 지역별 임대료','rent',1,0,0,0,'evidence only'
                );
                CREATE TABLE industry_hierarchy(industry_code TEXT,industry_name TEXT);
                INSERT INTO industry_hierarchy VALUES ('i','industry');
                CREATE TABLE location_lookup(area_code TEXT,district_name TEXT);
                INSERT INTO location_lookup VALUES ('1','district');
                CREATE TABLE rule_location_score(
                    quarter TEXT,area_code TEXT,industry_code TEXT,score_version TEXT,
                    current_location_score REAL,context_location_score REAL,grade TEXT,
                    score_coverage_tier TEXT,available_axis_count INT,
                    official_indicator_count INT,official_indicator_defined_count INT,
                    official_indicator_complete INT,
                    missing_axes TEXT,coverage_reason TEXT,taxonomy_direct_score_allowed INT,
                    official_rank_eligible INT,data_reliability_score REAL
                );
                INSERT INTO rule_location_score(
                    quarter,area_code,industry_code,score_version,current_location_score,
                    context_location_score,grade,score_coverage_tier,available_axis_count,
                    official_indicator_count,official_indicator_defined_count,
                    official_indicator_complete,missing_axes,coverage_reason,
                    taxonomy_direct_score_allowed,official_rank_eligible,data_reliability_score
                ) VALUES (
                    '20261','1','i','{admin_pipeline.LOCATION_SCORE_VERSION}',1,1,'C',
                    'full_4axis',4,14,14,1,'','ok',1,1,80
                );
                CREATE TABLE rule_area_score_summary(
                    quarter TEXT,area_code TEXT,score_version TEXT,top_industry_status TEXT,
                    score_definition TEXT
                );
                INSERT INTO rule_area_score_summary VALUES (
                    '20261','1','{admin_pipeline.AREA_SCORE_VERSION}','withheld','area_context'
                );
                CREATE TABLE spatial_store_point(id INT); INSERT INTO spatial_store_point VALUES (1);
                CREATE TABLE spatial_store_point_rtree(id INT); INSERT INTO spatial_store_point_rtree VALUES (1);
                CREATE TABLE spatial_transit_point(id INT); INSERT INTO spatial_transit_point VALUES (1);
                CREATE TABLE spatial_transit_point_rtree(id INT); INSERT INTO spatial_transit_point_rtree VALUES (1);
                CREATE TABLE spatial_dataset_status(dataset_key TEXT,record_count INT);
                INSERT INTO spatial_dataset_status VALUES ('store_point',1),('transit_point',1);
                CREATE TABLE users(id INT,is_admin INT); INSERT INTO users VALUES (1,1);
                """
            )
            connection.commit()
            connection.close()

            result = admin_pipeline._assert_refresh_product_postconditions(
                database_path=database,
                data_root=root,
            )
            self.assertEqual(result["expected_quarter"], "20261")
            connection = sqlite3.connect(database)
            connection.execute("UPDATE area_rone_cost_reference SET direct_value_allowed = 1")
            connection.commit()
            connection.close()
            with self.assertRaisesRegex(RuntimeError, "rone_unsafe_contract_flags"):
                admin_pipeline._assert_refresh_product_postconditions(
                    database_path=database,
                    data_root=root,
                )
            connection = sqlite3.connect(database)
            connection.execute("UPDATE area_rone_cost_reference SET direct_value_allowed = 0")
            connection.execute("UPDATE rule_location_score SET score_version = 'stale'")
            connection.commit()
            connection.close()
            with self.assertRaisesRegex(RuntimeError, "location_score_versions"):
                admin_pipeline._assert_refresh_product_postconditions(
                    database_path=database,
                    data_root=root,
                )

    def test_step_signature_tracks_imported_helper_and_result_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scripts = root / "scripts"
            scripts.mkdir()
            (scripts / "main.py").write_text("from helper import VALUE\n", encoding="utf-8")
            helper = scripts / "helper.py"
            helper.write_text("VALUE = 1\n", encoding="utf-8")
            with patch.object(admin_pipeline, "WORKSPACE_ROOT", root):
                first = admin_pipeline._step_input_signature(
                    resolved=["scripts/main.py"],
                    base_signature="raw",
                    upstream_signature="upstream",
                    environment={"LOCALFIT_GOLD_DIR": "gold-a"},
                )
                helper.write_text("VALUE = 2\n", encoding="utf-8")
                helper_changed = admin_pipeline._step_input_signature(
                    resolved=["scripts/main.py"],
                    base_signature="raw",
                    upstream_signature="upstream",
                    environment={"LOCALFIT_GOLD_DIR": "gold-a"},
                )
                environment_changed = admin_pipeline._step_input_signature(
                    resolved=["scripts/main.py"],
                    base_signature="raw",
                    upstream_signature="upstream",
                    environment={"LOCALFIT_GOLD_DIR": "gold-b"},
                )

            self.assertNotEqual(first, helper_changed)
            self.assertNotEqual(helper_changed, environment_changed)

    def test_score_step_signature_tracks_declared_weight_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "scripts" / "build_rule_based_location_scores.py"
            script.parent.mkdir()
            script.write_text("VALUE = 1\n", encoding="utf-8")
            data_root = root / "datacorpus"
            weights = data_root / "_score_backtest" / "location_score_backtest_recommended_weights.csv"
            transit = data_root / "_rule_validation" / "59_transit_accessibility_candidate_quarter_features.csv"
            weights.parent.mkdir(parents=True)
            transit.parent.mkdir(parents=True)
            weights.write_text("weight\n1\n", encoding="utf-8")
            transit.write_text("feature\n1\n", encoding="utf-8")
            with (
                patch.object(admin_pipeline, "WORKSPACE_ROOT", root),
                patch.object(admin_pipeline, "DATA_ROOT", data_root),
            ):
                before = admin_pipeline._step_input_signature(
                    resolved=["scripts/build_rule_based_location_scores.py"],
                    base_signature="raw",
                    upstream_signature="upstream",
                    environment={},
                )
                weights.write_text("weight\n2\n", encoding="utf-8")
                after = admin_pipeline._step_input_signature(
                    resolved=["scripts/build_rule_based_location_scores.py"],
                    base_signature="raw",
                    upstream_signature="upstream",
                    environment={},
                )
            self.assertNotEqual(before, after)

    def test_gold_step_signature_tracks_the_silver_input_tree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "scripts" / "build_rule_engine_gold_tables.py"
            script.parent.mkdir()
            script.write_text("VALUE = 1\n", encoding="utf-8")
            helper_paths = [
                root / "scripts" / name
                for name in (
                    "build_growth_label_candidates.py",
                    "validate_growth_rebound_stability.py",
                    "build_growth_rebound_candidate_gold.py",
                )
            ]
            for helper in helper_paths:
                helper.write_text("VALUE = 1\n", encoding="utf-8")
            data_root = root / "datacorpus"
            silver = data_root / "_silver"
            silver.mkdir(parents=True)
            source = silver / "silver_source.csv"
            source.write_text("value\n1\n", encoding="utf-8")
            with (
                patch.object(admin_pipeline, "WORKSPACE_ROOT", root),
                patch.object(admin_pipeline, "DATA_ROOT", data_root),
            ):
                before = admin_pipeline._step_input_signature(
                    resolved=["scripts/build_rule_engine_gold_tables.py"],
                    base_signature="raw",
                    upstream_signature="upstream",
                    environment={},
                )
                source.write_text("value\nchanged\n", encoding="utf-8")
                after = admin_pipeline._step_input_signature(
                    resolved=["scripts/build_rule_engine_gold_tables.py"],
                    base_signature="raw",
                    upstream_signature="upstream",
                    environment={},
                )
                helper_paths[0].write_text("VALUE = 2\n", encoding="utf-8")
                after_helper_change = admin_pipeline._step_input_signature(
                    resolved=["scripts/build_rule_engine_gold_tables.py"],
                    base_signature="raw",
                    upstream_signature="upstream",
                    environment={},
                )
            self.assertNotEqual(before, after)
            self.assertNotEqual(after, after_helper_change)

    def test_lookup_signature_tracks_inputs_without_hashing_its_own_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "scripts" / "build_rule_engine_input_lookup_tables.py"
            script.parent.mkdir()
            script.write_text("VALUE = 1\n", encoding="utf-8")
            data_root = root / "datacorpus"
            gold = data_root / "_gold"
            silver = data_root / "_silver"
            gold.mkdir(parents=True)
            silver.mkdir(parents=True)
            inputs = (
                gold / "gold_trade_area_profile.csv",
                gold / "gold_industry_taxonomy.csv",
                silver / "silver_trade_area_boundary_spatial_index.csv",
                silver / "silver_trade_area_boundary_vertices.csv",
            )
            for path in inputs:
                path.write_text("value\n1\n", encoding="utf-8")
            with (
                patch.object(admin_pipeline, "WORKSPACE_ROOT", root),
                patch.object(admin_pipeline, "DATA_ROOT", data_root),
            ):
                before = admin_pipeline._step_input_signature(
                    resolved=["scripts/build_rule_engine_input_lookup_tables.py"],
                    base_signature="raw",
                    upstream_signature="upstream",
                    environment={},
                )
                (gold / "gold_location_input_lookup.csv").write_text(
                    "generated\n1\n", encoding="utf-8"
                )
                after_own_output = admin_pipeline._step_input_signature(
                    resolved=["scripts/build_rule_engine_input_lookup_tables.py"],
                    base_signature="raw",
                    upstream_signature="upstream",
                    environment={},
                )
                inputs[0].write_text("value\nchanged\n", encoding="utf-8")
                after_input_change = admin_pipeline._step_input_signature(
                    resolved=["scripts/build_rule_engine_input_lookup_tables.py"],
                    base_signature="raw",
                    upstream_signature="upstream",
                    environment={},
                )
            self.assertEqual(before, after_own_output)
            self.assertNotEqual(after_own_output, after_input_change)

    def test_latest_raw_path_uses_newest_complete_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            raw_root = Path(directory)
            older = raw_root / "20260701" / "seoul_open_data" / "full" / "ServiceA"
            newest_complete = raw_root / "20260702" / "seoul_open_data" / "full" / "ServiceA"
            newest_incomplete = raw_root / "20260703" / "seoul_open_data" / "full" / "ServiceA"
            for path in (older, newest_incomplete, newest_complete):
                path.mkdir(parents=True)
            (older / "ServiceA_001.json").write_text("{}", encoding="utf-8")
            (newest_complete / "ServiceA_001.json").write_text("{}", encoding="utf-8")
            (newest_incomplete / "ServiceA_001.json").write_text("{}", encoding="utf-8")
            manifest = raw_root / "ingest_manifest.csv"
            fields = (
                "run_id",
                "source_id",
                "raw_path",
                "request_params_json",
                "full_collection_status",
                "full_collection_completed_at",
                "collected_at",
            )
            with manifest.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerow(
                    {
                        "run_id": "older_complete",
                        "source_id": "source_a",
                        "raw_path": newest_complete / "ServiceA_001.json",
                        "request_params_json": json.dumps({"service": "ServiceA"}),
                        "full_collection_status": "complete",
                        "full_collection_completed_at": "2026-07-02T12:00:00+00:00",
                        "collected_at": "2026-07-02T11:59:00+00:00",
                    }
                )
                writer.writerow(
                    {
                        "run_id": "same_day_complete",
                        "source_id": "source_a",
                        "raw_path": newest_incomplete / "ServiceA_001.json",
                        "request_params_json": json.dumps({"service": "ServiceA"}),
                        "full_collection_status": "complete",
                        "full_collection_completed_at": "2026-07-03T10:00:00+00:00",
                        "collected_at": "2026-07-03T09:59:00+00:00",
                    }
                )
                writer.writerow(
                    {
                        "run_id": "cancelled_partial",
                        "source_id": "source_a",
                        "raw_path": newest_incomplete / "ServiceA_001.json",
                        "request_params_json": json.dumps({"service": "ServiceA"}),
                        "collected_at": "2026-07-03T11:00:00+00:00",
                    }
                )

            with (
                patch.object(ingest_common, "RAW_ROOT", raw_root),
                patch.dict(os.environ, {}, clear=False),
            ):
                os.environ.pop("LOCALFIT_RAW_RUN_DATE", None)
                os.environ.pop("LOCALFIT_RUN_DATE", None)
                resolved = ingest_common.latest_raw_path(
                    "seoul_open_data", "full", "ServiceA", required_glob="ServiceA_*.json"
                )

            self.assertEqual(resolved, newest_complete)

    def test_run_date_supports_valid_override(self) -> None:
        with patch.dict(os.environ, {"LOCALFIT_RUN_DATE": "20301231"}):
            self.assertEqual(ingest_common.run_date(), "20301231")

    def test_refresh_job_chains_collection_through_spatial_publish(self) -> None:
        definition = JOB_DEFINITIONS["refresh_product_data"]
        commands = [step.args for step in definition.steps]

        self.assertEqual(
            commands[0],
            (
                "scripts/ingest_seoul_core_p0_full.py",
                "--include-store",
                "--skip-unchanged",
            ),
        )
        self.assertEqual(
            commands[1],
            ("scripts/ingest_seoul_sales_trade_area_full.py", "--skip-unchanged"),
        )
        self.assertIn(("scripts/build_rule_based_location_scores.py", "--batch"), commands)
        self.assertIn(("scripts/build_rule_engine_input_lookup_tables.py",), commands)
        self.assertEqual(
            commands[-3:],
            [
                ("final_proj/backend/scripts/seed_rule_gold_db.py",),
                ("final_proj/backend/scripts/seed_spatial_index.py",),
                ("scripts/validate_product_score_grounding.py",),
            ],
        )
        self.assertTrue(definition.requires_confirmation)

    def test_status_check_is_read_only_core_source_probe(self) -> None:
        definition = JOB_DEFINITIONS["status_check"]

        self.assertEqual(definition.group, "system")
        self.assertFalse(definition.requires_confirmation)
        self.assertEqual(definition.source_ids, admin_pipeline.CORE_PRODUCT_SOURCE_IDS)
        self.assertEqual(
            [step.args for step in definition.steps],
            [("scripts/check_core_source_freshness.py",)],
        )

    def test_freshness_report_reader_rejects_stale_job_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / "core_source_freshness_report.json"
            report_path.write_text(
                json.dumps(
                    {
                        "schema_version": "core_source_freshness.v1",
                        "job_id": 42,
                        "overall_status": "up_to_date",
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(admin_pipeline, "CORE_SOURCE_FRESHNESS_REPORT_PATH", report_path):
                self.assertIsNone(admin_pipeline._read_core_source_freshness_report(41))
                self.assertEqual(
                    admin_pipeline._read_core_source_freshness_report(42)["overall_status"],
                    "up_to_date",
                )

    def test_seed_chooses_latest_batch_for_current_gold_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gold_root = root / "gold"
            output_root = root / "outputs"
            validation_root = root / "gold_validation"
            for path in (gold_root, output_root, validation_root):
                path.mkdir()

            sales_path = gold_root / "gold_sales_strength_q_industry.csv"
            sales_path.write_text("기준_년분기_코드\n20254\n20261\n", encoding="utf-8-sig")
            gold_manifest = validation_root / "23_gold_output_manifest.csv"
            gold_manifest.write_text("release_id\nrule_gold_test\n", encoding="utf-8-sig")
            gold_hash = seed_rule_gold_db.sha256_file(gold_manifest)

            def write_batch(stamp: str, generated_at: str, lineage_hash: str = gold_hash) -> Path:
                batch_path = output_root / f"loc_score_v2_batch_20261_{stamp}.csv"
                batch_path.write_text(
                    "기준_년분기_코드,score_version\n"
                    f"20261,{seed_rule_gold_db.COVERAGE_SCORE_VERSION}\n",
                    encoding="utf-8-sig",
                )
                manifest_path = batch_path.with_suffix(".manifest.json")
                manifest_path.write_text(
                    json.dumps(
                        {
                            "schema_version": "localfit.score_batch_manifest.v1",
                            "batch_path": batch_path.relative_to(root).as_posix(),
                            "batch_sha256": seed_rule_gold_db.sha256_file(batch_path),
                            "analysis_quarter": "20261",
                            "score_version": seed_rule_gold_db.COVERAGE_SCORE_VERSION,
                            "row_count": 1,
                            "generated_at": generated_at,
                            "gold_manifest_sha256": lineage_hash,
                        }
                    ),
                    encoding="utf-8",
                )
                return batch_path

            write_batch("20260101_000000", "2026-01-01T00:00:00+00:00")
            latest_valid = write_batch("20260102_000000", "2026-01-02T00:00:00+00:00")
            write_batch("20990101_000000", "2099-01-01T00:00:00+00:00", "stale-gold")

            with (
                patch.object(seed_rule_gold_db, "GOLD", gold_root),
                patch.object(seed_rule_gold_db, "GOLD_MANIFEST", gold_manifest),
                patch.object(seed_rule_gold_db, "LOCATION_OUTPUTS", output_root),
                patch.object(seed_rule_gold_db, "WORKSPACE_ROOT", root),
            ):
                selected = seed_rule_gold_db.latest_score_batch()

            self.assertEqual(selected, latest_valid)


if __name__ == "__main__":
    unittest.main()
