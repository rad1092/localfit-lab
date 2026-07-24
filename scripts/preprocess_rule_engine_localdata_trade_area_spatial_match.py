from __future__ import annotations

from collections import Counter
from datetime import datetime
import math
from pathlib import Path
from typing import Any

import pandas as pd
import shapefile
from pyproj import CRS, Transformer
from shapely.geometry import Point, shape
from shapely.strtree import STRtree
from shapely.validation import make_valid


ROOT = Path(__file__).resolve().parents[1]
SILVER_DIR = ROOT / "datacorpus" / "_silver"
VALIDATION_DIR = ROOT / "datacorpus" / "_rule_validation"
RESEARCH_VALIDATION_DIR = ROOT / "research" / "rule_validation"

LOCALDATA_PATH = SILVER_DIR / "silver_localdata_food_license_raw_seoul.csv"
TRADE_AREA_GEOMETRY_PATH = SILVER_DIR / "silver_trade_area_boundary_geometry.csv"
TRADE_AREA_SHP_PATH = ROOT / "datacorpus" / "_unzipped" / "서울시 상권분석서비스(영역-상권)" / "서울시 상권분석서비스(영역-상권).shp"
TRADE_AREA_PRJ_PATH = TRADE_AREA_SHP_PATH.with_suffix(".prj")

MATCH_PATH = SILVER_DIR / "silver_localdata_food_license_trade_area_match.csv"
STATUS_SUMMARY_PATH = SILVER_DIR / "silver_localdata_food_license_trade_area_uptae_status_summary.csv"
MONTHLY_EVENT_PATH = SILVER_DIR / "silver_localdata_food_license_trade_area_open_close_monthly.csv"

SOURCE_CONTRACT_PATH = VALIDATION_DIR / "17_localdata_trade_area_spatial_match_source_contract.csv"
DOMAIN_VALIDATION_PATH = VALIDATION_DIR / "17_localdata_trade_area_spatial_match_domain_validation.csv"
GRAIN_VALIDATION_PATH = VALIDATION_DIR / "17_localdata_trade_area_spatial_match_grain_validation.csv"
CONSISTENCY_VALIDATION_PATH = VALIDATION_DIR / "17_localdata_trade_area_spatial_match_consistency_validation.csv"
MATCH_STATUS_PATH = VALIDATION_DIR / "17_localdata_trade_area_spatial_match_status.csv"
NEAREST_DISTANCE_AUDIT_PATH = VALIDATION_DIR / "17_localdata_trade_area_spatial_match_nearest_distance_audit.csv"
MULTI_CANDIDATE_AUDIT_PATH = VALIDATION_DIR / "17_localdata_trade_area_spatial_match_multi_candidate_audit.csv"
UNMATCHED_SAMPLE_PATH = VALIDATION_DIR / "17_localdata_trade_area_spatial_match_unmatched_sample.csv"
INVALID_SAMPLE_PATH = VALIDATION_DIR / "17_localdata_trade_area_spatial_match_invalid_or_outside_sample.csv"
MULTI_MATCH_SAMPLE_PATH = VALIDATION_DIR / "17_localdata_trade_area_spatial_match_multi_match_sample.csv"
SGG_MISMATCH_SAMPLE_PATH = VALIDATION_DIR / "17_localdata_trade_area_spatial_match_sgg_mismatch_sample.csv"
MD_REPORT_PATH = RESEARCH_VALIDATION_DIR / "17_localdata_trade_area_spatial_match_validation_20260704.md"

SNAPSHOT_DATE = "2026-07-04"
SOURCE_ID = "seoul_localdata_food_license"
PROVIDER = "서울열린데이터광장/행정안전부 지방행정 인허가"
SPATIAL_SOURCE_ID = "seoul_trade_area_boundary"
BOUNDARY_VERSION = "seoul_open_data_20260703_TbgisTrdarRelm"
SOURCE_CRS_RECORDED = "Bessel 중부원점TM(EPSG:5174)"
LOCALDATA_SOURCE_CRS = CRS.from_epsg(5174)
CHUNK_SIZE = 75_000

LOCALDATA_USECOLS = [
    "source_id",
    "provider",
    "service_code",
    "license_category",
    "dataset_name",
    "snapshot_date",
    "source_file",
    "source_crs_recorded",
    "원천행번호",
    "관리번호",
    "인허가기관코드",
    "사업장명",
    "업태명",
    "인허가일자",
    "인허가_년월",
    "폐업일자",
    "폐업_년월",
    "영업상태코드",
    "영업상태명",
    "상세영업상태코드",
    "상세영업상태명",
    "상태그룹",
    "영업중여부",
    "폐업여부",
    "소재지전체주소",
    "도로명전체주소",
    "자치구_코드",
    "자치구_코드_명",
    "X_EPSG5174",
    "Y_EPSG5174",
    "좌표유효여부",
    "서울_TM_bbox_범위여부",
    "면적_제곱미터",
    "면적유효여부",
    "주소보유여부",
    "점수직접사용상태",
]

MATCH_OUTPUT_COLUMNS = [
    *LOCALDATA_USECOLS,
    "x_epsg5181",
    "y_epsg5181",
    "spatial_source_id",
    "boundary_version",
    "spatial_match_crs",
    "spatial_match_method",
    "match_status",
    "match_candidate_count",
    "nearest_distance_m",
    "nearest_상권_코드",
    "nearest_상권_코드_명",
    "polygon_index",
    "상권_코드",
    "상권_코드_명",
    "상권_구분_코드",
    "상권_구분_코드_명",
    "상권_자치구_코드",
    "상권_자치구_코드_명",
    "상권_행정동_코드",
    "상권_행정동_코드_명",
    "상권_면적_제곱미터",
    "spatial_score_use_status",
]

