from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
DEFAULT_SOURCE_DB = PROJECT_ROOT / "runtime" / "db" / "commercial.db"
DEFAULT_RUN_ROOT = PROJECT_ROOT / "runtime" / "evaluations" / "report-template-20260721"


CASES: tuple[dict[str, Any], ...] = (
    {
        "id": "yangjae_chicken_a",
        "label": "양재역 · 치킨전문점 · A · 예산 5억원",
        "area_code": "3120179",
        "industry_code": "CS100007",
        "budget": 50000,
        "coverage": "full_4axis",
    },
    {
        "id": "geumnam_chicken_e",
        "label": "금남시장 · 치킨전문점 · E · 예산 5억원",
        "area_code": "3130064",
        "industry_code": "CS100007",
        "budget": 50000,
        "coverage": "full_4axis",
    },
    {
        "id": "gangnam_hair_a",
        "label": "강남역 · 미용실 · A · 예산 7천만원",
        "area_code": "3120189",
        "industry_code": "CS200028",
        "budget": 7000,
        "coverage": "full_4axis",
    },
    {
        "id": "seongsu_coffee_b",
        "label": "성수동카페거리 · 커피-음료 · B · 예산 1억5천만원",
        "area_code": "3110131",
        "industry_code": "CS100010",
        "budget": 15000,
        "coverage": "full_4axis",
    },
    {
        "id": "hongdae_korean_c",
        "label": "홍대입구역 3번 · 한식음식점 · C · 예산 6천만원",
        "area_code": "3110564",
        "industry_code": "CS100001",
        "budget": 6000,
        "coverage": "full_4axis",
    },
    {
        "id": "tongin_korean_d",
        "label": "통인시장 · 한식음식점 · D · 예산 5천만원",
        "area_code": "3130001",
        "industry_code": "CS100001",
        "budget": 5000,
        "coverage": "full_4axis",
    },
    {
        "id": "yeongjin_coffee_e",
        "label": "영진시장A동 · 커피-음료 · E · 예산 4천만원",
        "area_code": "3130258",
        "industry_code": "CS100010",
        "budget": 4000,
        "coverage": "full_4axis",
    },
    {
        "id": "magok_coffee_context",
        "label": "마곡역 · 커피-음료 · 공식 종합등급 없음",
        "area_code": "3120118",
        "industry_code": "CS100010",
        "budget": 10000,
        "coverage": "context_only_3axis",
    },
    {
        "id": "gangnam_mice_convenience_context",
        "label": "강남 마이스 관광특구 · 편의점 · 공식 종합등급 없음",
        "area_code": "3001496",
        "industry_code": "CS300002",
        "budget": 20000,
        "coverage": "context_only_partial_4axis",
    },
)


ISSUE_CODE_RE = re.compile(r"^\[([A-Z_]+)\]")
SENTENCE_RE = re.compile(r"(?<=[.!?다요])\s+")
NARRATIVE_FIELDS = (
    "executive_interpretation",
    "score_interpretation",
    "trend_analysis",
    "user_fit",
    "summary",
)
LIST_NARRATIVE_FIELDS = ("thesis", "risk_factors", "action_plan", "onsite_checklist")


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json_text(value) + "\n", encoding="utf-8")


