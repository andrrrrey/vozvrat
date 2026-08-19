import enum
from datetime import datetime
from typing import Optional, List
from sqlalchemy import String, Text, Enum, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class MessageVisibility(str, enum.Enum):
    all = "all"
    staff_only = "staff_only"


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    # Сообщение принадлежит либо возврату, либо запросу (ровно одному из них).
    refund_id: Mapped[Optional[int]] = mapped_column(ForeignKey("refunds.id"), nullable=True, index=True)
    request_id: Mapped[Optional[int]] = mapped_column(ForeignKey("requests.id"), nullable=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    visibility: Mapped[MessageVisibility] = mapped_column(
        Enum(MessageVisibility), nullable=False, default=MessageVisibility.all
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    # Relationships
    refund: Mapped[Optional["Refund"]] = relationship("Refund", back_populates="messages")
    request: Mapped[Optional["Request"]] = relationship("Request", back_populates="messages")
    user: Mapped["User"] = relationship("User", back_populates="messages")
    # Фото, прикреплённые к комментарию.
    files: Mapped[List["FileAttachment"]] = relationship(
        "FileAttachment", back_populates="message", cascade="all, delete-orphan"
    )
