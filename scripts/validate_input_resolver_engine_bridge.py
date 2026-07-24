from __future__ import annotations

import importlib.util
import json
import math
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

RULE_DIR = ROOT / "datacorpus" / "_rule_validation"
DOC_DIR = ROOT / "research" / "rule_validation"
SAMPLE_DIR = RULE_DIR / "90_input_resolver_engine_bridge_samples"

SUMMARY_66 = RULE_DIR / "66_input_resolver_operational_contract_summary.json"
SUMMARY_88 = RULE_DIR / "88_v1_backdata_contract_revalidation_summary.json"

OUT_CASES = RULE_DIR / "90_input_resolver_engine_bridge_cases.csv"
OUT_VALIDATION = RULE_DIR / "90_input_resolver_engine_bridge_validation.csv"
OUT_SUMMARY = RULE_DIR / "90_input_resolver_engine_bridge_summary.json"
OUT_DOC = DOC_DIR / "90_input_resolver_engine_bridge_20260707.md"

VERSION = "input_resolver_engine_bridge.v0.1-20260707"
OFFICIAL_AXES = ["sales", "competition", "demand", "accessibility"]


def import_module_from_path(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"모듈을 불러올 수 없습니다: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


resolver = import_module_from_path("resolve_rule_engine_inputs", SCRIPTS / "resolve_rule_engine_inputs.py")
engine = import_module_from_path("build_rule_based_location_scores", SCRIPTS / "build_rule_based_location_scores.py")


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def add_validation(
    rows: list[dict[str, Any]],
    validation_id: str,
    name: str,
    observed: Any,
    expected: Any,
    ok: bool,
    reason_ko: str,
) -> None:
    rows.append(
        {
            "validation_id": validation_id,
            "validation_name": name,
            "observed": observed,
            "expected": expected,
            "result": "PASS" if ok else "FAIL",
            "reason_ko": reason_ko,
        }
    )


def md_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join(["---"] * len(columns)) + " |",
    ]
    for row in rows:
        values = [str(row.get(col, "")).replace("\n", " ") for col in columns]
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def pass_fail_counts(rows: list[dict[str, Any]]) -> tuple[int, int]:
    pass_count = sum(1 for row in rows if row["result"] == "PASS")
    fail_count = sum(1 for row in rows if row["result"] == "FAIL")
    return pass_count, fail_count


def has_non_finite_number(value: Any) -> bool:
    if isinstance(value, float):
        return not math.isfinite(value)
    if isinstance(value, dict):
        return any(has_non_finite_number(v) for v in value.values())
    if isinstance(value, list):
        return any(has_non_finite_number(v) for v in value)
    return False


def single_location_code(location_resolution: dict[str, Any]) -> str | None:
    if (
        location_resolution.get("location_resolution_status") == "single_inside_confirmed"
        and location_resolution.get("inside_polygon_count") == 1
        and location_resolution.get("resolved_trade_areas")
    ):
        return str(location_resolution["resolved_trade_areas"][0]["trade_area_code"])
    return None


