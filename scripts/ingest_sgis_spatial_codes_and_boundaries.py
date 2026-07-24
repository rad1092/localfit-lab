from __future__ import annotations

import csv
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
SOURCE_ID = "sgis_spatial_admin_boundary"
FINDCODE_SOURCE_ID = "sgis_small_area_stats"


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
        relative_path=f"{RUN_DATE}/sgis/spatial_codes/{rid}_auth_sanitized.json",
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


def write_sgis_json(
    *,
    rid: str,
    endpoint: str,
    params: dict[str, Any],
    source_id: str,
    dataset_name: str,
    relative_path: str,
    spatial_unit: str,
    area_code_type: str = "",
    boundary_version: str = "",
    quality_notes_ko: str,
) -> dict[str, Any]:
    url = api_url(endpoint, params)
    status, body, _headers = http_get(url, timeout=60)
    data = parse_json(body)
    result_code = str(data.get("errCd", ""))
    result_msg = str(data.get("errMsg", ""))
    if result_code != "0":
        raise RuntimeError(f"{dataset_name} 실패: {result_code} {result_msg}")
    write_raw(
        run_id_value=rid,
        source_id=source_id,
        provider=PROVIDER,
        dataset_name=dataset_name,
        body=body,
        relative_path=relative_path,
        request_url_redacted=redact_url(url),
        request_params={**params, "accessToken": "<redacted>"},
        http_status=status,
        provider_result_code=result_code,
        provider_result_message=result_msg,
        spatial_unit=spatial_unit,
        time_unit="실행시점",
        source_period="2026-07-03",
        boundary_version=boundary_version,
        area_code_type=area_code_type,
        quality_notes_ko=quality_notes_ko,
    )
    return data