def _load_case_manifest(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("cases") if isinstance(payload, dict) else payload
    if not isinstance(rows, list) or not rows:
        raise ValueError("case manifest must contain a non-empty cases array")
    required = {"id", "area_code", "industry_code", "budget"}
    cases: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(rows, 1):
        if not isinstance(raw, dict):
            raise ValueError(f"case #{index} must be an object")
        missing = sorted(required - set(raw))
        if missing:
            raise ValueError(f"case #{index} is missing fields: {', '.join(missing)}")
        case_id = str(raw["id"]).strip()
        if not case_id or case_id in seen_ids:
            raise ValueError(f"case #{index} has an empty or duplicate id: {case_id!r}")
        budget = int(raw["budget"])
        if budget <= 0:
            raise ValueError(f"case {case_id} budget must be positive")
        case = {
            **raw,
            "id": case_id,
            "label": str(raw.get("label") or case_id),
            "area_code": str(raw["area_code"]),
            "industry_code": str(raw["industry_code"]),
            "budget": budget,
        }
        cases.append(case)
        seen_ids.add(case_id)
    return cases


def sample_cases(
    *,
    source_db: Path,
    output: Path,
    count: int,
    seed: int,
    coverage: str,
    budgets: list[int],
) -> int:
    if count < 1:
        raise ValueError("count must be at least 1")
    if not budgets or any(int(value) <= 0 for value in budgets):
        raise ValueError("budgets must be positive integers")
    with sqlite3.connect(source_db) as conn:
        conn.row_factory = sqlite3.Row
        quarter = str(
            conn.execute("SELECT MAX(quarter) FROM rule_location_score").fetchone()[0]
        )
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT
                    quarter,
                    area_code,
                    area_name,
                    district_name,
                    industry_code,
                    industry_name,
                    grade,
                    current_location_score,
                    score_coverage_tier AS coverage
                FROM rule_location_score
                WHERE quarter = ?
                  AND score_coverage_tier = ?
                  AND official_rank_eligible = 1
                ORDER BY area_code, industry_code
                """,
                (quarter, coverage),
            )
        ]
    rng = random.Random(seed)
    rng.shuffle(rows)
    selected: list[dict[str, Any]] = []
    used_areas: set[str] = set()
    used_industries: set[str] = set()
    for row in rows:
        area_code = str(row["area_code"])
        industry_code = str(row["industry_code"])
        if area_code in used_areas or industry_code in used_industries:
            continue
        budget = int(rng.choice(budgets))
        case_number = len(selected) + 1
        selected.append(
            {
                "id": f"random_{case_number:02d}_{area_code}_{industry_code.lower()}",
                "label": (
                    f"{row['area_name']} · {row['industry_name']} · "
                    f"예산 {budget:,}만원"
                ),
                "area_code": area_code,
                "area_name": row["area_name"],
                "district_name": row["district_name"],
                "industry_code": industry_code,
                "industry_name": row["industry_name"],
                "budget": budget,
                "coverage": row["coverage"],
                "reference_grade": row["grade"],
                "reference_location_score": row["current_location_score"],
                "quarter": row["quarter"],
            }
        )
        used_areas.add(area_code)
        used_industries.add(industry_code)
        if len(selected) == count:
            break
    if len(selected) != count:
        raise RuntimeError(
            f"could select only {len(selected)} unique area/industry cases from {len(rows)} rows"
        )
    manifest = {
        "protocol_version": "random-detailed-report-cases.v1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_db": str(source_db.resolve()),
        "quarter": quarter,
        "seed": seed,
        "population": {
            "table": "rule_location_score",
            "row_count": len(rows),
            "coverage": coverage,
            "official_rank_eligible": 1,
        },
        "constraints": {
            "unique_area_code": True,
            "unique_industry_code": True,
            "reason": (
                "15건의 지역·업종 다양성을 확보하면서 모든 상세 평가 문항을 동일하게 "
                "적용하기 위해 공식 4축 점수 산출 가능 모집단으로 제한한다."
            ),
        },
        "budget_candidates_manwon": budgets,
        "cases": selected,
    }
    _write_json(output, manifest)
    print(_json_text(manifest))
    return 0


def _issue_codes(issues: list[str] | None) -> list[str]:
    codes: list[str] = []
    for issue in issues or []:
        match = ISSUE_CODE_RE.match(str(issue))
        codes.append(match.group(1) if match else "UNKNOWN")
    return codes


def _narrative_text(data: dict[str, Any]) -> str:
    chunks = [str(data.get(field) or "") for field in NARRATIVE_FIELDS]
    for field in LIST_NARRATIVE_FIELDS:
        chunks.extend(str(item or "") for item in data.get(field) or [])
    for axis in data.get("axis_interpretations") or []:
        chunks.extend(str(axis.get(field) or "") for field in ("meaning", "risk", "action", "next_check"))
    return "\n".join(chunk for chunk in chunks if chunk.strip())


def _upgrade_two_tier_markdown(data: dict[str, Any]) -> dict[str, Any]:
    markdown = str(data.get("markdown_body") or "")
    if not markdown:
        return data
    data["markdown_body"] = (
        markdown.replace("### 두 단계 외부 자료", "## 두 단계 외부 자료")
        .replace("#### 1단계 · 판단 근거", "### 1단계 · 판단 근거")
        .replace("#### 2단계 · 참고·모니터링", "### 2단계 · 참고·모니터링")
    )
    return data


def _metrics(case: dict[str, Any], data: dict[str, Any], elapsed_sec: float) -> dict[str, Any]:
    original = list(data.get("original_validation_issues") or [])
    final = list(data.get("validation_issues") or [])
    warnings = list(data.get("quality_warnings") or [])
    usage = dict(data.get("token_usage") or {})
    text = _narrative_text(data)
    return {
        "case": case,
        "elapsed_sec": round(elapsed_sec, 3),
        "model": data.get("ai_model") or usage.get("model"),
        "reasoning_effort": data.get("reasoning_effort") or usage.get("reasoning_effort"),
        "generation_mode": data.get("generation_mode"),
        "ai_generated": data.get("ai_generated"),
        "quality_status": data.get("quality_status"),
        "original_issue_count": len(original),
        "original_issue_codes": _issue_codes(original),
        "final_issue_count": len(final),
        "final_issue_codes": _issue_codes(final),
        "warning_count": len(warnings),
        "warning_codes": _issue_codes(warnings),
        "fallback_fields": list(data.get("fallback_fields") or []),
        "repair_log": list(data.get("section_repair_log") or []),
        "input_tokens": int(usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or 0),
        "provider_usage": usage.get("estimated") is False,
        "narrative_chars": len(text),
        "narrative_sentences": len([part for part in SENTENCE_RE.split(text) if part.strip()]),
        "internal_news_marker_leaks": len(re.findall(r"\[(?:NEWS|근거)[: ]?\d+\]", text)),
    }


def _backup_database(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        return
    if not source.exists():
        raise FileNotFoundError(f"source database not found: {source}")
    with sqlite3.connect(source) as source_conn, sqlite3.connect(destination) as destination_conn:
        source_conn.backup(destination_conn)


def _configure_eval_runtime(
    run_root: Path,
    eval_db: Path,
    *,
    reasoning_effort: str,
    pdf_root: Path | None = None,
) -> None:
    os.environ["LOCALFIT_RUNTIME_ROOT"] = str(run_root / "isolated-runtime")
    os.environ["LOCALFIT_DATABASE_PATH"] = str(eval_db)
    if pdf_root is not None:
        os.environ["LOCALFIT_REPORTS_ROOT"] = str(pdf_root)
    os.environ.setdefault("OPENAI_MODEL", "gpt-5.4-mini")
    # An evaluation must never inherit an admin setting from another runtime.
    os.environ["OPENAI_REPORT_REASONING_EFFORT"] = reasoning_effort
    if str(BACKEND_ROOT) not in sys.path:
        sys.path.insert(0, str(BACKEND_ROOT))


def generate(
    label: str,
    run_root: Path,
    source_db: Path,
    case_ids: list[str] | None = None,
    *,
    cases: list[dict[str, Any]] | None = None,
    case_manifest: Path | None = None,
    reasoning_effort: str = "medium",
    repeats: int = 1,
    publish_pdf: bool = False,
    pdf_root: Path | None = None,
    resume_existing: bool = False,
) -> int:
    if repeats < 1:
        raise ValueError("repeats must be at least 1")
    eval_db = run_root / "commercial.eval.db"
    _backup_database(source_db, eval_db)
    _configure_eval_runtime(
        run_root,
        eval_db,
        reasoning_effort=reasoning_effort,
        pdf_root=pdf_root,
    )

    from app.ai.recursive_layer import calculate_token_cost
    from app.database import SessionLocal
    from app.repositories.commercial_area import CommercialAreaRepository
    from app.services import interpretive_report
    from app.services.llm_runtime_settings import get_report_reasoning_effort
    from app.services.report_publisher import publish_report_artifacts
    from app.services.single_report import SingleReportService

    effective_reasoning_effort = get_report_reasoning_effort()
    if effective_reasoning_effort != reasoning_effort:
        raise RuntimeError(
            "reasoning effort isolation failed: "
            f"requested={reasoning_effort}, effective={effective_reasoning_effort}"
        )

    # Every A/B trial is an independent paid call. The operating cache is never read or written.
    interpretive_report._read_cache = lambda _payload: None
    interpretive_report._write_cache = lambda _payload, _data: None
    # Freeze the dynamic source: durable local evidence remains available through the merger.
    interpretive_report.fetch_live_naver_news = lambda _payload: []

    stage_root = run_root / label
    stage_root.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    reused_case_count = 0
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def generate_unwrapped(service: SingleReportService, case: dict[str, Any]) -> dict[str, Any]:
        """Run the production interpretation chain before the HTTP response schema wraps it."""
        area_code = str(case["area_code"])
        db_item = service.repository.get_by_code(area_code)
        if not db_item:
            raise RuntimeError(f"unknown area code: {area_code}")
        summary = service.area_service._area_summary(area_code)
        resolved = service.area_service.resolve_industry(str(case["industry_code"]))
        rule = service.area_service._rule_score(area_code, resolved["industry_code"]) if resolved else None
        axes = rule or service.area_service._area_axis_summary(area_code)
        score_value = rule.get("current_location_score") if rule else summary.get("score") if summary else None
        score = float(score_value) if score_value is not None else None
        score_source = (
            "rule_location_score.full_4axis"
            if rule and score is not None
            else "rule_location_score.context_only"
            if rule
            else "rule_area_score_summary.area_context"
        )
        payload = service._rule_payload(
            area_code=area_code,
            area_name=db_item.area_name,
            resolved=resolved,
            rule=rule,
            summary=summary,
            axes=axes,
            score=score,
            score_source=score_source,
            top_industries=service._top_industries(area_code),
            budget=int(case["budget"]),
        )
        return interpretive_report.interpret_single_report(payload)

    available_cases = cases if cases is not None else list(CASES)
    selected_cases = [
        case for case in available_cases if not case_ids or case["id"] in set(case_ids)
    ]
    if not selected_cases:
        raise ValueError("no evaluation cases selected")
    call_specs = [
        (case, repeat_index)
        for repeat_index in range(1, repeats + 1)
        for case in selected_cases
    ]

    for index, (case, repeat_index) in enumerate(call_specs, 1):
        artifact_id = f"{case['id']}_r{repeat_index}"
        case_record = {**case, "repeat": repeat_index, "artifact_id": artifact_id}
        record_path = stage_root / f"{artifact_id}.json"
        if resume_existing and record_path.exists():
            existing = json.loads(record_path.read_text(encoding="utf-8"))
            existing_data = existing.get("report") if isinstance(existing, dict) else None
            existing_metrics = existing.get("metrics") if isinstance(existing, dict) else None
            if (
                isinstance(existing_data, dict)
                and isinstance(existing_metrics, dict)
                and not existing_metrics.get("exception")
            ):
                data = _upgrade_two_tier_markdown(dict(existing_data))
                row = _metrics(
                    case_record,
                    data,
                    float(existing_metrics.get("elapsed_sec") or 0),
                )
                row["estimated_cost_usd"] = float(
                    existing_metrics.get("estimated_cost_usd") or 0
                )
                row["resumed_from_existing"] = True
                if publish_pdf:
                    try:
                        safe_label = (
                            re.sub(r"[^A-Za-z0-9_.-]+", "_", label).strip("._")
                            or "run"
                        )
                        artifacts = publish_report_artifacts(
                            f"{safe_label}/{artifact_id}",
                            data,
                        )
                        pdf_path = Path(str(artifacts["pdf_path"]))
                        row["pdf_artifact"] = {
                            **artifacts,
                            "pdf_bytes": pdf_path.stat().st_size,
                            "pdf_sha256": hashlib.sha256(
                                pdf_path.read_bytes()
                            ).hexdigest(),
                        }
                    except Exception as pdf_exc:
                        row["pdf_exception_type"] = type(pdf_exc).__name__
                        row["pdf_exception"] = str(pdf_exc)
                _write_json(record_path, {"metrics": row, "report": data})
                rows.append(row)
                reused_case_count += 1
                print(
                    f"[{index}/{len(call_specs)}] {case['label']} · "
                    "existing JSON reused; PDF republished",
                    flush=True,
                )
                continue
        print(
            f"[{index}/{len(call_specs)}] {case['label']} · repeat {repeat_index}",
            flush=True,
        )
        db = SessionLocal()
        case_started = time.perf_counter()
        data: dict[str, Any] | None = None
        try:
            service = SingleReportService(CommercialAreaRepository(db))
            data = generate_unwrapped(service, case)
            elapsed_sec = time.perf_counter() - case_started
            row = _metrics(case_record, data, elapsed_sec)
            row["estimated_cost_usd"] = calculate_token_cost(
                str(row.get("model") or "gpt-5.4-mini"),
                int(row["input_tokens"]),
                int(row["output_tokens"]),
            )
            if publish_pdf:
                try:
                    safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "_", label).strip("._") or "run"
                    artifacts = publish_report_artifacts(f"{safe_label}/{artifact_id}", data)
                    pdf_path = Path(str(artifacts["pdf_path"]))
                    row["pdf_artifact"] = {
                        **artifacts,
                        "pdf_bytes": pdf_path.stat().st_size,
                        "pdf_sha256": hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
                    }
                except Exception as pdf_exc:
                    row["pdf_exception_type"] = type(pdf_exc).__name__
                    row["pdf_exception"] = str(pdf_exc)
            _write_json(record_path, {"metrics": row, "report": data})
        except Exception as exc:
            elapsed_sec = time.perf_counter() - case_started
            row = {
                "case": case_record,
                "elapsed_sec": round(elapsed_sec, 3),
                "exception_type": type(exc).__name__,
                "exception": str(exc),
            }
            _write_json(record_path, {"metrics": row, "report": data})
        finally:
            db.close()
        rows.append(row)
        print(
            f"  mode={row.get('generation_mode', '-')} "
            f"hard={row.get('final_issue_count', '-')} "
            f"warnings={row.get('warning_count', '-')} "
            f"tokens={row.get('total_tokens', '-')} "
            f"elapsed={row.get('elapsed_sec')}s",
            flush=True,
        )

    issue_counts = Counter(code for row in rows for code in row.get("original_issue_codes", []))
    warning_counts = Counter(code for row in rows for code in row.get("warning_codes", []))
    summary = {
        "label": label,
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_db": str(source_db),
        "eval_db": str(eval_db),
        "case_manifest": str(case_manifest) if case_manifest else None,
        "case_manifest_sha256": (
            hashlib.sha256(case_manifest.read_bytes()).hexdigest()
            if case_manifest
            else None
        ),
        "model": next((row.get("model") for row in rows if row.get("model")), None),
        "reasoning_effort": effective_reasoning_effort,
        "repeats": repeats,
        "selected_case_count": len(selected_cases),
        "expected_call_count": len(call_specs),
        "case_count": len(rows),
        "reused_case_count": reused_case_count,
        "success_count": sum("exception" not in row for row in rows),
        "pdf_count": sum(bool(row.get("pdf_artifact")) for row in rows),
        "clean_final_count": sum(row.get("final_issue_count") == 0 for row in rows),
        "generation_modes": dict(Counter(str(row.get("generation_mode")) for row in rows if row.get("generation_mode"))),
        "original_issue_codes": dict(issue_counts),
        "warning_codes": dict(warning_counts),
        "fallback_field_count": sum(len(row.get("fallback_fields") or []) for row in rows),
        "total_tokens": sum(int(row.get("total_tokens") or 0) for row in rows),
        "estimated_cost_usd": round(sum(float(row.get("estimated_cost_usd") or 0) for row in rows), 6),
        "elapsed_sec": round(sum(float(row.get("elapsed_sec") or 0) for row in rows), 3),
        "rows": rows,
    }
    _write_json(stage_root / "summary.json", summary)
    print(_json_text(summary))
    calls_succeeded = summary["success_count"] == len(call_specs)
    pdfs_succeeded = not publish_pdf or summary["pdf_count"] == len(call_specs)
    return 0 if calls_succeeded and pdfs_succeeded else 1


def compare(run_root: Path, before: str, after: str) -> int:
    before_summary = json.loads((run_root / before / "summary.json").read_text(encoding="utf-8"))
    after_summary = json.loads((run_root / after / "summary.json").read_text(encoding="utf-8"))
    before_rows = {row["case"]["id"]: row for row in before_summary["rows"]}
    after_rows = {row["case"]["id"]: row for row in after_summary["rows"]}
    pairs = []
    for case in CASES:
        old = before_rows.get(case["id"], {})
        new = after_rows.get(case["id"], {})
        pairs.append(
            {
                "case": case,
                "before": old,
                "after": new,
                "delta": {
                    "original_issue_count": int(new.get("original_issue_count") or 0)
                    - int(old.get("original_issue_count") or 0),
                    "final_issue_count": int(new.get("final_issue_count") or 0)
                    - int(old.get("final_issue_count") or 0),
                    "warning_count": int(new.get("warning_count") or 0) - int(old.get("warning_count") or 0),
                    "fallback_fields": len(new.get("fallback_fields") or []) - len(old.get("fallback_fields") or []),
                    "total_tokens": int(new.get("total_tokens") or 0) - int(old.get("total_tokens") or 0),
                    "narrative_chars": int(new.get("narrative_chars") or 0) - int(old.get("narrative_chars") or 0),
                },
            }
        )
    result = {
        "before": before_summary,
        "after": after_summary,
        "pairs": pairs,
    }
    _write_json(run_root / f"compare-{before}-vs-{after}.json", result)
    print(_json_text(result))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate isolated, cache-free detailed-report A/B corpora.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sample_parser = subparsers.add_parser("sample")
    sample_parser.add_argument("--source-db", type=Path, default=DEFAULT_SOURCE_DB)
    sample_parser.add_argument("--output", type=Path, required=True)
    sample_parser.add_argument("--count", type=int, default=15)
    sample_parser.add_argument("--seed", type=int, default=20260723)
    sample_parser.add_argument("--coverage", default="full_4axis")
    sample_parser.add_argument(
        "--budget",
        dest="budgets",
        type=int,
        action="append",
        help="Candidate budget in 만원; repeat to provide a fixed candidate set.",
    )

    generate_parser = subparsers.add_parser("generate")
    generate_parser.add_argument("--label", required=True)
    generate_parser.add_argument("--run-root", type=Path, default=DEFAULT_RUN_ROOT)
    generate_parser.add_argument("--source-db", type=Path, default=DEFAULT_SOURCE_DB)
    generate_parser.add_argument(
        "--reasoning-effort",
        choices=["none", "low", "medium", "high", "xhigh"],
        default="medium",
        help="Set the report reasoning effort explicitly inside the isolated runtime.",
    )
    generate_parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="Number of independent cache-free generations per selected case.",
    )
    generate_parser.add_argument(
        "--publish-pdf",
        action="store_true",
        help="Publish one PDF from each generated report without another model call.",
    )
    generate_parser.add_argument(
        "--pdf-root",
        type=Path,
        default=None,
        help="Optional final PDF root; defaults to the isolated runtime report root.",
    )
    generate_parser.add_argument(
        "--resume-existing",
        action="store_true",
        help=(
            "Reuse successful case JSON files already present in the stage and "
            "republish their PDFs without another model call."
        ),
    )
    generate_parser.add_argument(
        "--cases-manifest",
        type=Path,
        default=None,
        help="JSON manifest with a cases array. This freezes random samples across reruns.",
    )
    generate_parser.add_argument(
        "--case",
        dest="case_ids",
        action="append",
        help="Run only the selected case id; repeat to select multiple cases.",
    )

    compare_parser = subparsers.add_parser("compare")
    compare_parser.add_argument("--run-root", type=Path, default=DEFAULT_RUN_ROOT)
    compare_parser.add_argument("--before", default="baseline")
    compare_parser.add_argument("--after", default="candidate")

    args = parser.parse_args()
    if args.command == "sample":
        return sample_cases(
            source_db=args.source_db.resolve(),
            output=args.output.resolve(),
            count=args.count,
            seed=args.seed,
            coverage=args.coverage,
            budgets=args.budgets
            or [3000, 5000, 7000, 10000, 15000, 20000, 30000, 50000],
        )
    if args.command == "generate":
        case_manifest = args.cases_manifest.resolve() if args.cases_manifest else None
        return generate(
            args.label,
            args.run_root.resolve(),
            args.source_db.resolve(),
            args.case_ids,
            cases=_load_case_manifest(case_manifest) if case_manifest else None,
            case_manifest=case_manifest,
            reasoning_effort=args.reasoning_effort,
            repeats=args.repeat,
            publish_pdf=args.publish_pdf,
            pdf_root=args.pdf_root.resolve() if args.pdf_root else None,
            resume_existing=args.resume_existing,
        )
    return compare(args.run_root.resolve(), args.before, args.after)


if __name__ == "__main__":
    raise SystemExit(main())
