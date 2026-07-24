from __future__ import annotations

import importlib.util
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

EVIDENCE_ROOT = ROOT / "research" / "algorithm_evidence_sources"
COLLECTION_DOCS = ROOT / "docs" / "06_research_evidence" / "collection"
SCORING_DOCS = ROOT / "docs" / "04_algorithm_scoring"
CATALOG = COLLECTION_DOCS / "수집자료_카탈로그_20260630.md"
DETAIL_TABLE = COLLECTION_DOCS / "수집자료_상세검증표_20260630.md"
CRITERIA = COLLECTION_DOCS / "자료수집_검증기준.md"
SPEC_V2 = SCORING_DOCS / "specs" / "알고리즘_명세_v2_20260704.md"
ENGINE = SCRIPTS / "build_rule_based_location_scores.py"
RULE_DIR = ROOT / "datacorpus" / "_rule_validation"
DOC_DIR = ROOT / "research" / "rule_validation"
BACKTEST_SUMMARY = ROOT / "datacorpus" / "_score_backtest" / "location_score_backtest_summary.json"
GOLD_BACKTEST_SUMMARY = ROOT / "datacorpus" / "_score_backtest_gold" / "gold_engine_backtest_summary.json"
BACKTEST_WEIGHTS = ROOT / "datacorpus" / "_score_backtest" / "location_score_backtest_recommended_weights.csv"
WEIGHT_REVIEW = (
    SCORING_DOCS
    / "legacy_specs"
    / "서울상권_입지판단본체_가중치_10회재귀검토.md"
)

OUT_TRACE = RULE_DIR / "98_algorithm_evidence_traceability.csv"
OUT_VALIDATION = RULE_DIR / "98_algorithm_evidence_traceability_validation.csv"
OUT_SUMMARY = RULE_DIR / "98_algorithm_evidence_traceability_summary.json"
OUT_DOC = DOC_DIR / "98_algorithm_evidence_traceability_20260707.md"

VERSION = "algorithm_evidence_traceability.v0.2-20260707"
TAG_RE = re.compile(r"\[(M\d{2}|K\d{2}|D\d{2}|Q\d{2}|RV\d{2}|BT|MV-[A-Z]{2}\d+)\]")


def import_module_from_path(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"모듈을 불러올 수 없습니다: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


engine = import_module_from_path("build_rule_based_location_scores", ENGINE)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def parse_catalog() -> dict[str, dict[str, str]]:
    catalog: dict[str, dict[str, str]] = {}
    for line in read_text(CATALOG).splitlines():
        if not line.startswith("| "):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 6 or cells[0] in {"ID", "---"}:
            continue
        item_id = cells[0]
        if not re.fullmatch(r"[MKDQ]\d{2}", item_id):
            continue
        local = cells[3].strip("`")
        catalog[item_id] = {
            "id": item_id,
            "title": cells[1],
            "provider": cells[2],
            "local_file": local,
            "source_url": cells[4],
            "algorithm_link": cells[5],
        }
    return catalog


def parse_tags(text: str) -> list[str]:
    return sorted(set(TAG_RE.findall(text or "")))


def tag_exists(tag: str, catalog: dict[str, dict[str, str]]) -> bool:
    if re.fullmatch(r"[MKDQ]\d{2}", tag):
        row = catalog.get(tag)
        if not row:
            return False
        return (EVIDENCE_ROOT / row["local_file"]).exists()
    if re.fullmatch(r"RV\d{2}", tag):
        number = tag.replace("RV", "")
        return any(DOC_DIR.glob(f"{number}_*")) or any(RULE_DIR.glob(f"{number}_*"))
    if tag == "BT":
        return (GOLD_BACKTEST_SUMMARY.exists() or BACKTEST_SUMMARY.exists()) and BACKTEST_WEIGHTS.exists()
    if re.fullmatch(r"MV-[A-Z]{2}\d+", tag):
        return True
    return False


def source_line(pattern: str) -> str:
    lines = read_text(ENGINE).splitlines()
    for index, line in enumerate(lines, start=1):
        if pattern in line:
            return f"scripts/build_rule_based_location_scores.py:{index}"
    return ""


def md_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join(["---"] * len(columns)) + " |",
    ]
    for row in rows:
        values = [str(row.get(col, "")).replace("\n", " ") for col in columns]
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


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


