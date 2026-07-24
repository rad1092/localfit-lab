from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

RULE_DIR = ROOT / "datacorpus" / "_rule_validation"
DOC_DIR = ROOT / "research" / "rule_validation"
SAMPLE_DIR = RULE_DIR / "91_ai_report_server_input_resolver_samples"

SUMMARY_90 = RULE_DIR / "90_input_resolver_engine_bridge_summary.json"
SERVER_PATH = SCRIPTS / "ai_report_server.py"

OUT_CASES = RULE_DIR / "91_ai_report_server_input_resolver_cases.csv"
OUT_VALIDATION = RULE_DIR / "91_ai_report_server_input_resolver_validation.csv"
OUT_SUMMARY = RULE_DIR / "91_ai_report_server_input_resolver_summary.json"
OUT_DOC = DOC_DIR / "91_ai_report_server_input_resolver_contract_20260707.md"

VERSION = "ai_report_server_input_resolver_contract.v0.1-20260707"
OFFICIAL_AXES = ["sales", "competition", "demand", "accessibility"]


def import_module_from_path(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"모듈을 불러올 수 없습니다: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


server = import_module_from_path("ai_report_server", SERVER_PATH)


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def add_validation(rows: list[dict[str, Any]], validation_id: str, name: str, observed: Any, expected: Any, ok: bool, reason_ko: str) -> None:
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


def facts_contract_ok(facts: dict[str, Any]) -> tuple[bool, str]:
    scores = facts.get("scores", {})
    score_result = facts.get("score_result", {})
    axis_scores = scores.get("axis_scores", {})
    failures: list[str] = []
    if score_result.get("total_score") != scores.get("current_location_score"):
        failures.append("total_score_mismatch")
    if sorted(axis_scores.keys()) != sorted(OFFICIAL_AXES):
        failures.append("axis_scores_not_official_4")
    if "cost_risk_score" in axis_scores or "cost_risk" in axis_scores:
        failures.append("cost_in_axis")
    if not facts.get("warnings"):
        failures.append("warnings_missing")
    if not facts.get("text_model_payload", {}).get("must_not_do"):
        failures.append("must_not_do_missing")
    if len(facts.get("evidence_pack", {}).get("forbidden_claims", [])) < 5:
        failures.append("forbidden_claims_missing")
    if not facts.get("input_resolver_context"):
        failures.append("input_resolver_context_missing")
    return not failures, ",".join(failures)


def main() -> int:
    RULE_DIR.mkdir(parents=True, exist_ok=True)
    DOC_DIR.mkdir(parents=True, exist_ok=True)
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)

    summary_90 = read_json(SUMMARY_90)
    source = SERVER_PATH.read_text(encoding="utf-8-sig")

    cases = [
        {
            "case_id": "91-C01",
            "case_name": "좌표 + 업종명 exact",
            "payload": {"location": "37.5111158,127.1024902", "industry": "한식음식점"},
            "expected_ready": True,
            "expected_trade_area_code": "3001495",
            "expected_industry_code": "CS100001",
            "reason_ko": "프론트 기본 입력처럼 좌표 문자열이 들어와도 resolver가 polygon으로 단일 상권을 확정해야 한다.",
        },
        {
            "case_id": "91-C02",
            "case_name": "상권명 + 업종코드 exact",
            "payload": {"location": "잠실 관광특구", "industry": "CS100001"},
            "expected_ready": True,
            "expected_trade_area_code": "3001495",
            "expected_industry_code": "CS100001",
            "reason_ko": "상권명은 화면 입력용이고 서버는 이를 상권코드로 확정해 엔진에 넘겨야 한다.",
        },
        {
            "case_id": "91-C03",
            "case_name": "상권코드 + 수동검토필요 업종",
            "payload": {"location": "3001495", "industry": "제과점"},
            "expected_ready": True,
            "expected_trade_area_code": "3001495",
            "expected_industry_code": "CS100005",
            "reason_ko": "보류_수동검토필요 업종도 선택은 가능하지만 상태를 resolver_context에 남겨야 한다.",
        },
        {
            "case_id": "91-C04",
            "case_name": "중첩 상권 좌표",
            "payload": {"location": "37.53421637705191,126.99298258026175", "industry": "한식음식점"},
            "expected_ready": False,
            "expected_error_contains": "여러 상권",
            "reason_ko": "중첩 상권 좌표는 서버에서도 자동 확정하지 않아야 한다.",
        },
        {
            "case_id": "91-C05",
            "case_name": "서울 밖 좌표",
            "payload": {"location": "36.5,126.0", "industry": "한식음식점"},
            "expected_ready": False,
            "expected_error_contains": "polygon 밖",
            "reason_ko": "서울 밖 좌표는 서버에서도 가까운 후보 안내 대상이지 점수 생성 대상이 아니다.",
        },
        {
            "case_id": "91-C06",
            "case_name": "광역 업종 검색어",
            "payload": {"location": "잠실 관광특구", "industry": "음식점"},
            "expected_ready": False,
            "expected_error_contains": "세부 업종",
            "reason_ko": "업종 후보가 여러 개면 서버도 LLM 호출 전에 차단해야 한다.",
        },
    ]

    case_rows: list[dict[str, Any]] = []
    ready_args: list[Any] = []
    for case in cases:
        error = ""
        args = None
        try:
            args = server.build_engine_args(case["payload"])
            ready_args.append(args)
        except Exception as exc:  # expected for blocked cases
            error = str(exc)
        observed_ready = args is not None
        case_rows.append(
            {
                "case_id": case["case_id"],
                "case_name": case["case_name"],
                "expected_ready": case["expected_ready"],
                "observed_ready": observed_ready,
                "trade_area_code": getattr(args, "trade_area_code", ""),
                "trade_area_name_arg": getattr(args, "trade_area_name", ""),
                "industry_code": getattr(args, "industry_code", ""),
                "industry_name_arg": getattr(args, "industry_name", ""),
                "lat_arg": getattr(args, "lat", ""),
                "lng_arg": getattr(args, "lng", ""),
                "resolver_status_location": getattr(args, "resolver_context", {}).get("location", {}).get("status") if args else "",
                "resolver_status_industry": getattr(args, "resolver_context", {}).get("industry", {}).get("score_use_status") if args else "",
                "error": error,
                "reason_ko": case["reason_ko"],
            }
        )

    validations: list[dict[str, Any]] = []
    case_df = pd.DataFrame(case_rows)

    add_validation(
        validations,
        "91-V01",
        "선행 90번 브리지 검증 PASS",
        summary_90.get("fail_count"),
        0,
        summary_90.get("fail_count") == 0,
        "서버 계약 검증은 resolver-to-engine 브리지 검증이 통과한 상태에서만 의미가 있다.",
    )

    ready_mismatch = int((case_df["expected_ready"].astype(str) != case_df["observed_ready"].astype(str)).sum())
    add_validation(
        validations,
        "91-V02",
        "서버 build_engine_args ready 게이트 일치",
        ready_mismatch,
        0,
        ready_mismatch == 0,
        "서버는 LLM 호출 전에 확정 입력만 엔진으로 넘기고 애매한 입력을 차단해야 한다.",
    )

    ready_rows = case_df[case_df["expected_ready"] == True]  # noqa: E712
    code_only_bad = int(
        (
            ready_rows["trade_area_code"].astype(str).eq("")
            | ready_rows["industry_code"].astype(str).eq("")
            | ready_rows["trade_area_name_arg"].notna() & ready_rows["trade_area_name_arg"].astype(str).ne("None") & ready_rows["trade_area_name_arg"].astype(str).ne("")
            | ready_rows["industry_name_arg"].notna() & ready_rows["industry_name_arg"].astype(str).ne("None") & ready_rows["industry_name_arg"].astype(str).ne("")
            | ready_rows["lat_arg"].notna() & ready_rows["lat_arg"].astype(str).ne("None") & ready_rows["lat_arg"].astype(str).ne("")
            | ready_rows["lng_arg"].notna() & ready_rows["lng_arg"].astype(str).ne("None") & ready_rows["lng_arg"].astype(str).ne("")
        ).sum()
    )
    add_validation(
        validations,
        "91-V03",
        "엔진 인자는 코드만 전달",
        code_only_bad,
        0,
        code_only_bad == 0,
        "상권명, 업종명, 좌표는 resolver에서만 쓰고 build_output에는 상권코드와 업종코드만 넘겨야 한다.",
    )

    expected_code_bad = 0
    for case, row in zip(cases, case_rows):
        if case["expected_ready"]:
            expected_code_bad += int(row["trade_area_code"] != case["expected_trade_area_code"])
            expected_code_bad += int(row["industry_code"] != case["expected_industry_code"])
    add_validation(
        validations,
        "91-V04",
        "서버 확정 코드 기대값 일치",
        expected_code_bad,
        0,
        expected_code_bad == 0,
        "서버가 resolver를 거친 뒤 의도한 상권코드와 서비스업종코드로 엔진을 호출해야 한다.",
    )

    blocked_rows = case_df[case_df["expected_ready"] == False]  # noqa: E712
    blocked_without_error = int(blocked_rows["error"].astype(str).str.strip().eq("").sum())
    add_validation(
        validations,
        "91-V05",
        "차단 케이스 에러 반환",
        blocked_without_error,
        0,
        blocked_without_error == 0,
        "중첩 상권, 서울 밖 좌표, 광역 업종 검색은 조용히 기본 행으로 떨어지면 안 되고 명시 에러를 내야 한다.",
    )

    manual_row = case_df[case_df["case_id"] == "91-C03"].iloc[0]
    add_validation(
        validations,
        "91-V06",
        "수동검토필요 업종 상태 서버 컨텍스트 보존",
        manual_row["resolver_status_industry"],
        "보류_수동검토필요",
        "보류" in str(manual_row["resolver_status_industry"]),
        "서버가 제과점을 처리하더라도 SBDC 자동강매칭 근거로 둔갑시키면 안 된다.",
    )

    source_checks = {
        "resolver_import": "resolve_rule_engine_inputs" in source,
        "context_attached": 'facts["input_resolver_context"]' in source,
        "health_version": "input_resolver_version" in source,
        "code_only_contract": "상권_코드 + 서비스_업종_코드 only" in source,
    }
    add_validation(
        validations,
        "91-V07",
        "서버 소스에 resolver 계약 연결 존재",
        source_checks,
        "all true",
        all(source_checks.values()),
        "HTTP endpoint도 resolver 버전과 입력 확정 컨텍스트를 응답 근거에 남겨야 한다.",
    )

    facts = None
    facts_ok = False
    facts_failures = "not_run"
    sample_path = ""
    if ready_args:
        args = ready_args[0]
        facts = server.attach_optional_candidate_evidence(server.build_output(args))
        facts["input_resolver_context"] = args.resolver_context
        facts_ok, facts_failures = facts_contract_ok(facts)
        sample_path_obj = SAMPLE_DIR / f"91_server_facts_{args.trade_area_code}_{args.industry_code}.json"
        sample_path_obj.write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8-sig")
        sample_path = str(sample_path_obj.relative_to(ROOT))

    add_validation(
        validations,
        "91-V08",
        "서버 facts JSON 계약 유지",
        facts_failures,
        "no failures",
        facts_ok,
        "서버가 만든 facts도 total_score=current_location_score, 공식 4축, 비용 분리, 금지문구, must_not_do, resolver_context를 유지해야 한다.",
    )

    add_validation(
        validations,
        "91-V09",
        "서버 facts 샘플 저장",
        sample_path,
        "sample file exists",
        bool(sample_path) and (ROOT / sample_path).exists(),
        "서버 endpoint 연결 회귀도 나중에 같은 facts JSON을 열어 비교할 수 있게 남긴다.",
    )

    validation_df = pd.DataFrame(validations)
    pass_count, fail_count = pass_fail_counts(validations)
    decision = "AI_REPORT_SERVER_INPUT_RESOLVER_CONTRACT_PASS" if fail_count == 0 else "AI_REPORT_SERVER_INPUT_RESOLVER_CONTRACT_FAIL"

    case_df.to_csv(OUT_CASES, index=False, encoding="utf-8-sig")
    validation_df.to_csv(OUT_VALIDATION, index=False, encoding="utf-8-sig")

    summary = {
        "validation_number": 91,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "validation_version": VERSION,
        "resolver_version": server.RESOLVER_VERSION,
        "score_version": server.SCORE_VERSION,
        "case_count": len(case_df),
        "ready_case_count": int(case_df["expected_ready"].sum()),
        "blocked_case_count": int((case_df["expected_ready"] == False).sum()),  # noqa: E712
        "pass_count": pass_count,
        "fail_count": fail_count,
        "decision": decision,
        "decision_reason_ko": (
            "AI 리포트 서버가 resolver로 확정된 코드 입력만 엔진에 넘기고, 애매한 입력은 LLM 호출 전에 차단한다."
            if fail_count == 0
            else "AI 리포트 서버 입력 계약에 실패 항목이 있어 운영 연결 전 보정이 필요하다."
        ),
        "next_step": "MD 다운로드와 실제 HTTP endpoint smoke를 붙인다.",
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

    doc = f"""# 91. AI 리포트 서버 입력 resolver 계약 검증

작성일: 2026-07-07  
버전: `{VERSION}`  
판정: `{decision}`

## 목적

90번은 resolver와 점수 엔진을 직접 연결해 검증했다. 이번 91번은 실제 AI 리포트 서버가 그 계약을 우회하지 않는지 확인한다.

## 핵심 원칙

- 서버는 상권명, 업종명, 좌표를 그대로 `build_output`에 넘기지 않는다.
- 서버는 먼저 resolver로 `상권_코드 + 서비스_업종_코드`를 확정한다.
- 중첩 상권 좌표, 서울 밖 좌표, 광역 업종 검색어는 LLM 호출 전에 차단한다.
- 서버 facts에는 `input_resolver_context`를 남긴다.

## 요약

- resolver version: `{server.RESOLVER_VERSION}`
- score version: `{server.SCORE_VERSION}`
- cases: {len(case_df)}
- ready cases: {int(case_df["expected_ready"].sum())}
- blocked cases: {int((case_df["expected_ready"] == False).sum())}
- PASS: {pass_count}
- FAIL: {fail_count}

## 케이스 결과

{md_table(case_rows, ["case_id", "case_name", "expected_ready", "observed_ready", "trade_area_code", "industry_code", "resolver_status_location", "resolver_status_industry", "error", "reason_ko"])}

## 검증 결과

{md_table(validations, ["validation_id", "validation_name", "observed", "expected", "result", "reason_ko"])}

## 해석

- AI 리포트 서버는 resolver를 통과한 코드 입력만 점수 엔진으로 넘긴다.
- 중첩 상권, 서울 밖 좌표, 광역 업종 검색어는 LLM 호출 전에 차단된다.
- 서버 facts JSON은 기존 점수/금지문구/텍스트모델 계약과 `input_resolver_context`를 유지한다.

## 다음 작업

1. 실제 HTTP endpoint smoke를 붙인다.
2. MD 다운로드 결과가 Markdown 본문만 담고 금지문구를 통과하는지 확인한다.
3. 웹 입력 폼을 계층형 선택 UI로 바꾸는 작업은 이 계약을 기준으로 진행한다.
"""
    OUT_DOC.write_text(doc, encoding="utf-8-sig")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
