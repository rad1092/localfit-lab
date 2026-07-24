from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from ingest_common import latest_raw_path, raw_run_date, raw_snapshot_date


ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "datacorpus" / "_raw_ingest"
SILVER_DIR = ROOT / "datacorpus" / "_silver"
VALIDATION_DIR = ROOT / "datacorpus" / "_rule_validation"
RESEARCH_VALIDATION_DIR = ROOT / "research" / "rule_validation"

SERVICE = "VwsmTrdarFcltyQq"
RAW_PATH = latest_raw_path(
    "seoul_open_data", "full", SERVICE, required_glob=f"{SERVICE}_*.json"
)
TRADE_AREA_MASTER_PATH = SILVER_DIR / "silver_trade_area_master.csv"

RAW_RUN_DATE = raw_run_date(RAW_PATH)
SNAPSHOT_DATE = raw_snapshot_date(RAW_PATH)
PROVIDER = "서울열린데이터광장"
SOURCE_ID = "seoul_facility_trade_area"
KEY_COLS = ["기준_년분기_코드", "상권_코드"]

COLUMNS = {
    "STDR_YYQU_CD": "기준_년분기_코드",
    "TRDAR_SE_CD": "상권_구분_코드",
    "TRDAR_SE_CD_NM": "상권_구분_코드_명",
    "TRDAR_CD": "상권_코드",
    "TRDAR_CD_NM": "상권_코드_명",
    "VIATR_FCLTY_CO": "총_집객시설_수",
    "PBLOFC_CO": "공공기관_수",
    "BANK_CO": "은행_수",
    "GEHSPT_CO": "종합병원_수",
    "GNRL_HSPTL_CO": "일반병원_수",
    "PARMACY_CO": "약국_수",
    "KNDRGR_CO": "유치원_수",
    "ELESCH_CO": "초등학교_수",
    "MSKUL_CO": "중학교_수",
    "HGSCHL_CO": "고등학교_수",
    "UNIV_CO": "대학교_수",
    "DRTS_CO": "백화점_수",
    "SUPMK_CO": "슈퍼마켓_수",
    "THEAT_CO": "극장_수",
    "STAYNG_FCLTY_CO": "숙박시설_수",
    "ARPRT_CO": "공항_수",
    "RLROAD_STATN_CO": "철도역_수",
    "BUS_TRMINL_CO": "버스터미널_수",
    "SUBWAY_STATN_CO": "지하철역_수",
    "BUS_STTN_CO": "버스정류장_수",
}

FACILITY_GROUPS = {
    "공공": ["공공기관_수"],
    "금융": ["은행_수"],
    "의료": ["종합병원_수", "일반병원_수", "약국_수"],
    "교육": ["유치원_수", "초등학교_수", "중학교_수", "고등학교_수", "대학교_수"],
    "상업문화": ["백화점_수", "슈퍼마켓_수", "극장_수", "숙박시설_수"],
    "교통": ["공항_수", "철도역_수", "버스터미널_수", "지하철역_수", "버스정류장_수"],
}


def ensure_dirs() -> None:
    for path in [SILVER_DIR, VALIDATION_DIR, RESEARCH_VALIDATION_DIR]:
        path.mkdir(parents=True, exist_ok=True)


def read_openapi_pages() -> tuple[pd.DataFrame, int, int]:
    rows: list[dict[str, Any]] = []
    totals: set[int] = set()
    page_paths = sorted(RAW_PATH.glob(f"{SERVICE}_*.json"))
    if not page_paths:
        raise FileNotFoundError(f"{RAW_PATH} 아래에서 {SERVICE} 원응답을 찾지 못했습니다.")

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
        raise ValueError(f"{SERVICE} list_total_count가 하나로 고정되지 않았습니다: {sorted(totals)}")
    return pd.DataFrame(rows), len(page_paths), next(iter(totals))


