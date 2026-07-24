from __future__ import annotations

import csv
import json
import math
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
    write_raw,
)


RUN_DATE = run_date()
PROVIDER_SEOUL = "서울열린데이터광장"
PROVIDER_DATA_GO = "공공데이터포털/행정안전부"
PAGE_SIZE = 1000
SLEEP_SECONDS = 0.05

REGISTRY_FIELDS = [
    "source_id",
    "priority",
    "provider",
    "dataset_name",
    "method_axis",
    "score_axis",
    "spatial_unit",
    "time_unit",
    "collection_method",
    "credential_ref",
    "source_url",
    "local_doc",
    "current_status",
    "duplicate_policy",
    "reason_ko",
    "notes_ko",
]

REGISTRY_ROWS = [
    {
        "source_id": "seoul_real_estate_broker_office",
        "priority": "P1",
        "provider": PROVIDER_SEOUL,
        "dataset_name": "서울시 부동산 중개업소 정보",
        "method_axis": "부동산 서비스 생태계, 입지 보조/경쟁 밀도",
        "score_axis": "경쟁/상권환경, 데이터신뢰도",
        "spatial_unit": "중개업소",
        "time_unit": "수시",
        "collection_method": "서울 OpenAPI landBizInfo 전체 페이지 수집",
        "credential_ref": "SEOUL_OPEN_DATA_KEY",
        "source_url": "https://data.seoul.go.kr/dataList/OA-15550/A/1/datasetView.do",
        "local_doc": "research/algorithm_evidence_sources/data_docs/seoul_open_data_real_estate_broker_office_OA-15550.html",
        "current_status": "collected_raw",
        "duplicate_policy": "SYS_REG_NO+REST_BRKR_INFO+LASTMODTS+해시 기준",
        "reason_ko": "부동산 중개업소의 공간 분포와 영업상태를 보존해 부동산 입지 주변의 거래/중개 생태계를 설명한다.",
        "notes_ko": "개별 상업시설 성공률이 아니라 부동산 중개업소 현황 자료다. 전화번호 등 개인정보성 필드는 후처리 공개 시 마스킹한다.",
    },
    {
        "source_id": "seoul_localdata_general_restaurant_license",
        "priority": "P1",
        "provider": PROVIDER_SEOUL,
        "dataset_name": "서울시 일반음식점 인허가 정보",
        "method_axis": "인허가 개폐업, 생존/폐업 위험, 업종 밀도",
        "score_axis": "경쟁/상권환경, 성장/안정성",
        "spatial_unit": "인허가 사업장",
        "time_unit": "매일/3일전 자료",
        "collection_method": "서울 OpenAPI LOCALDATA_072404 전체 페이지 수집",
        "credential_ref": "SEOUL_OPEN_DATA_KEY",
        "source_url": "https://data.seoul.go.kr/dataList/OA-16094/S/1/datasetView.do",
        "local_doc": "research/algorithm_evidence_sources/data_docs/seoul_open_data_general_restaurant_license_OA-16094.html",
        "current_status": "collected_raw",
        "duplicate_policy": "MGTNO+LASTMODTS+UPDATEDT+해시 기준",
        "reason_ko": "서울 상권분석 점포 집계와 별도로 개별 일반음식점 인허가, 폐업일자, 영업상태, 면적, 좌표를 검증용 원천으로 보존한다.",
        "notes_ko": "좌표는 EPSG:5174 중부원점 TM으로 명시되어 있어 위경도 변환 전 원좌표를 그대로 보존한다.",
    },
    {
        "source_id": "seoul_localdata_rest_cafe_license",
        "priority": "P1",
        "provider": PROVIDER_SEOUL,
        "dataset_name": "서울시 휴게음식점 인허가 정보",
        "method_axis": "카페/분식/휴게 업종 인허가, 생존/폐업 위험, 업종 밀도",
        "score_axis": "경쟁/상권환경, 성장/안정성",
        "spatial_unit": "인허가 사업장",
        "time_unit": "매일/3일전 자료",
        "collection_method": "서울 OpenAPI LOCALDATA_072405 전체 페이지 수집",
        "credential_ref": "SEOUL_OPEN_DATA_KEY",
        "source_url": "https://data.seoul.go.kr/",
        "local_doc": "research/algorithm_evidence_sources/data_docs/data_go_kr_localdata_rest_cafe_file.html",
        "current_status": "collected_raw",
        "duplicate_policy": "MGTNO+LASTMODTS+UPDATEDT+해시 기준",
        "reason_ko": "커피숍·분식·휴게음식점 계열 후보 업종의 개별 인허가/폐업 이력을 보강한다.",
        "notes_ko": "행안부 파일 URL은 403이므로 서울 OpenAPI의 LOCALDATA_072405를 우선 채택한다.",
    },
]

