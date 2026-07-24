from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RULE = ROOT / "datacorpus" / "_rule_validation"
BACKTEST = ROOT / "datacorpus" / "_score_backtest_gold"
DOC = ROOT / "research" / "rule_validation"

RUN_DATE = "2026-07-07"
VERSION = "transit_accessibility_candidate_holdout_gate.v0.1-20260707"

ROWS = BACKTEST / "gold_engine_backtest_transit_accessibility_engine_candidate_rows.csv"
SUMMARY_59 = RULE / "59_transit_accessibility_candidate_backtest_summary.json"
SUMMARY_60 = RULE / "60_transit_accessibility_engine_candidate_summary.json"
SUMMARY_63 = RULE / "63_transit_accessibility_engine_parallel_output_summary.json"

OUT_METRICS = RULE / "80_transit_accessibility_candidate_holdout_metrics.csv"
OUT_YEAR = RULE / "80_transit_accessibility_candidate_holdout_by_year.csv"
OUT_VALIDATION = RULE / "80_transit_accessibility_candidate_holdout_validation.csv"
OUT_SUMMARY = RULE / "80_transit_accessibility_candidate_holdout_summary.json"
OUT_MD = DOC / "80_transit_accessibility_candidate_holdout_gate_20260707.md"

COLS = [
    "기준_년분기_코드",
    "상권_코드",
    "서비스_업종_코드",
    "자치구_코드_명",
    "weight_set",
    "current_location_score",
    "current_location_score_transit_250m_candidate",
    "axis__accessibility",
    "axis__accessibility_transit_250m_70_30_candidate",
    "transit_total_250m_score",
    "next_sales_pct_same_industry",
    "next_sales_log",
    "excess_log_growth_vs_industry",
    "transit_candidate_engine_active",
    "transit_candidate_engine_promotion_ready",
    "transit_candidate_score_version",
    "transit_candidate_formula_ko",
    "transit_candidate_forbidden_claim_ko",
]


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def pass_fail(ok: bool) -> str:
    return "PASS" if ok else "FAIL"


def corr(df: pd.DataFrame, score: str, target: str) -> float:
    return float(pd.to_numeric(df[score], errors="coerce").corr(pd.to_numeric(df[target], errors="coerce"), method="spearman"))


def metric_row(scope: str, rows: pd.DataFrame) -> dict:
    v24_current = corr(rows, "current_location_score", "next_sales_pct_same_industry")
    candidate_current = corr(rows, "current_location_score_transit_250m_candidate", "next_sales_pct_same_industry")
    v24_access = corr(rows, "axis__accessibility", "next_sales_pct_same_industry")
    candidate_access = corr(rows, "axis__accessibility_transit_250m_70_30_candidate", "next_sales_pct_same_industry")
    return {
        "scope": scope,
        "rows": int(len(rows)),
        "quarter_min": str(rows["기준_년분기_코드"].min()),
        "quarter_max": str(rows["기준_년분기_코드"].max()),
        "v24_current_corr": round(v24_current, 6),
        "candidate_current_corr": round(candidate_current, 6),
        "candidate_current_improvement": round(candidate_current - v24_current, 6),
        "v24_accessibility_corr": round(v24_access, 6),
        "candidate_accessibility_corr": round(candidate_access, 6),
        "candidate_accessibility_improvement": round(candidate_access - v24_access, 6),
        "v24_current_log_corr": round(corr(rows, "current_location_score", "next_sales_log"), 6),
        "candidate_current_log_corr": round(corr(rows, "current_location_score_transit_250m_candidate", "next_sales_log"), 6),
        "v24_excess_growth_corr": round(corr(rows, "current_location_score", "excess_log_growth_vs_industry"), 6),
        "candidate_excess_growth_corr": round(corr(rows, "current_location_score_transit_250m_candidate", "excess_log_growth_vs_industry"), 6),
        "candidate_rank_corr_with_v24": round(
            float(
                pd.to_numeric(rows["current_location_score_transit_250m_candidate"], errors="coerce").corr(
                    pd.to_numeric(rows["current_location_score"], errors="coerce"), method="spearman"
                )
            ),
            6,
        ),
    }


def add_validation(rows: list[dict], check_id: str, name: str, observed: object, expected: object, ok: bool, reason: str) -> None:
    rows.append(
        {
            "validation_id": check_id,
            "validation_name": name,
            "observed": observed,
            "expected": expected,
            "result": pass_fail(ok),
            "reason_ko": reason,
        }
    )


