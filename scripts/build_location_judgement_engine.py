from __future__ import annotations

import argparse
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
MODEL_READY_DIR = ROOT / "datacorpus" / "_final" / "model_ready"
REPORTFACTS_DIR = ROOT / "datacorpus" / "_final" / "reportfacts"
OUT_DIR = ROOT / "datacorpus" / "_location_judgement_outputs"

FEATURE_MART = MODEL_READY_DIR / "서울상권_최종공간OD_FeatureMart.parquet"
REPORTFACTS_CSV = REPORTFACTS_DIR / "서울상권_ReportFacts_최신분기_상권업종.csv"


BASE_WEIGHTS = {
    "demand": 0.170088,
    "sales": 0.279444,
    "competition": 0.202944,
    "accessibility": 0.148619,
    "growth_stability": 0.093794,
    "budget_risk": 0.035500,
    "data_reliability": 0.069611,
}

INDUSTRY_WEIGHT_OVERRIDES = {
    "CS1": {
        "demand": 0.181088,
        "sales": 0.268444,
        "competition": 0.208444,
        "accessibility": 0.165119,
        "growth_stability": 0.082794,
        "budget_risk": 0.035500,
        "data_reliability": 0.058611,
    },
    "CS2": {
        "demand": 0.159088,
        "sales": 0.268444,
        "competition": 0.197444,
        "accessibility": 0.143119,
        "growth_stability": 0.115794,
        "budget_risk": 0.046500,
        "data_reliability": 0.069611,
    },
    "CS3": {
        "demand": 0.164588,
        "sales": 0.284944,
        "competition": 0.208444,
        "accessibility": 0.148619,
        "growth_stability": 0.093794,
        "budget_risk": 0.035500,
        "data_reliability": 0.064111,
    },
}

METRIC_SOURCES = {
    "당월_매출_금액": "서울시 상권분석서비스 추정매출",
    "평균_객단가": "서울시 상권분석서비스 추정매출",
    "매출_전분기_증감률": "FeatureMart 파생 매출 변화율",
    "점포당_매출": "FeatureMart 파생 점포당 매출",
    "총_유동인구_수": "서울시 상권분석서비스 추정 유동인구",
    "총_상주인구_수": "서울시 상권분석서비스 상주인구",
    "총_직장_인구_수": "서울시 상권분석서비스 직장인구",
    "지출_총금액": "서울시 상권분석서비스 소비지출",
    "점포_수": "서울시 상권분석서비스 점포",
    "유사_업종_점포_수": "서울시 상권분석서비스 점포",
    "폐업_률": "서울시 상권분석서비스 점포",
    "개업_율": "서울시 상권분석서비스 점포",
    "운영_영업_개월_평균": "서울시 상권분석서비스 영업기간",
    "지하철_역_수": "서울시 상권분석서비스 집객시설",
    "버스_정거장_수": "서울시 상권분석서비스 집객시설",
    "집객시설_수": "서울시 상권분석서비스 집객시설",
    "공간POI_총점포수": "공간 POI 결합 피처",
    "공간시설_총수": "공간 시설 결합 피처",
    "생활이동_유입_이동인구_합계": "서울 생활이동 자치구 OD 결합 피처",
    "생활이동_순유입_이동인구": "서울 생활이동 자치구 OD 결합 피처",
    "실거래_상업업무_거래금액_만원_평균": "국토교통부 상업업무용 실거래 API 수집본",
    "실거래_상업업무_거래건수": "국토교통부 상업업무용 실거래 API 수집본",
}


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        num = float(value)
    except Exception:
        return None
    if math.isnan(num) or math.isinf(num):
        return None
    return num


def percentile(series: pd.Series, value: Any, higher_is_better: bool = True) -> float | None:
    num = safe_float(value)
    if num is None:
        return None
    s = pd.to_numeric(series, errors="coerce").dropna()
    if s.empty:
        return None
    rank = float((s <= num).mean() * 100)
    if not higher_is_better:
        rank = 100 - rank
    return max(0.0, min(100.0, rank))


