from __future__ import annotations

import importlib.util
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RULE_DIR = ROOT / "datacorpus" / "_rule_validation"
DOC_DIR = ROOT / "research" / "rule_validation"
SAMPLE_DIR = RULE_DIR / "87_runtime_json_samples"
ENGINE_PATH = ROOT / "scripts" / "build_rule_based_location_scores.py"

OUT_CASES = RULE_DIR / "87_v1_runtime_json_smoke_cases.csv"
OUT_VALIDATION = RULE_DIR / "87_v1_runtime_json_contract_validation.csv"
OUT_SUMMARY = RULE_DIR / "87_v1_runtime_json_contract_summary.json"
OUT_DOC = DOC_DIR / "87_v1_runtime_json_contract_20260707.md"

VERSION = "v1_runtime_json_contract.v0.1-20260707"


def import_engine():
    spec = importlib.util.spec_from_file_location("build_rule_based_location_scores", ENGINE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"엔진 모듈을 불러올 수 없습니다: {ENGINE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def add_validation(rows: list[dict[str, Any]], validation_id: str, name: str, observed: Any, expected: Any, ok: bool, reason_ko: str) -> None:
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
    out = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join(["---"] * len(columns)) + " |",
    ]
    for row in rows:
        vals = [str(row.get(col, "")).replace("\n", " ") for col in columns]
        out.append("| " + " | ".join(vals) + " |")
    return "\n".join(out)


def choose_samples(scored: pd.DataFrame) -> list[tuple[str, pd.Series]]:
    pool = scored[scored["current_location_score"].notna()].copy()
    for display_col in ["상권_코드_명", "서비스_업종_코드_명"]:
        pool = pool[pool[display_col].notna() & pool[display_col].astype(str).str.strip().ne("")]
    pool = pool.sort_values("current_location_score").reset_index(drop=True)
    if len(pool) < 3:
        raise RuntimeError("runtime smoke에 필요한 표시명 포함 점수 행이 3개 미만입니다.")
    picks = [
        ("low", pool.iloc[max(0, int(len(pool) * 0.10))]),
        ("middle", pool.iloc[int(len(pool) * 0.50)]),
        ("high", pool.iloc[min(len(pool) - 1, int(len(pool) * 0.90))]),
    ]
    return picks


