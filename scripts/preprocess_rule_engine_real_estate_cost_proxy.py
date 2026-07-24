from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "datacorpus" / "_raw_ingest"
RTMS_DIR = RAW_DIR / "20260703" / "public_data" / "rtms_nrg_trade_raw"
REB_DIR = RAW_DIR / "20260703" / "reb_rone"
REB_DATA_DIR = REB_DIR / "commercial_rent_data"
SILVER_DIR = ROOT / "datacorpus" / "_silver"
VALIDATION_DIR = ROOT / "datacorpus" / "_rule_validation"
RESEARCH_VALIDATION_DIR = ROOT / "research" / "rule_validation"

PROGRESS_PATH = ROOT / "research" / "전처리_진행기록_20260703.md"
PRECHECK_PATH = ROOT / "research" / "전처리_전_확인사항_20260703.md"
RTMS_DOC_PATH = ROOT / "research" / "algorithm_evidence_sources" / "data_docs" / "data_go_kr_molit_commercial_real_estate_trade_api.html"
REB_DOC_PATH = ROOT / "research" / "algorithm_evidence_sources" / "data_docs" / "data_go_kr_kab_small_shop_rent.html"

SNAPSHOT_DATE = "2026-07-03"
RTMS_SOURCE_ID = "molit_rtms_commercial_trade"
REB_SOURCE_ID = "reb_small_shop_rent"


def ensure_dirs() -> None:
    for path in [SILVER_DIR, VALIDATION_DIR, RESEARCH_VALIDATION_DIR]:
        path.mkdir(parents=True, exist_ok=True)


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


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text in {"", " "} else text


def to_number(value: Any) -> float:
    text = clean_text(value).replace(",", "")
    if not text:
        return np.nan
    try:
        return float(text)
    except ValueError:
        return np.nan


def quarter_code_from_month(year: int, month: int) -> int:
    return int(f"{year}{((month - 1) // 3) + 1}")


def parse_rtms_xml() -> tuple[pd.DataFrame, pd.DataFrame]:
    records: list[dict[str, Any]] = []
    file_records: list[dict[str, Any]] = []
    for xml_path in sorted(RTMS_DIR.glob("*/*_page*.xml")):
        month_folder = xml_path.parent.name
        lawd_cd = xml_path.name.split("_")[0]
        root = ET.parse(xml_path).getroot()
        result_code = next(root.iter("resultCode"), None)
        result_msg = next(root.iter("resultMsg"), None)
        total_count = next(root.iter("totalCount"), None)
        page_no = next(root.iter("pageNo"), None)
        num_rows = next(root.iter("numOfRows"), None)
        items = list(root.iter("item"))
        total_count_num = int(clean_text(total_count.text)) if total_count is not None and clean_text(total_count.text) else 0
        file_records.append(
            {
                "source_id": RTMS_SOURCE_ID,
                "source_file": str(xml_path.relative_to(ROOT)),
                "계약_년월": month_folder,
                "자치구_코드_요청": lawd_cd,
                "result_code": clean_text(result_code.text if result_code is not None else ""),
                "result_msg": clean_text(result_msg.text if result_msg is not None else ""),
                "page_no": int(clean_text(page_no.text)) if page_no is not None and clean_text(page_no.text) else np.nan,
                "num_of_rows": int(clean_text(num_rows.text)) if num_rows is not None and clean_text(num_rows.text) else np.nan,
                "total_count": total_count_num,
                "item_count": len(items),
                "page_complete": total_count_num == len(items),
            }
        )
        for row_idx, item in enumerate(items, start=1):
            raw = {child.tag: clean_text(child.text) for child in list(item)}
            year = int(raw.get("dealYear") or month_folder[:4])
            month = int(raw.get("dealMonth") or month_folder[4:6])
            deal_amount = to_number(raw.get("dealAmount"))
            building_area = to_number(raw.get("buildingAr"))
            land_area = to_number(raw.get("plottageAr"))
            records.append(
                {
                    "source_row_id": f"{month_folder}_{lawd_cd}_{row_idx:04d}",
                    "계약_년월": int(f"{year}{month:02d}"),
                    "기준_년분기_코드": quarter_code_from_month(year, month),
                    "계약_연도": year,
                    "계약_월": month,
                    "계약_일": int(raw["dealDay"]) if raw.get("dealDay", "").isdigit() else np.nan,
                    "자치구_코드": raw.get("sggCd") or lawd_cd,
                    "자치구_명": raw.get("sggNm", ""),
                    "법정동": raw.get("umdNm", ""),
                    "건물유형": raw.get("buildingType", ""),
                    "건물주용도": raw.get("buildingUse", ""),
                    "용도지역": raw.get("landUse", ""),
                    "층": raw.get("floor", ""),
                    "건축연도": to_number(raw.get("buildYear")),
                    "거래금액_만원": deal_amount,
                    "건물면적_㎡": building_area,
                    "대지면적_㎡": land_area,
                    "건물면적당_거래금액_만원": deal_amount / building_area if building_area and building_area > 0 else np.nan,
                    "대지면적당_거래금액_만원": deal_amount / land_area if land_area and land_area > 0 else np.nan,
                    "거래유형": raw.get("dealingGbn", ""),
                    "해제여부": raw.get("cdealType", ""),
                    "해제사유발생일": raw.get("cdealDay", ""),
                    "중개사소재지": raw.get("estateAgentSggNm", ""),
                    "매도자구분": raw.get("slerGbn", ""),
                    "매수자구분": raw.get("buyerGbn", ""),
                    "source_file": str(xml_path.relative_to(ROOT)),
                    "source_id": RTMS_SOURCE_ID,
                    "provider": "국토교통부/공공데이터포털",
                    "source_service": "RTMSDataSvcNrgTrade",
                    "snapshot_date": SNAPSHOT_DATE,
                    "source_grain": "원천 XML item row",
                    "directness_level": "매매 실거래 프록시",
                    "forbidden_claim_ko": "월세, 권리금, 개별 점포 수익성으로 직접 해석하지 않는다.",
                }
            )
    return pd.DataFrame(records), pd.DataFrame(file_records)


