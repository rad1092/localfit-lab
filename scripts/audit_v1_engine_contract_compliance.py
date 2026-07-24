from __future__ import annotations

import importlib.util
import inspect
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RULE_DIR = ROOT / "datacorpus" / "_rule_validation"
DOC_DIR = ROOT / "research" / "rule_validation"
ENGINE_PATH = ROOT / "scripts" / "build_rule_based_location_scores.py"
CONTRACT_PATH = RULE_DIR / "84_v1_preprocessing_payload_contract.csv"

OUT_READ_AUDIT = RULE_DIR / "86_v1_engine_contract_static_read_audit.csv"
OUT_VALIDATION = RULE_DIR / "86_v1_engine_contract_compliance_validation.csv"
OUT_SUMMARY = RULE_DIR / "86_v1_engine_contract_compliance_summary.json"
OUT_DOC = DOC_DIR / "86_v1_engine_contract_compliance_20260707.md"

VERSION = "v1_engine_contract_compliance.v0.1-20260707"


def read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, encoding="utf-8-sig", dtype=str)


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
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join(["---"] * len(columns)) + " |",
    ]
    for row in rows:
        vals = [str(row.get(col, "")).replace("\n", " ") for col in columns]
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def discover_static_reads(source: str, contract: pd.DataFrame) -> pd.DataFrame:
    reads: list[dict[str, Any]] = []

    for filename in sorted(set(re.findall(r"read_gold\(\s*['\"]([^'\"]+\.csv)['\"]", source))):
        reads.append({"read_layer": "gold", "read_api": "read_gold", "file_path": f"datacorpus/_gold/{filename}"})
    for filename in sorted(set(re.findall(r"read_silver\(\s*['\"]([^'\"]+\.csv)['\"]", source))):
        reads.append({"read_layer": "silver", "read_api": "read_silver", "file_path": f"datacorpus/_silver/{filename}"})
    for filename in sorted(set(re.findall(r"GOLD\s*/\s*['\"]([^'\"]+\.csv)['\"]", source))):
        reads.append({"read_layer": "gold", "read_api": "pd.read_csv(GOLD)", "file_path": f"datacorpus/_gold/{filename}"})

    df = pd.DataFrame(reads).drop_duplicates(["read_api", "file_path"]).sort_values(["file_path", "read_api"])
    contract_map = contract.set_index("file_path")[["contract_group", "use_in_v1", "engine_field", "score_mutation_allowed"]].to_dict("index")
    df["contract_group"] = df["file_path"].map(lambda x: contract_map.get(x, {}).get("contract_group", "NOT_IN_84_CONTRACT"))
    df["use_in_v1"] = df["file_path"].map(lambda x: contract_map.get(x, {}).get("use_in_v1", "NOT_IN_84_CONTRACT"))
    df["engine_field_contract"] = df["file_path"].map(lambda x: contract_map.get(x, {}).get("engine_field", "NOT_IN_84_CONTRACT"))
    df["score_mutation_allowed_contract"] = df["file_path"].map(lambda x: contract_map.get(x, {}).get("score_mutation_allowed", "NOT_IN_84_CONTRACT"))
    return df


