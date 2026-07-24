from __future__ import annotations

import csv
import hashlib
from collections import defaultdict
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RAW_ROOT = ROOT / "datacorpus" / "_raw_ingest"
INVENTORY_PATH = RAW_ROOT / "raw_file_inventory.csv"
DUPLICATE_PATH = RAW_ROOT / "raw_file_duplicate_audit.csv"

SKIP_FILES = {
    "ingest_manifest.csv",
    "failed_downloads.csv",
    "duplicate_candidates.csv",
    "raw_file_inventory.csv",
    "raw_file_duplicate_audit.csv",
}

INVENTORY_FIELDS = [
    "relative_path",
    "bytes",
    "sha256",
    "suffix",
    "parent_dir",
    "audited_at",
]

DUPLICATE_FIELDS = [
    "duplicate_path",
    "canonical_path",
    "match_type",
    "sha256",
    "bytes",
    "notes_ko",
]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace("\\", "/")


def collect_inventory() -> list[dict[str, str]]:
    audited_at = datetime.now().isoformat(timespec="seconds")
    files = [
        path
        for path in RAW_ROOT.rglob("*")
        if path.is_file() and path.name not in SKIP_FILES
    ]

    rows: list[dict[str, str]] = []
    for path in sorted(files):
        rows.append(
            {
                "relative_path": rel(path),
                "bytes": str(path.stat().st_size),
                "sha256": sha256_file(path),
                "suffix": path.suffix.lower(),
                "parent_dir": path.parent.name,
                "audited_at": audited_at,
            }
        )
    return rows


def write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def duplicate_rows(inventory: list[dict[str, str]]) -> list[dict[str, str]]:
    by_hash: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in inventory:
        by_hash[row["sha256"]].append(row)

    duplicates: list[dict[str, str]] = []
    for digest, rows in sorted(by_hash.items()):
        if len(rows) < 2:
            continue
        canonical = rows[0]
        for row in rows[1:]:
            duplicates.append(
                {
                    "duplicate_path": row["relative_path"],
                    "canonical_path": canonical["relative_path"],
                    "match_type": "same_sha256",
                    "sha256": digest,
                    "bytes": row["bytes"],
                    "notes_ko": "raw_ingest 내부에서 SHA256이 같은 파일이다. 스모크/본수집 파일명 차이일 수 있으므로 자동 삭제하지 않고 감사표에만 기록한다.",
                }
            )
    return duplicates


def main() -> None:
    inventory = collect_inventory()
    duplicates = duplicate_rows(inventory)
    write_csv(INVENTORY_PATH, INVENTORY_FIELDS, inventory)
    write_csv(DUPLICATE_PATH, DUPLICATE_FIELDS, duplicates)
    print(
        {
            "raw_files": len(inventory),
            "duplicate_rows": len(duplicates),
            "inventory": str(INVENTORY_PATH),
            "duplicates": str(DUPLICATE_PATH),
        }
    )


if __name__ == "__main__":
    main()
