from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


# Tables below contain local accounts, generated reports, usage logs, comments,
# API call logs, or other mutable development state. The published reference
# and scoring tables are intentionally preserved.
RESET_TABLES = (
    "report_evaluation_run",
    "report_generation_job",
    "pdf_export_history",
    "favorite_area",
    "chatbot_history",
    "saved_report",
    "ai_report_generation_cache",
    "comments",
    "token_usage_log",
    "user_events",
    "external_api_log",
    "users",
)


def table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        is not None
    )


def create_production_database(source: Path, target: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(source)
    if target.exists():
        raise FileExistsError(target)

    target.parent.mkdir(parents=True, exist_ok=True)
    source_uri = f"file:{source.resolve().as_posix()}?mode=ro"
    source_connection = sqlite3.connect(source_uri, uri=True, timeout=60)
    target_connection = sqlite3.connect(target, timeout=60)
    try:
        source_connection.execute("PRAGMA busy_timeout = 60000")
        target_connection.execute("PRAGMA busy_timeout = 60000")
        source_connection.backup(target_connection)

        target_connection.execute("PRAGMA foreign_keys = OFF")
        reset = [table for table in RESET_TABLES if table_exists(target_connection, table)]
        for table in reset:
            target_connection.execute(f'DELETE FROM "{table}"')

        if table_exists(target_connection, "sqlite_sequence") and reset:
            placeholders = ",".join("?" for _ in reset)
            target_connection.execute(
                f"DELETE FROM sqlite_sequence WHERE name IN ({placeholders})",
                reset,
            )
        target_connection.commit()

        target_connection.execute("VACUUM")
        target_connection.execute("PRAGMA journal_mode = DELETE")
        integrity = target_connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"integrity_check failed: {integrity}")
        foreign_key_errors = target_connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_errors:
            raise RuntimeError(
                f"foreign_key_check failed with {len(foreign_key_errors)} row(s)"
            )
    except Exception:
        target_connection.close()
        source_connection.close()
        if target.exists():
            target.unlink()
        raise
    else:
        target_connection.close()
        source_connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a privacy-clean LocalFit production database."
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    create_production_database(args.source, args.target)
    print(args.target.resolve())


if __name__ == "__main__":
    main()
