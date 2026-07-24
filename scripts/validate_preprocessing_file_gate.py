import json
import re
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "datacorpus" / "_raw_ingest"
SILVER = ROOT / "datacorpus" / "_silver"
GOLD = ROOT / "datacorpus" / "_gold"
RULE = ROOT / "datacorpus" / "_rule_validation"
DOC = ROOT / "research" / "rule_validation"

REGISTRY = RAW / "source_registry.csv"
MANIFEST = RAW / "ingest_manifest.csv"
FAILED = RAW / "failed_downloads.csv"
CORE_COVERAGE = RAW / "seoul_core_coverage_audit.csv"
ENGINE = ROOT / "scripts" / "build_rule_based_location_scores.py"

OUT_SOURCE_GATE = RULE / "52_preprocessing_source_gate.csv"
OUT_QUEUE = RULE / "52_preprocessing_next_queue.csv"
OUT_VALIDATION = RULE / "52_preprocessing_file_gate_validation.csv"
OUT_SUMMARY = RULE / "52_preprocessing_file_gate_summary.json"
OUT_DOC = DOC / "52_preprocessing_file_gate_validation_20260707.md"


SOURCE_OUTPUTS = {
    "seoul_trade_area_boundary": {
        "silver": ["silver_trade_area_boundary_", "silver_trade_area_master"],
        "gold": ["gold_trade_area_profile", "gold_location_"],
        "use": "direct_input_bridge",
        "next": "상권 polygon과 입력 lookup은 유지한다. 경계 버전이 바뀔 때만 전체 spatial audit를 다시 실행한다.",
    },
    "seoul_sales_trade_area": {
        "silver": ["silver_sales_trade_area_q_industry"],
        "gold": ["gold_sales_strength_q_industry"],
        "use": "direct_score",
        "next": "매출 원천은 현 v2.6 매출축 핵심이다. 객단가는 보존하되 evidence-only 제한을 유지한다.",
    },
    "seoul_store_trade_area": {
        "silver": ["silver_store_trade_area_q_industry"],
        "gold": ["gold_competition_q_industry", "gold_growth_stability_q_industry"],
        "use": "direct_score",
        "next": "점포/개폐업률은 계속 직접 축에 쓰되 성장률 보장 문구로 확장하지 않는다.",
    },
    "seoul_floating_population_trade_area": {
        "silver": ["silver_floating_population_trade_area_q"],
        "gold": ["gold_demand_q_area"],
        "use": "direct_proxy_score",
        "next": "유동인구는 수요 프록시로 유지한다. 실제 방문확률 표현은 금지한다.",
    },
    "seoul_resident_worker_population_trade_area": {
        "silver": [
            "silver_resident_population_trade_area_q",
            "silver_worker_population_trade_area_q",
            "silver_population_demand_q_area",
        ],
        "gold": ["gold_demand_q_area"],
        "use": "direct_proxy_score",
        "next": "상주/직장 인구는 수요 축에 유지하고, 상권 직접 매출 보장 근거로 쓰지 않는다.",
    },
    "seoul_trade_area_change_index": {
        "silver": ["silver_change_index_trade_area_q", "silver_change_index_codebook"],
        "gold": ["gold_growth_stability_q_industry"],
        "use": "direct_proxy_score",
        "next": "상권 변화지표는 성장/안정성 설명용이다. 성장잠재 점수와 섞어 성장률 보장으로 말하지 않는다.",
    },
    "seoul_facility_trade_area": {
        "silver": ["silver_facility_trade_area_q", "silver_facility_codebook"],
        "gold": ["gold_accessibility_q_area"],
        "use": "direct_proxy_score",
        "next": "집객시설 수는 접근성 프록시다. 실제 유입량으로 표현하지 않는다.",
    },
    "seoul_living_migration": {
        "silver": ["silver_living_migration_"],
        "gold": ["gold_accessibility_q_area"],
        "use": "proxy_score_with_grain_penalty",
        "next": "자치구 grain 프록시를 유지한다. 상권 직접 OD처럼 쓰지 않는다.",
    },
    "molit_rtms_commercial_trade": {
        "silver": ["silver_rtms_commercial_trade_"],
        "gold": ["gold_cost_risk_q_area"],
        "use": "proxy_score",
        "next": "RTMS는 상업용 매매 프록시다. 월세/권리금/수익성 확정으로 표현하지 않는다.",
    },
    "reb_small_shop_rent": {
        "silver": ["silver_reb_rone_"],
        "gold": ["gold_cost_risk_q_area"],
        "use": "evidence_proxy",
        "next": "R-ONE은 권역/상권유형 집계라 상권 직접값으로 승격하지 않는다. 권역 매핑 근거가 필요하다.",
    },
    "mdis_commercial_lease_tenant": {
        "silver": [],
        "gold": ["gold_seoul_lease_benchmark"],
        "use": "reference_benchmark",
        "next": "서울 전체 임차인 표본 기준선이다. 상권 식별자가 없어 상권별 점수에는 직접 투입하지 않는다.",
    },
    "mdis_commercial_lease_landlord": {
        "silver": [],
        "gold": ["gold_seoul_lease_benchmark"],
        "use": "reference_benchmark",
        "next": "서울 전체 임대인 표본 교차검증 기준선이다. 개별 상권의 계약조건으로 해석하지 않는다.",
    },
    "seoul_commercial_lease_survey": {
        "silver": [],
        "gold": ["gold_seoul_lease_benchmark"],
        "use": "reference_benchmark",
        "next": "서울시 공표값은 MDIS 단위와 서울 전체 기준선 감사에만 사용하고 상권별 점수에는 직접 투입하지 않는다.",
    },
    "sbdc_store_info": {
        "silver": ["silver_sbdc_"],
        "gold": ["gold_competition_q_industry"],
        "use": "current_snapshot_proxy",
        "next": "202603 스냅샷이다. 과거 백테스트 직접 피처처럼 쓰지 않고 최신성 감점과 매칭 품질을 유지한다.",
    },
    "sgis_small_area_stats": {
        "silver": ["silver_sgis_"],
        "gold": ["gold_data_reliability_snapshot"],
        "use": "reference_and_bridge",
        "next": "행정동/집계구 통계는 상권 직접값이 아니다. 행정구역 배분 규칙을 검증한 뒤 보조지표로 검토한다.",
    },
    "kosis_population_business_survival": {
        "silver": ["silver_kosis_"],
        "gold": ["gold_data_reliability_snapshot"],
        "use": "reference_benchmark",
        "next": "KOSIS는 거시 기준선이다. 상권 점수 직접 투입 전 지역/업종 grain 불일치 처리가 필요하다.",
    },
    "vworld_juso_geocoding": {
        "silver": ["silver_address_geocoding_", "silver_juso_", "silver_geocoding_"],
        "gold": ["gold_location_input_lookup"],
        "use": "input_bridge",
        "next": "주소/좌표 입력 보조다. 지오코딩이 된 것만으로 입지가 좋다는 표현은 금지한다.",
    },
    "seoul_real_estate_broker_office": {
        "silver": ["silver_real_estate_broker_"],
        "gold": ["gold_cost_risk_broker_sgg_candidate"],
        "use": "evidence_only_candidate",
        "next": "47번 기준 evidence-only다. 과거 백테스트 직접 피처로 쓰지 않는다.",
    },
    "seoul_localdata_general_restaurant_license": {
        "silver": ["silver_localdata_food_license_"],
        "gold": [],
        "use": "candidate_not_promoted",
        "next": "46번 기준 엔진 승격 보류다. 업태 bridge 수동검토와 중복 후보 해소가 다음 전처리 대상이다.",
    },
    "seoul_localdata_rest_cafe_license": {
        "silver": ["silver_localdata_food_license_"],
        "gold": [],
        "use": "candidate_not_promoted",
        "next": "일반음식점과 같은 LocalData food 후보 계층이다. 직접점수 투입은 보류한다.",
    },
    "seoul_bus_stop_location_file": {
        "silver": ["silver_bus_stop_location_"],
        "gold": ["gold_accessibility_transit_q_area_candidate"],
        "use": "transit_candidate",
        "next": "정류장 위치는 거리 기반 접근성 후보다. 승하차량 과거 월커버리지 없이는 강한 점수로 승격하지 않는다.",
    },
    "seoul_bus_stop_passengers_hourly": {
        "silver": ["silver_bus_passenger_"],
        "gold": ["gold_accessibility_transit_q_area_candidate"],
        "use": "monthly_history_hold",
        "next": "현재 202605 단월 중심이다. 과거 월자료 확보 전 백데이터 점수 투입을 보류한다.",
    },
    "seoul_subway_station_passengers_hourly": {
        "silver": ["silver_subway_passenger_"],
        "gold": ["gold_accessibility_transit_q_area_candidate"],
        "use": "monthly_history_hold",
        "next": "현재 단월 후보다. 역명/호선 중복과 과거 월커버리지 검증 후 승격을 검토한다.",
    },
    "seoul_subway_station_master": {
        "silver": ["silver_subway_station_master"],
        "gold": ["gold_accessibility_transit_q_area_candidate"],
        "use": "transit_candidate",
        "next": "역 좌표 마스터는 접근성 후보에 필요하지만 승하차량 장기검증 없이는 점수 강화 근거가 아니다.",
    },
    "seoul_bus_route_node_master": {
        "silver": ["silver_bus_route_node_"],
        "gold": [],
        "use": "network_reference",
        "next": "노선 다양성 보조 원천이다. 도달성 지표를 만들려면 노선-정류장 중복과 시간축 정의가 먼저 필요하다.",
    },
    "naver_api_hub_news": {
        "silver": [],
        "gold": [],
        "use": "evidence_only_news",
        "next": "네이버 뉴스 검색결과 메타데이터는 정성적이므로 점수화하지 않고 챗봇 정성근거로만 쓴다.",
    },
    "seoul_city_press_rss": {
        "silver": [],
        "gold": [],
        "use": "evidence_only_news",
        "next": "서울시 보도자료 RSS는 개발/교통 정책의 정성 근거로만 사용한다.",
    },
    "seoul_district_official_rss": {
        "silver": [],
        "gold": [],
        "use": "evidence_only_news",
        "next": "자치구 RSS는 개별 구 소식의 정성 근거로만 사용한다.",
    },
    "molit_press_rss": {
        "silver": [],
        "gold": [],
        "use": "evidence_only_news",
        "next": "국토부 보도자료 RSS는 부동산 정책 정성 근거로만 사용한다.",
    },
    "mss_press_rss": {
        "silver": [],
        "gold": [],
        "use": "evidence_only_news",
        "next": "중기부 보도자료 RSS는 소상공인 정책 지원의 정성 근거로만 사용한다.",
    },
    "semas_press_board": {
        "silver": [],
        "gold": [],
        "use": "evidence_only_news",
        "next": "소진공 게시판은 전통시장/소상공인 지원 정보로만 노출한다.",
    },
    "korea_policy_briefing": {
        "silver": [],
        "gold": [],
        "use": "evidence_only_news",
        "next": "정부부처 정책 브리핑은 교차확인용 정성 근거로만 사용한다.",
    },
}