def result_list(data: dict[str, Any]) -> list[dict[str, Any]]:
    result = data.get("result")
    if isinstance(result, list):
        return [item for item in result if isinstance(item, dict)]
    if isinstance(result, dict):
        for key in ["result", "list", "items"]:
            value = result.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def collect_admin_codes_and_boundary(rid: str, token: str) -> dict[str, Any]:
    summary: dict[str, Any] = {"stage_calls": [], "boundary_calls": []}

    years = write_sgis_json(
        rid=rid,
        endpoint="year/data.json",
        params={"accessToken": token},
        source_id=SOURCE_ID,
        dataset_name="SGIS 최신/전체 기준년도 조회",
        relative_path=f"{RUN_DATE}/sgis/spatial_codes/{rid}_year_data.json",
        spatial_unit="기준년도",
        area_code_type="boundary/statistical_year",
        quality_notes_ko="SGIS 경계/통계 API 호출 전 최신 경계년도와 통계 기준년도를 확인하기 위한 원응답이다.",
    )
    boundary_year = str((years.get("result") or {}).get("lboudary_yr") or "2025")
    summary["boundary_year"] = boundary_year

    province = write_sgis_json(
        rid=rid,
        endpoint="addr/stage.json",
        params={"accessToken": token, "pg_yn": "0"},
        source_id=SOURCE_ID,
        dataset_name="SGIS 단계별 주소 조회 전국 시도 코드",
        relative_path=f"{RUN_DATE}/sgis/spatial_codes/{rid}_addr_stage_sido.json",
        spatial_unit="시도",
        area_code_type="SGIS addr/stage cd",
        quality_notes_ko="서울 코드 11을 포함한 시도 코드 체계를 확인하기 위한 원응답이다. 현재 SGIS 신규 호스트에서는 시도 목록 조회 시 cd 파라미터를 생략해야 정상 응답한다.",
    )
    summary["stage_calls"].append({"level": "sido", "rows": len(result_list(province))})

    seoul_sgg = write_sgis_json(
        rid=rid,
        endpoint="addr/stage.json",
        params={"accessToken": token, "cd": "11", "pg_yn": "0"},
        source_id=SOURCE_ID,
        dataset_name="SGIS 단계별 주소 조회 서울 자치구 코드",
        relative_path=f"{RUN_DATE}/sgis/spatial_codes/{rid}_addr_stage_seoul_sgg.json",
        spatial_unit="서울 자치구",
        area_code_type="SGIS addr/stage cd",
        quality_notes_ko="서울 상권/행정동/집계구 매칭의 상위 행정구역 기준 코드 원응답이다.",
    )
    districts = result_list(seoul_sgg)
    summary["stage_calls"].append({"level": "seoul_sgg", "rows": len(districts)})

    for item in districts:
        cd = str(item.get("cd", "")).strip()
        name = str(item.get("addr_name") or item.get("full_addr") or cd)
        if not cd:
            continue
        try:
            data = write_sgis_json(
                rid=rid,
                endpoint="addr/stage.json",
                params={"accessToken": token, "cd": cd, "pg_yn": "0"},
                source_id=SOURCE_ID,
                dataset_name=f"SGIS 단계별 주소 조회 서울 {name} 행정동 코드",
                relative_path=f"{RUN_DATE}/sgis/spatial_codes/emdong/{rid}_addr_stage_{cd}_emdong.json",
                spatial_unit="서울 행정동",
                area_code_type="SGIS addr/stage cd",
                quality_notes_ko=f"서울 {name} 하위 행정동 코드 원응답이다. 행정동 단위 공간 조인 기준 검증에 사용한다.",
            )
            summary["stage_calls"].append({"level": "emdong", "cd": cd, "name": name, "rows": len(result_list(data))})
            time.sleep(0.05)
        except Exception as exc:
            log_failure(
                run_id_value=rid,
                source_id=SOURCE_ID,
                provider=PROVIDER,
                dataset_name=f"SGIS 단계별 주소 조회 서울 {name} 행정동 코드",
                failure_type=type(exc).__name__,
                failure_reason_ko=f"서울 {name} 행정동 코드 조회 실패: {exc}",
                next_action_ko="해당 자치구 코드 형식과 SGIS addr/stage API 응답을 재확인한다.",
                request_url_redacted=redact_url(api_url("addr/stage.json", {"accessToken": token, "cd": cd, "pg_yn": "0"})),
            )

    for low_search, label in [("1", "서울 자치구 경계"), ("2", "서울 행정동 경계")]:
        try:
            data = write_sgis_json(
                rid=rid,
                endpoint="boundary/hadmarea.geojson",
                params={"accessToken": token, "year": boundary_year, "adm_cd": "11", "low_search": low_search},
                source_id=SOURCE_ID,
                dataset_name=f"SGIS 행정구역경계 {label}",
                relative_path=f"{RUN_DATE}/sgis/boundary/{rid}_hadmarea_seoul_low{low_search}_{boundary_year}.geojson",
                spatial_unit=label,
                area_code_type="SGIS adm_cd",
                boundary_version=f"SGIS {boundary_year}",
                quality_notes_ko=f"{label} 원본 GeoJSON이다. 서울 상권 경계와 행정구역 매칭 검증에 사용한다.",
            )
            features = data.get("features") if isinstance(data, dict) else None
            summary["boundary_calls"].append({"label": label, "year": boundary_year, "features": len(features) if isinstance(features, list) else ""})
            time.sleep(0.05)
        except Exception as exc:
            log_failure(
                run_id_value=rid,
                source_id=SOURCE_ID,
                provider=PROVIDER,
                dataset_name=f"SGIS 행정구역경계 {label}",
                failure_type=type(exc).__name__,
                failure_reason_ko=f"{label} 조회 실패: {exc}",
                next_action_ko="최신 경계년도가 아직 해당 API에서 열리지 않았을 수 있으므로 전년도 경계년도로 재시도한다.",
                request_url_redacted=redact_url(api_url("boundary/hadmarea.geojson", {"accessToken": token, "year": boundary_year, "adm_cd": "11", "low_search": low_search})),
            )

    return summary


def load_trade_area_points(limit: int = 25) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    target: Path | None = None
    for path in Path("datacorpus/_final/spatial_od").glob("*.csv"):
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                header = reader.fieldnames or []
                if "XCNTS_VALU" in header and "YDNTS_VALU" in header:
                    target = path
                    rows = list(reader)
                    break
        except Exception:
            continue
    else:
        return []

    seen_districts: set[str] = set()
    for row in rows:
        district = row.get("자치구_코드_명") or row.get("자치구_코드") or ""
        lon = row.get("상권_중심경도")
        lat = row.get("상권_중심위도")
        if not district or district in seen_districts or not lon or not lat:
            continue
        try:
            lon_f = float(lon)
            lat_f = float(lat)
        except ValueError:
            continue
        seen_districts.add(district)
        candidates.append(
            {
                "trade_area_code": row.get("상권_코드", ""),
                "trade_area_name": row.get("상권_코드_명", ""),
                "district": district,
                "admin_dong": row.get("행정동_코드_명", ""),
                "lon": lon_f,
                "lat": lat_f,
                "source_file": str(target),
            }
        )
        if len(candidates) >= limit:
            break
    return candidates