TRADE_AREA_KEYS = [
    "상권_코드",
    "상권_코드_명",
    "상권_구분_코드",
    "상권_구분_코드_명",
    "상권_자치구_코드",
    "상권_자치구_코드_명",
    "상권_행정동_코드",
    "상권_행정동_코드_명",
]


def ensure_dirs() -> None:
    for path in [SILVER_DIR, VALIDATION_DIR, RESEARCH_VALIDATION_DIR]:
        path.mkdir(parents=True, exist_ok=True)


def clean_text(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "none", "<na>"}:
        return ""
    return text


def bool_text(value: Any) -> bool:
    return clean_text(value).lower() in {"true", "1", "y", "yes", "t"}


def write_csv(df: pd.DataFrame, path: Path) -> None:
    df.to_csv(path, index=False, encoding="utf-8-sig")


def append_csv(df: pd.DataFrame, path: Path, first_write: bool) -> None:
    df.to_csv(
        path,
        index=False,
        encoding="utf-8-sig" if first_write else "utf-8",
        mode="w" if first_write else "a",
        header=first_write,
    )


def count_csv_rows(path: Path) -> int:
    count = 0
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            count += block.count(b"\n")
    return max(count - 1, 0)


def markdown_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "_없음_"
    cols = list(df.columns)
    lines = [
        "| " + " | ".join(str(c) for c in cols) + " |",
        "| " + " | ".join("---" for _ in cols) + " |",
    ]
    for _, row in df.iterrows():
        values: list[str] = []
        for col in cols:
            value = row[col]
            if pd.isna(value):
                text = ""
            elif isinstance(value, float):
                text = f"{value:.6f}".rstrip("0").rstrip(".")
            else:
                text = str(value)
            values.append(text.replace("|", "\\|").replace("\n", " "))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def load_trade_area_polygons() -> tuple[list[Any], list[dict[str, Any]], STRtree, CRS]:
    prj_text = TRADE_AREA_PRJ_PATH.read_text(encoding="utf-8", errors="ignore").strip()
    source_crs = CRS.from_wkt(prj_text)
    reader = shapefile.Reader(str(TRADE_AREA_SHP_PATH), encoding="utf-8")
    fields = [field[0] for field in reader.fields[1:]]

    geometry_master = pd.read_csv(TRADE_AREA_GEOMETRY_PATH, encoding="utf-8-sig", dtype=str).fillna("")
    master_by_code = {
        clean_text(row["상권_코드"]): row
        for row in geometry_master[
            [
                "상권_코드",
                "상권_코드_명",
                "상권_구분_코드",
                "상권_구분_코드_명",
                "자치구_코드",
                "자치구_코드_명",
                "행정동_코드",
                "행정동_코드_명",
                "source_area_m2",
            ]
        ].to_dict("records")
    }

    polygons: list[Any] = []
    attrs: list[dict[str, Any]] = []
    for idx, shape_record in enumerate(reader.iterShapeRecords()):
        rec = dict(zip(fields, shape_record.record))
        code = clean_text(rec["TRDAR_CD"])
        geom = shape(shape_record.shape.__geo_interface__)
        if not geom.is_valid:
            geom = make_valid(geom)
        polygons.append(geom)
        base = master_by_code.get(code, {})
        attrs.append(
            {
                "polygon_index": idx,
                "상권_코드": code,
                "상권_코드_명": clean_text(base.get("상권_코드_명", rec.get("TRDAR_CD_N"))),
                "상권_구분_코드": clean_text(base.get("상권_구분_코드", rec.get("TRDAR_SE_C"))),
                "상권_구분_코드_명": clean_text(base.get("상권_구분_코드_명", rec.get("TRDAR_SE_1"))),
                "상권_자치구_코드": clean_text(base.get("자치구_코드", rec.get("SIGNGU_CD"))),
                "상권_자치구_코드_명": clean_text(base.get("자치구_코드_명", rec.get("SIGNGU_CD_"))),
                "상권_행정동_코드": clean_text(base.get("행정동_코드", rec.get("ADSTRD_CD"))),
                "상권_행정동_코드_명": clean_text(base.get("행정동_코드_명", rec.get("ADSTRD_CD_"))),
                "상권_면적_제곱미터": float(clean_text(base.get("source_area_m2", rec.get("RELM_AR"))) or 0),
            }
        )
    return polygons, attrs, STRtree(polygons), source_crs


def empty_match(status: str, score_use_status: str) -> dict[str, Any]:
    return {
        "match_status": status,
        "match_candidate_count": 0,
        "nearest_distance_m": "",
        "nearest_상권_코드": "",
        "nearest_상권_코드_명": "",
        "polygon_index": "",
        "상권_코드": "",
        "상권_코드_명": "",
        "상권_구분_코드": "",
        "상권_구분_코드_명": "",
        "상권_자치구_코드": "",
        "상권_자치구_코드_명": "",
        "상권_행정동_코드": "",
        "상권_행정동_코드_명": "",
        "상권_면적_제곱미터": "",
        "spatial_score_use_status": score_use_status,
    }


