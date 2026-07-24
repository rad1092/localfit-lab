from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.database import Base, get_db
from app import runtime_schema
from app.models.commercial_area import ReportEvaluationRun, ReportGenerationJob
from app.routers import admin_ops, reports as reports_router
from app.services import report_evaluation


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _single_report() -> dict:
    return {
        "narrative_title": "테스트 상권 AI 상세 리포트",
        "area_name": "테스트 상권",
        "industry_code": "CS100001",
        "industry_name": "한식음식점",
        "ai_model": "test-model",
        "generation_mode": "deterministic",
        "quality_status": "pass",
        "indicator_pack": {
            "target": {
                "area_code": "TEST-AREA-001",
                "area_name": "테스트 상권",
                "industry_code": "CS100001",
                "industry_name": "한식음식점",
                "quarter": "20251",
            }
        },
    }


def _job(
    *,
    job_id: str,
    report_type: str = "single",
    report: dict | None = None,
) -> ReportGenerationJob:
    return ReportGenerationJob(
        id=job_id,
        user_id=None,
        client_session_id=f"session-{job_id}",
        report_type=report_type,
        request_json=json.dumps(
            {
                "area_code": "TEST-AREA-001",
                "business_type": "한식음식점",
                "budget": 5000,
            },
            ensure_ascii=False,
        ),
        status="completed",
        progress_message="완료",
        result_json=json.dumps(report or _single_report(), ensure_ascii=False),
        created_at=_now_iso(),
        completed_at=_now_iso(),
    )