DOC_TARGETS = [
    {
        "source_id": "seoul_real_estate_broker_office_docs",
        "provider": PROVIDER_SEOUL,
        "dataset_name": "서울시 부동산 중개업소 정보 공식 문서",
        "url": "https://data.seoul.go.kr/dataList/OA-15550/A/1/datasetView.do",
        "relative_path": f"{RUN_DATE}/seoul_open_data/docs/seoul_real_estate_broker_office_OA-15550.html",
        "copy_to": "research/algorithm_evidence_sources/data_docs/seoul_open_data_real_estate_broker_office_OA-15550.html",
        "quality_notes_ko": "서울시 부동산정보광장 원천, 수시 갱신, 공공누리 1유형, Sheet/OpenAPI 제공 문서를 보존했다.",
    },
    {
        "source_id": "seoul_localdata_general_restaurant_docs",
        "provider": PROVIDER_SEOUL,
        "dataset_name": "서울시 일반음식점 인허가 정보 공식 문서",
        "url": "https://data.seoul.go.kr/dataList/OA-16094/S/1/datasetView.do",
        "relative_path": f"{RUN_DATE}/seoul_open_data/docs/seoul_general_restaurant_license_OA-16094.html",
        "copy_to": "research/algorithm_evidence_sources/data_docs/seoul_open_data_general_restaurant_license_OA-16094.html",
        "quality_notes_ko": "서울 열린데이터 문서의 매일 갱신, 3일전 자료, EPSG:5174 좌표 주의사항을 보존했다.",
    },
    {
        "source_id": "localdata_core_docs",
        "provider": PROVIDER_DATA_GO,
        "dataset_name": "지방행정 인허가정보 국가중점데이터 문서",
        "url": "https://www.data.go.kr/tcs/eds/selectCoreDataView.do?coreDataInsttCode=1741000&coreDataSn=6",
        "relative_path": f"{RUN_DATE}/public_data/docs/localdata_core_data_page.html",
        "copy_to": "research/algorithm_evidence_sources/data_docs/data_go_kr_localdata_core_data_page.html",
        "quality_notes_ko": "지방행정 인허가정보의 통합 제공 범위와 분야별 종수 설명을 보존했다.",
    },
    {
        "source_id": "localdata_mois_integration_notice",
        "provider": "행정안전부",
        "dataset_name": "지방행정 인허가 데이터 통합 개방 보도자료",
        "url": "https://www.mois.go.kr/frt/bbs/type010/commonSelectBoardArticle.do?bbsId=BBSMSTR_000000000008&nttId=123399",
        "relative_path": f"{RUN_DATE}/public_data/docs/mois_localdata_integration_notice_20260126.html",
        "copy_to": "research/algorithm_evidence_sources/data_docs/mois_localdata_integration_notice_20260126.html",
        "quality_notes_ko": "LocalData 계열 자료가 공공데이터포털 중심으로 통합 제공된다는 공식 보도자료를 보존했다.",
    },
    {
        "source_id": "data_go_kr_general_restaurant_docs",
        "provider": PROVIDER_DATA_GO,
        "dataset_name": "행정안전부 일반음식점 파일/조회서비스 문서",
        "url": "https://www.data.go.kr/data/15045016/fileData.do?recommendDataYn=Y",
        "relative_path": f"{RUN_DATE}/public_data/docs/data_go_kr_localdata_general_restaurant_file.html",
        "copy_to": "research/algorithm_evidence_sources/data_docs/data_go_kr_localdata_general_restaurant_file.html",
        "quality_notes_ko": "전국 일반음식점 파일데이터의 매일 갱신, 2일전 현행화, EPSG:5174 좌표 설명을 보존했다.",
    },
    {
        "source_id": "data_go_kr_rest_cafe_docs",
        "provider": PROVIDER_DATA_GO,
        "dataset_name": "행정안전부 휴게음식점 파일 문서",
        "url": "https://www.data.go.kr/data/15006730/fileData.do",
        "relative_path": f"{RUN_DATE}/public_data/docs/data_go_kr_localdata_rest_cafe_file.html",
        "copy_to": "research/algorithm_evidence_sources/data_docs/data_go_kr_localdata_rest_cafe_file.html",
        "quality_notes_ko": "전국 휴게음식점 파일데이터의 매일 갱신, 2일전 현행화, EPSG:5174 좌표 설명을 보존했다.",
    },
]

