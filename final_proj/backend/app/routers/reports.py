from datetime import datetime, timedelta
from io import BytesIO
import json
import re
import secrets
from typing import Callable, Literal, TypeVar
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette.responses import FileResponse, StreamingResponse

from app.database import SessionLocal, get_db
from app.dependencies import get_commercial_area_service, get_current_user, get_optional_user
from app.models.commercial_area import (
    PDFExportHistory,
    ReportEvaluationRun,
    ReportGenerationJob,
    SavedReport,
    TokenUsageLog,
    User,
)
from app.repositories.commercial_area import CommercialAreaRepository
from app.schemas.commercial_area import (
    AIAnalysisResponse,
    AIComparisonResponse,
    ComparisonRequest,
    SavedReportCreate,
    SavedReportResponse,
    PDFExportHistoryResponse,
)
from app.services.commercial_area import CommercialAreaService
from app.services.comparison_report import ComparisonReportService
from app.services.report_publisher import (
    REPORTS_OUT,
    normalize_public_report_data,
    publish_report_artifacts,
    report_artifacts_are_current,
)
from app.services.single_report import SingleReportService
from app.ai.recursive_layer import track_token_usage, calculate_token_cost, get_openai_model
from app.services.interpretive_report import ReportGenerationError
from app.services.llm_runtime_settings import get_report_reasoning_effort



class SingleReportRequest(BaseModel):
    area_code: str
    business_type: str | None = None
    budget: int | None = None


class ReportExportRequest(BaseModel):
    report_data: dict
    filename: str | None = None


ReportJobStatus = Literal["queued", "running", "completed", "failed"]


class ReportJobAccepted(BaseModel):
    job_id: str
    report_type: Literal["single", "comparison"]
    status: ReportJobStatus


class ReportJobResponse(ReportJobAccepted):
    progress_message: str
    result: dict | None = None
    error_message: str | None = None
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None


router = APIRouter(prefix="/reports", tags=["reports"])
ReportResponse = TypeVar("ReportResponse", AIAnalysisResponse, AIComparisonResponse)

_SECRET_VALUE = re.compile(
    r"(?i)(api[_-]?key|token|password|secret|authorization)(\s*[:=]\s*)([^\s,;]+)"
)
_BEARER_VALUE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+\-/]+=*")
_OPENAI_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")
_WINDOWS_PATH = re.compile(r"(?i)\b[A-Z]:\\(?:[^\\\s]+\\)*[^\\\s,;]*")
_REPORT_JOB_SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_REPORT_JOB_CLIENT_ID = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
_REPORT_JOB_RETENTION_DAYS = 7


def _sanitize_generation_error(value: object) -> str:
    text = str(value or "")
    text = _SECRET_VALUE.sub(lambda match: f"{match.group(1)}{match.group(2)}[redacted]", text)
    text = _BEARER_VALUE.sub("Bearer [redacted]", text)
    text = _OPENAI_KEY.sub("[redacted]", text)
    return _WINDOWS_PATH.sub("[path]", text)[:500]


def _write_usage_log(
    db: Session,
    *,
    current_user: User | None,
    usage,
    feature_name: str,
    reasoning_effort: str,
    status: str,
    generation_mode: str | None = None,
    quality_status: str | None = None,
    original_validation_issues: list[str] | None = None,
    error: ReportGenerationError | None = None,
) -> TokenUsageLog:
    model_name = usage.model_name or get_openai_model()
    log_entry = TokenUsageLog(
        user_id=current_user.id if current_user else None,
        model_name=model_name,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens,
        estimated_cost=calculate_token_cost(
            model_name,
            usage.prompt_tokens,
            usage.completion_tokens,
        ),
        feature_name=feature_name,
        status=status,
        reasoning_effort=reasoning_effort,
        generation_mode=generation_mode,
        quality_status=quality_status,
        original_validation_issues_json=(
            json.dumps(original_validation_issues, ensure_ascii=False)
            if original_validation_issues
            else None
        ),
        error_type=error.error_type if error else None,
        error_message=_sanitize_generation_error(error.provider_message) if error else None,
        created_at=datetime.now().isoformat(),
    )
    db.add(log_entry)
    db.commit()
    db.refresh(log_entry)
    return log_entry


