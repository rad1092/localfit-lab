# -*- coding: utf-8 -*-
"""
교통 승하차량 월별 적재 계약 검증.

목적:
  - 버스/지하철 승하차량 전처리가 특정 월(예: 202605)에 하드코딩되지 않았는지 확인한다.
  - 현재 보유한 full-history 원천을 처리해도 silver 월 범위가 보존되는지 확인한다.
  - 월 폴더 자동탐색 구조가 생겼더라도 backtest 전 점수 직접 투입 보류 판정은 유지되는지 확인한다.
"""

from __future__ import annotations

import importlib.util
import json
import re
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
RAW = ROOT / "datacorpus" / "_raw_ingest"
SILVER = ROOT / "datacorpus" / "_silver"
RULE_DATA = ROOT / "datacorpus" / "_rule_validation"
RULE_DOCS = ROOT / "research" / "rule_validation"

RUN_DATE = "2026-07-07"
VALIDATION_VERSION = "transit_passenger_multimonth_ingest_contract.v1.1-20260707"

BUS_SCRIPT = SCRIPTS / "preprocess_rule_engine_bus_passengers.py"
SUBWAY_SCRIPT = SCRIPTS / "preprocess_rule_engine_subway_passengers.py"
SUBWAY_STATION_MASTER_SCRIPT = SCRIPTS / "preprocess_rule_engine_subway_station_master.py"
VALIDATION_CSV = RULE_DATA / "43_transit_passenger_multimonth_ingest_contract_validation.csv"
SUMMARY_JSON = RULE_DATA / "43_transit_passenger_multimonth_ingest_contract_summary.json"
REPORT_MD = RULE_DOCS / "43_transit_passenger_multimonth_ingest_contract_validation_20260707.md"


def read_csv(path: Path, usecols: list[str] | None = None) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, encoding="utf-8-sig", low_memory=False, usecols=usecols)


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def raw_months(relative: str) -> list[str]:
    months: set[str] = set()
    for date_dir in sorted(RAW.glob("20??????")):
        base = date_dir / "seoul_open_data" / "transport" / relative
        if not base.exists():
            continue
        for path in base.iterdir():
            if path.is_dir() and re.fullmatch(r"\d{6}", path.name):
                months.add(path.name)
    return sorted(months)


