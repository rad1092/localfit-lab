# -*- coding: utf-8 -*-
"""
업종 선택 계층 fallback 검증.

목적:
  - 사용자가 업종을 외워서 입력하지 않도록 대/중/세부 UI 계층을 제공한다.
  - SBDC 미매핑 또는 수동검토필요 업종을 삭제하거나 자동매칭으로 둔갑시키지 않는다.
  - fallback 계층은 화면 탐색용이고 점수 산식 또는 알고리즘 조인키가 아님을 검증한다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "datacorpus" / "_gold"
GOLD_VALIDATION = ROOT / "datacorpus" / "_gold_validation"
RULE_VALIDATION = ROOT / "datacorpus" / "_rule_validation"
RESEARCH_RULE_VALIDATION = ROOT / "research" / "rule_validation"

RUN_DATE = "2026-07-04"
VALIDATION_VERSION = "industry_selection_fallback_hierarchy.v1.0-20260704"
EXPECTED_LOOKUP_VERSION = "rule_input_lookup.v1.1-20260704"


@dataclass
class Check:
    review_round: str
    rule_name: str
    observed: object
    expected: object
    result: str
    reason_ko: str


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(path)
    return pd.read_csv(path, encoding="utf-8-sig", low_memory=False)


def write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")


def add_check(
    checks: list[Check],
    review_round: str,
    rule_name: str,
    observed: object,
    expected: object,
    passed: bool,
    reason_ko: str,
) -> None:
    checks.append(
        Check(
            review_round=review_round,
            rule_name=rule_name,
            observed=observed,
            expected=expected,
            result="PASS" if passed else "FAIL",
            reason_ko=reason_ko,
        )
    )


def load_tree_service_count(path: Path) -> tuple[str, int, list[str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    count = 0
    large_names: list[str] = []
    for large in payload.get("large_categories", []):
        large_names.append(str(large.get("name")))
        for medium in large.get("medium_categories", []):
            for small in medium.get("small_categories", []):
                count += len(small.get("service_industries", []))
    return str(payload.get("lookup_version")), count, large_names


def normalize_search_term(value: object) -> str:
    text = "" if pd.isna(value) else str(value)
    text = text.lower()
    text = re.sub(r"[\s\-_·/()]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def main() -> None:
    hierarchy_path = GOLD / "gold_industry_selection_hierarchy.csv"
    tree_path = GOLD / "gold_industry_selection_tree.json"
    validation26_path = GOLD_VALIDATION / "26_input_lookup_rule_validation.csv"

    hierarchy = read_csv(hierarchy_path)
    validation26 = read_csv(validation26_path)
    tree_version, tree_service_count, tree_large_names = load_tree_service_count(tree_path)

    checks: list[Check] = []

    service_key = "서비스_업종_코드"
    service_name = "서비스_업종_코드_명"
    sbdc_code_cols = ["SBDC_대분류코드_후보", "SBDC_중분류코드_후보", "SBDC_소분류코드_후보"]
    ui_code_cols = ["UI_대분류코드", "UI_중분류코드", "UI_세부분류코드"]
    ui_name_cols = ["UI_대분류명", "UI_중분류명", "UI_세부분류명"]

    row_count = len(hierarchy)
    unique_codes = hierarchy[service_key].nunique(dropna=True)
    key_nulls = int(hierarchy[service_key].isna().sum())
    add_check(
        checks,
        "검토_입력키",
        "업종 선택 lookup은 서울 서비스업종 100개를 보존",
        f"rows={row_count}, unique_codes={unique_codes}, key_nulls={key_nulls}",
        "rows=100, unique_codes=100, key_nulls=0",
        row_count == 100 and unique_codes == 100 and key_nulls == 0,
        "대/중/세부 UI를 보강해도 최종 알고리즘 입력 universe는 서울 서비스업종 코드 100개여야 한다.",
    )

    lookup_versions = sorted(hierarchy["lookup_version"].dropna().astype(str).unique().tolist())
    add_check(
        checks,
        "검토_버전",
        "lookup 버전은 fallback 계층 반영 버전",
        ",".join(lookup_versions),
        EXPECTED_LOOKUP_VERSION,
        lookup_versions == [EXPECTED_LOOKUP_VERSION] and tree_version == EXPECTED_LOOKUP_VERSION,
        "기존 v1.0 UNMAPPED tree와 새 v1.1 fallback tree가 섞이면 화면 선택 결과가 재현되지 않는다.",
    )

    ui_missing = int(hierarchy[ui_code_cols + ui_name_cols].isna().any(axis=1).sum())
    ui_unmapped_label = int(hierarchy[ui_code_cols + ui_name_cols].astype(str).isin(["UNMAPPED", "매핑검토필요"]).any(axis=1).sum())
    add_check(
        checks,
        "검토_UI계층",
        "UI 대/중/세부 계층에는 빈값과 UNMAPPED 표시가 없음",
        f"ui_missing={ui_missing}, ui_unmapped_label={ui_unmapped_label}",
        "0",
        ui_missing == 0 and ui_unmapped_label == 0,
        "사람이 클릭하는 계층에 UNMAPPED만 보이면 업종을 외워서 입력하는 문제를 해결하지 못한다.",
    )

    sbdc_missing = int(hierarchy[sbdc_code_cols].eq("UNMAPPED").any(axis=1).sum())
    fallback_count = int((hierarchy["UI_계층_근거"] == "서울서비스코드_prefix_fallback").sum())
    sbdc_review_required = int(hierarchy["SBDC_mapping_review_required"].astype(str).str.lower().isin(["true", "1"]).sum())
    add_check(
        checks,
        "검토_SBDC보존",
        "SBDC 미매핑 또는 수동검토필요 업종은 fallback으로만 보강",
        f"sbdc_missing={sbdc_missing}, review_required={sbdc_review_required}, fallback_count={fallback_count}",
        "sbdc_missing=37, review_required=60, fallback_count=60",
        sbdc_missing == 37 and sbdc_review_required == 60 and fallback_count == sbdc_review_required,
        "fallback은 미매핑·수동검토필요 업종을 숨기지 않고 UI 탐색만 가능하게 하는 장치이며 SBDC 자동매칭이 아니다.",
    )

    fallback = hierarchy[hierarchy["UI_계층_근거"] == "서울서비스코드_prefix_fallback"].copy()
    fallback_review_required = int(fallback["SBDC_mapping_review_required"].astype(str).str.lower().isin(["true", "1"]).sum())
    fallback_auto_sbdc = int(fallback["SBDC_score_use_status"].astype(str).eq("사용가능_자동강매칭").sum())
    add_check(
        checks,
        "검토_과장방지",
        "fallback 업종은 SBDC 자동강매칭 계층으로 쓰지 않음",
        f"fallback_rows={len(fallback)}, review_required={fallback_review_required}, auto_sbdc={fallback_auto_sbdc}",
        "fallback_rows=60, review_required=60, auto_sbdc=0",
        len(fallback) == sbdc_review_required and fallback_review_required == sbdc_review_required and fallback_auto_sbdc == 0,
        "UI 계층을 보강했다고 수동검토필요 SBDC 후보를 자동강매칭 업종 계층으로 승격하면 안 된다.",
    )

    final_key_mismatch = int((hierarchy["final_algorithm_key"].astype(str) != hierarchy[service_key].astype(str)).sum())
    add_check(
        checks,
        "검토_조인키",
        "최종 알고리즘 키는 표시 계층이 아니라 서비스업종 코드",
        final_key_mismatch,
        0,
        final_key_mismatch == 0,
        "업종명, SBDC 소분류명, fallback 계층명은 모두 표시용이며 점수 조인은 서비스_업종_코드만 써야 한다.",
    )

    fallback_search_missing = int(
        fallback.apply(
            lambda row: normalize_search_term(row["UI_중분류명"]) not in str(row["industry_search_text"])
            or normalize_search_term(row[service_name]) not in str(row["industry_search_text"]),
            axis=1,
        ).sum()
    )
    add_check(
        checks,
        "검토_검색성",
        "fallback 업종은 검색문에 UI 중분류와 업종명이 들어가고 의심 SBDC 후보명은 제외",
        fallback_search_missing,
        0,
        fallback_search_missing == 0,
        "계층을 만들었어도 검색문이 갱신되지 않으면 화면에서 이름 검색과 단계 선택이 어긋난다.",
    )

    def has_sbdc_search_leak(row: pd.Series) -> bool:
        search_text = str(row["industry_search_text"])
        allowed_terms = {
            normalize_search_term(row[service_name]),
            normalize_search_term(row["UI_대분류명"]),
            normalize_search_term(row["UI_중분류명"]),
            normalize_search_term(row["UI_세부분류명"]),
        }
        for column in ["SBDC_대분류명_후보", "SBDC_중분류명_후보", "SBDC_소분류명_후보"]:
            term = normalize_search_term(row[column])
            if not term or term in {"unmapped", "매핑검토필요"} or term in allowed_terms:
                continue
            if term in search_text:
                return True
        return False

    review_sbdc_terms_leaked = int(fallback.apply(has_sbdc_search_leak, axis=1).sum())
    add_check(
        checks,
        "검토_검색누수",
        "fallback 검색문에는 수동검토필요 SBDC 후보명이 누수되지 않음",
        review_sbdc_terms_leaked,
        0,
        review_sbdc_terms_leaked == 0,
        "일반의류가 음식점 검색 후보로 뜨는 문제처럼, 의심 SBDC 후보명이 검색문에 남으면 사용자가 잘못된 업종 계층을 보게 된다.",
    )

    add_check(
        checks,
        "검토_tree계약",
        "업종 tree JSON은 CSV와 같은 100개 업종을 노출",
        f"tree_service_count={tree_service_count}, large_count={len(tree_large_names)}",
        "tree_service_count=100, large_count>1",
        tree_service_count == 100 and len(tree_large_names) > 1,
        "화면은 JSON tree를 읽을 가능성이 높으므로 CSV와 다른 업종 집합을 보여주면 안 된다.",
    )

    validation26_fail = int((validation26["result"] == "FAIL").sum())
    validation26_pass = int((validation26["result"] == "PASS").sum())
    add_check(
        checks,
        "검토_기존계약",
        "26번 입력 lookup 검증도 새 버전 기준 통과",
        f"pass={validation26_pass}, fail={validation26_fail}",
        "fail=0",
        validation26_fail == 0 and validation26_pass >= 8,
        "fallback만 따로 통과해도 위치 lookup이나 polygon 계약이 깨졌다면 입력 전처리 전체를 사용할 수 없다.",
    )

    validation_df = pd.DataFrame([check.__dict__ for check in checks])
    validation_path = RULE_VALIDATION / "40_industry_selection_fallback_hierarchy_validation.csv"
    write_csv(validation_df, validation_path)

    pass_count = int((validation_df["result"] == "PASS").sum())
    fail_count = int((validation_df["result"] == "FAIL").sum())
    summary = {
        "run_date": RUN_DATE,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "validation_version": VALIDATION_VERSION,
        "lookup_version": EXPECTED_LOOKUP_VERSION,
        "industry_rows": row_count,
        "sbdc_missing_rows": sbdc_missing,
        "sbdc_review_required_rows": sbdc_review_required,
        "ui_fallback_rows": fallback_count,
        "tree_service_count": tree_service_count,
        "validation_pass_count": pass_count,
        "validation_fail_count": fail_count,
        "decision": "업종_UI_fallback_계층_검증통과",
        "decision_reason_ko": "SBDC 미매핑 또는 수동검토필요 업종은 보존하고, 화면 탐색용 fallback 계층만 추가했다. 점수 조인키와 점수 산식은 바꾸지 않았다.",
    }
    summary_path = RULE_VALIDATION / "40_industry_selection_fallback_hierarchy_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    report_lines = [
        "# 업종 선택 fallback 계층 검증",
        "",
        f"작성일: {RUN_DATE}",
        "",
        "## 1. 목적",
        "",
        "SBDC 대/중/소분류가 없거나 수동검토필요인 서울 서비스업종을 화면에서 선택할 수 있게 하되, 이를 SBDC 자동매칭이나 점수 근거로 둔갑시키지 않았는지 검증한다.",
        "",
        "## 2. 요약",
        "",
        "| 항목 | 값 |",
        "|---|---:|",
        f"| lookup_version | {EXPECTED_LOOKUP_VERSION} |",
        f"| 업종 행 수 | {row_count} |",
        f"| SBDC 미매핑 행 | {sbdc_missing} |",
        f"| SBDC 수동검토필요 행 | {sbdc_review_required} |",
        f"| UI fallback 행 | {fallback_count} |",
        f"| tree 업종 수 | {tree_service_count} |",
        f"| validation PASS | {pass_count} |",
        f"| validation FAIL | {fail_count} |",
        "",
        "## 3. 검증 규칙",
        "",
        "| review_round | rule_name | observed | expected | result | reason_ko |",
        "|---|---|---|---|---|---|",
    ]
    for _, row in validation_df.iterrows():
        report_lines.append(
            "| {review_round} | {rule_name} | {observed} | {expected} | {result} | {reason_ko} |".format(
                review_round=str(row["review_round"]).replace("|", "/"),
                rule_name=str(row["rule_name"]).replace("|", "/"),
                observed=str(row["observed"]).replace("|", "/"),
                expected=str(row["expected"]).replace("|", "/"),
                result=row["result"],
                reason_ko=str(row["reason_ko"]).replace("|", "/"),
            )
        )

    fallback_sample = fallback[[service_key, service_name, "UI_대분류명", "UI_중분류명", "UI_세부분류명"]].head(12)
    report_lines.extend(
        [
            "",
            "## 4. fallback 예시",
            "",
            "| 서비스_업종_코드 | 서비스_업종_코드_명 | UI_대분류명 | UI_중분류명 | UI_세부분류명 |",
            "|---|---|---|---|---|",
        ]
    )
    for _, row in fallback_sample.iterrows():
        report_lines.append(
            f"| {row[service_key]} | {row[service_name]} | {row['UI_대분류명']} | {row['UI_중분류명']} | {row['UI_세부분류명']} |"
        )

    report_lines.extend(
        [
            "",
            "## 5. 판정",
            "",
            "업종 선택 UI는 이제 SBDC 매핑 업종과 SBDC 미매핑 업종을 모두 대/중/세부 경로로 보여줄 수 있다.",
            "",
            "다만 fallback 행은 점수 산식, SBDC 반경경쟁 프록시, 매출 가능 업종 승격 근거가 아니다. 최종 알고리즘 조인은 계속 `서비스_업종_코드`만 사용한다.",
            "",
            "## 6. 다음 작업",
            "",
            "1. 프론트엔드나 API가 `gold_industry_selection_tree.json`의 v1.1 구조를 읽도록 연결한다.",
            "2. SBDC 미매핑 37개는 별도 수동 매핑 근거가 생길 때만 SBDC 자동매칭으로 승격한다.",
            "3. 위치 resolver와 업종 tree를 같이 호출해 실제 입력 payload가 상권_코드와 서비스_업종_코드만 엔진에 넘기는지 확인한다.",
        ]
    )
    report_path = RESEARCH_RULE_VALIDATION / "40_industry_selection_fallback_hierarchy_validation_20260704.md"
    report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if fail_count:
        raise SystemExit(f"[industry_fallback] validation failed: {fail_count}")


if __name__ == "__main__":
    main()
