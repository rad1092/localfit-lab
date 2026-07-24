from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = WORKSPACE_ROOT / "final_proj" / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.security import get_password_hash, verify_password
from app.database import Base, get_db
from app.dependencies import get_commercial_area_service, get_current_user
from app.models.commercial_area import CommercialArea, DistrictSales, DistrictStoreCount, User
from app.repositories.commercial_area import CommercialAreaRepository
from app.routers import areas, auth
from app.services.commercial_area import (
    AREA_CONTEXT_SCORE_VERSION,
    EXPECTED_COVERAGE_SCORE_VERSION,
    CommercialAreaService,
)


def _create_contract_client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    db = session_factory()

    db.execute(
        text(
            """
            CREATE TABLE industry_hierarchy (
                industry_code TEXT PRIMARY KEY,
                industry_name TEXT,
                search_text TEXT,
                selection_path TEXT,
                final_algorithm_key TEXT,
                direct_score_allowed INTEGER DEFAULT 0,
                direct_score_blocker_ko TEXT
            )
            """
        )
    )
    db.execute(
        text(
            """
            CREATE TABLE rule_location_score (
                quarter TEXT,
                area_code TEXT,
                area_name TEXT,
                industry_code TEXT,
                industry_name TEXT,
                current_location_score REAL,
                context_location_score REAL,
                grade TEXT,
                decision_label TEXT,
                score_coverage_tier TEXT,
                available_axis_count INTEGER,
                official_indicator_count INTEGER,
                official_indicator_defined_count INTEGER,
                official_indicator_complete INTEGER DEFAULT 0,
                missing_axes TEXT,
                coverage_reason TEXT,
                taxonomy_direct_score_allowed INTEGER DEFAULT 0,
                official_rank_eligible INTEGER DEFAULT 0,
                cost_risk_score REAL,
                data_reliability_score REAL,
                axis_sales REAL,
                axis_competition REAL,
                axis_demand REAL,
                axis_accessibility REAL,
                score_version TEXT,
                PRIMARY KEY (quarter, area_code, industry_code)
            )
            """
        )
    )
    db.execute(
        text(
            """
            CREATE TABLE rule_area_score_summary (
                quarter TEXT,
                area_code TEXT,
                area_name TEXT,
                score REAL,
                score_version TEXT,
                score_definition TEXT,
                PRIMARY KEY (quarter, area_code)
            )
            """
        )
    )

    db.add(CommercialArea(area_code="A1", area_name="테스트 상권", district_code="D1"))
    db.add_all(
        [
            DistrictSales(
                area_code="A1",
                industry_code="I1",
                industry_name="테스트 업종",
                sales_amount=100.0,
                timestamp="20254",
            ),
            DistrictSales(
                area_code="A1",
                industry_code="I1",
                industry_name="테스트 업종",
                sales_amount=150.0,
                timestamp="20261",
            ),
            DistrictStoreCount(
                area_code="A1",
                industry_code="I1",
                industry_name="테스트 업종",
                store_count=4,
                timestamp="20254",
            ),
            DistrictStoreCount(
                area_code="A1",
                industry_code="I1",
                industry_name="테스트 업종",
                store_count=5,
                timestamp="20261",
            ),
        ]
    )
    db.execute(
        text(
            """
            INSERT INTO industry_hierarchy (
                industry_code, industry_name, search_text, selection_path,
                final_algorithm_key, direct_score_allowed, direct_score_blocker_ko
            ) VALUES
                ('I1', '테스트 업종', 'i1 테스트 업종', '대분류 > 테스트 업종', 'I1', 1, NULL),
                ('I2', '자료 없는 업종', 'i2 자료 없는 업종', '대분류 > 자료 없는 업종', 'I2', 1, NULL)
            """
        )
    )
    db.execute(
        text(
            """
            INSERT INTO rule_location_score (
                quarter, area_code, area_name, industry_code, industry_name,
                current_location_score, context_location_score, grade, decision_label,
                score_coverage_tier, available_axis_count, official_indicator_count,
                official_indicator_defined_count, official_indicator_complete,
                missing_axes, coverage_reason, taxonomy_direct_score_allowed,
                official_rank_eligible, cost_risk_score, data_reliability_score,
                axis_sales, axis_competition, axis_demand, axis_accessibility,
                score_version
            ) VALUES (
                '20261', 'A1', '테스트 상권', 'I1', '테스트 업종',
                84.0, 84.0, 'A', '상위 후보군',
                'full_4axis', 4, 12, 12, 1,
                '', '전체 축 사용 가능', 1,
                1, 45.0, 90.0,
                88.0, 59.0, 84.0, 94.0,
                :score_version
            )
            """
        ),
        {"score_version": EXPECTED_COVERAGE_SCORE_VERSION},
    )
    db.execute(
        text(
            """
            INSERT INTO rule_area_score_summary (
                quarter, area_code, area_name, score, score_version, score_definition
            ) VALUES ('20261', 'A1', '테스트 상권', 80.0, :score_version, 'test')
            """
        ),
        {"score_version": AREA_CONTEXT_SCORE_VERSION},
    )

    user = User(
        email="owner@example.com",
        nickname="기존 닉네임",
        password_hash=get_password_hash("old-password"),
        created_at="2026-07-20T00:00:00",
        is_admin=1,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    service = CommercialAreaService(CommercialAreaRepository(db))
    app = FastAPI()
    app.include_router(areas.router, prefix="/api")
    app.include_router(auth.router, prefix="/api")

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_commercial_area_service] = lambda: service
    return TestClient(app), db, user, service


