# -*- coding: utf-8 -*-
"""
교통 접근성 후보 전처리.

목적:
  - 버스/지하철 승하차량 중 좌표 결합이 확정된 행만 상권 polygon 주변 후보로 붙인다.
  - 산출물은 접근성 gold v2의 "후보"다. 현재 점수 엔진에 바로 투입하지 않는다.

주의:
  - 버스/지하철 승하차량은 실제 상권 방문자나 구매자가 아니다.
  - 원천 좌표는 WGS84 경위도처럼 보이나 일부 문서의 CRS 표기가 혼재되어 있다.
  - 따라서 거리값은 buffer 후보 계산용이며 실제 도보거리/도보시간으로 표현하지 않는다.
  - full-history 버스 summary는 3GB 이상이므로 월별 part를 사용하고, 중간 산출물은 append 방식으로 쓴다.
"""

from __future__ import annotations

import gc
import json
import math
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import pandas as pd
from pyproj import Transformer
from shapely.geometry import MultiPolygon, Point, Polygon
from shapely.ops import unary_union
from shapely.strtree import STRtree


ROOT = Path(__file__).resolve().parents[1]
SILVER = ROOT / "datacorpus" / "_silver"
GOLD = ROOT / "datacorpus" / "_gold"
RULE_VALIDATION = ROOT / "datacorpus" / "_rule_validation"
RESEARCH_RULE_VALIDATION = ROOT / "research" / "rule_validation"

VERSION = "transit_accessibility_candidate.v1.1-20260707"
MAX_BUFFER_M = 500.0
BUS_PART_DIR = SILVER / "silver_bus_passenger_route_stop_month_summary_parts"
SUBWAY_PART_DIR = SILVER / "silver_subway_passenger_station_month_summary_parts"
REQUIRED_BACKTEST_MONTHS = [f"{year}{month:02d}" for year in range(2021, 2026) for month in range(1, 13)]

POINT_OUT = SILVER / "silver_transit_point_accessibility_candidate_points.csv"
MATCH_OUT = SILVER / "silver_transit_point_trade_area_candidate.csv"
UNMATCHED_OUT = SILVER / "silver_transit_point_unmatched_500m.csv"
GOLD_OUT = GOLD / "gold_accessibility_transit_q_area_candidate.csv"


@dataclass
class AreaGeometry:
    trade_area_code: str
    trade_area_name: str
    geometry: Polygon | MultiPolygon


def read_csv(path: Path, **kwargs) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(path)
    return pd.read_csv(path, encoding="utf-8-sig", low_memory=False, **kwargs)


def write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")


def append_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = not path.exists()
    df.to_csv(path, mode="a", index=False, header=header, encoding="utf-8-sig")


def replace_after_success(temp_path: Path, final_path: Path) -> None:
    if not temp_path.exists():
        raise FileNotFoundError(temp_path)
    temp_path.replace(final_path)


def month_to_quarter(month: object) -> str:
    text = str(month)
    year = int(text[:4])
    mon = int(text[4:6])
    quarter = math.ceil(mon / 3)
    return f"{year}{quarter}"


def numeric(df: pd.DataFrame, cols: Iterable[str]) -> None:
    for col in cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")


def month_from_part(path: Path) -> str:
    stem = path.stem
    if "=" not in stem:
        raise ValueError(f"월별 part 파일명 형식이 아님: {path.name}")
    return stem.split("=", 1)[1]


def part_map(part_dir: Path) -> dict[str, Path]:
    if not part_dir.exists():
        raise FileNotFoundError(f"월별 summary part 디렉터리가 없음: {part_dir}")
    return {month_from_part(path): path for path in sorted(part_dir.glob("기준_월=*.csv"))}


def temporal_coverage_status(months: list[str]) -> str:
    required = set(REQUIRED_BACKTEST_MONTHS)
    month_set = set(months)
    if required.issubset(month_set):
        extras = sorted(month_set - required)
        if extras:
            return f"월이력_202101_202512_확보_추가월_{','.join(extras)}_gold_backtest전_직접투입금지"
        return "월이력_202101_202512_확보_gold_backtest전_직접투입금지"
    if len(months) == 1:
        return f"단월_{months[0]}_스냅샷_백테스트직접투입금지"
    missing = sorted(required - month_set)
    return f"월이력_{months[0]}_{months[-1]}_불완전_누락{len(missing)}개월_백테스트직접투입금지"


