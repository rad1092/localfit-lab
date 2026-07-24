from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError

from ingest_common import RAW_ROOT, ROOT, http_get, log_failure, redact_url, run_id, write_raw


DOCS = [
    {
        "provider": "SGIS",
        "source_id": "sgis_spatial_api_docs",
        "dataset_name": "SGIS 인증 API 공식 문서",
        "url": "https://sgis.mods.go.kr/developer/html/newOpenApi/api/dataApi/basics.html",
        "relative_path": "20260703/sgis/docs/sgis_auth_basics_20260703.html",
        "research_path": "research/algorithm_evidence_sources/data_docs/sgis_auth_basics_20260703.html",
        "note": "SGIS는 매 실행마다 서비스ID/Secret으로 accessToken을 발급받고 토큰으로 후속 API를 호출해야 한다.",
    },
    {
        "provider": "SGIS",
        "source_id": "sgis_spatial_api_docs",
        "dataset_name": "SGIS 주소경계 API 공식 문서",
        "url": "https://sgis.mods.go.kr/developer/html/newOpenApi/api/dataApi/addressBoundary.html",
        "relative_path": "20260703/sgis/docs/sgis_address_boundary_20260703.html",
        "research_path": "research/algorithm_evidence_sources/data_docs/sgis_address_boundary_20260703.html",
        "note": "행정구역 단계조회, 지오코딩, 리버스 지오코딩, 행정구역/집계구 경계 API의 기준 파라미터와 좌표계 확인용 원문이다.",
    },
    {
        "provider": "SGIS",
        "source_id": "sgis_spatial_api_docs",
        "dataset_name": "SGIS 소지역 코드찾기 API 공식 문서",
        "url": "https://sgis.mods.go.kr/developer/html/newOpenApi/api/dataApi/personal.html",
        "relative_path": "20260703/sgis/docs/sgis_personal_findcode_20260703.html",
        "research_path": "research/algorithm_evidence_sources/data_docs/sgis_personal_findcode_20260703.html",
        "note": "좌표를 집계구/행정동 코드로 매칭하는 findcodeinsmallarea API 근거 문서다.",
    },
    {
        "provider": "VWorld",
        "source_id": "vworld_juso_geocoding_docs",
        "dataset_name": "VWorld Geocoder API 2.0 공식 문서",
        "url": "https://www.vworld.kr/dev/v4dv_geocoderguide2_s001.do",
        "relative_path": "20260703/vworld/docs/vworld_geocoder_2_0_20260703.html",
        "research_path": "research/algorithm_evidence_sources/data_docs/vworld_geocoder_2_0_20260703.html",
        "note": "VWorld 주소-좌표 변환은 일일 요청 제한과 별도 저장 제한 문구가 있어 원본 데이터 적재보다는 검증/보정 호출로만 취급한다.",
    },
    {
        "provider": "Juso",
        "source_id": "vworld_juso_geocoding_docs",
        "dataset_name": "Juso 도로명주소 검색 API 공식 문서",
        "url": "https://business.juso.go.kr/jst/jstRoadNmAddrApiSearch",
        "relative_path": "20260703/juso/docs/juso_road_address_search_20260703.html",
        "research_path": "research/algorithm_evidence_sources/data_docs/juso_road_address_search_20260703.html",
        "note": "도로명주소 검색 API의 confmKey, currentPage, countPerPage, keyword 등 호출 형식 확인용 원문이다.",
    },
]


def save_research_copy(path_text: str, body: bytes) -> None:
    path = ROOT / path_text
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)


def main() -> int:
    rid = run_id("spatial_api_docs")
    run_log: dict[str, object] = {"run_id": rid, "docs": []}

    for doc in DOCS:
        url = doc["url"]
        try:
            status, body, headers = http_get(url, timeout=60)
            save_research_copy(doc["research_path"], body)
            write_raw(
                run_id_value=rid,
                source_id=doc["source_id"],
                provider=doc["provider"],
                dataset_name=doc["dataset_name"],
                body=body,
                relative_path=doc["relative_path"],
                request_url_redacted=redact_url(url),
                request_params={},
                http_status=status,
                provider_result_code=str(status),
                provider_result_message=f"content_type={headers.get('Content-Type', '')}; bytes={len(body)}",
                spatial_unit="API 문서",
                time_unit="문서 수집일",
                source_period="2026-07-03",
                quality_notes_ko=doc["note"],
            )
            run_log["docs"].append(
                {
                    "dataset_name": doc["dataset_name"],
                    "url": redact_url(url),
                    "status": "success",
                    "http_status": status,
                    "bytes": len(body),
                    "research_path": doc["research_path"],
                    "raw_path": str(RAW_ROOT / doc["relative_path"]),
                }
            )
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            log_failure(
                run_id_value=rid,
                source_id=doc["source_id"],
                provider=doc["provider"],
                dataset_name=doc["dataset_name"],
                failure_type=type(exc).__name__,
                failure_reason_ko=f"공식 문서 다운로드 실패: {exc}",
                next_action_ko="브라우저로 접근 가능한 대체 공식 문서 URL을 확인하거나 재시도한다.",
                request_url_redacted=redact_url(url),
            )
            run_log["docs"].append(
                {
                    "dataset_name": doc["dataset_name"],
                    "url": redact_url(url),
                    "status": "failed",
                    "error": repr(exc),
                }
            )

    log_path = RAW_ROOT / "run_logs" / f"{rid}.json"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(run_log, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(run_log, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
