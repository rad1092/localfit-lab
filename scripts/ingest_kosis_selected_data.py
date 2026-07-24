from __future__ import annotations

import csv
import json
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
SOURCE_ID = "kosis_population_business_survival"
PROVIDER = "KOSIS"
BASE_URL = "https://kosis.kr/openapi/Param/statisticsParameterData.do"
META_DIR = RAW_ROOT / RUN_DATE / "kosis" / "selected_meta"
OUT_DIR = f"{RUN_DATE}/kosis/selected_data"


CALLS: list[dict[str, Any]] = [
    {
        "name": "resident_population_sgg_60m",
        "org_id": "101",
        "tbl_id": "DT_1B040A3",
        "table_name": "행정구역(시군구)별 성별 인구수",
        "prd_se": "M",
        "period_mode": "latest_count",
        "new_est_prd_cnt": 60,
        "obj": {"objL1": "11*"},
        "itm_id": "ALL",
        "spatial_unit": "서울특별시+25개 자치구",
        "time_unit": "월",
        "source_period": "최근 60개월",
        "quality_notes_ko": "서울 및 자치구 인구의 장기 추세를 보기 위해 최근 60개월 월별 성/총인구 원응답을 저장한다.",
    },
    {
        "name": "resident_population_sgg_age1_latest",
        "org_id": "101",
        "tbl_id": "DT_1B04006",
        "table_name": "행정구역(시군구)별/1세별 주민등록인구",
        "prd_se": "M",
        "period_mode": "latest_count",
        "new_est_prd_cnt": 1,
        "obj": {"objL1": "11*", "objL2": "ALL"},
        "itm_id": "ALL",
        "spatial_unit": "서울특별시+25개 자치구",
        "time_unit": "월",
        "source_period": "최신 1개월",
        "quality_notes_ko": "자치구별 연령 세분 구조는 셀 수가 커서 최신월 원응답을 저장하고, 추세는 총인구 표와 결합해 해석한다.",
    },
    {
        "name": "resident_population_seoul_age1_60m",
        "org_id": "101",
        "tbl_id": "DT_1B04006",
        "table_name": "행정구역(시군구)별/1세별 주민등록인구",
        "prd_se": "M",
        "period_mode": "latest_count",
        "new_est_prd_cnt": 60,
        "obj": {"objL1": "11", "objL2": "ALL"},
        "itm_id": "ALL",
        "spatial_unit": "서울특별시",
        "time_unit": "월",
        "source_period": "최근 60개월",
        "quality_notes_ko": "서울 전체 연령 구조의 시간 변화를 보기 위해 서울 집계 기준 최근 60개월 1세별 원응답을 저장한다.",
    },
    {
        "name": "resident_population_emd_age5_latest",
        "org_id": "101",
        "tbl_id": "DT_1B04005N",
        "table_name": "행정구역(읍면동)별/5세별 주민등록인구",
        "prd_se": "M",
        "period_mode": "latest_count",
        "new_est_prd_cnt": 1,
        "obj": {"objL1": "11*", "objL2": "ALL"},
        "itm_id": "ALL",
        "spatial_unit": "서울특별시+자치구+행정동",
        "time_unit": "월",
        "source_period": "최신 1개월",
        "quality_notes_ko": "상권 주변 생활권의 연령 분포 보강을 위해 행정동 5세별 최신월 원응답을 저장한다.",
    },
    {
        "name": "business_count_sgg_industry_all_years",
        "org_id": "101",
        "tbl_id": "DT_6BD1132",
        "table_name": "시군구별 산업대분류별 기업 수(활동/신생/소멸)",
        "prd_se": "Y",
        "period_mode": "full_meta_range",
        "obj": {"objL1": "11*", "objL2": "ALL"},
        "itm_id": "ALL",
        "spatial_unit": "서울특별시+25개 자치구",
        "time_unit": "년",
        "source_period": "메타데이터 전체 기간",
        "quality_notes_ko": "상권 경쟁/성장 판단의 외부 기준으로 자치구-산업대분류별 활동/신생/소멸 기업 수와 비율 전체 기간을 저장한다.",
    },
    {
        "name": "worker_count_sgg_industry_all_years",
        "org_id": "101",
        "tbl_id": "DT_6BD1135",
        "table_name": "시군구별 산업대분류별 종사자 수(활동/신생/소멸)",
        "prd_se": "Y",
        "period_mode": "full_meta_range",
        "obj": {"objL1": "11*", "objL2": "ALL"},
        "itm_id": "ALL",
        "spatial_unit": "서울특별시+25개 자치구",
        "time_unit": "년",
        "source_period": "메타데이터 전체 기간",
        "quality_notes_ko": "자치구-산업대분류별 고용 규모와 신생/소멸 구조를 확인하기 위해 전체 기간 원응답을 저장한다.",
    },
    {
        "name": "survival_sido_all_years",
        "org_id": "101",
        "tbl_id": "DT_6BD1102",
        "table_name": "시도별 신생기업 생존율",
        "prd_se": "Y",
        "period_mode": "full_meta_range",
        "obj": {"objL1": "ALL"},
        "itm_id": "ALL",
        "spatial_unit": "전국 시도",
        "time_unit": "년",
        "source_period": "메타데이터 전체 기간",
        "quality_notes_ko": "서울 생존율을 다른 시도와 비교하기 위한 기준선으로 시도 전체 기간 원응답을 저장한다.",
    },
    {
        "name": "survival_sido_industry_seoul_all_years",
        "org_id": "101",
        "tbl_id": "DT_6BD1109",
        "table_name": "시도별 산업대분류별 신생기업 생존율",
        "prd_se": "Y",
        "period_mode": "full_meta_range",
        "obj": {"objL1": "11", "objL2": "ALL"},
        "itm_id": "ALL",
        "spatial_unit": "서울특별시",
        "time_unit": "년",
        "source_period": "메타데이터 전체 기간",
        "quality_notes_ko": "서울 업종대분류별 생존율 보정을 위해 서울만 전체 기간 원응답으로 저장한다.",
    },
    {
        "name": "survival_industry_all_years",
        "org_id": "101",
        "tbl_id": "DT_2BD1103",
        "table_name": "산업별 신생기업 생존율",
        "prd_se": "Y",
        "period_mode": "full_meta_range",
        "obj": {"objL1": "ALL"},
        "itm_id": "ALL",
        "spatial_unit": "전국",
        "time_unit": "년",
        "source_period": "메타데이터 전체 기간",
        "quality_notes_ko": "서울 세부 상권에 직접 대응하지 않는 전국 업종별 생존 기준선으로 저장한다.",
    },
    {
        "name": "business_count_industry_all_years",
        "org_id": "101",
        "tbl_id": "DT_1BD1101",
        "table_name": "산업별 기업수(활동/신생/소멸)",
        "prd_se": "Y",
        "period_mode": "full_meta_range",
        "obj": {"objL1": "ALL"},
        "itm_id": "ALL",
        "spatial_unit": "전국",
        "time_unit": "년",
        "source_period": "메타데이터 전체 기간",
        "quality_notes_ko": "업종 구조와 활동/신생/소멸 기준선을 만들기 위해 전국 산업별 기업 수 전체 기간을 저장한다.",
    },
    {
        "name": "worker_count_industry_all_years",
        "org_id": "101",
        "tbl_id": "DT_1BD1109",
        "table_name": "산업별 종사자수(활동/신생/소멸)",
        "prd_se": "Y",
        "period_mode": "full_meta_range",
        "obj": {"objL1": "ALL"},
        "itm_id": "ALL",
        "spatial_unit": "전국",
        "time_unit": "년",
        "source_period": "메타데이터 전체 기간",
        "quality_notes_ko": "업종별 고용 규모와 안정성 기준선을 만들기 위해 전국 산업별 종사자 수 전체 기간을 저장한다.",
    },
]


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def period_range(tbl_id: str, prd_se: str) -> tuple[str, str]:
    meta = load_json(META_DIR / f"{tbl_id}_PRD.json")
    if not isinstance(meta, list):
        raise RuntimeError(f"{tbl_id} PRD 메타가 배열이 아닙니다.")
    candidates = []
    for row in meta:
        label = str(row.get("PRD_SE", ""))
        if (prd_se == "Y" and label == "년") or (prd_se == "M" and label == "월"):
            candidates.append(row)
    if not candidates:
        raise RuntimeError(f"{tbl_id} PRD 메타에서 {prd_se} 수록주기를 찾지 못했습니다.")
    row = candidates[0]
    start = str(row.get("STRT_PRD_DE", "")).replace(".", "")
    end = str(row.get("END_PRD_DE", "")).replace(".", "")
    if not start or not end:
        raise RuntimeError(f"{tbl_id} PRD 메타에 시작/종료 수록시점이 없습니다.")
    return start, end


