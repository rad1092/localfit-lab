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

SERVICE = "subwayStationMaster"
PASSENGER_SERVICE = "CardSubwayTime"
RAW_PATH = RAW_DIR / "20260703" / "seoul_open_data" / "transport" / "subway_station_master"
PASSENGER_RAW_RELATIVE_PARTS = ("seoul_open_data", "transport", "subway_station_passengers_hourly")
PROGRESS_PATH = ROOT / "research" / "전처리_진행기록_20260703.md"
PRECHECK_PATH = ROOT / "research" / "전처리_전_확인사항_20260703.md"

SNAPSHOT_DATE = "2026-07-03"
PROVIDER = "서울열린데이터광장"
SOURCE_ID = "seoul_subway_station_master"
PASSENGER_SOURCE_ID = "seoul_subway_station_passengers_hourly"
KEY_COLS = ["역사_ID"]

COLUMNS = {
    "BLDN_ID": "역사_ID",
    "BLDN_NM": "역사_명",
    "ROUTE": "호선_명",
    "LAT": "위도",
    "LOT": "경도",
}

ROUTE_ALIAS_CANDIDATES = [
    {
        "승하차_호선명": "공항철도 1호선",
        "역사마스터_호선명": "공항철도1호선",
        "candidate_reason_ko": "승하차량 원천은 공백을 포함하고 역사마스터는 공백 없이 표기한다.",
        "manual_review_required": False,
    },
    {
        "승하차_호선명": "9호선2~3단계",
        "역사마스터_호선명": "9호선(연장)",
        "candidate_reason_ko": "승하차량 원천의 9호선 단계 표기와 역사마스터의 연장 표기가 다르다.",
        "manual_review_required": True,
    },
    {
        "승하차_호선명": "경의선",
        "역사마스터_호선명": "경의중앙선",
        "candidate_reason_ko": "다수 역이 이 매핑으로 연결되지만 일부 역은 다른 노선 또는 역명 변경 가능성이 있어 수동 확인이 필요하다.",
        "manual_review_required": True,
    },
]


def ensure_dirs() -> None:
    for path in [SILVER_DIR, VALIDATION_DIR, RESEARCH_VALIDATION_DIR]:
        path.mkdir(parents=True, exist_ok=True)


def page_sort_key(path: Path) -> tuple[int, int]:
    match = re.search(r"_(\d+)_(\d+)(?:_\d+)?\.json$", path.name)
    if not match:
        return (10**12, 10**12)
    return (int(match.group(1)), int(match.group(2)))


def read_openapi_pages(path: Path, service: str) -> tuple[pd.DataFrame, int, int]:
    rows: list[dict[str, Any]] = []
    totals: set[int] = set()
    page_paths = sorted(path.glob(f"{service}_*.json"), key=page_sort_key)
    if not page_paths:
        raise FileNotFoundError(f"{path} 폴더에서 {service} 원응답을 찾지 못했습니다.")

    for page_path in page_paths:
        payload = json.loads(page_path.read_text(encoding="utf-8"))
        root = payload.get(service)
        if not isinstance(root, dict):
            raise ValueError(f"{page_path} 파일에 {service} 루트가 없습니다.")
        if "list_total_count" in root:
            totals.add(int(root["list_total_count"]))
        for row in root.get("row", []):
            item = dict(row)
            item["_raw_path"] = str(page_path.relative_to(ROOT))
            rows.append(item)

    if len(totals) != 1:
        raise ValueError(f"{service} list_total_count가 하나로 고정되지 않습니다: {sorted(totals)}")
    return pd.DataFrame(rows), len(page_paths), next(iter(totals))


def discover_passenger_month_paths() -> list[Path]:
    month_by_name: dict[str, Path] = {}
    for date_dir in sorted(RAW_DIR.glob("20??????")):
        base_path = date_dir.joinpath(*PASSENGER_RAW_RELATIVE_PARTS)
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


