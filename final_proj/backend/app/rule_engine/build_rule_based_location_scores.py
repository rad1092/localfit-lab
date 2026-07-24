# -*- coding: utf-8 -*-
"""
서울 상권 입지판단 점수 알고리즘 v2 (규칙 기반)

점수 버전: loc_score.v2.6-coverage-contract-rc1  (검증 전 release candidate)
명세 문서: research/알고리즘_명세_v2_20260704.md  (모든 산식의 근거·이유는 명세 §3~§6)
상위 계약: research/전처리_알고리즘_실행계획_20260703.md, research/알고리즘_스펙_v1_20260703.md

인용 표기 (주석에서 사용):
  [M##][K##][D##][Q##] = research/algorithm_evidence_sources/수집자료_카탈로그_20260630.md 자료 ID
  [MV-SA#][MV-CV#]     = research/methodology_validation_sources/ 방법론 검증자료
  [BT]                 = datacorpus/_score_backtest/ 백테스트 산출물
  [RV##]               = research/rule_validation/##_* silver 검증 기록

원칙 (실행계획 §0):
  1) 근거 없는 규칙은 만들지 않는다. 모든 산식·방향·가중치·게이트 주석에 근거 ID를 남긴다.
  2) 데이터를 한 파일로 합치지 않는다. 검증된 gold 테이블만 점수축별 로더로 읽는다.
  3) 결측은 임의 0점 처리하지 않는다. 축 제외 + 가중치 재정규화 + 신뢰도 감점으로 처리한다.
  4) 알고리즘 입력 키는 상권_코드 + 서비스_업종_코드 + 기준_년분기_코드 뿐이다. 이름 조인 금지 [RV08].

실행 예:
  python scripts/build_rule_based_location_scores.py --trade-area-code 3001491 --industry-code CS100001
  python scripts/build_rule_based_location_scores.py --quarter 20261 --batch
  python scripts/build_rule_based_location_scores.py --emit-direction-matrix
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from app.core.settings import DATA_ROOT

# ---------------------------------------------------------------------------
# 경로·버전 상수
# ---------------------------------------------------------------------------
LOCAL_DATACORPUS = DATA_ROOT
SILVER = LOCAL_DATACORPUS / "_silver"
GOLD = LOCAL_DATACORPUS / "_gold"
BACKTEST_DIR = LOCAL_DATACORPUS / "_score_backtest"
OUT_DIR = LOCAL_DATACORPUS / "_location_judgement_outputs"
RULE_VALIDATION_DIR = LOCAL_DATACORPUS / "_rule_validation"

SCORE_VERSION = "loc_score.v2.6-coverage-contract-rc1"
TRANSIT_CANDIDATE_SCORE_VERSION = "loc_score.v2.5-transit-accessibility-candidate-rc1"
SPEC_DOC = "research/알고리즘_명세_v2_20260704.md"
TRANSIT_CANDIDATE_FEATURES_CSV = LOCAL_DATACORPUS / "_rule_validation" / "59_transit_accessibility_candidate_quarter_features.csv"

# 가중치는 코드에 하드코딩하지 않고 백테스트 산출물 파일을 진실 원천으로 읽는다.
# 근거: [Q09] PROV-O 계보 원칙, 명세 §5.1
WEIGHTS_CSV = BACKTEST_DIR / "location_score_backtest_recommended_weights.csv"

# ---------------------------------------------------------------------------
# 임시값(민감도 검증 대상) — 전부 명세 §11 미해결 항목에 등록되어 있다.
# 확정 전이므로 상수로 모아 두고, 민감도 스크립트가 이 값을 섭동한다 [MV-SA1..3].
# ---------------------------------------------------------------------------
PROVISIONAL = {
    # 신뢰도 게이트 임계값. 미만이면 판단 보류. 근거: 스펙 v1 §2 제약/마스크, [Q13]
    "reliability_gate": 40.0,
    # 업종 비교군 최소 상권 수. 미만이면 전체 모집단으로 확대 + 신뢰도 감점.
    # 근거: 소표본 백분위 불안정 (명세 §4), 값 30은 임시
    "min_industry_sample": 30,
    # 공간해상도 배점 (grain 일치도). 근거: [Q13] ISO 19157, 배점 간격은 임시
    "grain_points": {"area_industry": 100.0, "area": 80.0, "district": 50.0},
    # 최신성 감점: 분기 차이당 -25점. 근거: [Q02] timeliness 차원, 감점 폭은 임시
    "timeliness_penalty_per_quarter": 25.0,
    # 품질 플래그 1건당 감점. 근거: [RV03] 이상치를 삭제하지 않고 감점 처리하는 판단
    "quality_flag_penalty": 20.0,
    # 비교군 확대 시 완전성 차원 감점. 근거: 명세 §4 표본 과소 규칙
    "expanded_group_penalty": 10.0,
    # 원천성 배점: 직접 관측=100, 추정/프록시=60. 근거: [Q08] DQV, 실행계획 §0 원칙 3
    "directness_points": {"direct": 100.0, "proxy": 60.0},
    # OWA 보수형 참고값의 순서가중치 (약한 축부터). 근거: [M10] OWA, 벡터 값은 임시.
    # 등급 산정에는 쓰지 않고 참고 출력 전용 (명세 §5.4)
    "owa_weights_asc": [0.4, 0.3, 0.2, 0.1],
    # 매출 추세에 필요한 최소 분기 수. 근거: 실행계획 §3.3 (최소 4분기 규칙)
    "trend_quarters": 4,
}

# ---------------------------------------------------------------------------
# 금지 표현 계약 — text model(LLM)과 UI에 그대로 전달된다.
# 근거: 진행기록 §5, 명세 §8. LLM에게 숫자 계산·점수 생성을 맡기지 않는다 (실행계획 §3.1).
# ---------------------------------------------------------------------------
FORBIDDEN_CLAIMS = [
    {
        "금지": "창업 성공확률",
        "대체": "상대 입지 적합도",
        "이유": "개별 사업체 365일 생존 holdout 검증에서 예측력이 지지되지 않아 성공확률로 해석할 수 없음",
    },
    {"금지": "개별 매장 매출 보장", "대체": "상권·업종 매출 체력", "이유": "[D02]는 상권×업종 추정 집계값"},
    {"금지": "성장 보장·성장률 예측", "대체": "성장잠재 후보 점수", "이유": "[BT] 성장 상관 음수, 성장 점수는 검증 전"},
    {"금지": "월세/권리금 반영 수익성", "대체": "비용 리스크 프록시", "이유": "[RV12] RTMS/R-ONE은 집계·프록시"},
    {"금지": "실제 방문확률", "대체": "상대 흡인력/접근성 지수", "이유": "Huff 계수 보정용 방문 로그 부재"},
    {"금지": "데이터 완전 확보", "대체": "현재 확보 원천 기준 판단", "이유": "명세 §2.3 보류 원천 존재"},
]

TEXT_MODEL_RULES = [
    "리포트에 등장하는 모든 숫자는 evidence_pack에 있는 값만 사용한다.",
    "점수·백분위를 새로 계산하거나 수정하지 않는다.",
    "FORBIDDEN_CLAIMS의 금지 표현을 사용하지 않고 대체 표현만 사용한다.",
    "growth_potential_score는 '검증 전 후보 점수'임을 반드시 명시한다.",
    "growth_rebound_candidate_score는 '초과성장/반등 후보 신호'이며 현재입지 점수나 매출 수준 점수처럼 해석하지 않는다.",
    "transit_accessibility_250m_candidate_score는 '검증된 접근성 후보 신호'이며 실제 방문자 수, 실제 구매자 수, 도보시간, 방문확률, 성공확률처럼 설명하지 않는다.",
    "data_reliability_score가 낮은 축의 해석에는 반드시 한계를 함께 서술한다.",
]

TRANSIT_CANDIDATE_FORMULA_KO = (
    "후보 접근성축 = 기존 접근성축 * 0.70 + transit_total_250m_score * 0.30. "
    "후보 현재입지 총점은 공식 4축 WLC에서 접근성축만 후보 접근성축으로 교체한 병렬 참고값이다."
)
TRANSIT_CANDIDATE_FORBIDDEN_KO = (
    "실제 상권 방문자, 실제 구매자, 실제 도보시간, 실제 방문확률, 창업 성공확률로 표현 금지"
)

# ---------------------------------------------------------------------------
# 지표 명세 — 명세서 §3의 표를 기계가독 형태로 옮긴 것.
# 각 지표의 direction / grain / group(비교군) / evidence / reason_ko가
# "한 줄 한 줄의 근거" 계약이다. 여기 없는 지표는 점수에 들어가지 않는다.
#   direction: benefit(높을수록 유리) / cost(높을수록 불리 → 100-백분위) [M14][M18]
#   grain: area_industry(상권×업종) / area(상권) / district(자치구)
#   group: industry(같은 분기×같은 업종) / all(같은 분기 전체) — 명세 §4
# ---------------------------------------------------------------------------
INDICATORS = {
    # ── 매출 축 [BT component Spearman 0.683 → 최강 축] ──
    "당월_매출_금액": dict(
        axis="sales", direction="benefit", grain="area_industry", group="industry",
        evidence="[D02][K01][M08]",
        reason_ko="상권×업종 매출 규모의 직접 관측치(추정 집계). 업종 내 백분위로 업종 간 규모 차이 통제.",
    ),
    "점포당_매출": dict(
        axis="sales", direction="benefit", grain="area_industry", group="industry",
        evidence="[D02][D03][K01]",
        reason_ko="총매출의 상권 크기 비례 왜곡을 점포 수로 보정. [K01] 점포 단위 매출 요인 실증.",
    ),
    # ── 경쟁 축: 과밀(비용)과 집적(편익)을 분리 [M03][M04] ──
    "동종_과밀도": dict(
        axis="competition", direction="cost", grain="area_industry", group="industry",
        evidence="[M11][M03][K02]",
        reason_ko="같은 수요를 나누는 동종 공급 밀도(점포수/유동인구). 2SFCA의 공급/수요 비 개념 단순화.",
    ),
    "상권_집적_규모": dict(
        axis="competition", direction="benefit", grain="area", group="all",
        evidence="[M04][M03][K01]",
        reason_ko="클러스터 규모가 클수록 목적지 흡인력 증가(집적 효과). 동종 과밀과 방향 분리로 이중계상 방지.",
    ),
    "SBDC_동종_점포수": dict(
        axis="competition", direction="cost", grain="area_industry", group="industry",
        evidence="[D14][RV16]",
        reason_ko="polygon 내부 실점포 기반 경쟁 보조 관측. 자동강매칭 업종만 사용, 202603 시점 고정은 최신성 감점.",
    ),
    # ── 수요 축 [K01][K03][K04] ──
    "총_유동인구_수": dict(
        axis="demand", direction="benefit", grain="area", group="all",
        evidence="[D04][K01]",
        reason_ko="방문 기반 수요 프록시. 실방문자 아님 — 원천 금지문구 유지 [RV04].",
    ),
    "총_직장인구_수": dict(
        axis="demand", direction="benefit", grain="area", group="all",
        evidence="[D06][K01]",
        reason_ko="평일 점심·퇴근 수요 프록시.",
    ),
    "총_상주인구_수": dict(
        axis="demand", direction="benefit", grain="area", group="all",
        evidence="[D05][K04]",
        reason_ko="생활권 상시 수요 프록시.",
    ),
    "지출_총금액": dict(
        axis="demand", direction="benefit", grain="area", group="all",
        evidence="[D09][K05][RV28]",
        reason_ko="상권 단위 소비잠재 공식 추정집계. 실제 구매자 수나 업종별 소비 보장은 아님.",
    ),
    "기초수요당_소비": dict(
        axis="demand", direction="benefit", grain="area", group="all",
        evidence="[D09][K05][RV28]",
        reason_ko="인구 총량 중복을 완화하기 위한 소비 강도 후보. 결측/0분모는 0점이 아니라 결측 처리.",
    ),
    # ── 접근성 축 [M16][M17][K01], 거리감쇠·2SFCA는 CRS 확정 후 후속 버전에서 반영 (명세 §2.3) ──
    "총_집객시설_수": dict(
        axis="accessibility", direction="benefit", grain="area", group="all",
        evidence="[D07][M17][RV06]",
        reason_ko="앵커시설의 고객 유인. 4분기 갱신 성격이라 분기 변화 지표로는 쓰지 않음 [RV06].",
    ),
    "교통결절_수": dict(
        axis="accessibility", direction="benefit", grain="area", group="all",
        evidence="[D07][D12][M16]",
        reason_ko="지하철역+철도역+버스터미널. 거리감쇠 도입 전의 1단계 접근성 (실행계획 §3.4 단계1).",
    ),
    "버스정류장_수": dict(
        axis="accessibility", direction="benefit", grain="area", group="all",
        evidence="[D07][D11]",
        reason_ko="미시 접근성 보조. 결절 지표와 분리해 규모 차이 왜곡 방지.",
    ),
    "생활이동_외부유입": dict(
        axis="accessibility", direction="benefit", grain="district", group="all",
        evidence="[D10][Q13][RV11]",
        reason_ko="생활이동 OD 외부유입 프록시. Huff 방문확률이 아니라 자치구 grain 접근성 보조값이며 공간해상도 차이는 [Q13] 기준으로 감점한다.",
    ),
    # ── 성장잠재 (별도 점수, 검증 전 후보) [BT 성장 상관 -0.114 → 분리] ──
    "매출_추세_기울기": dict(
        axis="growth", direction="benefit", grain="area_industry", group="industry",
        evidence="[K03][BT]",
        reason_ko="최근 4분기 log 매출 OLS 기울기. 수준이 아닌 변화율 신호, 로그로 저기저 폭주 완화.",
    ),
    "개폐업_순동태": dict(
        axis="growth", direction="benefit", grain="area_industry", group="industry",
        evidence="[D03][K02]",
        reason_ko="개업률-폐업률. [K02]가 창·폐업을 상권 동태의 핵심 신호로 실증.",
    ),
    "폐업_률": dict(
        axis="growth", direction="cost", grain="area_industry", group="industry",
        evidence="[D03][K06]",
        reason_ko="하방 위험의 직접 신호. 순동태와 분리해 개업 급증이 폐업 급증을 가리는 것 방지.",
    ),
    "영업개월_서울대비": dict(
        axis="growth", direction="benefit", grain="area", group="all",
        evidence="[D08][K03][RV05]",
        reason_ko="상권 지속성 신호. 변화지표 코드의 선형화가 금지되어[RV05] 연속형 개월 차이를 사용.",
    ),
    # ── 비용 리스크 (별도 점수, 자치구 프록시) ──
    "자치구_상업실거래_단가": dict(
        axis="cost_risk", direction="cost", grain="district", group="all",
        evidence="[D18][M18][RV12]",
        reason_ko="지역 비용 압력 프록시. 극단 거래 왜곡을 피해 중앙값 사용 [RV12].",
    ),
}

# 48~49번 검증으로 객단가는 sales 축 직접 가점에서 제거한다.
# 값은 서울 추정매출 원천 안에서 계산 가능한 소비 단가 수준 참고값이므로 evidence-only로 보존한다.
# 근거: [RV48] sales 축 제외안 0.918025 > 포함안 0.832157,
#       [RV49] 후보 current score 0.722154 > 기존 0.613471, 13개 검증 PASS.
TICKET_EVIDENCE_ONLY = {
    "metric": "객단가",
    "axis": "sales",
    "score_contribution_status": "excluded_from_sales_axis",
    "direction": "점수 방향 없음 — evidence-only",
    "grain": "area_industry",
    "comparison_group": "industry",
    "evidence_ids": "[D02][K05][RV48][RV49]",
    "reason_ko": (
        "매출금액/매출건수 기반 소비 단가 수준 참고값이다. "
        "48~49번 백테스트에서 sales 축 직접 편익 가점보다 제거안이 강해 점수 산식에서 제외한다."
    ),
    "forbidden_claim_ko": "고객 구매력 보장, 성장률 보장, 성공확률, 매출 상승 보장으로 표현 금지",
}

CURRENT_AXES = ["sales", "competition", "demand", "accessibility"]
GROWTH_AXIS = "growth"

# 명세에서 보조 관측/보조 프록시로 명시된 두 지표는 공식 축·신뢰도·총점에서
# 제외하고 context evidence로만 보존한다. 나머지 12개 핵심 지표는 전부
# 관측되어야 공식 점수·등급·순위를 제공한다.
OFFICIAL_OPTIONAL_INDICATORS = {"SBDC_동종_점포수", "생활이동_외부유입"}
OFFICIAL_REQUIRED_INDICATORS = tuple(
    name
    for name, spec in INDICATORS.items()
    if spec["axis"] in CURRENT_AXES and name not in OFFICIAL_OPTIONAL_INDICATORS
)
OFFICIAL_REQUIRED_BY_AXIS = {
    axis: tuple(name for name in OFFICIAL_REQUIRED_INDICATORS if INDICATORS[name]["axis"] == axis)
    for axis in CURRENT_AXES
}

# `build_indicator_frame`의 pandas merge suffix 계약을 공식 4축 provenance와
# 명시적으로 연결한다. 신뢰도 원천성은 매출의 unsuffixed 메타 하나를 대표값으로
# 쓰지 않고, 실제로 점수에 들어간 각 축의 source/directness를 따로 읽는다.
OFFICIAL_AXIS_PROVENANCE_COLUMNS = {
    "sales": ("source_id", "directness_level"),
    "competition": ("source_id_store", "directness_level_store"),
    "demand": ("source_id_demand", "directness_level_demand"),
    "accessibility": ("source_id_fac", "directness_level_fac"),
}

# 등급 문구 — 진행기록 §5 허용 표현만 사용. 성공/실패 단정 금지.
GRADE_LABELS = {
    "A": "상위 후보군, 현장 확인 필요",
    "B": "양호, 현장 확인 필요",
    "C": "보통, 조건 비교 필요",
    "D": "하위권, 신중 검토 필요",
    "E": "약세 신호, 대안 비교 필요",
}
GATE_LABEL = "데이터 부족, 판단 보류"


# ---------------------------------------------------------------------------
# 공통 유틸
# ---------------------------------------------------------------------------
def prev_quarter(q: int, n: int = 1) -> int:
    """분기 산술. 20261 → n=1이면 20254. 미래 분기 참조는 어디에도 없다 (누수 방지, 루프3)."""
    y, qq = divmod(q, 10)
    for _ in range(n):
        qq -= 1
        if qq == 0:
            y -= 1
            qq = 4
    return y * 10 + qq


def read_silver(filename: str, wanted: list[str], dtypes: dict | None = None) -> pd.DataFrame:
    """silver CSV를 필요한 컬럼만 읽는다.

    - 헤더를 먼저 읽어 실제 존재하는 컬럼과 교집합만 요청한다
      (silver 스키마가 갱신되어도 로더가 죽지 않도록 — gold 전환 시 이 함수만 교체, 명세 §2.2).
    - 인코딩 utf-8-sig: silver 파일은 BOM 포함 UTF-8로 생성되어 있다.
    - 코드 컬럼은 문자열로 읽는다 (선행 0 손실·float 오염 방지, 루프2 코드키 계약).
    """
    path = SILVER / filename
    header = pd.read_csv(path, nrows=0, encoding="utf-8-sig").columns.tolist()
    usecols = [c for c in wanted if c in header]
    df = pd.read_csv(path, usecols=usecols, encoding="utf-8-sig", dtype=dtypes or {}, low_memory=False)
    return df


def read_gold(filename: str, wanted: list[str], dtypes: dict | None = None) -> pd.DataFrame:
    """gold CSV를 필요한 컬럼만 읽는다.

    - gold는 `scripts/build_rule_engine_gold_tables.py`가 만든 점수축별 입력 계층이다.
    - silver 원천 의미와 grain은 gold 생성 검증(`23_gold_preprocessing_validation_20260704.md`)에서 확인한다.
    - 여기서는 계산식이 원천 CSV 구조 변화에 흔들리지 않도록 gold 계약만 소비한다.
    """
    path = GOLD / filename
    header = pd.read_csv(path, nrows=0, encoding="utf-8-sig").columns.tolist()
    usecols = [c for c in wanted if c in header]
    df = pd.read_csv(path, usecols=usecols, encoding="utf-8-sig", dtype=dtypes or {}, low_memory=False)
    return df


CODE_COLS = {"상권_코드": str, "서비스_업종_코드": str, "자치구_코드": str, "기준_년분기_코드": str}


def to_num(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """수치 변환. 실패값은 NaN으로 남긴다 — 임의 0 대체 금지 (실행계획 §3.2, [RV18] 판단)."""
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def to_bool(value) -> bool:
    """CSV bool/string을 안전하게 bool로 바꾼다. 결측은 활성 아님으로 본다."""
    if value is None or pd.isna(value):
        return False
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def optional_text(value) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


# ---------------------------------------------------------------------------
# gold 로더 — 테이블별 격리. silver 원천은 gold 생성 스크립트와 검증 문서가 관리한다.
# ---------------------------------------------------------------------------
def load_master() -> pd.DataFrame:
    # [RV23] PASS. 상권 1,650개 기준 테이블. 위치 입력 브리지와 자치구 조인 키 제공.
    df = read_gold(
        "gold_trade_area_profile.csv",
        ["상권_코드", "상권_코드_명", "상권_구분_코드_명", "자치구_코드", "자치구_코드_명", "행정동_코드_명"],
        CODE_COLS,
    )
    return df.drop_duplicates(subset=["상권_코드"])


def load_industry_taxonomy() -> pd.DataFrame:
    """업종별 공식 점수 사용 허용 계약을 읽는다."""
    df = read_gold(
        "gold_industry_taxonomy.csv",
        [
            "서비스_업종_코드",
            "direct_score_allowed",
            "direct_score_blocker_ko",
            "score_use_status",
        ],
        CODE_COLS,
    )
    if "direct_score_allowed" not in df.columns:
        df["direct_score_allowed"] = False
    if "direct_score_blocker_ko" not in df.columns:
        df["direct_score_blocker_ko"] = None
    if "score_use_status" not in df.columns:
        df["score_use_status"] = None
    return df.drop_duplicates(subset=["서비스_업종_코드"])


def load_sales(quarters: list[int]) -> pd.DataFrame:
    # [RV23] PASS, [D02]. gold_sales_strength는 매출 직접축과 점포수 결합상태를 보존한다.
    df = read_gold(
        "gold_sales_strength_q_industry.csv",
        ["기준_년분기_코드", "상권_코드", "상권_코드_명", "서비스_업종_코드", "서비스_업종_코드_명",
         "당월_매출_금액", "당월_매출_건수",
         "quality_negative_core_cell_count", "source_id", "directness_level",
         "forbidden_claim_ko", "snapshot_date"],
        CODE_COLS,
    )
    df = df[df["기준_년분기_코드"].isin([str(q) for q in quarters])]
    return to_num(df, ["당월_매출_금액", "당월_매출_건수", "quality_negative_core_cell_count"])


def load_store(quarter: int) -> pd.DataFrame:
    # [RV23] PASS, [D03]. gold_competition은 점포 직접축과 SBDC 보조 프록시 상태를 분리한다.
    df = read_gold(
        "gold_competition_q_industry.csv",
        ["기준_년분기_코드", "상권_코드", "서비스_업종_코드",
         "점포_수", "유사_업종_점포_수", "개업_율", "폐업_률",
         "동종_후보소분류_점포수", "mapping_review_required",
         "quality_rate_above_100_cell_count", "source_id", "directness_level",
         "forbidden_claim_ko", "snapshot_date"],
        CODE_COLS,
    )
    df = df[df["기준_년분기_코드"] == str(quarter)]
    return to_num(df, ["점포_수", "유사_업종_점포_수", "개업_율", "폐업_률",
                       "동종_후보소분류_점포수", "quality_rate_above_100_cell_count"])


def load_demand(quarter: int) -> pd.DataFrame:
    # [RV23] PASS, [D04][D05][D06]. 상권 grain — 업종 행에는 같은 분기·상권 값을 조인.
    df = read_gold(
        "gold_demand_q_area.csv",
        ["기준_년분기_코드", "상권_코드", "총_유동인구_수", "총_상주인구_수", "총_직장인구_수",
         "지출_총금액", "기초수요당_소비", "상주인구당_소비", "소비_관측여부",
         "소비_품질_지출결측셀수", "소비_품질_음수셀수", "소비_품질_세부합계불일치",
         "유동인구_품질_음수셀수", "수요원천_존재_개수", "source_id", "directness_level",
         "forbidden_claim_ko", "snapshot_date"],
        CODE_COLS,
    )
    df = df[df["기준_년분기_코드"] == str(quarter)]
    return to_num(df, ["총_유동인구_수", "총_상주인구_수", "총_직장인구_수",
                       "지출_총금액", "기초수요당_소비", "상주인구당_소비",
                       "소비_품질_지출결측셀수", "소비_품질_음수셀수",
                       "유동인구_품질_음수셀수", "수요원천_존재_개수"])


def load_change(quarter: int) -> pd.DataFrame:
    # [RV23] PASS, [D08]. 변화지표 코드는 gold_growth에서 area 단위로 중복 제거해 evidence 전용으로 나른다.
    df = read_gold(
        "gold_growth_stability_q_industry.csv",
        ["기준_년분기_코드", "상권_코드", "상권_변화_지표_코드", "상권_변화_지표_명",
         "운영_서울대비_개월_차이", "source_id", "directness_level", "forbidden_claim_ko", "snapshot_date"],
        CODE_COLS,
    )
    df = df[df["기준_년분기_코드"] == str(quarter)]
    df = df.drop_duplicates(subset=["기준_년분기_코드", "상권_코드"])
    return to_num(df, ["운영_서울대비_개월_차이"])


def load_growth_rebound_candidate(quarter: int) -> pd.DataFrame:
    """성장 반등 후보 gold를 읽는다.

    [RV36][RV37]에서 미래 라벨 없는 런타임 안전 후보로 검증된 값이다.
    단, 이 값은 현재입지 점수·등급·가중치에 섞지 않고 별도 후보 컬럼으로만 출력한다.
    """
    df = read_gold(
        "gold_growth_rebound_candidate_q_industry.csv",
        ["기준_년분기_코드", "상권_코드", "서비스_업종_코드",
         "growth_rebound_candidate_score", "growth_rebound_candidate_grade",
         "growth_rebound_gate_reason", "runtime_feature_safe", "score_engine_active",
         "engine_activation_required_ko", "forbidden_claim_ko",
         "algorithm_use_note_ko", "gold_version"],
        CODE_COLS,
    )
    df = df[df["기준_년분기_코드"] == str(quarter)]
    df = df.rename(
        columns={
            "runtime_feature_safe": "growth_rebound_runtime_feature_safe",
            "score_engine_active": "growth_rebound_score_engine_active",
            "engine_activation_required_ko": "growth_rebound_activation_required_ko",
            "forbidden_claim_ko": "growth_rebound_forbidden_claim_ko",
            "algorithm_use_note_ko": "growth_rebound_algorithm_use_note_ko",
            "gold_version": "growth_rebound_gold_version",
        }
    )
    return to_num(df, ["growth_rebound_candidate_score"])


def load_transit_accessibility_candidate(quarter: int) -> pd.DataFrame:
    """교통 접근성 250m 후보 피처를 읽는다.

    [RV59][RV60]에서 기존 접근성축 보강 후보로 검증됐지만, 공식 4축 점수를 덮지 않는다.
    상권 grain 후보이므로 상권×업종 행에는 `상권_코드` 기준 many-to-one으로 반복 부착한다.
    """
    cols = ["기준_년분기_코드", "상권_코드", "transit_month_count", "transit_total_250m_score"]
    if not TRANSIT_CANDIDATE_FEATURES_CSV.exists():
        return pd.DataFrame(columns=cols)
    header = pd.read_csv(TRANSIT_CANDIDATE_FEATURES_CSV, nrows=0, encoding="utf-8-sig").columns.tolist()
    usecols = [c for c in cols if c in header]
    df = pd.read_csv(
        TRANSIT_CANDIDATE_FEATURES_CSV,
        usecols=usecols,
        encoding="utf-8-sig",
        dtype={"기준_년분기_코드": str, "상권_코드": str},
        low_memory=False,
    )
    if "기준_년분기_코드" not in df.columns or "상권_코드" not in df.columns:
        return pd.DataFrame(columns=cols)
    df = df[df["기준_년분기_코드"] == str(quarter)].copy()
    return to_num(df, ["transit_month_count", "transit_total_250m_score"])


def load_facility(quarter: int) -> pd.DataFrame:
    # [RV23] PASS, [D07]. gold_accessibility는 시설 미관측을 0 대체하지 않고 플래그로 보존한다.
    df = read_gold(
        "gold_accessibility_q_area.csv",
        ["기준_년분기_코드", "상권_코드", "총_집객시설_수",
         "지하철역_수", "철도역_수", "버스터미널_수", "버스정류장_수",
         "quality_negative_facility_cell_count", "source_id", "directness_level",
         "forbidden_claim_ko", "snapshot_date"],
        CODE_COLS,
    )
    df = df[df["기준_년분기_코드"] == str(quarter)]
    return to_num(df, ["총_집객시설_수", "지하철역_수", "철도역_수", "버스터미널_수",
                       "버스정류장_수", "quality_negative_facility_cell_count"])


def load_migration(quarter: int) -> pd.DataFrame:
    # [RV23] PASS, [D10]. gold_accessibility에 결합된 자치구 생활이동 프록시를 자치구 단위로 중복 제거한다.
    df = read_gold(
        "gold_accessibility_q_area.csv",
        ["기준_년분기_코드", "자치구_코드", "생활이동_외부유입_이동인구_합계",
         "생활이동_분기_포함월수", "source_id", "directness_level", "forbidden_claim_ko", "snapshot_date"],
        CODE_COLS,
    )
    df = df[df["기준_년분기_코드"] == str(quarter)]
    df = df.drop_duplicates(subset=["기준_년분기_코드", "자치구_코드"])
    return to_num(df, ["생활이동_외부유입_이동인구_합계", "생활이동_분기_포함월수"])


def load_rtms(quarter: int) -> pd.DataFrame:
    # [RV23] PASS, [D18]. gold_cost_risk는 상권 fan-out이지만 비용 계산에는 자치구 프록시만 쓴다.
    df = read_gold(
        "gold_cost_risk_q_area.csv",
        ["기준_년분기_코드", "자치구_코드", "건물면적당_거래금액_중앙값_만원_per_m2",
         "거래건수", "source_id", "directness_level", "forbidden_claim_ko"],
        CODE_COLS,
    )
    df = df[df["기준_년분기_코드"] == str(quarter)]
    df = df.drop_duplicates(subset=["기준_년분기_코드", "자치구_코드"])
    return to_num(df, ["건물면적당_거래금액_중앙값_만원_per_m2", "거래건수"])


def load_sbdc(quarter: int) -> pd.DataFrame:
    # [RV23] PASS, [D14]. gold_competition의 SBDC 보조 프록시 중 자동강매칭만 사용한다.
    # SBDC snapshot은 202603(=2026Q1)이다. 과거 백테스트에 투입하면 미래 정보 누수가 되므로
    # 기준분기가 20261보다 과거이면 빈 프록시로 반환한다 [RV24 후퇴 검토].
    if int(quarter) < 20261:
        return pd.DataFrame(columns=["상권_코드", "서비스_업종_코드", "동종_후보소분류_점포수"])
    df = read_gold(
        "gold_competition_q_industry.csv",
        ["상권_코드", "서비스_업종_코드", "동종_후보소분류_점포수",
         "mapping_review_required", "source_id", "snapshot_date", "기준_년분기_코드"],
        CODE_COLS,
    )
    df = df[df["기준_년분기_코드"] == str(quarter)]
    df = df[df["mapping_review_required"].astype(str).str.lower() == "false"]
    return to_num(df, ["동종_후보소분류_점포수"])


def sbdc_from_store_frame(store: pd.DataFrame, quarter: int) -> pd.DataFrame:
    """이미 읽은 competition Gold에서 SBDC 보조 근거를 분리해 2회 대용량 scan을 막는다."""
    cols = ["상권_코드", "서비스_업종_코드", "동종_후보소분류_점포수"]
    if int(quarter) < 20261 or any(column not in store.columns for column in cols):
        return pd.DataFrame(columns=cols)
    eligible = store
    if "mapping_review_required" in eligible.columns:
        eligible = eligible[
            eligible["mapping_review_required"].astype(str).str.lower() == "false"
        ]
    return eligible[cols].copy()


def load_rone_reference(district_name: str) -> list[dict]:
    """R-ONE 임대 통계 참고선 — 점수 투입 금지, evidence 전용 (명세 §3.3, [RV12])."""
    try:
        df = read_silver(
            "silver_reb_rone_seoul_cost_proxy_latest.csv",
            ["STATBL_NM", "상가유형", "지역_전체명", "ITM_NM", "DTA_VAL", "기준_년분기_코드", "forbidden_claim_ko"],
        )
    except Exception:
        return []
    hit = df[df["지역_전체명"].astype(str).str.contains(district_name, na=False)]
    return hit.head(6).to_dict("records")


# ---------------------------------------------------------------------------
# 지표 조립
# ---------------------------------------------------------------------------
def build_indicator_frame(quarter: int) -> pd.DataFrame:
    """기준분기의 상권×업종 단위 지표 프레임을 만든다.

    조인은 전부 코드 키 (루프2). 시간 방향은 기준분기와 그 이전만 (루프3, 누수 방지).
    """
    n_trend = PROVISIONAL["trend_quarters"]
    trend_quarters = [prev_quarter(quarter, i) for i in range(n_trend - 1, 0, -1)] + [quarter]

    master = load_master()
    sales_all = load_sales(trend_quarters)
    sales = sales_all[sales_all["기준_년분기_코드"] == str(quarter)].copy()
    store = load_store(quarter)
    demand = load_demand(quarter)
    change = load_change(quarter)
    facility = load_facility(quarter)
    migration = load_migration(quarter)
    rtms = load_rtms(quarter)
    sbdc = sbdc_from_store_frame(store, quarter)
    store = store.drop(
        columns=["동종_후보소분류_점포수", "mapping_review_required"],
        errors="ignore",
    )
    growth_rebound = load_growth_rebound_candidate(quarter)
    transit_candidate = load_transit_accessibility_candidate(quarter)
    taxonomy = load_industry_taxonomy()

    # 기준 모집단 = 기준분기의 매출·점포 상권×업종 합집합 (둘 다 [RV03] 검증 grain)
    key = ["상권_코드", "서비스_업종_코드"]
    base = pd.merge(
        sales.drop(columns=["기준_년분기_코드"]),
        store.drop(columns=["기준_년분기_코드"]),
        on=key, how="outer", suffixes=("", "_store"),
    )
    base = base.merge(master, on="상권_코드", how="left", suffixes=("", "_m"))
    base = base.merge(taxonomy, on="서비스_업종_코드", how="left", validate="many_to_one")

    # ── 파생: 매출 축 (명세 §3.1) ──
    # 점포당_매출: 점포_수>0일 때만. 0 나눗셈 결과를 0점 처리하지 않고 결측으로 (실행계획 §3.2)
    base["점포당_매출"] = np.where(base["점포_수"] > 0, base["당월_매출_금액"] / base["점포_수"], np.nan)
    # 객단가: 건수>0일 때만 [D02]
    base["객단가"] = np.where(base["당월_매출_건수"] > 0, base["당월_매출_금액"] / base["당월_매출_건수"], np.nan)

    # ── 파생: 경쟁 축 ──
    # 상권_집적_규모 = 상권 내 전 업종 점포수 합 (편익, 집적 효과 [M04])
    agg_stores = store.groupby("상권_코드")["점포_수"].sum(min_count=1).rename("상권_집적_규모")
    base = base.merge(agg_stores, on="상권_코드", how="left")

    # ── 수요 조인 (상권 grain → 업종 행에 동일 값, 백분위는 상권 단위로 계산 — 명세 §4) ──
    base = base.merge(demand.drop(columns=["기준_년분기_코드"]), on="상권_코드", how="left",
                      suffixes=("", "_demand"))
    # 동종_과밀도 = 유사업종 점포수 / 유동인구 (비용, [M11] 공급/수요 비)
    base["동종_과밀도"] = np.where(
        base["총_유동인구_수"] > 0, base["유사_업종_점포_수"] / base["총_유동인구_수"], np.nan
    )

    # ── SBDC 동종 점포수 (자동강매칭 업종만 [RV16]) ──
    base = base.merge(
        sbdc.rename(columns={"동종_후보소분류_점포수": "SBDC_동종_점포수"})[key + ["SBDC_동종_점포수"]],
        on=key, how="left",
    )

    # ── 접근성 조인 ──
    base = base.merge(facility.drop(columns=["기준_년분기_코드"]), on="상권_코드", how="left",
                      suffixes=("", "_fac"))
    # 교통결절_수 = 지하철역+철도역+버스터미널. 전부 결측이면 결측 유지 (min_count=1)
    base["교통결절_수"] = base[["지하철역_수", "철도역_수", "버스터미널_수"]].sum(axis=1, min_count=1)
    base = base.merge(
        migration.rename(columns={"생활이동_외부유입_이동인구_합계": "생활이동_외부유입"})[
            ["자치구_코드", "생활이동_외부유입"]],
        on="자치구_코드", how="left",
    )

    # ── 성장 축 ──
    base = base.merge(
        change.rename(columns={"운영_서울대비_개월_차이": "영업개월_서울대비"})[
            ["상권_코드", "영업개월_서울대비", "상권_변화_지표_코드", "상권_변화_지표_명"]],
        on="상권_코드", how="left",
    )
    base["개폐업_순동태"] = base["개업_율"] - base["폐업_률"]

    # 매출_추세_기울기: 최근 4분기 log(매출+1)의 OLS 기울기. 4분기 전부 있어야 산출 (실행계획 §3.3).
    # 폐쇄형 기울기 공식 slope = cov(x,y)/var(x), x=[0..3] — 별도 회귀 라이브러리 불필요.
    piv = sales_all.pivot_table(index=key, columns="기준_년분기_코드",
                                values="당월_매출_금액", aggfunc="first")
    qcols = [str(q) for q in trend_quarters if str(q) in piv.columns]
    if len(qcols) == n_trend:
        y = np.log1p(piv[qcols].to_numpy(dtype=float))
        x = np.arange(n_trend, dtype=float)
        xc = x - x.mean()
        complete = ~np.isnan(y).any(axis=1)  # 4분기 전부 존재하는 행만 (최소 이력 게이트)
        slope = np.full(len(piv), np.nan)
        yc = y - np.nanmean(y, axis=1, keepdims=True)
        slope_all = np.nansum(yc * xc, axis=1) / (xc ** 2).sum()
        slope[complete] = slope_all[complete]
        trend = pd.Series(slope, index=piv.index, name="매출_추세_기울기").reset_index()
        base = base.merge(trend, on=key, how="left")
    else:
        # 과거 분기 원천이 부족하면 추세를 만들지 않는다 — 임의 대체 금지 (실행계획 §3.2)
        base["매출_추세_기울기"] = np.nan

    # ── 비용 축 (자치구 grain) ──
    base = base.merge(
        rtms.rename(columns={"건물면적당_거래금액_중앙값_만원_per_m2": "자치구_상업실거래_단가"})[
            ["자치구_코드", "자치구_상업실거래_단가"]],
        on="자치구_코드", how="left",
    )

    # ── 성장 반등 후보 (별도 출력 전용) ──
    # [RV37] 기존 성장잠재보다 초과성장 라벨과 더 잘 맞는 후보로 확인됐지만,
    # 다음분기 매출 수준 점수가 아니므로 현재입지 점수·등급·가중치에 절대 섞지 않는다.
    rebound_cols = [c for c in growth_rebound.columns if c != "기준_년분기_코드"]
    base = base.merge(growth_rebound[rebound_cols], on=key, how="left", validate="many_to_one")

    # ── 교통 접근성 250m 후보 (별도 출력 전용) ──
    # [RV59][RV60] 기존 접근성축을 보강하는 후보로 검증됐지만,
    # 실제 방문자·구매자·도보시간이 아니므로 공식 4축 현재입지 점수에는 섞지 않는다.
    transit_cols = [c for c in transit_candidate.columns if c != "기준_년분기_코드"]
    if transit_cols:
        base = base.merge(transit_candidate[transit_cols], on="상권_코드", how="left", validate="many_to_one")
    else:
        base["transit_month_count"] = np.nan
        base["transit_total_250m_score"] = np.nan

    base["기준_년분기_코드"] = str(quarter)
    return base


# ---------------------------------------------------------------------------
# 정규화 (명세 §4)
# ---------------------------------------------------------------------------
def percentile_scores(base: pd.DataFrame) -> pd.DataFrame:
    """지표별 백분위 점수(0~100)를 계산해 `pct__지표명` 컬럼으로 붙인다.

    - 백분위 채택 사유: [M08] WLC의 척도 통약성 경고 회피 (명세 §4).
    - 비용형은 100-백분위 [M14][M18].
    - 상권 grain 지표는 상권 중복 제거 후 백분위 → 재조인
      (업종 수만큼 복제된 값이 백분위를 왜곡하는 것을 차단 — 명세 §4).
    - 업종 비교군이 최소 표본 미만이면 전체 모집단으로 확대하고 플래그를 남긴다 (신뢰도 감점).
    """
    df = base.copy()
    min_n = PROVISIONAL["min_industry_sample"]

    # 업종별 상권 표본 수 → 확대 여부 플래그
    ind_sizes = df.groupby("서비스_업종_코드")["상권_코드"].nunique()
    small_industries = set(ind_sizes[ind_sizes < min_n].index)
    df["비교군_확대"] = df["서비스_업종_코드"].isin(small_industries)

    area_level = df.drop_duplicates(subset=["상권_코드"]).set_index("상권_코드")
    district_level = df.drop_duplicates(subset=["자치구_코드"]).set_index("자치구_코드")

    for name, spec in INDICATORS.items():
        if name not in df.columns:
            df[f"pct__{name}"] = np.nan
            continue
        if spec["grain"] == "area":
            # 상권 단위 백분위 — 업종 fan-out 제거 후 상권 코드로 재조인
            s = area_level[name]
            pct = s.rank(pct=True) * 100.0
            mapped = df["상권_코드"].map(pct)
        elif spec["grain"] == "district":
            # 자치구 단위 백분위 — 25개 자치구를 한 번씩만 rank한다. 상권 수가
            # 많은 자치구에 가중치가 생기지 않도록 자치구 코드로 재조인한다.
            s = district_level[name]
            pct = s.rank(pct=True) * 100.0
            mapped = df["자치구_코드"].map(pct)
        else:
            # 상권×업종 단위: 같은 업종 내 백분위 [BT 기준선 유지].
            # 소표본 업종은 전체 모집단 백분위로 대체 (명세 §4)
            by_ind = df.groupby("서비스_업종_코드")[name].rank(pct=True) * 100.0
            overall = df[name].rank(pct=True) * 100.0
            mapped = by_ind.where(~df["비교군_확대"], overall)
        if spec["direction"] == "cost":
            mapped = 100.0 - mapped  # 비용형 방향 반전 [M14][M18]
        df[f"pct__{name}"] = mapped
    return df


# ---------------------------------------------------------------------------
# 가중치 (명세 §5)
# ---------------------------------------------------------------------------
def load_axis_weights() -> dict[str, dict[str, float]]:
    """백테스트 권장 가중치 CSV를 읽어 현재입지 4축으로 재정규화한다.

    - w = λ·w_AHP + (1-λ)·w_BT 구조에서 AHP 미실시 → λ=0, w_BT만 사용.
      "AHP 완료"라고 표기하지 않는다 (실행계획 §3.6 명문 규칙, [M06][M07]).
    - growth_stability(성장 점수로 분리), budget_risk·data_reliability(별도 점수)를
      제외한 4축 부분합을 1로 재정규화 (명세 §5.1).
    """
    w = pd.read_csv(WEIGHTS_CSV, encoding="utf-8-sig")
    out: dict[str, dict[str, float]] = {}
    for ws, grp in w.groupby("weight_set"):
        sub = grp[grp["component"].isin(CURRENT_AXES)]
        total = sub["recommended_weight"].sum()
        out[str(ws)] = {r["component"]: float(r["recommended_weight"]) / total for _, r in sub.iterrows()}
    return out


def weight_set_for_industry(industry_code: str, weight_sets: dict) -> tuple[str, dict[str, float]]:
    """업종 대분류 프리픽스(CS1 음식/CS2 서비스/CS3 소매)로 가중치 세트 선택.

    업종별 차등의 방법론 근거: [M09] Local WLC의 맥락 이질성 논리 (명세 §5.3).
    차등 값 자체의 근거: [BT] 권장 가중치 CSV + 가중치_10회재귀검토 문서.
    """
    prefix = str(industry_code)[:3] if industry_code else ""
    if prefix in weight_sets:
        return prefix, weight_sets[prefix]
    return "BASE", weight_sets["BASE"]


def perturb_weights(weights: dict[str, float], pct: float, direction: int, target: str) -> dict[str, float]:
    """가중치 민감도 검증용 섭동 유틸리티 [MV-SA1][MV-SA2][MV-SA3].

    target 축 가중치를 ±pct% 조정 후 합=1 재정규화 (OAT 전략 [MV-SA1]).
    실측 검증은 백테스트 스크립트에서 수행한다 (명세 §5.5, rc 해제 조건).
    """
    w = dict(weights)
    w[target] = w[target] * (1.0 + direction * pct)
    total = sum(w.values())
    return {k: v / total for k, v in w.items()}


# ---------------------------------------------------------------------------
# 점수 계산 (명세 §1, §4~§6)
# ---------------------------------------------------------------------------
def _axis_mean(
    row: Mapping[str, Any],
    axis: str,
    indicator_names: tuple[str, ...] | None = None,
) -> tuple[float | None, int, int]:
    """축 점수 = 사용 가능 지표 백분위의 단순 평균.

    단순 평균 사유: 지표 수준 상대 중요도의 실증 부재 — 근거 없는 차등 금지
    ([M15] 투명성 우선, 실행계획 §0 원칙 1). 백테스트(루프6) 후 차등화 검토.
    반환: (점수 또는 None, 사용 지표 수, 정의 지표 수)
    """
    cols = list(indicator_names) if indicator_names is not None else [
        name for name, spec in INDICATORS.items() if spec["axis"] == axis
    ]
    vals = [row.get(f"pct__{n}") for n in cols]
    vals = [v for v in vals if v is not None and not pd.isna(v)]
    if not vals:
        return None, 0, len(cols)
    return float(np.mean(vals)), len(vals), len(cols)


def _owa_conservative(axis_scores: dict[str, float]) -> float | None:
    """보수형 참고값: 약한 축에 무게를 두는 순서가중평균 [M10] (명세 §5.4).

    등급 산정에는 쓰지 않는다 — 참고 출력 전용.
    """
    vals = sorted(v for v in axis_scores.values() if v is not None)
    if not vals:
        return None
    w = PROVISIONAL["owa_weights_asc"][: len(vals)]
    w = [x / sum(w) for x in w]
    return float(sum(v * wi for v, wi in zip(vals, w)))


def _weighted_current_score(axis_scores: dict[str, float | None], weights: dict[str, float]) -> float | None:
    """가용 축 WLC 공식을 한 곳에서 계산한다.

    결측 축 재정규화 값은 `context_location_score`에서만 사용한다. 공식
    `current_location_score`는 네 축이 모두 있고 taxonomy가 허용할 때만 채운다.
    """
    avail = {ax: axis_scores[ax] for ax in CURRENT_AXES if axis_scores.get(ax) is not None}
    if not avail:
        return None
    wsum = sum(weights[ax] for ax in avail)
    return float(sum(axis_scores[ax] * weights[ax] / wsum for ax in avail))


def _reliability(row: Mapping[str, Any], axis_avail: dict[str, tuple[int, int]]) -> tuple[float, dict]:
    """데이터 신뢰도 5차원 점수표 (명세 §3.4).

    점수표 구조 [M18] LESA / 차원 정의 [Q01][Q02][Q06][Q13] / 표준 대조 [Q12][Q14].
    5차원 단순 평균 — 차원 가중의 실증 부재 ([M15], 실행계획 §0 원칙 1).
    """
    # 1) 완전성 [Q01][Q06]: 사용 지표 / 정의 지표 (+ 비교군 확대 감점, 명세 §4)
    official_axis_avail = {ax: axis_avail.get(ax, (0, 0)) for ax in CURRENT_AXES}
    used = sum(u for u, _ in official_axis_avail.values())
    defined = sum(d for _, d in official_axis_avail.values())
    completeness = 100.0 * used / defined if defined else 0.0
    if bool(row.get("비교군_확대")):
        completeness = max(0.0, completeness - PROVISIONAL["expanded_group_penalty"])

    # 2) 최신성 [Q02]: 공식 core 12개 원천은 기준분기 필터로 적재된다.
    # SBDC 고정 스냅샷은 공식 산식 밖의 보조 evidence이므로 이 차원에 섞지 않는다.
    gaps = [0]
    timeliness = float(np.mean([max(0.0, 100.0 - PROVISIONAL["timeliness_penalty_per_quarter"] * g)
                                for g in gaps]))

    # 3) 공간해상도 [Q13]: 사용 지표 grain 배점 평균 (상권×업종 100 / 상권 80 / 자치구 50)
    gp = PROVISIONAL["grain_points"]
    grain_vals = [
        gp[spec["grain"]]
        for name, spec in INDICATORS.items()
        if name in OFFICIAL_REQUIRED_INDICATORS
        and not pd.isna(row.get(f"pct__{name}", np.nan))
    ]
    grain_score = float(np.mean(grain_vals)) if grain_vals else 0.0

    # 4) 원천성 [Q08]: silver 메타 directness_level에 '직접' 명시가 없으면 프록시 배점.
    #    서울 상권분석서비스 원천은 공식 문서상 추정/집계값이므로 프록시로 처리 (실행계획 §0 원칙 3).
    dp = PROVISIONAL["directness_points"]
    directness_values: list[float] = []
    for axis in CURRENT_AXES:
        axis_used, _ = official_axis_avail[axis]
        if axis_used <= 0:
            continue
        source_col, directness_col = OFFICIAL_AXIS_PROVENANCE_COLUMNS[axis]
        source_id = optional_text(row.get(source_col))
        level = optional_text(row.get(directness_col))
        if source_id is None or level is None:
            # provenance 자체가 없으면 프록시라고 추정하지 않고 unknown=0으로 닫는다.
            directness_values.append(0.0)
        else:
            directness_values.append(dp["direct"] if "직접" in level else dp["proxy"])
    directness = float(np.mean(directness_values)) if directness_values else 0.0

    # 5) 품질 플래그 [RV03]: 이상치를 삭제하지 않고 신뢰도 감점으로 반영한 판단의 구현
    flags = 0
    for c in ("quality_negative_core_cell_count", "quality_rate_above_100_cell_count",
              "유동인구_품질_음수셀수", "소비_품질_지출결측셀수", "소비_품질_음수셀수",
              "quality_negative_facility_cell_count"):
        v = row.get(c)
        if v is not None and not pd.isna(v) and float(v) > 0:
            flags += 1
    quality = max(0.0, 100.0 - PROVISIONAL["quality_flag_penalty"] * flags)

    dims = {"완전성": round(completeness, 2), "최신성": round(timeliness, 2),
            "공간해상도": round(grain_score, 2), "원천성": round(directness, 2),
            "품질플래그": round(quality, 2)}
    return float(np.mean(list(dims.values()))), dims


def score_frame(base: pd.DataFrame) -> pd.DataFrame:
    """상권×업종 전 행에 4개 점수·등급·게이트를 계산한다 (배치 = 실험·백테스트 겸용)."""
    pct_cols = {f"pct__{name}" for name in INDICATORS}
    # CLI/build_output은 evidence용 percentile frame을 이미 만든다. 그 frame을
    # 다시 rank하면 전체 배치가 두 번 정규화되므로, 완성된 frame은 그대로 쓴다.
    df = base.copy() if pct_cols.issubset(base.columns) else percentile_scores(base)
    weight_sets = load_axis_weights()

    records = []
    # iterrows는 행마다 Series를 만들며 7만+ 행에서 병목이 컸다. 아래 계산은
    # Mapping.get만 사용하므로 records dict 순회로 의미를 유지하면서 비용을 줄인다.
    for row in df.to_dict(orient="records"):
        axis_scores: dict[str, float | None] = {}
        axis_avail: dict[str, tuple[int, int]] = {}
        for ax in CURRENT_AXES:
            # 공식 축은 모든 행에서 동일한 core indicator 분모를 사용한다.
            s, used, defined = _axis_mean(row, ax, OFFICIAL_REQUIRED_BY_AXIS[ax])
            axis_scores[ax] = s
            axis_avail[ax] = (used, defined)
        for ax in [GROWTH_AXIS, "cost_risk"]:
            s, used, defined = _axis_mean(row, ax)
            axis_scores[ax] = s
            axis_avail[ax] = (used, defined)

        required_used_by_axis = {
            ax: sum(not pd.isna(row.get(f"pct__{name}", np.nan)) for name in names)
            for ax, names in OFFICIAL_REQUIRED_BY_AXIS.items()
        }
        official_indicator_count = sum(required_used_by_axis.values())
        official_indicator_defined_count = len(OFFICIAL_REQUIRED_INDICATORS)
        official_indicator_complete = official_indicator_count == official_indicator_defined_count
        partial_axis_details = [
            f"{ax}:{required_used_by_axis[ax]}/{len(OFFICIAL_REQUIRED_BY_AXIS[ax])}"
            for ax in CURRENT_AXES
            if required_used_by_axis[ax] != len(OFFICIAL_REQUIRED_BY_AXIS[ax])
        ]

        ws_name, w = weight_set_for_industry(row.get("서비스_업종_코드", ""), weight_sets)

        available_axes = [ax for ax in CURRENT_AXES if axis_scores.get(ax) is not None]
        missing_axes = [ax for ax in CURRENT_AXES if axis_scores.get(ax) is None]
        taxonomy_direct_allowed = to_bool(row.get("direct_score_allowed"))
        coverage_eligible = (
            len(available_axes) == len(CURRENT_AXES)
            and official_indicator_complete
            and taxonomy_direct_allowed
        )
        # Industry-level context is intentionally limited to the explicit
        # 3-axis fallback contract.  Two observed axes remain available as
        # evidence (and feed the separate area demand/access summary), but
        # must not be promoted into an industry context score.
        context = _weighted_current_score(axis_scores, w) if len(available_axes) >= 3 else None
        if len(available_axes) == len(CURRENT_AXES):
            if official_indicator_complete:
                coverage_tier = "full_4axis"
            else:
                coverage_tier = "context_only_partial_4axis"
            if not taxonomy_direct_allowed:
                coverage_reason = optional_text(row.get("direct_score_blocker_ko")) or "taxonomy 직접 점수 사용 금지"
            elif official_indicator_complete:
                coverage_reason = "4개 공식 축의 필수 지표 12개 전부 관측 및 taxonomy 직접 점수 허용"
            else:
                coverage_reason = (
                    "4개 공식 축은 산출 가능하지만 축내 필수 지표가 부분 관측"
                    f"({','.join(partial_axis_details)}); 참고점수만 제공"
                )
        elif len(available_axes) == 3:
            coverage_tier = "context_only_3axis"
            if not taxonomy_direct_allowed:
                coverage_reason = optional_text(row.get("direct_score_blocker_ko")) or "taxonomy 직접 점수 사용 금지"
            else:
                coverage_reason = f"공식 축 결측({','.join(missing_axes)}); 3축 참고점수만 제공"
        else:
            coverage_tier = "insufficient_context"
            coverage_reason = f"공식 축 {len(available_axes)}/4개만 관측({','.join(missing_axes)})"

        # ── 성장잠재 점수 (후보): 4개 지표 동일가중 평균 (명세 §5.2) ──
        # 게이트: 매출 추세(4분기 이력)가 없으면 null (실행계획 §3.3)
        if pd.isna(row.get("pct__매출_추세_기울기")):
            growth = None
            growth_gate = "매출 이력 4분기 미만 — 성장잠재 산출 보류 (실행계획 §3.3)"
        else:
            growth = axis_scores[GROWTH_AXIS]
            growth_gate = None

        # ── 비용 리스크 점수 (자치구 프록시, 단일 지표) ──
        cost_risk = axis_scores["cost_risk"]

        # ── 데이터 신뢰도 ──
        reliability, rel_dims = _reliability(row, axis_avail)
        reliability_pass = reliability >= PROVISIONAL["reliability_gate"]
        official_rank_eligible = coverage_eligible and reliability_pass
        current = context if official_rank_eligible else None
        if coverage_eligible and not reliability_pass:
            coverage_reason = (
                f"{GATE_LABEL} — 데이터 신뢰도 {reliability:.2f} < "
                f"{PROVISIONAL['reliability_gate']:.2f}"
            )

        # ── 보수형 참고값 [M10] ──
        conservative = _owa_conservative({ax: axis_scores[ax] for ax in CURRENT_AXES})

        rebound_score = row.get("growth_rebound_candidate_score")
        rebound_score = None if rebound_score is None or pd.isna(rebound_score) else round(float(rebound_score), 2)
        rebound_gate = row.get("growth_rebound_gate_reason")
        rebound_status = "candidate_attached_not_in_current_score" if rebound_score is not None else "not_available_or_history_gate"

        transit_score = row.get("transit_total_250m_score")
        transit_score = None if transit_score is None or pd.isna(transit_score) else float(transit_score)
        transit_month_count = row.get("transit_month_count")
        transit_month_count = None if transit_month_count is None or pd.isna(transit_month_count) else int(transit_month_count)
        transit_axis_candidate = None
        transit_current_candidate = None
        transit_status = "not_available_or_history_gate"
        if axis_scores.get("accessibility") is not None and transit_score is not None:
            # [RV60] 고정 후보 산식. 공식 4축 접근성축을 교체하지 않고 병렬 후보만 산출한다.
            transit_axis_candidate = axis_scores["accessibility"] * 0.70 + transit_score * 0.30
            candidate_axis_scores = dict(axis_scores)
            candidate_axis_scores["accessibility"] = transit_axis_candidate
            transit_context_candidate = _weighted_current_score(candidate_axis_scores, w)
            transit_current_candidate = transit_context_candidate if official_rank_eligible else None
            transit_status = "candidate_attached_not_in_current_score"

        records.append({
            "기준_년분기_코드": row["기준_년분기_코드"],
            "상권_코드": row["상권_코드"],
            "상권_코드_명": row.get("상권_코드_명"),
            "자치구_코드": row.get("자치구_코드"),          # 루프7 공간 블록 CV 키 [MV-CV1..4]
            "자치구_코드_명": row.get("자치구_코드_명"),
            "서비스_업종_코드": row.get("서비스_업종_코드"),
            "서비스_업종_코드_명": row.get("서비스_업종_코드_명"),
            "weight_set": ws_name,
            "비교군_확대": bool(row.get("비교군_확대")),
            "current_location_score": None if current is None else round(current, 2),
            "context_location_score": None if context is None else round(context, 2),
            "score_coverage_tier": coverage_tier,
            "available_axis_count": len(available_axes),
            "official_indicator_count": official_indicator_count,
            "official_indicator_defined_count": official_indicator_defined_count,
            "official_indicator_complete": official_indicator_complete,
            "missing_axes": ",".join(missing_axes),
            "coverage_reason": coverage_reason,
            "taxonomy_direct_score_allowed": taxonomy_direct_allowed,
            "official_rank_eligible": official_rank_eligible,
            "context_evidence__sbdc_competition_percentile": (
                None
                if pd.isna(row.get("pct__SBDC_동종_점포수", np.nan))
                else round(float(row["pct__SBDC_동종_점포수"]), 2)
            ),
            "context_evidence__living_mobility_accessibility_percentile": (
                None
                if pd.isna(row.get("pct__생활이동_외부유입", np.nan))
                else round(float(row["pct__생활이동_외부유입"]), 2)
            ),
            "growth_potential_score": None if growth is None else round(growth, 2),
            "growth_gate_reason": growth_gate,
            "growth_rebound_candidate_score": rebound_score,
            "growth_rebound_candidate_grade": None if pd.isna(row.get("growth_rebound_candidate_grade")) else row.get("growth_rebound_candidate_grade"),
            "growth_rebound_gate_reason": None if pd.isna(rebound_gate) else rebound_gate,
            "growth_rebound_candidate_status": rebound_status,
            "growth_rebound_runtime_feature_safe": to_bool(row.get("growth_rebound_runtime_feature_safe")),
            "growth_rebound_score_engine_active": to_bool(row.get("growth_rebound_score_engine_active")),
            "growth_rebound_activation_required_ko": None if pd.isna(row.get("growth_rebound_activation_required_ko")) else row.get("growth_rebound_activation_required_ko"),
            "growth_rebound_forbidden_claim_ko": None if pd.isna(row.get("growth_rebound_forbidden_claim_ko")) else row.get("growth_rebound_forbidden_claim_ko"),
            "growth_rebound_algorithm_use_note_ko": None if pd.isna(row.get("growth_rebound_algorithm_use_note_ko")) else row.get("growth_rebound_algorithm_use_note_ko"),
            "transit_accessibility_candidate_score_version": TRANSIT_CANDIDATE_SCORE_VERSION,
            "transit_accessibility_candidate_status": transit_status,
            "transit_accessibility_candidate_engine_active": False,
            "transit_accessibility_candidate_engine_promotion_ready": False,
            "transit_accessibility_candidate_formula_ko": TRANSIT_CANDIDATE_FORMULA_KO,
            "transit_accessibility_candidate_forbidden_claim_ko": TRANSIT_CANDIDATE_FORBIDDEN_KO,
            "transit_month_count": transit_month_count,
            "transit_total_250m_score": None if transit_score is None else round(transit_score, 2),
            "transit_accessibility_250m_candidate_axis": None if transit_axis_candidate is None else round(transit_axis_candidate, 2),
            "current_location_score_transit_250m_candidate": None if transit_current_candidate is None else round(transit_current_candidate, 2),
            "cost_risk_score": None if cost_risk is None else round(cost_risk, 2),
            "data_reliability_score": round(reliability, 2),
            "conservative_score_owa": None if conservative is None else round(conservative, 2),
            **{f"axis__{ax}": (None if axis_scores[ax] is None else round(axis_scores[ax], 2))
               for ax in CURRENT_AXES},
            **{f"rel__{k}": v for k, v in rel_dims.items()},
            "score_version": SCORE_VERSION,
        })

    out = pd.DataFrame(records)

    # ── 등급: 비교군 내 5분위 (임시 컷 — 루프6 후 확정, 명세 §6, 이산 등급 선례 [M14]) ──
    def grade_group(g: pd.Series) -> pd.Series:
        valid = g.dropna()
        if len(valid) < 5:
            return pd.Series("C", index=g.index).where(g.notna(), None)
        q = valid.rank(pct=True)
        bins = pd.cut(q, [0, 0.2, 0.4, 0.6, 0.8, 1.0], labels=["E", "D", "C", "B", "A"])
        return bins.reindex(g.index)

    out["grade"] = (out.groupby("서비스_업종_코드")["current_location_score"]
                    .transform(grade_group).astype(object))

    # ── 신뢰도 게이트 (명세 §6): 임계 미만이면 판단 보류로 강등 ──
    gated = out["data_reliability_score"] < PROVISIONAL["reliability_gate"]
    out["decision_label"] = np.where(
        ~out["official_rank_eligible"],
        out["coverage_reason"],
        np.where(
            gated,
            GATE_LABEL,
            out["grade"].map(GRADE_LABELS).fillna("등급 산출 불가 — 점수 결측"),
        ),
    )
    return out


# ---------------------------------------------------------------------------
# TOPSIS 복수 후보 비교 (보조 기능, 명세 §7) [M05]
# ---------------------------------------------------------------------------
def topsis_compare(scored: pd.DataFrame) -> pd.DataFrame:
    """복수 후보의 축 점수 행렬로 상대 근접도를 계산한다 [M05].

    단일 리포트 절대점수는 WLC가 주 엔진이며, TOPSIS는 비교 화면 전용 (실행계획 §3.5).
    입력 축 점수는 이미 편익 방향으로 정렬된 백분위이므로 전부 편익형으로 처리한다.
    """
    cols = [f"axis__{ax}" for ax in CURRENT_AXES]
    m = scored[cols].to_numpy(dtype=float)
    norm = np.sqrt(np.nansum(m ** 2, axis=0))
    norm[norm == 0] = 1.0
    v = m / norm
    ideal, anti = np.nanmax(v, axis=0), np.nanmin(v, axis=0)
    d_pos = np.sqrt(np.nansum((v - ideal) ** 2, axis=1))
    d_neg = np.sqrt(np.nansum((v - anti) ** 2, axis=1))
    denom = d_pos + d_neg
    denom[denom == 0] = 1.0
    out = scored.copy()
    out["topsis_closeness"] = np.round(d_neg / denom, 4)
    return out.sort_values("topsis_closeness", ascending=False)


# ---------------------------------------------------------------------------
# evidence pack (명세 §1, §3.4 메타데이터 계약)
# ---------------------------------------------------------------------------
def build_evidence_pack(raw_row: pd.Series, scored_row: pd.Series) -> dict:
    """지표별 근거 묶음. 필드 계약 근거: [Q05][Q07][Q08][Q09][Q11] (명세 §3.4)."""
    items = []
    for name, spec in INDICATORS.items():
        val = raw_row.get(name)
        pct = raw_row.get(f"pct__{name}")
        items.append({
            "metric": name,
            "axis": spec["axis"],
            "value": None if val is None or pd.isna(val) else float(val),
            "percentile_score": None if pct is None or pd.isna(pct) else round(float(pct), 2),
            "direction": "높을수록 유리" if spec["direction"] == "benefit" else "높을수록 불리(반전 적용)",
            "grain": spec["grain"],
            "comparison_group": ("전체 확대" if bool(raw_row.get("비교군_확대"))
                                 and spec["group"] == "industry" else spec["group"]),
            "evidence_ids": spec["evidence"],
            "reason_ko": spec["reason_ko"],
            "score_contribution_status": (
                "official_core"
                if name in OFFICIAL_REQUIRED_INDICATORS
                else "optional_context_evidence_only"
                if name in OFFICIAL_OPTIONAL_INDICATORS
                else "separate_candidate_score"
            ),
        })

    ticket_value = raw_row.get("객단가")
    ticket_evidence = {
        **TICKET_EVIDENCE_ONLY,
        "value": None if ticket_value is None or pd.isna(ticket_value) else float(ticket_value),
        "사용_제한": TICKET_EVIDENCE_ONLY["forbidden_claim_ko"],
    }

    # 점수 미투입 evidence 전용 항목 (명세 §2.3): 변화지표 코드 [RV05], R-ONE 참고선 [RV12]
    evidence_only = {
        "객단가_소비단가_참고": ticket_evidence,
        "SBDC_동종점포_보조근거": {
            "percentile_score": scored_row.get("context_evidence__sbdc_competition_percentile"),
            "사용_제한": "공식 경쟁축·현재입지 총점·등급·순위에는 미포함; 보조 경쟁 맥락 전용",
        },
        "생활이동_외부유입_보조근거": {
            "percentile_score": scored_row.get("context_evidence__living_mobility_accessibility_percentile"),
            "사용_제한": "공식 접근성축·현재입지 총점·등급·순위에는 미포함; 보조 접근성 맥락 전용",
        },
        "상권_변화_지표": {
            "코드": raw_row.get("상권_변화_지표_코드"),
            "명": raw_row.get("상권_변화_지표_명"),
            "사용_제한": "범주형 코드의 선형 점수화 금지 [RV05] — 해석 참고 전용",
        },
        "R_ONE_임대_참고선": load_rone_reference(str(raw_row.get("자치구_코드_명") or "")),
        "성장_반등_후보": {
            "score": scored_row.get("growth_rebound_candidate_score"),
            "grade": scored_row.get("growth_rebound_candidate_grade"),
            "gate_reason": scored_row.get("growth_rebound_gate_reason"),
            "status": scored_row.get("growth_rebound_candidate_status"),
            "runtime_feature_safe": scored_row.get("growth_rebound_runtime_feature_safe"),
            "score_engine_active": scored_row.get("growth_rebound_score_engine_active"),
            "사용_제한": "초과성장/반등 후보 신호다. 현재입지 점수, 매출 수준 점수, 성공확률, 성장률 보장으로 해석하지 않는다. [RV36][RV37]",
            "activation_required_ko": scored_row.get("growth_rebound_activation_required_ko"),
            "forbidden_claim_ko": scored_row.get("growth_rebound_forbidden_claim_ko"),
            "algorithm_use_note_ko": scored_row.get("growth_rebound_algorithm_use_note_ko"),
        },
        "교통_접근성_250m_후보": {
            "candidate_score_version": scored_row.get("transit_accessibility_candidate_score_version"),
            "status": scored_row.get("transit_accessibility_candidate_status"),
            "score_engine_active": scored_row.get("transit_accessibility_candidate_engine_active"),
            "engine_promotion_ready": scored_row.get("transit_accessibility_candidate_engine_promotion_ready"),
            "transit_month_count": scored_row.get("transit_month_count"),
            "transit_total_250m_score": scored_row.get("transit_total_250m_score"),
            "candidate_accessibility_axis_score": scored_row.get("transit_accessibility_250m_candidate_axis"),
            "candidate_current_location_score": scored_row.get("current_location_score_transit_250m_candidate"),
            "formula_ko": scored_row.get("transit_accessibility_candidate_formula_ko"),
            "사용_제한": TRANSIT_CANDIDATE_FORBIDDEN_KO,
            "forbidden_claim_ko": scored_row.get("transit_accessibility_candidate_forbidden_claim_ko"),
            "algorithm_use_note_ko": "공식 4축 총점, 등급, 축 점수를 덮어쓰지 않는 접근성 보강 후보 신호다. [RV59][RV60]",
        },
    }

    return {
        "indicators": items,
        "evidence_only": evidence_only,
        "reliability_dimensions": {k.replace("rel__", ""): scored_row[k]
                                   for k in scored_row.index if str(k).startswith("rel__")},
        "provisional_parameters": {k: v for k, v in PROVISIONAL.items()},
        "forbidden_claims": FORBIDDEN_CLAIMS,
        "text_model_rules": TEXT_MODEL_RULES,
        "source_meta": {
            "source_id": raw_row.get("source_id"),
            "directness_level": raw_row.get("directness_level"),
            "forbidden_claim_ko": raw_row.get("forbidden_claim_ko"),
            "snapshot_date": raw_row.get("snapshot_date"),
        },
        "spec_doc": SPEC_DOC,
    }


# ---------------------------------------------------------------------------
# 방향·정규화 행렬 출력 (검증 루프 5)
# ---------------------------------------------------------------------------
def emit_direction_matrix() -> Path:
    rows = []
    for name, spec in INDICATORS.items():
        rows.append({
            "지표": name, "축": spec["axis"], "방향": spec["direction"],
            "grain": spec["grain"], "비교군": spec["group"],
            "근거ID": spec["evidence"], "채택이유": spec["reason_ko"],
            "재검토_후보": "",
        })
    rows.append({
        "지표": TICKET_EVIDENCE_ONLY["metric"],
        "축": TICKET_EVIDENCE_ONLY["axis"],
        "방향": TICKET_EVIDENCE_ONLY["direction"],
        "grain": TICKET_EVIDENCE_ONLY["grain"],
        "비교군": TICKET_EVIDENCE_ONLY["comparison_group"],
        "근거ID": TICKET_EVIDENCE_ONLY["evidence_ids"],
        "채택이유": TICKET_EVIDENCE_ONLY["reason_ko"],
        "재검토_후보": "48~49번 검증으로 점수 직접 가점 제외, evidence-only 유지",
    })
    RULE_VALIDATION_DIR.mkdir(parents=True, exist_ok=True)
    path = RULE_VALIDATION_DIR / "05_direction_normalization_matrix.csv"
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")
    return path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def sanitize_json(obj):
    """NaN/NA를 null로 정리한다. NaN은 JSON 표준에 없으므로 text model/서빙 소비 전 제거
    (출력 계약의 기계가독성 — [Q10] 웹 데이터 공개 기준)."""
    if isinstance(obj, dict):
        return {k: sanitize_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitize_json(v) for v in obj]
    if obj is None or obj is pd.NA:
        return None
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, float) and np.isnan(obj):
        return None
    if isinstance(obj, (np.floating, np.integer)):
        v = obj.item()
        return None if isinstance(v, float) and np.isnan(v) else v
    return obj


def latest_quarter() -> int:
    q = pd.read_csv(GOLD / "gold_sales_strength_q_industry.csv",
                    usecols=["기준_년분기_코드"], encoding="utf-8-sig", dtype=str)
    return int(q["기준_년분기_코드"].astype(int).max())


def resolve_target(base: pd.DataFrame, args) -> pd.Series | None:
    """단일 판정 대상 행 선택. 코드 키 우선, 이름 매칭은 임시 검증용 (스펙 v1 §9)."""
    df = base
    if args.trade_area_code:
        df = df[df["상권_코드"] == args.trade_area_code]
    elif args.trade_area_name:
        print("[경고] 상권명 부분 매칭은 임시 검증용입니다. 운영 입력은 상권_코드만 사용합니다 (스펙 v1 §9).")
        df = df[df["상권_코드_명"].astype(str).str.contains(args.trade_area_name, na=False)]
    if args.industry_code:
        df = df[df["서비스_업종_코드"] == args.industry_code]
    elif args.industry_name:
        print("[경고] 업종명 부분 매칭은 임시 검증용입니다. 운영 입력은 서비스_업종_코드만 사용합니다 (스펙 v1 §9).")
        df = df[df["서비스_업종_코드_명"].astype(str).str.contains(args.industry_name, na=False)]
    if df.empty:
        return None
    return df.iloc[0]


def target_payload(scored_row: pd.Series) -> dict[str, Any]:
    """서빙/리포트가 쓰기 쉬운 분석 대상 구조.

    화면과 LLM에는 사람이 읽는 이름을 주되, 내부 조인 근거로 코드도 함께 보존한다.
    """
    return {
        "analysis_quarter": scored_row["기준_년분기_코드"],
        "trade_area_code": scored_row["상권_코드"],
        "trade_area_name": scored_row["상권_코드_명"],
        "district": scored_row["자치구_코드_명"],
        "industry_code": scored_row["서비스_업종_코드"],
        "industry_name": scored_row["서비스_업종_코드_명"],
        "method": "gold_v2_code_or_name_match",
        "기준_년분기_코드": scored_row["기준_년분기_코드"],
        "상권_코드": scored_row["상권_코드"],
        "상권_코드_명": scored_row["상권_코드_명"],
        "자치구": scored_row["자치구_코드_명"],
        "서비스_업종_코드": scored_row["서비스_업종_코드"],
        "서비스_업종_코드_명": scored_row["서비스_업종_코드_명"],
    }


def scores_payload(scored_row: pd.Series) -> dict[str, Any]:
    """공식 점수 출력 구조. 반등 후보는 후보 신호로만 별도 보존한다."""
    missing_axes = optional_text(scored_row.get("missing_axes"))
    return {
        "current_location_score": scored_row["current_location_score"],
        "context_location_score": scored_row["context_location_score"],
        "score_coverage_tier": scored_row["score_coverage_tier"],
        "available_axis_count": scored_row["available_axis_count"],
        "official_indicator_count": scored_row["official_indicator_count"],
        "official_indicator_defined_count": scored_row["official_indicator_defined_count"],
        "official_indicator_complete": scored_row["official_indicator_complete"],
        "missing_axes": [axis for axis in str(missing_axes or "").split(",") if axis],
        "coverage_reason": scored_row["coverage_reason"],
        "taxonomy_direct_score_allowed": scored_row["taxonomy_direct_score_allowed"],
        "official_rank_eligible": scored_row["official_rank_eligible"],
        "growth_potential_score": scored_row["growth_potential_score"],
        "growth_gate_reason": scored_row["growth_gate_reason"],
        "growth_rebound_candidate_score": scored_row["growth_rebound_candidate_score"],
        "growth_rebound_candidate_grade": scored_row["growth_rebound_candidate_grade"],
        "growth_rebound_gate_reason": scored_row["growth_rebound_gate_reason"],
        "growth_rebound_candidate_status": scored_row["growth_rebound_candidate_status"],
        "transit_accessibility_candidate_status": scored_row["transit_accessibility_candidate_status"],
        "transit_accessibility_250m_candidate_axis": scored_row["transit_accessibility_250m_candidate_axis"],
        "current_location_score_transit_250m_candidate": scored_row["current_location_score_transit_250m_candidate"],
        "cost_risk_score": scored_row["cost_risk_score"],
        "data_reliability_score": scored_row["data_reliability_score"],
        "conservative_score_owa": scored_row["conservative_score_owa"],
        "axis_scores": {ax: scored_row[f"axis__{ax}"] for ax in CURRENT_AXES},
        "weight_set": scored_row["weight_set"],
        "grade": scored_row["grade"],
        "decision_label": scored_row["decision_label"],
    }


def report_components(evidence_pack: dict[str, Any], scores: dict[str, Any]) -> list[dict[str, Any]]:
    """AI 리포트 호환용 컴포넌트 묶음.

    기존 서버 프롬프트가 `score_result.components.evidence`를 읽으므로,
    v2 지표 evidence를 축별로 묶어 같은 계약을 유지한다.
    """
    labels = {
        "sales": "매출 축",
        "competition": "경쟁/상권환경 축",
        "demand": "수요 축",
        "accessibility": "접근성/유입 축",
    }
    indicators = evidence_pack.get("indicators", [])
    grouped: list[dict[str, Any]] = []
    for axis in CURRENT_AXES:
        axis_evidence = [item for item in indicators if item.get("axis") == axis]
        if axis == "sales":
            axis_evidence.append(evidence_pack.get("evidence_only", {}).get("객단가_소비단가_참고", {}))
        grouped.append(
            {
                "key": axis,
                "label_kr": labels[axis],
                "score": scores.get("axis_scores", {}).get(axis),
                "evidence": axis_evidence,
                "explanation_kr": "현재입지 점수 산식에 들어가는 축별 근거입니다.",
            }
        )
    grouped.append(
        {
            "key": "growth_rebound_candidate",
            "label_kr": "성장 반등 후보 신호",
            "score": scores.get("growth_rebound_candidate_score"),
            "evidence": [evidence_pack.get("evidence_only", {}).get("성장_반등_후보", {})],
            "explanation_kr": "현재입지 점수에 섞지 않는 초과성장/반등 후보 신호입니다.",
        }
    )
    grouped.append(
        {
            "key": "transit_accessibility_250m_candidate",
            "label_kr": "교통 접근성 250m 후보 신호",
            "score": scores.get("transit_accessibility_250m_candidate_axis"),
            "evidence": [evidence_pack.get("evidence_only", {}).get("교통_접근성_250m_후보", {})],
            "explanation_kr": "공식 4축 점수에 섞지 않는 접근성 보강 후보 신호입니다.",
        }
    )
    return grouped


def build_result(base: pd.DataFrame, scored: pd.DataFrame, args, quarter: int) -> dict[str, Any]:
    """단건 JSON 결과를 만든다. CLI와 AI 리포트 서버가 같은 계약을 쓴다."""
    raw_row = resolve_target(base, args)
    if raw_row is None:
        raise ValueError("대상 상권×업종을 찾지 못했습니다.")
    key_mask = (scored["상권_코드"] == raw_row["상권_코드"]) & \
               (scored["서비스_업종_코드"] == raw_row["서비스_업종_코드"])
    scored_row = scored[key_mask].iloc[0]
    matched_target = target_payload(scored_row)
    scores = scores_payload(scored_row)
    evidence_pack = build_evidence_pack(raw_row, scored_row)
    score_result = {
        "total_score": scores["current_location_score"],
        "raw_weighted_score": scores["current_location_score"],
        "grade": scores["grade"],
        "decision_label": scores["decision_label"],
        "score_version": SCORE_VERSION,
        "scores": scores,
        "components": report_components(evidence_pack, scores),
        "candidate_signals": {
            "growth_rebound_candidate": evidence_pack.get("evidence_only", {}).get("성장_반등_후보"),
            "transit_accessibility_250m_candidate": evidence_pack.get("evidence_only", {}).get("교통_접근성_250m_후보"),
        },
    }
    warnings = [
        "growth_rebound_candidate_score는 초과성장/반등 후보 신호이며 현재입지 점수나 매출 수준 점수로 해석하지 않습니다.",
        "transit_accessibility_250m_candidate_score는 공식 4축 점수를 덮지 않는 후보 신호이며 실제 방문자 수, 도보시간, 방문확률로 해석하지 않습니다.",
        "성공확률, 성장률 보장, 개별 매장 매출 보장 표현은 금지합니다.",
    ]
    return sanitize_json(
        {
            "schema_version": "seoul_location_judgement.v2",
            "score_version": SCORE_VERSION,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "matched_target": matched_target,
            "scores": scores,
            "score_result": score_result,
            "reportfacts_compact": None,
            "warnings": warnings,
            "evidence_pack": evidence_pack,
            "text_model_payload": {
                "role_kr": "검증된 숫자와 근거만 읽고 한국어 상세 리포트 문장을 작성합니다.",
                "must_use": [
                    "matched_target",
                    "score_result.components.evidence",
                    "score_result.candidate_signals.growth_rebound_candidate",
                    "score_result.candidate_signals.transit_accessibility_250m_candidate",
                    "evidence_pack.forbidden_claims",
                    "warnings",
                ],
                "must_not_do": [
                    "없는 숫자 생성",
                    "출처 없는 주장",
                    "창업 성공확률 표현",
                    "개별 매장 매출 보장",
                    "성장률 예측 또는 성장률 보장",
                    "growth_rebound_candidate_score를 현재입지 점수나 매출 수준 점수처럼 설명",
                    "transit_accessibility_250m_candidate_score를 실제 방문자 수, 실제 구매자 수, 도보시간, 방문확률처럼 설명",
                ],
                "recommended_sections_kr": [
                    "분석 대상",
                    "종합 판단",
                    "현재입지 근거",
                    "성장 반등 후보 신호와 한계",
                    "교통 접근성 후보 신호와 한계",
                    "리스크와 현장 확인 체크리스트",
                ],
            },
        }
    )


def build_output(args) -> dict[str, Any]:
    """AI 리포트 서버용 v2.6 coverage-contract gold 기반 출력 함수."""
    quarter = args.quarter or latest_quarter()
    base = percentile_scores(build_indicator_frame(quarter))
    scored = score_frame(base)
    return build_result(base, scored, args, quarter)


def main() -> int:
    ap = argparse.ArgumentParser(description="서울 상권 입지판단 점수 v2 (근거 명세: " + SPEC_DOC + ")")
    ap.add_argument("--quarter", type=int, help="기준 분기 (예: 20261). 생략 시 매출 silver 최신분기")
    ap.add_argument("--trade-area-code", type=str)
    ap.add_argument("--trade-area-name", type=str)
    ap.add_argument("--industry-code", type=str)
    ap.add_argument("--industry-name", type=str)
    ap.add_argument("--batch", action="store_true", help="분기 전체 배치 채점 CSV 출력 (실험·백테스트용)")
    ap.add_argument("--emit-direction-matrix", action="store_true", help="검증 루프5 방향행렬 CSV 출력")
    args = ap.parse_args()

    if args.emit_direction_matrix:
        path = emit_direction_matrix()
        print(f"방향·정규화 행렬 출력: {path}")
        return 0

    quarter = args.quarter or latest_quarter()
    print(f"[정보] 기준분기 {quarter}, 점수 버전 {SCORE_VERSION}")

    base = build_indicator_frame(quarter)
    base = percentile_scores(base)
    scored = score_frame(base)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    if args.batch:
        out_path = OUT_DIR / f"loc_score_v2_batch_{quarter}_{stamp}.csv"
        scored.to_csv(out_path, index=False, encoding="utf-8-sig")
        print(f"배치 채점 완료: {len(scored):,}행 → {out_path}")
        print("주의: 본 산출물은 rc(검증 전)이며, 루프6·7 실측 전까지 운영 확정판이 아닙니다 (명세 §9).")
        return 0

    try:
        result = build_result(base, scored, args, quarter)
    except ValueError as exc:
        print(f"[오류] {exc}")
        return 1

    out_path = OUT_DIR / f"loc_score_v2_{result['matched_target']['trade_area_code']}_{result['matched_target']['industry_code']}_{quarter}.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(result["scores"], ensure_ascii=False, indent=2, default=str))
    print(f"\n판정 JSON 저장: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
