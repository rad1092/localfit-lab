from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.settings import DATABASE_PATH
from app.database import SessionLocal
from app.models.commercial_area import ReportEvaluationRun, ReportGenerationJob
from app.services.report_publisher import REPORTS_OUT, publish_report_artifacts


BACKEND_ROOT = Path(__file__).resolve().parents[2]
FINAL_PROJ_ROOT = BACKEND_ROOT.parent
EVALUATOR_SCRIPT = BACKEND_ROOT / "scripts" / "evaluate_detailed_report_grounding.py"
ADMIN_EVALUATION_ROOT = (
    FINAL_PROJ_ROOT / "runtime" / "evaluations" / "admin-report-jobs"
)
MANUAL_REVIEW_METHODS = {
    "independent_visual_review",
    "independent_pdf_page_review",
}
EXPECTED_PROTOCOL_VERSION = "detailed-report-grounding.v1.6.0-batch-contract-repair"
EXPECTED_QUESTION_IDS = tuple(f"Q{index:03d}" for index in range(1, 57))
_SECRET_VALUE = re.compile(
    r"(?i)(api[_-]?key|token|password|secret|authorization)(\s*[:=]\s*)([^\s,;]+)"
)
_WINDOWS_PATH = re.compile(r"(?i)\b[A-Z]:\\(?:[^\\\s]+\\)*[^\\\s,;]*")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def report_sha256(report: dict[str, Any]) -> str:
    payload = _json_text(report).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _file_sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_question_results(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _safe_error(value: object) -> str:
    text = _SECRET_VALUE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}[redacted]",
        str(value or ""),
    )
    text = _WINDOWS_PATH.sub("[path]", text)
    return text.strip()[-500:] or "상세 리포트 평가 실행에 실패했습니다."


def report_evaluation_context(
    job: ReportGenerationJob,
    report: dict[str, Any],
) -> dict[str, Any]:
    try:
        request = json.loads(job.request_json)
    except (TypeError, json.JSONDecodeError):
        request = {}
    indicator_pack = report.get("indicator_pack")
    indicator_pack = indicator_pack if isinstance(indicator_pack, dict) else {}
    target = indicator_pack.get("target")
    target = target if isinstance(target, dict) else {}
    header = report.get("header_block")
    header = header if isinstance(header, dict) else {}
    supporting = indicator_pack.get("supporting_indicators")
    supporting = supporting if isinstance(supporting, dict) else {}
    budget_fit = supporting.get("budget_fit")
    budget_fit = budget_fit if isinstance(budget_fit, dict) else {}
    cost_block = indicator_pack.get("cost_block")
    cost_block = cost_block if isinstance(cost_block, dict) else {}
    cost_budget_fit = cost_block.get("budget_fit")
    cost_budget_fit = (
        cost_budget_fit if isinstance(cost_budget_fit, dict) else {}
    )

    area_code = request.get("area_code") or target.get("area_code")
    area_name = (
        report.get("area_name")
        or target.get("area_name")
        or header.get("area_name")
        or area_code
    )
    industry_code = report.get("industry_code") or target.get("industry_code")
    industry_name = (
        report.get("industry_name")
        or target.get("industry_name")
        or header.get("industry_name")
    )
    quarter = target.get("quarter")
    budget_value = (
        request.get("budget")
        if request.get("budget") not in (None, "")
        else budget_fit.get("budget_manwon")
        if budget_fit.get("budget_manwon") not in (None, "")
        else cost_budget_fit.get("budget_manwon")
    )
    try:
        budget_manwon = (
            int(str(budget_value).replace(",", ""))
            if budget_value not in (None, "") and not isinstance(budget_value, bool)
            else None
        )
    except (TypeError, ValueError):
        budget_manwon = None

    missing = [
        label
        for value, label in (
            (area_code, "상권 코드"),
            (industry_code, "업종 코드"),
            (quarter, "기준 분기"),
            (budget_manwon, "예산"),
        )
        if value in (None, "")
    ]
    return {
        "area_code": str(area_code) if area_code not in (None, "") else None,
        "area_name": str(area_name) if area_name not in (None, "") else None,
        "industry_code": (
            str(industry_code) if industry_code not in (None, "") else None
        ),
        "industry_name": (
            str(industry_name) if industry_name not in (None, "") else None
        ),
        "quarter": str(quarter) if quarter not in (None, "") else None,
        "budget_manwon": budget_manwon,
        "ai_model": report.get("ai_model"),
        "generation_mode": report.get("generation_mode"),
        "quality_status": report.get("quality_status"),
        "evaluable": job.report_type == "single" and not missing,
        "not_evaluable_reason": (
            None
            if job.report_type == "single" and not missing
            else (
                "비교 리포트는 현재 상세 grounding 평가 대상이 아닙니다."
                if job.report_type != "single"
                else f"평가 필수값 누락: {', '.join(missing)}"
            )
        ),
    }