def _run_report_generation(
    generate: Callable[[], ReportResponse],
    *,
    db: Session,
    current_user: User | None,
    feature_name: str,
) -> ReportResponse:
    reasoning_effort = get_report_reasoning_effort()
    with track_token_usage() as usage:
        try:
            result = generate()
        except ReportGenerationError as error:
            attempt = _write_usage_log(
                db,
                current_user=current_user,
                usage=usage,
                feature_name=feature_name,
                reasoning_effort=reasoning_effort,
                status="failed",
                error=error,
            )
            raise HTTPException(
                status_code=502,
                detail={
                    "message": "AI 리포트 생성 연결에 실패했습니다. 관리자 발생 로그를 확인해 주세요.",
                    "attempt_id": attempt.id,
                },
            ) from error

    generation_mode = getattr(result, "generation_mode", None)
    quality_status = getattr(result, "quality_status", None)
    final_validation_issues = list(getattr(result, "validation_issues", []) or [])
    original_validation_issues = list(
        getattr(result, "original_validation_issues", []) or []
    )

    if feature_name == "single_report" and (
        quality_status != "pass" or final_validation_issues
    ):
        error = ReportGenerationError(
            feature_name,
            RuntimeError("report validation did not reach a clean final state"),
        )
        attempt = _write_usage_log(
            db,
            current_user=current_user,
            usage=usage,
            feature_name=feature_name,
            reasoning_effort=reasoning_effort,
            status="failed",
            generation_mode=generation_mode,
            quality_status=quality_status,
            original_validation_issues=original_validation_issues,
            error=error,
        )
        raise HTTPException(
            status_code=502,
            detail={
                "message": "리포트 검증을 완료하지 못했습니다. 관리자 품질 로그를 확인해 주세요.",
                "attempt_id": attempt.id,
            },
        )

    if feature_name != "single_report" and not bool(getattr(result, "ai_generated", False)):
        error = ReportGenerationError(
            feature_name,
            RuntimeError("report response did not contain an AI-generated result"),
        )
        attempt = _write_usage_log(
            db,
            current_user=current_user,
            usage=usage,
            feature_name=feature_name,
            reasoning_effort=reasoning_effort,
            status="failed",
            error=error,
        )
        raise HTTPException(
            status_code=502,
            detail={
                "message": "AI 리포트 생성 결과를 확인하지 못했습니다. 관리자 발생 로그를 확인해 주세요.",
                "attempt_id": attempt.id,
            },
        )

    if usage.total_tokens > 0 or generation_mode == "deterministic":
        _write_usage_log(
            db,
            current_user=current_user,
            usage=usage,
            feature_name=feature_name,
            reasoning_effort=reasoning_effort,
            status="degraded" if generation_mode == "deterministic" else "success",
            generation_mode=generation_mode,
            quality_status=quality_status,
            original_validation_issues=original_validation_issues,
        )
    return result


def _safe_filename(value: str | None, fallback: str) -> str:
    name = (value or fallback).strip() or fallback
    return re.sub(r'[\\/:*?"<>|]+', "_", name)[:120]


def _repair_mojibake(text: str) -> str:
    if not any(marker in text for marker in ("ì", "í", "ë", "ê", "Â")):
        return text
    repaired = text
    for _ in range(2):
        try:
            candidate = repaired.encode("latin1").decode("utf-8")
        except UnicodeError:
            break
        if candidate == repaired:
            break
        repaired = candidate
    return repaired


