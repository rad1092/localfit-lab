from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
DEFAULT_RUN_ROOT = (
    PROJECT_ROOT / "runtime" / "evaluations" / "two-tier-news-random15-20260723"
)
DEFAULT_REPORTS_ROOT = (
    PROJECT_ROOT / "runtime" / "reports" / "two-tier-news-random15-20260723"
)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the detailed grounding evaluator across a frozen report-case manifest."
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_RUN_ROOT / "cases.json",
    )
    parser.add_argument(
        "--generation-stage",
        type=Path,
        default=DEFAULT_RUN_ROOT / "generated",
    )
    parser.add_argument(
        "--reports-root",
        type=Path,
        default=DEFAULT_REPORTS_ROOT,
    )
    parser.add_argument("--label", default="generated")
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_RUN_ROOT / "commercial.eval.db",
    )
    parser.add_argument(
        "--manual-review-root",
        type=Path,
        default=DEFAULT_RUN_ROOT / "manual_visual_reviews",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_RUN_ROOT / "detailed_evaluations",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest_path = args.manifest.resolve()
    generation_stage = args.generation_stage.resolve()
    reports_root = args.reports_root.resolve()
    db_path = args.db.resolve()
    manual_review_root = args.manual_review_root.resolve()
    output_root = args.output_root.resolve()
    evaluator = BACKEND_ROOT / "scripts" / "evaluate_detailed_report_grounding.py"

    manifest = _load_json(manifest_path)
    cases = manifest.get("cases") if isinstance(manifest, dict) else None
    if not isinstance(cases, list) or not cases:
        raise ValueError("manifest must contain a non-empty cases array")
    if not db_path.exists():
        raise FileNotFoundError(db_path)

    rows: list[dict[str, Any]] = []
    reproduction_lines = [
        "$ErrorActionPreference = 'Stop'",
        f"Set-Location '{PROJECT_ROOT}'",
        "",
    ]
    for index, case in enumerate(cases, 1):
        case_id = str(case["id"])
        artifact_id = f"{case_id}_r1"
        report_json = generation_stage / f"{artifact_id}.json"
        artifact_dir = reports_root / args.label / artifact_id
        manual_review = manual_review_root / f"{case_id}.json"
        output_dir = output_root / case_id
        required = [
            report_json,
            artifact_dir / "report.md",
            artifact_dir / "report.pdf",
            manual_review,
        ]
        missing = [str(path) for path in required if not path.exists()]
        if missing:
            rows.append(
                {
                    "case": case,
                    "artifact_id": artifact_id,
                    "status": "ERROR",
                    "error": "missing required files",
                    "missing": missing,
                }
            )
            continue

        command = [
            sys.executable,
            str(evaluator),
            "--db",
            str(db_path),
            "--artifact-dir",
            str(artifact_dir),
            "--report-json",
            str(report_json),
            "--output-dir",
            str(output_dir),
            "--manual-review",
            str(manual_review),
            "--no-fail-exit",
        ]
        print(f"[{index}/{len(cases)}] evaluate {case_id}", flush=True)
        completed = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "evaluator_stdout.txt").write_text(
            completed.stdout,
            encoding="utf-8",
        )
        (output_dir / "evaluator_stderr.txt").write_text(
            completed.stderr,
            encoding="utf-8",
        )
        summary_path = output_dir / "summary.json"
        if summary_path.exists():
            summary = _load_json(summary_path)
            report_payload = _load_json(report_json)
            report = report_payload.get("report", report_payload)
            decision_count = len(report.get("decision_news_evidence") or [])
            monitoring_count = len(report.get("monitoring_news_evidence") or [])
            row = {
                "case": case,
                "artifact_id": artifact_id,
                "status": summary.get("overall_status"),
                "question_count": summary.get("question_count"),
                "pass_count": summary.get("pass_count"),
                "fail_count": summary.get("fail_count"),
                "hard_fail_count": summary.get("hard_fail_count"),
                "failed_question_ids": summary.get("failed_question_ids"),
                "hard_failed_question_ids": summary.get("hard_failed_question_ids"),
                "decision_news_count": decision_count,
                "monitoring_news_count": monitoring_count,
                "pdf_sha256": _sha256(artifact_dir / "report.pdf"),
                "evaluation_dir": str(output_dir),
                "returncode": completed.returncode,
            }
        else:
            row = {
                "case": case,
                "artifact_id": artifact_id,
                "status": "ERROR",
                "error": "evaluator did not produce summary.json",
                "returncode": completed.returncode,
                "stderr": completed.stderr[-4000:],
            }
        rows.append(row)
        reproduction_lines.extend(
            [
                (
                    f"& '{sys.executable}' '{evaluator}' "
                    f"--db '{db_path}' --artifact-dir '{artifact_dir}' "
                    f"--report-json '{report_json}' --output-dir '{output_dir}' "
                    f"--manual-review '{manual_review}'"
                ),
                "",
            ]
        )

    status_counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status") or "ERROR")
        status_counts[status] = status_counts.get(status, 0) + 1
    all_pass = len(rows) == len(cases) and all(
        row.get("status") == "PASS" for row in rows
    )
    aggregate = {
        "protocol_version": "detailed-report-random-batch.v1.0",
        "evaluated_at": datetime.now().astimezone().isoformat(),
        "manifest": str(manifest_path),
        "manifest_sha256": _sha256(manifest_path),
        "database": str(db_path),
        "database_sha256": _sha256(db_path),
        "case_count": len(cases),
        "evaluated_case_count": len(rows),
        "status_counts": status_counts,
        "overall_status": "PASS" if all_pass else "FAIL",
        "decision_rule_ko": "15건이 모두 개별 hard-gate PASS일 때만 전체 PASS",
        "total_questions": sum(int(row.get("question_count") or 0) for row in rows),
        "total_pass": sum(int(row.get("pass_count") or 0) for row in rows),
        "total_fail": sum(int(row.get("fail_count") or 0) for row in rows),
        "total_decision_news": sum(
            int(row.get("decision_news_count") or 0) for row in rows
        ),
        "total_monitoring_news": sum(
            int(row.get("monitoring_news_count") or 0) for row in rows
        ),
        "rows": rows,
    }
    _write_json(output_root / "batch_summary.json", aggregate)
    (output_root / "reproduce_all.ps1").write_text(
        "\n".join(reproduction_lines).rstrip() + "\n",
        encoding="utf-8-sig",
    )
    print(json.dumps(aggregate, ensure_ascii=False, indent=2))
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
