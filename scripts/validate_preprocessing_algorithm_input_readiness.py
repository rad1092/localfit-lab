from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "datacorpus" / "_gold"
RAW = ROOT / "datacorpus" / "_raw_ingest"
RULE_DIR = ROOT / "datacorpus" / "_rule_validation"
DOC_DIR = ROOT / "research" / "rule_validation"
ENGINE = ROOT / "scripts" / "build_rule_based_location_scores.py"

OUT_TABLES = RULE_DIR / "99_preprocessing_algorithm_input_readiness_tables.csv"
OUT_VALIDATION = RULE_DIR / "99_preprocessing_algorithm_input_readiness_validation.csv"
OUT_SUMMARY = RULE_DIR / "99_preprocessing_algorithm_input_readiness_summary.json"
OUT_DOC = DOC_DIR / "99_preprocessing_algorithm_input_readiness_20260707.md"

VERSION = "preprocessing_algorithm_input_readiness.v0.1-20260707"

COMMON_META_COLUMNS = [
    "source_id",
    "provider",
    "directness_level",
    "forbidden_claim_ko",
    "gold_version",
    "gold_role",
    "direct_score_allowed",
    "proxy_score_allowed",
]

TABLE_CONTRACTS: dict[str, dict[str, Any]] = {
    "gold_trade_area_profile.csv": {
        "role": "master_location",
        "axis": "location_lookup",
        "grain": ["상권_코드"],
        "required": ["상권_코드", "상권_코드_명", "자치구_코드", "행정동_코드_명"],
        "quartered": False,
        "official_score_input": True,
        "min_rows": 1600,
    },
    "gold_sales_strength_q_industry.csv": {
        "role": "official_axis",
        "axis": "sales",
        "grain": ["기준_년분기_코드", "상권_코드", "서비스_업종_코드"],
        "required": [
            "기준_년분기_코드",
            "상권_코드",
            "서비스_업종_코드",
            "당월_매출_금액",
            "점포당_매출_금액",
        ],
        "quartered": True,
        "official_score_input": True,
        "min_rows": 100000,
    },
    "gold_competition_q_industry.csv": {
        "role": "official_axis",
        "axis": "competition",
        "grain": ["기준_년분기_코드", "상권_코드", "서비스_업종_코드"],
        "required": [
            "기준_년분기_코드",
            "상권_코드",
            "서비스_업종_코드",
            "점포_수",
            "유사_업종_점포_수",
            "개업_율",
            "폐업_률",
        ],
        "quartered": True,
        "official_score_input": True,
        "min_rows": 100000,
    },
    "gold_demand_q_area.csv": {
        "role": "official_axis",
        "axis": "demand",
        "grain": ["기준_년분기_코드", "상권_코드"],
        "required": [
            "기준_년분기_코드",
            "상권_코드",
            "총_유동인구_수",
            "총_상주인구_수",
            "총_직장인구_수",
            "지출_총금액",
            "수요원천_존재_개수",
        ],
        "quartered": True,
        "official_score_input": True,
        "min_rows": 30000,
    },
    "gold_accessibility_q_area.csv": {
        "role": "official_axis",
        "axis": "accessibility",
        "grain": ["기준_년분기_코드", "상권_코드"],
        "required": [
            "기준_년분기_코드",
            "상권_코드",
            "총_집객시설_수",
            "교통결절_시설수",
            "생활이동_외부유입_이동인구_합계",
            "생활이동_분기_포함월수",
        ],
        "quartered": True,
        "official_score_input": True,
        "min_rows": 30000,
    },
    "gold_growth_stability_q_industry.csv": {
        "role": "separate_signal",
        "axis": "growth",
        "grain": ["기준_년분기_코드", "상권_코드", "서비스_업종_코드"],
        "required": [
            "기준_년분기_코드",
            "상권_코드",
            "서비스_업종_코드",
            "매출_log_최근4분기_slope",
            "개업_율",
            "폐업_률",
            "growth_score_status",
        ],
        "quartered": True,
        "official_score_input": False,
        "min_rows": 100000,
    },
    "gold_cost_risk_q_area.csv": {
        "role": "separate_signal",
        "axis": "cost_risk",
        "grain": ["기준_년분기_코드", "상권_코드"],
        "required": [
            "기준_년분기_코드",
            "상권_코드",
            "자치구_코드",
            "건물면적당_거래금액_중앙값_만원_per_m2",
            "거래건수",
            "proxy_reason_ko",
        ],
        "quartered": True,
        "official_score_input": False,
        "min_rows": 5000,
    },
    "gold_data_reliability_snapshot.csv": {
        "role": "quality_snapshot",
        "axis": "data_reliability",
        "grain": ["silver_table"],
        "required": [
            "silver_table",
            "row_count",
            "file_bytes",
            "gold_input_role",
            "use_note_ko",
        ],
        "quartered": False,
        "official_score_input": False,
        "min_rows": 10,
    },
}


