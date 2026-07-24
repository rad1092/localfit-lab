from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.commercial_area import CommercialArea
from app.models.community import UserEvent
from app.schemas.community import EventCreate


router = APIRouter(prefix="/events", tags=["events"])
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
EVENT_RETENTION_DAYS = 30


def _now() -> datetime:
    return datetime.now(timezone.utc)


def purge_expired_events(db: Session, *, now: datetime | None = None) -> int:
    cutoff = ((now or _now()) - timedelta(days=EVENT_RETENTION_DAYS)).isoformat(timespec="seconds")
    return int(
        db.query(UserEvent)
        .filter(UserEvent.created_at < cutoff)
        .delete(synchronize_session=False)
        or 0
    )


@router.post("/log", status_code=status.HTTP_201_CREATED)
def log_user_event(
    request: EventCreate,
    session_id: str = Header(alias="X-LocalFit-Session", min_length=16, max_length=128),
    db: Session = Depends(get_db),
):
    session_id = session_id.strip()
    if not SESSION_ID_PATTERN.fullmatch(session_id):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid anonymous session identifier",
        )

    now = _now()
    area_code = (request.area_code or "").strip() or None
    if request.event_type in {
        "area_selected",
        "report_requested",
        "report_completed",
        "report_failed",
    } and area_code is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="This event requires an area code",
        )
    if area_code is not None and not db.query(CommercialArea.area_code).filter(
        CommercialArea.area_code == area_code
    ).first():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Area not found")
    purge_expired_events(db, now=now)
    event = UserEvent(
        session_id=session_id,
        event_type=request.event_type,
        area_code=area_code,
        created_at=now.isoformat(timespec="seconds"),
    )
    db.add(event)
    db.commit()
    return {
        "status": "logged",
        "event_type": event.event_type,
        "retention_days": EVENT_RETENTION_DAYS,
    }
