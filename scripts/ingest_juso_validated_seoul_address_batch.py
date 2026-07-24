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

from ingest_common import RAW_ROOT, http_get, latest_raw_path, log_failure, parse_key_file, redact_url, run_date, run_id, write_raw


RUN_DATE = run_date()
PROVIDER = "Juso"
SOURCE_ID = "juso_address_normalization"


def parse_json(body: bytes) -> dict[str, Any]:
    return json.loads(body.decode("utf-8-sig", errors="replace"))


def load_seoul_districts() -> list[str]:
    try:
        spatial_codes = latest_raw_path("sgis", "spatial_codes", required_glob="*_addr_stage_seoul_sgg.json")
        files = sorted(spatial_codes.glob("*_addr_stage_seoul_sgg.json"))
    except FileNotFoundError:
        files = []
    if files:
        data = json.loads(files[-1].read_text(encoding="utf-8"))
        rows = data.get("result", [])
        districts = [str(row.get("addr_name", "")).strip() for row in rows if isinstance(row, dict)]
        if districts:
            return districts
    return [
        "종로구",
        "중구",
        "용산구",
        "성동구",
        "광진구",
        "동대문구",
        "중랑구",
        "성북구",
        "강북구",
        "도봉구",
        "노원구",
        "은평구",
        "서대문구",
        "마포구",
        "양천구",
        "강서구",
        "구로구",
        "금천구",
        "영등포구",
        "동작구",
        "관악구",
        "서초구",
        "강남구",
        "송파구",
        "강동구",
    ]


def find_address_sources() -> list[Path]:
    paths: list[Path] = []
    required_sets = [
        {"자치구", "도로명주소"},
        {"구 명칭", "도로명주소"},
    ]
    for path in Path("datacorpus").glob("*.csv"):
        header: set[str] = set()
        for enc in ["utf-8-sig", "cp949", "euc-kr"]:
            try:
                with path.open("r", encoding=enc, newline="") as f:
                    reader = csv.DictReader(f)
                    header = set(reader.fieldnames or [])
                break
            except Exception:
                continue
        if any(req.issubset(header) for req in required_sets):
            paths.append(path)
    return paths