def test_area_industry_analysis_is_additive_and_explicit():
    client, db, _, service = _create_contract_client()
    try:
        base_response = client.get("/api/areas/A1")
        assert base_response.status_code == 200
        assert base_response.json() == service.get_area("A1").model_dump(mode="json")
        assert "industry_analysis" not in base_response.json()

        industry_response = client.get("/api/areas/A1", params={"industry_code": "I1"})
        assert industry_response.status_code == 200
        analysis = industry_response.json()["industry_analysis"]
        assert analysis["industry_code"] == "I1"
        assert analysis["reference_quarter"] == "20261"
        assert analysis["availability"] == "available"
        assert analysis["score_applicable"] is True
        assert analysis["display_grade"] == "A+"
        assert analysis["current_sales_amount"] == 150.0
        assert analysis["current_store_count"] == 5
        assert analysis["history"] == [
            {"quarter": "20254", "sales_amount": 100.0, "store_count": 4},
            {"quarter": "20261", "sales_amount": 150.0, "store_count": 5},
        ]
        assert analysis["axes"]["sales"] == {"internal_value": 88.0, "display_grade": "A"}
        assert analysis["axes"]["competition"] == {"internal_value": 59.0, "display_grade": "C+"}
        assert analysis["axes"]["demand"] == {"internal_value": 84.0, "display_grade": "A"}
        assert analysis["axes"]["accessibility"] == {"internal_value": 94.0, "display_grade": "A+"}
        assert analysis["missing_data"] == []

        invalid_response = client.get("/api/areas/A1", params={"industry_code": "UNKNOWN"})
        assert invalid_response.status_code == 400
        assert invalid_response.json()["detail"]["message"] == "industry unresolved"

        unavailable_response = client.get("/api/areas/A1", params={"industry_code": "I2"})
        assert unavailable_response.status_code == 200
        unavailable = unavailable_response.json()["industry_analysis"]
        assert unavailable["availability"] == "unavailable"
        assert unavailable["score_applicable"] is False
        assert unavailable["display_grade"] is None
        assert unavailable["current_sales_amount"] is None
        assert unavailable["current_store_count"] is None
        assert "rule_score" in unavailable["missing_data"]
    finally:
        client.close()
        db.close()


def test_account_update_validates_password_and_preserves_admin():
    client, db, user, _ = _create_contract_client()
    try:
        nickname_response = client.patch("/api/auth/me", json={"nickname": "  새 닉네임  "})
        assert nickname_response.status_code == 200
        assert nickname_response.json()["nickname"] == "새 닉네임"
        assert nickname_response.json()["is_admin"] is True
        assert verify_password("old-password", user.password_hash)

        wrong_password = client.patch(
            "/api/auth/me",
            json={"current_password": "wrong", "new_password": "new-password"},
        )
        assert wrong_password.status_code == 400
        assert verify_password("old-password", user.password_hash)

        short_password = client.patch(
            "/api/auth/me",
            json={"current_password": "old-password", "new_password": "short"},
        )
        assert short_password.status_code == 422

        blank_nickname = client.patch("/api/auth/me", json={"nickname": "   "})
        assert blank_nickname.status_code == 422

        no_changes = client.patch("/api/auth/me", json={})
        assert no_changes.status_code == 400

        password_response = client.patch(
            "/api/auth/me",
            json={"current_password": "old-password", "new_password": "new-password"},
        )
        assert password_response.status_code == 200
        assert password_response.json()["nickname"] == "새 닉네임"
        assert password_response.json()["is_admin"] is True
        assert verify_password("new-password", user.password_hash)
        assert not verify_password("old-password", user.password_hash)
    finally:
        client.close()
        db.close()
