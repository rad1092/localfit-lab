from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import sqlite3
from copy import deepcopy
from datetime import datetime
from typing import Any, Literal

from langchain_core.callbacks import UsageMetadataCallbackHandler
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from app.ai.recursive_layer import get_llm, get_openai_model
from app.services.evidence_retriever import evidence_pack_for_prompt, retrieve_evidence_pack
from app.services.indicator_pack import (
    DB_PATH,
    build_indicator_pack,
    public_axis_labels,
    public_coverage_context,
    public_coverage_header,
    public_coverage_reason,
    public_coverage_tier,
)
from app.services.korean import josa, with_josa
from app.services.llm_runtime_settings import get_report_reasoning_effort
from app.services.news_evidence import (
    DECISION_SUPPORT_TIER,
    NaverNewsConnectionError,
    REFERENCE_MONITORING_TIER,
    fetch_live_naver_news,
    merge_news_evidence_rows,
    news_evidence_version,
    retrieve_news_evidence_tiers,
)
from app.services.report_critic import validate_comparison_draft, validate_report_draft


logger = logging.getLogger(__name__)


class ReportGenerationError(RuntimeError):
    """Provider/structured-output failure that must not be presented as a report."""

    def __init__(self, stage: str, cause: Exception):
        self.stage = stage
        self.error_type = type(cause).__name__
        self.provider_message = str(cause)
        super().__init__(f"AI report generation failed during {stage}")


SPEC_VERSION = "ai-report-chain.v1.9.0.20260723-two-tier-news"
NEWS_EVIDENCE_THEME = "최근 정책·지역 이슈"

# 고정 텍스트: LLM이 생성·수정할 수 없다. invoke/repair 후 강제 재할당된다.
# 문구는 금지 패턴·sanitize 치환에 걸리지 않는 표현으로만 작성한다.
METHODOLOGY_NOTES = [
    "입지 종합 등급은 시장성·경쟁 구조·수요 기반·접근·유입의 4축을 서울 동일 업종 기준으로 비교해 산정합니다.",
    "상권 맥락 등급은 업종을 선택하기 전 수요·접근성 기준의 서울 상대 등급입니다.",
    "분석 단위는 서울시 상권×업종×분기이며 A+부터 E까지 같은 기준군 안에서 비교합니다.",
    "R-ONE 임대료·공실률은 표시된 지역 매핑 범위의 참고값이고, RTMS 값은 임대료가 아닌 상업용 부동산 매매가격 프록시입니다.",
]

LIMITATIONS = [
    "공공 데이터의 공개 시차 때문에 최근 개점·폐점이나 보행 동선 변화가 아직 반영되지 않았을 수 있습니다.",
    "점포별 임대료·권리금·관리비와 실제 매장 전면 조건은 포함하지 않습니다.",
]

AXIS_LABELS = {
    "sales": "시장성",
    "competition": "경쟁 구조",
    "demand": "수요 기반",
    "accessibility": "접근·유입",
}

DISPLAY_GRADE_RE = re.compile(r"^[A-E](?:\+)?$")


def _display_grade(*values: Any, fallback: str = "등급 보류") -> str:
    """Use only a grade computed by the backend; never reconstruct one from a score."""
    for value in values:
        candidate = str(value or "").strip().upper()
        if DISPLAY_GRADE_RE.fullmatch(candidate):
            return candidate
    return fallback

PUBLIC_SOURCE_CATALOG: dict[str, dict[str, str]] = {
    "score_model": {
        "provider": "입지봇 분석 모델",
        "dataset_name": "서울 상권·업종 입지 평가",
        "source_url": "",
        "granularity": "상권×업종",
        "theme": "산정 결과",
        "used_for": "공식 4축 완전 관측 시 입지점수, 판단 등급, 동일 업종 내 비교 위치",
        "caveat": "공공 원천 지표를 표준화해 계산한 비교 점수",
    },
    "seoul_sales_trade_area": {
        "provider": "서울특별시·서울 열린데이터광장",
        "dataset_name": "서울 상권분석서비스 추정매출-상권",
        "source_url": "https://data.seoul.go.kr/dataList/OA-15572/S/1/datasetView.do",
        "granularity": "상권×업종×분기",
        "theme": "시장성",
        "used_for": "매출 규모, 결제 건수, 객단가, 업종 내 순위와 분기 추이",
        "caveat": "개별 점포 매출이 아닌 카드 기반 추정·집계값",
    },
    "seoul_store_trade_area": {
        "provider": "서울특별시·서울 열린데이터광장",
        "dataset_name": "서울 상권분석서비스 점포-상권",
        "source_url": "https://data.seoul.go.kr/dataList/OA-15577/A/1/datasetView.do",
        "granularity": "상권×업종×분기",
        "theme": "경쟁 구조",
        "used_for": "동일 업종 점포 수, 점포 밀도, 개·폐업과 경쟁 강도",
        "caveat": "점포 수 자체는 우수·열위의 단독 판단 기준이 아님",
    },
    "seoul_floating_population_trade_area": {
        "provider": "서울특별시·서울 열린데이터광장",
        "dataset_name": "서울 상권분석서비스 길단위인구-상권",
        "source_url": "https://data.seoul.go.kr/dataList/OA-15568/S/1/datasetView.do",
        "granularity": "상권×분기×시간대",
        "theme": "수요 기반",
        "used_for": "유동인구 규모, 시간대·연령대·주중/주말 수요 구성",
        "caveat": "실제 방문자 수가 아닌 통신·공간 정보를 활용한 추정 집계값",
    },
    "seoul_resident_population_trade_area": {
        "provider": "서울특별시·서울 열린데이터광장",
        "dataset_name": "서울 상권분석서비스 상주인구-상권",
        "source_url": "https://data.seoul.go.kr/dataList/OA-15584/A/1/datasetView.do",
        "granularity": "상권×분기",
        "theme": "수요 기반",
        "used_for": "주거형 수요의 규모와 생활권 기반 수요",
        "caveat": "상주인구는 특정 업종의 실제 구매 고객 수가 아님",
    },
    "seoul_worker_population_trade_area": {
        "provider": "서울특별시·서울 열린데이터광장",
        "dataset_name": "서울 상권분석서비스 직장인구-상권",
        "source_url": "https://data.seoul.go.kr/dataList/OA-15569/S/1/datasetView.do",
        "granularity": "상권×분기",
        "theme": "수요 기반",
        "used_for": "업무형 수요의 규모와 평일 기반 수요",
        "caveat": "직장인구는 해당 상권의 실제 방문·구매 인원을 직접 나타내지 않음",
    },
    "seoul_facility_trade_area": {
        "provider": "서울특별시·서울 열린데이터광장",
        "dataset_name": "서울 상권분석서비스 집객시설-상권",
        "source_url": "https://data.seoul.go.kr/dataList/OA-15580/S/1/datasetView.do",
        "granularity": "상권×기준연도",
        "theme": "접근·유입",
        "used_for": "교통·학교·병원·공공기관 등 집객시설 접근성",
        "caveat": "시설 수는 실제 방문객 수가 아닌 접근성 보조지표",
    },
    "seoul_living_migration": {
        "provider": "서울특별시·서울 생활이동",
        "dataset_name": "서울 생활이동 OD",
        "source_url": "https://data.seoul.go.kr/dataVisual/seoul/seoulLivingMigration.do",
        "granularity": "자치구 OD×월×시간대",
        "theme": "접근·유입",
        "used_for": "출근·점심·퇴근·야간 외부 유입 방향",
        "caveat": "자치구 단위 보조지표로 개별 상권의 실제 방문을 직접 나타내지 않음",
    },
    "molit_rtms_commercial_trade": {
        "provider": "국토교통부·공공데이터포털",
        "dataset_name": "상업·업무용 부동산 매매 실거래",
        "source_url": "https://www.data.go.kr/data/15126463/openapi.do",
        "granularity": "시군구×법정동×거래월",
        "theme": "비용 부담",
        "used_for": "상업용 부동산 가격 수준과 비용 압력 방향",
        "caveat": "임대료가 아니라 건물면적당 매매 거래가격을 이용한 프록시",
    },
    "reb_small_shop_rent": {
        "provider": "한국부동산원 R-ONE",
        "dataset_name": "상업용부동산 임대동향 지역별 임대료·공실률",
        "source_url": "https://www.reb.or.kr/r-one/",
        "granularity": "지역×상가유형×분기",
        "theme": "비용 참고",
        "used_for": "표시된 지역 매핑 범위의 임대료·공실률과 33㎡·12개월 참고 환산",
        "caveat": "개별 점포 직접값이 아니며 지역명 후보 매핑 또는 서울 기준선임",
    },
}

AXIS_KEYS = {
    "sales": "axis_sales",
    "competition": "axis_competition",
    "demand": "axis_demand",
    "accessibility": "axis_accessibility",
}

FORBIDDEN_CLAIMS = [
    "창업 성공확률",
    "성공확률",
    "매출 보장",
    "매출을 보장",
    "성장률 보장",
    "성장 보장",
    "월세가 확정",
    "권리금이 확정",
    "수익성 보장",
]


class EvidenceCitation(BaseModel):
    title: str = ""
    source_path: str = ""
    provider: str = ""
    dataset_name: str = ""
    source_url: str = ""
    period: str = ""
    granularity: str = ""
    theme: str = ""
    used_for: str = ""
    caveat: str = ""


class AxisNarrative(BaseModel):
    axis: str = Field(description="Axis label")
    score: float | None = Field(default=None, description="Rule-engine axis score; null means the axis is not observed.")
    score_display: str = ""
    display_grade: str = ""
    interpretation_level: str = Field(description="Qualitative reading of the numeric axis score")
    evidence_metrics: list[str] = Field(default_factory=list)
    chart_id: str = "C1"
    meaning: str = Field(description="Why this score came out, citing the actual indicators")
    evidence: str = Field(description="Concrete source indicators and values used for the reading")
    risk: str = Field(description="Practical caveat or what the indicator cannot prove")
    action: str = Field(description="Concrete next field check for the founder")
    next_check: str = ""
    frame_citations: list[int] = Field(default_factory=list)


class SingleInterpretation(BaseModel):
    header_block: dict[str, Any] = Field(default_factory=dict)
    narrative_title: str
    thesis: list[str] = Field(default_factory=list)
    executive_interpretation: str
    score_interpretation: str
    axis_interpretations: list[AxisNarrative] = Field(default_factory=list)
    trend_analysis: str = ""
    alternatives: list[dict[str, Any]] = Field(default_factory=list)
    user_fit: str = ""
    evidence_basis: list[str] = Field(default_factory=list)
    source_citations: list[EvidenceCitation] = Field(default_factory=list)
    claim_source_map: list[dict[str, Any]] = Field(default_factory=list)
    methodology_notes: list[str] = Field(default_factory=list)
    action_plan: list[str] = Field(default_factory=list)
    onsite_checklist: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    chart_manifest: list[dict[str, Any]] = Field(default_factory=list)
    original_validation_issues: list[str] = Field(default_factory=list)
    validation_issues: list[str] = Field(default_factory=list)
    quality_warnings: list[str] = Field(default_factory=list)
    quality_status: str = "unchecked"
    generation_mode: Literal["llm", "partial_fallback", "deterministic"] = "llm"
    fallback_fields: list[str] = Field(default_factory=list)
    summary: str
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    recommended_businesses: list[str] = Field(default_factory=list)
    risk_factors: list[str] = Field(default_factory=list)


class HeaderSectionPatch(BaseModel):
    header_block: dict[str, Any] = Field(default_factory=dict)
    narrative_title: str = ""
    thesis: list[str] = Field(default_factory=list)
    executive_interpretation: str = ""
    score_interpretation: str = ""
    summary: str = ""


class AxisSectionPatch(BaseModel):
    axis_interpretations: list[AxisNarrative] = Field(default_factory=list)


class TrendAlternativeSectionPatch(BaseModel):
    trend_analysis: str = ""
    alternatives: list[dict[str, Any]] = Field(default_factory=list)


class UserRiskSectionPatch(BaseModel):
    # limitations는 고정 상수(LIMITATIONS)라 repair 대상에서 제외한다.
    user_fit: str = ""
    action_plan: list[str] = Field(default_factory=list)
    onsite_checklist: list[str] = Field(default_factory=list)
    risk_factors: list[str] = Field(default_factory=list)


class SourceSectionPatch(BaseModel):
    evidence_basis: list[str] = Field(default_factory=list)
    source_citations: list[EvidenceCitation] = Field(default_factory=list)
    methodology_notes: list[str] = Field(default_factory=list)


class FieldTextPatch(BaseModel):
    replacement: str = ""


class ComparisonRow(BaseModel):
    area_name: str
    interpretation_level: str
    strong_axis: str
    watch_axis: str
    interpretation: str


class ComparisonInterpretation(BaseModel):
    narrative_title: str
    executive_interpretation: str
    comparison_matrix: list[ComparisonRow] = Field(default_factory=list)
    evidence_basis: list[str] = Field(default_factory=list)
    source_citations: list[EvidenceCitation] = Field(default_factory=list)
    methodology_notes: list[str] = Field(default_factory=list)
    action_plan: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    summary: str
    top_recommendation_reason: str


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str, indent=2)


def _estimate_tokens(text: str) -> int:
    return max(1, int(len(text) / 3.2))


def _provider_token_usage(handler: UsageMetadataCallbackHandler, *, fallback_model: str) -> dict[str, Any] | None:
    usage_metadata = getattr(handler, "usage_metadata", None) or {}
    if not usage_metadata:
        return None

    input_tokens = 0
    output_tokens = 0
    total_tokens = 0
    input_details: dict[str, Any] = {}
    output_details: dict[str, Any] = {}
    provider_models: list[str] = []
    for model_name, usage in usage_metadata.items():
        provider_models.append(model_name)
        input_tokens += int(usage.get("input_tokens") or 0)
        output_tokens += int(usage.get("output_tokens") or 0)
        total_tokens += int(usage.get("total_tokens") or 0)
        for key, value in (usage.get("input_token_details") or {}).items():
            input_details[key] = int(input_details.get(key) or 0) + int(value or 0)
        for key, value in (usage.get("output_token_details") or {}).items():
            output_details[key] = int(output_details.get(key) or 0) + int(value or 0)

    return {
        "estimated": False,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "model": fallback_model,
        "provider_models": provider_models,
        "input_token_details": input_details,
        "output_token_details": output_details,
        "cache_read_tokens": int(input_details.get("cache_read") or 0),
        "cache_hit": False,
        "spec_version": SPEC_VERSION,
    }


