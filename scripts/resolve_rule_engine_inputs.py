# -*- coding: utf-8 -*-
"""
규칙 기반 입지판단 엔진 입력 resolver.

역할:
  - 지도 클릭/주소검색 결과 좌표를 상권_코드로 바꾼다.
  - 업종명 또는 서비스_업종_코드를 최종 서비스_업종_코드로 바꾼다.
  - 선택 후보는 코드에 하드코딩하지 않고 datacorpus/_gold lookup 산출물에서 읽는다.

주의:
  - 이 파일은 점수를 계산하지 않는다.
  - 좌표가 polygon 밖이면 실패가 아니라 최근접/인접 후보를 반환한다.
  - 업종 계층명은 UI 보조이고 최종 알고리즘 키는 서비스_업종_코드다.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from pyproj import Transformer
from shapely.geometry import Point, Polygon


ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "datacorpus" / "_gold"
GOLD_VALIDATION = ROOT / "datacorpus" / "_gold_validation"
RULE_VALIDATION = ROOT / "research" / "rule_validation"

RESOLVER_VERSION = "rule_input_resolver.v1.1-20260704"
WGS84_TO_EPSG5181 = Transformer.from_crs("EPSG:4326", "EPSG:5181", always_xy=True)


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(path)
    return pd.read_csv(path, encoding="utf-8-sig", low_memory=False)


def normalize_text(value: object) -> str:
    text = "" if pd.isna(value) else str(value)
    text = text.lower()
    text = re.sub(r"[\s\-_·/()]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def clean_value(value: object) -> Any:
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """최근접 후보 정렬용 거리다. 최종 polygon 판정은 거리값이 아니라 vertex 포함 여부로 한다."""
    radius = 6_371_000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def point_in_ring(lon: float, lat: float, ring: list[tuple[float, float]]) -> bool:
    """WGS84 lon/lat vertex에 ray casting을 적용한다. bbox로 후보를 좁힌 뒤에만 쓴다."""
    inside = False
    count = len(ring)
    if count < 3:
        return False
    j = count - 1
    for i in range(count):
        xi, yi = ring[i]
        xj, yj = ring[j]
        intersects = (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi
        if intersects:
            inside = not inside
        j = i
    return inside


@dataclass
class ResolverData:
    locations: pd.DataFrame
    spatial_index: pd.DataFrame
    vertices: pd.DataFrame
    industries: pd.DataFrame
    boundary_shapes: dict[str, list[Polygon]]


def build_boundary_shapes(vertices: pd.DataFrame) -> dict[str, list[Polygon]]:
    """상권별 EPSG:5181 polygon을 만든다. 좌표 판정과 인접 거리는 이 metric CRS에서만 계산한다."""
    shapes: dict[str, list[Polygon]] = {}
    for trade_area_code, code_df in vertices.groupby("상권_코드", sort=False):
        parts: list[Polygon] = []
        for _, part_df in code_df.groupby("part_index", sort=True):
            ordered = part_df.sort_values("vertex_index")
            coords = [
                (float(x), float(y))
                for x, y in zip(
                    pd.to_numeric(ordered["x_epsg5181"], errors="coerce"),
                    pd.to_numeric(ordered["y_epsg5181"], errors="coerce"),
                )
                if not (pd.isna(x) or pd.isna(y))
            ]
            if len(coords) < 3:
                continue
            if coords[0] != coords[-1]:
                coords.append(coords[0])
            polygon = Polygon(coords)
            if polygon.is_empty:
                continue
            parts.append(polygon)
        shapes[str(trade_area_code)] = parts
    return shapes


def load_resolver_data() -> ResolverData:
    locations = read_csv(GOLD / "gold_location_input_lookup.csv")
    spatial_index = read_csv(GOLD / "gold_location_spatial_index.csv")
    vertices = read_csv(GOLD / "gold_location_boundary_vertices.csv")
    industries = read_csv(GOLD / "gold_industry_selection_hierarchy.csv")

    for df in [locations, spatial_index, vertices]:
        df["상권_코드"] = df["상권_코드"].astype(str)
    industries["서비스_업종_코드"] = industries["서비스_업종_코드"].astype(str)
    boundary_shapes = build_boundary_shapes(vertices)

    return ResolverData(
        locations=locations,
        spatial_index=spatial_index,
        vertices=vertices,
        industries=industries,
        boundary_shapes=boundary_shapes,
    )


def location_row_to_dict(row: pd.Series, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    base = {
        "trade_area_code": clean_value(row.get("상권_코드")),
        "trade_area_name": clean_value(row.get("상권_코드_명")),
        "trade_area_type": clean_value(row.get("상권_구분_코드_명")),
        "district_code": clean_value(row.get("자치구_코드")),
        "district_name": clean_value(row.get("자치구_코드_명")),
        "admin_dong_code": clean_value(row.get("행정동_코드")),
        "admin_dong_name": clean_value(row.get("행정동_코드_명")),
        "display_label": clean_value(row.get("display_label")),
        "representative_lon_wgs84": clean_value(row.get("representative_lon_wgs84")),
        "representative_lat_wgs84": clean_value(row.get("representative_lat_wgs84")),
        "final_algorithm_key": clean_value(row.get("상권_코드")),
    }
    if extra:
        base.update(extra)
    return base


def polygon_distance_m(point_epsg5181: Point, shapes: list[Polygon]) -> float | None:
    distances = [shape.distance(point_epsg5181) for shape in shapes if not shape.is_empty]
    if not distances:
        return None
    return float(min(distances))


def resolve_location(lon: float, lat: float, data: ResolverData, nearest_limit: int = 5) -> dict[str, Any]:
    spatial = data.spatial_index
    locations = data.locations
    x_epsg5181, y_epsg5181 = WGS84_TO_EPSG5181.transform(lon, lat)
    point_epsg5181 = Point(float(x_epsg5181), float(y_epsg5181))

    # bbox는 후보를 줄이는 1차 필터다. bbox 안에 들어왔다고 상권 확정으로 보지 않는다.
    bbox_candidates = spatial[
        (pd.to_numeric(spatial["bbox_min_lon_wgs84"], errors="coerce") <= lon)
        & (pd.to_numeric(spatial["bbox_max_lon_wgs84"], errors="coerce") >= lon)
        & (pd.to_numeric(spatial["bbox_min_lat_wgs84"], errors="coerce") <= lat)
        & (pd.to_numeric(spatial["bbox_max_lat_wgs84"], errors="coerce") >= lat)
    ].copy()

    inside_codes: list[str] = []
    if not bbox_candidates.empty:
        for trade_area_code in bbox_candidates["상권_코드"].astype(str):
            shapes = data.boundary_shapes.get(str(trade_area_code), [])
            contained = any(shape.covers(point_epsg5181) for shape in shapes)
            if not contained:
                # 혹시 좌표 변환 또는 경계점 판정의 아주 작은 오차가 있을 때만 WGS84 ray casting으로 보조 확인한다.
                fallback_vertices = data.vertices[data.vertices["상권_코드"].astype(str) == str(trade_area_code)]
                for _, part_df in fallback_vertices.groupby("part_index", sort=True):
                    ring = list(
                        zip(
                            pd.to_numeric(part_df.sort_values("vertex_index")["lon_wgs84"], errors="coerce"),
                            pd.to_numeric(part_df.sort_values("vertex_index")["lat_wgs84"], errors="coerce"),
                        )
                    )
                    if point_in_ring(lon, lat, ring):
                        contained = True
                        break
            if contained:
                inside_codes.append(str(trade_area_code))

    resolved_rows = locations[locations["상권_코드"].astype(str).isin(inside_codes)].copy()
    resolved = [location_row_to_dict(row, {"resolution_status": "inside_polygon"}) for _, row in resolved_rows.iterrows()]

    if len(resolved) == 1:
        location_resolution_status = "single_inside_confirmed"
        rule_ko = "inside_polygon_count가 1이면 상권_코드를 확정한다."
    elif len(resolved) > 1:
        location_resolution_status = "multiple_inside_candidates"
        rule_ko = "여러 상권 polygon에 동시에 포함되면 자동 단일 확정하지 않고 후보를 모두 반환한다."
    else:
        location_resolution_status = "outside_nearest_candidates"
        rule_ko = "polygon 내부 상권이 없으면 최근접 후보를 비교용으로만 반환한다."

    nearest = locations.copy()
    nearest["_representative_distance_m"] = nearest.apply(
        lambda row: haversine_m(
            lon,
            lat,
            float(row["representative_lon_wgs84"]),
            float(row["representative_lat_wgs84"]),
        ),
        axis=1,
    )
    nearest_rows = nearest.sort_values("_representative_distance_m").head(nearest_limit)
    nearest_candidates = [
        location_row_to_dict(
            row,
            {
                "distance_m": round(float(row["_representative_distance_m"]), 2),
                "distance_basis": "representative_point_haversine",
                "resolution_status": "nearest_candidate",
            },
        )
        for _, row in nearest_rows.iterrows()
    ]
    boundary_distance_rows = []
    for _, row in locations.iterrows():
        trade_area_code = str(row["상권_코드"])
        distance_m = polygon_distance_m(point_epsg5181, data.boundary_shapes.get(trade_area_code, []))
        if distance_m is None:
            continue
        boundary_distance_rows.append((trade_area_code, distance_m, row))
    boundary_distance_rows.sort(key=lambda item: (item[1], item[0]))
    nearby_boundary_candidates = [
        location_row_to_dict(
            row,
            {
                "boundary_distance_m": round(float(distance_m), 2),
                "distance_basis": "epsg5181_polygon_distance",
                "resolution_status": "inside_polygon" if trade_area_code in inside_codes else "nearby_boundary_candidate",
            },
        )
        for trade_area_code, distance_m, row in boundary_distance_rows[:nearest_limit]
    ]

    return {
        "resolver_version": RESOLVER_VERSION,
        "input": {"lon_wgs84": lon, "lat_wgs84": lat},
        "input_epsg5181": {"x": round(float(x_epsg5181), 4), "y": round(float(y_epsg5181), 4)},
        "coordinate_rule_ko": "입력은 WGS84 경위도이며, 포함 판정과 인접 거리 계산은 서울권 metric 좌표계인 EPSG:5181로 변환해 수행한다.",
        "location_resolution_status": location_resolution_status,
        "bbox_candidate_count": int(len(bbox_candidates)),
        "inside_polygon_count": int(len(resolved)),
        "resolved_trade_areas": resolved,
        "nearest_candidates": nearest_candidates,
        "nearby_boundary_candidates": nearby_boundary_candidates,
        "rule_ko": rule_ko,
    }


def industry_row_to_dict(row: pd.Series, match_type: str) -> dict[str, Any]:
    return {
        "service_industry_code": clean_value(row.get("서비스_업종_코드")),
        "service_industry_name": clean_value(row.get("서비스_업종_코드_명")),
        "selection_path": clean_value(row.get("selection_path")),
        "display_label": clean_value(row.get("industry_display_label")),
        "direct_score_allowed": bool(clean_value(row.get("direct_score_allowed"))),
        "score_use_status": clean_value(row.get("score_use_status")),
        "final_algorithm_key": clean_value(row.get("서비스_업종_코드")),
        "match_type": match_type,
        "rule_ko": "업종명은 표시·검색용이고 최종 알고리즘 키는 서비스_업종_코드다.",
    }


def resolve_industry(query: str, data: ResolverData, limit: int = 10) -> dict[str, Any]:
    industries = data.industries.copy()
    normalized_query = normalize_text(query)

    code_match = industries[industries["서비스_업종_코드"].astype(str).str.upper() == str(query).strip().upper()]
    if not code_match.empty:
        matches = [industry_row_to_dict(row, "code_exact") for _, row in code_match.iterrows()]
    else:
        exact_name = industries[industries["서비스_업종_코드_명"].map(normalize_text) == normalized_query]
        if not exact_name.empty:
            matches = [industry_row_to_dict(row, "name_exact") for _, row in exact_name.iterrows()]
        else:
            search = industries[industries["industry_search_text"].fillna("").map(lambda text: normalized_query in normalize_text(text))].head(limit)
            matches = [industry_row_to_dict(row, "text_candidate") for _, row in search.iterrows()]

    return {
        "resolver_version": RESOLVER_VERSION,
        "input": {"industry_query": query},
        "match_count": len(matches),
        "matches": matches[:limit],
        "rule_ko": "코드 정확일치, 이름 정확일치, 검색 후보 순서로 찾되 최종 키는 서비스_업종_코드다.",
    }


def write_self_test_report(validation_df: pd.DataFrame) -> None:
    GOLD_VALIDATION.mkdir(parents=True, exist_ok=True)
    RULE_VALIDATION.mkdir(parents=True, exist_ok=True)

    validation_path = GOLD_VALIDATION / "27_input_resolver_rule_validation.csv"
    validation_df.to_csv(validation_path, index=False, encoding="utf-8-sig")
    summary = validation_df.groupby("result").size().reset_index(name="count")
    summary.to_csv(GOLD_VALIDATION / "27_input_resolver_rule_validation_summary.csv", index=False, encoding="utf-8-sig")

    pass_count = int((validation_df["result"] == "PASS").sum())
    fail_count = int((validation_df["result"] == "FAIL").sum())
    report_lines = [
        "# 위치·업종 입력 resolver 자체 검증",
        "",
        "작성일: 2026-07-04",
        "",
        "## 1. 목적",
        "",
        "`datacorpus/_gold` lookup 산출물이 실제로 좌표와 업종 입력을 코드로 확정할 수 있는지 확인한다.",
        "",
        "## 2. 검증 결과",
        "",
        f"- PASS: {pass_count}",
        f"- FAIL: {fail_count}",
        "",
        "| 규칙 | 관측값 | 기대값 | 결과 | 이유 |",
        "|---|---|---|---|---|",
    ]
    for _, row in validation_df.iterrows():
        report_lines.append(
            "| {rule_name} | {observed} | {expected} | {result} | {reason_ko} |".format(
                rule_name=str(row["rule_name"]).replace("|", "/"),
                observed=str(row["observed"]).replace("|", "/"),
                expected=str(row["expected"]).replace("|", "/"),
                result=row["result"],
                reason_ko=str(row["reason_ko"]).replace("|", "/"),
            )
        )
    report_lines.extend(
        [
            "",
            "## 3. 2보 전진 1보 후퇴 검토",
            "",
            "1. 전진: 대표좌표 샘플을 넣으면 원래 상권_코드가 polygon 내부 후보로 돌아오는지 확인했다.",
            "2. 전진: 업종 코드와 업종명 입력이 모두 서비스_업종_코드로 확정되는지 확인했다.",
            "3. 후퇴: polygon 밖 좌표는 점수 산정으로 바로 넘기지 않고 대표점 거리 후보와 경계거리 후보만 반환한다.",
            "4. 후퇴: direct_score_allowed가 False인 업종은 선택 가능하더라도 매출 축 직접 산정 한계를 노출해야 한다.",
            "",
            "## 4. 다음 작업",
            "",
            "1. 이 resolver를 웹/API 레이어에서 호출하거나 동일 로직으로 이식한다.",
            "2. 좌표가 여러 상권 polygon에 걸치는 예외 케이스를 실제 사용자 흐름에서 표시한다.",
            "3. resolver가 확정한 `상권_코드`, `서비스_업종_코드`만 점수 엔진에 전달한다.",
        ]
    )
    (RULE_VALIDATION / "27_input_resolver_validation_20260704.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    summary_json = {
        "resolver_version": RESOLVER_VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "validation_csv": str(validation_path.relative_to(ROOT)),
        "report": "research/rule_validation/27_input_resolver_validation_20260704.md",
    }
    (GOLD_VALIDATION / "27_input_resolver_summary.json").write_text(
        json.dumps(summary_json, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def add_check(rows: list[dict[str, Any]], rule_name: str, observed: object, expected: object, passed: bool, reason_ko: str) -> None:
    rows.append(
        {
            "rule_name": rule_name,
            "observed": observed,
            "expected": expected,
            "result": "PASS" if passed else "FAIL",
            "reason_ko": reason_ko,
        }
    )


def run_self_test(data: ResolverData) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []

    sample_locations = data.locations.sort_values("상권_코드").iloc[:: max(1, len(data.locations) // 20)].head(20)
    resolved_ok = 0
    for _, row in sample_locations.iterrows():
        result = resolve_location(float(row["representative_lon_wgs84"]), float(row["representative_lat_wgs84"]), data)
        inside_codes = {item["trade_area_code"] for item in result["resolved_trade_areas"]}
        if str(row["상권_코드"]) in inside_codes:
            resolved_ok += 1
    add_check(
        rows,
        "대표좌표 20개 샘플은 원래 상권 polygon 안으로 판정",
        f"{resolved_ok}/20",
        "20/20",
        resolved_ok == 20,
        "상권 대표좌표가 자기 polygon으로 돌아오지 않으면 지도 클릭 입력을 상권_코드로 안전하게 바꿀 수 없다.",
    )
    add_check(
        rows,
        "상권별 EPSG:5181 polygon shape가 모두 생성됨",
        f"shape_codes={len(data.boundary_shapes)}, empty_shape_codes={sum(1 for shapes in data.boundary_shapes.values() if not shapes)}",
        "1650개 상권 / 빈 shape 0",
        len(data.boundary_shapes) == 1650 and all(data.boundary_shapes.values()),
        "지도 클릭 판정은 이름 목록이 아니라 경계 polygon을 기준으로 해야 하므로 모든 상권의 도형 생성이 필요하다.",
    )

    outside = resolve_location(126.0, 36.5, data)
    add_check(
        rows,
        "서울 밖 좌표는 점수 확정이 아니라 후보 반환",
        f"status={outside['location_resolution_status']}, inside={outside['inside_polygon_count']}, nearest={len(outside['nearest_candidates'])}, boundary_nearby={len(outside['nearby_boundary_candidates'])}",
        "outside_nearest_candidates / inside=0 / nearest>0 / boundary_nearby>0",
        (
            outside["location_resolution_status"] == "outside_nearest_candidates"
            and outside["inside_polygon_count"] == 0
            and len(outside["nearest_candidates"]) > 0
            and len(outside["nearby_boundary_candidates"]) > 0
        ),
        "polygon 밖 좌표를 억지로 특정 상권으로 확정하면 잘못된 입지 리포트가 생성된다.",
    )
    boundary_sorted = all(
        outside["nearby_boundary_candidates"][i]["boundary_distance_m"]
        <= outside["nearby_boundary_candidates"][i + 1]["boundary_distance_m"]
        for i in range(len(outside["nearby_boundary_candidates"]) - 1)
    )
    add_check(
        rows,
        "인접 후보는 대표점이 아니라 polygon 경계거리 기준으로 정렬",
        [item["boundary_distance_m"] for item in outside["nearby_boundary_candidates"]],
        "EPSG:5181 polygon distance 오름차순",
        boundary_sorted
        and all(item["distance_basis"] == "epsg5181_polygon_distance" for item in outside["nearby_boundary_candidates"]),
        "상권 중심점이 넓은 상권에서 치우칠 수 있으므로, 지도 클릭의 인접 후보는 경계와의 실제 거리를 기준으로 봐야 한다.",
    )

    code_samples = data.industries.sort_values("서비스_업종_코드").head(20)
    code_ok = 0
    name_ok = 0
    for _, row in code_samples.iterrows():
        by_code = resolve_industry(str(row["서비스_업종_코드"]), data)
        by_name = resolve_industry(str(row["서비스_업종_코드_명"]), data)
        if by_code["matches"] and by_code["matches"][0]["service_industry_code"] == row["서비스_업종_코드"]:
            code_ok += 1
        if by_name["matches"] and by_name["matches"][0]["service_industry_code"] == row["서비스_업종_코드"]:
            name_ok += 1
    add_check(
        rows,
        "업종 코드 20개 샘플은 서비스_업종_코드로 정확 확정",
        f"{code_ok}/20",
        "20/20",
        code_ok == 20,
        "UI가 코드를 넘기면 이름 검색을 거치지 않고 바로 알고리즘 키가 확정되어야 한다.",
    )
    add_check(
        rows,
        "업종명 20개 샘플은 서비스_업종_코드 후보로 연결",
        f"{name_ok}/20",
        "20/20",
        name_ok == 20,
        "사용자가 이름으로 검색하더라도 최종 조인은 이름이 아니라 서비스_업종_코드여야 한다.",
    )

    direct_false_with_missing = data.industries[
        (~data.industries["direct_score_allowed"].astype(str).str.lower().isin(["true", "1"]))
        & (
            (~data.industries["매출_원천_존재"].astype(str).str.lower().isin(["true", "1"]))
            | (~data.industries["점포_원천_존재"].astype(str).str.lower().isin(["true", "1"]))
        )
    ]
    add_check(
        rows,
        "매출 또는 점포 원천이 없는 업종은 직접점수 가능으로 표시하지 않음",
        len(direct_false_with_missing),
        "37개 한계 업종이 direct_score_allowed=False로 노출",
        len(direct_false_with_missing) == 37,
        "선택 가능한 업종과 전체 입지점수 직접 산정 가능한 업종을 분리해야 리포트가 과장되지 않는다.",
    )

    itaewon = data.locations[data.locations["상권_코드"].astype(str) == "3001491"].iloc[0]
    loc_result = resolve_location(float(itaewon["representative_lon_wgs84"]), float(itaewon["representative_lat_wgs84"]), data)
    ind_result = resolve_industry("한식음식점", data)
    end_to_end_ok = (
        any(item["trade_area_code"] == "3001491" for item in loc_result["resolved_trade_areas"])
        and ind_result["matches"]
        and ind_result["matches"][0]["service_industry_code"] == "CS100001"
    )
    add_check(
        rows,
        "대표 예시 이태원 좌표+한식음식점 입력 후보/코드 반환",
        f"location_inside={loc_result['inside_polygon_count']}, boundary_nearby={len(loc_result['nearby_boundary_candidates'])}, industry_match={ind_result['match_count']}",
        "위치 후보에 3001491 포함 + 경계거리 후보 반환 + 업종 CS100001 확정",
        end_to_end_ok,
        "점수 엔진 호출 전 resolver는 위치 후보와 업종 코드를 반환하고, 다중 위치 후보는 사용자가 선택하거나 우선순위 규칙을 거쳐야 한다.",
    )

    add_check(
        rows,
        "중첩 상권 좌표는 자동 단일 확정하지 않음",
        f"status={loc_result['location_resolution_status']}, inside={loc_result['inside_polygon_count']}",
        "multiple_inside_candidates",
        loc_result["inside_polygon_count"] > 1 and loc_result["location_resolution_status"] == "multiple_inside_candidates",
        "관광특구·발달상권·골목상권이 겹칠 수 있으므로 다중 포함 좌표는 사용자가 후보를 선택하거나 우선순위 규칙을 별도로 적용해야 한다.",
    )
    add_check(
        rows,
        "중첩 상권 좌표의 인접 후보 선두에는 포함 상권이 노출됨",
        [item["trade_area_code"] for item in loc_result["nearby_boundary_candidates"][: loc_result["inside_polygon_count"]]],
        "polygon 내부 후보는 boundary_distance_m=0으로 우선 노출",
        all(
            float(item["boundary_distance_m"]) == 0.0
            for item in loc_result["nearby_boundary_candidates"][: loc_result["inside_polygon_count"]]
        ),
        "지도 클릭점이 여러 상권에 포함되면 포함 후보를 먼저 보여준 뒤 주변 후보를 보조로 보여줘야 사용자가 잘못 확정하지 않는다.",
    )

    validation_df = pd.DataFrame(rows)
    write_self_test_report(validation_df)
    return validation_df


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="위치/업종 입력을 규칙 엔진 코드로 확정")
    parser.add_argument("--lon", type=float, help="WGS84 경도")
    parser.add_argument("--lat", type=float, help="WGS84 위도")
    parser.add_argument("--industry", type=str, help="서비스_업종_코드 또는 업종명")
    parser.add_argument("--nearest-limit", type=int, default=5, help="polygon 밖일 때 반환할 최근접 후보 수")
    parser.add_argument("--self-test", action="store_true", help="대표 샘플 자체 검증 실행")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    data = load_resolver_data()

    output: dict[str, Any] = {"resolver_version": RESOLVER_VERSION}
    if args.self_test:
        validation_df = run_self_test(data)
        output["self_test"] = {
            "pass": int((validation_df["result"] == "PASS").sum()),
            "fail": int((validation_df["result"] == "FAIL").sum()),
        }
    if args.lon is not None and args.lat is not None:
        output["location_resolution"] = resolve_location(args.lon, args.lat, data, nearest_limit=args.nearest_limit)
    if args.industry:
        output["industry_resolution"] = resolve_industry(args.industry, data)

    print(json.dumps(output, ensure_ascii=False, indent=2))

    if args.self_test and output["self_test"]["fail"]:
        raise SystemExit(output["self_test"]["fail"])


if __name__ == "__main__":
    main()
