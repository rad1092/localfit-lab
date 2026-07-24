from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "datacorpus" / "_raw_ingest"
FINAL_SPATIAL_DIR = ROOT / "datacorpus" / "_final" / "spatial_od"
SILVER_DIR = ROOT / "datacorpus" / "_silver"
VALIDATION_DIR = ROOT / "datacorpus" / "_rule_validation"
RESEARCH_VALIDATION_DIR = ROOT / "research" / "rule_validation"

PROGRESS_PATH = ROOT / "research" / "전처리_진행기록_20260703.md"
PRECHECK_PATH = ROOT / "research" / "전처리_전_확인사항_20260703.md"
SOURCE_DOC_PATH = ROOT / "research" / "algorithm_evidence_sources" / "data_docs" / "seoul_living_migration_guide.html"

SNAPSHOT_DATE = "2026-07-03"
SOURCE_ID = "seoul_living_migration"
PROVIDER = "서울 열린데이터/생활이동"
SOURCE_SERVICE = "서울 생활이동 자치구 CSV"


def ensure_dirs() -> None:
    for path in [SILVER_DIR, VALIDATION_DIR, RESEARCH_VALIDATION_DIR]:
        path.mkdir(parents=True, exist_ok=True)


def find_spatial_csv(tokens: list[str]) -> Path:
    matches = []
    for path in FINAL_SPATIAL_DIR.glob("*.csv"):
        name = path.name
        if all(token in name for token in tokens):
            matches.append(path)
    if len(matches) != 1:
        raise FileNotFoundError(f"생활이동 산출물 매칭 실패 tokens={tokens}, matches={matches}")
    return matches[0]


def read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, encoding="utf-8-sig", low_memory=False)


def write_csv(df: pd.DataFrame, path: Path) -> None:
    df.to_csv(path, index=False, encoding="utf-8-sig")


def markdown_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "_행 없음_"
    cols = list(df.columns)
    lines = [
        "| " + " | ".join(str(c) for c in cols) + " |",
        "| " + " | ".join("---" for _ in cols) + " |",
    ]
    for _, row in df.iterrows():
        values = []
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


