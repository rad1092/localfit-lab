from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from pypdf import PdfReader


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


EXPECTED_CASES: dict[str, dict[str, Any]] = {
    "yangjae_chicken_a": {
        "area_name": "양재역",
        "area_code": "3120179",
        "industry_code": "CS100007",
        "industry_name": "치킨전문점",
        "budget": 50000,
        "grade": "A+",
        "coverage": "full_4axis",
    },
    "geumnam_chicken_e": {
        "area_name": "금남시장",
        "area_code": "3130064",
        "industry_code": "CS100007",
        "industry_name": "치킨전문점",
        "budget": 50000,
        "grade": "E",
        "coverage": "full_4axis",
    },
}
EXPECTED_EFFORTS = ("none", "medium")
EXPECTED_REPEATS = (1, 2)
EXPECTED_MODEL = "gpt-5.4-mini"
ALLOWED_MODES = {"llm", "partial_fallback", "deterministic"}

SIMPLE_NARRATIVE_FIELDS = (
    "narrative_title",
    "thesis",
    "executive_interpretation",
    "score_interpretation",
    "trend_analysis",
    "user_fit",
    "action_plan",
    "onsite_checklist",
    "summary",
    "strengths",
    "weaknesses",
    "risk_factors",
)

PUBLIC_BANNED_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("internal_chart_id", re.compile(r"(?<![A-Za-z0-9])C[1-5](?![A-Za-z0-9])")),
    (
        "numbered_evidence_badge",
        re.compile(
            r"\[\s*근거\s*[:#-]?\s*[1-9]\d*\s*\]|"
            r"(?<!\w)근거\s*[:#-]?\s*[1-9]\d*"
            r"(?![\d,.]|\s*(?:천|만)?(?:억원|만원|개월|분기|원|억|개|명|건|분|년|월|일|%|㎡))"
        ),
    ),
    ("news_marker", re.compile(r"\[NEWS:\d+\]", re.IGNORECASE)),
    ("experience_placeholder", re.compile(r"experience_level|경험\s*미입력", re.IGNORECASE)),
    ("private_trace_field", re.compile(r"claim_source_map|field_path|supporting_evidence")),
    ("raw_chart_marker", re.compile(r"\[CHART:C[1-5]\]")),
    ("numeric_internal_score", re.compile(r"\b\d{1,3}(?:\.\d+)?\s*점\b")),
)

NARRATIVE_BANNED_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("internal_chart_id", PUBLIC_BANNED_PATTERNS[0][1]),
    ("numbered_evidence_badge", PUBLIC_BANNED_PATTERNS[1][1]),
    ("news_marker", PUBLIC_BANNED_PATTERNS[2][1]),
    ("experience_placeholder", PUBLIC_BANNED_PATTERNS[3][1]),
)

