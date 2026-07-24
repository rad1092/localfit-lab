from __future__ import annotations

import logging
import os
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone

from app.core.settings import DATABASE_PATH


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RuntimeSchemaResult:
    users_column_added: bool
    configured_admins_promoted: int
    legacy_admin_promoted: bool
    admin_count: int


def configured_admin_emails() -> set[str]:
    """Return admin e-mails explicitly configured outside source control."""
    raw_values = [
        os.getenv("LOCALFIT_ADMIN_EMAIL", ""),
        os.getenv("LOCALFIT_ADMIN_EMAILS", ""),
    ]
    return {
        value.strip().casefold()
        for raw in raw_values
        for value in re.split(r"[,;\s]+", raw)
        if value.strip()
    }


def is_configured_admin_email(email: str) -> bool:
    return email.strip().casefold() in configured_admin_emails()


def development_admin_bootstrap_enabled() -> bool:
    """Allow a one-time first-admin fallback only outside production."""
    environment = os.getenv("LOCALFIT_ENV", "development").strip().casefold()
    if environment in {"prod", "production"}:
        return False
    mode = os.getenv("LOCALFIT_LEGACY_ADMIN_BOOTSTRAP", "oldest_existing")
    return mode.strip().casefold() in {"1", "true", "yes", "oldest", "oldest_existing"}


