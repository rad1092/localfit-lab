from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SILVER = ROOT / "datacorpus" / "_silver"
GOLD = ROOT / "datacorpus" / "_gold"
RULE_VALIDATION = ROOT / "datacorpus" / "_rule_validation"
RESEARCH_RULE_VALIDATION = ROOT / "research" / "rule_validation"

RAW_MATCH_PATH = SILVER / "silver_localdata_food_license_trade_area_match.csv"
TRADE_AREA_MONTHLY_PATH = SILVER / "silver_localdata_food_license_trade_area_open_close_monthly.csv"
INDUSTRY_TAXONOMY_PATH = GOLD / "gold_industry_selection_hierarchy.csv"

BRIDGE_PATH = SILVER / "silver_localdata_food_license_uptae_service_bridge.csv"
MONTHLY_CANDIDATE_PATH = SILVER / "silver_localdata_food_license_trade_area_service_monthly_candidate.csv"
QUARTER_CANDIDATE_PATH = SILVER / "silver_localdata_food_license_trade_area_service_quarter_candidate.csv"
VALIDATION_PATH = RULE_VALIDATION / "45_localdata_food_industry_bridge_validation.csv"
SUMMARY_PATH = RULE_VALIDATION / "45_localdata_food_industry_bridge_summary.json"
MD_PATH = RESEARCH_RULE_VALIDATION / "45_localdata_food_industry_bridge_validation_20260707.md"

BRIDGE_VERSION = "localdata_food_bridge.v0.1-20260707"


COL = {
    "license_category": "license_category",
    "uptae": "업태명",
    "month": "년월",
    "open_count": "인허가건수",
    "close_count": "폐업건수",
    "state_group": "상태그룹",
    "match_status": "match_status",
    "trade_area_code": "상권_코드",
    "trade_area_name": "상권_코드_명",
    "service_code": "서비스_업종_코드",
    "service_name": "서비스_업종_코드_명",
}


