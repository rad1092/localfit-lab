from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.database import Base, get_db
from app.dependencies import get_optional_user
from app.models.commercial_area import ReportGenerationJob, User
from app.routers import reports as reports_router
from app.schemas.commercial_area import AIAnalysisResponse, AIComparisonResponse
from main import app


@pytest.fixture
def job_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    database_path = tmp_path / "report_jobs_test.db"
    engine = create_engine(
        f"sqlite:///{database_path.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    test_session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = test_session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(reports_router, "SessionLocal", test_session)
    try:
        with TestClient(app) as client:
            yield client, test_session
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_optional_user, None)
        engine.dispose()


def _clean_single_report() -> AIAnalysisResponse:
    return AIAnalysisResponse(
        summary="백그라운드 작업 테스트 결과",
        strengths=[],
        weaknesses=[],
        recommended_businesses=[],
        risk_factors=[],
        quality_status="pass",
        generation_mode="deterministic",
        ai_generated=False,
    )


def _clean_comparison_report() -> AIComparisonResponse:
    return AIComparisonResponse(
        summary="비교 작업 테스트 결과",
        top_recommendation_name="테스트 상권",
        top_recommendation_reason="비교 작업 계약 확인",
        swot_analysis=[],
        ai_generated=True,
    )


def test_single_report_job_persists_result_and_is_session_scoped(job_client):
    client, test_session = job_client
    session_headers = {
        "X-LocalFit-Session": "report-job-test-session",
        "X-LocalFit-Report-Job": "report-job-test-0001",
    }

    with (
        patch(
            "app.routers.reports._run_report_generation",
            side_effect=lambda generate, **_kwargs: generate(),
        ),
        patch(
            "app.routers.reports._generate_single",
            return_value=_clean_single_report(),
        ),
    ):
        accepted = client.post(
            "/api/reports/jobs/single",
            headers=session_headers,
            json={
                "area_code": "test-area",
                "business_type": "테스트 업종",
                "budget": 5000,
            },
        )
        replayed = client.post(
            "/api/reports/jobs/single",
            headers=session_headers,
            json={
                "area_code": "test-area",
                "business_type": "테스트 업종",
                "budget": 5000,
            },
        )

    assert accepted.status_code == 202
    job_id = accepted.json()["job_id"]
    assert job_id == "report-job-test-0001"
    assert accepted.json()["status"] == "queued"
    assert accepted.json()["report_type"] == "single"
    assert replayed.status_code == 202
    assert replayed.json()["job_id"] == job_id
    assert replayed.json()["status"] == "completed"

    db = test_session()
    try:
        assert db.query(ReportGenerationJob).count() == 1
    finally:
        db.close()

    status_response = client.get(
        f"/api/reports/jobs/{job_id}",
        headers=session_headers,
    )
    assert status_response.status_code == 200
    payload = status_response.json()
    assert payload["status"] == "completed"
    assert payload["result"]["summary"] == "백그라운드 작업 테스트 결과"
    assert payload["result"]["quality_status"] == "pass"

    hidden_from_other_session = client.get(
        f"/api/reports/jobs/{job_id}",
        headers={"X-LocalFit-Session": "different-session"},
    )
    assert hidden_from_other_session.status_code == 404


def test_anonymous_report_job_requires_session_header(job_client):
    client, test_session = job_client
    response = client.post(
        "/api/reports/jobs/single",
        headers={"X-LocalFit-Report-Job": "anonymous-job-no-session"},
        json={
            "area_code": "test-area",
            "business_type": None,
            "budget": None,
        },
    )
    assert response.status_code == 422

    db = test_session()
    try:
        assert db.query(ReportGenerationJob).count() == 0
    finally:
        db.close()


def test_report_job_failure_is_persisted_with_user_safe_message(job_client):
    client, _test_session = job_client
    session_headers = {
        "X-LocalFit-Session": "report-job-failure-test",
        "X-LocalFit-Report-Job": "report-job-failure-0001",
    }

    with (
        patch(
            "app.routers.reports._run_report_generation",
            side_effect=lambda generate, **_kwargs: generate(),
        ),
        patch(
            "app.routers.reports._generate_single",
            side_effect=HTTPException(
                status_code=400,
                detail={
                    "message": "industry unresolved",
                    "options": [
                        {"industry_name": "커피전문점"},
                        "제과점",
                    ],
                },
            ),
        ),
    ):
        accepted = client.post(
            "/api/reports/jobs/single",
            headers=session_headers,
            json={
                "area_code": "test-area",
                "business_type": "모호한 업종",
                "budget": None,
            },
        )

    assert accepted.status_code == 202
    status_response = client.get(
        f"/api/reports/jobs/{accepted.json()['job_id']}",
        headers=session_headers,
    )
    assert status_response.status_code == 200
    payload = status_response.json()
    assert payload["status"] == "failed"
    assert payload["result"] is None
    assert payload["error_message"] == "업종을 확정하지 못했습니다. 후보: 커피전문점, 제과점"