SERVICES = [
    {
        "source_id": "seoul_real_estate_broker_office",
        "service": "landBizInfo",
        "dataset_name": "서울시 부동산 중개업소 정보 전체 원응답",
        "spatial_unit": "중개업소",
        "time_unit": "수시",
        "source_period": "API 전체 응답 기준",
        "boundary_version": "서울 부동산정보광장/열린데이터광장 2026-07-03 수집 기준",
        "area_code_type": "SYS_REG_NO+SGG_CD+STDG_CD+REST_BRKR_INFO",
        "quality_notes_ko": "부동산 중개업소 영업상태와 주소/법정동 코드를 보존한다. 공개 리포트에는 전화번호/대표자명 마스킹을 검토한다.",
    },
    {
        "source_id": "seoul_localdata_general_restaurant_license",
        "service": "LOCALDATA_072404",
        "dataset_name": "서울시 일반음식점 인허가 정보 전체 원응답",
        "spatial_unit": "인허가 사업장",
        "time_unit": "매일/3일전 자료",
        "source_period": "API 전체 응답 기준",
        "boundary_version": "서울 열린데이터광장 2026-07-03 수집 기준",
        "area_code_type": "MGTNO+OPNSFTEAMCODE+좌표(EPSG:5174)",
        "quality_notes_ko": "영업상태, 폐업일자, 면적, 업태, EPSG:5174 좌표를 보존한다. 좌표 변환은 후처리에서 별도 검증한다.",
    },
    {
        "source_id": "seoul_localdata_rest_cafe_license",
        "service": "LOCALDATA_072405",
        "dataset_name": "서울시 휴게음식점 인허가 정보 전체 원응답",
        "spatial_unit": "인허가 사업장",
        "time_unit": "매일/3일전 자료",
        "source_period": "API 전체 응답 기준",
        "boundary_version": "서울 열린데이터광장 2026-07-03 수집 기준",
        "area_code_type": "MGTNO+OPNSFTEAMCODE+좌표(EPSG:5174)",
        "quality_notes_ko": "커피숍/분식/휴게음식점 계열의 영업상태, 폐업일자, 면적, EPSG:5174 좌표를 보존한다.",
    },
]

LOCALDATA_FILE_PROBES = [
    {
        "source_id": "localdata_general_restaurant_file_probe",
        "provider": PROVIDER_DATA_GO,
        "dataset_name": "행정안전부 일반음식점 전국 파일 URL",
        "url": "https://file.localdata.go.kr/file/general_restaurants/info",
        "next_action_ko": "공공데이터포털 로그인 다운로드 또는 서울 OpenAPI LOCALDATA_072404 원응답을 우선 사용한다.",
    },
    {
        "source_id": "localdata_rest_cafe_file_probe",
        "provider": PROVIDER_DATA_GO,
        "dataset_name": "행정안전부 휴게음식점 전국 파일 URL",
        "url": "https://file.localdata.go.kr/file/rest_cafes/info",
        "next_action_ko": "공공데이터포털 로그인 다운로드 또는 서울 OpenAPI LOCALDATA_072405 원응답을 우선 사용한다.",
    },
]


def append_registry_rows() -> None:
    path = RAW_ROOT / "source_registry.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_ids: set[str] = set()
    if path.exists():
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                if row.get("source_id"):
                    existing_ids.add(row["source_id"])
    needs_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=REGISTRY_FIELDS)
        if needs_header:
            writer.writeheader()
        for row in REGISTRY_ROWS:
            if row["source_id"] not in existing_ids:
                writer.writerow({field: row.get(field, "") for field in REGISTRY_FIELDS})


def save_research_copy(copy_to: str, body: bytes) -> None:
    path = Path(copy_to)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)


