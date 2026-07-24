from __future__ import annotations

import json
import sys
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any

from ingest_common import (
    RAW_ROOT,
    http_get,
    log_failure,
    parse_key_file,
    redact_url,
    run_date,
    run_id,
    sanitize_sgis_auth_response,
    write_raw,
)


RUN_DATE = run_date()
PROVIDER = "SGIS"
SOURCE_ID = "sgis_small_area_stats"


def parse_json(body: bytes) -> dict[str, Any]:
    return json.loads(body.decode("utf-8", errors="replace"))


def api_url(endpoint: str, params: dict[str, Any]) -> str:
    return "https://sgisapi.mods.go.kr/OpenAPI3/" + endpoint + "?" + urllib.parse.urlencode(params)


def get_token(rid: str, keys: dict[str, str]) -> str:
    params = {
        "consumer_key": keys["sgis_service_id"],
        "consumer_secret": keys["sgis_secret"],
    }
    url = api_url("auth/authentication.json", params)
    status, body, _headers = http_get(url, timeout=30)
    data = parse_json(body)
    if str(data.get("errCd")) != "0":
        raise RuntimeError(f"SGIS 인증 실패: {data.get('errCd')} {data.get('errMsg')}")
    token = data.get("result", {}).get("accessToken")
    if not token:
        raise RuntimeError("SGIS 인증 응답에 accessToken이 없습니다.")

    write_raw(
        run_id_value=rid,
        source_id=SOURCE_ID,
        provider=PROVIDER,
        dataset_name="SGIS 인증 토큰 재발급 원응답",
        body=sanitize_sgis_auth_response(body),
        relative_path=f"{RUN_DATE}/sgis/census_stats/{rid}_auth_sanitized.json",
        request_url_redacted=redact_url(url),
        request_params={"consumer_key": "<redacted>", "consumer_secret": "<redacted>"},
        http_status=status,
        provider_result_code=str(data.get("errCd", "")),
        provider_result_message=str(data.get("errMsg", "")),
        spatial_unit="인증",
        time_unit="실행시점",
        quality_notes_ko="SGIS accessToken은 만료되므로 매 실행마다 재발급한다. 저장 원문에서는 토큰 값을 제거했다.",
    )
    return str(token)


def result_count(data: dict[str, Any]) -> int:
    result = data.get("result")
    return len(result) if isinstance(result, list) else 0


def call_and_store(
    *,
    rid: str,
    token: str,
    endpoint: str,
    params: dict[str, Any],
    dataset_name: str,
    relative_path: str,
    source_period: str,
    quality_notes_ko: str,
) -> dict[str, Any]:
    request_params = {"accessToken": token, **params}
    url = api_url(endpoint, request_params)
    status, body, _headers = http_get(url, timeout=60)
    data = parse_json(body)
    result_code = str(data.get("errCd", ""))
    result_msg = str(data.get("errMsg", ""))
    if result_code != "0":
        raise RuntimeError(f"{dataset_name} 실패: {result_code} {result_msg}")

    write_raw(
        run_id_value=rid,
        source_id=SOURCE_ID,
        provider=PROVIDER,
        dataset_name=dataset_name,
        body=body,
        relative_path=relative_path,
        request_url_redacted=redact_url(url),
        request_params={**params, "accessToken": "<redacted>"},
        http_status=status,
        provider_result_code=result_code,
        provider_result_message=result_msg,
        spatial_unit="서울 행정동",
        time_unit="연도",
        source_period=source_period,
        boundary_version="SGIS 행정구역 코드",
        area_code_type="SGIS adm_cd",
        quality_notes_ko=quality_notes_ko,
    )
    return data


def collect_company_with_fallback(rid: str, token: str) -> dict[str, Any]:
    for year in ["2024", "2023", "2022", "2021", "2020", "2019"]:
        params = {"year": year, "adm_cd": "11", "low_search": "2"}
        try:
            data = call_and_store(
                rid=rid,
                token=token,
                endpoint="stats/company.json",
                params=params,
                dataset_name=f"SGIS 서울 사업체통계 행정동 단위 {year}",
                relative_path=f"{RUN_DATE}/sgis/census_stats/{rid}_company_seoul_emd_{year}.json",
                source_period=year,
                quality_notes_ko=(
                    "SGIS 사업체통계는 서울 시도 코드 11에서 2단계 하위 행정구역을 요청해 "
                    "서울 행정동 단위 사업체수와 종사자수를 보존한다."
                ),
            )
            return {"year": year, "rows": result_count(data), "status": "success"}
        except Exception as exc:
            log_failure(
                run_id_value=rid,
                source_id=SOURCE_ID,
                provider=PROVIDER,
                dataset_name=f"SGIS 서울 사업체통계 행정동 단위 {year}",
                failure_type=type(exc).__name__,
                failure_reason_ko=f"SGIS 사업체통계 {year} 호출 실패: {exc}",
                next_action_ko="최신 기준년도가 거부되면 이전 제공 연도로 낮춰 재시도한다.",
                request_url_redacted=redact_url(api_url("stats/company.json", {"accessToken": token, **params})),
            )
            time.sleep(0.2)
    raise RuntimeError("SGIS 사업체통계 2024~2019 호출이 모두 실패했다.")


