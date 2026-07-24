from __future__ import annotations

import json
import os
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Literal
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import distinct, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload
from starlette.responses import FileResponse

from app.database import get_db
from app.dependencies import get_optional_user
from app.models.commercial_area import (
    CommercialArea,
    ExternalAPILog,
    ReportEvaluationRun,
    ReportGenerationJob,
    TokenUsageLog,
    User,
)
from app.models.community import Comment, UserEvent
from app.routers.admin import get_external_api_calls
from app.schemas.community import CommentStatusUpdate
from app.services.admin_pipeline import admin_dashboard, list_jobs
from app.services.report_evaluation import (
    evaluation_artifact_path,
    execute_manual_report_evaluation,
    execute_report_evaluation,
    report_evaluation_context,
    report_sha256,
    write_manual_review_input,
)


router = APIRouter(prefix="/admin", tags=["admin-ops"])
_HEALTH_ORDER = {
    "healthy": 0,
    "advisory": 1,
    "unknown": 2,
    "missing": 2,
    "warning": 3,
    "error": 4,
}
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|token|password|secret|authorization)\s*[:=]\s*[^\s,;]+"
)
_WINDOWS_PATH = re.compile(r"(?i)\b[A-Z]:\\(?:[^\\\s]+\\)*[^\\\s,;]*")
_POSIX_PRIVATE_PATH = re.compile(r"/(?:home|Users|mnt|var|tmp)/[^\s,;]+")
_REPORT_EVALUATION_STALE_AFTER = timedelta(minutes=10)


class ManualReviewDecision(BaseModel):
    decision: Literal["PASS", "FAIL"]
    actual: str = Field(min_length=3, max_length=2000)
    rationale_ko: str = Field(min_length=3, max_length=2000)


