from __future__ import annotations

import importlib.util
import inspect
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
GOLD_DIR = ROOT / "datacorpus" / "_gold"
BACKTEST_DIR = ROOT / "datacorpus" / "_score_backtest_gold"
RULE_DIR = ROOT / "datacorpus" / "_rule_validation"
DOC_DIR = ROOT / "research" / "rule_validation"
ENGINE_PATH = ROOT / "scripts" / "build_rule_based_location_scores.py"

OUT_VALIDATION = RULE_DIR / "82_cost_proxy_official_use_contract_validation.csv"
OUT_SUMMARY = RULE_DIR / "82_cost_proxy_official_use_contract_summary.json"
OUT_DOC = DOC_DIR / "82_cost_proxy_official_use_contract_20260707.md"

RTMS_GOLD = GOLD_DIR / "gold_cost_risk_q_area.csv"
RONE_CANDIDATE = GOLD_DIR / "gold_cost_risk_rone_region_trade_area_candidate.csv"
BROKER_CANDIDATE = GOLD_DIR / "gold_cost_risk_broker_sgg_candidate.csv"
COMPONENT_METRICS = BACKTEST_DIR / "gold_engine_backtest_component_metrics.csv"
FORBIDDEN_AUDIT = BACKTEST_DIR / "gold_engine_forbidden_claim_audit.csv"

VERSION = "cost_proxy_official_use_contract.v0.1-20260707"


def read_csv(path: Path, usecols: list[str] | None = None) -> pd.DataFrame:
    return pd.read_csv(path, encoding="utf-8-sig", usecols=usecols)


