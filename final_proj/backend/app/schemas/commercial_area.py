from typing import List, Literal, Optional

from pydantic import BaseModel, Field


ScoreGrade = Literal["A", "B", "C", "D", "E"]
DisplayScoreGrade = Literal["A+", "A", "B+", "B", "C+", "C", "D+", "D", "E+", "E"]
AxisDisplayScoreGrade = Literal["A+", "A", "B+", "B", "C+", "C", "D+", "D", "E+", "E", "등급 보류"]


class DistrictPopulationBase(BaseModel):
    district_name: Optional[str] = None
    resident_population: int
    worker_population: int
    timestamp: str

    class Config:
        from_attributes = True


class DistrictFloatingBase(BaseModel):
    floating_population: int
    timestamp: str

    class Config:
        from_attributes = True


class DistrictSalesBase(BaseModel):
    industry_code: str
    industry_name: str
    sales_amount: float
    timestamp: str

    class Config:
        from_attributes = True


class DistrictStoreCountBase(BaseModel):
    industry_code: str
    industry_name: Optional[str] = None
    store_count: int
    timestamp: str

    class Config:
        from_attributes = True


class DistrictGrowthHistoryBase(BaseModel):
    sales_amount: float
    floating_population: int
    store_count: int
    timestamp: str

    class Config:
        from_attributes = True


class AreaSalePriceProxyBase(BaseModel):
    sale_price_proxy_manwon_per_m2: Optional[float] = None
    period: str
    source_id: Optional[str] = None
    provider: Optional[str] = None
    grain: Optional[str] = None
    direct_score_allowed: bool = False
    proxy_score_allowed: bool = True
    provenance_note: Optional[str] = None

    class Config:
        from_attributes = True


class AreaRoneCostReferenceBase(BaseModel):
    period: str
    selection_group: Optional[str] = None
    metric_code: str
    metric_name: Optional[str] = None
    metric_value: Optional[float] = None
    unit: Optional[str] = None
    property_type: Optional[str] = None
    source_region_name: Optional[str] = None
    mapping_scope: Optional[str] = None
    mapping_method: Optional[str] = None
    mapping_confidence: Optional[str] = None
    source_id: Optional[str] = None
    provider: Optional[str] = None
    direct_value_allowed: bool = False
    proxy_score_allowed: bool = False
    engine_promotion_ready: bool = False
    forbidden_claim_ko: Optional[str] = None
    provenance_note: Optional[str] = None

    class Config:
        from_attributes = True


class IndustryAxisMetric(BaseModel):
    internal_value: Optional[float] = None
    display_grade: Optional[DisplayScoreGrade] = None


class IndustryAxisAnalysis(BaseModel):
    sales: IndustryAxisMetric
    competition: IndustryAxisMetric
    demand: IndustryAxisMetric
    accessibility: IndustryAxisMetric


class IndustryQuarterHistory(BaseModel):
    quarter: str
    sales_amount: Optional[float] = None
    store_count: Optional[int] = None


class IndustryAnalysisResponse(BaseModel):
    industry_code: str
    industry_name: str
    reference_quarter: str
    availability: Literal["available", "partial", "unavailable"]
    display_grade: Optional[DisplayScoreGrade] = None
    score_applicable: bool = False
    score_version: Optional[str] = None
    score_reason: str
    current_sales_amount: Optional[float] = None
    current_store_count: Optional[int] = None
    history: List[IndustryQuarterHistory] = Field(default_factory=list)
    axes: IndustryAxisAnalysis
    missing_data: List[str] = Field(default_factory=list)


class CommercialAreaResponse(BaseModel):
    area_code: str
    area_name: str
    district_code: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    score: Optional[int] = None
    grade: Optional[ScoreGrade] = None
    display_grade: Optional[DisplayScoreGrade] = None
    score_type: Literal["demand_accessibility_context"] = "demand_accessibility_context"
    score_label: str = "수요·접근성 맥락 등급"
    official_rank_eligible: Literal[False] = False

    district_populations: List[DistrictPopulationBase] = []
    district_floatings: List[DistrictFloatingBase] = []
    district_sales: List[DistrictSalesBase] = []
    district_store_counts: List[DistrictStoreCountBase] = []
    district_growth_histories: List[DistrictGrowthHistoryBase] = []
    sale_price_proxies: List[AreaSalePriceProxyBase] = []
    rone_cost_references: List[AreaRoneCostReferenceBase] = []

    class Config:
        from_attributes = True