OVERCLAIM_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("success_guarantee", re.compile(r"무조건\s*(?:성공|유리)|성공을\s*보장|매출을?\s*보장|반드시\s*성공")),
    ("causal_certainty", re.compile(r"(?:때문에|덕분에)\s*(?:매출|수익|성공)이?\s*(?:증가|상승|보장)")),
)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _flatten_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "\n".join(_flatten_text(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return "\n".join(_flatten_text(item) for item in value)
    return str(value)


def _narrative_text(report: dict[str, Any]) -> str:
    chunks = [_flatten_text(report.get(field)) for field in SIMPLE_NARRATIVE_FIELDS]
    for axis in report.get("axis_interpretations") or []:
        if isinstance(axis, dict):
            chunks.extend(
                _flatten_text(axis.get(field))
                for field in ("axis", "meaning", "risk", "action", "next_check", "evidence", "evidence_metrics")
            )
    for alternative in report.get("alternatives") or []:
        if isinstance(alternative, dict):
            chunks.extend(
                _flatten_text(alternative.get(field))
                for field in ("area_name", "differential", "judgement")
            )
    return "\n".join(chunk for chunk in chunks if chunk).strip()


def _pattern_hits(text: str, patterns: tuple[tuple[str, re.Pattern[str]], ...]) -> list[dict[str, str]]:
    hits: list[dict[str, str]] = []
    for code, pattern in patterns:
        for match in pattern.finditer(text):
            start = max(0, match.start() - 35)
            end = min(len(text), match.end() + 35)
            hits.append({"code": code, "match": match.group(0), "context": text[start:end].replace("\n", " ")})
    return hits


def _overclaim_hits(text: str) -> list[dict[str, str]]:
    hits: list[dict[str, str]] = []
    for code, pattern in OVERCLAIM_PATTERNS:
        for match in pattern.finditer(text):
            tail = text[match.end() : match.end() + 55]
            if re.search(r"(?:하지\s*않|아니|아닙|아닌|보류|단정할\s*수\s*없|보장하지\s*않)", tail):
                continue
            start = max(0, match.start() - 35)
            end = min(len(text), match.end() + 55)
            hits.append({"code": code, "match": match.group(0), "context": text[start:end].replace("\n", " ")})
    return hits


def _pdf_text_and_annotations(path: Path) -> tuple[str, int, int]:
    reader = PdfReader(str(path))
    texts: list[str] = []
    annotation_count = 0
    for page in reader.pages:
        texts.append(page.extract_text() or "")
        annotations = page.get("/Annots")
        if annotations:
            annotation_count += len(annotations)
    return "\n\n".join(texts), len(reader.pages), annotation_count


def _claim_map_issues(report: dict[str, Any], mapping: Any | None = None) -> list[str]:
    mapping = report.get("claim_source_map") if mapping is None else mapping
    if not isinstance(mapping, list) or not mapping:
        return ["claim_source_map missing or empty"]
    issues: list[str] = []
    paths: set[str] = set()
    for index, item in enumerate(mapping):
        if not isinstance(item, dict):
            issues.append(f"claim_source_map[{index}] is not an object")
            continue
        field_path = str(item.get("field_path") or "").strip()
        if not field_path:
            issues.append(f"claim_source_map[{index}] field_path missing")
        elif field_path in paths:
            issues.append(f"duplicate claim_source_map field_path: {field_path}")
        paths.add(field_path)
        if not item.get("sources"):
            issues.append(f"claim_source_map[{index}] sources missing")
        if not str(item.get("limitation") or "").strip():
            issues.append(f"claim_source_map[{index}] limitation missing")
        if item.get("public_inline_marker") is not False:
            issues.append(f"claim_source_map[{index}] public_inline_marker must be false")
    for required in ("header_block", "trend_analysis", "user_fit"):
        if required not in paths:
            issues.append(f"required mapping missing: {required}")
    for news_index, _ in enumerate(report.get("news_evidence") or []):
        required = f"news_evidence[{news_index}].decision_summary"
        if required not in paths:
            issues.append(f"required mapping missing: {required}")
    return issues


def _current_revalidation(report: dict[str, Any], expected: dict[str, Any]) -> tuple[list[str], list[str]]:
    from app.services.interpretive_report import _is_advisory_issue
    from app.services.report_critic import validate_report_draft

    issues = validate_report_draft(
        report,
        facts_pack_display=report.get("facts_pack_display") or {},
        user_condition={
            "area_name": expected["area_name"],
            "business_type": expected["industry_name"],
            "budget": expected["budget"],
        },
        evidence_frames=report.get("evidence_frames") or [],
        markdown_body=str(report.get("markdown_body") or ""),
    )
    return (
        [issue for issue in issues if not _is_advisory_issue(issue)],
        [issue for issue in issues if _is_advisory_issue(issue)],
    )


def _check_record(
    json_path: Path,
    expected_effort: str,
    expected_case_id: str,
    expected_repeat: int,
) -> dict[str, Any]:
    payload = _read_json(json_path)
    metrics = payload.get("metrics") or {}
    report = payload.get("report") or {}
    case = metrics.get("case") or {}
    expected = EXPECTED_CASES[expected_case_id]
    checks: dict[str, bool] = {}
    notes: list[str] = []

    checks["identity"] = (
        case.get("id") == expected_case_id
        and int(case.get("repeat") or 0) == expected_repeat
        and str(case.get("artifact_id") or "") == f"{expected_case_id}_r{expected_repeat}"
    )
    checks["controlled_input"] = (
        str(case.get("area_code")) == expected["area_code"]
        and str(case.get("industry_code")) == expected["industry_code"]
        and int(case.get("budget") or 0) == expected["budget"]
        and case.get("coverage") == expected["coverage"]
    )
    checks["model_and_effort"] = metrics.get("model") == EXPECTED_MODEL and metrics.get("reasoning_effort") == expected_effort
    checks["no_generation_exception"] = "exception" not in metrics
    recorded_validation_issues = list(report.get("validation_issues") or [])
    current_hard_issues, current_warnings = _current_revalidation(report, expected)
    checks["current_hard_validation_clean"] = not current_hard_issues
    checks["provenance_present"] = (
        isinstance(report.get("original_validation_issues"), list)
        and isinstance(report.get("section_repair_log"), list)
        and isinstance(report.get("fallback_fields"), list)
        and report.get("generation_mode") in ALLOWED_MODES
        and metrics.get("generation_mode") == report.get("generation_mode")
    )
    mode = report.get("generation_mode")
    fallback_fields = list(report.get("fallback_fields") or [])
    cache_meta = report.get("cache_meta") or {}
    token_usage = report.get("token_usage") or {}
    checks["generation_mode_consistency"] = (
        (mode == "llm" and report.get("ai_generated") is True and not fallback_fields)
        or (mode == "partial_fallback" and report.get("ai_generated") is True and bool(fallback_fields))
        or (
            mode == "deterministic"
            and report.get("ai_generated") is False
            and cache_meta.get("cacheable") is False
        )
    )
    checks["cache_and_usage_isolation"] = (
        token_usage.get("estimated") is False
        and token_usage.get("cache_hit") is False
        and cache_meta.get("cache_hit") is False
        and token_usage.get("model") == EXPECTED_MODEL
        and token_usage.get("reasoning_effort") == expected_effort
        and cache_meta.get("spec_version") == token_usage.get("spec_version")
        and bool(token_usage.get("spec_version"))
    )

    header = report.get("header_block") or {}
    header_grade = header.get("display_grade") or header.get("grade")
    checks["header_grade"] = header_grade == expected["grade"]
    narrative = _narrative_text(report)
    narrative_hits = _pattern_hits(narrative, NARRATIVE_BANNED_PATTERNS)
    overclaim_hits = _overclaim_hits(narrative)
    checks["narrative_private_labels_absent"] = not narrative_hits
    checks["obvious_guarantees_absent"] = not overclaim_hits

    serialized_report = json.dumps(report, ensure_ascii=False)
    experience_hits = _pattern_hits(serialized_report, (("experience_placeholder", PUBLIC_BANNED_PATTERNS[3][1]),))
    checks["experience_placeholder_absent"] = not experience_hits
    from app.services.interpretive_report import _build_claim_source_map

    recorded_claim_map_issues = _claim_map_issues(report)
    current_claim_map = _build_claim_source_map(report)
    current_claim_map_issues = _claim_map_issues(report, current_claim_map)
    checks["current_claim_source_map_valid"] = not current_claim_map_issues

    pdf_info = metrics.get("pdf_artifact") or {}
    pdf_path = Path(str(pdf_info.get("pdf_path") or ""))
    pdf_text = ""
    page_count = 0
    annotation_count = -1
    pdf_error = ""
    if pdf_path.is_file():
        try:
            pdf_text, page_count, annotation_count = _pdf_text_and_annotations(pdf_path)
        except Exception as exc:  # keep failed extraction visible in the audit artifact
            pdf_error = f"{type(exc).__name__}: {exc}"
    else:
        pdf_error = f"missing PDF: {pdf_path}"
    public_hits = _pattern_hits(pdf_text, PUBLIC_BANNED_PATTERNS) if pdf_text else []
    actual_pdf_sha256 = hashlib.sha256(pdf_path.read_bytes()).hexdigest() if pdf_path.is_file() else None
    checks["pdf_exists_and_readable"] = bool(pdf_text) and page_count > 0 and not pdf_error
    checks["pdf_hash_matches_generation_record"] = bool(actual_pdf_sha256) and actual_pdf_sha256 == pdf_info.get("pdf_sha256")
    checks["pdf_has_no_links_or_annotations"] = annotation_count == 0
    checks["pdf_private_labels_absent"] = not public_hits
    checks["pdf_identity_visible"] = expected["area_name"] in pdf_text and expected["industry_name"] in pdf_text
    checks["pdf_grade_visible"] = expected["grade"] in pdf_text
    checks["pdf_budget_visible"] = any(value in pdf_text for value in ("5억원", "5억 원", "50,000만원", "50,000만 원"))

    if pdf_error:
        notes.append(pdf_error)
    if recorded_validation_issues != current_hard_issues:
        notes.append(
            "recorded/current validator difference: "
            f"recorded={recorded_validation_issues!r}, current={current_hard_issues!r}"
        )
    notes.extend(f"recorded claim map: {issue}" for issue in recorded_claim_map_issues)
    notes.extend(f"current claim map: {issue}" for issue in current_claim_map_issues)
    notes.extend(f"current validator: {issue}" for issue in current_hard_issues)
    for hit in narrative_hits:
        notes.append(f"narrative {hit['code']}: {hit['context']}")
    for hit in public_hits:
        notes.append(f"pdf {hit['code']}: {hit['context']}")
    for hit in overclaim_hits:
        notes.append(f"overclaim {hit['code']}: {hit['context']}")
    if experience_hits:
        notes.append("experience placeholder remains in serialized report")

    if pdf_text:
        extracted_path = json_path.parents[1] / "audit" / "extracted_text" / f"{expected_effort}_{expected_case_id}_r{expected_repeat}.txt"
        extracted_path.parent.mkdir(parents=True, exist_ok=True)
        extracted_path.write_text(pdf_text + "\n", encoding="utf-8")

    return {
        "artifact": f"{expected_effort}/{expected_case_id}_r{expected_repeat}",
        "json_path": str(json_path),
        "pdf_path": str(pdf_path),
        "pdf_sha256": actual_pdf_sha256,
        "page_count": page_count,
        "pdf_text_chars": len(pdf_text),
        "annotation_count": annotation_count,
        "generation_mode": report.get("generation_mode"),
        "ai_generated": report.get("ai_generated"),
        "recorded_final_issue_count": len(recorded_validation_issues),
        "recorded_validation_issues": recorded_validation_issues,
        "current_final_issue_count": len(current_hard_issues),
        "current_validation_issues": current_hard_issues,
        "current_quality_warnings": current_warnings,
        "recorded_claim_map_issues": recorded_claim_map_issues,
        "current_claim_map_issues": current_claim_map_issues,
        "original_issue_count": len(report.get("original_validation_issues") or []),
        "original_validation_issues": report.get("original_validation_issues") or [],
        "fallback_fields": report.get("fallback_fields") or [],
        "repair_log": report.get("section_repair_log") or [],
        "warning_count": len(report.get("quality_warnings") or []),
        "warnings": report.get("quality_warnings") or [],
        "input_tokens": int((report.get("token_usage") or {}).get("input_tokens") or metrics.get("input_tokens") or 0),
        "output_tokens": int((report.get("token_usage") or {}).get("output_tokens") or metrics.get("output_tokens") or 0),
        "total_tokens": int((report.get("token_usage") or {}).get("total_tokens") or metrics.get("total_tokens") or 0),
        "elapsed_sec": float(metrics.get("elapsed_sec") or 0),
        "narrative_chars": len(narrative),
        "narrative_sha256": hashlib.sha256(narrative.encode("utf-8")).hexdigest(),
        "controlled_payload_sha256": hashlib.sha256(
            json.dumps(
                {
                    key: report.get(key)
                    for key in (
                        "facts_pack_display",
                        "facts_lite_display",
                        "indicator_pack",
                        "evidence_frames",
                        "visualization_data",
                        "chart_manifest",
                    )
                },
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest(),
        "news_evidence_sha256": hashlib.sha256(
            json.dumps(report.get("news_evidence") or [], ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest(),
        "checks": checks,
        "failed_checks": [name for name, passed in checks.items() if not passed],
        "notes": notes,
    }


def _similarity_rows(run_root: Path) -> list[dict[str, Any]]:
    records: dict[tuple[str, str, int], str] = {}
    for effort in EXPECTED_EFFORTS:
        for case_id in EXPECTED_CASES:
            for repeat in EXPECTED_REPEATS:
                path = run_root / effort / f"{case_id}_r{repeat}.json"
                if path.is_file():
                    records[(effort, case_id, repeat)] = _narrative_text((_read_json(path).get("report") or {}))
    rows: list[dict[str, Any]] = []
    for effort in EXPECTED_EFFORTS:
        for case_id in EXPECTED_CASES:
            left = records.get((effort, case_id, 1), "")
            right = records.get((effort, case_id, 2), "")
            rows.append(
                {
                    "comparison": "repeat_stability",
                    "effort": effort,
                    "case_id": case_id,
                    "left": f"{effort}/{case_id}_r1",
                    "right": f"{effort}/{case_id}_r2",
                    "sequence_similarity": round(SequenceMatcher(None, left, right).ratio(), 4) if left and right else None,
                }
            )
    for case_id in EXPECTED_CASES:
        for repeat in EXPECTED_REPEATS:
            left = records.get(("none", case_id, repeat), "")
            right = records.get(("medium", case_id, repeat), "")
            rows.append(
                {
                    "comparison": "effort_difference",
                    "effort": "none_vs_medium",
                    "case_id": case_id,
                    "left": f"none/{case_id}_r{repeat}",
                    "right": f"medium/{case_id}_r{repeat}",
                    "sequence_similarity": round(SequenceMatcher(None, left, right).ratio(), 4) if left and right else None,
                }
            )
    return rows


def audit(run_root: Path, pdf_root: Path) -> int:
    expected_artifacts = {
        f"{effort}/{case_id}_r{repeat}"
        for effort in EXPECTED_EFFORTS
        for case_id in EXPECTED_CASES
        for repeat in EXPECTED_REPEATS
    }
    present_jsons = {
        f"{path.parent.name}/{path.stem}"
        for effort in EXPECTED_EFFORTS
        for path in (run_root / effort).glob("*.json")
        if path.name != "summary.json"
    }
    rows: list[dict[str, Any]] = []
    for effort in EXPECTED_EFFORTS:
        for case_id in EXPECTED_CASES:
            for repeat in EXPECTED_REPEATS:
                json_path = run_root / effort / f"{case_id}_r{repeat}.json"
                if json_path.is_file():
                    rows.append(_check_record(json_path, effort, case_id, repeat))
                else:
                    rows.append(
                        {
                            "artifact": f"{effort}/{case_id}_r{repeat}",
                            "json_path": str(json_path),
                            "failed_checks": ["json_exists"],
                            "checks": {"json_exists": False},
                            "notes": ["expected JSON missing"],
                        }
                    )

    pdf_paths = sorted(pdf_root.rglob("report.pdf")) if pdf_root.is_dir() else []
    summaries: dict[str, Any] = {}
    summary_issues: list[str] = []
    for effort in EXPECTED_EFFORTS:
        path = run_root / effort / "summary.json"
        if not path.is_file():
            summary_issues.append(f"missing summary: {effort}")
            continue
        summary = _read_json(path)
        summaries[effort] = summary
        for key, expected in (("expected_call_count", 4), ("case_count", 4), ("success_count", 4), ("pdf_count", 4)):
            if int(summary.get(key) or 0) != expected:
                summary_issues.append(f"{effort} {key}={summary.get(key)!r}, expected={expected}")
        if summary.get("reasoning_effort") != effort:
            summary_issues.append(f"{effort} reasoning_effort={summary.get('reasoning_effort')!r}")

    controlled_inputs_consistent = all(
        len(
            {
                row.get("controlled_payload_sha256")
                for row in rows
                if f"/{case_id}_" in str(row.get("artifact") or "")
            }
        )
        == 1
        for case_id in EXPECTED_CASES
    )
    frozen_news_consistent = all(
        len(
            {
                row.get("news_evidence_sha256")
                for row in rows
                if f"/{case_id}_" in str(row.get("artifact") or "")
            }
        )
        == 1
        for case_id in EXPECTED_CASES
    )
    aggregate_checks = {
        "exact_json_matrix": present_jsons == expected_artifacts,
        "exactly_eight_pdfs": len(pdf_paths) == 8,
        "summaries_match_protocol": not summary_issues,
        "controlled_inputs_match_within_case": controlled_inputs_consistent,
        "frozen_news_matches_within_case": frozen_news_consistent,
        "all_artifact_checks_pass": all(not row.get("failed_checks") for row in rows),
    }
    generation_modes = Counter(str(row.get("generation_mode")) for row in rows if row.get("generation_mode"))
    original_codes = Counter()
    for row in rows:
        for issue in row.get("original_validation_issues") or []:
            match = re.match(r"^\[([A-Z_]+)\]", str(issue))
            original_codes[match.group(1) if match else "UNKNOWN"] += 1
    by_effort: dict[str, dict[str, Any]] = {}
    for effort in EXPECTED_EFFORTS:
        effort_rows = [row for row in rows if str(row.get("artifact", "")).startswith(f"{effort}/")]
        by_effort[effort] = {
            "artifacts": len(effort_rows),
            "clean_artifacts": sum(not row.get("failed_checks") for row in effort_rows),
            "recorded_validator_clean": sum(int(row.get("recorded_final_issue_count") or 0) == 0 for row in effort_rows),
            "current_validator_clean": sum(int(row.get("current_final_issue_count") or 0) == 0 for row in effort_rows),
            "modes": dict(Counter(str(row.get("generation_mode")) for row in effort_rows if row.get("generation_mode"))),
            "original_issue_count": sum(int(row.get("original_issue_count") or 0) for row in effort_rows),
            "fallback_field_count": sum(len(row.get("fallback_fields") or []) for row in effort_rows),
            "warning_count": sum(int(row.get("warning_count") or 0) for row in effort_rows),
            "total_tokens": sum(int(row.get("total_tokens") or 0) for row in effort_rows),
            "elapsed_sec": round(sum(float(row.get("elapsed_sec") or 0) for row in effort_rows), 3),
            "narrative_chars": sum(int(row.get("narrative_chars") or 0) for row in effort_rows),
        }
    result = {
        "protocol": {
            "model": EXPECTED_MODEL,
            "efforts": list(EXPECTED_EFFORTS),
            "case_ids": list(EXPECTED_CASES),
            "repeats": list(EXPECTED_REPEATS),
            "expected_artifacts": sorted(expected_artifacts),
        },
        "run_root": str(run_root),
        "pdf_root": str(pdf_root),
        "aggregate_checks": aggregate_checks,
        "summary_issues": summary_issues,
        "present_json_artifacts": sorted(present_jsons),
        "pdf_paths": [str(path) for path in pdf_paths],
        "generation_modes": dict(generation_modes),
        "original_issue_codes": dict(original_codes),
        "by_effort": by_effort,
        "similarities": _similarity_rows(run_root),
        "rows": rows,
    }

    audit_root = run_root / "audit"
    _write_json(audit_root / "automatic_audit.json", result)
    with (audit_root / "automatic_audit.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        fieldnames = [
            "artifact",
            "generation_mode",
            "original_issue_count",
            "fallback_field_count",
            "warning_count",
            "total_tokens",
            "elapsed_sec",
            "page_count",
            "pdf_text_chars",
            "annotation_count",
            "failed_checks",
            "pdf_path",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "artifact": row.get("artifact"),
                    "generation_mode": row.get("generation_mode"),
                    "original_issue_count": row.get("original_issue_count"),
                    "fallback_field_count": len(row.get("fallback_fields") or []),
                    "warning_count": row.get("warning_count"),
                    "total_tokens": row.get("total_tokens"),
                    "elapsed_sec": row.get("elapsed_sec"),
                    "page_count": row.get("page_count"),
                    "pdf_text_chars": row.get("pdf_text_chars"),
                    "annotation_count": row.get("annotation_count"),
                    "failed_checks": "; ".join(row.get("failed_checks") or []),
                    "pdf_path": row.get("pdf_path"),
                }
            )

    lines = [
        "# NONE 대 MEDIUM 8-PDF 자동 감사",
        "",
        f"- 전체 자동 판정: {'PASS' if all(aggregate_checks.values()) else 'FAIL'}",
        f"- JSON 행렬: {len(present_jsons)}/8",
        f"- PDF: {len(pdf_paths)}/8",
        f"- 생성 모드: {dict(generation_modes)}",
        f"- 최초 위반 코드: {dict(original_codes)}",
        "",
        "## 추론 강도별 집계",
        "",
        "| 강도 | 산출물 | 자동 무결점 | 당시 검증 통과 | 현재 검증 통과 | 최초 위반 | fallback 필드 | 경고 | 토큰 | 소요 초 | 서사 글자 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for effort in EXPECTED_EFFORTS:
        item = by_effort[effort]
        lines.append(
            f"| {effort} | {item['artifacts']} | {item['clean_artifacts']} | {item['recorded_validator_clean']} | "
            f"{item['current_validator_clean']} | {item['original_issue_count']} | "
            f"{item['fallback_field_count']} | {item['warning_count']} | {item['total_tokens']} | "
            f"{item['elapsed_sec']} | {item['narrative_chars']} |"
        )
    lines.extend(
        [
            "",
            "## 아티팩트별 판정",
            "",
            "| 아티팩트 | 모드 | 최초 위반 | fallback | PDF 쪽 | 주석/링크 | 결과 |",
            "|---|---|---:|---:|---:|---:|---|",
        ]
    )
    for row in rows:
        lines.append(
            f"| {row.get('artifact')} | {row.get('generation_mode', '-')} | {row.get('original_issue_count', '-')} | "
            f"{len(row.get('fallback_fields') or [])} | {row.get('page_count', '-')} | {row.get('annotation_count', '-')} | "
            f"{'PASS' if not row.get('failed_checks') else 'FAIL: ' + ', '.join(row.get('failed_checks') or [])} |"
        )
    if summary_issues:
        lines.extend(["", "## 실행 요약 문제", "", *[f"- {item}" for item in summary_issues]])
    lines.extend(
        [
            "",
            "## 수동 탐지자 검토 항목",
            "",
            "자동 PASS는 내용 품질의 충분조건이 아니다. 각 PDF를 다음 항목으로 별도 판정한다.",
            "",
            "1. 수치·등급·기간·방향이 같은 판단 영역의 근거와 일치하는가",
            "2. 관측 상관을 원인이나 미래 보장으로 과장하지 않는가",
            "3. A와 E 입지의 서사가 실제 데이터 차이를 의미 있게 구분하는가",
            "4. 예산 5억원을 공식 적합 판정처럼 오해시키지 않는가",
            "5. 실행 항목과 현장 체크가 중복 문구가 아니라 판단에 도움이 되는가",
            "6. 한국어 조사·호응·문장 연결이 자연스러운가",
            "7. 출처 표와 내부 claim-source 매핑이 감사 추적에 충분한가",
            "8. 전 페이지 표·차트·본문이 잘림, 겹침, 깨짐 없이 보이는가",
        ]
    )
    (audit_root / "automatic_audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"aggregate_checks": aggregate_checks, "by_effort": by_effort}, ensure_ascii=False, indent=2))
    return 0 if all(aggregate_checks.values()) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit the fixed eight-report NONE/MEDIUM experiment without model calls.")
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--pdf-root", type=Path, required=True)
    args = parser.parse_args()
    return audit(args.run_root.resolve(), args.pdf_root.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
