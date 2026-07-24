from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class GeoJsonPolygon(BaseModel):
    type: Literal["Polygon", "MultiPolygon"]
    coordinates: list[Any]


class ZoneShapeInput(BaseModel):
    kind: Literal["polygon", "rectangle", "circle"]
    geometry: GeoJsonPolygon | None = None
    center: tuple[float, float] | None = None
    radius_m: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_shape_payload(self):
        if self.kind == "circle":
            if self.center is None or self.radius_m is None:
                raise ValueError("circle requires center and radius_m")
            if self.geometry is not None:
                raise ValueError("circle does not accept geometry")
        else:
            if self.geometry is None:
                raise ValueError(f"{self.kind} requires geometry")
            if self.center is not None or self.radius_m is not None:
                raise ValueError(f"{self.kind} does not accept center or radius_m")
        return self


class SpatialZoneAnalysisRequest(BaseModel):
    shape: ZoneShapeInput | None = None
    official_area_codes: list[str] = Field(default_factory=list, max_length=20)
    industry_query: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def validate_analysis_source(self):
        has_shape = self.shape is not None
        has_official_areas = bool(self.official_area_codes)
        if has_shape == has_official_areas:
            raise ValueError("provide either shape or official_area_codes")
        self.official_area_codes = list(dict.fromkeys(str(code).strip() for code in self.official_area_codes if str(code).strip()))
        if len(self.official_area_codes) > 20:
            raise ValueError("official_area_codes supports up to 20 areas")
        return self


class SpatialMetric(BaseModel):
    key: str
    label: str
    value: float | int | None
    unit: str
    method: Literal[
        "direct_aggregation",
        "official_area_value",
        "area_ratio_estimate",
        "regional_reference",
    ]
    method_label: str
    confidence: Literal["high", "medium", "reference"]
    source: str
    period: str | None = None
    note: str | None = None


class SpatialCategoryCount(BaseModel):
    code: str | None = None
    name: str
    count: int


class AreaIntersection(BaseModel):
    area_code: str
    area_name: str
    intersection_area_m2: float
    zone_share_pct: float
    official_area_share_pct: float


class SpatialCoverage(BaseModel):
    official_boundary_coverage_pct: float
    store_point_index_ready: bool
    transit_point_index_ready: bool
    source_crs: Literal["EPSG:4326"] = "EPSG:4326"
    calculation_crs: Literal["EPSG:5181"] = "EPSG:5181"


class SpatialZoneAnalysisResponse(BaseModel):
    calculation_version: str
    zone_mode: Literal["official", "custom"]
    shape_kind: Literal["official_area", "polygon", "rectangle", "circle"]
    geometry: dict[str, Any]
    centroid: tuple[float, float]
    area_m2: float
    metrics: list[SpatialMetric]
    top_store_categories: list[SpatialCategoryCount]
    intersected_areas: list[AreaIntersection]
    coverage: SpatialCoverage
    generated_at: str


class AreaBoundaryFeature(BaseModel):
    type: Literal["Feature"] = "Feature"
    geometry: dict[str, Any]
    properties: dict[str, Any]


class AreaBoundaryCollection(BaseModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[AreaBoundaryFeature]
    calculation_version: str
    source_crs: Literal["EPSG:5181"] = "EPSG:5181"
    display_crs: Literal["EPSG:4326"] = "EPSG:4326"