class ReportManualReviewRequest(BaseModel):
    q050: ManualReviewDecision
    q051: ManualReviewDecision


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _cutoff(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")


def _sanitize_text(value: object) -> str:
    text = str(value or "")
    try:
        parsed = urlsplit(text)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            text = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    except ValueError:
        pass
    text = _SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=[redacted]", text)
    text = _WINDOWS_PATH.sub("[path]", text)
    return _POSIX_PRIVATE_PATH.sub("[path]", text)


def _json_object(value: str | None) -> dict:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_array(value: str | None) -> list:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _evaluation_run_payload(
    run: ReportEvaluationRun,
    *,
    include_questions: bool = False,
) -> dict:
    summary = _json_object(run.summary_json)
    payload = {
        "id": run.id,
        "report_job_id": run.report_job_id,
        "report_sha256": run.report_sha256,
        "status": run.status,
        "progress_message": run.progress_message,
        "protocol_version": run.protocol_version,
        "overall_status": run.overall_status,
        "automatic_status": run.automatic_status,
        "summary": summary or None,
        "error_message": _sanitize_text(run.error_message) if run.error_message else None,
        "created_at": run.created_at,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
    }
    if include_questions:
        payload["questions"] = _json_array(run.question_results_json)
    return payload


def _mark_stale_evaluation_failed(run: ReportEvaluationRun) -> bool:
    if run.status not in {"queued", "running"}:
        return False
    anchor_text = run.started_at or run.created_at
    try:
        anchor = datetime.fromisoformat(anchor_text)
        if anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        anchor = datetime.min.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - anchor <= _REPORT_EVALUATION_STALE_AFTER:
        return False
    run.status = "failed"
    run.progress_message = "중단된 평가 작업을 종료했습니다. 다시 실행할 수 있습니다."
    run.error_message = (
        "서버 재시작 또는 평가 제한시간 초과로 작업이 중단되었습니다."
    )
    run.completed_at = _now_iso()
    return True


def _quality_snapshot() -> tuple[dict, list[dict]]:
    dashboard = admin_dashboard()
    checks = []
    for layer in dashboard.get("layers", []):
        checks.append(
            {
                "key": layer.get("key"),
                "label": layer.get("label"),
                "status": layer.get("status", "unknown"),
                "count": layer.get("count"),
                "unit": layer.get("unit"),
                "updated_at": layer.get("updated_at"),
                "note": _sanitize_text(layer.get("note")),
            }
        )
    return dashboard, checks


@router.get("/access")
def read_admin_access(current_user: User | None = Depends(get_optional_user)):
    environment = os.getenv("LOCALFIT_ENV", "development").strip().casefold()
    local_open = environment not in {"prod", "production"}
    return {
        "environment": environment,
        "local_open": local_open,
        "authenticated": current_user is not None,
        "is_admin": bool(current_user and current_user.is_admin),
    }


@router.get("/comments")
def list_admin_comments(
    comment_status: Literal["all", "visible", "hidden", "deleted"] = Query(
        default="all", alias="status"
    ),
    area_code: str | None = Query(default=None, max_length=50),
    industry_code: str | None = Query(default=None, max_length=50),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
):
    query = db.query(Comment).options(selectinload(Comment.author))
    if comment_status != "all":
        query = query.filter(Comment.status == comment_status)
    if area_code:
        query = query.filter(Comment.area_code == area_code.strip())
    if industry_code is not None:
        normalized_industry = industry_code.strip()
        query = (
            query.filter(Comment.industry_code == normalized_industry)
            if normalized_industry
            else query.filter(Comment.industry_code.is_(None))
        )

    total = int(query.count())
    rows = (
        query.order_by(Comment.created_at.desc(), Comment.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    area_names = {
        code: name
        for code, name in db.query(CommercialArea.area_code, CommercialArea.area_name)
        .filter(CommercialArea.area_code.in_({row.area_code for row in rows}))
        .all()
    }
    reply_counts = dict(
        db.query(Comment.parent_id, func.count(Comment.id))
        .filter(Comment.parent_id.in_([row.id for row in rows]))
        .group_by(Comment.parent_id)
        .all()
    ) if rows else {}
    return {
        "items": [
            {
                "id": row.id,
                "area_code": row.area_code,
                "area_name": area_names.get(row.area_code),
                "industry_code": row.industry_code,
                "parent_id": row.parent_id,
                "body": row.body,
                "status": row.status,
                "author": (
                    {"id": row.author.id, "nickname": row.author.nickname}
                    if row.author is not None
                    else None
                ),
                "reply_count": int(reply_counts.get(row.id, 0)),
                "created_at": row.created_at,
                "updated_at": row.updated_at,
                "deleted_at": row.deleted_at,
            }
            for row in rows
        ],
        "page": page,
        "page_size": page_size,
        "total": total,
    }


@router.patch("/comments/{comment_id}/status")
def update_admin_comment_status(
    comment_id: int,
    request: CommentStatusUpdate,
    db: Session = Depends(get_db),
):
    comment = db.query(Comment).filter(Comment.id == comment_id).first()
    if comment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found")
    now = _now_iso()
    comment.status = request.status
    comment.updated_at = now
    comment.deleted_at = now if request.status == "deleted" else None
    db.commit()
    return {"id": comment.id, "status": comment.status, "updated_at": comment.updated_at}


def _event_query(db: Session, days: int):
    return db.query(UserEvent).filter(UserEvent.created_at >= _cutoff(days))


@router.get("/analytics/overview")
def analytics_overview(
    days: int = Query(default=30, ge=1, le=90),
    db: Session = Depends(get_db),
):
    base = _event_query(db, days)
    observed_counts = {
        event_type: int(count)
        for event_type, count in base.with_entities(
            UserEvent.event_type, func.count(UserEvent.id)
        ).group_by(UserEvent.event_type).all()
    }
    return {
        "period_days": days,
        "total_events": int(base.count()),
        "unique_sessions": int(
            base.with_entities(func.count(distinct(UserEvent.session_id))).scalar() or 0
        ),
        "event_counts": {
            event_type: observed_counts.get(event_type, 0)
            for event_type in (
                "page_view",
                "search_submitted",
                "area_selected",
                "report_requested",
                "report_completed",
                "report_failed",
            )
        },
    }


@router.get("/analytics/funnel")
def analytics_funnel(
    days: int = Query(default=30, ge=1, le=90),
    db: Session = Depends(get_db),
):
    stage_keys = [
        "search_submitted",
        "area_selected",
        "report_requested",
        "report_completed",
    ]
    rows = (
        _event_query(db, days)
        .with_entities(UserEvent.event_type, func.count(distinct(UserEvent.session_id)))
        .filter(UserEvent.event_type.in_(stage_keys))
        .group_by(UserEvent.event_type)
        .all()
    )
    counts = {key: int(value) for key, value in rows}
    first = counts.get(stage_keys[0], 0)
    previous = None
    stages = []
    for key in stage_keys:
        count = counts.get(key, 0)
        stages.append(
            {
                "event_type": key,
                "unique_sessions": count,
                "conversion_from_previous": (
                    None if previous is None else round((count / previous * 100), 2) if previous else 0.0
                ),
                "conversion_from_search": (
                    round((count / first * 100), 2) if first else 0.0
                ),
            }
        )
        previous = count
    return {"period_days": days, "stages": stages}


@router.get("/analytics/popular-areas")
def analytics_popular_areas(
    days: int = Query(default=30, ge=1, le=90),
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    rows = (
        _event_query(db, days)
        .with_entities(
            UserEvent.area_code,
            func.count(UserEvent.id).label("event_count"),
            func.count(distinct(UserEvent.session_id)).label("unique_sessions"),
        )
        .filter(
            UserEvent.area_code.is_not(None),
            UserEvent.event_type.in_(("area_selected", "report_requested", "report_completed")),
        )
        .group_by(UserEvent.area_code)
        .order_by(func.count(UserEvent.id).desc(), UserEvent.area_code.asc())
        .limit(limit)
        .all()
    )
    names = {
        code: name
        for code, name in db.query(CommercialArea.area_code, CommercialArea.area_name)
        .filter(CommercialArea.area_code.in_([row.area_code for row in rows]))
        .all()
    } if rows else {}
    return {
        "period_days": days,
        "items": [
            {
                "area_code": row.area_code,
                "area_name": names.get(row.area_code),
                "event_count": int(row.event_count),
                "unique_sessions": int(row.unique_sessions),
            }
            for row in rows
        ],
    }


@router.get("/data-quality/summary")
def data_quality_summary():
    dashboard, checks = _quality_snapshot()
    counts = Counter(check["status"] for check in checks)
    overall_status = max(
        (check["status"] for check in checks),
        key=lambda value: _HEALTH_ORDER.get(value, 2),
        default="unknown",
    )
    return {
        "generated_at": dashboard.get("generated_at"),
        "overall_status": overall_status,
        "status_counts": {
            health: int(counts.get(health, 0))
            for health in ("healthy", "advisory", "warning", "error", "missing", "unknown")
        },
        "source_count": dashboard.get("summary", {}).get("source_count", 0),
        "healthy_source_count": dashboard.get("summary", {}).get("healthy_source_count", 0),
        "product_quarter": dashboard.get("summary", {}).get("product_quarter"),
    }


@router.get("/data-quality/checks")
def data_quality_checks():
    dashboard, checks = _quality_snapshot()
    return {"generated_at": dashboard.get("generated_at"), "items": checks}


@router.get("/report-evaluations/reports")
def list_report_evaluation_reports(
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    base_query = db.query(ReportGenerationJob).filter(
        ReportGenerationJob.status == "completed",
        ReportGenerationJob.result_json.is_not(None),
    )
    total = int(base_query.count())
    jobs = (
        base_query.order_by(
            ReportGenerationJob.completed_at.desc(),
            ReportGenerationJob.created_at.desc(),
        )
        .limit(limit)
        .all()
    )
    job_ids = [job.id for job in jobs]
    latest_runs: dict[str, ReportEvaluationRun] = {}
    if job_ids:
        stale_changed = False
        runs = (
            db.query(ReportEvaluationRun)
            .filter(ReportEvaluationRun.report_job_id.in_(job_ids))
            .order_by(
                ReportEvaluationRun.created_at.desc(),
                ReportEvaluationRun.id.desc(),
            )
            .all()
        )
        for run in runs:
            stale_changed = _mark_stale_evaluation_failed(run) or stale_changed
            latest_runs.setdefault(run.report_job_id, run)
        if stale_changed:
            db.commit()
    user_ids = {job.user_id for job in jobs if job.user_id is not None}
    user_emails = dict(
        db.query(User.id, User.email).filter(User.id.in_(user_ids)).all()
    ) if user_ids else {}

    items = []
    for job in jobs:
        report = _json_object(job.result_json)
        context = report_evaluation_context(job, report)
        items.append(
            {
                "job_id": job.id,
                "report_type": job.report_type,
                "title": (
                    report.get("narrative_title")
                    or report.get("summary")
                    or "AI 상세 리포트"
                ),
                "owner_email": user_emails.get(job.user_id),
                "created_at": job.created_at,
                "completed_at": job.completed_at,
                "context": context,
                "latest_evaluation": (
                    _evaluation_run_payload(latest_runs[job.id])
                    if job.id in latest_runs
                    else None
                ),
            }
        )
    return {
        "generated_at": _now_iso(),
        "items": items,
        "count": len(items),
        "total": total,
        "retention_days": 7,
        "evaluated_reports_retained": True,
        "evaluator": {
            "protocol": "기존 상세리포트 56문항 grounding 평가기",
            "external_llm_called": False,
            "manual_review_question_ids": ["Q050", "Q051"],
            "decision_rule": "gate=True 문항이 하나라도 FAIL이면 전체 FAIL",
        },
    }


@router.post(
    "/report-evaluations/reports/{job_id}/run",
    status_code=status.HTTP_202_ACCEPTED,
)
def start_report_evaluation(
    job_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    job = db.get(ReportGenerationJob, job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="리포트 생성 작업을 찾지 못했습니다.",
        )
    if job.status != "completed" or not job.result_json:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="완료된 리포트만 평가할 수 있습니다.",
        )
    report = _json_object(job.result_json)
    if not report:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="저장된 리포트 결과를 읽지 못했습니다.",
        )
    context = report_evaluation_context(job, report)
    if not context["evaluable"]:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=context["not_evaluable_reason"],
        )

    active_runs = (
        db.query(ReportEvaluationRun)
        .filter(
            ReportEvaluationRun.report_job_id == job.id,
            ReportEvaluationRun.status.in_(("queued", "running")),
        )
        .order_by(ReportEvaluationRun.created_at.desc())
        .all()
    )
    stale_changed = False
    active = None
    for candidate in active_runs:
        if _mark_stale_evaluation_failed(candidate):
            stale_changed = True
        elif active is None:
            active = candidate
    if stale_changed:
        db.commit()
    if active is not None:
        return _evaluation_run_payload(active)

    run = ReportEvaluationRun(
        id=str(uuid4()),
        report_job_id=job.id,
        report_sha256=report_sha256(report),
        status="queued",
        progress_message="상세 리포트 평가 대기 중",
        created_at=_now_iso(),
    )
    db.add(run)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        active = (
            db.query(ReportEvaluationRun)
            .filter(
                ReportEvaluationRun.report_job_id == job.id,
                ReportEvaluationRun.status.in_(("queued", "running")),
            )
            .order_by(ReportEvaluationRun.created_at.desc())
            .first()
        )
        if active is not None:
            return _evaluation_run_payload(active)
        raise
    background_tasks.add_task(execute_report_evaluation, run.id)
    return _evaluation_run_payload(run)


@router.get("/report-evaluations/runs/{run_id}")
def read_report_evaluation_run(
    run_id: str,
    db: Session = Depends(get_db),
):
    run = db.get(ReportEvaluationRun, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="리포트 평가 실행 기록을 찾지 못했습니다.",
        )
    if _mark_stale_evaluation_failed(run):
        db.commit()
    return _evaluation_run_payload(run, include_questions=True)


@router.get(
    "/report-evaluations/runs/{run_id}/artifacts/{artifact_name}",
    response_class=FileResponse,
)
def read_report_evaluation_artifact(
    run_id: str,
    artifact_name: str,
    db: Session = Depends(get_db),
):
    run = db.get(ReportEvaluationRun, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="리포트 평가 실행 기록을 찾지 못했습니다.",
        )
    try:
        path = evaluation_artifact_path(run, artifact_name)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error
    except FileNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="평가 산출물을 찾지 못했습니다.",
        ) from error
    media_types = {
        "report.pdf": "application/pdf",
        "C3.png": "image/png",
        "C5.png": "image/png",
        "evaluation-report.md": "text/markdown; charset=utf-8",
    }
    return FileResponse(path, media_type=media_types[artifact_name])


@router.post(
    "/report-evaluations/runs/{run_id}/manual-review",
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_report_evaluation_manual_review(
    run_id: str,
    request: ReportManualReviewRequest,
    background_tasks: BackgroundTasks,
    current_user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    run = db.get(ReportEvaluationRun, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="리포트 평가 실행 기록을 찾지 못했습니다.",
        )
    if run.status != "completed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="자동 평가가 완료된 실행만 수동 검수할 수 있습니다.",
        )
    if _json_object(run.summary_json).get("manual_review_status") == "COMPLETE":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "이 평가 실행의 수동 검수는 이미 완료됐습니다. "
                "새로 검수하려면 다시 평가를 실행하세요."
            ),
        )
    other_active = (
        db.query(ReportEvaluationRun)
        .filter(
            ReportEvaluationRun.report_job_id == run.report_job_id,
            ReportEvaluationRun.id != run.id,
            ReportEvaluationRun.status.in_(("queued", "running")),
        )
        .first()
    )
    if other_active is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="같은 리포트의 다른 평가가 진행 중입니다.",
        )
    review = {
        "Q050": request.q050.model_dump(),
        "Q051": request.q051.model_dump(),
    }
    reviewer = (
        current_user.email
        if current_user is not None
        else "development-local-admin"
    )
    queued_at = _now_iso()
    try:
        updated = (
            db.query(ReportEvaluationRun)
            .filter(
                ReportEvaluationRun.id == run.id,
                ReportEvaluationRun.status == "completed",
            )
            .update(
                {
                    ReportEvaluationRun.status: "queued",
                    ReportEvaluationRun.progress_message: "수동 시각검수 반영 대기 중",
                    ReportEvaluationRun.error_message: None,
                    ReportEvaluationRun.started_at: queued_at,
                    ReportEvaluationRun.completed_at: None,
                },
                synchronize_session=False,
            )
        )
        if updated != 1:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="수동 검수 제출 상태가 이미 변경됐습니다.",
            )
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="같은 리포트의 다른 평가가 먼저 시작됐습니다.",
        ) from error
    db.refresh(run)
    try:
        write_manual_review_input(run, review, reviewer)
    except (ValueError, FileNotFoundError) as error:
        run.status = "failed"
        run.progress_message = "수동 검수 산출물을 준비하지 못했습니다."
        run.error_message = "수동 검수할 PDF 또는 차트 산출물을 찾지 못했습니다."
        run.completed_at = _now_iso()
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=run.error_message,
        ) from error
    background_tasks.add_task(execute_manual_report_evaluation, run.id)
    return _evaluation_run_payload(run)


