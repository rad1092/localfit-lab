from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from ingest_common import RAW_ROOT, run_id


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "seoul_store_trade_area"
SERVICE = "VwsmTrdarStorQq"


def main() -> None:
    rid = run_id("store_trade_area_api_manifest_audit")
    manifest_path = RAW_ROOT / "ingest_manifest.csv"
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    with manifest_path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("source_id") == SOURCE_ID and SERVICE in row.get("raw_path", ""):
                groups[row["raw_path"]].append(row)

    duplicate_rows: list[dict[str, Any]] = []
    canonical_count = 0
    manifest_records = 0
    for raw_path, rows in sorted(groups.items()):
        manifest_records += len(rows)
        # Keep the latest record for interpretation, but do not edit the append-only manifest.
        sorted_rows = sorted(rows, key=lambda r: r.get("collected_at", ""))
        for i, row in enumerate(sorted_rows):
            duplicate_rows.append(
                {
                    "raw_path": raw_path,
                    "manifest_records_for_path": len(rows),
                    "canonical_record": "Y" if i == len(sorted_rows) - 1 else "N",
                    "run_id": row.get("run_id", ""),
                    "collection_status": row.get("collection_status", ""),
                    "bytes": row.get("bytes", ""),
                    "sha256": row.get("sha256", ""),
                    "collected_at": row.get("collected_at", ""),
                    "notes_ko": "동일 raw_path가 여러 run에서 기록되었다. 실제 파일은 1개이며 최신 manifest 기록을 해석 기준으로 둔다."
                    if len(rows) > 1
                    else "동일 raw_path의 manifest 기록이 1개뿐이다.",
                }
            )
        canonical_count += 1

    out = RAW_ROOT / "store_trade_area_api_manifest_audit.csv"
    fields = [
        "raw_path",
        "manifest_records_for_path",
        "canonical_record",
        "run_id",
        "collection_status",
        "bytes",
        "sha256",
        "collected_at",
        "notes_ko",
    ]
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in duplicate_rows:
            writer.writerow(row)

    duplicate_path_count = sum(1 for rows in groups.values() if len(rows) > 1)
    duplicate_manifest_records = sum(len(rows) for rows in groups.values() if len(rows) > 1)
    summary = {
        "run_id": rid,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "manifest_records": manifest_records,
        "unique_raw_paths": canonical_count,
        "duplicate_raw_path_count": duplicate_path_count,
        "duplicate_manifest_records": duplicate_manifest_records,
        "audit_csv": str(out.relative_to(ROOT)),
    }
    (RAW_ROOT / "run_logs" / f"{rid}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    log = RAW_ROOT / "run_logs" / "20260703_store_trade_area_api_manifest_audit_ko.md"
    log.write_text(
        "\n".join(
            [
                "# 2026-07-03 점포-상권 API manifest 중복 감사",
                "",
                f"- 실행 ID: `{rid}`",
                "- 대상: 서울 상권분석서비스 점포-상권 `VwsmTrdarStorQq` 원응답 manifest 기록",
                f"- manifest 기록 수: {manifest_records:,}",
                f"- 실제 unique raw_path 수: {canonical_count:,}",
                f"- 중복 raw_path 수: {duplicate_path_count:,}",
                f"- 중복 raw_path에 속한 manifest 기록 수: {duplicate_manifest_records:,}",
                "",
                "## 판단",
                "- 장시간 store-only 수집이 시간 제한으로 중단된 뒤 resume 수집을 수행했기 때문에 일부 `raw_path`가 manifest에 두 번 이상 기록되었다.",
                "- 실제 파일 시스템 기준 원응답 파일은 `datacorpus/_raw_ingest/20260703/seoul_open_data/full/VwsmTrdarStorQq/` 아래 1,605개이며, 행 수 합계는 1,604,844행이다.",
                "- manifest는 이력 보존용으로 삭제하지 않는다. 해석과 후처리에서는 `store_trade_area_api_manifest_audit.csv`의 `canonical_record=Y` 또는 실제 파일 inventory를 기준으로 사용한다.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