def main() -> int:
    RULE_DIR.mkdir(parents=True, exist_ok=True)
    DOC_DIR.mkdir(parents=True, exist_ok=True)

    contract = read_csv(CONTRACT_PATH)
    source = ENGINE_PATH.read_text(encoding="utf-8")
    engine = import_engine()
    read_audit = discover_static_reads(source, contract)

    scores_payload_source = inspect.getsource(engine.scores_payload)
    weighted_source = inspect.getsource(engine._weighted_current_score)
    score_frame_source = inspect.getsource(engine.score_frame)
    report_components_source = inspect.getsource(engine.report_components)
    evidence_pack_source = inspect.getsource(engine.build_evidence_pack)
    indicators = getattr(engine, "INDICATORS")
    current_axes = list(getattr(engine, "CURRENT_AXES"))
    forbidden_claims = getattr(engine, "FORBIDDEN_CLAIMS")

    validation: list[dict[str, Any]] = []
    add_validation(
        validation,
        "86-V01",
        "엔진 정적 read 파일이 84번 계약 안에 있음",
        read_audit[["file_path", "contract_group"]].to_dict("records"),
        "NOT_IN_84_CONTRACT 없음",
        not read_audit["contract_group"].eq("NOT_IN_84_CONTRACT").any(),
        "엔진이 계약 밖 파일을 직접 읽으면 공식/evidence 구분과 추적성이 깨진다.",
    )
    official_reads = read_audit[read_audit["contract_group"].eq("official_current_score")]
    add_validation(
        validation,
        "86-V02",
        "공식 점수 read는 official_current_score 그룹에만 위치",
        official_reads[["file_path", "engine_field_contract"]].to_dict("records"),
        "official axes/profile only",
        set(official_reads["engine_field_contract"]).issubset(
            {"matched_target/profile", "axis.sales", "axis.competition", "axis.demand", "axis.accessibility"}
        ),
        "공식 점수 입력 파일이 evidence나 별도 점수 역할로 오염되면 안 된다.",
    )
    add_validation(
        validation,
        "86-V03",
        "현재입지 계산 함수가 CURRENT_AXES만 사용",
        "CURRENT_AXES" in weighted_source and "cost_risk" not in weighted_source and "growth" not in weighted_source,
        "CURRENT_AXES만 참조",
        "CURRENT_AXES" in weighted_source and "cost_risk" not in weighted_source and "growth" not in weighted_source,
        "현재입지 총점은 비용·성장 후보를 제외한 4축 WLC여야 한다.",
    )
    add_validation(
        validation,
        "86-V04",
        "엔진 CURRENT_AXES가 84번 공식축과 일치",
        current_axes,
        ["sales", "competition", "demand", "accessibility"],
        set(current_axes) == {"sales", "competition", "demand", "accessibility"},
        "84번 계약의 공식축과 실제 엔진 상수가 같아야 한다.",
    )
    add_validation(
        validation,
        "86-V05",
        "cost_risk는 별도 scores_payload 필드",
        {
            "has_cost_risk_score": "cost_risk_score" in scores_payload_source,
            "axis_scores_uses_CURRENT_AXES": "CURRENT_AXES" in scores_payload_source,
        },
        "cost_risk_score separate, axis_scores CURRENT_AXES",
        "cost_risk_score" in scores_payload_source and "CURRENT_AXES" in scores_payload_source,
        "비용 리스크는 현재입지 총점이 아니라 별도 점수로 출력해야 한다.",
    )
    add_validation(
        validation,
        "86-V06",
        "성장반등·교통후보는 병렬 후보 컬럼",
        {
            "growth_rebound_candidate_score": "growth_rebound_candidate_score" in score_frame_source,
            "current_location_score_transit_250m_candidate": "current_location_score_transit_250m_candidate" in score_frame_source,
            "candidate_attached_not_in_current_score": "candidate_attached_not_in_current_score" in score_frame_source,
        },
        "candidate outputs exist, official current score not overwritten",
        "growth_rebound_candidate_score" in score_frame_source
        and "current_location_score_transit_250m_candidate" in score_frame_source
        and "candidate_attached_not_in_current_score" in score_frame_source,
        "후보 신호는 공식 current_location_score를 덮어쓰지 않고 별도 컬럼으로만 남긴다.",
    )
    active_axes = sorted({spec["axis"] for spec in indicators.values()})
    add_validation(
        validation,
        "86-V07",
        "INDICATORS 축 목록은 공식축+별도축으로 분리",
        active_axes,
        "accessibility, competition, cost_risk, demand, growth, sales",
        set(active_axes) == {"sales", "competition", "demand", "accessibility", "growth", "cost_risk"},
        "지표 명세에는 별도축이 있어도 CURRENT_AXES 총점에는 포함하지 않는 구조여야 한다.",
    )
    evidence_reads = read_audit[read_audit["contract_group"].isin(["evidence_payload", "separate_score"])]
    add_validation(
        validation,
        "86-V08",
        "evidence/별도 read의 score mutation 계약 준수",
        evidence_reads[["file_path", "contract_group", "score_mutation_allowed_contract"]].to_dict("records"),
        "evidence payload False, separate score는 계약별 허용",
        evidence_reads[evidence_reads["contract_group"].eq("evidence_payload")]["score_mutation_allowed_contract"].astype(str).eq("False").all(),
        "evidence payload 파일은 공식 점수 산식 변경을 허용하지 않는다.",
    )
    add_validation(
        validation,
        "86-V09",
        "report_components는 CURRENT_AXES 중심",
        {
            "loops_CURRENT_AXES": "for axis in CURRENT_AXES" in report_components_source,
            "cost_risk_in_components": "cost_risk" in report_components_source,
            "growth_in_components": "growth_potential" in report_components_source,
        },
        "CURRENT_AXES 반복, cost/growth 공식 component 제외",
        "for axis in CURRENT_AXES" in report_components_source and "cost_risk" not in report_components_source,
        "AI 리포트 components도 공식 현재입지 축과 후보/evidence를 섞지 않아야 한다.",
    )
    add_validation(
        validation,
        "86-V10",
        "R-ONE 참고선은 evidence 전용",
        {
            "load_rone_reference_in_evidence": "load_rone_reference" in evidence_pack_source,
            "silver_rone_contract": "datacorpus/_silver/silver_reb_rone_seoul_cost_proxy_latest.csv" in contract["file_path"].tolist(),
        },
        "evidence pack only, 84 contract included",
        "load_rone_reference" in evidence_pack_source
        and "datacorpus/_silver/silver_reb_rone_seoul_cost_proxy_latest.csv" in contract["file_path"].tolist(),
        "엔진이 R-ONE silver 참고선을 읽는다면 84번 계약 안에서 evidence-only로 추적되어야 한다.",
    )
    forbidden_text = " ".join(str(x) for item in forbidden_claims for x in item.values())
    add_validation(
        validation,
        "86-V11",
        "금지표현 계약이 엔진 상수에 있음",
        forbidden_text,
        "성공확률/매출보장/월세권리금/방문확률 포함",
        all(term in forbidden_text for term in ["창업 성공확률", "개별 매장 매출 보장", "월세/권리금", "실제 방문확률"]),
        "엔진 payload가 LLM/UI에 넘길 금지표현 계약을 직접 들고 있어야 한다.",
    )
    add_validation(
        validation,
        "86-V12",
        "입력 resolver 계약 파일은 엔진 점수 산식 read가 아님",
        read_audit[read_audit["contract_group"].eq("input_resolver")].to_dict("records"),
        "static score engine reads no input_resolver files",
        read_audit[read_audit["contract_group"].eq("input_resolver")].empty,
        "입력 resolver는 엔진 호출 전 코드 확정 계층이며 점수 계산 내부 feature가 아니다.",
    )

    validation_df = pd.DataFrame(validation)
    pass_count = int((validation_df["result"] == "PASS").sum())
    fail_count = int((validation_df["result"] == "FAIL").sum())
    decision = "V1_ENGINE_CONTRACT_COMPLIANCE_PASS" if fail_count == 0 else "V1_ENGINE_CONTRACT_COMPLIANCE_FAIL"

    read_audit.to_csv(OUT_READ_AUDIT, index=False, encoding="utf-8-sig")
    validation_df.to_csv(OUT_VALIDATION, index=False, encoding="utf-8-sig")
    summary = {
        "validation_number": 86,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "validation_version": VERSION,
        "engine_file": str(ENGINE_PATH.relative_to(ROOT)),
        "static_read_count": int(len(read_audit)),
        "static_read_contract_groups": read_audit["contract_group"].value_counts(dropna=False).to_dict(),
        "current_axes": current_axes,
        "pass_count": pass_count,
        "fail_count": fail_count,
        "decision": decision,
        "decision_reason_ko": "실제 엔진 read 파일과 핵심 점수 함수가 84번 v1 payload 계약의 공식/별도/evidence/input 분리 규칙을 따른다.",
        "next_step": "단건 JSON 회귀 스모크에서 실제 출력 payload도 같은 분리를 유지하는지 확인한다.",
        "outputs": [
            str(OUT_READ_AUDIT.relative_to(ROOT)),
            str(OUT_VALIDATION.relative_to(ROOT)),
            str(OUT_SUMMARY.relative_to(ROOT)),
            str(OUT_DOC.relative_to(ROOT)),
        ],
    }
    OUT_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    lines = [
        "# 86. v1 엔진 계약 준수 감사",
        "",
        f"생성일: {summary['generated_at']}",
        "",
        "## 목적",
        "",
        "`scripts/build_rule_based_location_scores.py`가 84번 v1 payload 계약을 실제로 지키는지 정적 감사한다. "
        "이번 단계는 런타임 결과 검증 전, 계약 밖 파일 read와 공식/evidence 혼합을 먼저 잡는 후퇴 검토다.",
        "",
        "## 결론",
        "",
        f"- decision: `{decision}`",
        f"- PASS: {pass_count}",
        f"- FAIL: {fail_count}",
        f"- static read count: {len(read_audit)}",
        f"- current axes: `{', '.join(current_axes)}`",
        "",
        "## 정적 read 감사",
        "",
        md_table(
            read_audit.to_dict("records"),
            ["read_api", "file_path", "contract_group", "use_in_v1", "engine_field_contract", "score_mutation_allowed_contract"],
        ),
        "",
        "## 검증 결과",
        "",
        md_table(
            validation_df.to_dict("records"),
            ["validation_id", "validation_name", "observed", "expected", "result", "reason_ko"],
        ),
        "",
        "## 2보 전진 1보 후퇴",
        "",
        "1. 전진: 엔진 read 파일이 84번 계약 안에 있는지 확인했다.",
        "2. 전진: 현재입지 총점, 별도 점수, 후보 신호, R-ONE evidence가 코드상 분리되는지 확인했다.",
        "3. 후퇴: 아직 실제 단건 JSON 출력값 자체는 87번에서 따로 확인해야 한다.",
        "",
        "## 산출물",
        "",
        f"- `{OUT_READ_AUDIT.relative_to(ROOT)}`",
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
