import enum
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Text, Enum, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class RefundStatus(str, enum.Enum):
    received = "received"
    in_progress = "in_progress"
    approved = "approved"
    sent_to_supplier = "sent_to_supplier"
    stock = "stock"
    completed = "completed"
    archive = "archive"


class RefundSource(str, enum.Enum):
    manual = "manual"
    email = "email"


STATUS_LABELS = {
    RefundStatus.received: "Получен",
    RefundStatus.in_progress: "В работе",
    RefundStatus.approved: "Согласован",
    RefundStatus.sent_to_supplier: "Отдан поставщику",
    RefundStatus.stock: "Сток",
    RefundStatus.completed: "Проведен",
    RefundStatus.archive: "Архив",
}

STATUS_COLORS = {
    RefundStatus.received: "bg-blue-100 text-blue-700",
    RefundStatus.in_progress: "bg-yellow-100 text-yellow-700",
    RefundStatus.approved: "bg-green-100 text-green-700",
    RefundStatus.sent_to_supplier: "bg-indigo-100 text-indigo-700",
    RefundStatus.stock: "bg-orange-100 text-orange-700",
    RefundStatus.completed: "bg-emerald-100 text-emerald-700",
    RefundStatus.archive: "bg-gray-100 text-gray-500",
}


class Refund(Base):
    __tablename__ = "refunds"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    display_id: Mapped[str] = mapped_column(String(20), unique=True, index=True, nullable=False)
    status: Mapped[RefundStatus] = mapped_column(
        Enum(RefundStatus), nullable=False, default=RefundStatus.received
    )
    source: Mapped[RefundSource] = mapped_column(
        Enum(RefundSource), nullable=False, default=RefundSource.manual
    )
    client_name: Mapped[str] = mapped_column(String(255), nullable=False)
    client_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    supplier_id: Mapped[Optional[int]] = mapped_column(ForeignKey("suppliers.id"), nullable=True)
    order_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    email_subject: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    email_from: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    email_uid: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    supplier_doc_number: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    supplier_email_sent_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)

    # Relationships
    client_user: Mapped[Optional["User"]] = relationship(
        "User", foreign_keys=[client_user_id], back_populates="refunds_as_client"
    )
    created_by: Mapped[Optional["User"]] = relationship(
        "User", foreign_keys=[created_by_id], back_populates="refunds_created"
    )
    supplier: Mapped[Optional["Supplier"]] = relationship("Supplier", back_populates="refunds")
    items: Mapped[list["RefundItem"]] = relationship(
        "RefundItem", back_populates="refund", cascade="all, delete-orphan"
    )
    files: Mapped[list["FileAttachment"]] = relationship(
        "FileAttachment", back_populates="refund", cascade="all, delete-orphan"
    )
    messages: Mapped[list["Message"]] = relationship(
        "Message", back_populates="refund", cascade="all, delete-orphan"
    )

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status.value)

    @property
    def status_color(self) -> str:
        return STATUS_COLORS.get(self.status, "bg-gray-100 text-gray-500")
