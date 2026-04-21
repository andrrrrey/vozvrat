import enum
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Boolean, Enum, func
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
    external_id: Mapped[Optional[str]] = mapped_column(String(100), unique=True, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    # Relationships
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