def clamp_score(value: float | None, default: float = 50.0) -> float:
    if value is None or math.isnan(float(value)):
        return default
    return round(max(0.0, min(100.0, float(value))), 2)


def weighted_average(items: list[tuple[float | None, float]]) -> float:
    valid = [(score, weight) for score, weight in items if score is not None]
    if not valid:
        return 50.0
    total_weight = sum(weight for _, weight in valid)
    if total_weight <= 0:
        return 50.0
    return sum(float(score) * weight for score, weight in valid) / total_weight


def metric_evidence(
    df: pd.DataFrame,
    row: pd.Series,
    col: str,
    higher_is_better: bool = True,
    peer_df: pd.DataFrame | None = None,
) -> dict[str, Any]:
    value = row.get(col)
    peers = peer_df if peer_df is not None and col in peer_df.columns else df
    pct = percentile(peers[col], value, higher_is_better=higher_is_better) if col in peers.columns else None
    return {
        "metric": col,
        "value": safe_float(value),
        "percentile": None if pct is None else round(pct, 2),
        "direction": "높을수록 유리" if higher_is_better else "낮을수록 유리",
        "source": METRIC_SOURCES.get(col, "FeatureMart 파생 또는 결합 피처"),
    }


def component(label: str, key: str, score: float, weight: float, evidence: list[dict[str, Any]], explanation: str) -> dict[str, Any]:
    return {
        "key": key,
        "label_kr": label,
        "score": round(score, 2),
        "weight": weight,
        "weighted_score": round(score * weight, 4),
        "evidence": evidence,
        "explanation_kr": explanation,
    }


def industry_prefix(industry_code: str | None) -> str:
    if not industry_code:
        return ""
    return industry_code[:3]


def weights_for_industry(industry_code: str | None) -> dict[str, float]:
    weights = INDUSTRY_WEIGHT_OVERRIDES.get(industry_prefix(industry_code), BASE_WEIGHTS).copy()
    total = sum(weights.values())
    return {k: round(v / total, 6) for k, v in weights.items()}


def grade_for_score(score: float) -> str:
    if score >= 85:
        return "A"
    if score >= 75:
        return "B+"
    if score >= 65:
        return "B"
    if score >= 55:
        return "C"
    return "D"


def decision_for_score(score: float, warnings: list[str]) -> str:
    if any("판단 보류" in warning for warning in warnings):
        return "판단 보류"
    if score >= 75:
        return "조건부 긍정"
    if score >= 65:
        return "보통 이상, 현장 확인 필요"
    if score >= 55:
        return "주의 필요"
    return "낮은 적합도"


