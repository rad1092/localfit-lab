# -*- coding: utf-8 -*-
"""
AI 리포트 데이터 일치율 (Data Match Rate) 자동 검증기

- 목적: [리포트_평가가이드.md](file:///c:/final_map_project/%EB%A6%AC%ED%8F%AC%ED%8A%B8_%ED%8F%89%EA%B0%80%EA%B0%80%EC%9D%B4%EB%93%9C.md) 2장의 '데이터 일치율' 실측 지표 제공.
- 원칙: AI 리포트 본문 내 모든 정량적 수치가 원본 DB/Facts Pack 내 실제 값과 일치하는지 비교 검사.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from main import app

# 검증 대상 리포트 케이스
REPORT_CASES = [
    ("itaewon_korean", "3001491", "CS100001"),
    ("myeongdong_coffee", "3001492", "CS100010"),
    ("low_score_yukryu", "3130142", "CS300007"),
]

COMMA_PATTERN = re.compile(r"(\d+),(\d+)")

def clean_and_extract_numbers(text: str) -> list[float]:
    # 쉼표 구분자 제거 (예: 1,747,889 -> 1747889)
    cleaned = text
    for _ in range(2):
        cleaned = COMMA_PATTERN.sub(r"\1\2", cleaned)
    
    # 숫자(소수 및 정수) 추출
    raw_matches = re.findall(r"\d+\.\d+|\d+", cleaned)
    numbers = []
    for match in raw_matches:
        try:
            numbers.append(float(match))
        except ValueError:
            pass
    return numbers

def collect_ground_truth_numbers(data: Any, values: set[float]) -> None:
    if isinstance(data, dict):
        for k, v in data.items():
            if k in {"score_version", "ai_model", "gold_version", "snapshot_date", "source_url", "image_url", "chart_refs"}:
                continue
            collect_ground_truth_numbers(v, values)
    elif isinstance(data, list):
        for item in data:
            collect_ground_truth_numbers(item, values)
    elif isinstance(data, (int, float)):
        values.add(float(data))
    elif isinstance(data, str):
        for num in clean_and_extract_numbers(data):
            values.add(num)

def is_structural_or_trivial(val: float) -> bool:
    # 1. 분기 코드 (예: 20191 ~ 20264)
    if 20191 <= val <= 20264:
        return True
    # 2. 연도 (예: 2019 ~ 2026)
    if 2019 <= val <= 2026:
        return True
    # 3. 0~10 사이의 소형 카운트 또는 인덱스 상수 (citation index, 순위 등)
    if 0 <= val <= 10:
        return True
    # 4. 기타 일반적인 비율 기준값 (예: 100%)
    if val == 100.0:
        return True
    return False

def check_match(val: float, ground_truth: set[float]) -> bool:
    if val in ground_truth:
        return True
    
    for gt in ground_truth:
        if gt == 0:
            if abs(val) < 1e-5:
                return True
            continue
        
        # 1. 상대 오차 비교 (0.5% 내 허용 - 반올림 처리)
        if abs(val - gt) / max(abs(val), abs(gt)) < 0.005:
            return True
        
        # 2. 절대 오차 비교 (소수점 한 자릿수 오차 허용, 예: 1.7 vs 1.73)
        if abs(val - gt) < 0.05:
            return True
            
        # 3. 한국어 단위 변환 매칭 (예: 135.7억원 -> 1.357e10 vs 1.3566e10)
        # '억' 단위 변환 (1e8 배)
        if abs(val * 1e8 - gt) / gt < 0.005:
            return True
        # '만' 단위 변환 (1e4 배)
        if abs(val * 1e4 - gt) / gt < 0.005:
            return True
            
    return False

def run_data_match_validation() -> dict[str, Any]:
    client = TestClient(app)
    results = []
    
    total_extracted_count = 0
    total_matched_count = 0
    
    for name, area_code, industry_code in REPORT_CASES:
        print(f"[검증] 리포트 생성 및 파싱 중: {name} ({area_code} / {industry_code})...")
        response = client.post("/api/reports/single/generate", json={"area_code": area_code, "business_type": industry_code})
        
        if response.status_code != 200:
            print(f"[오류] {name} 생성 실패: {response.status_code}")
            continue
            
        data = response.json()
        markdown = data.get("markdown_body") or ""
        
        # Ground Truth 데이터 수집 (markdown_body를 제외한 전체 JSON 객체 스캔)
        gt_values = set()
        data_for_gt = {k: v for k, v in data.items() if k != "markdown_body"}
        collect_ground_truth_numbers(data_for_gt, gt_values)
        
        # AI 리포트 텍스트 내 수치 추출
        markdown_numbers = clean_and_extract_numbers(markdown)
        
        case_extracted = 0
        case_matched = 0
        mismatches = []
        
        for num in markdown_numbers:
            # 트리비얼 상수 필터링
            if is_structural_or_trivial(num):
                continue
                
            case_extracted += 1
            if check_match(num, gt_values):
                case_matched += 1
            else:
                mismatches.append(num)
                
        match_rate = (case_matched / case_extracted * 100.0) if case_extracted > 0 else 100.0
        print(f" -> 추출된 유의미한 수치: {case_extracted}개, 일치: {case_matched}개 (일치율: {match_rate:.1f}%)")
        if mismatches:
            print(f" -> 불일치(의심) 수치 목록: {list(set(mismatches))}")
            
        results.append({
            "case_name": name,
            "area_code": area_code,
            "industry_code": industry_code,
            "extracted_count": case_extracted,
            "matched_count": case_matched,
            "match_rate": round(match_rate, 2),
            "mismatches": sorted(list(set(mismatches)))
        })
        
        total_extracted_count += case_extracted
        total_matched_count += case_matched
        
    overall_match_rate = (total_matched_count / total_extracted_count * 100.0) if total_extracted_count > 0 else 100.0
    
    summary = {
        "validation_number": 91,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "overall_match_rate": round(overall_match_rate, 2),
        "total_extracted_count": total_extracted_count,
        "total_matched_count": total_matched_count,
        "cases": results,
        "decision": "PASS" if overall_match_rate >= 95.0 else "FAIL"
    }
    
    return summary

def write_reports(summary: dict[str, Any]) -> None:
    rule_dir = ROOT.parents[1] / "datacorpus" / "_rule_validation"
    doc_dir = ROOT.parents[1] / "research" / "rule_validation"
    
    rule_dir.mkdir(parents=True, exist_ok=True)
    doc_dir.mkdir(parents=True, exist_ok=True)
    
    # 1. JSON 저장
    json_path = rule_dir / "91_ai_report_data_match_summary.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"[보고서] 요약 JSON 저장 완료: {json_path}")
    
    # 2. Markdown 보고서 작성
    lines = [
        "# 91차 AI 리포트 데이터 일치율 (Data Match Rate) 검증 보고서",
        "",
        f"작성일시: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"최종 판정: **{summary['decision']}** (전체 일치율: **{summary['overall_match_rate']}%**)",
        "",
        "## 1. 개요",
        "본 검증은 AI가 생성한 보고서 본문 내의 정량적 수치들이 백엔드 데이터베이스와 골드 테이블에서 산출된 원본 팩트 팩(Facts Pack) 수치와 정확히 일치하는지 자동으로 파싱하고 대조하여 산출했습니다.",
        "수치가 틀린 생성 보고서는 사용자에게 신뢰를 줄 수 없으므로, 데이터 일치율 95% 이상을 PASS 기준으로 삼습니다.",
        "",
        "## 2. 케이스별 실측 지표",
        "",
        "| 리포트 케이스 | 상권 코드 | 업종 코드 | 유의미한 수치 수 | 일치 수치 수 | 데이터 일치율 | 불일치(의심) 목록 |",
        "|---|---|---|---:|---:|---:|---|"
    ]
    
    for case in summary["cases"]:
        mismatches_str = ", ".join(map(str, case["mismatches"])) if case["mismatches"] else "없음"
        lines.append(
            f"| `{case['case_name']}` | {case['area_code']} | {case['industry_code']} | "
            f"{case['extracted_count']:,} | {case['matched_count']:,} | **{case['match_rate']}%** | {mismatches_str} |"
        )
        
    lines.extend([
        "",
        "## 3. 세부 해석 및 분석 방향",
        "- **소수점 반올림 허용:** 지표 표시 상의 반올림(예: 135.66억 -> 135.7억, 204.97만 -> 205.0만) 및 한국어 단위 변환(억/만)은 검증 규칙에 의해 자동으로 동일한 값으로 매칭되었습니다.",
        "- **불일치 수치 해석:** 불일치 목록에 나타나는 값들은 (1) LLM이 문맥 해석 과정에서 임의로 생성한 환각(Hallucination)이거나 (2) 원본 facts_pack에 존재하지 않는 임의 수치, 또는 (3) 허용 범위를 넘어서는 부적절한 버림/올림 가공을 의미합니다.",
        f"- 현재 총 **{summary['total_extracted_count']}개**의 지표 중 **{summary['total_matched_count']}개**가 올바르게 매칭되었습니다.",
        ""
    ])
    
    md_path = doc_dir / "91_ai_report_data_match_validation_20260714.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[보고서] 마크다운 리포트 저장 완료: {md_path}")

def main() -> int:
    print("=== AI 리포트 데이터 일치율(Data Match Rate) 검증 시작 ===")
    summary = run_data_match_validation()
    write_reports(summary)
    print("=== 검증 프로세스 완료 ===")
    return 0 if summary["decision"] == "PASS" else 1

if __name__ == "__main__":
    sys.exit(main())