def collect_docs(rid: str) -> list[dict[str, Any]]:
    collected = []
    for target in DOC_TARGETS:
        url = target["url"]
        try:
            status, body, _headers = http_get(url, timeout=60)
            save_research_copy(target["copy_to"], body)
            path = write_raw(
                run_id_value=rid,
                source_id=target["source_id"],
                provider=target["provider"],
                dataset_name=target["dataset_name"],
                body=body,
                relative_path=target["relative_path"],
                request_url_redacted=redact_url(url),
                request_params={"doc_url": url},
                http_status=status,
                provider_result_code="DOC",
                provider_result_message="공식 문서 HTML 저장",
                spatial_unit="문서",
                time_unit="수집일",
                source_period="2026-07-03 접근",
                quality_notes_ko=target["quality_notes_ko"],
            )
            collected.append({"url": url, "path": str(path), "bytes": len(body)})
        except Exception as exc:
            log_failure(
                run_id_value=rid,
                source_id=target["source_id"],
                provider=target["provider"],
                dataset_name=target["dataset_name"],
                failure_type=type(exc).__name__,
                failure_reason_ko=f"공식 문서 저장 실패: {exc}",
                next_action_ko="문서 URL 접근 가능 여부와 사이트 차단 정책을 확인한 뒤 재시도한다.",
                request_url_redacted=redact_url(url),
            )
    return collected


def parse_openapi_response(body: bytes, service: str) -> tuple[str, str, int, int]:
    data = json.loads(body.decode("utf-8", errors="replace"))
    if "RESULT" in data:
        result = data["RESULT"]
        return str(result.get("CODE", "")), str(result.get("MESSAGE", "")), 0, 0
    payload = data.get(service, {})
    result = payload.get("RESULT", {}) if isinstance(payload, dict) else {}
    rows = payload.get("row", []) if isinstance(payload, dict) else []
    total = payload.get("list_total_count", 0) if isinstance(payload, dict) else 0
    return str(result.get("CODE", "")), str(result.get("MESSAGE", "")), int(total or 0), len(rows or [])


def build_seoul_url(key: str, service: str, start: int, end: int) -> str:
    return f"http://openapi.seoul.go.kr:8088/{urllib.parse.quote(key)}/json/{service}/{start}/{end}/"


def save_service_page(
    *,
    rid: str,
    key: str,
    service_info: dict[str, str],
    start: int,
    end: int,
    status: int,
    body: bytes,
    result_code: str,
    result_message: str,
    total_count: int,
    row_count: int,
) -> Path:
    service = service_info["service"]
    url = build_seoul_url(key, service, start, end)
    return write_raw(
        run_id_value=rid,
        source_id=service_info["source_id"],
        provider=PROVIDER_SEOUL,
        dataset_name=service_info["dataset_name"],
        body=body,
        relative_path=f"{RUN_DATE}/seoul_open_data/full/{service}/{service}_{start}_{end}.json",
        request_url_redacted=redact_url(url, extra_values=[key]),
        request_params={"service": service, "start": start, "end": end, "key": "<redacted>"},
        http_status=status,
        provider_result_code=result_code,
        provider_result_message=result_message,
        spatial_unit=service_info["spatial_unit"],
        time_unit=service_info["time_unit"],
        source_period=service_info["source_period"],
        boundary_version=service_info["boundary_version"],
        area_code_type=service_info["area_code_type"],
        quality_notes_ko=(
            f"{service_info['quality_notes_ko']} 이 페이지의 total_count={total_count}, row_count={row_count}."
        ),
    )


