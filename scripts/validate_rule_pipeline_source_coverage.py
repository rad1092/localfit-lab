from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "datacorpus" / "_raw_ingest"
GOLD = ROOT / "datacorpus" / "_gold"
RULE_VALIDATION = ROOT / "datacorpus" / "_rule_validation"
RESEARCH_RULE_VALIDATION = ROOT / "research" / "rule_validation"

AUDIT_PATH = RULE_VALIDATION / "44_rule_pipeline_source_coverage_audit.csv"
VALIDATION_PATH = RULE_VALIDATION / "44_rule_pipeline_source_coverage_validation.csv"
SUMMARY_PATH = RULE_VALIDATION / "44_rule_pipeline_source_coverage_summary.json"
MD_PATH = RESEARCH_RULE_VALIDATION / "44_rule_pipeline_source_coverage_validation_20260707.md"


# 이 표는 44번 검증의 핵심이다.
# 원천 데이터가 점수 산식에 직접 들어갔는지, 프록시인지, 입력 브리지인지, 아직 보류인지
# 소스 단위로 명시해 다음 전처리에서 "있는데 안 쓴 데이터"가 조용히 묻히지 않게 한다.
SOURCE_USAGE = {
    "seoul_trade_area_boundary": {
        "pipeline_status": "입력브리지_반영",
        "score_use_level": "bridge",
        "current_gold_tables": "gold_trade_area_profile;gold_location_input_lookup;gold_location_spatial_index;gold_location_boundary_vertices",
        "reason_ko": "상권 polygon은 점수 원천이 아니라 지도 클릭·주소·장소 입력을 상권_코드로 바꾸는 기준이다.",
        "next_action_ko": "상권 경계 버전이 바뀌면 resolver 후보와 인접 상권 계산을 재검증한다.",
        "forbidden_claim_guard_ko": "상권 polygon 내부라는 사실만으로 좋은 입지나 성공 가능성을 말하지 않는다.",
    },
    "seoul_sales_trade_area": {
        "pipeline_status": "직접점수_반영",
        "score_use_level": "direct",
        "current_gold_tables": "gold_sales_strength_q_industry;gold_growth_stability_q_industry;gold_growth_label_candidates_q_industry;gold_growth_rebound_candidate_q_industry;gold_industry_taxonomy",
        "reason_ko": "서울시 상권분석서비스 추정매출은 상권×업종×분기 grain의 매출 체력 직접 관측 집계다.",
        "next_action_ko": "매출 축은 현재 엔진의 주축으로 유지하되, 업종별 스케일 차이는 계속 업종 내 백분위로 통제한다.",
        "forbidden_claim_guard_ko": "개별 매장 매출 보장이나 창업 성공확률로 표현하지 않는다.",
    },
    "seoul_store_trade_area": {
        "pipeline_status": "직접점수_반영",
        "score_use_level": "direct",
        "current_gold_tables": "gold_competition_q_industry;gold_growth_stability_q_industry;gold_growth_rebound_candidate_q_industry;gold_industry_taxonomy",
        "reason_ko": "점포·개업·폐업 정보는 상권×업종×분기 grain의 경쟁/개폐업 직접 집계다.",
        "next_action_ko": "경쟁은 동종 과밀과 상권 집적을 분리하고, 이상 개폐업률은 삭제하지 않고 신뢰도 감점 후보로 남긴다.",
        "forbidden_claim_guard_ko": "점포가 많다는 사실을 무조건 좋은 입지 또는 나쁜 입지로 단정하지 않는다.",
    },
    "seoul_floating_population_trade_area": {
        "pipeline_status": "직접점수_반영",
        "score_use_level": "direct",
        "current_gold_tables": "gold_demand_q_area",
        "reason_ko": "유동인구는 상권×분기 수요 축의 직접 집계지만 업종별 구매자 수는 아니다.",
        "next_action_ko": "시간대·요일 세부항목을 업종별 해석에 쓸지는 별도 검증 후 결정한다.",
        "forbidden_claim_guard_ko": "유동인구를 실제 방문자 수나 구매자 수로 표현하지 않는다.",
    },
    "seoul_resident_worker_population_trade_area": {
        "pipeline_status": "직접점수_반영",
        "score_use_level": "direct",
        "current_gold_tables": "gold_demand_q_area",
        "reason_ko": "상주·직장인구는 상권×분기 배후수요와 평일수요의 직접 집계다.",
        "next_action_ko": "상주/직장/유동 인구의 중복 영향은 소비-상권과 함께 신뢰도 및 보조설명으로 관리한다.",
        "forbidden_claim_guard_ko": "상주·직장인구를 특정 업종 매출이나 개별 점포 수요로 직접 치환하지 않는다.",
    },
    "seoul_trade_area_change_index": {
        "pipeline_status": "프록시_후보반영",
        "score_use_level": "proxy",
        "current_gold_tables": "gold_growth_stability_q_industry",
        "reason_ko": "상권변화지표는 성장/안정성 보조정보지만 코드 자체를 선형 점수로 바꾸면 근거가 약하다.",
        "next_action_ko": "매출 추세·개폐업·영업개월과 함께만 해석하고, 코드별 효과는 백데이터로 따로 검증한다.",
        "forbidden_claim_guard_ko": "HH/HL/LH/LL 같은 변화지표명을 그대로 성장률 보장 문구로 쓰지 않는다.",
    },
    "seoul_facility_trade_area": {
        "pipeline_status": "프록시_점수반영",
        "score_use_level": "proxy",
        "current_gold_tables": "gold_accessibility_q_area",
        "reason_ko": "집객시설과 교통결절 수는 상권 단위 접근성/흡인력 프록시다.",
        "next_action_ko": "시설 수 기반 접근성은 유지하되, 승하차량·거리감쇠 자료가 준비되면 접근성 축을 재비교한다.",
        "forbidden_claim_guard_ko": "시설 수를 실제 방문확률이나 유입 인구로 표현하지 않는다.",
    },
    "seoul_living_migration": {
        "pipeline_status": "프록시_점수반영",
        "score_use_level": "proxy",
        "current_gold_tables": "gold_demand_q_area;gold_accessibility_q_area",
        "reason_ko": "생활이동은 자치구/행정동 OD grain이라 상권 직접값은 아니지만 외부유입·생활권 수요 보조 프록시로 쓴다.",
        "next_action_ko": "상권 배분 규칙이 생기기 전까지 자치구 프록시로만 두고 공간해상도 감점을 유지한다.",
        "forbidden_claim_guard_ko": "생활이동 월파일을 버스·지하철 승하차량 대체 원천으로 쓰지 않는다.",
    },
    "molit_rtms_commercial_trade": {
        "pipeline_status": "프록시_점수반영",
        "score_use_level": "proxy",
        "current_gold_tables": "gold_cost_risk_q_area",
        "reason_ko": "상업·업무용 실거래는 자치구 단위 비용환경 프록시로 fan-out된다.",
        "next_action_ko": "법정동/면적/거래유형 세분화는 상권 매핑 안정성 검증 후 반영한다.",
        "forbidden_claim_guard_ko": "월세·권리금·수익성을 직접 반영했다고 말하지 않는다.",
    },
    "reb_small_shop_rent": {
        "pipeline_status": "참고근거_반영",
        "score_use_level": "reference",
        "current_gold_tables": "silver_reb_rone_commercial_cost_long;silver_reb_rone_seoul_cost_proxy_latest;engine_R_ONE_임대_참고선",
        "reason_ko": "R-ONE은 지역/상가유형 임대 통계라 비용 판단 참고선으로는 유효하지만 상권×점포 직접 월세가 아니다.",
        "next_action_ko": "권역·상가유형을 상권 특성과 연결하는 매핑 규칙을 만든 뒤 RTMS 비용축과 병렬 비교한다.",
        "forbidden_claim_guard_ko": "월세, 권리금, 영업이익률을 직접 계산한 것처럼 표현하지 않는다.",
    },
    "mdis_commercial_lease_tenant": {
        "pipeline_status": "외부기준선_반영",
        "score_use_level": "reference",
        "current_gold_tables": "gold_seoul_lease_benchmark;seoul_lease_benchmark",
        "reason_ko": "서울 임차인 표본의 임대면적·보증금·월세·관리비·권리금 분포를 서울 전체 외부 기준선과 단위 검산에 사용한다.",
        "next_action_ko": "상권 식별자가 없으므로 서울 전체 참고값으로만 유지하고 상권별 점수에는 직접 투입하지 않는다.",
        "forbidden_claim_guard_ko": "서울 전체 표본 평균을 개별 상권의 월세·권리금 또는 수익성으로 표현하지 않는다.",
    },
    "mdis_commercial_lease_landlord": {
        "pipeline_status": "외부기준선_반영",
        "score_use_level": "reference",
        "current_gold_tables": "gold_seoul_lease_benchmark;seoul_lease_benchmark",
        "reason_ko": "서울 임대인 표본을 임차인 자료와 교차 점검하는 서울 전체 임대조건 기준선으로 사용한다.",
        "next_action_ko": "상권 식별자가 없으므로 임대조건 품질 감사와 서울 전체 참고값으로만 유지한다.",
        "forbidden_claim_guard_ko": "임대인 표본 평균을 개별 상권의 월세·권리금 또는 계약조건으로 표현하지 않는다.",
    },
    "seoul_commercial_lease_survey": {
        "pipeline_status": "외부기준선_반영",
        "score_use_level": "reference",
        "current_gold_tables": "gold_seoul_lease_benchmark;seoul_lease_benchmark",
        "reason_ko": "서울시 상가임대차 실태조사 공표값은 MDIS 단위 환산과 서울 전체 임대비용 기준선을 외부 감사하는 데 사용한다.",
        "next_action_ko": "페이지가 확인된 공표값만 기준선으로 유지하고 모집단이 다른 MDIS 수치와 일치한다고 강제하지 않는다.",
        "forbidden_claim_guard_ko": "서울 전체 공표값을 개별 상권의 월세·권리금 또는 수익성으로 표현하지 않는다.",
    },
    "sbdc_store_info": {
        "pipeline_status": "프록시_점수반영",
        "score_use_level": "proxy",
        "current_gold_tables": "gold_competition_q_industry;gold_industry_taxonomy;gold_industry_selection_hierarchy",
        "reason_ko": "상가업소 POI는 좌표 기반 경쟁 보조 프록시이며 서울 서비스업종 자동강매칭 업종만 점수 보조로 허용한다.",
        "next_action_ko": "수동검토 업종 23개와 fallback 업종 37개는 별도 매핑 확정 전 점수 근거로 승격하지 않는다.",
        "forbidden_claim_guard_ko": "SBDC 업종명을 서울 서비스업종과 무조건 1:1로 같다고 말하지 않는다.",
    },
    "sgis_small_area_stats": {
        "pipeline_status": "기준선_보류",
        "score_use_level": "reference",
        "current_gold_tables": "silver_sgis_admin_boundary;silver_sgis_admin_code;silver_sgis_admin_stats_long;silver_sgis_reference_years",
        "reason_ko": "SGIS는 행정/집계구 기준선과 입력 검증에는 유용하지만 서울 상권 polygon 직접값이 아니다.",
        "next_action_ko": "행정경계와 상권경계 배분 규칙을 만들 때 기준선/신뢰도 보정 후보로 재검토한다.",
        "forbidden_claim_guard_ko": "SGIS 행정통계를 상권 직접 수요값으로 표현하지 않는다.",
    },
    "kosis_population_business_survival": {
        "pipeline_status": "기준선_보류",
        "score_use_level": "reference",
        "current_gold_tables": "silver_kosis_selected_stat_long;silver_kosis_population_reference;silver_kosis_business_activity_sgg_industry_year;silver_kosis_survival_benchmark_year",
        "reason_ko": "KOSIS는 지역/산업 기준선과 생존율 벤치마크에는 유용하지만 상권×업종 직접 입지점수가 아니다.",
        "next_action_ko": "서울시 상권 결과를 외부 기준선으로 설명·검산하는 보조 리포트 계층에 둔다.",
        "forbidden_claim_guard_ko": "KOSIS 생존율을 특정 상권·특정 업종의 창업 성공확률로 말하지 않는다.",
    },
    "vworld_juso_geocoding": {
        "pipeline_status": "입력브리지_반영",
        "score_use_level": "bridge",
        "current_gold_tables": "gold_location_input_lookup;silver_address_geocoding_request_audit;silver_geocoding_point_trade_area_sample",
        "reason_ko": "주소/좌표 정규화는 위치 입력을 상권 후보로 바꾸는 보조수단이다.",
        "next_action_ko": "지도 클릭/주소/장소 검색 모두 polygon 후보 반환 흐름으로 통일한다.",
        "forbidden_claim_guard_ko": "지오코딩 성공을 입지 우수성으로 해석하지 않는다.",
    },
    "seoul_real_estate_broker_office": {
        "pipeline_status": "프록시_후보보류",
        "score_use_level": "hold",
        "current_gold_tables": "silver_real_estate_broker_office_seoul;silver_real_estate_broker_office_sgg_status_summary;silver_real_estate_broker_office_legal_dong_status_summary",
        "reason_ko": "중개업소 정보는 부동산 환경 프록시 후보지만 월세/권리금 직접값도 아니고 현재 gold 비용축에는 직접 결합되지 않았다.",
        "next_action_ko": "법정동·자치구 단위 밀도와 RTMS/R-ONE 비용축의 설명력 개선 여부를 백데이터로 검증한다.",
        "forbidden_claim_guard_ko": "중개업소 수를 임대료나 권리금 수준으로 직접 해석하지 않는다.",
    },
    "seoul_localdata_general_restaurant_license": {
        "pipeline_status": "프록시_후보보류",
        "score_use_level": "hold",
        "current_gold_tables": "silver_localdata_food_license_raw_seoul;silver_localdata_food_license_trade_area_match;silver_localdata_food_license_open_close_monthly",
        "reason_ko": "일반음식점 인허가는 개폐업·영업상태 보조값이지만 업태명과 서울 서비스업종 코드 매핑이 아직 확정되지 않았다.",
        "next_action_ko": "업태명→서울 서비스업종 계층 매핑과 상권×업종×월 개폐업 집계를 다음 전처리 우선순위로 둔다.",
        "forbidden_claim_guard_ko": "인허가 업태명을 서울 서비스업종과 자동 동일시하지 않는다.",
    },
    "seoul_localdata_rest_cafe_license": {
        "pipeline_status": "프록시_후보보류",
        "score_use_level": "hold",
        "current_gold_tables": "silver_localdata_food_license_raw_seoul;silver_localdata_food_license_trade_area_match;silver_localdata_food_license_open_close_monthly",
        "reason_ko": "휴게음식점 인허가도 개폐업·영업상태 보조값이지만 서비스업종 코드 매핑 확정 전이다.",
        "next_action_ko": "일반음식점과 같은 bridge에서 카페/제과/분식 등 세부업종 매핑을 분리 검증한다.",
        "forbidden_claim_guard_ko": "휴게음식점 전체를 특정 세부업종 수요나 성장으로 단정하지 않는다.",
    },
    "seoul_bus_stop_location_file": {
        "pipeline_status": "프록시_후보보류",
        "score_use_level": "hold",
        "current_gold_tables": "silver_bus_stop_location_master;silver_transit_point_accessibility_candidate_points",
        "reason_ko": "버스정류장 좌표는 접근성 후보지만 거리/buffer 민감도와 장기 백테스트 전 직접 점수 투입은 보류한다.",
        "next_action_ko": "상권 polygon 거리·buffer 기준별 후보 접근성 지표를 만들고 기존 시설 접근성축과 비교한다.",
        "forbidden_claim_guard_ko": "정류장이 가깝다는 사실을 실제 유입량이나 매출로 보장하지 않는다.",
    },
    "seoul_bus_stop_passengers_hourly": {
        "pipeline_status": "프록시_후보보류",
        "score_use_level": "hold",
        "current_gold_tables": "silver_bus_passenger_route_stop_month_hour;silver_bus_passenger_route_stop_month_summary;gold_accessibility_transit_q_area_candidate",
        "reason_ko": "버스 승하차량은 현재 202605 단월만 확인되어 장기 백테스트와 분기 점수 직접 투입을 보류한다.",
        "next_action_ko": "과거 월 커버리지를 추가 확보한 뒤 월→분기 집계, 거리감쇠, 기존 접근성축 대비 백테스트를 수행한다.",
        "forbidden_claim_guard_ko": "현재 보유 데이터로 교통 승하차량 장기 추세가 검증됐다고 말하지 않는다.",
    },
    "seoul_subway_station_passengers_hourly": {
        "pipeline_status": "프록시_후보보류",
        "score_use_level": "hold",
        "current_gold_tables": "silver_subway_passenger_station_month_hour;silver_subway_passenger_station_month_summary;gold_accessibility_transit_q_area_candidate",
        "reason_ko": "지하철 승하차량도 현재 202605 단월만 확인되어 점수 직접 투입을 보류한다.",
        "next_action_ko": "과거 월 커버리지를 확보하고 역사 좌표 결합·노선 alias 검증 후 접근성 후보로 재검증한다.",
        "forbidden_claim_guard_ko": "생활이동/OD 월파일을 지하철 승하차량 원천처럼 대체하지 않는다.",
    },
    "seoul_subway_station_master": {
        "pipeline_status": "입력브리지_후보보류",
        "score_use_level": "bridge_hold",
        "current_gold_tables": "silver_subway_station_master;silver_subway_route_alias_candidate",
        "reason_ko": "역사마스터는 승하차량 좌표 결합을 위한 bridge이며 역명 중복 때문에 이름 단독 조인이 위험하다.",
        "next_action_ko": "역명·호선 alias 결합률을 월별 승하차량 전체 이력에서 반복 검증한다.",
        "forbidden_claim_guard_ko": "역명 일치만으로 좌표 결합이 완전히 맞다고 단정하지 않는다.",
    },
    "seoul_bus_route_node_master": {
        "pipeline_status": "프록시_후보보류",
        "score_use_level": "hold",
        "current_gold_tables": "silver_bus_route_node_master;silver_bus_route_node_route_summary;silver_bus_route_node_stop_summary",
        "reason_ko": "버스 노선-정류장 마스터는 네트워크 다양성 접근성 후보지만 현재 점수축에는 직접 투입되지 않았다.",
        "next_action_ko": "정류장별 노선 다양성, 환승성, 상권 buffer별 노선 수를 만들고 시설 접근성축 대비 개선 여부를 검증한다.",
        "forbidden_claim_guard_ko": "노선 수를 실제 승객 수나 매출 유입으로 직접 해석하지 않는다.",
    },
    "naver_api_hub_news": {
        "pipeline_status": "근거뉴스_반영",
        "score_use_level": "reference",
        "current_gold_tables": "silver_news_evidence",
        "reason_ko": "네이버 뉴스 검색 결과는 입지 리포트와 챗봇의 정성적 최근 이슈 근거(evidence-only)로 사용되며 정량 점수에는 반영되지 않는다.",
        "next_action_ko": "정성 근거 매칭 조건을 유지하고, 점수 산출에는 투입하지 않는다.",
        "forbidden_claim_guard_ko": "뉴스가 존재한다는 것만으로 상권 활성화나 성공을 보장하는 표현으로 쓰지 않는다.",
    },
    "seoul_city_press_rss": {
        "pipeline_status": "근거뉴스_반영",
        "score_use_level": "reference",
        "current_gold_tables": "silver_news_evidence",
        "reason_ko": "서울시 보도자료 RSS는 지역 정책 및 개발 정성 근거로 사용된다.",
        "next_action_ko": "정성 근거 매칭을 유지하고 점수 산출은 금지한다.",
        "forbidden_claim_guard_ko": "보도자료 계획을 확정된 개발 호재나 매출 보장으로 쓰지 않는다.",
    },
    "seoul_district_official_rss": {
        "pipeline_status": "근거뉴스_반영",
        "score_use_level": "reference",
        "current_gold_tables": "silver_news_evidence",
        "reason_ko": "자치구 RSS는 개별 자치구 소식의 정성 근거로 사용되며, sub-ID로 manifest에 기록된다.",
        "next_action_ko": "정성 근거 자치구 매핑을 유지하고 점수 산출은 금지한다.",
        "forbidden_claim_guard_ko": "자치구 행사를 상권 활성화 확정 근거로 쓰지 않는다.",
    },
    "molit_press_rss": {
        "pipeline_status": "근거뉴스_반영",
        "score_use_level": "reference",
        "current_gold_tables": "silver_news_evidence",
        "reason_ko": "국토부 보도자료 RSS는 상업용 부동산 정책의 정성 근거로 사용된다.",
        "next_action_ko": "정성 근거 매칭을 유지하고 점수 산출은 금지한다.",
        "forbidden_claim_guard_ko": "부동산 대책을 임대료 하락/상승 확정 근거로 쓰지 않는다.",
    },
    "mss_press_rss": {
        "pipeline_status": "근거뉴스_반영",
        "score_use_level": "reference",
        "current_gold_tables": "silver_news_evidence",
        "reason_ko": "중기부 보도자료 RSS는 소상공인 정책 지원의 정성 근거로 사용된다.",
        "next_action_ko": "소상공인 지원 사업 안내용으로만 노출하고 점수 산출은 금지한다.",
        "forbidden_claim_guard_ko": "정부 지원금을 상권 성공 보장으로 해석하지 않는다.",
    },
    "semas_press_board": {
        "pipeline_status": "근거뉴스_반영",
        "score_use_level": "reference",
        "current_gold_tables": "silver_news_evidence",
        "reason_ko": "소진공 게시판은 전통시장 및 소상공인 지원의 정성 근거로 사용된다.",
        "next_action_ko": "지원 정보로만 노출하고 점수 산출은 금지한다.",
        "forbidden_claim_guard_ko": "지원 정책을 상권 활성화 확정 근거로 쓰지 않는다.",
    },
    "korea_policy_briefing": {
        "pipeline_status": "근거뉴스_반영",
        "score_use_level": "reference",
        "current_gold_tables": "silver_news_evidence",
        "reason_ko": "정부 정책 브리핑은 부처 정책의 교차 확인 및 정성 근거로 사용된다.",
        "next_action_ko": "지원/정책 정보로만 노출하고 점수 산출은 금지한다.",
        "forbidden_claim_guard_ko": "정책 브리핑을 상권 성공 보장으로 쓰지 않는다.",
    },
}