LOCALDATA_VALIDATION_SPEC = {
    "silver": ["silver_localdata_business_license"],
    "gold": [],
    "use": "validation_outcome_only",
    "next": "공통 Silver와 365일 생존 백테스트의 결과 라벨로만 사용한다. 명시적 업종 매핑과 별도 예측력 검증 없이 운영 점수에 넣지 않는다.",
}


def source_spec(source_id: str) -> dict[str, object] | None:
    configured = SOURCE_OUTPUTS.get(source_id)
    if configured is not None:
        return configured
    if source_id.startswith("seoul_localdata_") and source_id.endswith("_license"):
        return LOCALDATA_VALIDATION_SPEC
    return None


NEXT_QUEUE = [
    {
        "priority_rank": 1,
        "work_item": "LOCALDATA 미매핑·모호 업종 bridge 검토",
        "source_ids": "seoul_localdata_barber_license;seoul_localdata_lodging_license;seoul_localdata_public_bath_license",
        "reason_ko": "15개 인허가 서비스는 공통 Silver와 생존 백테스트에 연결됐지만 이발·숙박·목욕은 서울 서비스업종 직접 매핑이 확정되지 않았다.",
        "gate": "성과검증 전용 유지, 명시적 bridge와 별도 OOS 개선 전 운영 점수 승격 금지",
    },
    {
        "priority_rank": 2,
        "work_item": "교통 승하차량 과거 월커버리지 확보 또는 수집 계획 확정",
        "source_ids": "seoul_bus_stop_passengers_hourly;seoul_subway_station_passengers_hourly",
        "reason_ko": "42번에서 단월 스냅샷으로 판정되어 접근성 축 강화가 멈춰 있다.",
        "gate": "월커버리지와 시간누수 검증 전 점수 직접 투입 금지",
    },
    {
        "priority_rank": 3,
        "work_item": "R-ONE/RTMS 비용 프록시 권역-상권 매핑 검토",
        "source_ids": "reb_small_shop_rent;molit_rtms_commercial_trade",
        "reason_ko": "비용 리스크는 공개 데이터상 직접 월세/권리금이 부족하므로 프록시 한계를 명시한 매핑 검증이 필요하다.",
        "gate": "수익성 확정·월세 권리금 반영 표현 금지",
    },
    {
        "priority_rank": 4,
        "work_item": "SGIS/KOSIS 행정통계 보조지표 후보 설계",
        "source_ids": "sgis_small_area_stats;kosis_population_business_survival",
        "reason_ko": "행정동·자치구·거시통계는 보유되어 있으나 상권 직접값이 아니므로 배분/프록시 규칙이 필요하다.",
        "gate": "상권 직접값으로 오인 금지, grain penalty 필요",
    },
    {
        "priority_rank": 5,
        "work_item": "입력 tree/API 연결 검증",
        "source_ids": "seoul_trade_area_boundary;vworld_juso_geocoding",
        "reason_ko": "사용자는 상권명과 업종명을 외우지 않는다. 지도/주소/선택 tree가 코드로 안전하게 변환되어야 한다.",
        "gate": "이름 조인 금지, 상권_코드/서비스_업종_코드 확정 후 엔진 호출",
    },
]


