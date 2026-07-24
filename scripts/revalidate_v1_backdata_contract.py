from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RULE_DIR = ROOT / "datacorpus" / "_rule_validation"
BACKTEST_DIR = ROOT / "datacorpus" / "_score_backtest_gold"
DOC_DIR = ROOT / "research" / "rule_validation"

SUMMARY_84 = RULE_DIR / "84_v1_preprocessing_payload_contract_summary.json"
SUMMARY_86 = RULE_DIR / "86_v1_engine_contract_compliance_summary.json"
SUMMARY_87 = RULE_DIR / "87_v1_runtime_json_contract_summary.json"
BACKTEST_SUMMARY = BACKTEST_DIR / "gold_engine_backtest_summary.json"
BACKTEST_ROWS = BACKTEST_DIR / "gold_engine_backtest_labeled_rows.csv"
RULE_VALIDATIONS = BACKTEST_DIR / "gold_engine_backtest_rule_validations.csv"
SAMPLE_DIR_87 = RULE_DIR / "87_runtime_json_samples"

OUT_VALIDATION = RULE_DIR / "88_v1_backdata_contract_revalidation.csv"
OUT_AUDIT = RULE_DIR / "88_v1_backdata_contract_revalidation_audit.csv"
OUT_SUMMARY = RULE_DIR / "88_v1_backdata_contract_revalidation_summary.json"
OUT_DOC = DOC_DIR / "88_v1_backdata_contract_revalidation_20260707.md"

VERSION = "v1_backdata_contract_revalidation.v0.1-20260707"
OFFICIAL_AXES = ["sales", "competition", "demand", "accessibility"]
OFFICIAL_AXIS_COLS = [f"axis__{axis}" for axis in OFFICIAL_AXES]
KEY_COLS = ["기준_년분기_코드", "상권_코드", "서비스_업종_코드"]
NAME_COLS = ["상권_코드_명", "서비스_업종_코드_명"]
SEPARATE_SCORE_COLS = [
    "growth_potential_score",
    "cost_risk_score",
    "growth_rebound_candidate_score",
]
FORBIDDEN_TERMS = [
    "창업 성공확률",
    "개별 매장 매출 보장",
    "성장 보장",
    "성장률 예측",
    "월세/권리금 반영 수익성",
    "실제 방문확률",
    "데이터 완전 확보",
]


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def read_csv(path: Path, **kwargs: Any) -> pd.DataFrame:
    return pd.read_csv(path, encoding="utf-8-sig", low_memory=False, **kwargs)


def add_validation(
    rows: list[dict[str, Any]],
    validation_id: str,
    name: str,
    observed: Any,
    expected: Any,
    ok: bool,
    reason_ko: str,
) -> None:
    rows.append(
        {
            "validation_id": validation_id,
            "validation_name": name,
            "observed": observed,
            "expected": expected,
            "result": "PASS" if ok else "FAIL",
            "reason_ko": reason_ko,
        }
    )


def md_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join(["---"] * len(columns)) + " |",
    ]
    for row in rows:
        values = [str(row.get(col, "")).replace("\n", " ") for col in columns]
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def pass_fail_counts(rows: list[dict[str, Any]]) -> tuple[int, int]:
    pass_count = sum(1 for row in rows if row["result"] == "PASS")
    fail_count = sum(1 for row in rows if row["result"] == "FAIL")
    return pass_count, fail_count


def all_result_pass(path: Path) -> tuple[bool, int, int]:
    df = read_csv(path)
    if "result" not in df.columns:
        return False, len(df), len(df)
    fail_count = int(df["result"].astype(str).ne("PASS").sum())
    return fail_count == 0, int(len(df)), fail_count


def iter_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from iter_dicts(nested)
    elif isinstance(value, list):
        for item in value:
            yield from iter_dicts(item)


def has_non_finite_number(value: Any) -> bool:
    if isinstance(value, float):
        return not math.isfinite(value)
    if isinstance(value, dict):
        return any(has_non_finite_number(nested) for nested in value.values())
    if isinstance(value, list):
        return any(has_non_finite_number(item) for item in value)
    return False


