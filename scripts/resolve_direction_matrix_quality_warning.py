from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
RULE_VALIDATION = ROOT / "datacorpus" / "_rule_validation"
RESEARCH_RULE_VALIDATION = ROOT / "research" / "rule_validation"
SCORE_BACKTEST = ROOT / "datacorpus" / "_score_backtest_gold"

DIRECTION_MATRIX = RESEARCH_RULE_VALIDATION / "05_direction_normalization_matrix.csv"
BACKTEST_SCRIPT = SCRIPTS / "backtest_gold_rule_engine_scores.py"
GOLD_DIRECTION_AUDIT = SCORE_BACKTEST / "gold_engine_direction_effect_audit.csv"

REGISTRY_OUT = RULE_VALIDATION / "79_direction_matrix_quality_warning_registry.csv"
VALIDATION_OUT = RULE_VALIDATION / "79_direction_matrix_quality_warning_validation.csv"
CORRECTED_AUDIT_OUT = RULE_VALIDATION / "79_direction_matrix_corrected_direction_effect_audit.csv"
SUMMARY_OUT = RULE_VALIDATION / "79_direction_matrix_quality_warning_summary.json"
MD_OUT = RESEARCH_RULE_VALIDATION / "79_direction_matrix_quality_warning_resolution_20260707.md"

sys.path.insert(0, str(SCRIPTS))
import build_rule_based_location_scores as engine  # noqa: E402


def result(ok: bool) -> str:
    return "PASS" if ok else "FAIL"


def col(df: pd.DataFrame, preferred: str, fallback_index: int) -> str:
    return preferred if preferred in df.columns else df.columns[fallback_index]


def ticket_evidence_metrics() -> set[str]:
    metrics: set[str] = set()
    source = getattr(engine, "TICKET_EVIDENCE_ONLY", {})
    if isinstance(source, dict) and "metric" in source:
        metrics.add(str(source["metric"]))
    for key, value in source.items():
        metrics.add(str(key))
        if isinstance(value, dict):
            metrics.add(str(value.get("metric", key)))
    return metrics


def build_registry(direction: pd.DataFrame) -> pd.DataFrame:
    metric_col = col(direction, "지표", 0)
    axis_col = col(direction, "축", 1)
    direction_col = col(direction, "방향", 2)
    grain_col = col(direction, "grain", 3)
    group_col = col(direction, "비교군", 4)
    evidence_col = col(direction, "근거ID", 5)
    reason_col = col(direction, "채택이유", 6)
    review_col = "재검토_후보" if "재검토_후보" in direction.columns else None

    active_metrics = set(engine.INDICATORS)
    evidence_metrics = ticket_evidence_metrics()

    rows = []
    for _, raw in direction.iterrows():
        metric = str(raw[metric_col])
        in_engine = metric in active_metrics
        evidence_only = metric in evidence_metrics or "evidence-only" in str(raw[direction_col])
        rows.append(
            {
                "metric": metric,
                "registry_status": (
                    "active_score_indicator"
                    if in_engine
                    else "evidence_only_direction_row"
                    if evidence_only
                    else "orphan_direction_row"
                ),
                "score_contribution_status": "included_in_score" if in_engine else "excluded_from_score",
                "axis": raw[axis_col],
                "direction": raw[direction_col],
                "grain": raw[grain_col],
                "comparison_group": raw[group_col],
                "evidence_id": raw[evidence_col],
                "reason_ko": raw[reason_col],
                "review_note": raw[review_col] if review_col else "",
                "exists_in_engine_indicators": in_engine,
                "exists_in_ticket_evidence_only": metric in evidence_metrics,
            }
        )
    return pd.DataFrame(rows)