def normalize_codes(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in ["기준_년분기_코드", "상권_구분_코드", "상권_코드"]:
        out[col] = out[col].astype(str).str.strip()
    for col in ["상권_구분_코드_명", "상권_코드_명"]:
        out[col] = out[col].astype(str).str.strip()
    return out


def facility_count_cols() -> list[str]:
    return list(COLUMNS.values())[5:]


def facility_part_cols() -> list[str]:
    return facility_count_cols()[1:]


def build_facility_table() -> tuple[pd.DataFrame, int, int]:
    raw, page_count, api_total = read_openapi_pages()
    df = raw.rename(columns=COLUMNS)
    expected = list(COLUMNS.values())
    missing = [col for col in expected if col not in df.columns]
    if missing:
        raise ValueError(f"집객시설 컬럼 변환 후 누락 컬럼: {missing}")
    df = df[expected]
    df = normalize_codes(df)
    for col in facility_count_cols():
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # 총 집객시설 수는 제공 유형들의 단순 합보다 큰 경우가 많다.
    # 따라서 유형합 불일치를 실패로 보지 않고, 유형에 포함되지 않은 집객시설의 최소 잔여량으로만 기록한다.
    df["유형분류_시설수_합"] = df[facility_part_cols()].sum(axis=1)
    df["유형분류_미포함_추정시설_수"] = df["총_집객시설_수"] - df["유형분류_시설수_합"]
    df["quality_negative_facility_cell_count"] = (df[facility_count_cols()] < 0).sum(axis=1)
    df["quality_type_sum_exceeds_total"] = df["유형분류_시설수_합"] > df["총_집객시설_수"]

    for group, cols in FACILITY_GROUPS.items():
        df[f"{group}_시설_수"] = df[cols].sum(axis=1)

    df["source_id"] = SOURCE_ID
    df["provider"] = PROVIDER
    df["source_service"] = SERVICE
    df["snapshot_date"] = SNAPSHOT_DATE
    df["source_grain"] = "기준년분기+상권코드"
    df["raw_page_count"] = page_count
    df["api_list_total_count"] = api_total
    df["raw_row_count"] = len(df)
    df["directness_level"] = "P0_공식_상권_집계_프록시"
    df["forbidden_claim_ko"] = "실제 방문확률, 실제 유입 인원, 매출 보장으로 표현 금지"
    df["notes_ko"] = "상권 내 집객시설 수를 제공하는 접근성/앵커시설 원천이다. 시설 수는 실제 방문객 수가 아니므로 상대 흡인력 프록시로만 사용한다."
    return df.sort_values(KEY_COLS).reset_index(drop=True), page_count, api_total


def build_facility_codebook() -> pd.DataFrame:
    rows = []
    group_by_col = {}
    for group, cols in FACILITY_GROUPS.items():
        for col in cols:
            group_by_col[col] = group
    for col in facility_count_cols():
        if col == "총_집객시설_수":
            role = "전체 앵커시설 총량"
            group = "전체"
        else:
            role = "시설 유형별 보조 지표"
            group = group_by_col.get(col, "기타")
        rows.append(
            {
                "facility_column": col,
                "facility_group": group,
                "usage_role": role,
                "score_use_warning_ko": "시설 수는 실제 방문객 수가 아니므로 단독 점수보다 수요·교통·매출과 함께 상대 흡인력 프록시로 사용한다.",
                "source_id": SOURCE_ID,
                "provider": PROVIDER,
                "snapshot_date": SNAPSHOT_DATE,
            }
        )
    return pd.DataFrame(rows)


def load_trade_area_codes() -> set[str]:
    if not TRADE_AREA_MASTER_PATH.exists():
        return set()
    df = pd.read_csv(TRADE_AREA_MASTER_PATH, encoding="utf-8-sig", dtype=str).fillna("")
    return set(df["상권_코드"].astype(str).str.strip()) if "상권_코드" in df.columns else set()


def key_null_cells(df: pd.DataFrame) -> int:
    return sum(int((df[col].isna() | df[col].astype(str).str.strip().eq("")).sum()) for col in KEY_COLS)


def duplicate_key_rows(df: pd.DataFrame) -> int:
    return int(df.duplicated(KEY_COLS).sum())


def validate_facility(df: pd.DataFrame, codebook: pd.DataFrame, page_count: int, api_total: int) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    master_codes = load_trade_area_codes()
    observed_codes = set(df["상권_코드"])
    extra_codes = observed_codes - master_codes if master_codes else set()
    master_missing_codes = master_codes - observed_codes if master_codes else set()
    count_cols = facility_count_cols()
    key_null = key_null_cells(df)
    dup = duplicate_key_rows(df)
    negative_cells = int((df[count_cols] < 0).sum().sum())
    null_cells = int(df[count_cols].isna().sum().sum())
    type_sum_exceeds_total = int(df["quality_type_sum_exceeds_total"].sum())
    quarter_area_counts = df.groupby("기준_년분기_코드")["상권_코드"].nunique()
    quarter_area_count_min = int(quarter_area_counts.min())
    quarter_area_count_max = int(quarter_area_counts.max())
    hard_fail = (
        len(df) != api_total
        or key_null != 0
        or dup != 0
        or len(extra_codes) != 0
        or negative_cells != 0
        or null_cells != 0
        or type_sum_exceeds_total != 0
    )
    conditional = len(master_missing_codes) != 0
    judgement = "FAIL" if hard_fail else ("조건부 PASS" if conditional else "PASS")

    domain_df = pd.DataFrame(
        [
            {
                "table": "silver_facility_trade_area_q",
                "rows": len(df),
                "api_total_count": api_total,
                "raw_page_count": page_count,
                "row_count_matches_api": len(df) == api_total,
                "quarter_min": df["기준_년분기_코드"].min(),
                "quarter_max": df["기준_년분기_코드"].max(),
                "quarter_count": df["기준_년분기_코드"].nunique(),
                "area_count": df["상권_코드"].nunique(),
                "quarter_area_count_min": quarter_area_count_min,
                "quarter_area_count_max": quarter_area_count_max,
                "key_null_cells": key_null,
                "duplicate_key_rows": dup,
                "facility_count_negative_cells": negative_cells,
                "facility_count_null_cells": null_cells,
                "area_codes_missing_from_master": len(extra_codes),
                "master_area_codes_not_in_source": len(master_missing_codes),
                "type_sum_exceeds_total_rows": type_sum_exceeds_total,
                "type_sum_less_than_total_rows": int((df["유형분류_시설수_합"] < df["총_집객시설_수"]).sum()),
                "judgement": judgement,
                "conditional_reason_ko": "원천에 없는 상권 72개를 0으로 임의 대체하지 않고 커버리지 차이로 보존" if conditional else "",
            },
            {
                "table": "silver_facility_codebook",
                "rows": len(codebook),
                "api_total_count": "",
                "raw_page_count": "",
                "row_count_matches_api": "",
                "quarter_min": "",
                "quarter_max": "",
                "quarter_count": "",
                "area_count": "",
                "quarter_area_count_min": "",
                "quarter_area_count_max": "",
                "key_null_cells": int((codebook["facility_column"].astype(str).str.len() == 0).sum()),
                "duplicate_key_rows": int(codebook.duplicated(["facility_column"]).sum()),
                "facility_count_negative_cells": "",
                "facility_count_null_cells": "",
                "area_codes_missing_from_master": "",
                "master_area_codes_not_in_source": "",
                "type_sum_exceeds_total_rows": "",
                "type_sum_less_than_total_rows": "",
                "judgement": "PASS",
                "conditional_reason_ko": "",
            },
        ]
    )
    grain_df = pd.DataFrame(
        [
            {
                "table": "silver_facility_trade_area_q",
                "key_cols": " + ".join(KEY_COLS),
                "duplicate_key_rows": dup,
                "key_null_cells": key_null,
                "judgement": "PASS" if dup == 0 and key_null == 0 else "FAIL",
                "reason_ko": "집객시설은 업종 단위가 아니라 분기+상권 grain이다. 업종별 점수에는 상권 단위 접근성 보조축으로 조인한다.",
            },
            {
                "table": "silver_facility_codebook",
                "key_cols": "facility_column",
                "duplicate_key_rows": int(codebook.duplicated(["facility_column"]).sum()),
                "key_null_cells": int((codebook["facility_column"].astype(str).str.len() == 0).sum()),
                "judgement": "PASS",
                "reason_ko": "시설 컬럼별 역할과 점수화 경고를 분리해 리포트와 알고리즘 주석에 재사용한다.",
            },
        ]
    )
    contract_df = pd.DataFrame(
        [
            {
                "table": "silver_facility_trade_area_q",
                "source_id": SOURCE_ID,
                "provider": PROVIDER,
                "source_service": SERVICE,
                "rows": len(df),
                "contract_status": judgement,
                "usage_role": "접근성/유입, 앵커시설, Huff-lite/2SFCA 보조 프록시",
            },
            {
                "table": "silver_facility_codebook",
                "source_id": SOURCE_ID,
                "provider": PROVIDER,
                "source_service": SERVICE,
                "rows": len(codebook),
                "contract_status": "PASS",
                "usage_role": "시설 유형별 해석과 과장 금지 문구 관리",
            },
        ]
    )
    return domain_df, grain_df, contract_df


def write_validation_md(domain_df: pd.DataFrame, grain_df: pd.DataFrame, codebook: pd.DataFrame) -> None:
    path = RESEARCH_VALIDATION_DIR / "06_facility_silver_validation_20260703.md"
    main = domain_df.loc[domain_df["table"].eq("silver_facility_trade_area_q")].iloc[0].to_dict()
    lines = [
        "# 6회차 집객시설 silver 전처리 검증",
        "",
        f"작성시각: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "## 대상 파일",
        "",
        "- `datacorpus/_silver/silver_facility_trade_area_q.csv`",
        "- `datacorpus/_silver/silver_facility_codebook.csv`",
        "",
        "## 사용 근거",
        "",
        "- `datacorpus/_raw_ingest/source_registry.csv`: 집객시설은 접근성/유입 P0 원천으로 등록되어 있다.",
        "- `datacorpus/_raw_ingest/seoul_core_coverage_audit.csv`: 전체 API 원응답 행 수가 API 총 건수와 일치한다고 기록되어 있다.",
        "- `research/전처리_알고리즘_실행계획_20260703.md`: 집객시설은 앵커시설, 접근성, Huff-lite/2SFCA 보조 입력으로 지정되어 있다.",
        "- `research/algorithm_evidence_sources/data_docs/seoul_open_data_facility_trade_area.html`: 집객시설은 1년 중 4분기에 한 번 업데이트되고 다음 해 1~3분기 값이 동일하다는 원천 설명이 있다.",
        "- `research/site_selection_sources/09_esri_huff_model.html`: Huff류 모델은 거리와 매력도, 경쟁지를 함께 봐야 하며 보정 없이 방문확률로 표현하면 안 된다는 근거가 있다.",
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
            "판단: 집객시설 원천 row 수와 API 총 건수는 일치한다. 다만 상권 마스터 1,650개 중 원천에 관측된 상권은 1,578개다.",
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
            "판단: 집객시설은 업종 단위가 아니라 `기준_년분기_코드 + 상권_코드` 단위다. 업종별 행에는 상권 단위 접근성 보조축으로만 조인한다.",
            "",
            "## 검증 3: 값 범위와 커버리지",
            "",
            "| 항목 | 값 |",
            "|---|---:|",
            f"| 음수 시설수 셀 | {main['facility_count_negative_cells']} |",
            f"| null 시설수 셀 | {main['facility_count_null_cells']} |",
            f"| 상권 마스터에 없는 원천 상권 코드 | {main['area_codes_missing_from_master']} |",
            f"| 원천에 없는 상권 마스터 코드 | {main['master_area_codes_not_in_source']} |",
            f"| 유형합이 총 집객시설 수를 초과한 row | {main['type_sum_exceeds_total_rows']} |",
            f"| 유형합이 총 집객시설 수보다 작은 row | {main['type_sum_less_than_total_rows']} |",
            "",
            "판단: 원천에 없는 72개 상권을 임의로 0으로 채우지 않는다. 시설이 실제로 0인지, 원천 제공 대상에서 제외된 것인지 확인되지 않았기 때문이다.",
            "",
            "## 검증 4: 시설 코드북",
            "",
            "| facility_column | group | usage_role |",
            "|---|---|---|",
        ]
    )
    for row in codebook.to_dict("records"):
        lines.append(f"| `{row['facility_column']}` | {row['facility_group']} | {row['usage_role']} |")

    lines.extend(
        [
            "",
            "## 2보 전진 1보 후퇴 기록",
            "",
            "- 전진 1: 접근성/앵커시설 P0 원천인 집객시설을 전체 raw 기준으로 silver화했다.",
            "- 전진 2: 시설 유형별 그룹과 전체 집객시설 수를 모두 보존했다.",
            "- 후퇴 1: 총 집객시설 수를 유형별 시설 수의 단순 합으로 검증하지 않는다. 원천의 총 시설 수에는 제공 유형 외 시설이 포함될 수 있다.",
            "- 후퇴 2: 집객시설은 실제 방문객 수가 아니므로 Huff-lite에서 방문확률이 아니라 상대 흡인력 프록시로만 사용한다.",
            "- 후퇴 3: 원천 문서상 집객시설은 4분기 갱신 후 다음 해 1~3분기 값이 동일하므로, 단기 분기 변화 신호로 사용하지 않는다.",
            "",
            "## 다음 작업",
            "",
            "1. 버스정류소 위치 silver 전처리.",
            "2. 지하철역 마스터 silver 전처리.",
            "3. 버스/지하철 승하차량 월별 silver 전처리.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def append_progress(domain_df: pd.DataFrame) -> None:
    path = ROOT / "research" / "전처리_진행기록_20260703.md"
    if not path.exists():
        return
    main = domain_df.loc[domain_df["table"].eq("silver_facility_trade_area_q")].iloc[0].to_dict()
    codebook = domain_df.loc[domain_df["table"].eq("silver_facility_codebook")].iloc[0].to_dict()
    block = [
        "",
        "---",
        "",
        "## 8. 완료된 집객시설 silver 테이블",
        "",
        "| 산출물 | row 수 | 상태 | 역할 |",
        "|---|---:|---|---|",
        f"| `datacorpus/_silver/silver_facility_trade_area_q.csv` | {main['rows']:,} | {main['judgement']} | 접근성/앵커시설 상권 단위 신호 |",
        f"| `datacorpus/_silver/silver_facility_codebook.csv` | {codebook['rows']:,} | {codebook['judgement']} | 시설 유형 해석 |",
        "",
        "검증 근거:",
        "",
        "- `datacorpus/_rule_validation/06_facility_domain_validation.csv`",
        "- `datacorpus/_rule_validation/06_facility_grain_validation.csv`",
        "- `datacorpus/_rule_validation/06_facility_source_contract.csv`",
        "- `research/rule_validation/06_facility_silver_validation_20260703.md`",
        "",
        "판단:",
        "",
        "- 집객시설은 업종 단위가 아니라 `기준_년분기_코드 + 상권_코드` 단위다.",
        "- 시설 수는 실제 방문객 수가 아니라 접근성/흡인력 프록시다.",
        "- 원천에 없는 72개 상권은 0으로 임의 대체하지 않고 커버리지 차이로 남긴다.",
        "- 원천 문서상 4분기 갱신 성격이 있으므로 단기 분기 변화 지표로 쓰지 않는다.",
    ]
    text = path.read_text(encoding="utf-8")
    marker = "## 8. 완료된 집객시설 silver 테이블"
    if marker in text:
        text = text.split("\n---\n\n## 8. 완료된 집객시설 silver 테이블")[0].rstrip()
    path.write_text(text.rstrip() + "\n" + "\n".join(block) + "\n", encoding="utf-8")


def main() -> None:
    ensure_dirs()
    df, page_count, api_total = build_facility_table()
    codebook = build_facility_codebook()
    domain_df, grain_df, contract_df = validate_facility(df, codebook, page_count, api_total)

    df.to_csv(SILVER_DIR / "silver_facility_trade_area_q.csv", index=False, encoding="utf-8-sig")
    codebook.to_csv(SILVER_DIR / "silver_facility_codebook.csv", index=False, encoding="utf-8-sig")
    domain_df.to_csv(VALIDATION_DIR / "06_facility_domain_validation.csv", index=False, encoding="utf-8-sig")
    grain_df.to_csv(VALIDATION_DIR / "06_facility_grain_validation.csv", index=False, encoding="utf-8-sig")
    contract_df.to_csv(VALIDATION_DIR / "06_facility_source_contract.csv", index=False, encoding="utf-8-sig")
    write_validation_md(domain_df, grain_df, codebook)
    append_progress(domain_df)

    summary = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "rows": len(df),
        "codebook_rows": len(codebook),
        "validation_judgements": domain_df[["table", "judgement"]].to_dict("records"),
        "outputs": [
            "datacorpus/_silver/silver_facility_trade_area_q.csv",
            "datacorpus/_silver/silver_facility_codebook.csv",
            "datacorpus/_rule_validation/06_facility_domain_validation.csv",
            "datacorpus/_rule_validation/06_facility_grain_validation.csv",
            "datacorpus/_rule_validation/06_facility_source_contract.csv",
            "research/rule_validation/06_facility_silver_validation_20260703.md",
        ],
    }
    (VALIDATION_DIR / "06_facility_preprocess_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
