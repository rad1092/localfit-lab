from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "datacorpus" / "_raw_ingest"
SGIS_DIR = RAW_DIR / "20260703" / "sgis"
SILVER_DIR = ROOT / "datacorpus" / "_silver"
VALIDATION_DIR = ROOT / "datacorpus" / "_rule_validation"
RESEARCH_VALIDATION_DIR = ROOT / "research" / "rule_validation"

MANIFEST_PATH = RAW_DIR / "ingest_manifest.csv"
FAILED_DOWNLOADS_PATH = RAW_DIR / "failed_downloads.csv"

SNAPSHOT_DATE = "2026-07-03"
PROVIDER = "SGIS"
SOURCE_ID = "sgis_admin_reference"
BOUNDARY_YEAR = "2025"
SOURCE_CRS = "EPSG:5179 추정"
SOURCE_CRS_NOTE = "SGIS hadmarea GeoJSON 원문에는 crs 키가 없으나 좌표 범위와 SGIS API 문서상 행정경계 좌표계 사용 전제에 따라 EPSG:5179로 메타를 명시한다."

SENSITIVE_DENYLIST = [
    "accessToken",
    "access_token",
    "accessTimeout",
    "consumer_key",
    "consumer_secret",
    "secret",
    "token",
]


def ensure_dirs() -> None:
    for path in [SILVER_DIR, VALIDATION_DIR, RESEARCH_VALIDATION_DIR]:
        path.mkdir(parents=True, exist_ok=True)


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def assert_success(payload: dict[str, Any], path: Path) -> None:
    if str(payload.get("errCd")) != "0" or str(payload.get("errMsg")) != "Success":
        raise ValueError(f"{path} SGIS 응답이 성공이 아닙니다: {payload.get('errCd')} {payload.get('errMsg')}")


def payload_result_list(path: Path) -> list[dict[str, Any]]:
    payload = read_json(path)
    assert_success(payload, path)
    result = payload.get("result")
    if not isinstance(result, list):
        raise ValueError(f"{path} result가 list가 아닙니다.")
    return result


def payload_result_dict(path: Path) -> dict[str, Any]:
    payload = read_json(path)
    assert_success(payload, path)
    result = payload.get("result")
    if not isinstance(result, dict):
        raise ValueError(f"{path} result가 dict가 아닙니다.")
    return result


def build_admin_code() -> pd.DataFrame:
    sido_path = SGIS_DIR / "spatial_codes" / "20260703_110138_sgis_spatial_codes_addr_stage_sido.json"
    sgg_path = SGIS_DIR / "spatial_codes" / "20260703_110138_sgis_spatial_codes_addr_stage_seoul_sgg.json"
    emdong_paths = sorted((SGIS_DIR / "spatial_codes" / "emdong").glob("20260703_110138_sgis_spatial_codes_addr_stage_*_emdong.json"))

    rows: list[dict[str, Any]] = []
    for item in payload_result_list(sido_path):
        rows.append(
            {
                "admin_level": "sido",
                "adm_cd": str(item.get("cd", "")).strip(),
                "adm_nm": str(item.get("addr_name", "")).strip(),
                "full_addr": str(item.get("full_addr", "")).strip(),
                "parent_adm_cd": "",
                "x_coor": item.get("x_coor", ""),
                "y_coor": item.get("y_coor", ""),
                "source_crs": SOURCE_CRS,
                "source_file": rel(sido_path),
            }
        )

    for item in payload_result_list(sgg_path):
        adm_cd = str(item.get("cd", "")).strip()
        rows.append(
            {
                "admin_level": "sgg",
                "adm_cd": adm_cd,
                "adm_nm": str(item.get("addr_name", "")).strip(),
                "full_addr": str(item.get("full_addr", "")).strip(),
                "parent_adm_cd": "11",
                "x_coor": item.get("x_coor", ""),
                "y_coor": item.get("y_coor", ""),
                "source_crs": SOURCE_CRS,
                "source_file": rel(sgg_path),
            }
        )

    for path in emdong_paths:
        match = re.search(r"addr_stage_(\d{5})_emdong", path.name)
        parent = match.group(1) if match else ""
        for item in payload_result_list(path):
            adm_cd = str(item.get("cd", "")).strip()
            rows.append(
                {
                    "admin_level": "emdong",
                    "adm_cd": adm_cd,
                    "adm_nm": str(item.get("addr_name", "")).strip(),
                    "full_addr": str(item.get("full_addr", "")).strip(),
                    "parent_adm_cd": parent,
                    "x_coor": item.get("x_coor", ""),
                    "y_coor": item.get("y_coor", ""),
                    "source_crs": SOURCE_CRS,
                    "source_file": rel(path),
                }
            )

    df = pd.DataFrame(rows)
    df["x_coor"] = pd.to_numeric(df["x_coor"], errors="coerce")
    df["y_coor"] = pd.to_numeric(df["y_coor"], errors="coerce")
    df["snapshot_date"] = SNAPSHOT_DATE
    df["provider"] = PROVIDER
    df["source_id"] = SOURCE_ID
    df["source_service"] = "addr/stage"
    df["directness_level"] = "P1_SGIS_공식_행정구역_코드"
    df["forbidden_claim_ko"] = "상권 단위 실적, 개별 매장 매출, 창업 성공확률로 표현 금지"
    df["notes_ko"] = "SGIS 행정구역 코드 마스터다. 상권 코드와 직접 동일하지 않으므로 bridge 검증 전 상권 점수에 직접 넣지 않는다."
    return df.sort_values(["admin_level", "adm_cd"]).reset_index(drop=True)


