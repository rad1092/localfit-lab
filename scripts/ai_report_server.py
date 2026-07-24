from __future__ import annotations

import argparse
import json
import os
import re
import sys
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from attach_candidate_evidence_loader import (  # noqa: E402
    REGISTRY as CANDIDATE_EVIDENCE_REGISTRY,
    attach_candidate_evidence,
    read_csv as read_candidate_evidence_registry,
)
from build_rule_based_location_scores import SCORE_VERSION, build_output  # noqa: E402
from resolve_rule_engine_inputs import RESOLVER_VERSION, load_resolver_data, normalize_text, resolve_industry, resolve_location  # noqa: E402
from validate_ai_report_candidate_claims import validate_markdown_text  # noqa: E402


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_MODEL = "gpt-5.4-mini"
DEFAULT_REASONING_EFFORT = "high"
DEFAULT_MAX_OUTPUT_TOKENS = 8000
DEFAULT_OPENAI_TIMEOUT_SECONDS = 240
REPORT_CONTRACT_VERSION = "ai_report_contract.v1.1-sales-ticket-removed-20260707"
LOOKUP_CONTRACT_VERSION = "ai_report_lookup_contract.v0.1-20260707"
INDUSTRY_TREE_PATH = ROOT / "datacorpus" / "_gold" / "gold_industry_selection_tree.json"
FORBIDDEN_MARKDOWN_TERMS = [
    "창업 성공확률",
    "성공 보장",
    "개별 매장 매출 보장",
    "성장률 높은 상권 보장",
    "성장률 예측",
    "성장률 보장",
    "고객 구매력 보장",
    "구매력 보장",
    "매출 상승 보장",
    "객단가가 높으니 고객 구매력",
    "월세/권리금까지 반영한 수익성 확정",
    "추천",
    "권장",
    "적합",
]


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def json_default(value: Any) -> Any:
    if hasattr(value, "item"):
        return value.item()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def attach_optional_candidate_evidence(facts: dict[str, Any]) -> dict[str, Any]:
    """후보 evidence를 공식 점수 변경 없이 리포트 설명용 facts에만 붙인다."""
    try:
        registry = read_candidate_evidence_registry(CANDIDATE_EVIDENCE_REGISTRY)
        return attach_candidate_evidence(facts, registry)
    except Exception as exc:  # pragma: no cover - 운영 응답 보존용 방어 경로
        result = dict(facts)
        warnings = list(result.get("warnings") or [])
        warnings.append(f"candidate evidence loader failed: {exc}")
        result["warnings"] = warnings
        result["candidate_evidence_loader_contract"] = {
            "contract_version": "candidate_evidence_loader.v01",
            "status": "failed",
            "official_score_unchanged_required": True,
            "error": str(exc),
        }
        return result


