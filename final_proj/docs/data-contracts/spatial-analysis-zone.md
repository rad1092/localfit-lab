# Spatial Analysis Zone Contract

## Scope separation

- `official` mode accepts one or more official trade-area codes and uses the canonical boundary for those codes.
- `custom` mode accepts a circle, rectangle, or polygon drawn by the user.
- A custom geometry is never stored or described as an official trade area.
- Official trade-area values and custom-zone estimates use different method labels in the API and UI.

## Coordinate contract

| Stage | CRS | Rule |
| --- | --- | --- |
| Kakao map input and API geometry | `EPSG:4326` | Coordinate order is longitude, latitude. |
| Area, radius, and intersection calculation | `EPSG:5181` | All metric distance and area calculations run after server-side transformation. |
| API geometry response | `EPSG:4326` | Returned to the map for display only. |

The canonical official boundary source is `datacorpus/_gold/gold_location_boundary_vertices.csv`. It contains the original calculation coordinates and validated WGS84 display coordinates. The frontend does not transform or infer a projected CRS.

## Calculation labels

| API method | UI label | Meaning |
| --- | --- | --- |
| `direct_aggregation` | 직접 집계 | Coordinate points are tested directly against the requested geometry. |
| `official_area_value` | 공식 상권 지표 | Latest DB value for the selected official trade-area code. |
| `area_ratio_estimate` | 면적 비례 추정 | Area-grain source value is multiplied by its intersection ratio. |
| `regional_reference` | 지역 참고지표 | Area-weighted contextual value; not a direct custom-zone measurement. |

Point-based direct aggregation currently covers:

- SBDC store POIs from `silver_sbdc_store_poi_seoul_202603.csv`
- Seoul bus stops from `silver_bus_stop_location_master.csv`
- Seoul subway stations from `silver_subway_station_master.csv`

Population, floating population, sales, rent, and scores remain official-area-grain data. Custom-zone output must therefore keep the estimate or reference label. News and policy evidence remain a separate unstructured evidence path and are not spatially merged into these numeric metrics.

## Runtime index

The canonical CSV files remain under `datacorpus`. The product builds a compact SQLite RTree index under `final_proj/runtime/db/commercial.db`:

```powershell
Set-Location C:\final_map_project
final_proj\.venv\Scripts\python.exe final_proj\backend\scripts\seed_spatial_index.py
```

The script records source path, file size, modified time, row count, calculation version, and CRS in `spatial_dataset_status`.

## API

- `GET /api/spatial/status`
- `GET /api/spatial/areas/{area_code}/boundary`
- `GET /api/spatial/areas?west=&south=&east=&north=`
- `POST /api/spatial/zones/analyze`

`POST /api/spatial/zones/analyze` accepts exactly one of `official_area_codes` or `shape`. Circle radius is meters; rectangle and polygon coordinates are WGS84 GeoJSON coordinates.
