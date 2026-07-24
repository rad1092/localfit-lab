from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


CommentStatus = Literal["visible", "hidden", "deleted"]


class CommentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1, max_length=1000)
    industry_code: str | None = Field(default=None, max_length=50)
    parent_id: int | None = Field(default=None, ge=1)


class CommentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1, max_length=1000)


class CommentStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: CommentStatus


class EventCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: Literal[
        "page_view",
        "search_submitted",
        "area_selected",
        "report_requested",
        "report_completed",
        "report_failed",
    ]
    area_code: str | None = Field(default=None, max_length=50)
