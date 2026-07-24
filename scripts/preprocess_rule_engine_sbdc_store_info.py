from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
UNZIPPED_DIR = ROOT / "datacorpus" / "_unzipped"
SILVER_DIR = ROOT / "datacorpus" / "_silver"
VALIDATION_DIR = ROOT / "datacorpus" / "_rule_validation"
RESEARCH_VALIDATION_DIR = ROOT / "research" / "rule_validation"

SNAPSHOT_DATE = "2026-07-03"
SOURCE_ID = "sbdc_store_info"
PROVIDER = "소상공인시장진흥공단"
CHUNK_SIZE = 100_000

SEOUL_POINT_PATH = SILVER_DIR / "silver_sbdc_store_poi_seoul_202603.csv"
HDONG_INDUSTRY_PATH = SILVER_DIR / "silver_sbdc_store_competition_hdong_industry_202603.csv"
SGG_INDUSTRY_PATH = SILVER_DIR / "silver_sbdc_store_competition_sgg_industry_202603.csv"
OBSERVED_CODEBOOK_PATH = SILVER_DIR / "silver_sbdc_store_industry_codebook_observed_202603.csv"
SOURCE_AUDIT_PATH = SILVER_DIR / "silver_sbdc_store_source_file_audit.csv"
ISSUE_SAMPLE_PATH = SILVER_DIR / "silver_sbdc_store_quality_issue_samples_202603.csv"

DOMAIN_VALIDATION_PATH = VALIDATION_DIR / "13_sbdc_store_info_domain_validation.csv"
GRAIN_VALIDATION_PATH = VALIDATION_DIR / "13_sbdc_store_info_grain_validation.csv"
CONSISTENCY_VALIDATION_PATH = VALIDATION_DIR / "13_sbdc_store_info_consistency_validation.csv"
MD_REPORT_PATH = RESEARCH_VALIDATION_DIR / "13_sbdc_store_info_silver_validation_20260703.md"

STORE_FILE_RE = re.compile(r"상가\(상권\)정보_(?P<region>[^_]+)_(?P<ym>\d{6})\.csv$")

REQUIRED_COLUMNS = [
    "상가업소번호",
    "상호명",
    "상권업종대분류코드",
    "상권업종대분류명",
    "상권업종중분류코드",
    "상권업종중분류명",
    "상권업종소분류코드",
    "상권업종소분류명",
    "표준산업분류코드",
    "표준산업분류명",
    "시도코드",
    "시도명",
    "시군구코드",
    "시군구명",
    "행정동코드",
    "행정동명",
    "법정동코드",
    "법정동명",
    "지번주소",
    "도로명주소",
    "경도",
    "위도",
]

TEXT_COLUMNS = [
    "상가업소번호",
    "상호명",
    "지점명",
    "상권업종대분류코드",
    "상권업종대분류명",
    "상권업종중분류코드",
    "상권업종중분류명",
    "상권업종소분류코드",
    "상권업종소분류명",
    "표준산업분류코드",
    "표준산업분류명",
    "시도코드",
    "시도명",
    "시군구코드",
    "시군구명",
    "행정동코드",
    "행정동명",
    "법정동코드",
    "법정동명",
    "지번코드",
    "대지구분코드",
    "대지구분명",
    "지번본번지",
    "지번부번지",
    "지번주소",
    "도로명코드",
    "도로명",
    "건물본번지",
    "건물부번지",
    "건물관리번호",
    "건물명",
    "도로명주소",
    "구우편번호",
    "신우편번호",
    "동정보",
    "층정보",
    "호정보",
]


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


def write_csv(df: pd.DataFrame, path: Path) -> None:
    df.to_csv(path, index=False, encoding="utf-8-sig")


def append_csv(df: pd.DataFrame, path: Path, first_write: bool) -> None:
    encoding = "utf-8-sig" if first_write else "utf-8"
    df.to_csv(path, index=False, encoding=encoding, mode="w" if first_write else "a", header=first_write)


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