def _clean_export_text(value: str) -> str:
    text = _repair_mojibake(str(value))
    replacements = {
        "창업 성공확률": "입지 등급",
        "성공확률": "입지 등급",
        "매출 보장": "매출 지표",
        "매출을 보장": "매출 지표로 참고",
        "성장률 보장": "성장 지표 참고",
        "성장 보장": "성장 지표 참고",
        "수익성 보장": "수익성 검토 참고",
        "월세가 확정": "월세는 별도 계약 확인 필요",
        "권리금이 확정": "권리금은 별도 계약 확인 필요",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def _markdown_from_report(report_data: dict) -> str:
    markdown = report_data.get("markdown_body")
    if isinstance(markdown, str) and markdown.strip():
        return _clean_export_text(markdown.strip()) + "\n"

    title = report_data.get("narrative_title") or report_data.get("summary") or "AI 상세 리포트"
    lines = [f"# {_clean_export_text(title)}", ""]
    for key, heading in [
        ("executive_interpretation", "핵심 해석"),
        ("score_interpretation", "정량평가 해석"),
        ("summary", "요약"),
    ]:
        value = report_data.get(key)
        if value:
            lines.extend([f"## {heading}", _clean_export_text(str(value)), ""])

    axes = report_data.get("axis_interpretations") or []
    if axes:
        lines.extend(["## 판단 근거", "| 판단 영역 | 해석 수준 | 의미 | 현장 확인 |", "|---|---|---|---|"])
        for item in axes:
            lines.append(
                "| {axis} | {level} | {meaning} | {action} |".format(
                    axis=_clean_export_text(item.get("axis", "")),
                    level=_clean_export_text(item.get("interpretation_level", "해석 대상")),
                    meaning=_clean_export_text(item.get("meaning", "")),
                    action=_clean_export_text(item.get("action", "")),
                )
            )
        lines.append("")

    citations = report_data.get("source_citations") or []
    if citations:
        lines.extend(["## 데이터 출처 및 산정 기준", "| 원천 기관 | 데이터셋 | 기준 단위 | 사용 목적 |", "|---|---|---|---|"])
        for item in citations:
            if item.get("theme") == "해석 기준":
                continue
            lines.append(
                "| {provider} | {dataset} | {grain} | {used_for} |".format(
                    provider=_clean_export_text(item.get("provider") or "공공 데이터 원천"),
                    dataset=_clean_export_text(item.get("dataset_name") or item.get("title", "")),
                    grain=_clean_export_text(item.get("granularity") or "-"),
                    used_for=_clean_export_text(item.get("used_for", "")),
                )
            )
        lines.append("")

    for key, heading in [
        ("evidence_basis", "산정 기준"),
        ("methodology_notes", "산정 기준"),
        ("risk_factors", "주요 리스크"),
        ("risk_summary", "주요 리스크"),
        ("action_plan", "현장 검증 순서"),
        ("onsite_checklist", "현장 검증 순서"),
        ("limitations", "해석 범위"),
    ]:
        items = report_data.get(key) or []
        if items:
            lines.append(f"## {heading}")
            lines.extend(f"- {_clean_export_text(str(item))}" for item in items)
            lines.append("")

    return "\n".join(lines).strip() + "\n"


def _pdf_bytes_from_markdown(markdown: str) -> bytes:
    from pathlib import Path

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    font_name = "Helvetica"
    for path in [
        Path("C:/Windows/Fonts/malgun.ttf"),
        Path("C:/Windows/Fonts/NanumGothic.ttf"),
        Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
    ]:
        if path.exists():
            font_name = "KoreanBody"
            pdfmetrics.registerFont(TTFont(font_name, str(path)))
            break

    styles = getSampleStyleSheet()
    base = ParagraphStyle(
        "KoreanBase",
        parent=styles["BodyText"],
        fontName=font_name,
        fontSize=9.5,
        leading=14,
        spaceAfter=5,
    )
    heading = ParagraphStyle(
        "KoreanHeading",
        parent=base,
        fontSize=15,
        leading=20,
        spaceBefore=9,
        spaceAfter=8,
    )
    title = ParagraphStyle(
        "KoreanTitle",
        parent=base,
        fontSize=19,
        leading=25,
        spaceAfter=12,
    )

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
    )
    story = []
    table_rows: list[list[Paragraph]] = []

    def flush_table() -> None:
        nonlocal table_rows
        if not table_rows:
            return
        table = Table(table_rows, repeatRows=1 if len(table_rows) > 1 else 0)
        table.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
                ]
            )
        )
        story.append(table)
        table_rows = []

    for raw in markdown.splitlines():
        line = raw.strip()
        if not line:
            flush_table()
            story.append(Spacer(1, 4))
            continue
        if line.startswith("|") and line.endswith("|"):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if all(set(cell) <= {"-", ":"} for cell in cells):
                continue
            table_rows.append([Paragraph(cell, base) for cell in cells])
            continue

        flush_table()
        if line.startswith("# "):
            story.append(Paragraph(line[2:], title))
        elif line.startswith("## "):
            story.append(Paragraph(line[3:], heading))
        elif line.startswith("- "):
            story.append(Paragraph(f"• {line[2:]}", base))
        else:
            story.append(Paragraph(line, base))

    flush_table()
    doc.build(story)
    return buffer.getvalue()


