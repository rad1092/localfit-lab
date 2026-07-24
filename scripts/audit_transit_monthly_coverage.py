# -*- coding: utf-8 -*-
"""
교통 승하차량 월별 커버리지 감사.

목적:
  - 버스/지하철 승하차량 후보 gold를 백테스트에 넣을 수 있는지 판단한다.
  - 월별 커버리지가 부족하면 점수 투입을 막고, 필요한 수집 조건을 문서로 남긴다.

주의:
  - 이 스크립트는 데이터를 새로 수집하지 않는다.
  - 현재 보유한 raw/silver/gold 산출물만 근거로 판단한다.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SILVER = ROOT / "datacorpus" / "_silver"
GOLD = ROOT / "datacorpus" / "_gold"
RAW_INGEST = ROOT / "datacorpus" / "_raw_ingest"
RULE_VALIDATION = ROOT / "datacorpus" / "_rule_validation"
RESEARCH_RULE_VALIDATION = ROOT / "research" / "rule_validation"


FILES = [
    ("버스 월요약", SILVER / "silver_bus_passenger_route_stop_month_summary.csv"),
    ("버스 시간대 매니페스트", SILVER / "silver_bus_passenger_route_stop_month_hour_manifest.csv"),
    ("지하철 월요약", SILVER / "silver_subway_passenger_station_month_summary.csv"),
    ("지하철 시간대 매니페스트", SILVER / "silver_subway_passenger_station_month_hour_manifest.csv"),
    ("교통점 후보", SILVER / "silver_transit_point_accessibility_candidate_points.csv"),
    ("교통 상권 후보", SILVER / "silver_transit_point_trade_area_candidate.csv"),
    ("교통 후보 gold", GOLD / "gold_accessibility_transit_q_area_candidate.csv"),
]


def read_csv(path: Path, **kwargs) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(path)
    return pd.read_csv(path, encoding="utf-8-sig", low_memory=False, **kwargs)


def write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")


def inspect_file(label: str, path: Path) -> dict[str, object]:
    df = read_csv(path, usecols=lambda col: col in ["기준_월", "기준_년분기_코드", "교통_모드"])
    months = sorted(df["기준_월"].dropna().astype(str).unique()) if "기준_월" in df.columns else []
    quarters = sorted(df["기준_년분기_코드"].dropna().astype(str).unique()) if "기준_년분기_코드" in df.columns else []
    modes = {}
    if "교통_모드" in df.columns:
        modes = {str(k): int(v) for k, v in df["교통_모드"].value_counts(dropna=False).items()}
    return {
        "label": label,
        "path": str(path.relative_to(ROOT)),
        "row_count": int(len(df)),
        "month_count": int(len(months)),
        "months": ",".join(months),
        "quarter_count": int(len(quarters)),
        "quarters": ",".join(quarters),
        "mode_counts_json": json.dumps(modes, ensure_ascii=False),
    }


def inspect_manifest() -> pd.DataFrame:
    manifest_path = RAW_INGEST / "ingest_manifest.csv"
    manifest = read_csv(manifest_path)
    target_sources = [
        "seoul_bus_stop_passengers_hourly",
        "seoul_subway_station_passengers_hourly",
        "seoul_bus_stop_location_file",
        "seoul_subway_station_master",
        "seoul_bus_route_node_master",
    ]
    out = manifest[manifest["source_id"].astype(str).isin(target_sources)].copy()
    keep = [
        "source_id", "dataset_name", "collection_status", "source_period",
        "time_unit", "raw_path", "bytes", "quality_notes_ko",
    ]
    return out[keep].copy()


def build_validation(file_audit: pd.DataFrame, manifest_audit: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []

    def add(rule_name: str, observed: object, expected: object, result: str, reason_ko: str) -> None:
        rows.append(
            {
                "validation_id": len(rows) + 1,
                "rule_name": rule_name,
                "observed": observed,
                "expected": expected,
                "result": result,
                "reason_ko": reason_ko,
            }
        )

    bus_months = file_audit.loc[file_audit["label"].eq("버스 월요약"), "month_count"].iloc[0]
    subway_months = file_audit.loc[file_audit["label"].eq("지하철 월요약"), "month_count"].iloc[0]
    candidate_months = file_audit.loc[file_audit["label"].eq("교통 후보 gold"), "month_count"].iloc[0]
    candidate_quarters = file_audit.loc[file_audit["label"].eq("교통 후보 gold"), "quarter_count"].iloc[0]
    all_months = sorted(set(",".join(file_audit["months"].fillna("")).split(",")) - {""})

    add("버스 승하차량 월요약 존재", int(bus_months), "0보다 큼", "PASS" if bus_months > 0 else "FAIL", "버스 승하차량 원천이 아예 없으면 접근성 후보 검증을 할 수 없다.")
    add("지하철 승하차량 월요약 존재", int(subway_months), "0보다 큼", "PASS" if subway_months > 0 else "FAIL", "지하철 승하차량 원천이 아예 없으면 접근성 후보 검증을 할 수 없다.")
    add("교통 후보 gold 생성됨", int(candidate_months), "0보다 큼", "PASS" if candidate_months > 0 else "FAIL", "31번 검증 산출물이 존재해야 월 커버리지 판정을 할 수 있다.")
    add("백테스트용 월 커버리지 충분", len(all_months), "최소 20개 분기 대응 월", "NOT_READY" if len(all_months) < 20 else "PASS", "현재입지 백테스트 2021Q1~2025Q4에 맞추려면 과거 월별 승하차량이 필요하다.")
    add("후보 gold 분기 커버리지", int(candidate_quarters), "20개 분기 이상", "NOT_READY" if candidate_quarters < 20 else "PASS", "gold가 20개 분기 이상을 포함하면 월 커버리지 관문은 통과하지만, 점수 승격은 별도 백테스트 성능 검증 후에만 가능하다.")

    manifest_month_rows = manifest_audit[
        manifest_audit["source_id"].astype(str).isin(
            ["seoul_bus_stop_passengers_hourly", "seoul_subway_station_passengers_hourly"]
        )
    ]
    add("raw manifest 교통 승하차량 기록 존재", int(len(manifest_month_rows)), "0보다 큼", "PASS" if len(manifest_month_rows) > 0 else "FAIL", "silver 판단은 raw 수집 기록과 연결되어야 한다.")

    return pd.DataFrame(rows)


def write_report(file_audit: pd.DataFrame, manifest_audit: pd.DataFrame, validation: pd.DataFrame) -> None:
    not_ready_count = int((validation["result"] == "NOT_READY").sum())
    fail_count = int((validation["result"] == "FAIL").sum())
    pass_count = int((validation["result"] == "PASS").sum())
    coverage_ready = fail_count == 0 and not_ready_count == 0

    lines = [
        "# 교통 승하차량 월별 커버리지 검증",
        "",
        "작성일: 2026-07-04",
        "",
        "## 1. 목적",
        "",
        "`gold_accessibility_transit_q_area_candidate.csv`를 현재 점수 엔진이나 백테스트에 넣을 수 있는지 월별 커버리지 기준으로 판단한다.",
        "",
        "## 2. 파일별 월 커버리지",
        "",
        "| label | row_count | month_count | months | quarter_count | quarters |",
        "|---|---:|---:|---|---:|---|",
    ]
    for row in file_audit.itertuples(index=False):
        lines.append(f"| {row.label} | {row.row_count:,} | {row.month_count} | {row.months} | {row.quarter_count} | {row.quarters} |")

    manifest_summary = (
        manifest_audit
        .groupby(["source_id", "collection_status", "source_period", "time_unit"], dropna=False)
        .agg(raw_record_count=("raw_path", "size"), total_bytes=("bytes", "sum"))
        .reset_index()
    )
    manifest_summary["source_period"] = manifest_summary["source_period"].fillna("")
    manifest_summary["time_unit"] = manifest_summary["time_unit"].fillna("")

    lines.extend(
        [
            "",
            "## 3. raw manifest 확인",
            "",
            "| source_id | collection_status | source_period | time_unit | raw_record_count | total_bytes |",
            "|---|---|---|---|---:|---:|",
        ]
    )
    for row in manifest_summary.itertuples(index=False):
        lines.append(
            f"| {row.source_id} | {row.collection_status} | {row.source_period} | {row.time_unit} | {row.raw_record_count} | {row.total_bytes} |"
        )
    lines.extend(
        [
            "",
            "상세 raw path는 `datacorpus/_rule_validation/32_transit_monthly_coverage_manifest_audit.csv`에 남긴다.",
        ]
    )

    lines.extend(
        [
            "",
            "## 4. 검증 결과",
            "",
            f"- PASS: {pass_count}",
            f"- NOT_READY: {not_ready_count}",
            f"- FAIL: {fail_count}",
            "",
            "| rule_name | observed | expected | result | reason_ko |",
            "|---|---:|---:|---|---|",
        ]
    )
    for row in validation.itertuples(index=False):
        lines.append(f"| {row.rule_name} | {row.observed} | {row.expected} | {row.result} | {row.reason_ko} |")

    lines.extend(
        [
            "",
            "## 5. 판정",
            "",
            "월 커버리지 기준 PASS." if coverage_ready else "백테스트 투입 보류.",
            "",
            "이유:",
            "",
            "- 버스·지하철 승하차량 silver는 월별 이력으로 감사한다.",
            "- 현재입지 백테스트는 2021Q1~2025Q4 20개 분기를 대상으로 한다.",
            "- 교통 후보 gold가 20개 분기 이상을 포함하면 월 커버리지 관문은 통과한다.",
            "- 다만 승하차량은 실제 방문자나 구매자가 아니라 접근성 프록시이므로, 성능 백테스트와 CRS 검토 전까지 `build_rule_based_location_scores.py`의 접근성 점수에는 직접 넣지 않는다.",
            "",
            "## 6. 다음 조건",
            "",
            "1. 최소 2021Q1~2025Q4에 대응하는 월별 버스·지하철 승하차량을 유지한다.",
            "2. 월 자료를 분기 단위로 집계할 때 부분분기 누락 여부를 표시한다.",
            "3. 100m/250m/500m buffer별 민감도를 백테스트한다.",
            "4. 성능이 개선되고 시간누수가 없을 때만 `gold_accessibility_q_area` 또는 점수 엔진에 승격한다.",
        ]
    )

    RESEARCH_RULE_VALIDATION.mkdir(parents=True, exist_ok=True)
    (RESEARCH_RULE_VALIDATION / "32_transit_monthly_coverage_validation_20260707.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    file_audit = pd.DataFrame([inspect_file(label, path) for label, path in FILES])
    manifest_audit = inspect_manifest()
    validation = build_validation(file_audit, manifest_audit)
    coverage_ready = not validation["result"].isin(["FAIL", "NOT_READY"]).any()

    write_csv(file_audit, RULE_VALIDATION / "32_transit_monthly_coverage_file_audit.csv")
    write_csv(manifest_audit, RULE_VALIDATION / "32_transit_monthly_coverage_manifest_audit.csv")
    write_csv(validation, RULE_VALIDATION / "32_transit_monthly_coverage_validation.csv")

    summary = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "pass_count": int((validation["result"] == "PASS").sum()),
        "not_ready_count": int((validation["result"] == "NOT_READY").sum()),
        "fail_count": int((validation["result"] == "FAIL").sum()),
        "decision": "월커버리지_PASS_백테스트승격전_직접투입보류" if coverage_ready else "백테스트_투입_보류",
        "reason_ko": "월 커버리지는 충분해도 승하차량은 접근성 프록시이므로 성능 백테스트와 CRS 검토 전 점수 직접 투입은 보류한다." if coverage_ready else "교통 승하차량 후보 월 커버리지가 부족하면 2021Q1~2025Q4 백테스트에 직접 투입할 수 없다.",
    }
    (RULE_VALIDATION / "32_transit_monthly_coverage_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_report(file_audit, manifest_audit, validation)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