def _pending_manual_review(artifact_dir: Path) -> dict[str, Any]:
    return {
        "schema_version": "localfit.manual-visual-review.v1",
        "evaluator": "관리자 수동 검수 대기",
        "reviewed_at": None,
        "artifact_sha256": {
            "C3.png": _file_sha256(artifact_dir / "charts" / "C3.png"),
            "C5.png": _file_sha256(artifact_dir / "charts" / "C5.png"),
            "report.pdf": _file_sha256(artifact_dir / "report.pdf"),
        },
        "questions": {
            "Q050": {
                "decision": "PENDING",
                "actual": "관리자 화면에서 차트 원본을 수동 검수하기 전입니다.",
                "expected": "C3=억원, C5=만명 등 수치 해석에 필요한 단위를 차트 안에서 직접 표시",
                "rationale_ko": "자동 평가 이후 차트 단위 표시를 별도로 확인해야 합니다.",
            },
            "Q051": {
                "decision": "PENDING",
                "actual": "관리자 화면에서 PDF 페이지 배치를 수동 검수하기 전입니다.",
                "expected": "외부자료 제목·설명과 표가 페이지 경계에서 고립되지 않아야 함",
                "rationale_ko": "자동 평가 이후 PDF 페이지 배치를 별도로 확인해야 합니다.",
            },
        },
    }


def _managed_run_paths(
    run: ReportEvaluationRun,
) -> tuple[Path, Path]:
    output_dir = Path(run.output_dir or "").resolve()
    expected_output_dir = (
        ADMIN_EVALUATION_ROOT / run.report_job_id / run.id
    ).resolve()
    if output_dir != expected_output_dir:
        raise ValueError("평가 산출물 경로가 관리자 평가 폴더와 일치하지 않습니다.")
    artifact_dir = (REPORTS_OUT / f"admin-eval-{run.id}").resolve()
    return output_dir, artifact_dir


def evaluation_artifact_path(
    run: ReportEvaluationRun,
    artifact_name: str,
) -> Path:
    output_dir, artifact_dir = _managed_run_paths(run)
    allowed = {
        "report.pdf": artifact_dir / "report.pdf",
        "C3.png": artifact_dir / "charts" / "C3.png",
        "C5.png": artifact_dir / "charts" / "C5.png",
        "evaluation-report.md": output_dir / "EVALUATION_REPORT_KO.md",
    }
    path = allowed.get(artifact_name)
    if path is None:
        raise ValueError("허용되지 않은 평가 산출물입니다.")
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def write_manual_review_input(
    run: ReportEvaluationRun,
    review: dict[str, dict[str, str]],
    reviewer: str,
) -> Path:
    output_dir, artifact_dir = _managed_run_paths(run)
    for path in (
        artifact_dir / "charts" / "C3.png",
        artifact_dir / "charts" / "C5.png",
        artifact_dir / "report.pdf",
    ):
        if not path.is_file():
            raise FileNotFoundError(path)
    document = {
        "schema_version": "localfit.manual-visual-review.v1",
        "evaluator": reviewer,
        "reviewer": reviewer,
        "reviewed_at": _now_iso(),
        "artifact_sha256": {
            "C3.png": _file_sha256(artifact_dir / "charts" / "C3.png"),
            "C5.png": _file_sha256(artifact_dir / "charts" / "C5.png"),
            "report.pdf": _file_sha256(artifact_dir / "report.pdf"),
        },
        "questions": {
            "Q050": {
                **review["Q050"],
                "expected": (
                    "C3=억원, C5=만명 등 수치 해석에 필요한 단위를 "
                    "차트 안에서 직접 표시"
                ),
            },
            "Q051": {
                **review["Q051"],
                "expected": (
                    "외부자료 제목·설명과 표가 페이지 경계에서 "
                    "고립되지 않아야 함"
                ),
            },
        },
    }
    path = output_dir / "manual_visual_review.admin-input.json"
    history_path = (
        output_dir
        / "manual_review_history"
        / (
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            + ".json"
        )
    )
    _write_json(history_path, document)
    _write_json(path, document)
    return path