def collect_service(rid: str, key: str, service_info: dict[str, str]) -> dict[str, Any]:
    service = service_info["service"]
    saved_pages = []
    failed_pages = []
    total_count = 0

    first_url = build_seoul_url(key, service, 1, PAGE_SIZE)
    try:
        status, body, _headers = http_get(first_url, timeout=90)
        result_code, result_message, total_count, row_count = parse_openapi_response(body, service)
        if result_code and result_code not in {"INFO-000", "INFO-1000"}:
            raise RuntimeError(f"서울 OpenAPI 결과 오류 {result_code}: {result_message}")
        path = save_service_page(
            rid=rid,
            key=key,
            service_info=service_info,
            start=1,
            end=PAGE_SIZE,
            status=status,
            body=body,
            result_code=result_code,
            result_message=result_message,
            total_count=total_count,
            row_count=row_count,
        )
        saved_pages.append({"start": 1, "end": PAGE_SIZE, "row_count": row_count, "path": str(path)})
    except Exception as exc:
        log_failure(
            run_id_value=rid,
            source_id=service_info["source_id"],
            provider=PROVIDER_SEOUL,
            dataset_name=service_info["dataset_name"],
            failure_type=type(exc).__name__,
            failure_reason_ko=f"{service} 첫 페이지 수집 실패: {exc}",
            next_action_ko="서울 OpenAPI 키, 서비스명, 일일 호출 제한, 원천 서비스 상태를 확인하고 재시도한다.",
            request_url_redacted=redact_url(first_url, extra_values=[key]),
        )
        return {
            "service": service,
            "total_count": 0,
            "saved_pages": 0,
            "saved_rows": 0,
            "failed_pages": [{"start": 1, "end": PAGE_SIZE, "error": type(exc).__name__}],
        }

    total_pages = math.ceil(total_count / PAGE_SIZE) if total_count else 1
    for page_index in range(2, total_pages + 1):
        start = (page_index - 1) * PAGE_SIZE + 1
        end = min(page_index * PAGE_SIZE, total_count)
        url = build_seoul_url(key, service, start, end)
        try:
            status, body, _headers = http_get(url, timeout=90)
            result_code, result_message, page_total_count, row_count = parse_openapi_response(body, service)
            if result_code and result_code not in {"INFO-000", "INFO-1000"}:
                raise RuntimeError(f"서울 OpenAPI 결과 오류 {result_code}: {result_message}")
            path = save_service_page(
                rid=rid,
                key=key,
                service_info=service_info,
                start=start,
                end=end,
                status=status,
                body=body,
                result_code=result_code,
                result_message=result_message,
                total_count=page_total_count,
                row_count=row_count,
            )
            saved_pages.append({"start": start, "end": end, "row_count": row_count, "path": str(path)})
        except Exception as exc:
            log_failure(
                run_id_value=rid,
                source_id=service_info["source_id"],
                provider=PROVIDER_SEOUL,
                dataset_name=service_info["dataset_name"],
                failure_type=type(exc).__name__,
                failure_reason_ko=f"{service} {start}-{end} 페이지 수집 실패: {exc}",
                next_action_ko="실패 페이지만 재시도하고, 같은 오류가 반복되면 호출 제한 또는 서비스 상태를 확인한다.",
                request_url_redacted=redact_url(url, extra_values=[key]),
            )
            failed_pages.append({"start": start, "end": end, "error": type(exc).__name__})
        time.sleep(SLEEP_SECONDS)

    return {
        "service": service,
        "source_id": service_info["source_id"],
        "total_count": total_count,
        "expected_pages": total_pages,
        "saved_pages": len(saved_pages),
        "saved_rows": sum(int(page["row_count"]) for page in saved_pages),
        "failed_pages": failed_pages,
    }


def probe_localdata_file_urls(rid: str) -> list[dict[str, Any]]:
    results = []
    for target in LOCALDATA_FILE_PROBES:
        url = target["url"]
        try:
            status, body, _headers = http_get(url, timeout=30)
            path = write_raw(
                run_id_value=rid,
                source_id=target["source_id"],
                provider=target["provider"],
                dataset_name=target["dataset_name"],
                body=body,
                relative_path=f"{RUN_DATE}/localdata/probes/{Path(url).name or 'info'}.bin",
                request_url_redacted=redact_url(url),
                request_params={"probe_url": url},
                http_status=status,
                provider_result_code="PROBE_OK",
                provider_result_message="파일 URL 접근 성공",
                spatial_unit="전국",
                time_unit="수집일",
                source_period="2026-07-03 접근",
                quality_notes_ko="행안부 LocalData 파일 URL 접근성을 확인했다.",
            )
            results.append({"url": url, "status": status, "path": str(path), "bytes": len(body)})
        except Exception as exc:
            log_failure(
                run_id_value=rid,
                source_id=target["source_id"],
                provider=target["provider"],
                dataset_name=target["dataset_name"],
                failure_type=type(exc).__name__,
                failure_reason_ko=f"LocalData 파일 URL 직접 접근 실패: {exc}",
                next_action_ko=target["next_action_ko"],
                request_url_redacted=redact_url(url),
            )
            results.append({"url": url, "status": "failed", "error": f"{type(exc).__name__}: {exc}"})
    return results


