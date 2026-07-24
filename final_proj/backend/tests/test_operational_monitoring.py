# -*- coding: utf-8 -*-
import os
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

# Workspace path setup
WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = WORKSPACE_ROOT / "final_proj" / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from fastapi.testclient import TestClient
from main import app
from app.database import get_db, SessionLocal
from app.models.commercial_area import User, SavedReport, PDFExportHistory, TokenUsageLog
from app.ai.recursive_layer import token_usage_var
from app.services.interpretive_report import ReportGenerationError
from langchain_core.outputs import ChatGeneration, LLMResult
from langchain_core.messages import AIMessage

class TestOperationalMonitoring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.db = SessionLocal()
        
        # Create a unique test user
        cls.test_email = f"test_ops_{datetime.now().strftime('%Y%m%d%H%M%S')}@example.com"
        cls.test_password = "password123"
        cls.test_nickname = "OpsTester"
        
        # Register user
        reg_response = cls.client.post(
            "/api/auth/register",
            json={
                "email": cls.test_email,
                "password": cls.test_password,
                "nickname": cls.test_nickname
            }
        )
        assert reg_response.status_code == 200, f"Registration failed: {reg_response.text}"
        
        # Log in
        login_response = cls.client.post(
            "/api/auth/login",
            data={
                "username": cls.test_email,
                "password": cls.test_password
            }
        )
        assert login_response.status_code == 200, f"Login failed: {login_response.text}"
        cls.token = login_response.json()["access_token"]
        cls.headers = {"Authorization": f"Bearer {cls.token}"}
        
        # Retrieve user object
        cls.user = cls.db.query(User).filter(User.email == cls.test_email).first()
        assert cls.user is not None
        cls.user.is_admin = 1
        cls.db.commit()
        
    @classmethod
    def tearDownClass(cls):
        # Cleanup test user and data
        cls.db.query(PDFExportHistory).filter(PDFExportHistory.user_id == cls.user.id).delete()
        cls.db.query(TokenUsageLog).filter(TokenUsageLog.user_id == cls.user.id).delete()
        cls.db.query(SavedReport).filter(SavedReport.user_id == cls.user.id).delete()
        cls.db.query(User).filter(User.id == cls.user.id).delete()
        cls.db.commit()
        cls.db.close()

    def test_01_pdf_export_history_logging(self):
        # 1. Create a dummy saved report in DB
        report_payload = {
            "narrative_title": "테스트 상권 분석 리포트",
            "executive_interpretation": "매우 좋은 상권입니다.",
            "score_interpretation": "100점",
            "summary": "종합 의견",
            "facts_lite_display": {},
            "alternative_areas": []
        }
        
        save_response = self.client.post(
            "/api/reports/save",
            headers=self.headers,
            json={"report_data": report_payload}
        )
        self.assertEqual(save_response.status_code, 200)
        saved_id = save_response.json()["id"]
        
        # 2. Export report to PDF
        export_payload = {
            "report_data": {
                "id": saved_id,
                "narrative_title": "테스트 상권 분석 리포트",
                "executive_interpretation": "매우 좋은 상권입니다."
            },
            "filename": "Test_Export_Report"
        }
        export_response = self.client.post(
            "/api/reports/export/pdf",
            headers=self.headers,
            json=export_payload
        )
        self.assertEqual(export_response.status_code, 200)
        
        # 3. Verify PDFExportHistory entry was created in DB
        histories = self.db.query(PDFExportHistory).filter(PDFExportHistory.user_id == self.user.id).all()
        self.assertGreater(len(histories), 0)
        self.assertEqual(histories[0].report_id, saved_id)
        self.assertIn("Test_Export_Report", histories[0].filename)
        
        # 4. Test GET /reports/export/history endpoint
        history_get_response = self.client.get(
            "/api/reports/export/history",
            headers=self.headers
        )
        self.assertEqual(history_get_response.status_code, 200)
        history_list = history_get_response.json()
        self.assertGreater(len(history_list), 0)
        self.assertEqual(history_list[0]["report_id"], saved_id)

    @patch("app.services.single_report.interpret_single_report")
    def test_02_token_usage_logging_in_reports(self, mock_interpret):
        # Setup mock behavior that manually triggers callback handler
        def mock_interpret_impl(payload):
            handler = token_usage_var.get()
            if handler:
                message = AIMessage(
                    content="fake",
                    response_metadata={"model_name": "gpt-5.4-mini"},
                    usage_metadata={
                        "input_tokens": 120,
                        "output_tokens": 80,
                        "total_tokens": 200,
                    },
                )
                generation = ChatGeneration(message=message)
                result = LLMResult(generations=[[generation]], llm_output={})
                handler.on_llm_end(result)
            return {
                "summary": "상권 요약",
                "strengths": ["강점1"],
                "weaknesses": ["약점1"],
                "recommended_businesses": ["카페"],
                "risk_factors": ["위험1"],
                "markdown_body": "테스트 마크다운 본문",
                "ai_generated": True,
            }
        
        mock_interpret.side_effect = mock_interpret_impl
        
        # Call generate_single API
        payload = {
            "area_code": "3001491",
            "business_type": "한식음식점",
            "budget": 50000000
        }
        
        print("Sending POST request to generate single report...")
        response = self.client.post(
            "/api/reports/single/generate",
            headers=self.headers,
            json=payload
        )
        print("POST request completed, status code:", response.status_code)
        self.assertEqual(response.status_code, 200)
        
        # Verify TokenUsageLog entry in DB
        print("Checking TokenUsageLog in DB...")
        logs = self.db.query(TokenUsageLog).filter(TokenUsageLog.user_id == self.user.id, TokenUsageLog.feature_name == "single_report").all()
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0].prompt_tokens, 120)
        self.assertEqual(logs[0].completion_tokens, 80)
        self.assertEqual(logs[0].total_tokens, 200)
        self.assertEqual(logs[0].status, "success")
        self.assertIsNotNone(logs[0].reasoning_effort)
        self.assertAlmostEqual(logs[0].estimated_cost, (120 * 0.00000075) + (80 * 0.00000450))
        
        # Test Admin stats endpoint
        print("Testing admin stats endpoint...")
        admin_response = self.client.get(
            "/api/admin/token-usage",
            headers=self.headers
        )
        self.assertEqual(admin_response.status_code, 200)
        stats = admin_response.json()
        self.assertGreaterEqual(stats["summary"]["total_calls"], 1)
        self.assertGreaterEqual(stats["summary"]["total_tokens"], 200)
        self.assertIn("gpt-5.4-mini", stats["model_breakdown"])
        self.assertGreaterEqual(stats["model_breakdown"]["gpt-5.4-mini"]["calls"], 1)
        print("All assertions passed!")

    @patch("app.services.single_report.interpret_single_report")
    def test_02b_deterministic_report_is_returned_but_not_logged_as_success(self, mock_interpret):
        mock_interpret.return_value = {
            "summary": "규칙 기반 요약",
            "strengths": ["근거"],
            "weaknesses": ["확인"],
            "recommended_businesses": [],
            "risk_factors": ["위험"],
            "markdown_body": "규칙 기반 본문",
            "generation_mode": "deterministic",
            "quality_status": "pass",
            "original_validation_issues": ["[FACT_MISMATCH] [field=summary] 테스트"],
            "validation_issues": [],
            "ai_generated": False,
        }

        response = self.client.post(
            "/api/reports/single/generate",
            headers=self.headers,
            json={
                "area_code": "3001491",
                "business_type": "한식음식점",
                "budget": 50000000,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["generation_mode"], "deterministic")
        self.assertFalse(response.json()["ai_generated"])
        log = self.db.query(TokenUsageLog).filter(
            TokenUsageLog.user_id == self.user.id,
            TokenUsageLog.feature_name == "single_report",
            TokenUsageLog.status == "degraded",
        ).order_by(TokenUsageLog.id.desc()).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.generation_mode, "deterministic")
        self.assertIn("FACT_MISMATCH", log.original_validation_issues_json)

    @patch("app.services.single_report.interpret_single_report")
    def test_03_failed_report_attempt_is_visible_and_sanitized(self, mock_interpret):
        mock_interpret.side_effect = ReportGenerationError(
            "single_report",
            RuntimeError("api_key=sk-test-secret C:\\private\\key.md provider unavailable"),
        )

        response = self.client.post(
            "/api/reports/single/generate",
            headers=self.headers,
            json={
                "area_code": "3001491",
                "business_type": "한식음식점",
                "budget": 50000000,
            },
        )

        self.assertEqual(response.status_code, 502)
        attempt_id = response.json()["detail"]["attempt_id"]
        log = self.db.query(TokenUsageLog).filter(TokenUsageLog.id == attempt_id).one()
        self.assertEqual(log.status, "failed")
        self.assertEqual(log.total_tokens, 0)
        self.assertNotIn("sk-test-secret", log.error_message)
        self.assertNotIn("C:\\private", log.error_message)

        admin_response = self.client.get("/api/admin/token-usage", headers=self.headers)
        self.assertEqual(admin_response.status_code, 200)
        admin_payload = admin_response.json()
        self.assertGreaterEqual(admin_payload["summary"]["failed_calls"], 1)
        self.assertTrue(any(item["id"] == attempt_id for item in admin_payload["logs"]))

        error_response = self.client.get("/api/admin/error-logs", headers=self.headers)
        self.assertEqual(error_response.status_code, 200)
        self.assertTrue(
            any(item["id"] == f"report_ai:{attempt_id}" for item in error_response.json()["items"])
        )

if __name__ == "__main__":
    unittest.main()
