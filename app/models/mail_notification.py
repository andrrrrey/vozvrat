from datetime import datetime
from typing import Optional
from sqlalchemy import String, Boolean, Integer, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class MailNotification(Base):
    __tablename__ = "mail_notifications"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    email_uid: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    subject: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    from_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    from_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    refund_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("refunds.id", ondelete="SET NULL"), nullable=True
    )
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # 'new' | 'processed' | 'skipped' | 'failed'
    processing_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    skip_reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    refund: Mapped[Optional["Refund"]] = relationship("Refund", foreign_keys=[refund_id])
