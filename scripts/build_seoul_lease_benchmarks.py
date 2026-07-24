from __future__ import annotations

import csv
import hashlib
import json
import os
import sqlite3
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = ROOT / "datacorpus"
SILVER_ROOT = DATA_ROOT / "_silver"
GOLD_ROOT = DATA_ROOT / "_gold"
VALIDATION_ROOT = DATA_ROOT / "_rule_validation"

sys.path.insert(0, str(ROOT / "final_proj" / "backend"))
from app.core.settings import DATABASE_PATH  # noqa: E402

from ingest_common import latest_complete_full_collection  # noqa: E402


SCHEMA_VERSION = "seoul_lease_benchmark.v1"
VALIDATION_SCHEMA_VERSION = "seoul_lease_benchmark_validation.v1"
RONE_SERVICE = "reb_rone_seoul_commercial_market"
GEOGRAPHY = "서울특별시"
EXPECTED_TENANT_ROWS = 1617
EXPECTED_LANDLORD_ROWS = 310

TENANT_PROVINCE = "사업체 주소_시·도"
LANDLORD_PROVINCE = "사업체 주소_시도"
WEIGHT = "가중치"

TENANT_COLUMNS = {
    "region": "사업체 주소_권역",
    "business_format": "업태",
    "industry": "업종",
    "area": "현 계약사항_임대면적_계약(㎡)",
    "deposit": "현 계약사항_보증금(만원)",
    "monthly_rent": "현 계약사항_월세(만원)",
    "management_fee": "현 계약사항_2023년 평균 관리비(만원)",
    "utility_fee": "현 계약사항_관리비 중 사용료(만원)",
    "annual_sales": "2022년 총 매출액",
    "annual_profit": "2022년 총 순이익",
    "startup_cost": "소요비용_총 창업비용",
    "startup_deposit": "소요비용_보증금",
    "startup_key_money": "소요비용_권리금",
}

LANDLORD_COLUMNS = {
    "region": "사업체 주소_권역",
    "business_count": "임대 사업장 개수",
    "protected_contract_count": "상가임대차보호법 보호범위 내 속하는 계약 건수",
    "rent_income": "2022년 월세 총수입(백만원)",
}

NATURAL_KEY = [
    "source_id",
    "series_id",
    "period",
    "segment_type",
    "segment_code",
    "metric_code",
    "statistic",
]

GOLD_COLUMNS = [
    "release_id",
    "source_system",
    "source_id",
    "series_id",
    "period_type",
    "period",
    "geography",
    "segment_type",
    "segment_code",
    "segment_name",
    "metric_code",
    "metric_name",
    "statistic",
    "metric_value",
    "unit",
    "sample_n",
    "weight_sum",
    "source_file",
    "source_sha256",
    "source_vintage",
    "index_base_vintage",
    "cross_vintage_absolute_comparison_allowed",
    "direct_score_allowed",
    "allowed_use",
    "limitations",
    "schema_version",
    "generated_at_utc",
]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def discover_one(pattern: str, label: str) -> Path:
    matches = sorted(DATA_ROOT.glob(pattern))
    if len(matches) != 1:
        raise RuntimeError(f"{label} 파일은 정확히 1개여야 합니다: {matches}")
    return matches[0]


def validate_csv_width(path: Path) -> tuple[int, int]:
    with path.open("r", encoding="cp949", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        row_count = 0
        for line_number, row in enumerate(reader, start=2):
            row_count += 1
            if len(row) != len(header):
                raise RuntimeError(
                    f"{path.name} {line_number}행 폭 불일치: {len(row)}/{len(header)}"
                )
    return row_count, len(header)


def read_design(path: Path, province_field: str) -> tuple[list[str], str]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        items = pd.read_excel(path, sheet_name="항목정보", header=None, engine="openpyxl")
        codes = pd.read_excel(path, sheet_name="코드정보", header=None, engine="openpyxl")

    design_columns = [
        str(row.iloc[1]).strip()
        for _, row in items.iterrows()
        if str(row.iloc[0]).strip().isdigit() and pd.notna(row.iloc[1])
    ]
    matches = codes[
        codes.iloc[:, 1].astype(str).str.strip().eq(province_field)
        & codes.iloc[:, 3].astype(str).str.strip().eq(GEOGRAPHY)
    ]
    if len(matches) != 1:
        raise RuntimeError(f"{path.name}에서 {province_field}=서울특별시 코드를 하나로 결정하지 못했습니다.")
    raw_code = matches.iloc[0, 2]
    code = str(int(raw_code)) if isinstance(raw_code, (int, float, np.integer, np.floating)) else str(raw_code).strip()
    return design_columns, code


def numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series.astype("string").str.replace(",", "", regex=False), errors="coerce")


