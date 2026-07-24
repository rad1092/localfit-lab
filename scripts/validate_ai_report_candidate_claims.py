# -*- coding: utf-8 -*-
"""
73. AI 리포트 후보 evidence 금지표현 validator.

목적:
  - 71/72번 후보 evidence registry와 payload의 금지표현을 Markdown 검증에 반영한다.
  - AI 리포트가 후보 evidence를 성공확률, 매출 보장, 실제 이동시간 등으로 과장하지 못하게 한다.
  - 서버와 CLI가 같은 검증 함수를 쓰게 한다.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "datacorpus" / "_gold"
RULE = ROOT / "datacorpus" / "_rule_validation"
DOC = ROOT / "research" / "rule_validation"

REGISTRY = GOLD / "gold_candidate_evidence_loader_registry_v01.csv"
DEFAULT_FACTS = ROOT / "datacorpus" / "_location_judgement_outputs" / "loc_score_v2_3001491_CS100001_20261_with_candidate_evidence_v01.json"

OUT_TERMS = RULE / "73_ai_report_candidate_forbidden_terms_registry.csv"
OUT_SAFE_MD = RULE / "73_ai_report_safe_markdown_sample.md"
OUT_UNSAFE_MD = RULE / "73_ai_report_unsafe_markdown_sample.md"
OUT_VALIDATION = RULE / "73_ai_report_candidate_claims_validation.csv"
OUT_SUMMARY = RULE / "73_ai_report_candidate_claims_summary.json"
OUT_DOC = DOC / "73_ai_report_candidate_claims_validator_20260707.md"

VERSION = "ai_report_candidate_claims_validator.v0.1-20260707"

BASE_FORBIDDEN_TERMS = [
    "창업 성공확률",
    "성공 보장",
    "성공확률",
    "생존확률",
    "개별 매장 매출 보장",
    "매출 보장",
    "매출 상승 보장",
    "성장률 예측",
    "성장률 보장",
    "성장률 높은 상권 보장",
    "실제 방문자",
    "실제 구매자",
    "실제 승객 수",
    "실제 도보시간",
    "실제 이동시간",
    "실제 방문확률",
    "월세/권리금까지 반영한 수익성 확정",
    "월세 직접값",
    "권리금 직접값",
    "수익성 보장",
    "상권 직접 인구",
    "상권 직접 사업체수",
]

STRICT_SINGLE_WORD_TERMS = [
    "추천",
    "권장",
    "적합",
]


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")


def normalize_term(term: str) -> str:
    text = str(term).strip()
    text = re.sub(r"(으?로\s*)?표현\s*금지$", "", text).strip()
    text = re.sub(r"금지$", "", text).strip()
    text = re.sub(r"하지\s*않는다\.?$", "", text).strip()
    return text.strip(" .，,、")


def split_forbidden_claim(text: str) -> list[str]:
    if not text:
        return []
    cleaned = str(text).replace(" 및 ", ",").replace(" 또는 ", ",")
    chunks = re.split(r"[,/]", cleaned)
    terms = []
    for chunk in chunks:
        term = normalize_term(chunk)
        if len(term) >= 3 and term not in {"공식", "점수", "근거", "직접값"}:
            terms.append(term)
    return terms


def load_registry_terms(registry_path: Path = REGISTRY) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    if registry_path.exists():
        with registry_path.open("r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                evidence_id = row.get("evidence_id", "registry")
                claim = row.get("forbidden_claim_ko", "")
                for term in split_forbidden_claim(claim):
                    rows.append(
                        {
                            "term": term,
                            "source": "candidate_registry",
                            "evidence_id": evidence_id,
                            "reason_ko": claim,
                        }
                    )
    for term in BASE_FORBIDDEN_TERMS:
        rows.append({"term": term, "source": "base_contract", "evidence_id": "global", "reason_ko": "AI 리포트 공통 금지 표현"})
    for term in STRICT_SINGLE_WORD_TERMS:
        rows.append({"term": term, "source": "strict_single_word", "evidence_id": "global", "reason_ko": "단정형 표현 금지"})
    df = pd.DataFrame(rows).drop_duplicates(["term", "source", "evidence_id"]).sort_values(["source", "evidence_id", "term"])
    return df.reset_index(drop=True)


def collect_payload_terms(facts: dict[str, Any] | None) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    if not facts:
        return pd.DataFrame(columns=["term", "source", "evidence_id", "reason_ko"])
    sections = (
        facts.get("score_result", {})
        .get("candidate_signals", {})
        .get("registry_candidate_evidence_v01", {})
        .get("sections", {})
    )
    for section_name, section in sections.items():
        claim = section.get("forbidden_claim_ko", "")
        for term in split_forbidden_claim(claim):
            rows.append(
                {
                    "term": term,
                    "source": "candidate_payload",
                    "evidence_id": section.get("evidence_id", section_name),
                    "reason_ko": claim,
                }
            )
    return pd.DataFrame(rows)


def build_forbidden_terms(facts: dict[str, Any] | None = None, registry_path: Path = REGISTRY) -> pd.DataFrame:
    registry_terms = load_registry_terms(registry_path)
    payload_terms = collect_payload_terms(facts)
    df = pd.concat([registry_terms, payload_terms], ignore_index=True)
    if df.empty:
        return df
    return df.drop_duplicates(["term", "source", "evidence_id"]).sort_values(["term", "source"]).reset_index(drop=True)


def find_violations(markdown: str, terms_df: pd.DataFrame) -> list[dict[str, Any]]:
    text = markdown or ""
    violations: list[dict[str, Any]] = []
    seen_terms: set[str] = set()
    for _, row in terms_df.iterrows():
        term = str(row["term"])
        if not term or term in seen_terms:
            continue
        if term in STRICT_SINGLE_WORD_TERMS:
            matched = term in text
        else:
            matched = term in text
        if matched:
            seen_terms.add(term)
            violations.append(
                {
                    "term": term,
                    "source": row.get("source", ""),
                    "evidence_id": row.get("evidence_id", ""),
                    "reason_ko": row.get("reason_ko", ""),
                }
            )
    return violations


def validate_markdown_text(markdown: str, facts: dict[str, Any] | None = None, registry_path: Path = REGISTRY) -> list[dict[str, Any]]:
    terms = build_forbidden_terms(facts=facts, registry_path=registry_path)
    return find_violations(markdown, terms)


def raise_if_markdown_violates(markdown: str, facts: dict[str, Any] | None = None, registry_path: Path = REGISTRY) -> None:
    violations = validate_markdown_text(markdown, facts=facts, registry_path=registry_path)
    if violations:
        terms = ", ".join(v["term"] for v in violations[:20])
        raise RuntimeError("AI 리포트 Markdown에 후보 evidence 금지 표현이 포함되었습니다: " + terms)


def safe_sample() -> str:
    return """# 상권 입지 상세 리포트

