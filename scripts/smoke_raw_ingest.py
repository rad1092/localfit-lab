from __future__ import annotations

import json
import urllib.parse
from pathlib import Path
from xml.etree import ElementTree as ET

from ingest_common import (
    RAW_ROOT,
    http_get,
    log_failure,
    parse_key_file,
    redact_url,
    run_id,
    sanitize_sgis_auth_response,
    write_raw,
)


RUN_DATE = "20260703"


def collect_sgis_auth(run_id_value: str, keys: dict[str, str]) -> dict:
    source_id = "sgis_small_area_stats"
    provider = "SGIS"
    dataset_name = "SGIS 인증 토큰 재발급 스모크"
    params = {
        "consumer_key": keys.get("sgis_service_id", ""),
        "consumer_secret": keys.get("sgis_secret", ""),
    }
    url = "https://sgisapi.mods.go.kr/OpenAPI3/auth/authentication.json?" + urllib.parse.urlencode(params)
    redacted = redact_url(url)
    try:
        status, body, _headers = http_get(url, timeout=30)
        safe_body = sanitize_sgis_auth_response(body)
        result_code = ""
        result_msg = ""
        try:
            data = json.loads(body.decode("utf-8"))
            result_code = str(data.get("errCd", ""))
            result_msg = str(data.get("errMsg", ""))
        except Exception:
            result_msg = "JSON 파싱 실패"
        path = write_raw(
            run_id_value=run_id_value,
            source_id=source_id,
            provider=provider,
            dataset_name=dataset_name,
            body=safe_body,
            relative_path=f"{RUN_DATE}/sgis/{run_id_value}_sgis_auth_smoke_sanitized.json",
            request_url_redacted=redacted,
            request_params={"consumer_key": "<redacted>", "consumer_secret": "<redacted>"},
            http_status=status,
            provider_result_code=result_code,
            provider_result_message=result_msg,
            spatial_unit="인증",
            time_unit="실행시점",
            quality_notes_ko="토큰 값은 원본 저장 전에 제거했다. accessTimeout 존재 여부만 확인한다.",
        )
        return {"source_id": source_id, "status": "success", "path": str(path), "provider_result_code": result_code}
    except Exception as exc:
        log_failure(
            run_id_value=run_id_value,
            source_id=source_id,
            provider=provider,
            dataset_name=dataset_name,
            failure_type=type(exc).__name__,
            failure_reason_ko=f"SGIS 인증 스모크 실패: {exc}",
            next_action_ko="서비스ID와 보안 key 순서를 확인하고 sgisapi.mods.go.kr 호스트로 재시도한다.",
            request_url_redacted=redacted,
        )
        return {"source_id": source_id, "status": "failed", "error": type(exc).__name__}


def collect_rtms_one_page(run_id_value: str, keys: dict[str, str]) -> dict:
    source_id = "molit_rtms_commercial_trade"
    provider = "국토교통부/공공데이터포털"
    dataset_name = "상업·업무용 부동산 매매 실거래 스모크"
    endpoint = keys.get("rtms_endpoint", "").rstrip("/")
    params = {
        "serviceKey": keys.get("rtms_key", ""),
        "LAWD_CD": "11680",
        "DEAL_YMD": "202501",
        "pageNo": "1",
        "numOfRows": "1",
    }
    url = endpoint + "/getRTMSDataSvcNrgTrade?" + urllib.parse.urlencode(params, safe="%")
    redacted = redact_url(url)
    try:
        status, body, _headers = http_get(url, timeout=30)
        result_code = ""
        result_msg = ""
        try:
            root = ET.fromstring(body)
            result_code = root.findtext(".//resultCode", default="")
            result_msg = root.findtext(".//resultMsg", default="")
        except Exception:
            result_msg = "XML 파싱 실패"
        path = write_raw(
            run_id_value=run_id_value,
            source_id=source_id,
            provider=provider,
            dataset_name=dataset_name,
            body=body,
            relative_path=f"{RUN_DATE}/public_data/{run_id_value}_rtms_commercial_trade_11680_202501_page1_rows1.xml",
            request_url_redacted=redacted,
            request_params={**params, "serviceKey": "<redacted>"},
            http_status=status,
            provider_result_code=result_code,
            provider_result_message=result_msg,
            spatial_unit="시군구/법정동",
            time_unit="월",
            source_period="202501",
            area_code_type="LAWD_CD",
            quality_notes_ko="WAF 차단 방지를 위해 Mozilla 계열 User-Agent를 사용했다. 1건 스모크 원본이다.",
        )
        return {"source_id": source_id, "status": "success", "path": str(path), "provider_result_code": result_code}
    except Exception as exc:
        log_failure(
            run_id_value=run_id_value,
            source_id=source_id,
            provider=provider,
            dataset_name=dataset_name,
            failure_type=type(exc).__name__,
            failure_reason_ko=f"RTMS 스모크 실패: {exc}",
            next_action_ko="User-Agent 헤더, 신규 host, 서비스키 인코딩 상태를 확인하고 재시도한다.",
            request_url_redacted=redacted,
        )
        return {"source_id": source_id, "status": "failed", "error": type(exc).__name__}