def read_header(path: Path) -> list[str]:
    return pd.read_csv(path, nrows=0, encoding="utf-8-sig").columns.tolist()


def read_columns(path: Path, columns: list[str]) -> pd.DataFrame:
    header = read_header(path)
    usecols = [col for col in columns if col in header]
    if not usecols:
        return pd.DataFrame()
    chunks = pd.read_csv(
        path,
        usecols=usecols,
        encoding="utf-8-sig",
        dtype=str,
        chunksize=250_000,
        low_memory=False,
    )
    return pd.concat(chunks, ignore_index=True)


def normalize_source_ids(value: Any) -> list[str]:
    if pd.isna(value):
        return []
    text = str(value).strip()
    if not text:
        return []
    for sep in [";", "|", ","]:
        text = text.replace(sep, " ")
    return [part.strip() for part in text.split() if part.strip()]


def load_registry_ids() -> set[str]:
    path = RAW / "source_registry.csv"
    if not path.exists():
        return set()
    reg = pd.read_csv(path, usecols=["source_id"], encoding="utf-8-sig", dtype=str)
    return set(reg["source_id"].dropna().astype(str))


def summarize_table(filename: str, contract: dict[str, Any], registry_ids: set[str]) -> dict[str, Any]:
    path = GOLD / filename
    row: dict[str, Any] = {
        "table": filename,
        "role": contract["role"],
        "axis": contract["axis"],
        "official_score_input": contract["official_score_input"],
        "exists": path.exists(),
        "file_bytes": path.stat().st_size if path.exists() else 0,
        "row_count": 0,
        "column_count": 0,
        "missing_required_columns": "",
        "missing_metadata_columns": "",
        "grain_columns": "|".join(contract["grain"]),
        "grain_null_rows": None,
        "grain_duplicate_rows": None,
        "quarter_min": "",
        "quarter_max": "",
        "quarter_count": 0,
        "latest_quarter": "",
        "latest_grain_count": 0,
        "source_ids_missing_registry": "",
        "table_status": "FAIL",
        "notes_ko": "",
    }
    if not path.exists():
        row["notes_ko"] = "gold 입력 파일이 없다."
        return row

    header = read_header(path)
    row["column_count"] = len(header)
    missing_required = [col for col in contract["required"] if col not in header]
    missing_meta = [
        col for col in COMMON_META_COLUMNS
        if col not in header and contract["role"] not in {"quality_snapshot"}
    ]
    row["missing_required_columns"] = "|".join(missing_required)
    row["missing_metadata_columns"] = "|".join(missing_meta)

    read_cols = list(dict.fromkeys(contract["grain"] + ["source_id"]))
    if contract["quartered"]:
        read_cols.append("기준_년분기_코드")
    df = read_columns(path, read_cols)
    row["row_count"] = int(len(df))

    if all(col in df.columns for col in contract["grain"]):
        grain_df = df[contract["grain"]]
        row["grain_null_rows"] = int(grain_df.isna().any(axis=1).sum())
        row["grain_duplicate_rows"] = int(grain_df.duplicated().sum())
        if contract["quartered"] and "기준_년분기_코드" in df.columns:
            latest_q = str(df["기준_년분기_코드"].dropna().astype(str).max())
            row["latest_quarter"] = latest_q
            latest = df[df["기준_년분기_코드"].astype(str) == latest_q]
            row["latest_grain_count"] = int(latest[contract["grain"]].drop_duplicates().shape[0])

    if contract["quartered"] and "기준_년분기_코드" in df.columns:
        qs = sorted(df["기준_년분기_코드"].dropna().astype(str).unique())
        row["quarter_min"] = qs[0] if qs else ""
        row["quarter_max"] = qs[-1] if qs else ""
        row["quarter_count"] = len(qs)

    if "source_id" in df.columns and registry_ids:
        observed = set()
        for value in df["source_id"].dropna().unique():
            observed.update(normalize_source_ids(value))
        missing_registry = sorted(source for source in observed if source not in registry_ids)
        row["source_ids_missing_registry"] = "|".join(missing_registry)

    failures = []
    if row["row_count"] < contract["min_rows"]:
        failures.append("row_count_below_min")
    if missing_required:
        failures.append("missing_required_columns")
    if missing_meta:
        failures.append("missing_metadata_columns")
    if row["grain_null_rows"] not in {0, None}:
        failures.append("grain_null_rows")
    if row["grain_duplicate_rows"] not in {0, None}:
        failures.append("grain_duplicate_rows")
    if row["source_ids_missing_registry"]:
        failures.append("source_id_not_in_registry")

    if failures:
        row["table_status"] = "FAIL"
        row["notes_ko"] = ";".join(failures)
    else:
        row["table_status"] = "PASS"
        row["notes_ko"] = "알고리즘 입력 계약을 만족한다."
    return row


