from __future__ import annotations

import json
from datetime import datetime
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

SBDC_POINT_PATH = SILVER_DIR / "silver_sbdc_store_poi_seoul_202603.csv"
INDUSTRY_BRIDGE_PATH = SILVER_DIR / "silver_industry_bridge_seoul_sbdc.csv"
TRADE_AREA_GEOMETRY_PATH = SILVER_DIR / "silver_trade_area_boundary_geometry.csv"
TRADE_AREA_SHP_PATH = ROOT / "datacorpus" / "_unzipped" / "서울시 상권분석서비스(영역-상권)" / "서울시 상권분석서비스(영역-상권).shp"
TRADE_AREA_PRJ_PATH = TRADE_AREA_SHP_PATH.with_suffix(".prj")

MATCH_PATH = SILVER_DIR / "silver_sbdc_store_poi_trade_area_match_202603.csv"
SMALL_COMPETITION_PATH = SILVER_DIR / "silver_sbdc_store_competition_trade_area_sbdc_small_202603.csv"
MEDIUM_COMPETITION_PATH = SILVER_DIR / "silver_sbdc_store_competition_trade_area_sbdc_medium_202603.csv"
SEOUL_SERVICE_COMPETITION_PATH = SILVER_DIR / "silver_sbdc_store_competition_trade_area_seoul_service_202603.csv"

SOURCE_CONTRACT_PATH = VALIDATION_DIR / "16_sbdc_trade_area_spatial_match_source_contract.csv"
DOMAIN_VALIDATION_PATH = VALIDATION_DIR / "16_sbdc_trade_area_spatial_match_domain_validation.csv"
GRAIN_VALIDATION_PATH = VALIDATION_DIR / "16_sbdc_trade_area_spatial_match_grain_validation.csv"
CONSISTENCY_VALIDATION_PATH = VALIDATION_DIR / "16_sbdc_trade_area_spatial_match_consistency_validation.csv"
MATCH_STATUS_PATH = VALIDATION_DIR / "16_sbdc_trade_area_spatial_match_status.csv"
UNMATCHED_SAMPLE_PATH = VALIDATION_DIR / "16_sbdc_trade_area_spatial_match_unmatched_sample.csv"
MULTI_MATCH_SAMPLE_PATH = VALIDATION_DIR / "16_sbdc_trade_area_spatial_match_multi_match_sample.csv"
SERVICE_BRIDGE_AUDIT_PATH = VALIDATION_DIR / "16_sbdc_trade_area_seoul_service_bridge_audit.csv"
SERVICE_UNMAPPED_AUDIT_PATH = VALIDATION_DIR / "16_sbdc_trade_area_seoul_service_unmapped_audit.csv"
NEAREST_DISTANCE_AUDIT_PATH = VALIDATION_DIR / "16_sbdc_trade_area_spatial_match_nearest_distance_audit.csv"
MULTI_CANDIDATE_AUDIT_PATH = VALIDATION_DIR / "16_sbdc_trade_area_spatial_match_multi_candidate_audit.csv"
MD_REPORT_PATH = RESEARCH_VALIDATION_DIR / "16_sbdc_trade_area_spatial_match_validation_20260704.md"

SNAPSHOT_DATE = "2026-07-04"
SOURCE_ID = "sbdc_store_info"
PROVIDER = "소상공인시장진흥공단"
SPATIAL_SOURCE_ID = "seoul_trade_area_boundary"
BOUNDARY_VERSION = "seoul_open_data_20260703_TbgisTrdarRelm"
CHUNK_SIZE = 75_000

SBDC_USECOLS = [
    "상가업소번호",
    "상호명",
    "지점명",
    "상권업종대분류코드",
    "상권업종대분류명",
    "상권업종중분류코드",
    "상권업종중분류명",
    "상권업종소분류코드",
    "상권업종소분류명",
    "표준산업분류코드",
    "표준산업분류명",
    "시군구코드",
    "시군구명",
    "행정동코드",
    "행정동명",
    "법정동코드",
    "법정동명",
    "지번주소",
    "도로명주소",
    "경도",
    "위도",
    "기준_년월",
    "기준_년분기_코드",
    "source_file",
    "snapshot_date",
    "원천행번호",
]


def ensure_dirs() -> None:
    for path in [SILVER_DIR, VALIDATION_DIR, RESEARCH_VALIDATION_DIR]:
        path.mkdir(parents=True, exist_ok=True)


