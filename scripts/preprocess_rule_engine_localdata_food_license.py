from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RAW_BASE_DIR = ROOT / "datacorpus" / "_raw_ingest" / "20260703" / "seoul_open_data" / "full"
SILVER_DIR = ROOT / "datacorpus" / "_silver"
VALIDATION_DIR = ROOT / "datacorpus" / "_rule_validation"
RESEARCH_VALIDATION_DIR = ROOT / "research" / "rule_validation"

SNAPSHOT_DATE = "2026-07-03"
PROVIDER = "서울열린데이터광장/행정안전부 지방행정 인허가"
SOURCE_CRS_RECORDED = "Bessel 중부원점TM(EPSG:5174)"

RAW_OUT_PATH = SILVER_DIR / "silver_localdata_food_license_raw_seoul.csv"
SGG_UPTAE_STATUS_PATH = SILVER_DIR / "silver_localdata_food_license_sgg_uptae_status_summary.csv"
MONTH_EVENT_PATH = SILVER_DIR / "silver_localdata_food_license_open_close_monthly.csv"
STATUS_CODEBOOK_PATH = SILVER_DIR / "silver_localdata_food_license_status_codebook.csv"
SOURCE_AUDIT_PATH = SILVER_DIR / "silver_localdata_food_license_source_file_audit.csv"
ISSUE_SAMPLE_PATH = SILVER_DIR / "silver_localdata_food_license_quality_issue_samples.csv"
DUPLICATE_AUDIT_PATH = SILVER_DIR / "silver_localdata_food_license_duplicate_key_audit.csv"

DOMAIN_VALIDATION_PATH = VALIDATION_DIR / "14_localdata_food_license_domain_validation.csv"
GRAIN_VALIDATION_PATH = VALIDATION_DIR / "14_localdata_food_license_grain_validation.csv"
CONSISTENCY_VALIDATION_PATH = VALIDATION_DIR / "14_localdata_food_license_consistency_validation.csv"
SOURCE_CONTRACT_PATH = VALIDATION_DIR / "14_localdata_food_license_source_contract.csv"
MD_REPORT_PATH = RESEARCH_VALIDATION_DIR / "14_localdata_food_license_silver_validation_20260703.md"

SERVICE_CONFIGS = {
    "LOCALDATA_072404": {
        "source_id": "seoul_localdata_general_restaurant_license",
        "license_category": "일반음식점",
        "dataset_name": "서울시 일반음식점 인허가 정보",
        "doc_paths": [
            "research/algorithm_evidence_sources/data_docs/seoul_open_data_general_restaurant_license_OA-16094.html",
            "research/algorithm_evidence_sources/data_docs/data_go_kr_localdata_general_restaurant_file.html",
            "research/algorithm_evidence_sources/data_docs/data_go_kr_localdata_core_data_page.html",
        ],
    },
    "LOCALDATA_072405": {
        "source_id": "seoul_localdata_rest_cafe_license",
        "license_category": "휴게음식점",
        "dataset_name": "서울시 휴게음식점 인허가 정보",
        "doc_paths": [
            "research/algorithm_evidence_sources/data_docs/data_go_kr_localdata_rest_cafe_file.html",
            "research/algorithm_evidence_sources/data_docs/data_go_kr_localdata_core_data_page.html",
        ],
    },
}

RAW_COLUMNS = [
    "OPNSFTEAMCODE",
    "MGTNO",
    "APVPERMYMD",
    "TRDSTATEGBN",
    "TRDSTATENM",
    "DTLSTATEGBN",
    "DTLSTATENM",
    "DCBYMD",
    "SITETEL",
    "SITEAREA",
    "SITEPOSTNO",
    "SITEWHLADDR",
    "RDNWHLADDR",
    "RDNPOSTNO",
    "BPLCNM",
    "LASTMODTS",
    "UPDATEGBN",
    "UPDATEDT",
    "UPTAENM",
    "X",
    "Y",
    "SNTUPTAENM",
    "MANEIPCNT",
    "WMEIPCNT",
    "TRDPJUBNSENM",
    "LVSENM",
    "WTRSPLYFACILSENM",
    "HOFFEPCNT",
    "FCTYOWKEPCNT",
    "FCTYSILJOBEPCNT",
    "FCTYPDTJOBEPCNT",
    "BDNGOWNSENM",
    "ISREAM",
    "MONAM",
    "MULTUSNUPSOYN",
    "FACILTOTSCP",
    "JTUPSOASGNNO",
    "JTUPSOMAINEDF",
    "HOMEPAGE",
]

