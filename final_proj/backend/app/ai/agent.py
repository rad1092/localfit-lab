import os
from pathlib import Path
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field
from typing import List

class AIAnalysisOutput(BaseModel):
    summary: str = Field(description="Executive summary of the commercial area")
    strengths: List[str] = Field(description="List of strengths of the commercial area")
    weaknesses: List[str] = Field(description="List of weaknesses of the commercial area")
    recommended_businesses: List[str] = Field(description="Calibrated recommended categories; empty when no cross-industry model is supplied")
    risk_factors: List[str] = Field(description="List of risk factors")
    opportunity_score: float = Field(description="Predicted opportunity score from 0 to 100")

class AreaSWOT(BaseModel):
    area_name: str = Field(description="Name of the commercial area")
    pros: List[str] = Field(description="List of strengths/pros (장점) for this area")
    cons: List[str] = Field(description="List of weaknesses/cons (단점) for this area")

class AIComparisonOutput(BaseModel):
    summary: str = Field(description="비교 총평 (Overall comparison summary in Korean)")
    top_recommendation_name: str = Field(description="수요·접근성 맥락에서 먼저 비교할 후보 이름; 공식 추천 아님")
    top_recommendation_reason: str = Field(description="2축 맥락 우선 후보로 표시한 이유와 공식 추천이 아니라는 한계")
    swot_analysis: List[AreaSWOT] = Field(description="각 상권별 장단점 비교 (Pros and Cons for each area)")

from app.ai.recursive_layer import run_recursive_analysis

DEFAULT_OPENAI_MODEL = "gpt-5.4-mini"
BACKEND_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"


def get_openai_model() -> str:
    from dotenv import load_dotenv
    load_dotenv(BACKEND_ENV_PATH)
    return os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)


