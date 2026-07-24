from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.settings import (  # noqa: E402
    BUS_STOP_PATH,
    DATABASE_PATH,
    STORE_POI_PATH,
    SUBWAY_STATION_PATH,
)
from app.services.spatial_analysis import CALCULATION_VERSION  # noqa: E402


BATCH_SIZE = 10_000


def _float(row: dict[str, str], key: str) -> float | None:
    try:
        value = float(str(row.get(key, "")).strip())
    except (TypeError, ValueError):
        return None
    return value


def _valid_wgs84(lon: float | None, lat: float | None) -> bool:
    return lon is not None and lat is not None and 124.0 <= lon <= 132.0 and 33.0 <= lat <= 39.5


def _source_metadata(path: Path, record_count: int, notes: str) -> tuple:
    stat = path.stat()
    return (
        str(path),
        stat.st_size,
        datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        record_count,
        datetime.now(timezone.utc).isoformat(),
        "EPSG:4326",
        "EPSG:5181",
        CALCULATION_VERSION,
        notes,
    )


def ensure_status_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS spatial_dataset_status (
            dataset_key TEXT PRIMARY KEY,
            source_path TEXT NOT NULL,
            source_size_bytes INTEGER NOT NULL,
            source_modified_at TEXT NOT NULL,
            record_count INTEGER NOT NULL,
            loaded_at TEXT NOT NULL,
            source_crs TEXT NOT NULL,
            calculation_crs TEXT NOT NULL,
            calculation_version TEXT NOT NULL,
            notes TEXT
        )
        """
    )


def rebuild_store_index(connection: sqlite3.Connection, source_path: Path) -> int:
    if not source_path.exists():
        raise FileNotFoundError(f"store POI source not found: {source_path}")

    connection.execute("DROP TABLE IF EXISTS spatial_store_point_rtree")
    connection.execute("DROP TABLE IF EXISTS spatial_store_point")
    connection.execute(
        """
        CREATE TABLE spatial_store_point (
            id INTEGER PRIMARY KEY,
            source_store_id TEXT,
            store_name TEXT,
            major_code TEXT,
            major_name TEXT,
            middle_code TEXT,
            middle_name TEXT,
            minor_code TEXT,
            minor_name TEXT,
            lon REAL NOT NULL,
            lat REAL NOT NULL,
            source_period TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE VIRTUAL TABLE spatial_store_point_rtree
        USING rtree(id, min_lon, max_lon, min_lat, max_lat)
        """
    )

    insert_point = """
        INSERT INTO spatial_store_point (
            id, source_store_id, store_name,
            major_code, major_name, middle_code, middle_name, minor_code, minor_name,
            lon, lat, source_period
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    insert_rtree = """
        INSERT INTO spatial_store_point_rtree (id, min_lon, max_lon, min_lat, max_lat)
        VALUES (?, ?, ?, ?, ?)
    """
    point_batch: list[tuple] = []
    rtree_batch: list[tuple] = []
    record_id = 0

    with source_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            lon = _float(row, "경도")
            lat = _float(row, "위도")
            if not _valid_wgs84(lon, lat):
                continue
            if str(row.get("좌표유효여부", "True")).strip().lower() == "false":
                continue
            record_id += 1
            point_batch.append(
                (
                    record_id,
                    row.get("상가업소번호"),
                    row.get("상호명"),
                    row.get("상권업종대분류코드"),
                    row.get("상권업종대분류명"),
                    row.get("상권업종중분류코드"),
                    row.get("상권업종중분류명"),
                    row.get("상권업종소분류코드"),
                    row.get("상권업종소분류명"),
                    lon,
                    lat,
                    row.get("기준_년월"),
                )
            )
            rtree_batch.append((record_id, lon, lon, lat, lat))
            if len(point_batch) >= BATCH_SIZE:
                connection.executemany(insert_point, point_batch)
                connection.executemany(insert_rtree, rtree_batch)
                point_batch.clear()
                rtree_batch.clear()

    if point_batch:
        connection.executemany(insert_point, point_batch)
        connection.executemany(insert_rtree, rtree_batch)

    connection.execute("CREATE INDEX idx_spatial_store_minor_code ON spatial_store_point(minor_code)")
    connection.execute("CREATE INDEX idx_spatial_store_middle_code ON spatial_store_point(middle_code)")
    connection.execute("CREATE INDEX idx_spatial_store_major_code ON spatial_store_point(major_code)")
    connection.execute(
        """
        INSERT OR REPLACE INTO spatial_dataset_status (
            dataset_key, source_path, source_size_bytes, source_modified_at, record_count,
            loaded_at, source_crs, calculation_crs, calculation_version, notes
        ) VALUES ('store_point', ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        _source_metadata(
            source_path,
            record_id,
            "좌표 포함 점포만 적재하며 사용자 지정 영역 내부 포함 판정에 사용한다.",
        ),
    )
    return record_id


def rebuild_transit_index(
    connection: sqlite3.Connection,
    bus_source: Path,
    subway_source: Path,
) -> int:
    for source in (bus_source, subway_source):
        if not source.exists():
            raise FileNotFoundError(f"transit source not found: {source}")

    connection.execute("DROP TABLE IF EXISTS spatial_transit_point_rtree")
    connection.execute("DROP TABLE IF EXISTS spatial_transit_point")
    connection.execute(
        """
        CREATE TABLE spatial_transit_point (
            id INTEGER PRIMARY KEY,
            mode TEXT NOT NULL,
            source_point_id TEXT,
            point_name TEXT,
            detail_name TEXT,
            lon REAL NOT NULL,
            lat REAL NOT NULL,
            source_snapshot TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE VIRTUAL TABLE spatial_transit_point_rtree
        USING rtree(id, min_lon, max_lon, min_lat, max_lat)
        """
    )

    points: list[tuple] = []
    rtree_rows: list[tuple] = []
    record_id = 0

    with bus_source.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            lon = _float(row, "경도")
            lat = _float(row, "위도")
            if not _valid_wgs84(lon, lat):
                continue
            if str(row.get("quality_coordinate_missing", "False")).strip().lower() == "true":
                continue
            record_id += 1
            points.append(
                (
                    record_id,
                    "bus",
                    row.get("정류소_고유번호"),
                    row.get("정류소_명"),
                    row.get("정류소_유형"),
                    lon,
                    lat,
                    row.get("snapshot_date"),
                )
            )
            rtree_rows.append((record_id, lon, lon, lat, lat))

    with subway_source.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            lon = _float(row, "경도")
            lat = _float(row, "위도")
            if not _valid_wgs84(lon, lat):
                continue
            if str(row.get("quality_coordinate_missing", "False")).strip().lower() == "true":
                continue
            record_id += 1
            points.append(
                (
                    record_id,
                    "subway",
                    row.get("역사_ID"),
                    row.get("역사_명"),
                    row.get("호선_명"),
                    lon,
                    lat,
                    row.get("snapshot_date"),
                )
            )
            rtree_rows.append((record_id, lon, lon, lat, lat))

    connection.executemany(
        """
        INSERT INTO spatial_transit_point (
            id, mode, source_point_id, point_name, detail_name, lon, lat, source_snapshot
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        points,
    )
    connection.executemany(
        """
        INSERT INTO spatial_transit_point_rtree (id, min_lon, max_lon, min_lat, max_lat)
        VALUES (?, ?, ?, ?, ?)
        """,
        rtree_rows,
    )
    connection.execute("CREATE INDEX idx_spatial_transit_mode ON spatial_transit_point(mode)")
    connection.execute(
        """
        INSERT OR REPLACE INTO spatial_dataset_status (
            dataset_key, source_path, source_size_bytes, source_modified_at, record_count,
            loaded_at, source_crs, calculation_crs, calculation_version, notes
        ) VALUES ('transit_point', ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        _source_metadata(
            bus_source,
            record_id,
            f"버스정류소와 지하철 역사를 직접 집계한다. subway_source={subway_source}",
        ),
    )
    return record_id


def main() -> None:
    parser = argparse.ArgumentParser(description="Build compact spatial point indexes for final_proj.")
    parser.add_argument("--skip-stores", action="store_true")
    parser.add_argument("--skip-transit", action="store_true")
    args = parser.parse_args()

    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    ensure_status_table(connection)

    summary: dict[str, int | str] = {"database": str(DATABASE_PATH)}
    try:
        if not args.skip_stores:
            with connection:
                summary["store_points"] = rebuild_store_index(connection, STORE_POI_PATH)
        if not args.skip_transit:
            with connection:
                summary["transit_points"] = rebuild_transit_index(
                    connection,
                    BUS_STOP_PATH,
                    SUBWAY_STATION_PATH,
                )
    finally:
        connection.close()

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