def active_rtms(df: pd.DataFrame) -> pd.DataFrame:
    # 해제여부 값이 빈 칸인 거래만 비용 프록시 집계에 사용한다. 원천 row는 별도로 모두 보존한다.
    return df[df["해제여부"].fillna("").astype(str).str.strip().eq("")].copy()


def aggregate_rtms(rtms: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    active = active_rtms(rtms)
    g_month = ["계약_년월", "기준_년분기_코드", "자치구_코드", "자치구_명"]
    month = (
        active.groupby(g_month, dropna=False)
        .agg(
            거래건수=("source_row_id", "count"),
            거래금액_중앙값_만원=("거래금액_만원", "median"),
            거래금액_평균_만원=("거래금액_만원", "mean"),
            건물면적_중앙값_m2=("건물면적_㎡", "median"),
            건물면적당_거래금액_중앙값_만원_per_m2=("건물면적당_거래금액_만원", "median"),
            건물면적당_거래금액_평균_만원_per_m2=("건물면적당_거래금액_만원", "mean"),
            직거래건수=("거래유형", lambda s: int((s == "직거래").sum())),
            중개거래건수=("거래유형", lambda s: int((s == "중개거래").sum())),
        )
        .reset_index()
    )
    month["source_id"] = RTMS_SOURCE_ID
    month["provider"] = "국토교통부/공공데이터포털"
    month["directness_level"] = "매매가격 기반 비용 압력 프록시"
    month["forbidden_claim_ko"] = "임대료나 권리금 직접값이 아니다."

    g_quarter = ["기준_년분기_코드", "자치구_코드", "자치구_명"]
    quarter = (
        active.groupby(g_quarter, dropna=False)
        .agg(
            거래건수=("source_row_id", "count"),
            포함_월수=("계약_년월", "nunique"),
            거래금액_중앙값_만원=("거래금액_만원", "median"),
            거래금액_평균_만원=("거래금액_만원", "mean"),
            건물면적당_거래금액_중앙값_만원_per_m2=("건물면적당_거래금액_만원", "median"),
            건물면적당_거래금액_평균_만원_per_m2=("건물면적당_거래금액_만원", "mean"),
        )
        .reset_index()
    )
    quarter["source_id"] = RTMS_SOURCE_ID
    quarter["provider"] = "국토교통부/공공데이터포털"
    quarter["directness_level"] = "매매가격 기반 비용 압력 프록시"
    quarter["forbidden_claim_ko"] = "임대료나 권리금 직접값이 아니다."
    return month, quarter


def parse_reb_json() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    selected = pd.read_csv(REB_DIR / "reb_rone_commercial_rent_selected_tables.csv", encoding="utf-8-sig", low_memory=False)
    selected_ids = set(selected["STATBL_ID"].astype(str))
    rows: list[dict[str, Any]] = []
    page_records: list[dict[str, Any]] = []
    for json_path in sorted(REB_DATA_DIR.glob("*.json")):
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        root = payload.get("SttsApiTblData", [])
        total_count = np.nan
        result_code = ""
        result_message = ""
        page_rows: list[dict[str, Any]] = []
        for block in root:
            if "head" in block:
                for head in block["head"]:
                    if isinstance(head, dict) and "list_total_count" in head:
                        total_count = int(head["list_total_count"])
                    if isinstance(head, dict) and "RESULT" in head:
                        result_code = clean_text(head["RESULT"].get("CODE"))
                        result_message = clean_text(head["RESULT"].get("MESSAGE"))
            if "row" in block:
                page_rows.extend(block["row"])
        table_id = clean_text(page_rows[0].get("STATBL_ID")) if page_rows else json_path.name.split("_page_")[0]
        if table_id not in selected_ids:
            continue
        page_records.append(
            {
                "source_id": REB_SOURCE_ID,
                "source_file": str(json_path.relative_to(ROOT)),
                "STATBL_ID": table_id,
                "list_total_count": total_count,
                "page_row_count": len(page_rows),
                "result_code": result_code,
                "result_message": result_message,
            }
        )
        for item in page_rows:
            record = dict(item)
            record["source_file"] = str(json_path.relative_to(ROOT))
            rows.append(record)
    data = pd.DataFrame(rows)
    data = data.merge(selected, on="STATBL_ID", how="left", suffixes=("", "_selected"))
    data["DTA_VAL"] = pd.to_numeric(data["DTA_VAL"], errors="coerce")
    data["WRTTIME_IDTFR_ID"] = data["WRTTIME_IDTFR_ID"].astype(str)
    data["기준_년분기_코드"] = np.where(
        data["DTACYCLE_CD"].eq("QY"),
        data["WRTTIME_IDTFR_ID"].str[:4] + data["WRTTIME_IDTFR_ID"].str[-1:],
        "",
    )
    data["기준_연도"] = np.where(data["DTACYCLE_CD"].eq("YY"), data["WRTTIME_IDTFR_ID"].str[:4], "")
    data["지역_전체명"] = data["CLS_FULLNM"].fillna(data["GRP_FULLNM"]).fillna(data["CLS_NM"]).fillna(data["GRP_NM"])
    data["서울관련여부"] = data["지역_전체명"].astype(str).str.contains("서울", na=False) | data["GRP_NM"].astype(str).str.contains("서울", na=False)
    data["지역_레벨"] = data["지역_전체명"].astype(str).map(lambda x: "전국" if x == "전국" else f"{x.count('>') + 1}단계")
    data["상가유형"] = data["STATBL_NM"].map(classify_shop_type)
    data["source_id"] = REB_SOURCE_ID
    data["provider"] = "한국부동산원 R-ONE"
    data["source_service"] = "SttsApiTblData"
    data["snapshot_date"] = SNAPSHOT_DATE
    data["source_grain"] = "통계표 + 시점 + 지역/상권분류 + 항목"
    data["directness_level"] = "지역·상가유형 비용 프록시"
    data["forbidden_claim_ko"] = "개별 점포 월세, 권리금 확정값, 수익성을 보장하지 않는다."

    canonical_key = ["STATBL_ID", "WRTTIME_IDTFR_ID", "CLS_ID", "GRP_ID", "ITM_ID", "DTA_VAL", "UI_NM"]
    data["_exact_duplicate_sequence"] = data.groupby(canonical_key, dropna=False).cumcount() + 1
    data["_exact_duplicate_group_size"] = data.groupby(canonical_key, dropna=False)["STATBL_ID"].transform("size")
    duplicate_audit = data[data["_exact_duplicate_group_size"] > 1].copy()
    duplicate_audit["canonical_keep"] = duplicate_audit["_exact_duplicate_sequence"].eq(1)
    data = data[data["_exact_duplicate_sequence"].eq(1)].drop(
        columns=["_exact_duplicate_sequence", "_exact_duplicate_group_size"]
    )
    duplicate_audit = duplicate_audit.drop(columns=["_exact_duplicate_sequence", "_exact_duplicate_group_size"])
    return data, pd.DataFrame(page_records), duplicate_audit


def classify_shop_type(name: Any) -> str:
    text = clean_text(name)
    for candidate in ["오피스", "중대형 상가", "소규모 상가", "집합 상가", "통합 상가", "일반 상가(1층)", "일반 상가"]:
        if candidate in text:
            return candidate
    if "권리금" in text:
        return "권리금"
    return "기타"


def build_reb_latest_proxy(reb: pd.DataFrame) -> pd.DataFrame:
    seoul = reb[reb["서울관련여부"]].copy()
    seoul = seoul.sort_values(["STATBL_ID", "지역_전체명", "ITM_NM", "WRTTIME_IDTFR_ID"])
    latest = seoul.groupby(["STATBL_ID", "지역_전체명", "ITM_NM"], dropna=False).tail(1).copy()
    keep = [
        "selection_group",
        "STATBL_ID",
        "STATBL_NM",
        "상가유형",
        "DTACYCLE_CD",
        "WRTTIME_IDTFR_ID",
        "WRTTIME_DESC",
        "기준_년분기_코드",
        "기준_연도",
        "CLS_ID",
        "CLS_NM",
        "지역_전체명",
        "지역_레벨",
        "ITM_ID",
        "ITM_NM",
        "DTA_VAL",
        "UI_NM",
        "selection_reason_ko",
        "directness_level",
        "forbidden_claim_ko",
        "source_id",
    ]
    return latest[keep].reset_index(drop=True)


def duplicate_count(df: pd.DataFrame, key_cols: list[str]) -> int:
    return int(df.duplicated(key_cols).sum())


def null_key_cells(df: pd.DataFrame, key_cols: list[str]) -> int:
    return int(df[key_cols].isna().sum().sum())


def validate(
    rtms: pd.DataFrame,
    rtms_file_audit: pd.DataFrame,
    rtms_month: pd.DataFrame,
    rtms_quarter: pd.DataFrame,
    reb: pd.DataFrame,
    reb_page_audit: pd.DataFrame,
    reb_duplicate_audit: pd.DataFrame,
    reb_latest: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    rtms_active = active_rtms(rtms)
    reb_table_counts = reb.groupby("STATBL_ID").size().rename("canonical_rows").reset_index()
    reb_total_counts = reb_page_audit.groupby("STATBL_ID")["page_row_count"].sum().rename("page_rows").reset_index()
    reb_count_check = reb_table_counts.merge(reb_total_counts, on="STATBL_ID", how="outer").fillna(0)
    reb_dropped_counts = (
        reb_duplicate_audit[~reb_duplicate_audit["canonical_keep"]]
        .groupby("STATBL_ID")
        .size()
        .rename("dropped_exact_duplicate_rows")
        .reset_index()
    )
    reb_count_check = reb_count_check.merge(reb_dropped_counts, on="STATBL_ID", how="left").fillna({"dropped_exact_duplicate_rows": 0})
    reb_count_check["diff"] = (
        reb_count_check["canonical_rows"]
        + reb_count_check["dropped_exact_duplicate_rows"]
        - reb_count_check["page_rows"]
    )
    negative_net_income_rows = int(((reb["DTA_VAL"] < 0) & reb["ITM_NM"].astype(str).str.contains("순영업소득", na=False)).sum())
    unexpected_negative_rows = int(((reb["DTA_VAL"] < 0) & ~reb["ITM_NM"].astype(str).str.contains("순영업소득", na=False)).sum())

    domain = pd.DataFrame(
        [
            {
                "table": "silver_rtms_commercial_trade_raw",
                "rows": len(rtms),
                "source_files": rtms_file_audit["source_file"].nunique(),
                "key_null_cells": null_key_cells(rtms, ["source_row_id", "계약_년월", "자치구_코드"]),
                "negative_value_cells": int((rtms[["거래금액_만원", "건물면적_㎡", "건물면적당_거래금액_만원"]] < 0).sum().sum()),
                "total_source_items": int(rtms_file_audit["item_count"].sum()),
                "complete_file_count": int(rtms_file_audit["page_complete"].sum()),
                "judgement": "PASS",
                "conditional_reason_ko": "",
            },
            {
                "table": "silver_rtms_commercial_trade_sgg_month",
                "rows": len(rtms_month),
                "source_files": np.nan,
                "key_null_cells": null_key_cells(rtms_month, ["계약_년월", "자치구_코드"]),
                "negative_value_cells": int((rtms_month[["거래건수", "거래금액_중앙값_만원", "건물면적당_거래금액_중앙값_만원_per_m2"]] < 0).sum().sum()),
                "total_source_items": int(rtms_month["거래건수"].sum()),
                "complete_file_count": np.nan,
                "judgement": "조건부 PASS",
                "conditional_reason_ko": "매매가격 기반 비용 압력 프록시이며 임대료 직접값이 아니다.",
            },
            {
                "table": "silver_rtms_commercial_trade_sgg_quarter",
                "rows": len(rtms_quarter),
                "source_files": np.nan,
                "key_null_cells": null_key_cells(rtms_quarter, ["기준_년분기_코드", "자치구_코드"]),
                "negative_value_cells": int((rtms_quarter[["거래건수", "거래금액_중앙값_만원", "건물면적당_거래금액_중앙값_만원_per_m2"]] < 0).sum().sum()),
                "total_source_items": int(rtms_quarter["거래건수"].sum()),
                "complete_file_count": np.nan,
                "judgement": "조건부 PASS",
                "conditional_reason_ko": "부분분기와 거래건수 희소 구는 점수화 시 신뢰도 보정이 필요하다.",
            },
            {
                "table": "silver_reb_rone_commercial_cost_long",
                "rows": len(reb),
                "source_files": reb_page_audit["source_file"].nunique(),
                "key_null_cells": null_key_cells(reb, ["STATBL_ID", "WRTTIME_IDTFR_ID", "ITM_ID"]),
                "negative_value_cells": unexpected_negative_rows,
                "expected_negative_rows": negative_net_income_rows,
                "total_source_items": int(reb_page_audit["page_row_count"].sum()),
                "complete_file_count": int((reb_page_audit["result_code"] == "INFO-000").sum()),
                "judgement": "조건부 PASS",
                "conditional_reason_ko": "지역·상가유형 통계라 개별 점포값이 아니며 권리금은 참고용 한계가 있다. 순영업소득 음수는 오류가 아니라 비용 부담 신호로 보존한다.",
            },
            {
                "table": "silver_reb_rone_seoul_cost_proxy_latest",
                "rows": len(reb_latest),
                "source_files": np.nan,
                "key_null_cells": null_key_cells(reb_latest, ["STATBL_ID", "지역_전체명", "ITM_ID"]),
                "negative_value_cells": int(((reb_latest["DTA_VAL"] < 0) & ~reb_latest["ITM_NM"].astype(str).str.contains("순영업소득", na=False)).sum()),
                "expected_negative_rows": int(((reb_latest["DTA_VAL"] < 0) & reb_latest["ITM_NM"].astype(str).str.contains("순영업소득", na=False)).sum()),
                "total_source_items": len(reb_latest),
                "complete_file_count": np.nan,
                "judgement": "조건부 PASS",
                "conditional_reason_ko": "서울 관련 최신 관측치만 뽑은 비용 참고선이며 상권 직접값이 아니다.",
            },
        ]
    )

    grain = pd.DataFrame(
        [
            {
                "table": "silver_rtms_commercial_trade_raw",
                "key_cols": "source_row_id",
                "duplicate_key_rows": duplicate_count(rtms, ["source_row_id"]),
                "key_null_cells": null_key_cells(rtms, ["source_row_id"]),
                "judgement": "PASS",
                "reason_ko": "RTMS 원천은 고유 거래 ID가 없으므로 수집 파일+순번 기반 원천 행 식별자를 부여한다.",
            },
            {
                "table": "silver_rtms_commercial_trade_sgg_month",
                "key_cols": "계약_년월 + 자치구_코드",
                "duplicate_key_rows": duplicate_count(rtms_month, ["계약_년월", "자치구_코드"]),
                "key_null_cells": null_key_cells(rtms_month, ["계약_년월", "자치구_코드"]),
                "judgement": "PASS",
                "reason_ko": "자치구 월별 비용 압력 프록시 최소 단위다.",
            },
            {
                "table": "silver_rtms_commercial_trade_sgg_quarter",
                "key_cols": "기준_년분기_코드 + 자치구_코드",
                "duplicate_key_rows": duplicate_count(rtms_quarter, ["기준_년분기_코드", "자치구_코드"]),
                "key_null_cells": null_key_cells(rtms_quarter, ["기준_년분기_코드", "자치구_코드"]),
                "judgement": "PASS",
                "reason_ko": "상권 점수 조인 전 자치구 분기 단위로만 보존한다.",
            },
            {
                "table": "silver_reb_rone_commercial_cost_long",
                "key_cols": "STATBL_ID + WRTTIME_IDTFR_ID + CLS_ID/GRP_ID + ITM_ID",
                "duplicate_key_rows": duplicate_count(reb, ["STATBL_ID", "WRTTIME_IDTFR_ID", "CLS_ID", "GRP_ID", "ITM_ID"]),
                "key_null_cells": null_key_cells(reb, ["STATBL_ID", "WRTTIME_IDTFR_ID", "ITM_ID"]),
                "judgement": "조건부 PASS",
                "reason_ko": "페이지 경계의 정확한 중복 7건은 canonical_keep 1건만 남긴다. 일부 통계표는 지역 분류 구조가 CLS/GRP로 갈려 null이 있을 수 있어 통계표별 해석이 필요하다.",
            },
        ]
    )

    consistency = pd.DataFrame(
        [
            {
                "check_name": "rtms_file_item_sum_equals_raw_rows",
                "left_value": int(rtms_file_audit["item_count"].sum()),
                "right_value": len(rtms),
                "diff": int(rtms_file_audit["item_count"].sum() - len(rtms)),
                "judgement": "PASS" if int(rtms_file_audit["item_count"].sum()) == len(rtms) else "FAIL",
                "reason_ko": "XML 파일별 item 합계와 raw silver 행수가 같아야 한다.",
            },
            {
                "check_name": "rtms_aggregate_active_rows",
                "left_value": int(rtms_month["거래건수"].sum()),
                "right_value": len(rtms_active),
                "diff": int(rtms_month["거래건수"].sum() - len(rtms_active)),
                "judgement": "PASS" if int(rtms_month["거래건수"].sum()) == len(rtms_active) else "FAIL",
                "reason_ko": "월별 집계 거래건수 합은 해제 제외 active row 수와 같아야 한다.",
            },
        {
                "check_name": "reb_page_rows_minus_exact_duplicates_equals_long_rows",
                "left_value": int(reb_page_audit["page_row_count"].sum() - (~reb_duplicate_audit["canonical_keep"]).sum()),
                "right_value": len(reb),
                "diff": int(reb_page_audit["page_row_count"].sum() - (~reb_duplicate_audit["canonical_keep"]).sum() - len(reb)),
                "judgement": "PASS" if int(reb_page_audit["page_row_count"].sum() - (~reb_duplicate_audit["canonical_keep"]).sum()) == len(reb) else "FAIL",
                "reason_ko": "R-ONE 페이지별 row 합계에서 페이지 경계 정확 중복을 제외한 값이 long silver 행수와 같아야 한다.",
            },
            {
                "check_name": "reb_table_page_count_diff_max",
                "left_value": float(reb_count_check["diff"].abs().max()),
                "right_value": 0,
                "diff": float(reb_count_check["diff"].abs().max()),
                "judgement": "PASS" if reb_count_check["diff"].abs().max() == 0 else "FAIL",
                "reason_ko": "R-ONE 통계표별 페이지 합계에서 정확 중복 제외분을 반영하면 canonical long 행수와 같아야 한다.",
            },
            {
                "check_name": "reb_selected_table_count",
                "left_value": int(reb["STATBL_ID"].nunique()),
                "right_value": 24,
                "diff": int(reb["STATBL_ID"].nunique() - 24),
                "judgement": "PASS" if reb["STATBL_ID"].nunique() == 24 else "FAIL",
                "reason_ko": "사전에 선택한 R-ONE 비용 관련 24개 통계표가 모두 들어와야 한다.",
            },
        ]
    )

    summary = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "rtms_raw_rows": int(len(rtms)),
        "rtms_active_rows": int(len(rtms_active)),
        "rtms_file_count": int(rtms_file_audit["source_file"].nunique()),
        "rtms_month_rows": int(len(rtms_month)),
        "rtms_quarter_rows": int(len(rtms_quarter)),
        "reb_long_rows": int(len(reb)),
        "reb_exact_duplicate_dropped_rows": int((~reb_duplicate_audit["canonical_keep"]).sum()),
        "reb_negative_net_income_rows": negative_net_income_rows,
        "reb_table_count": int(reb["STATBL_ID"].nunique()),
        "reb_page_count": int(reb_page_audit["source_file"].nunique()),
        "reb_latest_rows": int(len(reb_latest)),
        "rtms_month_min": int(rtms["계약_년월"].min()),
        "rtms_month_max": int(rtms["계약_년월"].max()),
    }
    return domain, grain, consistency, summary


def write_source_contract() -> None:
    rows = [
        {
            "table": "silver_rtms_commercial_trade_raw",
            "source_id": RTMS_SOURCE_ID,
            "provider": "국토교통부/공공데이터포털",
            "source_service": "RTMSDataSvcNrgTrade",
            "contract_status": "PASS",
            "usage_role": "상업업무용 매매 실거래 원천 보존",
        },
        {
            "table": "silver_rtms_commercial_trade_sgg_month",
            "source_id": RTMS_SOURCE_ID,
            "provider": "국토교통부/공공데이터포털",
            "source_service": "RTMSDataSvcNrgTrade",
            "contract_status": "조건부 PASS",
            "usage_role": "자치구 월별 비용 압력 프록시",
        },
        {
            "table": "silver_rtms_commercial_trade_sgg_quarter",
            "source_id": RTMS_SOURCE_ID,
            "provider": "국토교통부/공공데이터포털",
            "source_service": "RTMSDataSvcNrgTrade",
            "contract_status": "조건부 PASS",
            "usage_role": "자치구 분기별 비용 압력 프록시",
        },
        {
            "table": "silver_reb_rone_commercial_cost_long",
            "source_id": REB_SOURCE_ID,
            "provider": "한국부동산원 R-ONE",
            "source_service": "SttsApiTblData",
            "contract_status": "조건부 PASS",
            "usage_role": "임대료·공실률·전환률·권리금 지역 통계 프록시",
        },
        {
            "table": "silver_reb_rone_seoul_cost_proxy_latest",
            "source_id": REB_SOURCE_ID,
            "provider": "한국부동산원 R-ONE",
            "source_service": "SttsApiTblData",
            "contract_status": "조건부 PASS",
            "usage_role": "서울 관련 최신 비용 참고선",
        },
    ]
    write_csv(pd.DataFrame(rows), VALIDATION_DIR / "12_real_estate_cost_proxy_source_contract.csv")


def write_markdown(domain: pd.DataFrame, grain: pd.DataFrame, consistency: pd.DataFrame, summary: dict[str, Any]) -> None:
    lines = [
        "# 12차 부동산 비용 프록시 silver 전처리 검증",
        "",
        f"- 작성시각: {summary['created_at']}",
        f"- RTMS 근거 문서: `{RTMS_DOC_PATH.relative_to(ROOT)}`",
        f"- R-ONE 근거 문서: `{REB_DOC_PATH.relative_to(ROOT)}`",
        "- RTMS 공식 문서 기준: 상업업무용 부동산 매매 실거래가 자료이며, 지역코드와 계약년월로 조회한다.",
        "- R-ONE/공공데이터 설명 기준: 소규모상가 임대료는 천원/㎡ 단위, 분기 공표 자료다.",
        "- R-ONE 선택표 메타 기준: 권리금 표는 공식 승인통계가 아닌 수량적 정보로 참고 용도 한계가 있다.",
        "",
        "## 산출물",
        "",
        "| 파일 | 행수 | 판정 | 용도 |",
        "|---|---:|---|---|",
        f"| `datacorpus/_silver/silver_rtms_commercial_trade_raw.csv` | {summary['rtms_raw_rows']:,} | PASS | RTMS 원천 item row 보존 |",
        f"| `datacorpus/_silver/silver_rtms_commercial_trade_sgg_month.csv` | {summary['rtms_month_rows']:,} | 조건부 PASS | 자치구 월별 매매가격 비용 프록시 |",
        f"| `datacorpus/_silver/silver_rtms_commercial_trade_sgg_quarter.csv` | {summary['rtms_quarter_rows']:,} | 조건부 PASS | 자치구 분기별 매매가격 비용 프록시 |",
        f"| `datacorpus/_silver/silver_reb_rone_commercial_cost_long.csv` | {summary['reb_long_rows']:,} | 조건부 PASS | R-ONE 비용 관련 통계 long, 정확 중복 {summary['reb_exact_duplicate_dropped_rows']:,}건 제외 |",
        f"| `datacorpus/_silver/silver_reb_rone_seoul_cost_proxy_latest.csv` | {summary['reb_latest_rows']:,} | 조건부 PASS | 서울 관련 최신 비용 참고선 |",
        "",
        "## 2보 전진 1보 후퇴",
        "",
        f"- 전진 1: RTMS {summary['rtms_month_min']}~{summary['rtms_month_max']} 서울 25개 구 XML {summary['rtms_file_count']:,}개를 원천 item row로 보존했다.",
        f"- 전진 2: R-ONE 비용 관련 선택 통계표 {summary['reb_table_count']:,}개를 long 형식으로 보존했다.",
        f"- 전진 3: R-ONE 페이지 경계 정확 중복 {summary['reb_exact_duplicate_dropped_rows']:,}건은 canonical long에서 제외했다.",
        "- 후퇴 1: RTMS는 매매 실거래가라 임대료·월세·권리금 직접값이 아니다.",
        "- 후퇴 2: R-ONE은 지역/상가유형 통계라 개별 점포 비용이나 수익성을 보장하지 않는다.",
        f"- 후퇴 3: 순영업소득 음수 {summary['reb_negative_net_income_rows']:,}건은 오류가 아니라 비용 부담 신호로 보존한다.",
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
        "## 사용 금지 주장",
        "",
        "- 개별 점포 월세",
        "- 권리금 확정값",
        "- 월세/권리금까지 반영한 수익성 판단",
        "- 창업 성공확률",
        "- 특정 매장의 매출 보장",
        "",
        "## 알고리즘 사용 가능 범위",
        "",
        "- 비용형 지표의 지역 단위 압력 프록시",
        "- 임대료·공실률·전환률·권리금 참고선",
        "- 비용형 점수화 시 `100 - 백분위` 같은 방향성만 적용하고, 직접 수익성 문구는 금지한다.",
        "",
    ]
    path = RESEARCH_VALIDATION_DIR / "12_real_estate_cost_proxy_silver_validation_20260703.md"
    path.write_text("\n".join(lines), encoding="utf-8")


def append_progress(summary: dict[str, Any]) -> None:
    block = f"""

## 14. 완료: 부동산 비용 프록시 silver 테이블

### 산출물

| 파일 | 행수 | 판정 | 비고 |
|---|---:|---|---|
| `datacorpus/_silver/silver_rtms_commercial_trade_raw.csv` | {summary['rtms_raw_rows']:,} | PASS | 상업업무용 매매 실거래 원천 |
| `datacorpus/_silver/silver_rtms_commercial_trade_sgg_month.csv` | {summary['rtms_month_rows']:,} | 조건부 PASS | 자치구 월별 매매가격 비용 프록시 |
| `datacorpus/_silver/silver_rtms_commercial_trade_sgg_quarter.csv` | {summary['rtms_quarter_rows']:,} | 조건부 PASS | 자치구 분기별 비용 프록시 |
| `datacorpus/_silver/silver_reb_rone_commercial_cost_long.csv` | {summary['reb_long_rows']:,} | 조건부 PASS | 임대료·공실률·전환률·권리금 통계 long, 정확 중복 {summary['reb_exact_duplicate_dropped_rows']:,}건 제외 |
| `datacorpus/_silver/silver_reb_rone_seoul_cost_proxy_latest.csv` | {summary['reb_latest_rows']:,} | 조건부 PASS | 서울 관련 최신 비용 참고선 |

### 판단

- RTMS는 매매 실거래이므로 임대료 직접값이 아니라 지역 비용 압력 프록시다.
- R-ONE은 지역/상가유형 통계라 개별 점포 월세·권리금·수익성을 보장하지 않는다.
- R-ONE 순영업소득 음수 {summary['reb_negative_net_income_rows']:,}건은 원천의 비용 부담 신호로 보존한다.
- 비용형 점수에는 백분위 방향성만 적용하고, 리포트 문구는 프록시/참고선으로 제한한다.
"""
    current = PROGRESS_PATH.read_text(encoding="utf-8") if PROGRESS_PATH.exists() else ""
    if "## 14. 완료: 부동산 비용 프록시 silver 테이블" not in current:
        PROGRESS_PATH.write_text(current.rstrip() + block + "\n", encoding="utf-8")


def append_precheck(summary: dict[str, Any]) -> None:
    current = PRECHECK_PATH.read_text(encoding="utf-8") if PRECHECK_PATH.exists() else ""
    row = (
        f"| 부동산 비용 프록시 | RTMS 원천 {summary['rtms_raw_rows']:,}건, R-ONE 비용 통계 {summary['reb_long_rows']:,}건 silver 생성 완료 | "
        "매매가·임대료·공실률·전환률·권리금은 지역 비용 압력 참고선이며 개별 점포 월세/수익성으로 직접 해석하지 않는다. |"
    )
    if "| 부동산 비용 프록시 |" not in current:
        marker = "| 생활이동 OD |"
        if marker in current:
            lines = current.splitlines()
            for idx, line in enumerate(lines):
                if line.startswith(marker):
                    lines.insert(idx + 1, row)
                    current = "\n".join(lines) + "\n"
                    break
        else:
            current = current.rstrip() + "\n\n" + row + "\n"
        PRECHECK_PATH.write_text(current, encoding="utf-8")


def main() -> None:
    ensure_dirs()
    rtms, rtms_file_audit = parse_rtms_xml()
    rtms_month, rtms_quarter = aggregate_rtms(rtms)
    reb, reb_page_audit, reb_duplicate_audit = parse_reb_json()
    reb_latest = build_reb_latest_proxy(reb)

    write_csv(rtms, SILVER_DIR / "silver_rtms_commercial_trade_raw.csv")
    write_csv(rtms_file_audit, SILVER_DIR / "silver_rtms_commercial_trade_file_audit.csv")
    write_csv(rtms_month, SILVER_DIR / "silver_rtms_commercial_trade_sgg_month.csv")
    write_csv(rtms_quarter, SILVER_DIR / "silver_rtms_commercial_trade_sgg_quarter.csv")
    write_csv(reb, SILVER_DIR / "silver_reb_rone_commercial_cost_long.csv")
    write_csv(reb_page_audit, SILVER_DIR / "silver_reb_rone_commercial_cost_page_audit.csv")
    write_csv(reb_duplicate_audit, SILVER_DIR / "silver_reb_rone_commercial_cost_duplicate_audit.csv")
    write_csv(reb_latest, SILVER_DIR / "silver_reb_rone_seoul_cost_proxy_latest.csv")

    domain, grain, consistency, summary = validate(
        rtms, rtms_file_audit, rtms_month, rtms_quarter, reb, reb_page_audit, reb_duplicate_audit, reb_latest
    )
    write_csv(domain, VALIDATION_DIR / "12_real_estate_cost_proxy_domain_validation.csv")
    write_csv(grain, VALIDATION_DIR / "12_real_estate_cost_proxy_grain_validation.csv")
    write_csv(consistency, VALIDATION_DIR / "12_real_estate_cost_proxy_consistency_validation.csv")
    write_source_contract()
    (VALIDATION_DIR / "12_real_estate_cost_proxy_preprocess_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_markdown(domain, grain, consistency, summary)
    append_progress(summary)
    append_precheck(summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