NORMALIZED_COLUMNS = [
    "source_id",
    "provider",
    "service_code",
    "license_category",
    "dataset_name",
    "snapshot_date",
    "source_file",
    "source_crs_recorded",
    "원천행번호",
    "관리번호",
    "인허가기관코드",
    "사업장명",
    "업태명",
    "인허가일자",
    "인허가_년월",
    "폐업일자",
    "폐업_년월",
    "영업상태코드",
    "영업상태명",
    "상세영업상태코드",
    "상세영업상태명",
    "상태그룹",
    "영업중여부",
    "폐업여부",
    "소재지전체주소",
    "도로명전체주소",
    "자치구_코드",
    "자치구_코드_명",
    "X_EPSG5174",
    "Y_EPSG5174",
    "좌표유효여부",
    "서울_TM_bbox_범위여부",
    "면적_제곱미터",
    "면적유효여부",
    "주소보유여부",
    "점수직접사용상태",
]

SILVER_COLUMNS = RAW_COLUMNS + NORMALIZED_COLUMNS


def ensure_dirs() -> None:
    for path in [SILVER_DIR, VALIDATION_DIR, RESEARCH_VALIDATION_DIR]:
        path.mkdir(parents=True, exist_ok=True)


def clean_text(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "none"}:
        return ""
    return text


def to_number(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series.map(clean_text).str.replace(",", "", regex=False), errors="coerce")


def parse_date_text(value: Any) -> str:
    text = clean_text(value)
    digits = re.sub(r"[^0-9]", "", text)
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return ""


def ym_from_date_text(date_text: str) -> str:
    if len(date_text) >= 7:
        return date_text[:7].replace("-", "")
    return ""


def parse_file_range(path: Path) -> tuple[int, int]:
    parts = path.stem.split("_")
    return int(parts[-2]), int(parts[-1])


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


def write_csv(df: pd.DataFrame, path: Path) -> None:
    df.to_csv(path, index=False, encoding="utf-8-sig")


def append_csv(df: pd.DataFrame, path: Path, first_write: bool) -> None:
    df.to_csv(
        path,
        index=False,
        encoding="utf-8-sig" if first_write else "utf-8",
        mode="w" if first_write else "a",
        header=first_write,
    )


def load_sgg_code_map() -> dict[str, str]:
    path = SILVER_DIR / "silver_trade_area_master.csv"
    if not path.exists():
        return {}
    df = pd.read_csv(path, encoding="utf-8-sig", dtype=str, usecols=["자치구_코드", "자치구_코드_명"])
    df = df.dropna().drop_duplicates("자치구_코드_명")
    return dict(zip(df["자치구_코드_명"], df["자치구_코드"]))


def extract_sgg_name(address: str) -> str:
    match = re.search(r"서울특별시\s+([가-힣]+구)", clean_text(address))
    return match.group(1) if match else ""


def status_group(row: pd.Series) -> str:
    state_code = clean_text(row.get("TRDSTATEGBN"))
    state_name = clean_text(row.get("TRDSTATENM"))
    detail_name = clean_text(row.get("DTLSTATENM"))
    close_date = parse_date_text(row.get("DCBYMD"))
    if state_code == "01" or "영업" in state_name:
        return "영업"
    if state_code == "03" or "폐업" in state_name or "폐업" in detail_name or close_date:
        return "폐업"
    if "취소" in state_name or "취소" in detail_name:
        return "취소"
    if "말소" in state_name or "말소" in detail_name:
        return "말소"
    return "기타"


