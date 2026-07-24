from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import exists, or_
from sqlalchemy.orm import Session, aliased, selectinload

from app.database import get_db
from app.dependencies import get_current_user
from app.models.commercial_area import CommercialArea, User
from app.models.community import Comment
from app.schemas.community import CommentCreate, CommentUpdate


router = APIRouter(tags=["comments"])


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _normalized_industry_code(value: str | None) -> str | None:
    normalized = (value or "").strip()
    return normalized or None


def _scope_query(query, *, area_code: str, industry_code: str | None):
    query = query.filter(Comment.area_code == area_code)
    if industry_code is None:
        return query.filter(Comment.industry_code.is_(None))
    return query.filter(Comment.industry_code == industry_code)


def _public_comment_payload(comment: Comment, *, include_replies: bool = True) -> dict:
    is_visible = comment.status == "visible"
    replies = [reply for reply in comment.replies if reply.status == "visible"] if include_replies else []
    return {
        "id": comment.id,
        "area_code": comment.area_code,
        "industry_code": comment.industry_code,
        "parent_id": comment.parent_id,
        "body": comment.body if is_visible else "삭제되었거나 숨겨진 댓글입니다.",
        "status": comment.status,
        "author": (
            {"id": comment.author.id, "nickname": comment.author.nickname}
            if is_visible and comment.author is not None
            else None
        ),
        "created_at": comment.created_at,
        "updated_at": comment.updated_at,
        "replies": [
            _public_comment_payload(reply, include_replies=False)
            for reply in replies
        ],
    }


@router.get("/areas/{area_code}/comments")
def list_area_comments(
    area_code: str,
    industry_code: str | None = Query(default=None, max_length=50),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
):
    industry_code = _normalized_industry_code(industry_code)
    reply = aliased(Comment)
    query = _scope_query(
        db.query(Comment)
        .options(selectinload(Comment.author), selectinload(Comment.replies).selectinload(Comment.author))
        .filter(
            Comment.parent_id.is_(None),
            or_(
                Comment.status == "visible",
                exists().where(reply.parent_id == Comment.id, reply.status == "visible"),
            ),
        ),
        area_code=area_code,
        industry_code=industry_code,
    ).order_by(Comment.created_at.desc(), Comment.id.desc())

    total = int(query.count())
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return {
        "items": [_public_comment_payload(item) for item in items],
        "page": page,
        "page_size": page_size,
        "total": total,
    }


@router.post(
    "/areas/{area_code}/comments",
    status_code=status.HTTP_201_CREATED,
)
def create_area_comment(
    area_code: str,
    request: CommentCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    area = db.query(CommercialArea.area_code).filter(CommercialArea.area_code == area_code).first()
    if area is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Area not found")

    industry_code = _normalized_industry_code(request.industry_code)
    parent = None
    if request.parent_id is not None:
        parent = db.query(Comment).filter(Comment.id == request.parent_id).first()
        if parent is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Parent comment not found")
        if parent.parent_id is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Replies may only be one level deep",
            )
        if parent.status != "visible":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Parent comment is not visible")
        if parent.area_code != area_code or parent.industry_code != industry_code:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Reply scope must match the parent comment",
            )

    body = request.body.strip()
    if not body:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Comment body is required")
    now = _now_iso()
    comment = Comment(
        area_code=area_code,
        industry_code=industry_code,
        parent_id=parent.id if parent else None,
        user_id=current_user.id,
        body=body,
        status="visible",
        created_at=now,
        updated_at=now,
    )
    db.add(comment)
    db.commit()
    db.refresh(comment)
    return _public_comment_payload(comment)


def _owned_visible_comment(comment_id: int, current_user: User, db: Session) -> Comment:
    comment = db.query(Comment).filter(Comment.id == comment_id).first()
    if comment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found")
    if comment.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Comment owner required")
    if comment.status != "visible":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Comment is not editable")
    return comment


@router.patch("/comments/{comment_id}")
def update_comment(
    comment_id: int,
    request: CommentUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    comment = _owned_visible_comment(comment_id, current_user, db)
    body = request.body.strip()
    if not body:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Comment body is required")
    comment.body = body
    comment.updated_at = _now_iso()
    db.commit()
    db.refresh(comment)
    return _public_comment_payload(comment)


@router.delete("/comments/{comment_id}")
def delete_comment(
    comment_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    comment = _owned_visible_comment(comment_id, current_user, db)
    now = _now_iso()
    comment.status = "deleted"
    comment.deleted_at = now
    comment.updated_at = now
    db.commit()
    return {"id": comment.id, "status": "deleted"}
