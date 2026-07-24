from __future__ import annotations

import csv
import json
import os
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "datacorpus"
OUT_DIR = DATA_DIR / "_inventory"
GENERATED_DIRS = {"_inventory", "_processed", "_analysis_outputs"}


def safe_text(value: object) -> str:
    return "" if value is None else str(value).replace("\r", " ").replace("\n", " ").strip()


def read_csv_head(path: Path, sample_rows: int = 5) -> dict:
    # 서울 공공데이터 CSV는 UTF-8-SIG와 CP949가 섞일 수 있어 후보를 순서대로 시도한다.
    encodings = ["utf-8-sig", "cp949", "euc-kr", "utf-8"]
    for encoding in encodings:
        try:
            with path.open("r", encoding=encoding, newline="") as f:
                reader = csv.reader(f)
                header = next(reader, [])
                rows = []
                for _, row in zip(range(sample_rows), reader):
                    rows.append(row)
            return {
                "encoding": encoding,
                "columns": [safe_text(x) for x in header],
                "sample": [[safe_text(x) for x in row] for row in rows],
                "error": "",
            }
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
    return {"encoding": "", "columns": [], "sample": [], "error": last_error}


def read_xlsx_info(path: Path) -> dict:
    # xlsx는 zip 안의 workbook XML만 읽어 시트명만 빠르게 확인한다.
    try:
        with zipfile.ZipFile(path) as zf:
            workbook = ET.fromstring(zf.read("xl/workbook.xml"))
        ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        sheets = [sheet.attrib.get("name", "") for sheet in workbook.findall(".//m:sheet", ns)]
        return {"sheets": sheets, "error": ""}
    except Exception as exc:
        return {"sheets": [], "error": f"{type(exc).__name__}: {exc}"}


def group_shapefiles(files: list[Path]) -> list[dict]:
    groups: dict[Path, set[str]] = defaultdict(set)
    for path in files:
        if path.suffix.lower() in {".shp", ".shx", ".dbf", ".prj", ".cpg", ".qmd"}:
            groups[path.with_suffix("")].add(path.suffix.lower())

    rows = []
    for stem, exts in sorted(groups.items(), key=lambda item: str(item[0])):
        rows.append(
            {
                "dataset": str(stem.relative_to(DATA_DIR)),
                "has_shp": ".shp" in exts,
                "has_shx": ".shx" in exts,
                "has_dbf": ".dbf" in exts,
                "has_prj": ".prj" in exts,
                "has_cpg": ".cpg" in exts,
                "has_qmd": ".qmd" in exts,
                "extensions": " ".join(sorted(exts)),
            }
        )
    return rows


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    files = [p for p in DATA_DIR.rglob("*") if p.is_file() and not any(part in GENERATED_DIRS for part in p.parts)]

    inventory_rows = []
    schema_rows = []
    sample_rows = []
    ext_counter = Counter()
    top_level_counter = Counter()

    for path in sorted(files, key=lambda p: str(p).lower()):
        rel = path.relative_to(DATA_DIR)
        ext = path.suffix.lower()
        ext_counter[ext or "(no_ext)"] += 1
        top_level_counter[rel.parts[0] if rel.parts else "."] += 1

        inventory_rows.append(
            {
                "relative_path": str(rel),
                "name": path.name,
                "extension": ext,
                "size_mb": round(path.stat().st_size / 1024 / 1024, 3),
                "modified_at": path.stat().st_mtime,
            }
        )

        if ext == ".csv":
            info = read_csv_head(path)
            schema_rows.append(
                {
                    "relative_path": str(rel),
                    "encoding": info["encoding"],
                    "column_count": len(info["columns"]),
                    "columns": " | ".join(info["columns"]),
                    "error": info["error"],
                }
            )
            for idx, row in enumerate(info["sample"], start=1):
                sample_rows.append(
                    {
                        "relative_path": str(rel),
                        "sample_no": idx,
                        "sample_values": " | ".join(row[:20]),
                    }
                )

        elif ext == ".xlsx":
            info = read_xlsx_info(path)
            schema_rows.append(
                {
                    "relative_path": str(rel),
                    "encoding": "xlsx",
                    "column_count": "",
                    "columns": " | ".join(info["sheets"]),
                    "error": info["error"],
                }
            )

    shape_rows = group_shapefiles(files)

    write_csv(
        OUT_DIR / "파일_목록.csv",
        inventory_rows,
        ["relative_path", "name", "extension", "size_mb", "modified_at"],
    )
    write_csv(
        OUT_DIR / "스키마_샘플_요약.csv",
        schema_rows,
        ["relative_path", "encoding", "column_count", "columns", "error"],
    )
    write_csv(
        OUT_DIR / "CSV_샘플값.csv",
        sample_rows,
        ["relative_path", "sample_no", "sample_values"],
    )
    write_csv(
        OUT_DIR / "공간데이터_묶음.csv",
        shape_rows,
        ["dataset", "has_shp", "has_shx", "has_dbf", "has_prj", "has_cpg", "has_qmd", "extensions"],
    )

    summary = {
        "data_dir": str(DATA_DIR),
        "file_count": len(files),
        "total_size_gb": round(sum(p.stat().st_size for p in files) / 1024 / 1024 / 1024, 3),
        "extensions": dict(sorted(ext_counter.items(), key=lambda item: (-item[1], item[0]))),
        "top_level_entries": dict(sorted(top_level_counter.items(), key=lambda item: (-item[1], item[0]))),
        "shapefile_group_count": len(shape_rows),
        "outputs": [
            "파일_목록.csv",
            "스키마_샘플_요약.csv",
            "CSV_샘플값.csv",
            "공간데이터_묶음.csv",
            "요약.json",
        ],
    }
    (OUT_DIR / "요약.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
