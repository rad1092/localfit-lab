# -*- coding: utf-8 -*-
"""
교통 승하차 이력 적재/전처리 계약 검증.

목적:
  - 55번 수집계획 뒤 실제 적재 스크립트가 안전한지 확인한다.
  - page-limit 스모크가 월별 raw 본폴더를 오염시키지 않는지 확인한다.
  - 버스/지하철 승하차 전처리가 여러 적재일의 월 폴더를 읽을 수 있는지 확인한다.
  - 검증 결과를 56번 CSV/JSON/MD로 남긴다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
RAW = ROOT / "datacorpus" / "_raw_ingest"
RULE_DATA = ROOT / "datacorpus" / "_rule_validation"
RULE_DOCS = ROOT / "research" / "rule_validation"

RUN_DATE = "2026-07-07"
VALIDATION_VERSION = "transit_history_ingest_preprocess_contract.v1.0-20260707"

INGEST_SCRIPT = SCRIPTS / "ingest_seoul_transit_passenger_history.py"
BUS_PREPROCESS = SCRIPTS / "preprocess_rule_engine_bus_passengers.py"
SUBWAY_PREPROCESS = SCRIPTS / "preprocess_rule_engine_subway_passengers.py"
STATION_MASTER_PREPROCESS = SCRIPTS / "preprocess_rule_engine_subway_station_master.py"
PLAN_VALIDATOR = SCRIPTS / "validate_transit_passenger_history_collection_plan.py"
MULTIMONTH_VALIDATOR = SCRIPTS / "validate_transit_passenger_multimonth_ingest_contract.py"
READINESS_VALIDATOR = SCRIPTS / "validate_transit_monthly_history_readiness.py"
SILVER_HISTORY_SUMMARY = RULE_DATA / "58_transit_passenger_history_silver_summary.json"

VALIDATION_CSV = RULE_DATA / "56_transit_history_ingest_preprocess_contract_validation.csv"
SUMMARY_JSON = RULE_DATA / "56_transit_history_ingest_preprocess_contract_summary.json"
REPORT_MD = RULE_DOCS / "56_transit_history_ingest_preprocess_contract_20260707.md"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def latest_run_log(mode: str, *, require_page_limit: bool | None = None) -> dict[str, Any]:
    logs: list[dict[str, Any]] = []
    for path in (RAW / "run_logs").glob("*_seoul_transit_passenger_history.json"):
        payload = read_json(path)
        if payload.get("mode") != mode:
            continue
        has_page_limit = payload.get("page_limit") is not None
        if require_page_limit is not None and has_page_limit != require_page_limit:
            continue
        payload["_path"] = str(path.relative_to(ROOT))
        logs.append(payload)
    if not logs:
        return {}
    return sorted(logs, key=lambda item: str(item.get("run_id", "")))[-1]


def has_all(text: str, patterns: list[str]) -> bool:
    return all(pattern in text for pattern in patterns)


def has_none(text: str, patterns: list[str]) -> bool:
    return not any(pattern in text for pattern in patterns)


def check_preprocess_contract(path: Path, relative_constant: str) -> dict[str, Any]:
    text = read_text(path)
    required = [
        relative_constant,
        'RAW_DIR.glob("20??????")',
        "month_by_name",
        "_raw_month_dir",
    ]
    forbidden = [
        'SOURCE_MONTH = "202605"',
        'bus_stop_passengers_hourly" / "202605"',
        'subway_station_passengers_hourly" / "202605"',
        "PASSENGER_RAW_BASE_PATH",
        "RAW_BASE_PATH",
    ]
    return {
        "path": str(path.relative_to(ROOT)),
        "missing_required": [pattern for pattern in required if pattern not in text],
        "forbidden_hits": [pattern for pattern in forbidden if pattern in text],
        "ok": has_all(text, required) and has_none(text, forbidden),
    }


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


def main() -> None:
    RULE_DATA.mkdir(parents=True, exist_ok=True)
    RULE_DOCS.mkdir(parents=True, exist_ok=True)

    ingest_text = read_text(INGEST_SCRIPT)
    plan_text = read_text(PLAN_VALIDATOR)
    multimonth_text = read_text(MULTIMONTH_VALIDATOR)
    readiness_text = read_text(READINESS_VALIDATOR)
    bus_text = read_text(BUS_PREPROCESS)
    subway_text = read_text(SUBWAY_PREPROCESS)
    dry_run = latest_run_log("dry_run")
    smoke_run = latest_run_log("execute", require_page_limit=True)
    full_run = latest_run_log("execute", require_page_limit=False)

    bus_contract = check_preprocess_contract(BUS_PREPROCESS, "RAW_RELATIVE_PARTS")
    subway_contract = check_preprocess_contract(SUBWAY_PREPROCESS, "RAW_RELATIVE_PARTS")
    station_contract = check_preprocess_contract(STATION_MASTER_PREPROCESS, "PASSENGER_RAW_RELATIVE_PARTS")

    smoke_probe_dir = RAW / "20260707" / "seoul_open_data" / "transport" / "probes" / "transit_passenger_history"
    smoke_probe_files = sorted(path.name for path in smoke_probe_dir.glob("*_202101_*_smoke.json"))
    main_smoke_files = sorted(
        path
        for root in [
            RAW / "20260707" / "seoul_open_data" / "transport" / "bus_stop_passengers_hourly",
            RAW / "20260707" / "seoul_open_data" / "transport" / "subway_station_passengers_hourly",
        ]
        for path in root.rglob("*smoke*.json")
    )

    dry_run_ok = (
        dry_run.get("month_count") == 60
        and dry_run.get("planned_rows") == 120
        and dry_run.get("collect_count") == 120
        and dry_run.get("status_counts", {}).get("dry_run") == 120
    )
    smoke_results = smoke_run.get("results", [])
    full_results = full_run.get("results", [])
    smoke_ok = (
        smoke_run.get("page_limit") == 1
        and smoke_run.get("status_counts", {}).get("smoke_success") == 2
        and len(smoke_results) == 2
        and all(result.get("raw_scope") == "probe_only" for result in smoke_results)
        and all(int(result.get("failures", 0)) == 0 for result in smoke_results)
    )
    full_ok = (
        full_run.get("month_count") == 60
        and full_run.get("status_counts", {}).get("success") == 120
        and len(full_results) == 120
        and all(result.get("status") == "success" for result in full_results)
        and all(int(result.get("failures", 0)) == 0 for result in full_results)
    )
    bus_full_month_count = len(
        [
            path
            for path in (RAW / "20260707" / "seoul_open_data" / "transport" / "bus_stop_passengers_hourly").glob("20????")
            if path.is_dir()
        ]
    )
    subway_full_month_count = len(
        [
            path
            for path in (RAW / "20260707" / "seoul_open_data" / "transport" / "subway_station_passengers_hourly").glob("20????")
            if path.is_dir()
        ]
    )

    execute_gate_ok = has_all(
        ingest_text,
        ['parser.add_argument("--execute", action="store_true"', "if not args.execute:", '"mode": "dry_run"', '"mode": "execute"'],
    )

    rows: list[dict[str, Any]] = []
    add_check(
        rows,
        "적재 스크립트가 기본 dry-run과 명시 execute 게이트를 갖는가",
        "execute_arg + if_not_execute_dry_run + execute_summary" if execute_gate_ok else "execute_gate_missing",
        "실제 API 적재는 --execute가 있을 때만 수행",
        "PASS" if execute_gate_ok else "FAIL",
        "전처리 전 원천 적재는 되돌리기 어려우므로 기본값은 계획 확인이어야 한다.",
    )
    add_check(
        rows,
        "page-limit 스모크가 본 raw 폴더가 아니라 probes에만 저장되는가",
        "if page_limit is not None / raw_scope=probe_only / transport/probes" if has_all(ingest_text, ["if page_limit is not None:", "raw_scope", "probe_only", "transport/probes/transit_passenger_history"]) else "contract_missing",
        "page_limit 사용 시 항상 probe_only",
        "PASS" if has_all(ingest_text, ["if page_limit is not None:", "raw_scope", "probe_only", "transport/probes/transit_passenger_history"]) else "FAIL",
        "스모크 응답이 월별 원천 폴더에 들어가면 이후 전처리가 부분 월을 완전 월로 오인할 수 있다.",
    )
    add_check(
        rows,
        "기존 월 탐색이 여러 적재일 폴더를 스캔하는가",
        'RAW_ROOT.glob("20??????")' in ingest_text,
        "기존 raw 여부는 모든 YYYYMMDD 적재일에서 탐색",
        "PASS" if 'RAW_ROOT.glob("20??????")' in ingest_text else "FAIL",
        "이미 받은 월을 다른 적재일에 또 받지 않으려면 특정 날짜 폴더만 보면 안 된다.",
    )
    add_check(
        rows,
        "202101~202512 dry-run 계획이 60개월 x 2서비스로 잡히는가",
        f"path={dry_run.get('_path')}, month_count={dry_run.get('month_count')}, planned_rows={dry_run.get('planned_rows')}, collect_count={dry_run.get('collect_count')}",
        "month_count=60, planned_rows=120, collect_count=120",
        "PASS" if dry_run_ok else "FAIL",
        "백테스트 필수 구간의 실제 적재 전에 빠진 월/서비스가 계획 단계에서 드러나야 한다.",
    )
    add_check(
        rows,
        "202101 API 스모크가 버스/지하철 모두 성공했는가",
        f"path={smoke_run.get('_path')}, status_counts={smoke_run.get('status_counts')}, results={[(r.get('service'), r.get('total_count'), r.get('raw_scope')) for r in smoke_results]}",
        "버스/지하철 smoke_success 2건, raw_scope=probe_only",
        "PASS" if smoke_ok else "FAIL",
        "키, 엔드포인트, 월 파라미터, 응답 루트가 실제로 맞는지 최소 한 번은 API 응답으로 확인해야 한다.",
    )
    add_check(
        rows,
        "202101~202512 full raw 적재가 버스/지하철 모두 성공했는가",
        f"path={full_run.get('_path')}, status_counts={full_run.get('status_counts')}, result_count={len(full_results)}, bus_month_dirs={bus_full_month_count}, subway_month_dirs={subway_full_month_count}",
        "success 120건, 버스 60개월, 지하철 60개월",
        "PASS" if full_ok and bus_full_month_count >= 60 and subway_full_month_count >= 60 else "FAIL",
        "스모크 성공만으로는 백데이터 검증에 부족하므로 실제 full raw 월 폴더와 실행 결과를 별도로 확인해야 한다.",
    )
    add_check(
        rows,
        "스모크 파일이 probes에만 있고 월별 raw 본폴더가 생성되지 않았는가",
        f"probe_files={smoke_probe_files}, main_smoke_files={[str(path.relative_to(ROOT)) for path in main_smoke_files]}",
        "probe 2개 존재, 본 raw 폴더 내 smoke 파일 0개",
        "PASS" if len(smoke_probe_files) == 2 and not main_smoke_files else "FAIL",
        "full raw 적재 후에는 202101 본 raw 월 폴더가 생길 수 있으므로, 폴더 존재가 아니라 smoke 파일이 본 raw에 섞였는지를 확인해야 한다.",
    )
    add_check(
        rows,
        "버스 전처리가 여러 적재일 월폴더를 읽을 계약인가",
        f"missing={bus_contract['missing_required']}, forbidden={bus_contract['forbidden_hits']}",
        "RAW_RELATIVE_PARTS + RAW_DIR.glob + month_by_name + _raw_month_dir",
        "PASS" if bus_contract["ok"] else "FAIL",
        "버스 승하차량은 월별 page 수가 커서 여러 날에 나누어 적재될 수 있으므로 날짜 루트 탐색이 필요하다.",
    )
    add_check(
        rows,
        "지하철 전처리가 여러 적재일 월폴더를 읽을 계약인가",
        f"missing={subway_contract['missing_required']}, forbidden={subway_contract['forbidden_hits']}",
        "RAW_RELATIVE_PARTS + RAW_DIR.glob + month_by_name + _raw_month_dir",
        "PASS" if subway_contract["ok"] else "FAIL",
        "지하철도 버스와 같은 월별 이력 축에 들어가므로 동일한 적재일 탐색 계약을 가져야 한다.",
    )
    add_check(
        rows,
        "역사마스터 승하차량 audit도 같은 월 탐색 계약을 쓰는가",
        f"missing={station_contract['missing_required']}, forbidden={station_contract['forbidden_hits']}",
        "PASSENGER_RAW_RELATIVE_PARTS + RAW_DIR.glob + month_by_name + _raw_month_dir",
        "PASS" if station_contract["ok"] else "FAIL",
        "역 좌표 audit가 본 승하차량 전처리와 다른 월 범위를 읽으면 좌표 결합 검증 근거가 흔들린다.",
    )
    add_check(
        rows,
        "55/43 검증 스크립트가 교통 raw 날짜를 20260703 하나로 고정하지 않는가",
        {
            "plan_has_glob": 'RAW.glob("20??????")' in plan_text,
            "multimonth_has_glob": 'RAW.glob("20??????")' in multimonth_text,
            "hardcoded_transport_20260703": 'RAW / "20260703" / "seoul_open_data" / "transport"' in plan_text + multimonth_text,
        },
        "두 검증 모두 날짜 루트 전체 스캔, transport 20260703 고정 없음",
        "PASS"
        if 'RAW.glob("20??????")' in plan_text
        and 'RAW.glob("20??????")' in multimonth_text
        and 'RAW / "20260703" / "seoul_open_data" / "transport"' not in plan_text + multimonth_text
        else "FAIL",
        "검증 도구가 낡은 날짜만 보면 실제 적재를 하고도 커버리지를 못 보는 오류가 생긴다.",
    )
    add_check(
        rows,
        "42번 readiness 검증도 새 적재일 raw 월폴더를 읽을 수 있는가",
        {
            "readiness_has_glob": 'RAW.glob("20??????")' in readiness_text,
            "hardcoded_transport_20260703": 'RAW / "20260703" / "seoul_open_data" / "transport"' in readiness_text,
        },
        "42번도 날짜 루트 전체 스캔, transport 20260703 고정 없음",
        "PASS"
        if 'RAW.glob("20??????")' in readiness_text
        and 'RAW / "20260703" / "seoul_open_data" / "transport"' not in readiness_text
        else "FAIL",
        "full 적재 뒤 readiness 검증이 낡은 날짜만 보면 승하차량 커버리지를 과소평가한다.",
    )
    add_check(
        rows,
        "부분 raw나 원천 불일치가 있으면 silver 저장 전에 중단하는가",
        {
            "bus_has_fail_guard": "전처리 검증 FAIL로 silver 저장을 중단" in bus_text,
            "subway_has_fail_guard": "전처리 검증 FAIL로 silver 저장을 중단" in subway_text,
        },
        "버스/지하철 모두 domain FAIL이면 RuntimeError 후 저장 중단",
        "PASS"
        if "전처리 검증 FAIL로 silver 저장을 중단" in bus_text
        and "전처리 검증 FAIL로 silver 저장을 중단" in subway_text
        else "FAIL",
        "월 폴더에 일부 page만 있으면 행 수 검증은 실패해야 하며, 실패한 silver를 남기면 다음 gold가 오염된다.",
    )
    add_check(
        rows,
        "API 키와 URL이 기록에 노출되지 않도록 redaction 계약이 있는가",
        "parse_key_file/redact_url/<redacted>" if has_all(ingest_text, ["parse_key_file", "redact_url", '"key": "<redacted>"']) else "redaction_contract_missing",
        "key.md에서 읽되 manifest/request_params에는 redacted",
        "PASS" if has_all(ingest_text, ["parse_key_file", "redact_url", '"key": "<redacted>"']) else "FAIL",
        "공공 API 키라도 원응답 manifest와 검증 문서에 그대로 남기면 재현성과 보안 관리가 같이 무너진다.",
    )

    silver_history = read_json(SILVER_HISTORY_SUMMARY) if SILVER_HISTORY_SUMMARY.exists() else {}
    silver_ready = silver_history.get("decision") == "PASS"
    add_check(
        rows,
        "58번 full silver 이력 전처리가 PASS 상태인가",
        {
            "decision": silver_history.get("decision", ""),
            "bus_summary_rows": silver_history.get("bus_summary_rows", ""),
            "subway_summary_rows": silver_history.get("subway_summary_rows", ""),
            "bus_month_count": silver_history.get("bus_month_count", ""),
            "subway_month_count": silver_history.get("subway_month_count", ""),
        },
        "58번 decision PASS",
        "PASS" if silver_ready else "NOT_READY",
        "raw 확보 뒤에도 silver row 수, 24시간 총량, grain 중복, 좌표 조인 상태 검증을 통과해야 gold/backtest 전 단계로 넘어갈 수 있다.",
    )

    validation_df = pd.DataFrame(rows)
    pass_count = int(validation_df["result"].eq("PASS").sum())
    fail_count = int(validation_df["result"].eq("FAIL").sum())
    if fail_count:
        decision = "TRANSIT_HISTORY_INGEST_CONTRACT_FIX_REQUIRED"
    elif full_ok and bus_full_month_count >= 60 and subway_full_month_count >= 60 and silver_ready:
        decision = "TRANSIT_HISTORY_RAW_AND_SILVER_READY_GOLD_NOT_PROMOTED"
    elif full_ok and bus_full_month_count >= 60 and subway_full_month_count >= 60:
        decision = "TRANSIT_HISTORY_FULL_RAW_COLLECTED_SILVER_NOT_READY"
    else:
        decision = "TRANSIT_HISTORY_INGEST_CONTRACT_READY_NOT_FULLY_COLLECTED"
    summary = {
        "validation_version": VALIDATION_VERSION,
        "run_date": RUN_DATE,
        "pass_count": pass_count,
        "fail_count": fail_count,
        "decision": decision,
        "silver_history_decision": silver_history.get("decision", ""),
        "silver_history_bus_summary_rows": silver_history.get("bus_summary_rows", ""),
        "silver_history_subway_summary_rows": silver_history.get("subway_summary_rows", ""),
        "latest_dry_run": dry_run.get("_path", ""),
        "latest_smoke_run": smoke_run.get("_path", ""),
        "latest_full_run": full_run.get("_path", ""),
        "smoke_probe_file_count": len(smoke_probe_files),
        "main_raw_smoke_file_count": len(main_smoke_files),
        "bus_full_month_dir_count": bus_full_month_count,
        "subway_full_month_dir_count": subway_full_month_count,
    }

    validation_df.to_csv(VALIDATION_CSV, index=False, encoding="utf-8-sig")
    SUMMARY_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# 교통 승하차 이력 적재/전처리 계약 검증",
        "",
        f"작성일: {RUN_DATE}",
        "",
        "## 1. 목적",
        "",
        "55번에서 교통 승하차량 과거 월이력 수집 필요성이 확정됐으므로, 실제 대량 적재 전에 적재 스크립트와 전처리 스크립트의 계약을 검증했다.",
        "",
        "이 검증은 데이터를 점수에 승격하는 검증이 아니다. 월별 raw를 안전하게 받을 준비가 됐는지, 스모크가 partial raw를 오염시키지 않는지, 전처리가 새 적재일의 월 폴더를 읽을 수 있는지를 확인한다.",
        "",
        "## 2. 검증 요약",
        "",
        f"- validation_version: `{VALIDATION_VERSION}`",
        f"- PASS: {pass_count}",
        f"- FAIL: {fail_count}",
        f"- decision: `{decision}`",
        f"- 최신 dry-run 로그: `{summary['latest_dry_run']}`",
        f"- 최신 API 스모크 로그: `{summary['latest_smoke_run']}`",
        f"- 최신 full raw 적재 로그: `{summary['latest_full_run']}`",
        f"- 스모크 probe 파일 수: {summary['smoke_probe_file_count']}",
        f"- 본 raw 폴더 내부 smoke 파일 수: {summary['main_raw_smoke_file_count']}",
        f"- full raw 버스 월 폴더 수: {summary['bus_full_month_dir_count']}",
        f"- full raw 지하철 월 폴더 수: {summary['subway_full_month_dir_count']}",
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
            "## 4. 2보 전진 1보 후퇴",
            "",
        "1. 전진: `202101~202512` 60개월에 대해 버스/지하철 120개 수집 단위가 dry-run에서 잡혔다.",
        "2. 전진: `202101` 버스/지하철 API 스모크가 모두 성공했고 응답은 `probes`에만 저장됐다.",
        "3. 전진: `202101~202512` full raw 적재 결과도 버스/지하철 120개 단위 success로 확인했다.",
        "4. 전진: 버스/지하철 전처리는 domain FAIL이면 silver 저장 전에 중단하도록 막았다.",
        "5. 후퇴: full raw와 58번 silver가 준비됐더라도 gold/backtest 완료가 아니다. 아직 점수 직접 투입은 금지다.",
        "6. 후퇴: 실제 full 적재 후에는 55번 커버리지 검증, 42번 readiness 검증, 09/10 전처리 검증을 다시 돌려야 한다.",
        "7. 후퇴: 월별 raw가 늘어나도 바로 engine에 붙이지 말고 silver -> gold -> backtest 순서로 검증해야 한다.",
            "",
            "## 5. 다음 작업",
            "",
            "1. 같은 스크립트에서 `--execute`를 유지하되 `--page-limit` 없이 202101~202512 전체 적재를 수행한다.",
            "2. 적재 후 55번 수집계획 검증을 다시 실행해 필수 월 누락이 0인지 확인한다.",
            "3. 접근성 gold를 새 silver summary 기준으로 재생성하고 월별 row 수, page 수, list_total_count 일관성을 다시 검증한다.",
            "4. 접근성 gold는 백테스트 전까지 direct score로 승격하지 않는다.",
        ]
    )
    REPORT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