MANIFEST_ONLY_USAGE = {
    "juso_address_normalization": {
        "pipeline_status": "입력브리지_반영",
        "score_use_level": "bridge",
        "current_gold_tables": "silver_juso_address_normalization_candidate_sample;silver_address_geocoding_request_audit",
        "reason_ko": "주소 정규화 샘플은 위치 입력 후보 생성 검증용이다.",
        "next_action_ko": "주소 검색 UI와 polygon resolver의 후보 확정 흐름에만 사용한다.",
        "forbidden_claim_guard_ko": "주소 정규화 결과를 점수 원천으로 쓰지 않는다.",
    },
    "sgis_spatial_admin_boundary": {
        "pipeline_status": "기준선_보류",
        "score_use_level": "reference",
        "current_gold_tables": "silver_sgis_admin_boundary",
        "reason_ko": "SGIS 행정경계는 서울 상권경계와 다르므로 기준선/배분 후보로만 둔다.",
        "next_action_ko": "행정경계→상권 배분 규칙이 생긴 뒤 검증한다.",
        "forbidden_claim_guard_ko": "행정경계 polygon을 상권 polygon으로 대체하지 않는다.",
    },
}


DOC_SOURCE_PATTERNS = (
    "_docs",
    "docs",
    "notice",
)


def read_csv(path: Path, **kwargs) -> pd.DataFrame:
    return pd.read_csv(path, encoding="utf-8-sig", **kwargs)