# LocalData 업태명은 서울 상권분석서비스 업종 코드와 같은 체계가 아니다.
# 따라서 이 규칙표는 "점수 직접 투입"이 아니라 업태를 서울 서비스업종 후보로 옮기기 위한 bridge다.
# auto_strong도 인허가 프록시 후보일 뿐이며, 매출/성공/생존을 직접 보장하지 않는다.
UPTAE_RULES: dict[str, dict[str, str | bool | float]] = {
    "한식": {
        "service_code": "CS100001",
        "mapping_status": "auto_strong",
        "confidence": 0.95,
        "review_required": False,
        "reason_ko": "업태명과 서울 서비스업종명이 직접 대응한다.",
    },
    "중국식": {
        "service_code": "CS100002",
        "mapping_status": "auto_strong",
        "confidence": 0.95,
        "review_required": False,
        "reason_ko": "중국식은 중식음식점 후보로 직접 대응한다.",
    },
    "일식": {
        "service_code": "CS100003",
        "mapping_status": "auto_strong",
        "confidence": 0.95,
        "review_required": False,
        "reason_ko": "일식은 일식음식점 후보로 직접 대응한다.",
    },
    "경양식": {
        "service_code": "CS100004",
        "mapping_status": "auto_strong",
        "confidence": 0.90,
        "review_required": False,
        "reason_ko": "경양식은 서울 서비스업종 양식음식점의 대표 업태다.",
    },
    "패스트푸드": {
        "service_code": "CS100006",
        "mapping_status": "auto_strong",
        "confidence": 0.95,
        "review_required": False,
        "reason_ko": "업태명과 서울 서비스업종명이 직접 대응한다.",
    },
    "통닭(치킨)": {
        "service_code": "CS100007",
        "mapping_status": "auto_strong",
        "confidence": 0.95,
        "review_required": False,
        "reason_ko": "통닭/치킨 업태는 치킨전문점 후보로 직접 대응한다.",
    },
    "분식": {
        "service_code": "CS100008",
        "mapping_status": "auto_strong",
        "confidence": 0.95,
        "review_required": False,
        "reason_ko": "분식 업태는 분식전문점 후보로 직접 대응한다.",
    },
    "김밥(도시락)": {
        "service_code": "CS100008",
        "mapping_status": "auto_strong",
        "confidence": 0.90,
        "review_required": False,
        "reason_ko": "김밥/도시락은 분식전문점의 세부 업태로 볼 수 있다.",
    },
    "정종/대포집/소주방": {
        "service_code": "CS100009",
        "mapping_status": "auto_review",
        "confidence": 0.75,
        "review_required": True,
        "reason_ko": "주류 중심 간이주점 후보이나 호프/간이주점과 영업형태가 완전히 같다고 단정하지 않는다.",
    },
    "커피숍": {
        "service_code": "CS100010",
        "mapping_status": "auto_strong",
        "confidence": 0.95,
        "review_required": False,
        "reason_ko": "커피숍은 커피-음료 후보로 직접 대응한다.",
    },
    "다방": {
        "service_code": "CS100010",
        "mapping_status": "auto_review",
        "confidence": 0.70,
        "review_required": True,
        "reason_ko": "다방은 음료 업태지만 현대 커피전문점과 영업형태가 다를 수 있어 검토 플래그를 유지한다.",
    },
    "전통찻집": {
        "service_code": "CS100010",
        "mapping_status": "auto_review",
        "confidence": 0.70,
        "review_required": True,
        "reason_ko": "차 음료 업태로 커피-음료 후보이나 커피전문점과 동일시하지 않는다.",
    },
    "과자점": {
        "service_code": "CS100005",
        "mapping_status": "auto_review",
        "confidence": 0.75,
        "review_required": True,
        "reason_ko": "과자점은 제과점 후보이나 판매/제조/휴게음식점 성격이 섞일 수 있어 검토 플래그를 유지한다.",
    },
    "제과점영업": {
        "service_code": "CS100005",
        "mapping_status": "auto_strong",
        "confidence": 0.95,
        "review_required": False,
        "reason_ko": "업태명 자체가 제과점 영업을 뜻한다.",
    },
    "패밀리레스트랑": {
        "service_code": "CS100004",
        "mapping_status": "auto_review",
        "confidence": 0.70,
        "review_required": True,
        "reason_ko": "서양식/양식 계열 후보지만 업태 범위가 넓어 수동검토를 유지한다.",
    },
    "외국음식전문점(인도,태국등)": {
        "service_code": "CS100004",
        "mapping_status": "auto_review",
        "confidence": 0.60,
        "review_required": True,
        "reason_ko": "서울 서비스업종에 별도 외국음식 코드가 없어 양식음식점 후보로만 보류한다.",
    },
    "뷔페식": {
        "service_code": "CS100004",
        "mapping_status": "auto_review",
        "confidence": 0.55,
        "review_required": True,
        "reason_ko": "뷔페는 조리 양식이 혼합될 수 있어 양식음식점 후보로만 보류한다.",
    },
    "식육(숯불구이)": {
        "service_code": "CS100001",
        "mapping_status": "auto_review",
        "confidence": 0.65,
        "review_required": True,
        "reason_ko": "한식 구이류 후보지만 업태 범위가 넓어 수동검토가 필요하다.",
    },
    "횟집": {
        "service_code": "CS100001",
        "mapping_status": "auto_review",
        "confidence": 0.60,
        "review_required": True,
        "reason_ko": "서울 서비스업종에 횟집 세부 코드가 없어 한식음식점 후보로만 보류한다.",
    },
    "냉면집": {
        "service_code": "CS100001",
        "mapping_status": "auto_review",
        "confidence": 0.65,
        "review_required": True,
        "reason_ko": "한식 세부 업태 후보이나 서울 서비스업종 세분류가 부족해 검토 플래그를 둔다.",
    },
    "탕류(보신용)": {
        "service_code": "CS100001",
        "mapping_status": "auto_review",
        "confidence": 0.60,
        "review_required": True,
        "reason_ko": "한식 탕류 후보이나 세부 업태 특수성이 있어 검토 플래그를 둔다.",
    },
    "복어취급": {
        "service_code": "CS100003",
        "mapping_status": "auto_review",
        "confidence": 0.55,
        "review_required": True,
        "reason_ko": "복어는 일식/한식으로 모두 볼 수 있어 일식음식점 후보로만 보류한다.",
    },
    "일반조리판매": {
        "service_code": "CS100008",
        "mapping_status": "auto_review",
        "confidence": 0.55,
        "review_required": True,
        "reason_ko": "휴게음식점 조리판매는 분식/간편식 후보이나 범위가 넓다.",
    },
    "떡카페": {
        "service_code": "CS100010",
        "mapping_status": "auto_review",
        "confidence": 0.55,
        "review_required": True,
        "reason_ko": "카페 성격이 있으나 떡 판매와 혼합되어 커피-음료 후보로만 보류한다.",
    },
    "아이스크림": {
        "service_code": "CS100010",
        "mapping_status": "auto_review",
        "confidence": 0.55,
        "review_required": True,
        "reason_ko": "음료/디저트 계열 후보이나 서울 서비스업종에 아이스크림 세부 코드가 없다.",
    },
    "감성주점": {
        "service_code": "CS100009",
        "mapping_status": "auto_review",
        "confidence": 0.65,
        "review_required": True,
        "reason_ko": "주점 계열 후보이나 일반 호프-간이주점과 영업형태가 다를 수 있다.",
    },
    "간이주점": {
        "service_code": "CS100009",
        "mapping_status": "auto_review",
        "confidence": 0.70,
        "review_required": True,
        "reason_ko": "명칭상 호프-간이주점과 가깝지만 원천 row가 극소수라 검토 플래그를 유지한다.",
    },
}