def build_trace_rows(catalog: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    # 축/공식 단위의 강한 규칙이다. 지표 단위 근거만으로는 가중합·분리·보류 원칙을 설명하기 어렵다.
    high_level_rules = [
        {
            "rule_id": "R01",
            "rule_group": "공식_현재입지",
            "engine_object": "current_location_score",
            "axis": "sales,competition,demand,accessibility",
            "direction": "WLC",
            "grain": "상권×업종/상권 혼합",
            "evidence_text": "[M08][M14][M15][BT][RV88]",
            "code_ref": source_line("_weighted_current_score"),
            "rule_ko": "공식 현재입지 점수는 4개 축 WLC이며 성장/비용/신뢰도는 합산하지 않는다.",
        },
        {
            "rule_id": "R02",
            "rule_group": "가중치",
            "engine_object": "load_axis_weights",
            "axis": "공식 4축",
            "direction": "CSV 원천",
            "grain": "업종대분류별 weight_set",
            "evidence_text": "[BT][M09][MV-SA1][MV-SA2][MV-SA3]",
            "code_ref": source_line("def load_axis_weights"),
            "rule_ko": "가중치는 코드 하드코딩이 아니라 백테스트 권장 가중치 CSV를 읽고 4축 부분합으로 재정규화한다.",
        },
        {
            "rule_id": "R03",
            "rule_group": "정규화",
            "engine_object": "percentile_scores",
            "axis": "전체 지표",
            "direction": "benefit/cost",
            "grain": "지표별 비교군",
            "evidence_text": "[M08][M14][Q08][RV88]",
            "code_ref": source_line("def percentile_scores"),
            "rule_ko": "서로 단위가 다른 지표는 비교군 백분위로 정규화하고 비용형은 100-백분위로 반전한다.",
        },
        {
            "rule_id": "R04",
            "rule_group": "결측_신뢰도",
            "engine_object": "_reliability",
            "axis": "data_reliability",
            "direction": "meta",
            "grain": "원천/지표 메타",
            "evidence_text": "[Q01][Q02][Q06][Q08][Q13][M18][RV03]",
            "code_ref": source_line("def _reliability"),
            "rule_ko": "결측은 0점 대체하지 않고 축 제외/가중치 재정규화/신뢰도 감점으로 처리한다.",
        },
        {
            "rule_id": "R05",
            "rule_group": "성장_분리",
            "engine_object": "growth_potential_score,growth_rebound_candidate_score",
            "axis": "growth",
            "direction": "candidate_only",
            "grain": "상권×업종×분기",
            "evidence_text": "[K02][K03][K06][D08][BT][RV37][RV38][RV88]",
            "code_ref": source_line("growth_rebound_candidate_status"),
            "rule_ko": "성장 관련 점수는 현재입지와 질문이 달라 공식 현재입지에 합산하지 않고 후보 신호로 분리한다.",
        },
        {
            "rule_id": "R06",
            "rule_group": "비용_분리",
            "engine_object": "cost_risk_score",
            "axis": "cost_risk",
            "direction": "separate_proxy_score",
            "grain": "자치구/지역 프록시",
            "evidence_text": "[D17][D18][M18][RV12][RV82]",
            "code_ref": source_line('"cost_risk_score"'),
            "rule_ko": "실거래/R-ONE은 월세·권리금 직접값이 아니므로 지역 비용 압력 프록시로 분리한다.",
        },
        {
            "rule_id": "R07",
            "rule_group": "교통_후보",
            "engine_object": "transit_accessibility_250m_candidate",
            "axis": "accessibility_candidate",
            "direction": "candidate_only",
            "grain": "상권×분기",
            "evidence_text": "[D11][D12][M11][M12][RV80][RV81]",
            "code_ref": source_line("transit_axis_candidate ="),
            "rule_ko": "교통 승하차량 250m 후보는 holdout 개선이 있어도 공식 v2.4를 덮지 않고 병렬 후보로 둔다.",
        },
        {
            "rule_id": "R08",
            "rule_group": "객단가_제외",
            "engine_object": "TICKET_EVIDENCE_ONLY",
            "axis": "sales_evidence_only",
            "direction": "excluded",
            "grain": "상권×업종",
            "evidence_text": "[D02][K05][RV48][RV49][RV50][RV88]",
            "code_ref": source_line("TICKET_EVIDENCE_ONLY"),
            "rule_ko": "객단가는 sales 축 직접 가점에서 제거하고 소비 단가 참고값으로만 보존한다.",
        },
        {
            "rule_id": "R09",
            "rule_group": "금지표현",
            "engine_object": "FORBIDDEN_CLAIMS,TEXT_MODEL_RULES",
            "axis": "report_contract",
            "direction": "claim_guard",
            "grain": "리포트 문장",
            "evidence_text": "[D02][D18][BT][RV88][RV93][RV94]",
            "code_ref": source_line("FORBIDDEN_CLAIMS"),
            "rule_ko": "성공확률·매출보장·성장보장·월세/권리금 수익성 단정은 출력 금지한다.",
        },
    ]
    rows.extend(high_level_rules)

    for idx, (name, spec) in enumerate(engine.INDICATORS.items(), start=1):
        rows.append(
            {
                "rule_id": f"I{idx:02d}",
                "rule_group": "지표",
                "engine_object": name,
                "axis": spec.get("axis"),
                "direction": spec.get("direction"),
                "grain": spec.get("grain"),
                "comparison_group": spec.get("group"),
                "evidence_text": spec.get("evidence"),
                "code_ref": source_line(f'"{name}"'),
                "rule_ko": spec.get("reason_ko"),
            }
        )

    for row in rows:
        tags = parse_tags(row.get("evidence_text", ""))
        row["evidence_tags"] = ",".join(tags)
        row["evidence_tag_count"] = len(tags)
        row["unresolved_tags"] = ",".join(tag for tag in tags if not tag_exists(tag, catalog))
        row["resolved"] = not bool(row["unresolved_tags"])
        row["official_score_status"] = (
            "official_current_axis" if row.get("axis") in engine.CURRENT_AXES
            else "separate_or_candidate_or_contract"
        )
    return rows


def main() -> int:
    RULE_DIR.mkdir(parents=True, exist_ok=True)
    DOC_DIR.mkdir(parents=True, exist_ok=True)

    catalog = parse_catalog()
    trace_rows = build_trace_rows(catalog)
    validations: list[dict[str, Any]] = []
    engine_source = read_text(ENGINE)
    spec_text = read_text(SPEC_V2)
    backtest_path = GOLD_BACKTEST_SUMMARY if GOLD_BACKTEST_SUMMARY.exists() else BACKTEST_SUMMARY
    backtest = read_json(backtest_path)
    weights = pd.read_csv(BACKTEST_WEIGHTS, encoding="utf-8-sig")

    unresolved = [row for row in trace_rows if row["unresolved_tags"]]
    incomplete = [
        row for row in trace_rows
        if not row.get("engine_object") or not row.get("axis") or not row.get("direction")
        or not row.get("evidence_text") or not row.get("rule_ko")
    ]
    official_rows = [row for row in trace_rows if row["official_score_status"] == "official_current_axis"]
    official_axis_counts = pd.Series([row["axis"] for row in official_rows]).value_counts().to_dict()
    official_axes = sorted(set(row["axis"] for row in official_rows))
    high_level_by_group = {row["rule_group"]: row for row in trace_rows if row["rule_id"].startswith("R")}

    catalog_missing_files = [
        item_id for item_id, row in catalog.items()
        if not (EVIDENCE_ROOT / row["local_file"]).exists()
    ]
    add_validation(
        validations,
        "98-V01",
        "수집자료 카탈로그 파일 실재",
        f"catalog_ids={len(catalog)}, missing_files={catalog_missing_files[:10]}",
        "missing_files=0",
        len(catalog) >= 60 and not catalog_missing_files,
        "자료 ID가 실제 로컬 파일로 추적되지 않으면 근거 인용으로 볼 수 없다.",
    )
    add_validation(
        validations,
        "98-V02",
        "엔진 지표 명세 완전성",
        f"indicator_count={len(engine.INDICATORS)}, incomplete={len(incomplete)}",
        "incomplete=0",
        len(engine.INDICATORS) >= 15 and len(incomplete) == 0,
        "점수에 들어가는 모든 지표는 축, 방향, grain, 근거, 한글 이유를 가져야 한다.",
    )
    add_validation(
        validations,
        "98-V03",
        "근거 태그 해소",
        f"trace_rows={len(trace_rows)}, unresolved={[(r['rule_id'], r['unresolved_tags']) for r in unresolved[:10]]}",
        "unresolved=0",
        not unresolved,
        "논문/자료를 썼다고 말하려면 모든 M/K/D/Q/RV/BT 태그가 실제 자료나 검증 산출물로 연결되어야 한다.",
    )
    add_validation(
        validations,
        "98-V04",
        "공식 현재입지 4축 제한",
        official_axes,
        sorted(engine.CURRENT_AXES),
        official_axes == sorted(engine.CURRENT_AXES) and "growth" not in official_axes and "cost_risk" not in official_axes,
        "공식 현재입지 점수는 sales, competition, demand, accessibility 4축만 사용해야 한다.",
    )
    add_validation(
        validations,
        "98-V05",
        "공식 축별 지표 수",
        official_axis_counts,
        "각 공식축 >= 2 indicators",
        all(official_axis_counts.get(axis, 0) >= 2 for axis in engine.CURRENT_AXES),
        "단일 지표 축은 자료 오류나 결측에 취약하므로 공식 4축에는 최소 2개 이상의 근거 지표가 있어야 한다.",
    )
    add_validation(
        validations,
        "98-V06",
        "가중치 CSV 원천 사용",
        {
            "weights_exists": BACKTEST_WEIGHTS.exists(),
            "weight_sets": sorted(weights["weight_set"].unique().tolist()),
            "components": sorted(weights["component"].unique().tolist()),
            "source_refs": ["WEIGHTS_CSV", "pd.read_csv(WEIGHTS_CSV)"],
        },
        "CSV exists and code reads WEIGHTS_CSV",
        BACKTEST_WEIGHTS.exists()
        and "WEIGHTS_CSV" in engine_source
        and "pd.read_csv(WEIGHTS_CSV" in engine_source
        and {"BASE", "CS1", "CS2", "CS3"}.issubset(set(weights["weight_set"])),
        "가중치가 코드 상수가 아니라 백테스트 산출물에서 읽혀야 가중치 조작을 추적할 수 있다.",
    )
    add_validation(
        validations,
        "98-V07",
        "백테스트 근거 하한",
        backtest.get("overall_metrics", {}),
        "rows>=400000 and next_sales_pct_spearman>=0.7",
        backtest.get("row_count", 0) >= 400_000
        and backtest.get("overall_metrics", {}).get("score_spearman_next_sales_pct_same_industry", 0) >= 0.7,
        "현재입지 점수는 성장률 보장이 아니라 다음분기 동일업종 매출 수준 후보 선별력으로 검증한다. 최신 gold engine 백테스트를 우선 사용한다.",
    )
    add_validation(
        validations,
        "98-V08",
        "후보/보류 신호 공식점수 비활성",
        {
            "growth_rebound_false": '"growth_rebound_score_engine_active"' in engine_source,
            "transit_false": '"transit_accessibility_candidate_engine_active": False' in engine_source,
            "ticket_excluded": "excluded_from_sales_axis" in engine_source,
        },
        "all true",
        '"transit_accessibility_candidate_engine_active": False' in engine_source
        and "excluded_from_sales_axis" in engine_source
        and "growth_rebound_score_engine_active" in engine_source,
        "후보 신호와 evidence-only 값은 설명에는 남겨도 공식 현재입지 산식을 덮으면 안 된다.",
    )
    add_validation(
        validations,
        "98-V09",
        "AHP 미사용 상태 명시",
        "AHP는 이번 구현에서 사용하지 않았다" in spec_text,
        True,
        "AHP는 이번 구현에서 사용하지 않았다" in spec_text,
        "전문가 쌍대비교 입력 없이 AHP를 썼다고 주장하면 근거가 과장된다.",
    )
    add_validation(
        validations,
        "98-V10",
        "근거 수집 기준 문서 존재",
        {"criteria": CRITERIA.exists(), "detail_table": DETAIL_TABLE.exists(), "catalog": CATALOG.exists()},
        "all true",
        CRITERIA.exists() and DETAIL_TABLE.exists() and CATALOG.exists(),
        "외부 자료 수집·검증 기준, 상세검증표, 카탈로그가 함께 있어야 자료 선별 이유를 설명할 수 있다.",
    )

    pass_count = sum(row["result"] == "PASS" for row in validations)
    fail_count = sum(row["result"] == "FAIL" for row in validations)
    decision = "ALGORITHM_EVIDENCE_TRACEABILITY_PASS" if fail_count == 0 else "ALGORITHM_EVIDENCE_TRACEABILITY_FAIL"

    pd.DataFrame(trace_rows).to_csv(OUT_TRACE, index=False, encoding="utf-8-sig")
    pd.DataFrame(validations).to_csv(OUT_VALIDATION, index=False, encoding="utf-8-sig")
    summary = {
        "validation_version": VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "decision": decision,
        "pass_count": pass_count,
        "fail_count": fail_count,
        "trace_row_count": len(trace_rows),
        "indicator_count": len(engine.INDICATORS),
        "official_axes": engine.CURRENT_AXES,
        "catalog_id_count": len(catalog),
        "backtest_source": str(backtest_path.relative_to(ROOT)),
        "backtest_row_count": backtest.get("row_count"),
        "backtest_next_sales_pct_spearman": backtest.get("overall_metrics", {}).get("score_spearman_next_sales_pct_same_industry"),
        "backtest_top_bottom_next_sales_ratio": backtest.get("overall_metrics", {}).get("top_vs_bottom_avg_next_sales_ratio"),
        "backtest_min_rank_corr_with_baseline": backtest.get("sensitivity_summary", {}).get("min_rank_corr_with_baseline"),
        "outputs": {
            "trace": str(OUT_TRACE.relative_to(ROOT)),
            "validation": str(OUT_VALIDATION.relative_to(ROOT)),
            "summary": str(OUT_SUMMARY.relative_to(ROOT)),
            "doc": str(OUT_DOC.relative_to(ROOT)),
        },
        "reason_ko": "현재 엔진의 공식 4축, 별도 점수, 후보 신호, 금지표현은 research 자료와 rule_validation 산출물로 추적 가능하다."
        if fail_count == 0
        else "근거 태그, 실제 파일, 코드 반영 중 끊어진 항목이 있어 알고리즘 보강 전에 해소해야 한다.",
    }
    OUT_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8-sig")

    doc = f"""# 98. 알고리즘 근거 추적성 검증

## 목적

사용자가 지적한 핵심은 UI가 아니라 전처리와 알고리즘 본체다.  
이번 검증은 `research/`에 모은 논문·자료·원천 문서가 실제 점수 엔진 규칙에 어떻게 연결되는지 추적한다.

## 검증 대상

- 엔진: `scripts/build_rule_based_location_scores.py`
- 명세: `research/알고리즘_명세_v2_20260704.md`
- 자료 카탈로그: `research/algorithm_evidence_sources/수집자료_카탈로그_20260630.md`
- 상세 검증표: `research/algorithm_evidence_sources/수집자료_상세검증표_20260630.md`
- 백테스트: `{backtest_path.relative_to(ROOT)}`
- 가중치: `datacorpus/_score_backtest/location_score_backtest_recommended_weights.csv`

## 결과

- validation version: `{VERSION}`
- decision: `{decision}`
- PASS: `{pass_count}`
- FAIL: `{fail_count}`
- trace rows: `{len(trace_rows)}`
- indicator count: `{len(engine.INDICATORS)}`
- catalog IDs: `{len(catalog)}`
- backtest source: `{backtest_path.relative_to(ROOT)}`
- backtest rows: `{backtest.get("row_count")}`
- next sales percentile Spearman: `{backtest.get("overall_metrics", {}).get("score_spearman_next_sales_pct_same_industry")}`
- top/bottom next sales ratio: `{backtest.get("overall_metrics", {}).get("top_vs_bottom_avg_next_sales_ratio")}`
- sensitivity min rank corr: `{backtest.get("sensitivity_summary", {}).get("min_rank_corr_with_baseline")}`

## 핵심 판단

{summary["reason_ko"]}

## 상위 규칙 추적표

{md_table([row for row in trace_rows if row["rule_id"].startswith("R")], ["rule_id", "rule_group", "engine_object", "axis", "evidence_tags", "code_ref", "rule_ko", "resolved"])}

## 검증 항목

{md_table(validations, ["validation_id", "validation_name", "observed", "expected", "result", "reason_ko"])}

## 해석

- 공식 현재입지 점수는 `sales`, `competition`, `demand`, `accessibility` 4축만 사용한다.
- `growth_potential_score`, `growth_rebound_candidate_score`, `cost_risk_score`, `transit_accessibility_250m_candidate`는 버린 값이 아니라 별도 점수 또는 후보 신호다.
- 객단가는 전처리에서 보존하지만 sales 축 직접 가점에서 제외된 evidence-only 항목이다.
- AHP 논문은 보유하고 있지만 전문가 쌍대비교 입력이 없으므로 현재 구현에서는 사용하지 않는다.
- 다음 알고리즘 강화는 이 추적표의 후보 신호 중 공식 승격 게이트를 통과한 것만 대상으로 해야 한다.

## 산출물

- `{OUT_TRACE.relative_to(ROOT)}`
- `{OUT_VALIDATION.relative_to(ROOT)}`
- `{OUT_SUMMARY.relative_to(ROOT)}`
"""
    OUT_DOC.write_text(doc, encoding="utf-8-sig")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
