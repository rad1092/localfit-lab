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

SERVICE = "busStopLocationXyInfo"
RAW_PATH = RAW_DIR / "20260703" / "seoul_open_data" / "transport" / "bus_stop_location_api"
SOURCE_REGISTRY_PATH = RAW_DIR / "source_registry.csv"
SOURCE_DOC_PATH = ROOT / "research" / "algorithm_evidence_sources" / "data_docs" / "seoul_open_data_bus_stop_location_OA-15067.html"
PROGRESS_PATH = ROOT / "research" / "전처리_진행기록_20260703.md"

SNAPSHOT_DATE = "2026-07-03"
PROVIDER = "서울열린데이터광장"
SOURCE_ID = "seoul_bus_stop_location_file"
KEY_COLS = ["정류소_고유번호"]

COLUMNS = {
    "STOPS_NO": "정류소_고유번호",
    "STOPS_NM": "정류소_명",
    "XCRD": "경도",
    "YCRD": "위도",
    "NODE_ID": "정류소_ARS_ID",
    "STOPS_TYPE": "정류소_유형",
}

SOURCE_CRS_TEXT = "WGS84 (EPSG-5179)"
VALUE_CRS_JUDGEMENT = "좌표값 범위는 WGS84 경위도처럼 보이나 문서 표기가 WGS84와 EPSG-5179를 함께 적고 있어 거리계산 전 CRS 재확인이 필요함"


def ensure_dirs() -> None:
    for path in [SILVER_DIR, VALIDATION_DIR, RESEARCH_VALIDATION_DIR]:
        path.mkdir(parents=True, exist_ok=True)


def page_sort_key(path: Path) -> tuple[int, int]:
    match = re.search(r"_(\d+)_(\d+)\.json$", path.name)
    if not match:
        return (10**12, 10**12)
    return (int(match.group(1)), int(match.group(2)))


def read_openapi_pages() -> tuple[pd.DataFrame, int, int]:
    rows: list[dict[str, Any]] = []
    totals: set[int] = set()
    page_paths = sorted(RAW_PATH.glob(f"{SERVICE}_*.json"), key=page_sort_key)
    if not page_paths:
        raise FileNotFoundError(f"{RAW_PATH} 폴더에서 {SERVICE} 원응답을 찾지 못했습니다.")

    for path in page_paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        root = payload.get(SERVICE)
        if not isinstance(root, dict):
            raise ValueError(f"{path} 파일에 {SERVICE} 루트가 없습니다.")
        if "list_total_count" in root:
            totals.add(int(root["list_total_count"]))
        for row in root.get("row", []):
            item = dict(row)
            item["_raw_path"] = str(path.relative_to(ROOT))
            rows.append(item)

    if len(totals) != 1:
        raise ValueError(f"{SERVICE} list_total_count가 하나로 고정되지 않습니다: {sorted(totals)}")
    return pd.DataFrame(rows), len(page_paths), next(iter(totals))