def normalize_rows(df: pd.DataFrame, service_code: str, source_file: Path, row_offset: int, sgg_map: dict[str, str]) -> pd.DataFrame:
    cfg = SERVICE_CONFIGS[service_code]
    out = df.copy()
    for col in RAW_COLUMNS:
        if col not in out.columns:
            out[col] = ""
        out[col] = out[col].map(clean_text)

    out["source_id"] = cfg["source_id"]
    out["provider"] = PROVIDER
    out["service_code"] = service_code
    out["license_category"] = cfg["license_category"]
    out["dataset_name"] = cfg["dataset_name"]
    out["snapshot_date"] = SNAPSHOT_DATE
    out["source_file"] = source_file.relative_to(ROOT).as_posix()
    out["source_crs_recorded"] = SOURCE_CRS_RECORDED
    out["원천행번호"] = range(row_offset + 1, row_offset + len(out) + 1)
    out["관리번호"] = out["MGTNO"]
    out["인허가기관코드"] = out["OPNSFTEAMCODE"]
    out["사업장명"] = out["BPLCNM"]
    out["업태명"] = out["UPTAENM"].where(out["UPTAENM"].ne(""), out["SNTUPTAENM"])
    out["인허가일자"] = out["APVPERMYMD"].map(parse_date_text)
    out["인허가_년월"] = out["인허가일자"].map(ym_from_date_text)
    out["폐업일자"] = out["DCBYMD"].map(parse_date_text)
    out["폐업_년월"] = out["폐업일자"].map(ym_from_date_text)
    out["영업상태코드"] = out["TRDSTATEGBN"]
    out["영업상태명"] = out["TRDSTATENM"]
    out["상세영업상태코드"] = out["DTLSTATEGBN"]
    out["상세영업상태명"] = out["DTLSTATENM"]
    out["상태그룹"] = out.apply(status_group, axis=1)
    out["영업중여부"] = out["상태그룹"].eq("영업")
    out["폐업여부"] = out["상태그룹"].eq("폐업")
    out["소재지전체주소"] = out["SITEWHLADDR"]
    out["도로명전체주소"] = out["RDNWHLADDR"]
    sgg_from_road = out["도로명전체주소"].map(extract_sgg_name)
    sgg_from_site = out["소재지전체주소"].map(extract_sgg_name)
    out["자치구_코드_명"] = sgg_from_road.where(sgg_from_road.ne(""), sgg_from_site)
    out["자치구_코드"] = out["자치구_코드_명"].map(sgg_map).fillna("")
    out["X_EPSG5174"] = to_number(out["X"])
    out["Y_EPSG5174"] = to_number(out["Y"])
    out["좌표유효여부"] = out["X_EPSG5174"].notna() & out["Y_EPSG5174"].notna()
    # 서울 EPSG:5174 원천 좌표의 1차 품질검사용 넓은 bbox다. 좌표계 변환/거리계산 확정 기준은 아니다.
    out["서울_TM_bbox_범위여부"] = out["X_EPSG5174"].between(160000, 230000) & out["Y_EPSG5174"].between(410000, 480000)
    out["면적_제곱미터"] = to_number(out["SITEAREA"]).where(to_number(out["SITEAREA"]).notna(), to_number(out["FACILTOTSCP"]))
    out["면적유효여부"] = out["면적_제곱미터"].notna() & (out["면적_제곱미터"] >= 0)
    out["주소보유여부"] = out["소재지전체주소"].ne("") | out["도로명전체주소"].ne("")
    out["점수직접사용상태"] = "조건부: 인허가 이력 프록시. 상권 공간매칭과 업종 매핑 후 경쟁/안정성 보조축에 반영"
    return out[SILVER_COLUMNS]


def source_files_for_service(service_code: str) -> list[Path]:
    service_dir = RAW_BASE_DIR / service_code
    paths = sorted(service_dir.glob("*.json"), key=lambda p: parse_file_range(p))
    if not paths:
        raise FileNotFoundError(f"{service_code} 원천 JSON 파일을 찾지 못했습니다.")
    return paths


def aggregate_status(df: pd.DataFrame) -> pd.DataFrame:
    work = df.assign(
        인허가건수=1,
        영업중건수=df["영업중여부"].astype(int),
        폐업건수=df["폐업여부"].astype(int),
        좌표유효건수=df["좌표유효여부"].astype(int),
        주소보유건수=df["주소보유여부"].astype(int),
        면적유효건수=df["면적유효여부"].astype(int),
        면적합계_제곱미터=df["면적_제곱미터"].fillna(0),
    )
    keys = ["license_category", "자치구_코드", "자치구_코드_명", "업태명", "상태그룹"]
    return (
        work.groupby(keys, dropna=False)[
            ["인허가건수", "영업중건수", "폐업건수", "좌표유효건수", "주소보유건수", "면적유효건수", "면적합계_제곱미터"]
        ]
        .sum()
        .reset_index()
    )


def aggregate_month_events(df: pd.DataFrame) -> pd.DataFrame:
    keys_base = ["license_category", "자치구_코드", "자치구_코드_명", "업태명"]
    open_df = df[df["인허가_년월"].ne("")].copy()
    close_df = df[df["폐업_년월"].ne("")].copy()
    open_agg = (
        open_df.assign(년월=open_df["인허가_년월"], 인허가건수=1)
        .groupby(keys_base + ["년월"], dropna=False)["인허가건수"]
        .sum()
        .reset_index()
    )
    close_agg = (
        close_df.assign(년월=close_df["폐업_년월"], 폐업건수=1)
        .groupby(keys_base + ["년월"], dropna=False)["폐업건수"]
        .sum()
        .reset_index()
    )
    merged = open_agg.merge(close_agg, on=keys_base + ["년월"], how="outer").fillna({"인허가건수": 0, "폐업건수": 0})
    merged["인허가건수"] = merged["인허가건수"].astype(int)
    merged["폐업건수"] = merged["폐업건수"].astype(int)
    return merged


def aggregate_status_codebook(df: pd.DataFrame) -> pd.DataFrame:
    work = df.assign(건수=1)
    keys = ["license_category", "영업상태코드", "영업상태명", "상세영업상태코드", "상세영업상태명", "상태그룹"]
    return work.groupby(keys, dropna=False)["건수"].sum().reset_index()


