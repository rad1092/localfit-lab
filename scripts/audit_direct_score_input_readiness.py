# -*- coding: utf-8 -*-
"""
76. direct score input 6개 원천 준비도 감사.

목적:
  - 75번 실행계약에서 direct_score_input으로 분류한 서울 상권분석 직접 원천 6개를 파일 단위로 검산한다.
  - 키, 기간, 중복, 핵심 결측, 음수, 금지표현, direct/proxy 플래그를 확인한다.
  - 공식 직접 원천과 후보/프록시 산출물을 섞지 않고 각 gold의 역할을 분리한다.

주의:
  - 이 스크립트는 새 점수 산식을 만들지 않는다.
  - 결측을 임의 0으로 채우지 않는다. 조건부 파생지표 결측은 상태 컬럼과 함께 보존한다.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SILVER = ROOT / "datacorpus" / "_silver"
GOLD = ROOT / "datacorpus" / "_gold"
RULE = ROOT / "datacorpus" / "_rule_validation"
DOC = ROOT / "research" / "rule_validation"

CONTRACT_75 = RULE / "75_preprocessing_file_execution_contract.csv"
OUT_FILE_AUDIT = RULE / "76_direct_score_input_file_readiness_audit.csv"
OUT_SOURCE_AUDIT = RULE / "76_direct_score_input_source_readiness_audit.csv"
OUT_VALIDATION = RULE / "76_direct_score_input_readiness_validation.csv"
OUT_SUMMARY = RULE / "76_direct_score_input_readiness_summary.json"
OUT_DOC = DOC / "76_direct_score_input_readiness_20260707.md"

VERSION = "direct_score_input_readiness.v0.1-20260707"


DIRECT_SOURCES = [
    "seoul_sales_trade_area",
    "seoul_store_trade_area",
    "seoul_floating_population_trade_area",
    "seoul_resident_worker_population_trade_area",
    "seoul_facility_trade_area",
    "seoul_trade_area_change_index",
]

RESEARCH_BASIS_DOCS = [
    ROOT / "research" / "알고리즘_명세_v2_20260704.md",
    DOC / "03_sales_store_silver_validation_20260703.md",
    DOC / "04_population_silver_validation_20260703.md",
    DOC / "05_change_index_silver_validation_20260703.md",
    DOC / "06_facility_silver_validation_20260703.md",
    DOC / "23_gold_preprocessing_validation_20260704.md",
    DOC / "24_gold_based_score_engine_validation_20260704.md",
    DOC / "50_sales_ticket_engine_patch_validation_20260707.md",
    DOC / "52_preprocessing_file_gate_validation_20260707.md",
    DOC / "75_preprocessing_file_execution_contract_20260707.md",
]

EXPECTED_GOLD_ROWS = {
    "gold_sales_strength_q_industry.csv": 619546,
    "gold_competition_q_industry.csv": 1604844,
    "gold_demand_q_area.csv": 34650,
    "gold_accessibility_q_area.csv": 34650,
    "gold_growth_stability_q_industry.csv": 619546,
}


FILE_SPECS: list[dict[str, Any]] = [
    {
        "source_id": "seoul_sales_trade_area",
        "layer": "silver",
        "path": SILVER / "silver_sales_trade_area_q_industry.csv",
        "grain": "상권×업종×분기",
        "key_cols": ["기준_년분기_코드", "상권_코드", "서비스_업종_코드"],
        "core_nonnegative_cols": ["당월_매출_금액", "당월_매출_건수"],
        "derived_cols": [],
        "status_cols": ["forbidden_claim_ko", "directness_level"],
    },
    {
        "source_id": "seoul_sales_trade_area",
        "layer": "gold",
        "path": GOLD / "gold_sales_strength_q_industry.csv",
        "grain": "상권×업종×분기",
        "key_cols": ["기준_년분기_코드", "상권_코드", "서비스_업종_코드"],
        "core_nonnegative_cols": ["당월_매출_금액", "당월_매출_건수"],
        "derived_cols": ["점포당_매출_금액", "객단가_추정_금액"],
        "status_cols": ["store_join_status", "forbidden_claim_ko", "direct_score_allowed", "proxy_score_allowed"],
    },
    {
        "source_id": "seoul_store_trade_area",
        "layer": "silver",
        "path": SILVER / "silver_store_trade_area_q_industry.csv",
        "grain": "상권×업종×분기",
        "key_cols": ["기준_년분기_코드", "상권_코드", "서비스_업종_코드"],
        "core_nonnegative_cols": ["유사_업종_점포_수", "점포_수", "개업_율", "폐업_률"],
        "derived_cols": [],
        "status_cols": ["forbidden_claim_ko", "directness_level"],
    },
    {
        "source_id": "seoul_store_trade_area",
        "layer": "gold",
        "path": GOLD / "gold_competition_q_industry.csv",
        "grain": "상권×업종×분기",
        "key_cols": ["기준_년분기_코드", "상권_코드", "서비스_업종_코드"],
        "core_nonnegative_cols": ["유사_업종_점포_수", "점포_수", "개업_율", "폐업_률"],
        "derived_cols": ["동종_후보소분류_점포수", "유사_후보중분류_점포수"],
        "status_cols": ["score_use_status", "mapping_review_required", "forbidden_claim_ko", "direct_score_allowed", "proxy_score_allowed"],
    },
    {
        "source_id": "seoul_floating_population_trade_area",
        "layer": "silver",
        "path": SILVER / "silver_floating_population_trade_area_q.csv",
        "grain": "상권×분기",
        "key_cols": ["기준_년분기_코드", "상권_코드"],
        "core_nonnegative_cols": ["총_유동인구_수"],
        "derived_cols": [],
        "status_cols": ["forbidden_claim_ko", "directness_level"],
    },
    {
        "source_id": "seoul_resident_worker_population_trade_area",
        "layer": "silver",
        "path": SILVER / "silver_resident_population_trade_area_q.csv",
        "grain": "상권×분기",
        "key_cols": ["기준_년분기_코드", "상권_코드"],
        "core_nonnegative_cols": ["총_상주인구_수", "총_가구_수"],
        "derived_cols": [],
        "status_cols": ["forbidden_claim_ko", "directness_level"],
    },
    {
        "source_id": "seoul_resident_worker_population_trade_area",
        "layer": "silver",
        "path": SILVER / "silver_worker_population_trade_area_q.csv",
        "grain": "상권×분기",
        "key_cols": ["기준_년분기_코드", "상권_코드"],
        "core_nonnegative_cols": ["총_직장인구_수"],
        "derived_cols": [],
        "status_cols": ["forbidden_claim_ko", "directness_level"],
    },
    {
        "source_id": "seoul_floating_population_trade_area;seoul_resident_worker_population_trade_area",
        "layer": "gold",
        "path": GOLD / "gold_demand_q_area.csv",
        "grain": "상권×분기",
        "key_cols": ["기준_년분기_코드", "상권_코드"],
        "core_nonnegative_cols": ["총_기초수요_프록시"],
        "derived_cols": ["총_유동인구_수", "총_상주인구_수", "총_직장인구_수"],
        "status_cols": ["유동인구_존재", "상주인구_존재", "직장인구_존재", "수요원천_존재_개수", "forbidden_claim_ko", "direct_score_allowed", "proxy_score_allowed"],
    },
    {
        "source_id": "seoul_facility_trade_area",
        "layer": "silver",
        "path": SILVER / "silver_facility_trade_area_q.csv",
        "grain": "상권×분기",
        "key_cols": ["기준_년분기_코드", "상권_코드"],
        "core_nonnegative_cols": ["총_집객시설_수", "철도역_수", "버스터미널_수", "지하철역_수", "버스정류장_수"],
        "derived_cols": [],
        "status_cols": ["forbidden_claim_ko", "directness_level"],
    },
    {
        "source_id": "seoul_facility_trade_area",
        "layer": "gold",
        "path": GOLD / "gold_accessibility_q_area.csv",
        "grain": "상권×분기",
        "key_cols": ["기준_년분기_코드", "상권_코드"],
        "core_nonnegative_cols": [],
        "derived_cols": ["총_집객시설_수", "교통결절_시설수", "버스정류장_수"],
        "status_cols": ["facility_observed", "facility_missing_not_imputed", "forbidden_claim_ko", "direct_score_allowed", "proxy_score_allowed"],
    },
    {
        "source_id": "seoul_trade_area_change_index",
        "layer": "silver",
        "path": SILVER / "silver_change_index_trade_area_q.csv",
        "grain": "상권×분기",
        "key_cols": ["기준_년분기_코드", "상권_코드"],
        "core_nonnegative_cols": ["운영_영업_개월_평균", "폐업_영업_개월_평균"],
        "derived_cols": ["운영_서울대비_개월_차이", "폐업_서울대비_개월_차이"],
        "status_cols": ["상권_변화_지표_코드", "상권_변화_지표_명", "forbidden_claim_ko", "directness_level"],
    },
    {
        "source_id": "seoul_trade_area_change_index",
        "layer": "gold",
        "path": GOLD / "gold_growth_stability_q_industry.csv",
        "grain": "상권×업종×분기",
        "key_cols": ["기준_년분기_코드", "상권_코드", "서비스_업종_코드"],
        "core_nonnegative_cols": [],
        "derived_cols": ["운영_영업_개월_평균", "운영_서울대비_개월_차이", "매출_log_최근4분기_slope"],
        "status_cols": ["상권_변화_지표_코드", "상권_변화_지표_명", "growth_score_status", "forbidden_claim_ko", "direct_score_allowed", "proxy_score_allowed"],
    },
]


def json_default(value: Any) -> Any:
    if hasattr(value, "item"):
        return value.item()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if pd.isna(value):
        return None
    return str(value)


def write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")


def write_json(data: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=json_default), encoding="utf-8")


def read_contract() -> pd.DataFrame:
    return pd.read_csv(CONTRACT_75, encoding="utf-8-sig")


def audit_file(spec: dict[str, Any]) -> dict[str, Any]:
    path = spec["path"]
    required_cols = list(dict.fromkeys(spec["key_cols"] + spec["core_nonnegative_cols"] + spec["derived_cols"] + spec["status_cols"]))
    base = {
        "source_id": spec["source_id"],
        "layer": spec["layer"],
        "file_path": str(path.relative_to(ROOT)).replace("\\", "/"),
        "file_exists": path.exists(),
        "grain": spec["grain"],
        "key_cols": ";".join(spec["key_cols"]),
        "required_cols": ";".join(required_cols),
    }
    if not path.exists():
        return {**base, "rows": 0, "missing_cols": ";".join(required_cols), "duplicate_key_rows": None, "key_null_rows": None}

    header = pd.read_csv(path, encoding="utf-8-sig", nrows=0)
    missing_cols = [col for col in required_cols if col not in header.columns]
    usecols = [col for col in required_cols if col in header.columns]
    try:
        df = pd.read_csv(path, encoding="utf-8-sig", usecols=usecols)
    except IndexError:
        # 일부 대형 CSV는 pandas C 엔진의 usecols 결합에서 실패할 수 있어 감사 단계만 Python 엔진으로 우회한다.
        df = pd.read_csv(path, encoding="utf-8-sig", usecols=usecols, engine="python")

    rows = len(df)
    key_cols = spec["key_cols"]
    key_null_rows = int(df[key_cols].isna().any(axis=1).sum()) if all(col in df.columns for col in key_cols) else None
    duplicate_key_rows = int(df.duplicated(key_cols).sum()) if all(col in df.columns for col in key_cols) else None
    q = pd.to_numeric(df["기준_년분기_코드"], errors="coerce") if "기준_년분기_코드" in df.columns else pd.Series(dtype="float")

    core_missing: dict[str, float] = {}
    core_negative: dict[str, int] = {}
    for col in spec["core_nonnegative_cols"]:
        if col in df.columns:
            s = pd.to_numeric(df[col], errors="coerce")
            core_missing[col] = round(float(s.isna().mean()), 8)
            core_negative[col] = int((s < 0).sum())

    derived_missing: dict[str, float] = {}
    derived_negative: dict[str, int] = {}
    for col in spec["derived_cols"]:
        if col in df.columns:
            s = pd.to_numeric(df[col], errors="coerce")
            derived_missing[col] = round(float(s.isna().mean()), 8)
            derived_negative[col] = int((s < 0).sum())

    forbidden_claim_present = False
    forbidden_claim_examples: list[str] = []
    if "forbidden_claim_ko" in df.columns:
        claims = sorted(set(str(v) for v in df["forbidden_claim_ko"].dropna().unique() if str(v).strip()))
        forbidden_claim_present = bool(claims)
        forbidden_claim_examples = claims[:3]

    flag_values: dict[str, list[str]] = {}
    for col in ["direct_score_allowed", "proxy_score_allowed", "growth_score_status", "facility_missing_not_imputed", "store_join_status"]:
        if col in df.columns:
            flag_values[col] = sorted(map(str, df[col].dropna().unique()))[:10]

    return {
        **base,
        "rows": int(rows),
        "file_size_bytes": int(path.stat().st_size),
        "missing_cols": ";".join(missing_cols),
        "duplicate_key_rows": duplicate_key_rows,
        "key_null_rows": key_null_rows,
        "quarter_count": int(q.nunique()) if not q.empty else 0,
        "quarter_min": int(q.min()) if not q.empty and pd.notna(q.min()) else None,
        "quarter_max": int(q.max()) if not q.empty and pd.notna(q.max()) else None,
        "core_missing_rate_json": json.dumps(core_missing, ensure_ascii=False),
        "core_negative_count_json": json.dumps(core_negative, ensure_ascii=False),
        "derived_missing_rate_json": json.dumps(derived_missing, ensure_ascii=False),
        "derived_negative_count_json": json.dumps(derived_negative, ensure_ascii=False),
        "forbidden_claim_present": forbidden_claim_present,
        "forbidden_claim_examples": " | ".join(forbidden_claim_examples),
        "flag_values_json": json.dumps(flag_values, ensure_ascii=False),
    }


def build_file_audit() -> pd.DataFrame:
    return pd.DataFrame([audit_file(spec) for spec in FILE_SPECS])


def build_source_audit(file_audit: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for source_id in DIRECT_SOURCES:
        mask = file_audit["source_id"].astype(str).str.contains(source_id, regex=False)
        part = file_audit[mask]
        rows.append(
            {
                "source_id": source_id,
                "file_count": int(len(part)),
                "all_files_exist": bool(part["file_exists"].all()),
                "total_rows_across_files": int(part["rows"].sum()),
                "min_quarter_count": int(part["quarter_count"].min()) if not part.empty else 0,
                "max_quarter": int(part["quarter_max"].max()) if not part.empty else None,
                "duplicate_key_rows_total": int(part["duplicate_key_rows"].fillna(0).sum()),
                "key_null_rows_total": int(part["key_null_rows"].fillna(0).sum()),
                "missing_cols_count": int(part["missing_cols"].astype(str).str.len().gt(0).sum()),
                "forbidden_claim_all_present": bool(part["forbidden_claim_present"].all()) if not part.empty else False,
                "layers": ";".join(sorted(part["layer"].unique())),
                "files": ";".join(part["file_path"].tolist()),
            }
        )
    return pd.DataFrame(rows)


def add_validation(rows: list[dict[str, Any]], check_id: str, item: str, observed: Any, expected: Any, passed: bool, reason_ko: str) -> None:
    rows.append(
        {
            "check_id": check_id,
            "item": item,
            "observed": json.dumps(observed, ensure_ascii=False, default=json_default) if isinstance(observed, (dict, list)) else observed,
            "expected": json.dumps(expected, ensure_ascii=False, default=json_default) if isinstance(expected, (dict, list)) else expected,
            "pass": bool(passed),
            "reason_ko": reason_ko,
        }
    )


def parse_json_dict(text: Any) -> dict[str, Any]:
    if text is None or pd.isna(text) or not str(text).strip():
        return {}
    return json.loads(str(text))


def validate(file_audit: pd.DataFrame, source_audit: pd.DataFrame) -> pd.DataFrame:
    contract = read_contract()
    rows: list[dict[str, Any]] = []

    contract_direct = sorted(contract.loc[contract["engine_role"].eq("direct_score_input"), "source_id"].astype(str).tolist())
    add_validation(rows, "76-V01", "75번 direct source 6개 고정", contract_direct, sorted(DIRECT_SOURCES), contract_direct == sorted(DIRECT_SOURCES), "76번 감사 대상은 75번 실행계약의 direct_score_input 6개와 일치해야 한다.")

    missing_files = file_audit.loc[~file_audit["file_exists"], "file_path"].tolist()
    add_validation(rows, "76-V02", "필수 silver/gold 파일 존재", missing_files, [], len(missing_files) == 0, "파일 단위 전처리 감사는 실제 존재하는 silver/gold만 대상으로 해야 한다.")

    missing_cols = file_audit.loc[file_audit["missing_cols"].astype(str).str.len().gt(0), ["file_path", "missing_cols"]].to_dict("records")
    add_validation(rows, "76-V03", "필수 컬럼 존재", missing_cols, [], len(missing_cols) == 0, "키, 핵심 수치, 상태/금지표현 컬럼이 없으면 알고리즘 근거를 추적할 수 없다.")

    dup_total = int(file_audit["duplicate_key_rows"].fillna(0).sum())
    key_null_total = int(file_audit["key_null_rows"].fillna(0).sum())
    add_validation(rows, "76-V04", "키 중복/키 결측 없음", {"duplicate_key_rows": dup_total, "key_null_rows": key_null_total}, {"duplicate_key_rows": 0, "key_null_rows": 0}, dup_total == 0 and key_null_total == 0, "상권×업종×분기 또는 상권×분기 grain은 중복되거나 비어 있으면 안 된다.")

    weak_period = source_audit.loc[(source_audit["min_quarter_count"] < 20) | (source_audit["max_quarter"] < 20261), ["source_id", "min_quarter_count", "max_quarter"]].to_dict("records")
    add_validation(rows, "76-V05", "기간 커버리지 충분성", weak_period, [], len(weak_period) == 0, "direct 입력 원천은 최소 20개 분기와 최신 20261 분기를 포함해야 한다.")

    core_missing_bad: list[dict[str, Any]] = []
    core_negative_bad: list[dict[str, Any]] = []
    for _, row in file_audit.iterrows():
        for col, rate in parse_json_dict(row["core_missing_rate_json"]).items():
            if float(rate) != 0.0:
                core_missing_bad.append({"file": row["file_path"], "column": col, "missing_rate": rate})
        for col, count in parse_json_dict(row["core_negative_count_json"]).items():
            if int(count) != 0:
                core_negative_bad.append({"file": row["file_path"], "column": col, "negative_count": count})
    add_validation(rows, "76-V06", "핵심 직접 수치 결측/음수 없음", {"missing": core_missing_bad, "negative": core_negative_bad}, {"missing": [], "negative": []}, not core_missing_bad and not core_negative_bad, "핵심 원천값은 결측이나 음수 없이 보존되어야 한다.")

    status_guard_files = []
    for _, row in file_audit.iterrows():
        derived_missing = parse_json_dict(row["derived_missing_rate_json"])
        has_missing = any(float(v) > 0 for v in derived_missing.values())
        flag_values = parse_json_dict(row["flag_values_json"])
        if has_missing and not flag_values:
            status_guard_files.append(row["file_path"])
    add_validation(rows, "76-V07", "파생지표 조건부 결측 상태컬럼 보존", status_guard_files, [], len(status_guard_files) == 0, "파생지표 결측은 임의 0 대체가 아니라 조인상태/관측여부/후보상태로 설명되어야 한다.")

    forbidden_missing = file_audit.loc[~file_audit["forbidden_claim_present"], "file_path"].tolist()
    add_validation(rows, "76-V08", "금지표현 계약 보존", forbidden_missing, [], len(forbidden_missing) == 0, "직접 입력 파일도 성공확률, 매출 보장, 실제 방문자 같은 과장 표현을 금지해야 한다.")

    flag_bad: list[dict[str, Any]] = []
    for _, row in file_audit[file_audit["layer"].eq("gold")].iterrows():
        flags = parse_json_dict(row["flag_values_json"])
        file_path = row["file_path"]
        if file_path.endswith("gold_growth_stability_q_industry.csv"):
            if flags.get("direct_score_allowed") != ["False"] or "후보_백테스트필요" not in flags.get("growth_score_status", []):
                flag_bad.append({"file": file_path, "flags": flags, "expected": "growth 후보는 direct false와 후보_백테스트필요"})
        elif file_path.endswith("gold_accessibility_q_area.csv"):
            if "True" not in flags.get("direct_score_allowed", []) or "True" not in flags.get("facility_missing_not_imputed", []):
                flag_bad.append({"file": file_path, "flags": flags, "expected": "관측 행 direct true, 미관측 행 not_imputed true"})
        else:
            if "True" not in flags.get("direct_score_allowed", []):
                flag_bad.append({"file": file_path, "flags": flags, "expected": "공식 gold는 direct true 포함"})
    add_validation(rows, "76-V09", "direct/proxy 플래그 해석 일관성", flag_bad, [], len(flag_bad) == 0, "원천 직접성과 산식 직접투입 여부를 분리해 플래그로 남겨야 한다.")

    missing_docs = [str(path.relative_to(ROOT)).replace("\\", "/") for path in RESEARCH_BASIS_DOCS if not path.exists()]
    add_validation(rows, "76-V10", "research 근거 문서 존재", missing_docs, [], len(missing_docs) == 0, "전처리 검증은 research에 모은 명세와 검증문서를 근거로 해야 한다.")

    mart_bad = contract.loc[contract["source_id"].isin(DIRECT_SOURCES) & (~contract["single_feature_mart_forbidden"]), "source_id"].tolist()
    add_validation(rows, "76-V11", "단일 feature mart 회귀 금지", mart_bad, [], len(mart_bad) == 0, "direct 입력도 파일별 silver/gold 경계를 유지해야 한다.")

    direct_contract = contract[contract["source_id"].isin(DIRECT_SOURCES)].copy()
    status_bad = direct_contract[
        (~direct_contract["preprocessing_status"].isin(["ready", "ready_with_tracked_failures"]))
        | (pd.to_numeric(direct_contract["failed_rows"], errors="coerce").fillna(0) != 0)
        | (direct_contract["blocked"].astype(str).str.lower().eq("true"))
    ][["source_id", "preprocessing_status", "failed_rows", "blocked"]].to_dict("records")
    add_validation(rows, "76-V12", "direct source ready 및 실패 0", status_bad, [], len(status_bad) == 0, "공식 직접 입력 원천 6개는 blocked가 아니고 실패 기록 없이 ready 상태여야 한다.")

    rw_files = source_audit.loc[source_audit["source_id"].eq("seoul_resident_worker_population_trade_area"), "files"].iloc[0]
    rw_required = ["silver_resident_population_trade_area_q.csv", "silver_worker_population_trade_area_q.csv", "gold_demand_q_area.csv"]
    rw_missing = [name for name in rw_required if name not in rw_files]
    add_validation(rows, "76-V13", "상주/직장인구 분리 silver 해소", rw_missing, [], len(rw_missing) == 0, "75번 계약표의 빈 silver 목록은 76번에서 상주/직장 분리 silver와 demand gold로 해소되어야 한다.")

    gold_row_bad = []
    for file_name, expected_rows in EXPECTED_GOLD_ROWS.items():
        selected = file_audit[file_audit["file_path"].str.endswith(file_name)]
        observed = int(selected["rows"].iloc[0]) if not selected.empty else None
        if observed != expected_rows:
            gold_row_bad.append({"file": file_name, "observed": observed, "expected": expected_rows})
    add_validation(rows, "76-V14", "기존 gold row 수 보존", gold_row_bad, [], len(gold_row_bad) == 0, "기존 검증에서 고정된 주요 gold 행수가 갑자기 변하면 조인 fan-out이나 누락을 의심해야 한다.")

    return pd.DataFrame(rows)


def build_report(file_audit: pd.DataFrame, source_audit: pd.DataFrame, validations: pd.DataFrame, summary: dict[str, Any]) -> str:
    lines: list[str] = [
        "# 76. direct score input 6개 원천 준비도 감사",
        "",
        f"- 작성일: {datetime.now().strftime('%Y-%m-%d')}",
        f"- 버전: `{VERSION}`",
        "",
        "## 목적",
        "",
        "75번 실행계약에서 direct_score_input으로 분류한 서울 상권분석 직접 원천 6개가 실제 silver/gold 파일에서 공식 점수 입력으로 쓸 수 있는지 확인했다. 검증은 키, grain, 기간, 중복, 결측, 음수, 금지표현, direct/proxy 플래그를 중심으로 했다.",
        "",
        "## 요약",
        "",
        f"- direct sources: {summary['direct_source_count']}",
        f"- audited files: {summary['audited_file_count']}",
        f"- total audited rows: {summary['total_audited_rows']}",
        f"- PASS: {summary['pass_count']}",
        f"- FAIL: {summary['fail_count']}",
        f"- decision: `{summary['decision']}`",
        "",
        "## source별 준비도",
        "",
        "| source | files | rows | min quarters | max quarter | duplicate keys | key nulls | forbidden claims |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for _, row in source_audit.iterrows():
        lines.append(
            f"| `{row['source_id']}` | {row['file_count']} | {row['total_rows_across_files']} | {row['min_quarter_count']} | {row['max_quarter']} | {row['duplicate_key_rows_total']} | {row['key_null_rows_total']} | {row['forbidden_claim_all_present']} |"
        )
    lines.extend(
        [
            "",
            "## 고정한 규칙",
            "",
            "- direct 입력 대상은 75번 계약의 6개 source와 일치해야 한다.",
            "- 상권×업종×분기 또는 상권×분기 키는 중복되거나 비면 안 된다.",
            "- 최소 20개 분기와 최신 20261 분기를 포함해야 한다.",
            "- 핵심 직접 수치에는 결측이나 음수가 없어야 한다.",
            "- 파생지표 결측은 임의 0 대체가 아니라 상태 컬럼으로 설명한다.",
            "- 금지표현 계약을 파일 안에 보존한다.",
            "- 상권변화지표 기반 성장 산출물은 아직 후보/프록시 상태를 유지한다.",
            "- 모든 direct 입력도 단일 feature mart로 합치지 않는다.",
            "",
            "## 검증표",
            "",
            "| check | 항목 | 결과 | 이유 |",
            "|---|---|---|---|",
        ]
    )
    for _, row in validations.iterrows():
        result = "PASS" if bool(row["pass"]) else "FAIL"
        lines.append(f"| {row['check_id']} | {row['item']} | {result} | {row['reason_ko']} |")
    lines.extend(
        [
            "",
            "## 해석",
            "",
            "direct score input 6개 원천은 파일 단위 키·기간·중복·핵심 수치 검증을 통과했다. 다만 모든 파생값이 공식 산식에 직접 들어간다는 뜻은 아니다. 객단가, SBDC 보조값, 생활이동/소비 보조값, 상권변화지표 코드, 성장 안정성 후보값은 각자의 제한 문구와 플래그를 유지한다.",
            "",
            "## 2보 전진 1보 후퇴",
            "",
            "1. 전진: 공식 직접 입력 6개 원천의 silver/gold 파일 준비도를 확인했다.",
            "2. 전진: 키 중복, 기간 커버리지, 핵심 수치 결측/음수, 금지표현을 한 번에 검증했다.",
            "3. 후퇴: 조건부 파생지표 결측을 억지로 0 처리하지 않았다.",
            "4. 후퇴: 성장/안정성 후보와 보조 프록시를 공식 성공확률이나 성장률 보장으로 승격하지 않았다.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    file_audit = build_file_audit()
    source_audit = build_source_audit(file_audit)
    validations = validate(file_audit, source_audit)
    pass_count = int(validations["pass"].sum())
    fail_count = int((~validations["pass"]).sum())
    summary = {
        "version": VERSION,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "direct_source_count": len(DIRECT_SOURCES),
        "audited_file_count": int(len(file_audit)),
        "total_audited_rows": int(file_audit["rows"].sum()),
        "source_rows": source_audit.to_dict("records"),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "decision": "DIRECT_SCORE_INPUT_READINESS_PASS" if fail_count == 0 else "DIRECT_SCORE_INPUT_READINESS_FAIL",
    }
    write_csv(file_audit, OUT_FILE_AUDIT)
    write_csv(source_audit, OUT_SOURCE_AUDIT)
    write_csv(validations, OUT_VALIDATION)
    write_json(summary, OUT_SUMMARY)
    OUT_DOC.parent.mkdir(parents=True, exist_ok=True)
    OUT_DOC.write_text(build_report(file_audit, source_audit, validations, summary), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=json_default))
    if fail_count:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