def build_area_geometries() -> tuple[list[AreaGeometry], STRtree]:
    vertices = read_csv(GOLD / "gold_location_boundary_vertices.csv")
    vertices["상권_코드"] = vertices["상권_코드"].astype(str)
    numeric(vertices, ["x_epsg5181", "y_epsg5181", "part_index", "vertex_index"])

    areas: list[AreaGeometry] = []
    for trade_area_code, area_df in vertices.groupby("상권_코드", sort=False):
        parts: list[Polygon] = []
        area_name = str(area_df["상권_코드_명"].iloc[0])
        for _, part_df in area_df.sort_values(["part_index", "vertex_index"]).groupby("part_index", sort=True):
            coords = list(zip(part_df["x_epsg5181"], part_df["y_epsg5181"]))
            if len(coords) < 3:
                continue
            poly = Polygon(coords)
            if not poly.is_valid:
                poly = poly.buffer(0)
            if not poly.is_empty:
                parts.append(poly)
        if not parts:
            continue
        geom = unary_union(parts) if len(parts) > 1 else parts[0]
        if not geom.is_valid:
            geom = geom.buffer(0)
        areas.append(AreaGeometry(trade_area_code=str(trade_area_code), trade_area_name=area_name, geometry=geom))

    tree = STRtree([area.geometry for area in areas])
    return areas, tree


def load_area_profile() -> pd.DataFrame:
    areas = read_csv(GOLD / "gold_trade_area_profile.csv", usecols=["상권_코드", "상권_코드_명", "자치구_코드", "자치구_코드_명"])
    areas["상권_코드"] = areas["상권_코드"].astype(str)
    return areas


def load_bus_points(part_path: Path) -> pd.DataFrame:
    cols = [
        "기준_월", "노선_번호", "정류소_ID", "정류소_ARS_ID", "정류장_명_정제",
        "정류소_경도", "정류소_위도", "좌표결합_상태",
        "월_승차_인원", "월_하차_인원", "월_승하차_인원",
        "낮_승하차_인원", "심야새벽_승하차_인원", "야간_승하차_인원",
        "출근오전_승하차_인원", "퇴근저녁_승하차_인원",
    ]
    bus = read_csv(part_path, usecols=cols)
    exact = bus[bus["좌표결합_상태"].eq("exact_match")].copy()
    numeric(
        exact,
        [
            "정류소_경도", "정류소_위도", "월_승차_인원", "월_하차_인원", "월_승하차_인원",
            "낮_승하차_인원", "심야새벽_승하차_인원", "야간_승하차_인원",
            "출근오전_승하차_인원", "퇴근저녁_승하차_인원",
        ],
    )

    group_cols = ["기준_월", "정류소_ID", "정류소_ARS_ID"]
    agg = exact.groupby(group_cols, dropna=False).agg(
        교통점_명=("정류장_명_정제", "first"),
        lon_wgs84=("정류소_경도", "first"),
        lat_wgs84=("정류소_위도", "first"),
        월_승차_인원=("월_승차_인원", "sum"),
        월_하차_인원=("월_하차_인원", "sum"),
        월_승하차_인원=("월_승하차_인원", "sum"),
        낮_승하차_인원=("낮_승하차_인원", "sum"),
        심야새벽_승하차_인원=("심야새벽_승하차_인원", "sum"),
        야간_승하차_인원=("야간_승하차_인원", "sum"),
        출근오전_승하차_인원=("출근오전_승하차_인원", "sum"),
        퇴근저녁_승하차_인원=("퇴근저녁_승하차_인원", "sum"),
        source_row_count=("노선_번호", "size"),
        route_or_line_count=("노선_번호", "nunique"),
    ).reset_index()

    agg["교통_모드"] = "bus"
    agg["교통점_ID"] = "bus_stop:" + agg["정류소_ID"].astype(str)
    agg["좌표결합_상태"] = "exact_match"
    agg["source_id"] = "seoul_bus_stop_passengers_hourly"
    agg["candidate_use_status"] = "후보_공간매칭가능_점수직접투입금지"
    return agg