def build_corrected_audit(registry: pd.DataFrame) -> pd.DataFrame:
    active = registry[registry["registry_status"] == "active_score_indicator"]
    evidence_only = registry[registry["registry_status"] == "evidence_only_direction_row"]
    cost_count = int((active["direction"] == "cost").sum())

    rows = [
        {
            "audit_item": "방향행렬_지표수",
            "observed": len(active),
            "expected": len(engine.INDICATORS),
            "result": result(len(active) == len(engine.INDICATORS)),
            "reason_ko": "점수에 들어가는 active 지표만 방향행렬 지표 수 감사에 포함한다.",
        },
        {
            "audit_item": "evidence_only_방향행렬_분리",
            "observed": len(evidence_only),
            "expected": "객단가 evidence-only 1행",
            "result": result(set(evidence_only["metric"]) == {"객단가"}),
            "reason_ko": "객단가는 v2.4에서 점수 산식에서 제외됐지만 리포트 설명 근거로 보존한다.",
        },
        {
            "audit_item": "비용형_방향_존재",
            "observed": cost_count,
            "expected": "0보다 큼",
            "result": result(cost_count > 0),
            "reason_ko": "비용형 지표는 100-백분위 반전 규칙을 적용해야 한다.",
        },
    ]

    if GOLD_DIRECTION_AUDIT.exists():
        previous = pd.read_csv(GOLD_DIRECTION_AUDIT, encoding="utf-8-sig")
        sensitivity = previous[previous["audit_item"].astype(str).str.contains("민감도", na=False)]
        for _, row in sensitivity.iterrows():
            rows.append(row.to_dict())

    return pd.DataFrame(rows)


def markdown_table(df: pd.DataFrame, columns: list[str]) -> str:
    table = df[columns].copy()
    headers = columns
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for _, row in table.iterrows():
        values = []
        for column in headers:
            text = str(row[column]).replace("\n", " ").replace("|", "\\|")
            values.append(text)
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def validate(direction: pd.DataFrame, registry: pd.DataFrame, corrected: pd.DataFrame) -> pd.DataFrame:
    active = registry[registry["registry_status"] == "active_score_indicator"]
    evidence_only = registry[registry["registry_status"] == "evidence_only_direction_row"]
    orphan = registry[registry["registry_status"] == "orphan_direction_row"]
    active_metrics = set(engine.INDICATORS)
    matrix_active_metrics = set(active["metric"])
    ticket_metrics = ticket_evidence_metrics()
    script_text = BACKTEST_SCRIPT.read_text(encoding="utf-8")

    required_cols = ["axis", "direction", "grain", "comparison_group", "evidence_id", "reason_ko"]
    active_required_ok = bool(active[required_cols].notna().all().all()) and bool(
        (active[required_cols].astype(str).apply(lambda s: s.str.strip()).ne("")).all().all()
    )
    active_direction_ok = set(active["direction"].astype(str)).issubset({"benefit", "cost"})

    validations = [
        {
            "check_id": "79-V01",
            "check_name": "방향행렬 파일 존재",
            "observed": f"rows={len(direction)}",
            "expected": "방향행렬 CSV 존재 및 1행 이상",
            "result": result(DIRECTION_MATRIX.exists() and len(direction) > 0),
            "reason_ko": "점수 전처리 전에 각 지표의 방향·grain·근거ID 계약이 있어야 한다.",
        },
        {
            "check_id": "79-V02",
            "check_name": "active 지표 수 일치",
            "observed": f"active_matrix={len(active)}, engine={len(engine.INDICATORS)}",
            "expected": "active matrix rows == engine.INDICATORS",
            "result": result(len(active) == len(engine.INDICATORS)),
            "reason_ko": "점수 산식에 들어가는 지표만 수량 감사에 포함해야 한다.",
        },
        {
            "check_id": "79-V03",
            "check_name": "active 지표 누락 없음",
            "observed": ", ".join(sorted(active_metrics - matrix_active_metrics)) or "none",
            "expected": "none",
            "result": result(not (active_metrics - matrix_active_metrics)),
            "reason_ko": "엔진 active 지표가 방향행렬에 없으면 백분위 방향 반전 근거가 빈다.",
        },
        {
            "check_id": "79-V04",
            "check_name": "비활성 행은 evidence-only로만 분리",
            "observed": ", ".join(orphan["metric"].astype(str)) or "none",
            "expected": "orphan 없음",
            "result": result(orphan.empty),
            "reason_ko": "엔진에도 없고 evidence-only도 아닌 지표가 방향행렬에 남으면 감사 수량이 흔들린다.",
        },
        {
            "check_id": "79-V05",
            "check_name": "객단가 active 지표 제외",
            "observed": f"in_engine={'객단가' in active_metrics}, evidence_only={'객단가' in set(evidence_only['metric'])}",
            "expected": "in_engine=False, evidence_only=True",
            "result": result("객단가" not in active_metrics and "객단가" in set(evidence_only["metric"])),
            "reason_ko": "48~51번 검증 결론상 객단가는 점수 직접 가점이 아니라 소비 단가 참고값이다.",
        },
        {
            "check_id": "79-V06",
            "check_name": "active 방향값 유효",
            "observed": ", ".join(sorted(active["direction"].astype(str).unique())),
            "expected": "benefit 또는 cost",
            "result": result(active_direction_ok),
            "reason_ko": "benefit/cost가 아니면 백분위 정규화와 100-백분위 반전 규칙을 적용할 수 없다.",
        },
        {
            "check_id": "79-V07",
            "check_name": "active 필수 메타 완비",
            "observed": f"required_cols={','.join(required_cols)}",
            "expected": "비어있는 active 메타 없음",
            "result": result(active_required_ok),
            "reason_ko": "축·방향·grain·비교군·근거ID·채택이유가 있어야 점수 설명이 가능하다.",
        },
        {
            "check_id": "79-V08",
            "check_name": "객단가 evidence-only 원장 존재",
            "observed": ", ".join(sorted(ticket_metrics)),
            "expected": "객단가 포함",
            "result": result("객단가" in ticket_metrics),
            "reason_ko": "방향행렬에서 제외하지 않고 설명용으로 남기려면 엔진 쪽 evidence-only 원장도 있어야 한다.",
        },
        {
            "check_id": "79-V09",
            "check_name": "백테스트 감사 로직 active/evidence 분리",
            "observed": "active_direction/evidence_only_direction",
            "expected": "source contains both terms",
            "result": result("active_direction" in script_text and "evidence_only_direction" in script_text),
            "reason_ko": "다음 백테스트 재실행 때 같은 지표 수 경고가 반복되지 않아야 한다.",
        },
        {
            "check_id": "79-V10",
            "check_name": "수정 감사 결과 PASS",
            "observed": ", ".join(corrected["result"].astype(str).unique()),
            "expected": "FAIL 없음",
            "result": result(not corrected["result"].astype(str).eq("FAIL").any()),
            "reason_ko": "78번에서 이월된 방향행렬 지표 수 FAIL이 active/evidence 분리 후 해소되는지 확인한다.",
        },
    ]
    return pd.DataFrame(validations)