def _cache_key(payload: dict[str, Any]) -> str:
    user_condition = payload.get("user_condition") or {}
    key_data = {
        "area_code": str(payload.get("area_code") or ""),
        "industry_code": str(payload.get("industry_code") or ""),
        "quarter": str(payload.get("quarter") or ""),
        "user_condition": user_condition,
        "news_evidence_version": str(payload.get("_news_evidence_version") or "no-news"),
        "model": str(payload.get("_report_model") or get_openai_model()),
        "reasoning_effort": str(
            payload.get("_report_reasoning_effort") or get_report_reasoning_effort()
        ),
        "spec_version": SPEC_VERSION,
    }
    serialized = json.dumps(key_data, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _ensure_cache_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_report_generation_cache (
            cache_key TEXT PRIMARY KEY,
            area_code TEXT,
            industry_code TEXT,
            quarter TEXT,
            user_condition_hash TEXT,
            spec_version TEXT,
            report_json TEXT NOT NULL,
            token_usage_json TEXT,
            created_at TEXT
        )
        """
    )
    conn.commit()


def _read_cache(payload: dict[str, Any]) -> dict[str, Any] | None:
    key = _cache_key(payload)
    with sqlite3.connect(DB_PATH) as conn:
        _ensure_cache_table(conn)
        row = conn.execute(
            "SELECT report_json, token_usage_json, created_at FROM ai_report_generation_cache WHERE cache_key = ?",
            (key,),
        ).fetchone()
    if not row:
        return None
    try:
        data = json.loads(row[0])
    except Exception:
        return None
    generation_mode = data.get("generation_mode")
    if (
        generation_mode not in {"llm", "partial_fallback"}
        or not bool(data.get("ai_generated"))
        or data.get("quality_status") != "pass"
        or bool(data.get("validation_issues"))
    ):
        return None
    token_usage = json.loads(row[1]) if row[1] else {}
    token_usage["cache_hit"] = True
    data["cache_meta"] = {
        "cache_hit": True,
        "cache_key": key,
        "created_at": row[2],
        "spec_version": SPEC_VERSION,
        "cacheable": True,
        "token_usage": token_usage,
    }
    data["token_usage"] = token_usage
    return data


def _write_cache(payload: dict[str, Any], data: dict[str, Any]) -> None:
    if (
        data.get("generation_mode") not in {"llm", "partial_fallback"}
        or not bool(data.get("ai_generated"))
        or data.get("quality_status") != "pass"
        or bool(data.get("validation_issues"))
    ):
        return
    key = _cache_key(payload)
    user_condition = payload.get("user_condition") or {}
    condition_hash = hashlib.sha256(
        json.dumps(user_condition, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    token_usage = data.get("token_usage") or {}
    cache_data = dict(data)
    cache_data["cache_meta"] = {
        "cache_hit": False,
        "cache_key": key,
        "created_at": datetime.now().isoformat(),
        "spec_version": SPEC_VERSION,
        "cacheable": True,
        "token_usage": token_usage,
    }
    with sqlite3.connect(DB_PATH) as conn:
        _ensure_cache_table(conn)
        conn.execute(
            """
            INSERT OR REPLACE INTO ai_report_generation_cache
            (cache_key, area_code, industry_code, quarter, user_condition_hash, spec_version, report_json, token_usage_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                key,
                str(payload.get("area_code") or ""),
                str(payload.get("industry_code") or ""),
                str(payload.get("quarter") or ""),
                condition_hash,
                SPEC_VERSION,
                json.dumps(cache_data, ensure_ascii=False, default=str),
                json.dumps(token_usage, ensure_ascii=False, default=str),
                datetime.now().isoformat(),
            ),
        )
        conn.commit()


def _safe_float(value: Any) -> float:
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return round(number, 2)


def _band(score: float) -> str:
    if score >= 78:
        return "상위 검토권"
    if score >= 64:
        return "비교 우위권"
    if score >= 50:
        return "조건부 검토권"
    return "보류 검토권"


def _format_value(value: Any, unit: str = "") -> str:
    if value is None:
        return "없음"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if unit == "%" and abs(number) <= 1:
        number *= 100
    if abs(number) >= 100000000:
        text = f"{number / 100000000:.2f}억"
    elif abs(number) >= 10000:
        text = f"{number:,.0f}"
    elif number.is_integer():
        text = f"{number:,.0f}"
    else:
        text = f"{number:,.2f}"
    if unit == "percentile":
        return f"상위 기준 {text}백분위"
    return f"{text}{unit}" if unit else text


def _metric_sentence(metric: dict[str, Any]) -> str:
    # 내부 테이블 경로(source)는 본문에 쓰지 않는다. 원천 표기는 원천 데이터 섹션 담당.
    label = metric.get("label", "지표")
    value = metric.get("display") or _format_value(metric.get("raw"), metric.get("unit") or "")
    note = str(metric.get("note") or "")
    if note and not re.search(r"(DB\.|gold\.|FeatureMart|\.csv)", note):
        return f"{label} {value}({note})"
    return f"{label} {value}"


def _top_metrics(pack: dict[str, Any], axis_key: str, limit: int = 5) -> list[dict[str, Any]]:
    axis_pack = (pack.get("axis_indicator_pack") or {}).get(axis_key) or {}
    return [item for item in axis_pack.get("score_drivers") or [] if item and item.get("display")][:limit]


def _metric_text(pack: dict[str, Any], axis_key: str, limit: int = 5) -> str:
    metrics = _top_metrics(pack, axis_key, limit=limit)
    if not metrics:
        missing = ((pack.get("axis_indicator_pack") or {}).get(axis_key) or {}).get("missing") or []
        if missing:
            labels = public_axis_labels(missing)
            return f"{'·'.join(labels)} 지표가 이 상권·업종 조합에 집계되지 않아 판단을 보류합니다."
        return "이 축은 직접 인용 가능한 원천 지표가 집계되지 않아 점수 중심으로 읽습니다."
    return "; ".join(_metric_sentence(item) for item in metrics)


def _axis_score(pack: dict[str, Any], axis_key: str) -> float | None:
    return _optional_float((pack.get("axis_scores") or {}).get(axis_key))


def _best_and_watch_axis(pack: dict[str, Any]) -> tuple[str, str]:
    scores = {axis: score for axis in AXIS_LABELS if (score := _axis_score(pack, axis)) is not None}
    if not scores:
        return "demand", "accessibility"
    return max(scores, key=scores.get), min(scores, key=scores.get)


def _clean_source_label(source: str) -> str:
    lowered = source.lower()
    if "rule_location_score" in lowered:
        return "상권·업종 입지 점수 산정 테이블"
    if "rule_area_score_summary" in lowered:
        return "상권 수요·접근성 맥락 점수 요약 테이블"
    if "district_sales" in lowered:
        return "상권 업종별 매출 집계"
    if "district_store_count" in lowered:
        return "상권 업종별 점포 수 집계"
    if "district_population" in lowered:
        return "상주·직장 인구 집계"
    if "district_floating" in lowered:
        return "유동인구 집계"
    if "area_rone_cost_reference" in lowered:
        return "R-ONE 임대료·공실률 지역 참고 지표"
    if "area_sale_price_proxy" in lowered:
        return "RTMS 상업용 부동산 매매가 프록시"
    if "gold" in lowered or "featuremart" in lowered:
        return "공공 원천 결합 검증 데이터"
    if "docs/" in lowered or "research/" in lowered:
        return "수집·검증 문서"
    return source.replace("DB.", "").replace("gold.", "").replace("_", " ").strip() or "근거 자료"


def _source_used_for(source: str) -> str:
    lowered = source.lower()
    if "rule_location_score" in lowered:
        return "공식 축 점수와 판단 라벨 확인"
    if "rule_area_score_summary" in lowered:
        return "업종 미입력 시 상권 수요·접근성 맥락 확인"
    if "district_sales" in lowered:
        return "매출 규모와 업종 내 위치 확인"
    if "district_store_count" in lowered:
        return "점포 밀도와 경쟁 강도 확인"
    if "district_population" in lowered or "district_floating" in lowered:
        return "수요 기반과 유입 규모 확인"
    if "area_rone_cost_reference" in lowered:
        return "임대료·공실률 지역 참고값과 매핑 범위 확인"
    if "area_sale_price_proxy" in lowered:
        return "상업용 부동산 매매가격 압력 방향 확인"
    return "리포트 해석에 필요한 근거 확인"


def _source_citations(pack: dict[str, Any]) -> list[EvidenceCitation]:
    sources = pack.get("data_sources") or []
    period = str(pack.get("data_period_text") or (pack.get("target") or {}).get("quarter") or "기준시점 별도 표기")
    citations: list[EvidenceCitation] = []
    for source in sources:
        source_key = str(source)
        meta = PUBLIC_SOURCE_CATALOG.get(source_key)
        if meta:
            citations.append(
                EvidenceCitation(
                    title=f"{meta['provider']} · {meta['dataset_name']}",
                    source_path=source_key,
                    provider=meta["provider"],
                    dataset_name=meta["dataset_name"],
                    source_url=meta["source_url"],
                    period=period,
                    granularity=meta["granularity"],
                    theme=meta["theme"],
                    used_for=meta["used_for"],
                    caveat=meta["caveat"],
                )
            )
            continue
        citations.append(
            EvidenceCitation(
                title=_clean_source_label(source_key),
                source_path=source_key,
                provider="공공 데이터 원천",
                dataset_name=_clean_source_label(source_key),
                period=period,
                theme="정량 지표 근거",
                used_for=_source_used_for(source_key),
            )
        )
    for idx, frame in enumerate(pack.get("evidence_frames") or [], start=1):
        citations.append(
            EvidenceCitation(
                title=f"[{idx}] {frame.get('title') or '해석 프레임'}",
                source_path=frame.get("source_path") or "",
                provider="분석 방법론 참고문헌",
                dataset_name=frame.get("title") or "해석 프레임",
                period="문헌 기준",
                granularity="방법론",
                theme="해석 기준",
                used_for="점수 산정값을 사실로 대체하지 않고 축 해석의 방법론 프레임으로만 사용",
                caveat="정량 수치의 원천이 아니라 해석 기준으로만 사용",
            )
        )
    return citations


def _frame_ids_for_axis(pack: dict[str, Any], axis_key: str) -> list[int]:
    preferred = {
        "sales": {"mcda_wlc", "source_data", "data_quality"},
        "competition": {"mcda_wlc", "source_data", "data_quality"},
        "demand": {"access_flow", "source_data", "data_quality"},
        "accessibility": {"access_flow", "mcda_wlc", "data_quality"},
    }.get(axis_key, {"mcda_wlc", "data_quality"})
    ids: list[int] = []
    for idx, frame in enumerate(pack.get("evidence_frames") or [], start=1):
        if frame.get("theme") in preferred:
            ids.append(idx)
        if len(ids) >= 2:
            break
    if not ids and pack.get("evidence_frames"):
        ids = [1]
    return ids


_AXIS_FALLBACK_TEXTS = {
    "sales": {
        "high": "상권·업종 합산 추정매출이 동일 업종 후보군 안에서 상위권입니다. 이는 관측된 시장 규모 신호이며 개별 점포 매출이나 창업 성과를 뜻하지 않습니다.",
        "mid": "매출 신호가 중간권이라 단독 근거보다는 다른 축과 묶어 읽어야 하는 수준입니다. 시장 크기보다 운영 조건이 성패를 가르는 구간에 가깝습니다.",
        "low": "매출 신호가 하위권이라 시장 크기 자체가 부담으로 작용하는 구조입니다. 다른 축이 강하더라도 이 축 때문에 보수적으로 접근할 여지가 큽니다.",
        "action": "동일 업종 후보 점포의 객단가, 회전율, 피크 시간 매출 동선을 현장에서 비교합니다.",
        "next_check": "최신 분기 매출과 점포 증감이 현재 현장 흐름과 같은 방향인지 대조합니다.",
        "risk": "매출 지표는 과거 분기 실적이라 현재 임대 조건이나 신규 경쟁 변화까지 담지 못합니다.",
    },
    "competition": {
        "high": "게시된 경쟁 구조 등급은 상대 비교 결과입니다. 점포 수와 상권 내 비중은 분모가 다른 관측값이므로 어느 하나만으로 경쟁이 낮다고 단정하지 않습니다.",
        "mid": "게시된 경쟁 구조 등급은 상대 비교 결과입니다. 점포 수만으로 과밀이나 경쟁 여유를 확정하지 않고 거리·서비스 범위와 함께 확인합니다.",
        "low": "게시된 경쟁 구조 등급은 상대 비교 결과입니다. 점포 수가 많아도 실제 중복 경쟁은 거리·가격·서비스 범위를 대조한 뒤 판단합니다.",
        "action": "반경 내 동종·유사 업종의 가격대, 대기 여부, 신규·폐업 흔적을 직접 확인합니다.",
        "next_check": "신규·폐업 흔적과 배달권 내 직접 경쟁점이 집계 점포수와 다른지 확인합니다.",
        "risk": "점포수가 많다는 사실은 수요의 방증이기도 해서 단독으로 불리하다고 단정하기 어렵습니다.",
    },
    "demand": {
        "high": "상주·직장·유동인구 집계가 상대적으로 큰 편입니다. 이용 접점의 가능성을 보여주는 신호지만 실제 방문·구매 전환이나 반복 이용을 입증하지는 않습니다.",
        "mid": "상주·직장·유동인구 집계가 중간권입니다. 이용 접점의 가능성만 보여주므로 통과형·체류형 동선과 실제 전환은 현장에서 확인해야 합니다.",
        "low": "상주·직장·유동인구 집계가 상대적으로 작은 편입니다. 업종 이용 수요가 부족하다고 확정하지 않고 외부 유입과 목적 방문 여부를 따로 확인합니다.",
        "action": "평일 점심, 퇴근, 주말 시간대를 나눠 보행량과 실제 구매 목적 방문을 대조합니다.",
        "next_check": "상주·직장·유동 수요 중 실제 구매로 이어지는 시간대와 방문 목적을 확인합니다.",
        "risk": "유동인구와 생활이동은 방문 가능성을 보여주지만 구매 전환까지 말해 주지는 않습니다.",
    },
    "accessibility": {
        "high": "직접 접근성 지표가 확인될 때에만 교통·보행 접근 가능성이 상대적으로 높다고 해석합니다. 실제 방문·매출 전환은 점포 전면과 동선 확인 전에는 알 수 없습니다.",
        "mid": "접근 가능성은 중간권 신호로만 읽습니다. 실제 이용 편의는 점포 전면, 층수, 출입 동선을 확인한 뒤 판단합니다.",
        "low": "접근 가능성은 상대적으로 낮은 신호로 읽습니다. 실제 유입 부족을 확정하지 않고 목적 방문·배달·보행 동선을 따로 확인합니다.",
        "action": "지하철 출구, 버스정류장, 횡단보도, 주출입 동선, 가시성, 주차 접근성을 현장에서 확인합니다.",
        "next_check": "출입구별 보행 흐름과 점포 전면 가시성이 시설 접근 지표와 실제로 맞는지 대조합니다.",
        "risk": "교통·시설 지표는 유입 가능성의 프록시라 매장 전면 가시성까지 대신 판단하지 못합니다.",
    },
}


