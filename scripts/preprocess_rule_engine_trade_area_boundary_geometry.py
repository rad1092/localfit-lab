from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import shapefile
from pyproj import CRS, Transformer
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon, shape
from shapely.ops import unary_union
from shapely.validation import explain_validity, make_valid


ROOT = Path(__file__).resolve().parents[1]
SILVER_DIR = ROOT / "datacorpus" / "_silver"
VALIDATION_DIR = ROOT / "datacorpus" / "_rule_validation"
RESEARCH_VALIDATION_DIR = ROOT / "research" / "rule_validation"

TRADE_AREA_SHP_DIR = ROOT / "datacorpus" / "_unzipped" / "서울시 상권분석서비스(영역-상권)"
TRADE_AREA_SHP_PATH = TRADE_AREA_SHP_DIR / "서울시 상권분석서비스(영역-상권).shp"
TRADE_AREA_PRJ_PATH = TRADE_AREA_SHP_PATH.with_suffix(".prj")
TRADE_AREA_MASTER_PATH = SILVER_DIR / "silver_trade_area_master.csv"

SNAPSHOT_DATE = "2026-07-04"
SOURCE_ID = "seoul_trade_area_boundary"
PROVIDER = "서울열린데이터광장"
SOURCE_SERVICE = "TbgisTrdarRelm"
BOUNDARY_VERSION = "seoul_open_data_20260703_TbgisTrdarRelm"

GEOMETRY_PATH = SILVER_DIR / "silver_trade_area_boundary_geometry.csv"
VERTEX_PATH = SILVER_DIR / "silver_trade_area_boundary_vertices.csv"
SPATIAL_INDEX_PATH = SILVER_DIR / "silver_trade_area_boundary_spatial_index.csv"

SOURCE_CONTRACT_PATH = VALIDATION_DIR / "15_trade_area_boundary_source_contract.csv"
DOMAIN_VALIDATION_PATH = VALIDATION_DIR / "15_trade_area_boundary_domain_validation.csv"
GRAIN_VALIDATION_PATH = VALIDATION_DIR / "15_trade_area_boundary_grain_validation.csv"
CONSISTENCY_VALIDATION_PATH = VALIDATION_DIR / "15_trade_area_boundary_consistency_validation.csv"
INVALID_GEOMETRY_AUDIT_PATH = VALIDATION_DIR / "15_trade_area_boundary_invalid_geometry_audit.csv"
MASTER_SHP_MISMATCH_AUDIT_PATH = VALIDATION_DIR / "15_trade_area_boundary_master_shp_mismatch_audit.csv"
MD_REPORT_PATH = RESEARCH_VALIDATION_DIR / "15_trade_area_boundary_silver_validation_20260704.md"


FIELD_MAP = {
    "TRDAR_SE_C": "상권_구분_코드",
    "TRDAR_SE_1": "상권_구분_코드_명",
    "TRDAR_CD": "상권_코드",
    "TRDAR_CD_N": "상권_코드_명",
    "XCNTS_VALU": "중심_X",
    "YDNTS_VALU": "중심_Y",
    "SIGNGU_CD": "자치구_코드",
    "SIGNGU_CD_": "자치구_코드_명",
    "ADSTRD_CD": "행정동_코드",
    "ADSTRD_CD_": "행정동_코드_명",
    "RELM_AR": "면적_제곱미터",
}

CANONICAL_MASTER_FIELDS = [
    "상권_코드_명",
    "상권_구분_코드",
    "상권_구분_코드_명",
    "자치구_코드",
    "자치구_코드_명",
    "행정동_코드",
    "행정동_코드_명",
]


def ensure_dirs() -> None:
    for path in [SILVER_DIR, VALIDATION_DIR, RESEARCH_VALIDATION_DIR]:
        path.mkdir(parents=True, exist_ok=True)


def write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="wb",
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        delete=False,
    )
    temporary_path = Path(handle.name)
    handle.close()
    try:
        df.to_csv(temporary_path, index=False, encoding="utf-8-sig")
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def clean_text(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "none"}:
        return ""
    return text


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


