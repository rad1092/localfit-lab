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

SERVICE = "CardSubwayTime"
RAW_RELATIVE_PARTS = ("seoul_open_data", "transport", "subway_station_passengers_hourly")
JOIN_AUDIT_PATH = VALIDATION_DIR / "08_subway_station_master_passenger_join_audit.csv"
STATION_MASTER_PATH = SILVER_DIR / "silver_subway_station_master.csv"
PROGRESS_PATH = ROOT / "research" / "전처리_진행기록_20260703.md"
PRECHECK_PATH = ROOT / "research" / "전처리_전_확인사항_20260703.md"

SNAPSHOT_DATE = "2026-07-03"
PROVIDER = "서울열린데이터광장"
SOURCE_ID = "seoul_subway_station_passengers_hourly"
STATION_MASTER_SOURCE_ID = "seoul_subway_station_master"
KEY_COLS_SUMMARY = ["기준_월", "승하차_호선명", "승하차_역명"]
KEY_COLS_LONG = ["기준_월", "승하차_호선명", "승하차_역명", "시간대"]


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
        raise FileNotFoundError("지하철 승하차량 YYYYMM 월 폴더를 찾지 못했습니다.")
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


def hour_columns() -> list[tuple[int, str, str]]:
    cols: list[tuple[int, str, str]] = []
    for hour in list(range(4, 24)) + list(range(0, 4)):
        cols.append((hour, f"HR_{hour}_GET_ON_NOPE", f"HR_{hour}_GET_OFF_NOPE"))
    return cols


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


def build_hour_codebook() -> pd.DataFrame:
    rows = []
    for hour, on_col, off_col in hour_columns():
        rows.append(
            {
                "시간대": hour,
                "시간대_라벨": f"{hour:02d}시",
                "시간대_그룹": hour_group(hour),
                "승차_원천컬럼": on_col,
                "하차_원천컬럼": off_col,
                "usage_role": "지하철 시간대별 접근성 강도 프록시",
                "score_use_warning_ko": "시간대 그룹은 분석 편의용이며 실제 체류시간이나 방문목적을 의미하지 않는다.",
                "source_id": SOURCE_ID,
                "provider": PROVIDER,
                "snapshot_date": SNAPSHOT_DATE,
            }
        )
    return pd.DataFrame(rows)


def load_join_audit() -> pd.DataFrame:
    if not JOIN_AUDIT_PATH.exists():
        raise FileNotFoundError(f"{JOIN_AUDIT_PATH}가 없습니다. 역사마스터 전처리를 먼저 실행해야 합니다.")
    return pd.read_csv(JOIN_AUDIT_PATH, encoding="utf-8-sig", dtype=str).fillna("")


def load_station_master() -> pd.DataFrame:
    if not STATION_MASTER_PATH.exists():
        raise FileNotFoundError(f"{STATION_MASTER_PATH}가 없습니다. 역사마스터 전처리를 먼저 실행해야 합니다.")
    return pd.read_csv(STATION_MASTER_PATH, encoding="utf-8-sig", dtype=str).fillna("")


