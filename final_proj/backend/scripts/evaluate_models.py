import sys
import os
import json
import time
from unittest.mock import patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langchain_openai import ChatOpenAI as OriginalChatOpenAI
from langchain_core.prompts import ChatPromptTemplate

from app.database import SessionLocal
from app.models.commercial_area import CommercialArea
from app.ai.agent import analyze_area_fit

# Import common functions from evaluate_chatbot
from scripts.evaluate_chatbot import (
    load_test_cases, 
    get_db_stats, 
    check_numerical_hallucination, 
    rule_based_checks, 
    EvalScores
)

DEFAULT_OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")

MODELS = [
    {"name": DEFAULT_OPENAI_MODEL, "model": DEFAULT_OPENAI_MODEL},
]

def get_mocked_chat_openai(model_name):
    def MockedChatOpenAI(*args, **kwargs):
        kwargs['model'] = model_name
        return OriginalChatOpenAI(*args, **kwargs)
    return MockedChatOpenAI

def run_model_evaluation():
    db = SessionLocal()
    test_cases = load_test_cases()
    
    llm_judge = OriginalChatOpenAI(model=DEFAULT_OPENAI_MODEL, temperature=0)
    structured_llm = llm_judge.with_structured_output(EvalScores)
    
    judge_prompt = ChatPromptTemplate.from_messages([
        ("system", """당신은 '상권 분석 및 창업 의사결정 지원 AI'를 평가하는 전문 심사위원입니다.
정답을 맞추는지가 아니라 "사용자의 창업 의사결정을 얼마나 잘 지원했는가"를 평가합니다.

[평가 기준]
① Data Accuracy: DB 데이터를 정확하게 사용했는가 (5점: 환각 없음, 1점: 없는 데이터 생성)
② User Fit ⭐: 
  - 5점: 실제 UI에서 입력된 예산/업종 등 사용자 조건을 반영하고, 현실적 전략과 대안을 제시함. 추천 이유가 입력된 조건과 명확하게 연결되어야 함. 입력되지 않은 경험·운영방식·우선순위를 요구하거나 추정하면 안 됨.
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
점수를 줄 때는 사용자 조건(User Context), DB Ground Truth, AI 응답 이 세 가지를 반드시 교차 비교하여 평가하십시오.
사용자 조건만 보고 평가하거나 AI 응답만 보고 평가하지 마십시오.
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
    
    chain = judge_prompt | structured_llm
    
    results = []
    
    print("==================================================")
    print("다중 모델 성능 비교 평가 프레임워크 가동 중...")
    print("==================================================")
    
    for model_info in MODELS:
        model_name = model_info["name"]
        print(f"\n▶ 평가 시작: {model_name} ◀")
        
        total_auto_pass = 0
        total_auto_tests = 0
        latencies = []
        
        cat_scores = {
            "Data Accuracy": [], "User Fit": [], "Business Logic": [],
            "Groundedness": [], "Rent Trend Awareness": [], "Recommendation Quality": [],
            "Actionability": [], "Risk Awareness": [], "Decision Support": []
        }
        
        test_case_reports = []
        
        for i, condition in enumerate(test_cases):
            print(f"  [Test Case {i+1}] {condition.area_name} / {condition.business_type} / 예산 {condition.budget}만")
            
            area = db.query(CommercialArea).filter(CommercialArea.area_name.like(f"%{condition.area_name}%")).first()
            if not area:
                print(f"    ❌ DB에 '{condition.area_name}' 상권이 없습니다. 스킵.")
                continue
                
            db_data = get_db_stats(area)
            
            print(f"    - {model_name} 응답 생성 중...")
            
            start_time = time.time()
            with patch('app.ai.agent.ChatOpenAI', new=get_mocked_chat_openai(model_info['model'])), \
                 patch('app.ai.recursive_layer.ChatOpenAI', new=get_mocked_chat_openai(model_info['model'])):
                ai_result = analyze_area_fit(db_data, condition)
            end_time = time.time()
            
            latency = end_time - start_time
            latencies.append(latency)
            
            ai_text = json.dumps(ai_result.model_dump(), ensure_ascii=False, indent=2)
            
            has_hallucination, _ = check_numerical_hallucination(ai_text, db_data, condition.budget)
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
                    
            print(f"    - LLM Judge 평가 중...")
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
            
            test_case_reports.append(f"""
#### [Test Case {i+1}] {condition.area_name} ({latency:.2f}s)
- **Score:** {weighted_score:.2f} / 5.0
- **Decision Support:** {eval_res.decision_support} / 5.0
  - Reason: {eval_res.decision_support_reason}
  - Evidence: {', '.join(eval_res.decision_support_evidence)}
""")
            print(f"    - 평가 완료: {weighted_score:.2f}점 ({latency:.2f}s)")
            
        avg_latency = sum(latencies) / len(latencies) if latencies else 0
        auto_pass_rate = total_auto_pass / total_auto_tests * 100 if total_auto_tests > 0 else 0
        
        avg_cat_scores = {k: sum(v)/len(v) if v else 0 for k, v in cat_scores.items()}
        
        weighted_overall = (
            avg_cat_scores["Data Accuracy"] + avg_cat_scores["User Fit"] + avg_cat_scores["Business Logic"] +
            avg_cat_scores["Groundedness"] + avg_cat_scores["Rent Trend Awareness"] + 
            avg_cat_scores["Recommendation Quality"] + avg_cat_scores["Actionability"] + 
            avg_cat_scores["Risk Awareness"] + (avg_cat_scores["Decision Support"] * 3)
        ) / 11
        
        results.append({
            "model_name": model_name,
            "overall_score": weighted_overall,
            "avg_latency": avg_latency,
            "auto_pass_rate": auto_pass_rate,
            "avg_cat_scores": avg_cat_scores,
            "reports": test_case_reports
        })
        
    db.close()
    
    print("\n==================================================")
    print("비교 평가 리포트 생성 중 (model_comparison_report.md)")
    print("==================================================")
    
    with open("model_comparison_report.md", "w", encoding="utf-8") as f:
        f.write("# Model Performance Evaluation Report\n\n")
        f.write("## Summary\n\n")
        f.write("| Model | Overall Score | Decision Support | User Fit | Data Accuracy | Auto PASS RATE | Avg Latency |\n")
        f.write("|---|---:|---:|---:|---:|---:|---:|\n")
        
        for r in results:
            m = r["model_name"]
            overall = f"{r['overall_score']:.2f}"
            ds = f"{r['avg_cat_scores']['Decision Support']:.2f}"
            uf = f"{r['avg_cat_scores']['User Fit']:.2f}"
            da = f"{r['avg_cat_scores']['Data Accuracy']:.2f}"
            ap = f"{r['auto_pass_rate']:.1f}%"
            lat = f"{r['avg_latency']:.2f}s"
            f.write(f"| {m} | {overall} | {ds} | {uf} | {da} | {ap} | {lat} |\n")
            
        f.write("\n## Model Details\n\n")
        for r in results:
            f.write(f"### {r['model_name']}\n\n")
            f.write(f"- **Overall Score:** {r['overall_score']:.2f} / 5.0\n")
            f.write(f"- **Avg Latency:** {r['avg_latency']:.2f}s\n")
            f.write(f"- **Auto Score PASS RATE:** {r['auto_pass_rate']:.1f}%\n\n")
            
            f.write("#### Category Averages\n")
            for k, v in r['avg_cat_scores'].items():
                f.write(f"- {k}: {v:.2f} / 5.0\n")
            
            f.write("\n#### Test Cases\n")
            for rep in r['reports']:
                f.write(rep)
            f.write("\n---\n\n")
            
    print("모든 평가가 완료되었습니다. model_comparison_report.md를 확인하세요.")

if __name__ == "__main__":
    run_model_evaluation()