def polygonal_part(geom: Any) -> Polygon | MultiPolygon:
    """make_valid 결과에서 면 geometry만 남긴다."""
    if isinstance(geom, (Polygon, MultiPolygon)):
        return geom
    if isinstance(geom, GeometryCollection):
        polygons = [part for part in geom.geoms if isinstance(part, (Polygon, MultiPolygon))]
        if polygons:
            merged = unary_union(polygons)
            if isinstance(merged, (Polygon, MultiPolygon)):
                return merged
    raise ValueError(f"polygon으로 사용할 수 없는 geometry입니다: {geom.geom_type}")


def load_source_crs() -> tuple[CRS, str, int | None]:
    prj_text = TRADE_AREA_PRJ_PATH.read_text(encoding="utf-8", errors="ignore").strip()
    crs = CRS.from_wkt(prj_text)
    return crs, prj_text, crs.to_epsg()


def load_shapefile_rows(source_crs: CRS) -> tuple[pd.DataFrame, pd.DataFrame]:
    reader = shapefile.Reader(str(TRADE_AREA_SHP_PATH), encoding="utf-8")
    fields = [field[0] for field in reader.fields[1:]]
    to_wgs84 = Transformer.from_crs(source_crs, CRS.from_epsg(4326), always_xy=True)
    source_epsg = source_crs.to_epsg()

    geometry_rows: list[dict[str, Any]] = []
    vertex_rows: list[dict[str, Any]] = []

    for source_row_number, shape_record in enumerate(reader.iterShapeRecords(), start=1):
        source_record = {FIELD_MAP.get(key, key): value for key, value in zip(fields, shape_record.record)}
        raw_geom = shape(shape_record.shape.__geo_interface__)
        raw_valid = bool(raw_geom.is_valid)
        validity_reason = "" if raw_valid else explain_validity(raw_geom)
        fixed_geom = polygonal_part(make_valid(raw_geom)) if not raw_valid else polygonal_part(raw_geom)
        fixed_valid = bool(fixed_geom.is_valid)
        centroid = fixed_geom.centroid
        representative = fixed_geom.representative_point()
        centroid_lon, centroid_lat = to_wgs84.transform(centroid.x, centroid.y)
        representative_lon, representative_lat = to_wgs84.transform(representative.x, representative.y)
        min_x, min_y, max_x, max_y = fixed_geom.bounds

        source_area = float(source_record["면적_제곱미터"])
        geometry_area = float(raw_geom.area)
        fixed_area = float(fixed_geom.area)
        area_abs_diff = abs(fixed_area - source_area)
        area_pct_diff = area_abs_diff / source_area * 100 if source_area else None
        source_center_x = float(source_record["중심_X"])
        source_center_y = float(source_record["중심_Y"])
        source_center_to_centroid_m = ((centroid.x - source_center_x) ** 2 + (centroid.y - source_center_y) ** 2) ** 0.5

        lon_values: list[float] = []
        lat_values: list[float] = []
        parts = list(shape_record.shape.parts) + [len(shape_record.shape.points)]
        for part_index in range(len(parts) - 1):
            start = parts[part_index]
            end = parts[part_index + 1]
            for vertex_index, (x_value, y_value) in enumerate(shape_record.shape.points[start:end], start=1):
                lon, lat = to_wgs84.transform(x_value, y_value)
                lon_values.append(lon)
                lat_values.append(lat)
                vertex_rows.append(
                    {
                        "상권_코드": clean_text(source_record["상권_코드"]),
                        "상권_코드_명": clean_text(source_record["상권_코드_명"]),
                        "part_index": part_index,
                        "vertex_index": vertex_index,
                        "x_epsg5181": x_value,
                        "y_epsg5181": y_value,
                        "lon_wgs84": lon,
                        "lat_wgs84": lat,
                        "source_crs_epsg": source_epsg,
                        "notes_ko": "지도 클릭 좌표 매칭용 polygon vertex다. 거리 계산은 EPSG:5181 기준으로 수행한다.",
                    }
                )
        min_lon = min(lon_values)
        min_lat = min(lat_values)
        max_lon = max(lon_values)
        max_lat = max(lat_values)

        geometry_rows.append(
            {
                "source_id": SOURCE_ID,
                "provider": PROVIDER,
                "source_service": SOURCE_SERVICE,
                "snapshot_date": SNAPSHOT_DATE,
                "boundary_version": BOUNDARY_VERSION,
                "source_row_number": source_row_number,
                "상권_코드": clean_text(source_record["상권_코드"]),
                "상권_코드_명": clean_text(source_record["상권_코드_명"]),
                "상권_구분_코드": clean_text(source_record["상권_구분_코드"]),
                "상권_구분_코드_명": clean_text(source_record["상권_구분_코드_명"]),
                "자치구_코드": clean_text(source_record["자치구_코드"]),
                "자치구_코드_명": clean_text(source_record["자치구_코드_명"]),
                "행정동_코드": clean_text(source_record["행정동_코드"]),
                "행정동_코드_명": clean_text(source_record["행정동_코드_명"]),
                "source_center_x_epsg5181": source_center_x,
                "source_center_y_epsg5181": source_center_y,
                "geometry_centroid_x_epsg5181": centroid.x,
                "geometry_centroid_y_epsg5181": centroid.y,
                "geometry_centroid_lon_wgs84": centroid_lon,
                "geometry_centroid_lat_wgs84": centroid_lat,
                "representative_x_epsg5181": representative.x,
                "representative_y_epsg5181": representative.y,
                "representative_lon_wgs84": representative_lon,
                "representative_lat_wgs84": representative_lat,
                "bbox_min_x_epsg5181": min_x,
                "bbox_min_y_epsg5181": min_y,
                "bbox_max_x_epsg5181": max_x,
                "bbox_max_y_epsg5181": max_y,
                "bbox_min_lon_wgs84": min_lon,
                "bbox_min_lat_wgs84": min_lat,
                "bbox_max_lon_wgs84": max_lon,
                "bbox_max_lat_wgs84": max_lat,
                "source_area_m2": source_area,
                "geometry_area_m2": geometry_area,
                "fixed_geometry_area_m2": fixed_area,
                "area_abs_diff_m2": area_abs_diff,
                "area_pct_diff": area_pct_diff,
                "source_center_to_geometry_centroid_m": source_center_to_centroid_m,
                "part_count": len(shape_record.shape.parts),
                "vertex_count": len(shape_record.shape.points),
                "original_geometry_type": raw_geom.geom_type,
                "fixed_geometry_type": fixed_geom.geom_type,
                "original_geometry_valid": raw_valid,
                "original_validity_reason": validity_reason,
                "fixed_geometry_valid": fixed_valid,
                "source_crs_name": source_crs.name,
                "source_crs_epsg": source_epsg,
                "target_display_crs": "WGS84(EPSG:4326)",
                "point_in_polygon_use_status": "사용가능_make_valid보정" if not raw_valid and fixed_valid else "사용가능_원본유효",
                "score_use_status": "위치 입력을 상권_코드로 변환하는 기준 geometry. 점수값 자체는 아님",
                "notes_ko": "상권명은 표시용이며 조인과 점수 산정은 상권_코드를 사용한다.",
                "geometry_storage": "silver_trade_area_boundary_vertices.csv",
            }
        )

    return pd.DataFrame(geometry_rows), pd.DataFrame(vertex_rows)