def _report_job_error_message(error: Exception) -> str:
    if isinstance(error, HTTPException):
        detail = error.detail
        if isinstance(detail, dict):
            options = detail.get("options")
            if isinstance(options, list) and options:
                names = []
                for item in options:
                    if isinstance(item, dict):
                        name = item.get("industry_name")
                        if name:
                            names.append(str(name))
                    elif item:
                        names.append(str(item))
                if names:
                    return f"업종을 확정하지 못했습니다. 후보: {', '.join(names)}"
            message = detail.get("message")
            if message:
                return _sanitize_generation_error(message)
        elif detail:
            return _sanitize_generation_error(detail)
    message = _sanitize_generation_error(error)
    return message or "리포트 생성 중 오류가 발생했습니다."


def _execute_report_job(job_id: str) -> None:
    db = SessionLocal()
    try:
        job = db.get(ReportGenerationJob, job_id)
        if not job or job.status != "queued":
            return

        job.status = "running"
        job.progress_message = "AI 리포트를 생성하고 있습니다."
        job.started_at = datetime.now().isoformat()
        db.commit()

        request_payload = json.loads(job.request_json)
        repository = CommercialAreaRepository(db)
        commercial_area_service = CommercialAreaService(repository)
        current_user = db.get(User, job.user_id) if job.user_id is not None else None

        if job.report_type == "single":
            request = SingleReportRequest.model_validate(request_payload)
            result = _run_report_generation(
                lambda: _generate_single(request, commercial_area_service),
                db=db,
                current_user=current_user,
                feature_name="single_report",
            )
        elif job.report_type == "comparison":
            request = ComparisonRequest.model_validate(request_payload)
            result = _run_report_generation(
                lambda: _generate_comparison(request, commercial_area_service),
                db=db,
                current_user=current_user,
                feature_name="comparison_report",
            )
        else:
            raise ValueError(f"Unsupported report type: {job.report_type}")

        job = db.get(ReportGenerationJob, job_id)
        if not job:
            return
        job.status = "completed"
        job.progress_message = "AI 리포트 생성이 완료되었습니다."
        job.result_json = json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
        job.error_message = None
        job.completed_at = datetime.now().isoformat()
        db.commit()
    except Exception as error:
        db.rollback()
        job = db.get(ReportGenerationJob, job_id)
        if job:
            job.status = "failed"
            job.progress_message = "AI 리포트 생성에 실패했습니다."
            job.error_message = _report_job_error_message(error)
            job.completed_at = datetime.now().isoformat()
            db.commit()
    finally:
        db.close()


def _purge_expired_report_jobs(db: Session) -> int:
    cutoff = (datetime.now() - timedelta(days=_REPORT_JOB_RETENTION_DAYS)).isoformat()
    deleted = int(
        db.query(ReportGenerationJob)
        .filter(
            ReportGenerationJob.created_at < cutoff,
            ~ReportGenerationJob.id.in_(
                db.query(ReportEvaluationRun.report_job_id)
            ),
        )
        .delete(synchronize_session=False)
        or 0
    )
    if deleted:
        db.commit()
    return deleted


def _normalize_report_job_session(
    current_user: User | None,
    client_session_id: str | None,
) -> str | None:
    session_id = (client_session_id or "").strip()
    if session_id and not _REPORT_JOB_SESSION_ID.fullmatch(session_id):
        raise HTTPException(status_code=422, detail="invalid report job session")
    if current_user is None and not session_id:
        raise HTTPException(status_code=422, detail="anonymous report job session required")
    return session_id or None