def match_one_point(point: Point, polygons: list[Any], attrs: list[dict[str, Any]], tree: STRtree) -> dict[str, Any]:
    candidate_indices = list(tree.query(point))
    matched_indices = [int(idx) for idx in candidate_indices if polygons[int(idx)].covers(point)]
    if matched_indices:
        # polygon 경계 중첩 예외는 중복 집계를 막기 위해 가장 작은 면적 상권 1개만 선택한다.
        chosen_idx = min(matched_indices, key=lambda idx: polygons[idx].area)
        chosen = attrs[chosen_idx]
        status = "polygon_match" if len(matched_indices) == 1 else "multi_polygon_match_choose_smallest_area"
        return {
            "match_status": status,
            "match_candidate_count": len(matched_indices),
            "nearest_distance_m": 0.0,
            "nearest_상권_코드": chosen["상권_코드"],
            "nearest_상권_코드_명": chosen["상권_코드_명"],
            "spatial_score_use_status": (
                "상권 polygon 직접매칭 완료: 식품업 개폐업/상태 보조 프록시로 조건부 사용"
                if status == "polygon_match"
                else "상권 polygon 다중매칭: 가장 작은 상권으로 1개 선택, audit 확인 후 조건부 사용"
            ),
            **chosen,
        }

    nearest_idx = int(tree.nearest(point))
    nearest_attr = attrs[nearest_idx]
    nearest_distance = float(polygons[nearest_idx].distance(point))
    return {
        "match_status": "unmatched_nearest_candidate",
        "match_candidate_count": 0,
        "nearest_distance_m": nearest_distance,
        "nearest_상권_코드": nearest_attr["상권_코드"],
        "nearest_상권_코드_명": nearest_attr["상권_코드_명"],
        "polygon_index": "",
        "상권_코드": "",
        "상권_코드_명": "",
        "상권_구분_코드": "",
        "상권_구분_코드_명": "",
        "상권_자치구_코드": "",
        "상권_자치구_코드_명": "",
        "상권_행정동_코드": "",
        "상권_행정동_코드_명": "",
        "상권_면적_제곱미터": "",
        "spatial_score_use_status": "상권 polygon 밖: 점수 직접 사용 금지, 최근접 후보와 거리만 audit",
    }


def normalize_match_chunk(
    chunk: pd.DataFrame,
    transformer: Transformer,
    polygons: list[Any],
    attrs: list[dict[str, Any]],
    tree: STRtree,
) -> pd.DataFrame:
    out = chunk.copy()
    for col in LOCALDATA_USECOLS:
        if col not in out.columns:
            out[col] = ""
        out[col] = out[col].map(clean_text)

    x5174 = pd.to_numeric(out["X_EPSG5174"], errors="coerce")
    y5174 = pd.to_numeric(out["Y_EPSG5174"], errors="coerce")
    coord_valid = x5174.notna() & y5174.notna() & out["좌표유효여부"].map(bool_text)
    source_bbox_valid = coord_valid & out["서울_TM_bbox_범위여부"].map(bool_text)

    out["X_EPSG5174"] = x5174
    out["Y_EPSG5174"] = y5174
    out["x_epsg5181"] = pd.NA
    out["y_epsg5181"] = pd.NA

    if source_bbox_valid.any():
        transformed_x, transformed_y = transformer.transform(
            x5174[source_bbox_valid].astype(float).to_numpy(),
            y5174[source_bbox_valid].astype(float).to_numpy(),
        )
        out.loc[source_bbox_valid, "x_epsg5181"] = transformed_x
        out.loc[source_bbox_valid, "y_epsg5181"] = transformed_y

    match_rows: list[dict[str, Any]] = []
    for valid_coord, valid_bbox, x_value, y_value in zip(coord_valid, source_bbox_valid, out["x_epsg5181"], out["y_epsg5181"]):
        if not bool(valid_coord):
            match_rows.append(empty_match("invalid_coordinate", "좌표 무효: 상권 공간매칭/점수 직접 사용 금지"))
            continue
        if not bool(valid_bbox):
            match_rows.append(empty_match("outside_source_bbox", "서울 TM bbox 밖 좌표: 상권 공간매칭/점수 직접 사용 금지"))
            continue
        if pd.isna(x_value) or pd.isna(y_value) or not math.isfinite(float(x_value)) or not math.isfinite(float(y_value)):
            match_rows.append(empty_match("transform_failed", "좌표 변환 실패: 상권 공간매칭/점수 직접 사용 금지"))
            continue
        match_rows.append(match_one_point(Point(float(x_value), float(y_value)), polygons, attrs, tree))

    match_df = pd.DataFrame(match_rows)
    out = pd.concat([out.reset_index(drop=True), match_df.reset_index(drop=True)], axis=1)
    out["spatial_source_id"] = SPATIAL_SOURCE_ID
    out["boundary_version"] = BOUNDARY_VERSION
    out["spatial_match_crs"] = "EPSG:5181"
    out["spatial_match_method"] = "EPSG:5174 식품 인허가 좌표를 EPSG:5181로 변환 후 상권 polygon covers 검사"
    return out[MATCH_OUTPUT_COLUMNS]


def aggregate_status(match_chunk: pd.DataFrame) -> pd.DataFrame:
    matched = match_chunk[match_chunk["상권_코드"].astype(str).ne("")].copy()
    if matched.empty:
        return pd.DataFrame(columns=TRADE_AREA_KEYS + ["license_category", "업태명", "상태그룹"])
    matched["인허가건수"] = 1
    matched["영업중건수"] = matched["영업중여부"].map(bool_text).astype(int)
    matched["폐업건수"] = matched["폐업여부"].map(bool_text).astype(int)
    matched["주소보유건수"] = matched["주소보유여부"].map(bool_text).astype(int)
    matched["면적유효건수"] = matched["면적유효여부"].map(bool_text).astype(int)
    matched["면적합계_제곱미터"] = pd.to_numeric(matched["면적_제곱미터"], errors="coerce").fillna(0)
    keys = TRADE_AREA_KEYS + ["license_category", "업태명", "상태그룹"]
    return (
        matched.groupby(keys, dropna=False)[
            ["인허가건수", "영업중건수", "폐업건수", "주소보유건수", "면적유효건수", "면적합계_제곱미터"]
        ]
        .sum()
        .reset_index()
    )


