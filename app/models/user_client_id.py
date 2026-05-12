from sqlalchemy import String, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class UserClientId(Base):
    __tablename__ = "user_client_ids"
    __table_args__ = (UniqueConstraint("client_id", name="uq_user_client_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    client_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)

    user: Mapped["User"] = relationship("User", back_populates="client_ids")
