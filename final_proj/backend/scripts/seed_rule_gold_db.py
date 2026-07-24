from __future__ import annotations

import atexit
import hashlib
import json
import re
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path

import pandas as pd


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.settings import DATA_ROOT, DATABASE_BACKUP_ROOT, DATABASE_PATH


DB_PATH = DATABASE_PATH
DATACORPUS_ROOT = DATA_ROOT
GOLD = DATACORPUS_ROOT / "_gold"
LOCATION_OUTPUTS = DATACORPUS_ROOT / "_location_judgement_outputs"
RONE_COST_BRIDGE = GOLD / "gold_cost_risk_rone_region_trade_area_candidate.csv"
GOLD_MANIFEST = DATACORPUS_ROOT / "_gold_validation" / "23_gold_output_manifest.csv"
WORKSPACE_ROOT = BACKEND_ROOT.parents[1]
COVERAGE_SCORE_VERSION = "loc_score.v2.6-coverage-contract-rc1"
RELIABILITY_GATE = 40.0

PRODUCT_TABLES_TO_DROP = (
    "district_vacancy",
    "district_rent",
    "area_sale_price_proxy",
    "area_rone_cost_reference",
    "district_growth_history",
    "district_store_count",
    "district_sales",
    "district_floating",
    "district_population",
    "commercial_area",
    "store",
    "sales",
    "population",
    "real_estate",
    "rule_location_score",
    "rule_area_score_summary",
    "industry_hierarchy",
    "location_lookup",
    "test",
)

PRODUCT_TABLES_TO_PUBLISH = (
    "commercial_area",
    "district_population",
    "district_floating",
    "district_sales",
    "district_store_count",
    "district_growth_history",
    "area_sale_price_proxy",
    "area_rone_cost_reference",
    "rule_location_score",
    "rule_area_score_summary",
    "industry_hierarchy",
    "location_lookup",
)