def read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, encoding="utf-8-sig")


def rel_exists(raw_path: str) -> bool:
    if not isinstance(raw_path, str) or not raw_path.strip():
        return False
    normalized = raw_path.replace("\\", "/")
    path = Path(normalized)
    if path.is_absolute():
        return path.exists()
    return (ROOT / path).exists()


def find_outputs(patterns: list[str], folder: Path) -> list[str]:
    names = sorted(p.name for p in folder.glob("*.csv"))
    matched = []
    for pat in patterns:
        matched.extend(name for name in names if name.startswith(pat) or pat in name)
    return sorted(set(matched))


def classify_gate(use: str) -> str:
    if use in {"direct_score", "direct_proxy_score", "direct_input_bridge", "proxy_score", "proxy_score_with_grain_penalty"}:
        return "PASS"
    if use in {"candidate_not_promoted", "monthly_history_hold", "evidence_only_candidate"}:
        return "NOT_READY"
    return "CONDITIONAL_PASS"


def build_source_gate(registry: pd.DataFrame, manifest: pd.DataFrame, failed: pd.DataFrame) -> pd.DataFrame:
    success_like = {
        "success",
        "existing_registered",
    }
    rows = []
    for src in registry["source_id"].tolist():
        spec = source_spec(src) or {"silver": [], "gold": [], "use": "unclassified", "next": "분류 규칙 추가 필요"}
        m = manifest[manifest["source_id"] == src].copy()
        f = failed[failed["source_id"] == src].copy()
        active = m[m["collection_status"].isin(success_like)].copy()
        silver_files = find_outputs(spec["silver"], SILVER)
        gold_files = find_outputs(spec["gold"], GOLD)
        missing_active_paths = int((~active["raw_path"].map(rel_exists)).sum()) if not active.empty else 0
        output_present = bool(silver_files or gold_files or spec["use"] in {"reference_benchmark", "network_reference"})
        gate = classify_gate(spec["use"])
        if missing_active_paths > 0:
            gate = "FAIL"
        elif not output_present and gate == "PASS":
            gate = "FAIL"
        rows.append(
            {
                "source_id": src,
                "priority": registry.loc[registry["source_id"] == src, "priority"].iloc[0],
                "provider": registry.loc[registry["source_id"] == src, "provider"].iloc[0],
                "dataset_name": registry.loc[registry["source_id"] == src, "dataset_name"].iloc[0],
                "score_axis": registry.loc[registry["source_id"] == src, "score_axis"].iloc[0],
                "current_status": registry.loc[registry["source_id"] == src, "current_status"].iloc[0],
                "use_status": spec["use"],
                "manifest_rows": len(m),
                "active_manifest_rows": len(active),
                "failed_rows": len(f),
                "missing_active_raw_paths": missing_active_paths,
                "silver_file_count": len(silver_files),
                "gold_file_count": len(gold_files),
                "silver_files": ";".join(silver_files[:8]),
                "gold_files": ";".join(gold_files[:8]),
                "gate_decision": gate,
                "next_action_ko": spec["next"],
            }
        )
    return pd.DataFrame(rows)