def test_comparison_report_job_uses_same_persistent_contract(job_client):
    client, _test_session = job_client
    headers = {
        "X-LocalFit-Session": "report-job-comparison-test",
        "X-LocalFit-Report-Job": "report-job-comparison-0001",
    }

    with (
        patch(
            "app.routers.reports._run_report_generation",
            side_effect=lambda generate, **_kwargs: generate(),
        ),
        patch(
            "app.routers.reports._generate_comparison",
            return_value=_clean_comparison_report(),
        ),
    ):
        accepted = client.post(
            "/api/reports/jobs/comparison",
            headers=headers,
            json={"area_codes": ["area-a", "area-b"]},
        )

    assert accepted.status_code == 202
    status_response = client.get(
        f"/api/reports/jobs/{accepted.json()['job_id']}",
        headers=headers,
    )
    assert status_response.status_code == 200
    payload = status_response.json()
    assert payload["status"] == "completed"
    assert payload["report_type"] == "comparison"
    assert payload["result"]["summary"] == "비교 작업 테스트 결과"


def test_authenticated_report_job_is_user_scoped(job_client):
    client, test_session = job_client
    db = test_session()
    try:
        user_one = User(
            email="job-owner-one@example.com",
            password_hash="test",
            nickname="owner-one",
            created_at=datetime.now().isoformat(),
            is_admin=0,
        )
        user_two = User(
            email="job-owner-two@example.com",
            password_hash="test",
            nickname="owner-two",
            created_at=datetime.now().isoformat(),
            is_admin=0,
        )
        db.add_all([user_one, user_two])
        db.commit()
        db.refresh(user_one)
        db.refresh(user_two)
        user_one_id = user_one.id
        user_two_id = user_two.id
    finally:
        db.close()

    try:
        app.dependency_overrides[get_optional_user] = lambda: SimpleNamespace(id=user_one_id)
        with (
            patch(
                "app.routers.reports._run_report_generation",
                side_effect=lambda generate, **_kwargs: generate(),
            ),
            patch(
                "app.routers.reports._generate_single",
                return_value=_clean_single_report(),
            ),
        ):
            accepted = client.post(
                "/api/reports/jobs/single",
                headers={"X-LocalFit-Report-Job": "authenticated-job-0001"},
                json={
                    "area_code": "test-area",
                    "business_type": None,
                    "budget": None,
                },
            )
        assert accepted.status_code == 202
        job_id = accepted.json()["job_id"]

        app.dependency_overrides[get_optional_user] = lambda: SimpleNamespace(id=user_two_id)
        assert client.get(f"/api/reports/jobs/{job_id}").status_code == 404

        app.dependency_overrides[get_optional_user] = lambda: SimpleNamespace(id=user_one_id)
        assert client.get(f"/api/reports/jobs/{job_id}").status_code == 200
    finally:
        app.dependency_overrides.pop(get_optional_user, None)


def test_expired_report_jobs_are_purged(job_client):
    client, test_session = job_client
    db = test_session()
    try:
        db.add(
            ReportGenerationJob(
                id="expired-report-job-0001",
                user_id=None,
                client_session_id="expired-session-0001",
                report_type="single",
                request_json="{}",
                status="completed",
                progress_message="완료",
                result_json="{}",
                created_at=(datetime.now() - timedelta(days=8)).isoformat(),
                completed_at=(datetime.now() - timedelta(days=8)).isoformat(),
            )
        )
        db.commit()
    finally:
        db.close()

    response = client.get(
        "/api/reports/jobs/expired-report-job-0001",
        headers={"X-LocalFit-Session": "expired-session-0001"},
    )
    assert response.status_code == 404

    db = test_session()
    try:
        assert db.get(ReportGenerationJob, "expired-report-job-0001") is None
    finally:
        db.close()
