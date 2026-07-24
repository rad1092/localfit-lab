from __future__ import annotations

import calendar
import math
import re
import sqlite3
from contextlib import closing
from typing import Any

from app.core.settings import DATABASE_PATH


DB_PATH = DATABASE_PATH
EXPECTED_COVERAGE_SCORE_VERSION = "loc_score.v2.6-coverage-contract-rc1"
DISPLAY_GRADES = ("E", "E+", "D", "D+", "C", "C+", "B", "B+", "A", "A+")
DISPLAY_GRADE_SET = frozenset(DISPLAY_GRADES)
SALES_AGGREGATE_LABEL = "최근 분기 상권·업종 합산 추정매출액"
PUBLIC_AXIS_LABELS = {
    "sales": "시장성",
    "competition": "경쟁 구조",
    "demand": "수요 기반",
    "accessibility": "접근·유입",
}
PUBLIC_COVERAGE_TIERS = {
    "full_4axis": "네 판단 영역의 필수 지표 확인",
    "context_only_partial_4axis": "일부 필수 지표만 확인된 참고 판단",
    "context_only_3axis": "세 판단 영역만 확인된 참고 판단",
    "insufficient_context": "판단 자료 부족",
    "legacy_reference": "이전 산정 결과 참고",
}
PARTIAL_AXIS_REASON_PATTERN = re.compile(
    r"\b(sales|competition|demand|accessibility):(\d+)/(\d+)\b"
)


def public_axis_labels(axis_codes: list[Any] | tuple[Any, ...] | None) -> list[str]:
    return [PUBLIC_AXIS_LABELS.get(str(code), str(code)) for code in (axis_codes or [])]


def public_coverage_reason(coverage: dict[str, Any] | None) -> str:
    """Turn rule-engine coverage codes into report-reader language."""
    coverage = coverage or {}
    if bool(coverage.get("official_rank_eligible")):
        return "공식 판단에 필요한 네 영역의 필수 지표가 모두 확인됐습니다."

    missing_labels = public_axis_labels(coverage.get("missing_axes") or [])
    if missing_labels:
        return f"{'·'.join(missing_labels)} 지표가 없어 공식 종합 판단을 보류합니다."

    raw_reason = str(coverage.get("reason") or "")
    partial = PARTIAL_AXIS_REASON_PATTERN.search(raw_reason)
    if partial:
        label = PUBLIC_AXIS_LABELS.get(partial.group(1), "해당 판단 영역")
        return f"{label}의 필수 지표가 일부 없어 공식 종합 판단을 보류합니다."
    if "신뢰도" in raw_reason or "reliability" in raw_reason.lower():
        return "데이터 신뢰도 기준을 충족하지 못해 공식 종합 판단을 보류합니다."
    if "레거시" in raw_reason or "legacy" in raw_reason.lower():
        return "이전 산정 결과여서 공식 종합 판단을 보류하고 참고값만 제공합니다."
    return "공식 판단에 필요한 지표가 일부 확인되지 않아 종합 판단을 보류합니다."


def public_coverage_context(coverage: dict[str, Any] | None) -> str:
    coverage = coverage or {}
    missing = set(str(code) for code in (coverage.get("missing_axes") or []))
    if missing:
        available = [
            label
            for code, label in PUBLIC_AXIS_LABELS.items()
            if code not in missing
        ]
        return f"현재는 확인 가능한 {'·'.join(available)} 지표만 참고합니다."
    if not bool(coverage.get("official_rank_eligible")):
        return "현재 등급은 확인 가능한 지표 범위의 참고값입니다."
    return ""


def public_coverage_header(coverage: dict[str, Any] | None, default: Any = None) -> str:
    coverage = coverage or {}
    if bool(coverage.get("official_rank_eligible")):
        return str(default or "입지 조건 검토")
    missing_labels = public_axis_labels(coverage.get("missing_axes") or [])
    if missing_labels:
        return f"{'·'.join(missing_labels)} 지표 없음 · 공식 판단 보류"
    partial = PARTIAL_AXIS_REASON_PATTERN.search(str(coverage.get("reason") or ""))
    if partial:
        label = PUBLIC_AXIS_LABELS.get(partial.group(1), "일부")
        return f"{label} 일부 지표 없음 · 공식 판단 보류"
    return "일부 필수 지표 없음 · 공식 판단 보류"


def public_coverage_tier(value: Any) -> str:
    return PUBLIC_COVERAGE_TIERS.get(str(value or ""), "판단 범위 확인 필요")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _row_value(row: sqlite3.Row | dict[str, Any] | None, key: str, default: Any = None) -> Any:
    """Read a possibly-new field without breaking on a legacy SQLite row."""
    if row is None:
        return default
    if isinstance(row, sqlite3.Row):
        return row[key] if key in row.keys() else default
    return row.get(key, default)


def _select_or_null(conn: sqlite3.Connection, table: str, columns: list[str]) -> str:
    """Build a SELECT list that remains valid while reload uses an older DB schema."""
    available = {
        str(row[1])
        for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    }
    return ", ".join(
        f'"{column}"' if column in available else f'NULL AS "{column}"'
        for column in columns
    )


def _to_number(value: Any) -> float | int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        number = float(text.replace(",", ""))
    except ValueError:
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    if number.is_integer():
        return int(number)
    return round(number, 4)


def _score_grade(value: Any) -> str | None:
    number = _to_number(value)
    if number is None or not 0 <= float(number) <= 100:
        return None
    value_float = float(number)
    if value_float > 90:
        return "A+"
    if value_float > 80:
        return "A"
    if value_float > 70:
        return "B+"
    if value_float > 60:
        return "B"
    if value_float > 50:
        return "C+"
    if value_float > 40:
        return "C"
    if value_float > 30:
        return "D+"
    if value_float > 20:
        return "D"
    if value_float > 10:
        return "E+"
    return "E"


def _detailed_grade(base_grade: Any, percentile: Any, score: Any) -> str | None:
    base = str(base_grade or "").strip().upper()
    pct = _to_number(percentile)
    plus_threshold = {"A": 90.0, "B": 70.0, "C": 50.0, "D": 30.0, "E": 10.0}
    if base in plus_threshold:
        return f"{base}+" if pct is not None and float(pct) > plus_threshold[base] else base
    return None


def _validated_display_grade(value: Any, base_grade: Any) -> str | None:
    """Accept a backend display grade only when it preserves the Gold base grade."""
    candidate = str(value or "").strip().upper()
    base = str(base_grade or "").strip().upper()
    if candidate not in DISPLAY_GRADE_SET or base not in {"A", "B", "C", "D", "E"}:
        return None
    return candidate if candidate[0] == base else None


def _format_count(number: float, unit: str) -> str:
    if unit in {"명", "건"} and abs(number) >= 1_000_000:
        return f"{number / 10_000:.1f}만 {unit}"
    return f"{number:,.0f}{unit}"