def iter_address_candidates(districts: set[str]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for path in find_address_sources():
        rows: list[dict[str, str]] = []
        for enc in ["utf-8-sig", "cp949", "euc-kr"]:
            try:
                with path.open("r", encoding=enc, newline="") as f:
                    rows = list(csv.DictReader(f))
                break
            except Exception:
                continue
        for row in rows:
            district = (row.get("자치구") or row.get("구 명칭") or "").strip()
            address = (row.get("도로명주소") or "").strip()
            if district not in districts or not address or not re.search(r"\d", address):
                continue
            if (district, address) in seen:
                continue
            seen.add((district, address))
            candidates.append(
                {
                    "district": district,
                    "address": address,
                    "name": row.get("와이파이명") or row.get("건물명") or row.get("관리번호") or row.get("연번") or "",
                    "x": row.get("X좌표") or row.get("x 좌표") or "",
                    "y": row.get("Y좌표") or row.get("y 좌표") or "",
                    "source_file": str(path),
                }
            )
    return candidates


def call_juso(key: str, address: str) -> tuple[int, bytes, dict[str, Any], str]:
    params = {
        "confmKey": key,
        "currentPage": "1",
        "countPerPage": "10",
        "keyword": address,
        "resultType": "json",
    }
    url = "https://business.juso.go.kr/addrlink/addrLinkApi.do?" + urllib.parse.urlencode(params)
    status, body, _headers = http_get(url, timeout=30)
    return status, body, params, url


def is_accepted(data: dict[str, Any], district: str) -> tuple[bool, str]:
    common = data.get("results", {}).get("common", {})
    error_code = str(common.get("errorCode", ""))
    if error_code != "0":
        return False, f"errorCode={error_code}"
    try:
        total_count = int(common.get("totalCount", 0))
    except Exception:
        total_count = 0
    if total_count <= 0:
        return False, "totalCount=0"
    if total_count > 20:
        return False, f"too_many_results={total_count}"
    rows = data.get("results", {}).get("juso", [])
    for row in rows:
        if row.get("siNm") == "서울특별시" and row.get("sggNm") == district:
            return True, f"accepted_totalCount={total_count}"
    return False, f"no_matching_seoul_district_totalCount={total_count}"


def write_accepted(rid: str, idx: int, key: str, item: dict[str, Any], status: int, body: bytes, params: dict[str, Any], url: str, reason: str) -> str:
    data = parse_json(body)
    common = data.get("results", {}).get("common", {})
    district_index = f"{idx:02d}"
    path = write_raw(
        run_id_value=rid,
        source_id=SOURCE_ID,
        provider=PROVIDER,
        dataset_name=f"Juso 검증형 도로명주소 정규화 서울 {item['district']} 대표주소",
        body=body,
        relative_path=f"{RUN_DATE}/juso/address_normalization_validated/{rid}_{district_index}.json",
        request_url_redacted=redact_url(url),
        request_params={**params, "confmKey": "<redacted>"},
        http_status=status,
        provider_result_code=str(common.get("errorCode", "")),
        provider_result_message=f"{common.get('errorMessage', '')}; {reason}",
        spatial_unit="도로명주소",
        time_unit="실행시점",
        source_period="2026-07-03",
        area_code_type="admCd+rnMgtSn+bdMgtSn",
        quality_notes_ko=(
            f"입력 주소가 Juso 응답에서 서울특별시 {item['district']}로 확인된 경우만 저장했다. "
            f"입력 주소={item['address']}, 입력 좌표=({item.get('x')}, {item.get('y')}), 원천파일={item['source_file']}"
        ),
    )
    return str(path)


def main() -> int:
    rid = run_id("juso_validated_address")
    keys = parse_key_file()
    districts = load_seoul_districts()
    candidates = iter_address_candidates(set(districts))
    selected: dict[str, dict[str, Any]] = {}
    attempts: dict[str, int] = {district: 0 for district in districts}
    rejects: dict[str, list[str]] = {district: [] for district in districts}
    summary: dict[str, Any] = {
        "run_id": rid,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "target_districts": districts,
        "candidate_count": len(candidates),
        "success": 0,
        "missing": [],
        "results": [],
        "decision_ko": "기존 공공와이파이 단순 첫 행 배치는 일부 비서울/광범위 주소가 섞여 품질이 낮았다. 이번 배치는 Juso 응답에서 서울특별시와 동일 자치구가 확인되고 검색결과가 과도하게 넓지 않은 경우만 저장한다.",
    }

    for item in candidates:
        district = item["district"]
        if district in selected:
            continue
        attempts[district] += 1
        try:
            status, body, params, url = call_juso(keys["juso_key"], item["address"])
            data = parse_json(body)
            accepted, reason = is_accepted(data, district)
            if accepted:
                path = write_accepted(rid, len(selected) + 1, keys["juso_key"], item, status, body, params, url, reason)
                selected[district] = {**item, "raw_path": path, "reason": reason}
                summary["results"].append({"district": district, "address": item["address"], "status": "success", "reason": reason, "raw_path": path})
            else:
                if len(rejects[district]) < 5:
                    rejects[district].append(f"{item['address']} => {reason}")
        except Exception as exc:
            if len(rejects[district]) < 5:
                rejects[district].append(f"{item['address']} => {type(exc).__name__}: {exc}")
        if len(selected) == len(districts):
            break
        time.sleep(0.03)

    summary["success"] = len(selected)
    missing = [district for district in districts if district not in selected]
    summary["missing"] = missing
    summary["attempts"] = attempts
    summary["reject_samples"] = rejects

    for district in missing:
        log_failure(
            run_id_value=rid,
            source_id=SOURCE_ID,
            provider=PROVIDER,
            dataset_name=f"Juso 검증형 도로명주소 정규화 서울 {district} 대표주소",
            failure_type="NoValidatedAddress",
            failure_reason_ko=f"후보 주소 중 Juso 응답이 서울특별시 {district}로 명확히 확인되는 대표주소를 찾지 못했다.",
            next_action_ko="해당 자치구의 공식 시설/청사 도로명주소 원천을 추가 확보한 뒤 재수집한다.",
            request_url_redacted="",
        )

    log_path = RAW_ROOT / "run_logs" / f"{rid}.json"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not missing else 2


if __name__ == "__main__":
    sys.exit(main())