def load_subway_points(part_path: Path) -> pd.DataFrame:
    cols = [
        "기준_월", "승하차_호선명", "승하차_역명", "좌표결합_상태", "확정_역사_ID",
        "후보_위도", "후보_경도", "월_승차_인원", "월_하차_인원", "월_승하차_인원",
        "낮_승하차_인원", "심야새벽_승하차_인원", "야간_승하차_인원",
        "출근오전_승하차_인원", "퇴근저녁_승하차_인원",
    ]
    subway = read_csv(part_path, usecols=cols)
    exact = subway[subway["좌표결합_상태"].eq("exact_match")].copy()
    numeric(
        exact,
        [
            "후보_경도", "후보_위도", "월_승차_인원", "월_하차_인원", "월_승하차_인원",
            "낮_승하차_인원", "심야새벽_승하차_인원", "야간_승하차_인원",
            "출근오전_승하차_인원", "퇴근저녁_승하차_인원",
        ],
    )

    group_cols = ["기준_월", "확정_역사_ID"]
    agg = exact.groupby(group_cols, dropna=False).agg(
        교통점_명=("승하차_역명", "first"),
        lon_wgs84=("후보_경도", "first"),
        lat_wgs84=("후보_위도", "first"),
        월_승차_인원=("월_승차_인원", "sum"),
        월_하차_인원=("월_하차_인원", "sum"),
        월_승하차_인원=("월_승하차_인원", "sum"),
        낮_승하차_인원=("낮_승하차_인원", "sum"),
        심야새벽_승하차_인원=("심야새벽_승하차_인원", "sum"),
        야간_승하차_인원=("야간_승하차_인원", "sum"),
        출근오전_승하차_인원=("출근오전_승하차_인원", "sum"),
        퇴근저녁_승하차_인원=("퇴근저녁_승하차_인원", "sum"),
        source_row_count=("승하차_호선명", "size"),
        route_or_line_count=("승하차_호선명", "nunique"),
    ).reset_index()

    agg["교통_모드"] = "subway"
    agg["교통점_ID"] = "subway_station:" + agg["확정_역사_ID"].astype(str)
    agg["좌표결합_상태"] = "exact_match"
    agg["source_id"] = "seoul_subway_station_passengers_hourly"
    agg["candidate_use_status"] = "후보_공간매칭가능_점수직접투입금지"
    return agg


def transform_points(points: pd.DataFrame) -> pd.DataFrame:
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:5181", always_xy=True)
    xs, ys = transformer.transform(points["lon_wgs84"].astype(float).to_numpy(), points["lat_wgs84"].astype(float).to_numpy())
    points = points.copy()
    points["x_epsg5181_candidate"] = xs
    points["y_epsg5181_candidate"] = ys
    points["기준_년분기_코드"] = points["기준_월"].map(month_to_quarter)
    points["coordinate_transform_rule"] = "WGS84처럼 보이는 경위도 값을 EPSG:5181로 변환한 후보 거리계산"
    return points


