from __future__ import annotations

import ast
import csv
import hashlib
import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.settings import DATA_ROOT, DATABASE_PATH, KEY_FILE, RUNTIME_ROOT, WORKSPACE_ROOT


ADMIN_ROOT = RUNTIME_ROOT / "admin"
JOB_DB_PATH = ADMIN_ROOT / "pipeline_jobs.db"
JOB_LOG_ROOT = ADMIN_ROOT / "logs"
CORE_SOURCE_FRESHNESS_REPORT_PATH = ADMIN_ROOT / "core_source_freshness_report.json"
SOURCE_REGISTRY_PATH = DATA_ROOT / "_raw_ingest" / "source_registry.csv"
INGEST_MANIFEST_PATH = DATA_ROOT / "_raw_ingest" / "ingest_manifest.csv"
FAILED_DOWNLOADS_PATH = DATA_ROOT / "_raw_ingest" / "failed_downloads.csv"
SOURCE_STATE_PATH = DATA_ROOT / "_raw_ingest" / "source_state_catalog.json"
EXECUTION_CONTRACT_PATH = DATA_ROOT / "_rule_validation" / "75_preprocessing_file_execution_contract.csv"
GOLD_MANIFEST_PATH = DATA_ROOT / "_gold_validation" / "23_gold_output_manifest.csv"
NEWS_EVIDENCE_PATH = DATA_ROOT / "_silver" / "silver_news_evidence.csv"
PRODUCT_SCORE_VALIDATION_ROOT = DATA_ROOT / "_score_predictive_validation"
# Optional test/runtime override.  Production resolves the newest completed
# grounding summary under PRODUCT_SCORE_VALIDATION_ROOT.
PRODUCT_SCORE_GROUNDING_SUMMARY_PATH: Path | None = None
MARKET_SCORE_VALIDATION_SUMMARY_PATH = (
    DATA_ROOT
    / "_score_predictive_validation"
    / "v2_6_20260716"
    / "validation_summary.json"
)
BUSINESS_SURVIVAL_VALIDATION_SUMMARY_PATH = (
    DATA_ROOT
    / "_score_predictive_validation"
    / "business_survival_v1_20260717"
    / "validation_summary.json"
)
WORKER_SCRIPT_PATH = WORKSPACE_ROOT / "final_proj" / "backend" / "scripts" / "admin_pipeline_worker.py"
WORKER_ENV_FLAG = "LOCALFIT_ADMIN_WORKER"
LOCATION_SCORE_VERSION = "loc_score.v2.6-coverage-contract-rc1"
AREA_SCORE_VERSION = "area_context.demand_accessibility.v1"
GOLD_VERSION = "rule_gold.v1.0-20260704"
DEFAULT_CORE_FULL_COLLECTION_TTL_HOURS = 24.0
DEFAULT_CORE_SOURCE_HEALTH_TTL_HOURS = 168.0

ACTIVE_STATUSES = ("queued", "running", "cancelling")
NEWS_SOURCE_IDS = {
    "naver_api_hub_news",
    "seoul_city_press_rss",
    "seoul_district_official_rss",
    "molit_press_rss",
    "mss_press_rss",
    "semas_press_board",
    "korea_policy_briefing",
}
ADMIN_HIDDEN_JOB_KEYS = {"news_all", "news_naver", "news_official"}
SOURCE_SERVICE_MAP: dict[str, tuple[str, ...]] = {
    "seoul_trade_area_boundary": ("TbgisTrdarRelm",),
    "seoul_floating_population_trade_area": ("VwsmTrdarFlpopQq",),
    "seoul_resident_worker_population_trade_area": ("VwsmTrdarRepopQq", "VwsmTrdarWrcPopltnQq"),
    "seoul_trade_area_change_index": ("VwsmTrdarIxQq",),
    "seoul_facility_trade_area": ("VwsmTrdarFcltyQq",),
    "seoul_store_trade_area": ("VwsmTrdarStorQq",),
    "seoul_sales_trade_area": ("VwsmTrdarSelngQq",),
    "seoul_localdata_general_restaurant_license": ("LOCALDATA_072404",),
    "seoul_localdata_rest_cafe_license": ("LOCALDATA_072405",),
    "seoul_localdata_beauty_license": ("LOCALDATA_051801",),
    "seoul_localdata_barber_license": ("LOCALDATA_051901",),
    "seoul_localdata_laundry_license": ("LOCALDATA_062001",),
    "seoul_localdata_public_bath_license": ("LOCALDATA_114401",),
    "seoul_localdata_lodging_license": ("LOCALDATA_031101",),
    "seoul_localdata_singing_practice_license": ("LOCALDATA_030901",),
    "seoul_localdata_domestic_travel_license": ("LOCALDATA_031201",),
    "seoul_localdata_overseas_travel_license": ("LOCALDATA_031202",),
    "seoul_localdata_general_travel_license": ("LOCALDATA_031203",),
    "seoul_localdata_golf_practice_license": ("LOCALDATA_103101",),
    "seoul_localdata_billiards_license": ("LOCALDATA_103201",),
    "seoul_localdata_sports_dojo_license": ("LOCALDATA_104101",),
    "seoul_localdata_fitness_license": ("LOCALDATA_104201",),
}

CORE_PRODUCT_SOURCE_IDS = (
    "seoul_trade_area_boundary",
    "seoul_floating_population_trade_area",
    "seoul_resident_worker_population_trade_area",
    "seoul_trade_area_change_index",
    "seoul_facility_trade_area",
    "seoul_store_trade_area",
    "seoul_sales_trade_area",
)

LEASE_BENCHMARK_SOURCE_IDS = (
    "mdis_commercial_lease_tenant",
    "mdis_commercial_lease_landlord",
    "seoul_commercial_lease_survey",
    "reb_small_shop_rent",
)

LOCALDATA_BUSINESS_SOURCE_IDS = (
    "seoul_localdata_general_restaurant_license",
    "seoul_localdata_rest_cafe_license",
    "seoul_localdata_beauty_license",
    "seoul_localdata_barber_license",
    "seoul_localdata_laundry_license",
    "seoul_localdata_public_bath_license",
    "seoul_localdata_lodging_license",
    "seoul_localdata_singing_practice_license",
    "seoul_localdata_domestic_travel_license",
    "seoul_localdata_overseas_travel_license",
    "seoul_localdata_general_travel_license",
    "seoul_localdata_golf_practice_license",
    "seoul_localdata_billiards_license",
    "seoul_localdata_sports_dojo_license",
    "seoul_localdata_fitness_license",
)

# These sources are consumed by the product or by a product-visible candidate,
# but their collector buttons do not run the downstream Silver -> Gold -> score
# -> DB chain.  Keep that distinction machine-readable so the dashboard cannot
# present a successful raw API call as a refreshed product value.
PRODUCT_EXTERNAL_SOURCE_CONTRACTS: dict[str, dict[str, Any]] = {
    "molit_rtms_commercial_trade": {
        "product_role": "상업용 매매가 비용 프록시",
        "artifacts": (
            "_silver/silver_rtms_commercial_trade_sgg_quarter.csv",
        ),
        "validation_artifacts": (
            "_rule_validation/12_real_estate_cost_proxy_preprocess_summary.json",
        ),
        "period_artifact": "_silver/silver_rtms_commercial_trade_sgg_quarter.csv",
        "period_column": "기준_년분기_코드",
        "refresh_note": "RTMS 버튼은 원천 XML만 수집합니다. 비용 Silver·Gold·점수·DB 반영은 별도 제품 게시가 필요합니다.",
    },
    "reb_small_shop_rent": {
        "product_role": "서울 임대료·공실률 최신 기준선",
        "artifacts": (
            "_gold/gold_seoul_lease_benchmark.csv",
        ),
        "validation_artifacts": (
            "_rule_validation/104_seoul_lease_benchmark_validation.json",
        ),
        "period_artifact": "_gold/gold_seoul_lease_benchmark.csv",
        "period_column": "period",
        "database_table": "seoul_lease_benchmark",
        "database_source_value": "reb_small_shop_rent",
        "release_artifact": "_gold/gold_seoul_lease_benchmark.csv",
        "release_column": "release_id",
        "database_required_columns": (
            "release_id",
            "source_id",
            "period",
            "metric_code",
            "metric_value",
            "unit",
            "geography",
            "direct_score_allowed",
            "generated_at_utc",
        ),
        "engine_role": "reference_benchmark",
        "refresh_job_key": "reb_rent",
        "included_in_product_refresh": True,
        "refresh_note": "R-ONE 서울 집계부터 기준선 Gold와 제품 DB 게시까지 검증했습니다. 상권별 점수에는 직접 사용하지 않습니다.",
    },
    "mdis_commercial_lease_tenant": {
        "product_role": "서울 임차 점포 임대비용·매출 표본 기준선",
        "artifacts": (
            "_silver/silver_mdis_seoul_tenant_lease_2023.csv",
            "_gold/gold_seoul_lease_benchmark.csv",
        ),
        "validation_artifacts": (
            "_rule_validation/104_seoul_lease_benchmark_validation.json",
        ),
        "period_hint": "2023",
        "database_table": "seoul_lease_benchmark",
        "database_source_value": "mdis_commercial_lease_tenant",
        "release_artifact": "_gold/gold_seoul_lease_benchmark.csv",
        "release_column": "release_id",
        "database_required_columns": (
            "release_id",
            "source_id",
            "period",
            "metric_code",
            "metric_value",
            "unit",
            "geography",
            "direct_score_allowed",
            "generated_at_utc",
        ),
        "engine_role": "reference_benchmark",
        "refresh_job_key": "reb_rent",
        "included_in_product_refresh": True,
        "refresh_note": "2023년 설계서의 서울특별시 코드로 필터링한 임차인 표본과 기준선 DB를 검증했습니다.",
    },
    "mdis_commercial_lease_landlord": {
        "product_role": "서울 상가 임대인 표본 기준선",
        "artifacts": (
            "_silver/silver_mdis_seoul_landlord_lease_2023.csv",
            "_gold/gold_seoul_lease_benchmark.csv",
        ),
        "validation_artifacts": (
            "_rule_validation/104_seoul_lease_benchmark_validation.json",
        ),
        "period_hint": "2023",
        "database_table": "seoul_lease_benchmark",
        "database_source_value": "mdis_commercial_lease_landlord",
        "release_artifact": "_gold/gold_seoul_lease_benchmark.csv",
        "release_column": "release_id",
        "database_required_columns": (
            "release_id",
            "source_id",
            "period",
            "metric_code",
            "metric_value",
            "unit",
            "geography",
            "direct_score_allowed",
            "generated_at_utc",
        ),
        "engine_role": "reference_benchmark",
        "refresh_job_key": "reb_rent",
        "included_in_product_refresh": True,
        "refresh_note": "2023년 설계서의 서울특별시 코드로 필터링한 임대인 표본과 기준선 DB를 검증했습니다.",
    },
    "seoul_commercial_lease_survey": {
        "product_role": "서울시 상가임대차 공식 보고서 감사 기준",
        "artifacts": (
            "_gold/gold_seoul_lease_benchmark.csv",
        ),
        "validation_artifacts": (
            "_rule_validation/104_seoul_lease_benchmark_validation.json",
        ),
        "period_hint": "2023",
        "database_table": "seoul_lease_benchmark",
        "database_source_value": "seoul_commercial_lease_survey",
        "release_artifact": "_gold/gold_seoul_lease_benchmark.csv",
        "release_column": "release_id",
        "database_required_columns": (
            "release_id",
            "source_id",
            "period",
            "metric_code",
            "metric_value",
            "unit",
            "geography",
            "direct_score_allowed",
            "generated_at_utc",
        ),
        "engine_role": "audit_reference",
        "refresh_job_key": "reb_rent",
        "included_in_product_refresh": True,
        "refresh_note": "서울시 2023년 공식 보고서의 페이지별 공표값을 감사 기준으로 연결했습니다.",
    },
    "sbdc_store_info": {
        "product_role": "SBDC 점포 경쟁 보조 프록시",
        "artifacts": (
            "_silver/silver_sbdc_store_poi_seoul_202603.csv",
            "_silver/silver_sbdc_store_competition_trade_area_seoul_service_202603.csv",
        ),
        "validation_artifacts": (
            "_rule_validation/13_sbdc_store_info_consistency_validation.csv",
        ),
        "period_hint": "202603",
        "refresh_note": "SBDC 버튼은 반경 API 연결 표본입니다. 제품은 202603 전체 파일 스냅샷을 사용하며 표본 호출로 갱신되지 않습니다.",
    },
    "seoul_living_migration": {
        "product_role": "자치구 생활이동 수요·접근성 프록시",
        "artifacts": (
            "_silver/silver_living_migration_district_quarter_features.csv",
        ),
        "validation_artifacts": (
            "_rule_validation/11_living_migration_preprocess_summary.json",
        ),
        "period_artifact": "_silver/silver_living_migration_district_quarter_features.csv",
        "period_column": "기준_년분기_코드",
        "refresh_note": "생활이동은 현재 별도 정적 전처리 스냅샷입니다. 제품 핵심 갱신에 새 원천 수집이 포함되지 않습니다.",
    },
    "seoul_bus_stop_location_file": {
        "product_role": "공간 검색용 버스정류장 기준점",
        "artifacts": ("_silver/silver_bus_stop_location_master.csv",),
        "validation_artifacts": (
            "_rule_validation/07_bus_stop_location_preprocess_summary.json",
        ),
        "refresh_note": "교통 기준정보 버튼은 raw 수집만 수행합니다. Silver와 공간 인덱스는 별도 재게시해야 합니다.",
    },
    "seoul_subway_station_master": {
        "product_role": "공간 검색용 지하철역 기준점",
        "artifacts": ("_silver/silver_subway_station_master.csv",),
        "validation_artifacts": (
            "_rule_validation/08_subway_station_master_preprocess_summary.json",
        ),
        "refresh_note": "교통 기준정보 버튼은 raw 수집만 수행합니다. Silver와 공간 인덱스는 별도 재게시해야 합니다.",
    },
    "seoul_bus_route_node_master": {
        "product_role": "교통 접근성 후보 기준정보",
        "artifacts": ("_silver/silver_bus_route_node_master.csv",),
        "validation_artifacts": (),
        "refresh_note": "교통 기준정보 버튼은 raw 수집만 수행하며 접근성 후보 산출물은 자동 재생성하지 않습니다.",
    },
    "seoul_bus_stop_passengers_hourly": {
        "product_role": "교통 접근성 병렬 후보",
        "artifacts": (
            "_rule_validation/59_transit_accessibility_candidate_quarter_features.csv",
        ),
        "validation_artifacts": (
            "_rule_validation/59_transit_accessibility_candidate_backtest_summary.json",
        ),
        "period_artifact": "_rule_validation/59_transit_accessibility_candidate_quarter_features.csv",
        "period_column": "기준_년분기_코드",
        "refresh_note": "최근 승하차 버튼은 raw 월자료만 수집합니다. Silver·후보 백테스트·점수 배치는 자동 갱신되지 않습니다.",
    },
    "seoul_subway_station_passengers_hourly": {
        "product_role": "교통 접근성 병렬 후보",
        "artifacts": (
            "_rule_validation/59_transit_accessibility_candidate_quarter_features.csv",
        ),
        "validation_artifacts": (
            "_rule_validation/59_transit_accessibility_candidate_backtest_summary.json",
        ),
        "period_artifact": "_rule_validation/59_transit_accessibility_candidate_quarter_features.csv",
        "period_column": "기준_년분기_코드",
        "refresh_note": "최근 승하차 버튼은 raw 월자료만 수집합니다. Silver·후보 백테스트·점수 배치는 자동 갱신되지 않습니다.",
    },
}

for _source_id in LOCALDATA_BUSINESS_SOURCE_IDS:
    PRODUCT_EXTERNAL_SOURCE_CONTRACTS[_source_id] = {
        "product_role": "개별 사업체 365일 생존 성과 검증 원천",
        "artifacts": (
            "_silver/silver_localdata_business_license.csv",
        ),
        "validation_artifacts": (
            "_rule_validation/105_localdata_business_license_validation.json",
            "_score_predictive_validation/business_survival_v1_20260717/validation_summary.json",
        ),
        "period_artifact": "_silver/silver_localdata_business_license.csv",
        "period_column": "snapshot_date",
        "engine_role": "validation_only_not_direct_score",
        "refresh_job_key": "localdata_business_survival",
        "included_in_product_refresh": False,
        "reviewed_external_chain": True,
        "refresh_note": (
            "인허가 원천부터 공통 Silver와 365일 생존 백테스트까지 연결했습니다. "
            "현재 입지점수를 개별 점포의 생존확률로 해석하거나 직접 가점하는 것은 금지합니다."
        ),
    }


@dataclass(frozen=True)
class JobStep:
    label: str
    args: tuple[str, ...]


@dataclass(frozen=True)
class JobDefinition:
    key: str
    label: str
    description: str
    group: str
    steps: tuple[JobStep, ...]
    estimate: str
    source_ids: tuple[str, ...] = ()
    risk: str = "normal"
    requires_confirmation: bool = False
    timeout_seconds: int = 3600


class JobConflictError(RuntimeError):
    def __init__(self, active_job: dict[str, Any]):
        super().__init__("다른 데이터 작업이 실행 중입니다.")
        self.active_job = active_job


class JobCancelledError(RuntimeError):
    pass


def _step(label: str, *args: str) -> JobStep:
    return JobStep(label=label, args=tuple(args))


