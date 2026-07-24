from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUN_ROOT = (
    PROJECT_ROOT
    / "runtime"
    / "evaluations"
    / "two-tier-news-random15-20260723"
)
OLD_SUFFIX = "이 확인되지 않아 점수·등급·추천 판단에는 사용하지 않음"
NEW_SUFFIX = "따라서 점수·등급·추천 판단에는 사용하지 않음"


def _rewrite_strings(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, str):
        for old, new in replacements.items():
            value = value.replace(old, new)
        return value
    if isinstance(value, list):
        return [_rewrite_strings(item, replacements) for item in value]
    if isinstance(value, dict):
        return {
            key: _rewrite_strings(item, replacements)
            for key, item in value.items()
        }
    return value


def _replacement_map(payload: dict[str, Any]) -> dict[str, str]:
    report = payload.get("report")
    if not isinstance(report, dict):
        raise ValueError("artifact JSON does not contain a report object")
    rows = report.get("monitoring_news_evidence")
    if not isinstance(rows, list):
        raise ValueError("report.monitoring_news_evidence is not a list")
    replacements: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        old = str(row.get("applicability_limit") or "").strip()
        if not old.endswith(OLD_SUFFIX):
            continue
        missing = old[: -len(OLD_SUFFIX)].strip()
        if not missing:
            missing = "판단 직접성"
        replacements[old] = f"미확인 항목: {missing}. {NEW_SUFFIX}"
    return replacements


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Normalize the deterministic monitoring exclusion copy in an "
            "existing random-15 report artifact set."
        )
    )
    parser.add_argument("--run-root", type=Path, default=DEFAULT_RUN_ROOT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate that no legacy copy remains without writing files.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_root = args.run_root.resolve()
    manifest = json.loads((run_root / "cases.json").read_text(encoding="utf-8"))
    cases = manifest.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("cases.json must contain a non-empty cases array")

    changed_files = 0
    changed_phrases = 0
    legacy_remaining: list[str] = []
    for case in cases:
        case_id = str(case["id"])
        artifact_path = run_root / "generated" / f"{case_id}_r1.json"
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        replacements = _replacement_map(payload)
        rewritten = _rewrite_strings(payload, replacements)
        serialized = json.dumps(rewritten, ensure_ascii=False)
        if OLD_SUFFIX in serialized:
            legacy_remaining.append(case_id)
        if replacements:
            changed_files += 1
            changed_phrases += len(replacements)
            if not args.check:
                _write_json_atomic(artifact_path, rewritten)

    result = {
        "case_count": len(cases),
        "changed_files": changed_files,
        "changed_phrases": changed_phrases,
        "legacy_remaining": legacy_remaining,
        "mode": "check" if args.check else "write",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if legacy_remaining:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
