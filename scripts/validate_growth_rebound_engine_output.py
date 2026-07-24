# -*- coding: utf-8 -*-
"""
공식 점수 엔진 출력에 성장 반등 후보가 별도 컬럼으로 붙었는지 검증한다.

이 검증은 38번 기록이다.
핵심은 `growth_rebound_candidate_score`를 현재입지 점수에 섞지 않고,
단건 JSON과 batch CSV에 후보 신호로만 노출했는지 확인하는 것이다.
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import build_rule_based_location_scores as engine  # noqa: E402


OUT_DIR = ROOT / "datacorpus" / "_location_judgement_outputs"
GOLD = ROOT / "datacorpus" / "_gold"
RULE_VALIDATION = ROOT / "datacorpus" / "_rule_validation"
RESEARCH_RULE_VALIDATION = ROOT / "research" / "rule_validation"

RUN_DATE = "2026-07-04"
VALIDATION_VERSION = "growth_rebound_engine_output_validation.v1.0-20260704"
QUARTER = "20261"
SAMPLE_JSON = OUT_DIR / f"loc_score_v2_3001491_CS100001_{QUARTER}.json"
REBOUND_GOLD = GOLD / "gold_growth_rebound_candidate_q_industry.csv"
SUMMARY_37 = RULE_VALIDATION / "37_growth_rebound_engine_attachment_summary.json"

OUT_VALIDATION = RULE_VALIDATION / "38_growth_rebound_engine_output_validation.csv"
OUT_PROFILE = RULE_VALIDATION / "38_growth_rebound_engine_output_profile.csv"
OUT_SUMMARY = RULE_VALIDATION / "38_growth_rebound_engine_output_summary.json"
OUT_REPORT = RESEARCH_RULE_VALIDATION / "38_growth_rebound_engine_output_validation_20260704.md"

KEYS = ["기준_년분기_코드", "상권_코드", "서비스_업종_코드"]
REBOUND_COL = "growth_rebound_candidate_score"
REQUIRED_BATCH_COLS = [
    REBOUND_COL,
    "growth_rebound_candidate_grade",
    "growth_rebound_gate_reason",
    "growth_rebound_candidate_status",
    "growth_rebound_runtime_feature_safe",
    "growth_rebound_score_engine_active",
    "growth_rebound_activation_required_ko",
]
FORBIDDEN_RUNTIME_COL_PATTERNS = [
    "next_sales",
    "next_log_growth",
    "next_growth",
    "excess_log_growth",
    "future_",
    "미래",
]


@dataclass
class Validation:
    review_round: str
    rule_name: str
    observed: object
    expected: object
    result: str
    reason_ko: str


validations: list[Validation] = []


def ensure_dirs() -> None:
    RULE_VALIDATION.mkdir(parents=True, exist_ok=True)
    RESEARCH_RULE_VALIDATION.mkdir(parents=True, exist_ok=True)


def add_validation(review_round: str, rule_name: str, observed: object, expected: object, result: str, reason_ko: str) -> None:
    validations.append(Validation(review_round, rule_name, observed, expected, result, reason_ko))


def read_csv(path: Path, **kwargs) -> pd.DataFrame:
    return pd.read_csv(path, encoding="utf-8-sig", low_memory=False, **kwargs)


def latest_batch_path() -> Path:
    paths = sorted(OUT_DIR.glob(f"loc_score_v2_batch_{QUARTER}_*.csv"), key=lambda p: p.stat().st_mtime)
    if not paths:
        raise FileNotFoundError(f"loc_score_v2_batch_{QUARTER}_*.csv")
    return paths[-1]


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def no_forbidden_runtime_columns(columns: list[str]) -> tuple[bool, list[str]]:
    violations = []
    for col in columns:
        low = col.lower()
        if col == "decision_label":
            continue
        if any(pattern in low or pattern in col for pattern in FORBIDDEN_RUNTIME_COL_PATTERNS):
            violations.append(col)
    return len(violations) == 0, violations


def safe_value(value: Any) -> str:
    if isinstance(value, float):
        if math.isnan(value):
            return "nan"
        return f"{value:.6f}"
    return str(value)


def profile_outputs(batch_path: Path, batch: pd.DataFrame, rebound_gold: pd.DataFrame, sample_json: dict[str, Any], summary_37: dict[str, Any]) -> pd.DataFrame:
    latest_rebound = rebound_gold[rebound_gold["기준_년분기_코드"] == QUARTER].copy()
    rows = [
        {"item": "score_version", "value": engine.SCORE_VERSION, "note_ko": "공식 엔진 점수 버전"},
        {"item": "batch_path", "value": str(batch_path.relative_to(ROOT)), "note_ko": "검증에 사용한 최신 batch CSV"},
        {"item": "sample_json_path", "value": str(SAMPLE_JSON.relative_to(ROOT)), "note_ko": "검증에 사용한 단건 JSON"},
        {"item": "batch_rows", "value": len(batch), "note_ko": "최신분기 공식 batch 행 수"},
        {"item": "batch_rebound_non_null_rows", "value": int(batch[REBOUND_COL].notna().sum()), "note_ko": "batch에서 반등 후보 점수가 붙은 행 수"},
        {"item": "batch_rebound_missing_rows", "value": int(batch[REBOUND_COL].isna().sum()), "note_ko": "매출 universe 밖 또는 최근4분기 이력 gate로 후보가 없는 행"},
        {"item": "rebound_gold_latest_rows", "value": len(latest_rebound), "note_ko": "반등 후보 gold 최신분기 행 수"},
        {"item": "rebound_gold_latest_non_null_rows", "value": int(latest_rebound[REBOUND_COL].notna().sum()), "note_ko": "반등 후보 gold 최신분기 점수 산출 행"},
        {"item": "sample_rebound_score", "value": sample_json["scores"].get(REBOUND_COL), "note_ko": "단건 JSON의 반등 후보 점수"},
        {"item": "sample_rebound_status", "value": sample_json["scores"].get("growth_rebound_candidate_status"), "note_ko": "단건 JSON의 반등 후보 상태"},
        {"item": "summary37_old_excess_corr", "value": summary_37.get("old_growth_excess_corr"), "note_ko": "37번 비교의 기존 성장잠재 초과성장 상관"},
        {"item": "summary37_new_excess_corr", "value": summary_37.get("new_rebound_excess_corr"), "note_ko": "37번 비교의 반등 후보 초과성장 상관"},
        {"item": "summary37_fail_count", "value": summary_37.get("validation_fail_count"), "note_ko": "37번 비교 검증 실패 수"},
    ]
    return pd.DataFrame(rows)


def build_validations(batch_path: Path, batch: pd.DataFrame, rebound_gold: pd.DataFrame, sample_json: dict[str, Any], summary_37: dict[str, Any]) -> pd.DataFrame:
    header = batch.columns.tolist()
    missing_batch_cols = [c for c in REQUIRED_BATCH_COLS if c not in header]
    forbidden_ok, forbidden_runtime_cols = no_forbidden_runtime_columns(header)
    score_versions = sorted(batch["score_version"].dropna().astype(str).unique().tolist()) if "score_version" in batch.columns else []
    latest_rebound = rebound_gold[rebound_gold["기준_년분기_코드"] == QUARTER].copy()
    rebound_dups = int(rebound_gold.duplicated(KEYS).sum())
    latest_rebound_non_null = int(latest_rebound[REBOUND_COL].notna().sum())
    batch_rebound_non_null = int(batch[REBOUND_COL].notna().sum()) if REBOUND_COL in batch.columns else 0
    active_count = int(batch.get("growth_rebound_score_engine_active", pd.Series(dtype=object)).astype(str).str.lower().isin(["true", "1"]).sum())

    sample_scores = sample_json.get("scores", {})
    sample_evidence = sample_json.get("evidence_pack", {}).get("evidence_only", {}).get("성장_반등_후보", {})

    add_validation(
        "검토1_공식출력계약",
        "batch CSV에 반등 후보 컬럼 존재",
        ",".join(missing_batch_cols) if missing_batch_cols else "모두 존재",
        "필수 컬럼 모두 존재",
        "PASS" if not missing_batch_cols else "FAIL",
        "공식 batch 출력에서 후보 점수, 등급, gate, 상태, 금지문구 메타를 확인할 수 있어야 한다.",
    )
    add_validation(
        "검토1_공식출력계약",
        "단건 JSON scores에 반등 후보 존재",
        sample_scores.get(REBOUND_COL),
        "null 아님",
        "PASS" if sample_scores.get(REBOUND_COL) is not None else "FAIL",
        "AI 리포트 단건 JSON에서도 후보 점수를 별도 점수로 전달해야 한다.",
    )
    add_validation(
        "검토1_공식출력계약",
        "score_version 최신화",
        ";".join(score_versions),
        engine.SCORE_VERSION,
        "PASS" if score_versions == [engine.SCORE_VERSION] and sample_json.get("score_version") == engine.SCORE_VERSION else "FAIL",
        "구버전 batch 캐시와 새 공식 출력이 섞이면 검증 수치가 무의미해진다.",
    )
    add_validation(
        "검토2_grain_key",
        "반등 후보 gold key 중복 없음",
        rebound_dups,
        0,
        "PASS" if rebound_dups == 0 else "FAIL",
        "분기×상권×업종 후보 gold가 중복되면 공식 엔진 left join이 fan-out된다.",
    )
    add_validation(
        "검토2_grain_key",
        "batch 행 보존과 후보 결측 해석",
        f"batch_rows={len(batch)}, rebound_non_null={batch_rebound_non_null}, latest_gold_non_null={latest_rebound_non_null}",
        "batch 행 유지, 후보 결측 허용",
        "PASS" if len(batch) > 0 and batch_rebound_non_null == latest_rebound_non_null else "FAIL",
        "최신분기 batch는 점포 universe까지 포함하므로 후보 결측이 있다. 결측은 조인 실패가 아니라 매출 universe 밖 또는 4분기 이력 gate로 해석한다.",
    )
    add_validation(
        "검토3_시간누수",
        "런타임 batch에 미래 라벨 컬럼 없음",
        ",".join(forbidden_runtime_cols) if forbidden_runtime_cols else "없음",
        "next/excess/future/미래 컬럼 없음",
        "PASS" if forbidden_ok else "FAIL",
        "공식 런타임 출력에는 next_sales, excess_log_growth 같은 백테스트 정답지가 들어가면 안 된다.",
    )
    add_validation(
        "검토3_시간누수",
        "반등 후보 runtime safe 유지",
        sorted(latest_rebound["runtime_feature_safe"].astype(str).str.lower().unique().tolist()),
        "true만",
        "PASS" if set(latest_rebound["runtime_feature_safe"].astype(str).str.lower().unique()) == {"true"} else "FAIL",
        "후보 gold는 미래 라벨 없는 현재/과거 피처만 포함해야 공식 출력에 붙일 수 있다.",
    )
    add_validation(
        "검토4_점수분리",
        "반등 후보가 현재입지 축에 미포함",
        REBOUND_COL in engine.INDICATORS or "growth_rebound" in engine.CURRENT_AXES,
        False,
        "PASS" if REBOUND_COL not in engine.INDICATORS and "growth_rebound" not in engine.CURRENT_AXES else "FAIL",
        "반등 후보는 현재입지 점수와 등급 산식에 섞지 않고 후보 신호로만 둔다.",
    )
    add_validation(
        "검토4_점수분리",
        "score_engine_active False 유지",
        active_count,
        0,
        "PASS" if active_count == 0 else "FAIL",
        "공식 출력에 붙였어도 엔진 공식 점수로 활성화된 것은 아니다.",
    )
    add_validation(
        "검토5_문구계약",
        "단건 JSON evidence에 사용제한 문구 존재",
        sample_evidence.get("사용_제한"),
        "초과성장/반등 후보 신호와 금지표현 제한",
        "PASS" if "초과성장/반등 후보" in str(sample_evidence.get("사용_제한")) else "FAIL",
        "LLM 리포트가 후보 점수를 성공확률이나 매출 수준 점수로 바꾸지 못하게 evidence에 제한을 둔다.",
    )
    add_validation(
        "검토6_37번근거연결",
        "37번 전체 라벨판 비교 개선 유지",
        f"old={safe_value(summary_37.get('old_growth_excess_corr'))}, new={safe_value(summary_37.get('new_rebound_excess_corr'))}, fail={summary_37.get('validation_fail_count')}",
        "new > old and fail=0",
        "PASS" if summary_37.get("new_rebound_excess_corr", 0) > summary_37.get("old_growth_excess_corr", 0) and summary_37.get("validation_fail_count") == 0 else "FAIL",
        "공식 출력 부착은 37번의 전체 라벨판 비교를 근거로 하되, 교체가 아니라 병렬 후보 출력이다.",
    )

    out = pd.DataFrame([v.__dict__ for v in validations])
    out.insert(0, "validation_id", range(1, len(out) + 1))
    return out


def write_report(batch_path: Path, profile: pd.DataFrame, validation: pd.DataFrame, summary: dict[str, Any]) -> None:
    lines = [
        "# 성장 반등 후보 공식 엔진 출력 부착 검증",
        "",
        "작성일: 2026-07-04",
        "",
        "## 1. 목적",
        "",
        "37번에서 개선 신호를 확인한 `growth_rebound_candidate_score`를 공식 점수 엔진 출력에 별도 후보 컬럼으로 붙였는지 확인한다.",
        "",
        "이 검증은 엔진 교체가 아니다. 현재입지 점수, 등급, 가중치 산식에는 반등 후보를 섞지 않는다.",
        "",
        "## 2. 산출물",
        "",
        f"- `datacorpus/_location_judgement_outputs/{batch_path.name}`",
        f"- `datacorpus/_location_judgement_outputs/{SAMPLE_JSON.name}`",
        f"- `datacorpus/_rule_validation/{OUT_VALIDATION.name}`",
        f"- `datacorpus/_rule_validation/{OUT_PROFILE.name}`",
        f"- `datacorpus/_rule_validation/{OUT_SUMMARY.name}`",
        "",
        "## 3. 요약",
        "",
        "| 항목 | 값 |",
        "|---|---:|",
        f"| score_version | {summary['score_version']} |",
        f"| batch_rows | {summary['batch_rows']:,} |",
        f"| batch_rebound_non_null_rows | {summary['batch_rebound_non_null_rows']:,} |",
        f"| batch_rebound_missing_rows | {summary['batch_rebound_missing_rows']:,} |",
        f"| summary37_old_excess_corr | {summary['summary37_old_excess_corr']:.6f} |",
        f"| summary37_new_excess_corr | {summary['summary37_new_excess_corr']:.6f} |",
        f"| validation PASS | {summary['validation_pass_count']} |",
        f"| validation FAIL | {summary['validation_fail_count']} |",
        "",
        "## 4. 6회 규칙 검토",
        "",
        "| review_round | rule_name | observed | expected | result | reason_ko |",
        "|---|---|---|---|---|---|",
    ]
    for row in validation.itertuples(index=False):
        lines.append(f"| {row.review_round} | {row.rule_name} | {row.observed} | {row.expected} | {row.result} | {row.reason_ko} |")

    lines.extend(
        [
            "",
            "## 5. 출력 프로필",
            "",
            "| item | value | note_ko |",
            "|---|---:|---|",
        ]
    )
    for row in profile.itertuples(index=False):
        lines.append(f"| {row.item} | {row.value} | {row.note_ko} |")

    lines.extend(
        [
            "",
            "## 6. 판정",
            "",
            "공식 엔진 출력에는 `growth_rebound_candidate_score`가 붙었다.",
            "",
            "다만 이 컬럼은 현재입지 점수나 등급 산식에 들어가지 않는다. 다음분기 매출 수준 점수도 아니다.",
            "",
            "리포트에서는 `초과성장/반등 후보 신호`로만 설명하고, 성공확률·성장률 보장·개별 매장 매출 보장 표현은 금지한다.",
            "",
            "다음 단계는 이 병렬 후보 출력이 AI 상세리포트 문구와 UI 다운로드 MD에 어떻게 들어갈지 계약을 고정하는 것이다.",
        ]
    )
    OUT_REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ensure_dirs()
    batch_path = latest_batch_path()
    batch = read_csv(
        batch_path,
        dtype={"기준_년분기_코드": str, "상권_코드": str, "서비스_업종_코드": str},
    )
    rebound_gold = read_csv(
        REBOUND_GOLD,
        dtype={"기준_년분기_코드": str, "상권_코드": str, "서비스_업종_코드": str},
    )
    sample_json = load_json(SAMPLE_JSON)
    summary_37 = load_json(SUMMARY_37)

    profile = profile_outputs(batch_path, batch, rebound_gold, sample_json, summary_37)
    validation = build_validations(batch_path, batch, rebound_gold, sample_json, summary_37)
    summary = {
        "run_date": RUN_DATE,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "validation_version": VALIDATION_VERSION,
        "score_version": engine.SCORE_VERSION,
        "batch_path": str(batch_path.relative_to(ROOT)),
        "sample_json_path": str(SAMPLE_JSON.relative_to(ROOT)),
        "batch_rows": int(len(batch)),
        "batch_rebound_non_null_rows": int(batch[REBOUND_COL].notna().sum()),
        "batch_rebound_missing_rows": int(batch[REBOUND_COL].isna().sum()),
        "summary37_old_excess_corr": float(summary_37["old_growth_excess_corr"]),
        "summary37_new_excess_corr": float(summary_37["new_rebound_excess_corr"]),
        "validation_pass_count": int((validation["result"] == "PASS").sum()),
        "validation_fail_count": int((validation["result"] == "FAIL").sum()),
        "decision": "공식엔진_별도후보컬럼_부착통과_점수산식미반영",
        "decision_reason_ko": "반등 후보는 공식 JSON/batch 출력에 붙었지만 현재입지 점수와 등급 산식에는 들어가지 않는다.",
    }

    profile.to_csv(OUT_PROFILE, index=False, encoding="utf-8-sig")
    validation.to_csv(OUT_VALIDATION, index=False, encoding="utf-8-sig")
    OUT_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_report(batch_path, profile, validation, summary)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["validation_fail_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