# Admin API requests can select only these reviewed commands; arbitrary shell input is never accepted.
JOB_DEFINITIONS: dict[str, JobDefinition] = {
    item.key: item
    for item in (
        JobDefinition(
            key="status_check",
            label="데이터 최신 상태 점검",
            description="수집 없이 핵심 7원천의 연결·최신 분기·표본 변경과 제품 DB 반영 상태를 빠르게 확인합니다.",
            group="system",
            steps=(_step("핵심 7원천 연결·최신성 표본 점검", "scripts/check_core_source_freshness.py"),),
            estimate="약 30초~1분",
            source_ids=CORE_PRODUCT_SOURCE_IDS,
            timeout_seconds=180,
        ),
        JobDefinition(
            key="news_all",
            label="뉴스 전체 갱신",
            description="네이버 뉴스와 서울시·자치구·국가기관 공식 자료를 수집하고 필터링합니다.",
            group="collection",
            steps=(_step("뉴스 전체 수집", "scripts/ingest_news_evidence.py", "--source", "all"),),
            estimate="1~3분",
            source_ids=tuple(sorted(NEWS_SOURCE_IDS)),
            timeout_seconds=900,
        ),
        JobDefinition(
            key="news_naver",
            label="네이버 뉴스 갱신",
            description="NAVER API HUB 검색 결과를 수집하고 광고·중복·저품질 문서를 제거합니다.",
            group="collection",
            steps=(
                _step(
                    "네이버 뉴스 수집",
                    "scripts/ingest_news_evidence.py",
                    "--source",
                    "naver",
                    "--naver-display",
                    "20",
                ),
            ),
            estimate="1분 이내",
            source_ids=("naver_api_hub_news",),
            timeout_seconds=600,
        ),
        JobDefinition(
            key="news_official",
            label="공식 뉴스 갱신",
            description="서울시·25개 자치구와 국가기관의 공식 RSS·보도자료를 갱신합니다.",
            group="collection",
            steps=(
                _step(
                    "공식 뉴스 수집",
                    "scripts/ingest_news_evidence.py",
                    "--source",
                    "seoul",
                    "--source",
                    "government",
                ),
            ),
            estimate="1~2분",
            source_ids=tuple(sorted(NEWS_SOURCE_IDS - {"naver_api_hub_news"})),
            timeout_seconds=900,
        ),
        JobDefinition(
            key="seoul_core",
            label="서울 상권 핵심 원천 갱신",
            description="상권 경계·인구·집객시설·변화지표 등 핵심 서울 OpenAPI 원천을 갱신합니다.",
            group="collection",
            steps=(_step("서울 핵심 원천 수집", "scripts/ingest_seoul_core_p0_full.py"),),
            estimate="수십 분",
            source_ids=(
                "seoul_trade_area_boundary",
                "seoul_floating_population_trade_area",
                "seoul_resident_worker_population_trade_area",
                "seoul_trade_area_change_index",
                "seoul_facility_trade_area",
            ),
            risk="caution",
            requires_confirmation=True,
            timeout_seconds=7200,
        ),
        JobDefinition(
            key="seoul_sales",
            label="서울 추정매출 갱신",
            description="서울 상권분석서비스 추정매출 전체 페이지를 수집합니다.",
            group="collection",
            steps=(_step("추정매출 전체 수집", "scripts/ingest_seoul_sales_trade_area_full.py"),),
            estimate="수십 분",
            source_ids=("seoul_sales_trade_area",),
            risk="caution",
            requires_confirmation=True,
            timeout_seconds=7200,
        ),
        JobDefinition(
            key="seoul_store",
            label="서울 점포 원천 갱신",
            description="대용량 점포-상권 원천을 전체 수집합니다. API 호출과 저장 시간이 큽니다.",
            group="collection",
            steps=(
                _step("점포-상권 전체 수집", "scripts/ingest_seoul_core_p0_full.py", "--store-only"),
            ),
            estimate="1시간 이상",
            source_ids=("seoul_store_trade_area",),
            risk="high",
            requires_confirmation=True,
            timeout_seconds=14400,
        ),
        JobDefinition(
            key="real_estate_localdata",
            label="중개업소·인허가 갱신",
            description="서울시 중개업소와 음식점·휴게음식점 인허가 원천을 갱신합니다.",
            group="collection",
            steps=(
                _step(
                    "부동산·LocalData 수집",
                    "scripts/ingest_seoul_real_estate_and_localdata_license.py",
                ),
            ),
            estimate="수십 분",
            source_ids=(
                "seoul_real_estate_broker_office",
                "seoul_localdata_general_restaurant_license",
                "seoul_localdata_rest_cafe_license",
            ),
            risk="caution",
            requires_confirmation=True,
            timeout_seconds=7200,
        ),
        JobDefinition(
            key="localdata_business_survival",
            label="다업종 인허가·생존 백테스트 갱신",
            description=(
                "현재 호출 가능한 서울시 개별 인허가 원천을 전체 수집하고 공통 Silver로 정규화한 뒤, "
                "개업 시점 이전의 입지점수로 365일 생존 성과를 시간분리 검증합니다. "
                "결과는 점수 검증용이며 개별 점포의 성공확률로 사용하지 않습니다."
            ),
            group="pipeline",
            steps=(
                _step(
                    "서울 다업종 인허가 전체 수집",
                    "scripts/ingest_seoul_localdata_business_licenses.py",
                    "--refresh",
                ),
                _step(
                    "다업종 인허가 공통 Silver",
                    "scripts/preprocess_rule_engine_localdata_business_license.py",
                ),
                _step(
                    "365일 생존 성과 백테스트",
                    "scripts/backtest_localdata_business_survival.py",
                ),
            ),
            estimate="수십 분",
            source_ids=LOCALDATA_BUSINESS_SOURCE_IDS,
            risk="caution",
            requires_confirmation=True,
            timeout_seconds=14400,
        ),
        JobDefinition(
            key="rtms",
            label="RTMS 원천 수집",
            description=(
                "국토교통부 상업·업무용 부동산 매매 실거래 원천 XML을 수집합니다. "
                "이 작업만으로 제품 비용 프록시·점수·DB는 갱신되지 않습니다."
            ),
            group="collection",
            steps=(_step("RTMS 실거래 수집", "scripts/ingest_rtms_commercial_raw.py"),),
            estimate="수 분",
            source_ids=("molit_rtms_commercial_trade",),
            timeout_seconds=1800,
        ),
        JobDefinition(
            key="reb_rent",
            label="서울 임대비용 기준 갱신",
            description=(
                "한국부동산원 R-ONE 서울 통계를 새로 수집하고, 2023년 MDIS 임차인·임대인 자료와 "
                "서울시 공식 보고서를 결합해 서울 임대비용 기준선과 제품 DB를 함께 갱신합니다. "
                "이 기준선은 서울 전체 참고값이며 상권별 점수에 직접 사용하지 않습니다."
            ),
            group="pipeline",
            steps=(
                _step("R-ONE 서울 임대동향 수집", "scripts/ingest_reb_rone_commercial_rent_raw.py"),
                _step("서울 임대비용 기준선·DB 생성", "scripts/build_seoul_lease_benchmarks.py"),
            ),
            estimate="2~5분",
            source_ids=LEASE_BENCHMARK_SOURCE_IDS,
            risk="caution",
            requires_confirmation=True,
            timeout_seconds=1800,
        ),
        JobDefinition(
            key="sbdc_store",
            label="SBDC API 표본 점검",
            description=(
                "소상공인시장진흥공단 점포 API 반경 표본을 수집해 연결 상태를 점검합니다. "
                "제품의 전체 점포 스냅샷을 갱신하는 작업은 아닙니다."
            ),
            group="collection",
            steps=(_step("SBDC 점포 표본 수집", "scripts/ingest_sbdc_store_api_samples.py"),),
            estimate="수 분",
            source_ids=("sbdc_store_info",),
            timeout_seconds=1800,
        ),
        JobDefinition(
            key="sgis_stats",
            label="SGIS 통계 갱신",
            description="SGIS 서울 통계와 공간코드 원천을 갱신합니다.",
            group="collection",
            steps=(
                _step("SGIS 공간코드 수집", "scripts/ingest_sgis_spatial_codes_and_boundaries.py"),
                _step("SGIS 서울 통계 수집", "scripts/ingest_sgis_census_stats_seoul.py"),
            ),
            estimate="수 분",
            source_ids=("sgis_small_area_stats",),
            timeout_seconds=2400,
        ),
        JobDefinition(
            key="kosis_stats",
            label="KOSIS 통계 갱신",
            description="선정된 KOSIS 통계표의 메타데이터와 값을 갱신합니다.",
            group="collection",
            steps=(
                _step("KOSIS 메타데이터 수집", "scripts/ingest_kosis_selected_metadata.py"),
                _step("KOSIS 통계값 수집", "scripts/ingest_kosis_selected_data.py"),
            ),
            estimate="수 분",
            source_ids=("kosis_population_business_survival",),
            timeout_seconds=2400,
        ),
        JobDefinition(
            key="transport_master",
            label="교통 기준 원천 수집",
            description=(
                "버스정류장·노선·지하철역 raw 기준정보를 수집합니다. "
                "Silver와 제품 공간 인덱스는 별도 재게시해야 합니다."
            ),
            group="collection",
            steps=(
                _step(
                    "교통 접근성 원천 수집",
                    "scripts/ingest_seoul_transport_accessibility_sources.py",
                ),
            ),
            estimate="수 분",
            source_ids=(
                "seoul_bus_stop_location_file",
                "seoul_subway_station_master",
                "seoul_bus_route_node_master",
            ),
            timeout_seconds=2400,
        ),
        JobDefinition(
            key="transit_latest",
            label="최근 교통 승하차 원천 수집",
            description=(
                "현재 시점에서 안정적으로 제공되는 최근 월의 버스·지하철 승하차 raw를 수집합니다. "
                "Silver·접근성 후보·점수 배치는 자동 갱신되지 않습니다."
            ),
            group="collection",
            steps=(
                _step(
                    "최근 교통 승하차 수집",
                    "scripts/ingest_seoul_transit_passenger_history.py",
                    "--months",
                    "{latest_stable_month}",
                    "--services",
                    "bus,subway",
                    "--execute",
                ),
            ),
            estimate="수십 분",
            source_ids=(
                "seoul_bus_stop_passengers_hourly",
                "seoul_subway_station_passengers_hourly",
            ),
            risk="caution",
            requires_confirmation=True,
            timeout_seconds=7200,
        ),
        JobDefinition(
            key="preprocess_core",
            label="핵심 Silver 전처리",
            description="서울 상권 핵심 원천을 표준 Silver 테이블로 다시 전처리합니다.",
            group="pipeline",
            steps=(
                _step("기준 테이블", "scripts/preprocess_rule_engine_seed_tables.py"),
                _step("상권 경계", "scripts/preprocess_rule_engine_trade_area_boundary_geometry.py"),
                _step("매출·점포", "scripts/preprocess_rule_engine_trade_tables.py"),
                _step("인구", "scripts/preprocess_rule_engine_population_tables.py"),
                _step("집객시설", "scripts/preprocess_rule_engine_facility_table.py"),
                _step("변화지표", "scripts/preprocess_rule_engine_change_index.py"),
                _step("소비", "scripts/preprocess_rule_engine_consumption_table.py"),
            ),
            estimate="수십 분",
            risk="caution",
            requires_confirmation=True,
            timeout_seconds=7200,
        ),
        JobDefinition(
            key="build_gold",
            label="Gold 테이블 생성",
            description="검증된 Silver 입력으로 점수축별 Gold 테이블을 다시 생성합니다.",
            group="pipeline",
            steps=(_step("Gold 생성", "scripts/build_rule_engine_gold_tables.py"),),
            estimate="수 분",
            risk="caution",
            requires_confirmation=True,
            timeout_seconds=3600,
        ),
        JobDefinition(
            key="build_lookup",
            label="검색·공간 Lookup 생성",
            description="최신 상권 경계와 업종 계층으로 검색, 지도 좌표 판정용 Lookup을 다시 생성합니다.",
            group="pipeline",
            steps=(_step("검색·공간 Lookup 생성", "scripts/build_rule_engine_input_lookup_tables.py"),),
            estimate="수 분",
            risk="caution",
            requires_confirmation=True,
            timeout_seconds=3600,
        ),
        JobDefinition(
            key="build_scores",
            label="입지 점수 배치 생성",
            description="현재 제품 분기의 전체 상권·업종 입지 점수 배치를 생성합니다.",
            group="pipeline",
            steps=(
                _step(
                    "입지 점수 생성",
                    "scripts/build_rule_based_location_scores.py",
                    "--batch",
                ),
            ),
            estimate="수십 분",
            risk="caution",
            requires_confirmation=True,
            timeout_seconds=7200,
        ),
        JobDefinition(
            key="validate_pipeline",
            label="파이프라인 계약 검증",
            description="원천 커버리지와 전처리 파일 게이트를 실행해 게시 전 조건을 확인합니다.",
            group="pipeline",
            steps=(
                _step("원천 커버리지 검증", "scripts/validate_rule_pipeline_source_coverage.py"),
                _step("전처리 파일 게이트", "scripts/validate_preprocessing_file_gate.py"),
                _step("점수 제품 경로 검증", "scripts/validate_product_score_grounding.py"),
            ),
            estimate="수 분",
            timeout_seconds=1800,
        ),
        JobDefinition(
            key="publish_database",
            label="제품 DB 반영",
            description="최신 Gold와 점수 배치를 commercial.db에 반영합니다. 실행 전 DB 백업이 생성됩니다.",
            group="pipeline",
            steps=(
                _step("제품 DB 재구성", "final_proj/backend/scripts/seed_rule_gold_db.py"),
                _step("공간 인덱스 재구성", "final_proj/backend/scripts/seed_spatial_index.py"),
                _step("점수 제품 경로 검증", "scripts/validate_product_score_grounding.py"),
            ),
            estimate="수 분",
            risk="high",
            requires_confirmation=True,
            timeout_seconds=3600,
        ),
        JobDefinition(
            key="refresh_product_data",
            label="제품 핵심 데이터 갱신",
            description=(
                "서울 핵심 원천과 매출·점포를 같은 실행일로 수집한 뒤 Silver, Gold, "
                "입지 점수, 검증, 제품 DB와 공간 인덱스까지 순서대로 갱신합니다. "
                "RTMS·R-ONE·SBDC 전체 파일·생활이동·교통 후보 원천은 이 작업의 수집 범위가 아닙니다."
            ),
            group="pipeline",
            steps=(
                _step(
                    "서울 핵심·점포 원천 수집",
                    "scripts/ingest_seoul_core_p0_full.py",
                    "--include-store",
                    "--skip-unchanged",
                ),
                _step(
                    "서울 추정매출 원천 수집",
                    "scripts/ingest_seoul_sales_trade_area_full.py",
                    "--skip-unchanged",
                ),
                _step("기준 테이블 전처리", "scripts/preprocess_rule_engine_seed_tables.py"),
                _step("상권 경계 전처리", "scripts/preprocess_rule_engine_trade_area_boundary_geometry.py"),
                _step("매출·점포 전처리", "scripts/preprocess_rule_engine_trade_tables.py"),
                _step("인구 전처리", "scripts/preprocess_rule_engine_population_tables.py"),
                _step("집객시설 전처리", "scripts/preprocess_rule_engine_facility_table.py"),
                _step("변화지표 전처리", "scripts/preprocess_rule_engine_change_index.py"),
                _step("소비 전처리", "scripts/preprocess_rule_engine_consumption_table.py"),
                _step("Gold 생성", "scripts/build_rule_engine_gold_tables.py"),
                _step("검색·공간 Lookup 생성", "scripts/build_rule_engine_input_lookup_tables.py"),
                _step("입지 점수 생성", "scripts/build_rule_based_location_scores.py", "--batch"),
                _step("원천 커버리지 검증", "scripts/validate_rule_pipeline_source_coverage.py"),
                _step("전처리 파일 게이트", "scripts/validate_preprocessing_file_gate.py"),
                _step("제품 DB 재구성", "final_proj/backend/scripts/seed_rule_gold_db.py"),
                _step("공간 인덱스 재구성", "final_proj/backend/scripts/seed_spatial_index.py"),
                _step("점수 제품 경로 검증", "scripts/validate_product_score_grounding.py"),
            ),
            estimate="1시간 이상",
            source_ids=CORE_PRODUCT_SOURCE_IDS,
            risk="high",
            requires_confirmation=True,
            timeout_seconds=28800,
        ),
    )
}


