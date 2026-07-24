from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


BACKEND_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BACKEND_ROOT.parent
WORKSPACE_ROOT = PROJECT_ROOT.parent

load_dotenv(BACKEND_ROOT / ".env")


def _cors_origins_from_env() -> list[str]:
    raw = os.getenv("LOCALFIT_CORS_ORIGINS", "").strip()
    origins = [
        origin.strip().rstrip("/")
        for origin in raw.split(",")
        if origin.strip()
    ] or ["http://127.0.0.1:3000", "http://localhost:3000"]
    if "*" in origins:
        raise RuntimeError("LOCALFIT_CORS_ORIGINS must list explicit origins, not '*'")
    return origins


def _path_from_env(name: str, default: Path) -> Path:
    raw = os.getenv(name, "").strip()
    path = Path(raw).expanduser() if raw else default
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


RUNTIME_ROOT = _path_from_env("LOCALFIT_RUNTIME_ROOT", PROJECT_ROOT / "runtime")
DATABASE_PATH = _path_from_env("LOCALFIT_DATABASE_PATH", RUNTIME_ROOT / "db" / "commercial.db")
DATABASE_BACKUP_ROOT = _path_from_env(
    "LOCALFIT_DATABASE_BACKUP_ROOT",
    RUNTIME_ROOT / "db" / "backups",
)
REPORTS_ROOT = _path_from_env("LOCALFIT_REPORTS_ROOT", RUNTIME_ROOT / "reports")
EXPORTS_ROOT = _path_from_env("LOCALFIT_EXPORTS_ROOT", RUNTIME_ROOT / "exports")
LOGS_ROOT = _path_from_env("LOCALFIT_LOGS_ROOT", RUNTIME_ROOT / "logs")
TMP_ROOT = _path_from_env("LOCALFIT_TMP_ROOT", RUNTIME_ROOT / "tmp")

DATA_ROOT = _path_from_env("LOCALFIT_DATA_ROOT", WORKSPACE_ROOT / "datacorpus")
RESEARCH_ROOT = _path_from_env("LOCALFIT_RESEARCH_ROOT", WORKSPACE_ROOT / "research")
KEY_FILE = _path_from_env("LOCALFIT_KEY_FILE", WORKSPACE_ROOT / "docs" / "90_private" / "key.md")
KNOWLEDGE_ROOT = _path_from_env(
    "LOCALFIT_KNOWLEDGE_ROOT",
    PROJECT_ROOT / "resources" / "knowledge" / "rag_sources",
)

BOUNDARY_VERTICES_PATH = _path_from_env(
    "LOCALFIT_BOUNDARY_VERTICES_PATH",
    DATA_ROOT / "_gold" / "gold_location_boundary_vertices.csv",
)
STORE_POI_PATH = _path_from_env(
    "LOCALFIT_STORE_POI_PATH",
    DATA_ROOT / "_silver" / "silver_sbdc_store_poi_seoul_202603.csv",
)
BUS_STOP_PATH = _path_from_env(
    "LOCALFIT_BUS_STOP_PATH",
    DATA_ROOT / "_silver" / "silver_bus_stop_location_master.csv",
)
SUBWAY_STATION_PATH = _path_from_env(
    "LOCALFIT_SUBWAY_STATION_PATH",
    DATA_ROOT / "_silver" / "silver_subway_station_master.csv",
)

SQLALCHEMY_DATABASE_URL = f"sqlite:///{DATABASE_PATH.as_posix()}"
CORS_ORIGINS = _cors_origins_from_env()
