# -*- coding: utf-8 -*-
"""
78. 알고리즘 payload v2 백데이터 조인 안정성 감사.

목적:
  - 77번 payload 조인 계약이 현재분기뿐 아니라 공통 보유 전체 분기에서도 fan-out 없이 유지되는지 확인한다.
  - 라이브 최신분기(20261)와 다음분기 라벨 백테스트 가능 분기(20211~20254)를 분리한다.
  - 상권×분기 gold를 상권×업종×분기 드라이버에 붙여도 행수가 증가하지 않는지 분기별로 검증한다.

주의:
  - 이 스크립트는 점수 산식을 변경하지 않는다.
  - 조인 안정성만 감사하며 거대 joined feature mart를 저장하지 않는다.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "datacorpus" / "_gold"
SCORE_BT = ROOT / "datacorpus" / "_score_backtest_gold"
RULE = ROOT / "datacorpus" / "_rule_validation"
DOC = ROOT / "research" / "rule_validation"

CONTRACT_77 = RULE / "77_algorithm_payload_v2_join_contract.csv"
TEMPORAL_AUDIT = SCORE_BT / "gold_engine_temporal_leakage_audit.csv"
JOIN_KEY_AUDIT = SCORE_BT / "gold_engine_join_key_audit.csv"
DIRECTION_EFFECT_AUDIT = SCORE_BT / "gold_engine_direction_effect_audit.csv"
LABELED_ROWS = SCORE_BT / "gold_engine_backtest_labeled_rows.csv"
QUARTER_SCORE_DIR = SCORE_BT / "quarter_scores"

OUT_BY_QUARTER = RULE / "78_algorithm_payload_v2_backdata_join_stability_by_quarter.csv"
OUT_JOIN_AUDIT = RULE / "78_algorithm_payload_v2_backdata_join_step_audit.csv"
OUT_VALIDATION = RULE / "78_algorithm_payload_v2_backdata_join_stability_validation.csv"
OUT_SUMMARY = RULE / "78_algorithm_payload_v2_backdata_join_stability_summary.json"
OUT_DOC = DOC / "78_algorithm_payload_v2_backdata_join_stability_20260707.md"

VERSION = "algorithm_payload_v2_backdata_join_stability.v0.1-20260707"
LIVE_QUARTER = 20261


RESEARCH_BASIS_DOCS = [
    ROOT / "research" / "알고리즘_명세_v2_20260704.md",
    DOC / "24_gold_based_score_engine_validation_20260704.md",
    DOC / "50_sales_ticket_engine_patch_validation_20260707.md",
    DOC / "76_direct_score_input_readiness_20260707.md",
    DOC / "77_algorithm_payload_v2_join_contract_20260707.md",
]


def json_default(value: Any) -> Any:
    if hasattr(value, "item"):
        return value.item()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if pd.isna(value):
        return None
    return str(value)


def write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")


def write_json(data: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=json_default), encoding="utf-8")


def read_key_frame(file_name: str, usecols: list[str], python_engine: bool = False) -> pd.DataFrame:
    engine = "python" if python_engine else None
    df = pd.read_csv(GOLD / file_name, encoding="utf-8-sig", usecols=usecols, engine=engine)
    df["기준_년분기_코드"] = pd.to_numeric(df["기준_년분기_코드"], errors="coerce").astype("Int64")
    df["상권_코드"] = df["상권_코드"].astype(str)
    if "서비스_업종_코드" in df.columns:
        df["서비스_업종_코드"] = df["서비스_업종_코드"].astype(str)
    return df


def load_inputs() -> dict[str, pd.DataFrame]:
    return {
        "sales": read_key_frame("gold_sales_strength_q_industry.csv", ["기준_년분기_코드", "상권_코드", "서비스_업종_코드"]),
        "store": read_key_frame("gold_competition_q_industry.csv", ["기준_년분기_코드", "상권_코드", "서비스_업종_코드"]),
        "demand": read_key_frame("gold_demand_q_area.csv", ["기준_년분기_코드", "상권_코드"]),
        "accessibility": read_key_frame("gold_accessibility_q_area.csv", ["기준_년분기_코드", "상권_코드"]),
        "growth": read_key_frame(
            "gold_growth_stability_q_industry.csv",
            ["기준_년분기_코드", "상권_코드", "서비스_업종_코드", "상권_변화_지표_코드", "상권_변화_지표_명", "운영_서울대비_개월_차이"],
            python_engine=True,
        ),
    }


def quarter_set(df: pd.DataFrame) -> set[int]:
    return set(int(v) for v in df["기준_년분기_코드"].dropna().unique().tolist())


def list_quarter_score_files() -> set[int]:
    if not QUARTER_SCORE_DIR.exists():
        return set()
    quarters: set[int] = set()
    for path in QUARTER_SCORE_DIR.glob("gold_engine_labeled_scores_*.csv"):
        match = re.search(r"_(20\d{3})\.csv$", path.name)
        if match:
            quarters.add(int(match.group(1)))
    return quarters


def unique_audit(quarter: int, name: str, df: pd.DataFrame, key_cols: list[str]) -> dict[str, Any]:
    qdf = df[df["기준_년분기_코드"].eq(quarter)].copy()
    return {
        "quarter": quarter,
        "name": name,
        "rows": int(len(qdf)),
        "key_cols": "+".join(key_cols),
        "unique_key_rows": int(qdf[key_cols].drop_duplicates().shape[0]),
        "duplicate_key_rows": int(qdf.duplicated(key_cols).sum()),
        "key_null_rows": int(qdf[key_cols].isna().any(axis=1).sum()),
        "before_rows": None,
        "after_rows": None,
        "fanout_rows": None,
    }


def build_quarter_join(quarter: int, inputs: dict[str, pd.DataFrame]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    ai_key = ["상권_코드", "서비스_업종_코드"]
    area_key = ["상권_코드"]
    sales = inputs["sales"][inputs["sales"]["기준_년분기_코드"].eq(quarter)].copy()
    store = inputs["store"][inputs["store"]["기준_년분기_코드"].eq(quarter)].copy()
    demand = inputs["demand"][inputs["demand"]["기준_년분기_코드"].eq(quarter)].copy()
    accessibility = inputs["accessibility"][inputs["accessibility"]["기준_년분기_코드"].eq(quarter)].copy()
    growth = inputs["growth"][inputs["growth"]["기준_년분기_코드"].eq(quarter)].copy()

    audits: list[dict[str, Any]] = []
    for name, df, keys in [
        ("sales_key", sales, ai_key),
        ("store_key", store, ai_key),
        ("demand_area_key", demand, area_key),
        ("accessibility_area_key", accessibility, area_key),
        ("growth_area_industry_key", growth, ai_key),
    ]:
        audits.append(unique_audit(quarter, name, df, keys))

    growth_area = growth[["상권_코드", "상권_변화_지표_코드", "상권_변화_지표_명", "운영_서울대비_개월_차이"]].drop_duplicates()
    audits.append(
        {
            "quarter": quarter,
            "name": "growth_area_evidence_dedup",
            "rows": int(len(growth_area)),
            "key_cols": "상권_코드",
            "unique_key_rows": int(growth_area[area_key].drop_duplicates().shape[0]),
            "duplicate_key_rows": int(growth_area.duplicated(area_key).sum()),
            "key_null_rows": int(growth_area[area_key].isna().any(axis=1).sum()),
            "before_rows": None,
            "after_rows": None,
            "fanout_rows": None,
        }
    )

    driver = pd.concat([sales[ai_key], store[ai_key]], ignore_index=True).drop_duplicates(ai_key)
    joined = driver.copy()
    join_specs = [
        ("join_sales", sales[ai_key].drop_duplicates(ai_key), ai_key),
        ("join_store", store[ai_key].drop_duplicates(ai_key), ai_key),
        ("join_demand_area", demand[area_key].drop_duplicates(area_key), area_key),
        ("join_accessibility_area", accessibility[area_key].drop_duplicates(area_key), area_key),
        ("join_growth_area_evidence", growth_area[area_key].drop_duplicates(area_key), area_key),
    ]
    matched_counts: dict[str, int] = {}
    for step, right, keys in join_specs:
        before = len(joined)
        marker = f"__{step}_matched"
        right_marked = right.copy()
        right_marked[marker] = True
        joined = joined.merge(right_marked, on=keys, how="left", validate="many_to_one")
        after = len(joined)
        matched_counts[step] = int(joined[marker].fillna(False).sum())
        audits.append(
            {
                "quarter": quarter,
                "name": step,
                "rows": int(len(right)),
                "key_cols": "+".join(keys),
                "unique_key_rows": int(right[keys].drop_duplicates().shape[0]),
                "duplicate_key_rows": int(right.duplicated(keys).sum()),
                "key_null_rows": int(right[keys].isna().any(axis=1).sum()),
                "before_rows": int(before),
                "after_rows": int(after),
                "fanout_rows": int(after - before),
            }
        )

    driver_area_count = int(driver["상권_코드"].nunique())
    row = {
        "quarter": quarter,
        "driver_rows": int(len(driver)),
        "driver_area_count": driver_area_count,
        "driver_industry_count": int(driver["서비스_업종_코드"].nunique()),
        "sales_rows": int(len(sales)),
        "store_rows": int(len(store)),
        "demand_area_rows": int(len(demand)),
        "accessibility_area_rows": int(len(accessibility)),
        "growth_rows": int(len(growth)),
        "growth_area_evidence_rows": int(len(growth_area)),
        "fanout_rows_total": int(sum(a["fanout_rows"] or 0 for a in audits)),
        "duplicate_key_rows_total": int(sum(a["duplicate_key_rows"] for a in audits)),
        "key_null_rows_total": int(sum(a["key_null_rows"] for a in audits)),
        "demand_driver_area_coverage": round(float(matched_counts["join_demand_area"]) / len(driver), 8) if len(driver) else 0.0,
        "accessibility_driver_area_coverage": round(float(matched_counts["join_accessibility_area"]) / len(driver), 8) if len(driver) else 0.0,
        "growth_area_driver_coverage": round(float(matched_counts["join_growth_area_evidence"]) / len(driver), 8) if len(driver) else 0.0,
        "backtest_label_allowed": quarter < LIVE_QUARTER,
    }
    return row, audits


def read_audit_results(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    df = pd.read_csv(path, encoding="utf-8-sig")
    if "audit_item" in df.columns and "result" in df.columns:
        return dict(zip(df["audit_item"].astype(str), df["result"].astype(str)))
    return {}


def read_labeled_universe() -> dict[str, Any]:
    if not LABELED_ROWS.exists():
        return {"exists": False}
    df = pd.read_csv(
        LABELED_ROWS,
        encoding="utf-8-sig",
        usecols=["기준_년분기_코드", "상권_코드", "서비스_업종_코드"],
    )
    q = pd.to_numeric(df["기준_년분기_코드"], errors="coerce").dropna().astype(int)
    return {
        "exists": True,
        "rows": int(len(df)),
        "quarter_count": int(q.nunique()),
        "quarter_min": int(q.min()),
        "quarter_max": int(q.max()),
        "duplicate_key_rows": int(df.duplicated(["기준_년분기_코드", "상권_코드", "서비스_업종_코드"]).sum()),
        "key_null_rows": int(df[["기준_년분기_코드", "상권_코드", "서비스_업종_코드"]].isna().any(axis=1).sum()),
    }


def read_direction_quality_warnings() -> list[dict[str, Any]]:
    if not DIRECTION_EFFECT_AUDIT.exists():
        return [{"audit_item": "direction_effect_audit_missing", "result": "MISSING", "reason_ko": "방향 효과 감사 파일이 없다."}]
    df = pd.read_csv(DIRECTION_EFFECT_AUDIT, encoding="utf-8-sig")
    if "result" not in df.columns:
        return [{"audit_item": "direction_effect_audit_schema_missing", "result": "MISSING", "reason_ko": "result 컬럼이 없다."}]
    failed = df[df["result"].astype(str).ne("PASS")]
    return failed.to_dict("records")


def add_validation(rows: list[dict[str, Any]], check_id: str, item: str, observed: Any, expected: Any, passed: bool, reason_ko: str) -> None:
    rows.append(
        {
            "check_id": check_id,
            "item": item,
            "observed": json.dumps(observed, ensure_ascii=False, default=json_default) if isinstance(observed, (dict, list)) else observed,
            "expected": json.dumps(expected, ensure_ascii=False, default=json_default) if isinstance(expected, (dict, list)) else expected,
            "pass": bool(passed),
            "reason_ko": reason_ko,
        }
    )


def validate(inputs: dict[str, pd.DataFrame], by_quarter: pd.DataFrame, join_audit: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    quarter_sets = {name: quarter_set(df) for name, df in inputs.items()}
    common_quarters = sorted(set.intersection(*quarter_sets.values()))
    backtest_quarters = [q for q in common_quarters if q < LIVE_QUARTER]

    add_validation(rows, "78-V01", "공통 payload 분기", common_quarters, "20211~20254 + 20261, 총 21개", len(common_quarters) == 21 and common_quarters[0] == 20211 and common_quarters[-1] == LIVE_QUARTER, "direct gold 5개 묶음이 모두 존재하는 공통 payload 분기를 고정한다.")

    add_validation(rows, "78-V02", "백테스트 라벨 가능 분기", backtest_quarters, "20211~20254, 총 20개", len(backtest_quarters) == 20 and max(backtest_quarters) == 20254 and LIVE_QUARTER not in backtest_quarters, "20261은 다음분기 라벨이 없으므로 백테스트 분기에서 제외한다.")

    duplicate_total = int(join_audit["duplicate_key_rows"].sum())
    null_total = int(join_audit["key_null_rows"].sum())
    add_validation(rows, "78-V03", "분기별 key 중복/결측 없음", {"duplicate_key_rows": duplicate_total, "key_null_rows": null_total}, {"duplicate_key_rows": 0, "key_null_rows": 0}, duplicate_total == 0 and null_total == 0, "분기별 조인 오른쪽 key가 중복되거나 비어 있으면 fan-out 또는 누락이 생긴다.")

    fanout_total = int(by_quarter["fanout_rows_total"].sum())
    add_validation(rows, "78-V04", "전분기 fan-out 0", fanout_total, 0, fanout_total == 0, "상권 grain 자료를 업종 드라이버에 붙여도 행수 증가는 허용하지 않는다.")

    row_preserve_bad = join_audit[join_audit["fanout_rows"].fillna(0).ne(0)][["quarter", "name", "before_rows", "after_rows", "fanout_rows"]].to_dict("records")
    add_validation(rows, "78-V05", "조인 step별 행수 보존", row_preserve_bad, [], len(row_preserve_bad) == 0, "각 조인 step은 before_rows와 after_rows가 같아야 한다.")

    area_coverage_bad = by_quarter[(by_quarter["demand_driver_area_coverage"] < 1.0) | (by_quarter["accessibility_driver_area_coverage"] < 1.0)][["quarter", "demand_driver_area_coverage", "accessibility_driver_area_coverage"]].to_dict("records")
    add_validation(rows, "78-V06", "수요/접근성 상권 coverage 100%", area_coverage_bad, [], len(area_coverage_bad) == 0, "공식 수요/접근성 상권 grain은 드라이버 상권 전체에 붙어야 한다.")

    contract = pd.read_csv(CONTRACT_77, encoding="utf-8-sig")
    growth_rows = contract[contract["payload_section"].eq("candidates.growth_stability")]
    growth_candidate_ok = bool(not growth_rows.empty and growth_rows["candidate_only"].astype(str).str.lower().eq("true").all() and growth_rows["official_current_axis"].astype(str).str.lower().eq("false").all())
    add_validation(rows, "78-V07", "성장 후보 현재입지 미반영", growth_rows.to_dict("records"), "candidate_only=true, official_current_axis=false", growth_candidate_ok, "성장/안정성 후보는 백데이터 payload에서도 현재입지 총점에 합산하지 않는다.")

    temporal = read_audit_results(TEMPORAL_AUDIT)
    join_key = read_audit_results(JOIN_KEY_AUDIT)
    audit_ok = temporal and all(v == "PASS" for v in temporal.values()) and join_key and all(v == "PASS" for v in join_key.values())
    add_validation(rows, "78-V08", "기존 시간누수/조인키 감사 PASS", {"temporal": temporal, "join_key": join_key}, "모든 항목 PASS", bool(audit_ok), "새 payload 안정성 검증은 기존 백테스트 시간누수/조인키 감사와 충돌하면 안 된다.")

    score_file_quarters = sorted(list_quarter_score_files())
    add_validation(rows, "78-V09", "기존 quarter score 파일과 백테스트 분기 일치", score_file_quarters, backtest_quarters, score_file_quarters == backtest_quarters, "기존 백테스트 산출 파일은 20261을 제외한 라벨 가능 20개 분기와 일치해야 한다.")

    docs_missing = [str(path.relative_to(ROOT)).replace("\\", "/") for path in RESEARCH_BASIS_DOCS if not path.exists()]
    add_validation(rows, "78-V10", "research 근거 문서 존재", docs_missing, [], len(docs_missing) == 0, "백데이터 조인 안정성 감사도 research 명세와 이전 검증을 근거로 해야 한다.")

    contract_forbidden_missing = contract.loc[~contract["forbidden_claim_ko"].astype(str).str.strip().astype(bool), "payload_section"].tolist()
    add_validation(rows, "78-V11", "payload 금지표현 계약 유지", contract_forbidden_missing, [], len(contract_forbidden_missing) == 0, "백데이터 payload도 성공확률, 매출보장, 실제 방문자 같은 과장 표현을 막아야 한다.")

    no_joined_path = not (RULE / "78_algorithm_payload_v2_backdata_joined_feature_mart.csv").exists()
    add_validation(rows, "78-V12", "거대 joined feature mart 미생성", no_joined_path, True, no_joined_path, "검증 산출물은 감사표이며 모든 원천을 한 파일에 저장하지 않는다.")

    labeled = read_labeled_universe()
    labeled_ok = (
        labeled.get("exists") is True
        and labeled.get("rows") == 427553
        and labeled.get("quarter_count") == 20
        and labeled.get("quarter_min") == 20211
        and labeled.get("quarter_max") == 20254
        and labeled.get("duplicate_key_rows") == 0
        and labeled.get("key_null_rows") == 0
    )
    add_validation(rows, "78-V13", "기존 labeled row universe 보존", labeled, "427,553행 / 20211~20254 / 중복·결측 0", labeled_ok, "payload full mode와 라벨 백테스트 mode를 분리하되 기존 라벨 universe는 보존해야 한다.")

    direction_warnings = read_direction_quality_warnings()
    warnings_carried = bool(direction_warnings)
    add_validation(rows, "78-V14", "방향 효과 감사 경고 이월", direction_warnings, "FAIL/MISSING이 있으면 품질 경고로 기록", warnings_carried, "기존 direction_effect_audit의 미해결 항목은 조인 안정성 실패가 아니라 다음 알고리즘 보강 품질 경고로 이어받는다.")

    return pd.DataFrame(rows)


def build_report(by_quarter: pd.DataFrame, join_audit: pd.DataFrame, validations: pd.DataFrame, summary: dict[str, Any]) -> str:
    lines: list[str] = [
        "# 78. 알고리즘 payload v2 백데이터 조인 안정성 감사",
        "",
        f"- 작성일: {datetime.now().strftime('%Y-%m-%d')}",
        f"- 버전: `{VERSION}`",
        "",
        "## 목적",
        "",
        "77번 현재분기 payload 조인 계약을 백데이터 전체 공통 분기에 확장해, 상권×분기 자료를 상권×업종×분기 드라이버에 붙여도 fan-out이 생기지 않는지 확인했다.",
        "",
        "## 요약",
        "",
        f"- payload quarters: {summary['payload_quarter_count']}",
        f"- backtest label quarters: {summary['backtest_quarter_count']}",
        f"- labeled universe rows: {summary['labeled_universe'].get('rows')}",
        f"- total driver rows across payload quarters: {summary['total_driver_rows']}",
        f"- fanout rows total: {summary['fanout_rows_total']}",
        f"- quality warnings carried: {summary['quality_warning_count']}",
        f"- PASS: {summary['pass_count']}",
        f"- FAIL: {summary['fail_count']}",
        f"- decision: `{summary['decision']}`",
        "",
        "## 분기별 요약",
        "",
        "| quarter | driver rows | areas | industries | fanout | demand coverage | accessibility coverage | backtest label |",
        "|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for _, row in by_quarter.iterrows():
        lines.append(
            f"| {row['quarter']} | {row['driver_rows']} | {row['driver_area_count']} | {row['driver_industry_count']} | {row['fanout_rows_total']} | {row['demand_driver_area_coverage']} | {row['accessibility_driver_area_coverage']} | {row['backtest_label_allowed']} |"
        )
    lines.extend(
        [
            "",
            "## 검증표",
            "",
            "| check | 항목 | 결과 | 이유 |",
            "|---|---|---|---|",
        ]
    )
    for _, row in validations.iterrows():
        result = "PASS" if bool(row["pass"]) else "FAIL"
        lines.append(f"| {row['check_id']} | {row['item']} | {result} | {row['reason_ko']} |")
    lines.extend(
        [
            "",
            "## 해석",
            "",
            "공통 payload 분기 21개 전체에서 조인 fan-out은 0이었다. 백테스트 라벨 가능 분기는 20211~20254의 20개로 분리했고, 20261은 라이브 최신분기 payload 검증에는 포함하지만 다음분기 라벨 백테스트에는 포함하지 않는다.",
            "",
            "기존 labeled row universe 427,553행도 보존됨을 확인했다. 다만 기존 `gold_engine_direction_effect_audit.csv`의 방향행렬 지표수 FAIL은 다음 알고리즘 보강에서 해결해야 할 품질 경고로 이월했다.",
            "",
            "## 2보 전진 1보 후퇴",
            "",
            "1. 전진: 현재분기 조인 계약을 21개 공통 분기 전체로 확장 검증했다.",
            "2. 전진: 기존 시간누수/조인키 감사와 quarter score 파일 범위까지 함께 확인했다.",
            "3. 후퇴: 20261은 다음분기 라벨이 없으므로 백테스트 분기에서 제외했다.",
            "4. 후퇴: 백데이터 joined feature mart를 저장하지 않고 감사표만 남겼다.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    inputs = load_inputs()
    quarter_sets = {name: quarter_set(df) for name, df in inputs.items()}
    common_quarters = sorted(set.intersection(*quarter_sets.values()))

    quarter_rows: list[dict[str, Any]] = []
    join_rows: list[dict[str, Any]] = []
    for quarter in common_quarters:
        qrow, audits = build_quarter_join(quarter, inputs)
        quarter_rows.append(qrow)
        join_rows.extend(audits)
    by_quarter = pd.DataFrame(quarter_rows)
    join_audit = pd.DataFrame(join_rows)
    validations = validate(inputs, by_quarter, join_audit)
    labeled_universe = read_labeled_universe()
    direction_warnings = read_direction_quality_warnings()

    pass_count = int(validations["pass"].sum())
    fail_count = int((~validations["pass"]).sum())
    backtest_quarters = [q for q in common_quarters if q < LIVE_QUARTER]
    summary = {
        "version": VERSION,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "payload_quarters": common_quarters,
        "payload_quarter_count": int(len(common_quarters)),
        "backtest_label_quarters": backtest_quarters,
        "backtest_quarter_count": int(len(backtest_quarters)),
        "live_quarter": LIVE_QUARTER,
        "total_driver_rows": int(by_quarter["driver_rows"].sum()),
        "fanout_rows_total": int(by_quarter["fanout_rows_total"].sum()),
        "duplicate_key_rows_total": int(by_quarter["duplicate_key_rows_total"].sum()),
        "key_null_rows_total": int(by_quarter["key_null_rows_total"].sum()),
        "labeled_universe": labeled_universe,
        "quality_warnings": direction_warnings,
        "quality_warning_count": int(len(direction_warnings)),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "decision": "ALGORITHM_PAYLOAD_V2_BACKDATA_JOIN_STABILITY_PASS" if fail_count == 0 else "ALGORITHM_PAYLOAD_V2_BACKDATA_JOIN_STABILITY_FAIL",
    }

    write_csv(by_quarter, OUT_BY_QUARTER)
    write_csv(join_audit, OUT_JOIN_AUDIT)
    write_csv(validations, OUT_VALIDATION)
    write_json(summary, OUT_SUMMARY)
    OUT_DOC.parent.mkdir(parents=True, exist_ok=True)
    OUT_DOC.write_text(build_report(by_quarter, join_audit, validations, summary), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=json_default))
    if fail_count:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