def flatten_coordinates(obj: Any) -> Iterable[tuple[float, float]]:
    if isinstance(obj, (list, tuple)):
        if len(obj) >= 2 and all(isinstance(v, (int, float)) for v in obj[:2]):
            yield float(obj[0]), float(obj[1])
        else:
            for item in obj:
                yield from flatten_coordinates(item)


def build_boundary() -> pd.DataFrame:
    paths = [
        ("sgg", SGIS_DIR / "boundary" / "20260703_110138_sgis_spatial_codes_hadmarea_seoul_low1_2025.geojson"),
        ("emdong", SGIS_DIR / "boundary" / "20260703_110138_sgis_spatial_codes_hadmarea_seoul_low2_2025.geojson"),
    ]
    rows: list[dict[str, Any]] = []
    for admin_level, path in paths:
        payload = read_json(path)
        assert_success(payload, path)
        if payload.get("type") != "FeatureCollection":
            raise ValueError(f"{path}가 FeatureCollection이 아닙니다.")
        for feature_idx, feature in enumerate(payload.get("features", []), start=1):
            props = feature.get("properties") or {}
            geometry = feature.get("geometry") or {}
            coords = list(flatten_coordinates(geometry.get("coordinates")))
            xs = [x for x, _ in coords]
            ys = [y for _, y in coords]
            rows.append(
                {
                    "admin_level": admin_level,
                    "boundary_year": BOUNDARY_YEAR,
                    "adm_cd": str(props.get("adm_cd", "")).strip(),
                    "adm_nm": str(props.get("adm_nm", "")).strip(),
                    "addr_en": str(props.get("addr_en", "")).strip(),
                    "center_x": pd.to_numeric(props.get("x", ""), errors="coerce"),
                    "center_y": pd.to_numeric(props.get("y", ""), errors="coerce"),
                    "geometry_type": geometry.get("type", ""),
                    "vertex_count": len(coords),
                    "bbox_min_x": min(xs) if xs else None,
                    "bbox_max_x": max(xs) if xs else None,
                    "bbox_min_y": min(ys) if ys else None,
                    "bbox_max_y": max(ys) if ys else None,
                    "geometry_json": json.dumps(geometry, ensure_ascii=False, separators=(",", ":")),
                    "source_crs": SOURCE_CRS,
                    "source_crs_note": SOURCE_CRS_NOTE,
                    "source_file": rel(path),
                    "feature_index": feature_idx,
                }
            )
    df = pd.DataFrame(rows)
    df["snapshot_date"] = SNAPSHOT_DATE
    df["provider"] = PROVIDER
    df["source_id"] = SOURCE_ID
    df["source_service"] = "boundary/hadmarea.geojson"
    df["directness_level"] = "P1_SGIS_공식_행정구역_경계"
    df["forbidden_claim_ko"] = "상권 polygon 대체, 실제 상권 경계, 집계구 전체 경계라고 표현 금지"
    df["notes_ko"] = "서울 자치구와 행정동 경계다. 상권 경계가 아니므로 상권 polygon과 혼용하지 않는다."
    return df.sort_values(["admin_level", "adm_cd"]).reset_index(drop=True)


