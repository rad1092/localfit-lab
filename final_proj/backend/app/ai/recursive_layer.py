import os
import json
import contextvars
import contextlib
from pathlib import Path
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.callbacks import BaseCallbackHandler
from pydantic import BaseModel, Field

class ReportValidationResult(BaseModel):
    is_valid: bool = Field(description="True if the report is strictly grounded in data without hallucinations")
    hallucinations: list[str] = Field(description="List of hallucinated or contradictory statements")

DEFAULT_OPENAI_MODEL = "gpt-5.4-mini"
BACKEND_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"

# Text-token prices per 1M tokens.  Keep aliases and dated snapshots together
# so usage reported by the provider is priced with the same family as the
# configured model.
MODEL_TOKEN_PRICING_PER_MILLION: tuple[tuple[str, tuple[float, float]], ...] = (
    ("gpt-5.4-mini", (0.75, 4.50)),
    ("gpt-4o-mini", (0.15, 0.60)),
    ("gpt-4o", (2.50, 10.00)),
)

class TokenUsageCallbackHandler(BaseCallbackHandler):
    def __init__(self):
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.model_name = ""

    def on_llm_end(self, response, **kwargs):
        llm_output = response.llm_output or {}
        usage = llm_output.get("token_usage") or {}
        prompt_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        completion_tokens = int(
            usage.get("completion_tokens") or usage.get("output_tokens") or 0
        )
        total_tokens = int(usage.get("total_tokens") or 0)

        # Chat Completions reports usage through ``llm_output``. Responses API
        # reports it on the returned AIMessage instead, so read the first
        # provider usage surface that actually contains tokens.
        if not total_tokens and not prompt_tokens and not completion_tokens:
            for generation_list in response.generations or []:
                for generation in generation_list:
                    message = getattr(generation, "message", None)
                    message_usage = getattr(message, "usage_metadata", None) or {}
                    prompt_tokens += int(message_usage.get("input_tokens") or 0)
                    completion_tokens += int(message_usage.get("output_tokens") or 0)
                    total_tokens += int(message_usage.get("total_tokens") or 0)
                    metadata = getattr(message, "response_metadata", None) or {}
                    self.model_name = str(
                        metadata.get("model_name")
                        or metadata.get("model")
                        or self.model_name
                    )

        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        self.total_tokens += total_tokens or (prompt_tokens + completion_tokens)
        self.model_name = str(llm_output.get("model_name") or self.model_name)

token_usage_var = contextvars.ContextVar("token_usage", default=None)

@contextlib.contextmanager
def track_token_usage():
    handler = TokenUsageCallbackHandler()
    token = token_usage_var.set(handler)
    try:
        yield handler
    finally:
        token_usage_var.reset(token)

