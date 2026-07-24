from __future__ import annotations

import csv
import json
import os
from collections import defaultdict
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Optional, List, Any, Literal
from fastapi import APIRouter, HTTPException, status, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.models.commercial_area import TokenUsageLog, ExternalAPILog
from app.ai.recursive_layer import get_openai_model
from app.services.llm_runtime_settings import (
    ReportReasoningSettings,
    read_report_reasoning_settings,
    set_report_reasoning_effort,
)
from app.services.news_evidence import NaverNewsConnectionError, check_naver_news_connection
from app.services.admin_pipeline import (
    JOB_DEFINITIONS,
    JobConflictError,
    admin_dashboard,
    cancel_job,
    get_job,
    get_job_status,
    list_jobs,
    public_job_definitions,
    start_job,
    DATA_ROOT,
    INGEST_MANIFEST_PATH,
    source_statuses,
)


public_router = APIRouter(prefix="/admin", tags=["client-events"])
router = APIRouter(prefix="/admin", tags=["admin"])


class JobRunRequest(BaseModel):
    confirmed: bool = False


class ExternalAPILogCreate(BaseModel):
    api_name: Literal["Kakao Map SDK"]
    endpoint: Literal["https://dapi.kakao.com/v2/maps/sdk.js"]
    status_code: Literal[200, 500]
    response_time_ms: Optional[int] = Field(default=None, ge=0, le=300_000)
    call_type: Literal["GET"] = "GET"


class OpenAIReportSettingsUpdate(BaseModel):
    reasoning_effort: Literal["none", "low", "medium", "high", "xhigh"]


def _openai_report_config(
    settings: ReportReasoningSettings | None = None,
) -> dict[str, object]:
    active_settings = settings or read_report_reasoning_settings()
    return {
        "configured_model": get_openai_model(),
        **active_settings.as_dict(),
    }


@public_router.post("/external-api-log", status_code=status.HTTP_201_CREATED)
def log_external_api_call_route(
    request: ExternalAPILogCreate,
    db: Session = Depends(get_db)
):
    """Persist a credential-free browser integration event.

    The local administrator API is intentionally available without login, so
    this browser event endpoint follows the same access policy as the other
    ``/api/admin`` routes.
    """
    cutoff = (datetime.now() - timedelta(hours=1)).isoformat()
    duplicate = (
        db.query(ExternalAPILog.id)
        .filter(
            ExternalAPILog.api_name == request.api_name,
            ExternalAPILog.endpoint == request.endpoint,
            ExternalAPILog.status_code == request.status_code,
            ExternalAPILog.call_type == request.call_type,
            ExternalAPILog.created_at >= cutoff,
        )
        .first()
    )
    if duplicate:
        return {"status": "deduplicated"}

    log_entry = ExternalAPILog(
        api_name=request.api_name,
        endpoint=request.endpoint,
        status_code=request.status_code,
        response_time_ms=request.response_time_ms,
        call_type=request.call_type,
        created_at=datetime.now().isoformat()
    )
    db.add(log_entry)
    db.commit()
    return {"status": "logged"}


