from __future__ import annotations

import json
import sys
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any

from ingest_common import ROOT, http_get, log_failure, parse_key_file, redact_url, run_date, run_id, write_raw


RUN_DATE = run_date()
PROVIDER = "소상공인시장진흥공단/공공데이터포털"
SOURCE_ID = "sbdc_store_info"
DOC_SOURCE_ID = "sbdc_store_info_docs"


DOCS = [
    (
        "상가업소 정보 파일 데이터포털 문서",
        "research/algorithm_evidence_sources/data_docs/data_go_kr_sbiz_store_info.html",
        f"{RUN_DATE}/sbdc/docs/data_go_kr_sbiz_store_info_{RUN_DATE}.html",
        "상가업소 파일 데이터의 제공 범위와 갱신 기준을 확인하기 위한 공식 문서 사본이다.",
    ),
    (
        "상가업소 정보 API 데이터포털 문서",
        "research/algorithm_evidence_sources/data_docs/data_go_kr_sbiz_store_info_api.html",
        f"{RUN_DATE}/sbdc/docs/data_go_kr_sbiz_store_info_api_{RUN_DATE}.html",
        "반경/행정동/업종별 상가업소 API 호출 파라미터와 응답 구조를 확인하기 위한 공식 문서 사본이다.",
    ),
    (
        "소상공인365 상권분석 데이터포털 문서",
        "research/algorithm_evidence_sources/data_docs/data_go_kr_sbiz365_market_analysis.html",
        f"{RUN_DATE}/sbdc/docs/data_go_kr_sbiz365_market_analysis_{RUN_DATE}.html",
        "소상공인365 상권분석 파일 데이터의 외부 비교 가능성을 확인하기 위한 공식 문서 사본이다.",
    ),
]


SAMPLES = [
    {
        "name": "SBDC 상가업소 API 강남역 반경 500m 전체 업종 샘플",
        "path_suffix": "gangnam_station_radius500_all.json",
        "params": {
            "pageNo": "1",
            "numOfRows": "20",
            "radius": "500",
            "cx": "127.027610",
            "cy": "37.497942",
            "type": "json",
        },
        "note": "강남역 중심 반경 500m 전체 업종 상가업소 API 샘플이다. 반경 경쟁점 조회 가능성 검증에 사용한다.",
    },
    {
        "name": "SBDC 상가업소 API 강남역 반경 500m 부동산 업종 샘플",
        "path_suffix": "gangnam_station_radius500_real_estate.json",
        "params": {
            "pageNo": "1",
            "numOfRows": "20",
            "radius": "500",
            "cx": "127.027610",
            "cy": "37.497942",
            "indsLclsCd": "L1",
            "type": "json",
        },
        "note": "강남역 중심 반경 500m 중 SBDC 대분류 L1 부동산 업종만 조회한 샘플이다. 부동산 입지 분석의 직접 경쟁점 조회 가능성 검증에 사용한다.",
    },
]


def parse_json(body: bytes) -> dict[str, Any]:
    return json.loads(body.decode("utf-8-sig", errors="replace"))