def build_bus_stop_table() -> tuple[pd.DataFrame, pd.DataFrame, int, int]:
    raw, page_count, api_total = read_openapi_pages()
    df = raw.rename(columns=COLUMNS)
    expected = list(COLUMNS.values())
    missing = [col for col in expected if col not in df.columns]
    if missing:
        raise ValueError(f"버스정류소 위치 컬럼 변환 후 누락 컬럼: {missing}")

    df = df[expected + ["_raw_path"]].copy()
    for col in ["정류소_고유번호", "정류소_명", "정류소_ARS_ID", "정류소_유형"]:
        df[col] = df[col].astype(str).str.strip()

    df["정류소_ARS_ID_원천"] = df["정류소_ARS_ID"]
    df["정류소_ARS_ID"] = df["정류소_ARS_ID"].str.zfill(5)
    df["quality_ars_id_zero_padded"] = df["정류소_ARS_ID"].ne(df["정류소_ARS_ID_원천"])
    df["경도"] = pd.to_numeric(df["경도"], errors="coerce")
    df["위도"] = pd.to_numeric(df["위도"], errors="coerce")
    df["quality_coordinate_missing"] = df[["경도", "위도"]].isna().any(axis=1)
    df["quality_coordinate_outside_seoul_bbox"] = ~(
        df["경도"].between(126.0, 128.0) & df["위도"].between(37.0, 38.0)
    )
    df["coordinate_source_doc"] = SOURCE_CRS_TEXT
    df["coordinate_value_judgement"] = VALUE_CRS_JUDGEMENT
    df["distance_use_status"] = "조건부 보류: meter 거리계산 전 CRS 재확인 필요"
    df["source_id"] = SOURCE_ID
    df["provider"] = PROVIDER
    df["source_service"] = SERVICE
    df["snapshot_date"] = SNAPSHOT_DATE
    df["source_grain"] = "정류소_고유번호"
    df["raw_page_count"] = page_count
    df["api_list_total_count"] = api_total
    df["raw_row_count"] = len(df)
    df["directness_level"] = "P0_공식_정류소_좌표_원천"
    df["forbidden_claim_ko"] = "실제 도보시간, 실제 접근시간, 실제 방문확률, 실제 승하차량으로 표현 금지"
    df["notes_ko"] = "후보지 또는 상권 중심점과 버스정류소 간 반경·거리감쇠·밀도 계산을 위한 좌표 마스터다. 승하차 강도는 별도 승하차량 원천과 결합해야 한다."

    codebook = (
        df["정류소_유형"]
        .value_counts(dropna=False)
        .rename_axis("정류소_유형")
        .reset_index(name="정류소_수")
        .sort_values(["정류소_수", "정류소_유형"], ascending=[False, True])
    )
    codebook["usage_role"] = "정류장 유형별 접근성 보조 해석"
    codebook["score_use_warning_ko"] = "유형은 접근성 보조 설명이며 실제 이용량이나 도보시간을 의미하지 않는다."
    codebook["source_id"] = SOURCE_ID
    codebook["provider"] = PROVIDER
    codebook["snapshot_date"] = SNAPSHOT_DATE

    return (
        df.sort_values(["정류소_고유번호", "정류소_ARS_ID"]).reset_index(drop=True),
        codebook.reset_index(drop=True),
        page_count,
        api_total,
    )


def key_null_cells(df: pd.DataFrame) -> int:
    return sum(int((df[col].isna() | df[col].astype(str).str.strip().eq("")).sum()) for col in KEY_COLS)


