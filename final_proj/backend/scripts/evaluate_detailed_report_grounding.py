from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import math
import re
import shutil
import sqlite3
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from PIL import Image
from pypdf import PdfReader


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


SCRIPT_PATH = Path(__file__).resolve()
BACKEND_ROOT = SCRIPT_PATH.parents[1]
FINAL_PROJ_ROOT = SCRIPT_PATH.parents[2]
WORKSPACE_ROOT = SCRIPT_PATH.parents[3]

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.korean import josa  # noqa: E402
from app.services.report_critic import validate_report_draft  # noqa: E402
from app.services.report_publisher import PUBLIC_PRESENTATION_VERSION  # noqa: E402


DEFAULT_DB = FINAL_PROJ_ROOT / "runtime" / "db" / "commercial.db"
DEFAULT_ARTIFACT_DIR = (
    FINAL_PROJ_ROOT / "runtime" / "reports" / "export_20260723132208051894"
)
DEFAULT_OUTPUT_DIR = (
    FINAL_PROJ_ROOT
    / "runtime"
    / "evaluations"
    / "detailed-report-grounding-20260723"
)
DEFAULT_HISTORICAL_ROOT = (
    FINAL_PROJ_ROOT
    / "runtime"
    / "evaluations"
    / "report-paper-audit-20260722"
)
DEFAULT_GOLD_MANIFEST = (
    WORKSPACE_ROOT / "datacorpus" / "_gold_validation" / "23_gold_output_manifest.csv"
)
DEFAULT_SCORE_MANIFEST = (
    WORKSPACE_ROOT
    / "datacorpus"
    / "_location_judgement_outputs"
    / "loc_score_v2_batch_20261_20260718_063148.manifest.json"
)
DEFAULT_SCORE_BATCH = (
    WORKSPACE_ROOT
    / "datacorpus"
    / "_location_judgement_outputs"
    / "loc_score_v2_batch_20261_20260718_063148.csv"
)
DEFAULT_WEIGHT_FILE = (
    WORKSPACE_ROOT
    / "datacorpus"
    / "_score_backtest"
    / "location_score_backtest_recommended_weights.csv"
)
DEFAULT_MANUAL_REVIEW = (
    FINAL_PROJ_ROOT
    / "docs"
    / "evaluation"
    / "detailed_report_grounding_20260723"
    / "manual_visual_review.json"
)

EVALUATOR_ID = "OpenAI gpt-5.6-sol"
PROTOCOL_VERSION = "detailed-report-grounding.v1.6.0-batch-contract-repair"

PERSISTENT_NEWS_SIGNALS = {"development", "transport"}
PERSISTENT_NEWS_TERMS = {
    "개발",
    "공사",
    "착공",
    "준공",
    "정비",
    "개선",
    "신설",
    "확장",
    "개통",
    "도로",
    "교통",
    "보행",
    "동선",
    "지하철",
    "정류장",
    "환승",
}
DECISION_USE_DOMAIN_TERMS = {
    "지원",
    "신청",
    "자부담",
    "공실",
    "임대",
    "개통",
    "교통",
    "보행",
    "개발",
    "공사",
    "점포",
    "상권",
    "수요",
    "영향",
    "운영",
    "도로",
    "지하철",
    "버스",
    "환승",
    "폐업",
    "위험",
}


SQL_QUERIES: dict[str, str] = {
    "target_area": """
SELECT area_code, area_name, district_code, latitude, longitude
FROM commercial_area
WHERE area_code = :area_code
""".strip(),
    "target_score": """
SELECT quarter, area_code, area_name, district_code, district_name,
       industry_code, industry_name, current_location_score,
       context_location_score, grade, decision_label,
       score_coverage_tier, available_axis_count,
       official_indicator_count, official_indicator_defined_count,
       official_indicator_complete, missing_axes, coverage_reason,
       taxonomy_direct_score_allowed, official_rank_eligible,
       cost_risk_score, data_reliability_score, conservative_score_owa,
       axis_sales, axis_competition, axis_demand, axis_accessibility,
       growth_potential_score, growth_rebound_candidate_score, score_version
FROM rule_location_score
WHERE quarter = :quarter
  AND area_code = :area_code
  AND industry_code = :industry_code
""".strip(),
    "score_rank": """
WITH pool AS (
    SELECT area_code, current_location_score,
           RANK() OVER (ORDER BY current_location_score DESC) AS score_rank,
           COUNT(*) OVER () AS candidate_count
    FROM rule_location_score
    WHERE quarter = :quarter
      AND industry_code = :industry_code
      AND score_coverage_tier = 'full_4axis'
      AND official_rank_eligible = 1
)
SELECT area_code, current_location_score, score_rank, candidate_count,
       ROUND(
           100.0 * (candidate_count - score_rank + 1) / candidate_count,
           6
       ) AS score_percentile
FROM pool
WHERE area_code = :area_code
""".strip(),
    "latest_sales": """
SELECT id, area_code, industry_code, industry_name, sales_amount, timestamp
FROM district_sales
WHERE area_code = :area_code
  AND industry_code = :industry_code
  AND timestamp = :quarter
""".strip(),
    "sales_history": """
SELECT timestamp, sales_amount
FROM district_sales
WHERE area_code = :area_code
  AND industry_code = :industry_code
ORDER BY timestamp DESC
LIMIT 8
""".strip(),
    "sales_seoul_rank": """
WITH pool AS (
    SELECT area_code, sales_amount,
           RANK() OVER (ORDER BY sales_amount DESC) AS sales_rank,
           COUNT(*) OVER () AS candidate_count
    FROM district_sales
    WHERE industry_code = :industry_code
      AND timestamp = :quarter
      AND sales_amount IS NOT NULL
)
SELECT area_code, sales_amount, sales_rank, candidate_count,
       ROUND(
           100.0 * (candidate_count - sales_rank + 1) / candidate_count,
           6
       ) AS sales_percentile
FROM pool
WHERE area_code = :area_code
""".strip(),
    "sales_area_rank": """
WITH pool AS (
    SELECT area_code, industry_code, industry_name, sales_amount,
           RANK() OVER (ORDER BY sales_amount DESC) AS area_industry_rank,
           COUNT(*) OVER () AS industry_count
    FROM district_sales
    WHERE area_code = :area_code
      AND timestamp = :quarter
      AND sales_amount IS NOT NULL
)
SELECT area_code, industry_code, industry_name, sales_amount,
       area_industry_rank, industry_count
FROM pool
WHERE industry_code = :industry_code
""".strip(),
    "latest_store": """
SELECT id, area_code, industry_code, industry_name, store_count, timestamp
FROM district_store_count
WHERE area_code = :area_code
  AND industry_code = :industry_code
  AND timestamp = :quarter
""".strip(),
    "store_history": """
SELECT timestamp, store_count
FROM district_store_count
WHERE area_code = :area_code
  AND industry_code = :industry_code
ORDER BY timestamp DESC
LIMIT 8
""".strip(),
    "store_total": """
SELECT area_code, timestamp, SUM(store_count) AS total_store_count
FROM district_store_count
WHERE area_code = :area_code
  AND timestamp = :quarter
GROUP BY area_code, timestamp
""".strip(),
    "store_seoul_rank": """
WITH pool AS (
    SELECT area_code, store_count,
           RANK() OVER (ORDER BY store_count DESC) AS store_rank,
           COUNT(*) OVER () AS candidate_count
    FROM district_store_count
    WHERE industry_code = :industry_code
      AND timestamp = :quarter
      AND store_count IS NOT NULL
)
SELECT area_code, store_count, store_rank, candidate_count,
       ROUND(
           100.0 * (candidate_count - store_rank + 1) / candidate_count,
           6
       ) AS store_percentile
FROM pool
WHERE area_code = :area_code
""".strip(),
    "latest_population": """
SELECT id, area_code, district_name, resident_population,
       worker_population, timestamp
FROM district_population
WHERE area_code = :area_code
  AND timestamp = :quarter
""".strip(),
    "latest_floating": """
SELECT id, area_code, floating_population, timestamp
FROM district_floating
WHERE area_code = :area_code
  AND timestamp = :quarter
""".strip(),
    "cost_proxy": """
SELECT id, area_code, sale_price_proxy_manwon_per_m2, period,
       source_id, provider, grain, direct_score_allowed,
       proxy_score_allowed, provenance_note
FROM area_sale_price_proxy
WHERE area_code = :area_code
  AND period = :quarter
""".strip(),
    "rone_reference": """
WITH ranked AS (
    SELECT id, area_code, period, selection_group, metric_code, metric_name,
           metric_value, unit, property_type, source_region_name,
           mapping_scope, mapping_method, mapping_confidence,
           source_id, provider, direct_value_allowed,
           proxy_score_allowed, engine_promotion_ready,
           forbidden_claim_ko, provenance_note,
           ROW_NUMBER() OVER (
               PARTITION BY metric_code
               ORDER BY
                   CASE mapping_scope
                       WHEN 'rone_level3_name_match_candidate' THEN 0
                       WHEN 'seoul_baseline_reference' THEN 1
                       ELSE 2
                   END,
                   CASE property_type
                       WHEN '중대형 상가' THEN 0
                       WHEN '집합 상가' THEN 1
                       WHEN '소규모 상가' THEN 2
                       ELSE 3
                   END,
                   source_region_name ASC
           ) AS selection_rank
    FROM area_rone_cost_reference AS ref
    WHERE area_code = :area_code
      AND metric_code IN ('rent', 'vacancy')
      AND period = (
          SELECT MAX(candidate.period)
          FROM area_rone_cost_reference AS candidate
          WHERE candidate.area_code = ref.area_code
            AND candidate.metric_code = ref.metric_code
            AND candidate.period <= :quarter
      )
)
SELECT id, area_code, period, selection_group, metric_code, metric_name,
       metric_value, unit, property_type, source_region_name,
       mapping_scope, mapping_method, mapping_confidence,
       source_id, provider, direct_value_allowed,
       proxy_score_allowed, engine_promotion_ready,
       forbidden_claim_ko, provenance_note
FROM ranked
WHERE selection_rank = 1
ORDER BY metric_code
""".strip(),
    "alternative_scores": """
WITH pool AS (
    SELECT quarter, area_code, area_name, industry_code,
           current_location_score, grade, cost_risk_score,
           axis_sales, axis_competition, axis_demand, axis_accessibility,
           RANK() OVER (ORDER BY current_location_score DESC) AS score_rank,
           COUNT(*) OVER () AS candidate_count
    FROM rule_location_score
    WHERE quarter = :quarter
      AND industry_code = :industry_code
      AND score_coverage_tier = 'full_4axis'
      AND official_rank_eligible = 1
)
SELECT quarter, area_code, area_name, industry_code,
       current_location_score, grade,
       CASE
           WHEN grade = 'A'
            AND 100.0 * (candidate_count - score_rank + 1) / candidate_count > 90
           THEN 'A+'
           ELSE grade
       END AS display_grade,
       cost_risk_score, axis_sales, axis_competition,
       axis_demand, axis_accessibility, score_rank, candidate_count
FROM pool
WHERE area_code IN (
    SELECT CAST(value AS TEXT)
    FROM json_each(:alternative_area_codes_json)
)
ORDER BY score_rank, area_code
""".strip(),
    "table_counts": """
SELECT 'rule_location_score' AS table_name, COUNT(*) AS row_count
FROM rule_location_score
UNION ALL
SELECT 'district_sales', COUNT(*) FROM district_sales
UNION ALL
SELECT 'district_store_count', COUNT(*) FROM district_store_count
UNION ALL
SELECT 'district_population', COUNT(*) FROM district_population
UNION ALL
SELECT 'district_floating', COUNT(*) FROM district_floating
UNION ALL
SELECT 'area_sale_price_proxy', COUNT(*) FROM area_sale_price_proxy
UNION ALL
SELECT 'area_rone_cost_reference', COUNT(*) FROM area_rone_cost_reference
""".strip(),
}


def _sha256(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_identity(path: Path, *, hash_file: bool = True) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "exists": False}
    stat = path.stat()
    result = {
        "path": str(path),
        "exists": True,
        "bytes": stat.st_size,
        "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
    }
    if hash_file:
        result["sha256"] = _sha256(path)
    return result


PARENTHESIZED_NAME_PARTICLE_PATTERN = re.compile(
    r"(?P<name>[^\s|\n]+?\([^)|\n]+\))(?P<particle>은|는)(?=\s|[,.!?|])"
)


def _korean_particle_mismatches(markdown: str) -> list[dict[str, Any]]:
    mismatches: list[dict[str, Any]] = []
    for line_number, line in enumerate(markdown.splitlines(), start=1):
        for match in PARENTHESIZED_NAME_PARTICLE_PATTERN.finditer(line):
            name = match.group("name")
            actual = match.group("particle")
            expected = josa(name, "은는")
            if actual != expected:
                mismatches.append(
                    {
                        "line": line_number,
                        "text": line,
                        "name": name,
                        "actual_particle": actual,
                        "expected_particle": expected,
                    }
                )
    return mismatches


def _budget_caveat_found(markdown: str) -> bool:
    patterns = (
        r"(?:공식\s*)?(?:예산|비용)[^.!?\n]{0,80}(?:적합도)?[^.!?\n]{0,30}보류",
        r"예산[^.!?\n]{0,100}(?:충분|감당\s*가능|진입\s*가능)[^.!?\n]{0,30}"
        r"(?:말|볼|단정|판단)할\s*수(?:는)?\s*없",
        r"예산[^.!?\n]{0,80}(?:상한선?|검토\s*기준)(?:으로만)?",
    )
    return any(re.search(pattern, markdown) for pattern in patterns)


def _trend_direction_from_text(text: str) -> str:
    down_matches = list(
        re.finditer(r"내려왔|하락|감소|낮아졌|줄었|약해졌|둔화", str(text or ""))
    )
    up_matches = list(
        re.finditer(r"올라왔|상승|증가|높아졌|늘었|강해졌", str(text or ""))
    )
    if down_matches and not up_matches:
        return "down"
    if up_matches and not down_matches:
        return "up_or_flat"
    if down_matches and up_matches:
        return (
            "down"
            if down_matches[-1].start() > up_matches[-1].start()
            else "up_or_flat"
        )
    if re.search(r"유지|보합|비슷한\s*수준", str(text or "")):
        return "up_or_flat"
    return "other"


def _report_section_path(markdown: str, heading: str) -> str:
    lines = markdown.splitlines()
    start = next(
        (
            index
            for index, line in enumerate(lines)
            if line.strip() == f"## {heading}"
        ),
        None,
    )
    if start is None:
        return f"report.md:section-not-found({heading})"
    end = next(
        (
            index - 1
            for index in range(start + 1, len(lines))
            if lines[index].startswith("## ")
        ),
        len(lines) - 1,
    )
    while end > start and not lines[end].strip():
        end -= 1
    return f"report.md:{start + 1}-{end + 1}"


def _render_result_is_complete(
    render_result: dict[str, Any],
    *,
    pdf_page_count: int,
) -> bool:
    rendered_pages = render_result.get("rendered_pages") or []
    return (
        render_result.get("returncode") == 0
        and len(rendered_pages) == pdf_page_count
        and all(
            bool(page.get("exists"))
            and str(page.get("path") or "").lower().endswith(".png")
            and Path(str(page.get("path") or "")).is_file()
            for page in rendered_pages
        )
    )