def _evaluation_command(
    *,
    report_json_path: Path,
    artifact_dir: Path,
    output_dir: Path,
    manual_review_path: Path,
    context: dict[str, Any],
) -> list[str]:
    command = [
        sys.executable,
        str(EVALUATOR_SCRIPT),
        "--db",
        str(DATABASE_PATH),
        "--artifact-dir",
        str(artifact_dir),
        "--report-json",
        str(report_json_path),
        "--output-dir",
        str(output_dir),
        "--manual-review",
        str(manual_review_path),
        "--area-code",
        str(context["area_code"]),
        "--industry-code",
        str(context["industry_code"]),
        "--no-fail-exit",
    ]
    if context.get("budget_manwon") is not None:
        command.extend(["--budget-manwon", str(context["budget_manwon"])])
    return command


def _validate_evaluation_output(
    summary: dict[str, Any],
    questions: list[dict[str, Any]],
) -> None:
    if summary.get("protocol_version") != EXPECTED_PROTOCOL_VERSION:
        raise RuntimeError(
            "평가기 프로토콜이 관리자 계약과 다릅니다. "
            f"expected={EXPECTED_PROTOCOL_VERSION}, "
            f"actual={summary.get('protocol_version')}"
        )

    question_ids = [str(row.get("id") or "") for row in questions]
    if len(question_ids) != len(set(question_ids)):
        raise RuntimeError("평가 결과에 중복 문항 ID가 있습니다.")
    if tuple(sorted(question_ids)) != EXPECTED_QUESTION_IDS:
        missing = sorted(set(EXPECTED_QUESTION_IDS) - set(question_ids))
        unexpected = sorted(set(question_ids) - set(EXPECTED_QUESTION_IDS))
        raise RuntimeError(
            "56문항 평가 결과가 불완전합니다. "
            f"missing={missing}, unexpected={unexpected}"
        )

    invalid_decisions = [
        row.get("id")
        for row in questions
        if row.get("decision") not in {"PASS", "FAIL"}
        or not isinstance(row.get("gate"), bool)
    ]
    if invalid_decisions:
        raise RuntimeError(
            f"평가 판정 계약이 잘못된 문항: {invalid_decisions}"
        )

    failures = [row for row in questions if row["decision"] == "FAIL"]
    hard_failures = [
        row for row in failures if row.get("gate") is True
    ]
    expected_summary = {
        "question_count": len(questions),
        "pass_count": len(questions) - len(failures),
        "fail_count": len(failures),
        "hard_fail_count": len(hard_failures),
        "overall_status": "FAIL" if hard_failures else "PASS",
    }
    for key, expected in expected_summary.items():
        if summary.get(key) != expected:
            raise RuntimeError(
                f"평가 요약 불일치: {key} expected={expected}, "
                f"actual={summary.get(key)}"
            )

    failed_ids = [str(row["id"]) for row in failures]
    hard_failed_ids = [str(row["id"]) for row in hard_failures]
    if summary.get("failed_question_ids") != failed_ids:
        raise RuntimeError("평가 요약의 failed_question_ids가 원본 결과와 다릅니다.")
    if summary.get("hard_failed_question_ids") != hard_failed_ids:
        raise RuntimeError(
            "평가 요약의 hard_failed_question_ids가 원본 결과와 다릅니다."
        )

    by_id = {str(row["id"]): row for row in questions}
    if by_id["Q050"].get("method") != "independent_visual_review":
        raise RuntimeError("Q050 수동 시각검수 계약이 바뀌었습니다.")
    if by_id["Q051"].get("method") != "independent_pdf_page_review":
        raise RuntimeError("Q051 PDF 혼합검수 계약이 바뀌었습니다.")
    q051_actual = by_id["Q051"].get("actual")
    q051_layout = (
        q051_actual.get("automated_layout")
        if isinstance(q051_actual, dict)
        else None
    )
    if (
        not isinstance(q051_layout, dict)
        or not isinstance(q051_layout.get("passed"), bool)
    ):
        raise RuntimeError("Q051 자동 PDF 배치 판정값이 없습니다.")


