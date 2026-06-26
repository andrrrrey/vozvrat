import enum
from datetime import datetime
from decimal import Decimal
from typing import Optional
from sqlalchemy import String, Text, Integer, Numeric, Enum, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class RequestStatus(str, enum.Enum):
    received = "received"
    in_work = "in_work"
    completed = "completed"


class RequestSubject(str, enum.Enum):
    delivery = "delivery"
    rejection = "rejection"
    site = "site"
    defect = "defect"
    free = "free"


SUBJECT_LABELS = {
    RequestSubject.delivery: "Срок поставки",
    RequestSubject.rejection: "Отказ",
    RequestSubject.site: "Вопрос по работе сайта",
    RequestSubject.defect: "Дефект детали",
    RequestSubject.free: "Свободная тема",
}


STATUS_LABELS = {
    RequestStatus.received: "Получен",
    RequestStatus.in_work: "В работе",
    RequestStatus.completed: "Завершён",
}

STATUS_COLORS = {
    RequestStatus.received: "bg-blue-100 text-blue-700",
    RequestStatus.in_work: "bg-amber-100 text-amber-700",
    RequestStatus.completed: "bg-green-100 text-green-700",
}


class Request(Base):
    """Запрос — облегчённый аналог возврата без обязательных полей.

    Не попадает в выгрузку 1С. Имеет исполнителя (назначает admin/staff).
    Переписка и файлы переиспользуют общие таблицы messages/file_attachments.
    """
    __tablename__ = "requests"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    display_id: Mapped[str] = mapped_column(String(20), unique=True, index=True, nullable=False)
    status: Mapped[RequestStatus] = mapped_column(
        Enum(RequestStatus), nullable=False, default=RequestStatus.received
    )
    subject: Mapped[Optional[RequestSubject]] = mapped_column(
        Enum(RequestSubject), nullable=True
    )
    client_name: Mapped[str] = mapped_column(String(255), nullable=False)
    client_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    executor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    order_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)

    # Relationships (одно-направленные к User, чтобы не трогать модель User)
    client_user: Mapped[Optional["User"]] = relationship("User", foreign_keys=[client_user_id])
    executor: Mapped[Optional["User"]] = relationship("User", foreign_keys=[executor_id])
    created_by: Mapped[Optional["User"]] = relationship("User", foreign_keys=[created_by_id])
    items: Mapped[list["RequestItem"]] = relationship(
        "RequestItem", back_populates="request", cascade="all, delete-orphan"
    )
    files: Mapped[list["FileAttachment"]] = relationship(
        "FileAttachment", back_populates="request", cascade="all, delete-orphan"
    )
    messages: Mapped[list["Message"]] = relationship(
        "Message", back_populates="request", cascade="all, delete-orphan"
    )

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status.value)

    @property
    def subject_label(self) -> Optional[str]:
        if self.subject is None:
            return None
        return SUBJECT_LABELS.get(self.subject, self.subject.value)

    @property
    def status_color(self) -> str:
        return STATUS_COLORS.get(self.status, "bg-gray-100 text-gray-500")


class RequestItem(Base):
    __tablename__ = "request_items"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("requests.id"), nullable=False, index=True)
    article: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    brand: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    quantity: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    price: Mapped[Optional[Decimal]] = mapped_column(Numeric(16, 2), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    position_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    comment: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)

    # Relationships
    request: Mapped["Request"] = relationship("Request", back_populates="items")
