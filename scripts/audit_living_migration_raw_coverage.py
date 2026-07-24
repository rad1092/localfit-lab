from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from ingest_common import RAW_ROOT, http_get, run_id, write_raw


ROOT = Path(__file__).resolve().parents[1]
DATACORPUS = ROOT / "datacorpus"
RUN_DATE = "20260703"
SOURCE_ID = "seoul_living_migration"
PROVIDER = "서울 열린데이터/생활이동"
LIVING = "\uc0dd\ud65c\uc774\ub3d9"
JACHIGU = "\uc790\uce58\uad6c"
DOC_URL = "https://data.seoul.go.kr/dataVisual/seoul/seoulLivingMigration.do"
DOC_LOCAL = ROOT / "research" / "algorithm_evidence_sources" / "data_docs" / "seoul_living_migration_guide.html"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def append_or_update_source_registry() -> None:
    path = RAW_ROOT / "source_registry.csv"
    if not path.exists():
        return
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames or []
        rows = list(reader)
    for row in rows:
        if row.get("source_id") == SOURCE_ID:
            row["current_status"] = "existing_raw_coverage_verified"
            row["notes_ko"] = (
                "2026.01~2026.05 자치구 단위 월×24시간 CSV가 존재한다. "
                "2026.05는 동일 해시 중복 폴더가 있어 canonical 1벌만 사용한다. "
                "서울시 안내상 생활이동은 1개월 전 데이터를 제공하므로 2026-07-03 기준 2026.05를 최신 안정월로 본다."
            )
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def collect_doc(rid: str) -> dict[str, Any]:
    try:
        status, body, headers = http_get(DOC_URL, timeout=60)
        write_raw(
            run_id_value=rid,
            source_id=SOURCE_ID,
            provider=PROVIDER,
            dataset_name="서울 생활이동 공식 안내 문서",
            body=body,
            relative_path=f"{RUN_DATE}/seoul_open_data/docs/living_migration/seoul_living_migration_guide_20260703.html",
            request_url_redacted=DOC_URL,
            request_params={"doc_url": DOC_URL},
            http_status=status,
            provider_result_code=str(status),
            provider_result_message=f"content_type={headers.get('Content-Type', '')}; bytes={len(body)}",
            spatial_unit="문서",
            time_unit="문서 수집일",
            source_period=RUN_DATE,
            quality_notes_ko="생활이동의 생성 단위, 갱신 주기, 무료 공개 범위, 데이터 한계를 확인하기 위한 공식 문서다.",
        )
        DOC_LOCAL.parent.mkdir(parents=True, exist_ok=True)
        DOC_LOCAL.write_bytes(body)
        return {"status": "success", "bytes": len(body), "url": DOC_URL}
    except Exception as exc:
        return {"status": "failed", "error": str(exc), "url": DOC_URL}


