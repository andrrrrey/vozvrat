from decimal import Decimal
from typing import Optional
from sqlalchemy import String, Integer, Numeric, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class RefundItem(Base):
    __tablename__ = "refund_items"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    refund_id: Mapped[int] = mapped_column(ForeignKey("refunds.id"), nullable=False, index=True)
    article: Mapped[str] = mapped_column(String(255), nullable=False)
    brand: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    description: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Relationships
    refund: Mapped["Refund"] = relationship("Refund", back_populates="items")