def main() -> int:
    RULE_DIR.mkdir(parents=True, exist_ok=True)
    DOC_DIR.mkdir(parents=True, exist_ok=True)
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)

    engine = import_engine()
    quarter = int(engine.latest_quarter())
    current_axes = list(engine.CURRENT_AXES)

    # 실제 엔진 계산을 한 번 수행한다. 단건별로 반복 계산하지 않고 같은 scored frame을 공유해
    # runtime smoke가 알고리즘 자체의 조인/점수 구조만 검증하도록 한다.
    base = engine.percentile_scores(engine.build_indicator_frame(quarter))
    scored = engine.score_frame(base)
    samples = choose_samples(scored)

    case_rows: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    for label, row in samples:
        args = SimpleNamespace(
            quarter=quarter,
            trade_area_code=str(row["상권_코드"]),
            trade_area_name=None,
            industry_code=str(row["서비스_업종_코드"]),
            industry_name=None,
            batch=False,
            emit_direction_matrix=False,
        )
        result = engine.build_result(base, scored, args, quarter)
        sample_path = SAMPLE_DIR / f"runtime_smoke_{label}_{args.trade_area_code}_{args.industry_code}_{quarter}.json"
        sample_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        scores = result["scores"]
        score_result = result["score_result"]
        axis_scores = scores.get("axis_scores", {})
        candidate_signals = score_result.get("candidate_signals", {})
        components = score_result.get("components", [])
        case_rows.append(
            {
                "case_label": label,
                "sample_json": str(sample_path.relative_to(ROOT)),
                "quarter": quarter,
                "trade_area_code": result["matched_target"]["trade_area_code"],
                "trade_area_name": result["matched_target"]["trade_area_name"],
                "industry_code": result["matched_target"]["industry_code"],
                "industry_name": result["matched_target"]["industry_name"],
                "current_location_score": scores.get("current_location_score"),
                "total_score": score_result.get("total_score"),
                "cost_risk_score": scores.get("cost_risk_score"),
                "growth_rebound_candidate_score": scores.get("growth_rebound_candidate_score"),
                "transit_candidate_status": scores.get("transit_accessibility_candidate_status"),
                "axis_score_keys": ";".join(sorted(axis_scores.keys())),
                "component_keys": ";".join(str(c.get("key")) for c in components),
                "candidate_signal_keys": ";".join(sorted(candidate_signals.keys())),
                "warning_count": len(result.get("warnings", [])),
                "forbidden_claim_count": len(result.get("evidence_pack", {}).get("forbidden_claims", [])),
            }
        )
        results.append(result)

    validations: list[dict[str, Any]] = []
    add_validation(
        validations,
        "87-V01",
        "샘플 JSON 3건 생성",
        len(results),
        3,
        len(results) == 3,
        "단건 1개가 아니라 저/중/고 점수대에서 최소 3건을 확인한다.",
    )
    add_validation(
        validations,
        "87-V02",
        "total_score와 current_location_score 일치",
        [(r["score_result"].get("total_score"), r["scores"].get("current_location_score")) for r in results],
        "all equal",
        all(r["score_result"].get("total_score") == r["scores"].get("current_location_score") for r in results),
        "공식 총점은 현재입지 점수여야 하며 별도 점수나 후보 점수로 대체되면 안 된다.",
    )
    add_validation(
        validations,
        "87-V03",
        "axis_scores는 공식 4축만 포함",
        [sorted(r["scores"].get("axis_scores", {}).keys()) for r in results],
        sorted(current_axes),
        all(set(r["scores"].get("axis_scores", {}).keys()) == set(current_axes) for r in results),
        "비용·성장·후보 evidence가 공식 축 점수에 섞이지 않아야 한다.",
    )
    add_validation(
        validations,
        "87-V04",
        "cost_risk_score는 별도 필드이고 axis_scores에는 없음",
        [
            {
                "has_cost": "cost_risk_score" in r["scores"],
                "cost_in_axis": "cost_risk" in r["scores"].get("axis_scores", {}),
            }
            for r in results
        ],
        "has_cost True, cost_in_axis False",
        all("cost_risk_score" in r["scores"] and "cost_risk" not in r["scores"].get("axis_scores", {}) for r in results),
        "비용 리스크는 별도 프록시 점수로만 출력한다.",
    )
    add_validation(
        validations,
        "87-V05",
        "성장반등·교통 후보는 candidate_signals에 있음",
        [sorted(r["score_result"].get("candidate_signals", {}).keys()) for r in results],
        "growth_rebound_candidate, transit_accessibility_250m_candidate",
        all(
            {"growth_rebound_candidate", "transit_accessibility_250m_candidate"}.issubset(
                set(r["score_result"].get("candidate_signals", {}).keys())
            )
            for r in results
        ),
        "후보 신호는 공식 total_score가 아니라 candidate_signals로 분리한다.",
    )
    add_validation(
        validations,
        "87-V06",
        "report components는 공식 4축 중심",
        [case["component_keys"] for case in case_rows],
        "sales/competition/demand/accessibility 포함, cost_risk 제외",
        all(
            set(current_axes).issubset({str(c.get("key")) for c in r["score_result"].get("components", [])})
            and "cost_risk" not in {str(c.get("key")) for c in r["score_result"].get("components", [])}
            for r in results
        ),
        "AI 리포트 components가 비용·성장 후보를 공식 축처럼 보여주면 안 된다.",
    )
    add_validation(
        validations,
        "87-V07",
        "warnings와 forbidden_claims가 payload에 포함",
        [
            {
                "warnings": len(r.get("warnings", [])),
                "forbidden_claims": len(r.get("evidence_pack", {}).get("forbidden_claims", [])),
            }
            for r in results
        ],
        "warnings>0 and forbidden_claims>=5",
        all(len(r.get("warnings", [])) > 0 and len(r.get("evidence_pack", {}).get("forbidden_claims", [])) >= 5 for r in results),
        "LLM/UI가 과장 표현을 하지 않도록 금지문구 계약을 런타임 payload에 포함해야 한다.",
    )
    add_validation(
        validations,
        "87-V08",
        "text_model_payload의 must_not_do 존재",
        [len(r.get("text_model_payload", {}).get("must_not_do", [])) for r in results],
        "all > 0",
        all(len(r.get("text_model_payload", {}).get("must_not_do", [])) > 0 for r in results),
        "AI 상세리포트는 숫자를 새로 만들거나 성공확률처럼 말하지 못하게 must_not_do 계약을 받아야 한다.",
    )
    add_validation(
        validations,
        "87-V09",
        "sample JSON 파일 실제 저장",
        [case["sample_json"] for case in case_rows],
        "all files exist",
        all((ROOT / case["sample_json"]).exists() for case in case_rows),
        "런타임 회귀는 나중에 같은 JSON을 열어 직접 비교할 수 있게 파일로 남겨야 한다.",
    )
    add_validation(
        validations,
        "87-V10",
        "샘플 표시명 누락 없음",
        [
            {
                "case_label": case["case_label"],
                "trade_area_name": case["trade_area_name"],
                "industry_name": case["industry_name"],
            }
            for case in case_rows
        ],
        "trade_area_name and industry_name not blank",
        all(str(case["trade_area_name"]).strip() for case in case_rows)
        and all(str(case["industry_name"]).strip() for case in case_rows),
        "사용자 화면과 AI 리포트에는 코드만이 아니라 사람이 읽는 상권명과 업종명이 같이 있어야 한다.",
    )

    cases_df = pd.DataFrame(case_rows)
    validation_df = pd.DataFrame(validations)
    pass_count = int((validation_df["result"] == "PASS").sum())
    fail_count = int((validation_df["result"] == "FAIL").sum())
    decision = "V1_RUNTIME_JSON_CONTRACT_PASS" if fail_count == 0 else "V1_RUNTIME_JSON_CONTRACT_FAIL"

    cases_df.to_csv(OUT_CASES, index=False, encoding="utf-8-sig")
    validation_df.to_csv(OUT_VALIDATION, index=False, encoding="utf-8-sig")
    summary = {
        "validation_number": 87,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "validation_version": VERSION,
        "quarter": quarter,
        "sample_count": len(results),
        "scored_rows": int(len(scored)),
        "current_axes": current_axes,
        "pass_count": pass_count,
        "fail_count": fail_count,
        "decision": decision,
        "decision_reason_ko": "실제 단건 JSON 3건에서 공식 총점, 별도 비용 점수, 성장/교통 후보, 금지문구 payload가 분리되어 출력된다.",
        "next_step": "백데이터 재검증 또는 입력 resolver 운영 연결 검증으로 이동한다.",
        "outputs": [
            str(OUT_CASES.relative_to(ROOT)),
            str(OUT_VALIDATION.relative_to(ROOT)),
            str(OUT_SUMMARY.relative_to(ROOT)),
            str(OUT_DOC.relative_to(ROOT)),
            str(SAMPLE_DIR.relative_to(ROOT)),
        ],
    }
    OUT_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    lines = [
        "# 87. v1 단건 JSON 런타임 계약 스모크",
        "",
        f"생성일: {summary['generated_at']}",
        "",
        "## 목적",
        "",
        "84번 payload 계약과 86번 엔진 정적 감사가 실제 JSON 출력에서도 유지되는지 확인한다. "
        "샘플 3건을 저/중/고 점수대에서 뽑아 공식 총점, 별도 점수, 후보 신호, 금지문구 payload 분리를 검사했다.",
        "",
        "## 결론",
        "",
        f"- decision: `{decision}`",
        f"- PASS: {pass_count}",
        f"- FAIL: {fail_count}",
        f"- quarter: `{quarter}`",
        f"- scored rows: {len(scored):,}",
        "",
        "## 샘플",
        "",
        md_table(cases_df.to_dict("records"), ["case_label", "sample_json", "trade_area_name", "industry_name", "current_location_score", "cost_risk_score", "axis_score_keys", "candidate_signal_keys"]),
        "",
        "## 검증 결과",
        "",
        md_table(validation_df.to_dict("records"), ["validation_id", "validation_name", "observed", "expected", "result", "reason_ko"]),
        "",
        "## 2보 전진 1보 후퇴",
        "",
        "1. 전진: 실제 런타임에서 샘플 JSON 3건을 생성했다.",
        "2. 전진: 공식 총점과 별도 비용 점수, 성장/교통 후보 신호가 분리되어 있음을 확인했다.",
        "3. 후퇴: 이 스모크는 단건 JSON 구조 검증이며, 전체 백데이터 성능 재검증은 다음 단계에서 별도로 수행한다.",
        "",
        "## 산출물",
        "",
        f"- `{OUT_CASES.relative_to(ROOT)}`",
        f"- `{OUT_VALIDATION.relative_to(ROOT)}`",
        f"- `{OUT_SUMMARY.relative_to(ROOT)}`",
        f"- `{OUT_DOC.relative_to(ROOT)}`",
        f"- `{SAMPLE_DIR.relative_to(ROOT)}`",
        "",
    ]
    OUT_DOC.write_text("\n".join(lines), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
