# -*- coding: utf-8 -*-
"""서울 인허가 사업체의 365일 생존과 과거 입지점수의 연관성을 검증한다.

이 파일은 테스트 픽스처가 아니라 재실행 가능한 분석·기록 도구다. 기본 입력은
다업종 공통 Silver이고, 아직 공통 파일이 없으면 기존 음식점 상권매칭 Silver를
명시적으로 fallback으로 사용한다.

시간 누수를 막기 위해 개업일이 속한 분기의 점수가 아니라 개업 시점에 이미
완료된 직전 분기의 공식 v2.6 점수 캐시를 사용한다. 개업일부터 365일을 관측할
수 없는 우측검열 행과 폐업 상태인데 폐업일이 없는 행은 정답에서 제외한다.
결과는 점수의 순위 예측력을 검증할 뿐 인과효과나 개별 인허가 레코드의 생존
보장이 아니다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Iterable

import numpy as np
import pandas as pd
from pyproj import CRS, Transformer
from shapely import points as shapely_points

from evaluate_score_predictive_validity import (
    add_quarters,
    average_precision,
    group_percentile,
    roc_auc,
    sha256_file,
)
from preprocess_rule_engine_localdata_trade_area_spatial_match import (
    LOCALDATA_SOURCE_CRS,
    load_trade_area_polygons,
)


ROOT = Path(__file__).resolve().parents[1]
SILVER = ROOT / "datacorpus" / "_silver"
GOLD = ROOT / "datacorpus" / "_gold"
VALIDATION_ROOT = ROOT / "datacorpus" / "_score_predictive_validation"

DEFAULT_INPUT = SILVER / "silver_localdata_business_license.csv"
FOOD_FALLBACK = SILVER / "silver_localdata_food_license_trade_area_match.csv"
FOOD_BRIDGE = GOLD / "gold_localdata_food_bridge_resolution_v02.csv"
DEFAULT_OUTPUT = VALIDATION_ROOT / "business_survival_v1_20260717"

VALIDATION_VERSION = "localdata_business_survival.v1.0-20260717"
HORIZON_DAYS = 365
SCORE_LAG_QUARTERS = 1

ALIASES: dict[str, tuple[str, ...]] = {
    "source_id": ("source_id", "원천_ID"),
    "service_code": ("service_code", "서비스코드"),
    "license_category": (
        "license_category",
        "service_name_ko",
        "인허가업종",
        "업종구분",
    ),
    "business_id": ("business_id", "management_no", "관리번호", "MGTNO"),
    "agency_code": (
        "agency_code",
        "licensing_agency_code",
        "인허가기관코드",
        "OPNSFTEAMCODE",
    ),
    "open_date": ("open_date", "인허가일자", "APVPERMYMD"),
    "close_date": ("close_date", "폐업일자", "DCBYMD"),
    "snapshot_date": (
        "snapshot_date",
        "source_snapshot_date",
        "기준일자",
        "as_of_date",
    ),
    "status_group": (
        "status_group",
        "trade_status_name",
        "상태그룹",
        "영업상태명",
        "TRDSTATENM",
    ),
    "industry_code": (
        "서비스_업종_코드",
        "industry_code",
        "candidate_서비스_업종_코드",
    ),
    "industry_name": (
        "서비스_업종_코드_명",
        "industry_name",
        "candidate_서비스_업종_코드_명",
    ),
    "uptae_name": (
        "source_industry_name",
        "업태명",
        "UPTAENM",
        "business_type_name",
    ),
    "trade_area_code": ("상권_코드", "trade_area_code"),
    "trade_area_name": ("상권_코드_명", "trade_area_name"),
    "district_code": (
        "상권_자치구_코드",
        "자치구_코드",
        "district_code",
    ),
    "district_name": (
        "상권_자치구_코드_명",
        "자치구_코드_명",
        "district_name",
        "address_sgg_name",
    ),
    "spatial_status": ("match_status", "spatial_match_status"),
    "x_5181": ("x_epsg5181", "X_EPSG5181"),
    "y_5181": ("y_epsg5181", "Y_EPSG5181"),
    "x_5174": ("x_epsg5174", "X_EPSG5174", "X"),
    "y_5174": ("y_epsg5174", "Y_EPSG5174", "Y"),
    "longitude": ("longitude", "경도", "lon", "lng"),
    "latitude": ("latitude", "위도", "lat"),
    "source_row": ("원천행번호", "source_row_number"),
}


def clean_text(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "<na>"} else text


def clean_text_series(values: pd.Series) -> pd.Series:
    """Vectorized equivalent of clean_text for large Silver columns."""
    cleaned = values.astype("string").fillna("").str.strip()
    invalid = cleaned.str.lower().isin({"nan", "none", "<na>"})
    return cleaned.mask(invalid, "")


def bool_text(value: Any) -> bool:
    return clean_text(value).lower() in {"true", "1", "y", "yes", "t"}


def resolve_alias(columns: Iterable[str], logical_name: str) -> str | None:
    present = set(columns)
    return next((name for name in ALIASES[logical_name] if name in present), None)


def quarter_from_date(values: pd.Series) -> pd.Series:
    return values.dt.year.astype("Int64") * 10 + ((values.dt.month - 1) // 3 + 1).astype(
        "Int64"
    )


def split_from_quarter(value: Any) -> str:
    quarter = int(value)
    if quarter <= 20234:
        return "development"
    if quarter <= 20244:
        return "validation"
    return "holdout"


def resolve_input(requested: str | None) -> tuple[Path, str]:
    if requested:
        path = Path(requested).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(
                f"인허가 입력 파일을 찾을 수 없습니다: {path}\n"
                "필수 논리 필드: business_id/관리번호, open_date/인허가일자, "
                "close_date/폐업일자, snapshot_date, 상권코드 또는 좌표, "
                "서비스업종코드 또는 업태명"
            )
        return path, "explicit"
    if DEFAULT_INPUT.exists():
        return DEFAULT_INPUT, "business_silver"
    if FOOD_FALLBACK.exists():
        return FOOD_FALLBACK, "food_spatial_match_fallback"
    raise FileNotFoundError(
        "다업종 공통 Silver와 음식점 fallback을 모두 찾을 수 없습니다.\n"
        f"- {DEFAULT_INPUT}\n- {FOOD_FALLBACK}\n"
        "수집·정규화 후 다시 실행하십시오."
    )


def read_input(path: Path, snapshot_override: str | None) -> tuple[pd.DataFrame, dict[str, Any]]:
    if path.suffix.lower() == ".parquet":
        header_columns = list(pd.read_parquet(path).columns)
    else:
        header_columns = list(pd.read_csv(path, encoding="utf-8-sig", nrows=0).columns)

    resolved = {logical: resolve_alias(header_columns, logical) for logical in ALIASES}
    missing_required = [
        logical for logical in ("business_id", "open_date") if resolved[logical] is None
    ]
    if missing_required:
        raise ValueError(
            "인허가 입력 계약에 필요한 필드가 없습니다: "
            + ", ".join(missing_required)
            + f"\n실제 컬럼: {header_columns}"
        )
    if resolved["snapshot_date"] is None and snapshot_override is None:
        raise ValueError(
            "우측검열 판정에 snapshot_date/as_of_date가 필요합니다. "
            "--snapshot-date YYYY-MM-DD를 지정할 수도 있습니다."
        )
    has_area = resolved["trade_area_code"] is not None
    has_xy = (
        resolved["x_5181"] is not None
        and resolved["y_5181"] is not None
    ) or (
        resolved["x_5174"] is not None
        and resolved["y_5174"] is not None
    ) or (
        resolved["longitude"] is not None
        and resolved["latitude"] is not None
    )
    if not has_area and not has_xy:
        raise ValueError(
            "상권 연결에 상권_코드 또는 좌표쌍(x/y EPSG:5181, EPSG:5174, 경도/위도)이 필요합니다."
        )

    usecols = sorted({value for value in resolved.values() if value is not None})
    if path.suffix.lower() == ".parquet":
        source = pd.read_parquet(path, columns=usecols)
    else:
        source = pd.read_csv(
            path,
            encoding="utf-8-sig",
            dtype=str,
            usecols=usecols,
            low_memory=False,
            memory_map=True,
        )

    def text_col(logical: str, default: str = "") -> pd.Series:
        actual = resolved[logical]
        if actual is None:
            return pd.Series(default, index=source.index, dtype="string")
        return clean_text_series(source[actual])

    out = pd.DataFrame(index=source.index)
    out["_source_order"] = np.arange(len(source), dtype=np.int64)
    out["source_id"] = text_col("source_id", path.stem)
    out.loc[out["source_id"].eq(""), "source_id"] = path.stem
    out["service_code"] = text_col("service_code")
    out["license_category"] = text_col("license_category")
    out["business_id"] = text_col("business_id")
    out["agency_code"] = text_col("agency_code")
    out["open_date"] = pd.to_datetime(
        text_col("open_date"), errors="coerce", format="mixed", cache=True
    )
    out["close_date"] = pd.to_datetime(
        text_col("close_date"), errors="coerce", format="mixed", cache=True
    )
    if snapshot_override:
        parsed_snapshot = pd.to_datetime(snapshot_override, errors="raise")
        out["snapshot_date"] = parsed_snapshot
    else:
        out["snapshot_date"] = pd.to_datetime(
            text_col("snapshot_date"), errors="coerce", format="mixed", cache=True
        )
    out["status_group"] = text_col("status_group")
    out["industry_code"] = text_col("industry_code")
    out["industry_name"] = text_col("industry_name")
    out["uptae_name"] = text_col("uptae_name")
    out["trade_area_code"] = text_col("trade_area_code")
    out["trade_area_name"] = text_col("trade_area_name")
    out["district_code"] = text_col("district_code")
    out["district_name"] = text_col("district_name")
    out["source_spatial_status"] = text_col("spatial_status")
    out["source_row"] = text_col("source_row")
    for logical in ("x_5181", "y_5181", "x_5174", "y_5174", "longitude", "latitude"):
        actual = resolved[logical]
        out[logical] = (
            pd.to_numeric(source[actual], errors="coerce")
            if actual is not None
            else np.nan
        )
    out["mapping_tier"] = np.where(out["industry_code"].ne(""), "provided", "")
    return out, {
        "resolved_columns": resolved,
        "input_rows": int(len(out)),
        "input_columns": header_columns,
    }


def attach_food_bridge(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    needs_mapping = df["industry_code"].eq("")
    if not needs_mapping.any():
        return df, {"bridge_used": False, "bridge_rows": 0}
    if not FOOD_BRIDGE.exists():
        return df, {
            "bridge_used": False,
            "bridge_rows": 0,
            "bridge_missing": str(FOOD_BRIDGE),
        }
    bridge = pd.read_csv(FOOD_BRIDGE, encoding="utf-8-sig", dtype=str).fillna("")
    required = {
        "license_category",
        "업태명",
        "candidate_서비스_업종_코드",
        "candidate_서비스_업종_코드_명",
        "mapping_status",
        "final_candidate_gold_include",
    }
    if not required.issubset(bridge.columns):
        raise ValueError(f"음식업 bridge 계약 불일치: {sorted(required - set(bridge.columns))}")
    bridge = bridge[
        bridge["final_candidate_gold_include"].map(bool_text)
        & bridge["candidate_서비스_업종_코드"].map(clean_text).ne("")
    ].copy()
    bridge["license_category"] = bridge["license_category"].map(clean_text)
    bridge["uptae_name"] = bridge["업태명"].map(clean_text)
    if bridge.duplicated(["license_category", "uptae_name"]).any():
        raise ValueError("음식업 bridge에 license_category×업태명 중복이 있습니다.")
    bridge = bridge.rename(
        columns={
            "candidate_서비스_업종_코드": "_bridge_industry_code",
            "candidate_서비스_업종_코드_명": "_bridge_industry_name",
            "mapping_status": "_bridge_mapping_status",
        }
    )
    out = df.merge(
        bridge[
            [
                "license_category",
                "uptae_name",
                "_bridge_industry_code",
                "_bridge_industry_name",
                "_bridge_mapping_status",
            ]
        ],
        on=["license_category", "uptae_name"],
        how="left",
        validate="many_to_one",
    )
    fill_mask = out["industry_code"].eq("") & out["_bridge_industry_code"].map(clean_text).ne("")
    out.loc[fill_mask, "industry_code"] = out.loc[fill_mask, "_bridge_industry_code"].map(
        clean_text
    )
    out.loc[fill_mask & out["industry_name"].eq(""), "industry_name"] = out.loc[
        fill_mask & out["industry_name"].eq(""), "_bridge_industry_name"
    ].map(clean_text)
    out.loc[fill_mask, "mapping_tier"] = out.loc[fill_mask, "_bridge_mapping_status"].map(
        clean_text
    )
    out = out.drop(
        columns=[
            "_bridge_industry_code",
            "_bridge_industry_name",
            "_bridge_mapping_status",
        ]
    )
    return out, {
        "bridge_used": True,
        "bridge_rows": int(len(bridge)),
        "bridge_mapped_rows": int(fill_mask.sum()),
        "bridge_sha256": sha256_file(FOOD_BRIDGE),
    }


def attach_spatial_codes(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    out = df.copy()
    prior_status = out["source_spatial_status"]
    out["spatial_method"] = np.where(
        out["trade_area_code"].ne(""), "provided", prior_status
    )
    # 공간매칭 산출물을 fallback 입력으로 쓸 때 polygon 밖/좌표무효 행을 다시
    # 계산하지 않는다. 새 공통 Silver에서 아직 시도하지 않은 좌표만 자동 매칭한다.
    need = out["trade_area_code"].eq("") & prior_status.eq("")
    if not need.any():
        return out, {
            "geometry_used": False,
            "spatially_resolved_rows": 0,
            "preexisting_unmatched_or_invalid_rows": int(
                (out["trade_area_code"].eq("") & prior_status.ne("")).sum()
            ),
        }

    polygons, attrs, tree, polygon_crs = load_trade_area_polygons()
    x = out["x_5181"].copy()
    y = out["y_5181"].copy()
    need_5174 = need & (x.isna() | y.isna()) & out["x_5174"].notna() & out["y_5174"].notna()
    if need_5174.any():
        transformer = Transformer.from_crs(LOCALDATA_SOURCE_CRS, polygon_crs, always_xy=True)
        tx, ty = transformer.transform(
            out.loc[need_5174, "x_5174"].astype(float).to_numpy(),
            out.loc[need_5174, "y_5174"].astype(float).to_numpy(),
        )
        x.loc[need_5174] = tx
        y.loc[need_5174] = ty
    need_wgs84 = (
        need
        & (x.isna() | y.isna())
        & out["longitude"].notna()
        & out["latitude"].notna()
    )
    if need_wgs84.any():
        transformer = Transformer.from_crs(CRS.from_epsg(4326), polygon_crs, always_xy=True)
        tx, ty = transformer.transform(
            out.loc[need_wgs84, "longitude"].astype(float).to_numpy(),
            out.loc[need_wgs84, "latitude"].astype(float).to_numpy(),
        )
        x.loc[need_wgs84] = tx
        y.loc[need_wgs84] = ty

    coordinate_rows = need & x.notna() & y.notna()
    coordinate_index = out.index[coordinate_rows]
    if len(coordinate_index) == 0:
        return out, {
            "geometry_used": True,
            "coordinate_candidate_rows": 0,
            "spatially_resolved_rows": 0,
            "unique_coordinate_pairs": 0,
        }

    # The former row loop called STRtree.query/nearest and DataFrame.at once per
    # permit (nearly 900k calls).  Shapely 2 accepts an array of points and
    # returns all covered_by pairs in one native query.  Factorizing rounded
    # coordinates preserves the old 6-decimal cache and duplicate semantics.
    rounded_x = np.round(x.loc[coordinate_index].astype(float).to_numpy(), 6)
    rounded_y = np.round(y.loc[coordinate_index].astype(float).to_numpy(), 6)
    coordinate_keys = np.empty(
        len(coordinate_index), dtype=[("x", "<f8"), ("y", "<f8")]
    )
    coordinate_keys["x"] = rounded_x
    coordinate_keys["y"] = rounded_y
    unique_keys, inverse = np.unique(coordinate_keys, return_inverse=True)
    unique_points = shapely_points(unique_keys["x"], unique_keys["y"])
    pair_indices = tree.query(unique_points, predicate="covered_by")
    point_indices = np.asarray(pair_indices[0], dtype=np.int64)
    polygon_indices = np.asarray(pair_indices[1], dtype=np.int64)

    unique_count = len(unique_keys)
    match_counts = np.bincount(point_indices, minlength=unique_count)
    chosen_polygon = np.full(unique_count, -1, dtype=np.int64)
    if len(point_indices):
        polygon_areas = np.asarray([polygon.area for polygon in polygons], dtype=float)
        # Primary order is point index, secondary order is polygon area.  The
        # first pair per point therefore matches the previous smallest-area rule.
        order = np.lexsort((polygon_areas[polygon_indices], point_indices))
        ordered_points = point_indices[order]
        first_for_point = np.r_[True, ordered_points[1:] != ordered_points[:-1]]
        chosen_polygon[ordered_points[first_for_point]] = polygon_indices[order][
            first_for_point
        ]

    statuses = np.full(unique_count, "unmatched_nearest_candidate", dtype=object)
    statuses[match_counts == 1] = "polygon_match"
    statuses[match_counts > 1] = "multi_polygon_match_choose_smallest_area"
    matched_unique = chosen_polygon >= 0

    attr_arrays = {
        "trade_area_code": np.asarray(
            [clean_text(item.get("상권_코드")) for item in attrs], dtype=object
        ),
        "trade_area_name": np.asarray(
            [clean_text(item.get("상권_코드_명")) for item in attrs], dtype=object
        ),
        "district_code": np.asarray(
            [clean_text(item.get("상권_자치구_코드")) for item in attrs], dtype=object
        ),
        "district_name": np.asarray(
            [clean_text(item.get("상권_자치구_코드_명")) for item in attrs], dtype=object
        ),
    }
    unique_values: dict[str, np.ndarray] = {}
    for column, lookup in attr_arrays.items():
        values = np.full(unique_count, "", dtype=object)
        values[matched_unique] = lookup[chosen_polygon[matched_unique]]
        unique_values[column] = values

    out.loc[coordinate_index, "spatial_method"] = statuses[inverse]
    for column, values in unique_values.items():
        out.loc[coordinate_index, column] = values[inverse]
    resolved = int(np.count_nonzero(unique_values["trade_area_code"][inverse] != ""))
    return out, {
        "geometry_used": True,
        "coordinate_candidate_rows": int(coordinate_rows.sum()),
        "spatially_resolved_rows": int(resolved),
        "unique_coordinate_pairs": int(unique_count),
    }


def deduplicate(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    out = df.copy()
    out["entity_key"] = (
        out["source_id"]
        + "|"
        + out["license_category"]
        + "|"
        + out["business_id"]
        + "|"
        + out["agency_code"]
    )
    valid_id = out["business_id"].ne("")
    out = out[valid_id].copy()
    duplicate_mask = out.duplicated("entity_key", keep=False)
    conflict_fields = ["open_date", "close_date", "trade_area_code", "industry_code"]
    conflicts = (
        out.loc[duplicate_mask]
        .groupby("entity_key", dropna=False)[conflict_fields]
        .nunique(dropna=True)
    )
    conflict_keys = set(conflicts.index[(conflicts > 1).any(axis=1)])
    conflict_rows = int(out["entity_key"].isin(conflict_keys).sum())
    out = out[~out["entity_key"].isin(conflict_keys)].copy()
    duplicate_rows_before_drop = int(out.duplicated("entity_key", keep=False).sum())
    out = (
        out.sort_values("_source_order", kind="mergesort")
        .drop_duplicates("entity_key", keep="last")
        .reset_index(drop=True)
    )
    return out, {
        "missing_business_id_rows": int((~valid_id).sum()),
        "duplicate_rows_before_drop": duplicate_rows_before_drop,
        "conflicting_entity_keys": int(len(conflict_keys)),
        "conflicting_rows_excluded": conflict_rows,
        "deduplicated_rows": int(len(out)),
    }


def discover_score_cache() -> tuple[list[Path], dict[str, Any]]:
    candidates: dict[Path, list[Path]] = {}
    for path in VALIDATION_ROOT.glob("*/quarter_scores/official_v2_6_scores_*.parquet"):
        candidates.setdefault(path.parent, []).append(path)
    if not candidates:
        raise FileNotFoundError(
            "공식 v2.6 분기 점수 캐시를 찾을 수 없습니다: "
            "datacorpus/_score_predictive_validation/*/quarter_scores/"
        )
    selected_dir, paths = max(
        candidates.items(),
        key=lambda item: (len(item[1]), max(p.stat().st_mtime for p in item[1])),
    )

    def cache_quarter(path: Path) -> int:
        return int(path.stem.rsplit("_", 1)[-1])

    paths = sorted(paths, key=cache_quarter)
    return paths, {
        "score_cache_dir": str(selected_dir.relative_to(ROOT)).replace("\\", "/"),
        "score_cache_files": int(len(paths)),
        "score_cache_first_quarter": cache_quarter(paths[0]),
        "score_cache_last_quarter": cache_quarter(paths[-1]),
    }


def load_score_cache(paths: list[Path]) -> tuple[pd.DataFrame, dict[str, Any]]:
    frames: list[pd.DataFrame] = []
    digest = hashlib.sha256()
    required = {
        "기준_년분기_코드",
        "상권_코드",
        "서비스_업종_코드",
        "current_location_score",
        "score_version",
    }
    optional = ["grade", "data_reliability_score"]
    for path in paths:
        frame = pd.read_parquet(path)
        missing = required - set(frame.columns)
        if missing:
            raise ValueError(f"점수 캐시 계약 불일치 {path.name}: {sorted(missing)}")
        keep = list(required) + [col for col in optional if col in frame.columns]
        frame = frame[keep].copy()
        frames.append(frame)
        digest.update(path.name.encode("utf-8"))
        digest.update(sha256_file(path).encode("ascii"))
    scores = pd.concat(frames, ignore_index=True)
    scores = scores.rename(
        columns={
            "기준_년분기_코드": "score_quarter",
            "상권_코드": "trade_area_code",
            "서비스_업종_코드": "industry_code",
        }
    )
    scores["score_quarter"] = pd.to_numeric(scores["score_quarter"], errors="raise").astype(int)
    scores["trade_area_code"] = scores["trade_area_code"].map(clean_text)
    scores["industry_code"] = scores["industry_code"].map(clean_text)
    scores["current_location_score"] = pd.to_numeric(
        scores["current_location_score"], errors="coerce"
    )
    key = ["score_quarter", "trade_area_code", "industry_code"]
    if scores.duplicated(key).any():
        raise ValueError("점수 캐시에 분기×상권×업종 중복 키가 있습니다.")
    scores["score_percentile"] = scores.groupby(
        ["score_quarter", "industry_code"], group_keys=False
    )["current_location_score"].apply(group_percentile)
    versions = sorted(scores["score_version"].dropna().astype(str).unique())
    return scores, {
        "score_rows": int(len(scores)),
        "score_versions": versions,
        "score_cache_combined_sha256": digest.hexdigest(),
    }


def fit_logistic_calibrator(x: pd.Series, y: pd.Series) -> tuple[float, float]:
    pair = pd.DataFrame({"x": x, "y": y}).dropna()
    if len(pair) < 100 or pair["y"].nunique() < 2:
        raise ValueError("development 구간에 확률 보정용 양·음성 표본이 충분하지 않습니다.")
    x_values = pair["x"].astype(float).to_numpy()
    y_values = pair["y"].astype(float).to_numpy()
    design = np.column_stack([np.ones(len(pair)), x_values])
    base = float(np.clip(y_values.mean(), 1e-6, 1 - 1e-6))
    beta = np.array([math.log(base / (1 - base)), 0.0], dtype=float)
    ridge = np.diag([1e-8, 1e-6])
    for _ in range(100):
        linear = np.clip(design @ beta, -30, 30)
        probability = 1.0 / (1.0 + np.exp(-linear))
        weights = np.clip(probability * (1 - probability), 1e-8, None)
        hessian = design.T @ (design * weights[:, None]) + ridge
        gradient = design.T @ (y_values - probability) - ridge @ beta
        step = np.linalg.solve(hessian, gradient)
        beta += step
        if float(np.max(np.abs(step))) < 1e-10:
            break
    return float(beta[0]), float(beta[1])


def calibrated_probability(signal: pd.Series, intercept: float, slope: float) -> pd.Series:
    linear = np.clip(intercept + slope * signal.astype(float), -30, 30)
    return 1.0 / (1.0 + np.exp(-linear))


def metric_row(
    frame: pd.DataFrame,
    *,
    segment_type: str,
    segment: str,
) -> dict[str, Any]:
    data = frame[
        [
            "survived_365d",
            "score_percentile",
            "predicted_survival_probability",
            "constant_development_probability",
        ]
    ].dropna()
    n = int(len(data))
    survivors = int(data["survived_365d"].sum()) if n else 0
    survival_rate = survivors / n if n else float("nan")
    failure_rate = 1 - survival_rate if n else float("nan")
    top = data[data["score_percentile"] >= 0.80]
    bottom = data[data["score_percentile"] <= 0.20]
    top_survival = float(top["survived_365d"].mean()) if len(top) else float("nan")
    bottom_failure = (
        float((1 - bottom["survived_365d"]).mean()) if len(bottom) else float("nan")
    )
    brier = (
        float(
            np.mean(
                (
                    data["predicted_survival_probability"].astype(float)
                    - data["survived_365d"].astype(float)
                )
                ** 2
            )
        )
        if n
        else float("nan")
    )
    baseline_brier = (
        float(
            np.mean(
                (
                    data["constant_development_probability"].astype(float)
                    - data["survived_365d"].astype(float)
                )
                ** 2
            )
        )
        if n
        else float("nan")
    )
    return {
        "segment_type": segment_type,
        "segment": segment,
        "n": n,
        "survivors": survivors,
        "failures": n - survivors,
        "survival_rate": survival_rate,
        "auc": roc_auc(data["survived_365d"], data["score_percentile"]),
        "average_precision": average_precision(
            data["survived_365d"], data["score_percentile"]
        ),
        "average_precision_lift": (
            average_precision(data["survived_365d"], data["score_percentile"])
            / survival_rate
            if survival_rate
            else float("nan")
        ),
        "top20_rows": int(len(top)),
        "top20_survival_rate": top_survival,
        "top20_survival_lift": (
            top_survival / survival_rate
            if survival_rate and np.isfinite(top_survival)
            else float("nan")
        ),
        "bottom20_rows": int(len(bottom)),
        "bottom20_failure_rate": bottom_failure,
        "bottom20_failure_lift": (
            bottom_failure / failure_rate
            if failure_rate and np.isfinite(bottom_failure)
            else float("nan")
        ),
        "brier_score_dev_calibrated": brier,
        "brier_score_development_constant": baseline_brier,
        "brier_skill_vs_development_constant": (
            1.0 - brier / baseline_brier
            if baseline_brier and np.isfinite(brier)
            else float("nan")
        ),
    }


def build_metrics(cohort: pd.DataFrame, min_segment_size: int) -> pd.DataFrame:
    rows = [metric_row(cohort, segment_type="overall", segment="all")]
    for split, frame in cohort.groupby("split", sort=False):
        rows.append(metric_row(frame, segment_type="split", segment=str(split)))
    segment_specs = [
        ("industry", "industry_code"),
        ("district", "district_name"),
        ("open_quarter", "open_quarter"),
        ("mapping_tier", "mapping_tier"),
    ]
    for segment_type, column in segment_specs:
        for value, frame in cohort.groupby(column, dropna=False):
            if len(frame) < min_segment_size:
                continue
            segment = "unknown" if pd.isna(value) or clean_text(value) == "" else clean_text(value)
            rows.append(metric_row(frame, segment_type=segment_type, segment=segment))
    return pd.DataFrame(rows)


def build_calibration(cohort: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for split, frame in cohort.groupby("split", sort=False):
        valid = frame[
            ["predicted_survival_probability", "survived_365d"]
        ].dropna().copy()
        if valid.empty:
            continue
        bins = pd.qcut(
            valid["predicted_survival_probability"].rank(method="first"),
            q=min(10, len(valid)),
            labels=False,
            duplicates="drop",
        )
        valid["calibration_bin"] = bins.astype(int) + 1
        grouped = valid.groupby("calibration_bin", sort=True)
        for bin_id, part in grouped:
            predicted = float(part["predicted_survival_probability"].mean())
            observed = float(part["survived_365d"].mean())
            rows.append(
                {
                    "split": split,
                    "calibration_bin": int(bin_id),
                    "n": int(len(part)),
                    "mean_predicted_survival": predicted,
                    "observed_survival": observed,
                    "calibration_gap": predicted - observed,
                }
            )
    return pd.DataFrame(rows)


def coverage_frame(stages: list[tuple[str, int]], raw_rows: int) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    previous = raw_rows
    for stage, count in stages:
        rows.append(
            {
                "stage": stage,
                "rows": int(count),
                "share_of_raw": count / raw_rows if raw_rows else float("nan"),
                "retention_from_previous": count / previous if previous else float("nan"),
            }
        )
        previous = count
    return pd.DataFrame(rows)


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is pd.NA:
        return None
    return value


def markdown_table(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "_없음_"
    cols = list(frame.columns)
    lines = [
        "| " + " | ".join(cols) + " |",
        "| " + " | ".join("---" for _ in cols) + " |",
    ]
    for _, row in frame.iterrows():
        values = []
        for col in cols:
            value = row[col]
            if pd.isna(value):
                text = ""
            elif isinstance(value, float):
                text = f"{value:.4f}"
            else:
                text = str(value)
            values.append(text.replace("|", "\\|").replace("\n", " "))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def write_record(
    path: Path,
    summary: dict[str, Any],
    metrics: pd.DataFrame,
    coverage: pd.DataFrame,
) -> None:
    headline = metrics[
        (metrics["segment_type"] == "split") & (metrics["segment"] == "holdout")
    ]
    if headline.empty:
        headline = metrics[metrics["segment_type"] == "overall"]
    display_metrics = headline[
        [
            "segment",
            "n",
            "survival_rate",
            "auc",
            "average_precision",
            "top20_survival_lift",
            "bottom20_failure_lift",
            "brier_score_dev_calibrated",
            "brier_skill_vs_development_constant",
        ]
    ]
    checks = pd.DataFrame(
        [
            {"검사": name, "판정": "PASS" if item["pass"] else "FAIL", "측정값": item["value"]}
            for name, item in summary["leakage_checks"].items()
        ]
    )
    text = f"""# 서울 다업종 인허가 365일 생존 백테스트

