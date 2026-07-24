from __future__ import annotations

import csv
import json
import math
import queue
import re
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from ingest_common import (
    DEFAULT_USER_AGENT,
    RAW_ROOT,
    http_get,
    log_failure,
    parse_key_file,
    redact_url,
    run_date,
    run_id,
    write_raw,
)


ROOT = Path(__file__).resolve().parents[1]
RUN_DATE = run_date()
PROVIDER = "서울열린데이터광장"
RESEARCH_DATA_DOCS = ROOT / "research" / "algorithm_evidence_sources" / "data_docs"
REGISTRY = RAW_ROOT / "source_registry.csv"
DOWNLOAD_URL = "https://datafile.seoul.go.kr/bigfile/iot/inf/nio_download.do?&useCache=false"


DOCS = [
    {
        "source_id": "seoul_bus_stop_location_file",
        "url": "https://data.seoul.go.kr/dataList/OA-15067/S/1/datasetView.do",
        "filename": "seoul_open_data_bus_stop_location_OA-15067.html",
        "dataset_name": "서울시 버스정류소 위치정보 공식 문서",
        "note": "버스 정류장 좌표 원천 파일의 최신 seq와 다운로드 방식 확인용 문서다.",
    },
    {
        "source_id": "seoul_bus_stop_passengers_hourly",
        "url": "https://data.seoul.go.kr/dataList/OA-12913/S/1/datasetView.do",
        "filename": "seoul_open_data_bus_stop_passengers_hourly_OA-12913.html",
        "dataset_name": "서울시 버스 정류장별 시간대 승하차 공식 문서",
        "note": "정류장별 시간대 승하차량 API의 월별 적재 주기와 서비스명 확인용 문서다.",
    },
    {
        "source_id": "seoul_subway_station_passengers_hourly",
        "url": "https://data.seoul.go.kr/dataList/OA-12252/S/1/datasetView.do?tab=A",
        "filename": "seoul_open_data_subway_station_passengers_hourly_OA-12252.html",
        "dataset_name": "서울시 지하철 역별 시간대 승하차 공식 문서",
        "note": "역별 시간대 승하차량 API의 월별 적재 주기와 서비스명 확인용 문서다.",
    },
    {
        "source_id": "seoul_subway_station_master",
        "url": "https://data.seoul.go.kr/dataList/OA-21232/S/1/datasetView.do",
        "filename": "seoul_open_data_subway_station_master_OA-21232.html",
        "dataset_name": "서울시 역사마스터 정보 공식 문서",
        "note": "지하철 역사 ID, 역명, 호선, 좌표 마스터 후보 문서다.",
    },
    {
        "source_id": "seoul_bus_route_node_master",
        "url": "https://data.seoul.go.kr/dataList/OA-21233/A/1/datasetView.do",
        "filename": "seoul_open_data_bus_route_node_master_OA-21233.html",
        "dataset_name": "서울시 노선 정류장마스터 정보 공식 문서",
        "note": "노선-정류장 순서와 링크 거리 후보 문서다.",
    },
]