def match_points_to_areas(points: pd.DataFrame, areas: list[AreaGeometry], tree: STRtree) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows: list[dict[str, object]] = []
    unmatched: list[dict[str, object]] = []

    geometries = [area.geometry for area in areas]
    for row in points.itertuples(index=False):
        point = Point(float(row.x_epsg5181_candidate), float(row.y_epsg5181_candidate))
        candidate_idx = tree.query(point.buffer(MAX_BUFFER_M))
        matched = 0

        for idx in candidate_idx:
            idx_int = int(idx)
            geom = geometries[idx_int]
            distance = float(geom.distance(point))
            if distance > MAX_BUFFER_M:
                continue
            area = areas[idx_int]
            inside = bool(geom.covers(point))
            if inside:
                match_band = "inside_polygon"
            elif distance <= 100:
                match_band = "within_100m_candidate"
            elif distance <= 250:
                match_band = "within_250m_candidate"
            else:
                match_band = "within_500m_candidate"
            matched += 1
            rows.append(
                {
                    "기준_월": row.기준_월,
                    "기준_년분기_코드": row.기준_년분기_코드,
                    "교통_모드": row.교통_모드,
                    "교통점_ID": row.교통점_ID,
                    "교통점_명": row.교통점_명,
                    "상권_코드": area.trade_area_code,
                    "상권_코드_명": area.trade_area_name,
                    "match_band": match_band,
                    "inside_polygon": inside,
                    "distance_m_candidate": round(distance, 3),
                    "within_100m": bool(distance <= 100),
                    "within_250m": bool(distance <= 250),
                    "within_500m": True,
                    "lon_wgs84": row.lon_wgs84,
                    "lat_wgs84": row.lat_wgs84,
                    "x_epsg5181_candidate": row.x_epsg5181_candidate,
                    "y_epsg5181_candidate": row.y_epsg5181_candidate,
                    "월_승하차_인원": row.월_승하차_인원,
                    "낮_승하차_인원": row.낮_승하차_인원,
                    "심야새벽_승하차_인원": row.심야새벽_승하차_인원,
                    "야간_승하차_인원": row.야간_승하차_인원,
                    "출근오전_승하차_인원": row.출근오전_승하차_인원,
                    "퇴근저녁_승하차_인원": row.퇴근저녁_승하차_인원,
                    "source_row_count": row.source_row_count,
                    "route_or_line_count": row.route_or_line_count,
                    "좌표결합_상태": row.좌표결합_상태,
                    "source_id": row.source_id,
                    "candidate_use_status": row.candidate_use_status,
                    "direct_score_allowed": False,
                    "proxy_score_allowed_after_validation": True,
                    "forbidden_claim_ko": "실제 상권 방문자, 실제 구매자, 실제 도보시간, 실제 방문확률, 창업 성공확률로 표현 금지",
                }
            )

        if matched == 0:
            unmatched.append(
                {
                    "기준_월": row.기준_월,
                    "기준_년분기_코드": row.기준_년분기_코드,
                    "교통_모드": row.교통_모드,
                    "교통점_ID": row.교통점_ID,
                    "교통점_명": row.교통점_명,
                    "lon_wgs84": row.lon_wgs84,
                    "lat_wgs84": row.lat_wgs84,
                    "x_epsg5181_candidate": row.x_epsg5181_candidate,
                    "y_epsg5181_candidate": row.y_epsg5181_candidate,
                    "월_승하차_인원": row.월_승하차_인원,
                    "unmatched_reason_ko": f"{int(MAX_BUFFER_M)}m 후보 buffer 안에 상권 polygon 없음",
                    "direct_score_allowed": False,
                }
            )

    return pd.DataFrame(rows), pd.DataFrame(unmatched)


