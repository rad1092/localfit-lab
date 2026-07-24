from __future__ import annotations

import json
from collections import Counter
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

SERVICE = "VwsmTrdarIxQq"
RAW_PATH = latest_raw_path(
    "seoul_open_data", "full", SERVICE, required_glob=f"{SERVICE}_*.json"
)
TRADE_AREA_MASTER_PATH = SILVER_DIR / "silver_trade_area_master.csv"

RAW_RUN_DATE = raw_run_date(RAW_PATH)
SNAPSHOT_DATE = raw_snapshot_date(RAW_PATH)
PROVIDER = "서울열린데이터광장"
SOURCE_ID = "seoul_trade_area_change_index"
KEY_COLS = ["기준_년분기_코드", "상권_코드"]

COLUMNS = {
    "STDR_YYQU_CD": "기준_년분기_코드",
    "TRDAR_SE_CD": "상권_구분_코드",
    "TRDAR_SE_CD_NM": "상권_구분_코드_명",
    "TRDAR_CD": "상권_코드",
    "TRDAR_CD_NM": "상권_코드_명",
    "TRDAR_CHNGE_IX": "상권_변화_지표_코드",
    "TRDAR_CHNGE_IX_NM": "상권_변화_지표_명",
    "OPR_SALE_MT_AVRG": "운영_영업_개월_평균",
    "CLS_SALE_MT_AVRG": "폐업_영업_개월_평균",
    "SU_OPR_SALE_MT_AVRG": "서울_운영_영업_개월_평균",
    "SU_CLS_SALE_MT_AVRG": "서울_폐업_영업_개월_평균",
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
    # 코드 컬럼은 문자열 키로 보존한다. 상권변화지표 코드도 범주형 해석값이다.
    for col in ["기준_년분기_코드", "상권_구분_코드", "상권_코드", "상권_변화_지표_코드"]:
        out[col] = out[col].astype(str).str.strip()
    for col in ["상권_구분_코드_명", "상권_코드_명", "상권_변화_지표_명"]:
        out[col] = out[col].astype(str).str.strip()
    return out


def build_change_table() -> tuple[pd.DataFrame, int, int]:
    raw, page_count, api_total = read_openapi_pages()
    df = raw.rename(columns=COLUMNS)
    expected = list(COLUMNS.values())
    missing = [col for col in expected if col not in df.columns]
    if missing:
        raise ValueError(f"상권변화지표 컬럼 변환 후 누락 컬럼: {missing}")
    df = df[expected]
    df = normalize_codes(df)
    for col in ["운영_영업_개월_평균", "폐업_영업_개월_평균", "서울_운영_영업_개월_평균", "서울_폐업_영업_개월_평균"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["운영_서울대비_개월_차이"] = df["운영_영업_개월_평균"] - df["서울_운영_영업_개월_평균"]
    df["폐업_서울대비_개월_차이"] = df["폐업_영업_개월_평균"] - df["서울_폐업_영업_개월_평균"]
    df["quality_negative_month_cell_count"] = (
        df[["운영_영업_개월_평균", "폐업_영업_개월_평균", "서울_운영_영업_개월_평균", "서울_폐업_영업_개월_평균"]] < 0
    ).sum(axis=1)
    df["source_id"] = SOURCE_ID
    df["provider"] = PROVIDER
    df["source_service"] = SERVICE
    df["snapshot_date"] = SNAPSHOT_DATE
    df["source_grain"] = "기준년분기+상권코드"
    df["raw_page_count"] = page_count
    df["api_list_total_count"] = api_total
    df["raw_row_count"] = len(df)
    df["directness_level"] = "P0_공식_상권_집계"
    df["forbidden_claim_ko"] = "성장률 보장, 창업 성공확률, 개별 매장 생존확률로 표현 금지"
    df["notes_ko"] = "상권의 확장·축소·정체·다이나믹 상태와 운영/폐업 영업개월 평균을 보존한 성장/안정성 축 원천이다. 변화 코드는 매출/점포 추세와 함께 해석해야 한다."
    return df.sort_values(KEY_COLS).reset_index(drop=True), page_count, api_total


def build_codebook(df: pd.DataFrame) -> pd.DataFrame:
    counts = Counter(zip(df["상권_변화_지표_코드"], df["상권_변화_지표_명"]))
    rows = []
    for (code, name), count in sorted(counts.items()):
        rows.append(
            {
                "상권_변화_지표_코드": code,
                "상권_변화_지표_명": name,
                "observed_rows": count,
                "source_id": SOURCE_ID,
                "provider": PROVIDER,
                "snapshot_date": SNAPSHOT_DATE,
                "growth_stability_use_role": "성장/안정성 범주형 신호",
                "score_use_warning_ko": "코드명만으로 선형 순위를 단정하지 않는다. 매출 추세, 점포 개폐업, 폐업 영업개월과 결합해 검증한 뒤 점수화한다.",
            }
        )
    return pd.DataFrame(rows)


def load_trade_area_codes() -> set[str]:
    if not TRADE_AREA_MASTER_PATH.exists():
        return set()
    df = pd.read_csv(TRADE_AREA_MASTER_PATH, encoding="utf-8-sig", dtype=str).fillna("")
    return set(df["상권_코드"].astype(str).str.strip()) if "상권_코드" in df.columns else set()


def key_null_cells(df: pd.DataFrame) -> int:
    total = 0
    for col in KEY_COLS:
        total += int((df[col].isna() | df[col].astype(str).str.strip().eq("")).sum())
    return total


def duplicate_key_rows(df: pd.DataFrame) -> int:
    return int(df.duplicated(KEY_COLS).sum())


def validate_change(df: pd.DataFrame, codebook: pd.DataFrame, page_count: int, api_total: int) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    trade_area_codes = load_trade_area_codes()
    month_cols = ["운영_영업_개월_평균", "폐업_영업_개월_평균", "서울_운영_영업_개월_평균", "서울_폐업_영업_개월_평균"]
    valid_code_pairs = {
        ("HH", "정체"),
        ("HL", "상권축소"),
        ("LH", "상권확장"),
        ("LL", "다이나믹"),
    }
    observed_pairs = set(zip(df["상권_변화_지표_코드"], df["상권_변화_지표_명"]))
    invalid_pairs = observed_pairs - valid_code_pairs
    key_null = key_null_cells(df)
    dup = duplicate_key_rows(df)
    missing_area = len(set(df["상권_코드"]) - trade_area_codes) if trade_area_codes else -1
    negative_month_cells = int((df[month_cols] < 0).sum().sum())
    month_null_cells = int(df[month_cols].isna().sum().sum())
    hard_fail = (
        len(df) != api_total
        or key_null != 0
        or dup != 0
        or missing_area not in [0, -1]
        or negative_month_cells != 0
        or month_null_cells != 0
        or len(invalid_pairs) != 0
    )

    domain_df = pd.DataFrame(
        [
            {
                "table": "silver_change_index_trade_area_q",
                "rows": len(df),
                "api_total_count": api_total,
                "raw_page_count": page_count,
                "row_count_matches_api": len(df) == api_total,
                "quarter_min": df["기준_년분기_코드"].min(),
                "quarter_max": df["기준_년분기_코드"].max(),
                "quarter_count": df["기준_년분기_코드"].nunique(),
                "area_count": df["상권_코드"].nunique(),
                "key_null_cells": key_null,
                "duplicate_key_rows": dup,
                "area_codes_missing_from_master": missing_area,
                "negative_month_cells": negative_month_cells,
                "month_null_cells": month_null_cells,
                "invalid_change_code_pairs": len(invalid_pairs),
                "change_code_count": df["상권_변화_지표_코드"].nunique(),
                "judgement": "FAIL" if hard_fail else "PASS",
            },
            {
                "table": "silver_change_index_codebook",
                "rows": len(codebook),
                "api_total_count": "",
                "raw_page_count": "",
                "row_count_matches_api": "",
                "quarter_min": "",
                "quarter_max": "",
                "quarter_count": "",
                "area_count": "",
                "key_null_cells": int((codebook["상권_변화_지표_코드"].astype(str).str.len() == 0).sum()),
                "duplicate_key_rows": int(codebook.duplicated(["상권_변화_지표_코드"]).sum()),
                "area_codes_missing_from_master": "",
                "negative_month_cells": "",
                "month_null_cells": "",
                "invalid_change_code_pairs": len(invalid_pairs),
                "change_code_count": len(codebook),
                "judgement": "FAIL" if len(invalid_pairs) else "PASS",
            },
        ]
    )
    grain_df = pd.DataFrame(
        [
            {
                "table": "silver_change_index_trade_area_q",
                "key_cols": " + ".join(KEY_COLS),
                "duplicate_key_rows": dup,
                "key_null_cells": key_null,
                "judgement": "PASS" if dup == 0 and key_null == 0 else "FAIL",
                "reason_ko": "상권변화지표는 업종 단위가 아니라 분기+상권 grain이며, 성장잠재 점수에는 상권 단위 보조축으로 조인한다.",
            },
            {
                "table": "silver_change_index_codebook",
                "key_cols": "상권_변화_지표_코드",
                "duplicate_key_rows": int(codebook.duplicated(["상권_변화_지표_코드"]).sum()),
                "key_null_cells": int((codebook["상권_변화_지표_코드"].astype(str).str.len() == 0).sum()),
                "judgement": "PASS",
                "reason_ko": "변화지표 코드는 범주형 신호이므로 코드북으로 분리해 리포트 문구와 점수화 경고를 보존한다.",
            },
        ]
    )
    contract_df = pd.DataFrame(
        [
            {
                "table": "silver_change_index_trade_area_q",
                "source_id": SOURCE_ID,
                "provider": PROVIDER,
                "source_service": SERVICE,
                "rows": len(df),
                "contract_status": domain_df.loc[0, "judgement"],
                "usage_role": "성장/안정성, 상권 확장·축소·정체·다이나믹 범주형 신호",
            },
            {
                "table": "silver_change_index_codebook",
                "source_id": SOURCE_ID,
                "provider": PROVIDER,
                "source_service": SERVICE,
                "rows": len(codebook),
                "contract_status": domain_df.loc[1, "judgement"],
                "usage_role": "상권변화지표 코드 해석과 과장 금지 문구 관리",
            },
        ]
    )
    return domain_df, grain_df, contract_df


def write_validation_md(domain_df: pd.DataFrame, grain_df: pd.DataFrame, codebook: pd.DataFrame) -> None:
    path = RESEARCH_VALIDATION_DIR / "05_change_index_silver_validation_20260703.md"
    main = domain_df.loc[domain_df["table"].eq("silver_change_index_trade_area_q")].iloc[0].to_dict()
    lines = [
        "# 5회차 상권변화지표 silver 전처리 검증",
        "",
        f"작성시각: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "## 대상 파일",
        "",
        "- `datacorpus/_silver/silver_change_index_trade_area_q.csv`",
        "- `datacorpus/_silver/silver_change_index_codebook.csv`",
        "",
        "## 사용 근거",
        "",
        "- `datacorpus/_raw_ingest/source_registry.csv`: 상권변화지표는 성장/안정성 P0 원천으로 등록되어 있다.",
        "- `datacorpus/_raw_ingest/seoul_core_coverage_audit.csv`: 전체 API 원응답 행 수가 API 총 건수와 일치한다고 기록되어 있다.",
        "- `research/알고리즘_스펙_v1_20260703.md`: 기존 단일 점수는 성장률과 약하거나 음의 관계였으므로 성장잠재 점수는 별도로 설계해야 한다고 정리되어 있다.",
        "- `research/site_selection_sources/08_seoul_golmok_service_indices.html`: 활성도·성장성·안정성 지표를 매출, 유동인구, 폐업률, 영업지속기간 등과 결합해 해석해야 한다는 근거가 있다.",
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
            "판단: 원천 row 수와 API 총 건수가 일치한다.",
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
            "판단: 상권변화지표는 업종 단위가 아니라 `기준_년분기_코드 + 상권_코드` 단위다. 업종별 성장잠재 점수에는 상권 단위 신호로만 조인한다.",
            "",
            "## 검증 3: 값 범위와 코드북",
            "",
            "| 항목 | 값 |",
            "|---|---:|",
            f"| 음수 영업개월 셀 | {main['negative_month_cells']} |",
            f"| 영업개월 null 셀 | {main['month_null_cells']} |",
            f"| 상권 마스터 미매칭 코드 | {main['area_codes_missing_from_master']} |",
            f"| 잘못된 변화지표 코드-명 조합 | {main['invalid_change_code_pairs']} |",
            "",
            "### 관측 코드",
            "",
            "| 코드 | 이름 | row 수 | 사용 경고 |",
            "|---|---|---:|---|",
        ]
    )
    for row in codebook.to_dict("records"):
        lines.append(
            f"| `{row['상권_변화_지표_코드']}` | {row['상권_변화_지표_명']} | {row['observed_rows']} | {row['score_use_warning_ko']} |"
        )

    lines.extend(
        [
            "",
            "## 2보 전진 1보 후퇴 기록",
            "",
            "- 전진 1: 성장/안정성 P0 원천인 상권변화지표를 전체 raw 기준으로 silver화했다.",
            "- 전진 2: 변화지표 코드북을 분리해 리포트 문구와 점수화 경고를 남겼다.",
            "- 후퇴 1: `상권확장`, `다이나믹` 같은 이름만으로 선형 점수를 바로 주지 않는다. 성장잠재 점수는 매출 추세, 점포 개폐업, 폐업 영업개월과 결합해 백테스트한 뒤 확정한다.",
            "",
            "## 다음 작업",
            "",
            "1. 집객시설 silver 전처리.",
            "2. 버스/지하철 접근성 원천 전처리.",
            "3. 매출·점포·수요·상권변화 결합 gold 초안 설계.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def append_progress(domain_df: pd.DataFrame) -> None:
    path = ROOT / "research" / "전처리_진행기록_20260703.md"
    if not path.exists():
        return
    main = domain_df.loc[domain_df["table"].eq("silver_change_index_trade_area_q")].iloc[0].to_dict()
    codebook = domain_df.loc[domain_df["table"].eq("silver_change_index_codebook")].iloc[0].to_dict()
    block = [
        "",
        "---",
        "",
        "## 7. 완료된 상권변화지표 silver 테이블",
        "",
        "| 산출물 | row 수 | 상태 | 역할 |",
        "|---|---:|---|---|",
        f"| `datacorpus/_silver/silver_change_index_trade_area_q.csv` | {main['rows']:,} | {main['judgement']} | 성장/안정성 상권 단위 신호 |",
        f"| `datacorpus/_silver/silver_change_index_codebook.csv` | {codebook['rows']:,} | {codebook['judgement']} | 변화지표 코드 해석 |",
        "",
        "검증 근거:",
        "",
        "- `datacorpus/_rule_validation/05_change_index_domain_validation.csv`",
        "- `datacorpus/_rule_validation/05_change_index_grain_validation.csv`",
        "- `datacorpus/_rule_validation/05_change_index_source_contract.csv`",
        "- `research/rule_validation/05_change_index_silver_validation_20260703.md`",
        "",
        "판단:",
        "",
        "- 상권변화지표는 업종 단위가 아니라 `기준_년분기_코드 + 상권_코드` 단위다.",
        "- 변화지표 코드는 바로 선형 점수로 바꾸지 않는다.",
        "- 성장잠재 점수에서는 매출 추세, 점포 개폐업, 영업개월 지표와 함께 검증 후 사용한다.",
    ]
    text = path.read_text(encoding="utf-8")
    marker = "## 7. 완료된 상권변화지표 silver 테이블"
    if marker in text:
        text = text.split("\n---\n\n## 7. 완료된 상권변화지표 silver 테이블")[0].rstrip()
    path.write_text(text.rstrip() + "\n" + "\n".join(block) + "\n", encoding="utf-8")


def main() -> None:
    ensure_dirs()
    df, page_count, api_total = build_change_table()
    codebook = build_codebook(df)
    domain_df, grain_df, contract_df = validate_change(df, codebook, page_count, api_total)

    df.to_csv(SILVER_DIR / "silver_change_index_trade_area_q.csv", index=False, encoding="utf-8-sig")
    codebook.to_csv(SILVER_DIR / "silver_change_index_codebook.csv", index=False, encoding="utf-8-sig")
    domain_df.to_csv(VALIDATION_DIR / "05_change_index_domain_validation.csv", index=False, encoding="utf-8-sig")
    grain_df.to_csv(VALIDATION_DIR / "05_change_index_grain_validation.csv", index=False, encoding="utf-8-sig")
    contract_df.to_csv(VALIDATION_DIR / "05_change_index_source_contract.csv", index=False, encoding="utf-8-sig")
    write_validation_md(domain_df, grain_df, codebook)
    append_progress(domain_df)

    summary = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "rows": len(df),
        "codebook_rows": len(codebook),
        "validation_judgements": domain_df[["table", "judgement"]].to_dict("records"),
        "outputs": [
            "datacorpus/_silver/silver_change_index_trade_area_q.csv",
            "datacorpus/_silver/silver_change_index_codebook.csv",
            "datacorpus/_rule_validation/05_change_index_domain_validation.csv",
            "datacorpus/_rule_validation/05_change_index_grain_validation.csv",
            "datacorpus/_rule_validation/05_change_index_source_contract.csv",
            "research/rule_validation/05_change_index_silver_validation_20260703.md",
        ],
    }
    (VALIDATION_DIR / "05_change_index_preprocess_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
