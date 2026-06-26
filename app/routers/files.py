import logging
import os
from urllib.parse import quote
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.database import get_db
from app.models.refund import Refund
from app.models.request import Request as RequestModel
from app.models.file_attachment import FileAttachment
from app.services.auth import get_current_user
from app.services.file_service import save_file, get_file_for_download, guess_media_type

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/files", tags=["files"])


def _templates():
    from fastapi.templating import Jinja2Templates
    templates_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
    return Jinja2Templates(directory=templates_dir)


def _render_files_section(request, refund, user, is_internal):
    """Render the files partial for either the public or the internal section.

    Клиент видит только не-внутренние файлы и свой клиентский партиал
    (с кнопками просмотра/скачивания/удаления своих файлов).
    """
    if user.role.value == "client":
        files = [f for f in refund.files if not f.is_internal]
        files.sort(key=lambda f: f.id)
        return _templates().TemplateResponse(
            "client/_files_list.html",
            {"request": request, "files": files, "user": user},
        )
    files = [f for f in refund.files if bool(f.is_internal) == is_internal]
    files.sort(key=lambda f: f.id)
    return _templates().TemplateResponse(
        "refunds/_files_list.html",
        {"request": request, "files": files, "user": user, "is_internal": is_internal},
    )


@router.post("/upload/{refund_id}")
async def upload_file(
    refund_id: int,
    request: Request,
    file: UploadFile = File(...),
    internal: str = Form("false"),
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)

    result = await db.execute(select(Refund).where(Refund.id == refund_id))
    refund = result.scalar_one_or_none()
    if not refund:
        raise HTTPException(status_code=404, detail="Возврат не найден")

    if user.role.value == "client" and refund.client_user_id != user.id:
        raise HTTPException(status_code=403, detail="Нет доступа к этому возврату")

    is_internal = str(internal).lower() in ("true", "1", "on")
    # Only staff/admin may upload internal files.
    if is_internal and user.role.value not in ("admin", "staff"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    await save_file(file, refund_id, db, uploaded_by_id=user.id, is_internal=is_internal)

    result2 = await db.execute(
        select(Refund).options(selectinload(Refund.files)).where(Refund.id == refund_id)
    )
    updated_refund = result2.scalar_one()

    return _render_files_section(request, updated_refund, user, is_internal)


@router.post("/upload-request/{request_id}")
async def upload_request_file(
    request_id: int,
    request: Request,
    file: UploadFile = File(...),
    internal: str = Form("false"),
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)

    result = await db.execute(select(RequestModel).where(RequestModel.id == request_id))
    req = result.scalar_one_or_none()
    if not req:
        raise HTTPException(status_code=404, detail="Запрос не найден")

    if user.role.value == "client" and req.client_user_id != user.id:
        raise HTTPException(status_code=403, detail="Нет доступа к этому запросу")

    is_internal = str(internal).lower() in ("true", "1", "on")
    if is_internal and user.role.value not in ("admin", "staff"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    await save_file(file, None, db, uploaded_by_id=user.id, is_internal=is_internal, request_id=request_id)

    result2 = await db.execute(
        select(RequestModel).options(selectinload(RequestModel.files)).where(RequestModel.id == request_id)
    )
    updated_req = result2.scalar_one()

    return _render_files_section(request, updated_req, user, is_internal)


@router.delete("/{file_id}")
async def delete_file(
    file_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)

    result = await db.execute(select(FileAttachment).where(FileAttachment.id == file_id))
    attachment = result.scalar_one_or_none()
    if not attachment:
        raise HTTPException(status_code=404, detail="Файл не найден")

    refund_id = attachment.refund_id
    request_id = attachment.request_id
    was_internal = bool(attachment.is_internal)

    # Клиент может удалять только свои не-внутренние файлы на своих возврате/запросе.
    if user.role.value == "client":
        if was_internal:
            raise HTTPException(status_code=403, detail="Нет доступа к этому файлу")
        owner_id = None
        if request_id is not None:
            owner_result = await db.execute(
                select(RequestModel.client_user_id).where(RequestModel.id == request_id)
            )
            owner_id = owner_result.scalar_one_or_none()
        elif refund_id is not None:
            owner_result = await db.execute(
                select(Refund.client_user_id).where(Refund.id == refund_id)
            )
            owner_id = owner_result.scalar_one_or_none()
        if owner_id != user.id:
            raise HTTPException(status_code=403, detail="Нет доступа к этому файлу")

    if os.path.exists(attachment.stored_path):
        try:
            os.remove(attachment.stored_path)
        except OSError as e:
            logger.warning(f"Could not delete file from disk: {e}")

    await db.delete(attachment)
    await db.flush()

    if request_id is not None:
        result2 = await db.execute(
            select(RequestModel).options(selectinload(RequestModel.files)).where(RequestModel.id == request_id)
        )
        parent = result2.scalar_one()
    else:
        result2 = await db.execute(
            select(Refund).options(selectinload(Refund.files)).where(Refund.id == refund_id)
        )
        parent = result2.scalar_one()

    return _render_files_section(request, parent, user, was_internal)


@router.get("/{file_id}/download")
async def download_file(
    file_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    attachment = await get_file_for_download(
        file_id, db,
        user_id=user.id,
        user_role=user.role.value,
    )

    return FileResponse(
        path=attachment.stored_path,
        filename=attachment.filename,
        media_type="application/octet-stream",
    )


@router.get("/{file_id}/preview")
async def preview_file(
    file_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Serve a file inline (Content-Disposition: inline) for in-browser preview."""
    user = await get_current_user(request, db)
    attachment = await get_file_for_download(
        file_id, db,
        user_id=user.id,
        user_role=user.role.value,
    )

    media_type = guess_media_type(attachment.filename)
    # Use RFC 5987 encoding so non-ASCII filenames don't break the header.
    disposition = f"inline; filename*=UTF-8''{quote(attachment.filename)}"
    return FileResponse(
        path=attachment.stored_path,
        media_type=media_type,
        headers={"Content-Disposition": disposition},
    )