HOLD_REASONS = {
    "기타": "기타 업태는 음식 종류를 특정할 수 없어 서비스업종 코드 후보를 만들지 않는다.",
    "기타 휴게음식점": "기타 휴게음식점은 업종 범위가 넓어 서비스업종 코드 후보를 만들지 않는다.",
    "호프/통닭": "호프와 통닭이 혼합된 일대다 후보라 단일 서비스업종으로 자동 집계하지 않는다.",
    "까페": "일반음식점 까페는 커피-음료와 주점/음식점 성격이 섞일 수 있어 보류한다.",
    "라이브카페": "라이브카페는 커피-음료와 주점 성격이 섞일 수 있어 보류한다.",
    "키즈카페": "키즈카페는 음식점보다 놀이시설 성격이 섞일 수 있어 보류한다.",
    "편의점": "편의점은 서울 서비스업종 음식점 10개와 직접 대응하지 않는다.",
    "백화점": "백화점은 장소/입지 업태이지 음식 세부업종이 아니다.",
    "철도역구내": "철도역구내는 장소 업태라 음식 서비스업종을 특정할 수 없다.",
    "푸드트럭": "푸드트럭은 이동 영업 형태라 음식 종류를 특정할 수 없다.",
    "출장조리": "출장조리는 위치 기반 상권 점수와 결합하기 부적합한 영업 형태다.",
    "이동조리": "이동조리는 고정 상권 polygon과 직접 연결하기 어렵다.",
    "유원지": "유원지는 장소 업태라 음식 종류를 특정할 수 없다.",
    "관광호텔": "관광호텔은 숙박/시설 성격이 섞여 음식 서비스업종을 특정할 수 없다.",
    "극장": "극장은 장소 업태라 음식 서비스업종을 특정할 수 없다.",
    "공항": "공항은 장소 업태라 음식 서비스업종을 특정할 수 없다.",
    "고속도로": "고속도로는 장소 업태라 음식 서비스업종을 특정할 수 없다.",
    "룸살롱": "유흥 성격이 강해 서울 음식점 서비스업종 후보로 자동 연결하지 않는다.",
    "단란주점": "유흥 성격이 강해 서울 음식점 서비스업종 후보로 자동 연결하지 않는다.",
    "식품등 수입판매업": "판매/유통 인허가 성격이라 음식점 서비스업종 후보가 아니다.",
    "식품소분업": "제조/소분 인허가 성격이라 음식점 서비스업종 후보가 아니다.",
    "미상": "업태명이 결측이라 서비스업종 코드 후보를 만들지 않는다.",
}


def normalize_uptae(value: object) -> str:
    text = "" if pd.isna(value) else str(value).strip()
    return text if text else "미상"


def load_food_service_taxonomy() -> pd.DataFrame:
    taxonomy = pd.read_csv(INDUSTRY_TAXONOMY_PATH, encoding="utf-8-sig")
    food = taxonomy[taxonomy[COL["service_code"]].astype(str).str.startswith("CS1")].copy()
    food = food[
        [
            COL["service_code"],
            COL["service_name"],
            "direct_score_allowed",
            "selection_path",
            "final_algorithm_key",
            "lookup_use_status",
        ]
    ]
    return food