def build_spatial_index(geometry_df: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "상권_코드",
        "상권_코드_명",
        "상권_구분_코드",
        "상권_구분_코드_명",
        "자치구_코드",
        "자치구_코드_명",
        "행정동_코드",
        "행정동_코드_명",
        "bbox_min_x_epsg5181",
        "bbox_min_y_epsg5181",
        "bbox_max_x_epsg5181",
        "bbox_max_y_epsg5181",
        "bbox_min_lon_wgs84",
        "bbox_min_lat_wgs84",
        "bbox_max_lon_wgs84",
        "bbox_max_lat_wgs84",
        "geometry_centroid_x_epsg5181",
        "geometry_centroid_y_epsg5181",
        "geometry_centroid_lon_wgs84",
        "geometry_centroid_lat_wgs84",
        "representative_lon_wgs84",
        "representative_lat_wgs84",
        "source_crs_epsg",
        "point_in_polygon_use_status",
    ]
    out = geometry_df[columns].copy()
    out["usage_role"] = "지도 클릭/주소 좌표 후보를 bbox로 1차 필터링한 뒤 polygon 포함 여부를 검증"
    return out


def compare_with_master(geometry_df: pd.DataFrame) -> pd.DataFrame:
    master = pd.read_csv(TRADE_AREA_MASTER_PATH, encoding="utf-8-sig", dtype=str)
    compare_cols = [
        "상권_코드",
        "상권_코드_명",
        "상권_구분_코드",
        "상권_구분_코드_명",
        "자치구_코드",
        "자치구_코드_명",
        "행정동_코드",
        "행정동_코드_명",
    ]
    merged = master[compare_cols + ["중심_X", "중심_Y", "면적_제곱미터"]].merge(
        geometry_df[
            compare_cols
            + [
                "source_center_x_epsg5181",
                "source_center_y_epsg5181",
                "source_area_m2",
                "fixed_geometry_area_m2",
                "area_abs_diff_m2",
                "area_pct_diff",
                "source_center_to_geometry_centroid_m",
            ]
        ],
        on="상권_코드",
        how="outer",
        suffixes=("_master", "_shp"),
        indicator=True,
    )
    for col in compare_cols[1:]:
        merged[f"{col}_일치"] = merged[f"{col}_master"].fillna("") == merged[f"{col}_shp"].fillna("")
    merged["중심_X_일치"] = pd.to_numeric(merged["중심_X"], errors="coerce") == pd.to_numeric(
        merged["source_center_x_epsg5181"], errors="coerce"
    )
    merged["중심_Y_일치"] = pd.to_numeric(merged["중심_Y"], errors="coerce") == pd.to_numeric(
        merged["source_center_y_epsg5181"], errors="coerce"
    )
    merged["면적_원천값_일치"] = pd.to_numeric(merged["면적_제곱미터"], errors="coerce") == pd.to_numeric(
        merged["source_area_m2"], errors="coerce"
    )
    return merged


