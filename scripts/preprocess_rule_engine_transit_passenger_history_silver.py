# -*- coding: utf-8 -*-
"""
교통 승하차량 2021~2025 이력 silver 전처리.

이 스크립트의 목적은 단순히 raw를 크게 합치는 것이 아니라,
알고리즘 점수에 바로 필요한 월별 summary는 기존 downstream 파일명으로
갱신하고, 시간대 long 구조는 필요 시 월별 파티션으로만 물리화하는 것이다.
기본 실행은 hour-long 단일 대형 CSV를 만들지 않는다. 원천 raw에는 24시간
승하차 컬럼이 그대로 남아 있고, summary에는 월합계와 시간대 그룹합계를
남기므로 점수 엔진은 필요한 수준의 정보를 사용한다.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "datacorpus" / "_raw_ingest"
SILVER_DIR = ROOT / "datacorpus" / "_silver"
VALIDATION_DIR = ROOT / "datacorpus" / "_rule_validation"
RESEARCH_VALIDATION_DIR = ROOT / "research" / "rule_validation"

SNAPSHOT_DATE = "2026-07-07"
PROVIDER = "서울열린데이터광장"

BUS_SERVICE = "CardBusTimeNew"
BUS_SOURCE_ID = "seoul_bus_stop_passengers_hourly"
BUS_RAW_RELATIVE_PARTS = ("seoul_open_data", "transport", "bus_stop_passengers_hourly")
BUS_STOP_MASTER_PATH = SILVER_DIR / "silver_bus_stop_location_master.csv"
BUS_SUMMARY_PATH = SILVER_DIR / "silver_bus_passenger_route_stop_month_summary.csv"
BUS_LEGACY_HOUR_PATH = SILVER_DIR / "silver_bus_passenger_route_stop_month_hour.csv"
BUS_HOUR_MANIFEST_PATH = SILVER_DIR / "silver_bus_passenger_route_stop_month_hour_manifest.csv"
BUS_HOUR_PART_DIR = SILVER_DIR / "silver_bus_passenger_route_stop_month_hour_parts"
BUS_SUMMARY_PART_DIR = SILVER_DIR / "silver_bus_passenger_route_stop_month_summary_parts"
BUS_HOUR_CODEBOOK_PATH = SILVER_DIR / "silver_bus_passenger_hour_codebook.csv"
BUS_TYPE_CODEBOOK_PATH = SILVER_DIR / "silver_bus_passenger_transport_type_codebook.csv"

SUBWAY_SERVICE = "CardSubwayTime"
SUBWAY_SOURCE_ID = "seoul_subway_station_passengers_hourly"
SUBWAY_RAW_RELATIVE_PARTS = ("seoul_open_data", "transport", "subway_station_passengers_hourly")
SUBWAY_JOIN_AUDIT_PATH = VALIDATION_DIR / "08_subway_station_master_passenger_join_audit.csv"
SUBWAY_STATION_MASTER_PATH = SILVER_DIR / "silver_subway_station_master.csv"
SUBWAY_SUMMARY_PATH = SILVER_DIR / "silver_subway_passenger_station_month_summary.csv"
SUBWAY_LEGACY_HOUR_PATH = SILVER_DIR / "silver_subway_passenger_station_month_hour.csv"
SUBWAY_HOUR_MANIFEST_PATH = SILVER_DIR / "silver_subway_passenger_station_month_hour_manifest.csv"
SUBWAY_HOUR_PART_DIR = SILVER_DIR / "silver_subway_passenger_station_month_hour_parts"
SUBWAY_SUMMARY_PART_DIR = SILVER_DIR / "silver_subway_passenger_station_month_summary_parts"
SUBWAY_HOUR_CODEBOOK_PATH = SILVER_DIR / "silver_subway_passenger_hour_codebook.csv"

BUS_KEY_COLS_SUMMARY = [
    "기준_월",
    "노선_번호",
    "노선_명",
    "정류소_ID",
    "정류소_ARS_ID",
    "원천_정류장명_순번포함",
    "교통수단유형_코드",
    "원천_row_id",
]
BUS_KEY_COLS_LONG = BUS_KEY_COLS_SUMMARY + ["시간대"]
SUBWAY_KEY_COLS_SUMMARY = ["기준_월", "승하차_호선명", "승하차_역명"]
SUBWAY_KEY_COLS_LONG = SUBWAY_KEY_COLS_SUMMARY + ["시간대"]

TIME_GROUPS = ["심야/새벽", "출근/오전", "낮", "퇴근/저녁", "야간"]
TIME_GROUP_OUTPUT_COLS = {
    "심야/새벽": "심야새벽_승하차_인원",
    "출근/오전": "출근오전_승하차_인원",
    "낮": "낮_승하차_인원",
    "퇴근/저녁": "퇴근저녁_승하차_인원",
    "야간": "야간_승하차_인원",
}


@dataclass
class MonthResult:
    mode: str
    month: str
    raw_rows: int
    api_total: int
    page_count: int
    summary_rows: int
    expected_long_rows: int
    key_null_cells: int
    duplicate_summary_key_rows: int
    duplicate_long_key_rows_estimate: int
    passenger_null_cells: int
    passenger_negative_cells: int
    passenger_fractional_cells: int
    summary_total_passengers: int
    hourly_total_passengers: int
    total_diff: int
    exact_match_rows: int
    review_or_unmatched_rows: int
    summary_path: str
    hour_partition_path: str
    hour_partition_materialized: bool
    hour_partition_bytes: int


def ensure_dirs() -> None:
    for path in [
        SILVER_DIR,
        VALIDATION_DIR,
        RESEARCH_VALIDATION_DIR,
        BUS_SUMMARY_PART_DIR,
        SUBWAY_SUMMARY_PART_DIR,
        BUS_HOUR_PART_DIR,
        SUBWAY_HOUR_PART_DIR,
    ]:
        path.mkdir(parents=True, exist_ok=True)


def page_sort_key(path: Path) -> tuple[int, int]:
    match = re.search(r"_(\d+)_(\d+)(?:_\d+)?\.json$", path.name)
    if not match:
        return (10**12, 10**12)
    return (int(match.group(1)), int(match.group(2)))


def discover_month_paths(relative_parts: tuple[str, ...]) -> list[Path]:
    month_by_name: dict[str, Path] = {}
    for date_dir in sorted(RAW_DIR.glob("20??????")):
        base_path = date_dir.joinpath(*relative_parts)
        if not base_path.exists():
            continue
        for path in base_path.iterdir():
            if path.is_dir() and re.fullmatch(r"\d{6}", path.name):
                # 같은 월이 여러 수집일에 있으면 최신 수집일 폴더를 채택한다.
                month_by_name[path.name] = path
    month_paths = [month_by_name[name] for name in sorted(month_by_name)]
    if not month_paths:
        raise FileNotFoundError(f"{relative_parts} 아래에서 YYYYMM 월 폴더를 찾지 못했습니다.")
    return month_paths


def read_month_pages(month_path: Path, service: str) -> tuple[pd.DataFrame, int, int]:
    rows: list[dict[str, Any]] = []
    page_paths = sorted(month_path.glob(f"{service}_*.json"), key=page_sort_key)
    if not page_paths:
        raise FileNotFoundError(f"{month_path} 폴더에서 {service} 원응답을 찾지 못했습니다.")

    month_totals: set[int] = set()
    for page_path in page_paths:
        payload = json.loads(page_path.read_text(encoding="utf-8"))
        root = payload.get(service)
        if not isinstance(root, dict):
            raise ValueError(f"{page_path} 파일에 {service} 루트가 없습니다.")
        if "list_total_count" in root:
            month_totals.add(int(root["list_total_count"]))
            for row_index, row in enumerate(root.get("row", []), start=1):
                item = dict(row)
                item["_raw_path"] = str(page_path.relative_to(ROOT))
                item["_raw_month_dir"] = month_path.name
                item["_raw_page_row_number"] = row_index
                item["_raw_record_id"] = f"{item['_raw_path']}#{row_index:06d}"
                rows.append(item)

    if len(month_totals) != 1:
        raise ValueError(f"{service} {month_path.name} list_total_count가 하나로 고정되지 않습니다: {sorted(month_totals)}")
    if not rows:
        raise ValueError(f"{service} {month_path.name} 원천 row를 읽지 못했습니다.")
    return pd.DataFrame(rows), len(page_paths), next(iter(month_totals))


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


def bus_hour_columns(df: pd.DataFrame) -> list[tuple[int, str, str]]:
    found: dict[int, dict[str, str]] = {}
    for col in df.columns:
        match = re.match(r"HR_(\d+)_GET_(ON|OFF)_(?:T?NOPE)$", col)
        if not match:
            continue
        hour = int(match.group(1))
        found.setdefault(hour, {})[match.group(2)] = col
    missing = [hour for hour in range(24) if set(found.get(hour, {}).keys()) != {"ON", "OFF"}]
    if missing:
        raise ValueError(f"버스 승하차량 시간대 승차/하차 컬럼이 부족합니다: {missing}")
    return [(hour, found[hour]["ON"], found[hour]["OFF"]) for hour in range(24)]


def subway_hour_columns() -> list[tuple[int, str, str]]:
    return [(hour, f"HR_{hour}_GET_ON_NOPE", f"HR_{hour}_GET_OFF_NOPE") for hour in list(range(4, 24)) + list(range(0, 4))]


def build_hour_codebook(mode: str, hour_cols: list[tuple[int, str, str]]) -> pd.DataFrame:
    usage_role = (
        "버스 정류장별 시간대 접근성 강도 프록시"
        if mode == "bus"
        else "지하철 역별 시간대 접근성 강도 프록시"
    )
    source_id = BUS_SOURCE_ID if mode == "bus" else SUBWAY_SOURCE_ID
    rows = []
    for hour, on_col, off_col in hour_cols:
        rows.append(
            {
                "시간대": hour,
                "시간대_라벨": f"{hour:02d}시",
                "시간대_그룹": hour_group(hour),
                "승차_원천컬럼": on_col,
                "하차_원천컬럼": off_col,
                "usage_role": usage_role,
                "score_use_warning_ko": "시간대 그룹은 분석 편의용이며 실제 체류시간, 방문목적, 구매시간을 직접 의미하지 않는다.",
                "source_id": source_id,
                "provider": PROVIDER,
                "snapshot_date": SNAPSHOT_DATE,
            }
        )
    return pd.DataFrame(rows)


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


def load_subway_join_audit() -> pd.DataFrame:
    if not SUBWAY_JOIN_AUDIT_PATH.exists():
        raise FileNotFoundError(f"{SUBWAY_JOIN_AUDIT_PATH}가 없습니다. 지하철 역사마스터 전처리를 먼저 실행해야 합니다.")
    return pd.read_csv(SUBWAY_JOIN_AUDIT_PATH, encoding="utf-8-sig", dtype=str).fillna("")


def load_subway_station_master() -> pd.DataFrame:
    if not SUBWAY_STATION_MASTER_PATH.exists():
        raise FileNotFoundError(f"{SUBWAY_STATION_MASTER_PATH}가 없습니다. 지하철 역사마스터 전처리를 먼저 실행해야 합니다.")
    return pd.read_csv(SUBWAY_STATION_MASTER_PATH, encoding="utf-8-sig", dtype=str).fillna("")


def numeric_hour_stats(df: pd.DataFrame, hour_cols: list[tuple[int, str, str]]) -> tuple[int, int, int, int]:
    null_cells = 0
    negative_cells = 0
    fractional_cells = 0
    total = 0
    for _, on_col, off_col in hour_cols:
        on = pd.to_numeric(df[on_col], errors="coerce")
        off = pd.to_numeric(df[off_col], errors="coerce")
        both = on + off
        for series in [on, off, both]:
            null_cells += int(series.isna().sum())
            negative_cells += int((series < 0).sum())
            fractional_cells += int(((series.dropna() % 1) != 0).sum())
        total += int(both.fillna(0).sum())
        df[on_col] = on
        df[off_col] = off
    return null_cells, negative_cells, fractional_cells, total


def add_time_group_columns(summary: pd.DataFrame, df: pd.DataFrame, hour_cols: list[tuple[int, str, str]]) -> pd.DataFrame:
    for group in TIME_GROUPS:
        cols = [on_col for hour, on_col, _ in hour_cols if hour_group(hour) == group] + [
            off_col for hour, _, off_col in hour_cols if hour_group(hour) == group
        ]
        summary[TIME_GROUP_OUTPUT_COLS[group]] = df[cols].sum(axis=1).round().astype("Int64")
    return summary


def key_null_cells(df: pd.DataFrame, key_cols: list[str]) -> int:
    return sum(int((df[col].isna() | df[col].astype(str).str.strip().eq("")).sum()) for col in key_cols)


def duplicate_key_rows(df: pd.DataFrame, key_cols: list[str]) -> int:
    return int(df.duplicated(key_cols).sum())


def write_csv_atomic(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    df.to_csv(tmp, index=False, encoding="utf-8-sig")
    tmp.replace(path)


def append_csv(df: pd.DataFrame, path: Path, *, header: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig", mode="w" if header else "a", header=header)


def reset_output_path(path: Path) -> None:
    if path.exists():
        path.unlink()


def reset_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def archive_legacy_hour_file(path: Path) -> str:
    if not path.exists():
        return ""
    archive_dir = SILVER_DIR / "_legacy_single_file_hour_long"
    archive_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = archive_dir / f"{path.stem}_{timestamp}{path.suffix}"
    path.replace(target)
    return str(target.relative_to(ROOT))


def maybe_write_hour_partition(
    mode: str,
    month: str,
    df: pd.DataFrame,
    hour_cols: list[tuple[int, str, str]],
    output_dir: Path,
    write_hour_partitions: bool,
) -> tuple[str, bool, int]:
    output_path = output_dir / f"기준_월={month}.csv"
    if not write_hour_partitions:
        return str(output_path.relative_to(ROOT)), False, 0
    if output_path.exists():
        output_path.unlink()

    first = True
    if mode == "bus":
        base_cols = BUS_KEY_COLS_SUMMARY + [
            "정류소_ARS_ID_원천",
            "정류장_명_정제",
            "노선정류장_순번",
            "교통수단유형_명",
            "작업_일자",
            "_raw_path",
        ]
        source_grain = "기준_월+노선_번호+정류소_ID+정류소_ARS_ID+원천_정류장명_순번포함+교통수단유형_코드+원천_row_id+시간대"
        directness = "P1_공식_버스노선별_정류장별_시간대_승하차량"
        forbidden = "실제 상권 방문자, 실제 구매자, 전체 버스 수요, 창업 성공확률로 표현 금지"
        notes = "서울 열린데이터광장의 버스노선별 정류장별 시간대 승하차량이다. 접근성 강도 프록시이며, 좌표 결합 상태는 summary의 상태를 따른다."
    else:
        base_cols = ["기준_월", "승하차_호선명", "승하차_역명", "작업_일자", "_raw_path"]
        source_grain = "기준_월+승하차_호선명+승하차_역명+시간대"
        directness = "P1_공식_시간대_승하차량"
        forbidden = "실제 상권 방문자, 실제 구매자, 전체 역세권 수요, 창업 성공확률로 표현 금지"
        notes = "서울 열린데이터광장의 호선별 역별 시간대 승하차량이다. 접근성 강도 프록시이며, 역사 좌표 조인은 summary의 상태를 따른다."

    for hour, on_col, off_col in hour_cols:
        part = df[base_cols + [on_col, off_col]].copy()
        part["시간대"] = hour
        part["시간대_라벨"] = f"{hour:02d}시"
        part["시간대_그룹"] = hour_group(hour)
        part = part.rename(columns={on_col: "승차_인원", off_col: "하차_인원"})
        part["승하차_인원"] = (part["승차_인원"] + part["하차_인원"]).round().astype("Int64")
        part["승차_인원"] = part["승차_인원"].round().astype("Int64")
        part["하차_인원"] = part["하차_인원"].round().astype("Int64")
        part["source_id"] = BUS_SOURCE_ID if mode == "bus" else SUBWAY_SOURCE_ID
        part["provider"] = PROVIDER
        part["source_service"] = BUS_SERVICE if mode == "bus" else SUBWAY_SERVICE
        part["snapshot_date"] = SNAPSHOT_DATE
        part["source_grain"] = source_grain
        part["directness_level"] = directness
        part["forbidden_claim_ko"] = forbidden
        part["notes_ko"] = notes
        append_csv(part, output_path, header=first)
        first = False
    return str(output_path.relative_to(ROOT)), True, output_path.stat().st_size


def prepare_bus_summary(df: pd.DataFrame, page_count: int, api_total: int) -> tuple[pd.DataFrame, list[tuple[int, str, str]], dict[str, int]]:
    hour_cols = bus_hour_columns(df)
    passenger_null, passenger_negative, passenger_fractional, hourly_total = numeric_hour_stats(df, hour_cols)

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
    df["원천_row_id"] = df["_raw_record_id"].astype(str)

    summary = df[
        BUS_KEY_COLS_SUMMARY
        + [
            "정류소_ARS_ID_원천",
            "정류장_명_정제",
            "노선정류장_순번",
            "교통수단유형_명",
            "작업_일자",
            "_raw_path",
        ]
    ].copy()
    on_cols = [on_col for _, on_col, _ in hour_cols]
    off_cols = [off_col for _, _, off_col in hour_cols]
    summary["월_승차_인원"] = df[on_cols].sum(axis=1).round().astype("Int64")
    summary["월_하차_인원"] = df[off_cols].sum(axis=1).round().astype("Int64")
    summary["월_승하차_인원"] = (summary["월_승차_인원"] + summary["월_하차_인원"]).astype("Int64")
    summary = add_time_group_columns(summary, df, hour_cols)

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
    summary["source_id"] = BUS_SOURCE_ID
    summary["provider"] = PROVIDER
    summary["source_service"] = BUS_SERVICE
    summary["snapshot_date"] = SNAPSHOT_DATE
    summary["source_grain"] = "기준_월+노선_번호+정류소_ID+정류소_ARS_ID+원천_정류장명_순번포함+교통수단유형_코드+원천_row_id"
    summary["raw_page_count"] = page_count
    summary["api_list_total_count"] = api_total
    summary["raw_row_count"] = len(df)
    summary["directness_level"] = "P1_공식_버스노선별_정류장별_월간_승하차량_요약"
    summary["forbidden_claim_ko"] = "실제 상권 방문자, 실제 구매자, 전체 버스 수요, 창업 성공확률로 표현 금지"
    summary["notes_ko"] = "시간대별 승하차량을 월간 노선-정류소 단위로 접은 테이블이다. 2021~2022년 일부 순환/반복 노선은 같은 노선+정류소가 복수 row로 나오므로 원천_row_id를 grain에 포함한다. 좌표결합_상태가 exact_match가 아닌 row는 좌표 기반 점수에 직접 투입하지 않는다."
    summary = summary.sort_values(BUS_KEY_COLS_SUMMARY).reset_index(drop=True)

    stats = {
        "passenger_null_cells": passenger_null,
        "passenger_negative_cells": passenger_negative,
        "passenger_fractional_cells": passenger_fractional,
        "hourly_total_passengers": hourly_total,
    }
    return summary, hour_cols, stats


def prepare_subway_summary(df: pd.DataFrame, page_count: int, api_total: int) -> tuple[pd.DataFrame, list[tuple[int, str, str]], dict[str, int]]:
    hour_cols = subway_hour_columns()
    missing_cols = [col for _, on_col, off_col in hour_cols for col in [on_col, off_col] if col not in df.columns]
    if missing_cols:
        raise ValueError(f"지하철 승하차량 시간대 컬럼 누락: {missing_cols}")
    passenger_null, passenger_negative, passenger_fractional, hourly_total = numeric_hour_stats(df, hour_cols)

    df["기준_월"] = df["USE_MM"].astype(str).str.strip()
    df["승하차_호선명"] = df["SBWY_ROUT_LN_NM"].astype(str).str.strip()
    df["승하차_역명"] = df["STTN"].astype(str).str.strip()
    df["작업_일자"] = df["JOB_YMD"].astype(str).str.strip()

    summary = df[["기준_월", "승하차_호선명", "승하차_역명", "작업_일자", "_raw_path"]].copy()
    on_cols = [on_col for _, on_col, _ in hour_cols]
    off_cols = [off_col for _, _, off_col in hour_cols]
    summary["월_승차_인원"] = df[on_cols].sum(axis=1).round().astype("Int64")
    summary["월_하차_인원"] = df[off_cols].sum(axis=1).round().astype("Int64")
    summary["월_승하차_인원"] = (summary["월_승차_인원"] + summary["월_하차_인원"]).astype("Int64")
    summary = add_time_group_columns(summary, df, hour_cols)

    join_audit = load_subway_join_audit()
    join_cols = [
        "승하차_호선명",
        "승하차_역명",
        "exact_match_역사_ID",
        "normalized_candidate_역사_ID",
        "normalized_candidate_호선명",
        "normalized_candidate_역명",
        "match_status",
        "manual_review_required",
    ]
    summary = summary.merge(join_audit[join_cols], on=["승하차_호선명", "승하차_역명"], how="left")
    summary["확정_역사_ID"] = summary["exact_match_역사_ID"].where(summary["match_status"].eq("exact_match"), "")
    summary["후보_역사_ID"] = summary["normalized_candidate_역사_ID"].fillna("")
    summary["좌표결합_상태"] = summary["match_status"].fillna("join_audit_missing")
    summary["좌표결합_주의사항"] = summary["좌표결합_상태"].map(
        {
            "exact_match": "확정 조인 가능",
            "normalized_candidate": "정규화 후보이며 수동검토 전 점수 직접 사용 금지",
            "unmatched_after_candidate": "역사마스터 후보 없음. 수동 매핑 전 좌표 결합 금지",
        }
    ).fillna("조인 audit 누락")

    station_master = load_subway_station_master()
    coord_cols = station_master[["역사_ID", "위도", "경도"]].rename(
        columns={"역사_ID": "후보_역사_ID", "위도": "후보_위도", "경도": "후보_경도"}
    )
    summary = summary.merge(coord_cols, on="후보_역사_ID", how="left")
    summary["source_id"] = SUBWAY_SOURCE_ID
    summary["provider"] = PROVIDER
    summary["source_service"] = SUBWAY_SERVICE
    summary["snapshot_date"] = SNAPSHOT_DATE
    summary["source_grain"] = "기준_월+승하차_호선명+승하차_역명"
    summary["raw_page_count"] = page_count
    summary["api_list_total_count"] = api_total
    summary["raw_row_count"] = len(df)
    summary["directness_level"] = "P1_공식_역별_월간_승하차량_요약"
    summary["forbidden_claim_ko"] = "실제 상권 방문자, 실제 구매자, 전체 역세권 수요, 창업 성공확률로 표현 금지"
    summary["notes_ko"] = "시간대별 승하차량을 월간 역별 요약으로 접은 테이블이다. 좌표 결합 상태가 exact_match가 아닌 row는 알고리즘 점수에 직접 투입하지 않는다."
    summary = summary.sort_values(SUBWAY_KEY_COLS_SUMMARY).reset_index(drop=True)

    stats = {
        "passenger_null_cells": passenger_null,
        "passenger_negative_cells": passenger_negative,
        "passenger_fractional_cells": passenger_fractional,
        "hourly_total_passengers": hourly_total,
    }
    return summary, hour_cols, stats


def process_mode(mode: str, write_hour_partitions: bool) -> tuple[list[MonthResult], list[dict[str, Any]], str]:
    if mode == "bus":
        month_paths = discover_month_paths(BUS_RAW_RELATIVE_PARTS)
        service = BUS_SERVICE
        source_id = BUS_SOURCE_ID
        summary_path = BUS_SUMMARY_PATH
        summary_part_dir = BUS_SUMMARY_PART_DIR
        hour_part_dir = BUS_HOUR_PART_DIR
        hour_manifest_path = BUS_HOUR_MANIFEST_PATH
        legacy_hour_path = BUS_LEGACY_HOUR_PATH
        summary_key_cols = BUS_KEY_COLS_SUMMARY
        prepare_summary = prepare_bus_summary
    elif mode == "subway":
        month_paths = discover_month_paths(SUBWAY_RAW_RELATIVE_PARTS)
        service = SUBWAY_SERVICE
        source_id = SUBWAY_SOURCE_ID
        summary_path = SUBWAY_SUMMARY_PATH
        summary_part_dir = SUBWAY_SUMMARY_PART_DIR
        hour_part_dir = SUBWAY_HOUR_PART_DIR
        hour_manifest_path = SUBWAY_HOUR_MANIFEST_PATH
        legacy_hour_path = SUBWAY_LEGACY_HOUR_PATH
        summary_key_cols = SUBWAY_KEY_COLS_SUMMARY
        prepare_summary = prepare_subway_summary
    else:
        raise ValueError(mode)

    reset_dir(summary_part_dir)
    if write_hour_partitions:
        reset_dir(hour_part_dir)
    summary_tmp_path = summary_path.with_name(summary_path.name + ".tmp")
    reset_output_path(summary_tmp_path)

    month_results: list[MonthResult] = []
    type_codebook_rows: list[pd.DataFrame] = []
    first_summary = True

    for month_path in month_paths:
        raw, page_count, api_total = read_month_pages(month_path, service)
        summary, hour_cols, stats = prepare_summary(raw, page_count, api_total)
        month = month_path.name
        part_summary_path = summary_part_dir / f"기준_월={month}.csv"
        write_csv_atomic(summary, part_summary_path)
        append_csv(summary, summary_tmp_path, header=first_summary)
        first_summary = False

        hour_partition_path, hour_materialized, hour_bytes = maybe_write_hour_partition(
            mode, month, raw, hour_cols, hour_part_dir, write_hour_partitions
        )

        summary_total = int(summary["월_승하차_인원"].sum())
        exact_rows = int(summary["좌표결합_상태"].eq("exact_match").sum())
        review_rows = int(len(summary) - exact_rows)
        dup_summary = duplicate_key_rows(summary, summary_key_cols)
        result = MonthResult(
            mode=mode,
            month=month,
            raw_rows=len(raw),
            api_total=api_total,
            page_count=page_count,
            summary_rows=len(summary),
            expected_long_rows=len(summary) * 24,
            key_null_cells=key_null_cells(summary, summary_key_cols),
            duplicate_summary_key_rows=dup_summary,
            duplicate_long_key_rows_estimate=dup_summary * 24,
            passenger_null_cells=stats["passenger_null_cells"],
            passenger_negative_cells=stats["passenger_negative_cells"],
            passenger_fractional_cells=stats["passenger_fractional_cells"],
            summary_total_passengers=summary_total,
            hourly_total_passengers=stats["hourly_total_passengers"],
            total_diff=abs(summary_total - stats["hourly_total_passengers"]),
            exact_match_rows=exact_rows,
            review_or_unmatched_rows=review_rows,
            summary_path=str(part_summary_path.relative_to(ROOT)),
            hour_partition_path=hour_partition_path,
            hour_partition_materialized=hour_materialized,
            hour_partition_bytes=hour_bytes,
        )
        month_results.append(result)

        if mode == "bus":
            grouped = (
                summary.groupby(["교통수단유형_코드", "교통수단유형_명"], as_index=False)
                .agg(row_count=("기준_월", "count"), 월_승하차_인원=("월_승하차_인원", "sum"))
            )
            type_codebook_rows.append(grouped)

    if mode == "bus":
        # 버스 유형은 점수의 주축이 아니라 해석 보조 분류이므로 코드북으로만 보존한다.
        type_codebook = pd.concat(type_codebook_rows, ignore_index=True)
        type_codebook = (
            type_codebook.groupby(["교통수단유형_코드", "교통수단유형_명"], as_index=False)
            .agg(row_count=("row_count", "sum"), 월_승하차_인원=("월_승하차_인원", "sum"))
            .sort_values("row_count", ascending=False)
        )
        type_codebook["usage_role"] = "버스 유형별 접근성 보조 해석"
        type_codebook["score_use_warning_ko"] = "버스 유형은 접근성 보조 분류이며 실제 상권 방문목적을 의미하지 않는다."
        type_codebook["source_id"] = BUS_SOURCE_ID
        type_codebook["provider"] = PROVIDER
        type_codebook["snapshot_date"] = SNAPSHOT_DATE
        write_csv_atomic(type_codebook, BUS_TYPE_CODEBOOK_PATH)

    hour_codebook = build_hour_codebook(mode, bus_hour_columns(raw) if mode == "bus" else subway_hour_columns())
    write_csv_atomic(hour_codebook, BUS_HOUR_CODEBOOK_PATH if mode == "bus" else SUBWAY_HOUR_CODEBOOK_PATH)

    # 모든 월 처리가 끝난 뒤에만 기존 summary를 교체한다.
    # 중간 실패 시 이전 summary 파일을 남겨두기 위한 안전장치다.
    summary_tmp_path.replace(summary_path)

    archived_path = archive_legacy_hour_file(legacy_hour_path)
    manifest_rows = []
    for item in month_results:
        manifest_rows.append(
            {
                "mode": item.mode,
                "기준_월": item.month,
                "expected_long_rows": item.expected_long_rows,
                "hour_partition_materialized": item.hour_partition_materialized,
                "hour_partition_path": item.hour_partition_path,
                "hour_partition_bytes": item.hour_partition_bytes,
                "source_id": source_id,
                "provider": PROVIDER,
                "source_service": service,
                "notes_ko": "기본 정책은 단일 대형 hour-long CSV 금지다. 필요 시 월별 파티션을 물리화하고, 점수 엔진은 summary 파일을 읽는다.",
            }
        )
    write_csv_atomic(pd.DataFrame(manifest_rows), hour_manifest_path)

    return month_results, manifest_rows, archived_path


def result_dicts(results: Iterable[MonthResult]) -> list[dict[str, Any]]:
    return [item.__dict__.copy() for item in results]


def build_validation(bus_results: list[MonthResult], subway_results: list[MonthResult], archived: dict[str, str]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []

    def add(rule_name: str, observed: object, expected: object, result: str, reason_ko: str) -> None:
        rows.append(
            {
                "validation_id": len(rows) + 1,
                "rule_name": rule_name,
                "observed": observed,
                "expected": expected,
                "result": result,
                "reason_ko": reason_ko,
            }
        )

    all_results = bus_results + subway_results
    bus_months = sorted({item.month for item in bus_results})
    subway_months = sorted({item.month for item in subway_results})
    required_months = [f"{year}{month:02d}" for year in range(2021, 2026) for month in range(1, 13)]
    required_set = set(required_months)
    bus_set = set(bus_months)
    subway_set = set(subway_months)
    bus_missing = sorted(required_set - bus_set)
    subway_missing = sorted(required_set - subway_set)
    bus_extra = sorted(bus_set - required_set)
    subway_extra = sorted(subway_set - required_set)

    add(
        "버스 raw 이력 월 커버리지",
        f"required_covered={not bus_missing}, count={len(bus_months)}, extra={bus_extra}, missing={bus_missing}",
        "202101~202512 60개월 필수, 추가 최신월은 보존",
        "PASS" if not bus_missing and len(bus_months) >= 60 else "FAIL",
        "백데이터 검증 기간과 맞추려면 2021~2025 월별 승하차량이 빠지면 안 된다.",
    )
    add(
        "지하철 raw 이력 월 커버리지",
        f"required_covered={not subway_missing}, count={len(subway_months)}, extra={subway_extra}, missing={subway_missing}",
        "202101~202512 60개월 필수, 추가 최신월은 보존",
        "PASS" if not subway_missing and len(subway_months) >= 60 else "FAIL",
        "접근성 축을 시간에 따라 검증하려면 지하철도 버스와 같은 기간 커버리지를 가져야 한다.",
    )
    add(
        "summary row와 API 총량 일치",
        {
            "bus_summary_rows": sum(item.summary_rows for item in bus_results),
            "bus_api_total": sum(item.api_total for item in bus_results),
            "subway_summary_rows": sum(item.summary_rows for item in subway_results),
            "subway_api_total": sum(item.api_total for item in subway_results),
        },
        "mode별 summary_rows == api_total",
        "PASS"
        if all(item.summary_rows == item.api_total and item.raw_rows == item.api_total for item in all_results)
        else "FAIL",
        "원천 row 하나가 월별 summary row 하나로 보존되어야 raw 누락이나 임의 집계가 없다고 볼 수 있다.",
    )
    add(
        "24시간 컬럼 총량 보존",
        {
            "max_total_diff": max(item.total_diff for item in all_results),
            "bus_total": sum(item.summary_total_passengers for item in bus_results),
            "subway_total": sum(item.summary_total_passengers for item in subway_results),
        },
        "월 summary 총량 == 24시간 승하차 총량",
        "PASS" if all(item.total_diff == 0 for item in all_results) else "FAIL",
        "hour-long 단일 파일을 만들지 않아도 24시간 컬럼이 summary와 시간대 그룹 산출에 모두 반영됐는지 확인한다.",
    )
    add(
        "승하차 값 품질",
        {
            "null_cells": sum(item.passenger_null_cells for item in all_results),
            "negative_cells": sum(item.passenger_negative_cells for item in all_results),
            "fractional_cells": sum(item.passenger_fractional_cells for item in all_results),
        },
        "null=0, negative=0, fractional=0",
        "PASS"
        if all(
            item.passenger_null_cells == 0
            and item.passenger_negative_cells == 0
            and item.passenger_fractional_cells == 0
            for item in all_results
        )
        else "FAIL",
        "승하차량은 공식 집계 인원수이므로 음수, 결측, 소수값이 있으면 점수화 전에 보류해야 한다.",
    )
    add(
        "summary grain 중복과 key 결측",
        {
            "key_null_cells": sum(item.key_null_cells for item in all_results),
            "duplicate_summary_key_rows": sum(item.duplicate_summary_key_rows for item in all_results),
            "duplicate_long_key_rows_estimate": sum(item.duplicate_long_key_rows_estimate for item in all_results),
        },
        "key_null=0, duplicate=0",
        "PASS" if all(item.key_null_cells == 0 and item.duplicate_summary_key_rows == 0 for item in all_results) else "FAIL",
        "규칙 엔진 조인 키는 이름이 아니라 코드/공간 grain이다. 버스는 2021~2022년 일부 반복 노선에서 같은 노선+정류소가 복수 row로 나오므로 원천_row_id까지 포함해 원천 row를 잃지 않는다.",
    )
    add(
        "좌표 조인 상태 분리",
        {
            "bus_exact_rows": sum(item.exact_match_rows for item in bus_results),
            "bus_review_rows": sum(item.review_or_unmatched_rows for item in bus_results),
            "subway_exact_rows": sum(item.exact_match_rows for item in subway_results),
            "subway_review_rows": sum(item.review_or_unmatched_rows for item in subway_results),
        },
        "exact과 review/unmatched를 버리지 않고 분리",
        "PASS" if all(item.exact_match_rows > 0 for item in all_results) else "FAIL",
        "좌표가 없는 row도 원천으로 보존하되, 거리감쇠 접근성 점수에는 exact_match만 직접 투입해야 한다.",
    )
    add(
        "hour-long 단일 대형 파일 제거와 매니페스트 작성",
        {
            "bus_manifest": str(BUS_HOUR_MANIFEST_PATH.relative_to(ROOT)),
            "subway_manifest": str(SUBWAY_HOUR_MANIFEST_PATH.relative_to(ROOT)),
            "archived_legacy": archived,
        },
        "단일 long CSV 대신 월별 파티션 계약/매니페스트",
        "PASS" if BUS_HOUR_MANIFEST_PATH.exists() and SUBWAY_HOUR_MANIFEST_PATH.exists() else "FAIL",
        "전처리 산출물을 한 파일에 과도하게 몰아넣지 말라는 원칙에 맞춰 hour-level은 필요 시 월별 파티션으로만 물리화한다.",
    )

    return pd.DataFrame(rows)


def write_report(
    validation: pd.DataFrame,
    bus_results: list[MonthResult],
    subway_results: list[MonthResult],
    archived: dict[str, str],
    write_hour_partitions: bool,
) -> None:
    pass_count = int(validation["result"].eq("PASS").sum())
    fail_count = int(validation["result"].eq("FAIL").sum())
    decision = "PASS" if fail_count == 0 else "FAIL"
    bus_total_rows = sum(item.summary_rows for item in bus_results)
    subway_total_rows = sum(item.summary_rows for item in subway_results)
    bus_months = sorted({item.month for item in bus_results})
    subway_months = sorted({item.month for item in subway_results})
    bus_review_rows = sum(item.review_or_unmatched_rows for item in bus_results)
    subway_review_rows = sum(item.review_or_unmatched_rows for item in subway_results)

    lines = [
        "# 58차 교통 승하차량 이력 silver 전처리 검증",
        "",
        f"작성시각: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "## 1. 결론",
        "",
        f"- 판정: `{decision}`",
        f"- PASS: {pass_count} / FAIL: {fail_count}",
        f"- 버스 summary: {bus_total_rows:,}행, {bus_months[0]}~{bus_months[-1]}",
        f"- 지하철 summary: {subway_total_rows:,}행, {subway_months[0]}~{subway_months[-1]}",
        f"- hour-long 물리 파티션 생성 여부: `{write_hour_partitions}`",
        "",
        "## 2. 산출물",
        "",
        "| 산출물 | 역할 |",
        "|---|---|",
        f"| `datacorpus/_silver/{BUS_SUMMARY_PATH.name}` | 버스 월별 노선-정류소 승하차량 summary, downstream 접근성 후보 입력 |",
        f"| `datacorpus/_silver/{SUBWAY_SUMMARY_PATH.name}` | 지하철 월별 호선-역 승하차량 summary, downstream 접근성 후보 입력 |",
        f"| `datacorpus/_silver/{BUS_HOUR_MANIFEST_PATH.name}` | 버스 hour-level 파티션 계약과 예상 long row 수 |",
        f"| `datacorpus/_silver/{SUBWAY_HOUR_MANIFEST_PATH.name}` | 지하철 hour-level 파티션 계약과 예상 long row 수 |",
        "| `datacorpus/_rule_validation/58_transit_passenger_history_month_audit.csv` | 월별 row/총량/품질 감사 |",
        "| `datacorpus/_rule_validation/58_transit_passenger_history_silver_validation.csv` | 규칙 검증 결과 |",
        "",
        "## 3. 근거와 처리 원칙",
        "",
        "- 서울 열린데이터광장 승하차량 원천은 공식 시간대별 승하차량이므로 접근성/유입 축의 P1 프록시로만 쓴다.",
        "- 승하차량은 실제 상권 방문자, 실제 구매자, 매장 매출, 창업 성공확률을 직접 의미하지 않는다.",
        "- 엔진 입력에 필요한 것은 월별 summary와 좌표 조인 상태다. 시간대 long은 점수 입력의 주 테이블이 아니라 추적/감사용 보조 구조다.",
        "- 그래서 `summary.csv`는 기존 파일명을 유지해 downstream 호환을 보존하고, hour-level은 단일 대형 CSV 대신 매니페스트와 선택 파티션으로 관리한다.",
        "",
        "## 4. 검증 결과",
        "",
        "| id | rule | result | observed | expected |",
        "|---:|---|---|---|---|",
    ]
    for row in validation.to_dict("records"):
        observed = str(row["observed"]).replace("|", "/")
        expected = str(row["expected"]).replace("|", "/")
        lines.append(f"| {row['validation_id']} | {row['rule_name']} | {row['result']} | {observed} | {expected} |")

    lines.extend(
        [
            "",
            "## 5. 2보 전진 1보 후퇴 기록",
            "",
            "- 전진 1: 2021년 1월부터 2025년 12월까지 버스와 지하철 승하차량 raw를 월별로 모두 읽어 summary를 재생성했다.",
            "- 전진 2: 24시간 승차/하차 컬럼을 모두 합산해 월총량과 시간대 그룹총량을 만들었고, 총량 차이 0을 검증했다.",
            "- 후퇴 1: 단일 hour-long CSV는 검증 편의는 있지만 대용량 전처리 원칙에 맞지 않으므로 기본 산출물에서 제외했다.",
            "- 후퇴 2: 좌표 조인이 안 된 승하차 row는 버리지 않고 보존하되, 거리감쇠 점수에는 직접 투입하지 않는다.",
            "- 후퇴 3: 승하차량은 접근성 강도 프록시일 뿐 실제 방문/구매/성공확률 문구로 확대하지 않는다.",
            "",
            "## 6. 좌표 조인 주의",
            "",
            f"- 버스 review/unmatched row: {bus_review_rows:,}",
            f"- 지하철 review/unmatched row: {subway_review_rows:,}",
            "- 위 row들은 원천 보존 대상이지만 좌표 기반 알고리즘 점수에는 수동 매핑 또는 보수적 제외 규칙이 필요하다.",
            "",
            "## 7. legacy hour-long 처리",
            "",
        ]
    )
    for mode, path in archived.items():
        if path:
            lines.append(f"- {mode}: 기존 단일 hour-long CSV를 `{path}`로 이동했다.")
        else:
            lines.append(f"- {mode}: 이동할 기존 단일 hour-long CSV가 없었다.")

    lines.extend(
        [
            "",
            "## 8. 다음 작업",
            "",
            "1. `preprocess_rule_engine_transit_accessibility_candidates.py`를 새 summary 기준으로 재실행한다.",
            "2. 42/55번 월이력 readiness 검증을 다시 돌려 gold가 새 월 커버리지를 받는지 확인한다.",
            "3. 필요하면 `--write-hour-partitions` 옵션으로 특정 월 또는 전체 월의 hour-level long 파티션을 물리화한다.",
        ]
    )
    report_path = RESEARCH_VALIDATION_DIR / "58_transit_passenger_history_silver_validation_20260707.md"
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="교통 승하차량 full history silver 전처리")
    parser.add_argument(
        "--write-hour-partitions",
        action="store_true",
        help="시간대 long 데이터를 월별 CSV 파티션으로 물리화한다. 기본값은 매니페스트만 작성한다.",
    )
    args = parser.parse_args()

    ensure_dirs()
    bus_results, bus_manifest, archived_bus = process_mode("bus", args.write_hour_partitions)
    subway_results, subway_manifest, archived_subway = process_mode("subway", args.write_hour_partitions)

    archived = {"bus": archived_bus, "subway": archived_subway}
    month_audit = pd.DataFrame(result_dicts(bus_results + subway_results))
    hour_manifest = pd.DataFrame(bus_manifest + subway_manifest)
    validation = build_validation(bus_results, subway_results, archived)

    write_csv_atomic(month_audit, VALIDATION_DIR / "58_transit_passenger_history_month_audit.csv")
    write_csv_atomic(hour_manifest, VALIDATION_DIR / "58_transit_passenger_history_hour_manifest.csv")
    write_csv_atomic(validation, VALIDATION_DIR / "58_transit_passenger_history_silver_validation.csv")
    write_report(validation, bus_results, subway_results, archived, args.write_hour_partitions)

    summary_json = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "decision": "PASS" if int(validation["result"].eq("FAIL").sum()) == 0 else "FAIL",
        "write_hour_partitions": bool(args.write_hour_partitions),
        "bus_summary_rows": int(sum(item.summary_rows for item in bus_results)),
        "subway_summary_rows": int(sum(item.summary_rows for item in subway_results)),
        "bus_month_count": int(len({item.month for item in bus_results})),
        "subway_month_count": int(len({item.month for item in subway_results})),
        "archived_legacy_hour_files": archived,
        "outputs": [
            str(BUS_SUMMARY_PATH.relative_to(ROOT)),
            str(SUBWAY_SUMMARY_PATH.relative_to(ROOT)),
            str(BUS_HOUR_MANIFEST_PATH.relative_to(ROOT)),
            str(SUBWAY_HOUR_MANIFEST_PATH.relative_to(ROOT)),
            "datacorpus/_rule_validation/58_transit_passenger_history_month_audit.csv",
            "datacorpus/_rule_validation/58_transit_passenger_history_hour_manifest.csv",
            "datacorpus/_rule_validation/58_transit_passenger_history_silver_validation.csv",
            "research/rule_validation/58_transit_passenger_history_silver_validation_20260707.md",
        ],
    }
    (VALIDATION_DIR / "58_transit_passenger_history_silver_summary.json").write_text(
        json.dumps(summary_json, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary_json, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