def single_industry_code(industry_resolution: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    matches = industry_resolution.get("matches", [])
    if industry_resolution.get("match_count") == 1 and matches:
        match = matches[0]
        if bool(match.get("direct_score_allowed")):
            return str(match.get("service_industry_code")), match
    return None, matches[0] if matches else None


def json_contract_ok(payload: dict[str, Any]) -> tuple[bool, str]:
    scores = payload.get("scores", {})
    score_result = payload.get("score_result", {})
    axis_scores = scores.get("axis_scores", {})
    failures: list[str] = []
    if score_result.get("total_score") != scores.get("current_location_score"):
        failures.append("total_score_mismatch")
    if sorted(axis_scores.keys()) != sorted(OFFICIAL_AXES):
        failures.append("axis_scores_not_official_4")
    if "cost_risk" in axis_scores or "cost_risk_score" in axis_scores:
        failures.append("cost_in_axis_scores")
    if not payload.get("warnings"):
        failures.append("warnings_missing")
    if len(payload.get("evidence_pack", {}).get("forbidden_claims", [])) < 5:
        failures.append("forbidden_claims_missing")
    if not payload.get("text_model_payload", {}).get("must_not_do"):
        failures.append("must_not_do_missing")
    if has_non_finite_number(payload):
        failures.append("non_finite_number")
    return not failures, ",".join(failures)


def build_engine_context() -> tuple[int, pd.DataFrame, pd.DataFrame]:
    quarter = engine.latest_quarter()
    base = engine.percentile_scores(engine.build_indicator_frame(quarter))
    scored = engine.score_frame(base)
    return quarter, base, scored


def build_engine_payload(base: pd.DataFrame, scored: pd.DataFrame, quarter: int, trade_area_code: str, industry_code: str) -> dict[str, Any]:
    args = SimpleNamespace(
        quarter=quarter,
        trade_area_code=str(trade_area_code),
        trade_area_name=None,
        industry_code=str(industry_code),
        industry_name=None,
    )
    return engine.build_result(base, scored, args, quarter)


def get_location_row(data: Any, trade_area_code: str) -> pd.Series:
    rows = data.locations[data.locations["상권_코드"].astype(str) == str(trade_area_code)]
    if rows.empty:
        raise RuntimeError(f"상권 코드를 찾을 수 없습니다: {trade_area_code}")
    return rows.iloc[0]


def main() -> int:
    RULE_DIR.mkdir(parents=True, exist_ok=True)
    DOC_DIR.mkdir(parents=True, exist_ok=True)
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)

    summary_66 = read_json(SUMMARY_66)
    summary_88 = read_json(SUMMARY_88)

    data = resolver.load_resolver_data()
    single_lon = 127.1024902
    single_lat = 37.5111158
    multiple_row = get_location_row(data, "3001491")
    multiple_lon = float(multiple_row["representative_lon_wgs84"])
    multiple_lat = float(multiple_row["representative_lat_wgs84"])

    cases = [
        {
            "case_id": "90-C01",
            "case_name": "단일 상권 좌표 + 업종명 exact",
            "lon": single_lon,
            "lat": single_lat,
            "industry_query": "한식음식점",
            "expected_engine_ready": True,
            "expected_location_status": "single_inside_confirmed",
            "expected_industry_match_count": 1,
            "reason_ko": "사용자가 지도에서 단일 상권 안을 찍고 업종명이 1개로 확정되면 엔진 입력으로 넘길 수 있다.",
        },
        {
            "case_id": "90-C02",
            "case_name": "단일 상권 좌표 + 업종 코드 exact",
            "lon": single_lon,
            "lat": single_lat,
            "industry_query": "CS100001",
            "expected_engine_ready": True,
            "expected_location_status": "single_inside_confirmed",
            "expected_industry_match_count": 1,
            "reason_ko": "업종 코드가 오면 이름 검색 없이 서비스_업종_코드로 확정되어야 한다.",
        },
        {
            "case_id": "90-C03",
            "case_name": "수동검토필요 업종 exact",
            "lon": single_lon,
            "lat": single_lat,
            "industry_query": "제과점",
            "expected_engine_ready": True,
            "expected_location_status": "single_inside_confirmed",
            "expected_industry_match_count": 1,
            "reason_ko": "선택 가능한 업종은 엔진 입력으로 넘기되, SBDC 자동강매칭으로 둔갑시키지 않고 보류 상태를 보존한다.",
        },
        {
            "case_id": "90-C04",
            "case_name": "중첩 상권 좌표",
            "lon": multiple_lon,
            "lat": multiple_lat,
            "industry_query": "한식음식점",
            "expected_engine_ready": False,
            "expected_location_status": "multiple_inside_candidates",
            "expected_industry_match_count": 1,
            "reason_ko": "여러 상권 polygon에 동시에 포함되면 사용자 선택이나 별도 우선순위 규칙 전에는 자동 확정하면 안 된다.",
        },
        {
            "case_id": "90-C05",
            "case_name": "서울 밖 좌표",
            "lon": 126.0,
            "lat": 36.5,
            "industry_query": "한식음식점",
            "expected_engine_ready": False,
            "expected_location_status": "outside_nearest_candidates",
            "expected_industry_match_count": 1,
            "reason_ko": "서울 상권 polygon 밖 좌표는 가까운 후보만 보여주고 엔진으로 바로 넘기지 않는다.",
        },
        {
            "case_id": "90-C06",
            "case_name": "업종 광역 검색어",
            "lon": single_lon,
            "lat": single_lat,
            "industry_query": "음식점",
            "expected_engine_ready": False,
            "expected_location_status": "single_inside_confirmed",
            "expected_industry_match_count": 6,
            "reason_ko": "업종 후보가 여러 개면 사용자가 세부 업종을 선택하기 전까지 엔진 호출을 막아야 한다.",
        },
    ]

    quarter = None
    base = None
    scored = None
    case_rows: list[dict[str, Any]] = []
    ready_payloads: list[dict[str, Any]] = []
    code_name_pair: dict[str, str] = {}

    for case in cases:
        loc = resolver.resolve_location(float(case["lon"]), float(case["lat"]), data, nearest_limit=5)
        ind = resolver.resolve_industry(str(case["industry_query"]), data)
        trade_area_code = single_location_code(loc)
        industry_code, industry_match = single_industry_code(ind)
        observed_ready = trade_area_code is not None and industry_code is not None
        should_call_engine = observed_ready
        payload: dict[str, Any] | None = None
        json_ok = None
        json_failures = ""
        sample_path = ""

        if should_call_engine:
            if quarter is None or base is None or scored is None:
                quarter, base, scored = build_engine_context()
            payload = build_engine_payload(base, scored, int(quarter), trade_area_code, industry_code)
            json_ok, json_failures = json_contract_ok(payload)
            sample_path_obj = SAMPLE_DIR / f"{case['case_id']}_{trade_area_code}_{industry_code}_{quarter}.json"
            sample_path_obj.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8-sig")
            sample_path = str(sample_path_obj.relative_to(ROOT))
            ready_payloads.append(payload)

        if str(case["industry_query"]) in {"한식음식점", "CS100001"} and industry_code:
            code_name_pair[str(case["industry_query"])] = industry_code

        case_rows.append(
            {
                "case_id": case["case_id"],
                "case_name": case["case_name"],
                "lon": case["lon"],
                "lat": case["lat"],
                "industry_query": case["industry_query"],
                "location_status": loc.get("location_resolution_status"),
                "inside_polygon_count": loc.get("inside_polygon_count"),
                "nearest_candidate_count": len(loc.get("nearest_candidates", [])),
                "boundary_candidate_count": len(loc.get("nearby_boundary_candidates", [])),
                "industry_match_count": ind.get("match_count"),
                "industry_score_use_status": industry_match.get("score_use_status") if industry_match else "",
                "industry_match_type": industry_match.get("match_type") if industry_match else "",
                "expected_engine_ready": case["expected_engine_ready"],
                "observed_engine_ready": observed_ready,
                "engine_called": should_call_engine,
                "engine_trade_area_code": trade_area_code or "",
                "engine_industry_code": industry_code or "",
                "payload_target_trade_area_code": payload.get("matched_target", {}).get("trade_area_code") if payload else "",
                "payload_target_industry_code": payload.get("matched_target", {}).get("industry_code") if payload else "",
                "payload_total_score": payload.get("score_result", {}).get("total_score") if payload else "",
                "payload_current_location_score": payload.get("scores", {}).get("current_location_score") if payload else "",
                "payload_axis_keys": ";".join(sorted(payload.get("scores", {}).get("axis_scores", {}).keys())) if payload else "",
                "payload_json_contract_ok": json_ok if json_ok is not None else "",
                "payload_json_contract_failures": json_failures,
                "sample_json_path": sample_path,
                "reason_ko": case["reason_ko"],
            }
        )

    validations: list[dict[str, Any]] = []
    case_df = pd.DataFrame(case_rows)

    add_validation(
        validations,
        "90-V01",
        "선행 입력/백데이터 계약 PASS",
        f"66_fail={summary_66.get('fail_count')}, 88_fail={summary_88.get('fail_count')}",
        "66=0, 88=0",
        summary_66.get("fail_count") == 0 and summary_88.get("fail_count") == 0,
        "운영 연결 검증은 입력 resolver 자체 계약과 백데이터 계약이 통과한 상태에서만 의미가 있다.",
    )
    add_validation(
        validations,
        "90-V02",
        "운영 smoke case 6건 실행",
        len(case_df),
        6,
        len(case_df) == 6,
        "단일 확정, 코드 exact, 수동검토 업종, 중첩 상권, 서울 밖 좌표, 광역 업종 검색을 모두 봐야 한다.",
    )

    ready_mismatch = int((case_df["expected_engine_ready"].astype(str) != case_df["observed_engine_ready"].astype(str)).sum())
    add_validation(
        validations,
        "90-V03",
        "engine_ready 게이트 기대값 일치",
        ready_mismatch,
        0,
        ready_mismatch == 0,
        "resolver가 확정하지 못한 입력은 점수 엔진으로 넘어가면 안 되고, 확정 입력은 코드로만 넘어가야 한다.",
    )

    blocked_called = int(((case_df["expected_engine_ready"] == False) & (case_df["engine_called"] == True)).sum())  # noqa: E712
    add_validation(
        validations,
        "90-V04",
        "차단 케이스 엔진 미호출",
        blocked_called,
        0,
        blocked_called == 0,
        "중첩 상권, 서울 밖 좌표, 광역 업종 검색은 바로 리포트를 만들지 않고 선택/확정 단계로 보내야 한다.",
    )

    ready_rows = case_df[case_df["expected_engine_ready"] == True]  # noqa: E712
    ready_called = int((ready_rows["engine_called"] == True).sum())  # noqa: E712
    add_validation(
        validations,
        "90-V05",
        "확정 케이스 엔진 호출",
        ready_called,
        len(ready_rows),
        ready_called == len(ready_rows),
        "단일 상권과 단일 업종이 확정된 경우에는 엔진 입력으로 넘겨 실제 JSON이 나와야 한다.",
    )

    code_mismatch = int(
        (
            (ready_rows["engine_trade_area_code"].astype(str) != ready_rows["payload_target_trade_area_code"].astype(str))
            | (ready_rows["engine_industry_code"].astype(str) != ready_rows["payload_target_industry_code"].astype(str))
        ).sum()
    )
    add_validation(
        validations,
        "90-V06",
        "resolver 코드와 payload 대상 코드 일치",
        code_mismatch,
        0,
        code_mismatch == 0,
        "화면 표시명은 사람이 읽는 값이고, 엔진과 JSON 대상은 resolver가 확정한 코드와 일치해야 한다.",
    )

    json_contract_fail = int((ready_rows["payload_json_contract_ok"].astype(str) != "True").sum())
    add_validation(
        validations,
        "90-V07",
        "확정 케이스 JSON 계약 유지",
        json_contract_fail,
        0,
        json_contract_fail == 0,
        "입력 연결 뒤에도 total_score=current_location_score, 공식 4축, 비용 분리, 경고/금지문구/텍스트모델 계약이 유지되어야 한다.",
    )

    status_mismatch = int((case_df["location_status"].astype(str) != [case["expected_location_status"] for case in cases]).sum())
    add_validation(
        validations,
        "90-V08",
        "위치 상태 기대값 일치",
        status_mismatch,
        0,
        status_mismatch == 0,
        "좌표 입력은 단일 포함, 중첩 포함, 서울 밖 후보 상태를 구분해야 한다.",
    )

    broad = case_df[case_df["case_id"] == "90-C06"].iloc[0]
    add_validation(
        validations,
        "90-V09",
        "광역 업종 검색어 차단",
        f"matches={broad['industry_match_count']}, engine_called={broad['engine_called']}",
        "matches>1, engine_called=False",
        int(broad["industry_match_count"]) > 1 and not bool(broad["engine_called"]),
        "음식점처럼 넓은 검색어는 후보 목록만 보여주고 세부 업종 선택 전까지 엔진 호출을 막아야 한다.",
    )

    manual = case_df[case_df["case_id"] == "90-C03"].iloc[0]
    add_validation(
        validations,
        "90-V10",
        "수동검토필요 업종 상태 보존",
        f"{manual['engine_industry_code']} / {manual['industry_score_use_status']} / called={manual['engine_called']}",
        "CS100005 / 보류_수동검토필요 / called=True",
        manual["engine_industry_code"] == "CS100005"
        and "보류" in str(manual["industry_score_use_status"])
        and bool(manual["engine_called"]),
        "제과점은 선택 가능하지만 SBDC 자동강매칭 근거로 둔갑시키지 않고 보류/수동검토 상태를 남겨야 한다.",
    )

    outside = case_df[case_df["case_id"] == "90-C05"].iloc[0]
    add_validation(
        validations,
        "90-V11",
        "서울 밖 좌표는 후보만 반환",
        f"inside={outside['inside_polygon_count']}, nearest={outside['nearest_candidate_count']}, boundary={outside['boundary_candidate_count']}, called={outside['engine_called']}",
        "inside=0, nearest>0, boundary>0, called=False",
        int(outside["inside_polygon_count"]) == 0
        and int(outside["nearest_candidate_count"]) > 0
        and int(outside["boundary_candidate_count"]) > 0
        and not bool(outside["engine_called"]),
        "서울 밖 좌표를 임의 상권으로 확정하면 잘못된 리포트가 생성되므로 가까운 후보만 반환해야 한다.",
    )

    code_name_ok = code_name_pair.get("한식음식점") == "CS100001" and code_name_pair.get("CS100001") == "CS100001"
    add_validation(
        validations,
        "90-V12",
        "업종명 exact와 업종코드 exact 동일 코드",
        code_name_pair,
        "{'한식음식점': 'CS100001', 'CS100001': 'CS100001'}",
        code_name_ok,
        "UI가 이름으로 보여도 최종 조인은 항상 같은 서비스_업종_코드로 이루어져야 한다.",
    )

    sample_paths = [path for path in ready_rows["sample_json_path"].dropna().astype(str).tolist() if path]
    sample_files_ok = all((ROOT / path).exists() for path in sample_paths)
    add_validation(
        validations,
        "90-V13",
        "확정 케이스 JSON 샘플 저장",
        sample_paths,
        "ready case JSON files exist",
        sample_files_ok and len(sample_paths) == len(ready_rows),
        "운영 연결 회귀는 나중에 같은 JSON을 열어 비교할 수 있게 샘플 파일로 남겨야 한다.",
    )

    score_versions = sorted({payload.get("score_version") for payload in ready_payloads})
    add_validation(
        validations,
        "90-V14",
        "엔진 score_version 단일성",
        score_versions,
        [engine.SCORE_VERSION],
        score_versions == [engine.SCORE_VERSION],
        "입력 연결 smoke에서 서로 다른 점수 버전이 섞이면 같은 알고리즘 계약으로 볼 수 없다.",
    )

    validation_df = pd.DataFrame(validations)
    pass_count, fail_count = pass_fail_counts(validations)
    decision = "INPUT_RESOLVER_ENGINE_BRIDGE_PASS" if fail_count == 0 else "INPUT_RESOLVER_ENGINE_BRIDGE_FAIL"

    case_df.to_csv(OUT_CASES, index=False, encoding="utf-8-sig")
    validation_df.to_csv(OUT_VALIDATION, index=False, encoding="utf-8-sig")

    summary = {
        "validation_number": 90,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "validation_version": VERSION,
        "resolver_version": resolver.RESOLVER_VERSION,
        "score_version": engine.SCORE_VERSION,
        "quarter": quarter,
        "case_count": len(case_df),
        "ready_case_count": int(case_df["expected_engine_ready"].sum()),
        "blocked_case_count": int((case_df["expected_engine_ready"] == False).sum()),  # noqa: E712
        "pass_count": pass_count,
        "fail_count": fail_count,
        "decision": decision,
        "decision_reason_ko": (
            "입력 resolver가 단일 확정 입력만 엔진으로 넘기고, 중첩 상권·서울 밖 좌표·광역 업종 검색은 차단한다."
            if fail_count == 0
            else "입력 resolver와 엔진 연결에서 실패 항목이 있어 운영 연결 전 보정이 필요하다."
        ),
        "next_step": "AI 리포트 서버 endpoint와 MD 다운로드 smoke에서 같은 payload 계약을 확인한다.",
        "outputs": [
            str(OUT_CASES.relative_to(ROOT)),
            str(OUT_VALIDATION.relative_to(ROOT)),
            str(OUT_SUMMARY.relative_to(ROOT)),
            str(OUT_DOC.relative_to(ROOT)),
            str(SAMPLE_DIR.relative_to(ROOT)),
        ],
    }

    with OUT_SUMMARY.open("w", encoding="utf-8-sig") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    doc = f"""# 90. 입력 resolver - 점수 엔진 운영 연결 검증

작성일: 2026-07-07  
버전: `{VERSION}`  
판정: `{decision}`

## 목적

66번은 입력 resolver 자체가 상권/업종 코드를 안전하게 확정할 수 있는지 봤고, 88번은 전처리·엔진·JSON·백데이터 계약이 서로 충돌하지 않는지 봤다.  
이번 90번은 그 다음 단계로, resolver가 확정한 입력만 실제 점수 엔진 JSON으로 넘어가고 애매한 입력은 차단되는지 확인한다.

## 핵심 원칙

- 엔진 입력은 `상권_코드 + 서비스_업종_코드`만 사용한다.
- 지도 클릭 좌표가 여러 상권에 포함되면 자동 확정하지 않는다.
- 서울 상권 polygon 밖 좌표는 가까운 후보만 보여주고 엔진을 호출하지 않는다.
- 업종 검색어가 여러 후보를 반환하면 사용자가 세부 업종을 고르기 전까지 엔진을 호출하지 않는다.
- 수동검토필요 업종은 선택 가능하더라도 그 상태를 보존해야 한다.

## 요약

- resolver version: `{resolver.RESOLVER_VERSION}`
- score version: `{engine.SCORE_VERSION}`
- 기준 분기: `{quarter}`
- smoke cases: {len(case_df)}
- ready cases: {int(case_df["expected_engine_ready"].sum())}
- blocked cases: {int((case_df["expected_engine_ready"] == False).sum())}
- PASS: {pass_count}
- FAIL: {fail_count}

## 케이스 결과

{md_table(case_rows, ["case_id", "case_name", "location_status", "inside_polygon_count", "industry_match_count", "expected_engine_ready", "observed_engine_ready", "engine_called", "engine_trade_area_code", "engine_industry_code", "payload_total_score", "payload_json_contract_ok", "reason_ko"])}

## 검증 결과

{md_table(validations, ["validation_id", "validation_name", "observed", "expected", "result", "reason_ko"])}

## 해석

- 단일 상권 좌표와 단일 업종이 확정된 3건은 실제 엔진 JSON까지 생성됐다.
- 중첩 상권 좌표, 서울 밖 좌표, 광역 업종 검색어는 엔진 호출이 차단됐다.
- 엔진 JSON은 기존 계약처럼 `total_score=current_location_score`, 공식 4축, 비용 리스크 별도, 금지문구/텍스트 모델 제한을 유지했다.
- 이 검증은 점수 정확도를 새로 주장하는 작업이 아니라, 잘못된 입력이 점수 엔진으로 들어가는 것을 막는 운영 연결 검증이다.

## 다음 작업

1. `scripts/ai_report_server.py` endpoint에서 같은 입력 확정 계약을 사용하게 연결한다.
2. 웹 화면의 지도 클릭/주소 검색/업종 계층 선택이 90번과 같은 차단 조건을 따르는지 smoke를 붙인다.
3. MD 다운로드 리포트에도 확정된 코드와 표시명을 함께 남긴다.
"""
    OUT_DOC.write_text(doc, encoding="utf-8-sig")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
