from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "datacorpus" / "_raw_ingest"
SILVER_DIR = ROOT / "datacorpus" / "_silver"
VALIDATION_DIR = ROOT / "datacorpus" / "_rule_validation"
RESEARCH_VALIDATION_DIR = ROOT / "research" / "rule_validation"

SERVICE = "CardBusTimeNew"
RAW_RELATIVE_PARTS = ("seoul_open_data", "transport", "bus_stop_passengers_hourly")
BUS_STOP_MASTER_PATH = SILVER_DIR / "silver_bus_stop_location_master.csv"
PROGRESS_PATH = ROOT / "research" / "전처리_진행기록_20260703.md"
PRECHECK_PATH = ROOT / "research" / "전처리_전_확인사항_20260703.md"

SNAPSHOT_DATE = "2026-07-03"
PROVIDER = "서울열린데이터광장"
SOURCE_ID = "seoul_bus_stop_passengers_hourly"
BUS_STOP_SOURCE_ID = "seoul_bus_stop_location_file"
KEY_COLS_SUMMARY = [
    "기준_월",
    "노선_번호",
    "노선_명",
    "정류소_ID",
    "정류소_ARS_ID",
    "원천_정류장명_순번포함",
    "교통수단유형_코드",
]
KEY_COLS_LONG = KEY_COLS_SUMMARY + ["시간대"]


def ensure_dirs() -> None:
    for path in [SILVER_DIR, VALIDATION_DIR, RESEARCH_VALIDATION_DIR]:
        path.mkdir(parents=True, exist_ok=True)


def page_sort_key(path: Path) -> tuple[int, int]:
    match = re.search(r"_(\d+)_(\d+)(?:_\d+)?\.json$", path.name)
    if not match:
        return (10**12, 10**12)
    return (int(match.group(1)), int(match.group(2)))


def discover_month_paths() -> list[Path]:
    month_by_name: dict[str, Path] = {}
    for date_dir in sorted(RAW_DIR.glob("20??????")):
        base_path = date_dir.joinpath(*RAW_RELATIVE_PARTS)
        if not base_path.exists():
            continue
        for path in base_path.iterdir():
            if path.is_dir() and re.fullmatch(r"\d{6}", path.name):
                # 같은 월이 여러 수집일에 있으면 최신 수집일 폴더를 채택한다.
                month_by_name[path.name] = path
    month_paths = [month_by_name[name] for name in sorted(month_by_name)]
    if not month_paths:
        raise FileNotFoundError("버스 승하차량 YYYYMM 월 폴더를 찾지 못했습니다.")
    return month_paths


def read_openapi_pages() -> tuple[pd.DataFrame, int, int]:
    rows: list[dict[str, Any]] = []
    total_page_count = 0
    totals_by_month: dict[str, set[int]] = {}

    for month_path in discover_month_paths():
        page_paths = sorted(month_path.glob(f"{SERVICE}_*.json"), key=page_sort_key)
        if not page_paths:
            raise FileNotFoundError(f"{month_path} 폴더에서 {SERVICE} 원응답을 찾지 못했습니다.")
        total_page_count += len(page_paths)
        month_totals = totals_by_month.setdefault(month_path.name, set())

        for page_path in page_paths:
            payload = json.loads(page_path.read_text(encoding="utf-8"))
            root = payload.get(SERVICE)
            if not isinstance(root, dict):
                raise ValueError(f"{page_path} 파일에 {SERVICE} 루트가 없습니다.")
            if "list_total_count" in root:
                month_totals.add(int(root["list_total_count"]))
            for row in root.get("row", []):
                item = dict(row)
                item["_raw_path"] = str(page_path.relative_to(ROOT))
                item["_raw_month_dir"] = month_path.name
                rows.append(item)

    invalid_month_totals = {
        month: sorted(values)
        for month, values in totals_by_month.items()
        if len(values) != 1
    }
    if invalid_month_totals:
        raise ValueError(f"{SERVICE} 월별 list_total_count가 하나로 고정되지 않습니다: {invalid_month_totals}")
    if not rows:
        raise ValueError(f"{SERVICE} 원천 row를 읽지 못했습니다.")
    api_total = sum(next(iter(values)) for values in totals_by_month.values())
    return pd.DataFrame(rows), total_page_count, api_total


def detect_hour_columns(df: pd.DataFrame) -> list[tuple[int, str, str]]:
    found: dict[int, dict[str, str]] = {}
    for col in df.columns:
        match = re.match(r"HR_(\d+)_GET_(ON|OFF)_(?:T?NOPE)$", col)
        if not match:
            continue
        hour = int(match.group(1))
        kind = match.group(2)
        found.setdefault(hour, {})[kind] = col
    missing = [hour for hour in range(24) if set(found.get(hour, {}).keys()) != {"ON", "OFF"}]
    if missing:
        raise ValueError(f"버스 승하차량 시간대 승차/하차 컬럼이 부족합니다: {missing}")
    return [(hour, found[hour]["ON"], found[hour]["OFF"]) for hour in range(24)]


def hour_group(hour: int) -> str:
    if 0 <= hour <= 5:
        return "심야/새벽"
    if 6 <= hour <= 9:
        return "출근/오전"
    if 10 <= hour <= 16:
        return "낮"
    if 17 <= hour <= 20:
        return "퇴근/저녁"
    return "야간"


def parse_stop_label(value: str) -> tuple[str, str]:
    text = str(value).strip()
    match = re.match(r"^(?P<name>.*)\((?P<seq>[^()]*)\)$", text)
    if not match:
        return text, ""
    return match.group("name").strip(), match.group("seq").strip()