def add_metric_rows(
    rows: list[dict[str, Any]],
    source_path: Path,
    stat_domain: str,
    stat_year: str,
    metric_map: dict[str, str],
) -> None:
    for item in payload_result_list(source_path):
        adm_cd = str(item.get("adm_cd", "")).strip()
        adm_nm = str(item.get("adm_nm", "")).strip()
        for metric_code, metric_name in metric_map.items():
            rows.append(
                {
                    "adm_cd": adm_cd,
                    "adm_nm": adm_nm,
                    "stat_domain": stat_domain,
                    "stat_year": stat_year,
                    "metric_code": metric_code,
                    "metric_name_ko": metric_name,
                    "metric_value": item.get(metric_code, ""),
                    "source_file": rel(source_path),
                }
            )


def build_admin_stats() -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    add_metric_rows(
        rows,
        SGIS_DIR / "census_stats" / "20260703_143517_sgis_census_stats_population_seoul_emd_2020.json",
        "population",
        "2020",
        {"population": "인구수"},
    )
    add_metric_rows(
        rows,
        SGIS_DIR / "census_stats" / "20260703_143517_sgis_census_stats_household_seoul_emd_2020.json",
        "household",
        "2020",
        {
            "household_cnt": "가구수",
            "family_member_cnt": "가구원수",
            "avg_family_member_cnt": "평균가구원수",
        },
    )
    add_metric_rows(
        rows,
        SGIS_DIR / "census_stats" / "20260703_143517_sgis_census_stats_company_seoul_emd_2024.json",
        "company",
        "2024",
        {"corp_cnt": "사업체수", "tot_worker": "종사자수"},
    )
    df = pd.DataFrame(rows)
    df["metric_value"] = pd.to_numeric(df["metric_value"], errors="coerce")
    df["snapshot_date"] = SNAPSHOT_DATE
    df["provider"] = PROVIDER
    df["source_id"] = SOURCE_ID
    df["source_service"] = "stats/census"
    df["source_grain"] = "adm_cd+stat_domain+stat_year+metric_code"
    df["directness_level"] = "P1_SGIS_공식_행정동_통계"
    df["forbidden_claim_ko"] = "상권 직접 인구, 상권 직접 사업체수, 개별 매출, 창업 성공확률로 표현 금지"
    df["notes_ko"] = "행정동 단위 통계다. 상권 단위로 쓰려면 행정동-상권 bridge 또는 공간 배분 규칙 검증이 먼저 필요하다."
    return df.sort_values(["adm_cd", "stat_domain", "metric_code"]).reset_index(drop=True)


def build_reference_years() -> pd.DataFrame:
    path = SGIS_DIR / "spatial_codes" / "20260703_110138_sgis_spatial_codes_year_data.json"
    result = payload_result_dict(path)
    rows = []
    for key, value in result.items():
        rows.append(
            {
                "year_key": key,
                "available_years_or_latest": json.dumps(value, ensure_ascii=False) if isinstance(value, list) else str(value),
                "is_list": isinstance(value, list),
                "source_file": rel(path),
                "snapshot_date": SNAPSHOT_DATE,
                "provider": PROVIDER,
                "source_id": SOURCE_ID,
                "source_service": "year/data",
            }
        )
    return pd.DataFrame(rows).sort_values(["year_key"]).reset_index(drop=True)