def normalize_quarter_codes(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        year, quarter = match.group(1), match.group(2)
        return f"{year}년 {quarter}분기"

    return re.sub(r"\b(20\d{2})([1-4])\b", replace, text)


def parse_location(value: str | None) -> dict[str, Any]:
    text = (value or "").strip()
    if not text:
        return {}
    if re.fullmatch(r"\d+", text):
        return {"trade_area_code": text}
    coord_match = re.fullmatch(r"\s*([0-9.]+)\s*,\s*([0-9.]+)\s*", text)
    if coord_match:
        return {"lat": float(coord_match.group(1)), "lng": float(coord_match.group(2))}
    return {"trade_area_name": text}


def parse_industry(value: str | None) -> dict[str, Any]:
    text = (value or "").strip()
    if not text:
        return {}
    if re.fullmatch(r"CS\d{6}", text, flags=re.IGNORECASE):
        return {"industry_code": text.upper()}
    return {"industry_name": text}


@lru_cache(maxsize=1)
def get_resolver_data():
    return load_resolver_data()


@lru_cache(maxsize=1)
def get_industry_selection_tree() -> dict[str, Any]:
    """화면 업종 선택용 계층이다. 최종 알고리즘 키는 여기서 고른 서비스_업종_코드만 쓴다."""
    return json.loads(INDUSTRY_TREE_PATH.read_text(encoding="utf-8"))


def parse_limit(value: str | None, default: int = 20, maximum: int = 50) -> int:
    try:
        limit = int(value or default)
    except ValueError:
        return default
    return max(1, min(limit, maximum))


def location_lookup_row(row, match_reason: str) -> dict[str, Any]:
    return {
        "trade_area_code": row.get("상권_코드"),
        "trade_area_name": row.get("상권_코드_명"),
        "trade_area_type": row.get("상권_구분_코드_명"),
        "district_name": row.get("자치구_코드_명"),
        "admin_dong_name": row.get("행정동_코드_명"),
        "display_label": row.get("display_label"),
        "representative_lon_wgs84": row.get("representative_lon_wgs84"),
        "representative_lat_wgs84": row.get("representative_lat_wgs84"),
        "final_algorithm_key": row.get("상권_코드"),
        "match_reason": match_reason,
    }


def search_location_candidates(query: str, limit: int = 20) -> dict[str, Any]:
    """상권 후보 검색이다. 점수 계산이 아니라 상권_코드 확정 전 UI 후보 생성에만 쓴다."""
    query_text = (query or "").strip()
    query_norm = normalize_text(query_text)
    if not query_norm:
        return {
            "query": query_text,
            "match_count": 0,
            "candidates": [],
            "rule_ko": "빈 검색어는 자동 후보를 만들지 않는다. 사용자가 상권명, 자치구, 행정동, 또는 상권코드를 입력해야 한다.",
        }

    locations = get_resolver_data().locations.copy()
    search_text = locations["location_search_text"].map(normalize_text)
    names = locations["상권_코드_명"].map(normalize_text)
    displays = locations["display_label"].map(normalize_text)
    codes = locations["상권_코드"].astype(str)

    exact_code = codes == query_text
    exact_name = (names == query_norm) | (displays == query_norm)
    prefix_name = names.str.startswith(query_norm, na=False) | displays.str.startswith(query_norm, na=False)
    contains = search_text.str.contains(query_norm, regex=False, na=False)
    candidates = locations[exact_code | exact_name | prefix_name | contains].copy()
    if candidates.empty:
        return {
            "query": query_text,
            "match_count": 0,
            "candidates": [],
            "rule_ko": "검색어와 일치하는 상권 후보가 없으면 엔진으로 넘기지 않는다.",
        }

    candidates["_rank"] = 4
    candidates.loc[exact_code.loc[candidates.index], "_rank"] = 0
    candidates.loc[exact_name.loc[candidates.index], "_rank"] = 1
    candidates.loc[prefix_name.loc[candidates.index], "_rank"] = candidates.loc[prefix_name.loc[candidates.index], "_rank"].clip(upper=2)
    candidates = candidates.sort_values(["_rank", "상권_구분_코드_명", "상권_코드_명", "상권_코드"])

    reason_by_rank = {
        0: "상권코드 정확일치",
        1: "상권명/표시명 정확일치",
        2: "상권명/표시명 전방일치",
        4: "검색텍스트 부분일치",
    }
    rows = [
        location_lookup_row(row, reason_by_rank.get(int(row["_rank"]), "검색텍스트 부분일치"))
        for _, row in candidates.head(limit).iterrows()
    ]
    return {
        "query": query_text,
        "match_count": int(len(candidates)),
        "returned_count": len(rows),
        "candidates": rows,
        "rule_ko": "화면 후보는 사람이 고르는 용도이고, 엔진 입력은 선택된 상권_코드 하나만 허용한다.",
    }


def resolve_location_name_to_code(name: str, data) -> tuple[str, dict[str, Any]]:
    query = normalize_text(name)
    if not query:
        raise RuntimeError("위치 입력이 비어 있습니다.")
    locations = data.locations.copy()
    name_col = "상권_코드_명"
    display_col = "display_label"
    locations["_name_norm"] = locations[name_col].map(normalize_text)
    locations["_display_norm"] = locations[display_col].map(normalize_text)

    exact = locations[(locations["_name_norm"] == query) | (locations["_display_norm"] == query)]
    if len(exact) == 1:
        row = exact.iloc[0]
        return str(row["상권_코드"]), {
            "mode": "trade_area_name_exact",
            "status": "single_name_confirmed",
            "match_count": 1,
            "trade_area_name": row[name_col],
        }
    if len(exact) > 1:
        raise RuntimeError(f"상권명이 여러 개와 정확히 일치합니다. 후보를 먼저 선택해야 합니다: {len(exact)}건")

    partial = locations[
        locations["_name_norm"].str.contains(query, na=False)
        | locations["_display_norm"].str.contains(query, na=False)
    ]
    if len(partial) == 1:
        row = partial.iloc[0]
        return str(row["상권_코드"]), {
            "mode": "trade_area_name_partial",
            "status": "single_name_confirmed",
            "match_count": 1,
            "trade_area_name": row[name_col],
        }
    if len(partial) > 1:
        sample = partial[[name_col, "상권_코드"]].head(5).to_dict("records")
        raise RuntimeError(f"상권명 검색 결과가 여러 개입니다. 후보를 먼저 선택해야 합니다: {len(partial)}건 / 예: {sample}")
    raise RuntimeError(f"상권명을 찾지 못했습니다: {name}")


def resolve_location_payload_to_code(payload: dict[str, Any], parsed_location: dict[str, Any], data) -> tuple[str, dict[str, Any]]:
    trade_area_code = payload.get("trade_area_code") or parsed_location.get("trade_area_code")
    if trade_area_code:
        rows = data.locations[data.locations["상권_코드"].astype(str) == str(trade_area_code)]
        if len(rows) != 1:
            raise RuntimeError(f"상권코드를 찾지 못했습니다: {trade_area_code}")
        row = rows.iloc[0]
        return str(trade_area_code), {
            "mode": "trade_area_code_exact",
            "status": "single_code_confirmed",
            "match_count": 1,
            "trade_area_name": row.get("상권_코드_명"),
        }

    lat = payload.get("lat", parsed_location.get("lat"))
    lng = payload.get("lng", parsed_location.get("lng"))
    if lat not in (None, "") and lng not in (None, ""):
        resolved = resolve_location(float(lng), float(lat), data)
        status = resolved.get("location_resolution_status")
        if status == "single_inside_confirmed" and resolved.get("inside_polygon_count") == 1:
            row = resolved["resolved_trade_areas"][0]
            return str(row["trade_area_code"]), {
                "mode": "coordinate_polygon",
                "status": status,
                "inside_polygon_count": resolved.get("inside_polygon_count"),
                "nearest_candidate_count": len(resolved.get("nearest_candidates", [])),
                "boundary_candidate_count": len(resolved.get("nearby_boundary_candidates", [])),
                "trade_area_name": row.get("trade_area_name"),
            }
        if status == "multiple_inside_candidates":
            names = [item.get("trade_area_name") for item in resolved.get("resolved_trade_areas", [])]
            raise RuntimeError(f"좌표가 여러 상권에 포함됩니다. 후보를 먼저 선택해야 합니다: {names}")
        raise RuntimeError("좌표가 서울 상권 polygon 밖입니다. 가까운 후보를 먼저 선택해야 합니다.")

    trade_area_name = payload.get("trade_area_name") or parsed_location.get("trade_area_name")
    if trade_area_name:
        return resolve_location_name_to_code(str(trade_area_name), data)

    raise RuntimeError("위치 입력을 상권코드, 상권명, 또는 좌표로 확정하지 못했습니다.")


def resolve_industry_payload_to_code(payload: dict[str, Any], parsed_industry: dict[str, Any], data) -> tuple[str, dict[str, Any]]:
    industry_query = payload.get("industry_code") or parsed_industry.get("industry_code") or payload.get("industry_name") or parsed_industry.get("industry_name")
    if not industry_query:
        raise RuntimeError("업종 입력이 비어 있습니다.")
    resolved = resolve_industry(str(industry_query), data)
    matches = resolved.get("matches", [])
    if resolved.get("match_count") != 1 or not matches:
        raise RuntimeError(f"업종 검색 결과가 여러 개이거나 없습니다. 세부 업종을 먼저 선택해야 합니다: {resolved.get('match_count')}건")
    match = matches[0]
    if not match.get("direct_score_allowed"):
        raise RuntimeError(f"선택한 업종은 현재 직접 점수 산정 대상이 아닙니다: {match.get('service_industry_name')}")
    return str(match["service_industry_code"]), {
        "mode": match.get("match_type"),
        "status": "single_industry_confirmed",
        "match_count": resolved.get("match_count"),
        "industry_name": match.get("service_industry_name"),
        "score_use_status": match.get("score_use_status"),
    }


def build_engine_args(payload: dict[str, Any]) -> SimpleNamespace:
    location = parse_location(payload.get("location"))
    industry = parse_industry(payload.get("industry"))
    data = get_resolver_data()
    trade_area_code, location_context = resolve_location_payload_to_code(payload, location, data)
    industry_code, industry_context = resolve_industry_payload_to_code(payload, industry, data)
    return SimpleNamespace(
        trade_area_code=trade_area_code,
        trade_area_name=None,
        industry_code=industry_code,
        industry_name=None,
        budget_krw=payload.get("budget_krw") or payload.get("budget"),
        lat=None,
        lng=None,
        quarter=int(payload["quarter"]) if payload.get("quarter") not in (None, "") else None,
        output=None,
        resolver_context={
            "resolver_version": RESOLVER_VERSION,
            "location": location_context,
            "industry": industry_context,
            "engine_input_contract": "상권_코드 + 서비스_업종_코드 only",
        },
    )


def build_developer_prompt() -> str:
    return """
너는 서울 상권 입지판단 상세리포트를 작성하는 한국어 분석가다.

입력은 이미 검증된 판단엔진 JSON이다. 너는 계산기가 아니다.
반드시 JSON 안의 matched_target, score_result, score_result.components.evidence,
score_result.candidate_signals, evidence_pack, warnings만 근거로 사용한다.

출력은 Markdown만 작성한다. 코드블록, JSON, HTML은 쓰지 않는다.
분량은 일반 A4 문서 반 페이지 정도로, 너무 길게 쓰지 않는다.
목표 길이는 한국어 1300~1500자, 5개 안팎의 짧은 섹션이다.

반드시 지킬 표현:
- 점수는 "매출 체력형 입지 비교 점수"로 설명한다.
- 과거 427,553개 엄격 라벨 백테스트 기준으로 다음분기 매출 규모와
  동업종 내 매출 상위권 선별에 근거가 있다고 설명한다.
- growth_rebound_candidate_score가 있으면 "초과성장/반등 후보 신호"로만 설명한다.
- growth_rebound_candidate_score는 현재입지 점수, 매출 수준 점수, 성장률 예측값이 아니다.
- 객단가 evidence가 있어도 점수 산식에 들어간 근거로 설명하지 않는다.
- 객단가는 `score_contribution_status=excluded_from_sales_axis`인 경우 소비 단가 수준 참고값이며,
  고객 구매력, 성장률, 성공확률, 매출 상승 보장 근거가 아니다.
- 성장률 선별, 개별 매장 성과, 월세/권리금 수익성은 한계로 분리한다.

절대 쓰면 안 되는 표현:
- 창업 성공확률
- 성공 보장
- 개별 매장 매출 보장
- 성장률 높은 상권 보장
- 성장률 예측
- 성장률 보장
- 고객 구매력 보장
- 매출 상승 보장
- 월세/권리금까지 반영한 수익성 확정
- JSON에 없는 숫자나 출처 없는 주장
- "추천", "권장", "적합" 같은 단정적 표현
- 점수를 직접적으로 표시 하지 않는다. (예: "점수 85점" 대신 "상위권에 속하는 입지로 해석된다" 등)


Markdown 구조:
# 상권 입지 상세 리포트
## 1. 분석 대상(상권코드, 업종코드 등 수치적 표현은 쓰지 않는다)
## 2. 종합 판단
## 3. 핵심 근거
## 4. 성장 반등 후보 신호와 한계
## 5. 리스크와 현장 확인 체크리스트

문체는 비전문가가 읽기 쉽게 쓴다. 단정 대신 "확인된다", "해석된다", "주의가 필요하다"처럼 신중한 표현을 쓴다.
""".strip()


def build_user_prompt(facts: dict[str, Any]) -> str:
    compact = {
        "matched_target": facts.get("matched_target"),
        "score_result": facts.get("score_result"),
        "reportfacts_compact": facts.get("reportfacts_compact"),
        "warnings": facts.get("warnings"),
        "evidence_pack": facts.get("evidence_pack"),
        "text_model_payload": facts.get("text_model_payload"),
    }
    return "아래 판단엔진 JSON만 근거로 한국어 Markdown 리포트를 작성해.\n\n" + json.dumps(
        compact,
        ensure_ascii=False,
        indent=2,
        default=json_default,
    )


def extract_output_text(response: dict[str, Any]) -> str:
    if isinstance(response.get("output_text"), str):
        return response["output_text"].strip()

    chunks: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("type") == "output_text" and isinstance(value.get("text"), str):
                chunks.append(value["text"])
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(response.get("output"))
    return "\n".join(chunks).strip()


def validate_markdown_contract(markdown: str, facts: dict[str, Any] | None = None) -> None:
    violations = [term for term in FORBIDDEN_MARKDOWN_TERMS if term in markdown]
    candidate_violations = validate_markdown_text(markdown, facts=facts)
    violations.extend(v["term"] for v in candidate_violations)
    violations = sorted(set(violations))
    if violations:
        raise RuntimeError(
            "AI 리포트 Markdown에 금지 표현이 포함되었습니다: " + ", ".join(violations)
        )


def build_dry_run_markdown(facts: dict[str, Any]) -> str:
    """HTTP smoke 전용 Markdown을 만든다.

    실제 LLM 호출을 대체하려는 기능이 아니라, 같은 facts payload가 Markdown 검증과
    다운로드 경로를 통과하는지 네트워크 없이 확인하기 위한 테스트 경로다.
    """
    target = facts.get("matched_target", {})
    score_result = facts.get("score_result", {})
    scores = facts.get("scores", {})
    axis_scores = scores.get("axis_scores", {})
    markdown = f"""# 상권 입지 상세 리포트

## 1. 분석 대상
분석 대상은 {target.get('trade_area_name', '상권명 미확인')}의 {target.get('industry_name', '업종명 미확인')}이다. 입력은 resolver를 거쳐 상권과 업종 코드로 확정된 뒤 판단엔진에 전달됐다.

## 2. 종합 판단
현재입지 등급은 {score_result.get('grade', '-')}로 확인된다. 이 값은 매출, 경쟁, 수요, 접근성 네 축을 기준으로 계산된 비교 지표이며 개별 점포의 결과를 단정하지 않는다.

## 3. 축별 근거
- 매출 축: {axis_scores.get('sales', '-')}
- 경쟁 축: {axis_scores.get('competition', '-')}
- 수요 축: {axis_scores.get('demand', '-')}
- 접근성 축: {axis_scores.get('accessibility', '-')}

## 4. 보조 신호와 한계
비용 리스크와 성장 반등 후보는 별도 참고 신호로만 확인한다. 리포트 본문에서는 공식 네 축과 보조 신호를 분리해서 읽어야 한다.

## 5. 현장 확인 항목
- 상권 경계와 실제 점포 후보지가 같은 생활권인지 확인한다.
- 임대 조건, 권리 관계, 점포 면적은 별도 자료로 확인한다.
- 유동 흐름과 경쟁 점포의 실제 상태는 현장에서 다시 확인한다.
"""
    markdown = normalize_quarter_codes(markdown.strip())
    validate_markdown_contract(markdown, facts=facts)
    return markdown


def dry_run_response(facts: dict[str, Any]) -> dict[str, Any]:
    return {
        "markdown": build_dry_run_markdown(facts),
        "model": "dry-run-local",
        "reasoning_effort": "none",
        "status": "dry_run",
        "usage": None,
        "response_id": None,
    }


def call_openai(facts: dict[str, Any]) -> dict[str, Any]:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY가 .env에 설정되어 있지 않습니다.")

    model = os.environ.get("OPENAI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    reasoning_effort = os.environ.get("OPENAI_REASONING_EFFORT", DEFAULT_REASONING_EFFORT).strip() or DEFAULT_REASONING_EFFORT
    max_output_tokens = int(os.environ.get("AI_REPORT_MAX_OUTPUT_TOKENS", DEFAULT_MAX_OUTPUT_TOKENS))
    timeout_seconds = float(os.environ.get("OPENAI_TIMEOUT_SECONDS", DEFAULT_OPENAI_TIMEOUT_SECONDS))

    body = {
        "model": model,
        "reasoning": {"effort": reasoning_effort},
        "max_output_tokens": max_output_tokens,
        "store": False,
        "input": [
            {
                "role": "developer",
                "content": [{"type": "input_text", "text": build_developer_prompt()}],
            },
            {
                "role": "user",
                "content": [{"type": "input_text", "text": build_user_prompt(facts)}],
            },
        ],
    }

    req = urllib.request.Request(
        OPENAI_RESPONSES_URL,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as res:
            raw = res.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI API 오류 {exc.code}: {raw}") from exc

    data = json.loads(raw)
    markdown = normalize_quarter_codes(extract_output_text(data))
    if data.get("status") == "incomplete":
        raise RuntimeError(
            "OpenAI response was incomplete. "
            f"incomplete_details={data.get('incomplete_details')}, usage={data.get('usage')}"
        )
    if not markdown:
        raise RuntimeError(
            "OpenAI 응답에서 Markdown 본문을 찾지 못했습니다. "
            f"status={data.get('status')}, incomplete_details={data.get('incomplete_details')}, "
            f"usage={data.get('usage')}"
        )
    validate_markdown_contract(markdown, facts=facts)

    return {
        "markdown": markdown,
        "model": data.get("model", model),
        "reasoning_effort": reasoning_effort,
        "status": data.get("status"),
        "usage": data.get("usage"),
        "response_id": data.get("id"),
    }


def markdown_to_plain_blocks(markdown: str) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    for raw in markdown.splitlines():
        line = raw.strip()
        if not line:
            blocks.append(("space", ""))
            continue
        if line.startswith("# "):
            blocks.append(("title", line[2:].strip()))
        elif line.startswith("## "):
            blocks.append(("heading", line[3:].strip()))
        elif line.startswith("### "):
            blocks.append(("subheading", line[4:].strip()))
        elif line.startswith("- "):
            blocks.append(("bullet", line[2:].strip()))
        else:
            line = re.sub(r"\*\*(.*?)\*\*", r"\1", line)
            line = re.sub(r"`([^`]+)`", r"\1", line)
            blocks.append(("body", line))
    return blocks


def pdf_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def make_pdf(markdown: str) -> bytes:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    except Exception as exc:  # pragma: no cover - dependency guard
        raise RuntimeError("PDF 생성을 위해 reportlab 패키지가 필요합니다.") from exc

    font_name = "Helvetica"
    for font_path in [
        Path("C:/Windows/Fonts/malgun.ttf"),
        Path("C:/Windows/Fonts/NanumGothic.ttf"),
        Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
    ]:
        if font_path.exists():
            pdfmetrics.registerFont(TTFont("KoreanBase", str(font_path)))
            font_name = "KoreanBase"
            break

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title="서울 상권 AI 상세 리포트",
    )
    styles = getSampleStyleSheet()
    style_map = {
        "title": ParagraphStyle(
            "ReportTitle",
            parent=styles["Title"],
            fontName=font_name,
            fontSize=19,
            leading=25,
            textColor=colors.HexColor("#111827"),
            spaceAfter=7 * mm,
        ),
        "heading": ParagraphStyle(
            "ReportHeading",
            parent=styles["Heading2"],
            fontName=font_name,
            fontSize=13,
            leading=18,
            textColor=colors.HexColor("#1F2937"),
            spaceBefore=4 * mm,
            spaceAfter=2 * mm,
        ),
        "subheading": ParagraphStyle(
            "ReportSubheading",
            parent=styles["Heading3"],
            fontName=font_name,
            fontSize=11,
            leading=15,
            textColor=colors.HexColor("#374151"),
            spaceBefore=3 * mm,
            spaceAfter=1.5 * mm,
        ),
        "body": ParagraphStyle(
            "ReportBody",
            parent=styles["BodyText"],
            fontName=font_name,
            fontSize=9.5,
            leading=14,
            textColor=colors.HexColor("#111827"),
            spaceAfter=2 * mm,
        ),
        "bullet": ParagraphStyle(
            "ReportBullet",
            parent=styles["BodyText"],
            fontName=font_name,
            fontSize=9.3,
            leading=13.5,
            leftIndent=5 * mm,
            firstLineIndent=-3 * mm,
            textColor=colors.HexColor("#111827"),
            spaceAfter=1.8 * mm,
        ),
    }

    story = []
    for kind, text in markdown_to_plain_blocks(markdown):
        if kind == "space":
            story.append(Spacer(1, 2.5 * mm))
        elif kind == "bullet":
            story.append(Paragraph(f"- {pdf_escape(text)}", style_map["bullet"]))
        else:
            story.append(Paragraph(pdf_escape(text), style_map.get(kind, style_map["body"])))
    doc.build(story)
    return buffer.getvalue()


class AiReportHandler(BaseHTTPRequestHandler):
    server_version = "SeoulAiReportServer/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False, default=json_default).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_pdf(self, payload: bytes, filename: str) -> None:
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", filename).strip("_") or "ai-report.pdf"
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Disposition", f'attachment; filename="{safe_name}"')
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_OPTIONS(self) -> None:
        self._send_json(204, {})

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        if parsed.path == "/api/ai-report/health":
            self._send_json(
                200,
                {
                    "ok": True,
                    "server": self.server_version,
                    "openai_key_configured": bool(os.environ.get("OPENAI_API_KEY")),
                    "score_version": SCORE_VERSION,
                    "input_resolver_version": RESOLVER_VERSION,
                    "report_contract_version": REPORT_CONTRACT_VERSION,
                    "lookup_contract_version": LOOKUP_CONTRACT_VERSION,
                    "model": os.environ.get("OPENAI_MODEL", DEFAULT_MODEL),
                    "reasoning_effort": os.environ.get("OPENAI_REASONING_EFFORT", DEFAULT_REASONING_EFFORT),
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                },
            )
            return
        if parsed.path == "/api/ai-report/lookups/industries":
            tree = get_industry_selection_tree()
            self._send_json(
                200,
                {
                    "ok": True,
                    "lookup_contract_version": LOOKUP_CONTRACT_VERSION,
                    "input_resolver_version": RESOLVER_VERSION,
                    "tree": tree,
                    "rule_ko": "업종 계층은 UI 선택 보조이고 엔진에는 서비스_업종_코드 하나만 전달한다.",
                },
            )
            return
        if parsed.path == "/api/ai-report/lookups/locations":
            q = query.get("q", [""])[0]
            limit = parse_limit(query.get("limit", ["20"])[0])
            result = search_location_candidates(q, limit=limit)
            self._send_json(
                200,
                {
                    "ok": True,
                    "lookup_contract_version": LOOKUP_CONTRACT_VERSION,
                    "input_resolver_version": RESOLVER_VERSION,
                    **result,
                },
            )
            return
        self._send_json(404, {"ok": False, "error": "not_found"})

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if not parsed.path.startswith("/api/ai-report"):
            self._send_json(404, {"ok": False, "error": "not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            if parsed.path == "/api/ai-report/pdf":
                markdown = payload.get("markdown", "")
                if not isinstance(markdown, str) or not markdown.strip():
                    raise RuntimeError("PDF로 변환할 Markdown 본문이 없습니다.")
                validate_markdown_contract(markdown)
                self._send_pdf(make_pdf(markdown), payload.get("filename", "seoul-ai-report.pdf"))
                return
            if parsed.path == "/api/ai-report/resolve":
                args = build_engine_args(payload)
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "lookup_contract_version": LOOKUP_CONTRACT_VERSION,
                        "input_resolver_version": RESOLVER_VERSION,
                        "engine_input": {
                            "trade_area_code": args.trade_area_code,
                            "industry_code": args.industry_code,
                            "budget_krw": args.budget_krw,
                            "quarter": args.quarter,
                        },
                        "input_resolver_context": args.resolver_context,
                        "rule_ko": "resolve endpoint는 LLM 호출 전 화면 입력이 엔진 코드 입력으로 확정되는지만 확인한다.",
                    },
                )
                return
            args = build_engine_args(payload)
            facts = attach_optional_candidate_evidence(build_output(args))
            facts["input_resolver_context"] = args.resolver_context
            if payload.get("dry_run") or os.environ.get("AI_REPORT_DRY_RUN") == "1":
                llm = dry_run_response(facts)
            else:
                llm = call_openai(facts)
            self._send_json(
                200,
                {
                    "ok": True,
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                    "markdown": llm["markdown"],
                    "facts": facts,
                    "score_version": facts.get("score_version"),
                    "report_contract_version": REPORT_CONTRACT_VERSION,
                    "model": llm["model"],
                    "reasoning_effort": llm["reasoning_effort"],
                    "llm_status": llm["status"],
                    "usage": llm["usage"],
                    "response_id": llm["response_id"],
                },
            )
        except Exception as exc:
            if os.environ.get("AI_REPORT_DEBUG") == "1":
                traceback.print_exc()
            else:
                self.log_message("request failed: %s", exc)
            self._send_json(500, {"ok": False, "error": str(exc)})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="서울 상권 AI 상세리포트 API 서버")
    parser.add_argument("--host", default=os.environ.get("AI_REPORT_SERVER_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("AI_REPORT_SERVER_PORT", "8787")))
    return parser.parse_args()


def main() -> None:
    load_dotenv(ROOT / ".env")
    args = parse_args()
    httpd = ThreadingHTTPServer((args.host, args.port), AiReportHandler)
    print(
        json.dumps(
            {
                "ok": True,
                "url": f"http://{args.host}:{args.port}/api/ai-report",
                "health": f"http://{args.host}:{args.port}/api/ai-report/health",
                "model": os.environ.get("OPENAI_MODEL", DEFAULT_MODEL),
                "reasoning_effort": os.environ.get("OPENAI_REASONING_EFFORT", DEFAULT_REASONING_EFFORT),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    httpd.serve_forever()


if __name__ == "__main__":
    main()