class CommercialAreaWithIndustryResponse(CommercialAreaResponse):
    industry_analysis: IndustryAnalysisResponse


class RankingResponse(BaseModel):
    rank: int
    area_code: str
    area_name: str
    score: int
    grade: ScoreGrade
    display_grade: DisplayScoreGrade
    trend: str
    score_type: Literal["demand_accessibility_context"] = "demand_accessibility_context"
    score_label: str = "수요·접근성 맥락 등급"
    official_rank_eligible: Literal[False] = False


class DashboardSummaryResponse(BaseModel):
    area_code: str
    area_name: str
    total_stores: int
    floating_population: int
    sale_price_proxy_manwon_per_m2: Optional[float] = None
    rent_reference_thousand_won_per_m2: Optional[float] = None
    vacancy_reference_pct: Optional[float] = None
    cost_reference_provenance: Optional[dict] = None
    total_sales: float
    score: Optional[int] = None
    grade: Optional[ScoreGrade] = None
    display_grade: Optional[DisplayScoreGrade] = None
    score_type: Literal["demand_accessibility_context"] = "demand_accessibility_context"
    score_label: str = "수요·접근성 맥락 등급"
    official_rank_eligible: Literal[False] = False


class RadarMetric(BaseModel):
    subject: str
    scores: dict[str, Optional[float]]


class AxisInterpretation(BaseModel):
    axis: str
    score: Optional[float] = None
    score_display: str = ""
    display_grade: Optional[AxisDisplayScoreGrade] = None
    interpretation_level: Optional[str] = None
    evidence_metrics: list[str] = Field(default_factory=list)
    chart_id: str = "C1"
    meaning: str
    evidence: str
    risk: str
    action: str
    next_check: str = ""
    frame_citations: list[int] = Field(default_factory=list)


class VisualizationMetric(BaseModel):
    label: str
    value: Optional[float] = None
    group: str


class SourceCitation(BaseModel):
    title: str = ""
    source_path: str = ""
    provider: str = ""
    dataset_name: str = ""
    source_url: str = ""
    period: str = ""
    granularity: str = ""
    theme: str = ""
    used_for: str = ""
    caveat: str = ""


class AIAnalysisResponse(BaseModel):
    summary: str
    strengths: list[str]
    weaknesses: list[str]
    recommended_businesses: list[str]
    risk_factors: list[str]
    opportunity_score: Optional[float] = None
    radar_metrics: list[RadarMetric] = []
    industry_code: Optional[str] = None
    industry_name: Optional[str] = None
    score_source: Optional[str] = None
    header_block: dict = Field(default_factory=dict)
    narrative_title: str = ""
    thesis: list[str] = Field(default_factory=list)
    executive_interpretation: str = ""
    score_interpretation: str = ""
    axis_interpretations: list[AxisInterpretation] = Field(default_factory=list)
    trend_analysis: str = ""
    alternatives: list[dict] = Field(default_factory=list)
    user_fit: str = ""
    evidence_basis: list[str] = Field(default_factory=list)
    source_citations: list[SourceCitation] = Field(default_factory=list)
    claim_source_map: list[dict] = Field(default_factory=list)
    methodology_notes: list[str] = Field(default_factory=list)
    action_plan: list[str] = Field(default_factory=list)
    onsite_checklist: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    chart_manifest: list[dict] = Field(default_factory=list)
    original_validation_issues: list[str] = Field(default_factory=list)
    validation_issues: list[str] = Field(default_factory=list)
    quality_warnings: list[str] = Field(default_factory=list)
    quality_status: str = "unchecked"
    generation_mode: Literal["llm", "partial_fallback", "deterministic"] = "deterministic"
    fallback_fields: list[str] = Field(default_factory=list)
    facts_pack_display: dict = Field(default_factory=dict)
    facts_lite_display: dict = Field(default_factory=dict)
    indicator_pack: dict = Field(default_factory=dict)
    evidence_frames: list[dict] = Field(default_factory=list)
    news_evidence: list[dict] = Field(default_factory=list)
    section_repair_log: list[dict] = Field(default_factory=list)
    token_usage: dict = Field(default_factory=dict)
    cache_meta: dict = Field(default_factory=dict)
    visualization_data: list[VisualizationMetric] = Field(default_factory=list)
    markdown_body: str = ""
    ai_model: Optional[str] = None
    ai_generated: bool = False


class AreaSWOTResponse(BaseModel):
    area_name: str
    pros: list[str]
    cons: list[str]


