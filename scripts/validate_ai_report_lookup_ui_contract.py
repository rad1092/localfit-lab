from __future__ import annotations

import importlib.util
import json
import sys
import threading
import urllib.error
import urllib.parse
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

SERVER_PATH = SCRIPTS / "ai_report_server.py"
JS_AI_REPORT = ROOT / "js" / "aiReport.js"
CSS_COMPONENTS = ROOT / "css" / "components.css"
SUMMARY_93 = RULE_DIR / "93_ai_report_http_endpoint_smoke_summary.json"
SUMMARY_94 = RULE_DIR / "94_ai_report_markdown_download_summary.json"

OUT_CASES = RULE_DIR / "96_ai_report_lookup_ui_contract_cases.csv"
OUT_VALIDATION = RULE_DIR / "96_ai_report_lookup_ui_contract_validation.csv"
OUT_SUMMARY = RULE_DIR / "96_ai_report_lookup_ui_contract_summary.json"
OUT_DOC = DOC_DIR / "96_ai_report_lookup_ui_contract_20260707.md"

VERSION = "ai_report_lookup_ui_contract.v0.1-20260707"


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
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


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


def find_service_path(tree: dict[str, Any], industry_code: str) -> dict[str, Any] | None:
    for large in tree.get("large_categories", []):
        for medium in large.get("medium_categories", []):
            for small in medium.get("small_categories", []):
                for service in small.get("service_industries", []):
                    if service.get("service_industry_code") == industry_code:
                        return {
                            "large": large.get("name"),
                            "medium": medium.get("name"),
                            "small": small.get("name"),
                            "service": service.get("service_industry_name"),
                            "service_code": service.get("service_industry_code"),
                        }
    return None


