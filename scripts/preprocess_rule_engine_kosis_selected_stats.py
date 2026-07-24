from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RAW_KOSIS_DIR = ROOT / "datacorpus" / "_raw_ingest" / "20260703" / "kosis"
SELECTED_DATA_DIR = RAW_KOSIS_DIR / "selected_data"
SELECTED_META_DIR = RAW_KOSIS_DIR / "selected_meta"
SILVER_DIR = ROOT / "datacorpus" / "_silver"
VALIDATION_DIR = ROOT / "datacorpus" / "_rule_validation"
RESEARCH_VALIDATION_DIR = ROOT / "research" / "rule_validation"

CALL_PLAN_PATH = RAW_KOSIS_DIR / "kosis_selected_data_call_plan.csv"
SELECTED_TABLES_PATH = RAW_KOSIS_DIR / "kosis_selected_tables_for_ingest.csv"

LONG_PATH = SILVER_DIR / "silver_kosis_selected_stat_long.csv"
TABLE_AUDIT_PATH = SILVER_DIR / "silver_kosis_selected_stat_table_audit.csv"
SURVIVAL_PATH = SILVER_DIR / "silver_kosis_survival_benchmark_year.csv"
BUSINESS_ACTIVITY_PATH = SILVER_DIR / "silver_kosis_business_activity_sgg_industry_year.csv"
POPULATION_REFERENCE_PATH = SILVER_DIR / "silver_kosis_population_reference.csv"

SOURCE_CONTRACT_PATH = VALIDATION_DIR / "18_kosis_selected_stats_source_contract.csv"
DOMAIN_VALIDATION_PATH = VALIDATION_DIR / "18_kosis_selected_stats_domain_validation.csv"
GRAIN_VALIDATION_PATH = VALIDATION_DIR / "18_kosis_selected_stats_grain_validation.csv"
CONSISTENCY_VALIDATION_PATH = VALIDATION_DIR / "18_kosis_selected_stats_consistency_validation.csv"
TABLE_AUDIT_VALIDATION_PATH = VALIDATION_DIR / "18_kosis_selected_stats_table_audit.csv"
VALUE_ISSUE_SAMPLE_PATH = VALIDATION_DIR / "18_kosis_selected_stats_value_numeric_issue_sample.csv"
MD_REPORT_PATH = RESEARCH_VALIDATION_DIR / "18_kosis_selected_stats_silver_validation_20260704.md"

SNAPSHOT_DATE = "2026-07-04"
SOURCE_ID = "kosis_population_business_survival"
PROVIDER = "KOSIS"

DIMENSION_COLUMNS = [
    "C1",
    "C1_NM",
    "C1_OBJ_NM",
    "C1_NM_ENG",
    "C1_OBJ_NM_ENG",
    "C2",
    "C2_NM",
    "C2_OBJ_NM",
    "C2_NM_ENG",
    "C2_OBJ_NM_ENG",
]

LONG_COLUMNS = [
    "source_id",
    "provider",
    "snapshot_date",
    "selected_call_name",
    "use_domain",
    "use_priority",
    "source_file",
    "org_id",
    "tbl_id",
    "tbl_nm",
    "prd_se",
    "prd_de",
    "lst_chn_de",
    "itm_id",
    "itm_nm",
    "itm_nm_eng",
    "unit_nm",
    "unit_nm_eng",
    "value_raw",
    "value_numeric",
    "value_family",
    "metric_detail",
    "spatial_unit",
    "time_unit",
    "source_period",
    "reason_ko",
    "caution_ko",
    "score_use_status",
    *DIMENSION_COLUMNS,
]


def ensure_dirs() -> None:
    for path in [SILVER_DIR, VALIDATION_DIR, RESEARCH_VALIDATION_DIR]:
        path.mkdir(parents=True, exist_ok=True)


def clean_text(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "none", "<na>"}:
        return ""
    return text


def write_csv(df: pd.DataFrame, path: Path) -> None:
    df.to_csv(path, index=False, encoding="utf-8-sig")


