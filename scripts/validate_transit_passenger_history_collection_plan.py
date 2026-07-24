# -*- coding: utf-8 -*-
"""
교통 승하차량 과거 월이력 수집계획 검증.

목적:
  - 현재 datacorpus에 있는 버스/지하철 승하차량 월 커버리지와
    백테스트 기간에 필요한 월을 직접 비교한다.
  - 생활이동/OD 월자료를 승하차량 월이력으로 대체하지 않도록 막는다.
  - 실제 적재 전에 어떤 월을 더 받아야 하는지 CSV와 MD로 남긴다.

주의:
  - 이 스크립트는 원천 API를 호출하지 않는다.
  - 현재 worktree의 raw/silver/gold/검증 산출물만 근거로 판단한다.
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
SILVER = ROOT / "datacorpus" / "_silver"
GOLD = ROOT / "datacorpus" / "_gold"
SCORE_BACKTEST = ROOT / "datacorpus" / "_score_backtest"
RULE_DATA = ROOT / "datacorpus" / "_rule_validation"
RULE_DOCS = ROOT / "research" / "rule_validation"

RUN_DATE = "2026-07-07"
VALIDATION_VERSION = "transit_passenger_history_collection_plan.v1.1-20260707"

BACKTEST_START_MONTH = "202101"
BACKTEST_END_MONTH = "202512"
CURRENT_EVIDENCE_MONTHS = ["202601", "202602", "202603", "202604", "202605"]

BUS_SOURCE_ID = "seoul_bus_stop_passengers_hourly"
SUBWAY_SOURCE_ID = "seoul_subway_station_passengers_hourly"
BUS_SERVICE = "CardBusTimeNew"
SUBWAY_SERVICE = "CardSubwayTime"

VALIDATION_CSV = RULE_DATA / "55_transit_passenger_history_collection_plan_validation.csv"
REQUIRED_MONTHS_CSV = RULE_DATA / "55_transit_passenger_history_required_months.csv"
MISSING_MONTHS_CSV = RULE_DATA / "55_transit_passenger_history_missing_months.csv"
SERVICE_PLAN_CSV = RULE_DATA / "55_transit_passenger_history_service_plan.csv"
SUMMARY_JSON = RULE_DATA / "55_transit_passenger_history_collection_plan_summary.json"
REPORT_MD = RULE_DOCS / "55_transit_passenger_history_collection_plan_20260707.md"


def read_csv(path: Path, **kwargs: Any) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, encoding="utf-8-sig", low_memory=False, **kwargs)


def write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")


def unique_sorted(values: pd.Series) -> list[str]:
    out: list[str] = []
    for value in values.dropna().astype(str):
        value = value.strip()
        if value and value.lower() != "nan":
            out.append(value)
    return sorted(set(out))


def month_range(start_month: str, end_month: str) -> list[str]:
    start_year = int(start_month[:4])
    start_num = int(start_month[4:6])
    end_year = int(end_month[:4])
    end_num = int(end_month[4:6])
    months: list[str] = []
    year = start_year
    month = start_num
    while (year, month) <= (end_year, end_num):
        months.append(f"{year}{month:02d}")
        month += 1
        if month == 13:
            year += 1
            month = 1
    return months


def month_to_quarter(month: str) -> str:
    if not re.fullmatch(r"\d{6}", str(month)):
        return ""
    year = int(str(month)[:4])
    month_num = int(str(month)[4:6])
    quarter = (month_num - 1) // 3 + 1
    return f"{year}{quarter}"


def scan_raw_month_dirs(relative: str) -> list[str]:
    months: set[str] = set()
    for date_dir in sorted(RAW.glob("20??????")):
        base = date_dir / "seoul_open_data" / "transport" / relative
        if not base.exists():
            continue
        for path in base.iterdir():
            if path.is_dir() and re.fullmatch(r"\d{6}", path.name):
                months.add(path.name)
    return sorted(months)


def count_raw_pages(relative: str, service_prefix: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for month in scan_raw_month_dirs(relative):
        latest_month_dir: Path | None = None
        for date_dir in sorted(RAW.glob("20??????")):
            candidate = date_dir / "seoul_open_data" / "transport" / relative / month
            if candidate.exists():
                latest_month_dir = candidate
        counts[month] = len(list(latest_month_dir.glob(f"{service_prefix}_*.json"))) if latest_month_dir else 0
    return counts


def read_month_column(path: Path) -> list[str]:
    df = read_csv(path, usecols=lambda col: col == "기준_월")
    if df.empty or "기준_월" not in df.columns:
        return []
    return unique_sorted(df["기준_월"])


def read_gold_state() -> dict[str, Any]:
    path = GOLD / "gold_accessibility_transit_q_area_candidate.csv"
    df = read_csv(
        path,
        usecols=lambda col: col
        in [
            "기준_월",
            "기준_년분기_코드",
            "direct_score_allowed",
            "proxy_score_allowed_after_validation",
            "temporal_coverage_status",
        ],
    )
    if df.empty:
        return {
            "months": [],
            "quarters": [],
            "row_count": 0,
            "direct_score_allowed_true_count": 0,
            "proxy_score_allowed_true_count": 0,
            "temporal_coverage_status": [],
        }
    direct_true = 0
    proxy_true = 0
    if "direct_score_allowed" in df.columns:
        direct_true = int(df["direct_score_allowed"].astype(str).str.lower().isin(["true", "1", "yes"]).sum())
    if "proxy_score_allowed_after_validation" in df.columns:
        proxy_true = int(
            df["proxy_score_allowed_after_validation"].astype(str).str.lower().isin(["true", "1", "yes"]).sum()
        )
    return {
        "months": unique_sorted(df.get("기준_월", pd.Series(dtype=str))),
        "quarters": unique_sorted(df.get("기준_년분기_코드", pd.Series(dtype=str))),
        "row_count": int(len(df)),
        "direct_score_allowed_true_count": direct_true,
        "proxy_score_allowed_true_count": proxy_true,
        "temporal_coverage_status": unique_sorted(df.get("temporal_coverage_status", pd.Series(dtype=str))),
    }


def read_backtest_quarters() -> list[str]:
    parquet_path = SCORE_BACKTEST / "location_score_backtest_rows.parquet"
    if parquet_path.exists():
        try:
            df = pd.read_parquet(parquet_path, columns=["기준_년분기_코드"])
            return unique_sorted(df["기준_년분기_코드"])
        except Exception:
            pass
    sample_path = SCORE_BACKTEST / "location_score_backtest_rows_sample.csv"
    if sample_path.exists():
        sample = read_csv(sample_path, usecols=lambda col: col == "기준_년분기_코드")
        quarters = unique_sorted(sample.get("기준_년분기_코드", pd.Series(dtype=str)))
        if quarters:
            return quarters
    return sorted(set(month_to_quarter(month) for month in month_range(BACKTEST_START_MONTH, BACKTEST_END_MONTH)))


def read_transport_manifest() -> pd.DataFrame:
    manifest = read_csv(RAW / "ingest_manifest.csv", dtype=str).fillna("")
    if manifest.empty or "source_id" not in manifest.columns:
        return pd.DataFrame()
    return manifest[manifest["source_id"].isin([BUS_SOURCE_ID, SUBWAY_SOURCE_ID])].copy()


def read_failed_transport() -> pd.DataFrame:
    failed = read_csv(RAW / "failed_downloads.csv", dtype=str).fillna("")
    if failed.empty or "source_id" not in failed.columns:
        return pd.DataFrame()
    return failed[failed["source_id"].isin([BUS_SOURCE_ID, SUBWAY_SOURCE_ID])].copy()


def build_required_months_table(
    required_months: list[str],
    bus_raw_months: list[str],
    subway_raw_months: list[str],
    bus_silver_months: list[str],
    subway_silver_months: list[str],
    failed_transport: pd.DataFrame,
) -> pd.DataFrame:
    failed_notes: dict[tuple[str, str], str] = {}
    if not failed_transport.empty:
        for _, row in failed_transport.iterrows():
            text = " ".join(str(row.get(col, "")) for col in ["dataset_name", "failure_reason_ko", "request_url_redacted"])
            months = re.findall(r"20\d{4}", text)
            for month in months:
                failed_notes[(str(row.get("source_id", "")), month)] = str(row.get("failure_type", ""))

    rows: list[dict[str, Any]] = []
    for month in required_months:
        bus_raw = month in bus_raw_months
        subway_raw = month in subway_raw_months
        bus_silver = month in bus_silver_months
        subway_silver = month in subway_silver_months
        missing_services = []
        if not bus_raw:
            missing_services.append("bus_raw")
        if not subway_raw:
            missing_services.append("subway_raw")
        if not bus_silver:
            missing_services.append("bus_silver")
        if not subway_silver:
            missing_services.append("subway_silver")
        rows.append(
            {
                "기준_월": month,
                "기준_년분기_코드": month_to_quarter(month),
                "required_for_backtest": True,
                "bus_raw_present": bus_raw,
                "subway_raw_present": subway_raw,
                "bus_silver_present": bus_silver,
                "subway_silver_present": subway_silver,
                "both_raw_present": bus_raw and subway_raw,
                "both_silver_present": bus_silver and subway_silver,
                "missing_service_count": len(missing_services),
                "missing_services": ",".join(missing_services),
                "bus_failed_record": failed_notes.get((BUS_SOURCE_ID, month), ""),
                "subway_failed_record": failed_notes.get((SUBWAY_SOURCE_ID, month), ""),
                "collection_priority": "P1_BACKTEST_REQUIRED",
                "notes_ko": "백테스트 기간의 접근성 축 검증에 필요한 버스/지하철 승하차량 월이다.",
            }
        )
    return pd.DataFrame(rows)


def build_current_evidence_months_table(
    months: list[str],
    bus_raw_months: list[str],
    subway_raw_months: list[str],
    bus_silver_months: list[str],
    subway_silver_months: list[str],
    failed_transport: pd.DataFrame,
) -> pd.DataFrame:
    base = build_required_months_table(
        months,
        bus_raw_months,
        subway_raw_months,
        bus_silver_months,
        subway_silver_months,
        failed_transport,
    )
    if base.empty:
        return base
    base["required_for_backtest"] = False
    base["collection_priority"] = "P2_CURRENT_EVIDENCE"
    base["notes_ko"] = "현재/최근 리포트 evidence 보강용 월이다. 과거 백테스트 필수월은 아니다."
    return base


def build_service_plan(required_missing: pd.DataFrame, current_missing: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for source_id, service_name, raw_dir in [
        (BUS_SOURCE_ID, BUS_SERVICE, "bus_stop_passengers_hourly"),
        (SUBWAY_SOURCE_ID, SUBWAY_SERVICE, "subway_station_passengers_hourly"),
    ]:
        if source_id == BUS_SOURCE_ID:
            missing_col = "bus_raw_present"
        else:
            missing_col = "subway_raw_present"
        required_months = required_missing.loc[~required_missing[missing_col], "기준_월"].tolist()
        current_months = current_missing.loc[~current_missing[missing_col], "기준_월"].tolist()
        rows.append(
            {
                "source_id": source_id,
                "service_name": service_name,
                "raw_dir": raw_dir,
                "required_backtest_months_to_collect": ",".join(required_months),
                "required_backtest_month_count": len(required_months),
                "current_evidence_months_to_collect": ",".join(current_months),
                "current_evidence_month_count": len(current_months),
                "call_contract_ko": "월 파라미터를 바꿔 같은 서비스 원천을 월별로 적재한다. 새 raw 월 폴더 추가 후 09/10/31/32/42/43/55 검증을 다시 돌린다.",
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


def build_validation(
    required_table: pd.DataFrame,
    current_table: pd.DataFrame,
    service_plan: pd.DataFrame,
    bus_raw_months: list[str],
    subway_raw_months: list[str],
    bus_silver_months: list[str],
    subway_silver_months: list[str],
    gold_state: dict[str, Any],
    backtest_quarters: list[str],
    manifest: pd.DataFrame,
    failed_transport: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    required_quarters = sorted(required_table["기준_년분기_코드"].unique().tolist())
    present_required_both_raw = int(required_table["both_raw_present"].sum())
    present_required_both_silver = int(required_table["both_silver_present"].sum())
    missing_required_any = int((~required_table["both_raw_present"]).sum())
    current_both_raw = int(current_table["both_raw_present"].sum()) if not current_table.empty else 0
    required_raw_complete = present_required_both_raw == len(required_table)
    required_silver_complete = present_required_both_silver == len(required_table)
    raw_month_union = sorted(set(bus_raw_months) | set(subway_raw_months))
    required_month_set = set(required_table["기준_월"].astype(str))
    required_quarter_set = set(required_table["기준_년분기_코드"].astype(str))
    gold_month_set = set(gold_state["months"])
    gold_quarter_set = set(gold_state["quarters"])
    gold_required_month_count = len(required_month_set.intersection(gold_month_set))
    gold_required_quarter_count = len(required_quarter_set.intersection(gold_quarter_set))
    gold_month_coverage_ready = required_month_set.issubset(gold_month_set)
    gold_quarter_coverage_ready = (
        set(backtest_quarters).issubset(gold_quarter_set)
        if backtest_quarters
        else required_quarter_set.issubset(gold_quarter_set)
    )
    manifest_month_periods = (
        unique_sorted(
            manifest.loc[
                manifest.get("source_period", pd.Series(dtype=str)).astype(str).str.fullmatch(r"\d{6}", na=False),
                "source_period",
            ]
        )
        if not manifest.empty
        else []
    )

    add_check(
        rows,
        "백테스트 요구 월 목록은 2021Q1~2025Q4 전체 60개월인가",
        f"months={len(required_table)}, quarters={len(required_quarters)}, first={required_table['기준_월'].min()}, last={required_table['기준_월'].max()}",
        "60개월, 20개 분기",
        "PASS" if len(required_table) == 60 and len(required_quarters) == 20 else "FAIL",
        "교통 승하차량을 과거 백테스트에 넣으려면 현재 점수 검증 기간과 같은 월/분기 축을 먼저 정의해야 한다.",
    )
    add_check(
        rows,
        "백테스트 필수 raw 승하차량 확보 상태는 명확한가",
        f"bus={bus_raw_months}, subway={subway_raw_months}",
        "수집 전이면 202605 단월, 수집 후이면 202101~202512 필수 raw 동시 확보",
        "PASS" if (bus_raw_months == ["202605"] and subway_raw_months == ["202605"]) or required_raw_complete else "FAIL",
        "현재 보유 raw의 실제 월 범위를 먼저 고정해야 필요한 추가 수집 또는 전처리 범위를 계산할 수 있다.",
    )
    add_check(
        rows,
        "silver 승하차량 월 범위는 raw와 일치하는가",
        f"bus_silver={bus_silver_months}, subway_silver={subway_silver_months}",
        "raw 월과 silver 월 일치",
        "PASS"
        if bus_silver_months == bus_raw_months and subway_silver_months == subway_raw_months
        else "NOT_READY",
        "raw 적재 직후에는 silver가 아직 단월일 수 있다. 이 경우 적재 실패가 아니라 09/10 전처리 재실행 대기 상태로 본다.",
    )
    add_check(
        rows,
        "백테스트 필수 월 중 버스/지하철 raw가 동시에 있는 월은 없는가",
        f"present_required_both_raw={present_required_both_raw}, missing_required_any={missing_required_any}",
        "수집 전 0개월 또는 수집 후 60개월",
        "NOT_READY" if present_required_both_raw < len(required_table) else "PASS",
        "2021~2025 검증에는 같은 기간의 월별 승하차량 raw가 필요하다. full 적재 후에는 이 항목이 PASS가 되어야 한다.",
    )
    add_check(
        rows,
        "백테스트 필수 월 중 silver가 동시에 있는 월은 없는가",
        f"present_required_both_silver={present_required_both_silver}",
        "수집 전 0개월 또는 전처리 후 60개월",
        "NOT_READY" if present_required_both_silver < len(required_table) else "PASS",
        "raw가 확보되어도 silver는 별도 전처리를 거쳐야 하므로 full raw 적재 직후에는 NOT_READY가 정상이다.",
    )
    add_check(
        rows,
        "교통 후보 gold는 직접점수 비허용 상태인가",
        f"months={gold_state['months']}, quarters={gold_state['quarters']}, direct_true={gold_state['direct_score_allowed_true_count']}, proxy_true={gold_state['proxy_score_allowed_true_count']}, coverage={gold_state['temporal_coverage_status']}",
        "gold 월이력 존재 + direct_score_allowed 없음 + proxy evidence 허용 가능",
        "PASS" if gold_state["months"] and gold_state["direct_score_allowed_true_count"] == 0 else "FAIL",
        "proxy_score_allowed_after_validation은 리포트 보조 evidence 허용이며, direct_score_allowed가 0이어야 점수 직접투입 금지 계약을 만족한다.",
    )
    add_check(
        rows,
        "백테스트 분기와 교통 gold 분기는 대응되는가",
        f"backtest_quarters={backtest_quarters}, transit_gold_quarters={gold_state['quarters']}",
        "2021Q1~2025Q4 gold 분기 포함",
        "PASS" if gold_quarter_coverage_ready else "NOT_READY",
        "full-history gold는 백테스트 분기와 대응되어야 한다. 단, 대응되더라도 성능 검증 전 점수 직접 투입은 금지한다.",
    )
    add_check(
        rows,
        "백테스트 필수 월 중 교통 gold가 포함하는 월은 충분한가",
        f"gold_required_month_count={gold_required_month_count}, gold_required_quarter_count={gold_required_quarter_count}",
        "60개월, 20개 분기",
        "PASS" if gold_month_coverage_ready else "NOT_READY",
        "접근성 gold는 상권×월 후보이므로 2021~2025 필수 60개월을 포함해야 백테스트 후보로 볼 수 있다.",
    )
    add_check(
        rows,
        "202606 승하차량 미적재 기록은 실패 로그에 남아 있는가",
        f"failed_rows={len(failed_transport)}, has_202606={failed_transport.astype(str).apply(lambda col: col.str.contains('202606', regex=False, na=False)).any(axis=1).any() if not failed_transport.empty else False}",
        "INFO-200 또는 실패 기록에 202606 명시",
        "PASS"
        if not failed_transport.empty
        and failed_transport.astype(str).apply(lambda col: col.str.contains("202606", regex=False, na=False)).any(axis=1).any()
        else "NOT_READY",
        "최신월이 비어 있으면 조용히 누락시키지 말고 실패/미적재 이유를 기록해야 다음 수집 판단이 가능하다.",
    )
    add_check(
        rows,
        "현재 evidence 보강 월 202601~202605 중 확보된 월은 202605뿐인가",
        f"current_months={CURRENT_EVIDENCE_MONTHS}, both_raw_present={current_both_raw}",
        "현재 evidence 보강은 202605만 보유",
        "NOT_READY" if current_both_raw < len(CURRENT_EVIDENCE_MONTHS) else "PASS",
        "운영 리포트의 최근 접근성 설명을 안정화하려면 백테스트 필수월과 별도로 최근월도 축적해야 한다.",
    )
    add_check(
        rows,
        "서비스별 수집 계획이 버스와 지하철을 모두 포함하는가",
        f"sources={service_plan['source_id'].tolist()}, required_counts={service_plan['required_backtest_month_count'].tolist()}",
        "CardBusTimeNew와 CardSubwayTime 각각 계획 또는 완료 상태",
        "PASS"
        if set(service_plan["source_id"]) == {BUS_SOURCE_ID, SUBWAY_SOURCE_ID}
        and (
            int(service_plan["required_backtest_month_count"].min()) >= 60
            or (required_raw_complete and int(service_plan["required_backtest_month_count"].max()) == 0)
        )
        else "FAIL",
        "접근성 축은 버스와 지하철을 같이 보므로 한쪽만 수집하면 축 해석이 비대칭이 된다. full 적재 후에는 남은 수집계획이 0이어야 한다.",
    )
    add_check(
        rows,
        "manifest의 교통 승하차량 수집 기록은 raw 월 범위를 포괄하는가",
        f"manifest_rows={len(manifest)}, month_periods={manifest_month_periods}",
        "manifest 월 목록이 raw 월 범위를 포함",
        "PASS"
        if not manifest.empty
        and set(raw_month_union).issubset(set(manifest_month_periods))
        else "FAIL",
        "manifest에는 실행일 같은 8자리 스냅샷 값도 섞일 수 있으므로 승하차량 월 커버리지는 YYYYMM 형식만 따로 보고 raw 월 범위와 대조한다.",
    )
    add_check(
        rows,
        "55번 검증은 점수 승격이 아니라 수집계획 확정인가",
        "decision=collection_plan_only",
        "engine promotion 금지",
        "PASS",
        "현재 작업은 새 데이터를 받기 전의 계획 검증이므로 엔진 산식이나 점수축을 바꾸지 않는다.",
    )
    return pd.DataFrame(rows)


def write_report(
    validation: pd.DataFrame,
    summary: dict[str, Any],
    required_table: pd.DataFrame,
    current_table: pd.DataFrame,
    service_plan: pd.DataFrame,
) -> None:
    RULE_DOCS.mkdir(parents=True, exist_ok=True)
    pass_count = int((validation["result"] == "PASS").sum())
    not_ready_count = int((validation["result"] == "NOT_READY").sum())
    fail_count = int((validation["result"] == "FAIL").sum())

    required_missing_count = int((~required_table["both_raw_present"]).sum())
    current_missing_count = int((~current_table["both_raw_present"]).sum()) if not current_table.empty else 0
    required_raw_complete = required_missing_count == 0
    required_silver_missing_count = int((~required_table["both_silver_present"]).sum())
    gold_state = summary.get("gold_state", {})
    gold_months = set(gold_state.get("months", []))
    gold_quarters = set(gold_state.get("quarters", []))
    required_month_set = set(required_table["기준_월"].astype(str))
    required_quarter_set = set(required_table["기준_년분기_코드"].astype(str))
    gold_ready = (
        required_month_set.issubset(gold_months)
        and required_quarter_set.issubset(gold_quarters)
        and int(gold_state.get("direct_score_allowed_true_count", 1)) == 0
    )
    raw_status_sentence = (
        "2021~2025 백테스트 필수 raw/silver/gold 월이력은 확보됐지만, 성능 백테스트와 CRS 검토 전이므로 엔진 점수 직접 반영은 하지 않는다."
        if required_raw_complete and required_silver_missing_count == 0 and gold_ready
        else
        "2021~2025 백테스트 필수 raw 60개월은 확보됐지만, silver/gold 전처리와 백테스트 검증 전이므로 엔진 점수 직접 반영은 하지 않는다."
        if required_raw_complete
        else "따라서 현재 교통 승하차량 후보는 계속 evidence-only이며, 엔진 점수 직접 반영은 하지 않는다."
    )
    step_one = (
        "1. 전진: 55번 재검증에서 백테스트 필수 `202101~202512` raw 60개월이 버스/지하철 모두 확보됐음을 확인했다."
        if required_raw_complete
        else "1. 전진: 43번에서 전처리 스크립트가 월 폴더 자동탐색 구조임을 확인했다."
    )
    step_two = (
        "2. 전진: 31번 접근성 gold가 백테스트 필수 월/분기와 대응되는지 별도 검증 조건으로 분리했다."
        if gold_ready
        else "2. 전진: raw 확보와 silver/gold 미처리 상태를 분리해, 다음 작업 범위를 명확히 했다."
        if required_raw_complete
        else "2. 전진: 55번에서 백테스트 필수 월 60개월을 명시해 수집 대상이 모호하지 않게 됐다."
    )
    step_three = (
        "3. 후퇴: raw/silver/gold 월이력이 확보돼도 승하차량은 접근성 프록시이므로 성능 백테스트 전 직접 점수 투입은 계속 금지한다."
        if gold_ready
        else
        "3. 후퇴: 현재 silver/gold는 아직 202605 단월이므로 접근성 점수 직접 투입은 계속 금지한다."
        if required_raw_complete and required_silver_missing_count
        else "3. 후퇴: 현재 실제 승하차량 raw/silver는 202605 단월뿐이므로 접근성 점수 직접 투입은 아직 금지한다."
    )
    next_one = (
        "1. 접근성 후보 gold를 백테스트 성능 검증에 넣을 수 있는 별도 실험을 수행한다."
        if gold_ready
        else "1. 적재된 `202101~202512` raw를 기준으로 버스/지하철 silver와 접근성 gold를 다시 만든다."
        if required_raw_complete
        else "1. 백테스트 필수 구간 `202101~202512`에 대해 `CardBusTimeNew`, `CardSubwayTime`을 월별로 적재한다."
    )

    lines = [
        "# 교통 승하차량 과거 월이력 수집계획 검증",
        "",
        f"작성일: {RUN_DATE}",
        "",
        "## 1. 목적",
        "",
        "버스·지하철 승하차량을 접근성 축에 넣기 전에, 현재 보유 월과 백테스트에 필요한 월을 직접 비교한다. 이 문서는 새 데이터를 수집하지 않고, 전처리 전에 어떤 월을 더 확보해야 하는지 확정한다.",
        "",
        "## 2. 결론",
        "",
        f"- 판정: `{summary['decision']}`",
        f"- 백테스트 필수 월: {summary['required_backtest_month_count']}개월",
        f"- 백테스트 필수 월 중 현재 버스·지하철 raw 동시 확보: {summary['required_backtest_both_raw_present_count']}개월",
        f"- 백테스트 필수 월 중 추가 수집 필요: {required_missing_count}개월",
        f"- 현재 evidence 보강 월 중 추가 수집 필요: {current_missing_count}개월",
        f"- {raw_status_sentence}",
        "",
        "## 3. 검증 결과",
        "",
        f"- validation_version: `{VALIDATION_VERSION}`",
        f"- PASS: {pass_count}",
        f"- NOT_READY: {not_ready_count}",
        f"- FAIL: {fail_count}",
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
                result=str(row["result"]).replace("|", "/"),
                reason_ko=str(row["reason_ko"]).replace("|", "/"),
            )
        )

    lines.extend(
        [
            "",
            "## 4. 서비스별 추가 수집 계획",
            "",
            "| source_id | service_name | 백테스트 필수월 수 | 최근 evidence 월 수 | 호출 계약 |",
            "|---|---|---:|---:|---|",
        ]
    )
    for _, row in service_plan.iterrows():
        lines.append(
            f"| {row['source_id']} | {row['service_name']} | {row['required_backtest_month_count']} | {row['current_evidence_month_count']} | {row['call_contract_ko']} |"
        )

    lines.extend(
        [
            "",
            "상세 월 목록은 아래 CSV에 남긴다.",
            "",
            "- `datacorpus/_rule_validation/55_transit_passenger_history_required_months.csv`",
            "- `datacorpus/_rule_validation/55_transit_passenger_history_missing_months.csv`",
            "- `datacorpus/_rule_validation/55_transit_passenger_history_service_plan.csv`",
            "",
            "## 5. 2보 전진 1보 후퇴 검토",
            "",
            step_one,
            step_two,
            step_three,
            "4. 후퇴: 202606은 미적재/INFO-200 기록이 있으므로 최신월이 있다고 가정하지 않는다.",
            "5. 후퇴: 생활이동/OD 월파일은 승하차량 원천이 아니므로 빈 교통 월을 대체하지 않는다.",
            "",
            "## 6. 다음 작업",
            "",
            next_one,
            "2. 운영 리포트 최신 evidence 보강용으로 `202601~202605`도 같은 계약으로 정리한다.",
            "3. 새 raw 월 폴더가 생기면 버스/지하철 silver를 다시 만들고 31/32/42/43/55 검증을 재실행한다.",
            "4. 20개 분기 커버리지와 시간누수 검증을 통과한 뒤에만 접근성 점수축 승격을 검토한다.",
        ]
    )
    REPORT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    bus_raw_months = scan_raw_month_dirs("bus_stop_passengers_hourly")
    subway_raw_months = scan_raw_month_dirs("subway_station_passengers_hourly")
    bus_page_counts = count_raw_pages("bus_stop_passengers_hourly", BUS_SERVICE)
    subway_page_counts = count_raw_pages("subway_station_passengers_hourly", SUBWAY_SERVICE)
    bus_silver_months = read_month_column(SILVER / "silver_bus_passenger_route_stop_month_summary.csv")
    subway_silver_months = read_month_column(SILVER / "silver_subway_passenger_station_month_summary.csv")
    gold_state = read_gold_state()
    backtest_quarters = read_backtest_quarters()
    manifest = read_transport_manifest()
    failed_transport = read_failed_transport()

    required_months = month_range(BACKTEST_START_MONTH, BACKTEST_END_MONTH)
    required_table = build_required_months_table(
        required_months,
        bus_raw_months,
        subway_raw_months,
        bus_silver_months,
        subway_silver_months,
        failed_transport,
    )
    current_table = build_current_evidence_months_table(
        CURRENT_EVIDENCE_MONTHS,
        bus_raw_months,
        subway_raw_months,
        bus_silver_months,
        subway_silver_months,
        failed_transport,
    )
    service_plan = build_service_plan(required_table, current_table)
    validation = build_validation(
        required_table,
        current_table,
        service_plan,
        bus_raw_months,
        subway_raw_months,
        bus_silver_months,
        subway_silver_months,
        gold_state,
        backtest_quarters,
        manifest,
        failed_transport,
    )

    missing_required = required_table[~required_table["both_raw_present"]].copy()
    missing_current = current_table[~current_table["both_raw_present"]].copy()
    missing_all = pd.concat([missing_required, missing_current], ignore_index=True)

    pass_count = int((validation["result"] == "PASS").sum())
    not_ready_count = int((validation["result"] == "NOT_READY").sum())
    fail_count = int((validation["result"] == "FAIL").sum())
    required_both_raw_count = int(required_table["both_raw_present"].sum())
    current_both_raw_count = int(current_table["both_raw_present"].sum())
    required_silver_count = int(required_table["both_silver_present"].sum())
    required_month_set = set(required_table["기준_월"].astype(str))
    required_quarter_set = set(required_table["기준_년분기_코드"].astype(str))
    gold_month_set = set(gold_state["months"])
    gold_quarter_set = set(gold_state["quarters"])
    gold_required_month_count = len(required_month_set.intersection(gold_month_set))
    gold_required_quarter_count = len(required_quarter_set.intersection(gold_quarter_set))
    gold_backtest_ready = (
        required_month_set.issubset(gold_month_set)
        and required_quarter_set.issubset(gold_quarter_set)
        and gold_state["direct_score_allowed_true_count"] == 0
    )
    if fail_count:
        decision = "TRANSIT_PASSENGER_HISTORY_COLLECTION_PLAN_FAILED"
        decision_reason = "교통 승하차량 수집/전처리 상태 검증에 실패 항목이 있어 원천 범위와 manifest를 먼저 확인해야 한다."
    elif required_both_raw_count == len(required_table) and required_silver_count < len(required_table):
        decision = "TRANSIT_PASSENGER_HISTORY_RAW_COLLECTED_SILVER_NOT_READY"
        decision_reason = "2021~2025 백테스트 필수 60개월 raw는 확보됐지만 silver/gold 전처리 전이므로 점수 직접 투입은 보류한다."
    elif required_both_raw_count == len(required_table) and required_silver_count == len(required_table) and gold_backtest_ready:
        decision = "TRANSIT_PASSENGER_HISTORY_RAW_SILVER_GOLD_READY_BACKTEST_NOT_PROMOTED"
        decision_reason = "백테스트 필수 raw/silver/gold 월이력은 확보됐지만 성능 백테스트와 CRS 검토 전까지 점수 직접 투입은 보류한다."
    elif required_both_raw_count == len(required_table) and required_silver_count == len(required_table):
        decision = "TRANSIT_PASSENGER_HISTORY_RAW_AND_SILVER_READY_NOT_PROMOTED"
        decision_reason = "백테스트 필수 raw와 silver는 확보됐지만 gold/backtest 검증 전까지 점수 직접 투입은 보류한다."
    else:
        decision = "TRANSIT_PASSENGER_HISTORY_COLLECTION_PLAN_READY_NOT_PROMOTED"
        decision_reason = "현재 승하차량 raw는 백테스트 필수월이 부족하므로 점수 직접 투입은 보류하고, 2021~2025 필수 월 수집계획을 유지한다."

    summary = {
        "run_date": RUN_DATE,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "validation_version": VALIDATION_VERSION,
        "pass_count": pass_count,
        "not_ready_count": not_ready_count,
        "fail_count": fail_count,
        "decision": decision,
        "decision_reason_ko": decision_reason,
        "bus_raw_months": bus_raw_months,
        "subway_raw_months": subway_raw_months,
        "bus_page_counts": bus_page_counts,
        "subway_page_counts": subway_page_counts,
        "bus_silver_months": bus_silver_months,
        "subway_silver_months": subway_silver_months,
        "gold_state": gold_state,
        "gold_required_backtest_month_present_count": gold_required_month_count,
        "gold_required_backtest_quarter_present_count": gold_required_quarter_count,
        "gold_backtest_ready": gold_backtest_ready,
        "backtest_quarters": backtest_quarters,
        "required_backtest_month_count": len(required_table),
        "required_backtest_quarter_count": int(required_table["기준_년분기_코드"].nunique()),
        "required_backtest_both_raw_present_count": required_both_raw_count,
        "required_backtest_missing_month_count": int(len(missing_required)),
        "current_evidence_month_count": len(current_table),
        "current_evidence_both_raw_present_count": current_both_raw_count,
        "current_evidence_missing_month_count": int(len(missing_current)),
        "manifest_transport_rows": int(len(manifest)),
        "failed_transport_rows": int(len(failed_transport)),
        "engine_promotion_ready": False,
        "next_validation_number": 57,
    }

    RULE_DATA.mkdir(parents=True, exist_ok=True)
    write_csv(required_table, REQUIRED_MONTHS_CSV)
    write_csv(missing_all, MISSING_MONTHS_CSV)
    write_csv(service_plan, SERVICE_PLAN_CSV)
    write_csv(validation, VALIDATION_CSV)
    SUMMARY_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(validation, summary, required_table, current_table, service_plan)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if fail_count:
        raise SystemExit(fail_count)


if __name__ == "__main__":
    main()