def _automatic_failure_ids(
    questions: list[dict[str, Any]],
) -> list[str]:
    failures: list[str] = []
    for row in questions:
        if not row.get("gate") or row.get("decision") != "FAIL":
            continue
        method = row.get("method")
        if method == "independent_visual_review":
            continue
        if method == "independent_pdf_page_review":
            actual = row.get("actual")
            automated_layout = (
                actual.get("automated_layout")
                if isinstance(actual, dict)
                else None
            )
            if (
                isinstance(automated_layout, dict)
                and automated_layout.get("passed") is False
            ):
                failures.append(str(row["id"]))
            continue
        failures.append(str(row["id"]))
    return failures


def _load_job_report_context(
    db,
    run: ReportEvaluationRun,
) -> tuple[ReportGenerationJob, dict[str, Any], dict[str, Any]]:
    job = db.get(ReportGenerationJob, run.report_job_id)
    if (
        job is None
        or job.status != "completed"
        or job.report_type != "single"
        or not job.result_json
    ):
        raise ValueError("완료된 단일 상세 리포트를 찾지 못했습니다.")
    report = json.loads(job.result_json)
    if not isinstance(report, dict):
        raise ValueError("저장된 리포트 결과가 JSON 객체가 아닙니다.")
    if report_sha256(report) != run.report_sha256:
        raise ValueError("평가 대기 중 리포트 원문이 변경되었습니다.")
    context = report_evaluation_context(job, report)
    if not context["evaluable"]:
        raise ValueError(context["not_evaluable_reason"])
    return job, report, context


def _run_evaluator(
    *,
    report_json_path: Path,
    artifact_dir: Path,
    output_dir: Path,
    manual_review_path: Path,
    context: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        _evaluation_command(
            report_json_path=report_json_path,
            artifact_dir=artifact_dir,
            output_dir=output_dir,
            manual_review_path=manual_review_path,
            context=context,
        ),
        cwd=BACKEND_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr or completed.stdout)
    summary = _read_json(output_dir / "summary.json")
    questions = _read_question_results(output_dir / "question_results.jsonl")
    if not isinstance(summary, dict):
        raise RuntimeError("평가기 summary.json이 JSON 객체가 아닙니다.")
    _validate_evaluation_output(summary, questions)
    return summary, questions


def _persist_evaluation_result(
    db,
    *,
    run_id: str,
    job: ReportGenerationJob,
    context: dict[str, Any],
    output_dir: Path,
    manual_review_path: Path,
    summary: dict[str, Any],
    questions: list[dict[str, Any]],
) -> None:
    automatic_failures = _automatic_failure_ids(questions)
    manual_question_ids = [
        str(row["id"])
        for row in questions
        if row.get("method") in MANUAL_REVIEW_METHODS
    ]
    manual_review = _read_json(manual_review_path)
    manual_questions = (
        manual_review.get("questions")
        if isinstance(manual_review, dict)
        else {}
    )
    manual_complete = bool(manual_question_ids) and all(
        isinstance(manual_questions, dict)
        and (manual_questions.get(question_id) or {}).get("decision")
        in {"PASS", "FAIL"}
        for question_id in manual_question_ids
    )
    summary["automatic_status"] = "FAIL" if automatic_failures else "PASS"
    summary["automatic_failed_question_ids"] = automatic_failures
    summary["manual_review_status"] = (
        "COMPLETE"
        if manual_complete
        else "PENDING"
        if manual_question_ids
        else "NOT_REQUIRED"
    )
    summary["manual_review_question_ids"] = manual_question_ids
    if manual_complete and isinstance(manual_review, dict):
        summary["manual_review"] = {
            "reviewer": manual_review.get("reviewer")
            or manual_review.get("evaluator"),
            "reviewed_at": manual_review.get("reviewed_at"),
        }
        history_documents: list[dict[str, Any]] = []
        history_dir = output_dir / "manual_review_history"
        if history_dir.is_dir():
            for history_path in sorted(history_dir.glob("*.json"))[-100:]:
                try:
                    history_document = _read_json(history_path)
                except (OSError, json.JSONDecodeError):
                    continue
                if isinstance(history_document, dict):
                    history_documents.append(history_document)
        if not history_documents:
            history_documents = [manual_review]
        summary["manual_review_history"] = [
            {
                "reviewer": document.get("reviewer")
                or document.get("evaluator"),
                "reviewed_at": document.get("reviewed_at"),
                "artifact_sha256": document.get("artifact_sha256"),
                "questions": document.get("questions"),
            }
            for document in history_documents
        ]
    else:
        summary.pop("manual_review", None)
        summary.pop("manual_review_history", None)
    summary["report_job_id"] = job.id
    run = db.get(ReportEvaluationRun, run_id)
    if run is None:
        return
    summary["report_sha256"] = run.report_sha256
    summary["context"] = context
    run.status = "completed"
    run.progress_message = (
        f"평가 완료 · 자동 {summary['automatic_status']} · "
        f"전체 {summary.get('overall_status', '-')}"
    )
    run.protocol_version = summary.get("protocol_version")
    run.overall_status = summary.get("overall_status")
    run.automatic_status = summary["automatic_status"]
    run.summary_json = json.dumps(summary, ensure_ascii=False, default=str)
    run.question_results_json = json.dumps(
        questions,
        ensure_ascii=False,
        default=str,
    )
    run.output_dir = str(output_dir)
    run.error_message = None
    run.completed_at = _now_iso()
    db.commit()


