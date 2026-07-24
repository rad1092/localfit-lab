from __future__ import annotations

import concurrent.futures
import json
import math
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from ingest_common import RAW_ROOT, parse_key_file, redact_url, run_id, write_raw, log_failure
from ingest_seoul_transport_accessibility_sources import PROVIDER, RUN_DATE, fetch_api_with_retries, seoul_api_url


SERVICE = "VwsmTrdarStorQq"
SOURCE_ID = "seoul_store_trade_area"
DATASET_NAME = "서울 상권분석서비스 점포-상권 전체 원응답"
OUT_DIR = RAW_ROOT / RUN_DATE / "seoul_open_data" / "full" / SERVICE
PAGE_SIZE = 1000


def parse_existing_pages() -> tuple[dict[tuple[int, int], dict[str, Any]], int]:
    pages: dict[tuple[int, int], dict[str, Any]] = {}
    totals: list[int] = []
    if not OUT_DIR.exists():
        return pages, 0
    for path in OUT_DIR.glob(f"{SERVICE}_*.json"):
        m = re.search(r"_(\d+)_(\d+)\.json$", path.name)
        if not m:
            continue
        start, end = int(m.group(1)), int(m.group(2))
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            payload = data.get(SERVICE, {})
            rows = payload.get("row") or []
            total = int(payload.get("list_total_count") or 0)
            if total:
                totals.append(total)
            expected_rows = end - start + 1
            if rows and len(rows) == min(expected_rows, len(rows)):
                pages[(start, end)] = {"path": path, "rows": len(rows), "total": total}
        except Exception:
            continue
    return pages, max(totals or [0])


def fetch_page(key: str, start: int, end: int) -> dict[str, Any]:
    url = seoul_api_url(key, SERVICE, start, end)
    redacted = redact_url(url, extra_values=[key])
    status, body, code, msg, total, rows = fetch_api_with_retries(url, SERVICE, attempts=4)
    if code != "INFO-000":
        raise RuntimeError(f"{code}: {msg}")
    return {
        "start": start,
        "end": end,
        "status": status,
        "body": body,
        "code": code,
        "message": msg,
        "total": total,
        "rows": len(rows),
        "redacted": redacted,
    }


def write_page(rid: str, item: dict[str, Any]) -> None:
    write_raw(
        run_id_value=rid,
        source_id=SOURCE_ID,
        provider=PROVIDER,
        dataset_name=DATASET_NAME,
        body=item["body"],
        relative_path=f"{RUN_DATE}/seoul_open_data/full/{SERVICE}/{SERVICE}_{item['start']}_{item['end']}.json",
        request_url_redacted=item["redacted"],
        request_params={"service": SERVICE, "start": item["start"], "end": item["end"], "key": "<redacted>"},
        http_status=item["status"],
        provider_result_code=item["code"],
        provider_result_message=item["message"],
        spatial_unit="상권",
        time_unit="분기/연",
        source_period=RUN_DATE,
        area_code_type="상권코드+서비스업종코드",
        quality_notes_ko=f"서울 상권분석서비스 점포-상권 원응답 resume 수집분이다. list_total_count={item['total']}, page_rows={item['rows']}.",
    )


def write_log(rid: str, summary: dict[str, Any]) -> None:
    path = RAW_ROOT / "run_logs" / "20260703_seoul_store_trade_area_resume_ko.md"
    lines = [
        "# 2026-07-03 서울 점포-상권 API resume 수집 기록",
        "",
        f"- 실행 ID: `{rid}`",
        "- 범위: 서울 열린데이터광장 `VwsmTrdarStorQq`, 서울 상권분석서비스 점포-상권만 수집한다.",
        "- 목적: 기존 CSV 등록본과 별개로 서울 OpenAPI 원응답 전체를 재현 가능하게 보존한다.",
        "",
        "## 결과",
        f"- 전체 기준 행 수: {summary['total_count']:,}",
        f"- 예상 페이지 수: {summary['expected_pages']:,}",
        f"- 실행 전 보유 페이지 수: {summary['existing_before']:,}",
        f"- 이번 실행 수집 페이지 수: {summary['fetched_pages']:,}",
        f"- 실행 후 보유 페이지 수: {summary['existing_after']:,}",
        f"- 실행 후 보유 행 수: {summary['rows_after']:,}",
        f"- 실패 페이지 수: {summary['failed_pages']:,}",
        "",
        "## 주의",
        "- 이전 장시간 수집이 시간 제한으로 중단되어 처음부터 재수집하지 않고 누락 페이지 범위만 계산해 이어받았다.",
        "- 서울 상권분석서비스 원천이므로 수집 범위는 서울 상권 코드에 한정된다.",
    ]
    if summary["failed_examples"]:
        lines.append("")
        lines.append("## 실패 예시")
        for item in summary["failed_examples"][:20]:
            lines.append(f"- {item}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    key = parse_key_file()["seoul_key"]
    if not key:
        raise RuntimeError("서울 열린데이터광장 키를 key.md에서 찾지 못했다.")

    rid = run_id("seoul_store_trade_area_resume")
    existing, total = parse_existing_pages()
    if not total:
        first = fetch_page(key, 1, PAGE_SIZE)
        total = int(first["total"])
        write_page(rid, first)
        existing[(1, PAGE_SIZE)] = {"path": OUT_DIR / f"{SERVICE}_1_{PAGE_SIZE}.json", "rows": first["rows"], "total": total}

    expected = [(start, min(start + PAGE_SIZE - 1, total)) for start in range(1, total + 1, PAGE_SIZE)]
    missing = [rng for rng in expected if rng not in existing]

    fetched = 0
    failures: list[str] = []
    # Moderate concurrency: enough to finish, gentle enough to avoid hammering the API.
    batch_size = 60
    max_workers = 4
    for batch_start in range(0, len(missing), batch_size):
        batch = missing[batch_start : batch_start + batch_size]
        results: list[dict[str, Any]] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {executor.submit(fetch_page, key, start, end): (start, end) for start, end in batch}
            for future in concurrent.futures.as_completed(future_map):
                start, end = future_map[future]
                try:
                    results.append(future.result())
                except Exception as exc:
                    failures.append(f"{start}-{end}: {type(exc).__name__}: {exc}")
                    log_failure(
                        run_id_value=rid,
                        source_id=SOURCE_ID,
                        provider=PROVIDER,
                        dataset_name=DATASET_NAME,
                        failure_type=type(exc).__name__,
                        failure_reason_ko=f"{SERVICE} {start}-{end} resume 수집 실패: {exc}",
                        next_action_ko="해당 페이지만 다시 resume 수집한다.",
                        request_url_redacted=redact_url(seoul_api_url(key, SERVICE, start, end), extra_values=[key]),
                    )
        for item in sorted(results, key=lambda x: x["start"]):
            write_page(rid, item)
            fetched += 1
        # Small pause between batches for Seoul OpenAPI stability.
        time.sleep(0.4)

    existing_after, total_after = parse_existing_pages()
    rows_after = sum(int(info["rows"]) for info in existing_after.values())
    summary = {
        "run_id": rid,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "total_count": total_after or total,
        "expected_pages": len(expected),
        "existing_before": len(existing),
        "fetched_pages": fetched,
        "existing_after": len(existing_after),
        "rows_after": rows_after,
        "failed_pages": len(failures),
        "failed_examples": failures[:50],
    }
    (RAW_ROOT / "run_logs" / f"{rid}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_log(rid, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
