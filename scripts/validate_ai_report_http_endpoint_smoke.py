from __future__ import annotations

import importlib.util
import json
import sys
import threading
import urllib.error
import urllib.request
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

RULE_DIR = ROOT / "datacorpus" / "_rule_validation"
DOC_DIR = ROOT / "research" / "rule_validation"
SAMPLE_DIR = RULE_DIR / "93_ai_report_http_endpoint_samples"

SUMMARY_91 = RULE_DIR / "91_ai_report_server_input_resolver_summary.json"
SERVER_PATH = SCRIPTS / "ai_report_server.py"

OUT_CASES = RULE_DIR / "93_ai_report_http_endpoint_smoke_cases.csv"
OUT_VALIDATION = RULE_DIR / "93_ai_report_http_endpoint_smoke_validation.csv"
OUT_SUMMARY = RULE_DIR / "93_ai_report_http_endpoint_smoke_summary.json"
OUT_DOC = DOC_DIR / "93_ai_report_http_endpoint_smoke_20260707.md"

VERSION = "ai_report_http_endpoint_smoke.v0.1-20260707"
OFFICIAL_AXES = ["sales", "competition", "demand", "accessibility"]


def import_module_from_path(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"모듈을 불러올 수 없습니다: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


server_module = import_module_from_path("ai_report_server", SERVER_PATH)


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


def request_json(base_url: str, path: str, payload: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        base_url + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method="GET" if payload is None else "POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as res:
            return res.status, json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            parsed = {"ok": False, "error": body}
        return exc.code, parsed


def facts_contract_failures(data: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    facts = data.get("facts", {})
    scores = facts.get("scores", {})
    score_result = facts.get("score_result", {})
    axis_scores = scores.get("axis_scores", {})
    if not data.get("ok"):
        failures.append("ok_false")
    if data.get("model") != "dry-run-local":
        failures.append("not_dry_run")
    if score_result.get("total_score") != scores.get("current_location_score"):
        failures.append("total_score_mismatch")
    if sorted(axis_scores.keys()) != sorted(OFFICIAL_AXES):
        failures.append("axis_scores_not_official_4")
    if "cost_risk" in axis_scores or "cost_risk_score" in axis_scores:
        failures.append("cost_in_axis_scores")
    if not facts.get("input_resolver_context"):
        failures.append("input_resolver_context_missing")
    if not facts.get("text_model_payload", {}).get("must_not_do"):
        failures.append("must_not_do_missing")
    if len(facts.get("evidence_pack", {}).get("forbidden_claims", [])) < 5:
        failures.append("forbidden_claims_missing")
    try:
        server_module.validate_markdown_contract(data.get("markdown", ""), facts=facts)
    except Exception as exc:
        failures.append(f"markdown_contract_fail:{exc}")
    return failures


def main() -> int:
    RULE_DIR.mkdir(parents=True, exist_ok=True)
    DOC_DIR.mkdir(parents=True, exist_ok=True)
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)

    summary_91 = read_json(SUMMARY_91)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server_module.AiReportHandler)
    port = int(httpd.server_address[1])
    base_url = f"http://127.0.0.1:{port}"
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    case_rows: list[dict[str, Any]] = []
    validations: list[dict[str, Any]] = []

    try:
        health_status, health = request_json(base_url, "/api/ai-report/health")
        add_validation(
            validations,
            "93-V01",
            "health endpoint exposes versions",
            f"status={health_status}, score={health.get('score_version')}, resolver={health.get('input_resolver_version')}",
            "200 with score/resolver/report contract versions",
            health_status == 200
            and health.get("ok") is True
            and health.get("score_version") == server_module.SCORE_VERSION
            and health.get("input_resolver_version") == server_module.RESOLVER_VERSION,
            "HTTP smoke는 실제 서버 health에서 score_version과 input_resolver_version을 확인해야 한다.",
        )

        cases = [
            {
                "case_id": "93-C01",
                "case_name": "좌표 + 업종명 dry-run 성공",
                "payload": {"location": "37.5111158,127.1024902", "industry": "한식음식점", "dry_run": True},
                "expected_status": 200,
                "expected_ok": True,
                "expected_error_contains": "",
                "reason_ko": "실제 HTTP POST에서도 resolver가 좌표를 상권코드로 확정하고 dry-run Markdown을 반환해야 한다.",
            },
            {
                "case_id": "93-C02",
                "case_name": "상권명 + 업종코드 dry-run 성공",
                "payload": {"location": "잠실 관광특구", "industry": "CS100001", "dry_run": True},
                "expected_status": 200,
                "expected_ok": True,
                "expected_error_contains": "",
                "reason_ko": "상권명과 업종코드 입력도 HTTP 경로에서 코드 입력으로 확정되어야 한다.",
            },
            {
                "case_id": "93-C03",
                "case_name": "중첩 상권 좌표 차단",
                "payload": {"location": "37.53421637705191,126.99298258026175", "industry": "한식음식점", "dry_run": True},
                "expected_status": 500,
                "expected_ok": False,
                "expected_error_contains": "여러 상권",
                "reason_ko": "중첩 상권 좌표는 HTTP endpoint에서도 LLM 호출 전에 차단되어야 한다.",
            },
            {
                "case_id": "93-C04",
                "case_name": "서울 밖 좌표 차단",
                "payload": {"location": "36.5,126.0", "industry": "한식음식점", "dry_run": True},
                "expected_status": 500,
                "expected_ok": False,
                "expected_error_contains": "polygon 밖",
                "reason_ko": "서울 밖 좌표는 HTTP endpoint에서도 점수 생성 대상이 아니다.",
            },
            {
                "case_id": "93-C05",
                "case_name": "광역 업종 검색어 차단",
                "payload": {"location": "잠실 관광특구", "industry": "음식점", "dry_run": True},
                "expected_status": 500,
                "expected_ok": False,
                "expected_error_contains": "세부 업종",
                "reason_ko": "광역 업종 검색어는 HTTP endpoint에서도 후보 선택 전까지 차단되어야 한다.",
            },
        ]

        for case in cases:
            status, data = request_json(base_url, "/api/ai-report", case["payload"])
            sample_path = SAMPLE_DIR / f"{case['case_id']}_response.json"
            sample_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8-sig")
            contract_failures = facts_contract_failures(data) if status == 200 and data.get("ok") else []
            ok = (
                status == case["expected_status"]
                and data.get("ok") == case["expected_ok"]
                and (not case["expected_error_contains"] or case["expected_error_contains"] in str(data.get("error", "")))
                and not contract_failures
            )
            case_rows.append(
                {
                    "case_id": case["case_id"],
                    "case_name": case["case_name"],
                    "status": status,
                    "ok": data.get("ok"),
                    "model": data.get("model", ""),
                    "llm_status": data.get("llm_status", ""),
                    "target_trade_area_code": data.get("facts", {}).get("matched_target", {}).get("trade_area_code", ""),
                    "target_industry_code": data.get("facts", {}).get("matched_target", {}).get("industry_code", ""),
                    "error": data.get("error", ""),
                    "contract_failures": ",".join(contract_failures),
                    "sample_response_path": str(sample_path.relative_to(ROOT)),
                    "result": "PASS" if ok else "FAIL",
                    "reason_ko": case["reason_ko"],
                }
            )

        case_fail_count = sum(1 for row in case_rows if row["result"] != "PASS")
        add_validation(
            validations,
            "93-V02",
            "HTTP report smoke cases pass",
            f"cases={len(case_rows)}, fail={case_fail_count}",
            "fail=0",
            case_fail_count == 0,
            "성공 입력은 dry-run Markdown/facts를 반환하고, 애매한 입력은 명시 에러로 차단해야 한다.",
        )

        success_rows = [row for row in case_rows if row["status"] == 200]
        success_contract_bad = sum(1 for row in success_rows if row["contract_failures"])
        add_validation(
            validations,
            "93-V03",
            "HTTP success responses keep facts contract",
            success_contract_bad,
            0,
            success_contract_bad == 0 and len(success_rows) >= 2,
            "HTTP 성공 응답도 total_score=current_location_score, 공식 4축, 비용 분리, 금지문구, resolver_context를 유지해야 한다.",
        )

        blocked_rows = [row for row in case_rows if row["status"] != 200]
        add_validation(
            validations,
            "93-V04",
            "HTTP blocked responses do not include facts",
            [row["case_id"] for row in blocked_rows if row["target_trade_area_code"] or row["target_industry_code"]],
            "[]",
            all(not row["target_trade_area_code"] and not row["target_industry_code"] for row in blocked_rows),
            "차단 응답이 기본 facts를 함께 반환하면 사용자가 잘못된 리포트를 정상 결과로 오해할 수 있다.",
        )

        add_validation(
            validations,
            "93-V05",
            "선행 91번 서버 입력 계약 PASS",
            summary_91.get("fail_count"),
            0,
            summary_91.get("fail_count") == 0,
            "HTTP smoke는 서버 입력 resolver 계약이 통과한 뒤 그 계약을 실제 요청 경로에서 다시 보는 검증이다.",
        )
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=10)

    case_df = pd.DataFrame(case_rows)
    validation_df = pd.DataFrame(validations)
    pass_count, fail_count = pass_fail_counts(validations)
    decision = "AI_REPORT_HTTP_ENDPOINT_SMOKE_PASS" if fail_count == 0 else "AI_REPORT_HTTP_ENDPOINT_SMOKE_FAIL"

    case_df.to_csv(OUT_CASES, index=False, encoding="utf-8-sig")
    validation_df.to_csv(OUT_VALIDATION, index=False, encoding="utf-8-sig")
    summary = {
        "validation_number": 93,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "validation_version": VERSION,
        "base_url": base_url,
        "case_count": len(case_df),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "decision": decision,
        "decision_reason_ko": (
            "실제 HTTP 경로에서도 dry-run 성공 입력은 facts/Markdown 계약을 유지하고 애매한 입력은 차단된다."
            if fail_count == 0
            else "HTTP endpoint smoke에서 실패 항목이 있어 운영 연결 전 보정이 필요하다."
        ),
        "next_step": "MD 다운로드 계약 검증을 실행한다.",
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

    doc = f"""# 93. AI 리포트 HTTP endpoint smoke

작성일: 2026-07-07  
버전: `{VERSION}`  
판정: `{decision}`

## 목적

91번은 서버 내부 함수 계약을 검증했다. 이번 93번은 실제 로컬 HTTP 서버를 띄워 `/api/ai-report/health`와 `/api/ai-report` 요청/응답 경로에서도 같은 계약이 유지되는지 확인한다.  
OpenAI 네트워크 호출은 하지 않고 `dry_run` 경로로 facts와 Markdown 계약만 검증한다.

## 요약

- case count: {len(case_df)}
- PASS: {pass_count}
- FAIL: {fail_count}
- base url: `{base_url}`

## 케이스 결과

{md_table(case_rows, ["case_id", "case_name", "status", "ok", "model", "llm_status", "target_trade_area_code", "target_industry_code", "error", "contract_failures", "result", "reason_ko"])}

## 검증 결과

{md_table(validations, ["validation_id", "validation_name", "observed", "expected", "result", "reason_ko"])}

## 해석

- HTTP 성공 응답은 `dry-run-local` 모델 상태로 반환되며 OpenAI를 호출하지 않는다.
- 성공 응답의 facts는 `total_score=current_location_score`, 공식 4축, 비용 분리, 금지문구, `input_resolver_context`를 유지한다.
- 중첩 상권 좌표, 서울 밖 좌표, 광역 업종 검색어는 HTTP 경로에서도 차단된다.

## 다음 작업

1. MD 다운로드 계약 검증을 실행한다.
2. 브라우저 UI는 HTTP smoke 통과 계약을 기준으로 계층형 선택으로 바꾼다.
"""
    OUT_DOC.write_text(doc, encoding="utf-8-sig")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