def _resolve_report_job_id(client_job_id: str | None) -> str:
    job_id = (client_job_id or "").strip()
    if not job_id:
        return str(uuid4())
    if not _REPORT_JOB_CLIENT_ID.fullmatch(job_id):
        raise HTTPException(status_code=422, detail="invalid report job id")
    return job_id


def _create_report_job(
    *,
    report_type: Literal["single", "comparison"],
    request_payload: BaseModel,
    background_tasks: BackgroundTasks,
    db: Session,
    current_user: User | None,
    client_session_id: str | None,
    client_job_id: str | None,
) -> ReportJobAccepted:
    _purge_expired_report_jobs(db)
    normalized_session = _normalize_report_job_session(current_user, client_session_id)
    job_id = _resolve_report_job_id(client_job_id)
    request_json = request_payload.model_dump_json()

    existing = db.get(ReportGenerationJob, job_id)
    if existing:
        same_owner = (
            existing.user_id == current_user.id
            if current_user is not None
            else (
                existing.user_id is None
                and existing.client_session_id is not None
                and normalized_session is not None
                and secrets.compare_digest(existing.client_session_id, normalized_session)
            )
        )
        if not same_owner:
            raise HTTPException(status_code=409, detail="report job id conflict")
        if existing.report_type != report_type or existing.request_json != request_json:
            raise HTTPException(status_code=409, detail="report job request conflict")
        return ReportJobAccepted(
            job_id=existing.id,
            report_type=existing.report_type,
            status=existing.status,
        )

    job = ReportGenerationJob(
        id=job_id,
        user_id=current_user.id if current_user else None,
        client_session_id=normalized_session,
        report_type=report_type,
        request_json=request_json,
        status="queued",
        progress_message="리포트 생성 대기 중",
        created_at=datetime.now().isoformat(),
    )
    db.add(job)
    db.commit()
    background_tasks.add_task(_execute_report_job, job.id)
    return ReportJobAccepted(
        job_id=job.id,
        report_type=report_type,
        status="queued",
    )


def _get_accessible_report_job(
    job_id: str,
    *,
    db: Session,
    current_user: User | None,
    client_session_id: str | None,
) -> ReportGenerationJob:
    _purge_expired_report_jobs(db)
    job = db.get(ReportGenerationJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="report job not found")

    if job.user_id is not None:
        if current_user is None or current_user.id != job.user_id:
            raise HTTPException(status_code=404, detail="report job not found")
    elif job.client_session_id:
        supplied_session = (client_session_id or "").strip()
        if not supplied_session or not secrets.compare_digest(job.client_session_id, supplied_session):
            raise HTTPException(status_code=404, detail="report job not found")
    else:
        raise HTTPException(status_code=404, detail="report job not found")
    return job


@router.post(
    "/jobs/single",
    response_model=ReportJobAccepted,
    status_code=202,
)
def queue_single_report(
    request: SingleReportRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_optional_user),
    client_session_id: str | None = Header(default=None, alias="X-LocalFit-Session"),
    client_job_id: str | None = Header(default=None, alias="X-LocalFit-Report-Job"),
):
    return _create_report_job(
        report_type="single",
        request_payload=request,
        background_tasks=background_tasks,
        db=db,
        current_user=current_user,
        client_session_id=client_session_id,
        client_job_id=client_job_id,
    )


@router.post(
    "/jobs/comparison",
    response_model=ReportJobAccepted,
    status_code=202,
)
def queue_comparison_report(
    request: ComparisonRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_optional_user),
    client_session_id: str | None = Header(default=None, alias="X-LocalFit-Session"),
    client_job_id: str | None = Header(default=None, alias="X-LocalFit-Report-Job"),
):
    return _create_report_job(
        report_type="comparison",
        request_payload=request,
        background_tasks=background_tasks,
        db=db,
        current_user=current_user,
        client_session_id=client_session_id,
        client_job_id=client_job_id,
    )


