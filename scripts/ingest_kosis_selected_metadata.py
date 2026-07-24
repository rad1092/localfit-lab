from __future__ import annotations

import json
import urllib.parse
from datetime import datetime

from ingest_common import RAW_ROOT, http_get, log_failure, parse_key_file, redact_url, run_date, run_id, write_raw


RUN_DATE = run_date()
SOURCE_ID = "kosis_population_business_survival"
PROVIDER = "KOSIS"
BASE_LIST_URL = "https://kosis.kr/openapi/statisticsList.do"
BASE_DATA_URL = "https://kosis.kr/openapi/statisticsData.do"

PARENT_LISTS = [
    ("J2_6", "기업생멸행정통계"),
    ("J2_6_001", "11차 개정"),
    ("J2_6_001_001", "기업수 및 종사자수"),
    ("J2_6_001_002", "기업 생존 및 고성장ㆍ가젤기업"),
    ("J2_6_001_004", "지역별"),
    ("J2_6_001_005", "상용근로자 1인 이상"),
    ("A_7", "주민등록인구현황"),
]

TABLES = [
    ("101", "DT_1B040A3", "행정구역(시군구)별, 성별 인구수", "resident_population_sgg"),
    ("101", "DT_1B04006", "행정구역(시군구)별/1세별 주민등록인구", "resident_population_sgg_age1"),
    ("101", "DT_1B04005N", "행정구역(읍면동)별/5세별 주민등록인구(2011년~)", "resident_population_emd_age5"),
    ("101", "DT_6BD1132", "시군구별 산업대분류별 기업 수(활동/신생/소멸)", "business_count_sgg_industry"),
    ("101", "DT_6BD1135", "시군구별 산업대분류별 종사자 수(활동/신생/소멸)", "worker_count_sgg_industry"),
    ("101", "DT_6BD1102", "시도별 신생기업 생존율", "survival_sido"),
    ("101", "DT_6BD1109", "시도별 산업대분류별 신생기업 생존율", "survival_sido_industry"),
    ("101", "DT_2BD1103", "산업별 신생기업 생존율", "survival_industry"),
    ("101", "DT_1BD1101", "산업별 기업수(활동/신생/소멸)", "business_count_industry"),
    ("101", "DT_1BD1109", "산업별 종사자수(활동/신생/소멸)", "worker_count_industry"),
]

META_TYPES = ["TBL", "PRD", "ITM", "UNIT", "CMMT"]


def fetch_list(key: str, rid: str, parent: str, label: str) -> dict[str, object]:
    params = {
        "method": "getList",
        "apiKey": key,
        "vwCd": "MT_ZTITLE",
        "format": "json",
        "jsonVD": "Y",
        "parentListId": parent,
    }
    url = BASE_LIST_URL + "?" + urllib.parse.urlencode(params)
    status, body, _headers = http_get(url, timeout=60)
    path = write_raw(
        run_id_value=rid,
        source_id=SOURCE_ID,
        provider=PROVIDER,
        dataset_name=f"KOSIS 통계목록 {label}",
        body=body,
        relative_path=f"{RUN_DATE}/kosis/selected_lists/{parent}.json",
        request_url_redacted=redact_url(url, extra_values=[key]),
        request_params={**params, "apiKey": "<redacted>"},
        http_status=status,
        provider_result_code="statisticsList",
        provider_result_message=label,
        spatial_unit="통계목록",
        time_unit="수집일",
        quality_notes_ko="입지 분석용 KOSIS 후보 통계표 확정을 위해 저장한 공식 통계목록 원응답이다.",
    )
    return {"parent": parent, "label": label, "path": str(path), "bytes": len(body)}


def fetch_meta(key: str, rid: str, org_id: str, tbl_id: str, table_name: str, domain: str, meta_type: str) -> dict[str, object]:
    params = {
        "method": "getMeta",
        "apiKey": key,
        "format": "json",
        "jsonVD": "Y",
        "orgId": org_id,
        "tblId": tbl_id,
        "type": meta_type,
    }
    url = BASE_DATA_URL + "?" + urllib.parse.urlencode(params)
    status, body, _headers = http_get(url, timeout=60)
    provider_code = "getMeta"
    provider_msg = meta_type
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
        if isinstance(data, dict) and data.get("err"):
            provider_code = str(data.get("err"))
            provider_msg = str(data.get("errMsg", ""))
    except Exception:
        pass
    path = write_raw(
        run_id_value=rid,
        source_id=SOURCE_ID,
        provider=PROVIDER,
        dataset_name=f"KOSIS {table_name} getMeta {meta_type}",
        body=body,
        relative_path=f"{RUN_DATE}/kosis/selected_meta/{tbl_id}_{meta_type}.json",
        request_url_redacted=redact_url(url, extra_values=[key]),
        request_params={**params, "apiKey": "<redacted>"},
        http_status=status,
        provider_result_code=provider_code,
        provider_result_message=provider_msg,
        spatial_unit="KOSIS 통계표",
        time_unit="메타데이터",
        source_period="메타데이터",
        area_code_type="orgId+tblId+item/object/period code",
        quality_notes_ko=(
            f"{domain} 후보 통계표의 본자료 호출 파라미터를 확정하기 위한 메타데이터다. "
            "KOSIS 본자료는 통계표별 item/object/period 코드 확인 후 호출해야 한다."
        ),
    )
    return {
        "tbl_id": tbl_id,
        "meta_type": meta_type,
        "provider_code": provider_code,
        "provider_message": provider_msg,
        "path": str(path),
        "bytes": len(body),
    }


def main() -> None:
    key = parse_key_file()["kosis_key"]
    rid = run_id("kosis_selected_meta")
    list_results: list[dict[str, object]] = []
    meta_results: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []

    for parent, label in PARENT_LISTS:
        try:
            list_results.append(fetch_list(key, rid, parent, label))
        except Exception as exc:
            failures.append({"kind": "list", "id": parent, "error": type(exc).__name__})
            log_failure(
                run_id_value=rid,
                source_id=SOURCE_ID,
                provider=PROVIDER,
                dataset_name=f"KOSIS 통계목록 {label}",
                failure_type=type(exc).__name__,
                failure_reason_ko=f"KOSIS 통계목록 {parent} 수집 실패: {exc}",
                next_action_ko="parentListId와 KOSIS 호출 제한 여부를 확인한다.",
                request_url_redacted=BASE_LIST_URL,
            )

    for org_id, tbl_id, table_name, domain in TABLES:
        for meta_type in META_TYPES:
            try:
                meta_results.append(fetch_meta(key, rid, org_id, tbl_id, table_name, domain, meta_type))
            except Exception as exc:
                failures.append({"kind": "meta", "id": f"{tbl_id}:{meta_type}", "error": type(exc).__name__})
                log_failure(
                    run_id_value=rid,
                    source_id=SOURCE_ID,
                    provider=PROVIDER,
                    dataset_name=f"KOSIS {table_name} getMeta {meta_type}",
                    failure_type=type(exc).__name__,
                    failure_reason_ko=f"KOSIS {tbl_id} getMeta {meta_type} 실패: {exc}",
                    next_action_ko="통계표 ID, getMeta type, KOSIS 호출 제한 여부를 확인한다.",
                    request_url_redacted=BASE_DATA_URL,
                )

    summary = {
        "run_id": rid,
        "list_results": len(list_results),
        "meta_results": len(meta_results),
        "failures": failures,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    log_path = RAW_ROOT / "run_logs" / f"{rid}.json"
    log_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
