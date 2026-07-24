from __future__ import annotations

import csv
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path
from xml.etree import ElementTree as ET

from ingest_common import KEY_FILE


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "datacorpus"
OUT_CSV = DATA_DIR / "국토교통부_상업업무용_실거래_서울_202501_202605.csv"
OUT_LOG = DATA_DIR / "_processed" / "국토교통부_상업업무용_실거래_API수집로그.json"

SEOUL_LAWD_CODES = [
    "11110", "11140", "11170", "11200", "11215", "11230", "11260", "11290", "11305", "11320",
    "11350", "11380", "11410", "11440", "11470", "11500", "11530", "11545", "11560", "11590",
    "11620", "11650", "11680", "11710", "11740",
]


def read_key_block() -> tuple[str, str]:
    text = KEY_FILE.read_text(encoding="utf-8")
    endpoint_match = re.search(r"endpoint:\s*(https://apis\.data\.go\.kr/1613000/RTMSDataSvcNrgTrade)", text)
    key_match = re.search(r"https://www\.data\.go\.kr/data/15126463/openapi\.do[\s\S]*?key:\s*(\S+)", text)
    if not endpoint_match or not key_match:
        raise RuntimeError("key.md에서 국토부 실거래 endpoint/key를 찾지 못했습니다.")
    return endpoint_match.group(1), key_match.group(1)


def request_xml(url: str) -> ET.Element:
    with urllib.request.urlopen(url, timeout=30) as response:
        body = response.read()
    return ET.fromstring(body)


def item_to_dict(item: ET.Element) -> dict:
    return {child.tag: (child.text or "").strip() for child in list(item)}


def fetch_month(endpoint: str, service_key: str, lawd_cd: str, deal_ymd: str) -> list[dict]:
    rows = []
    page = 1
    while True:
        params = {
            "serviceKey": service_key,
            "LAWD_CD": lawd_cd,
            "DEAL_YMD": deal_ymd,
            "pageNo": page,
            "numOfRows": 1000,
        }
        url = endpoint.rstrip("/") + "/getRTMSDataSvcNrgTrade?" + urllib.parse.urlencode(params, safe="%")
        root = request_xml(url)
        result_code = root.findtext(".//resultCode", default="")
        if result_code and result_code not in {"00", "000"}:
            message = root.findtext(".//resultMsg", default="")
            raise RuntimeError(f"{lawd_cd} {deal_ymd} API 오류: {result_code} {message}")

        items = [item_to_dict(item) for item in root.findall(".//item")]
        for row in items:
            row["LAWD_CD"] = lawd_cd
            row["DEAL_YMD"] = deal_ymd
        rows.extend(items)

        total_count = int(root.findtext(".//totalCount", default=str(len(rows))) or 0)
        if page * 1000 >= total_count or not items:
            break
        page += 1
        time.sleep(0.05)
    return rows


def main() -> None:
    endpoint, service_key = read_key_block()
    months = [f"{year}{month:02d}" for year in [2025, 2026] for month in range(1, 13)]
    months = [m for m in months if "202501" <= m <= "202605"]

    all_rows = []
    errors = []
    for deal_ymd in months:
        for lawd_cd in SEOUL_LAWD_CODES:
            try:
                all_rows.extend(fetch_month(endpoint, service_key, lawd_cd, deal_ymd))
            except Exception as exc:
                errors.append({"DEAL_YMD": deal_ymd, "LAWD_CD": lawd_cd, "error": str(exc)})
            time.sleep(0.05)

    fieldnames = sorted({key for row in all_rows for key in row})
    with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)

    log = {
        "endpoint": endpoint,
        "period": "202501-202605",
        "district_count": len(SEOUL_LAWD_CODES),
        "row_count": len(all_rows),
        "error_count": len(errors),
        "errors_sample": errors[:20],
        "output": str(OUT_CSV.relative_to(ROOT)),
    }
    OUT_LOG.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(log, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