def main() -> int:
    rid = run_id("sgis_census_stats")
    keys = parse_key_file()
    summary: dict[str, Any] = {"run_id": rid, "created_at": datetime.now().isoformat(timespec="seconds")}

    try:
        token = get_token(rid, keys)
    except Exception as exc:
        log_failure(
            run_id_value=rid,
            source_id=SOURCE_ID,
            provider=PROVIDER,
            dataset_name="SGIS 인증 토큰 재발급",
            failure_type=type(exc).__name__,
            failure_reason_ko=f"SGIS 인증 실패: {exc}",
            next_action_ko="consumer_key=서비스ID, consumer_secret=key 순서와 SGIS 승인 상태를 확인한다.",
            request_url_redacted="https://sgisapi.mods.go.kr/OpenAPI3/auth/authentication.json?consumer_key=<redacted>&consumer_secret=<redacted>",
        )
        print(json.dumps({"run_id": rid, "status": "failed_auth", "error": repr(exc)}, ensure_ascii=False, indent=2))
        return 1

    calls = [
        {
            "key": "population",
            "endpoint": "stats/searchpopulation.json",
            "params": {"year": "2020", "adm_cd": "11", "low_search": "2", "gender": "0"},
            "dataset_name": "SGIS 서울 인구통계 행정동 단위 2020",
            "relative_path": f"{RUN_DATE}/sgis/census_stats/{rid}_population_seoul_emd_2020.json",
            "source_period": "2020",
            "quality_notes_ko": "SGIS 인구통계는 인구주택총조사 2020 기준 서울 행정동 단위 인구수와 평균나이를 보존한다.",
        },
        {
            "key": "household",
            "endpoint": "stats/household.json",
            "params": {"year": "2020", "adm_cd": "11", "low_search": "2"},
            "dataset_name": "SGIS 서울 가구통계 행정동 단위 2020",
            "relative_path": f"{RUN_DATE}/sgis/census_stats/{rid}_household_seoul_emd_2020.json",
            "source_period": "2020",
            "quality_notes_ko": "SGIS 가구통계는 인구주택총조사 2020 기준 서울 행정동 단위 가구수와 평균가구원수를 보존한다.",
        },
    ]

    for spec in calls:
        try:
            data = call_and_store(
                rid=rid,
                token=token,
                endpoint=str(spec["endpoint"]),
                params=dict(spec["params"]),
                dataset_name=str(spec["dataset_name"]),
                relative_path=str(spec["relative_path"]),
                source_period=str(spec["source_period"]),
                quality_notes_ko=str(spec["quality_notes_ko"]),
            )
            summary[str(spec["key"])] = {"rows": result_count(data), "status": "success"}
        except Exception as exc:
            log_failure(
                run_id_value=rid,
                source_id=SOURCE_ID,
                provider=PROVIDER,
                dataset_name=str(spec["dataset_name"]),
                failure_type=type(exc).__name__,
                failure_reason_ko=f"{spec['dataset_name']} 호출 실패: {exc}",
                next_action_ko="SGIS 기준년도, adm_cd, low_search 조합을 공식 문서와 대조해 재시도한다.",
                request_url_redacted=redact_url(api_url(str(spec["endpoint"]), {"accessToken": token, **dict(spec["params"])})),
            )
            summary[str(spec["key"])] = {"status": "failed", "error": repr(exc)}

    try:
        summary["company"] = collect_company_with_fallback(rid, token)
    except Exception as exc:
        summary["company"] = {"status": "failed", "error": repr(exc)}

    log_path = RAW_ROOT / "run_logs" / f"{rid}.json"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    success_count = sum(1 for key in ["population", "household", "company"] if summary.get(key, {}).get("status") == "success")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if success_count == 3 else 1


if __name__ == "__main__":
    sys.exit(main())