def _competition_meaning_fallback(metrics: list[dict[str, Any]]) -> str:
    by_label = {str(item.get("label") or ""): item for item in metrics}
    count = by_label.get("동업종 점포수")
    position = by_label.get("동업종 점포수 위치")
    share = by_label.get("동업종 점포 비중")
    parts: list[str] = []
    if count and count.get("display"):
        parts.append(f"동업종 점포수는 {count['display']}입니다.")
    if position and position.get("display"):
        position_text = str(position["display"])
        if position_text.startswith("상위 "):
            parts.append(f"서울 비교군에서 {position_text}이므로 점포 수 자체를 적다고 볼 수 없습니다.")
        else:
            parts.append(f"서울 비교군에서 점포 수 위치는 {position_text}입니다.")
    if share and share.get("display"):
        parts.append(
            f"상권 전체 점포 중 동업종 비중 {share['display']}는 분모가 다른 값이므로 낮은 경쟁의 증거로 바꾸어 읽지 않습니다."
        )
    parts.append("실제 경쟁 압력은 점포 간 거리·가격·서비스 범위를 현장에서 대조해야 합니다.")
    return " ".join(parts)


def _axis_meaning_fallback(
    axis_key: str,
    label: str,
    score: float,
    top_metrics: list[dict[str, Any]],
) -> str:
    texts = _AXIS_FALLBACK_TEXTS[axis_key]
    tier = "high" if score >= 78 else ("mid" if score >= 55 else "low")
    base = texts[tier]
    if axis_key == "competition" and top_metrics:
        return _competition_meaning_fallback(top_metrics)
    if not top_metrics:
        return (
            f"{label}의 게시 등급은 확인되지만 이 보고서에 직접 인용 가능한 같은 영역의 세부 지표가 없습니다. "
            "따라서 등급의 원인이나 실제 이용·유입 효과는 설명하지 않고 현장 확인 대상으로 남깁니다."
        )
    top_metric = top_metrics[0]
    if top_metric.get("display"):
        lead = f"{top_metric.get('label')} {top_metric.get('display')}가 이 판단에서 확인된 관측값입니다. "
        return lead + base
    return base


def _axis_narratives_from_pack(pack: dict[str, Any]) -> list[AxisNarrative]:
    rows = []
    facts_axis_scores = (((pack.get("facts_pack") or {}).get("score_block") or {}).get("axis_scores") or {})
    for axis_key, label in AXIS_LABELS.items():
        raw_score = _axis_score(pack, axis_key)
        score = round(raw_score, 1) if raw_score is not None else None
        axis_metric = facts_axis_scores.get(axis_key)
        display_grade = _display_grade(
            axis_metric.get("grade") if isinstance(axis_metric, dict) else None,
            axis_metric.get("display") if isinstance(axis_metric, dict) else None,
            fallback="등급 보류",
        )
        score_display = display_grade
        level = f"{display_grade}등급" if axis_metric and score is not None else "등급 보류"
        texts = _AXIS_FALLBACK_TEXTS[axis_key]
        top_metrics = _top_metrics(pack, axis_key, limit=4)
        if score is None:
            meaning = f"{with_josa(label, '은는')} 현재 분기 원천이 없어 등급과 방향 해석을 보류했습니다."
            risk = f"{label} 원천 없이 다른 판단 영역만으로 시장 상태를 대신 설명하면 결론이 과장될 수 있습니다."
            action = f"{label}의 최신 원천을 확보한 뒤 해당 영역을 다시 평가합니다."
            next_check = f"{label} 원천의 최신 분기, 집계 범위, 업종 분류 일치 여부를 확인합니다."
        else:
            meaning = _axis_meaning_fallback(axis_key, label, score, top_metrics)
            risk = texts["risk"]
            action = texts["action"]
            next_check = texts["next_check"]
        rows.append(
            AxisNarrative(
                axis=label,
                score=score,
                score_display=score_display,
                display_grade=display_grade,
                interpretation_level=level,
                evidence_metrics=[_metric_sentence(item) for item in top_metrics],
                chart_id="C1" if axis_key != "demand" else "C5",
                meaning=meaning,
                evidence=_metric_text(pack, axis_key, limit=4),
                risk=risk,
                action=action,
                next_check=next_check,
                frame_citations=_frame_ids_for_axis(pack, axis_key),
            )
        )
    return rows


def _claim_safe_text(text: str) -> str:
    safe = str(text)
    safe = re.sub(
        r"\s*(?:\[NEWS:\d+\]|\[근거\s*\d+\]|"
        r"(?<!\w)근거\s*\d+(?![\d,.]|\s*(?:천|만)?(?:억원|만원|개월|분기|원|억|개|명|건|분|년|월|일|%|㎡)))\s*",
        " ",
        safe,
    )
    safe = re.sub(
        r"[^.!?\n]*(?:성공|생존|폐업)\s*(?:확률|가능성)[^.!?\n]*[.!?]?",
        "",
        safe,
    )
    replacements = {
        "매출 보장": "매출 지표",
        "매출을 보장": "매출 지표로 참고",
        "성장률 보장": "성장 지표 참고",
        "성장 보장": "성장 지표 참고",
        "수익성 보장": "수익성 검토 참고",
        "월세가 확정": "월세는 별도 계약 확인 필요",
        "권리금이 확정": "권리금은 별도 계약 확인 필요",
        "매출 축": "시장성",
        "경쟁 축": "경쟁 구조",
        "수요 축": "수요 기반",
        "접근성 축": "접근·유입",
    }
    for old, new in replacements.items():
        safe = safe.replace(old, new)
    safe = re.sub(r"(?<![\d.])\d{1,3}(?:\.\d+)?\s*점(?!포)", "해당 등급", safe)
    particle_pairs = {
        "은": "은는",
        "는": "은는",
        "이": "이가",
        "가": "이가",
        "을": "을를",
        "를": "을를",
        "과": "과와",
        "와": "과와",
    }
    for entity in set(AXIS_LABELS.values()):
        for particle, pair in particle_pairs.items():
            expected = josa(entity, pair)
            if particle != expected:
                safe = re.sub(
                    rf"{re.escape(entity)}{particle}(?=[\s,.):!?]|$)",
                    f"{entity}{expected}",
                    safe,
                )
    return safe


def _sanitize_claims(value: Any) -> Any:
    if isinstance(value, str):
        return _claim_safe_text(value)
    if isinstance(value, list):
        return [_sanitize_claims(item) for item in value]
    if isinstance(value, dict):
        return {key: _sanitize_claims(item) for key, item in value.items()}
    return value


def _normalize_budget_language(result: SingleInterpretation, user_condition: dict[str, Any]) -> SingleInterpretation:
    try:
        amount = int(user_condition.get("budget") or 0)
    except (TypeError, ValueError):
        return result
    if amount <= 0:
        return result

    canonical = f"{amount:,}만원"
    forms = sorted({str(amount), f"{amount:,}"}, key=len, reverse=True)

    def normalize(text: str) -> str:
        value = str(text)
        for form in forms:
            value = re.sub(
                rf"(예산(?:은|이|을|으로|대)?\s*){re.escape(form)}\s*만\s*원",
                rf"\g<1>{canonical}",
                value,
            )
            value = re.sub(
                rf"(예산(?:은|이|을|으로|대)?\s*){re.escape(form)}(?![\d,]|\s*(?:만\s*원|만원|원|억|천))",
                rf"\g<1>{canonical}",
                value,
            )
        value = value.replace(f"{canonical}만 원", canonical).replace(f"{canonical}만원", canonical)
        return value

    for field_name in ["executive_interpretation", "score_interpretation", "trend_analysis", "summary", "user_fit"]:
        setattr(result, field_name, normalize(getattr(result, field_name, "")))
    for field_name in ["thesis", "action_plan", "onsite_checklist", "risk_factors"]:
        setattr(result, field_name, [normalize(item) for item in getattr(result, field_name, [])])
    return result


def _anchor_news_context(
    result: SingleInterpretation,
    items: list[dict[str, Any]],
    user_condition: dict[str, Any],
) -> SingleInterpretation:
    def clean_markers(text: str) -> str:
        cleaned = re.sub(
            r"\[NEWS:\d+\]|\[근거\s*\d+\]|"
            r"(?<!\w)근거\s*\d+(?![\d,.]|\s*(?:천|만)?(?:억원|만원|개월|분기|원|억|개|명|건|분|년|월|일|%|㎡))",
            "",
            str(text or ""),
        )
        cleaned = re.sub(r"\[CHART:C[1-5]\]", "", cleaned)
        return re.sub(r"\s{2,}", " ", cleaned).strip()

    for field_name in [
        "executive_interpretation",
        "score_interpretation",
        "trend_analysis",
        "user_fit",
        "summary",
    ]:
        setattr(result, field_name, clean_markers(getattr(result, field_name, "")))
    result.trend_analysis = result.trend_analysis.replace("정량 점수는 바꾸지 않고 ", "")
    for field_name in ["thesis", "risk_factors", "action_plan", "onsite_checklist"]:
        setattr(result, field_name, [clean_markers(item) for item in getattr(result, field_name, [])])
    for axis in result.axis_interpretations:
        for field_name in ("meaning", "risk", "action", "next_check"):
            setattr(axis, field_name, clean_markers(getattr(axis, field_name, "")))

    # Selected external items and their decision-use metadata remain attached to the
    # report as a separate table. Narrative generation does not rely on them because,
    # without reader-facing badges, a prose claim could not identify its exact item.
    return result


def _coerce_axis_narratives(items: Any) -> list[AxisNarrative]:
    fixed: list[AxisNarrative] = []
    for item in items or []:
        if isinstance(item, AxisNarrative):
            fixed.append(item)
        elif isinstance(item, dict):
            try:
                fixed.append(AxisNarrative.model_validate(item))
            except Exception:
                continue
    return fixed


def _fill_single_gaps(result: SingleInterpretation, fallback: SingleInterpretation) -> SingleInterpretation:
    result.axis_interpretations = _coerce_axis_narratives(result.axis_interpretations)
    fallback.axis_interpretations = _coerce_axis_narratives(fallback.axis_interpretations)
    fallback_by_axis = {item.axis: item for item in fallback.axis_interpretations}
    fixed_axes: list[AxisNarrative] = []
    for item in result.axis_interpretations or []:
        base = fallback_by_axis.get(item.axis)
        score = _optional_float(item.score if item.score is not None else (base.score if base else None))
        display_grade = _display_grade(
            item.display_grade,
            item.score_display,
            base.display_grade if base else None,
            base.score_display if base else None,
            fallback="등급 보류",
        )
        score_display = display_grade
        interpretation_level = item.interpretation_level or (
            f"{display_grade}등급" if display_grade != "등급 보류" else "등급 보류"
        )
        fixed_axes.append(
            AxisNarrative(
                axis=item.axis or (base.axis if base else ""),
                score=score,
                score_display=score_display,
                display_grade=display_grade,
                interpretation_level=interpretation_level,
                evidence_metrics=item.evidence_metrics or (base.evidence_metrics if base else []),
                chart_id=item.chart_id or (base.chart_id if base else "C1"),
                meaning=item.meaning or (base.meaning if base else ""),
                evidence=item.evidence or (base.evidence if base else ""),
                risk=item.risk or (base.risk if base else ""),
                action=item.action or (base.action if base else ""),
                next_check=item.next_check or item.action or (base.next_check if base else ""),
                frame_citations=item.frame_citations or (base.frame_citations if base else []),
            )
        )
    if not fixed_axes:
        fixed_axes = fallback.axis_interpretations
    result.axis_interpretations = fixed_axes

    for field_name in [
        "narrative_title",
        "executive_interpretation",
        "score_interpretation",
        "summary",
    ]:
        if not getattr(result, field_name, None):
            setattr(result, field_name, getattr(fallback, field_name))

    for field_name in [
        "header_block",
        "thesis",
        "trend_analysis",
        "alternatives",
        "user_fit",
        "evidence_basis",
        "source_citations",
        "methodology_notes",
        "action_plan",
        "onsite_checklist",
        "limitations",
        "chart_manifest",
        "strengths",
        "weaknesses",
        "risk_factors",
    ]:
        if not getattr(result, field_name, None):
            setattr(result, field_name, getattr(fallback, field_name))

    return result


def _fill_axis_citation_gaps(result: SingleInterpretation, fallback: SingleInterpretation) -> SingleInterpretation:
    fallback_by_axis = {item.axis: item.frame_citations for item in fallback.axis_interpretations}
    for item in result.axis_interpretations:
        if not item.frame_citations:
            item.frame_citations = fallback_by_axis.get(item.axis, [])
    existing = {(item.title, item.source_path) for item in result.source_citations}
    for item in fallback.source_citations:
        key = (item.title, item.source_path)
        if key not in existing:
            result.source_citations.append(item)
            existing.add(key)
    return result