class AIComparisonResponse(BaseModel):
    summary: str
    top_recommendation_name: str
    top_recommendation_reason: str
    swot_analysis: list[AreaSWOTResponse]
    radar_metrics: list[RadarMetric] = []
    narrative_title: str = ""
    executive_interpretation: str = ""
    comparison_matrix: list[dict] = Field(default_factory=list)
    evidence_basis: list[str] = Field(default_factory=list)
    source_citations: list[SourceCitation] = Field(default_factory=list)
    methodology_notes: list[str] = Field(default_factory=list)
    action_plan: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    visualization_data: list[VisualizationMetric] = Field(default_factory=list)
    markdown_body: str = ""
    ai_model: Optional[str] = None
    ai_generated: bool = False


class ComparisonRequest(BaseModel):
    area_codes: list[str]


class SavedReportCreate(BaseModel):
    report_data: dict


class SavedReportResponse(BaseModel):
    id: int
    report_data: dict
    created_at: Optional[str] = None

    class Config:
        from_attributes = True


class ChatbotRequest(BaseModel):
    area_name: str
    business_type: Optional[str] = None
    budget: Optional[int] = None


class CompactResponse(BaseModel):
    condition_summary: str
    quick_judgement: str
    main_risks: list[str]
    alternative_areas: list[dict] = []
    cta: str = ""
    report_id: Optional[int] = None
    has_detailed_report: bool = True
    ai_explanation: str = ""
    evidence_basis: list[str] = Field(default_factory=list)
    source_citations: list[SourceCitation] = Field(default_factory=list)
    recommended_strategy: list[str] = Field(default_factory=list)


class DashboardQuery(BaseModel):
    area_name: str
    business_type: str
    budget: int


class ChatbotAction(BaseModel):
    label: str
    type: str
    target: str

class ChatOption(BaseModel):
    label: str
    type: str
    value: str
    payload: dict = Field(default_factory=dict)


class ChatState(BaseModel):
    area_code: Optional[str] = None
    area_name: Optional[str] = None
    industry_code: Optional[str] = None
    business_type: Optional[str] = None
    budget: Optional[int] = None
    last_report_id: Optional[int] = None


class UserBusinessCondition(BaseModel):
    area_name: str
    business_type: Optional[str] = None
    budget: Optional[int] = None


class ChatbotResponse(BaseModel):
    area_code: Optional[str] = None
    area_name: Optional[str] = None
    compact_response: CompactResponse
    report_id: Optional[int] = None
    condition: UserBusinessCondition
    actions: list[ChatbotAction] = []
    is_guest: bool = False
    message: Optional[str] = None


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str
    history: list[ChatMessage] = []
    state: Optional[ChatState] = None


class ChatReplyResponse(BaseModel):
    type: str
    text: str = ""
    report: Optional[ChatbotResponse] = None
    is_guest: bool = False
    message: Optional[str] = None
    options: Optional[List[str]] = Field(default_factory=list)
    option_payloads: Optional[List[ChatOption]] = Field(default_factory=list)
    state: Optional[ChatState] = None
    missing_fields: list[str] = Field(default_factory=list)
    pending_slot: Optional[str] = None


class DetailedFitReportResponse(BaseModel):
    user_condition: UserBusinessCondition
    target_area_analysis: dict
    fit_score: int
    score_breakdown: dict
    risk_summary: list[str]
    alternative_areas: list[dict]
    recommended_strategy: list[str]
    disclaimer: str = ""


class ChatbotHistoryResponse(BaseModel):
    id: int
    area_name: str
    business_type: Optional[str] = None
    budget: Optional[int] = None
    result_data: dict
    created_at: Optional[str] = None

    class Config:
        from_attributes = True


class PDFExportHistoryResponse(BaseModel):
    id: int
    user_id: int
    report_id: Optional[int] = None
    filename: str
    exported_at: str

    class Config:
        from_attributes = True


class TokenUsageLogResponse(BaseModel):
    id: int
    user_id: Optional[int] = None
    model_name: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_cost: float
    feature_name: str
    status: str = "success"
    reasoning_effort: Optional[str] = None
    generation_mode: Optional[Literal["llm", "partial_fallback", "deterministic"]] = None
    quality_status: Optional[str] = None
    original_validation_issues: list[str] = Field(default_factory=list)
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    created_at: str

    class Config:
        from_attributes = True


class ExternalAPILogResponse(BaseModel):
    id: int
    api_name: str
    endpoint: str
    status_code: int
    response_time_ms: Optional[int] = None
    call_type: str
    created_at: str

    class Config:
        from_attributes = True
