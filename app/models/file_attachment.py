import enum
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Integer, Boolean, Enum, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class FileType(str, enum.Enum):
    xls = "xls"
    photo = "photo"
    pdf_ukd = "pdf_ukd"
    other = "other"


FILE_TYPE_ICONS = {
    FileType.xls: ("bg-green-100 text-green-600", "XLS"),
    FileType.photo: ("bg-purple-100 text-purple-600", "IMG"),
    FileType.pdf_ukd: ("bg-red-100 text-red-600", "PDF"),
    FileType.other: ("bg-gray-100 text-gray-600", "FILE"),
}


class FileAttachment(Base):
    __tablename__ = "file_attachments"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    # Файл принадлежит либо возврату, либо запросу (ровно одному из них).
    refund_id: Mapped[Optional[int]] = mapped_column(ForeignKey("refunds.id"), nullable=True, index=True)
    request_id: Mapped[Optional[int]] = mapped_column(ForeignKey("requests.id"), nullable=True, index=True)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    stored_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    file_type: Mapped[FileType] = mapped_column(
        Enum(FileType), nullable=False, default=FileType.other
    )
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    # Internal files are visible only to staff/admin, never to clients or suppliers.
    is_internal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    uploaded_at: Mapped[datetime] = mapped_column(server_default=func.now())
    uploaded_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)

    # Relationships
    refund: Mapped[Optional["Refund"]] = relationship("Refund", back_populates="files")
    request: Mapped[Optional["Request"]] = relationship("Request", back_populates="files")
    uploaded_by: Mapped[Optional["User"]] = relationship("User", back_populates="uploaded_files")

    @property
    def icon_classes(self) -> str:
        return FILE_TYPE_ICONS.get(self.file_type, FILE_TYPE_ICONS[FileType.other])[0]

    @property
    def type_label(self) -> str:
        return FILE_TYPE_ICONS.get(self.file_type, FILE_TYPE_ICONS[FileType.other])[1]

    @property
    def is_image(self) -> bool:
        """Whether the file can be previewed inline in the browser as an image."""
        return self.file_type == FileType.photo

    @property
    def size_human(self) -> str:
        if self.file_size < 1024:
            return f"{self.file_size} B"
        elif self.file_size < 1024 * 1024:
            return f"{self.file_size // 1024} KB"
        else:
            return f"{self.file_size // (1024 * 1024)} MB"