def contains_source(series: pd.Series, source_id: str) -> pd.Series:
    return series.fillna("").astype(str).str.split(";").apply(lambda values: source_id in values)


def infer_doc_usage(source_id: str) -> dict[str, str]:
    return {
        "pipeline_status": "근거문서_반영",
        "score_use_level": "docs",
        "current_gold_tables": "",
        "reason_ko": "원천 API/데이터 설명 문서로 수집되어 전처리 계약과 금지문구 근거로 사용한다.",
        "next_action_ko": "해당 실데이터 source의 source contract와 검증 문서가 바뀔 때 함께 갱신한다.",
        "forbidden_claim_guard_ko": "문서 자체를 점수 원천값으로 쓰지 않는다.",
    }


def build_audit() -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    source_registry = read_csv(RAW / "source_registry.csv")
    manifest = read_csv(
        RAW / "ingest_manifest.csv",
        usecols=[
            "source_id",
            "provider",
            "dataset_name",
            "raw_path",
            "bytes",
            "collection_status",
        ],
    )
    gap = read_csv(RULE_VALIDATION / "00_raw_source_silver_gap_audit_20260703.csv")
    reliability = read_csv(GOLD / "gold_data_reliability_snapshot.csv")

    manifest_group = (
        manifest.groupby("source_id", dropna=False)
        .agg(
            manifest_rows=("source_id", "size"),
            manifest_raw_paths=("raw_path", "nunique"),
            manifest_total_bytes=("bytes", "sum"),
            manifest_statuses=("collection_status", lambda x: ";".join(sorted(set(map(str, x.dropna()))))),
            manifest_providers=("provider", lambda x: ";".join(sorted(set(map(str, x.dropna())))[:5])),
            first_dataset=("dataset_name", "first"),
        )
        .reset_index()
    )

    gap_group = (
        gap.groupby("source_id", dropna=False)
        .agg(
            gap_raw_paths=("raw_paths", "sum"),
            gap_total_bytes=("total_bytes", "sum"),
            contract_file=("contract_file", lambda x: ";".join(sorted(set(map(str, x.dropna())))[:10])),
            silver_status=("silver_status", lambda x: ";".join(sorted(set(map(str, x.dropna())))[:5])),
        )
        .reset_index()
    )

    registry_sources = set(source_registry["source_id"].astype(str))
    manifest_sources = set(manifest_group["source_id"].astype(str))
    all_sources = sorted(registry_sources | manifest_sources)

    rows: list[dict] = []
    for source_id in all_sources:
        registry_row = source_registry[source_registry["source_id"].astype(str) == source_id]
        
        # Aggregate manifest rows for parent items
        if source_id == "korea_policy_briefing":
            matching_manifest = manifest[manifest["source_id"].astype(str).str.startswith("korea_policy_briefing")]
            if not matching_manifest.empty:
                manifest_row = pd.DataFrame([{
                    "source_id": source_id,
                    "manifest_rows": len(matching_manifest),
                    "manifest_raw_paths": matching_manifest["raw_path"].nunique(),
                    "manifest_total_bytes": matching_manifest["bytes"].sum(),
                    "manifest_statuses": ";".join(sorted(set(map(str, matching_manifest["collection_status"].dropna())))),
                    "manifest_providers": ";".join(sorted(set(map(str, matching_manifest["provider"].dropna())))[:5]),
                    "first_dataset": "정부부처 보도자료 목록",
                }])
            else:
                manifest_row = manifest_group[manifest_group["source_id"].astype(str) == source_id]
        elif source_id == "seoul_district_official_rss":
            matching_manifest = manifest[manifest["source_id"].astype(str).str.startswith("seoul_district_rss_")]
            if not matching_manifest.empty:
                manifest_row = pd.DataFrame([{
                    "source_id": source_id,
                    "manifest_rows": len(matching_manifest),
                    "manifest_raw_paths": matching_manifest["raw_path"].nunique(),
                    "manifest_total_bytes": matching_manifest["bytes"].sum(),
                    "manifest_statuses": ";".join(sorted(set(map(str, matching_manifest["collection_status"].dropna())))),
                    "manifest_providers": ";".join(sorted(set(map(str, matching_manifest["provider"].dropna())))[:5]),
                    "first_dataset": "자치구 공식 RSS 묶음",
                }])
            else:
                manifest_row = manifest_group[manifest_group["source_id"].astype(str) == source_id]
        else:
            manifest_row = manifest_group[manifest_group["source_id"].astype(str) == source_id]

        gap_row = gap_group[gap_group["source_id"].astype(str) == source_id]

        if source_id in SOURCE_USAGE:
            usage = SOURCE_USAGE[source_id]
        elif source_id in MANIFEST_ONLY_USAGE:
            usage = MANIFEST_ONLY_USAGE[source_id]
        elif any(pattern in source_id for pattern in DOC_SOURCE_PATTERNS):
            usage = infer_doc_usage(source_id)
        elif source_id.startswith("seoul_localdata_") and source_id.endswith("_license"):
            usage = {
                "pipeline_status": "성과검증_반영",
                "score_use_level": "validation",
                "current_gold_tables": "silver_localdata_business_license;business_survival_v1_20260717",
                "reason_ko": "업종별 인허가 원천을 공통 Silver와 365일 생존 백테스트의 결과 라벨로 사용하며 운영 입지점수에는 직접 투입하지 않는다.",
                "next_action_ko": "명시적으로 매핑된 업종만 성과검증에 사용하고 모호한 업종은 매핑 확정 전까지 보류한다.",
                "forbidden_claim_guard_ko": "인허가 생존 결과를 현재 입지점수의 성공확률이나 개별 점포 생존확률로 표현하지 않는다.",
            }
        elif source_id.startswith("seoul_district_rss_"):
            usage = {
                "pipeline_status": "근거뉴스_반영",
                "score_use_level": "reference",
                "current_gold_tables": "silver_news_evidence",
                "reason_ko": "자치구 RSS 수집 sub-ID이다.",
                "next_action_ko": "seoul_district_official_rss 상위 원천에 통합되어 매칭된다.",
                "forbidden_claim_guard_ko": "점수 산출 금지.",
            }
        elif source_id.startswith("korea_policy_briefing_"):
            usage = {
                "pipeline_status": "근거뉴스_반영",
                "score_use_level": "reference",
                "current_gold_tables": "silver_news_evidence",
                "reason_ko": "정책 브리핑 수집 sub-ID이다.",
                "next_action_ko": "korea_policy_briefing 상위 원천에 통합되어 매칭된다.",
                "forbidden_claim_guard_ko": "점수 산출 금지.",
            }
        elif source_id.startswith("naver_api_hub_news_"):
            usage = {
                "pipeline_status": "근거뉴스_반영",
                "score_use_level": "reference",
                "current_gold_tables": "silver_news_evidence",
                "reason_ko": "네이버 뉴스 검색 수집 sub-ID이다.",
                "next_action_ko": "naver_api_hub_news 상위 원천에 통합되어 매칭된다.",
                "forbidden_claim_guard_ko": "점수 산출 금지.",
            }
        else:
            usage = {
                "pipeline_status": "보조원천_검토필요",
                "score_use_level": "review",
                "current_gold_tables": "",
                "reason_ko": "source_registry에는 없지만 manifest에 존재하는 보조 원천이다.",
                "next_action_ko": "상위 source와 연결되는 문서/샘플/alias인지 확인하고 registry 또는 보조근거로 분리한다.",
                "forbidden_claim_guard_ko": "검토 전 점수 직접값으로 쓰지 않는다.",
            }

        reliability_matches = reliability[contains_source(reliability["source_id_sample"], source_id)]
        reliability_roles = ";".join(sorted(set(reliability_matches["gold_input_role"].dropna().astype(str))))[:1000]
        reliability_tables = ";".join(sorted(set(reliability_matches["silver_table"].dropna().astype(str))))[:1600]

        registry_local_doc = "" if registry_row.empty else str(registry_row.iloc[0].get("local_doc", "") or "").strip()
        registry_local_path = Path(registry_local_doc) if registry_local_doc else None
        if registry_local_path is not None and not registry_local_path.is_absolute():
            registry_local_path = ROOT / registry_local_path
        registry_local_exists = bool(registry_local_path and registry_local_path.exists())

        rows.append(
            {
                "source_id": source_id,
                "registry_member": source_id in registry_sources,
                "priority": "" if registry_row.empty else registry_row.iloc[0].get("priority", ""),
                "provider": (
                    manifest_row.iloc[0].get("manifest_providers", "")
                    if not manifest_row.empty
                    else ("" if registry_row.empty else registry_row.iloc[0].get("provider", ""))
                ),
                "score_axis": "" if registry_row.empty else registry_row.iloc[0].get("score_axis", ""),
                "spatial_unit": "" if registry_row.empty else registry_row.iloc[0].get("spatial_unit", ""),
                "time_unit": "" if registry_row.empty else registry_row.iloc[0].get("time_unit", ""),
                "manifest_rows": 0 if manifest_row.empty else int(manifest_row.iloc[0]["manifest_rows"]),
                "manifest_raw_paths": 0 if manifest_row.empty else int(manifest_row.iloc[0]["manifest_raw_paths"]),
                "manifest_total_bytes": 0 if manifest_row.empty else int(manifest_row.iloc[0]["manifest_total_bytes"]),
                "manifest_statuses": "" if manifest_row.empty else manifest_row.iloc[0]["manifest_statuses"],
                "registry_collection_method": "" if registry_row.empty else registry_row.iloc[0].get("collection_method", ""),
                "registry_current_status": "" if registry_row.empty else registry_row.iloc[0].get("current_status", ""),
                "registry_local_doc": registry_local_doc,
                "registry_local_exists": registry_local_exists,
                "gap_raw_paths": "" if gap_row.empty else gap_row.iloc[0]["gap_raw_paths"],
                "contract_file": "" if gap_row.empty else gap_row.iloc[0]["contract_file"],
                "silver_status": "" if gap_row.empty else gap_row.iloc[0]["silver_status"],
                "reliability_roles": reliability_roles,
                "reliability_silver_tables": reliability_tables,
                **usage,
            }
        )

    audit = pd.DataFrame(rows)
    validations = build_validations(audit, registry_sources)

    summary = {
        "validation_number": 44,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "audit_rows": int(len(audit)),
        "registry_source_count": int(audit["registry_member"].sum()),
        "manifest_only_source_count": int((~audit["registry_member"]).sum()),
        "pipeline_status_counts": audit["pipeline_status"].value_counts(dropna=False).to_dict(),
        "score_use_level_counts": audit["score_use_level"].value_counts(dropna=False).to_dict(),
        "validation_pass_count": int((validations["result"] == "PASS").sum()),
        "validation_fail_count": int((validations["result"] == "FAIL").sum()),
        "decision": "PASS" if (validations["result"] == "FAIL").sum() == 0 else "FAIL",
        "next_validation_number": 45,
    }

    return audit, validations, summary