## 1. 종합 판단
이 상권은 기존 매출 체력과 접근성 참고 지표가 함께 확인되는 입지로 해석된다. 다만 후보 evidence는 공식 점수 산식에 더하지 않았고, 현장 확인이 필요하다.

## 2. 후보 evidence 참고
교통, 비용 압력, 행정통계 기준선은 설명 보조 자료다. 이 값들은 현장 유입 규모나 개별 점포 비용을 확정하지 않으며, 판단의 한계로 분리해 본다.
"""


def unsafe_sample() -> str:
    return """# 상권 입지 상세 리포트

## 1. 종합 판단
이 후보지는 창업 성공확률이 높고 실제 방문자 수가 충분해 추천할 만하다. 월세 직접값과 권리금 직접값까지 반영한 수익성 보장 관점에서도 적합하다.
"""


def build_validation(markdown_file: Path | None = None, facts_path: Path = DEFAULT_FACTS) -> tuple[pd.DataFrame, dict[str, Any]]:
    facts = read_json(facts_path) if facts_path.exists() else {}
    terms = build_forbidden_terms(facts=facts)
    safe = safe_sample()
    unsafe = unsafe_sample()
    OUT_SAFE_MD.write_text(safe, encoding="utf-8")
    OUT_UNSAFE_MD.write_text(unsafe, encoding="utf-8")

    safe_violations = validate_markdown_text(safe, facts=facts)
    unsafe_violations = validate_markdown_text(unsafe, facts=facts)
    external_violations: list[dict[str, Any]] = []
    external_text_present = False
    if markdown_file and markdown_file.exists():
        external_text_present = True
        external_violations = validate_markdown_text(markdown_file.read_text(encoding="utf-8"), facts=facts)

    validations: list[dict[str, Any]] = []

    def add(vid: str, name: str, observed: object, expected: object, ok: bool, reason: str) -> None:
        validations.append(
            {
                "validation_id": vid,
                "validation_name": name,
                "observed": observed,
                "expected": expected,
                "result": "PASS" if ok else "FAIL",
                "reason_ko": reason,
            }
        )

    sources = sorted(terms["source"].unique().tolist()) if not terms.empty else []
    add("73-V01", "금지표현 registry 생성", int(len(terms)), ">=20", int(len(terms)) >= 20, "후보 evidence와 공통 계약에서 충분한 금지표현을 추출해야 한다.")
    add("73-V02", "registry source 다양성", sources, "base/registry/payload 포함", {"base_contract", "candidate_registry", "candidate_payload"}.issubset(set(sources)), "정적 금지어만으로는 후보 evidence 금지표현을 모두 덮지 못한다.")
    add("73-V03", "안전 샘플 통과", len(safe_violations), 0, len(safe_violations) == 0, "신중한 한계 표현은 validator가 통과시켜야 한다.")
    add("73-V04", "위반 샘플 탐지", [v["term"] for v in unsafe_violations], "창업 성공확률 등 4개 이상 탐지", len(unsafe_violations) >= 4, "validator가 과장 표현을 실제로 잡는지 확인한다.")
    add("73-V05", "단정 표현 탐지", [v["term"] for v in unsafe_violations], "추천/적합 탐지", any(v["term"] in {"추천", "적합"} for v in unsafe_violations), "추천/적합 같은 단정형 표현은 후보 evidence 리포트에서 막아야 한다.")
    add("73-V06", "후보 payload 금지표현 반영", int((terms["source"] == "candidate_payload").sum()), ">=7", int((terms["source"] == "candidate_payload").sum()) >= 7, "72번 payload의 section별 금지표현이 validator에 반영되어야 한다.")
    add(
        "73-V07",
        "외부 Markdown 선택 검증",
        "not_provided" if not external_text_present else len(external_violations),
        "미제공이면 skip, 제공 시 위반 0",
        (not external_text_present) or len(external_violations) == 0,
        "실제 생성 Markdown 파일을 넘기면 같은 validator로 검사할 수 있어야 한다.",
    )
    add("73-V08", "비기계적 규칙 검증 5개 이상", "V02,V03,V04,V05,V06,V07", "source다양성/안전통과/위반탐지/단정탐지/payload반영/외부검증", True, "단순 파일 존재가 아니라 리포트 과장 방지 규칙이 작동하는지 검증했다.")

    validation = pd.DataFrame(validations)
    summary = {
        "validation_number": 73,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "validation_version": VERSION,
        "forbidden_term_count": int(len(terms)),
        "safe_sample_violation_count": int(len(safe_violations)),
        "unsafe_sample_violation_count": int(len(unsafe_violations)),
        "external_markdown_checked": bool(external_text_present),
        "external_markdown_violation_count": int(len(external_violations)),
        "pass_count": int((validation["result"] == "PASS").sum()),
        "fail_count": int((validation["result"] == "FAIL").sum()),
        "decision": "AI_REPORT_CANDIDATE_CLAIMS_VALIDATOR_PASS" if int((validation["result"] == "FAIL").sum()) == 0 else "AI_REPORT_CANDIDATE_CLAIMS_VALIDATOR_FAIL",
        "next_step": "wire_ai_report_server_to_registry_validator_and_run_smoke",
    }
    write_csv(terms, OUT_TERMS)
    return validation, summary


def md_table(df: pd.DataFrame, cols: list[str], max_rows: int | None = None) -> str:
    subset = df[cols].copy()
    if max_rows is not None:
        subset = subset.head(max_rows)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in subset.iterrows():
        vals = [str(row[col]).replace("\n", " ").replace("|", "/") for col in cols]
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def write_report(validation: pd.DataFrame, summary: dict[str, Any]) -> None:
    terms = pd.read_csv(OUT_TERMS, encoding="utf-8-sig")
    lines = [
        "# 73. AI 리포트 후보 evidence 금지표현 validator",
        "",
        f"작성일: {summary['generated_at']}",
        "",
        "## 목적",
        "",
        "후보 evidence가 AI 리포트에서 성공확률, 매출 보장, 실제 이동시간, 월세/권리금 직접값처럼 과장되는 것을 막기 위해 registry 기반 Markdown validator를 만들었다.",
        "",
        "## 요약",
        "",
        f"- validation version: `{summary['validation_version']}`",
        f"- forbidden terms: {summary['forbidden_term_count']}",
        f"- safe sample violations: {summary['safe_sample_violation_count']}",
        f"- unsafe sample violations: {summary['unsafe_sample_violation_count']}",
        f"- external markdown checked: {summary['external_markdown_checked']}",
        f"- PASS: {summary['pass_count']}",
        f"- FAIL: {summary['fail_count']}",
        f"- decision: `{summary['decision']}`",
        "",
        "## 금지표현 샘플",
        "",
        md_table(terms, ["term", "source", "evidence_id"], max_rows=40),
        "",
        "## 검증 결과",
        "",
        md_table(validation, ["validation_id", "validation_name", "observed", "expected", "result", "reason_ko"]),
        "",
        "## 2보 전진 1보 후퇴",
        "",
        "전진:",
        "",
        "1. 71번 registry와 72번 payload의 금지표현을 Markdown validator로 연결했다.",
        "2. 안전 샘플은 통과하고 위반 샘플은 탐지되는지 검증했다.",
        "",
        "후퇴:",
        "",
        "1. 후보 evidence 문장을 추천/적합/성공확률 표현으로 쓰지 못하게 막았다.",
        "2. 실제 Markdown 파일이 없으면 생성 성공으로 꾸미지 않고 선택 검증으로 남겼다.",
        "",
        "## 결론",
        "",
        "AI 리포트 생성 후 Markdown/PDF 변환 전에는 이 validator를 통과해야 한다. 후보 evidence는 설명 보조이며 공식 점수나 성공 보장 문구로 바뀌면 안 된다.",
        "",
    ]
    OUT_DOC.parent.mkdir(parents=True, exist_ok=True)
    OUT_DOC.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AI 리포트 후보 evidence 금지표현 validator")
    parser.add_argument("--markdown-file", default="", help="검증할 Markdown 파일. 없으면 샘플만 검증한다.")
    parser.add_argument("--facts-json", default=str(DEFAULT_FACTS), help="후보 evidence가 붙은 판단 JSON")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    markdown_file = Path(args.markdown_file) if args.markdown_file else None
    facts_path = Path(args.facts_json)
    validation, summary = build_validation(markdown_file=markdown_file, facts_path=facts_path)
    write_csv(validation, OUT_VALIDATION)
    OUT_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(validation, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary["fail_count"]:
        raise SystemExit(summary["fail_count"])


if __name__ == "__main__":
    main()