def _mark_evaluation_failed(db, run_id: str, error: Exception) -> None:
    db.rollback()
    run = db.get(ReportEvaluationRun, run_id)
    if run is None:
        return
    run.status = "failed"
    run.progress_message = "상세 리포트 평가 실행에 실패했습니다."
    run.error_message = _safe_error(error)
    run.completed_at = _now_iso()
    db.commit()


def execute_report_evaluation(run_id: str) -> None:
    db = SessionLocal()
    try:
        run = db.get(ReportEvaluationRun, run_id)
        if run is None or run.status != "queued":
            return
        run.status = "running"
        run.progress_message = "기존 상세 리포트 평가기를 실행하고 있습니다."
        run.started_at = _now_iso()
        db.commit()

        job, report, context = _load_job_report_context(db, run)

        output_dir = ADMIN_EVALUATION_ROOT / job.id / run.id
        output_dir.mkdir(parents=True, exist_ok=True)
        report_json_path = output_dir / "report_response.generated.json"
        _write_json(
            report_json_path,
            {
                "request": {
                    "area_code": context["area_code"],
                    "industry_code": context["industry_code"],
                    "budget_manwon": context["budget_manwon"],
                },
                "report": report,
            },
        )
        artifacts = publish_report_artifacts(f"admin-eval-{run.id}", report)
        artifact_dir = Path(artifacts["report_dir"]).resolve()
        manual_review_path = output_dir / "manual_visual_review.input.json"
        _write_json(manual_review_path, _pending_manual_review(artifact_dir))

        summary, questions = _run_evaluator(
            report_json_path=report_json_path,
            artifact_dir=artifact_dir,
            output_dir=output_dir,
            manual_review_path=manual_review_path,
            context=context,
        )
        _persist_evaluation_result(
            db,
            run_id=run_id,
            job=job,
            context=context,
            output_dir=output_dir,
            manual_review_path=manual_review_path,
            summary=summary,
            questions=questions,
        )
    except Exception as error:
        _mark_evaluation_failed(db, run_id, error)
    finally:
        db.close()


def execute_manual_report_evaluation(run_id: str) -> None:
    db = SessionLocal()
    try:
        run = db.get(ReportEvaluationRun, run_id)
        if run is None or run.status != "queued":
            return
        run.status = "running"
        run.progress_message = "수동 시각검수 결과를 반영해 다시 평가하고 있습니다."
        run.started_at = _now_iso()
        run.completed_at = None
        db.commit()

        job, _report, context = _load_job_report_context(db, run)
        output_dir, artifact_dir = _managed_run_paths(run)
        report_json_path = output_dir / "report_response.generated.json"
        manual_review_path = output_dir / "manual_visual_review.admin-input.json"
        if not report_json_path.is_file():
            raise FileNotFoundError(report_json_path)
        if not manual_review_path.is_file():
            raise FileNotFoundError(manual_review_path)
        summary, questions = _run_evaluator(
            report_json_path=report_json_path,
            artifact_dir=artifact_dir,
            output_dir=output_dir,
            manual_review_path=manual_review_path,
            context=context,
        )
        _persist_evaluation_result(
            db,
            run_id=run_id,
            job=job,
            context=context,
            output_dir=output_dir,
            manual_review_path=manual_review_path,
            summary=summary,
            questions=questions,
        )
    except Exception as error:
        _mark_evaluation_failed(db, run_id, error)
    finally:
        db.close()