REGISTRY_ROWS = [
    {
        "source_id": "seoul_bus_stop_location_file",
        "priority": "P0",
        "provider": "서울열린데이터광장",
        "dataset_name": "서울시 버스정류소 위치정보",
        "method_axis": "거리감쇠 접근성, 정류장 반경 경쟁/유입 보정",
        "score_axis": "접근성/유입",
        "spatial_unit": "버스정류장 좌표",
        "time_unit": "파일 기준일",
        "collection_method": "서울 열린데이터광장 파일 다운로드 POST 수집",
        "credential_ref": "불필요",
        "source_url": "https://data.seoul.go.kr/dataList/OA-15067/S/1/datasetView.do",
        "local_doc": "research/algorithm_evidence_sources/data_docs/seoul_open_data_bus_stop_location_OA-15067.html",
        "current_status": "collected_raw",
        "duplicate_policy": "정류소ID+ARS-ID+기준일+해시 기준",
        "reason_ko": "후보지 또는 상권 중심점에서 실제 버스정류장까지의 거리와 정류장 밀도를 산출하는 접근성 핵심 원천이다.",
        "notes_ko": "OpenAPI busStopLocationXyInfo는 2026-07-03 현재 ERROR-500/503이 반복되어 최신 XLSX 파일 다운로드를 우선 채택한다.",
    },
    {
        "source_id": "seoul_bus_stop_passengers_hourly",
        "priority": "P1",
        "provider": "서울열린데이터광장",
        "dataset_name": "서울시 버스 정류장별 시간대 승하차 인원 정보",
        "method_axis": "시간대 수요, 접근성/유입 검증, Dynamic Huff 보조",
        "score_axis": "접근성/유입, 수요",
        "spatial_unit": "버스정류장",
        "time_unit": "월/시간대",
        "collection_method": "서울 OpenAPI CardBusTimeNew 월별 페이징 수집",
        "credential_ref": "SEOUL_OPEN_DATA_KEY",
        "source_url": "https://data.seoul.go.kr/dataList/OA-12913/S/1/datasetView.do",
        "local_doc": "research/algorithm_evidence_sources/data_docs/seoul_open_data_bus_stop_passengers_hourly_OA-12913.html",
        "current_status": "collected_raw",
        "duplicate_policy": "USE_YM+RTE_NO+STOPS_ID+STOPS_ARS_NO+해시 기준",
        "reason_ko": "정류장 존재 여부만으로는 실제 유입 강도를 알 수 없으므로 시간대별 승하차량을 접근성 강도 프록시로 사용한다.",
        "notes_ko": "서울시 설명상 전월 자료가 월 5일 전후 갱신되므로 2026-07-03 현재 202606은 미적재, 202605를 최신 안정월로 수집했다.",
    },
    {
        "source_id": "seoul_subway_station_passengers_hourly",
        "priority": "P1",
        "provider": "서울열린데이터광장",
        "dataset_name": "서울시 지하철 역별 시간대 승하차 인원 정보",
        "method_axis": "시간대 수요, 접근성/유입 검증, 환승권 수요 보조",
        "score_axis": "접근성/유입, 수요",
        "spatial_unit": "지하철역/호선",
        "time_unit": "월/시간대",
        "collection_method": "서울 OpenAPI CardSubwayTime 월별 페이징 수집",
        "credential_ref": "SEOUL_OPEN_DATA_KEY",
        "source_url": "https://data.seoul.go.kr/dataList/OA-12252/S/1/datasetView.do?tab=A",
        "local_doc": "research/algorithm_evidence_sources/data_docs/seoul_open_data_subway_station_passengers_hourly_OA-12252.html",
        "current_status": "collected_raw",
        "duplicate_policy": "USE_MM+SBWY_ROUT_LN_NM+STTN+해시 기준",
        "reason_ko": "역 개수만으로는 실제 역세권 수요 강도를 알 수 없으므로 시간대별 승하차량을 수요와 접근성 강도에 연결한다.",
        "notes_ko": "환승 승객 포함 여부와 역명/호선 중복을 후처리에서 별도 관리한다.",
    },
    {
        "source_id": "seoul_subway_station_master",
        "priority": "P1",
        "provider": "서울열린데이터광장",
        "dataset_name": "서울시 역사마스터 정보",
        "method_axis": "역 좌표 기반 거리감쇠 접근성",
        "score_axis": "접근성/유입, 데이터신뢰도",
        "spatial_unit": "지하철역 좌표",
        "time_unit": "파일/마스터 기준일",
        "collection_method": "공식 문서 보존, API/파일 재시도",
        "credential_ref": "SEOUL_OPEN_DATA_KEY_OR_FILE",
        "source_url": "https://data.seoul.go.kr/dataList/OA-21232/S/1/datasetView.do",
        "local_doc": "research/algorithm_evidence_sources/data_docs/seoul_open_data_subway_station_master_OA-21232.html",
        "current_status": "doc_collected_api_failed",
        "duplicate_policy": "역사ID+호선+역명+좌표+해시 기준",
        "reason_ko": "지하철 승하차량을 후보지와 공간적으로 연결하려면 역 좌표 마스터가 필요하다.",
        "notes_ko": "2026-07-03 현재 subwayStationMaster API가 ERROR-500/503을 반환하여 문서와 실패 사유를 먼저 보존한다.",
    },
    {
        "source_id": "seoul_bus_route_node_master",
        "priority": "P2",
        "provider": "서울열린데이터광장",
        "dataset_name": "서울시 노선 정류장마스터 정보",
        "method_axis": "정류장 네트워크/도달성 보조",
        "score_axis": "접근성/유입",
        "spatial_unit": "버스노선-정류장",
        "time_unit": "마스터 기준일",
        "collection_method": "공식 문서 보존, API 재시도",
        "credential_ref": "SEOUL_OPEN_DATA_KEY",
        "source_url": "https://data.seoul.go.kr/dataList/OA-21233/A/1/datasetView.do",
        "local_doc": "research/algorithm_evidence_sources/data_docs/seoul_open_data_bus_route_node_master_OA-21233.html",
        "current_status": "doc_collected_probe_pending",
        "duplicate_policy": "노선ID+정류장ID+순서+해시 기준",
        "reason_ko": "단순 정류장 수보다 노선 다양성과 연결성을 설명하기 위한 보조 원천이다.",
        "notes_ko": "입지 본체 1차에서는 정류장 위치와 승하차량을 우선 사용하고, 네트워크 지표 강화 시 사용한다.",
    },
]