@lru_cache(maxsize=8)
def _pipeline_api_calls_snapshot(
    manifest_path_value: str,
    manifest_mtime_ns: int,
    manifest_size: int,
    failed_path_value: str,
    failed_mtime_ns: int,
    failed_size: int,
) -> tuple[dict[str, Any], ...]:
    del manifest_mtime_ns, manifest_size, failed_mtime_ns, failed_size
    manifest_path = Path(manifest_path_value)
    failed_path = Path(failed_path_value)
    
    calls = []
    if manifest_path.exists():
        try:
            with open(manifest_path, mode="r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for csv_line, row in enumerate(reader, start=2):
                    api_name = row.get("provider") or "Unknown"
                    if row.get("source_id") == "naver_api_hub_news":
                        api_name = "NAVER API HUB"
                    elif "rss" in str(row.get("source_id")).lower():
                        api_name = "RSS Feed"
                    
                    endpoint = row.get("request_url_redacted") or row.get("raw_path") or "Pipeline Ingest"
                    created_at = row.get("collected_at") or row.get("snapshot_date") or ""
                    
                    status_val = row.get("http_status")
                    try:
                        status_code = int(status_val) if status_val and status_val.strip().isdigit() else 200
                    except ValueError:
                        status_code = 200
                    
                    calls.append({
                        # A run can contain many requests for the same source.
                        # The CSV line gives each row a stable, unique React key.
                        "id": f"p_success_{csv_line}",
                        "api_name": api_name,
                        "endpoint": endpoint,
                        "status_code": status_code,
                        "response_time_ms": None,
                        "call_type": "GET",
                        "created_at": created_at
                    })
        except Exception as e:
            print(f"Error reading manifest: {e}")
            
    if failed_path.exists():
        try:
            with open(failed_path, mode="r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for csv_line, row in enumerate(reader, start=2):
                    api_name = row.get("provider") or "Unknown"
                    if row.get("source_id") == "naver_api_hub_news":
                        api_name = "NAVER API HUB"
                    elif "rss" in str(row.get("source_id")).lower():
                        api_name = "RSS Feed"
                        
                    calls.append({
                        "id": f"p_fail_{csv_line}",
                        "api_name": api_name,
                        "endpoint": row.get("request_url_redacted") or "Pipeline Ingest Attempt",
                        "status_code": 500,
                        "response_time_ms": None,
                        "call_type": "GET",
                        "created_at": row.get("attempted_at") or ""
                    })
        except Exception as e:
            print(f"Error reading failed downloads: {e}")
            
    return tuple(calls)


def get_pipeline_api_calls() -> list[dict[str, Any]]:
    manifest_path = INGEST_MANIFEST_PATH
    failed_path = DATA_ROOT / "_raw_ingest" / "failed_downloads.csv"

    def snapshot_key(path: Path) -> tuple[str, int, int]:
        if not path.exists():
            return str(path.resolve()), 0, 0
        stat = path.stat()
        return str(path.resolve()), stat.st_mtime_ns, stat.st_size

    manifest_key = snapshot_key(manifest_path)
    failed_key = snapshot_key(failed_path)
    return [
        dict(call)
        for call in _pipeline_api_calls_snapshot(*manifest_key, *failed_key)
    ]


def get_external_api_calls(db: Session) -> list[dict[str, Any]]:
    """Combine persisted runtime events with redacted pipeline request history."""
    db_external_logs = db.query(ExternalAPILog).order_by(ExternalAPILog.id.desc()).all()
    runtime_calls = [
        {
            "id": f"db_{log.id}",
            "api_name": log.api_name,
            "endpoint": log.endpoint,
            "status_code": log.status_code,
            "response_time_ms": log.response_time_ms,
            "call_type": log.call_type,
            "created_at": log.created_at,
            "origin": (
                "client_observation"
                if log.api_name == "Kakao Map SDK"
                and log.endpoint == "https://dapi.kakao.com/v2/maps/sdk.js"
                else "runtime"
            ),
        }
        for log in db_external_logs
    ]
    calls = runtime_calls + [
        {**call, "origin": "pipeline"}
        for call in get_pipeline_api_calls()
    ]
    calls.sort(key=lambda call: str(call.get("created_at") or ""), reverse=True)
    return calls


def _operational_external_calls(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Exclude credential-free client observations from operational SLI math."""
    return [call for call in calls if call.get("origin") != "client_observation"]


def _combined_health(health_values: list[str]) -> str:
    if "error" in health_values:
        return "error"
    if any(value in {"warning", "missing", "unknown"} for value in health_values):
        return "warning"
    return "healthy"


def _latest_timestamp(current: str | None, candidate: str | None) -> str | None:
    if not candidate:
        return current
    if not current or candidate > current:
        return candidate
    return current


def build_provider_integrations(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Summarize registry-backed data providers without exposing credentials."""
    grouped: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "source_count": 0,
            "credential_required": 0,
            "credential_configured": 0,
            "credential_missing": 0,
            "health_values": [],
            "failure_rows": 0,
            "last_collected_at": None,
            "refresh_available_count": 0,
        }
    )

    for source in sources:
        provider = (source.get("provider") or "미분류 제공기관").strip()
        group = grouped[provider]
        credential_status = source.get("credential_status") or "unknown"
        group["source_count"] += 1
        group["health_values"].append(source.get("health") or "unknown")
        group["failure_rows"] += int(source.get("failure_rows") or 0)
        group["last_collected_at"] = _latest_timestamp(
            group["last_collected_at"], source.get("last_collected_at")
        )
        group["refresh_available_count"] += int(bool(source.get("refresh_available")))
        if credential_status in {"configured", "missing"}:
            group["credential_required"] += 1
        if credential_status == "configured":
            group["credential_configured"] += 1
        if credential_status == "missing":
            group["credential_missing"] += 1

    providers = []
    for provider, group in grouped.items():
        if group["credential_missing"]:
            credential_status = "missing"
        elif group["credential_required"]:
            credential_status = "configured"
        else:
            credential_status = "not_required"
        providers.append(
            {
                "provider_id": f"provider:{provider}",
                "provider": provider,
                "source_count": group["source_count"],
                "credential_status": credential_status,
                "credential_configured": group["credential_configured"],
                "credential_required": group["credential_required"],
                "health": _combined_health(group["health_values"]),
                "failure_rows": group["failure_rows"],
                "last_collected_at": group["last_collected_at"],
                "refresh_available_count": group["refresh_available_count"],
            }
        )
    return sorted(providers, key=lambda provider: provider["provider"].casefold())


@router.post("/integrations/naver-news/check")
def check_naver_news_integration():
    try:
        return check_naver_news_connection()
    except NaverNewsConnectionError as error:
        raise HTTPException(status_code=error.http_status, detail=str(error)) from error


@router.patch("/integrations/openai/report-settings")
def update_openai_report_settings(request: OpenAIReportSettingsUpdate):
    settings = set_report_reasoning_effort(request.reasoning_effort)
    return _openai_report_config(settings)


@router.get("/integrations")
def read_external_integrations(db: Session = Depends(get_db)):
    """Return a credential-safe, operations-focused view of external integrations."""
    all_calls = get_external_api_calls(db)
    sources = source_statuses()
    providers = build_provider_integrations(sources)

    report_ai_config = _openai_report_config()
    configured_model = str(report_ai_config["configured_model"])
    openai_key_configured = bool(os.getenv("OPENAI_API_KEY", "").strip())
    model_rows = (
        db.query(
            TokenUsageLog.model_name,
            func.count(TokenUsageLog.id).label("call_count"),
            func.max(TokenUsageLog.created_at).label("last_used_at"),
        )
        .group_by(TokenUsageLog.model_name)
        .order_by(func.max(TokenUsageLog.created_at).desc())
        .all()
    )
    observed_models = [
        {
            "model_name": row.model_name or "unknown",
            "call_count": int(row.call_count or 0),
            "last_used_at": row.last_used_at,
        }
        for row in model_rows
    ]
    openai_call_count = sum(model["call_count"] for model in observed_models)
    openai_last_used_at = next(
        (model["last_used_at"] for model in observed_models if model["last_used_at"]), None
    )

    kakao_calls = [
        call
        for call in all_calls
        if "kakao" in str(call.get("api_name") or "").lower()
        or "kakao" in str(call.get("endpoint") or "").lower()
    ]
    # Browser telemetry is intentionally credential-free, so a reported failure
    # is useful in the event log but must never be allowed to poison the
    # administrator health summary.  Only an observed successful SDK load is
    # treated as connection evidence; otherwise the state remains unknown.
    successful_kakao_calls = [
        call for call in kakao_calls if int(call.get("status_code") or 0) < 400
    ]
    latest_kakao_success = successful_kakao_calls[0] if successful_kakao_calls else None
    latest_kakao_observation = kakao_calls[0] if kakao_calls else None

    naver_calls = [
        call
        for call in all_calls
        if "naver" in str(call.get("api_name") or "").lower()
        or "naver" in str(call.get("endpoint") or "").lower()
    ]
    latest_naver_call = naver_calls[0] if naver_calls else None
    latest_naver_status = int(latest_naver_call["status_code"]) if latest_naver_call else None
    naver_configured = any(
        source.get("source_id") == "naver_api_hub_news"
        and source.get("credential_status") == "configured"
        for source in sources
    )

    runtime_integrations = [
        {
            "integration_id": "openai",
            "label": "OpenAI",
            "status": "healthy" if openai_key_configured and openai_call_count else (
                "warning" if openai_key_configured else "missing"
            ),
            "configured": openai_key_configured,
            "configured_model": configured_model,
            "report_reasoning_effort": report_ai_config["reasoning_effort"],
            "observed_models": observed_models,
            "call_count": openai_call_count,
            "last_activity_at": openai_last_used_at,
            "status_note": "키 값은 표시하지 않으며 최근 응답 모델은 provider snapshot으로 표시됩니다.",
        },
        {
            "integration_id": "kakao-map-sdk",
            "label": "Kakao Map SDK",
            # A credential-free browser event is an observation, not trusted
            # proof of configuration or provider health.
            "status": "unknown",
            "configured": False,
            "configured_model": None,
            "observed_models": [],
            "call_count": 0,
            "last_activity_at": None,
            "client_observation": {
                "status": "success_observed" if latest_kakao_success else (
                    "failure_observed" if latest_kakao_observation else "none"
                ),
                "success_count": len(successful_kakao_calls),
                "failure_count": len(kakao_calls) - len(successful_kakao_calls),
                "last_success_at": latest_kakao_success.get("created_at") if latest_kakao_success else None,
                "last_event_at": latest_kakao_observation.get("created_at") if latest_kakao_observation else None,
            },
            "status_note": "브라우저 SDK 이벤트는 별도 관측값입니다. 공개 이벤트만으로 설정 완료나 공급자 정상 상태를 판정하지 않습니다.",
        },
        {
            "integration_id": "naver-news-search",
            "label": "NAVER 뉴스 검색",
            "status": "missing" if not naver_configured else (
                "healthy" if latest_naver_status and latest_naver_status < 400 else (
                    "error" if latest_naver_status and latest_naver_status >= 400 else "warning"
                )
            ),
            "configured": naver_configured,
            "configured_model": None,
            "observed_models": [],
            "call_count": len(naver_calls),
            "last_activity_at": latest_naver_call.get("created_at") if latest_naver_call else None,
            "status_note": "상세 리포트 생성 시 실시간으로 조회하며, 관리자에서는 연결 상태만 확인합니다.",
        },
    ]

    operational_calls = _operational_external_calls(all_calls)
    total_calls = len(operational_calls)
    success_calls = sum(1 for call in operational_calls if int(call["status_code"]) < 400)
    return {
        "generated_at": datetime.now().isoformat(),
        "summary": {
            "provider_count": len(providers),
            "source_count": sum(provider["source_count"] for provider in providers),
            "credential_configured": sum(provider["credential_configured"] for provider in providers),
            "credential_required": sum(provider["credential_required"] for provider in providers),
            "warning_provider_count": sum(provider["health"] == "warning" for provider in providers),
            "error_provider_count": sum(provider["health"] == "error" for provider in providers),
            "total_call_count": total_calls,
            "success_rate": round((success_calls / total_calls * 100.0) if total_calls else 100.0, 2),
        },
        "runtime_integrations": runtime_integrations,
        "report_ai_config": report_ai_config,
        "providers": providers,
        "recent_calls": all_calls[:100],
    }


@router.get("/dashboard")
def read_admin_dashboard():
    return admin_dashboard()


@router.get("/job-definitions")
def read_job_definitions():
    return public_job_definitions()


@router.get("/jobs")
def read_jobs():
    return list_jobs()


@router.get("/jobs/{job_id}/status")
def read_job_status(job_id: str):
    job = get_job_status(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/jobs/{job_id}")
def read_job(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/jobs/{job_key}")
def run_job(job_key: str, request: JobRunRequest):
    try:
        return start_job(job_key, confirmed=request.confirmed)
    except JobConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail=f"Job definition {job_key} not found") from error
    except PermissionError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except FileNotFoundError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/jobs/{job_id}/cancel")
def cancel_running_job(job_id: str):
    try:
        return cancel_job(job_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=f"Job definition {job_id} not found") from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/token-usage")
def get_token_usage_stats(
    db: Session = Depends(get_db)
):
    total_calls = db.query(func.count(TokenUsageLog.id)).scalar() or 0
    successful_calls = db.query(func.count(TokenUsageLog.id)).filter(
        TokenUsageLog.status == "success"
    ).scalar() or 0
    degraded_calls = db.query(func.count(TokenUsageLog.id)).filter(
        TokenUsageLog.status == "degraded"
    ).scalar() or 0
    failed_calls = db.query(func.count(TokenUsageLog.id)).filter(
        TokenUsageLog.status == "failed"
    ).scalar() or 0
    total_prompt_tokens = db.query(func.sum(TokenUsageLog.prompt_tokens)).scalar() or 0
    total_completion_tokens = db.query(func.sum(TokenUsageLog.completion_tokens)).scalar() or 0
    total_tokens = db.query(func.sum(TokenUsageLog.total_tokens)).scalar() or 0
    total_cost = db.query(func.sum(TokenUsageLog.estimated_cost)).scalar() or 0.0
    
    model_breakdown = {}
    model_rows = db.query(
        TokenUsageLog.model_name,
        func.count(TokenUsageLog.id).label("calls"),
        func.sum(TokenUsageLog.total_tokens).label("tokens"),
        func.sum(TokenUsageLog.estimated_cost).label("cost")
    ).group_by(TokenUsageLog.model_name).all()
    
    for row in model_rows:
        model_breakdown[row[0] or "unknown"] = {
            "calls": row[1],
            "total_tokens": row[2],
            "estimated_cost": round(float(row[3] or 0.0), 6)
        }
        
    feature_breakdown = {}
    feature_rows = db.query(
        TokenUsageLog.feature_name,
        func.count(TokenUsageLog.id).label("calls"),
        func.sum(TokenUsageLog.total_tokens).label("tokens"),
        func.sum(TokenUsageLog.estimated_cost).label("cost")
    ).group_by(TokenUsageLog.feature_name).all()
    
    feature_name_mapping = {
        "single_report": "상세 리포트 생성",
        "comparison_report": "상권 비교 분석",
        "chatbot_analysis": "챗봇 상권 분석",
        "chatbot_chat": "챗봇 일반 대화"
    }
    
    for row in feature_rows:
        raw_name = row[0] or "unknown"
        clean_name = feature_name_mapping.get(raw_name, raw_name)
        feature_breakdown[clean_name] = {
            "calls": row[1],
            "total_tokens": row[2],
            "estimated_cost": round(float(row[3] or 0.0), 6)
        }
        
    raw_logs_db = db.query(TokenUsageLog).order_by(TokenUsageLog.id.desc()).limit(100).all()
    raw_logs = [
        {
            "id": log.id,
            "user_id": log.user_id,
            "model_name": log.model_name,
            "prompt_tokens": log.prompt_tokens,
            "completion_tokens": log.completion_tokens,
            "total_tokens": log.total_tokens,
            "estimated_cost": round(float(log.estimated_cost or 0.0), 6),
            "feature_name": feature_name_mapping.get(log.feature_name, log.feature_name),
            "status": log.status or "success",
            "reasoning_effort": log.reasoning_effort,
            "generation_mode": log.generation_mode,
            "quality_status": log.quality_status,
            "original_validation_issues": (
                json.loads(log.original_validation_issues_json)
                if log.original_validation_issues_json
                else []
            ),
            "error_type": log.error_type,
            "error_message": log.error_message,
            "created_at": log.created_at
        }
        for log in raw_logs_db
    ]
    
    chatbot_cost = db.query(func.sum(TokenUsageLog.estimated_cost)).filter(
        TokenUsageLog.feature_name.in_(["chatbot_analysis", "chatbot_chat"])
    ).scalar() or 0.0
    report_cost = db.query(func.sum(TokenUsageLog.estimated_cost)).filter(
        TokenUsageLog.feature_name.in_(["single_report", "comparison_report"])
    ).scalar() or 0.0
    
    all_external_calls = get_external_api_calls(db)
    operational_external_calls = _operational_external_calls(all_external_calls)
    
    total_ext_calls = len(operational_external_calls)
    kakao_calls = sum(1 for c in operational_external_calls if "kakao" in c["api_name"].lower() or "kakao" in c["endpoint"].lower())
    naver_calls = sum(1 for c in operational_external_calls if "naver" in c["api_name"].lower() or "naver" in c["endpoint"].lower())
    open_data_calls = total_ext_calls - kakao_calls - naver_calls
    
    success_ext_calls = sum(1 for c in operational_external_calls if c["status_code"] < 400)
    success_rate = (success_ext_calls / total_ext_calls * 100.0) if total_ext_calls > 0 else 100.0
    error_ext_count = total_ext_calls - success_ext_calls
    
    return {
        "report_ai_config": _openai_report_config(),
        "summary": {
            "total_calls": total_calls,
            "successful_calls": successful_calls,
            "degraded_calls": degraded_calls,
            "failed_calls": failed_calls,
            "total_prompt_tokens": total_prompt_tokens,
            "total_completion_tokens": total_completion_tokens,
            "total_tokens": total_tokens,
            "total_cost": round(float(total_cost or 0.0), 6),
            "chatbot_cost": round(float(chatbot_cost), 6),
            "report_cost": round(float(report_cost), 6)
        },
        "model_breakdown": model_breakdown,
        "feature_breakdown": feature_breakdown,
        "logs": raw_logs,
        "external_api": {
            "summary": {
                "total_calls": total_ext_calls,
                "kakao_calls": kakao_calls,
                "naver_calls": naver_calls,
                "open_data_calls": open_data_calls,
                "success_rate": round(success_rate, 2),
                "error_count": error_ext_count
            },
            "logs": all_external_calls[:100]
        }
    }