@pytest.fixture
def evaluation_db(tmp_path: Path):
    database_path = tmp_path / "admin_report_evaluations.db"
    engine = create_engine(
        f"sqlite:///{database_path.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    test_session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    try:
        yield test_session
    finally:
        engine.dispose()


@pytest.fixture
def evaluation_client(
    evaluation_db,
    monkeypatch: pytest.MonkeyPatch,
):
    test_app = FastAPI()
    test_app.include_router(admin_ops.router, prefix="/api")

    def override_get_db():
        db = evaluation_db()
        try:
            yield db
        finally:
            db.close()

    test_app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(
        admin_ops,
        "execute_report_evaluation",
        lambda _run_id: None,
    )
    monkeypatch.setattr(
        admin_ops,
        "execute_manual_report_evaluation",
        lambda _run_id: None,
    )
    with TestClient(test_app) as client:
        yield client, evaluation_db


def test_admin_report_evaluation_api_contract_and_active_run_dedupe(
    evaluation_client,
):
    client, test_session = evaluation_client
    db = test_session()
    try:
        db.add(_job(job_id="single-report-job-001"))
        db.commit()
    finally:
        db.close()

    listed = client.get("/api/admin/report-evaluations/reports")
    assert listed.status_code == 200
    listed_body = listed.json()
    assert listed_body["count"] == 1
    assert listed_body["total"] == 1
    assert listed_body["evaluator"]["external_llm_called"] is False
    assert listed_body["evaluator"]["manual_review_question_ids"] == ["Q050", "Q051"]
    candidate = listed_body["items"][0]
    assert candidate["job_id"] == "single-report-job-001"
    assert candidate["context"] == {
        "area_code": "TEST-AREA-001",
        "area_name": "테스트 상권",
        "industry_code": "CS100001",
        "industry_name": "한식음식점",
        "quarter": "20251",
        "budget_manwon": 5000,
        "ai_model": "test-model",
        "generation_mode": "deterministic",
        "quality_status": "pass",
        "evaluable": True,
        "not_evaluable_reason": None,
    }
    assert candidate["latest_evaluation"] is None

    started = client.post(
        "/api/admin/report-evaluations/reports/single-report-job-001/run"
    )
    assert started.status_code == 202
    started_body = started.json()
    assert started_body["report_job_id"] == "single-report-job-001"
    assert started_body["status"] == "queued"
    assert started_body["report_sha256"] == report_evaluation.report_sha256(
        _single_report()
    )

    duplicate = client.post(
        "/api/admin/report-evaluations/reports/single-report-job-001/run"
    )
    assert duplicate.status_code == 202
    assert duplicate.json()["id"] == started_body["id"]

    detail = client.get(
        f"/api/admin/report-evaluations/runs/{started_body['id']}"
    )
    assert detail.status_code == 200
    detail_body = detail.json()
    assert detail_body["id"] == started_body["id"]
    assert detail_body["questions"] == []

    db = test_session()
    try:
        assert db.query(ReportEvaluationRun).count() == 1
    finally:
        db.close()


def test_comparison_report_cannot_start_grounding_evaluation(evaluation_client):
    client, test_session = evaluation_client
    db = test_session()
    try:
        db.add(
            _job(
                job_id="comparison-report-job-001",
                report_type="comparison",
            )
        )
        db.commit()
    finally:
        db.close()

    response = client.post(
        "/api/admin/report-evaluations/reports/comparison-report-job-001/run"
    )
    assert response.status_code == 422
    assert "비교 리포트" in response.json()["detail"]

    db = test_session()
    try:
        assert db.query(ReportEvaluationRun).count() == 0
    finally:
        db.close()


def test_budgetless_report_is_not_evaluable(evaluation_client):
    client, test_session = evaluation_client
    budgetless_job = _job(job_id="budgetless-report-job")
    budgetless_job.request_json = json.dumps(
        {
            "area_code": "TEST-AREA-001",
            "business_type": "한식음식점",
        },
        ensure_ascii=False,
    )
    db = test_session()
    try:
        db.add(budgetless_job)
        db.commit()
    finally:
        db.close()

    listed = client.get("/api/admin/report-evaluations/reports")
    assert listed.status_code == 200
    assert listed.json()["items"][0]["context"]["evaluable"] is False
    assert "예산" in listed.json()["items"][0]["context"]["not_evaluable_reason"]

    response = client.post(
        "/api/admin/report-evaluations/reports/budgetless-report-job/run"
    )
    assert response.status_code == 422
    assert "예산" in response.json()["detail"]


def test_stale_active_run_is_failed_and_replaced(evaluation_client):
    client, test_session = evaluation_client
    job = _job(job_id="stale-run-report-job")
    stale_run = ReportEvaluationRun(
        id="stale-evaluation-run",
        report_job_id=job.id,
        report_sha256=report_evaluation.report_sha256(_single_report()),
        status="running",
        progress_message="평가 중",
        created_at="2000-01-01T00:00:00+00:00",
        started_at="2000-01-01T00:00:00+00:00",
    )
    stale_run_id = stale_run.id
    db = test_session()
    try:
        db.add_all([job, stale_run])
        db.commit()
    finally:
        db.close()

    stale_detail = client.get(
        "/api/admin/report-evaluations/runs/stale-evaluation-run"
    )
    assert stale_detail.status_code == 200
    assert stale_detail.json()["status"] == "failed"

    response = client.post(
        "/api/admin/report-evaluations/reports/stale-run-report-job/run"
    )
    assert response.status_code == 202
    assert response.json()["id"] != stale_run_id
    assert response.json()["status"] == "queued"

    db = test_session()
    try:
        persisted_stale = db.get(ReportEvaluationRun, stale_run_id)
        assert persisted_stale is not None
        assert persisted_stale.status == "failed"
        assert "서버 재시작" in persisted_stale.error_message
        assert db.query(ReportEvaluationRun).count() == 2
    finally:
        db.close()


def test_database_rejects_two_active_runs_for_one_report(evaluation_db):
    job = _job(job_id="unique-active-report-job")
    db = evaluation_db()
    try:
        db.add(job)
        db.commit()
        db.add_all(
            [
                ReportEvaluationRun(
                    id="active-run-one",
                    report_job_id=job.id,
                    report_sha256="a" * 64,
                    status="queued",
                    progress_message="대기",
                    created_at=_now_iso(),
                ),
                ReportEvaluationRun(
                    id="active-run-two",
                    report_job_id=job.id,
                    report_sha256="a" * 64,
                    status="running",
                    progress_message="실행",
                    created_at=_now_iso(),
                ),
            ]
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    finally:
        db.close()


def test_runtime_schema_recovers_legacy_duplicate_active_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    database_path = tmp_path / "legacy-duplicate-runs.db"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                email TEXT,
                is_admin INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE report_evaluation_run (
                id TEXT PRIMARY KEY,
                report_job_id TEXT NOT NULL,
                status TEXT NOT NULL,
                progress_message TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT
            );
            INSERT INTO report_evaluation_run
                (id, report_job_id, status, progress_message, created_at)
            VALUES
                ('older-active', 'same-report', 'queued', '대기', '2026-07-24T01:00:00+00:00'),
                ('newer-active', 'same-report', 'running', '실행', '2026-07-24T02:00:00+00:00'),
                ('completed-run', 'same-report', 'completed', '완료', '2026-07-24T00:00:00+00:00');
            """
        )
    monkeypatch.setattr(runtime_schema, "DATABASE_PATH", database_path)

    runtime_schema.ensure_runtime_schema()

    with sqlite3.connect(database_path) as connection:
        statuses = dict(
            connection.execute(
                "SELECT id, status FROM report_evaluation_run"
            ).fetchall()
        )
        assert statuses["newer-active"] == "running"
        assert statuses["older-active"] == "failed"
        recovered = connection.execute(
            "SELECT error_message, completed_at FROM report_evaluation_run "
            "WHERE id = 'older-active'"
        ).fetchone()
        assert "중복 평가" in recovered[0]
        assert recovered[1]
        index_names = {
            row[1]
            for row in connection.execute(
                "PRAGMA index_list(report_evaluation_run)"
            ).fetchall()
        }
        assert "ux_report_evaluation_active_job" in index_names
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO report_evaluation_run "
                "(id, report_job_id, status, created_at) "
                "VALUES ('third-active', 'same-report', 'queued', ?)",
                (_now_iso(),),
            )


def test_manual_review_submission_and_artifact_api(
    evaluation_client,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    client, test_session = evaluation_client
    job = _job(job_id="manual-review-report-job")
    run = ReportEvaluationRun(
        id="manual-review-run",
        report_job_id=job.id,
        report_sha256=report_evaluation.report_sha256(_single_report()),
        status="completed",
        progress_message="평가 완료",
        protocol_version=report_evaluation.EXPECTED_PROTOCOL_VERSION,
        overall_status="FAIL",
        automatic_status="PASS",
        summary_json=json.dumps({"manual_review_status": "PENDING"}),
        question_results_json="[]",
        output_dir=str(tmp_path / "evaluation-output"),
        created_at=_now_iso(),
        completed_at=_now_iso(),
    )
    db = test_session()
    try:
        db.add_all([job, run])
        db.commit()
    finally:
        db.close()

    observed: dict[str, object] = {}
    artifact = tmp_path / "report.pdf"
    artifact.write_bytes(b"%PDF-test")

    def fake_write_manual_review_input(_run, review, reviewer):
        observed["review"] = review
        observed["reviewer"] = reviewer
        return tmp_path / "manual-review.json"

    monkeypatch.setattr(
        admin_ops,
        "write_manual_review_input",
        fake_write_manual_review_input,
    )
    monkeypatch.setattr(
        admin_ops,
        "evaluation_artifact_path",
        lambda _run, _artifact_name: artifact,
    )

    artifact_response = client.get(
        "/api/admin/report-evaluations/runs/manual-review-run/artifacts/report.pdf"
    )
    assert artifact_response.status_code == 200
    assert artifact_response.headers["content-type"] == "application/pdf"
    assert artifact_response.content == b"%PDF-test"

    response = client.post(
        "/api/admin/report-evaluations/runs/manual-review-run/manual-review",
        json={
            "q050": {
                "decision": "PASS",
                "actual": "C3와 C5의 단위를 확인했습니다.",
                "rationale_ko": "두 차트 모두 단위가 직접 표시됩니다.",
            },
            "q051": {
                "decision": "FAIL",
                "actual": "표 헤더가 다음 페이지로 분리됐습니다.",
                "rationale_ko": "페이지 경계에서 표 의미가 끊깁니다.",
            },
        },
    )
    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    assert observed["review"]["Q050"]["decision"] == "PASS"
    assert observed["review"]["Q051"]["decision"] == "FAIL"
    assert observed["reviewer"] == "development-local-admin"

    db = test_session()
    try:
        persisted = db.get(ReportEvaluationRun, "manual-review-run")
        assert persisted is not None
        assert persisted.status == "queued"
        assert persisted.completed_at is None
    finally:
        db.close()


def test_completed_manual_review_cannot_be_submitted_again(
    evaluation_client,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    client, test_session = evaluation_client
    job = _job(job_id="completed-manual-review-report-job")
    run = ReportEvaluationRun(
        id="completed-manual-review-run",
        report_job_id=job.id,
        report_sha256=report_evaluation.report_sha256(_single_report()),
        status="completed",
        progress_message="평가 완료",
        protocol_version=report_evaluation.EXPECTED_PROTOCOL_VERSION,
        overall_status="PASS",
        automatic_status="PASS",
        summary_json=json.dumps({"manual_review_status": "COMPLETE"}),
        question_results_json="[]",
        output_dir=str(tmp_path / "completed-evaluation-output"),
        created_at=_now_iso(),
        completed_at=_now_iso(),
    )
    db = test_session()
    try:
        db.add_all([job, run])
        db.commit()
    finally:
        db.close()

    write_called = False

    def fail_if_manual_input_is_written(*_args, **_kwargs):
        nonlocal write_called
        write_called = True

    monkeypatch.setattr(
        admin_ops,
        "write_manual_review_input",
        fail_if_manual_input_is_written,
    )

    response = client.post(
        "/api/admin/report-evaluations/runs/"
        "completed-manual-review-run/manual-review",
        json={
            "q050": {
                "decision": "PASS",
                "actual": "C3와 C5의 단위를 확인했습니다.",
                "rationale_ko": "두 차트 모두 단위가 직접 표시됩니다.",
            },
            "q051": {
                "decision": "PASS",
                "actual": "PDF 페이지 배치를 확인했습니다.",
                "rationale_ko": "페이지 경계에서 내용이 끊기지 않습니다.",
            },
        },
    )

    assert response.status_code == 409
    assert "이미 완료" in response.json()["detail"]
    assert write_called is False

    db = test_session()
    try:
        persisted = db.get(
            ReportEvaluationRun,
            "completed-manual-review-run",
        )
        assert persisted is not None
        assert persisted.status == "completed"
        assert json.loads(persisted.summary_json)["manual_review_status"] == "COMPLETE"
    finally:
        db.close()


def test_evaluation_service_parses_outputs_and_keeps_manual_review_pending(
    evaluation_db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    report = _single_report()
    job = _job(job_id="service-report-job-001", report=report)
    run = ReportEvaluationRun(
        id="service-evaluation-run-001",
        report_job_id=job.id,
        report_sha256=report_evaluation.report_sha256(report),
        status="queued",
        progress_message="평가 대기 중",
        created_at=_now_iso(),
    )
    job_id = job.id
    run_id = run.id
    db = evaluation_db()
    try:
        db.add_all([job, run])
        db.commit()
    finally:
        db.close()

    artifact_dir = tmp_path / "published-artifacts"
    chart_dir = artifact_dir / "charts"
    chart_dir.mkdir(parents=True)
    (chart_dir / "C3.png").write_bytes(b"chart-c3")
    (chart_dir / "C5.png").write_bytes(b"chart-c5")
    (artifact_dir / "report.pdf").write_bytes(b"pdf")

    monkeypatch.setattr(report_evaluation, "SessionLocal", evaluation_db)
    monkeypatch.setattr(
        report_evaluation,
        "ADMIN_EVALUATION_ROOT",
        tmp_path / "evaluation-output",
    )
    monkeypatch.setattr(
        report_evaluation,
        "publish_report_artifacts",
        lambda _report_id, _report: {"report_dir": str(artifact_dir)},
    )

    observed: dict[str, object] = {}

    def fake_subprocess_run(command: list[str], **_kwargs):
        output_dir = Path(command[command.index("--output-dir") + 1])
        manual_review_path = Path(command[command.index("--manual-review") + 1])
        manual_review = json.loads(manual_review_path.read_text(encoding="utf-8"))
        observed["command"] = command
        observed["manual_review"] = manual_review

        questions = []
        for index in range(1, 57):
            question_id = f"Q{index:03d}"
            method = "sql_exact"
            decision = "PASS"
            actual: object = "100"
            expected: object = "100"
            if question_id == "Q050":
                method = "independent_visual_review"
                decision = "FAIL"
                actual = "PENDING"
                expected = "manual review"
            elif question_id == "Q051":
                method = "independent_pdf_page_review"
                decision = "FAIL"
                actual = {
                    "automated_layout": {"passed": True},
                    "manual_pdf_review": {"passed": False},
                }
                expected = "manual review"
            questions.append(
                {
                    "id": question_id,
                    "gate": True,
                    "decision": decision,
                    "method": method,
                    "actual": actual,
                    "expected": expected,
                }
            )
        summary = {
            "protocol_version": report_evaluation.EXPECTED_PROTOCOL_VERSION,
            "overall_status": "FAIL",
            "question_count": 56,
            "pass_count": 54,
            "fail_count": 2,
            "hard_fail_count": 2,
            "failed_question_ids": ["Q050", "Q051"],
            "hard_failed_question_ids": ["Q050", "Q051"],
        }
        (output_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False),
            encoding="utf-8",
        )
        (output_dir / "question_results.jsonl").write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in questions)
            + "\n",
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(report_evaluation.subprocess, "run", fake_subprocess_run)

    report_evaluation.execute_report_evaluation(run_id)

    db = evaluation_db()
    try:
        persisted = db.get(ReportEvaluationRun, run_id)
        assert persisted is not None
        assert persisted.status == "completed"
        assert persisted.protocol_version == report_evaluation.EXPECTED_PROTOCOL_VERSION
        assert persisted.overall_status == "FAIL"
        assert persisted.automatic_status == "PASS"
        assert persisted.error_message is None
        assert persisted.output_dir == str(
            tmp_path / "evaluation-output" / job_id / run_id
        )

        summary = json.loads(persisted.summary_json)
        assert summary["automatic_status"] == "PASS"
        assert summary["automatic_failed_question_ids"] == []
        assert summary["manual_review_status"] == "PENDING"
        assert summary["manual_review_question_ids"] == ["Q050", "Q051"]
        assert summary["report_job_id"] == job_id
        assert summary["report_sha256"] == persisted.report_sha256
        assert summary["context"]["area_code"] == "TEST-AREA-001"
        assert summary["context"]["budget_manwon"] == 5000

        questions = json.loads(persisted.question_results_json)
        assert len(questions) == 56
        assert [row["id"] for row in questions[-7:-4]] == [
            "Q050",
            "Q051",
            "Q052",
        ]
    finally:
        db.close()

    manual_review = observed["manual_review"]
    assert isinstance(manual_review, dict)
    assert manual_review["questions"]["Q050"]["decision"] == "PENDING"
    assert manual_review["questions"]["Q051"]["decision"] == "PENDING"
    assert manual_review["reviewed_at"] is None
    assert manual_review["artifact_sha256"]["C3.png"]
    assert manual_review["artifact_sha256"]["C5.png"]
    assert manual_review["artifact_sha256"]["report.pdf"]
    assert "--no-fail-exit" in observed["command"]


def test_evaluator_contract_rejects_partial_output_and_counts_q051_layout():
    summary = {
        "protocol_version": report_evaluation.EXPECTED_PROTOCOL_VERSION,
        "overall_status": "PASS",
        "question_count": 1,
        "pass_count": 1,
        "fail_count": 0,
        "hard_fail_count": 0,
        "failed_question_ids": [],
        "hard_failed_question_ids": [],
    }
    with pytest.raises(RuntimeError, match="56문항"):
        report_evaluation._validate_evaluation_output(
            summary,
            [
                {
                    "id": "Q001",
                    "gate": True,
                    "decision": "PASS",
                    "method": "sql_exact",
                }
            ],
        )

    q051 = {
        "id": "Q051",
        "gate": True,
        "decision": "FAIL",
        "method": "independent_pdf_page_review",
        "actual": {
            "automated_layout": {"passed": False},
            "manual_pdf_review": {"passed": False},
        },
    }
    assert report_evaluation._automatic_failure_ids([q051]) == ["Q051"]


def test_manual_review_input_is_bound_to_current_artifact_hashes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    evaluation_root = tmp_path / "evaluations"
    reports_root = tmp_path / "reports"
    run = ReportEvaluationRun(
        id="hash-bound-review-run",
        report_job_id="hash-bound-report-job",
        report_sha256="a" * 64,
        status="completed",
        progress_message="평가 완료",
        output_dir=str(
            evaluation_root / "hash-bound-report-job" / "hash-bound-review-run"
        ),
        created_at=_now_iso(),
    )
    artifact_dir = reports_root / "admin-eval-hash-bound-review-run"
    (artifact_dir / "charts").mkdir(parents=True)
    (artifact_dir / "charts" / "C3.png").write_bytes(b"c3-current")
    (artifact_dir / "charts" / "C5.png").write_bytes(b"c5-current")
    (artifact_dir / "report.pdf").write_bytes(b"pdf-current")
    Path(run.output_dir).mkdir(parents=True)
    monkeypatch.setattr(
        report_evaluation,
        "ADMIN_EVALUATION_ROOT",
        evaluation_root,
    )
    monkeypatch.setattr(report_evaluation, "REPORTS_OUT", reports_root)

    path = report_evaluation.write_manual_review_input(
        run,
        {
            "Q050": {
                "decision": "PASS",
                "actual": "차트 단위를 확인했습니다.",
                "rationale_ko": "필요한 단위가 모두 표시됩니다.",
            },
            "Q051": {
                "decision": "PASS",
                "actual": "PDF 페이지 배치를 확인했습니다.",
                "rationale_ko": "제목과 표가 같은 흐름으로 읽힙니다.",
            },
        },
        "reviewer@example.com",
    )
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["questions"]["Q050"]["decision"] == "PASS"
    assert document["questions"]["Q051"]["decision"] == "PASS"
    assert document["reviewer"] == "reviewer@example.com"
    assert document["reviewed_at"]
    assert all(document["artifact_sha256"].values())
    assert document["artifact_sha256"]["C3.png"] == report_evaluation._file_sha256(
        artifact_dir / "charts" / "C3.png"
    )
    history_files = list(
        (Path(run.output_dir) / "manual_review_history").glob("*.json")
    )
    assert len(history_files) == 1
    assert json.loads(history_files[0].read_text(encoding="utf-8")) == document


def test_manual_review_executor_reuses_run_and_marks_review_complete(
    evaluation_db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    evaluation_root = tmp_path / "evaluations"
    reports_root = tmp_path / "reports"
    report = _single_report()
    job = _job(job_id="manual-executor-job", report=report)
    run = ReportEvaluationRun(
        id="manual-executor-run",
        report_job_id=job.id,
        report_sha256=report_evaluation.report_sha256(report),
        status="queued",
        progress_message="수동 검수 반영 대기",
        output_dir=str(
            evaluation_root / "manual-executor-job" / "manual-executor-run"
        ),
        created_at=_now_iso(),
    )
    output_dir = Path(run.output_dir)
    output_dir.mkdir(parents=True)
    (output_dir / "report_response.generated.json").write_text(
        json.dumps({"report": report}, ensure_ascii=False),
        encoding="utf-8",
    )
    manual_path = output_dir / "manual_visual_review.admin-input.json"
    manual_path.write_text(
        json.dumps(
            {
                "reviewer": "reviewer@example.com",
                "reviewed_at": "2026-07-24T12:00:00+00:00",
                "questions": {
                    "Q050": {"decision": "PASS"},
                    "Q051": {"decision": "PASS"},
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    db = evaluation_db()
    try:
        db.add_all([job, run])
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(report_evaluation, "SessionLocal", evaluation_db)
    monkeypatch.setattr(
        report_evaluation,
        "ADMIN_EVALUATION_ROOT",
        evaluation_root,
    )
    monkeypatch.setattr(report_evaluation, "REPORTS_OUT", reports_root)
    monkeypatch.setattr(
        report_evaluation,
        "_run_evaluator",
        lambda **_kwargs: (
            {
                "protocol_version": report_evaluation.EXPECTED_PROTOCOL_VERSION,
                "overall_status": "PASS",
            },
            [
                {
                    "id": "Q050",
                    "gate": True,
                    "decision": "PASS",
                    "method": "independent_visual_review",
                },
                {
                    "id": "Q051",
                    "gate": True,
                    "decision": "PASS",
                    "method": "independent_pdf_page_review",
                    "actual": {"automated_layout": {"passed": True}},
                },
            ],
        ),
    )

    report_evaluation.execute_manual_report_evaluation("manual-executor-run")

    db = evaluation_db()
    try:
        persisted = db.get(ReportEvaluationRun, "manual-executor-run")
        assert persisted is not None
        assert persisted.status == "completed"
        assert persisted.overall_status == "PASS"
        assert persisted.automatic_status == "PASS"
        summary = json.loads(persisted.summary_json)
        assert summary["manual_review_status"] == "COMPLETE"
        assert summary["manual_review_question_ids"] == ["Q050", "Q051"]
        assert summary["manual_review"] == {
            "reviewer": "reviewer@example.com",
            "reviewed_at": "2026-07-24T12:00:00+00:00",
        }
        assert len(summary["manual_review_history"]) == 1
        assert summary["manual_review_history"][0]["questions"]["Q050"][
            "decision"
        ] == "PASS"
    finally:
        db.close()


def test_expired_report_cleanup_preserves_evaluated_source(evaluation_db):
    evaluated_job = _job(job_id="evaluated-old-job")
    unevaluated_job = _job(job_id="unevaluated-old-job")
    evaluated_job_id = evaluated_job.id
    unevaluated_job_id = unevaluated_job.id
    evaluated_job.created_at = "2000-01-01T00:00:00"
    unevaluated_job.created_at = "2000-01-01T00:00:00"
    evaluation_run = ReportEvaluationRun(
        id="completed-evaluation-run",
        report_job_id=evaluated_job.id,
        report_sha256=report_evaluation.report_sha256(_single_report()),
        status="completed",
        progress_message="평가 완료",
        created_at=_now_iso(),
        completed_at=_now_iso(),
    )

    db = evaluation_db()
    try:
        db.add_all([evaluated_job, unevaluated_job, evaluation_run])
        db.commit()

        assert reports_router._purge_expired_report_jobs(db) == 1
        assert db.get(ReportGenerationJob, evaluated_job_id) is not None
        assert db.get(ReportGenerationJob, unevaluated_job_id) is None
        assert db.get(ReportEvaluationRun, evaluation_run.id) is not None
    finally:
        db.close()