def normalize_ars(value: str) -> str:
    text = str(value).strip()
    if text.isdigit():
        return text.zfill(5)
    return text


def build_hour_codebook(hour_cols: list[tuple[int, str, str]]) -> pd.DataFrame:
    rows = []
    for hour, on_col, off_col in hour_cols:
        rows.append(
            {
                "시간대": hour,
                "시간대_라벨": f"{hour:02d}시",
                "시간대_그룹": hour_group(hour),
                "승차_원천컬럼": on_col,
                "하차_원천컬럼": off_col,
                "usage_role": "버스 정류장별 시간대 접근성 강도 프록시",
                "score_use_warning_ko": "시간대 그룹은 분석 편의용이며 실제 체류시간이나 방문목적을 의미하지 않는다.",
                "source_id": SOURCE_ID,
                "provider": PROVIDER,
                "snapshot_date": SNAPSHOT_DATE,
            }
        )
    return pd.DataFrame(rows)


def load_bus_stop_master() -> pd.DataFrame:
    if not BUS_STOP_MASTER_PATH.exists():
        raise FileNotFoundError(f"{BUS_STOP_MASTER_PATH}가 없습니다. 버스정류소 위치 전처리를 먼저 실행해야 합니다.")
    master = pd.read_csv(BUS_STOP_MASTER_PATH, encoding="utf-8-sig", dtype=str).fillna("")
    stop_id_col, stop_name_col, lon_col, lat_col, ars_col = master.columns[0], master.columns[1], master.columns[2], master.columns[3], master.columns[4]
    return master[[stop_id_col, ars_col, stop_name_col, lon_col, lat_col]].rename(
        columns={
            stop_id_col: "정류소_ID",
            ars_col: "정류소_ARS_ID",
            stop_name_col: "정류소_마스터명",
            lon_col: "정류소_경도",
            lat_col: "정류소_위도",
        }
    )


