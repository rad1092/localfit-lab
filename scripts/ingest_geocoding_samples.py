from __future__ import annotations

import json
import urllib.parse
from datetime import datetime

from ingest_common import RAW_ROOT, http_get, log_failure, parse_key_file, redact_url, run_date, run_id, write_raw


RUN_DATE = run_date()
ADDRESS = "\uc11c\uc6b8\ud2b9\ubcc4\uc2dc \uc911\uad6c \uc138\uc885\ub300\ub85c 110"


def parse_json(body: bytes) -> dict:
    return json.loads(body.decode("utf-8-sig", errors="replace"))


def collect_vworld_coord(rid: str, keys: dict[str, str]) -> dict:
    source_id = "vworld_juso_geocoding"
    provider = "VWorld"
    dataset_name = "VWorld 주소→좌표 샘플"
    params = {
        "service": "address",
        "request": "getCoord",
        "version": "2.0",
        "crs": "epsg:4326",
        "address": ADDRESS,
        "format": "json",
        "type": "ROAD",
        "key": keys["vworld_key"],
    }
    url = "https://api.vworld.kr/req/address?" + urllib.parse.urlencode(params)
    redacted = redact_url(url)
    try:
        status, body, _headers = http_get(url, timeout=30)
        data = parse_json(body)
        response = data.get("response", {})
        result_status = str(response.get("status", ""))
        if result_status != "OK":
            raise RuntimeError(f"VWorld 상태 오류: {result_status}")
        point = response.get("result", {}).get("point", {})
        path = write_raw(
            run_id_value=rid,
            source_id=source_id,
            provider=provider,
            dataset_name=dataset_name,
            body=body,
            relative_path=f"{RUN_DATE}/vworld/{rid}_vworld_getcoord_seoul_city_hall.json",
            request_url_redacted=redacted,
            request_params={**params, "key": "<redacted>"},
            http_status=status,
            provider_result_code=result_status,
            provider_result_message="주소 좌표변환 성공",
            spatial_unit="주소→좌표",
            time_unit="실행시점",
            area_code_type="WGS84",
            quality_notes_ko="서울시청 도로명주소를 WGS84 좌표로 변환하는 샘플 원응답이다.",
        )
        return {"name": dataset_name, "status": "success", "path": str(path), "point": point}
    except Exception as exc:
        log_failure(
            run_id_value=rid,
            source_id=source_id,
            provider=provider,
            dataset_name=dataset_name,
            failure_type=type(exc).__name__,
            failure_reason_ko=f"VWorld 주소→좌표 샘플 실패: {exc}",
            next_action_ko="주소 인코딩, type=ROAD/JIBUN, VWorld 키 승인 상태를 확인하고 재시도한다.",
            request_url_redacted=redacted,
        )
        return {"name": dataset_name, "status": "failed", "error": type(exc).__name__}


def collect_juso_search(rid: str, keys: dict[str, str]) -> dict:
    source_id = "vworld_juso_geocoding"
    provider = "Juso"
    dataset_name = "Juso 도로명주소 검색 샘플"
    params = {
        "confmKey": keys["juso_key"],
        "currentPage": "1",
        "countPerPage": "5",
        "keyword": ADDRESS,
        "resultType": "json",
    }
    url = "https://business.juso.go.kr/addrlink/addrLinkApi.do?" + urllib.parse.urlencode(params)
    redacted = redact_url(url)
    try:
        status, body, _headers = http_get(url, timeout=30)
        data = parse_json(body)
        common = data.get("results", {}).get("common", {})
        error_code = str(common.get("errorCode", ""))
        error_msg = str(common.get("errorMessage", ""))
        if error_code != "0":
            raise RuntimeError(f"Juso 오류: {error_code} {error_msg}")
        path = write_raw(
            run_id_value=rid,
            source_id=source_id,
            provider=provider,
            dataset_name=dataset_name,
            body=body,
            relative_path=f"{RUN_DATE}/juso/{rid}_juso_addr_search_seoul_city_hall.json",
            request_url_redacted=redacted,
            request_params={**params, "confmKey": "<redacted>"},
            http_status=status,
            provider_result_code=error_code,
            provider_result_message=error_msg,
            spatial_unit="주소 정규화",
            time_unit="실행시점",
            area_code_type="도로명주소/건물관리번호",
            quality_notes_ko="서울시청 도로명주소 검색 샘플 원응답이다. 주소 정규화와 건물관리번호 확인에 쓴다.",
        )
        return {"name": dataset_name, "status": "success", "path": str(path), "total_count": common.get("totalCount")}
    except Exception as exc:
        log_failure(
            run_id_value=rid,
            source_id=source_id,
            provider=provider,
            dataset_name=dataset_name,
            failure_type=type(exc).__name__,
            failure_reason_ko=f"Juso 도로명주소 검색 샘플 실패: {exc}",
            next_action_ko="승인키, API 엔드포인트, keyword 인코딩을 확인하고 재시도한다.",
            request_url_redacted=redacted,
        )
        return {"name": dataset_name, "status": "failed", "error": type(exc).__name__}


def main() -> None:
    keys = parse_key_file()
    rid = run_id("geocoding_samples")
    results = [collect_vworld_coord(rid, keys), collect_juso_search(rid, keys)]
    summary = {"run_id": rid, "results": results, "created_at": datetime.now().isoformat(timespec="seconds")}
    log_path = RAW_ROOT / "run_logs" / f"{rid}.json"
    log_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
