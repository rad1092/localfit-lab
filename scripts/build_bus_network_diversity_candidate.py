# -*- coding: utf-8 -*-
"""
버스 노선-정류장 네트워크 다양성 접근성 후보 생성/검증.

근거:
  - 21차 버스 노선-정류장 마스터 silver 검증에서 RTE_ID와 승하차량 노선번호
    직접 조인을 금지했다.
  - 정류소 위치 마스터와 exact_match된 정류소만 좌표 기반 상권 후보 계산에 사용한다.
  - 이 후보는 승객 수, 실제 도보시간, 실제 이동시간, 실제 방문확률이 아니다.

역할:
  - 상권 polygon 내부/250m/500m 주변의 정류소별 경유 노선 다양성을 집계한다.
  - 공식 접근성 점수를 덮어쓰지 않고 evidence-only 후보 gold로 남긴다.
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from pyproj import Transformer
from shapely.geometry import Point

from resolve_rule_engine_inputs import build_boundary_shapes


ROOT = Path(__file__).resolve().parents[1]
SILVER = ROOT / "datacorpus" / "_silver"
GOLD = ROOT / "datacorpus" / "_gold"
RULE_VALIDATION_DATA = ROOT / "datacorpus" / "_rule_validation"
RULE_VALIDATION_DOCS = ROOT / "research" / "rule_validation"

RUN_DATE = "2026-07-07"
VALIDATION_NUMBER = 67
CANDIDATE_VERSION = "bus_network_diversity_candidate.v0.1-20260707"
OUTPUT_GOLD = GOLD / "gold_accessibility_bus_network_diversity_candidate.csv"
VALIDATION_CSV = RULE_VALIDATION_DATA / "67_bus_network_diversity_candidate_validation.csv"
SUMMARY_JSON = RULE_VALIDATION_DATA / "67_bus_network_diversity_candidate_summary.json"
SAMPLE_CSV = RULE_VALIDATION_DATA / "67_bus_network_diversity_candidate_sample_rows.csv"
MD_REPORT = RULE_VALIDATION_DOCS / "67_bus_network_diversity_candidate_20260707.md"

WGS84_TO_EPSG5181 = Transformer.from_crs("EPSG:4326", "EPSG:5181", always_xy=True)


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(path)
    return pd.read_csv(path, encoding="utf-8-sig", low_memory=False)


def write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")


def as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    text = "" if pd.isna(value) else str(value).strip().lower()
    return text in {"true", "1", "y", "yes"}


def percentile_score(series: pd.Series) -> pd.Series:
    """후보 비교용 백분위 점수다. 공식 점수로 승격하지 않는다."""
    numeric = pd.to_numeric(series, errors="coerce").fillna(0)
    if numeric.nunique(dropna=False) <= 1:
        return pd.Series([0.0] * len(numeric), index=series.index)
    return numeric.rank(method="average", pct=True) * 100


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


def prepare_stops(stop_summary: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    exact = stop_summary[stop_summary["정류소위치_결합상태"].astype(str).eq("exact_match")].copy()
    exact["경도"] = pd.to_numeric(exact["경도"], errors="coerce")
    exact["위도"] = pd.to_numeric(exact["위도"], errors="coerce")
    exact["경유_노선_ID수"] = pd.to_numeric(exact["경유_노선_ID수"], errors="coerce").fillna(0)
    exact["승하차자료_정류소존재_bool"] = exact["승하차자료_정류소존재"].map(as_bool)
    exact = exact.dropna(subset=["경도", "위도"]).copy()
    xy = [WGS84_TO_EPSG5181.transform(lon, lat) for lon, lat in zip(exact["경도"], exact["위도"], strict=False)]
    exact["x_epsg5181"] = [item[0] for item in xy]
    exact["y_epsg5181"] = [item[1] for item in xy]
    exact["log1p_route_count"] = exact["경유_노선_ID수"].map(lambda value: math.log1p(max(float(value), 0.0)))
    summary = {
        "exact_match_stop_rows": int(len(exact)),
        "exact_match_unique_stop_ids": int(exact["정류소_ID"].nunique()),
        "exact_match_with_passenger_id": int(exact["승하차자료_정류소존재_bool"].sum()),
        "unmatched_stop_rows": int((stop_summary["정류소위치_결합상태"].astype(str) != "exact_match").sum()),
    }
    return exact, summary


def build_candidate() -> tuple[pd.DataFrame, dict[str, Any]]:
    profile = read_csv(GOLD / "gold_trade_area_profile.csv")
    spatial_index = read_csv(GOLD / "gold_location_spatial_index.csv")
    vertices = read_csv(GOLD / "gold_location_boundary_vertices.csv")
    stop_summary = read_csv(SILVER / "silver_bus_route_node_stop_summary.csv")
    accessibility = read_csv(GOLD / "gold_accessibility_q_area.csv")
    latest_quarter = int(pd.to_numeric(accessibility["기준_년분기_코드"], errors="coerce").max())

    for df in [profile, spatial_index, vertices]:
        df["상권_코드"] = df["상권_코드"].astype(str)
    shapes = build_boundary_shapes(vertices)
    stops, stop_stats = prepare_stops(stop_summary)

    rows: list[dict[str, Any]] = []
    for _, area in spatial_index.sort_values("상권_코드").iterrows():
        code = str(area["상권_코드"])
        area_shapes = shapes.get(code, [])
        if not area_shapes:
            continue
        min_x = float(area["bbox_min_x_epsg5181"]) - 500.0
        max_x = float(area["bbox_max_x_epsg5181"]) + 500.0
        min_y = float(area["bbox_min_y_epsg5181"]) - 500.0
        max_y = float(area["bbox_max_y_epsg5181"]) + 500.0
        candidates = stops[
            (stops["x_epsg5181"] >= min_x)
            & (stops["x_epsg5181"] <= max_x)
            & (stops["y_epsg5181"] >= min_y)
            & (stops["y_epsg5181"] <= max_y)
        ].copy()

        distances: list[float] = []
        for _, stop in candidates.iterrows():
            point = Point(float(stop["x_epsg5181"]), float(stop["y_epsg5181"]))
            distances.append(float(min(shape.distance(point) for shape in area_shapes if not shape.is_empty)))
        if candidates.empty:
            candidates["boundary_distance_m"] = []
        else:
            candidates["boundary_distance_m"] = distances

        within_inside = candidates[candidates["boundary_distance_m"] <= 0.000001]
        within_250 = candidates[candidates["boundary_distance_m"] <= 250.0]
        within_500 = candidates[candidates["boundary_distance_m"] <= 500.0]

        def agg(frame: pd.DataFrame, prefix: str) -> dict[str, Any]:
            return {
                f"{prefix}_정류소수": int(len(frame)),
                f"{prefix}_경유노선수_합계": float(frame["경유_노선_ID수"].sum()) if not frame.empty else 0.0,
                f"{prefix}_경유노선수_최대": float(frame["경유_노선_ID수"].max()) if not frame.empty else 0.0,
                f"{prefix}_경유노선수_평균": float(frame["경유_노선_ID수"].mean()) if not frame.empty else 0.0,
                f"{prefix}_log1p노선수_합계": float(frame["log1p_route_count"].sum()) if not frame.empty else 0.0,
                f"{prefix}_승하차ID존재_정류소수": int(frame["승하차자료_정류소존재_bool"].sum()) if not frame.empty else 0,
            }

        row = {
            "기준_년분기_코드": latest_quarter,
            "상권_코드": code,
            "상권_코드_명": area.get("상권_코드_명"),
            "자치구_코드": area.get("자치구_코드"),
            "자치구_코드_명": area.get("자치구_코드_명"),
            "행정동_코드": area.get("행정동_코드"),
            "행정동_코드_명": area.get("행정동_코드_명"),
            "candidate_version": CANDIDATE_VERSION,
            "source_id": "seoul_bus_route_node_master;seoul_bus_stop_location_file",
            "provider": "서울열린데이터광장",
            "snapshot_date": "2026-07-03",
            "source_grain": "정류소_ID",
            "spatial_rule": "상권 polygon 경계거리 0/250/500m 후보",
            "candidate_use_status": "evidence_only_not_promoted",
            "direct_score_allowed": False,
            "engine_promotion_ready": False,
            "forbidden_claim_ko": "실제 승객 수, 실제 도보시간, 실제 이동시간, 실제 방문확률, 매출 유입으로 표현 금지",
        }
        row.update(agg(within_inside, "inside"))
        row.update(agg(within_250, "radius250m"))
        row.update(agg(within_500, "radius500m"))
        rows.append(row)

    candidate = pd.DataFrame(rows)
    candidate["log1p_radius250m_경유노선수_합계"] = candidate["radius250m_경유노선수_합계"].map(lambda value: math.log1p(max(float(value), 0.0)))
    candidate["log1p_radius500m_경유노선수_합계"] = candidate["radius500m_경유노선수_합계"].map(lambda value: math.log1p(max(float(value), 0.0)))
    candidate["bus_network_diversity_250m_score"] = percentile_score(candidate["log1p_radius250m_경유노선수_합계"])
    candidate["bus_network_diversity_500m_score"] = percentile_score(candidate["log1p_radius500m_경유노선수_합계"])
    candidate["bus_network_diversity_blend_score"] = (
        candidate["bus_network_diversity_250m_score"] * 0.6 + candidate["bus_network_diversity_500m_score"] * 0.4
    ).round(4)
    candidate["algorithm_use_note_ko"] = (
        "버스 정류소별 경유 노선 다양성 후보 신호다. 승객 수나 실제 접근시간이 아니며 공식 접근성 점수를 덮어쓰지 않는다."
    )

    latest_access = accessibility[accessibility["기준_년분기_코드"].astype(int) == latest_quarter][
        ["상권_코드", "버스정류장_수", "교통결절_시설수"]
    ].copy()
    latest_access["상권_코드"] = latest_access["상권_코드"].astype(str)
    compare = candidate.merge(latest_access, on="상권_코드", how="left", validate="one_to_one")
    bus_stop_corr = compare[["radius500m_정류소수", "버스정류장_수"]].corr(method="spearman").iloc[0, 1]
    transit_node_corr = compare[["radius500m_경유노선수_합계", "교통결절_시설수"]].corr(method="spearman").iloc[0, 1]

    summary = {
        "candidate_rows": int(len(candidate)),
        "trade_area_count": int(candidate["상권_코드"].nunique()),
        "latest_accessibility_quarter_for_sanity_compare": latest_quarter,
        "bus_stop_spearman_with_existing_bus_stop_count": None if pd.isna(bus_stop_corr) else float(bus_stop_corr),
        "route_sum_spearman_with_existing_transit_node_count": None if pd.isna(transit_node_corr) else float(transit_node_corr),
        **stop_stats,
    }
    return candidate, summary


def validate_candidate(candidate: pd.DataFrame, summary: dict[str, Any]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    add_check(
        rows,
        "67-V01",
        "상권 전체 universe 보존",
        f"rows={len(candidate)}, unique_trade_area={candidate['상권_코드'].nunique()}",
        "1650 rows / 1650 trade areas",
        len(candidate) == 1650 and candidate["상권_코드"].nunique() == 1650,
        "상권이 누락되면 네트워크 후보가 일부 지역만 설명하는 편향된 보조 신호가 된다.",
    )
    add_check(
        rows,
        "67-V02",
        "좌표 exact_match 정류소만 반경 계산에 사용",
        f"exact={summary['exact_match_stop_rows']}, unmatched_excluded={summary['unmatched_stop_rows']}",
        "exact > 0 and unmatched excluded > 0",
        summary["exact_match_stop_rows"] > 0 and summary["unmatched_stop_rows"] > 0,
        "정류소 위치 마스터와 매칭되지 않는 노선-정류소 row는 좌표 기반 상권 반경 계산에 직접 넣지 않는다.",
    )
    add_check(
        rows,
        "67-V03",
        "반경 집계는 단조 관계를 유지",
        "inside<=250m<=500m violations="
        + str(
            int(
                (
                    (candidate["inside_정류소수"] > candidate["radius250m_정류소수"])
                    | (candidate["radius250m_정류소수"] > candidate["radius500m_정류소수"])
                    | (candidate["inside_경유노선수_합계"] > candidate["radius250m_경유노선수_합계"])
                    | (candidate["radius250m_경유노선수_합계"] > candidate["radius500m_경유노선수_합계"])
                ).sum()
            )
        ),
        "0 violations",
        int(
            (
                (candidate["inside_정류소수"] > candidate["radius250m_정류소수"])
                | (candidate["radius250m_정류소수"] > candidate["radius500m_정류소수"])
                | (candidate["inside_경유노선수_합계"] > candidate["radius250m_경유노선수_합계"])
                | (candidate["radius250m_경유노선수_합계"] > candidate["radius500m_경유노선수_합계"])
            ).sum()
        )
        == 0,
        "250m 후보가 500m 후보보다 커지면 공간 반경 집계가 깨진 것이다.",
    )
    score_min = float(candidate["bus_network_diversity_blend_score"].min())
    score_max = float(candidate["bus_network_diversity_blend_score"].max())
    add_check(
        rows,
        "67-V04",
        "후보 점수 범위는 0~100 내부",
        f"min={score_min:.4f}, max={score_max:.4f}",
        "0 <= score <= 100",
        score_min >= 0 and score_max <= 100,
        "백분위 후보 점수는 비교용 스케일일 뿐 공식 접근성 점수가 아니다.",
    )
    promotion_values = sorted(candidate["engine_promotion_ready"].astype(str).str.lower().unique().tolist())
    direct_values = sorted(candidate["direct_score_allowed"].astype(str).str.lower().unique().tolist())
    add_check(
        rows,
        "67-V05",
        "엔진 직접 승격 금지",
        f"engine_promotion_ready={promotion_values}, direct_score_allowed={direct_values}",
        "all false",
        promotion_values == ["false"] and direct_values == ["false"],
        "현재 후보는 노선 다양성 보조 신호이며 공식 접근성축이나 창업 성공확률로 승격하지 않는다.",
    )
    forbidden_text = " ".join(candidate["forbidden_claim_ko"].dropna().astype(str).unique().tolist())
    add_check(
        rows,
        "67-V06",
        "금지 표현 계약 포함",
        forbidden_text,
        "승객 수/도보시간/이동시간/방문확률/매출 유입 금지",
        all(token in forbidden_text for token in ["승객", "도보시간", "이동시간", "방문확률", "매출"]),
        "노선 수는 승객 수나 매출 유입이 아니므로 AI 리포트에서 과장 표현을 막아야 한다.",
    )
    bus_corr = summary["bus_stop_spearman_with_existing_bus_stop_count"]
    add_check(
        rows,
        "67-V07",
        "기존 접근성 버스정류장 수와 양의 방향 sanity",
        bus_corr,
        "Spearman > 0",
        bus_corr is not None and bus_corr > 0,
        "공식 상권 집객시설의 버스정류장 수와 완전히 반대 방향이면 후보 신호로 쓰기 어렵다. 이 검증은 승격 근거가 아니라 sanity check다.",
    )
    expected_quarter = int(summary["latest_accessibility_quarter_for_sanity_compare"])
    add_check(
        rows,
        "67-V08",
        "최신 스냅샷 후보로만 표기",
        sorted(candidate["기준_년분기_코드"].unique().tolist()),
        f"[{expected_quarter}]",
        sorted(candidate["기준_년분기_코드"].unique().tolist()) == [expected_quarter],
        "2026-07-03 노선마스터 스냅샷을 과거 2021~2025 백테스트 행에 fan-out하면 미래정보 누수가 생긴다.",
    )
    return pd.DataFrame(rows)


def write_report(candidate: pd.DataFrame, validation_df: pd.DataFrame, summary: dict[str, Any]) -> None:
    pass_count = int((validation_df["result"] == "PASS").sum())
    fail_count = int((validation_df["result"] == "FAIL").sum())
    summary_out = {
        "validation_number": VALIDATION_NUMBER,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "candidate_version": CANDIDATE_VERSION,
        **summary,
        "pass_count": pass_count,
        "fail_count": fail_count,
        "decision": "BUS_NETWORK_DIVERSITY_CANDIDATE_PASS_NOT_PROMOTED" if fail_count == 0 else "BUS_NETWORK_DIVERSITY_CANDIDATE_FAIL",
        "next_step": "candidate_evidence_loader_or_accessibility_candidate_backtest_after_historical_network_available",
    }
    SUMMARY_JSON.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_JSON.write_text(json.dumps(summary_out, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(validation_df, VALIDATION_CSV)
    sample_cols = [
        "기준_년분기_코드",
        "상권_코드",
        "상권_코드_명",
        "inside_정류소수",
        "radius250m_정류소수",
        "radius500m_정류소수",
        "radius250m_경유노선수_합계",
        "radius500m_경유노선수_합계",
        "bus_network_diversity_blend_score",
        "candidate_use_status",
        "engine_promotion_ready",
    ]
    write_csv(candidate.sort_values("bus_network_diversity_blend_score", ascending=False).head(100)[sample_cols], SAMPLE_CSV)

    lines = [
        "# 67. 버스 노선-정류장 네트워크 다양성 접근성 후보 검증",
        "",
        f"작성일: {RUN_DATE}",
        "",
        "## 목적",
        "",
        "버스 승하차량과 별개로, 상권 주변 정류소가 얼마나 다양한 버스 노선을 경유하는지 evidence-only 접근성 후보로 정리한다.",
        "",
        "## 근거 자료",
        "",
        "- `research/rule_validation/21_bus_route_node_master_silver_validation_20260704.md`: 노선-정류장 마스터 사용 제한",
        "- `research/rule_validation/07_bus_stop_location_silver_validation_20260703.md`: 정류소 위치 좌표 사용 제한",
        "- `research/rule_validation/44_rule_pipeline_source_coverage_validation_20260707.md`: 교통 원천의 입력/프록시 분류",
        "- `datacorpus/_silver/silver_bus_route_node_stop_summary.csv`",
        "- `datacorpus/_gold/gold_location_boundary_vertices.csv`",
        "- `datacorpus/_gold/gold_location_spatial_index.csv`",
        "",
        "## 핵심 결과",
        "",
        f"- candidate version: `{CANDIDATE_VERSION}`",
        f"- candidate rows: {summary_out['candidate_rows']:,}",
        f"- trade area count: {summary_out['trade_area_count']:,}",
        f"- exact match stop rows used: {summary_out['exact_match_stop_rows']:,}",
        f"- unmatched stop rows excluded: {summary_out['unmatched_stop_rows']:,}",
        f"- existing bus stop count Spearman sanity: {summary_out['bus_stop_spearman_with_existing_bus_stop_count']}",
        f"- PASS: {pass_count}",
        f"- FAIL: {fail_count}",
        f"- decision: `{summary_out['decision']}`",
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
            "## 해석",
            "",
            "- 이 후보는 정류소별 경유 노선 다양성 신호다.",
            "- 승하차량 원천과 노선마스터의 노선 키를 직접 조인하지 않는다.",
            "- 좌표 매칭이 안 된 정류소는 반경 계산에서 제외했다.",
            "- 2026-07-03 스냅샷이므로 과거 분기 백테스트 행에 fan-out하지 않는다.",
            "- 공식 v2.4 접근성 점수나 등급을 바꾸지 않는다.",
            "",
            "## 금지 표현",
            "",
            "- 실제 승객 수",
            "- 실제 도보시간",
            "- 실제 버스 이동시간",
            "- 실제 방문확률",
            "- 매출 유입 또는 창업 성공확률",
            "",
            "## 2보 전진 1보 후퇴",
            "",
            "1. 전진: 노선-정류장 마스터의 경유 노선 다양성 정보를 상권 polygon 주변 후보로 정리했다.",
            "2. 전진: 기존 접근성 버스정류장 수와 방향성 sanity check를 추가했다.",
            "3. 후퇴: 정류소 위치 미매칭 행은 좌표 후보에 투입하지 않았다.",
            "4. 후퇴: 현재 스냅샷을 과거 백테스트 분기에 fan-out하지 않았다.",
            "5. 후퇴: 엔진 공식 점수로 승격하지 않고 `PASS_NOT_PROMOTED`로 남겼다.",
            "",
            "## 산출물",
            "",
            "- `datacorpus/_gold/gold_accessibility_bus_network_diversity_candidate.csv`",
            "- `datacorpus/_rule_validation/67_bus_network_diversity_candidate_validation.csv`",
            "- `datacorpus/_rule_validation/67_bus_network_diversity_candidate_summary.json`",
            "- `datacorpus/_rule_validation/67_bus_network_diversity_candidate_sample_rows.csv`",
            "- `research/rule_validation/67_bus_network_diversity_candidate_20260707.md`",
        ]
    )
    MD_REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    candidate, summary = build_candidate()
    write_csv(candidate, OUTPUT_GOLD)
    validation_df = validate_candidate(candidate, summary)
    write_report(candidate, validation_df, summary)
    print(SUMMARY_JSON.read_text(encoding="utf-8"))
    if int((validation_df["result"] == "FAIL").sum()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
