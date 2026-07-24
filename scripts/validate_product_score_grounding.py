#!/usr/bin/env python3
"""Validate the released location score from batch through product report output.

The validator is intentionally an executable audit record, not a test fixture.  It
checks every latest-batch row against commercial.db, recalculates every official
WLC score, verifies every withheld and area-context row, then traces one
deterministic official row through the production map/report score-selection
services.  News and LLM calls remain blocked so this record is reproducible.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
FINAL_PROJ = ROOT / "final_proj"
BACKEND = FINAL_PROJ / "backend"


def environment_path(name: str, default: Path) -> Path:
    raw = os.getenv(name, "").strip()
    path = Path(raw).expanduser() if raw else default
    if not path.is_absolute():
        path = FINAL_PROJ / path
    return path.resolve()


RUNTIME_ROOT = environment_path("LOCALFIT_RUNTIME_ROOT", FINAL_PROJ / "runtime")
DATA_ROOT = environment_path("LOCALFIT_DATA_ROOT", ROOT / "datacorpus")
DEFAULT_DB = environment_path(
    "LOCALFIT_DATABASE_PATH", RUNTIME_ROOT / "db" / "commercial.db"
)
DEFAULT_OUTPUT = (
    DATA_ROOT
    / "_score_predictive_validation"
    / "product_score_grounding_v1_3_20260718_strict_grade_display"
)
SCORE_OUTPUT_DIR = DATA_ROOT / "_location_judgement_outputs"
WEIGHTS_PATH = (
    DATA_ROOT
    / "_score_backtest"
    / "location_score_backtest_recommended_weights.csv"
)
TRACE_SUMMARY_PATH = (
    DATA_ROOT
    / "_rule_validation"
    / "98_algorithm_evidence_traceability_summary.json"
)
TRACE_CSV_PATH = (
    DATA_ROOT
    / "_rule_validation"
    / "98_algorithm_evidence_traceability.csv"
)
TRACE_VALIDATION_CSV_PATH = (
    DATA_ROOT
    / "_rule_validation"
    / "98_algorithm_evidence_traceability_validation.csv"
)
SALES_BACKTEST_PATH = (
    DATA_ROOT
    / "_score_backtest"
    / "location_score_backtest_summary.json"
)
SURVIVAL_BACKTEST_PATH = (
    DATA_ROOT
    / "_score_predictive_validation"
    / "business_survival_v1_20260717"
    / "validation_summary.json"
)
LOCALDATA_BUSINESS_SILVER_PATH = (
    DATA_ROOT / "_silver" / "silver_localdata_business_license.csv"
)

KEY_COLUMNS = ["기준_년분기_코드", "상권_코드", "서비스_업종_코드"]
DB_KEY_COLUMNS = ["quarter", "area_code", "industry_code"]
EXACT_TOLERANCE = 1e-9
# The released axes are rounded to two decimals after the total was calculated
# from higher-precision axes.  Recalculation from the published axes can
# therefore differ by one published score quantum (0.01), but never more.
PUBLISHED_AXIS_QUANTUM_TOLERANCE = 0.010001
FIELD_MAP = {
    "current_location_score": "current_location_score",
    "context_location_score": "context_location_score",
    "grade": "grade",
    "decision_label": "decision_label",
    "score_coverage_tier": "score_coverage_tier",
    "available_axis_count": "available_axis_count",
    "official_indicator_count": "official_indicator_count",
    "official_indicator_defined_count": "official_indicator_defined_count",
    "official_indicator_complete": "official_indicator_complete",
    "missing_axes": "missing_axes",
    "coverage_reason": "coverage_reason",
    "taxonomy_direct_score_allowed": "taxonomy_direct_score_allowed",
    "official_rank_eligible": "official_rank_eligible",
    "data_reliability_score": "data_reliability_score",
    "axis__sales": "axis_sales",
    "axis__competition": "axis_competition",
    "axis__demand": "axis_demand",
    "axis__accessibility": "axis_accessibility",
    "score_version": "score_version",
}
FLOAT_FIELDS = {
    "current_location_score",
    "context_location_score",
    "data_reliability_score",
    "axis__sales",
    "axis__competition",
    "axis__demand",
    "axis__accessibility",
}
INTEGER_FIELDS = {
    "available_axis_count",
    "official_indicator_count",
    "official_indicator_defined_count",
}
BOOLEAN_FIELDS = {
    "official_indicator_complete",
    "taxonomy_direct_score_allowed",
    "official_rank_eligible",
}
AXIS_COMPONENTS = {
    "sales": "axis__sales",
    "competition": "axis__competition",
    "demand": "axis__demand",
    "accessibility": "axis__accessibility",
}
AXIS_DISPLAY_LABELS = {
    "시장성": "axis_sales",
    "경쟁 구조": "axis_competition",
    "수요 기반": "axis_demand",
    "접근·유입": "axis_accessibility",
}
DISPLAY_GRADES = ("E", "E+", "D", "D+", "C", "C+", "B", "B+", "A", "A+")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


def resolve_reference(value: str | Path) -> Path:
    candidate = Path(str(value).replace("\\", "/"))
    return candidate if candidate.is_absolute() else ROOT / candidate


def is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def as_float(value: Any) -> float | None:
    if is_missing(value) or str(value).strip() == "":
        return None
    return float(value)


def as_int(value: Any) -> int | None:
    number = as_float(value)
    return None if number is None else int(number)


def as_bool(value: Any) -> bool | None:
    if is_missing(value) or str(value).strip() == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(int(value))
    text = str(value).strip().lower()
    if text in {"1", "true", "t", "yes", "y"}:
        return True
    if text in {"0", "false", "f", "no", "n"}:
        return False
    raise ValueError(f"cannot parse boolean: {value!r}")


def as_text(value: Any) -> str:
    return "" if is_missing(value) else str(value).strip()


def display(value: Any) -> str:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if value is None or is_missing(value):
        return "null"
    return str(value)


def add_check(
    checks: list[dict[str, Any]],
    check_id: str,
    group: str,
    passed: bool,
    *,
    actual: Any = None,
    expected: Any = None,
    detail: str = "",
    sample_type: str = "",
) -> None:
    checks.append(
        {
            "check_id": check_id,
            "group": group,
            "sample_type": sample_type,
            "status": "PASS" if passed else "FAIL",
            "actual": display(actual),
            "expected": display(expected),
            "detail": detail,
        }
    )


def latest_manifest(explicit: str | None) -> tuple[Path, dict[str, Any]]:
    candidates = [Path(explicit)] if explicit else list(SCORE_OUTPUT_DIR.glob("*.manifest.json"))
    valid: list[tuple[str, Path, dict[str, Any]]] = []
    for raw in candidates:
        path = raw if raw.is_absolute() else ROOT / raw
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != "localfit.score_batch_manifest.v1":
            continue
        batch = resolve_reference(payload.get("batch_path", ""))
        if batch.exists():
            valid.append((str(payload.get("generated_at") or ""), path, payload))
    if not valid:
        raise FileNotFoundError("usable localfit.score_batch_manifest.v1 was not found")
    _, path, payload = max(valid, key=lambda item: (item[0], str(item[1])))
    return path, payload


def equivalent(batch_value: Any, db_value: Any, batch_field: str) -> tuple[bool, float | None]:
    if batch_field in FLOAT_FIELDS:
        left, right = as_float(batch_value), as_float(db_value)
        if left is None or right is None:
            return left is None and right is None, None
        diff = abs(left - right)
        return diff <= 1e-9, diff
    if batch_field in INTEGER_FIELDS:
        return as_int(batch_value) == as_int(db_value), None
    if batch_field in BOOLEAN_FIELDS:
        return as_bool(batch_value) == as_bool(db_value), None
    return as_text(batch_value) == as_text(db_value), None


def boolean_series(values: pd.Series) -> pd.Series:
    """Normalize CSV/SQLite boolean representations without treating null as false."""
    return values.map(as_bool)


def display_grade_from_percentile(
    percentile: float | None,
    base_grade: str | None = None,
) -> str | None:
    """Map a cumulative percentile to the public A+..E display contract."""
    if percentile is None or not math.isfinite(float(percentile)):
        return None
    value = float(percentile)
    if not 0.0 <= value <= 100.0:
        return None
    base = as_text(base_grade).upper()
    plus_threshold = {"A": 90.0, "B": 70.0, "C": 50.0, "D": 30.0, "E": 10.0}
    if base in plus_threshold:
        return f"{base}+" if value > plus_threshold[base] else base
    if value > 90.0:
        return "A+"
    if value > 80.0:
        return "A"
    if value > 70.0:
        return "B+"
    if value > 60.0:
        return "B"
    if value > 50.0:
        return "C+"
    if value > 40.0:
        return "C"
    if value > 30.0:
        return "D+"
    if value > 20.0:
        return "D"
    if value > 10.0:
        return "E+"
    return "E"


def score_to_display_grade(score: Any) -> str | None:
    """Independently replay the ten-band display contract for a 0..100 axis score."""
    value = as_float(score)
    if value is None or not math.isfinite(value) or not 0.0 <= value <= 100.0:
        return None
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


def base_grade_from_percentile(percentile: float | None) -> str | None:
    if percentile is None or not math.isfinite(float(percentile)):
        return None
    value = float(percentile)
    if value > 80.0:
        return "A"
    if value > 60.0:
        return "B"
    if value > 40.0:
        return "C"
    if value > 20.0:
        return "D"
    return "E"


def series_match(
    left: pd.Series,
    right: pd.Series,
    batch_field: str,
) -> tuple[pd.Series, float | None]:
    """Vectorized equivalent() used by the full latest-batch parity gate."""
    if batch_field in FLOAT_FIELDS:
        left_number = pd.to_numeric(left, errors="coerce")
        right_number = pd.to_numeric(right, errors="coerce")
        comparable = left_number.notna() & right_number.notna()
        differences = (left_number - right_number).abs()
        same = (left_number.isna() & right_number.isna()) | (
            comparable & differences.le(EXACT_TOLERANCE)
        )
        max_difference = (
            float(differences.loc[comparable].max()) if comparable.any() else None
        )
        return same, max_difference
    if batch_field in INTEGER_FIELDS:
        left_number = pd.to_numeric(left, errors="coerce")
        right_number = pd.to_numeric(right, errors="coerce")
        return (
            (left_number.isna() & right_number.isna()) | left_number.eq(right_number),
            None,
        )
    if batch_field in BOOLEAN_FIELDS:
        left_bool = boolean_series(left)
        right_bool = boolean_series(right)
        return (
            (left_bool.isna() & right_bool.isna()) | left_bool.eq(right_bool),
            None,
        )
    left_text = left.fillna("").astype(str).str.strip()
    right_text = right.fillna("").astype(str).str.strip()
    return left_text.eq(right_text), None


def recompute_grades(scores: pd.Series, industries: pd.Series) -> pd.Series:
    """Reproduce the builder's within-industry five-band grade contract."""

    def grade_group(group: pd.Series) -> pd.Series:
        valid = group.dropna()
        if len(valid) < 5:
            return pd.Series("C", index=group.index).where(group.notna(), None)
        percent_rank = valid.rank(pct=True)
        bands = pd.cut(
            percent_rank,
            [0, 0.2, 0.4, 0.6, 0.8, 1.0],
            labels=["E", "D", "C", "B", "A"],
        )
        return bands.reindex(group.index)

    frame = pd.DataFrame({"industry": industries, "score": scores})
    return frame.groupby("industry", dropna=False)["score"].transform(grade_group).astype(object)