def _anchor_verified_facts(result: SingleInterpretation, fallback: SingleInterpretation) -> SingleInterpretation:
    def axis_key(label: str) -> str:
        value = str(label or "")
        if "매출" in value or "시장" in value:
            return "sales"
        if "경쟁" in value:
            return "competition"
        if "수요" in value:
            return "demand"
        if "접근" in value or "유입" in value:
            return "accessibility"
        return ""

    generated_by_key = {axis_key(item.axis): item for item in result.axis_interpretations if axis_key(item.axis)}
    anchored: list[AxisNarrative] = []
    for base in fallback.axis_interpretations:
        base_axis_key = axis_key(base.axis)
        item = generated_by_key.get(base_axis_key) or base
        if base_axis_key == "competition":
            # 점포 수 위치와 상권 내 비중은 분모가 달라 유창한 산문만으로
            # 방향을 다시 조립하지 않는다. 검증값을 함께 적은 결정론 해석을 유지한다.
            anchored_meaning = base.meaning
        elif base.evidence_metrics:
            anchored_meaning = item.meaning or base.meaning
        else:
            anchored_meaning = base.meaning
        anchored.append(
            AxisNarrative(
                axis=base.axis,
                score=base.score,
                score_display=base.score_display,
                display_grade=base.display_grade,
                interpretation_level=base.interpretation_level,
                evidence_metrics=list(base.evidence_metrics),
                chart_id=base.chart_id,
                meaning=anchored_meaning,
                evidence=base.evidence,
                risk=item.risk or base.risk,
                action=item.action or base.action,
                next_check=item.next_check or item.action or base.next_check,
                frame_citations=list(base.frame_citations),
            )
        )
    result.axis_interpretations = anchored
    result.header_block = dict(fallback.header_block)
    result.alternatives = [dict(item) for item in fallback.alternatives]
    result.chart_manifest = [dict(item) for item in fallback.chart_manifest]
    return result


ADVISORY_VALIDATION_CODES = {
    "REDUNDANT_SECTION",
    "REDUNDANT_METRIC",
    "NO_INFERENCE",
    "HEDGE_SPREAD",
    "JOSA",
}
ISSUE_CODE_RE = re.compile(r"^\[([A-Z_]+)\]")
ISSUE_FIELD_RE = re.compile(r"\[field=(.+?)\](?=\s)")
FIELD_PATH_RE = re.compile(
    r"^(?P<root>[a-z_]+)(?:\[(?P<index>\d+)\])?(?:\.(?P<child>[a-z_]+))?$"
)
NARRATIVE_SECTIONS = ("header", "axis", "trend_alternatives", "user_risk")
PROVENANCE_METADATA_FIELDS = (
    "original_validation_issues",
    "validation_issues",
    "quality_warnings",
    "quality_status",
    "generation_mode",
    "fallback_fields",
    "section_repair_log",
    "token_usage",
    "cache_meta",
    "ai_model",
    "reasoning_effort",
    "ai_generated",
)


def _issue_code(issue: str) -> str:
    match = ISSUE_CODE_RE.search(issue)
    return match.group(1) if match else "UNKNOWN"


def _is_advisory_issue(issue: str) -> bool:
    return _issue_code(issue) in ADVISORY_VALIDATION_CODES


def _merge_sanitized_cached_interpretation(
    cached: dict[str, Any],
    cleaned: SingleInterpretation,
) -> dict[str, Any]:
    preserved = {
        field_name: deepcopy(cached[field_name])
        for field_name in PROVENANCE_METADATA_FIELDS
        if field_name in cached
    }
    merged = dict(cached)
    merged.update(_sanitize_claims(cleaned.model_dump()))
    merged.update(preserved)
    return merged


def _field_targets_for_violations(violations: list[str]) -> list[str]:
    return sorted(
        {
            match.group(1)
            for violation in violations
            if (match := ISSUE_FIELD_RE.search(violation)) is not None
        }
    )


def _section_for_field(field_path: str) -> str | None:
    root = field_path.split("[", 1)[0].split(".", 1)[0]
    if root in {
        "header_block",
        "narrative_title",
        "thesis",
        "executive_interpretation",
        "score_interpretation",
        "summary",
    }:
        return "header"
    if root == "axis_interpretations":
        return "axis"
    if root in {"trend_analysis", "alternatives"}:
        return "trend_alternatives"
    if root in {"user_fit", "action_plan", "onsite_checklist", "limitations", "risk_factors"}:
        return "user_risk"
    return None


def _section_targets_for_violations(violations: list[str]) -> list[str]:
    return sorted(
        {
            section
            for field_path in _field_targets_for_violations(violations)
            if (section := _section_for_field(field_path)) is not None
        }
    )


def _field_value(result: SingleInterpretation, field_path: str) -> Any:
    match = FIELD_PATH_RE.fullmatch(field_path)
    if not match:
        raise KeyError(field_path)
    value: Any = getattr(result, match.group("root"))
    index_text = match.group("index")
    if index_text is not None:
        value = value[int(index_text)]
    child = match.group("child")
    if child:
        value = value.get(child) if isinstance(value, dict) else getattr(value, child)
    return value


def _set_field_value(result: SingleInterpretation, field_path: str, value: Any) -> SingleInterpretation:
    match = FIELD_PATH_RE.fullmatch(field_path)
    if not match:
        raise KeyError(field_path)
    root = match.group("root")
    index_text = match.group("index")
    child = match.group("child")
    replacement = deepcopy(value)
    if index_text is None:
        if child:
            container = deepcopy(getattr(result, root))
            if isinstance(container, dict):
                container[child] = replacement
            else:
                setattr(container, child, replacement)
            setattr(result, root, container)
        else:
            setattr(result, root, replacement)
        return result

    items = deepcopy(getattr(result, root))
    index = int(index_text)
    if index >= len(items):
        raise IndexError(field_path)
    if child:
        if isinstance(items[index], dict):
            items[index][child] = replacement
        else:
            setattr(items[index], child, replacement)
    else:
        items[index] = replacement
    setattr(result, root, items)
    return result


def _remove_field_value(result: SingleInterpretation, field_path: str) -> SingleInterpretation:
    match = FIELD_PATH_RE.fullmatch(field_path)
    if not match or match.group("index") is None:
        raise KeyError(field_path)
    root = match.group("root")
    items = deepcopy(getattr(result, root))
    index = int(match.group("index"))
    if index >= len(items):
        raise IndexError(field_path)
    child = match.group("child")
    if child:
        if isinstance(items[index], dict):
            items[index][child] = ""
        else:
            setattr(items[index], child, "")
    else:
        items.pop(index)
    setattr(result, root, items)
    return result


def _field_path_mutation_order(field_path: str) -> tuple[str, int, int, str]:
    match = FIELD_PATH_RE.fullmatch(field_path)
    if not match:
        return (field_path, 0, 0, "")
    index_text = match.group("index")
    # Removing a list item shifts every later index. Process indexed targets
    # from the end so several invalid extras are all removed as requested.
    return (
        match.group("root"),
        1 if index_text is not None else 0,
        -int(index_text) if index_text is not None else 0,
        match.group("child") or "",
    )


def _section_payload(result: SingleInterpretation, section_id: str) -> dict[str, Any]:
    data = result.model_dump()
    fields = {
        "header": ["header_block", "narrative_title", "thesis", "executive_interpretation", "score_interpretation", "summary"],
        "axis": ["axis_interpretations"],
        "trend_alternatives": ["trend_analysis", "alternatives"],
        "user_risk": [
            "user_fit",
            "action_plan",
            "onsite_checklist",
            "limitations",
            "strengths",
            "weaknesses",
            "risk_factors",
        ],
        "sources": ["evidence_basis", "source_citations", "methodology_notes"],
    }.get(section_id, [])
    return {field: data.get(field) for field in fields}


def _section_schema(section_id: str):
    return {
        "header": HeaderSectionPatch,
        "axis": AxisSectionPatch,
        "trend_alternatives": TrendAlternativeSectionPatch,
        "user_risk": UserRiskSectionPatch,
        "sources": SourceSectionPatch,
    }[section_id]


def _apply_section_patch(result: SingleInterpretation, patch: BaseModel, section_id: str) -> SingleInterpretation:
    patch_data = patch.model_dump()
    for field_name, value in patch_data.items():
        if value:
            setattr(result, field_name, value)
    return result


def _repair_invalid_fields(
    *,
    current: SingleInterpretation,
    fallback: SingleInterpretation,
    validation_issues: list[str],
    facts_display: dict[str, Any],
    user_condition: dict[str, Any],
    usage_handler: UsageMetadataCallbackHandler,
    reasoning_effort: str,
) -> tuple[SingleInterpretation, list[str], list[str]]:
    targets = sorted(
        _field_targets_for_violations(validation_issues),
        key=_field_path_mutation_order,
    )
    if not targets:
        return current, [], []

    repaired = current.model_copy(deep=True)
    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                (
                    "너는 서울 상권 입지 리포트에서 지적된 문장 하나만 고치는 편집자다. "
                    "반드시 요청받은 field_path의 현재 문장만 다시 작성한다. "
                    "위반과 무관한 사실, 판단 방향, 문장 구조는 가능한 한 보존한다. "
                    "모든 숫자는 facts pack display 값 그대로만 쓴다. "
                    "판단 영역의 meaning을 고칠 때는 그 영역에 대응하는 facts pack 블록의 수치만 쓴다. "
                    "DB., gold., 테이블명, 파일 경로 같은 내부 식별자는 본문에 쓰지 않는다. "
                    "sales, competition, demand, accessibility 같은 내부 필드 코드도 본문에 쓰지 않는다. "
                    "공식 판단이 보류된 보고서는 유망·상위·추천 후보라고 부르지 않는다. "
                    "공식 예산 적합도가 보류된 경우 예산 충분성이나 진입 가능성을 단정하지 않는다. "
                    "같은 수치의 불필요한 반복을 줄이고, 필요한 경우 지표가 판단에 주는 의미를 설명한다. "
                    "성공확률, 매출 보장, 월세/권리금 단정, 수익성 보장 표현은 금지한다."
                ),
            ),
            (
                "user",
                (
                    "field_path:\n{field_path}\n\n"
                    "critic violations:\n{violations}\n\n"
                    "facts pack display:\n{facts_pack_display}\n\n"
                    "user condition:\n{user_condition}\n\n"
                    "current text:\n{current_text}\n\n"
                    "verified fallback text:\n{fallback_text}"
                ),
            ),
        ]
    )
    fallback_fields: list[str] = []
    llm_repaired_fields: list[str] = []
    for field_path in targets:
        matching_issues = [
            issue
            for issue in validation_issues
            if f"[field={field_path}]" in issue
        ]
        hard_issue = any(not _is_advisory_issue(issue) for issue in matching_issues)
        try:
            current_value = _field_value(repaired, field_path)
        except (AttributeError, IndexError, KeyError, TypeError):
            continue
        fallback_available = True
        try:
            fallback_value = _field_value(fallback, field_path)
        except (AttributeError, IndexError, KeyError, TypeError):
            fallback_available = False
            fallback_value = ""

        if not isinstance(current_value, str) or not isinstance(fallback_value, str):
            if hard_issue:
                try:
                    repaired = (
                        _set_field_value(repaired, field_path, fallback_value)
                        if fallback_available
                        else _remove_field_value(repaired, field_path)
                    )
                    fallback_fields.append(field_path)
                except (AttributeError, IndexError, KeyError, TypeError):
                    pass
            continue

        try:
            chain = prompt | get_llm(
                reasoning_effort=reasoning_effort
            ).with_structured_output(FieldTextPatch, method="function_calling")
            patch = chain.invoke(
                {
                    "field_path": field_path,
                    "violations": "\n".join(matching_issues),
                    "facts_pack_display": _json(facts_display),
                    "user_condition": _json(user_condition),
                    "current_text": current_value,
                    "fallback_text": fallback_value,
                },
                config={"callbacks": [usage_handler]},
            )
            if not patch.replacement.strip():
                raise ValueError("empty field repair")
            repaired = _set_field_value(repaired, field_path, patch.replacement.strip())
            llm_repaired_fields.append(field_path)
        except Exception:
            if hard_issue:
                try:
                    repaired = (
                        _set_field_value(repaired, field_path, fallback_value)
                        if fallback_available
                        else _remove_field_value(repaired, field_path)
                    )
                    fallback_fields.append(field_path)
                except (AttributeError, IndexError, KeyError, TypeError):
                    pass
    return (
        _fill_axis_citation_gaps(_fill_single_gaps(repaired, fallback), fallback),
        sorted(set(fallback_fields)),
        sorted(set(llm_repaired_fields)),
    )


def _replace_fields_with_fallback(
    current: SingleInterpretation,
    fallback: SingleInterpretation,
    field_paths: list[str],
) -> tuple[SingleInterpretation, list[str]]:
    replaced = current.model_copy(deep=True)
    applied: list[str] = []

    for field_path in sorted(set(field_paths), key=_field_path_mutation_order):
        try:
            fallback_value = _field_value(fallback, field_path)
            replaced = _set_field_value(replaced, field_path, fallback_value)
            applied.append(field_path)
        except (AttributeError, IndexError, KeyError, TypeError):
            try:
                replaced = _remove_field_value(replaced, field_path)
                applied.append(field_path)
            except (AttributeError, IndexError, KeyError, TypeError):
                continue
    return replaced, sorted(set(applied))


def _all_narrative_sections_are_fallback(
    result: SingleInterpretation,
    fallback: SingleInterpretation,
) -> bool:
    return all(
        _sanitize_claims(_section_payload(result, section_id))
        == _sanitize_claims(_section_payload(fallback, section_id))
        for section_id in NARRATIVE_SECTIONS
    )


def _axis_key_from_label(label: str) -> str:
    value = str(label or "")
    if "매출" in value or "시장" in value:
        return "sales"
    if "경쟁" in value:
        return "competition"
    if "수요" in value:
        return "demand"
    if "접근" in value or "유입" in value:
        return "accessibility"
    return ""