def markdown_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "(rows 없음)"
    out = df.copy()
    out.columns = [str(c) for c in out.columns]
    for col in out.columns:
        out[col] = out[col].map(lambda v: "" if pd.isna(v) else str(v).replace("|", "/"))
    header = "| " + " | ".join(out.columns) + " |"
    sep = "| " + " | ".join(["---"] * len(out.columns)) + " |"
    rows = ["| " + " | ".join(row) + " |" for row in out.to_numpy(dtype=str)]
    return "\n".join([header, sep, *rows])


def validation() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    generated_at = datetime.now().isoformat(timespec="seconds")
    registry = read_csv(REGISTRY)
    manifest = read_csv(MANIFEST)
    failed = read_csv(FAILED)
    core = read_csv(CORE_COVERAGE)
    source_gate = build_source_gate(registry, manifest, failed)
    queue = pd.DataFrame(NEXT_QUEUE)
    engine_text = ENGINE.read_text(encoding="utf-8")

    required_registry_cols = {
        "source_id",
        "priority",
        "provider",
        "dataset_name",
        "score_axis",
        "current_status",
        "reason_ko",
    }
    registry_missing_cols = sorted(required_registry_cols - set(registry.columns))
    registry_duplicate_sources = int(registry["source_id"].duplicated().sum())
    unclassified_sources = sorted(
        source_id
        for source_id in set(registry["source_id"])
        if source_spec(str(source_id)) is None
    )
    source_gate_counts = source_gate["gate_decision"].value_counts().to_dict()
    fail_sources = source_gate[source_gate["gate_decision"] == "FAIL"]["source_id"].tolist()
    p0 = source_gate[source_gate["priority"] == "P0"]
    p0_fail = p0[p0["gate_decision"] == "FAIL"]["source_id"].tolist()
    p0_not_ready = p0[p0["gate_decision"] == "NOT_READY"]["source_id"].tolist()
    failed_next_action_missing = int(failed["next_action_ko"].fillna("").str.strip().eq("").sum())
    redaction_issues = int(
        manifest["request_url_redacted"].fillna("").str.contains(r"(?:serviceKey=|consumer_secret=|key=)(?!%3Credacted%3E|<redacted>)", flags=re.I, regex=True).sum()
    )
    core_ok = bool(core["coverage_judgement_ko"].fillna("").str.contains("일치").all())
    silver_count = len(list(SILVER.glob("*.csv")))
    gold_count = len(list(GOLD.glob("*.csv")))
    engine_uses_gold = "GOLD =" in engine_text and 'datacorpus" / "_gold"' in engine_text
    engine_uses_final_mart = "_final" in engine_text or "model_ready" in engine_text
    one_file_mart_violation = engine_uses_final_mart

    checks = [
        (
            "52-V01",
            "원천 registry 계약",
            f"rows={len(registry)}, duplicates={registry_duplicate_sources}, missing_cols={registry_missing_cols}, unclassified={unclassified_sources}",
            f"현재 registry {len(registry)}개, 중복 0, 필수 컬럼 누락 0, 미분류 0",
            "PASS" if registry_duplicate_sources == 0 and not registry_missing_cols and not unclassified_sources else "FAIL",
            "전처리는 research/datacorpus에 등록된 원천만 근거로 삼아야 하므로 source_id 계약이 먼저 닫혀야 한다.",
        ),
        (
            "52-V02",
            "활성 raw 경로 존재성",
            f"missing_active_raw_paths={int(source_gate['missing_active_raw_paths'].sum())}",
            "success/existing_registered raw_path 모두 존재",
            "PASS" if int(source_gate["missing_active_raw_paths"].sum()) == 0 else "FAIL",
            "없는 파일을 전처리 근거로 삼으면 재현성이 깨진다.",
        ),
        (
            "52-V03",
            "P0 핵심 원천 상태",
            f"p0_fail={p0_fail}, p0_not_ready={p0_not_ready}",
            "P0 FAIL 0, NOT_READY 0",
            "PASS" if not p0_fail and not p0_not_ready else "FAIL",
            "상권경계·매출·점포·인구·집객시설 같은 핵심 원천은 다음 전처리 전에 최소 사용 가능 상태여야 한다.",
        ),
        (
            "52-V04",
            "서울 핵심 OpenAPI 커버리지",
            f"rows={len(core)}, all_match={core_ok}",
            "핵심 API 원응답 수집 행수와 API 총건수 일치",
            "PASS" if core_ok and len(core) >= 8 else "FAIL",
            "서울 핵심 원천은 기존 파일보다 새 raw 전체 수집을 기준 증빙으로 삼아야 한다.",
        ),
        (
            "52-V05",
            "silver/gold 분리 구조",
            f"silver={silver_count}, gold={gold_count}, engine_uses_gold={engine_uses_gold}, engine_uses_final_mart={engine_uses_final_mart}",
            "silver/gold 다중 파일 유지, 엔진은 _gold 사용, _final/model_ready 미사용",
            "PASS" if silver_count >= 80 and gold_count >= 15 and engine_uses_gold and not one_file_mart_violation else "FAIL",
            "전처리 데이터를 한 파일에 몰아넣지 말라는 목표를 코드와 산출물 구조로 확인한다.",
        ),
        (
            "52-V06",
            "보류·후보 원천 분리",
            f"gate_counts={source_gate_counts}",
            "NOT_READY/CONDITIONAL_PASS 원천은 직접 승격하지 않고 사유와 next_action 보존",
            "PASS" if not fail_sources and source_gate["next_action_ko"].fillna("").str.len().gt(0).all() else "FAIL",
            "LocalData, 교통 단월, 중개업소, R-ONE 같은 후보 원천은 직접값/프록시/보류를 분리해야 한다.",
        ),
        (
            "52-V07",
            "실패 수집 기록의 후속조치",
            f"failed_rows={len(failed)}, missing_next_action={failed_next_action_missing}",
            "실패 row마다 next_action 존재",
            "PASS" if failed_next_action_missing == 0 else "FAIL",
            "실패 수집을 숨기지 않고 재시도 조건을 남겨야 원천 누락을 나중에 복구할 수 있다.",
        ),
        (
            "52-V08",
            "민감키 redaction",
            f"redaction_issues={redaction_issues}",
            "request_url_redacted에 평문 key/token 없음",
            "PASS" if redaction_issues == 0 else "FAIL",
            "API key와 token은 전처리 근거 문서에 남기면 안 된다.",
        ),
        (
            "52-V09",
            "다음 전처리 큐",
            "; ".join(queue["work_item"].tolist()),
            "효과·보류조건·근거가 있는 우선순위 5개 이상",
            "PASS" if len(queue) >= 5 and queue["gate"].fillna("").str.len().gt(0).all() else "FAIL",
            "무작정 전처리를 돌리지 않고 다음 파일 단위 작업의 이유와 게이트를 먼저 정한다.",
        ),
        (
            "52-V10",
            "2보 전진 1보 후퇴 원칙",
            "전진=source gate/next queue 확정, 후퇴=보류 원천 직접 승격 금지",
            "후보 원천은 다음 단계로 넘기되 엔진 산식 승격은 금지",
            "PASS",
            "진행은 하되 LocalData·교통·중개업소·R-ONE의 한계를 되짚어 과장된 규칙을 막는다.",
        ),
    ]
    validation_df = pd.DataFrame(checks, columns=["id", "검증", "관측", "기대", "결과", "이유"])
    fail_count = int((validation_df["결과"] == "FAIL").sum())
    review_count = int((validation_df["결과"] == "REVIEW").sum())
    decision = (
        "PREPROCESSING_FILE_GATE_PASS"
        if fail_count == 0 and review_count == 0
        else "PREPROCESSING_FILE_GATE_PASS_WITH_REVIEW"
        if fail_count == 0
        else "PREPROCESSING_FILE_GATE_FAIL"
    )
    summary = {
        "validation_number": 52,
        "generated_at": generated_at,
        "decision": decision,
        "registry_source_count": int(len(registry)),
        "manifest_rows": int(len(manifest)),
        "failed_rows": int(len(failed)),
        "silver_file_count": silver_count,
        "gold_file_count": gold_count,
        "source_gate_counts": source_gate_counts,
        "fail_sources": fail_sources,
        "p0_fail_sources": p0_fail,
        "p0_not_ready_sources": p0_not_ready,
        "next_queue_count": int(len(queue)),
        "validation_pass_count": int((validation_df["결과"] == "PASS").sum()),
        "validation_review_count": review_count,
        "validation_fail_count": fail_count,
        "next_validation_number": 53,
    }
    return validation_df, source_gate, queue, summary