def db_score_row(conn: sqlite3.Connection, row: pd.Series) -> dict[str, Any] | None:
    found = conn.execute(
        """
        SELECT *
        FROM rule_location_score
        WHERE quarter = ? AND area_code = ? AND industry_code = ?
        """,
        tuple(as_text(row[column]) for column in KEY_COLUMNS),
    ).fetchone()
    return dict(found) if found is not None else None


def select_samples(batch: pd.DataFrame) -> dict[str, pd.Series]:
    eligible = batch["official_rank_eligible"].map(as_bool).fillna(False)
    current = pd.to_numeric(batch["current_location_score"], errors="coerce")
    axes_complete = batch[list(AXIS_COMPONENTS.values())].notna().all(axis=1)
    official = batch.loc[eligible & current.notna() & axes_complete].copy()
    if official.empty:
        raise RuntimeError("latest batch has no official fully observed row")
    official["_score_sort"] = pd.to_numeric(
        official["current_location_score"], errors="coerce"
    )
    official = official.sort_values(
        ["_score_sort", "상권_코드", "서비스_업종_코드"],
        ascending=[False, True, True],
        kind="mergesort",
    )

    withheld = batch.loc[~eligible & current.isna()].copy()
    if withheld.empty:
        withheld = batch.loc[~eligible].copy()
    if withheld.empty:
        raise RuntimeError("latest batch has no withheld row")
    withheld["_has_context"] = pd.to_numeric(
        withheld["context_location_score"], errors="coerce"
    ).notna()
    withheld["_axis_count"] = pd.to_numeric(
        withheld["available_axis_count"], errors="coerce"
    ).fillna(-1)
    withheld = withheld.sort_values(
        ["_has_context", "_axis_count", "상권_코드", "서비스_업종_코드"],
        ascending=[False, False, True, True],
        kind="mergesort",
    )
    return {"official": official.iloc[0], "withheld": withheld.iloc[0]}


def deterministic_interpreter(payload: dict[str, Any]) -> dict[str, Any]:
    """Run the production deterministic report path without news or LLM calls."""
    from app.services import interpretive_report as report_module

    pack = report_module.build_indicator_pack(payload)
    fallback = report_module._base_single_interpretation(payload, pack)
    data = report_module._sanitize_claims(fallback.model_dump())
    data["header_block"] = report_module._default_header_block(
        payload, data.get("header_block")
    )
    clean_result = report_module.SingleInterpretation(**data)
    data["markdown_body"] = report_module._sanitize_claims(
        report_module._single_markdown(payload, clean_result, None, [])
    )
    data["indicator_pack"] = pack
    data["facts_pack_display"] = pack.get("facts_pack_display") or {}
    data["facts_lite_display"] = pack.get("facts_lite_display") or {}
    data["chart_manifest"] = pack.get("chart_manifest") or []
    data["evidence_frames"] = []
    data["news_evidence"] = []
    data["validation_issues"] = []
    data["quality_status"] = "deterministic_local_fallback"
    data["ai_model"] = None
    data["ai_generated"] = False
    return data