def load_87_samples() -> tuple[list[Path], list[dict[str, Any]], int]:
    sample_paths = sorted(SAMPLE_DIR_87.glob("*.json"))
    payloads: list[dict[str, Any]] = []
    raw_nan_hits = 0
    for path in sample_paths:
        text = path.read_text(encoding="utf-8-sig")
        raw_nan_hits += text.count("NaN")
        payloads.append(json.loads(text))
    return sample_paths, payloads, raw_nan_hits


def build_contract_audit(backtest_summary: dict[str, Any]) -> pd.DataFrame:
    audit_files = list(backtest_summary.get("contract_audit_files", []))
    # 79번 이후 별도 생성된 방향성 감사도 백데이터 재검증에 포함한다.
    direction_file = "gold_engine_direction_effect_audit.csv"
    if (BACKTEST_DIR / direction_file).exists() and direction_file not in audit_files:
        audit_files.append(direction_file)
    if RULE_VALIDATIONS.exists() and RULE_VALIDATIONS.name not in audit_files:
        audit_files.append(RULE_VALIDATIONS.name)

    rows: list[dict[str, Any]] = []
    for file_name in audit_files:
        path = BACKTEST_DIR / file_name
        exists = path.exists()
        ok = False
        row_count = 0
        fail_count = 0
        if exists:
            ok, row_count, fail_count = all_result_pass(path)
        rows.append(
            {
                "audit_file": file_name,
                "exists": exists,
                "row_count": row_count,
                "fail_count": fail_count,
                "result": "PASS" if exists and ok else "FAIL",
                "reason_ko": "백데이터 계약 감사 파일은 존재해야 하며 모든 행이 PASS여야 한다.",
            }
        )
    return pd.DataFrame(rows)


def load_labeled_rows() -> tuple[list[str], pd.DataFrame]:
    header = list(read_csv(BACKTEST_ROWS, nrows=0).columns)
    needed = [
        *KEY_COLS,
        *NAME_COLS,
        "current_location_score",
        "data_reliability_score",
        "score_version",
        "decision_label",
        "growth_rebound_score_engine_active",
        *OFFICIAL_AXIS_COLS,
        *SEPARATE_SCORE_COLS,
    ]
    usecols = [col for col in needed if col in header]
    return header, read_csv(BACKTEST_ROWS, usecols=usecols)


