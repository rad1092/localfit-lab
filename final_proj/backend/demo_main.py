"""LocalFit Lab public execution demo API.

This module is intentionally isolated from the production database, accounts,
external APIs, and secrets. Every value returned here is synthetic sample data
for exercising the public UI.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware


DEMO_NOTICE = (
    "실행 데모용 합성 샘플 데이터입니다. 실제 매출·인구·임대료 또는 "
    "공식 상권 평가로 해석하지 마세요."
)

app = FastAPI(
    title="LocalFit Lab Execution Demo API",
    version="1.0.0-demo",
    description=DEMO_NOTICE,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:4310",
        "http://localhost:4310",
        "http://127.0.0.1:3000",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


AREAS: list[dict[str, Any]] = [
    {
        "area_code": "DEMO-HONGDAE",
        "area_name": "홍대입구역",
        "district_name": "마포구",
        "latitude": 37.5563,
        "longitude": 126.9237,
        "score": 88,
        "grade": "A",
        "display_grade": "A+",
        "trend": "+2",
        "resident": 24_800,
        "worker": 31_500,
        "floating": 1_420_000,
        "sales": 18_650_000_000,
        "stores": 312,
        "rent": 84.0,
        "vacancy": 5.8,
    },
    {
        "area_code": "DEMO-SEONGSU",
        "area_name": "성수역",
        "district_name": "성동구",
        "latitude": 37.5446,
        "longitude": 127.0559,
        "score": 84,
        "grade": "A",
        "display_grade": "A",
        "trend": "+4",
        "resident": 21_300,
        "worker": 43_900,
        "floating": 1_180_000,
        "sales": 16_240_000_000,
        "stores": 268,
        "rent": 78.0,
        "vacancy": 4.9,
    },
    {
        "area_code": "DEMO-GANGNAM",
        "area_name": "강남역",
        "district_name": "강남구",
        "latitude": 37.4979,
        "longitude": 127.0276,
        "score": 81,
        "grade": "A",
        "display_grade": "A",
        "trend": "+1",
        "resident": 18_100,
        "worker": 82_600,
        "floating": 1_760_000,
        "sales": 24_980_000_000,
        "stores": 421,
        "rent": 132.0,
        "vacancy": 6.7,
    },
    {
        "area_code": "DEMO-YEONNAM",
        "area_name": "연남동",
        "district_name": "마포구",
        "latitude": 37.5658,
        "longitude": 126.9232,
        "score": 77,
        "grade": "B",
        "display_grade": "B+",
        "trend": "-1",
        "resident": 28_600,
        "worker": 17_400,
        "floating": 910_000,
        "sales": 11_720_000_000,
        "stores": 224,
        "rent": 69.0,
        "vacancy": 5.2,
    },
    {
        "area_code": "DEMO-JAMSIL",
        "area_name": "잠실역",
        "district_name": "송파구",
        "latitude": 37.5133,
        "longitude": 127.1002,
        "score": 74,
        "grade": "B",
        "display_grade": "B",
        "trend": "+1",
        "resident": 35_700,
        "worker": 46_200,
        "floating": 1_310_000,
        "sales": 19_430_000_000,
        "stores": 356,
        "rent": 96.0,
        "vacancy": 5.5,
    },
]

INDUSTRIES: list[dict[str, str]] = [
    {
        "industry_code": "DEMO-CAFE",
        "industry_name": "커피·음료",
        "display_label": "외식 > 카페 > 커피·음료",
        "selection_path": "외식 > 카페 > 커피·음료",
        "major": "외식",
        "middle": "카페",
        "detail": "커피·음료",
    },
    {
        "industry_code": "DEMO-BAKERY",
        "industry_name": "제과·베이커리",
        "display_label": "외식 > 디저트 > 제과·베이커리",
        "selection_path": "외식 > 디저트 > 제과·베이커리",
        "major": "외식",
        "middle": "디저트",
        "detail": "제과·베이커리",
    },
    {
        "industry_code": "DEMO-KOREAN",
        "industry_name": "한식 일반 음식점",
        "display_label": "외식 > 음식점 > 한식 일반 음식점",
        "selection_path": "외식 > 음식점 > 한식 일반 음식점",
        "major": "외식",
        "middle": "음식점",
        "detail": "한식 일반 음식점",
    },
    {
        "industry_code": "DEMO-RETAIL",
        "industry_name": "생활 편집숍",
        "display_label": "소매 > 생활용품 > 생활 편집숍",
        "selection_path": "소매 > 생활용품 > 생활 편집숍",
        "major": "소매",
        "middle": "생활용품",
        "detail": "생활 편집숍",
    },
]

REPORT_JOBS: dict[str, dict[str, Any]] = {}


def _score_metadata() -> dict[str, Any]:
    return {
        "score_type": "demand_accessibility_context",
        "score_label": "수요·접근성 맥락 등급",
        "official_rank_eligible": False,
    }


def _find_area(area_code: str) -> dict[str, Any]:
    normalized = area_code.strip().upper()
    for area in AREAS:
        if area["area_code"].upper() == normalized:
            return area
    raise HTTPException(status_code=404, detail="데모 상권을 찾지 못했습니다.")


def _find_industry(industry_code: str | None) -> dict[str, str]:
    if industry_code:
        normalized = industry_code.strip().upper()
        for industry in INDUSTRIES:
            if industry["industry_code"].upper() == normalized:
                return industry
    return INDUSTRIES[0]


def _ranking(area: dict[str, Any], rank: int) -> dict[str, Any]:
    return {
        "rank": rank,
        "area_code": area["area_code"],
        "area_name": area["area_name"],
        "score": area["score"],
        "grade": area["grade"],
        "display_grade": area["display_grade"],
        "trend": area["trend"],
        **_score_metadata(),
    }


def _area_detail(area: dict[str, Any], industry_code: str | None = None) -> dict[str, Any]:
    industry = _find_industry(industry_code)
    sales = int(area["sales"])
    stores = int(area["stores"])
    industry_sales = round(sales * 0.18)
    industry_stores = max(12, round(stores * 0.15))
    history = [
        ("20252", 0.84),
        ("20253", 0.90),
        ("20254", 0.95),
        ("20261", 1.00),
    ]
    detail = {
        "area_code": area["area_code"],
        "area_name": area["area_name"],
        "district_code": f"DEMO-{area['district_name']}",
        "latitude": area["latitude"],
        "longitude": area["longitude"],
        "score": area["score"],
        "grade": area["grade"],
        "display_grade": area["display_grade"],
        **_score_metadata(),
        "district_populations": [
            {
                "district_name": area["district_name"],
                "resident_population": area["resident"],
                "worker_population": area["worker"],
                "timestamp": "20261",
            }
        ],
        "district_floatings": [
            {
                "floating_population": area["floating"],
                "timestamp": "20261",
            }
        ],
        "district_sales": [
            {
                "industry_code": "DEMO-CAFE",
                "industry_name": "커피·음료",
                "sales_amount": round(sales * 0.18),
                "timestamp": "20261",
            },
            {
                "industry_code": "DEMO-KOREAN",
                "industry_name": "한식 일반 음식점",
                "sales_amount": round(sales * 0.32),
                "timestamp": "20261",
            },
            {
                "industry_code": "DEMO-RETAIL",
                "industry_name": "생활 편집숍",
                "sales_amount": round(sales * 0.14),
                "timestamp": "20261",
            },
        ],
        "district_store_counts": [
            {
                "industry_code": "DEMO-CAFE",
                "industry_name": "커피·음료",
                "store_count": max(12, round(stores * 0.15)),
                "timestamp": "20261",
            },
            {
                "industry_code": "DEMO-KOREAN",
                "industry_name": "한식 일반 음식점",
                "store_count": max(18, round(stores * 0.24)),
                "timestamp": "20261",
            },
            {
                "industry_code": "DEMO-RETAIL",
                "industry_name": "생활 편집숍",
                "store_count": max(8, round(stores * 0.09)),
                "timestamp": "20261",
            },
        ],
        "district_growth_histories": [
            {
                "sales_amount": round(sales * factor),
                "floating_population": round(area["floating"] * factor),
                "store_count": max(1, round(stores * (0.92 + (factor - 0.84) / 2))),
                "timestamp": quarter,
            }
            for quarter, factor in history
        ],
        "sale_price_proxies": [
            {
                "sale_price_proxy_manwon_per_m2": round(area["rent"] * 31.5, 1),
                "period": "20261",
                "source_id": "demo.synthetic.v1",
                "provider": "LocalFit Lab 실행 데모",
                "grain": "synthetic_sample",
                "direct_score_allowed": False,
                "proxy_score_allowed": False,
                "provenance_note": DEMO_NOTICE,
            }
        ],
        "rone_cost_references": [
            {
                "period": "20261",
                "selection_group": area["district_name"],
                "metric_code": "rent",
                "metric_name": "합성 임대료 참고값",
                "metric_value": area["rent"],
                "unit": "천원/㎡",
                "property_type": "중대형 상가",
                "source_region_name": area["district_name"],
                "mapping_scope": "demo_synthetic",
                "mapping_method": "static_fixture",
                "mapping_confidence": "demo_only",
                "source_id": "demo.synthetic.v1",
                "provider": "LocalFit Lab 실행 데모",
                "direct_value_allowed": False,
                "proxy_score_allowed": False,
                "engine_promotion_ready": False,
                "forbidden_claim_ko": "실제 임대료로 인용할 수 없습니다.",
                "provenance_note": DEMO_NOTICE,
            },
            {
                "period": "20261",
                "selection_group": area["district_name"],
                "metric_code": "vacancy",
                "metric_name": "합성 공실률 참고값",
                "metric_value": area["vacancy"],
                "unit": "%",
                "property_type": "중대형 상가",
                "source_region_name": area["district_name"],
                "mapping_scope": "demo_synthetic",
                "mapping_method": "static_fixture",
                "mapping_confidence": "demo_only",
                "source_id": "demo.synthetic.v1",
                "provider": "LocalFit Lab 실행 데모",
                "direct_value_allowed": False,
                "proxy_score_allowed": False,
                "engine_promotion_ready": False,
                "forbidden_claim_ko": "실제 공실률로 인용할 수 없습니다.",
                "provenance_note": DEMO_NOTICE,
            },
        ],
        "industry_analysis": {
            "industry_code": industry["industry_code"],
            "industry_name": industry["industry_name"],
            "reference_quarter": "20261",
            "availability": "available",
            "display_grade": area["display_grade"],
            "score_applicable": False,
            "score_version": "demo.synthetic.v1",
            "score_reason": DEMO_NOTICE,
            "current_sales_amount": industry_sales,
            "current_store_count": industry_stores,
            "history": [
                {
                    "quarter": quarter,
                    "sales_amount": round(industry_sales * factor),
                    "store_count": max(1, round(industry_stores * (0.94 + (factor - 0.84) / 2))),
                }
                for quarter, factor in history
            ],
            "axes": {
                "sales": {"internal_value": 82, "display_grade": area["display_grade"]},
                "competition": {"internal_value": 63, "display_grade": "B"},
                "demand": {"internal_value": 79, "display_grade": "A"},
                "accessibility": {"internal_value": 86, "display_grade": "A+"},
            },
            "missing_data": [],
        },
        "demo": True,
        "demo_notice": DEMO_NOTICE,
    }
    return detail


def _metric(label: str, raw: float, display: str, unit: str = "") -> dict[str, Any]:
    return {"label": label, "raw": raw, "display": display, "unit": unit}


def _single_report(area: dict[str, Any], business_type: str | None, budget: int | None) -> dict[str, Any]:
    industry_name = business_type or "커피·음료"
    budget_text = f"{budget:,}만원" if budget else "예산 미입력"
    return {
        "summary": (
            f"{area['area_name']}은 샘플 기준으로 유입과 접근성이 강한 후보입니다. "
            f"{industry_name} 출점 전에는 시간대별 보행과 실제 임대 조건을 현장에서 확인하세요."
        ),
        "narrative_title": f"{area['area_name']} · {industry_name} 실행 데모 리포트",
        "executive_interpretation": (
            "수요는 긍정적이지만 경쟁 밀도와 비용 부담을 함께 확인해야 하는 후보입니다. "
            "이 문장은 데모 흐름을 보여주기 위한 결정론적 예시입니다."
        ),
        "score_interpretation": "샘플 수요·접근성 맥락 등급은 A이며 공식 평가가 아닙니다.",
        "trend_analysis": "네 개 샘플 분기에서 매출과 유동 인구가 완만하게 증가하는 형태입니다.",
        "user_fit": f"입력 예산: {budget_text}. 실제 계약 전 손익분기점 검증이 필요합니다.",
        "strengths": [
            "대중교통 접근성과 유동 흐름이 함께 강한 샘플 후보",
            "주거·업무 수요가 혼합되어 시간대 분산 가능",
            "인접 상권과 비교 분석을 이어가기 쉬움",
        ],
        "weaknesses": [
            "동일 업종 경쟁이 높아 콘셉트 차별화가 필요",
            "임대 조건 변화에 손익이 민감할 수 있음",
        ],
        "recommended_businesses": [industry_name, "테이크아웃 특화 소형 매장", "지역 협업형 팝업"],
        "risk_factors": [
            "실제 임대료와 권리금은 샘플 데이터에 포함되지 않음",
            "요일·시간대별 현장 보행량을 별도로 확인해야 함",
            "본 데모 수치를 실제 투자 판단에 사용할 수 없음",
        ],
        "industry_code": "DEMO-CAFE",
        "industry_name": industry_name,
        "score_source": "demo.synthetic.v1",
        "axis_interpretations": [
            {
                "axis": "수요",
                "display_grade": "A",
                "meaning": "샘플 유동·거주·직장 인구가 고르게 배치되었습니다.",
                "evidence": "합성 샘플 인구 및 유입 데이터",
                "risk": "실측 유동과 차이가 날 수 있습니다.",
                "action": "평일·주말의 세 시간대를 직접 관찰하세요.",
            },
            {
                "axis": "경쟁",
                "display_grade": "B",
                "meaning": "수요 대비 동종 점포도 많은 형태입니다.",
                "evidence": "합성 샘플 업종 점포 수",
                "risk": "가격 경쟁보다 콘셉트 경쟁이 클 수 있습니다.",
                "action": "반경 500m 경쟁점의 가격·회전율을 기록하세요.",
            },
            {
                "axis": "접근성",
                "display_grade": "A+",
                "meaning": "대중교통 결절점에 가까운 샘플 후보입니다.",
                "evidence": "합성 샘플 위치 및 접근성 축",
                "risk": "역세권 내부에서도 동선 편차가 큽니다.",
                "action": "출구별 주 동선과 가시성을 확인하세요.",
            },
        ],
        "alternatives": [
            {
                "area_name": "성수역",
                "display_grade": "A",
                "judgement": "업무 수요와 목적형 방문을 함께 비교할 샘플 후보",
            },
            {
                "area_name": "연남동",
                "display_grade": "B+",
                "judgement": "생활권 고객과 주말 방문 수요를 비교할 샘플 후보",
            },
        ],
        "header_block": {
            "judgement_line": "유입 강점은 분명하지만 경쟁·비용을 현장에서 검증할 후보",
            "score_label": "샘플 수요·접근성 맥락 등급",
            "display_grade": area["display_grade"],
            "key_metrics": [
                {"label": "샘플 유동", "display": f"{area['floating']:,}명/분기", "note": "합성값"},
                {"label": "샘플 점포", "display": f"{area['stores']:,}개", "note": "합성값"},
                {"label": "입력 예산", "display": budget_text, "note": "사용자 입력"},
            ],
        },
        "indicator_pack": {
            "facts_pack": {
                "target": {"area_name": area["area_name"], "industry_name": industry_name},
                "score_block": {
                    "current_location_score": _metric("샘플 맥락", area["score"], area["display_grade"]),
                    "axis_scores": {
                        "sales": _metric("시장성", 82, "A"),
                        "competition": _metric("경쟁 구조", 63, "B"),
                        "demand": _metric("수요 기반", 79, "A"),
                        "accessibility": _metric("접근·유입", 86, "A+"),
                    },
                    "supporting_signals": {
                        "cost_risk_score": _metric("비용 주의", 68, "B"),
                        "data_reliability_score": _metric("데모 데이터", 50, "샘플"),
                        "growth_potential_score": _metric("성장 흐름", 73, "B+"),
                    },
                },
                "sales_block": {
                    "sales_trend": [
                        {
                            "timestamp": quarter,
                            "sales_amount": _metric(
                                "샘플 매출",
                                round(area["sales"] * factor),
                                f"{round(area['sales'] * factor / 100_000_000, 1)}억원",
                                "원",
                            ),
                        }
                        for quarter, factor in [
                            ("2025 Q2", 0.84),
                            ("2025 Q3", 0.90),
                            ("2025 Q4", 0.95),
                            ("2026 Q1", 1.00),
                        ]
                    ],
                    "area_top_industries": [
                        {
                            "rank": 1,
                            "industry_name": "한식 일반 음식점",
                            "sales_amount": _metric("샘플 매출", area["sales"] * 0.32, "32%"),
                        },
                        {
                            "rank": 2,
                            "industry_name": "커피·음료",
                            "sales_amount": _metric("샘플 매출", area["sales"] * 0.18, "18%"),
                        },
                        {
                            "rank": 3,
                            "industry_name": "생활 편집숍",
                            "sales_amount": _metric("샘플 매출", area["sales"] * 0.14, "14%"),
                        },
                    ],
                },
                "demand_block": {
                    "resident_population": _metric("거주 인구", area["resident"], f"{area['resident']:,}명", "명"),
                    "worker_population": _metric("직장 인구", area["worker"], f"{area['worker']:,}명", "명"),
                    "floating_population": _metric("유동 인구", area["floating"], f"{area['floating']:,}명", "명"),
                    "floating_population_daily_average": _metric(
                        "일평균 유동",
                        round(area["floating"] / 90),
                        f"{round(area['floating'] / 90):,}명",
                        "명",
                    ),
                },
                "cost_block": {"cost_risk_score": _metric("비용 주의", 68, "B")},
                "alternatives": [
                    {
                        "area_name": "성수역",
                        "display_grade": "A",
                        "current_location_score": _metric("샘플 맥락", 84, "A"),
                        "cost_risk_score": _metric("비용 주의", 65, "B"),
                        "major_differential_axis": "업무 수요",
                    },
                    {
                        "area_name": "연남동",
                        "display_grade": "B+",
                        "current_location_score": _metric("샘플 맥락", 77, "B+"),
                        "cost_risk_score": _metric("비용 주의", 58, "B"),
                        "major_differential_axis": "생활권 수요",
                    },
                ],
                "data_period_text": "합성 샘플 2025 Q2~2026 Q1",
            }
        },
        "evidence_basis": [
            "공개 UI 동작 확인을 위한 합성 샘플 데이터",
            "운영 DB·회원·외부 API·실제 평가 결과는 포함하지 않음",
        ],
        "source_citations": [
            {
                "title": "LocalFit Lab 실행 데모 픽스처",
                "provider": "LocalFit Lab",
                "dataset_name": "demo.synthetic.v1",
                "period": "샘플 2025 Q2~2026 Q1",
                "granularity": "5개 데모 상권",
                "theme": "실행 데모",
                "used_for": "화면과 API 흐름 재현",
                "caveat": DEMO_NOTICE,
            }
        ],
        "methodology_notes": [DEMO_NOTICE],
        "action_plan": [
            "후보 점포 앞의 평일·주말 유입을 직접 기록",
            "실제 임대료·권리금·관리비를 계약서 기준으로 재계산",
            "동종 점포의 메뉴·가격·회전율을 비교",
        ],
        "onsite_checklist": [
            "역 출구에서 점포까지 보행 동선",
            "간판 가시성과 횡단보도 대기 동선",
            "배달·주차·폐기물 처리 조건",
        ],
        "limitations": [DEMO_NOTICE],
        "markdown_body": f"# {area['area_name']} 실행 데모 리포트\n\n{DEMO_NOTICE}",
        "ai_model": "none-demo-deterministic",
        "ai_generated": False,
        "generation_mode": "deterministic",
        "quality_status": "pass",
        "quality_warnings": [DEMO_NOTICE],
        "validation_issues": [],
        "original_validation_issues": [],
        "fallback_fields": [],
        "news_evidence": [],
        "demo": True,
        "demo_notice": DEMO_NOTICE,
    }


@app.get("/")
def root() -> dict[str, Any]:
    return {
        "service": "LocalFit Lab Execution Demo API",
        "mode": "demo",
        "notice": DEMO_NOTICE,
        "docs": "/docs",
    }


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "mode": "demo"}


@app.get("/api/demo/meta")
def demo_meta() -> dict[str, Any]:
    return {
        "name": "LocalFit Lab",
        "mode": "execution-demo",
        "synthetic_data": True,
        "notice": DEMO_NOTICE,
        "features": ["상권 탐색", "업종 분석", "입지봇", "결정론적 샘플 리포트"],
    }


@app.get("/api/areas/rankings")
@app.get("/api/rankings")
def rankings() -> list[dict[str, Any]]:
    return [_ranking(area, index + 1) for index, area in enumerate(AREAS)]


@app.get("/api/areas/stats")
def area_stats() -> dict[str, Any]:
    return {
        "latest_quarter": "20261",
        "area_count": len(AREAS),
        "store_point_count": sum(int(area["stores"]) for area in AREAS),
        "demo": True,
    }


@app.get("/api/search")
def search(keyword: str = Query(min_length=1, max_length=80)) -> list[dict[str, Any]]:
    normalized = keyword.replace(" ", "").lower()
    items = []
    for index, area in enumerate(AREAS):
        searchable = f"{area['area_name']}{area['district_name']}{area['area_code']}".replace(" ", "").lower()
        if normalized in searchable:
            items.append(
                {
                    **_ranking(area, index + 1),
                    "latitude": area["latitude"],
                    "longitude": area["longitude"],
                }
            )
    return items


@app.get("/api/chatbot/industry-options")
def industry_options(
    q: str = Query(default="", max_length=80),
    limit: int = Query(default=100, ge=1, le=200),
) -> list[dict[str, str]]:
    normalized = q.replace(" ", "").lower()
    if not normalized:
        return deepcopy(INDUSTRIES[:limit])
    return [
        deepcopy(industry)
        for industry in INDUSTRIES
        if normalized
        in (
            industry["industry_code"]
            + industry["industry_name"]
            + industry["display_label"]
        ).replace(" ", "").lower()
    ][:limit]


@app.get("/api/areas/{area_code}/comments")
def area_comments(
    area_code: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    _find_area(area_code)
    return {"items": [], "page": page, "page_size": page_size, "total": 0}


@app.get("/api/areas/{area_code}")
def area_detail(
    area_code: str,
    industry_code: str | None = Query(default=None),
) -> dict[str, Any]:
    return _area_detail(_find_area(area_code), industry_code)


@app.get("/api/favorites")
def favorites() -> list[Any]:
    return []


@app.post("/api/events/log", status_code=204)
def log_event() -> Response:
    return Response(status_code=204)


@app.post("/api/admin/external-api-log", status_code=204)
def log_external_api() -> Response:
    return Response(status_code=204)


@app.post("/api/chatbot/chat")
def chatbot_chat(payload: dict[str, Any]) -> dict[str, Any]:
    message = str(payload.get("message") or "").strip()
    selected = next(
        (area for area in AREAS if area["area_name"] in message or area["area_code"] in message),
        AREAS[0],
    )
    return {
        "type": "text",
        "text": (
            f"{selected['area_name']} 실행 데모 기준으로는 접근성과 유입이 강점입니다. "
            "상권 상세에서 업종을 선택한 뒤 AI 리포트 버튼을 눌러 전체 흐름을 체험해 보세요.\n\n"
            f"※ {DEMO_NOTICE}"
        ),
        "state": {
            "area_code": selected["area_code"],
            "area_name": selected["area_name"],
        },
        "is_guest": True,
        "message": "계정 없이 이용하는 공개 실행 데모입니다.",
        "option_payloads": [
            {
                "type": "area",
                "label": f"{selected['area_name']} 분석 보기",
                "value": selected["area_name"],
                "payload": {
                    "area_code": selected["area_code"],
                    "area_name": selected["area_name"],
                },
            }
        ],
    }


@app.post("/api/reports/jobs/single")
def create_single_report_job(
    payload: dict[str, Any],
    x_localfit_report_job: str | None = Header(default=None),
) -> dict[str, Any]:
    area = _find_area(str(payload.get("area_code") or ""))
    job_id = x_localfit_report_job or str(uuid4())
    now = datetime.now(UTC).isoformat()
    result = _single_report(
        area,
        str(payload.get("business_type") or "").strip() or None,
        int(payload["budget"]) if payload.get("budget") is not None else None,
    )
    job = {
        "job_id": job_id,
        "report_type": "single",
        "status": "completed",
        "progress_message": "실행 데모 리포트가 준비되었습니다.",
        "result": result,
        "error_message": None,
        "created_at": now,
        "started_at": now,
        "completed_at": now,
    }
    REPORT_JOBS[job_id] = job
    return deepcopy(job)


@app.post("/api/reports/jobs/comparison")
def create_comparison_report_job(
    payload: dict[str, Any],
    x_localfit_report_job: str | None = Header(default=None),
) -> dict[str, Any]:
    area_codes = [str(code) for code in payload.get("area_codes") or []]
    areas = [_find_area(code) for code in area_codes] if area_codes else AREAS[:3]
    top = max(areas, key=lambda item: item["score"])
    job_id = x_localfit_report_job or str(uuid4())
    now = datetime.now(UTC).isoformat()
    job = {
        "job_id": job_id,
        "report_type": "comparison",
        "status": "completed",
        "progress_message": "실행 데모 비교 리포트가 준비되었습니다.",
        "result": {
            "summary": "합성 샘플 기준의 후보 비교입니다.",
            "top_recommendation_name": top["area_name"],
            "top_recommendation_reason": "샘플 수요·접근성 맥락 등급이 가장 높습니다.",
            "comparison_matrix": [
                {
                    "area_name": area["area_name"],
                    "interpretation_level": area["display_grade"],
                    "strong_axis": "접근·유입",
                    "watch_axis": "경쟁·비용",
                    "interpretation": DEMO_NOTICE,
                }
                for area in areas
            ],
            "ai_generated": True,
            "generation_mode": "deterministic",
            "quality_status": "pass",
            "limitations": [DEMO_NOTICE],
            "source_citations": [],
            "demo": True,
        },
        "error_message": None,
        "created_at": now,
        "started_at": now,
        "completed_at": now,
    }
    REPORT_JOBS[job_id] = job
    return deepcopy(job)


@app.get("/api/reports/jobs/{job_id}")
def get_report_job(job_id: str) -> dict[str, Any]:
    job = REPORT_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="데모 리포트 작업을 찾지 못했습니다.")
    return deepcopy(job)


@app.post("/api/reports/export/pdf")
def demo_pdf_export() -> None:
    raise HTTPException(
        status_code=409,
        detail="실행 데모에서는 PDF 파일을 생성하지 않습니다. 화면 리포트를 확인해 주세요.",
    )