def report_text(response: dict[str, Any]) -> str:
    public_fields = [
        "summary",
        "strengths",
        "weaknesses",
        "risk_factors",
        "header_block",
        "narrative_title",
        "thesis",
        "executive_interpretation",
        "score_interpretation",
        "axis_interpretations",
        "trend_analysis",
        "alternatives",
        "user_fit",
        "evidence_basis",
        "source_citations",
        "methodology_notes",
        "action_plan",
        "onsite_checklist",
        "limitations",
        "markdown_body",
    ]
    return json.dumps(
        {field: response.get(field) for field in public_fields}, ensure_ascii=False
    )


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def run_validation(
    *,
    db_path: Path,
    manifest_path_arg: str | None,
    checks: list[dict[str, Any]],
) -> dict[str, Any]:
    midpoint_cases = [
        ("E", 10.0, "E", "E"),
        ("E", 10.0001, "E+", "E+"),
        ("D", 30.0, "D", "D"),
        ("D", 30.0001, "D+", "D+"),
        ("C", 50.0, "C", "C"),
        ("C", 50.0001, "C+", "C+"),
        ("B", 70.0, "B", "B"),
        ("B", 70.0001, "B+", "B+"),
        ("A", 90.0, "A", "A"),
        ("A", 90.0001, "A+", "A+"),
    ]
    midpoint_results = [
        {
            "base_grade": base,
            "value": value,
            "percentile_grade": display_grade_from_percentile(value, base),
            "axis_grade": score_to_display_grade(value),
            "expected_percentile_grade": expected_percentile,
            "expected_axis_grade": expected_axis,
        }
        for base, value, expected_percentile, expected_axis in midpoint_cases
    ]
    invalid_grade_inputs = {
        "percentile_negative": display_grade_from_percentile(-0.01, "E"),
        "percentile_over_100": display_grade_from_percentile(100.01, "A"),
        "percentile_nan": display_grade_from_percentile(float("nan"), "C"),
        "axis_negative": score_to_display_grade(-0.01),
        "axis_over_100": score_to_display_grade(100.01),
        "axis_inf": score_to_display_grade(float("inf")),
    }
    add_check(
        checks,
        "grade.strict_midpoint_and_invalid_input_contract",
        "grade_contract",
        all(
            item["percentile_grade"] == item["expected_percentile_grade"]
            and item["axis_grade"] == item["expected_axis_grade"]
            for item in midpoint_results
        )
        and all(value is None for value in invalid_grade_inputs.values()),
        actual={"midpoints": midpoint_results, "invalid_inputs": invalid_grade_inputs},
        expected="midpoints stay in the base grade; only values above them receive plus; invalid values return null",
    )

    manifest_path, manifest = latest_manifest(manifest_path_arg)
    batch_path = resolve_reference(manifest["batch_path"])
    batch_hash = sha256_file(batch_path)
    add_check(
        checks,
        "manifest.batch_sha256",
        "release_artifact",
        batch_hash == manifest.get("batch_sha256"),
        actual=batch_hash,
        expected=manifest.get("batch_sha256"),
        detail=rel(batch_path),
    )
    gold_manifest_path = resolve_reference(manifest["gold_manifest_path"])
    gold_hash_error: str | None = None
    try:
        gold_hash = sha256_file(gold_manifest_path) if gold_manifest_path.exists() else None
    except OSError as exc:
        # A concurrently running release may hold this tiny CSV exclusively on
        # Windows.  Record the gate failure but continue with the product checks.
        gold_hash = None
        gold_hash_error = f"{type(exc).__name__}: {exc}"
    add_check(
        checks,
        "manifest.gold_manifest_sha256",
        "release_artifact",
        gold_hash == manifest.get("gold_manifest_sha256"),
        actual=gold_hash,
        expected=manifest.get("gold_manifest_sha256"),
        detail=(
            f"{rel(gold_manifest_path)}"
            + (f"; read_error={gold_hash_error}" if gold_hash_error else "")
        ),
    )

    batch = pd.read_csv(batch_path, dtype=str, low_memory=False, encoding="utf-8-sig")
    required = set(KEY_COLUMNS) | set(FIELD_MAP) | {
        "상권_코드_명",
        "자치구_코드",
        "자치구_코드_명",
        "서비스_업종_코드_명",
        "weight_set",
    }
    missing_columns = sorted(required - set(batch.columns))
    add_check(
        checks,
        "batch.required_columns",
        "release_artifact",
        not missing_columns,
        actual=missing_columns,
        expected=[],
    )
    if missing_columns:
        raise RuntimeError(f"batch missing required columns: {missing_columns}")
    add_check(
        checks,
        "batch.row_count",
        "release_artifact",
        len(batch) == int(manifest["row_count"]),
        actual=len(batch),
        expected=manifest["row_count"],
    )
    batch_quarters = sorted(batch["기준_년분기_코드"].dropna().astype(str).unique())
    add_check(
        checks,
        "batch.analysis_quarter",
        "release_artifact",
        batch_quarters == [str(manifest["analysis_quarter"])],
        actual=batch_quarters,
        expected=[str(manifest["analysis_quarter"])],
    )
    batch_versions = sorted(batch["score_version"].dropna().astype(str).unique())
    add_check(
        checks,
        "batch.score_version",
        "release_artifact",
        batch_versions == [str(manifest["score_version"])],
        actual=batch_versions,
        expected=[str(manifest["score_version"])],
    )

    samples = select_samples(batch)
    all_row_parity: dict[str, Any] = {}
    score_population_audit: dict[str, Any] = {}
    area_population_audit: dict[str, Any] = {}
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        latest_db_quarter = str(
            conn.execute("SELECT MAX(quarter) FROM rule_location_score").fetchone()[0]
        )
        add_check(
            checks,
            "db.latest_quarter",
            "batch_db",
            latest_db_quarter == str(manifest["analysis_quarter"]),
            actual=latest_db_quarter,
            expected=manifest["analysis_quarter"],
        )

        batch_column_map = dict(zip(KEY_COLUMNS, DB_KEY_COLUMNS))
        batch_column_map.update(FIELD_MAP)
        batch_db_view = batch[list(batch_column_map)].rename(
            columns=batch_column_map
        )
        db_select_columns = DB_KEY_COLUMNS + list(FIELD_MAP.values())
        db_all = pd.read_sql_query(
            "SELECT "
            + ", ".join(db_select_columns)
            + " FROM rule_location_score WHERE quarter = ?",
            conn,
            params=[str(manifest["analysis_quarter"])],
            dtype=str,
        )
        batch_duplicate_count = int(
            batch_db_view.duplicated(DB_KEY_COLUMNS, keep=False).sum()
        )
        db_duplicate_count = int(db_all.duplicated(DB_KEY_COLUMNS, keep=False).sum())
        add_check(
            checks,
            "all_rows.unique_keys",
            "batch_db_population",
            batch_duplicate_count == 0 and db_duplicate_count == 0,
            actual={
                "batch_duplicate_rows": batch_duplicate_count,
                "db_duplicate_rows": db_duplicate_count,
            },
            expected={"batch_duplicate_rows": 0, "db_duplicate_rows": 0},
        )
        if batch_duplicate_count or db_duplicate_count:
            raise RuntimeError("batch or DB contains duplicate score keys")

        all_rows = batch_db_view.merge(
            db_all,
            on=DB_KEY_COLUMNS,
            how="outer",
            suffixes=("_batch", "_db"),
            indicator=True,
            validate="one_to_one",
        )
        key_counts = {
            key: int(value)
            for key, value in all_rows["_merge"].value_counts().to_dict().items()
        }
        expected_both = int(manifest["row_count"])
        add_check(
            checks,
            "all_rows.key_set_and_count",
            "batch_db_population",
            key_counts.get("both", 0) == expected_both
            and key_counts.get("left_only", 0) == 0
            and key_counts.get("right_only", 0) == 0
            and len(batch_db_view) == len(db_all) == expected_both,
            actual={
                "batch_rows": len(batch_db_view),
                "db_rows": len(db_all),
                "key_merge": key_counts,
            },
            expected={
                "batch_rows": expected_both,
                "db_rows": expected_both,
                "key_merge": {
                    "both": expected_both,
                    "left_only": 0,
                    "right_only": 0,
                },
            },
        )
        parity_mismatches: dict[str, int] = {}
        parity_max_differences: dict[str, float | None] = {}
        for batch_field, db_field in FIELD_MAP.items():
            same, max_difference = series_match(
                all_rows[f"{db_field}_batch"],
                all_rows[f"{db_field}_db"],
                batch_field,
            )
            mismatch_count = int((~same).sum())
            parity_mismatches[db_field] = mismatch_count
            parity_max_differences[db_field] = max_difference
            examples = all_rows.loc[~same, DB_KEY_COLUMNS].head(3).to_dict("records")
            add_check(
                checks,
                f"all_rows.field.{db_field}",
                "batch_db_population",
                mismatch_count == 0,
                actual=mismatch_count,
                expected=0,
                detail=(
                    f"max_abs_diff={max_difference}; examples={display(examples)}"
                    if max_difference is not None or examples
                    else "full latest-quarter comparison"
                ),
            )
        all_row_parity = {
            "batch_rows": len(batch_db_view),
            "db_rows": len(db_all),
            "key_merge": key_counts,
            "field_mismatch_counts": parity_mismatches,
            "field_max_abs_differences": parity_max_differences,
        }

        db_rows: dict[str, dict[str, Any]] = {}
        for sample_type, sample in samples.items():
            db_row = db_score_row(conn, sample)
            add_check(
                checks,
                f"{sample_type}.db_row_exists",
                "batch_db",
                db_row is not None,
                actual=db_row is not None,
                expected=True,
                sample_type=sample_type,
                detail=(
                    f"quarter={as_text(sample['기준_년분기_코드'])}, "
                    f"area={as_text(sample['상권_코드'])}, "
                    f"industry={as_text(sample['서비스_업종_코드'])}"
                ),
            )
            if db_row is None:
                continue
            db_rows[sample_type] = db_row
            for batch_field, db_field in FIELD_MAP.items():
                passed, diff = equivalent(sample[batch_field], db_row[db_field], batch_field)
                add_check(
                    checks,
                    f"{sample_type}.field.{db_field}",
                    "batch_db",
                    passed,
                    actual=sample[batch_field],
                    expected=db_row[db_field],
                    sample_type=sample_type,
                    detail=(f"abs_diff={diff:.12g}" if diff is not None else "exact/null-aware"),
                )

        if "official" not in db_rows:
            raise RuntimeError("official sample was not found in product DB")
        official = samples["official"]
        official_db = db_rows["official"]

        weights = pd.read_csv(WEIGHTS_PATH, encoding="utf-8-sig")
        weight_set = as_text(official["weight_set"])
        selected_weights = weights[
            weights["weight_set"].astype(str).eq(weight_set)
            & weights["component"].isin(AXIS_COMPONENTS)
        ].copy()
        selected_weights["recommended_weight"] = pd.to_numeric(
            selected_weights["recommended_weight"], errors="coerce"
        )
        selected_weights = selected_weights.dropna(subset=["recommended_weight"])
        component_weights = dict(
            zip(selected_weights["component"], selected_weights["recommended_weight"])
        )
        add_check(
            checks,
            "official.weight_components",
            "score_recalculation",
            set(component_weights) == set(AXIS_COMPONENTS),
            actual=sorted(component_weights),
            expected=sorted(AXIS_COMPONENTS),
            detail=f"weight_set={weight_set}",
            sample_type="official",
        )
        weight_sum = sum(float(component_weights.get(component, 0.0)) for component in AXIS_COMPONENTS)
        axis_values = {
            component: as_float(official[column])
            for component, column in AXIS_COMPONENTS.items()
        }
        if set(component_weights) == set(AXIS_COMPONENTS) and all(
            value is not None for value in axis_values.values()
        ) and weight_sum > 0:
            raw_recalculated = sum(
                float(axis_values[component]) * float(component_weights[component])
                for component in AXIS_COMPONENTS
            ) / weight_sum
            rounded_recalculated = round(raw_recalculated, 2)
            reported = as_float(official["current_location_score"])
            score_diff = (
                abs(float(reported) - rounded_recalculated) if reported is not None else math.inf
            )
            add_check(
                checks,
                "official.four_axis_weighted_score",
                "score_recalculation",
                score_diff <= 0.005001,
                actual=reported,
                expected=rounded_recalculated,
                sample_type="official",
                detail=(
                    f"raw={raw_recalculated:.8f}; normalized_weights="
                    f"{display({k: round(v / weight_sum, 8) for k, v in component_weights.items()})}"
                ),
            )
        else:
            raw_recalculated = None
            rounded_recalculated = None
            add_check(
                checks,
                "official.four_axis_weighted_score",
                "score_recalculation",
                False,
                actual=axis_values,
                expected="four axes and four weights",
                sample_type="official",
            )

        # Population gate: recompute every published score from the released
        # two-decimal axes and the released weight table.  The builder computes
        # totals from higher-precision axes before publishing the axes, so an
        # exact two-decimal replay is diagnostic and a one-quantum (0.01) bound
        # is the enforceable released-artifact contract.
        weight_rows = weights.loc[
            weights["component"].isin(AXIS_COMPONENTS),
            ["weight_set", "component", "recommended_weight"],
        ].copy()
        duplicate_weight_rows = int(
            weight_rows.duplicated(["weight_set", "component"], keep=False).sum()
        )
        weight_rows["recommended_weight"] = pd.to_numeric(
            weight_rows["recommended_weight"], errors="coerce"
        )
        add_check(
            checks,
            "all_scores.unique_finite_weights",
            "score_population",
            duplicate_weight_rows == 0
            and weight_rows["recommended_weight"].notna().all()
            and weight_rows["recommended_weight"].gt(0).all(),
            actual={
                "duplicate_rows": duplicate_weight_rows,
                "invalid_rows": int(weight_rows["recommended_weight"].isna().sum()),
                "nonpositive_rows": int(weight_rows["recommended_weight"].le(0).sum()),
            },
            expected={
                "duplicate_rows": 0,
                "invalid_rows": 0,
                "nonpositive_rows": 0,
            },
        )
        if duplicate_weight_rows:
            raise RuntimeError("weight table has duplicate weight_set/component rows")
        weight_matrix = weight_rows.pivot(
            index="weight_set", columns="component", values="recommended_weight"
        )
        missing_weight_components = sorted(set(AXIS_COMPONENTS) - set(weight_matrix.columns))
        weight_matrix = weight_matrix.reindex(columns=list(AXIS_COMPONENTS))
        invalid_weight_sets = weight_matrix.index[
            weight_matrix.isna().any(axis=1) | weight_matrix.sum(axis=1).le(0)
        ].astype(str).tolist()
        weight_matrix = weight_matrix.div(weight_matrix.sum(axis=1), axis=0)
        batch_weight_sets = batch["weight_set"].fillna("").astype(str).str.strip()
        unmapped_weight_sets = sorted(
            set(batch_weight_sets) - set(weight_matrix.index.astype(str))
        )
        add_check(
            checks,
            "all_scores.weight_set_coverage",
            "score_population",
            not missing_weight_components
            and not invalid_weight_sets
            and not unmapped_weight_sets,
            actual={
                "missing_components": missing_weight_components,
                "invalid_weight_sets": invalid_weight_sets,
                "unmapped_batch_weight_sets": unmapped_weight_sets,
            },
            expected={
                "missing_components": [],
                "invalid_weight_sets": [],
                "unmapped_batch_weight_sets": [],
            },
        )

        axis_frame = pd.DataFrame(
            {
                component: pd.to_numeric(batch[column], errors="coerce")
                for component, column in AXIS_COMPONENTS.items()
            }
        )
        available_axis_count = axis_frame.notna().sum(axis=1)
        weighted_total = pd.Series(0.0, index=batch.index)
        available_weight_total = pd.Series(0.0, index=batch.index)
        row_weight_complete = pd.Series(True, index=batch.index)
        for component in AXIS_COMPONENTS:
            row_component_weights = batch_weight_sets.map(
                weight_matrix[component].to_dict()
            )
            row_weight_complete &= row_component_weights.notna()
            present = axis_frame[component].notna() & row_component_weights.notna()
            weighted_total += (axis_frame[component] * row_component_weights).where(
                present, 0.0
            )
            available_weight_total += row_component_weights.where(present, 0.0)
        population_raw_score = (weighted_total / available_weight_total).where(
            available_weight_total.gt(0)
        )
        population_rounded_score = population_raw_score.round(2)
        reported_current = pd.to_numeric(
            batch["current_location_score"], errors="coerce"
        )
        reported_context = pd.to_numeric(
            batch["context_location_score"], errors="coerce"
        )
        official_mask = boolean_series(batch["official_rank_eligible"]).fillna(False).astype(bool)
        withheld_mask = ~official_mask
        official_count = int(official_mask.sum())
        official_recalculation_ready = (
            official_mask
            & available_axis_count.eq(len(AXIS_COMPONENTS))
            & row_weight_complete
            & reported_current.notna()
            & population_rounded_score.notna()
        )
        add_check(
            checks,
            "all_scores.official_recalculation_ready",
            "score_population",
            official_count > 0 and int(official_recalculation_ready.sum()) == official_count,
            actual={
                "official_rows": official_count,
                "recalculation_ready_rows": int(official_recalculation_ready.sum()),
            },
            expected={
                "official_rows": official_count,
                "recalculation_ready_rows": official_count,
            },
        )
        official_difference = (
            reported_current - population_rounded_score
        ).abs().where(official_mask)
        official_exact_count = int(official_difference.le(EXACT_TOLERANCE).sum())
        official_within_quantum_count = int(
            official_difference.le(PUBLISHED_AXIS_QUANTUM_TOLERANCE).sum()
        )
        official_max_difference = (
            float(official_difference.max())
            if official_difference.notna().any()
            else None
        )
        add_check(
            checks,
            "all_scores.official_within_published_axis_quantum",
            "score_population",
            official_within_quantum_count == official_count,
            actual={
                "official_rows": official_count,
                "exact_2dp_matches": official_exact_count,
                "within_0_01_matches": official_within_quantum_count,
                "max_abs_difference": official_max_difference,
            },
            expected={
                "within_0_01_matches": official_count,
                "max_abs_difference_lte": PUBLISHED_AXIS_QUANTUM_TOLERANCE,
            },
            detail=(
                "The total was calculated from hidden pre-rounding axis precision; "
                "published two-decimal axes may replay one 0.01 quantum away."
            ),
        )

        context_expected_mask = available_axis_count.ge(3)
        context_difference = (
            reported_context - population_rounded_score
        ).abs().where(context_expected_mask)
        context_within_quantum = int(
            context_difference.le(PUBLISHED_AXIS_QUANTUM_TOLERANCE).sum()
        )
        context_expected_count = int(context_expected_mask.sum())
        context_fail_closed_count = int(
            (available_axis_count.lt(3) & reported_context.notna()).sum()
        )
        add_check(
            checks,
            "all_scores.context_contract",
            "score_population",
            context_within_quantum == context_expected_count
            and context_fail_closed_count == 0,
            actual={
                "expected_context_rows": context_expected_count,
                "within_0_01_matches": context_within_quantum,
                "under_3axis_nonnull_context": context_fail_closed_count,
                "max_abs_difference": (
                    float(context_difference.max())
                    if context_difference.notna().any()
                    else None
                ),
            },
            expected={
                "within_0_01_matches": context_expected_count,
                "under_3axis_nonnull_context": 0,
            },
        )

        indicator_complete = boolean_series(
            batch["official_indicator_complete"]
        ).fillna(False).astype(bool)
        taxonomy_allowed = boolean_series(
            batch["taxonomy_direct_score_allowed"]
        ).fillna(False).astype(bool)
        reliability = pd.to_numeric(batch["data_reliability_score"], errors="coerce")
        expected_official = (
            available_axis_count.eq(4)
            & indicator_complete
            & taxonomy_allowed
            & reliability.ge(40.0)
        )
        eligibility_mismatch = int(expected_official.ne(official_mask).sum())
        add_check(
            checks,
            "all_scores.official_eligibility_contract",
            "score_population",
            eligibility_mismatch == 0,
            actual=eligibility_mismatch,
            expected=0,
            detail="4 axes + 12 official indicators + taxonomy + reliability >= 40",
        )

        expected_coverage = pd.Series(
            "insufficient_context", index=batch.index, dtype=object
        )
        expected_coverage.loc[available_axis_count.eq(3)] = "context_only_3axis"
        expected_coverage.loc[
            available_axis_count.eq(4) & ~indicator_complete
        ] = "context_only_partial_4axis"
        expected_coverage.loc[
            available_axis_count.eq(4) & indicator_complete
        ] = "full_4axis"
        coverage_mismatch = int(
            expected_coverage.ne(batch["score_coverage_tier"].fillna("").astype(str)).sum()
        )
        add_check(
            checks,
            "all_scores.coverage_tier_contract",
            "score_population",
            coverage_mismatch == 0,
            actual=coverage_mismatch,
            expected=0,
        )

        expected_grades = recompute_grades(
            reported_current,
            batch["서비스_업종_코드"],
        )
        grade_match = expected_grades.fillna("").astype(str).eq(
            batch["grade"].fillna("").astype(str)
        )
        grade_mismatch = int((~grade_match).sum())
        add_check(
            checks,
            "all_scores.grade_contract",
            "score_population",
            grade_mismatch == 0,
            actual=grade_mismatch,
            expected=0,
            detail="within-industry percentile five-band replay for all rows",
        )

        official_grade_frame = pd.DataFrame(
            {
                "quarter": batch.loc[official_mask, "기준_년분기_코드"].astype(str),
                "area_code": batch.loc[official_mask, "상권_코드"].astype(str),
                "industry": batch.loc[official_mask, "서비스_업종_코드"].astype(str),
                "score": reported_current.loc[official_mask],
                "base_grade": batch.loc[official_mask, "grade"].astype(str),
            }
        )
        official_grade_frame["percentile"] = (
            official_grade_frame.groupby("industry", dropna=False)["score"]
            .rank(method="max", pct=True)
            .mul(100.0)
        )
        official_grade_frame["display_grade"] = official_grade_frame.apply(
            lambda row: display_grade_from_percentile(row["percentile"], row["base_grade"]),
            axis=1,
        )
        display_base_mismatch = int(
            official_grade_frame["display_grade"].str[0].ne(official_grade_frame["base_grade"]).sum()
        )
        official_display_distribution = {
            str(key): int(value)
            for key, value in official_grade_frame["display_grade"].value_counts().sort_index().items()
        }
        add_check(
            checks,
            "all_scores.display_grade_preserves_base_grade",
            "score_population",
            display_base_mismatch == 0,
            actual={
                "mismatch_count": display_base_mismatch,
                "display_grade_distribution": official_display_distribution,
            },
            expected={"mismatch_count": 0},
        )

        withheld_nonnull_score = int(
            (withheld_mask & reported_current.notna()).sum()
        )
        withheld_nonnull_grade = int(
            (
                withheld_mask
                & batch["grade"].fillna("").astype(str).str.strip().ne("")
            ).sum()
        )
        add_check(
            checks,
            "all_scores.withheld_fail_closed",
            "score_population",
            withheld_nonnull_score == 0 and withheld_nonnull_grade == 0,
            actual={
                "withheld_rows": int(withheld_mask.sum()),
                "nonnull_official_score": withheld_nonnull_score,
                "nonnull_grade": withheld_nonnull_grade,
            },
            expected={
                "nonnull_official_score": 0,
                "nonnull_grade": 0,
            },
        )
        score_population_audit = {
            "official_rows": official_count,
            "withheld_rows": int(withheld_mask.sum()),
            "official_exact_2dp_matches": official_exact_count,
            "official_exact_2dp_rate": (
                official_exact_count / official_count if official_count else None
            ),
            "official_within_0_01_matches": official_within_quantum_count,
            "official_max_abs_difference": official_max_difference,
            "context_rows": context_expected_count,
            "context_exact_2dp_matches": int(
                context_difference.le(EXACT_TOLERANCE).sum()
            ),
            "context_within_0_01_matches": context_within_quantum,
            "context_max_abs_difference": (
                float(context_difference.max())
                if context_difference.notna().any()
                else None
            ),
            "eligibility_mismatch_count": eligibility_mismatch,
            "coverage_tier_mismatch_count": coverage_mismatch,
            "grade_mismatch_count": grade_mismatch,
            "display_grade_base_mismatch_count": display_base_mismatch,
            "display_grade_distribution": official_display_distribution,
            "withheld_nonnull_score_count": withheld_nonnull_score,
            "withheld_nonnull_grade_count": withheld_nonnull_grade,
            "published_axis_precision_caveat": (
                "The batch exposes axes at two decimals but calculates totals from "
                "higher-precision axes; exact two-decimal replay is not guaranteed, "
                "and the enforced maximum replay error is one 0.01 quantum."
            ),
        }

        area_code = as_text(official["상권_코드"])
        quarter = as_text(official["기준_년분기_코드"])
        area_summary_row = conn.execute(
            "SELECT * FROM rule_area_score_summary WHERE quarter = ? AND area_code = ?",
            (quarter, area_code),
        ).fetchone()
        add_check(
            checks,
            "area.summary_exists",
            "area_context",
            area_summary_row is not None,
            actual=area_summary_row is not None,
            expected=True,
            detail=f"quarter={quarter}, area={area_code}",
        )
        axis_aggregate = dict(
            conn.execute(
                """
                SELECT
                    COUNT(*) AS row_count,
                    COUNT(DISTINCT axis_demand) AS demand_variants,
                    COUNT(DISTINCT axis_accessibility) AS accessibility_variants,
                    MIN(axis_demand) AS demand_min,
                    MAX(axis_demand) AS demand_max,
                    MIN(axis_accessibility) AS accessibility_min,
                    MAX(axis_accessibility) AS accessibility_max
                FROM rule_location_score
                WHERE quarter = ? AND area_code = ?
                """,
                (quarter, area_code),
            ).fetchone()
        )
        axes_stable = (
            int(axis_aggregate["row_count"]) > 0
            and int(axis_aggregate["demand_variants"]) == 1
            and int(axis_aggregate["accessibility_variants"]) == 1
        )
        add_check(
            checks,
            "area.axes_identical_across_industries",
            "area_context",
            axes_stable,
            actual={
                "rows": axis_aggregate["row_count"],
                "demand_variants": axis_aggregate["demand_variants"],
                "accessibility_variants": axis_aggregate["accessibility_variants"],
            },
            expected={"demand_variants": 1, "accessibility_variants": 1},
        )
        area_expected = None
        area_summary = dict(area_summary_row) if area_summary_row is not None else None
        if axes_stable:
            # Seed uses pandas mean().round(2); keep the same IEEE-754/rounding
            # path instead of Python's scalar round at an x.xx5 boundary.
            area_expected = float(
                pd.Series(
                    [
                        float(axis_aggregate["demand_min"]),
                        float(axis_aggregate["accessibility_min"]),
                    ]
                )
                .mean()
                .round(2)
            )
        area_actual = as_float(area_summary.get("score")) if area_summary else None
        add_check(
            checks,
            "area.demand_accessibility_mean",
            "area_context",
            area_expected is not None
            and area_actual is not None
            and abs(area_actual - area_expected) <= 1e-9,
            actual=area_actual,
            expected=area_expected,
        )
        if area_summary:
            add_check(
                checks,
                "area.definition_contract",
                "area_context",
                area_summary["score_definition"]
                == "area_context_demand_accessibility_mean_v1"
                and area_summary["score_version"]
                == "area_context.demand_accessibility.v1"
                and int(area_summary["score_count"]) == 2
                and area_summary["top_industry_status"]
                == "withheld_no_cross_industry_calibration",
                actual={
                    "definition": area_summary["score_definition"],
                    "version": area_summary["score_version"],
                    "score_count": area_summary["score_count"],
                    "top_industry_status": area_summary["top_industry_status"],
                },
                expected={
                    "definition": "area_context_demand_accessibility_mean_v1",
                    "version": "area_context.demand_accessibility.v1",
                    "score_count": 2,
                    "top_industry_status": "withheld_no_cross_industry_calibration",
                },
            )

        population_axes = db_all[
            ["quarter", "area_code", "axis_demand", "axis_accessibility"]
        ].copy()
        for column in ["axis_demand", "axis_accessibility"]:
            population_axes[column] = pd.to_numeric(
                population_axes[column], errors="coerce"
            )
        area_variation = population_axes.groupby(
            ["quarter", "area_code"], dropna=False
        )[["axis_demand", "axis_accessibility"]].nunique(dropna=False)
        inconsistent_area_axes = area_variation.gt(1).any(axis=1)
        inconsistent_area_count = int(inconsistent_area_axes.sum())
        add_check(
            checks,
            "area.all_axes_identical_across_industries",
            "area_context_population",
            inconsistent_area_count == 0,
            actual={
                "inconsistent_area_count": inconsistent_area_count,
                "examples": [
                    {"quarter": str(key[0]), "area_code": str(key[1])}
                    for key in inconsistent_area_axes[inconsistent_area_axes].index[:5]
                ],
            },
            expected={"inconsistent_area_count": 0},
        )
        area_axis_rows = (
            population_axes.groupby(
                ["quarter", "area_code"], as_index=False, dropna=False, sort=False
            )
            .first()
        )
        expected_area_rows = area_axis_rows.dropna(
            subset=["axis_demand", "axis_accessibility"]
        ).copy()
        expected_area_rows["expected_score"] = (
            expected_area_rows[["axis_demand", "axis_accessibility"]]
            .mean(axis=1, skipna=False)
            .round(2)
        )
        area_summary_all = pd.read_sql_query(
            "SELECT quarter, area_code, score, score_mean, score_median, "
            "score_min, score_max, score_count, top_industry_code, "
            "top_industry_name, top_industry_status, score_definition, "
            "score_version FROM rule_area_score_summary WHERE quarter = ?",
            conn,
            params=[str(manifest["analysis_quarter"])],
        )
        area_population = expected_area_rows.merge(
            area_summary_all,
            on=["quarter", "area_code"],
            how="outer",
            indicator=True,
            validate="one_to_one",
        )
        area_key_counts = {
            key: int(value)
            for key, value in area_population["_merge"].value_counts().to_dict().items()
        }
        expected_area_count = len(expected_area_rows)
        add_check(
            checks,
            "area.all_summary_keys",
            "area_context_population",
            area_key_counts.get("both", 0) == expected_area_count
            and area_key_counts.get("left_only", 0) == 0
            and area_key_counts.get("right_only", 0) == 0
            and len(area_summary_all) == expected_area_count,
            actual={
                "expected_rows": expected_area_count,
                "summary_rows": len(area_summary_all),
                "key_merge": area_key_counts,
            },
            expected={
                "summary_rows": expected_area_count,
                "key_merge": {
                    "both": expected_area_count,
                    "left_only": 0,
                    "right_only": 0,
                },
            },
        )
        area_score_mismatch_counts: dict[str, int] = {}
        for column in ["score", "score_mean", "score_median", "score_min", "score_max"]:
            expected_number = pd.to_numeric(
                area_population["expected_score"], errors="coerce"
            )
            actual_number = pd.to_numeric(area_population[column], errors="coerce")
            same = (expected_number.isna() & actual_number.isna()) | (
                expected_number.notna()
                & actual_number.notna()
                & (expected_number - actual_number).abs().le(EXACT_TOLERANCE)
            )
            area_score_mismatch_counts[column] = int((~same).sum())
        add_check(
            checks,
            "area.all_demand_accessibility_means",
            "area_context_population",
            all(count == 0 for count in area_score_mismatch_counts.values()),
            actual=area_score_mismatch_counts,
            expected={column: 0 for column in area_score_mismatch_counts},
        )
        area_contract_match = (
            pd.to_numeric(area_population["score_count"], errors="coerce").eq(2)
            & area_population["top_industry_code"].isna()
            & area_population["top_industry_name"].isna()
            & area_population["top_industry_status"].eq(
                "withheld_no_cross_industry_calibration"
            )
            & area_population["score_definition"].eq(
                "area_context_demand_accessibility_mean_v1"
            )
            & area_population["score_version"].eq(
                "area_context.demand_accessibility.v1"
            )
        )
        area_contract_mismatch = int((~area_contract_match).sum())
        add_check(
            checks,
            "area.all_definition_contracts",
            "area_context_population",
            area_contract_mismatch == 0,
            actual=area_contract_mismatch,
            expected=0,
        )
        area_population_audit = {
            "area_groups": len(area_axis_rows),
            "areas_with_demand_and_accessibility": expected_area_count,
            "area_summary_rows": len(area_summary_all),
            "inconsistent_axis_area_count": inconsistent_area_count,
            "summary_key_merge": area_key_counts,
            "score_mismatch_counts": area_score_mismatch_counts,
            "definition_contract_mismatch_count": area_contract_mismatch,
        }

        ui_contracts = {
            FINAL_PROJ / "frontend" / "src" / "app" / "page.tsx": [
                "수요·접근성 맥락 등급",
                "displayGradeOrPending",
            ],
            FINAL_PROJ / "frontend" / "src" / "app" / "rankings" / "page.tsx": [
                "상권 수요·접근성 맥락 순위",
                "수요·접근성 등급",
                "displayGradeOrPending",
            ],
            FINAL_PROJ / "frontend" / "src" / "app" / "trade" / "page.tsx": [
                "상권 맥락 등급",
                "displayGradeOrPending",
            ],
            FINAL_PROJ / "frontend" / "src" / "app" / "ai" / "page.tsx": [
                "비워두면 수요·접근성 상권 맥락만 봅니다.",
                "입지 등급",
                "displayGradeOrPending",
                "userFacingMetricDisplay",
                "reportData.limitations",
            ],
            FINAL_PROJ / "frontend" / "src" / "app" / "reports" / "[id]" / "page.tsx": [
                "입지 등급",
                "displayGradeOrPending",
                "userFacingMetricDisplay",
                "data.limitations",
                "데이터 출처 및 산정 기준",
            ],
            FINAL_PROJ / "frontend" / "src" / "components" / "topbar.tsx": [
                "수요·접근",
                "displayGradeOrPending",
            ],
        }
        for path, tokens in ui_contracts.items():
            text = path.read_text(encoding="utf-8") if path.exists() else ""
            missing_tokens = [token for token in tokens if token not in text]
            add_check(
                checks,
                f"ui.{path.stem}.{sha256_file(path)[:8] if path.exists() else 'missing'}",
                "ui_contract",
                path.exists() and not missing_tokens,
                actual=missing_tokens,
                expected=[],
                detail=rel(path),
            )
    finally:
        conn.close()

    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    from app.database import SessionLocal
    from app.repositories.commercial_area import CommercialAreaRepository
    from app.services import single_report as single_report_module
    from app.services.commercial_area import AXIS_SUBJECT_MAP, CommercialAreaService
    from app.services.single_report import SingleReportService

    area_api_score_frame = area_axis_rows.loc[
        area_axis_rows["quarter"].astype(str).eq(str(manifest["analysis_quarter"])),
        ["area_code", "axis_demand", "axis_accessibility"],
    ].dropna(subset=["axis_demand", "axis_accessibility"]).copy()
    area_api_score_frame["score"] = (
        area_api_score_frame["axis_demand"].astype(float)
        + area_api_score_frame["axis_accessibility"].astype(float)
    ) / 2.0

    session = SessionLocal()
    original_interpreter = single_report_module.interpret_single_report
    map_response = None
    map_all_areas: list[Any] = []
    map_rankings: list[Any] = []
    map_overview: dict[str, Any] = {}
    map_search_summaries: list[dict[str, Any]] = []
    industry_display_samples: list[dict[str, Any]] = []
    map_expected_score: float | None = None
    map_expected_area_code = ""
    try:
        single_report_module.interpret_single_report = deterministic_interpreter
        repository = CommercialAreaRepository(session)
        area_service = CommercialAreaService(repository)
        top_area = area_api_score_frame.sort_values(
            ["score", "area_code"], ascending=[False, True], kind="mergesort"
        ).iloc[0]
        map_expected_area_code = as_text(top_area["area_code"])
        map_expected_score = as_float(top_area["score"])
        map_response = area_service.get_area(map_expected_area_code)
        map_all_areas = area_service.get_all_areas()
        map_rankings = area_service.get_rankings()
        map_overview = area_service.get_overview_stats()
        top_db_item = repository.get_by_code(map_expected_area_code)
        if top_db_item:
            map_search_summaries = repository.search_summaries(top_db_item.area_name, limit=50)
        sampled_display_rows = (
            official_grade_frame.sort_values(
                ["display_grade", "industry", "score", "area_code"],
                kind="mergesort",
            )
            .groupby("display_grade", sort=True, dropna=False)
            .head(1)
        )
        for _, sample in sampled_display_rows.iterrows():
            sample_rule = area_service._rule_score(
                as_text(sample["area_code"]),
                as_text(sample["industry"]),
            )
            industry_display_samples.append(
                {
                    "area_code": as_text(sample["area_code"]),
                    "industry_code": as_text(sample["industry"]),
                    "base_grade": as_text(sample["base_grade"]),
                    "expected_display_grade": as_text(sample["display_grade"]),
                    "actual_display_grade": area_service._industry_display_grade(sample_rule),
                }
            )
        report_service = SingleReportService(repository)
        response_model = report_service.generate(
            as_text(official["상권_코드"]),
            business_type=as_text(official["서비스_업종_코드"]),
        )
    finally:
        single_report_module.interpret_single_report = original_interpreter
        session.close()

    map_payload = map_response.model_dump(mode="json") if map_response is not None else {}
    expected_map_integer = (
        int(round(float(map_expected_score))) if map_expected_score is not None else None
    )
    area_scores = pd.to_numeric(area_api_score_frame["score"], errors="coerce").dropna()
    map_percentile = (
        100.0 * float(area_scores.le(float(map_expected_score)).sum()) / len(area_scores)
        if map_expected_score is not None and len(area_scores)
        else None
    )
    expected_map_grade = base_grade_from_percentile(map_percentile)
    expected_map_display_grade = display_grade_from_percentile(map_percentile, expected_map_grade)
    area_contract_frame = area_api_score_frame[["area_code", "score"]].copy()
    area_contract_frame["score"] = pd.to_numeric(area_contract_frame["score"], errors="coerce")
    area_contract_frame = area_contract_frame.dropna(subset=["score"])
    area_contract_frame["percentile"] = (
        area_contract_frame["score"].rank(method="max", pct=True) * 100.0
    )
    area_contract_frame["grade"] = area_contract_frame["percentile"].map(base_grade_from_percentile)
    area_contract_frame["display_grade"] = area_contract_frame.apply(
        lambda row: display_grade_from_percentile(row["percentile"], row["grade"]),
        axis=1,
    )
    expected_area_contract = {
        as_text(row["area_code"]): row
        for _, row in area_contract_frame.iterrows()
    }
    area_contract_mismatches: list[str] = []
    api_area_codes: list[str] = []
    for model in map_all_areas:
        item = model.model_dump(mode="json")
        code = as_text(item.get("area_code"))
        api_area_codes.append(code)
        expected = expected_area_contract.get(code)
        if expected is None:
            matches = (
                item.get("score") is None
                and item.get("grade") is None
                and item.get("display_grade") is None
            )
        else:
            matches = (
                item.get("score") == int(round(float(expected["score"])))
                and item.get("grade") == expected["grade"]
                and item.get("display_grade") == expected["display_grade"]
            )
        if not matches:
            area_contract_mismatches.append(code)
    missing_api_area_codes = sorted(set(expected_area_contract) - set(api_area_codes))
    duplicate_api_area_codes = len(api_area_codes) - len(set(api_area_codes))
    display_grade_distribution = {
        str(key): int(value)
        for key, value in area_contract_frame["display_grade"].value_counts().sort_index().items()
    }
    search_payload = next(
        (
            item
            for item in map_search_summaries
            if as_text(item.get("area_code")) == map_expected_area_code
        ),
        None,
    )
    add_check(
        checks,
        "search.representative_area_display_grade_contract",
        "search_service",
        bool(search_payload)
        and as_float(search_payload.get("score")) == map_expected_score
        and as_text(search_payload.get("grade")) == expected_map_grade
        and as_text(search_payload.get("display_grade")) == expected_map_display_grade,
        actual=search_payload,
        expected={
            "area_code": map_expected_area_code,
            "score": map_expected_score,
            "grade": expected_map_grade,
            "display_grade": expected_map_display_grade,
        },
    )
    industry_sample_mismatches = [
        item
        for item in industry_display_samples
        if item["actual_display_grade"] != item["expected_display_grade"]
        or as_text(item["actual_display_grade"])[:1] != item["base_grade"]
    ]
    add_check(
        checks,
        "industry.representative_api_display_grade_contract",
        "industry_service",
        len(industry_display_samples) == len(official_display_distribution)
        and not industry_sample_mismatches,
        actual={
            "sample_count": len(industry_display_samples),
            "samples": industry_display_samples,
            "mismatches": industry_sample_mismatches,
        },
        expected={
            "sample_count": len(official_display_distribution),
            "mismatches": [],
        },
        detail="one production service lookup for every released display-grade bin",
    )
    add_check(
        checks,
        "map.all_area_display_grade_contract",
        "map_service",
        len(map_all_areas) >= len(area_contract_frame)
        and not area_contract_mismatches
        and not missing_api_area_codes
        and duplicate_api_area_codes == 0,
        actual={
            "api_rows": len(map_all_areas),
            "graded_rows": len(area_contract_frame),
            "mismatch_count": len(area_contract_mismatches),
            "missing_graded_area_count": len(missing_api_area_codes),
            "duplicate_area_code_count": duplicate_api_area_codes,
            "display_grade_distribution": display_grade_distribution,
        },
        expected={
            "mismatch_count": 0,
            "missing_graded_area_count": 0,
            "duplicate_area_code_count": 0,
            "display_grade_distribution": "approximately deciles over scored Seoul areas",
        },
    )
    area_population_audit.update(
        {
            "display_grade_mismatch_count": len(area_contract_mismatches),
            "display_grade_distribution": display_grade_distribution,
        }
    )
    add_check(
        checks,
        "map.service_area_score_contract",
        "map_service",
        bool(map_payload)
        and map_payload.get("area_code") == map_expected_area_code
        and map_payload.get("score") == expected_map_integer
        and map_payload.get("grade") == expected_map_grade
        and map_payload.get("display_grade") == expected_map_display_grade
        and map_payload.get("score_type") == "demand_accessibility_context"
        and map_payload.get("score_label") == "수요·접근성 맥락 등급"
        and map_payload.get("official_rank_eligible") is False,
        actual={
            key: map_payload.get(key)
            for key in [
                "area_code",
                "score",
                "grade",
                "display_grade",
                "score_type",
                "score_label",
                "official_rank_eligible",
            ]
        },
        expected={
            "area_code": map_expected_area_code,
            "score": expected_map_integer,
            "grade": expected_map_grade,
            "display_grade": expected_map_display_grade,
            "score_type": "demand_accessibility_context",
            "score_label": "수요·접근성 맥락 등급",
            "official_rank_eligible": False,
        },
        detail=(
            f"DB two-decimal context={map_expected_score}; API/UI contract intentionally "
            "returns an integer-rounded context score"
        ),
    )
    ranking_payload = (
        map_rankings[0].model_dump(mode="json") if map_rankings else {}
    )
    add_check(
        checks,
        "map.service_ranking_contract",
        "map_service",
        bool(ranking_payload)
        and ranking_payload.get("rank") == 1
        and ranking_payload.get("area_code") == map_expected_area_code
        and ranking_payload.get("score") == expected_map_integer
        and ranking_payload.get("grade") == expected_map_grade
        and ranking_payload.get("display_grade") == expected_map_display_grade
        and ranking_payload.get("score_type") == "demand_accessibility_context"
        and ranking_payload.get("score_label") == "수요·접근성 맥락 등급"
        and ranking_payload.get("official_rank_eligible") is False,
        actual=ranking_payload,
        expected={
            "rank": 1,
            "area_code": map_expected_area_code,
            "score": expected_map_integer,
            "grade": expected_map_grade,
            "display_grade": expected_map_display_grade,
            "score_type": "demand_accessibility_context",
            "score_label": "수요·접근성 맥락 등급",
            "official_rank_eligible": False,
        },
    )
    add_check(
        checks,
        "map.service_overview_contract",
        "map_service",
        as_float(map_overview.get("top_score")) == map_expected_score
        and map_overview.get("top_grade") == expected_map_grade
        and map_overview.get("top_display_grade") == expected_map_display_grade
        and map_overview.get("score_type") == "demand_accessibility_context"
        and map_overview.get("score_label") == "수요·접근성 맥락 등급"
        and map_overview.get("official_rank_eligible") is False,
        actual={
            key: map_overview.get(key)
            for key in [
                "top_score",
                "top_grade",
                "top_display_grade",
                "score_type",
                "score_label",
                "official_rank_eligible",
            ]
        },
        expected={
            "top_score": map_expected_score,
            "top_grade": expected_map_grade,
            "top_display_grade": expected_map_display_grade,
            "score_type": "demand_accessibility_context",
            "score_label": "수요·접근성 맥락 등급",
            "official_rank_eligible": False,
        },
    )
    add_check(
        checks,
        "report.response_exists",
        "single_report",
        response_model is not None,
        actual=response_model is not None,
        expected=True,
        detail="deterministic_local_fallback_no_external_calls",
    )
    if response_model is None:
        raise RuntimeError("SingleReportService returned no report for official sample")
    response = response_model.model_dump(mode="json")
    db_score = as_float(official_db["current_location_score"])
    add_check(
        checks,
        "report.opportunity_score",
        "single_report",
        as_float(response.get("opportunity_score")) == round(float(db_score), 1),
        actual=response.get("opportunity_score"),
        expected=round(float(db_score), 1),
    )
    add_check(
        checks,
        "report.score_source",
        "single_report",
        response.get("score_source") == "rule_location_score.full_4axis",
        actual=response.get("score_source"),
        expected="rule_location_score.full_4axis",
    )
    header = response.get("header_block") or {}
    same_industry_scores = pd.to_numeric(
        batch.loc[
            batch["서비스_업종_코드"].astype(str).eq(as_text(official["서비스_업종_코드"]))
            & boolean_series(batch["official_rank_eligible"]).fillna(False),
            "current_location_score",
        ],
        errors="coerce",
    ).dropna()
    expected_display_grade = display_grade_from_percentile(
        100.0 * float(same_industry_scores.le(float(db_score)).sum()) / len(same_industry_scores)
        if len(same_industry_scores)
        else None,
        as_text(official_db["grade"]),
    )
    add_check(
        checks,
        "report.header_display_grade",
        "single_report",
        as_text(header.get("score")) == expected_display_grade
        and as_text(header.get("grade")) == expected_display_grade
        and as_text(header.get("display_grade")) == expected_display_grade
        and as_text(header.get("judgement_line"))
        == as_text(official_db["decision_label"])
        and as_text(header.get("score_label")) == "입지 종합 등급",
        actual={
            key: header.get(key)
            for key in ["score", "grade", "display_grade", "judgement_line", "score_label"]
        },
        expected={
            "score": expected_display_grade,
            "grade": expected_display_grade,
            "display_grade": expected_display_grade,
            "judgement_line": official_db["decision_label"],
            "score_label": "입지 종합 등급",
        },
    )
    radar_by_subject = {
        item.get("subject"): next(iter((item.get("scores") or {}).values()), None)
        for item in response.get("radar_metrics") or []
    }
    expected_radar = {
        subject: round(float(official_db[axis]), 1)
        for subject, axis in AXIS_SUBJECT_MAP.items()
    }
    add_check(
        checks,
        "report.radar_axes",
        "single_report",
        radar_by_subject == expected_radar,
        actual=radar_by_subject,
        expected=expected_radar,
    )
    pack = response.get("indicator_pack") or {}
    target = pack.get("target") or {}
    pack_axes = pack.get("axis_scores") or {}
    expected_pack_axes = {
        "sales": as_float(official_db["axis_sales"]),
        "competition": as_float(official_db["axis_competition"]),
        "demand": as_float(official_db["axis_demand"]),
        "accessibility": as_float(official_db["axis_accessibility"]),
    }
    target_ok = (
        as_float(target.get("score")) == as_float(official_db["current_location_score"])
        and as_text(target.get("grade")) == as_text(official_db["grade"])
        and as_text(target.get("display_grade")) == expected_display_grade
        and as_text(target.get("score_version")) == as_text(official_db["score_version"])
        and as_text(target.get("score_source")) == "rule_location_score.full_4axis"
        and as_text(target.get("area_code")) == as_text(official_db["area_code"])
        and as_text(target.get("industry_code")) == as_text(official_db["industry_code"])
    )
    add_check(
        checks,
        "report.indicator_target",
        "single_report",
        target_ok,
        actual={
            key: target.get(key)
            for key in [
                "score",
                "grade",
                "display_grade",
                "score_version",
                "score_source",
                "area_code",
                "industry_code",
            ]
        },
        expected={
            "score": official_db["current_location_score"],
            "grade": official_db["grade"],
            "display_grade": expected_display_grade,
            "score_version": official_db["score_version"],
            "score_source": "rule_location_score.full_4axis",
            "area_code": official_db["area_code"],
            "industry_code": official_db["industry_code"],
        },
    )
    display_grade_pattern = re.compile(r"^[A-E](?:\+)?$")
    axis_interpretations = response.get("axis_interpretations") or []
    displayed_axis_grades = {
        as_text(item.get("axis")): as_text(
            item.get("display_grade") or item.get("score_display")
        )
        for item in axis_interpretations
    }
    expected_axis_grades = {
        label: score_to_display_grade(official_db[column])
        for label, column in AXIS_DISPLAY_LABELS.items()
    }
    add_check(
        checks,
        "report.axis_display_grades",
        "single_report",
        len(axis_interpretations) == 4
        and displayed_axis_grades == expected_axis_grades
        and all(
            display_grade_pattern.fullmatch(value or "")
            for value in displayed_axis_grades.values()
        ),
        actual=displayed_axis_grades,
        expected=expected_axis_grades,
        detail="independent raw 0..100 axis score to strict-midpoint A+..E replay",
    )
    report_coverage = target.get("score_coverage") or {}
    expected_missing_axes = [
        value
        for value in as_text(official_db["missing_axes"]).split(",")
        if value
    ]
    coverage_ok = (
        as_text(report_coverage.get("tier"))
        == as_text(official_db["score_coverage_tier"])
        and as_int(report_coverage.get("available_axis_count"))
        == as_int(official_db["available_axis_count"])
        and as_int(report_coverage.get("official_indicator_count"))
        == as_int(official_db["official_indicator_count"])
        and as_int(report_coverage.get("official_indicator_defined_count"))
        == as_int(official_db["official_indicator_defined_count"])
        and as_bool(report_coverage.get("official_indicator_complete"))
        == as_bool(official_db["official_indicator_complete"])
        and list(report_coverage.get("missing_axes") or []) == expected_missing_axes
        and as_text(report_coverage.get("reason"))
        == as_text(official_db["coverage_reason"])
        and as_bool(report_coverage.get("official_rank_eligible"))
        == as_bool(official_db["official_rank_eligible"])
        and as_float(report_coverage.get("context_location_score"))
        == as_float(official_db["context_location_score"])
    )
    add_check(
        checks,
        "report.coverage_contract",
        "single_report",
        coverage_ok,
        actual=report_coverage,
        expected={
            "tier": official_db["score_coverage_tier"],
            "available_axis_count": official_db["available_axis_count"],
            "official_indicator_count": official_db["official_indicator_count"],
            "official_indicator_defined_count": official_db[
                "official_indicator_defined_count"
            ],
            "official_indicator_complete": bool(
                official_db["official_indicator_complete"]
            ),
            "missing_axes": expected_missing_axes,
            "reason": official_db["coverage_reason"],
            "official_rank_eligible": bool(official_db["official_rank_eligible"]),
            "context_location_score": official_db["context_location_score"],
        },
    )
    pack_axes_ok = all(
        as_float(pack_axes.get(axis)) == expected
        for axis, expected in expected_pack_axes.items()
    )
    add_check(
        checks,
        "report.indicator_axes",
        "single_report",
        pack_axes_ok,
        actual=pack_axes,
        expected=expected_pack_axes,
    )
    data_sources = pack.get("data_sources") or []
    add_check(
        checks,
        "report.source_trace",
        "single_report",
        pack.get("detail_source") == "product_db"
        and "score_model" in data_sources
        and len(data_sources) >= 5,
        actual={"detail_source": pack.get("detail_source"), "data_sources": data_sources},
        expected="product_db plus score_model and source list",
    )

    public_report_text = report_text(response)
    forbidden_terms = [
        "성공확률",
        "성공 확률",
        "생존확률",
        "생존 확률",
        "폐업확률",
        "폐업 확률",
    ]
    mentioned_terms = [term for term in forbidden_terms if term in public_report_text]
    unqualified_terms: list[str] = []
    negation_markers = [
        "해석하지",
        "확인되지",
        "아니다",
        "아닙니다",
        "금지",
        "주장할 수 없",
        "사용하지 않",
    ]
    for term in mentioned_terms:
        for match in re.finditer(re.escape(term), public_report_text):
            window = public_report_text[
                max(0, match.start() - 100) : min(len(public_report_text), match.end() + 140)
            ]
            if not any(marker in window for marker in negation_markers):
                unqualified_terms.append(term)
                break
    probability_pattern = re.compile(
        r"(?:성공|생존|폐업)\s*(?:확률|가능성)\s*[:：]?\s*\d+(?:\.\d+)?\s*%"
    )
    matched_percent_claims = probability_pattern.findall(public_report_text)
    limitations = [as_text(value) for value in response.get("limitations") or []]
    limitations_text = "\n".join(limitations)
    survival_disclaimer_markers = [
        "365일 생존",
        "생존 예측력",
        "생존 가능성이나 사업 성공",
        "개별 점포의 매출이나 수익성을 예측하지",
    ]
    visible_survival_disclaimers = [
        marker for marker in survival_disclaimer_markers if marker in public_report_text
    ]
    numeric_score_displays = re.findall(
        r"(?<![\d.])\d{1,3}(?:\.\d+)?\s*점(?!포)", public_report_text
    )
    add_check(
        checks,
        "report.no_success_probability_claim",
        "claim_contract",
        not mentioned_terms and not matched_percent_claims,
        actual={
            "mentioned_terms": mentioned_terms,
            "unqualified_terms": unqualified_terms,
            "percent_claims": matched_percent_claims,
        },
        expected={"mentioned_terms": [], "percent_claims": []},
    )
    add_check(
        checks,
        "report.user_facing_survival_disclaimer_absent",
        "claim_contract",
        not visible_survival_disclaimers,
        actual=visible_survival_disclaimers,
        expected=[],
    )
    add_check(
        checks,
        "report.numeric_score_display_absent",
        "grade_presentation",
        not numeric_score_displays,
        actual=numeric_score_displays,
        expected=[],
    )

    artifact_details: dict[str, Any] = {}
    for name, path in {
        "trace_summary": TRACE_SUMMARY_PATH,
        "trace_csv": TRACE_CSV_PATH,
        "trace_validation_csv": TRACE_VALIDATION_CSV_PATH,
        "sales_backtest": SALES_BACKTEST_PATH,
        "survival_backtest": SURVIVAL_BACKTEST_PATH,
    }.items():
        exists = path.exists() and path.stat().st_size > 0
        artifact_details[name] = {
            "path": rel(path),
            "exists": exists,
            "sha256": sha256_file(path) if exists else None,
        }
        add_check(
            checks,
            f"artifact.{name}",
            "evidence_artifacts",
            exists,
            actual=exists,
            expected=True,
            detail=rel(path),
        )

    trace_summary = read_json(TRACE_SUMMARY_PATH)
    add_check(
        checks,
        "trace.summary_decision",
        "evidence_artifacts",
        trace_summary.get("decision") == "ALGORITHM_EVIDENCE_TRACEABILITY_PASS"
        and int(trace_summary.get("fail_count", -1)) == 0
        and int(trace_summary.get("trace_row_count", 0)) > 0,
        actual={
            "decision": trace_summary.get("decision"),
            "fail_count": trace_summary.get("fail_count"),
            "trace_row_count": trace_summary.get("trace_row_count"),
        },
        expected={"decision": "ALGORITHM_EVIDENCE_TRACEABILITY_PASS", "fail_count": 0},
    )
    sales_backtest = read_json(SALES_BACKTEST_PATH)
    add_check(
        checks,
        "backtest.sales_summary",
        "evidence_artifacts",
        int(sales_backtest.get("row_count", 0)) > 0
        and len(sales_backtest.get("valid_quarters") or []) > 1,
        actual={
            "row_count": sales_backtest.get("row_count"),
            "quarters": len(sales_backtest.get("valid_quarters") or []),
        },
        expected="positive rows and multiple quarters",
    )
    survival_backtest = read_json(SURVIVAL_BACKTEST_PATH)
    predictive_status = survival_backtest.get("predictive_status")
    survival_input = survival_backtest.get("input") or {}
    current_survival_input_hash = (
        sha256_file(LOCALDATA_BUSINESS_SILVER_PATH)
        if LOCALDATA_BUSINESS_SILVER_PATH.exists()
        else None
    )
    add_check(
        checks,
        "backtest.survival_input_lineage",
        "evidence_artifacts",
        current_survival_input_hash is not None
        and survival_input.get("sha256") == current_survival_input_hash,
        actual={
            "summary_input_sha256": survival_input.get("sha256"),
            "current_silver_sha256": current_survival_input_hash,
        },
        expected="survival backtest input hash equals current common business-license Silver",
        detail=rel(LOCALDATA_BUSINESS_SILVER_PATH),
    )
    add_check(
        checks,
        "backtest.survival_summary",
        "evidence_artifacts",
        predictive_status in {"not_supported", "weak_signal", "positive_signal"}
        and int((survival_backtest.get("cohort") or {}).get("analyzed_rows", 0)) > 0,
        actual={
            "predictive_status": predictive_status,
            "analyzed_rows": (survival_backtest.get("cohort") or {}).get("analyzed_rows"),
        },
        expected="completed survival evaluation",
    )
    cutoff_verified = survival_backtest.get("score_weight_training_cutoff_verified")
    temporal_scope = survival_backtest.get("temporal_holdout_scope")
    nested_status = survival_backtest.get("nested_out_of_sample_status")
    add_check(
        checks,
        "backtest.weight_cutoff_caveat",
        "methodology_contract",
        cutoff_verified is False
        and temporal_scope == "calibration_and_survival_labels_only"
        and nested_status == "not_verified",
        actual={
            "score_weight_training_cutoff_verified": cutoff_verified,
            "temporal_holdout_scope": temporal_scope,
            "nested_out_of_sample_status": nested_status,
        },
        expected={
            "score_weight_training_cutoff_verified": False,
            "temporal_holdout_scope": "calibration_and_survival_labels_only",
            "nested_out_of_sample_status": "not_verified",
        },
        detail="temporal holdout applies to calibration and outcome labels, not verified score-weight training",
    )
    add_check(
        checks,
        "report.survival_claim_gate",
        "claim_contract",
        predictive_status != "not_supported"
        or (
            not mentioned_terms
            and not matched_percent_claims
            and not visible_survival_disclaimers
        ),
        actual={
            "predictive_status": predictive_status,
            "mentioned_terms": mentioned_terms,
            "percent_claims": matched_percent_claims,
            "visible_survival_disclaimers": visible_survival_disclaimers,
        },
        expected=(
            "no survival/success probability claim and no user-facing predictive disclaimer "
            "when predictive_status=not_supported"
        ),
    )

    sample_summary = {
        sample_type: {
            "quarter": as_text(row["기준_년분기_코드"]),
            "area_code": as_text(row["상권_코드"]),
            "area_name": as_text(row["상권_코드_명"]),
            "district_code": as_text(row["자치구_코드"]),
            "district_name": as_text(row["자치구_코드_명"]),
            "industry_code": as_text(row["서비스_업종_코드"]),
            "industry_name": as_text(row["서비스_업종_코드_명"]),
            "official_rank_eligible": as_bool(row["official_rank_eligible"]),
            "current_location_score": as_float(row["current_location_score"]),
            "context_location_score": as_float(row["context_location_score"]),
            "coverage_tier": as_text(row["score_coverage_tier"]),
        }
        for sample_type, row in samples.items()
    }
    return {
        "manifest": {
            "path": rel(manifest_path),
            "batch_path": rel(batch_path),
            "analysis_quarter": str(manifest["analysis_quarter"]),
            "score_version": manifest["score_version"],
            "row_count": int(manifest["row_count"]),
            "gold_release_id": manifest.get("gold_release_id"),
        },
        "database": rel(db_path),
        "batch_db_population": all_row_parity,
        "score_population": score_population_audit,
        "area_context_population": area_population_audit,
        "samples": sample_summary,
        "score_recalculation": {
            "weight_set": weight_set,
            "axes": axis_values,
            "normalized_weights": {
                key: value / weight_sum for key, value in component_weights.items()
            }
            if weight_sum > 0
            else {},
            "raw_score": raw_recalculated,
            "rounded_score": rounded_recalculated,
        },
        "area_context": {
            "area_code": area_code,
            "expected_demand_accessibility_mean": area_expected,
            "db_score": area_actual,
        },
        "map_service": {
            "area_code": map_expected_area_code,
            "db_two_decimal_score": map_expected_score,
            "api_integer_score": map_payload.get("score"),
            "score_type": map_payload.get("score_type"),
            "score_label": map_payload.get("score_label"),
            "official_rank_eligible": map_payload.get("official_rank_eligible"),
            "precision_contract": (
                "rule_area_score_summary stores two decimals; CommercialAreaResponse "
                "and RankingResponse intentionally expose an integer-rounded context score"
            ),
        },
        "single_report": {
            "execution_mode": "deterministic_local_fallback_no_external_calls",
            "opportunity_score": response.get("opportunity_score"),
            "score_source": response.get("score_source"),
            "header": {
                key: header.get(key)
                for key in ["score", "grade", "judgement_line", "score_label"]
            },
            "score_coverage": report_coverage,
            "quality_status": response.get("quality_status"),
            "forbidden_claim_matches": mentioned_terms + matched_percent_claims,
            "visible_survival_disclaimers": visible_survival_disclaimers,
            "numeric_score_displays": numeric_score_displays,
        },
        "artifacts": artifact_details,
        "survival_predictive_status": predictive_status,
        "methodology": {
            "score_weight_training_cutoff_verified": cutoff_verified,
            "temporal_holdout_scope": temporal_scope,
            "nested_out_of_sample_status": nested_status,
            "report_execution_scope": (
                "Production CommercialAreaService and SingleReportService score-selection "
                "paths were executed. interpret_single_report was replaced by the "
                "deterministic production fallback builder; external news/LLM, cache, "
                "and report critic behavior were not exercised by this validator."
            ),
        },
    }