- 검증 버전: {summary["validation_version"]}
- 검증 절차 상태: {summary["integrity_status"]}
- 예측력 판정: {summary["predictive_status"]}
- 입력: {summary["input"]["path"]} ({summary["input"]["mode"]})
- 점수: 개업분기 직전 완료 분기의 공식 v2.6 점수
- 정답: 개업일부터 365일 이내 폐업이면 0, 그 이후까지 생존하면 1
- 검열: snapshot_date가 365일 기념일보다 빠른 행은 제외
- 확률 보정: development 구간만으로 로지스틱 보정 후 validation/holdout 평가
- 분기 안정성: 365일 관찰이 끝난 완전한 개업분기만 평가

## 핵심 결과

{markdown_table(display_metrics)}

## 커버리지

{markdown_table(coverage)}

## 시간 누수·정합성 검사

{markdown_table(checks)}

## 해석 제한

- 이 결과는 과거 입지점수와 365일 생존의 순위 연관성을 측정한다.
- 인과효과, 개별 사업체의 확정 생존확률, 매출 또는 수익 보장이 아니다.
- 음식점 fallback 실행이면 다업종 공통 Silver가 들어오기 전의 연결 검증 결과다.
- 업태-서비스업종 bridge의 auto_review 행은 mapping_tier로 분리해 안정성을 확인한다.
- 과거 당시 배포된 점수 버전이 아니라 현행 v2.6을 과거 분기에 재계산한 회고 검증이다.
- v2.6 가중치가 holdout 시작 전에 고정되었다는 학습 이력 증거가 없어 완전한 nested OOS로 부르지 않는다.
- 시간 holdout은 생존 라벨 분리와 development-only 확률 보정에만 적용되었다.
- 2026년 스냅샷 인허가 이력과 현재 상권경계를 사용하므로 진정한 시점별 production replay는 아니다.
"""
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="서울 인허가 사업체의 365일 생존과 직전 분기 v2.6 점수를 시간분리 검증"
    )
    parser.add_argument("--input", help="공통 인허가 Silver CSV/Parquet 경로")
    parser.add_argument(
        "--snapshot-date",
        help="입력에 기준일이 없을 때 사용할 관측 기준일(YYYY-MM-DD)",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT),
        help="검증 JSON/CSV/MD 출력 디렉터리",
    )
    parser.add_argument(
        "--min-segment-size",
        type=int,
        default=100,
        help="업종·자치구·분기 안정성 표에 포함할 최소 표본",
    )
    args = parser.parse_args()

    input_path, input_mode = resolve_input(args.input)
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now().astimezone()

    print(f"[survival] input={input_path} mode={input_mode}", flush=True)
    stage_started = perf_counter()
    data, input_audit = read_input(input_path, args.snapshot_date)
    print(
        f"[survival] stage=read_input rows={len(data):,} "
        f"seconds={perf_counter() - stage_started:.1f}",
        flush=True,
    )
    raw_rows = len(data)
    valid_id_rows = int(data["business_id"].ne("").sum())
    valid_open_rows = int(
        (data["business_id"].ne("") & data["open_date"].notna()).sum()
    )
    stage_started = perf_counter()
    data, bridge_audit = attach_food_bridge(data)
    print(
        f"[survival] stage=industry_bridge seconds={perf_counter() - stage_started:.1f}",
        flush=True,
    )
    stage_started = perf_counter()
    data, spatial_audit = attach_spatial_codes(data)
    print(
        f"[survival] stage=spatial rows={spatial_audit.get('coordinate_candidate_rows', 0):,} "
        f"resolved={spatial_audit.get('spatially_resolved_rows', 0):,} "
        f"seconds={perf_counter() - stage_started:.1f}",
        flush=True,
    )
    stage_started = perf_counter()
    data, duplicate_audit = deduplicate(data)
    print(
        f"[survival] stage=deduplicate rows={len(data):,} "
        f"seconds={perf_counter() - stage_started:.1f}",
        flush=True,
    )

    valid_dates = (
        data["open_date"].notna()
        & data["snapshot_date"].notna()
        & (data["snapshot_date"] >= data["open_date"])
        & (data["close_date"].isna() | (data["close_date"] >= data["open_date"]))
        & (data["close_date"].isna() | (data["close_date"] <= data["snapshot_date"]))
    )
    data = data[valid_dates].copy()
    valid_date_rows = int(len(data))

    closed_words = r"폐업|취소|말소|종료"
    closed_without_date = (
        data["close_date"].isna()
        & data["status_group"].str.contains(closed_words, regex=True, na=False)
    )
    data = data[~closed_without_date].copy()
    outcome_date_known_rows = int(len(data))
    data["survival_anniversary"] = data["open_date"] + pd.to_timedelta(
        HORIZON_DAYS, unit="D"
    )
    full_followup = data["snapshot_date"] >= data["survival_anniversary"]
    data = data[full_followup].copy()
    full_followup_rows = int(len(data))
    data["open_quarter_end"] = (
        data["open_date"].dt.to_period("Q").dt.end_time.dt.normalize()
    )
    complete_origin_quarter = (
        data["snapshot_date"]
        >= data["open_quarter_end"] + pd.to_timedelta(HORIZON_DAYS, unit="D")
    )
    data = data[complete_origin_quarter].copy()
    complete_origin_quarter_rows = int(len(data))
    data["survived_365d"] = (
        data["close_date"].isna()
        | (data["close_date"] > data["survival_anniversary"])
    ).astype(int)

    area_mask = data["trade_area_code"].ne("")
    spatial_rows = int(area_mask.sum())
    data = data[area_mask].copy()
    industry_mask = data["industry_code"].ne("")
    industry_rows = int(industry_mask.sum())
    data = data[industry_mask].copy()
    data["open_quarter"] = quarter_from_date(data["open_date"]).astype(int)
    open_quarter_values = data["open_quarter"].to_numpy(dtype=np.int64)
    quarter_number = open_quarter_values % 10
    year = open_quarter_values // 10
    data["score_quarter"] = np.where(
        quarter_number > 1,
        open_quarter_values - SCORE_LAG_QUARTERS,
        (year - 1) * 10 + 4,
    )
    data["split"] = np.select(
        [open_quarter_values <= 20234, open_quarter_values <= 20244],
        ["development", "validation"],
        default="holdout",
    )

    stage_started = perf_counter()
    cache_paths, cache_discovery = discover_score_cache()
    scores, cache_audit = load_score_cache(cache_paths)
    available_score_quarters = set(scores["score_quarter"].unique())
    cache_window_rows = int(data["score_quarter"].isin(available_score_quarters).sum())
    cohort = data.merge(
        scores,
        on=["score_quarter", "trade_area_code", "industry_code"],
        how="left",
        validate="many_to_one",
    )
    scored_mask = cohort["current_location_score"].notna() & cohort["score_percentile"].notna()
    score_matched_rows = int(scored_mask.sum())
    cohort = cohort[scored_mask].copy()
    if cohort.empty:
        raise ValueError(
            "365일 정답 코호트와 공식 점수 캐시가 한 건도 연결되지 않았습니다. "
            "상권코드·서비스업종코드·개업분기 범위를 확인하십시오."
        )
    if cohort["split"].eq("development").sum() == 0:
        raise ValueError("확률 보정에 필요한 development 코호트가 없습니다.")
    print(
        f"[survival] stage=score_join cohort={len(cohort):,} "
        f"seconds={perf_counter() - stage_started:.1f}",
        flush=True,
    )

    intercept, slope = fit_logistic_calibrator(
        cohort.loc[cohort["split"].eq("development"), "score_percentile"],
        cohort.loc[cohort["split"].eq("development"), "survived_365d"],
    )
    cohort["predicted_survival_probability"] = calibrated_probability(
        cohort["score_percentile"], intercept, slope
    )
    development_base_probability = float(
        cohort.loc[cohort["split"].eq("development"), "survived_365d"].mean()
    )
    cohort["constant_development_probability"] = development_base_probability

    metrics = build_metrics(cohort, args.min_segment_size)
    calibration = build_calibration(cohort)
    coverage = coverage_frame(
        [
            ("raw_input", raw_rows),
            ("nonblank_business_id", valid_id_rows),
            ("valid_open_date", valid_open_rows),
            ("deduplicated_nonconflicting", duplicate_audit["deduplicated_rows"]),
            ("valid_open_close_snapshot_dates", valid_date_rows),
            ("known_close_date_or_not_closed", outcome_date_known_rows),
            ("full_365_day_followup", full_followup_rows),
            ("complete_origin_quarter_followup", complete_origin_quarter_rows),
            ("trade_area_mapped", spatial_rows),
            ("industry_mapped", industry_rows),
            ("score_reference_quarter_available", cache_window_rows),
            ("official_score_matched", score_matched_rows),
        ],
        raw_rows,
    )

    leakage_checks = {
        "score_precedes_open_quarter": {
            "pass": bool((cohort["score_quarter"] < cohort["open_quarter"]).all()),
            "value": int((cohort["score_quarter"] >= cohort["open_quarter"]).sum()),
        },
        "full_followup_only": {
            "pass": bool(
                (cohort["snapshot_date"] >= cohort["survival_anniversary"]).all()
            ),
            "value": int(
                (cohort["snapshot_date"] < cohort["survival_anniversary"]).sum()
            ),
        },
        "no_close_before_open": {
            "pass": bool(
                (
                    cohort["close_date"].isna()
                    | (cohort["close_date"] >= cohort["open_date"])
                ).all()
            ),
            "value": int(
                (
                    cohort["close_date"].notna()
                    & (cohort["close_date"] < cohort["open_date"])
                ).sum()
            ),
        },
        "no_close_after_snapshot": {
            "pass": bool(
                (
                    cohort["close_date"].isna()
                    | (cohort["close_date"] <= cohort["snapshot_date"])
                ).all()
            ),
            "value": int(
                (
                    cohort["close_date"].notna()
                    & (cohort["close_date"] > cohort["snapshot_date"])
                ).sum()
            ),
        },
        "unique_business_cohort": {
            "pass": not bool(cohort["entity_key"].duplicated().any()),
            "value": int(cohort["entity_key"].duplicated().sum()),
        },
        "single_score_version": {
            "pass": int(cohort["score_version"].nunique()) == 1,
            "value": int(cohort["score_version"].nunique()),
        },
        "calibrator_development_only": {
            "pass": True,
            "value": int(cohort["split"].eq("development").sum()),
        },
        "strict_time_ordered_splits": {
            "pass": bool(
                (
                    cohort.loc[cohort["split"].eq("validation"), "open_quarter"].min()
                    > cohort.loc[cohort["split"].eq("development"), "open_quarter"].max()
                )
                and (
                    cohort.loc[cohort["split"].eq("holdout"), "open_quarter"].min()
                    > cohort.loc[cohort["split"].eq("validation"), "open_quarter"].max()
                )
            )
            if {"development", "validation", "holdout"}.issubset(set(cohort["split"]))
            else False,
            "value": ",".join(
                f"{split}:{int(group['open_quarter'].min())}-{int(group['open_quarter'].max())}"
                for split, group in cohort.groupby("split", sort=False)
            ),
        },
    }
    all_checks_pass = all(item["pass"] for item in leakage_checks.values())
    holdout_metric = metrics[
        (metrics["segment_type"] == "split") & (metrics["segment"] == "holdout")
    ]
    status = (
        "pass"
        if all_checks_pass
        and not holdout_metric.empty
        and int(holdout_metric.iloc[0]["failures"]) > 0
        and int(holdout_metric.iloc[0]["survivors"]) > 0
        else "warning"
    )
    if holdout_metric.empty:
        predictive_status = "unavailable"
    else:
        holdout_auc = float(holdout_metric.iloc[0]["auc"])
        holdout_lift = float(holdout_metric.iloc[0]["top20_survival_lift"])
        if holdout_auc <= 0.5 and holdout_lift <= 1.0:
            predictive_status = "not_supported"
        elif holdout_auc < 0.55 or holdout_lift < 1.05:
            predictive_status = "weak_signal"
        else:
            predictive_status = "positive_signal"

    summary = {
        "validation_version": VALIDATION_VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "duration_seconds": round(
            (datetime.now().astimezone() - started_at).total_seconds(), 3
        ),
        "status": status,
        "integrity_status": status,
        "predictive_status": predictive_status,
        "predictive_status_rule": (
            "holdout AUC<=0.5 이고 상위점수 생존 lift<=1이면 not_supported; "
            "AUC<0.55 또는 lift<1.05이면 weak_signal; 그 외 positive_signal"
        ),
        "prospective_simulation_status": "retrospective_current_v2_6_only",
        "score_weight_training_cutoff_verified": False,
        "temporal_holdout_scope": "calibration_and_survival_labels_only",
        "nested_out_of_sample_status": "not_verified",
        "scope": {
            "geography": "서울",
            "unit": "source+인허가유형+관리번호+기관코드 기준 개별 인허가 레코드",
            "horizon_days": HORIZON_DAYS,
            "label": "365일 이내 폐업=0, 365일 이후 생존=1",
            "right_censoring": "snapshot_date가 365일 기념일 전인 행 전체 제외",
            "partial_origin_quarter": "분기말 기준 365일이 지나지 않은 개업분기 전체 제외",
            "score_timing": "개업분기 직전 완료 분기",
            "score_lag_quarters": SCORE_LAG_QUARTERS,
            "split": {
                "development": "<=2023Q4",
                "validation": "2024Q1~2024Q4",
                "holdout": ">=2025Q1",
            },
        },
        "input": {
            "path": str(input_path.relative_to(ROOT)).replace("\\", "/")
            if input_path.is_relative_to(ROOT)
            else str(input_path),
            "mode": input_mode,
            "sha256": sha256_file(input_path),
            **input_audit,
        },
        "bridge": bridge_audit,
        "spatial": spatial_audit,
        "deduplication": duplicate_audit,
        "score_cache": {**cache_discovery, **cache_audit},
        "cohort": {
            "analyzed_rows": int(len(cohort)),
            "businesses": int(cohort["entity_key"].nunique()),
            "industries": int(cohort["industry_code"].nunique()),
            "districts": int(cohort["district_name"].replace("", np.nan).nunique()),
            "trade_areas": int(cohort["trade_area_code"].nunique()),
            "open_quarter_first": int(cohort["open_quarter"].min()),
            "open_quarter_last": int(cohort["open_quarter"].max()),
            "split_rows": {
                str(key): int(value)
                for key, value in cohort["split"].value_counts().to_dict().items()
            },
        },
        "calibration_model": {
            "method": "development-only one-variable logistic calibration",
            "intercept": intercept,
            "slope": slope,
            "development_base_probability": development_base_probability,
        },
        "headline_holdout": (
            json_ready(holdout_metric.iloc[0].to_dict()) if not holdout_metric.empty else None
        ),
        "leakage_checks": leakage_checks,
        "interpretation": (
            "순위 연관성 검증이며 인과효과, 개별 점포 확정 생존확률, "
            "매출·수익 보장이 아니다."
        ),
        "temporal_caveats": [
            "개별 365일 생존 라벨은 development/validation/holdout으로 분리했으며 확률 보정은 development만 사용했다.",
            "점수는 개업분기 직전 분기를 사용해 행 단위 미래 정보 연결을 차단했다.",
            "과거 당시 배포 모델 스냅샷이 없어 현행 v2.6과 현행 가중치를 과거 분기에 재계산했다.",
            "v2.6 가중치가 holdout 시작 전에 고정되었다는 증거가 없어 완전한 nested OOS 평가는 아니다.",
            "따라서 temporal holdout 주장은 생존 라벨 분리와 development-only 확률 보정 범위로 제한한다.",
            "인허가 이력과 상권경계는 최신 스냅샷이므로 시점별 production replay는 아니다.",
        ],
        "outputs": {
            "summary": "validation_summary.json",
            "metrics": "metrics.csv",
            "coverage": "coverage.csv",
            "calibration": "calibration.csv",
            "record": "validation_record.md",
        },
    }

    metrics.to_csv(output_dir / "metrics.csv", index=False, encoding="utf-8-sig")
    coverage.to_csv(output_dir / "coverage.csv", index=False, encoding="utf-8-sig")
    calibration.to_csv(output_dir / "calibration.csv", index=False, encoding="utf-8-sig")
    (output_dir / "validation_summary.json").write_text(
        json.dumps(json_ready(summary), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_record(output_dir / "validation_record.md", summary, metrics, coverage)

    print(
        f"[survival] status={status} cohort={len(cohort):,} "
        f"industries={cohort['industry_code'].nunique()} "
        f"holdout={int(cohort['split'].eq('holdout').sum()):,}",
        flush=True,
    )
    print(f"[survival] outputs={output_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
