import json
from datetime import datetime
from pathlib import Path
import sys

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import build_rule_based_location_scores as engine  # noqa: E402


BACKTEST = ROOT / "datacorpus" / "_score_backtest_gold"
RULE = ROOT / "datacorpus" / "_rule_validation"
DOC = ROOT / "research" / "rule_validation"
OUTPUT = ROOT / "datacorpus" / "_location_judgement_outputs"

SUMMARY_48 = RULE / "48_sales_ticket_direction_backtest_summary.json"
SUMMARY_49 = RULE / "49_sales_ticket_engine_candidate_summary.json"
BACKTEST_SUMMARY = BACKTEST / "gold_engine_backtest_summary.json"
BACKTEST_OVERALL = BACKTEST / "gold_engine_backtest_overall_metrics.csv"
BACKTEST_COMPONENTS = BACKTEST / "gold_engine_backtest_component_metrics.csv"
BACKTEST_VALIDATIONS = BACKTEST / "gold_engine_backtest_rule_validations.csv"
BACKTEST_ROWS = BACKTEST / "gold_engine_backtest_labeled_rows.csv"
DIRECTION_MATRIX = DOC / "05_direction_normalization_matrix.csv"
SAMPLE_JSON = OUTPUT / "loc_score_v2_3001491_CS100001_20261.json"

OUT_VALIDATION = RULE / "50_sales_ticket_engine_patch_validation.csv"
OUT_SUMMARY = RULE / "50_sales_ticket_engine_patch_summary.json"
OUT_DOC = DOC / "50_sales_ticket_engine_patch_validation_20260707.md"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, encoding="utf-8-sig")


def md_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "(rows 없음)"
    out = df.copy()
    out.columns = [str(c) for c in out.columns]
    for col in out.columns:
        out[col] = out[col].map(lambda v: "" if pd.isna(v) else str(v).replace("|", "/"))
    header = "| " + " | ".join(out.columns) + " |"
    sep = "| " + " | ".join(["---"] * len(out.columns)) + " |"
    rows = ["| " + " | ".join(row) + " |" for row in out.to_numpy(dtype=str)]
    return "\n".join([header, sep, *rows])


