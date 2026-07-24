from fastapi import APIRouter, Depends, Query
from app.dependencies import get_commercial_area_repository
from app.repositories.commercial_area import CommercialAreaRepository
from app.schemas.search import SearchAreaResponse

router = APIRouter(prefix="/search", tags=["search"])

@router.get("", response_model=list[SearchAreaResponse])
def search_areas(
    keyword: str = Query(min_length=1, max_length=100),
    limit: int = Query(default=20, ge=1, le=50),
    repo: CommercialAreaRepository = Depends(get_commercial_area_repository),
):
    return [SearchAreaResponse.model_validate(item) for item in repo.search_summaries(keyword, limit)]
