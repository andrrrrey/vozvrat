import enum
from datetime import datetime
from sqlalchemy import String, Text, Enum, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class MessageVisibility(str, enum.Enum):
    all = "all"
    staff_only = "staff_only"


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    refund_id: Mapped[int] = mapped_column(ForeignKey("refunds.id"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    visibility: Mapped[MessageVisibility] = mapped_column(
        Enum(MessageVisibility), nullable=False, default=MessageVisibility.all
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    # Relationships
    refund: Mapped["Refund"] = relationship("Refund", back_populates="messages")
    user: Mapped["User"] = relationship("User", back_populates="messages")