@router.get("/jobs/{job_id}", response_model=ReportJobResponse)
def get_report_job(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_optional_user),
    client_session_id: str | None = Header(default=None, alias="X-LocalFit-Session"),
):
    job = _get_accessible_report_job(
        job_id,
        db=db,
        current_user=current_user,
        client_session_id=client_session_id,
    )
    result = None
    if job.status == "completed" and job.result_json:
        try:
            result = json.loads(job.result_json)
        except json.JSONDecodeError:
            job.status = "failed"
            job.progress_message = "저장된 리포트 결과를 읽지 못했습니다."
            job.error_message = "리포트 결과 데이터가 손상되었습니다."
            job.completed_at = datetime.now().isoformat()
            db.commit()
    elif job.status == "completed":
        job.status = "failed"
        job.progress_message = "저장된 리포트 결과를 찾지 못했습니다."
        job.error_message = "리포트 결과 데이터가 없습니다."
        job.completed_at = datetime.now().isoformat()
        db.commit()

    return ReportJobResponse(
        job_id=job.id,
        report_type=job.report_type,
        status=job.status,
        progress_message=job.progress_message,
        result=result,
        error_message=job.error_message,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
    )


@router.get("/", response_model=list[SavedReportResponse])
def get_reports(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    reports = db.query(SavedReport).filter(SavedReport.user_id == current_user.id).order_by(SavedReport.created_at.desc()).all()
    results = []
    for report in reports:
        try:
            data = json.loads(report.report_data)
        except Exception:
            data = {}
        results.append(
            SavedReportResponse(
                id=report.id,
                report_data=normalize_public_report_data(data),
                created_at=report.created_at,
            )
        )
    return results


@router.get("/{report_id}", response_model=SavedReportResponse)
def get_report(report_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    report = db.query(SavedReport).filter(SavedReport.id == report_id, SavedReport.user_id == current_user.id).first()
    if not report:
        raise HTTPException(status_code=404, detail="report not found")
    try:
        data = json.loads(report.report_data)
    except Exception:
        data = {}
    return SavedReportResponse(
        id=report.id,
        report_data=normalize_public_report_data(data),
        created_at=report.created_at,
    )


@router.get("/{report_id}/download")
def download_saved_report(
    report_id: int,
    format: str = "pdf",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    report = db.query(SavedReport).filter(SavedReport.id == report_id, SavedReport.user_id == current_user.id).first()
    if not report:
        raise HTTPException(status_code=404, detail="report not found")
    try:
        data = json.loads(report.report_data)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="saved report is not valid JSON") from exc
    artifacts = publish_report_artifacts(report.id, data)
    fmt = format.lower()
    if fmt == "pdf":
        path = artifacts["pdf_path"]
        media_type = "application/pdf"
        suffix = "pdf"
    else:
        raise HTTPException(status_code=400, detail="only pdf download is supported")
    filename = _safe_filename(data.get("narrative_title"), f"report_{report_id}") + f".{suffix}"
    return FileResponse(path, media_type=media_type, filename=filename)


@router.get("/{report_id}/charts/{chart_id}")
def get_saved_report_chart(
    report_id: int,
    chart_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not re.fullmatch(r"C[1-5]", chart_id):
        raise HTTPException(status_code=400, detail="chart_id must be C1~C5")
    report = db.query(SavedReport).filter(SavedReport.id == report_id, SavedReport.user_id == current_user.id).first()
    if not report:
        raise HTTPException(status_code=404, detail="report not found")
    chart_path = REPORTS_OUT / str(report_id) / "charts" / f"{chart_id}.png"
    if not report_artifacts_are_current(report.id):
        try:
            data = json.loads(report.report_data)
        except Exception as exc:
            raise HTTPException(status_code=500, detail="saved report is not valid JSON") from exc
        publish_report_artifacts(report.id, data)
    if not chart_path.exists():
        raise HTTPException(status_code=404, detail="chart not available for this report")
    return FileResponse(str(chart_path), media_type="image/png")


def _generate_single(
    request: SingleReportRequest,
    commercial_area_service: CommercialAreaService,
) -> AIAnalysisResponse:
    single_report_service = SingleReportService(repository=commercial_area_service.repository)
    if request.business_type and not commercial_area_service.resolve_industry(request.business_type):
        raise HTTPException(
            status_code=400,
            detail={
                "message": "industry unresolved",
                "options": commercial_area_service.industry_options(request.business_type),
            },
        )
    result = single_report_service.generate(request.area_code, business_type=request.business_type, budget=request.budget)
    if not result:
        raise HTTPException(status_code=404, detail="Could not generate single report")
    return result


def _generate_comparison(
    request: ComparisonRequest,
    commercial_area_service: CommercialAreaService,
) -> AIComparisonResponse:
    comparison_report_service = ComparisonReportService(repository=commercial_area_service.repository)
    result = comparison_report_service.generate(request.area_codes)
    if not result:
        raise HTTPException(status_code=404, detail="Could not generate comparison report")
    return result


@router.post("/single/generate", response_model=AIAnalysisResponse)
def generate_single_report(
    request: SingleReportRequest,
    commercial_area_service: CommercialAreaService = Depends(get_commercial_area_service),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_optional_user),
):
    return _run_report_generation(
        lambda: _generate_single(request, commercial_area_service),
        db=db,
        current_user=current_user,
        feature_name="single_report",
    )


@router.post("/comparison/generate", response_model=AIComparisonResponse)
def generate_comparison_report(
    request: ComparisonRequest,
    commercial_area_service: CommercialAreaService = Depends(get_commercial_area_service),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_optional_user),
):
    return _run_report_generation(
        lambda: _generate_comparison(request, commercial_area_service),
        db=db,
        current_user=current_user,
        feature_name="comparison_report",
    )


@router.post("/generate_single", response_model=AIAnalysisResponse)
def generate_single_report_legacy(
    request: SingleReportRequest,
    commercial_area_service: CommercialAreaService = Depends(get_commercial_area_service),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_optional_user),
):
    return _run_report_generation(
        lambda: _generate_single(request, commercial_area_service),
        db=db,
        current_user=current_user,
        feature_name="single_report",
    )