def clean_text(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "none"}:
        return ""
    return text


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

    geometry_master = pd.read_csv(TRADE_AREA_GEOMETRY_PATH, encoding="utf-8-sig", dtype=str)
    master_by_code = {
        str(row["상권_코드"]): row
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
                "상권_코드_명": base.get("상권_코드_명", clean_text(rec.get("TRDAR_CD_N"))),
                "상권_구분_코드": base.get("상권_구분_코드", clean_text(rec.get("TRDAR_SE_C"))),
                "상권_구분_코드_명": base.get("상권_구분_코드_명", clean_text(rec.get("TRDAR_SE_1"))),
                "자치구_코드": base.get("자치구_코드", clean_text(rec.get("SIGNGU_CD"))),
                "자치구_코드_명": base.get("자치구_코드_명", clean_text(rec.get("SIGNGU_CD_"))),
                "행정동_코드": base.get("행정동_코드", clean_text(rec.get("ADSTRD_CD"))),
                "행정동_코드_명": base.get("행정동_코드_명", clean_text(rec.get("ADSTRD_CD_"))),
                "상권_면적_제곱미터": float(base.get("source_area_m2", rec.get("RELM_AR", 0)) or 0),
            }
        )
    return polygons, attrs, STRtree(polygons), source_crs


def match_one_point(point: Point, polygons: list[Any], attrs: list[dict[str, Any]], tree: STRtree) -> dict[str, Any]:
    candidate_indices = list(tree.query(point))
    matched_indices = [int(idx) for idx in candidate_indices if polygons[int(idx)].covers(point)]
    if matched_indices:
        # 여러 상권이 겹치는 예외는 작은 면적 상권을 더 구체적인 후보로 보고 1개만 선택한다.
        chosen_idx = min(matched_indices, key=lambda idx: polygons[idx].area)
        chosen = attrs[chosen_idx]
        status = "polygon_match" if len(matched_indices) == 1 else "multi_polygon_match_choose_smallest_area"
        return {
            "match_status": status,
            "match_candidate_count": len(matched_indices),
            "nearest_distance_m": 0.0,
            "nearest_상권_코드": chosen["상권_코드"],
            "nearest_상권_코드_명": chosen["상권_코드_명"],
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
        "자치구_코드": "",
        "자치구_코드_명": "",
        "행정동_코드": "",
        "행정동_코드_명": "",
        "상권_면적_제곱미터": "",
    }


def normalize_chunk(chunk: pd.DataFrame, transformer: Transformer, polygons: list[Any], attrs: list[dict[str, Any]], tree: STRtree) -> pd.DataFrame:
    out = chunk.copy()
    for col in SBDC_USECOLS:
        if col not in out.columns:
            out[col] = ""
    text_cols = [col for col in SBDC_USECOLS if col not in {"경도", "위도"}]
    for col in text_cols:
        out[col] = out[col].map(clean_text)
    lon = pd.to_numeric(out["경도"], errors="coerce")
    lat = pd.to_numeric(out["위도"], errors="coerce")
    x_arr, y_arr = transformer.transform(lon.astype(float).to_numpy(), lat.astype(float).to_numpy())
    out["x_epsg5181"] = x_arr
    out["y_epsg5181"] = y_arr

    match_rows: list[dict[str, Any]] = []
    for x_value, y_value, lon_value, lat_value in zip(x_arr, y_arr, lon, lat):
        if pd.isna(x_value) or pd.isna(y_value) or pd.isna(lon_value) or pd.isna(lat_value):
            match_rows.append(
                {
                    "match_status": "invalid_coordinate",
                    "match_candidate_count": 0,
                    "nearest_distance_m": "",
                    "nearest_상권_코드": "",
                    "nearest_상권_코드_명": "",
                    "polygon_index": "",
                    "상권_코드": "",
                    "상권_코드_명": "",
                    "상권_구분_코드": "",
                    "상권_구분_코드_명": "",
                    "자치구_코드": "",
                    "자치구_코드_명": "",
                    "행정동_코드": "",
                    "행정동_코드_명": "",
                    "상권_면적_제곱미터": "",
                }
            )
            continue
        match_rows.append(match_one_point(Point(float(x_value), float(y_value)), polygons, attrs, tree))

    match_df = pd.DataFrame(match_rows)
    out = pd.concat([out.reset_index(drop=True), match_df.reset_index(drop=True)], axis=1)
    out["source_id"] = SOURCE_ID
    out["provider"] = PROVIDER
    out["spatial_source_id"] = SPATIAL_SOURCE_ID
    out["boundary_version"] = BOUNDARY_VERSION
    out["spatial_match_crs"] = "EPSG:5181"
    out["spatial_match_method"] = "WGS84 좌표를 EPSG:5181로 변환 후 상권 polygon covers 검사"
    out["점수직접사용상태"] = out["match_status"].map(
        {
            "polygon_match": "상권 polygon 직접매칭 완료: 경쟁/밀집 보조 프록시로 조건부 사용",
            "multi_polygon_match_choose_smallest_area": "상권 polygon 다중매칭: 가장 작은 상권으로 1개 선택, audit 확인 후 조건부 사용",
            "unmatched_nearest_candidate": "상권 polygon 밖: 상권 점수 직접 사용 금지, 최근접 후보와 행정구역 fallback만 가능",
            "invalid_coordinate": "좌표 무효: 상권 점수 직접 사용 금지",
        }
    )
    return out