def import_engine():
    spec = importlib.util.spec_from_file_location("build_rule_based_location_scores", ENGINE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"엔진 모듈을 불러올 수 없습니다: {ENGINE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def false_all(series: pd.Series) -> bool:
    return series.astype(str).str.strip().str.lower().isin(["false", "0", "nan", "none"]).all()


def true_all(series: pd.Series) -> bool:
    return series.astype(str).str.strip().str.lower().isin(["true", "1"]).all()


def contains_all_text(series: pd.Series, terms: list[str]) -> bool:
    text = " ".join(series.dropna().astype(str).unique().tolist())
    return all(term in text for term in terms)


def add_validation(
    rows: list[dict[str, Any]],
    validation_id: str,
    validation_name: str,
    observed: Any,
    expected: Any,
    ok: bool,
    reason_ko: str,
) -> None:
    rows.append(
        {
            "validation_id": validation_id,
            "validation_name": validation_name,
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
        vals = [str(row.get(col, "")).replace("\n", " ") for col in columns]
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def main() -> int:
    RULE_DIR.mkdir(parents=True, exist_ok=True)
    DOC_DIR.mkdir(parents=True, exist_ok=True)

    engine = import_engine()
    rtms = read_csv(
        RTMS_GOLD,
        [
            "상권_코드",
            "자치구_코드",
            "기준_년분기_코드",
            "건물면적당_거래금액_중앙값_만원_per_m2",
            "directness_level",
            "forbidden_claim_ko",
            "direct_score_allowed",
            "proxy_score_allowed",
            "proxy_reason_ko",
        ],
    )
    rone = read_csv(
        RONE_CANDIDATE,
        [
            "상권_코드",
            "기준_년분기_코드",
            "mapping_scope",
            "mapping_confidence",
            "forbidden_claim_ko",
            "direct_score_allowed",
            "proxy_score_allowed",
            "engine_promotion_ready",
        ],
    )
    broker = read_csv(
        BROKER_CANDIDATE,
        [
            "시군구_코드",
            "기준_년분기_코드",
            "direct_score_allowed",
            "engine_score_allowed",
            "valid_for_backtest",
            "proxy_reason_ko",
            "forbidden_claim_ko",
        ],
    )
    component_metrics = read_csv(COMPONENT_METRICS)
    forbidden_audit = read_csv(FORBIDDEN_AUDIT)

    current_axes = list(getattr(engine, "CURRENT_AXES"))
    indicators = getattr(engine, "INDICATORS")
    cost_spec = indicators.get("자치구_상업실거래_단가", {})
    weights = engine.load_axis_weights()
    scores_payload_source = inspect.getsource(engine.scores_payload)
    weighted_score_source = inspect.getsource(engine._weighted_current_score)

    rows: list[dict[str, Any]] = []

    add_validation(
        rows,
        "82-V01",
        "비용 지표 명세가 비용축·비용형·자치구 grain으로 고정",
        f"axis={cost_spec.get('axis')}; direction={cost_spec.get('direction')}; grain={cost_spec.get('grain')}",
        "axis=cost_risk; direction=cost; grain=district",
        cost_spec.get("axis") == "cost_risk"
        and cost_spec.get("direction") == "cost"
        and cost_spec.get("grain") == "district",
        "RTMS 비용 프록시는 상권 직접값이 아니라 자치구 비용 압력 지표이므로 별도 비용축과 비용형 반전을 유지해야 한다.",
    )
    add_validation(
        rows,
        "82-V02",
        "현재입지 공식 4축에서 비용축 제외",
        ",".join(current_axes),
        "sales,competition,demand,accessibility only",
        "cost_risk" not in current_axes and set(current_axes) == {"sales", "competition", "demand", "accessibility"},
        "비용 리스크는 개별 점포 수익성 직접 판단이 아니므로 현재입지 WLC 점수에 섞지 않고 별도 출력한다.",
    )
    add_validation(
        rows,
        "82-V03",
        "현재입지 가중치 로더의 비용축 제외",
        {name: sorted(w.keys()) for name, w in weights.items()},
        "모든 weight_set에서 CURRENT_AXES만 사용",
        all("cost_risk" not in w and set(w) == set(current_axes) for w in weights.values())
        and all(abs(sum(w.values()) - 1.0) < 1e-9 for w in weights.values()),
        "가중치 파일에 비용 관련 값이 있더라도 엔진은 현재입지 4축만 재정규화해야 한다.",
    )
    add_validation(
        rows,
        "82-V04",
        "현재입지 계산 함수가 CURRENT_AXES만 사용",
        "CURRENT_AXES" in weighted_score_source and "cost_risk" not in weighted_score_source,
        "CURRENT_AXES 기반, cost_risk 직접 참조 없음",
        "CURRENT_AXES" in weighted_score_source and "cost_risk" not in weighted_score_source,
        "공식 현재입지 점수 함수 내부에서 비용축을 참조하지 않아야 향후 수정 때도 섞이지 않는다.",
    )
    add_validation(
        rows,
        "82-V05",
        "RTMS gold 직접값 금지 플래그",
        f"direct={sorted(rtms['direct_score_allowed'].astype(str).unique())}; proxy={sorted(rtms['proxy_score_allowed'].astype(str).unique())}",
        "direct_score_allowed all False; proxy_score_allowed all True",
        false_all(rtms["direct_score_allowed"]) and true_all(rtms["proxy_score_allowed"]),
        "RTMS는 매매가격 기반 비용 압력 프록시일 뿐 월세·권리금 직접값이 아니므로 직접점수 플래그는 false여야 한다.",
    )
    add_validation(
        rows,
        "82-V06",
        "RTMS fan-out 구조 보존",
        f"rows={len(rtms)}; quarters={rtms['기준_년분기_코드'].nunique()}; areas={rtms['상권_코드'].nunique()}; duplicate_keys={rtms.duplicated(['기준_년분기_코드', '상권_코드']).sum()}",
        "quarter×trade_area unique, quarter×district 값 단일",
        int(rtms.duplicated(["기준_년분기_코드", "상권_코드"]).sum()) == 0
        and int(rtms.groupby(["기준_년분기_코드", "자치구_코드"])["건물면적당_거래금액_중앙값_만원_per_m2"].nunique(dropna=False).max()) == 1,
        "같은 자치구·분기의 모든 상권이 같은 RTMS 값을 가져야 하며, 상권별 직접 월세처럼 변형되면 안 된다.",
    )
    add_validation(
        rows,
        "82-V07",
        "RTMS 금지문구와 프록시 설명 보존",
        {
            "forbidden_terms_ok": contains_all_text(rtms["forbidden_claim_ko"], ["임대료", "권리금", "직접값"]),
            "proxy_reason_has_rtms": rtms["proxy_reason_ko"].astype(str).str.contains("RTMS|프록시|월세|권리금", regex=True).any(),
        },
        "임대료/권리금 직접값 아님 + 프록시 사유",
        contains_all_text(rtms["forbidden_claim_ko"], ["임대료", "권리금", "직접값"])
        and rtms["proxy_reason_ko"].astype(str).str.contains("프록시").any(),
        "AI 리포트가 비용 점수를 월세·권리금 반영 수익성으로 오해하지 않게 금지문구가 gold에 있어야 한다.",
    )
    add_validation(
        rows,
        "82-V08",
        "R-ONE 후보 공식 승격 금지",
        f"direct={sorted(rone['direct_score_allowed'].astype(str).unique())}; engine={sorted(rone['engine_promotion_ready'].astype(str).unique())}; scopes={sorted(rone['mapping_scope'].astype(str).unique())[:5]}",
        "direct False, engine_promotion_ready False, 기준선/후보 범위 분리",
        false_all(rone["direct_score_allowed"])
        and false_all(rone["engine_promotion_ready"])
        and rone["mapping_scope"].astype(str).str.contains("seoul_baseline_reference|rone_level3_name_match_candidate", regex=True).any(),
        "R-ONE은 권역·상가유형 집계 기준선이므로 상권 직접 비용점수로 자동 승격하지 않는다.",
    )
    add_validation(
        rows,
        "82-V09",
        "중개업소 후보 공식 점수/백테스트 투입 금지",
        f"direct={sorted(broker['direct_score_allowed'].astype(str).unique())}; engine={sorted(broker['engine_score_allowed'].astype(str).unique())}; backtest={sorted(broker['valid_for_backtest'].astype(str).unique())}",
        "direct False, engine_score_allowed False, valid_for_backtest False",
        false_all(broker["direct_score_allowed"])
        and false_all(broker["engine_score_allowed"])
        and false_all(broker["valid_for_backtest"]),
        "중개업소 수는 2026년 스냅샷이고 월세·권리금 직접값도 아니므로 과거 라벨 백테스트와 공식 점수에 넣지 않는다.",
    )
    add_validation(
        rows,
        "82-V10",
        "백테스트 문구 금지 감사 유지",
        forbidden_audit["result"].value_counts(dropna=False).to_dict(),
        "FAIL 없음",
        not (forbidden_audit["result"] == "FAIL").any(),
        "점수 등급과 리포트 계약에서 창업 성공확률, 매출 보장, 월세/권리금 반영 수익성 표현을 금지해야 한다.",
    )
    add_validation(
        rows,
        "82-V11",
        "비용축은 성능지표에 별도 component로만 존재",
        component_metrics.loc[component_metrics["component"].eq("cost_risk"), ["component", "non_null_rows"]].to_dict("records"),
        "cost_risk component exists, current_location_score와 별도",
        int(component_metrics["component"].eq("cost_risk").sum()) == 1,
        "비용축 상관은 참고로 계산할 수 있지만 현재입지 공식 점수의 4축 가중합과는 분리되어야 한다.",
    )
    add_validation(
        rows,
        "82-V12",
        "payload 점수 구조에서 비용은 별도 필드",
        "cost_risk_score" in scores_payload_source and '"axis_scores"' in scores_payload_source,
        "cost_risk_score 별도, axis_scores는 CURRENT_AXES",
        "cost_risk_score" in scores_payload_source and "CURRENT_AXES" in scores_payload_source,
        "AI 리포트 입력 payload에서도 비용 리스크를 현재입지 축처럼 합산하지 않고 별도 점수로 넘겨야 한다.",
    )

    validation = pd.DataFrame(rows)
    pass_count = int((validation["result"] == "PASS").sum())
    fail_count = int((validation["result"] == "FAIL").sum())
    decision = "COST_PROXY_OFFICIAL_USE_CONTRACT_PASS_SEPARATE_PROXY_SCORE" if fail_count == 0 else "COST_PROXY_OFFICIAL_USE_CONTRACT_FAIL"

    summary = {
        "validation_number": 82,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "validation_version": VERSION,
        "rtms_rows": int(len(rtms)),
        "rtms_quarter_min": int(pd.to_numeric(rtms["기준_년분기_코드"], errors="coerce").min()),
        "rtms_quarter_max": int(pd.to_numeric(rtms["기준_년분기_코드"], errors="coerce").max()),
        "rtms_trade_area_count": int(rtms["상권_코드"].nunique()),
        "rone_rows": int(len(rone)),
        "broker_rows": int(len(broker)),
        "current_axes": current_axes,
        "cost_component_non_null_rows": int(component_metrics.loc[component_metrics["component"].eq("cost_risk"), "non_null_rows"].iloc[0]),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "decision": decision,
        "decision_reason_ko": "비용 리스크는 RTMS 자치구 매매가격 프록시로 별도 점수만 허용하고, R-ONE·중개업소는 evidence-only 또는 후보로 유지한다.",
        "next_step": "v1 알고리즘에서 비용 축은 별도 리스크 섹션으로 출력하고 현재입지 총점에는 합산하지 않는다.",
        "outputs": [str(OUT_VALIDATION.relative_to(ROOT)), str(OUT_SUMMARY.relative_to(ROOT)), str(OUT_DOC.relative_to(ROOT))],
    }

    validation.to_csv(OUT_VALIDATION, index=False, encoding="utf-8-sig")
    OUT_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    doc_rows = validation.to_dict("records")
    lines = [
        "# 82. 비용 리스크 프록시 공식 사용 계약 검증",
        "",
        f"생성일: {summary['generated_at']}",
        "",
        "## 목적",
        "",
        "RTMS, R-ONE, 중개업소 데이터가 비용 리스크 판단에 쓰일 수는 있지만 월세·권리금·수익성 직접 판단으로 오해되면 안 된다. "
        "이번 검증은 비용 데이터를 공식 알고리즘에 넣을 수 있는 범위를 코드와 gold 파일 기준으로 고정한다.",
        "",
        "## 결론",
        "",
        f"- decision: `{decision}`",
        f"- PASS: {pass_count}",
        f"- FAIL: {fail_count}",
        f"- 현재입지 공식축: `{', '.join(current_axes)}`",
        f"- RTMS gold rows: {summary['rtms_rows']:,}",
        f"- RTMS quarter range: `{summary['rtms_quarter_min']}~{summary['rtms_quarter_max']}`",
        f"- R-ONE candidate rows: {summary['rone_rows']:,}",
        f"- broker candidate rows: {summary['broker_rows']:,}",
        "",
        "## 판정",
        "",
        "- RTMS는 자치구 단위 상업·업무용 매매가격 기반 비용 압력 프록시다.",
        "- `cost_risk_score`는 별도 점수로만 출력한다.",
        "- 현재입지 총점은 `sales`, `competition`, `demand`, `accessibility` 네 축만 사용한다.",
        "- R-ONE은 권역/상가유형 기준선 또는 상권명 후보 evidence로만 유지한다.",
        "- 중개업소 후보는 스냅샷 보조 신호이므로 공식 점수와 과거 백테스트에 넣지 않는다.",
        "- 금지 표현: 월세 반영, 권리금 반영, 임대수익 확정, 개별 매장 수익성, 창업 성공확률.",
        "",
        "## 검증 결과",
        "",
        md_table(doc_rows, ["validation_id", "validation_name", "observed", "expected", "result", "reason_ko"]),
        "",
        "## 2보 전진 1보 후퇴",
        "",
        "1. 전진: RTMS gold가 비용 리스크 별도 점수로는 사용 가능함을 코드와 플래그로 확인했다.",
        "2. 전진: R-ONE과 중개업소 후보를 버리지 않고 evidence-only 후보 계층으로 보존했다.",
        "3. 후퇴: 비용 축은 현재입지 총점에 합산하지 않는다. 월세·권리금·수익성 판단으로도 표현하지 않는다.",
        "",
        "## 산출물",
        "",
        f"- `{OUT_VALIDATION.relative_to(ROOT)}`",
        f"- `{OUT_SUMMARY.relative_to(ROOT)}`",
        f"- `{OUT_DOC.relative_to(ROOT)}`",
        "",
    ]
    OUT_DOC.write_text("\n".join(lines), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
