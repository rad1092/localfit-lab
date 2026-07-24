from __future__ import annotations

import csv
import hashlib
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RAW_ROOT = ROOT / "datacorpus" / "_raw_ingest"
RUN_DATE = "20260703"
CANDIDATES = RAW_ROOT / RUN_DATE / "metadata" / "existing_datacorpus_candidates.csv"
DUPLICATES = RAW_ROOT / "duplicate_candidates.csv"

FIELDS = ["candidate_path", "existing_path", "match_type", "sha256", "bytes", "notes_ko"]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_candidates() -> list[dict]:
    with CANDIDATES.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_duplicates(rows: list[dict]) -> None:
    DUPLICATES.parent.mkdir(parents=True, exist_ok=True)
    with DUPLICATES.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in FIELDS})


def main() -> None:
    candidates = read_candidates()
    by_size: dict[int, list[dict]] = defaultdict(list)
    for row in candidates:
        try:
            size = int(row.get("bytes") or 0)
        except ValueError:
            continue
        if size > 0:
            by_size[size].append(row)

    digest_cache: dict[str, str] = {}
    duplicates: list[dict] = []
    for size, rows in by_size.items():
        if len(rows) < 2:
            continue
        by_hash: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            path = ROOT / row["candidate_path"]
            if not path.exists() or not path.is_file():
                continue
            key = str(path)
            if key not in digest_cache:
                digest_cache[key] = sha256_file(path)
            by_hash[digest_cache[key]].append(row)
        for digest, same_rows in by_hash.items():
            if len(same_rows) < 2:
                continue
            first = same_rows[0]
            for row in same_rows[1:]:
                duplicates.append(
                    {
                        "candidate_path": row["candidate_path"],
                        "existing_path": first["candidate_path"],
                        "match_type": "same_size_and_sha256",
                        "sha256": digest,
                        "bytes": size,
                        "notes_ko": "기존 datacorpus 후보 목록 안에서 파일 크기와 SHA256이 동일해 중복 원본/추출본 후보로 본다. 삭제하지 않고 기록만 한다.",
                    }
                )

    write_duplicates(duplicates)
    print(f"duplicate_rows={len(duplicates)}")


if __name__ == "__main__":
    main()