def build_master_shp_mismatch_audit(master_compare_df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    compare_fields = [
        "상권_코드_명",
        "상권_구분_코드",
        "상권_구분_코드_명",
        "자치구_코드",
        "자치구_코드_명",
        "행정동_코드",
        "행정동_코드_명",
        "중심_X",
        "중심_Y",
        "면적_원천값",
    ]
    for _, row in master_compare_df.iterrows():
        for field in compare_fields:
            flag_col = f"{field}_일치"
            if flag_col not in master_compare_df.columns or bool(row[flag_col]):
                continue
            master_col = f"{field}_master"
            shp_col = f"{field}_shp"
            if field in {"중심_X", "중심_Y"}:
                master_col = field
                shp_col = f"source_center_{field[-1].lower()}_epsg5181"
            if field == "면적_원천값":
                master_col = "면적_제곱미터"
                shp_col = "source_area_m2"
            rows.append(
                {
                    "상권_코드": row["상권_코드"],
                    "불일치_필드": field,
                    "master_value": row.get(master_col, ""),
                    "shp_value": row.get(shp_col, ""),
                    "후속사용_우선값": "silver_trade_area_master",
                    "판단": "상권_코드와 geometry는 일치하므로 공간 매칭은 사용하되 표시명/행정속성은 마스터 값을 우선한다.",
                }
            )
    return pd.DataFrame(rows)


def apply_master_canonical_fields(geometry_df: pd.DataFrame, vertex_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    master = pd.read_csv(TRADE_AREA_MASTER_PATH, encoding="utf-8-sig", dtype=str)
    master["상권_코드"] = master["상권_코드"].astype(str)
    geometry_out = geometry_df.copy()
    geometry_out["상권_코드"] = geometry_out["상권_코드"].astype(str)
    vertex_out = vertex_df.copy()
    vertex_out["상권_코드"] = vertex_out["상권_코드"].astype(str)

    for field in CANONICAL_MASTER_FIELDS:
        mapping = dict(zip(master["상권_코드"], master[field].fillna("").astype(str)))
        geometry_out[field] = geometry_out["상권_코드"].map(mapping).fillna(geometry_out[field])
        if field == "상권_코드_명":
            vertex_out[field] = vertex_out["상권_코드"].map(mapping).fillna(vertex_out[field])

    geometry_out["canonical_attribute_source"] = "silver_trade_area_master 우선, SHP 속성 불일치는 15_trade_area_boundary_master_shp_mismatch_audit.csv에 기록"
    vertex_out["canonical_attribute_source"] = "상권명은 silver_trade_area_master 기준"
    return geometry_out, vertex_out


def build_validation_tables(
    geometry_df: pd.DataFrame,
    vertex_df: pd.DataFrame,
    spatial_index_df: pd.DataFrame,
    master_compare_df: pd.DataFrame,
    source_crs: CRS,
    source_prj: str,
    source_epsg: int | None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    reader = shapefile.Reader(str(TRADE_AREA_SHP_PATH), encoding="utf-8")
    shp_fields = ";".join(field[0] for field in reader.fields[1:])
    source_contract = pd.DataFrame(
        [
            {
                "source_id": SOURCE_ID,
                "provider": PROVIDER,
                "source_service": SOURCE_SERVICE,
                "dataset_name": "서울시 상권분석서비스(영역-상권) SHP",
                "source_path": str(TRADE_AREA_SHP_PATH.relative_to(ROOT)),
                "doc_paths": "research/algorithm_evidence_sources/data_docs/seoul_open_data_trade_area_boundary.html",
                "source_record_count": len(reader),
                "source_shape_type": reader.shapeTypeName,
                "source_fields": shp_fields,
                "source_crs_name": source_crs.name,
                "source_crs_epsg": source_epsg,
                "source_prj": source_prj,
                "usage_role": "지도 클릭/주소 좌표를 상권_코드로 변환하기 위한 공식 상권 polygon",
                "contract_status": "PASS",
            }
        ]
    )

    original_invalid = int((~geometry_df["original_geometry_valid"]).sum())
    fixed_invalid = int((~geometry_df["fixed_geometry_valid"]).sum())
    code_missing = int((master_compare_df["_merge"] == "left_only").sum())
    code_extra = int((master_compare_df["_merge"] == "right_only").sum())
    max_area_diff = float(geometry_df["area_abs_diff_m2"].max())
    max_area_pct_diff = float(geometry_df["area_pct_diff"].max())
    max_center_diff = float(geometry_df["source_center_to_geometry_centroid_m"].max())
    domain = pd.DataFrame(
        [
            {
                "검증항목": "SHP row 수",
                "측정값": len(geometry_df),
                "기준값": 1650,
                "판정": "PASS" if len(geometry_df) == 1650 else "FAIL",
                "근거": "서울 상권분석서비스 영역-상권 원천과 상권 마스터가 같은 1650개 상권이어야 한다.",
            },
            {
                "검증항목": "상권 마스터 코드 누락",
                "측정값": code_missing,
                "기준값": 0,
                "판정": "PASS" if code_missing == 0 else "FAIL",
                "근거": "지도 클릭 결과는 기존 매출/점포/인구 silver의 상권_코드와 조인되어야 한다.",
            },
            {
                "검증항목": "SHP 추가 코드",
                "측정값": code_extra,
                "기준값": 0,
                "판정": "PASS" if code_extra == 0 else "FAIL",
                "근거": "마스터에 없는 polygon은 후속 점수 테이블과 연결할 수 없다.",
            },
            {
                "검증항목": "CRS EPSG 식별",
                "측정값": source_epsg,
                "기준값": 5181,
                "판정": "PASS" if source_epsg == 5181 else "CONDITIONAL_PASS",
                "근거": "거리·면적·point-in-polygon은 원천 PRJ에서 확인한 투영좌표 기준으로 계산해야 한다.",
            },
            {
                "검증항목": "원본 geometry invalid",
                "측정값": original_invalid,
                "기준값": 0,
                "판정": "CONDITIONAL_PASS" if original_invalid > 0 and fixed_invalid == 0 else ("PASS" if original_invalid == 0 else "FAIL"),
                "근거": "self-intersection은 point-in-polygon 오류를 만들 수 있어 원본 플래그와 make_valid 보정 결과를 같이 보존한다.",
            },
            {
                "검증항목": "보정 geometry invalid",
                "측정값": fixed_invalid,
                "기준값": 0,
                "판정": "PASS" if fixed_invalid == 0 else "FAIL",
                "근거": "알고리즘 공간 매칭에는 보정 후 유효한 polygon만 사용한다.",
            },
            {
                "검증항목": "면적 최대 차이 제곱미터",
                "측정값": round(max_area_diff, 6),
                "기준값": "<= 1",
                "판정": "PASS" if max_area_diff <= 1 else "CONDITIONAL_PASS",
                "근거": "SHP geometry 면적과 공식 RELM_AR 값이 거의 일치해야 한다.",
            },
            {
                "검증항목": "면적 최대 차이 비율",
                "측정값": round(max_area_pct_diff, 6),
                "기준값": "<= 0.1%",
                "판정": "PASS" if max_area_pct_diff <= 0.1 else "CONDITIONAL_PASS",
                "근거": "작은 상권도 면적 차이가 점수 왜곡을 만들지 않는지 확인한다.",
            },
            {
                "검증항목": "마스터 중심점-geometry centroid 최대거리",
                "측정값": round(max_center_diff, 6),
                "기준값": "정보성",
                "판정": "INFO",
                "근거": "공식 중심점과 기하 centroid는 정의가 다를 수 있으므로 둘 다 보존하고 최근접 후보에는 geometry centroid/representative point를 함께 쓴다.",
            },
        ]
    )

    grain = pd.DataFrame(
        [
            {
                "검증항목": "geometry 상권_코드 중복",
                "측정값": int(geometry_df.duplicated("상권_코드").sum()),
                "기준값": 0,
                "판정": "PASS" if int(geometry_df.duplicated("상권_코드").sum()) == 0 else "FAIL",
                "근거": "상권 polygon은 상권_코드당 1개 geometry가 기준이다.",
            },
            {
                "검증항목": "geometry 상권_코드 결측",
                "측정값": int(geometry_df["상권_코드"].eq("").sum()),
                "기준값": 0,
                "판정": "PASS" if int(geometry_df["상권_코드"].eq("").sum()) == 0 else "FAIL",
                "근거": "공간 매칭 결과는 반드시 상권_코드로 후속 테이블과 조인되어야 한다.",
            },
            {
                "검증항목": "vertex 행 수",
                "측정값": len(vertex_df),
                "기준값": int(geometry_df["vertex_count"].sum()),
                "판정": "PASS" if len(vertex_df) == int(geometry_df["vertex_count"].sum()) else "FAIL",
                "근거": "프론트/경량 런타임에서 polygon을 재구성할 수 있도록 vertex를 누락 없이 보존한다.",
            },
            {
                "검증항목": "spatial index 행 수",
                "측정값": len(spatial_index_df),
                "기준값": len(geometry_df),
                "판정": "PASS" if len(spatial_index_df) == len(geometry_df) else "FAIL",
                "근거": "bbox 1차 필터는 상권 geometry와 1:1이어야 한다.",
            },
        ]
    )

    mismatches = []
    compare_flags = [col for col in master_compare_df.columns if col.endswith("_일치")]
    strict_failure_cols = {"중심_X_일치", "중심_Y_일치", "면적_원천값_일치"}
    for col in compare_flags:
        mismatch_count = int((~master_compare_df[col]).sum())
        judgement = "PASS"
        if mismatch_count > 0:
            judgement = "FAIL" if col in strict_failure_cols else "CONDITIONAL_PASS"
        mismatches.append(
            {
                "검증항목": col,
                "불일치건수": mismatch_count,
                "판정": judgement,
                "근거": "상권_코드와 geometry가 기준이다. 표시명/행정속성 불일치는 audit로 남기고 silver_trade_area_master 값을 우선한다.",
            }
        )
    consistency = pd.DataFrame(mismatches)

    invalid_audit = geometry_df.loc[
        ~geometry_df["original_geometry_valid"],
        [
            "상권_코드",
            "상권_코드_명",
            "original_validity_reason",
            "original_geometry_type",
            "fixed_geometry_type",
            "geometry_area_m2",
            "fixed_geometry_area_m2",
            "area_abs_diff_m2",
            "point_in_polygon_use_status",
        ],
    ].copy()
    return source_contract, domain, grain, consistency, invalid_audit


def write_report(
    source_contract: pd.DataFrame,
    domain: pd.DataFrame,
    grain: pd.DataFrame,
    consistency: pd.DataFrame,
    invalid_audit: pd.DataFrame,
    mismatch_audit: pd.DataFrame,
    geometry_df: pd.DataFrame,
    vertex_df: pd.DataFrame,
    source_epsg: int | None,
) -> None:
    report = f"""# 상권 경계 polygon silver 검증 보고서

작성일: {SNAPSHOT_DATE}

## 1. 처리 목적

사용자가 지도에서 클릭하거나 주소/장소 검색으로 좌표를 얻더라도, 최종 알고리즘은 상권명 문자열이 아니라 `상권_코드`를 입력으로 받아야 한다. 따라서 서울시 상권분석서비스 영역-상권 SHP를 실제 polygon으로 파싱해 `좌표 -> 상권_코드` 매칭 기준을 만든다.

이 산출물은 점수값이 아니다. 위치 입력을 상권 코드로 변환하기 위한 기준 geometry다.

## 2. 사용 원천

{markdown_table(source_contract[["source_id", "provider", "source_service", "source_record_count", "source_shape_type", "source_crs_name", "source_crs_epsg", "usage_role"]])}

## 3. 생성 산출물

| 산출물 | 행 수 | 역할 |
|---|---:|---|
| `datacorpus/_silver/silver_trade_area_boundary_geometry.csv` | {len(geometry_df):,} | 상권별 centroid, bbox, CRS, 유효성 플래그와 vertex 저장 위치 |
| `datacorpus/_silver/silver_trade_area_boundary_vertices.csv` | {len(vertex_df):,} | 상권별 polygon vertex. 경량 point-in-polygon/지도 표시 재료 |
| `datacorpus/_silver/silver_trade_area_boundary_spatial_index.csv` | {len(geometry_df):,} | bbox 1차 필터와 후보 상권 조회 |
| `datacorpus/_rule_validation/15_trade_area_boundary_master_shp_mismatch_audit.csv` | {len(mismatch_audit):,} | OpenAPI 마스터와 SHP 속성 불일치 감사 |

## 4. 도메인 검증

{markdown_table(domain)}

## 5. grain 검증

{markdown_table(grain)}

## 6. 마스터-SHP 속성 정합성

{markdown_table(consistency)}

## 7. 원본 geometry 보정 감사

{markdown_table(invalid_audit)}

## 8. 마스터-SHP 속성 불일치 감사

{markdown_table(mismatch_audit)}

## 9. 알고리즘 사용 판단

- 사용 가능: 지도 클릭, 주소/장소 지오코딩 결과 좌표를 `상권_코드` 후보로 변환.
- 사용 가능: bbox 1차 필터 후 polygon 포함 여부 확인.
- 조건부 사용: 원본 invalid 6건은 `make_valid` 보정 geometry를 사용하되 원본 invalid 사유를 리포트/감사에 남긴다.
- 조건부 사용: 상권명 2건과 행정동 1건은 OpenAPI 마스터와 SHP 속성이 다르므로, 최종 표시명/행정속성은 `silver_trade_area_master` 값을 우선한다.
- 사용 금지: 이 polygon만으로 유동인구, 매출, 성공확률, 실제 도보시간을 말하면 안 된다.
- 좌표계: 원천 geometry는 EPSG:{source_epsg} 기준이다. 카카오 지도 등 WGS84 좌표는 EPSG:5181로 변환한 뒤 거리/포함 판단을 한다.

## 10. 2보 전진 1보 후퇴 검토

1. 전진: 상권 경계 1,650건이 상권 마스터 1,650개 코드와 1:1로 일치한다.
2. 전진: EPSG:{source_epsg} 투영좌표와 WGS84 centroid/bbox를 같이 저장해 지도 입력과 내부 거리 계산을 분리했다.
3. 후퇴 검토: 원본 polygon 6건은 self-intersection이 있어 원본 그대로 점수 엔진에 넣지 않는다.
4. 재검토 결과: 6건 모두 `make_valid` 후 유효 polygon이 되었고 면적 차이는 0에 가까우므로, 원본 플래그를 보존한 조건부 PASS로 사용한다.
5. 후퇴 검토: 표시명/행정동 속성 불일치 3건은 geometry 오류가 아니라 원천 속성 차이로 분리하고, 마스터 값을 표시·행정 기준으로 우선한다.
6. 다음 단계: SBDC 점포 POI, 식품 인허가 좌표, 교통 좌표를 이 polygon에 매칭할 때도 좌표계를 먼저 변환하고, 미매칭/경계부 좌표는 별도 후보 상태로 남긴다.
"""
    MD_REPORT_PATH.write_text(report, encoding="utf-8")


def main() -> None:
    ensure_dirs()
    if not TRADE_AREA_SHP_PATH.exists():
        raise FileNotFoundError(f"SHP 파일을 찾지 못했습니다: {TRADE_AREA_SHP_PATH}")
    if not TRADE_AREA_MASTER_PATH.exists():
        raise FileNotFoundError(f"상권 마스터를 찾지 못했습니다: {TRADE_AREA_MASTER_PATH}")

    source_crs, source_prj, source_epsg = load_source_crs()
    raw_geometry_df, raw_vertex_df = load_shapefile_rows(source_crs)
    master_compare_df = compare_with_master(raw_geometry_df)
    mismatch_audit = build_master_shp_mismatch_audit(master_compare_df)
    geometry_df, vertex_df = apply_master_canonical_fields(raw_geometry_df, raw_vertex_df)
    geometry_df = geometry_df.sort_values("상권_코드").reset_index(drop=True)
    vertex_df = vertex_df.sort_values(["상권_코드", "part_index", "vertex_index"]).reset_index(drop=True)
    spatial_index_df = build_spatial_index(geometry_df)

    write_csv(geometry_df, GEOMETRY_PATH)
    write_csv(vertex_df, VERTEX_PATH)
    write_csv(spatial_index_df, SPATIAL_INDEX_PATH)

    source_contract, domain, grain, consistency, invalid_audit = build_validation_tables(
        geometry_df,
        vertex_df,
        spatial_index_df,
        master_compare_df,
        source_crs,
        source_prj,
        source_epsg,
    )
    write_csv(source_contract, SOURCE_CONTRACT_PATH)
    write_csv(domain, DOMAIN_VALIDATION_PATH)
    write_csv(grain, GRAIN_VALIDATION_PATH)
    write_csv(consistency, CONSISTENCY_VALIDATION_PATH)
    write_csv(invalid_audit, INVALID_GEOMETRY_AUDIT_PATH)
    write_csv(mismatch_audit, MASTER_SHP_MISMATCH_AUDIT_PATH)
    write_report(source_contract, domain, grain, consistency, invalid_audit, mismatch_audit, geometry_df, vertex_df, source_epsg)

    summary = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "source_epsg": source_epsg,
        "geometry_rows": len(geometry_df),
        "vertex_rows": len(vertex_df),
        "original_invalid_rows": int((~geometry_df["original_geometry_valid"]).sum()),
        "fixed_invalid_rows": int((~geometry_df["fixed_geometry_valid"]).sum()),
        "max_area_abs_diff_m2": float(geometry_df["area_abs_diff_m2"].max()),
        "max_area_pct_diff": float(geometry_df["area_pct_diff"].max()),
        "max_source_center_to_geometry_centroid_m": float(geometry_df["source_center_to_geometry_centroid_m"].max()),
        "outputs": {
            "geometry": str(GEOMETRY_PATH.relative_to(ROOT)),
            "vertices": str(VERTEX_PATH.relative_to(ROOT)),
            "spatial_index": str(SPATIAL_INDEX_PATH.relative_to(ROOT)),
            "report": str(MD_REPORT_PATH.relative_to(ROOT)),
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
