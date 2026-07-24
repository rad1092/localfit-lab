from fastapi.testclient import TestClient

from demo_main import DEMO_NOTICE, app


client = TestClient(app)


def test_demo_metadata_makes_synthetic_scope_explicit():
    response = client.get("/api/demo/meta")

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "execution-demo"
    assert payload["synthetic_data"] is True
    assert payload["notice"] == DEMO_NOTICE


def test_demo_home_and_area_flow():
    rankings = client.get("/api/areas/rankings")
    assert rankings.status_code == 200
    assert len(rankings.json()) >= 5

    area_code = rankings.json()[0]["area_code"]
    area = client.get(f"/api/areas/{area_code}", params={"industry_code": "DEMO-CAFE"})
    assert area.status_code == 200
    payload = area.json()
    assert payload["demo"] is True
    assert payload["industry_analysis"]["industry_name"] == "커피·음료"
    assert payload["industry_analysis"]["score_applicable"] is False


def test_demo_report_job_completes_without_external_services():
    response = client.post(
        "/api/reports/jobs/single",
        json={
            "area_code": "DEMO-HONGDAE",
            "business_type": "커피·음료",
            "budget": 5000,
        },
        headers={"X-LocalFit-Report-Job": "demo-test-job"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "completed"

    job = client.get("/api/reports/jobs/demo-test-job")
    assert job.status_code == 200
    result = job.json()["result"]
    assert result["generation_mode"] == "deterministic"
    assert result["quality_status"] == "pass"
    assert result["ai_generated"] is False
    assert result["demo_notice"] == DEMO_NOTICE


def test_demo_search_and_chatbot_are_useful_without_accounts():
    search = client.get("/api/search", params={"keyword": "성수"})
    assert search.status_code == 200
    assert search.json()[0]["area_code"] == "DEMO-SEONGSU"

    chat = client.post("/api/chatbot/chat", json={"message": "홍대입구역 알려줘"})
    assert chat.status_code == 200
    assert chat.json()["is_guest"] is True
    assert DEMO_NOTICE in chat.json()["text"]
    assert chat.json()["option_payloads"][0]["payload"]["area_code"] == "DEMO-HONGDAE"
