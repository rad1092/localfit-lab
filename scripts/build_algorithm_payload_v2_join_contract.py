# -*- coding: utf-8 -*-
"""
77. 알고리즘 payload v2 조인 계약 생성.

목적:
  - 76번에서 준비도를 통과한 direct score gold를 알고리즘이 어떤 키로 읽을지 고정한다.
  - 상권×분기 자료를 상권×업종×분기 드라이버에 붙일 때 fan-out이 생기지 않는지 검증한다.
  - 공식 현재입지 4축과 성장/프록시 후보를 payload 안에서 분리한다.

주의:
  - 이 스크립트는 점수 산식을 변경하지 않는다.
  - 거대 feature mart를 만들지 않고, 런타임 payload 계약과 샘플만 만든다.
  - 결측은 임의 0 대체하지 않고 상태 플래그와 함께 보존한다.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

GOLD = ROOT / "datacorpus" / "_gold"
RULE = ROOT / "datacorpus" / "_rule_validation"
DOC = ROOT / "research" / "rule_validation"

OUT_CONTRACT = RULE / "77_algorithm_payload_v2_join_contract.csv"
OUT_JOIN_AUDIT = RULE / "77_algorithm_payload_v2_join_fanout_audit.csv"
OUT_SAMPLE = RULE / "77_algorithm_payload_v2_sample_3001491_CS100001_20261.json"
OUT_VALIDATION = RULE / "77_algorithm_payload_v2_join_contract_validation.csv"
OUT_SUMMARY = RULE / "77_algorithm_payload_v2_join_contract_summary.json"
OUT_DOC = DOC / "77_algorithm_payload_v2_join_contract_20260707.md"

VERSION = "algorithm_payload_v2_join_contract.v0.1-20260707"
QUARTER = 20261
SAMPLE_TRADE_AREA = "3001491"
SAMPLE_INDUSTRY = "CS100001"


RESEARCH_BASIS_DOCS = [
    ROOT / "research" / "알고리즘_명세_v2_20260704.md",
    ROOT / "research" / "알고리즘_스펙_v1_20260703.md",
    DOC / "24_gold_based_score_engine_validation_20260704.md",
    DOC / "50_sales_ticket_engine_patch_validation_20260707.md",
    DOC / "76_direct_score_input_readiness_20260707.md",
]


PAYLOAD_CONTRACT_ROWS = [
    {
        "payload_section": "driver.area_industry",
        "axis": "driver",
        "gold_file": "gold_sales_strength_q_industry.csv;gold_competition_q_industry.csv",
        "source_grain": "상권×업종×분기",
        "lookup_key": "기준_년분기_코드+상권_코드+서비스_업종_코드",
        "join_cardinality": "sales/store outer union, 이후 many_to_one",
        "official_current_axis": False,
        "candidate_only": False,
        "fanout_guard_ko": "드라이버는 기준분기 sales/store 키 합집합이며 이후 어떤 조인도 행수를 늘리면 안 된다.",
        "forbidden_claim_ko": "드라이버 존재를 창업 성공확률이나 추천 근거로 표현 금지",
        "basis_ko": "알고리즘 명세 v2 §2.1, 76번 direct 준비도 감사",
    },
    {
        "payload_section": "axes.sales",
        "axis": "sales",
        "gold_file": "gold_sales_strength_q_industry.csv",
        "source_grain": "상권×업종×분기",
        "lookup_key": "기준_년분기_코드+상권_코드+서비스_업종_코드",
        "join_cardinality": "many_to_one",
        "official_current_axis": True,
        "candidate_only": False,
        "fanout_guard_ko": "매출 gold key는 드라이버 key와 같은 grain이라 중복이 있으면 안 된다.",
        "forbidden_claim_ko": "개별 매장 매출 보장, 창업 성공확률, 실제 카드매출 원장으로 표현 금지",
        "basis_ko": "알고리즘 명세 v2 §3.1 sales, 50번 객단가 제거 검증",
    },
    {
        "payload_section": "axes.competition",
        "axis": "competition",
        "gold_file": "gold_competition_q_industry.csv",
        "source_grain": "상권×업종×분기",
        "lookup_key": "기준_년분기_코드+상권_코드+서비스_업종_코드",
        "join_cardinality": "many_to_one",
        "official_current_axis": True,
        "candidate_only": False,
        "fanout_guard_ko": "점포 gold key는 드라이버 key와 같은 grain이라 중복이 있으면 안 된다.",
        "forbidden_claim_ko": "점포가 많다는 사실을 무조건 좋은 입지 또는 나쁜 입지로 단정 금지",
        "basis_ko": "알고리즘 명세 v2 §3.1 competition, De Beule 외 확장 Huff 근거",
    },
    {
        "payload_section": "axes.demand",
        "axis": "demand",
        "gold_file": "gold_demand_q_area.csv",
        "source_grain": "상권×분기",
        "lookup_key": "기준_년분기_코드+상권_코드",
        "join_cardinality": "area_to_area_industry many_to_one",
        "official_current_axis": True,
        "candidate_only": False,
        "fanout_guard_ko": "상권 grain 수요값은 상권별 1행이어야 하며 업종 행에 복제돼도 행수를 늘리면 안 된다.",
        "forbidden_claim_ko": "유동인구를 실제 방문자 수나 구매자 수로 표현 금지",
        "basis_ko": "알고리즘 명세 v2 §3.1 demand, 04번 인구 silver 검증",
    },
    {
        "payload_section": "axes.accessibility",
        "axis": "accessibility",
        "gold_file": "gold_accessibility_q_area.csv",
        "source_grain": "상권×분기",
        "lookup_key": "기준_년분기_코드+상권_코드",
        "join_cardinality": "area_to_area_industry many_to_one",
        "official_current_axis": True,
        "candidate_only": False,
        "fanout_guard_ko": "시설 gold는 상권별 1행이어야 하며 시설 미관측은 0 대체하지 않는다.",
        "forbidden_claim_ko": "집객시설 수를 실제 방문확률이나 실제 유입 인원으로 표현 금지",
        "basis_ko": "알고리즘 명세 v2 §3.1 accessibility, 06번 집객시설 검증",
    },
    {
        "payload_section": "candidates.growth_stability",
        "axis": "growth_candidate",
        "gold_file": "gold_growth_stability_q_industry.csv",
        "source_grain": "상권×업종×분기 및 상권 변화 evidence",
        "lookup_key": "기준_년분기_코드+상권_코드+서비스_업종_코드",
        "join_cardinality": "candidate many_to_one, 현재입지 총점 미반영",
        "official_current_axis": False,
        "candidate_only": True,
        "fanout_guard_ko": "성장 안정성 후보는 현재입지 4축 총점에 합산하지 않는다.",
        "forbidden_claim_ko": "성장률 보장, 창업 성공확률, 상권변화지표 코드의 선형 점수화 금지",
        "basis_ko": "알고리즘 명세 v2 §3.2, 33~38번 성장 후보 검증",
    },
]


def json_default(value: Any) -> Any:
    if hasattr(value, "item"):
        return value.item()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if pd.isna(value):
        return None
    return str(value)


def write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")


def write_json(data: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=json_default), encoding="utf-8")


def read_gold(file_name: str, usecols: list[str], python_engine: bool = False) -> pd.DataFrame:
    path = GOLD / file_name
    engine = "python" if python_engine else None
    return pd.read_csv(path, encoding="utf-8-sig", usecols=usecols, engine=engine)


def filter_quarter(df: pd.DataFrame, quarter: int = QUARTER) -> pd.DataFrame:
    return df[pd.to_numeric(df["기준_년분기_코드"], errors="coerce").eq(quarter)].copy()


def prepare_inputs() -> dict[str, pd.DataFrame]:
    sales = filter_quarter(
        read_gold(
            "gold_sales_strength_q_industry.csv",
            [
                "기준_년분기_코드",
                "상권_코드",
                "상권_코드_명",
                "서비스_업종_코드",
                "서비스_업종_코드_명",
                "당월_매출_금액",
                "당월_매출_건수",
                "점포당_매출_금액",
                "객단가_추정_금액",
                "store_join_status",
                "forbidden_claim_ko",
                "direct_score_allowed",
                "proxy_score_allowed",
            ],
        )
    )
    store = filter_quarter(
        read_gold(
            "gold_competition_q_industry.csv",
            [
                "기준_년분기_코드",
                "상권_코드",
                "상권_코드_명",
                "서비스_업종_코드",
                "서비스_업종_코드_명",
                "유사_업종_점포_수",
                "점포_수",
                "개업_율",
                "폐업_률",
                "score_use_status",
                "mapping_review_required",
                "SBDC_proxy_allowed",
                "동종_후보소분류_점포수",
                "forbidden_claim_ko",
                "direct_score_allowed",
                "proxy_score_allowed",
            ],
        )
    )
    demand = filter_quarter(
        read_gold(
            "gold_demand_q_area.csv",
            [
                "기준_년분기_코드",
                "상권_코드",
                "총_유동인구_수",
                "총_상주인구_수",
                "총_직장인구_수",
                "수요원천_존재_개수",
                "총_기초수요_프록시",
                "지출_총금액",
                "기초수요당_소비",
                "생활이동_proxy_allowed",
                "소비_proxy_allowed",
                "forbidden_claim_ko",
                "direct_score_allowed",
                "proxy_score_allowed",
            ],
        )
    )
    accessibility = filter_quarter(
        read_gold(
            "gold_accessibility_q_area.csv",
            [
                "기준_년분기_코드",
                "상권_코드",
                "총_집객시설_수",
                "철도역_수",
                "버스터미널_수",
                "지하철역_수",
                "버스정류장_수",
                "교통결절_시설수",
                "facility_observed",
                "facility_missing_not_imputed",
                "forbidden_claim_ko",
                "direct_score_allowed",
                "proxy_score_allowed",
            ],
        )
    )
    growth = filter_quarter(
        read_gold(
            "gold_growth_stability_q_industry.csv",
            [
                "기준_년분기_코드",
                "상권_코드",
                "상권_코드_명",
                "서비스_업종_코드",
                "서비스_업종_코드_명",
                "개업_율",
                "폐업_률",
                "상권_변화_지표_코드",
                "상권_변화_지표_명",
                "운영_서울대비_개월_차이",
                "매출_log_최근4분기_slope",
                "growth_score_status",
                "forbidden_claim_ko",
                "direct_score_allowed",
                "proxy_score_allowed",
            ],
            python_engine=True,
        )
    )
    return {
        "sales": sales,
        "store": store,
        "demand": demand,
        "accessibility": accessibility,
        "growth": growth,
    }


def unique_key_audit(name: str, df: pd.DataFrame, key_cols: list[str]) -> dict[str, Any]:
    return {
        "name": name,
        "rows": int(len(df)),
        "key_cols": "+".join(key_cols),
        "unique_key_rows": int(df[key_cols].drop_duplicates().shape[0]),
        "duplicate_key_rows": int(df.duplicated(key_cols).sum()),
        "key_null_rows": int(df[key_cols].isna().any(axis=1).sum()),
    }


def build_driver_and_join_audit(inputs: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame]:
    key = ["상권_코드", "서비스_업종_코드"]
    area_key = ["상권_코드"]
    sales = inputs["sales"]
    store = inputs["store"]
    demand = inputs["demand"]
    accessibility = inputs["accessibility"]
    growth = inputs["growth"]

    driver = pd.concat(
        [
            sales[key + ["상권_코드_명", "서비스_업종_코드_명"]],
            store[key + ["상권_코드_명", "서비스_업종_코드_명"]],
        ],
        ignore_index=True,
    ).drop_duplicates(key)

    audits: list[dict[str, Any]] = []
    for name, df, keys in [
        ("sales_key", sales, key),
        ("store_key", store, key),
        ("demand_area_key", demand, area_key),
        ("accessibility_area_key", accessibility, area_key),
        ("growth_area_industry_key", growth, key),
    ]:
        audits.append({**unique_key_audit(name, df, keys), "join_step": "source_key_uniqueness", "before_rows": None, "after_rows": None, "fanout_rows": None})

    growth_area = growth[
        ["상권_코드", "상권_변화_지표_코드", "상권_변화_지표_명", "운영_서울대비_개월_차이"]
    ].drop_duplicates()
    audits.append(
        {
            **unique_key_audit("growth_area_evidence_dedup", growth_area, ["상권_코드"]),
            "join_step": "area_evidence_dedup",
            "before_rows": None,
            "after_rows": None,
            "fanout_rows": None,
        }
    )

    joined = driver.copy()
    join_specs = [
        ("join_sales", sales.drop(columns=["기준_년분기_코드"]), key),
        ("join_store", store.drop(columns=["기준_년분기_코드"]), key),
        ("join_demand_area", demand.drop(columns=["기준_년분기_코드"]), area_key),
        ("join_accessibility_area", accessibility.drop(columns=["기준_년분기_코드"]), area_key),
        ("join_growth_area_evidence", growth_area, area_key),
    ]
    for step, right, keys in join_specs:
        before = len(joined)
        joined = joined.merge(right, on=keys, how="left", validate="many_to_one", suffixes=("", f"_{step}"))
        after = len(joined)
        audits.append(
            {
                "name": step,
                "rows": int(len(right)),
                "key_cols": "+".join(keys),
                "unique_key_rows": int(right[keys].drop_duplicates().shape[0]),
                "duplicate_key_rows": int(right.duplicated(keys).sum()),
                "key_null_rows": int(right[keys].isna().any(axis=1).sum()),
                "join_step": step,
                "before_rows": int(before),
                "after_rows": int(after),
                "fanout_rows": int(after - before),
            }
        )
    return joined, pd.DataFrame(audits)


def sample_value(row: pd.Series, col: str) -> Any:
    return row[col] if col in row.index else None


def build_sample_payload(joined: pd.DataFrame) -> dict[str, Any]:
    selected = joined[
        joined["상권_코드"].astype(str).eq(SAMPLE_TRADE_AREA)
        & joined["서비스_업종_코드"].astype(str).eq(SAMPLE_INDUSTRY)
    ]
    if selected.empty:
        raise RuntimeError("샘플 상권×업종 payload 행을 찾지 못했습니다.")
    row = selected.iloc[0]
    return {
        "contract_version": VERSION,
        "quarter": QUARTER,
        "driver_key": {
            "상권_코드": sample_value(row, "상권_코드"),
            "상권_코드_명": sample_value(row, "상권_코드_명"),
            "서비스_업종_코드": sample_value(row, "서비스_업종_코드"),
            "서비스_업종_코드_명": sample_value(row, "서비스_업종_코드_명"),
        },
        "official_current_axes": {
            "sales": {
                "source": "gold_sales_strength_q_industry.csv",
                "indicators": {
                    "당월_매출_금액": sample_value(row, "당월_매출_금액"),
                    "점포당_매출_금액": sample_value(row, "점포당_매출_금액"),
                },
                "evidence_only": {
                    "객단가_추정_금액": sample_value(row, "객단가_추정_금액"),
                    "store_join_status": sample_value(row, "store_join_status"),
                },
                "forbidden_claim_ko": sample_value(row, "forbidden_claim_ko"),
            },
            "competition": {
                "source": "gold_competition_q_industry.csv",
                "indicators": {
                    "유사_업종_점포_수": sample_value(row, "유사_업종_점포_수"),
                    "점포_수": sample_value(row, "점포_수"),
                    "SBDC_동종_점포수": sample_value(row, "동종_후보소분류_점포수"),
                },
                "derived_formula_ko": "동종_과밀도=유사_업종_점포_수/총_유동인구_수, 상권_집적_규모=상권 내 전 업종 점포수 합",
                "forbidden_claim_ko": sample_value(row, "forbidden_claim_ko_join_store"),
            },
            "demand": {
                "source": "gold_demand_q_area.csv",
                "indicators": {
                    "총_유동인구_수": sample_value(row, "총_유동인구_수"),
                    "총_상주인구_수": sample_value(row, "총_상주인구_수"),
                    "총_직장인구_수": sample_value(row, "총_직장인구_수"),
                    "지출_총금액": sample_value(row, "지출_총금액"),
                    "기초수요당_소비": sample_value(row, "기초수요당_소비"),
                },
                "proxy_flags": {
                    "생활이동_proxy_allowed": sample_value(row, "생활이동_proxy_allowed"),
                    "소비_proxy_allowed": sample_value(row, "소비_proxy_allowed"),
                },
                "forbidden_claim_ko": sample_value(row, "forbidden_claim_ko_join_demand_area"),
            },
            "accessibility": {
                "source": "gold_accessibility_q_area.csv",
                "indicators": {
                    "총_집객시설_수": sample_value(row, "총_집객시설_수"),
                    "교통결절_시설수": sample_value(row, "교통결절_시설수"),
                    "버스정류장_수": sample_value(row, "버스정류장_수"),
                },
                "missing_policy": {
                    "facility_observed": sample_value(row, "facility_observed"),
                    "facility_missing_not_imputed": sample_value(row, "facility_missing_not_imputed"),
                },
                "forbidden_claim_ko": sample_value(row, "forbidden_claim_ko_join_accessibility_area"),
            },
        },
        "candidate_axes_not_in_current_score": {
            "growth_stability": {
                "source": "gold_growth_stability_q_industry.csv",
                "status": "candidate_only",
                "indicators": {
                    "상권_변화_지표_코드": sample_value(row, "상권_변화_지표_코드"),
                    "상권_변화_지표_명": sample_value(row, "상권_변화_지표_명"),
                    "운영_서울대비_개월_차이": sample_value(row, "운영_서울대비_개월_차이"),
                },
                "forbidden_claim_ko": "성장률 보장, 창업 성공확률, 상권변화지표 코드의 선형 점수화 금지",
            }
        },
        "join_policy": {
            "driver_row_count": int(len(joined)),
            "single_feature_mart_forbidden": True,
            "runtime_lookup_required": True,
            "fanout_rows_allowed": 0,
        },
    }


def add_validation(rows: list[dict[str, Any]], check_id: str, item: str, observed: Any, expected: Any, passed: bool, reason_ko: str) -> None:
    rows.append(
        {
            "check_id": check_id,
            "item": item,
            "observed": json.dumps(observed, ensure_ascii=False, default=json_default) if isinstance(observed, (list, dict)) else observed,
            "expected": json.dumps(expected, ensure_ascii=False, default=json_default) if isinstance(expected, (list, dict)) else expected,
            "pass": bool(passed),
            "reason_ko": reason_ko,
        }
    )


def validate_contract(contract: pd.DataFrame, join_audit: pd.DataFrame, joined: pd.DataFrame, sample_payload: dict[str, Any]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    add_validation(rows, "77-V01", "payload 계약 section 수", len(contract), 6, len(contract) == 6, "driver, 4개 현재입지 축, 성장 후보 section이 모두 있어야 한다.")

    source_key_bad = join_audit[
        join_audit["join_step"].isin(["source_key_uniqueness", "area_evidence_dedup"])
        & ((join_audit["duplicate_key_rows"] != 0) | (join_audit["key_null_rows"] != 0))
    ].to_dict("records")
    add_validation(rows, "77-V02", "source key 유일성", source_key_bad, [], len(source_key_bad) == 0, "조인 대상 오른쪽 테이블은 many_to_one을 만족해야 한다.")

    fanout_bad = join_audit[join_audit["fanout_rows"].fillna(0).ne(0)].to_dict("records")
    add_validation(rows, "77-V03", "조인 fan-out 0", fanout_bad, [], len(fanout_bad) == 0, "상권 grain 자료를 업종 행에 붙여도 행수가 늘면 안 된다.")

    driver_rows = len(joined)
    union_rows = int(join_audit.loc[join_audit["name"].eq("join_sales"), "before_rows"].iloc[0])
    add_validation(rows, "77-V04", "driver row 보존", driver_rows, union_rows, driver_rows == union_rows, "payload 조인은 기준분기 sales/store 키 합집합 행수를 보존해야 한다.")

    official_axes = sample_payload.get("official_current_axes", {})
    add_validation(rows, "77-V05", "공식 현재입지 4축 존재", sorted(official_axes.keys()), ["accessibility", "competition", "demand", "sales"], sorted(official_axes.keys()) == ["accessibility", "competition", "demand", "sales"], "현재입지 공식 payload는 sales/competition/demand/accessibility 4축만 가져야 한다.")

    candidate_axes = sample_payload.get("candidate_axes_not_in_current_score", {})
    add_validation(rows, "77-V06", "성장 후보 분리", candidate_axes.get("growth_stability", {}).get("status"), "candidate_only", candidate_axes.get("growth_stability", {}).get("status") == "candidate_only", "성장/안정성 후보는 현재입지 총점에 섞지 않는다.")

    forbidden_missing = contract.loc[~contract["forbidden_claim_ko"].astype(str).str.strip().astype(bool), "payload_section"].tolist()
    add_validation(rows, "77-V07", "금지표현 계약 존재", forbidden_missing, [], len(forbidden_missing) == 0, "각 payload section은 AI 리포트 과장 해석을 막는 금지문구가 있어야 한다.")

    docs_missing = [str(path.relative_to(ROOT)).replace("\\", "/") for path in RESEARCH_BASIS_DOCS if not path.exists()]
    add_validation(rows, "77-V08", "research 근거 문서 존재", docs_missing, [], len(docs_missing) == 0, "payload 계약은 research 명세와 검증 문서를 근거로 해야 한다.")

    mart_bad = bool(sample_payload.get("join_policy", {}).get("single_feature_mart_forbidden") is not True)
    add_validation(rows, "77-V09", "단일 feature mart 금지", mart_bad, False, not mart_bad, "payload는 런타임 조회 계약이지 모든 원천을 한 파일에 합치는 산출물이 아니다.")

    sample_key_ok = (
        str(sample_payload.get("driver_key", {}).get("상권_코드")) == SAMPLE_TRADE_AREA
        and str(sample_payload.get("driver_key", {}).get("서비스_업종_코드")) == SAMPLE_INDUSTRY
    )
    add_validation(rows, "77-V10", "샘플 payload target 확인", sample_payload.get("driver_key"), {"상권_코드": SAMPLE_TRADE_AREA, "서비스_업종_코드": SAMPLE_INDUSTRY}, sample_key_ok, "단건 리포트와 검증 샘플이 같은 상권×업종을 바라봐야 한다.")

    return pd.DataFrame(rows)


def build_report(contract: pd.DataFrame, join_audit: pd.DataFrame, summary: dict[str, Any], validations: pd.DataFrame) -> str:
    lines: list[str] = [
        "# 77. 알고리즘 payload v2 조인 계약",
        "",
        f"- 작성일: {datetime.now().strftime('%Y-%m-%d')}",
        f"- 버전: `{VERSION}`",
        f"- 기준분기: `{QUARTER}`",
        "",
        "## 목적",
        "",
        "76번에서 준비도를 통과한 direct score gold를 알고리즘이 어떤 키와 축으로 읽을지 payload 계약으로 고정했다. 이 계약은 모든 데이터를 한 파일로 합치는 feature mart가 아니라, 런타임에 필요한 gold를 코드 키로 조회하는 방식이다.",
        "",
        "## 요약",
        "",
        f"- contract sections: {summary['contract_sections']}",
        f"- driver rows: {summary['driver_rows']}",
        f"- fanout rows total: {summary['fanout_rows_total']}",
        f"- PASS: {summary['pass_count']}",
        f"- FAIL: {summary['fail_count']}",
        f"- decision: `{summary['decision']}`",
        "",
        "## payload section",
        "",
        "| section | axis | gold | key | official | candidate |",
        "|---|---|---|---|---:|---:|",
    ]
    for _, row in contract.iterrows():
        lines.append(f"| `{row['payload_section']}` | `{row['axis']}` | `{row['gold_file']}` | `{row['lookup_key']}` | {row['official_current_axis']} | {row['candidate_only']} |")
    lines.extend(
        [
            "",
            "## fan-out 감사",
            "",
            "| step | rows | unique keys | duplicate keys | before | after | fanout |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for _, row in join_audit.iterrows():
        lines.append(f"| `{row['name']}` | {row['rows']} | {row['unique_key_rows']} | {row['duplicate_key_rows']} | {row.get('before_rows', '')} | {row.get('after_rows', '')} | {row.get('fanout_rows', '')} |")
    lines.extend(
        [
            "",
            "## 검증표",
            "",
            "| check | 항목 | 결과 | 이유 |",
            "|---|---|---|---|",
        ]
    )
    for _, row in validations.iterrows():
        result = "PASS" if bool(row["pass"]) else "FAIL"
        lines.append(f"| {row['check_id']} | {row['item']} | {result} | {row['reason_ko']} |")
    lines.extend(
        [
            "",
            "## 해석",
            "",
            "payload v2는 기준분기 sales/store 상권×업종 키 합집합을 드라이버로 삼고, 상권 grain 자료는 many-to-one으로만 붙인다. 성장/안정성은 후보 section으로 분리되며 현재입지 총점에 합산하지 않는다.",
            "",
            "## 2보 전진 1보 후퇴",
            "",
            "1. 전진: direct gold를 알고리즘 payload section으로 분리했다.",
            "2. 전진: 상권 grain 조인이 상권×업종 드라이버 행수를 늘리지 않는지 검증했다.",
            "3. 후퇴: 성장 후보와 evidence-only 값을 공식 현재입지 4축 총점에 넣지 않았다.",
            "4. 후퇴: 모든 원천을 단일 feature mart로 합치지 않았다.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    contract = pd.DataFrame(PAYLOAD_CONTRACT_ROWS)
    inputs = prepare_inputs()
    joined, join_audit = build_driver_and_join_audit(inputs)
    sample_payload = build_sample_payload(joined)
    validations = validate_contract(contract, join_audit, joined, sample_payload)
    pass_count = int(validations["pass"].sum())
    fail_count = int((~validations["pass"]).sum())
    summary = {
        "version": VERSION,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "quarter": QUARTER,
        "contract_sections": int(len(contract)),
        "driver_rows": int(len(joined)),
        "driver_area_count": int(joined["상권_코드"].nunique()),
        "driver_industry_count": int(joined["서비스_업종_코드"].nunique()),
        "fanout_rows_total": int(join_audit["fanout_rows"].fillna(0).sum()),
        "sample_trade_area_code": SAMPLE_TRADE_AREA,
        "sample_industry_code": SAMPLE_INDUSTRY,
        "pass_count": pass_count,
        "fail_count": fail_count,
        "decision": "ALGORITHM_PAYLOAD_V2_JOIN_CONTRACT_PASS" if fail_count == 0 else "ALGORITHM_PAYLOAD_V2_JOIN_CONTRACT_FAIL",
    }
    write_csv(contract, OUT_CONTRACT)
    write_csv(join_audit, OUT_JOIN_AUDIT)
    write_csv(validations, OUT_VALIDATION)
    write_json(sample_payload, OUT_SAMPLE)
    write_json(summary, OUT_SUMMARY)
    OUT_DOC.parent.mkdir(parents=True, exist_ok=True)
    OUT_DOC.write_text(build_report(contract, join_audit, summary, validations), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=json_default))
    if fail_count:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
