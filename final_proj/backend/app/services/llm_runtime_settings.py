from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Literal, cast

from app.core.settings import RUNTIME_ROOT


ReasoningEffort = Literal["none", "low", "medium", "high", "xhigh"]
SUPPORTED_REPORT_REASONING_EFFORTS: tuple[ReasoningEffort, ...] = (
    "none",
    "low",
    "medium",
    "high",
    "xhigh",
)
DEFAULT_REPORT_REASONING_EFFORT: ReasoningEffort = "low"
REPORT_REASONING_ENV = "OPENAI_REPORT_REASONING_EFFORT"
REPORT_REASONING_SETTING_KEY = "openai_report_reasoning_effort"
SETTINGS_DB_PATH = RUNTIME_ROOT / "admin" / "pipeline_jobs.db"


@dataclass(frozen=True)
class ReportReasoningSettings:
    reasoning_effort: ReasoningEffort
    source: Literal["admin", "environment", "default"]
    updated_at: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            **asdict(self),
            "supported_reasoning_efforts": list(SUPPORTED_REPORT_REASONING_EFFORTS),
        }


def _normalize_reasoning_effort(value: str) -> ReasoningEffort:
    normalized = str(value or "").strip().casefold()
    if normalized not in SUPPORTED_REPORT_REASONING_EFFORTS:
        supported = ", ".join(SUPPORTED_REPORT_REASONING_EFFORTS)
        raise ValueError(f"Unsupported report reasoning effort: {value!r}. Expected one of: {supported}")
    return cast(ReasoningEffort, normalized)


def _connect_settings() -> sqlite3.Connection:
    SETTINGS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(SETTINGS_DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 30000")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS runtime_setting (
            setting_key TEXT PRIMARY KEY,
            setting_value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    return connection


def _default_settings() -> ReportReasoningSettings:
    configured = os.getenv(REPORT_REASONING_ENV, "").strip()
    if configured:
        return ReportReasoningSettings(
            reasoning_effort=_normalize_reasoning_effort(configured),
            source="environment",
            updated_at=None,
        )
    return ReportReasoningSettings(
        reasoning_effort=DEFAULT_REPORT_REASONING_EFFORT,
        source="default",
        updated_at=None,
    )


def read_report_reasoning_settings() -> ReportReasoningSettings:
    with closing(_connect_settings()) as connection:
        row = connection.execute(
            "SELECT setting_value, updated_at FROM runtime_setting WHERE setting_key = ?",
            (REPORT_REASONING_SETTING_KEY,),
        ).fetchone()
    if row is None:
        return _default_settings()
    return ReportReasoningSettings(
        reasoning_effort=_normalize_reasoning_effort(str(row["setting_value"])),
        source="admin",
        updated_at=str(row["updated_at"]),
    )


def get_report_reasoning_effort() -> ReasoningEffort:
    return read_report_reasoning_settings().reasoning_effort


def set_report_reasoning_effort(value: str) -> ReportReasoningSettings:
    reasoning_effort = _normalize_reasoning_effort(value)
    updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with closing(_connect_settings()) as connection, connection:
        connection.execute(
            """
            INSERT INTO runtime_setting (setting_key, setting_value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(setting_key) DO UPDATE SET
                setting_value = excluded.setting_value,
                updated_at = excluded.updated_at
            """,
            (REPORT_REASONING_SETTING_KEY, reasoning_effort, updated_at),
        )
    return ReportReasoningSettings(
        reasoning_effort=reasoning_effort,
        source="admin",
        updated_at=updated_at,
    )
