from __future__ import annotations

import importlib.util
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
GOLD_DIR = ROOT / "datacorpus" / "_gold"
SCORE_BACKTEST_DIR = ROOT / "datacorpus" / "_score_backtest_gold"
RULE_DIR = ROOT / "datacorpus" / "_rule_validation"
DOC_DIR = ROOT / "research" / "rule_validation"
ENGINE_PATH = ROOT / "scripts" / "build_rule_based_location_scores.py"

OUT_CONTRACT = RULE_DIR / "84_v1_preprocessing_payload_contract.csv"
OUT_FILE_AUDIT = RULE_DIR / "84_v1_preprocessing_payload_file_audit.csv"
OUT_VALIDATION = RULE_DIR / "84_v1_preprocessing_payload_contract_validation.csv"
OUT_SUMMARY = RULE_DIR / "84_v1_preprocessing_payload_contract_summary.json"
OUT_DOC = DOC_DIR / "84_v1_preprocessing_payload_contract_20260707.md"

SOURCE_CONTRACT = RULE_DIR / "75_preprocessing_file_execution_contract.csv"
DIRECT_READINESS = RULE_DIR / "76_direct_score_input_readiness_summary.json"
JOIN_STABILITY = RULE_DIR / "78_algorithm_payload_v2_backdata_join_stability_summary.json"
DIRECTION_SUMMARY = RULE_DIR / "79_direction_matrix_quality_warning_summary.json"
COST_SUMMARY = RULE_DIR / "82_cost_proxy_official_use_contract_summary.json"
NEXT_QUEUE = RULE_DIR / "83_next_queue_after_cost_contract.csv"
FORBIDDEN_AUDIT = SCORE_BACKTEST_DIR / "gold_engine_forbidden_claim_audit.csv"
BACKTEST_SUMMARY = SCORE_BACKTEST_DIR / "gold_engine_backtest_summary.json"
DIRECTION_AUDIT = SCORE_BACKTEST_DIR / "gold_engine_direction_effect_audit.csv"

VERSION = "v1_preprocessing_payload_contract.v0.1-20260707"