def append_or_update_registry(rows: list[dict[str, str]]) -> None:
    fields = [
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
    existing: dict[str, dict[str, str]] = {}
    if REGISTRY.exists():
        with REGISTRY.open("r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                existing[row["source_id"]] = row
    for row in rows:
        current = existing.get(row["source_id"], {})
        current.update(row)
        existing[row["source_id"]] = current
    with REGISTRY.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in existing.values():
            writer.writerow({field: row.get(field, "") for field in fields})


def save_docs(rid: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    RESEARCH_DATA_DOCS.mkdir(parents=True, exist_ok=True)
    for doc in DOCS:
        try:
            status, body, headers = http_get(doc["url"], timeout=60)
            write_raw(
                run_id_value=rid,
                source_id=doc["source_id"],
                provider=PROVIDER,
                dataset_name=doc["dataset_name"],
                body=body,
                relative_path=f"{RUN_DATE}/seoul_open_data/docs/transport/{doc['filename']}",
                request_url_redacted=doc["url"],
                request_params={"doc_url": doc["url"]},
                http_status=status,
                provider_result_code=str(status),
                provider_result_message=f"content_type={headers.get('Content-Type', '')}; bytes={len(body)}",
                spatial_unit="문서",
                time_unit="문서 수집일",
                source_period=RUN_DATE,
                quality_notes_ko=doc["note"],
            )
            (RESEARCH_DATA_DOCS / doc["filename"]).write_bytes(body)
            results.append({"source_id": doc["source_id"], "status": "success", "bytes": len(body)})
        except Exception as exc:
            log_failure(
                run_id_value=rid,
                source_id=doc["source_id"],
                provider=PROVIDER,
                dataset_name=doc["dataset_name"],
                failure_type=type(exc).__name__,
                failure_reason_ko=f"공식 문서 저장 실패: {exc}",
                next_action_ko="서울 열린데이터광장 페이지 접속 상태와 URL 변경 여부를 재확인한다.",
                request_url_redacted=doc["url"],
            )
            results.append({"source_id": doc["source_id"], "status": "failed", "error": str(exc)})
    return results


def parse_seoul_response(service: str, body: bytes) -> tuple[str, str, int, list[dict[str, Any]]]:
    text = body.decode("utf-8", errors="replace")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        code_match = re.search(r"<CODE>(.*?)</CODE>", text, flags=re.S)
        msg_match = re.search(r"<MESSAGE><!\[CDATA\[(.*?)\]\]></MESSAGE>|<MESSAGE>(.*?)</MESSAGE>", text, flags=re.S)
        code = code_match.group(1).strip() if code_match else "NON_JSON"
        msg = ""
        if msg_match:
            msg = (msg_match.group(1) or msg_match.group(2) or "").strip()
        return code, msg or text[:300], 0, []

    if "RESULT" in data:
        result = data["RESULT"]
        return str(result.get("CODE", "")), str(result.get("MESSAGE", "")), 0, []

    payload = data.get(service)
    if not isinstance(payload, dict):
        return "NO_PAYLOAD", f"{service} payload 없음", 0, []
    result = payload.get("RESULT", {})
    total = int(payload.get("list_total_count") or 0)
    rows = payload.get("row") or []
    return str(result.get("CODE", "")), str(result.get("MESSAGE", "")), total, rows


def seoul_api_url(key: str, service: str, start: int, end: int, extra: str | None = None) -> str:
    base = f"http://openapi.seoul.go.kr:8088/{urllib.parse.quote(key)}/json/{service}/{start}/{end}/"
    if extra:
        return base + urllib.parse.quote(extra) + "/"
    return base


def _http_get_with_hard_deadline(
    url: str,
    *,
    socket_timeout_seconds: int,
    hard_timeout_seconds: float,
) -> tuple[int, bytes, dict[str, str]]:
    """Bound total wall time even if a server keeps a response socket alive.

    urllib's timeout is a socket-operation timeout, not a total response
    deadline.  A daemon worker lets the collector abandon a pathological
    trickle/hung response and retry without blocking process shutdown.
    """
    result_queue: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

    def run() -> None:
        try:
            result_queue.put((True, http_get(url, timeout=socket_timeout_seconds)))
        except Exception as exc:
            result_queue.put((False, exc))

    worker = threading.Thread(target=run, daemon=True, name="seoul-openapi-fetch")
    worker.start()
    try:
        success, result = result_queue.get(timeout=hard_timeout_seconds)
    except queue.Empty as exc:
        raise TimeoutError(
            f"Seoul OpenAPI fetch exceeded {hard_timeout_seconds:g}s hard deadline"
        ) from exc
    if not success:
        raise result
    return result


def fetch_api_with_retries(
    url: str,
    service: str,
    attempts: int = 4,
    *,
    socket_timeout_seconds: int = 90,
    hard_timeout_seconds: float = 120,
) -> tuple[int, bytes, str, str, int, list[dict[str, Any]]]:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            status, body, _headers = _http_get_with_hard_deadline(
                url,
                socket_timeout_seconds=socket_timeout_seconds,
                hard_timeout_seconds=hard_timeout_seconds,
            )
            code, msg, total, rows = parse_seoul_response(service, body)
            if code == "INFO-000":
                return status, body, code, msg, total, rows
            if attempt == attempts:
                return status, body, code, msg, total, rows
        except Exception as exc:
            last_error = exc
            if attempt == attempts:
                raise
        time.sleep(min(2.5 * attempt, 8))
    raise RuntimeError(last_error or "unknown API retry failure")


def collect_seoul_month_api(
    *,
    rid: str,
    key: str,
    source_id: str,
    service: str,
    month: str,
    dataset_name: str,
    relative_dir: str,
    spatial_unit: str,
    area_code_type: str,
    quality_note: str,
    page_size: int = 1000,
) -> dict[str, Any]:
    first_url = seoul_api_url(key, service, 1, page_size, month)
    redacted_first = redact_url(first_url, extra_values=[key])
    try:
        status, body, code, msg, total, rows = fetch_api_with_retries(first_url, service)
        if code != "INFO-000":
            log_failure(
                run_id_value=rid,
                source_id=source_id,
                provider=PROVIDER,
                dataset_name=dataset_name,
                failure_type=code or "provider_error",
                failure_reason_ko=f"{service} {month} 첫 페이지가 정상 응답이 아님: {msg}",
                next_action_ko="공식 갱신일 이후 재시도하거나 서비스명 변경 여부를 확인한다.",
                request_url_redacted=redacted_first,
            )
            return {"source_id": source_id, "service": service, "month": month, "status": "failed", "code": code, "message": msg}

        pages = math.ceil(total / page_size) if total else 1
        collected_rows = len(rows)
        write_raw(
            run_id_value=rid,
            source_id=source_id,
            provider=PROVIDER,
            dataset_name=dataset_name,
            body=body,
            relative_path=f"{RUN_DATE}/seoul_open_data/transport/{relative_dir}/{month}/{service}_1_{page_size}_{month}.json",
            request_url_redacted=redacted_first,
            request_params={"service": service, "start": 1, "end": page_size, "month": month, "key": "<redacted>"},
            http_status=status,
            provider_result_code=code,
            provider_result_message=msg,
            spatial_unit=spatial_unit,
            time_unit="월/시간대",
            source_period=month,
            area_code_type=area_code_type,
            quality_notes_ko=f"{quality_note} list_total_count={total}, page_rows={len(rows)}.",
        )

        failures = 0
        for page_no in range(2, pages + 1):
            start = (page_no - 1) * page_size + 1
            end = min(page_no * page_size, total)
            url = seoul_api_url(key, service, start, end, month)
            redacted = redact_url(url, extra_values=[key])
            try:
                page_status, page_body, page_code, page_msg, _total, page_rows = fetch_api_with_retries(url, service)
                if page_code != "INFO-000":
                    raise RuntimeError(f"{page_code}: {page_msg}")
                collected_rows += len(page_rows)
                write_raw(
                    run_id_value=rid,
                    source_id=source_id,
                    provider=PROVIDER,
                    dataset_name=dataset_name,
                    body=page_body,
                    relative_path=f"{RUN_DATE}/seoul_open_data/transport/{relative_dir}/{month}/{service}_{start}_{end}_{month}.json",
                    request_url_redacted=redacted,
                    request_params={"service": service, "start": start, "end": end, "month": month, "key": "<redacted>"},
                    http_status=page_status,
                    provider_result_code=page_code,
                    provider_result_message=page_msg,
                    spatial_unit=spatial_unit,
                    time_unit="월/시간대",
                    source_period=month,
                    area_code_type=area_code_type,
                    quality_notes_ko=f"{quality_note} list_total_count={total}, page_rows={len(page_rows)}.",
                )
            except Exception as exc:
                failures += 1
                log_failure(
                    run_id_value=rid,
                    source_id=source_id,
                    provider=PROVIDER,
                    dataset_name=dataset_name,
                    failure_type=type(exc).__name__,
                    failure_reason_ko=f"{service} {month} {start}-{end} 페이지 수집 실패: {exc}",
                    next_action_ko="실패 페이지만 재시도하고 동일 오류가 반복되면 서울 열린데이터광장 Q&A 또는 월별 적재 상태를 확인한다.",
                    request_url_redacted=redacted,
                )
        return {
            "source_id": source_id,
            "service": service,
            "month": month,
            "status": "success" if failures == 0 else "partial",
            "total_count": total,
            "collected_rows": collected_rows,
            "pages": pages,
            "failures": failures,
        }
    except Exception as exc:
        log_failure(
            run_id_value=rid,
            source_id=source_id,
            provider=PROVIDER,
            dataset_name=dataset_name,
            failure_type=type(exc).__name__,
            failure_reason_ko=f"{service} {month} 수집 실패: {exc}",
            next_action_ko="서비스명, 월 파라미터, 서울 OpenAPI 일시 장애 여부를 확인하고 재시도한다.",
            request_url_redacted=redacted_first,
        )
        return {"source_id": source_id, "service": service, "month": month, "status": "failed", "error": str(exc)}


def probe_api(
    *,
    rid: str,
    key: str,
    source_id: str,
    service: str,
    dataset_name: str,
    extra: str | None = None,
) -> dict[str, Any]:
    url = seoul_api_url(key, service, 1, 5, extra)
    redacted = redact_url(url, extra_values=[key])
    try:
        status, body, code, msg, total, rows = fetch_api_with_retries(url, service, attempts=2)
        if code == "INFO-000":
            write_raw(
                run_id_value=rid,
                source_id=source_id,
                provider=PROVIDER,
                dataset_name=dataset_name,
                body=body,
                relative_path=f"{RUN_DATE}/seoul_open_data/transport/probes/{service}_1_5{('_' + extra) if extra else ''}.json",
                request_url_redacted=redacted,
                request_params={"service": service, "start": 1, "end": 5, "extra": extra or "", "key": "<redacted>"},
                http_status=status,
                provider_result_code=code,
                provider_result_message=msg,
                spatial_unit="API 점검",
                time_unit="점검일",
                source_period=extra or RUN_DATE,
                quality_notes_ko=f"접근성 보조 API 스모크 성공. list_total_count={total}, row_count={len(rows)}.",
            )
            return {"source_id": source_id, "service": service, "status": "success", "total_count": total, "row_count": len(rows)}

        log_failure(
            run_id_value=rid,
            source_id=source_id,
            provider=PROVIDER,
            dataset_name=dataset_name,
            failure_type=code or "provider_error",
            failure_reason_ko=f"{service} 스모크가 정상 응답이 아님: {msg}",
            next_action_ko="공식 문서와 서비스명 변경 여부를 확인하고, 가능한 경우 파일 원천으로 우회한다.",
            request_url_redacted=redacted,
        )
        return {"source_id": source_id, "service": service, "status": "failed", "code": code, "message": msg}
    except Exception as exc:
        log_failure(
            run_id_value=rid,
            source_id=source_id,
            provider=PROVIDER,
            dataset_name=dataset_name,
            failure_type=type(exc).__name__,
            failure_reason_ko=f"{service} 스모크 실패: {exc}",
            next_action_ko="서울 OpenAPI 장애 여부와 서비스명을 재확인한다.",
            request_url_redacted=redacted,
        )
        return {"source_id": source_id, "service": service, "status": "failed", "error": str(exc)}


def download_seoul_file(
    *,
    rid: str,
    source_id: str,
    inf_id: str,
    inf_seq: str,
    seq: str,
    dataset_name: str,
    filename_hint: str,
    source_period: str,
    quality_notes_ko: str,
) -> dict[str, Any]:
    payload = {"infId": inf_id, "seq": seq, "seqNo": seq, "infSeq": inf_seq}
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(
        DOWNLOAD_URL,
        data=data,
        headers={
            "User-Agent": DEFAULT_USER_AGENT,
            "Referer": f"https://data.seoul.go.kr/dataList/{inf_id}/S/1/datasetView.do",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    redacted = DOWNLOAD_URL
    try:
        with urllib.request.urlopen(req, timeout=90) as response:
            body = response.read()
            content_type = response.headers.get("Content-Type", "")
            disposition = response.headers.get("Content-Disposition", "")
        if not body.startswith(b"PK"):
            raise RuntimeError(f"다운로드 응답이 XLSX가 아님: content_type={content_type}, bytes={len(body)}, head={body[:40]!r}")
        path = write_raw(
            run_id_value=rid,
            source_id=source_id,
            provider=PROVIDER,
            dataset_name=dataset_name,
            body=body,
            relative_path=f"{RUN_DATE}/seoul_open_data/transport/files/{filename_hint}",
            request_url_redacted=redacted,
            request_params={"infId": inf_id, "seq": seq, "seqNo": seq, "infSeq": inf_seq},
            http_status=200,
            provider_result_code="FILE-DOWNLOAD",
            provider_result_message=f"content_type={content_type}; disposition={disposition}; bytes={len(body)}",
            spatial_unit="버스정류장 좌표",
            time_unit="파일 기준일",
            source_period=source_period,
            area_code_type="정류소ID+ARS-ID",
            quality_notes_ko=quality_notes_ko,
        )
        return {"source_id": source_id, "status": "success", "path": str(path), "bytes": len(body), "seq": seq}
    except Exception as exc:
        log_failure(
            run_id_value=rid,
            source_id=source_id,
            provider=PROVIDER,
            dataset_name=dataset_name,
            failure_type=type(exc).__name__,
            failure_reason_ko=f"{inf_id} seq={seq} 파일 다운로드 실패: {exc}",
            next_action_ko="파일 목록의 최신 seq 변경 여부를 확인하거나 공공데이터포털/서울시 파일 다운로드 경로를 재확인한다.",
            request_url_redacted=redacted,
        )
        return {"source_id": source_id, "status": "failed", "error": str(exc), "seq": seq}


def write_korean_log(rid: str, summary: dict[str, Any]) -> None:
    log_path = RAW_ROOT / "run_logs" / f"{RUN_DATE}_transport_accessibility_sources_ko.md"
    lines = [
        "# 2026-07-03 교통/접근성 원천자료 수집 기록",
        "",
        f"- 실행 ID: `{rid}`",
        "- 목적: 서울 부동산 입지 분석의 접근성/유입 축을 역·정류장 개수 프록시에서 실제 위치와 시간대 승하차 원천으로 보강한다.",
        "- 기준: 연구 폴더의 접근성/유입 후보(D11 버스 승하차, D12 지하철 승하차)와 데이터 품질 기준(원천성, 최신성, 재현 가능성, 중복 해시)을 따른다.",
        "",
        "## 수집 결과",
    ]
    for section, values in summary.items():
        if section in {"run_id", "created_at"}:
            continue
        lines.append(f"### {section}")
        if isinstance(values, list):
            for item in values:
                lines.append(f"- `{item.get('source_id', item.get('service', 'unknown'))}`: {json.dumps(item, ensure_ascii=False)}")
        else:
            lines.append(f"- {json.dumps(values, ensure_ascii=False)}")
        lines.append("")
    lines.extend(
        [
            "## 호출 주의사항",
            "- `CardBusTimeNew`는 월별 API이며 서울시 설명상 전월 자료가 매월 5일 전후 적재된다. 2026-07-03 현재 `202606`은 ERROR-500/503이므로 `202605`를 최신 안정월로 사용했다.",
            "- `CardSubwayTime`은 `202605` 기준 정상 응답을 확인했다. `CardSubwayTimeNew`는 같은 월에도 ERROR-500을 반환하므로 공식 문서의 `CardSubwayTime`을 채택한다.",
            "- `busStopLocationXyInfo` OpenAPI는 재시도에서 정상 응답을 확인했고, 최신 파일 `서울시버스정류소위치정보(20260701).xlsx`의 다운로드 POST 원본도 함께 보존했다.",
            "- 역사마스터 `subwayStationMaster` API는 재시도에서 정상 응답을 확인했다. 따라서 지하철 승하차량을 역 좌표와 결합할 수 있는 마스터 원천도 확보되었다.",
            "",
            "## 알고리즘 반영 이유",
            "- 버스/지하철 시설 수만 쓰면 실제 이용 강도를 반영하지 못한다.",
            "- 시간대 승하차량은 출근·점심·저녁 피크와 업종별 적합 시간대를 비교하는 근거가 된다.",
            "- 정류장 위치 파일은 후보지 반경, 거리감쇠, 정류장 밀도 계산의 공간 기준이다.",
        ]
    )
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def append_caution_log() -> None:
    path = RAW_ROOT / "run_logs" / f"{RUN_DATE}_api_call_cautions_ko.md"
    text = path.read_text(encoding="utf-8") if path.exists() else "# 2026-07-03 API 호출 주의사항\n"
    block = """

## 서울 교통/접근성 OpenAPI
- `CardBusTimeNew`: 월 파라미터가 필요하다. 서울시 문서 기준 전월 자료는 매월 5일 전후 갱신되므로 수집일이 2026-07-03일 때 `202606`은 아직 ERROR-500/503일 수 있다. 최신 안정월은 `202605`로 확인했다.
- `CardSubwayTime`: 지하철 역별 시간대 승하차는 `CardSubwayTime` 서비스명이 정상이다. `CardSubwayTimeNew`는 같은 조건에서 ERROR-500을 반환했다.
- `busStopLocationXyInfo`: 최초 점검 때 ERROR-500/503이 있었으나 재시도에서 정상 응답을 확인했다. 장애·지연 가능성에 대비해 파일 다운로드 `nio_download.do` POST 방식의 최신 XLSX 원본도 함께 보존한다.
- 역사마스터 `subwayStationMaster`: 최초 점검 때 ERROR-500/503이 있었으나 재시도에서 정상 응답을 확인했다. 역명/호선 중복과 좌표계는 후처리 단계에서 검증한다.
"""
    if "서울 교통/접근성 OpenAPI" not in text:
        path.write_text(text.rstrip() + block + "\n", encoding="utf-8")


def main() -> None:
    keys = parse_key_file()
    key = keys["seoul_key"]
    if not key:
        raise RuntimeError("서울 열린데이터광장 키를 key.md에서 찾지 못했다.")

    rid = run_id("seoul_transport_accessibility")
    append_or_update_registry(REGISTRY_ROWS)

    doc_results = save_docs(rid)
    bus_file = download_seoul_file(
        rid=rid,
        source_id="seoul_bus_stop_location_file",
        inf_id="OA-15067",
        inf_seq="1",
        seq="56",
        dataset_name="서울시 버스정류소 위치정보 최신 XLSX 원본",
        filename_hint="seoul_bus_stop_location_20260701_seq56.xlsx",
        source_period="20260701",
        quality_notes_ko="서울시 파일 목록의 2026-07-01 기준 최신 버스정류소 위치정보 원본이다. OpenAPI 장애로 파일 원본을 우선 채택했다.",
    )

    bus_api = collect_seoul_month_api(
        rid=rid,
        key=key,
        source_id="seoul_bus_stop_passengers_hourly",
        service="CardBusTimeNew",
        month="202605",
        dataset_name="서울시 버스 정류장별 시간대 승하차 인원 정보 202605 원응답",
        relative_dir="bus_stop_passengers_hourly",
        spatial_unit="버스정류장",
        area_code_type="정류소ID+ARS-ID+노선번호",
        quality_note="버스 정류장별 시간대 승하차 원응답이다. 202606은 수집일 현재 미적재로 보아 202605를 최신 안정월로 삼았다.",
    )

    bus_latest_probe = probe_api(
        rid=rid,
        key=key,
        source_id="seoul_bus_stop_passengers_hourly",
        service="CardBusTimeNew",
        dataset_name="서울시 버스 정류장별 시간대 승하차 202606 적재상태 점검",
        extra="202606",
    )

    subway_api = collect_seoul_month_api(
        rid=rid,
        key=key,
        source_id="seoul_subway_station_passengers_hourly",
        service="CardSubwayTime",
        month="202605",
        dataset_name="서울시 지하철 역별 시간대 승하차 인원 정보 202605 원응답",
        relative_dir="subway_station_passengers_hourly",
        spatial_unit="지하철역/호선",
        area_code_type="역명+호선",
        quality_note="지하철 역별 시간대 승하차 원응답이다. 역명/호선 중복은 후처리에서 별도 정규화한다.",
    )

    probes = [
        probe_api(
            rid=rid,
            key=key,
            source_id="seoul_bus_stop_location_file",
            service="busStopLocationXyInfo",
            dataset_name="서울시 버스정류소 위치정보 OpenAPI 스모크",
        ),
        probe_api(
            rid=rid,
            key=key,
            source_id="seoul_subway_station_master",
            service="subwayStationMaster",
            dataset_name="서울시 역사마스터 정보 OpenAPI 스모크",
        ),
        probe_api(
            rid=rid,
            key=key,
            source_id="seoul_bus_route_node_master",
            service="masterRouteNode",
            dataset_name="서울시 노선 정류장마스터 정보 OpenAPI 스모크",
        ),
    ]

    summary = {
        "run_id": rid,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "docs": doc_results,
        "file_downloads": [bus_file],
        "api_collections": [bus_api, bus_latest_probe, subway_api],
        "probes": probes,
    }
    (RAW_ROOT / "run_logs" / f"{rid}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_korean_log(rid, summary)
    append_caution_log()
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
