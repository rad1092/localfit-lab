from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


PRIVATE_TABLES = (
    "favorite_area",
    "chatbot_history",
    "saved_report",
    "ai_report_generation_cache",
    "users",
)


def create_handoff_database(source: Path, target: Path) -> None:
    if target.exists():
        raise FileExistsError(target)
    target.parent.mkdir(parents=True, exist_ok=True)

    source_connection = sqlite3.connect(f"file:{source.resolve().as_posix()}?mode=ro", uri=True)
    target_connection = sqlite3.connect(target)
    try:
        source_connection.backup(target_connection)
        target_connection.execute("PRAGMA foreign_keys=OFF")
        for table in PRIVATE_TABLES:
            target_connection.execute(f'DELETE FROM "{table}"')
        placeholders = ",".join("?" for _ in PRIVATE_TABLES)
        target_connection.execute(
            f"DELETE FROM sqlite_sequence WHERE name IN ({placeholders})",
            PRIVATE_TABLES,
        )
        target_connection.commit()
        integrity = target_connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"integrity_check failed: {integrity}")
        target_connection.execute("VACUUM")
    finally:
        source_connection.close()
        target_connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a privacy-clean LocalFit handoff database.")
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    create_handoff_database(args.source, args.target)
    print(args.target.resolve())


if __name__ == "__main__":
    main()
