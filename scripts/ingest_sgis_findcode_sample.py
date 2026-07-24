from __future__ import annotations

import json
import urllib.parse
from datetime import datetime

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
SOURCE_ID = "sgis_small_area_stats"
PROVIDER = "SGIS"


def get_sgis_token(run_id_value: str, keys: dict[str, str]) -> str:
    params = {
        "consumer_key": keys["sgis_service_id"],
        "consumer_secret": keys["sgis_secret"],
    }
    url = "https://sgisapi.mods.go.kr/OpenAPI3/auth/authentication.json?" + urllib.parse.urlencode(params)
    status, body, _headers = http_get(url, timeout=30)
    data = json.loads(body.decode("utf-8"))
    if str(data.get("errCd")) not in {"0"}:
        raise RuntimeError(f"SGIS 인증 실패: {data.get('errCd')} {data.get('errMsg')}")
    token = data.get("result", {}).get("accessToken")
    if not token:
        raise RuntimeError("SGIS 인증 응답에 accessToken이 없습니다.")
    write_raw(
        run_id_value=run_id_value,
        source_id=SOURCE_ID,
        provider=PROVIDER,
        dataset_name="SGIS 인증 토큰 재발급",
        body=sanitize_sgis_auth_response(body),
        relative_path=f"{RUN_DATE}/sgis/{run_id_value}_sgis_auth_sanitized.json",
        request_url_redacted=redact_url(url),
        request_params={"consumer_key": "<redacted>", "consumer_secret": "<redacted>"},
        http_status=status,
        provider_result_code=str(data.get("errCd", "")),
        provider_result_message=str(data.get("errMsg", "")),
        spatial_unit="인증",
        time_unit="실행시점",
        quality_notes_ko="SGIS accessToken은 저장 전 제거했다. 실행마다 토큰을 재발급하는 구조를 검증한다.",
    )
    return token


def transform_seoul_city_hall_to_sgis() -> tuple[float, float, str]:
    try:
        from pyproj import Transformer
    except Exception as exc:
        return 953901.0, 1952032.0, f"fallback_sgis_known_seoul_center_coordinate_no_pyproj:{type(exc).__name__}"

    lon, lat = 126.9784147, 37.5666805
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:5179", always_xy=True)
    x, y = transformer.transform(lon, lat)
    return round(x, 3), round(y, 3), "pyproj_epsg4326_to_epsg5179"


def main() -> None:
    keys = parse_key_file()
    rid = run_id("sgis_findcode")
    results = []
    token = ""
    try:
        token = get_sgis_token(rid, keys)
    except Exception as exc:
        log_failure(
            run_id_value=rid,
            source_id=SOURCE_ID,
            provider=PROVIDER,
            dataset_name="SGIS 인증 토큰 재발급",
            failure_type=type(exc).__name__,
            failure_reason_ko=f"SGIS 토큰 재발급 실패: {exc}",
            next_action_ko="서비스ID/보안 key 순서와 SGIS 계정 승인 상태를 확인한다.",
            request_url_redacted="https://sgisapi.mods.go.kr/OpenAPI3/auth/authentication.json?consumer_key=<redacted>&consumer_secret=<redacted>",
        )
        print(json.dumps({"run_id": rid, "status": "failed_auth"}, ensure_ascii=False, indent=2))
        return

    try:
        x, y, transform_method = transform_seoul_city_hall_to_sgis()
        params = {
            "accessToken": token,
            "x_coor": str(x),
            "y_coor": str(y),
        }
        url = "https://sgisapi.mods.go.kr/OpenAPI3/personal/findcodeinsmallarea.json?" + urllib.parse.urlencode(params)
        status, body, _headers = http_get(url, timeout=30)
        data = json.loads(body.decode("utf-8"))
        result_code = str(data.get("errCd", ""))
        result_msg = str(data.get("errMsg", ""))
        if result_code not in {"0"}:
            raise RuntimeError(f"SGIS 소지역 코드찾기 실패: {result_code} {result_msg}")
        path = write_raw(
            run_id_value=rid,
            source_id=SOURCE_ID,
            provider=PROVIDER,
            dataset_name="SGIS 소지역 코드찾기 샘플",
            body=body,
            relative_path=f"{RUN_DATE}/sgis/{rid}_findcodeinsmallarea_seoul_city_hall.json",
            request_url_redacted=redact_url(url),
            request_params={"accessToken": "<redacted>", "x_coor": x, "y_coor": y, "source_wgs84": [126.9784147, 37.5666805], "transform_method": transform_method},
            http_status=status,
            provider_result_code=result_code,
            provider_result_message=result_msg,
            spatial_unit="좌표→집계구/행정동",
            time_unit="실행시점",
            boundary_version="SGIS 현재 경계",
            area_code_type="sido_cd+sgg_cd+emdong_cd+tot_reg_cd",
            quality_notes_ko=f"서울 중심 좌표로 SGIS 행정동/집계구 코드 매칭을 검증했다. 좌표 처리 방식: {transform_method}",
        )
        results.append({"status": "success", "path": str(path), "result_code": result_code, "result": data.get("result", {})})
    except Exception as exc:
        log_failure(
            run_id_value=rid,
            source_id=SOURCE_ID,
            provider=PROVIDER,
            dataset_name="SGIS 소지역 코드찾기 샘플",
            failure_type=type(exc).__name__,
            failure_reason_ko=f"SGIS 소지역 코드찾기 실패: {exc}",
            next_action_ko="좌표계(EPSG:5179)와 x/y 파라미터 형식을 확인하고 다른 서울 좌표로 재시도한다.",
            request_url_redacted="https://sgisapi.mods.go.kr/OpenAPI3/personal/findcodeinsmallarea.json?accessToken=<redacted>",
        )
        results.append({"status": "failed", "error": type(exc).__name__})

    summary = {"run_id": rid, "results": results, "created_at": datetime.now().isoformat(timespec="seconds")}
    log_path = RAW_ROOT / "run_logs" / f"{rid}.json"
    log_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