SOURCE_JOB_MAP = {
    source_id: definition.key
    for definition in JOB_DEFINITIONS.values()
    for source_id in definition.source_ids
    if definition.group == "collection"
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect_jobs() -> sqlite3.Connection:
    ADMIN_ROOT.mkdir(parents=True, exist_ok=True)
    JOB_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(JOB_DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _pid_running(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == 259
        finally:
            kernel32.CloseHandle(handle)
    except (AttributeError, OSError):
        return False


def _hidden_startup_info() -> Any:
    if os.name != "nt":
        return None
    startup_info = subprocess.STARTUPINFO()
    startup_info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup_info.wShowWindow = subprocess.SW_HIDE
    return startup_info


def _spawn_background_process(command: list[str], *, env: dict[str, str] | None = None) -> int:
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    process = subprocess.Popen(
        command,
        cwd=WORKSPACE_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
        start_new_session=os.name != "nt",
        creationflags=creationflags,
        startupinfo=_hidden_startup_info(),
    )
    return process.pid


def _reconcile_inactive_jobs(*, grace_seconds: float = 5.0) -> None:
    """Finalize active DB rows whose worker process has already exited."""
    if os.getenv(WORKER_ENV_FLAG) == "1":
        return
    with closing(_connect_jobs()) as conn:
        rows = conn.execute(
            """
            SELECT id, status, pid, created_at, started_at
              FROM pipeline_job
             WHERE status IN ('queued', 'running', 'cancelling')
            """
        ).fetchall()
        now = datetime.now(timezone.utc)
        for row in rows:
            timestamp = str(row["started_at"] or row["created_at"] or "")
            try:
                reference = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                if reference.tzinfo is None:
                    reference = reference.replace(tzinfo=timezone.utc)
                age_seconds = (now - reference.astimezone(timezone.utc)).total_seconds()
            except ValueError:
                age_seconds = grace_seconds
            if age_seconds < grace_seconds or _pid_running(row["pid"]):
                continue
            cancelled = row["status"] == "cancelling"
            final_status = "cancelled" if cancelled else "interrupted"
            step_status = "cancelled" if cancelled else "failed"
            message = (
                "작업 프로세스가 종료되어 중지 처리를 완료했습니다."
                if cancelled
                else "실행 프로세스가 예기치 않게 종료되어 작업이 중단되었습니다."
            )
            finished_at = _now_iso()
            conn.execute(
                """
                UPDATE pipeline_job
                   SET status = ?, finished_at = ?, pid = NULL, current_step = NULL,
                       eta_seconds = NULL, message = ?
                 WHERE id = ? AND status IN ('queued', 'running', 'cancelling')
                """,
                (final_status, finished_at, message, row["id"]),
            )
            conn.execute(
                """
                UPDATE pipeline_job_step
                   SET status = ?, finished_at = ?, message = ?
                 WHERE job_id = ? AND status = 'running'
                """,
                (step_status, finished_at, message, row["id"]),
            )
        conn.commit()


def _init_job_store() -> None:
    with closing(_connect_jobs()) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS pipeline_job (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_key TEXT NOT NULL,
                label TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                exit_code INTEGER,
                pid INTEGER,
                current_step TEXT,
                step_count INTEGER NOT NULL DEFAULT 1,
                completed_steps INTEGER NOT NULL DEFAULT 0,
                skipped_steps INTEGER NOT NULL DEFAULT 0,
                current_units INTEGER NOT NULL DEFAULT 0,
                total_units INTEGER NOT NULL DEFAULT 0,
                current_unit TEXT,
                eta_seconds REAL,
                data_period_start TEXT,
                data_period_end TEXT,
                input_signature TEXT,
                change_summary_json TEXT,
                resumed_from_job_id INTEGER,
                log_path TEXT,
                message TEXT
            );
            CREATE TABLE IF NOT EXISTS pipeline_job_step (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                step_index INTEGER NOT NULL,
                label TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                input_signature TEXT,
                output_signature TEXT,
                output_files_json TEXT,
                current_units INTEGER NOT NULL DEFAULT 0,
                total_units INTEGER NOT NULL DEFAULT 0,
                unit TEXT,
                eta_seconds REAL,
                started_at TEXT,
                finished_at TEXT,
                message TEXT,
                reused_from_job_id INTEGER,
                UNIQUE(job_id, step_index),
                FOREIGN KEY(job_id) REFERENCES pipeline_job(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS ix_pipeline_job_status ON pipeline_job(status);
            CREATE INDEX IF NOT EXISTS ix_pipeline_job_created ON pipeline_job(created_at DESC);
            CREATE INDEX IF NOT EXISTS ix_pipeline_job_step_job ON pipeline_job_step(job_id, step_index);
            CREATE INDEX IF NOT EXISTS ix_pipeline_job_step_signature
                ON pipeline_job_step(step_index, input_signature, status);
            """
        )
        existing_columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(pipeline_job)").fetchall()
        }
        migrations = {
            "skipped_steps": "INTEGER NOT NULL DEFAULT 0",
            "current_units": "INTEGER NOT NULL DEFAULT 0",
            "total_units": "INTEGER NOT NULL DEFAULT 0",
            "current_unit": "TEXT",
            "eta_seconds": "REAL",
            "data_period_start": "TEXT",
            "data_period_end": "TEXT",
            "input_signature": "TEXT",
            "change_summary_json": "TEXT",
            "resumed_from_job_id": "INTEGER",
        }
        for column, declaration in migrations.items():
            if column not in existing_columns:
                conn.execute(f"ALTER TABLE pipeline_job ADD COLUMN {column} {declaration}")
        legacy_failures = conn.execute(
            """
            SELECT id, message
              FROM pipeline_job
             WHERE status = 'failed' AND (exit_code IS NULL OR exit_code = 0)
            """
        ).fetchall()
        for row in legacy_failures:
            match = re.search(r"종료 코드\s+(-?\d+)", str(row["message"] or ""))
            if match:
                conn.execute(
                    "UPDATE pipeline_job SET exit_code = ? WHERE id = ?",
                    (int(match.group(1)), row["id"]),
                )
        conn.commit()
    if os.getenv(WORKER_ENV_FLAG) == "1":
        return
    _reconcile_inactive_jobs()


def _duration_seconds(row: sqlite3.Row) -> float | None:
    if not row["started_at"]:
        return None
    try:
        start = datetime.fromisoformat(row["started_at"])
        end = datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else datetime.now(timezone.utc)
        return round(max(0.0, (end - start).total_seconds()), 1)
    except ValueError:
        return None


def _parse_json_object(value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _step_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "step_index": row["step_index"],
        "label": row["label"],
        "status": row["status"],
        "input_signature": row["input_signature"],
        "output_signature": row["output_signature"],
        "current_units": row["current_units"],
        "total_units": row["total_units"],
        "unit": row["unit"],
        "eta_seconds": row["eta_seconds"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "message": row["message"],
        "reused_from_job_id": row["reused_from_job_id"],
    }


def _job_steps(job_id: int | str) -> list[dict[str, Any]]:
    with closing(_connect_jobs()) as conn:
        rows = conn.execute(
            "SELECT * FROM pipeline_job_step WHERE job_id = ? ORDER BY step_index",
            (job_id,),
        ).fetchall()
    return [_step_row(row) for row in rows]


def _job_row(
    row: sqlite3.Row,
    include_log: bool = False,
    include_change_summary: bool = False,
) -> dict[str, Any]:
    result = {
        "id": row["id"],
        "job_key": row["job_key"],
        "label": row["label"],
        "status": row["status"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "exit_code": row["exit_code"],
        "pid": row["pid"],
        "current_step": row["current_step"],
        "step_count": row["step_count"],
        "completed_steps": row["completed_steps"],
        "skipped_steps": row["skipped_steps"],
        "current_units": row["current_units"],
        "total_units": row["total_units"],
        "current_unit": row["current_unit"],
        "eta_seconds": row["eta_seconds"],
        "data_period_start": row["data_period_start"],
        "data_period_end": row["data_period_end"],
        "input_signature": row["input_signature"],
        "resumed_from_job_id": row["resumed_from_job_id"],
        "message": row["message"],
        "duration_seconds": _duration_seconds(row),
        "is_active": row["status"] in ACTIVE_STATUSES,
    }
    if include_change_summary:
        result["change_summary"] = _parse_json_object(row["change_summary_json"])
    if include_log:
        result["log"] = _read_log_tail(row["log_path"])
    return result


def _read_log_tail(log_path: str | None, max_chars: int = 16000) -> str:
    if not log_path:
        return ""
    path = Path(log_path)
    if not path.exists():
        return ""
    # Long collection logs can be many MB. Read only enough bytes from the end
    # instead of loading the complete file on every detail poll.
    with path.open("rb") as handle:
        size = handle.seek(0, os.SEEK_END)
        handle.seek(max(0, size - max_chars * 4), os.SEEK_SET)
        text = handle.read().decode("utf-8", errors="replace")
    return text[-max_chars:]


def list_jobs(limit: int = 30) -> list[dict[str, Any]]:
    _reconcile_inactive_jobs()
    safe_limit = max(1, min(limit, 100))
    with closing(_connect_jobs()) as conn:
        rows = conn.execute(
            "SELECT * FROM pipeline_job ORDER BY id DESC LIMIT ?",
            (safe_limit,),
        ).fetchall()
    return [_job_row(row) for row in rows]


def _latest_job_for_key(job_key: str) -> dict[str, Any] | None:
    with closing(_connect_jobs()) as conn:
        row = conn.execute(
            "SELECT * FROM pipeline_job WHERE job_key = ? ORDER BY id DESC LIMIT 1",
            (job_key,),
        ).fetchone()
    return _job_row(row, include_change_summary=True) if row else None


def get_job(job_id: int | str, include_log: bool = True) -> dict[str, Any] | None:
    _reconcile_inactive_jobs()
    with closing(_connect_jobs()) as conn:
        row = conn.execute("SELECT * FROM pipeline_job WHERE id = ?", (job_id,)).fetchone()
    if not row:
        return None
    result = _job_row(
        row,
        include_log=include_log,
        include_change_summary=True,
    )
    result["steps"] = _job_steps(job_id)
    return result


def get_job_status(job_id: int | str) -> dict[str, Any] | None:
    """Return only the job row and structured step progress, never manifests or logs."""
    _reconcile_inactive_jobs()
    with closing(_connect_jobs()) as conn:
        row = conn.execute("SELECT * FROM pipeline_job WHERE id = ?", (job_id,)).fetchone()
    if not row:
        return None
    result = _job_row(row, include_change_summary=True)
    compact_fields = {
        "step_index",
        "label",
        "status",
        "current_units",
        "total_units",
        "unit",
        "eta_seconds",
        "message",
        "reused_from_job_id",
    }
    result["steps"] = [
        {key: value for key, value in step.items() if key in compact_fields}
        for step in _job_steps(job_id)
    ]
    return result


def active_job() -> dict[str, Any] | None:
    _reconcile_inactive_jobs()
    with closing(_connect_jobs()) as conn:
        row = conn.execute(
            "SELECT * FROM pipeline_job WHERE status IN ('queued', 'running', 'cancelling') ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return _job_row(row) if row else None


def _update_job(job_id: int, **values: Any) -> None:
    if not values:
        return
    columns = ", ".join(f"{key} = ?" for key in values)
    params = [*values.values(), job_id]
    with closing(_connect_jobs()) as conn:
        conn.execute(f"UPDATE pipeline_job SET {columns} WHERE id = ?", params)
        conn.commit()


def _job_status(job_id: int) -> str | None:
    with closing(_connect_jobs()) as conn:
        row = conn.execute("SELECT status FROM pipeline_job WHERE id = ?", (job_id,)).fetchone()
    return str(row[0]) if row else None


def _update_step(job_id: int, step_index: int, **values: Any) -> None:
    if not values:
        return
    columns = ", ".join(f"{key} = ?" for key in values)
    params = [*values.values(), job_id, step_index]
    with closing(_connect_jobs()) as conn:
        conn.execute(
            f"UPDATE pipeline_job_step SET {columns} WHERE job_id = ? AND step_index = ?",
            params,
        )
        conn.commit()


def _hash_payload(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


CHECKPOINT_ROOTS = (
    DATA_ROOT / "_silver",
    DATA_ROOT / "_gold",
    DATA_ROOT / "_gold_validation",
    DATA_ROOT / "_rule_validation",
    DATA_ROOT / "_location_judgement_outputs",
)


def _checkpoint_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(WORKSPACE_ROOT.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve()).replace("\\", "/")


def _snapshot_outputs() -> dict[str, dict[str, Any]]:
    snapshot: dict[str, dict[str, Any]] = {}
    for root in CHECKPOINT_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.name.endswith((".tmp", ".wal", ".shm")):
                continue
            stat = path.stat()
            snapshot[_checkpoint_path(path)] = {
                "exists": True,
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            }
    if DATABASE_PATH.exists():
        stat = DATABASE_PATH.stat()
        snapshot[_checkpoint_path(DATABASE_PATH)] = {
            "exists": True,
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }
    return snapshot


def _changed_output_files(
    before: dict[str, dict[str, Any]],
    after: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    changed: list[dict[str, Any]] = []
    for path in sorted(set(before) | set(after)):
        if before.get(path) == after.get(path):
            continue
        changed.append({"path": path, **after.get(path, {"exists": False})})
    return changed


def _output_checkpoint_valid(output_files_json: str | None) -> bool:
    if not output_files_json:
        return True
    try:
        entries = json.loads(output_files_json)
    except ValueError:
        return False
    if not isinstance(entries, list):
        return False
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("path"):
            return False
        path_value = Path(str(entry["path"]))
        path = path_value if path_value.is_absolute() else WORKSPACE_ROOT / path_value
        expected_exists = bool(entry.get("exists", True))
        if path.exists() != expected_exists:
            return False
        if not expected_exists:
            continue
        stat = path.stat()
        if stat.st_size != int(entry.get("size", -1)) or stat.st_mtime_ns != int(entry.get("mtime_ns", -1)):
            return False
    return True


def _step_input_signature(
    *,
    resolved: list[str],
    base_signature: str,
    upstream_signature: str,
    environment: dict[str, str] | None = None,
) -> str:
    script_value = Path(resolved[0])
    script = script_value if script_value.is_absolute() else WORKSPACE_ROOT / script_value
    return _hash_payload(
        {
            "signature_version": "localfit.step-input.v4",
            "args": resolved,
            "local_python_dependencies": _local_python_dependency_hashes(script),
            "declared_file_inputs": _declared_step_input_hashes(script),
            "result_environment": {
                key: (environment or os.environ).get(key, "")
                for key in RESULT_AFFECTING_ENV_KEYS
            },
            "pipeline_input": base_signature,
            "upstream_output": upstream_signature,
        }
    )


def _advance_pipeline_signature(input_signature: str, output_signature: str) -> str:
    """Carry every upstream input and output into the next checkpoint key."""
    return _hash_payload(
        {
            "signature_version": "localfit.pipeline-chain.v1",
            "step_input": input_signature,
            "step_output": output_signature,
        }
    )


RESULT_AFFECTING_ENV_KEYS = (
    "LOCALFIT_DATA_ROOT",
    "LOCALFIT_DATABASE_PATH",
    "LOCALFIT_GOLD_DIR",
    "LOCALFIT_RULE_VALIDATION_DIR",
    "LOCALFIT_RESEARCH_RULE_VALIDATION_DIR",
    "LOCALFIT_SCORE_BACKTEST_DIR",
    "LOCALFIT_BOUNDARY_VERTICES_PATH",
    "LOCALFIT_BUS_STOP_PATH",
    "LOCALFIT_STORE_POI_PATH",
    "LOCALFIT_SUBWAY_STATION_PATH",
)


def _local_module_candidates(module: str, current_file: Path, level: int = 0) -> list[Path]:
    parts = tuple(part for part in module.split(".") if part)
    roots: list[Path] = []
    if level:
        relative_root = current_file.parent
        for _ in range(max(0, level - 1)):
            relative_root = relative_root.parent
        roots.append(relative_root)
    else:
        roots.extend(
            (
                current_file.parent,
                WORKSPACE_ROOT,
                WORKSPACE_ROOT / "scripts",
                WORKSPACE_ROOT / "final_proj" / "backend",
            )
        )
    candidates: list[Path] = []
    for root in roots:
        module_path = root.joinpath(*parts) if parts else root
        candidates.extend((module_path.with_suffix(".py"), module_path / "__init__.py"))
    return candidates


def _local_python_dependency_hashes(entry_script: Path) -> list[dict[str, str]]:
    """Hash the entrypoint and recursively imported Python files inside this workspace."""
    workspace = WORKSPACE_ROOT.resolve()
    pending = [entry_script]
    visited: set[Path] = set()
    hashes: list[dict[str, str]] = []
    while pending:
        path = pending.pop().resolve()
        if path in visited:
            continue
        visited.add(path)
        try:
            path.relative_to(workspace)
        except ValueError:
            continue
        if not path.is_file() or path.suffix.lower() != ".py":
            hashes.append({"path": _checkpoint_path(path), "sha256": "missing"})
            continue
        content = path.read_bytes()
        hashes.append({"path": _checkpoint_path(path), "sha256": hashlib.sha256(content).hexdigest()})
        try:
            tree = ast.parse(content, filename=str(path))
        except (SyntaxError, ValueError):
            continue
        imports: list[tuple[str, int]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend((alias.name, 0) for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                imports.append((module, node.level))
                imports.extend(
                    (f"{module}.{alias.name}".strip("."), node.level)
                    for alias in node.names
                    if alias.name != "*"
                )
        for module, level in imports:
            for candidate in _local_module_candidates(module, path, level):
                if candidate.is_file():
                    pending.append(candidate)
                    break
    return sorted(hashes, key=lambda item: item["path"])


def _declared_step_input_hashes(entry_script: Path) -> list[dict[str, str]]:
    consumption_sources = tuple(
        sorted(
            path
            for path in DATA_ROOT.glob("*.csv")
            if "소비" in path.name
        )
    )
    declared: dict[str, tuple[Path, ...]] = {
        "scripts/preprocess_rule_engine_seed_tables.py": (
            DATA_ROOT / "_unzipped" / "서울시 상권분석서비스(영역-상권)",
            DATA_ROOT / "_final" / "spatial_od" / "SBDC_업종분류표_247.csv",
            DATA_ROOT / "_final" / "spatial_od" / "업종코드_서울_SBDC_매핑검증.csv",
        ),
        "scripts/preprocess_rule_engine_consumption_table.py": consumption_sources,
        "scripts/build_rule_engine_gold_tables.py": (
            DATA_ROOT / "_silver",
            WORKSPACE_ROOT / "scripts" / "build_growth_label_candidates.py",
            WORKSPACE_ROOT / "scripts" / "validate_growth_rebound_stability.py",
            WORKSPACE_ROOT / "scripts" / "build_growth_rebound_candidate_gold.py",
        ),
        "scripts/build_rule_engine_input_lookup_tables.py": (
            DATA_ROOT / "_gold" / "gold_trade_area_profile.csv",
            DATA_ROOT / "_gold" / "gold_industry_taxonomy.csv",
            DATA_ROOT / "_silver" / "silver_trade_area_boundary_spatial_index.csv",
            DATA_ROOT / "_silver" / "silver_trade_area_boundary_vertices.csv",
        ),
        "scripts/build_rule_based_location_scores.py": (
            DATA_ROOT / "_gold" / "gold_trade_area_profile.csv",
            DATA_ROOT / "_gold" / "gold_industry_taxonomy.csv",
            DATA_ROOT / "_gold" / "gold_sales_strength_q_industry.csv",
            DATA_ROOT / "_gold" / "gold_competition_q_industry.csv",
            DATA_ROOT / "_gold" / "gold_demand_q_area.csv",
            DATA_ROOT / "_gold" / "gold_growth_stability_q_industry.csv",
            DATA_ROOT / "_gold" / "gold_growth_rebound_candidate_q_industry.csv",
            DATA_ROOT / "_gold" / "gold_accessibility_q_area.csv",
            DATA_ROOT / "_gold" / "gold_cost_risk_q_area.csv",
            DATA_ROOT / "_silver" / "silver_reb_rone_seoul_cost_proxy_latest.csv",
            DATA_ROOT / "_score_backtest" / "location_score_backtest_recommended_weights.csv",
            DATA_ROOT / "_rule_validation" / "59_transit_accessibility_candidate_quarter_features.csv",
        ),
        "scripts/validate_rule_pipeline_source_coverage.py": (
            SOURCE_REGISTRY_PATH,
            INGEST_MANIFEST_PATH,
            FAILED_DOWNLOADS_PATH,
            DATA_ROOT / "_silver",
            DATA_ROOT / "_gold",
            DATA_ROOT / "_location_judgement_outputs",
        ),
        "scripts/validate_preprocessing_file_gate.py": (
            SOURCE_REGISTRY_PATH,
            INGEST_MANIFEST_PATH,
            FAILED_DOWNLOADS_PATH,
            DATA_ROOT / "_silver",
            DATA_ROOT / "_gold",
            DATA_ROOT / "_location_judgement_outputs",
        ),
    }
    paths = declared.get(_checkpoint_path(entry_script), ())
    entries: list[dict[str, str]] = []
    for path in paths:
        entries.append(
            {
                "path": _checkpoint_path(path),
                "sha256": _declared_input_fingerprint(path),
            }
        )
    return entries


def _declared_input_fingerprint(path: Path) -> str:
    if not path.exists():
        return "missing"
    if path.is_file():
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    entries: list[str] = []
    for child in sorted(path.rglob("*")):
        if not child.is_file() or child.name.endswith((".tmp", ".wal", ".shm")):
            continue
        stat = child.stat()
        relative = child.relative_to(path).as_posix()
        entries.append(f"{relative}:{stat.st_size}:{stat.st_mtime_ns}")
    return hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest()


def _find_reusable_step(
    *,
    job_id: int,
    job_key: str,
    step_index: int,
    input_signature: str,
) -> sqlite3.Row | None:
    with closing(_connect_jobs()) as conn:
        rows = conn.execute(
            """
            SELECT s.*, j.id AS source_job_id
              FROM pipeline_job_step AS s
              JOIN pipeline_job AS j ON j.id = s.job_id
             WHERE j.id < ?
               AND j.job_key = ?
               AND s.step_index = ?
               AND s.input_signature = ?
               AND s.status IN ('completed', 'skipped_checkpoint')
             ORDER BY j.id DESC
            """,
            (job_id, job_key, step_index, input_signature),
        ).fetchall()
    return next((row for row in rows if _output_checkpoint_valid(row["output_files_json"])), None)


RAW_PIPELINE_SERVICES = (
    "TbgisTrdarRelm",
    "VwsmTrdarFlpopQq",
    "VwsmTrdarRepopQq",
    "VwsmTrdarWrcPopltnQq",
    "VwsmTrdarIxQq",
    "VwsmTrdarFcltyQq",
    "VwsmTrdarStorQq",
    "VwsmTrdarSelngQq",
)
CUMULATIVE_RAW_SERVICES = {"VwsmTrdarStorQq", "VwsmTrdarSelngQq"}


def _raw_directory_content_fingerprint(
    directory: Path,
    service: str,
    manifest_hashes: dict[str, str],
) -> str:
    entries: list[str] = []
    for path in sorted(directory.glob(f"{service}_*.json")):
        relative = _checkpoint_path(path)
        digest = manifest_hashes.get(relative) or hashlib.sha256(path.read_bytes()).hexdigest()
        entries.append(f"{path.name}:{digest}")
    return hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest()


def _raw_pipeline_signature() -> str:
    raw_root = DATA_ROOT / "_raw_ingest"
    manifest_rows = _read_csv(INGEST_MANIFEST_PATH)
    hashes = {
        str(row.get("raw_path", "")).replace("\\", "/"): str(row.get("sha256", ""))
        for row in manifest_rows
        if row.get("raw_path") and row.get("sha256")
    }
    dated = sorted(
        (
            path
            for path in raw_root.iterdir()
            if path.is_dir() and len(path.name) == 8 and path.name.isdigit()
        ),
        key=lambda path: path.name,
        reverse=True,
    ) if raw_root.exists() else []
    entries: list[str] = []
    for service in RAW_PIPELINE_SERVICES:
        available = [
            (run, run / "seoul_open_data" / "full" / service)
            for run in dated
            if (run / "seoul_open_data" / "full" / service).exists()
        ]
        if not available:
            entries.append(f"{service}:missing")
            continue
        selected = available if service in CUMULATIVE_RAW_SERVICES else available[:1]
        fingerprints: list[str] = []
        seen_fingerprints: set[str] = set()
        for _run, directory in selected:
            fingerprint = _raw_directory_content_fingerprint(directory, service, hashes)
            if fingerprint in seen_fingerprints:
                continue
            seen_fingerprints.add(fingerprint)
            fingerprints.append(fingerprint)
        entries.extend(
            f"{service}:{index}:{fingerprint}"
            for index, fingerprint in enumerate(fingerprints)
        )
    return hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest()


def _read_collection_change_summary(job_id: int, run_date_value: str) -> dict[str, Any] | None:
    path = DATA_ROOT / "_raw_ingest" / run_date_value / "collection_change_summary.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if payload.get("pipeline_run_id") != f"job_{job_id}":
        return None
    return payload if isinstance(payload, dict) else None


def _read_core_source_freshness_report(job_id: int) -> dict[str, Any] | None:
    if not CORE_SOURCE_FRESHNESS_REPORT_PATH.exists():
        return None
    try:
        payload = json.loads(CORE_SOURCE_FRESHNESS_REPORT_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema_version") != "core_source_freshness.v1":
        return None
    if payload.get("job_id") != job_id:
        return None
    return payload


def _checkpoint_reuse_allowed(definition: JobDefinition, step_index: int) -> bool:
    if definition.group != "pipeline":
        return False
    if definition.key == "refresh_product_data":
        # Collection steps always run. The two publishers mutate the same database,
        # and the final grounding check must execute against that newly published DB,
        # so none of the last three steps may reuse an older checkpoint.
        return 2 < step_index <= len(definition.steps) - 3
    # Standalone transform buttons are explicit rebuild requests. Their inputs
    # span broad Silver/Gold trees, so silently reusing an older output would
    # violate the button contract when any artifact changed out of band.
    return False


def _read_progress_events(
    log_path: Path,
    offset: int,
    buffer: str,
) -> tuple[int, str, list[dict[str, Any]]]:
    if not log_path.exists():
        return offset, buffer, []
    with log_path.open("rb") as handle:
        handle.seek(offset)
        chunk = handle.read()
        new_offset = handle.tell()
    if not chunk:
        return offset, buffer, []
    text = buffer + chunk.decode("utf-8", errors="replace")
    lines = text.split("\n")
    next_buffer = lines.pop()
    events: list[dict[str, Any]] = []
    for line in lines:
        marker = "LOCALFIT_PROGRESS "
        position = line.find(marker)
        if position < 0:
            continue
        try:
            event = json.loads(line[position + len(marker):])
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return new_offset, next_buffer, events


def _apply_progress_event(job_id: int, step_index: int, event: dict[str, Any]) -> None:
    current_units = max(0, int(event.get("current_units") or 0))
    total_units = max(0, int(event.get("total_units") or 0))
    unit = str(event.get("unit") or "건")
    label = str(event.get("label") or "처리 중")
    eta_value = event.get("eta_seconds")
    eta_seconds = max(0.0, float(eta_value)) if eta_value is not None else None
    values: dict[str, Any] = {
        "current_units": current_units,
        "total_units": total_units,
        "unit": unit,
        "eta_seconds": eta_seconds,
        "message": str(event.get("message") or label),
    }
    _update_step(job_id, step_index, **values)
    job_values: dict[str, Any] = {
        "current_units": current_units,
        "total_units": total_units,
        "current_unit": unit,
        "eta_seconds": eta_seconds,
        "message": f"{label} · {current_units}/{total_units} {unit}" if total_units else label,
    }
    if event.get("data_period_start"):
        job_values["data_period_start"] = str(event["data_period_start"])
    if event.get("data_period_end"):
        job_values["data_period_end"] = str(event["data_period_end"])
    _update_job(job_id, **job_values)


def _latest_stable_month() -> str:
    today = datetime.now().date().replace(day=1)
    previous = today - timedelta(days=1)
    stable = previous.replace(day=1) - timedelta(days=1)
    return stable.strftime("%Y%m")


def _latest_quarter() -> str:
    if DATABASE_PATH.exists():
        try:
            with closing(sqlite3.connect(DATABASE_PATH, timeout=2)) as conn:
                row = conn.execute("SELECT MAX(quarter) FROM rule_location_score").fetchone()
            if row and row[0]:
                return str(row[0])
        except sqlite3.Error:
            pass
    score_files = sorted(
        (DATA_ROOT / "_location_judgement_outputs").glob("loc_score_v2_batch_*.csv"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if score_files:
        parts = score_files[0].stem.split("_")
        if len(parts) >= 5:
            return parts[4]
    return "20261"


@lru_cache(maxsize=8)
def _expected_gold_quarter_snapshot(path_value: str, mtime_ns: int, size: int) -> str:
    del mtime_ns, size
    path = Path(path_value)
    latest: int | None = None
    with path.open(encoding="utf-8-sig", newline="") as handle:
        header = handle.readline().rstrip("\r\n").split(",")
        if not header or header[0] != "기준_년분기_코드":
            raise RuntimeError(f"Gold sales quarter column is missing: {path}")
        for line in handle:
            value = line.split(",", 1)[0].strip().strip('"')
            if value.isdigit():
                latest = max(latest or int(value), int(value))
    if latest is None:
        raise RuntimeError(f"Gold sales has no quarter values: {path}")
    return str(latest)


def _expected_gold_quarter(data_root: Path) -> str:
    path = data_root / "_gold" / "gold_sales_strength_q_industry.csv"
    if not path.exists():
        raise RuntimeError(f"Gold sales artifact is missing: {path}")
    stat = path.stat()
    return _expected_gold_quarter_snapshot(
        str(path.resolve()), stat.st_mtime_ns, stat.st_size
    )


def _assert_refresh_product_postconditions(
    *,
    database_path: Path | None = None,
    data_root: Path | None = None,
    expected_quarter_override: str | None = None,
) -> dict[str, Any]:
    db_path = database_path or DATABASE_PATH
    corpus_root = data_root or DATA_ROOT
    expected_quarter = expected_quarter_override or _expected_gold_quarter(corpus_root)
    required_columns: dict[str, set[str]] = {
        "commercial_area": {"area_code", "area_name", "district_code"},
        "district_population": {"area_code", "resident_population", "worker_population"},
        "district_floating": {"area_code", "floating_population"},
        "district_sales": {"area_code", "industry_code", "sales_amount", "timestamp"},
        "district_store_count": {"area_code", "industry_code", "store_count", "timestamp"},
        "district_growth_history": {
            "area_code",
            "sales_amount",
            "floating_population",
            "store_count",
            "timestamp",
        },
        "area_sale_price_proxy": {
            "area_code",
            "sale_price_proxy_manwon_per_m2",
            "period",
            "source_id",
            "direct_score_allowed",
            "proxy_score_allowed",
        },
        "area_rone_cost_reference": {
            "area_code",
            "period",
            "selection_group",
            "metric_code",
            "metric_value",
            "direct_value_allowed",
            "proxy_score_allowed",
            "engine_promotion_ready",
            "forbidden_claim_ko",
        },
        "industry_hierarchy": {"industry_code", "industry_name"},
        "location_lookup": {"area_code", "district_name"},
        "rule_location_score": {
            "quarter",
            "area_code",
            "industry_code",
            "score_version",
            "current_location_score",
            "context_location_score",
            "grade",
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
        },
        "rule_area_score_summary": {
            "quarter",
            "area_code",
            "score_version",
            "top_industry_status",
            "score_definition",
        },
    }
    spatial_tables = (
        "spatial_store_point",
        "spatial_store_point_rtree",
        "spatial_transit_point",
        "spatial_transit_point_rtree",
        "spatial_dataset_status",
    )
    failures: list[str] = []
    counts: dict[str, int] = {}
    if not db_path.exists():
        raise RuntimeError(f"Product database is missing: {db_path}")
    with closing(sqlite3.connect(db_path, timeout=10)) as conn:
        quick_check = [str(row[0]) for row in conn.execute("PRAGMA quick_check").fetchall()]
        if quick_check != ["ok"]:
            failures.append(f"quick_check={quick_check}")
        existing_tables = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        for table, expected_columns in required_columns.items():
            if table not in existing_tables:
                failures.append(f"missing_table:{table}")
                continue
            columns = {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")')}
            missing_columns = sorted(expected_columns - columns)
            if missing_columns:
                failures.append(f"missing_columns:{table}:{','.join(missing_columns)}")
            count = int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            counts[table] = count
            if count <= 0:
                failures.append(f"empty_table:{table}")
        for table in spatial_tables:
            if table not in existing_tables:
                failures.append(f"missing_table:{table}")
                continue
            count = int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            counts[table] = count
            if count <= 0:
                failures.append(f"empty_table:{table}")
        if "rule_location_score" in existing_tables:
            latest_quarter = str(
                conn.execute("SELECT MAX(quarter) FROM rule_location_score").fetchone()[0] or ""
            )
            versions = {
                str(row[0])
                for row in conn.execute("SELECT DISTINCT score_version FROM rule_location_score")
            }
            if latest_quarter != expected_quarter:
                failures.append(f"location_quarter={latest_quarter}/{expected_quarter}")
            if versions != {LOCATION_SCORE_VERSION}:
                failures.append(f"location_score_versions={sorted(versions)}")
            unsafe_score_count = int(
                conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM rule_location_score
                    WHERE
                        (COALESCE(official_rank_eligible, 0) = 0
                         AND (current_location_score IS NOT NULL OR grade IS NOT NULL))
                        OR
                        (COALESCE(official_rank_eligible, 0) = 1
                         AND (current_location_score IS NULL OR grade IS NULL))
                        OR
                        (data_reliability_score < 40
                         AND COALESCE(official_rank_eligible, 0) != 0)
                        OR
                        (COALESCE(official_indicator_complete, 0) = 0
                         AND COALESCE(official_rank_eligible, 0) != 0)
                        OR
                        (COALESCE(available_axis_count, 0) < 3
                         AND context_location_score IS NOT NULL)
                        OR
                        (COALESCE(available_axis_count, 0) >= 3
                         AND context_location_score IS NULL)
                    """
                ).fetchone()[0]
            )
            if unsafe_score_count:
                failures.append(f"rule_score_fail_closed={unsafe_score_count}")
        if "rule_area_score_summary" in existing_tables:
            latest_area_quarter = str(
                conn.execute("SELECT MAX(quarter) FROM rule_area_score_summary").fetchone()[0] or ""
            )
            area_versions = {
                str(row[0])
                for row in conn.execute("SELECT DISTINCT score_version FROM rule_area_score_summary")
            }
            if latest_area_quarter != expected_quarter:
                failures.append(f"area_quarter={latest_area_quarter}/{expected_quarter}")
            if area_versions != {AREA_SCORE_VERSION}:
                failures.append(f"area_score_versions={sorted(area_versions)}")
        if "area_rone_cost_reference" in existing_tables:
            unsafe_flag_count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM area_rone_cost_reference "
                    "WHERE COALESCE(direct_value_allowed, 0) != 0 "
                    "OR COALESCE(proxy_score_allowed, 0) != 0 "
                    "OR COALESCE(engine_promotion_ready, 0) != 0"
                ).fetchone()[0]
            )
            if unsafe_flag_count:
                failures.append(f"rone_unsafe_contract_flags={unsafe_flag_count}")
        for point_table, rtree_table in (
            ("spatial_store_point", "spatial_store_point_rtree"),
            ("spatial_transit_point", "spatial_transit_point_rtree"),
        ):
            if point_table in counts and rtree_table in counts and counts[point_table] != counts[rtree_table]:
                failures.append(
                    f"spatial_count_mismatch:{point_table}={counts[point_table]}/"
                    f"{rtree_table}={counts[rtree_table]}"
                )
        if "spatial_dataset_status" in existing_tables:
            status_counts = {
                str(row[0]): int(row[1])
                for row in conn.execute(
                    "SELECT dataset_key, record_count FROM spatial_dataset_status"
                )
            }
            for dataset_key, point_table in (
                ("store_point", "spatial_store_point"),
                ("transit_point", "spatial_transit_point"),
            ):
                if status_counts.get(dataset_key) != counts.get(point_table):
                    failures.append(
                        f"spatial_status_mismatch:{dataset_key}={status_counts.get(dataset_key)}/"
                        f"{point_table}={counts.get(point_table)}"
                    )
        if "users" not in existing_tables:
            failures.append("missing_table:users")
        else:
            user_columns = {str(row[1]) for row in conn.execute('PRAGMA table_info("users")')}
            if "is_admin" not in user_columns:
                failures.append("missing_columns:users:is_admin")
            else:
                admin_count = int(
                    conn.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1").fetchone()[0]
                )
                counts["admin_users"] = admin_count
                if admin_count <= 0:
                    failures.append("admin_users=0")
    if failures:
        raise RuntimeError("Final product postcondition failed: " + "; ".join(failures))
    return {"expected_quarter": expected_quarter, "counts": counts, "quick_check": "ok"}


def _resolve_args(args: tuple[str, ...]) -> list[str]:
    resolved: list[str] = []
    for value in args:
        if value == "{latest_stable_month}":
            resolved.append(_latest_stable_month())
        elif value == "{latest_quarter}":
            resolved.append(_latest_quarter())
        else:
            resolved.append(value)
    return resolved


def _execute_job(job_id: int, definition: JobDefinition) -> None:
    log_path = JOB_LOG_ROOT / f"job_{job_id:06d}.log"
    current_index = 0
    completion_message: str | None = None
    if _job_status(job_id) == "cancelling":
        _update_job(
            job_id,
            status="cancelled",
            finished_at=_now_iso(),
            message="실행 전에 작업이 중지되었습니다.",
        )
        return
    _update_job(
        job_id,
        status="running",
        started_at=_now_iso(),
        log_path=str(log_path),
        current_units=0,
        total_units=0,
        current_unit=None,
        eta_seconds=None,
        message="작업을 시작했습니다.",
    )
    job_environment = {**os.environ, "PYTHONUTF8": "1"}
    step_python = Path(sys.executable)
    if os.name == "nt":
        step_python = Path(getattr(sys, "_base_executable", sys.executable))
        job_environment["__PYVENV_LAUNCHER__"] = sys.executable
    run_date_value = datetime.now().strftime("%Y%m%d")
    if definition.key == "refresh_product_data":
        job_environment["LOCALFIT_RUN_DATE"] = run_date_value
        job_environment["LOCALFIT_PIPELINE_RUN_ID"] = f"job_{job_id}"
    if definition.key == "status_check":
        job_environment["LOCALFIT_FRESHNESS_JOB_ID"] = str(job_id)

    base_signature = _raw_pipeline_signature()
    upstream_signature = base_signature
    skipped_steps = 0

    try:
        with log_path.open("w", encoding="utf-8", newline="") as log:
            log.write(f"[{_now_iso()}] {definition.label}\n")
            log.write(f"workspace: {WORKSPACE_ROOT}\n")
            log.flush()
            for index, step in enumerate(definition.steps, start=1):
                current_index = index
                resolved = _resolve_args(step.args)
                command = [str(step_python), *resolved]
                input_signature = _step_input_signature(
                    resolved=resolved,
                    base_signature=base_signature,
                    upstream_signature=upstream_signature,
                    environment=job_environment,
                )

                if _checkpoint_reuse_allowed(definition, index):
                    reusable = _find_reusable_step(
                        job_id=job_id,
                        job_key=definition.key,
                        step_index=index,
                        input_signature=input_signature,
                    )
                    if reusable:
                        source_job_id = int(reusable["source_job_id"])
                        output_signature = str(reusable["output_signature"] or _hash_payload({"empty": True}))
                        _update_step(
                            job_id,
                            index,
                            status="skipped_checkpoint",
                            input_signature=input_signature,
                            output_signature=output_signature,
                            output_files_json=reusable["output_files_json"],
                            current_units=int(reusable["total_units"] or 1),
                            total_units=int(reusable["total_units"] or 1),
                            unit=reusable["unit"] or "단계",
                            eta_seconds=0,
                            started_at=_now_iso(),
                            finished_at=_now_iso(),
                            message=f"동일 입력 체크포인트 재사용 (작업 #{source_job_id})",
                            reused_from_job_id=source_job_id,
                        )
                        skipped_steps += 1
                        _update_job(
                            job_id,
                            current_step=step.label,
                            completed_steps=index,
                            skipped_steps=skipped_steps,
                            current_units=1,
                            total_units=1,
                            current_unit="단계",
                            eta_seconds=0,
                            resumed_from_job_id=source_job_id,
                            message=f"{step.label}: 동일 입력 체크포인트를 재사용했습니다.",
                        )
                        log.write(
                            f"\n[{_now_iso()}] STEP {index}/{len(definition.steps)}: {step.label} "
                            f"SKIPPED checkpoint job={source_job_id}\n"
                        )
                        log.flush()
                        upstream_signature = _advance_pipeline_signature(
                            input_signature,
                            output_signature,
                        )
                        continue

                _update_job(
                    job_id,
                    current_step=step.label,
                    completed_steps=index - 1,
                    current_units=0,
                    total_units=0,
                    current_unit=None,
                    eta_seconds=None,
                    message=f"{step.label} 작업을 시작했습니다.",
                )
                _update_step(
                    job_id,
                    index,
                    status="running",
                    input_signature=input_signature,
                    current_units=0,
                    total_units=0,
                    unit=None,
                    eta_seconds=None,
                    started_at=_now_iso(),
                    finished_at=None,
                    message="실행 중",
                )
                log.write(f"\n[{_now_iso()}] STEP {index}/{len(definition.steps)}: {step.label}\n")
                log.write(f"command: python {' '.join(resolved)}\n")
                log.flush()
                before_outputs = (
                    {}
                    if definition.key == "status_check"
                    else (_snapshot_outputs() if index > 2 or definition.key != "refresh_product_data" else {})
                )
                progress_offset = log_path.stat().st_size if log_path.exists() else 0
                progress_buffer = ""
                creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                process = subprocess.Popen(
                    command,
                    cwd=WORKSPACE_ROOT,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    env=job_environment,
                    creationflags=creationflags,
                    startupinfo=_hidden_startup_info(),
                )
                deadline = time.monotonic() + definition.timeout_seconds
                while process.poll() is None:
                    progress_offset, progress_buffer, progress_events = _read_progress_events(
                        log_path, progress_offset, progress_buffer
                    )
                    if progress_events:
                        _apply_progress_event(job_id, index, progress_events[-1])
                    if _job_status(job_id) == "cancelling":
                        process.terminate()
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=10)
                        raise JobCancelledError("사용자가 작업을 중지했습니다.")
                    if time.monotonic() >= deadline:
                        process.kill()
                        process.wait(timeout=30)
                        raise RuntimeError(f"{step.label} 작업이 제한 시간을 초과했습니다.")
                    time.sleep(0.25)
                progress_offset, progress_buffer, progress_events = _read_progress_events(
                    log_path, progress_offset, progress_buffer
                )
                if progress_events:
                    _apply_progress_event(job_id, index, progress_events[-1])
                exit_code = process.returncode
                if exit_code != 0:
                    _update_job(job_id, exit_code=exit_code)
                    _update_step(
                        job_id,
                        index,
                        status="failed",
                        finished_at=_now_iso(),
                        message=f"종료 코드 {exit_code}",
                    )
                    raise RuntimeError(f"{step.label} 작업이 종료 코드 {exit_code}로 실패했습니다.")

                if definition.key == "status_check":
                    freshness_report = _read_core_source_freshness_report(job_id)
                    if not freshness_report:
                        _update_job(job_id, exit_code=1)
                        raise RuntimeError("외부 원천 점검 결과 파일이 없거나 현재 작업과 일치하지 않습니다.")
                    services = [
                        service
                        for source in freshness_report.get("sources", [])
                        if isinstance(source, dict)
                        for service in source.get("services", [])
                        if isinstance(service, dict)
                    ]
                    period_starts = [
                        str(item["data_period_start"])
                        for item in services
                        if item.get("data_period_start")
                    ]
                    _update_job(
                        job_id,
                        change_summary_json=json.dumps(
                            freshness_report,
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                        data_period_start=min(period_starts) if period_starts else None,
                        data_period_end=freshness_report.get("latest_provider_period"),
                    )
                    completion_message = str(
                        freshness_report.get("decision") or "데이터 최신 상태 점검이 완료되었습니다."
                    )

                after_outputs = _snapshot_outputs() if before_outputs else {}
                output_files = _changed_output_files(before_outputs, after_outputs) if before_outputs else []
                output_signature = _hash_payload(output_files)
                if definition.key == "refresh_product_data" and index in {1, 2}:
                    output_signature = _raw_pipeline_signature()
                if definition.key == "refresh_product_data" and index == 2:
                    change_summary = _read_collection_change_summary(job_id, run_date_value)
                    if change_summary:
                        sources = change_summary.get("sources", [])
                        period_starts = [str(item["data_period_start"]) for item in sources if item.get("data_period_start")]
                        period_ends = [str(item["data_period_end"]) for item in sources if item.get("data_period_end")]
                        _update_job(
                            job_id,
                            change_summary_json=json.dumps(change_summary, ensure_ascii=False, sort_keys=True),
                            data_period_start=min(period_starts) if period_starts else None,
                            data_period_end=max(period_ends) if period_ends else None,
                        )
                    base_signature = _raw_pipeline_signature()
                    output_signature = base_signature
                    _update_job(job_id, input_signature=base_signature)

                final_units = 1
                final_unit = "단계"
                with closing(_connect_jobs()) as conn:
                    progress_row = conn.execute(
                        "SELECT total_units, unit FROM pipeline_job_step WHERE job_id = ? AND step_index = ?",
                        (job_id, index),
                    ).fetchone()
                if progress_row and int(progress_row[0] or 0) > 0:
                    final_units = int(progress_row[0])
                    final_unit = str(progress_row[1] or "단계")
                _update_step(
                    job_id,
                    index,
                    status="completed",
                    input_signature=input_signature,
                    output_signature=output_signature,
                    output_files_json=json.dumps(output_files, ensure_ascii=False, separators=(",", ":")),
                    current_units=final_units,
                    total_units=final_units,
                    unit=final_unit,
                    eta_seconds=0,
                    finished_at=_now_iso(),
                    message="완료",
                )
                _update_job(
                    job_id,
                    completed_steps=index,
                    exit_code=exit_code,
                    current_units=final_units,
                    total_units=final_units,
                    current_unit=final_unit,
                    eta_seconds=0,
                )
                log.write(f"[{_now_iso()}] STEP {index} completed\n")
                log.flush()
                upstream_signature = _advance_pipeline_signature(
                    input_signature,
                    output_signature,
                )
        if _job_status(job_id) == "cancelling":
            raise JobCancelledError("사용자가 작업을 중지했습니다.")
        if definition.key == "refresh_product_data":
            postconditions = _assert_refresh_product_postconditions()
            with log_path.open("a", encoding="utf-8") as log:
                log.write(
                    f"\n[{_now_iso()}] FINAL POSTCONDITION OK: "
                    f"{json.dumps(postconditions, ensure_ascii=False, sort_keys=True)}\n"
                )
        _update_job(
            job_id,
            status="success",
            finished_at=_now_iso(),
            pid=None,
            current_step=None,
            completed_steps=len(definition.steps),
            current_units=0,
            total_units=0,
            current_unit=None,
            eta_seconds=0,
            exit_code=0,
            message=(
                completion_message
                or (
                    f"작업이 완료되었습니다. {skipped_steps}개 단계는 검증된 체크포인트로 재사용했습니다."
                    if skipped_steps
                    else "작업이 완료되었습니다."
                )
            ),
        )
    except JobCancelledError as exc:
        try:
            with log_path.open("a", encoding="utf-8") as log:
                log.write(f"\n[{_now_iso()}] CANCELLED: {exc}\n")
        except OSError:
            pass
        if current_index:
            _update_step(
                job_id,
                current_index,
                status="cancelled",
                finished_at=_now_iso(),
                message=str(exc),
            )
        _update_job(
            job_id,
            status="cancelled",
            finished_at=_now_iso(),
            pid=None,
            current_step=None,
            eta_seconds=None,
            message=str(exc),
        )
    except Exception as exc:
        try:
            with log_path.open("a", encoding="utf-8") as log:
                log.write(f"\n[{_now_iso()}] FAILED: {type(exc).__name__}: {exc}\n")
        except OSError:
            pass
        if current_index:
            _update_step(
                job_id,
                current_index,
                status="failed",
                finished_at=_now_iso(),
                message=str(exc),
            )
        _update_job(
            job_id,
            status="failed",
            finished_at=_now_iso(),
            pid=None,
            current_step=None,
            eta_seconds=None,
            message=str(exc),
        )


_SUBMIT_LOCK = threading.Lock()


def execute_stored_job(job_id: int) -> None:
    with closing(_connect_jobs()) as conn:
        row = conn.execute("SELECT job_key FROM pipeline_job WHERE id = ?", (job_id,)).fetchone()
    if not row:
        raise KeyError(job_id)
    definition = JOB_DEFINITIONS.get(str(row["job_key"]))
    if not definition:
        _update_job(
            job_id,
            status="failed",
            finished_at=_now_iso(),
            message="등록되지 않은 작업 정의입니다.",
        )
        return
    _execute_job(job_id, definition)


def start_job(job_key: str, confirmed: bool = False) -> dict[str, Any]:
    definition = JOB_DEFINITIONS.get(job_key)
    if not definition:
        raise KeyError(job_key)
    if not _definition_enabled(definition):
        raise FileNotFoundError("작업에 필요한 스크립트가 없습니다.")
    if definition.requires_confirmation and not confirmed:
        raise PermissionError("이 작업은 실행 전 확인이 필요합니다.")

    with _SUBMIT_LOCK:
        current = active_job()
        if current:
            raise JobConflictError(current)
        created_at = _now_iso()
        with closing(_connect_jobs()) as conn:
            cursor = conn.execute(
                """
                INSERT INTO pipeline_job (
                    job_key, label, status, created_at, step_count, completed_steps, message
                ) VALUES (?, ?, 'queued', ?, ?, 0, ?)
                """,
                (
                    definition.key,
                    definition.label,
                    created_at,
                    len(definition.steps),
                    "실행 대기 중입니다.",
                ),
            )
            job_id = int(cursor.lastrowid)
            conn.executemany(
                """
                INSERT INTO pipeline_job_step (job_id, step_index, label, status)
                VALUES (?, ?, ?, 'pending')
                """,
                [
                    (job_id, index, step.label)
                    for index, step in enumerate(definition.steps, start=1)
                ],
            )
            conn.commit()
        python_path = WORKSPACE_ROOT / "final_proj" / ".venv" / "Scripts" / "python.exe"
        worker_executable = python_path if python_path.exists() else Path(sys.executable)
        worker_environment = {**os.environ}
        if os.name == "nt" and python_path.exists():
            worker_executable = Path(getattr(sys, "_base_executable", sys.executable))
            worker_environment["__PYVENV_LAUNCHER__"] = str(python_path)
        worker_command = [
            str(worker_executable),
            str(WORKER_SCRIPT_PATH),
            "--job-id",
            str(job_id),
        ]
        try:
            worker_pid = _spawn_background_process(worker_command, env=worker_environment)
            _update_job(job_id, pid=worker_pid)
        except OSError as exc:
            _update_job(
                job_id,
                status="failed",
                finished_at=_now_iso(),
                message=f"작업 워커를 시작하지 못했습니다: {exc}",
            )
            raise RuntimeError("작업 워커를 시작하지 못했습니다.") from exc
    job = get_job(job_id, include_log=False)
    if not job:
        raise RuntimeError("생성한 작업을 찾지 못했습니다.")
    return job


def cancel_job(job_id: int) -> dict[str, Any]:
    with closing(_connect_jobs()) as conn:
        cursor = conn.execute(
            """
            UPDATE pipeline_job
               SET status = 'cancelling',
                   message = '작업 중지를 요청했습니다.'
             WHERE id = ?
               AND status IN ('queued', 'running')
            """,
            (job_id,),
        )
        conn.commit()
    if cursor.rowcount != 1:
        raise ValueError("실행 중인 작업만 중지할 수 있습니다.")
    job = get_job(job_id, include_log=False)
    if not job:
        raise RuntimeError("중지할 작업을 찾지 못했습니다.")
    return job


def _definition_enabled(definition: JobDefinition) -> bool:
    return all((WORKSPACE_ROOT / step.args[0]).exists() for step in definition.steps)


def _job_output_contract(definition: JobDefinition) -> tuple[str, bool, str]:
    if definition.key == "reb_rent":
        return (
            "pipeline_stage",
            True,
            "R-ONE 서울 수집부터 MDIS·공식 보고서 결합, 검증, 기준선 DB 게시까지 수행합니다. 상권별 점수에는 직접 사용하지 않습니다.",
        )
    if definition.group == "collection":
        return (
            "raw_only",
            False,
            "원천 수집 결과만 저장합니다. 제품 수치는 후속 전처리·검증·게시 전까지 바뀌지 않습니다.",
        )
    if definition.key == "refresh_product_data":
        return (
            "core_product_chain",
            True,
            "서울 핵심 7개 원천부터 제품 DB까지 반영합니다. 외부·정적 제품 입력은 별도 계보로 관리합니다.",
        )
    if definition.group == "pipeline":
        return (
            "pipeline_stage",
            definition.key == "publish_database",
            "선택한 파이프라인 단계만 실행합니다. 앞 단계 입력과 검증 상태를 함께 확인해야 합니다.",
        )
    return ("status_only", False, "상태를 읽기만 하며 제품 데이터를 변경하지 않습니다.")


def public_job_definitions() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for definition in JOB_DEFINITIONS.values():
        if definition.key in ADMIN_HIDDEN_JOB_KEYS:
            continue
        output_scope, updates_product, scope_note = _job_output_contract(definition)
        results.append({
            "key": definition.key,
            "label": definition.label,
            "description": definition.description,
            "group": definition.group,
            "estimate": definition.estimate,
            "risk": definition.risk,
            "requires_confirmation": definition.requires_confirmation,
            "step_count": len(definition.steps),
            "source_ids": list(definition.source_ids),
            "enabled": _definition_enabled(definition),
            "output_scope": output_scope,
            "updates_product": updates_product,
            "scope_note": scope_note,
        })
    return results


@lru_cache(maxsize=8)
def _read_csv_snapshot(
    path_value: str,
    mtime_ns: int,
    size: int,
) -> tuple[tuple[tuple[str, str], ...], ...]:
    del mtime_ns, size  # Cache-key only; file metadata invalidates published CSV snapshots.
    path = Path(path_value)
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return tuple(
            tuple((str(key), str(value or "")) for key, value in row.items())
            for row in csv.DictReader(handle)
        )


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    stat = path.stat()
    snapshot = _read_csv_snapshot(str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    return [dict(row) for row in snapshot]


def _file_iso(path: Path) -> str | None:
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")


def _directory_summary(path: Path, pattern: str = "*.csv") -> dict[str, Any]:
    files = list(path.glob(pattern)) if path.exists() else []
    return {
        "file_count": len(files),
        "bytes": sum(item.stat().st_size for item in files),
        "updated_at": max((_file_iso(item) for item in files), default=None),
    }


def _health_worst(*values: str) -> str:
    rank = {"healthy": 0, "advisory": 1, "unknown": 2, "warning": 3, "missing": 4, "error": 5}
    available = [value for value in values if value]
    return max(available, key=lambda value: rank.get(value, 1), default="unknown")


@lru_cache(maxsize=32)
def _json_snapshot(path_value: str, mtime_ns: int, size: int) -> Any:
    del mtime_ns, size
    try:
        return json.loads(Path(path_value).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    stat = path.stat()
    return _json_snapshot(str(path.resolve()), stat.st_mtime_ns, stat.st_size)


def _score_validation_layer() -> dict[str, Any]:
    """Summarize score grounding separately from data-pipeline health."""

    def read_summary(path: Path) -> tuple[dict[str, Any] | None, str | None]:
        try:
            payload = _read_json(path)
            updated_at = _file_iso(path)
        except OSError:
            return None, None
        return (payload if isinstance(payload, dict) else None), updated_at

    def nonnegative_int(value: Any) -> int:
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    grounding_path = PRODUCT_SCORE_GROUNDING_SUMMARY_PATH
    if grounding_path is None:
        grounding_candidates = list(
            PRODUCT_SCORE_VALIDATION_ROOT.glob("product_score_grounding*/validation_summary.json")
        )
        grounding_path = max(
            grounding_candidates,
            key=lambda path: path.stat().st_mtime_ns,
            default=None,
        )
    grounding, grounding_updated = (
        read_summary(grounding_path) if grounding_path else (None, None)
    )
    market, market_updated = read_summary(MARKET_SCORE_VALIDATION_SUMMARY_PATH)
    survival, survival_updated = read_summary(BUSINESS_SURVIVAL_VALIDATION_SUMMARY_PATH)

    pass_count = nonnegative_int(grounding.get("pass_count")) if grounding else 0
    fail_count = nonnegative_int(grounding.get("fail_count")) if grounding else 0
    grounding_status = str(grounding.get("status") or "").strip().lower() if grounding else "missing"
    failed_check_ids = (
        [str(value) for value in grounding.get("failed_check_ids", [])]
        if grounding and isinstance(grounding.get("failed_check_ids"), list)
        else []
    )
    predictive_status = (
        str(survival.get("predictive_status") or "unknown").strip().lower()
        if survival
        else "missing"
    )
    market_promotion_pass = bool(
        market
        and isinstance(market.get("weight_decision"), dict)
        and market["weight_decision"].get("promotion_pass") is True
    )

    if grounding is None:
        status = "warning"
        grounding_note = "Gold→DB→지도/리포트 일치 검증 결과가 없습니다."
    elif grounding_status != "pass" or fail_count:
        status = "error"
        failed_label = ", ".join(failed_check_ids[:3]) or f"실패 {fail_count}건"
        grounding_note = f"Gold→DB→지도/리포트 일치 검증 실패: {failed_label}."
    else:
        status = "healthy"
        grounding_note = f"Gold→DB→지도/리포트 일치 검증 {pass_count}건 통과."

    if market is None or survival is None:
        status = _health_worst(status, "warning")
    if market is not None and not market_promotion_pass:
        status = _health_worst(status, "advisory")
    if predictive_status not in {"supported", "positive_signal", "validated"}:
        # This is a model-interpretation limitation, not a pipeline execution failure.
        status = _health_worst(status, "advisory")

    if market is None:
        market_note = "시장 성과 검증 결과 없음."
    elif market_promotion_pass:
        market_note = "시장 성과 기반 가중치 승격 조건 통과."
    else:
        market_note = "시장 성과 기반 가중치 승격 조건 미충족."
    survival_note = (
        "개별 365일 생존 예측 검증 통과."
        if predictive_status in {"supported", "positive_signal", "validated"}
        else "개별 365일 생존확률로 해석 불가."
    )

    methodology = {}
    if grounding and isinstance(grounding.get("details"), dict):
        raw_methodology = grounding["details"].get("methodology")
        if isinstance(raw_methodology, dict):
            methodology = raw_methodology

    return {
        "key": "score_validation",
        "label": "점수 근거 검증",
        "status": status,
        "count": pass_count,
        "unit": "통과 항목",
        "updated_at": max(
            (value for value in (grounding_updated, market_updated, survival_updated) if value),
            default=None,
        ),
        "note": f"{grounding_note} {market_note} {survival_note}",
        "job_key": "validate_pipeline",
        "grounding_status": grounding_status,
        "failed_check_ids": failed_check_ids,
        "market_validation_available": market is not None,
        "market_promotion_pass": market_promotion_pass,
        "survival_predictive_status": predictive_status,
        "methodology": methodology,
    }


@lru_cache(maxsize=24)
def _csv_period_range_snapshot(
    path_value: str,
    mtime_ns: int,
    size: int,
    column: str,
) -> tuple[str | None, str | None]:
    del mtime_ns, size
    values: set[str] = set()
    try:
        with Path(path_value).open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                value = str(row.get(column) or "").strip()
                if value:
                    values.add(value)
    except OSError:
        return None, None
    if not values:
        return None, None

    def period_key(value: str) -> tuple[int, int | str]:
        return (0, int(value)) if value.isdigit() else (1, value)

    ordered = sorted(values, key=period_key)
    return ordered[0], ordered[-1]


def _csv_period_range(path: Path, column: str) -> tuple[str | None, str | None]:
    if not path.exists():
        return None, None
    stat = path.stat()
    return _csv_period_range_snapshot(
        str(path.resolve()), stat.st_mtime_ns, stat.st_size, column
    )


def _validation_artifact_failed(path: Path) -> bool:
    if path.suffix.lower() == ".json":
        payload = _read_json(path)
        if not isinstance(payload, dict) or not payload:
            return True
        for key in ("validation_fail_count", "fail_count", "failed_count"):
            value = payload.get(key)
            if value in (None, ""):
                continue
            if isinstance(value, bool):
                parsed_count = int(value)
            elif isinstance(value, int):
                parsed_count = value
            elif isinstance(value, float) and value.is_integer():
                parsed_count = int(value)
            elif isinstance(value, str) and value.strip().lstrip("+").isdigit():
                parsed_count = int(value.strip())
            else:
                return True
            if parsed_count < 0 or parsed_count > 0:
                return True
        raw_decision = (
            payload.get("decision")
            or payload.get("status")
            or payload.get("overall_status")
            or ""
        )
        if not isinstance(raw_decision, str):
            return True
        decision = raw_decision.strip().lower()
        if payload.get("schema_version") and decision not in {
            "pass",
            "passed",
            "success",
            "healthy",
            "ok",
            "통과",
        }:
            return True
        return any(token in decision for token in ("fail", "error", "실패"))

    rows = _read_csv(path)
    for row in rows:
        for key, value in row.items():
            key_lower = str(key).lower()
            if not any(token in key_lower for token in ("result", "status", "judgement", "판정")):
                continue
            normalized = str(value or "").strip().lower()
            if normalized in {"fail", "failed", "error", "실패"}:
                return True
    return False


def _external_source_lineage(source_id: str) -> dict[str, Any] | None:
    contract = PRODUCT_EXTERNAL_SOURCE_CONTRACTS.get(source_id)
    if not contract:
        return None
    artifact_paths = [DATA_ROOT / value for value in contract.get("artifacts", ())]
    validation_paths = [DATA_ROOT / value for value in contract.get("validation_artifacts", ())]
    missing_artifacts = [path.name for path in artifact_paths if not path.exists()]
    missing_validations = [path.name for path in validation_paths if not path.exists()]
    failed_validations = [
        path.name for path in validation_paths if path.exists() and _validation_artifact_failed(path)
    ]
    existing_artifacts = [path for path in artifact_paths if path.exists()]
    updated_values = [_file_iso(path) for path in existing_artifacts]
    release_artifact_value = str(contract.get("release_artifact") or "").strip()
    release_column = str(contract.get("release_column") or "release_id")
    artifact_release_id: str | None = None
    release_problem: str | None = None
    if release_artifact_value:
        release_artifact_path = DATA_ROOT / release_artifact_value
        if not release_artifact_path.exists():
            release_problem = f"release 산출물 누락: {release_artifact_path.name}"
        else:
            artifact_release_ids = {
                str(row.get(release_column) or "").strip()
                for row in _read_csv(release_artifact_path)
                if str(row.get(release_column) or "").strip()
            }
            if len(artifact_release_ids) != 1:
                release_problem = (
                    f"release ID가 하나가 아님: {sorted(artifact_release_ids)[:3]}"
                )
            else:
                artifact_release_id = next(iter(artifact_release_ids))
    database_table = str(contract.get("database_table") or "").strip()
    database_row_count: int | None = None
    database_problem: str | None = release_problem
    database_published_at: str | None = None
    if database_table:
        if not database_table.replace("_", "").isalnum():
            database_problem = "허용되지 않은 DB 테이블 이름"
        elif not DATABASE_PATH.exists():
            database_problem = "제품 DB 누락"
        else:
            try:
                database_uri = f"file:{DATABASE_PATH.resolve().as_posix()}?mode=ro"
                with closing(sqlite3.connect(database_uri, uri=True, timeout=1)) as conn:
                    conn.execute("PRAGMA query_only = ON")
                    table_exists = conn.execute(
                        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                        (database_table,),
                    ).fetchone()
                    if not table_exists:
                        database_problem = f"DB 테이블 누락: {database_table}"
                    else:
                        database_columns = {
                            str(row[1])
                            for row in conn.execute(f'PRAGMA table_info("{database_table}")')
                        }
                        missing_columns = sorted(
                            set(contract.get("database_required_columns", ())) - database_columns
                        )
                        if missing_columns:
                            database_problem = "DB 필수 열 누락: " + ", ".join(missing_columns)
                        else:
                            database_source_value = str(contract.get("database_source_value") or "")
                            if database_source_value:
                                database_row_count = int(
                                    conn.execute(
                                        f'SELECT COUNT(*) FROM "{database_table}" WHERE "source_id" = ?',
                                        (database_source_value,),
                                    ).fetchone()[0]
                                )
                                unsafe_rows = int(
                                    conn.execute(
                                        f'SELECT COUNT(*) FROM "{database_table}" '
                                        'WHERE "source_id" = ? AND COALESCE("direct_score_allowed", 0) != 0',
                                        (database_source_value,),
                                    ).fetchone()[0]
                                )
                                database_release_ids = {
                                    str(row[0])
                                    for row in conn.execute(
                                        f'SELECT DISTINCT "release_id" FROM "{database_table}" '
                                        'WHERE "source_id" = ?',
                                        (database_source_value,),
                                    )
                                }
                                database_published_at = str(
                                    conn.execute(
                                        f'SELECT MAX("generated_at_utc") FROM "{database_table}" '
                                        'WHERE "source_id" = ?',
                                        (database_source_value,),
                                    ).fetchone()[0]
                                    or ""
                                ) or None
                            else:
                                database_row_count = int(
                                    conn.execute(
                                        f'SELECT COUNT(*) FROM "{database_table}"'
                                    ).fetchone()[0]
                                )
                                unsafe_rows = 0
                                database_release_ids = set()
                            if database_row_count <= 0:
                                database_problem = (
                                    f"DB 소스 행 누락: {database_table}/{database_source_value}"
                                )
                            elif unsafe_rows:
                                database_problem = (
                                    f"DB 직접 점수 사용 금지 위반: {database_source_value} {unsafe_rows}행"
                                )
                            elif artifact_release_id and database_release_ids != {artifact_release_id}:
                                database_problem = (
                                    "Gold/DB release 불일치: "
                                    f"gold={artifact_release_id}, db={sorted(database_release_ids)}"
                                )
                if database_published_at:
                    updated_values.append(database_published_at)
            except sqlite3.Error as exc:
                database_problem = f"DB 확인 실패: {exc}"
    period_start = period_end = contract.get("period_hint")
    period_artifact = contract.get("period_artifact")
    period_column = contract.get("period_column")
    if period_artifact and period_column:
        period_start, period_end = _csv_period_range(DATA_ROOT / period_artifact, period_column)

    if missing_artifacts or database_problem:
        status = "missing"
        details = []
        if missing_artifacts:
            details.append("제품 산출물 누락: " + ", ".join(missing_artifacts))
        if database_problem:
            details.append(database_problem)
        detail = "; ".join(details)
    elif failed_validations:
        status = "error"
        detail = "제품 계보 검증 실패: " + ", ".join(failed_validations)
    elif missing_validations:
        status = "warning"
        detail = str(contract["refresh_note"])
        detail += " 검증 산출물 누락: " + ", ".join(missing_validations)
    elif contract.get("reviewed_external_chain") or contract.get("included_in_product_refresh"):
        status = "healthy"
        detail = str(contract["refresh_note"])
    else:
        # A valid static artifact is still not refreshed by refresh_product_data.
        # Keep it visible as a separate lineage without presenting it as a broken API.
        status = "advisory"
        detail = str(contract["refresh_note"])
    return {
        "product_role": contract["product_role"],
        "product_lineage_status": status,
        "product_artifact_updated_at": max((value for value in updated_values if value), default=None),
        "product_artifact_oldest_updated_at": min((value for value in updated_values if value), default=None),
        "product_data_period_start": period_start,
        "product_data_period_end": period_end,
        "included_in_product_refresh": bool(contract.get("included_in_product_refresh")),
        "reviewed_external_chain": bool(
            contract.get("reviewed_external_chain") or contract.get("included_in_product_refresh")
        ),
        "product_refresh_note": detail,
        "product_artifacts": [path.name for path in artifact_paths],
        "product_database_table": database_table or None,
        "product_database_rows": database_row_count,
        "product_release_id": artifact_release_id,
        "product_database_published_at": database_published_at,
    }


CORE_SILVER_ARTIFACTS = (
    "silver_trade_area_master.csv",
    "silver_trade_area_boundary_geometry.csv",
    "silver_sales_trade_area_q_industry.csv",
    "silver_store_trade_area_q_industry.csv",
    "silver_population_demand_q_area.csv",
    "silver_facility_trade_area_q.csv",
    "silver_change_index_trade_area_q.csv",
    "silver_consumption_trade_area_q.csv",
)
CORE_SILVER_VALIDATIONS = (
    "03_sales_store_domain_validation.csv",
    "04_population_domain_validation.csv",
    "05_change_index_domain_validation.csv",
    "06_facility_domain_validation.csv",
    "28_consumption_domain_validation.csv",
)


def _silver_contract_status() -> dict[str, Any]:
    artifacts = [DATA_ROOT / "_silver" / name for name in CORE_SILVER_ARTIFACTS]
    validations = [DATA_ROOT / "_rule_validation" / name for name in CORE_SILVER_VALIDATIONS]
    missing = [path.name for path in artifacts + validations if not path.exists()]
    failed = [path.name for path in validations if path.exists() and _validation_artifact_failed(path)]
    existing = [path for path in artifacts if path.exists()]
    if missing:
        status = "missing"
        note = "필수 Silver/검증 산출물 누락: " + ", ".join(missing)
    elif failed:
        status = "error"
        note = "Silver 검증 실패: " + ", ".join(failed)
    else:
        status = "healthy"
        note = "핵심 Silver 8종과 도메인 검증 산출물이 존재합니다."
    return {
        "status": status,
        "note": note,
        "count": len(existing),
        "updated_at": max((_file_iso(path) for path in existing), default=None),
        "newest_mtime_ns": max((path.stat().st_mtime_ns for path in existing), default=0),
    }


@lru_cache(maxsize=16)
def _sha256_snapshot(path_value: str, mtime_ns: int, size: int) -> str:
    digest = hashlib.sha256()
    with Path(path_value).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_file(path: Path) -> str:
    stat = path.stat()
    return _sha256_snapshot(str(path.resolve()), stat.st_mtime_ns, stat.st_size)


def _gold_release_status(silver_state: dict[str, Any] | None = None) -> dict[str, Any]:
    summary_path = DATA_ROOT / "_gold_validation" / "23_gold_preprocess_summary.json"
    validation_path = DATA_ROOT / "_gold_validation" / "23_gold_rule_validation_summary.csv"
    required_tables = {
        "gold_trade_area_profile.csv",
        "gold_industry_taxonomy.csv",
        "gold_sales_strength_q_industry.csv",
        "gold_competition_q_industry.csv",
        "gold_demand_q_area.csv",
        "gold_accessibility_q_area.csv",
        "gold_growth_stability_q_industry.csv",
        "gold_cost_risk_q_area.csv",
        "gold_data_reliability_snapshot.csv",
    }
    manifest_rows = _read_csv(GOLD_MANIFEST_PATH)
    summary = _read_json(summary_path)
    failures: list[str] = []
    if not manifest_rows:
        failures.append("Gold manifest 없음")
    if not isinstance(summary, dict):
        failures.append("Gold 검증 요약 없음")
        summary = {}
    if not validation_path.exists() or _validation_artifact_failed(validation_path):
        failures.append("Gold rule validation 실패/누락")
    tables = {row.get("gold_table", "") for row in manifest_rows}
    missing_tables = sorted(required_tables - tables)
    if missing_tables:
        failures.append("manifest 필수 테이블 누락=" + ",".join(missing_tables))
    versions = {row.get("gold_version", "") for row in manifest_rows if row.get("gold_version")}
    releases = {row.get("release_id", "") for row in manifest_rows if row.get("release_id")}
    if versions != {GOLD_VERSION}:
        failures.append(f"gold_version={sorted(versions)}")
    if len(releases) != 1 or summary.get("release_id") not in releases:
        failures.append("Gold release_id 불일치")
    if int(summary.get("validation_fail_count") or 0) != 0:
        failures.append(f"Gold validation_fail_count={summary.get('validation_fail_count')}")
    for row in manifest_rows:
        raw_path = str(row.get("path") or "").replace("\\", os.sep)
        path = WORKSPACE_ROOT / raw_path
        if not raw_path or not path.exists():
            failures.append(f"Gold 파일 누락={row.get('gold_table')}")
            continue
        expected_size = int(row.get("file_bytes") or 0)
        if expected_size and path.stat().st_size != expected_size:
            failures.append(f"Gold 파일 크기 불일치={row.get('gold_table')}")
    manifest_mtime_ns = GOLD_MANIFEST_PATH.stat().st_mtime_ns if GOLD_MANIFEST_PATH.exists() else 0
    if silver_state and silver_state.get("newest_mtime_ns", 0) > manifest_mtime_ns:
        failures.append("Gold가 최신 Silver보다 오래됨")
    status = "healthy" if not failures else ("missing" if not manifest_rows else "warning")
    return {
        "status": status,
        "note": "검증된 현재 Gold release입니다." if not failures else "; ".join(failures[:4]),
        "count": len(manifest_rows),
        "updated_at": _file_iso(GOLD_MANIFEST_PATH),
        "release_id": next(iter(releases), None) if len(releases) == 1 else None,
        "generated_at": summary.get("generated_at"),
        "manifest_sha256": _sha256_file(GOLD_MANIFEST_PATH) if GOLD_MANIFEST_PATH.exists() else None,
    }


def _canonical_manifest_source(source_id: str) -> str:
    if source_id.startswith("seoul_district_rss_"):
        return "seoul_district_official_rss"
    if source_id.startswith("korea_policy_briefing"):
        return "korea_policy_briefing"
    return source_id


def _key_presence() -> dict[str, bool]:
    module_path = WORKSPACE_ROOT / "scripts" / "ingest_common.py"
    if not module_path.exists() or not KEY_FILE.exists():
        return {}
    try:
        spec = importlib.util.spec_from_file_location("_localfit_ingest_common", module_path)
        if not spec or not spec.loader:
            return {}
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return {key: bool(value) for key, value in module.parse_key_file().items()}
    except Exception:
        return {}


def _credential_status(reference: str, presence: dict[str, bool]) -> str:
    if not reference or reference == "불필요":
        return "not_required"
    requirements: dict[str, tuple[str, ...]] = {
        "SEOUL_OPEN_DATA_KEY": ("seoul_key",),
        "PUBLIC_DATA_KEY_RTMS": ("rtms_key",),
        "REB_RONE_KEY": ("reb_key",),
        "PUBLIC_DATA_KEY_SBDC": ("sbdc_key",),
        "SGIS_SERVICE_ID_AND_SECRET": ("sgis_service_id", "sgis_secret"),
        "KOSIS_API_KEY": ("kosis_key",),
        "VWORLD_KEY;JUSO_KEY": ("vworld_key", "juso_key"),
        "NAVER_API_HUB_CLIENT_ID_SECRET": (
            "naver_api_hub_client_id",
            "naver_api_hub_client_secret",
        ),
    }
    if "_OR_" in reference:
        candidate = reference.split("_OR_", 1)[0]
        keys = requirements.get(candidate, ("seoul_key",))
        return "configured" if all(presence.get(key, False) for key in keys) else "optional"
    keys = requirements.get(reference)
    if not keys:
        return "unknown"
    return "configured" if all(presence.get(key, False) for key in keys) else "missing"


def _source_health(registry_status: str, latest_status: str | None) -> str:
    status = (latest_status or "").lower()
    if status and any(token in status for token in ("fail", "error", "blocked")):
        return "error"
    if status.startswith("success"):
        return "healthy"
    if registry_status == "docs_and_validated_samples_collected_bulk_restricted":
        return "warning"
    if latest_status or registry_status.startswith(("collected", "existing")):
        return "healthy"
    return "unknown"


def _core_source_health_ttl_hours(sampled_skip_ttl_hours: float | None = None) -> float:
    value = os.getenv(
        "LOCALFIT_CORE_SOURCE_HEALTH_TTL_HOURS",
        str(DEFAULT_CORE_SOURCE_HEALTH_TTL_HOURS),
    ).strip()
    try:
        configured = max(0.0, float(value))
    except ValueError as exc:
        raise ValueError(
            "LOCALFIT_CORE_SOURCE_HEALTH_TTL_HOURS must be a non-negative number."
        ) from exc
    if sampled_skip_ttl_hours is None:
        return configured
    return max(configured, max(0.0, float(sampled_skip_ttl_hours)))


@lru_cache(maxsize=4)
def _source_state_snapshot(path_value: str, mtime_ns: int, size: int) -> dict[str, Any]:
    del mtime_ns, size
    try:
        payload = json.loads(Path(path_value).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _source_state_for(source_id: str) -> dict[str, Any]:
    if not SOURCE_STATE_PATH.exists():
        return {}
    stat = SOURCE_STATE_PATH.stat()
    payload = _source_state_snapshot(str(SOURCE_STATE_PATH.resolve()), stat.st_mtime_ns, stat.st_size)
    services = payload.get("services", {}) if isinstance(payload.get("services"), dict) else {}
    expected_services = SOURCE_SERVICE_MAP.get(source_id, ())
    values = [services.get(service) for service in expected_services]
    values = [value for value in values if isinstance(value, dict)]
    if not values:
        return {}
    starts = [str(value["data_period_start"]) for value in values if value.get("data_period_start")]
    ends = [str(value["data_period_end"]) for value in values if value.get("data_period_end")]
    latest_window_starts = [
        str(value["latest_window_period_start"])
        for value in values
        if value.get("latest_window_period_start")
    ]
    latest_window_ends = [
        str(value["latest_window_period_end"])
        for value in values
        if value.get("latest_window_period_end")
    ]
    retained_starts = [
        str(value["retained_period_start"])
        for value in values
        if value.get("retained_period_start")
    ]
    retained_ends = [
        str(value["retained_period_end"])
        for value in values
        if value.get("retained_period_end")
    ]
    content_version_dates = [
        str(value["content_version_date"])
        for value in values
        if value.get("content_version_date")
    ]
    latest_snapshot_dates = [
        str(value["latest_snapshot_date"])
        for value in values
        if value.get("latest_snapshot_date")
    ]
    last_checked_times = [
        str(value["last_checked_at"])
        for value in values
        if value.get("last_checked_at")
    ]
    full_collection_times: list[str] = []
    for value in values:
        candidates = [
            str(timestamp)
            for timestamp in (
                value.get("full_collection_completed_at"),
                value.get("last_full_collection_at"),
            )
            if timestamp
        ]
        if candidates:
            # Older catalogs retained both fields.  The newest proven full run is
            # authoritative; preferring the first non-empty field created false TTL alerts.
            full_collection_times.append(max(candidates))
    # Collector state used to persist an age calculated at collection time.
    # Recalculate from the completion timestamps on every dashboard request so
    # freshness keeps advancing even when no collector has run since then.
    full_ages: list[float] = []
    now = datetime.now(timezone.utc)
    for timestamp in full_collection_times:
        try:
            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            full_ages.append(
                max(0.0, (now - parsed.astimezone(timezone.utc)).total_seconds() / 3600)
            )
        except ValueError:
            continue
    if not full_ages:
        full_ages = [
            float(value["last_full_collection_age_hours"])
            for value in values
            if value.get("last_full_collection_age_hours") is not None
        ]
    ttl_values = [
        float(value["sampled_skip_ttl_hours"])
        for value in values
        if value.get("sampled_skip_ttl_hours") is not None
    ]
    if not ttl_values and source_id in CORE_PRODUCT_SOURCE_IDS:
        configured_ttl = os.getenv("LOCALFIT_SAMPLED_SKIP_TTL_HOURS", "24").strip()
        try:
            ttl_values = [max(0.0, float(configured_ttl))]
        except ValueError:
            ttl_values = [DEFAULT_CORE_FULL_COLLECTION_TTL_HOURS]
    sampled_skip_ttl = min(ttl_values) if ttl_values else None
    health_ttl = (
        _core_source_health_ttl_hours(sampled_skip_ttl)
        if source_id in CORE_PRODUCT_SOURCE_IDS
        else sampled_skip_ttl
    )
    full_refresh_due = any(
        "full_refresh_due" in str(value.get("probe_status") or "")
        and str(value.get("change_status") or "") not in {"unchanged_full", "revised_full"}
        for value in values
    )
    return {
        "expected_service_count": len(expected_services),
        "observed_service_count": len(values),
        "full_collection_service_count": len(full_collection_times),
        "data_period_start": min(starts) if starts else None,
        "data_period_end": max(ends) if ends else None,
        "latest_window_period_start": min(latest_window_starts) if latest_window_starts else None,
        "latest_window_period_end": max(latest_window_ends) if latest_window_ends else None,
        "retained_period_start": min(retained_starts) if retained_starts else None,
        "retained_period_end": max(retained_ends) if retained_ends else None,
        "content_version_date": min(content_version_dates) if content_version_dates else None,
        "latest_snapshot_date": max(latest_snapshot_dates) if latest_snapshot_dates else None,
        "last_checked_at": max(last_checked_times) if last_checked_times else None,
        "last_full_collection_at": max(full_collection_times) if full_collection_times else None,
        "last_full_collection_age_hours": max(full_ages) if full_ages else None,
        "sampled_skip_ttl_hours": sampled_skip_ttl,
        "health_ttl_hours": health_ttl,
        "sample_count": sum(int(value.get("sample_count") or 0) for value in values) or None,
        "full_refresh_due": full_refresh_due,
        "probe_status": ";".join(
            sorted({str(value.get("probe_status")) for value in values if value.get("probe_status")})
        ) or None,
        "change_status": ";".join(sorted({str(value.get("change_status")) for value in values if value.get("change_status")})) or None,
        "content_fingerprint": ";".join(
            str(value.get("full_content_fingerprint") or value.get("content_fingerprint"))
            for value in values
            if value.get("full_content_fingerprint") or value.get("content_fingerprint")
        ) or None,
    }


def source_statuses() -> list[dict[str, Any]]:
    registry = _read_csv(SOURCE_REGISTRY_PATH)
    manifest = _read_csv(INGEST_MANIFEST_PATH)
    failed_downloads = _read_csv(FAILED_DOWNLOADS_PATH)
    contract = {row.get("source_id", ""): row for row in _read_csv(EXECUTION_CONTRACT_PATH)}
    grouped: dict[str, dict[str, Any]] = {}
    for row in manifest:
        source_id = _canonical_manifest_source(row.get("source_id", ""))
        bucket = grouped.setdefault(
            source_id,
            {
                "rows": 0,
                "failures": 0,
                "latest_manifest": None,
                "latest_event": None,
                "latest_failure": None,
            },
        )
        bucket["rows"] += 1
        status = row.get("collection_status", "")
        if any(token in status.lower() for token in ("fail", "error", "blocked")) and not status.startswith("superseded"):
            bucket["failures"] += 1
        latest_manifest = bucket["latest_manifest"]
        if not latest_manifest or row.get("collected_at", "") > latest_manifest.get("collected_at", ""):
            bucket["latest_manifest"] = row
        latest_event = bucket["latest_event"]
        if not latest_event or row.get("collected_at", "") > latest_event.get("collected_at", ""):
            bucket["latest_event"] = row

    for row in failed_downloads:
        source_id = _canonical_manifest_source(row.get("source_id", ""))
        bucket = grouped.setdefault(
            source_id,
            {
                "rows": 0,
                "failures": 0,
                "latest_manifest": None,
                "latest_event": None,
                "latest_failure": None,
            },
        )
        bucket["failures"] += 1
        failure_event = {
            **row,
            "collection_status": "failed",
            "collected_at": row.get("attempted_at", ""),
            "http_status": "500",
            "change_status": "failed",
        }
        latest_failure = bucket["latest_failure"]
        if not latest_failure or failure_event["collected_at"] > latest_failure.get("collected_at", ""):
            bucket["latest_failure"] = failure_event
        latest_event = bucket["latest_event"]
        if not latest_event or failure_event["collected_at"] > latest_event.get("collected_at", ""):
            bucket["latest_event"] = failure_event

    presence = _key_presence()
    results: list[dict[str, Any]] = []
    for source in registry:
        source_id = source.get("source_id", "")
        state = _source_state_for(source_id)
        bucket = grouped.get(source_id, {})
        latest_manifest = bucket.get("latest_manifest") or {}
        latest_event = bucket.get("latest_event") or latest_manifest
        latest_failure = bucket.get("latest_failure") or {}
        contract_row = contract.get(source_id, {})
        product_contract = PRODUCT_EXTERNAL_SOURCE_CONTRACTS.get(source_id, {})
        engine_role = contract_row.get("engine_role") or product_contract.get("engine_role") or (
            "evidence_only" if source_id in NEWS_SOURCE_IDS else "unclassified"
        )
        refresh_job_key = None if source_id in NEWS_SOURCE_IDS else (
            SOURCE_JOB_MAP.get(source_id) or product_contract.get("refresh_job_key")
        )
        health = _source_health(source.get("current_status", ""), latest_event.get("collection_status"))
        if source_id in CORE_PRODUCT_SOURCE_IDS:
            expected_count = int(state.get("expected_service_count") or len(SOURCE_SERVICE_MAP.get(source_id, ())))
            full_count = int(state.get("full_collection_service_count") or 0)
            if not expected_count or full_count != expected_count:
                health = _health_worst(health, "warning")
            age = state.get("last_full_collection_age_hours")
            ttl = state.get("health_ttl_hours")
            if age is not None and ttl is not None and float(age) > float(ttl):
                health = _health_worst(health, "warning")
            if state.get("full_refresh_due"):
                health = _health_worst(health, "warning")
        product_lineage = _external_source_lineage(source_id)
        result = {
                "source_id": source_id,
                "provider": source.get("provider", ""),
                "dataset_name": source.get("dataset_name", ""),
                "priority": source.get("priority", ""),
                "registry_status": source.get("current_status", ""),
                "collection_method": source.get("collection_method", ""),
                "credential_status": _credential_status(source.get("credential_ref", ""), presence),
                "engine_role": engine_role,
                "preprocessing_status": contract_row.get("preprocessing_status")
                or (
                    "reviewed_external_chain"
                    if product_contract.get("reviewed_external_chain")
                    or product_contract.get("included_in_product_refresh")
                    else "not_registered"
                ),
                "health": health,
                "manifest_rows": int(bucket.get("rows", 0)),
                "failure_rows": int(bucket.get("failures", 0)),
                "last_status": latest_event.get("collection_status"),
                "last_collected_at": latest_manifest.get("collected_at"),
                "last_http_status": latest_event.get("http_status"),
                "last_failure_at": latest_failure.get("collected_at"),
                "last_failure_type": latest_failure.get("failure_type"),
                "last_data_period_start": state.get("latest_window_period_start")
                or state.get("data_period_start")
                or latest_manifest.get("data_period_start"),
                "last_data_period_end": state.get("latest_window_period_end")
                or state.get("data_period_end")
                or latest_manifest.get("data_period_end"),
                "retained_data_period_start": state.get("retained_period_start"),
                "retained_data_period_end": state.get("retained_period_end"),
                "content_version_date": state.get("content_version_date"),
                "latest_snapshot_date": state.get("latest_snapshot_date"),
                "last_checked_at": state.get("last_checked_at"),
                "last_full_collection_at": state.get("last_full_collection_at"),
                "last_full_collection_age_hours": state.get("last_full_collection_age_hours"),
                "sampled_skip_ttl_hours": state.get("sampled_skip_ttl_hours"),
                "health_ttl_hours": state.get("health_ttl_hours"),
                "sample_count": state.get("sample_count"),
                "probe_status": state.get("probe_status"),
                "last_change_status": state.get("change_status") or latest_manifest.get("change_status"),
                "last_content_fingerprint": state.get("content_fingerprint")
                or latest_manifest.get("content_fingerprint")
                or latest_manifest.get("sha256")
                or None,
                "refresh_job_key": refresh_job_key,
                "refresh_available": bool(
                    refresh_job_key
                    and refresh_job_key in JOB_DEFINITIONS
                    and _definition_enabled(JOB_DEFINITIONS[refresh_job_key])
                ),
            }
        if product_lineage:
            result.update(product_lineage)
        results.append(result)
    return results


def _count_csv_rows(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open(encoding="utf-8-sig", errors="replace") as handle:
        return max(0, sum(1 for _ in handle) - 1)


def _database_summary() -> dict[str, Any]:
    result: dict[str, Any] = {
        "exists": DATABASE_PATH.exists(),
        "bytes": DATABASE_PATH.stat().st_size if DATABASE_PATH.exists() else 0,
        "updated_at": _file_iso(DATABASE_PATH),
        "quarter": None,
        "table_counts": {},
        "status": "missing" if not DATABASE_PATH.exists() else "healthy",
    }
    if not DATABASE_PATH.exists():
        return result
    tables = [
        "commercial_area",
        "district_sales",
        "district_store_count",
        "rule_location_score",
        "industry_hierarchy",
    ]
    try:
        with closing(sqlite3.connect(DATABASE_PATH, timeout=3)) as conn:
            for table in tables:
                result["table_counts"][table] = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            row = conn.execute("SELECT MAX(quarter) FROM rule_location_score").fetchone()
            result["quarter"] = str(row[0]) if row and row[0] is not None else None
    except sqlite3.Error as exc:
        result["status"] = "warning"
        result["message"] = str(exc)
    return result


@lru_cache(maxsize=8)
def _database_contract_snapshot(
    database_path_value: str,
    database_mtime_ns: int,
    database_size: int,
    gold_sales_mtime_ns: int,
    gold_sales_size: int,
    expected_quarter: str | None,
    verified_refresh_job_id: int | None,
) -> dict[str, Any]:
    del database_mtime_ns, database_size, gold_sales_mtime_ns, gold_sales_size
    failures: list[str] = []
    counts: dict[str, int] = {}
    try:
        with closing(sqlite3.connect(database_path_value, timeout=1)) as conn:
            existing_tables = {
                str(row[0])
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            required_columns = {
                "users": {"is_admin"},
                "rule_location_score": {
                    "quarter",
                    "score_version",
                    "current_location_score",
                    "context_location_score",
                    "grade",
                    "official_rank_eligible",
                    "available_axis_count",
                    "official_indicator_complete",
                    "data_reliability_score",
                },
                "rule_area_score_summary": {"quarter", "score_version"},
                "area_sale_price_proxy": {"period", "source_id", "proxy_score_allowed"},
                "area_rone_cost_reference": {
                    "period",
                    "direct_value_allowed",
                    "proxy_score_allowed",
                    "engine_promotion_ready",
                },
                "spatial_dataset_status": {"dataset_key", "record_count"},
            }
            available_columns: dict[str, set[str]] = {}
            for table, expected_columns in required_columns.items():
                if table not in existing_tables:
                    failures.append(f"missing_table:{table}")
                    continue
                columns = {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")')}
                available_columns[table] = columns
                missing_columns = sorted(expected_columns - columns)
                if missing_columns:
                    failures.append(f"missing_columns:{table}:{','.join(missing_columns)}")
            score_columns = available_columns.get("rule_location_score", set())
            if required_columns["rule_location_score"].issubset(score_columns):
                latest_quarter = str(
                    conn.execute("SELECT MAX(quarter) FROM rule_location_score").fetchone()[0] or ""
                )
                versions = {
                    str(row[0])
                    for row in conn.execute("SELECT DISTINCT score_version FROM rule_location_score")
                }
                counts["rule_location_score"] = int(
                    conn.execute("SELECT COUNT(*) FROM rule_location_score").fetchone()[0]
                )
                if expected_quarter and latest_quarter != expected_quarter:
                    failures.append(f"location_quarter={latest_quarter}/{expected_quarter}")
                if versions != {LOCATION_SCORE_VERSION}:
                    failures.append(f"location_score_versions={sorted(versions)}")
                unsafe_score_count = int(
                    conn.execute(
                        """
                        SELECT COUNT(*) FROM rule_location_score
                        WHERE
                            (COALESCE(official_rank_eligible, 0) = 0
                             AND (current_location_score IS NOT NULL OR grade IS NOT NULL))
                            OR (COALESCE(official_rank_eligible, 0) = 1
                                AND (current_location_score IS NULL OR grade IS NULL))
                            OR (data_reliability_score < 40
                                AND COALESCE(official_rank_eligible, 0) != 0)
                            OR (COALESCE(official_indicator_complete, 0) = 0
                                AND COALESCE(official_rank_eligible, 0) != 0)
                            OR (COALESCE(available_axis_count, 0) < 3
                                AND context_location_score IS NOT NULL)
                            OR (COALESCE(available_axis_count, 0) >= 3
                                AND context_location_score IS NULL)
                        """
                    ).fetchone()[0]
                )
                if unsafe_score_count:
                    failures.append(f"rule_score_fail_closed={unsafe_score_count}")
            area_columns = available_columns.get("rule_area_score_summary", set())
            if required_columns["rule_area_score_summary"].issubset(area_columns):
                area_quarter = str(
                    conn.execute("SELECT MAX(quarter) FROM rule_area_score_summary").fetchone()[0] or ""
                )
                area_versions = {
                    str(row[0])
                    for row in conn.execute(
                        "SELECT DISTINCT score_version FROM rule_area_score_summary"
                    )
                }
                if expected_quarter and area_quarter != expected_quarter:
                    failures.append(f"area_quarter={area_quarter}/{expected_quarter}")
                if area_versions != {AREA_SCORE_VERSION}:
                    failures.append(f"area_score_versions={sorted(area_versions)}")
            if required_columns["users"].issubset(available_columns.get("users", set())):
                counts["admin_users"] = int(
                    conn.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1").fetchone()[0]
                )
                if counts["admin_users"] <= 0:
                    failures.append("admin_users=0")
            if required_columns["area_rone_cost_reference"].issubset(
                available_columns.get("area_rone_cost_reference", set())
            ):
                unsafe_rone = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM area_rone_cost_reference "
                        "WHERE COALESCE(direct_value_allowed, 0) != 0 "
                        "OR COALESCE(proxy_score_allowed, 0) != 0 "
                        "OR COALESCE(engine_promotion_ready, 0) != 0"
                    ).fetchone()[0]
                )
                if unsafe_rone:
                    failures.append(f"rone_unsafe_contract_flags={unsafe_rone}")
            if required_columns["spatial_dataset_status"].issubset(
                available_columns.get("spatial_dataset_status", set())
            ):
                invalid_spatial = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM spatial_dataset_status WHERE record_count <= 0"
                    ).fetchone()[0]
                )
                if invalid_spatial:
                    failures.append(f"empty_spatial_dataset_status={invalid_spatial}")
        if not verified_refresh_job_id:
            failures.append("postcondition을 통과한 최신 제품 핵심 갱신 이력 없음")
        if failures:
            return {
                "status": "warning",
                "note": "; ".join(failures[:5]),
                "postcondition": None,
            }
        return {
            "status": "healthy",
            "note": f"제품 핵심 갱신 #{verified_refresh_job_id}의 최종 postcondition과 현재 핵심 DB 계약이 일치합니다.",
            "postcondition": {
                "verified_refresh_job_id": verified_refresh_job_id,
                "expected_quarter": expected_quarter,
                "counts": counts,
            },
        }
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        return {
            "status": "warning",
            "note": str(exc),
            "postcondition": None,
        }


def _database_contract_status(
    expected_quarter: str | None = None,
    verified_refresh_job_id: int | None = None,
) -> dict[str, Any]:
    if not DATABASE_PATH.exists():
        return {"status": "missing", "note": "제품 DB가 없습니다.", "postcondition": None}
    db_stat = DATABASE_PATH.stat()
    gold_sales = DATA_ROOT / "_gold" / "gold_sales_strength_q_industry.csv"
    gold_stat = gold_sales.stat() if gold_sales.exists() else None
    return _database_contract_snapshot(
        str(DATABASE_PATH.resolve()),
        db_stat.st_mtime_ns,
        db_stat.st_size,
        gold_stat.st_mtime_ns if gold_stat else 0,
        gold_stat.st_size if gold_stat else 0,
        expected_quarter,
        verified_refresh_job_id,
    )


def _latest_score_batch() -> dict[str, Any]:
    files = sorted(
        (DATA_ROOT / "_location_judgement_outputs").glob("loc_score_v2_batch_*.csv"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not files:
        return {
            "exists": False,
            "selected": False,
            "status": "missing",
            "name": None,
            "updated_at": None,
            "bytes": 0,
            "score_version": None,
            "gold_release_id": None,
            "reason": "점수 배치가 없습니다.",
        }

    current_gold_hash = _sha256_file(GOLD_MANIFEST_PATH) if GOLD_MANIFEST_PATH.exists() else None
    gold_summary = _read_json(DATA_ROOT / "_gold_validation" / "23_gold_preprocess_summary.json")
    expected_release = gold_summary.get("release_id") if isinstance(gold_summary, dict) else None
    try:
        expected_quarter = _expected_gold_quarter(DATA_ROOT)
    except RuntimeError:
        expected_quarter = None
    invalid_reasons: list[str] = []
    selected_path: Path | None = None
    selected_manifest: dict[str, Any] | None = None
    latest_manifest = _read_json(files[0].with_suffix(".manifest.json"))
    latest_manifest = latest_manifest if isinstance(latest_manifest, dict) else {}
    for path in files:
        manifest_path = path.with_suffix(".manifest.json")
        payload = _read_json(manifest_path)
        if not isinstance(payload, dict):
            invalid_reasons.append(f"{path.name}: manifest 없음")
            continue
        reasons: list[str] = []
        if payload.get("schema_version") != "localfit.score_batch_manifest.v1":
            reasons.append("manifest schema")
        if payload.get("score_version") != LOCATION_SCORE_VERSION:
            reasons.append(f"score_version={payload.get('score_version')}")
        if expected_quarter and str(payload.get("analysis_quarter") or "") != expected_quarter:
            reasons.append(f"quarter={payload.get('analysis_quarter')}/{expected_quarter}")
        if not current_gold_hash or payload.get("gold_manifest_sha256") != current_gold_hash:
            reasons.append("현재 Gold hash 불일치")
        if expected_release and payload.get("gold_release_id") != expected_release:
            reasons.append("현재 Gold release 불일치")
        declared_path = str(payload.get("batch_path") or "").replace("\\", "/")
        if declared_path and Path(declared_path).name != path.name:
            reasons.append("batch_path 불일치")
        if not reasons and payload.get("batch_sha256") != _sha256_file(path):
            reasons.append("batch sha256 불일치")
        if reasons:
            invalid_reasons.append(f"{path.name}: " + ", ".join(reasons))
            continue
        selected_path = path
        selected_manifest = payload
        break

    if not selected_path or not selected_manifest:
        latest = files[0]
        return {
            "exists": True,
            "selected": False,
            "status": "warning",
            "name": latest.name,
            "updated_at": _file_iso(latest),
            "bytes": latest.stat().st_size,
            "score_version": latest_manifest.get("score_version"),
            "gold_release_id": latest_manifest.get("gold_release_id"),
            "analysis_quarter": latest_manifest.get("analysis_quarter"),
            "reason": "; ".join(invalid_reasons[:2]) or "검증된 현재 점수 배치가 없습니다.",
        }
    return {
        "exists": True,
        "selected": True,
        "status": "healthy",
        "name": selected_path.name,
        "updated_at": _file_iso(selected_path),
        "bytes": selected_path.stat().st_size,
        "score_version": selected_manifest.get("score_version"),
        "gold_release_id": selected_manifest.get("gold_release_id"),
        "analysis_quarter": selected_manifest.get("analysis_quarter"),
        "reason": "정확한 v2.6 버전, 배치 hash, 현재 Gold release 계보를 검증했습니다.",
    }


def _core_raw_contract_status(sources: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {item.get("source_id"): item for item in sources}
    missing_registry = [source_id for source_id in CORE_PRODUCT_SOURCE_IDS if source_id not in by_id]
    missing_full = [
        source_id
        for source_id in CORE_PRODUCT_SOURCE_IDS
        if source_id in by_id and not by_id[source_id].get("last_full_collection_at")
    ]
    unhealthy = [
        source_id
        for source_id in CORE_PRODUCT_SOURCE_IDS
        if source_id in by_id and by_id[source_id].get("health") == "error"
    ]
    stale = []
    for source_id in CORE_PRODUCT_SOURCE_IDS:
        source = by_id.get(source_id, {})
        age = source.get("last_full_collection_age_hours")
        ttl = source.get("health_ttl_hours")
        if age is not None and ttl is not None and float(age) > float(ttl):
            stale.append(source_id)
    if missing_registry or missing_full:
        status = "warning"
        note = "핵심 원천 full collection 계보 미확인: " + ", ".join(missing_registry + missing_full)
    elif unhealthy:
        status = "error"
        note = "핵심 원천 수집 오류: " + ", ".join(unhealthy)
    elif stale:
        status = "warning"
        note = "핵심 원천 full collection TTL 초과: " + ", ".join(stale)
    else:
        status = "healthy"
        note = "서울 핵심 7개 원천의 full collection 계보를 확인했습니다."
    full_times = [
        str(by_id[source_id].get("last_full_collection_at"))
        for source_id in CORE_PRODUCT_SOURCE_IDS
        if source_id in by_id and by_id[source_id].get("last_full_collection_at")
    ]
    return {
        "status": status,
        "note": note,
        "count": len(CORE_PRODUCT_SOURCE_IDS) - len(missing_registry) - len(missing_full),
        "updated_at": min(full_times) if full_times else None,
    }


def admin_dashboard() -> dict[str, Any]:
    sources = source_statuses()
    raw_manifest = _read_csv(INGEST_MANIFEST_PATH)
    silver_state = _silver_contract_status()
    gold_state = _gold_release_status(silver_state)
    score = _latest_score_batch()
    database = _database_summary()
    active = active_job()
    recent_jobs = list_jobs(20)
    latest_data_check = _latest_job_for_key("status_check")
    latest_refresh_job = next(
        (job for job in recent_jobs if job.get("job_key") == "refresh_product_data"),
        None,
    )
    verified_refresh_job_id: int | None = None
    if latest_refresh_job and latest_refresh_job.get("status") == "success":
        try:
            # A successful core refresh already ran the final postcondition.  Recheck
            # the current DB contract below; dashboard-only code edits must not erase
            # otherwise valid lineage evidence.
            verified_refresh_job_id = int(latest_refresh_job["id"])
        except (TypeError, ValueError, KeyError):
            verified_refresh_job_id = None
    database_contract = _database_contract_status(
        str(score.get("analysis_quarter")) if score.get("analysis_quarter") else None,
        verified_refresh_job_id,
    )
    news_rows = _count_csv_rows(NEWS_EVIDENCE_PATH)
    core_pipeline_active = bool(active and active.get("job_key") == "refresh_product_data")
    lease_refresh_active = bool(active and active.get("job_key") == "reb_rent")
    full_refresh_active = bool(active and active.get("job_key") == "refresh_product_data")
    raw_state = _core_raw_contract_status(sources)
    external_sources = [
        item for item in sources if item.get("product_lineage_status") is not None
    ]
    # Show every external product lineage, including reviewed validation-only
    # chains that intentionally do not run inside the core product refresh.
    external_layer_sources = external_sources
    external_statuses = [str(item["product_lineage_status"]) for item in external_layer_sources]
    if any(value in {"error", "missing"} for value in external_statuses):
        external_status = "error"
    elif any(value == "warning" for value in external_statuses):
        external_status = "warning"
    elif any(value == "advisory" for value in external_statuses):
        external_status = "advisory"
    elif external_statuses:
        external_status = "healthy"
    else:
        external_status = "unknown"
    external_updated = max(
        (
            str(item["product_artifact_updated_at"])
            for item in external_layer_sources
            if item.get("product_artifact_updated_at")
        ),
        default=None,
    )
    external_ready_count = sum(
        item.get("product_lineage_status") == "healthy"
        for item in external_layer_sources
    )
    external_advisory_count = sum(
        item.get("product_lineage_status") == "advisory"
        for item in external_layer_sources
    )
    external_note = (
        f"검증된 계보 {external_ready_count}개, 핵심 자동 갱신 밖에서 별도 반영하는 계보 "
        f"{external_advisory_count}개입니다. 별도 반영은 연결 실패가 아니며 핵심 게시본을 막지 않습니다."
    )
    if lease_refresh_active:
        external_status = "warning"
        external_note = f"{active.get('current_step') or '서울 임대비용 기준 갱신'} 실행 중입니다. 완료 전에는 이전 게시본을 유지합니다."

    if full_refresh_active:
        raw_state = {
            **raw_state,
            "status": "warning",
            "note": f"{active.get('current_step') or '핵심 원천 수집'} 실행 중입니다. 완료 전에는 현재 계보로 판정하지 않습니다.",
        }
    if core_pipeline_active:
        active_note = f"{active.get('label')} 실행 중이므로 게시 전 상태입니다."
        silver_state = {**silver_state, "status": "warning", "note": active_note}
        gold_state = {**gold_state, "status": "warning", "note": active_note}
        score = {**score, "status": "warning", "reason": active_note}
        database_contract = {**database_contract, "status": "warning", "note": active_note}

    if gold_state["status"] != "healthy" and score["status"] == "healthy":
        score = {
            **score,
            "status": "warning",
            "reason": "점수 배치는 검증됐지만 현재 Gold가 최신 Silver와 일치하지 않습니다.",
        }
    database_status = _health_worst(database.get("status", "unknown"), database_contract["status"])
    if score["status"] != "healthy" or gold_state["status"] != "healthy":
        database_status = _health_worst(database_status, "warning")
    database.update(
        {
            "status": database_status,
            "contract_note": database_contract["note"],
            "postcondition": database_contract.get("postcondition"),
        }
    )
    credential_required = [
        item for item in sources if item["credential_status"] not in {"not_required", "optional", "unknown"}
    ]
    credential_configured = [item for item in credential_required if item["credential_status"] == "configured"]
    source_errors = [item for item in sources if item["health"] == "error"]
    source_warnings = [item for item in sources if item["health"] == "warning"]
    core_sources = [item for item in sources if item.get("source_id") in CORE_PRODUCT_SOURCE_IDS]
    data_period_starts = [item["last_data_period_start"] for item in core_sources if item.get("last_data_period_start")]
    data_period_ends = [item["last_data_period_end"] for item in core_sources if item.get("last_data_period_end")]
    validation_status = "healthy" if (
        EXECUTION_CONTRACT_PATH.exists()
        and gold_state["status"] == "healthy"
        and score["status"] == "healthy"
        and database["status"] == "healthy"
        and not core_pipeline_active
    ) else "warning"
    score_validation_layer = _score_validation_layer()
    layers = [
        {
            "key": "raw",
            "label": "핵심 원천 수집",
            "status": raw_state["status"],
            "count": raw_state["count"],
            "unit": f"/{len(CORE_PRODUCT_SOURCE_IDS)} full 계보",
            "updated_at": raw_state["updated_at"],
            "note": raw_state["note"],
            "data_period_start": min(data_period_starts) if data_period_starts else None,
            "data_period_end": max(data_period_ends) if data_period_ends else None,
            "job_key": None,
        },
        {
            "key": "external_lineage",
            "label": "외부·정적 제품 입력",
            "status": external_status,
            "count": external_ready_count,
            "unit": f"/{len(external_layer_sources)} 검토된 계보",
            "updated_at": external_updated,
            "note": external_note,
            "job_key": None,
        },
        {
            "key": "silver",
            "label": "Silver 전처리",
            "status": silver_state["status"],
            "count": silver_state["count"],
            "unit": "/8 핵심 파일",
            "updated_at": silver_state["updated_at"],
            "note": silver_state["note"],
            "job_key": "preprocess_core",
        },
        {
            "key": "gold",
            "label": "Gold 생성",
            "status": gold_state["status"],
            "count": gold_state["count"],
            "unit": "manifest 파일",
            "updated_at": gold_state["updated_at"],
            "note": gold_state["note"],
            "job_key": "build_gold",
        },
        {
            "key": "score",
            "label": "입지 점수",
            "status": score["status"],
            "count": 1 if score.get("selected") else 0,
            "unit": "검증 배치",
            "updated_at": score["updated_at"],
            "note": score["reason"],
            "job_key": "build_scores",
        },
        score_validation_layer,
        {
            "key": "validation",
            "label": "계약 검증",
            "status": validation_status,
            "count": len(_read_csv(EXECUTION_CONTRACT_PATH)),
            "unit": "원천 계약",
            "updated_at": _file_iso(EXECUTION_CONTRACT_PATH),
            "note": "Gold·v2.6 점수·제품 DB postcondition이 모두 현재 계보일 때만 정상입니다.",
            "job_key": "validate_pipeline",
        },
        {
            "key": "database",
            "label": "제품 DB",
            "status": database["status"],
            "count": database["table_counts"].get("rule_location_score", 0),
            "unit": "점수 행",
            "updated_at": database["updated_at"],
            "note": database_contract["note"],
            "job_key": "publish_database",
        },
    ]
    return {
        "generated_at": _now_iso(),
        "summary": {
            "source_count": len(sources),
            "healthy_source_count": sum(item["health"] == "healthy" for item in sources),
            "warning_source_count": len(source_warnings),
            "error_source_count": len(source_errors),
            "credential_configured": len(credential_configured),
            "credential_required": len(credential_required),
            "raw_manifest_rows": len(raw_manifest),
            "gold_file_count": gold_state["count"],
            "news_rows": news_rows,
            "product_quarter": database.get("quarter"),
            "raw_data_period_start": min(data_period_starts) if data_period_starts else None,
            "raw_data_period_end": max(data_period_ends) if data_period_ends else None,
        },
        "layers": layers,
        "sources": sources,
        "database": database,
        "score_batch": score,
        "news": {
            "rows": news_rows,
            "updated_at": _file_iso(NEWS_EVIDENCE_PATH),
            "status": "healthy" if news_rows else "missing",
        },
        "job_definitions": public_job_definitions(),
        "active_job": active,
        "latest_data_check": latest_data_check,
        "recent_jobs": recent_jobs,
    }


_init_job_store()