@router.post("/generate", response_model=AIComparisonResponse)
def generate_report_legacy(
    request: ComparisonRequest,
    commercial_area_service: CommercialAreaService = Depends(get_commercial_area_service),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_optional_user),
):
    return _run_report_generation(
        lambda: _generate_comparison(request, commercial_area_service),
        db=db,
        current_user=current_user,
        feature_name="comparison_report",
    )


@router.post("/export/pdf")
def export_report_pdf(
    request: ReportExportRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_optional_user),
):
    filename = _safe_filename(request.filename or request.report_data.get("narrative_title"), "ai_report") + ".pdf"
    public_report = normalize_public_report_data(request.report_data)
    artifacts = publish_report_artifacts(
        f"export_{datetime.now().strftime('%Y%m%d%H%M%S%f')}",
        public_report,
    )

    if current_user:
        report_id = request.report_data.get("id") or request.report_data.get("report_id")
        history = PDFExportHistory(
            user_id=current_user.id,
            report_id=report_id,
            filename=filename,
            exported_at=datetime.now().isoformat()
        )
        db.add(history)
        db.commit()

    return FileResponse(artifacts["pdf_path"], media_type="application/pdf", filename=filename)


@router.get("/export/history", response_model=list[PDFExportHistoryResponse])
def get_pdf_export_history(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return db.query(PDFExportHistory).filter(PDFExportHistory.user_id == current_user.id).order_by(PDFExportHistory.id.desc()).all()



@router.post("/save", response_model=SavedReportResponse)
def save_report(
    request: SavedReportCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        json_data = json.dumps(request.report_data, ensure_ascii=False)
        new_report = SavedReport(
            report_data=json_data,
            user_id=current_user.id,
            created_at=datetime.now().isoformat(),
        )
        db.add(new_report)
        db.commit()
        db.refresh(new_report)
        artifacts = publish_report_artifacts(new_report.id, request.report_data)
        data = dict(request.report_data)
        data["artifact_paths"] = artifacts
        new_report.report_data = json.dumps(data, ensure_ascii=False)
        db.commit()
        return SavedReportResponse(
            id=new_report.id,
            report_data=normalize_public_report_data(data),
            created_at=new_report.created_at,
        )
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/{report_id}", status_code=204)
def delete_report(
    report_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    report = db.query(SavedReport).filter(SavedReport.id == report_id, SavedReport.user_id == current_user.id).first()
    if not report:
        raise HTTPException(status_code=404, detail="report not found or not owned by current user")
    try:
        db.delete(report)
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="delete failed") from exc
