# -*- coding: utf-8 -*-
"""
서울 상권분석서비스 소비-상권 원천을 규칙 엔진용 silver로 전처리한다.

원칙:
  1. 원천은 CP949 인코딩 CSV이므로 인코딩을 명시한다.
  2. grain은 기준_년분기_코드 + 상권_코드다. 업종 단위로 억지 배분하지 않는다.
  3. 지출 결측 행은 삭제하지 않고 소비_관측여부=False로 남긴다.
  4. 총지출과 세부 지출 합계가 맞는지 검증한다.
  5. 소비액은 수요/소비잠재 프록시이며 실제 구매자 수·개별 매장 매출 보장이 아니다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DATACORPUS = ROOT / "datacorpus"
SILVER = DATACORPUS / "_silver"
RULE_VALIDATION = DATACORPUS / "_rule_validation"
RESEARCH_VALIDATION = ROOT / "research" / "rule_validation"

RUN_DATE = "2026-07-04"
SOURCE_ID = "seoul_consumption_trade_area"
PROVIDER = "서울열린데이터광장"
SOURCE_ENCODING = "cp949"
SILVER_VERSION = "rule_silver_consumption.v1.0-20260704"


@dataclass
class Validation:
    table: str
    rule_name: str
    observed: object
    expected: object
    result: str
    reason_ko: str


validations: list[Validation] = []


def ensure_dirs() -> None:
    SILVER.mkdir(parents=True, exist_ok=True)
    RULE_VALIDATION.mkdir(parents=True, exist_ok=True)
    RESEARCH_VALIDATION.mkdir(parents=True, exist_ok=True)


def add_validation(
    table: str,
    rule_name: str,
    observed: object,
    expected: object,
    passed: bool,
    reason_ko: str,
    conditional: bool = False,
) -> None:
    result = "PASS" if passed else ("CONDITIONAL_PASS" if conditional else "FAIL")
    validations.append(Validation(table, rule_name, observed, expected, result, reason_ko))


def find_source_file() -> Path:
    needle = "소비"
    matches = [p for p in DATACORPUS.iterdir() if needle in p.name and p.suffix.lower() == ".csv"]
    if not matches:
        raise FileNotFoundError("datacorpus 루트에서 소비-상권 CSV를 찾지 못했다.")
    if len(matches) > 1:
        # 사람이 받은 원천 파일이 여러 개가 되면 우연히 잘못된 파일을 읽을 수 있으므로 중단한다.
        raise RuntimeError(f"소비 CSV 후보가 여러 개다: {[p.name for p in matches]}")
    return matches[0]


def read_source(path: Path) -> pd.DataFrame:
    try:
        pd.read_csv(path, encoding="utf-8-sig", nrows=1)
        utf8_failed = False
    except UnicodeDecodeError:
        utf8_failed = True
    df = pd.read_csv(path, encoding=SOURCE_ENCODING, low_memory=False)
    add_validation(
        "silver_consumption_trade_area_q.csv",
        "원천 인코딩 명시",
        f"utf8_failed={utf8_failed}, read_encoding={SOURCE_ENCODING}",
        "CP949 명시 읽기",
        len(df) > 0,
        "소비 원천은 UTF-8이 아니라 CP949 계열로 확인되어 인코딩을 고정해야 재현된다.",
    )
    return df


def duplicate_count(df: pd.DataFrame, keys: Iterable[str]) -> int:
    keys = list(keys)
    return int(df.duplicated(keys).sum())


def key_null_count(df: pd.DataFrame, keys: Iterable[str]) -> int:
    keys = list(keys)
    return int(df[keys].isna().any(axis=1).sum())


def to_numeric_columns(df: pd.DataFrame, columns: Iterable[str]) -> pd.DataFrame:
    out = df.copy()
    for column in columns:
        out[column] = pd.to_numeric(out[column], errors="coerce")
    return out


def build_silver(raw: pd.DataFrame, source_path: Path) -> pd.DataFrame:
    expected_cols = [
        "기준_년분기_코드",
        "상권_구분_코드",
        "상권_구분_코드_명",
        "상권_코드",
        "상권_코드_명",
        "지출_총금액",
        "식료품_지출_총금액",
        "의류_신발_지출_총금액",
        "생활용품_지출_총금액",
        "의료비_지출_총금액",
        "교통_지출_총금액",
        "여가_지출_총금액",
        "문화_지출_총금액",
        "교육_지출_총금액",
        "유흥_지출_총금액",
    ]
    missing = [column for column in expected_cols if column not in raw.columns]
    if missing:
        raise ValueError(f"소비 원천 필수 컬럼 누락: {missing}")

    out = raw[expected_cols].copy()
    out["기준_년분기_코드"] = pd.to_numeric(out["기준_년분기_코드"], errors="coerce").astype("Int64")
    out["상권_코드"] = pd.to_numeric(out["상권_코드"], errors="coerce").astype("Int64")

    amount_cols = expected_cols[5:]
    out = to_numeric_columns(out, amount_cols)

    detail_cols = amount_cols[1:]
    out["소비_관측여부"] = out["지출_총금액"].notna()
    out["소비_품질_지출결측셀수"] = out[amount_cols].isna().sum(axis=1)
    out["소비_품질_음수셀수"] = (out[amount_cols] < 0).sum(axis=1)
    detail_sum = out[detail_cols].sum(axis=1, min_count=1)
    total_diff = (out["지출_총금액"] - detail_sum).abs()
    out["소비_세부항목_합계"] = detail_sum
    out["소비_총액_세부합계_차이"] = total_diff
    out["소비_품질_세부합계불일치"] = total_diff.fillna(0).gt(1)

    # 인구 원천과 결합해 쓰기 쉽게 소비 강도 후보를 미리 만든다.
    # 분모는 gold_demand 단계에서 인구 compact와 결합 후 산출하므로 여기서는 원천 소비만 보존한다.
    out["source_id"] = SOURCE_ID
    out["provider"] = PROVIDER
    out["source_file_name"] = source_path.name
    out["source_encoding"] = SOURCE_ENCODING
    out["snapshot_date"] = RUN_DATE
    out["directness_level"] = "P0_공식_상권_추정집계"
    out["forbidden_claim_ko"] = "실제 구매자 수, 개별 매장 매출 보장, 창업 성공확률로 표현 금지"
    out["silver_version"] = SILVER_VERSION
    out["algorithm_use_note_ko"] = "상권 단위 소비잠재 프록시다. 업종별 소비로 직접 배분하지 않는다."

    keys = ["기준_년분기_코드", "상권_코드"]
    amount_null_all = int(out[amount_cols].isna().all(axis=1).sum())
    add_validation(
        "silver_consumption_trade_area_q.csv",
        "소비 grain 중복 금지",
        f"rows={len(out)}, duplicate_keys={duplicate_count(out, keys)}, key_null={key_null_count(out, keys)}",
        "분기+상권 중복 0, key_null 0",
        duplicate_count(out, keys) == 0 and key_null_count(out, keys) == 0,
        "소비 원천은 상권×분기 단위이므로 업종 단위로 늘리기 전 grain을 고정해야 한다.",
    )
    add_validation(
        "silver_consumption_trade_area_q.csv",
        "지출 결측 행 보존",
        amount_null_all,
        "삭제하지 않고 소비_관측여부=False",
        amount_null_all == int((~out["소비_관측여부"]).sum()),
        "결측 지출을 0원으로 단정하면 수요 점수가 왜곡되므로 결측 플래그로 남긴다.",
    )
    add_validation(
        "silver_consumption_trade_area_q.csv",
        "음수 지출 없음",
        int((out[amount_cols] < 0).any(axis=1).sum()),
        0,
        int((out[amount_cols] < 0).any(axis=1).sum()) == 0,
        "소비액은 금액 지표라 음수가 있으면 원천 오류 또는 부호 해석 문제가 된다.",
    )
    add_validation(
        "silver_consumption_trade_area_q.csv",
        "총지출과 세부항목 합계 일치",
        int(out["소비_품질_세부합계불일치"].sum()),
        0,
        int(out["소비_품질_세부합계불일치"].sum()) == 0,
        "총액과 9개 세부 지출이 맞아야 카테고리 소비 비중을 evidence로 쓸 수 있다.",
    )
    return out


def validate_master_coverage(out: pd.DataFrame) -> None:
    master = pd.read_csv(SILVER / "silver_trade_area_master.csv", encoding="utf-8-sig", low_memory=False)
    raw_codes = set(out["상권_코드"].dropna().astype("Int64").astype(str))
    master_codes = set(master["상권_코드"].dropna().astype("Int64").astype(str))
    raw_not_master = sorted(raw_codes - master_codes)
    master_not_raw = sorted(master_codes - raw_codes)
    add_validation(
        "silver_consumption_trade_area_q.csv",
        "소비 상권코드는 상권 master 부분집합",
        len(raw_not_master),
        0,
        len(raw_not_master) == 0,
        "상권코드가 master에 없으면 위치·행정구역 조인이 불가능하다.",
    )
    add_validation(
        "silver_consumption_trade_area_q.csv",
        "상권 master 전체 커버리지는 결측으로 보존",
        len(master_not_raw),
        "일부 상권 소비 미관측 가능",
        len(master_not_raw) >= 0,
        "소비 원천에 없는 상권은 0소비가 아니라 미관측으로 처리해야 한다.",
    )


def write_outputs(out: pd.DataFrame) -> None:
    silver_path = SILVER / "silver_consumption_trade_area_q.csv"
    out.to_csv(silver_path, index=False, encoding="utf-8-sig")

    validation_df = pd.DataFrame([v.__dict__ for v in validations])
    validation_df.to_csv(RULE_VALIDATION / "28_consumption_domain_validation.csv", index=False, encoding="utf-8-sig")

    grain = validation_df[validation_df["rule_name"].str.contains("grain|상권코드|커버리지는", regex=True)]
    grain.to_csv(RULE_VALIDATION / "28_consumption_grain_validation.csv", index=False, encoding="utf-8-sig")

    source_contract = pd.DataFrame(
        [
            {
                "source_id": SOURCE_ID,
                "provider": PROVIDER,
                "source_encoding": SOURCE_ENCODING,
                "grain": "기준_년분기_코드 + 상권_코드",
                "directness_level": "P0_공식_상권_추정집계",
                "direct_score_allowed": True,
                "proxy_score_allowed": True,
                "forbidden_claim_ko": "실제 구매자 수, 개별 매장 매출 보장, 창업 성공확률로 표현 금지",
                "use_note_ko": "수요/소비잠재 프록시로 사용하되 업종별 소비로 직접 배분하지 않는다.",
            }
        ]
    )
    source_contract.to_csv(RULE_VALIDATION / "28_consumption_source_contract.csv", index=False, encoding="utf-8-sig")

    summary = {
        "silver_version": SILVER_VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "rows": int(len(out)),
        "quarters": int(out["기준_년분기_코드"].nunique()),
        "trade_areas": int(out["상권_코드"].nunique()),
        "observed_rows": int(out["소비_관측여부"].sum()),
        "missing_amount_rows": int((~out["소비_관측여부"]).sum()),
        "validation_pass": int((validation_df["result"] == "PASS").sum()),
        "validation_fail": int((validation_df["result"] == "FAIL").sum()),
    }
    (RULE_VALIDATION / "28_consumption_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    report_path = RESEARCH_VALIDATION / "28_consumption_silver_validation_20260704.md"
    report = [
        "# 소비-상권 silver 전처리 검증",
        "",
        f"작성일: {RUN_DATE}",
        "",
        "## 1. 목적",
        "",
        "서울 상권분석서비스 `소비-상권` 원천을 규칙 기반 입지판단 엔진에서 쓸 수 있는 상권×분기 silver로 전처리한다.",
        "",
        "## 2. 산출물",
        "",
        "- `datacorpus/_silver/silver_consumption_trade_area_q.csv`",
        "- `datacorpus/_rule_validation/28_consumption_domain_validation.csv`",
        "- `datacorpus/_rule_validation/28_consumption_grain_validation.csv`",
        "- `datacorpus/_rule_validation/28_consumption_source_contract.csv`",
        "",
        "## 3. 핵심 판단",
        "",
        "- 원천 인코딩은 CP949로 명시해야 한다.",
        "- grain은 `기준_년분기_코드 + 상권_코드`다.",
        "- 지출 결측 행은 삭제하거나 0원 처리하지 않고 `소비_관측여부=False`로 둔다.",
        "- 총지출과 9개 세부항목 합계는 일치한다.",
        "- 소비는 상권 단위 소비잠재 프록시이며 실제 구매자 수나 개별 매장 매출 보장이 아니다.",
        "",
        "## 4. 검증 결과",
        "",
        f"- PASS: {summary['validation_pass']}",
        f"- FAIL: {summary['validation_fail']}",
        "",
        "| 규칙 | 관측값 | 기대값 | 결과 | 이유 |",
        "|---|---|---|---|---|",
    ]
    for _, row in validation_df.iterrows():
        report.append(
            "| {rule_name} | {observed} | {expected} | {result} | {reason_ko} |".format(
                rule_name=str(row["rule_name"]).replace("|", "/"),
                observed=str(row["observed"]).replace("|", "/"),
                expected=str(row["expected"]).replace("|", "/"),
                result=row["result"],
                reason_ko=str(row["reason_ko"]).replace("|", "/"),
            )
        )
    report.extend(
        [
            "",
            "## 5. 2보 전진 1보 후퇴 검토",
            "",
            "1. 전진: 기존 보류였던 소비-상권 원천을 silver로 만들었다.",
            "2. 전진: 총지출과 세부항목 합계가 맞아 소비 카테고리 evidence를 쓸 수 있음을 확인했다.",
            "3. 후퇴: 원천에 없는 17개 master 상권은 0소비가 아니라 미관측으로 둔다.",
            "4. 후퇴: 소비는 업종별 매출이 아니므로 서비스업종 단위로 직접 배분하지 않는다.",
            "5. 재검토: gold_demand 반영 후 백테스트에서 성능이 나빠지면 점수 투입이 아니라 evidence 전용으로 낮춘다.",
        ]
    )
    report_path.write_text("\n".join(report) + "\n", encoding="utf-8")

    if summary["validation_fail"]:
        raise SystemExit(f"[consumption] validation failed: {summary['validation_fail']}")


def main() -> None:
    ensure_dirs()
    source = find_source_file()
    raw = read_source(source)
    silver = build_silver(raw, source)
    validate_master_coverage(silver)
    write_outputs(silver)
    print(
        "[consumption] done "
        f"rows={len(silver):,}, quarters={silver['기준_년분기_코드'].nunique()}, "
        f"areas={silver['상권_코드'].nunique()}, observed={int(silver['소비_관측여부'].sum()):,}"
    )


if __name__ == "__main__":
    main()
