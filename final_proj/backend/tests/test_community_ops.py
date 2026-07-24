from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.security import create_access_token, get_password_hash
from app.database import Base, get_db
from app.models.commercial_area import CommercialArea, User
from app.models.community import Comment, UserEvent
from main import app


@pytest.fixture()
def community_client(tmp_path, monkeypatch):
    database_path = tmp_path / "community.sqlite3"
    engine = create_engine(
        f"sqlite:///{database_path.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    testing_session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    db = testing_session()
    area = CommercialArea(area_code="A100", area_name="테스트 상권", district_code="D1")
    owner = User(
        email="owner@example.com",
        password_hash=get_password_hash("password123"),
        nickname="작성자",
        created_at="2026-07-20T00:00:00+00:00",
        is_admin=0,
    )
    other = User(
        email="other@example.com",
        password_hash=get_password_hash("password123"),
        nickname="답글러",
        created_at="2026-07-20T00:00:00+00:00",
        is_admin=0,
    )
    administrator = User(
        email="admin@example.com",
        password_hash=get_password_hash("password123"),
        nickname="관리자",
        created_at="2026-07-20T00:00:00+00:00",
        is_admin=1,
    )
    db.add_all([area, owner, other, administrator])
    db.commit()
    for item in (owner, other, administrator):
        db.refresh(item)

    def override_get_db():
        session = testing_session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setenv("LOCALFIT_ENV", "development")
    client = TestClient(app)
    context = {
        "client": client,
        "session_factory": testing_session,
        "owner_headers": {
            "Authorization": f"Bearer {create_access_token({'email': owner.email})}"
        },
        "other_headers": {
            "Authorization": f"Bearer {create_access_token({'email': other.email})}"
        },
        "admin_headers": {
            "Authorization": f"Bearer {create_access_token({'email': administrator.email})}"
        },
    }
    try:
        yield context
    finally:
        client.close()
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()


def test_comments_public_read_member_write_scope_depth_owner_and_moderation(community_client):
    client = community_client["client"]
    owner_headers = community_client["owner_headers"]
    other_headers = community_client["other_headers"]

    assert client.get("/api/areas/A100/comments").status_code == 200
    assert client.post(
        "/api/areas/A100/comments", json={"body": "로그인 필요"}
    ).status_code == 401

    root_response = client.post(
        "/api/areas/A100/comments",
        headers=owner_headers,
        json={"body": "  업종 댓글  ", "industry_code": "I-1"},
    )
    assert root_response.status_code == 201
    root_id = root_response.json()["id"]
    assert root_response.json()["body"] == "업종 댓글"

    assert client.get("/api/areas/A100/comments").json()["total"] == 0
    scoped = client.get("/api/areas/A100/comments?industry_code=I-1").json()
    assert scoped["total"] == 1

    reply_response = client.post(
        "/api/areas/A100/comments",
        headers=other_headers,
        json={"body": "답글", "industry_code": "I-1", "parent_id": root_id},
    )
    assert reply_response.status_code == 201
    reply_id = reply_response.json()["id"]
    nested = client.post(
        "/api/areas/A100/comments",
        headers=owner_headers,
        json={"body": "중첩", "industry_code": "I-1", "parent_id": reply_id},
    )
    assert nested.status_code == 400

    assert client.patch(
        f"/api/comments/{root_id}", headers=other_headers, json={"body": "탈취"}
    ).status_code == 403
    updated = client.patch(
        f"/api/comments/{root_id}", headers=owner_headers, json={"body": "수정됨"}
    )
    assert updated.status_code == 200
    assert updated.json()["body"] == "수정됨"

    deleted = client.delete(f"/api/comments/{root_id}", headers=owner_headers)
    assert deleted.status_code == 200
    public_after_delete = client.get("/api/areas/A100/comments?industry_code=I-1").json()
    assert public_after_delete["total"] == 1
    assert public_after_delete["items"][0]["status"] == "deleted"
    assert public_after_delete["items"][0]["body"] == "삭제되었거나 숨겨진 댓글입니다."
    assert public_after_delete["items"][0]["replies"][0]["body"] == "답글"

    admin_list = client.get("/api/admin/comments?status=deleted")
    assert admin_list.status_code == 200
    assert admin_list.json()["items"][0]["id"] == root_id
    restored = client.patch(
        f"/api/admin/comments/{root_id}/status", json={"status": "visible"}
    )
    assert restored.status_code == 200
    assert restored.json()["status"] == "visible"
    hidden = client.patch(
        f"/api/admin/comments/{root_id}/status", json={"status": "hidden"}
    )
    assert hidden.status_code == 200
    assert hidden.json()["status"] == "hidden"


def test_anonymous_events_allowlist_retention_and_aggregate_only_admin_views(community_client):
    client = community_client["client"]
    session_factory = community_client["session_factory"]
    headers = {"X-LocalFit-Session": "11111111-1111-4111-8111-111111111111"}
    second_headers = {"X-LocalFit-Session": "22222222-2222-4222-8222-222222222222"}

    rejected = client.post(
        "/api/events/log",
        headers=headers,
        json={"event_type": "page_view", "user_id": 99},
    )
    assert rejected.status_code == 422
    assert client.post(
        "/api/events/log", headers=headers, json={"event_type": "unknown"}
    ).status_code == 422

    db = session_factory()
    old = UserEvent(
        session_id="old-old-old-old-1",
        event_type="page_view",
        area_code=None,
        created_at=(datetime.now(timezone.utc) - timedelta(days=31)).isoformat(timespec="seconds"),
    )
    db.add(old)
    db.commit()
    db.close()

    for event_type in (
        "search_submitted",
        "area_selected",
        "report_requested",
        "report_completed",
    ):
        response = client.post(
            "/api/events/log",
            headers=headers,
            json={"event_type": event_type, "area_code": "A100"},
        )
        assert response.status_code == 201
    assert client.post(
        "/api/events/log",
        headers=second_headers,
        json={"event_type": "area_selected", "area_code": "A100"},
    ).status_code == 201

    db = session_factory()
    assert db.query(UserEvent).filter(UserEvent.session_id == "old-old-old-old-1").count() == 0
    assert "user_id" not in UserEvent.__table__.columns
    db.close()

    overview = client.get("/api/admin/analytics/overview").json()
    assert overview["total_events"] == 5
    assert overview["unique_sessions"] == 2
    assert overview["event_counts"]["area_selected"] == 2
    funnel = client.get("/api/admin/analytics/funnel").json()
    assert [stage["event_type"] for stage in funnel["stages"]] == [
        "search_submitted",
        "area_selected",
        "report_requested",
        "report_completed",
    ]
    popular = client.get("/api/admin/analytics/popular-areas").json()["items"]
    assert popular[0]["area_code"] == "A100"
    assert popular[0]["area_name"] == "테스트 상권"
    assert "session_id" not in str(overview)
    assert "session_id" not in str(funnel)
    assert "session_id" not in str(popular)


def test_admin_quality_sanitized_errors_and_environment_access(community_client, monkeypatch):
    client = community_client["client"]
    owner_headers = community_client["owner_headers"]
    admin_headers = community_client["admin_headers"]

    dashboard = {
        "generated_at": "2026-07-20T00:00:00+00:00",
        "summary": {
            "source_count": 45,
            "healthy_source_count": 45,
            "product_quarter": "20261",
        },
        "layers": [
            {
                "key": "raw",
                "label": "원천",
                "status": "healthy",
                "count": 7,
                "unit": "/7",
                "updated_at": "2026-07-20T00:00:00+00:00",
                "note": r"C:\\private\\raw.csv api_key=super-secret",
            },
            {
                "key": "external",
                "label": "외부",
                "status": "advisory",
                "count": 19,
                "unit": "/27",
                "updated_at": None,
                "note": "별도 관리",
            },
        ],
    }
    failed_jobs = [
        {
            "id": 9,
            "status": "failed",
            "label": "검증",
            "message": r"C:\\secret\\worker.py token=abc123 failed",
            "finished_at": "2026-07-20T01:00:00+00:00",
        }
    ]
    external_calls = [
        {
            "id": "db_1",
            "api_name": "Provider",
            "endpoint": "https://example.com/path?api_key=secret",
            "status_code": 500,
            "created_at": "2026-07-20T02:00:00+00:00",
        }
    ]
    with (
        patch("app.routers.admin_ops.admin_dashboard", return_value=dashboard),
        patch("app.routers.admin_ops.list_jobs", return_value=failed_jobs),
        patch("app.routers.admin_ops.get_external_api_calls", return_value=external_calls),
    ):
        summary = client.get("/api/admin/data-quality/summary")
        assert summary.status_code == 200
        assert summary.json()["overall_status"] == "advisory"
        checks_text = str(client.get("/api/admin/data-quality/checks").json())
        assert "super-secret" not in checks_text
        assert "C:\\" not in checks_text
        errors = client.get("/api/admin/error-logs?limit=100").json()
        assert errors["count"] == 2
        errors_text = str(errors)
        assert "api_key=secret" not in errors_text
        assert "abc123" not in errors_text
        assert "C:\\" not in errors_text

    assert client.get("/api/admin/access").json()["local_open"] is True
    monkeypatch.setenv("LOCALFIT_ENV", "production")
    assert client.get("/api/admin/access").status_code == 401
    assert client.get("/api/admin/job-definitions").status_code == 401
    assert client.get("/api/admin/access", headers=owner_headers).status_code == 403
    allowed = client.get("/api/admin/access", headers=admin_headers)
    assert allowed.status_code == 200
    assert allowed.json()["is_admin"] is True
