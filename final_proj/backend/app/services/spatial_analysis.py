from __future__ import annotations

import csv
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Iterable

from pyproj import Transformer
from shapely import make_valid
from shapely.geometry import GeometryCollection, MultiPolygon, Point, Polygon, box, mapping, shape
from shapely.ops import transform, unary_union
from shapely.prepared import prep
from shapely.strtree import STRtree
from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from app.core.settings import BOUNDARY_VERTICES_PATH
from app.schemas.spatial import SpatialZoneAnalysisRequest, ZoneShapeInput


CALCULATION_VERSION = "spatial-zone.v1-epsg5181"
SOURCE_CRS = "EPSG:4326"
CALCULATION_CRS = "EPSG:5181"
MIN_ZONE_AREA_M2 = 25.0
MAX_ZONE_AREA_M2 = 25_000_000.0
MAX_CIRCLE_RADIUS_M = 5_000.0

TO_5181 = Transformer.from_crs(4326, 5181, always_xy=True)
TO_WGS84 = Transformer.from_crs(5181, 4326, always_xy=True)

METHOD_LABELS = {
    "direct_aggregation": "직접 집계",
    "official_area_value": "공식 상권 지표",
    "area_ratio_estimate": "면적 비례 추정",
    "regional_reference": "지역 참고지표",
}


@dataclass(frozen=True)
class BoundaryRecord:
    area_code: str
    area_name: str
    geometry_5181: Polygon | MultiPolygon

    @property
    def area_m2(self) -> float:
        return float(self.geometry_5181.area)


@dataclass(frozen=True)
class BoundaryHit:
    record: BoundaryRecord
    intersection: Polygon | MultiPolygon | GeometryCollection

    @property
    def intersection_area_m2(self) -> float:
        return float(self.intersection.area)


def _polygonal_only(geometry):
    if isinstance(geometry, (Polygon, MultiPolygon)):
        return geometry
    if isinstance(geometry, GeometryCollection):
        polygons = [part for part in geometry.geoms if isinstance(part, (Polygon, MultiPolygon))]
        if polygons:
            merged = unary_union(polygons)
            if isinstance(merged, (Polygon, MultiPolygon)):
                return merged
    raise ValueError("geometry must resolve to Polygon or MultiPolygon")


