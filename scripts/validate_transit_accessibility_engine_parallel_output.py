# -*- coding: utf-8 -*-
"""
63. 교통 접근성 250m 후보 병렬 출력 검증.

목적:
  - [RV59][RV60] 후보 산식을 공식 loc_score.v2.4 총점에 섞지 않았는지 확인한다.
  - 단건 JSON/배치 scored 프레임에 후보 버전, 후보 산식, 금지 표현 계약이 분리되어 붙는지 확인한다.

이 검증은 단순 컬럼 존재 검사가 아니라 공식 점수 산식 재계산, 후보 산식 재계산,
증거 payload 계약, 미래월 누수 방지 조건을 함께 본다.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
ENGINE_PATH = ROOT / "scripts" / "build_rule_based_location_scores.py"
OUT_DIR = ROOT / "datacorpus" / "_rule_validation"
MD_DIR = ROOT / "research" / "rule_validation"
VALIDATION_NO = 63
QUARTER = 20251
EXPECTED_OFFICIAL_VERSION = "loc_score.v2.4-sales-ticket-removed-rc1"
EXPECTED_CANDIDATE_VERSION = "loc_score.v2.5-transit-accessibility-candidate-rc1"


def load_engine():
    spec = importlib.util.spec_from_file_location("build_rule_based_location_scores", ENGINE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"엔진 모듈을 불러올 수 없습니다: {ENGINE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def clean_json_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (np.integer, np.floating)):
        value = value.item()
    if isinstance(value, float) and np.isnan(value):
        return None
    return value


def validation_row(validation_id: str, name: str, observed: Any, expected: Any, passed: bool, reason_ko: str) -> dict[str, Any]:
    return {
        "validation_id": validation_id,
        "validation_name": name,
        "observed": clean_json_value(observed),
        "expected": expected,
        "result": "PASS" if passed else "FAIL",
        "reason_ko": reason_ko,
    }


def dataframe_to_markdown(df: pd.DataFrame) -> str:
    """추가 패키지 없이 검증표를 markdown으로 만든다."""
    cols = list(df.columns)
    lines = [
        "| " + " | ".join(cols) + " |",
        "| " + " | ".join(["---"] * len(cols)) + " |",
    ]
    for _, row in df.iterrows():
        values = []
        for col in cols:
            text = "" if pd.isna(row[col]) else str(row[col])
            values.append(text.replace("|", "\\|").replace("\n", " "))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def recompute_current_scores(engine, scored: pd.DataFrame, use_candidate_axis: bool = False) -> pd.Series:
    """공식 WLC를 행 단위로 재계산한다.

    후보 검증에서도 같은 가중치 함수를 쓰되, 공식 컬럼을 덮지 않고 검증용 Series로만 만든다.
    """
    weight_sets = engine.load_axis_weights()
    values: list[float | None] = []
    for _, row in scored.iterrows():
        _, weights = engine.weight_set_for_industry(row.get("서비스_업종_코드", ""), weight_sets)
        axis_scores = {}
        for ax in engine.CURRENT_AXES:
            value = row.get(f"axis__{ax}")
            if value is None or pd.isna(value):
                axis_scores[ax] = None
            else:
                axis_scores[ax] = float(value)
        if use_candidate_axis:
            cand = row.get("transit_accessibility_250m_candidate_axis")
            if cand is None or pd.isna(cand):
                values.append(None)
                continue
            axis_scores["accessibility"] = float(cand)
        current = engine._weighted_current_score(axis_scores, weights)
        values.append(None if current is None else round(float(current), 2))
    return pd.Series(values, index=scored.index)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    MD_DIR.mkdir(parents=True, exist_ok=True)
    engine = load_engine()

    base = engine.build_indicator_frame(QUARTER)
    base_scored_input = engine.percentile_scores(base)
    scored = engine.score_frame(base_scored_input)
    args = SimpleNamespace(
        quarter=QUARTER,
        trade_area_code="3001491",
        trade_area_name=None,
        industry_code="CS100001",
        industry_name=None,
    )
    sample_result = engine.build_result(base_scored_input, scored, args, QUARTER)

    candidate_feature_path = ROOT / "datacorpus" / "_rule_validation" / "59_transit_accessibility_candidate_quarter_features.csv"
    feature = pd.read_csv(
        candidate_feature_path,
        usecols=["기준_년분기_코드", "상권_코드", "transit_month_count", "transit_total_250m_score"],
        encoding="utf-8-sig",
        dtype={"기준_년분기_코드": str, "상권_코드": str},
    )
    feature_q = feature[feature["기준_년분기_코드"] == str(QUARTER)].copy()

    validations: list[dict[str, Any]] = []

    validations.append(validation_row(
        "63-V01",
        "공식 점수 버전 불변",
        engine.SCORE_VERSION,
        EXPECTED_OFFICIAL_VERSION,
        engine.SCORE_VERSION == EXPECTED_OFFICIAL_VERSION,
        "교통 후보를 붙여도 공식 v2.4 점수 버전을 덮어쓰지 않아야 한다.",
    ))
    validations.append(validation_row(
        "63-V02",
        "후보 점수 버전 분리",
        getattr(engine, "TRANSIT_CANDIDATE_SCORE_VERSION", None),
        EXPECTED_CANDIDATE_VERSION,
        getattr(engine, "TRANSIT_CANDIDATE_SCORE_VERSION", None) == EXPECTED_CANDIDATE_VERSION,
        "후보는 별도 버전으로 남겨야 공식 점수 승격과 구분된다.",
    ))

    required_cols = [
        "current_location_score",
        "axis__accessibility",
        "transit_accessibility_candidate_score_version",
        "transit_accessibility_candidate_status",
        "transit_accessibility_candidate_engine_active",
        "transit_accessibility_candidate_engine_promotion_ready",
        "transit_month_count",
        "transit_total_250m_score",
        "transit_accessibility_250m_candidate_axis",
        "current_location_score_transit_250m_candidate",
    ]
    missing = [c for c in required_cols if c not in scored.columns]
    validations.append(validation_row(
        "63-V03",
        "병렬 후보 컬럼 존재",
        ";".join(missing) if missing else "none",
        "none",
        not missing,
        "공식 총점과 후보 총점을 나란히 보려면 후보 컬럼이 명시적으로 분리되어야 한다.",
    ))

    validations.append(validation_row(
        "63-V04",
        "조인 후 행 수 보존",
        len(scored),
        len(base_scored_input),
        len(scored) == len(base_scored_input),
        "교통 후보는 상권 grain many-to-one 조인이다. 행 수가 늘면 공간 fan-out 오염이다.",
    ))

    duplicate_keys = int(feature_q.duplicated(["기준_년분기_코드", "상권_코드"]).sum())
    validations.append(validation_row(
        "63-V05",
        "후보 피처 키 중복 없음",
        duplicate_keys,
        0,
        duplicate_keys == 0,
        "후보 피처는 분기×상권 하나당 한 행이어야 엔진 조인이 안정적이다.",
    ))

    official_recomputed = recompute_current_scores(engine, scored, use_candidate_axis=False)
    official_diff = (official_recomputed - scored["current_location_score"]).abs()
    official_max_diff = float(official_diff.dropna().max()) if official_diff.notna().any() else 0.0
    # 출력 축 점수는 소수 둘째 자리로 반올림되어 있으므로, 재계산 검증은 반올림 오차 0.02 이내를 허용한다.
    validations.append(validation_row(
        "63-V06",
        "공식 WLC 재계산 일치",
        round(official_max_diff, 6),
        "<=0.02",
        official_max_diff <= 0.02,
        "공식 current_location_score가 후보 접근성축을 쓰지 않고 기존 4축 WLC로 계산되는지 확인한다.",
    ))

    attached = scored["transit_accessibility_candidate_status"] == "candidate_attached_not_in_current_score"
    attached_count = int(attached.sum())
    validations.append(validation_row(
        "63-V07",
        "후보 부착 행 존재",
        attached_count,
        ">0",
        attached_count > 0,
        "후보 산식이 실제 엔진 출력에 붙지 않으면 병렬 검토가 불가능하다.",
    ))

    candidate_axis_expected = scored["axis__accessibility"] * 0.70 + scored["transit_total_250m_score"] * 0.30
    candidate_axis_diff = (candidate_axis_expected[attached] - scored.loc[attached, "transit_accessibility_250m_candidate_axis"]).abs()
    candidate_axis_max_diff = float(candidate_axis_diff.dropna().max()) if candidate_axis_diff.notna().any() else 0.0
    validations.append(validation_row(
        "63-V08",
        "후보 접근성축 산식 재계산 일치",
        round(candidate_axis_max_diff, 6),
        "<=0.01",
        candidate_axis_max_diff <= 0.01,
        "60번에서 고정한 70/30 산식을 코드 출력이 그대로 따르는지 확인한다.",
    ))

    candidate_current_recomputed = recompute_current_scores(engine, scored.loc[attached].copy(), use_candidate_axis=True)
    candidate_current_diff = (
        candidate_current_recomputed - scored.loc[attached, "current_location_score_transit_250m_candidate"]
    ).abs()
    candidate_current_max_diff = float(candidate_current_diff.dropna().max()) if candidate_current_diff.notna().any() else 0.0
    validations.append(validation_row(
        "63-V09",
        "후보 현재입지 총점 산식 재계산 일치",
        round(candidate_current_max_diff, 6),
        "<=0.02",
        candidate_current_max_diff <= 0.02,
        "후보 총점은 공식 WLC에서 접근성축만 후보축으로 바꾼 값이어야 한다.",
    ))

    active_values = sorted(set(scored["transit_accessibility_candidate_engine_active"].astype(str).str.lower()))
    promotion_values = sorted(set(scored["transit_accessibility_candidate_engine_promotion_ready"].astype(str).str.lower()))
    validations.append(validation_row(
        "63-V10",
        "후보 공식 승격 플래그 비활성",
        f"active={active_values}; promotion={promotion_values}",
        "all false",
        active_values == ["false"] and promotion_values == ["false"],
        "후보가 붙어도 공식 엔진 승격 상태로 오인되면 안 된다.",
    ))

    sample_scores = sample_result["scores"]
    sample_candidate = sample_result["score_result"]["candidate_signals"].get("transit_accessibility_250m_candidate")
    validations.append(validation_row(
        "63-V11",
        "단건 JSON candidate_signals 분리",
        "present" if sample_candidate else "missing",
        "present",
        bool(sample_candidate)
        and sample_result["score_result"]["total_score"] == sample_scores["current_location_score"]
        and sample_result["score_result"]["total_score"] != sample_scores["current_location_score_transit_250m_candidate"],
        "단건 JSON의 total_score는 공식 점수이고, 교통 후보는 candidate_signals 아래에 있어야 한다.",
    ))

    forbidden_text = " ".join(
        str(sample_candidate.get(k, ""))
        for k in ["사용_제한", "forbidden_claim_ko", "algorithm_use_note_ko", "formula_ko"]
    ) if sample_candidate else ""
    validations.append(validation_row(
        "63-V12",
        "후보 금지 표현 계약 명시",
        "금지" if "금지" in forbidden_text and "실제 상권 방문자" in forbidden_text else forbidden_text[:80],
        "금지 문구와 실제 방문자 오해 방지",
        "금지" in forbidden_text and "실제 상권 방문자" in forbidden_text and "방문확률" in forbidden_text,
        "교통 승하차 후보는 실제 방문자·방문확률이 아니라 접근성 프록시라는 제한을 payload에 싣는다.",
    ))

    month_counts = sorted(pd.to_numeric(scored.loc[attached, "transit_month_count"], errors="coerce").dropna().unique().tolist())
    validations.append(validation_row(
        "63-V13",
        "분기 후보 월수 게이트",
        ",".join(str(int(x)) for x in month_counts[:10]),
        "3",
        month_counts == [3.0] or month_counts == [3],
        "2025년 1분기 후보 검증은 해당 분기 3개월 집계만 써야 하며 202605 같은 미래월이 섞이면 안 된다.",
    ))

    component_keys = [c.get("key") for c in sample_result["score_result"].get("components", [])]
    validations.append(validation_row(
        "63-V14",
        "리포트 컴포넌트 후보 라벨 분리",
        ";".join(str(x) for x in component_keys),
        "official axes plus candidate labels",
        "transit_accessibility_250m_candidate" in component_keys
        and all(ax in component_keys for ax in engine.CURRENT_AXES),
        "리포트에 후보 신호를 보여주되 공식 4축과 키를 분리한다.",
    ))

    validation_df = pd.DataFrame(validations)
    pass_count = int((validation_df["result"] == "PASS").sum())
    fail_count = int((validation_df["result"] == "FAIL").sum())
    decision = (
        "TRANSIT_ACCESSIBILITY_ENGINE_PARALLEL_OUTPUT_PASS_NOT_PROMOTED"
        if fail_count == 0
        else "TRANSIT_ACCESSIBILITY_ENGINE_PARALLEL_OUTPUT_FAIL"
    )

    sample_cols = [
        "기준_년분기_코드",
        "상권_코드",
        "상권_코드_명",
        "서비스_업종_코드",
        "서비스_업종_코드_명",
        "current_location_score",
        "axis__accessibility",
        "transit_total_250m_score",
        "transit_accessibility_250m_candidate_axis",
        "current_location_score_transit_250m_candidate",
        "transit_accessibility_candidate_status",
        "transit_accessibility_candidate_engine_active",
        "transit_accessibility_candidate_engine_promotion_ready",
    ]
    sample_rows = scored.loc[attached, [c for c in sample_cols if c in scored.columns]].head(50).copy()

    validation_path = OUT_DIR / "63_transit_accessibility_engine_parallel_output_validation.csv"
    sample_path = OUT_DIR / "63_transit_accessibility_engine_parallel_output_sample_rows.csv"
    summary_path = OUT_DIR / "63_transit_accessibility_engine_parallel_output_summary.json"
    md_path = MD_DIR / "63_transit_accessibility_engine_parallel_output_validation_20260707.md"

    validation_df.to_csv(validation_path, index=False, encoding="utf-8-sig")
    sample_rows.to_csv(sample_path, index=False, encoding="utf-8-sig")

    summary = {
        "validation_number": VALIDATION_NO,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "validation_version": "transit_accessibility_engine_parallel_output.v0.1-20260707",
        "quarter": QUARTER,
        "official_score_version": engine.SCORE_VERSION,
        "candidate_score_version": engine.TRANSIT_CANDIDATE_SCORE_VERSION,
        "scored_rows": int(len(scored)),
        "candidate_attached_rows": attached_count,
        "candidate_feature_rows_quarter": int(len(feature_q)),
        "official_recompute_max_diff": round(official_max_diff, 6),
        "candidate_axis_formula_max_diff": round(candidate_axis_max_diff, 6),
        "candidate_current_formula_max_diff": round(candidate_current_max_diff, 6),
        "sample_official_current_score": clean_json_value(sample_scores["current_location_score"]),
        "sample_candidate_current_score": clean_json_value(sample_scores["current_location_score_transit_250m_candidate"]),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "decision": decision,
        "next_step": "keep_parallel_candidate_output_then_review_ui_ai_report_copy_or_cost_proxy_mapping",
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    md_lines = [
        "# 63. 교통 접근성 250m 후보 병렬 출력 검증",
        "",
        f"작성일: {summary['generated_at']}",
        "",
        "## 목적",
        "",
        "60번에서 검증한 교통 접근성 250m 70/30 후보를 공식 `loc_score.v2.4` 점수에 섞지 않고, 엔진 출력에 별도 후보 신호로만 붙였는지 검증했다.",
        "",
        "## 핵심 결과",
        "",
        f"- validation version: `{summary['validation_version']}`",
        f"- 기준 분기: `{QUARTER}`",
        f"- 공식 점수 버전: `{summary['official_score_version']}`",
        f"- 후보 점수 버전: `{summary['candidate_score_version']}`",
        f"- scored rows: {summary['scored_rows']:,}",
        f"- candidate attached rows: {summary['candidate_attached_rows']:,}",
        f"- candidate feature rows quarter: {summary['candidate_feature_rows_quarter']:,}",
        f"- 공식 WLC 재계산 최대 차이: {summary['official_recompute_max_diff']}",
        f"- 후보 접근성축 산식 최대 차이: {summary['candidate_axis_formula_max_diff']}",
        f"- 후보 현재입지 산식 최대 차이: {summary['candidate_current_formula_max_diff']}",
        f"- PASS: {pass_count}",
        f"- FAIL: {fail_count}",
        f"- decision: `{decision}`",
        "",
        "## 해석",
        "",
        "- 공식 `current_location_score`와 `score_version`은 `loc_score.v2.4-sales-ticket-removed-rc1`로 유지했다.",
        "- 교통 후보는 `transit_accessibility_250m_candidate_axis`, `current_location_score_transit_250m_candidate`, `score_result.candidate_signals.transit_accessibility_250m_candidate`에만 붙었다.",
        "- 후보는 실제 방문자 수, 실제 구매자 수, 실제 도보시간, 방문확률, 창업 성공확률로 표현하지 않는다.",
        "- 이번 결과는 병렬 출력 PASS이지 공식 점수 승격이 아니다.",
        "",
        "## 검증 결과",
        "",
        dataframe_to_markdown(validation_df),
        "",
        "## 산출물",
        "",
        f"- `{validation_path.relative_to(ROOT)}`",
        f"- `{sample_path.relative_to(ROOT)}`",
        f"- `{summary_path.relative_to(ROOT)}`",
        "",
        "## 다음 2보 전진 1보 후퇴",
        "",
        "1. 전진: 공식 v2.4를 유지한 채 교통 후보를 엔진 출력에 병렬로 붙였다.",
        "2. 전진: 단건 JSON, candidate_signals, text model 금지 규칙까지 후보 신호를 전달한다.",
        "3. 후퇴: 후보 점수가 좋아 보이거나 유용해 보여도 공식 총점, 등급, 성공확률 표현으로 승격하지 않는다.",
    ]
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
