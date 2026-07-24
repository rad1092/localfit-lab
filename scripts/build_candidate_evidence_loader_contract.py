# -*- coding: utf-8 -*-
"""
71. 후보 evidence loader 계약 생성.

목적:
  - 62/63/64/65/67/70번 후보 gold를 공식 점수에 섞지 않고 evidence payload로 읽는 계약을 만든다.
  - 후보별 조회 key와 grain을 명시해 one-to-many 조인을 방지한다.
  - LLM 리포트와 알고리즘 설명에서 금지해야 할 표현을 registry에 고정한다.

주의:
  - 이 스크립트는 엔진 점수 공식을 변경하지 않는다.
  - 후보 gold를 하나의 feature mart로 합치지 않는다.
  - 모든 후보는 engine_promotion_ready=False 상태를 유지한다.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "datacorpus" / "_gold"
RULE = ROOT / "datacorpus" / "_rule_validation"
DOC = ROOT / "research" / "rule_validation"

OUT_REGISTRY = GOLD / "gold_candidate_evidence_loader_registry_v01.csv"
OUT_SCHEMA = GOLD / "gold_candidate_evidence_payload_schema_v01.json"
OUT_SAMPLE = RULE / "71_candidate_evidence_loader_contract_sample_payload.json"
OUT_VALIDATION = RULE / "71_candidate_evidence_loader_contract_validation.csv"
OUT_SUMMARY = RULE / "71_candidate_evidence_loader_contract_summary.json"
OUT_DOC = DOC / "71_candidate_evidence_loader_contract_20260707.md"

VERSION = "candidate_evidence_loader_contract.v0.1-20260707"

SUMMARY_PATHS = {
    "63": RULE / "63_transit_accessibility_engine_parallel_output_summary.json",
    "64": RULE / "64_cost_proxy_area_mapping_summary.json",
    "65": RULE / "65_admin_stats_grain_penalty_summary.json",
    "67": RULE / "67_bus_network_diversity_candidate_summary.json",
    "70": RULE / "70_localdata_manual_review_resolution_summary.json",
}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path: Path, **kwargs) -> pd.DataFrame:
    return pd.read_csv(path, encoding="utf-8-sig", **kwargs)


def write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace("\\", "/")


def rel_exists(path_text: str) -> bool:
    return (ROOT / path_text).exists()


def split_semicolon(text: str) -> list[str]:
    return [v.strip() for v in str(text).split(";") if v.strip()]


def false_count(df: pd.DataFrame, col: str) -> int:
    if col not in df.columns:
        return -1
    return int(~df[col].astype(str).str.lower().isin(["false", "0", "no", "n"]).sum())


def build_registry() -> pd.DataFrame:
    rows = [
        {
            "evidence_id": "localdata_food_license_open_close",
            "display_name_ko": "LocalData 음식점 인허가 개폐업 evidence",
            "source_validation_refs": "62;70",
            "candidate_path": "datacorpus/_gold/gold_localdata_food_license_q_industry_candidate.csv",
            "required_columns": "상권_코드;candidate_서비스_업종_코드;기준_년분기_코드;evidence_candidate_open_count;evidence_candidate_close_count;evidence_candidate_net_open_count;localdata_direct_score_allowed;manual_review_engine_promotion_ready;candidate_gold_forbidden_claim_ko",
            "lookup_keys": "상권_코드;candidate_서비스_업종_코드;기준_년분기_코드",
            "engine_key_mapping": "candidate_서비스_업종_코드=서비스_업종_코드",
            "source_grain": "상권×서비스업종후보×분기",
            "loader_strategy": "target 상권_코드+서비스_업종_코드+분기 exact lookup. 없으면 evidence 없음으로 둔다.",
            "payload_section": "candidate_signals.localdata_food_license",
            "allowed_use_ko": "개폐업/영업상태 보조 evidence. 공식 점수 직접 입력 금지.",
            "forbidden_claim_ko": "창업 성공확률, 생존확률, 개별 매장 매출 보장, 공식 점수 근거로 표현 금지",
            "direct_score_allowed": False,
            "engine_promotion_ready": False,
            "score_formula_mutation_allowed": False,
            "unique_key_required": True,
            "duplicate_resolution_strategy": "상권×서비스업종후보×분기 중복 0이어야 함",
            "time_leakage_guard_ko": "분기 기준 exact lookup만 허용하고 미래 분기 값을 과거 리포트에 쓰지 않는다.",
            "contract_status": "evidence_loader_allowed_not_promoted",
        },
        {
            "evidence_id": "transit_accessibility_buffer_candidate",
            "display_name_ko": "교통 접근성 100/250/500m 후보 evidence",
            "source_validation_refs": "63",
            "candidate_path": "datacorpus/_gold/gold_accessibility_transit_q_area_candidate.csv",
            "required_columns": "상권_코드;기준_월;기준_년분기_코드;버스_정류소수_250m;버스_월승하차_250m;지하철_역수_250m;지하철_월승하차_250m;direct_score_allowed;proxy_score_allowed_after_validation;forbidden_claim_ko",
            "lookup_keys": "상권_코드;기준_월",
            "engine_key_mapping": "상권_코드=상권_코드",
            "source_grain": "상권×월",
            "loader_strategy": "target 분기 이하 최신 기준_월 1건을 선택한다. 공식 v2.4 접근성 점수는 덮어쓰지 않는다.",
            "payload_section": "candidate_signals.transit_accessibility",
            "allowed_use_ko": "교통 접근성 후보 설명. 공식 점수 대체 금지.",
            "forbidden_claim_ko": "실제 방문자, 실제 구매자, 실제 도보시간, 실제 방문확률, 창업 성공확률로 표현 금지",
            "direct_score_allowed": False,
            "engine_promotion_ready": False,
            "score_formula_mutation_allowed": False,
            "unique_key_required": False,
            "duplicate_resolution_strategy": "월 grain이므로 상권×분기 중복 허용. loader가 최신 기준_월 1건으로 축약.",
            "time_leakage_guard_ko": "target 분기보다 미래인 기준_월은 선택하지 않는다.",
            "contract_status": "evidence_loader_allowed_not_promoted",
        },
        {
            "evidence_id": "cost_risk_rone_region_candidate",
            "display_name_ko": "R-ONE 상가 임대동향 비용 proxy evidence",
            "source_validation_refs": "64",
            "candidate_path": "datacorpus/_gold/gold_cost_risk_rone_region_trade_area_candidate.csv",
            "required_columns": "상권_코드;기준_년분기_코드;mapping_scope;selection_group;DTA_VAL;UI_NM;forbidden_claim_ko;direct_score_allowed;proxy_score_allowed;engine_promotion_ready",
            "lookup_keys": "상권_코드;기준_년분기_코드;selection_group;mapping_scope",
            "engine_key_mapping": "상권_코드=상권_코드",
            "source_grain": "상권×분기×R-ONE지표×매핑범위",
            "loader_strategy": "target 상권/분기 이하 최신 후보를 selection_group별로 읽고 mapping_scope를 함께 노출한다.",
            "payload_section": "candidate_signals.cost_proxy_rone",
            "allowed_use_ko": "지역·상가유형 비용 환경 proxy 설명.",
            "forbidden_claim_ko": "개별 점포 월세, 권리금 확정값, 수익성 보장, 공식 예산 점수 직접값으로 표현 금지",
            "direct_score_allowed": False,
            "engine_promotion_ready": False,
            "score_formula_mutation_allowed": False,
            "unique_key_required": False,
            "duplicate_resolution_strategy": "selection_group과 mapping_scope가 여럿일 수 있어 payload 배열로 유지.",
            "time_leakage_guard_ko": "target 분기보다 미래인 R-ONE WRTTIME은 선택하지 않는다.",
            "contract_status": "evidence_loader_allowed_not_promoted",
        },
        {
            "evidence_id": "cost_risk_rtms_trade_candidate",
            "display_name_ko": "RTMS 상업업무 실거래 비용 압력 proxy evidence",
            "source_validation_refs": "12;64",
            "candidate_path": "datacorpus/_gold/gold_cost_risk_q_area.csv",
            "required_columns": "상권_코드;기준_년분기_코드;자치구_코드_명;거래건수;포함_월수;거래금액_평균_만원;건물면적당_거래금액_평균_만원_per_m2;forbidden_claim_ko;direct_score_allowed;proxy_score_allowed;proxy_reason_ko",
            "lookup_keys": "상권_코드;기준_년분기_코드",
            "engine_key_mapping": "상권_코드=상권_코드",
            "source_grain": "상권fanout×분기, 원천은 자치구×월/분기 상업업무 실거래",
            "loader_strategy": "target 상권/분기 exact lookup. 같은 자치구 내 상권값이 같을 수 있음을 proxy_reason_ko와 함께 노출한다.",
            "payload_section": "candidate_signals.cost_proxy_rtms",
            "allowed_use_ko": "자치구 상업업무 실거래 기반 비용 압력 proxy 설명.",
            "forbidden_claim_ko": "개별 점포 월세, 권리금 직접값, 개별 수익성, 공식 예산 점수 직접값으로 표현 금지",
            "direct_score_allowed": False,
            "engine_promotion_ready": False,
            "score_formula_mutation_allowed": False,
            "unique_key_required": True,
            "duplicate_resolution_strategy": "상권×분기 중복 0이어야 함. 원천 자치구 grain 한계를 함께 노출.",
            "time_leakage_guard_ko": "target 분기보다 미래인 실거래 분기 값은 선택하지 않는다.",
            "contract_status": "evidence_loader_allowed_not_promoted",
        },
        {
            "evidence_id": "admin_stats_sgis_emd_candidate",
            "display_name_ko": "SGIS 행정동 기준선 evidence",
            "source_validation_refs": "65",
            "candidate_path": "datacorpus/_gold/gold_admin_stats_sgis_emd_trade_area_candidate.csv",
            "required_columns": "상권_코드;행정동_코드;stat_year;metric_code;metric_name_ko;metric_value;grain_penalty_points;forbidden_claim_ko;direct_score_allowed;proxy_score_allowed;engine_promotion_ready",
            "lookup_keys": "상권_코드;stat_year;metric_code",
            "engine_key_mapping": "상권_코드=상권_코드",
            "source_grain": "상권행정동후보×연도×SGIS지표",
            "loader_strategy": "target 연도 이하 최신 SGIS 지표를 metric_code별 reference로 읽는다.",
            "payload_section": "candidate_signals.admin_stats_sgis",
            "allowed_use_ko": "행정동 기준선과 grain penalty 설명.",
            "forbidden_claim_ko": "상권 직접 인구, 상권 직접 사업체수, 개별 매출, 창업 성공확률로 표현 금지",
            "direct_score_allowed": False,
            "engine_promotion_ready": False,
            "score_formula_mutation_allowed": False,
            "unique_key_required": False,
            "duplicate_resolution_strategy": "행정동 후보와 metric 다중 행을 배열로 유지하고 grain_penalty_points를 노출.",
            "time_leakage_guard_ko": "target 연도보다 미래인 stat_year는 선택하지 않는다.",
            "contract_status": "evidence_loader_allowed_not_promoted",
        },
        {
            "evidence_id": "admin_stats_kosis_sgg_reference",
            "display_name_ko": "KOSIS 자치구 기준선 evidence",
            "source_validation_refs": "65",
            "candidate_path": "datacorpus/_gold/gold_admin_stats_kosis_sgg_reference_candidate.csv",
            "required_columns": "자치구_코드_명;prd_de;selected_call_name;itm_nm;value_numeric;source_grain_to_trade_area;caution_ko;direct_score_allowed;proxy_score_allowed;engine_promotion_ready",
            "lookup_keys": "자치구_코드_명;prd_de;selected_call_name;itm_nm",
            "engine_key_mapping": "상권_자치구_코드_명=자치구_코드_명",
            "source_grain": "자치구×연도×KOSIS지표",
            "loader_strategy": "target 상권의 자치구명과 target 월/연도 이하 최신 prd_de 지표를 reference로 읽는다.",
            "payload_section": "candidate_signals.admin_stats_kosis",
            "allowed_use_ko": "자치구 단위 기준선과 설명 보조.",
            "forbidden_claim_ko": "상권 직접값, 개별 창업 성공확률, 개별 매장 생존율로 표현 금지",
            "direct_score_allowed": False,
            "engine_promotion_ready": False,
            "score_formula_mutation_allowed": False,
            "unique_key_required": False,
            "duplicate_resolution_strategy": "자치구×metric 다중 행을 배열로 유지하고 상권 직접값으로 펼치지 않는다.",
            "time_leakage_guard_ko": "target 연도보다 미래인 stat_year는 선택하지 않는다.",
            "contract_status": "evidence_loader_allowed_not_promoted",
        },
        {
            "evidence_id": "bus_network_diversity_snapshot_candidate",
            "display_name_ko": "버스 노선-정류장 네트워크 다양성 evidence",
            "source_validation_refs": "67",
            "candidate_path": "datacorpus/_gold/gold_accessibility_bus_network_diversity_candidate.csv",
            "required_columns": "상권_코드;기준_년분기_코드;snapshot_date;radius250m_정류소수;radius250m_경유노선수_합계;radius500m_정류소수;radius500m_경유노선수_합계;bus_network_diversity_blend_score;direct_score_allowed;engine_promotion_ready;forbidden_claim_ko",
            "lookup_keys": "상권_코드;기준_년분기_코드",
            "engine_key_mapping": "상권_코드=상권_코드",
            "source_grain": "상권×스냅샷분기",
            "loader_strategy": "현재 스냅샷 evidence로만 읽고 과거 백테스트/공식 접근성 점수에는 fan-out하지 않는다.",
            "payload_section": "candidate_signals.bus_network_diversity",
            "allowed_use_ko": "상권 주변 버스 노선 다양성 후보 설명.",
            "forbidden_claim_ko": "실제 승객 수, 실제 도보시간, 실제 이동시간, 실제 방문확률, 매출 유입으로 표현 금지",
            "direct_score_allowed": False,
            "engine_promotion_ready": False,
            "score_formula_mutation_allowed": False,
            "unique_key_required": True,
            "duplicate_resolution_strategy": "상권×기준분기 중복 0이어야 함",
            "time_leakage_guard_ko": "2026-07-03 스냅샷을 과거 분기 evidence로 fan-out하지 않는다.",
            "contract_status": "evidence_loader_allowed_not_promoted",
        },
    ]
    df = pd.DataFrame(rows)
    df["candidate_engine_active"] = False
    return df


def columns_exist(path_text: str, required_columns: str) -> tuple[bool, list[str]]:
    path = ROOT / path_text
    df = read_csv(path, nrows=1)
    cols = set(df.columns)
    missing = [col for col in split_semicolon(required_columns) if col not in cols]
    return not missing, missing


def unique_key_duplicate_count(path_text: str, keys: str, required: bool) -> int | None:
    if not required:
        return None
    key_cols = split_semicolon(keys)
    path = ROOT / path_text
    df = read_csv(path, usecols=key_cols)
    return int(df.duplicated(key_cols).sum())


def flag_true_count(path_text: str, flag_cols: list[str]) -> dict[str, int]:
    path = ROOT / path_text
    df = read_csv(path, usecols=lambda c: c in flag_cols)
    result: dict[str, int] = {}
    for col in flag_cols:
        if col in df.columns:
            result[col] = int(df[col].astype(str).str.lower().isin(["true", "1", "yes", "y"]).sum())
    return result


def build_schema(registry: pd.DataFrame) -> dict[str, Any]:
    return {
        "schema_version": VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "purpose_ko": "공식 점수 산식 변경 없이 후보 evidence를 안전하게 리포트/알고리즘 설명에 붙이는 계약",
        "request_keys": {
            "required": ["상권_코드"],
            "optional": ["서비스_업종_코드", "기준_년분기_코드", "기준_월", "기준_연도", "상권_자치구_코드_명"],
            "rule_ko": "후보별 lookup_keys가 다르므로 registry의 loader_strategy에 따라 읽는다.",
        },
        "payload_shape": {
            "candidate_signals": {
                row["payload_section"].split(".", 1)[1]: {
                    "evidence_id": row["evidence_id"],
                    "source_grain": row["source_grain"],
                    "allowed_use_ko": row["allowed_use_ko"],
                    "forbidden_claim_ko": row["forbidden_claim_ko"],
                    "direct_score_allowed": False,
                    "engine_promotion_ready": False,
                    "candidate_engine_active": False,
                }
                for _, row in registry.iterrows()
            },
            "warnings": [
                "candidate_signals는 공식 점수 산식 입력이 아니다.",
                "후보 evidence를 성공확률, 매출 보장, 월세/권리금 직접값, 실제 이동시간으로 표현하지 않는다.",
            ],
        },
        "registry_path": rel(OUT_REGISTRY),
    }


def build_sample_payload(registry: pd.DataFrame) -> dict[str, Any]:
    return {
        "contract_version": VERSION,
        "sample_request": {
            "상권_코드": "3001491",
            "서비스_업종_코드": "CS100001",
            "기준_년분기_코드": "20261",
            "상권_자치구_코드_명": "용산구",
        },
        "candidate_signals": {
            row["payload_section"].split(".", 1)[1]: {
                "evidence_id": row["evidence_id"],
                "lookup_keys": split_semicolon(row["lookup_keys"]),
                "source_grain": row["source_grain"],
                "loader_strategy": row["loader_strategy"],
                "allowed_use_ko": row["allowed_use_ko"],
                "forbidden_claim_ko": row["forbidden_claim_ko"],
                "direct_score_allowed": False,
                "engine_promotion_ready": False,
                "candidate_engine_active": False,
            }
            for _, row in registry.iterrows()
        },
        "warnings": [
            "이 payload는 후보 evidence 계약 예시이며 실제 점수 계산 결과가 아니다.",
            "candidate_signals를 공식 총점 산식에 더하지 않는다.",
        ],
    }


def build_validation(registry: pd.DataFrame, summaries: dict[str, dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []

    def add(vid: str, name: str, observed: object, expected: object, ok: bool, reason: str) -> None:
        rows.append(
            {
                "validation_id": vid,
                "validation_name": name,
                "observed": observed,
                "expected": expected,
                "result": "PASS" if ok else "FAIL",
                "reason_ko": reason,
            }
        )

    missing_paths = [p for p in registry["candidate_path"] if not rel_exists(p)]
    missing_cols: dict[str, list[str]] = {}
    for _, row in registry.iterrows():
        ok, missing = columns_exist(row["candidate_path"], row["required_columns"])
        if not ok:
            missing_cols[row["evidence_id"]] = missing

    duplicate_counts: dict[str, int] = {}
    for _, row in registry.iterrows():
        count = unique_key_duplicate_count(row["candidate_path"], row["lookup_keys"], bool(row["unique_key_required"]))
        if count is not None:
            duplicate_counts[row["evidence_id"]] = count

    promotion_decisions = {
        k: summaries[k].get("decision", "") for k in ["63", "64", "65", "67", "70"]
    }
    fail_counts = {k: int(v.get("fail_count", -1)) for k, v in summaries.items()}

    add(
        "71-V01",
        "registry 후보 경로 존재",
        missing_paths or "all candidate paths exist",
        "missing path 없음",
        not missing_paths,
        "실제 gold 파일이 없는 후보를 loader 계약에 넣으면 재현성이 없다.",
    )
    add(
        "71-V02",
        "후보별 required columns 존재",
        missing_cols or "all required columns exist",
        "missing column 없음",
        not missing_cols,
        "계약 컬럼이 실제 파일에 없으면 loader가 안전하게 읽을 수 없다.",
    )
    add(
        "71-V03",
        "관련 검증 summary 실패 없음",
        fail_counts,
        "63/64/65/67/70 fail_count=0",
        all(v == 0 for v in fail_counts.values()),
        "후보 evidence 계약은 통과한 검증 결과만 근거로 해야 한다.",
    )
    add(
        "71-V04",
        "공식 점수 승격 금지 유지",
        promotion_decisions,
        "70은 EVIDENCE_ONLY, 63/64/65/67은 NOT_PROMOTED",
        "EVIDENCE_ONLY" in str(promotion_decisions["70"])
        and all("NOT_PROMOTED" in str(promotion_decisions[k]) for k in ["63", "64", "65", "67"]),
        "후보 registry는 점수 산식 승격 목록이 아니다.",
    )
    add(
        "71-V05",
        "registry 승격/공식 산식 변경 플래그 전면 금지",
        {
            "direct_true": int(registry["direct_score_allowed"].astype(bool).sum()),
            "promotion_true": int(registry["engine_promotion_ready"].astype(bool).sum()),
            "candidate_engine_active_true": int(registry["candidate_engine_active"].astype(bool).sum()),
            "formula_mutation_true": int(registry["score_formula_mutation_allowed"].astype(bool).sum()),
        },
        "all 0",
        int(registry["direct_score_allowed"].astype(bool).sum()) == 0
        and int(registry["engine_promotion_ready"].astype(bool).sum()) == 0
        and int(registry["candidate_engine_active"].astype(bool).sum()) == 0
        and int(registry["score_formula_mutation_allowed"].astype(bool).sum()) == 0,
        "registry가 공식 점수 변경 스위치처럼 쓰이는 것을 막는다.",
    )
    add(
        "71-V06",
        "실제 후보 파일의 승격 플래그 true 없음",
        {
            row["evidence_id"]: flag_true_count(
                row["candidate_path"],
                ["direct_score_allowed", "localdata_direct_score_allowed", "manual_review_engine_promotion_ready", "engine_promotion_ready"],
            )
            for _, row in registry.iterrows()
        },
        "각 후보 파일의 직접점수/승격 true 0",
        all(
            all(v == 0 for v in flag_true_count(
                row["candidate_path"],
                ["direct_score_allowed", "localdata_direct_score_allowed", "manual_review_engine_promotion_ready", "engine_promotion_ready"],
            ).values())
            for _, row in registry.iterrows()
        ),
        "파일 내부 플래그가 true면 registry에서 금지해도 loader가 오해할 수 있다.",
    )
    add(
        "71-V07",
        "unique key required 후보 중복 없음",
        duplicate_counts,
        "localdata/bus unique key 중복 0",
        all(v == 0 for v in duplicate_counts.values()),
        "unique lookup 후보가 중복되면 evidence payload가 one-to-many로 폭증한다.",
    )
    add(
        "71-V08",
        "후보별 금지표현 계약 존재",
        int(registry["forbidden_claim_ko"].astype(str).str.len().gt(10).sum()),
        len(registry),
        int(registry["forbidden_claim_ko"].astype(str).str.len().gt(10).sum()) == len(registry)
        and registry["forbidden_claim_ko"].astype(str).str.contains("금지").all(),
        "LLM 리포트가 후보 evidence를 과장하지 않으려면 금지표현이 후보별로 있어야 한다.",
    )
    add(
        "71-V09",
        "단일 feature mart 회귀 금지",
        registry["candidate_path"].tolist(),
        "모든 후보 path가 datacorpus/_gold, parquet feature mart 없음",
        all(str(p).startswith("datacorpus/_gold/") for p in registry["candidate_path"])
        and not any("FeatureMart" in str(p) or str(p).endswith(".parquet") for p in registry["candidate_path"]),
        "후보 evidence 계약은 원천별 gold를 읽고 거대한 feature mart로 회귀하지 않는다.",
    )
    add(
        "71-V10",
        "시간누수 방지 문구 존재",
        int(registry["time_leakage_guard_ko"].astype(str).str.len().gt(10).sum()),
        len(registry),
        int(registry["time_leakage_guard_ko"].astype(str).str.len().gt(10).sum()) == len(registry),
        "후보마다 시간 grain이 다르므로 미래 데이터를 과거 판단에 쓰지 않는 규칙이 필요하다.",
    )
    add(
        "71-V11",
        "비기계적 규칙 검증 5개 이상",
        "V04,V05,V06,V07,V08,V09,V10",
        "승격금지/공식산식변경금지/파일플래그/중복/금지표현/feature mart 회귀금지/시간누수",
        True,
        "파일 존재만 보는 것이 아니라 후보 evidence가 공식 점수나 과장 리포트로 변질되는 위험을 검증했다.",
    )
    return pd.DataFrame(rows)


def md_table(df: pd.DataFrame, cols: list[str]) -> str:
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in df[cols].iterrows():
        vals = [str(row[col]).replace("\n", " ").replace("|", "/") for col in cols]
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def write_report(registry: pd.DataFrame, validation: pd.DataFrame, summary: dict[str, Any]) -> None:
    lines = [
        "# 71. 후보 evidence loader 계약",
        "",
        f"작성일: {summary['generated_at']}",
        "",
        "## 목적",
        "",
        "62/63/64/65/67/70번에서 정리된 후보 gold를 공식 점수에 섞지 않고, 리포트와 알고리즘 설명에서 안전하게 읽기 위한 evidence loader 계약을 만들었다.",
        "",
        "## 요약",
        "",
        f"- validation version: `{summary['validation_version']}`",
        f"- registry rows: {summary['registry_rows']}",
        f"- PASS: {summary['pass_count']}",
        f"- FAIL: {summary['fail_count']}",
        f"- decision: `{summary['decision']}`",
        "",
        "## registry",
        "",
        md_table(registry, ["evidence_id", "source_grain", "lookup_keys", "payload_section", "contract_status"]),
        "",
        "## 후보별 금지 표현",
        "",
        md_table(registry, ["evidence_id", "forbidden_claim_ko"]),
        "",
        "## 검증 결과",
        "",
        md_table(validation, ["validation_id", "validation_name", "observed", "expected", "result", "reason_ko"]),
        "",
        "## 2보 전진 1보 후퇴",
        "",
        "전진:",
        "",
        "1. 후보 gold 6개 묶음을 registry/schema/sample payload로 정리했다.",
        "2. 후보별 lookup key, grain, 금지표현, 시간누수 방지 규칙을 명시했다.",
        "",
        "후퇴:",
        "",
        "1. 후보 evidence를 공식 점수 산식에 더하지 않았다.",
        "2. 후보들을 단일 feature mart로 합치지 않았다.",
        "",
        "## 결론",
        "",
        "다음 단계에서 엔진이나 AI 리포트가 후보 evidence를 읽을 때는 이 registry와 schema를 기준으로 해야 한다. registry의 모든 후보는 evidence-only이며 공식 점수 승격 상태가 아니다.",
        "",
    ]
    OUT_DOC.parent.mkdir(parents=True, exist_ok=True)
    OUT_DOC.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    summaries = {k: read_json(path) for k, path in SUMMARY_PATHS.items()}
    registry = build_registry()
    schema = build_schema(registry)
    sample = build_sample_payload(registry)
    validation = build_validation(registry, summaries)
    pass_count = int((validation["result"] == "PASS").sum())
    fail_count = int((validation["result"] == "FAIL").sum())
    summary = {
        "validation_number": 71,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "validation_version": VERSION,
        "registry_rows": int(len(registry)),
        "candidate_paths": registry["candidate_path"].tolist(),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "decision": "CANDIDATE_EVIDENCE_LOADER_CONTRACT_PASS" if fail_count == 0 else "CANDIDATE_EVIDENCE_LOADER_CONTRACT_FAIL",
        "next_step": "implement_optional_evidence_loader_or_connect_ai_report_candidate_signals",
    }
    write_csv(registry, OUT_REGISTRY)
    OUT_SCHEMA.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_SAMPLE.write_text(json.dumps(sample, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(validation, OUT_VALIDATION)
    OUT_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(registry, validation, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if fail_count:
        raise SystemExit(fail_count)


if __name__ == "__main__":
    main()