def _fallback_gap_fields(
    candidate: SingleInterpretation,
    fallback: SingleInterpretation,
) -> list[str]:
    """Record actual missing model-owned fields before code fills them."""
    root_fields = [
        "thesis",
        "executive_interpretation",
        "score_interpretation",
        "summary",
        "trend_analysis",
        "user_fit",
        "action_plan",
        "onsite_checklist",
        "risk_factors",
        "strengths",
        "weaknesses",
    ]
    missing = [field_name for field_name in root_fields if not getattr(candidate, field_name, None)]
    candidate_axes = {
        _axis_key_from_label(item.axis): item
        for item in candidate.axis_interpretations
        if _axis_key_from_label(item.axis)
    }
    if not candidate_axes:
        missing.append("axis_interpretations")
        return sorted(set(missing))
    for index, base in enumerate(fallback.axis_interpretations):
        item = candidate_axes.get(_axis_key_from_label(base.axis))
        if item is None:
            missing.append(f"axis_interpretations[{index}]")
            continue
        for field_name in ("meaning", "risk", "action"):
            if not getattr(item, field_name, None):
                missing.append(f"axis_interpretations[{index}].{field_name}")
        # _fill_single_gaps copies a generated action into next_check when only the
        # duplicate field is omitted. That is not deterministic fallback provenance.
        if not item.next_check and not item.action:
            missing.append(f"axis_interpretations[{index}].next_check")
    return sorted(set(missing))


def _generation_mode_for(
    result: SingleInterpretation,
    fallback: SingleInterpretation,
    fallback_fields: set[str] | list[str],
) -> Literal["llm", "partial_fallback", "deterministic"]:
    if _all_narrative_sections_are_fallback(result, fallback):
        return "deterministic"
    if fallback_fields:
        return "partial_fallback"
    return "llm"


def _fill_comparison_gaps(result: ComparisonInterpretation, fallback: ComparisonInterpretation) -> ComparisonInterpretation:
    if not result.comparison_matrix:
        result.comparison_matrix = fallback.comparison_matrix
    for field_name in [
        "narrative_title",
        "executive_interpretation",
        "summary",
        "top_recommendation_reason",
    ]:
        if not getattr(result, field_name, None):
            setattr(result, field_name, getattr(fallback, field_name))
    for field_name in ["evidence_basis", "source_citations", "methodology_notes", "action_plan", "limitations"]:
        if not getattr(result, field_name, None):
            setattr(result, field_name, getattr(fallback, field_name))
    return result


def _sales_trend_fallback_text(facts: dict[str, Any]) -> str:
    """Describe only the observed start-to-latest direction of the available window."""

    rows = ((facts.get("sales_block") or {}).get("sales_trend") or [])
    points: list[tuple[int, float]] = []
    for row in rows:
        amount = (row.get("sales_amount") or {}).get("raw")
        try:
            points.append((int(row.get("timestamp")), float(amount)))
        except (TypeError, ValueError):
            continue
    points.sort(key=lambda item: item[0])

    prefix = (
        "[CHART:C2] 최근 8분기 매출 추이와 [CHART:C3] 상권 내 업종 지표를 함께 봅니다. "
    )
    if len(points) < 2:
        return prefix + "시작 분기와 최근 분기의 방향을 비교할 원값이 부족해 추세 판단은 보류합니다."

    oldest_amount = points[0][1]
    latest_amount = points[-1][1]
    if latest_amount < oldest_amount:
        direction = "시작 분기보다 최근 분기 매출이 낮아져 전체 방향은 하락입니다."
    elif latest_amount > oldest_amount:
        direction = "시작 분기보다 최근 분기 매출이 높아져 전체 방향은 상승입니다."
    else:
        direction = "시작 분기와 최근 분기 매출이 같은 수준을 유지해 전체 방향은 보합입니다."
    return prefix + direction + " 분기별 등락은 별도로 확인해야 합니다."


def _base_single_interpretation(payload: dict[str, Any], pack: dict[str, Any]) -> SingleInterpretation:
    target = pack.get("target") or {}
    facts = pack.get("facts_pack") or {}
    display_facts = pack.get("facts_pack_display") or {}
    score_block = facts.get("score_block") or {}
    score_value = _optional_float(target.get("score"))
    coverage = score_block.get("coverage") or target.get("score_coverage") or {}
    is_area_context = not bool(target.get("industry_code") or payload.get("industry_code"))
    official_score_available = score_value is not None and bool(coverage.get("official_rank_eligible", False))
    score_metric = score_block.get("current_location_score") or {}
    score_display = _display_grade(
        score_block.get("display_grade"),
        score_metric.get("grade") if isinstance(score_metric, dict) else None,
        score_metric.get("display") if isinstance(score_metric, dict) else None,
        target.get("display_grade"),
        target.get("grade"),
    )
    grade = score_display
    comparison_basis = "서울 상권 기준" if is_area_context else "서울 동일 업종 기준"
    area = target.get("area_name") or payload.get("area_name") or "선택 상권"
    industry = target.get("industry_name") or payload.get("industry_name") or "선택 업종"
    coverage_reason = public_coverage_reason(coverage)
    context_note = public_coverage_context(coverage)
    coverage_header = public_coverage_header(
        coverage,
        target.get("decision_label") or score_block.get("decision_label"),
    )
    coverage_tier = public_coverage_tier(coverage.get("tier"))
    best_axis, watch_axis = _best_and_watch_axis(pack)
    axis_items = _axis_narratives_from_pack(pack)
    best_label = AXIS_LABELS[best_axis]
    watch_label = AXIS_LABELS[watch_axis]
    best_metrics = _metric_text(pack, best_axis, limit=4)
    watch_metrics = _metric_text(pack, watch_axis, limit=4)

    def _top_display(axis_key: str) -> str:
        metrics = _top_metrics(pack, axis_key, limit=1)
        if metrics:
            return f"{metrics[0].get('label')} {metrics[0].get('display')}"
        return "핵심 지표 결측"

    best_top_display = _top_display(best_axis)
    watch_top_display = _top_display(watch_axis)
    key_metrics = []
    for block_name, metric_name in [
        ("sales_block", "sales_amount"),
        ("sales_block", "sales_count"),
        ("sales_block", "ticket_size"),
        ("competition_block", "same_industry_store_count"),
        ("cost_block", "cost_risk_score"),
    ]:
        metric = ((facts.get(block_name) or {}).get(metric_name))
        if metric:
            key_metrics.append(metric)
    alternatives = []
    for item in (facts.get("alternatives") or [])[:3]:
        alt_name = str(item.get("area_name") or "대안 상권")
        differential = str(item.get("major_differential_axis") or "").strip()
        if differential:
            comparison_sentence = differential if differential.endswith((".", "!", "?")) else f"{differential}."
            judgement = (
                f"{with_josa(alt_name, '은는')} {comparison_sentence} "
                "같은 예산이라면 비용 여건과 이 차이를 함께 비교해야 합니다."
            )
        else:
            judgement = f"{with_josa(alt_name, '은는')} 동일 업종 기준으로 함께 검토할 만한 대안입니다."
        alternatives.append(
            {
                "area_name": item.get("area_name"),
                "score": (item.get("current_location_score") or {}).get("display", ""),
                "cost": (item.get("cost_risk_score") or {}).get("display", ""),
                "differential": item.get("major_differential_axis", ""),
                "judgement": judgement,
            }
        )
    user_condition = facts.get("user_condition") or payload.get("user_condition") or {}
    budget = user_condition.get("budget")
    business_type = user_condition.get("business_type") or industry
    budget_text = f"{int(budget):,}만원" if isinstance(budget, int) and budget > 0 else "예산 미입력"
    budget_fit = (facts.get("cost_block") or {}).get("budget_fit") or payload.get("budget_fit") or {}
    if budget_fit.get("status") in {"mapped_reference_only", "broad_seoul_reference_only"} and budget_fit.get(
        "standardized_12m_reference_manwon"
    ) is not None:
        ratio = budget_fit.get("reference_to_input_budget_ratio")
        ratio_text = (
            f"입력 예산 대비 참고비율은 {float(ratio) * 100:.1f}%입니다. "
            if ratio is not None
            else "입력 예산이 없어 참고비율은 계산하지 않았습니다. "
        )
        scope_text = (
            "지역명 후보 매핑"
            if budget_fit.get("status") == "mapped_reference_only"
            else "서울 전체 기준선"
        )
        budget_fit_text = (
            f"R-ONE {scope_text} 원값을 33㎡·12개월로 단순 환산한 참고액은 "
            f"{float(budget_fit['standardized_12m_reference_manwon']):,.0f}만원입니다. {ratio_text}"
            "이는 해당 상권의 실제 임대료나 공식 예산 적합도 점수가 아니며 적합도 판정은 보류합니다."
        )
    else:
        budget_fit_text = "R-ONE 참고 산술을 제공할 수 없으며 공식 예산 적합도 점수는 evidence-only 계약에 따라 보류합니다."
    user_fit = (
        f"입력된 조건은 업종 {business_type}, 예산 {budget_text}입니다. "
        f"{area}의 {industry} 판단 등급은 {score_display}이며 비교 기준은 {comparison_basis}입니다. "
        f"이 조건에서는 {with_josa(watch_label, '이가')} 가장 큰 변수이기 때문에 그 축부터 따져 보는 순서가 맞습니다. "
        f"{budget_fit_text}"
    )
    trend_analysis = _sales_trend_fallback_text(facts)

    if official_score_available:
        thesis = [
            f"{area}의 {industry} 입지 종합 등급은 {score_display}입니다.",
            f"판단을 앞에서 끄는 축은 {with_josa(best_label, '이가')} {best_top_display} 수준이고, "
            f"확인할 축은 {with_josa(watch_label, '이가')} {watch_top_display} 수준입니다.",
            f"따라서 {best_label} 우위가 현장에서도 유지되는지 확인하면서 {watch_label} 조건을 함께 비교해야 합니다.",
        ]
        executive_interpretation = (
            f"{area}의 {industry} 후보는 {comparison_basis} {score_display}등급입니다. "
            f"{with_josa(best_label, '이가')} 판단을 받쳐 주지만 "
            f"{with_josa(watch_label, '은는')} 별도 현장 대조가 필요합니다."
        )
        summary_text = f"{area} · {industry}: {score_display}등급, 핵심 확인 축은 {best_label}, 보수 확인 축은 {watch_label}입니다."
    elif is_area_context:
        thesis = [
            f"{area}의 수요·접근성 상권 맥락 등급은 {score_display}입니다.",
            f"제공된 상권 축 중 {with_josa(best_label, '은는')} {best_top_display}, "
            f"{with_josa(watch_label, '은는')} {watch_top_display} 수준입니다.",
            "업종을 선택하면 시장성과 경쟁 구조까지 포함한 입지 종합 등급으로 이어집니다.",
        ]
        executive_interpretation = (
            f"{area}은 {comparison_basis} 수요·접근성 상권 맥락 {score_display}등급입니다. "
            f"{best_label} 강점과 {watch_label} 확인사항을 함께 봅니다."
        )
        summary_text = f"{area}: 수요·접근성 상권 맥락 {score_display}등급입니다."
    else:
        thesis = [
            f"{area}의 {with_josa(industry, '은는')} {coverage_reason}",
            f"관측된 축 중 {with_josa(best_label, '은는')} {best_top_display}, "
            f"{with_josa(watch_label, '은는')} {watch_top_display} 수준입니다.",
            context_note or "가용 축의 원천 지표를 먼저 확인합니다.",
        ]
        executive_interpretation = (
            f"{area}의 {with_josa(industry, '은는')} {coverage_reason} "
            f"{context_note or '현재는 확인된 원천 범위만 참고합니다.'}"
        )
        summary_text = f"{area} · {industry}: {coverage_reason}"

    return SingleInterpretation(
        header_block={
            "judgement_line": coverage_header,
            "score_label": (
                "수요·접근성 상권 맥락 등급"
                if is_area_context
                else "입지 종합 등급"
                if official_score_available
                else "참고 등급"
            ),
            "score": score_display,
            "grade": grade,
            "display_grade": score_display,
            "percentile": comparison_basis,
            "key_metrics": [
                {
                    "label": item.get("label"),
                    "display": item.get("display"),
                    "note": item.get("note", ""),
                }
                for item in key_metrics[:6]
            ],
        },
        narrative_title=f"{area} · {industry} 입지 리서치",
        thesis=thesis,
        executive_interpretation=executive_interpretation,
        score_interpretation=(
            (
                f"{score_display}등급은 검증된 규칙 엔진의 산정 결과입니다. "
                f"{with_josa(best_label, '이가')} 기여가 가장 컸고, {with_josa(watch_label, '은는')} 상대적으로 낮아 "
                f"해석의 무게는 두 축의 격차가 실제 운영에서 무엇을 의미하는지에 둡니다."
            )
            if official_score_available
            else (
                f"{score_display}등급은 서울 상권의 수요·접근성 상대 위치를 나타냅니다."
                if is_area_context and score_display != "등급 보류"
                else f"{coverage_reason} {context_note}"
            )
        ),
        axis_interpretations=axis_items,
        trend_analysis=trend_analysis,
        alternatives=alternatives,
        user_fit=user_fit,
        evidence_basis=[
            f"분석 기준: {pack.get('data_period_text') or target.get('quarter') or '가용 최신 분기'}",
            f"분석 단위: 서울시 상권 × {industry} × 분기",
            "평가 범위: 시장성, 경쟁 구조, 수요 기반, 접근·유입의 네 판단 영역",
            f"등급 산정 범위: {coverage_tier} · {coverage_reason}",
        ],
        source_citations=_source_citations(pack),
        methodology_notes=list(METHODOLOGY_NOTES),
        action_plan=[
            f"{best_label}에 나온 수치가 현재 현장에서도 유지되는지 같은 시간대에 직접 확인합니다.",
            f"{with_josa(watch_label, '은는')} 같은 업종의 주변 후보지와 비교해 반증 지점을 먼저 찾습니다.",
            "계약 전에는 임대료, 권리금, 관리비, 영업 제한 조건을 지표와 분리해 확인합니다.",
        ],
        onsite_checklist=[
            "평일 점심·퇴근·주말 피크 시간대 보행량과 구매 목적 방문 비중",
            "동일 업종 경쟁점의 가격대, 회전율, 대기 여부, 신규·폐업 흔적",
            "매장 전면 가시성, 횡단보도·정류장·역 출구와의 실제 동선",
            "임대료, 권리금, 관리비, 계약기간, 원상복구 조건",
        ],
        limitations=list(LIMITATIONS),
        chart_manifest=pack.get("chart_manifest") or [],
        quality_status="fallback_ready",
        summary=summary_text,
        strengths=[f"{best_label}: {best_metrics}"],
        weaknesses=[f"{watch_label}: {watch_metrics}"],
        recommended_businesses=[str(item) for item in (payload.get("top_industries") or [])],
        risk_factors=[item.risk for item in axis_items if item.risk][:3],
    )