def extract_store_file_info(path: Path) -> dict[str, Any] | None:
    match = STORE_FILE_RE.search(path.name)
    if not match:
        return None
    return {
        "source_id": SOURCE_ID,
        "provider": PROVIDER,
        "source_file": path.relative_to(ROOT).as_posix(),
        "region_name": match.group("region"),
        "source_ym": match.group("ym"),
        "file_size_bytes": path.stat().st_size,
    }


def count_csv_rows_by_newline(path: Path) -> int:
    # 상가업소 원천은 지역별 대용량 CSV라 전체 행 수 audit는 빠른 줄 수 기준으로 확인한다.
    line_count = 0
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            line_count += block.count(b"\n")
    return max(line_count - 1, 0)


def find_source_files() -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for path in sorted(UNZIPPED_DIR.rglob("*.csv")):
        info = extract_store_file_info(path)
        if info is None:
            continue
        info["row_count_line_scan"] = count_csv_rows_by_newline(path)
        info["selected_for_silver"] = info["region_name"] == "서울"
        info["process_scope"] = "서울 알고리즘 입력" if info["region_name"] == "서울" else "전국 비교/후속 확장 보존"
        info["notes_ko"] = (
            "현재 서울 입지 알고리즘의 POI/경쟁 프록시로 silver 생성"
            if info["region_name"] == "서울"
            else "서울 외 지역은 원천 존재와 행 수를 audit에 남기고 후속 전국 확장 시 사용"
        )
        records.append(info)
    if not records:
        raise FileNotFoundError("SBDC 상가(상권)정보 CSV를 datacorpus/_unzipped에서 찾지 못했습니다.")
    return pd.DataFrame(records).sort_values(["source_ym", "region_name"], ascending=[False, True])


def choose_seoul_source(source_audit: pd.DataFrame) -> Path:
    seoul = source_audit[source_audit["region_name"] == "서울"].copy()
    if seoul.empty:
        raise FileNotFoundError("SBDC 서울 상가(상권)정보 CSV를 찾지 못했습니다.")
    latest = seoul.sort_values(["source_ym", "file_size_bytes"], ascending=[False, False]).iloc[0]
    return ROOT / latest["source_file"]


def quarter_code_from_ym(source_ym: str) -> int:
    year = int(source_ym[:4])
    month = int(source_ym[4:6])
    return int(f"{year}{((month - 1) // 3) + 1}")


def normalize_chunk(chunk: pd.DataFrame, source_file: Path, source_ym: str, row_offset: int) -> pd.DataFrame:
    chunk = chunk.copy()
    for col in REQUIRED_COLUMNS:
        if col not in chunk.columns:
            chunk[col] = ""
    for col in TEXT_COLUMNS:
        if col in chunk.columns:
            chunk[col] = chunk[col].map(clean_text)

    lon = pd.to_numeric(chunk["경도"], errors="coerce")
    lat = pd.to_numeric(chunk["위도"], errors="coerce")
    chunk["경도"] = lon
    chunk["위도"] = lat
    chunk["source_id"] = SOURCE_ID
    chunk["provider"] = PROVIDER
    chunk["기준_년월"] = source_ym
    chunk["기준_년분기_코드"] = quarter_code_from_ym(source_ym)
    chunk["source_file"] = source_file.relative_to(ROOT).as_posix()
    chunk["snapshot_date"] = SNAPSHOT_DATE
    chunk["원천행번호"] = range(row_offset + 1, row_offset + len(chunk) + 1)

    chunk["좌표유효여부"] = lon.notna() & lat.notna()
    chunk["서울광역좌표범위여부"] = lon.between(126.5, 127.5, inclusive="both") & lat.between(37.0, 38.0, inclusive="both")
    chunk["주소보유여부"] = chunk["지번주소"].ne("") | chunk["도로명주소"].ne("")
    chunk["업종코드보유여부"] = chunk["상권업종대분류코드"].ne("") & chunk["상권업종중분류코드"].ne("") & chunk["상권업종소분류코드"].ne("")
    chunk["행정동코드보유여부"] = chunk["행정동코드"].ne("")
    chunk["점수직접사용상태"] = "좌표/행정동 POI 프록시: 상권 polygon 매칭 전 직접 상권점수 사용 보류"
    return chunk