def merge_aggregates(parts: list[pd.DataFrame], keys: list[str]) -> pd.DataFrame:
    if not parts:
        return pd.DataFrame(columns=keys)
    df = pd.concat(parts, ignore_index=True)
    value_cols = [c for c in df.columns if c not in keys]
    return df.groupby(keys, dropna=False)[value_cols].sum().reset_index().sort_values(keys).reset_index(drop=True)


def build_validation_rows(metrics: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    source_contract = pd.DataFrame(
        [
            {
                "source_id": cfg["source_id"],
                "service_code": service_code,
                "provider": PROVIDER,
                "dataset_name": cfg["dataset_name"],
                "row_count": metrics["service_rows"][service_code],
                "list_total_count": metrics["service_total_counts"][service_code],
                "source_crs_recorded": SOURCE_CRS_RECORDED,
                "doc_paths": ";".join(cfg["doc_paths"]),
                "usage_role": "식품업 인허가·폐업 이력 기반 경쟁/안정성 보조 프록시",
                "score_use_status": "상권 공간매칭과 업종 매핑 후 조건부 사용",
            }
            for service_code, cfg in SERVICE_CONFIGS.items()
        ]
    )
    domain = pd.DataFrame(
        [
            {
                "검증항목": "전체 원천 row와 raw silver row 일치",
                "측정값": metrics["raw_output_rows"],
                "기준값": metrics["expected_rows"],
                "판정": "PASS" if metrics["raw_output_rows"] == metrics["expected_rows"] else "FAIL",
                "근거": "인허가 이력은 개폐업 추이를 보존해야 하므로 원천 row 누락이 있으면 안 된다.",
            },
            {
                "검증항목": "일반음식점 페이지 커버리지",
                "측정값": metrics["service_rows"]["LOCALDATA_072404"],
                "기준값": metrics["service_total_counts"]["LOCALDATA_072404"],
                "판정": "PASS" if metrics["service_rows"]["LOCALDATA_072404"] == metrics["service_total_counts"]["LOCALDATA_072404"] else "FAIL",
                "근거": "서울 일반음식점 인허가 원천의 list_total_count와 실제 적재 row가 같아야 한다.",
            },
            {
                "검증항목": "휴게음식점 페이지 커버리지",
                "측정값": metrics["service_rows"]["LOCALDATA_072405"],
                "기준값": metrics["service_total_counts"]["LOCALDATA_072405"],
                "판정": "PASS" if metrics["service_rows"]["LOCALDATA_072405"] == metrics["service_total_counts"]["LOCALDATA_072405"] else "FAIL",
                "근거": "서울 휴게음식점 인허가 원천의 list_total_count와 실제 적재 row가 같아야 한다.",
            },
            {
                "검증항목": "공식 문서 근거 보존",
                "측정값": metrics["doc_exists_count"],
                "기준값": metrics["doc_expected_count"],
                "판정": "PASS" if metrics["doc_exists_count"] == metrics["doc_expected_count"] else "FAIL",
                "근거": "좌표계, 제공 목적, 인허가/영업상태 설명은 research 공식 문서에 근거해야 한다.",
            },
        ]
    )
    grain = pd.DataFrame(
        [
            {
                "검증항목": "관리번호 결측",
                "측정값": metrics["missing_mgtno_rows"],
                "기준값": "0",
                "판정": "PASS" if metrics["missing_mgtno_rows"] == 0 else "FAIL",
                "근거": "인허가 raw grain은 license_category+관리번호+인허가기관코드다.",
            },
            {
                "검증항목": "관리번호+기관코드 중복",
                "측정값": metrics["duplicate_license_key_rows"],
                "기준값": "0",
                "판정": "PASS" if metrics["duplicate_license_key_rows"] == 0 else "CONDITIONAL_PASS",
                "근거": "중복은 개폐업 수를 부풀릴 수 있으므로 확인해야 한다. 중복이 있으면 원천 이력 중복인지 재검토한다.",
            },
            {
                "검증항목": "자치구×업태×상태 집계 합계",
                "측정값": metrics["status_summary_sum"],
                "기준값": metrics["raw_output_rows"],
                "판정": "PASS" if metrics["status_summary_sum"] == metrics["raw_output_rows"] else "FAIL",
                "근거": "경쟁/안정성 프록시 집계가 raw 전체를 빠짐없이 대표해야 한다.",
            },
            {
                "검증항목": "월별 인허가 이벤트 합계",
                "측정값": metrics["monthly_open_sum"],
                "기준값": metrics["permit_date_rows"],
                "판정": "PASS" if metrics["monthly_open_sum"] == metrics["permit_date_rows"] else "FAIL",
                "근거": "월별 개업 추이는 인허가일자 보유 row 전체에서 만들어야 한다.",
            },
            {
                "검증항목": "월별 폐업 이벤트 합계",
                "측정값": metrics["monthly_close_sum"],
                "기준값": metrics["close_date_rows"],
                "판정": "PASS" if metrics["monthly_close_sum"] == metrics["close_date_rows"] else "FAIL",
                "근거": "월별 폐업 추이는 폐업일자 보유 row 전체에서 만들어야 한다.",
            },
        ]
    )
    consistency = pd.DataFrame(
        [
            {
                "검증항목": "영업상태 결측",
                "측정값": metrics["missing_status_rows"],
                "기준값": "0",
                "판정": "PASS" if metrics["missing_status_rows"] == 0 else "FAIL",
                "근거": "영업/폐업 상태가 없으면 안정성 판단 프록시로 쓸 수 없다.",
            },
            {
                "검증항목": "자치구 파싱 실패",
                "측정값": metrics["missing_sgg_rows"],
                "기준값": "0",
                "판정": "PASS" if metrics["missing_sgg_rows"] == 0 else "CONDITIONAL_PASS",
                "근거": "상권 공간매칭 전에는 자치구가 최소 공간 fallback이다.",
            },
            {
                "검증항목": "좌표 결측 또는 숫자 변환 실패",
                "측정값": metrics["invalid_coord_rows"],
                "기준값": "0",
                "판정": "PASS" if metrics["invalid_coord_rows"] == 0 else "CONDITIONAL_PASS",
                "근거": "좌표가 있어야 이후 상권 polygon 또는 최근접 상권 매칭이 가능하다.",
            },
            {
                "검증항목": "서울 TM bbox 밖 좌표",
                "측정값": metrics["outside_tm_bbox_rows"],
                "기준값": "0",
                "판정": "PASS" if metrics["outside_tm_bbox_rows"] == 0 else "CONDITIONAL_PASS",
                "근거": "공식 좌표계가 EPSG:5174이므로 1차 범위 검증으로 좌표 이상치를 분리한다.",
            },
            {
                "검증항목": "폐업일자가 인허가일자보다 빠른 row",
                "측정값": metrics["date_contradiction_rows"],
                "기준값": "0",
                "판정": "PASS" if metrics["date_contradiction_rows"] == 0 else "CONDITIONAL_PASS",
                "근거": "운영기간/안정성 프록시 계산 전 날짜 역전 row는 제외 또는 보정 검토가 필요하다.",
            },
            {
                "검증항목": "폐업 상태인데 폐업일자 결측",
                "측정값": metrics["closed_without_close_date_rows"],
                "기준값": "0",
                "판정": "PASS" if metrics["closed_without_close_date_rows"] == 0 else "CONDITIONAL_PASS",
                "근거": "폐업 건수에는 쓸 수 있지만 월별 폐업 추이에는 쓸 수 없는 row를 분리해야 한다.",
            },
            {
                "검증항목": "주소 결측",
                "측정값": metrics["missing_address_rows"],
                "기준값": "0",
                "판정": "PASS" if metrics["missing_address_rows"] == 0 else "CONDITIONAL_PASS",
                "근거": "주소는 좌표 검증과 사용자 표시 보조 정보다. 점수 직접값은 아니다.",
            },
        ]
    )
    return source_contract, domain, grain, consistency


def write_report(
    source_contract: pd.DataFrame,
    domain: pd.DataFrame,
    grain: pd.DataFrame,
    consistency: pd.DataFrame,
    metrics: dict[str, Any],
) -> None:
    report = f"""# 서울 식품 인허가 silver 검증 보고서

작성일: {SNAPSHOT_DATE}

## 1. 처리 목적

서울 일반음식점·휴게음식점 인허가 원천은 인허가일자, 영업상태, 폐업일자, 업태명, 주소, 좌표를 제공한다. `research/algorithm_evidence_sources/data_docs/data_go_kr_localdata_core_data_page.html`는 인허가 이력을 상권 분석, 업종별 개·폐업 추이, 경쟁도, 폐업 위험도, 지역 경제 모니터링에 활용할 수 있다고 설명한다. 일반/휴게음식점 공식 문서는 좌표계가 `{SOURCE_CRS_RECORDED}`이며 위경도 좌표가 아니라고 설명한다.

따라서 이번 산출물은 개별 점포 성공확률이 아니라, 식품업 개폐업 이력과 영업상태를 기반으로 한 경쟁·안정성 보조 프록시다.

## 2. 사용 원천

{markdown_table(source_contract[["source_id", "service_code", "dataset_name", "row_count", "list_total_count", "source_crs_recorded", "score_use_status"]])}

## 3. 생성 산출물

| 산출물 | 행 수 | 역할 |
|---|---:|---|
| `datacorpus/_silver/silver_localdata_food_license_raw_seoul.csv` | {metrics["raw_output_rows"]:,} | 일반/휴게음식점 인허가 원천 row 보존 |
| `datacorpus/_silver/silver_localdata_food_license_sgg_uptae_status_summary.csv` | {metrics["status_summary_rows"]:,} | 자치구×업태×영업상태 요약 |
| `datacorpus/_silver/silver_localdata_food_license_open_close_monthly.csv` | {metrics["monthly_rows"]:,} | 월별 인허가/폐업 이벤트 |
| `datacorpus/_silver/silver_localdata_food_license_status_codebook.csv` | {metrics["status_codebook_rows"]:,} | 영업상태/상세상태 코드북 |
| `datacorpus/_silver/silver_localdata_food_license_source_file_audit.csv` | {metrics["source_audit_rows"]:,} | 원천 JSON 페이지별 커버리지 |
| `datacorpus/_silver/silver_localdata_food_license_duplicate_key_audit.csv` | {metrics["duplicate_audit_rows"]:,} | 관리번호+기관코드 중복 row 검토 |

## 4. 도메인 검증

{markdown_table(domain)}

## 5. grain 검증

{markdown_table(grain)}

## 6. 정합성 검증

{markdown_table(consistency)}

## 7. 알고리즘 사용 판단

- 사용 가능: 자치구·업태별 영업/폐업 분포, 월별 인허가/폐업 추이, 식품업 경쟁/안정성 보조 신호.
- 조건부 사용: 상권 polygon 매칭과 서울 서비스업종 매핑이 끝난 뒤 상권·업종 점수에 반영한다.
- 사용 금지: 개별 점포 성공확률, 개별 매출 보장, 개별 월세·권리금·수익성 판단.
- 좌표 주의: X/Y는 `{SOURCE_CRS_RECORDED}`로 기록하며, 위경도처럼 지도에 직접 찍지 않는다.

## 8. 2보 전진 1보 후퇴 검토

1. 전진: 일반음식점 {metrics["service_rows"]["LOCALDATA_072404"]:,}건과 휴게음식점 {metrics["service_rows"]["LOCALDATA_072405"]:,}건을 모두 보존했다.
2. 전진: 인허가/폐업 월별 이벤트를 별도 테이블로 만들어 성장·안정성 검증 재료를 확보했다.
3. 후퇴 검토: 좌표계가 EPSG:5174이므로 위경도 지도 표시와 직접 거리 계산은 보류했다.
4. 재검토 결과: 이 원천은 SBDC 현재 영업 POI와 다르게 폐업 이력을 포함하므로, 경쟁/안정성 축의 중요한 보조근거로 유지한다.
"""
    MD_REPORT_PATH.write_text(report, encoding="utf-8")


def main() -> None:
    ensure_dirs()
    for path in [RAW_OUT_PATH, ISSUE_SAMPLE_PATH, DUPLICATE_AUDIT_PATH]:
        if path.exists():
            path.unlink()

    sgg_map = load_sgg_code_map()
    first_write = True
    row_offset = 0
    seen_keys: set[tuple[str, str, str]] = set()
    duplicate_license_key_rows = 0
    issue_samples: list[pd.DataFrame] = []
    status_parts: list[pd.DataFrame] = []
    month_parts: list[pd.DataFrame] = []
    codebook_parts: list[pd.DataFrame] = []
    source_records: list[dict[str, Any]] = []
    service_rows = {service_code: 0 for service_code in SERVICE_CONFIGS}
    service_total_counts: dict[str, int] = {}

    missing_mgtno_rows = 0
    missing_status_rows = 0
    missing_sgg_rows = 0
    invalid_coord_rows = 0
    outside_tm_bbox_rows = 0
    missing_address_rows = 0
    date_contradiction_rows = 0
    closed_without_close_date_rows = 0
    permit_date_rows = 0
    close_date_rows = 0

    for service_code in SERVICE_CONFIGS:
        for json_path in source_files_for_service(service_code):
            data = json.loads(json_path.read_text(encoding="utf-8"))
            root = data.get(service_code, {})
            rows = root.get("row", []) if isinstance(root, dict) else []
            result = root.get("RESULT", {}) if isinstance(root, dict) else {}
            total_count = int(root.get("list_total_count") or 0)
            service_total_counts.setdefault(service_code, total_count)
            start, end = parse_file_range(json_path)
            source_records.append(
                {
                    "source_id": SERVICE_CONFIGS[service_code]["source_id"],
                    "provider": PROVIDER,
                    "service_code": service_code,
                    "license_category": SERVICE_CONFIGS[service_code]["license_category"],
                    "source_file": json_path.relative_to(ROOT).as_posix(),
                    "request_start": start,
                    "request_end": end,
                    "file_size_bytes": json_path.stat().st_size,
                    "result_code": clean_text(result.get("CODE")),
                    "result_message": clean_text(result.get("MESSAGE")),
                    "list_total_count": total_count,
                    "row_count": len(rows),
                    "page_complete": len(rows) == max(0, min(end, total_count) - start + 1),
                }
            )
            if not rows:
                continue

            raw_df = pd.DataFrame(rows)
            normalized = normalize_rows(raw_df, service_code, json_path, row_offset, sgg_map)
            service_rows[service_code] += len(normalized)
            row_offset += len(normalized)

            keys = list(
                zip(
                    normalized["license_category"].astype(str),
                    normalized["관리번호"].astype(str),
                    normalized["인허가기관코드"].astype(str),
                )
            )
            for key in keys:
                if key[1] == "":
                    continue
                if key in seen_keys:
                    duplicate_license_key_rows += 1
                else:
                    seen_keys.add(key)

            missing_mgtno_rows += int(normalized["관리번호"].eq("").sum())
            missing_status_rows += int((normalized["영업상태코드"].eq("") & normalized["영업상태명"].eq("")).sum())
            missing_sgg_rows += int(normalized["자치구_코드_명"].eq("").sum())
            invalid_coord_rows += int((~normalized["좌표유효여부"]).sum())
            outside_tm_bbox_rows += int((normalized["좌표유효여부"] & ~normalized["서울_TM_bbox_범위여부"]).sum())
            missing_address_rows += int((~normalized["주소보유여부"]).sum())
            permit_date_rows += int(normalized["인허가_년월"].ne("").sum())
            close_date_rows += int(normalized["폐업_년월"].ne("").sum())
            permit_dt = pd.to_datetime(normalized["인허가일자"], errors="coerce")
            close_dt = pd.to_datetime(normalized["폐업일자"], errors="coerce")
            date_contradiction_rows += int((permit_dt.notna() & close_dt.notna() & (close_dt < permit_dt)).sum())
            closed_without_close_date_rows += int((normalized["폐업여부"] & normalized["폐업일자"].eq("")).sum())

            issue_mask = (
                normalized["관리번호"].eq("")
                | normalized["영업상태코드"].eq("")
                | normalized["자치구_코드_명"].eq("")
                | (~normalized["좌표유효여부"])
                | (normalized["좌표유효여부"] & ~normalized["서울_TM_bbox_범위여부"])
                | (~normalized["주소보유여부"])
                | (permit_dt.notna() & close_dt.notna() & (close_dt < permit_dt))
                | (normalized["폐업여부"] & normalized["폐업일자"].eq(""))
            )
            if issue_mask.any() and sum(len(x) for x in issue_samples) < 200:
                sample_cols = [
                    "license_category",
                    "원천행번호",
                    "관리번호",
                    "사업장명",
                    "업태명",
                    "영업상태명",
                    "상세영업상태명",
                    "인허가일자",
                    "폐업일자",
                    "자치구_코드_명",
                    "소재지전체주소",
                    "도로명전체주소",
                    "X_EPSG5174",
                    "Y_EPSG5174",
                    "좌표유효여부",
                    "서울_TM_bbox_범위여부",
                ]
                remain = 200 - sum(len(x) for x in issue_samples)
                issue_samples.append(normalized.loc[issue_mask, sample_cols].head(remain))

            status_parts.append(aggregate_status(normalized))
            month_parts.append(aggregate_month_events(normalized))
            codebook_parts.append(aggregate_status_codebook(normalized))
            append_csv(normalized, RAW_OUT_PATH, first_write=first_write)
            first_write = False

    source_audit = pd.DataFrame(source_records).sort_values(["service_code", "request_start"]).reset_index(drop=True)
    write_csv(source_audit, SOURCE_AUDIT_PATH)

    status_keys = ["license_category", "자치구_코드", "자치구_코드_명", "업태명", "상태그룹"]
    status_summary = merge_aggregates(status_parts, status_keys)
    status_summary["source_id"] = "seoul_localdata_food_license"
    status_summary["provider"] = PROVIDER
    status_summary["snapshot_date"] = SNAPSHOT_DATE
    status_summary["usage_role"] = "자치구·업태별 인허가/폐업 상태 기반 경쟁·안정성 보조 프록시"
    status_summary["score_use_status"] = "조건부: 상권 공간매칭과 업종 매핑 뒤 점수 반영"
    write_csv(status_summary, SGG_UPTAE_STATUS_PATH)

    month_keys = ["license_category", "자치구_코드", "자치구_코드_명", "업태명", "년월"]
    monthly = merge_aggregates(month_parts, month_keys)
    monthly["source_id"] = "seoul_localdata_food_license"
    monthly["provider"] = PROVIDER
    monthly["snapshot_date"] = SNAPSHOT_DATE
    monthly["usage_role"] = "월별 인허가/폐업 이벤트 추이"
    monthly["score_use_status"] = "조건부: 기간 보정과 상권 공간매칭 뒤 성장·안정성 보조축에 반영"
    write_csv(monthly, MONTH_EVENT_PATH)

    codebook_keys = ["license_category", "영업상태코드", "영업상태명", "상세영업상태코드", "상세영업상태명", "상태그룹"]
    status_codebook = merge_aggregates(codebook_parts, codebook_keys)
    status_codebook["source_id"] = "seoul_localdata_food_license"
    status_codebook["provider"] = PROVIDER
    status_codebook["snapshot_date"] = SNAPSHOT_DATE
    status_codebook["notes_ko"] = "영업상태 코드는 폐업위험/안정성 직접값이 아니라 인허가 상태 프록시다."
    write_csv(status_codebook, STATUS_CODEBOOK_PATH)

    if issue_samples:
        write_csv(pd.concat(issue_samples, ignore_index=True), ISSUE_SAMPLE_PATH)
    else:
        write_csv(pd.DataFrame(columns=["이슈없음"]), ISSUE_SAMPLE_PATH)

    expected_rows = sum(service_total_counts.values())
    raw_output_rows = sum(1 for _ in RAW_OUT_PATH.open("rb")) - 1
    duplicate_cols = [
        "license_category",
        "관리번호",
        "인허가기관코드",
        "사업장명",
        "업태명",
        "영업상태명",
        "상세영업상태명",
        "인허가일자",
        "폐업일자",
        "자치구_코드_명",
        "소재지전체주소",
        "도로명전체주소",
        "source_file",
    ]
    duplicate_df = pd.read_csv(RAW_OUT_PATH, encoding="utf-8-sig", dtype=str, usecols=duplicate_cols)
    duplicate_mask = duplicate_df.duplicated(["license_category", "관리번호", "인허가기관코드"], keep=False)
    duplicate_audit = duplicate_df.loc[duplicate_mask].sort_values(["license_category", "관리번호", "인허가기관코드"])
    write_csv(duplicate_audit, DUPLICATE_AUDIT_PATH)
    doc_paths = []
    for cfg in SERVICE_CONFIGS.values():
        doc_paths.extend(cfg["doc_paths"])
    doc_paths = sorted(set(doc_paths))
    metrics: dict[str, Any] = {
        "service_rows": service_rows,
        "service_total_counts": service_total_counts,
        "expected_rows": expected_rows,
        "raw_output_rows": raw_output_rows,
        "source_audit_rows": len(source_audit),
        "status_summary_rows": len(status_summary),
        "monthly_rows": len(monthly),
        "status_codebook_rows": len(status_codebook),
        "duplicate_audit_rows": len(duplicate_audit),
        "doc_expected_count": len(doc_paths),
        "doc_exists_count": sum(1 for p in doc_paths if (ROOT / p).exists()),
        "missing_mgtno_rows": missing_mgtno_rows,
        "duplicate_license_key_rows": duplicate_license_key_rows,
        "status_summary_sum": int(status_summary["인허가건수"].sum()) if not status_summary.empty else 0,
        "monthly_open_sum": int(monthly["인허가건수"].sum()) if not monthly.empty else 0,
        "monthly_close_sum": int(monthly["폐업건수"].sum()) if not monthly.empty else 0,
        "permit_date_rows": permit_date_rows,
        "close_date_rows": close_date_rows,
        "missing_status_rows": missing_status_rows,
        "missing_sgg_rows": missing_sgg_rows,
        "invalid_coord_rows": invalid_coord_rows,
        "outside_tm_bbox_rows": outside_tm_bbox_rows,
        "date_contradiction_rows": date_contradiction_rows,
        "closed_without_close_date_rows": closed_without_close_date_rows,
        "missing_address_rows": missing_address_rows,
    }
    source_contract, domain, grain, consistency = build_validation_rows(metrics)
    write_csv(source_contract, SOURCE_CONTRACT_PATH)
    write_csv(domain, DOMAIN_VALIDATION_PATH)
    write_csv(grain, GRAIN_VALIDATION_PATH)
    write_csv(consistency, CONSISTENCY_VALIDATION_PATH)
    write_report(source_contract, domain, grain, consistency, metrics)

    print("서울 식품 인허가 silver 생성 완료")
    print(f"raw_rows={raw_output_rows:,}")
    print(f"general_rows={service_rows['LOCALDATA_072404']:,}")
    print(f"rest_cafe_rows={service_rows['LOCALDATA_072405']:,}")
    print(f"status_summary_rows={len(status_summary):,}")
    print(f"monthly_rows={len(monthly):,}")
    print(f"status_codebook_rows={len(status_codebook):,}")
    print(f"invalid_coord_rows={invalid_coord_rows:,}")
    print(f"closed_without_close_date_rows={closed_without_close_date_rows:,}")


if __name__ == "__main__":
    main()