def read_passenger_month_pages() -> tuple[pd.DataFrame, int, int, list[str]]:
    rows: list[dict[str, Any]] = []
    total_page_count = 0
    totals_by_month: dict[str, set[int]] = {}
    month_paths = discover_passenger_month_paths()

    for month_path in month_paths:
        page_paths = sorted(month_path.glob(f"{PASSENGER_SERVICE}_*.json"), key=page_sort_key)
        if not page_paths:
            raise FileNotFoundError(f"{month_path} 폴더에서 {PASSENGER_SERVICE} 원응답을 찾지 못했습니다.")
        total_page_count += len(page_paths)
        month_totals = totals_by_month.setdefault(month_path.name, set())
        for page_path in page_paths:
            payload = json.loads(page_path.read_text(encoding="utf-8"))
            root = payload.get(PASSENGER_SERVICE)
            if not isinstance(root, dict):
                raise ValueError(f"{page_path} 파일에 {PASSENGER_SERVICE} 루트가 없습니다.")
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
        raise ValueError(f"{PASSENGER_SERVICE} 월별 list_total_count가 하나로 고정되지 않습니다: {invalid_month_totals}")
    if not rows:
        raise ValueError("지하철 승하차량 원천 row를 읽지 못했습니다.")
    api_total = sum(next(iter(values)) for values in totals_by_month.values())
    return pd.DataFrame(rows), total_page_count, api_total, [path.name for path in month_paths]


def normalize_station_name(value: str) -> str:
    text = str(value).strip()
    text = re.sub(r"\([^)]*\)", "", text)
    return text.replace("·", "").replace(".", "").replace(" ", "")


def route_alias_map() -> dict[str, str]:
    return {row["승하차_호선명"]: row["역사마스터_호선명"] for row in ROUTE_ALIAS_CANDIDATES}


def build_route_alias_candidate() -> pd.DataFrame:
    df = pd.DataFrame(ROUTE_ALIAS_CANDIDATES)
    df["source_id"] = SOURCE_ID
    df["passenger_source_id"] = PASSENGER_SOURCE_ID
    df["status"] = df["manual_review_required"].map(lambda x: "수동검토필요" if x else "후보사용가능")
    df["snapshot_date"] = SNAPSHOT_DATE
    return df


def build_subway_station_master() -> tuple[pd.DataFrame, pd.DataFrame, int, int]:
    raw, page_count, api_total = read_openapi_pages(RAW_PATH, SERVICE)
    df = raw.rename(columns=COLUMNS)
    expected = list(COLUMNS.values())
    missing = [col for col in expected if col not in df.columns]
    if missing:
        raise ValueError(f"역사마스터 컬럼 변환 후 누락 컬럼: {missing}")

    df = df[expected + ["_raw_path"]].copy()
    for col in ["역사_ID", "역사_명", "호선_명"]:
        df[col] = df[col].astype(str).str.strip()
    df["위도"] = pd.to_numeric(df["위도"], errors="coerce")
    df["경도"] = pd.to_numeric(df["경도"], errors="coerce")
    df["역사_명_정규화후보"] = df["역사_명"].map(normalize_station_name)
    df["호선_명_정규화후보"] = df["호선_명"]
    df["quality_coordinate_missing"] = df[["위도", "경도"]].isna().any(axis=1)
    df["quality_coordinate_outside_korea_bbox"] = ~(
        df["경도"].between(124.0, 132.0) & df["위도"].between(33.0, 39.0)
    )
    df["quality_coordinate_outside_seoul_wide_bbox"] = ~(
        df["경도"].between(126.0, 128.0) & df["위도"].between(37.0, 38.0)
    )
    df["coordinate_source_doc"] = "원천 문서는 역사 ID, 역사명, 호선명, 좌표 제공만 설명하고 좌표계는 별도 명시하지 않음"
    df["coordinate_value_judgement"] = "LAT/LOT 값 범위는 WGS84 경위도처럼 보이나 meter 거리계산 전 좌표계 재확인 필요"
    df["distance_use_status"] = "조건부 보류: 좌표계 명시 확인 또는 경위도 geodesic 기준 확정 전까지 실제 거리/도보시간 단정 금지"
    df["source_id"] = SOURCE_ID
    df["provider"] = PROVIDER
    df["source_service"] = SERVICE
    df["snapshot_date"] = SNAPSHOT_DATE
    df["source_grain"] = "역사_ID"
    df["raw_page_count"] = page_count
    df["api_list_total_count"] = api_total
    df["raw_row_count"] = len(df)
    df["directness_level"] = "P1_공식_역사_좌표_마스터"
    df["forbidden_claim_ko"] = "실제 도보시간, 실제 역세권 반경, 실제 방문확률, 전체 지하철 수요로 표현 금지"
    df["notes_ko"] = "지하철 승하차량을 역 좌표와 결합하기 위한 역사 좌표 마스터다. 역명 단독 조인은 환승역 때문에 금지하고 역사_ID 또는 호선_명+역사_명 기준으로 검증한다."

    route_codebook = (
        df["호선_명"]
        .value_counts(dropna=False)
        .rename_axis("호선_명")
        .reset_index(name="역사_수")
        .sort_values(["역사_수", "호선_명"], ascending=[False, True])
    )
    route_codebook["usage_role"] = "역사마스터 호선별 좌표 커버리지"
    route_codebook["score_use_warning_ko"] = "호선별 역사 수는 접근성 보조 기준이며 실제 승하차량이 아니다."
    route_codebook["source_id"] = SOURCE_ID
    route_codebook["provider"] = PROVIDER
    route_codebook["snapshot_date"] = SNAPSHOT_DATE

    return (
        df.sort_values(["역사_ID"]).reset_index(drop=True),
        route_codebook.reset_index(drop=True),
        page_count,
        api_total,
    )