def analyze_commercial_area_base(area_data: dict, feedback: str = "") -> AIAnalysisOutput:
    openai_key = os.getenv("OPENAI_API_KEY")
    if not openai_key:
        from dotenv import load_dotenv
        load_dotenv(BACKEND_ENV_PATH)
        openai_key = os.getenv("OPENAI_API_KEY")
        
    llm = ChatOpenAI(model=get_openai_model(), temperature=0.2, openai_api_key=openai_key)
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", """You are a senior Commercial Area Analyst. 
        Your role is to analyze commercial districts based on data and provide structured insights for entrepreneurs.
        Focus on business location analysis. Do not infer recommended industries from within-industry percentile scores.
        Return an empty recommended_businesses list unless calibrated cross-industry evidence is explicitly supplied.
        """),
        ("user", """
        다음의 상권 데이터를 분석해주세요:
        상권 코드: {commercial_area_code}
        상권명: {commercial_area_name}
        총 점포수: {store_count}
        총 유동인구: {floating_population}
        RTMS 상업용 부동산 매매가 프록시: {sale_price_proxy_manwon_per_m2}만원/㎡
        총 매출액: {total_sales}
        총 폐업수: {close_count}
        
        이러한 유동인구, 매출액, 점포 밀도를 위치 맥락으로만 설명하고 업종 간 순위를 새로 만들지 마세요. 한국어로 작성해주세요.
        """)
    ])
    
    
    # If feedback is provided from the validation loop, incorporate it
    if feedback:
        prompt.append(("user", f"Previous attempt failed validation. Please refine based on this feedback:\n{feedback}"))
        
    chain = prompt | llm.with_structured_output(AIAnalysisOutput)
    
    response = chain.invoke(area_data)
    return response

def analyze_commercial_area(area_data: dict) -> AIAnalysisOutput:
    return run_recursive_analysis(area_data, analyze_commercial_area_base, feedback_aware=True)

def analyze_comparison_base(areas_data: list[dict], feedback: str = "") -> AIComparisonOutput:
    openai_key = os.getenv("OPENAI_API_KEY")
    if not openai_key:
        from dotenv import load_dotenv
        load_dotenv(BACKEND_ENV_PATH)
        openai_key = os.getenv("OPENAI_API_KEY")
        
    llm = ChatOpenAI(model=get_openai_model(), temperature=0.2, openai_api_key=openai_key)
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", """당신은 서울시 상권 분석을 전문으로 하는 최고 수준의 상권 분석 AI 에이전트입니다.
        여러 상권의 데이터를 바탕으로 객관적인 장단점(SWOT)을 비교 분석하세요. 수요·접근성 2축만 있으면 먼저 비교할 후보는 표시할 수 있지만 공식 업종 추천이나 4축 종합 1위로 단정하면 안 됩니다.
        """),
        ("user", """
        다음은 비교할 상권들의 데이터 목록입니다:
        {areas_data_str}
        
        이 데이터를 바탕으로 다음을 작성해주세요:
        1. 비교 총평 (summary): 주어진 상권들을 간략히 비교하여 평가한 종합 의견.
        2. 수요·접근성 맥락 우선 후보 (top_recommendation_name): 2축 맥락에서 먼저 비교할 상권 이름. 공식 추천이 아님을 전제로 합니다.
        3. 우선 비교 이유 (top_recommendation_reason): 해당 후보가 2축 맥락에서 앞선 근거와 결측된 매출·경쟁 축의 한계.
        4. 장단점 분석 (swot_analysis): 주어진 '모든' 상권에 대해 각각 최소 2개 이상의 장점(Pros)과 단점(Cons)을 도출.
        """)
    ])
    
    if feedback:
        prompt.append(("user", f"Previous attempt failed validation. Please refine based on this feedback:\n{feedback}"))
        
    chain = prompt | llm.with_structured_output(AIComparisonOutput)
    
    # Format the input data cleanly for the prompt
    areas_str = ""
    for d in areas_data:
        areas_str += f"\n- 상권명: {d.get('area_name')}\n"
        areas_str += f"  점포수: {d.get('store_count', 0)}\n"
        areas_str += f"  유동인구: {d.get('floating_population', 0)}\n"
        areas_str += f"  RTMS 매매가 프록시: {d.get('sale_price_proxy_manwon_per_m2')}만원/㎡ (임대료 아님)\n"
        areas_str += f"  총 매출액: {d.get('total_sales', 0.0)}\n"
        
    response = chain.invoke({"areas_data_str": areas_str})
    return response

def analyze_comparison(areas_data: list[dict]) -> AIComparisonOutput:
    # Package multiple areas as a single raw_data dict for validation
    raw_wrapper = {"comparison_areas": areas_data}
    
    # Custom wrapper to unwrap for base generator
    def generator_wrapper(valid_data, feedback=""):
        return analyze_comparison_base(valid_data.get("comparison_areas", []), feedback=feedback)
        
    return run_recursive_analysis(raw_wrapper, generator_wrapper, feedback_aware=True)

class RecommendedQuestion(BaseModel):
    question: str = Field(description="후속 질문 텍스트 (예: '예산을 5000만원 늘리면 어떨까요?')")
    area_name: str = Field(description="이 질문에 대한 후속 검색용 상권명")
    business_type: str = Field(description="이 질문에 대한 후속 검색용 업종명")
    budget: int = Field(description="이 질문에 대한 후속 검색용 예산")

class ChatbotAIOutput(BaseModel):
    condition_summary: str = Field(description="입력 조건 요약 (1문장)")
    quick_judgement: str = Field(description="현재 상권의 간단 판단 (1문장)")
    main_risks: List[str] = Field(description="핵심 리스크 1~2개")
    recommended_strategy: List[str] = Field(description="상세 리포트용 추천 전략 2~3개")

def analyze_area_fit_base(area_data: dict, feedback: str = "") -> ChatbotAIOutput:
    openai_key = os.getenv("OPENAI_API_KEY")
    if not openai_key:
        from dotenv import load_dotenv
        load_dotenv(BACKEND_ENV_PATH)
        openai_key = os.getenv("OPENAI_API_KEY")
        
    llm = ChatOpenAI(model=get_openai_model(), temperature=0.2, openai_api_key=openai_key)
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", """당신은 창업 준비생들에게 예산, 상권, 업종 데이터를 분석해주는 냉철한 AI 창업 컨설턴트입니다.
        제공받은 상권 데이터와 사용자의 창업 조건을 비교하여 상권 적합도를 판단하세요.
        
        [중요 지침]
        1. 절대적 상권 평가가 아닌, 실제 입력된 사용자 조건에 맞춘 평가여야 합니다.
        2. 성공을 보장하거나 단정짓는 표현은 금지합니다.
        3. condition_summary, quick_judgement, main_risks는 챗봇용으로 간결하게 작성하세요.
        4. recommended_strategy는 상세 리포트용으로 깊이 있게 작성하세요.
        """),
        ("user", """
        [고객 입력 정보]
        - 희망 창업 상권명: {commercial_area_name}
        - 희망 창업 업종: {business_type}
        - 가용 예산: {budget}만원
        
        [상권 데이터 요약]
        - 점포 수: {store_count}개
        - 유동인구: {floating_population}명
        - RTMS 상업용 부동산 매매가 프록시: {sale_price_proxy_manwon_per_m2}만원/㎡ (임대료 아님)
        - 총 매출액: {total_sales}원
        
        이 데이터를 바탕으로 사용자 맞춤 창업 상담 결과를 도출해주세요.
        """)
    ])
    
    if feedback:
        prompt.append(("user", f"Previous attempt failed validation. Please refine based on this feedback:\n{feedback}"))
        
    chain = prompt | llm.with_structured_output(ChatbotAIOutput)
    
    response = chain.invoke({
        "commercial_area_name": area_data.get("commercial_area_name", "알 수 없음"),
        "business_type": area_data.get("_meta_business_type", "알 수 없음"),
        "budget": area_data.get("_meta_budget", 0),
        "store_count": area_data.get("store_count", 0),
        "floating_population": area_data.get("floating_population", 0),
        "sale_price_proxy_manwon_per_m2": area_data.get("sale_price_proxy_manwon_per_m2"),
        "total_sales": area_data.get("total_sales", 0.0)
    })
    return response

def analyze_area_fit(area_data: dict, condition) -> ChatbotAIOutput:
    # Inject metadata for validation loop tracking
    raw_data = dict(area_data)
    raw_data["_meta_business_type"] = condition.business_type
    raw_data["_meta_budget"] = condition.budget
    
    return run_recursive_analysis(raw_data, analyze_area_fit_base, feedback_aware=True)
