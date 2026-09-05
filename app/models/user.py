import enum
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Boolean, Enum, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class UserRole(str, enum.Enum):
    admin = "admin"
    staff = "staff"
    client = "client"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), nullable=False, default=UserRole.staff)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    # Менеджер клиента — сотрудник (admin/staff), отвечающий за этого клиента.
    # Заполняется вручную администратором. Значим только для пользователей-клиентов;
    # сотрудник видит только своих клиентов и их возвраты/запросы.
    manager_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Relationships
    manager: Mapped[Optional["User"]] = relationship(
        "User", remote_side="User.id", foreign_keys=[manager_id], backref="managed_clients"
    )
    client_ids: Mapped[list["UserClientId"]] = relationship(
        "UserClientId", back_populates="user", cascade="all, delete-orphan"
    )
    refunds_as_client: Mapped[list["Refund"]] = relationship(
        "Refund", foreign_keys="Refund.client_user_id", back_populates="client_user"
    )
    refunds_created: Mapped[list["Refund"]] = relationship(
        "Refund", foreign_keys="Refund.created_by_id", back_populates="created_by"
    )
    messages: Mapped[list["Message"]] = relationship("Message", back_populates="user")
    uploaded_files: Mapped[list["FileAttachment"]] = relationship(
        "FileAttachment", back_populates="uploaded_by"
    )