def build_bridge() -> pd.DataFrame:
    # 원천 전체 row에서 license_category+업태명별 관측 수와 공간매칭 가능 수를 집계한다.
    raw = pd.read_csv(
        RAW_MATCH_PATH,
        usecols=[COL["license_category"], COL["uptae"], COL["match_status"]],
        encoding="utf-8-sig",
        low_memory=False,
    )
    raw["normalized_uptae"] = raw[COL["uptae"]].map(normalize_uptae)
    usable_match = raw[COL["match_status"]].isin(["polygon_match", "multi_polygon_match_choose_smallest_area"])
    raw["_spatial_candidate_row"] = usable_match.astype(int)

    observed = (
        raw.groupby([COL["license_category"], "normalized_uptae"], dropna=False)
        .agg(
            observed_raw_rows=("normalized_uptae", "size"),
            spatial_candidate_rows=("_spatial_candidate_row", "sum"),
        )
        .reset_index()
    )

    food = load_food_service_taxonomy()
    service_lookup = food.set_index(COL["service_code"]).to_dict("index")

    rows: list[dict] = []
    for _, row in observed.iterrows():
        uptae = row["normalized_uptae"]
        rule = UPTAE_RULES.get(uptae)
        if rule is None:
            rule = {
                "service_code": "",
                "mapping_status": "hold_unmapped",
                "confidence": 0.0,
                "review_required": True,
                "reason_ko": HOLD_REASONS.get(uptae, "명시 규칙이 없어 수동검토 전까지 보류한다."),
            }

        service_code = str(rule["service_code"])
        service_info = service_lookup.get(service_code, {})
        rows.append(
            {
                "license_category": row[COL["license_category"]],
                "업태명": uptae,
                "normalized_uptae": uptae,
                "candidate_서비스_업종_코드": service_code,
                "candidate_서비스_업종_코드_명": service_info.get(COL["service_name"], ""),
                "mapping_status": rule["mapping_status"],
                "mapping_confidence": rule["confidence"],
                "mapping_review_required": bool(rule["review_required"]),
                "mapping_reason_ko": rule["reason_ko"],
                "observed_raw_rows": int(row["observed_raw_rows"]),
                "spatial_candidate_rows": int(row["spatial_candidate_rows"]),
                "service_code_exists_in_gold": bool(service_code and service_code in service_lookup),
                "target_direct_score_available": bool(service_info.get("direct_score_allowed", False)),
                "localdata_direct_score_allowed": False,
                "localdata_proxy_candidate_allowed": bool(service_code and rule["mapping_status"] in {"auto_strong", "auto_review"}),
                "bridge_use_status": (
                    "candidate_proxy_only"
                    if service_code and rule["mapping_status"] in {"auto_strong", "auto_review"}
                    else "hold_not_aggregated"
                ),
                "forbidden_claim_ko": "LocalData 인허가는 개폐업/영업상태 프록시 후보이며 창업 성공확률, 개별 매출, 생존확률을 직접 보장하지 않는다.",
                "source_validation_refs": "14_localdata_food_license_silver;17_localdata_trade_area_spatial_match;40_industry_selection_fallback_hierarchy;44_rule_pipeline_source_coverage",
                "bridge_version": BRIDGE_VERSION,
            }
        )

    return pd.DataFrame(rows).sort_values(
        ["mapping_status", "candidate_서비스_업종_코드", "license_category", "업태명"],
        na_position="last",
    )


def build_monthly_candidate(bridge: pd.DataFrame) -> pd.DataFrame:
    monthly = pd.read_csv(TRADE_AREA_MONTHLY_PATH, encoding="utf-8-sig")
    monthly["normalized_uptae"] = monthly[COL["uptae"]].map(normalize_uptae)
    merged = monthly.merge(
        bridge,
        on=["license_category", "normalized_uptae"],
        how="left",
        validate="many_to_one",
    )

    # 보류 업태는 잃어버리는 것이 아니라 bridge/audit에 보존하고, 서비스업종 후보 집계에서는 제외한다.
    candidate = merged[merged["localdata_proxy_candidate_allowed"].fillna(False)].copy()
    group_cols = [
        COL["trade_area_code"],
        COL["trade_area_name"],
        "상권_구분_코드",
        "상권_구분_코드_명",
        "상권_자치구_코드",
        "상권_자치구_코드_명",
        "상권_행정동_코드",
        "상권_행정동_코드_명",
        "candidate_서비스_업종_코드",
        "candidate_서비스_업종_코드_명",
        "mapping_status",
        "mapping_review_required",
        COL["month"],
    ]

    out = (
        candidate.groupby(group_cols, dropna=False)
        .agg(
            인허가건수=(COL["open_count"], "sum"),
            폐업건수=(COL["close_count"], "sum"),
            contributing_license_category_count=("license_category", "nunique"),
            contributing_uptae_count=("normalized_uptae", "nunique"),
            observed_spatial_rows_from_bridge=("spatial_candidate_rows", "sum"),
        )
        .reset_index()
    )
    out["source_id"] = "seoul_localdata_food_license"
    out["provider"] = "서울열린데이터광장/행정안전부 지방행정 인허가"
    out["candidate_role"] = "상권×서비스업종×월 인허가 개폐업 프록시 후보"
    out["localdata_direct_score_allowed"] = False
    out["score_use_status"] = "후보: 업태 bridge 기반 보조 프록시. 직접점수 투입 전 백테스트 필요"
    out["forbidden_claim_ko"] = "개별 매장 성공확률, 생존확률, 매출 보장으로 표현 금지"
    out["bridge_version"] = BRIDGE_VERSION
    return out.sort_values(group_cols)


