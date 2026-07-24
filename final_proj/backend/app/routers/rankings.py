from fastapi import APIRouter, Depends
from app.dependencies import get_commercial_area_service
from app.services.commercial_area import CommercialAreaService
from app.schemas.commercial_area import RankingResponse

router = APIRouter(prefix="/rankings", tags=["rankings"])

@router.get("", response_model=list[RankingResponse])
def get_rankings(service: CommercialAreaService = Depends(get_commercial_area_service)):
    return service.get_rankings()