def normalize_budget(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    text = str(value).replace(",", "").strip()
    if text.endswith("만원"):
        text = text[:-2]
        num = safe_float(text)
        return None if num is None else num * 10_000
    if text.endswith("억"):
        text = text[:-1]
        num = safe_float(text)
        return None if num is None else num * 100_000_000
    return safe_float(text)


def find_target_row(
    mart: pd.DataFrame,
    trade_area_code: str | None,
    industry_code: str | None,
    trade_area_name: str | None,
    industry_name: str | None,
    lat: float | None,
    lng: float | None,
    quarter: int | None,
) -> tuple[pd.Series, dict[str, Any], list[str]]:
    warnings: list[str] = []
    df = mart.copy()
    df["기준_년분기_코드"] = pd.to_numeric(df["기준_년분기_코드"], errors="coerce").astype("Int64")
    target_quarter = int(quarter or df["기준_년분기_코드"].max())
    latest = df[df["기준_년분기_코드"] == target_quarter].copy()
    if latest.empty:
        raise ValueError(f"분석 기준분기 {target_quarter}에 해당하는 행이 없습니다.")

    matched = latest
    match_method: list[str] = []
    if trade_area_code:
        matched = matched[matched["상권_코드"].astype(str) == str(trade_area_code)]
        match_method.append("상권코드 직접 매칭")
    elif trade_area_name:
        matched = matched[matched["상권_코드_명"].astype(str).str.contains(str(trade_area_name), na=False, regex=False)]
        match_method.append("상권명 부분 매칭")
    elif lat is not None and lng is not None and {"상권_중심위도", "상권_중심경도"}.issubset(latest.columns):
        areas = latest.drop_duplicates("상권_코드").copy()
        areas["_dist2"] = (pd.to_numeric(areas["상권_중심위도"], errors="coerce") - lat) ** 2 + (
            pd.to_numeric(areas["상권_중심경도"], errors="coerce") - lng
        ) ** 2
        nearest_code = areas.sort_values("_dist2").iloc[0]["상권_코드"]
        matched = latest[latest["상권_코드"].astype(str) == str(nearest_code)]
        match_method.append("상권 중심점 최근접 매칭")
        warnings.append("좌표 입력은 상권 폴리곤 point-in-polygon이 아니라 중심점 최근접으로 매칭했으므로 실제 경계 확인이 필요합니다.")
    else:
        raise ValueError("상권코드, 상권명, 또는 위도/경도 중 하나가 필요합니다.")

    if industry_code:
        matched = matched[matched["서비스_업종_코드"].astype(str) == str(industry_code)]
        match_method.append("업종코드 직접 매칭")
    elif industry_name:
        matched = matched[matched["서비스_업종_코드_명"].astype(str).str.contains(str(industry_name), na=False, regex=False)]
        match_method.append("업종명 부분 매칭")
    else:
        raise ValueError("서비스 업종코드 또는 업종명이 필요합니다.")

    if matched.empty:
        raise ValueError("입력 조건에 맞는 최신분기 상권-업종 행을 찾지 못했습니다.")
    if len(matched) > 1:
        warnings.append(f"입력 조건에 {len(matched)}개 행이 매칭되어 첫 번째 행을 사용했습니다. 업종코드/상권코드를 더 정확히 지정하는 것이 좋습니다.")
    row = matched.sort_values(["상권_코드", "서비스_업종_코드"]).iloc[0]
    match = {
        "analysis_quarter": target_quarter,
        "trade_area_code": str(row.get("상권_코드")),
        "trade_area_name": str(row.get("상권_코드_명")),
        "industry_code": str(row.get("서비스_업종_코드")),
        "industry_name": str(row.get("서비스_업종_코드_명")),
        "district": str(row.get("자치구_코드_명")),
        "dong": str(row.get("행정동_코드_명")),
        "method": " + ".join(match_method),
    }
    return row, match, warnings


def build_data_quality(row: pd.Series) -> tuple[float, list[str], list[dict[str, Any]]]:
    checks = [
        ("매출", "당월_매출_금액", 18),
        ("점포", "점포_수", 12),
        ("유동인구", "총_유동인구_수", 12),
        ("직장인구", "총_직장_인구_수", 8),
        ("소비지출", "지출_총금액", 8),
        ("공간시설", "공간시설_총수", 6),
        ("공간POI", "공간POI_총점포수", 6),
        ("실거래", "실거래_상업업무_거래건수", 4),
        ("생활이동", "생활이동_유입_이동인구_합계", 4),
    ]
    score = 0.0
    details: list[dict[str, Any]] = []
    warnings: list[str] = []
    for label, col, weight in checks:
        present = col in row.index and safe_float(row.get(col)) is not None
        score += weight if present else 0
        details.append({"label_kr": label, "column": col, "present": present, "weight": weight})
        if not present and weight >= 8:
            warnings.append(f"{label} 핵심 데이터가 비어 있어 해당 축의 판단 신뢰도가 낮습니다.")

    if safe_float(row.get("다음분기_매출")) is not None:
        warnings.append("최신 리포트 입력에서는 다음분기_매출 라벨을 판단에 사용하지 않습니다.")
    for col in ["SDOT보행_관측수", "SDOT환경_관측수", "생활인구250m_관측일수"]:
        if col in row.index and safe_float(row.get(col)) is None:
            warnings.append(f"{col} 값이 없어 해당 세부 피처는 설명에서 제외합니다.")

    return clamp_score(score), warnings, details


def score_target(mart: pd.DataFrame, row: pd.Series, budget_krw: float | None) -> dict[str, Any]:
    latest_q = int(row["기준_년분기_코드"])
    latest = mart[pd.to_numeric(mart["기준_년분기_코드"], errors="coerce") == latest_q].copy()
    industry_df = latest[latest["서비스_업종_코드"].astype(str) == str(row["서비스_업종_코드"])].copy()
    district_df = latest[latest["자치구_코드_명"].astype(str) == str(row.get("자치구_코드_명"))].copy()
    peer_df = industry_df if len(industry_df) >= 20 else latest

    data_score, data_warnings, data_checks = build_data_quality(row)
    demand_evidence = [
        metric_evidence(latest, row, "총_유동인구_수", True, latest),
        metric_evidence(latest, row, "총_직장_인구_수", True, latest),
        metric_evidence(latest, row, "총_상주인구_수", True, latest),
        metric_evidence(latest, row, "지출_총금액", True, latest),
    ]
    demand_score = weighted_average([(e["percentile"], 1) for e in demand_evidence])

    sales_evidence = [
        metric_evidence(latest, row, "당월_매출_금액", True, peer_df),
        metric_evidence(latest, row, "점포당_매출", True, peer_df),
        metric_evidence(latest, row, "평균_객단가", True, peer_df),
        metric_evidence(latest, row, "매출_전분기_증감률", True, peer_df),
    ]
    sales_score = weighted_average([(sales_evidence[0]["percentile"], 1.2), (sales_evidence[1]["percentile"], 1.2), (sales_evidence[2]["percentile"], 0.6), (sales_evidence[3]["percentile"], 0.8)])

    competition_evidence = [
        metric_evidence(latest, row, "점포당_매출", True, peer_df),
        metric_evidence(latest, row, "유사_업종_점포_수", False, peer_df),
        metric_evidence(latest, row, "점포_수", True, district_df if len(district_df) >= 20 else latest),
        metric_evidence(latest, row, "폐업_률", False, peer_df),
    ]
    competition_score = weighted_average([(competition_evidence[0]["percentile"], 1.2), (competition_evidence[1]["percentile"], 0.8), (competition_evidence[2]["percentile"], 0.5), (competition_evidence[3]["percentile"], 1.0)])

    accessibility_evidence = [
        metric_evidence(latest, row, "지하철_역_수", True, latest),
        metric_evidence(latest, row, "버스_정거장_수", True, latest),
        metric_evidence(latest, row, "집객시설_수", True, latest),
        metric_evidence(latest, row, "공간시설_총수", True, latest),
        metric_evidence(latest, row, "생활이동_유입_이동인구_합계", True, latest),
    ]
    accessibility_score = weighted_average([(e["percentile"], 1) for e in accessibility_evidence])

    growth_evidence = [
        metric_evidence(latest, row, "매출_전분기_증감률", True, peer_df),
        metric_evidence(latest, row, "개업_율", True, peer_df),
        metric_evidence(latest, row, "폐업_률", False, peer_df),
        metric_evidence(latest, row, "운영_영업_개월_평균", True, peer_df),
    ]
    growth_score = weighted_average([(growth_evidence[0]["percentile"], 1.0), (growth_evidence[1]["percentile"], 0.5), (growth_evidence[2]["percentile"], 1.0), (growth_evidence[3]["percentile"], 0.7)])

    budget_evidence = [
        metric_evidence(latest, row, "실거래_상업업무_거래금액_만원_평균", False, latest),
        metric_evidence(latest, row, "실거래_상업업무_거래건수", True, latest),
    ]
    budget_score = weighted_average([(budget_evidence[0]["percentile"], 0.8), (budget_evidence[1]["percentile"], 0.2)])
    budget_warnings: list[str] = []
    if budget_krw is None:
        budget_score = 50.0
        budget_warnings.append("사용자 예산이 없어 예산 적합도는 중립 50점으로 처리했습니다.")
    else:
        budget_warnings.append("현재 데이터에는 월세·권리금 원자료가 없어 예산은 실거래 매매 프록시와 위험 설명에만 제한적으로 사용합니다.")
        avg_trade_10k = safe_float(row.get("실거래_상업업무_거래금액_만원_평균"))
        if avg_trade_10k is not None:
            trade_proxy_krw = avg_trade_10k * 10_000
            ratio = budget_krw / trade_proxy_krw if trade_proxy_krw > 0 else None
            if ratio is not None:
                budget_evidence.append(
                    {
                        "metric": "사용자예산/상업업무용_평균거래금액",
                        "value": round(ratio, 4),
                        "percentile": None,
                        "direction": "높을수록 여유",
                        "source": "사용자 입력 예산 + 국토교통부 상업업무용 실거래 프록시",
                    }
                )
                if ratio < 0.05:
                    budget_score = min(budget_score, 35)
                    budget_warnings.append("입력 예산이 상업업무용 평균 거래금액 프록시에 비해 매우 낮아 비용 리스크를 낮게 평가했습니다.")

    weights = weights_for_industry(str(row.get("서비스_업종_코드")))
    components = [
        component("수요 점수", "demand", clamp_score(demand_score), weights["demand"], demand_evidence, "유동인구, 직장인구, 상주인구, 소비지출을 함께 본 배후수요 점수입니다."),
        component("매출 점수", "sales", clamp_score(sales_score), weights["sales"], sales_evidence, "같은 업종 내 현재 매출, 점포당 매출, 객단가, 전분기 변화율을 비교한 점수입니다."),
        component("경쟁/상권환경 점수", "competition", clamp_score(competition_score), weights["competition"], competition_evidence, "점포가 많다는 집객 효과와 유사 업종 경쟁, 폐업률 위험을 함께 본 점수입니다."),
        component("접근성/유입 점수", "accessibility", clamp_score(accessibility_score), weights["accessibility"], accessibility_evidence, "교통, 집객시설, 공간시설, 생활이동 유입을 함께 본 점수입니다."),
        component("성장/안정성 점수", "growth_stability", clamp_score(growth_score), weights["growth_stability"], growth_evidence, "매출 변화, 개업률, 폐업률, 평균 영업기간으로 본 안정성 점수입니다."),
        component("예산/비용 리스크 점수", "budget_risk", clamp_score(budget_score), weights["budget_risk"], budget_evidence, "월세·권리금 원자료가 없으므로 실거래 프록시와 사용자 예산을 제한적으로만 반영한 점수입니다."),
        component("데이터 신뢰도 점수", "data_reliability", clamp_score(data_score), weights["data_reliability"], data_checks, "핵심 데이터가 비어 있는지, 기간 정합성이 약한 프록시가 섞였는지 보는 게이트 점수입니다."),
    ]
    raw_score = sum(item["weighted_score"] for item in components)
    warnings = data_warnings + budget_warnings
    if data_score < 60:
        warnings.append("데이터 신뢰도 점수가 낮아 최종 판단 보류가 필요합니다.")
        final_score = min(raw_score, 65)
    else:
        final_score = raw_score

    return {
        "score": {
            "total_score": round(final_score, 2),
            "raw_weighted_score": round(raw_score, 2),
            "grade": grade_for_score(final_score),
            "decision_label": decision_for_score(final_score, warnings),
            "weights": weights,
            "components": components,
        },
        "warnings": warnings,
    }


def build_reportfacts_lookup() -> pd.DataFrame:
    if not REPORTFACTS_CSV.exists():
        return pd.DataFrame()
    return pd.read_csv(REPORTFACTS_CSV)


def find_reportfacts_row(rf: pd.DataFrame, row: pd.Series) -> dict[str, Any] | None:
    if rf.empty:
        return None
    key = (
        (pd.to_numeric(rf["기준_년분기_코드"], errors="coerce") == int(row["기준_년분기_코드"]))
        & (rf["상권_코드"].astype(str) == str(row["상권_코드"]))
        & (rf["서비스_업종_코드"].astype(str) == str(row["서비스_업종_코드"]))
    )
    matched = rf[key]
    if matched.empty:
        return None
    rec = matched.iloc[0].to_dict()
    return {k: (None if pd.isna(v) else v) for k, v in rec.items()}


def build_output(args: argparse.Namespace) -> dict[str, Any]:
    mart = pd.read_parquet(FEATURE_MART)
    budget_krw = normalize_budget(args.budget_krw)
    row, match, match_warnings = find_target_row(
        mart,
        trade_area_code=args.trade_area_code,
        industry_code=args.industry_code,
        trade_area_name=args.trade_area_name,
        industry_name=args.industry_name,
        lat=args.lat,
        lng=args.lng,
        quarter=args.quarter,
    )
    scored = score_target(mart, row, budget_krw)
    reportfacts = find_reportfacts_row(build_reportfacts_lookup(), row)

    warnings = match_warnings + scored["warnings"]
    output = {
        "schema_version": "seoul_location_judgement.v1",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "language": "ko",
        "request": {
            "trade_area_code": args.trade_area_code,
            "trade_area_name": args.trade_area_name,
            "industry_code": args.industry_code,
            "industry_name": args.industry_name,
            "budget_krw": budget_krw,
            "lat": args.lat,
            "lng": args.lng,
            "quarter": args.quarter,
        },
        "matched_target": match,
        "score_result": scored["score"],
        "reportfacts_compact": reportfacts,
        "warnings": warnings,
        "text_model_payload": {
            "role_kr": "검증된 숫자와 근거만 읽고 한국어 상세 리포트 문장을 작성합니다.",
            "must_use": [
                "matched_target",
                "score_result.components.evidence",
                "reportfacts_compact",
                "warnings",
            ],
            "must_not_do": [
                "없는 숫자 생성",
                "출처 없는 주장",
                "개별 매장 매출 단정",
                "성공 가능성 보장",
                "모델 예측값이나 성공확률 임의 생성",
            ],
            "recommended_sections_kr": [
                "분석 대상과 기준일",
                "종합 점수와 등급",
                "수요와 매출 근거",
                "경쟁과 접근성 근거",
                "예산/비용 리스크",
                "데이터 신뢰도와 경고",
                "현장 확인 체크리스트",
            ],
        },
    }
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="서울 상권 입지 판단 본체 JSON 생성")
    parser.add_argument("--trade-area-code", help="상권 코드")
    parser.add_argument("--trade-area-name", help="상권명 부분 문자열")
    parser.add_argument("--industry-code", help="서비스 업종 코드")
    parser.add_argument("--industry-name", help="서비스 업종명 부분 문자열")
    parser.add_argument("--budget-krw", help="사용자 예산. 예: 50000000, 5000만원, 1.2억")
    parser.add_argument("--lat", type=float, help="위도. 상권코드가 없을 때 중심점 최근접 매칭")
    parser.add_argument("--lng", type=float, help="경도. 상권코드가 없을 때 중심점 최근접 매칭")
    parser.add_argument("--quarter", type=int, help="분석 기준 분기. 기본값은 최신분기")
    parser.add_argument("--output", default=str(OUT_DIR / "sample_location_judgement.json"), help="출력 JSON 경로")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    output = build_output(args)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(out_path), "total_score": output["score_result"]["total_score"], "grade": output["score_result"]["grade"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