def collect_seoul_open_data_sample(run_id_value: str, keys: dict[str, str]) -> dict:
    source_id = "seoul_trade_area_boundary"
    provider = "서울열린데이터광장"
    dataset_name = "서울 OpenAPI 서비스목록 스모크"
    key = keys.get("seoul_key", "")
    url = f"http://openapi.seoul.go.kr:8088/{urllib.parse.quote(key)}/json/SearchOpenDataServiceList/1/1/"
    redacted = redact_url(url, extra_values=[key])
    try:
        status, body, _headers = http_get(url, timeout=30)
        result_code = ""
        result_msg = ""
        try:
            data = json.loads(body.decode("utf-8"))
            if "RESULT" in data:
                result_code = str(data["RESULT"].get("CODE", ""))
                result_msg = str(data["RESULT"].get("MESSAGE", ""))
        except Exception:
            try:
                root = ET.fromstring(body)
                result_code = root.findtext(".//CODE", default="")
                result_msg = root.findtext(".//MESSAGE", default="")
            except Exception:
                result_msg = "JSON/XML 파싱 실패"
        if result_code and result_code not in {"INFO-000", "INFO-1000"}:
            raise RuntimeError(f"서울 OpenAPI 결과 오류: {result_code} {result_msg}")
        path = write_raw(
            run_id_value=run_id_value,
            source_id=source_id,
            provider=provider,
            dataset_name=dataset_name,
            body=body,
            relative_path=f"{RUN_DATE}/seoul_open_data/{run_id_value}_search_open_data_service_list_1_1.json",
            request_url_redacted=redacted,
            request_params={"service": "SearchOpenDataServiceList", "start": 1, "end": 1, "key": "<redacted>"},
            http_status=status,
            provider_result_code=result_code,
            provider_result_message=result_msg,
            spatial_unit="서비스목록",
            time_unit="실행시점",
            quality_notes_ko="서울 OpenAPI 키와 기본 응답 구조 확인용 1건 스모크다.",
        )
        return {"source_id": source_id, "status": "success", "path": str(path), "provider_result_code": result_code}
    except Exception as exc:
        log_failure(
            run_id_value=run_id_value,
            source_id=source_id,
            provider=provider,
            dataset_name=dataset_name,
            failure_type=type(exc).__name__,
            failure_reason_ko=f"서울 OpenAPI 스모크 실패: {exc}",
            next_action_ko="키 활성화 여부와 서울 OpenAPI 서비스명을 확인하고 HTTPS/프록시 필요 여부를 검토한다.",
            request_url_redacted=redacted,
        )
        return {"source_id": source_id, "status": "failed", "error": type(exc).__name__}


def collect_kosis_home(run_id_value: str, keys: dict[str, str]) -> dict:
    source_id = "kosis_population_business_survival"
    provider = "KOSIS"
    dataset_name = "KOSIS OpenAPI 홈 스모크"
    url = "https://kosis.kr/openapi/"
    redacted = redact_url(url)
    try:
        status, body, _headers = http_get(url, timeout=30)
        path = write_raw(
            run_id_value=run_id_value,
            source_id=source_id,
            provider=provider,
            dataset_name=dataset_name,
            body=body,
            relative_path=f"{RUN_DATE}/kosis/{run_id_value}_kosis_openapi_home_smoke.html",
            request_url_redacted=redacted,
            request_params={"credential_ref": "KOSIS_API_KEY", "key_present": bool(keys.get("kosis_key"))},
            http_status=status,
            provider_result_message="홈 문서 응답 저장",
            spatial_unit="문서",
            time_unit="실행시점",
            quality_notes_ko="통계표 확정 전 KOSIS OpenAPI 접근성과 문서 응답만 확인했다.",
        )
        return {"source_id": source_id, "status": "success", "path": str(path)}
    except Exception as exc:
        log_failure(
            run_id_value=run_id_value,
            source_id=source_id,
            provider=provider,
            dataset_name=dataset_name,
            failure_type=type(exc).__name__,
            failure_reason_ko=f"KOSIS 홈 스모크 실패: {exc}",
            next_action_ko="통계표 ID를 확정한 뒤 공식 개발 가이드 엔드포인트로 재시도한다.",
            request_url_redacted=redacted,
        )
        return {"source_id": source_id, "status": "failed", "error": type(exc).__name__}


def main() -> None:
    RAW_ROOT.mkdir(parents=True, exist_ok=True)
    keys = parse_key_file()
    rid = run_id("raw_smoke")
    results = [
        collect_seoul_open_data_sample(rid, keys),
        collect_rtms_one_page(rid, keys),
        collect_sgis_auth(rid, keys),
        collect_kosis_home(rid, keys),
    ]
    log_path = RAW_ROOT / "run_logs" / f"{rid}.json"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps({"run_id": rid, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"run_id": rid, "results": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
