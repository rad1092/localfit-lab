from __future__ import annotations

import csv
import json
import re
import sys
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any

from ingest_common import RAW_ROOT, http_get, log_failure, parse_key_file, redact_url, run_id, write_raw


RUN_DATE = "20260703"
PROVIDER = "Juso"
SOURCE_ID = "juso_address_normalization"
INPUT_SOURCE_HINT = "서울시 공공와이파이 서비스 위치 정보"


def parse_json(body: bytes) -> dict[str, Any]:
    return json.loads(body.decode("utf-8-sig", errors="replace"))


def safe_name(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z_-]+", "_", value).strip("_")[:80] or "item"


def find_wifi_address_file() -> Path | None:
    required = {"자치구", "도로명주소"}
    for path in Path("datacorpus").glob("*.csv"):
        for enc in ["utf-8-sig", "cp949", "euc-kr"]:
            try:
                with path.open("r", encoding=enc, newline="") as f:
                    reader = csv.DictReader(f)
                    header = set(reader.fieldnames or [])
                break
            except Exception:
                header = set()
        if required.issubset(header) and INPUT_SOURCE_HINT in path.name:
            return path
    for path in Path("datacorpus").glob("*.csv"):
        for enc in ["utf-8-sig", "cp949", "euc-kr"]:
            try:
                with path.open("r", encoding=enc, newline="") as f:
                    reader = csv.DictReader(f)
                    header = set(reader.fieldnames or [])
                break
            except Exception:
                header = set()
        if required.issubset(header):
            return path
    return None


def load_representative_addresses(limit: int = 25) -> list[dict[str, Any]]:
    path = find_wifi_address_file()
    if path is None:
        return []
    rows: list[dict[str, str]] = []
    last_error: Exception | None = None
    for enc in ["utf-8-sig", "cp949", "euc-kr"]:
        try:
            with path.open("r", encoding=enc, newline="") as f:
                rows = list(csv.DictReader(f))
            break
        except Exception as exc:
            last_error = exc
    if not rows and last_error:
        raise last_error

    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        district = (row.get("자치구") or "").strip()
        address = (row.get("도로명주소") or "").strip()
        if not district or district in seen or not address:
            continue
        seen.add(district)
        selected.append(
            {
                "district": district,
                "address": address,
                "name": row.get("와이파이명", ""),
                "x": row.get("X좌표", ""),
                "y": row.get("Y좌표", ""),
                "source_file": str(path),
            }
        )
        if len(selected) >= limit:
            break
    return selected


def collect_one(rid: str, key: str, item: dict[str, Any], idx: int) -> dict[str, Any]:
    params = {
        "confmKey": key,
        "currentPage": "1",
        "countPerPage": "10",
        "keyword": item["address"],
        "resultType": "json",
    }
    url = "https://business.juso.go.kr/addrlink/addrLinkApi.do?" + urllib.parse.urlencode(params)
    status, body, _headers = http_get(url, timeout=30)
    data = parse_json(body)
    common = data.get("results", {}).get("common", {})
    error_code = str(common.get("errorCode", ""))
    error_message = str(common.get("errorMessage", ""))
    total_count = str(common.get("totalCount", ""))
    if error_code != "0":
        raise RuntimeError(f"Juso 오류: {error_code} {error_message}")

    raw_name = f"{idx:02d}_{safe_name(item['district'])}_{safe_name(item.get('name', ''))}"
    path = write_raw(
        run_id_value=rid,
        source_id=SOURCE_ID,
        provider=PROVIDER,
        dataset_name=f"Juso 도로명주소 정규화 서울 {item['district']} 대표주소",
        body=body,
        relative_path=f"{RUN_DATE}/juso/address_normalization/{rid}_{raw_name}.json",
        request_url_redacted=redact_url(url),
        request_params={**params, "confmKey": "<redacted>"},
        http_status=status,
        provider_result_code=error_code,
        provider_result_message=f"{error_message}; totalCount={total_count}",
        spatial_unit="도로명주소",
        time_unit="실행시점",
        source_period="2026-07-03",
        area_code_type="admCd+rnMgtSn+bdMgtSn",
        quality_notes_ko=(
            f"서울 공공와이파이 위치정보의 도로명주소를 Juso 기준으로 정규화한 원응답이다. "
            f"입력 자치구={item['district']}, 입력 주소={item['address']}, 입력 좌표=({item.get('x')}, {item.get('y')}), 원천파일={item['source_file']}"
        ),
    )
    return {
        "district": item["district"],
        "address": item["address"],
        "status": "success",
        "total_count": total_count,
        "raw_path": str(path),
    }


def main() -> int:
    rid = run_id("juso_address_batch")
    keys = parse_key_file()
    items = load_representative_addresses(limit=25)
    summary: dict[str, Any] = {
        "run_id": rid,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "input_count": len(items),
        "success": 0,
        "failed": 0,
        "results": [],
        "vworld_bulk_storage_decision_ko": "VWorld 지오코더는 공식 문서에 별도 저장 금지 문구가 있어 대량 원응답 저장 대상에서 제외하고, 기존 단건 스모크와 문서/주의사항 기록으로만 유지한다.",
    }

    if not items:
        log_failure(
            run_id_value=rid,
            source_id=SOURCE_ID,
            provider=PROVIDER,
            dataset_name="Juso 도로명주소 정규화 서울 대표주소",
            failure_type="NoInputAddress",
            failure_reason_ko="자치구와 도로명주소를 가진 입력 CSV를 찾지 못했다.",
            next_action_ko="주소 컬럼이 있는 서울 공공데이터 원천 파일을 먼저 확보한다.",
            request_url_redacted="",
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 1

    for idx, item in enumerate(items, start=1):
        try:
            result = collect_one(rid, keys["juso_key"], item, idx)
            summary["success"] += 1
            summary["results"].append(result)
        except Exception as exc:
            summary["failed"] += 1
            params = {
                "confmKey": "<redacted>",
                "currentPage": "1",
                "countPerPage": "10",
                "keyword": item.get("address", ""),
                "resultType": "json",
            }
            log_failure(
                run_id_value=rid,
                source_id=SOURCE_ID,
                provider=PROVIDER,
                dataset_name=f"Juso 도로명주소 정규화 서울 {item.get('district', '')} 대표주소",
                failure_type=type(exc).__name__,
                failure_reason_ko=f"Juso 주소 정규화 실패: {exc}",
                next_action_ko="입력 주소의 특수문자, 승인키 상태, addrLinkApi.do 호출 형식을 재확인한다.",
                request_url_redacted="https://business.juso.go.kr/addrlink/addrLinkApi.do?" + urllib.parse.urlencode(params),
            )
            summary["results"].append({"district": item.get("district"), "address": item.get("address"), "status": "failed", "error": repr(exc)})
        time.sleep(0.05)

    log_path = RAW_ROOT / "run_logs" / f"{rid}.json"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["failed"] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
