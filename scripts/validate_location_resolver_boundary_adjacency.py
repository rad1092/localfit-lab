# -*- coding: utf-8 -*-
"""
지도 클릭 위치 resolver의 polygon 포함/인접 후보 계약 검증.

이 검증은 점수 산정 검증이 아니다. 사용자가 상권명을 외워 입력하지 않아도
지도 좌표 하나로 후보 상권을 찾고, 최종 점수 엔진에는 상권_코드만 넘길 수
있는지를 확인한다.
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
RULE_VALIDATION_DATA = ROOT / "datacorpus" / "_rule_validation"
RULE_VALIDATION_DOCS = ROOT / "research" / "rule_validation"
RUN_DATE = "2026-07-04"
VALIDATION_VERSION = "location_resolver_boundary_adjacency.v1.0-20260704"


def add_check(
    rows: list[dict[str, Any]],
    rule_name: str,
    observed: object,
    expected: object,
    passed: bool,
    reason_ko: str,
) -> None:
    rows.append(
        {
            "rule_name": rule_name,
            "observed": observed,
            "expected": expected,
            "result": "PASS" if passed else "FAIL",
            "reason_ko": reason_ko,
        }
    )


def write_report(validation_df: pd.DataFrame, summary: dict[str, Any]) -> None:
    RULE_VALIDATION_DATA.mkdir(parents=True, exist_ok=True)
    RULE_VALIDATION_DOCS.mkdir(parents=True, exist_ok=True)

    csv_path = RULE_VALIDATION_DATA / "41_location_resolver_boundary_adjacency_validation.csv"
    json_path = RULE_VALIDATION_DATA / "41_location_resolver_boundary_adjacency_summary.json"
    md_path = RULE_VALIDATION_DOCS / "41_location_resolver_boundary_adjacency_validation_20260704.md"

    validation_df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# 지도 클릭 위치 resolver 경계/인접 후보 검증",
        "",
        f"작성일: {RUN_DATE}",
        "",
        "## 1. 목적",
        "",
        "위치 입력을 더 이상 상권명 하드코딩이나 드롭다운 암기에 의존하지 않고, 지도 클릭 좌표에서 상권 후보를 찾을 수 있는지 검증한다.",
        "",
        "이 검증은 점수 산정이 아니라 입력 변환 검증이다. 점수 엔진에는 최종적으로 `상권_코드`와 `서비스_업종_코드`만 전달해야 한다.",
        "",
        "## 2. 근거 자료와 사용 범위",
        "",
        "- `datacorpus/_gold/gold_location_input_lookup.csv`: 표시명, 검색문, 상권 코드 확정용 lookup",
        "- `datacorpus/_gold/gold_location_spatial_index.csv`: 지도 클릭 좌표의 bbox 1차 후보 필터",
        "- `datacorpus/_gold/gold_location_boundary_vertices.csv`: polygon 포함 판정과 경계거리 계산 원천",
        "- `research/rule_validation/15_trade_area_boundary_silver_validation_20260704.md`: 상권 경계 원천 검증",
        "- `research/rule_validation/26_input_lookup_hardcoding_removal_validation_20260704.md`: 위치/업종 lookup 생성 계약",
        "- `research/rule_validation/27_input_resolver_validation_20260704.md`: resolver 자체 검증",
        "",
        "## 3. 검증 결과",
        "",
        f"- resolver_version: `{summary['resolver_version']}`",
        f"- PASS: {summary['validation_pass_count']}",
        f"- FAIL: {summary['validation_fail_count']}",
        f"- 판정: {summary['decision']}",
        "",
        "| 규칙 | 관측값 | 기대값 | 결과 | 이유 |",
        "|---|---|---|---|---|",
    ]
    for _, row in validation_df.iterrows():
        lines.append(
            "| {rule_name} | {observed} | {expected} | {result} | {reason_ko} |".format(
                rule_name=str(row["rule_name"]).replace("|", "/"),
                observed=str(row["observed"]).replace("|", "/"),
                expected=str(row["expected"]).replace("|", "/"),
                result=row["result"],
                reason_ko=str(row["reason_ko"]).replace("|", "/"),
            )
        )

    lines.extend(
        [
            "",
            "## 4. 2보 전진 1보 후퇴 검토",
            "",
            "1. 전진: 지도 좌표 입력을 WGS84에서 EPSG:5181로 변환해 polygon 포함과 거리 계산을 같은 metric 좌표계에서 처리했다.",
            "2. 전진: polygon 내부 후보뿐 아니라 경계거리 기반 인접 후보를 함께 반환해, 사용자가 위치를 외우지 않아도 후보를 고를 수 있게 했다.",
            "3. 후퇴: bbox 후보만으로 상권을 확정하지 않는다. bbox는 성능용 1차 필터이고 최종 판정은 polygon이다.",
            "4. 후퇴: 여러 상권에 동시에 포함되는 좌표는 자동 단일 확정하지 않는다. 후보를 보여주거나 별도 우선순위 규칙을 적용해야 한다.",
            "5. 후퇴: 서울 밖 좌표는 점수 엔진으로 바로 넘기지 않고 후보 비교 상태로 남긴다.",
            "",
            "## 5. 다음 작업",
            "",
            "1. 웹/API 레이어에서 이 resolver 또는 동일 계약을 호출한다.",
            "2. 지도 클릭 시 `resolved_trade_areas`와 `nearby_boundary_candidates`를 같이 표시한다.",
            "3. 다중 포함 좌표의 자동 우선순위 규칙을 만들 경우, 별도 문서와 백데이터 검증을 추가한다.",
        ]
    )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    data = load_resolver_data()
    rows: list[dict[str, Any]] = []

    base_validation = run_self_test(data)
    add_check(
        rows,
        "resolver 자체 검증은 10개 규칙 모두 통과",
        f"pass={(base_validation['result'] == 'PASS').sum()}, fail={(base_validation['result'] == 'FAIL').sum()}",
        "PASS 10 / FAIL 0",
        int((base_validation["result"] == "PASS").sum()) == 10
        and int((base_validation["result"] == "FAIL").sum()) == 0,
        "위치 resolver는 단순 파일 존재가 아니라 대표좌표, 서울 밖 좌표, 다중 포함, 업종 코드 연결까지 함께 확인해야 한다.",
    )

    add_check(
        rows,
        "상권 polygon shape는 모든 상권 코드에 대해 생성",
        f"shape_codes={len(data.boundary_shapes)}, empty={sum(1 for shapes in data.boundary_shapes.values() if not shapes)}",
        "1650개 / empty 0",
        len(data.boundary_shapes) == 1650 and all(data.boundary_shapes.values()),
        "지도 클릭 좌표를 상권명 목록으로 맞추는 것은 하드코딩에 가깝기 때문에, 전 상권 경계 도형이 필요하다.",
    )

    itaewon = data.locations[data.locations["상권_코드"].astype(str) == "3001491"].iloc[0]
    loc_result = resolve_location(
        float(itaewon["representative_lon_wgs84"]),
        float(itaewon["representative_lat_wgs84"]),
        data,
        nearest_limit=7,
    )
    inside_codes = [item["trade_area_code"] for item in loc_result["resolved_trade_areas"]]
    nearby_codes = [item["trade_area_code"] for item in loc_result["nearby_boundary_candidates"]]
    add_check(
        rows,
        "이태원 대표좌표는 polygon 포함 후보에 원래 상권 포함",
        f"status={loc_result['location_resolution_status']}, inside_codes={inside_codes}",
        "3001491 포함",
        "3001491" in inside_codes,
        "대표 좌표가 자기 상권으로 돌아오지 않으면 지도 클릭 기반 입력 변환을 신뢰할 수 없다.",
    )
    add_check(
        rows,
        "지도 클릭 결과는 경계거리 기반 인접 후보를 함께 반환",
        f"nearby_count={len(nearby_codes)}, first_distances={[item['boundary_distance_m'] for item in loc_result['nearby_boundary_candidates'][:3]]}",
        "후보 1개 이상 / 포함 상권 boundary_distance_m=0",
        len(loc_result["nearby_boundary_candidates"]) > 0
        and any(item["trade_area_code"] == "3001491" and float(item["boundary_distance_m"]) == 0.0 for item in loc_result["nearby_boundary_candidates"]),
        "상권을 하나만 찍어 외우는 방식이 아니라, 포함 상권과 인접 상권 후보를 같이 보여줘야 사용자가 위치를 고를 수 있다.",
    )

    outside = resolve_location(126.0, 36.5, data, nearest_limit=7)
    outside_distances = [item["boundary_distance_m"] for item in outside["nearby_boundary_candidates"]]
    add_check(
        rows,
        "서울 밖 좌표는 점수 확정이 아니라 인접 후보 상태",
        f"status={outside['location_resolution_status']}, inside={outside['inside_polygon_count']}, distances={outside_distances[:5]}",
        "outside_nearest_candidates / inside=0 / 경계거리 후보 정렬",
        outside["location_resolution_status"] == "outside_nearest_candidates"
        and outside["inside_polygon_count"] == 0
        and outside_distances == sorted(outside_distances),
        "상권 밖 좌표를 가장 가까운 상권으로 자동 확정하면 잘못된 리포트가 생기므로 후보 상태를 유지해야 한다.",
    )

    industry = resolve_industry("한식음식점", data)
    add_check(
        rows,
        "위치 resolver와 업종 resolver는 최종 알고리즘 키만 넘길 수 있음",
        f"location_keys={inside_codes[:3]}, industry_match={industry['matches'][0]['service_industry_code'] if industry['matches'] else None}",
        "상권_코드 후보 + CS100001",
        bool(inside_codes) and industry["matches"] and industry["matches"][0]["service_industry_code"] == "CS100001",
        "화면에서는 이름을 보여주더라도 알고리즘 호출은 코드 기반이어야 하므로 위치와 업종의 최종 키 계약을 같이 확인한다.",
    )

    validation_df = pd.DataFrame(rows)
    pass_count = int((validation_df["result"] == "PASS").sum())
    fail_count = int((validation_df["result"] == "FAIL").sum())
    summary = {
        "run_date": RUN_DATE,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "validation_version": VALIDATION_VERSION,
        "resolver_version": RESOLVER_VERSION,
        "validation_pass_count": pass_count,
        "validation_fail_count": fail_count,
        "decision": "위치_resolver_경계거리_검증통과" if fail_count == 0 else "위치_resolver_보완필요",
        "decision_reason_ko": "지도 클릭 좌표는 bbox 후보, polygon 포함, EPSG:5181 경계거리 기반 인접 후보로 처리 가능하다. 점수 산정은 아직 하지 않고 상권_코드 확정 전 단계로만 사용한다.",
    }
    write_report(validation_df, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if fail_count:
        raise SystemExit(fail_count)


if __name__ == "__main__":
    main()
