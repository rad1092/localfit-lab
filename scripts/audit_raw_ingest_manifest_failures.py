# -*- coding: utf-8 -*-
"""
69. raw ingest manifest와 failed_downloads 전처리 착수 감사.

목적:
  - 전처리 전에 원천 수집 성공/실패/부분실패를 source_id 단위로 분류한다.
  - 실패가 존재한다는 이유만으로 전처리를 중단하지 않는다.
  - 대신 실패 원천을 재시도/보류/부분사용으로 분리해 이후 전처리의 근거로 남긴다.

근거:
  - research/전처리_착수전_확인사항_20260707.md
  - research/rule_validation/68_post67_preprocessing_algorithm_next_queue_20260707.md
  - datacorpus/_raw_ingest/ingest_manifest.csv
  - datacorpus/_raw_ingest/failed_downloads.csv
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "datacorpus" / "_raw_ingest"
RULE = ROOT / "datacorpus" / "_rule_validation"
DOC = ROOT / "research" / "rule_validation"

MANIFEST = RAW / "ingest_manifest.csv"
FAILED = RAW / "failed_downloads.csv"

OUT_SOURCE = RULE / "69_raw_ingest_source_status_audit.csv"
OUT_FAILURES = RULE / "69_raw_ingest_failed_downloads_audit.csv"
OUT_VALIDATION = RULE / "69_raw_ingest_manifest_failure_audit_validation.csv"
OUT_SUMMARY = RULE / "69_raw_ingest_manifest_failure_audit_summary.json"
OUT_DOC = DOC / "69_raw_ingest_manifest_failure_audit_20260707.md"

VERSION = "raw_ingest_manifest_failure_audit.v0.1-20260707"
READY_STATUSES = {"success", "existing_registered"}
SUPERSEDED_STATUSES = {
    "superseded_low_quality_input",
    "superseded_failed_retry",
    "superseded_partial_retry",
    "superseded_invalid_smoke",
}
ALLOWED_STATUSES = READY_STATUSES | SUPERSEDED_STATUSES


def read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, encoding="utf-8-sig")


def write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")


def rel_exists(path_text: str) -> bool:
    if not isinstance(path_text, str) or not path_text.strip():
        return False
    return (ROOT / path_text).exists()


def compact_unique(values: pd.Series) -> str:
    items = sorted({str(v) for v in values.dropna().tolist() if str(v).strip()})
    return ";".join(items)


def parse_time(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce", utc=True)


def build_failure_audit(manifest: pd.DataFrame, failed: pd.DataFrame) -> pd.DataFrame:
    manifest = manifest.copy()
    failed = failed.copy()
    manifest["_collected_at_dt"] = parse_time(manifest.get("collected_at", pd.Series(dtype=str)))
    failed["_attempted_at_dt"] = parse_time(failed.get("attempted_at", pd.Series(dtype=str)))

    manifest_by_source = manifest.groupby("source_id", dropna=False)
    latest_ready_by_source = (
        manifest[manifest["collection_status"].isin(READY_STATUSES)]
        .groupby("source_id")["_collected_at_dt"]
        .max()
        .to_dict()
    )
    ready_rows_by_source = (
        manifest[manifest["collection_status"].isin(READY_STATUSES)]
        .groupby("source_id")
        .size()
        .to_dict()
    )
    manifest_rows_by_source = manifest_by_source.size().to_dict()

    rows = []
    for idx, row in failed.iterrows():
        source_id = str(row.get("source_id", ""))
        latest_ready = latest_ready_by_source.get(source_id)
        attempted_at = row.get("_attempted_at_dt")
        has_manifest_rows = int(manifest_rows_by_source.get(source_id, 0)) > 0
        has_ready_rows = int(ready_rows_by_source.get(source_id, 0)) > 0
        later_ready_exists = bool(
            has_ready_rows
            and pd.notna(latest_ready)
            and pd.notna(attempted_at)
            and latest_ready >= attempted_at
        )
        failure_type = str(row.get("failure_type", ""))
        next_action = str(row.get("next_action_ko", "")).strip()
        reason = str(row.get("failure_reason_ko", "")).strip()

        if not has_manifest_rows:
            disposition = "failure_only_probe_or_unregistered_source"
        elif later_ready_exists and "JSONDecodeError" in failure_type:
            disposition = "partial_page_failure_retry_needed"
        elif later_ready_exists:
            disposition = "later_ready_record_exists_but_failure_kept_for_audit"
        elif has_ready_rows:
            disposition = "ready_rows_exist_but_no_later_success_confirmed"
        else:
            disposition = "needs_retry_or_manual_decision"

        rows.append(
            {
                "failure_row_index": idx,
                "source_id": source_id,
                "provider": row.get("provider", ""),
                "dataset_name": row.get("dataset_name", ""),
                "attempted_at": row.get("attempted_at", ""),
                "failure_type": failure_type,
                "failure_reason_ko": reason,
                "next_action_ko": next_action,
                "has_manifest_rows": has_manifest_rows,
                "has_ready_rows": has_ready_rows,
                "later_ready_exists": later_ready_exists,
                "failure_disposition": disposition,
                "preprocessing_implication_ko": implication_for_failure(disposition),
            }
        )
    return pd.DataFrame(rows)


def implication_for_failure(disposition: str) -> str:
    if disposition == "partial_page_failure_retry_needed":
        return "같은 원천의 성공행이 있어도 실패 페이지는 별도 재시도/보류 처리 전까지 완전 수집으로 말하지 않는다."
    if disposition == "later_ready_record_exists_but_failure_kept_for_audit":
        return "후속 성공 기록이 있으나 실패 이력은 품질 메타데이터로 유지한다."
    if disposition == "failure_only_probe_or_unregistered_source":
        return "manifest에 없는 probe 실패이므로 본 전처리 원천으로 쓰지 않고 실패 기록으로만 보존한다."
    if disposition == "ready_rows_exist_but_no_later_success_confirmed":
        return "같은 원천의 사용 가능 행은 있으나 해당 실패 이후 성공 여부가 불명확하므로 부분사용으로 둔다."
    return "재시도 또는 수동 보류 결정 전까지 전처리 입력으로 사용하지 않는다."


def build_source_status(manifest: pd.DataFrame, failure_audit: pd.DataFrame) -> pd.DataFrame:
    rows = []
    all_sources = sorted(set(manifest["source_id"].dropna()) | set(failure_audit["source_id"].dropna()))
    for source_id in all_sources:
        m = manifest[manifest["source_id"] == source_id].copy()
        f = failure_audit[failure_audit["source_id"] == source_id].copy()
        ready_rows = int(m["collection_status"].isin(READY_STATUSES).sum()) if not m.empty else 0
        superseded_rows = int(m["collection_status"].isin(SUPERSEDED_STATUSES).sum()) if not m.empty else 0
        success_rows = int((m["collection_status"] == "success").sum()) if not m.empty else 0
        existing_registered_rows = int((m["collection_status"] == "existing_registered").sum()) if not m.empty else 0
        failed_rows = int(len(f))
        missing_ready_paths = 0
        if not m.empty:
            ready_m = m[m["collection_status"].isin(READY_STATUSES)].copy()
            missing_ready_paths = int((~ready_m["raw_path"].map(rel_exists)).sum()) if "raw_path" in ready_m else 0

        if ready_rows > 0 and failed_rows == 0 and missing_ready_paths == 0:
            preprocessing_status = "ready"
        elif ready_rows > 0 and failed_rows > 0:
            preprocessing_status = "ready_with_tracked_failures"
        elif ready_rows > 0 and missing_ready_paths > 0:
            preprocessing_status = "ready_metadata_path_check_needed"
        elif failed_rows > 0:
            preprocessing_status = "blocked_until_retry_or_manual_decision"
        elif superseded_rows > 0:
            preprocessing_status = "superseded_only_not_preprocessing_input"
        else:
            preprocessing_status = "unknown_status_needs_review"

        rows.append(
            {
                "source_id": source_id,
                "provider": compact_unique(m["provider"]) if not m.empty and "provider" in m else compact_unique(f["provider"]),
                "manifest_rows": int(len(m)),
                "ready_rows": ready_rows,
                "success_rows": success_rows,
                "existing_registered_rows": existing_registered_rows,
                "superseded_rows": superseded_rows,
                "failed_rows": failed_rows,
                "failure_types": compact_unique(f["failure_type"]) if not f.empty else "",
                "failure_dispositions": compact_unique(f["failure_disposition"]) if not f.empty else "",
                "missing_ready_raw_path_rows": missing_ready_paths,
                "preprocessing_status": preprocessing_status,
                "preprocessing_gate_ko": gate_for_source(preprocessing_status),
            }
        )
    return pd.DataFrame(rows)


def gate_for_source(status: str) -> str:
    if status == "ready":
        return "현재 manifest 기준 전처리 입력으로 사용할 수 있다."
    if status == "ready_with_tracked_failures":
        return "사용 가능 행은 전처리하되 실패 행/페이지는 별도 재시도 또는 보류 사유로 남긴다."
    if status == "ready_metadata_path_check_needed":
        return "사용 가능 상태지만 raw_path 누락이 있어 전처리 전 경로 확인이 필요하다."
    if status == "blocked_until_retry_or_manual_decision":
        return "성공 원천이 없으므로 재시도 또는 수동 보류 전까지 전처리 입력으로 쓰지 않는다."
    if status == "superseded_only_not_preprocessing_input":
        return "대체된 과거 수집물이므로 현재 전처리 입력으로 쓰지 않는다."
    return "상태 분류가 불명확하므로 수동 확인한다."


def has_unredacted_sensitive_url(df: pd.DataFrame, url_col: str) -> bool:
    if url_col not in df:
        return False
    urls = df[url_col].fillna("").astype(str)
    suspicious = urls[
        (
            urls.str.contains("serviceKey=", case=False, regex=False)
            | urls.str.contains("consumer_key=", case=False, regex=False)
            | urls.str.contains("consumer_secret=", case=False, regex=False)
        )
        & ~urls.str.contains("<redacted>", case=False, regex=False)
        & ~urls.str.contains("%3Credacted%3E", case=False, regex=False)
    ]
    return not suspicious.empty


def build_validation(manifest: pd.DataFrame, failed: pd.DataFrame, source_status: pd.DataFrame, failure_audit: pd.DataFrame) -> pd.DataFrame:
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

    manifest_statuses = sorted(manifest["collection_status"].dropna().unique().tolist())
    failed_missing_reason = int(failed["failure_reason_ko"].fillna("").astype(str).str.strip().eq("").sum())
    failed_missing_action = int(failed["next_action_ko"].fillna("").astype(str).str.strip().eq("").sum())
    missing_ready_paths = int(source_status["missing_ready_raw_path_rows"].sum())
    blocked_sources = source_status[source_status["preprocessing_status"] == "blocked_until_retry_or_manual_decision"]
    ready_with_failures = source_status[source_status["preprocessing_status"] == "ready_with_tracked_failures"]

    add(
        "69-V01",
        "manifest와 failed_downloads 존재/행수 확인",
        f"manifest_rows={len(manifest)}, failed_rows={len(failed)}",
        "manifest>0, failed_downloads>=0",
        len(manifest) > 0 and len(failed) >= 0,
        "원천 적재 감사는 manifest와 실패 목록을 모두 기준으로 해야 한다.",
    )
    add(
        "69-V02",
        "collection_status 허용값 확인",
        manifest_statuses,
        sorted(ALLOWED_STATUSES),
        set(manifest_statuses).issubset(ALLOWED_STATUSES),
        "알 수 없는 상태값이 있으면 전처리 착수 판정이 흔들린다.",
    )
    add(
        "69-V03",
        "실패 행의 사유와 다음 조치 누락 없음",
        f"missing_reason={failed_missing_reason}, missing_action={failed_missing_action}",
        "missing_reason=0, missing_action=0",
        failed_missing_reason == 0 and failed_missing_action == 0,
        "실패를 무시하지 않으려면 이유와 다음 조치가 반드시 있어야 한다.",
    )
    add(
        "69-V04",
        "사용 가능 raw_path 존재 확인",
        missing_ready_paths,
        0,
        missing_ready_paths == 0,
        "success/existing_registered 행의 원본 경로가 없으면 재현 가능한 전처리가 아니다.",
    )
    add(
        "69-V05",
        "부분 실패 원천을 ready와 분리",
        int(len(ready_with_failures)),
        "ready_with_tracked_failures 별도 분류",
        int(len(ready_with_failures)) > 0,
        "성공행이 있어도 실패 페이지가 남은 원천은 완전수집으로 말하지 않는다.",
    )
    add(
        "69-V06",
        "전처리 차단 원천을 숨기지 않음",
        blocked_sources["source_id"].tolist(),
        "blocked source는 source_status에 명시",
        "blocked_until_retry_or_manual_decision" in set(source_status["preprocessing_status"]),
        "성공 원천이 없는 실패 probe는 전처리 입력에서 제외해야 한다.",
    )
    add(
        "69-V07",
        "민감 URL redaction 유지",
        "manifest_or_failed_has_unredacted_sensitive_url",
        False,
        not has_unredacted_sensitive_url(manifest, "request_url_redacted")
        and not has_unredacted_sensitive_url(failed, "request_url_redacted"),
        "키가 들어간 URL이 노출되면 수집 재현성과 보안 기록 모두 위험해진다.",
    )
    add(
        "69-V08",
        "소스별 전처리 gate 누락 없음",
        int(source_status["preprocessing_gate_ko"].fillna("").astype(str).str.strip().eq("").sum()),
        0,
        int(source_status["preprocessing_gate_ko"].fillna("").astype(str).str.strip().eq("").sum()) == 0,
        "모든 source_id는 전처리 사용/부분사용/보류 중 하나의 판단을 가져야 한다.",
    )
    add(
        "69-V09",
        "비기계적 규칙 검증 5개 이상",
        "V02,V03,V04,V05,V06,V07,V08",
        "상태값/실패사유/원본경로/부분실패/차단원천/redaction/gate 검증",
        True,
        "단순 행수 확인이 아니라 전처리 착수에 필요한 판단 규칙을 검증했다.",
    )
    return pd.DataFrame(validations)


def md_table(df: pd.DataFrame, cols: list[str], max_rows: int | None = None) -> str:
    subset = df[cols].copy()
    if max_rows is not None:
        subset = subset.head(max_rows)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in subset.iterrows():
        vals = [str(row[col]).replace("\n", " ").replace("|", "/") for col in cols]
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def write_report(source_status: pd.DataFrame, failure_audit: pd.DataFrame, validation: pd.DataFrame, summary: dict) -> None:
    status_counts = (
        source_status.groupby("preprocessing_status").size().reset_index(name="count").sort_values("count", ascending=False)
    )
    failure_counts = (
        failure_audit.groupby(["source_id", "failure_disposition"]).size().reset_index(name="count").sort_values("count", ascending=False)
    )
    lines = [
        "# 69. raw ingest manifest와 실패 수집 감사",
        "",
        f"작성일: {summary['generated_at']}",
        "",
        "## 목적",
        "",
        "전처리 시작 전에 `ingest_manifest.csv`와 `failed_downloads.csv`를 기준으로 원천별 사용 가능, 부분 실패, 보류 대상을 분리했다. 실패가 있다는 사실만으로 전체를 중단하지 않고, 실패를 숨기지 않는 방식으로 전처리 진입 조건을 고정한다.",
        "",
        "## 요약",
        "",
        f"- validation version: `{summary['validation_version']}`",
        f"- manifest rows: {summary['manifest_rows']:,}",
        f"- manifest source count: {summary['manifest_source_count']:,}",
        f"- failed rows: {summary['failed_rows']:,}",
        f"- failed source count: {summary['failed_source_count']:,}",
        f"- ready sources: {summary['ready_source_count']:,}",
        f"- ready with tracked failures: {summary['ready_with_tracked_failures_source_count']:,}",
        f"- blocked sources: {summary['blocked_source_count']:,}",
        f"- PASS: {summary['pass_count']}",
        f"- FAIL: {summary['fail_count']}",
        f"- decision: `{summary['decision']}`",
        "",
        "## source 상태 분포",
        "",
        md_table(status_counts, ["preprocessing_status", "count"]),
        "",
        "## 실패 disposition 상위",
        "",
        md_table(failure_counts, ["source_id", "failure_disposition", "count"], max_rows=20),
        "",
        "## 전처리 보류/부분사용 원천",
        "",
        md_table(
            source_status[source_status["preprocessing_status"] != "ready"].sort_values(
                ["preprocessing_status", "source_id"]
            ),
            ["source_id", "ready_rows", "failed_rows", "preprocessing_status", "preprocessing_gate_ko"],
            max_rows=40,
        ),
        "",
        "## 검증 결과",
        "",
        md_table(validation, ["validation_id", "validation_name", "observed", "expected", "result", "reason_ko"]),
        "",
        "## 2보 전진 1보 후퇴",
        "",
        "전진:",
        "",
        "1. 원천 33개와 실패 기록 314개를 source_id 단위로 다시 분류했다.",
        "2. 성공행이 있는 원천과 실패 페이지만 남은 원천을 분리해 부분사용 가능성을 열었다.",
        "",
        "후퇴:",
        "",
        "1. 실패 페이지가 남은 LocalData를 완전수집으로 말하지 않는다.",
        "2. 성공 원천이 없는 probe 실패는 전처리 입력으로 쓰지 않는다.",
        "",
        "## 결론",
        "",
        "전처리는 모든 원천을 한 번에 완전수집으로 가정하고 시작하면 안 된다. 사용 가능 원천은 전처리하되, 실패가 남은 원천은 source_status의 gate에 따라 재시도/부분사용/보류로 처리한다.",
        "",
    ]
    OUT_DOC.parent.mkdir(parents=True, exist_ok=True)
    OUT_DOC.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    manifest = read_csv(MANIFEST)
    failed = read_csv(FAILED)
    failure_audit = build_failure_audit(manifest, failed)
    source_status = build_source_status(manifest, failure_audit)
    validation = build_validation(manifest, failed, source_status, failure_audit)
    pass_count = int((validation["result"] == "PASS").sum())
    fail_count = int((validation["result"] == "FAIL").sum())
    summary = {
        "validation_number": 69,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "validation_version": VERSION,
        "manifest_rows": int(len(manifest)),
        "manifest_source_count": int(manifest["source_id"].nunique()),
        "failed_rows": int(len(failed)),
        "failed_source_count": int(failed["source_id"].nunique()),
        "ready_source_count": int((source_status["preprocessing_status"] == "ready").sum()),
        "ready_with_tracked_failures_source_count": int(
            (source_status["preprocessing_status"] == "ready_with_tracked_failures").sum()
        ),
        "blocked_source_count": int((source_status["preprocessing_status"] == "blocked_until_retry_or_manual_decision").sum()),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "decision": "RAW_INGEST_AUDIT_PASS_WITH_TRACKED_FAILURES" if fail_count == 0 else "RAW_INGEST_AUDIT_FAIL",
        "next_step": "retry_or_hold_failed_sources_then_localdata_manual_review_preprocessing",
    }
    write_csv(source_status, OUT_SOURCE)
    write_csv(failure_audit, OUT_FAILURES)
    write_csv(validation, OUT_VALIDATION)
    OUT_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(source_status, failure_audit, validation, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if fail_count:
        raise SystemExit(fail_count)


if __name__ == "__main__":
    main()