def _close_ring(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if points and points[0] != points[-1]:
        return [*points, points[0]]
    return points


def _finalize_boundary(
    area_code: str,
    area_name: str,
    parts: dict[int, list[tuple[float, float]]],
) -> BoundaryRecord:
    polygons = []
    for part_index in sorted(parts):
        coordinates = _close_ring(parts[part_index])
        if len(coordinates) < 4:
            continue
        polygon = Polygon(coordinates)
        if not polygon.is_valid:
            polygon = _polygonal_only(make_valid(polygon))
        polygons.append(polygon)
    if not polygons:
        raise ValueError(f"boundary {area_code} has no valid polygon parts")
    geometry = _polygonal_only(unary_union(polygons))
    return BoundaryRecord(area_code=area_code, area_name=area_name, geometry_5181=geometry)


class BoundaryCatalog:
    def __init__(self, source_path: Path = BOUNDARY_VERTICES_PATH):
        self.source_path = Path(source_path)
        self.records = self._load_records()
        self.by_code = {record.area_code: record for record in self.records}
        self._geometries = [record.geometry_5181 for record in self.records]
        self._tree = STRtree(self._geometries)
        self._geometry_index = {id(geometry): index for index, geometry in enumerate(self._geometries)}

    def _load_records(self) -> list[BoundaryRecord]:
        if not self.source_path.exists():
            raise FileNotFoundError(f"boundary source not found: {self.source_path}")

        records: list[BoundaryRecord] = []
        current_code = ""
        current_name = ""
        current_parts: dict[int, list[tuple[float, float]]] = defaultdict(list)
        seen_codes: set[str] = set()

        with self.source_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            required = {"상권_코드", "상권_코드_명", "part_index", "x_epsg5181", "y_epsg5181"}
            if not required.issubset(reader.fieldnames or []):
                raise ValueError(f"boundary source is missing columns: {sorted(required)}")

            for row in reader:
                area_code = str(row["상권_코드"]).strip()
                if not area_code:
                    continue
                if current_code and area_code != current_code:
                    records.append(_finalize_boundary(current_code, current_name, current_parts))
                    seen_codes.add(current_code)
                    current_parts = defaultdict(list)
                if area_code in seen_codes:
                    raise ValueError("boundary vertices must be grouped by area code")
                current_code = area_code
                current_name = str(row["상권_코드_명"]).strip()
                current_parts[int(row["part_index"] or 0)].append(
                    (float(row["x_epsg5181"]), float(row["y_epsg5181"]))
                )

        if current_code:
            records.append(_finalize_boundary(current_code, current_name, current_parts))
        if not records:
            raise ValueError("boundary source contains no records")
        return records

    def get(self, area_code: str) -> BoundaryRecord | None:
        return self.by_code.get(str(area_code))

    def _query_indices(self, geometry) -> Iterable[int]:
        for hit in self._tree.query(geometry):
            try:
                yield int(hit)
            except (TypeError, ValueError):
                index = self._geometry_index.get(id(hit))
                if index is not None:
                    yield index

    def intersecting(self, geometry_5181) -> list[BoundaryHit]:
        hits: list[BoundaryHit] = []
        for index in self._query_indices(geometry_5181):
            record = self.records[index]
            if not record.geometry_5181.intersects(geometry_5181):
                continue
            intersection = record.geometry_5181.intersection(geometry_5181)
            if not intersection.is_empty and intersection.area > 0:
                hits.append(BoundaryHit(record=record, intersection=intersection))
        return hits

    def in_wgs84_bounds(self, west: float, south: float, east: float, north: float) -> list[BoundaryRecord]:
        bounds_5181 = transform(TO_5181.transform, box(west, south, east, north))
        records = [self.records[index] for index in self._query_indices(bounds_5181)]
        return [record for record in records if record.geometry_5181.intersects(bounds_5181)]


_BOUNDARY_CATALOG_LOCK = Lock()
_BOUNDARY_CATALOG_CACHE: tuple[tuple[str, int, int], BoundaryCatalog] | None = None


def _boundary_source_signature(source_path: Path = BOUNDARY_VERTICES_PATH) -> tuple[str, int, int]:
    resolved = Path(source_path).resolve()
    stat = resolved.stat()
    return str(resolved), int(stat.st_mtime_ns), int(stat.st_size)


def clear_boundary_catalog_cache() -> None:
    global _BOUNDARY_CATALOG_CACHE
    with _BOUNDARY_CATALOG_LOCK:
        _BOUNDARY_CATALOG_CACHE = None


def get_boundary_catalog() -> BoundaryCatalog:
    """Reuse the catalog until the published boundary artifact changes."""
    global _BOUNDARY_CATALOG_CACHE
    signature = _boundary_source_signature()
    cached = _BOUNDARY_CATALOG_CACHE
    if cached is not None and cached[0] == signature:
        return cached[1]

    with _BOUNDARY_CATALOG_LOCK:
        signature = _boundary_source_signature()
        cached = _BOUNDARY_CATALOG_CACHE
        if cached is not None and cached[0] == signature:
            return cached[1]

        for _ in range(5):
            load_signature = _boundary_source_signature()
            catalog = BoundaryCatalog()
            published_signature = _boundary_source_signature()
            if published_signature == load_signature:
                _BOUNDARY_CATALOG_CACHE = (published_signature, catalog)
                return catalog
        raise RuntimeError("boundary artifact changed repeatedly while loading")


def _wgs84_geometry(geometry_5181, simplify_m: float = 0.0):
    display_geometry = geometry_5181
    if simplify_m > 0:
        display_geometry = display_geometry.simplify(simplify_m, preserve_topology=True)
    return transform(TO_WGS84.transform, display_geometry)


def boundary_feature(record: BoundaryRecord, simplify_m: float = 1.5) -> dict[str, Any]:
    return {
        "type": "Feature",
        "geometry": mapping(_wgs84_geometry(record.geometry_5181, simplify_m)),
        "properties": {
            "area_code": record.area_code,
            "area_name": record.area_name,
            "area_m2": round(record.area_m2, 2),
            "source_crs": CALCULATION_CRS,
            "display_crs": SOURCE_CRS,
            "calculation_version": CALCULATION_VERSION,
        },
    }


def _validate_wgs84_bounds(geometry) -> None:
    west, south, east, north = geometry.bounds
    if west < 124.0 or east > 132.0 or south < 33.0 or north > 39.5:
        raise ValueError("coordinates must be WGS84 longitude/latitude values in Korea")


def build_zone_geometry(shape_input: ZoneShapeInput):
    if shape_input.kind == "circle":
        lon, lat = shape_input.center or (0.0, 0.0)
        if not (124.0 <= lon <= 132.0 and 33.0 <= lat <= 39.5):
            raise ValueError("circle center must use WGS84 longitude/latitude in Korea")
        radius_m = float(shape_input.radius_m or 0.0)
        if radius_m > MAX_CIRCLE_RADIUS_M:
            raise ValueError(f"circle radius must be {MAX_CIRCLE_RADIUS_M:.0f}m or less")
        center_x, center_y = TO_5181.transform(lon, lat)
        geometry_5181 = Point(center_x, center_y).buffer(radius_m, quad_segs=48)
    else:
        geometry_data = shape_input.geometry.model_dump() if shape_input.geometry else {}
        geometry_wgs84 = shape(geometry_data)
        _validate_wgs84_bounds(geometry_wgs84)
        if geometry_wgs84.is_empty:
            raise ValueError("geometry is empty")
        if not geometry_wgs84.is_valid:
            geometry_wgs84 = make_valid(geometry_wgs84)
        geometry_wgs84 = _polygonal_only(geometry_wgs84)
        geometry_5181 = _polygonal_only(transform(TO_5181.transform, geometry_wgs84))

    area_m2 = float(geometry_5181.area)
    if area_m2 < MIN_ZONE_AREA_M2:
        raise ValueError(f"analysis zone must be at least {MIN_ZONE_AREA_M2:.0f}㎡")
    if area_m2 > MAX_ZONE_AREA_M2:
        raise ValueError(f"analysis zone must be {MAX_ZONE_AREA_M2 / 1_000_000:.0f}㎢ or less")
    return geometry_5181


def _table_exists(db: Session, table_name: str) -> bool:
    row = db.execute(
        text("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = :name"),
        {"name": table_name},
    ).first()
    return row is not None


def _metric(
    *,
    key: str,
    label: str,
    value: float | int | None,
    unit: str,
    method: str,
    confidence: str,
    source: str,
    period: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    if isinstance(value, float):
        value = round(value, 2)
    return {
        "key": key,
        "label": label,
        "value": value,
        "unit": unit,
        "method": method,
        "method_label": METHOD_LABELS[method],
        "confidence": confidence,
        "source": source,
        "period": period,
        "note": note,
    }


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class SpatialAnalysisService:
    def __init__(self, db: Session, catalog: BoundaryCatalog | None = None):
        self.db = db
        self.catalog = catalog or get_boundary_catalog()

    def area_boundary(self, area_code: str, simplify_m: float = 1.5) -> dict[str, Any] | None:
        record = self.catalog.get(area_code)
        return boundary_feature(record, simplify_m) if record else None

    def area_boundaries_in_view(
        self,
        *,
        west: float,
        south: float,
        east: float,
        north: float,
        simplify_m: float = 3.0,
        limit: int = 200,
    ) -> dict[str, Any]:
        if west >= east or south >= north:
            raise ValueError("invalid WGS84 bounding box")
        if not (124.0 <= west <= 132.0 and 124.0 <= east <= 132.0):
            raise ValueError("longitude is outside the supported range")
        if not (33.0 <= south <= 39.5 and 33.0 <= north <= 39.5):
            raise ValueError("latitude is outside the supported range")
        records = self.catalog.in_wgs84_bounds(west, south, east, north)[:limit]
        return {
            "type": "FeatureCollection",
            "features": [boundary_feature(record, simplify_m) for record in records],
            "calculation_version": CALCULATION_VERSION,
            "source_crs": CALCULATION_CRS,
            "display_crs": SOURCE_CRS,
        }

    def _point_rows(self, geometry_wgs84, *, table: str, rtree: str, industry_query: str | None = None):
        west, south, east, north = geometry_wgs84.bounds
        params: dict[str, Any] = {
            "west": west,
            "south": south,
            "east": east,
            "north": north,
        }
        industry_clause = ""
        if table == "spatial_store_point" and industry_query:
            params["industry_query"] = f"%{_escape_like(industry_query.strip())}%"
            industry_clause = """
                AND (
                    p.major_code = :industry_exact
                    OR p.middle_code = :industry_exact
                    OR p.minor_code = :industry_exact
                    OR p.major_name LIKE :industry_query ESCAPE '\\'
                    OR p.middle_name LIKE :industry_query ESCAPE '\\'
                    OR p.minor_name LIKE :industry_query ESCAPE '\\'
                )
            """
            params["industry_exact"] = industry_query.strip()

        statement = text(
            f"""
            SELECT p.*
            FROM {rtree} r
            JOIN {table} p ON p.id = r.id
            WHERE r.max_lon >= :west
              AND r.min_lon <= :east
              AND r.max_lat >= :south
              AND r.min_lat <= :north
              {industry_clause}
            """
        )
        return self.db.execute(statement, params).mappings().all()

    def _direct_store_metrics(self, geometry_wgs84, industry_query: str | None):
        ready = _table_exists(self.db, "spatial_store_point") and _table_exists(
            self.db, "spatial_store_point_rtree"
        )
        if not ready:
            return False, [], []

        rows = self._point_rows(
            geometry_wgs84,
            table="spatial_store_point",
            rtree="spatial_store_point_rtree",
            industry_query=industry_query,
        )
        prepared = prep(geometry_wgs84)
        included = [row for row in rows if prepared.covers(Point(float(row["lon"]), float(row["lat"])))]
        categories = Counter(
            (
                str(row.get("minor_code") or ""),
                str(row.get("minor_name") or row.get("middle_name") or row.get("major_name") or "미분류"),
            )
            for row in included
        )
        top_categories = [
            {"code": code or None, "name": name, "count": count}
            for (code, name), count in categories.most_common(5)
        ]
        metrics = [
            _metric(
                key="store_count",
                label="영역 내 점포",
                value=len(included),
                unit="개",
                method="direct_aggregation",
                confidence="high",
                source="소상공인시장진흥공단 상가업소 좌표",
                period="202603",
                note="점포 좌표가 분석 영역 내부에 포함되는지 직접 판정했습니다.",
            )
        ]
        return True, metrics, top_categories

    def _direct_transit_metrics(self, geometry_wgs84):
        ready = _table_exists(self.db, "spatial_transit_point") and _table_exists(
            self.db, "spatial_transit_point_rtree"
        )
        if not ready:
            return False, []
        rows = self._point_rows(
            geometry_wgs84,
            table="spatial_transit_point",
            rtree="spatial_transit_point_rtree",
        )
        prepared = prep(geometry_wgs84)
        included = [row for row in rows if prepared.covers(Point(float(row["lon"]), float(row["lat"])))]
        mode_counts = Counter(str(row["mode"]) for row in included)
        metrics = [
            _metric(
                key="bus_stop_count",
                label="버스정류소",
                value=mode_counts.get("bus", 0),
                unit="개",
                method="direct_aggregation",
                confidence="high",
                source="서울열린데이터광장 정류소 위치정보",
                period="2026-07-03",
            ),
            _metric(
                key="subway_station_count",
                label="지하철 역사",
                value=mode_counts.get("subway", 0),
                unit="개",
                method="direct_aggregation",
                confidence="high",
                source="서울열린데이터광장 역사 좌표정보",
                period="2026-07-03",
                note="환승역은 노선별 역사 ID 기준으로 집계될 수 있습니다.",
            ),
        ]
        return True, metrics

    def _reference_rows(self, area_codes: list[str]) -> dict[str, dict[str, Any]]:
        if not area_codes:
            return {}
        statement = text(
            """
            SELECT
                ca.area_code,
                (
                    SELECT (MAX(rs.axis_demand) + MAX(rs.axis_accessibility)) / 2.0
                    FROM rule_location_score rs
                    WHERE rs.area_code = ca.area_code
                      AND rs.quarter = (SELECT MAX(quarter) FROM rule_location_score)
                    HAVING MAX(rs.axis_demand) IS NOT NULL
                       AND MAX(rs.axis_accessibility) IS NOT NULL
                ) AS score,
                (
                    SELECT SUM(df.floating_population)
                    FROM district_floating df
                    WHERE df.area_code = ca.area_code
                      AND df.timestamp = (SELECT MAX(timestamp) FROM district_floating)
                ) AS floating_population,
                (
                    SELECT SUM(ds.sales_amount)
                    FROM district_sales ds
                    WHERE ds.area_code = ca.area_code
                      AND ds.timestamp = (SELECT MAX(timestamp) FROM district_sales)
                ) AS sales_amount,
                (
                    SELECT AVG(sp.sale_price_proxy_manwon_per_m2)
                    FROM area_sale_price_proxy sp
                    WHERE sp.area_code = ca.area_code
                      AND sp.period = (
                          SELECT MAX(period) FROM area_sale_price_proxy WHERE period <= (
                              SELECT MAX(quarter) FROM rule_location_score
                          )
                      )
                ) AS sale_price_proxy_manwon_per_m2,
                (
                    SELECT SUM(dp.resident_population + dp.worker_population)
                    FROM district_population dp
                    WHERE dp.area_code = ca.area_code
                      AND dp.timestamp = (SELECT MAX(timestamp) FROM district_population)
                ) AS resident_worker_population
            FROM commercial_area ca
            WHERE ca.area_code IN :area_codes
            """
        ).bindparams(bindparam("area_codes", expanding=True))
        rows = self.db.execute(statement, {"area_codes": area_codes}).mappings().all()
        return {str(row["area_code"]): dict(row) for row in rows}

    @staticmethod
    def _weighted_reference(
        rows: dict[str, dict[str, Any]],
        hits: list[BoundaryHit],
        key: str,
    ) -> float | None:
        values: list[tuple[float, float]] = []
        for hit in hits:
            value = rows.get(hit.record.area_code, {}).get(key)
            if value is not None and hit.intersection_area_m2 > 0:
                values.append((float(value), hit.intersection_area_m2))
        weight_sum = sum(weight for _, weight in values)
        if weight_sum <= 0:
            return None
        return sum(value * weight for value, weight in values) / weight_sum

    @staticmethod
    def _area_ratio_estimate(
        rows: dict[str, dict[str, Any]],
        hits: list[BoundaryHit],
        key: str,
    ) -> float | None:
        estimates = []
        for hit in hits:
            value = rows.get(hit.record.area_code, {}).get(key)
            if value is None or hit.record.area_m2 <= 0:
                continue
            estimates.append(float(value) * min(hit.intersection_area_m2 / hit.record.area_m2, 1.0))
        return sum(estimates) if estimates else None

    def _area_based_metrics(self, hits: list[BoundaryHit], zone_area_m2: float):
        rows = self._reference_rows([hit.record.area_code for hit in hits])
        if not rows:
            return []

        intersection_sum = sum(hit.intersection_area_m2 for hit in hits)
        union_area = float(unary_union([hit.intersection for hit in hits]).area) if hits else 0.0
        overlap_factor = intersection_sum / union_area if union_area else 1.0
        estimate_confidence = "medium" if overlap_factor <= 1.15 else "reference"
        estimate_note = "공식 상권별 값을 교차면적 비율로 환산한 추정치입니다."
        if overlap_factor > 1.15:
            estimate_note += " 공식 상권 경계가 일부 중첩되어 참고용으로 해석해야 합니다."

        metrics = []
        for key, label, unit in (
            ("floating_population", "추정 유동인구", "명"),
            ("sales_amount", "추정 분기 매출", "원"),
            ("resident_worker_population", "추정 상주·직장인구", "명"),
        ):
            value = self._area_ratio_estimate(rows, hits, key)
            if value is not None:
                metrics.append(
                    _metric(
                        key=key,
                        label=label,
                        value=value,
                        unit=unit,
                        method="area_ratio_estimate",
                        confidence=estimate_confidence,
                        source="상권코드 단위 정형지표와 공식 상권 경계",
                        period="최신 적재 분기",
                        note=estimate_note,
                    )
                )

        for key, label, unit in (
            ("score", "교차 상권 수요·접근성 맥락점수", "점"),
            ("sale_price_proxy_manwon_per_m2", "교차 상권 RTMS 매매가 프록시", "만원/㎡"),
        ):
            value = self._weighted_reference(rows, hits, key)
            if value is not None:
                metrics.append(
                    _metric(
                        key=f"reference_{key}",
                        label=label,
                        value=value,
                        unit=unit,
                        method="regional_reference",
                        confidence="reference",
                        source="교차 공식 상권 정형지표",
                        period="최신 적재 분기",
                        note="사용자 지정 영역의 직접 측정값이 아니라 교차 상권의 면적가중 참고값입니다.",
                    )
                )
        return metrics

    def _official_area_metrics(self, hits: list[BoundaryHit]):
        rows = self._reference_rows([hit.record.area_code for hit in hits])
        if not rows:
            return []

        confidence = "high" if len(hits) == 1 else "medium"
        note = "선택한 공식 상권코드의 최신 적재 값을 사용했습니다."
        if len(hits) > 1:
            note += " 복수 상권 합산 시 서로 겹치는 공식 경계의 지표가 중복될 수 있습니다."

        metrics = []
        for key, label, unit in (
            ("floating_population", "공식 상권 유동인구", "명"),
            ("sales_amount", "공식 상권 분기 매출", "원"),
            ("resident_worker_population", "공식 상권 상주·직장인구", "명"),
        ):
            values = [rows.get(hit.record.area_code, {}).get(key) for hit in hits]
            values = [float(value) for value in values if value is not None]
            if values:
                metrics.append(
                    _metric(
                        key=key,
                        label=label,
                        value=sum(values),
                        unit=unit,
                        method="official_area_value",
                        confidence=confidence,
                        source="상권코드 단위 정형지표",
                        period="최신 적재 분기",
                        note=note,
                    )
                )

        for key, label, unit in (
            ("score", "공식 상권 수요·접근성 맥락점수", "점"),
            ("sale_price_proxy_manwon_per_m2", "공식 상권 RTMS 매매가 프록시", "만원/㎡"),
        ):
            value = self._weighted_reference(rows, hits, key)
            if value is not None:
                metrics.append(
                    _metric(
                        key=f"official_{key}",
                        label=label,
                        value=value,
                        unit=unit,
                        method="official_area_value",
                        confidence=confidence,
                        source="상권코드 단위 정형지표",
                        period="최신 적재 분기",
                        note=note,
                    )
                )
        return metrics

    def analyze(self, request: SpatialZoneAnalysisRequest) -> dict[str, Any]:
        if request.official_area_codes:
            records = [self.catalog.get(code) for code in request.official_area_codes]
            missing_codes = [
                code for code, record in zip(request.official_area_codes, records, strict=True) if record is None
            ]
            if missing_codes:
                raise ValueError(f"official trade area boundary not found: {', '.join(missing_codes)}")
            official_records = [record for record in records if record is not None]
            geometry_5181 = _polygonal_only(unary_union([record.geometry_5181 for record in official_records]))
            hits = [
                BoundaryHit(record=record, intersection=record.geometry_5181)
                for record in official_records
            ]
            zone_mode = "official"
            shape_kind = "official_area"
        else:
            if request.shape is None:
                raise ValueError("custom analysis requires shape")
            geometry_5181 = build_zone_geometry(request.shape)
            hits = sorted(
                self.catalog.intersecting(geometry_5181),
                key=lambda hit: hit.intersection_area_m2,
                reverse=True,
            )
            zone_mode = "custom"
            shape_kind = request.shape.kind

        geometry_wgs84 = _wgs84_geometry(geometry_5181)

        store_ready, store_metrics, top_categories = self._direct_store_metrics(
            geometry_wgs84, request.industry_query
        )
        transit_ready, transit_metrics = self._direct_transit_metrics(geometry_wgs84)
        area_metrics = (
            self._official_area_metrics(hits)
            if zone_mode == "official"
            else self._area_based_metrics(hits, float(geometry_5181.area))
        )

        coverage_geometry = unary_union([hit.intersection for hit in hits]) if hits else GeometryCollection()
        coverage_pct = (
            min(float(coverage_geometry.area) / float(geometry_5181.area), 1.0) * 100.0
            if geometry_5181.area
            else 0.0
        )
        centroid_wgs84 = _wgs84_geometry(geometry_5181.centroid)

        intersections = [
            {
                "area_code": hit.record.area_code,
                "area_name": hit.record.area_name,
                "intersection_area_m2": round(hit.intersection_area_m2, 2),
                "zone_share_pct": round(hit.intersection_area_m2 / geometry_5181.area * 100.0, 2),
                "official_area_share_pct": round(
                    hit.intersection_area_m2 / hit.record.area_m2 * 100.0, 2
                ),
            }
            for hit in hits[:20]
        ]

        return {
            "calculation_version": CALCULATION_VERSION,
            "zone_mode": zone_mode,
            "shape_kind": shape_kind,
            "geometry": mapping(geometry_wgs84),
            "centroid": (float(centroid_wgs84.x), float(centroid_wgs84.y)),
            "area_m2": round(float(geometry_5181.area), 2),
            "metrics": [*store_metrics, *transit_metrics, *area_metrics],
            "top_store_categories": top_categories,
            "intersected_areas": intersections,
            "coverage": {
                "official_boundary_coverage_pct": round(coverage_pct, 2),
                "store_point_index_ready": store_ready,
                "transit_point_index_ready": transit_ready,
                "source_crs": SOURCE_CRS,
                "calculation_crs": CALCULATION_CRS,
            },
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