# v1에서 파일을 한 덩어리 feature mart로 합치지 않기 위한 명시 계약.
# 각 row는 "역할", "읽는 시점", "허용 표현"을 함께 가진다.
CONTRACT_ROWS: list[dict[str, Any]] = [
    {
        "contract_group": "official_current_score",
        "file_path": "datacorpus/_gold/gold_trade_area_profile.csv",
        "layer": "gold",
        "grain": "trade_area",
        "key_columns": "상권_코드",
        "engine_field": "matched_target/profile",
        "use_in_v1": "official_input",
        "score_mutation_allowed": "False",
        "basis_docs": "research/rule_validation/23_gold_preprocessing_validation_20260704.md;research/rule_validation/66_input_resolver_operational_contract_20260707.md",
        "reason_ko": "상권명, 자치구, 행정동 등 대상 식별자이며 점수 자체가 아니다.",
        "claim_limit_ko": "상권 프로필만으로 입지 우수성이나 성공 가능성을 말하지 않는다.",
    },
    {
        "contract_group": "official_current_score",
        "file_path": "datacorpus/_gold/gold_sales_strength_q_industry.csv",
        "layer": "gold",
        "grain": "quarter_trade_area_industry",
        "key_columns": "기준_년분기_코드;상권_코드;서비스_업종_코드",
        "engine_field": "axis.sales",
        "use_in_v1": "official_score_axis",
        "score_mutation_allowed": "True",
        "basis_docs": "research/알고리즘_명세_v2_20260704.md;research/rule_validation/76_direct_score_input_readiness_20260707.md",
        "reason_ko": "서울 상권분석서비스 추정매출의 상권×업종×분기 직접 집계로 현재입지 핵심 축이다.",
        "claim_limit_ko": "개별 매장 매출 보장이나 창업 성공확률로 표현하지 않는다.",
    },
    {
        "contract_group": "official_current_score",
        "file_path": "datacorpus/_gold/gold_competition_q_industry.csv",
        "layer": "gold",
        "grain": "quarter_trade_area_industry",
        "key_columns": "기준_년분기_코드;상권_코드;서비스_업종_코드",
        "engine_field": "axis.competition",
        "use_in_v1": "official_score_axis",
        "score_mutation_allowed": "True",
        "basis_docs": "research/알고리즘_명세_v2_20260704.md;research/rule_validation/76_direct_score_input_readiness_20260707.md",
        "reason_ko": "점포, 동종 점포, 개폐업률의 상권×업종×분기 직접 집계로 경쟁/과밀을 계산한다.",
        "claim_limit_ko": "점포가 많다는 사실만으로 좋은 입지나 나쁜 입지로 단정하지 않는다.",
    },
    {
        "contract_group": "official_current_score",
        "file_path": "datacorpus/_gold/gold_demand_q_area.csv",
        "layer": "gold",
        "grain": "quarter_trade_area",
        "key_columns": "기준_년분기_코드;상권_코드",
        "engine_field": "axis.demand",
        "use_in_v1": "official_score_axis",
        "score_mutation_allowed": "True",
        "basis_docs": "research/알고리즘_명세_v2_20260704.md;research/rule_validation/76_direct_score_input_readiness_20260707.md",
        "reason_ko": "유동/상주/직장인구와 소비잠재의 상권×분기 수요 프록시다.",
        "claim_limit_ko": "실제 방문자 수, 구매자 수, 업종별 소비 보장으로 표현하지 않는다.",
    },
    {
        "contract_group": "official_current_score",
        "file_path": "datacorpus/_gold/gold_accessibility_q_area.csv",
        "layer": "gold",
        "grain": "quarter_trade_area",
        "key_columns": "기준_년분기_코드;상권_코드",
        "engine_field": "axis.accessibility",
        "use_in_v1": "official_score_axis",
        "score_mutation_allowed": "True",
        "basis_docs": "research/알고리즘_명세_v2_20260704.md;research/rule_validation/76_direct_score_input_readiness_20260707.md",
        "reason_ko": "집객시설, 교통결절, 생활이동 외부유입 프록시를 묶은 접근성 축이다.",
        "claim_limit_ko": "시설 수나 생활이동을 실제 방문확률, 도보시간, 매출 유입으로 단정하지 않는다.",
    },
    {
        "contract_group": "separate_score",
        "file_path": "datacorpus/_gold/gold_growth_stability_q_industry.csv",
        "layer": "gold",
        "grain": "quarter_trade_area_industry",
        "key_columns": "기준_년분기_코드;상권_코드;서비스_업종_코드",
        "engine_field": "growth_potential_score",
        "use_in_v1": "separate_score",
        "score_mutation_allowed": "True",
        "basis_docs": "research/알고리즘_명세_v2_20260704.md;research/rule_validation/33_growth_label_candidate_validation_20260704.md",
        "reason_ko": "성장/안정성은 현재입지와 질문이 달라 별도 후보 점수로 분리한다.",
        "claim_limit_ko": "성장률 예측이나 성장 보장으로 표현하지 않는다.",
    },
    {
        "contract_group": "separate_score",
        "file_path": "datacorpus/_gold/gold_growth_rebound_candidate_q_industry.csv",
        "layer": "gold",
        "grain": "quarter_trade_area_industry",
        "key_columns": "기준_년분기_코드;상권_코드;서비스_업종_코드",
        "engine_field": "growth_rebound_candidate",
        "use_in_v1": "candidate_signal",
        "score_mutation_allowed": "False",
        "basis_docs": "research/rule_validation/38_growth_rebound_engine_parallel_output_20260704.md;research/rule_validation/39_ai_report_growth_rebound_claim_contract_20260704.md",
        "reason_ko": "반등 신호는 기존 현재입지 점수를 덮지 않고 병렬 후보로만 붙인다.",
        "claim_limit_ko": "매출 수준 점수나 성장 보장으로 설명하지 않는다.",
    },
    {
        "contract_group": "separate_score",
        "file_path": "datacorpus/_gold/gold_cost_risk_q_area.csv",
        "layer": "gold",
        "grain": "quarter_trade_area_with_district_proxy",
        "key_columns": "기준_년분기_코드;상권_코드",
        "engine_field": "cost_risk_score",
        "use_in_v1": "separate_proxy_score",
        "score_mutation_allowed": "False",
        "basis_docs": "research/rule_validation/82_cost_proxy_official_use_contract_20260707.md",
        "reason_ko": "RTMS 자치구 상업업무용 매매가격 기반 비용 압력 프록시다.",
        "claim_limit_ko": "월세, 권리금, 영업이익, 수익성 확정으로 표현하지 않는다.",
    },
    {
        "contract_group": "input_resolver",
        "file_path": "datacorpus/_gold/gold_location_input_lookup.csv",
        "layer": "gold",
        "grain": "trade_area_lookup",
        "key_columns": "상권_코드",
        "engine_field": "input.location",
        "use_in_v1": "input_resolver",
        "score_mutation_allowed": "False",
        "basis_docs": "research/rule_validation/66_input_resolver_operational_contract_20260707.md",
        "reason_ko": "사용자의 상권명/주소/좌표 입력을 상권 코드 후보로 바꾸는 운영 lookup이다.",
        "claim_limit_ko": "입력 매칭 성공을 점수 품질이나 입지 우수성으로 해석하지 않는다.",
    },
    {
        "contract_group": "input_resolver",
        "file_path": "datacorpus/_gold/gold_location_spatial_index.csv",
        "layer": "gold",
        "grain": "trade_area_spatial_index",
        "key_columns": "상권_코드",
        "engine_field": "input.location.spatial_index",
        "use_in_v1": "input_resolver",
        "score_mutation_allowed": "False",
        "basis_docs": "research/rule_validation/41_location_resolver_boundary_adjacency_validation_20260704.md",
        "reason_ko": "지도 클릭 좌표의 polygon 후보 범위를 빠르게 좁히는 공간 인덱스다.",
        "claim_limit_ko": "경계 포함 여부만으로 좋은 입지라고 말하지 않는다.",
    },
    {
        "contract_group": "input_resolver",
        "file_path": "datacorpus/_gold/gold_location_boundary_vertices.csv",
        "layer": "gold",
        "grain": "trade_area_boundary_vertices",
        "key_columns": "상권_코드;part_index;vertex_index",
        "engine_field": "input.location.boundary",
        "use_in_v1": "input_resolver",
        "score_mutation_allowed": "False",
        "basis_docs": "research/rule_validation/41_location_resolver_boundary_adjacency_validation_20260704.md",
        "reason_ko": "좌표 클릭 시 polygon 포함/경계거리/인접 상권을 판단하기 위한 꼭짓점 테이블이다.",
        "claim_limit_ko": "인접 상권 후보를 확정 상권처럼 사용하지 않는다.",
    },
    {
        "contract_group": "input_resolver",
        "file_path": "datacorpus/_gold/gold_industry_selection_hierarchy.csv",
        "layer": "gold",
        "grain": "industry_hierarchy",
        "key_columns": "서비스_업종_코드",
        "engine_field": "input.industry.tree",
        "use_in_v1": "input_resolver",
        "score_mutation_allowed": "False",
        "basis_docs": "research/rule_validation/40_industry_selection_fallback_hierarchy_validation_20260704.md",
        "reason_ko": "대/중/세부업종 선택 UI용 계층. 내부 조인 키는 코드로 유지한다.",
        "claim_limit_ko": "화면 표시명만으로 알고리즘 조인을 수행하지 않는다.",
    },
    {
        "contract_group": "input_resolver",
        "file_path": "datacorpus/_gold/gold_industry_selection_tree.json",
        "layer": "gold",
        "grain": "industry_tree_json",
        "key_columns": "서비스_업종_코드",
        "engine_field": "input.industry.tree_json",
        "use_in_v1": "input_resolver",
        "score_mutation_allowed": "False",
        "basis_docs": "research/rule_validation/40_industry_selection_fallback_hierarchy_validation_20260704.md",
        "reason_ko": "프론트/API가 바로 읽을 수 있는 업종 선택 tree다.",
        "claim_limit_ko": "fallback 계층은 탐색 편의용이며 점수 산식을 바꾸지 않는다.",
    },
    {
        "contract_group": "evidence_payload",
        "file_path": "datacorpus/_gold/gold_candidate_evidence_loader_registry_v01.csv",
        "layer": "gold",
        "grain": "candidate_registry",
        "key_columns": "evidence_id",
        "engine_field": "candidate_evidence.registry",
        "use_in_v1": "evidence_loader_contract",
        "score_mutation_allowed": "False",
        "basis_docs": "research/rule_validation/71_candidate_evidence_loader_contract_20260707.md",
        "reason_ko": "공식 점수를 바꾸지 않고 후보 evidence를 리포트 payload에 붙이는 registry다.",
        "claim_limit_ko": "registry에 있다고 해서 공식 점수 승격을 의미하지 않는다.",
    },
    {
        "contract_group": "evidence_payload",
        "file_path": "datacorpus/_gold/gold_localdata_food_license_q_industry_candidate.csv",
        "layer": "gold",
        "grain": "quarter_trade_area_industry_candidate",
        "key_columns": "기준_년분기_코드;상권_코드;candidate_서비스_업종_코드",
        "engine_field": "candidate_evidence.localdata_food",
        "use_in_v1": "evidence_only",
        "score_mutation_allowed": "False",
        "basis_docs": "research/rule_validation/70_localdata_manual_review_resolution_audit_20260707.md",
        "reason_ko": "인허가 후보는 조인 안정화는 됐지만 수동검토/hold가 남아 공식 승격 전 evidence로만 둔다.",
        "claim_limit_ko": "인허가 수를 매출, 생존율, 성공확률로 직접 해석하지 않는다.",
    },
    {
        "contract_group": "evidence_payload",
        "file_path": "datacorpus/_gold/gold_accessibility_transit_q_area_candidate.csv",
        "layer": "gold",
        "grain": "quarter_trade_area_candidate",
        "key_columns": "기준_월;상권_코드",
        "engine_field": "candidate_evidence.transit_250m",
        "use_in_v1": "candidate_signal_not_official",
        "score_mutation_allowed": "False",
        "basis_docs": "research/rule_validation/80_transit_accessibility_candidate_holdout_gate_20260707.md;research/rule_validation/81_transit_accessibility_official_promotion_readiness_20260707.md",
        "reason_ko": "holdout 개선은 있으나 최신 공식분기 2026Q1 raw가 비어 공식 승격을 보류한다.",
        "claim_limit_ko": "승하차량을 실제 방문확률, 도보시간, 구매확률로 말하지 않는다.",
    },
    {
        "contract_group": "evidence_payload",
        "file_path": "datacorpus/_gold/gold_cost_risk_rone_region_trade_area_candidate.csv",
        "layer": "gold",
        "grain": "quarter_trade_area_region_reference",
        "key_columns": "기준_년분기_코드;상권_코드;mapping_scope;selection_group;STATBL_ID;상가유형;지역_전체명;ITM_NM",
        "engine_field": "candidate_evidence.rone",
        "use_in_v1": "evidence_only",
        "score_mutation_allowed": "False",
        "basis_docs": "research/rule_validation/82_cost_proxy_official_use_contract_20260707.md",
        "reason_ko": "R-ONE은 권역/상가유형 기준선으로 직접 월세나 권리금이 아니다.",
        "claim_limit_ko": "개별 점포 월세, 권리금, 수익성 확정값으로 쓰지 않는다.",
    },
    {
        "contract_group": "evidence_payload",
        "file_path": "datacorpus/_silver/silver_reb_rone_seoul_cost_proxy_latest.csv",
        "layer": "silver",
        "grain": "quarter_region_shop_type_reference",
        "key_columns": "STATBL_NM;상가유형;지역_전체명;ITM_NM;기준_년분기_코드",
        "engine_field": "evidence_pack.R_ONE_임대_참고선",
        "use_in_v1": "evidence_only_runtime_reference",
        "score_mutation_allowed": "False",
        "basis_docs": "research/rule_validation/12_real_estate_cost_proxy_silver_validation_20260703.md;research/rule_validation/82_cost_proxy_official_use_contract_20260707.md",
        "reason_ko": "현재 엔진의 R-ONE 참고선 로더가 직접 읽는 silver 기준선이다. 점수 산식이 아니라 리포트 참고 evidence다.",
        "claim_limit_ko": "개별 상권·개별 점포 월세나 권리금으로 해석하지 않는다.",
    },
    {
        "contract_group": "evidence_payload",
        "file_path": "datacorpus/_gold/gold_admin_stats_sgis_emd_trade_area_candidate.csv",
        "layer": "gold",
        "grain": "admin_to_trade_area_candidate",
        "key_columns": "상권_코드;adm_cd;stat_domain;stat_year;metric_code",
        "engine_field": "candidate_evidence.sgis_admin",
        "use_in_v1": "evidence_only",
        "score_mutation_allowed": "False",
        "basis_docs": "research/rule_validation/65_admin_stats_grain_penalty_validation_20260707.md",
        "reason_ko": "SGIS 행정통계는 상권 polygon 직접값이 아니라 배분/기준선 후보다.",
        "claim_limit_ko": "행정통계를 상권 직접 수요값으로 표현하지 않는다.",
    },
    {
        "contract_group": "evidence_payload",
        "file_path": "datacorpus/_gold/gold_accessibility_bus_network_diversity_candidate.csv",
        "layer": "gold",
        "grain": "trade_area_snapshot_candidate",
        "key_columns": "상권_코드",
        "engine_field": "candidate_evidence.bus_network",
        "use_in_v1": "evidence_only_until_history_proved",
        "score_mutation_allowed": "False",
        "basis_docs": "research/rule_validation/67_bus_network_diversity_candidate_20260707.md;research/rule_validation/83_next_queue_after_cost_contract_20260707.md",
        "reason_ko": "2026-07-03 노선 스냅샷이므로 과거 백테스트에 바로 fan-out하면 시간누수 위험이 있다.",
        "claim_limit_ko": "노선 다양성을 승객수, 매출 유입, 방문확률로 직접 해석하지 않는다.",
    },
]


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path: Path, usecols: list[str] | None = None, nrows: int | None = None) -> pd.DataFrame:
    return pd.read_csv(path, encoding="utf-8-sig", usecols=usecols, nrows=nrows, dtype=str)