def _format_display(value: Any, unit: str = "", label: str = "") -> str:
    number = _to_number(value)
    if number is None:
        return "없음"
    number_float = float(number)
    if unit == "score":
        return _score_grade(number_float) or "등급 없음"
    if unit == "percentile":
        top_pct = max(0.0, 100.0 - number_float)
        if top_pct <= 50:
            return f"상위 {top_pct:.1f}%"
        return f"하위 {number_float:.1f}%"
    if unit == "fraction_percent":
        number_float *= 100
        precision = 2 if 0 < abs(number_float) < 0.1 else 1
        return f"{number_float:.{precision}f}%"
    if unit == "%":
        return f"{number_float:.1f}%"
    if unit == "원":
        if any(token in label for token in ["객단가", "단가"]):
            return f"{number_float:,.0f}원"
        if abs(number_float) >= 100_000_000:
            return f"{number_float / 100_000_000:.1f}억원"
        return f"{number_float / 10_000:,.0f}만원"
    if unit == "만원":
        return f"{number_float:,.0f}만원"
    if unit in {"개", "건", "명", "면"}:
        return _format_count(number_float, unit)
    if unit == "분":
        return f"{number_float:.1f}분"
    if unit == "개/1만m2":
        return f"{number_float:.1f}개/1만m2"
    if "지표" in unit or "지수" in unit:
        return f"비용 압력 지표 {number_float:.1f}"
    if float(number_float).is_integer():
        text = f"{number_float:,.0f}"
    else:
        text = f"{number_float:.1f}"
    return f"{text}{unit}" if unit else text


def _quarter_period_text(quarter: str) -> str:
    text = str(quarter or "").strip()
    if len(text) == 5 and text.isdigit() and text[-1] in {"1", "2", "3", "4"}:
        quarter_number = int(text[-1])
        start_month = (quarter_number - 1) * 3 + 1
        end_month = start_month + 2
        return f"{text[:4]}년 {quarter_number}분기({start_month}~{end_month}월)"
    return text


def _quarter_label(quarter: Any) -> str:
    text = str(quarter or "").strip()
    if len(text) == 5 and text.isdigit() and text[-1] in {"1", "2", "3", "4"}:
        return f"{text[:4]}년 {int(text[-1])}분기"
    return text


def _quarter_day_count(quarter: Any) -> int | None:
    text = str(quarter or "").strip()
    if len(text) != 5 or not text.isdigit() or text[-1] not in {"1", "2", "3", "4"}:
        return None
    year = int(text[:4])
    quarter_number = int(text[-1])
    start_month = (quarter_number - 1) * 3 + 1
    return sum(calendar.monthrange(year, month)[1] for month in range(start_month, start_month + 3))


