import sys
import os
import json
import re
import math
from pydantic import BaseModel, Field
from typing import List
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models.commercial_area import CommercialArea
from app.schemas.commercial_area import UserBusinessCondition
from app.ai.agent import analyze_area_fit
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from dotenv import load_dotenv

load_dotenv()

DEFAULT_OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")

class EvalScores(BaseModel):
    data_accuracy: float = Field(ge=1, le=5)
    data_accuracy_reason: str = Field(description="어떤 DB 데이터 때문에 이 점수를 주었는지 구체적인 근거")
    data_accuracy_evidence: List[str] = Field(description="점수의 근거가 된 데이터 항목 목록 (예: ['floating_population'])")
    
    user_fit: float = Field(ge=1, le=5)
    user_fit_reason: str = Field(description="어떤 사용자 조건 때문에 이 점수를 주었는지 구체적인 근거")
    user_fit_evidence: List[str] = Field(description="점수의 근거가 된 데이터 항목 목록 (예: ['budget', 'business_type'])")
    
    business_logic: float = Field(ge=1, le=5)
    business_logic_reason: str = Field(description="어떤 데이터와 논리 흐름 때문에 이 점수를 주었는지 구체적인 근거")
    business_logic_evidence: List[str] = Field(description="점수의 근거가 된 데이터 항목 목록")
    
    groundedness: float = Field(ge=1, le=5)
    groundedness_reason: str = Field(description="어떤 데이터/문장 때문에 이 점수를 주었는지 구체적인 근거")
    groundedness_evidence: List[str] = Field(description="점수의 근거가 된 데이터 항목 목록")
    
    rent_trend_awareness: float = Field(ge=1, le=5)
    rent_trend_awareness_reason: str = Field(description="임대동향 해석에 대한 구체적인 근거")
    rent_trend_awareness_evidence: List[str] = Field(description="점수의 근거가 된 데이터 항목 목록")
    
    recommendation_quality: float = Field(ge=1, le=5)
    recommendation_quality_reason: str = Field(description="어떤 대안 상권과 리스크 설명 때문에 이 점수를 주었는지 구체적인 근거")
    recommendation_quality_evidence: List[str] = Field(description="점수의 근거가 된 데이터 항목 목록")
    
    actionability: float = Field(ge=1, le=5)
    actionability_reason: str = Field(description="어떤 행동 제안 때문에 이 점수를 주었는지 구체적인 근거")
    actionability_evidence: List[str] = Field(description="점수의 근거가 된 데이터 항목 목록")
    
    risk_awareness: float = Field(ge=1, le=5)
    risk_awareness_reason: str = Field(description="어떤 리스크 설명 때문에 이 점수를 주었는지 구체적인 근거")
    risk_awareness_evidence: List[str] = Field(description="점수의 근거가 된 데이터 항목 목록")
    
    decision_support: float = Field(ge=1, le=5)
    decision_support_reason: str = Field(description="의사결정에 도움이 되는 구체적인 근거")
    decision_support_evidence: List[str] = Field(description="점수의 근거가 된 데이터 항목 목록")

def check_numerical_hallucination(ai_text: str, db_data: dict, budget: int):
    # Python-based numerical hallucination check
    import re
    
    # Extract numbers with optional '만', '억'
    matches = re.finditer(r'([0-9,]+(?:\.[0-9]+)?)\s*(만|억)?', ai_text)
    
    db_values = [float(v) for v in db_data.values() if isinstance(v, (int, float))]
    # Add budget to valid values to prevent flagging user budget
    db_values.append(float(budget))
    db_values.append(float(budget) * 10000)
    
    hallucinations = []
    
    for match in matches:
        num_str = match.group(1).replace(',', '')
        unit = match.group(2)
        
        try:
            num = float(num_str)
            if unit == '만':
                num *= 10000
            elif unit == '억':
                num *= 100000000
                
            # Ignore small numbers, years, percentages, or UI-related numbers
            if num <= 100 or (2000 <= num <= 2100 and unit is None):
                continue
                
            matched = False
            for db_val in db_values:
                if db_val == 0:
                    continue
                # Allow 2% margin
                if abs(num - db_val) / db_val <= 0.02:
                    matched = True
                    break
                    
            if not matched:
                hallucinations.append(f"{match.group(0)} (parsed: {num})")
                
        except ValueError:
            continue
            
    if hallucinations:
        hallucinations = list(set(hallucinations))
        return True, f"DB에 없는 숫자 발견: {', '.join(hallucinations)}"
    return False, ""