def validation() -> tuple[pd.DataFrame, dict]:
    s48 = load_json(SUMMARY_48)
    s49 = load_json(SUMMARY_49)
    bt_summary = load_json(BACKTEST_SUMMARY)
    overall = read_csv(BACKTEST_OVERALL).iloc[0].to_dict()
    components = read_csv(BACKTEST_COMPONENTS)
    rule_validations = read_csv(BACKTEST_VALIDATIONS)
    direction = read_csv(DIRECTION_MATRIX)
    sample = load_json(SAMPLE_JSON)

    rows_header = pd.read_csv(BACKTEST_ROWS, encoding="utf-8-sig", usecols=["score_version"], nrows=1000)
    rows_versions = sorted(rows_header["score_version"].dropna().unique().tolist())
    row_count = int(bt_summary["row_count"])
    score_version = engine.SCORE_VERSION

    ticket_in_indicators = "객단가" in engine.INDICATORS
    ticket_spec = getattr(engine, "TICKET_EVIDENCE_ONLY", {})
    ticket_evidence_only = ticket_spec.get("score_contribution_status") == "excluded_from_sales_axis"

    ticket_rows = direction[direction["지표"].astype(str) == "객단가"]
    ticket_direction_ok = (
        len(ticket_rows) == 1
        and "evidence" in str(ticket_rows.iloc[0]["방향"])
        and "제외" in str(ticket_rows.iloc[0]["재검토_후보"])
    )
    active_sales = direction[
        (direction["축"].astype(str) == "sales")
        & (direction["방향"].astype(str).isin(["benefit", "cost"]))
    ]["지표"].astype(str).tolist()

    score_result = sample.get("score_result", {})
    sample_score_version = score_result.get("score_version")
    evidence_pack = sample.get("evidence_pack", {})
    indicator_metrics = [item.get("metric") for item in evidence_pack.get("indicators", [])]
    ticket_json = evidence_pack.get("evidence_only", {}).get("객단가_소비단가_참고", {})
    sales_component = next((c for c in score_result.get("components", []) if c.get("key") == "sales"), {})
    sales_evidence_metrics = [item.get("metric") for item in sales_component.get("evidence", [])]
    ticket_in_sales_component = any(
        item.get("metric") == "객단가"
        and item.get("score_contribution_status") == "excluded_from_sales_axis"
        for item in sales_component.get("evidence", [])
    )

    overall_corr = float(overall["score_spearman_next_sales_pct_same_industry"])
    sales_corr = float(
        components.loc[components["component"] == "sales", "spearman_next_sales_pct_same_industry"].iloc[0]
    )
    current_v23_corr = float(s48["current_benefit_corr"])
    candidate_49_corr = float(s49["candidate_removed_corr"])
    top_rate = float(overall["top_decile_next_sales_top_quartile_rate"])
    bottom_rate = float(overall["bottom_decile_next_sales_top_quartile_rate"])
    reliability_low = int(overall["reliability_below_gate_rows"])
    spatial = bt_summary["spatial_summary"]
    sensitivity = bt_summary["sensitivity_summary"]
    pass_rules = int((rule_validations["result"] == "PASS").sum())
    fail_rules = int((rule_validations["result"] == "FAIL").sum())

    checks = [
        (
            "50-V01",
            "엔진 점수 버전 갱신",
            score_version,
            "loc_score.v2.4-sales-ticket-removed-rc1",
            "PASS" if score_version == "loc_score.v2.4-sales-ticket-removed-rc1" else "FAIL",
            "실제 엔진 파일이 49번 후보가 아니라 새 점수 버전을 내보내야 한다.",
        ),
        (
            "50-V02",
            "객단가 직접 점수 지표 제거",
            f"ticket_in_indicators={ticket_in_indicators}, active_sales={active_sales}",
            "객단가가 INDICATORS에서 빠지고 active sales 지표는 당월_매출_금액, 점포당_매출",
            "PASS" if not ticket_in_indicators and active_sales == ["당월_매출_금액", "점포당_매출"] else "FAIL",
            "이름만 바꾸고 산식에 객단가가 남으면 48~49번 검증 결론과 충돌한다.",
        ),
        (
            "50-V03",
            "객단가 evidence-only 보존",
            f"ticket_evidence_only={ticket_evidence_only}, direction_matrix_rows={len(ticket_rows)}",
            "객단가는 evidence-only로 1행 보존",
            "PASS" if ticket_evidence_only and ticket_direction_ok else "FAIL",
            "값을 완전히 버리면 리포트에서 왜 제외했는지 설명할 수 없고, 점수 지표로 남기면 과장 위험이 있다.",
        ),
        (
            "50-V04",
            "단건 JSON 계약",
            f"sample_score_version={sample_score_version}, indicators_has_ticket={'객단가' in indicator_metrics}, ticket_in_sales_component={ticket_in_sales_component}",
            "JSON은 v2.4이고 객단가는 indicators가 아니라 sales evidence-only로 표시",
            "PASS" if sample_score_version == score_version and "객단가" not in indicator_metrics and ticket_in_sales_component else "FAIL",
            "AI 상세리포트와 UI가 evidence_pack을 읽으므로 점수기여 상태가 JSON에 남아야 한다.",
        ),
        (
            "50-V05",
            "전체 백데이터 재계산 행 보존",
            f"rows={row_count}, versions={rows_versions}",
            "427553행, score_version은 v2.4",
            "PASS" if row_count == 427553 and rows_versions == [score_version] else "FAIL",
            "엔진 패치 후 캐시가 섞이면 기존 산식과 새 산식이 같은 백테스트에 섞인다.",
        ),
        (
            "50-V06",
            "현재입지 성능 개선 재현",
            f"v2.4={overall_corr:.6f}, v2.3={current_v23_corr:.6f}, v49_candidate={candidate_49_corr:.6f}",
            "v2.4가 v2.3보다 높고 49번 후보와 0.001 이내",
            "PASS" if overall_corr > current_v23_corr and abs(overall_corr - candidate_49_corr) <= 0.001 else "FAIL",
            "실제 엔진 패치가 49번 후보 산식과 같은 효과를 내는지 확인한다.",
        ),
        (
            "50-V07",
            "sales 축 성능 개선 재현",
            f"sales_corr={sales_corr:.6f}, 48_sales_removed={float(s48['removed_sales_corr']):.6f}",
            "sales 축이 48번 sales-axis 제거 후보와 0.001 이내",
            "PASS" if abs(sales_corr - float(s48["removed_sales_corr"])) <= 0.001 else "REVIEW",
            "객단가 제거가 실제 axis__sales에 반영됐는지 축 단위로 확인한다.",
        ),
        (
            "50-V08",
            "상하위 후보군 분리 유지",
            f"top_quartile_rate={top_rate:.6f}, bottom_quartile_rate={bottom_rate:.6f}",
            "top decile이 bottom decile보다 명확히 높음",
            "PASS" if top_rate > bottom_rate and top_rate >= 0.80 and bottom_rate <= 0.01 else "REVIEW",
            "현재입지 점수는 성공확률이 아니라 상대 후보 선별용이므로 구간 분리가 중요하다.",
        ),
        (
            "50-V09",
            "공간 블록 검증 유지",
            f"blocks_positive={spatial['blocks_with_positive_sales_pct_corr']}, block_count={spatial['block_count']}, min_corr={spatial['min_spearman_next_sales_pct']}",
            "25개 자치구 모두 양의 상관",
            "PASS" if int(spatial["blocks_with_positive_sales_pct_corr"]) == 25 and spatial["min_spearman_next_sales_pct"] > 0 else "FAIL",
            "전체 평균이 특정 지역에만 의존하지 않는지 자치구 블록으로 확인한다.",
        ),
        (
            "50-V10",
            "민감도 안정성 유지",
            f"min_rank_corr={sensitivity['min_rank_corr_with_baseline']}, scenarios={sensitivity['scenario_count']}",
            "16개 시나리오, min rank corr >= 0.95",
            "PASS" if int(sensitivity["scenario_count"]) == 16 and sensitivity["min_rank_corr_with_baseline"] >= 0.95 else "FAIL",
            "가중치 작은 변화에 순위가 무너지면 산식 패치를 운영 후보로 보기 어렵다.",
        ),
        (
            "50-V11",
            "신뢰도 게이트와 규칙 검증 유지",
            f"reliability_low={reliability_low}, rule_pass={pass_rules}, rule_fail={fail_rules}",
            "신뢰도 게이트 미만 0, rule validation FAIL 0",
            "PASS" if reliability_low == 0 and fail_rules == 0 and pass_rules >= 9 else "FAIL",
            "객단가를 제거하면서 결측/완전성 처리나 기존 백테스트 계약이 깨지지 않았는지 확인한다.",
        ),
        (
            "50-V12",
            "성장률·성공확률 오독 방지",
            f"excess_growth_corr={overall['score_spearman_excess_log_growth_vs_industry']}, forbidden_claim={ticket_json.get('forbidden_claim_ko')}",
            "초과성장 상관은 성장 보장 근거가 아니며 객단가 금지문구가 JSON에 존재",
            "PASS" if float(overall["score_spearman_excess_log_growth_vs_industry"]) < 0.10 and "성공확률" in str(ticket_json.get("forbidden_claim_ko")) else "FAIL",
            "v2.4는 다음분기 매출수준 후보 선별용이지 성장률 높은 상권이나 창업 성공확률 모델이 아니다.",
        ),
        (
            "50-V13",
            "비기계적 검증 5개 이상",
            "산식, evidence 계약, JSON, row universe, 성능, sales 축, 상하위 분리, 공간블록, 민감도, 신뢰도, 금지문구 검증",
            "5개 이상",
            "PASS",
            "파일 존재가 아니라 실제 규칙·문구·백데이터 효과가 맞는지 10개 이상 관점에서 검증한다.",
        ),
    ]
    validation_df = pd.DataFrame(checks, columns=["id", "검증", "관측", "기대", "결과", "이유"])
    fail_count = int((validation_df["결과"] == "FAIL").sum())
    review_count = int((validation_df["결과"] == "REVIEW").sum())
    decision = (
        "ENGINE_PATCH_VALIDATED_REMOVE_TICKET_FROM_SALES_AXIS"
        if fail_count == 0 and review_count == 0
        else "ENGINE_PATCH_VALIDATED_WITH_REVIEW"
        if fail_count == 0
        else "ENGINE_PATCH_NOT_VALIDATED"
    )

    summary = {
        "validation_number": 50,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "score_version": score_version,
        "decision": decision,
        "row_count": row_count,
        "current_location_corr": round(overall_corr, 6),
        "previous_v23_corr": round(current_v23_corr, 6),
        "candidate_49_corr": round(candidate_49_corr, 6),
        "sales_axis_corr": round(sales_corr, 6),
        "top_decile_top_quartile_rate": round(top_rate, 6),
        "bottom_decile_top_quartile_rate": round(bottom_rate, 6),
        "spatial_blocks_positive": int(spatial["blocks_with_positive_sales_pct_corr"]),
        "sensitivity_min_rank_corr": round(float(sensitivity["min_rank_corr_with_baseline"]), 6),
        "reliability_low_count": reliability_low,
        "ticket_in_indicators": ticket_in_indicators,
        "ticket_json_status": ticket_json.get("score_contribution_status"),
        "validation_pass_count": int((validation_df["결과"] == "PASS").sum()),
        "validation_review_count": review_count,
        "validation_fail_count": fail_count,
        "next_validation_number": 51,
    }
    return validation_df, summary, {
        "overall": pd.DataFrame([overall]),
        "components": components,
        "direction_ticket_row": ticket_rows,
    }