def build_quarter_candidate(monthly: pd.DataFrame) -> pd.DataFrame:
    out = monthly.copy()
    month_num = pd.to_numeric(out[COL["month"]], errors="coerce")
    year = (month_num // 100).astype("Int64")
    month = (month_num % 100).astype("Int64")
    quarter = ((month - 1) // 3 + 1).astype("Int64")
    out["기준_년분기_코드"] = (year * 10 + quarter).astype("Int64")
    group_cols = [
        COL["trade_area_code"],
        COL["trade_area_name"],
        "상권_구분_코드",
        "상권_구분_코드_명",
        "상권_자치구_코드",
        "상권_자치구_코드_명",
        "상권_행정동_코드",
        "상권_행정동_코드_명",
        "candidate_서비스_업종_코드",
        "candidate_서비스_업종_코드_명",
        "mapping_status",
        "mapping_review_required",
        "기준_년분기_코드",
    ]
    q = (
        out.groupby(group_cols, dropna=False)
        .agg(
            인허가건수=("인허가건수", "sum"),
            폐업건수=("폐업건수", "sum"),
            contributing_month_count=(COL["month"], "nunique"),
            contributing_license_category_count=("contributing_license_category_count", "max"),
            contributing_uptae_count=("contributing_uptae_count", "max"),
        )
        .reset_index()
    )
    q["source_id"] = "seoul_localdata_food_license"
    q["provider"] = "서울열린데이터광장/행정안전부 지방행정 인허가"
    q["candidate_role"] = "상권×서비스업종×분기 인허가 개폐업 프록시 후보"
    q["localdata_direct_score_allowed"] = False
    q["score_use_status"] = "후보: 업태 bridge 기반 보조 프록시. 직접점수 투입 전 백테스트 필요"
    q["forbidden_claim_ko"] = "개별 매장 성공확률, 생존확률, 매출 보장으로 표현 금지"
    q["bridge_version"] = BRIDGE_VERSION
    return q.sort_values(group_cols)


def add_validation(rows: list[dict], rule_id: str, name: str, observed, expected, passed: bool, reason: str) -> None:
    rows.append(
        {
            "validation_id": rule_id,
            "validation_name": name,
            "observed": observed,
            "expected": expected,
            "result": "PASS" if passed else "FAIL",
            "reason_ko": reason,
        }
    )


def build_validations(bridge: pd.DataFrame, monthly: pd.DataFrame, quarter: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    validations: list[dict] = []

    raw = pd.read_csv(
        RAW_MATCH_PATH,
        usecols=[COL["license_category"], COL["uptae"], COL["match_status"]],
        encoding="utf-8-sig",
        low_memory=False,
    )
    raw["normalized_uptae"] = raw[COL["uptae"]].map(normalize_uptae)
    observed_pairs = raw[[COL["license_category"], "normalized_uptae"]].drop_duplicates()
    usable_raw = raw[raw[COL["match_status"]].isin(["polygon_match", "multi_polygon_match_choose_smallest_area"])].copy()
    service_codes = set(load_food_service_taxonomy()[COL["service_code"]].astype(str))

    add_validation(
        validations,
        "45-V01",
        "LocalData 업태 pair 전체 bridge 상태 부여",
        int(len(bridge)),
        int(len(observed_pairs)),
        int(len(bridge)) == int(len(observed_pairs)),
        "업태 bridge가 일부 업태를 조용히 버리면 이후 개폐업 프록시가 왜곡된다.",
    )
    add_validation(
        validations,
        "45-V02",
        "후보 서비스업종 코드는 gold 음식업 CS100 코드 안에 존재",
        int(bridge.loc[bridge["candidate_서비스_업종_코드"].ne(""), "candidate_서비스_업종_코드"].isin(service_codes).all()),
        1,
        bool(bridge.loc[bridge["candidate_서비스_업종_코드"].ne(""), "candidate_서비스_업종_코드"].isin(service_codes).all()),
        "LocalData 업태명으로 조인하지 않고 최종 알고리즘 키인 서비스_업종_코드 후보만 사용한다.",
    )
    add_validation(
        validations,
        "45-V03",
        "LocalData 직접점수 승격 금지",
        int((~bridge["localdata_direct_score_allowed"]).all() and (~monthly["localdata_direct_score_allowed"]).all() and (~quarter["localdata_direct_score_allowed"]).all()),
        1,
        bool((~bridge["localdata_direct_score_allowed"]).all() and (~monthly["localdata_direct_score_allowed"]).all() and (~quarter["localdata_direct_score_allowed"]).all()),
        "14/17/44번 검증에 따라 LocalData는 인허가 이력 프록시 후보이지 매출·성공 직접점수가 아니다.",
    )
    ambiguous = [
        "기타",
        "기타 휴게음식점",
        "호프/통닭",
        "편의점",
        "백화점",
        "푸드트럭",
        "철도역구내",
        "관광호텔",
        "극장",
        "공항",
        "고속도로",
    ]
    ambiguous_rows = bridge[bridge["normalized_uptae"].isin(ambiguous)]
    ambiguous_ok = ambiguous_rows["bridge_use_status"].eq("hold_not_aggregated").all()
    add_validation(
        validations,
        "45-V04",
        "모호/장소/혼합 업태 자동집계 금지",
        int(ambiguous_ok),
        1,
        bool(ambiguous_ok),
        "음식 종류를 특정할 수 없는 업태는 서비스업종 후보로 억지 배정하지 않는다.",
    )
    monthly_source = pd.read_csv(TRADE_AREA_MONTHLY_PATH, encoding="utf-8-sig")
    monthly_source["normalized_uptae"] = monthly_source[COL["uptae"]].map(normalize_uptae)
    source_merged = monthly_source.merge(
        bridge[[COL["license_category"], "normalized_uptae", "localdata_proxy_candidate_allowed"]],
        on=[COL["license_category"], "normalized_uptae"],
        how="left",
        validate="many_to_one",
    )
    expected_open = int(source_merged.loc[source_merged["localdata_proxy_candidate_allowed"].fillna(False), COL["open_count"]].sum())
    expected_close = int(source_merged.loc[source_merged["localdata_proxy_candidate_allowed"].fillna(False), COL["close_count"]].sum())
    observed_open = int(monthly["인허가건수"].sum())
    observed_close = int(monthly["폐업건수"].sum())
    add_validation(
        validations,
        "45-V05",
        "서비스업종 후보 월별 집계 합계 보존",
        f"open={observed_open}, close={observed_close}",
        f"open={expected_open}, close={expected_close}",
        observed_open == expected_open and observed_close == expected_close,
        "bridge 후보로 인정한 업태의 개폐업 이벤트는 월별 후보 집계에서 누락되거나 증폭되면 안 된다.",
    )
    dup_month = int(
        monthly.duplicated(
            [
                COL["trade_area_code"],
                "candidate_서비스_업종_코드",
                "mapping_status",
                "mapping_review_required",
                COL["month"],
            ]
        ).sum()
    )
    add_validation(
        validations,
        "45-V06",
        "월별 후보 grain 중복 금지",
        dup_month,
        0,
        dup_month == 0,
        "상권×서비스업종×월 후보가 중복되면 개폐업 이벤트가 중복 집계된다.",
    )
    dup_quarter = int(
        quarter.duplicated(
            [
                COL["trade_area_code"],
                "candidate_서비스_업종_코드",
                "mapping_status",
                "mapping_review_required",
                "기준_년분기_코드",
            ]
        ).sum()
    )
    add_validation(
        validations,
        "45-V07",
        "분기 후보 grain 중복 금지",
        dup_quarter,
        0,
        dup_quarter == 0,
        "알고리즘은 분기 단위이므로 상권×서비스업종×분기 후보가 1행이어야 한다.",
    )
    review_rows = int(bridge["mapping_review_required"].sum())
    add_validation(
        validations,
        "45-V08",
        "수동검토 업태 보존",
        review_rows,
        ">0",
        review_rows > 0,
        "LocalData 업태를 모두 강매칭하면 모호 업태가 점수 근거로 과장된다.",
    )
    hold_rows = int(bridge["bridge_use_status"].eq("hold_not_aggregated").sum())
    add_validation(
        validations,
        "45-V09",
        "미매핑/보류 업태 명시 보존",
        hold_rows,
        ">0",
        hold_rows > 0,
        "사용하지 않는 업태도 삭제하지 않고 보류 사유를 남겨 다음 수동검토 대상으로 둔다.",
    )
    used_raw_rows = int(bridge.loc[bridge["localdata_proxy_candidate_allowed"], "observed_raw_rows"].sum())
    total_raw_rows = int(bridge["observed_raw_rows"].sum())
    add_validation(
        validations,
        "45-V10",
        "원천 전체 대비 후보 사용범위 기록",
        f"{used_raw_rows}/{total_raw_rows}",
        "부분사용+보류사유",
        0 < used_raw_rows < total_raw_rows,
        "모든 LocalData를 억지로 쓰지 않고, 후보와 보류를 함께 기록해야 한다.",
    )
    validation_count_before = len(validations)
    add_validation(
        validations,
        "45-V11",
        "비기계적 규칙 검증 5개 이상",
        validation_count_before,
        ">=5",
        validation_count_before >= 5,
        "파일 존재 여부가 아니라 업태 매핑, 직접점수 금지, 모호 업태 보류, grain 보존을 검증한다.",
    )

    validation_df = pd.DataFrame(validations)
    summary = {
        "validation_number": 45,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "bridge_version": BRIDGE_VERSION,
        "observed_raw_rows": int(len(raw)),
        "spatial_candidate_raw_rows": int(len(usable_raw)),
        "bridge_rows": int(len(bridge)),
        "mapping_status_counts": bridge["mapping_status"].value_counts(dropna=False).to_dict(),
        "candidate_proxy_raw_rows": int(bridge.loc[bridge["localdata_proxy_candidate_allowed"], "observed_raw_rows"].sum()),
        "hold_raw_rows": int(bridge.loc[~bridge["localdata_proxy_candidate_allowed"], "observed_raw_rows"].sum()),
        "monthly_candidate_rows": int(len(monthly)),
        "quarter_candidate_rows": int(len(quarter)),
        "validation_pass_count": int((validation_df["result"] == "PASS").sum()),
        "validation_fail_count": int((validation_df["result"] == "FAIL").sum()),
        "decision": "PASS" if (validation_df["result"] == "FAIL").sum() == 0 else "FAIL",
        "next_validation_number": 46,
    }
    return validation_df, summary


def write_markdown(bridge: pd.DataFrame, validations: pd.DataFrame, summary: dict) -> None:
    status_counts = bridge["mapping_status"].value_counts(dropna=False).reset_index()
    status_counts.columns = ["mapping_status", "count"]

    top_bridge = bridge.sort_values("observed_raw_rows", ascending=False).head(30)

    lines = [
        "# 45. LocalData 식품 인허가 업태-서울 서비스업종 bridge 검증",
        "",
        "작성일: 2026-07-07",
        "",
        "## 목적",
        "",
        "LocalData 일반음식점/휴게음식점 인허가의 `업태명`을 서울 상권분석서비스 `서비스_업종_코드` 후보로 연결한다. 이 작업은 점수 직접 반영이 아니라, 개폐업·영업상태 보조 프록시를 업종 코드 체계로 옮기기 위한 bridge다.",
        "",
        "## 근거",
        "",
        "- 14번 LocalData silver 검증: 원천 680,725행 보존, 인허가/폐업 이벤트 보존, 업태 매핑 전 조건부 사용",
        "- 17번 LocalData 상권 공간매칭 검증: polygon 직접매칭만 조건부 프록시 가능, 좌표무효/밖 row 임의배정 금지",
        "- 40번 업종 선택 fallback 검증: 최종 알고리즘 조인키는 업종명이 아니라 `서비스_업종_코드`",
        "- 44번 원천 사용 커버리지 검증: LocalData는 업태 bridge 전 `프록시_후보보류`",
        "",
        "## 요약 판정",
        "",
        f"- bridge 버전: `{summary['bridge_version']}`",
        f"- LocalData 원천 row: {summary['observed_raw_rows']:,}",
        f"- 공간 후보 row: {summary['spatial_candidate_raw_rows']:,}",
        f"- bridge row: {summary['bridge_rows']:,}",
        f"- 후보 프록시 raw row: {summary['candidate_proxy_raw_rows']:,}",
        f"- 보류 raw row: {summary['hold_raw_rows']:,}",
        f"- 월별 후보 row: {summary['monthly_candidate_rows']:,}",
        f"- 분기 후보 row: {summary['quarter_candidate_rows']:,}",
        f"- 검증 PASS: {summary['validation_pass_count']:,}",
        f"- 검증 FAIL: {summary['validation_fail_count']:,}",
        f"- 최종 판정: **{summary['decision']}**",
        "",
        "## 매핑 상태별 개수",
        "",
        "| mapping_status | bridge row |",
        "|---|---:|",
    ]
    for _, row in status_counts.iterrows():
        lines.append(f"| {row['mapping_status']} | {int(row['count']):,} |")

    lines.extend(
        [
            "",
            "## 고빈도 업태 매핑 결과",
            "",
            "| license_category | 업태명 | 후보 서비스업종 | 상태 | 검토필요 | 원천 row | 사유 |",
            "|---|---|---|---|---:|---:|---|",
        ]
    )
    for _, row in top_bridge.iterrows():
        target = f"{row['candidate_서비스_업종_코드']} {row['candidate_서비스_업종_코드_명']}".strip()
        if not target:
            target = "(보류)"
        lines.append(
            f"| {row['license_category']} | {row['업태명']} | {target} | {row['mapping_status']} | {row['mapping_review_required']} | {int(row['observed_raw_rows']):,} | {str(row['mapping_reason_ko']).replace('|', '/')} |"
        )

    lines.extend(
        [
            "",
            "## 검증 결과",
            "",
            "| id | 검증 | 결과 | 관측 | 기대 | 이유 |",
            "|---|---|---|---|---|---|",
        ]
    )
    for _, row in validations.iterrows():
        lines.append(
            f"| {row['validation_id']} | {row['validation_name']} | {row['result']} | {row['observed']} | {row['expected']} | {str(row['reason_ko']).replace('|', '/')} |"
        )

    lines.extend(
        [
            "",
            "## 다음 단계",
            "",
            "1. 이 후보를 곧바로 현재입지 점수에 넣지 않는다.",
            "2. `auto_strong`과 `auto_review`를 분리한 상태로 기존 경쟁/성장 축 백데이터에 붙여 개선 여부를 검증한다.",
            "3. `hold_not_aggregated` 업태는 수동 매핑표가 생기기 전까지 점수 후보 집계에서 제외한다.",
            "4. 월별 후보는 추세/이벤트 분석용, 분기 후보는 백테스트 결합용으로만 쓴다.",
            "5. 리포트 문구는 '인허가 개폐업 프록시 후보'로 제한하고 성공확률·생존확률·매출 보장 표현을 금지한다.",
            "",
            "## 산출물",
            "",
            "- `datacorpus/_silver/silver_localdata_food_license_uptae_service_bridge.csv`",
            "- `datacorpus/_silver/silver_localdata_food_license_trade_area_service_monthly_candidate.csv`",
            "- `datacorpus/_silver/silver_localdata_food_license_trade_area_service_quarter_candidate.csv`",
            "- `datacorpus/_rule_validation/45_localdata_food_industry_bridge_validation.csv`",
            "- `datacorpus/_rule_validation/45_localdata_food_industry_bridge_summary.json`",
        ]
    )
    MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    SILVER.mkdir(parents=True, exist_ok=True)
    RULE_VALIDATION.mkdir(parents=True, exist_ok=True)
    RESEARCH_RULE_VALIDATION.mkdir(parents=True, exist_ok=True)

    bridge = build_bridge()
    monthly = build_monthly_candidate(bridge)
    quarter = build_quarter_candidate(monthly)
    validations, summary = build_validations(bridge, monthly, quarter)

    bridge.to_csv(BRIDGE_PATH, index=False, encoding="utf-8-sig")
    monthly.to_csv(MONTHLY_CANDIDATE_PATH, index=False, encoding="utf-8-sig")
    quarter.to_csv(QUARTER_CANDIDATE_PATH, index=False, encoding="utf-8-sig")
    validations.to_csv(VALIDATION_PATH, index=False, encoding="utf-8-sig")
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(bridge, validations, summary)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary["decision"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