def build_summary_and_long() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, int, int]:
    raw, page_count, api_total = read_openapi_pages()
    df = raw.copy()
    hour_cols = detect_hour_columns(df)
    passenger_cols = [col for _, on_col, off_col in hour_cols for col in [on_col, off_col]]
    for col in passenger_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["기준_월"] = df["USE_YM"].astype(str).str.strip()
    df["노선_번호"] = df["RTE_NO"].astype(str).str.strip()
    df["노선_명"] = df["RTE_NM"].astype(str).str.strip()
    df["정류소_ID"] = df["STOPS_ID"].astype(str).str.strip()
    df["정류소_ARS_ID_원천"] = df["STOPS_ARS_NO"].astype(str).str.strip()
    df["정류소_ARS_ID"] = df["정류소_ARS_ID_원천"].map(normalize_ars)
    df["원천_정류장명_순번포함"] = df["SBWY_STNS_NM"].astype(str).str.strip()
    parsed = df["원천_정류장명_순번포함"].map(parse_stop_label)
    df["정류장_명_정제"] = parsed.map(lambda item: item[0])
    df["노선정류장_순번"] = parsed.map(lambda item: item[1])
    df["교통수단유형_코드"] = df["TRFC_MNS_TYPE_CD"].astype(str).str.strip()
    df["교통수단유형_명"] = df["TRFC_MNS_TYPE_NM"].astype(str).str.strip()
    df["작업_일자"] = df["REG_YMD"].astype(str).str.strip()
    df["quality_ars_id_not_5_digit"] = ~df["정류소_ARS_ID"].str.fullmatch(r"\d{5}")
    df["quality_route_stop_sequence_missing"] = df["노선정류장_순번"].eq("")

    long_parts: list[pd.DataFrame] = []
    for hour, on_col, off_col in hour_cols:
        part = df[
            KEY_COLS_SUMMARY
            + [
                "정류소_ARS_ID_원천",
                "정류장_명_정제",
                "노선정류장_순번",
                "교통수단유형_명",
                "작업_일자",
                "_raw_path",
                on_col,
                off_col,
            ]
        ].copy()
        part["시간대"] = hour
        part["시간대_라벨"] = f"{hour:02d}시"
        part["시간대_그룹"] = hour_group(hour)
        part = part.rename(columns={on_col: "승차_인원", off_col: "하차_인원"})
        part["승하차_인원"] = part["승차_인원"] + part["하차_인원"]
        for col in ["승차_인원", "하차_인원", "승하차_인원"]:
            part[col] = part[col].round().astype("Int64")
        long_parts.append(part)
    long_df = pd.concat(long_parts, ignore_index=True)
    long_df["source_id"] = SOURCE_ID
    long_df["provider"] = PROVIDER
    long_df["source_service"] = SERVICE
    long_df["snapshot_date"] = SNAPSHOT_DATE
    long_df["source_grain"] = "기준_월+노선_번호+정류소_ID+정류소_ARS_ID+원천_정류장명_순번포함+교통수단유형_코드+시간대"
    long_df["directness_level"] = "P1_공식_버스노선별_정류장별_시간대_승하차량"
    long_df["forbidden_claim_ko"] = "실제 상권 방문자, 실제 구매자, 전체 버스 수요, 창업 성공확률로 표현 금지"
    long_df["notes_ko"] = "서울 열린데이터광장의 버스노선별 정류장별 시간대 승하차량이다. 상권 접근성 강도 프록시로 쓰며, 정류소 좌표 결합 상태를 별도로 따른다."

    summary = (
        long_df.groupby(KEY_COLS_SUMMARY + ["정류소_ARS_ID_원천", "정류장_명_정제", "노선정류장_순번", "교통수단유형_명", "작업_일자", "_raw_path"], as_index=False)
        .agg(
            월_승차_인원=("승차_인원", "sum"),
            월_하차_인원=("하차_인원", "sum"),
            월_승하차_인원=("승하차_인원", "sum"),
        )
        .sort_values(KEY_COLS_SUMMARY)
        .reset_index(drop=True)
    )
    for col in ["월_승차_인원", "월_하차_인원", "월_승하차_인원"]:
        summary[col] = summary[col].round().astype("Int64")

    group_totals = (
        long_df.groupby(KEY_COLS_SUMMARY + ["시간대_그룹"], as_index=False)["승하차_인원"]
        .sum()
        .pivot_table(index=KEY_COLS_SUMMARY, columns="시간대_그룹", values="승하차_인원", fill_value=0)
        .reset_index()
    )
    for group in ["심야/새벽", "출근/오전", "낮", "퇴근/저녁", "야간"]:
        if group not in group_totals.columns:
            group_totals[group] = 0
    group_totals = group_totals.rename(
        columns={
            "심야/새벽": "심야새벽_승하차_인원",
            "출근/오전": "출근오전_승하차_인원",
            "낮": "낮_승하차_인원",
            "퇴근/저녁": "퇴근저녁_승하차_인원",
            "야간": "야간_승하차_인원",
        }
    )
    for col in ["심야새벽_승하차_인원", "출근오전_승하차_인원", "낮_승하차_인원", "퇴근저녁_승하차_인원", "야간_승하차_인원"]:
        group_totals[col] = group_totals[col].round().astype("Int64")
    summary = summary.merge(group_totals, on=KEY_COLS_SUMMARY, how="left")

    master = load_bus_stop_master()
    summary = summary.merge(master, on=["정류소_ID", "정류소_ARS_ID"], how="left")
    summary["좌표결합_상태"] = "exact_match"
    summary.loc[summary["정류소_마스터명"].isna(), "좌표결합_상태"] = "unmatched_bus_stop_master"
    summary["좌표결합_주의사항"] = summary["좌표결합_상태"].map(
        {
            "exact_match": "정류소 위치 좌표 결합 가능",
            "unmatched_bus_stop_master": "정류소 위치 마스터 미매칭. 수동 매핑 전 좌표 기반 점수 직접 사용 금지",
        }
    )
    summary["quality_ars_id_not_5_digit"] = ~summary["정류소_ARS_ID"].str.fullmatch(r"\d{5}")
    summary["quality_route_stop_sequence_missing"] = summary["노선정류장_순번"].eq("")
    summary["source_id"] = SOURCE_ID
    summary["provider"] = PROVIDER
    summary["source_service"] = SERVICE
    summary["snapshot_date"] = SNAPSHOT_DATE
    summary["source_grain"] = "기준_월+노선_번호+정류소_ID+정류소_ARS_ID+원천_정류장명_순번포함+교통수단유형_코드"
    summary["raw_page_count"] = page_count
    summary["api_list_total_count"] = api_total
    summary["raw_row_count"] = len(df)
    summary["directness_level"] = "P1_공식_버스노선별_정류장별_월간_승하차량_요약"
    summary["forbidden_claim_ko"] = "실제 상권 방문자, 실제 구매자, 전체 버스 수요, 창업 성공확률로 표현 금지"
    summary["notes_ko"] = "시간대별 승하차량을 월간 노선-정류소 단위로 접은 테이블이다. 좌표결합_상태가 exact_match가 아닌 row는 좌표 기반 점수에 직접 투입하지 않는다."

    type_codebook = (
        summary.groupby(["교통수단유형_코드", "교통수단유형_명"], as_index=False)
        .agg(row_count=("기준_월", "count"), 월_승하차_인원=("월_승하차_인원", "sum"))
        .sort_values("row_count", ascending=False)
    )
    type_codebook["usage_role"] = "버스 유형별 접근성 보조 해석"
    type_codebook["score_use_warning_ko"] = "버스 유형은 접근성 보조 분류이며 실제 상권 방문목적을 의미하지 않는다."
    type_codebook["source_id"] = SOURCE_ID
    type_codebook["provider"] = PROVIDER
    type_codebook["snapshot_date"] = SNAPSHOT_DATE

    return summary, long_df.sort_values(KEY_COLS_LONG).reset_index(drop=True), build_hour_codebook(hour_cols), type_codebook, page_count, api_total


def key_null_cells(df: pd.DataFrame, key_cols: list[str]) -> int:
    return sum(int((df[col].isna() | df[col].astype(str).str.strip().eq("")).sum()) for col in key_cols)


def duplicate_key_rows(df: pd.DataFrame, key_cols: list[str]) -> int:
    return int(df.duplicated(key_cols).sum())