def build_summary_and_long() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, int, int]:
    raw, page_count, api_total = read_openapi_pages()
    df = raw.copy()
    df["기준_월"] = df["USE_MM"].astype(str).str.strip()
    df["승하차_호선명"] = df["SBWY_ROUT_LN_NM"].astype(str).str.strip()
    df["승하차_역명"] = df["STTN"].astype(str).str.strip()
    df["작업_일자"] = df["JOB_YMD"].astype(str).str.strip()

    missing_cols = [col for _, on_col, off_col in hour_columns() for col in [on_col, off_col] if col not in df.columns]
    if missing_cols:
        raise ValueError(f"지하철 승하차량 시간대 컬럼 누락: {missing_cols}")

    passenger_cols = [col for _, on_col, off_col in hour_columns() for col in [on_col, off_col]]
    for col in passenger_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    long_rows: list[pd.DataFrame] = []
    for hour, on_col, off_col in hour_columns():
        part = df[["기준_월", "승하차_호선명", "승하차_역명", "작업_일자", "_raw_path", on_col, off_col]].copy()
        part["시간대"] = hour
        part["시간대_라벨"] = f"{hour:02d}시"
        part["시간대_그룹"] = hour_group(hour)
        part = part.rename(columns={on_col: "승차_인원", off_col: "하차_인원"})
        part["승하차_인원"] = part["승차_인원"] + part["하차_인원"]
        long_rows.append(part)
    long_df = pd.concat(long_rows, ignore_index=True)
    for col in ["승차_인원", "하차_인원", "승하차_인원"]:
        long_df[col] = long_df[col].round().astype("Int64")
    long_df["source_id"] = SOURCE_ID
    long_df["provider"] = PROVIDER
    long_df["source_service"] = SERVICE
    long_df["snapshot_date"] = SNAPSHOT_DATE
    long_df["source_grain"] = "기준_월+승하차_호선명+승하차_역명+시간대"
    long_df["raw_page_count"] = page_count
    long_df["api_list_total_count"] = api_total
    long_df["raw_row_count"] = len(df)
    long_df["directness_level"] = "P1_공식_시간대_승하차량"
    long_df["forbidden_claim_ko"] = "실제 상권 방문자, 실제 구매자, 전체 역세권 수요, 창업 성공확률로 표현 금지"
    long_df["notes_ko"] = "서울 열린데이터광장의 호선별 역별 시간대 승하차량이다. 상권 접근성 강도 프록시로 쓰며, 역사 좌표 조인은 별도 audit 상태를 따른다."

    summary = (
        long_df.groupby(["기준_월", "승하차_호선명", "승하차_역명", "작업_일자", "_raw_path"], as_index=False)
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

    join_audit = load_join_audit()
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

    station_master = load_station_master()
    coord_cols = station_master[["역사_ID", "위도", "경도"]].rename(
        columns={"역사_ID": "후보_역사_ID", "위도": "후보_위도", "경도": "후보_경도"}
    )
    summary = summary.merge(coord_cols, on="후보_역사_ID", how="left")
    summary["source_id"] = SOURCE_ID
    summary["provider"] = PROVIDER
    summary["source_service"] = SERVICE
    summary["snapshot_date"] = SNAPSHOT_DATE
    summary["source_grain"] = "기준_월+승하차_호선명+승하차_역명"
    summary["raw_page_count"] = page_count
    summary["api_list_total_count"] = api_total
    summary["raw_row_count"] = len(df)
    summary["directness_level"] = "P1_공식_역별_월간_승하차량_요약"
    summary["forbidden_claim_ko"] = "실제 상권 방문자, 실제 구매자, 전체 역세권 수요, 창업 성공확률로 표현 금지"
    summary["notes_ko"] = "시간대별 승하차량을 월간 역별 요약으로 접은 테이블이다. 좌표 결합 상태가 exact_match가 아닌 row는 알고리즘 점수에 직접 투입하지 않는다."

    return summary, long_df.sort_values(KEY_COLS_LONG).reset_index(drop=True), build_hour_codebook(), page_count, api_total


def key_null_cells(df: pd.DataFrame, key_cols: list[str]) -> int:
    return sum(int((df[col].isna() | df[col].astype(str).str.strip().eq("")).sum()) for col in key_cols)


def duplicate_key_rows(df: pd.DataFrame, key_cols: list[str]) -> int:
    return int(df.duplicated(key_cols).sum())


def validate_subway_passengers(
    summary: pd.DataFrame,
    long_df: pd.DataFrame,
    hour_codebook: pd.DataFrame,
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
    normalized_count = int(match_counts.get("normalized_candidate", 0))
    unmatched_count = int(match_counts.get("unmatched_after_candidate", 0))

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
    judgement = "FAIL" if hard_fail else ("조건부 PASS" if normalized_count or unmatched_count else "PASS")

    domain_df = pd.DataFrame(
        [
            {
                "table": "silver_subway_passenger_station_month_summary",
                "rows": len(summary),
                "api_total_count": api_total,
                "raw_page_count": page_count,
                "row_count_matches_api": len(summary) == api_total,
                "month_min": summary["기준_월"].min(),
                "month_max": summary["기준_월"].max(),
                "route_count": summary["승하차_호선명"].nunique(),
                "station_name_count": summary["승하차_역명"].nunique(),
                "key_null_cells": key_null_summary,
                "duplicate_key_rows": dup_summary,
                "passenger_null_cells": "",
                "passenger_negative_cells": "",
                "passenger_fractional_cells": "",
                "summary_total_passengers": summary_total,
                "long_total_passengers": long_total,
                "total_diff": total_diff,
                "exact_match_rows": exact_count,
                "normalized_candidate_rows": normalized_count,
                "unmatched_after_candidate_rows": unmatched_count,
                "judgement": judgement,
                "conditional_reason_ko": "좌표 결합이 exact_match가 아닌 승하차량 row가 있어 수동 매핑 전 접근성 점수 직접 사용을 제한함" if judgement == "조건부 PASS" else "",
            },
            {
                "table": "silver_subway_passenger_station_month_hour",
                "rows": len(long_df),
                "api_total_count": api_total * 24,
                "raw_page_count": page_count,
                "row_count_matches_api": len(long_df) == api_total * 24,
                "month_min": long_df["기준_월"].min(),
                "month_max": long_df["기준_월"].max(),
                "route_count": long_df["승하차_호선명"].nunique(),
                "station_name_count": long_df["승하차_역명"].nunique(),
                "key_null_cells": key_null_long,
                "duplicate_key_rows": dup_long,
                "passenger_null_cells": null_passenger_cells,
                "passenger_negative_cells": negative_passenger_cells,
                "passenger_fractional_cells": fractional_passenger_cells,
                "summary_total_passengers": summary_total,
                "long_total_passengers": long_total,
                "total_diff": total_diff,
                "exact_match_rows": "",
                "normalized_candidate_rows": "",
                "unmatched_after_candidate_rows": "",
                "judgement": judgement,
                "conditional_reason_ko": "원천의 시간대 값을 모두 long 형식으로 보존했으며 좌표 결합은 summary의 상태를 따른다." if judgement != "FAIL" else "",
            },
            {
                "table": "silver_subway_passenger_hour_codebook",
                "rows": len(hour_codebook),
                "api_total_count": 24,
                "raw_page_count": "",
                "row_count_matches_api": len(hour_codebook) == 24,
                "month_min": "",
                "month_max": "",
                "route_count": "",
                "station_name_count": "",
                "key_null_cells": key_null_cells(hour_codebook, ["시간대"]),
                "duplicate_key_rows": duplicate_key_rows(hour_codebook, ["시간대"]),
                "passenger_null_cells": "",
                "passenger_negative_cells": "",
                "passenger_fractional_cells": "",
                "summary_total_passengers": "",
                "long_total_passengers": "",
                "total_diff": "",
                "exact_match_rows": "",
                "normalized_candidate_rows": "",
                "unmatched_after_candidate_rows": "",
                "judgement": "PASS",
                "conditional_reason_ko": "",
            },
        ]
    )
    grain_df = pd.DataFrame(
        [
            {
                "table": "silver_subway_passenger_station_month_summary",
                "key_cols": " + ".join(KEY_COLS_SUMMARY),
                "duplicate_key_rows": dup_summary,
                "key_null_cells": key_null_summary,
                "judgement": "PASS" if dup_summary == 0 and key_null_summary == 0 else "FAIL",
                "reason_ko": "승하차량 원천은 역사_ID가 없으므로 기준월+호선명+역명 grain을 먼저 보존한다.",
            },
            {
                "table": "silver_subway_passenger_station_month_hour",
                "key_cols": " + ".join(KEY_COLS_LONG),
                "duplicate_key_rows": dup_long,
                "key_null_cells": key_null_long,
                "judgement": "PASS" if dup_long == 0 and key_null_long == 0 else "FAIL",
                "reason_ko": "시간대별 승차/하차 컬럼을 24시간 long grain으로 풀어 시간대 수요 패턴을 잃지 않는다.",
            },
            {
                "table": "silver_subway_passenger_hour_codebook",
                "key_cols": "시간대",
                "duplicate_key_rows": duplicate_key_rows(hour_codebook, ["시간대"]),
                "key_null_cells": key_null_cells(hour_codebook, ["시간대"]),
                "judgement": "PASS",
                "reason_ko": "시간대와 분석 편의용 그룹을 분리해 알고리즘 주석과 리포트에 재사용한다.",
            },
        ]
    )
    join_status_df = (
        summary["좌표결합_상태"]
        .value_counts(dropna=False)
        .rename_axis("좌표결합_상태")
        .reset_index(name="row_count")
    )
    join_status_df["usage_decision_ko"] = join_status_df["좌표결합_상태"].map(
        {
            "exact_match": "좌표 결합 가능",
            "normalized_candidate": "수동 검토 전 점수 직접 사용 금지",
            "unmatched_after_candidate": "수동 매핑 전 좌표 결합 금지",
        }
    ).fillna("확인 필요")
    join_status_df["source_id"] = SOURCE_ID
    join_status_df["station_master_source_id"] = STATION_MASTER_SOURCE_ID
    contract_df = pd.DataFrame(
        [
            {
                "table": "silver_subway_passenger_station_month_summary",
                "source_id": SOURCE_ID,
                "provider": PROVIDER,
                "source_service": SERVICE,
                "rows": len(summary),
                "contract_status": judgement,
                "usage_role": "월별 역·호선 승하차량 요약과 좌표 결합 상태 관리",
            },
            {
                "table": "silver_subway_passenger_station_month_hour",
                "source_id": SOURCE_ID,
                "provider": PROVIDER,
                "source_service": SERVICE,
                "rows": len(long_df),
                "contract_status": judgement,
                "usage_role": "시간대별 승하차 접근성 강도 프록시",
            },
            {
                "table": "silver_subway_passenger_hour_codebook",
                "source_id": SOURCE_ID,
                "provider": PROVIDER,
                "source_service": SERVICE,
                "rows": len(hour_codebook),
                "contract_status": "PASS",
                "usage_role": "시간대 그룹과 원천 컬럼 해석",
            },
        ]
    )
    return domain_df, grain_df, join_status_df, contract_df


def write_validation_md(
    domain_df: pd.DataFrame,
    grain_df: pd.DataFrame,
    join_status_df: pd.DataFrame,
    hour_codebook: pd.DataFrame,
    summary: pd.DataFrame,
) -> None:
    path = RESEARCH_VALIDATION_DIR / "09_subway_passenger_silver_validation_20260703.md"
    main = domain_df.loc[domain_df["table"].eq("silver_subway_passenger_station_month_summary")].iloc[0].to_dict()
    hour = domain_df.loc[domain_df["table"].eq("silver_subway_passenger_station_month_hour")].iloc[0].to_dict()
    top_rows = summary.sort_values("월_승하차_인원", ascending=False).head(10)
    source_month_text = ", ".join(sorted(summary["기준_월"].astype(str).unique()))
    summary_rows = int(main["rows"])
    long_rows = int(hour["rows"])
    raw_page_count = int(main["raw_page_count"])
    lines = [
        "# 9차 지하철 승하차량 silver 전처리 검증",
        "",
        f"작성시각: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "## 대상 파일",
        "",
        "- `datacorpus/_silver/silver_subway_passenger_station_month_summary.csv`",
        "- `datacorpus/_silver/silver_subway_passenger_station_month_hour.csv`",
        "- `datacorpus/_silver/silver_subway_passenger_hour_codebook.csv`",
        "",
        "## 사용 근거",
        "",
        "- `datacorpus/_raw_ingest/source_registry.csv`: 지하철 승하차량은 시간대 수요와 접근성/유입 검증용 P1 원천으로 등록되어 있다.",
        f"- `datacorpus/_raw_ingest/run_logs/20260703_transport_accessibility_sources_ko.md`: `CardSubwayTime` 원천 수집 기록을 기준으로 현재 읽은 월 {source_month_text}, summary {summary_rows:,}건 / raw page {raw_page_count:,}페이지를 검증 대상으로 삼았다.",
        "- `research/algorithm_evidence_sources/data_docs/seoul_open_data_subway_station_passengers_hourly_OA-12252.html`: 지하철 호선별 역별 시간대별 승하차인원이며 매월 5일 전월 데이터를 갱신한다고 설명한다.",
        "- `research/rule_validation/08_subway_station_master_silver_validation_20260703.md`: 역사마스터와 승하차량의 exact 조인 미매칭 70건, 정규화 후보 후 미매칭 6건이 확인되어 있다.",
        "- `research/전처리_알고리즘_실행계획_20260703.md`: 접근성/유입 축은 역 개수만이 아니라 시간대 승하차량과 좌표 결합을 목표로 한다.",
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
            f"판단: 원천 {summary_rows:,}건을 월별 요약으로 보존했고, 시간대별 long 테이블은 {summary_rows:,}×24={long_rows:,}행으로 풀었다.",
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
            "",
            "판단: 시간대별 승차/하차 값에는 null과 음수가 없고, 요약 총량과 long 총량이 일치한다.",
            "",
            "## 검증 4: 역사마스터 좌표 결합 상태",
            "",
            "| 좌표결합_상태 | row_count | 사용 판단 |",
            "|---|---:|---|",
        ]
    )
    for row in join_status_df.to_dict("records"):
        lines.append(f"| {row['좌표결합_상태']} | {row['row_count']} | {row['usage_decision_ko']} |")

    lines.extend(
        [
            "",
            "판단: exact_match가 아닌 row를 버리지 않고 보존했다. 다만 `normalized_candidate`와 `unmatched_after_candidate`는 수동 매핑 전 좌표 기반 점수에 직접 넣지 않는다.",
            "",
            "## 검증 5: 시간대 코드북",
            "",
            "| 시간대 | 라벨 | 그룹 |",
            "|---:|---|---|",
        ]
    )
    for row in hour_codebook.to_dict("records"):
        lines.append(f"| {row['시간대']} | {row['시간대_라벨']} | {row['시간대_그룹']} |")

    lines.extend(
        [
            "",
            "## 참고: 월 승하차량 상위 10개 row",
            "",
            "| 기준_월 | 호선 | 역명 | 월_승하차_인원 | 좌표결합_상태 |",
            "|---|---|---|---:|---|",
        ]
    )
    for row in top_rows.to_dict("records"):
        lines.append(
            f"| {row['기준_월']} | {row['승하차_호선명']} | {row['승하차_역명']} | {row['월_승하차_인원']} | {row['좌표결합_상태']} |"
        )

    lines.extend(
        [
            "",
            "## 2보 전진 1보 후퇴 기록",
            "",
            f"- 전진 1: {source_month_text} 지하철 승하차량 {summary_rows:,}건을 월별 요약과 24시간 long 테이블로 모두 보존했다.",
            "- 전진 2: 역사마스터 조인 audit를 결합해 좌표 사용 가능 row와 수동 검토 row를 분리했다.",
            "- 후퇴 1: 승하차량은 역 이용량이지 해당 상권 방문자나 구매자가 아니다.",
            "- 후퇴 2: exact_match가 아닌 row는 좌표 기반 접근성 점수에 직접 투입하지 않는다.",
            "- 후퇴 3: 시간대 그룹은 분석 편의용이며 체류시간, 방문목적, 업종 구매시간을 직접 의미하지 않는다.",
            "",
            "## 알고리즘 단계에서 금지할 표현",
            "",
            "- 실제 상권 방문자",
            "- 실제 구매자",
            "- 전체 역세권 수요",
            "- 창업 성공확률",
            "",
            "허용 표현:",
            "",
            "- 시간대 승하차량 기반 접근성 강도 프록시",
            "- 역 좌표 결합 가능 여부가 표시된 교통 수요 원천",
            "- 호선+역명 기준 월별 승하차량",
            "",
            "## 다음 작업",
            "",
            "1. 버스 정류장별 시간대 승하차량 silver 전처리.",
            "2. 버스정류소 좌표와 승하차량 조인 audit.",
            "3. 교통 접근성 gold 후보는 exact 좌표 결합 row부터 보수적으로 산출.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def append_progress(domain_df: pd.DataFrame) -> None:
    if not PROGRESS_PATH.exists():
        return
    summary = domain_df.loc[domain_df["table"].eq("silver_subway_passenger_station_month_summary")].iloc[0].to_dict()
    hour = domain_df.loc[domain_df["table"].eq("silver_subway_passenger_station_month_hour")].iloc[0].to_dict()
    codebook = domain_df.loc[domain_df["table"].eq("silver_subway_passenger_hour_codebook")].iloc[0].to_dict()
    block = [
        "",
        "---",
        "",
        "## 11. 완료: 지하철 승하차량 silver 테이블",
        "",
        "| 산출물 | row 수 | 상태 | 역할 |",
        "|---|---:|---|---|",
        f"| `datacorpus/_silver/silver_subway_passenger_station_month_summary.csv` | {summary['rows']:,} | {summary['judgement']} | 월별 역·호선 승하차량 요약 |",
        f"| `datacorpus/_silver/silver_subway_passenger_station_month_hour.csv` | {hour['rows']:,} | {hour['judgement']} | 시간대별 승하차량 long 테이블 |",
        f"| `datacorpus/_silver/silver_subway_passenger_hour_codebook.csv` | {codebook['rows']:,} | {codebook['judgement']} | 시간대 그룹 코드북 |",
        "",
        "검증 근거:",
        "",
        "- `datacorpus/_rule_validation/09_subway_passenger_domain_validation.csv`",
        "- `datacorpus/_rule_validation/09_subway_passenger_grain_validation.csv`",
        "- `datacorpus/_rule_validation/09_subway_passenger_join_status.csv`",
        "- `datacorpus/_rule_validation/09_subway_passenger_source_contract.csv`",
        "- `research/rule_validation/09_subway_passenger_silver_validation_20260703.md`",
        "",
        "판단:",
        "",
        f"- 원천 {int(summary['rows']):,}건과 summary row 수가 일치한다.",
        f"- 시간대 long 테이블은 {int(summary['rows']):,}×24={int(hour['rows']):,}행이며 시간대별 값을 잃지 않는다.",
        f"- 좌표 exact_match {summary['exact_match_rows']}건, normalized_candidate {summary['normalized_candidate_rows']}건, unmatched_after_candidate {summary['unmatched_after_candidate_rows']}건으로 분리했다.",
        "- exact_match가 아닌 row는 좌표 기반 점수에 직접 투입하지 않는다.",
        "- 승하차량은 실제 상권 방문자나 구매자가 아니라 접근성 강도 프록시다.",
    ]
    text = PROGRESS_PATH.read_text(encoding="utf-8")
    marker = "## 11. 완료: 지하철 승하차량 silver 테이블"
    if marker in text:
        text = text.split("\n---\n\n" + marker)[0].rstrip()
    PROGRESS_PATH.write_text(text.rstrip() + "\n" + "\n".join(block) + "\n", encoding="utf-8")


def append_precheck(domain_df: pd.DataFrame) -> None:
    if not PRECHECK_PATH.exists():
        return
    main = domain_df.loc[domain_df["table"].eq("silver_subway_passenger_station_month_summary")].iloc[0].to_dict()
    text = PRECHECK_PATH.read_text(encoding="utf-8")
    marker = "| 지하철 승하차량 |"
    if marker in text:
        return
    target = "| 지하철 역사마스터 | 784건 역사 좌표 silver 생성 완료 | 역명 단독 중복이 129개라 `역사_ID` 또는 `호선_명+역사_명` 기준으로만 조인한다. 승하차량 exact 조인은 미매칭 70개가 있어 별도 매핑 검토가 필요하다. |"
    addition = (
        target
        + "\n"
        + f"| 지하철 승하차량 | {main['rows']:,}건 월별 요약과 {int(main['rows']) * 24:,}건 시간대 long silver 생성 완료 | 시간대 접근성 강도 프록시로 쓰되 exact 좌표 결합 {main['exact_match_rows']}건 외에는 수동 매핑 전 점수 직접 사용을 제한한다. |"
    )
    if target in text:
        text = text.replace(target, addition)
        PRECHECK_PATH.write_text(text, encoding="utf-8")


def main() -> None:
    ensure_dirs()
    summary, long_df, hour_codebook, page_count, api_total = build_summary_and_long()
    domain_df, grain_df, join_status_df, contract_df = validate_subway_passengers(
        summary,
        long_df,
        hour_codebook,
        page_count,
        api_total,
    )
    failed_rules = domain_df.loc[domain_df["judgement"].eq("FAIL"), "table"].astype(str).tolist()
    if failed_rules:
        raise RuntimeError(
            "지하철 승하차량 전처리 검증 FAIL로 silver 저장을 중단합니다. "
            f"부분 raw 또는 원천 불일치 가능성이 있습니다: {failed_rules}"
        )

    summary.to_csv(SILVER_DIR / "silver_subway_passenger_station_month_summary.csv", index=False, encoding="utf-8-sig")
    long_df.to_csv(SILVER_DIR / "silver_subway_passenger_station_month_hour.csv", index=False, encoding="utf-8-sig")
    hour_codebook.to_csv(SILVER_DIR / "silver_subway_passenger_hour_codebook.csv", index=False, encoding="utf-8-sig")
    domain_df.to_csv(VALIDATION_DIR / "09_subway_passenger_domain_validation.csv", index=False, encoding="utf-8-sig")
    grain_df.to_csv(VALIDATION_DIR / "09_subway_passenger_grain_validation.csv", index=False, encoding="utf-8-sig")
    join_status_df.to_csv(VALIDATION_DIR / "09_subway_passenger_join_status.csv", index=False, encoding="utf-8-sig")
    contract_df.to_csv(VALIDATION_DIR / "09_subway_passenger_source_contract.csv", index=False, encoding="utf-8-sig")
    write_validation_md(domain_df, grain_df, join_status_df, hour_codebook, summary)
    append_progress(domain_df)
    append_precheck(domain_df)

    summary_json = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "summary_rows": len(summary),
        "long_rows": len(long_df),
        "hour_codebook_rows": len(hour_codebook),
        "validation_judgements": domain_df[["table", "judgement"]].to_dict("records"),
        "outputs": [
            "datacorpus/_silver/silver_subway_passenger_station_month_summary.csv",
            "datacorpus/_silver/silver_subway_passenger_station_month_hour.csv",
            "datacorpus/_silver/silver_subway_passenger_hour_codebook.csv",
            "datacorpus/_rule_validation/09_subway_passenger_domain_validation.csv",
            "datacorpus/_rule_validation/09_subway_passenger_grain_validation.csv",
            "datacorpus/_rule_validation/09_subway_passenger_join_status.csv",
            "datacorpus/_rule_validation/09_subway_passenger_source_contract.csv",
            "research/rule_validation/09_subway_passenger_silver_validation_20260703.md",
        ],
    }
    (VALIDATION_DIR / "09_subway_passenger_preprocess_summary.json").write_text(
        json.dumps(summary_json, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary_json, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
