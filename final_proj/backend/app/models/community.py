from __future__ import annotations

from sqlalchemy import Column, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import relationship

from app.database import Base


class Comment(Base):
    __tablename__ = "comments"
    __table_args__ = (
        Index(
            "ix_comments_area_industry_status_created",
            "area_code",
            "industry_code",
            "status",
            "created_at",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    area_code = Column(
        String(50),
        ForeignKey("commercial_area.area_code"),
        nullable=False,
        index=True,
    )
    industry_code = Column(String(50), nullable=True, index=True)
    parent_id = Column(Integer, ForeignKey("comments.id"), nullable=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    body = Column(Text, nullable=False)
    status = Column(String(20), nullable=False, default="visible", server_default="visible", index=True)
    created_at = Column(String(50), nullable=False)
    updated_at = Column(String(50), nullable=False)
    deleted_at = Column(String(50), nullable=True)

    author = relationship("User")
    parent = relationship("Comment", remote_side=[id], back_populates="replies")
    replies = relationship("Comment", back_populates="parent", order_by="Comment.id")


class UserEvent(Base):
    __tablename__ = "user_events"
    __table_args__ = (
        Index("ix_user_events_type_created", "event_type", "created_at"),
        Index("ix_user_events_area_created", "area_code", "created_at"),
        Index("ix_user_events_session_created", "session_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(128), nullable=False)
    event_type = Column(String(40), nullable=False)
    area_code = Column(String(50), nullable=True)
    created_at = Column(String(50), nullable=False)
