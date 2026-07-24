from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

import ingest_seoul_transport_passenger_months as live_ingest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
SILVER = ROOT / "datacorpus" / "_silver"
RULE = ROOT / "datacorpus" / "_rule_validation"
DOC = ROOT / "research" / "rule_validation"

OUT_PLAN = RULE / "101_transit_live_quarter_ingest_contract_plan.csv"
OUT_VALIDATION = RULE / "101_transit_live_quarter_ingest_contract_validation.csv"
OUT_SUMMARY = RULE / "101_transit_live_quarter_ingest_contract_summary.json"
OUT_DOC = DOC / "101_transit_live_quarter_ingest_contract_20260707.md"

VERSION = "transit_live_quarter_ingest_contract.v0.1-20260707"
REQUIRED_MONTHS = ["202601", "202602", "202603"]
BUS_MANIFEST = SILVER / "silver_bus_passenger_route_stop_month_hour_manifest.csv"
SUBWAY_MANIFEST = SILVER / "silver_subway_passenger_station_month_hour_manifest.csv"


def read_months(path: Path) -> list[str]:
    if not path.exists():
        return []
    df = pd.read_csv(path, usecols=["기준_월"], encoding="utf-8-sig", dtype=str)
    return sorted(df["기준_월"].dropna().astype(str).unique().tolist())


def add_validation(rows: list[dict[str, Any]], check_id: str, name: str, observed: Any, expected: Any, passed: bool, reason: str) -> None:
    rows.append(
        {
            "validation_id": check_id,
            "validation_name": name,
            "observed": observed,
            "expected": expected,
            "result": "PASS" if passed else "FAIL",
            "reason_ko": reason,
        }
    )