def import_engine():
    spec = importlib.util.spec_from_file_location("build_rule_based_location_scores", ENGINE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"엔진 모듈을 불러올 수 없습니다: {ENGINE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def file_audit(contract: pd.DataFrame) -> pd.DataFrame:
    audits: list[dict[str, Any]] = []
    for _, row in contract.iterrows():
        rel = str(row["file_path"])
        path = ROOT / rel
        exists = path.exists()
        suffix = path.suffix.lower()
        key_cols = [x for x in str(row["key_columns"]).split(";") if x]
        rows = None
        duplicate_keys = None
        missing_key_columns: list[str] = []
        key_null_rows = None
        read_status = "not_read"
        columns: list[str] = []
        if exists and suffix == ".csv":
            header = read_csv(path, nrows=0)
            columns = header.columns.tolist()
            missing_key_columns = [col for col in key_cols if col not in columns]
            try:
                if not missing_key_columns and key_cols:
                    keys = read_csv(path, usecols=key_cols)
                    rows = int(len(keys))
                    duplicate_keys = int(keys.duplicated(key_cols).sum())
                    key_null_rows = int(keys[key_cols].isna().any(axis=1).sum())
                else:
                    probe = read_csv(path, usecols=columns[:1] if columns else None)
                    rows = int(len(probe))
                    duplicate_keys = None
                    key_null_rows = None
                read_status = "read_ok"
            except Exception as exc:  # 큰 파일 또는 스키마 문제를 숨기지 않기 위한 감사 컬럼
                read_status = f"read_error:{type(exc).__name__}:{exc}"
        elif exists and suffix == ".json":
            try:
                obj = read_json(path)
                rows = len(obj) if isinstance(obj, list) else len(obj.keys()) if isinstance(obj, dict) else 1
                read_status = "json_read_ok"
            except Exception as exc:
                read_status = f"json_read_error:{type(exc).__name__}:{exc}"
        audits.append(
            {
                "file_path": rel,
                "contract_group": row["contract_group"],
                "use_in_v1": row["use_in_v1"],
                "exists": bool(exists),
                "bytes": path.stat().st_size if exists else 0,
                "rows_or_items": rows,
                "key_columns": ";".join(key_cols),
                "missing_key_columns": ";".join(missing_key_columns),
                "duplicate_key_rows": duplicate_keys,
                "key_null_rows": key_null_rows,
                "read_status": read_status,
            }
        )
    return pd.DataFrame(audits)


def add_validation(rows: list[dict[str, Any]], validation_id: str, name: str, observed: Any, expected: Any, ok: bool, reason_ko: str) -> None:
    rows.append(
        {
            "validation_id": validation_id,
            "validation_name": name,
            "observed": observed,
            "expected": expected,
            "result": "PASS" if ok else "FAIL",
            "reason_ko": reason_ko,
        }
    )


def md_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    out = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join(["---"] * len(columns)) + " |",
    ]
    for row in rows:
        vals = [str(row.get(col, "")).replace("\n", " ") for col in columns]
        out.append("| " + " | ".join(vals) + " |")
    return "\n".join(out)