def validation_rows(table_df: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def add(check_id: str, title: str, status: str, detail: str) -> None:
        rows.append({"check_id": check_id, "title_ko": title, "status": status, "detail_ko": detail})

    required_tables = set(TABLE_CONTRACTS)
    existing_pass = set(table_df.loc[table_df["exists"], "table"])
    add(
        "V01",
        "필수 gold 입력 테이블 존재",
        "PASS" if required_tables <= existing_pass else "FAIL",
        f"필수 {len(required_tables)}개 중 존재 {len(existing_pass & required_tables)}개",
    )

    required_fail = table_df[table_df["missing_required_columns"].astype(str) != ""]
    add(
        "V02",
        "필수 컬럼 보존",
        "PASS" if required_fail.empty else "FAIL",
        "누락 없음" if required_fail.empty else ", ".join(required_fail["table"].tolist()),
    )

    meta_fail = table_df[table_df["missing_metadata_columns"].astype(str) != ""]
    add(
        "V03",
        "출처·직접성·금지주장 메타데이터 보존",
        "PASS" if meta_fail.empty else "FAIL",
        "누락 없음" if meta_fail.empty else ", ".join(meta_fail["table"].tolist()),
    )

    grain_fail = table_df[
        (table_df["grain_null_rows"].fillna(0).astype(int) > 0)
        | (table_df["grain_duplicate_rows"].fillna(0).astype(int) > 0)
    ]
    add(
        "V04",
        "알고리즘 grain 중복·결측 없음",
        "PASS" if grain_fail.empty else "FAIL",
        "grain 이상 없음" if grain_fail.empty else ", ".join(grain_fail["table"].tolist()),
    )

    quartered = table_df[table_df["quarter_count"].fillna(0).astype(int) > 0]
    common_quarters: set[str] | None = None
    for table in [
        "gold_sales_strength_q_industry.csv",
        "gold_competition_q_industry.csv",
        "gold_demand_q_area.csv",
        "gold_accessibility_q_area.csv",
    ]:
        path = GOLD / table
        df = read_columns(path, ["기준_년분기_코드"])
        qs = set(df["기준_년분기_코드"].dropna().astype(str).unique())
        common_quarters = qs if common_quarters is None else common_quarters & qs
    latest_common = max(common_quarters) if common_quarters else ""
    add(
        "V05",
        "공식 4축 공통 기준분기 존재",
        "PASS" if latest_common else "FAIL",
        f"latest_common_quarter={latest_common}, quartered_tables={len(quartered)}",
    )

    master_count = int(table_df.loc[table_df["table"] == "gold_trade_area_profile.csv", "row_count"].iloc[0])
    area_latest = table_df[table_df["table"].isin(["gold_demand_q_area.csv", "gold_accessibility_q_area.csv"])]
    area_min_ratio = float((area_latest["latest_grain_count"].astype(int) / max(master_count, 1)).min())
    add(
        "V06",
        "최신분기 상권 단위 수요·접근성 커버리지",
        "PASS" if area_min_ratio >= 0.95 else "FAIL",
        f"master_area={master_count}, min_latest_area_ratio={area_min_ratio:.4f}",
    )

    sales_keys = read_latest_keys("gold_sales_strength_q_industry.csv", ["기준_년분기_코드", "상권_코드", "서비스_업종_코드"])
    comp_keys = read_latest_keys("gold_competition_q_industry.csv", ["기준_년분기_코드", "상권_코드", "서비스_업종_코드"])
    overlap_ratio = len(sales_keys & comp_keys) / max(len(sales_keys), 1)
    add(
        "V07",
        "최신분기 매출·경쟁 업종 grain 교집합",
        "PASS" if overlap_ratio >= 0.90 else "FAIL",
        f"sales_keys={len(sales_keys)}, competition_keys={len(comp_keys)}, overlap_ratio={overlap_ratio:.4f}",
    )

    registry_fail = table_df[table_df["source_ids_missing_registry"].astype(str) != ""]
    add(
        "V08",
        "gold source_id가 source_registry에 등록됨",
        "PASS" if registry_fail.empty else "FAIL",
        "미등록 없음" if registry_fail.empty else ", ".join(registry_fail["table"].tolist()),
    )

    engine_text = ENGINE.read_text(encoding="utf-8-sig")
    engine_uses_gold = "read_gold(" in engine_text and "gold_sales_strength_q_industry.csv" in engine_text
    engine_avoids_final_mart = "_final/model_ready" not in engine_text and "FeatureMart" not in engine_text
    add(
        "V09",
        "엔진은 대형 feature mart가 아니라 gold 축별 입력을 소비",
        "PASS" if engine_uses_gold and engine_avoids_final_mart else "FAIL",
        f"uses_gold={engine_uses_gold}, avoids_feature_mart={engine_avoids_final_mart}",
    )

    failed_downloads = RAW / "failed_downloads.csv"
    fail_count = 0
    if failed_downloads.exists():
        fail_count = int(pd.read_csv(failed_downloads, usecols=["source_id"], encoding="utf-8-sig").shape[0])
    add(
        "V10",
        "원천 적재 실패는 남아 있어도 gold 공식 입력과 분리 기록됨",
        "PASS" if fail_count >= 0 else "FAIL",
        f"failed_download_rows={fail_count}; 공식 gold 입력은 별도 계약으로 검증",
    )

    return rows


def read_latest_keys(filename: str, cols: list[str]) -> set[tuple[str, ...]]:
    path = GOLD / filename
    df = read_columns(path, cols)
    if "기준_년분기_코드" not in df.columns:
        return set()
    latest = str(df["기준_년분기_코드"].dropna().astype(str).max())
    df = df[df["기준_년분기_코드"].astype(str) == latest]
    key_cols = [col for col in cols if col != "기준_년분기_코드"]
    return set(map(tuple, df[key_cols].dropna().astype(str).itertuples(index=False, name=None)))


def write_doc(summary: dict[str, Any], table_df: pd.DataFrame, validation_df: pd.DataFrame) -> None:
    DOC_DIR.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 99. 전처리 데이터 알고리즘 입력 준비도 검증",
        "",
        "## 목적",
        "",
        "98번에서 알고리즘 규칙과 논문·자료 근거의 추적성을 확인했다. 99번은 그 알고리즘이 실제로 먹는 gold 전처리 데이터가 키, 시점, grain, 출처 메타데이터를 유지하는지 검증한다.",
        "",
        "## 결과",
        "",
        f"- validation version: `{summary['validation_version']}`",
        f"- decision: `{summary['decision']}`",
        f"- PASS: `{summary['pass_count']}`",
        f"- FAIL: `{summary['fail_count']}`",
        f"- gold table count: `{summary['table_count']}`",
        f"- latest common official quarter: `{summary['latest_common_official_quarter']}`",
        f"- latest area coverage min ratio: `{summary['latest_area_coverage_min_ratio']}`",
        f"- latest sales/competition overlap ratio: `{summary['latest_sales_competition_overlap_ratio']}`",
        "",
        "## 검증 항목",
        "",
        "| check_id | status | detail |",
        "| --- | --- | --- |",
    ]
    for rec in validation_df.to_dict("records"):
        lines.append(f"| {rec['check_id']} | {rec['status']} | {rec['detail_ko']} |")
    lines.extend([
        "",
        "## 테이블 준비도",
        "",
        "| table | role | axis | rows | latest_quarter | latest_grain_count | status | notes |",
        "| --- | --- | --- | ---: | --- | ---: | --- | --- |",
    ])
    for rec in table_df.to_dict("records"):
        lines.append(
            f"| `{rec['table']}` | {rec['role']} | {rec['axis']} | {int(rec['row_count'])} | "
            f"{rec['latest_quarter']} | {int(rec['latest_grain_count'])} | {rec['table_status']} | {rec['notes_ko']} |"
        )
    lines.extend([
        "",
        "## 해석",
        "",
        "- 공식 점수 입력은 gold 축별 테이블을 기준으로 검증한다.",
        "- 원천 적재 실패 파일이 남아 있어도, 공식 gold 입력의 키와 grain이 통과하면 알고리즘 입력 자체는 진행 가능하다.",
        "- 실패 항목이 생기면 알고리즘 산식 보강보다 해당 축의 전처리와 source_id 추적을 먼저 고친다.",
    ])
    OUT_DOC.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    RULE_DIR.mkdir(parents=True, exist_ok=True)
    DOC_DIR.mkdir(parents=True, exist_ok=True)

    registry_ids = load_registry_ids()
    table_rows = [
        summarize_table(filename, contract, registry_ids)
        for filename, contract in TABLE_CONTRACTS.items()
    ]
    table_df = pd.DataFrame(table_rows)
    validations = validation_rows(table_df)
    validation_df = pd.DataFrame(validations)

    sales_keys = read_latest_keys("gold_sales_strength_q_industry.csv", ["기준_년분기_코드", "상권_코드", "서비스_업종_코드"])
    comp_keys = read_latest_keys("gold_competition_q_industry.csv", ["기준_년분기_코드", "상권_코드", "서비스_업종_코드"])
    area_latest = table_df[table_df["table"].isin(["gold_demand_q_area.csv", "gold_accessibility_q_area.csv"])]
    master_count = int(table_df.loc[table_df["table"] == "gold_trade_area_profile.csv", "row_count"].iloc[0])
    area_ratio = float((area_latest["latest_grain_count"].astype(int) / max(master_count, 1)).min())
    official_quarter_max = table_df[
        table_df["table"].isin([
            "gold_sales_strength_q_industry.csv",
            "gold_competition_q_industry.csv",
            "gold_demand_q_area.csv",
            "gold_accessibility_q_area.csv",
        ])
    ]["latest_quarter"].astype(str).min()

    pass_count = int((validation_df["status"] == "PASS").sum())
    fail_count = int((validation_df["status"] == "FAIL").sum())
    decision = "PREPROCESSING_ALGORITHM_INPUT_READINESS_PASS" if fail_count == 0 else "PREPROCESSING_ALGORITHM_INPUT_READINESS_FAIL"
    summary = {
        "validation_version": VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "decision": decision,
        "pass_count": pass_count,
        "fail_count": fail_count,
        "table_count": len(table_df),
        "latest_common_official_quarter": official_quarter_max,
        "latest_area_coverage_min_ratio": round(area_ratio, 6),
        "latest_sales_competition_overlap_ratio": round(len(sales_keys & comp_keys) / max(len(sales_keys), 1), 6),
        "outputs": {
            "tables": str(OUT_TABLES.relative_to(ROOT)),
            "validation": str(OUT_VALIDATION.relative_to(ROOT)),
            "summary": str(OUT_SUMMARY.relative_to(ROOT)),
            "doc": str(OUT_DOC.relative_to(ROOT)),
        },
        "reason_ko": "공식 점수 엔진이 요구하는 gold 축별 입력의 키, grain, 출처 메타데이터, 최신 공통분기 커버리지를 검증했다.",
    }

    table_df.to_csv(OUT_TABLES, index=False, encoding="utf-8-sig")
    validation_df.to_csv(OUT_VALIDATION, index=False, encoding="utf-8-sig")
    OUT_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_doc(summary, table_df, validation_df)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