def source_contract(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    forbidden_patterns = [
        'RAW_PATH =',
        'SOURCE_MONTH = "202605"',
        'bus_stop_passengers_hourly" / "202605"',
        'subway_station_passengers_hourly" / "202605"',
        '202605 기준',
        '43,122건',
        '단일 hour-long CSV',
        '620건',
        '단일 hour-long CSV',
    ]
    required_patterns = [
        "RAW_RELATIVE_PARTS",
        "discover_month_paths",
        "totals_by_month",
        "_raw_month_dir",
        "api_total = sum",
    ]
    forbidden_hits = [pattern for pattern in forbidden_patterns if pattern in text]
    missing_required = [pattern for pattern in required_patterns if pattern not in text]
    return {
        "forbidden_hits": forbidden_hits,
        "missing_required": missing_required,
        "has_contract": not forbidden_hits and not missing_required,
    }


def station_master_passenger_contract(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    forbidden_patterns = [
        "PASSENGER_RAW_PATH",
        'PASSENGER_MONTH = "202605"',
        'subway_station_passengers_hourly" / "202605"',
        "승하차량 202605",
    ]
    required_patterns = [
        "PASSENGER_RAW_RELATIVE_PARTS",
        "discover_passenger_month_paths",
        "read_passenger_month_pages",
        "_raw_month_dir",
        "passenger_month_text",
    ]
    forbidden_hits = [pattern for pattern in forbidden_patterns if pattern in text]
    missing_required = [pattern for pattern in required_patterns if pattern not in text]
    return {
        "forbidden_hits": forbidden_hits,
        "missing_required": missing_required,
        "has_contract": not forbidden_hits and not missing_required,
    }


def import_module_from_path(path: Path, module_name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"{path} 모듈 spec을 만들 수 없습니다.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def discovered_months_by_module(path: Path, module_name: str) -> list[str]:
    module = import_module_from_path(path, module_name)
    month_paths = module.discover_month_paths()
    return sorted(path.name for path in month_paths)


def discovered_passenger_months_by_station_master_module(path: Path, module_name: str) -> list[str]:
    module = import_module_from_path(path, module_name)
    month_paths = module.discover_passenger_month_paths()
    return sorted(path.name for path in month_paths)


def csv_row_count_and_months(path: Path) -> tuple[int, list[str]]:
    df = read_csv(path, usecols=["기준_월"])
    if df.empty:
        return 0, []
    months = sorted(df["기준_월"].dropna().astype(str).str.strip().unique().tolist())
    return len(df), months


def hour_manifest_count_and_months(path: Path) -> tuple[int, int, list[str]]:
    df = read_csv(path, usecols=["기준_월", "expected_long_rows"])
    if df.empty:
        return 0, 0, []
    df["expected_long_rows"] = pd.to_numeric(df["expected_long_rows"], errors="coerce").fillna(0)
    months = sorted(df["기준_월"].dropna().astype(str).str.strip().unique().tolist())
    return len(df), int(df["expected_long_rows"].sum()), months


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


def write_report(validation_df: pd.DataFrame, summary: dict[str, Any]) -> None:
    RULE_DATA.mkdir(parents=True, exist_ok=True)
    RULE_DOCS.mkdir(parents=True, exist_ok=True)

    validation_df.to_csv(VALIDATION_CSV, index=False, encoding="utf-8-sig")
    SUMMARY_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# 교통 승하차량 월별 적재 계약 검증",
        "",
        f"작성일: {RUN_DATE}",
        "",
        "## 1. 목적",
        "",
        "버스/지하철 승하차량 전처리 스크립트가 특정 월 폴더에 묶여 있으면, 새 월 데이터가 들어올 때마다 코드를 고쳐야 한다. 이 검증은 전처리 구조가 월 폴더 자동탐색과 full-history silver 계약을 지키는지 확인한다.",
        "",
        "중요한 점은 두 가지다.",
        "",
        "1. 전처리 스크립트는 여러 월 폴더를 읽을 준비가 되어야 한다.",
        "2. 현재 보유 데이터가 full-history로 확장되어도 backtest와 CRS 검토 전 점수 직접 투입은 보류해야 한다.",
        "",
        "## 2. 검증 요약",
        "",
        f"- validation_version: `{summary['validation_version']}`",
        f"- PASS: {summary['pass_count']}",
        f"- FAIL: {summary['fail_count']}",
        f"- decision: `{summary['decision']}`",
        "",
        "## 3. 검증 결과",
        "",
        "| 규칙 | 관측값 | 기대값 | 결과 | 이유 |",
        "|---|---|---|---|---|",
    ]
    for _, row in validation_df.iterrows():
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
            "## 4. 2보 전진 1보 후퇴 검토",
            "",
            "1. 전진: `RAW_RELATIVE_PARTS`와 `discover_month_paths()`를 두어 여러 수집일의 월 폴더를 자동탐색하게 했다.",
            "2. 전진: 현재 보유한 full-history 원천을 처리해도 summary와 hour manifest의 월 범위가 일치하는지 확인한다.",
            "3. 후퇴: 자동탐색 구조와 full-history silver가 있어도 성능 검증을 통과한 것은 아니므로 직접 점수 투입은 금지한다.",
            "4. 후퇴: 42번 검증의 `교통_월이력_점수투입_보류` 판정은 유지한다.",
            "5. 후퇴: 생활이동/OD 월파일은 교통 승하차량 원천이 아니므로 버스·지하철 승하차량 월이력 대체물로 쓰지 않는다.",
            "",
            "## 5. 다음 작업",
            "",
            "1. `CardBusTimeNew`, `CardSubwayTime` 원천 월 폴더가 추가되면 09/10 전처리를 그대로 재실행한다.",
            "2. 새 월 데이터가 들어온 뒤 31/32/42/43 검증을 다시 돌린다.",
            "3. 백테스트 기간과 월 커버리지가 맞기 전까지 승하차량은 접근성 evidence 후보로만 둔다.",
        ]
    )
    REPORT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    bus_raw_months = raw_months("bus_stop_passengers_hourly")
    subway_raw_months = raw_months("subway_station_passengers_hourly")

    bus_contract = source_contract(BUS_SCRIPT)
    subway_contract = source_contract(SUBWAY_SCRIPT)
    station_master_contract = station_master_passenger_contract(SUBWAY_STATION_MASTER_SCRIPT)

    bus_discovered = discovered_months_by_module(BUS_SCRIPT, "preprocess_rule_engine_bus_passengers_contract_check")
    subway_discovered = discovered_months_by_module(SUBWAY_SCRIPT, "preprocess_rule_engine_subway_passengers_contract_check")
    station_master_discovered = discovered_passenger_months_by_station_master_module(
        SUBWAY_STATION_MASTER_SCRIPT,
        "preprocess_rule_engine_subway_station_master_contract_check",
    )

    bus_summary_rows, bus_summary_months = csv_row_count_and_months(
        SILVER / "silver_bus_passenger_route_stop_month_summary.csv"
    )
    bus_long_manifest_rows, bus_expected_long_rows, bus_long_months = hour_manifest_count_and_months(
        SILVER / "silver_bus_passenger_route_stop_month_hour_manifest.csv"
    )
    subway_summary_rows, subway_summary_months = csv_row_count_and_months(
        SILVER / "silver_subway_passenger_station_month_summary.csv"
    )
    subway_long_manifest_rows, subway_expected_long_rows, subway_long_months = hour_manifest_count_and_months(
        SILVER / "silver_subway_passenger_station_month_hour_manifest.csv"
    )
    subway_join_audit = read_csv(
        RULE_DATA / "08_subway_station_master_passenger_join_audit.csv",
        usecols=["passenger_month"],
    )
    subway_join_audit_rows = len(subway_join_audit)
    subway_join_audit_months = (
        sorted(subway_join_audit["passenger_month"].dropna().astype(str).str.strip().unique().tolist())
        if not subway_join_audit.empty
        else []
    )

    summary_42 = read_json(RULE_DATA / "42_transit_monthly_history_readiness_summary.json")
    decision_42 = summary_42.get("decision", "")
    reason_42 = summary_42.get("decision_reason_ko", "")

    rows: list[dict[str, Any]] = []
    add_check(
        rows,
        "버스 전처리 스크립트가 월 하드코딩을 제거했는가",
        f"forbidden={bus_contract['forbidden_hits']}, missing_required={bus_contract['missing_required']}",
        "RAW_RELATIVE_PARTS + discover_month_paths + 월별 list_total_count",
        "PASS" if bus_contract["has_contract"] else "FAIL",
        "새 월 폴더를 추가할 때 코드 수정 없이 읽어야 하므로 경로 끝에 특정 월을 박아두면 안 된다.",
    )
    add_check(
        rows,
        "지하철 전처리 스크립트가 월 하드코딩을 제거했는가",
        f"forbidden={subway_contract['forbidden_hits']}, missing_required={subway_contract['missing_required']}",
        "RAW_RELATIVE_PARTS + discover_month_paths + 월별 list_total_count",
        "PASS" if subway_contract["has_contract"] else "FAIL",
        "원천이 매월 갱신되는 구조라면 전처리도 월 폴더 자동탐색을 기본 계약으로 가져가야 한다.",
    )
    add_check(
        rows,
        "버스 discover_month_paths가 실제 raw 월 폴더를 반환하는가",
        f"raw={bus_raw_months}, discovered={bus_discovered}",
        "raw 월 폴더와 모듈 탐색 결과 일치",
        "PASS" if bus_raw_months == bus_discovered else "FAIL",
        "정적 문자열 제거만으로는 부족하므로 실제 함수가 현재 raw 구조를 읽는지 확인했다.",
    )
    add_check(
        rows,
        "지하철 discover_month_paths가 실제 raw 월 폴더를 반환하는가",
        f"raw={subway_raw_months}, discovered={subway_discovered}",
        "raw 월 폴더와 모듈 탐색 결과 일치",
        "PASS" if subway_raw_months == subway_discovered else "FAIL",
        "정적 검사와 실행 검사를 함께 해야 새 월 폴더 추가 시 사고를 줄일 수 있다.",
    )
    add_check(
        rows,
        "지하철 역사마스터 예비 조인 audit도 승하차량 월 하드코딩을 제거했는가",
        f"forbidden={station_master_contract['forbidden_hits']}, missing_required={station_master_contract['missing_required']}",
        "PASSENGER_RAW_RELATIVE_PARTS + discover_passenger_month_paths + read_passenger_month_pages",
        "PASS" if station_master_contract["has_contract"] else "FAIL",
        "역사마스터 audit가 202605 raw만 직접 보면 09번 승하차량 전처리와 다른 기준으로 움직이므로 같은 월 폴더 탐색 계약을 써야 한다.",
    )
    add_check(
        rows,
        "지하철 역사마스터 audit의 승하차량 월 탐색이 실제 raw와 일치하는가",
        f"raw={subway_raw_months}, discovered={station_master_discovered}",
        "raw 월 폴더와 역사마스터 audit 탐색 결과 일치",
        "PASS" if subway_raw_months == station_master_discovered else "FAIL",
        "승하차량 좌표 결합 audit도 본 승하차량 전처리와 같은 월 범위를 읽어야 한다.",
    )
    add_check(
        rows,
        "버스 full-history summary와 hour manifest가 월 범위를 보존하는가",
        f"summary={bus_summary_rows}, expected_long={bus_expected_long_rows}, manifest_rows={bus_long_manifest_rows}, summary_months={bus_summary_months}, long_months={bus_long_months}",
        "summary 60개월 이상 + hour manifest 월 일치",
        "PASS"
        if len(bus_summary_months) >= 60
        and bus_summary_months == bus_long_months
        and bus_summary_rows > 0
        and bus_expected_long_rows > 0
        else "FAIL",
        "full-history 전처리에서는 단일 hour-long CSV 대신 manifest로 시간대 long 예상 행 수와 월 범위를 보존한다.",
    )
    add_check(
        rows,
        "지하철 full-history summary와 hour manifest가 월 범위를 보존하는가",
        f"summary={subway_summary_rows}, expected_long={subway_expected_long_rows}, manifest_rows={subway_long_manifest_rows}, summary_months={subway_summary_months}, long_months={subway_long_months}",
        "summary 60개월 이상 + hour manifest 월 일치",
        "PASS"
        if len(subway_summary_months) >= 60
        and subway_summary_months == subway_long_months
        and subway_summary_rows > 0
        and subway_expected_long_rows > 0
        else "FAIL",
        "전처리 구조 개선은 데이터 자체를 요약하거나 누락시키는 작업이 아니어야 하며, 시간대 long은 manifest로 추적한다.",
    )
    add_check(
        rows,
        "지하철 역사마스터 승하차량 join audit 결과가 raw 월 범위 안에 있는가",
        f"rows={subway_join_audit_rows}, months={subway_join_audit_months}",
        "audit 월은 raw 승하차 월 범위의 부분집합 또는 full-history",
        "PASS" if subway_join_audit_rows > 0 and set(subway_join_audit_months).issubset(set(subway_raw_months)) else "FAIL",
        "예비 조인 audit가 아직 최신 full-history로 재생성되지 않았더라도 raw에 없는 월을 만들면 안 된다.",
    )
    add_check(
        rows,
        "42번 월이력 준비도 판정이 여전히 점수투입 보류인가",
        f"decision={decision_42}",
        "교통_월이력_점수투입_보류",
        "PASS" if decision_42 == "교통_월이력_점수투입_보류" else "FAIL",
        "월 폴더 자동탐색은 적재 구조 개선일 뿐, 과거 월 커버리지를 새로 만들어내지는 않는다.",
    )
    add_check(
        rows,
        "생활이동/OD를 승하차량 대체물로 쓰지 않는다는 계약이 유지되는가",
        reason_42,
        "생활이동 OD는 원천과 의미가 달라 대체하지 않는다는 판단",
        "PASS"
        if "생활이동" in reason_42 and ("대체하지 않는다" in reason_42 or "대체물로 쓰지 않는다" in reason_42)
        else "FAIL",
        "월파일이 많다는 이유로 서로 다른 원천을 같은 교통 승하차량 이력처럼 쓰면 알고리즘 근거가 무너진다.",
    )

    validation_df = pd.DataFrame(rows)
    fail_count = int(validation_df["result"].eq("FAIL").sum())
    pass_count = int(validation_df["result"].eq("PASS").sum())
    summary = {
        "run_date": RUN_DATE,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "validation_version": VALIDATION_VERSION,
        "pass_count": pass_count,
        "fail_count": fail_count,
        "bus_raw_months": bus_raw_months,
        "subway_raw_months": subway_raw_months,
        "bus_summary_rows": bus_summary_rows,
        "bus_hour_manifest_rows": bus_long_manifest_rows,
        "bus_expected_long_rows": bus_expected_long_rows,
        "subway_summary_rows": subway_summary_rows,
        "subway_hour_manifest_rows": subway_long_manifest_rows,
        "subway_expected_long_rows": subway_expected_long_rows,
        "subway_station_join_audit_rows": subway_join_audit_rows,
        "decision": "월별_적재구조_PASS_점수투입은_42번_판정_유지" if fail_count == 0 else "월별_적재구조_FAIL",
        "next_validation_number": 44,
    }
    write_report(validation_df, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
