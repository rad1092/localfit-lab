from __future__ import annotations

import json
import re
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
RAW_DIR = ROOT / "datacorpus" / "_raw_ingest"
SILVER_DIR = ROOT / "datacorpus" / "_silver"
VALIDATION_DIR = ROOT / "datacorpus" / "_rule_validation"
RESEARCH_VALIDATION_DIR = ROOT / "research" / "rule_validation"

INGEST_MANIFEST_PATH = RAW_DIR / "ingest_manifest.csv"
TRADE_AREA_GEOMETRY_PATH = SILVER_DIR / "silver_trade_area_boundary_geometry.csv"
TRADE_AREA_SHP_PATH = ROOT / "datacorpus" / "_unzipped" / "서울시 상권분석서비스(영역-상권)" / "서울시 상권분석서비스(영역-상권).shp"
TRADE_AREA_PRJ_PATH = TRADE_AREA_SHP_PATH.with_suffix(".prj")

JUSO_CANDIDATE_PATH = SILVER_DIR / "silver_juso_address_normalization_candidate_sample.csv"
POINT_MATCH_PATH = SILVER_DIR / "silver_geocoding_point_trade_area_sample.csv"
REQUEST_AUDIT_PATH = SILVER_DIR / "silver_address_geocoding_request_audit.csv"

SOURCE_CONTRACT_PATH = VALIDATION_DIR / "19_address_geocoding_source_contract.csv"
DOMAIN_VALIDATION_PATH = VALIDATION_DIR / "19_address_geocoding_domain_validation.csv"
GRAIN_VALIDATION_PATH = VALIDATION_DIR / "19_address_geocoding_grain_validation.csv"
CONSISTENCY_VALIDATION_PATH = VALIDATION_DIR / "19_address_geocoding_consistency_validation.csv"
REQUEST_STATUS_PATH = VALIDATION_DIR / "19_address_geocoding_request_status.csv"
POINT_MATCH_STATUS_PATH = VALIDATION_DIR / "19_address_geocoding_point_match_status.csv"
MULTI_CANDIDATE_SAMPLE_PATH = VALIDATION_DIR / "19_address_geocoding_multi_candidate_sample.csv"
ZERO_RESULT_SAMPLE_PATH = VALIDATION_DIR / "19_address_geocoding_zero_result_sample.csv"
MD_REPORT_PATH = RESEARCH_VALIDATION_DIR / "19_address_geocoding_sample_validation_20260704.md"

SNAPSHOT_DATE = "2026-07-04"
SOURCE_ID = "vworld_juso_geocoding"
JUSO_SOURCE_ID = "juso_address_normalization"
BOUNDARY_VERSION = "seoul_open_data_20260703_TbgisTrdarRelm"


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


def write_csv(df: pd.DataFrame, path: Path) -> None:
    df.to_csv(path, index=False, encoding="utf-8-sig")


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


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_request_params(text: str) -> dict[str, Any]:
    if not clean_text(text):
        return {}
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def raw_path_to_abs(raw_path: str) -> Path:
    return ROOT / raw_path.replace("\\", "/")


def load_manifest_subset() -> pd.DataFrame:
    manifest = pd.read_csv(INGEST_MANIFEST_PATH, encoding="utf-8-sig", dtype=str).fillna("")
    source_mask = manifest["source_id"].isin(
        [
            SOURCE_ID,
            JUSO_SOURCE_ID,
            "vworld_juso_geocoding_docs",
            "sgis_spatial_api_docs",
        ]
    )
    path_mask = manifest["raw_path"].str.contains(r"juso|vworld", case=False, regex=True, na=False)
    subset = manifest[source_mask & path_mask].copy()
    subset["request_params"] = subset["request_params_json"].map(parse_request_params)
    subset["query_keyword"] = subset["request_params"].map(
        lambda data: clean_text(data.get("keyword") or data.get("address"))
    )
    subset["raw_path_posix"] = subset["raw_path"].str.replace("\\", "/", regex=False)
    subset["source_file"] = subset["raw_path_posix"]
    return subset


def extract_input_coordinate(notes: str) -> tuple[float | None, float | None]:
    match = re.search(r"입력 좌표=\(([-0-9.]+),\s*([-0-9.]+)\)", clean_text(notes))
    if not match:
        return None, None
    return float(match.group(1)), float(match.group(2))