def main() -> int:
    RULE_DIR.mkdir(parents=True, exist_ok=True)
    DOC_DIR.mkdir(parents=True, exist_ok=True)

    summary_84 = read_json(SUMMARY_84)
    summary_86 = read_json(SUMMARY_86)
    summary_87 = read_json(SUMMARY_87)
    backtest_summary = read_json(BACKTEST_SUMMARY)
    validations: list[dict[str, Any]] = []

    add_validation(
        validations,
        "88-V01",
        "선행 계약 검증 PASS",
        f"84={summary_84.get('fail_count')}, 86={summary_86.get('fail_count')}, 87={summary_87.get('fail_count')}",
        "모두 0",
        summary_84.get("fail_count") == 0 and summary_86.get("fail_count") == 0 and summary_87.get("fail_count") == 0,
        "백데이터 재검증은 84 전처리 계약, 86 엔진 계약, 87 런타임 JSON 계약이 모두 통과한 상태에서만 의미가 있다.",
    )

    add_validation(
        validations,
        "88-V02",
        "백데이터 row 수와 84 universe 일치",
        backtest_summary.get("row_count"),
        summary_84.get("backtest_rows"),
        backtest_summary.get("row_count") == summary_84.get("backtest_rows"),
        "84번 전처리 payload 계약에서 확인한 백데이터 universe와 실제 백테스트 요약 row 수가 같아야 한다.",
    )

    add_validation(
        validations,
        "88-V03",
        "백데이터 분기 범위와 84 universe 일치",
        ",".join(map(str, backtest_summary.get("quarters", []))),
        ",".join(map(str, summary_84.get("backtest_quarters", []))),
        list(map(str, backtest_summary.get("quarters", []))) == list(map(str, summary_84.get("backtest_quarters", []))),
        "2021~2025 검증 분기 범위가 달라지면 성능 수치와 계약 검증을 같은 근거로 볼 수 없다.",
    )

    contract_audit = build_contract_audit(backtest_summary)
    audit_fail_count = int(contract_audit["result"].ne("PASS").sum()) if not contract_audit.empty else 1
    add_validation(
        validations,
        "88-V04",
        "백데이터 감사 파일 전체 PASS",
        f"files={len(contract_audit)}, fail_files={audit_fail_count}",
        "fail_files=0",
        audit_fail_count == 0,
        "금지문구, 조인키, 라벨 계약, 결측 처리, 시간 누수, 방향성 감사가 모두 PASS여야 한다.",
    )

    header, labeled = load_labeled_rows()
    add_validation(
        validations,
        "88-V05",
        "labeled rows 실제 행 수와 요약 일치",
        len(labeled),
        backtest_summary.get("row_count"),
        len(labeled) == int(backtest_summary.get("row_count", -1)),
        "요약 JSON만 맞고 실제 labeled row 파일이 다르면 백데이터 검증 근거가 약해진다.",
    )

    key_nulls = int(labeled[KEY_COLS].isna().sum().sum())
    duplicate_keys = int(labeled.duplicated(KEY_COLS).sum())
    add_validation(
        validations,
        "88-V06",
        "코드 키 결측과 grain 중복 없음",
        f"key_nulls={key_nulls}, duplicate_keys={duplicate_keys}",
        "0, 0",
        key_nulls == 0 and duplicate_keys == 0,
        "상권명/업종명 표시가 아니라 분기+상권코드+업종코드로 결합해야 과장 집계가 생기지 않는다.",
    )

    name_blank_count = 0
    for col in NAME_COLS:
        if col in labeled.columns:
            name_blank_count += int(labeled[col].isna().sum())
            name_blank_count += int(labeled[col].astype(str).str.strip().eq("").sum())
    add_validation(
        validations,
        "88-V07",
        "표시명 결측 없음",
        name_blank_count,
        0,
        name_blank_count == 0,
        "JSON 리포트와 MD 다운로드는 사람이 읽어야 하므로 코드 키뿐 아니라 표시명도 비어 있으면 안 된다.",
    )

    axis_cols = [col for col in header if col.startswith("axis__")]
    add_validation(
        validations,
        "88-V08",
        "공식 축 컬럼 4개만 존재",
        ",".join(axis_cols),
        ",".join(OFFICIAL_AXIS_COLS),
        sorted(axis_cols) == sorted(OFFICIAL_AXIS_COLS),
        "공식 현재입지 총점은 sales, competition, demand, accessibility 4축만으로 설명되어야 한다.",
    )

    score_cols = ["current_location_score", "data_reliability_score", *OFFICIAL_AXIS_COLS]
    range_bad = 0
    for col in score_cols:
        values = pd.to_numeric(labeled[col], errors="coerce")
        range_bad += int((values.dropna().lt(0) | values.dropna().gt(100)).sum())
    add_validation(
        validations,
        "88-V09",
        "공식 점수 범위 0~100 유지",
        range_bad,
        0,
        range_bad == 0,
        "백분위/WLC 기반 점수는 리포트에서 해석 가능한 0~100 범위를 벗어나면 안 된다.",
    )

    separate_missing = [col for col in SEPARATE_SCORE_COLS if col not in labeled.columns]
    separate_range_bad = 0
    for col in SEPARATE_SCORE_COLS:
        if col in labeled.columns:
            values = pd.to_numeric(labeled[col], errors="coerce").dropna()
            separate_range_bad += int((values.lt(0) | values.gt(100)).sum())
    add_validation(
        validations,
        "88-V10",
        "별도 점수 컬럼 존재 및 범위 유지",
        f"missing={separate_missing}, range_bad={separate_range_bad}",
        "missing=[], range_bad=0",
        not separate_missing and separate_range_bad == 0,
        "성장/비용 후보는 버리지 않되 공식 축과 분리된 별도 점수로만 보존해야 한다.",
    )

    active_col = "growth_rebound_score_engine_active"
    active_true_count = int(labeled[active_col].astype(str).str.lower().isin(["true", "1"]).sum())
    add_validation(
        validations,
        "88-V11",
        "성장 반등 후보 엔진 비활성 유지",
        active_true_count,
        0,
        active_true_count == 0,
        "성장 반등 후보는 현재 공식 점수 엔진이 아니라 후보 신호이므로 백데이터에서도 active가 되면 안 된다.",
    )

    forbidden_hits = 0
    if "decision_label" in labeled.columns:
        label_text = "\n".join(labeled["decision_label"].dropna().astype(str).unique().tolist())
        forbidden_hits = sum(label_text.count(term) for term in FORBIDDEN_TERMS)
    add_validation(
        validations,
        "88-V12",
        "결정 라벨 금지 표현 없음",
        forbidden_hits,
        0,
        forbidden_hits == 0,
        "리포트 출력은 창업 성공확률, 매출 보장, 월세/권리금 반영 수익성처럼 검증되지 않은 표현을 쓰면 안 된다.",
    )

    metrics = backtest_summary.get("overall_metrics", {})
    sensitivity = backtest_summary.get("sensitivity_summary", {})
    spatial = backtest_summary.get("spatial_summary", {})
    perf_ok = (
        float(metrics.get("score_spearman_next_sales_pct_same_industry", -1)) >= 0.6
        and float(metrics.get("top_vs_bottom_avg_next_sales_ratio", 0)) > 10
        and float(sensitivity.get("min_rank_corr_with_baseline", 0)) >= 0.95
        and int(float(spatial.get("blocks_with_positive_sales_pct_corr", 0))) == 25
    )
    add_validation(
        validations,
        "88-V13",
        "백데이터 성능/안정성 하한 유지",
        (
            f"sales_pct_corr={metrics.get('score_spearman_next_sales_pct_same_industry')}, "
            f"top_bottom_ratio={metrics.get('top_vs_bottom_avg_next_sales_ratio')}, "
            f"sensitivity_rank={sensitivity.get('min_rank_corr_with_baseline')}, "
            f"spatial_positive_blocks={spatial.get('blocks_with_positive_sales_pct_corr')}"
        ),
        "corr>=0.6, ratio>10, rank>=0.95, blocks=25",
        perf_ok,
        "현재입지 점수는 성장률 보장이 아니라 다음분기 동업종 매출 수준 후보 선별력이 유지되는지 봐야 한다.",
    )

    add_validation(
        validations,
        "88-V14",
        "신뢰도 게이트 미만 행 없음",
        metrics.get("reliability_below_gate_rows"),
        0,
        int(metrics.get("reliability_below_gate_rows", -1)) == 0,
        "데이터 신뢰도 기준 미만 행은 판단 보류가 되어야 하며, 공식 후보군에 섞이면 안 된다.",
    )

    versions = sorted(labeled["score_version"].dropna().astype(str).unique().tolist())
    add_validation(
        validations,
        "88-V15",
        "백데이터 score_version 단일성",
        ",".join(versions),
        metrics.get("score_version"),
        len(versions) == 1 and versions[0] == metrics.get("score_version"),
        "서로 다른 점수 버전이 한 백데이터 파일에 섞이면 검증 수치를 하나의 알고리즘 근거로 볼 수 없다.",
    )

    runtime_quarter = str(summary_87.get("quarter"))
    backtest_quarters = list(map(str, backtest_summary.get("quarters", [])))
    add_validation(
        validations,
        "88-V16",
        "런타임 최신분기와 백테스트 라벨분기 분리",
        f"runtime={runtime_quarter}, backtest_last={backtest_quarters[-1] if backtest_quarters else ''}",
        "runtime not in backtest quarters",
        runtime_quarter not in backtest_quarters,
        "20261처럼 다음분기 매출 라벨이 아직 없는 분기는 실제 JSON 출력 검증에는 쓰되 성능 백테스트에는 넣지 않는다.",
    )

    sample_paths, sample_payloads, raw_nan_hits = load_87_samples()
    runtime_contract_failures = 0
    component_candidate_seen = False
    excluded_unit_ticket_seen = False
    for payload in sample_payloads:
        scores = payload.get("scores", {})
        score_result = payload.get("score_result", {})
        axis_scores = scores.get("axis_scores", {})
        runtime_contract_failures += int(score_result.get("total_score") != scores.get("current_location_score"))
        runtime_contract_failures += int(sorted(axis_scores.keys()) != sorted(OFFICIAL_AXES))
        runtime_contract_failures += int("cost_risk" in axis_scores or "cost_risk_score" in axis_scores)
        runtime_contract_failures += int(not payload.get("warnings"))
        runtime_contract_failures += int(len(payload.get("evidence_pack", {}).get("forbidden_claims", [])) < 5)
        runtime_contract_failures += int(not payload.get("text_model_payload", {}).get("must_not_do"))
        runtime_contract_failures += int(has_non_finite_number(payload))

        component_keys = [
            item.get("key")
            for item in score_result.get("components", [])
            if isinstance(item, dict)
        ]
        component_candidate_seen = component_candidate_seen or any(
            key in {"growth_rebound_candidate", "transit_accessibility_250m_candidate"} for key in component_keys
        )
        for item in iter_dicts(payload):
            if item.get("score_contribution_status") == "excluded_from_sales_axis":
                excluded_unit_ticket_seen = True

    add_validation(
        validations,
        "88-V17",
        "87 런타임 JSON 샘플 계약 재확인",
        f"samples={len(sample_payloads)}, failures={runtime_contract_failures}, raw_NaN={raw_nan_hits}",
        "samples>=3, failures=0, raw_NaN=0",
        len(sample_payloads) >= 3 and runtime_contract_failures == 0 and raw_nan_hits == 0,
        "87번은 단건 스모크이므로 88번에서도 total_score 일치, 공식 4축, 비용 분리, 경고/금지문구/텍스트모델 계약, NaN 금지를 다시 확인한다.",
    )

    add_validation(
        validations,
        "88-V18",
        "컴포넌트 표시와 공식 산식 분리",
        f"candidate_component_seen={component_candidate_seen}, official_axis_cols={','.join(OFFICIAL_AXIS_COLS)}",
        "candidate may appear in components, not in official axis_scores",
        component_candidate_seen and sorted(axis_cols) == sorted(OFFICIAL_AXIS_COLS),
        "리포트 컴포넌트에는 후보 설명이 보일 수 있지만 공식 점수 산식은 axis_scores 4축으로만 제한되어야 한다.",
    )

    add_validation(
        validations,
        "88-V19",
        "객단가 evidence-only 상태 확인",
        excluded_unit_ticket_seen,
        True,
        excluded_unit_ticket_seen,
        "객단가는 소비 단가 참고값으로 보존하되 sales 축 직접 가점, 구매력 보장, 성장률 보장, 성공확률 근거로 쓰지 않는다.",
    )

    validation_df = pd.DataFrame(validations)
    pass_count, fail_count = pass_fail_counts(validations)
    decision = "V1_BACKDATA_CONTRACT_REVALIDATION_PASS" if fail_count == 0 else "V1_BACKDATA_CONTRACT_REVALIDATION_FAIL"

    audit_summary_rows = [
        {
            "item": "backtest_rows",
            "value": backtest_summary.get("row_count"),
            "reason_ko": "84번 전처리 계약과 같은 백데이터 universe인지 확인",
        },
        {
            "item": "backtest_quarters",
            "value": ",".join(backtest_quarters),
            "reason_ko": "다음분기 라벨이 존재하는 2021Q1~2025Q4만 성능 검증에 사용",
        },
        {
            "item": "official_axes",
            "value": ",".join(OFFICIAL_AXES),
            "reason_ko": "공식 현재입지 점수에 허용되는 축",
        },
        {
            "item": "separate_score_columns",
            "value": ",".join(SEPARATE_SCORE_COLS),
            "reason_ko": "총점과 섞지 않고 별도 출력 또는 후보 신호로만 유지",
        },
        {
            "item": "runtime_smoke_quarter",
            "value": runtime_quarter,
            "reason_ko": "최신분기 JSON 출력 확인용이며 백테스트 라벨 분기와 분리",
        },
    ]
    audit_df = pd.concat([pd.DataFrame(audit_summary_rows), contract_audit], ignore_index=True)

    summary = {
        "validation_number": 88,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "validation_version": VERSION,
        "backtest_rows": backtest_summary.get("row_count"),
        "backtest_quarters": backtest_quarters,
        "runtime_smoke_quarter": runtime_quarter,
        "runtime_sample_files": [str(path.relative_to(ROOT)) for path in sample_paths],
        "official_axes": OFFICIAL_AXES,
        "separate_score_columns": SEPARATE_SCORE_COLS,
        "contract_audit_files": contract_audit["audit_file"].tolist(),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "decision": decision,
        "decision_reason_ko": (
            "84 전처리 계약, 86 엔진 계약, 87 런타임 JSON 계약과 2021~2025 백데이터 검증 universe가 서로 충돌하지 않는다."
            if fail_count == 0
            else "백데이터 계약 재검증에서 실패 항목이 있어 공식 v1 근거로 쓰기 전 보정이 필요하다."
        ),
        "next_step": "입력 resolver 운영 연결 검증 또는 전체 백테스트 재실행 중 하나를 진행한다.",
        "outputs": [
            str(OUT_VALIDATION.relative_to(ROOT)),
            str(OUT_AUDIT.relative_to(ROOT)),
            str(OUT_SUMMARY.relative_to(ROOT)),
            str(OUT_DOC.relative_to(ROOT)),
        ],
    }

    validation_df.to_csv(OUT_VALIDATION, index=False, encoding="utf-8-sig")
    audit_df.to_csv(OUT_AUDIT, index=False, encoding="utf-8-sig")
    with OUT_SUMMARY.open("w", encoding="utf-8-sig") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    doc = f"""# 88. v1 백데이터 계약 재검증

작성일: 2026-07-07  
버전: `{VERSION}`  
판정: `{decision}`

## 목적

84번은 전처리 payload 계약, 86번은 실제 엔진 read 계약, 87번은 최신분기 JSON 출력 계약을 확인했다.  
이번 88번 검증은 그 세 계약이 기존 2021~2025 백데이터 성능 검증 산출물과 충돌하지 않는지 확인한다.

즉 이 검증은 새 점수를 만드는 작업이 아니라, 현재 백데이터 검증 결과를 v1 알고리즘 근거로 계속 써도 되는지 확인하는 안전장치다.

## 핵심 판정

- 백데이터 행 수: {backtest_summary.get("row_count")}
- 백데이터 분기: {", ".join(backtest_quarters)}
- 런타임 JSON 스모크 분기: {runtime_quarter}
- 공식 현재입지 축: {", ".join(OFFICIAL_AXES)}
- 별도 점수/후보 컬럼: {", ".join(SEPARATE_SCORE_COLS)}
- PASS: {pass_count}
- FAIL: {fail_count}

## 검증 결과

{md_table(validations, ["validation_id", "validation_name", "observed", "expected", "result", "reason_ko"])}

## 백데이터 감사 파일

{md_table(contract_audit.to_dict("records"), ["audit_file", "exists", "row_count", "fail_count", "result", "reason_ko"])}

## 해석

- 현재 백데이터는 `20211~20254` 20개 분기, 427,553행 universe로 유지된다.
- `20261`은 실제 JSON 출력 smoke에는 쓰지만 다음분기 매출 라벨이 없으므로 성능 백테스트에는 넣지 않는다.
- 공식 현재입지 점수는 `sales`, `competition`, `demand`, `accessibility` 4축만 사용한다.
- 비용 리스크와 성장 반등 후보는 버리지 않고 별도 점수 또는 candidate signal로 보존한다.
- 리포트 컴포넌트에는 성장 반등/교통 후보 설명이 함께 보일 수 있지만, 이것은 공식 축 점수 산식에 포함됐다는 뜻이 아니다.
- 객단가는 소비 단가 참고값으로만 보존된 evidence-only 항목이며, sales 축 직접 가점이나 성공확률·성장률·매출 상승 보장 근거로 쓰지 않는다.
- 이 검증은 창업 성공확률, 매출 보장, 성장률 보장, 월세/권리금 반영 수익성 같은 표현을 허용하지 않는다.

## 다음 작업

1. 입력 resolver를 실제 지도 클릭/주소/상권명/업종 계층 선택과 연결한다.
2. 필요하면 전체 백테스트를 재실행해 같은 88번 계약을 다시 통과시키는지 확인한다.
3. 최신 교통·LocalData·R-ONE 후보는 공식 점수 승격 전 별도 evidence-only 검증을 계속한다.
"""
    OUT_DOC.write_text(doc, encoding="utf-8-sig")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
