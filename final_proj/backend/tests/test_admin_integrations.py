import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import HumanMessage


WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = WORKSPACE_ROOT / "final_proj" / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.ai.recursive_layer import calculate_token_cost, get_llm
from app.routers.admin import build_provider_integrations, get_pipeline_api_calls
from app.services.interpretive_report import _cache_key
from app.services.llm_runtime_settings import (
    read_report_reasoning_settings,
    set_report_reasoning_effort,
)


class TestAdminIntegrations(unittest.TestCase):
    def test_pipeline_request_ids_are_unique_for_repeated_source_rows(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            manifest_path = root / "ingest_manifest.csv"
            manifest_path.write_text(
                "run_id,source_id,provider,request_url_redacted,http_status,collected_at\n"
                "run-1,naver_api_hub_news,Naver,https://example.test/a,200,2026-07-15T10:00:00\n"
                "run-1,naver_api_hub_news,Naver,https://example.test/b,200,2026-07-15T10:01:00\n",
                encoding="utf-8",
            )
            failed_directory = root / "_raw_ingest"
            failed_directory.mkdir()
            (failed_directory / "failed_downloads.csv").write_text(
                "run_id,source_id,provider,request_url_redacted,attempted_at\n"
                "run-1,naver_api_hub_news,Naver,https://example.test/c,2026-07-15T10:02:00\n",
                encoding="utf-8",
            )

            with patch("app.routers.admin.INGEST_MANIFEST_PATH", manifest_path), patch(
                "app.routers.admin.DATA_ROOT", root
            ):
                calls = get_pipeline_api_calls()

        ids = [call["id"] for call in calls]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(ids, ["p_success_2", "p_success_3", "p_fail_2"])

    def test_gpt_54_mini_alias_and_snapshot_use_the_same_price(self):
        expected_cost = (120 * 0.75 + 80 * 4.50) / 1_000_000
        self.assertAlmostEqual(calculate_token_cost("gpt-5.4-mini", 120, 80), expected_cost)
        self.assertAlmostEqual(
            calculate_token_cost("gpt-5.4-mini-2026-03-17", 120, 80), expected_cost
        )
        self.assertEqual(calculate_token_cost("unpriced-model", 120, 80), 0.0)

    def test_provider_summary_only_returns_operational_status(self):
        providers = build_provider_integrations(
            [
                {
                    "provider": "Provider A",
                    "credential_status": "configured",
                    "health": "healthy",
                    "failure_rows": 0,
                    "last_collected_at": "2026-07-15T10:00:00",
                    "refresh_available": True,
                },
                {
                    "provider": "Provider A",
                    "credential_status": "missing",
                    "health": "warning",
                    "failure_rows": 2,
                    "last_collected_at": "2026-07-15T11:00:00",
                    "refresh_available": False,
                },
            ]
        )

        self.assertEqual(len(providers), 1)
        provider = providers[0]
        self.assertEqual(provider["credential_status"], "missing")
        self.assertEqual(provider["health"], "warning")
        self.assertEqual(provider["source_count"], 2)
        self.assertEqual(provider["failure_rows"], 2)
        self.assertNotIn("credential_ref", provider)

    def test_report_reasoning_defaults_to_low_and_admin_override_persists(self):
        with tempfile.TemporaryDirectory() as temporary_directory, patch(
            "app.services.llm_runtime_settings.SETTINGS_DB_PATH",
            Path(temporary_directory) / "pipeline_jobs.db",
        ), patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENAI_REPORT_REASONING_EFFORT", None)
            initial = read_report_reasoning_settings()
            updated = set_report_reasoning_effort("high")
            persisted = read_report_reasoning_settings()

        self.assertEqual(initial.reasoning_effort, "low")
        self.assertEqual(initial.source, "default")
        self.assertEqual(updated.reasoning_effort, "high")
        self.assertEqual(updated.source, "admin")
        self.assertEqual(persisted.reasoning_effort, "high")
        with self.assertRaises(ValueError):
            set_report_reasoning_effort("max")

    def test_langchain_request_carries_report_reasoning_effort(self):
        with patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "sk-test", "OPENAI_MODEL": "gpt-5.4-mini"},
            clear=False,
        ):
            llm = get_llm(reasoning_effort="low")
            payload = llm._get_request_payload([HumanMessage(content="probe")])

        self.assertTrue(llm.use_responses_api)
        self.assertEqual(payload["model"], "gpt-5.4-mini")
        self.assertEqual(payload["reasoning"], {"effort": "low"})
        self.assertIn("input", payload)

    def test_report_cache_is_separated_by_model_and_reasoning_effort(self):
        base_payload = {
            "area_code": "100001",
            "industry_code": "CS100001",
            "quarter": "20261",
            "user_condition": {"budget": 10000},
            "_news_evidence_version": "news-v1",
            "_report_model": "gpt-5.4-mini",
        }
        none_key = _cache_key({**base_payload, "_report_reasoning_effort": "none"})
        low_key = _cache_key({**base_payload, "_report_reasoning_effort": "low"})
        other_model_key = _cache_key(
            {
                **base_payload,
                "_report_model": "gpt-5.4-mini-2026-03-17",
                "_report_reasoning_effort": "low",
            }
        )

        self.assertNotEqual(none_key, low_key)
        self.assertNotEqual(low_key, other_model_key)


if __name__ == "__main__":
    unittest.main()
