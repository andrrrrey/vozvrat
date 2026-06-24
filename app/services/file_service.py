import os
import uuid
import logging
from pathlib import Path
from typing import Optional
from fastapi import UploadFile, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.config import settings
from app.models.file_attachment import FileAttachment, FileType
from app.models.refund import Refund

logger = logging.getLogger(__name__)


ALLOWED_EXTENSIONS = {".xls", ".xlsx", ".pdf", ".jpg", ".jpeg", ".png", ".webp"}

# Map image extensions to their MIME types for inline browser preview.
IMAGE_MIME_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def build_stored_name(filename: Optional[str]) -> str:
    """Build an ASCII-safe on-disk filename: random uuid + lowercased extension only.

    The original (possibly non-ASCII / Cyrillic) name is preserved separately in
    FileAttachment.filename. Embedding the raw name in the filesystem path can raise on
    open() under a non-UTF-8 locale (e.g. a systemd unit with no LANG set), so we never
    put it on disk.
    """
    ext = Path(filename or "").suffix.lower()
    return f"{uuid.uuid4().hex}{ext}"


def detect_file_type(filename: str) -> FileType:
    ext = Path(filename).suffix.lower()
    if ext in (".xls", ".xlsx"):
        return FileType.xls
    elif ext in (".jpg", ".jpeg", ".png", ".webp"):
        return FileType.photo
    elif ext == ".pdf":
        return FileType.pdf_ukd
    return FileType.other


def guess_media_type(filename: str) -> str:
    """Return a MIME type suitable for inline preview of the given file."""
    ext = Path(filename).suffix.lower()
    if ext in IMAGE_MIME_TYPES:
        return IMAGE_MIME_TYPES[ext]
    if ext == ".pdf":
        return "application/pdf"
    return "application/octet-stream"


async def save_file(
    file: UploadFile,
    refund_id: Optional[int],
    db: AsyncSession,
    uploaded_by_id: Optional[int] = None,
    is_internal: bool = False,
    request_id: Optional[int] = None,
) -> FileAttachment:
    """Save an uploaded file attached either to a refund or to a request.

    Exactly one of refund_id / request_id must be set."""
    if (refund_id is None) == (request_id is None):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Файл должен быть привязан либо к возврату, либо к запросу",
        )

    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Недопустимый тип файла. Разрешены: XLS, XLSX, PDF, JPG, PNG",
        )

    content = await file.read()
    file_size = len(content)

    if file_size > settings.max_upload_size_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Файл слишком большой. Максимум {settings.MAX_UPLOAD_SIZE_MB} МБ",
        )

    subdir = f"refund_{refund_id}" if refund_id is not None else f"request_{request_id}"
    target_dir = Path(settings.UPLOAD_DIR) / subdir
    target_dir.mkdir(parents=True, exist_ok=True)

    unique_name = build_stored_name(file.filename)
    stored_path = str(target_dir / unique_name)

    with open(stored_path, "wb") as f:
        f.write(content)

    file_type = detect_file_type(file.filename or "")

    attachment = FileAttachment(
        refund_id=refund_id,
        request_id=request_id,
        filename=file.filename or unique_name,
        stored_path=stored_path,
        file_type=file_type,
        file_size=file_size,
        uploaded_by_id=uploaded_by_id,
        is_internal=is_internal,
    )
    db.add(attachment)
    await db.flush()
    await db.refresh(attachment)
    logger.info(f"Saved file {file.filename} for {subdir}, type={file_type}")
    return attachment


async def get_file_for_download(
    file_id: int,
    db: AsyncSession,
    user_id: Optional[int] = None,
    user_role: Optional[str] = None,
) -> FileAttachment:
    result = await db.execute(
        select(FileAttachment).where(FileAttachment.id == file_id)
    )
    attachment = result.scalar_one_or_none()

    if not attachment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Файл не найден",
        )

    if user_role == "client":
        # Internal files are never accessible to clients.
        if attachment.is_internal:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Нет доступа к этому файлу",
            )
        owned = False
        if attachment.refund_id is not None:
            refund_result = await db.execute(
                select(Refund).where(
                    Refund.id == attachment.refund_id,
                    Refund.client_user_id == user_id,
                )
            )
            owned = refund_result.scalar_one_or_none() is not None
        elif attachment.request_id is not None:
            from app.models.request import Request
            req_result = await db.execute(
                select(Request).where(
                    Request.id == attachment.request_id,
                    Request.client_user_id == user_id,
                )
            )
            owned = req_result.scalar_one_or_none() is not None
        if not owned:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Нет доступа к этому файлу",
            )

    if not os.path.exists(attachment.stored_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Файл не найден на диске",
        )

    return attachment
