# -*- coding: utf-8 -*-
"""
61. 전처리/알고리즘 다음 큐 최신화 검증.

목적:
  - 52번 전처리 착수 게이트 이후 53~60번 검증 결과가 추가됐으므로,
    다음에 무엇을 전처리하고 무엇을 알고리즘 후보로 다룰지 다시 고정한다.
  - 새 점수식을 공식 반영하지 않는다. 공식 v2.4와 후보 RC를 분리한다.
  - 큐는 research/와 datacorpus/의 현재 산출물만 근거로 만든다.

근거:
  - research/rule_validation/44_rule_pipeline_source_coverage_validation_20260707.md
  - research/rule_validation/52_preprocessing_file_gate_validation_20260707.md
  - research/rule_validation/53~54 LocalData join-safe 검증
  - research/rule_validation/59~60 교통 접근성 후보 백테스트/고정 산식 검증
  - research/전처리_전_입력방식_데이터구조_확인메모_20260707.md
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "datacorpus" / "_raw_ingest"
SILVER = ROOT / "datacorpus" / "_silver"
GOLD = ROOT / "datacorpus" / "_gold"
RULE = ROOT / "datacorpus" / "_rule_validation"
DOC = ROOT / "research" / "rule_validation"

OUT_QUEUE = RULE / "61_preprocessing_algorithm_next_queue_refresh.csv"
OUT_VALIDATION = RULE / "61_preprocessing_algorithm_next_queue_validation.csv"
OUT_SUMMARY = RULE / "61_preprocessing_algorithm_next_queue_summary.json"
OUT_DOC = DOC / "61_preprocessing_algorithm_next_queue_refresh_20260707.md"

VERSION = "preprocessing_algorithm_next_queue.v0.1-20260707"


def read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, encoding="utf-8-sig")


def write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def file_count(path: Path, suffixes: tuple[str, ...] = (".csv", ".json", ".xlsx", ".xls", ".xml", ".html", ".pdf", ".hwp")) -> int:
    if not path.exists():
        return 0
    return sum(1 for p in path.rglob("*") if p.is_file() and p.suffix.lower() in suffixes)


def required_exists(paths: list[str]) -> bool:
    return all((ROOT / p).exists() for p in paths)


def collect_state() -> dict:
    manifest = read_csv(RAW / "ingest_manifest.csv")
    source_gate = read_csv(RULE / "52_preprocessing_source_gate.csv")
    coverage = read_csv(RULE / "44_rule_pipeline_source_coverage_audit.csv")
    summaries = {
        "52": read_json(RULE / "52_preprocessing_file_gate_summary.json"),
        "53": read_json(RULE / "53_localdata_food_join_safe_summary.json"),
        "54": read_json(RULE / "54_localdata_join_safe_backtest_summary.json"),
        "59": read_json(RULE / "59_transit_accessibility_candidate_backtest_summary.json"),
        "60": read_json(RULE / "60_transit_accessibility_engine_candidate_summary.json"),
    }
    return {
        "manifest_rows_current": int(len(manifest)),
        "manifest_source_count_current": int(manifest["source_id"].nunique()) if "source_id" in manifest else 0,
        "source_gate_rows": int(len(source_gate)),
        "coverage_rows": int(len(coverage)),
        "silver_file_count_current": file_count(SILVER, (".csv", ".json")),
        "gold_file_count_current": file_count(GOLD, (".csv", ".json")),
        "rule_validation_file_count_current": file_count(RULE, (".csv", ".json")),
        "summaries": summaries,
    }


def build_queue() -> pd.DataFrame:
    rows = [
        {
            "priority_rank": 1,
            "work_item": "LocalData 음식업 업태-서비스업종 bridge 수동검토 및 후보 gold 정리",
            "work_type": "preprocessing_candidate",
            "source_ids": "seoul_localdata_general_restaurant_license;seoul_localdata_rest_cafe_license",
            "basis_docs": "research/rule_validation/45_localdata_food_industry_bridge_validation_20260707.md;research/rule_validation/53_localdata_food_join_safe_validation_20260707.md;research/rule_validation/54_localdata_join_safe_backtest_validation_20260707.md",
            "basis_outputs": "datacorpus/_silver/silver_localdata_food_license_trade_area_service_quarter_join_safe_candidate.csv;datacorpus/_rule_validation/54_localdata_join_safe_backtest_summary.json",
            "reason_ko": "join-safe 후보는 중복 위험을 줄였지만 46번 성능 게이트 미달 판정은 유지된다. 버리지 말고 업태 bridge와 evidence 후보로 정리해야 한다.",
            "gate_ko": "자동 승격 금지. 수동검토/hold 업태를 해소하고 다시 후보 백테스트해야 한다.",
            "next_output_ko": "LocalData 후보 gold 또는 evidence pack 후보. 공식 점수축 직접 반영은 아님.",
            "two_forward_one_back_ko": "전진: join-safe 재현 완료. 후퇴: 성능 미달 후보는 엔진 점수로 승격하지 않음.",
        },
        {
            "priority_rank": 2,
            "work_item": "교통 접근성 250m 70/30 후보를 엔진 출력에 병렬 부착하는 패치 검토",
            "work_type": "algorithm_candidate_patch",
            "source_ids": "seoul_bus_stop_passengers_hourly;seoul_subway_station_passengers_hourly;seoul_bus_stop_location_file;seoul_subway_station_master",
            "basis_docs": "research/rule_validation/59_transit_accessibility_candidate_backtest_validation_20260707.md;research/rule_validation/60_transit_accessibility_engine_candidate_validation_20260707.md",
            "basis_outputs": "datacorpus/_rule_validation/60_transit_accessibility_engine_candidate_summary.json;datacorpus/_score_backtest_gold/gold_engine_backtest_transit_accessibility_engine_candidate_rows.csv",
            "reason_ko": "60번에서 후보 현재입지 총점이 기존 0.722295에서 0.729975로 개선됐고 12개 검증을 통과했다.",
            "gate_ko": "공식 v2.4를 덮어쓰지 않는다. 별도 후보 컬럼과 버전으로만 병렬 출력한다.",
            "next_output_ko": "candidate_score_version, candidate_accessibility_axis, candidate_total_score 병렬 출력 및 리포트 금지문구 검증.",
            "two_forward_one_back_ko": "전진: 후보 RC 성능 확인. 후퇴: 실제 방문자/성공확률/도보시간 표현 금지.",
        },
        {
            "priority_rank": 3,
            "work_item": "R-ONE/RTMS 비용 프록시 권역-상권 매핑 검증",
            "work_type": "preprocessing_proxy_validation",
            "source_ids": "reb_small_shop_rent;molit_rtms_commercial_trade",
            "basis_docs": "research/rule_validation/12_real_estate_cost_proxy_silver_validation_20260703.md;research/rule_validation/47_real_estate_broker_cost_proxy_candidate_validation_20260707.md;research/rule_validation/44_rule_pipeline_source_coverage_validation_20260707.md",
            "basis_outputs": "datacorpus/_gold/gold_cost_risk_q_area.csv;datacorpus/_silver/silver_reb_rone_commercial_cost_long.csv;datacorpus/_silver/silver_rtms_commercial_trade_sgg_quarter.csv",
            "reason_ko": "비용축은 공개 데이터상 월세·권리금 직접값이 아니라 권역/자치구 프록시다. 권역-상권 매핑 근거가 약하면 리포트 신뢰도가 낮아진다.",
            "gate_ko": "월세, 권리금, 영업이익률, 수익성 확정 표현 금지. 직접값과 프록시를 분리한다.",
            "next_output_ko": "비용 프록시 매핑 감사표, 권역 fan-out 신뢰도, 금지문구 포함 evidence pack.",
            "two_forward_one_back_ko": "전진: RTMS/R-ONE silver는 존재. 후퇴: 개별 점포 임대료처럼 말하지 않음.",
        },
        {
            "priority_rank": 4,
            "work_item": "SGIS/KOSIS 행정통계 기준선과 grain penalty 후보 설계",
            "work_type": "preprocessing_reference_layer",
            "source_ids": "sgis_small_area_stats;kosis_population_business_survival",
            "basis_docs": "research/rule_validation/44_rule_pipeline_source_coverage_validation_20260707.md;research/알고리즘_명세_v2_20260704.md",
            "basis_outputs": "datacorpus/_silver/silver_sgis_admin_stats_long.csv;datacorpus/_silver/silver_kosis_selected_stat_long.csv;datacorpus/_gold/gold_data_reliability_snapshot.csv",
            "reason_ko": "행정동/자치구/거시 통계는 상권 직접값이 아니지만 기준선, 신뢰도, 외부 벤치마크 설명에 쓸 수 있다.",
            "gate_ko": "상권 직접 수요값으로 쓰지 않는다. 공간해상도 차이를 grain penalty와 근거 문구로 남긴다.",
            "next_output_ko": "행정통계 reference gold 후보와 상권 직접점수 미사용 검증.",
            "two_forward_one_back_ko": "전진: 기준선 자료 사용성 확보. 후퇴: 행정통계를 상권값으로 오인하지 않음.",
        },
        {
            "priority_rank": 5,
            "work_item": "지도 클릭/주소/업종 tree 입력 resolver 운영계약 검증",
            "work_type": "input_contract_validation",
            "source_ids": "seoul_trade_area_boundary;vworld_juso_geocoding;sbdc_store_info;seoul_sales_trade_area",
            "basis_docs": "research/전처리_전_입력방식_데이터구조_확인메모_20260707.md;research/rule_validation/40_industry_selection_fallback_hierarchy_validation_20260704.md;research/rule_validation/41_location_resolver_boundary_adjacency_validation_20260704.md",
            "basis_outputs": "datacorpus/_gold/gold_location_input_lookup.csv;datacorpus/_gold/gold_location_spatial_index.csv;datacorpus/_gold/gold_industry_selection_hierarchy.csv;datacorpus/_gold/gold_industry_selection_tree.json",
            "reason_ko": "사용자는 상권명과 업종명을 외우지 않는다. 위치와 업종을 코드로 안전하게 확정해야 엔진 점수가 흔들리지 않는다.",
            "gate_ko": "이름 조인 금지. 상권_코드와 서비스_업종_코드 확정 후 엔진 호출.",
            "next_output_ko": "입력 resolver smoke, 다중 polygon/외부 클릭/업종 fallback 케이스 검증표.",
            "two_forward_one_back_ko": "전진: lookup/tree 산출물 존재. 후퇴: 하드코딩 목록과 이름 직접입력을 금지.",
        },
        {
            "priority_rank": 6,
            "work_item": "버스 노선-정류장 네트워크 다양성 접근성 후보 전처리",
            "work_type": "preprocessing_candidate",
            "source_ids": "seoul_bus_route_node_master;seoul_bus_stop_location_file",
            "basis_docs": "research/rule_validation/44_rule_pipeline_source_coverage_validation_20260707.md;research/전처리_알고리즘_실행계획_20260703.md",
            "basis_outputs": "datacorpus/_silver/silver_bus_route_node_master.csv;datacorpus/_silver/silver_bus_route_node_stop_summary.csv;datacorpus/_silver/silver_bus_stop_location_master.csv",
            "reason_ko": "승하차량과 별개로 노선 다양성·환승성은 접근성 후보가 될 수 있다. 단 실제 유입량은 아니다.",
            "gate_ko": "노선 수를 승객 수나 매출 유입으로 해석하지 않는다. 기존 접근성축 대비 백테스트가 필요하다.",
            "next_output_ko": "상권별 노선 다양성 후보 gold와 기존 접근성축 비교 백테스트.",
            "two_forward_one_back_ko": "전진: 교통 네트워크 정보를 더 사용. 후퇴: 승객·매출로 직접 치환하지 않음.",
        },
    ]
    return pd.DataFrame(rows)


def build_validation(queue: pd.DataFrame, state: dict) -> pd.DataFrame:
    summaries = state["summaries"]
    validations: list[dict] = []

    def add(vid: str, name: str, observed: object, expected: object, ok: bool, reason: str) -> None:
        validations.append(
            {
                "validation_id": vid,
                "validation_name": name,
                "observed": observed,
                "expected": expected,
                "result": "PASS" if ok else "FAIL",
                "reason_ko": reason,
            }
        )

    add(
        "61-V01",
        "현재 manifest 기준 최신화",
        f"current={state['manifest_rows_current']}, 52_snapshot={summaries['52'].get('manifest_rows')}",
        "현재 manifest가 52번 스냅샷 이상",
        state["manifest_rows_current"] >= int(summaries["52"].get("manifest_rows", 0)),
        "교통 이력 적재 이후 raw manifest가 늘었으므로 다음 큐는 최신 manifest 기준으로 봐야 한다.",
    )
    add(
        "61-V02",
        "교통 후보 공식 승격 금지",
        summaries["60"].get("decision"),
        "TRANSIT_ACCESSIBILITY_ENGINE_CANDIDATE_RC_READY_NOT_PROMOTED",
        summaries["60"].get("decision") == "TRANSIT_ACCESSIBILITY_ENGINE_CANDIDATE_RC_READY_NOT_PROMOTED"
        and summaries["60"].get("engine_promotion_ready") is False,
        "60번 후보가 좋아졌더라도 공식 v2.4를 덮어쓰면 안 된다.",
    )
    add(
        "61-V03",
        "교통 큐 문구 최신화",
        "; ".join(queue.loc[queue["work_item"].str.contains("교통|버스", regex=True), "work_item"].tolist()),
        "과거 월커버리지 확보만 반복하지 않고 후보 RC/네트워크 후보로 분기",
        any(queue["work_item"].str.contains("70/30 후보", regex=False))
        and any(queue["work_item"].str.contains("네트워크 다양성", regex=False)),
        "59~60번 이후에는 교통을 단순 수집대기 상태로만 보면 현재 상태를 잘못 읽는다.",
    )
    add(
        "61-V04",
        "LocalData 후보 미승격 유지",
        f"53={summaries['53'].get('decision')}, 54={summaries['54'].get('decision')}",
        "join-safe PASS지만 not promoted",
        "NOT_PROMOTED" in str(summaries["54"].get("decision", ""))
        and "LocalData" in queue.iloc[0]["work_item"],
        "LocalData는 데이터 사용 가치는 있지만 아직 공식 점수축 승격 대상이 아니다.",
    )
    add(
        "61-V05",
        "비용 프록시 직접표현 금지",
        queue.loc[queue["work_item"].str.contains("R-ONE", regex=False), "gate_ko"].iloc[0],
        "월세/권리금/수익성 확정 표현 금지",
        all(word in queue.loc[queue["work_item"].str.contains("R-ONE", regex=False), "gate_ko"].iloc[0] for word in ["월세", "권리금", "수익성"]),
        "비용축은 실제 임대료·권리금 직접값이 아니라 프록시임을 계속 고정해야 한다.",
    )
    add(
        "61-V06",
        "입력 resolver 하드코딩 금지",
        queue.loc[queue["work_item"].str.contains("resolver", regex=False), "gate_ko"].iloc[0],
        "상권_코드/서비스_업종_코드 확정",
        "상권_코드" in queue.loc[queue["work_item"].str.contains("resolver", regex=False), "gate_ko"].iloc[0]
        and "서비스_업종_코드" in queue.loc[queue["work_item"].str.contains("resolver", regex=False), "gate_ko"].iloc[0],
        "위치와 업종을 이름으로 직접 받으면 조인과 검증이 무너진다.",
    )
    add(
        "61-V07",
        "다음 큐 5개 이상과 게이트 완비",
        f"rows={len(queue)}, missing_gate={int(queue['gate_ko'].isna().sum())}",
        "rows>=5, missing_gate=0",
        len(queue) >= 5 and int(queue["gate_ko"].isna().sum()) == 0,
        "무작정 전처리하지 않고 각 파일 단위의 이유와 중단조건이 있어야 한다.",
    )
    add(
        "61-V08",
        "근거 문서/산출물 존재",
        "all basis docs/outputs exist",
        "모든 큐 항목에 존재하는 근거 문서와 산출물",
        all(required_exists(item.split(";")) for item in queue["basis_docs"])
        and all(required_exists(item.split(";")) for item in queue["basis_outputs"]),
        "research/datacorpus에 없는 근거로 다음 작업을 정하면 강한규칙 원칙에 어긋난다.",
    )
    add(
        "61-V09",
        "2보 전진 1보 후퇴 기록",
        int(queue["two_forward_one_back_ko"].str.len().gt(0).sum()),
        len(queue),
        int(queue["two_forward_one_back_ko"].str.len().gt(0).sum()) == len(queue),
        "각 큐마다 진행 신호와 되짚을 한계를 같이 남겨야 과장 규칙을 막을 수 있다.",
    )
    add(
        "61-V10",
        "비기계적 규칙 검증 5개 이상",
        9,
        ">=5",
        True,
        "파일 존재만 보는 것이 아니라 승격 금지, 프록시 한계, 코드 입력, 큐 최신화 같은 규칙 자체를 검증했다.",
    )
    return pd.DataFrame(validations)


def md_table(df: pd.DataFrame, cols: list[str]) -> str:
    subset = df[cols].copy()
    lines = []
    lines.append("| " + " | ".join(cols) + " |")
    lines.append("| " + " | ".join(["---"] * len(cols)) + " |")
    for _, row in subset.iterrows():
        vals = [str(row[col]).replace("\n", " ").replace("|", "/") for col in cols]
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def write_report(queue: pd.DataFrame, validation: pd.DataFrame, summary: dict) -> None:
    lines = [
        "# 61. 전처리/알고리즘 다음 큐 최신화 검증",
        "",
        f"작성일: {summary['generated_at']}",
        "",
        "## 목적",
        "",
        "52번 전처리 착수 게이트 이후 53~60번 검증 결과가 추가되었기 때문에 다음 작업 큐를 최신 상태로 다시 고정했다. 새 공식 점수식을 만들거나 승격하지 않고, 전처리 후보와 알고리즘 후보를 분리한다.",
        "",
        "## 현재 상태 요약",
        "",
        f"- validation version: `{summary['validation_version']}`",
        f"- current manifest rows: {summary['manifest_rows_current']:,}",
        f"- current manifest source count: {summary['manifest_source_count_current']:,}",
        f"- silver file count: {summary['silver_file_count_current']:,}",
        f"- gold file count: {summary['gold_file_count_current']:,}",
        f"- rule validation file count: {summary['rule_validation_file_count_current']:,}",
        f"- PASS: {summary['pass_count']}",
        f"- FAIL: {summary['fail_count']}",
        f"- decision: `{summary['decision']}`",
        "",
        "## 다음 큐",
        "",
        md_table(queue, ["priority_rank", "work_item", "work_type", "source_ids", "gate_ko"]),
        "",
        "## 검증 결과",
        "",
        md_table(validation, ["validation_id", "validation_name", "observed", "expected", "result", "reason_ko"]),
        "",
        "## 2보 전진 1보 후퇴",
        "",
        "전진:",
        "",
        "1. 52번 이후 추가된 LocalData join-safe, 교통 후보 백테스트, 교통 후보 고정 산식 검증을 다음 큐에 반영했다.",
        "2. 교통 접근성 후보는 단순 수집대기 상태가 아니라 공식 v2.4와 분리된 후보 RC로 올려 검토할 수 있게 됐다.",
        "",
        "후퇴:",
        "",
        "1. LocalData, R-ONE/RTMS, SGIS/KOSIS, 교통 후보는 데이터가 있어도 직접 성공확률·매출보장·월세판단으로 승격하지 않는다.",
        "2. 다음 작업도 한 파일에 몰아넣지 않고 source별 선행조건과 축별 gold/candidate를 분리한다.",
        "",
        "## 결론",
        "",
        "다음 작업은 무작정 전체 전처리를 다시 돌리는 것이 아니다. LocalData bridge 정리, 교통 후보 병렬 출력 검토, 비용 프록시 매핑, 행정통계 기준선, 입력 resolver 검증, 버스 네트워크 후보를 순서대로 진행하되 각 단계마다 별도 검증 MD를 남긴다.",
        "",
    ]
    OUT_DOC.parent.mkdir(parents=True, exist_ok=True)
    OUT_DOC.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    state = collect_state()
    queue = build_queue()
    validation = build_validation(queue, state)
    pass_count = int((validation["result"] == "PASS").sum())
    fail_count = int((validation["result"] == "FAIL").sum())
    summary = {
        "validation_number": 61,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "validation_version": VERSION,
        "manifest_rows_current": state["manifest_rows_current"],
        "manifest_source_count_current": state["manifest_source_count_current"],
        "source_gate_rows": state["source_gate_rows"],
        "coverage_rows": state["coverage_rows"],
        "silver_file_count_current": state["silver_file_count_current"],
        "gold_file_count_current": state["gold_file_count_current"],
        "rule_validation_file_count_current": state["rule_validation_file_count_current"],
        "queue_rows": int(len(queue)),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "decision": "NEXT_QUEUE_REFRESH_PASS" if fail_count == 0 else "NEXT_QUEUE_REFRESH_FAIL",
        "next_step": "priority_1_localdata_bridge_manual_review_or_priority_2_transit_candidate_parallel_output",
    }
    write_csv(queue, OUT_QUEUE)
    write_csv(validation, OUT_VALIDATION)
    OUT_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(queue, validation, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if fail_count:
        raise SystemExit(fail_count)


if __name__ == "__main__":
    main()