def read_gold(filename: str, usecols: list[str] | None = None) -> pd.DataFrame:
    return pd.read_csv(
        GOLD / filename,
        usecols=usecols,
        encoding="utf-8-sig",
        low_memory=False,
        dtype={
            "상권_코드": str,
            "기준_년분기_코드": str,
            "서비스_업종_코드": str,
            "자치구_코드": str,
        },
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def latest_score_batch() -> Path:
    if not GOLD_MANIFEST.exists():
        raise FileNotFoundError(f"Gold manifest is missing: {GOLD_MANIFEST}")
    sales_path = GOLD / "gold_sales_strength_q_industry.csv"
    quarter_column = "기준_년분기_코드"
    sales_quarters = pd.read_csv(
        sales_path,
        usecols=[quarter_column],
        dtype={quarter_column: str},
        encoding="utf-8-sig",
    )[quarter_column].dropna().astype(str)
    if sales_quarters.empty:
        raise RuntimeError(f"Gold sales has no quarter values: {sales_path}")
    expected_quarter = max(sales_quarters, key=int)
    expected_gold_manifest_hash = sha256_file(GOLD_MANIFEST)

    candidates: list[tuple[str, Path]] = []
    rejection_reasons: list[str] = []
    for manifest_path in LOCATION_OUTPUTS.glob(f"loc_score_v2_batch_{expected_quarter}_*.manifest.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("schema_version") != "localfit.score_batch_manifest.v1":
                raise ValueError("unsupported schema_version")
            if str(manifest.get("score_version") or "") != COVERAGE_SCORE_VERSION:
                raise ValueError(
                    f"manifest score_version mismatch: expected {COVERAGE_SCORE_VERSION}, "
                    f"got {manifest.get('score_version')!r}"
                )
            if str(manifest.get("analysis_quarter")) != expected_quarter:
                raise ValueError("analysis_quarter mismatch")
            if manifest.get("gold_manifest_sha256") != expected_gold_manifest_hash:
                raise ValueError("gold manifest lineage mismatch")

            batch_path = (WORKSPACE_ROOT / str(manifest.get("batch_path", ""))).resolve()
            expected_batch_path = manifest_path.with_name(
                manifest_path.name.removesuffix(".manifest.json") + ".csv"
            ).resolve()
            if batch_path != expected_batch_path or batch_path.parent != LOCATION_OUTPUTS.resolve():
                raise ValueError("batch path mismatch")
            if not batch_path.exists() or sha256_file(batch_path) != manifest.get("batch_sha256"):
                raise ValueError("batch sha256 mismatch")

            batch_contract = pd.read_csv(
                batch_path,
                usecols=[quarter_column, "score_version"],
                dtype={quarter_column: str, "score_version": str},
                encoding="utf-8-sig",
            )
            if len(batch_contract) != int(manifest.get("row_count", -1)):
                raise ValueError("batch row_count mismatch")
            if set(batch_contract[quarter_column].dropna().astype(str).unique()) != {expected_quarter}:
                raise ValueError("batch quarter values mismatch")
            batch_versions = set(batch_contract["score_version"].dropna().astype(str).unique())
            if batch_versions != {COVERAGE_SCORE_VERSION}:
                raise ValueError(
                    f"batch score_version mismatch: expected {COVERAGE_SCORE_VERSION}, got {sorted(batch_versions)}"
                )
            candidates.append((str(manifest.get("generated_at", "")), batch_path))
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            rejection_reasons.append(f"{manifest_path.name}: {exc}")

    if not candidates:
        details = "; ".join(rejection_reasons[-5:]) or "no lineage manifest found"
        raise FileNotFoundError(
            f"No score batch matches current Gold quarter={expected_quarter}, "
            f"manifest_sha256={expected_gold_manifest_hash}: {details}"
        )
    return max(candidates, key=lambda item: (item[0], item[1].name))[1]


def replace_tables(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    for table in PRODUCT_TABLES_TO_DROP:
        cur.execute(f'DROP TABLE IF EXISTS "{table}"')

    cur.executescript(
        """
        CREATE TABLE commercial_area (
            area_code TEXT PRIMARY KEY,
            area_name TEXT,
            district_code TEXT,
            latitude REAL,
            longitude REAL
        );

        CREATE TABLE district_population (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            area_code TEXT,
            district_name TEXT,
            resident_population INTEGER DEFAULT 0,
            worker_population INTEGER DEFAULT 0,
            timestamp TEXT
        );

        CREATE TABLE district_floating (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            area_code TEXT,
            floating_population INTEGER DEFAULT 0,
            timestamp TEXT
        );

        CREATE TABLE district_sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            area_code TEXT,
            industry_code TEXT,
            industry_name TEXT,
            sales_amount REAL DEFAULT 0,
            timestamp TEXT
        );

        CREATE TABLE district_store_count (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            area_code TEXT,
            industry_code TEXT,
            industry_name TEXT,
            store_count INTEGER DEFAULT 0,
            timestamp TEXT
        );

        CREATE TABLE district_growth_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            area_code TEXT,
            sales_amount REAL DEFAULT 0,
            floating_population INTEGER DEFAULT 0,
            store_count INTEGER DEFAULT 0,
            timestamp TEXT
        );

        CREATE TABLE area_sale_price_proxy (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            area_code TEXT,
            sale_price_proxy_manwon_per_m2 REAL,
            period TEXT,
            source_id TEXT,
            provider TEXT,
            grain TEXT,
            direct_score_allowed INTEGER DEFAULT 0,
            proxy_score_allowed INTEGER DEFAULT 1,
            provenance_note TEXT
        );

        CREATE TABLE area_rone_cost_reference (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            area_code TEXT,
            period TEXT,
            selection_group TEXT,
            metric_code TEXT,
            metric_name TEXT,
            metric_value REAL,
            unit TEXT,
            property_type TEXT,
            source_region_name TEXT,
            mapping_scope TEXT,
            mapping_method TEXT,
            mapping_confidence TEXT,
            source_id TEXT,
            provider TEXT,
            direct_value_allowed INTEGER DEFAULT 0,
            proxy_score_allowed INTEGER DEFAULT 0,
            engine_promotion_ready INTEGER DEFAULT 0,
            forbidden_claim_ko TEXT,
            provenance_note TEXT
        );

        CREATE TABLE rule_location_score (
            quarter TEXT,
            area_code TEXT,
            area_name TEXT,
            district_code TEXT,
            district_name TEXT,
            industry_code TEXT,
            industry_name TEXT,
            current_location_score REAL,
            context_location_score REAL,
            grade TEXT,
            decision_label TEXT,
            score_coverage_tier TEXT,
            available_axis_count INTEGER,
            official_indicator_count INTEGER,
            official_indicator_defined_count INTEGER,
            official_indicator_complete INTEGER DEFAULT 0,
            missing_axes TEXT,
            coverage_reason TEXT,
            taxonomy_direct_score_allowed INTEGER DEFAULT 0,
            official_rank_eligible INTEGER DEFAULT 0,
            cost_risk_score REAL,
            data_reliability_score REAL,
            conservative_score_owa REAL,
            axis_sales REAL,
            axis_competition REAL,
            axis_demand REAL,
            axis_accessibility REAL,
            growth_potential_score REAL,
            growth_rebound_candidate_score REAL,
            score_version TEXT,
            PRIMARY KEY (quarter, area_code, industry_code)
        );

        CREATE TABLE rule_area_score_summary (
            quarter TEXT,
            area_code TEXT,
            area_name TEXT,
            district_code TEXT,
            district_name TEXT,
            score REAL,
            score_mean REAL,
            score_median REAL,
            score_min REAL,
            score_max REAL,
            score_count INTEGER,
            top_industry_code TEXT,
            top_industry_name TEXT,
            top_industry_status TEXT,
            score_definition TEXT,
            score_version TEXT,
            PRIMARY KEY (quarter, area_code)
        );

        CREATE TABLE industry_hierarchy (
            industry_code TEXT PRIMARY KEY,
            industry_name TEXT,
            ui_major_code TEXT,
            ui_major_name TEXT,
            ui_middle_code TEXT,
            ui_middle_name TEXT,
            ui_detail_code TEXT,
            ui_detail_name TEXT,
            display_label TEXT,
            search_text TEXT,
            selection_path TEXT,
            final_algorithm_key TEXT,
            lookup_use_status TEXT,
            direct_score_allowed INTEGER DEFAULT 0,
            direct_score_blocker_ko TEXT,
            score_use_status TEXT
        );

        CREATE TABLE location_lookup (
            area_code TEXT PRIMARY KEY,
            area_name TEXT,
            district_code TEXT,
            district_name TEXT,
            latitude REAL,
            longitude REAL,
            display_label TEXT,
            search_text TEXT,
            input_resolution_method TEXT,
            lookup_use_status TEXT
        );
        """
    )
    conn.commit()


def create_app_tables_if_missing(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE,
            password_hash TEXT,
            nickname TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS favorite_area (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            area_code TEXT
        );
        CREATE TABLE IF NOT EXISTS saved_report (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            report_data TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS chatbot_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            area_name TEXT,
            business_type TEXT,
            budget INTEGER,
            result_data TEXT,
            created_at TEXT
        );
        """
    )
    conn.commit()


def insert_dataframe(conn: sqlite3.Connection, table: str, df: pd.DataFrame, chunksize: int = 20000) -> None:
    df.to_sql(table, conn, if_exists="append", index=False, chunksize=chunksize)


def _contract_bool_series(values: pd.Series) -> pd.Series:
    """CSV bool/int/string을 점수 계약용 0/1 Series로 정규화한다."""
    return values.map(
        lambda value: 1
        if value is not None
        and not pd.isna(value)
        and str(value).strip().lower() in {"true", "1", "yes", "y"}
        else 0
    ).astype(int)


def _area_context_frame(scored: pd.DataFrame) -> pd.DataFrame:
    """상권 grain 축의 업종행 복제가 동일한지 확인하고 공통 행으로 집계한다."""
    keys = ["quarter", "area_code"]
    axes = ["axis_demand", "axis_accessibility"]
    variation = scored.groupby(keys, dropna=False)[axes].nunique(dropna=False)
    inconsistent = variation.gt(1).any(axis=1)
    if inconsistent.any():
        examples = [f"{quarter}/{area}" for quarter, area in inconsistent[inconsistent].index[:5]]
        raise ValueError(
            "v2.6 score batch area-grain demand/accessibility axes differ across industry rows: "
            f"groups={int(inconsistent.sum())}, examples={examples}"
        )
    return scored.groupby(keys, as_index=False, dropna=False, sort=False).agg(
        area_name=("area_name", "first"),
        district_code=("district_code", "first"),
        district_name=("district_name", "first"),
        axis_demand=("axis_demand", "first"),
        axis_accessibility=("axis_accessibility", "first"),
    )


def seed_lookup_tables(conn: sqlite3.Connection) -> None:
    location = read_gold(
        "gold_location_input_lookup.csv",
        [
            "상권_코드",
            "상권_코드_명",
            "자치구_코드",
            "자치구_코드_명",
            "representative_lat_wgs84",
            "representative_lon_wgs84",
            "display_label",
            "location_search_text",
            "input_resolution_method",
            "lookup_use_status",
        ],
    )
    location_lookup = location.rename(
        columns={
            "상권_코드": "area_code",
            "상권_코드_명": "area_name",
            "자치구_코드": "district_code",
            "자치구_코드_명": "district_name",
            "representative_lat_wgs84": "latitude",
            "representative_lon_wgs84": "longitude",
            "location_search_text": "search_text",
        }
    )
    insert_dataframe(conn, "location_lookup", location_lookup)

    commercial_area = location_lookup[["area_code", "area_name", "district_code", "latitude", "longitude"]].copy()
    insert_dataframe(conn, "commercial_area", commercial_area)

    industry = read_gold(
        "gold_industry_selection_hierarchy.csv",
        [
            "서비스_업종_코드",
            "서비스_업종_코드_명",
            "UI_대분류코드",
            "UI_대분류명",
            "UI_중분류코드",
            "UI_중분류명",
            "UI_세부분류코드",
            "UI_세부분류명",
            "industry_display_label",
            "industry_search_text",
            "selection_path",
            "final_algorithm_key",
            "lookup_use_status",
        ],
    ).rename(
        columns={
            "서비스_업종_코드": "industry_code",
            "서비스_업종_코드_명": "industry_name",
            "UI_대분류코드": "ui_major_code",
            "UI_대분류명": "ui_major_name",
            "UI_중분류코드": "ui_middle_code",
            "UI_중분류명": "ui_middle_name",
            "UI_세부분류코드": "ui_detail_code",
            "UI_세부분류명": "ui_detail_name",
            "industry_display_label": "display_label",
            "industry_search_text": "search_text",
        }
    )
    taxonomy = read_gold(
        "gold_industry_taxonomy.csv",
        [
            "서비스_업종_코드",
            "direct_score_allowed",
            "direct_score_blocker_ko",
            "score_use_status",
        ],
    ).rename(columns={"서비스_업종_코드": "industry_code"})
    taxonomy["direct_score_allowed"] = (
        taxonomy["direct_score_allowed"].astype(str).str.lower().isin(["true", "1", "yes"])
    ).astype(int)
    industry = industry.merge(taxonomy, on="industry_code", how="left", validate="one_to_one")
    industry["direct_score_allowed"] = industry["direct_score_allowed"].fillna(0).astype(int)
    insert_dataframe(conn, "industry_hierarchy", industry)


def seed_axis_tables(conn: sqlite3.Connection) -> None:
    demand = read_gold(
        "gold_demand_q_area.csv",
        ["기준_년분기_코드", "상권_코드", "자치구_코드_명", "총_상주인구_수", "총_직장인구_수", "총_유동인구_수"],
    )
    population = demand.rename(
        columns={
            "상권_코드": "area_code",
            "자치구_코드_명": "district_name",
            "총_상주인구_수": "resident_population",
            "총_직장인구_수": "worker_population",
            "기준_년분기_코드": "timestamp",
        }
    )[["area_code", "district_name", "resident_population", "worker_population", "timestamp"]]
    population["resident_population"] = pd.to_numeric(population["resident_population"], errors="coerce").fillna(0).astype(int)
    population["worker_population"] = pd.to_numeric(population["worker_population"], errors="coerce").fillna(0).astype(int)
    insert_dataframe(conn, "district_population", population)

    floating = demand.rename(
        columns={
            "상권_코드": "area_code",
            "총_유동인구_수": "floating_population",
            "기준_년분기_코드": "timestamp",
        }
    )[["area_code", "floating_population", "timestamp"]]
    floating["floating_population"] = pd.to_numeric(floating["floating_population"], errors="coerce").fillna(0).astype(int)
    insert_dataframe(conn, "district_floating", floating)

    sales = read_gold(
        "gold_sales_strength_q_industry.csv",
        ["기준_년분기_코드", "상권_코드", "서비스_업종_코드", "서비스_업종_코드_명", "당월_매출_금액"],
    ).rename(
        columns={
            "상권_코드": "area_code",
            "서비스_업종_코드": "industry_code",
            "서비스_업종_코드_명": "industry_name",
            "당월_매출_금액": "sales_amount",
            "기준_년분기_코드": "timestamp",
        }
    )
    sales["sales_amount"] = pd.to_numeric(sales["sales_amount"], errors="coerce").fillna(0.0)
    insert_dataframe(conn, "district_sales", sales[["area_code", "industry_code", "industry_name", "sales_amount", "timestamp"]])

    competition = read_gold(
        "gold_competition_q_industry.csv",
        ["기준_년분기_코드", "상권_코드", "서비스_업종_코드", "서비스_업종_코드_명", "점포_수"],
    ).rename(
        columns={
            "상권_코드": "area_code",
            "서비스_업종_코드": "industry_code",
            "서비스_업종_코드_명": "industry_name",
            "점포_수": "store_count",
            "기준_년분기_코드": "timestamp",
        }
    )
    competition["store_count"] = pd.to_numeric(competition["store_count"], errors="coerce").fillna(0).astype(int)
    insert_dataframe(conn, "district_store_count", competition[["area_code", "industry_code", "industry_name", "store_count", "timestamp"]])

    sale_price_proxy = read_gold(
        "gold_cost_risk_q_area.csv",
        [
            "기준_년분기_코드",
            "상권_코드",
            "건물면적당_거래금액_중앙값_만원_per_m2",
            "source_id",
            "provider",
            "direct_score_allowed",
            "proxy_score_allowed",
            "proxy_reason_ko",
        ],
    ).rename(
        columns={
            "상권_코드": "area_code",
            "건물면적당_거래금액_중앙값_만원_per_m2": "sale_price_proxy_manwon_per_m2",
            "기준_년분기_코드": "period",
            "proxy_reason_ko": "provenance_note",
        }
    )
    sale_price_proxy["sale_price_proxy_manwon_per_m2"] = pd.to_numeric(
        sale_price_proxy["sale_price_proxy_manwon_per_m2"], errors="coerce"
    )
    sale_price_proxy["grain"] = "district_proxy_fanned_out_to_trade_area"
    for flag in ("direct_score_allowed", "proxy_score_allowed"):
        sale_price_proxy[flag] = (
            sale_price_proxy[flag].astype(str).str.lower().isin(["true", "1", "yes"])
        ).astype(int)
    insert_dataframe(
        conn,
        "area_sale_price_proxy",
        sale_price_proxy[
            [
                "area_code",
                "sale_price_proxy_manwon_per_m2",
                "period",
                "source_id",
                "provider",
                "grain",
                "direct_score_allowed",
                "proxy_score_allowed",
                "provenance_note",
            ]
        ],
    )

    if not RONE_COST_BRIDGE.exists():
        raise FileNotFoundError(f"R-ONE cost reference bridge is missing: {RONE_COST_BRIDGE}")
    rone = pd.read_csv(
        RONE_COST_BRIDGE,
        usecols=[
            "상권_코드",
            "mapping_scope",
            "mapping_method",
            "mapping_confidence",
            "selection_group",
            "상가유형",
            "기준_년분기_코드",
            "지역_전체명",
            "rone_terminal_region",
            "ITM_NM",
            "DTA_VAL",
            "UI_NM",
            "source_id",
            "direct_score_allowed",
            "proxy_score_allowed",
            "engine_promotion_ready",
            "forbidden_claim_ko",
            "mapping_use_note_ko",
        ],
        encoding="utf-8-sig",
        low_memory=False,
        dtype={"상권_코드": str, "기준_년분기_코드": str},
    )
    rone = rone[rone["selection_group"].isin(["최신 지역별 임대료", "최신 지역별 공실률"])].copy()
    rone["metric_code"] = rone["selection_group"].map(
        {"최신 지역별 임대료": "rent", "최신 지역별 공실률": "vacancy"}
    )
    rone["metric_name"] = rone["ITM_NM"]
    rone["metric_value"] = pd.to_numeric(rone["DTA_VAL"], errors="coerce")
    rone["period"] = rone["기준_년분기_코드"]
    rone["property_type"] = rone["상가유형"]
    rone["source_region_name"] = rone["지역_전체명"].fillna(rone["rone_terminal_region"])
    rone["unit"] = rone["UI_NM"]
    rone["provider"] = "한국부동산원 R-ONE"
    for source_flag, target_flag in (
        ("direct_score_allowed", "direct_value_allowed"),
        ("proxy_score_allowed", "proxy_score_allowed"),
        ("engine_promotion_ready", "engine_promotion_ready"),
    ):
        rone[target_flag] = (
            rone[source_flag].astype(str).str.lower().isin(["true", "1", "yes"])
        ).astype(int)
    # Loader eligibility does not authorize a product score. Until a separate
    # promotion review exists, publish the product-side proxy flag as disabled.
    rone["proxy_score_allowed"] = 0
    rone["provenance_note"] = rone["mapping_use_note_ko"]
    rone["_mapping_rank"] = rone["mapping_scope"].map(
        {"rone_level3_name_match_candidate": 0, "seoul_baseline_reference": 1}
    ).fillna(9)
    rone = rone.sort_values(
        ["상권_코드", "metric_code", "property_type", "period", "_mapping_rank", "source_region_name"],
        kind="stable",
    ).drop_duplicates(
        subset=[
            "상권_코드",
            "period",
            "selection_group",
            "metric_code",
            "property_type",
            "mapping_scope",
            "source_region_name",
            "ITM_NM",
            "DTA_VAL",
            "UI_NM",
            "source_id",
        ],
        keep="first",
    )
    rone = rone.rename(columns={"상권_코드": "area_code"})
    insert_dataframe(
        conn,
        "area_rone_cost_reference",
        rone[
            [
                "area_code",
                "period",
                "selection_group",
                "metric_code",
                "metric_name",
                "metric_value",
                "unit",
                "property_type",
                "source_region_name",
                "mapping_scope",
                "mapping_method",
                "mapping_confidence",
                "source_id",
                "provider",
                "direct_value_allowed",
                "proxy_score_allowed",
                "engine_promotion_ready",
                "forbidden_claim_ko",
                "provenance_note",
            ]
        ],
    )

    history_sales = sales.groupby(["timestamp", "area_code"], as_index=False)["sales_amount"].sum()
    history_float = floating.groupby(["timestamp", "area_code"], as_index=False)["floating_population"].sum()
    history_store = competition.groupby(["timestamp", "area_code"], as_index=False)["store_count"].sum()
    history = history_sales.merge(history_float, on=["timestamp", "area_code"], how="outer").merge(
        history_store, on=["timestamp", "area_code"], how="outer"
    )
    history["sales_amount"] = history["sales_amount"].fillna(0.0)
    history["floating_population"] = history["floating_population"].fillna(0).astype(int)
    history["store_count"] = history["store_count"].fillna(0).astype(int)
    insert_dataframe(conn, "district_growth_history", history[["area_code", "sales_amount", "floating_population", "store_count", "timestamp"]])


def seed_rule_scores(conn: sqlite3.Connection) -> None:
    score_batch = latest_score_batch()
    score = pd.read_csv(score_batch, encoding="utf-8-sig", low_memory=False, dtype={"상권_코드": str, "서비스_업종_코드": str, "기준_년분기_코드": str, "자치구_코드": str})
    renamed = score.rename(
        columns={
            "기준_년분기_코드": "quarter",
            "상권_코드": "area_code",
            "상권_코드_명": "area_name",
            "자치구_코드": "district_code",
            "자치구_코드_명": "district_name",
            "서비스_업종_코드": "industry_code",
            "서비스_업종_코드_명": "industry_name",
            "axis__sales": "axis_sales",
            "axis__competition": "axis_competition",
            "axis__demand": "axis_demand",
            "axis__accessibility": "axis_accessibility",
        }
    )
    required_builder_columns = {
        "current_location_score",
        "context_location_score",
        "grade",
        "decision_label",
        "score_coverage_tier",
        "available_axis_count",
        "official_indicator_count",
        "official_indicator_defined_count",
        "official_indicator_complete",
        "missing_axes",
        "coverage_reason",
        "taxonomy_direct_score_allowed",
        "official_rank_eligible",
        "data_reliability_score",
    }
    missing_builder_columns = sorted(required_builder_columns - set(renamed.columns))
    if missing_builder_columns:
        raise ValueError(
            "v2.6 score batch is missing coverage-contract columns: "
            f"{missing_builder_columns}"
        )
    builder_available_axis_count = pd.to_numeric(
        renamed["available_axis_count"], errors="coerce"
    )
    builder_taxonomy_allowed = _contract_bool_series(
        renamed["taxonomy_direct_score_allowed"]
    )
    builder_official_eligible = _contract_bool_series(
        renamed["official_rank_eligible"]
    )
    builder_indicator_complete = _contract_bool_series(
        renamed["official_indicator_complete"]
    )
    indicator_count = pd.to_numeric(
        renamed["official_indicator_count"], errors="coerce"
    )
    indicator_defined_count = pd.to_numeric(
        renamed["official_indicator_defined_count"], errors="coerce"
    )
    invalid_indicator_contract = (
        indicator_count.isna()
        | indicator_defined_count.isna()
        | indicator_defined_count.le(0)
        | indicator_count.lt(0)
        | indicator_count.gt(indicator_defined_count)
        | builder_indicator_complete.ne(indicator_count.eq(indicator_defined_count).astype(int))
    )
    if invalid_indicator_contract.any():
        raise ValueError(
            "v2.6 score batch official indicator completeness fields disagree for "
            f"{int(invalid_indicator_contract.sum())} rows"
        )
    renamed["official_indicator_count"] = indicator_count.astype(int)
    renamed["official_indicator_defined_count"] = indicator_defined_count.astype(int)
    renamed["official_indicator_complete"] = builder_indicator_complete
    taxonomy = pd.read_sql_query(
        "SELECT industry_code, direct_score_allowed, direct_score_blocker_ko FROM industry_hierarchy",
        conn,
    ).drop_duplicates(subset=["industry_code"])
    renamed = renamed.merge(
        taxonomy.rename(
            columns={
                "direct_score_allowed": "_taxonomy_direct_score_allowed",
                "direct_score_blocker_ko": "_taxonomy_direct_score_blocker_ko",
            }
        ),
        on="industry_code",
        how="left",
        validate="many_to_one",
    )
    axis_cols = ["axis_sales", "axis_competition", "axis_demand", "axis_accessibility"]
    derived_available_axis_count = renamed[axis_cols].notna().sum(axis=1)
    axis_count_mismatch = builder_available_axis_count.ne(derived_available_axis_count)
    if axis_count_mismatch.any():
        raise ValueError(
            "v2.6 score batch available_axis_count disagrees with four official axis columns for "
            f"{int(axis_count_mismatch.sum())} rows"
        )
    renamed["available_axis_count"] = derived_available_axis_count
    renamed["missing_axes"] = renamed[axis_cols].apply(
        lambda row: ",".join(column.removeprefix("axis_") for column, value in row.items() if pd.isna(value)),
        axis=1,
    )
    renamed["taxonomy_direct_score_allowed"] = (
        pd.to_numeric(renamed["_taxonomy_direct_score_allowed"], errors="coerce").fillna(0).astype(int)
    )
    taxonomy_mismatch = builder_taxonomy_allowed.ne(renamed["taxonomy_direct_score_allowed"])
    if taxonomy_mismatch.any():
        raise ValueError(
            "v2.6 score batch taxonomy_direct_score_allowed disagrees with product taxonomy for "
            f"{int(taxonomy_mismatch.sum())} rows"
        )
    reliability = pd.to_numeric(renamed["data_reliability_score"], errors="coerce")
    derived_official_eligible = (
        (renamed["available_axis_count"] == 4) & (renamed["taxonomy_direct_score_allowed"] == 1)
        & renamed["official_indicator_complete"].eq(1)
        & reliability.ge(RELIABILITY_GATE)
    ).astype(int)
    eligibility_mismatch = builder_official_eligible.ne(derived_official_eligible)
    if eligibility_mismatch.any():
        raise ValueError(
            "v2.6 score batch official_rank_eligible violates coverage/reliability contract for "
            f"{int(eligibility_mismatch.sum())} rows"
        )
    renamed["official_rank_eligible"] = builder_official_eligible

    official = renamed["official_rank_eligible"].eq(1)
    invalid_current_contract = (
        (official & renamed["current_location_score"].isna())
        | (~official & renamed["current_location_score"].notna())
    )
    if invalid_current_contract.any():
        raise ValueError(
            "v2.6 score batch current_location_score violates official eligibility contract for "
            f"{int(invalid_current_contract.sum())} rows"
        )
    context_available = renamed["context_location_score"].notna()
    invalid_context_contract = (
        ((renamed["available_axis_count"] < 3) & context_available)
        | ((renamed["available_axis_count"] >= 3) & ~context_available)
    )
    if invalid_context_contract.any():
        raise ValueError(
            "v2.6 score batch context_location_score violates the 3-axis fallback contract for "
            f"{int(invalid_context_contract.sum())} rows"
        )
    invalid_withheld_grade = ~official & renamed["grade"].notna()
    if invalid_withheld_grade.any():
        raise ValueError(
            "v2.6 score batch must null grade for non-official rows: "
            f"{int(invalid_withheld_grade.sum())} rows"
        )
    missing_builder_decision = official & (
        renamed["grade"].isna() | renamed["decision_label"].isna()
    )
    if missing_builder_decision.any():
        raise ValueError(
            "v2.6 score batch is missing builder grade/decision_label for "
            f"{int(missing_builder_decision.sum())} official rows"
        )
    missing_withheld_decision = ~official & renamed["decision_label"].isna()
    if missing_withheld_decision.any():
        raise ValueError(
            "v2.6 score batch is missing builder decision_label for non-official rows: "
            f"{int(missing_withheld_decision.sum())} rows"
        )
    # Builder의 공식/보류 판정과 문구를 그대로 게시한다. 시드에서 공식 적격을
    # 재계산해 저신뢰도 행을 다시 활성화하지 않는다.
    renamed["score_version"] = COVERAGE_SCORE_VERSION
    rule_cols = [
        "quarter",
        "area_code",
        "area_name",
        "district_code",
        "district_name",
        "industry_code",
        "industry_name",
        "current_location_score",
        "context_location_score",
        "grade",
        "decision_label",
        "score_coverage_tier",
        "available_axis_count",
        "official_indicator_count",
        "official_indicator_defined_count",
        "official_indicator_complete",
        "missing_axes",
        "coverage_reason",
        "taxonomy_direct_score_allowed",
        "official_rank_eligible",
        "cost_risk_score",
        "data_reliability_score",
        "conservative_score_owa",
        "axis_sales",
        "axis_competition",
        "axis_demand",
        "axis_accessibility",
        "growth_potential_score",
        "growth_rebound_candidate_score",
        "score_version",
    ]
    insert_dataframe(conn, "rule_location_score", renamed[rule_cols])
    conn.execute(
        """
        UPDATE rule_location_score
        SET industry_name = (
            SELECT industry_name
            FROM industry_hierarchy
            WHERE industry_hierarchy.industry_code = rule_location_score.industry_code
        )
        WHERE industry_name IS NULL OR industry_name = ''
        """
    )
    conn.execute(
        """
        UPDATE rule_location_score
        SET
            area_name = COALESCE(NULLIF(area_name, ''), (
                SELECT area_name
                FROM commercial_area
                WHERE commercial_area.area_code = rule_location_score.area_code
            )),
            district_code = COALESCE(NULLIF(district_code, ''), (
                SELECT district_code
                FROM commercial_area
                WHERE commercial_area.area_code = rule_location_score.area_code
            )),
            district_name = COALESCE(NULLIF(district_name, ''), (
                SELECT district_name
                FROM location_lookup
                WHERE location_lookup.area_code = rule_location_score.area_code
            ))
        WHERE area_name IS NULL OR area_name = ''
           OR district_code IS NULL OR district_code = ''
           OR district_name IS NULL OR district_name = ''
        """
    )

    # 수요·접근성 축은 상권 grain이며 업종 행에 동일 복제되어야 한다. 동일성 검증
    # 뒤 공통 집계 한 행을 만들므로 runtime의 MAX 집계와 seed summary가 갈라지지 않는다.
    summary = _area_context_frame(renamed)
    summary = summary.dropna(subset=["axis_demand", "axis_accessibility"]).copy()
    summary["score"] = summary[["axis_demand", "axis_accessibility"]].mean(axis=1, skipna=False)
    summary = summary[
        ["quarter", "area_code", "area_name", "district_code", "district_name", "score"]
    ].copy()
    summary["score_version"] = "area_context.demand_accessibility.v1"
    summary["score"] = summary["score"].round(2)
    summary["score_mean"] = summary["score"]
    summary["score_median"] = summary["score"]
    summary["score_min"] = summary["score"]
    summary["score_max"] = summary["score"]
    summary["score_count"] = 2
    summary["top_industry_code"] = None
    summary["top_industry_name"] = None
    summary["top_industry_status"] = "withheld_no_cross_industry_calibration"
    summary["score_definition"] = "area_context_demand_accessibility_mean_v1"
    insert_dataframe(
        conn,
        "rule_area_score_summary",
        summary[
            [
                "quarter",
                "area_code",
                "area_name",
                "district_code",
                "district_name",
                "score",
                "score_mean",
                "score_median",
                "score_min",
                "score_max",
                "score_count",
                "top_industry_code",
                "top_industry_name",
                "top_industry_status",
                "score_definition",
                "score_version",
            ]
        ],
    )
    conn.execute(
        """
        UPDATE rule_area_score_summary
        SET
            area_name = COALESCE(NULLIF(area_name, ''), (
                SELECT area_name
                FROM commercial_area
                WHERE commercial_area.area_code = rule_area_score_summary.area_code
            )),
            district_code = COALESCE(NULLIF(district_code, ''), (
                SELECT district_code
                FROM commercial_area
                WHERE commercial_area.area_code = rule_area_score_summary.area_code
            )),
            district_name = COALESCE(NULLIF(district_name, ''), (
                SELECT district_name
                FROM location_lookup
                WHERE location_lookup.area_code = rule_area_score_summary.area_code
            ))
        WHERE area_name IS NULL OR area_name = ''
           OR district_code IS NULL OR district_code = ''
           OR district_name IS NULL OR district_name = ''
        """
    )


def create_indexes(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS ix_commercial_area_name ON commercial_area(area_name);
        CREATE INDEX IF NOT EXISTS ix_district_population_area ON district_population(area_code);
        CREATE INDEX IF NOT EXISTS ix_district_floating_area ON district_floating(area_code);
        CREATE INDEX IF NOT EXISTS ix_district_sales_area_industry ON district_sales(area_code, industry_code, timestamp);
        CREATE INDEX IF NOT EXISTS ix_district_store_area_industry ON district_store_count(area_code, industry_code, timestamp);
        CREATE INDEX IF NOT EXISTS ix_district_growth_area ON district_growth_history(area_code, timestamp);
        CREATE INDEX IF NOT EXISTS ix_area_sale_price_proxy_area ON area_sale_price_proxy(area_code, period);
        CREATE INDEX IF NOT EXISTS ix_area_rone_cost_reference_area ON area_rone_cost_reference(area_code, period, metric_code);
        CREATE INDEX IF NOT EXISTS ix_rule_score_area_industry ON rule_location_score(area_code, industry_code);
        CREATE INDEX IF NOT EXISTS ix_rule_score_industry_score ON rule_location_score(industry_code, current_location_score DESC);
        CREATE INDEX IF NOT EXISTS ix_rule_area_score ON rule_area_score_summary(score DESC);
        CREATE INDEX IF NOT EXISTS ix_industry_hierarchy_name ON industry_hierarchy(industry_name);
        CREATE INDEX IF NOT EXISTS ix_location_lookup_name ON location_lookup(area_name);
        """
    )
    conn.commit()


def clear_generated_report_cache(conn: sqlite3.Connection) -> None:
    cache_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'ai_report_generation_cache'"
    ).fetchone()
    if not cache_exists:
        return
    deleted = conn.execute("SELECT COUNT(*) FROM ai_report_generation_cache").fetchone()[0]
    conn.execute("DELETE FROM ai_report_generation_cache")
    print(f"AI report generation cache cleared: {deleted:,}")


def backup_database(source_path: Path, target_path: Path) -> str:
    source_uri = f"{source_path.resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(source_uri, uri=True, timeout=30.0)) as source, closing(
        sqlite3.connect(target_path, timeout=30.0)
    ) as target:
        target.execute("PRAGMA busy_timeout=30000")
        source.backup(target)
        target.commit()
    with closing(sqlite3.connect(target_path, timeout=30.0)) as check_conn:
        quick_check = str(check_conn.execute("PRAGMA quick_check").fetchone()[0])
    if quick_check.lower() != "ok":
        raise RuntimeError(f"SQLite backup validation failed: {target_path} quick_check={quick_check}")
    return quick_check


def remove_database_files(path: Path) -> None:
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        candidate.unlink(missing_ok=True)


def publish_product_tables(staging_path: Path) -> tuple[dict[str, int], str]:
    create_table_name = re.compile(
        r'^\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:"[^"]+"|`[^`]+`|\[[^\]]+\]|\S+)',
        re.IGNORECASE,
    )
    create_index_name = re.compile(
        r'^\s*(CREATE\s+(?:UNIQUE\s+)?INDEX(?:\s+IF\s+NOT\s+EXISTS)?)\s+'
        r'(?:"[^"]+"|`[^`]+`|\[[^\]]+\]|\S+)(\s+ON\s+.*)$',
        re.IGNORECASE | re.DOTALL,
    )

    with closing(sqlite3.connect(DB_PATH, timeout=30.0)) as conn:
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA journal_mode=WAL")
        create_app_tables_if_missing(conn)
        conn.execute("ATTACH DATABASE ? AS staged", (str(staging_path),))
        try:
            conn.execute("BEGIN IMMEDIATE")
            for table in PRODUCT_TABLES_TO_DROP:
                conn.execute(f'DROP TABLE IF EXISTS main."{table}"')

            for table in PRODUCT_TABLES_TO_PUBLISH:
                schema_row = conn.execute(
                    "SELECT sql FROM staged.sqlite_master WHERE type = 'table' AND name = ?",
                    (table,),
                ).fetchone()
                if not schema_row or not schema_row[0]:
                    raise RuntimeError(f"Missing staging schema for {table}")
                create_sql = create_table_name.sub(
                    f'CREATE TABLE main."{table}"',
                    str(schema_row[0]),
                    count=1,
                )
                conn.execute(create_sql)

                columns = [
                    str(row[1])
                    for row in conn.execute(f'PRAGMA staged.table_info("{table}")').fetchall()
                ]
                if not columns:
                    raise RuntimeError(f"Missing staging columns for {table}")
                column_sql = ", ".join(f'"{column}"' for column in columns)
                conn.execute(
                    f'INSERT INTO main."{table}" ({column_sql}) '
                    f'SELECT {column_sql} FROM staged."{table}"'
                )

            product_placeholders = ",".join("?" for _ in PRODUCT_TABLES_TO_PUBLISH)
            indexes = conn.execute(
                f"SELECT name, sql FROM staged.sqlite_master "
                f"WHERE type = 'index' AND sql IS NOT NULL AND tbl_name IN ({product_placeholders})",
                PRODUCT_TABLES_TO_PUBLISH,
            ).fetchall()
            for index_name, index_sql in indexes:
                match = create_index_name.match(str(index_sql))
                if not match:
                    raise RuntimeError(f"Unsupported staging index schema: {index_sql}")
                conn.execute(f'{match.group(1)} main."{index_name}"{match.group(2)}')

            clear_generated_report_cache(conn)
            checks = {
                "commercial_area": conn.execute("SELECT COUNT(*) FROM commercial_area").fetchone()[0],
                "district_population": conn.execute("SELECT COUNT(*) FROM district_population").fetchone()[0],
                "district_floating": conn.execute("SELECT COUNT(*) FROM district_floating").fetchone()[0],
                "district_sales": conn.execute("SELECT COUNT(*) FROM district_sales").fetchone()[0],
                "district_store_count": conn.execute("SELECT COUNT(*) FROM district_store_count").fetchone()[0],
                "area_sale_price_proxy": conn.execute("SELECT COUNT(*) FROM area_sale_price_proxy").fetchone()[0],
                "area_rone_cost_reference": conn.execute("SELECT COUNT(*) FROM area_rone_cost_reference").fetchone()[0],
                "rule_location_score": conn.execute("SELECT COUNT(*) FROM rule_location_score").fetchone()[0],
                "rule_area_score_summary": conn.execute("SELECT COUNT(*) FROM rule_area_score_summary").fetchone()[0],
                "industry_hierarchy": conn.execute("SELECT COUNT(*) FROM industry_hierarchy").fetchone()[0],
                "location_lookup": conn.execute("SELECT COUNT(*) FROM location_lookup").fetchone()[0],
            }
            empty_tables = [table for table, count in checks.items() if count <= 0]
            quick_check = str(conn.execute("PRAGMA quick_check").fetchone()[0])
            if empty_tables or quick_check.lower() != "ok":
                raise RuntimeError(
                    f"Product DB publish validation failed: empty_tables={empty_tables}, quick_check={quick_check}"
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.execute("DETACH DATABASE staged")
    return checks, quick_check


def main() -> None:
    if not DATACORPUS_ROOT.exists():
        raise FileNotFoundError(f"Missing canonical datacorpus: {DATACORPUS_ROOT}")
    DATABASE_BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    run_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = DATABASE_BACKUP_ROOT / f"commercial.before_rule_gold_{run_stamp}.db"
    staging = DATABASE_BACKUP_ROOT / f"commercial.rule_gold_staging_{run_stamp}.db"
    if DB_PATH.exists():
        backup_database(DB_PATH, backup)
        print(f"Backup created: {backup}")
    remove_database_files(staging)
    sqlite3.connect(staging).close()
    atexit.register(remove_database_files, staging)

    with closing(sqlite3.connect(staging, timeout=30.0)) as conn:
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA journal_mode=WAL")
        replace_tables(conn)
        seed_lookup_tables(conn)
        seed_axis_tables(conn)
        seed_rule_scores(conn)
        create_indexes(conn)

        checks = {
            "commercial_area": conn.execute("SELECT COUNT(*) FROM commercial_area").fetchone()[0],
            "district_population": conn.execute("SELECT COUNT(*) FROM district_population").fetchone()[0],
            "district_floating": conn.execute("SELECT COUNT(*) FROM district_floating").fetchone()[0],
            "district_sales": conn.execute("SELECT COUNT(*) FROM district_sales").fetchone()[0],
            "district_store_count": conn.execute("SELECT COUNT(*) FROM district_store_count").fetchone()[0],
            "area_sale_price_proxy": conn.execute("SELECT COUNT(*) FROM area_sale_price_proxy").fetchone()[0],
            "area_rone_cost_reference": conn.execute("SELECT COUNT(*) FROM area_rone_cost_reference").fetchone()[0],
            "rule_location_score": conn.execute("SELECT COUNT(*) FROM rule_location_score").fetchone()[0],
            "rule_area_score_summary": conn.execute("SELECT COUNT(*) FROM rule_area_score_summary").fetchone()[0],
            "industry_hierarchy": conn.execute("SELECT COUNT(*) FROM industry_hierarchy").fetchone()[0],
            "location_lookup": conn.execute("SELECT COUNT(*) FROM location_lookup").fetchone()[0],
        }
        empty_tables = [table for table, count in checks.items() if count <= 0]
        quick_check = str(conn.execute("PRAGMA quick_check").fetchone()[0])
        if empty_tables or quick_check.lower() != "ok":
            raise RuntimeError(
                f"제품 DB 반영 검증 실패: empty_tables={empty_tables}, quick_check={quick_check}"
            )
    checks, quick_check = publish_product_tables(staging)
    print(f"Validated product tables published: {DB_PATH}")
    for table, count in checks.items():
        print(f"{table}: {count:,}")
    print(f"quick_check: {quick_check}")
    remove_database_files(staging)
    atexit.unregister(remove_database_files)


if __name__ == "__main__":
    main()
