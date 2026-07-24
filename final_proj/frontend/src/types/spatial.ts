export type GeoJsonPolygonGeometry = {
  type: "Polygon" | "MultiPolygon";
  coordinates: number[][][] | number[][][][];
};

export type PolygonZoneShape = {
  kind: "polygon" | "rectangle";
  geometry: GeoJsonPolygonGeometry;
};

export type CircleZoneShape = {
  kind: "circle";
  center: [number, number];
  radius_m: number;
};

export type ZoneShape = PolygonZoneShape | CircleZoneShape;

export type SpatialMetricMethod =
  | "direct_aggregation"
  | "official_area_value"
  | "area_ratio_estimate"
  | "regional_reference";

export interface SpatialMetric {
  key: string;
  label: string;
  value: number | null;
  unit: string;
  method: SpatialMetricMethod;
  method_label: string;
  confidence: "high" | "medium" | "reference";
  source: string;
  period?: string | null;
  note?: string | null;
}

export interface SpatialCategoryCount {
  code?: string | null;
  name: string;
  count: number;
}

export interface AreaIntersection {
  area_code: string;
  area_name: string;
  intersection_area_m2: number;
  zone_share_pct: number;
  official_area_share_pct: number;
}

export interface SpatialZoneAnalysis {
  calculation_version: string;
  zone_mode: "official" | "custom";
  shape_kind: ZoneShape["kind"] | "official_area";
  geometry: GeoJsonPolygonGeometry;
  centroid: [number, number];
  area_m2: number;
  metrics: SpatialMetric[];
  top_store_categories: SpatialCategoryCount[];
  intersected_areas: AreaIntersection[];
  coverage: {
    official_boundary_coverage_pct: number;
    store_point_index_ready: boolean;
    transit_point_index_ready: boolean;
    source_crs: "EPSG:4326";
    calculation_crs: "EPSG:5181";
  };
  generated_at: string;
}

export interface AreaBoundaryFeature {
  type: "Feature";
  geometry: GeoJsonPolygonGeometry;
  properties: {
    area_code: string;
    area_name: string;
    area_m2: number;
    source_crs: "EPSG:5181";
    display_crs: "EPSG:4326";
    calculation_version: string;
  };
}
