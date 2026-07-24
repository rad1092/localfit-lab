# -*- coding: utf-8 -*-
"""
교통 승하차량 full raw 적재 완전성 검증.

목적:
  - 202101~202512 버스/지하철 승하차량 full raw 적재가 실제 파일시스템,
    실행 로그, manifest, failed log 기준으로 서로 맞는지 확인한다.
  - 이 검증은 silver/gold를 만들지 않는다.
  - 09/10 전처리 재실행 전에 raw가 완전한지 확인하기 위한 57번 기록이다.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "datacorpus" / "_raw_ingest"
RULE_DATA = ROOT / "datacorpus" / "_rule_validation"
RULE_DOCS = ROOT / "research" / "rule_validation"

RUN_DATE = "2026-07-07"
VALIDATION_VERSION = "transit_full_raw_ingest_completeness.v1.0-20260707"

BUS_SOURCE_ID = "seoul_bus_stop_passengers_hourly"
SUBWAY_SOURCE_ID = "seoul_subway_station_passengers_hourly"
BUS_SERVICE = "CardBusTimeNew"
SUBWAY_SERVICE = "CardSubwayTime"

VALIDATION_CSV = RULE_DATA / "57_transit_full_raw_ingest_completeness_validation.csv"
MONTH_AUDIT_CSV = RULE_DATA / "57_transit_full_raw_ingest_month_audit.csv"
SUMMARY_JSON = RULE_DATA / "57_transit_full_raw_ingest_completeness_summary.json"
REPORT_MD = RULE_DOCS / "57_transit_full_raw_ingest_completeness_20260707.md"


def month_range(start_month: str, end_month: str) -> list[str]:
    months: list[str] = []
    year = int(start_month[:4])
    month = int(start_month[4:])
    end_year = int(end_month[:4])
    end_month_num = int(end_month[4:])
    while (year, month) <= (end_year, end_month_num):
        months.append(f"{year}{month:02d}")
        month += 1
        if month == 13:
            year += 1
            month = 1
    return months


def read_csv(path: Path, **kwargs: Any) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, encoding="utf-8-sig", low_memory=False, **kwargs)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def latest_full_run() -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for path in (RAW / "run_logs").glob("*_seoul_transit_passenger_history.json"):
        payload = read_json(path)
        if payload.get("mode") == "execute" and payload.get("page_limit") is None and payload.get("month_count") == 60:
            payload["_path"] = str(path.relative_to(ROOT))
            candidates.append(payload)
    if not candidates:
        return {}
    return sorted(candidates, key=lambda item: str(item.get("run_id", "")))[-1]


def service_spec(service_key: str) -> dict[str, str]:
    if service_key == "bus":
        return {
            "source_id": BUS_SOURCE_ID,
            "service": BUS_SERVICE,
            "relative_dir": "bus_stop_passengers_hourly",
        }
    if service_key == "subway":
        return {
            "source_id": SUBWAY_SOURCE_ID,
            "service": SUBWAY_SERVICE,
            "relative_dir": "subway_station_passengers_hourly",
        }
    raise ValueError(f"알 수 없는 service_key: {service_key}")


def raw_month_dir(relative_dir: str, month: str) -> Path:
    return RAW / "20260707" / "seoul_open_data" / "transport" / relative_dir / month


def count_month_files(relative_dir: str, month: str, service: str) -> int:
    path = raw_month_dir(relative_dir, month)
    if not path.exists():
        return 0
    return len(list(path.glob(f"{service}_*.json")))


def build_month_audit(full_run: dict[str, Any]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for result in full_run.get("results", []):
        service_key = str(result.get("service_key", ""))
        spec = service_spec(service_key)
        month = str(result.get("month", ""))
        expected_pages = int(result.get("pages_expected", 0))
        expected_rows = int(result.get("total_count", 0))
        collected_rows = int(result.get("collected_rows", -1))
        failures = int(result.get("failures", -1))
        actual_files = count_month_files(spec["relative_dir"], month, spec["service"])
        rows.append(
            {
                "source_id": spec["source_id"],
                "service": spec["service"],
                "service_key": service_key,
                "기준_월": month,
                "expected_pages_from_run_log": expected_pages,
                "actual_raw_file_count": actual_files,
                "expected_rows_from_run_log": expected_rows,
                "collected_rows_from_run_log": collected_rows,
                "failures_from_run_log": failures,
                "month_dir_exists": raw_month_dir(spec["relative_dir"], month).exists(),
                "file_count_matches_run_log": actual_files == expected_pages,
                "row_count_matches_run_log": expected_rows == collected_rows,
                "no_page_failures": failures == 0,
            }
        )
    return pd.DataFrame(rows)


def add_check(
    rows: list[dict[str, Any]],
    rule_name: str,
    observed: object,
    expected: object,
    result: str,
    reason_ko: str,
) -> None:
    rows.append(
        {
            "rule_name": rule_name,
            "observed": observed,
            "expected": expected,
            "result": result,
            "reason_ko": reason_ko,
        }
    )


def build_validation(full_run: dict[str, Any], month_audit: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    required_months = set(month_range("202101", "202512"))
    manifest = read_csv(RAW / "ingest_manifest.csv", dtype=str).fillna("")
    failed = read_csv(RAW / "failed_downloads.csv", dtype=str).fillna("")
    run_id = str(full_run.get("run_id", ""))
    run_manifest = manifest[manifest.get("run_id", pd.Series(dtype=str)).eq(run_id)].copy()
    run_failed = failed[failed.get("run_id", pd.Series(dtype=str)).eq(run_id)].copy()
    manifest_paths = run_manifest.get("raw_path", pd.Series(dtype=str)).astype(str).tolist()
    missing_manifest_files = [path for path in manifest_paths if path and not (ROOT / path).exists()]
    main_smoke_files = sorted(
        path
        for root in [
            RAW / "20260707" / "seoul_open_data" / "transport" / "bus_stop_passengers_hourly",
            RAW / "20260707" / "seoul_open_data" / "transport" / "subway_station_passengers_hourly",
        ]
        for path in root.rglob("*smoke*.json")
    )

    by_service = (
        month_audit.groupby("source_id")
        .agg(
            month_count=("기준_월", "nunique"),
            file_count=("actual_raw_file_count", "sum"),
            expected_page_count=("expected_pages_from_run_log", "sum"),
            total_rows=("expected_rows_from_run_log", "sum"),
            collected_rows=("collected_rows_from_run_log", "sum"),
            failed_pages=("failures_from_run_log", "sum"),
            file_match_count=("file_count_matches_run_log", "sum"),
            row_match_count=("row_count_matches_run_log", "sum"),
        )
        .reset_index()
        if not month_audit.empty
        else pd.DataFrame()
    )

    rows: list[dict[str, Any]] = []
    add_check(
        rows,
        "full 실행 로그가 60개월 x 2서비스 120건 success인가",
        f"path={full_run.get('_path')}, status_counts={full_run.get('status_counts')}, result_count={len(full_run.get('results', []))}",
        "success=120, result_count=120",
        "PASS" if full_run.get("status_counts", {}).get("success") == 120 and len(full_run.get("results", [])) == 120 else "FAIL",
        "원천 적재 완전성은 파일 수만으로 보지 않고 실행 단위 성공 기록부터 확인한다.",
    )
    add_check(
        rows,
        "버스와 지하철 각각 백테스트 필수 60개월을 보유하는가",
        by_service[["source_id", "month_count"]].to_dict("records") if not by_service.empty else [],
        "각 source_id month_count=60",
        "PASS" if set(by_service["month_count"].astype(int).tolist()) == {60} and len(by_service) == 2 else "FAIL",
        "접근성 축은 버스와 지하철을 함께 보므로 한쪽 60개월만 있어서는 부족하다.",
    )
    add_check(
        rows,
        "월별 raw 파일 수가 실행 로그의 pages_expected와 모두 일치하는가",
        f"mismatched={month_audit.loc[~month_audit['file_count_matches_run_log'], ['source_id', '기준_월', 'expected_pages_from_run_log', 'actual_raw_file_count']].to_dict('records') if not month_audit.empty else []}",
        "불일치 0건",
        "PASS" if not month_audit.empty and bool(month_audit["file_count_matches_run_log"].all()) else "FAIL",
        "페이지 일부가 빠지면 list_total_count와 row 수가 맞아도 전처리 중 월 단위 누락이 생길 수 있다.",
    )
    add_check(
        rows,
        "실행 로그의 total_count와 collected_rows가 모든 월에서 일치하는가",
        f"mismatched={month_audit.loc[~month_audit['row_count_matches_run_log'], ['source_id', '기준_월', 'expected_rows_from_run_log', 'collected_rows_from_run_log']].to_dict('records') if not month_audit.empty else []}",
        "불일치 0건",
        "PASS" if not month_audit.empty and bool(month_audit["row_count_matches_run_log"].all()) else "FAIL",
        "row 수 일치는 월별 원천 완전성의 최소 조건이다.",
    )
    add_check(
        rows,
        "full run 실패 페이지가 없는가",
        f"failed_rows_in_failed_downloads={len(run_failed)}, failures_sum={int(month_audit['failures_from_run_log'].sum()) if not month_audit.empty else 'NA'}",
        "failed log 0, failures_sum 0",
        "PASS" if len(run_failed) == 0 and not month_audit.empty and int(month_audit["failures_from_run_log"].sum()) == 0 else "FAIL",
        "실패 페이지가 있으면 같은 월 폴더가 존재해도 partial raw일 수 있다.",
    )
    add_check(
        rows,
        "manifest가 full run raw 파일을 빠짐없이 가리키는가",
        f"manifest_rows={len(run_manifest)}, expected_files={int(month_audit['actual_raw_file_count'].sum()) if not month_audit.empty else 0}, missing_manifest_files={missing_manifest_files[:5]}",
        "manifest_rows == actual_raw_file_count, missing path 0",
        "PASS"
        if not month_audit.empty
        and len(run_manifest) == int(month_audit["actual_raw_file_count"].sum())
        and len(missing_manifest_files) == 0
        else "FAIL",
        "나중에 원천 추적을 하려면 raw 파일과 manifest가 1:1로 대응해야 한다.",
    )
    add_check(
        rows,
        "본 raw 폴더에 스모크 파일이 섞이지 않았는가",
        [str(path.relative_to(ROOT)) for path in main_smoke_files],
        "main raw smoke file 0",
        "PASS" if not main_smoke_files else "FAIL",
        "page-limit 스모크가 본 raw에 섞이면 전처리가 partial 월을 완전 월로 오인할 수 있다.",
    )
    add_check(
        rows,
        "required month set이 정확히 202101~202512인가",
        sorted(set(month_audit["기준_월"].astype(str)) if not month_audit.empty else []),
        "202101~202512 60개월",
        "PASS" if set(month_audit["기준_월"].astype(str)) == required_months else "FAIL",
        "백데이터 검증 범위와 다른 월이 섞이면 시간축이 흔들린다.",
    )

    validation = pd.DataFrame(rows)
    summary = {
        "validation_version": VALIDATION_VERSION,
        "run_date": RUN_DATE,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "full_run_id": run_id,
        "full_run_path": full_run.get("_path", ""),
        "pass_count": int(validation["result"].eq("PASS").sum()),
        "fail_count": int(validation["result"].eq("FAIL").sum()),
        "decision": "TRANSIT_FULL_RAW_READY_FOR_SILVER_PREPROCESSING"
        if int(validation["result"].eq("FAIL").sum()) == 0
        else "TRANSIT_FULL_RAW_FIX_REQUIRED",
        "month_audit_rows": int(len(month_audit)),
        "manifest_rows_for_run": int(len(run_manifest)),
        "failed_rows_for_run": int(len(run_failed)),
        "bus_file_count": int(by_service.loc[by_service["source_id"].eq(BUS_SOURCE_ID), "file_count"].iloc[0])
        if not by_service.empty and by_service["source_id"].eq(BUS_SOURCE_ID).any()
        else 0,
        "subway_file_count": int(by_service.loc[by_service["source_id"].eq(SUBWAY_SOURCE_ID), "file_count"].iloc[0])
        if not by_service.empty and by_service["source_id"].eq(SUBWAY_SOURCE_ID).any()
        else 0,
        "engine_promotion_ready": False,
        "next_validation_number": 58,
    }
    return validation, summary


def write_outputs(validation: pd.DataFrame, month_audit: pd.DataFrame, summary: dict[str, Any]) -> None:
    RULE_DATA.mkdir(parents=True, exist_ok=True)
    RULE_DOCS.mkdir(parents=True, exist_ok=True)
    validation.to_csv(VALIDATION_CSV, index=False, encoding="utf-8-sig")
    month_audit.to_csv(MONTH_AUDIT_CSV, index=False, encoding="utf-8-sig")
    SUMMARY_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# 교통 승하차량 full raw 적재 완전성 검증",
        "",
        f"작성일: {RUN_DATE}",
        "",
        "## 1. 목적",
        "",
        "버스/지하철 승하차량 `202101~202512` full raw 적재가 전처리 가능한 상태인지, 실행 로그·파일시스템·manifest·failed log를 서로 대조했다.",
        "",
        "이 검증은 silver/gold를 만들지 않는다. 점수 직접 투입도 허용하지 않는다. 다음 단계인 09/10 전처리 재실행 전에 raw 자체가 완전한지만 확인한다.",
        "",
        "## 2. 요약",
        "",
        f"- validation_version: `{summary['validation_version']}`",
        f"- full_run_id: `{summary['full_run_id']}`",
        f"- full_run_path: `{summary['full_run_path']}`",
        f"- PASS: {summary['pass_count']}",
        f"- FAIL: {summary['fail_count']}",
        f"- decision: `{summary['decision']}`",
        f"- bus_file_count: {summary['bus_file_count']}",
        f"- subway_file_count: {summary['subway_file_count']}",
        f"- manifest_rows_for_run: {summary['manifest_rows_for_run']}",
        "",
        "## 3. 검증 결과",
        "",
        "| 규칙 | 관측값 | 기대값 | 결과 | 이유 |",
        "|---|---|---|---|---|",
    ]
    for _, row in validation.iterrows():
        lines.append(
            "| {rule_name} | {observed} | {expected} | {result} | {reason_ko} |".format(
                rule_name=str(row["rule_name"]).replace("|", "/"),
                observed=str(row["observed"]).replace("|", "/"),
                expected=str(row["expected"]).replace("|", "/"),
                result=row["result"],
                reason_ko=str(row["reason_ko"]).replace("|", "/"),
            )
        )
    lines.extend(
        [
            "",
            "## 4. 2보 전진 1보 후퇴",
            "",
            "1. 전진: full 실행 로그 기준 버스/지하철 120개 수집 단위가 모두 success다.",
            "2. 전진: 월 폴더와 페이지 파일 수가 실행 로그의 `pages_expected`와 일치한다.",
            "3. 후퇴: 이 검증은 raw 완전성만 확인한다. silver/gold/backtest는 아직 아니다.",
            "4. 후퇴: 202601~202604 최근 evidence 월은 아직 별도 확보가 필요하다.",
            "5. 후퇴: 접근성 점수 직접 투입은 09/10 전처리, gold 재생성, 백테스트 이후에만 검토한다.",
            "",
            "## 5. 다음 작업",
            "",
            "1. `scripts/preprocess_rule_engine_bus_passengers.py`를 재실행해 버스 silver를 월이력 기준으로 다시 만든다.",
            "2. `scripts/preprocess_rule_engine_subway_passengers.py`를 재실행해 지하철 silver를 월이력 기준으로 다시 만든다.",
            "3. 09/10 silver 검증 후 42/55/57을 다시 확인하고 gold 접근성 후보 재생성을 검토한다.",
        ]
    )
    REPORT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    full_run = latest_full_run()
    month_audit = build_month_audit(full_run) if full_run else pd.DataFrame()
    validation, summary = build_validation(full_run, month_audit)
    write_outputs(validation, month_audit, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary["fail_count"]:
        raise SystemExit(summary["fail_count"])


if __name__ == "__main__":
    main()