def collect_findcode_for_trade_areas(rid: str, token: str) -> dict[str, Any]:
    points = load_trade_area_points(limit=25)
    summary: dict[str, Any] = {"points": len(points), "success": 0, "failed": 0, "details": []}
    if not points:
        log_failure(
            run_id_value=rid,
            source_id=FINDCODE_SOURCE_ID,
            provider=PROVIDER,
            dataset_name="SGIS 소지역 코드찾기 서울 상권 대표좌표",
            failure_type="NoCandidatePoints",
            failure_reason_ko="상권 기준테이블에서 대표 좌표 후보를 찾지 못했다.",
            next_action_ko="datacorpus/_final/spatial_od의 공간 기준테이블 생성 상태를 다시 확인한다.",
            request_url_redacted="",
        )
        return summary

    try:
        from pyproj import Transformer
    except Exception as exc:
        log_failure(
            run_id_value=rid,
            source_id=FINDCODE_SOURCE_ID,
            provider=PROVIDER,
            dataset_name="SGIS 소지역 코드찾기 서울 상권 대표좌표",
            failure_type=type(exc).__name__,
            failure_reason_ko=f"pyproj가 없어 WGS84 좌표를 SGIS UTM-K(EPSG:5179)로 변환하지 못했다: {exc}",
            next_action_ko="uv run --with pyproj python scripts/ingest_sgis_spatial_codes_and_boundaries.py 로 재실행한다.",
            request_url_redacted="",
        )
        return summary

    transformer = Transformer.from_crs("EPSG:4326", "EPSG:5179", always_xy=True)
    for idx, point in enumerate(points, start=1):
        x, y = transformer.transform(point["lon"], point["lat"])
        params = {
            "accessToken": token,
            "x_coor": f"{x:.3f}",
            "y_coor": f"{y:.3f}",
        }
        name = point["trade_area_name"] or point["trade_area_code"] or f"point_{idx}"
        safe_code = point["trade_area_code"] or f"point_{idx:02d}"
        try:
            data = write_sgis_json(
                rid=rid,
                endpoint="personal/findcodeinsmallarea.json",
                params=params,
                source_id=FINDCODE_SOURCE_ID,
                dataset_name=f"SGIS 소지역 코드찾기 서울 상권 대표좌표 {name}",
                relative_path=f"{RUN_DATE}/sgis/findcode_trade_area/{rid}_{safe_code}_findcode.json",
                spatial_unit="상권 대표좌표→집계구/행정동",
                area_code_type="sido_cd+sgg_cd+emdong_cd+tot_reg_cd",
                quality_notes_ko=(
                    f"상권 기준테이블의 WGS84 중심좌표를 EPSG:5179로 변환해 SGIS 집계구/행정동 코드를 조회했다. "
                    f"자치구={point['district']}, 행정동={point['admin_dong']}, 원천파일={point['source_file']}"
                ),
            )
            summary["success"] += 1
            summary["details"].append({"trade_area_code": safe_code, "name": name, "status": "success", "result": data.get("result")})
        except Exception as exc:
            summary["failed"] += 1
            log_failure(
                run_id_value=rid,
                source_id=FINDCODE_SOURCE_ID,
                provider=PROVIDER,
                dataset_name=f"SGIS 소지역 코드찾기 서울 상권 대표좌표 {name}",
                failure_type=type(exc).__name__,
                failure_reason_ko=f"{name} 대표좌표 소지역 코드 조회 실패: {exc}",
                next_action_ko="좌표계 변환값과 해당 지점이 SGIS 서비스 범위 안에 있는지 확인한다.",
                request_url_redacted=redact_url(api_url("personal/findcodeinsmallarea.json", params)),
            )
        time.sleep(0.05)
    return summary


def main() -> int:
    rid = run_id("sgis_spatial_codes")
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

    summary["admin_codes_and_boundaries"] = collect_admin_codes_and_boundary(rid, token)
    summary["findcode_trade_areas"] = collect_findcode_for_trade_areas(rid, token)

    log_path = RAW_ROOT / "run_logs" / f"{rid}.json"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