def aggregate_count(chunk: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    working = chunk.copy()
    working["점포수"] = 1
    working["좌표유효점포수"] = working["좌표유효여부"].astype(int)
    working["도로명주소보유점포수"] = working["도로명주소"].ne("").astype(int)
    working["지번주소보유점포수"] = working["지번주소"].ne("").astype(int)
    working["지점명보유점포수"] = working["지점명"].ne("").astype(int) if "지점명" in working.columns else 0
    return (
        working.groupby(keys, dropna=False)[
            ["점포수", "좌표유효점포수", "도로명주소보유점포수", "지번주소보유점포수", "지점명보유점포수"]
        ]
        .sum()
        .reset_index()
    )


def merge_aggregates(parts: list[pd.DataFrame], keys: list[str]) -> pd.DataFrame:
    if not parts:
        return pd.DataFrame(columns=keys)
    merged = pd.concat(parts, ignore_index=True)
    count_cols = [c for c in merged.columns if c not in keys]
    return merged.groupby(keys, dropna=False)[count_cols].sum().reset_index().sort_values(keys).reset_index(drop=True)


def status(pass_condition: bool, conditional: bool = False) -> str:
    if pass_condition:
        return "CONDITIONAL_PASS" if conditional else "PASS"
    return "FAIL"


def build_validation_rows(metrics: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    domain = pd.DataFrame(
        [
            {
                "검증항목": "서울 원천 행 수와 point silver 행 수 일치",
                "측정값": metrics["source_row_count"],
                "기준값": metrics["point_row_count"],
                "판정": status(metrics["source_row_count"] == metrics["point_row_count"]),
                "근거": "원천 CSV 전체 row를 보존해야 POI/경쟁 프록시가 왜곡되지 않는다.",
            },
            {
                "검증항목": "필수 컬럼 존재",
                "측정값": metrics["missing_required_columns"],
                "기준값": "0",
                "판정": status(metrics["missing_required_columns"] == 0),
                "근거": "상가업소번호, 업종코드, 행정동, 주소, 좌표가 최소 계약이다.",
            },
            {
                "검증항목": "시도명 서울 여부",
                "측정값": metrics["non_seoul_rows"],
                "기준값": "0",
                "판정": status(metrics["non_seoul_rows"] == 0),
                "근거": "이번 silver는 서울 알고리즘 입력이므로 서울 파일만 직접 처리한다.",
            },
            {
                "검증항목": "전국 원천 파일 audit",
                "측정값": metrics["national_source_file_count"],
                "기준값": "서울 포함 지역별 파일 목록",
                "판정": status(metrics["national_source_file_count"] >= 1, conditional=True),
                "근거": "서울 외 지역은 후속 전국 확장/비교용으로 원천 존재와 행 수를 남긴다.",
            },
        ]
    )

    grain = pd.DataFrame(
        [
            {
                "검증항목": "상가업소번호 결측",
                "측정값": metrics["null_store_id_rows"],
                "기준값": "0",
                "판정": status(metrics["null_store_id_rows"] == 0),
                "근거": "point grain은 기준년월+상가업소번호다.",
            },
            {
                "검증항목": "기준년월+상가업소번호 중복 row",
                "측정값": metrics["duplicate_store_id_rows"],
                "기준값": "0",
                "판정": status(metrics["duplicate_store_id_rows"] == 0),
                "근거": "중복이 있으면 점포 밀집/경쟁 프록시가 부풀려진다.",
            },
            {
                "검증항목": "행정동×소분류 집계 합계",
                "측정값": metrics["hdong_sum"],
                "기준값": metrics["point_row_count"],
                "판정": status(metrics["hdong_sum"] == metrics["point_row_count"]),
                "근거": "행정동 단위 경쟁 프록시는 point silver 전체를 빠짐없이 집계해야 한다.",
            },
            {
                "검증항목": "자치구×소분류 집계 합계",
                "측정값": metrics["sgg_sum"],
                "기준값": metrics["point_row_count"],
                "판정": status(metrics["sgg_sum"] == metrics["point_row_count"]),
                "근거": "자치구 단위 fallback/후보 필터링에도 동일한 전체성이 필요하다.",
            },
        ]
    )

    consistency = pd.DataFrame(
        [
            {
                "검증항목": "업종 소분류 코드 결측",
                "측정값": metrics["null_small_industry_rows"],
                "기준값": "0",
                "판정": status(metrics["null_small_industry_rows"] == 0),
                "근거": "업종 계층 선택과 경쟁 프록시 모두 소분류 코드가 필요하다.",
            },
            {
                "검증항목": "SBDC 업종 계층 미매칭 소분류",
                "측정값": metrics["unmatched_small_code_count"],
                "기준값": "0",
                "판정": status(metrics["unmatched_small_code_count"] == 0),
                "근거": "공공데이터 설명의 10/75/247 업종 체계와 내부 업종 계층이 같은지 확인한다.",
            },
            {
                "검증항목": "좌표 결측 또는 숫자 변환 실패",
                "측정값": metrics["invalid_coordinate_rows"],
                "기준값": "0",
                "판정": status(metrics["invalid_coordinate_rows"] == 0),
                "근거": "좌표가 있어야 이후 point-in-polygon/최근접 상권 매칭이 가능하다.",
            },
            {
                "검증항목": "서울 광역 bbox 밖 좌표",
                "측정값": metrics["outside_seoul_bbox_rows"],
                "기준값": "0",
                "판정": status(metrics["outside_seoul_bbox_rows"] == 0),
                "근거": "경도/위도 순서 오류나 타지역 혼입을 빠르게 잡기 위한 1차 범위 검증이다.",
            },
            {
                "검증항목": "주소 결측",
                "측정값": metrics["missing_address_rows"],
                "기준값": "0",
                "판정": status(metrics["missing_address_rows"] == 0),
                "근거": "주소는 좌표 검증과 사용자 표시 보조 정보다. 점수 직접값은 아니다.",
            },
        ]
    )
    return domain, grain, consistency


def write_report(
    source_path: Path,
    source_audit: pd.DataFrame,
    domain: pd.DataFrame,
    grain: pd.DataFrame,
    consistency: pd.DataFrame,
    metrics: dict[str, Any],
) -> None:
    report = f"""# SBDC 상가업소 silver 검증 보고서

작성일: {SNAPSHOT_DATE}

## 1. 처리 목적

소상공인시장진흥공단 상가(상권)정보는 영업 중인 전국 상가업소의 상호, 업종코드, 주소, 경도, 위도 등을 제공하는 원천이다. `research/algorithm_evidence_sources/data_docs/data_go_kr_sbiz_store_info.html`와 `research/site_selection_sources/10_sbiz_shop_data_portal.html`에 같은 설명이 보존되어 있고, 업종분류 체계는 대분류 10개, 중분류 75개, 소분류 247개로 안내되어 있다.

이번 전처리는 서울 입지 알고리즘의 보조 입력으로 쓸 `점포 POI/경쟁 밀집 프록시`를 만드는 단계다. 단, 아직 상권 polygon point-in-polygon을 붙인 것이 아니므로 이 산출물을 곧바로 상권별 점수로 쓰지 않는다. 현재 산출물은 행정동/자치구/좌표 단위의 중간 silver다.

## 2. 사용 원천

- 선택 원천: `{source_path.relative_to(ROOT).as_posix()}`
- 기준년월: `{metrics["source_ym"]}`
- 서울 원천 행 수: `{metrics["source_row_count"]:,}`
- 전국 원천 파일 수: `{metrics["national_source_file_count"]:,}`
- 전국 원천 줄 수 합계: `{metrics["national_row_count_line_scan"]:,}`

## 3. 생성 산출물

| 산출물 | 행 수 | 역할 |
|---|---:|---|
| `datacorpus/_silver/silver_sbdc_store_poi_seoul_202603.csv` | {metrics["point_row_count"]:,} | 서울 상가업소 point 원천 보존, 좌표/행정동/업종 보유 |
| `datacorpus/_silver/silver_sbdc_store_competition_hdong_industry_202603.csv` | {metrics["hdong_rows"]:,} | 행정동×업종 소분류 점포 밀집 프록시 |
| `datacorpus/_silver/silver_sbdc_store_competition_sgg_industry_202603.csv` | {metrics["sgg_rows"]:,} | 자치구×업종 소분류 점포 밀집 프록시 |
| `datacorpus/_silver/silver_sbdc_store_industry_codebook_observed_202603.csv` | {metrics["observed_codebook_rows"]:,} | 서울 원천에서 실제 관측된 SBDC 업종 코드북 |
| `datacorpus/_silver/silver_sbdc_store_source_file_audit.csv` | {len(source_audit):,} | 전국 지역별 파일 존재, 크기, 줄 수, 사용 범위 기록 |

## 4. 도메인 검증

{markdown_table(domain)}

## 5. grain 검증

{markdown_table(grain)}

## 6. 정합성 검증

{markdown_table(consistency)}

## 7. 알고리즘 사용 판단

- 사용 가능: 행정동/자치구 단위의 동종·유사업종 점포 밀집, 좌표 기반 후속 상권 매칭 입력.
- 조건부 사용: 상권 polygon 매칭이 끝난 뒤에만 상권별 경쟁/밀집 점수에 직접 반영한다.
- 사용 금지: 개별 점포 성공확률, 개별 매출 보장, 개별 월세·수익성 판단.

## 8. 2보 전진 1보 후퇴 검토

1. 전진: 상가업소번호 단위 POI를 버리지 않고 보존했다.
2. 전진: 행정동/자치구×업종 집계를 만들어 당장 UI 후보와 경쟁 프록시로 쓸 수 있게 했다.
3. 후퇴 검토: 상권 polygon 매칭 전에는 상권별 점수로 직접 쓰지 않도록 `점수직접사용상태`를 보류로 남겼다.
4. 재검토 결과: SBDC 원천은 위치와 업종이 강하므로 버릴 데이터가 아니라, 공간 매칭 전 단계의 핵심 silver로 유지하는 것이 맞다.
"""
    MD_REPORT_PATH.write_text(report, encoding="utf-8")


def main() -> None:
    ensure_dirs()
    source_audit = find_source_files()
    write_csv(source_audit, SOURCE_AUDIT_PATH)
    source_path = choose_seoul_source(source_audit)
    source_info = extract_store_file_info(source_path)
    if source_info is None:
        raise RuntimeError("선택된 SBDC 서울 파일에서 기준년월을 해석하지 못했습니다.")
    source_ym = source_info["source_ym"]

    for path in [SEOUL_POINT_PATH, ISSUE_SAMPLE_PATH]:
        if path.exists():
            path.unlink()

    first_write = True
    row_offset = 0
    source_row_count = 0
    missing_required_columns: set[str] | None = None
    non_seoul_rows = 0
    null_store_id_rows = 0
    duplicate_store_id_rows = 0
    null_small_industry_rows = 0
    invalid_coordinate_rows = 0
    outside_seoul_bbox_rows = 0
    missing_address_rows = 0
    seen_store_ids: set[str] = set()
    duplicate_store_ids: set[str] = set()
    issue_samples: list[pd.DataFrame] = []
    hdong_parts: list[pd.DataFrame] = []
    sgg_parts: list[pd.DataFrame] = []
    industry_parts: list[pd.DataFrame] = []

    dtype = {col: "string" for col in TEXT_COLUMNS + ["경도", "위도"]}
    for raw_chunk in pd.read_csv(source_path, encoding="utf-8-sig", dtype=dtype, chunksize=CHUNK_SIZE, low_memory=False):
        if missing_required_columns is None:
            missing_required_columns = set(REQUIRED_COLUMNS) - set(raw_chunk.columns)
        chunk = normalize_chunk(raw_chunk, source_path, source_ym, row_offset)
        source_row_count += len(chunk)
        row_offset += len(chunk)

        store_ids = chunk["상가업소번호"].fillna("").astype(str)
        null_store_id_rows += int(store_ids.eq("").sum())
        for store_id in store_ids[store_ids.ne("")]:
            if store_id in seen_store_ids:
                duplicate_store_ids.add(store_id)
            else:
                seen_store_ids.add(store_id)

        non_seoul_rows += int(chunk["시도명"].ne("서울특별시").sum())
        null_small_industry_rows += int(chunk["상권업종소분류코드"].eq("").sum())
        invalid_coordinate_rows += int((~chunk["좌표유효여부"]).sum())
        outside_seoul_bbox_rows += int((chunk["좌표유효여부"] & ~chunk["서울광역좌표범위여부"]).sum())
        missing_address_rows += int((~chunk["주소보유여부"]).sum())

        issue_mask = (
            store_ids.eq("")
            | chunk["상권업종소분류코드"].eq("")
            | (~chunk["좌표유효여부"])
            | (chunk["좌표유효여부"] & ~chunk["서울광역좌표범위여부"])
            | (~chunk["주소보유여부"])
        )
        if issue_mask.any() and sum(len(x) for x in issue_samples) < 200:
            sample_cols = [
                "원천행번호",
                "상가업소번호",
                "상호명",
                "상권업종소분류코드",
                "상권업종소분류명",
                "시군구명",
                "행정동명",
                "지번주소",
                "도로명주소",
                "경도",
                "위도",
                "좌표유효여부",
                "서울광역좌표범위여부",
            ]
            issue_samples.append(chunk.loc[issue_mask, sample_cols].head(200 - sum(len(x) for x in issue_samples)))

        hdong_keys = [
            "기준_년월",
            "기준_년분기_코드",
            "시도코드",
            "시도명",
            "시군구코드",
            "시군구명",
            "행정동코드",
            "행정동명",
            "상권업종대분류코드",
            "상권업종대분류명",
            "상권업종중분류코드",
            "상권업종중분류명",
            "상권업종소분류코드",
            "상권업종소분류명",
        ]
        sgg_keys = [
            "기준_년월",
            "기준_년분기_코드",
            "시도코드",
            "시도명",
            "시군구코드",
            "시군구명",
            "상권업종대분류코드",
            "상권업종대분류명",
            "상권업종중분류코드",
            "상권업종중분류명",
            "상권업종소분류코드",
            "상권업종소분류명",
        ]
        industry_keys = [
            "기준_년월",
            "상권업종대분류코드",
            "상권업종대분류명",
            "상권업종중분류코드",
            "상권업종중분류명",
            "상권업종소분류코드",
            "상권업종소분류명",
            "표준산업분류코드",
            "표준산업분류명",
        ]
        hdong_parts.append(aggregate_count(chunk, hdong_keys))
        sgg_parts.append(aggregate_count(chunk, sgg_keys))
        industry_parts.append(aggregate_count(chunk, industry_keys))

        append_csv(chunk, SEOUL_POINT_PATH, first_write=first_write)
        first_write = False

    duplicate_store_id_rows = len(duplicate_store_ids)
    if issue_samples:
        write_csv(pd.concat(issue_samples, ignore_index=True), ISSUE_SAMPLE_PATH)
    else:
        write_csv(pd.DataFrame(columns=["이슈없음"]), ISSUE_SAMPLE_PATH)

    hdong_keys = [
        "기준_년월",
        "기준_년분기_코드",
        "시도코드",
        "시도명",
        "시군구코드",
        "시군구명",
        "행정동코드",
        "행정동명",
        "상권업종대분류코드",
        "상권업종대분류명",
        "상권업종중분류코드",
        "상권업종중분류명",
        "상권업종소분류코드",
        "상권업종소분류명",
    ]
    sgg_keys = [
        "기준_년월",
        "기준_년분기_코드",
        "시도코드",
        "시도명",
        "시군구코드",
        "시군구명",
        "상권업종대분류코드",
        "상권업종대분류명",
        "상권업종중분류코드",
        "상권업종중분류명",
        "상권업종소분류코드",
        "상권업종소분류명",
    ]
    industry_keys = [
        "기준_년월",
        "상권업종대분류코드",
        "상권업종대분류명",
        "상권업종중분류코드",
        "상권업종중분류명",
        "상권업종소분류코드",
        "상권업종소분류명",
        "표준산업분류코드",
        "표준산업분류명",
    ]
    hdong = merge_aggregates(hdong_parts, hdong_keys)
    sgg = merge_aggregates(sgg_parts, sgg_keys)
    observed_codebook = merge_aggregates(industry_parts, industry_keys)

    hierarchy = pd.read_csv(SILVER_DIR / "silver_industry_hierarchy_sbdc.csv", encoding="utf-8-sig", dtype=str)
    hierarchy_codes = set(hierarchy["소분류코드"].fillna("").astype(str))
    observed_codes = set(observed_codebook["상권업종소분류코드"].fillna("").astype(str)) - {""}
    unmatched_small_codes = sorted(observed_codes - hierarchy_codes)
    observed_codebook["SBDC_계층마스터_매칭여부"] = observed_codebook["상권업종소분류코드"].isin(hierarchy_codes)
    observed_codebook["source_id"] = SOURCE_ID
    observed_codebook["provider"] = PROVIDER
    observed_codebook["snapshot_date"] = SNAPSHOT_DATE
    observed_codebook["notes_ko"] = "서울 상가업소 원천에서 실제 관측된 업종 코드. 서울 서비스업종 매핑은 별도 bridge에서 검토한다."

    for df in [hdong, sgg]:
        df["source_id"] = SOURCE_ID
        df["provider"] = PROVIDER
        df["snapshot_date"] = SNAPSHOT_DATE
        df["usage_role"] = "상권 polygon 매칭 전 행정구역 단위 점포 밀집/경쟁 프록시"
        df["score_use_status"] = "조건부: 상권 공간매칭 뒤 상권 점수에 반영"

    write_csv(hdong, HDONG_INDUSTRY_PATH)
    write_csv(sgg, SGG_INDUSTRY_PATH)
    write_csv(observed_codebook, OBSERVED_CODEBOOK_PATH)

    metrics: dict[str, Any] = {
        "source_ym": source_ym,
        "source_row_count": source_row_count,
        "point_row_count": 0,
        "missing_required_columns": len(missing_required_columns or set()),
        "non_seoul_rows": non_seoul_rows,
        "national_source_file_count": len(source_audit),
        "national_row_count_line_scan": int(source_audit["row_count_line_scan"].sum()),
        "null_store_id_rows": null_store_id_rows,
        "duplicate_store_id_rows": duplicate_store_id_rows,
        "null_small_industry_rows": null_small_industry_rows,
        "invalid_coordinate_rows": invalid_coordinate_rows,
        "outside_seoul_bbox_rows": outside_seoul_bbox_rows,
        "missing_address_rows": missing_address_rows,
        "hdong_sum": int(hdong["점포수"].sum()),
        "sgg_sum": int(sgg["점포수"].sum()),
        "hdong_rows": len(hdong),
        "sgg_rows": len(sgg),
        "observed_codebook_rows": len(observed_codebook),
        "unmatched_small_code_count": len(unmatched_small_codes),
    }
    # 출력 CSV의 행 수를 다시 세어 원천 행 수와 실제 산출물 행 수가 같은지 검증한다.
    metrics["point_row_count"] = count_csv_rows_by_newline(SEOUL_POINT_PATH)

    domain, grain, consistency = build_validation_rows(metrics)
    write_csv(domain, DOMAIN_VALIDATION_PATH)
    write_csv(grain, GRAIN_VALIDATION_PATH)
    write_csv(consistency, CONSISTENCY_VALIDATION_PATH)
    write_report(source_path, source_audit, domain, grain, consistency, metrics)

    print("SBDC 서울 상가업소 silver 생성 완료")
    print(f"source_rows={metrics['source_row_count']:,}")
    print(f"point_rows={metrics['point_row_count']:,}")
    print(f"hdong_rows={metrics['hdong_rows']:,}")
    print(f"sgg_rows={metrics['sgg_rows']:,}")
    print(f"observed_codebook_rows={metrics['observed_codebook_rows']:,}")
    print(f"unmatched_small_codes={metrics['unmatched_small_code_count']:,}")


if __name__ == "__main__":
    main()
