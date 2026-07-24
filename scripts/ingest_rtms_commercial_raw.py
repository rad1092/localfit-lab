from __future__ import annotations

import json
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

from ingest_common import RAW_ROOT, http_get, log_failure, parse_key_file, redact_url, run_date, run_id, write_raw


RUN_DATE = run_date()
SOURCE_ID = "molit_rtms_commercial_trade"
PROVIDER = "국토교통부/공공데이터포털"
DATASET_NAME = "상업·업무용 부동산 매매 실거래 원응답"

SEOUL_LAWD_CODES = [
    "11110",
    "11140",
    "11170",
    "11200",
    "11215",
    "11230",
    "11260",
    "11290",
    "11305",
    "11320",
    "11350",
    "11380",
    "11410",
    "11440",
    "11470",
    "11500",
    "11530",
    "11545",
    "11560",
    "11590",
    "11620",
    "11650",
    "11680",
    "11710",
    "11740",
]


def months_between(start: str, end: str) -> list[str]:
    sy, sm = int(start[:4]), int(start[4:])
    ey, em = int(end[:4]), int(end[4:])
    out = []
    y, m = sy, sm
    while (y, m) <= (ey, em):
        out.append(f"{y}{m:02d}")
        m += 1
        if m == 13:
            y += 1
            m = 1
    return out


def parse_result(body: bytes) -> tuple[str, str, int]:
    root = ET.fromstring(body)
    result_code = root.findtext(".//resultCode", default="")
    result_msg = root.findtext(".//resultMsg", default="")
    total_count = int(root.findtext(".//totalCount", default="0") or "0")
    return result_code, result_msg, total_count


def main() -> None:
    keys = parse_key_file()
    endpoint = keys["rtms_endpoint"].rstrip("/")
    service_key = keys["rtms_key"]
    rid = run_id("rtms_raw")
    months = months_between("202501", "202606")
    saved = 0
    skipped_existing = 0
    failed = 0
    total_rows_hint = 0

    for deal_ymd in months:
        for lawd_cd in SEOUL_LAWD_CODES:
            page = 1
            while True:
                params = {
                    "serviceKey": service_key,
                    "LAWD_CD": lawd_cd,
                    "DEAL_YMD": deal_ymd,
                    "pageNo": str(page),
                    "numOfRows": "1000",
                }
                url = endpoint + "/getRTMSDataSvcNrgTrade?" + urllib.parse.urlencode(params, safe="%")
                redacted = redact_url(url)
                rel_path = f"{RUN_DATE}/public_data/rtms_nrg_trade_raw/{deal_ymd}/{lawd_cd}_page{page}.xml"
                out_path = RAW_ROOT / rel_path
                if out_path.exists():
                    skipped_existing += 1
                    try:
                        _, _, total_count = parse_result(out_path.read_bytes())
                    except Exception:
                        total_count = 0
                    if page * 1000 >= total_count:
                        break
                    page += 1
                    continue

                try:
                    status, body, _headers = http_get(url, timeout=45)
                    result_code, result_msg, total_count = parse_result(body)
                    total_rows_hint += total_count if page == 1 else 0
                    if result_code and result_code not in {"00", "000"}:
                        raise RuntimeError(f"기관 결과 오류 {result_code}: {result_msg}")
                    write_raw(
                        run_id_value=rid,
                        source_id=SOURCE_ID,
                        provider=PROVIDER,
                        dataset_name=DATASET_NAME,
                        body=body,
                        relative_path=rel_path,
                        request_url_redacted=redacted,
                        request_params={**params, "serviceKey": "<redacted>"},
                        http_status=status,
                        provider_result_code=result_code,
                        provider_result_message=result_msg,
                        spatial_unit="시군구/법정동",
                        time_unit="월/거래",
                        source_period=deal_ymd,
                        area_code_type="LAWD_CD",
                        quality_notes_ko="상업·업무용 실거래 원응답 XML. User-Agent를 명시해 수집했다. 매매 실거래로 임대료 직접값은 아니다.",
                    )
                    saved += 1
                    if page * 1000 >= total_count:
                        break
                    page += 1
                    time.sleep(0.05)
                except Exception as exc:
                    failed += 1
                    log_failure(
                        run_id_value=rid,
                        source_id=SOURCE_ID,
                        provider=PROVIDER,
                        dataset_name=DATASET_NAME,
                        failure_type=type(exc).__name__,
                        failure_reason_ko=f"{deal_ymd} {lawd_cd} page {page} 수집 실패: {exc}",
                        next_action_ko="서비스 응답 코드, User-Agent, 해당 월 공개 여부, 서비스키 인코딩을 확인하고 같은 파라미터로 재시도한다.",
                        request_url_redacted=redacted,
                    )
                    break
                time.sleep(0.05)

    summary = {
        "run_id": rid,
        "months": months,
        "lawd_count": len(SEOUL_LAWD_CODES),
        "saved_xml_pages": saved,
        "skipped_existing_pages": skipped_existing,
        "failed_pages": failed,
        "total_rows_hint_sum_first_pages": total_rows_hint,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    log_path = RAW_ROOT / "run_logs" / f"{rid}.json"
    log_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