@router.get("/error-logs")
def read_sanitized_error_logs(
    limit: int = Query(default=100, ge=1, le=100),
    db: Session = Depends(get_db),
):
    items: list[dict] = []
    for call in get_external_api_calls(db):
        status_code = int(call.get("status_code") or 0)
        if status_code < 400:
            continue
        items.append(
            {
                "id": f"external:{call.get('id')}",
                "source": "external_api",
                "level": "error",
                "title": _sanitize_text(call.get("api_name") or "External API"),
                "message": _sanitize_text(call.get("endpoint") or "External request failed"),
                "status_code": status_code,
                "occurred_at": call.get("created_at"),
            }
        )
    for job in list_jobs(100):
        if job.get("status") != "failed":
            continue
        items.append(
            {
                "id": f"pipeline:{job.get('id')}",
                "source": "pipeline",
                "level": "error",
                "title": _sanitize_text(job.get("label") or job.get("job_key") or "Pipeline job"),
                "message": _sanitize_text(job.get("message") or "Pipeline job failed"),
                "status_code": None,
                "occurred_at": job.get("finished_at") or job.get("created_at"),
            }
        )
    report_failures = (
        db.query(TokenUsageLog)
        .filter(TokenUsageLog.status == "failed")
        .order_by(TokenUsageLog.id.desc())
        .limit(limit)
        .all()
    )
    for failure in report_failures:
        items.append(
            {
                "id": f"report_ai:{failure.id}",
                "source": "report_ai",
                "level": "error",
                "title": _sanitize_text(failure.feature_name or "AI report generation"),
                "message": _sanitize_text(
                    " · ".join(
                        part
                        for part in (failure.error_type, failure.error_message)
                        if part
                    )
                    or "AI report generation failed"
                ),
                "status_code": 502,
                "occurred_at": failure.created_at,
            }
        )
    items.sort(key=lambda item: str(item.get("occurred_at") or ""), reverse=True)
    items = items[:limit]
    return {"generated_at": _now_iso(), "items": items, "count": len(items), "limit": limit}
