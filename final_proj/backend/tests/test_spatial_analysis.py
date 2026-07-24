from __future__ import annotations

import math
import unittest

from shapely.geometry import shape

from app.database import SessionLocal
from app.schemas.spatial import SpatialZoneAnalysisRequest
from app.services.spatial_analysis import (
    BoundaryCatalog,
    SpatialAnalysisService,
    boundary_feature,
    build_zone_geometry,
)


class SpatialGeometryTest(unittest.TestCase):
    def test_circle_is_measured_in_epsg5181_meters(self):
        request = SpatialZoneAnalysisRequest.model_validate(
            {
                "shape": {
                    "kind": "circle",
                    "center": [126.9943153697, 37.5342971362],
                    "radius_m": 100,
                }
            }
        )
        geometry = build_zone_geometry(request.shape)
        self.assertAlmostEqual(geometry.area, math.pi * 100**2, delta=math.pi * 100**2 * 0.01)

    def test_polygon_coordinates_are_transformed_before_area_calculation(self):
        request = SpatialZoneAnalysisRequest.model_validate(
            {
                "shape": {
                    "kind": "rectangle",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [126.993, 37.533],
                                [126.996, 37.533],
                                [126.996, 37.536],
                                [126.993, 37.536],
                                [126.993, 37.533],
                            ]
                        ],
                    },
                }
            }
        )
        geometry = build_zone_geometry(request.shape)
        self.assertGreater(geometry.area, 50_000)
        self.assertLess(geometry.area, 150_000)


class SpatialIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = BoundaryCatalog()

    def test_official_boundary_uses_wgs84_for_map_output(self):
        record = self.catalog.get("3001491")
        self.assertIsNotNone(record)
        feature = boundary_feature(record)
        geometry = shape(feature["geometry"])
        west, south, east, north = geometry.bounds
        self.assertTrue(126.9 < west < 127.1)
        self.assertTrue(37.4 < south < 37.7)
        self.assertTrue(126.9 < east < 127.1)
        self.assertTrue(37.4 < north < 37.7)
        self.assertEqual(feature["properties"]["source_crs"], "EPSG:5181")
        self.assertEqual(feature["properties"]["display_crs"], "EPSG:4326")

    def test_custom_circle_uses_point_indexes_and_boundary_intersections(self):
        request = SpatialZoneAnalysisRequest.model_validate(
            {
                "shape": {
                    "kind": "circle",
                    "center": [126.9943153697, 37.5342971362],
                    "radius_m": 250,
                }
            }
        )
        with SessionLocal() as db:
            result = SpatialAnalysisService(db, self.catalog).analyze(request)

        self.assertEqual(result["coverage"]["source_crs"], "EPSG:4326")
        self.assertEqual(result["coverage"]["calculation_crs"], "EPSG:5181")
        self.assertTrue(result["coverage"]["store_point_index_ready"])
        self.assertTrue(result["coverage"]["transit_point_index_ready"])
        self.assertGreater(result["area_m2"], 190_000)
        self.assertTrue(any(item["area_code"] == "3001491" for item in result["intersected_areas"]))
        store_metric = next(item for item in result["metrics"] if item["key"] == "store_count")
        self.assertEqual(store_metric["method"], "direct_aggregation")
        self.assertGreater(store_metric["value"], 0)

    def test_official_area_mode_keeps_code_level_metrics_separate(self):
        request = SpatialZoneAnalysisRequest.model_validate(
            {"official_area_codes": ["3001491"]}
        )
        with SessionLocal() as db:
            result = SpatialAnalysisService(db, self.catalog).analyze(request)

        self.assertEqual(result["zone_mode"], "official")
        self.assertEqual(result["shape_kind"], "official_area")
        self.assertEqual([item["area_code"] for item in result["intersected_areas"]], ["3001491"])
        sales_metric = next(item for item in result["metrics"] if item["key"] == "sales_amount")
        self.assertEqual(sales_metric["method"], "official_area_value")
        self.assertGreater(sales_metric["value"], 0)


if __name__ == "__main__":
    unittest.main()