def validate_bus_passengers(
    summary: pd.DataFrame,
    long_df: pd.DataFrame,
    hour_codebook: pd.DataFrame,
    type_codebook: pd.DataFrame,
    page_count: int,
    api_total: int,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    numeric_cols = ["승차_인원", "하차_인원", "승하차_인원"]
    null_passenger_cells = int(long_df[numeric_cols].isna().sum().sum())
    negative_passenger_cells = int((long_df[numeric_cols] < 0).sum().sum())
    fractional_passenger_cells = int(((long_df[numeric_cols] % 1) != 0).sum().sum())
    summary_total = int(summary["월_승하차_인원"].sum())
    long_total = int(long_df["승하차_인원"].sum())
    total_diff = abs(summary_total - long_total)
    key_null_summary = key_null_cells(summary, KEY_COLS_SUMMARY)
    key_null_long = key_null_cells(long_df, KEY_COLS_LONG)
    dup_summary = duplicate_key_rows(summary, KEY_COLS_SUMMARY)
    dup_long = duplicate_key_rows(long_df, KEY_COLS_LONG)
    match_counts = summary["좌표결합_상태"].value_counts().to_dict()
    exact_count = int(match_counts.get("exact_match", 0))
    unmatched_count = int(match_counts.get("unmatched_bus_stop_master", 0))
    invalid_ars_rows = int(summary["quality_ars_id_not_5_digit"].sum())
    sequence_missing_rows = int(summary["quality_route_stop_sequence_missing"].sum())
    unique_stop_pairs = int(summary[["정류소_ID", "정류소_ARS_ID"]].drop_duplicates().shape[0])
    unmatched_stop_pairs = int(
        summary.loc[summary["좌표결합_상태"].eq("unmatched_bus_stop_master"), ["정류소_ID", "정류소_ARS_ID"]]
        .drop_duplicates()
        .shape[0]
    )

    hard_fail = (
        len(summary) != api_total
        or len(long_df) != api_total * 24
        or key_null_summary != 0
        or key_null_long != 0
        or dup_summary != 0
        or dup_long != 0
        or null_passenger_cells != 0
        or negative_passenger_cells != 0
        or total_diff > 0
    )
    judgement = "FAIL" if hard_fail else ("조건부 PASS" if unmatched_count or invalid_ars_rows else "PASS")

    domain_df = pd.DataFrame(
        [
            {
                "table": "silver_bus_passenger_route_stop_month_summary",
                "rows": len(summary),
                "api_total_count": api_total,
                "raw_page_count": page_count,
                "row_count_matches_api": len(summary) == api_total,
                "month_min": summary["기준_월"].min(),
                "month_max": summary["기준_월"].max(),
                "route_count": summary["노선_번호"].nunique(),
                "stop_id_count": summary["정류소_ID"].nunique(),
                "unique_stop_pair_count": unique_stop_pairs,
                "key_null_cells": key_null_summary,
                "duplicate_key_rows": dup_summary,
                "passenger_null_cells": "",
                "passenger_negative_cells": "",
                "passenger_fractional_cells": "",
                "summary_total_passengers": summary_total,
                "long_total_passengers": long_total,
                "total_diff": total_diff,
                "exact_match_rows": exact_count,
                "unmatched_bus_stop_master_rows": unmatched_count,
                "unmatched_stop_pair_count": unmatched_stop_pairs,
                "ars_id_not_5_digit_rows": invalid_ars_rows,
                "route_stop_sequence_missing_rows": sequence_missing_rows,
                "judgement": judgement,
                "conditional_reason_ko": "정류소 위치 마스터 미매칭 또는 비표준 ARS-ID가 있어 좌표 기반 점수 직접 사용을 제한함" if judgement == "조건부 PASS" else "",
            },
            {
                "table": "silver_bus_passenger_route_stop_month_hour",
                "rows": len(long_df),
                "api_total_count": api_total * 24,
                "raw_page_count": page_count,
                "row_count_matches_api": len(long_df) == api_total * 24,
                "month_min": long_df["기준_월"].min(),
                "month_max": long_df["기준_월"].max(),
                "route_count": long_df["노선_번호"].nunique(),
                "stop_id_count": long_df["정류소_ID"].nunique(),
                "unique_stop_pair_count": "",
                "key_null_cells": key_null_long,
                "duplicate_key_rows": dup_long,
                "passenger_null_cells": null_passenger_cells,
                "passenger_negative_cells": negative_passenger_cells,
                "passenger_fractional_cells": fractional_passenger_cells,
                "summary_total_passengers": summary_total,
                "long_total_passengers": long_total,
                "total_diff": total_diff,
                "exact_match_rows": "",
                "unmatched_bus_stop_master_rows": "",
                "unmatched_stop_pair_count": "",
                "ars_id_not_5_digit_rows": "",
                "route_stop_sequence_missing_rows": "",
                "judgement": judgement,
                "conditional_reason_ko": "원천의 시간대 값을 모두 long 형식으로 보존했으며 좌표 결합은 summary의 상태를 따른다." if judgement != "FAIL" else "",
            },
            {
                "table": "silver_bus_passenger_hour_codebook",
                "rows": len(hour_codebook),
                "api_total_count": 24,
                "raw_page_count": "",
                "row_count_matches_api": len(hour_codebook) == 24,
                "month_min": "",
                "month_max": "",
                "route_count": "",
                "stop_id_count": "",
                "unique_stop_pair_count": "",
                "key_null_cells": key_null_cells(hour_codebook, ["시간대"]),
                "duplicate_key_rows": duplicate_key_rows(hour_codebook, ["시간대"]),
                "passenger_null_cells": "",
                "passenger_negative_cells": "",
                "passenger_fractional_cells": "",
                "summary_total_passengers": "",
                "long_total_passengers": "",
                "total_diff": "",
                "exact_match_rows": "",
                "unmatched_bus_stop_master_rows": "",
                "unmatched_stop_pair_count": "",
                "ars_id_not_5_digit_rows": "",
                "route_stop_sequence_missing_rows": "",
                "judgement": "PASS",
                "conditional_reason_ko": "",
            },
            {
                "table": "silver_bus_passenger_transport_type_codebook",
                "rows": len(type_codebook),
                "api_total_count": "",
                "raw_page_count": "",
                "row_count_matches_api": "",
                "month_min": "",
                "month_max": "",
                "route_count": "",
                "stop_id_count": "",
                "unique_stop_pair_count": "",
                "key_null_cells": key_null_cells(type_codebook, ["교통수단유형_코드", "교통수단유형_명"]),
                "duplicate_key_rows": duplicate_key_rows(type_codebook, ["교통수단유형_코드", "교통수단유형_명"]),
                "passenger_null_cells": "",
                "passenger_negative_cells": "",
                "passenger_fractional_cells": "",
                "summary_total_passengers": "",
                "long_total_passengers": "",
                "total_diff": "",
                "exact_match_rows": "",
                "unmatched_bus_stop_master_rows": "",
                "unmatched_stop_pair_count": "",
                "ars_id_not_5_digit_rows": "",
                "route_stop_sequence_missing_rows": "",
                "judgement": "PASS",
                "conditional_reason_ko": "",
            },
        ]
    )
    grain_df = pd.DataFrame(
        [
            {
                "table": "silver_bus_passenger_route_stop_month_summary",
                "key_cols": " + ".join(KEY_COLS_SUMMARY),
                "duplicate_key_rows": dup_summary,
                "key_null_cells": key_null_summary,
                "judgement": "PASS" if dup_summary == 0 and key_null_summary == 0 else "FAIL",
                "reason_ko": "같은 노선+정류소라도 원천정류장명 괄호 속 순번이 다르면 진행 방향/순번이 다를 수 있어 원천 라벨을 grain에 포함한다.",
            },
            {
                "table": "silver_bus_passenger_route_stop_month_hour",
                "key_cols": " + ".join(KEY_COLS_LONG),
                "duplicate_key_rows": dup_long,
                "key_null_cells": key_null_long,
                "judgement": "PASS" if dup_long == 0 and key_null_long == 0 else "FAIL",
                "reason_ko": "시간대별 승차/하차 컬럼을 24시간 long grain으로 풀어 시간대 수요 패턴을 잃지 않는다.",
            },
            {
                "table": "silver_bus_passenger_hour_codebook",
                "key_cols": "시간대",
                "duplicate_key_rows": duplicate_key_rows(hour_codebook, ["시간대"]),
                "key_null_cells": key_null_cells(hour_codebook, ["시간대"]),
                "judgement": "PASS",
                "reason_ko": "시간대와 원천 컬럼명을 분리해 알고리즘 주석과 리포트에 재사용한다.",
            },
            {
                "table": "silver_bus_passenger_transport_type_codebook",
                "key_cols": "교통수단유형_코드 + 교통수단유형_명",
                "duplicate_key_rows": duplicate_key_rows(type_codebook, ["교통수단유형_코드", "교통수단유형_명"]),
                "key_null_cells": key_null_cells(type_codebook, ["교통수단유형_코드", "교통수단유형_명"]),
                "judgement": "PASS",
                "reason_ko": "교통수단유형은 노선별 성격을 해석하는 보조 분류이며 실제 방문목적이 아니다.",
            },
        ]
    )
    join_status_df = (
        summary["좌표결합_상태"]
        .value_counts(dropna=False)
        .rename_axis("좌표결합_상태")
        .reset_index(name="row_count")
    )
    join_status_df["unique_stop_pair_count"] = join_status_df["좌표결합_상태"].map(
        summary.groupby("좌표결합_상태")[["정류소_ID", "정류소_ARS_ID"]].apply(lambda x: x.drop_duplicates().shape[0]).to_dict()
    )
    join_status_df["usage_decision_ko"] = join_status_df["좌표결합_상태"].map(
        {
            "exact_match": "좌표 결합 가능",
            "unmatched_bus_stop_master": "수동 매핑 전 좌표 기반 점수 직접 사용 금지",
        }
    ).fillna("확인 필요")
    join_status_df["source_id"] = SOURCE_ID
    join_status_df["bus_stop_source_id"] = BUS_STOP_SOURCE_ID
    contract_df = pd.DataFrame(
        [
            {
                "table": "silver_bus_passenger_route_stop_month_summary",
                "source_id": SOURCE_ID,
                "provider": PROVIDER,
                "source_service": SERVICE,
                "rows": len(summary),
                "contract_status": judgement,
                "usage_role": "월별 노선-정류소 승하차량 요약과 좌표 결합 상태 관리",
            },
            {
                "table": "silver_bus_passenger_route_stop_month_hour",
                "source_id": SOURCE_ID,
                "provider": PROVIDER,
                "source_service": SERVICE,
                "rows": len(long_df),
                "contract_status": judgement,
                "usage_role": "시간대별 버스 승하차 접근성 강도 프록시",
            },
            {
                "table": "silver_bus_passenger_hour_codebook",
                "source_id": SOURCE_ID,
                "provider": PROVIDER,
                "source_service": SERVICE,
                "rows": len(hour_codebook),
                "contract_status": "PASS",
                "usage_role": "시간대 그룹과 원천 컬럼 해석",
            },
            {
                "table": "silver_bus_passenger_transport_type_codebook",
                "source_id": SOURCE_ID,
                "provider": PROVIDER,
                "source_service": SERVICE,
                "rows": len(type_codebook),
                "contract_status": "PASS",
                "usage_role": "버스 유형별 보조 해석",
            },
        ]
    )
    return domain_df, grain_df, join_status_df, contract_df


def write_validation_md(
    domain_df: pd.DataFrame,
    grain_df: pd.DataFrame,
    join_status_df: pd.DataFrame,
    hour_codebook: pd.DataFrame,
    type_codebook: pd.DataFrame,
    summary: pd.DataFrame,
) -> None:
    path = RESEARCH_VALIDATION_DIR / "10_bus_passenger_silver_validation_20260703.md"
    main = domain_df.loc[domain_df["table"].eq("silver_bus_passenger_route_stop_month_summary")].iloc[0].to_dict()
    hour = domain_df.loc[domain_df["table"].eq("silver_bus_passenger_route_stop_month_hour")].iloc[0].to_dict()
    top_rows = summary.sort_values("월_승하차_인원", ascending=False).head(10)
    source_month_text = ", ".join(sorted(summary["기준_월"].astype(str).unique()))
    summary_rows = int(main["rows"])
    long_rows = int(hour["rows"])
    raw_page_count = int(main["raw_page_count"])
    lines = [
        "# 10차 버스 승하차량 silver 전처리 검증",
        "",
        f"작성시각: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "## 대상 파일",
        "",
        "- `datacorpus/_silver/silver_bus_passenger_route_stop_month_summary.csv`",
        "- `datacorpus/_silver/silver_bus_passenger_route_stop_month_hour.csv`",
        "- `datacorpus/_silver/silver_bus_passenger_hour_codebook.csv`",
        "- `datacorpus/_silver/silver_bus_passenger_transport_type_codebook.csv`",
        "",
        "## 사용 근거",
        "",
        "- `datacorpus/_raw_ingest/source_registry.csv`: 버스 정류장별 시간대 승하차량은 접근성/유입 검증과 Dynamic Huff 보조 원천으로 등록되어 있다.",
        f"- `datacorpus/_raw_ingest/run_logs/20260703_transport_accessibility_sources_ko.md`: `CardBusTimeNew` 원천 수집 기록을 기준으로 현재 읽은 월 {source_month_text}, summary {summary_rows:,}건 / raw page {raw_page_count:,}페이지를 검증 대상으로 삼았다.",
        "- `research/algorithm_evidence_sources/data_docs/seoul_open_data_bus_stop_passengers_hourly_OA-12913.html`: 버스노선별 정류장별 시간대별 승하차인원이며 월단위, 서울버스 대상, 매월 5일 전월 데이터 갱신이라고 설명한다.",
        "- `research/rule_validation/07_bus_stop_location_silver_validation_20260703.md`: 버스정류소 위치 마스터 11,248건이 정류소 좌표 기준으로 정리되어 있다.",
        "- `research/전처리_알고리즘_실행계획_20260703.md`: 접근성/유입 축은 정류장 개수만이 아니라 좌표, 거리감쇠, 시간대 승하차량 결합을 목표로 한다.",
        "",
        "## 검증 1: 원천 총량 계약",
        "",
        "| table | rows | api_total_count | raw_page_count | judgement |",
        "|---|---:|---:|---:|---|",
    ]
    for row in domain_df.to_dict("records"):
        lines.append(f"| `{row['table']}` | {row['rows']} | {row['api_total_count']} | {row['raw_page_count']} | {row['judgement']} |")

    lines.extend(
        [
            "",
            f"판단: 원천 {summary_rows:,}건을 월별 노선-정류소 요약으로 보존했고, 시간대별 long 테이블은 {summary_rows:,}×24={long_rows:,}행으로 풀었다.",
            "",
            "## 검증 2: grain과 중복",
            "",
            "| table | key_cols | duplicate_key_rows | key_null_cells | judgement |",
            "|---|---|---:|---:|---|",
        ]
    )
    for row in grain_df.to_dict("records"):
        lines.append(f"| `{row['table']}` | `{row['key_cols']}` | {row['duplicate_key_rows']} | {row['key_null_cells']} | {row['judgement']} |")

    lines.extend(
        [
            "",
            "판단: 원천의 `SBWY_STNS_NM` 괄호 속 순번이 같은 노선+정류소의 진행 순서를 구분한다. 따라서 순번이 제거된 노선+정류소 grain으로 미리 합산하지 않는다.",
            "",
            "## 검증 3: 승하차량 값 품질",
            "",
            "| 항목 | 값 |",
            "|---|---:|",
            f"| passenger null cells | {hour['passenger_null_cells']} |",
            f"| passenger negative cells | {hour['passenger_negative_cells']} |",
            f"| passenger fractional cells | {hour['passenger_fractional_cells']} |",
            f"| summary total passengers | {main['summary_total_passengers']} |",
            f"| long total passengers | {main['long_total_passengers']} |",
            f"| total diff | {main['total_diff']} |",
            f"| ARS-ID 5자리 아님 row | {main['ars_id_not_5_digit_rows']} |",
            f"| 노선정류장 순번 누락 row | {main['route_stop_sequence_missing_rows']} |",
            "",
            "판단: 시간대별 승차/하차 값에는 null과 음수가 없고, 요약 총량과 long 총량이 일치한다. 비표준 ARS-ID는 좌표 결합 제한 사유로 남긴다.",
            "",
            "## 검증 4: 버스정류소 위치 좌표 결합 상태",
            "",
            "| 좌표결합_상태 | row_count | unique_stop_pair_count | 사용 판단 |",
            "|---|---:|---:|---|",
        ]
    )
    for row in join_status_df.to_dict("records"):
        lines.append(f"| {row['좌표결합_상태']} | {row['row_count']} | {row['unique_stop_pair_count']} | {row['usage_decision_ko']} |")

    lines.extend(
        [
            "",
            "판단: 정류소 위치 마스터와 exact 결합되지 않는 row를 버리지 않고 보존했다. 다만 좌표 결합이 안 된 row는 거리감쇠 접근성 점수에 직접 넣지 않는다.",
            "",
            "## 검증 5: 교통수단 유형 코드북",
            "",
            "| 교통수단유형_코드 | 교통수단유형_명 | row_count | 월_승하차_인원 |",
            "|---|---|---:|---:|",
        ]
    )
    for row in type_codebook.to_dict("records"):
        lines.append(f"| {row['교통수단유형_코드']} | {row['교통수단유형_명']} | {row['row_count']} | {row['월_승하차_인원']} |")

    lines.extend(
        [
            "",
            "## 검증 6: 시간대 코드북",
            "",
            "| 시간대 | 라벨 | 그룹 | 승차 원천컬럼 | 하차 원천컬럼 |",
            "|---:|---|---|---|---|",
        ]
    )
    for row in hour_codebook.to_dict("records"):
        lines.append(f"| {row['시간대']} | {row['시간대_라벨']} | {row['시간대_그룹']} | `{row['승차_원천컬럼']}` | `{row['하차_원천컬럼']}` |")

    lines.extend(
        [
            "",
            "## 참고: 월 승하차량 상위 10개 row",
            "",
            "| 기준_월 | 노선 | 정류장 | 순번 | 월_승하차_인원 | 좌표결합_상태 |",
            "|---|---|---|---|---:|---|",
        ]
    )
    for row in top_rows.to_dict("records"):
        lines.append(
            f"| {row['기준_월']} | {row['노선_번호']} | {row['정류장_명_정제']} | {row['노선정류장_순번']} | {row['월_승하차_인원']} | {row['좌표결합_상태']} |"
        )

    lines.extend(
        [
            "",
            "## 2보 전진 1보 후퇴 기록",
            "",
            f"- 전진 1: {source_month_text} 버스 승하차량 {summary_rows:,}건을 월별 요약과 24시간 long 테이블로 모두 보존했다.",
            "- 전진 2: 정류소 위치 마스터와 exact 좌표 결합 상태를 분리했다.",
            "- 후퇴 1: 버스 승하차량은 해당 상권 방문자나 구매자가 아니다.",
            "- 후퇴 2: `unmatched_bus_stop_master` row는 좌표 기반 거리감쇠 점수에 직접 투입하지 않는다.",
            "- 후퇴 3: 노선정류장 순번을 제거하면 왕복/순환 노선의 진행 방향 정보가 섞일 수 있으므로 원천 라벨을 보존한다.",
            "",
            "## 알고리즘 단계에서 금지할 표현",
            "",
            "- 실제 상권 방문자",
            "- 실제 구매자",
            "- 전체 버스 수요",
            "- 창업 성공확률",
            "",
            "허용 표현:",
            "",
            "- 시간대 버스 승하차량 기반 접근성 강도 프록시",
            "- 정류소 좌표 결합 가능 여부가 표시된 교통 수요 원천",
            "- 노선+정류소+순번 기준 월별 승하차량",
            "",
            "## 다음 작업",
            "",
            "1. 교통 접근성 gold 후보 설계 전, exact 좌표 결합 row만 사용할지 수동매핑을 먼저 할지 결정.",
            "2. 생활이동 OD silver 전처리.",
            "3. 상권 polygon point-in-polygon 기준 확정.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def append_progress(domain_df: pd.DataFrame) -> None:
    if not PROGRESS_PATH.exists():
        return
    summary = domain_df.loc[domain_df["table"].eq("silver_bus_passenger_route_stop_month_summary")].iloc[0].to_dict()
    hour = domain_df.loc[domain_df["table"].eq("silver_bus_passenger_route_stop_month_hour")].iloc[0].to_dict()
    hour_codebook = domain_df.loc[domain_df["table"].eq("silver_bus_passenger_hour_codebook")].iloc[0].to_dict()
    type_codebook = domain_df.loc[domain_df["table"].eq("silver_bus_passenger_transport_type_codebook")].iloc[0].to_dict()
    block = [
        "",
        "---",
        "",
        "## 12. 완료: 버스 승하차량 silver 테이블",
        "",
        "| 산출물 | row 수 | 상태 | 역할 |",
        "|---|---:|---|---|",
        f"| `datacorpus/_silver/silver_bus_passenger_route_stop_month_summary.csv` | {summary['rows']:,} | {summary['judgement']} | 월별 노선-정류소 승하차량 요약 |",
        f"| `datacorpus/_silver/silver_bus_passenger_route_stop_month_hour.csv` | {hour['rows']:,} | {hour['judgement']} | 시간대별 승하차량 long 테이블 |",
        f"| `datacorpus/_silver/silver_bus_passenger_hour_codebook.csv` | {hour_codebook['rows']:,} | {hour_codebook['judgement']} | 시간대 그룹 코드북 |",
        f"| `datacorpus/_silver/silver_bus_passenger_transport_type_codebook.csv` | {type_codebook['rows']:,} | {type_codebook['judgement']} | 버스 유형 코드북 |",
        "",
        "검증 근거:",
        "",
        "- `datacorpus/_rule_validation/10_bus_passenger_domain_validation.csv`",
        "- `datacorpus/_rule_validation/10_bus_passenger_grain_validation.csv`",
        "- `datacorpus/_rule_validation/10_bus_passenger_join_status.csv`",
        "- `datacorpus/_rule_validation/10_bus_passenger_source_contract.csv`",
        "- `research/rule_validation/10_bus_passenger_silver_validation_20260703.md`",
        "",
        "판단:",
        "",
        f"- 원천 {int(summary['rows']):,}건과 summary row 수가 일치한다.",
        f"- 시간대 long 테이블은 {int(summary['rows']):,}×24={int(hour['rows']):,}행이며 시간대별 값을 잃지 않는다.",
        f"- 좌표 exact_match {summary['exact_match_rows']}건, unmatched_bus_stop_master {summary['unmatched_bus_stop_master_rows']}건으로 분리했다.",
        f"- 비표준 ARS-ID row는 {summary['ars_id_not_5_digit_rows']}건이며 좌표 결합 제한 사유로 남긴다.",
        "- 승하차량은 실제 상권 방문자나 구매자가 아니라 접근성 강도 프록시다.",
    ]
    text = PROGRESS_PATH.read_text(encoding="utf-8")
    marker = "## 12. 완료: 버스 승하차량 silver 테이블"
    if marker in text:
        text = text.split("\n---\n\n" + marker)[0].rstrip()
    PROGRESS_PATH.write_text(text.rstrip() + "\n" + "\n".join(block) + "\n", encoding="utf-8")


def append_precheck(domain_df: pd.DataFrame) -> None:
    if not PRECHECK_PATH.exists():
        return
    main = domain_df.loc[domain_df["table"].eq("silver_bus_passenger_route_stop_month_summary")].iloc[0].to_dict()
    text = PRECHECK_PATH.read_text(encoding="utf-8")
    marker = "| 버스 승하차량 |"
    if marker in text:
        return
    target = next((line for line in text.splitlines() if line.startswith("| 지하철 승하차량 |")), "")
    addition = (
        target
        + "\n"
        + f"| 버스 승하차량 | {main['rows']:,}건 월별 요약과 {int(main['rows']) * 24:,}건 시간대 long silver 생성 완료 | 시간대 접근성 강도 프록시로 쓰되 좌표 exact_match {main['exact_match_rows']}건 외에는 수동 매핑 전 점수 직접 사용을 제한한다. |"
    )
    if target and target in text:
        text = text.replace(target, addition)
        PRECHECK_PATH.write_text(text, encoding="utf-8")


def main() -> None:
    ensure_dirs()
    summary, long_df, hour_codebook, type_codebook, page_count, api_total = build_summary_and_long()
    domain_df, grain_df, join_status_df, contract_df = validate_bus_passengers(
        summary,
        long_df,
        hour_codebook,
        type_codebook,
        page_count,
        api_total,
    )
    failed_rules = domain_df.loc[domain_df["judgement"].eq("FAIL"), "table"].astype(str).tolist()
    if failed_rules:
        raise RuntimeError(
            "버스 승하차량 전처리 검증 FAIL로 silver 저장을 중단합니다. "
            f"부분 raw 또는 원천 불일치 가능성이 있습니다: {failed_rules}"
        )

    summary.to_csv(SILVER_DIR / "silver_bus_passenger_route_stop_month_summary.csv", index=False, encoding="utf-8-sig")
    long_df.to_csv(SILVER_DIR / "silver_bus_passenger_route_stop_month_hour.csv", index=False, encoding="utf-8-sig")
    hour_codebook.to_csv(SILVER_DIR / "silver_bus_passenger_hour_codebook.csv", index=False, encoding="utf-8-sig")
    type_codebook.to_csv(SILVER_DIR / "silver_bus_passenger_transport_type_codebook.csv", index=False, encoding="utf-8-sig")
    domain_df.to_csv(VALIDATION_DIR / "10_bus_passenger_domain_validation.csv", index=False, encoding="utf-8-sig")
    grain_df.to_csv(VALIDATION_DIR / "10_bus_passenger_grain_validation.csv", index=False, encoding="utf-8-sig")
    join_status_df.to_csv(VALIDATION_DIR / "10_bus_passenger_join_status.csv", index=False, encoding="utf-8-sig")
    contract_df.to_csv(VALIDATION_DIR / "10_bus_passenger_source_contract.csv", index=False, encoding="utf-8-sig")
    write_validation_md(domain_df, grain_df, join_status_df, hour_codebook, type_codebook, summary)
    append_progress(domain_df)
    append_precheck(domain_df)

    summary_json = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "summary_rows": len(summary),
        "long_rows": len(long_df),
        "hour_codebook_rows": len(hour_codebook),
        "transport_type_codebook_rows": len(type_codebook),
        "validation_judgements": domain_df[["table", "judgement"]].to_dict("records"),
        "outputs": [
            "datacorpus/_silver/silver_bus_passenger_route_stop_month_summary.csv",
            "datacorpus/_silver/silver_bus_passenger_route_stop_month_hour.csv",
            "datacorpus/_silver/silver_bus_passenger_hour_codebook.csv",
            "datacorpus/_silver/silver_bus_passenger_transport_type_codebook.csv",
            "datacorpus/_rule_validation/10_bus_passenger_domain_validation.csv",
            "datacorpus/_rule_validation/10_bus_passenger_grain_validation.csv",
            "datacorpus/_rule_validation/10_bus_passenger_join_status.csv",
            "datacorpus/_rule_validation/10_bus_passenger_source_contract.csv",
            "research/rule_validation/10_bus_passenger_silver_validation_20260703.md",
        ],
    }
    (VALIDATION_DIR / "10_bus_passenger_preprocess_summary.json").write_text(
        json.dumps(summary_json, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary_json, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
