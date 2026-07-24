from sqlalchemy import Column, String, Float, Integer, ForeignKey, Text
from sqlalchemy import Index, text
from sqlalchemy.orm import relationship
from app.database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    email = Column(String(100), unique=True, index=True)
    password_hash = Column(String(200))
    nickname = Column(String(50))
    created_at = Column(String(50))
    is_admin = Column(Integer, nullable=False, default=0, server_default="0")


class CommercialArea(Base):
    __tablename__ = "commercial_area"

    area_code = Column(String(50), primary_key=True, index=True)
    area_name = Column(String(100), index=True)
    district_code = Column(String(50))
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)

    district_populations = relationship("DistrictPopulation", back_populates="area")
    district_floatings = relationship("DistrictFloating", back_populates="area")
    district_sales = relationship("DistrictSales", back_populates="area")
    district_store_counts = relationship("DistrictStoreCount", back_populates="area")
    district_growth_histories = relationship("DistrictGrowthHistory", back_populates="area")
    sale_price_proxies = relationship("AreaSalePriceProxy", back_populates="area")
    rone_cost_references = relationship("AreaRoneCostReference", back_populates="area")

class DistrictPopulation(Base):
    __tablename__ = "district_population"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    area_code = Column(String(50), ForeignKey("commercial_area.area_code"), index=True)
    district_name = Column(String(100))
    resident_population = Column(Integer, default=0)
    worker_population = Column(Integer, default=0)
    timestamp = Column(String(50))
    area = relationship("CommercialArea", back_populates="district_populations")

class DistrictFloating(Base):
    __tablename__ = "district_floating"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    area_code = Column(String(50), ForeignKey("commercial_area.area_code"), index=True)
    floating_population = Column(Integer, default=0)
    timestamp = Column(String(50))
    area = relationship("CommercialArea", back_populates="district_floatings")

class DistrictSales(Base):
    __tablename__ = "district_sales"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    area_code = Column(String(50), ForeignKey("commercial_area.area_code"), index=True)
    industry_code = Column(String(50))
    industry_name = Column(String(100))
    sales_amount = Column(Float, default=0.0)
    timestamp = Column(String(50))
    area = relationship("CommercialArea", back_populates="district_sales")

class DistrictStoreCount(Base):
    __tablename__ = "district_store_count"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    area_code = Column(String(50), ForeignKey("commercial_area.area_code"), index=True)
    industry_code = Column(String(50))
    industry_name = Column(String(100))
    store_count = Column(Integer, default=0)
    timestamp = Column(String(50))
    area = relationship("CommercialArea", back_populates="district_store_counts")

class DistrictGrowthHistory(Base):
    __tablename__ = "district_growth_history"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    area_code = Column(String(50), ForeignKey("commercial_area.area_code"), index=True)
    sales_amount = Column(Float, default=0.0)
    floating_population = Column(Integer, default=0)
    store_count = Column(Integer, default=0)
    timestamp = Column(String(50))
    area = relationship("CommercialArea", back_populates="district_growth_histories")

class AreaSalePriceProxy(Base):
    __tablename__ = "area_sale_price_proxy"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    area_code = Column(String(50), ForeignKey("commercial_area.area_code"), index=True)
    sale_price_proxy_manwon_per_m2 = Column(Float, nullable=True)
    period = Column(String(50))
    source_id = Column(String(100))
    provider = Column(String(100))
    grain = Column(String(100))
    direct_score_allowed = Column(Integer, default=0)
    proxy_score_allowed = Column(Integer, default=1)
    provenance_note = Column(Text)
    area = relationship("CommercialArea", back_populates="sale_price_proxies")