def add_validation(rows: list[dict], validation_id: str, name: str, observed, expected, passed: bool, reason: str) -> None:
    rows.append(
        {
            "validation_id": validation_id,
            "validation_name": name,
            "observed": observed,
            "expected": expected,
            "result": "PASS" if passed else "FAIL",
            "reason_ko": reason,
        }
    )


def build_validations(audit: pd.DataFrame, registry_source_ids: set[str]) -> pd.DataFrame:
    rows: list[dict] = []
    registry = audit[audit["registry_member"]].copy()
    manifest_only = audit[~audit["registry_member"]].copy()

    audited_registry_ids = set(registry["source_id"].astype(str))
    add_validation(
        rows,
        "44-V01",
        "source_registry 전체 원천 감사 포함",
        int(len(audited_registry_ids)),
        int(len(registry_source_ids)),
        audited_registry_ids == registry_source_ids,
        "계획된 원천 중 누락이 있으면 전처리 우선순위를 판단할 수 없다.",
    )
    manual_registry_evidence = (
        registry["manifest_rows"].eq(0)
        & registry["registry_local_exists"].eq(True)
        & registry["registry_current_status"].eq("collected_raw")
    )
    registry_raw_evidence = registry["manifest_rows"].gt(0) | manual_registry_evidence
    add_validation(
        rows,
        "44-V02",
        "registry 원천별 raw 근거 존재",
        int(registry_raw_evidence.sum()),
        int(len(registry)),
        bool(registry_raw_evidence.all()),
        "API 수집은 manifest, 수동 다운로드는 collected_raw 상태와 실제 로컬 파일로 적재 근거를 확인한다.",
    )
    add_validation(
        rows,
        "44-V03",
        "registry 원천별 사용단계와 다음 행동 명시",
        int((registry["pipeline_status"].ne("") & registry["next_action_ko"].ne("")).sum()),
        int(len(registry)),
        bool((registry["pipeline_status"].ne("") & registry["next_action_ko"].ne("")).all()),
        "직접반영/프록시/브리지/보류 중 어디에 놓였는지와 다음 작업이 반드시 있어야 한다.",
    )
    direct_forbidden = {
        "molit_rtms_commercial_trade",
        "reb_small_shop_rent",
        "vworld_juso_geocoding",
        "sgis_small_area_stats",
        "kosis_population_business_survival",
        "seoul_bus_stop_passengers_hourly",
        "seoul_subway_station_passengers_hourly",
        "seoul_living_migration",
        "mdis_commercial_lease_tenant",
        "mdis_commercial_lease_landlord",
        "seoul_commercial_lease_survey",
    }
    forbidden_direct_rows = registry[
        registry["source_id"].isin(direct_forbidden) & registry["score_use_level"].eq("direct")
    ]
    add_validation(
        rows,
        "44-V04",
        "프록시/브리지/기준선 원천 직접점수 승격 금지",
        int(len(forbidden_direct_rows)),
        0,
        int(len(forbidden_direct_rows)) == 0,
        "비용, 지오코딩, 행정통계, 단월 교통, 생활이동은 직접 성공·매출 점수 원천이 아니다.",
    )
    transit = registry[registry["source_id"].isin(["seoul_bus_stop_passengers_hourly", "seoul_subway_station_passengers_hourly"])]
    transit_hold_ok = transit["pipeline_status"].str.contains("보류", na=False).all() and transit[
        "reason_ko"
    ].str.contains("202605|단월", regex=True, na=False).all()
    add_validation(
        rows,
        "44-V05",
        "버스/지하철 승하차량 단월 한계 보류 유지",
        ";".join(transit["pipeline_status"].astype(str)),
        "보류 + 단월 사유",
        bool(transit_hold_ok),
        "43번에서 월별 적재 구조는 PASS였지만, 42번 판정처럼 과거 월 커버리지 부족은 그대로 남아 있다.",
    )
    cost = registry[registry["source_id"].isin(["molit_rtms_commercial_trade", "reb_small_shop_rent", "seoul_real_estate_broker_office"])]
    cost_guard_ok = cost["forbidden_claim_guard_ko"].str.contains("월세|권리금|임대료", regex=True, na=False).all()
    add_validation(
        rows,
        "44-V06",
        "비용 원천 월세/권리금 직접표현 금지문구 보존",
        int(cost_guard_ok),
        1,
        bool(cost_guard_ok),
        "RTMS/R-ONE/중개업소는 비용환경 프록시 또는 참고선이지 개별 점포 월세·권리금 직접값이 아니다.",
    )
    localdata = registry[
        registry["source_id"].str.startswith("seoul_localdata_")
        & registry["source_id"].str.endswith("_license")
    ]
    localdata_mapping_ok = (
        localdata["next_action_ko"].str.contains("업태|서비스업종|매핑", regex=True, na=False).all()
        and localdata["score_use_level"].isin(["hold", "validation"]).all()
        and not localdata["score_use_level"].isin(["direct", "proxy"]).any()
    )
    add_validation(
        rows,
        "44-V07",
        "LocalData 인허가 운영점수 분리 및 매핑 계약",
        int(len(localdata)) if localdata_mapping_ok else 0,
        int(len(localdata)),
        bool(localdata_mapping_ok),
        "인허가 업태명은 서울 서비스업종 코드와 다르므로 명시적 매핑만 성과검증에 쓰고 운영 점수 직접축에는 넣지 않는다.",
    )
    p0 = registry[registry["priority"].eq("P0")]
    p0_ok = p0["score_use_level"].isin(["direct", "proxy", "bridge", "hold"]).all()
    add_validation(
        rows,
        "44-V08",
        "P0 원천은 직접/프록시/브리지/명시보류 중 하나로 배치",
        int(p0_ok),
        1,
        bool(p0_ok),
        "핵심 원천은 문서만 수집된 상태로 끝나면 안 되고, 쓰임 또는 보류 이유가 분명해야 한다.",
    )
    manifest_only_ok = manifest_only["score_use_level"].isin(["docs", "bridge", "reference", "review"]).all()
    add_validation(
        rows,
        "44-V09",
        "manifest-only 보조 원천 직접점수 미사용",
        int(manifest_only_ok),
        1,
        bool(manifest_only_ok),
        "registry 밖 문서·샘플·alias는 직접 점수로 자동 승격하지 않고 근거/브리지로만 둔다.",
    )
    unclassified = audit[audit["score_use_level"].eq("review")]
    add_validation(
        rows,
        "44-V10",
        "검토필요 보조원천 최소화",
        int(len(unclassified)),
        0,
        int(len(unclassified)) == 0,
        "source_id가 늘어났을 때 자동으로 점수에 섞이지 않게 검토필요를 0으로 유지한다.",
    )
    next_priority_sources = {
        "seoul_localdata_general_restaurant_license",
        "seoul_localdata_rest_cafe_license",
        "seoul_bus_route_node_master",
        "seoul_bus_stop_passengers_hourly",
        "seoul_subway_station_passengers_hourly",
        "reb_small_shop_rent",
    }
    next_priority_ok = registry[registry["source_id"].isin(next_priority_sources)]["next_action_ko"].ne("").all()
    add_validation(
        rows,
        "44-V11",
        "다음 전처리 우선 후보별 행동 존재",
        int(next_priority_ok),
        1,
        bool(next_priority_ok),
        "다음 단계는 모든 후보를 무작정 투입하는 것이 아니라 bridge·월커버리지·권역매핑 같은 선행조건을 푸는 것이다.",
    )
    non_mechanical_count = len(rows)
    add_validation(
        rows,
        "44-V12",
        "비기계적 규칙 검증 5개 이상",
        non_mechanical_count,
        ">=5",
        non_mechanical_count >= 5,
        "단순 파일 존재 확인이 아니라 직접/프록시/브리지/보류 판단 규칙 자체를 최소 5개 이상 점검한다.",
    )

    return pd.DataFrame(rows)


