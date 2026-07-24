from fastapi import APIRouter, Depends, HTTPException
from app.dependencies import get_commercial_area_service, get_dashboard_service, get_recommendation_service, get_comparison_service
from app.services.commercial_area import CommercialAreaService, DashboardService
from app.services.comparison_report import ComparisonReportService
from app.services.single_report import SingleReportService
from app.schemas.commercial_area import CommercialAreaResponse, CommercialAreaWithIndustryResponse, DashboardSummaryResponse, AIAnalysisResponse, AIComparisonResponse, ComparisonRequest, RankingResponse

router = APIRouter(prefix="/areas", tags=["areas"])

@router.get("", response_model=list[CommercialAreaResponse])
def get_areas(service: CommercialAreaService = Depends(get_commercial_area_service)):
    return service.get_all_areas()

@router.get("/rankings", response_model=list[RankingResponse])
def get_rankings(service: CommercialAreaService = Depends(get_commercial_area_service)):
    return service.get_rankings()

@router.get("/stats")
def get_overview_stats(service: CommercialAreaService = Depends(get_commercial_area_service)):
    return service.get_overview_stats()

@router.get("/{code}", response_model=CommercialAreaResponse | CommercialAreaWithIndustryResponse)
def get_area(
    code: str,
    industry_code: str | None = None,
    service: CommercialAreaService = Depends(get_commercial_area_service),
):
    area = service.get_area(code)
    if not area:
        raise HTTPException(status_code=404, detail="Area not found")
    if industry_code is not None:
        query = str(industry_code).strip()
        resolved = service.resolve_industry(query)
        resolved_codes = {
            str((resolved or {}).get("industry_code") or "").upper(),
            str((resolved or {}).get("final_algorithm_key") or "").upper(),
        }
        if not query or not resolved or query.upper() not in resolved_codes:
            raise HTTPException(
                status_code=400,
                detail={
                    "message": "industry unresolved",
                    "industry_code": query,
                },
            )
        return CommercialAreaWithIndustryResponse(
            **area.model_dump(),
            industry_analysis=service.get_industry_analysis(code, resolved),
        )
    return area

@router.get("/{code}/dashboard", response_model=DashboardSummaryResponse)
def get_dashboard(code: str, service: DashboardService = Depends(get_dashboard_service)):
    dashboard = service.get_dashboard_summary(code)
    if not dashboard:
        raise HTTPException(status_code=404, detail="Area not found")
    return dashboard

@router.get("/{code}/recommendations", response_model=AIAnalysisResponse)
def get_recommendations(
    code: str,
    business_type: str | None = None,
    service: SingleReportService = Depends(get_recommendation_service),
):
    if business_type and not service.area_service.resolve_industry(business_type):
        raise HTTPException(
            status_code=400,
            detail={
                "message": "industry unresolved",
                "options": service.area_service.industry_options(business_type),
            },
        )
    recommendation = service.get_recommendations(code, business_type=business_type)
    if not recommendation:
        raise HTTPException(status_code=404, detail="Area not found")
    return recommendation

@router.post("/compare", response_model=AIComparisonResponse)
def compare_areas(request: ComparisonRequest, service: ComparisonReportService = Depends(get_comparison_service)):
    comparison = service.get_comparison(request.area_codes)
    if not comparison:
        raise HTTPException(status_code=400, detail="Invalid area codes provided")
    return comparison
