from typing import Literal

from pydantic import BaseModel

from app.schemas.commercial_area import DisplayScoreGrade, ScoreGrade


class SearchAreaResponse(BaseModel):
    area_code: str
    area_name: str
    district_code: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    score: float | None = None
    grade: ScoreGrade | None = None
    display_grade: DisplayScoreGrade | None = None
    score_type: Literal["demand_accessibility_context"] = "demand_accessibility_context"
    score_label: str = "수요·접근성 맥락 등급"
    official_rank_eligible: Literal[False] = False
