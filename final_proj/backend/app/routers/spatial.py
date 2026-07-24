from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.settings import BOUNDARY_VERTICES_PATH, BUS_STOP_PATH, STORE_POI_PATH, SUBWAY_STATION_PATH
from app.database import get_db
from app.schemas.spatial import (
    AreaBoundaryCollection,
    AreaBoundaryFeature,
    SpatialZoneAnalysisRequest,
    SpatialZoneAnalysisResponse,
)
from app.services.spatial_analysis import CALCULATION_VERSION, SpatialAnalysisService


router = APIRouter(prefix="/spatial", tags=["spatial"])


def _service(db: Session = Depends(get_db)) -> SpatialAnalysisService:
    return SpatialAnalysisService(db)


@router.get("/status")
def spatial_status(db: Session = Depends(get_db)):
    table_rows = db.execute(
        text(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
              AND name IN (
                'spatial_store_point',
                'spatial_store_point_rtree',
                'spatial_transit_point',
                'spatial_transit_point_rtree'
              )
            """
        )
    ).all()
    tables = {str(row[0]) for row in table_rows}
    return {
        "calculation_version": CALCULATION_VERSION,
        "boundary_source": {"path": str(BOUNDARY_VERTICES_PATH), "ready": BOUNDARY_VERTICES_PATH.exists()},
        "store_source": {"path": str(STORE_POI_PATH), "ready": STORE_POI_PATH.exists()},
        "bus_stop_source": {"path": str(BUS_STOP_PATH), "ready": BUS_STOP_PATH.exists()},
        "subway_source": {"path": str(SUBWAY_STATION_PATH), "ready": SUBWAY_STATION_PATH.exists()},
        "store_index_ready": {
            "spatial_store_point",
            "spatial_store_point_rtree",
        }.issubset(tables),
        "transit_index_ready": {
            "spatial_transit_point",
            "spatial_transit_point_rtree",
        }.issubset(tables),
        "source_crs": "EPSG:4326",
        "calculation_crs": "EPSG:5181",
    }


@router.get("/areas", response_model=AreaBoundaryCollection)
def area_boundaries_in_view(
    west: float,
    south: float,
    east: float,
    north: float,
    simplify_m: float = Query(default=3.0, ge=0.0, le=100.0),
    limit: int = Query(default=200, ge=1, le=500),
    service: SpatialAnalysisService = Depends(_service),
):
    try:
        return service.area_boundaries_in_view(
            west=west,
            south=south,
            east=east,
            north=north,
            simplify_m=simplify_m,
            limit=limit,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/areas/{area_code}/boundary", response_model=AreaBoundaryFeature)
def area_boundary(
    area_code: str,
    simplify_m: float = Query(default=1.5, ge=0.0, le=100.0),
    service: SpatialAnalysisService = Depends(_service),
):
    feature = service.area_boundary(area_code, simplify_m)
    if not feature:
        raise HTTPException(status_code=404, detail="official trade area boundary not found")
    return feature


@router.post("/zones/analyze", response_model=SpatialZoneAnalysisResponse)
def analyze_zone(
    request: SpatialZoneAnalysisRequest,
    service: SpatialAnalysisService = Depends(_service),
):
    try:
        return service.analyze(request)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