def rule_based_checks(ai_text: str):
    refusal_keywords = [
        "답변할 수 없습니다", "분석할 수 없습니다", "제공할 수 없습니다", 
        "판단할 수 없습니다", "알 수 없습니다", "정보가 부족합니다", "지원하지 않습니다"
    ]
    has_refusal = any(kw in ai_text for kw in refusal_keywords)
    # Exception for valid data missing explanation
    if "현재 데이터만으로는 판단하기 어렵습니다" in ai_text:
        has_refusal = False
    
    success_keywords = [
        "반드시 성공", "무조건 성공", "창업하면 성공", "100% 성공", 
        "실패하지 않습니다", "확실히 성공", "성공 가능성이 매우 높습니다", 
        "안정적인 수익을 기대할 수 있습니다", "충분히 성공할 것으로 보입니다", "강력히 추천합니다"
    ]
    has_success_guarantee = any(kw in ai_text for kw in success_keywords)
    
    prob_keywords = ["리스크가 존재합니다", "검토할 가치가 있습니다", "예상됩니다", "가능성이 있습니다", "어려울 수 있습니다", "위험이 있습니다", "주의가 필요합니다"]
    has_probabilistic_expression = any(kw in ai_text for kw in prob_keywords)
    
    return {
        "has_refusal": has_refusal,
        "has_success_guarantee": has_success_guarantee,
        "has_probabilistic_expression": has_probabilistic_expression
    }

def get_db_stats(area: CommercialArea):
    store_count = sum([s.store_count for s in area.district_store_counts]) if area.district_store_counts else 0
    floating_pop = sum([p.floating_population for p in area.district_floatings]) if area.district_floatings else 0
    sale_values = [
        row.sale_price_proxy_manwon_per_m2
        for row in area.sale_price_proxies
        if row.sale_price_proxy_manwon_per_m2 is not None
    ]
    sale_price_proxy = sum(sale_values) / len(sale_values) if sale_values else None
    total_sales = sum([s.sales_amount for s in area.district_sales]) if area.district_sales else 0.0
    return {
        "store_count": store_count,
        "floating_population": floating_pop,
        "sale_price_proxy_manwon_per_m2": sale_price_proxy,
        "total_sales": total_sales
    }