def ensure_runtime_schema() -> RuntimeSchemaResult:
    """Apply small, idempotent SQLite upgrades required before ORM queries.

    A legacy database did not have ``users.is_admin``. On that one local/development
    migration only, the oldest existing account becomes the first administrator
    unless explicitly disabled. Production always requires an exact account through
    ``LOCALFIT_ADMIN_EMAIL``/``LOCALFIT_ADMIN_EMAILS``.
    """
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    column_added = False
    configured_promoted = 0
    legacy_promoted = False
    admin_count = 0
    rone_columns_added: list[str] = []
    rone_contract_rows_backfilled = 0
    token_usage_columns_added: list[str] = []
    duplicate_evaluations_failed = 0

    with closing(sqlite3.connect(DATABASE_PATH, timeout=30)) as connection, connection:
        connection.execute("PRAGMA busy_timeout = 30000")
        token_usage_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'token_usage_log'"
        ).fetchone()
        if token_usage_exists:
            token_usage_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(token_usage_log)").fetchall()
            }
            for column_name, declaration in (
                ("status", "TEXT NOT NULL DEFAULT 'success'"),
                ("reasoning_effort", "TEXT"),
                ("generation_mode", "TEXT"),
                ("quality_status", "TEXT"),
                ("original_validation_issues_json", "TEXT"),
                ("error_type", "TEXT"),
                ("error_message", "TEXT"),
            ):
                if column_name not in token_usage_columns:
                    connection.execute(
                        f'ALTER TABLE token_usage_log ADD COLUMN "{column_name}" {declaration}'
                    )
                    token_usage_columns_added.append(column_name)
            connection.execute(
                "CREATE INDEX IF NOT EXISTS ix_token_usage_status_created "
                "ON token_usage_log(status, created_at)"
            )

        report_evaluation_exists = connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'table' AND name = 'report_evaluation_run'"
        ).fetchone()
        if report_evaluation_exists:
            duplicate_jobs = connection.execute(
                "SELECT report_job_id FROM report_evaluation_run "
                "WHERE status IN ('queued', 'running') "
                "GROUP BY report_job_id HAVING COUNT(*) > 1"
            ).fetchall()
            recovered_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            for (report_job_id,) in duplicate_jobs:
                active_rows = connection.execute(
                    "SELECT id FROM report_evaluation_run "
                    "WHERE report_job_id = ? "
                    "AND status IN ('queued', 'running') "
                    "ORDER BY created_at DESC, id DESC",
                    (report_job_id,),
                ).fetchall()
                stale_ids = [str(row[0]) for row in active_rows[1:]]
                if not stale_ids:
                    continue
                placeholders = ",".join("?" for _ in stale_ids)
                cursor = connection.execute(
                    "UPDATE report_evaluation_run SET "
                    "status = 'failed', "
                    "progress_message = ?, error_message = ?, completed_at = ? "
                    f"WHERE id IN ({placeholders})",
                    (
                        "중복 활성 평가를 종료했습니다. 다시 실행할 수 있습니다.",
                        "동시 실행으로 생성된 중복 평가를 런타임 마이그레이션에서 종료했습니다.",
                        recovered_at,
                        *stale_ids,
                    ),
                )
                duplicate_evaluations_failed += max(
                    0,
                    int(cursor.rowcount or 0),
                )
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "ux_report_evaluation_active_job "
                "ON report_evaluation_run(report_job_id) "
                "WHERE status IN ('queued', 'running')"
            )

        rone_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'area_rone_cost_reference'"
        ).fetchone()
        if rone_exists:
            rone_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(area_rone_cost_reference)").fetchall()
            }
            for column_name, declaration in (
                ("selection_group", "TEXT"),
                ("direct_value_allowed", "INTEGER NOT NULL DEFAULT 0"),
                ("proxy_score_allowed", "INTEGER NOT NULL DEFAULT 0"),
                ("engine_promotion_ready", "INTEGER NOT NULL DEFAULT 0"),
                ("forbidden_claim_ko", "TEXT"),
            ):
                if column_name not in rone_columns:
                    connection.execute(
                        f'ALTER TABLE area_rone_cost_reference ADD COLUMN "{column_name}" {declaration}'
                    )
                    rone_columns_added.append(column_name)
            cursor = connection.execute(
                "UPDATE area_rone_cost_reference SET "
                "direct_value_allowed = 0, proxy_score_allowed = 0, "
                "engine_promotion_ready = 0, "
                "forbidden_claim_ko = COALESCE(NULLIF(forbidden_claim_ko, ''), ?) "
                "WHERE COALESCE(direct_value_allowed, 0) <> 0 "
                "   OR COALESCE(proxy_score_allowed, 0) <> 0 "
                "   OR COALESCE(engine_promotion_ready, 0) <> 0 "
                "   OR forbidden_claim_ko IS NULL OR forbidden_claim_ko = ''",
                ("개별 점포 월세, 권리금 확정값, 수익성을 보장하지 않는다.",),
            )
            rone_contract_rows_backfilled = max(0, int(cursor.rowcount or 0))

        users_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'users'"
        ).fetchone()
        if not users_exists:
            if token_usage_columns_added or rone_columns_added or rone_contract_rows_backfilled:
                logger.warning(
                    "Runtime schema updated: token_usage_columns_added=%s "
                    "rone_columns_added=%s contract_rows_backfilled=%s",
                    token_usage_columns_added,
                    rone_columns_added,
                    rone_contract_rows_backfilled,
                )
            return RuntimeSchemaResult(False, 0, False, 0)

        columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(users)").fetchall()
        }
        if "is_admin" not in columns:
            connection.execute(
                "ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0"
            )
            column_added = True

        duplicate_email = connection.execute(
            "SELECT lower(trim(email)) AS canonical_email, COUNT(*) "
            "FROM users GROUP BY lower(trim(email)) HAVING COUNT(*) > 1 LIMIT 1"
        ).fetchone()
        if duplicate_email:
            raise RuntimeError(
                "users contains case-insensitive duplicate email accounts: "
                f"{duplicate_email[0]}"
            )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_users_email_ci "
            "ON users(lower(trim(email)))"
        )

        for email in configured_admin_emails():
            cursor = connection.execute(
                "UPDATE users SET is_admin = 1 "
                "WHERE lower(email) = lower(?) AND COALESCE(is_admin, 0) <> 1",
                (email,),
            )
            configured_promoted += max(0, int(cursor.rowcount or 0))

        admin_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM users WHERE COALESCE(is_admin, 0) = 1"
            ).fetchone()[0]
        )
        if column_added and admin_count == 0 and development_admin_bootstrap_enabled():
            oldest = connection.execute(
                "SELECT id FROM users ORDER BY id ASC LIMIT 1"
            ).fetchone()
            if oldest:
                connection.execute(
                    "UPDATE users SET is_admin = 1 WHERE id = ?", (int(oldest[0]),)
                )
                legacy_promoted = True
                admin_count = 1

    result = RuntimeSchemaResult(
        users_column_added=column_added,
        configured_admins_promoted=configured_promoted,
        legacy_admin_promoted=legacy_promoted,
        admin_count=admin_count,
    )
    if any((column_added, configured_promoted, legacy_promoted, token_usage_columns_added, rone_columns_added, rone_contract_rows_backfilled, duplicate_evaluations_failed)):
        logger.warning(
            "Runtime auth schema updated: column_added=%s configured_promoted=%s "
            "legacy_promoted=%s admin_count=%s token_usage_columns_added=%s rone_columns_added=%s "
            "rone_contract_rows_backfilled=%s duplicate_evaluations_failed=%s",
            column_added,
            configured_promoted,
            legacy_promoted,
            admin_count,
            token_usage_columns_added,
            rone_columns_added,
            rone_contract_rows_backfilled,
            duplicate_evaluations_failed,
        )
    return result