def canonical_column_label(value: Any) -> str:
    return " ".join(str(value).strip().replace("^", ",").split())


def source_sample_id(role: str, row_number: int, source_hash: str) -> str:
    value = f"{role}|2023|{row_number}|{source_hash}".encode("utf-8")
    return f"{role}_" + hashlib.sha256(value).hexdigest()[:16]


def validate_nonnegative(frame: pd.DataFrame, columns: Iterable[str], label: str) -> None:
    for column in columns:
        values = numeric(frame[column])
        invalid = int((values.notna() & values.lt(0)).sum())
        if invalid:
            raise RuntimeError(f"{label} {column} 음수 {invalid}행")


def load_mdis(
    csv_path: Path,
    design_path: Path,
    province_field: str,
    expected_rows: int,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    physical_rows, physical_columns = validate_csv_width(csv_path)
    frame = pd.read_csv(csv_path, encoding="cp949", dtype="string", keep_default_na=True)
    design_columns, seoul_code = read_design(design_path, province_field)
    raw_canonical = [canonical_column_label(value) for value in frame.columns]
    design_canonical = [canonical_column_label(value) for value in design_columns]
    if raw_canonical != design_canonical:
        missing = sorted(set(design_canonical) - set(raw_canonical))
        extra = sorted(set(raw_canonical) - set(design_canonical))
        raise RuntimeError(
            f"{csv_path.name} 설계서 열 불일치: missing={missing[:5]}, extra={extra[:5]}"
        )
    if physical_rows != len(frame) or physical_columns != len(frame.columns):
        raise RuntimeError(f"{csv_path.name} 물리 행·열과 pandas 해석 결과가 다릅니다.")

    province = frame[province_field].astype("string").str.strip().str.replace(r"\.0$", "", regex=True)
    seoul = frame.loc[province.eq(seoul_code)].copy()
    if len(seoul) != expected_rows:
        raise RuntimeError(
            f"{csv_path.name} 서울 행수 불일치: expected={expected_rows}, actual={len(seoul)}"
        )
    weights = numeric(seoul[WEIGHT])
    if weights.isna().any() or weights.le(0).any():
        raise RuntimeError(f"{csv_path.name} 서울 표본 가중치는 모두 양수여야 합니다.")

    metadata = {
        "source_file": relative(csv_path),
        "source_sha256": sha256_file(csv_path),
        "design_file": relative(design_path),
        "design_sha256": sha256_file(design_path),
        "raw_rows": len(frame),
        "raw_columns": len(frame.columns),
        "normalized_column_label_count": sum(
            raw != design for raw, design in zip(frame.columns, design_columns)
        ),
        "seoul_code_field": province_field,
        "seoul_code": seoul_code,
        "seoul_code_label": GEOGRAPHY,
        "seoul_rows": len(seoul),
        "weight_sum": float(weights.sum()),
        "full_row_duplicate_count": int(seoul.duplicated(keep=False).sum()),
    }
    return seoul, metadata


def build_tenant_silver(frame: pd.DataFrame, metadata: dict[str, Any]) -> pd.DataFrame:
    required = [WEIGHT, TENANT_PROVINCE, *TENANT_COLUMNS.values()]
    missing = sorted(set(required) - set(frame.columns))
    if missing:
        raise RuntimeError(f"임차인 필수 열 누락: {missing}")
    validate_nonnegative(
        frame,
        [
            TENANT_COLUMNS["area"],
            TENANT_COLUMNS["deposit"],
            TENANT_COLUMNS["monthly_rent"],
            TENANT_COLUMNS["management_fee"],
            TENANT_COLUMNS["utility_fee"],
            TENANT_COLUMNS["startup_cost"],
            TENANT_COLUMNS["startup_deposit"],
            TENANT_COLUMNS["startup_key_money"],
        ],
        "임차인",
    )
    area = numeric(frame[TENANT_COLUMNS["area"]])
    if area.isna().any() or area.le(0).any():
        raise RuntimeError("임차인 현 계약 임대면적은 모두 양수여야 합니다.")

    result = pd.DataFrame(index=frame.index)
    result["sample_id"] = [
        source_sample_id("tenant", int(index) + 2, metadata["source_sha256"])
        for index in frame.index
    ]
    result["source_row_number"] = [int(index) + 2 for index in frame.index]
    result["survey_year"] = 2023
    result["geography"] = GEOGRAPHY
    result["province_code"] = metadata["seoul_code"]
    result["region_code"] = frame[TENANT_COLUMNS["region"]].astype("string").str.strip().values
    result["business_format_code"] = frame[TENANT_COLUMNS["business_format"]].astype("string").str.strip().values
    result["industry_code"] = frame[TENANT_COLUMNS["industry"]].astype("string").str.strip().values
    result["weight"] = numeric(frame[WEIGHT]).values
    result["lease_area_m2"] = area.values
    for output, source in (
        ("deposit_10k_krw", "deposit"),
        ("monthly_rent_10k_krw", "monthly_rent"),
        ("monthly_management_fee_10k_krw", "management_fee"),
        ("monthly_utility_fee_10k_krw", "utility_fee"),
        ("annual_sales_reported", "annual_sales"),
        ("annual_profit_reported", "annual_profit"),
        ("startup_cost_reported", "startup_cost"),
        ("startup_deposit_reported", "startup_deposit"),
        ("startup_key_money_reported", "startup_key_money"),
    ):
        result[output] = numeric(frame[TENANT_COLUMNS[source]]).values
    result["deposit_per_m2_10k_krw"] = result["deposit_10k_krw"] / result["lease_area_m2"]
    result["monthly_rent_per_m2_10k_krw"] = result["monthly_rent_10k_krw"] / result["lease_area_m2"]
    result["monthly_management_fee_per_m2_10k_krw"] = (
        result["monthly_management_fee_10k_krw"] / result["lease_area_m2"]
    )
    result["direct_score_allowed"] = 0
    if result["sample_id"].duplicated().any():
        raise RuntimeError("임차인 sample_id 중복")
    return result.reset_index(drop=True)


def build_landlord_silver(frame: pd.DataFrame, metadata: dict[str, Any]) -> pd.DataFrame:
    required = [WEIGHT, LANDLORD_PROVINCE, *LANDLORD_COLUMNS.values()]
    missing = sorted(set(required) - set(frame.columns))
    if missing:
        raise RuntimeError(f"임대인 필수 열 누락: {missing}")
    validate_nonnegative(frame, LANDLORD_COLUMNS.values(), "임대인")

    result = pd.DataFrame(index=frame.index)
    result["sample_id"] = [
        source_sample_id("landlord", int(index) + 2, metadata["source_sha256"])
        for index in frame.index
    ]
    result["source_row_number"] = [int(index) + 2 for index in frame.index]
    result["survey_year"] = 2023
    result["geography"] = GEOGRAPHY
    result["province_code"] = metadata["seoul_code"]
    result["region_code"] = frame[LANDLORD_COLUMNS["region"]].astype("string").str.strip().values
    result["weight"] = numeric(frame[WEIGHT]).values
    result["rental_business_count"] = numeric(frame[LANDLORD_COLUMNS["business_count"]]).values
    result["protected_contract_count"] = numeric(
        frame[LANDLORD_COLUMNS["protected_contract_count"]]
    ).values
    result["rent_income_2022_million_krw"] = numeric(frame[LANDLORD_COLUMNS["rent_income"]]).values
    result["protected_contract_share"] = np.where(
        result["rental_business_count"].gt(0),
        result["protected_contract_count"] / result["rental_business_count"],
        np.nan,
    )
    if (result["protected_contract_share"].dropna() > 1).any():
        raise RuntimeError("임대인 보호범위 계약 건수가 임대 사업장 개수를 초과합니다.")
    result["direct_score_allowed"] = 0
    if result["sample_id"].duplicated().any():
        raise RuntimeError("임대인 sample_id 중복")
    return result.reset_index(drop=True)


def weighted_mean(values: pd.Series, weights: pd.Series) -> tuple[float, int, float]:
    mask = values.notna() & weights.notna() & weights.gt(0)
    if not mask.any():
        raise RuntimeError("가중평균에 사용할 유효 표본이 없습니다.")
    return (
        float(np.average(values.loc[mask].astype(float), weights=weights.loc[mask].astype(float))),
        int(mask.sum()),
        float(weights.loc[mask].sum()),
    )


def base_gold_row(
    *,
    source_system: str,
    source_id: str,
    series_id: str,
    period_type: str,
    period: str,
    segment_type: str,
    segment_code: str,
    segment_name: str,
    metric_code: str,
    metric_name: str,
    statistic: str,
    metric_value: float,
    unit: str,
    sample_n: int | None,
    weight_sum: float | None,
    source_file: str,
    source_sha256: str,
    source_vintage: str,
    index_base_vintage: str = "not_applicable",
    cross_vintage_allowed: int = 0,
    allowed_use: str,
    limitations: str,
    generated_at: str,
) -> dict[str, Any]:
    return {
        "release_id": "",
        "source_system": source_system,
        "source_id": source_id,
        "series_id": series_id,
        "period_type": period_type,
        "period": period,
        "geography": GEOGRAPHY,
        "segment_type": segment_type,
        "segment_code": segment_code,
        "segment_name": segment_name,
        "metric_code": metric_code,
        "metric_name": metric_name,
        "statistic": statistic,
        "metric_value": float(metric_value),
        "unit": unit,
        "sample_n": sample_n,
        "weight_sum": weight_sum,
        "source_file": source_file,
        "source_sha256": source_sha256,
        "source_vintage": source_vintage,
        "index_base_vintage": index_base_vintage,
        "cross_vintage_absolute_comparison_allowed": int(cross_vintage_allowed),
        "direct_score_allowed": 0,
        "allowed_use": allowed_use,
        "limitations": limitations,
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": generated_at,
    }


def add_weighted_row(
    rows: list[dict[str, Any]],
    *,
    values: pd.Series,
    weights: pd.Series,
    metric_code: str,
    metric_name: str,
    unit: str,
    source_id: str,
    series_id: str,
    source_file: str,
    source_sha256: str,
    generated_at: str,
    statistic: str = "weighted_mean",
) -> None:
    value, sample_n, weight_sum = weighted_mean(values, weights)
    rows.append(
        base_gold_row(
            source_system="통계청 MDIS",
            source_id=source_id,
            series_id=series_id,
            period_type="survey_year",
            period="2023",
            segment_type="all",
            segment_code="all",
            segment_name="서울 전체 표본",
            metric_code=metric_code,
            metric_name=metric_name,
            statistic=statistic,
            metric_value=value,
            unit=unit,
            sample_n=sample_n,
            weight_sum=weight_sum,
            source_file=source_file,
            source_sha256=source_sha256,
            source_vintage="2023 survey / downloaded 2026-07-17",
            allowed_use="서울 전체 임대비용 분포의 외부 기준선과 품질 감사",
            limitations="주소·상권 식별자가 없어 개별 상권 값이나 상권별 점수 차이에 사용할 수 없음",
            generated_at=generated_at,
        )
    )


def load_rone_rows() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    latest = latest_complete_full_collection("reb_small_shop_rent", RONE_SERVICE)
    if not latest or not latest.get("raw_directory"):
        raise RuntimeError("완료 처리된 R-ONE 서울 수집본이 없습니다.")
    raw_directory = Path(str(latest["raw_directory"]))
    files = sorted(raw_directory.rglob("*_page_*.json"))
    if not files:
        raise RuntimeError(f"R-ONE raw JSON이 없습니다: {raw_directory}")

    records: list[dict[str, Any]] = []
    table_ids: set[str] = set()
    source_files: dict[str, str] = {}
    for path in files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        blocks = payload.get("SttsApiTblData")
        if not isinstance(blocks, list) or len(blocks) < 2:
            raise RuntimeError(f"R-ONE 응답 구조 오류: {path}")
        head = blocks[0].get("head", [])
        body_rows = blocks[1].get("row", [])
        total = next(
            (int(item["list_total_count"]) for item in head if isinstance(item, dict) and "list_total_count" in item),
            None,
        )
        if total != len(body_rows):
            raise RuntimeError(f"R-ONE head/row 수 불일치: {path} {total}/{len(body_rows)}")
        file_hash = sha256_file(path)
        source_files[relative(path)] = file_hash
        for row in body_rows:
            table_id = str(row.get("STATBL_ID") or "")
            table_ids.add(table_id)
            if table_id == "A_2024_00445":
                if str(row.get("GRP_ID")) != "900002" or str(row.get("GRP_NM")) != "서울":
                    raise RuntimeError("R-ONE 권리금에 비서울 행이 포함됐습니다.")
            elif str(row.get("CLS_NM")) != "서울":
                raise RuntimeError(f"R-ONE {table_id}에 비서울 행이 포함됐습니다.")
            value_text = str(row.get("DTA_VAL") or "").replace(",", "").strip()
            try:
                value = float(value_text)
            except ValueError as exc:
                raise RuntimeError(f"R-ONE 값 숫자 변환 실패: {table_id}/{value_text}") from exc
            records.append({**row, "_source_file": relative(path), "_source_sha256": file_hash, "_value": value})

    if len(table_ids) != 14:
        raise RuntimeError(f"R-ONE 서울 통계표 수 불일치: {len(table_ids)}/14")
    key_fields = ("STATBL_ID", "DTACYCLE_CD", "WRTTIME_IDTFR_ID", "GRP_ID", "CLS_ID", "ITM_ID")
    natural_keys = [tuple(str(row.get(key) or "") for key in key_fields) for row in records]
    if len(natural_keys) != len(set(natural_keys)):
        raise RuntimeError("R-ONE 자연키 중복")
    return records, {
        "raw_directory": str(raw_directory),
        "latest_complete": latest,
        "file_count": len(files),
        "table_count": len(table_ids),
        "row_count": len(records),
        "source_files": source_files,
        "non_seoul_rows": 0,
        "natural_key_duplicates": 0,
    }


def normalized_period(cycle: str, raw_period: str) -> tuple[str, str]:
    if cycle == "QY" and len(raw_period) == 6 and raw_period.isdigit():
        return "quarter", f"{raw_period[:4]}Q{int(raw_period[4:])}"
    if cycle == "YY" and raw_period[:4].isdigit():
        return "year", raw_period[:4]
    return cycle.lower(), raw_period


def build_gold(
    tenant: pd.DataFrame,
    landlord: pd.DataFrame,
    tenant_meta: dict[str, Any],
    landlord_meta: dict[str, Any],
    report_path: Path,
    rone_records: list[dict[str, Any]],
    generated_at: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    tenant_weights = tenant["weight"]
    tenant_series = "mdis_tenant_seoul_2023"
    for values, code, name, unit, statistic in (
        (tenant["lease_area_m2"], "lease_area_m2", "현 계약 임대면적", "㎡/점포", "weighted_mean"),
        (tenant["deposit_10k_krw"], "deposit_10k_krw", "현 계약 보증금", "만원/점포", "weighted_mean"),
        (tenant["monthly_rent_10k_krw"], "monthly_rent_10k_krw", "현 계약 월세", "만원/월/점포", "weighted_mean"),
        (tenant["monthly_management_fee_10k_krw"], "management_fee_10k_krw", "월 관리비", "만원/월/점포", "weighted_mean"),
        (tenant["deposit_per_m2_10k_krw"], "deposit_per_m2", "현 계약 보증금 면적단가", "만원/㎡", "weighted_mean_of_unit_ratios"),
        (tenant["monthly_rent_per_m2_10k_krw"], "monthly_rent_per_m2", "현 계약 월세 면적단가", "만원/㎡/월", "weighted_mean_of_unit_ratios"),
        (tenant["monthly_management_fee_per_m2_10k_krw"], "management_fee_per_m2", "월 관리비 면적단가", "만원/㎡/월", "weighted_mean_of_unit_ratios"),
        ((tenant["startup_key_money_reported"] > 0).astype(float) * 100, "startup_key_money_positive_rate", "창업 시 권리금 지출 표본 비율", "%", "weighted_rate"),
    ):
        add_weighted_row(
            rows,
            values=values,
            weights=tenant_weights,
            metric_code=code,
            metric_name=name,
            unit=unit,
            source_id="mdis_commercial_lease_tenant",
            series_id=tenant_series,
            source_file=tenant_meta["source_file"],
            source_sha256=tenant_meta["source_sha256"],
            generated_at=generated_at,
            statistic=statistic,
        )

    landlord_weights = landlord["weight"]
    for values, code, name, unit in (
        (landlord["rental_business_count"], "rental_business_count", "임대 사업장 개수", "개/임대인"),
        (landlord["protected_contract_count"], "protected_contract_count", "보호범위 내 계약 건수", "건/임대인"),
        (landlord["rent_income_2022_million_krw"], "rent_income_2022", "2022년 월세 총수입", "백만원/년/임대인"),
        (landlord["protected_contract_share"] * 100, "protected_contract_share", "보호범위 내 계약 비중", "%"),
    ):
        add_weighted_row(
            rows,
            values=values,
            weights=landlord_weights,
            metric_code=code,
            metric_name=name,
            unit=unit,
            source_id="mdis_commercial_lease_landlord",
            series_id="mdis_landlord_seoul_2023",
            source_file=landlord_meta["source_file"],
            source_sha256=landlord_meta["source_sha256"],
            generated_at=generated_at,
        )

    report_hash = sha256_file(report_path)
    report_metrics = (
        ("ordinary_rent_per_m2", "통상임대료", 7.49, "만원/㎡/월", 17),
        ("deposit_per_m2", "보증금", 95.61, "만원/㎡", 19),
        ("monthly_rent_per_m2", "월세", 6.24, "만원/㎡/월", 21),
        ("management_fee_per_m2", "공용관리비", 0.29, "만원/㎡/월", 23),
        ("monthly_sales_per_m2", "월 매출액", 46.30, "만원/㎡/월", 42),
    )
    for code, name, value, unit, page in report_metrics:
        rows.append(
            base_gold_row(
                source_system="서울특별시",
                source_id="seoul_commercial_lease_survey",
                series_id="seoul_commercial_lease_survey_2023",
                period_type="survey_year",
                period="2023",
                segment_type="all",
                segment_code="all",
                segment_name="서울 전체 공표값",
                metric_code=code,
                metric_name=name,
                statistic="published_mean",
                metric_value=value,
                unit=unit,
                sample_n=None,
                weight_sum=None,
                source_file=relative(report_path),
                source_sha256=report_hash,
                source_vintage=f"2023 final report page {page}",
                allowed_use="MDIS 표본 통계와 임대비용 단위의 외부 감사 기준",
                limitations="서울시 별도 조사 공표값으로 MDIS와 모집단·조사방법이 달라 수치 일치를 요구할 수 없음",
                generated_at=generated_at,
            )
        )

    for raw in rone_records:
        table_id = str(raw.get("STATBL_ID") or "")
        cycle = str(raw.get("DTACYCLE_CD") or "")
        raw_period = str(raw.get("WRTTIME_IDTFR_ID") or "")
        period_type, period = normalized_period(cycle, raw_period)
        key_money = table_id == "A_2024_00445"
        segment_code = str(raw.get("CLS_ID") or "all") if key_money else str(raw.get("GRP_ID") or "all")
        segment_name = str(raw.get("CLS_NM") or "전체") if key_money else str(raw.get("GRP_NM") or "전체")
        unit = str(raw.get("UI_NM") or "")
        index_series = "지수" in unit or "지수" in str(raw.get("ITM_NM") or "")
        rows.append(
            base_gold_row(
                source_system="한국부동산원 R-ONE",
                source_id="reb_small_shop_rent",
                series_id=table_id,
                period_type=period_type,
                period=period,
                segment_type="industry" if key_money else "property_group",
                segment_code=segment_code,
                segment_name=segment_name,
                metric_code=str(raw.get("ITM_ID") or ""),
                metric_name=str(raw.get("ITM_NM") or ""),
                statistic="published_value",
                metric_value=float(raw["_value"]),
                unit=unit,
                sample_n=None,
                weight_sum=None,
                source_file=str(raw["_source_file"]),
                source_sha256=str(raw["_source_sha256"]),
                source_vintage=raw_period,
                index_base_vintage="source_not_supplied" if index_series else "not_applicable",
                cross_vintage_allowed=0,
                allowed_use="서울 전체 임대비용·공실·권리금의 최근 수준과 방향 참고",
                limitations="서울 집계값이며 상권별 직접값·대체점수로 사용할 수 없음; 지수는 같은 STATBL_ID 안에서만 비교",
                generated_at=generated_at,
            )
        )

    gold = pd.DataFrame(rows, columns=GOLD_COLUMNS)
    if gold[NATURAL_KEY].duplicated().any():
        duplicates = gold.loc[gold[NATURAL_KEY].duplicated(False), NATURAL_KEY].head().to_dict("records")
        raise RuntimeError(f"Gold 자연키 중복: {duplicates}")
    if set(gold["geography"]) != {GEOGRAPHY}:
        raise RuntimeError("Gold에 비서울 지리가 포함됐습니다.")
    if (gold["direct_score_allowed"] != 0).any():
        raise RuntimeError("Gold 직접 점수 사용 금지 플래그 위반")

    release_payload = gold.drop(columns=["release_id", "generated_at_utc"]).sort_values(NATURAL_KEY)
    release_json = release_payload.where(pd.notna(release_payload), None).to_json(
        orient="records", force_ascii=False, double_precision=10
    )
    release_id = "seoul_lease." + hashlib.sha256(release_json.encode("utf-8")).hexdigest()[:16]
    gold["release_id"] = release_id

    metric_lookup = {
        (row["source_id"], row["metric_code"]): float(row["metric_value"])
        for row in rows
    }
    comparisons = []
    for metric in ("deposit_per_m2", "monthly_rent_per_m2", "management_fee_per_m2"):
        mdis_value = metric_lookup[("mdis_commercial_lease_tenant", metric)]
        report_value = metric_lookup[("seoul_commercial_lease_survey", metric)]
        comparisons.append(
            {
                "metric_code": metric,
                "mdis_weighted_mean_of_unit_ratios": mdis_value,
                "seoul_report_published_mean": report_value,
                "absolute_difference": mdis_value - report_value,
                "difference_is_integrity_failure": False,
                "reason": "서로 다른 조사 모집단·표본설계·정의의 독립 기준선이므로 일치를 요구하지 않음",
            }
        )
    return gold, {"release_id": release_id, "cross_source_comparisons": comparisons}


def write_csv_atomic(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    frame.to_csv(temporary, index=False, encoding="utf-8-sig")
    os.replace(temporary, path)


def write_json_atomic(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def sqlite_value(value: Any) -> Any:
    if pd.isna(value):
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    return value


def publish_database(gold: pd.DataFrame) -> dict[str, Any]:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_table = "seoul_lease_benchmark__new"
    table = "seoul_lease_benchmark"
    column_types = {
        column: (
            "REAL"
            if column in {"metric_value", "weight_sum"}
            else "INTEGER"
            if column in {"sample_n", "cross_vintage_absolute_comparison_allowed", "direct_score_allowed"}
            else "TEXT"
        )
        for column in GOLD_COLUMNS
    }
    create_columns = ", ".join(f'"{column}" {column_types[column]}' for column in GOLD_COLUMNS)
    placeholders = ", ".join("?" for _ in GOLD_COLUMNS)
    quoted_columns = ", ".join(f'"{column}"' for column in GOLD_COLUMNS)
    records = [tuple(sqlite_value(value) for value in row) for row in gold[GOLD_COLUMNS].itertuples(index=False, name=None)]

    with sqlite3.connect(DATABASE_PATH, timeout=30) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(f'DROP TABLE IF EXISTS "{temp_table}"')
            conn.execute(f'CREATE TABLE "{temp_table}" ({create_columns})')
            conn.executemany(
                f'INSERT INTO "{temp_table}" ({quoted_columns}) VALUES ({placeholders})',
                records,
            )
            inserted = int(conn.execute(f'SELECT COUNT(*) FROM "{temp_table}"').fetchone()[0])
            if inserted != len(gold):
                raise RuntimeError(f"DB staging 행수 불일치: {inserted}/{len(gold)}")
            duplicate = conn.execute(
                f'SELECT 1 FROM "{temp_table}" GROUP BY '
                + ", ".join(f'"{column}"' for column in NATURAL_KEY)
                + " HAVING COUNT(*) > 1 LIMIT 1"
            ).fetchone()
            if duplicate:
                raise RuntimeError("DB staging 자연키 중복")
            conn.execute(f'DROP TABLE IF EXISTS "{table}"')
            conn.execute(f'ALTER TABLE "{temp_table}" RENAME TO "{table}"')
            conn.execute(
                f'CREATE UNIQUE INDEX "{table}_natural_key" ON "{table}" ('
                + ", ".join(f'"{column}"' for column in NATURAL_KEY)
                + ")"
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        db_rows = int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
        source_counts = {
            str(source_id): int(count)
            for source_id, count in conn.execute(
                f'SELECT source_id, COUNT(*) FROM "{table}" GROUP BY source_id'
            )
        }
        release_ids = {
            str(row[0]) for row in conn.execute(f'SELECT DISTINCT release_id FROM "{table}"')
        }
        unsafe_rows = int(
            conn.execute(
                f'SELECT COUNT(*) FROM "{table}" WHERE COALESCE(direct_score_allowed, 0) != 0'
            ).fetchone()[0]
        )
        quick_check = str(conn.execute("PRAGMA quick_check").fetchone()[0])
    if db_rows != len(gold) or release_ids != {str(gold["release_id"].iloc[0])} or unsafe_rows:
        raise RuntimeError("게시 후 DB 검증 실패")
    return {
        "database_path": str(DATABASE_PATH),
        "table": table,
        "row_count": db_rows,
        "source_counts": source_counts,
        "release_id": next(iter(release_ids)),
        "unsafe_direct_score_rows": unsafe_rows,
        "natural_key_duplicates": 0,
        "quick_check": quick_check,
    }


def main() -> None:
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    tenant_csv = discover_one("임차인_*_데이터/2023_임차인_*.csv", "2023 임차인")
    landlord_csv = discover_one("임대인_*_데이터/2023_임대인_*.csv", "2023 임대인")
    tenant_design = discover_one("2023년_*임차인_파일설계서.xlsx", "2023 임차인 설계서")
    landlord_design = discover_one("2023년_*임대인_파일설계서.xlsx", "2023 임대인 설계서")
    report_path = discover_one("*2023년_서울시_상가임대차_실태조사_최종보고서.pdf", "2023 서울시 보고서")

    tenant_raw, tenant_meta = load_mdis(
        tenant_csv, tenant_design, TENANT_PROVINCE, EXPECTED_TENANT_ROWS
    )
    landlord_raw, landlord_meta = load_mdis(
        landlord_csv, landlord_design, LANDLORD_PROVINCE, EXPECTED_LANDLORD_ROWS
    )
    tenant_silver = build_tenant_silver(tenant_raw, tenant_meta)
    landlord_silver = build_landlord_silver(landlord_raw, landlord_meta)
    rone_records, rone_meta = load_rone_rows()
    gold, gold_meta = build_gold(
        tenant_silver,
        landlord_silver,
        tenant_meta,
        landlord_meta,
        report_path,
        rone_records,
        generated_at,
    )

    tenant_path = SILVER_ROOT / "silver_mdis_seoul_tenant_lease_2023.csv"
    landlord_path = SILVER_ROOT / "silver_mdis_seoul_landlord_lease_2023.csv"
    gold_path = GOLD_ROOT / "gold_seoul_lease_benchmark.csv"
    validation_path = VALIDATION_ROOT / "104_seoul_lease_benchmark_validation.json"
    write_csv_atomic(tenant_silver, tenant_path)
    write_csv_atomic(landlord_silver, landlord_path)
    write_csv_atomic(gold, gold_path)
    database = publish_database(gold)

    required_sources = {
        "mdis_commercial_lease_tenant",
        "mdis_commercial_lease_landlord",
        "seoul_commercial_lease_survey",
        "reb_small_shop_rent",
    }
    checks = {
        "tenant_schema_exact": True,
        "landlord_schema_exact": True,
        "tenant_seoul_rows": len(tenant_silver) == EXPECTED_TENANT_ROWS,
        "landlord_seoul_rows": len(landlord_silver) == EXPECTED_LANDLORD_ROWS,
        "silver_non_seoul_rows": int((tenant_silver["geography"] != GEOGRAPHY).sum())
        + int((landlord_silver["geography"] != GEOGRAPHY).sum()),
        "silver_sample_id_duplicates": int(tenant_silver["sample_id"].duplicated().sum())
        + int(landlord_silver["sample_id"].duplicated().sum()),
        "gold_natural_key_duplicates": int(gold[NATURAL_KEY].duplicated().sum()),
        "gold_non_seoul_rows": int((gold["geography"] != GEOGRAPHY).sum()),
        "gold_unsafe_direct_score_rows": int((gold["direct_score_allowed"] != 0).sum()),
        "gold_required_sources_present": set(gold["source_id"]) == required_sources,
        "rone_table_count": rone_meta["table_count"],
        "rone_non_seoul_rows": rone_meta["non_seoul_rows"],
        "rone_natural_key_duplicates": rone_meta["natural_key_duplicates"],
        "db_gold_row_count_match": database["row_count"] == len(gold),
        "db_gold_release_match": database["release_id"] == gold_meta["release_id"],
        "db_all_sources_present": set(database["source_counts"]) == required_sources,
        "db_unsafe_direct_score_rows": database["unsafe_direct_score_rows"],
        "db_quick_check": database["quick_check"],
    }
    failed: list[str] = []
    for key, value in checks.items():
        if isinstance(value, (bool, np.bool_)):
            is_failure = not bool(value)
        elif key.endswith(("_rows", "_duplicates")):
            is_failure = int(value) != 0
        elif key == "db_quick_check":
            is_failure = value != "ok"
        elif key == "rone_table_count":
            is_failure = int(value) != 14
        else:
            is_failure = False
        if is_failure:
            failed.append(key)
    validation = {
        "schema_version": VALIDATION_SCHEMA_VERSION,
        "status": "pass" if not failed else "fail",
        "failed_count": len(failed),
        "failed_checks": failed,
        "generated_at_utc": generated_at,
        "scope": "서울특별시 only",
        "checks": checks,
        "sources": {
            "tenant": tenant_meta,
            "landlord": landlord_meta,
            "rone": rone_meta,
            "seoul_report": {
                "source_file": relative(report_path),
                "source_sha256": sha256_file(report_path),
                "audited_pages": [17, 19, 21, 23, 42],
            },
        },
        "exclusions": {
            "2018_mdis": "시·도 식별 열이 없어 서울특별시를 정확히 분리할 수 없으므로 제품 입력에서 제외",
            "direct_trade_area_scoring": "MDIS·서울시 보고서·R-ONE 모두 상권 식별자가 없는 서울 집계/표본이므로 금지",
        },
        "cross_source_comparisons": gold_meta["cross_source_comparisons"],
        "artifacts": {
            "tenant_silver": {"path": relative(tenant_path), "rows": len(tenant_silver), "sha256": sha256_file(tenant_path)},
            "landlord_silver": {"path": relative(landlord_path), "rows": len(landlord_silver), "sha256": sha256_file(landlord_path)},
            "gold": {"path": relative(gold_path), "rows": len(gold), "sha256": sha256_file(gold_path), "release_id": gold_meta["release_id"]},
            "database": database,
        },
    }
    write_json_atomic(validation, validation_path)
    if failed:
        raise RuntimeError("서울 임대비용 기준선 검증 실패: " + ", ".join(failed))
    print(
        json.dumps(
            {
                "status": validation["status"],
                "failed_count": validation["failed_count"],
                "tenant_seoul_rows": len(tenant_silver),
                "landlord_seoul_rows": len(landlord_silver),
                "rone_rows": rone_meta["row_count"],
                "gold_rows": len(gold),
                "database_rows": database["row_count"],
                "release_id": gold_meta["release_id"],
                "validation_path": str(validation_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