def _base_comparison_interpretation(payload: dict[str, Any]) -> ComparisonInterpretation:
    is_area_context = payload.get("score_contract") == "area_context_2axis"
    areas = sorted(
        payload.get("areas") or [],
        key=lambda item: (
            _optional_float(item.get("score")) is not None,
            _optional_float(item.get("score")) or float("-inf"),
        ),
        reverse=True,
    )
    rows = []
    axis_grade_keys = {
        "매출 축": "sales",
        "경쟁 축": "competition",
        "수요 축": "demand",
        "접근성 축": "accessibility",
    }
    for area in areas:
        axes = area.get("axes") or {}
        axis_display_grades = area.get("axis_display_grades") or {}
        scores = {
            label: score
            for label, value in [
                ("매출 축", axes.get("axis_sales")),
                ("경쟁 축", axes.get("axis_competition")),
                ("수요 축", axes.get("axis_demand")),
                ("접근성 축", axes.get("axis_accessibility")),
            ]
            if (score := _optional_float(value)) is not None
        }
        strong_axis = max(scores, key=scores.get) if scores else None
        watch_axis = min(scores, key=scores.get) if scores else None
        area_name = str(area.get("area_name", ""))
        area_grade = _display_grade(area.get("display_grade"), area.get("grade"))
        strong_grade = _display_grade(
            axis_display_grades.get(axis_grade_keys.get(strong_axis, "")) if strong_axis else None,
            fallback="등급 보류",
        )
        watch_grade = _display_grade(
            axis_display_grades.get(axis_grade_keys.get(watch_axis, "")) if watch_axis else None,
            fallback="등급 보류",
        )
        rows.append(
            ComparisonRow(
                area_name=area_name,
                interpretation_level=f"{area_grade}등급" if area_grade != "등급 보류" else area_grade,
                strong_axis=f"{strong_axis} {strong_grade}등급" if strong_axis else "관측 축 없음",
                watch_axis=f"{watch_axis} {watch_grade}등급" if watch_axis else "관측 축 없음",
                interpretation=(
                    f"{with_josa(area_name, '은는')} {strong_axis}이 상대적으로 강하고 {watch_axis}은 확인이 필요합니다."
                    if strong_axis and watch_axis
                    else f"{area_name}은 관측 축이 없어 수치 비교를 보류합니다."
                ),
            )
        )

    top_area = next((area for area in areas if _optional_float(area.get("score")) is not None), None)
    top = str(top_area.get("area_name")) if top_area else None
    return ComparisonInterpretation(
        narrative_title="상권 비교 AI 상세 리포트",
        executive_interpretation=(
            f"수요·접근성 상권 맥락 등급을 기준으로 {with_josa(top, '을를')} 먼저 비교합니다."
            if top and is_area_context
            else "수요·접근성 상권 맥락 등급이 없어 비교 우선순위를 보류합니다."
            if is_area_context
            else f"입지 종합 등급이 있는 후보 중 {with_josa(top, '을를')} 먼저 검토하고 축별 조건을 함께 비교합니다."
            if top
            else "입지 종합 등급이 있는 후보가 없어 우선순위를 정하지 않습니다."
        ),
        comparison_matrix=rows,
        evidence_basis=(
            ["상권 단위 수요·접근성 두 축의 산술평균과 각 축 원값; 매출·경쟁 축은 결측 유지"]
            if is_area_context
            else ["후보별 입지 종합 등급과 네 판단 영역의 상대 등급"]
        ),
        source_citations=[
            EvidenceCitation(
                title="상권 수요·접근성 맥락 등급 요약" if is_area_context else "상권·업종 입지 종합 등급",
                source_path="rule_area_score_summary" if is_area_context else "rule_location_score",
                theme="실제 지표팩",
                used_for="후보별 수요·접근성 맥락 비교" if is_area_context else "후보별 입지 종합 등급과 축 등급 비교",
            )
        ],
        methodology_notes=[
            "상권 맥락 등급은 수요·접근성 2축의 서울 상대 위치로 산정합니다."
            if is_area_context
            else "입지 종합 등급은 서울 동일 업종의 네 판단 영역을 기준으로 비교합니다.",
            "업종을 선택하면 시장성과 경쟁 구조까지 포함한 입지 종합 등급을 함께 확인합니다."
            if is_area_context
            else "각 후보의 자세한 원천 지표는 상권×업종 단일 리포트에서 확인합니다.",
        ],
        action_plan=["수요·접근성 맥락 상위 후보와 다른 후보를 같은 시간대에 현장 비교합니다."],
        limitations=list(LIMITATIONS),
        summary=(
            f"{top}을 수요·접근성 맥락 기준으로 우선 비교" if top and is_area_context
            else "상권 맥락 등급 결측으로 우선순위 보류" if is_area_context
            else f"{top} 우선 검토, 단 현장 대조 후 결정" if top
            else "입지 종합 등급 결측으로 우선순위 보류"
        ),
        top_recommendation_reason=(
            f"{with_josa(top, '이가')} 수요·접근성 상권 맥락 등급 기준에서 앞섭니다."
            if top and is_area_context
            else "상권 맥락 등급이 없어 비교 우선순위를 보류합니다."
            if is_area_context
            else f"{with_josa(top, '이가')} 입지 종합 등급이 있는 비교 후보 중 앞섭니다."
            if top
            else "입지 종합 등급이 있는 후보가 없어 추천을 보류합니다."
        ),
    )


def _visualization_data(payload: dict[str, Any]) -> list[dict[str, Any]]:
    # 숫자 축 시각화는 과거 radar 호환 필드였다. 공개 응답에서는 등급형
    # C1 차트를 사용하므로 중복 raw 점수를 내보내지 않는다.
    return []


def _default_header_block(payload: dict[str, Any], header: dict[str, Any] | None) -> dict[str, Any]:
    block = dict(header or {})

    def usable(value: Any) -> bool:
        return value not in (None, "", "-")

    is_area_summary = (payload.get("industry_name") or "") in {"상권 종합", "상권 맥락"} or not payload.get("industry_code")
    display_grade = _display_grade(
        payload.get("display_grade"),
        block.get("display_grade"),
        block.get("grade"),
        block.get("score"),
        payload.get("grade"),
    )
    block["score_label"] = (
        "수요·접근성 상권 맥락 등급"
        if is_area_summary
        else "입지 종합 등급"
        if payload.get("official_rank_eligible") and display_grade != "등급 보류"
        else "참고 등급"
    )
    if is_area_summary:
        block["judgement_line"] = "상권 수요·접근성 맥락 기준 검토"
    elif not usable(block.get("judgement_line")):
        block["judgement_line"] = payload.get("decision_label") or "입지 조건 검토"
    block["score"] = display_grade
    block["grade"] = display_grade
    block["display_grade"] = display_grade
    block["percentile"] = "서울 상권 기준" if is_area_summary else "서울 동일 업종 기준"

    key_metrics: list[dict[str, Any]] = []
    for item in block.get("key_metrics") or []:
        metric = dict(item) if isinstance(item, dict) else {}
        label = str(metric.get("label") or "")
        note = str(metric.get("note") or "")
        if "점수" in label or "점수" in note or "등급" in label:
            metric_grade = _display_grade(metric.get("display"), metric.get("grade"), fallback="")
            if not metric_grade:
                continue
            metric["display"] = metric_grade
        key_metrics.append(metric)
    block["key_metrics"] = key_metrics
    return block