def build_params(key: str, call: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {
        "method": "getList",
        "apiKey": key,
        "format": "json",
        "jsonVD": "Y",
        "orgId": call["org_id"],
        "tblId": call["tbl_id"],
        "itmId": call["itm_id"],
        "prdSe": call["prd_se"],
        "prdInterval": "1",
    }
    params.update(call["obj"])
    if call["period_mode"] == "latest_count":
        params["newEstPrdCnt"] = str(call["new_est_prd_cnt"])
    elif call["period_mode"] == "full_meta_range":
        start, end = period_range(call["tbl_id"], call["prd_se"])
        params["startPrdDe"] = start
        params["endPrdDe"] = end
    else:
        raise RuntimeError(f"알 수 없는 기간 모드: {call['period_mode']}")
    return params


def response_summary(body: bytes) -> dict[str, Any]:
    text = body.decode("utf-8", errors="replace")
    data = json.loads(text)
    if isinstance(data, dict) and data.get("err"):
        return {
            "ok": False,
            "provider_result_code": str(data.get("err", "")),
            "provider_result_message": str(data.get("errMsg", "")),
            "row_count": 0,
            "first_period": "",
            "last_period": "",
        }
    if not isinstance(data, list):
        return {
            "ok": False,
            "provider_result_code": "unexpected_shape",
            "provider_result_message": type(data).__name__,
            "row_count": 0,
            "first_period": "",
            "last_period": "",
        }
    periods = sorted({str(row.get("PRD_DE", "")) for row in data if row.get("PRD_DE")})
    return {
        "ok": True,
        "provider_result_code": "getList",
        "provider_result_message": "success",
        "row_count": len(data),
        "first_period": periods[0] if periods else "",
        "last_period": periods[-1] if periods else "",
    }


def write_call_plan(calls: list[dict[str, Any]], key: str) -> Path:
    path = RAW_ROOT / RUN_DATE / "kosis" / "kosis_selected_data_call_plan.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "name",
        "tbl_id",
        "table_name",
        "endpoint",
        "params_redacted_json",
        "spatial_unit",
        "time_unit",
        "source_period",
        "reason_ko",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for call in calls:
            params = build_params(key, call)
            safe_params = {**params, "apiKey": "<redacted>"}
            writer.writerow(
                {
                    "name": call["name"],
                    "tbl_id": call["tbl_id"],
                    "table_name": call["table_name"],
                    "endpoint": BASE_URL,
                    "params_redacted_json": json.dumps(safe_params, ensure_ascii=False, sort_keys=True),
                    "spatial_unit": call["spatial_unit"],
                    "time_unit": call["time_unit"],
                    "source_period": call["source_period"],
                    "reason_ko": call["quality_notes_ko"],
                }
            )
    return path


def fetch_call(key: str, rid: str, call: dict[str, Any]) -> dict[str, Any]:
    params = build_params(key, call)
    url = BASE_URL + "?" + urllib.parse.urlencode(params, safe="*+")
    status, body, _headers = http_get(url, timeout=120)
    summary = response_summary(body)
    if not summary["ok"]:
        log_failure(
            run_id_value=rid,
            source_id=SOURCE_ID,
            provider=PROVIDER,
            dataset_name=f"KOSIS {call['table_name']} 본자료 {call['name']}",
            failure_type=str(summary["provider_result_code"]),
            failure_reason_ko=f"KOSIS 본자료 호출 실패: {summary['provider_result_message']}",
            next_action_ko="선정 표의 OBJ_ID, itmId, 기간 파라미터와 4만 셀 제한을 다시 확인한다.",
            request_url_redacted=redact_url(url, extra_values=[key]),
        )
        return {**summary, "name": call["name"], "tbl_id": call["tbl_id"], "path": "", "bytes": len(body)}

    period_note = f"{summary['first_period']}~{summary['last_period']}" if summary["first_period"] else call["source_period"]
    path = write_raw(
        run_id_value=rid,
        source_id=SOURCE_ID,
        provider=PROVIDER,
        dataset_name=f"KOSIS {call['table_name']} 본자료 {call['name']}",
        body=body,
        relative_path=f"{OUT_DIR}/{call['tbl_id']}_{call['name']}.json",
        request_url_redacted=redact_url(url, extra_values=[key]),
        request_params={**params, "apiKey": "<redacted>"},
        http_status=status,
        provider_result_code=str(summary["provider_result_code"]),
        provider_result_message=f"rows={summary['row_count']}",
        spatial_unit=call["spatial_unit"],
        time_unit=call["time_unit"],
        source_period=period_note,
        area_code_type="KOSIS OBJ_ID/ITM_ID/PRD_DE",
        quality_notes_ko=call["quality_notes_ko"],
    )
    return {**summary, "name": call["name"], "tbl_id": call["tbl_id"], "path": str(path), "bytes": len(body)}


def main() -> None:
    key = parse_key_file()["kosis_key"]
    rid = run_id("kosis_selected_data")
    call_plan_path = write_call_plan(CALLS, key)
    results = []
    failures = []

    for call in CALLS:
        try:
            result = fetch_call(key, rid, call)
            results.append(result)
            if not result["ok"]:
                failures.append(result)
        except Exception as exc:
            failures.append({"name": call["name"], "tbl_id": call["tbl_id"], "error": type(exc).__name__, "message": str(exc)})
            log_failure(
                run_id_value=rid,
                source_id=SOURCE_ID,
                provider=PROVIDER,
                dataset_name=f"KOSIS {call['table_name']} 본자료 {call['name']}",
                failure_type=type(exc).__name__,
                failure_reason_ko=f"KOSIS 본자료 호출 중 예외 발생: {exc}",
                next_action_ko="통계표별 필수 차원과 KOSIS 호출 제한을 재확인한다.",
                request_url_redacted=BASE_URL,
            )

    total_rows = sum(int(r.get("row_count") or 0) for r in results)
    summary = {
        "run_id": rid,
        "endpoint": BASE_URL,
        "call_plan_path": str(call_plan_path),
        "calls": len(CALLS),
        "successes": sum(1 for r in results if r.get("ok")),
        "failures": failures,
        "total_rows": total_rows,
        "results": results,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    log_path = RAW_ROOT / "run_logs" / f"{rid}.json"
    log_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