def aggregate_month_events(match_chunk: pd.DataFrame) -> pd.DataFrame:
    matched = match_chunk[match_chunk["상권_코드"].astype(str).ne("")].copy()
    if matched.empty:
        return pd.DataFrame(columns=TRADE_AREA_KEYS + ["license_category", "업태명", "년월", "인허가건수", "폐업건수"])
    keys_base = TRADE_AREA_KEYS + ["license_category", "업태명"]
    open_df = matched[matched["인허가_년월"].map(clean_text).ne("")].copy()
    close_df = matched[matched["폐업_년월"].map(clean_text).ne("")].copy()
    open_agg = (
        open_df.assign(년월=open_df["인허가_년월"].map(clean_text), 인허가건수=1)
        .groupby(keys_base + ["년월"], dropna=False)["인허가건수"]
        .sum()
        .reset_index()
    )
    close_agg = (
        close_df.assign(년월=close_df["폐업_년월"].map(clean_text), 폐업건수=1)
        .groupby(keys_base + ["년월"], dropna=False)["폐업건수"]
        .sum()
        .reset_index()
    )
    merged = open_agg.merge(close_agg, on=keys_base + ["년월"], how="outer").fillna({"인허가건수": 0, "폐업건수": 0})
    merged["인허가건수"] = merged["인허가건수"].astype(int)
    merged["폐업건수"] = merged["폐업건수"].astype(int)
    return merged


def merge_aggregates(parts: list[pd.DataFrame], keys: list[str]) -> pd.DataFrame:
    if not parts:
        return pd.DataFrame(columns=keys)
    merged = pd.concat(parts, ignore_index=True)
    if merged.empty:
        return merged
    value_cols = [col for col in merged.columns if col not in keys]
    return merged.groupby(keys, dropna=False)[value_cols].sum().reset_index().sort_values(keys).reset_index(drop=True)