def _manual_pdf_review_status(
    manual_review: dict[str, Any],
    *,
    current_pdf_sha256: str | None,
) -> dict[str, Any]:
    reviewed_hash = str(
        (manual_review.get("artifact_sha256") or {}).get("report.pdf") or ""
    ).lower()
    current_hash = str(current_pdf_sha256 or "").lower()
    decision = (
        ((manual_review.get("questions") or {}).get("Q051") or {}).get("decision")
    )
    hash_matches = bool(reviewed_hash) and reviewed_hash == current_hash
    return {
        "reviewed_pdf_sha256": reviewed_hash or None,
        "current_pdf_sha256": current_hash or None,
        "artifact_hash_matches_review": hash_matches,
        "manual_q051_decision": decision,
        "passed": hash_matches and decision == "PASS",
    }


def _external_evidence_layout_status(
    extracted_pages: list[str],
    *,
    news_present: bool,
    decision_present: bool | None = None,
    monitoring_present: bool | None = None,
) -> dict[str, Any]:
    if not news_present:
        return {
            "applicability": "not_applicable",
            "reason": "news_evidence가 없어 외부자료 섹션 배치 검사를 적용하지 않음",
            "page_checks": [],
            "passed": True,
        }

    decision_present = news_present if decision_present is None else decision_present
    monitoring_present = False if monitoring_present is None else monitoring_present
    heading = "두 단계 외부 자료"
    intro_terms = ("정형 점수·등급과 분리", "점수·등급·추천 판단에 사용하지 않습니다")
    decision_terms = ("1단계 · 판단 근거", "조건 적합성", "판단에 사용한 방식")
    monitoring_terms = ("2단계 · 참고·모니터링", "선정 이유", "참고할 내용", "판단 제외 사유")
    page_checks: list[dict[str, Any]] = []
    for page_index, page_text in enumerate(extracted_pages):
        normalized_page = re.sub(r"\s+", " ", page_text)
        checks = {
            "heading": heading in normalized_page,
            "intro": all(term in normalized_page for term in intro_terms),
            "decision_table_context": (
                not decision_present
                or all(term in normalized_page for term in decision_terms)
            ),
            "monitoring_table_context": (
                not monitoring_present
                or all(term in normalized_page for term in monitoring_terms)
            ),
        }
        if not any(checks.values()):
            continue
        page_checks.append(
            {
                "page": page_index + 1,
                "checks": checks,
                "main_context_on_page": checks["heading"] and checks["intro"],
                "decision_context_on_page": checks["decision_table_context"],
                "monitoring_context_on_page": checks["monitoring_table_context"],
            }
        )
    heading_ok = any(row["main_context_on_page"] for row in page_checks)
    decision_ok = not decision_present or any(
        row["decision_context_on_page"] for row in page_checks
    )
    monitoring_ok = not monitoring_present or any(
        row["monitoring_context_on_page"] for row in page_checks
    )
    return {
        "applicability": "required",
        "reason": (
            "외부자료 제목·분리 원칙과 각 증거 층의 제목·표 헤더가 각각 같은 페이지에 있어야 함"
        ),
        "required_tiers": {
            "decision_support": decision_present,
            "reference_monitoring": monitoring_present,
        },
        "page_checks": page_checks,
        "passed": heading_ok and decision_ok and monitoring_ok,
    }


def _news_signal_types(row: dict[str, Any]) -> set[str]:
    value = row.get("signal_types")
    if isinstance(value, list):
        return {str(item).strip() for item in value if str(item).strip()}
    return {
        token
        for token in re.split(r"[,;|\s]+", str(value or ""))
        if token
    }


def _audit_news_rows(news: list[dict[str, Any]]) -> list[dict[str, Any]]:
    audits: list[dict[str, Any]] = []
    for index, row in enumerate(news):
        title = str(row.get("title") or "")
        summary = str(row.get("summary") or "")
        content = f"{title} {summary}"
        matched_location = str(row.get("matched_location") or "")
        compact_title = re.sub(r"\s+", "", title)
        compact_location = re.sub(r"\s+", "", matched_location)
        location_in_title = bool(compact_location) and compact_location in compact_title

        signals = _news_signal_types(row)
        tier = str(row.get("evidence_tier") or "decision_support")
        location_scope = str(row.get("location_scope") or "")
        scope_known = location_scope in {
            "exact_area",
            "nearby",
            "district",
            "seoul",
            "national",
        }
        local_scope = location_scope in {"exact_area", "nearby", "district"}
        broad_scope = location_scope in {"seoul", "national"}
        persistent_terms = sorted(term for term in PERSISTENT_NEWS_TERMS if term in content)
        industry_match = bool(row.get("industry_match"))
        non_industry_scope_ok = industry_match or (
            local_scope
            and bool(signals & PERSISTENT_NEWS_SIGNALS)
            and bool(persistent_terms)
        )
        location_scope_ok = (
            scope_known
            and (
                (local_scope and location_in_title)
                or broad_scope
            )
        )
        broad_scope_ok = not broad_scope or industry_match

        decision_use = str(row.get("decision_use") or "")
        decision_terms = sorted(
            term for term in DECISION_USE_DOMAIN_TERMS if term in decision_use
        )
        supported_decision_terms = sorted(term for term in decision_terms if term in content)
        decision_use_supported = bool(decision_terms) and set(decision_terms).issubset(
            supported_decision_terms
        )

        violations: list[str] = []
        if not scope_known:
            violations.append("unknown_location_scope")
        if tier == "decision_support":
            if not location_scope_ok:
                violations.append("matched_location_not_in_title")
            if not broad_scope_ok:
                violations.append("broad_scope_without_industry_match")
            if not non_industry_scope_ok:
                violations.append("non_industry_without_persistent_signal_and_content")
            if not decision_use_supported:
                violations.append("decision_use_domain_term_not_in_title_or_summary")
            if row.get("eligible_for_decision") is not True:
                violations.append("decision_tier_not_eligible")
            if str(row.get("score_role") or "") != "context_only":
                violations.append("decision_tier_score_role_mismatch")
        elif tier == "reference_monitoring":
            monitoring_basis = str(row.get("monitoring_location_basis") or "")
            allowed_bases = {
                "title_location",
                "official_jurisdiction",
                "broad_industry",
                "broad_official_policy",
                "broad_business_policy",
            }
            if monitoring_basis not in allowed_bases:
                violations.append("monitoring_location_basis_missing")
            if monitoring_basis == "title_location" and not location_in_title:
                violations.append("monitoring_title_location_not_supported")
            if monitoring_basis == "official_jurisdiction" and not (
                location_scope == "district"
                and str(row.get("source_group") or "") == "seoul_district_official"
                and str(row.get("source_grade") or "") == "A"
                and bool(matched_location)
                and str(row.get("provider") or "").startswith(matched_location)
            ):
                violations.append("monitoring_official_jurisdiction_not_supported")
            if monitoring_basis == "broad_business_policy" and not (
                broad_scope
                and "small_business_policy" in signals
                and str(row.get("source_group") or "") == "seoul_district_official"
                and str(row.get("source_grade") or "") == "A"
            ):
                violations.append("monitoring_broad_business_policy_not_supported")
            if row.get("eligible_for_decision") is not False:
                violations.append("monitoring_marked_decision_eligible")
            if decision_use.strip():
                violations.append("monitoring_has_decision_use")
            if str(row.get("score_role") or "") != "reference_only":
                violations.append("monitoring_score_role_mismatch")
            if not str(row.get("reference_use") or "").strip():
                violations.append("monitoring_reference_use_missing")
            applicability_limit = str(row.get("applicability_limit") or "")
            if "사용하지 않음" not in applicability_limit:
                violations.append("monitoring_exclusion_reason_missing")
        else:
            violations.append("unknown_evidence_tier")
        if str(row.get("structured_score_impact") or "") != "none":
            violations.append("structured_score_impact_not_none")
        audits.append(
            {
                "index": index,
                "evidence_tier": tier,
                "title": title,
                "matched_location": matched_location,
                "location_scope": location_scope,
                "location_in_title": location_in_title,
                "location_scope_ok": location_scope_ok,
                "broad_scope_ok": broad_scope_ok,
                "industry_match": industry_match,
                "signals": sorted(signals),
                "persistent_content_terms": persistent_terms,
                "non_industry_scope_ok": non_industry_scope_ok,
                "decision_use": decision_use,
                "decision_use_domain_terms": decision_terms,
                "supported_decision_use_terms": supported_decision_terms,
                "monitoring_location_basis": row.get("monitoring_location_basis"),
                "reference_use": row.get("reference_use"),
                "applicability_limit": row.get("applicability_limit"),
                "eligible_for_decision": row.get("eligible_for_decision"),
                "score_role": row.get("score_role"),
                "structured_score_impact": row.get("structured_score_impact"),
                "violations": violations,
                "passed": not violations,
            }
        )
    return audits