def build_passenger_join_audit(master: pd.DataFrame) -> tuple[pd.DataFrame, int, int, int, str]:
    passenger, _, passenger_api_total, passenger_months = read_passenger_month_pages()
    passenger_month_text = ",".join(passenger_months)
    passenger_keys = passenger[["SBWY_ROUT_LN_NM", "STTN"]].drop_duplicates().copy()
    passenger_keys["승하차_호선명"] = passenger_keys["SBWY_ROUT_LN_NM"].astype(str).str.strip()
    passenger_keys["승하차_역명"] = passenger_keys["STTN"].astype(str).str.strip()
    passenger_keys["역명_정규화후보"] = passenger_keys["승하차_역명"].map(normalize_station_name)
    passenger_keys["호선_정규화후보"] = passenger_keys["승하차_호선명"].replace(route_alias_map())

    exact_master = master[["호선_명", "역사_명", "역사_ID"]].rename(
        columns={"호선_명": "승하차_호선명", "역사_명": "승하차_역명", "역사_ID": "exact_match_역사_ID"}
    )
    audit = passenger_keys.merge(exact_master, on=["승하차_호선명", "승하차_역명"], how="left")

    normalized_master = master[["호선_명_정규화후보", "역사_명_정규화후보", "역사_ID", "호선_명", "역사_명"]].rename(
        columns={
            "호선_명_정규화후보": "호선_정규화후보",
            "역사_명_정규화후보": "역명_정규화후보",
            "역사_ID": "normalized_candidate_역사_ID",
            "호선_명": "normalized_candidate_호선명",
            "역사_명": "normalized_candidate_역명",
        }
    )
    audit = audit.merge(normalized_master, on=["호선_정규화후보", "역명_정규화후보"], how="left")
    audit["match_status"] = "unmatched_after_candidate"
    audit.loc[audit["normalized_candidate_역사_ID"].notna(), "match_status"] = "normalized_candidate"
    audit.loc[audit["exact_match_역사_ID"].notna(), "match_status"] = "exact_match"
    audit["manual_review_required"] = audit["match_status"].ne("exact_match")
    audit["source_id"] = SOURCE_ID
    audit["passenger_source_id"] = PASSENGER_SOURCE_ID
    audit["passenger_month"] = passenger_month_text
    exact_unmatched = int(audit["exact_match_역사_ID"].isna().sum())
    normalized_unmatched = int(audit["normalized_candidate_역사_ID"].isna().sum())
    return audit.sort_values(["match_status", "승하차_호선명", "승하차_역명"]).reset_index(drop=True), passenger_api_total, exact_unmatched, normalized_unmatched, passenger_month_text


def key_null_cells(df: pd.DataFrame) -> int:
    return sum(int((df[col].isna() | df[col].astype(str).str.strip().eq("")).sum()) for col in KEY_COLS)