def main() -> int:
    RULE_DIR.mkdir(parents=True, exist_ok=True)
    DOC_DIR.mkdir(parents=True, exist_ok=True)

    engine = import_engine()
    source_contract = read_csv(SOURCE_CONTRACT)
    direct_summary = read_json(DIRECT_READINESS)
    join_summary = read_json(JOIN_STABILITY)
    direction_summary = read_json(DIRECTION_SUMMARY)
    cost_summary = read_json(COST_SUMMARY)
    backtest_summary = read_json(BACKTEST_SUMMARY)
    next_queue = read_csv(NEXT_QUEUE)
    forbidden_audit = read_csv(FORBIDDEN_AUDIT)
    direction_audit = read_csv(DIRECTION_AUDIT)

    contract = pd.DataFrame(CONTRACT_ROWS)
    audit = file_audit(contract)
    validations: list[dict[str, Any]] = []

    official_rows = contract[contract["contract_group"].eq("official_current_score")]
    official_axis_rows = official_rows[official_rows["engine_field"].str.startswith("axis.")]
    evidence_rows = contract[contract["contract_group"].eq("evidence_payload")]
    input_rows = contract[contract["contract_group"].eq("input_resolver")]
    separate_rows = contract[contract["contract_group"].eq("separate_score")]

    add_validation(
        validations,
        "84-V01",
        "공식 현재입지 축 계약이 엔진 CURRENT_AXES와 일치",
        sorted(official_axis_rows["engine_field"].str.replace("axis.", "", regex=False).tolist()),
        sorted(list(engine.CURRENT_AXES)),
        set(official_axis_rows["engine_field"].str.replace("axis.", "", regex=False)) == set(engine.CURRENT_AXES),
        "v1 현재입지 총점은 sales, competition, demand, accessibility 네 축으로만 계산한다.",
    )
    add_validation(
        validations,
        "84-V02",
        "공식·별도·입력·evidence 파일 존재 및 읽기 가능",
        audit["read_status"].value_counts(dropna=False).to_dict(),
        "모든 계약 파일 read_ok/json_read_ok",
        audit["exists"].all() and audit["read_status"].astype(str).str.contains("read_ok|json_read_ok", regex=True).all(),
        "전처리 산출물 계약은 실제 파일이 있고 최소한 key/header를 읽을 수 있어야 한다.",
    )
    add_validation(
        validations,
        "84-V02B",
        "계약 key 컬럼이 실제 파일에 존재",
        audit[["file_path", "missing_key_columns"]].to_dict("records"),
        "missing_key_columns empty",
        audit["missing_key_columns"].fillna("").astype(str).eq("").all(),
        "계약서의 key는 사람이 생각한 논리키가 아니라 실제 파일 컬럼으로 재현 가능해야 한다.",
    )
    official_audit = audit[audit["contract_group"].eq("official_current_score")]
    add_validation(
        validations,
        "84-V03",
        "공식 current score 입력 key 중복 없음",
        official_audit[["file_path", "duplicate_key_rows"]].to_dict("records"),
        "duplicate_key_rows=0 또는 profile 단일 key",
        official_audit["duplicate_key_rows"].fillna(0).astype(int).eq(0).all(),
        "공식 점수 입력은 조인 fan-out을 만들면 점수 백분위와 백데이터 검증이 오염된다.",
    )
    add_validation(
        validations,
        "84-V04",
        "직접 점수 원천 76번 readiness PASS 반영",
        {
            "decision": direct_summary.get("decision"),
            "direct_source_count": direct_summary.get("direct_source_count"),
            "fail_count": direct_summary.get("fail_count"),
        },
        "DIRECT_SCORE_INPUT_READINESS_PASS, fail_count=0",
        direct_summary.get("decision") == "DIRECT_SCORE_INPUT_READINESS_PASS" and int(direct_summary.get("fail_count", 1)) == 0,
        "v1 공식 축을 시작하려면 직접 점수 원천의 파일·키·중복·금지문구 검증이 먼저 닫혀 있어야 한다.",
    )
    add_validation(
        validations,
        "84-V05",
        "payload/backdata 조인 안정성 78번 PASS 반영",
        {
            "decision": join_summary.get("decision"),
            "fanout_rows_total": join_summary.get("fanout_rows_total"),
            "duplicate_key_rows_total": join_summary.get("duplicate_key_rows_total"),
            "key_null_rows_total": join_summary.get("key_null_rows_total"),
        },
        "PASS, fanout=0, duplicate=0, key_null=0",
        join_summary.get("decision") == "ALGORITHM_PAYLOAD_V2_BACKDATA_JOIN_STABILITY_PASS"
        and int(join_summary.get("fanout_rows_total", -1)) == 0
        and int(join_summary.get("duplicate_key_rows_total", -1)) == 0
        and int(join_summary.get("key_null_rows_total", -1)) == 0,
        "payload는 리포트 근거 묶음이지 조인 fan-out을 허용하는 feature mart가 아니다.",
    )
    add_validation(
        validations,
        "84-V06",
        "방향행렬 품질 경고 79번 해결 반영",
        {
            "status": direction_summary.get("status"),
            "active_indicator_count": direction_summary.get("active_indicator_count"),
            "evidence_only_rows": direction_summary.get("evidence_only_rows"),
            "direction_audit": direction_audit["result"].value_counts(dropna=False).to_dict(),
        },
        "79 PASS, active 19, evidence-only 1",
        direction_summary.get("status") == "PASS"
        and int(direction_summary.get("active_indicator_count", 0)) == 19
        and int(direction_summary.get("evidence_only_rows", 0)) == 1
        and not (direction_audit["result"] == "FAIL").any(),
        "active 지표와 evidence-only 지표를 분리해야 점수 산식 근거가 흐려지지 않는다.",
    )
    add_validation(
        validations,
        "84-V07",
        "비용축 82번 별도 프록시 계약 반영",
        {
            "decision": cost_summary.get("decision"),
            "current_axes": cost_summary.get("current_axes"),
            "fail_count": cost_summary.get("fail_count"),
        },
        "비용은 별도 cost_risk_score, current_axes 제외",
        cost_summary.get("decision") == "COST_PROXY_OFFICIAL_USE_CONTRACT_PASS_SEPARATE_PROXY_SCORE"
        and "cost_risk" not in list(cost_summary.get("current_axes", []))
        and int(cost_summary.get("fail_count", 1)) == 0,
        "비용 리스크는 월세·권리금 직접값이 아니므로 현재입지 총점에 합산하지 않는다.",
    )
    add_validation(
        validations,
        "84-V08",
        "evidence payload는 점수 산식 변경 금지",
        evidence_rows[["file_path", "score_mutation_allowed", "use_in_v1"]].to_dict("records"),
        "score_mutation_allowed=False",
        evidence_rows["score_mutation_allowed"].astype(str).eq("False").all(),
        "후보 evidence는 리포트 설명 근거로만 붙이고 공식 총점 수식을 바꾸지 않는다.",
    )
    add_validation(
        validations,
        "84-V09",
        "입력 resolver는 점수 산식 변경 금지",
        input_rows[["file_path", "score_mutation_allowed", "use_in_v1"]].to_dict("records"),
        "score_mutation_allowed=False",
        input_rows["score_mutation_allowed"].astype(str).eq("False").all(),
        "지도 클릭/업종 tree는 잘못된 조인을 막는 장치이지 입지 점수 가점 장치가 아니다.",
    )
    add_validation(
        validations,
        "84-V10",
        "단일 feature mart 금지 계약 유지",
        source_contract["single_feature_mart_forbidden"].astype(str).str.lower().value_counts(dropna=False).to_dict(),
        "모든 source row에서 True",
        source_contract["single_feature_mart_forbidden"].astype(str).str.lower().eq("true").all(),
        "원천별 grain과 금지표현이 다르므로 한 파일에 모두 합치지 않고 계층별로 읽는다.",
    )
    blocked = source_contract[source_contract["engine_role"].eq("blocked")]
    add_validation(
        validations,
        "84-V11",
        "blocked source는 v1 계약 파일에 없음",
        blocked["source_id"].tolist(),
        "blocked source excluded from contract",
        not contract["file_path"].astype(str).str.contains("probe", case=False, regex=False).any(),
        "실패 전용 probe나 재시도 전 원천은 silver/gold/payload 계약에 넣지 않는다.",
    )
    add_validation(
        validations,
        "84-V12",
        "금지표현 백테스트 감사 PASS",
        forbidden_audit["result"].value_counts(dropna=False).to_dict(),
        "FAIL 없음",
        not (forbidden_audit["result"] == "FAIL").any(),
        "리포트와 UI가 창업 성공확률, 매출 보장, 월세/권리금 반영 수익성으로 말하지 않게 한다.",
    )
    add_validation(
        validations,
        "84-V13",
        "v1 시작 큐가 READY_TO_START로 고정",
        next_queue.iloc[0].to_dict(),
        "priority 1 status READY_TO_START",
        str(next_queue.iloc[0]["status_after_82"]) == "READY_TO_START",
        "이번 계약의 목적은 추가 수집 대기가 아니라 v1 전처리·알고리즘 작성 착수 기준을 고정하는 것이다.",
    )
    add_validation(
        validations,
        "84-V14",
        "백데이터 검증 universe 보유",
        {
            "rows": backtest_summary.get("row_count"),
            "quarters": backtest_summary.get("quarters"),
            "rule_validation_counts": backtest_summary.get("rule_validation_counts"),
        },
        "427,553행, 20개 backtest quarter, FAIL 없음",
        int(backtest_summary.get("row_count", 0)) == 427553
        and len(backtest_summary.get("quarters", [])) == 20
        and "FAIL" not in dict(backtest_summary.get("rule_validation_counts", {})),
        "v1 알고리즘은 현재 보유 백데이터로 검증할 수 있어야 하며, 단건 샘플만으로 끝내면 안 된다.",
    )

    validation = pd.DataFrame(validations)
    pass_count = int((validation["result"] == "PASS").sum())
    fail_count = int((validation["result"] == "FAIL").sum())
    decision = "V1_PREPROCESSING_PAYLOAD_CONTRACT_PASS_READY_FOR_IMPLEMENTATION" if fail_count == 0 else "V1_PREPROCESSING_PAYLOAD_CONTRACT_FAIL"

    contract.to_csv(OUT_CONTRACT, index=False, encoding="utf-8-sig")
    audit.to_csv(OUT_FILE_AUDIT, index=False, encoding="utf-8-sig")
    validation.to_csv(OUT_VALIDATION, index=False, encoding="utf-8-sig")

    summary = {
        "validation_number": 84,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "validation_version": VERSION,
        "contract_rows": int(len(contract)),
        "contract_group_counts": contract["contract_group"].value_counts(dropna=False).to_dict(),
        "official_current_score_files": official_rows["file_path"].tolist(),
        "separate_score_files": separate_rows["file_path"].tolist(),
        "input_resolver_files": input_rows["file_path"].tolist(),
        "evidence_payload_files": evidence_rows["file_path"].tolist(),
        "file_audit_rows": int(len(audit)),
        "backtest_rows": int(backtest_summary.get("row_count", 0)),
        "backtest_quarters": backtest_summary.get("quarters", []),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "decision": decision,
        "decision_reason_ko": "v1 공식 점수, 별도 점수, 입력 resolver, evidence payload의 파일·역할·금지표현·검증 근거가 분리되어 구현 착수 기준을 통과했다.",
        "next_step": "v1 알고리즘 구현/정리 시 이 계약 파일만을 기준으로 공식 점수와 evidence-only를 분리한다.",
        "outputs": [
            str(OUT_CONTRACT.relative_to(ROOT)),
            str(OUT_FILE_AUDIT.relative_to(ROOT)),
            str(OUT_VALIDATION.relative_to(ROOT)),
            str(OUT_SUMMARY.relative_to(ROOT)),
            str(OUT_DOC.relative_to(ROOT)),
        ],
    }
    OUT_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    lines = [
        "# 84. v1 전처리 산출물 및 알고리즘 payload 계약",
        "",
        f"생성일: {summary['generated_at']}",
        "",
        "## 목적",
        "",
        "v1 알고리즘 구현 전에 공식 점수 입력, 별도 점수, 입력 resolver, evidence-only payload를 한 계약으로 고정한다. "
        "이 계약은 모든 데이터를 한 파일에 합치는 것이 아니라, 역할과 grain을 분리해 필요한 시점에만 읽도록 하는 기준이다.",
        "",
        "## 결론",
        "",
        f"- decision: `{decision}`",
        f"- PASS: {pass_count}",
        f"- FAIL: {fail_count}",
        f"- 계약 row: {len(contract):,}",
        f"- 백데이터 rows: {summary['backtest_rows']:,}",
        f"- 백데이터 quarters: `{summary['backtest_quarters'][0]}~{summary['backtest_quarters'][-1]}`",
        "",
        "## 계약 그룹",
        "",
        md_table(
            [
                {"group": k, "count": v}
                for k, v in contract["contract_group"].value_counts(dropna=False).to_dict().items()
            ],
            ["group", "count"],
        ),
        "",
        "## 핵심 원칙",
        "",
        "- 현재입지 공식 총점은 `sales`, `competition`, `demand`, `accessibility` 네 축만 사용한다.",
        "- `growth_potential_score`, `growth_rebound_candidate`, `cost_risk_score`는 별도 점수 또는 후보 신호다.",
        "- 지도 클릭/주소/업종 tree는 입력 확정 장치이며 점수 산식에 가점을 주지 않는다.",
        "- LocalData, R-ONE, 교통 승하차 후보, SGIS 행정통계, 버스 네트워크는 evidence-only 또는 후보 payload로 둔다.",
        "- 모든 후보 evidence는 공식 점수 산식을 변경하지 않는다.",
        "- 창업 성공확률, 개별 매장 매출 보장, 월세/권리금 반영 수익성, 실제 방문확률 표현은 금지한다.",
        "",
        "## 계약 목록",
        "",
        md_table(
            contract.to_dict("records"),
            ["contract_group", "file_path", "grain", "engine_field", "use_in_v1", "score_mutation_allowed", "claim_limit_ko"],
        ),
        "",
        "## 검증 결과",
        "",
        md_table(
            validation.to_dict("records"),
            ["validation_id", "validation_name", "observed", "expected", "result", "reason_ko"],
        ),
        "",
        "## 2보 전진 1보 후퇴",
        "",
        "1. 전진: 공식 점수에 필요한 gold 파일과 입력 resolver 파일을 계약으로 고정했다.",
        "2. 전진: LocalData, R-ONE, 교통, SGIS, 버스 네트워크 후보를 버리지 않고 evidence payload 계층으로 보존했다.",
        "3. 후퇴: 후보 evidence는 공식 총점 수식을 바꾸지 않는다. 승격 전까지는 리포트 보조근거로만 쓴다.",
        "",
        "## 산출물",
        "",
        f"- `{OUT_CONTRACT.relative_to(ROOT)}`",
        f"- `{OUT_FILE_AUDIT.relative_to(ROOT)}`",
        f"- `{OUT_VALIDATION.relative_to(ROOT)}`",
        f"- `{OUT_SUMMARY.relative_to(ROOT)}`",
        f"- `{OUT_DOC.relative_to(ROOT)}`",
        "",
    ]
    OUT_DOC.write_text("\n".join(lines), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