def load_test_cases():
    dataset_path = os.path.join(os.path.dirname(__file__), "evaluation_dataset.json")
    if os.path.exists(dataset_path):
        with open(dataset_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return [UserBusinessCondition(**item) for item in data]
    else:
        return [
            UserBusinessCondition(
                area_name="명동", business_type="카페", budget=10000
            )
        ]

def run_evaluation():
    db = SessionLocal()
    test_cases = load_test_cases()
    
    llm_judge = ChatOpenAI(model=DEFAULT_OPENAI_MODEL, temperature=0)
    structured_llm = llm_judge.with_structured_output(EvalScores)
    
    judge_prompt = ChatPromptTemplate.from_messages([
        ("system", """당신은 '상권 분석 및 창업 의사결정 지원 AI'를 평가하는 전문 심사위원입니다.
정답을 맞추는지가 아니라 "사용자의 창업 의사결정을 얼마나 잘 지원했는가"를 평가합니다.

[평가 기준]
① Data Accuracy: DB 데이터를 정확하게 사용했는가 (5점: 환각 없음, 1점: 없는 데이터 생성)
② User Fit ⭐: 
  - 5점: 실제 입력된 예산/업종을 반영하고 현실적 전략과 대안을 제시. 추천 이유가 반드시 사용자 조건과 명확하게 연결되어야 함.
  - 3점: 일부 조건만 반영, 추천 이유 연결성 약함.
  - 1점: 사용자 조건 대부분 무시, 비현실적 제안, 일반적 답변.
③ Business Logic: 분석 논리가 자연스러운가 (5점: 데이터->원인->결론 흐름 자연스러움. 1점: 논리 비약)
④ Groundedness: 데이터를 근거로 설명했는가 (5점: 모든 판단에 데이터 근거. 1점: 근거 없는 주장)
⑤ Rent Trend Awareness: 임대동향 해석 (5점: 임대동향 활용, 예산과 연결. 1점: 월세 임의 생성)
⑥ Recommendation Quality: 추천 품질 (5점: 대안상권 추천, 리스크 포함. 1점: 현재 상권만 반복)
⑦ Actionability: 실행 가능성 (5점: 행동 가능한 제안. 1점: 제안 없음)
⑧ Risk Awareness: 리스크 인식 (5점: 경쟁과 비용 리스크 충분히 설명. 1점: 장점만 설명)
⑨ Decision Support ⭐⭐⭐: 의사결정에 실제 도움이 되는가 (5점: 장점/리스크 균형, 행동 제안. 1점: 근거 없는 추천)

[Reason 작성 규칙 - 매우 중요]
- 각 Reason은 어떤 사용자 조건(예: 예산, 업종) 때문인지 또는 어떤 DB 데이터(예: 유동인구, 임대료) 때문인지 점수의 근거를 반드시 명시해야 합니다.
- 추상적인 표현("전반적으로 좋습니다", "적절합니다", "조건을 잘 반영했습니다", "분석이 우수합니다")은 절대 사용하지 마십시오.
- 예시: "입력 예산을 고려해 소규모 매장을 추천했지만 임대동향과의 연결 설명이 부족하여 4점을 부여했습니다."

[Evidence 반환 규칙]
- 각 항목별로 점수의 근거가 된 데이터/조건의 식별자(목록)를 Evidence 배열로 함께 반환하세요.
- 예: ["budget", "business_type", "floating_population"]

[특별 규칙]
- 점포별 임대료를 임의 생성하면 FAIL (0점 처리 가능)
- 없는 데이터를 추측하면 감점

[평가 시 주의사항 - 매우 중요]
점수를 줄 때는
사용자 조건(User Context)
DB Ground Truth
AI 응답
이 세 가지를 반드시 교차 비교하여 평가하십시오.
사용자 조건만 보고 평가하거나
AI 응답만 보고 평가하지 마십시오.
"""),
        ("user", """
[사용자 조건]
{condition}

[DB Ground Truth]
{db_data}

[AI 응답]
{ai_response}

위 항목을 바탕으로 9개 기준에 대해 점수와 이유, 그리고 Evidence(근거 항목 리스트)를 반환해주세요.
""")
    ])
    
    print("==================================================")
    print("의사결정 지원 AI 평가 프레임워크 가동 중...")
    print("==================================================")
    
    total_auto_pass = 0
    total_auto_tests = 0
    
    cat_scores = {
        "Data Accuracy": [], "User Fit": [], "Business Logic": [],
        "Groundedness": [], "Rent Trend Awareness": [], "Recommendation Quality": [],
        "Actionability": [], "Risk Awareness": [], "Decision Support": []
    }
    
    final_justifications = []
    
    for i, condition in enumerate(test_cases):
        print(f"\n[Test Case {i+1}] {condition.area_name} / {condition.business_type} / 예산 {condition.budget}만")
        
        area = db.query(CommercialArea).filter(CommercialArea.area_name.like(f"%{condition.area_name}%")).first()
        if not area:
            print(f"  ❌ DB에 '{condition.area_name}' 상권이 없습니다. 스킵.")
            continue
            
        db_data = get_db_stats(area)
        
        print("  - AI 분석 중...")
        ai_result = analyze_area_fit(db_data, condition)
        ai_text = json.dumps(ai_result.model_dump(), ensure_ascii=False, indent=2)
        
        print("  - 자동 채점(규칙+Python 검증) 진행 중...")
        has_hallucination, hallucination_details = check_numerical_hallucination(ai_text, db_data, condition.budget)
        rule_res = rule_based_checks(ai_text)
        
        checks = {
            "No Hallucination (Python)": not has_hallucination,
            "No Refusal (Rule)": not rule_res["has_refusal"],
            "No Success Guarantee (Rule)": not rule_res["has_success_guarantee"],
            "Has Probabilistic Expr (Rule)": rule_res["has_probabilistic_expression"]
        }
        
        for k, v in checks.items():
            total_auto_tests += 1
            if v:
                total_auto_pass += 1
                print(f"    ✅ {k} (PASS)")
            else:
                details = f" - {hallucination_details}" if k == "No Hallucination (Python)" and has_hallucination else ""
                print(f"    ❌ {k} (FAIL){details}")
                
        print("  - LLM Judge 평가 중...")
        chain = judge_prompt | structured_llm
        eval_res = chain.invoke({
            "condition": json.dumps(condition.model_dump(), ensure_ascii=False),
            "db_data": json.dumps(db_data, ensure_ascii=False),
            "ai_response": ai_text
        })
        
        cat_scores["Data Accuracy"].append(eval_res.data_accuracy)
        cat_scores["User Fit"].append(eval_res.user_fit)
        cat_scores["Business Logic"].append(eval_res.business_logic)
        cat_scores["Groundedness"].append(eval_res.groundedness)
        cat_scores["Rent Trend Awareness"].append(eval_res.rent_trend_awareness)
        cat_scores["Recommendation Quality"].append(eval_res.recommendation_quality)
        cat_scores["Actionability"].append(eval_res.actionability)
        cat_scores["Risk Awareness"].append(eval_res.risk_awareness)
        cat_scores["Decision Support"].append(eval_res.decision_support)
        
        weighted_score = (
            eval_res.data_accuracy + eval_res.user_fit + eval_res.business_logic +
            eval_res.groundedness + eval_res.rent_trend_awareness + 
            eval_res.recommendation_quality + eval_res.actionability + 
            eval_res.risk_awareness + (eval_res.decision_support * 3)
        ) / 11
        
        test_case_report = f"""
## [Test Case {i+1}] {condition.area_name} / {condition.business_type} / 예산 {condition.budget}만

### Question & Context
- 상권명: {condition.area_name}
- 업종: {condition.business_type}
- 예산: {condition.budget}만 원

### Ground Truth DB Values
```json
{json.dumps(db_data, ensure_ascii=False, indent=2)}
```

### AI Answer
```json
{ai_text}
```

### Auto Score
- No Hallucination (Python): {"PASS" if not has_hallucination else "FAIL"} {hallucination_details if has_hallucination else ""}
- No Refusal: {"PASS" if not rule_res["has_refusal"] else "FAIL"}
- No Success Guarantee: {"PASS" if not rule_res["has_success_guarantee"] else "FAIL"}
- Has Probabilistic Expression: {"PASS" if rule_res["has_probabilistic_expression"] else "FAIL"}

### LLM Judge

#### Data Accuracy
- Score: {eval_res.data_accuracy} / 5.0
- Reason: {eval_res.data_accuracy_reason}
- Evidence: {', '.join(eval_res.data_accuracy_evidence)}

#### User Fit
- Score: {eval_res.user_fit} / 5.0
- Reason: {eval_res.user_fit_reason}
- Evidence: {', '.join(eval_res.user_fit_evidence)}

#### Business Logic
- Score: {eval_res.business_logic} / 5.0
- Reason: {eval_res.business_logic_reason}
- Evidence: {', '.join(eval_res.business_logic_evidence)}

#### Groundedness
- Score: {eval_res.groundedness} / 5.0
- Reason: {eval_res.groundedness_reason}
- Evidence: {', '.join(eval_res.groundedness_evidence)}

#### Rent Trend Awareness
- Score: {eval_res.rent_trend_awareness} / 5.0
- Reason: {eval_res.rent_trend_awareness_reason}
- Evidence: {', '.join(eval_res.rent_trend_awareness_evidence)}

#### Recommendation Quality
- Score: {eval_res.recommendation_quality} / 5.0
- Reason: {eval_res.recommendation_quality_reason}
- Evidence: {', '.join(eval_res.recommendation_quality_evidence)}

#### Actionability
- Score: {eval_res.actionability} / 5.0
- Reason: {eval_res.actionability_reason}
- Evidence: {', '.join(eval_res.actionability_evidence)}

#### Risk Awareness
- Score: {eval_res.risk_awareness} / 5.0
- Reason: {eval_res.risk_awareness_reason}
- Evidence: {', '.join(eval_res.risk_awareness_evidence)}

#### Decision Support
- Score: {eval_res.decision_support} / 5.0
- Reason: {eval_res.decision_support_reason}
- Evidence: {', '.join(eval_res.decision_support_evidence)}

### Decision Support Weighted Score
- **총점:** {weighted_score:.2f} / 5.0

### Reviewer Check
- [ ] LLM Judge 점수에 동의
- [ ] LLM Judge 점수 수정 필요
- 수정 점수: 
- Reviewer Comment: 

---
"""
        final_justifications.append(test_case_report)
        print(f"    평가 완료: {weighted_score:.2f}점")
        
    db.close()
    
    print("\n==================================================")
    print("최종 평가 결과 (Overall Result)")
    print("==================================================")
    
    if total_auto_tests > 0:
        auto_pass_rate = total_auto_pass / total_auto_tests * 100
        print(f"자동채점 (PASS RATE): {auto_pass_rate:.1f}%")
    else:
        auto_pass_rate = 0.0
    
    overall_sum = 0
    print("\n카테고리별 평균 (Category Scores):")
    for k, v in cat_scores.items():
        avg = sum(v) / len(v) if v else 0
        overall_sum += avg
        print(f"  {k}: {avg:.1f} / 5.0")
        
    final_score = overall_sum / len(cat_scores) if cat_scores else 0
    print(f"\nOverall Score: {final_score:.2f} / 5.0")
    
    rating = 'Need Improvement'
    if final_score >= 4.5: rating = 'Excellent'
    elif final_score >= 4.0: rating = 'Good'
    elif final_score >= 3.5: rating = 'Acceptable'
    print(f"Rating: {rating}")
        
    print("\n최종 의견 및 상세 리포트가 evaluation_report.md 에 저장되었습니다.")
    
    with open("evaluation_report.md", "w", encoding="utf-8") as f:
        f.write("# 의사결정 지원 AI 평가 리포트\n\n")
        f.write("## Overall Result\n")
        f.write(f"- Overall Score: **{final_score:.2f} / 5.0**\n")
        f.write(f"- Auto Score PASS RATE: **{auto_pass_rate:.1f}%**\n")
        f.write(f"- Rating: **{rating}**\n\n")
        
        f.write("### Category Average Scores\n")
        for k, v in cat_scores.items():
            avg = sum(v) / len(v) if v else 0
            f.write(f"- {k}: {avg:.1f} / 5.0\n")
            
        f.write("\n---\n\n")
        f.write("## Test Case\n\n")
        
        for j in final_justifications:
            f.write(j)
            
    print("==================================================")

if __name__ == "__main__":
    run_evaluation()