def _comparison_visualization_data(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return []


def _news_source_citations(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        EvidenceCitation(
            title=item.get("title", ""),
            provider=item.get("provider", ""),
            dataset_name=item.get("title", ""),
            source_url=item.get("original_url", ""),
            period=item.get("published_date", ""),
            granularity=item.get("location_scope_label") or item.get("region_hints") or "적용 범위 확인 필요",
            theme=NEWS_EVIDENCE_THEME,
            used_for=(
                item.get("reference_use")
                if item.get("evidence_tier") == REFERENCE_MONITORING_TIER
                else item.get("decision_use")
            )
            or "최근 변화의 대상 범위 확인",
            caveat=item.get("usage_limit")
            or "정량 점수에는 반영하지 않으며 선정 이유와 발표·시행 상태는 원문에서 재확인해야 합니다.",
        ).model_dump()
        for item in items
        if item.get("title") and item.get("original_url")
    ]


def _attach_news_evidence(data: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, Any]:
    attached = dict(data)
    public_items: list[dict[str, Any]] = []
    for item in items:
        row = dict(item)
        row.pop("citation_marker", None)
        public_items.append(row)
    citations: list[dict[str, Any]] = []
    for item in attached.get("source_citations") or []:
        row = item.model_dump() if isinstance(item, BaseModel) else dict(item)
        if row.get("theme") != NEWS_EVIDENCE_THEME:
            citations.append(row)
    citations.extend(_news_source_citations(public_items))
    attached["source_citations"] = citations
    attached["news_evidence"] = public_items
    attached["decision_news_evidence"] = [
        item
        for item in public_items
        if item.get("evidence_tier", DECISION_SUPPORT_TIER) == DECISION_SUPPORT_TIER
    ]
    attached["monitoring_news_evidence"] = [
        item
        for item in public_items
        if item.get("evidence_tier") == REFERENCE_MONITORING_TIER
    ]
    return attached


def _build_claim_source_map(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep field-to-source traceability without adding inline badges or links."""
    citations = [dict(item) for item in data.get("source_citations") or [] if isinstance(item, dict)]

    def source_refs(themes: set[str]) -> list[dict[str, Any]]:
        refs: list[dict[str, Any]] = []
        for source_index, citation in enumerate(citations, 1):
            if str(citation.get("theme") or "") not in themes:
                continue
            refs.append(
                {
                    "source_index": source_index,
                    "title": citation.get("title") or citation.get("dataset_name") or "",
                    "provider": citation.get("provider") or "",
                    "dataset_name": citation.get("dataset_name") or "",
                    "source_url": citation.get("source_url") or "",
                    "period": citation.get("period") or "",
                    "granularity": citation.get("granularity") or "",
                    "used_for": citation.get("used_for") or "",
                    "caveat": citation.get("caveat") or "",
                }
            )
        return refs

    mappings: list[dict[str, Any]] = []
    header_sources = source_refs({"산정 결과"})
    if header_sources:
        mappings.append(
            {
                "field_path": "header_block",
                "claim_scope": "공식 입지 등급과 비교 기준",
                "supporting_evidence": [],
                "sources": header_sources,
                "attribution_level": "field",
                "public_inline_marker": False,
                "limitation": "등급 산정 원천을 연결하며 LLM 해석 문장 전체를 보증하지 않습니다.",
            }
        )

    for axis_index, axis in enumerate(data.get("axis_interpretations") or []):
        if not isinstance(axis, dict):
            continue
        axis_label = str(axis.get("axis") or "")
        axis_sources = source_refs({axis_label})
        if not axis_sources:
            continue
        mappings.append(
            {
                "field_path": f"axis_interpretations[{axis_index}].meaning",
                "claim_scope": axis_label,
                "supporting_evidence": list(axis.get("evidence_metrics") or []),
                "sources": axis_sources,
                "attribution_level": "field",
                "public_inline_marker": False,
                "limitation": "표시 수치와 판단 영역의 원천 범위를 연결하며 해석의 인과성을 직접 입증하지 않습니다.",
            }
        )

    trend_sources = source_refs({"시장성", "경쟁 구조"})
    if trend_sources:
        mappings.append(
            {
                "field_path": "trend_analysis",
                "claim_scope": "매출 및 점포 변화",
                "supporting_evidence": [],
                "sources": trend_sources,
                "attribution_level": "field",
                "public_inline_marker": False,
                "limitation": "기간별 관측 원천을 연결하며 미래 추세를 보장하지 않습니다.",
            }
        )

    cost_sources = source_refs({"비용 부담", "비용 참고", "비용 여건"})
    if cost_sources:
        mappings.append(
            {
                "field_path": "user_fit",
                "claim_scope": "예산 및 비용 참고 범위",
                "supporting_evidence": [],
                "sources": cost_sources,
                "attribution_level": "field",
                "public_inline_marker": False,
                "limitation": "비용 참고 원천이며 실제 임대료·권리금 또는 공식 예산 적합도를 뜻하지 않습니다.",
            }
        )

    news_sources = source_refs({NEWS_EVIDENCE_THEME})
    for news_index, item in enumerate(data.get("news_evidence") or []):
        if not isinstance(item, dict):
            continue
        item_title = str(item.get("title") or "")
        item_url = str(item.get("original_url") or "")
        matching_sources = [
            source
            for source in news_sources
            if (item_url and str(source.get("source_url") or "") == item_url)
            or (item_title and str(source.get("title") or "") == item_title)
        ]
        if not matching_sources:
            continue
        monitoring_only = item.get("evidence_tier") == REFERENCE_MONITORING_TIER
        mappings.append(
            {
                "field_path": (
                    f"news_evidence[{news_index}].monitoring_summary"
                    if monitoring_only
                    else f"news_evidence[{news_index}].decision_summary"
                ),
                "claim_scope": (
                    "참고·모니터링 범위"
                    if monitoring_only
                    else item.get("decision_area_label") or "최근 정책·지역 변화"
                ),
                "supporting_evidence": [
                    value
                    for value in (
                        [
                            item.get("selection_reason"),
                            item.get("reference_use"),
                            item.get("applicability_limit"),
                        ]
                        if monitoring_only
                        else [item.get("condition_fit"), item.get("decision_use")]
                    )
                    if value
                ],
                "sources": matching_sources,
                "attribution_level": "item",
                "public_inline_marker": False,
                "limitation": (
                    "업종·예산·지속성의 직접 근거가 부족하므로 점수·등급·추천 판단에 사용하지 않습니다."
                    if monitoring_only
                    else "정량 점수에는 반영하지 않으며 조건 적합성·발표 단계·원문 내용을 별도로 재확인해야 합니다."
                ),
            }
        )

    return mappings


def _single_markdown(
    payload: dict[str, Any],
    result: SingleInterpretation,
    ai_model: str | None,
    news_evidence: list[dict[str, Any]] | None = None,
) -> str:
    def cell(value: Any) -> str:
        return str(value or "-").replace("|", "/").replace("\n", " ").strip()

    def strip_order_prefix(value: Any) -> str:
        return re.sub(r"^\s*(?:\d+\s*[.)]|[①-⑳])\s*", "", str(value or "")).strip()

    header = _default_header_block(payload, result.header_block or {})
    key_metrics = header.get("key_metrics") or []
    key_metric_rows = [
        f"| {cell(item.get('label'))} | {cell(item.get('display'))} | {cell(item.get('note'))} |"
        for item in key_metrics
    ]
    alternative_rows = [
        f"| {cell(item.get('area_name'))} | {cell(item.get('score'))} | {cell(item.get('cost'))} | {cell(item.get('differential'))} | {cell(item.get('judgement'))} |"
        for item in result.alternatives
    ]

    risk_candidates = list(result.risk_factors) or [axis.risk for axis in result.axis_interpretations]
    risk_items: list[str] = []
    for item in risk_candidates:
        if item and item not in risk_items:
            risk_items.append(item)

    action_items: list[str] = []
    for item in result.action_plan:
        cleaned_item = strip_order_prefix(item)
        if cleaned_item and cleaned_item not in action_items:
            action_items.append(cleaned_item)
    onsite_items: list[str] = []
    for item in result.onsite_checklist:
        if item and item not in action_items and item not in onsite_items:
            onsite_items.append(item)

    data_sources = [
        item
        for item in result.source_citations
        if item.theme not in {"해석 기준", "산정 결과", NEWS_EVIDENCE_THEME}
    ]
    source_rows = [
        f"| {cell(item.provider or item.title)} | {cell(item.dataset_name or item.title)} | {cell(item.granularity)} | {cell(item.used_for)} |"
        for item in data_sources
    ]
    period = next((item.period for item in data_sources if item.period), "") or "가용 최신 분기"
    generation_mode_label = {
        "llm": "AI 해석",
        "partial_fallback": "AI 해석 · 일부 규칙 보정",
        "deterministic": "규칙 기반 결과",
    }.get(result.generation_mode, "생성 방식 기록 없음")
    decision_news_rows: list[str] = []
    monitoring_news_rows: list[str] = []
    for item in news_evidence or []:
        source = " · ".join(
            value
            for value in [
                item.get("provider", ""),
                item.get("published_date", ""),
                item.get("title", ""),
                item.get("original_url", ""),
            ]
            if value
        )
        if item.get("evidence_tier") == REFERENCE_MONITORING_TIER:
            monitoring_news_rows.append(
                f"| {cell(source)} | {cell(item.get('selection_reason'))} | "
                f"{cell(item.get('reference_use'))} | {cell(item.get('applicability_limit'))} |"
            )
        else:
            decision_news_rows.append(
                f"| {cell(source)} | {cell(item.get('condition_fit'))} | {cell(item.get('decision_use'))} |"
            )

    lines = [
        f"# {result.narrative_title}",
        "",
        "- 보고서 유형: 상권·업종 입지 리서치",
        f"- 분석 기준: {period}",
        f"- 분석 대상: {payload.get('area_name', '-')} · {payload.get('industry_name') or '상권 맥락'}",
        f"- 생성 방식: {generation_mode_label}",
        "",
        "## 핵심 판단",
        "| 종합 의견 | 입지 등급 |",
        "|---|---|",
        f"| {cell(header.get('judgement_line'))} | {cell(header.get('display_grade') or header.get('grade'))} |",
        "",
        result.executive_interpretation,
        "",
        "### 핵심 지표",
        "| 지표 | 값 | 해석 메모 |",
        "|---|---:|---|",
        *(key_metric_rows or ["| - | - | - |"]),
        "",
        "## 핵심 논거",
        *[f"{idx}. {item}" for idx, item in enumerate(result.thesis or [result.executive_interpretation], 1)],
        "",
        "## 판단 근거",
        "[CHART:C1]",
        "| 판단 영역 | 등급 | 핵심 근거 | 해석 |",
        "|---|---|---|---|",
    ]
    for item in result.axis_interpretations:
        evidence = "; ".join(item.evidence_metrics) if item.evidence_metrics else item.evidence
        lines.append(
            f"| {cell(item.axis)} | {cell(_display_grade(item.display_grade, item.score_display))} | {cell(evidence)} | {cell(item.meaning)} |"
        )
    lines.extend(
        [
            "",
            "## 시장 추이와 업종 구조",
            "[CHART:C2]",
            "[CHART:C3]",
            result.trend_analysis or result.score_interpretation,
            "",
            *(
                [
                    "## 두 단계 외부 자료",
                    "모든 외부 자료는 정형 점수·등급과 분리합니다. 판단 근거는 원문이 직접 뒷받침하는 범위에서만 사용하고, 참고·모니터링 자료는 점수·등급·추천 판단에 사용하지 않습니다.",
                    *(
                        [
                            "### 1단계 · 판단 근거",
                            "| 자료 | 조건 적합성 | 판단에 사용한 방식 |",
                            "|---|---|---|",
                            *decision_news_rows,
                            "",
                        ]
                        if decision_news_rows
                        else []
                    ),
                    *(
                        [
                            "### 2단계 · 참고·모니터링",
                            "| 자료 | 선정 이유 | 참고할 내용 | 판단 제외 사유 |",
                            "|---|---|---|---|",
                            *monitoring_news_rows,
                            "",
                        ]
                        if monitoring_news_rows
                        else []
                    ),
                    "",
                ]
                if decision_news_rows or monitoring_news_rows
                else []
            ),
            "## 대안 상권 비교",
            "[CHART:C4]",
            "| 상권 | 입지 등급 | 비용 여건 등급 | 차이 원인 | 한 줄 판단 |",
            "|---|---|---|---|---|",
            *(alternative_rows or ["| - | - | - | - | - |"]),
            "",
            "## 예산·운영 조건",
            "[CHART:C5]",
            result.user_fit or result.score_interpretation,
            "",
            "## 주요 리스크",
            *([f"- {item}" for item in risk_items[:3]] or ["- 현재 데이터에서 별도 위험 요인이 식별되지 않았습니다."]),
            "",
            "## 실행 우선순위",
            *(
                [f"{idx}. {item}" for idx, item in enumerate(action_items[:4], 1)]
                or ["1. 우선 확인할 실행 항목을 정리합니다."]
            ),
            "",
            "## 현장 확인 항목",
            *(
                [f"- [ ] {item}" for item in onsite_items[:6]]
                or ["- [ ] 실행 우선순위의 항목을 현장에서 대조합니다."]
            ),
            "",
            "## 데이터 출처 및 산정 기준",
            "| 원천 기관 | 데이터셋 | 기준 단위 | 사용 목적 |",
            "|---|---|---|---|",
            *(source_rows or ["| - | - | - | - |"]),
            "",
            "### 산정 기준",
            *[f"- {item}" for item in result.evidence_basis],
            *[f"- {item}" for item in result.methodology_notes],
            "",
            "### 해석 범위",
            *[f"- {item}" for item in result.limitations],
        ]
    )
    markdown = "\n".join(lines)
    return re.sub(
        r"\s*(?:\[NEWS:\d+\]|\[근거\s*\d+\]|"
        r"(?<!\w)근거\s*\d+(?![\d,.]|\s*(?:천|만)?(?:억원|만원|개월|분기|원|억|개|명|건|분|년|월|일|%|㎡)))",
        "",
        markdown,
    )


def _comparison_markdown(payload: dict[str, Any], result: ComparisonInterpretation, ai_model: str | None) -> str:
    score_column = "상권 맥락 등급" if payload.get("score_contract") == "area_context_2axis" else "입지 종합 등급"
    rows = [
        f"| {item.area_name} | {item.interpretation_level} | {item.strong_axis} | {item.watch_axis} | {item.interpretation} |"
        for item in result.comparison_matrix
    ]
    lines = [
        f"# {result.narrative_title}",
        "",
        f"- AI model: {ai_model or 'fallback'}",
        "",
        "## 핵심 해석",
        result.executive_interpretation,
        "",
        "## 비교 해석",
        f"| 상권 | {score_column} | 강한 축 | 확인 축 | 해석 |",
        "|---|---|---|---|---|",
        *rows,
        "",
        "## 사용한 데이터",
        *[f"- {item}" for item in result.evidence_basis],
        "",
        "## 방법론 메모",
        *[f"- {item}" for item in result.methodology_notes],
        "",
        "## 다음 확인",
        *[f"- {item}" for item in result.action_plan],
    ]
    return "\n".join(lines)


def interpret_single_report(payload: dict[str, Any]) -> dict[str, Any]:
    payload = dict(payload)
    payload["_report_model"] = get_openai_model()
    payload["_report_reasoning_effort"] = get_report_reasoning_effort()
    try:
        live_news_rows = fetch_live_naver_news(payload)
    except NaverNewsConnectionError:
        live_news_rows = []
    candidate_news_rows = merge_news_evidence_rows(live_news_rows)
    news_tiers = retrieve_news_evidence_tiers(
        payload,
        decision_limit=3,
        monitoring_limit=3,
        rows=candidate_news_rows,
    )
    decision_news_evidence = news_tiers[DECISION_SUPPORT_TIER]
    monitoring_news_evidence = news_tiers[REFERENCE_MONITORING_TIER]
    news_evidence = [*decision_news_evidence, *monitoring_news_evidence]
    payload["_news_evidence_version"] = news_evidence_version(news_evidence)
    cached = _read_cache(payload)
    if cached:
        cached["header_block"] = _default_header_block(payload, cached.get("header_block"))
        try:
            cached_pack = cached.get("indicator_pack") or build_indicator_pack(payload)
            cached_fallback = _base_single_interpretation(payload, cached_pack)
            cached_clean = _anchor_news_context(
                _normalize_budget_language(
                    _anchor_verified_facts(
                        SingleInterpretation(
                            **{key: value for key, value in cached.items() if key in SingleInterpretation.model_fields}
                        ),
                        cached_fallback,
                    ),
                    payload.get("user_condition") or {},
                ),
                decision_news_evidence,
                payload.get("user_condition") or {},
            )
            cached = _merge_sanitized_cached_interpretation(cached, cached_clean)
            cached = _attach_news_evidence(cached, news_evidence)
            cached["claim_source_map"] = _build_claim_source_map(cached)
            cached_clean = SingleInterpretation(
                **{key: value for key, value in cached.items() if key in SingleInterpretation.model_fields}
            )
            cached["markdown_body"] = _sanitize_claims(
                _single_markdown(payload, cached_clean, cached.get("ai_model"), news_evidence)
            )
        except Exception:
            cached["news_evidence"] = news_evidence
            cached["decision_news_evidence"] = decision_news_evidence
            cached["monitoring_news_evidence"] = monitoring_news_evidence
        return cached

    indicator_pack = build_indicator_pack(payload)
    evidence_frames = retrieve_evidence_pack(payload, limit=8)
    indicator_pack["evidence_frames"] = evidence_frames
    fallback = _base_single_interpretation(payload, indicator_pack)
    ai_model = str(payload["_report_model"])
    reasoning_effort = str(payload["_report_reasoning_effort"])
    facts_display = indicator_pack.get("facts_pack_display") or {}
    chart_manifest = indicator_pack.get("chart_manifest") or []
    user_condition = payload.get("user_condition") or facts_display.get("user_condition") or {}
    result = fallback
    ai_generated = False
    validation_issues: list[str] = []
    original_validation_issues: list[str] = []
    quality_warnings: list[str] = []
    fallback_fields: set[str] = set()
    usage_handler = UsageMetadataCallbackHandler()
    section_repair_log: list[dict[str, Any]] = []
    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                (
                    "너는 서울 상권 데이터를 창업 의사결정에 맞게 해석하는 한국어 분석가다.\n"
                    "\n"
                    "[우선순위 1 — 반드시 지킬 사실 계약]\n"
                    "- 매출·인구·점포·임대 참고값 같은 관측 수치는 facts pack의 display 값만 인용한다. 새 관측값, 비율, 순위, 기간을 계산하거나 만들지 않는다.\n"
                    "- 사용자가 입력한 예산은 만원 표기 또는 같은 금액의 자연스러운 억원 표기로 쓸 수 있지만, 권장 배분액을 새로 만들지 않는다.\n"
                    "- cost_block.budget_fit의 공식 적합도가 보류 상태이면 예산이 충분하다거나 진입·검토가 가능하다고 단정하지 않는다. 예산은 입력 상한으로만 다루고 실제 임대료·권리금·공사비가 없다는 한계를 밝힌다.\n"
                    "- score_block.coverage의 공식 판단이 보류 상태이면 유망 후보·상위 후보·추천 상권·높은 적합성이라고 부르지 않는다. 확인 가능한 지표 범위의 참고값이라고 쓴다.\n"
                    "- sales, competition, demand, accessibility, context_only 같은 내부 필드 코드는 본문에 쓰지 않고 각각 시장성, 경쟁 구조, 수요 기반, 접근·유입처럼 독자가 이해하는 말로 바꾼다.\n"
                    "- 종합 판단, 판단 영역, 비용 여건, 신뢰도, 성장성은 제공된 A+~E 등급만 쓴다. raw 점수·백분위·점수 순위 숫자는 사용자 문장에 쓰지 않는다.\n"
                    "- 판단 영역별 meaning에는 해당 영역의 metrics와 score_drivers에 포함된 관측 수치만 사용한다. 다른 영역의 수치나 등급을 가져와 비율·인과관계로 연결하지 않는다.\n"
                    "- 해당 판단 영역의 score_drivers가 비어 있으면 게시 등급과 세부 근거 미표시 사실만 말한다. 상주·직장·유동인구를 접근·유입 등급의 원인으로 재사용하지 않는다.\n"
                    "- 관측 사실, 그 사실에서 읽은 해석, 사용자가 확인할 행동을 서로 섞어 확정 사실처럼 쓰지 않는다.\n"
                    "- 상주·직장·유동인구와 합산 추정매출은 이용 접점 가능성에 대한 관측 신호다. 이를 실제 수요, 반복 접점, 방문·구매 전환이 충분하다는 증명으로 쓰지 않는다.\n"
                    "- DB., gold., 테이블명, 파일 경로 같은 내부 식별자는 절대 본문에 쓰지 않는다. 출처 표기는 코드가 별도 섹션에서 처리한다.\n"
                    "- 금지 주장: 창업 성공확률, 개별 매장 매출 보장, 성장률 보장, 실제 방문확률, 월세/권리금 직접값 단정.\n"
                    "- cost 관련 파생 평가는 비용 여건 등급으로만 서술하며 A+에 가까울수록 상대적으로 유리한 방향이다.\n"
                    "- 외부 자료는 코드가 별도 자료표로 붙인다. 모델 산문에는 외부 자료의 제목·내용·주장을 사용하지 않는다.\n"
                    "- 본문에는 외부 자료 번호·각주·링크 표식을 쓰지 않는다. frame_citations에는 제공된 해석 프레임 번호만 넣는다.\n"
                    "\n"
                    "[우선순위 2 — 담아야 할 판단 내용]\n"
                    "- executive_interpretation 앞부분에서 검토 자세를 분명히 하고, 가장 강한 근거와 가장 큰 제약, 입력 업종·예산의 의미를 함께 설명한다. 고정된 첫 문장 형식은 없다.\n"
                    "- thesis는 서로 겹치지 않는 핵심 논지만 고른다. 각 논지의 문장 순서와 연결 방식은 내용에 맞게 자유롭게 쓴다.\n"
                    "- 각 판단 영역의 meaning은 근거 지표를 낭독하지 말고 그 조합이 이 업종의 입지 판단에 왜 중요한지 설명한다. risk와 action은 그 판단을 바꿀 조건과 확인 방법을 구체화한다.\n"
                    "- trend_analysis는 facts pack의 분기 흐름과 경쟁 변화만 종합한다. 외부 자료를 산문에 섞지 않는다.\n"
                    "- user_fit은 입력 예산과 업종에서 가능한 검토 자세와 포기 조건을 말한다. 대안 상권은 등급 차이를 만든 원인 축으로 비교한다.\n"
                    "- risk_factors, action_plan, onsite_checklist에는 각각 위험, 조사 우선순위, 현장 확인 항목이 드러나야 한다.\n"
                    "\n"
                    "[문체 선호 — 사실 계약보다 낮은 우선순위]\n"
                    "- 자연스럽고 간결한 한국어를 쓰며, 섹션마다 같은 결론-근거-행동 문형을 기계적으로 반복하지 않는다.\n"
                    "- 같은 수치와 면책 문장을 불필요하게 되풀이하지 않는다. 다만 자연성을 위해 특정 연결어·문장 수·글자 수를 맞출 필요는 없다.\n"
                    "- source_citations, evidence_basis, methodology_notes, limitations, claim_source_map은 생성하지 않는다. 검증된 코드가 붙인다."
                ),
            ),
            (
                "user",
                (
                    "Facts pack display only:\n{facts_pack_display}\n\n"
                    "차트 목록. 해석 가능한 시각 자료의 범위만 참고하고 C1 같은 내부 ID는 출력하지 않는다:\n{chart_manifest}\n\n"
                    "해석 프레임. 사실 원천이 아니며, 필요할 때 frame_citations에 제공된 번호만 기록한다:\n{evidence_frames}\n\n"
                    "사용자 조건:\n{user_condition}\n\n"
                    "critic_feedback:\n{critic_feedback}"
                ),
            ),
        ]
    )
    def prepare_candidate(
        candidate: SingleInterpretation,
        *,
        sanitize: bool,
    ) -> tuple[SingleInterpretation, list[str]]:
        candidate = _anchor_news_context(
            _normalize_budget_language(
                _anchor_verified_facts(
                    _fill_axis_citation_gaps(_fill_single_gaps(candidate, fallback), fallback),
                    fallback,
                ),
                user_condition,
            ),
            decision_news_evidence,
            user_condition,
        )
        # 원천·산정 기준은 LLM 출력과 무관하게 검증된 코드 결과로 강제한다.
        candidate.narrative_title = fallback.narrative_title
        candidate.source_citations = list(fallback.source_citations)
        candidate.evidence_basis = list(fallback.evidence_basis)
        candidate.methodology_notes = list(METHODOLOGY_NOTES)
        candidate.limitations = list(LIMITATIONS)
        candidate_data = candidate.model_dump()
        if sanitize:
            candidate_data = _sanitize_claims(candidate_data)
        clean_candidate = SingleInterpretation(**candidate_data)
        markdown = _single_markdown(
            payload,
            clean_candidate,
            ai_model if ai_generated else None,
            news_evidence,
        )
        if sanitize:
            markdown = _sanitize_claims(markdown)
        validation_issues = validate_report_draft(
            candidate_data,
            facts_pack_display=facts_display,
            user_condition=user_condition,
            evidence_frames=evidence_frames,
            markdown_body=markdown,
        )
        return clean_candidate, validation_issues

    try:
        chain = prompt | get_llm(
            reasoning_effort=reasoning_effort
        ).with_structured_output(SingleInterpretation, method="function_calling")
        candidate = chain.invoke(
            {
                "facts_pack_display": _json(facts_display),
                "chart_manifest": _json(chart_manifest),
                "evidence_frames": evidence_pack_for_prompt(evidence_frames),
                "user_condition": _json(user_condition),
                "critic_feedback": "없음",
            },
            config={"callbacks": [usage_handler]},
        )
        ai_generated = True
    except Exception as exc:
        logger.warning(
            "AI single-report generation failed (%s): %s",
            type(exc).__name__,
            str(exc)[:500],
        )
        raise ReportGenerationError("single_report", exc) from exc

    fallback_fields.update(_fallback_gap_fields(candidate, fallback))
    result, initial_issues = prepare_candidate(candidate, sanitize=False)
    original_validation_issues = list(initial_issues)
    initial_hard_issues = [issue for issue in initial_issues if not _is_advisory_issue(issue)]

    if initial_hard_issues and ai_generated:
        repaired_candidate, repair_fallback_fields, llm_repaired_fields = _repair_invalid_fields(
            current=result,
            fallback=fallback,
            validation_issues=initial_hard_issues,
            facts_display=facts_display,
            user_condition=user_condition,
            usage_handler=usage_handler,
            reasoning_effort=reasoning_effort,
        )
        fallback_fields.update(repair_fallback_fields)
        result, repaired_issues = prepare_candidate(repaired_candidate, sanitize=False)
        section_repair_log.append(
            {
                "attempt": 1,
                "mode": "field_llm",
                "fields": llm_repaired_fields,
                "fallback_fields": repair_fallback_fields,
                "before_count": len(initial_hard_issues),
                "after_count": len(repaired_issues),
            }
        )

        remaining_hard_issues = [
            issue for issue in repaired_issues if not _is_advisory_issue(issue)
        ]
        if remaining_hard_issues:
            hard_fields = _field_targets_for_violations(remaining_hard_issues)
            result, applied_fields = _replace_fields_with_fallback(result, fallback, hard_fields)
            fallback_fields.update(applied_fields)
            result, fallback_issues = prepare_candidate(result, sanitize=False)
            section_repair_log.append(
                {
                    "attempt": 1,
                    "mode": "field_deterministic_fallback",
                    "fields": applied_fields,
                    "before_count": len(repaired_issues),
                    "after_count": len(fallback_issues),
                }
            )

    result, final_issues = prepare_candidate(result, sanitize=True)
    validation_issues = [issue for issue in final_issues if not _is_advisory_issue(issue)]
    quality_warnings = [issue for issue in final_issues if _is_advisory_issue(issue)]

    generation_mode = _generation_mode_for(result, fallback, fallback_fields)
    ai_generated = generation_mode != "deterministic"

    data = _sanitize_claims(result.model_dump())
    data["original_validation_issues"] = original_validation_issues
    data["validation_issues"] = validation_issues
    data["quality_warnings"] = quality_warnings
    data["quality_status"] = "pass" if not validation_issues else "partial"
    data["generation_mode"] = generation_mode
    data["fallback_fields"] = sorted(fallback_fields)
    data["header_block"] = _default_header_block(payload, data.get("header_block"))
    data = _attach_news_evidence(data, news_evidence)
    data["claim_source_map"] = _build_claim_source_map(data)
    clean_result = SingleInterpretation(**data)
    data["visualization_data"] = _visualization_data(payload)
    data["markdown_body"] = _sanitize_claims(
        _single_markdown(payload, clean_result, ai_model if ai_generated else None, news_evidence)
    )
    data["ai_model"] = ai_model
    data["reasoning_effort"] = reasoning_effort
    data["ai_generated"] = ai_generated
    data["indicator_pack"] = indicator_pack
    data["facts_pack_display"] = facts_display
    data["facts_lite_display"] = indicator_pack.get("facts_lite_display") or {}
    data["evidence_frames"] = evidence_frames
    data["section_repair_log"] = section_repair_log
    input_text = (
        _json(facts_display)
        + _json(chart_manifest)
        + evidence_pack_for_prompt(evidence_frames)
        + _json(user_condition)
    )
    output_text = _json(data.get("header_block")) + _json(data.get("axis_interpretations")) + data["markdown_body"]
    data["token_usage"] = _provider_token_usage(usage_handler, fallback_model=ai_model) or {
        "estimated": True,
        "input_tokens": _estimate_tokens(input_text),
        "output_tokens": _estimate_tokens(output_text),
        "total_tokens": _estimate_tokens(input_text) + _estimate_tokens(output_text),
        "model": ai_model,
        "cache_hit": False,
        "spec_version": SPEC_VERSION,
    }
    data["token_usage"]["reasoning_effort"] = reasoning_effort
    cacheable = (
        generation_mode in {"llm", "partial_fallback"}
        and ai_generated
        and data["quality_status"] == "pass"
        and not validation_issues
    )
    data["cache_meta"] = {
        "cache_hit": False,
        "cache_key": _cache_key(payload),
        "spec_version": SPEC_VERSION,
        "cacheable": cacheable,
        "token_usage": data["token_usage"],
    }
    if cacheable:
        _write_cache(payload, data)
    return data


def interpret_comparison_report(payload: dict[str, Any]) -> dict[str, Any]:
    fallback = _base_comparison_interpretation(payload)
    ai_model = get_openai_model()
    reasoning_effort = get_report_reasoning_effort()
    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                (
                    "너는 서울 상권 후보들을 비교하는 한국어 분석가다. "
                    "사용자 문장에는 comparison payload의 display_grade와 axis_display_grades만 쓰며 raw 숫자 점수는 인용하지 않는다. "
                    "후보 간 차이를 원인 축으로 설명하며, 각 문장의 첫머리에 결론을 둔다. "
                    "DB., gold., 테이블명, 파일 경로 같은 내부 식별자는 본문에 쓰지 않는다. "
                    "같은 수치를 반복 인용하지 않고, 해석은 축 등급 낭독이 아니라 후보 간 구조 차이 서술로 쓴다. "
                    "성공확률, 매출 보장, 성장률 보장, 월세/권리금 확정, 수익성 보장 표현은 금지한다."
                ),
            ),
            ("user", "comparison_payload:\n{payload}\n\ncritic_feedback:\n{critic_feedback}"),
        ]
    )
    result = fallback
    ai_generated = False
    validation_issues: list[str] = []
    feedback = ""
    for _attempt in range(2):
        try:
            chain = prompt | get_llm(
                reasoning_effort=reasoning_effort
            ).with_structured_output(ComparisonInterpretation, method="function_calling")
            candidate = chain.invoke({"payload": _json(payload), "critic_feedback": feedback or "없음"})
            ai_generated = True
        except Exception as exc:
            logger.warning(
                "AI comparison-report generation failed (%s): %s",
                type(exc).__name__,
                str(exc)[:500],
            )
            raise ReportGenerationError("comparison_report", exc) from exc
        candidate = _fill_comparison_gaps(candidate, fallback)
        if payload.get("score_contract") == "area_context_2axis":
            candidate.executive_interpretation = fallback.executive_interpretation
            candidate.comparison_matrix = fallback.comparison_matrix
            candidate.evidence_basis = fallback.evidence_basis
            candidate.source_citations = fallback.source_citations
            candidate.methodology_notes = fallback.methodology_notes
            candidate.limitations = fallback.limitations
            candidate.summary = fallback.summary
            candidate.top_recommendation_reason = fallback.top_recommendation_reason
        else:
            candidate.methodology_notes = list(METHODOLOGY_NOTES)
            candidate.limitations = list(LIMITATIONS)
        validation_issues = validate_comparison_draft(candidate.model_dump(), payload)
        result = candidate
        if not validation_issues or not ai_generated:
            break
        feedback = "\n".join(validation_issues)

    data = _sanitize_claims(result.model_dump())
    if payload.get("score_contract") == "area_context_2axis":
        data["methodology_notes"] = fallback.methodology_notes
        data["limitations"] = fallback.limitations
    else:
        data["methodology_notes"] = list(METHODOLOGY_NOTES)
        data["limitations"] = list(LIMITATIONS)
    data["validation_issues"] = validation_issues
    data["quality_status"] = "pass" if not validation_issues else "partial"
    clean_result = ComparisonInterpretation(**{k: v for k, v in data.items() if k in ComparisonInterpretation.model_fields})
    data["visualization_data"] = _comparison_visualization_data(payload)
    data["markdown_body"] = _sanitize_claims(_comparison_markdown(payload, clean_result, ai_model if ai_generated else None))
    data["ai_model"] = ai_model
    data["reasoning_effort"] = reasoning_effort
    data["ai_generated"] = ai_generated
    return data
