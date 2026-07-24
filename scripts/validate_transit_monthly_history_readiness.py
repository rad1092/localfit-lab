# -*- coding: utf-8 -*-
"""
교통 승하차량 월별 이력 투입 준비도 검증.

목적:
  - 현재 datacorpus 안의 버스/지하철 승하차량이 백데이터 검증에 충분한지 판단한다.
  - 생활이동/OD 월파일을 교통 승하차량 월이력으로 오인해 섞는 것을 막는다.
  - 월이력이 확보되어도 백테스트 전에는 현재 리포트 evidence 후보로만 남기고 점수 직접 투입을 보류한다.
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
VALIDATION_VERSION = "transit_monthly_history_readiness.v1.1-20260707"
EXPECTED_BACKTEST_QUARTERS = 20
REQUIRED_BACKTEST_MONTHS = [f"{year}{month:02d}" for year in range(2021, 2026) for month in range(1, 13)]


def read_csv(path: Path, **kwargs: Any) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, encoding="utf-8-sig", low_memory=False, **kwargs)


def month_to_quarter(month: object) -> str:
    text = str(month)
    if not re.fullmatch(r"\d{6}", text):
        return ""
    year = int(text[:4])
    month_num = int(text[4:6])
    quarter = (month_num - 1) // 3 + 1
    return f"{year}{quarter}"


def unique_sorted(values: pd.Series) -> list[str]:
    out = []
    for value in values.dropna().astype(str):
        value = value.strip()
        if value and value.lower() != "nan":
            out.append(value)
    return sorted(set(out))


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


def backtest_quarters() -> list[str]:
    parquet_path = SCORE_BACKTEST / "location_score_backtest_rows.parquet"
    if parquet_path.exists():
        df = pd.read_parquet(parquet_path, columns=["기준_년분기_코드"])
        return unique_sorted(df["기준_년분기_코드"])
    return []


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
    validation_path = RULE_DATA / "42_transit_monthly_history_readiness_validation.csv"
    summary_path = RULE_DATA / "42_transit_monthly_history_readiness_summary.json"
    report_path = RULE_DOCS / "42_transit_monthly_history_readiness_validation_20260707.md"

    validation_df.to_csv(validation_path, index=False, encoding="utf-8-sig")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# 교통 승하차량 월별 이력 투입 준비도 검증",
        "",
        f"작성일: {RUN_DATE}",
        "",
        "## 1. 목적",
        "",
        "버스/지하철 승하차량을 접근성 축에 넣기 전에, 현재 `datacorpus/`에 쌓인 월별 이력이 백데이터 검증에 충분한지 판단한다.",
        "",
        "결론부터 말하면 교통 승하차량은 raw/silver/gold 월 커버리지와 점수 승격 가능성을 분리해서 봐야 한다. 월이력이 확보되어도 backtest와 CRS 검토 전에는 점수 엔진에 직접 넣지 않는다.",
        "",
        "## 2. 근거 자료",
        "",
        "- `research/rule_validation/09_subway_passenger_silver_validation_20260703.md`",
        "- `research/rule_validation/10_bus_passenger_silver_validation_20260703.md`",
        "- `research/rule_validation/31_transit_accessibility_candidate_validation_20260707.md`",
        "- `research/rule_validation/32_transit_monthly_coverage_validation_20260707.md`",
        "- `research/algorithm_evidence_sources/data_docs/seoul_open_data_bus_stop_passengers_hourly_OA-12913.html`",
        "- `research/algorithm_evidence_sources/data_docs/seoul_open_data_subway_station_passengers_hourly_OA-12252.html`",
        "- `datacorpus/_raw_ingest/ingest_manifest.csv`",
        "",
        "## 3. 검증 결과",
        "",
        f"- validation_version: `{summary['validation_version']}`",
        f"- PASS: {summary['pass_count']}",
        f"- NOT_READY: {summary['not_ready_count']}",
        f"- FAIL: {summary['fail_count']}",
        f"- 판정: `{summary['decision']}`",
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
            "1. 전진: 버스/지하철 승하차량은 raw와 silver 월 커버리지를 분리해 확인한다.",
            "2. 전진: exact 좌표 결합 교통점만 상권 polygon 주변 후보로 붙였기 때문에 공간 후보 자체는 재사용 가능하다.",
            "3. 후퇴: 월 커버리지가 충분해도 승하차량은 실제 방문자나 구매자가 아니므로 성능 백테스트 전 점수축 승격은 금지한다.",
            "4. 후퇴: `spatial_od`의 202601~202605 생활이동 파일은 승하차량이 아니므로 교통 승하차 월이력 보강으로 섞지 않는다.",
            "5. 후퇴: 승하차량은 실제 상권 방문자·구매자·도보시간이 아니므로 리포트 문구도 접근성 강도 프록시로 제한한다.",
            "",
            "## 5. 다음 작업",
            "",
            "1. 버스 `CardBusTimeNew`, 지하철 `CardSubwayTime`의 2021Q1~2025Q4 월 커버리지를 유지한다.",
            "2. 새 월 폴더가 추가되면 09/10/31/32/42 검증을 다시 수행한다.",
            "3. 20개 분기 대응 월과 성능 검증이 같이 통과하기 전까지 교통 승하차량은 `접근성 evidence 후보`로만 표시한다.",
        ]
    )
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    bus_raw_months = scan_raw_month_dirs("bus_stop_passengers_hourly")
    subway_raw_months = scan_raw_month_dirs("subway_station_passengers_hourly")
    bus_page_counts = count_raw_pages("bus_stop_passengers_hourly", "CardBusTimeNew")
    subway_page_counts = count_raw_pages("subway_station_passengers_hourly", "CardSubwayTime")

    bus_summary = read_csv(
        SILVER / "silver_bus_passenger_route_stop_month_summary.csv",
        usecols=lambda col: col == "기준_월",
    )
    subway_summary = read_csv(
        SILVER / "silver_subway_passenger_station_month_summary.csv",
        usecols=lambda col: col == "기준_월",
    )
    transit_gold = read_csv(
        GOLD / "gold_accessibility_transit_q_area_candidate.csv",
        usecols=lambda col: col in ["기준_월", "기준_년분기_코드", "direct_score_allowed"],
    )
    manifest = read_csv(RAW / "ingest_manifest.csv", dtype=str).fillna("")
    source_registry = read_csv(RAW / "source_registry.csv", dtype=str).fillna("")

    bus_silver_months = unique_sorted(bus_summary.get("기준_월", pd.Series(dtype=str)))
    subway_silver_months = unique_sorted(subway_summary.get("기준_월", pd.Series(dtype=str)))
    gold_months = unique_sorted(transit_gold.get("기준_월", pd.Series(dtype=str)))
    gold_quarters = unique_sorted(transit_gold.get("기준_년분기_코드", pd.Series(dtype=str)))
    bt_quarters = backtest_quarters()

    transport_manifest = manifest[
        manifest.get("source_id", pd.Series(dtype=str)).isin(
            ["seoul_bus_stop_passengers_hourly", "seoul_subway_station_passengers_hourly"]
        )
    ].copy()
    manifest_months = unique_sorted(
        transport_manifest.loc[transport_manifest["source_period"].str.fullmatch(r"\d{6}", na=False), "source_period"]
    )
    transport_main_manifest = transport_manifest[
        ~transport_manifest.get("raw_path", pd.Series(dtype=str)).astype(str).str.contains(r"[\\/]probes[\\/]", regex=True, na=False)
    ].copy()
    transport_probe_manifest = transport_manifest[
        transport_manifest.get("raw_path", pd.Series(dtype=str)).astype(str).str.contains(r"[\\/]probes[\\/]", regex=True, na=False)
    ].copy()
    main_manifest_months = unique_sorted(
        transport_main_manifest.loc[
            transport_main_manifest["source_period"].str.fullmatch(r"\d{6}", na=False), "source_period"
        ]
    )
    probe_manifest_months = unique_sorted(
        transport_probe_manifest.loc[
            transport_probe_manifest["source_period"].str.fullmatch(r"\d{6}", na=False), "source_period"
        ]
    )
    registry_sources = set(source_registry.get("source_id", pd.Series(dtype=str)))

    spatial_od_months: set[str] = set()
    spatial_od_root = ROOT / "datacorpus" / "_final" / "spatial_od"
    if spatial_od_root.exists():
        for path in spatial_od_root.rglob("*"):
            if path.is_file():
                spatial_od_months.update(re.findall(r"20\d{4}", path.name))

    rows: list[dict[str, Any]] = []
    required_months = set(REQUIRED_BACKTEST_MONTHS)
    required_quarters = sorted({month_to_quarter(month) for month in REQUIRED_BACKTEST_MONTHS})
    raw_ready = required_months.issubset(set(bus_raw_months)) and required_months.issubset(set(subway_raw_months))
    silver_ready = required_months.issubset(set(bus_silver_months)) and required_months.issubset(set(subway_silver_months))
    gold_quarter_ready = set(bt_quarters).issubset(set(gold_quarters)) if bt_quarters else len(gold_quarters) >= EXPECTED_BACKTEST_QUARTERS
    gold_direct_blocked = (
        "direct_score_allowed" in transit_gold.columns
        and not transit_gold["direct_score_allowed"].astype(str).str.lower().isin(["true", "1", "yes"]).any()
    )
    add_check(
        rows,
        "버스 raw 월 폴더는 백테스트 필수 60개월을 포함하는가",
        f"months={bus_raw_months}, page_counts={bus_page_counts}",
        "202101~202512 60개월 포함",
        "PASS" if required_months.issubset(set(bus_raw_months)) else "NOT_READY",
        "버스 승하차량은 월별 원천이므로 과거 백테스트에 넣으려면 기간별 월 데이터가 필요하다.",
    )
    add_check(
        rows,
        "지하철 raw 월 폴더는 백테스트 필수 60개월을 포함하는가",
        f"months={subway_raw_months}, page_counts={subway_page_counts}",
        "202101~202512 60개월 포함",
        "PASS" if required_months.issubset(set(subway_raw_months)) else "NOT_READY",
        "지하철 승하차량도 월별 원천이므로 과거 백테스트에 넣으려면 기간별 월 데이터가 필요하다.",
    )
    add_check(
        rows,
        "silver 승하차량 월 커버리지는 raw와 일치하거나 전처리 대기 상태인가",
        f"bus={bus_silver_months}, subway={subway_silver_months}",
        "백테스트 필수 silver 60개월 포함",
        "PASS" if silver_ready else "NOT_READY",
        "raw 적재 직후에는 silver가 아직 단월일 수 있다. 이 경우 실패가 아니라 09/10 전처리 재실행 대기 상태로 본다.",
    )
    add_check(
        rows,
        "교통 후보 gold는 월이력 후보이면서 직접점수 비허용 상태인가",
        f"months={gold_months}, quarters={gold_quarters}",
        "gold 월이력 존재 + direct_score_allowed=False",
        "PASS" if gold_months and gold_direct_blocked else "FAIL",
        "full-history 교통 후보라도 backtest 승격 전에는 direct_score_allowed=False를 유지해야 한다.",
    )
    add_check(
        rows,
        "백테스트 기간과 교통 gold 분기 커버리지는 대응되는가",
        f"backtest_quarters={len(bt_quarters)}개, transit_quarters={gold_quarters}",
        f"백테스트 {len(bt_quarters) or EXPECTED_BACKTEST_QUARTERS}개 분기와 교통 월별 이력 대응",
        "PASS" if gold_quarter_ready else "NOT_READY",
        "현재 점수 백테스트는 2021Q1~2025Q4 범위를 대상으로 하므로 교통 gold도 같은 분기 커버리지를 가져야 한다.",
    )
    add_check(
        rows,
        "source_registry와 manifest에는 교통 승하차량 원천 계약이 남아 있음",
        f"registry_has_bus={'seoul_bus_stop_passengers_hourly' in registry_sources}, registry_has_subway={'seoul_subway_station_passengers_hourly' in registry_sources}, manifest_months={manifest_months}, main_manifest_months={main_manifest_months}, probe_manifest_months={probe_manifest_months}",
        "원천 계약 존재 + 본 raw 월과 probe 월 분리",
        "PASS"
        if {"seoul_bus_stop_passengers_hourly", "seoul_subway_station_passengers_hourly"}.issubset(registry_sources)
        and main_manifest_months == sorted(set(bus_raw_months) | set(subway_raw_months))
        else "FAIL",
        "보류 판단도 원천 계약과 수집 기록에 연결되어야 한다. 같은 월이 probe 후 full raw로 다시 적재될 수 있으므로 월 값 겹침이 아니라 raw_path의 probes 분리와 본 raw manifest 포함 여부를 본다.",
    )
    add_check(
        rows,
        "생활이동/OD 월파일은 교통 승하차량 월이력으로 대체하지 않음",
        f"spatial_od_months={sorted(spatial_od_months)}",
        "OD는 별도 유입/이동 축, 승하차량 대체 금지",
        "PASS" if spatial_od_months else "NOT_READY",
        "생활이동은 자치구 OD/이동량이고 버스·지하철 승하차 원천이 아니므로 같은 월이 있어도 교통 승하차량으로 섞지 않는다.",
    )

    validation_df = pd.DataFrame(rows)
    pass_count = int((validation_df["result"] == "PASS").sum())
    not_ready_count = int((validation_df["result"] == "NOT_READY").sum())
    fail_count = int((validation_df["result"] == "FAIL").sum())
    required_months = {f"{year}{month:02d}" for year in range(2021, 2026) for month in range(1, 13)}
    raw_ready = required_months.issubset(set(bus_raw_months)) and required_months.issubset(set(subway_raw_months))
    silver_ready = required_months.issubset(set(bus_silver_months)) and required_months.issubset(set(subway_silver_months))
    gold_ready = gold_quarter_ready and gold_direct_blocked
    if raw_ready and not silver_ready:
        decision_reason = "버스/지하철 승하차량 raw는 2021~2025 백데이터 구간을 확보했지만 silver/gold 전처리 전까지 점수 직접 투입은 보류한다. 생활이동 OD 월파일은 원천과 의미가 달라 승하차량 대체물로 쓰지 않는다."
    elif raw_ready and silver_ready and gold_ready:
        decision_reason = "버스/지하철 승하차량 raw/silver/gold는 2021~2025 백데이터 구간을 포함하지만, 성능 백테스트와 CRS 검토 전까지 점수 직접 투입은 보류한다. 생활이동 OD 월파일은 원천과 의미가 달라 승하차량 대체물로 쓰지 않는다."
    elif raw_ready and silver_ready:
        decision_reason = "버스/지하철 승하차량 raw와 silver는 2021~2025 백데이터 구간을 확보했지만 gold 커버리지 검증 전까지 점수 직접 투입은 보류한다. 생활이동 OD 월파일은 원천과 의미가 달라 승하차량 대체물로 쓰지 않는다."
    else:
        decision_reason = "현재 버스/지하철 승하차량은 202605 단월 스냅샷이므로 백데이터 검증과 점수 직접 투입에는 부족하다. 생활이동 OD 월파일은 원천과 의미가 달라 승하차량 대체물로 쓰지 않는다."
    summary = {
        "run_date": RUN_DATE,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "validation_version": VALIDATION_VERSION,
        "pass_count": pass_count,
        "not_ready_count": not_ready_count,
        "fail_count": fail_count,
        "bus_raw_months": bus_raw_months,
        "subway_raw_months": subway_raw_months,
        "gold_months": gold_months,
        "gold_quarters": gold_quarters,
        "backtest_quarter_count": len(bt_quarters),
        "decision": "교통_월이력_점수투입_보류",
        "decision_reason_ko": decision_reason,
        "next_validation_number": 43,
    }
    write_report(validation_df, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if fail_count:
        raise SystemExit(fail_count)


if __name__ == "__main__":
    main()