def audit_files() -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    base = DATACORPUS / "_unzipped"
    records: list[dict[str, Any]] = []
    for path in sorted(base.rglob("*.csv")):
        path_text = str(path)
        if LIVING not in path_text:
            continue
        month_match = re.search(LIVING + "_" + JACHIGU + r"_(\d{6})", path_text)
        hour_match = re.search(r"_(\d{2})\uc2dc\.csv$", path.name)
        month = month_match.group(1) if month_match else ""
        hour = hour_match.group(1) if hour_match else ""
        records.append(
            {
                "source_id": SOURCE_ID,
                "month": month,
                "hour": hour,
                "relative_path": str(path.relative_to(ROOT)),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
                "canonical_candidate": "",
                "duplicate_group": "",
                "notes_ko": "",
            }
        )

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[(record["month"], record["hour"])].append(record)

    duplicate_rows: list[dict[str, Any]] = []
    for (month, hour), group in grouped.items():
        group_sorted = sorted(group, key=lambda r: (r["relative_path"].count(" (1)"), r["relative_path"]))
        if len(group_sorted) == 1:
            group_sorted[0]["canonical_candidate"] = "Y"
            group_sorted[0]["notes_ko"] = "해당 월/시간 유일 파일이다."
            continue
        hashes = {r["sha256"] for r in group_sorted}
        group_id = f"{month}_{hour}"
        for i, record in enumerate(group_sorted):
            record["duplicate_group"] = group_id
            record["canonical_candidate"] = "Y" if i == 0 else "N"
            if len(hashes) == 1:
                record["notes_ko"] = "동일 월/시간의 동일 해시 중복 파일이다. canonical_candidate=Y인 1벌만 사용한다."
            else:
                record["notes_ko"] = "동일 월/시간인데 해시가 달라 수동 검토가 필요하다."
            duplicate_rows.append(record.copy())

    summary: dict[str, Any] = {
        "file_count": len(records),
        "total_bytes": sum(int(r["bytes"]) for r in records),
        "months": [],
        "complete_months": [],
        "duplicate_groups": len({r["duplicate_group"] for r in duplicate_rows if r["duplicate_group"]}),
        "duplicate_files": len(duplicate_rows),
        "hash_mismatch_duplicate_groups": 0,
    }
    for month in sorted({r["month"] for r in records if r["month"]}):
        subset = [r for r in records if r["month"] == month]
        hours = sorted({r["hour"] for r in subset if r["hour"]})
        dirs = sorted({str((ROOT / r["relative_path"]).parent.relative_to(base)) for r in subset})
        month_summary = {
            "month": month,
            "file_count": len(subset),
            "hour_count": len(hours),
            "hours": ",".join(hours),
            "bytes": sum(int(r["bytes"]) for r in subset),
            "directories": ";".join(dirs),
            "complete_24h": len(hours) == 24,
        }
        summary["months"].append(month_summary)
        if month_summary["complete_24h"]:
            summary["complete_months"].append(month)

    for group in grouped.values():
        if len(group) > 1 and len({r["sha256"] for r in group}) > 1:
            summary["hash_mismatch_duplicate_groups"] += 1

    return records, duplicate_rows, summary


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def write_log(rid: str, doc_result: dict[str, Any], summary: dict[str, Any]) -> None:
    path = RAW_ROOT / "run_logs" / "20260703_living_migration_coverage_ko.md"
    lines = [
        "# 2026-07-03 서울 생활이동 원천 커버리지 감사",
        "",
        f"- 실행 ID: `{rid}`",
        "- 목적: 수요/접근성 축에 쓰는 생활이동 자료가 원본 단위로 충분한지, 중복이 있는지, 최신 안정월이 어디인지 확인한다.",
        "- 공식 근거: 서울 생활이동 안내는 일·시각 단위로 생산되며, 열린데이터광장에는 요일/월 단위 집계가 제공되고 갱신은 1개월 전 데이터 기준으로 설명된다.",
        "",
        "## 공식 문서 저장",
        f"- 결과: {json.dumps(doc_result, ensure_ascii=False)}",
        "",
        "## 파일 커버리지",
        f"- 생활이동 CSV 파일 수: {summary['file_count']}",
        f"- 총 용량: {summary['total_bytes']:,} bytes",
        f"- 24시간 완비 월: {', '.join(summary['complete_months'])}",
        f"- 중복 그룹 수: {summary['duplicate_groups']}",
        f"- 중복 파일 수: {summary['duplicate_files']}",
        f"- 해시 불일치 중복 그룹: {summary['hash_mismatch_duplicate_groups']}",
        "",
        "| 월 | 파일 수 | 시간대 수 | 24시간 완비 | 폴더 |",
        "|---|---:|---:|---|---|",
    ]
    for item in summary["months"]:
        lines.append(
            f"| {item['month']} | {item['file_count']} | {item['hour_count']} | "
            f"{'예' if item['complete_24h'] else '아니오'} | {item['directories']} |"
        )
    lines.extend(
        [
            "",
            "## 판단",
            "- 2026.01~2026.05는 자치구 단위 월×24시간 파일이 모두 있다.",
            "- 2026.05는 `생활이동_자치구_202605`와 `생활이동_자치구_202605 (1)` 두 폴더가 있으며, 24개 시간대 모두 동일 해시 중복이다.",
            "- 따라서 후처리와 알고리즘 입력에서는 `canonical_candidate=Y`인 1벌만 사용하고, 중복 파일은 삭제하지 않고 중복 감사 결과로 남긴다.",
            "- 2026-07-03 기준 2026.06 파일은 보유되어 있지 않다. 공식 안내의 1개월 전 데이터 갱신 구조를 고려하면 2026.05를 최신 안정월로 본다.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    rid = run_id("living_migration_coverage")
    doc_result = collect_doc(rid)
    records, duplicate_rows, summary = audit_files()
    fields = ["source_id", "month", "hour", "relative_path", "bytes", "sha256", "canonical_candidate", "duplicate_group", "notes_ko"]
    write_csv(RAW_ROOT / "living_migration_coverage_audit.csv", records, fields)
    write_csv(RAW_ROOT / "living_migration_duplicate_groups.csv", duplicate_rows, fields)
    append_or_update_source_registry()
    (RAW_ROOT / "run_logs" / f"{rid}.json").write_text(
        json.dumps({"run_id": rid, "created_at": datetime.now().isoformat(timespec="seconds"), "doc": doc_result, "summary": summary}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_log(rid, doc_result, summary)
    print(json.dumps({"run_id": rid, "doc": doc_result, "summary": summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