def aggregate_counts(match_chunk: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    matched = match_chunk[match_chunk["상권_코드"].astype(str).ne("")].copy()
    if matched.empty:
        return pd.DataFrame(columns=keys + ["점포수", "지점명보유점포수", "도로명주소보유점포수"])
    matched["점포수"] = 1
    matched["지점명보유점포수"] = matched["지점명"].fillna("").astype(str).ne("").astype(int)
    matched["도로명주소보유점포수"] = matched["도로명주소"].fillna("").astype(str).ne("").astype(int)
    return (
        matched.groupby(keys, dropna=False)[["점포수", "지점명보유점포수", "도로명주소보유점포수"]]
        .sum()
        .reset_index()
    )


def merge_aggregates(parts: list[pd.DataFrame], keys: list[str]) -> pd.DataFrame:
    if not parts:
        return pd.DataFrame(columns=keys + ["점포수", "지점명보유점포수", "도로명주소보유점포수"])
    merged = pd.concat(parts, ignore_index=True)
    value_cols = [col for col in merged.columns if col not in keys]
    return merged.groupby(keys, dropna=False)[value_cols].sum().reset_index().sort_values(keys).reset_index(drop=True)


def build_seoul_service_competition(small_df: pd.DataFrame, medium_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    trade_area = pd.read_csv(
        TRADE_AREA_GEOMETRY_PATH,
        encoding="utf-8-sig",
        dtype=str,
        usecols=[
            "상권_코드",
            "상권_코드_명",
            "상권_구분_코드",
            "상권_구분_코드_명",
            "자치구_코드",
            "자치구_코드_명",
            "행정동_코드",
            "행정동_코드_명",
        ],
    )
    bridge = pd.read_csv(INDUSTRY_BRIDGE_PATH, encoding="utf-8-sig", dtype=str).fillna("")
    trade_area["_join_key"] = 1
    bridge["_join_key"] = 1
    out = trade_area.merge(bridge, on="_join_key", how="inner").drop(columns="_join_key")

    small_counts = small_df[
        [
            "상권_코드",
            "상권업종소분류코드",
            "점포수",
            "지점명보유점포수",
        ]
    ].rename(
        columns={
            "상권업종소분류코드": "SBDC_소분류코드_후보",
            "점포수": "동종_후보소분류_점포수",
            "지점명보유점포수": "동종_후보소분류_지점명보유점포수",
        }
    )
    medium_counts = medium_df[
        [
            "상권_코드",
            "상권업종중분류코드",
            "점포수",
            "지점명보유점포수",
        ]
    ].rename(
        columns={
            "상권업종중분류코드": "SBDC_중분류코드_후보",
            "점포수": "유사_후보중분류_점포수",
            "지점명보유점포수": "유사_후보중분류_지점명보유점포수",
        }
    )
    out = out.merge(small_counts, on=["상권_코드", "SBDC_소분류코드_후보"], how="left")
    out = out.merge(medium_counts, on=["상권_코드", "SBDC_중분류코드_후보"], how="left")
    count_cols = [
        "동종_후보소분류_점포수",
        "동종_후보소분류_지점명보유점포수",
        "유사_후보중분류_점포수",
        "유사_후보중분류_지점명보유점포수",
    ]
    for col in count_cols:
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0).astype(int)
    out["source_id"] = "sbdc_store_info;seoul_trade_area_boundary;seoul_sales_trade_area"
    out["provider"] = "소상공인시장진흥공단;서울열린데이터광장"
    out["snapshot_date"] = SNAPSHOT_DATE
    out["usage_role"] = "서울 서비스업종별 상권 내 후보 동종/유사 점포 밀집 프록시"
    out["score_use_status"] = out["mapping_review_required"].astype(str).str.lower().isin({"true", "1"}).map(
        {
            True: "조건부_업종매핑수동검토필요",
            False: "조건부_상권공간매칭완료_업종매핑자동",
        }
    )

    audit = (
        bridge.assign(mapping_review_required_bool=bridge["mapping_review_required"].astype(str).str.lower().isin({"true", "1"}))
        .groupby(["mapping_review_required_bool", "score_use_status"], dropna=False)
        .agg(서비스업종수=("서비스_업종_코드", "nunique"))
        .reset_index()
    )
    return out.sort_values(["상권_코드", "서비스_업종_코드"]).reset_index(drop=True), audit


def build_service_unmapped_audit(bridge: pd.DataFrame) -> pd.DataFrame:
    industry_master_path = SILVER_DIR / "silver_industry_master_seoul_open_data.csv"
    if not industry_master_path.exists():
        return pd.DataFrame(columns=["서비스_업종_코드", "서비스_업종_코드_명", "SBDC_bridge_status", "score_use_status"])
    master = pd.read_csv(industry_master_path, encoding="utf-8-sig", dtype=str).fillna("")
    bridge_codes = set(bridge["서비스_업종_코드"].astype(str))
    out = master.loc[~master["서비스_업종_코드"].astype(str).isin(bridge_codes), ["서비스_업종_코드", "서비스_업종_코드_명"]].copy()
    out["SBDC_bridge_status"] = "미매핑"
    out["score_use_status"] = "SBDC POI 기반 동종/유사업종 경쟁 프록시 직접 사용 보류"
    out["notes_ko"] = "서울 서비스업종 마스터에는 있으나 SBDC bridge 63개에는 없는 업종이다. 서울 상권 점포 원천의 점포수/개폐업률은 계속 사용 가능하다."
    return out.sort_values("서비스_업종_코드").reset_index(drop=True)


def build_match_distribution_audits(match_path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    usecols = ["match_status", "nearest_distance_m", "match_candidate_count"]
    df = pd.read_csv(match_path, encoding="utf-8-sig", dtype=str, usecols=usecols)
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


def build_validation_tables(metrics: dict[str, Any], status_df: pd.DataFrame, service_audit: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    source_contract = pd.DataFrame(
        [
            {
                "source_id": SOURCE_ID,
                "provider": PROVIDER,
                "source_path": SBDC_POINT_PATH.relative_to(ROOT).as_posix(),
                "row_count": metrics["source_point_rows"],
                "doc_paths": "research/algorithm_evidence_sources/data_docs/data_go_kr_sbiz_store_info.html;research/site_selection_sources/10_sbiz_shop_data_portal.html",
                "usage_role": "상가업소 좌표와 업종코드를 상권 polygon에 매칭해 경쟁/밀집 프록시 생성",
                "contract_status": "PASS",
            },
            {
                "source_id": SPATIAL_SOURCE_ID,
                "provider": "서울열린데이터광장",
                "source_path": TRADE_AREA_SHP_PATH.relative_to(ROOT).as_posix(),
                "row_count": metrics["trade_area_polygon_rows"],
                "doc_paths": "research/rule_validation/15_trade_area_boundary_silver_validation_20260704.md",
                "usage_role": "SBDC POI 좌표를 상권_코드로 변환하는 polygon 기준",
                "contract_status": "PASS",
            },
        ]
    )
    domain = pd.DataFrame(
        [
            {
                "검증항목": "SBDC POI row 보존",
                "측정값": metrics["match_rows"],
                "기준값": metrics["source_point_rows"],
                "판정": "PASS" if metrics["match_rows"] == metrics["source_point_rows"] else "FAIL",
                "근거": "point별 공간매칭 결과는 원천 POI row를 누락하지 않아야 한다.",
            },
            {
                "검증항목": "상권 polygon 기준 존재",
                "측정값": metrics["trade_area_polygon_rows"],
                "기준값": 1650,
                "판정": "PASS" if metrics["trade_area_polygon_rows"] == 1650 else "FAIL",
                "근거": "상권_코드 변환은 검증된 1,650개 official polygon을 기준으로 한다.",
            },
            {
                "검증항목": "상권 polygon 직접매칭률",
                "측정값": f"{metrics['polygon_match_rate']:.4f}",
                "기준값": "정보성",
                "판정": "INFO",
                "근거": "SBDC 서울 점포 전체가 서울 상권분석서비스의 상권 polygon 내부에 있어야 하는 것은 아니므로 비율을 기록한다.",
            },
            {
                "검증항목": "미매칭 POI 보존",
                "측정값": metrics["unmatched_rows"],
                "기준값": "정보성",
                "판정": "INFO",
                "근거": "상권 polygon 밖 점포는 임의로 상권 점수에 넣지 않고 최근접 후보와 함께 보존한다.",
            },
            {
                "검증항목": "다중매칭 POI",
                "측정값": metrics["multi_match_rows"],
                "기준값": 0,
                "판정": "CONDITIONAL_PASS" if metrics["multi_match_rows"] > 0 else "PASS",
                "근거": "polygon 겹침이나 경계부 예외는 중복 집계하지 않고 가장 작은 면적 상권 1개로 선택해 audit한다.",
            },
        ]
    )
    grain = pd.DataFrame(
        [
            {
                "검증항목": "match 기준년월+상가업소번호 중복",
                "측정값": metrics["match_duplicate_key_rows"],
                "기준값": 0,
                "판정": "PASS" if metrics["match_duplicate_key_rows"] == 0 else "FAIL",
                "근거": "POI 한 개가 여러 상권에 중복 집계되면 경쟁 점포수가 부풀려진다.",
            },
            {
                "검증항목": "small 집계 합계",
                "측정값": metrics["small_sum"],
                "기준값": metrics["matched_rows"],
                "판정": "PASS" if metrics["small_sum"] == metrics["matched_rows"] else "FAIL",
                "근거": "상권×SBDC 소분류 집계는 polygon 매칭된 POI 전체를 정확히 대표해야 한다.",
            },
            {
                "검증항목": "medium 집계 합계",
                "측정값": metrics["medium_sum"],
                "기준값": metrics["matched_rows"],
                "판정": "PASS" if metrics["medium_sum"] == metrics["matched_rows"] else "FAIL",
                "근거": "상권×SBDC 중분류 집계는 유사업종 후보 프록시의 기본 합계다.",
            },
            {
                "검증항목": "서울 서비스업종 경쟁 테이블 grain",
                "측정값": metrics["service_competition_rows"],
                "기준값": metrics["trade_area_polygon_rows"] * metrics["bridge_service_count"],
                "판정": "PASS" if metrics["service_competition_rows"] == metrics["trade_area_polygon_rows"] * metrics["bridge_service_count"] else "FAIL",
                "근거": "서비스업종별 경쟁 후보는 상권 1,650개와 bridge 서비스업종 전체 조합을 유지해 0점포도 보존한다.",
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
                "검증항목": "SBDC 소분류 코드 결측",
                "측정값": metrics["null_small_code_rows"],
                "기준값": 0,
                "판정": "PASS" if metrics["null_small_code_rows"] == 0 else "FAIL",
                "근거": "경쟁 프록시는 업종 계층이 있어야 동종/유사업종 해석이 가능하다.",
            },
            {
                "검증항목": "좌표 무효 row",
                "측정값": metrics["invalid_coordinate_rows"],
                "기준값": 0,
                "판정": "PASS" if metrics["invalid_coordinate_rows"] == 0 else "FAIL",
                "근거": "SBDC POI 좌표가 무효면 polygon 매칭을 신뢰할 수 없다.",
            },
            {
                "검증항목": "업종 bridge 서비스 수",
                "측정값": metrics["bridge_service_count"],
                "기준값": 63,
                "판정": "PASS" if metrics["bridge_service_count"] == 63 else "CONDITIONAL_PASS",
                "근거": "서울 서비스업종과 SBDC 후보 업종 매핑 범위를 명시해야 한다.",
            },
            {
                "검증항목": "SBDC bridge 미매핑 서울 서비스업종 수",
                "측정값": metrics["service_unmapped_count"],
                "기준값": "정보성",
                "판정": "INFO",
                "근거": "SBDC POI 경쟁 프록시를 서울 서비스업종 전체에 무리하게 확장하지 않기 위해 미매핑 업종을 분리한다.",
            },
            {
                "검증항목": "업종 bridge 수동검토 필요 서비스 수",
                "측정값": metrics["bridge_manual_review_count"],
                "기준값": "정보성",
                "판정": "INFO",
                "근거": "수동검토 업종은 점수 직접 반영 전 별도 확인이 필요하다.",
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
    service_audit: pd.DataFrame,
    service_unmapped_audit: pd.DataFrame,
    nearest_audit: pd.DataFrame,
    multi_candidate_audit: pd.DataFrame,
    metrics: dict[str, Any],
) -> None:
    report = f"""# SBDC 상가업소 상권 polygon 공간매칭 검증 보고서

작성일: {SNAPSHOT_DATE}

## 1. 처리 목적

SBDC 상가업소 POI는 좌표와 SBDC 업종 대/중/소분류를 갖고 있어 경쟁·밀집 프록시로 유용하다. 다만 이전 단계에서는 행정동/자치구 단위까지만 집계되어 있었고, 상권 polygon에 직접 붙지 않았기 때문에 상권별 점수에 바로 넣지 않았다.

이번 단계는 `research/알고리즘_스펙_v1_20260703.md`와 `research/전처리_알고리즘_실행계획_20260703.md`의 계약대로, SBDC POI를 공식 상권 polygon에 point-in-polygon 매칭해 `상권_코드` 단위 경쟁/밀집 프록시를 만든다.

## 2. 사용 원천과 근거

{markdown_table(source_contract)}

근거:

- `research/rule_validation/13_sbdc_store_info_silver_validation_20260703.md`: SBDC POI는 좌표·업종 보유, 상권 polygon 매칭 전에는 점수 직접 사용 보류.
- `research/rule_validation/15_trade_area_boundary_silver_validation_20260704.md`: 상권 polygon 1,650건, EPSG:5181, point-in-polygon 가능.
- `research/알고리즘_스펙_v1_20260703.md`: 경쟁축은 [D03][D14][D15], Huff 경쟁보정 [M03]을 근거로 하되 성공확률로 표현 금지.

## 3. 생성 산출물

| 산출물 | 행 수 | 역할 |
|---|---:|---|
| `datacorpus/_silver/silver_sbdc_store_poi_trade_area_match_202603.csv` | {metrics["match_rows"]:,} | POI별 상권 polygon 매칭 결과와 미매칭 최근접 후보 |
| `datacorpus/_silver/silver_sbdc_store_competition_trade_area_sbdc_small_202603.csv` | {metrics["small_rows"]:,} | 상권×SBDC 소분류 점포수 |
| `datacorpus/_silver/silver_sbdc_store_competition_trade_area_sbdc_medium_202603.csv` | {metrics["medium_rows"]:,} | 상권×SBDC 중분류 점포수 |
| `datacorpus/_silver/silver_sbdc_store_competition_trade_area_seoul_service_202603.csv` | {metrics["service_competition_rows"]:,} | 서울 서비스업종별 동종/유사 후보 점포수 |
| `datacorpus/_rule_validation/16_sbdc_trade_area_seoul_service_unmapped_audit.csv` | {len(service_unmapped_audit):,} | SBDC bridge 미매핑 서울 서비스업종 감사 |
| `datacorpus/_rule_validation/16_sbdc_trade_area_spatial_match_nearest_distance_audit.csv` | {len(nearest_audit):,} | 미매칭 POI 최근접 상권 거리 분포 |
| `datacorpus/_rule_validation/16_sbdc_trade_area_spatial_match_multi_candidate_audit.csv` | {len(multi_candidate_audit):,} | 다중매칭 후보 수 분포 |

## 4. 공간매칭 상태

{markdown_table(status_df)}

## 5. 도메인 검증

{markdown_table(domain)}

## 6. grain 검증

{markdown_table(grain)}

## 7. 정합성 검증

{markdown_table(consistency)}

## 8. 서울 서비스업종 bridge 감사

{markdown_table(service_audit)}

## 9. SBDC bridge 미매핑 서울 서비스업종

{markdown_table(service_unmapped_audit.head(50))}

## 10. 미매칭 최근접 거리 감사

{markdown_table(nearest_audit)}

## 11. 다중매칭 후보 수 감사

{markdown_table(multi_candidate_audit)}

## 12. 알고리즘 사용 판단

- 사용 가능: 상권 내부 SBDC 동종/유사 업종 점포수, 경쟁·집적 보조 프록시.
- 조건부 사용: 서울 서비스업종과 SBDC 소분류 bridge가 `수동검토필요`인 업종은 점수 직접 반영 전 검토가 필요하다.
- 보류: SBDC bridge가 없는 서울 서비스업종 {metrics["service_unmapped_count"]:,}개는 SBDC POI 기반 동종/유사 업종 경쟁 프록시를 직접 쓰지 않는다.
- 보류: polygon 밖 미매칭 POI는 상권 점수에 직접 넣지 않고, 최근접 후보/행정구역 fallback 감사용으로만 쓴다.
- 금지: 이 값은 개별 점포 성공확률, 생존확률, 매출 보장이 아니다.

## 13. 2보 전진 1보 후퇴 검토

1. 전진: SBDC POI {metrics["match_rows"]:,}건을 누락 없이 공간매칭 결과 테이블로 보존했다.
2. 전진: polygon 내부 매칭 POI {metrics["matched_rows"]:,}건을 상권×업종 경쟁 프록시로 집계했다.
3. 후퇴 검토: 서울 전체 점포가 상권 polygon 안에 모두 들어갈 필요는 없으므로 미매칭 {metrics["unmatched_rows"]:,}건은 임의 배정하지 않았다.
4. 후퇴 검토: 다중매칭 {metrics["multi_match_rows"]:,}건은 중복 집계하지 않고 작은 면적 상권 1개로 선택했으며 별도 샘플을 남겼다.
5. 후퇴 검토: 서울 서비스업종 전체 100개 중 SBDC bridge가 있는 업종은 {metrics["bridge_service_count"]:,}개이므로, 미매핑 {metrics["service_unmapped_count"]:,}개에는 이 프록시를 무리하게 대입하지 않는다.
6. 재검토 결과: SBDC POI는 이제 상권 단위 경쟁/밀집 프록시로 조건부 사용 가능하지만, 서울 서비스업종 bridge 수동검토 업종과 미매핑 업종은 계속 보류 플래그를 유지한다.
"""
    MD_REPORT_PATH.write_text(report, encoding="utf-8")


def main() -> None:
    ensure_dirs()
    for path in [MATCH_PATH, UNMATCHED_SAMPLE_PATH, MULTI_MATCH_SAMPLE_PATH]:
        if path.exists():
            path.unlink()

    polygons, attrs, tree, polygon_crs = load_trade_area_polygons()
    transformer = Transformer.from_crs(CRS.from_epsg(4326), polygon_crs, always_xy=True)
    trade_area_codes = {attr["상권_코드"] for attr in attrs}

    small_parts: list[pd.DataFrame] = []
    medium_parts: list[pd.DataFrame] = []
    unmatched_samples: list[pd.DataFrame] = []
    multi_samples: list[pd.DataFrame] = []
    first_write = True
    match_rows = 0
    invalid_coordinate_rows = 0
    null_small_code_rows = 0

    dtype = {col: "string" for col in SBDC_USECOLS}
    for chunk_index, chunk in enumerate(
        pd.read_csv(SBDC_POINT_PATH, encoding="utf-8-sig", dtype=dtype, usecols=SBDC_USECOLS, chunksize=CHUNK_SIZE, low_memory=False),
        start=1,
    ):
        matched = normalize_chunk(chunk, transformer, polygons, attrs, tree)
        match_rows += len(matched)
        invalid_coordinate_rows += int(matched["match_status"].eq("invalid_coordinate").sum())
        null_small_code_rows += int(matched["상권업종소분류코드"].fillna("").astype(str).eq("").sum())

        small_keys = [
            "기준_년월",
            "기준_년분기_코드",
            "상권_코드",
            "상권_코드_명",
            "상권_구분_코드",
            "상권_구분_코드_명",
            "자치구_코드",
            "자치구_코드_명",
            "행정동_코드",
            "행정동_코드_명",
            "상권업종대분류코드",
            "상권업종대분류명",
            "상권업종중분류코드",
            "상권업종중분류명",
            "상권업종소분류코드",
            "상권업종소분류명",
        ]
        medium_keys = [
            "기준_년월",
            "기준_년분기_코드",
            "상권_코드",
            "상권_코드_명",
            "상권_구분_코드",
            "상권_구분_코드_명",
            "자치구_코드",
            "자치구_코드_명",
            "행정동_코드",
            "행정동_코드_명",
            "상권업종대분류코드",
            "상권업종대분류명",
            "상권업종중분류코드",
            "상권업종중분류명",
        ]
        small_parts.append(aggregate_counts(matched, small_keys))
        medium_parts.append(aggregate_counts(matched, medium_keys))

        if len(unmatched_samples) < 5:
            unmatched = matched[matched["match_status"].eq("unmatched_nearest_candidate")]
            if not unmatched.empty:
                unmatched_samples.append(unmatched.head(max(0, 200 - sum(len(x) for x in unmatched_samples))))
        if len(multi_samples) < 5:
            multi = matched[matched["match_status"].eq("multi_polygon_match_choose_smallest_area")]
            if not multi.empty:
                multi_samples.append(multi.head(max(0, 200 - sum(len(x) for x in multi_samples))))

        append_csv(matched, MATCH_PATH, first_write=first_write)
        first_write = False
        print(f"chunk {chunk_index} 처리 완료: 누적 {match_rows:,}건")

    small_keys = [
        "기준_년월",
        "기준_년분기_코드",
        "상권_코드",
        "상권_코드_명",
        "상권_구분_코드",
        "상권_구분_코드_명",
        "자치구_코드",
        "자치구_코드_명",
        "행정동_코드",
        "행정동_코드_명",
        "상권업종대분류코드",
        "상권업종대분류명",
        "상권업종중분류코드",
        "상권업종중분류명",
        "상권업종소분류코드",
        "상권업종소분류명",
    ]
    medium_keys = [
        "기준_년월",
        "기준_년분기_코드",
        "상권_코드",
        "상권_코드_명",
        "상권_구분_코드",
        "상권_구분_코드_명",
        "자치구_코드",
        "자치구_코드_명",
        "행정동_코드",
        "행정동_코드_명",
        "상권업종대분류코드",
        "상권업종대분류명",
        "상권업종중분류코드",
        "상권업종중분류명",
    ]
    small_df = merge_aggregates(small_parts, small_keys)
    medium_df = merge_aggregates(medium_parts, medium_keys)
    for df in [small_df, medium_df]:
        df["source_id"] = "sbdc_store_info;seoul_trade_area_boundary"
        df["provider"] = "소상공인시장진흥공단;서울열린데이터광장"
        df["snapshot_date"] = SNAPSHOT_DATE
        df["usage_role"] = "상권 polygon 내부 SBDC 점포수 기반 경쟁/밀집 프록시"
        df["score_use_status"] = "조건부_상권공간매칭완료_업종매핑검토후사용"
    write_csv(small_df, SMALL_COMPETITION_PATH)
    write_csv(medium_df, MEDIUM_COMPETITION_PATH)

    service_df, service_audit = build_seoul_service_competition(small_df, medium_df)
    write_csv(service_df, SEOUL_SERVICE_COMPETITION_PATH)
    write_csv(service_audit, SERVICE_BRIDGE_AUDIT_PATH)
    bridge = pd.read_csv(INDUSTRY_BRIDGE_PATH, encoding="utf-8-sig", dtype=str).fillna("")
    service_unmapped_audit = build_service_unmapped_audit(bridge)
    write_csv(service_unmapped_audit, SERVICE_UNMAPPED_AUDIT_PATH)

    if unmatched_samples:
        unmatched_cols = [
            "상가업소번호",
            "상호명",
            "상권업종소분류코드",
            "상권업종소분류명",
            "시군구명",
            "행정동명",
            "경도",
            "위도",
            "nearest_상권_코드",
            "nearest_상권_코드_명",
            "nearest_distance_m",
            "점수직접사용상태",
        ]
        write_csv(pd.concat(unmatched_samples, ignore_index=True)[unmatched_cols].head(200), UNMATCHED_SAMPLE_PATH)
    else:
        write_csv(pd.DataFrame(columns=["미매칭없음"]), UNMATCHED_SAMPLE_PATH)
    if multi_samples:
        multi_cols = [
            "상가업소번호",
            "상호명",
            "상권업종소분류코드",
            "상권업종소분류명",
            "시군구명",
            "행정동명",
            "경도",
            "위도",
            "상권_코드",
            "상권_코드_명",
            "match_candidate_count",
            "점수직접사용상태",
        ]
        write_csv(pd.concat(multi_samples, ignore_index=True)[multi_cols].head(200), MULTI_MATCH_SAMPLE_PATH)
    else:
        write_csv(pd.DataFrame(columns=["다중매칭없음"]), MULTI_MATCH_SAMPLE_PATH)

    match_row_count = count_csv_rows(MATCH_PATH)
    status_df = (
        pd.read_csv(MATCH_PATH, encoding="utf-8-sig", dtype=str, usecols=["match_status"])
        .assign(건수=1)
        .groupby("match_status", dropna=False)["건수"]
        .sum()
        .reset_index()
        .sort_values("건수", ascending=False)
    )
    write_csv(status_df, MATCH_STATUS_PATH)
    nearest_audit, multi_candidate_audit = build_match_distribution_audits(MATCH_PATH)
    write_csv(nearest_audit, NEAREST_DISTANCE_AUDIT_PATH)
    write_csv(multi_candidate_audit, MULTI_CANDIDATE_AUDIT_PATH)

    matched_rows = int(status_df[status_df["match_status"].isin(["polygon_match", "multi_polygon_match_choose_smallest_area"])]["건수"].sum())
    unmatched_rows = int(status_df[status_df["match_status"].eq("unmatched_nearest_candidate")]["건수"].sum())
    multi_match_rows = int(status_df[status_df["match_status"].eq("multi_polygon_match_choose_smallest_area")]["건수"].sum())
    bridge_manual_review_count = int(bridge["mapping_review_required"].astype(str).str.lower().isin({"true", "1"}).sum())
    unknown_trade_area_code_rows = int(len(set(small_df["상권_코드"].astype(str)) - trade_area_codes))
    duplicate_keys = pd.read_csv(MATCH_PATH, encoding="utf-8-sig", dtype=str, usecols=["기준_년월", "상가업소번호"]).duplicated(["기준_년월", "상가업소번호"]).sum()

    metrics: dict[str, Any] = {
        "source_point_rows": count_csv_rows(SBDC_POINT_PATH),
        "match_rows": match_row_count,
        "trade_area_polygon_rows": len(polygons),
        "matched_rows": matched_rows,
        "unmatched_rows": unmatched_rows,
        "multi_match_rows": multi_match_rows,
        "polygon_match_rate": matched_rows / match_row_count if match_row_count else 0,
        "match_duplicate_key_rows": int(duplicate_keys),
        "small_rows": len(small_df),
        "medium_rows": len(medium_df),
        "small_sum": int(small_df["점포수"].sum()) if not small_df.empty else 0,
        "medium_sum": int(medium_df["점포수"].sum()) if not medium_df.empty else 0,
        "service_competition_rows": len(service_df),
        "bridge_service_count": int(bridge["서비스_업종_코드"].nunique()),
        "bridge_manual_review_count": bridge_manual_review_count,
        "service_unmapped_count": len(service_unmapped_audit),
        "unknown_trade_area_code_rows": unknown_trade_area_code_rows,
        "null_small_code_rows": null_small_code_rows,
        "invalid_coordinate_rows": invalid_coordinate_rows,
    }
    source_contract, domain, grain, consistency = build_validation_tables(metrics, status_df, service_audit)
    write_csv(source_contract, SOURCE_CONTRACT_PATH)
    write_csv(domain, DOMAIN_VALIDATION_PATH)
    write_csv(grain, GRAIN_VALIDATION_PATH)
    write_csv(consistency, CONSISTENCY_VALIDATION_PATH)
    write_report(source_contract, domain, grain, consistency, status_df, service_audit, service_unmapped_audit, nearest_audit, multi_candidate_audit, metrics)

    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