def record_docs(rid: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for dataset_name, src, dst, note in DOCS:
        path = ROOT / src
        if not path.exists():
            log_failure(
                run_id_value=rid,
                source_id=DOC_SOURCE_ID,
                provider=PROVIDER,
                dataset_name=dataset_name,
                failure_type="MissingResearchDoc",
                failure_reason_ko=f"기존 research 문서 사본을 찾지 못했다: {src}",
                next_action_ko="공공데이터포털 공식 문서를 다시 다운로드해 data_docs와 raw_ingest에 저장한다.",
                request_url_redacted="",
            )
            results.append({"dataset_name": dataset_name, "status": "missing", "source": src})
            continue
        body = path.read_bytes()
        raw_path = write_raw(
            run_id_value=rid,
            source_id=DOC_SOURCE_ID,
            provider=PROVIDER,
            dataset_name=dataset_name,
            body=body,
            relative_path=dst,
            request_url_redacted="기존 research 공식문서 사본",
            request_params={},
            http_status="local_copy",
            provider_result_code="local_copy",
            provider_result_message=f"bytes={len(body)}",
            spatial_unit="공식 문서",
            time_unit="문서 수집일",
            source_period="2026-06-30 문서 사본",
            quality_notes_ko=note,
        )
        results.append({"dataset_name": dataset_name, "status": "success", "raw_path": str(raw_path), "bytes": len(body)})
    return results


def call_sample(rid: str, endpoint: str, key: str, sample: dict[str, Any]) -> dict[str, Any]:
    params = {"ServiceKey": key, **sample["params"]}
    url = endpoint.rstrip("/") + "/storeListInRadius?" + urllib.parse.urlencode(params)
    status, body, _headers = http_get(url, timeout=60)
    data = parse_json(body)
    result_code = str(data.get("header", {}).get("resultCode") or data.get("resultCode") or "")
    result_msg = str(data.get("header", {}).get("resultMsg") or data.get("resultMsg") or "")
    total_count = str(data.get("body", {}).get("totalCount") or data.get("totalCount") or "")
    if result_code and result_code not in {"00", "0", "NORMAL_CODE"}:
        raise RuntimeError(f"SBDC API 오류: {result_code} {result_msg}")
    raw_path = write_raw(
        run_id_value=rid,
        source_id=SOURCE_ID,
        provider=PROVIDER,
        dataset_name=sample["name"],
        body=body,
        relative_path=f"{RUN_DATE}/sbdc/store_api_samples/{rid}_{sample['path_suffix']}",
        request_url_redacted=redact_url(url),
        request_params={**sample["params"], "ServiceKey": "<redacted>"},
        http_status=status,
        provider_result_code=result_code,
        provider_result_message=f"{result_msg}; totalCount={total_count}",
        spatial_unit="반경 500m",
        time_unit="실행시점",
        source_period="2026-07-03",
        area_code_type="SBDC 업종코드+WGS84 좌표",
        quality_notes_ko=sample["note"],
    )
    return {"dataset_name": sample["name"], "status": "success", "raw_path": str(raw_path), "result_code": result_code, "total_count": total_count}


def main() -> int:
    rid = run_id("sbdc_store_api")
    keys = parse_key_file()
    summary: dict[str, Any] = {
        "run_id": rid,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "docs": record_docs(rid),
        "samples": [],
    }
    endpoint = keys.get("sbdc_endpoint", "")
    key = keys.get("sbdc_key", "")
    if not endpoint or not key:
        log_failure(
            run_id_value=rid,
            source_id=SOURCE_ID,
            provider=PROVIDER,
            dataset_name="SBDC 상가업소 API 샘플",
            failure_type="MissingCredential",
            failure_reason_ko="SBDC endpoint 또는 service key를 key.md에서 찾지 못했다.",
            next_action_ko="공공데이터포털 SBDC 상가업소 API 인증키와 엔드포인트를 key.md에 추가한다.",
            request_url_redacted="",
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 1

    for sample in SAMPLES:
        try:
            summary["samples"].append(call_sample(rid, endpoint, key, sample))
        except Exception as exc:
            params = {"ServiceKey": "<redacted>", **sample["params"]}
            url = endpoint.rstrip("/") + "/storeListInRadius?" + urllib.parse.urlencode(params)
            log_failure(
                run_id_value=rid,
                source_id=SOURCE_ID,
                provider=PROVIDER,
                dataset_name=sample["name"],
                failure_type=type(exc).__name__,
                failure_reason_ko=f"SBDC 상가업소 API 샘플 호출 실패: {exc}",
                next_action_ko="ServiceKey 인코딩, 반경 파라미터, 데이터포털 승인 상태를 확인한 뒤 재시도한다.",
                request_url_redacted=redact_url(url),
            )
            summary["samples"].append({"dataset_name": sample["name"], "status": "failed", "error": repr(exc)})

    log_path = ROOT / "datacorpus" / "_raw_ingest" / "run_logs" / f"{rid}.json"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if all(item["status"] == "success" for item in summary["samples"]) else 2


if __name__ == "__main__":
    sys.exit(main())