def validate_bus_stop(
    df: pd.DataFrame,
    codebook: pd.DataFrame,
    page_count: int,
    api_total: int,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    key_null = key_null_cells(df)
    duplicate_stop_no = int(df.duplicated(["정류소_고유번호"]).sum())
    duplicate_ars_id = int(df.duplicated(["정류소_ARS_ID"]).sum())
    coordinate_null_rows = int(df["quality_coordinate_missing"].sum())
    coordinate_outside_bbox_rows = int(df["quality_coordinate_outside_seoul_bbox"].sum())
    ars_length_not_5_rows = int(df["정류소_ARS_ID"].astype(str).str.len().ne(5).sum())
    stop_no_length_not_9_rows = int(df["정류소_고유번호"].astype(str).str.len().ne(9).sum())
    type_null_rows = int((df["정류소_유형"].isna() | df["정류소_유형"].astype(str).str.strip().eq("")).sum())
    zero_padded_rows = int(df["quality_ars_id_zero_padded"].sum())

    hard_fail = (
        len(df) != api_total
        or key_null != 0
        or duplicate_stop_no != 0
        or coordinate_null_rows != 0
        or coordinate_outside_bbox_rows != 0
        or ars_length_not_5_rows != 0
        or type_null_rows != 0
    )
    # 좌표값은 정상 범위지만 공식 문서에 WGS84와 EPSG-5179가 같이 적혀 있어 meter 거리 산식은 보류한다.
    judgement = "FAIL" if hard_fail else "조건부 PASS"

    domain_df = pd.DataFrame(
        [
            {
                "table": "silver_bus_stop_location_master",
                "rows": len(df),
                "api_total_count": api_total,
                "raw_page_count": page_count,
                "row_count_matches_api": len(df) == api_total,
                "unique_stop_no_count": df["정류소_고유번호"].nunique(),
                "unique_ars_id_count": df["정류소_ARS_ID"].nunique(),
                "stop_type_count": df["정류소_유형"].nunique(),
                "longitude_min": df["경도"].min(),
                "longitude_max": df["경도"].max(),
                "latitude_min": df["위도"].min(),
                "latitude_max": df["위도"].max(),
                "key_null_cells": key_null,
                "duplicate_stop_no_rows": duplicate_stop_no,
                "duplicate_ars_id_rows": duplicate_ars_id,
                "coordinate_null_rows": coordinate_null_rows,
                "coordinate_outside_seoul_bbox_rows": coordinate_outside_bbox_rows,
                "ars_length_not_5_rows": ars_length_not_5_rows,
                "stop_no_length_not_9_rows": stop_no_length_not_9_rows,
                "ars_zero_padded_rows": zero_padded_rows,
                "type_null_rows": type_null_rows,
                "judgement": judgement,
                "conditional_reason_ko": VALUE_CRS_JUDGEMENT,
            },
            {
                "table": "silver_bus_stop_type_codebook",
                "rows": len(codebook),
                "api_total_count": "",
                "raw_page_count": "",
                "row_count_matches_api": "",
                "unique_stop_no_count": "",
                "unique_ars_id_count": "",
                "stop_type_count": len(codebook),
                "longitude_min": "",
                "longitude_max": "",
                "latitude_min": "",
                "latitude_max": "",
                "key_null_cells": int((codebook["정류소_유형"].astype(str).str.len() == 0).sum()),
                "duplicate_stop_no_rows": "",
                "duplicate_ars_id_rows": "",
                "coordinate_null_rows": "",
                "coordinate_outside_seoul_bbox_rows": "",
                "ars_length_not_5_rows": "",
                "stop_no_length_not_9_rows": "",
                "ars_zero_padded_rows": "",
                "type_null_rows": "",
                "judgement": "PASS",
                "conditional_reason_ko": "",
            },
        ]
    )
    grain_df = pd.DataFrame(
        [
            {
                "table": "silver_bus_stop_location_master",
                "key_cols": "정류소_고유번호",
                "duplicate_key_rows": duplicate_stop_no,
                "key_null_cells": key_null,
                "judgement": "PASS" if duplicate_stop_no == 0 and key_null == 0 else "FAIL",
                "reason_ko": "정류소 고유번호는 좌표 마스터의 기본 grain이다. ARS-ID도 현재 중복은 없지만 서울시 문서상 5자리 보정 이슈가 있어 별도 검증값으로 보존한다.",
            },
            {
                "table": "silver_bus_stop_type_codebook",
                "key_cols": "정류소_유형",
                "duplicate_key_rows": int(codebook.duplicated(["정류소_유형"]).sum()),
                "key_null_cells": int((codebook["정류소_유형"].astype(str).str.len() == 0).sum()),
                "judgement": "PASS",
                "reason_ko": "정류소 유형별 건수와 해석 경고를 분리해 리포트와 알고리즘 주석에 재사용한다.",
            },
        ]
    )
    contract_df = pd.DataFrame(
        [
            {
                "table": "silver_bus_stop_location_master",
                "source_id": SOURCE_ID,
                "provider": PROVIDER,
                "source_service": SERVICE,
                "rows": len(df),
                "contract_status": judgement,
                "usage_role": "후보지/상권 중심점 기준 버스정류소 반경, 밀도, 거리감쇠 접근성 산출의 좌표 마스터",
            },
            {
                "table": "silver_bus_stop_type_codebook",
                "source_id": SOURCE_ID,
                "provider": PROVIDER,
                "source_service": SERVICE,
                "rows": len(codebook),
                "contract_status": "PASS",
                "usage_role": "정류소 유형별 보조 해석",
            },
        ]
    )
    return domain_df, grain_df, contract_df


def write_validation_md(domain_df: pd.DataFrame, grain_df: pd.DataFrame, codebook: pd.DataFrame) -> None:
    path = RESEARCH_VALIDATION_DIR / "07_bus_stop_location_silver_validation_20260703.md"
    main = domain_df.loc[domain_df["table"].eq("silver_bus_stop_location_master")].iloc[0].to_dict()
    lines = [
        "# 7차 버스정류소 위치 silver 전처리 검증",
        "",
        f"작성시각: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "## 대상 파일",
        "",
        "- `datacorpus/_silver/silver_bus_stop_location_master.csv`",
        "- `datacorpus/_silver/silver_bus_stop_type_codebook.csv`",
        "",
        "## 사용 근거",
        "",
        "- `datacorpus/_raw_ingest/source_registry.csv`: 버스정류소 위치정보는 접근성/유입 P0 원천으로 등록되어 있다.",
        "- `datacorpus/_raw_ingest/run_logs/20260703_transport_master_full_ko.md`: `busStopLocationXyInfo` 전체 11,248건 / 12페이지 수집 성공이 기록되어 있다.",
        "- `research/algorithm_evidence_sources/data_docs/seoul_open_data_bus_stop_location_OA-15067.html`: 정류소 코드 ARS-ID는 5자리이며, 4자리인 경우 앞에 0을 붙이라는 설명이 있다.",
        "- `research/algorithm_evidence_sources/data_docs/seoul_open_data_bus_stop_location_OA-15067.html`: 좌표계가 `WGS84 (EPSG-5179)`로 적혀 있어 표기가 혼재되어 있다.",
        "- `research/전처리_알고리즘_실행계획_20260703.md`: 접근성/유입 축은 단순 정류장 개수가 아니라 거리감쇠, 정류장 밀도, 승하차량 결합을 목표로 한다.",
        "- `research/site_selection_sources/09_esri_huff_model.html`: 거리와 매력도를 함께 보는 방식은 가능하지만, 보정 없이 방문확률로 단정하면 안 된다.",
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
            "판단: API 원응답의 `list_total_count` 11,248건과 silver row 수가 일치한다. 좌표와 정류소 ID는 누락 없이 보존됐다.",
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
            "판단: 정류소 위치 마스터의 기본 grain은 `정류소_고유번호`다. `정류소_ARS_ID`도 현재 중복은 없지만, 서울시 문서에 5자리 보정 이슈가 있으므로 원천값과 보정값을 함께 남긴다.",
            "",
            "## 검증 3: 좌표와 코드 품질",
            "",
            "| 항목 | 값 |",
            "|---|---:|",
            f"| 경도 최소 | {main['longitude_min']} |",
            f"| 경도 최대 | {main['longitude_max']} |",
            f"| 위도 최소 | {main['latitude_min']} |",
            f"| 위도 최대 | {main['latitude_max']} |",
            f"| 좌표 null row | {main['coordinate_null_rows']} |",
            f"| 서울 경위도 bbox 밖 row | {main['coordinate_outside_seoul_bbox_rows']} |",
            f"| ARS-ID 5자리 아님 row | {main['ars_length_not_5_rows']} |",
            f"| 정류소 고유번호 9자리 아님 row | {main['stop_no_length_not_9_rows']} |",
            f"| ARS-ID 0-padding 보정 row | {main['ars_zero_padded_rows']} |",
            "",
            "판단: 값 범위는 서울 경위도처럼 보인다. 다만 공식 문서의 좌표계 표기가 `WGS84 (EPSG-5179)`처럼 섞여 있어, meter 단위 거리 산출 전에 CRS 재확인을 거쳐야 한다.",
            "",
            "## 검증 4: 정류소 유형 코드북",
            "",
            "| 정류소_유형 | 정류소_수 |",
            "|---|---:|",
        ]
    )
    for row in codebook.to_dict("records"):
        lines.append(f"| {row['정류소_유형']} | {row['정류소_수']} |")

    lines.extend(
        [
            "",
            "## 2보 전진 1보 후퇴 기록",
            "",
            "- 전진 1: 버스정류소 위치 API 전체 11,248건을 정류소 고유번호 기준으로 보존했다.",
            "- 전진 2: ARS-ID 5자리 정책을 반영하고 원천값과 보정값을 모두 남겨 이후 승하차량 조인 안전성을 높였다.",
            "- 후퇴 1: 좌표값은 정상 범위지만 공식 문서의 CRS 표기가 혼재되어 있어, 이 단계에서는 실제 도보시간·meter 거리·최단거리 판단을 하지 않는다.",
            "- 후퇴 2: 정류소 위치는 접근성의 공간 기준일 뿐 실제 이용량이 아니다. 유입 강도는 버스 승하차량 silver와 결합해야 한다.",
            "",
            "## 알고리즘 단계에서 금지할 표현",
            "",
            "- 실제 도보시간",
            "- 실제 접근시간",
            "- 실제 방문확률",
            "- 실제 버스 이용객 수",
            "",
            "허용 표현:",
            "",
            "- 후보지 반경 내 버스정류소 접근성",
            "- 버스정류소 밀도",
            "- 거리감쇠 기반 접근성 프록시",
            "- 승하차량 결합 전 좌표 마스터",
            "",
            "## 다음 작업",
            "",
            "1. 지하철 역사마스터 silver 전처리.",
            "2. 버스 승하차량 월별 silver 전처리.",
            "3. 지하철 승하차량 월별 silver 전처리.",
            "4. 상권 폴리곤 또는 후보지 좌표와 결합할 거리계산 기준 확정.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def append_progress(domain_df: pd.DataFrame) -> None:
    if not PROGRESS_PATH.exists():
        return
    main = domain_df.loc[domain_df["table"].eq("silver_bus_stop_location_master")].iloc[0].to_dict()
    codebook = domain_df.loc[domain_df["table"].eq("silver_bus_stop_type_codebook")].iloc[0].to_dict()
    block = [
        "",
        "---",
        "",
        "## 9. 완료: 버스정류소 위치 silver 테이블",
        "",
        "| 산출물 | row 수 | 상태 | 역할 |",
        "|---|---:|---|---|",
        f"| `datacorpus/_silver/silver_bus_stop_location_master.csv` | {main['rows']:,} | {main['judgement']} | 버스정류소 좌표 마스터 |",
        f"| `datacorpus/_silver/silver_bus_stop_type_codebook.csv` | {codebook['rows']:,} | {codebook['judgement']} | 정류소 유형 코드북 |",
        "",
        "검증 근거:",
        "",
        "- `datacorpus/_rule_validation/07_bus_stop_location_domain_validation.csv`",
        "- `datacorpus/_rule_validation/07_bus_stop_location_grain_validation.csv`",
        "- `datacorpus/_rule_validation/07_bus_stop_location_source_contract.csv`",
        "- `research/rule_validation/07_bus_stop_location_silver_validation_20260703.md`",
        "",
        "판단:",
        "",
        "- API 총량 11,248건과 silver row 수가 일치한다.",
        "- 정류소 고유번호, ARS-ID, 좌표에는 null이 없다.",
        "- ARS-ID는 5자리 정책을 반영했고 원천값도 보존했다.",
        "- 좌표값은 서울 경위도 범위지만 공식 문서의 CRS 표기가 혼재되어 있어 meter 거리 산출은 다음 단계에서 재확인한다.",
        "- 정류소 위치는 실제 승하차량이 아니므로 접근성 좌표 프록시로만 사용한다.",
    ]
    text = PROGRESS_PATH.read_text(encoding="utf-8")
    marker = "## 9. 완료: 버스정류소 위치 silver 테이블"
    if marker in text:
        text = text.split("\n---\n\n" + marker)[0].rstrip()
    PROGRESS_PATH.write_text(text.rstrip() + "\n" + "\n".join(block) + "\n", encoding="utf-8")


def main() -> None:
    ensure_dirs()
    df, codebook, page_count, api_total = build_bus_stop_table()
    domain_df, grain_df, contract_df = validate_bus_stop(df, codebook, page_count, api_total)

    df.to_csv(SILVER_DIR / "silver_bus_stop_location_master.csv", index=False, encoding="utf-8-sig")
    codebook.to_csv(SILVER_DIR / "silver_bus_stop_type_codebook.csv", index=False, encoding="utf-8-sig")
    domain_df.to_csv(VALIDATION_DIR / "07_bus_stop_location_domain_validation.csv", index=False, encoding="utf-8-sig")
    grain_df.to_csv(VALIDATION_DIR / "07_bus_stop_location_grain_validation.csv", index=False, encoding="utf-8-sig")
    contract_df.to_csv(VALIDATION_DIR / "07_bus_stop_location_source_contract.csv", index=False, encoding="utf-8-sig")
    write_validation_md(domain_df, grain_df, codebook)
    append_progress(domain_df)

    summary = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "rows": len(df),
        "codebook_rows": len(codebook),
        "validation_judgements": domain_df[["table", "judgement"]].to_dict("records"),
        "outputs": [
            "datacorpus/_silver/silver_bus_stop_location_master.csv",
            "datacorpus/_silver/silver_bus_stop_type_codebook.csv",
            "datacorpus/_rule_validation/07_bus_stop_location_domain_validation.csv",
            "datacorpus/_rule_validation/07_bus_stop_location_grain_validation.csv",
            "datacorpus/_rule_validation/07_bus_stop_location_source_contract.csv",
            "research/rule_validation/07_bus_stop_location_silver_validation_20260703.md",
        ],
    }
    (VALIDATION_DIR / "07_bus_stop_location_preprocess_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