def _parse_missing_axes(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [str(axis).strip() for axis in value if str(axis).strip()]
    return [axis.strip() for axis in str(value or "").split(",") if axis.strip()]


def _metric(label: str, value: Any, *, unit: str = "", source: str, note: str = "") -> dict[str, Any] | None:
    number = _to_number(value)
    if number is None:
        return None
    return {
        "label": label,
        "display": _format_display(number, unit, label),
        "raw": number,
        "unit": "%" if unit == "fraction_percent" else unit,
        "source": source,
        "note": note,
    }


def _series_unit(column: str) -> tuple[str, str]:
    if "sales_amount" in column or "매출" in column:
        return "원", "매출액"
    if "store_count" in column or "점포" in column:
        return "개", "점포수"
    if "population" in column or "인구" in column:
        return "명", "인구"
    if "rent" in column or "임대" in column:
        return "지표", "비용 압력"
    if "sale_price_proxy" in column:
        return "만원/㎡", "매매가 프록시"
    return "", column


def _series(rows: list[sqlite3.Row], value_columns: list[str], source: str) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        item = {"timestamp": row["timestamp"], "source": source}
        for column in value_columns:
            number = _to_number(row[column])
            unit, label = _series_unit(column)
            item[column] = {
                "display": _format_display(number, unit, label) if number is not None else "없음",
                "raw": number,
            }
        result.append(item)
    return result


def _pct_change(current: Any, previous: Any) -> float | None:
    cur = _to_number(current)
    prev = _to_number(previous)
    if cur is None or prev in (None, 0):
        return None
    return round((float(cur) - float(prev)) / abs(float(prev)) * 100, 2)


def _percentile(conn: sqlite3.Connection, table: str, column: str, where_sql: str, params: tuple[Any, ...], value: Any) -> float | None:
    target = _to_number(value)
    if target is None:
        return None
    row = conn.execute(
        f"""
        SELECT
            SUM(CASE WHEN {column} <= ? THEN 1 ELSE 0 END) AS below_or_equal,
            COUNT(*) AS total
        FROM {table}
        WHERE {where_sql} AND {column} IS NOT NULL
        """,
        (target, *params),
    ).fetchone()
    if not row or not row["total"]:
        return None
    return round(float(row["below_or_equal"]) / float(row["total"]) * 100, 1)


def _latest_row(
    conn: sqlite3.Connection,
    table: str,
    area_code: str,
    columns: str,
    quarter: str,
    industry_code: str | None = None,
) -> sqlite3.Row | None:
    if industry_code:
        return conn.execute(
            f"""
            SELECT {columns}
            FROM {table}
            WHERE area_code = ? AND industry_code = ? AND timestamp = ?
            LIMIT 1
            """,
            (area_code, industry_code, quarter),
        ).fetchone()
    return conn.execute(
        f"""
        SELECT {columns}
        FROM {table}
        WHERE area_code = ? AND timestamp = ?
        LIMIT 1
        """,
        (area_code, quarter),
    ).fetchone()


def _recent_rows(
    conn: sqlite3.Connection,
    table: str,
    area_code: str,
    columns: str,
    quarter: str,
    limit: int = 6,
    industry_code: str | None = None,
) -> list[sqlite3.Row]:
    if industry_code:
        return conn.execute(
            f"""
            SELECT {columns}
            FROM {table}
            WHERE area_code = ? AND industry_code = ? AND timestamp <= ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (area_code, industry_code, quarter, limit),
        ).fetchall()
    return conn.execute(
        f"""
        SELECT {columns}
        FROM {table}
        WHERE area_code = ? AND timestamp <= ?
        ORDER BY timestamp DESC
        LIMIT ?
        """,
        (area_code, quarter, limit),
    ).fetchall()


def _metric_display(metric: dict[str, Any] | None) -> str:
    if not metric:
        return "없음"
    label = metric.get("label")
    display = metric.get("display")
    note = metric.get("note")
    body = f"{label} {display}" if label and display else str(display or label or "")
    return f"{body} ({note})" if note else body


def _display_only(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, list):
        return [_display_only(item) for item in value]
    if isinstance(value, dict):
        if "display" in value and "raw" in value and set(value.keys()).issubset({"display", "raw"}):
            return value["display"]
        result: dict[str, Any] = {}
        for key, item in value.items():
            # "source"는 내부 테이블 경로라 LLM 입력·본문에 노출하지 않는다.
            # 원천 표기는 facts_pack 원본을 읽는 원천 데이터 섹션이 담당한다.
            if key in {
                "raw",
                "value",
                "source",
                "facts_pack",
                "facts_pack_display",
                "score_percentile",
                "score_rank",
                "percentile",
            }:
                continue
            result[key] = _display_only(item)
        return result
    if isinstance(value, (int, float)):
        return _format_display(value)
    return value


def _budget_fit_display(raw_budget_fit: Any, display_budget_fit: Any) -> dict[str, Any]:
    """Attach units to budget-reference values used by the narrative contract."""
    raw = raw_budget_fit if isinstance(raw_budget_fit, dict) else {}
    display = dict(display_budget_fit) if isinstance(display_budget_fit, dict) else {}
    format_specs = {
        "budget_manwon": "만원",
        "reference_area_m2": "㎡",
        "reference_months": "개월",
        "rone_rent_reference_thousand_won_per_m2": "천원/㎡",
        "standardized_12m_reference_manwon": "만원",
        "reference_to_input_budget_ratio": "fraction_percent",
    }
    for key, unit in format_specs.items():
        if raw.get(key) is not None:
            display[key] = _format_display(raw[key], unit=unit)
    return display


def _score_item(label: str, value: Any, *, source: str, note: str = "") -> dict[str, Any] | None:
    item = _metric(label, value, unit="score", source=source, note=note)
    if item:
        item["grade"] = _score_grade(value)
        item["display"] = item["grade"] or "등급 없음"
    return item


def _rank_metric(label: str, rank: int | None, total: int | None, *, source: str) -> dict[str, Any] | None:
    if not rank or not total:
        return None
    return {
        "label": label,
        "display": f"{total:,}개 후보 중 {rank:,}위",
        "raw": {"rank": int(rank), "total": int(total)},
        "unit": "rank",
        "source": source,
        "note": "",
    }


def _axis_metric(axis: str, label: str, value: Any) -> dict[str, Any] | None:
    return _score_item(label, value, source=f"DB.rule_location_score.{axis}")


def _axis_diff_label(target_axes: dict[str, Any], row: sqlite3.Row) -> str:
    labels = {
        "axis_sales": "시장성",
        "axis_competition": "경쟁 구조",
        "axis_demand": "수요 기반",
        "axis_accessibility": "접근·유입",
    }
    diffs = []
    for key, label in labels.items():
        target = _to_number(target_axes.get(key))
        other = _to_number(row[key])
        if target is None or other is None:
            continue
        diffs.append((abs(float(other) - float(target)), label, float(other) - float(target)))
    if not diffs:
        return "축별 차이 산출 불가"
    _, label, diff = max(diffs, key=lambda item: item[0])
    direction = "높습니다" if diff > 0 else "낮습니다"
    return f"선택 상권보다 {label} 평가가 {direction}"


def _latest_metric(metrics: list[dict[str, Any]], label: str) -> dict[str, Any] | None:
    for item in metrics:
        if item.get("label") == label:
            return item
    return None


def build_indicator_pack(payload: dict[str, Any]) -> dict[str, Any]:
    """Build the LLM input from the actual area x industry data used by scoring."""

    area_code = str(payload.get("area_code") or "")
    industry_code = str(payload.get("industry_code") or "")
    quarter = str(payload.get("quarter") or "")
    data_period_text = _quarter_period_text(quarter)

    with closing(_connect()) as conn:
        rule = conn.execute(
            """
            SELECT *
            FROM rule_location_score
            WHERE quarter = ? AND area_code = ? AND industry_code = ?
            LIMIT 1
            """,
            (quarter, area_code, industry_code),
        ).fetchone()
        latest_sales = _latest_row(conn, "district_sales", area_code, "timestamp, sales_amount", quarter, industry_code)
        latest_store = _latest_row(conn, "district_store_count", area_code, "timestamp, store_count", quarter, industry_code)
        latest_population = _latest_row(conn, "district_population", area_code, "timestamp, resident_population, worker_population", quarter)
        latest_floating = _latest_row(conn, "district_floating", area_code, "timestamp, floating_population", quarter)
        latest_sale_price_proxy = conn.execute(
            """
            SELECT period AS timestamp, sale_price_proxy_manwon_per_m2
            FROM area_sale_price_proxy
            WHERE area_code = ? AND period <= ?
            ORDER BY period DESC
            LIMIT 1
            """,
            (area_code, quarter),
        ).fetchone()
        rone_contract_columns = _select_or_null(
            conn,
            "area_rone_cost_reference",
            [
                "selection_group",
                "direct_value_allowed",
                "proxy_score_allowed",
                "engine_promotion_ready",
                "forbidden_claim_ko",
            ],
        )
        def rone_reference_options(metric_code: str) -> list[sqlite3.Row]:
            return conn.execute(
                f"""
                SELECT ref.period AS timestamp, ref.metric_code, ref.metric_value, ref.unit,
                       ref.property_type, ref.source_region_name, ref.mapping_scope,
                       ref.mapping_confidence, ref.provider, ref.source_id,
                       {rone_contract_columns}, ref.provenance_note
                FROM area_rone_cost_reference AS ref
                WHERE ref.area_code = ? AND ref.metric_code = ?
                  AND ref.period = (
                      SELECT MAX(candidate.period)
                      FROM area_rone_cost_reference AS candidate
                      WHERE candidate.area_code = ?
                        AND candidate.metric_code = ?
                        AND candidate.period <= ?
                  )
                ORDER BY
                    CASE ref.mapping_scope WHEN 'rone_level3_name_match_candidate' THEN 0 WHEN 'seoul_baseline_reference' THEN 1 ELSE 2 END,
                    CASE ref.property_type WHEN '중대형 상가' THEN 0 WHEN '집합 상가' THEN 1 WHEN '소규모 상가' THEN 2 ELSE 3 END,
                    ref.source_region_name ASC
                """,
                (area_code, metric_code, area_code, metric_code, quarter),
            ).fetchall()

        rent_reference_options = rone_reference_options("rent")
        vacancy_reference_options = rone_reference_options("vacancy")
        rent_reference = rent_reference_options[0] if rent_reference_options else None
        vacancy_reference = vacancy_reference_options[0] if vacancy_reference_options else None

        sales_history = _recent_rows(conn, "district_sales", area_code, "timestamp, sales_amount", quarter, 8, industry_code)
        store_history = _recent_rows(conn, "district_store_count", area_code, "timestamp, store_count", quarter, 8, industry_code)
        population_history = _recent_rows(conn, "district_population", area_code, "timestamp, resident_population, worker_population", quarter, 4)
        floating_history = _recent_rows(conn, "district_floating", area_code, "timestamp, floating_population", quarter, 4)
        growth_history = _recent_rows(
            conn,
            "district_growth_history",
            area_code,
            "timestamp, sales_amount, floating_population, store_count",
            quarter,
            6,
        )
        sale_price_history = conn.execute(
            """
            SELECT period AS timestamp, sale_price_proxy_manwon_per_m2
            FROM area_sale_price_proxy
            WHERE area_code = ? AND period <= ?
            ORDER BY period DESC
            LIMIT 4
            """,
            (area_code, quarter),
        ).fetchall()

        if not industry_code:
            latest_sales = conn.execute(
                """
                SELECT timestamp, SUM(sales_amount) AS sales_amount
                FROM district_sales
                WHERE area_code = ? AND timestamp = ?
                GROUP BY timestamp
                LIMIT 1
                """,
                (area_code, quarter),
            ).fetchone()
            latest_store = conn.execute(
                """
                SELECT timestamp, SUM(store_count) AS store_count
                FROM district_store_count
                WHERE area_code = ? AND timestamp = ?
                GROUP BY timestamp
                LIMIT 1
                """,
                (area_code, quarter),
            ).fetchone()
            sales_history = conn.execute(
                """
                SELECT timestamp, SUM(sales_amount) AS sales_amount
                FROM district_sales
                WHERE area_code = ? AND timestamp <= ?
                GROUP BY timestamp
                ORDER BY timestamp DESC
                LIMIT 8
                """,
                (area_code, quarter),
            ).fetchall()
            store_history = conn.execute(
                """
                SELECT timestamp, SUM(store_count) AS store_count
                FROM district_store_count
                WHERE area_code = ? AND timestamp <= ?
                GROUP BY timestamp
                ORDER BY timestamp DESC
                LIMIT 8
                """,
                (area_code, quarter),
            ).fetchall()

        sales_percentile = None
        if latest_sales and industry_code:
            sales_percentile = _percentile(
                conn,
                "district_sales",
                "sales_amount",
                "industry_code = ? AND timestamp = ?",
                (industry_code, latest_sales["timestamp"]),
                latest_sales["sales_amount"],
            )
        store_percentile = None
        if latest_store and industry_code:
            store_percentile = _percentile(
                conn,
                "district_store_count",
                "store_count",
                "industry_code = ? AND timestamp = ?",
                (industry_code, latest_store["timestamp"]),
                latest_store["store_count"],
            )

        score_value = _row_value(rule, "current_location_score", payload.get("score"))
        score_official_eligible = bool(
            _row_value(rule, "official_rank_eligible", payload.get("official_rank_eligible", False))
        )
        if industry_code and not score_official_eligible:
            score_value = None
        score_percentile = None
        score_rank = None
        score_total = None
        if score_value is not None:
            if industry_code:
                score_percentile = _percentile(
                    conn,
                    "rule_location_score",
                    "current_location_score",
                    "quarter = ? AND industry_code = ? AND official_rank_eligible = 1",
                    (quarter, industry_code),
                    score_value,
                )
                rank_row = conn.execute(
                    """
                    SELECT
                        1 + SUM(CASE WHEN current_location_score > ? THEN 1 ELSE 0 END) AS rank,
                        COUNT(*) AS total
                    FROM rule_location_score
                    WHERE quarter = ? AND industry_code = ?
                      AND official_rank_eligible = 1
                      AND current_location_score IS NOT NULL
                    """,
                    (score_value, quarter, industry_code),
                ).fetchone()
            else:
                rank_row = conn.execute(
                    """
                    WITH area_context AS (
                        SELECT
                            area_code,
                            (MAX(axis_demand) + MAX(axis_accessibility)) / 2.0 AS score
                        FROM rule_location_score
                        WHERE quarter = ?
                        GROUP BY area_code
                        HAVING MAX(axis_demand) IS NOT NULL
                           AND MAX(axis_accessibility) IS NOT NULL
                    )
                    SELECT
                        1 + SUM(CASE WHEN score > ? THEN 1 ELSE 0 END) AS rank,
                        COUNT(*) AS total,
                        100.0 * SUM(CASE WHEN score <= ? THEN 1 ELSE 0 END) / COUNT(*) AS percentile
                    FROM area_context
                    """,
                    (quarter, score_value, score_value),
                ).fetchone()
            if rank_row:
                score_rank = int(rank_row["rank"] or 0)
                score_total = int(rank_row["total"] or 0)
                if not industry_code:
                    score_percentile = _to_number(rank_row["percentile"])

        target_axes = {
            "axis_sales": _row_value(rule, "axis_sales", (payload.get("axes") or {}).get("axis_sales")),
            "axis_competition": _row_value(rule, "axis_competition", (payload.get("axes") or {}).get("axis_competition")),
            "axis_demand": _row_value(rule, "axis_demand", (payload.get("axes") or {}).get("axis_demand")),
            "axis_accessibility": _row_value(rule, "axis_accessibility", (payload.get("axes") or {}).get("axis_accessibility")),
        }
        if industry_code:
            alternatives_rows = conn.execute(
                """
                WITH scored AS (
                    SELECT
                        area_code,
                        area_name,
                        current_location_score,
                        grade,
                        cost_risk_score,
                        axis_sales,
                        axis_competition,
                        axis_demand,
                        axis_accessibility
                    FROM rule_location_score
                    WHERE quarter = ?
                      AND industry_code = ?
                      AND official_rank_eligible = 1
                      AND current_location_score IS NOT NULL
                ), graded AS (
                    SELECT
                        *,
                        CUME_DIST() OVER (ORDER BY current_location_score) AS score_percentile
                    FROM scored
                )
                SELECT
                    *,
                    CASE
                        WHEN grade = 'A' AND score_percentile > 0.9 THEN 'A+'
                        WHEN grade = 'B' AND score_percentile > 0.7 THEN 'B+'
                        WHEN grade = 'C' AND score_percentile > 0.5 THEN 'C+'
                        WHEN grade = 'D' AND score_percentile > 0.3 THEN 'D+'
                        WHEN grade = 'E' AND score_percentile > 0.1 THEN 'E+'
                        ELSE grade
                    END AS display_grade
                FROM graded
                WHERE area_code != ?
                ORDER BY current_location_score DESC, area_name ASC
                LIMIT 5
                """,
                (quarter, industry_code, area_code),
            ).fetchall()
        else:
            alternatives_rows = conn.execute(
                """
                WITH area_context AS (
                    SELECT
                        area_code,
                        COALESCE(MAX(NULLIF(area_name, '')), area_code) AS area_name,
                        (MAX(axis_demand) + MAX(axis_accessibility)) / 2.0 AS current_location_score,
                        MAX(cost_risk_score) AS cost_risk_score,
                        MAX(axis_demand) AS axis_demand,
                        MAX(axis_accessibility) AS axis_accessibility
                    FROM rule_location_score
                    WHERE quarter = ?
                    GROUP BY area_code
                    HAVING MAX(axis_demand) IS NOT NULL
                       AND MAX(axis_accessibility) IS NOT NULL
                ), ranked AS (
                    SELECT
                        *,
                        CUME_DIST() OVER (ORDER BY current_location_score) AS score_percentile
                    FROM area_context
                )
                SELECT
                    area_code,
                    area_name,
                    current_location_score,
                    cost_risk_score,
                    NULL AS axis_sales,
                    NULL AS axis_competition,
                    axis_demand,
                    axis_accessibility,
                    CASE
                        WHEN score_percentile > 0.9 THEN 'A+'
                        WHEN score_percentile > 0.8 THEN 'A'
                        WHEN score_percentile > 0.7 THEN 'B+'
                        WHEN score_percentile > 0.6 THEN 'B'
                        WHEN score_percentile > 0.5 THEN 'C+'
                        WHEN score_percentile > 0.4 THEN 'C'
                        WHEN score_percentile > 0.3 THEN 'D+'
                        WHEN score_percentile > 0.2 THEN 'D'
                        WHEN score_percentile > 0.1 THEN 'E+'
                        ELSE 'E'
                    END AS display_grade
                FROM ranked
                WHERE area_code != ?
                ORDER BY current_location_score DESC, area_name ASC
                LIMIT 5
                """,
                (quarter, area_code),
            ).fetchall()

        total_store_row = None
        if latest_store:
            total_store_row = conn.execute(
                """
                SELECT SUM(store_count) AS total_store_count
                FROM district_store_count
                WHERE area_code = ? AND timestamp = ?
                """,
                (area_code, latest_store["timestamp"]),
            ).fetchone()

        seoul_sales_rank = None
        seoul_sales_total = None
        area_sales_rank = None
        area_sales_total = None
        area_top_industries: list[dict[str, Any]] = []
        if latest_sales and industry_code:
            seoul_sales_row = conn.execute(
                """
                SELECT
                    1 + SUM(CASE WHEN sales_amount > ? THEN 1 ELSE 0 END) AS rank,
                    COUNT(*) AS total
                FROM district_sales
                WHERE timestamp = ? AND industry_code = ? AND sales_amount IS NOT NULL
                """,
                (latest_sales["sales_amount"], latest_sales["timestamp"], industry_code),
            ).fetchone()
            if seoul_sales_row:
                seoul_sales_rank = int(seoul_sales_row["rank"] or 0)
                seoul_sales_total = int(seoul_sales_row["total"] or 0)
        if latest_sales:
            area_sales_rows = conn.execute(
                """
                SELECT industry_code, industry_name, sales_amount
                FROM district_sales
                WHERE timestamp = ? AND area_code = ? AND sales_amount IS NOT NULL
                ORDER BY sales_amount DESC, industry_name ASC
                """,
                (latest_sales["timestamp"], area_code),
            ).fetchall()
            area_sales_total = len(area_sales_rows)
            for idx, row in enumerate(area_sales_rows, 1):
                if industry_code and str(row["industry_code"]) == industry_code:
                    area_sales_rank = idx
                if idx <= 8:
                    area_top_industries.append(
                        {
                            "rank": idx,
                            "industry_code": row["industry_code"],
                            "industry_name": row["industry_name"],
                            "sales_amount": _metric(
                                "상권·업종 합산 추정매출",
                                row["sales_amount"],
                                unit="원",
                                source="DB.district_sales.sales_amount",
                            ),
                        }
                    )

    sales_metrics = []
    if latest_sales:
        sales_metrics.append(
            _metric(
                SALES_AGGREGATE_LABEL,
                latest_sales["sales_amount"],
                unit="원",
                source="DB.district_sales.sales_amount",
                note=f"{_quarter_label(latest_sales['timestamp'])} 기준",
            )
        )
    if sales_percentile is not None:
        sales_metrics.append(
            _metric("동업종 내 매출 위치", sales_percentile, unit="percentile", source="DB.district_sales percentile")
        )
    seoul_rank_metric = _rank_metric(
        "동일 업종 서울 매출 순위",
        seoul_sales_rank,
        seoul_sales_total,
        source="DB.district_sales same industry rank",
    )
    if seoul_rank_metric:
        sales_metrics.append(seoul_rank_metric)
    area_rank_metric = _rank_metric(
        "상권 내 업종 매출 순위",
        area_sales_rank,
        area_sales_total,
        source="DB.district_sales area industry rank",
    )
    if area_rank_metric:
        sales_metrics.append(area_rank_metric)
    sales_metrics = [item for item in sales_metrics if item]

    competition_metrics = []
    if latest_store and industry_code:
        competition_metrics.append(
            _metric(
                "동업종 점포수",
                latest_store["store_count"],
                unit="개",
                source="DB.district_store_count.store_count",
                note=f"{_quarter_label(latest_store['timestamp'])} 기준",
            )
        )
    if store_percentile is not None:
        competition_metrics.append(
            _metric("동업종 점포수 위치", store_percentile, unit="percentile", source="DB.district_store_count percentile")
        )
    total_store_count = _to_number(total_store_row["total_store_count"]) if total_store_row else None
    if total_store_count:
        competition_metrics.append(
            _metric("상권 전체 점포수", total_store_count, unit="개", source="DB.district_store_count SUM(store_count)")
        )
    if (
        industry_code
        and total_store_count
        and latest_store
        and _to_number(latest_store["store_count"]) is not None
    ):
        competition_metrics.append(
            _metric(
                "동업종 점포 비중",
                float(latest_store["store_count"]) / float(total_store_count),
                unit="fraction_percent",
                source="DB.district_store_count same industry / total",
            )
        )

    demand_metrics = []
    if latest_population:
        demand_metrics.extend(
            [
                _metric("상주인구", latest_population["resident_population"], unit="명", source="DB.district_population.resident_population"),
                _metric("직장인구", latest_population["worker_population"], unit="명", source="DB.district_population.worker_population"),
            ]
        )
    if latest_floating:
        floating_total = _to_number(latest_floating["floating_population"])
        floating_period = str(latest_floating["timestamp"] or quarter)
        floating_days = _quarter_day_count(floating_period)
        demand_metrics.append(
            _metric(
                "총 유동인구",
                floating_total,
                unit="명",
                source="DB.district_floating.floating_population",
                note=f"{_quarter_label(floating_period)} 3개월 누계",
            )
        )
        if floating_total is not None and floating_days:
            demand_metrics.append(
                _metric(
                    "일평균 유동인구",
                    round(float(floating_total) / floating_days),
                    unit="명/일",
                    source="DB.district_floating.floating_population / quarter_day_count",
                    note=f"{_quarter_label(floating_period)} 누계 {float(floating_total):,.0f}명 / {floating_days}일",
                )
            )
    if latest_population and _to_number(latest_population["worker_population"]):
        resident_worker_ratio = float(latest_population["resident_population"]) / float(latest_population["worker_population"])
        demand_metrics.append(
            _metric(
                "상주/직장 비율",
                resident_worker_ratio,
                unit="fraction_percent",
                source="DB.district_population resident/worker",
            )
        )
    demand_metrics = [item for item in demand_metrics if item]

    accessibility_metrics: list[dict[str, Any]] = []

    cost_metrics = []
    if rent_reference:
        cost_metrics.append(
            _metric(
                "R-ONE 임대료 참고",
                rent_reference["metric_value"],
                unit=str(rent_reference["unit"] or "천원/㎡"),
                source="DB.area_rone_cost_reference.metric_value",
                note=(
                    f"{rent_reference['provider'] or '한국부동산원 R-ONE'} · "
                    f"{rent_reference['source_region_name'] or '서울 기준'} · "
                    f"{rent_reference['property_type'] or '상가'} · "
                    f"{rent_reference['mapping_scope'] or '매핑 범위 미상'} · "
                    "engine_promotion_ready=False, 공식 예산 점수 미산출"
                ),
            )
        )
    if vacancy_reference:
        cost_metrics.append(
            _metric(
                "R-ONE 공실률 참고",
                vacancy_reference["metric_value"],
                unit=str(vacancy_reference["unit"] or "%"),
                source="DB.area_rone_cost_reference.metric_value",
                note=(
                    f"{vacancy_reference['source_region_name'] or '서울 기준'} · "
                    f"{vacancy_reference['property_type'] or '상가'} · "
                    f"{vacancy_reference['mapping_scope'] or '매핑 범위 미상'} · "
                    "engine_promotion_ready=False, evidence-only"
                ),
            )
        )
    if latest_sale_price_proxy:
        cost_metrics.append(
            _metric(
                "RTMS 상업용 부동산 매매가 프록시",
                latest_sale_price_proxy["sale_price_proxy_manwon_per_m2"],
                unit="만원/㎡",
                source="DB.area_sale_price_proxy.sale_price_proxy_manwon_per_m2",
                note="상업·업무용 부동산 매매 실거래의 건물면적당 중앙값으로, 실제 임대료·권리금이 아님",
            )
        )

    extra = payload.get("extra_signals") or {}
    if extra.get("cost_risk_score") is not None:
        cost_metrics.insert(
            0,
            _score_item(
                "비용 여건 등급",
                extra.get("cost_risk_score"),
                source="DB.rule_location_score.cost_risk_score",
                note="임대료·권리금 직접값이 아니라 비용 리스크 프록시",
            ),
        )

    axes = {
        "axis_sales": target_axes.get("axis_sales"),
        "axis_competition": target_axes.get("axis_competition"),
        "axis_demand": target_axes.get("axis_demand"),
        "axis_accessibility": target_axes.get("axis_accessibility"),
    }
    target_score = _row_value(rule, "current_location_score", payload.get("score"))
    target_grade = _row_value(rule, "grade", payload.get("grade"))
    calculated_display_grade = _detailed_grade(
        target_grade,
        score_percentile,
        target_score,
    )
    target_display_grade = (
        calculated_display_grade
        if rule is not None
        else _validated_display_grade(payload.get("display_grade"), target_grade)
        or calculated_display_grade
    )
    decision_label = _row_value(rule, "decision_label", payload.get("decision_label"))
    score_version = _row_value(rule, "score_version", payload.get("score_version"))
    score_source = payload.get("score_source") or ("DB.rule_location_score" if rule else "payload")
    legacy_rule = bool(rule) and score_version != EXPECTED_COVERAGE_SCORE_VERSION
    if legacy_rule:
        target_grade = None
        target_display_grade = None
        decision_label = "레거시 점수는 v2.6 coverage 검증 전 참고값으로만 유지"
    available_axes = [axis for axis, value in axes.items() if _to_number(value) is not None]
    inferred_missing_axes = [
        axis.removeprefix("axis_") for axis in axes if axis not in available_axes
    ]
    score_coverage = {
        "tier": (
            "legacy_reference"
            if legacy_rule
            else _row_value(rule, "score_coverage_tier", payload.get("score_coverage_tier"))
        ),
        "available_axis_count": (
            len(available_axes)
            if legacy_rule
            else _row_value(rule, "available_axis_count", payload.get("available_axis_count"))
        ),
        "official_indicator_count": (
            None
            if legacy_rule
            else _row_value(rule, "official_indicator_count", payload.get("official_indicator_count"))
        ),
        "official_indicator_defined_count": (
            None
            if legacy_rule
            else _row_value(
                rule,
                "official_indicator_defined_count",
                payload.get("official_indicator_defined_count"),
            )
        ),
        "official_indicator_complete": (
            False
            if legacy_rule
            else bool(
                _row_value(
                    rule,
                    "official_indicator_complete",
                    payload.get("official_indicator_complete", False),
                )
            )
        ),
        "missing_axes": (
            inferred_missing_axes
            if legacy_rule
            else _parse_missing_axes(_row_value(rule, "missing_axes", payload.get("missing_axes")))
        ),
        "reason": (
            "레거시 점수는 v2.6 coverage 계약 검증 전 참고값으로만 유지합니다."
            if legacy_rule
            else _row_value(rule, "coverage_reason", payload.get("coverage_reason"))
        ),
        "official_rank_eligible": (
            False
            if legacy_rule
            else bool(_row_value(rule, "official_rank_eligible", payload.get("official_rank_eligible")))
        ),
        "context_location_score": (
            target_score
            if legacy_rule
            else _row_value(rule, "context_location_score", payload.get("context_location_score"))
        ),
    }
    score_metric_label = (
        "레거시 입지 참고 등급"
        if legacy_rule
        else "입지 종합 등급"
        if industry_code and score_coverage["official_rank_eligible"]
        else "가용 축 입지 맥락 등급"
        if industry_code
        else "상권 수요·접근성 맥락 등급"
    )
    score_metric = _score_item(score_metric_label, target_score, source=score_source)
    if score_metric:
        score_metric["grade"] = target_display_grade
        score_metric["display"] = target_display_grade or "등급 보류"
    score_position_metric = None if legacy_rule else _metric(
        "동일 업종 내 위치" if industry_code else "상권 수요·접근성 맥락 위치",
        score_percentile,
        unit="percentile",
        source="DB.rule_location_score percentile" if industry_code else "DB.rule_location_score.area_context_2axis percentile",
        note=f"서울 {score_total:,}개 후보 중 {score_rank:,}위" if score_rank and score_total else "",
    )
    axis_score_metrics = {
        "sales": _axis_metric("axis_sales", "시장성 등급", axes.get("axis_sales")),
        "competition": _axis_metric("axis_competition", "경쟁 구조 등급", axes.get("axis_competition")),
        "demand": _axis_metric("axis_demand", "수요 기반 등급", axes.get("axis_demand")),
        "accessibility": _axis_metric("axis_accessibility", "접근·유입 등급", axes.get("axis_accessibility")),
    }
    supporting_score_metrics = {
        "cost_risk_score": _score_item("비용 여건 등급", extra.get("cost_risk_score"), source="DB.rule_location_score.cost_risk_score"),
        "data_reliability_score": _score_item("데이터 신뢰도", extra.get("data_reliability_score"), source="DB.rule_location_score.data_reliability_score"),
        "growth_potential_score": _score_item("성장/안정성", extra.get("growth_potential_score"), source="DB.rule_location_score.growth_potential_score"),
    }
    alternative_score_label = "입지 종합 등급" if industry_code else "상권 수요·접근성 맥락 등급"
    alternative_score_source = (
        "DB.rule_location_score.current_location_score"
        if industry_code
        else "DB.rule_location_score.area_context_2axis"
    )
    alternatives = [] if legacy_rule else [
        {
            "area_code": row["area_code"],
            "area_name": row["area_name"],
            "display_grade": row["display_grade"],
            "current_location_score": dict(
                _score_item(
                    alternative_score_label,
                    row["current_location_score"],
                    source=alternative_score_source,
                )
                or {},
                grade=row["display_grade"],
                display=row["display_grade"],
            ),
            "cost_risk_score": _score_item(
                "비용 여건 등급",
                row["cost_risk_score"],
                source="DB.rule_location_score.cost_risk_score",
            ),
            "major_differential_axis": _axis_diff_label(axes, row),
        }
        for row in alternatives_rows
    ]

    sales_amount_metric = _latest_metric(sales_metrics, SALES_AGGREGATE_LABEL)
    store_count_metric = _latest_metric(competition_metrics, "동업종 점포수")
    cost_metric = cost_metrics[0] if cost_metrics else None
    facts_pack = {
        "target": {
            "quarter": quarter,
            "area_code": area_code,
            "area_name": payload.get("area_name"),
            "industry_code": industry_code or None,
            "industry_name": payload.get("industry_name"),
        },
        "score_block": {
            "current_location_score": score_metric,
            "grade": target_grade,
            "display_grade": target_display_grade,
            "decision_label": decision_label,
            "score_percentile": score_position_metric,
            "score_rank": (
                None
                if legacy_rule
                else f"서울 {score_total:,}개 후보 중 {score_rank:,}위"
                if score_rank and score_total
                else None
            ),
            "score_version": score_version,
            "quarter": quarter,
            "axis_scores": axis_score_metrics,
            "coverage": score_coverage,
            "supporting_signals": supporting_score_metrics,
        },
        "sales_block": {
            "metrics": sales_metrics[:14],
            "sales_amount": sales_amount_metric,
            "sales_count": _latest_metric(sales_metrics, "매출 건수"),
            "ticket_size": _latest_metric(sales_metrics, "평균 객단가"),
            "store_count": store_count_metric,
            "sales_per_store": _latest_metric(sales_metrics, "점포당 매출"),
            "seoul_rank": seoul_rank_metric,
            "area_rank": area_rank_metric,
            "area_top_industries": area_top_industries,
            "sales_trend": _series(sales_history, ["sales_amount"], "DB.district_sales"),
        },
        "competition_block": {
            "metrics": competition_metrics[:12],
            "same_industry_store_count": store_count_metric,
            "total_store_count": _latest_metric(competition_metrics, "상권 전체 점포수"),
            "same_industry_ratio": _latest_metric(competition_metrics, "동업종 점포 비중"),
            "store_trend": _series(store_history, ["store_count"], "DB.district_store_count"),
        },
        "demand_block": {
            "metrics": demand_metrics[:14],
            "resident_population": _latest_metric(demand_metrics, "상주인구") or _latest_metric(demand_metrics, "총 상주인구"),
            "worker_population": _latest_metric(demand_metrics, "직장인구") or _latest_metric(demand_metrics, "총 직장인구"),
            "floating_population": _latest_metric(demand_metrics, "총 유동인구"),
            "floating_population_daily_average": _latest_metric(demand_metrics, "일평균 유동인구"),
            "resident_worker_ratio": _latest_metric(demand_metrics, "상주/직장 비율"),
        },
        "accessibility_block": {
            "metrics": accessibility_metrics[:12],
        },
        "cost_block": {
            "metrics": cost_metrics[:6],
            "cost_risk_score": supporting_score_metrics["cost_risk_score"],
            "rent_reference_latest": _latest_metric(cost_metrics, "R-ONE 임대료 참고"),
            "vacancy_reference_latest": _latest_metric(cost_metrics, "R-ONE 공실률 참고"),
            "sale_price_proxy_latest": _latest_metric(cost_metrics, "RTMS 상업용 부동산 매매가 프록시"),
            "sale_price_proxy_trend": _series(
                sale_price_history[:2],
                ["sale_price_proxy_manwon_per_m2"],
                "DB.area_sale_price_proxy",
            ),
            "rone_contract": {
                "direct_value_allowed": bool(_row_value(rent_reference, "direct_value_allowed", 0)),
                "proxy_score_allowed": bool(_row_value(rent_reference, "proxy_score_allowed", 0)),
                "engine_promotion_ready": bool(_row_value(rent_reference, "engine_promotion_ready", 0)),
                "forbidden_claim_ko": _row_value(rent_reference, "forbidden_claim_ko"),
            },
            "selected_reference": {
                "rent": dict(rent_reference) if rent_reference else None,
                "vacancy": dict(vacancy_reference) if vacancy_reference else None,
            },
            "reference_options": {
                "rent": [dict(row) for row in rent_reference_options],
                "vacancy": [dict(row) for row in vacancy_reference_options],
            },
            "evidence_trace": {
                "contract_status": "evidence_loader_allowed_not_promoted",
                "selection_rule": "latest_period_then_candidate_scope_then_property_type",
                "selection_groups": sorted({
                    str(_row_value(row, "selection_group"))
                    for row in [*rent_reference_options, *vacancy_reference_options]
                    if _row_value(row, "selection_group")
                }),
                "option_count": len(rent_reference_options) + len(vacancy_reference_options),
                "score_fields_withheld": True,
            },
            "proxy_notice": "R-ONE 임대료·공실률은 지역명 후보 매핑 또는 서울 전체 기준선(상권 직접값 아님) evidence이며 공식 예산 적합도 점수를 만들지 않습니다. RTMS 값은 임대료가 아닌 매매가격 압력 프록시입니다.",
            "budget_fit": payload.get("budget_fit") or None,
        },
        "alternatives": alternatives,
        "user_condition": payload.get("user_condition") or {},
        "data_period_text": data_period_text,
        "gold_row_found": False,
        "detail_source": "product_db",
    }
    chart_manifest = [
        {
            "id": "C1",
            "type": "axis_bar",
            "title": "입지 평가 축(결측 제외) 및 보조신호",
            "key_values": [
                _metric_display(axis_score_metrics["sales"]),
                _metric_display(axis_score_metrics["competition"]),
                _metric_display(axis_score_metrics["demand"]),
                _metric_display(axis_score_metrics["accessibility"]),
            ],
        },
        {
            "id": "C2",
            "type": "sales_trend",
            "title": "최근 8분기 매출 추이",
            "key_values": [_metric_display(sales_amount_metric)],
        },
        {
            "id": "C3",
            "type": "industry_rank",
            "title": "상권 내 업종 지표",
            "key_values": [_metric_display(store_count_metric), _metric_display(_latest_metric(sales_metrics, "점포당 매출"))],
        },
        {
            "id": "C4",
            "type": "alt_compare",
            "title": "동일 업종 상위 대안 비교" if industry_code else "상권 수요·접근성 맥락 대안 비교",
            "key_values": [f"{item['area_name']} {_metric_display(item['current_location_score'])}" for item in alternatives[:3]],
        },
        {
            "id": "C5",
            "type": "demand_mix",
            "title": "수요 인구 지표",
            "key_values": [
                _metric_display(facts_pack["demand_block"]["resident_population"]),
                _metric_display(facts_pack["demand_block"]["worker_population"]),
                _metric_display(facts_pack["demand_block"]["floating_population_daily_average"]),
            ],
        },
    ]
    facts_lite = {
        "target": facts_pack["target"],
        "score": score_metric,
        "grade": target_grade,
        "display_grade": target_display_grade,
        "percentile": score_position_metric,
        "sales": sales_amount_metric,
        "same_industry_store_count": store_count_metric,
        "cost_indicator": cost_metric,
        "top_alternative": alternatives[0] if alternatives else None,
        "decision_label": decision_label,
    }
    facts_pack_display = _display_only(facts_pack)
    raw_budget_fit = (facts_pack.get("cost_block") or {}).get("budget_fit") or {}
    display_cost_block = facts_pack_display.get("cost_block") or {}
    display_cost_block["budget_fit"] = _budget_fit_display(
        raw_budget_fit,
        display_cost_block.get("budget_fit"),
    )
    facts_pack_display["cost_block"] = display_cost_block
    display_score_block = facts_pack_display.get("score_block") or {}
    display_coverage = display_score_block.get("coverage") or {}
    display_coverage["tier"] = public_coverage_tier(score_coverage.get("tier"))
    display_coverage["missing_axes"] = public_axis_labels(score_coverage.get("missing_axes") or [])
    display_coverage["reason"] = public_coverage_reason(score_coverage)
    display_score_block["coverage"] = display_coverage
    display_score_block["decision_label"] = public_coverage_header(score_coverage, decision_label)
    facts_pack_display["score_block"] = display_score_block

    facts_lite_display = _display_only(facts_lite)
    facts_lite_display["decision_label"] = public_coverage_header(score_coverage, decision_label)
    pack = {
        "target": {
            "quarter": quarter,
            "area_code": area_code,
            "area_name": payload.get("area_name"),
            "industry_code": industry_code or None,
            "industry_name": payload.get("industry_name"),
            "score": target_score,
            "grade": target_grade,
            "display_grade": target_display_grade,
            "decision_label": decision_label,
            "score_source": score_source,
            "score_version": score_version,
            "score_coverage": score_coverage,
        },
        "axis_scores": {
            "sales": axes.get("axis_sales"),
            "competition": axes.get("axis_competition"),
            "demand": axes.get("axis_demand"),
            "accessibility": axes.get("axis_accessibility"),
        },
        "axis_indicator_pack": {
            "sales": {
                "axis_score": axes.get("axis_sales"),
                "score_drivers": sales_metrics,
                "recent_series": _series(sales_history, ["sales_amount"], "DB.district_sales"),
                "missing": ["sales"] if axes.get("axis_sales") is None else [],
            },
            "competition": {
                "axis_score": axes.get("axis_competition"),
                "score_drivers": competition_metrics,
                "recent_series": _series(store_history, ["store_count"], "DB.district_store_count"),
                "missing": ["competition"] if axes.get("axis_competition") is None else [],
            },
            "demand": {
                "axis_score": axes.get("axis_demand"),
                "score_drivers": demand_metrics,
                "recent_population": _series(population_history, ["resident_population", "worker_population"], "DB.district_population"),
                "recent_floating": _series(floating_history, ["floating_population"], "DB.district_floating"),
                "missing": ["demand"] if axes.get("axis_demand") is None else [],
            },
            "accessibility": {
                "axis_score": axes.get("axis_accessibility"),
                "score_drivers": accessibility_metrics,
                "missing": ["accessibility"] if axes.get("axis_accessibility") is None else [],
            },
        },
        "supporting_indicators": {
            "cost_risk_score": extra.get("cost_risk_score"),
            "data_reliability_score": extra.get("data_reliability_score"),
            "conservative_score_owa": extra.get("conservative_score_owa"),
            "growth_potential_score": extra.get("growth_potential_score"),
            "growth_rebound_candidate_score": extra.get("growth_rebound_candidate_score"),
            "cost_metrics": cost_metrics,
            "budget_fit": payload.get("budget_fit") or None,
            "area_growth_series": _series(growth_history, ["sales_amount", "floating_population", "store_count"], "DB.district_growth_history"),
            "sale_price_proxy_series": _series(
                sale_price_history,
                ["sale_price_proxy_manwon_per_m2"],
                "DB.area_sale_price_proxy",
            ),
        },
        "facts_pack": facts_pack,
        "facts_pack_display": facts_pack_display,
        "facts_lite": facts_lite,
        "facts_lite_display": facts_lite_display,
        "chart_manifest": _display_only(chart_manifest),
        "data_sources": [
            "score_model",
            "seoul_sales_trade_area",
            "seoul_store_trade_area",
            "seoul_floating_population_trade_area",
            "seoul_resident_population_trade_area",
            "seoul_worker_population_trade_area",
            "seoul_facility_trade_area",
            "seoul_living_migration",
            "molit_rtms_commercial_trade",
            "reb_small_shop_rent",
        ],
        "data_period_text": data_period_text,
        "gold_row_found": False,
        "detail_source": "product_db",
    }
    return pack