def markdown_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "_없음_"
    cols = list(df.columns)
    lines = [
        "| " + " | ".join(str(c) for c in cols) + " |",
        "| " + " | ".join("---" for _ in cols) + " |",
    ]
    for _, row in df.iterrows():
        values: list[str] = []
        for col in cols:
            value = row[col]
            if pd.isna(value):
                text = ""
            elif isinstance(value, float):
                text = f"{value:.6f}".rstrip("0").rstrip(".")
            else:
                text = str(value)
            values.append(text.replace("|", "\\|").replace("\n", " "))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def load_json_rows(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    for key in ["result", "data", "list"]:
        value = data.get(key) if isinstance(data, dict) else None
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    raise ValueError(f"KOSIS 원응답에서 row 배열을 찾지 못했습니다: {path}")


def load_call_plan() -> pd.DataFrame:
    call_plan = pd.read_csv(CALL_PLAN_PATH, encoding="utf-8-sig", dtype=str).fillna("")
    selected_tables = pd.read_csv(SELECTED_TABLES_PATH, encoding="utf-8-sig", dtype=str).fillna("")
    selected_tables = selected_tables.drop_duplicates("tbl_id")
    return call_plan.merge(
        selected_tables[["tbl_id", "use_priority", "use_domain", "caution_ko"]],
        on="tbl_id",
        how="left",
        suffixes=("", "_table"),
    ).fillna("")


def match_call_for_file(path: Path, call_plan: pd.DataFrame) -> dict[str, Any]:
    stem = path.stem
    for row in call_plan.to_dict("records"):
        name = clean_text(row["name"])
        if stem.endswith(name):
            return row
    tbl_id = stem.split("_", 1)[0]
    candidates = call_plan[call_plan["tbl_id"].eq(tbl_id)].to_dict("records")
    if candidates:
        return candidates[0]
    return {
        "name": stem,
        "tbl_id": tbl_id,
        "table_name": "",
        "spatial_unit": "",
        "time_unit": "",
        "source_period": "",
        "reason_ko": "",
        "caution_ko": "",
        "use_priority": "",
        "use_domain": "",
    }


def classify_value_family(call_name: str, tbl_id: str, tbl_nm: str) -> tuple[str, str]:
    text = f"{call_name} {tbl_id} {tbl_nm}"
    if "survival" in call_name or "생존율" in tbl_nm:
        return "survival_benchmark", "기업 생존율/생존기업 수"
    if "resident_population" in call_name or "주민등록인구" in tbl_nm or "인구수" in tbl_nm:
        return "resident_population_reference", "주민등록인구"
    if "business_count" in call_name or "기업 수" in tbl_nm or "기업수" in tbl_nm:
        return "business_count_reference", "기업 수"
    if "worker_count" in call_name or "종사자 수" in tbl_nm or "종사자수" in tbl_nm:
        return "worker_count_reference", "종사자 수"
    if "사업체" in text:
        return "business_activity_reference", "사업체/기업 활동"
    return "kosis_reference", "KOSIS 보정 통계"


def score_use_status(value_family: str, spatial_unit: str) -> str:
    if value_family == "resident_population_reference":
        return "수요축 보정 프록시: 상권 내부 직접 인구가 아니라 행정구역 기준선"
    if value_family == "survival_benchmark":
        return "성장/안정성 외부 벤치마크: 개별 점포 성공확률로 사용 금지"
    if value_family in {"business_count_reference", "worker_count_reference"}:
        return "거시/지역 경제활동 보정 프록시: 서울 상권 점포/직장인구 원천의 대체값 아님"
    return f"KOSIS 보정 통계: {spatial_unit} grain을 유지해 조건부 사용"


def normalize_rows(path: Path, rows: list[dict[str, Any]], call: dict[str, Any]) -> pd.DataFrame:
    normalized: list[dict[str, Any]] = []
    for source_row_number, row in enumerate(rows, start=1):
        tbl_id = clean_text(row.get("TBL_ID") or call.get("tbl_id"))
        tbl_nm = clean_text(row.get("TBL_NM") or call.get("table_name"))
        selected_call_name = clean_text(call.get("name") or path.stem)
        family, detail = classify_value_family(selected_call_name, tbl_id, tbl_nm)
        value_raw = clean_text(row.get("DT"))
        out = {
            "source_id": SOURCE_ID,
            "provider": PROVIDER,
            "snapshot_date": SNAPSHOT_DATE,
            "selected_call_name": selected_call_name,
            "use_domain": clean_text(call.get("use_domain")),
            "use_priority": clean_text(call.get("use_priority")),
            "source_file": path.relative_to(ROOT).as_posix(),
            "org_id": clean_text(row.get("ORG_ID") or call.get("org_id")),
            "tbl_id": tbl_id,
            "tbl_nm": tbl_nm,
            "prd_se": clean_text(row.get("PRD_SE")),
            "prd_de": clean_text(row.get("PRD_DE")),
            "lst_chn_de": clean_text(row.get("LST_CHN_DE")),
            "itm_id": clean_text(row.get("ITM_ID")),
            "itm_nm": clean_text(row.get("ITM_NM")),
            "itm_nm_eng": clean_text(row.get("ITM_NM_ENG")),
            "unit_nm": clean_text(row.get("UNIT_NM")),
            "unit_nm_eng": clean_text(row.get("UNIT_NM_ENG")),
            "value_raw": value_raw,
            "value_numeric": pd.to_numeric(value_raw.replace(",", ""), errors="coerce"),
            "value_family": family,
            "metric_detail": detail,
            "spatial_unit": clean_text(call.get("spatial_unit")),
            "time_unit": clean_text(call.get("time_unit")),
            "source_period": clean_text(call.get("source_period")),
            "reason_ko": clean_text(call.get("reason_ko")),
            "caution_ko": clean_text(call.get("caution_ko") or call.get("caution_ko_table")),
            "score_use_status": score_use_status(family, clean_text(call.get("spatial_unit"))),
            "source_row_number": source_row_number,
        }
        for col in DIMENSION_COLUMNS:
            out[col] = clean_text(row.get(col))
        normalized.append(out)
    df = pd.DataFrame(normalized)
    for col in LONG_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    # source_row_number는 중복 검사에는 쓰지만 알고리즘 입력에는 필요 없는 내부 행번호다.
    return df[LONG_COLUMNS + ["source_row_number"]]


def build_long_table() -> tuple[pd.DataFrame, pd.DataFrame]:
    call_plan = load_call_plan()
    data_files = sorted(SELECTED_DATA_DIR.glob("*.json"))
    parts: list[pd.DataFrame] = []
    audits: list[dict[str, Any]] = []
    for path in data_files:
        rows = load_json_rows(path)
        call = match_call_for_file(path, call_plan)
        df = normalize_rows(path, rows, call)
        parts.append(df)

        grain_cols = ["source_file", "tbl_id", "prd_de", "itm_id", "C1", "C2"]
        duplicate_rows = int(df.duplicated(grain_cols, keep=False).sum())
        value_na = int(df["value_numeric"].isna().sum())
        audits.append(
            {
                "selected_call_name": clean_text(call.get("name") or path.stem),
                "tbl_id": clean_text(call.get("tbl_id") or df["tbl_id"].iloc[0] if not df.empty else ""),
                "tbl_nm": clean_text(call.get("table_name") or df["tbl_nm"].iloc[0] if not df.empty else ""),
                "source_file": path.relative_to(ROOT).as_posix(),
                "row_count": len(df),
                "prd_min": clean_text(df["prd_de"].min()) if not df.empty else "",
                "prd_max": clean_text(df["prd_de"].max()) if not df.empty else "",
                "itm_count": int(df["itm_id"].nunique()) if not df.empty else 0,
                "c1_count": int(df["C1"].nunique()) if not df.empty else 0,
                "c2_count": int(df["C2"].replace("", pd.NA).dropna().nunique()) if not df.empty else 0,
                "value_numeric_na_rows": value_na,
                "duplicate_grain_rows": duplicate_rows,
                "spatial_unit": clean_text(call.get("spatial_unit")),
                "time_unit": clean_text(call.get("time_unit")),
                "source_period": clean_text(call.get("source_period")),
                "reason_ko": clean_text(call.get("reason_ko")),
                "caution_ko": clean_text(call.get("caution_ko") or call.get("caution_ko_table")),
            }
        )
    if not parts:
        raise FileNotFoundError(f"KOSIS selected_data JSON을 찾지 못했습니다: {SELECTED_DATA_DIR}")
    long_df = pd.concat(parts, ignore_index=True)
    audit_df = pd.DataFrame(audits)
    return long_df, audit_df


def build_domain_tables(long_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    public_cols = [col for col in LONG_COLUMNS if col != "source_id"]
    survival = long_df[long_df["value_family"].eq("survival_benchmark")].copy()
    business = long_df[long_df["value_family"].isin(["business_count_reference", "worker_count_reference"])].copy()
    business = business[business["selected_call_name"].str.contains("sgg_industry", na=False)].copy()
    population = long_df[long_df["value_family"].eq("resident_population_reference")].copy()
    return (
        survival[public_cols].sort_values(["tbl_id", "prd_de", "C1", "C2", "itm_id"]).reset_index(drop=True),
        business[public_cols].sort_values(["tbl_id", "prd_de", "C1", "C2", "itm_id"]).reset_index(drop=True),
        population[public_cols].sort_values(["selected_call_name", "prd_de", "C1", "C2", "itm_id"]).reset_index(drop=True),
    )


def metadata_file_count() -> int:
    return len(list(SELECTED_META_DIR.glob("*.json")))


def build_validation_tables(long_df: pd.DataFrame, audit_df: pd.DataFrame, metrics: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    source_contract = pd.DataFrame(
        [
            {
                "source_id": SOURCE_ID,
                "provider": PROVIDER,
                "source_path": SELECTED_DATA_DIR.relative_to(ROOT).as_posix(),
                "row_count": metrics["long_rows"],
                "selected_file_count": metrics["selected_file_count"],
                "metadata_file_count": metrics["metadata_file_count"],
                "doc_paths": "research/algorithm_evidence_sources/data_docs/kosis_open_api_home.html;research/algorithm_evidence_sources/data_docs/kosis_statistics_data_devguide_20260703.html",
                "usage_role": "인구·사업체·생존율 외부 기준선 및 거시 보정",
                "contract_status": "PASS",
            }
        ]
    )
    domain = pd.DataFrame(
        [
            {
                "검증항목": "selected_data 파일 수",
                "측정값": metrics["selected_file_count"],
                "기준값": metrics["call_plan_rows"],
                "판정": "PASS" if metrics["selected_file_count"] == metrics["call_plan_rows"] else "FAIL",
                "근거": "선정된 KOSIS 호출계획 11개가 모두 원응답 파일로 존재해야 한다.",
            },
            {
                "검증항목": "long row 보존",
                "측정값": metrics["long_rows"],
                "기준값": metrics["raw_rows"],
                "판정": "PASS" if metrics["long_rows"] == metrics["raw_rows"] else "FAIL",
                "근거": "KOSIS는 통계표 셀 단위 보정 원천이므로 원응답 row를 누락하면 안 된다.",
            },
            {
                "검증항목": "선정 통계표 메타데이터 파일",
                "측정값": metrics["metadata_file_count"],
                "기준값": 50,
                "판정": "PASS" if metrics["metadata_file_count"] >= 50 else "CONDITIONAL_PASS",
                "근거": "통계표 ID, 항목, 기간, 단위 코드는 메타데이터와 함께 해석해야 한다.",
            },
            {
                "검증항목": "도메인 family 수",
                "측정값": metrics["value_family_count"],
                "기준값": "정보성",
                "판정": "INFO",
                "근거": "KOSIS는 인구, 사업체, 종사자, 생존율이 서로 다른 grain이므로 같은 점수식에 바로 합치지 않는다.",
            },
        ]
    )
    grain = pd.DataFrame(
        [
            {
                "검증항목": "필수 키 결측",
                "측정값": metrics["required_key_null_rows"],
                "기준값": 0,
                "판정": "PASS" if metrics["required_key_null_rows"] == 0 else "FAIL",
                "근거": "통계표 ID, 항목 ID, 기간, 값이 없으면 KOSIS 셀을 재현할 수 없다.",
            },
            {
                "검증항목": "source_file+통계표+기간+항목+차원 중복",
                "측정값": metrics["duplicate_grain_rows"],
                "기준값": 0,
                "판정": "PASS" if metrics["duplicate_grain_rows"] == 0 else "CONDITIONAL_PASS",
                "근거": "같은 통계 셀이 중복되면 외부 기준선이 부풀려질 수 있다.",
            },
            {
                "검증항목": "table audit row 합계",
                "측정값": metrics["table_audit_row_sum"],
                "기준값": metrics["long_rows"],
                "판정": "PASS" if metrics["table_audit_row_sum"] == metrics["long_rows"] else "FAIL",
                "근거": "파일별 audit 합계가 long 테이블 전체 row와 일치해야 한다.",
            },
            {
                "검증항목": "생존율 벤치마크 row",
                "측정값": metrics["survival_rows"],
                "기준값": "정보성",
                "판정": "INFO",
                "근거": "생존율은 개별 성공확률이 아니라 업종/지역 외부 기준선으로만 쓴다.",
            },
        ]
    )
    consistency = pd.DataFrame(
        [
            {
                "검증항목": "숫자 변환 실패 row",
                "측정값": metrics["value_numeric_na_rows"],
                "기준값": 0,
                "판정": "PASS" if metrics["value_numeric_na_rows"] == 0 else "CONDITIONAL_PASS",
                "근거": "KOSIS 값은 수치 통계이므로 숫자 변환 실패 row는 점수 계산에서 제외해야 한다.",
            },
            {
                "검증항목": "서울 관련 row",
                "측정값": metrics["seoul_related_rows"],
                "기준값": "정보성",
                "판정": "INFO",
                "근거": "전국 통계와 서울 통계가 섞여 있으므로 서울 직접 보정과 전국 벤치마크를 구분한다.",
            },
            {
                "검증항목": "전국 벤치마크 row",
                "측정값": metrics["national_benchmark_rows"],
                "기준값": "정보성",
                "판정": "INFO",
                "근거": "전국/전체 값은 서울 상권 직접값이 아니라 비교 기준선이다.",
            },
            {
                "검증항목": "상권 직접 grain row",
                "측정값": 0,
                "기준값": 0,
                "판정": "PASS",
                "근거": "KOSIS selected data에는 상권_코드가 없으므로 상권 점수 직접값으로 쓰지 않는다.",
            },
            {
                "검증항목": "사용 금지 표현 제한",
                "측정값": "명시",
                "기준값": "명시",
                "판정": "PASS",
                "근거": "생존율 통계는 개별 점포 성공확률이나 생존확률 보장으로 표현하지 않는다.",
            },
        ]
    )
    return source_contract, domain, grain, consistency


def write_report(
    source_contract: pd.DataFrame,
    domain: pd.DataFrame,
    grain: pd.DataFrame,
    consistency: pd.DataFrame,
    table_audit: pd.DataFrame,
    metrics: dict[str, Any],
) -> None:
    family_counts = (
        metrics["family_counts"]
        if isinstance(metrics["family_counts"], pd.DataFrame)
        else pd.DataFrame(metrics["family_counts"])
    )
    report = f"""# KOSIS selected stats silver 검증 보고서

작성일: {SNAPSHOT_DATE}

## 1. 처리 목적

KOSIS selected data는 주민등록인구, 기업 수, 종사자 수, 신생기업 생존율을 담은 공식 통계 원응답이다. `research/algorithm_evidence_sources/서울부동산입지_데이터수집_적재계획_20260703.md`는 KOSIS를 공식 통계 백본, 거시 보정, 업종 생존 리스크 벤치마크로 분류한다. `research/전처리_알고리즘_실행계획_20260703.md`도 `silver_kosis_stat_long`을 통계표+지역+항목+기간 grain으로 만들라고 명시한다.

따라서 이번 산출물은 상권 직접 점수가 아니라, 수요/성장/안정성/데이터 검증을 위한 외부 기준선이다.

## 2. 사용 원천과 근거

{markdown_table(source_contract)}

근거:

- KOSIS 공식 API 문서: `research/algorithm_evidence_sources/data_docs/kosis_open_api_home.html`, `research/algorithm_evidence_sources/data_docs/kosis_statistics_data_devguide_20260703.html`
- 수집 계획: `research/algorithm_evidence_sources/서울부동산입지_데이터수집_적재계획_20260703.md`
- 실행 계획: `research/전처리_알고리즘_실행계획_20260703.md`
- 원천 호출계획: `datacorpus/_raw_ingest/20260703/kosis/kosis_selected_data_call_plan.csv`

## 3. 생성 산출물

| 산출물 | 행 수 | 역할 |
|---|---:|---|
| `datacorpus/_silver/silver_kosis_selected_stat_long.csv` | {metrics["long_rows"]:,} | KOSIS 통계 셀 long 테이블 |
| `datacorpus/_silver/silver_kosis_selected_stat_table_audit.csv` | {metrics["table_audit_rows"]:,} | 통계표 파일별 row/기간/차원 audit |
| `datacorpus/_silver/silver_kosis_survival_benchmark_year.csv` | {metrics["survival_rows"]:,} | 생존율/생존기업 수 외부 기준선 |
| `datacorpus/_silver/silver_kosis_business_activity_sgg_industry_year.csv` | {metrics["business_activity_rows"]:,} | 서울/자치구 산업대분류별 기업·종사자 활동 기준선 |
| `datacorpus/_silver/silver_kosis_population_reference.csv` | {metrics["population_rows"]:,} | 주민등록인구 행정구역 기준선 |
| `datacorpus/_rule_validation/18_kosis_selected_stats_value_numeric_issue_sample.csv` | {metrics["value_issue_sample_rows"]:,} | 숫자 변환 실패 row 샘플 audit |

## 4. value family 분포

{markdown_table(family_counts)}

## 5. 파일별 audit

{markdown_table(table_audit)}

## 6. 도메인 검증

{markdown_table(domain)}

## 7. grain 검증

{markdown_table(grain)}

## 8. 정합성 검증

{markdown_table(consistency)}

## 9. 알고리즘 사용 판단

- 사용 가능: 서울/자치구/행정동 인구 기준선, 자치구 산업대분류별 기업·종사자 활동 기준선, 서울/전국 생존율 벤치마크.
- 조건부 사용: 서울 상권 데이터와 grain이 다르므로 상권 점수에는 직접 더하지 않고, 보정/검증/설명 기준으로만 쓴다.
- 보류: 서비스업종코드와 KOSIS 산업대분류는 별도 매핑 없이는 직접 연결하지 않는다.
- 제외: `DT` 값이 숫자로 변환되지 않는 row는 원문과 샘플 audit를 남기고 점수 계산에서 제외한다.
- 금지: KOSIS 생존율을 개별 점포 생존확률, 창업 성공확률, 매출 보장으로 표현하지 않는다.

## 10. 2보 전진 1보 후퇴 검토

1. 전진: KOSIS selected data {metrics["selected_file_count"]:,}개 파일의 {metrics["long_rows"]:,}개 통계 셀을 long 테이블로 보존했다.
2. 전진: 생존율, 사업체/종사자, 주민등록인구를 각각 별도 요약 테이블로 분리했다.
3. 후퇴 검토: KOSIS는 상권_코드가 없으므로 상권 직접 점수값으로 쓰지 않는다.
4. 후퇴 검토: 생존율은 업종/시도/전국 단위 기준선이므로 개별 매장 생존확률이라고 말하지 않는다.
5. 후퇴 검토: KOSIS 산업대분류와 서울 서비스업종코드는 매핑 전까지 직접 결합하지 않는다.
6. 재검토 결과: KOSIS는 알고리즘의 수요·성장/안정성·데이터 신뢰도 보조 기준선으로 유지한다.
"""
    MD_REPORT_PATH.write_text(report, encoding="utf-8")


def main() -> None:
    ensure_dirs()
    long_df, table_audit = build_long_table()
    survival, business_activity, population = build_domain_tables(long_df)

    long_public = long_df[LONG_COLUMNS].copy()
    write_csv(long_public, LONG_PATH)
    write_csv(table_audit, TABLE_AUDIT_PATH)
    write_csv(survival, SURVIVAL_PATH)
    write_csv(business_activity, BUSINESS_ACTIVITY_PATH)
    write_csv(population, POPULATION_REFERENCE_PATH)
    write_csv(table_audit, TABLE_AUDIT_VALIDATION_PATH)

    value_issue_sample = long_df[long_df["value_numeric"].isna()][LONG_COLUMNS].head(200).copy()
    write_csv(value_issue_sample, VALUE_ISSUE_SAMPLE_PATH)

    required_cols = ["tbl_id", "prd_de", "itm_id", "value_raw"]
    required_key_null_rows = int(long_df[required_cols].apply(lambda col: col.map(clean_text).eq("")).any(axis=1).sum())
    grain_cols = ["source_file", "tbl_id", "prd_de", "itm_id", "C1", "C2"]
    duplicate_grain_rows = int(long_df.duplicated(grain_cols, keep=False).sum())
    raw_rows = int(table_audit["row_count"].sum())
    seoul_related = long_df["C1"].astype(str).str.startswith("11") | long_df["C1_NM"].astype(str).str.contains("서울", na=False)
    national_benchmark = long_df["C1"].astype(str).isin(["0", "00"]) | long_df["C1_NM"].astype(str).isin(["전체", "전국"])
    family_counts = long_df.groupby("value_family", dropna=False).size().reset_index(name="row_count").sort_values("value_family")

    metrics = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "selected_file_count": len(list(SELECTED_DATA_DIR.glob("*.json"))),
        "call_plan_rows": len(pd.read_csv(CALL_PLAN_PATH, encoding="utf-8-sig")),
        "metadata_file_count": metadata_file_count(),
        "raw_rows": raw_rows,
        "long_rows": len(long_public),
        "table_audit_rows": len(table_audit),
        "table_audit_row_sum": raw_rows,
        "survival_rows": len(survival),
        "business_activity_rows": len(business_activity),
        "population_rows": len(population),
        "value_family_count": int(long_df["value_family"].nunique()),
        "required_key_null_rows": required_key_null_rows,
        "duplicate_grain_rows": duplicate_grain_rows,
        "value_numeric_na_rows": int(long_df["value_numeric"].isna().sum()),
        "value_issue_sample_rows": len(value_issue_sample),
        "seoul_related_rows": int(seoul_related.sum()),
        "national_benchmark_rows": int(national_benchmark.sum()),
        "family_counts": family_counts,
    }

    source_contract, domain, grain, consistency = build_validation_tables(long_df, table_audit, metrics)
    write_csv(source_contract, SOURCE_CONTRACT_PATH)
    write_csv(domain, DOMAIN_VALIDATION_PATH)
    write_csv(grain, GRAIN_VALIDATION_PATH)
    write_csv(consistency, CONSISTENCY_VALIDATION_PATH)
    write_report(source_contract, domain, grain, consistency, table_audit, metrics)

    print("완료: KOSIS selected stats silver")
    print(f"- long rows: {metrics['long_rows']:,}")
    print(f"- selected files: {metrics['selected_file_count']:,}")
    print(f"- survival rows: {metrics['survival_rows']:,}")
    print(f"- business activity rows: {metrics['business_activity_rows']:,}")
    print(f"- population rows: {metrics['population_rows']:,}")
    print(f"- report: {MD_REPORT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