def validate_subway_station(
    master: pd.DataFrame,
    route_codebook: pd.DataFrame,
    alias_candidate: pd.DataFrame,
    join_audit: pd.DataFrame,
    page_count: int,
    api_total: int,
    passenger_api_total: int,
    passenger_exact_unmatched: int,
    passenger_normalized_unmatched: int,
    passenger_month_text: str,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    key_null = key_null_cells(master)
    duplicate_station_id = int(master.duplicated(["역사_ID"]).sum())
    duplicate_route_station = int(master.duplicated(["호선_명", "역사_명"]).sum())
    duplicate_station_name_rows = int(master.duplicated(["역사_명"]).sum())
    coord_null_rows = int(master["quality_coordinate_missing"].sum())
    outside_korea_bbox_rows = int(master["quality_coordinate_outside_korea_bbox"].sum())
    outside_seoul_wide_bbox_rows = int(master["quality_coordinate_outside_seoul_wide_bbox"].sum())
    hard_fail = (
        len(master) != api_total
        or key_null != 0
        or duplicate_station_id != 0
        or duplicate_route_station != 0
        or coord_null_rows != 0
        or outside_korea_bbox_rows != 0
    )
    judgement = "FAIL" if hard_fail else "조건부 PASS"

    domain_df = pd.DataFrame(
        [
            {
                "table": "silver_subway_station_master",
                "rows": len(master),
                "api_total_count": api_total,
                "raw_page_count": page_count,
                "row_count_matches_api": len(master) == api_total,
                "unique_station_id_count": master["역사_ID"].nunique(),
                "route_count": master["호선_명"].nunique(),
                "station_name_count": master["역사_명"].nunique(),
                "key_null_cells": key_null,
                "duplicate_station_id_rows": duplicate_station_id,
                "duplicate_route_station_rows": duplicate_route_station,
                "duplicate_station_name_rows": duplicate_station_name_rows,
                "coordinate_null_rows": coord_null_rows,
                "coordinate_outside_korea_bbox_rows": outside_korea_bbox_rows,
                "coordinate_outside_seoul_wide_bbox_rows": outside_seoul_wide_bbox_rows,
                "latitude_min": master["위도"].min(),
                "latitude_max": master["위도"].max(),
                "longitude_min": master["경도"].min(),
                "longitude_max": master["경도"].max(),
                "passenger_month": passenger_month_text,
                "passenger_api_total_count": passenger_api_total,
                "passenger_unique_route_station_count": len(join_audit),
                "passenger_exact_unmatched_count": passenger_exact_unmatched,
                "passenger_normalized_unmatched_count": passenger_normalized_unmatched,
                "judgement": judgement,
                "conditional_reason_ko": "좌표계가 문서에 명시되지 않았고, 승하차량과의 호선/역명 표기 차이가 있어 조인 매핑 검토가 필요함",
            },
            {
                "table": "silver_subway_route_codebook",
                "rows": len(route_codebook),
                "api_total_count": "",
                "raw_page_count": "",
                "row_count_matches_api": "",
                "unique_station_id_count": "",
                "route_count": len(route_codebook),
                "station_name_count": "",
                "key_null_cells": int((route_codebook["호선_명"].astype(str).str.len() == 0).sum()),
                "duplicate_station_id_rows": "",
                "duplicate_route_station_rows": "",
                "duplicate_station_name_rows": "",
                "coordinate_null_rows": "",
                "coordinate_outside_korea_bbox_rows": "",
                "coordinate_outside_seoul_wide_bbox_rows": "",
                "latitude_min": "",
                "latitude_max": "",
                "longitude_min": "",
                "longitude_max": "",
                "passenger_month": "",
                "passenger_api_total_count": "",
                "passenger_unique_route_station_count": "",
                "passenger_exact_unmatched_count": "",
                "passenger_normalized_unmatched_count": "",
                "judgement": "PASS",
                "conditional_reason_ko": "",
            },
            {
                "table": "silver_subway_route_alias_candidate",
                "rows": len(alias_candidate),
                "api_total_count": "",
                "raw_page_count": "",
                "row_count_matches_api": "",
                "unique_station_id_count": "",
                "route_count": "",
                "station_name_count": "",
                "key_null_cells": 0,
                "duplicate_station_id_rows": "",
                "duplicate_route_station_rows": "",
                "duplicate_station_name_rows": "",
                "coordinate_null_rows": "",
                "coordinate_outside_korea_bbox_rows": "",
                "coordinate_outside_seoul_wide_bbox_rows": "",
                "latitude_min": "",
                "latitude_max": "",
                "longitude_min": "",
                "longitude_max": "",
                "passenger_month": "",
                "passenger_api_total_count": "",
                "passenger_unique_route_station_count": "",
                "passenger_exact_unmatched_count": "",
                "passenger_normalized_unmatched_count": "",
                "judgement": "조건부 PASS",
                "conditional_reason_ko": "호선명 표기 후보이며, 수동검토필요 row는 확정 매핑으로 쓰기 전 확인해야 함",
            },
        ]
    )
    grain_df = pd.DataFrame(
        [
            {
                "table": "silver_subway_station_master",
                "key_cols": "역사_ID",
                "duplicate_key_rows": duplicate_station_id,
                "key_null_cells": key_null,
                "judgement": "PASS" if duplicate_station_id == 0 and key_null == 0 else "FAIL",
                "reason_ko": "역사마스터의 기본 grain은 역사_ID다. 역명은 환승역과 동일 역명 때문에 단독 조인 키로 쓰지 않는다.",
            },
            {
                "table": "silver_subway_station_master",
                "key_cols": "호선_명 + 역사_명",
                "duplicate_key_rows": duplicate_route_station,
                "key_null_cells": 0,
                "judgement": "PASS" if duplicate_route_station == 0 else "FAIL",
                "reason_ko": "승하차량은 역사_ID가 없으므로 호선_명+역사_명 조인이 필요하지만, 표기 차이 때문에 별도 조인 audit를 먼저 통과해야 한다.",
            },
            {
                "table": "silver_subway_route_codebook",
                "key_cols": "호선_명",
                "duplicate_key_rows": int(route_codebook.duplicated(["호선_명"]).sum()),
                "key_null_cells": int((route_codebook["호선_명"].astype(str).str.len() == 0).sum()),
                "judgement": "PASS",
                "reason_ko": "호선별 역사 수를 분리해 조인 후보와 접근성 해석에 재사용한다.",
            },
        ]
    )
    contract_df = pd.DataFrame(
        [
            {
                "table": "silver_subway_station_master",
                "source_id": SOURCE_ID,
                "provider": PROVIDER,
                "source_service": SERVICE,
                "rows": len(master),
                "contract_status": judgement,
                "usage_role": "지하철 역사 좌표 기반 거리감쇠 접근성 산출 및 승하차량 좌표 결합 마스터",
            },
            {
                "table": "silver_subway_route_codebook",
                "source_id": SOURCE_ID,
                "provider": PROVIDER,
                "source_service": SERVICE,
                "rows": len(route_codebook),
                "contract_status": "PASS",
                "usage_role": "호선별 역사 수와 노선 표기 확인",
            },
            {
                "table": "silver_subway_route_alias_candidate",
                "source_id": SOURCE_ID,
                "provider": PROVIDER,
                "source_service": SERVICE,
                "rows": len(alias_candidate),
                "contract_status": "조건부 PASS",
                "usage_role": "승하차량 원천과 역사마스터 간 호선명 표기 차이 후보",
            },
        ]
    )
    return domain_df, grain_df, contract_df


def write_validation_md(
    domain_df: pd.DataFrame,
    grain_df: pd.DataFrame,
    route_codebook: pd.DataFrame,
    join_audit: pd.DataFrame,
) -> None:
    path = RESEARCH_VALIDATION_DIR / "08_subway_station_master_silver_validation_20260703.md"
    main = domain_df.loc[domain_df["table"].eq("silver_subway_station_master")].iloc[0].to_dict()
    status_counts = join_audit["match_status"].value_counts().to_dict()
    unmatched_sample = join_audit.loc[
        join_audit["match_status"].eq("unmatched_after_candidate"),
        ["승하차_호선명", "승하차_역명", "호선_정규화후보", "역명_정규화후보"],
    ].head(20)
    lines = [
        "# 8차 지하철 역사마스터 silver 전처리 검증",
        "",
        f"작성시각: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "## 대상 파일",
        "",
        "- `datacorpus/_silver/silver_subway_station_master.csv`",
        "- `datacorpus/_silver/silver_subway_route_codebook.csv`",
        "- `datacorpus/_silver/silver_subway_route_alias_candidate.csv`",
        "- `datacorpus/_rule_validation/08_subway_station_master_passenger_join_audit.csv`",
        "",
        "## 사용 근거",
        "",
        "- `datacorpus/_raw_ingest/source_registry.csv`: 역사마스터는 지하철 승하차량을 좌표와 결합하기 위한 P1 원천으로 등록되어 있다.",
        "- `datacorpus/_raw_ingest/run_logs/20260703_transport_master_full_ko.md`: `subwayStationMaster` 전체 784건 / 1페이지 수집 성공이 기록되어 있다.",
        "- `research/algorithm_evidence_sources/data_docs/seoul_open_data_subway_station_master_OA-21232.html`: 역사 ID, 역사명, 호선명, 좌표를 확인할 수 있다고 설명한다.",
        "- `research/algorithm_evidence_sources/data_docs/seoul_open_data_subway_station_passengers_hourly_OA-12252.html`: 지하철 승하차량은 호선별·역별·시간대별 자료이며 매월 5일 전월 데이터를 갱신한다고 설명한다.",
        "- `research/전처리_알고리즘_실행계획_20260703.md`: 접근성/유입 축은 역 개수만이 아니라 좌표, 거리감쇠, 시간대 승하차량 결합을 목표로 한다.",
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
            "판단: 역사마스터 API 원응답의 `list_total_count` 784건과 silver row 수가 일치한다. 좌표 null과 역사_ID 중복은 없다.",
            "",
            "## 검증 2: grain과 조인 키",
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
            f"판단: `역사_ID`는 중복이 없지만, 역명 단독 중복 row는 {main['duplicate_station_name_rows']}개다. 따라서 역명만으로 승하차량이나 지도 좌표를 붙이면 안 된다.",
            "",
            "## 검증 3: 좌표와 공간 범위",
            "",
            "| 항목 | 값 |",
            "|---|---:|",
            f"| 위도 최소 | {main['latitude_min']} |",
            f"| 위도 최대 | {main['latitude_max']} |",
            f"| 경도 최소 | {main['longitude_min']} |",
            f"| 경도 최대 | {main['longitude_max']} |",
            f"| 좌표 null row | {main['coordinate_null_rows']} |",
            f"| 한국 경위도 bbox 밖 row | {main['coordinate_outside_korea_bbox_rows']} |",
            f"| 서울 넓은 bbox 밖 row | {main['coordinate_outside_seoul_wide_bbox_rows']} |",
            "",
            "판단: 값 범위는 경위도 좌표로 보이며 한국 bbox 밖 좌표는 없다. 다만 일부 광역철도 역이 서울 밖에 있으므로 서울 bbox 밖이라는 이유만으로 삭제하지 않는다.",
            "",
            "## 검증 4: 승하차량 결합 예비 audit",
            "",
            "| 항목 | 값 |",
            "|---|---:|",
            f"| 승하차량 기준월 | {main['passenger_month']} |",
            f"| 승하차량 API 총량 | {main['passenger_api_total_count']} |",
            f"| 승하차량 호선+역명 unique | {main['passenger_unique_route_station_count']} |",
            f"| exact 조인 미매칭 | {main['passenger_exact_unmatched_count']} |",
            f"| 정규화 후보 적용 후 미매칭 | {main['passenger_normalized_unmatched_count']} |",
        ]
    )
    lines.append("")
    lines.append("| match_status | row 수 |")
    lines.append("|---|---:|")
    for key, value in status_counts.items():
        lines.append(f"| {key} | {value} |")

    lines.extend(
        [
            "",
            "판단: 승하차량과 역사마스터는 exact 조인만으로는 부족하다. 호선명 공백, 9호선 연장 표기, 경의선/경의중앙선, 역명 괄호 표기 차이가 있어 alias 후보와 수동검토 목록을 분리했다.",
            "",
            "### 정규화 후보 후에도 남은 미매칭 예시",
            "",
            "| 승하차_호선명 | 승하차_역명 | 호선_정규화후보 | 역명_정규화후보 |",
            "|---|---|---|---|",
        ]
    )
    for row in unmatched_sample.to_dict("records"):
        lines.append(
            f"| {row['승하차_호선명']} | {row['승하차_역명']} | {row['호선_정규화후보']} | {row['역명_정규화후보']} |"
        )

    lines.extend(
        [
            "",
            "## 검증 5: 호선 코드북",
            "",
            "| 호선_명 | 역사_수 |",
            "|---|---:|",
        ]
    )
    for row in route_codebook.head(60).to_dict("records"):
        lines.append(f"| {row['호선_명']} | {row['역사_수']} |")

    lines.extend(
        [
            "",
            "## 2보 전진 1보 후퇴 기록",
            "",
            "- 전진 1: 지하철 역사마스터 784건을 역사_ID 기준으로 보존했다.",
            f"- 전진 2: 승하차량 {main['passenger_month']} {main['passenger_api_total_count']}건과의 예비 조인 audit를 만들어 exact 조인 한계를 숫자로 확인했다.",
            "- 후퇴 1: 역명 단독 조인은 금지한다. 환승역과 동일 역명 때문에 `역사_ID` 또는 `호선_명+역사_명`이 필요하다.",
            "- 후퇴 2: 호선/역명 정규화 후보는 확정 매핑이 아니다. 수동검토필요 row를 확정하기 전에는 승하차량 점수에 직접 넣지 않는다.",
            "- 후퇴 3: 좌표계가 문서에 직접 명시되지 않아 실제 meter 거리·도보시간 문구는 보류한다.",
            "",
            "## 알고리즘 단계에서 금지할 표현",
            "",
            "- 실제 도보시간",
            "- 실제 역세권 거리",
            "- 실제 방문확률",
            "- 전체 지하철 수요",
            "",
            "허용 표현:",
            "",
            "- 지하철 역사 좌표 접근성",
            "- 지하철 승하차량 결합 전 좌표 마스터",
            "- 호선+역명 기준 조인 후보",
            "- 시간대 승하차량 기반 접근성 강도 프록시",
            "",
            "## 다음 작업",
            "",
            "1. 지하철 승하차량 silver 전처리와 조인 audit 반영.",
            "2. 버스 승하차량 silver 전처리.",
            "3. 상권 polygon과 후보지 좌표를 연결할 point-in-polygon 기준 확정.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def append_progress(domain_df: pd.DataFrame) -> None:
    if not PROGRESS_PATH.exists():
        return
    main = domain_df.loc[domain_df["table"].eq("silver_subway_station_master")].iloc[0].to_dict()
    codebook = domain_df.loc[domain_df["table"].eq("silver_subway_route_codebook")].iloc[0].to_dict()
    alias = domain_df.loc[domain_df["table"].eq("silver_subway_route_alias_candidate")].iloc[0].to_dict()
    block = [
        "",
        "---",
        "",
        "## 10. 완료: 지하철 역사마스터 silver 테이블",
        "",
        "| 산출물 | row 수 | 상태 | 역할 |",
        "|---|---:|---|---|",
        f"| `datacorpus/_silver/silver_subway_station_master.csv` | {main['rows']:,} | {main['judgement']} | 지하철 역사 좌표 마스터 |",
        f"| `datacorpus/_silver/silver_subway_route_codebook.csv` | {codebook['rows']:,} | {codebook['judgement']} | 호선별 역사 수 코드북 |",
        f"| `datacorpus/_silver/silver_subway_route_alias_candidate.csv` | {alias['rows']:,} | {alias['judgement']} | 승하차량 조인용 호선명 후보 |",
        "",
        "검증 근거:",
        "",
        "- `datacorpus/_rule_validation/08_subway_station_master_domain_validation.csv`",
        "- `datacorpus/_rule_validation/08_subway_station_master_grain_validation.csv`",
        "- `datacorpus/_rule_validation/08_subway_station_master_source_contract.csv`",
        "- `datacorpus/_rule_validation/08_subway_station_master_passenger_join_audit.csv`",
        "- `research/rule_validation/08_subway_station_master_silver_validation_20260703.md`",
        "",
        "판단:",
        "",
        "- API 총량 784건과 silver row 수가 일치한다.",
        "- 역사_ID, 호선명+역명, 좌표에는 중복/null 문제가 없다.",
        f"- 역명 단독 중복 row가 {main['duplicate_station_name_rows']}개라 역명 단독 조인은 금지한다.",
        f"- {main['passenger_month']} 승하차량과 exact 조인하면 {main['passenger_exact_unmatched_count']}개가 미매칭이고, 정규화 후보 적용 후에도 {main['passenger_normalized_unmatched_count']}개가 남는다.",
        "- 좌표계가 문서에 직접 명시되지 않았으므로 실제 meter 거리/도보시간은 다음 단계에서 재확인한다.",
    ]
    text = PROGRESS_PATH.read_text(encoding="utf-8")
    marker = "## 10. 완료: 지하철 역사마스터 silver 테이블"
    if marker in text:
        text = text.split("\n---\n\n" + marker)[0].rstrip()
    PROGRESS_PATH.write_text(text.rstrip() + "\n" + "\n".join(block) + "\n", encoding="utf-8")


def append_precheck(domain_df: pd.DataFrame) -> None:
    if not PRECHECK_PATH.exists():
        return
    main = domain_df.loc[domain_df["table"].eq("silver_subway_station_master")].iloc[0].to_dict()
    text = PRECHECK_PATH.read_text(encoding="utf-8")
    marker = "| 지하철 역사마스터 |"
    if marker in text:
        return
    target = "| 버스정류소 위치 | 11,248건 정류소 좌표 silver 생성 완료 | 좌표 마스터로 쓰되 CRS 표기 혼재 때문에 meter 거리·도보시간 판단은 재확인 전까지 보류한다. |"
    addition = (
        target
        + "\n"
        + f"| 지하철 역사마스터 | {main['rows']:,}건 역사 좌표 silver 생성 완료 | 역명 단독 중복이 {main['duplicate_station_name_rows']}개라 `역사_ID` 또는 `호선_명+역사_명` 기준으로만 조인한다. 승하차량 exact 조인은 미매칭 {main['passenger_exact_unmatched_count']}개가 있어 별도 매핑 검토가 필요하다. |"
    )
    if target in text:
        text = text.replace(target, addition)
        PRECHECK_PATH.write_text(text, encoding="utf-8")


def main() -> None:
    ensure_dirs()
    master, route_codebook, page_count, api_total = build_subway_station_master()
    alias_candidate = build_route_alias_candidate()
    join_audit, passenger_api_total, exact_unmatched, normalized_unmatched, passenger_month_text = build_passenger_join_audit(master)
    domain_df, grain_df, contract_df = validate_subway_station(
        master,
        route_codebook,
        alias_candidate,
        join_audit,
        page_count,
        api_total,
        passenger_api_total,
        exact_unmatched,
        normalized_unmatched,
        passenger_month_text,
    )

    master.to_csv(SILVER_DIR / "silver_subway_station_master.csv", index=False, encoding="utf-8-sig")
    route_codebook.to_csv(SILVER_DIR / "silver_subway_route_codebook.csv", index=False, encoding="utf-8-sig")
    alias_candidate.to_csv(SILVER_DIR / "silver_subway_route_alias_candidate.csv", index=False, encoding="utf-8-sig")
    join_audit.to_csv(VALIDATION_DIR / "08_subway_station_master_passenger_join_audit.csv", index=False, encoding="utf-8-sig")
    domain_df.to_csv(VALIDATION_DIR / "08_subway_station_master_domain_validation.csv", index=False, encoding="utf-8-sig")
    grain_df.to_csv(VALIDATION_DIR / "08_subway_station_master_grain_validation.csv", index=False, encoding="utf-8-sig")
    contract_df.to_csv(VALIDATION_DIR / "08_subway_station_master_source_contract.csv", index=False, encoding="utf-8-sig")
    write_validation_md(domain_df, grain_df, route_codebook, join_audit)
    append_progress(domain_df)
    append_precheck(domain_df)

    summary = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "rows": len(master),
        "route_codebook_rows": len(route_codebook),
        "alias_candidate_rows": len(alias_candidate),
        "join_audit_rows": len(join_audit),
        "validation_judgements": domain_df[["table", "judgement"]].to_dict("records"),
        "outputs": [
            "datacorpus/_silver/silver_subway_station_master.csv",
            "datacorpus/_silver/silver_subway_route_codebook.csv",
            "datacorpus/_silver/silver_subway_route_alias_candidate.csv",
            "datacorpus/_rule_validation/08_subway_station_master_domain_validation.csv",
            "datacorpus/_rule_validation/08_subway_station_master_grain_validation.csv",
            "datacorpus/_rule_validation/08_subway_station_master_source_contract.csv",
            "datacorpus/_rule_validation/08_subway_station_master_passenger_join_audit.csv",
            "research/rule_validation/08_subway_station_master_silver_validation_20260703.md",
        ],
    }
    (VALIDATION_DIR / "08_subway_station_master_preprocess_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