def extract_input_address(notes: str) -> str:
    match = re.search(r"입력 주소=([^,]+)", clean_text(notes))
    return clean_text(match.group(1)) if match else ""


def parse_juso_candidates(manifest: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    juso_manifest = manifest[
        manifest["provider"].eq("Juso")
        & manifest["raw_path_posix"].str.endswith(".json")
        & manifest["raw_path_posix"].str.contains("/juso/", na=False)
    ].copy()

    for _, item in juso_manifest.iterrows():
        path = raw_path_to_abs(item["raw_path"])
        if not path.exists():
            continue
        data = load_json(path)
        results = data.get("results", {}) if isinstance(data, dict) else {}
        common = results.get("common", {}) if isinstance(results, dict) else {}
        candidates = results.get("juso", []) if isinstance(results, dict) else []
        if not isinstance(candidates, list):
            candidates = []

        total_count = int(clean_text(common.get("totalCount")) or 0)
        base = {
            "source_id": item["source_id"],
            "provider": item["provider"],
            "snapshot_date": item["snapshot_date"],
            "run_id": item["run_id"],
            "collection_status": item["collection_status"],
            "source_file": item["source_file"],
            "query_keyword": item["query_keyword"],
            "http_status": item["http_status"],
            "provider_result_code": item["provider_result_code"],
            "provider_result_message": item["provider_result_message"],
            "response_error_code": clean_text(common.get("errorCode")),
            "response_error_message": clean_text(common.get("errorMessage")),
            "response_total_count": total_count,
            "quality_notes_ko": item["quality_notes_ko"],
        }
        if not candidates:
            rows.append(
                {
                    **base,
                    "candidate_index": "",
                    "candidate_count_in_file": 0,
                    "siNm": "",
                    "sggNm": "",
                    "emdNm": "",
                    "admCd": "",
                    "roadAddr": "",
                    "jibunAddr": "",
                    "bdMgtSn": "",
                    "rnMgtSn": "",
                    "zipNo": "",
                    "bdNm": "",
                    "rn": "",
                    "engAddr": "",
                    "candidate_use_status": "zero_result_not_usable",
                    "caution_ko": "검색 결과가 없으므로 주소 후보로 사용할 수 없다.",
                }
            )
            continue

        for idx, candidate in enumerate(candidates, start=1):
            is_validated = item["run_id"] == "20260703_110550_juso_validated_address"
            is_city_sample = item["run_id"] == "20260703_094338_geocoding_samples"
            is_seoul = clean_text(candidate.get("siNm")) == "서울특별시"
            if item["collection_status"] == "superseded_low_quality_input":
                use_status = "superseded_sample_only"
                caution = "초기 batch는 품질 기준에 미달해 검증형 run으로 대체했으므로 점수나 자동선택에 쓰지 않는다."
            elif is_validated and is_seoul and total_count == 1:
                use_status = "validated_single_candidate"
                caution = "검증형 서울 주소이며 단일 후보라 주소 정규화 캐시 예시로 쓸 수 있다."
            elif is_validated and is_seoul and total_count > 1:
                use_status = "validated_multi_candidate_needs_choice"
                caution = "검증형 서울 주소지만 후보가 2개 이상이므로 자동 확정하지 않고 후보 선택/audit가 필요하다."
            elif is_city_sample:
                use_status = "city_hall_sample_only"
                caution = "서울시청 샘플 호출 검증용이다."
            else:
                use_status = "sample_only"
                caution = "검증형 주소 캐시가 아니므로 입력 경로 예시로만 보존한다."

            rows.append(
                {
                    **base,
                    "candidate_index": idx,
                    "candidate_count_in_file": len(candidates),
                    "siNm": clean_text(candidate.get("siNm")),
                    "sggNm": clean_text(candidate.get("sggNm")),
                    "emdNm": clean_text(candidate.get("emdNm")),
                    "admCd": clean_text(candidate.get("admCd")),
                    "roadAddr": clean_text(candidate.get("roadAddr")),
                    "jibunAddr": clean_text(candidate.get("jibunAddr")),
                    "bdMgtSn": clean_text(candidate.get("bdMgtSn")),
                    "rnMgtSn": clean_text(candidate.get("rnMgtSn")),
                    "zipNo": clean_text(candidate.get("zipNo")),
                    "bdNm": clean_text(candidate.get("bdNm")),
                    "rn": clean_text(candidate.get("rn")),
                    "engAddr": clean_text(candidate.get("engAddr")),
                    "candidate_use_status": use_status,
                    "caution_ko": caution,
                }
            )

    return pd.DataFrame(rows)


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
        code = clean_text(rec.get("TRDAR_CD"))
        geom = shape(shape_record.shape.__geo_interface__)
        if not geom.is_valid:
            geom = make_valid(geom)
        polygons.append(geom)
        base = master_by_code.get(code, {})
        attrs.append(
            {
                "polygon_index": idx,
                "상권_코드": code,
                "상권_코드_명": clean_text(base.get("상권_코드_명") or rec.get("TRDAR_CD_N")),
                "상권_구분_코드": clean_text(base.get("상권_구분_코드") or rec.get("TRDAR_SE_C")),
                "상권_구분_코드_명": clean_text(base.get("상권_구분_코드_명") or rec.get("TRDAR_SE_1")),
                "자치구_코드": clean_text(base.get("자치구_코드") or rec.get("SIGNGU_CD")),
                "자치구_코드_명": clean_text(base.get("자치구_코드_명") or rec.get("SIGNGU_CD_")),
                "행정동_코드": clean_text(base.get("행정동_코드") or rec.get("ADSTRD_CD")),
                "행정동_코드_명": clean_text(base.get("행정동_코드_명") or rec.get("ADSTRD_CD_")),
            }
        )
    return polygons, attrs, STRtree(polygons), source_crs


def match_point(point: Point, polygons: list[Any], attrs: list[dict[str, Any]], tree: STRtree) -> dict[str, Any]:
    candidate_indices = [int(idx) for idx in tree.query(point)]
    matched_indices = [idx for idx in candidate_indices if polygons[idx].covers(point)]
    if matched_indices:
        chosen_idx = min(matched_indices, key=lambda idx: polygons[idx].area)
        chosen = attrs[chosen_idx]
        return {
            "match_status": "polygon_match" if len(matched_indices) == 1 else "multi_polygon_match_choose_smallest_area",
            "match_candidate_count": len(matched_indices),
            "nearest_distance_m": 0.0,
            "nearest_상권_코드": chosen["상권_코드"],
            "nearest_상권_코드_명": chosen["상권_코드_명"],
            **chosen,
        }

    nearest_idx = int(tree.nearest(point))
    nearest = attrs[nearest_idx]
    return {
        "match_status": "unmatched_nearest_candidate",
        "match_candidate_count": 0,
        "nearest_distance_m": float(polygons[nearest_idx].distance(point)),
        "nearest_상권_코드": nearest["상권_코드"],
        "nearest_상권_코드_명": nearest["상권_코드_명"],
        "polygon_index": "",
        "상권_코드": "",
        "상권_코드_명": "",
        "상권_구분_코드": "",
        "상권_구분_코드_명": "",
        "자치구_코드": "",
        "자치구_코드_명": "",
        "행정동_코드": "",
        "행정동_코드_명": "",
    }


def parse_vworld_point(path: Path) -> tuple[float | None, float | None, str]:
    data = load_json(path)
    response = data.get("response", {}) if isinstance(data, dict) else {}
    result = response.get("result", {}) if isinstance(response, dict) else {}
    point = result.get("point", {}) if isinstance(result, dict) else {}
    crs = clean_text(result.get("crs") or response.get("input", {}).get("crs") or "EPSG:4326")
    x = clean_text(point.get("x"))
    y = clean_text(point.get("y"))
    if not x or not y:
        return None, None, crs
    return float(x), float(y), crs


def build_point_matches(manifest: pd.DataFrame) -> pd.DataFrame:
    polygons, attrs, tree, trade_area_crs = load_trade_area_polygons()
    transformer = Transformer.from_crs(CRS.from_epsg(4326), trade_area_crs, always_xy=True)
    rows: list[dict[str, Any]] = []

    point_sources = manifest[
        manifest["raw_path_posix"].str.endswith(".json")
        & manifest["provider"].isin(["VWorld", "Juso"])
        & manifest["collection_status"].isin(["success"])
    ].copy()
    for _, item in point_sources.iterrows():
        lon: float | None = None
        lat: float | None = None
        coordinate_role = ""
        point_crs = "EPSG:4326"
        input_address = extract_input_address(item["quality_notes_ko"]) or item["query_keyword"]
        if item["provider"] == "VWorld":
            lon, lat, point_crs = parse_vworld_point(raw_path_to_abs(item["raw_path"]))
            coordinate_role = "vworld_returned_geocode_point"
        elif item["run_id"] == "20260703_110550_juso_validated_address":
            lon, lat = extract_input_coordinate(item["quality_notes_ko"])
            coordinate_role = "manifest_input_coordinate_for_validated_juso_address"
        else:
            continue

        valid_coord = lon is not None and lat is not None and 123 <= lon <= 132 and 32 <= lat <= 39
        if valid_coord:
            x_5181, y_5181 = transformer.transform(float(lon), float(lat))
            match = match_point(Point(x_5181, y_5181), polygons, attrs, tree)
        else:
            x_5181, y_5181 = None, None
            match = {
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
            }
        rows.append(
            {
                "source_id": item["source_id"],
                "provider": item["provider"],
                "snapshot_date": item["snapshot_date"],
                "run_id": item["run_id"],
                "source_file": item["source_file"],
                "query_keyword": item["query_keyword"],
                "input_address_from_note": input_address,
                "coordinate_role": coordinate_role,
                "source_crs": point_crs,
                "target_crs": "EPSG:5181",
                "longitude": lon if lon is not None else "",
                "latitude": lat if lat is not None else "",
                "x_5181": x_5181 if x_5181 is not None else "",
                "y_5181": y_5181 if y_5181 is not None else "",
                "boundary_version": BOUNDARY_VERSION,
                "score_use_status": "input_geocoding_validation_only",
                "caution_ko": "주소/좌표 입력 경로 검증용 샘플이며 입지 점수 직접값이 아니다.",
                **match,
            }
        )
    return pd.DataFrame(rows)


def build_request_audit(manifest: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for _, item in manifest.iterrows():
        rows.append(
            {
                "source_id": item["source_id"],
                "provider": item["provider"],
                "snapshot_date": item["snapshot_date"],
                "run_id": item["run_id"],
                "dataset_name": item["dataset_name"],
                "source_file": item["source_file"],
                "collection_status": item["collection_status"],
                "http_status": item["http_status"],
                "provider_result_code": item["provider_result_code"],
                "provider_result_message": item["provider_result_message"],
                "query_keyword": item["query_keyword"],
                "spatial_unit": item["spatial_unit"],
                "area_code_type": item["area_code_type"],
                "quality_notes_ko": item["quality_notes_ko"],
            }
        )
    return pd.DataFrame(rows)


def build_validation_tables(
    manifest: pd.DataFrame,
    request_audit: pd.DataFrame,
    juso_candidates: pd.DataFrame,
    point_matches: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    source_contract = pd.DataFrame(
        [
            {
                "source_id": SOURCE_ID,
                "provider": "VWorld/Juso",
                "source_path": "datacorpus/_raw_ingest/20260703/vworld;datacorpus/_raw_ingest/20260703/juso",
                "request_rows": len(request_audit),
                "juso_candidate_rows": len(juso_candidates),
                "point_sample_rows": len(point_matches),
                "doc_paths": "research/algorithm_evidence_sources/data_docs/vworld_geocoder_api_guide.html;research/algorithm_evidence_sources/data_docs/juso_road_address_search_20260703.html",
                "usage_role": "주소 정규화, 좌표화, 좌표→상권 polygon 매칭 입력 검증",
                "contract_status": "CONDITIONAL_PASS",
            }
        ]
    )

    request_status = (
        request_audit.groupby(["provider", "source_id", "collection_status"], dropna=False)
        .size()
        .reset_index(name="request_count")
        .sort_values(["provider", "source_id", "collection_status"])
    )
    point_status = (
        point_matches.groupby("match_status", dropna=False)
        .size()
        .reset_index(name="point_count")
        .sort_values("match_status")
    )
    multi_sample = juso_candidates[juso_candidates["candidate_use_status"].eq("validated_multi_candidate_needs_choice")].head(200)
    zero_sample = juso_candidates[juso_candidates["candidate_use_status"].eq("zero_result_not_usable")].head(200)

    validated_requests = request_audit["run_id"].eq("20260703_110550_juso_validated_address").sum()
    low_quality_requests = request_audit["collection_status"].eq("superseded_low_quality_input").sum()
    vworld_success = (
        request_audit["provider"].eq("VWorld")
        & request_audit["collection_status"].eq("success")
        & request_audit["source_file"].str.endswith(".json")
    ).sum()
    juso_docs = request_audit["source_file"].str.contains("/juso/docs/", regex=False).sum()
    vworld_docs = request_audit["source_file"].str.contains("/vworld/docs/", regex=False).sum()

    domain = pd.DataFrame(
        [
            {
                "검증항목": "Juso 검증형 요청 수",
                "측정값": int(validated_requests),
                "기준값": 25,
                "판정": "PASS" if int(validated_requests) == 25 else "CONDITIONAL_PASS",
                "근거": "서울 25개 자치구 대표 주소 검증형 응답이 있어야 주소 정규화 경로를 대표 샘플로 볼 수 있다.",
            },
            {
                "검증항목": "VWorld 좌표 샘플",
                "측정값": int(vworld_success),
                "기준값": 1,
                "판정": "PASS" if int(vworld_success) >= 1 else "CONDITIONAL_PASS",
                "근거": "주소→WGS84 좌표 변환 응답이 최소 1건 이상 있어야 좌표계 계약을 확인할 수 있다.",
            },
            {
                "검증항목": "공식 문서 보존",
                "측정값": int(juso_docs + vworld_docs),
                "기준값": 2,
                "판정": "PASS" if int(juso_docs + vworld_docs) >= 2 else "CONDITIONAL_PASS",
                "근거": "Juso/VWorld 호출 파라미터와 저장 한계를 공식 문서로 추적해야 한다.",
            },
            {
                "검증항목": "초기 저품질 batch 보존",
                "측정값": int(low_quality_requests),
                "기준값": "정보성",
                "판정": "INFO",
                "근거": "실패 또는 대체된 주소 검색도 삭제하지 않고 왜 자동 사용하지 않는지 기록한다.",
            },
        ]
    )

    required_candidate_cols = ["source_file", "query_keyword", "response_error_code", "response_total_count"]
    candidate_required_null = int(
        juso_candidates[required_candidate_cols].apply(lambda col: col.map(clean_text).eq("")).any(axis=1).sum()
    )
    candidate_dup_cols = ["source_file", "candidate_index", "roadAddr", "bdMgtSn"]
    candidate_duplicate_rows = int(juso_candidates.duplicated(candidate_dup_cols, keep=False).sum())
    point_required_cols = ["source_file", "longitude", "latitude", "source_crs", "target_crs"]
    point_required_null = int(
        point_matches[point_required_cols].apply(lambda col: col.map(clean_text).eq("")).any(axis=1).sum()
    )
    grain = pd.DataFrame(
        [
            {
                "검증항목": "Juso 요청 필수키 결측",
                "측정값": candidate_required_null,
                "기준값": 0,
                "판정": "PASS" if candidate_required_null == 0 else "FAIL",
                "근거": "주소 후보는 원본 요청 문자열, 응답 코드, 총 후보 수를 보존해야 재현된다.",
            },
            {
                "검증항목": "Juso 후보 중복 row",
                "측정값": candidate_duplicate_rows,
                "기준값": 0,
                "판정": "PASS" if candidate_duplicate_rows == 0 else "CONDITIONAL_PASS",
                "근거": "같은 응답 파일의 같은 후보가 중복되면 주소 선택 UI가 과대 표시된다.",
            },
            {
                "검증항목": "좌표 샘플 필수키 결측",
                "측정값": point_required_null,
                "기준값": 0,
                "판정": "PASS" if point_required_null == 0 else "FAIL",
                "근거": "좌표→상권 매칭은 원본 좌표, 원본 CRS, 목표 CRS를 모두 남겨야 한다.",
            },
        ]
    )

    validated_candidate_rows = juso_candidates["candidate_use_status"].isin(
        ["validated_single_candidate", "validated_multi_candidate_needs_choice"]
    )
    non_seoul_validated = int((validated_candidate_rows & ~juso_candidates["siNm"].eq("서울특별시")).sum())
    multi_candidate_requests = int(
        juso_candidates[juso_candidates["candidate_use_status"].eq("validated_multi_candidate_needs_choice")][
            "source_file"
        ].nunique()
    )
    zero_result_rows = int(juso_candidates["candidate_use_status"].eq("zero_result_not_usable").sum())
    point_inside = int(point_matches["match_status"].isin(["polygon_match", "multi_polygon_match_choose_smallest_area"]).sum())
    invalid_points = int(point_matches["match_status"].eq("invalid_coordinate").sum())
    consistency = pd.DataFrame(
        [
            {
                "검증항목": "검증형 Juso 서울 외 후보",
                "측정값": non_seoul_validated,
                "기준값": 0,
                "판정": "PASS" if non_seoul_validated == 0 else "CONDITIONAL_PASS",
                "근거": "서울 상권 입력 검증용 샘플은 서울특별시 후보만 자동 사용 대상으로 본다.",
            },
            {
                "검증항목": "검증형 Juso 다중후보 요청",
                "측정값": multi_candidate_requests,
                "기준값": 0,
                "판정": "PASS" if multi_candidate_requests == 0 else "CONDITIONAL_PASS",
                "근거": "주소 검색 결과가 2개 이상이면 화면에서 후보를 선택하거나 추가 검증해야 한다.",
            },
            {
                "검증항목": "Juso 결과 없음 row",
                "측정값": zero_result_rows,
                "기준값": 0,
                "판정": "PASS" if zero_result_rows == 0 else "CONDITIONAL_PASS",
                "근거": "검색 실패는 삭제하지 않고 실패 사례로 남겨 주소 입력 UX와 재시도 규칙에 반영한다.",
            },
            {
                "검증항목": "좌표 샘플 polygon 내부 매칭",
                "측정값": point_inside,
                "기준값": len(point_matches),
                "판정": "PASS" if point_inside == len(point_matches) else "CONDITIONAL_PASS",
                "근거": "주소나 입력 좌표가 상권 polygon 내부에 들어가는지 확인해야 상권_코드로 확정할 수 있다.",
            },
            {
                "검증항목": "좌표 샘플 좌표 무효",
                "측정값": invalid_points,
                "기준값": 0,
                "판정": "PASS" if invalid_points == 0 else "FAIL",
                "근거": "WGS84 좌표가 없거나 서울 범위를 벗어나면 상권 매칭에 쓸 수 없다.",
            },
            {
                "검증항목": "VWorld 대량 저장 제한 반영",
                "측정값": "명시",
                "기준값": "명시",
                "판정": "PASS",
                "근거": "VWorld/Juso는 대량 좌표 원천이 아니라 주소·좌표 입력 검증 캐시로 제한한다.",
            },
        ]
    )

    return source_contract, domain, grain, consistency, request_status, point_status, multi_sample, zero_sample


def write_report(
    source_contract: pd.DataFrame,
    domain: pd.DataFrame,
    grain: pd.DataFrame,
    consistency: pd.DataFrame,
    request_status: pd.DataFrame,
    point_status: pd.DataFrame,
    metrics: dict[str, Any],
) -> None:
    report = f"""# 주소·좌표 정규화 샘플 silver 검증 보고서

작성일: {SNAPSHOT_DATE}

## 1. 처리 목적

Juso와 VWorld는 입지 점수를 직접 만드는 원천이 아니다. 사용자가 상권명을 외워 입력하지 않아도 `주소/장소명 -> 정규 주소 -> WGS84 좌표 -> EPSG:5181 변환 -> 상권 polygon 매칭 -> 상권_코드 후보`로 넘어가게 만드는 입력 보조 원천이다.

`research/algorithm_evidence_sources/서울부동산입지_데이터수집_적재계획_20260703.md`는 Juso를 주소 정규화, VWorld를 주소↔좌표 변환 및 공공 좌표 검증용으로 분류한다. 같은 문서는 VWorld/Juso 결과를 캐시하되 원본 요청 문자열과 응답을 함께 보관하라고 명시한다.

## 2. 사용 원천과 근거

{markdown_table(source_contract)}

근거:

- 데이터 수집 계획: `research/algorithm_evidence_sources/서울부동산입지_데이터수집_적재계획_20260703.md`
- 전처리 전 확인사항: `research/전처리_전_확인사항_20260703.md`
- Juso 공식 문서: `datacorpus/_raw_ingest/20260703/juso/docs/juso_road_address_search_20260703.html`
- VWorld 공식 문서: `datacorpus/_raw_ingest/20260703/vworld/docs/vworld_geocoder_2_0_20260703.html`

## 3. 생성 산출물

| 산출물 | 행 수 | 역할 |
|---|---:|---|
| `datacorpus/_silver/silver_address_geocoding_request_audit.csv` | {metrics["request_rows"]:,} | 주소/좌표 API 요청 단위 audit |
| `datacorpus/_silver/silver_juso_address_normalization_candidate_sample.csv` | {metrics["juso_candidate_rows"]:,} | Juso 주소 후보 응답 long 샘플 |
| `datacorpus/_silver/silver_geocoding_point_trade_area_sample.csv` | {metrics["point_rows"]:,} | 좌표 샘플의 상권 polygon 매칭 결과 |
| `datacorpus/_rule_validation/19_address_geocoding_multi_candidate_sample.csv` | {metrics["multi_sample_rows"]:,} | 다중 주소 후보 샘플 |
| `datacorpus/_rule_validation/19_address_geocoding_zero_result_sample.csv` | {metrics["zero_sample_rows"]:,} | 주소 검색 결과 없음 샘플 |

## 4. 요청 상태

{markdown_table(request_status)}

## 5. 좌표→상권 매칭 상태

{markdown_table(point_status)}

## 6. 도메인 검증

{markdown_table(domain)}

## 7. grain 검증

{markdown_table(grain)}

## 8. 정합성 검증

{markdown_table(consistency)}

## 9. 알고리즘 사용 판단

- 사용 가능: 주소 입력 후보 정규화, 도로명/지번/건물관리번호 확인, 좌표 샘플의 상권 polygon 매칭 경로 검증.
- 조건부 사용: 다중 후보 주소는 자동 확정하지 않고 후보 선택이나 추가 검증이 필요하다.
- 보류: VWorld 좌표 결과는 대량 저장 원천으로 보지 않고, 호출 시점 검증 또는 캐시 샘플로 제한한다.
- 금지: 주소·좌표 샘플을 입지 점수, 매출, 성공확률, 유입량으로 해석하지 않는다.

## 10. 2보 전진 1보 후퇴 검토

1. 전진: Juso 검증형 주소 25개 요청과 후보 응답을 보존했다.
2. 전진: VWorld WGS84 좌표 샘플과 Juso 검증형 입력 좌표를 상권 polygon 매칭 경로에 태웠다.
3. 후퇴 검토: 일부 Juso 주소는 다중 후보가 있으므로 주소 문자열만으로 상권을 자동 확정하지 않는다.
4. 후퇴 검토: 초기 Juso batch는 `superseded_low_quality_input`이므로 삭제하지 않고 실패/대체 사례로만 보존한다.
5. 후퇴 검토: VWorld/Juso는 점수 원천이 아니라 입력 변환·검증 보조 원천으로 제한한다.
6. 재검토 결과: 이 산출물은 하드코딩 없는 위치 입력 구조의 계약과 검증 샘플로 유지한다.
"""
    MD_REPORT_PATH.write_text(report, encoding="utf-8")


def main() -> None:
    ensure_dirs()
    manifest = load_manifest_subset()
    request_audit = build_request_audit(manifest)
    juso_candidates = parse_juso_candidates(manifest)
    point_matches = build_point_matches(manifest)

    (
        source_contract,
        domain,
        grain,
        consistency,
        request_status,
        point_status,
        multi_sample,
        zero_sample,
    ) = build_validation_tables(manifest, request_audit, juso_candidates, point_matches)

    write_csv(request_audit, REQUEST_AUDIT_PATH)
    write_csv(juso_candidates, JUSO_CANDIDATE_PATH)
    write_csv(point_matches, POINT_MATCH_PATH)
    write_csv(source_contract, SOURCE_CONTRACT_PATH)
    write_csv(domain, DOMAIN_VALIDATION_PATH)
    write_csv(grain, GRAIN_VALIDATION_PATH)
    write_csv(consistency, CONSISTENCY_VALIDATION_PATH)
    write_csv(request_status, REQUEST_STATUS_PATH)
    write_csv(point_status, POINT_MATCH_STATUS_PATH)
    write_csv(multi_sample, MULTI_CANDIDATE_SAMPLE_PATH)
    write_csv(zero_sample, ZERO_RESULT_SAMPLE_PATH)

    metrics = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "request_rows": len(request_audit),
        "juso_candidate_rows": len(juso_candidates),
        "point_rows": len(point_matches),
        "multi_sample_rows": len(multi_sample),
        "zero_sample_rows": len(zero_sample),
    }
    write_report(source_contract, domain, grain, consistency, request_status, point_status, metrics)

    print("완료: address geocoding sample silver")
    print(f"- request rows: {metrics['request_rows']:,}")
    print(f"- juso candidate rows: {metrics['juso_candidate_rows']:,}")
    print(f"- point rows: {metrics['point_rows']:,}")
    print(f"- report: {MD_REPORT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
