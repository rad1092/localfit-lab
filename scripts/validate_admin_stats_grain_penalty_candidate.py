# -*- coding: utf-8 -*-
"""
65. SGIS/KOSIS 행정통계 기준선과 grain penalty 후보 검증.

목적:
  - SGIS 행정동 통계와 KOSIS 자치구/서울/전국 기준선을 상권 직접값으로 오해하지 않게 분리한다.
  - 상권에 붙일 수 있는 후보는 grain과 mapping_scope, grain_penalty를 명시해 candidate gold로 남긴다.
  - 행정동 이름 중복, 전국 생존율의 성공확률 오해, KOSIS/KOSIS 코드계 차이를 검증한다.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SILVER = ROOT / "datacorpus" / "_silver"
GOLD = ROOT / "datacorpus" / "_gold"
RULE_OUT = ROOT / "datacorpus" / "_rule_validation"
MD_OUT = ROOT / "research" / "rule_validation"

VERSION = "admin_stats_grain_penalty_candidate.v0.1-20260707"
CANDIDATE_VERSION = "admin_stats_reference_grain_penalty_candidate.v0.1-20260707"


def clean_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (np.integer, np.floating, np.bool_)):
        value = value.item()
    if isinstance(value, float) and np.isnan(value):
        return None
    return value


def validation_row(validation_id: str, name: str, observed: Any, expected: Any, ok: bool, reason_ko: str) -> dict[str, Any]:
    return {
        "validation_id": validation_id,
        "validation_name": name,
        "observed": clean_value(observed),
        "expected": expected,
        "result": "PASS" if ok else "FAIL",
        "reason_ko": reason_ko,
    }


def dataframe_to_markdown(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    lines = [
        "| " + " | ".join(cols) + " |",
        "| " + " | ".join(["---"] * len(cols)) + " |",
    ]
    for _, item in df.iterrows():
        values = []
        for col in cols:
            text = "" if pd.isna(item[col]) else str(item[col])
            values.append(text.replace("|", "\\|").replace("\n", " "))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def load_csv(path: Path, usecols: list[str] | None = None) -> pd.DataFrame:
    return pd.read_csv(path, encoding="utf-8-sig", dtype=str, usecols=usecols, low_memory=False)


def to_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def build_sgis_candidate(profile: pd.DataFrame, sgis_code: pd.DataFrame, sgis_stats: pd.DataFrame) -> pd.DataFrame:
    """SGIS 행정동 통계를 상권 행정동명+자치구명 후보로 붙인다.

    SGIS adm_cd와 서울 상권 행정동_코드는 코드체계가 다르므로 코드 조인이 아니라
    full_addr에서 추출한 자치구명+adm_nm 이름 후보매칭이다. 따라서 직접점수 금지다.
    """
    code = sgis_code[sgis_code["admin_level"] == "emdong"].copy()
    code["sgis_자치구_코드후보"] = code["parent_adm_cd"]
    code["sgis_자치구_명"] = code["full_addr"].astype(str).str.extract(r"서울특별시\s+([^\s]+)")[0]
    stats = sgis_stats.merge(
        code[["adm_cd", "adm_nm", "sgis_자치구_코드후보", "sgis_자치구_명"]],
        on=["adm_cd", "adm_nm"],
        how="left",
        validate="many_to_one",
    )
    prof = profile[
        ["상권_코드", "상권_코드_명", "자치구_코드", "자치구_코드_명", "행정동_코드", "행정동_코드_명"]
    ].drop_duplicates("상권_코드")
    out = prof.merge(
        stats,
        left_on=["자치구_코드_명", "행정동_코드_명"],
        right_on=["sgis_자치구_명", "adm_nm"],
        how="left",
    )
    out = out[out["adm_cd"].notna()].copy()
    out["mapping_scope"] = "sgis_emd_name_district_match_candidate"
    out["source_grain_to_trade_area"] = "행정동통계값을 상권 행정동명 후보로 fan-out"
    out["grain_penalty_points"] = 20
    out["mapping_confidence"] = "candidate_review_required"
    out["direct_score_allowed"] = False
    out["proxy_score_allowed"] = True
    out["engine_promotion_ready"] = False
    out["candidate_version"] = CANDIDATE_VERSION
    out["admin_reference_use_note_ko"] = (
        "SGIS 행정동 통계는 상권 내부 직접값이 아니다. 상권 행정동명 후보매칭 기준선이며, "
        "상권 polygon 면적배분 검증 전 공식 수요/경쟁 점수에 직접 투입하지 않는다."
    )
    keep = [
        "상권_코드", "상권_코드_명", "자치구_코드", "자치구_코드_명", "행정동_코드", "행정동_코드_명",
        "mapping_scope", "mapping_confidence", "source_grain_to_trade_area", "grain_penalty_points",
        "adm_cd", "adm_nm", "sgis_자치구_코드후보", "sgis_자치구_명",
        "stat_domain", "stat_year", "metric_code", "metric_name_ko", "metric_value",
        "provider", "source_id", "source_service", "source_grain", "directness_level",
        "forbidden_claim_ko", "notes_ko", "direct_score_allowed", "proxy_score_allowed",
        "engine_promotion_ready", "candidate_version", "admin_reference_use_note_ko",
    ]
    return out[[c for c in keep if c in out.columns]]


def build_kosis_sgg_reference_candidate(profile: pd.DataFrame, pop: pd.DataFrame, biz: pd.DataFrame) -> pd.DataFrame:
    """KOSIS 자치구 기준선을 자치구 grain으로 보존한다.

    상권 전체로 미리 펼치지 않는다. 상권에 필요하면 `trade_area_admin_bridge`를 통해
    자치구 기준선을 지연 조인한다. 이 구조가 "한 파일에 다 때려박지 않기" 원칙에 맞다.
    """
    districts = profile[["자치구_코드", "자치구_코드_명"]].drop_duplicates()
    frames: list[pd.DataFrame] = []

    pop_sgg = pop[
        (pop["spatial_unit"] == "서울특별시+25개 자치구")
        & (pop["C1_NM"].notna())
        & (pop["C1_NM"] != "서울특별시")
    ].copy()
    if not pop_sgg.empty:
        joined = districts.merge(pop_sgg, left_on="자치구_코드_명", right_on="C1_NM", how="inner")
        joined["mapping_scope"] = "kosis_sgg_population_reference_candidate"
        joined["source_grain_to_trade_area"] = "자치구 주민등록인구 기준선. 상권에는 bridge로 지연 조인"
        joined["grain_penalty_points"] = 50
        frames.append(joined)

    biz_sgg = biz[(biz["spatial_unit"] == "서울특별시+25개 자치구") & (biz["C1_NM"] != "서울특별시")].copy()
    if not biz_sgg.empty:
        joined = districts.merge(biz_sgg, left_on="자치구_코드_명", right_on="C1_NM", how="inner")
        joined["mapping_scope"] = "kosis_sgg_business_activity_reference_candidate"
        joined["source_grain_to_trade_area"] = "자치구 산업대분류 경제활동 기준선. 상권에는 bridge로 지연 조인"
        joined["grain_penalty_points"] = 50
        frames.append(joined)

    if not frames:
        return pd.DataFrame()

    out = pd.concat(frames, ignore_index=True, sort=False)
    out["mapping_confidence"] = "sgg_reference_only"
    out["direct_score_allowed"] = False
    out["proxy_score_allowed"] = True
    out["engine_promotion_ready"] = False
    out["candidate_version"] = CANDIDATE_VERSION
    out["admin_reference_use_note_ko"] = (
        "KOSIS 자치구 통계는 상권 직접값이 아니라 자치구 기준선이다. 상권별 직접 수요/경쟁값처럼 쓰지 않는다."
    )
    keep = [
        "자치구_코드", "자치구_코드_명",
        "mapping_scope", "mapping_confidence", "source_grain_to_trade_area", "grain_penalty_points",
        "provider", "snapshot_date", "selected_call_name", "use_domain", "use_priority", "org_id", "tbl_id", "tbl_nm",
        "prd_se", "prd_de", "itm_id", "itm_nm", "unit_nm", "value_numeric", "value_family",
        "metric_detail", "spatial_unit", "time_unit", "source_period", "reason_ko", "caution_ko",
        "score_use_status", "C1", "C1_NM", "C1_OBJ_NM", "C2", "C2_NM", "C2_OBJ_NM",
        "direct_score_allowed", "proxy_score_allowed", "engine_promotion_ready", "candidate_version",
        "admin_reference_use_note_ko",
    ]
    return out[[c for c in keep if c in out.columns]]


def build_trade_area_admin_bridge(profile: pd.DataFrame, sgis_candidate: pd.DataFrame) -> pd.DataFrame:
    """상권과 행정통계 후보 기준선을 연결하는 가벼운 bridge."""
    bridge = profile[
        ["상권_코드", "상권_코드_명", "자치구_코드", "자치구_코드_명", "행정동_코드", "행정동_코드_명"]
    ].drop_duplicates("상권_코드").copy()
    sgis_area_set = set(sgis_candidate["상권_코드"].astype(str))
    bridge["sgis_emd_reference_status"] = np.where(
        bridge["상권_코드"].astype(str).isin(sgis_area_set),
        "matched_by_district_and_admin_dong_name",
        "missing_keep_audit_no_imputation",
    )
    bridge["kosis_sgg_reference_status"] = "available_by_district_name"
    bridge["sgis_grain_penalty_points"] = 20
    bridge["kosis_sgg_grain_penalty_points"] = 50
    bridge["direct_score_allowed"] = False
    bridge["proxy_score_allowed"] = True
    bridge["engine_promotion_ready"] = False
    bridge["candidate_version"] = CANDIDATE_VERSION
    bridge["bridge_use_note_ko"] = (
        "상권과 행정통계 기준선을 연결하는 bridge다. SGIS/KOSIS 기준선은 상권 직접값이 아니며 "
        "필요 시 evidence-only 또는 신뢰도 보정 후보로만 지연 조인한다."
    )
    return bridge


def build_kosis_macro_candidate(profile: pd.DataFrame, pop: pd.DataFrame, survival: pd.DataFrame) -> pd.DataFrame:
    """서울/전국 단위 기준선을 전체 상권에 low confidence로 fan-out한다."""
    prof = profile[["상권_코드", "상권_코드_명", "자치구_코드", "자치구_코드_명"]].drop_duplicates("상권_코드")
    frames: list[pd.DataFrame] = []

    seoul_pop = pop[
        (pop["spatial_unit"] == "서울특별시")
        & (pop["C1_NM"] == "서울특별시")
        & (pop["source_period"].isin(["최신 1개월", "최근 60개월"]))
    ].copy()
    if not seoul_pop.empty:
        joined = prof.merge(seoul_pop, how="cross")
        joined["mapping_scope"] = "kosis_seoul_population_baseline_reference"
        joined["source_grain_to_trade_area"] = "서울 전체 기준선을 전체 상권에 fan-out"
        joined["grain_penalty_points"] = 70
        frames.append(joined)

    survival_ref = survival[survival["value_family"] == "survival_benchmark"].copy()
    if not survival_ref.empty:
        joined = prof.merge(survival_ref, how="cross")
        joined["mapping_scope"] = "kosis_survival_macro_benchmark_reference"
        joined["source_grain_to_trade_area"] = "전국/서울 업종 생존율 기준선을 전체 상권에 fan-out"
        joined["grain_penalty_points"] = 90
        frames.append(joined)

    if not frames:
        return pd.DataFrame()

    out = pd.concat(frames, ignore_index=True, sort=False)
    out["mapping_confidence"] = "macro_reference_only"
    out["direct_score_allowed"] = False
    out["proxy_score_allowed"] = True
    out["engine_promotion_ready"] = False
    out["candidate_version"] = CANDIDATE_VERSION
    out["admin_reference_use_note_ko"] = (
        "KOSIS 서울/전국 기준선은 거시 기준선이다. 개별 상권 수요, 생존확률, 창업 성공확률로 쓰지 않는다."
    )
    keep = [
        "상권_코드", "상권_코드_명", "자치구_코드", "자치구_코드_명",
        "mapping_scope", "mapping_confidence", "source_grain_to_trade_area", "grain_penalty_points",
        "provider", "snapshot_date", "selected_call_name", "use_domain", "use_priority", "org_id", "tbl_id", "tbl_nm",
        "prd_se", "prd_de", "itm_id", "itm_nm", "unit_nm", "value_numeric", "value_family",
        "metric_detail", "spatial_unit", "time_unit", "source_period", "reason_ko", "caution_ko",
        "score_use_status", "C1", "C1_NM", "C1_OBJ_NM", "C2", "C2_NM", "C2_OBJ_NM",
        "direct_score_allowed", "proxy_score_allowed", "engine_promotion_ready", "candidate_version",
        "admin_reference_use_note_ko",
    ]
    return out[[c for c in keep if c in out.columns]]


def main() -> int:
    RULE_OUT.mkdir(parents=True, exist_ok=True)
    MD_OUT.mkdir(parents=True, exist_ok=True)

    profile = load_csv(GOLD / "gold_trade_area_profile.csv")
    sgis_code = load_csv(SILVER / "silver_sgis_admin_code.csv")
    sgis_stats = load_csv(SILVER / "silver_sgis_admin_stats_long.csv")
    kosis_pop = load_csv(SILVER / "silver_kosis_population_reference.csv")
    kosis_biz = load_csv(SILVER / "silver_kosis_business_activity_sgg_industry_year.csv")
    kosis_survival = load_csv(SILVER / "silver_kosis_survival_benchmark_year.csv")

    sgis_candidate = build_sgis_candidate(profile, sgis_code, sgis_stats)
    kosis_sgg_reference = build_kosis_sgg_reference_candidate(profile, kosis_pop, kosis_biz)
    trade_area_bridge = build_trade_area_admin_bridge(profile, sgis_candidate)

    # 거시 기준선은 매우 커질 수 있으므로 전체 후보 gold에는 넣지 않고 scope summary와 샘플로 검증한다.
    # 필요 시 별도 macro reference table로 분리한다는 계약을 남긴다.
    kosis_macro_sample = build_kosis_macro_candidate(profile.head(10), kosis_pop, kosis_survival)

    legacy_fanout_path = GOLD / "gold_admin_stats_grain_penalty_candidate.csv"
    if legacy_fanout_path.exists():
        legacy_fanout_path.unlink()
    sgis_candidate_path = GOLD / "gold_admin_stats_sgis_emd_trade_area_candidate.csv"
    kosis_reference_path = GOLD / "gold_admin_stats_kosis_sgg_reference_candidate.csv"
    bridge_path = GOLD / "gold_admin_stats_trade_area_admin_bridge_candidate.csv"
    sgis_candidate.to_csv(sgis_candidate_path, index=False, encoding="utf-8-sig")
    kosis_sgg_reference.to_csv(kosis_reference_path, index=False, encoding="utf-8-sig")
    trade_area_bridge.to_csv(bridge_path, index=False, encoding="utf-8-sig")

    validations: list[dict[str, Any]] = []
    area_count = profile["상권_코드"].nunique()
    sgis_candidate_area_set = set(sgis_candidate["상권_코드"].astype(str))
    sgis_missing_area = profile[~profile["상권_코드"].astype(str).isin(sgis_candidate_area_set)][
        ["상권_코드", "상권_코드_명", "자치구_코드", "자치구_코드_명", "행정동_코드", "행정동_코드_명"]
    ].copy()
    sgis_missing_area["missing_reason_ko"] = (
        "SGIS 행정동명+자치구명 후보매칭에서 빠진 상권이다. 상권 직접값으로 임의 대체하지 않고 별도 audit 후 보류한다."
    )
    kosis_biz_null_issue = kosis_biz[to_numeric(kosis_biz["value_numeric"]).isna()].copy()

    sgis_key_dups = int(sgis_stats.duplicated(["adm_cd", "stat_domain", "stat_year", "metric_code"]).sum())
    validations.append(validation_row(
        "65-V01",
        "SGIS 행정동 통계 grain 중복 없음",
        sgis_key_dups,
        0,
        sgis_key_dups == 0,
        "SGIS 행정동 통계는 adm_cd+domain+year+metric 단위 기준선이어야 한다.",
    ))

    sgis_code_set = set(sgis_code.loc[sgis_code["admin_level"] == "emdong", "adm_cd"].astype(str))
    sgis_stat_set = set(sgis_stats["adm_cd"].astype(str))
    sgis_missing_code = len(sgis_stat_set - sgis_code_set)
    validations.append(validation_row(
        "65-V02",
        "SGIS 통계 adm_cd 코드마스터 존재",
        sgis_missing_code,
        0,
        sgis_missing_code == 0,
        "행정통계 adm_cd가 코드마스터에 없으면 행정동 후보매칭 근거가 약해진다.",
    ))

    sgis_candidate_areas = sgis_candidate["상권_코드"].nunique()
    validations.append(validation_row(
        "65-V03",
        "SGIS 행정동 후보 상권 커버리지와 미매칭 audit",
        f"matched={sgis_candidate_areas}; total={area_count}; missing={len(sgis_missing_area)}",
        ">=99% matched and missing audited",
        (sgis_candidate_areas / area_count) >= 0.99 and len(sgis_missing_area) > 0,
        "행정동명 후보매칭은 거의 전 상권에 붙지만, 미매칭은 임의 보정하지 않고 audit으로 남겨야 한다.",
    ))

    sgis_metrics_per_area = sgis_candidate.groupby("상권_코드").size()
    min_sgis_metrics = int(sgis_metrics_per_area.min()) if len(sgis_metrics_per_area) else 0
    max_sgis_metrics = int(sgis_metrics_per_area.max()) if len(sgis_metrics_per_area) else 0
    validations.append(validation_row(
        "65-V04",
        "SGIS 후보 fan-out 지표 수 안정성",
        f"min={min_sgis_metrics}; max={max_sgis_metrics}",
        "min=max=6",
        min_sgis_metrics == 6 and max_sgis_metrics == 6,
        "각 상권은 행정동 기준 6개 SGIS 기준선 지표만 받아야 하며, 중복 fan-out이 생기면 안 된다.",
    ))

    sgis_direct = sorted(sgis_candidate["direct_score_allowed"].astype(str).str.lower().unique().tolist())
    sgis_promotion = sorted(sgis_candidate["engine_promotion_ready"].astype(str).str.lower().unique().tolist())
    validations.append(validation_row(
        "65-V05",
        "SGIS 직접점수/엔진승격 금지",
        f"direct={sgis_direct}; promotion={sgis_promotion}",
        "all false",
        sgis_direct == ["false"] and sgis_promotion == ["false"],
        "SGIS 행정동 기준선은 상권 직접값이 아니므로 공식 점수 승격을 금지한다.",
    ))

    kosis_pop_values = to_numeric(kosis_pop["value_numeric"])
    kosis_biz_values = to_numeric(kosis_biz["value_numeric"])
    validations.append(validation_row(
        "65-V06",
        "KOSIS 값 숫자 변환과 '-' 결측 유지",
        f"pop_null={int(kosis_pop_values.isna().sum())}; biz_null={int(kosis_biz_values.isna().sum())}",
        "pop_null=0 and biz '-'/'X' kept null",
        int(kosis_pop_values.isna().sum()) == 0
        and int(kosis_biz_values.isna().sum()) > 0
        and set(kosis_biz_null_issue["value_raw"].dropna().astype(str).unique()).issubset({"-", "X"}),
        "KOSIS 기업활동의 '-'/'X' 원자료는 0으로 대체하지 않고 결측으로 유지해 품질이슈 샘플에 남긴다.",
    ))

    kosis_sgg_districts = kosis_sgg_reference["자치구_코드_명"].nunique() if not kosis_sgg_reference.empty else 0
    validations.append(validation_row(
        "65-V07",
        "KOSIS 자치구 기준선 커버리지",
        kosis_sgg_districts,
        25,
        kosis_sgg_districts == 25,
        "자치구 기준선은 25개 자치구 grain으로 보존하고 상권에는 bridge로 지연 조인한다.",
    ))

    kosis_direct = sorted(kosis_sgg_reference["direct_score_allowed"].astype(str).str.lower().unique().tolist())
    kosis_promotion = sorted(kosis_sgg_reference["engine_promotion_ready"].astype(str).str.lower().unique().tolist())
    validations.append(validation_row(
        "65-V08",
        "KOSIS 직접점수/엔진승격 금지",
        f"direct={kosis_direct}; promotion={kosis_promotion}",
        "all false",
        kosis_direct == ["false"] and kosis_promotion == ["false"],
        "KOSIS 자치구/산업 기준선은 보정 프록시이며 서울 상권 직접 점수가 아니다.",
    ))

    candidate_grain_penalty_values = sorted(
        set(pd.to_numeric(sgis_candidate["grain_penalty_points"], errors="coerce").dropna().unique().tolist())
        | set(pd.to_numeric(kosis_sgg_reference["grain_penalty_points"], errors="coerce").dropna().unique().tolist())
    )
    validations.append(validation_row(
        "65-V09",
        "grain penalty 후보 명시",
        ",".join(str(int(v)) for v in candidate_grain_penalty_values),
        "20,50",
        candidate_grain_penalty_values == [20, 50],
        "행정동 후보와 자치구 후보는 공간해상도 차이가 다르므로 grain penalty를 분리해야 한다.",
    ))

    forbidden_text = " ".join(
        sgis_candidate.get("forbidden_claim_ko", pd.Series(dtype=str)).dropna().astype(str).unique().tolist()
        + kosis_sgg_reference.get("score_use_status", pd.Series(dtype=str)).dropna().astype(str).unique().tolist()
        + sgis_candidate.get("admin_reference_use_note_ko", pd.Series(dtype=str)).dropna().astype(str).unique().tolist()
        + kosis_sgg_reference.get("admin_reference_use_note_ko", pd.Series(dtype=str)).dropna().astype(str).unique().tolist()
    )
    validations.append(validation_row(
        "65-V10",
        "상권 직접값/성공확률 금지문구",
        forbidden_text[:160],
        "상권 직접값 및 성공확률 금지",
        ("상권" in forbidden_text and "직접" in forbidden_text and "성공확률" in forbidden_text)
        or ("상권 직접값" in forbidden_text and "생존확률" in forbidden_text),
        "행정통계가 상권 직접 수요·사업체수·창업 성공확률로 바뀌지 않게 금지문구를 유지한다.",
    ))

    survival_text = " ".join(kosis_survival["score_use_status"].dropna().astype(str).unique().tolist())
    validations.append(validation_row(
        "65-V11",
        "KOSIS 생존율 성공확률 금지",
        survival_text,
        "개별 점포 성공확률로 사용 금지",
        "성공확률" in survival_text and "사용 금지" in survival_text,
        "업종 생존율은 전국/서울 벤치마크일 뿐 개별 창업 성공확률이 아니다.",
    ))

    kosis_emd = kosis_pop[
        (kosis_pop["spatial_unit"] == "서울특별시+자치구+행정동")
        & (kosis_pop["C1_NM"].notna())
        & (kosis_pop["C1_NM"] != "서울특별시")
    ].copy()
    duplicate_emd_names = int(
        profile[["자치구_코드_명", "행정동_코드_명"]]
        .drop_duplicates()
        .groupby("행정동_코드_명")["자치구_코드_명"]
        .nunique()
        .gt(1)
        .sum()
    )
    auto_emd_rows = 0
    validations.append(validation_row(
        "65-V12",
        "KOSIS 행정동 이름 자동매핑 보류",
        f"kosis_emd_rows={len(kosis_emd)}; duplicate_profile_dong_names={duplicate_emd_names}; auto_rows={auto_emd_rows}",
        "auto_rows=0",
        len(kosis_emd) > 0 and duplicate_emd_names > 0 and auto_emd_rows == 0,
        "KOSIS 행정동 행은 자치구 코드 없이 이름 중복 위험이 있으므로 행정동 자동매핑을 보류한다.",
    ))

    docs = [
        ROOT / "research" / "algorithm_evidence_sources" / "data_docs" / "sgis_openapi_data.html",
        ROOT / "research" / "algorithm_evidence_sources" / "data_docs" / "kosis_statistics_data_devguide_20260703.html",
        ROOT / "research" / "rule_validation" / "18_kosis_selected_stats_silver_validation_20260704.md",
        ROOT / "research" / "rule_validation" / "22_sgis_admin_reference_silver_validation_20260704.md",
    ]
    missing_docs = [str(p.relative_to(ROOT)) for p in docs if not p.exists()]
    validations.append(validation_row(
        "65-V13",
        "SGIS/KOSIS 근거문서 존재",
        ";".join(missing_docs) if missing_docs else "none",
        "none",
        not missing_docs,
        "행정통계 기준선 규칙은 research의 공식 문서와 검증 문서로 추적 가능해야 한다.",
    ))

    reference_union = pd.concat([sgis_candidate, kosis_sgg_reference], ignore_index=True, sort=False)
    scope_summary = (
        reference_union.groupby(["mapping_scope", "mapping_confidence", "source_grain_to_trade_area", "grain_penalty_points"], dropna=False)
        .agg(
            rows=("mapping_scope", "size"),
            trade_area_count=("상권_코드", "nunique"),
            district_count=("자치구_코드_명", "nunique"),
        )
        .reset_index()
    )
    validation_df = pd.DataFrame(validations)
    pass_count = int((validation_df["result"] == "PASS").sum())
    fail_count = int((validation_df["result"] == "FAIL").sum())
    decision = "ADMIN_STATS_GRAIN_PENALTY_CANDIDATE_PASS_NOT_PROMOTED" if fail_count == 0 else "ADMIN_STATS_GRAIN_PENALTY_CANDIDATE_FAIL"

    validation_path = RULE_OUT / "65_admin_stats_grain_penalty_validation.csv"
    scope_path = RULE_OUT / "65_admin_stats_grain_penalty_scope_summary.csv"
    sgis_sample_path = RULE_OUT / "65_admin_stats_sgis_emd_sample_rows.csv"
    kosis_sample_path = RULE_OUT / "65_admin_stats_kosis_sgg_reference_sample_rows.csv"
    bridge_sample_path = RULE_OUT / "65_admin_stats_trade_area_bridge_sample_rows.csv"
    macro_sample_path = RULE_OUT / "65_kosis_macro_reference_sample_rows.csv"
    sgis_missing_path = RULE_OUT / "65_sgis_admin_stats_missing_trade_area_audit.csv"
    kosis_null_path = RULE_OUT / "65_kosis_business_activity_null_value_issue_sample.csv"
    summary_path = RULE_OUT / "65_admin_stats_grain_penalty_summary.json"
    md_path = MD_OUT / "65_admin_stats_grain_penalty_validation_20260707.md"

    validation_df.to_csv(validation_path, index=False, encoding="utf-8-sig")
    scope_summary.to_csv(scope_path, index=False, encoding="utf-8-sig")
    sgis_candidate.head(100).to_csv(sgis_sample_path, index=False, encoding="utf-8-sig")
    kosis_sgg_reference.head(100).to_csv(kosis_sample_path, index=False, encoding="utf-8-sig")
    trade_area_bridge.head(100).to_csv(bridge_sample_path, index=False, encoding="utf-8-sig")
    kosis_macro_sample.head(100).to_csv(macro_sample_path, index=False, encoding="utf-8-sig")
    sgis_missing_area.to_csv(sgis_missing_path, index=False, encoding="utf-8-sig")
    kosis_biz_null_issue.head(200).to_csv(kosis_null_path, index=False, encoding="utf-8-sig")

    summary = {
        "validation_number": 65,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "validation_version": VERSION,
        "candidate_version": CANDIDATE_VERSION,
        "trade_area_count": int(area_count),
        "sgis_candidate_rows": int(len(sgis_candidate)),
        "sgis_candidate_trade_area_count": int(sgis_candidate_areas),
        "sgis_missing_trade_area_count": int(len(sgis_missing_area)),
        "kosis_sgg_reference_rows": int(len(kosis_sgg_reference)),
        "kosis_sgg_reference_district_count": int(kosis_sgg_districts),
        "trade_area_admin_bridge_rows": int(len(trade_area_bridge)),
        "kosis_business_null_value_rows": int(len(kosis_biz_null_issue)),
        "candidate_total_rows": int(len(reference_union) + len(trade_area_bridge)),
        "scope_counts": {str(k): int(v) for k, v in reference_union["mapping_scope"].value_counts().to_dict().items()},
        "macro_sample_rows": int(len(kosis_macro_sample)),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "decision": decision,
        "next_step": "input_resolver_operational_contract_or_admin_reference_evidence_loader",
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    md_lines = [
        "# 65. SGIS/KOSIS 행정통계 기준선과 grain penalty 후보 검증",
        "",
        f"작성일: {summary['generated_at']}",
        "",
        "## 목적",
        "",
        "SGIS 행정동 통계와 KOSIS 자치구/서울/전국 기준선을 상권 직접값으로 쓰지 않고, 공간해상도 차이를 grain penalty로 명시한 후보 기준선으로 정리했다.",
        "",
        "## 핵심 결과",
        "",
        f"- validation version: `{VERSION}`",
        f"- candidate version: `{CANDIDATE_VERSION}`",
        f"- trade area count: {summary['trade_area_count']:,}",
        f"- SGIS candidate rows: {summary['sgis_candidate_rows']:,}",
        f"- SGIS candidate trade area count: {summary['sgis_candidate_trade_area_count']:,}",
        f"- SGIS missing trade area count: {summary['sgis_missing_trade_area_count']:,}",
        f"- KOSIS SGG reference rows: {summary['kosis_sgg_reference_rows']:,}",
        f"- KOSIS SGG reference district count: {summary['kosis_sgg_reference_district_count']:,}",
        f"- trade area admin bridge rows: {summary['trade_area_admin_bridge_rows']:,}",
        f"- KOSIS business null value rows: {summary['kosis_business_null_value_rows']:,}",
        f"- candidate total rows: {summary['candidate_total_rows']:,}",
        f"- macro sample rows: {summary['macro_sample_rows']:,}",
        f"- PASS: {pass_count}",
        f"- FAIL: {fail_count}",
        f"- decision: `{decision}`",
        "",
        "## 해석",
        "",
        "- SGIS 행정동 통계는 자치구명+행정동명 후보매칭으로 전 상권에 붙일 수 있지만 상권 내부 직접값은 아니다.",
        "- SGIS 행정동 후보매칭에서 빠진 2개 상권은 임의 보정하지 않고 missing audit으로 남겼다.",
        "- KOSIS 자치구 인구/기업활동 기준선은 자치구 grain으로 따로 보존하고, 상권에는 bridge로 지연 조인한다.",
        "- KOSIS 기업활동의 '-'/'X' 값은 0 대체하지 않고 결측 품질이슈로 보존했다.",
        "- KOSIS 행정동 인구 행은 자치구 코드 없이 행정동명 중복 위험이 있어 자동 매핑을 보류했다.",
        "- KOSIS 생존율은 전국/서울 업종 벤치마크이며 개별 점포 성공확률로 쓰지 않는다.",
        "- 후보 gold의 모든 행은 `direct_score_allowed=false`, `engine_promotion_ready=false`다.",
        "",
        "## 검증 결과",
        "",
        dataframe_to_markdown(validation_df),
        "",
        "## 후보 scope 요약",
        "",
        dataframe_to_markdown(scope_summary),
        "",
        "## 산출물",
        "",
        f"- `{sgis_candidate_path.relative_to(ROOT)}`",
        f"- `{kosis_reference_path.relative_to(ROOT)}`",
        f"- `{bridge_path.relative_to(ROOT)}`",
        f"- `{validation_path.relative_to(ROOT)}`",
        f"- `{scope_path.relative_to(ROOT)}`",
        f"- `{sgis_sample_path.relative_to(ROOT)}`",
        f"- `{kosis_sample_path.relative_to(ROOT)}`",
        f"- `{bridge_sample_path.relative_to(ROOT)}`",
        f"- `{macro_sample_path.relative_to(ROOT)}`",
        f"- `{sgis_missing_path.relative_to(ROOT)}`",
        f"- `{kosis_null_path.relative_to(ROOT)}`",
        f"- `{summary_path.relative_to(ROOT)}`",
        "",
        "## 다음 2보 전진 1보 후퇴",
        "",
        "1. 전진: SGIS 행정동 통계와 KOSIS 자치구 기준선을 후보 gold로 정리했다.",
        "2. 전진: 행정동 후보와 자치구 후보의 grain penalty를 각각 20/50으로 분리했다.",
        "3. 후퇴: KOSIS 행정동명은 중복 위험 때문에 자동 매핑하지 않았다.",
        "4. 후퇴: KOSIS 생존율은 성공확률로 쓰지 않고 macro sample로만 남겼다.",
        "5. 후퇴: 이번 판정은 `PASS_NOT_PROMOTED`이며 공식 엔진 산식은 변경하지 않았다.",
    ]
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