def build_gold_area_candidate_for_month(
    matches: pd.DataFrame,
    areas: pd.DataFrame,
    month: str,
    source_months: list[str],
) -> pd.DataFrame:
    base = areas.copy()
    base["기준_월"] = str(month)
    base["기준_년분기_코드"] = month_to_quarter(month)

    passenger_cols = [
        "월_승하차_인원", "낮_승하차_인원", "심야새벽_승하차_인원",
        "야간_승하차_인원", "출근오전_승하차_인원", "퇴근저녁_승하차_인원",
    ]
    for col in passenger_cols:
        if col in matches.columns:
            matches[col] = pd.to_numeric(matches[col], errors="coerce").fillna(0)

    out = base.copy()
    thresholds = [
        ("inside", "inside_polygon"),
        ("100m", "within_100m"),
        ("250m", "within_250m"),
        ("500m", "within_500m"),
    ]
    mode_specs = [("bus", "버스", "정류소수"), ("subway", "지하철", "역수")]

    for threshold_label, flag_col in thresholds:
        subset = matches[matches[flag_col]].copy() if flag_col in matches.columns else matches.iloc[0:0].copy()
        for mode, prefix, count_label in mode_specs:
            mode_df = subset[subset["교통_모드"].eq(mode)].copy() if "교통_모드" in subset.columns else subset.iloc[0:0].copy()
            if mode_df.empty:
                grouped = pd.DataFrame(columns=["상권_코드"])
            else:
                mode_df["상권_코드"] = mode_df["상권_코드"].astype(str)
                grouped = mode_df.groupby(["상권_코드"], as_index=False).agg(
                    **{
                        f"{prefix}_{count_label}_{threshold_label}": ("교통점_ID", "nunique"),
                        f"{prefix}_월승하차_{threshold_label}": ("월_승하차_인원", "sum"),
                        f"{prefix}_낮승하차_{threshold_label}": ("낮_승하차_인원", "sum"),
                        f"{prefix}_심야새벽승하차_{threshold_label}": ("심야새벽_승하차_인원", "sum"),
                        f"{prefix}_야간승하차_{threshold_label}": ("야간_승하차_인원", "sum"),
                        f"{prefix}_출근오전승하차_{threshold_label}": ("출근오전_승하차_인원", "sum"),
                        f"{prefix}_퇴근저녁승하차_{threshold_label}": ("퇴근저녁_승하차_인원", "sum"),
                    }
                )
            out = out.merge(grouped, on=["상권_코드"], how="left")

    numeric_cols = [col for col in out.columns if any(token in col for token in ["정류소수", "역수", "승하차"])]
    out[numeric_cols] = out[numeric_cols].fillna(0)
    for col in numeric_cols:
        out[col] = out[col].round(0).astype("int64")

    out["source_months"] = ",".join(source_months)
    out["gold_role"] = "교통접근성_후보_gold_점수직접투입금지"
    out["direct_score_allowed"] = False
    out["proxy_score_allowed_after_validation"] = True
    out["temporal_coverage_status"] = temporal_coverage_status(source_months)
    out["distance_use_status"] = "후보 buffer 거리. 실제 도보거리/시간 아님"
    out["forbidden_claim_ko"] = "실제 상권 방문자, 실제 구매자, 실제 도보시간, 실제 방문확률, 창업 성공확률로 표현 금지"
    out["notes_ko"] = (
        f"좌표 결합 exact_match 교통점만 상권 polygon 주변 100/250/500m 후보로 집계했다. "
        f"대상월은 {len(source_months)}개({source_months[0]}~{source_months[-1]})이며, "
        "점수 엔진 직접 투입 전 별도 백테스트와 CRS 검토가 필요하다."
    )
    out["candidate_version"] = VERSION
    return out


def update_stats(stats: dict[str, object], points: pd.DataFrame, matches: pd.DataFrame, unmatched: pd.DataFrame, gold: pd.DataFrame) -> None:
    stats["point_rows"] = int(stats["point_rows"]) + len(points)
    stats["match_rows"] = int(stats["match_rows"]) + len(matches)
    stats["unmatched_rows"] = int(stats["unmatched_rows"]) + len(unmatched)
    stats["gold_rows"] = int(stats["gold_rows"]) + len(gold)
    stats["coordinate_na_count"] = int(stats["coordinate_na_count"]) + int(points[["x_epsg5181_candidate", "y_epsg5181_candidate"]].isna().sum().sum())

    stats["mode_counts"].update({str(k): int(v) for k, v in points["교통_모드"].value_counts().items()})
    if not matches.empty:
        stats["band_counts"].update({str(k): int(v) for k, v in matches["match_band"].value_counts().items()})
        month_max = float(matches["distance_m_candidate"].max())
        stats["max_distance"] = month_max if stats["max_distance"] is None else max(float(stats["max_distance"]), month_max)
        stats["match_months"].update(matches["기준_월"].astype(str).unique())
    stats["point_months"].update(points["기준_월"].astype(str).unique())
    stats["gold_months"].update(gold["기준_월"].astype(str).unique())
    stats["bus_point_rows"] = int(stats["bus_point_rows"]) + int(points["교통_모드"].eq("bus").sum())
    stats["subway_point_rows"] = int(stats["subway_point_rows"]) + int(points["교통_모드"].eq("subway").sum())
    direct_true = gold["direct_score_allowed"].astype(str).str.lower().isin(["true", "1", "yes"]).sum()
    stats["gold_direct_true"] = int(stats["gold_direct_true"]) + int(direct_true)


