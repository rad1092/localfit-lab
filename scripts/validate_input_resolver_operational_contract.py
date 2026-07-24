# -*- coding: utf-8 -*-
"""
위치/업종 입력 resolver 운영계약 통합 검증.

목적:
  - 사용자가 상권명과 업종명을 외워서 입력하는 구조를 운영 입력으로 보지 않는다.
  - 지도 클릭/주소/장소 검색 결과 좌표는 상권 polygon 후보로 변환한다.
  - 업종 선택은 대/중/세부 UI tree 또는 검색 후보를 거쳐 최종 서비스_업종_코드로 확정한다.
  - 점수 엔진에는 확정된 `상권_코드 + 서비스_업종_코드`만 전달한다.

이 검증은 점수 산정이 아니라 전처리-서빙 경계의 입력 계약 검증이다.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from resolve_rule_engine_inputs import (
    RESOLVER_VERSION,
    load_resolver_data,
    resolve_industry,
    resolve_location,
    run_self_test,
)


ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "datacorpus" / "_gold"
GOLD_VALIDATION = ROOT / "datacorpus" / "_gold_validation"
RULE_VALIDATION_DATA = ROOT / "datacorpus" / "_rule_validation"
RULE_VALIDATION_DOCS = ROOT / "research" / "rule_validation"

RUN_DATE = "2026-07-07"
VALIDATION_NUMBER = 66
VALIDATION_VERSION = "input_resolver_operational_contract.v0.1-20260707"
EXPECTED_LOOKUP_VERSION = "rule_input_lookup.v1.1-20260704"


def as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    text = "" if pd.isna(value) else str(value).strip().lower()
    return text in {"true", "1", "y", "yes"}


def add_check(
    rows: list[dict[str, Any]],
    validation_id: str,
    validation_name: str,
    observed: object,
    expected: object,
    passed: bool,
    reason_ko: str,
) -> None:
    rows.append(
        {
            "validation_id": validation_id,
            "validation_name": validation_name,
            "observed": observed,
            "expected": expected,
            "result": "PASS" if passed else "FAIL",
            "reason_ko": reason_ko,
        }
    )


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(path)
    return pd.read_csv(path, encoding="utf-8-sig", low_memory=False)


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def engine_gate(location_result: dict[str, Any], industry_result: dict[str, Any]) -> dict[str, Any]:
    """resolver 결과를 엔진 입력으로 넘겨도 되는지 판정한다."""
    location_ready = location_result.get("location_resolution_status") == "single_inside_confirmed"
    industry_ready = int(industry_result.get("match_count", 0)) == 1
    if location_ready and industry_ready:
        trade_area = location_result["resolved_trade_areas"][0]
        industry = industry_result["matches"][0]
        return {
            "engine_ready": True,
            "block_reason_ko": "",
            "engine_input": {
                "상권_코드": trade_area["trade_area_code"],
                "서비스_업종_코드": industry["service_industry_code"],
            },
        }
    reasons: list[str] = []
    if not location_ready:
        reasons.append("위치가 단일 상권으로 확정되지 않음")
    if not industry_ready:
        reasons.append("업종이 단일 서비스업종코드로 확정되지 않음")
    return {"engine_ready": False, "block_reason_ko": "; ".join(reasons), "engine_input": None}


def find_single_inside_case(data: Any) -> pd.Series:
    """대표좌표 기준 단일 상권 확정 케이스를 찾는다. 검증용 샘플이며 하드코딩 목록이 아니다."""
    candidates = data.locations.sort_values("상권_코드").head(300)
    for _, row in candidates.iterrows():
        result = resolve_location(
            float(row["representative_lon_wgs84"]),
            float(row["representative_lat_wgs84"]),
            data,
            nearest_limit=3,
        )
        if result["location_resolution_status"] == "single_inside_confirmed":
            return row
    raise RuntimeError("단일 polygon 확정 검증 샘플을 찾지 못했다.")


def build_smoke_cases(data: Any) -> tuple[pd.DataFrame, dict[str, Any]]:
    single_row = find_single_inside_case(data)
    single_loc = resolve_location(
        float(single_row["representative_lon_wgs84"]),
        float(single_row["representative_lat_wgs84"]),
        data,
        nearest_limit=5,
    )
    food_by_name = resolve_industry("한식음식점", data)
    single_gate = engine_gate(single_loc, food_by_name)

    itaewon = data.locations[data.locations["상권_코드"].astype(str) == "3001491"].iloc[0]
    multi_loc = resolve_location(
        float(itaewon["representative_lon_wgs84"]),
        float(itaewon["representative_lat_wgs84"]),
        data,
        nearest_limit=7,
    )
    multi_gate = engine_gate(multi_loc, food_by_name)

    outside_loc = resolve_location(126.0, 36.5, data, nearest_limit=7)
    outside_gate = engine_gate(outside_loc, food_by_name)

    food_by_code = resolve_industry("CS100001", data)
    broad_food_query = resolve_industry("음식점", data, limit=10)

    fallback_rows = data.industries[data.industries["UI_계층_근거"].astype(str) == "서울서비스코드_prefix_fallback"]
    fallback_row = fallback_rows.iloc[0]
    fallback_result = resolve_industry(str(fallback_row["서비스_업종_코드_명"]), data)

    cases = [
        {
            "case_id": "66-C01",
            "case_name": "단일 상권 좌표 + 업종명 exact",
            "input_ko": f"{single_row['상권_코드_명']} 대표좌표 + 한식음식점",
            "location_status": single_loc["location_resolution_status"],
            "inside_polygon_count": single_loc["inside_polygon_count"],
            "industry_match_count": food_by_name["match_count"],
            "engine_ready": single_gate["engine_ready"],
            "engine_input": json.dumps(single_gate["engine_input"], ensure_ascii=False),
            "expected_behavior_ko": "단일 상권과 단일 업종이면 엔진 입력으로 넘길 수 있다.",
        },
        {
            "case_id": "66-C02",
            "case_name": "중첩 상권 좌표",
            "input_ko": "이태원 대표좌표 + 한식음식점",
            "location_status": multi_loc["location_resolution_status"],
            "inside_polygon_count": multi_loc["inside_polygon_count"],
            "industry_match_count": food_by_name["match_count"],
            "engine_ready": multi_gate["engine_ready"],
            "engine_input": json.dumps(multi_gate["engine_input"], ensure_ascii=False),
            "expected_behavior_ko": "여러 상권에 포함되면 자동 확정하지 않고 사용자 선택 또는 별도 우선순위 규칙이 필요하다.",
        },
        {
            "case_id": "66-C03",
            "case_name": "서울 밖 좌표",
            "input_ko": "서울 밖 좌표(126.0, 36.5) + 한식음식점",
            "location_status": outside_loc["location_resolution_status"],
            "inside_polygon_count": outside_loc["inside_polygon_count"],
            "industry_match_count": food_by_name["match_count"],
            "engine_ready": outside_gate["engine_ready"],
            "engine_input": json.dumps(outside_gate["engine_input"], ensure_ascii=False),
            "expected_behavior_ko": "polygon 밖 좌표는 가까운 후보만 보여주고 엔진으로 바로 넘기지 않는다.",
        },
        {
            "case_id": "66-C04",
            "case_name": "업종 코드 exact",
            "input_ko": "CS100001",
            "location_status": "",
            "inside_polygon_count": "",
            "industry_match_count": food_by_code["match_count"],
            "engine_ready": food_by_code["match_count"] == 1,
            "engine_input": food_by_code["matches"][0]["service_industry_code"] if food_by_code["matches"] else "",
            "expected_behavior_ko": "코드가 오면 이름 검색 없이 서비스_업종_코드를 확정한다.",
        },
        {
            "case_id": "66-C05",
            "case_name": "업종 광역 검색어",
            "input_ko": "음식점",
            "location_status": "",
            "inside_polygon_count": "",
            "industry_match_count": broad_food_query["match_count"],
            "engine_ready": broad_food_query["match_count"] == 1,
            "engine_input": "",
            "expected_behavior_ko": "검색 후보가 여러 개면 사용자가 세부 업종을 선택하기 전까지 엔진으로 넘기지 않는다.",
        },
        {
            "case_id": "66-C06",
            "case_name": "fallback UI 업종",
            "input_ko": str(fallback_row["서비스_업종_코드_명"]),
            "location_status": "",
            "inside_polygon_count": "",
            "industry_match_count": fallback_result["match_count"],
            "engine_ready": fallback_result["match_count"] == 1,
            "engine_input": fallback_result["matches"][0]["service_industry_code"] if fallback_result["matches"] else "",
            "expected_behavior_ko": "SBDC 미매핑 또는 수동검토필요 업종도 선택은 가능하지만 SBDC 자동강매칭 계층으로 둔갑시키지 않는다.",
        },
    ]
    context = {
        "single_trade_area_code": str(single_row["상권_코드"]),
        "single_trade_area_name": str(single_row["상권_코드_명"]),
        "multi_inside_codes": [item["trade_area_code"] for item in multi_loc["resolved_trade_areas"]],
        "outside_nearby_count": len(outside_loc["nearby_boundary_candidates"]),
        "broad_food_match_count": broad_food_query["match_count"],
        "fallback_service_code": str(fallback_row["서비스_업종_코드"]),
        "fallback_service_name": str(fallback_row["서비스_업종_코드_명"]),
        "fallback_direct_score_allowed": as_bool(fallback_row["direct_score_allowed"]),
        "fallback_ui_source": str(fallback_row["UI_계층_근거"]),
        "fallback_sbdc_review_required": as_bool(fallback_row["SBDC_mapping_review_required"]),
        "fallback_sbdc_score_use_status": str(fallback_row["SBDC_score_use_status"]),
    }
    return pd.DataFrame(cases), context


def write_report(validation_df: pd.DataFrame, case_df: pd.DataFrame, summary: dict[str, Any]) -> None:
    RULE_VALIDATION_DATA.mkdir(parents=True, exist_ok=True)
    RULE_VALIDATION_DOCS.mkdir(parents=True, exist_ok=True)

    validation_path = RULE_VALIDATION_DATA / "66_input_resolver_operational_contract_validation.csv"
    cases_path = RULE_VALIDATION_DATA / "66_input_resolver_operational_smoke_cases.csv"
    summary_path = RULE_VALIDATION_DATA / "66_input_resolver_operational_contract_summary.json"
    md_path = RULE_VALIDATION_DOCS / "66_input_resolver_operational_contract_20260707.md"

    validation_df.to_csv(validation_path, index=False, encoding="utf-8-sig")
    case_df.to_csv(cases_path, index=False, encoding="utf-8-sig")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# 66. 입력 resolver 운영계약 통합 검증",
        "",
        f"작성일: {RUN_DATE}",
        "",
        "## 목적",
        "",
        "상권명·업종명을 외워서 넣는 임시 테스트 방식을 운영 계약으로 보지 않고, 화면 입력을 `상권_코드 + 서비스_업종_코드`로 안전하게 확정할 수 있는지 검증한다.",
        "",
        "## 근거 자료",
        "",
        "- `research/알고리즘_스펙_v1_20260703.md` §9: 위치·업종 입력 구조",
        "- `research/알고리즘_명세_v2_20260704.md` §2: 입력 계약",
        "- `research/rule_validation/26_input_lookup_hardcoding_removal_validation_20260704.md`: lookup 하드코딩 제거",
        "- `research/rule_validation/27_input_resolver_validation_20260704.md`: resolver 자체 검증",
        "- `research/rule_validation/40_industry_selection_fallback_hierarchy_validation_20260704.md`: 업종 UI fallback 계층",
        "- `research/rule_validation/41_location_resolver_boundary_adjacency_validation_20260704.md`: 지도 클릭 경계/인접 후보",
        "- `datacorpus/_gold/gold_location_input_lookup.csv`",
        "- `datacorpus/_gold/gold_location_spatial_index.csv`",
        "- `datacorpus/_gold/gold_location_boundary_vertices.csv`",
        "- `datacorpus/_gold/gold_industry_selection_hierarchy.csv`",
        "- `datacorpus/_gold/gold_industry_selection_tree.json`",
        "",
        "## 핵심 결과",
        "",
        f"- validation version: `{summary['validation_version']}`",
        f"- resolver version: `{summary['resolver_version']}`",
        f"- lookup version: `{summary['lookup_version']}`",
        f"- location rows: {summary['location_rows']:,}",
        f"- industry rows: {summary['industry_rows']:,}",
        f"- smoke cases: {summary['smoke_case_count']}",
        f"- PASS: {summary['pass_count']}",
        f"- FAIL: {summary['fail_count']}",
        f"- decision: `{summary['decision']}`",
        "",
        "## 검증 결과",
        "",
        "| validation_id | validation_name | observed | expected | result | reason_ko |",
        "|---|---|---|---|---|---|",
    ]
    for _, row in validation_df.iterrows():
        lines.append(
            "| {validation_id} | {validation_name} | {observed} | {expected} | {result} | {reason_ko} |".format(
                validation_id=row["validation_id"],
                validation_name=str(row["validation_name"]).replace("|", "/"),
                observed=str(row["observed"]).replace("|", "/"),
                expected=str(row["expected"]).replace("|", "/"),
                result=row["result"],
                reason_ko=str(row["reason_ko"]).replace("|", "/"),
            )
        )

    lines.extend(
        [
            "",
            "## 운영 smoke case",
            "",
            "| case_id | case_name | location_status | inside_polygon_count | industry_match_count | engine_ready | expected_behavior_ko |",
            "|---|---|---|---:|---:|---|---|",
        ]
    )
    for _, row in case_df.iterrows():
        lines.append(
            "| {case_id} | {case_name} | {location_status} | {inside_polygon_count} | {industry_match_count} | {engine_ready} | {expected_behavior_ko} |".format(
                case_id=row["case_id"],
                case_name=str(row["case_name"]).replace("|", "/"),
                location_status=str(row["location_status"]).replace("|", "/"),
                inside_polygon_count=row["inside_polygon_count"],
                industry_match_count=row["industry_match_count"],
                engine_ready=row["engine_ready"],
                expected_behavior_ko=str(row["expected_behavior_ko"]).replace("|", "/"),
            )
        )

    lines.extend(
        [
            "",
            "## 운영 규칙",
            "",
            "- 화면에는 이름과 계층을 보여줘도 되지만, 엔진에는 `상권_코드 + 서비스_업종_코드`만 넘긴다.",
            "- 지도 클릭 좌표가 여러 polygon에 포함되면 자동 단일 확정하지 않는다.",
            "- 서울 밖 좌표나 polygon 밖 좌표는 인접 후보 상태로 남기고 점수 엔진을 호출하지 않는다.",
            "- 업종 검색 결과가 여러 개면 사용자가 세부 업종을 선택하기 전까지 엔진을 호출하지 않는다.",
            "- fallback 업종은 UI 선택 가능성을 위한 것이며 SBDC 자동강매칭 계층이나 SBDC 직접 근거로 둔갑시키지 않는다.",
            "- 이 검증은 점수 산정 검증이 아니며 입력 변환 계약 검증이다.",
            "",
            "## 2보 전진 1보 후퇴",
            "",
            "1. 전진: 위치 lookup, spatial index, boundary vertices, 업종 hierarchy/tree가 모두 운영 입력 후보로 연결되는지 확인했다.",
            "2. 전진: 단일 확정, 다중 포함, 외부 좌표, 코드 exact, 이름 exact, 광역 검색어, fallback 업종을 smoke case로 분리했다.",
            "3. 후퇴: 중첩 상권과 외부 좌표는 자동 엔진 입력으로 승격하지 않았다.",
            "4. 후퇴: 업종 광역 검색어와 fallback 계층은 선택 보조이며 SBDC 자동강매칭 근거로 승격하지 않았다.",
            "5. 후퇴: 프론트엔드/API 연결은 아직 별도 구현 검증 대상이며, 이번 단계는 데이터 기반 운영계약 확정이다.",
            "",
            "## 산출물",
            "",
            "- `datacorpus/_rule_validation/66_input_resolver_operational_contract_validation.csv`",
            "- `datacorpus/_rule_validation/66_input_resolver_operational_smoke_cases.csv`",
            "- `datacorpus/_rule_validation/66_input_resolver_operational_contract_summary.json`",
            "- `research/rule_validation/66_input_resolver_operational_contract_20260707.md`",
        ]
    )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    data = load_resolver_data()
    locations = data.locations
    industries = data.industries
    spatial_index = data.spatial_index
    vertices = data.vertices

    validation40 = load_json(RULE_VALIDATION_DATA / "40_industry_selection_fallback_hierarchy_summary.json")
    validation41 = load_json(RULE_VALIDATION_DATA / "41_location_resolver_boundary_adjacency_summary.json")
    validation27 = load_json(GOLD_VALIDATION / "27_input_resolver_summary.json")
    case_df, case_context = build_smoke_cases(data)

    rows: list[dict[str, Any]] = []

    add_check(
        rows,
        "66-V01",
        "입력 lookup은 상권 1,650개와 업종 100개를 보존",
        f"location_rows={len(locations)}, location_unique={locations['상권_코드'].nunique()}, industry_rows={len(industries)}, industry_unique={industries['서비스_업종_코드'].nunique()}",
        "location 1650 unique / industry 100 unique",
        len(locations) == 1650
        and locations["상권_코드"].nunique() == 1650
        and len(industries) == 100
        and industries["서비스_업종_코드"].nunique() == 100,
        "상권 또는 업종 universe가 줄어들면 사용자가 선택할 수 없는 대상이 생기고, 하드코딩 후보 목록으로 퇴행한다.",
    )
    lookup_versions = sorted(
        set(locations["lookup_version"].astype(str).unique().tolist())
        | set(spatial_index["lookup_version"].astype(str).unique().tolist())
        | set(industries["lookup_version"].astype(str).unique().tolist())
    )
    add_check(
        rows,
        "66-V02",
        "위치/업종 lookup 버전은 같은 운영계약 버전",
        ",".join(lookup_versions),
        EXPECTED_LOOKUP_VERSION,
        lookup_versions == [EXPECTED_LOOKUP_VERSION],
        "위치와 업종 lookup 버전이 섞이면 화면 후보와 엔진 입력 키가 서로 다른 기준으로 확정된다.",
    )
    add_check(
        rows,
        "66-V03",
        "상권 spatial index와 polygon vertices는 전 상권을 덮음",
        f"spatial_index={len(spatial_index)}, vertex_trade_areas={vertices['상권_코드'].nunique()}, empty_shapes={sum(1 for shapes in data.boundary_shapes.values() if not shapes)}",
        "spatial_index 1650 / vertex trade_area 1650 / empty_shapes 0",
        len(spatial_index) == 1650 and vertices["상권_코드"].nunique() == 1650 and all(data.boundary_shapes.values()),
        "지도 클릭을 상권명 검색으로 대체하지 않으려면 bbox 후보와 polygon 포함 판정을 전 상권에 대해 보유해야 한다.",
    )
    add_check(
        rows,
        "66-V04",
        "기존 입력 resolver 자체 검증은 무실패",
        f"pass={validation27.get('pass_count')}, fail={validation27.get('fail_count')}",
        "fail=0 and pass>=10",
        int(validation27.get("fail_count", -1)) == 0 and int(validation27.get("pass_count", 0)) >= 10,
        "운영 smoke는 27번 자체 검증을 대체하지 않고 그 위에 얹는 통합 계약이다.",
    )
    add_check(
        rows,
        "66-V05",
        "업종 UI fallback 계층 검증은 무실패",
        f"pass={validation40.get('validation_pass_count')}, fail={validation40.get('validation_fail_count')}, fallback={validation40.get('ui_fallback_rows')}",
        "fail=0 and fallback=60",
        int(validation40.get("validation_fail_count", -1)) == 0 and int(validation40.get("ui_fallback_rows", -1)) == 60,
        "SBDC 미매핑 또는 수동검토필요 업종은 삭제하지 않고 선택 가능하게 하되, SBDC 자동강매칭 계층으로 승격하지 않아야 한다.",
    )
    add_check(
        rows,
        "66-V06",
        "지도 클릭 위치 resolver 경계/인접 후보 검증은 무실패",
        f"pass={validation41.get('validation_pass_count')}, fail={validation41.get('validation_fail_count')}",
        "fail=0 and pass>=6",
        int(validation41.get("validation_fail_count", -1)) == 0 and int(validation41.get("validation_pass_count", 0)) >= 6,
        "지도 클릭 좌표는 bbox가 아니라 polygon 포함과 EPSG:5181 경계거리 후보로 처리해야 한다.",
    )
    ready_by_case = dict(zip(case_df["case_id"], case_df["engine_ready"]))
    add_check(
        rows,
        "66-V07",
        "단일 상권 + 단일 업종만 엔진 입력 가능",
        f"C01={ready_by_case.get('66-C01')}, C02={ready_by_case.get('66-C02')}, C03={ready_by_case.get('66-C03')}",
        "C01 true / C02 false / C03 false",
        bool(ready_by_case.get("66-C01")) and not bool(ready_by_case.get("66-C02")) and not bool(ready_by_case.get("66-C03")),
        "다중 포함 좌표나 서울 밖 좌표를 자동으로 점수 엔진에 넘기면 잘못된 리포트가 생성된다.",
    )
    add_check(
        rows,
        "66-V08",
        "업종 코드 exact와 광역 검색어를 구분",
        f"code_ready={ready_by_case.get('66-C04')}, broad_ready={ready_by_case.get('66-C05')}, broad_match_count={case_context['broad_food_match_count']}",
        "code true / broad false / broad_match_count>1",
        bool(ready_by_case.get("66-C04"))
        and not bool(ready_by_case.get("66-C05"))
        and int(case_context["broad_food_match_count"]) > 1,
        "검색어가 넓어 여러 업종 후보가 나오면 사용자가 세부 업종을 선택하기 전까지 엔진 호출을 막아야 한다.",
    )
    add_check(
        rows,
        "66-V09",
        "fallback 업종은 선택 가능하되 SBDC 자동강매칭 계층이 아님",
        "fallback={code} {name}, ui_source={source}, sbdc_review_required={review}, sbdc_status={status}, direct_score_allowed={direct}".format(
            code=case_context["fallback_service_code"],
            name=case_context["fallback_service_name"],
            source=case_context["fallback_ui_source"],
            review=case_context["fallback_sbdc_review_required"],
            status=case_context["fallback_sbdc_score_use_status"],
            direct=case_context["fallback_direct_score_allowed"],
        ),
        "fallback selectable and SBDC review-required, not SBDC auto hierarchy",
        bool(ready_by_case.get("66-C06"))
        and case_context["fallback_ui_source"] == "서울서비스코드_prefix_fallback"
        and bool(case_context["fallback_sbdc_review_required"]),
        "UI fallback은 사용자가 업종을 찾기 위한 장치이지 수동검토필요 SBDC 후보를 자동강매칭 계층으로 둔갑시키는 장치가 아니다.",
    )
    forbidden_payload_cols = {"상권_코드_명", "서비스_업종_코드_명", "display_label", "selection_path"}
    c01_input = json.loads(case_df.loc[case_df["case_id"] == "66-C01", "engine_input"].iloc[0])
    add_check(
        rows,
        "66-V10",
        "엔진 입력 payload는 코드 키만 포함",
        ",".join(sorted(c01_input.keys())),
        "상권_코드,서비스_업종_코드",
        set(c01_input.keys()) == {"상권_코드", "서비스_업종_코드"} and not forbidden_payload_cols.intersection(c01_input.keys()),
        "화면 표시명이나 선택 경로가 엔진 조인 키로 들어가면 이름 기반 조인의 위험이 다시 생긴다.",
    )

    validation_df = pd.DataFrame(rows)
    pass_count = int((validation_df["result"] == "PASS").sum())
    fail_count = int((validation_df["result"] == "FAIL").sum())
    summary = {
        "validation_number": VALIDATION_NUMBER,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "validation_version": VALIDATION_VERSION,
        "resolver_version": RESOLVER_VERSION,
        "lookup_version": EXPECTED_LOOKUP_VERSION,
        "location_rows": int(len(locations)),
        "industry_rows": int(len(industries)),
        "smoke_case_count": int(len(case_df)),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "decision": "INPUT_RESOLVER_OPERATIONAL_CONTRACT_PASS" if fail_count == 0 else "INPUT_RESOLVER_OPERATIONAL_CONTRACT_FAIL",
        "next_step": "web_api_layer_connection_or_preprocessing_next_gold",
    }
    write_report(validation_df, case_df, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if fail_count:
        raise SystemExit(fail_count)


if __name__ == "__main__":
    main()