class AreaRoneCostReference(Base):
    __tablename__ = "area_rone_cost_reference"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    area_code = Column(String(50), ForeignKey("commercial_area.area_code"), index=True)
    period = Column(String(50))
    selection_group = Column(String(150))
    metric_code = Column(String(50))
    metric_name = Column(String(100))
    metric_value = Column(Float, nullable=True)
    unit = Column(String(50))
    property_type = Column(String(100))
    source_region_name = Column(String(200))
    mapping_scope = Column(String(100))
    mapping_method = Column(String(250))
    mapping_confidence = Column(String(100))
    source_id = Column(String(100))
    provider = Column(String(100))
    direct_value_allowed = Column(Integer, default=0)
    proxy_score_allowed = Column(Integer, default=0)
    engine_promotion_ready = Column(Integer, default=0)
    forbidden_claim_ko = Column(Text)
    provenance_note = Column(Text)
    area = relationship("CommercialArea", back_populates="rone_cost_references")

class FavoriteArea(Base):
    __tablename__ = "favorite_area"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    area_code = Column(String(50), ForeignKey("commercial_area.area_code"), index=True)
    
    area = relationship("CommercialArea")

class SavedReport(Base):
    __tablename__ = "saved_report"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    report_data = Column(Text) # Storing JSON as text
    created_at = Column(String(50))


class ReportGenerationJob(Base):
    __tablename__ = "report_generation_job"

    id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=True)
    client_session_id = Column(String(128), index=True, nullable=True)
    report_type = Column(String(20), nullable=False)
    request_json = Column(Text, nullable=False)
    status = Column(String(20), nullable=False, default="queued", index=True)
    progress_message = Column(String(200), nullable=False, default="리포트 생성 대기 중")
    result_json = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(String(50), nullable=False)
    started_at = Column(String(50), nullable=True)
    completed_at = Column(String(50), nullable=True)


class ReportEvaluationRun(Base):
    __tablename__ = "report_evaluation_run"
    __table_args__ = (
        Index(
            "ux_report_evaluation_active_job",
            "report_job_id",
            unique=True,
            sqlite_where=text("status IN ('queued', 'running')"),
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
    )

    id = Column(String(64), primary_key=True)
    report_job_id = Column(
        String(64),
        ForeignKey("report_generation_job.id"),
        index=True,
        nullable=False,
    )
    report_sha256 = Column(String(64), nullable=False)
    status = Column(String(20), nullable=False, default="queued", index=True)
    progress_message = Column(String(200), nullable=False, default="평가 대기 중")
    protocol_version = Column(String(100), nullable=True)
    overall_status = Column(String(20), nullable=True)
    automatic_status = Column(String(20), nullable=True)
    summary_json = Column(Text, nullable=True)
    question_results_json = Column(Text, nullable=True)
    output_dir = Column(Text, nullable=True)
    error_message = Column(String(500), nullable=True)
    created_at = Column(String(50), nullable=False)
    started_at = Column(String(50), nullable=True)
    completed_at = Column(String(50), nullable=True)


class ChatbotHistory(Base):
    __tablename__ = "chatbot_history"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    area_name = Column(String(100))
    business_type = Column(String(100))
    budget = Column(Integer)
    result_data = Column(Text) # Storing JSON as text
    created_at = Column(String(50))


class PDFExportHistory(Base):
    __tablename__ = "pdf_export_history"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    report_id = Column(Integer, ForeignKey("saved_report.id"), nullable=True)
    filename = Column(String(250))
    exported_at = Column(String(50))


class TokenUsageLog(Base):
    __tablename__ = "token_usage_log"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=True)
    model_name = Column(String(100))
    prompt_tokens = Column(Integer)
    completion_tokens = Column(Integer)
    total_tokens = Column(Integer)
    estimated_cost = Column(Float)
    feature_name = Column(String(100))
    status = Column(String(20), nullable=False, default="success")
    reasoning_effort = Column(String(20), nullable=True)
    generation_mode = Column(String(30), nullable=True)
    quality_status = Column(String(20), nullable=True)
    original_validation_issues_json = Column(Text, nullable=True)
    error_type = Column(String(100), nullable=True)
    error_message = Column(String(500), nullable=True)
    created_at = Column(String(50))



class ExternalAPILog(Base):
    __tablename__ = "external_api_log"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    api_name = Column(String(100), index=True)
    endpoint = Column(String(250))
    status_code = Column(Integer)
    response_time_ms = Column(Integer, nullable=True)
    call_type = Column(String(10), default="GET")
    created_at = Column(String(50))