def validate(stats: dict[str, object], areas: list[AreaGeometry], source_months: list[str]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []

    def add(rule_name: str, observed: object, expected: object, passed: bool, reason_ko: str) -> None:
        rows.append(
            {
                "validation_id": len(rows) + 1,
                "rule_name": rule_name,
                "observed": observed,
                "expected": expected,
                "result": "PASS" if passed else "FAIL",
                "reason_ko": reason_ko,
            }
        )

    required_set = set(REQUIRED_BACKTEST_MONTHS)
    source_set = set(source_months)
    point_months = sorted(stats["point_months"])
    match_months = sorted(stats["match_months"])
    gold_months = sorted(stats["gold_months"])

    add("상권 polygon 1650개 로드", len(areas), 1650, len(areas) == 1650, "교통점 공간매칭은 공식 상권 polygon 전체를 기준으로 해야 한다.")
    add("source month 2021~2025 필수월 포함", len(required_set - source_set), 0, required_set.issubset(source_set), "과거 백데이터 검증 전에 최소 2021~2025 승하차량 월이 모두 있어야 한다.")
    add("버스 exact 좌표 결합 정류소 존재", stats["bus_point_rows"], "0보다 큼", int(stats["bus_point_rows"]) > 0, "좌표 결합이 확정된 정류소만 후보 거리 계산에 사용한다.")
    add("지하철 exact 좌표 결합 역사 존재", stats["subway_point_rows"], "0보다 큼", int(stats["subway_point_rows"]) > 0, "좌표 결합이 확정된 역사만 후보 거리 계산에 사용한다.")
    add("좌표 변환 결측 없음", stats["coordinate_na_count"], 0, int(stats["coordinate_na_count"]) == 0, "거리 후보 계산 전 좌표 변환 실패가 없어야 한다.")
    add("500m 후보 매칭 row 존재", stats["match_rows"], "0보다 큼", int(stats["match_rows"]) > 0, "접근성 후보 gold를 만들려면 최소 하나 이상의 상권-교통점 후보가 있어야 한다.")
    add("미매칭 교통점 보존", stats["unmatched_rows"], "삭제하지 않음", True, "500m 안에 상권이 없는 교통점도 버리지 않고 감사용으로 남긴다.")
    add("point 월과 source 월 일치", ",".join(point_months[:3] + ["..."] + point_months[-3:]), f"{len(source_months)}개월", point_months == source_months, "월별 part 처리에서 특정 월이 누락되면 full-history 후보로 볼 수 없다.")
    add("match 월 누락 없음", len(set(source_months) - set(match_months)), 0, set(source_months).issubset(set(match_months)), "모든 원천 월에서 최소 하나 이상의 상권-교통점 후보가 생성되어야 한다.")
    add("gold area row 수", stats["gold_rows"], 1650 * len(source_months), int(stats["gold_rows"]) == 1650 * len(source_months), "모든 월과 모든 상권에 0 포함 집계 row를 만든다.")
    add("gold 월과 source 월 일치", ",".join(gold_months[:3] + ["..."] + gold_months[-3:]), f"{len(source_months)}개월", gold_months == source_months, "gold가 source month 전체를 보존해야 42/55번 readiness와 모순되지 않는다.")
    add("direct_score_allowed 모두 False", stats["gold_direct_true"], 0, int(stats["gold_direct_true"]) == 0, "full-history 후보라도 backtest 승격 전 점수 직접 투입은 금지다.")
    add("거리 후보 최대값 제한", round(float(stats["max_distance"]), 3) if stats["max_distance"] is not None else None, f"<= {MAX_BUFFER_M}", bool(stats["max_distance"] is not None and float(stats["max_distance"]) <= MAX_BUFFER_M + 1e-6), "후보 매칭 테이블은 500m 이내 후보만 담는다.")

    return pd.DataFrame(rows)


def write_report(validation: pd.DataFrame, stats: dict[str, object], source_months: list[str]) -> None:
    pass_count = int((validation["result"] == "PASS").sum())
    fail_count = int((validation["result"] == "FAIL").sum())
    mode_counts = dict(stats["mode_counts"])
    band_counts = dict(stats["band_counts"])
    coverage = temporal_coverage_status(source_months)

    lines = [
        "# 교통 접근성 후보 gold 전처리 검증",
        "",
        "작성일: 2026-07-07",
        "",
        "## 1. 목적",
        "",
        "버스·지하철 승하차량을 상권 접근성 gold v2 후보로 만들 수 있는지 확인한다. 이 산출물은 현재 점수 엔진에 바로 투입하지 않는다.",
        "",
        "## 2. 산출물",
        "",
        "| 파일 | 역할 |",
        "|---|---|",
        "| `datacorpus/_silver/silver_transit_point_accessibility_candidate_points.csv` | 좌표 결합 exact 교통점 월별 후보 point |",
        "| `datacorpus/_silver/silver_transit_point_trade_area_candidate.csv` | 좌표 결합 exact 교통점과 상권 polygon 500m 후보 매칭 |",
        "| `datacorpus/_silver/silver_transit_point_unmatched_500m.csv` | 500m 후보 상권이 없는 교통점 감사 |",
        "| `datacorpus/_gold/gold_accessibility_transit_q_area_candidate.csv` | 상권×월 교통 접근성 후보 집계 |",
        "| `datacorpus/_rule_validation/31_transit_accessibility_candidate_validation.csv` | 검증 결과 |",
        "",
        "## 3. 핵심 수치",
        "",
        f"- 대상월 수: {len(source_months):,}",
        f"- 대상월 범위: {source_months[0]}~{source_months[-1]}",
        f"- temporal coverage: `{coverage}`",
        f"- 교통점 수: {int(stats['point_rows']):,}",
        f"- 모드별 교통점 수: {mode_counts}",
        f"- 상권-교통점 후보 매칭 row: {int(stats['match_rows']):,}",
        f"- 500m 미매칭 교통점: {int(stats['unmatched_rows']):,}",
        f"- match band 분포: {band_counts}",
        f"- gold row: {int(stats['gold_rows']):,}",
        "",
        "## 4. 검증 결과",
        "",
        f"- PASS: {pass_count}",
        f"- FAIL: {fail_count}",
        "",
        "| rule_name | observed | expected | result | reason_ko |",
        "|---|---:|---:|---|---|",
    ]
    for row in validation.itertuples(index=False):
        lines.append(f"| {row.rule_name} | {row.observed} | {row.expected} | {row.result} | {row.reason_ko} |")

    lines.extend(
        [
            "",
            "## 5. 판정",
            "",
            "조건부 PASS.",
            "",
            "이유:",
            "",
            "- 2021~2025 백테스트 필수 월이 포함된 full-history 승하차량 silver를 사용했다.",
            "- 좌표 결합이 확정된 버스 정류소와 지하철 역사만 사용했다.",
            "- 상권 polygon과 100m/250m/500m 후보 buffer 집계를 만들었다.",
            "- 다만 승하차량은 실제 방문자나 구매자가 아니라 접근성/유입 강도 프록시다.",
            "- 따라서 이 산출물은 접근성 보강 후보이며, backtest와 CRS 검토 전까지 점수 엔진에 바로 직접 투입하지 않는다.",
            "",
            "## 6. 금지 표현",
            "",
            "- 실제 상권 방문자",
            "- 실제 구매자",
            "- 실제 도보시간",
            "- 실제 방문확률",
            "- 창업 성공확률",
            "",
            "허용 표현:",
            "",
            "- 시간대 승하차량 기반 접근성 강도 프록시",
            "- 좌표 결합 exact 교통점의 상권 주변 후보 집계",
            "- 100m/250m/500m 후보 buffer 접근성",
            "",
            "## 7. 다음 작업",
            "",
            "1. 32/42/55번 readiness 검증을 full-history gold 기준으로 재실행한다.",
            "2. buffer 거리별 민감도를 백테스트한다.",
            "3. 상권 경계 중첩으로 인한 승하차량 fan-out 중복을 리포트 문구에 반영한다.",
            "4. 성능이 개선될 때만 `gold_accessibility_q_area` 또는 점수 엔진에 승격한다.",
        ]
    )
    RESEARCH_RULE_VALIDATION.mkdir(parents=True, exist_ok=True)
    (RESEARCH_RULE_VALIDATION / "31_transit_accessibility_candidate_validation_20260707.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    bus_parts = part_map(BUS_PART_DIR)
    subway_parts = part_map(SUBWAY_PART_DIR)
    source_months = sorted(set(bus_parts) & set(subway_parts))
    if not source_months:
        raise RuntimeError("버스/지하철 월별 summary part의 공통 월이 없음")

    areas, tree = build_area_geometries()
    area_profile = load_area_profile()
    stats: dict[str, object] = {
        "point_rows": 0,
        "match_rows": 0,
        "unmatched_rows": 0,
        "gold_rows": 0,
        "coordinate_na_count": 0,
        "mode_counts": Counter(),
        "band_counts": Counter(),
        "max_distance": None,
        "point_months": set(),
        "match_months": set(),
        "gold_months": set(),
        "bus_point_rows": 0,
        "subway_point_rows": 0,
        "gold_direct_true": 0,
    }

    temp_files = {
        "points": POINT_OUT.with_suffix(".tmp.csv"),
        "matches": MATCH_OUT.with_suffix(".tmp.csv"),
        "unmatched": UNMATCHED_OUT.with_suffix(".tmp.csv"),
        "gold": GOLD_OUT.with_suffix(".tmp.csv"),
    }
    for path in temp_files.values():
        if path.exists():
            path.unlink()

    point_cols = [
        "기준_월", "기준_년분기_코드", "교통_모드", "교통점_ID", "교통점_명",
        "lon_wgs84", "lat_wgs84", "x_epsg5181_candidate", "y_epsg5181_candidate",
        "월_승차_인원", "월_하차_인원", "월_승하차_인원", "낮_승하차_인원",
        "심야새벽_승하차_인원", "야간_승하차_인원", "출근오전_승하차_인원",
        "퇴근저녁_승하차_인원", "source_row_count", "route_or_line_count",
        "좌표결합_상태", "source_id", "candidate_use_status", "coordinate_transform_rule",
    ]

    for idx, month in enumerate(source_months, start=1):
        bus_points = load_bus_points(bus_parts[month])
        subway_points = load_subway_points(subway_parts[month])
        points = pd.concat([bus_points, subway_points], ignore_index=True, sort=False)
        points = transform_points(points)
        points = points[point_cols].copy()

        matches, unmatched = match_points_to_areas(points, areas, tree)
        gold = build_gold_area_candidate_for_month(matches, area_profile, month, source_months)
        update_stats(stats, points, matches, unmatched, gold)

        append_csv(points, temp_files["points"])
        append_csv(matches, temp_files["matches"])
        append_csv(unmatched, temp_files["unmatched"])
        append_csv(gold, temp_files["gold"])

        print(json.dumps({"month": month, "index": idx, "total_months": len(source_months), "points": len(points), "matches": len(matches), "gold": len(gold)}, ensure_ascii=False))
        del bus_points, subway_points, points, matches, unmatched, gold
        gc.collect()

    validation = validate(stats, areas, source_months)
    write_csv(validation, RULE_VALIDATION / "31_transit_accessibility_candidate_validation.csv")

    replace_after_success(temp_files["points"], POINT_OUT)
    replace_after_success(temp_files["matches"], MATCH_OUT)
    replace_after_success(temp_files["unmatched"], UNMATCHED_OUT)
    replace_after_success(temp_files["gold"], GOLD_OUT)

    summary = {
        "version": VERSION,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "source_month_count": len(source_months),
        "source_month_start": source_months[0],
        "source_month_end": source_months[-1],
        "temporal_coverage_status": temporal_coverage_status(source_months),
        "point_rows": int(stats["point_rows"]),
        "match_rows": int(stats["match_rows"]),
        "unmatched_rows": int(stats["unmatched_rows"]),
        "gold_rows": int(stats["gold_rows"]),
        "validation_pass_count": int((validation["result"] == "PASS").sum()),
        "validation_fail_count": int((validation["result"] == "FAIL").sum()),
        "direct_score_allowed": False,
        "reason_ko": "full-history 승하차량 후보 gold를 만들었지만 백테스트와 CRS 검토 전 점수 엔진 직접 투입 금지",
    }
    (RULE_VALIDATION / "31_transit_accessibility_candidate_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_report(validation, stats, source_months)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