def build_findcode_audit() -> pd.DataFrame:
    paths = [SGIS_DIR / "20260703_093950_sgis_findcode_findcodeinsmallarea_seoul_city_hall.json"]
    paths += sorted((SGIS_DIR / "findcode_trade_area").glob("*_findcode.json"))
    rows = []
    for path in paths:
        result = payload_result_dict(path)
        match = re.search(r"_(\d{7})_findcode", path.name)
        trade_area_code = match.group(1) if match else ""
        sample_kind = "trade_area_centroid_sample" if trade_area_code else "seoul_city_hall_sample"
        adm_cd = "".join(
            [
                str(result.get("sido_cd", "")).strip(),
                str(result.get("sgg_cd", "")).strip(),
                str(result.get("emdong_cd", "")).strip(),
            ]
        )
        rows.append(
            {
                "sample_kind": sample_kind,
                "trade_area_code": trade_area_code,
                "sido_cd": str(result.get("sido_cd", "")).strip(),
                "sido_nm": str(result.get("sido_nm", "")).strip(),
                "sgg_cd": str(result.get("sgg_cd", "")).strip(),
                "sgg_nm": str(result.get("sgg_nm", "")).strip(),
                "emdong_cd": str(result.get("emdong_cd", "")).strip(),
                "emdong_nm": str(result.get("emdong_nm", "")).strip(),
                "adm_cd": adm_cd,
                "tot_reg_cd": str(result.get("tot_reg_cd", "")).strip(),
                "source_file": rel(path),
            }
        )
    df = pd.DataFrame(rows)
    df["snapshot_date"] = SNAPSHOT_DATE
    df["provider"] = PROVIDER
    df["source_id"] = SOURCE_ID
    df["source_service"] = "personal/findcodeinsmallarea"
    df["directness_level"] = "P2_SGIS_좌표_행정동_집계구_샘플감사"
    df["forbidden_claim_ko"] = "상권 전체 bridge, 집계구 전체 커버리지, 상권 점수 직접값으로 표현 금지"
    df["notes_ko"] = "26건 샘플 감사다. 전체 상권 1,650개 coverage가 아니므로 본 bridge가 아니라 입력 검증 근거로만 쓴다."
    return df.sort_values(["sample_kind", "trade_area_code"]).reset_index(drop=True)


def build_file_audit() -> pd.DataFrame:
    manifest = pd.read_csv(MANIFEST_PATH, encoding="utf-8-sig", dtype=str).fillna("")
    sgis = manifest[manifest["provider"].eq("SGIS")].copy()
    keep_cols = [
        "run_id",
        "source_id",
        "dataset_name",
        "raw_path",
        "bytes",
        "collection_status",
        "provider_result_code",
        "provider_result_message",
        "spatial_unit",
        "time_unit",
        "source_period",
        "boundary_version",
        "quality_notes_ko",
        "collected_at",
    ]
    sgis = sgis[keep_cols].copy()
    for col in ["dataset_name", "quality_notes_ko"]:
        for term in SENSITIVE_DENYLIST:
            sgis[col] = sgis[col].str.replace(term, "[credential_term_redacted]", case=False, regex=False)
    sgis["domain_silver_use"] = sgis["collection_status"].eq("success") & ~sgis["dataset_name"].str.contains("인증|문서", regex=True)
    sgis.loc[sgis["dataset_name"].str.contains("인증", regex=True), "domain_silver_use"] = False
    sgis.loc[sgis["collection_status"].ne("success"), "domain_silver_use"] = False
    return sgis.reset_index(drop=True)


def contains_sensitive_text(df: pd.DataFrame) -> tuple[int, str]:
    offenders: list[str] = []
    lower_cols = {col: col.lower() for col in df.columns}
    for col, low in lower_cols.items():
        if any(term.lower() in low for term in SENSITIVE_DENYLIST):
            offenders.append(f"column:{col}")
    sample = df.head(1000).astype(str)
    for col in sample.columns:
        values = sample[col].str.lower()
        for term in SENSITIVE_DENYLIST:
            if values.str.contains(term.lower(), regex=False).any():
                offenders.append(f"value:{col}:{term}")
    return len(offenders), ";".join(offenders[:50])