def markdown_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "(없음)"
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join(["---"] * len(columns)) + " |",
    ]
    for row in rows:
        values = [
            str(row.get(column, "")).replace("|", "\\|").replace("\n", " ")
            for column in columns
        ]
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def write_outputs(
    output_dir: Path,
    summary: dict[str, Any],
    checks: list[dict[str, Any]],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "validation_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    pd.DataFrame(checks).to_csv(
        output_dir / "validation_checks.csv", index=False, encoding="utf-8-sig"
    )
    failed = [row for row in checks if row["status"] == "FAIL"]
    samples = summary.get("details", {}).get("samples", {})
    sample_rows = [
        {"표본": name, **payload} for name, payload in samples.items()
    ]
    details = summary.get("details", {})
    population = details.get("score_population", {})
    parity = details.get("batch_db_population", {})
    area_population = details.get("area_context_population", {})
    methodology = details.get("methodology", {})
    failed_table = markdown_table(
        failed,
        ["check_id", "group", "actual", "expected", "detail"],
    )
    record = f"""# 제품 점수 Grounding 검증

- 검증 버전: {summary['validation_version']}
- 판정: {summary['status']}
- 통과/실패: {summary['pass_count']} / {summary['fail_count']}
- 점수 배치: {summary.get('details', {}).get('manifest', {}).get('batch_path', '-')}
- 제품 DB: {summary.get('details', {}).get('database', '-')}
- 배치/DB 전행: {parity.get('batch_rows', 0):,} / {parity.get('db_rows', 0):,}행
- 공식/보류: {population.get('official_rows', 0):,} / {population.get('withheld_rows', 0):,}행
- 리포트 실행: production score-selection service + deterministic interpretation fallback, 뉴스/LLM 외부 호출 없음

## 대표 표본

{markdown_table(sample_rows, ['표본', 'quarter', 'area_code', 'area_name', 'industry_code', 'industry_name', 'official_rank_eligible', 'current_location_score', 'context_location_score', 'coverage_tier'])}

## 검증 범위

- 최신 배치 {parity.get('batch_rows', 0):,}행 전부를 DB 키·점수·등급·4축·커버리지·버전과 필드별 대조했다.
- 공식 {population.get('official_rows', 0):,}행 전부를 권고 가중치 CSV로 재계산했고, 보류 {population.get('withheld_rows', 0):,}행의 점수·등급이 fail-closed인지 확인했다.
- 공식 점수의 공개 2자리 축 기준 정확 일치는 {population.get('official_exact_2dp_matches', 0):,}/{population.get('official_rows', 0):,}행이며, 0.01 이내 일치는 {population.get('official_within_0_01_matches', 0):,}/{population.get('official_rows', 0):,}행이다.
- 업종 기본 A~E와 A+~E 표시등급의 기본등급 불일치는 {population.get('display_grade_base_mismatch_count', 0):,}행이며 분포는 {population.get('display_grade_distribution', {})}이다.
- 상권 맥락 {area_population.get('area_summary_rows', 0):,}행 전부가 수요축·접근성축 평균 및 정의 계약과 일치하는지 확인했다.
- 지도·검색용 상권 표시등급 불일치는 {area_population.get('display_grade_mismatch_count', 0):,}행이며 분포는 {area_population.get('display_grade_distribution', {})}이다.
- 실제 CommercialAreaService의 지도 점수·순위·API schema 계약과 화면 라벨을 확인했다.
- SingleReportService를 실제 DB로 실행하되 뉴스와 LLM 호출만 결정론 fallback으로 막았다.
- 상세 리포트 헤더 표시 등급·내부 4축·커버리지·score_version을 DB와 대조하고 숫자 점수 및 생존 면책 문구 비노출을 확인했다.
- 근거 추적 산출물, 매출 백테스트, 365일 생존 백테스트가 존재하고 계약을 만족하는지 확인했다.

## 산정 방식과 내부 검증 한계

- 업종 점수의 기본 A~E는 출시 Gold의 기존 판정을 그대로 사용하며, 동일 업종 누적분위가 각 기본 구간의 상위 절반일 때만 A+·B+·C+·D+·E+로 세분한다. 중간점인 90·70·50·30·10 백분위를 초과해야 plus가 되고, 중간점과 같은 값은 기본등급에 남으며 동일 점수는 CUME_DIST로 같은 표시등급을 유지한다.
- 업종이 없는 상권 맥락 등급은 서울 상권 모집단 CUME_DIST를 사용해 같은 10단계 경계를 적용한다. 원시 점수 숫자는 계산·감사용으로만 유지한다.
- survival temporal holdout은 생존 라벨 분리와 development-only 확률 보정 기준이다.
- v2.6 점수 가중치가 holdout 이전에 고정되었다는 증거는 확인되지 않아 완전한 nested OOS라고 부르지 않는다.
- survival predictive_status와 확률 주장 금지는 관리자 검증 기록에서 관리하고 사용자 리포트에는 면책 문구를 반복 노출하지 않는다.
- 공개 축은 2자리지만 총점은 반올림 전 축으로 계산되어, 공개 축만으로 재현할 때 최대 0.01 차이가 난다. 이는 {population.get('published_axis_precision_caveat', '-')}
- {methodology.get('report_execution_scope', '-')}

## 실패 항목

{failed_table}
"""
    (output_dir / "validation_record.md").write_text(record, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate released score batch, product DB, UI contract, and report grounding"
    )
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--batch-manifest")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()

    started = datetime.now().astimezone()
    db_path = Path(args.db).resolve()
    # Product services are imported lazily below.  Point them at the exact DB
    # audited by this invocation so SQLite parity and report parity cannot
    # silently inspect different databases.
    os.environ["LOCALFIT_DATABASE_PATH"] = str(db_path)
    output_dir = Path(args.output_dir).resolve()
    checks: list[dict[str, Any]] = []
    details: dict[str, Any] = {}
    unhandled_error: str | None = None
    try:
        details = run_validation(
            db_path=db_path,
            manifest_path_arg=args.batch_manifest,
            checks=checks,
        )
    except Exception as exc:
        unhandled_error = f"{type(exc).__name__}: {exc}"
        add_check(
            checks,
            "validator.unhandled_error",
            "validator",
            False,
            actual=unhandled_error,
            expected="no exception",
        )

    failed = [row for row in checks if row["status"] == "FAIL"]
    summary = {
        "validation_version": "product_score_grounding.v1.3-20260718-strict-grade-display",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "duration_seconds": round(
            (datetime.now().astimezone() - started).total_seconds(), 3
        ),
        "status": "pass" if not failed and checks else "fail",
        "pass_count": sum(row["status"] == "PASS" for row in checks),
        "fail_count": len(failed),
        "failed_check_ids": [row["check_id"] for row in failed],
        "unhandled_error": unhandled_error,
        "details": details,
        "outputs": {
            "summary": "validation_summary.json",
            "checks": "validation_checks.csv",
            "record": "validation_record.md",
        },
    }
    write_outputs(output_dir, summary, checks)
    print(
        f"[grounding] status={summary['status']} pass={summary['pass_count']} "
        f"fail={summary['fail_count']}",
        flush=True,
    )
    print(f"[grounding] outputs={output_dir}", flush=True)
    if failed:
        print(
            "[grounding] failed=" + ",".join(summary["failed_check_ids"]),
            flush=True,
        )
    return 0 if summary["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
