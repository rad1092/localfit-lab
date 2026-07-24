from __future__ import annotations

import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
KOSIS_DIR = ROOT / "datacorpus" / "_raw_ingest" / "20260703" / "kosis"
CANDIDATES = KOSIS_DIR / "kosis_candidate_tables_population_business_survival.csv"
OUTPUT = KOSIS_DIR / "kosis_keyword_inspection.csv"

KEYWORDS = [
    "기업생멸",
    "신생기업",
    "소멸기업",
    "생존율",
    "전국사업체",
    "사업체조사",
    "산업세세분류",
    "사업체",
    "종사자",
    "산업별",
    "지역별",
    "읍면동",
    "행정구역",
    "시군구",
]

FIELDS = [
    "keyword",
    "source_file",
    "parent_list_id",
    "org_id",
    "tbl_id",
    "list_id",
    "name",
    "send_de",
]


def inspect_candidate_csv() -> list[dict[str, str]]:
    rows_out: list[dict[str, str]] = []
    with CANDIDATES.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        name = row["table_name"] or row["list_name"]
        for keyword in KEYWORDS:
            if keyword in name:
                rows_out.append(
                    {
                        "keyword": keyword,
                        "source_file": str(CANDIDATES.relative_to(ROOT)),
                        "parent_list_id": row["parent_list_id"],
                        "org_id": row["org_id"],
                        "tbl_id": row["tbl_id"],
                        "list_id": row["list_id"],
                        "name": name,
                        "send_de": row["send_de"],
                    }
                )
    return rows_out


def inspect_raw_lists() -> list[dict[str, str]]:
    rows_out: list[dict[str, str]] = []
    for path in sorted((KOSIS_DIR / "statistics_list").glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict):
            data = [data]
        parent = path.stem
        for item in data:
            name = item.get("TBL_NM") or item.get("LIST_NM") or ""
            for keyword in KEYWORDS:
                if keyword in name:
                    rows_out.append(
                        {
                            "keyword": keyword,
                            "source_file": str(path.relative_to(ROOT)),
                            "parent_list_id": parent,
                            "org_id": item.get("ORG_ID", ""),
                            "tbl_id": item.get("TBL_ID", ""),
                            "list_id": item.get("LIST_ID", ""),
                            "name": name,
                            "send_de": item.get("SEND_DE", ""),
                        }
                    )
    return rows_out


def main() -> None:
    rows = inspect_candidate_csv() + inspect_raw_lists()
    unique: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for row in rows:
        key = (row["keyword"], row["org_id"], row["tbl_id"], row["list_id"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    unique.sort(key=lambda r: (r["keyword"], r["name"], r["org_id"], r["tbl_id"]))
    with OUTPUT.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(unique)
    print({"rows": len(unique), "output": str(OUTPUT)})


if __name__ == "__main__":
    main()