def write_korean_log(rid: str, summary: dict[str, Any]) -> Path:
    log_path = RAW_ROOT / "run_logs" / f"{RUN_DATE}_real_estate_localdata_sources_ko.md"
    lines = [
        "# 2026-07-03 부동산 중개업소 및 LocalData 인허가 수집 기록",
        "",
        f"- 실행 ID: `{rid}`",
        f"- 작성 시각: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "## 수집 목적",
        "",
        "- 서울 부동산 입지 분석에서 주변 부동산 중개업소 분포와 영업상태를 보조 지표로 쓰기 위해 `landBizInfo` 원응답을 보존했다.",
        "- 창업/상권 입지에서 개별 업소 인허가, 영업상태, 폐업일자, 면적, 좌표를 검증용 원천으로 쓰기 위해 일반음식점과 휴게음식점 LocalData 원응답을 보존했다.",
        "- 서울 상권분석서비스 점포 집계는 이미 있으나, 개별 인허가 자료는 집계값의 설명 가능성과 폐업/개업 이력 검수에 필요하다.",
        "",
        "## 공식 문서 저장",
    ]
    for doc in summary["docs"]:
        lines.append(f"- {doc['url']} -> `{doc['path']}` ({doc['bytes']} bytes)")

    lines.extend(["", "## 서울 OpenAPI 전체 수집 결과"])
    for result in summary["services"]:
        fail_count = len(result.get("failed_pages", []))
        lines.append(
            f"- `{result['service']}`: total_count={result['total_count']}, "
            f"expected_pages={result.get('expected_pages', 0)}, saved_pages={result['saved_pages']}, "
            f"saved_rows={result['saved_rows']}, failed_pages={fail_count}"
        )

    lines.extend(["", "## LocalData 전국 파일 URL 확인"])
    for probe in summary["localdata_file_probes"]:
        if probe.get("status") == "failed":
            lines.append(f"- {probe['url']}: 실패 - {probe['error']}")
        else:
            lines.append(f"- {probe['url']}: 성공 - `{probe['path']}`")

    lines.extend(
        [
            "",
            "## 호출 및 전처리 주의사항",
            "",
            "- 서울 OpenAPI URL에는 키가 path에 들어가므로 manifest에는 키를 `<redacted>`로 기록했다.",
            "- `landBizInfo`는 OA-15550 문서의 실제 호출 서비스명이며, `OA-15550` 자체를 서비스명으로 호출하면 서버 오류가 난다.",
            "- `LOCALDATA_072404`와 `LOCALDATA_072405`는 서울시 전체 일반음식점/휴게음식점 서비스명이다.",
            "- 서울 일반음식점 문서에는 좌표계가 EPSG:5174 중부원점 TM으로 적혀 있어 원본 X/Y를 그대로 저장하고, 위경도 변환은 후처리에서 검증해야 한다.",
            "- 공공데이터포털/행안부 전국 파일 URL은 직접 접근 시 403이 발생했으므로 실패표에 남기고 서울 OpenAPI 원응답을 우선 채택했다.",
            "- 전화번호, 대표자명 등 개인정보성 필드는 원천에는 보존하되 외부 리포트/샘플 공개 시 마스킹이 필요하다.",
            "",
            "## 중복 정책",
            "",
            "- 부동산 중개업소: `SYS_REG_NO`, `REST_BRKR_INFO`, `LASTMODTS`, 응답 해시 기준으로 중복 여부를 본다.",
            "- LocalData 인허가: `MGTNO`, `LASTMODTS`, `UPDATEDT`, 응답 해시 기준으로 중복 여부를 본다.",
            "- 같은 원천을 다시 수집하면 날짜/실행 ID별 원응답을 남기고, 후처리 대표본은 최신 수집일과 해시 감사로 고른다.",
        ]
    )
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return log_path


def main() -> None:
    keys = parse_key_file()
    key = keys["seoul_key"]
    if not key:
        raise RuntimeError("key.md에서 서울 열린데이터광장 키를 찾지 못했습니다.")

    rid = run_id("seoul_real_estate_localdata")
    append_registry_rows()

    docs = collect_docs(rid)
    service_results = [collect_service(rid, key, service_info) for service_info in SERVICES]
    file_probe_results = probe_localdata_file_urls(rid)

    summary = {
        "run_id": rid,
        "docs": docs,
        "services": service_results,
        "localdata_file_probes": file_probe_results,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    json_log = RAW_ROOT / "run_logs" / f"{rid}.json"
    json_log.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    md_log = write_korean_log(rid, summary)

    print(json.dumps({**summary, "korean_log": str(md_log)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
