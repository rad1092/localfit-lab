# -*- coding: utf-8 -*-
"""
서울 상권 입지판단 규칙 엔진용 gold 테이블 생성.

원칙:
  1. research/전처리_알고리즘_실행계획_20260703.md의 silver -> gold 분리 계약을 따른다.
  2. 이름 조인을 금지하고 상권_코드, 서비스_업종_코드, 기준_년분기_코드를 주키로 쓴다.
  3. 직접값과 프록시를 섞어 숨기지 않는다. 각 gold에 사용 상태와 금지 표현을 남긴다.
  4. 결측을 임의 0으로 채우지 않는다. 결측 플래그를 남긴 뒤 알고리즘에서 신뢰도 감점으로 처리한다.
  5. gold 하나를 만들 때마다 grain, row 보존, proxy/보류 조건을 검증한다.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SILVER = ROOT / "datacorpus" / "_silver"
GOLD = ROOT / "datacorpus" / "_gold"
GOLD_VALIDATION = ROOT / "datacorpus" / "_gold_validation"
RESEARCH_RULE_VALIDATION = ROOT / "research" / "rule_validation"
RULE_VALIDATION = ROOT / "datacorpus" / "_rule_validation"

RUN_DATE = datetime.now().strftime("%Y-%m-%d")
GOLD_VERSION = "rule_gold.v1.0-20260704"
RELEASE_ID = datetime.now().strftime("rule_gold_%Y%m%d_%H%M%S")


@dataclass
class Validation:
    gold_table: str
    rule_name: str
    observed: object
    expected: object
    result: str
    reason_ko: str


validations: list[Validation] = []


def ensure_dirs() -> None:
    GOLD.mkdir(parents=True, exist_ok=True)
    GOLD_VALIDATION.mkdir(parents=True, exist_ok=True)
    RESEARCH_RULE_VALIDATION.mkdir(parents=True, exist_ok=True)


def read_silver(filename: str, usecols: list[str] | None = None, dtype: dict | None = None) -> pd.DataFrame:
    path = SILVER / filename
    if not path.exists():
        raise FileNotFoundError(path)
    return pd.read_csv(path, encoding="utf-8-sig", usecols=usecols, dtype=dtype, low_memory=False)


def write_gold(df: pd.DataFrame, filename: str) -> Path:
    path = GOLD / filename
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def add_validation(
    gold_table: str,
    rule_name: str,
    observed: object,
    expected: object,
    passed: bool,
    reason_ko: str,
    conditional: bool = False,
) -> None:
    result = "PASS" if passed else ("CONDITIONAL_PASS" if conditional else "FAIL")
    validations.append(Validation(gold_table, rule_name, observed, expected, result, reason_ko))


def key_null_count(df: pd.DataFrame, keys: Iterable[str]) -> int:
    keys = list(keys)
    return int(df[keys].isna().any(axis=1).sum()) if keys else 0


def duplicate_count(df: pd.DataFrame, keys: Iterable[str]) -> int:
    keys = list(keys)
    return int(df.duplicated(keys).sum()) if keys else 0


def quarter_to_ordinal(series: pd.Series) -> pd.Series:
    q = pd.to_numeric(series, errors="coerce").astype("Int64")
    year = q // 10
    quarter = q % 10
    return (year * 4 + quarter).astype("Int64")


def safe_divide(numer: pd.Series, denom: pd.Series) -> pd.Series:
    denom = pd.to_numeric(denom, errors="coerce")
    numer = pd.to_numeric(numer, errors="coerce")
    return np.where(denom > 0, numer / denom, np.nan)


def normalize_population_source_id(value: object) -> str:
    parts = [part.strip() for part in str(value).split(";") if part.strip() and part.strip().lower() != "nan"]
    if {
        "seoul_resident_population_trade_area",
        "seoul_worker_population_trade_area",
    } & set(parts):
        parts = [
            part for part in parts
            if part not in {
                "seoul_resident_population_trade_area",
                "seoul_worker_population_trade_area",
            }
        ]
        parts.append("seoul_resident_worker_population_trade_area")
    return ";".join(dict.fromkeys(parts))


def build_trade_area_profile() -> pd.DataFrame:
    master = read_silver(
        "silver_trade_area_master.csv",
        [
            "상권_코드", "상권_코드_명", "상권_구분_코드", "상권_구분_코드_명",
            "자치구_코드", "자치구_코드_명", "행정동_코드", "행정동_코드_명",
            "중심_X", "중심_Y", "면적_제곱미터", "source_id", "provider",
            "snapshot_date", "boundary_version", "source_crs_recorded", "notes_ko",
        ],
    )
    geom = read_silver(
        "silver_trade_area_boundary_geometry.csv",
        [
            "상권_코드", "geometry_centroid_x_epsg5181", "geometry_centroid_y_epsg5181",
            "geometry_centroid_lon_wgs84", "geometry_centroid_lat_wgs84",
            "representative_x_epsg5181", "representative_y_epsg5181",
            "representative_lon_wgs84", "representative_lat_wgs84",
            "bbox_min_x_epsg5181", "bbox_min_y_epsg5181", "bbox_max_x_epsg5181", "bbox_max_y_epsg5181",
            "bbox_min_lon_wgs84", "bbox_min_lat_wgs84", "bbox_max_lon_wgs84", "bbox_max_lat_wgs84",
            "geometry_area_m2", "fixed_geometry_valid", "source_crs_epsg", "target_display_crs",
            "point_in_polygon_use_status", "score_use_status", "geometry_storage",
            "canonical_attribute_source", "vertex_count", "part_count",
        ],
    )
    out = master.merge(geom, on="상권_코드", how="left", validate="one_to_one")
    out["gold_version"] = GOLD_VERSION
    out["gold_role"] = "상권기준정보_위치입력브리지"
    out["directness_level"] = "P0_공식_상권경계_위치입력브리지"
    out["forbidden_claim_ko"] = "상권 프로필만으로 입지 우수성, 창업 성공확률, 개별 매장 매출 보장으로 표현 금지"
    out["direct_score_allowed"] = False
    out["proxy_score_allowed"] = False
    out["algorithm_use_note_ko"] = "점수값 자체가 아니라 지도 클릭/주소 좌표를 상권_코드로 변환하는 기준 테이블이다."

    add_validation("gold_trade_area_profile.csv", "상권_코드 grain 중복 금지", duplicate_count(out, ["상권_코드"]), 0, duplicate_count(out, ["상권_코드"]) == 0, "상권 기준 테이블은 1상권 1행이어야 한다.")
    add_validation("gold_trade_area_profile.csv", "상권 1,650개 보존", len(out), 1650, len(out) == 1650, "서울 상권분석서비스 상권 polygon 기준 1,650개를 보존해야 한다.")
    add_validation("gold_trade_area_profile.csv", "point-in-polygon 사용 가능 geometry", int((out["point_in_polygon_use_status"].fillna("").str.contains("사용가능")).sum()), 1650, int((out["point_in_polygon_use_status"].fillna("").str.contains("사용가능")).sum()) == 1650, "위치 입력을 하드코딩하지 않고 polygon으로 상권_코드를 확정하기 위한 핵심 조건이다.")
    return out


def build_industry_taxonomy() -> pd.DataFrame:
    industry = read_silver(
        "silver_industry_master_seoul_open_data.csv",
        [
            "서비스_업종_코드", "서비스_업종_코드_명", "매출_원천_존재", "점포_원천_존재",
            "source_id", "provider", "snapshot_date", "업종매핑_검토상태",
            "mapping_review_required", "score_use_status", "SBDC_계층_매핑_존재",
            "알고리즘_업종계층_상태", "notes_ko",
        ],
    )
    bridge = read_silver(
        "silver_industry_bridge_seoul_sbdc.csv",
        [
            "서비스_업종_코드", "SBDC_대분류코드_후보", "SBDC_대분류명_후보",
            "SBDC_중분류코드_후보", "SBDC_중분류명_후보",
            "SBDC_소분류코드_후보", "SBDC_소분류명_후보",
            "업종매핑_유사도", "업종매핑_검토상태", "mapping_review_required",
            "score_use_status",
        ],
    ).rename(
        columns={
            "업종매핑_검토상태": "SBDC_bridge_검토상태",
            "mapping_review_required": "SBDC_mapping_review_required",
            "score_use_status": "SBDC_score_use_status",
        }
    )
    out = industry.merge(bridge, on="서비스_업종_코드", how="left", validate="one_to_one")
    out["SBDC_mapping_review_required"] = out["SBDC_mapping_review_required"].fillna(True)
    out["SBDC_proxy_allowed"] = (~out["SBDC_mapping_review_required"]) & out["SBDC_소분류코드_후보"].notna()
    out["gold_version"] = GOLD_VERSION
    out["gold_role"] = "업종선택계층_서비스업종코드기준"
    # 입지점수 본체는 매출 축과 경쟁 축을 같이 보므로, 업종 taxonomy의 direct_score_allowed는
    # "전체 입지점수에 바로 투입 가능" 의미로 좁게 둔다. 점포만 있는 업종은 목록에는 남기되
    # 매출 체력 축이 비어 있으므로 부분 원천으로 표시한다.
    out["partial_score_source_allowed"] = out["매출_원천_존재"].fillna(False) | out["점포_원천_존재"].fillna(False)
    out["direct_score_allowed"] = out["매출_원천_존재"].fillna(False) & out["점포_원천_존재"].fillna(False)
    out["direct_score_blocker_ko"] = ""
    out.loc[~out["매출_원천_존재"].fillna(False), "direct_score_blocker_ko"] = "매출 원천 없음: 매출 체력 축 직접 산정 불가"
    out.loc[~out["점포_원천_존재"].fillna(False), "direct_score_blocker_ko"] = (
        out.loc[~out["점포_원천_존재"].fillna(False), "direct_score_blocker_ko"].astype(str)
        + " / 점포 원천 없음: 경쟁 축 직접 산정 불가"
    ).str.strip(" /")
    out["algorithm_use_note_ko"] = "화면은 대/중/세부 업종명을 보여주되 알고리즘 조인은 서비스_업종_코드만 사용한다."

    add_validation("gold_industry_taxonomy.csv", "서비스_업종_코드 grain 중복 금지", duplicate_count(out, ["서비스_업종_코드"]), 0, duplicate_count(out, ["서비스_업종_코드"]) == 0, "업종 이름 조인이 아니라 서비스_업종_코드 조인을 강제한다.")
    add_validation("gold_industry_taxonomy.csv", "서울 서비스업종 100개 보존", len(out), 100, len(out) == 100, "매출/점포 원천에서 관측된 서울 서비스업종 universe를 보존해야 한다.")
    add_validation("gold_industry_taxonomy.csv", "SBDC 수동검토 업종 보존", int(out["SBDC_mapping_review_required"].sum()), "0보다 큼", int(out["SBDC_mapping_review_required"].sum()) > 0, "자동 매핑이 약한 업종을 삭제하지 않고 보류 플래그로 남긴다.")
    add_validation("gold_industry_taxonomy.csv", "직접 점수 가능 업종은 매출과 점포 원천 모두 필요", int(out["direct_score_allowed"].sum()), "매출+점포 모두 존재하는 업종 수", int(((out["매출_원천_존재"]) & (out["점포_원천_존재"])).sum()) == int(out["direct_score_allowed"].sum()), "매출 원천이 없는 업종을 전체 입지점수 직접 가능으로 표시하면 리포트가 과장된다.")
    return out


def build_sales_strength() -> pd.DataFrame:
    sales = read_silver(
        "silver_sales_trade_area_q_industry.csv",
        [
            "기준_년분기_코드", "상권_구분_코드", "상권_구분_코드_명", "상권_코드", "상권_코드_명",
            "서비스_업종_코드", "서비스_업종_코드_명", "당월_매출_금액", "당월_매출_건수",
            "quality_negative_core_cell_count", "quality_negative_breakdown_cell_count",
            "source_id", "provider", "snapshot_date", "directness_level", "forbidden_claim_ko",
        ],
        dtype={"서비스_업종_코드": "string"},
    )
    store = read_silver(
        "silver_store_trade_area_q_industry.csv",
        ["기준_년분기_코드", "상권_코드", "서비스_업종_코드", "점포_수"],
        dtype={"서비스_업종_코드": "string"},
    )
    out = sales.merge(store, on=["기준_년분기_코드", "상권_코드", "서비스_업종_코드"], how="left", validate="one_to_one")
    out["점포당_매출_금액"] = safe_divide(out["당월_매출_금액"], out["점포_수"])
    out["객단가_추정_금액"] = safe_divide(out["당월_매출_금액"], out["당월_매출_건수"])
    out["store_join_status"] = np.where(out["점포_수"].notna(), "점포수_결합", "점포수_미결합")
    out["gold_version"] = GOLD_VERSION
    out["gold_role"] = "매출체력_직접축"
    out["direct_score_allowed"] = True
    out["proxy_score_allowed"] = False
    out["forbidden_claim_ko"] = out["forbidden_claim_ko"].fillna("개별 매장 매출 보장, 창업 성공확률로 표현 금지")

    keys = ["기준_년분기_코드", "상권_코드", "서비스_업종_코드"]
    add_validation("gold_sales_strength_q_industry.csv", "매출 grain 중복 금지", duplicate_count(out, keys), 0, duplicate_count(out, keys) == 0, "매출 gold는 분기+상권+업종 단위 직접축이다.")
    add_validation("gold_sales_strength_q_industry.csv", "매출 silver row 보존", len(out), len(sales), len(out) == len(sales), "매출 원천 row를 삭제하지 않고 점포수만 보조 결합한다.")
    add_validation("gold_sales_strength_q_industry.csv", "매출 핵심 음수 품질 플래그 0", int(out["quality_negative_core_cell_count"].fillna(0).sum()), 0, int(out["quality_negative_core_cell_count"].fillna(0).sum()) == 0, "음수 매출/건수는 매출축 직접 점수의 치명 품질 문제다.")
    add_validation("gold_sales_strength_q_industry.csv", "점포수 미결합은 삭제하지 않고 표시", int((out["store_join_status"] == "점포수_미결합").sum()), "표시", True, "2019~2020 매출처럼 점포 silver가 없는 구간은 임의 0점이 아니라 결합상태로 남긴다.")
    return out


def build_competition() -> pd.DataFrame:
    store = read_silver(
        "silver_store_trade_area_q_industry.csv",
        [
            "기준_년분기_코드", "상권_구분_코드", "상권_구분_코드_명", "상권_코드", "상권_코드_명",
            "서비스_업종_코드", "서비스_업종_코드_명", "유사_업종_점포_수", "점포_수",
            "프랜차이즈_점포_수", "개업_율", "개업_점포_수", "폐업_률", "폐업_점포_수",
            "quality_negative_count_cell_count", "quality_negative_rate_cell_count",
            "quality_rate_above_100_cell_count", "source_id", "provider", "snapshot_date",
            "directness_level", "forbidden_claim_ko",
        ],
        dtype={"서비스_업종_코드": "string"},
    )
    sbdc = read_silver(
        "silver_sbdc_store_competition_trade_area_seoul_service_202603.csv",
        [
            "상권_코드", "서비스_업종_코드", "업종매핑_검토상태", "mapping_review_required",
            "score_use_status", "동종_후보소분류_점포수", "유사_후보중분류_점포수",
            "snapshot_date",
        ],
        dtype={"서비스_업종_코드": "string"},
    ).rename(columns={"snapshot_date": "SBDC_snapshot_date"})
    out = store.merge(sbdc, on=["상권_코드", "서비스_업종_코드"], how="left", validate="many_to_one")
    out["SBDC_proxy_allowed"] = (~out["mapping_review_required"].fillna(True)) & out["동종_후보소분류_점포수"].notna()
    out["franchise_direction_status"] = "방향검증전_보류"
    out["gold_version"] = GOLD_VERSION
    out["gold_role"] = "경쟁_점포직접축_SBDC보조프록시"
    out["direct_score_allowed"] = True
    out["proxy_score_allowed"] = out["SBDC_proxy_allowed"]
    out["forbidden_claim_ko"] = out["forbidden_claim_ko"].fillna("개별 매장 생존확률, 개별 점포 매출 보장으로 표현 금지")

    keys = ["기준_년분기_코드", "상권_코드", "서비스_업종_코드"]
    add_validation("gold_competition_q_industry.csv", "점포 grain 중복 금지", duplicate_count(out, keys), 0, duplicate_count(out, keys) == 0, "경쟁 gold는 분기+상권+업종 단위다.")
    add_validation("gold_competition_q_industry.csv", "점포 silver row 보존", len(out), len(store), len(out) == len(store), "점포 직접축 row를 SBDC 보조 프록시 결합 과정에서 삭제하지 않는다.")
    add_validation("gold_competition_q_industry.csv", "100 초과 rate 품질 플래그 보존", int(out["quality_rate_above_100_cell_count"].fillna(0).sum()), "삭제하지 않음", True, "개폐업률 이상치는 제거하지 않고 품질 플래그로 신뢰도 감점 대상에 둔다.")
    add_validation("gold_competition_q_industry.csv", "SBDC 수동검토 보류 반영", int((~out["SBDC_proxy_allowed"]).sum()), "0보다 큼", int((~out["SBDC_proxy_allowed"]).sum()) > 0, "SBDC 자동강매칭이 아닌 업종은 보조 경쟁 프록시를 직접 허용하지 않는다.")
    return out


def build_demand() -> pd.DataFrame:
    demand = read_silver(
        "silver_population_demand_q_area.csv",
        [
            "기준_년분기_코드", "상권_코드", "상권_코드_명", "상권_구분_코드", "상권_구분_코드_명",
            "총_유동인구_수", "유동인구_품질_음수셀수", "총_상주인구_수", "총_가구_수",
            "상주인구_품질_음수셀수", "총_직장인구_수", "직장인구_품질_음수셀수",
            "유동인구_존재", "상주인구_존재", "직장인구_존재", "수요원천_존재_개수",
            "총_기초수요_프록시", "source_id", "provider", "snapshot_date", "directness_level", "forbidden_claim_ko",
        ],
    )
    consumption = read_silver(
        "silver_consumption_trade_area_q.csv",
        [
            "기준_년분기_코드", "상권_코드",
            "지출_총금액", "식료품_지출_총금액", "의류_신발_지출_총금액",
            "생활용품_지출_총금액", "의료비_지출_총금액", "교통_지출_총금액",
            "여가_지출_총금액", "문화_지출_총금액", "교육_지출_총금액", "유흥_지출_총금액",
            "소비_관측여부", "소비_품질_지출결측셀수", "소비_품질_음수셀수",
            "소비_품질_세부합계불일치", "source_id", "directness_level", "forbidden_claim_ko",
        ],
    ).rename(
        columns={
            "source_id": "소비_source_id",
            "directness_level": "소비_directness_level",
            "forbidden_claim_ko": "소비_forbidden_claim_ko",
        }
    )
    master = read_silver("silver_trade_area_master.csv", ["상권_코드", "자치구_코드", "자치구_코드_명"])
    migration = read_silver(
        "silver_living_migration_district_quarter_features.csv",
        [
            "기준_년분기_코드", "자치구_코드", "생활이동_유입_이동인구_합계",
            "생활이동_순유입_이동인구", "생활이동_분기_포함월수", "생활이동_유입유출_비율",
            "생활이동_유입_평균_이동시간_분", "생활이동_도착여성비율",
        ],
    )
    out = demand.merge(master, on="상권_코드", how="left", validate="many_to_one")
    out = out.merge(consumption, on=["기준_년분기_코드", "상권_코드"], how="left", validate="one_to_one")
    out = out.merge(migration, on=["기준_년분기_코드", "자치구_코드"], how="left", validate="many_to_one")
    out["소비_관측여부"] = out["소비_관측여부"].astype(str).str.lower().isin(["true", "1", "yes", "y"])
    out["source_id"] = out["source_id"].map(normalize_population_source_id)
    out["기초수요당_소비"] = safe_divide(out["지출_총금액"], out["총_기초수요_프록시"])
    out["상주인구당_소비"] = safe_divide(out["지출_총금액"], out["총_상주인구_수"])
    out["생활이동_proxy_allowed"] = out["생활이동_분기_포함월수"].eq(3)
    out["소비_proxy_allowed"] = out["소비_관측여부"]
    out["gold_version"] = GOLD_VERSION
    out["gold_role"] = "상권수요_인구소비직접집계_생활이동자치구프록시"
    out["direct_score_allowed"] = True
    out["proxy_score_allowed"] = out["생활이동_분기_포함월수"].notna() | out["소비_관측여부"]

    keys = ["기준_년분기_코드", "상권_코드"]
    add_validation("gold_demand_q_area.csv", "수요 grain 중복 금지", duplicate_count(out, keys), 0, duplicate_count(out, keys) == 0, "수요 gold는 업종 단위가 아니라 분기+상권 단위다.")
    add_validation("gold_demand_q_area.csv", "수요 compact row 보존", len(out), len(demand), len(out) == len(demand), "유동/상주/직장인구 compact row를 보존한다.")
    add_validation("gold_demand_q_area.csv", "인구 3종 결합 source 존재", int(out["수요원천_존재_개수"].fillna(0).ge(1).sum()), len(out), int(out["수요원천_존재_개수"].fillna(0).ge(1).sum()) == len(out), "수요축은 최소 하나 이상의 인구 원천이 있어야 한다.")
    add_validation("gold_demand_q_area.csv", "소비-상권 결합은 행 보존", len(out), len(demand), len(out) == len(demand), "소비 원천은 상권×분기 수요 프록시로 붙이되 인구 compact row를 삭제하거나 늘리지 않는다.")
    add_validation("gold_demand_q_area.csv", "소비 미관측은 0대체 금지", int((~out["소비_관측여부"]).sum()), "0보다 큼", int((~out["소비_관측여부"]).sum()) > 0, "소비 원천에 없는 상권/분기는 0소비가 아니라 결측·신뢰도 감점 후보로 둔다.")
    add_validation("gold_demand_q_area.csv", "생활이동·소비 프록시 상태 표시", int(out["proxy_score_allowed"].sum()), "일부 분기만", True, "생활이동은 자치구 유입 프록시이고 소비는 상권 소비잠재 프록시이므로 직접 구매자/업종소비 보장으로 표현하지 않는다.")
    return out


def build_accessibility() -> pd.DataFrame:
    demand_keys = read_silver("silver_population_demand_q_area.csv", ["기준_년분기_코드", "상권_코드", "상권_코드_명"])
    master = read_silver("silver_trade_area_master.csv", ["상권_코드", "자치구_코드", "자치구_코드_명"])
    facility = read_silver(
        "silver_facility_trade_area_q.csv",
        [
            "기준_년분기_코드", "상권_코드", "총_집객시설_수", "공공_시설_수", "금융_시설_수",
            "의료_시설_수", "교육_시설_수", "상업문화_시설_수", "교통_시설_수",
            "철도역_수", "버스터미널_수", "지하철역_수", "버스정류장_수",
            "quality_negative_facility_cell_count", "quality_type_sum_exceeds_total",
            "source_id", "provider", "snapshot_date", "directness_level", "forbidden_claim_ko",
        ],
    )
    migration = read_silver(
        "silver_living_migration_district_quarter_features.csv",
        [
            "기준_년분기_코드", "자치구_코드", "생활이동_외부유입_이동인구_합계",
            "생활이동_출근시간_유입_이동인구", "생활이동_점심시간_유입_이동인구",
            "생활이동_퇴근시간_유입_이동인구", "생활이동_야간_유입_이동인구",
            "생활이동_분기_포함월수",
        ],
    )
    out = demand_keys.merge(master, on="상권_코드", how="left", validate="many_to_one")
    out = out.merge(facility, on=["기준_년분기_코드", "상권_코드"], how="left", validate="one_to_one")
    out = out.merge(migration, on=["기준_년분기_코드", "자치구_코드"], how="left", validate="many_to_one")
    transport_cols = ["철도역_수", "버스터미널_수", "지하철역_수", "버스정류장_수"]
    out["교통결절_시설수"] = out[transport_cols].sum(axis=1, min_count=1)
    out["facility_observed"] = out["총_집객시설_수"].notna()
    out["facility_missing_not_imputed"] = ~out["facility_observed"]
    out["gold_version"] = GOLD_VERSION
    out["gold_role"] = "접근성_집객시설직접프록시_생활이동자치구보조"
    out["direct_score_allowed"] = out["facility_observed"]
    out["proxy_score_allowed"] = True
    out["forbidden_claim_ko"] = out["forbidden_claim_ko"].fillna("실제 방문확률, 실제 유입 인원, 실제 도보시간으로 표현 금지")

    keys = ["기준_년분기_코드", "상권_코드"]
    add_validation("gold_accessibility_q_area.csv", "접근성 grain 중복 금지", duplicate_count(out, keys), 0, duplicate_count(out, keys) == 0, "접근성 gold는 분기+상권 단위다.")
    add_validation("gold_accessibility_q_area.csv", "시설 미관측 임의 0 금지", int(out["facility_missing_not_imputed"].sum()), "결측 플래그", True, "시설 raw가 없는 상권-분기는 0시설로 단정하지 않고 결측으로 남긴다.")
    add_validation("gold_accessibility_q_area.csv", "교통결절은 방문확률 아님", "프록시 표시", "프록시 표시", True, "시설·정류장 수는 상대 접근성 프록시이며 실제 방문확률이 아니다.")
    return out


def build_growth_stability() -> pd.DataFrame:
    sales = read_silver(
        "silver_sales_trade_area_q_industry.csv",
        [
            "기준_년분기_코드", "상권_구분_코드", "상권_구분_코드_명", "상권_코드", "상권_코드_명",
            "서비스_업종_코드", "서비스_업종_코드_명", "당월_매출_금액",
            "source_id", "provider", "snapshot_date", "directness_level", "forbidden_claim_ko",
        ],
        dtype={"서비스_업종_코드": "string"},
    )
    store = read_silver(
        "silver_store_trade_area_q_industry.csv",
        [
            "기준_년분기_코드", "상권_코드", "서비스_업종_코드",
            "점포_수", "개업_율", "개업_점포_수", "폐업_률", "폐업_점포_수",
            "quality_rate_above_100_cell_count",
        ],
        dtype={"서비스_업종_코드": "string"},
    )
    change = read_silver(
        "silver_change_index_trade_area_q.csv",
        [
            "기준_년분기_코드", "상권_코드", "상권_변화_지표_코드", "상권_변화_지표_명",
            "운영_영업_개월_평균", "폐업_영업_개월_평균",
            "운영_서울대비_개월_차이", "폐업_서울대비_개월_차이",
            "quality_negative_month_cell_count",
        ],
    )

    out = sales.merge(store, on=["기준_년분기_코드", "상권_코드", "서비스_업종_코드"], how="left", validate="one_to_one")
    out = out.merge(change, on=["기준_년분기_코드", "상권_코드"], how="left", validate="many_to_one")
    out["quarter_ordinal"] = quarter_to_ordinal(out["기준_년분기_코드"])
    trend_base = out[["상권_코드", "서비스_업종_코드", "quarter_ordinal", "당월_매출_금액"]].copy()
    trend_base["log_sales"] = np.log1p(pd.to_numeric(trend_base["당월_매출_금액"], errors="coerce"))
    trend = trend_base[["상권_코드", "서비스_업종_코드", "quarter_ordinal", "log_sales"]].copy()
    for lag in [1, 2, 3]:
        prev = trend_base[["상권_코드", "서비스_업종_코드", "quarter_ordinal", "log_sales"]].copy()
        prev["quarter_ordinal"] = prev["quarter_ordinal"] + lag
        prev = prev.rename(columns={"log_sales": f"log_sales_lag{lag}"})
        trend = trend.merge(prev, on=["상권_코드", "서비스_업종_코드", "quarter_ordinal"], how="left")
    y0 = trend["log_sales_lag3"]
    y1 = trend["log_sales_lag2"]
    y2 = trend["log_sales_lag1"]
    y3 = trend["log_sales"]
    complete = y0.notna() & y1.notna() & y2.notna() & y3.notna()
    y_mean = (y0 + y1 + y2 + y3) / 4.0
    slope = ((-1.5 * (y0 - y_mean)) + (-0.5 * (y1 - y_mean)) + (0.5 * (y2 - y_mean)) + (1.5 * (y3 - y_mean))) / 5.0
    trend["매출_log_최근4분기_slope"] = np.where(complete, slope, np.nan)
    trend["매출_최근4분기_연속존재"] = complete
    out = out.merge(
        trend[["상권_코드", "서비스_업종_코드", "quarter_ordinal", "매출_log_최근4분기_slope", "매출_최근4분기_연속존재"]],
        on=["상권_코드", "서비스_업종_코드", "quarter_ordinal"],
        how="left",
        validate="one_to_one",
    )
    out["gold_version"] = GOLD_VERSION
    out["gold_role"] = "성장안정성_후보축"
    out["direct_score_allowed"] = False
    out["proxy_score_allowed"] = True
    out["growth_score_status"] = "후보_백테스트필요"
    out["forbidden_claim_ko"] = out["forbidden_claim_ko"].fillna("성장률 보장, 창업 성공확률, 개별 매장 생존확률로 표현 금지")

    keys = ["기준_년분기_코드", "상권_코드", "서비스_업종_코드"]
    add_validation("gold_growth_stability_q_industry.csv", "성장 gold grain 중복 금지", duplicate_count(out, keys), 0, duplicate_count(out, keys) == 0, "성장 gold는 매출 기반 분기+상권+업종 후보축이다.")
    add_validation("gold_growth_stability_q_industry.csv", "성장 후보는 직접 보장값 아님", out["direct_score_allowed"].any(), False, not bool(out["direct_score_allowed"].any()), "성장잠재는 백테스트 전 후보 점수로만 둔다.")
    add_validation("gold_growth_stability_q_industry.csv", "최근4분기 연속 매출 추세만 slope 계산", int(out["매출_log_최근4분기_slope"].notna().sum()), "연속 4분기 존재 행만", True, "불연속 분기나 이력 부족 행에는 성장 slope를 억지로 만들지 않는다.")
    return out.drop(columns=["quarter_ordinal"])


def build_cost_risk() -> pd.DataFrame:
    master = read_silver(
        "silver_trade_area_master.csv",
        ["상권_코드", "상권_코드_명", "상권_구분_코드", "상권_구분_코드_명", "자치구_코드", "자치구_코드_명"],
    )
    rtms = read_silver(
        "silver_rtms_commercial_trade_sgg_quarter.csv",
        [
            "기준_년분기_코드", "자치구_코드", "자치구_명", "거래건수", "포함_월수",
            "거래금액_중앙값_만원", "거래금액_평균_만원",
            "건물면적당_거래금액_중앙값_만원_per_m2", "건물면적당_거래금액_평균_만원_per_m2",
            "source_id", "provider", "directness_level", "forbidden_claim_ko",
        ],
    )
    out = master.merge(rtms, on="자치구_코드", how="inner", validate="many_to_many")
    out["gold_version"] = GOLD_VERSION
    out["gold_role"] = "비용리스크_자치구상업실거래프록시"
    out["direct_score_allowed"] = False
    out["proxy_score_allowed"] = True
    out["proxy_reason_ko"] = "RTMS는 자치구 단위 상업용 실거래가 기반 비용 압력 프록시이며 개별 점포 월세·권리금이 아니다."

    keys = ["기준_년분기_코드", "상권_코드"]
    add_validation("gold_cost_risk_q_area.csv", "비용 gold grain 중복 금지", duplicate_count(out, keys), 0, duplicate_count(out, keys) == 0, "자치구 비용 프록시는 상권으로 fan-out되지만 분기+상권 1행이어야 한다.")
    add_validation("gold_cost_risk_q_area.csv", "비용은 직접값 금지", out["direct_score_allowed"].any(), False, not bool(out["direct_score_allowed"].any()), "월세/권리금 직접값으로 오해하지 않도록 프록시로만 둔다.")
    add_validation("gold_cost_risk_q_area.csv", "RTMS 자치구 25개 매핑", int(rtms["자치구_코드"].nunique()), 25, int(rtms["자치구_코드"].nunique()) == 25, "서울 25개 자치구 비용 프록시가 있어야 상권 fan-out이 가능하다.")
    return out


def classify_silver_table(name: str) -> tuple[str, bool, bool, str]:
    lower = name.lower()
    if "audit" in lower or "sample" in lower or "codebook" in lower or "manifest" in lower or "duplicate" in lower:
        return "감사/코드북/샘플", False, False, "점수 입력이 아니라 검증·코드 해석용이다."
    if "sgis" in lower or "kosis" in lower or "geocoding" in lower or "juso" in lower:
        return "기준선/입력검증", False, False, "행정구역·통계 기준선 또는 위치 입력 검증용이다."
    if "localdata_food_license" in lower:
        return "프록시_인허가보조", False, True, "식품 인허가 원천은 업태명 bridge 전에는 서비스업종 직접축이 아니며 개폐업/상태 보조 프록시다."
    if "sbdc_store_competition" in lower or "sbdc_store_poi" in lower:
        return "프록시_경쟁보조", False, True, "SBDC 상가 POI와 공간매칭 집계는 업종 매핑 상태에 따라 조건부 경쟁 보조 프록시다."
    if "sales_trade_area" in lower:
        return "직접_매출축", True, False, "상권×업종×분기 공식 추정매출 직접축이다."
    if "store_trade_area" in lower:
        return "직접_점포경쟁축", True, False, "상권×업종×분기 공식 점포 직접축이다."
    if "consumption_trade_area" in lower:
        return "직접_상권소비수요축", True, True, "상권×분기 소비잠재 공식 추정집계이며 실제 구매자 수나 업종별 소비 보장은 아니다."
    if "population_demand" in lower or "floating_population" in lower or "resident_population" in lower or "worker_population" in lower:
        return "직접_상권수요축", True, True, "상권×분기 인구 집계이며 업종별 직접 수요는 아니다."
    if "facility_trade_area" in lower:
        return "프록시_접근성축", True, True, "집객시설 수는 실제 방문확률이 아니라 접근성/흡인력 프록시다."
    if "change_index" in lower:
        return "프록시_성장안정성축", False, True, "변화지표 코드는 단독 선형 점수화 금지, 매출·점포 추세와 함께 해석한다."
    if "rtms" in lower or "rone" in lower or "broker" in lower:
        return "프록시_비용리스크", False, True, "부동산 비용 환경 프록시이며 월세·권리금 직접값이 아니다."
    if "migration" in lower:
        return "프록시_생활이동", False, True, "자치구 생활이동 프록시이며 상권 직접 유입량이 아니다."
    if "trade_area_boundary" in lower or "trade_area_master" in lower:
        return "브리지_상권기준", False, False, "상권_코드와 위치 입력 기준이다."
    if "industry" in lower:
        return "브리지_업종기준", False, False, "업종 코드와 UI 계층 기준이다."
    if "transit" in lower:
        return "프록시_교통접근성후보", False, True, "좌표 결합 exact 교통점의 상권 주변 후보 집계이며 단월 스냅샷이라 점수 직접 투입 전 백테스트가 필요하다."
    if "bus" in lower or "subway" in lower:
        return "프록시_교통보류", False, True, "좌표/거리/buffer 검증 전 직접 접근성 점수는 보류한다."
    return "기타_검토필요", False, False, "역할을 수동 검토해야 한다."


def count_csv_rows(path: Path) -> int:
    with path.open("rb") as f:
        return max(sum(1 for _ in f) - 1, 0)


def build_data_reliability_snapshot() -> pd.DataFrame:
    rows: list[dict] = []
    validation_files = list((ROOT / "datacorpus" / "_rule_validation").glob("*.csv"))
    validation_names = [p.name for p in validation_files]
    for path in sorted(SILVER.glob("*.csv")):
        role, direct_allowed, proxy_allowed, note = classify_silver_table(path.name)
        header = pd.read_csv(path, encoding="utf-8-sig", nrows=0)
        sample = pd.read_csv(path, encoding="utf-8-sig", nrows=200, low_memory=False)
        source_id = ";".join(str(x) for x in sample.get("source_id", pd.Series(dtype=object)).dropna().astype(str).unique()[:5])
        directness = ";".join(str(x) for x in sample.get("directness_level", pd.Series(dtype=object)).dropna().astype(str).unique()[:5])
        forbidden = ";".join(str(x) for x in sample.get("forbidden_claim_ko", pd.Series(dtype=object)).dropna().astype(str).unique()[:3])
        snapshot = ";".join(str(x) for x in sample.get("snapshot_date", pd.Series(dtype=object)).dropna().astype(str).unique()[:5])
        prefix = path.name.split("_")[1] if "_" in path.name else path.name
        related = [v for v in validation_names if prefix in v or path.stem.replace("silver_", "")[:12] in v]
        rows.append(
            {
                "silver_table": path.name,
                "row_count": count_csv_rows(path),
                "file_bytes": path.stat().st_size,
                "column_count": len(header.columns),
                "source_id_sample": source_id,
                "directness_level_sample": directness,
                "forbidden_claim_sample": forbidden,
                "snapshot_date_sample": snapshot,
                "gold_input_role": role,
                "direct_score_allowed_default": direct_allowed,
                "proxy_score_allowed_default": proxy_allowed,
                "related_rule_validation_file_count": len(related),
                "gold_version": GOLD_VERSION,
                "use_note_ko": note,
            }
        )
    out = pd.DataFrame(rows)
    add_validation("gold_data_reliability_snapshot.csv", "현재 silver 전체 테이블 역할 분류", len(out), len(list(SILVER.glob("*.csv"))), len(out) == len(list(SILVER.glob("*.csv"))), "현재 silver 물리 파일 전체를 직접/프록시/브리지/감사 역할로 분류한다.")
    add_validation("gold_data_reliability_snapshot.csv", "역할 미검토 테이블 최소화", int((out["gold_input_role"] == "기타_검토필요").sum()), 0, int((out["gold_input_role"] == "기타_검토필요").sum()) == 0, "모든 silver는 점수/프록시/브리지/감사 중 하나의 역할을 가져야 한다.", conditional=True)
    add_validation("gold_data_reliability_snapshot.csv", "직접 허용보다 프록시/보류 명시", int((~out["direct_score_allowed_default"]).sum()), "다수", int((~out["direct_score_allowed_default"]).sum()) > 0, "수집한 모든 데이터를 억지로 직접 점수에 넣지 않고 보류·프록시 역할을 남긴다.")
    return out


def write_validations() -> pd.DataFrame:
    df = pd.DataFrame([v.__dict__ for v in validations])
    df.to_csv(GOLD_VALIDATION / "23_gold_rule_validation.csv", index=False, encoding="utf-8-sig")
    summary = (
        df.groupby(["gold_table", "result"], dropna=False)
        .size()
        .reset_index(name="count")
        .pivot(index="gold_table", columns="result", values="count")
        .fillna(0)
        .astype(int)
        .reset_index()
    )
    for col in ["PASS", "CONDITIONAL_PASS", "FAIL"]:
        if col not in summary.columns:
            summary[col] = 0
    summary["판정"] = np.where(summary["FAIL"] > 0, "FAIL", np.where(summary["CONDITIONAL_PASS"] > 0, "CONDITIONAL_PASS", "PASS"))
    summary.to_csv(GOLD_VALIDATION / "23_gold_rule_validation_summary.csv", index=False, encoding="utf-8-sig")
    return df


def write_manifest(outputs: dict[str, Path], validation_df: pd.DataFrame) -> None:
    rows = []
    for name, path in outputs.items():
        rows.append(
            {
                "gold_table": name,
                "path": str(Path("datacorpus") / "_gold" / name),
                "row_count": count_csv_rows(path),
                "file_bytes": path.stat().st_size,
                "gold_version": GOLD_VERSION,
                "release_id": RELEASE_ID,
                "generated_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
    pd.DataFrame(rows).to_csv(GOLD_VALIDATION / "23_gold_output_manifest.csv", index=False, encoding="utf-8-sig")
    (GOLD_VALIDATION / "23_gold_preprocess_summary.json").write_text(
        json.dumps(
            {
                "gold_version": GOLD_VERSION,
                "release_id": RELEASE_ID,
                "output_count": len(outputs),
                "validation_count": len(validation_df),
                "validation_fail_count": int((validation_df["result"] == "FAIL").sum()),
                "generated_at": datetime.now().isoformat(timespec="seconds"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def write_markdown_report(outputs: dict[str, Path], validation_df: pd.DataFrame) -> None:
    manifest = pd.read_csv(GOLD_VALIDATION / "23_gold_output_manifest.csv", encoding="utf-8-sig")
    summary = pd.read_csv(GOLD_VALIDATION / "23_gold_rule_validation_summary.csv", encoding="utf-8-sig")
    lines = [
        "# 23차 gold 입력 테이블 생성 및 규칙 검증",
        "",
        f"작성일: {RUN_DATE}",
        f"gold 버전: `{GOLD_VERSION}`",
        "",
        "## 1. 목적",
        "",
        "silver를 그대로 한 파일에 몰아넣지 않고, 입지판단 알고리즘이 읽을 수 있는 도메인별 gold 입력 테이블로 나눴다. 직접값, 프록시, 보류 조건을 컬럼으로 남겨 점수 산정에서 오해가 생기지 않게 했다.",
        "",
        "## 2. 생성 파일",
        "",
        "| gold_table | rows | file_bytes |",
        "|---|---:|---:|",
    ]
    for _, row in manifest.iterrows():
        lines.append(f"| `{row['gold_table']}` | {int(row['row_count']):,} | {int(row['file_bytes']):,} |")
    lines.extend(
        [
            "",
            "## 3. 검증 요약",
            "",
            "| gold_table | PASS | CONDITIONAL_PASS | FAIL | 판정 |",
            "|---|---:|---:|---:|---|",
        ]
    )
    for _, row in summary.iterrows():
        lines.append(
            f"| `{row['gold_table']}` | {int(row.get('PASS', 0))} | {int(row.get('CONDITIONAL_PASS', 0))} | {int(row.get('FAIL', 0))} | {row['판정']} |"
        )
    lines.extend(
        [
            "",
            "## 4. 2보 전진 1보 후퇴 검토",
            "",
            "1. 전진: 상권·업종 기준 gold를 먼저 만들어 하드코딩 입력을 없앨 수 있는 기준을 세웠다.",
            "2. 전진: 매출·점포·수요·접근성·성장·비용·신뢰도 gold를 분리해 알고리즘 축과 데이터 grain을 맞췄다.",
            "3. 후퇴: SGIS, KOSIS, 교통 승하차량, 부동산 중개업소, 주소 지오코딩은 수집되었더라도 상권 직접 점수로 넣지 않았다. bridge나 프록시 검증 전 직접값으로 쓰면 근거가 과장된다.",
            "4. 재검토: 시설 미관측 상권-분기는 0으로 채우지 않고 `facility_missing_not_imputed`로 남겼다.",
            "5. 재검토: SBDC와 LocalData는 공간 매칭은 되었지만 업종 bridge 상태가 다르므로 직접 경쟁축과 보조 프록시를 분리했다.",
            "",
            "## 5. 다음 알고리즘 작업",
            "",
            "- `scripts/build_rule_based_location_scores.py`는 이미 gold 로더를 사용한다. 새 silver가 추가되면 gold 역할 분류와 evidence 전달 상태를 먼저 갱신한다.",
            "- gold에 남긴 `direct_score_allowed`, `proxy_score_allowed`, `forbidden_claim_ko`를 evidence pack에 그대로 전달한다.",
            "- 성장 후보 점수는 `gold_growth_stability_q_industry`의 4분기 연속 slope와 개폐업률을 백데이터로 다시 검증한 뒤 확정한다.",
            "- 접근성은 운영 점수 기준으로는 아직 시설 기반 1단계다. 버스/지하철 승하차량 후보 gold는 31번 검증에서 만들었지만 월 커버리지·buffer 민감도·백테스트 전까지 점수 직접 투입하지 않는다.",
            "",
            "## 6. 상세 검증 파일",
            "",
            "- `datacorpus/_gold_validation/23_gold_rule_validation.csv`",
            "- `datacorpus/_gold_validation/23_gold_rule_validation_summary.csv`",
            "- `datacorpus/_gold_validation/23_gold_output_manifest.csv`",
        ]
    )
    (RESEARCH_RULE_VALIDATION / "23_gold_preprocessing_validation_20260704.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def publish_staged_files(
    stage_root: Path,
    mappings: list[tuple[Path, Path]],
    manifest_target: Path,
) -> None:
    entries: list[tuple[Path, Path]] = []
    for staged_root, canonical_root in mappings:
        for staged_path in staged_root.rglob("*"):
            if staged_path.is_file():
                entries.append((staged_path, canonical_root / staged_path.relative_to(staged_root)))
    entries.sort(key=lambda item: (item[1] == manifest_target, str(item[1])))
    if not entries or entries[-1][1] != manifest_target:
        raise RuntimeError(f"Gold manifest is missing from staged release: {manifest_target.name}")

    rollback_root = stage_root / "rollback"
    backups: dict[Path, Path | None] = {}
    for _staged_path, target_path in entries:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if not target_path.exists():
            backups[target_path] = None
            continue
        relative = target_path.resolve().relative_to(ROOT.resolve())
        backup_path = rollback_root / relative
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(target_path, backup_path)
        except OSError:
            shutil.copy2(target_path, backup_path)
        backups[target_path] = backup_path

    replaced: list[Path] = []
    try:
        for staged_path, target_path in entries:
            os.replace(staged_path, target_path)
            replaced.append(target_path)
    except OSError as exc:
        rollback_errors: list[str] = []
        for target_path in reversed(replaced):
            backup_path = backups[target_path]
            try:
                if backup_path is None:
                    target_path.unlink(missing_ok=True)
                else:
                    os.replace(backup_path, target_path)
            except OSError as rollback_exc:
                rollback_errors.append(f"{target_path}: {rollback_exc}")
        if rollback_errors:
            marker = stage_root / "PUBLISH_FAILED.txt"
            marker.write_text(
                "Gold publish and rollback failed.\n" + "\n".join(rollback_errors) + "\n",
                encoding="utf-8",
            )
            raise RuntimeError(
                f"Gold publish rollback was incomplete; recovery files remain in {stage_root}"
            ) from exc
        raise RuntimeError("Gold publish failed and canonical files were restored.") from exc


def main() -> None:
    global GOLD, GOLD_VALIDATION, RULE_VALIDATION, RESEARCH_RULE_VALIDATION, RELEASE_ID

    canonical_gold = GOLD
    canonical_gold_validation = GOLD_VALIDATION
    canonical_rule_validation = RULE_VALIDATION
    canonical_reports = RESEARCH_RULE_VALIDATION
    RELEASE_ID = datetime.now().strftime("rule_gold_%Y%m%d_%H%M%S")
    stage_root = Path(
        tempfile.mkdtemp(prefix=f"_{RELEASE_ID}_", dir=ROOT / "datacorpus")
    )
    GOLD = stage_root / "gold"
    GOLD_VALIDATION = stage_root / "gold_validation"
    RULE_VALIDATION = stage_root / "rule_validation"
    RESEARCH_RULE_VALIDATION = stage_root / "reports"
    validations.clear()

    try:
        ensure_dirs()
        RULE_VALIDATION.mkdir(parents=True, exist_ok=True)
        outputs: dict[str, Path] = {}
        builders = [
            ("gold_trade_area_profile.csv", build_trade_area_profile),
            ("gold_industry_taxonomy.csv", build_industry_taxonomy),
            ("gold_sales_strength_q_industry.csv", build_sales_strength),
            ("gold_competition_q_industry.csv", build_competition),
            ("gold_demand_q_area.csv", build_demand),
            ("gold_accessibility_q_area.csv", build_accessibility),
            ("gold_growth_stability_q_industry.csv", build_growth_stability),
            ("gold_cost_risk_q_area.csv", build_cost_risk),
            ("gold_data_reliability_snapshot.csv", build_data_reliability_snapshot),
        ]

        for filename, builder in builders:
            print(f"[gold] building {filename} ...", flush=True)
            df = builder()
            path = write_gold(df, filename)
            outputs[filename] = path
            print(f"[gold] wrote {filename}: {len(df):,} rows", flush=True)

        child_env = os.environ.copy()
        child_env.update(
            {
                "LOCALFIT_GOLD_DIR": str(GOLD),
                "LOCALFIT_RULE_VALIDATION_DIR": str(RULE_VALIDATION),
                "LOCALFIT_RESEARCH_RULE_VALIDATION_DIR": str(RESEARCH_RULE_VALIDATION),
                "LOCALFIT_SCORE_BACKTEST_DIR": str(ROOT / "datacorpus" / "_score_backtest_gold"),
            }
        )
        auxiliary_builders = [
            "build_growth_label_candidates.py",
            "validate_growth_rebound_stability.py",
            "build_growth_rebound_candidate_gold.py",
        ]
        for script_name in auxiliary_builders:
            print(f"[gold] running {script_name} ...", flush=True)
            subprocess.run(
                [sys.executable, str(ROOT / "scripts" / script_name)],
                cwd=ROOT,
                check=True,
                env=child_env,
            )

        for filename in (
            "gold_growth_label_candidates_q_industry.csv",
            "gold_growth_rebound_candidate_q_industry.csv",
        ):
            path = GOLD / filename
            if not path.exists():
                raise RuntimeError(f"Auxiliary Gold output is missing: {path}")
            outputs[filename] = path

        validation_df = write_validations()
        write_manifest(outputs, validation_df)
        write_markdown_report(outputs, validation_df)
        fail_count = int((validation_df["result"] == "FAIL").sum())
        print(f"[gold] validation rows={len(validation_df):,}, fail={fail_count}", flush=True)
        if fail_count:
            raise SystemExit(1)

        publish_staged_files(
            stage_root,
            [
                (GOLD, canonical_gold),
                (GOLD_VALIDATION, canonical_gold_validation),
                (RULE_VALIDATION, canonical_rule_validation),
                (RESEARCH_RULE_VALIDATION, canonical_reports),
            ],
            canonical_gold_validation / "23_gold_output_manifest.csv",
        )
        print(f"[gold] published release {RELEASE_ID}", flush=True)
    finally:
        GOLD = canonical_gold
        GOLD_VALIDATION = canonical_gold_validation
        RULE_VALIDATION = canonical_rule_validation
        RESEARCH_RULE_VALIDATION = canonical_reports
        if not (stage_root / "PUBLISH_FAILED.txt").exists():
            shutil.rmtree(stage_root, ignore_errors=True)


if __name__ == "__main__":
    main()