def main() -> int:
    RULE_DIR.mkdir(parents=True, exist_ok=True)
    DOC_DIR.mkdir(parents=True, exist_ok=True)

    summary_93 = read_json(SUMMARY_93)
    summary_94 = read_json(SUMMARY_94)
    js_source = JS_AI_REPORT.read_text(encoding="utf-8-sig")
    css_source = CSS_COMPONENTS.read_text(encoding="utf-8-sig")

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server_module.AiReportHandler)
    port = int(httpd.server_address[1])
    base_url = f"http://127.0.0.1:{port}"
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    case_rows: list[dict[str, Any]] = []
    validation_rows: list[dict[str, Any]] = []

    try:
        health_status, health = request_json(base_url, "/api/ai-report/health")
        industries_status, industries = request_json(base_url, "/api/ai-report/lookups/industries")
        path_ko_food = find_service_path(industries.get("tree", {}), "CS100001")
        path_bakery = find_service_path(industries.get("tree", {}), "CS100005")

        location_query = urllib.parse.quote("이태원")
        loc_status, loc_data = request_json(base_url, f"/api/ai-report/lookups/locations?q={location_query}&limit=20")
        location_candidates = loc_data.get("candidates", [])
        itaewon = next((row for row in location_candidates if str(row.get("trade_area_code")) == "3001491"), None)

        resolve_status, resolve_data = request_json(
            base_url,
            "/api/ai-report/resolve",
            {
                "trade_area_code": "3001491",
                "industry_code": "CS100001",
                "budget": "80000000",
            },
        )
        resolve_candidate_status, resolve_candidate_data = request_json(
            base_url,
            "/api/ai-report/resolve",
            {
                "location": itaewon.get("display_label") if itaewon else "이태원 관광특구",
                "trade_area_code": itaewon.get("trade_area_code") if itaewon else "3001491",
                "industry": path_ko_food.get("service") if path_ko_food else "한식음식점",
                "industry_code": "CS100001",
                "budget": "80000000",
            },
        )
    finally:
        httpd.shutdown()
        thread.join(timeout=10)

    case_rows.extend(
        [
            {
                "case_id": "96-C01",
                "case_name": "health lookup contract version",
                "status": health_status,
                "observed": health.get("lookup_contract_version"),
                "expected": server_module.LOOKUP_CONTRACT_VERSION,
                "result": "PASS" if health_status == 200 and health.get("lookup_contract_version") == server_module.LOOKUP_CONTRACT_VERSION else "FAIL",
                "reason_ko": "프론트는 서버 lookup 계약 버전을 확인할 수 있어야 한다.",
            },
            {
                "case_id": "96-C02",
                "case_name": "industry tree contains direct service path",
                "status": industries_status,
                "observed": path_ko_food,
                "expected": "CS100001 path exists",
                "result": "PASS" if industries_status == 200 and path_ko_food and path_ko_food.get("service_code") == "CS100001" else "FAIL",
                "reason_ko": "업종 UI는 대분류-중분류-세부분류-서울 서비스업종 경로로 최종 업종코드를 확정해야 한다.",
            },
            {
                "case_id": "96-C03",
                "case_name": "industry fallback/manual path still selectable",
                "status": industries_status,
                "observed": path_bakery,
                "expected": "CS100005 path exists",
                "result": "PASS" if path_bakery and path_bakery.get("service_code") == "CS100005" else "FAIL",
                "reason_ko": "수동검토 또는 fallback 업종도 화면 탐색 후보로는 보여야 하며, 최종 조인은 서비스업종코드로 한다.",
            },
            {
                "case_id": "96-C04",
                "case_name": "location lookup returns Itaewon candidate",
                "status": loc_status,
                "observed": {
                    "match_count": loc_data.get("match_count"),
                    "returned_count": loc_data.get("returned_count"),
                    "itaewon": itaewon,
                },
                "expected": "candidate 3001491 returned",
                "result": "PASS" if loc_status == 200 and itaewon and str(itaewon.get("trade_area_code")) == "3001491" else "FAIL",
                "reason_ko": "사용자는 상권코드를 외우지 않고 검색 후보에서 상권을 선택할 수 있어야 한다.",
            },
            {
                "case_id": "96-C05",
                "case_name": "resolve endpoint accepts selected codes",
                "status": resolve_status,
                "observed": resolve_data.get("engine_input"),
                "expected": {"trade_area_code": "3001491", "industry_code": "CS100001"},
                "result": "PASS"
                if resolve_status == 200
                and resolve_data.get("engine_input", {}).get("trade_area_code") == "3001491"
                and resolve_data.get("engine_input", {}).get("industry_code") == "CS100001"
                else "FAIL",
                "reason_ko": "선택 UI의 최종 출력은 LLM이 아니라 엔진이 읽을 상권_코드와 서비스_업종_코드여야 한다.",
            },
            {
                "case_id": "96-C06",
                "case_name": "resolve endpoint accepts UI candidate payload",
                "status": resolve_candidate_status,
                "observed": resolve_candidate_data.get("input_resolver_context"),
                "expected": "single code confirmed",
                "result": "PASS"
                if resolve_candidate_status == 200
                and resolve_candidate_data.get("engine_input", {}).get("trade_area_code") == "3001491"
                and resolve_candidate_data.get("engine_input", {}).get("industry_code") == "CS100001"
                else "FAIL",
                "reason_ko": "화면의 표시명은 사람이 읽는 값이고, 서버는 hidden code를 우선해 확정해야 한다.",
            },
        ]
    )

    js_checks = {
        "industry_lookup_endpoint": "/api/ai-report/lookups/industries" in js_source,
        "location_lookup_endpoint": "/api/ai-report/lookups/locations" in js_source,
        "large_select": "aiReportIndustryLarge" in js_source,
        "medium_select": "aiReportIndustryMedium" in js_source,
        "small_select": "aiReportIndustrySmall" in js_source,
        "service_select": "aiReportIndustryService" in js_source,
        "trade_area_hidden": "aiReportTradeAreaCode" in js_source and "trade_area_code" in js_source,
        "industry_hidden": "aiReportIndustryCode" in js_source and "industry_code" in js_source,
        "candidate_select_action": "select-ai-report-location" in js_source,
        "no_location_guard": "위치를 검색해 후보를 선택해야 합니다." in js_source,
        "no_industry_guard": "서울 서비스업종까지 선택해야 합니다." in js_source,
    }
    css_checks = {
        "location_row_css": ".ai-report-location-row" in css_source,
        "candidate_css": ".ai-report-location-candidate" in css_source,
        "industry_grid_css": ".ai-report-industry-grid" in css_source,
        "mobile_css": "@media (max-width: 560px)" in css_source,
    }

    add_validation(
        validation_rows,
        "96-V01",
        "previous HTTP/Markdown contracts passed",
        {"93_fail": summary_93.get("fail_count"), "94_fail": summary_94.get("fail_count")},
        {"93_fail": 0, "94_fail": 0},
        summary_93.get("fail_count") == 0 and summary_94.get("fail_count") == 0,
        "입력 UI를 붙여도 기존 HTTP 리포트와 Markdown 다운로드 계약 위에서 검증해야 한다.",
    )
    case_fail_count = sum(1 for row in case_rows if row["result"] != "PASS")
    add_validation(
        validation_rows,
        "96-V02",
        "lookup and resolve endpoint cases",
        f"cases={len(case_rows)}, fail={case_fail_count}",
        "fail=0",
        case_fail_count == 0,
        "서버 lookup/resolve 경로가 코드 확정 계약을 지키는지 실제 HTTP로 확인해야 한다.",
    )
    add_validation(
        validation_rows,
        "96-V03",
        "frontend code selection contract",
        js_checks,
        "all true",
        all(js_checks.values()),
        "프론트는 자유입력 문자열이 아니라 선택된 상권코드와 업종코드를 payload에 포함해야 한다.",
    )
    add_validation(
        validation_rows,
        "96-V04",
        "frontend layout contract",
        css_checks,
        "all true",
        all(css_checks.values()),
        "후보 목록과 계층형 업종 선택은 모달 안에서 모바일/데스크톱 모두 레이아웃이 깨지지 않아야 한다.",
    )

    pass_count, fail_count = pass_fail_counts(validation_rows)
    decision = "AI_REPORT_LOOKUP_UI_CONTRACT_PASS" if fail_count == 0 else "AI_REPORT_LOOKUP_UI_CONTRACT_FAIL"

    pd.DataFrame(case_rows).to_csv(OUT_CASES, index=False, encoding="utf-8-sig")
    pd.DataFrame(validation_rows).to_csv(OUT_VALIDATION, index=False, encoding="utf-8-sig")

    summary = {
        "validation_version": VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "decision": decision,
        "pass_count": pass_count,
        "fail_count": fail_count,
        "case_count": len(case_rows),
        "case_fail_count": case_fail_count,
        "lookup_contract_version": server_module.LOOKUP_CONTRACT_VERSION,
        "resolver_version": server_module.RESOLVER_VERSION,
        "outputs": {
            "cases": str(OUT_CASES.relative_to(ROOT)),
            "validation": str(OUT_VALIDATION.relative_to(ROOT)),
            "summary": str(OUT_SUMMARY.relative_to(ROOT)),
            "doc": str(OUT_DOC.relative_to(ROOT)),
        },
        "reason_ko": "AI 리포트 입력부는 검색/계층 선택으로 코드를 확정하고, 엔진에는 상권_코드와 서비스_업종_코드만 넘기는 계약을 통과했다."
        if fail_count == 0
        else "lookup/resolve/UI 계약 중 실패 항목이 있어 운영 입력부 보완이 필요하다.",
    }
    OUT_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8-sig")

    doc = f"""# 96. AI 리포트 lookup/UI 입력 계약 검증

## 목적

위치와 업종을 사람이 외우는 자유입력으로 두면 알고리즘 입력이 흔들린다.  
이번 검증은 화면에서 후보를 고르고, 서버가 최종적으로 `상권_코드 + 서비스_업종_코드`만 엔진에 넘기는지 확인한다.

## 근거

- `datacorpus/_gold/gold_location_input_lookup.csv`: 상권 검색/표시 후보
- `datacorpus/_gold/gold_industry_selection_tree.json`: 업종 계층 선택 후보
- `research/rule_validation/90_input_resolver_engine_bridge_20260707.md`: resolver가 확정한 코드만 엔진으로 넘기는 계약
- `research/rule_validation/93_ai_report_http_endpoint_smoke_20260707.md`: HTTP 리포트 생성 smoke
- `research/rule_validation/94_ai_report_markdown_download_contract_20260707.md`: Markdown 다운로드 계약

## 검증 결과

- validation version: `{VERSION}`
- decision: `{decision}`
- PASS: `{pass_count}`
- FAIL: `{fail_count}`
- lookup contract: `{server_module.LOOKUP_CONTRACT_VERSION}`
- resolver: `{server_module.RESOLVER_VERSION}`

## Endpoint 사례

{md_table(case_rows, ["case_id", "case_name", "status", "observed", "expected", "result", "reason_ko"])}

## 검증 항목

{md_table(validation_rows, ["validation_id", "validation_name", "observed", "expected", "result", "reason_ko"])}

## 판단

{summary["reason_ko"]}

## 산출물

- `{OUT_CASES.relative_to(ROOT)}`
- `{OUT_VALIDATION.relative_to(ROOT)}`
- `{OUT_SUMMARY.relative_to(ROOT)}`
"""
    OUT_DOC.write_text(doc, encoding="utf-8-sig")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