def _json_dump(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def _rows(connection: sqlite3.Connection, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    cursor = connection.execute(sql, params)
    return [dict(row) for row in cursor.fetchall()]


def _one(reference: dict[str, Any], query_id: str) -> dict[str, Any]:
    rows = reference[query_id]["rows"]
    if len(rows) != 1:
        raise RuntimeError(f"{query_id}: expected one row, got {len(rows)}")
    return rows[0]


def _num_equal(actual: Any, expected: Any, tolerance: float = 0.01) -> bool:
    try:
        return math.isclose(
            float(actual),
            float(expected),
            rel_tol=0.0,
            abs_tol=tolerance,
        )
    except (TypeError, ValueError):
        return False


def _grade(score: float | int | None) -> str | None:
    if score is None:
        return None
    value = float(score)
    if value > 90:
        return "A+"
    if value > 80:
        return "A"
    if value > 70:
        return "B+"
    if value > 60:
        return "B"
    if value > 50:
        return "C+"
    if value > 40:
        return "C"
    if value > 30:
        return "D+"
    if value > 20:
        return "D"
    if value > 10:
        return "E+"
    return "E"


def _expected_display_grade(base_grade: Any, score_percentile: Any) -> str | None:
    base = str(base_grade or "").strip().upper()
    thresholds = {"A": 90.0, "B": 70.0, "C": 50.0, "D": 30.0, "E": 10.0}
    if base not in thresholds:
        return None
    try:
        percentile = float(score_percentile)
    except (TypeError, ValueError):
        return base
    return f"{base}+" if percentile > thresholds[base] else base


def _expected_score_rank_text(candidate_count: Any, score_rank: Any) -> str:
    """Mirror the public report's thousands-separated rank presentation."""
    try:
        total = int(candidate_count)
        rank = int(score_rank)
    except (TypeError, ValueError):
        return ""
    return f"서울 {total:,}개 후보 중 {rank:,}위"


def _driver(report: dict[str, Any], axis: str, label: str) -> dict[str, Any]:
    drivers = (
        report.get("indicator_pack", {})
        .get("axis_indicator_pack", {})
        .get(axis, {})
        .get("score_drivers", [])
    )
    for item in drivers:
        if item.get("label") == label:
            return item
    return {}


def _driver_by_source(
    report: dict[str, Any],
    axis: str,
    source: str,
) -> dict[str, Any]:
    drivers = (
        report.get("indicator_pack", {})
        .get("axis_indicator_pack", {})
        .get(axis, {})
        .get("score_drivers", [])
    )
    return next(
        (item for item in drivers if item.get("source") == source),
        {},
    )


def _series(report: dict[str, Any], axis: str, key: str, value_key: str) -> list[dict[str, Any]]:
    rows = (
        report.get("indicator_pack", {})
        .get("axis_indicator_pack", {})
        .get(axis, {})
        .get(key, [])
    )
    return [
        {"timestamp": row.get("timestamp"), value_key: (row.get(value_key) or {}).get("raw")}
        for row in rows
    ]


def _issue_code(issue: str) -> str:
    match = re.match(r"^\[([A-Z_]+)\]", issue or "")
    return match.group(1) if match else "UNKNOWN"


class QuestionRecorder:
    def __init__(self) -> None:
        self.results: list[dict[str, Any]] = []

    def add(
        self,
        question_id: str,
        category: str,
        question: str,
        actual: Any,
        expected: Any,
        passed: bool,
        rationale: str,
        *,
        severity: str = "high",
        gate: bool = True,
        method: str = "deterministic",
        comparator: str = "exact",
        tolerance: float | None = None,
        unit: str | None = None,
        report_path: str | None = None,
        source_query_ids: Iterable[str] = (),
        source_tables: Iterable[str] = (),
        source_artifacts: Iterable[str] = (),
    ) -> None:
        self.results.append(
            {
                "id": question_id,
                "category": category,
                "question_ko": question,
                "severity": severity,
                "gate": gate,
                "method": method,
                "report_path": report_path,
                "actual": actual,
                "expected": expected,
                "comparator": comparator,
                "tolerance": tolerance,
                "unit": unit,
                "decision": "PASS" if passed else "FAIL",
                "passed": bool(passed),
                "rationale_ko": rationale,
                "source_query_ids": list(source_query_ids),
                "source_tables": list(source_tables),
                "source_artifacts": list(source_artifacts),
            }
        )


def _weight_set_for_industry(industry_code: str) -> str:
    prefix = str(industry_code or "").strip().upper()[:3]
    return prefix if prefix in {"CS1", "CS2", "CS3"} else "BASE"


def _extract_report(payload: dict[str, Any], *, source_path: Path) -> dict[str, Any]:
    report = payload.get("report") if isinstance(payload.get("report"), dict) else payload
    if not isinstance(report, dict) or not isinstance(report.get("indicator_pack"), dict):
        raise ValueError(
            f"리포트 JSON에 report 또는 indicator_pack이 없습니다: {source_path}"
        )
    return report


def _resolve_report_json_path(
    *,
    explicit_path: Path | None,
    artifact_dir: Path,
    output_dir: Path,
) -> Path:
    if explicit_path is not None:
        path = explicit_path.resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        return path

    candidates = (
        artifact_dir / "report_response.generated.json",
        artifact_dir / "report.json",
        output_dir / "report_response.raw.json",
    )
    for path in candidates:
        if path.is_file():
            return path.resolve()
    raise FileNotFoundError(
        "리포트 JSON을 찾을 수 없습니다. --report-json 또는 "
        "artifact-dir/report_response.generated.json을 지정하세요."
    )


def _infer_budget_manwon(
    payload: dict[str, Any],
    report: dict[str, Any],
) -> int | None:
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
    case = metrics.get("case") if isinstance(metrics.get("case"), dict) else {}
    request = payload.get("request") if isinstance(payload.get("request"), dict) else {}
    budget_fit = (
        report.get("indicator_pack", {})
        .get("supporting_indicators", {})
        .get("budget_fit", {})
    )
    for value in (
        case.get("budget_manwon"),
        case.get("budget"),
        request.get("budget_manwon"),
        budget_fit.get("budget_manwon"),
    ):
        if value is not None:
            return int(value)
    return None


def _resolve_request(
    *,
    report: dict[str, Any],
    payload: dict[str, Any],
    area_code: str | None,
    industry_code: str | None,
    budget_manwon: int | None,
) -> dict[str, Any]:
    target = report["indicator_pack"].get("target") or {}
    report_area_code = str(target.get("area_code") or "").strip()
    report_industry_code = str(target.get("industry_code") or "").strip()
    if not report_area_code or not report_industry_code:
        raise ValueError("리포트 target에 area_code 또는 industry_code가 없습니다.")
    if area_code is not None and str(area_code) != report_area_code:
        raise ValueError(
            f"--area-code({area_code})와 리포트({report_area_code})가 다릅니다."
        )
    if industry_code is not None and str(industry_code) != report_industry_code:
        raise ValueError(
            f"--industry-code({industry_code})와 리포트({report_industry_code})가 다릅니다."
        )
    inferred_budget = _infer_budget_manwon(payload, report)
    if budget_manwon is not None and inferred_budget is not None:
        if int(budget_manwon) != inferred_budget:
            raise ValueError(
                f"--budget-manwon({budget_manwon})과 리포트({inferred_budget})가 다릅니다."
            )
    resolved_budget = int(budget_manwon) if budget_manwon is not None else inferred_budget
    if resolved_budget is None:
        raise ValueError(
            "예산을 리포트 JSON에서 찾을 수 없습니다. --budget-manwon을 지정하세요."
        )
    return {
        "area_code": report_area_code,
        "area_name": target.get("area_name"),
        "industry_code": report_industry_code,
        "industry_name": target.get("industry_name"),
        "budget_manwon": resolved_budget,
        "quarter": str(target.get("quarter") or ""),
    }


def _alternative_area_codes(report: dict[str, Any]) -> list[str]:
    visible = report.get("alternatives") or []
    facts = (
        report.get("indicator_pack", {})
        .get("facts_pack", {})
        .get("alternatives", [])
        or []
    )
    by_name: dict[str, list[str]] = {}
    for row in facts:
        code = str(row.get("area_code") or "").strip()
        name = str(row.get("area_name") or "").strip()
        if code and name:
            by_name.setdefault(name, []).append(code)
    resolved: list[str] = []
    for row in visible:
        code = str(row.get("area_code") or "").strip()
        if not code:
            matches = by_name.get(str(row.get("area_name") or "").strip()) or []
            code = matches.pop(0) if matches else ""
        if code and code not in resolved:
            resolved.append(code)
    return resolved


def _load_weights(path: Path, weight_set: str) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [
            row
            for row in csv.DictReader(handle)
            if row.get("weight_set") == weight_set
        ]


def _gold_manifest_rows(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _build_reference_bundle(
    connection: sqlite3.Connection,
    *,
    area_code: str,
    industry_code: str,
    quarter: str,
    alternative_area_codes: list[str],
) -> dict[str, Any]:
    params = {
        "area_code": area_code,
        "industry_code": industry_code,
        "quarter": quarter,
        "alternative_area_codes_json": json.dumps(
            alternative_area_codes,
            ensure_ascii=False,
        ),
    }
    bundle: dict[str, Any] = {}
    for query_id, sql in SQL_QUERIES.items():
        query_params = {
            name: params[name]
            for name in (
                "area_code",
                "industry_code",
                "quarter",
                "alternative_area_codes_json",
            )
            if f":{name}" in sql
        }
        bundle[query_id] = {
            "sql": sql,
            "params": query_params,
            "rows": _rows(connection, sql, query_params),
        }
    return bundle


def _build_questions(
    report: dict[str, Any],
    reference: dict[str, Any],
    *,
    budget_manwon: int,
    artifact_dir: Path,
    weight_rows: list[dict[str, Any]],
    weight_set: str,
    score_manifest: dict[str, Any],
    manual_review: dict[str, Any],
    manual_review_path: Path,
    render_result: dict[str, Any],
) -> list[dict[str, Any]]:
    q = QuestionRecorder()
    target = report["indicator_pack"]["target"]
    score = _one(reference, "target_score")
    area = _one(reference, "target_area")
    score_rank = _one(reference, "score_rank")
    latest_sales = _one(reference, "latest_sales")
    sales_rank = _one(reference, "sales_seoul_rank")
    area_rank = _one(reference, "sales_area_rank")
    latest_store = _one(reference, "latest_store")
    store_rank = _one(reference, "store_seoul_rank")
    store_total = _one(reference, "store_total")
    population = _one(reference, "latest_population")
    floating = _one(reference, "latest_floating")
    cost_proxy = _one(reference, "cost_proxy")
    rone_by_metric: dict[str, dict[str, Any]] = {}
    for row in reference["rone_reference"]["rows"]:
        rone_by_metric.setdefault(str(row["metric_code"]), row)
    facts = report["indicator_pack"]["facts_pack"]
    score_block = facts["score_block"]
    markdown = (artifact_dir / "report.md").read_text(encoding="utf-8")

    q.add(
        "Q001",
        "요청 조건",
        "평가 대상 상권 코드가 DB 원문과 같은가?",
        target.get("area_code"),
        area.get("area_code"),
        target.get("area_code") == area.get("area_code"),
        "리포트 indicator_pack.target과 commercial_area 기본키를 직접 비교했습니다.",
        report_path="indicator_pack.target.area_code",
        source_query_ids=["target_area"],
        source_tables=["commercial_area"],
    )
    q.add(
        "Q002",
        "요청 조건",
        "상권명이 DB 원문과 같은가?",
        target.get("area_name"),
        area.get("area_name"),
        target.get("area_name") == area.get("area_name"),
        "표시 상권명과 commercial_area.area_name을 비교했습니다.",
        report_path="indicator_pack.target.area_name",
        source_query_ids=["target_area"],
        source_tables=["commercial_area"],
    )
    q.add(
        "Q003",
        "요청 조건",
        "업종 코드와 업종명이 점수 DB 원문과 같은가?",
        {
            "industry_code": target.get("industry_code"),
            "industry_name": target.get("industry_name"),
        },
        {
            "industry_code": score.get("industry_code"),
            "industry_name": score.get("industry_name"),
        },
        target.get("industry_code") == score.get("industry_code")
        and target.get("industry_name") == score.get("industry_name"),
        "리포트의 업종 식별자를 rule_location_score 행과 비교했습니다.",
        report_path="indicator_pack.target",
        source_query_ids=["target_score"],
        source_tables=["rule_location_score"],
    )
    q.add(
        "Q004",
        "요청 조건",
        "분기 코드가 점수·매출·점포 원문과 모두 같은가?",
        target.get("quarter"),
        {
            "score": score.get("quarter"),
            "sales": latest_sales.get("timestamp"),
            "store": latest_store.get("timestamp"),
        },
        len(
            {
                target.get("quarter"),
                score.get("quarter"),
                latest_sales.get("timestamp"),
                latest_store.get("timestamp"),
            }
        )
        == 1,
        "서로 다른 세 원천 테이블의 기간키를 교차 확인했습니다.",
        report_path="indicator_pack.target.quarter",
        source_query_ids=["target_score", "latest_sales", "latest_store"],
        source_tables=["rule_location_score", "district_sales", "district_store_count"],
    )
    report_budget = (
        report["indicator_pack"]["supporting_indicators"]["budget_fit"].get(
            "budget_manwon"
        )
    )
    q.add(
        "Q005",
        "요청 조건",
        f"사용자 입력 예산 {budget_manwon:,}만원이 변형 없이 보존됐는가?",
        report_budget,
        budget_manwon,
        report_budget == budget_manwon,
        "예산은 DB 점수가 아니라 사용자 입력값이므로 요청 기준값과 직접 비교했습니다.",
        unit="만원",
        report_path="indicator_pack.supporting_indicators.budget_fit.budget_manwon",
        source_artifacts=["evaluation_manifest.json:request"],
    )

    q.add(
        "Q006",
        "점수·등급",
        "종합 입지점수 원값이 DB와 같은가?",
        target.get("score"),
        score.get("current_location_score"),
        _num_equal(target.get("score"), score.get("current_location_score"), 0.001),
        "반올림 표시값이 아니라 indicator_pack의 원점수를 비교했습니다.",
        comparator="absolute_tolerance",
        tolerance=0.001,
        unit="score",
        report_path="indicator_pack.target.score",
        source_query_ids=["target_score"],
        source_tables=["rule_location_score"],
    )
    q.add(
        "Q007",
        "점수·등급",
        f"DB 기본등급 {score.get('grade')}가 리포트 내부에 그대로 보존됐는가?",
        target.get("grade"),
        score.get("grade"),
        target.get("grade") == score.get("grade"),
        "DB 기본등급과 동일 업종 백분위 세분 등급을 구분해 검사했습니다.",
        report_path="indicator_pack.target.grade",
        source_query_ids=["target_score"],
        source_tables=["rule_location_score"],
    )
    expected_display_grade = _expected_display_grade(
        score.get("grade"),
        score_rank.get("score_percentile"),
    )
    q.add(
        "Q008",
        "점수·등급",
        (
            f"공개 등급 {target.get('display_grade')}가 DB 기본등급과 "
            "동일 업종 백분위 세분 규칙에 맞는가?"
        ),
        target.get("display_grade"),
        {
            "base_grade": score.get("grade"),
            "score_percentile": score_rank.get("score_percentile"),
            "derived_display_grade": expected_display_grade,
        },
        target.get("display_grade") == expected_display_grade,
        "플러스 등급은 DB 원열이 아니라 기본등급별 동일 업종 백분위 경계(A 90, B 70, C 50, D 30, E 10)로 만든 공개 세분 등급입니다.",
        report_path="indicator_pack.target.display_grade",
        source_query_ids=["target_score", "score_rank"],
        source_tables=["rule_location_score"],
        source_artifacts=[
            "backend/app/services/indicator_pack.py:_detailed_grade"
        ],
    )
    q.add(
        "Q009",
        "점수·등급",
        "종합 판단 문구가 DB 원문과 같은가?",
        target.get("decision_label"),
        score.get("decision_label"),
        target.get("decision_label") == score.get("decision_label"),
        "판단 문구를 rule_location_score.decision_label과 정확 일치 비교했습니다.",
        report_path="indicator_pack.target.decision_label",
        source_query_ids=["target_score"],
        source_tables=["rule_location_score"],
    )
    coverage_actual = target.get("score_coverage", {})
    coverage_expected = {
        "tier": score.get("score_coverage_tier"),
        "available_axis_count": score.get("available_axis_count"),
        "official_indicator_count": score.get("official_indicator_count"),
        "official_indicator_defined_count": score.get(
            "official_indicator_defined_count"
        ),
        "official_indicator_complete": bool(score.get("official_indicator_complete")),
        "official_rank_eligible": bool(score.get("official_rank_eligible")),
    }
    coverage_compare = {
        key: coverage_actual.get(key) for key in coverage_expected
    }
    q.add(
        "Q010",
        "점수·등급",
        (
            f"{score.get('score_coverage_tier')}의 축 수·필수지표 수·"
            "완결성·순위 자격이 DB와 같은가?"
        ),
        coverage_compare,
        coverage_expected,
        coverage_compare == coverage_expected,
        "coverage 계약의 여섯 필드를 묶어서 비교했습니다.",
        report_path="indicator_pack.target.score_coverage",
        source_query_ids=["target_score"],
        source_tables=["rule_location_score"],
    )
    percentile_raw = score_block["score_percentile"].get("raw")
    expected_percentile_display = (
        round(float(score_rank["score_percentile"]), 1)
        if score_rank.get("score_percentile") is not None
        else None
    )
    q.add(
        "Q011",
        "점수·등급",
        "동일 업종 점수 순위와 백분위가 DB로 재계산되는가?",
        {
            "rank_text": score_block.get("score_rank"),
            "percentile_raw": percentile_raw,
        },
        {
            "rank": score_rank.get("score_rank"),
            "candidate_count": score_rank.get("candidate_count"),
            "percentile_db_exact": score_rank.get("score_percentile"),
            "percentile_report_one_decimal": expected_percentile_display,
        },
        score_block.get("score_rank")
        == _expected_score_rank_text(
            score_rank.get("candidate_count"),
            score_rank.get("score_rank"),
        )
        and _num_equal(percentile_raw, expected_percentile_display, 0.0001),
        "동일 분기·동일 업종·공식 순위 자격 행으로 SQL 순위와 정밀 백분위를 재계산한 뒤, 리포트 공개 정밀도인 소수 첫째 자리로 반올림해 비교했습니다.",
        comparator="rank_and_one_decimal_rounding",
        tolerance=None,
        report_path="indicator_pack.facts_pack.score_block",
        source_query_ids=["score_rank"],
        source_tables=["rule_location_score"],
    )
    q.add(
        "Q012",
        "점수·등급",
        "점수 버전이 DB와 점수 배치 manifest에 모두 일치하는가?",
        target.get("score_version"),
        {
            "db": score.get("score_version"),
            "score_manifest": score_manifest.get("score_version"),
        },
        target.get("score_version") == score.get("score_version")
        == score_manifest.get("score_version"),
        "DB 행과 점수 배치 manifest를 함께 확인했습니다.",
        report_path="indicator_pack.target.score_version",
        source_query_ids=["target_score"],
        source_tables=["rule_location_score"],
        source_artifacts=[str(DEFAULT_SCORE_MANIFEST)],
    )
    axis_map = {
        "Q013": ("sales", "axis_sales", "시장성"),
        "Q014": ("competition", "axis_competition", "경쟁 구조"),
        "Q015": ("demand", "axis_demand", "수요 기반"),
        "Q016": ("accessibility", "axis_accessibility", "접근·유입"),
    }
    for question_id, (report_axis, db_column, label) in axis_map.items():
        actual = report["indicator_pack"]["axis_scores"].get(report_axis)
        expected = score.get(db_column)
        q.add(
            question_id,
            "점수·등급",
            f"{label} 축 점수가 DB 원문과 같은가?",
            actual,
            expected,
            _num_equal(actual, expected, 0.001),
            f"{label} 축의 원점수를 직접 비교했습니다.",
            comparator="absolute_tolerance",
            tolerance=0.001,
            unit="score",
            report_path=f"indicator_pack.axis_scores.{report_axis}",
            source_query_ids=["target_score"],
            source_tables=["rule_location_score"],
        )
    weight_map = {
        row["component"]: float(row["recommended_weight"])
        for row in weight_rows
        if row["component"] in {"sales", "competition", "demand", "accessibility"}
    }
    available_weight_map = {
        axis: weight
        for axis, weight in weight_map.items()
        if report["indicator_pack"]["axis_scores"].get(axis) is not None
    }
    weighted_numerator = sum(
        float(report["indicator_pack"]["axis_scores"][axis]) * weight
        for axis, weight in available_weight_map.items()
    )
    weighted_denominator = sum(available_weight_map.values())
    if not weighted_denominator:
        raise ValueError(
            f"{weight_set} 가중치와 리포트 축 점수의 공통 항목이 없습니다."
        )
    recomputed_score = weighted_numerator / weighted_denominator
    q.add(
        "Q017",
        "점수·등급",
        (
            f"{weight_set} 공식 권장가중치로 종합점수 "
            f"{score.get('current_location_score')}가 재계산되는가?"
        ),
        {
            "published_score": target.get("score"),
            "recomputed_score": round(recomputed_score, 6),
            "weight_set": weight_set,
            "weights": available_weight_map,
        },
        score.get("current_location_score"),
        _num_equal(recomputed_score, score.get("current_location_score"), 0.01),
        (
            f"{target.get('industry_code')}의 업종군은 {weight_set}입니다. "
            "관측된 공식 축의 권장가중치를 결측축 제외 방식으로 정규화해 재계산했습니다."
        ),
        comparator="absolute_tolerance",
        tolerance=0.01,
        unit="score",
        report_path="indicator_pack.axis_scores",
        source_query_ids=["target_score"],
        source_tables=["rule_location_score"],
        source_artifacts=[str(DEFAULT_WEIGHT_FILE)],
    )

    sales_driver = _driver_by_source(
        report,
        "sales",
        "DB.district_sales.sales_amount",
    ) or _driver(report, "sales", "최근 분기 상권·업종 합산 추정매출액")
    q.add(
        "Q018",
        "시장·경쟁 원문",
        "최근 분기 상권×업종 합산 추정매출 원값이 DB와 같은가?",
        sales_driver.get("raw"),
        latest_sales.get("sales_amount"),
        _num_equal(sales_driver.get("raw"), latest_sales.get("sales_amount"), 0.5),
        (
            "요약 표시값이 아니라 "
            f"{latest_sales.get('sales_amount'):,}원 원값을 대조했습니다."
            if latest_sales.get("sales_amount") is not None
            else "요약 표시값이 아니라 DB 원값을 대조했습니다."
        ),
        comparator="absolute_tolerance",
        tolerance=0.5,
        unit="원",
        report_path="indicator_pack.axis_indicator_pack.sales.score_drivers",
        source_query_ids=["latest_sales"],
        source_tables=["district_sales"],
    )
    seoul_rank_driver = _driver(report, "sales", "동일 업종 서울 매출 순위")
    q.add(
        "Q019",
        "시장·경쟁 원문",
        (
            "동일 업종 서울 매출 순위 "
            f"{sales_rank.get('sales_rank')}/{sales_rank.get('candidate_count')}와 "
            f"백분위 {sales_rank.get('sales_percentile')}가 DB에서 재현되는가?"
        ),
        {
            "rank": seoul_rank_driver.get("raw"),
            "percentile": _driver(report, "sales", "동업종 내 매출 위치").get(
                "raw"
            ),
        },
        {
            "rank": sales_rank.get("sales_rank"),
            "total": sales_rank.get("candidate_count"),
            "percentile": sales_rank.get("sales_percentile"),
        },
        seoul_rank_driver.get("raw")
        == {
            "rank": sales_rank.get("sales_rank"),
            "total": sales_rank.get("candidate_count"),
        }
        and _num_equal(
            _driver(report, "sales", "동업종 내 매출 위치").get("raw"),
            sales_rank.get("sales_percentile"),
            0.1,
        ),
        "동일 분기·동일 업종의 전체 상권을 SQL로 다시 순위화했습니다.",
        comparator="rank_and_tolerance",
        tolerance=0.1,
        report_path="indicator_pack.axis_indicator_pack.sales.score_drivers",
        source_query_ids=["sales_seoul_rank"],
        source_tables=["district_sales"],
    )
    area_rank_driver = _driver(report, "sales", "상권 내 업종 매출 순위")
    q.add(
        "Q020",
        "시장·경쟁 원문",
        (
            f"{target.get('area_name')} 내부 업종 매출 순위 "
            f"{area_rank.get('area_industry_rank')}/{area_rank.get('industry_count')}가 "
            "DB에서 재현되는가?"
        ),
        area_rank_driver.get("raw"),
        {
            "rank": area_rank.get("area_industry_rank"),
            "total": area_rank.get("industry_count"),
        },
        area_rank_driver.get("raw")
        == {
            "rank": area_rank.get("area_industry_rank"),
            "total": area_rank.get("industry_count"),
        },
        "같은 상권·같은 분기의 업종 전체를 SQL로 다시 순위화했습니다.",
        report_path="indicator_pack.axis_indicator_pack.sales.score_drivers",
        source_query_ids=["sales_area_rank"],
        source_tables=["district_sales"],
    )
    report_sales_history = _series(report, "sales", "recent_series", "sales_amount")
    expected_sales_history = [
        {"timestamp": row["timestamp"], "sales_amount": row["sales_amount"]}
        for row in reference["sales_history"]["rows"]
    ]
    q.add(
        "Q021",
        "시장·경쟁 원문",
        (
            f"최근 {len(expected_sales_history)}개 분기 매출 원값 배열이 "
            "DB 원문과 행별로 같은가?"
        ),
        report_sales_history,
        expected_sales_history,
        report_sales_history == expected_sales_history,
        (
            "요약 증감률이 아니라 "
            f"{len(expected_sales_history)}개 분기의 기간키와 원 단위 값을 전부 비교했습니다."
        ),
        report_path="indicator_pack.axis_indicator_pack.sales.recent_series",
        source_query_ids=["sales_history"],
        source_tables=["district_sales"],
    )
    store_driver = _driver(report, "competition", "동업종 점포수")
    q.add(
        "Q022",
        "시장·경쟁 원문",
        f"동업종 점포수 {latest_store.get('store_count')}개가 DB 원문과 같은가?",
        store_driver.get("raw"),
        latest_store.get("store_count"),
        store_driver.get("raw") == latest_store.get("store_count"),
        "동일 상권·업종·분기의 점포 수를 비교했습니다.",
        unit="개",
        report_path="indicator_pack.axis_indicator_pack.competition.score_drivers",
        source_query_ids=["latest_store"],
        source_tables=["district_store_count"],
    )
    total_store_driver = _driver(report, "competition", "상권 전체 점포수")
    q.add(
        "Q023",
        "시장·경쟁 원문",
        (
            f"상권 전체 점포수 {store_total.get('total_store_count'):,}개가 "
            "DB 합계와 같은가?"
            if store_total.get("total_store_count") is not None
            else "상권 전체 점포수가 DB 합계와 같은가?"
        ),
        total_store_driver.get("raw"),
        store_total.get("total_store_count"),
        total_store_driver.get("raw") == store_total.get("total_store_count"),
        f"{target.get('area_name')}의 같은 분기 업종별 점포수를 SUM으로 재계산했습니다.",
        unit="개",
        report_path="indicator_pack.axis_indicator_pack.competition.score_drivers",
        source_query_ids=["store_total"],
        source_tables=["district_store_count"],
    )
    ratio_driver = _driver(report, "competition", "동업종 점포 비중")
    expected_ratio = latest_store["store_count"] / store_total["total_store_count"]
    q.add(
        "Q024",
        "시장·경쟁 원문",
        (
            "동업종 점포 비중 원값이 "
            f"{latest_store.get('store_count')}/{store_total.get('total_store_count')}로 "
            "재계산되는가?"
        ),
        ratio_driver.get("raw"),
        expected_ratio,
        _num_equal(ratio_driver.get("raw"), expected_ratio, 0.00005),
        "표시 백분율 이전의 비율 원값을 나눗셈으로 검산했습니다.",
        comparator="absolute_tolerance",
        tolerance=0.00005,
        unit="ratio",
        report_path="indicator_pack.axis_indicator_pack.competition.score_drivers",
        source_query_ids=["latest_store", "store_total"],
        source_tables=["district_store_count"],
    )
    report_store_history = _series(
        report, "competition", "recent_series", "store_count"
    )
    expected_store_history = [
        {"timestamp": row["timestamp"], "store_count": row["store_count"]}
        for row in reference["store_history"]["rows"]
    ]
    q.add(
        "Q025",
        "시장·경쟁 원문",
        (
            f"최근 {len(expected_store_history)}개 분기 점포수 원값 배열이 "
            "DB 원문과 행별로 같은가?"
        ),
        report_store_history,
        expected_store_history,
        report_store_history == expected_store_history,
        (
            "DB의 전체 시계열 "
            f"{[row['store_count'] for row in expected_store_history]}을 비교했습니다."
        ),
        report_path="indicator_pack.axis_indicator_pack.competition.recent_series",
        source_query_ids=["store_history"],
        source_tables=["district_store_count"],
    )
    q.add(
        "Q026",
        "수요 원문",
        "상주인구와 직장인구가 DB 원문과 같은가?",
        {
            "resident": _driver(report, "demand", "상주인구").get("raw"),
            "worker": _driver(report, "demand", "직장인구").get("raw"),
        },
        {
            "resident": population.get("resident_population"),
            "worker": population.get("worker_population"),
        },
        _driver(report, "demand", "상주인구").get("raw")
        == population.get("resident_population")
        and _driver(report, "demand", "직장인구").get("raw")
        == population.get("worker_population"),
        "동일 상권·분기의 두 인구 필드를 직접 비교했습니다.",
        unit="명",
        report_path="indicator_pack.axis_indicator_pack.demand.score_drivers",
        source_query_ids=["latest_population"],
        source_tables=["district_population"],
    )
    expected_daily_floating = round(floating["floating_population"] / 90)
    q.add(
        "Q027",
        "수요 원문",
        "분기 유동인구와 90일 일평균이 DB 원문에서 재계산되는가?",
        {
            "quarter_total": _driver(report, "demand", "총 유동인구").get("raw"),
            "daily_average": _driver(report, "demand", "일평균 유동인구").get(
                "raw"
            ),
        },
        {
            "quarter_total": floating.get("floating_population"),
            "daily_average": expected_daily_floating,
            "formula": f"{floating.get('floating_population')} / 90",
        },
        _driver(report, "demand", "총 유동인구").get("raw")
        == floating.get("floating_population")
        and _driver(report, "demand", "일평균 유동인구").get("raw")
        == expected_daily_floating,
        "분기 누계 원값과 리포트가 명시한 90일 나눗셈을 검산했습니다.",
        unit="명",
        report_path="indicator_pack.axis_indicator_pack.demand.score_drivers",
        source_query_ids=["latest_floating"],
        source_tables=["district_floating"],
    )

    cost_metrics = report["indicator_pack"]["supporting_indicators"]["cost_metrics"]
    cost_grade_metric = cost_metrics[0]
    q.add(
        "Q028",
        "비용·예산 원문",
        (
            f"비용 여건 점수 {score.get('cost_risk_score')}와 등급 "
            f"{_grade(score.get('cost_risk_score'))}가 DB 및 등급 경계와 같은가?"
        ),
        {
            "raw": cost_grade_metric.get("raw"),
            "grade": cost_grade_metric.get("grade"),
        },
        {
            "raw": score.get("cost_risk_score"),
            "derived_grade": _grade(score.get("cost_risk_score")),
        },
        _num_equal(cost_grade_metric.get("raw"), score.get("cost_risk_score"), 0.001)
        and cost_grade_metric.get("grade") == _grade(score.get("cost_risk_score")),
        "비용 점수 원값과 공개 등급 경계를 함께 검산했습니다.",
        report_path="indicator_pack.supporting_indicators.cost_metrics[0]",
        source_query_ids=["target_score"],
        source_tables=["rule_location_score"],
    )
    rent_metric = next(
        row for row in cost_metrics if row.get("label") == "R-ONE 임대료 참고"
    )
    q.add(
        "Q029",
        "비용·예산 원문",
        "R-ONE 임대료 참고값과 evidence-only 권한 플래그가 DB와 같은가?",
        {
            "raw": rent_metric.get("raw"),
            "note": rent_metric.get("note"),
        },
        rone_by_metric.get("rent"),
        _num_equal(
            rent_metric.get("raw"),
            rone_by_metric.get("rent", {}).get("metric_value"),
            0.0001,
        )
        and rone_by_metric.get("rent", {}).get("direct_value_allowed") == 0
        and rone_by_metric.get("rent", {}).get("proxy_score_allowed") == 0
        and rone_by_metric.get("rent", {}).get("engine_promotion_ready") == 0,
        "indicator_pack과 같은 우선순위(지역명 후보, 서울 기준선, 상가 유형)로 R-ONE 참고행을 독립 선택하고 사용 제한 플래그를 함께 확인했습니다.",
        comparator="value_and_permission_flags",
        tolerance=0.0001,
        unit="천원/㎡",
        report_path="indicator_pack.supporting_indicators.cost_metrics",
        source_query_ids=["rone_reference"],
        source_tables=["area_rone_cost_reference"],
    )
    vacancy_metric = next(
        row for row in cost_metrics if row.get("label") == "R-ONE 공실률 참고"
    )
    q.add(
        "Q030",
        "비용·예산 원문",
        "R-ONE 공실률 참고값과 evidence-only 권한 플래그가 DB와 같은가?",
        {
            "raw": vacancy_metric.get("raw"),
            "note": vacancy_metric.get("note"),
        },
        rone_by_metric.get("vacancy"),
        _num_equal(
            vacancy_metric.get("raw"),
            rone_by_metric.get("vacancy", {}).get("metric_value"),
            0.0001,
        )
        and rone_by_metric.get("vacancy", {}).get("direct_value_allowed") == 0
        and rone_by_metric.get("vacancy", {}).get("proxy_score_allowed") == 0
        and rone_by_metric.get("vacancy", {}).get("engine_promotion_ready") == 0,
        "indicator_pack과 같은 지역·상가유형 우선순위로 공실률 참고행을 독립 선택하고 사용 제한 플래그를 함께 비교했습니다.",
        comparator="value_and_permission_flags",
        tolerance=0.0001,
        unit="%",
        report_path="indicator_pack.supporting_indicators.cost_metrics",
        source_query_ids=["rone_reference"],
        source_tables=["area_rone_cost_reference"],
    )
    rtms_metric = next(
        row
        for row in cost_metrics
        if row.get("label") == "RTMS 상업용 부동산 매매가 프록시"
    )
    q.add(
        "Q031",
        "비용·예산 원문",
        "RTMS 매매가 프록시가 DB와 같고 임대료가 아님을 표시했는가?",
        {"raw": rtms_metric.get("raw"), "note": rtms_metric.get("note")},
        cost_proxy,
        _num_equal(
            rtms_metric.get("raw"),
            cost_proxy.get("sale_price_proxy_manwon_per_m2"),
            0.0001,
        )
        and "임대료" in (rtms_metric.get("note") or "")
        and "아님" in (rtms_metric.get("note") or ""),
        "자치구 매매 실거래 프록시 원값과 공개 한계 문구를 같이 확인했습니다.",
        comparator="value_and_scope_label",
        tolerance=0.0001,
        unit="만원/㎡",
        report_path="indicator_pack.supporting_indicators.cost_metrics",
        source_query_ids=["cost_proxy"],
        source_tables=["area_sale_price_proxy"],
    )
    budget_fit = report["indicator_pack"]["supporting_indicators"]["budget_fit"]
    q.add(
        "Q032",
        "비용·예산 원문",
        "예산 적합도를 점수화하지 않고 공식 보류(withheld)로 유지했는가?",
        {
            "budget_fit_score": budget_fit.get("budget_fit_score"),
            "official_budget_fit_status": budget_fit.get(
                "official_budget_fit_status"
            ),
            "direct_value_allowed": budget_fit.get("direct_value_allowed"),
            "proxy_score_allowed": budget_fit.get("proxy_score_allowed"),
            "engine_promotion_ready": budget_fit.get("engine_promotion_ready"),
        },
        {
            "budget_fit_score": None,
            "official_budget_fit_status": "withheld_evidence_only",
            "direct_value_allowed": False,
            "proxy_score_allowed": False,
            "engine_promotion_ready": False,
        },
        budget_fit.get("budget_fit_score") is None
        and budget_fit.get("official_budget_fit_status")
        == "withheld_evidence_only"
        and budget_fit.get("direct_value_allowed") is False
        and budget_fit.get("proxy_score_allowed") is False
        and budget_fit.get("engine_promotion_ready") is False,
        "현재 원천은 개별 점포 임대료·권리금이 아니므로 공식 예산 적합도 산출이 금지됩니다.",
        report_path="indicator_pack.supporting_indicators.budget_fit",
        source_query_ids=["rone_reference", "cost_proxy"],
        source_tables=["area_rone_cost_reference", "area_sale_price_proxy"],
    )

    actual_alternatives = report.get("alternatives", [])
    fact_alternatives = (
        report.get("indicator_pack", {})
        .get("facts_pack", {})
        .get("alternatives", [])
        or []
    )
    fact_by_name = {
        str(row.get("area_name") or ""): row
        for row in fact_alternatives
    }
    db_by_code = {
        str(row.get("area_code") or ""): row
        for row in reference["alternative_scores"]["rows"]
    }
    expected_alternatives: list[dict[str, Any]] = []
    alt_pass = bool(actual_alternatives)
    for actual in actual_alternatives:
        fact = fact_by_name.get(str(actual.get("area_name") or "")) or {}
        expected = db_by_code.get(str(fact.get("area_code") or "")) or {}
        expected_alternatives.append(expected)
        alt_pass = (
            alt_pass
            and bool(expected)
            and actual.get("area_name") == expected.get("area_name")
            and actual.get("score") == expected.get("display_grade")
            and actual.get("cost") == _grade(expected.get("cost_risk_score"))
        )
    alt_pass = (
        alt_pass
        and len(expected_alternatives) == len(actual_alternatives)
        and len(reference["alternative_scores"]["rows"]) == len(actual_alternatives)
    )
    q.add(
        "Q033",
        "대안 상권",
        (
            f"공개된 {len(actual_alternatives)}개 대안의 "
            "상권명·입지등급·비용등급이 DB와 같은가?"
        ),
        actual_alternatives,
        [
            {
                "area_code": row.get("area_code"),
                "area_name": row.get("area_name"),
                "raw_score": row.get("current_location_score"),
                "base_grade": row.get("grade"),
                "display_grade": row.get("display_grade"),
                "cost_risk_score": row.get("cost_risk_score"),
                "cost_grade": _grade(row.get("cost_risk_score")),
            }
            for row in expected_alternatives
        ],
        alt_pass,
        "대안의 표시 등급을 해당 상권의 동일 업종 점수 행으로 다시 계산했습니다.",
        report_path="alternatives",
        source_query_ids=["alternative_scores"],
        source_tables=["rule_location_score"],
    )

    critic_issues = validate_report_draft(
        report,
        facts_pack_display=report["facts_pack_display"],
        user_condition=facts["user_condition"],
        evidence_frames=report.get("evidence_frames") or [],
        markdown_body=report.get("markdown_body") or markdown,
    )
    q.add(
        "Q034",
        "자동 검증 계약",
        "저장된 품질 상태와 현재 deterministic critic 재검증이 모두 clean인가?",
        {
            "quality_status": report.get("quality_status"),
            "recorded_validation_issues": report.get("validation_issues"),
            "current_critic_issues": critic_issues,
        },
        {
            "quality_status": "pass",
            "recorded_validation_issues": [],
            "current_critic_issues": [],
        },
        report.get("quality_status") == "pass"
        and not report.get("validation_issues")
        and not critic_issues,
        "프로세스 종료코드가 아니라 저장 당시 상태와 현재 critic 결과를 각각 확인했습니다.",
        report_path="quality_status, validation_issues",
        source_artifacts=["backend/app/services/report_critic.py"],
    )
    chart_ids = [row.get("id") for row in report.get("chart_manifest", [])]
    q.add(
        "Q035",
        "자동 검증 계약",
        "차트 manifest가 C1~C5를 중복·누락 없이 포함하는가?",
        chart_ids,
        ["C1", "C2", "C3", "C4", "C5"],
        chart_ids == ["C1", "C2", "C3", "C4", "C5"],
        "차트 ID 순서와 집합을 함께 확인했습니다.",
        report_path="chart_manifest[*].id",
    )
    q.add(
        "Q036",
        "자동 검증 계약",
        "출처와 해석 한계가 비어 있지 않은가?",
        {
            "source_citation_count": len(report.get("source_citations") or []),
            "limitation_count": len(report.get("limitations") or []),
        },
        {"source_citation_count_min": 1, "limitation_count_min": 1},
        bool(report.get("source_citations")) and bool(report.get("limitations")),
        "숫자 정확도 외에 공개 출처와 한계 섹션의 존재를 확인했습니다.",
        report_path="source_citations, limitations",
    )
    budget_caveat = _budget_caveat_found(markdown)
    forbidden_non_negated = re.search(
        r"(?<!않)(?:매출|수익성|성공)(?:을|를)?\s*(?:보장|확정)", markdown
    )
    q.add(
        "Q037",
        "서사 안전성",
        "예산 보류 한계가 명시되고 매출·수익·성공 보장 표현이 없는가?",
        {
            "budget_caveat_found": budget_caveat,
            "forbidden_match": (
                forbidden_non_negated.group(0) if forbidden_non_negated else None
            ),
        },
        {"budget_caveat_found": True, "forbidden_match": None},
        budget_caveat and forbidden_non_negated is None,
        (
            "PASS: 예산의 공식 적합도 보류·상한 성격을 밝혔고 보장형 문구도 없습니다."
            if budget_caveat and forbidden_non_negated is None
            else "FAIL: 예산 보류·상한 한계가 없거나 매출·수익·성공 보장 표현이 탐지됐습니다."
        ),
        method="deterministic_text_rule",
        report_path="report.md:13,25,60",
        source_artifacts=[str(artifact_dir / "report.md")],
    )
    db_sales_history = reference["sales_history"]["rows"]
    trend_direction_expected = (
        "down"
        if db_sales_history[0]["sales_amount"] < db_sales_history[-1]["sales_amount"]
        else "up_or_flat"
    )
    trend_direction_actual = _trend_direction_from_text(
        report.get("trend_analysis") or ""
    )
    q.add(
        "Q038",
        "서사 의미 검증",
        (
            f"{len(db_sales_history)}개 분기 매출 방향 해석이 "
            "원값의 시작·끝 방향과 같은가?"
        ),
        trend_direction_actual,
        {
            "direction": trend_direction_expected,
            "latest": db_sales_history[0],
            "oldest": db_sales_history[-1],
        },
        trend_direction_actual == trend_direction_expected,
        (
            "PASS: 시작·끝 원값의 방향과 추이 문장의 방향이 같습니다."
            if trend_direction_actual == trend_direction_expected
            else "FAIL: 시작·끝 원값의 방향과 추이 문장의 방향이 다르거나 방향 표현이 없습니다."
        ),
        method="deterministic_direction_check",
        report_path="trend_analysis; report.md:39",
        source_query_ids=["sales_history"],
        source_tables=["district_sales"],
        source_artifacts=[str(artifact_dir / "report.md")],
    )
    competition_text = next(
        row.get("meaning", "")
        for row in report.get("axis_interpretations", [])
        if row.get("axis") == "경쟁 구조"
    )
    competition_percentile = store_rank.get("store_percentile")
    competition_phrase_direction_ok = not (
        (competition_percentile or 0) >= 90 and "수가 아주 많지 않" in competition_text
    )
    competition_critic_issues = [
        issue
        for issue in critic_issues
        if _issue_code(issue) == "COMPETITION_DIRECTION_MISMATCH"
    ]
    competition_direction_ok = (
        competition_phrase_direction_ok and not competition_critic_issues
    )
    q.add(
        "Q039",
        "서사 의미 검증",
        "점포수 백분위와 경쟁 설명의 많고 적음 방향이 모순되지 않는가?",
        {
            "store_count": store_rank.get("store_count"),
            "store_rank": store_rank.get("store_rank"),
            "candidate_count": store_rank.get("candidate_count"),
            "store_percentile": competition_percentile,
            "report_excerpt": competition_text,
            "phrase_direction_ok": competition_phrase_direction_ok,
            "critic_issues": competition_critic_issues,
        },
        "동업종 점포수가 서울 상위권이면 '수가 많지 않다'고 반대로 설명하지 않아야 함",
        competition_direction_ok,
        (
            "PASS: 백분위 방향의 독립 문구 검사와 현재 critic이 모두 일치합니다."
            if competition_direction_ok
            else "FAIL: 상위권 점포수를 적다고 해석한 문구 또는 COMPETITION_DIRECTION_MISMATCH가 탐지됐습니다."
        ),
        method="independent_semantic_rule",
        report_path="axis_interpretations[competition].meaning; report.md:32",
        source_query_ids=["store_seoul_rank"],
        source_tables=["district_store_count"],
        source_artifacts=[str(artifact_dir / "report.md")],
    )
    accessibility = next(
        row
        for row in report.get("axis_interpretations", [])
        if row.get("axis") == "접근·유입"
    )
    access_drivers = (
        report["indicator_pack"]["axis_indicator_pack"]["accessibility"].get(
            "score_drivers"
        )
        or []
    )
    reuses_demand = any(
        token in (accessibility.get("meaning") or "")
        for token in ("직장인구", "유동인구")
    )
    access_critic_issues = [
        issue
        for issue in critic_issues
        if _issue_code(issue) == "ACCESS_EVIDENCE_SCOPE_MISMATCH"
    ]
    access_trace_ok = (bool(access_drivers) or not reuses_demand) and not access_critic_issues
    q.add(
        "Q040",
        "서사 의미 검증",
        (
            "접근·유입 "
            f"{accessibility.get('display_grade') or accessibility.get('score_display')} "
            "설명이 같은 축의 직접 지표에 연결되는가?"
        ),
        {
            "db_axis_score": score.get("axis_accessibility"),
            "app_axis_score": (
                report["indicator_pack"]["axis_indicator_pack"]["accessibility"].get(
                    "axis_score"
                )
            ),
            "app_display_grade": (
                accessibility.get("display_grade")
                or accessibility.get("score_display")
            ),
            "score_drivers": access_drivers,
            "evidence_metrics": accessibility.get("evidence_metrics"),
            "meaning": accessibility.get("meaning"),
            "critic_issues": access_critic_issues,
        },
        {
            "db_axis_score": "rule_location_score.axis_accessibility 원값",
            "app_display_grade": "앱 파생 공개 등급",
            "score_drivers": "같은 축 직접 지표 또는 빈 배열",
            "evidence_metrics": "공개 근거 또는 빈 배열",
            "scope_rule": "직접 지표가 없으면 타 축 지표를 원인으로 재사용하지 않음",
        },
        access_trace_ok,
        (
            "PASS: DB 점수·앱 표시등급·직접 근거를 구분했고 타 축 근거 재사용이 없습니다."
            if access_trace_ok
            else "FAIL: 직접 지표가 비어 있는데 타 축 근거를 재사용했거나 ACCESS_EVIDENCE_SCOPE_MISMATCH가 탐지됐습니다."
        ),
        method="independent_axis_trace_review",
        report_path="indicator_pack.axis_indicator_pack.accessibility; axis_interpretations[3]",
        source_query_ids=["target_score"],
        source_tables=["rule_location_score"],
        source_artifacts=[
            "backend/app/services/indicator_pack.py",
            "backend/app/services/interpretive_report.py",
            "backend/app/services/report_critic.py",
        ],
    )
    news = report.get("news_evidence") or []
    news_row_audits = _audit_news_rows(news)
    news_relevance_ok = all(row["passed"] for row in news_row_audits)
    decision_news = [
        row
        for row in news
        if str(row.get("evidence_tier") or "decision_support") == "decision_support"
    ]
    monitoring_news = [
        row
        for row in news
        if str(row.get("evidence_tier") or "") == "reference_monitoring"
    ]
    q.add(
        "Q041",
        "외부 자료",
        "외부 자료가 판단 근거와 참고·모니터링으로 분리되고 각 층의 사용 범위를 넘지 않았는가?",
        {
            "news_count": len(news),
            "decision_support_count": len(decision_news),
            "reference_monitoring_count": len(monitoring_news),
            "industry_match_count": sum(bool(row.get("industry_match")) for row in news),
            "applicability": "required" if news else "not_applicable",
            "row_audits": news_row_audits,
        },
        (
            "판단 근거는 위치·업종 또는 지속 입지 변화와 원문 범위가 직접 확인되어야 함. "
            "참고·모니터링은 위치 선정 근거가 검증되어야 하고 decision_use가 비어 있으며 "
            "eligible_for_decision=false, score_role=reference_only, "
            "structured_score_impact=none, 판단 제외 사유를 모두 가져야 함"
        ),
        news_relevance_ok,
        (
            (
                "PASS: news_evidence가 없어 관련성 검사를 적용하지 않았습니다."
                if not news
                else (
                    "PASS: 판단 근거와 참고·모니터링 자료가 각각의 위치·사용 제한·"
                    "점수 비반영 계약을 통과했습니다."
                )
            )
            if news_relevance_ok
            else (
                "FAIL: 한 개 이상의 자료가 판단 근거 직접성 또는 참고·모니터링 "
                "사용 제한 계약을 통과하지 못했습니다."
            )
        ),
        method="independent_evidence_relevance_review",
        report_path=(
            "news_evidence; report.md:not-applicable(no-news)"
            if not news
            else f"news_evidence; {_report_section_path(markdown, '두 단계 외부 자료')}"
        ),
        source_artifacts=[str(artifact_dir / "report.md")],
    )
    headline_sales_specific = (
        "상권×업종" in "\n".join(markdown.splitlines()[:25])
        or "상권·업종 합산" in "\n".join(markdown.splitlines()[:25])
    )
    public_sales_label = str(sales_driver.get("label") or "")
    sales_db_key_matches = (
        str(latest_sales.get("area_code")) == str(target.get("area_code"))
        and str(latest_sales.get("industry_code")) == str(target.get("industry_code"))
        and str(latest_sales.get("timestamp")) == str(target.get("quarter"))
    )
    sales_db_value_matches = _num_equal(
        sales_driver.get("raw"),
        latest_sales.get("sales_amount"),
        0.5,
    )
    q.add(
        "Q042",
        "공개 표현",
        "헤드라인 매출이 개별 점포 매출이 아니라 상권×업종 합산 추정매출임을 가까운 위치에서 밝히는가?",
        {
            "db_key": {
                "id": latest_sales.get("id"),
                "area_code": latest_sales.get("area_code"),
                "industry_code": latest_sales.get("industry_code"),
                "timestamp": latest_sales.get("timestamp"),
            },
            "db_sales_amount": latest_sales.get("sales_amount"),
            "report_driver_raw": sales_driver.get("raw"),
            "public_driver_label": public_sales_label,
            "headline_excerpt": "\n".join(markdown.splitlines()[12:20]),
        },
        {
            "db_key": {
                "area_code": target.get("area_code"),
                "industry_code": target.get("industry_code"),
                "timestamp": target.get("quarter"),
            },
            "db_value_matches_report_driver": True,
            "public_label_scope": "상권·업종 합산 추정매출",
            "headline_scope_near_value": True,
        },
        (
            sales_db_key_matches
            and sales_db_value_matches
            and "합산 추정매출" in public_sales_label
            and headline_sales_specific
        ),
        (
            "PASS: latest_sales의 상권·업종·분기 키와 원값, 공개 합산 라벨, 헤드라인 표현이 함께 일치합니다."
            if (
                sales_db_key_matches
                and sales_db_value_matches
                and "합산 추정매출" in public_sales_label
                and headline_sales_specific
            )
            else "FAIL: latest_sales 키·원값 또는 공개 합산 라벨·헤드라인 범위가 일치하지 않습니다."
        ),
        method="independent_reader_clarity_review",
        report_path=_report_section_path(markdown, "핵심 판단"),
        source_query_ids=["latest_sales"],
        source_tables=["district_sales"],
        source_artifacts=[str(artifact_dir / "report.md")],
    )
    raw_quarter_hits = [
        {"line": index, "text": line}
        for index, line in enumerate(markdown.splitlines(), start=1)
        if re.search(r"\b20\d{3}\b", line)
    ]
    q.add(
        "Q043",
        "공개 표현",
        "공개 문서에 연도+분기 숫자로 된 내부 분기 코드가 노출되지 않는가?",
        raw_quarter_hits,
        [],
        not raw_quarter_hits,
        (
            "PASS: 분기 코드가 독자용 기간으로 변환됐습니다."
            if not raw_quarter_hits
            else "FAIL: 내부형 분기 코드가 본문·표에 노출됩니다."
        ),
        method="deterministic_text_rule",
        report_path="report.md",
        source_artifacts=[str(artifact_dir / "report.md")],
    )
    duplicate_number_hits = [
        {"line": index, "text": line}
        for index, line in enumerate(markdown.splitlines(), start=1)
        if re.match(r"^\d+\.\s+\d+\)", line)
    ]
    q.add(
        "Q044",
        "공개 표현",
        "실행 우선순위에 '1. 1)' 같은 이중 번호가 없는가?",
        duplicate_number_hits,
        [],
        not duplicate_number_hits,
        (
            "PASS: 목록 번호가 한 번만 표시됩니다."
            if not duplicate_number_hits
            else "FAIL: 실행 우선순위 네 줄 모두 Markdown 번호와 문장 번호가 중복됐습니다."
        ),
        method="deterministic_text_rule",
        report_path=_report_section_path(markdown, "실행 우선순위"),
        source_artifacts=[str(artifact_dir / "report.md")],
    )
    grammar_hits = _korean_particle_mismatches(markdown)
    grammar_hits.extend(
        [
        {"line": index, "text": line}
        for index, line in enumerate(markdown.splitlines(), start=1)
        if "대상 우위" in line or "대안 우위" in line
        ]
    )
    q.add(
        "Q045",
        "공개 표현",
        "대안 문구의 조사와 차이 설명이 독자용 한국어로 자연스러운가?",
        grammar_hits,
        "상권명의 실제 받침에 맞지 않는 조사와 내부식 '대상/대안 우위' 표현이 없어야 함",
        not grammar_hits,
        (
            "PASS: 공개 대안 문구가 자연스럽습니다."
            if not grammar_hits
            else "FAIL: 괄호 안 마지막 발음 음절과 맞지 않는 조사 또는 '시장성 대상/대안 우위' 같은 내부식 표현이 남았습니다."
        ),
        method="independent_korean_copy_review",
        report_path=_report_section_path(markdown, "대안 상권 비교"),
        source_artifacts=[str(artifact_dir / "report.md")],
    )
    causal_excerpts = [
        line
        for line in markdown.splitlines()
        if (
            "실수요를 기대할 수 있는 구조" in line
            or "반복 접점이 충분" in line
            or "수요와 유입이 충분히 강" in line
        )
        and not any(
            hedge in line
            for hedge in (
                "검증이 필요",
                "별도 검증",
                "확인이 필요",
                "확인할 수 없",
                "단정할 수 없",
                "충분한지는",
                "충분한지",
                "보장하지",
            )
        )
    ]
    causal_critic_issues = [
        issue
        for issue in critic_issues
        if _issue_code(issue) == "CAUSAL_SCOPE_OVERCLAIM"
    ]
    causal_scope_ok = not causal_excerpts and not causal_critic_issues
    q.add(
        "Q046",
        "서사 의미 검증",
        "집계 인구·매출 관측을 실수요·반복접점의 원인 증명으로 승격하지 않았는가?",
        {
            "targeted_phrase_excerpts": causal_excerpts,
            "critic_issues": causal_critic_issues,
        },
        {"targeted_phrase_excerpts": [], "critic_issues": []},
        causal_scope_ok,
        (
            "PASS: 지정 문구 검사와 현재 critic의 CAUSAL_SCOPE_OVERCLAIM 검사가 모두 clean입니다."
            if causal_scope_ok
            else "FAIL: 지정된 실수요·반복접점 문구 또는 critic의 CAUSAL_SCOPE_OVERCLAIM이 탐지됐습니다."
        ),
        method="independent_causal_scope_review",
        report_path="report.md:23,33-34",
        source_query_ids=["latest_population", "latest_floating"],
        source_tables=["district_population", "district_floating"],
        source_artifacts=[str(artifact_dir / "report.md")],
    )

    marker_path = artifact_dir / ".public-presentation-version"
    marker = marker_path.read_text(encoding="utf-8").strip() if marker_path.exists() else None
    q.add(
        "Q047",
        "산출물 무결성",
        "PDF/Markdown 산출물의 presentation marker가 현재 발행 코드 버전과 같은가?",
        marker,
        PUBLIC_PRESENTATION_VERSION,
        marker == PUBLIC_PRESENTATION_VERSION,
        (
            "PASS: 현재 발행 코드로 만든 산출물입니다."
            if marker == PUBLIC_PRESENTATION_VERSION
            else (
                f"FAIL: 저장 marker는 {marker or '없음'}, 현재 발행 버전은 "
                f"{PUBLIC_PRESENTATION_VERSION}입니다."
            )
        ),
        method="deterministic_version_check",
        report_path=".public-presentation-version",
        source_artifacts=[
            str(marker_path),
            "backend/app/services/report_publisher.py:PUBLIC_PRESENTATION_VERSION",
        ],
    )
    pdf_path = artifact_dir / "report.pdf"
    md_path = artifact_dir / "report.md"
    reader = PdfReader(str(pdf_path))
    annotation_count = 0
    extracted_pages: list[str] = []
    for page in reader.pages:
        extracted_pages.append(page.extract_text() or "")
        annotations = page.get("/Annots")
        annotation_count += len(annotations) if annotations else 0
    rendered_pages = render_result.get("rendered_pages") or []
    render_complete = _render_result_is_complete(
        render_result,
        pdf_page_count=len(reader.pages),
    )
    q.add(
        "Q048",
        "산출물 무결성",
        "PDF가 파싱·전 페이지 텍스트 추출·전 페이지 PNG 렌더 가능하고 외부 링크 주석이 0개인가?",
        {
            "exists": pdf_path.exists(),
            "bytes": pdf_path.stat().st_size if pdf_path.exists() else 0,
            "pages": len(reader.pages),
            "extractable_page_count": sum(bool(text.strip()) for text in extracted_pages),
            "annotation_count": annotation_count,
            "render": {
                "returncode": render_result.get("returncode"),
                "rendered_page_count": len(rendered_pages),
                "all_rendered_png_exist": all(
                    bool(page.get("exists"))
                    and str(page.get("path") or "").lower().endswith(".png")
                    and Path(str(page.get("path") or "")).is_file()
                    for page in rendered_pages
                ),
                "error": render_result.get("error"),
            },
        },
        {
            "exists": True,
            "min_bytes": 1000,
            "min_pages": 1,
            "extractable_page_count": "all pages",
            "annotation_count": 0,
            "render": {
                "returncode": 0,
                "rendered_page_count": "PDF page count",
                "all_rendered_png_exist": True,
            },
        },
        pdf_path.exists()
        and pdf_path.stat().st_size > 1000
        and len(reader.pages) >= 1
        and all(text.strip() for text in extracted_pages)
        and annotation_count == 0
        and render_complete,
        "pypdf 구조·전체 페이지 텍스트·annotation과 선행 pdftoppm 전 페이지 렌더 결과를 함께 검사했습니다.",
        method="deterministic_pdf_structure",
        source_artifacts=[
            str(pdf_path),
            "visual_qa/render_result.json",
        ],
    )
    chart_evidence: list[dict[str, Any]] = []
    charts_ok = True
    for chart_id in ("C1", "C2", "C3", "C4", "C5"):
        path = artifact_dir / "charts" / f"{chart_id}.png"
        item = _file_identity(path)
        if path.exists():
            with Image.open(path) as image:
                item["width"] = image.width
                item["height"] = image.height
                item["format"] = image.format
                item["mode"] = image.mode
            charts_ok = (
                charts_ok
                and item["width"] >= 1000
                and item["height"] >= 600
                and item["format"] == "PNG"
            )
        else:
            charts_ok = False
        chart_evidence.append(item)
    q.add(
        "Q049",
        "산출물 무결성",
        "C1~C5가 모두 PNG로 존재하고 최소 1000×600인가?",
        chart_evidence,
        {"ids": ["C1", "C2", "C3", "C4", "C5"], "min_size": [1000, 600]},
        charts_ok,
        "각 차트를 PIL로 열어 포맷과 실제 픽셀 크기를 확인했습니다.",
        method="deterministic_image_structure",
        source_artifacts=[str(artifact_dir / "charts")],
    )
    q050_review = (manual_review.get("questions") or {}).get("Q050") or {}
    reviewed_hashes = {
        name: str(value).lower()
        for name, value in (manual_review.get("artifact_sha256") or {}).items()
    }
    current_visual_hashes = {
        "C3.png": _sha256(artifact_dir / "charts" / "C3.png"),
        "C5.png": _sha256(artifact_dir / "charts" / "C5.png"),
    }
    manual_review_matches_artifacts = all(
        reviewed_hashes.get(name) == (digest or "").lower()
        for name, digest in current_visual_hashes.items()
    )
    q050_passed = (
        manual_review_matches_artifacts
        and q050_review.get("decision") == "PASS"
    )
    q.add(
        "Q050",
        "시각 품질",
        "C3·C5 차트의 수치 단위가 축 또는 값 라벨에 명시돼 있는가?",
        {
            "reviewed_actual": q050_review.get("actual"),
            "reviewed_artifact_sha256": reviewed_hashes,
            "current_artifact_sha256": current_visual_hashes,
            "artifact_hashes_match_review": manual_review_matches_artifacts,
        },
        q050_review.get("expected"),
        q050_passed,
        (
            "PASS: 해시가 일치하는 차트를 독립 시각검수했고 필요한 단위가 표시됐습니다."
            if q050_passed
            else (
                f"FAIL: {q050_review.get('rationale_ko')}"
                if manual_review_matches_artifacts
                else "FAIL: 차트 해시가 수동 시각검수 기록과 달라 재검수가 필요합니다."
            )
        ),
        method="independent_visual_review",
        source_artifacts=[
            str(artifact_dir / "charts" / "C3.png"),
            str(artifact_dir / "charts" / "C5.png"),
            str(manual_review_path),
        ],
    )
    external_layout_status = _external_evidence_layout_status(
        extracted_pages,
        news_present=bool(news),
        decision_present=bool(decision_news),
        monitoring_present=bool(monitoring_news),
    )
    manual_pdf_review_status = _manual_pdf_review_status(
        manual_review,
        current_pdf_sha256=_sha256(pdf_path),
    )
    q051_passed = (
        external_layout_status["passed"]
        and manual_pdf_review_status["passed"]
    )
    q.add(
        "Q051",
        "시각 품질",
        "두 단계 외부자료의 분리 원칙과 각 층의 표 헤더가 PDF에서 함께 읽히고 현재 PDF의 수동 검토가 유효한가?",
        {
            "automated_layout": external_layout_status,
            "manual_pdf_review": manual_pdf_review_status,
        },
        {
            "automated_layout": (
                "news_evidence가 있으면 제목·분리 원칙이 같은 페이지에 있고, "
                "존재하는 각 층의 제목·표 헤더가 각각 같은 페이지에 있어야 함. "
                "없으면 명시적 not_applicable"
            ),
            "manual_pdf_review": {
                "report.pdf_sha256_matches": True,
                "Q051_decision": "PASS",
            },
        },
        q051_passed,
        (
            (
                "PASS: 외부자료가 없어 배치 검사는 not_applicable이며, 현재 PDF 해시에 묶인 Q051 수동 판정이 PASS입니다."
                if not news
                else "PASS: 두 단계 외부자료의 분리 원칙과 각 층의 표 헤더가 읽히며 현재 PDF 해시에 묶인 Q051 수동 판정이 PASS입니다."
            )
            if q051_passed
            else "FAIL: 자동 외부자료 배치 계약 또는 현재 PDF 해시에 결합된 Q051 수동 판정이 충족되지 않았습니다."
        ),
        method="independent_pdf_page_review",
        report_path=(
            "report.md:not-applicable(no-news)"
            if not news
            else _report_section_path(markdown, "두 단계 외부 자료")
        ),
        source_artifacts=[str(pdf_path), str(manual_review_path)],
    )
    effective_reasoning_effort = (
        report.get("reasoning_effort")
        or report.get("token_usage", {}).get("reasoning_effort")
    )
    q.add(
        "Q052",
        "모델·실행 추적",
        "생성 모델·provider 모델·reasoning effort·실토큰 사용량이 기록됐는가?",
        {
            "ai_model": report.get("ai_model"),
            "reasoning_effort": effective_reasoning_effort,
            "top_level_reasoning_effort": report.get("reasoning_effort"),
            "token_usage": report.get("token_usage"),
        },
        {
            "report_generator_present": True,
            "provider_model_present": True,
            "reasoning_effort_present": True,
            "estimated": False,
            "positive_total_tokens": True,
        },
        bool(report.get("ai_model"))
        and bool(effective_reasoning_effort)
        and report.get("token_usage", {}).get("estimated") is False
        and bool(report.get("token_usage", {}).get("provider_models"))
        and (report.get("token_usage", {}).get("total_tokens") or 0) > 0,
        "평가 모델과 생성 모델을 혼동하지 않도록 생성 측 provider 기록을 따로 확인했습니다.",
        report_path="ai_model, reasoning_effort, token_usage",
        source_artifacts=["report_json:report.token_usage"],
    )
    table_counts = {
        row["table_name"]: row["row_count"]
        for row in reference["table_counts"]["rows"]
    }
    expected_score_row_count = table_counts.get("rule_location_score")
    manifest_ok = (
        score_manifest.get("analysis_quarter") == target.get("quarter")
        and score_manifest.get("score_version") == target.get("score_version")
        and score_manifest.get("row_count") == expected_score_row_count
    )
    q.add(
        "Q053",
        "원천 계보",
        "점수 배치 manifest의 분기·버전·행 수가 현재 리포트와 일치하는가?",
        score_manifest,
        {
            "analysis_quarter": target.get("quarter"),
            "score_version": target.get("score_version"),
            "row_count": expected_score_row_count,
        },
        manifest_ok,
        "리포트 점수의 배치 SHA·Gold manifest SHA·release id까지 source_catalog에 보존했습니다.",
        source_query_ids=["table_counts"],
        source_tables=["rule_location_score"],
        source_artifacts=[str(DEFAULT_SCORE_MANIFEST), str(DEFAULT_GOLD_MANIFEST)],
    )
    q.add(
        "Q054",
        "자동 검증 계약",
        "초안 오류와 최종 오류가 분리 기록되어 최종 수리 여부를 확인할 수 있는가?",
        {
            "original_validation_issues": report.get("original_validation_issues"),
            "final_validation_issues": report.get("validation_issues"),
            "repair_log": report.get("section_repair_log"),
        },
        "초안 오류는 보존되고 최종 hard issue는 0이어야 함",
        isinstance(report.get("original_validation_issues"), list)
        and report.get("validation_issues") == [],
        "초안 단계 오류를 지우지 않고 최종 수리 결과와 분리해 보존했는지 확인했습니다.",
        report_path="original_validation_issues, validation_issues, section_repair_log",
    )
    internal_marker_hits = re.findall(
        r"\[(?:NEWS|근거):?\s*\d+\]|C[1-5](?!\.png)", markdown
    )
    q.add(
        "Q055",
        "공개 표현",
        "내부 뉴스·근거·차트 토큰이 공개 본문에 노출되지 않는가?",
        internal_marker_hits,
        [],
        not internal_marker_hits,
        "내부 인용/차트 마커와 공개 분기 코드는 별도 규칙으로 검사했습니다.",
        method="deterministic_text_rule",
        report_path="report.md",
        source_artifacts=[str(md_path)],
    )
    q.add(
        "Q056",
        "산출물 무결성",
        "평가 대상 Markdown·PDF 해시가 재대조용으로 기록됐는가?",
        {
            "markdown": _file_identity(md_path),
            "pdf": _file_identity(pdf_path),
        },
        {"sha256_present_for_each": True},
        bool(_sha256(md_path)) and bool(_sha256(pdf_path)),
        "동일 파일인지 확인할 수 있도록 SHA-256을 manifest와 문항 결과에 기록했습니다.",
        source_artifacts=[str(md_path), str(pdf_path)],
    )
    return q.results


def _run_negative_controls(
    report: dict[str, Any],
    *,
    budget_manwon: int,
) -> list[dict[str, Any]]:
    facts_display = report["facts_pack_display"]
    user_condition = report["indicator_pack"]["facts_pack"]["user_condition"]
    evidence_frames = report.get("evidence_frames") or []

    controls: list[dict[str, Any]] = []

    def run(
        control_id: str,
        description: str,
        expected_codes: list[str],
        mutate: Any,
    ) -> None:
        draft = copy.deepcopy(report)
        mutate(draft)
        issues = validate_report_draft(
            draft,
            facts_pack_display=facts_display,
            user_condition=user_condition,
            evidence_frames=evidence_frames,
            markdown_body=draft.get("markdown_body") or "",
        )
        codes = sorted(set(_issue_code(issue) for issue in issues))
        controls.append(
            {
                "id": control_id,
                "description_ko": description,
                "expected_issue_codes": expected_codes,
                "detected_issue_codes": codes,
                "issues": issues,
                "decision": (
                    "PASS"
                    if all(code in codes for code in expected_codes)
                    else "FAIL"
                ),
            }
        )

    run(
        "NC01",
        "매출 보장 문구를 넣으면 금지 주장으로 잡히는가?",
        ["FORBIDDEN"],
        lambda draft: draft.__setitem__(
            "summary", (draft.get("summary") or "") + " 이 입지는 매출을 보장합니다."
        ),
    )
    run(
        "NC02",
        "facts pack에 없는 999.9억원을 넣으면 숫자 불일치로 잡히는가?",
        ["FACT_MISMATCH"],
        lambda draft: draft.__setitem__(
            "summary",
            (draft.get("summary") or "") + " 최근 분기 매출은 999.9억원입니다.",
        ),
    )
    run(
        "NC03",
        "보류된 예산으로 진입 가능하다고 단정하면 예산 과장으로 잡히는가?",
        ["BUDGET_SCOPE_OVERCLAIM"],
        lambda draft: draft.__setitem__(
            "user_fit",
            f"예산 {budget_manwon:,}만원이면 이 상권에 충분히 진입 가능합니다.",
        ),
    )
    run(
        "NC04",
        "축 표시등급을 DB와 다르게 바꾸면 등급 불일치로 잡히는가?",
        ["GRADE_MISMATCH"],
        lambda draft: draft["axis_interpretations"][0].__setitem__(
            "meaning",
            (draft["axis_interpretations"][0].get("meaning") or "")
            + " 따라서 시장성은 E 등급입니다.",
        ),
    )
    run(
        "NC05",
        "대안 상권을 모두 지우면 누락으로 잡히는가?",
        ["MISSING_ALTERNATIVES"],
        lambda draft: draft.__setitem__("alternatives", []),
    )
    run(
        "NC06",
        "공개 서사에 raw float를 넣으면 형식 오류로 잡히는가?",
        ["FORMAT"],
        lambda draft: draft.__setitem__(
            "summary", (draft.get("summary") or "") + " 내부값은 12.3456입니다."
        ),
    )
    return controls


def _collect_historical_failures(root: Path) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    issue_counter: Counter[str] = Counter()
    for path in sorted(root.rglob("*.json")):
        if path.name == "summary.json" or "audit" in path.parts:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        report = payload.get("report", {})
        metrics = payload.get("metrics", {})
        issues = report.get("original_validation_issues") or []
        if not issues:
            continue
        codes = [_issue_code(issue) for issue in issues]
        issue_counter.update(codes)
        cases.append(
            {
                "artifact": str(path),
                "case": metrics.get("case"),
                "model": metrics.get("model"),
                "reasoning_effort": metrics.get("reasoning_effort"),
                "generation_mode": metrics.get("generation_mode"),
                "quality_status_after_repair": report.get("quality_status"),
                "original_validation_issues": issues,
                "original_issue_codes": codes,
                "final_validation_issues": report.get("validation_issues") or [],
                "fallback_fields": report.get("fallback_fields") or [],
            }
        )
    return {
        "source_root": str(root),
        "actual_historical_case_count": len(cases),
        "original_issue_code_counts": dict(sorted(issue_counter.items())),
        "cases": cases,
        "note_ko": (
            "실제 과거 생성 초안의 오류입니다. 합성 negative control과 구분하며, "
            "최종 수리 후 PASS 여부도 별도 필드로 보존했습니다."
        ),
    }


def _write_csv(path: Path, results: list[dict[str, Any]]) -> None:
    fields = [
        "id",
        "category",
        "question_ko",
        "severity",
        "gate",
        "method",
        "decision",
        "rationale_ko",
        "report_path",
        "actual_json",
        "expected_json",
        "comparator",
        "tolerance",
        "unit",
        "source_query_ids",
        "source_tables",
        "source_artifacts",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for result in results:
            writer.writerow(
                {
                    **{key: result.get(key) for key in fields if key not in {
                        "actual_json",
                        "expected_json",
                        "source_query_ids",
                        "source_tables",
                        "source_artifacts",
                    }},
                    "actual_json": json.dumps(
                        result.get("actual"), ensure_ascii=False, default=str
                    ),
                    "expected_json": json.dumps(
                        result.get("expected"), ensure_ascii=False, default=str
                    ),
                    "source_query_ids": ";".join(result.get("source_query_ids") or []),
                    "source_tables": ";".join(result.get("source_tables") or []),
                    "source_artifacts": ";".join(
                        result.get("source_artifacts") or []
                    ),
                }
            )


def _write_queries(path: Path, reference: dict[str, Any]) -> None:
    lines = [
        "-- 상세리포트 DB 기준값 재현 SQL",
        "-- SQLite named parameters: :area_code, :industry_code, :quarter",
        "",
    ]
    for query_id, item in reference.items():
        lines.append(f"-- [{query_id}] params={json.dumps(item['params'], ensure_ascii=False)}")
        lines.append(item["sql"].rstrip(";") + ";")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_question_set_markdown(
    path: Path, results: list[dict[str, Any]]
) -> None:
    lines = [
        "# 상세리포트 평가 질문셋",
        "",
        f"- 프로토콜: `{PROTOCOL_VERSION}`",
        f"- 문항 수: {len(results)}",
        "- 판정 규칙: `gate=True` 문항이 하나라도 FAIL이면 전체 FAIL",
        "- 실제값과 기준값은 `question_results.jsonl` 및 `question_results.csv` 참조",
        "",
        "| ID | 분류 | Gate | 방법 | 평가 질문 | 비교 기준 | 기준값 출처 |",
        "|---|---|---:|---|---|---|---|",
    ]
    for row in results:
        sources = ", ".join(
            (row.get("source_query_ids") or [])
            + (row.get("source_tables") or [])
            + (row.get("source_artifacts") or [])
        )
        lines.append(
            f"| {row['id']} | {row['category']} | {str(row['gate']).lower()} | "
            f"{row['method']} | {row['question_ko'].replace('|', '/')} | "
            f"{row['comparator']} | {sources.replace('|', '/')} |"
        )
    lines.extend(
        [
            "",
            "## 문항 원문 대조 규칙",
            "",
            "- DB 문항: JSONL의 `source_queries[*].sql`과 `params`를 그대로 실행합니다.",
            "- 산출물 문항: `report_path`와 `source_artifacts`의 Markdown/PDF/PNG를 확인합니다.",
            "- 수동 의미 문항: 실제 문장·원천 지표·판정 기준을 JSONL의 `actual`, `expected`, `rationale_ko`에 함께 보존합니다.",
            "- 합성 오류 검출 문항은 이 질문셋과 섞지 않고 `negative_controls.json`에 별도로 둡니다.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_reproduction_script(
    path: Path,
    *,
    db_path: Path,
    artifact_dir: Path,
    report_json_path: Path,
    output_dir: Path,
    request: dict[str, Any],
) -> None:
    content = f"""# PowerShell 7 / Windows
$ErrorActionPreference = "Stop"
if (Test-Path variable:PSNativeCommandUseErrorActionPreference) {{
  $PSNativeCommandUseErrorActionPreference = $false
}}
$workspace = "C:\\final_map_project"
$python = Join-Path $workspace "final_proj\\.venv\\Scripts\\python.exe"
$evaluator = Join-Path $workspace "final_proj\\backend\\scripts\\evaluate_detailed_report_grounding.py"
$canonicalOutput = "{output_dir}"
$rerunOutput = Join-Path $canonicalOutput "rerun"
$hardGateOutput = Join-Path $canonicalOutput "hard-gate-rerun"
$manualReview = Join-Path $canonicalOutput "manual_visual_review.json"

Set-Location $workspace

# 1) 보존된 canonical 증거를 덮어쓰지 않고 별도 폴더에 전체 결과 재생성.
& $python $evaluator `
  --db "{db_path}" `
  --artifact-dir "{artifact_dir}" `
  --report-json "{report_json_path}" `
  --output-dir $rerunOutput `
  --area-code "{request['area_code']}" `
  --industry-code "{request['industry_code']}" `
  --budget-manwon {request['budget_manwon']} `
  --manual-review $manualReview `
  --no-fail-exit

# 2) canonical 결과에서 특정 문항만 읽기. 이 호출은 파일을 다시 쓰지 않습니다.
& $python $evaluator `
  --output-dir $canonicalOutput `
  --question-id "Q018" `
  --no-fail-exit

# 3) evaluator의 hard-gate 종료코드를 별도 출력 폴더에서 검증.
$canonicalStatus = (Get-Content -LiteralPath (Join-Path $canonicalOutput "summary.json") -Raw -Encoding UTF8 | ConvertFrom-Json).overall_status
$expectedHardGateExit = if ($canonicalStatus -eq "PASS") {{ 0 }} else {{ 1 }}
& $python $evaluator `
  --db "{db_path}" `
  --artifact-dir "{artifact_dir}" `
  --report-json "{report_json_path}" `
  --output-dir $hardGateOutput `
  --area-code "{request['area_code']}" `
  --industry-code "{request['industry_code']}" `
  --budget-manwon {request['budget_manwon']} `
  --manual-review $manualReview
$hardGateExit = $LASTEXITCODE
if ($hardGateExit -ne $expectedHardGateExit) {{
  throw "hard-gate exit mismatch: expected=$expectedHardGateExit actual=$hardGateExit"
}}
Write-Output "hard-gate exit verified: $hardGateExit"
exit 0
"""
    path.write_text(content, encoding="utf-8-sig")


def _write_report(
    path: Path,
    *,
    results: list[dict[str, Any]],
    summary: dict[str, Any],
    request: dict[str, Any],
    negative_controls: list[dict[str, Any]],
    historical: dict[str, Any],
    artifact_dir: Path,
) -> None:
    failed = [row for row in results if row["decision"] == "FAIL"]
    q018 = next((row for row in results if row.get("id") == "Q018"), {})
    q018_expected = q018.get("expected")
    lines = [
        "# 상세리포트 독립 검증 결과",
        "",
        f"- 평가자: **{EVALUATOR_ID}**",
        f"- 평가 대상 생성 모델: **{summary.get('report_generator_model') or '미기록'}**",
        f"- 프로토콜: `{PROTOCOL_VERSION}`",
        (
            f"- 대상: `{request['area_code']}` {request.get('area_name') or ''} × "
            f"`{request['industry_code']}` {request.get('industry_name') or ''} × "
            f"예산 `{request['budget_manwon']:,}`만원"
        ),
        f"- 최종 판정: **{summary['overall_status']}**",
        f"- 문항: PASS {summary['pass_count']} / FAIL {summary['fail_count']} / 전체 {summary['question_count']}",
        "",
        "## 판정 해석",
        "",
        f"- DB 원값 정합성: **{summary['db_grounding_status']}**",
        f"- 저장/critic 자동 계약: **{summary['automatic_contract_status']}**",
        f"- 의미·출처·표현 검증: **{summary['semantic_quality_status']}**",
        f"- PDF·차트 산출물 검증: **{summary['artifact_quality_status']}**",
        "",
        (
            "모든 문항이 기준값·DB 원문·자동 계약·의미 범위·PDF/차트 품질 게이트를 통과했습니다."
            if summary["overall_status"] == "PASS"
            else (
                "숫자 원값 대부분이 DB와 일치해도 다른 하드 게이트가 실패하면 전체 배포 판정은 FAIL입니다. "
                "실패 이유는 아래 문항별 결과의 실제값·기준값·근거에서 확인합니다."
            )
        ),
        "",
        "## 실패 문항",
        "",
        "| ID | 분류 | 질문 | 실패 근거 |",
        "|---|---|---|---|",
    ]
    for row in failed:
        lines.append(
            f"| {row['id']} | {row['category']} | {row['question_ko']} | "
            f"{str(row['rationale_ko']).replace('|', '/')} |"
        )
    if not failed:
        lines.append("| - | - | 실패 문항 없음 | 모든 하드 게이트 통과 |")
    lines.extend(
        [
            "",
            "## 문항별 결과",
            "",
            "아래 표는 빠른 색인입니다. 실제값·기준값·SQL·출처는 "
            "`question_results.jsonl`, `question_results.csv`, "
            "`db_reference.raw.json`, `queries_used.sql`에 원문 그대로 있습니다.",
            "",
            "| ID | 판정 | 분류 | 문항 | 기준 출처 |",
            "|---|---|---|---|---|",
        ]
    )
    for row in results:
        sources = ", ".join(
            (row.get("source_query_ids") or [])
            + (row.get("source_tables") or [])
            + (row.get("source_artifacts") or [])
        )
        lines.append(
            f"| {row['id']} | {row['decision']} | {row['category']} | "
            f"{row['question_ko'].replace('|', '/')} | {sources.replace('|', '/')} |"
        )
    lines.extend(
        [
            "",
            "## 실패 사례",
            "",
            f"- 현재 산출물 실패: {len(failed)}건 (`current_failure_cases.json`)",
            f"- 실제 과거 초안 오류 포함 artifact: {historical['actual_historical_case_count']}건 (`historical_failure_cases.json`)",
            f"- 합성 negative control: {sum(row['decision'] == 'PASS' for row in negative_controls)}/{len(negative_controls)} 탐지 성공 (`negative_controls.json`)",
            "",
            "과거 실패는 실제 저장 artifact에서 가져왔고, negative control은 검출기 민감도 확인을 위해 "
            "의도적으로 변조한 예시입니다. 두 종류를 섞어 성공률로 계산하지 않았습니다.",
            "",
            "## 임의 문항 DB 대조 방법",
            "",
            "1. `question_results.jsonl`에서 문항 ID를 찾습니다.",
            "2. `source_query_ids`에 적힌 ID를 `db_reference.raw.json`의 `queries`에서 찾습니다.",
            "3. 그 객체의 `sql`, `params`, `rows`가 이번 평가에서 사용한 기준값입니다.",
            "4. 같은 SQL은 `queries_used.sql`에 복사 가능한 형태로도 있습니다.",
            "5. `report_response.raw.json`의 `report`에서 `report_path` 실제값을 확인합니다.",
            "",
            "예: Q018은 `indicator_pack.axis_indicator_pack.sales.score_drivers`의 "
            f"원값과 `district_sales.sales_amount={q018_expected}`를 비교합니다.",
            "",
            "## 실행과 재현",
            "",
            (
                "실행 명령은 `reproduce.ps1`에 고정했습니다. 현재 전체 판정은 PASS이므로 "
                "기본 실행의 기대 종료코드는 0입니다."
                if summary["overall_status"] == "PASS"
                else (
                    "실행 명령은 `reproduce.ps1`에 고정했습니다. 현재 전체 판정은 FAIL이므로 "
                    "기본 실행의 종료코드 1은 오류가 아니라 검증기의 의도된 hard gate입니다. "
                    "파일만 다시 만들려면 `--no-fail-exit`를 사용합니다."
                )
            ),
            "",
            "## 평가 대상 파일",
            "",
            f"- Markdown: `{artifact_dir / 'report.md'}`",
            f"- PDF: `{artifact_dir / 'report.pdf'}`",
            f"- 차트: `{artifact_dir / 'charts'}`",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def _render_pdf_pages(pdf_path: Path, output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    for stale_page in output_dir.glob("page-*.png"):
        stale_page.unlink(missing_ok=True)
    executable = shutil.which("pdftoppm") or shutil.which("pdftoppm.cmd")
    if not executable:
        return {
            "command": None,
            "returncode": None,
            "error": "pdftoppm executable not found",
            "rendered_pages": [],
        }
    executable_path = Path(executable)
    native_executable = (
        executable_path.parents[2]
        / "native"
        / "poppler"
        / "Library"
        / "bin"
        / "pdftoppm.exe"
    )
    if native_executable.exists():
        command_prefix = [str(native_executable)]
    elif executable_path.suffix.lower() in {".cmd", ".bat"}:
        command_prefix = ["cmd.exe", "/d", "/c", executable]
    else:
        command_prefix = [executable]
    command = command_prefix + [
        "-png",
        "-r",
        "120",
        str(pdf_path),
        str(output_dir / "page"),
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "command": command,
            "returncode": None,
            "error": str(exc),
            "rendered_pages": [],
        }
    rendered = sorted(output_dir.glob("page-*.png"))
    return {
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "rendered_pages": [_file_identity(path) for path in rendered],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="상세리포트의 문항별 DB grounding·서사·PDF 검증 패키지를 생성합니다."
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACT_DIR)
    parser.add_argument(
        "--report-json",
        type=Path,
        help=(
            "평가할 생성 결과 JSON. 직접 리포트 객체와 "
            "{metrics, report} 배치 wrapper를 모두 지원합니다. "
            "생략 시 artifact-dir/report_response.generated.json을 우선 사용합니다."
        ),
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--area-code")
    parser.add_argument("--industry-code")
    parser.add_argument("--budget-manwon", type=int)
    parser.add_argument(
        "--manual-review",
        type=Path,
        default=DEFAULT_MANUAL_REVIEW,
        help=(
            "Q050 차트 PNG 및 Q051 PDF 육안검수 판정과 "
            "각 대상 SHA-256을 담은 JSON 경로"
        ),
    )
    parser.add_argument("--question-id")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="--question-id와 함께 사용해도 기존 결과를 읽지 않고 전체 평가를 다시 실행합니다.",
    )
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = args.db.resolve()
    artifact_dir = args.artifact_dir.resolve()
    output_dir = args.output_dir.resolve()
    manual_review_path = args.manual_review.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    existing_results_path = output_dir / "question_results.jsonl"
    if args.question_id and not args.refresh and existing_results_path.exists():
        existing_results = [
            json.loads(line)
            for line in existing_results_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        selected = next(
            (row for row in existing_results if row.get("id") == args.question_id),
            None,
        )
        if selected is None:
            print(f"unknown question id: {args.question_id}", file=sys.stderr)
            return 2
        print(json.dumps(selected, ensure_ascii=False, indent=2, default=str))
        return 0

    if not db_path.exists():
        raise FileNotFoundError(db_path)
    report_json_path = _resolve_report_json_path(
        explicit_path=args.report_json,
        artifact_dir=artifact_dir,
        output_dir=output_dir,
    )
    report_payload = json.loads(report_json_path.read_text(encoding="utf-8"))
    if not isinstance(report_payload, dict):
        raise ValueError(f"리포트 JSON 최상위가 객체가 아닙니다: {report_json_path}")
    report = _extract_report(report_payload, source_path=report_json_path)
    resolved_request = _resolve_request(
        report=report,
        payload=report_payload,
        area_code=args.area_code,
        industry_code=args.industry_code,
        budget_manwon=args.budget_manwon,
    )
    area_code = resolved_request["area_code"]
    industry_code = resolved_request["industry_code"]
    budget_manwon = resolved_request["budget_manwon"]
    quarter = resolved_request["quarter"]
    if not quarter:
        raise ValueError("리포트 target.quarter가 비어 있습니다.")
    if not (artifact_dir / "report.md").exists():
        raise FileNotFoundError(artifact_dir / "report.md")
    if not (artifact_dir / "report.pdf").exists():
        raise FileNotFoundError(artifact_dir / "report.pdf")
    if not manual_review_path.exists():
        raise FileNotFoundError(manual_review_path)

    render_result = _render_pdf_pages(
        artifact_dir / "report.pdf",
        output_dir / "visual_qa" / "pages",
    )

    uri = f"file:{db_path.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN")
        reference = _build_reference_bundle(
            connection,
            area_code=area_code,
            industry_code=industry_code,
            quarter=quarter,
            alternative_area_codes=_alternative_area_codes(report),
        )
        data_version = connection.execute("PRAGMA data_version").fetchone()[0]
        schema_version = connection.execute("PRAGMA schema_version").fetchone()[0]
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]
        connection.rollback()

    weight_set = _weight_set_for_industry(industry_code)
    weight_rows = _load_weights(DEFAULT_WEIGHT_FILE, weight_set)
    if not weight_rows:
        raise RuntimeError(f"가중치 파일에 {weight_set} 행이 없습니다.")
    gold_rows = _gold_manifest_rows(DEFAULT_GOLD_MANIFEST)
    score_manifest = json.loads(DEFAULT_SCORE_MANIFEST.read_text(encoding="utf-8"))
    manual_review = json.loads(manual_review_path.read_text(encoding="utf-8"))

    results = _build_questions(
        report,
        reference,
        budget_manwon=budget_manwon,
        artifact_dir=artifact_dir,
        weight_rows=weight_rows,
        weight_set=weight_set,
        score_manifest=score_manifest,
        manual_review=manual_review,
        manual_review_path=manual_review_path,
        render_result=render_result,
    )
    for result in results:
        result["source_queries"] = [
            {
                "id": query_id,
                "sql": reference[query_id]["sql"],
                "params": reference[query_id]["params"],
            }
            for query_id in result.get("source_query_ids", [])
            if query_id in reference
        ]
    negative_controls = _run_negative_controls(
        report,
        budget_manwon=budget_manwon,
    )
    historical = _collect_historical_failures(DEFAULT_HISTORICAL_ROOT)
    current_failures = [row for row in results if row["decision"] == "FAIL"]

    categories = {
        category: {
            "pass": sum(
                row["decision"] == "PASS"
                for row in results
                if row["category"] == category
            ),
            "fail": sum(
                row["decision"] == "FAIL"
                for row in results
                if row["category"] == category
            ),
        }
        for category in sorted({row["category"] for row in results})
    }
    hard_failures = [
        row
        for row in results
        if row["gate"] and row["decision"] == "FAIL"
    ]
    db_categories = {
        "요청 조건",
        "점수·등급",
        "시장·경쟁 원문",
        "수요 원문",
        "비용·예산 원문",
        "대안 상권",
        "원천 계보",
    }
    semantic_categories = {"서사 안전성", "서사 의미 검증", "외부 자료", "공개 표현"}
    artifact_categories = {"산출물 무결성", "시각 품질"}

    def category_status(selected: set[str]) -> str:
        return (
            "PASS"
            if all(
                row["decision"] == "PASS"
                for row in results
                if row["category"] in selected
            )
            else "FAIL"
        )

    summary = {
        "protocol_version": PROTOCOL_VERSION,
        "evaluated_at": datetime.now().astimezone().isoformat(),
        "evaluator": EVALUATOR_ID,
        "report_generator_model": report.get("ai_model"),
        "question_count": len(results),
        "pass_count": sum(row["decision"] == "PASS" for row in results),
        "fail_count": len(current_failures),
        "hard_fail_count": len(hard_failures),
        "overall_status": "FAIL" if hard_failures else "PASS",
        "db_grounding_status": category_status(db_categories),
        "automatic_contract_status": category_status({"자동 검증 계약", "모델·실행 추적"}),
        "semantic_quality_status": category_status(semantic_categories),
        "artifact_quality_status": category_status(artifact_categories),
        "negative_control_status": (
            "PASS"
            if all(row["decision"] == "PASS" for row in negative_controls)
            else "FAIL"
        ),
        "categories": categories,
        "failed_question_ids": [row["id"] for row in current_failures],
        "hard_failed_question_ids": [row["id"] for row in hard_failures],
        "decision_rule_ko": (
            "gate=True 문항이 하나라도 FAIL이면 전체 FAIL. "
            "DB 원값 정합성, 자동 critic, 의미 방향, 출처 관련성, "
            "PDF/차트 품질을 서로 대체하지 않고 별도 판정."
        ),
    }

    request = {
        **resolved_request,
        "artifact_dir": str(artifact_dir),
        "report_json": str(report_json_path),
    }
    payload_metadata = (
        {
            key: value
            for key, value in report_payload.items()
            if key != "report"
        }
        if isinstance(report_payload.get("report"), dict)
        else {}
    )
    report_response = {
        "source": "artifact JSON",
        "source_file": _file_identity(report_json_path),
        "source_payload_metadata": payload_metadata,
        "report": report,
    }
    db_reference = {
        "database": str(db_path),
        "read_mode": "SQLite URI mode=ro; explicit read transaction",
        "pragma": {
            "data_version": data_version,
            "schema_version": schema_version,
            "user_version": user_version,
        },
        "request": request,
        "queries": reference,
    }
    source_catalog = {
        "database_files": [
            _file_identity(db_path),
            _file_identity(Path(str(db_path) + "-wal")),
            _file_identity(Path(str(db_path) + "-shm")),
        ],
        "report_artifacts": [
            _file_identity(report_json_path),
            _file_identity(artifact_dir / "report.md"),
            _file_identity(artifact_dir / "report.pdf"),
            _file_identity(artifact_dir / ".public-presentation-version"),
        ],
        "gold_manifest": {
            "file": _file_identity(DEFAULT_GOLD_MANIFEST),
            "rows": gold_rows,
        },
        "score_manifest": {
            "file": _file_identity(DEFAULT_SCORE_MANIFEST),
            "content": score_manifest,
        },
        "score_batch": _file_identity(DEFAULT_SCORE_BATCH),
        "weight_file": {
            "file": _file_identity(DEFAULT_WEIGHT_FILE),
            "selected_weight_set": weight_set,
            "rows": weight_rows,
        },
        "manual_visual_review": {
            "file": _file_identity(manual_review_path),
            "content": manual_review,
        },
        "lineage": [
            {
                "report_metric": "매출",
                "db_table": "district_sales",
                "gold_table": "gold_sales_strength_q_industry.csv",
                "provider_dataset": "서울 상권분석서비스 추정매출-상권",
            },
            {
                "report_metric": "점포수",
                "db_table": "district_store_count",
                "gold_table": "gold_competition_q_industry.csv",
                "provider_dataset": "서울 상권분석서비스 점포-상권",
            },
            {
                "report_metric": "상주·직장·유동인구",
                "db_table": "district_population / district_floating",
                "gold_table": "gold_demand_q_area.csv",
                "provider_dataset": (
                    "서울 상권분석서비스 상주인구-상권 / 직장인구-상권 / 길단위인구-상권"
                ),
            },
            {
                "report_metric": "4축 점수",
                "db_table": "rule_location_score",
                "gold_table": "score batch manifest가 지정한 Gold 축 조합",
                "provider_dataset": "loc_score.v2.6-coverage-contract-rc1",
            },
            {
                "report_metric": "매매가 비용 프록시",
                "db_table": "area_sale_price_proxy",
                "gold_table": "gold_cost_risk_q_area.csv",
                "provider_dataset": "국토교통부 RTMS 상업·업무용 부동산 매매 실거래",
            },
            {
                "report_metric": "임대료·공실률 참고",
                "db_table": "area_rone_cost_reference",
                "gold_table": "evidence-only reference bridge",
                "provider_dataset": "한국부동산원 R-ONE",
            },
        ],
        "warning_ko": (
            "DB와 WAL은 서비스가 살아 있는 운영 파일이므로 물리 SHA는 해시 시점 식별자입니다. "
            "문항의 기준값은 read transaction에서 저장한 queries[*].rows가 권위 기준입니다."
        ),
    }
    manifest = {
        "protocol_version": PROTOCOL_VERSION,
        "evaluated_at": summary["evaluated_at"],
        "evaluator": {
            "model": EVALUATOR_ID,
            "role": "independent detailed-report evaluator",
        },
        "report_generator": {
            "model": report.get("ai_model"),
            "provider_models": report.get("token_usage", {}).get("provider_models"),
            "reasoning_effort": (
                report.get("reasoning_effort")
                or report.get("token_usage", {}).get("reasoning_effort")
            ),
            "generation_mode": report.get("generation_mode"),
        },
        "request": request,
        "overall_status": summary["overall_status"],
        "decision_rule": summary["decision_rule_ko"],
        "files": {},
    }

    _json_dump(output_dir / "report_response.raw.json", report_response)
    _json_dump(output_dir / "db_reference.raw.json", db_reference)
    _json_dump(output_dir / "source_catalog.json", source_catalog)
    _json_dump(
        output_dir / "question_set.json",
        [
            {
                key: row.get(key)
                for key in (
                    "id",
                    "category",
                    "question_ko",
                    "severity",
                    "gate",
                    "method",
                    "report_path",
                    "comparator",
                    "tolerance",
                    "unit",
                    "source_query_ids",
                    "source_tables",
                    "source_artifacts",
                )
            }
            for row in results
        ],
    )
    with (output_dir / "question_results.jsonl").open("w", encoding="utf-8") as handle:
        for row in results:
            handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    _write_csv(output_dir / "question_results.csv", results)
    _write_question_set_markdown(output_dir / "QUESTION_SET_KO.md", results)
    _json_dump(output_dir / "negative_controls.json", negative_controls)
    _json_dump(output_dir / "manual_visual_review.json", manual_review)
    _json_dump(output_dir / "historical_failure_cases.json", historical)
    _json_dump(output_dir / "current_failure_cases.json", current_failures)
    _json_dump(output_dir / "summary.json", summary)
    _write_queries(output_dir / "queries_used.sql", reference)
    _write_reproduction_script(
        output_dir / "reproduce.ps1",
        db_path=db_path,
        artifact_dir=artifact_dir,
        report_json_path=report_json_path,
        output_dir=output_dir,
        request=request,
    )
    _json_dump(output_dir / "visual_qa" / "render_result.json", render_result)
    _write_report(
        output_dir / "EVALUATION_REPORT_KO.md",
        results=results,
        summary=summary,
        request=request,
        negative_controls=negative_controls,
        historical=historical,
        artifact_dir=artifact_dir,
    )

    for name in (
        "report_response.raw.json",
        "db_reference.raw.json",
        "source_catalog.json",
        "question_set.json",
        "question_results.jsonl",
        "question_results.csv",
        "QUESTION_SET_KO.md",
        "negative_controls.json",
        "manual_visual_review.json",
        "historical_failure_cases.json",
        "current_failure_cases.json",
        "summary.json",
        "queries_used.sql",
        "reproduce.ps1",
        "EVALUATION_REPORT_KO.md",
        "visual_qa/render_result.json",
    ):
        manifest["files"][name] = _file_identity(output_dir / name)
    _json_dump(output_dir / "evaluation_manifest.json", manifest)

    if args.question_id:
        selected = next(
            (row for row in results if row["id"] == args.question_id), None
        )
        if selected is None:
            print(f"unknown question id: {args.question_id}", file=sys.stderr)
            return 2
        print(json.dumps(selected, ensure_ascii=False, indent=2, default=str))
    else:
        print(json.dumps(summary, ensure_ascii=False, indent=2))

    if summary["overall_status"] == "FAIL" and not args.no_fail_exit:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