def calculate_token_cost(model_name: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Estimate standard text-token cost for a configured alias or provider snapshot.

    Unknown models intentionally return zero instead of silently using an
    unrelated model's price.  That keeps the operations view honest until a
    matching price is explicitly added.
    """
    normalized_name = (model_name or "").strip().lower()
    for model_prefix, (input_per_million, output_per_million) in MODEL_TOKEN_PRICING_PER_MILLION:
        if normalized_name == model_prefix or normalized_name.startswith(f"{model_prefix}-"):
            return (prompt_tokens * input_per_million + completion_tokens * output_per_million) / 1_000_000
    return 0.0

def get_openai_model() -> str:
    from dotenv import load_dotenv
    load_dotenv(BACKEND_ENV_PATH)
    return os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)


def get_llm(*, reasoning_effort: str | None = None):
    openai_key = os.getenv("OPENAI_API_KEY")
    if not openai_key:
        from dotenv import load_dotenv
        load_dotenv(BACKEND_ENV_PATH)
        openai_key = os.getenv("OPENAI_API_KEY")

    callbacks = []
    active_handler = token_usage_var.get()
    if active_handler is not None:
        callbacks.append(active_handler)

    options = {
        "model": get_openai_model(),
        "temperature": 0.1,
        "openai_api_key": openai_key,
        "callbacks": callbacks,
    }
    if reasoning_effort is not None:
        options["reasoning_effort"] = reasoning_effort
        # gpt-5.4-mini rejects function tools combined with reasoning_effort on
        # /v1/chat/completions. The same structured-output request is supported
        # on /v1/responses, which is the report-generation path we need here.
        options["use_responses_api"] = True

    return ChatOpenAI(
        **options
    )


def data_validator_loop(data: dict) -> dict:
    """1. Data Validation Loop (Python Rule Engine)
    - Null Check
    - Range Check
    - Outlier Detection
    - Missing Value Imputation
    """
    current_data = dict(data)
    issues = []
    
    # Check for basic fields
    fields_to_check = ['store_count', 'floating_population', 'sale_price_proxy_manwon_per_m2', 'total_sales']
    
    # Null check and Imputation
    for key in fields_to_check:
        if key not in current_data or current_data[key] is None:
            issues.append(f"Missing {key}")
            current_data[key] = 0
            
    # Range check
    for key in fields_to_check:
        val = current_data.get(key, 0)
        if isinstance(val, (int, float)) and val < 0:
            issues.append(f"Negative value for {key}")
            current_data[key] = 0
            
    # Outlier Detection (simple heuristic)
    if current_data.get('floating_population', 0) > 100_000_000:
        issues.append("Unrealistically high floating population")
        current_data['floating_population'] = 100_000_000
        
    if issues:
        print(f"Data issues detected & corrected via Rule Engine: {issues}")
        
    return current_data

def scoring_validator_loop(report_data: dict, raw_data: dict) -> dict:
    """2. Scoring Validation Loop (Python Rule Engine)
    - Weight Consistency
    - Score Distribution
    - Ranking Stability
    """
    feedback = []
    
    # 1. Weight Consistency & Score Distribution
    score = report_data.get("opportunity_score")
    if score is not None:
        if not isinstance(score, (int, float)):
            feedback.append("opportunity_score must be a number.")
        elif not (0 <= score <= 100):
            feedback.append(f"opportunity_score {score} is out of valid range (0-100).")
            
    # 2. Ranking Stability (for comparison reports)
    if "top_recommendation_name" in report_data and "swot_analysis" in report_data:
        top_name = report_data.get("top_recommendation_name")
        analyzed_areas = [swot.get("area_name") for swot in report_data.get("swot_analysis", [])]
        if top_name and analyzed_areas and top_name not in analyzed_areas:
            feedback.append(f"Top recommended area '{top_name}' is hallucinated. Must be one of {analyzed_areas}.")
            
    is_valid = len(feedback) == 0
    return {"is_valid": is_valid, "feedback": " ".join(feedback)}

def report_validator_loop(report_data: dict, raw_data: dict) -> ReportValidationResult:
    """3. Report Validation Loop"""
    llm = get_llm()
    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are a Report Validation Agent. You strictly check for hallucinations. Every claim in the report must be backed by the raw data. If the report says 'high floating population', the raw data must actually be high."),
        ("user", "Raw Data: {raw_data}\n\nReport: {report_data}\n\nFind any hallucinations.")
    ])
    chain = prompt | llm.with_structured_output(ReportValidationResult)
    return chain.invoke({
        "raw_data": json.dumps(raw_data, ensure_ascii=False),
        "report_data": json.dumps(report_data, ensure_ascii=False)
    })

def run_recursive_analysis(raw_data: dict, generator_func, feedback_aware=False):
    """
    Orchestrates the 4-step pipeline: Analysis -> Validation -> Refinement -> Finalization
    """
    # 1. Data Validation Loop
    valid_data = data_validator_loop(raw_data)
    
    max_retries = 2
    feedback = ""
    
    for attempt in range(max_retries):
        # Analysis
        if feedback_aware and feedback:
            report = generator_func(valid_data, feedback=feedback)
        else:
            report = generator_func(valid_data)
            
        # 2. Scoring Validation Loop
        score_val = scoring_validator_loop(report.model_dump(), valid_data)
        
        # 3. Report Validation Loop
        report_val = report_validator_loop(report.model_dump(), valid_data)
        
        # Refinement checking
        if score_val["is_valid"] and report_val.is_valid:
            # Finalization
            return report
            
        # Prepare feedback for next iteration
        feedback_parts = []
        if not score_val["is_valid"]:
            feedback_parts.append(f"Scoring Feedback: {score_val['feedback']}")
        if not report_val.is_valid:
            feedback_parts.append(f"Hallucinations found: {', '.join(report_val.hallucinations)}")
        
        feedback = "\n".join(feedback_parts)
        print(f"Validation failed on attempt {attempt+1}. Retrying with feedback: {feedback}")
        
    # Return last report if retries exhausted
    return report