def markdown_table(df: pd.DataFrame, columns: list[str]) -> str:
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for _, row in df[columns].iterrows():
        values = [str(row[col]).replace("\n", " ").replace("|", "\\|") for col in columns]
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def main() -> int:
    RULE.mkdir(parents=True, exist_ok=True)
    DOC.mkdir(parents=True, exist_ok=True)

    summary_59 = read_json(SUMMARY_59)
    summary_60 = read_json(SUMMARY_60)
    summary_63 = read_json(SUMMARY_63)
    df = pd.read_csv(ROWS, encoding="utf-8-sig", usecols=COLS)
    df["year"] = df["기준_년분기_코드"].astype(str).str[:4].astype(int)

    train = df[df["year"].between(2021, 2023)].copy()
    holdout = df[df["year"].between(2024, 2025)].copy()
    metrics = pd.DataFrame(
        [
            metric_row("train_2021_2023", train),
            metric_row("holdout_2024_2025", holdout),
            metric_row("all_2021_2025", df),
        ]
    )
    by_year = pd.DataFrame([metric_row(f"year_{year}", part) for year, part in df.groupby("year", sort=True)])

    train_row = metrics[metrics["scope"] == "train_2021_2023"].iloc[0]
    holdout_row = metrics[metrics["scope"] == "holdout_2024_2025"].iloc[0]
    all_row = metrics[metrics["scope"] == "all_2021_2025"].iloc[0]

    required_years = {2021, 2022, 2023, 2024, 2025}
    flags_active = int(pd.Series(df["transit_candidate_engine_active"]).astype(str).str.lower().eq("true").sum())
    flags_promotion = int(pd.Series(df["transit_candidate_engine_promotion_ready"]).astype(str).str.lower().eq("true").sum())
    forbidden_text = " ".join(df["transit_candidate_forbidden_claim_ko"].dropna().astype(str).unique())
    formula_text = " ".join(df["transit_candidate_formula_ko"].dropna().astype(str).unique())
    forbidden_terms = ["실제 방문자", "실제 구매자", "실제 도보시간", "실제 방문확률", "창업 성공확률"]
    forbid_ok = all(term in forbidden_text for term in forbidden_terms)

    validations: list[dict] = []
    add_validation(
        validations,
        "80-V01",
        "선행 교통 후보 검증 PASS",
        f"59={summary_59.get('decision')}, 60={summary_60.get('decision')}, 63={summary_63.get('decision')}",
        "59/60/63 모두 FAIL 없이 후보 상태",
        summary_59.get("fail_count") == 0 and summary_60.get("fail_count") == 0 and summary_63.get("fail_count") == 0,
        "holdout 게이트는 기존 후보 gold, 후보 엔진 산식, 병렬 출력 검증이 먼저 통과한 경우에만 의미가 있다.",
    )
    add_validation(
        validations,
        "80-V02",
        "백테스트 row와 기간 확인",
        f"rows={len(df)}, years={sorted(df['year'].unique().tolist())}",
        "2021~2025 전체와 1행 이상",
        len(df) > 0 and set(df["year"].unique()) == required_years,
        "시간 분리 검증은 2021~2023 학습구간과 2024~2025 확인구간이 모두 있어야 한다.",
    )
    add_validation(
        validations,
        "80-V03",
        "train/holdout row 존재",
        f"train={len(train)}, holdout={len(holdout)}",
        "둘 다 1행 이상",
        len(train) > 0 and len(holdout) > 0,
        "같은 전체 백테스트에서만 좋아진 후보인지 확인하려면 시간상 뒤쪽 구간을 따로 봐야 한다.",
    )
    add_validation(
        validations,
        "80-V04",
        "train 현재입지 개선",
        f"candidate={train_row['candidate_current_corr']}, v24={train_row['v24_current_corr']}, diff={train_row['candidate_current_improvement']}",
        "diff >= 0.002",
        float(train_row["candidate_current_improvement"]) >= 0.002,
        "후보 산식이 만들어진 앞구간에서도 기존 공식 현재입지 점수보다 주 타깃 상관이 높아야 한다.",
    )
    add_validation(
        validations,
        "80-V05",
        "holdout 현재입지 개선",
        f"candidate={holdout_row['candidate_current_corr']}, v24={holdout_row['v24_current_corr']}, diff={holdout_row['candidate_current_improvement']}",
        "diff >= 0.002",
        float(holdout_row["candidate_current_improvement"]) >= 0.002,
        "뒤쪽 기간에서도 개선이 유지돼야 같은 시험지 과적합 가능성을 낮출 수 있다.",
    )
    add_validation(
        validations,
        "80-V06",
        "train 접근성축 개선",
        f"candidate={train_row['candidate_accessibility_corr']}, v24={train_row['v24_accessibility_corr']}, diff={train_row['candidate_accessibility_improvement']}",
        "diff >= 0.005",
        float(train_row["candidate_accessibility_improvement"]) >= 0.005,
        "교통 후보는 접근성축 보강 후보이므로 축 자체의 설명력이 앞구간에서 개선돼야 한다.",
    )
    add_validation(
        validations,
        "80-V07",
        "holdout 접근성축 개선",
        f"candidate={holdout_row['candidate_accessibility_corr']}, v24={holdout_row['v24_accessibility_corr']}, diff={holdout_row['candidate_accessibility_improvement']}",
        "diff >= 0.005",
        float(holdout_row["candidate_accessibility_improvement"]) >= 0.005,
        "뒤쪽 기간에서 접근성축 개선이 유지돼야 후보를 공식 산식 패치 검토 대상으로 볼 수 있다.",
    )
    add_validation(
        validations,
        "80-V08",
        "연도별 현재입지 개선 유지",
        "; ".join(f"{row.scope}:{row.candidate_current_improvement}" for row in by_year.itertuples()),
        "모든 연도 diff > 0",
        bool((by_year["candidate_current_improvement"] > 0).all()),
        "특정 연도 하나의 우연한 개선이면 공식 점수에 넣기 어렵다.",
    )
    add_validation(
        validations,
        "80-V09",
        "기존 공식 점수와 순위 안정성",
        f"all_rank_corr={all_row['candidate_rank_corr_with_v24']}",
        "rank corr >= 0.98",
        float(all_row["candidate_rank_corr_with_v24"]) >= 0.98,
        "접근성 후보를 넣더라도 공식 현재입지 점수의 전체 순위 체계가 과도하게 뒤집히면 안 된다.",
    )
    add_validation(
        validations,
        "80-V10",
        "엔진 승격 플래그 미활성",
        f"active_true={flags_active}, promotion_true={flags_promotion}",
        "0, 0",
        flags_active == 0 and flags_promotion == 0,
        "이번 검증은 승격 가능성 평가이지, 아직 공식 엔진을 바꾼 상태가 아니다.",
    )
    add_validation(
        validations,
        "80-V11",
        "금지표현 계약 유지",
        forbidden_text,
        ", ".join(forbidden_terms),
        forbid_ok,
        "승하차량 후보를 실제 방문자·구매자·도보시간·방문확률·성공확률로 설명하면 안 된다.",
    )
    add_validation(
        validations,
        "80-V12",
        "고정 산식 문구 확인",
        formula_text,
        "기존 접근성축 70% + 250m 승하차 후보 30%",
        "70%" in formula_text and "30%" in formula_text and "250m" in formula_text,
        "holdout 검증은 59번에서 고른 후보 산식을 임의로 다시 탐색하지 않고 고정해야 한다.",
    )
    add_validation(
        validations,
        "80-V13",
        "성장률 보장 근거 아님",
        f"v24_excess={all_row['v24_excess_growth_corr']}, candidate_excess={all_row['candidate_excess_growth_corr']}",
        "초과성장 개선 요구 없음, 성장 보장 금지",
        abs(float(all_row["candidate_excess_growth_corr"])) < 0.1,
        "이 후보의 목적은 다음분기 동일업종 매출 수준 후보 선별이지 성장률 높은 상권 보장이 아니다.",
    )

    validation = pd.DataFrame(validations)
    fail_count = int(validation["result"].eq("FAIL").sum())
    pass_count = int(validation["result"].eq("PASS").sum())
    ready_for_patch = fail_count == 0
    decision = (
        "TRANSIT_ACCESSIBILITY_HOLDOUT_READY_FOR_OFFICIAL_PATCH_REVIEW_NOT_PATCHED"
        if ready_for_patch
        else "TRANSIT_ACCESSIBILITY_HOLDOUT_NOT_READY"
    )

    metrics.to_csv(OUT_METRICS, index=False, encoding="utf-8-sig")
    by_year.to_csv(OUT_YEAR, index=False, encoding="utf-8-sig")
    validation.to_csv(OUT_VALIDATION, index=False, encoding="utf-8-sig")

    summary = {
        "run_date": RUN_DATE,
        "validation_version": VERSION,
        "source_rows": int(len(df)),
        "train_rows": int(len(train)),
        "holdout_rows": int(len(holdout)),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "train_current_improvement": float(train_row["candidate_current_improvement"]),
        "holdout_current_improvement": float(holdout_row["candidate_current_improvement"]),
        "train_accessibility_improvement": float(train_row["candidate_accessibility_improvement"]),
        "holdout_accessibility_improvement": float(holdout_row["candidate_accessibility_improvement"]),
        "candidate_rank_corr_with_v24": float(all_row["candidate_rank_corr_with_v24"]),
        "ready_for_official_patch_review": ready_for_patch,
        "official_engine_patched": False,
        "decision": decision,
        "decision_reason_ko": "holdout에서도 개선이 유지되어 공식 엔진 패치 검토는 가능하지만, 이 스크립트는 아직 공식 v2.4 산식을 변경하지 않는다.",
        "outputs": [
            str(OUT_METRICS.relative_to(ROOT)),
            str(OUT_YEAR.relative_to(ROOT)),
            str(OUT_VALIDATION.relative_to(ROOT)),
            str(OUT_SUMMARY.relative_to(ROOT)),
            str(OUT_MD.relative_to(ROOT)),
        ],
    }
    OUT_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# 80. 교통 접근성 후보 holdout 승격 게이트",
        "",
        "## 목적",
        "",
        "59~63번에서 교통 승하차량 기반 250m 접근성 후보는 성능이 개선됐지만, 같은 전체 백테스트 안에서 혼합비를 고른 후보였다. "
        "이번 검증은 2021~2023 구간과 2024~2025 holdout 구간을 나누어 개선이 뒤쪽 기간에서도 유지되는지 확인한다.",
        "",
        "## 결론",
        "",
        f"- decision: `{decision}`",
        f"- PASS {pass_count} / FAIL {fail_count}",
        f"- train 현재입지 개선: `{summary['train_current_improvement']}`",
        f"- holdout 현재입지 개선: `{summary['holdout_current_improvement']}`",
        f"- train 접근성축 개선: `{summary['train_accessibility_improvement']}`",
        f"- holdout 접근성축 개선: `{summary['holdout_accessibility_improvement']}`",
        f"- 후보와 기존 공식 현재입지 순위상관: `{summary['candidate_rank_corr_with_v24']}`",
        "",
        "공식 엔진 패치 검토는 가능하다. 다만 이 검증은 아직 공식 v2.4 산식을 바꾸지 않는다. "
        "다음 단계에서 별도 패치와 회귀검증을 해야 한다.",
        "",
        "## 기간별 지표",
        "",
        markdown_table(
            metrics,
            [
                "scope",
                "rows",
                "v24_current_corr",
                "candidate_current_corr",
                "candidate_current_improvement",
                "v24_accessibility_corr",
                "candidate_accessibility_corr",
                "candidate_accessibility_improvement",
                "candidate_rank_corr_with_v24",
            ],
        ),
        "",
        "## 연도별 확인",
        "",
        markdown_table(
            by_year,
            [
                "scope",
                "rows",
                "v24_current_corr",
                "candidate_current_corr",
                "candidate_current_improvement",
                "v24_accessibility_corr",
                "candidate_accessibility_corr",
                "candidate_accessibility_improvement",
            ],
        ),
        "",
        "## 검증 결과",
        "",
        markdown_table(validation, ["validation_id", "validation_name", "observed", "expected", "result", "reason_ko"]),
        "",
        "## 2보 전진",
        "",
        "1. 교통 승하차량 후보가 2021~2023뿐 아니라 2024~2025 holdout에서도 기존 공식 점수보다 개선됨을 확인했다.",
        "2. 후보 산식은 공식 점수를 덮지 않고 승격 검토 가능 상태로만 고정했다.",
        "",
        "## 1보 후퇴",
        "",
        "- 이 검증만으로 공식 v2.4 산식을 바꾸지는 않는다. 다음 단계에서 엔진 패치, 백데이터 재계산, 리포트 문구 계약을 다시 통과해야 한다.",
        "- 교통 후보는 실제 방문자, 실제 구매자, 실제 도보시간, 실제 방문확률, 창업 성공확률로 설명하지 않는다.",
        "",
        "## 산출물",
        "",
        f"- `{OUT_METRICS.relative_to(ROOT)}`",
        f"- `{OUT_YEAR.relative_to(ROOT)}`",
        f"- `{OUT_VALIDATION.relative_to(ROOT)}`",
        f"- `{OUT_SUMMARY.relative_to(ROOT)}`",
        f"- `{OUT_MD.relative_to(ROOT)}`",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