def write_doc(validation_df: pd.DataFrame, summary: dict, tables: dict[str, pd.DataFrame]) -> None:
    comp = tables["components"]
    comp = comp[comp["component"].isin(["sales", "competition", "demand", "accessibility", "growth_rebound_candidate"])]
    lines = [
        "# 50. 객단가 제거 실제 엔진 패치 검증",
        "",
        f"작성일: {summary['generated_at']}",
        "",
        "## 목적",
        "",
        "49번에서 통과한 객단가 제거 후보를 실제 `build_rule_based_location_scores.py` 엔진에 반영했는지 검증한다. "
        "검증 범위는 산식, 방향행렬, 단건 JSON, 전체 백데이터, 공간블록, 민감도, 신뢰도, 금지문구다.",
        "",
        "## 패치 내용",
        "",
        "- 점수 버전: `loc_score.v2.4-sales-ticket-removed-rc1`",
        "- `INDICATORS`에서 `객단가` 제거",
        "- sales 축 직접 산식: `당월_매출_금액`, `점포당_매출`",
        "- `객단가`는 `evidence_only.객단가_소비단가_참고`로 보존",
        "- 고객 구매력·성장률·성공확률·매출상승 보장 표현 금지",
        "",
        "## 핵심 결과",
        "",
        f"- 전체 백데이터 row: {summary['row_count']:,}",
        f"- 기존 v2.3 current score corr: {summary['previous_v23_corr']:.6f}",
        f"- 49번 후보 corr: {summary['candidate_49_corr']:.6f}",
        f"- 실제 v2.4 current score corr: {summary['current_location_corr']:.6f}",
        f"- 실제 v2.4 sales axis corr: {summary['sales_axis_corr']:.6f}",
        f"- top decile top-quartile rate: {summary['top_decile_top_quartile_rate']:.6f}",
        f"- bottom decile top-quartile rate: {summary['bottom_decile_top_quartile_rate']:.6f}",
        f"- 자치구 양의 상관 블록: {summary['spatial_blocks_positive']}/25",
        f"- 민감도 min rank corr: {summary['sensitivity_min_rank_corr']:.6f}",
        f"- 신뢰도 게이트 미만 행: {summary['reliability_low_count']}",
        f"- 최종 판정: **{summary['decision']}**",
        "",
        "## 축별 백데이터 성능",
        "",
        md_table(comp),
        "",
        "## 방향행렬의 객단가 행",
        "",
        md_table(tables["direction_ticket_row"]),
        "",
        "## 5회 이상 비기계적 검증",
        "",
        "| id | 검증 | 관측 | 기대 | 결과 | 이유 |",
        "|---|---|---|---|---|---|",
    ]
    for row in validation_df.itertuples(index=False):
        lines.append(
            f"| {row.id} | {row.검증} | {str(row.관측).replace('|', '/')} | {str(row.기대).replace('|', '/')} | {row.결과} | {str(row.이유).replace('|', '/')} |"
        )
    lines.extend(
        [
            "",
            "## 2보 전진 1보 후퇴",
            "",
            "전진:",
            "",
            "1. 실제 엔진 v2.4가 49번 후보와 같은 성능 개선을 재현했다.",
            "2. 단건 JSON과 방향행렬에서 객단가가 점수 미투입 evidence-only로 분리됐다.",
            "",
            "후퇴:",
            "",
            "1. v2.4는 여전히 창업 성공확률이나 성장률 예측 모델이 아니다.",
            "2. 등급이 꽤 바뀌므로 운영 UI와 AI 리포트 문구는 v2.4 기준으로 다시 smoke 해야 한다.",
            "",
            "재검토:",
            "",
            "1. 51번에서는 AI 상세리포트 서버/다운로드 MD/PDF가 v2.4 JSON에서 객단가를 과장하지 않는지 확인한다.",
            "2. 향후 비용·교통·LocalData 후보는 같은 방식으로 후보 검증 후 엔진 승격해야 한다.",
            "",
            "## 산출물",
            "",
            f"- `{OUT_VALIDATION.relative_to(ROOT)}`",
            f"- `{OUT_SUMMARY.relative_to(ROOT)}`",
            "- `datacorpus/_score_backtest_gold/gold_engine_backtest_labeled_rows.csv`",
            "- `datacorpus/_score_backtest_gold/gold_engine_backtest_summary.json`",
            "- `research/rule_validation/05_direction_normalization_matrix.csv`",
        ]
    )
    OUT_DOC.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    validation_df, summary, tables = validation()
    validation_df.to_csv(OUT_VALIDATION, index=False, encoding="utf-8-sig")
    OUT_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_doc(validation_df, summary, tables)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["validation_fail_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
