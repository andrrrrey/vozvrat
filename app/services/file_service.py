import os
import shutil
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

# Только изображения — для фото в комментариях.
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

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
    elif ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
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
    message_id: Optional[int] = None,
    allowed_extensions: Optional[set] = None,
) -> FileAttachment:
    """Save an uploaded file attached to a refund, a request or a comment (message).

    Exactly one of refund_id / request_id / message_id must be set."""
    owners = [x for x in (refund_id, request_id, message_id) if x is not None]
    if len(owners) != 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Файл должен быть привязан к возврату, запросу или комментарию",
        )

    allowed = allowed_extensions if allowed_extensions is not None else ALLOWED_EXTENSIONS
    ext = Path(file.filename or "").suffix.lower()
    if ext not in allowed:
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

    if refund_id is not None:
        subdir = f"refund_{refund_id}"
    elif request_id is not None:
        subdir = f"request_{request_id}"
    else:
        subdir = f"comment_{message_id}"
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
        message_id=message_id,
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


async def copy_file_to_refund(
    source: FileAttachment,
    refund_id: int,
    db: AsyncSession,
    uploaded_by_id: Optional[int] = None,
) -> Optional[FileAttachment]:
    """Скопировать вложение (файл на диске + запись) на указанный возврат.

    Используется при создании возврата из запроса, чтобы перенести приложенные
    к запросу фото/файлы. Возвращает новую запись FileAttachment или None, если
    исходный файл отсутствует на диске."""
    if not source.stored_path or not os.path.exists(source.stored_path):
        logger.warning(
            f"copy_file_to_refund: source file missing on disk ({source.stored_path}); skipping"
        )
        return None

    target_dir = Path(settings.UPLOAD_DIR) / f"refund_{refund_id}"
    target_dir.mkdir(parents=True, exist_ok=True)

    ext = Path(source.stored_path).suffix.lower()
    unique_name = f"{uuid.uuid4().hex}{ext}"
    dest_path = str(target_dir / unique_name)

    shutil.copy2(source.stored_path, dest_path)

    attachment = FileAttachment(
        refund_id=refund_id,
        filename=source.filename,
        stored_path=dest_path,
        file_type=source.file_type,
        file_size=source.file_size,
        uploaded_by_id=uploaded_by_id,
        is_internal=bool(source.is_internal),
    )
    db.add(attachment)
    await db.flush()
    await db.refresh(attachment)
    logger.info(f"Copied file {source.filename} to refund_{refund_id}")
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
        # Фото из staff-only комментариев клиентам недоступны.
        if attachment.message_id is not None:
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