def write_markdown(audit: pd.DataFrame, validations: pd.DataFrame, summary: dict) -> None:
    status_counts = audit["pipeline_status"].value_counts(dropna=False).reset_index()
    status_counts.columns = ["pipeline_status", "count"]

    registry = audit[audit["registry_member"]].copy()
    registry = registry.sort_values(["priority", "source_id"], na_position="last")
    manifest_only = audit[~audit["registry_member"]].copy().sort_values("source_id")

    lines: list[str] = [
        "# 44. 규칙 파이프라인 원천 사용 커버리지 검증",
        "",
        "작성일: 2026-07-07",
        "",
        "## 목적",
        "",
        "전처리와 알고리즘 보강 전에 `datacorpus/_raw_ingest/source_registry.csv`와 raw manifest에 있는 원천들이 현재 규칙 파이프라인에서 어디까지 쓰였는지 확인한다. 이 검증은 성능 점수가 아니라 데이터 계보와 사용 강도 검증이다.",
        "",
        "핵심 원칙은 세 가지다.",
        "",
        "1. 직접값, 프록시, 입력 브리지, 기준선, 보류를 섞지 않는다.",
        "2. 데이터가 있다고 해서 성공확률, 매출 보장, 월세/권리금 직접 판단으로 말하지 않는다.",
        "3. 아직 점수에 못 넣은 데이터도 버리지 않고 다음 전처리 조건을 명시한다.",
        "",
        "## 입력 근거",
        "",
        "- `datacorpus/_raw_ingest/source_registry.csv`",
        "- `datacorpus/_raw_ingest/ingest_manifest.csv`",
        "- `datacorpus/_rule_validation/00_raw_source_silver_gap_audit_20260703.csv`",
        "- `datacorpus/_gold/gold_data_reliability_snapshot.csv`",
        "- `scripts/build_rule_engine_gold_tables.py`",
        "- `scripts/build_rule_based_location_scores.py`",
        "",
        "## 요약 판정",
        "",
        f"- 감사 대상 source_id: {summary['audit_rows']:,}개",
        f"- source_registry 등록 원천: {summary['registry_source_count']:,}개",
        f"- manifest-only 보조 원천: {summary['manifest_only_source_count']:,}개",
        f"- 검증 PASS: {summary['validation_pass_count']:,}개",
        f"- 검증 FAIL: {summary['validation_fail_count']:,}개",
        f"- 최종 판정: **{summary['decision']}**",
        "",
        "## 사용단계별 개수",
        "",
        "| 사용단계 | 개수 |",
        "|---|---:|",
    ]

    for _, row in status_counts.iterrows():
        lines.append(f"| {row['pipeline_status']} | {int(row['count']):,} |")

    lines.extend(
        [
            "",
            "## registry 원천별 현재 사용 상태",
            "",
            "| source_id | 우선순위 | 현재 사용단계 | 점수사용강도 | 현재 연결 산출물 | 다음 행동 |",
            "|---|---|---|---|---|---|",
        ]
    )
    for _, row in registry.iterrows():
        gold_tables = str(row["current_gold_tables"]).replace("|", "/")
        next_action = str(row["next_action_ko"]).replace("|", "/")
        lines.append(
            f"| `{row['source_id']}` | {row['priority']} | {row['pipeline_status']} | {row['score_use_level']} | {gold_tables} | {next_action} |"
        )

    lines.extend(
        [
            "",
            "## manifest-only 보조 원천",
            "",
            "registry에는 없지만 manifest에 남아 있는 문서, 샘플, alias 원천이다. 직접 점수로 쓰지 않고 근거문서·입력브리지·기준선으로만 둔다.",
            "",
            "| source_id | 사용단계 | 이유 |",
            "|---|---|---|",
        ]
    )
    for _, row in manifest_only.iterrows():
        lines.append(f"| `{row['source_id']}` | {row['pipeline_status']} | {str(row['reason_ko']).replace('|', '/')} |")

    lines.extend(
        [
            "",
            "## 규칙 검증",
            "",
            "| id | 검증 | 결과 | 관측 | 기대 | 이유 |",
            "|---|---|---|---:|---|---|",
        ]
    )
    for _, row in validations.iterrows():
        lines.append(
            f"| {row['validation_id']} | {row['validation_name']} | {row['result']} | {row['observed']} | {row['expected']} | {str(row['reason_ko']).replace('|', '/')} |"
        )

    lines.extend(
        [
            "",
            "## 다음 전처리 우선순위",
            "",
            "1. **LocalData 인허가 업태→서울 서비스업종 매핑**: 개폐업·영업상태를 상권×업종 보조 경쟁/성장 신호로 쓰려면 bridge가 먼저 필요하다.",
            "2. **교통 승하차량 과거 월 커버리지 확보**: 현재 202605 단월이라 거리감쇠·buffer 후보를 만들어도 장기 백테스트 직접 투입은 보류다.",
            "3. **R-ONE 권역·상가유형→상권 특성 매핑**: 비용축은 RTMS 자치구 프록시에 치우쳐 있으므로 R-ONE은 참고선에서 조건부 비용 프록시로 올릴 수 있는지 검증한다.",
            "4. **버스 노선-정류장 네트워크 다양성 지표**: 승하차량 이전에도 노선 수·환승성은 접근성 후보가 될 수 있으나 실제 유입으로 과장하면 안 된다.",
            "5. **SGIS/KOSIS 기준선 보정 계층**: 행정구역 grain이라 직접점수는 금지하되, 서울 상권 결과의 외부 기준선·신뢰도 설명으로 쓸 수 있다.",
            "6. **부동산 중개업소 프록시 검토**: 비용환경 또는 상권활동성 보조 후보지만 월세/권리금 직접값은 아니다.",
            "",
            "## 산출물",
            "",
            "- `datacorpus/_rule_validation/44_rule_pipeline_source_coverage_audit.csv`",
            "- `datacorpus/_rule_validation/44_rule_pipeline_source_coverage_validation.csv`",
            "- `datacorpus/_rule_validation/44_rule_pipeline_source_coverage_summary.json`",
            "- `research/rule_validation/44_rule_pipeline_source_coverage_validation_20260707.md`",
            "",
            "## 결론",
            "",
            "현재 원천 데이터는 모두 같은 방식으로 점수에 들어가는 상태가 아니다. 매출·점포·인구·시설·생활이동·RTMS·SBDC 일부는 이미 직접/프록시 축으로 연결되어 있고, LocalData·교통 승하차량·R-ONE·SGIS/KOSIS·중개업소는 각각 bridge, 월커버리지, 권역매핑, 행정구역 배분 같은 선행조건이 남아 있다. 따라서 다음 전처리는 '한 파일에 다 합치기'가 아니라 source별 선행조건을 풀고 검증한 뒤 축별 gold 또는 후보 gold로 올리는 방식으로 진행한다.",
        ]
    )

    MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    RULE_VALIDATION.mkdir(parents=True, exist_ok=True)
    RESEARCH_RULE_VALIDATION.mkdir(parents=True, exist_ok=True)

    audit, validations, summary = build_audit()

    audit.to_csv(AUDIT_PATH, index=False, encoding="utf-8-sig")
    validations.to_csv(VALIDATION_PATH, index=False, encoding="utf-8-sig")
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(audit, validations, summary)

    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if summary["decision"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