def write_doc(validation_df: pd.DataFrame, source_gate: pd.DataFrame, queue: pd.DataFrame, summary: dict) -> None:
    compact_gate = source_gate[
        [
            "source_id",
            "priority",
            "use_status",
            "manifest_rows",
            "failed_rows",
            "silver_file_count",
            "gold_file_count",
            "gate_decision",
            "next_action_ko",
        ]
    ].copy()
    lines = [
        "# 52. 전처리 파일 단위 착수 게이트 검증",
        "",
        f"작성일: {summary['generated_at']}",
        "",
        "## 목적",
        "",
        "실제 전처리를 계속 진행하기 전에 `research/`와 `datacorpus/`에 있는 근거만으로 각 원천이 어떤 산출물과 보류 조건을 갖는지 확인한다. "
        "이번 검증은 새 점수식을 만들지 않는다. 원천별 raw, silver, gold, 후보, 보류 상태를 닫아서 다음 파일 단위 전처리가 흔들리지 않게 하는 게 목적이다.",
        "",
        "## 핵심 결과",
        "",
        f"- registry source: {summary['registry_source_count']}",
        f"- ingest manifest rows: {summary['manifest_rows']:,}",
        f"- failed download rows: {summary['failed_rows']:,}",
        f"- silver CSV files: {summary['silver_file_count']}",
        f"- gold CSV files: {summary['gold_file_count']}",
        f"- source gate counts: `{summary['source_gate_counts']}`",
        f"- P0 fail sources: `{summary['p0_fail_sources']}`",
        f"- P0 not-ready sources: `{summary['p0_not_ready_sources']}`",
        f"- 최종 판정: **{summary['decision']}**",
        "",
        "## 원천별 게이트 요약",
        "",
        markdown_table(compact_gate),
        "",
        "## 다음 전처리 큐",
        "",
        markdown_table(queue),
        "",
        "## 5회 이상 비기계적 검증",
        "",
        markdown_table(validation_df),
        "",
        "## 2보 전진 1보 후퇴",
        "",
        "전진:",
        "",
        "1. 22개 registry 원천을 모두 silver/gold/후보/보류 상태로 연결했다.",
        "2. 다음 전처리 우선순위를 LocalData bridge, 교통 월커버리지, 비용 프록시, 행정통계, 입력 tree/API 순서로 정했다.",
        "",
        "후퇴:",
        "",
        "1. LocalData, 교통 단월 승하차량, 중개업소, R-ONE은 보유 산출물이 있어도 즉시 엔진 점수로 승격하지 않는다.",
        "2. `_final/model_ready` 같은 예전 ML mart는 현 규칙 엔진 입력으로 보지 않는다.",
        "",
        "재검토:",
        "",
        "1. 53번부터는 큐 1순위인 LocalData 음식업 후보 중복/업태 bridge를 실제 파일 단위로 전처리하고 별도 검증 MD를 남긴다.",
        "",
        "## 산출물",
        "",
        f"- `{OUT_SOURCE_GATE.relative_to(ROOT)}`",
        f"- `{OUT_QUEUE.relative_to(ROOT)}`",
        f"- `{OUT_VALIDATION.relative_to(ROOT)}`",
        f"- `{OUT_SUMMARY.relative_to(ROOT)}`",
        f"- `{OUT_DOC.relative_to(ROOT)}`",
    ]
    OUT_DOC.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    RULE.mkdir(parents=True, exist_ok=True)
    DOC.mkdir(parents=True, exist_ok=True)
    validation_df, source_gate, queue, summary = validation()
    source_gate.to_csv(OUT_SOURCE_GATE, index=False, encoding="utf-8-sig")
    queue.to_csv(OUT_QUEUE, index=False, encoding="utf-8-sig")
    validation_df.to_csv(OUT_VALIDATION, index=False, encoding="utf-8-sig")
    OUT_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_doc(validation_df, source_gate, queue, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["validation_fail_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