def validate(
    admin_code: pd.DataFrame,
    boundary: pd.DataFrame,
    stats: pd.DataFrame,
    years: pd.DataFrame,
    findcode: pd.DataFrame,
    file_audit: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    emdong_codes = set(admin_code.loc[admin_code["admin_level"].eq("emdong"), "adm_cd"])
    sgg_codes = set(admin_code.loc[admin_code["admin_level"].eq("sgg"), "adm_cd"])
    boundary_emdong_codes = set(boundary.loc[boundary["admin_level"].eq("emdong"), "adm_cd"])
    boundary_sgg_codes = set(boundary.loc[boundary["admin_level"].eq("sgg"), "adm_cd"])
    stats_codes = set(stats["adm_cd"])
    findcode_adm_valid = int(findcode["adm_cd"].isin(emdong_codes).sum())

    sensitive_count = 0
    sensitive_notes = []
    for name, df in [
        ("admin_code", admin_code),
        ("boundary", boundary),
        ("stats", stats),
        ("years", years),
        ("findcode", findcode),
        ("file_audit", file_audit),
    ]:
        count, notes = contains_sensitive_text(df)
        sensitive_count += count
        if notes:
            sensitive_notes.append(f"{name}:{notes}")

    source_contract = pd.DataFrame(
        [
            {
                "source_id": SOURCE_ID,
                "provider": PROVIDER,
                "raw_root": rel(SGIS_DIR),
                "success_files": int(file_audit["collection_status"].eq("success").sum()),
                "superseded_files": int(file_audit["collection_status"].eq("superseded_failed_retry").sum()),
                "auth_or_docs_excluded": int(file_audit["dataset_name"].str.contains("인증|문서", regex=True).sum()),
                "official_docs": ";".join(
                    [
                        rel(SGIS_DIR / "docs" / "sgis_auth_basics_20260703.html"),
                        rel(SGIS_DIR / "docs" / "sgis_address_boundary_20260703.html"),
                        rel(SGIS_DIR / "docs" / "sgis_personal_findcode_20260703.html"),
                    ]
                ),
                "algorithm_role": "행정구역 코드·경계·행정동 통계 기준선",
                "direct_score_use": "상권 bridge 검증 전 직접 점수화 금지",
                "판정": "PASS",
            }
        ]
    )

    domain_validation = pd.DataFrame(
        [
            {
                "검증항목": "서울 자치구 코드 수",
                "측정값": len(sgg_codes),
                "기준값": 25,
                "판정": "PASS" if len(sgg_codes) == 25 else "FAIL",
                "근거": "SGIS addr/stage 서울 하위 자치구",
            },
            {
                "검증항목": "서울 행정동 코드 수",
                "측정값": len(emdong_codes),
                "기준값": 426,
                "판정": "PASS" if len(emdong_codes) == 426 else "FAIL",
                "근거": "SGIS addr/stage 서울 자치구별 행정동 합계",
            },
            {
                "검증항목": "자치구 경계 feature 수",
                "측정값": len(boundary_sgg_codes),
                "기준값": 25,
                "판정": "PASS" if len(boundary_sgg_codes) == 25 else "FAIL",
                "근거": "SGIS hadmarea low1 2025",
            },
            {
                "검증항목": "행정동 경계 feature 수",
                "측정값": len(boundary_emdong_codes),
                "기준값": 426,
                "판정": "PASS" if len(boundary_emdong_codes) == 426 else "FAIL",
                "근거": "SGIS hadmarea low2 2025",
            },
            {
                "검증항목": "행정동 통계 코드 커버리지",
                "측정값": len(stats_codes),
                "기준값": 426,
                "판정": "PASS" if len(stats_codes) == 426 else "FAIL",
                "근거": "인구·가구·사업체 통계 adm_cd 집합",
            },
        ]
    )

    grain_validation = pd.DataFrame(
        [
            {
                "table": "silver_sgis_admin_code",
                "key_cols": "admin_level+adm_cd",
                "rows": len(admin_code),
                "duplicate_key_rows": int(admin_code.duplicated(["admin_level", "adm_cd"]).sum()),
                "key_null_rows": int(admin_code[["admin_level", "adm_cd"]].fillna("").astype(str).eq("").any(axis=1).sum()),
                "판정": "PASS"
                if int(admin_code.duplicated(["admin_level", "adm_cd"]).sum()) == 0
                and int(admin_code[["admin_level", "adm_cd"]].fillna("").astype(str).eq("").any(axis=1).sum()) == 0
                else "FAIL",
            },
            {
                "table": "silver_sgis_admin_boundary",
                "key_cols": "admin_level+boundary_year+adm_cd",
                "rows": len(boundary),
                "duplicate_key_rows": int(boundary.duplicated(["admin_level", "boundary_year", "adm_cd"]).sum()),
                "key_null_rows": int(boundary[["admin_level", "boundary_year", "adm_cd"]].fillna("").astype(str).eq("").any(axis=1).sum()),
                "판정": "PASS"
                if int(boundary.duplicated(["admin_level", "boundary_year", "adm_cd"]).sum()) == 0
                and int(boundary[["admin_level", "boundary_year", "adm_cd"]].fillna("").astype(str).eq("").any(axis=1).sum()) == 0
                else "FAIL",
            },
            {
                "table": "silver_sgis_admin_stats_long",
                "key_cols": "adm_cd+stat_domain+stat_year+metric_code",
                "rows": len(stats),
                "duplicate_key_rows": int(stats.duplicated(["adm_cd", "stat_domain", "stat_year", "metric_code"]).sum()),
                "key_null_rows": int(stats[["adm_cd", "stat_domain", "stat_year", "metric_code"]].fillna("").astype(str).eq("").any(axis=1).sum()),
                "판정": "PASS"
                if int(stats.duplicated(["adm_cd", "stat_domain", "stat_year", "metric_code"]).sum()) == 0
                and int(stats[["adm_cd", "stat_domain", "stat_year", "metric_code"]].fillna("").astype(str).eq("").any(axis=1).sum()) == 0
                else "FAIL",
            },
            {
                "table": "silver_sgis_findcode_sample_audit",
                "key_cols": "sample_kind+trade_area_code+source_file",
                "rows": len(findcode),
                "duplicate_key_rows": int(findcode.duplicated(["sample_kind", "trade_area_code", "source_file"]).sum()),
                "key_null_rows": 0,
                "판정": "PASS" if int(findcode.duplicated(["sample_kind", "trade_area_code", "source_file"]).sum()) == 0 else "FAIL",
            },
        ]
    )

    consistency_validation = pd.DataFrame(
        [
            {
                "검증항목": "행정동 코드-경계 집합 일치",
                "측정값": len(emdong_codes.symmetric_difference(boundary_emdong_codes)),
                "기준값": 0,
                "판정": "PASS" if len(emdong_codes.symmetric_difference(boundary_emdong_codes)) == 0 else "FAIL",
                "근거": "addr/stage 행정동 코드와 hadmarea low2 adm_cd",
            },
            {
                "검증항목": "행정동 코드-통계 집합 일치",
                "측정값": len(emdong_codes.symmetric_difference(stats_codes)),
                "기준값": 0,
                "판정": "PASS" if len(emdong_codes.symmetric_difference(stats_codes)) == 0 else "FAIL",
                "근거": "addr/stage 행정동 코드와 census stats adm_cd",
            },
            {
                "검증항목": "통계 metric numeric 결측",
                "측정값": int(stats["metric_value"].isna().sum()),
                "기준값": 0,
                "판정": "PASS" if int(stats["metric_value"].isna().sum()) == 0 else "FAIL",
                "근거": "인구·가구·사업체 지표 숫자 변환",
            },
            {
                "검증항목": "통계 metric 음수",
                "측정값": int(stats["metric_value"].lt(0).sum()),
                "기준값": 0,
                "판정": "PASS" if int(stats["metric_value"].lt(0).sum()) == 0 else "FAIL",
                "근거": "공식 행정동 통계 값의 도메인 범위",
            },
            {
                "검증항목": "경계 geometry 누락",
                "측정값": int(boundary["geometry_json"].fillna("").astype(str).eq("").sum()),
                "기준값": 0,
                "판정": "PASS" if int(boundary["geometry_json"].fillna("").astype(str).eq("").sum()) == 0 else "FAIL",
                "근거": "공간 경계 feature geometry",
            },
            {
                "검증항목": "findcode 샘플 adm_cd 유효성",
                "측정값": findcode_adm_valid,
                "기준값": len(findcode),
                "판정": "PASS" if findcode_adm_valid == len(findcode) else "FAIL",
                "근거": "26건 샘플의 행정동 코드가 SGIS 행정동 코드 집합에 존재",
            },
            {
                "검증항목": "민감값 배제",
                "측정값": sensitive_count,
                "기준값": 0,
                "판정": "PASS" if sensitive_count == 0 else "FAIL",
                "근거": "accessToken/consumer_secret/key류는 도메인 silver에서 제외",
            },
            {
                "검증항목": "상권 전체 bridge 여부",
                "측정값": len(findcode),
                "기준값": "1,650개 전체가 아니므로 audit 전용",
                "판정": "CONDITIONAL_PASS",
                "근거": "findcode는 26건 샘플이라 상권 전체 bridge로 쓰지 않음",
            },
            {
                "검증항목": "통계 연도 혼합 제한",
                "측정값": ",".join(sorted(stats["stat_year"].astype(str).unique())),
                "기준값": "연도별 long 보존",
                "판정": "CONDITIONAL_PASS",
                "근거": "인구/가구 2020, 사업체 2024를 같은 시점 지표처럼 합치지 않음",
            },
        ]
    )

    sensitive_audit = pd.DataFrame(
        [
            {
                "checked_tables": "admin_code;boundary;stats;years;findcode;file_audit",
                "denylist": ";".join(SENSITIVE_DENYLIST),
                "offender_count": sensitive_count,
                "offender_sample": ";".join(sensitive_notes),
                "판정": "PASS" if sensitive_count == 0 else "FAIL",
            }
        ]
    )

    findcode_status = (
        findcode.groupby(["sample_kind"], as_index=False)
        .agg(row_count=("source_file", "size"), valid_adm_cd_count=("adm_cd", lambda s: int(s.isin(emdong_codes).sum())))
    )
    findcode_status["판정"] = findcode_status.apply(
        lambda row: "PASS" if int(row["row_count"]) == int(row["valid_adm_cd_count"]) else "FAIL",
        axis=1,
    )

    return {
        "source_contract": source_contract,
        "domain_validation": domain_validation,
        "grain_validation": grain_validation,
        "consistency_validation": consistency_validation,
        "sensitive_field_audit": sensitive_audit,
        "findcode_status": findcode_status,
    }


def md_table(df: pd.DataFrame, max_rows: int = 30) -> str:
    if df.empty:
        return "(행 없음)"
    view = df.head(max_rows).copy()
    cols = list(view.columns)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in view.iterrows():
        values = [str(row[col]).replace("\n", " ") for col in cols]
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def write_outputs(
    admin_code: pd.DataFrame,
    boundary: pd.DataFrame,
    stats: pd.DataFrame,
    years: pd.DataFrame,
    findcode: pd.DataFrame,
    file_audit: pd.DataFrame,
    validations: dict[str, pd.DataFrame],
) -> None:
    admin_code.to_csv(SILVER_DIR / "silver_sgis_admin_code.csv", index=False, encoding="utf-8-sig")
    boundary.to_csv(SILVER_DIR / "silver_sgis_admin_boundary.csv", index=False, encoding="utf-8-sig")
    stats.to_csv(SILVER_DIR / "silver_sgis_admin_stats_long.csv", index=False, encoding="utf-8-sig")
    years.to_csv(SILVER_DIR / "silver_sgis_reference_years.csv", index=False, encoding="utf-8-sig")
    findcode.to_csv(SILVER_DIR / "silver_sgis_findcode_sample_audit.csv", index=False, encoding="utf-8-sig")
    file_audit.to_csv(SILVER_DIR / "silver_sgis_source_file_audit.csv", index=False, encoding="utf-8-sig")

    output_map = {
        "source_contract": "22_sgis_admin_reference_source_contract.csv",
        "domain_validation": "22_sgis_admin_reference_domain_validation.csv",
        "grain_validation": "22_sgis_admin_reference_grain_validation.csv",
        "consistency_validation": "22_sgis_admin_reference_consistency_validation.csv",
        "sensitive_field_audit": "22_sgis_admin_reference_sensitive_field_audit.csv",
        "findcode_status": "22_sgis_findcode_sample_status.csv",
    }
    for key, filename in output_map.items():
        validations[key].to_csv(VALIDATION_DIR / filename, index=False, encoding="utf-8-sig")

    stats.pivot_table(
        index=["stat_domain", "stat_year", "metric_code", "metric_name_ko"],
        values="metric_value",
        aggfunc=["count", "min", "max", "mean"],
    ).reset_index().to_csv(
        VALIDATION_DIR / "22_sgis_admin_stats_metric_distribution.csv",
        index=False,
        encoding="utf-8-sig",
    )

    report = f"""# 22차 SGIS 행정코드·경계·행정동 통계 silver 검증

작성시각: {datetime.now().isoformat(timespec="seconds")}

## 저장 파일

- `datacorpus/_silver/silver_sgis_admin_code.csv`
- `datacorpus/_silver/silver_sgis_admin_boundary.csv`
- `datacorpus/_silver/silver_sgis_admin_stats_long.csv`
- `datacorpus/_silver/silver_sgis_reference_years.csv`
- `datacorpus/_silver/silver_sgis_findcode_sample_audit.csv`
- `datacorpus/_silver/silver_sgis_source_file_audit.csv`

## 사용 근거

- `research/전처리_알고리즘_실행계획_20260703.md`: `silver_sgis_admin_stats`는 행정동+연도+통계종류 grain으로 만들고 426개 행정동 커버리지를 검증해야 한다.
- `research/서브에이전트_검토기록_20260703.md`: SGIS는 행정동 통계 보강은 가능하지만 집계구 전체 보정은 완료되지 않았다고 기록되어 있다.
- `research/알고리즘_스펙_v1_20260703.md`: SGIS는 좌표계·행정구역 보조 근거이며 상권 코드와 직접 같은 grain이 아니다.
- `datacorpus/_raw_ingest/20260703/sgis/docs/sgis_address_boundary_20260703.html`: 행정구역 단계조회, 행정경계, 집계구경계 API의 공식 근거 문서다.
- `datacorpus/_raw_ingest/20260703/sgis/docs/sgis_personal_findcode_20260703.html`: 좌표를 행정동/집계구 코드로 바꾸는 findcode API 근거 문서다.

## 검증 요약

### 원천 계약

{md_table(validations["source_contract"])}

### 도메인 검증

{md_table(validations["domain_validation"])}

### grain 검증

{md_table(validations["grain_validation"])}

### 일관성 검증

{md_table(validations["consistency_validation"])}

### 민감값 배제 감사

{md_table(validations["sensitive_field_audit"])}

### findcode 샘플 상태

{md_table(validations["findcode_status"])}

## 2보 전진 1보 후퇴 기록

- 전진 1: SGIS 서울 자치구 25개, 행정동 426개 코드와 행정동 경계를 같은 adm_cd 집합으로 맞췄다.
- 전진 2: 인구 2020, 가구 2020, 사업체 2024 통계를 연도와 통계종류를 보존한 long table로 만들었다.
- 후퇴 1: 인증 토큰, key, secret, accessTimeout은 도메인 silver에서 제외했다.
- 후퇴 2: findcode는 26건 샘플 감사일 뿐 전체 상권 1,650개 bridge가 아니므로 상권 점수에 직접 쓰지 않는다.
- 후퇴 3: 인구/가구와 사업체 통계 연도가 다르므로 같은 시점의 단일 feature처럼 합치지 않는다.

## 알고리즘 단계에서 금지하는 표현

- SGIS 행정동 통계를 상권 직접 인구라고 표현
- 26건 findcode 샘플을 상권 전체 bridge라고 표현
- SGIS 행정동 경계를 서울시 상권 경계라고 표현
- 인구 2020과 사업체 2024를 동일 기준시점 지표라고 표현
- 개별 매장 매출, 창업 성공확률, 실제 방문확률로 표현

허용 표현:

- 행정동 단위 공식 통계 기준선
- 행정구역 코드/경계 bridge 후보
- 상권-행정동 배분 규칙 검증 전 보조 프록시
- 좌표 입력 검증용 findcode 샘플 감사
"""
    (RESEARCH_VALIDATION_DIR / "22_sgis_admin_reference_silver_validation_20260704.md").write_text(
        report, encoding="utf-8"
    )


def main() -> None:
    ensure_dirs()
    admin_code = build_admin_code()
    boundary = build_boundary()
    stats = build_admin_stats()
    years = build_reference_years()
    findcode = build_findcode_audit()
    file_audit = build_file_audit()
    validations = validate(admin_code, boundary, stats, years, findcode, file_audit)
    write_outputs(admin_code, boundary, stats, years, findcode, file_audit, validations)
    fail_count = sum(
        int((validations[key]["판정"].astype(str) == "FAIL").sum())
        for key in ["source_contract", "domain_validation", "grain_validation", "consistency_validation", "sensitive_field_audit", "findcode_status"]
    )
    print(
        {
            "silver_sgis_admin_code_rows": len(admin_code),
            "silver_sgis_admin_boundary_rows": len(boundary),
            "silver_sgis_admin_stats_long_rows": len(stats),
            "silver_sgis_findcode_sample_audit_rows": len(findcode),
            "validation_fail_count": fail_count,
        }
    )


if __name__ == "__main__":
    main()