def build_match_distribution_audits(match_path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    df = pd.read_csv(match_path, encoding="utf-8-sig", dtype=str, usecols=["match_status", "nearest_distance_m", "match_candidate_count"])
    distance = pd.to_numeric(df["nearest_distance_m"], errors="coerce")
    unmatched_distance = distance[df["match_status"].eq("unmatched_nearest_candidate")].dropna()
    if unmatched_distance.empty:
        nearest_audit = pd.DataFrame(columns=["구분", "값"])
    else:
        nearest_audit = pd.DataFrame(
            [
                {"구분": "unmatched_count", "값": int(unmatched_distance.size)},
                {"구분": "min_m", "값": float(unmatched_distance.min())},
                {"구분": "p50_m", "값": float(unmatched_distance.quantile(0.50))},
                {"구분": "p90_m", "값": float(unmatched_distance.quantile(0.90))},
                {"구분": "p95_m", "값": float(unmatched_distance.quantile(0.95))},
                {"구분": "p99_m", "값": float(unmatched_distance.quantile(0.99))},
                {"구분": "max_m", "값": float(unmatched_distance.max())},
                {"구분": "within_50m", "값": int((unmatched_distance <= 50).sum())},
                {"구분": "within_100m", "값": int((unmatched_distance <= 100).sum())},
                {"구분": "within_250m", "값": int((unmatched_distance <= 250).sum())},
                {"구분": "within_500m", "값": int((unmatched_distance <= 500).sum())},
                {"구분": "over_500m", "값": int((unmatched_distance > 500).sum())},
            ]
        )

    multi = df[df["match_status"].eq("multi_polygon_match_choose_smallest_area")].copy()
    if multi.empty:
        multi_audit = pd.DataFrame(columns=["match_candidate_count", "건수"])
    else:
        multi["match_candidate_count"] = pd.to_numeric(multi["match_candidate_count"], errors="coerce").fillna(0).astype(int)
        multi_audit = multi.groupby("match_candidate_count", dropna=False).size().reset_index(name="건수").sort_values("match_candidate_count")
    return nearest_audit, multi_audit


def build_validation_tables(metrics: dict[str, Any], status_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    source_contract = pd.DataFrame(
        [
            {
                "source_id": SOURCE_ID,
                "provider": PROVIDER,
                "source_path": LOCALDATA_PATH.relative_to(ROOT).as_posix(),
                "row_count": metrics["source_rows"],
                "source_crs_recorded": SOURCE_CRS_RECORDED,
                "doc_paths": "research/rule_validation/14_localdata_food_license_silver_validation_20260703.md;research/algorithm_evidence_sources/data_docs/data_go_kr_localdata_core_data_page.html",
                "usage_role": "식품업 인허가·폐업 이력을 상권 polygon에 매칭해 경쟁/안정성 보조 프록시 생성",
                "contract_status": "PASS",
            },
            {
                "source_id": SPATIAL_SOURCE_ID,
                "provider": "서울열린데이터광장",
                "source_path": TRADE_AREA_SHP_PATH.relative_to(ROOT).as_posix(),
                "row_count": metrics["trade_area_polygon_rows"],
                "source_crs_recorded": "Korea 2000 / Central Belt(EPSG:5181)",
                "doc_paths": "research/rule_validation/15_trade_area_boundary_silver_validation_20260704.md",
                "usage_role": "식품 인허가 좌표를 상권_코드로 변환하는 polygon 기준",
                "contract_status": "PASS",
            },
        ]
    )
    domain = pd.DataFrame(
        [
            {
                "검증항목": "LocalData row 보존",
                "측정값": metrics["match_rows"],
                "기준값": metrics["source_rows"],
                "판정": "PASS" if metrics["match_rows"] == metrics["source_rows"] else "FAIL",
                "근거": "인허가 이력은 개폐업 추이를 만들기 때문에 원천 row가 누락되면 안 된다.",
            },
            {
                "검증항목": "상권 polygon 기준 존재",
                "측정값": metrics["trade_area_polygon_rows"],
                "기준값": 1650,
                "판정": "PASS" if metrics["trade_area_polygon_rows"] == 1650 else "FAIL",
                "근거": "상권_코드 변환은 검증된 official polygon 1,650개를 기준으로 한다.",
            },
            {
                "검증항목": "좌표계 변환 계약",
                "측정값": "EPSG:5174 -> EPSG:5181",
                "기준값": "EPSG:5174 -> EPSG:5181",
                "판정": "PASS",
                "근거": "식품 인허가 공식 좌표계는 Bessel 중부원점TM(EPSG:5174)이고 상권 polygon은 EPSG:5181이다.",
            },
            {
                "검증항목": "상권 polygon 직접매칭률",
                "측정값": f"{metrics['polygon_match_rate']:.4f}",
                "기준값": "정보성",
                "판정": "INFO",
                "근거": "인허가 전체 이력 중 일부는 좌표 결측, 이상치, polygon 밖 좌표일 수 있으므로 비율을 기록한다.",
            },
            {
                "검증항목": "polygon 밖 최근접 후보 보존",
                "측정값": metrics["unmatched_rows"],
                "기준값": "정보성",
                "판정": "INFO",
                "근거": "polygon 밖 row는 임의 배정하지 않고 최근접 후보와 거리만 남긴다.",
            },
        ]
    )
    grain = pd.DataFrame(
        [
            {
                "검증항목": "match 원천행번호 중복",
                "측정값": metrics["match_duplicate_source_row_rows"],
                "기준값": 0,
                "판정": "PASS" if metrics["match_duplicate_source_row_rows"] == 0 else "FAIL",
                "근거": "한 인허가 row가 여러 상권에 중복 집계되면 개폐업 수가 부풀려진다.",
            },
            {
                "검증항목": "관리번호+기관코드 중복",
                "측정값": metrics["duplicate_license_key_rows"],
                "기준값": 0,
                "판정": "CONDITIONAL_PASS" if metrics["duplicate_license_key_rows"] > 0 else "PASS",
                "근거": "원천 이력 중복 가능성이 있어 개별 업소 단위 해석 전 audit가 필요하다.",
            },
            {
                "검증항목": "상권×업태×상태 집계 합계",
                "측정값": metrics["status_summary_sum"],
                "기준값": metrics["matched_rows"],
                "판정": "PASS" if metrics["status_summary_sum"] == metrics["matched_rows"] else "FAIL",
                "근거": "상권 단위 상태 집계는 polygon 매칭된 인허가 row 전체를 대표해야 한다.",
            },
            {
                "검증항목": "월별 인허가 이벤트 합계",
                "측정값": metrics["monthly_open_sum"],
                "기준값": metrics["matched_open_rows"],
                "판정": "PASS" if metrics["monthly_open_sum"] == metrics["matched_open_rows"] else "FAIL",
                "근거": "상권 단위 개업 추이는 polygon 매칭된 인허가일자 보유 row를 빠짐없이 집계해야 한다.",
            },
            {
                "검증항목": "월별 폐업 이벤트 합계",
                "측정값": metrics["monthly_close_sum"],
                "기준값": metrics["matched_close_rows"],
                "판정": "PASS" if metrics["monthly_close_sum"] == metrics["matched_close_rows"] else "FAIL",
                "근거": "상권 단위 폐업 추이는 polygon 매칭된 폐업일자 보유 row를 빠짐없이 집계해야 한다.",
            },
        ]
    )
    consistency = pd.DataFrame(
        [
            {
                "검증항목": "매칭 상권코드 마스터 미존재",
                "측정값": metrics["unknown_trade_area_code_rows"],
                "기준값": 0,
                "판정": "PASS" if metrics["unknown_trade_area_code_rows"] == 0 else "FAIL",
                "근거": "공간매칭 결과는 상권 마스터/매출/점포 silver와 조인 가능해야 한다.",
            },
            {
                "검증항목": "좌표 무효 row",
                "측정값": metrics["invalid_coordinate_rows"],
                "기준값": 0,
                "판정": "CONDITIONAL_PASS" if metrics["invalid_coordinate_rows"] > 0 else "PASS",
                "근거": "좌표가 없는 인허가 row는 상권 점수에 직접 넣지 않고 원천 이력으로만 보존한다.",
            },
            {
                "검증항목": "서울 TM bbox 밖 row",
                "측정값": metrics["outside_source_bbox_rows"],
                "기준값": 0,
                "판정": "CONDITIONAL_PASS" if metrics["outside_source_bbox_rows"] > 0 else "PASS",
                "근거": "공식 좌표계 범위 밖 row는 좌표 이상치로 분리하고 임의 매칭하지 않는다.",
            },
            {
                "검증항목": "좌표 변환 실패 row",
                "측정값": metrics["transform_failed_rows"],
                "기준값": 0,
                "판정": "PASS" if metrics["transform_failed_rows"] == 0 else "FAIL",
                "근거": "EPSG:5174에서 EPSG:5181로 변환되지 않는 row는 공간매칭 결과를 신뢰할 수 없다.",
            },
            {
                "검증항목": "다중매칭 row",
                "측정값": metrics["multi_match_rows"],
                "기준값": 0,
                "판정": "CONDITIONAL_PASS" if metrics["multi_match_rows"] > 0 else "PASS",
                "근거": "polygon 겹침/경계 예외는 중복 집계하지 않고 작은 면적 상권 1개로 선택해 audit한다.",
            },
            {
                "검증항목": "원천 자치구와 상권 자치구 불일치",
                "측정값": metrics["sgg_mismatch_rows"],
                "기준값": 0,
                "판정": "CONDITIONAL_PASS" if metrics["sgg_mismatch_rows"] > 0 else "PASS",
                "근거": "주소 파싱 자치구와 좌표 기반 polygon 자치구가 다르면 주소/좌표 품질 검토가 필요하다.",
            },
            {
                "검증항목": "영업상태 결측",
                "측정값": metrics["missing_status_rows"],
                "기준값": 0,
                "판정": "PASS" if metrics["missing_status_rows"] == 0 else "FAIL",
                "근거": "영업/폐업 상태가 없으면 안정성 판단 프록시로 쓸 수 없다.",
            },
            {
                "검증항목": "폐업 상태인데 폐업일자 결측",
                "측정값": metrics["closed_without_close_date_rows"],
                "기준값": 0,
                "판정": "CONDITIONAL_PASS" if metrics["closed_without_close_date_rows"] > 0 else "PASS",
                "근거": "폐업 건수에는 쓸 수 있지만 월별 폐업 추이에는 쓸 수 없는 row를 분리해야 한다.",
            },
        ]
    )
    return source_contract, domain, grain, consistency


def write_report(
    source_contract: pd.DataFrame,
    domain: pd.DataFrame,
    grain: pd.DataFrame,
    consistency: pd.DataFrame,
    status_df: pd.DataFrame,
    nearest_audit: pd.DataFrame,
    multi_candidate_audit: pd.DataFrame,
    metrics: dict[str, Any],
) -> None:
    report = f"""# 서울 식품 인허가 상권 polygon 공간매칭 검증 보고서

작성일: {SNAPSHOT_DATE}

## 1. 처리 목적

서울 일반음식점·휴게음식점 인허가 데이터는 인허가일자, 폐업일자, 영업상태, 업태명, 주소, 좌표를 갖고 있다. `research/rule_validation/14_localdata_food_license_silver_validation_20260703.md`에서는 이 데이터를 식품업 개폐업 이력과 영업상태 기반 경쟁·안정성 보조 프록시로 판단했지만, 좌표계가 `{SOURCE_CRS_RECORDED}`라 상권 점수에 바로 넣지 않았다.

이번 단계는 식품 인허가 좌표를 공식 상권 polygon에 매칭해 `상권_코드` 단위의 개폐업/상태 집계를 만든다. 이 값은 개별 점포 성공확률이 아니라 상권 단위 안정성·경쟁 보조 신호다.

## 2. 사용 원천과 근거

{markdown_table(source_contract)}

근거:

- `research/rule_validation/14_localdata_food_license_silver_validation_20260703.md`: 식품 인허가 680,725건, 좌표계 EPSG:5174, 상권 공간매칭 전 조건부 사용.
- `research/rule_validation/15_trade_area_boundary_silver_validation_20260704.md`: 상권 polygon 1,650건, EPSG:5181, point-in-polygon 가능.
- `research/전처리_전_확인사항_20260703.md`: 좌표는 상권 polygon으로 변환한 뒤 `상권_코드`로 조인하고, polygon 밖 좌표는 임의 배정하지 않는다.
- `research/알고리즘_스펙_v1_20260703.md`: 성장/안정성 축은 개폐업·상권변화·운영 이력 신호를 쓰되, 성공확률/매출보장 표현은 금지한다.

## 3. 생성 산출물

| 산출물 | 행 수 | 역할 |
|---|---:|---|
| `datacorpus/_silver/silver_localdata_food_license_trade_area_match.csv` | {metrics["match_rows"]:,} | 인허가 row별 상권 polygon 매칭 결과 |
| `datacorpus/_silver/silver_localdata_food_license_trade_area_uptae_status_summary.csv` | {metrics["status_summary_rows"]:,} | 상권×업태×영업상태 집계 |
| `datacorpus/_silver/silver_localdata_food_license_trade_area_open_close_monthly.csv` | {metrics["monthly_rows"]:,} | 상권×업태×월별 인허가/폐업 이벤트 |
| `datacorpus/_rule_validation/17_localdata_trade_area_spatial_match_nearest_distance_audit.csv` | {len(nearest_audit):,} | polygon 밖 좌표의 최근접 상권 거리 분포 |
| `datacorpus/_rule_validation/17_localdata_trade_area_spatial_match_multi_candidate_audit.csv` | {len(multi_candidate_audit):,} | 다중 polygon 매칭 후보 수 분포 |

## 4. 공간매칭 상태

{markdown_table(status_df)}

## 5. 도메인 검증

{markdown_table(domain)}

## 6. grain 검증

{markdown_table(grain)}

## 7. 정합성 검증

{markdown_table(consistency)}

## 8. 미매칭 최근접 거리 감사

{markdown_table(nearest_audit)}

## 9. 다중매칭 후보 수 감사

{markdown_table(multi_candidate_audit)}

## 10. 알고리즘 사용 판단

- 사용 가능: polygon 내부 매칭된 식품 인허가 row의 상권×업태×상태 집계, 상권×업태×월별 인허가/폐업 이벤트.
- 조건부 사용: 다중매칭 row는 작은 면적 상권 1개로 선택했으므로 audit 확인 후 사용한다.
- 보류: polygon 밖 row, 좌표 무효 row, 서울 TM bbox 밖 row는 상권 점수에 직접 넣지 않는다.
- 보류: 업태명은 서울 서비스업종 코드와 같은 체계가 아니므로 서비스업종별 점수에 직접 연결하려면 별도 업종 매핑이 필요하다.
- 금지: 이 값은 창업 성공확률, 개별 매장 생존확률, 개별 매출 보장이 아니다.

## 11. 2보 전진 1보 후퇴 검토

1. 전진: 식품 인허가 {metrics["match_rows"]:,}건을 누락 없이 공간매칭 결과 테이블로 보존했다.
2. 전진: polygon 내부 매칭 {metrics["matched_rows"]:,}건을 상권×업태×상태와 월별 개폐업 이벤트로 집계했다.
3. 후퇴 검토: 좌표 무효 {metrics["invalid_coordinate_rows"]:,}건, 서울 TM bbox 밖 {metrics["outside_source_bbox_rows"]:,}건, 좌표 변환 실패 {metrics["transform_failed_rows"]:,}건은 임의로 상권에 붙이지 않았다.
4. 후퇴 검토: polygon 밖 {metrics["unmatched_rows"]:,}건은 최근접 후보만 남기고 점수 직접 사용을 보류했다.
5. 후퇴 검토: 원천 관리번호+기관코드 중복 {metrics["duplicate_license_key_rows"]:,}row와 원천/상권 자치구 불일치 {metrics["sgg_mismatch_rows"]:,}row는 audit 대상으로 남겼다.
6. 재검토 결과: 이 산출물은 상권 단위 성장/안정성 보조 프록시로 쓸 수 있지만, 업종 매핑 없이 서울 서비스업종 점수에 바로 합치면 안 된다.
"""
    MD_REPORT_PATH.write_text(report, encoding="utf-8")


def collect_sample(samples: list[pd.DataFrame], chunk: pd.DataFrame, mask: pd.Series, limit: int = 200) -> None:
    current = sum(len(part) for part in samples)
    remain = limit - current
    if remain <= 0:
        return
    sample = chunk[mask].head(remain)
    if not sample.empty:
        samples.append(sample)


def main() -> None:
    ensure_dirs()
    for path in [
        MATCH_PATH,
        UNMATCHED_SAMPLE_PATH,
        INVALID_SAMPLE_PATH,
        MULTI_MATCH_SAMPLE_PATH,
        SGG_MISMATCH_SAMPLE_PATH,
    ]:
        if path.exists():
            path.unlink()

    polygons, attrs, tree, polygon_crs = load_trade_area_polygons()
    transformer = Transformer.from_crs(LOCALDATA_SOURCE_CRS, polygon_crs, always_xy=True)
    trade_area_codes = {attr["상권_코드"] for attr in attrs}

    status_parts: list[pd.DataFrame] = []
    monthly_parts: list[pd.DataFrame] = []
    unmatched_samples: list[pd.DataFrame] = []
    invalid_samples: list[pd.DataFrame] = []
    multi_samples: list[pd.DataFrame] = []
    sgg_mismatch_samples: list[pd.DataFrame] = []

    status_counter: Counter[str] = Counter()
    first_write = True
    match_rows = 0
    missing_status_rows = 0
    closed_without_close_date_rows = 0
    sgg_mismatch_rows = 0
    matched_open_rows = 0
    matched_close_rows = 0

    dtype = {col: "string" for col in LOCALDATA_USECOLS}
    for chunk_index, chunk in enumerate(
        pd.read_csv(
            LOCALDATA_PATH,
            encoding="utf-8-sig",
            dtype=dtype,
            usecols=LOCALDATA_USECOLS,
            chunksize=CHUNK_SIZE,
            low_memory=False,
        ),
        start=1,
    ):
        matched = normalize_match_chunk(chunk, transformer, polygons, attrs, tree)
        match_rows += len(matched)
        status_counter.update(matched["match_status"].astype(str).tolist())
        missing_status_rows += int(matched["상태그룹"].map(clean_text).eq("").sum())
        closed_without_close_date_rows += int((matched["상태그룹"].eq("폐업") & matched["폐업일자"].map(clean_text).eq("")).sum())

        matched_mask = matched["상권_코드"].map(clean_text).ne("")
        matched_open_rows += int((matched_mask & matched["인허가_년월"].map(clean_text).ne("")).sum())
        matched_close_rows += int((matched_mask & matched["폐업_년월"].map(clean_text).ne("")).sum())
        mismatch_mask = (
            matched_mask
            & matched["자치구_코드"].map(clean_text).ne("")
            & matched["상권_자치구_코드"].map(clean_text).ne("")
            & matched["자치구_코드"].map(clean_text).ne(matched["상권_자치구_코드"].map(clean_text))
        )
        sgg_mismatch_rows += int(mismatch_mask.sum())

        status_parts.append(aggregate_status(matched))
        monthly_parts.append(aggregate_month_events(matched))
        collect_sample(unmatched_samples, matched, matched["match_status"].eq("unmatched_nearest_candidate"))
        collect_sample(invalid_samples, matched, matched["match_status"].isin(["invalid_coordinate", "outside_source_bbox", "transform_failed"]))
        collect_sample(multi_samples, matched, matched["match_status"].eq("multi_polygon_match_choose_smallest_area"))
        collect_sample(sgg_mismatch_samples, matched, mismatch_mask)

        append_csv(matched, MATCH_PATH, first_write=first_write)
        first_write = False
        print(f"chunk {chunk_index} 처리 완료: 누적 {match_rows:,}건")

    status_summary = merge_aggregates(status_parts, TRADE_AREA_KEYS + ["license_category", "업태명", "상태그룹"])
    monthly = merge_aggregates(monthly_parts, TRADE_AREA_KEYS + ["license_category", "업태명", "년월"])
    write_csv(status_summary, STATUS_SUMMARY_PATH)
    write_csv(monthly, MONTHLY_EVENT_PATH)

    if unmatched_samples:
        write_csv(pd.concat(unmatched_samples, ignore_index=True), UNMATCHED_SAMPLE_PATH)
    else:
        write_csv(pd.DataFrame(columns=MATCH_OUTPUT_COLUMNS), UNMATCHED_SAMPLE_PATH)
    if invalid_samples:
        write_csv(pd.concat(invalid_samples, ignore_index=True), INVALID_SAMPLE_PATH)
    else:
        write_csv(pd.DataFrame(columns=MATCH_OUTPUT_COLUMNS), INVALID_SAMPLE_PATH)
    if multi_samples:
        write_csv(pd.concat(multi_samples, ignore_index=True), MULTI_MATCH_SAMPLE_PATH)
    else:
        write_csv(pd.DataFrame(columns=MATCH_OUTPUT_COLUMNS), MULTI_MATCH_SAMPLE_PATH)
    if sgg_mismatch_samples:
        write_csv(pd.concat(sgg_mismatch_samples, ignore_index=True), SGG_MISMATCH_SAMPLE_PATH)
    else:
        write_csv(pd.DataFrame(columns=MATCH_OUTPUT_COLUMNS), SGG_MISMATCH_SAMPLE_PATH)

    status_df = (
        pd.DataFrame([{"match_status": status, "건수": count} for status, count in status_counter.items()])
        .sort_values("match_status")
        .reset_index(drop=True)
    )
    status_df["비율"] = status_df["건수"] / match_rows if match_rows else 0
    write_csv(status_df, MATCH_STATUS_PATH)
    nearest_audit, multi_candidate_audit = build_match_distribution_audits(MATCH_PATH)
    write_csv(nearest_audit, NEAREST_DISTANCE_AUDIT_PATH)
    write_csv(multi_candidate_audit, MULTI_CANDIDATE_AUDIT_PATH)

    matched_rows = int(status_counter["polygon_match"] + status_counter["multi_polygon_match_choose_smallest_area"])
    unmatched_rows = int(status_counter["unmatched_nearest_candidate"])
    invalid_coordinate_rows = int(status_counter["invalid_coordinate"])
    outside_source_bbox_rows = int(status_counter["outside_source_bbox"])
    transform_failed_rows = int(status_counter["transform_failed"])
    multi_match_rows = int(status_counter["multi_polygon_match_choose_smallest_area"])
    unknown_trade_area_code_rows = 0
    if not status_summary.empty:
        unknown_trade_area_code_rows = int((~status_summary["상권_코드"].astype(str).isin(trade_area_codes)).sum())

    match_duplicate_source_row_rows = 0
    source_row_numbers = pd.read_csv(MATCH_PATH, encoding="utf-8-sig", dtype=str, usecols=["원천행번호"])
    match_duplicate_source_row_rows = int(source_row_numbers["원천행번호"].duplicated().sum())
    license_keys = pd.read_csv(MATCH_PATH, encoding="utf-8-sig", dtype=str, usecols=["관리번호", "인허가기관코드"])
    duplicate_license_key_rows = int(license_keys.duplicated(["관리번호", "인허가기관코드"], keep=False).sum())

    metrics = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_rows": count_csv_rows(LOCALDATA_PATH),
        "match_rows": count_csv_rows(MATCH_PATH),
        "trade_area_polygon_rows": len(attrs),
        "matched_rows": matched_rows,
        "polygon_match_rate": matched_rows / match_rows if match_rows else 0,
        "unmatched_rows": unmatched_rows,
        "invalid_coordinate_rows": invalid_coordinate_rows,
        "outside_source_bbox_rows": outside_source_bbox_rows,
        "transform_failed_rows": transform_failed_rows,
        "multi_match_rows": multi_match_rows,
        "duplicate_license_key_rows": duplicate_license_key_rows,
        "match_duplicate_source_row_rows": match_duplicate_source_row_rows,
        "status_summary_rows": len(status_summary),
        "status_summary_sum": int(status_summary["인허가건수"].sum()) if "인허가건수" in status_summary.columns else 0,
        "monthly_rows": len(monthly),
        "monthly_open_sum": int(monthly["인허가건수"].sum()) if "인허가건수" in monthly.columns else 0,
        "monthly_close_sum": int(monthly["폐업건수"].sum()) if "폐업건수" in monthly.columns else 0,
        "matched_open_rows": matched_open_rows,
        "matched_close_rows": matched_close_rows,
        "unknown_trade_area_code_rows": unknown_trade_area_code_rows,
        "missing_status_rows": missing_status_rows,
        "closed_without_close_date_rows": closed_without_close_date_rows,
        "sgg_mismatch_rows": sgg_mismatch_rows,
    }

    source_contract, domain, grain, consistency = build_validation_tables(metrics, status_df)
    write_csv(source_contract, SOURCE_CONTRACT_PATH)
    write_csv(domain, DOMAIN_VALIDATION_PATH)
    write_csv(grain, GRAIN_VALIDATION_PATH)
    write_csv(consistency, CONSISTENCY_VALIDATION_PATH)
    write_report(source_contract, domain, grain, consistency, status_df, nearest_audit, multi_candidate_audit, metrics)

    print("완료: 식품 인허가 상권 polygon 공간매칭")
    print(f"- match rows: {metrics['match_rows']:,}")
    print(f"- matched rows: {matched_rows:,}")
    print(f"- unmatched rows: {unmatched_rows:,}")
    print(f"- invalid/outside/transform failed rows: {invalid_coordinate_rows + outside_source_bbox_rows + transform_failed_rows:,}")
    print(f"- report: {MD_REPORT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
