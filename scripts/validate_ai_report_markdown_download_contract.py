from __future__ import annotations

import importlib.util
import json
import re
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
SAMPLE_DIR = RULE_DIR / "94_ai_report_markdown_download_samples"

SUMMARY_93 = RULE_DIR / "93_ai_report_http_endpoint_smoke_summary.json"
HTTP_SAMPLE_DIR = RULE_DIR / "93_ai_report_http_endpoint_samples"
JS_AI_REPORT = ROOT / "js" / "aiReport.js"
SERVER_PATH = SCRIPTS / "ai_report_server.py"

OUT_FILES = RULE_DIR / "94_ai_report_markdown_download_files.csv"
OUT_VALIDATION = RULE_DIR / "94_ai_report_markdown_download_validation.csv"
OUT_SUMMARY = RULE_DIR / "94_ai_report_markdown_download_summary.json"
OUT_DOC = DOC_DIR / "94_ai_report_markdown_download_contract_20260707.md"

VERSION = "ai_report_markdown_download_contract.v0.1-20260707"


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


def safe_filename_part(value: Any, fallback: str) -> str:
    text = str(value or fallback)
    text = re.sub(r'[\\/:*?"<>|]+', "_", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or fallback


def markdown_shape_failures(markdown: str) -> list[str]:
    failures: list[str] = []
    stripped = markdown.strip()
    if not stripped:
        failures.append("empty")
    if not stripped.startswith("# "):
        failures.append("missing_h1")
    if "```" in markdown:
        failures.append("code_fence")
    if '"schema_version"' in markdown or '"score_result"' in markdown or '"facts"' in markdown:
        failures.append("json_raw_text")
    if stripped.startswith("{") or stripped.endswith("}"):
        failures.append("looks_like_json")
    return failures


def main() -> int:
    RULE_DIR.mkdir(parents=True, exist_ok=True)
    DOC_DIR.mkdir(parents=True, exist_ok=True)
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)

    summary_93 = read_json(SUMMARY_93)
    responses = []
    for path in sorted(HTTP_SAMPLE_DIR.glob("*_response.json")):
        data = read_json(path)
        if data.get("ok") is True and data.get("markdown"):
            responses.append((path, data))

    file_rows: list[dict[str, Any]] = []
    validation_rows: list[dict[str, Any]] = []

    for source_path, data in responses:
        facts = data.get("facts", {})
        target = facts.get("matched_target", {})
        filename = "_".join(
            [
                "서울상권_AI상세리포트",
                safe_filename_part(target.get("trade_area_name") or target.get("trade_area_code"), "location"),
                safe_filename_part(target.get("industry_name") or target.get("industry_code"), "industry"),
            ]
        ) + ".md"
        out_path = SAMPLE_DIR / filename
        markdown = str(data.get("markdown", ""))
        out_path.write_text(markdown, encoding="utf-8-sig")

        contract_error = ""
        try:
            server.validate_markdown_contract(markdown, facts=facts)
        except Exception as exc:
            contract_error = str(exc)

        shape_failures = markdown_shape_failures(markdown)
        decoded_ok = out_path.read_text(encoding="utf-8-sig") == markdown
        file_rows.append(
            {
                "source_response": str(source_path.relative_to(ROOT)),
                "markdown_file": str(out_path.relative_to(ROOT)),
                "filename": filename,
                "bytes": out_path.stat().st_size,
                "line_count": markdown.count("\n") + 1,
                "contract_error": contract_error,
                "shape_failures": ",".join(shape_failures),
                "decoded_ok": decoded_ok,
                "result": "PASS" if not contract_error and not shape_failures and decoded_ok else "FAIL",
                "reason_ko": "HTTP dry-run 응답 Markdown을 MD 다운로드 파일로 저장했을 때 본문 계약이 유지되어야 한다.",
            }
        )

    js_source = JS_AI_REPORT.read_text(encoding="utf-8-sig")
    unsafe_rejected = False
    unsafe_error = ""
    try:
        server.validate_markdown_contract("# 상권 입지 상세 리포트\n\n창업 성공확률이 높다는 추천 문구")
    except Exception as exc:
        unsafe_rejected = True
        unsafe_error = str(exc)

    file_fail_count = sum(1 for row in file_rows if row["result"] != "PASS")
    add_validation(
        validation_rows,
        "94-V01",
        "선행 93번 HTTP smoke PASS",
        summary_93.get("fail_count"),
        0,
        summary_93.get("fail_count") == 0,
        "MD 다운로드 계약은 HTTP dry-run 응답이 통과한 뒤 그 Markdown 본문을 대상으로 검증한다.",
    )
    add_validation(
        validation_rows,
        "94-V02",
        "성공 HTTP 응답 Markdown 2건 이상 확보",
        len(responses),
        ">=2",
        len(responses) >= 2,
        "서로 다른 입력 경로에서 나온 Markdown을 저장해 다운로드 계약을 봐야 한다.",
    )
    add_validation(
        validation_rows,
        "94-V03",
        "MD 파일 저장 계약 PASS",
        f"files={len(file_rows)}, fail={file_fail_count}",
        "fail=0",
        file_fail_count == 0 and len(file_rows) >= 2,
        "다운로드되는 MD는 Markdown 본문만 포함하고 JSON 원문, 코드블록, 금지문구를 포함하면 안 된다.",
    )
    add_validation(
        validation_rows,
        "94-V04",
        "프론트 MD 다운로드 Blob 계약 존재",
        {
            "download_action": "download-ai-report-md" in js_source,
            "text_markdown": "text/markdown;charset=utf-8" in js_source,
            "latest_markdown": "latestReport.markdown" in js_source,
            "md_extension": ".md" in js_source,
        },
        "all true",
        all(
            [
                "download-ai-report-md" in js_source,
                "text/markdown;charset=utf-8" in js_source,
                "latestReport.markdown" in js_source,
                ".md" in js_source,
            ]
        ),
        "프론트 다운로드는 서버 facts가 아니라 Markdown 본문을 text/markdown Blob으로 저장해야 한다.",
    )
    add_validation(
        validation_rows,
        "94-V05",
        "금지문구 Markdown 거부 확인",
        unsafe_error[:160],
        "unsafe markdown rejected",
        unsafe_rejected,
        "검증기가 창업 성공확률, 추천 같은 금지 표현을 실제로 차단하는지 확인한다.",
    )

    filename_bad = [row["filename"] for row in file_rows if re.search(r'[\\/:*?"<>|]', row["filename"])]
    add_validation(
        validation_rows,
        "94-V06",
        "MD 파일명 금지 문자 없음",
        filename_bad,
        "[]",
        not filename_bad,
        "상권명과 업종명을 파일명에 쓰더라도 Windows 금지 문자는 제거되어야 한다.",
    )

    validation_df = pd.DataFrame(validation_rows)
    file_df = pd.DataFrame(file_rows)
    pass_count, fail_count = pass_fail_counts(validation_rows)
    decision = "AI_REPORT_MARKDOWN_DOWNLOAD_CONTRACT_PASS" if fail_count == 0 else "AI_REPORT_MARKDOWN_DOWNLOAD_CONTRACT_FAIL"

    file_df.to_csv(OUT_FILES, index=False, encoding="utf-8-sig")
    validation_df.to_csv(OUT_VALIDATION, index=False, encoding="utf-8-sig")
    summary = {
        "validation_number": 94,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "validation_version": VERSION,
        "markdown_file_count": len(file_rows),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "decision": decision,
        "decision_reason_ko": (
            "HTTP dry-run Markdown은 MD 파일로 저장해도 본문/금지문구/파일명 계약을 유지한다."
            if fail_count == 0
            else "MD 다운로드 계약에서 실패 항목이 있어 다운로드 경로 보정이 필요하다."
        ),
        "next_step": "웹 입력 UI 계층화 또는 실제 브라우저 smoke를 진행한다.",
        "outputs": [
            str(OUT_FILES.relative_to(ROOT)),
            str(OUT_VALIDATION.relative_to(ROOT)),
            str(OUT_SUMMARY.relative_to(ROOT)),
            str(OUT_DOC.relative_to(ROOT)),
            str(SAMPLE_DIR.relative_to(ROOT)),
        ],
    }
    with OUT_SUMMARY.open("w", encoding="utf-8-sig") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    doc = f"""# 94. AI 리포트 Markdown 다운로드 계약 검증

작성일: 2026-07-07  
버전: `{VERSION}`  
판정: `{decision}`

## 목적

상세리포트는 웹에서 보고 MD로 가져가는 것이 목표다.  
이번 검증은 93번 HTTP dry-run 성공 응답의 Markdown을 실제 `.md` 파일로 저장했을 때 본문 계약, 금지문구 계약, 파일명 계약이 유지되는지 확인한다.

## 요약

- markdown files: {len(file_rows)}
- PASS: {pass_count}
- FAIL: {fail_count}

## 저장 파일

{md_table(file_rows, ["source_response", "markdown_file", "filename", "bytes", "line_count", "contract_error", "shape_failures", "decoded_ok", "result", "reason_ko"])}

## 검증 결과

{md_table(validation_rows, ["validation_id", "validation_name", "observed", "expected", "result", "reason_ko"])}

## 해석

- 다운로드 대상은 facts JSON 원문이 아니라 Markdown 본문이다.
- Markdown 본문은 코드블록과 JSON 원문을 포함하지 않는다.
- 금지문구 검증기는 위험 문구 샘플을 실제로 차단한다.
- 파일명은 상권명과 업종명을 쓰되 Windows 금지 문자를 제거한다.

## 다음 작업

1. 웹 입력 UI를 계층형 업종 선택과 위치 후보 선택으로 바꾼다.
2. 실제 브라우저 smoke에서 입력, 서버 호출, Markdown 표시, MD 다운로드까지 확인한다.
"""
    OUT_DOC.write_text(doc, encoding="utf-8-sig")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