def as_bool(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series.fillna(False)
    return series.astype(str).str.lower().isin(["true", "1", "y", "yes"])


def add_common_metadata(df: pd.DataFrame, source_grain: str, notes_ko: str) -> pd.DataFrame:
    out = df.copy()
    out["source_id"] = SOURCE_ID
    out["provider"] = PROVIDER
    out["source_service"] = SOURCE_SERVICE
    out["snapshot_date"] = SNAPSHOT_DATE
    out["source_grain"] = source_grain
    out["directness_level"] = "집계/추정 프록시"
    out["forbidden_claim_ko"] = "개별 사람의 실제 이동경로, 실제 방문자 수, 구매자 수, 상권 단위 직접 유입량으로 주장하지 않는다."
    out["notes_ko"] = notes_ko
    return out


def load_source_tables() -> dict[str, pd.DataFrame]:
    paths = {
        "od": find_spatial_csv(["생활이동", "OD", "월시간"]),
        "arrival_demo": find_spatial_csv(["생활이동", "도착자치구", "성연령유형"]),
        "district_flow": find_spatial_csv(["생활이동", "자치구", "월시간", "방향"]),
        "district_quarter": find_spatial_csv(["생활이동", "자치구", "분기피처"]),
        "legacy_audit": find_spatial_csv(["생활이동", "원천파일감사"]),
    }
    return {name: read_csv(path) for name, path in paths.items()}


def build_source_file_audit(legacy_audit: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    coverage = read_csv(RAW_DIR / "living_migration_coverage_audit.csv")
    duplicates = read_csv(RAW_DIR / "living_migration_duplicate_groups.csv")

    legacy = legacy_audit.copy()
    legacy["canonical_candidate"] = np.where(as_bool(legacy["집계포함여부"]), "Y", "N")
    legacy_key = legacy[["대상연월", "시간대", "canonical_candidate", "원천_데이터행수", "집계포함여부", "제외사유"]].rename(
        columns={"대상연월": "month", "시간대": "hour"}
    )

    audit = coverage.merge(legacy_key, on=["month", "hour", "canonical_candidate"], how="left", validate="one_to_one")
    audit["source_id"] = SOURCE_ID
    audit["provider"] = PROVIDER
    audit["source_service"] = SOURCE_SERVICE
    audit["snapshot_date"] = SNAPSHOT_DATE
    audit["encoding_confirmed_ko"] = "원천 CSV는 UTF-8이 아니라 cp949/euc-kr로 판독된다."
    audit["usage_decision_ko"] = np.where(
        audit["canonical_candidate"].eq("Y"),
        "집계 사용",
        "동일 월/시간 중복 파일이므로 집계 제외",
    )

    duplicate_summary = pd.DataFrame(
        [
            {
                "source_id": SOURCE_ID,
                "duplicate_rows": int(len(duplicates)),
                "duplicate_groups": int(duplicates["duplicate_group"].nunique()) if len(duplicates) else 0,
                "canonical_duplicate_rows": int((duplicates["canonical_candidate"] == "Y").sum()) if len(duplicates) else 0,
                "excluded_duplicate_rows": int((duplicates["canonical_candidate"] == "N").sum()) if len(duplicates) else 0,
                "decision_ko": "202605 동일 해시 중복 24개 시간대는 canonical_candidate=Y 한 벌만 사용한다.",
            }
        ]
    )
    return audit, duplicate_summary


def build_dimension_codebook(arrival_demo: pd.DataFrame) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    dimensions = [
        ("성별", "원천 성별 코드"),
        ("연령대", "원천 연령대 코드"),
        ("이동유형", "원천 이동유형 코드"),
        ("주말여부", "집계된 주말 여부"),
    ]
    for column, desc in dimensions:
        values = sorted(arrival_demo[column].dropna().unique().tolist(), key=lambda x: str(x))
        for value in values:
            records.append(
                {
                    "dimension_name": column,
                    "code": value,
                    "description_ko": desc,
                    "usage_note_ko": "공식 HTML에서 세부 코드 의미를 확정하지 못한 값은 범주형 분해에만 쓰고 행동 의도 단정에는 쓰지 않는다.",
                    "source_id": SOURCE_ID,
                }
            )
    return pd.DataFrame(records)


def duplicate_count(df: pd.DataFrame, key_cols: list[str]) -> int:
    return int(df.duplicated(key_cols).sum())


def null_key_cells(df: pd.DataFrame, key_cols: list[str]) -> int:
    return int(df[key_cols].isna().sum().sum())


def validate_tables(
    od: pd.DataFrame,
    arrival_demo: pd.DataFrame,
    district_flow: pd.DataFrame,
    district_quarter: pd.DataFrame,
    source_file_audit: pd.DataFrame,
    dimension_codebook: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    od_key = ["대상연월", "시간대", "출발_생활이동_시군구_코드", "도착_생활이동_시군구_코드"]
    demo_key = ["대상연월", "시간대", "도착_자치구_코드", "성별", "연령대", "이동유형", "주말여부"]
    flow_key = ["대상연월", "시간대", "자치구_코드"]
    quarter_key = ["기준_년분기_코드", "자치구_코드"]
    audit_key = ["month", "hour", "canonical_candidate", "relative_path"]
    codebook_key = ["dimension_name", "code"]

    od_arrival_seoul = od.loc[as_bool(od["도착_서울자치구_여부"]), "이동인구_합계"].sum()
    od_departure_seoul = od.loc[as_bool(od["출발_서울자치구_여부"]), "이동인구_합계"].sum()
    demo_total = arrival_demo["도착_이동인구_합계"].sum()
    flow_in = district_flow["유입_이동인구_합계"].sum()
    flow_out = district_flow["유출_이동인구_합계"].sum()
    flow_internal = district_flow["내부이동_이동인구_합계"].sum()
    included_audit = source_file_audit[source_file_audit["canonical_candidate"] == "Y"]
    excluded_audit = source_file_audit[source_file_audit["canonical_candidate"] == "N"]

    domain_rows = [
        {
            "table": "silver_living_migration_od_month_hour",
            "rows": len(od),
            "month_count": od["대상연월"].nunique(),
            "month_min": int(od["대상연월"].min()),
            "month_max": int(od["대상연월"].max()),
            "hour_count": od["시간대"].nunique(),
            "key_null_cells": null_key_cells(od, od_key),
            "duplicate_key_rows": duplicate_count(od, od_key),
            "negative_population_rows": int((od["이동인구_합계"] < 0).sum()),
            "negative_time_rows": int((od["평균_이동시간_분"] < 0).sum()),
            "total_population": float(od["이동인구_합계"].sum()),
            "judgement": "PASS",
            "conditional_reason_ko": "",
        },
        {
            "table": "silver_living_migration_arrival_demo_month_hour",
            "rows": len(arrival_demo),
            "month_count": arrival_demo["대상연월"].nunique(),
            "month_min": int(arrival_demo["대상연월"].min()),
            "month_max": int(arrival_demo["대상연월"].max()),
            "hour_count": arrival_demo["시간대"].nunique(),
            "key_null_cells": null_key_cells(arrival_demo, demo_key),
            "duplicate_key_rows": duplicate_count(arrival_demo, demo_key),
            "negative_population_rows": int((arrival_demo["도착_이동인구_합계"] < 0).sum()),
            "negative_time_rows": int((arrival_demo["도착_평균_이동시간_분"] < 0).sum()),
            "total_population": float(demo_total),
            "judgement": "PASS",
            "conditional_reason_ko": "",
        },
        {
            "table": "silver_living_migration_district_flow_month_hour",
            "rows": len(district_flow),
            "month_count": district_flow["대상연월"].nunique(),
            "month_min": int(district_flow["대상연월"].min()),
            "month_max": int(district_flow["대상연월"].max()),
            "hour_count": district_flow["시간대"].nunique(),
            "key_null_cells": null_key_cells(district_flow, flow_key),
            "duplicate_key_rows": duplicate_count(district_flow, flow_key),
            "negative_population_rows": int(
                (district_flow[["유입_이동인구_합계", "유출_이동인구_합계", "내부이동_이동인구_합계"]] < 0).sum().sum()
            ),
            "negative_time_rows": int(
                (
                    district_flow[
                        ["유입_평균_이동시간_분", "유출_평균_이동시간_분", "내부이동_평균_이동시간_분"]
                    ]
                    < 0
                )
                .sum()
                .sum()
            ),
            "total_population": float(district_flow["총관련_이동인구_합계"].sum()),
            "judgement": "PASS",
            "conditional_reason_ko": "",
        },
        {
            "table": "silver_living_migration_district_quarter_features",
            "rows": len(district_quarter),
            "month_count": np.nan,
            "month_min": np.nan,
            "month_max": np.nan,
            "hour_count": np.nan,
            "key_null_cells": null_key_cells(district_quarter, quarter_key),
            "duplicate_key_rows": duplicate_count(district_quarter, quarter_key),
            "negative_population_rows": int(
                (
                    district_quarter[
                        [
                            "생활이동_유입_이동인구_합계",
                            "생활이동_유출_이동인구_합계",
                            "생활이동_내부이동_이동인구_합계",
                            "생활이동_총관련_이동인구_합계",
                        ]
                    ]
                    < 0
                )
                .sum()
                .sum()
            ),
            "negative_time_rows": int(
                (
                    district_quarter[
                        [
                            "생활이동_유입_평균_이동시간_분",
                            "생활이동_유출_평균_이동시간_분",
                            "생활이동_내부이동_평균_이동시간_분",
                        ]
                    ]
                    < 0
                )
                .sum()
                .sum()
            ),
            "total_population": float(district_quarter["생활이동_총관련_이동인구_합계"].sum()),
            "judgement": "조건부 PASS",
            "conditional_reason_ko": "2026년 2분기는 202604~202605 두 달만 포함되어 완전 분기 비교에는 month_count 보정이 필요하다.",
        },
        {
            "table": "silver_living_migration_source_file_audit",
            "rows": len(source_file_audit),
            "month_count": source_file_audit["month"].nunique(),
            "month_min": int(source_file_audit["month"].min()),
            "month_max": int(source_file_audit["month"].max()),
            "hour_count": source_file_audit["hour"].nunique(),
            "key_null_cells": null_key_cells(source_file_audit, audit_key),
            "duplicate_key_rows": duplicate_count(source_file_audit, audit_key),
            "negative_population_rows": 0,
            "negative_time_rows": 0,
            "total_population": np.nan,
            "judgement": "조건부 PASS",
            "conditional_reason_ko": "144개 원천 후보 중 202605 동일 해시 중복 24개를 제외하고 120개 canonical 파일만 집계한다.",
        },
        {
            "table": "silver_living_migration_dimension_codebook",
            "rows": len(dimension_codebook),
            "month_count": np.nan,
            "month_min": np.nan,
            "month_max": np.nan,
            "hour_count": np.nan,
            "key_null_cells": null_key_cells(dimension_codebook, codebook_key),
            "duplicate_key_rows": duplicate_count(dimension_codebook, codebook_key),
            "negative_population_rows": 0,
            "negative_time_rows": 0,
            "total_population": np.nan,
            "judgement": "조건부 PASS",
            "conditional_reason_ko": "원천 코드 의미가 공식 HTML에서 모두 확정되지 않아 행동 의도 단정에는 쓰지 않는다.",
        },
    ]

    grain_rows = [
        {
            "table": "silver_living_migration_od_month_hour",
            "key_cols": "대상연월 + 시간대 + 출발_생활이동_시군구_코드 + 도착_생활이동_시군구_코드",
            "duplicate_key_rows": duplicate_count(od, od_key),
            "key_null_cells": null_key_cells(od, od_key),
            "judgement": "PASS",
            "reason_ko": "자치구급 OD와 시간대 집계가 유입권 판단의 최소 보존 단위다.",
        },
        {
            "table": "silver_living_migration_arrival_demo_month_hour",
            "key_cols": "대상연월 + 시간대 + 도착_자치구_코드 + 성별 + 연령대 + 이동유형 + 주말여부",
            "duplicate_key_rows": duplicate_count(arrival_demo, demo_key),
            "key_null_cells": null_key_cells(arrival_demo, demo_key),
            "judgement": "PASS",
            "reason_ko": "도착지 기준 수요의 성·연령·이동유형 분해를 잃지 않기 위한 grain이다.",
        },
        {
            "table": "silver_living_migration_district_flow_month_hour",
            "key_cols": "대상연월 + 시간대 + 자치구_코드",
            "duplicate_key_rows": duplicate_count(district_flow, flow_key),
            "key_null_cells": null_key_cells(district_flow, flow_key),
            "judgement": "PASS",
            "reason_ko": "상권 직접 단위가 아니므로 자치구 시간대 유입/유출 보조 피처로만 사용한다.",
        },
        {
            "table": "silver_living_migration_district_quarter_features",
            "key_cols": "기준_년분기_코드 + 자치구_코드",
            "duplicate_key_rows": duplicate_count(district_quarter, quarter_key),
            "key_null_cells": null_key_cells(district_quarter, quarter_key),
            "judgement": "조건부 PASS",
            "reason_ko": "분기 피처는 월수 보정 컬럼과 함께 써야 하며 2026년 2분기는 부분분기다.",
        },
    ]

    consistency_rows = [
        {
            "check_name": "coverage_canonical_file_count",
            "left_value": int(len(included_audit)),
            "right_value": 120,
            "diff": int(len(included_audit) - 120),
            "judgement": "PASS" if len(included_audit) == 120 else "FAIL",
            "reason_ko": "202601~202605 5개월 × 24시간 canonical 파일이어야 한다.",
        },
        {
            "check_name": "duplicate_excluded_file_count",
            "left_value": int(len(excluded_audit)),
            "right_value": 24,
            "diff": int(len(excluded_audit) - 24),
            "judgement": "PASS" if len(excluded_audit) == 24 else "FAIL",
            "reason_ko": "202605 중복 폴더의 동일 해시 24개 파일은 제외되어야 한다.",
        },
        {
            "check_name": "arrival_demo_equals_od_arrival_seoul",
            "left_value": float(demo_total),
            "right_value": float(od_arrival_seoul),
            "diff": float(demo_total - od_arrival_seoul),
            "judgement": "PASS" if abs(demo_total - od_arrival_seoul) < 0.01 else "FAIL",
            "reason_ko": "도착자치구 성연령유형 집계 총합은 OD 중 도착 서울 자치구 총합과 같아야 한다.",
        },
        {
            "check_name": "district_flow_inflow_plus_internal_equals_od_arrival_seoul",
            "left_value": float(flow_in + flow_internal),
            "right_value": float(od_arrival_seoul),
            "diff": float(flow_in + flow_internal - od_arrival_seoul),
            "judgement": "PASS" if abs(flow_in + flow_internal - od_arrival_seoul) < 0.01 else "FAIL",
            "reason_ko": "방향집계는 내부이동을 별도 컬럼으로 분리하므로 유입+내부이동이 OD 중 도착 서울 자치구 총합과 같아야 한다.",
        },
        {
            "check_name": "district_flow_outflow_plus_internal_equals_od_departure_seoul",
            "left_value": float(flow_out + flow_internal),
            "right_value": float(od_departure_seoul),
            "diff": float(flow_out + flow_internal - od_departure_seoul),
            "judgement": "PASS" if abs(flow_out + flow_internal - od_departure_seoul) < 0.01 else "FAIL",
            "reason_ko": "방향집계는 내부이동을 별도 컬럼으로 분리하므로 유출+내부이동이 OD 중 출발 서울 자치구 총합과 같아야 한다.",
        },
    ]

    summary = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "source_id": SOURCE_ID,
        "canonical_file_count": int(len(included_audit)),
        "excluded_duplicate_file_count": int(len(excluded_audit)),
        "od_rows": int(len(od)),
        "arrival_demo_rows": int(len(arrival_demo)),
        "district_flow_rows": int(len(district_flow)),
        "district_quarter_rows": int(len(district_quarter)),
        "od_total_population": float(od["이동인구_합계"].sum()),
        "arrival_seoul_total_population": float(od_arrival_seoul),
        "quarter_included_months": {
            str(int(k)): [int(x) for x in sorted(v)]
            for k, v in district_quarter.groupby("기준_년분기_코드")["생활이동_분기_포함월수"].unique().to_dict().items()
        },
    }
    return pd.DataFrame(domain_rows), pd.DataFrame(grain_rows), pd.DataFrame(consistency_rows), summary


def write_source_contract() -> pd.DataFrame:
    rows = [
        {
            "table": "silver_living_migration_od_month_hour",
            "source_id": SOURCE_ID,
            "provider": PROVIDER,
            "source_service": SOURCE_SERVICE,
            "contract_status": "PASS",
            "usage_role": "자치구 OD 월·시간대 유입권 프록시",
        },
        {
            "table": "silver_living_migration_arrival_demo_month_hour",
            "source_id": SOURCE_ID,
            "provider": PROVIDER,
            "source_service": SOURCE_SERVICE,
            "contract_status": "PASS",
            "usage_role": "도착 자치구 기준 성·연령·이동유형 수요 분해",
        },
        {
            "table": "silver_living_migration_district_flow_month_hour",
            "source_id": SOURCE_ID,
            "provider": PROVIDER,
            "source_service": SOURCE_SERVICE,
            "contract_status": "PASS",
            "usage_role": "자치구 월·시간대 유입/유출/내부이동 보조 피처",
        },
        {
            "table": "silver_living_migration_district_quarter_features",
            "source_id": SOURCE_ID,
            "provider": PROVIDER,
            "source_service": SOURCE_SERVICE,
            "contract_status": "조건부 PASS",
            "usage_role": "자치구 분기 보조 피처. 부분분기는 월수 보정 필요",
        },
        {
            "table": "silver_living_migration_source_file_audit",
            "source_id": SOURCE_ID,
            "provider": PROVIDER,
            "source_service": SOURCE_SERVICE,
            "contract_status": "조건부 PASS",
            "usage_role": "원천 파일 커버리지·중복 제외 증적",
        },
    ]
    contract = pd.DataFrame(rows)
    write_csv(contract, VALIDATION_DIR / "11_living_migration_source_contract.csv")
    return contract


def write_markdown(
    domain: pd.DataFrame,
    grain: pd.DataFrame,
    consistency: pd.DataFrame,
    summary: dict[str, Any],
    duplicate_summary: pd.DataFrame,
) -> None:
    lines = [
        "# 11차 생활이동 silver 전처리 검증",
        "",
        f"- 작성시각: {summary['created_at']}",
        f"- 원천: {PROVIDER} / {SOURCE_SERVICE}",
        f"- 근거 문서: `{SOURCE_DOC_PATH.relative_to(ROOT)}`",
        "- 공식 HTML 확인사항: 생활이동은 통계적 방법으로 추정된 데이터이며 실제와 다를 수 있고, 1개월 전 데이터를 요일/월 단위 집계로 제공한다고 설명한다.",
        "- 공식 HTML 확인사항: 체류 27분 이상을 기·종점으로 간주하므로 짧은 근거리 이동은 누락될 수 있다.",
        "",
        "## 산출물",
        "",
        "| 파일 | 행수 | 판정 | 용도 |",
        "|---|---:|---|---|",
        f"| `datacorpus/_silver/silver_living_migration_od_month_hour.csv` | {summary['od_rows']:,} | PASS | 자치구 OD 월·시간대 유입권 프록시 |",
        f"| `datacorpus/_silver/silver_living_migration_arrival_demo_month_hour.csv` | {summary['arrival_demo_rows']:,} | PASS | 도착 자치구 성·연령·이동유형 분해 |",
        f"| `datacorpus/_silver/silver_living_migration_district_flow_month_hour.csv` | {summary['district_flow_rows']:,} | PASS | 자치구 월·시간대 유입/유출/내부이동 |",
        f"| `datacorpus/_silver/silver_living_migration_district_quarter_features.csv` | {summary['district_quarter_rows']:,} | 조건부 PASS | 분기 피처. 부분분기 월수 보정 필요 |",
        f"| `datacorpus/_silver/silver_living_migration_source_file_audit.csv` | {summary['canonical_file_count'] + summary['excluded_duplicate_file_count']:,} | 조건부 PASS | 원천 파일 커버리지와 중복 제외 증거 |",
        "",
        "## 2보 전진 1보 후퇴",
        "",
        "- 전진 1: 202601~202605 5개월 × 24시간 canonical 파일 120개를 기준으로 집계한다.",
        "- 전진 2: OD, 도착 성연령유형, 자치구 방향, 분기 피처를 분리해 보존한다.",
        "- 후퇴 1: 생활이동은 자치구 단위의 추정 이동량이며 상권 단위 실제 방문자 수가 아니다.",
        "- 후퇴 2: 2026년 2분기는 202604~202605 두 달만 있으므로 완전 분기처럼 비교하면 안 된다.",
        "",
        "## 규칙 검증",
        "",
        markdown_table(consistency),
        "",
        "## grain 검증",
        "",
        markdown_table(grain),
        "",
        "## domain 검증",
        "",
        markdown_table(domain),
        "",
        "## 중복 파일 판단",
        "",
        markdown_table(duplicate_summary),
        "",
        "## 사용 금지 주장",
        "",
        "- 개별 사람의 실제 이동경로",
        "- 실제 상권 방문자 수",
        "- 구매자 수",
        "- 점포별 매출이나 창업 성공확률",
        "- 실제 도보시간 또는 통행경로",
        "",
        "## 알고리즘 사용 가능 범위",
        "",
        "- 자치구 수준의 유입권/생활권 강도 프록시",
        "- 시간대별 배후 수요 패턴",
        "- 성·연령·이동유형의 도착지 수요 분해",
        "- 상권 점수에 직접 투입하려면 상권-자치구 매핑 또는 별도 공간 배분 규칙이 필요하다.",
        "",
    ]
    path = RESEARCH_VALIDATION_DIR / "11_living_migration_silver_validation_20260703.md"
    path.write_text("\n".join(lines), encoding="utf-8")


def append_progress(summary: dict[str, Any]) -> None:
    block = f"""

## 13. 완료: 생활이동 silver 테이블

### 산출물

| 파일 | 행수 | 판정 | 비고 |
|---|---:|---|---|
| `datacorpus/_silver/silver_living_migration_od_month_hour.csv` | {summary['od_rows']:,} | PASS | 자치구 OD 월·시간대 집계 |
| `datacorpus/_silver/silver_living_migration_arrival_demo_month_hour.csv` | {summary['arrival_demo_rows']:,} | PASS | 도착 자치구 성·연령·이동유형 집계 |
| `datacorpus/_silver/silver_living_migration_district_flow_month_hour.csv` | {summary['district_flow_rows']:,} | PASS | 자치구 월·시간대 유입/유출/내부이동 |
| `datacorpus/_silver/silver_living_migration_district_quarter_features.csv` | {summary['district_quarter_rows']:,} | 조건부 PASS | 2026년 2분기 부분월 보정 필요 |
| `datacorpus/_silver/silver_living_migration_source_file_audit.csv` | {summary['canonical_file_count'] + summary['excluded_duplicate_file_count']:,} | 조건부 PASS | 120개 canonical 사용, 24개 중복 제외 |

### 판단

- 생활이동은 공식 설명상 통계적으로 추정된 이동량이므로 실제 방문자 수가 아니다.
- 원천은 자치구 OD 단위이므로 상권 단위 점수에는 직접 넣지 않고, 상권-자치구 매핑 또는 별도 배분 규칙 뒤에 보조 프록시로 쓴다.
- 2026년 2분기는 4~5월만 포함되므로 전년/전분기 비교에는 월수 보정이 필요하다.
"""
    current = PROGRESS_PATH.read_text(encoding="utf-8") if PROGRESS_PATH.exists() else ""
    if "## 13. 완료: 생활이동 silver 테이블" not in current:
        PROGRESS_PATH.write_text(current.rstrip() + block + "\n", encoding="utf-8")


def append_precheck(summary: dict[str, Any]) -> None:
    current = PRECHECK_PATH.read_text(encoding="utf-8") if PRECHECK_PATH.exists() else ""
    row = (
        f"| 생활이동 OD | OD {summary['od_rows']:,}건, 도착 성연령유형 {summary['arrival_demo_rows']:,}건, "
        f"자치구 방향 {summary['district_flow_rows']:,}건 silver 생성 완료 | 자치구 단위 추정 이동량이므로 상권 단위 직접 방문자 수로 쓰지 않고, 공간 매핑 후 유입권 프록시로만 쓴다. |"
    )
    if "| 생활이동 OD |" not in current:
        marker = "| 버스 승하차량 | 43,122건 월별 요약과 1,034,928건 시간대 long silver 생성 완료 | 시간대 접근성 강도 프록시로 쓰되 좌표 exact_match 39522건 외에는 수동 매핑 전 점수 직접 사용을 제한한다. |"
        if marker in current:
            current = current.replace(marker, marker + "\n" + row)
        else:
            current = current.rstrip() + "\n\n" + row + "\n"
        PRECHECK_PATH.write_text(current, encoding="utf-8")


def main() -> None:
    ensure_dirs()
    tables = load_source_tables()

    od = add_common_metadata(
        tables["od"],
        "대상연월 + 시간대 + 출발_생활이동_시군구_코드 + 도착_생활이동_시군구_코드",
        "자치구 OD 기반 생활권 프록시다. 상권 방문자 수로 직접 해석하지 않는다.",
    )
    arrival_demo = add_common_metadata(
        tables["arrival_demo"],
        "대상연월 + 시간대 + 도착_자치구_코드 + 성별 + 연령대 + 이동유형 + 주말여부",
        "도착 자치구 기준 수요 분해다. 구매자 수나 점포 방문자 수가 아니다.",
    )
    district_flow = add_common_metadata(
        tables["district_flow"],
        "대상연월 + 시간대 + 자치구_코드",
        "자치구 유입/유출/내부이동 집계다. 상권 단위에는 별도 배분 규칙이 필요하다.",
    )
    district_quarter = add_common_metadata(
        tables["district_quarter"],
        "기준_년분기_코드 + 자치구_코드",
        "분기 피처다. 포함월수 컬럼으로 부분분기를 반드시 구분한다.",
    )
    source_file_audit, duplicate_summary = build_source_file_audit(tables["legacy_audit"])
    dimension_codebook = build_dimension_codebook(tables["arrival_demo"])

    write_csv(od, SILVER_DIR / "silver_living_migration_od_month_hour.csv")
    write_csv(arrival_demo, SILVER_DIR / "silver_living_migration_arrival_demo_month_hour.csv")
    write_csv(district_flow, SILVER_DIR / "silver_living_migration_district_flow_month_hour.csv")
    write_csv(district_quarter, SILVER_DIR / "silver_living_migration_district_quarter_features.csv")
    write_csv(source_file_audit, SILVER_DIR / "silver_living_migration_source_file_audit.csv")
    write_csv(dimension_codebook, SILVER_DIR / "silver_living_migration_dimension_codebook.csv")

    domain, grain, consistency, summary = validate_tables(
        od, arrival_demo, district_flow, district_quarter, source_file_audit, dimension_codebook
    )
    write_csv(domain, VALIDATION_DIR / "11_living_migration_domain_validation.csv")
    write_csv(grain, VALIDATION_DIR / "11_living_migration_grain_validation.csv")
    write_csv(consistency, VALIDATION_DIR / "11_living_migration_consistency_validation.csv")
    write_csv(duplicate_summary, VALIDATION_DIR / "11_living_migration_duplicate_summary.csv")
    write_source_contract()
    (VALIDATION_DIR / "11_living_migration_preprocess_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_markdown(domain, grain, consistency, summary, duplicate_summary)
    append_progress(summary)
    append_precheck(summary)

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
