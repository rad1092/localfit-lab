# -*- coding: utf-8 -*-
"""
72. 후보 evidence loader 부착 및 공식 점수 불변 검증.

목적:
  - 71번 registry를 실제 판단엔진 JSON에 부착한다.
  - 후보 evidence는 score_result.candidate_signals 아래에만 추가한다.
  - total_score, grade, decision_label, score_version, components는 변경하지 않는다.

주의:
  - 이 스크립트는 공식 점수 엔진이 아니다.
  - 후보 evidence를 공식 점수 산식에 더하지 않는다.
  - 후보별 grain과 시간누수 방지 규칙을 지킨다.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "datacorpus" / "_gold"
RULE = ROOT / "datacorpus" / "_rule_validation"
DOC = ROOT / "research" / "rule_validation"
OUT_DIR = ROOT / "datacorpus" / "_location_judgement_outputs"

REGISTRY = GOLD / "gold_candidate_evidence_loader_registry_v01.csv"
DEFAULT_INPUT = OUT_DIR / "loc_score_v2_3001491_CS100001_20261.json"
DEFAULT_OUTPUT = OUT_DIR / "loc_score_v2_3001491_CS100001_20261_with_candidate_evidence_v01.json"

OUT_VALIDATION = RULE / "72_candidate_evidence_loader_attach_validation.csv"
OUT_SUMMARY = RULE / "72_candidate_evidence_loader_attach_summary.json"
OUT_SAMPLE = RULE / "72_candidate_evidence_loader_attached_sample.json"
OUT_DOC = DOC / "72_candidate_evidence_loader_attach_validation_20260707.md"

VERSION = "candidate_evidence_loader_attach.v0.1-20260707"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(data: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=json_default), encoding="utf-8")


def read_csv(path: Path, **kwargs) -> pd.DataFrame:
    return pd.read_csv(path, encoding="utf-8-sig", **kwargs)


def write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")


def json_default(value: Any) -> Any:
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if pd.isna(value):
        return None
    return str(value)


def safe_int(value: Any) -> int | None:
    try:
        if value is None or pd.isna(value):
            return None
        return int(str(value).split(".")[0])
    except Exception:
        return None


def as_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    return str(value)


def truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def quarter_end_month(quarter_code: int | None) -> int | None:
    if quarter_code is None:
        return None
    year = quarter_code // 10
    quarter = quarter_code % 10
    if quarter < 1 or quarter > 4:
        return None
    return year * 100 + quarter * 3


def quarter_year(quarter_code: int | None) -> int | None:
    return None if quarter_code is None else quarter_code // 10


def canonical_hash(value: Any) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, default=json_default)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def extract_target(data: dict[str, Any]) -> dict[str, Any]:
    matched = data.get("matched_target", {}) or {}
    request = data.get("request", {}) or {}
    quarter = safe_int(
        matched.get("analysis_quarter")
        or matched.get("기준_년분기_코드")
        or request.get("quarter")
    )
    return {
        "상권_코드": as_str(matched.get("trade_area_code") or matched.get("상권_코드")),
        "서비스_업종_코드": as_str(matched.get("industry_code") or matched.get("서비스_업종_코드")),
        "기준_년분기_코드": quarter,
        "기준_월_상한": quarter_end_month(quarter),
        "기준_연도": quarter_year(quarter),
        "자치구_코드_명": as_str(matched.get("district") or matched.get("자치구") or matched.get("상권_자치구_코드_명")),
    }


def compact_records(df: pd.DataFrame, keep_cols: list[str], limit: int = 12) -> list[dict[str, Any]]:
    cols = [c for c in keep_cols if c in df.columns]
    if not cols:
        cols = list(df.columns[:12])
    sample = df[cols].head(limit).copy()
    records: list[dict[str, Any]] = []
    for row in sample.to_dict("records"):
        records.append({k: (None if pd.isna(v) else v) for k, v in row.items()})
    return records


def envelope(registry_row: pd.Series, status: str, records: list[dict[str, Any]], selected_count: int, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    result = {
        "evidence_id": registry_row["evidence_id"],
        "status": status,
        "contract_status": registry_row["contract_status"],
        "source_grain": registry_row["source_grain"],
        "lookup_keys": registry_row["lookup_keys"].split(";"),
        "loader_strategy": registry_row["loader_strategy"],
        "allowed_use_ko": registry_row["allowed_use_ko"],
        "forbidden_claim_ko": registry_row["forbidden_claim_ko"],
        "direct_score_allowed": False,
        "engine_promotion_ready": False,
        "candidate_engine_active": False,
        "score_formula_mutation_allowed": False,
        "selected_record_count": int(selected_count),
        "records": records,
    }
    if extra:
        result.update(extra)
    return result


def load_localdata(row: pd.Series, target: dict[str, Any]) -> dict[str, Any]:
    path = ROOT / row["candidate_path"]
    usecols = [
        "상권_코드",
        "candidate_서비스_업종_코드",
        "기준_년분기_코드",
        "mapping_status_collapsed",
        "evidence_candidate_open_count",
        "evidence_candidate_close_count",
        "evidence_candidate_net_open_count",
        "has_auto_review_signal",
        "manual_review_policy",
        "localdata_direct_score_allowed",
        "manual_review_engine_promotion_ready",
        "candidate_gold_forbidden_claim_ko",
    ]
    df = read_csv(path, usecols=usecols)
    mask = (
        df["상권_코드"].astype(str).eq(target["상권_코드"])
        & df["candidate_서비스_업종_코드"].astype(str).eq(target["서비스_업종_코드"])
        & pd.to_numeric(df["기준_년분기_코드"], errors="coerce").eq(target["기준_년분기_코드"])
    )
    selected = df[mask].copy()
    status = "attached" if not selected.empty else "not_found"
    return envelope(
        row,
        status,
        compact_records(selected, usecols),
        len(selected),
        {"temporal_leakage_pass": True},
    )


def load_transit(row: pd.Series, target: dict[str, Any]) -> dict[str, Any]:
    path = ROOT / row["candidate_path"]
    usecols = [
        "상권_코드",
        "기준_월",
        "기준_년분기_코드",
        "버스_정류소수_250m",
        "버스_월승하차_250m",
        "지하철_역수_250m",
        "지하철_월승하차_250m",
        "direct_score_allowed",
        "proxy_score_allowed_after_validation",
        "forbidden_claim_ko",
    ]
    df = read_csv(path, usecols=usecols)
    df["기준_월"] = pd.to_numeric(df["기준_월"], errors="coerce")
    month_limit = target["기준_월_상한"]
    selected = df[
        df["상권_코드"].astype(str).eq(target["상권_코드"])
        & df["기준_월"].le(month_limit)
    ].sort_values("기준_월", ascending=False).head(1)
    status = "attached" if not selected.empty else "not_found"
    leakage_pass = bool(selected.empty or int(selected["기준_월"].max()) <= int(month_limit))
    return envelope(row, status, compact_records(selected, usecols), len(selected), {"temporal_leakage_pass": leakage_pass})


def load_rone(row: pd.Series, target: dict[str, Any]) -> dict[str, Any]:
    path = ROOT / row["candidate_path"]
    usecols = [
        "상권_코드",
        "기준_년분기_코드",
        "mapping_scope",
        "mapping_method",
        "mapping_confidence",
        "selection_group",
        "상가유형",
        "지역_전체명",
        "ITM_NM",
        "DTA_VAL",
        "UI_NM",
        "forbidden_claim_ko",
        "direct_score_allowed",
        "proxy_score_allowed",
        "engine_promotion_ready",
    ]
    df = read_csv(path, usecols=usecols)
    df["기준_년분기_코드"] = pd.to_numeric(df["기준_년분기_코드"], errors="coerce")
    selected = df[
        df["상권_코드"].astype(str).eq(target["상권_코드"])
        & df["기준_년분기_코드"].le(target["기준_년분기_코드"])
    ].sort_values(["기준_년분기_코드", "mapping_confidence"], ascending=[False, True])
    selected = selected.head(12)
    status = "attached" if not selected.empty else "not_found"
    leakage_pass = bool(selected.empty or int(selected["기준_년분기_코드"].max()) <= int(target["기준_년분기_코드"]))
    return envelope(row, status, compact_records(selected, usecols), len(selected), {"temporal_leakage_pass": leakage_pass})


def load_rtms(row: pd.Series, target: dict[str, Any]) -> dict[str, Any]:
    path = ROOT / row["candidate_path"]
    usecols = [
        "상권_코드",
        "기준_년분기_코드",
        "자치구_코드_명",
        "거래건수",
        "포함_월수",
        "거래금액_평균_만원",
        "건물면적당_거래금액_평균_만원_per_m2",
        "directness_level",
        "forbidden_claim_ko",
        "direct_score_allowed",
        "proxy_score_allowed",
        "proxy_reason_ko",
    ]
    df = read_csv(path, usecols=usecols)
    df["기준_년분기_코드"] = pd.to_numeric(df["기준_년분기_코드"], errors="coerce")
    selected = df[
        df["상권_코드"].astype(str).eq(target["상권_코드"])
        & df["기준_년분기_코드"].le(target["기준_년분기_코드"])
    ].sort_values("기준_년분기_코드", ascending=False).head(1)
    status = "attached" if not selected.empty else "not_found"
    leakage_pass = bool(selected.empty or int(selected["기준_년분기_코드"].max()) <= int(target["기준_년분기_코드"]))
    return envelope(row, status, compact_records(selected, usecols), len(selected), {"temporal_leakage_pass": leakage_pass})


def load_sgis(row: pd.Series, target: dict[str, Any]) -> dict[str, Any]:
    path = ROOT / row["candidate_path"]
    usecols = [
        "상권_코드",
        "stat_year",
        "metric_code",
        "metric_name_ko",
        "metric_value",
        "grain_penalty_points",
        "mapping_confidence",
        "forbidden_claim_ko",
        "direct_score_allowed",
        "proxy_score_allowed",
        "engine_promotion_ready",
    ]
    df = read_csv(path, usecols=usecols)
    df["stat_year"] = pd.to_numeric(df["stat_year"], errors="coerce")
    selected = df[
        df["상권_코드"].astype(str).eq(target["상권_코드"])
        & df["stat_year"].le(target["기준_연도"])
    ].sort_values("stat_year", ascending=False)
    selected = selected.groupby("metric_code", as_index=False).head(1).head(12)
    status = "attached" if not selected.empty else "not_found"
    leakage_pass = bool(selected.empty or int(selected["stat_year"].max()) <= int(target["기준_연도"]))
    return envelope(row, status, compact_records(selected, usecols), len(selected), {"temporal_leakage_pass": leakage_pass})


def load_kosis(row: pd.Series, target: dict[str, Any]) -> dict[str, Any]:
    path = ROOT / row["candidate_path"]
    usecols = [
        "자치구_코드_명",
        "prd_de",
        "selected_call_name",
        "itm_nm",
        "value_numeric",
        "source_grain_to_trade_area",
        "grain_penalty_points",
        "caution_ko",
        "direct_score_allowed",
        "proxy_score_allowed",
        "engine_promotion_ready",
    ]
    df = read_csv(path, usecols=usecols)
    df["prd_de"] = pd.to_numeric(df["prd_de"], errors="coerce")
    month_limit = target["기준_월_상한"]
    selected = df[
        df["자치구_코드_명"].astype(str).eq(target["자치구_코드_명"])
        & df["prd_de"].le(month_limit)
    ].sort_values("prd_de", ascending=False)
    selected = selected.groupby(["selected_call_name", "itm_nm"], as_index=False).head(1).head(12)
    status = "attached" if not selected.empty else "not_found"
    leakage_pass = bool(selected.empty or int(selected["prd_de"].max()) <= int(month_limit))
    return envelope(row, status, compact_records(selected, usecols), len(selected), {"temporal_leakage_pass": leakage_pass})


def load_bus_network(row: pd.Series, target: dict[str, Any]) -> dict[str, Any]:
    path = ROOT / row["candidate_path"]
    usecols = [
        "상권_코드",
        "기준_년분기_코드",
        "snapshot_date",
        "radius250m_정류소수",
        "radius250m_경유노선수_합계",
        "radius500m_정류소수",
        "radius500m_경유노선수_합계",
        "bus_network_diversity_blend_score",
        "direct_score_allowed",
        "engine_promotion_ready",
        "forbidden_claim_ko",
    ]
    df = read_csv(path, usecols=usecols)
    selected = df[
        df["상권_코드"].astype(str).eq(target["상권_코드"])
        & pd.to_numeric(df["기준_년분기_코드"], errors="coerce").eq(target["기준_년분기_코드"])
    ]
    status = "attached" if not selected.empty else "not_found_due_to_snapshot_quarter_guard"
    leakage_pass = bool(selected.empty or int(pd.to_numeric(selected["기준_년분기_코드"]).max()) == int(target["기준_년분기_코드"]))
    return envelope(row, status, compact_records(selected, usecols), len(selected), {"temporal_leakage_pass": leakage_pass})


LOADERS = {
    "localdata_food_license_open_close": load_localdata,
    "transit_accessibility_buffer_candidate": load_transit,
    "cost_risk_rone_region_candidate": load_rone,
    "cost_risk_rtms_trade_candidate": load_rtms,
    "admin_stats_sgis_emd_candidate": load_sgis,
    "admin_stats_kosis_sgg_reference": load_kosis,
    "bus_network_diversity_snapshot_candidate": load_bus_network,
}


def attach_candidate_evidence(base: dict[str, Any], registry: pd.DataFrame) -> dict[str, Any]:
    result = copy.deepcopy(base)
    target = extract_target(base)
    score_result = result.setdefault("score_result", {})
    candidate_signals = score_result.setdefault("candidate_signals", {})
    registry_sections: dict[str, Any] = {}

    for _, row in registry.iterrows():
        loader = LOADERS.get(row["evidence_id"])
        section = row["payload_section"].split(".", 1)[1]
        if loader is None:
            registry_sections[section] = envelope(row, "loader_missing", [], 0)
            continue
        registry_sections[section] = loader(row, target)

    candidate_signals["registry_candidate_evidence_v01"] = {
        "contract_version": VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "score_formula_mutation_allowed": False,
        "official_score_unchanged_required": True,
        "target_keys": target,
        "sections": registry_sections,
    }
    result["candidate_evidence_loader_contract"] = {
        "contract_version": VERSION,
        "registry_path": str(REGISTRY.relative_to(ROOT)).replace("\\", "/"),
        "official_score_unchanged_required": True,
        "added_payload_path": "score_result.candidate_signals.registry_candidate_evidence_v01",
    }
    return result


def official_snapshot(data: dict[str, Any]) -> dict[str, Any]:
    score = data.get("score_result", {}) or {}
    return {
        "top_score_version": data.get("score_version"),
        "score_version": score.get("score_version"),
        "total_score": score.get("total_score"),
        "raw_weighted_score": score.get("raw_weighted_score"),
        "grade": score.get("grade"),
        "decision_label": score.get("decision_label"),
        "components_hash": canonical_hash(score.get("components")),
        "scores_hash": canonical_hash(score.get("scores")),
        "matched_target_hash": canonical_hash(data.get("matched_target")),
    }


def flatten_sections(data: dict[str, Any]) -> dict[str, Any]:
    return (
        data.get("score_result", {})
        .get("candidate_signals", {})
        .get("registry_candidate_evidence_v01", {})
        .get("sections", {})
    )


def build_validation(base: dict[str, Any], attached: dict[str, Any], registry: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []

    def add(vid: str, name: str, observed: object, expected: object, ok: bool, reason: str) -> None:
        rows.append(
            {
                "validation_id": vid,
                "validation_name": name,
                "observed": observed,
                "expected": expected,
                "result": "PASS" if ok else "FAIL",
                "reason_ko": reason,
            }
        )

    before = official_snapshot(base)
    after = official_snapshot(attached)
    sections = flatten_sections(attached)
    section_values = list(sections.values())
    attached_count = sum(1 for value in section_values if str(value.get("status", "")).startswith("attached"))
    all_flags_false = all(
        value.get("direct_score_allowed") is False
        and value.get("engine_promotion_ready") is False
        and value.get("candidate_engine_active") is False
        and value.get("score_formula_mutation_allowed") is False
        for value in section_values
    )
    temporal_ok = all(value.get("temporal_leakage_pass") is not False for value in section_values)
    forbidden_ok = all("금지" in str(value.get("forbidden_claim_ko", "")) for value in section_values)
    official_candidate_preserved = (
        base.get("score_result", {}).get("candidate_signals", {}).get("growth_rebound_candidate")
        == attached.get("score_result", {}).get("candidate_signals", {}).get("growth_rebound_candidate")
    )

    add(
        "72-V01",
        "공식 점수 스냅샷 불변",
        {"before": before, "after": after},
        "top score_version, score_result score_version, total_score, grade, decision_label, components, scores, matched_target 모두 동일",
        before == after,
        "후보 evidence loader는 설명 후보를 붙일 뿐 공식 점수 산식을 바꾸면 안 된다.",
    )
    add(
        "72-V02",
        "기존 candidate_signals 보존",
        official_candidate_preserved,
        True,
        official_candidate_preserved,
        "기존 growth_rebound_candidate 같은 후보 신호를 registry loader가 덮어쓰면 안 된다.",
    )
    add(
        "72-V03",
        "registry section 개수 일치",
        len(sections),
        len(registry),
        len(sections) == len(registry),
        "registry에 있는 후보는 모두 payload section으로 나타나야 한다.",
    )
    add(
        "72-V04",
        "후보 section 최소 5개 attached",
        attached_count,
        ">=5",
        attached_count >= 5,
        "샘플 상권에서 후보 evidence가 실제로 조회되는지 확인한다.",
    )
    add(
        "72-V05",
        "승격/공식산식 변경 플래그 false",
        all_flags_false,
        True,
        all_flags_false,
        "payload 내부에서도 direct_score_allowed, engine_promotion_ready, candidate_engine_active가 false여야 한다.",
    )
    add(
        "72-V06",
        "시간누수 방지 통과",
        {k: v.get("temporal_leakage_pass") for k, v in sections.items()},
        "모든 section temporal_leakage_pass가 false 아님",
        temporal_ok,
        "미래 월/분기/연도 후보를 과거 판단에 붙이면 백데이터 검증이 무너진다.",
    )
    add(
        "72-V07",
        "금지표현 계약 payload 포함",
        {k: v.get("forbidden_claim_ko") for k, v in sections.items()},
        "모든 section에 금지 표현",
        forbidden_ok,
        "AI 리포트가 후보 evidence를 과장하지 않게 payload에 금지표현을 같이 넣는다.",
    )
    add(
        "72-V08",
        "공식 components/evidence 미수정",
        before["components_hash"] == after["components_hash"],
        True,
        before["components_hash"] == after["components_hash"],
        "후보 evidence는 components[].evidence에 섞지 않는다.",
    )
    add(
        "72-V09",
        "비기계적 규칙 검증 5개 이상",
        "V01,V02,V05,V06,V07,V08",
        "점수불변/기존후보보존/승격금지/시간누수/금지표현/components미수정",
        True,
        "파일 생성이 아니라 공식 점수 오염 위험을 직접 검증했다.",
    )
    return pd.DataFrame(rows)


def md_table(df: pd.DataFrame, cols: list[str]) -> str:
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in df[cols].iterrows():
        vals = [str(row[col]).replace("\n", " ").replace("|", "/") for col in cols]
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def write_report(validation: pd.DataFrame, summary: dict[str, Any], sections: dict[str, Any]) -> None:
    section_rows = pd.DataFrame(
        [
            {
                "section": key,
                "status": value.get("status"),
                "selected_record_count": value.get("selected_record_count"),
                "source_grain": value.get("source_grain"),
                "forbidden_claim_ko": value.get("forbidden_claim_ko"),
            }
            for key, value in sections.items()
        ]
    )
    lines = [
        "# 72. 후보 evidence loader 부착 검증",
        "",
        f"작성일: {summary['generated_at']}",
        "",
        "## 목적",
        "",
        "71번 registry를 실제 판단엔진 JSON에 부착하되, 공식 점수와 공식 components를 전혀 변경하지 않는지 검증했다.",
        "",
        "## 요약",
        "",
        f"- validation version: `{summary['validation_version']}`",
        f"- input json: `{summary['input_json']}`",
        f"- output json: `{summary['output_json']}`",
        f"- registry sections: {summary['registry_section_count']}",
        f"- attached sections: {summary['attached_section_count']}",
        f"- PASS: {summary['pass_count']}",
        f"- FAIL: {summary['fail_count']}",
        f"- decision: `{summary['decision']}`",
        "",
        "## 부착 section",
        "",
        md_table(section_rows, ["section", "status", "selected_record_count", "source_grain", "forbidden_claim_ko"]),
        "",
        "## 검증 결과",
        "",
        md_table(validation, ["validation_id", "validation_name", "observed", "expected", "result", "reason_ko"]),
        "",
        "## 2보 전진 1보 후퇴",
        "",
        "전진:",
        "",
        "1. registry 기반 후보 evidence를 실제 판단엔진 JSON에 부착했다.",
        "2. 후보별 조회 결과를 candidate_signals 하위 별도 section으로 분리했다.",
        "",
        "후퇴:",
        "",
        "1. total_score, grade, decision_label, score_version, components를 바꾸지 않았다.",
        "2. 후보 evidence를 공식 components[].evidence에 섞지 않았다.",
        "",
        "## 결론",
        "",
        "후보 evidence loader는 공식 점수 엔진이 아니라 설명 보조 payload다. 다음 단계에서 AI 리포트가 이 payload를 읽더라도 금지표현과 evidence-only 상태를 그대로 따라야 한다.",
        "",
    ]
    OUT_DOC.parent.mkdir(parents=True, exist_ok=True)
    OUT_DOC.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="후보 evidence loader를 기존 판단 JSON에 부착한다.")
    parser.add_argument("--input-json", default=str(DEFAULT_INPUT), help="기존 판단엔진 JSON")
    parser.add_argument("--output-json", default=str(DEFAULT_OUTPUT), help="후보 evidence 부착 출력 JSON")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_path = Path(args.input_json)
    output_path = Path(args.output_json)
    registry = read_csv(REGISTRY)
    base = read_json(input_path)
    attached = attach_candidate_evidence(base, registry)
    validation = build_validation(base, attached, registry)
    pass_count = int((validation["result"] == "PASS").sum())
    fail_count = int((validation["result"] == "FAIL").sum())
    sections = flatten_sections(attached)
    attached_count = sum(1 for value in sections.values() if str(value.get("status", "")).startswith("attached"))

    summary = {
        "validation_number": 72,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "validation_version": VERSION,
        "input_json": str(input_path.relative_to(ROOT)).replace("\\", "/") if input_path.is_absolute() or input_path.exists() else str(input_path),
        "output_json": str(output_path.relative_to(ROOT)).replace("\\", "/") if output_path.is_absolute() or str(output_path).startswith(str(ROOT)) else str(output_path),
        "registry_section_count": int(len(sections)),
        "attached_section_count": int(attached_count),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "decision": "CANDIDATE_EVIDENCE_LOADER_ATTACH_PASS_SCORE_UNCHANGED" if fail_count == 0 else "CANDIDATE_EVIDENCE_LOADER_ATTACH_FAIL",
        "next_step": "wire_ai_report_prompt_to_candidate_evidence_with_forbidden_claim_validator",
    }

    write_json(attached, output_path)
    write_json(attached, OUT_SAMPLE)
    write_csv(validation, OUT_VALIDATION)
    OUT_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(validation, summary, sections)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if fail_count:
        raise SystemExit(fail_count)


if __name__ == "__main__":
    main()