def write_markdown(summary: dict, registry: pd.DataFrame, validation: pd.DataFrame) -> None:
    active = registry[registry["registry_status"] == "active_score_indicator"]
    evidence_only = registry[registry["registry_status"] == "evidence_only_direction_row"]
    lines = [
        "# 79. 방향행렬 품질 경고 해소 기록",
        "",
        "## 확인 목적",
        "",
        "78번 조인 안정성 검증에서 기존 `gold_engine_direction_effect_audit.csv`의 `방향행렬_지표수`가 FAIL로 이월됐다. "
        "전처리 전에 이 경고가 실제 데이터 부족인지, 아니면 active 점수 지표와 evidence-only 행을 섞어 센 감사 오류인지 확인했다.",
        "",
        "## 결론",
        "",
        "- 엔진 active 점수 지표는 19개다.",
        "- 방향행렬 CSV는 20행이지만, 추가 1행은 `객단가` evidence-only 행이다.",
        "- `객단가`는 48~51번 검증 결론대로 점수 산식에 넣지 않는다.",
        "- 전처리에서는 `객단가` 값을 버리지 않고 소비 단가 참고값으로 보존한다.",
        "- 백테스트 감사 로직은 active 지표 수와 evidence-only 행을 분리하도록 수정했다.",
        "",
        "## 수량 확인",
        "",
        "| 항목 | 값 |",
        "|---|---:|",
        f"| 방향행렬 전체 행 | {summary['direction_matrix_rows']} |",
        f"| 엔진 active 지표 | {summary['active_indicator_count']} |",
        f"| 방향행렬 내 active 행 | {summary['active_direction_rows']} |",
        f"| evidence-only 행 | {summary['evidence_only_rows']} |",
        f"| orphan 행 | {summary['orphan_rows']} |",
        "",
        "## evidence-only 행",
        "",
        markdown_table(evidence_only, ["metric", "axis", "direction", "grain", "comparison_group", "evidence_id", "review_note"]),
        "",
        "## active 지표 목록",
        "",
        markdown_table(active, ["metric", "axis", "direction", "grain", "comparison_group", "evidence_id"]),
        "",
        "## 검증 결과",
        "",
        markdown_table(validation, ["check_id", "check_name", "observed", "expected", "result", "reason_ko"]),
        "",
        "## 2보 전진",
        "",
        "1. 전처리 전에 방향행렬의 지표 수 경고가 데이터 누락이 아니라 `객단가` evidence-only 행 때문임을 분리했다.",
        "2. 다음 백테스트 재실행 때 같은 경고가 반복되지 않도록 감사 로직을 active/evidence-only로 나눴다.",
        "",
        "## 1보 후퇴",
        "",
        "- `객단가`는 전처리 산출물에는 보존하지만 현재 점수에 넣지 않는다. 리포트에서도 고객 구매력 보장, 성장률 보장, 성공확률, 매출 상승 보장 근거로 쓰면 안 된다.",
        "",
        "## 산출물",
        "",
        f"- `{REGISTRY_OUT.relative_to(ROOT)}`",
        f"- `{VALIDATION_OUT.relative_to(ROOT)}`",
        f"- `{CORRECTED_AUDIT_OUT.relative_to(ROOT)}`",
        f"- `{SUMMARY_OUT.relative_to(ROOT)}`",
        f"- `{MD_OUT.relative_to(ROOT)}`",
    ]
    MD_OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    RULE_VALIDATION.mkdir(parents=True, exist_ok=True)
    RESEARCH_RULE_VALIDATION.mkdir(parents=True, exist_ok=True)

    direction = pd.read_csv(DIRECTION_MATRIX, encoding="utf-8-sig")
    registry = build_registry(direction)
    corrected = build_corrected_audit(registry)
    validation = validate(direction, registry, corrected)

    # Keep the historical audit name aligned with the patched audit logic.
    corrected.to_csv(GOLD_DIRECTION_AUDIT, index=False, encoding="utf-8-sig")
    registry.to_csv(REGISTRY_OUT, index=False, encoding="utf-8-sig")
    validation.to_csv(VALIDATION_OUT, index=False, encoding="utf-8-sig")
    corrected.to_csv(CORRECTED_AUDIT_OUT, index=False, encoding="utf-8-sig")

    summary = {
        "status": "PASS" if not validation["result"].astype(str).eq("FAIL").any() else "FAIL",
        "pass_count": int(validation["result"].astype(str).eq("PASS").sum()),
        "fail_count": int(validation["result"].astype(str).eq("FAIL").sum()),
        "direction_matrix_rows": int(len(direction)),
        "active_indicator_count": int(len(engine.INDICATORS)),
        "active_direction_rows": int((registry["registry_status"] == "active_score_indicator").sum()),
        "evidence_only_rows": int((registry["registry_status"] == "evidence_only_direction_row").sum()),
        "orphan_rows": int((registry["registry_status"] == "orphan_direction_row").sum()),
        "evidence_only_metrics": sorted(
            registry.loc[registry["registry_status"] == "evidence_only_direction_row", "metric"].astype(str)
        ),
        "corrected_gold_direction_audit": str(GOLD_DIRECTION_AUDIT.relative_to(ROOT)),
        "outputs": [
            str(REGISTRY_OUT.relative_to(ROOT)),
            str(VALIDATION_OUT.relative_to(ROOT)),
            str(CORRECTED_AUDIT_OUT.relative_to(ROOT)),
            str(SUMMARY_OUT.relative_to(ROOT)),
            str(MD_OUT.relative_to(ROOT)),
        ],
    }
    SUMMARY_OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(summary, registry, validation)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