def main() -> None:
    RULE.mkdir(parents=True, exist_ok=True)
    DOC.mkdir(parents=True, exist_ok=True)

    plan = pd.DataFrame(live_ingest.build_collection_plan(REQUIRED_MONTHS, "both"))
    bus_months = read_months(BUS_MANIFEST)
    subway_months = read_months(SUBWAY_MANIFEST)
    bus_missing = sorted(set(REQUIRED_MONTHS) - set(bus_months))
    subway_missing = sorted(set(REQUIRED_MONTHS) - set(subway_months))
    bus_2026 = [month for month in bus_months if month.startswith("2026")]
    subway_2026 = [month for month in subway_months if month.startswith("2026")]

    ingest_runner = SCRIPTS / "ingest_seoul_transport_passenger_months.py"
    silver_script = SCRIPTS / "preprocess_rule_engine_transit_passenger_history_silver.py"
    candidate_script = SCRIPTS / "preprocess_rule_engine_transit_accessibility_candidates.py"
    promotion_script = SCRIPTS / "validate_transit_accessibility_official_promotion_readiness.py"
    gate_script = SCRIPTS / "validate_candidate_official_promotion_gate.py"

    silver_text = silver_script.read_text(encoding="utf-8-sig")
    candidate_text = candidate_script.read_text(encoding="utf-8-sig")
    ingest_text = ingest_runner.read_text(encoding="utf-8-sig")

    validations: list[dict[str, Any]] = []
    add_validation(
        validations,
        "101-V01",
        "수집 대상 월은 최신 공식분기 2026Q1의 3개월",
        ",".join(REQUIRED_MONTHS),
        "202601,202602,202603",
        REQUIRED_MONTHS == live_ingest.DEFAULT_LIVE_QUARTER_MONTHS,
        "81번에서 막힌 최신 공식분기 gap은 2026Q1 3개월 raw 부재이므로 다른 월을 대체하면 안 된다.",
    )
    add_validation(
        validations,
        "101-V02",
        "수집 요청은 버스 3건과 지하철 3건",
        plan.groupby("mode").size().to_dict(),
        {"bus": 3, "subway": 3},
        plan.groupby("mode").size().to_dict() == {"bus": 3, "subway": 3},
        "버스와 지하철 중 하나만 있으면 250m 접근성 후보의 같은 분기 혼합 피처를 만들 수 없다.",
    )
    add_validation(
        validations,
        "101-V03",
        "서울 API 서비스명 고정",
        sorted(plan["service"].unique().tolist()),
        "CardBusTimeNew + CardSubwayTime",
        set(plan["service"]) == {"CardBusTimeNew", "CardSubwayTime"},
        "기존 문서에서 버스는 CardBusTimeNew, 지하철은 CardSubwayTime이 정상 서비스명으로 검증됐다.",
    )
    add_validation(
        validations,
        "101-V04",
        "현재 silver에는 2026Q1 승하차량 월이 없음",
        {"bus_2026": bus_2026, "subway_2026": subway_2026, "bus_missing": bus_missing, "subway_missing": subway_missing},
        "202601~202603 missing, 202605만 보유 가능",
        set(REQUIRED_MONTHS).isdisjoint(bus_months) and set(REQUIRED_MONTHS).isdisjoint(subway_months),
        "현재 상태에서 202605를 2026Q1 대체로 쓰면 시간 기준이 틀어지므로 공식 승격 금지가 맞다.",
    )
    add_validation(
        validations,
        "101-V05",
        "runner는 dry-run을 지원해 네트워크 전 계획 검증 가능",
        "--dry-run" in ingest_text and "DRY_RUN_NO_NETWORK" in ingest_text,
        "dry-run supported",
        "--dry-run" in ingest_text and "DRY_RUN_NO_NETWORK" in ingest_text,
        "수집 전에도 월/서비스/금지문구 계약을 검증해야 한다.",
    )
    add_validation(
        validations,
        "101-V06",
        "runner는 기존 월별 수집 함수를 재사용",
        "collect_seoul_month_api" in ingest_text,
        "collect_seoul_month_api reused",
        "collect_seoul_month_api" in ingest_text,
        "기존 manifest/redaction/failure logging 계약을 깨지 않고 필요한 월만 추가 수집해야 한다.",
    )
    add_validation(
        validations,
        "101-V07",
        "silver 전처리는 raw 월 폴더 자동 탐색",
        "discover_month_paths" in silver_text and "month_by_name" in silver_text,
        "discover_month_paths + latest duplicate by month",
        "discover_month_paths" in silver_text and "month_by_name" in silver_text,
        "수집이 끝나면 하드코딩 없이 새 월 폴더를 자동 포함해야 한다.",
    )
    add_validation(
        validations,
        "101-V08",
        "접근성 후보 전처리는 버스·지하철 공통 월만 사용",
        "source_months = sorted(set(bus_parts) & set(subway_parts))" in candidate_text,
        "intersection of bus/subway months",
        "source_months = sorted(set(bus_parts) & set(subway_parts))" in candidate_text,
        "한쪽 교통수단만 있는 월을 억지로 넣으면 혼합 접근성축이 깨진다.",
    )
    add_validation(
        validations,
        "101-V09",
        "성공 후 재검증 체인 명확",
        [p.name for p in [silver_script, candidate_script, promotion_script, gate_script] if p.exists()],
        "silver -> candidate -> 81 -> 100",
        all(p.exists() for p in [silver_script, candidate_script, promotion_script, gate_script]),
        "수집만 하고 공식 산식을 바꾸지 말고, 전처리와 승격 게이트를 다시 통과해야 한다.",
    )
    add_validation(
        validations,
        "101-V10",
        "금지표현 계약이 수집 계획에 포함",
        " ".join(plan["quality_note"].astype(str).tolist()),
        "실제 상권 방문자/구매자 표현 금지",
        plan["quality_note"].astype(str).str.contains("실제 상권 방문자").all()
        and plan["quality_note"].astype(str).str.contains("구매자").all(),
        "승하차량은 접근성 프록시이지 실제 방문자나 구매자가 아니다.",
    )

    validation_df = pd.DataFrame(validations)
    pass_count = int((validation_df["result"] == "PASS").sum())
    fail_count = int((validation_df["result"] == "FAIL").sum())
    decision = "TRANSIT_LIVE_QUARTER_INGEST_CONTRACT_PASS_READY_TO_COLLECT" if fail_count == 0 else "TRANSIT_LIVE_QUARTER_INGEST_CONTRACT_FAIL"

    plan.to_csv(OUT_PLAN, index=False, encoding="utf-8-sig")
    validation_df.to_csv(OUT_VALIDATION, index=False, encoding="utf-8-sig")
    summary = {
        "validation_version": VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "decision": decision,
        "pass_count": pass_count,
        "fail_count": fail_count,
        "required_months": REQUIRED_MONTHS,
        "request_count": int(len(plan)),
        "current_bus_2026_months": bus_2026,
        "current_subway_2026_months": subway_2026,
        "current_bus_missing_required_months": bus_missing,
        "current_subway_missing_required_months": subway_missing,
        "collection_command": "python scripts/ingest_seoul_transport_passenger_months.py --months 202601,202602,202603 --mode both --run-date 20260707",
        "dry_run_command": "python scripts/ingest_seoul_transport_passenger_months.py --months 202601,202602,202603 --mode both --run-date 20260707 --dry-run",
        "post_collection_commands": [
            "python scripts/preprocess_rule_engine_transit_passenger_history_silver.py",
            "python scripts/preprocess_rule_engine_transit_accessibility_candidates.py",
            "python scripts/validate_transit_accessibility_official_promotion_readiness.py",
            "python scripts/validate_candidate_official_promotion_gate.py",
        ],
        "outputs": {
            "plan": str(OUT_PLAN.relative_to(ROOT)),
            "validation": str(OUT_VALIDATION.relative_to(ROOT)),
            "summary": str(OUT_SUMMARY.relative_to(ROOT)),
            "doc": str(OUT_DOC.relative_to(ROOT)),
        },
        "reason_ko": "교통 접근성 후보 공식 승격의 다음 병목은 2026Q1 버스·지하철 승하차 raw 부재이며, 수집 전 계약은 검증됐다.",
    }
    OUT_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8-sig")

    lines = [
        "# 101. 교통 접근성 최신분기 승하차 raw 수집 계약",
        "",
        "## 목적",
        "",
        "100번에서 공식 승격에 가장 가까운 후보는 교통 접근성 250m 승하차량 후보로 판정됐다. 그러나 81번 기준 최신 공식분기 `20261`의 `202601~202603` raw/피처가 없어 승격이 보류됐다. 101번은 수집을 실행하기 전에 어떤 월과 서비스를 받아야 하는지, 그리고 수집 후 어떤 전처리·검증을 다시 해야 하는지 고정한다.",
        "",
        "## 결과",
        "",
        f"- validation version: `{VERSION}`",
        f"- decision: `{decision}`",
        f"- PASS: `{pass_count}`",
        f"- FAIL: `{fail_count}`",
        f"- required months: `{','.join(REQUIRED_MONTHS)}`",
        f"- request count: `{len(plan)}`",
        f"- current bus 2026 months: `{','.join(bus_2026)}`",
        f"- current subway 2026 months: `{','.join(subway_2026)}`",
        "",
        "## 수집 계획",
        "",
        "| mode | service | month | relative_dir | forbidden note |",
        "| --- | --- | --- | --- | --- |",
    ]
    for rec in plan.to_dict("records"):
        lines.append(f"| {rec['mode']} | {rec['service']} | {rec['month']} | {rec['relative_dir']} | {rec['quality_note']} |")
    lines.extend([
        "",
        "## 검증",
        "",
        "| id | result | observed | reason |",
        "| --- | --- | --- | --- |",
    ])
    for rec in validation_df.to_dict("records"):
        lines.append(f"| {rec['validation_id']} | {rec['result']} | {rec['observed']} | {rec['reason_ko']} |")
    lines.extend([
        "",
        "## 실행 순서",
        "",
        "1. `python scripts/ingest_seoul_transport_passenger_months.py --months 202601,202602,202603 --mode both --run-date 20260707 --dry-run`",
        "2. 네트워크 수집 승인 후 `python scripts/ingest_seoul_transport_passenger_months.py --months 202601,202602,202603 --mode both --run-date 20260707`",
        "3. `python scripts/preprocess_rule_engine_transit_passenger_history_silver.py`",
        "4. `python scripts/preprocess_rule_engine_transit_accessibility_candidates.py`",
        "5. `python scripts/validate_transit_accessibility_official_promotion_readiness.py`",
        "6. `python scripts/validate_candidate_official_promotion_gate.py`",
        "",
        "## 주의",
        "",
        "- `202605`는 2026Q2 월자료라서 2026Q1 공식 점수 대체값으로 쓰지 않는다.",
        "- 승하차량은 실제 상권 방문자, 실제 구매자, 실제 도보시간, 창업 성공확률로 표현하지 않는다.",
        "- 수집 성공만으로 공식 산식을 바꾸지 않고, 81번과 100번을 다시 통과한 뒤에만 공식 접근성축 패치를 검토한다.",
    ])
    OUT_DOC.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
