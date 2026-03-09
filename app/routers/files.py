import logging
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models.refund import Refund
from app.services.auth import get_current_user
from app.services.file_service import save_file, get_file_for_download

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/files", tags=["files"])


@router.post("/upload/{refund_id}")
async def upload_file(
    refund_id: int,
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)

    result = await db.execute(select(Refund).where(Refund.id == refund_id))
    refund = result.scalar_one_or_none()
    if not refund:
        raise HTTPException(status_code=404, detail="Возврат не найден")

    if user.role.value == "client" and refund.client_user_id != user.id:
        raise HTTPException(status_code=403, detail="Нет доступа к этому возврату")

    attachment = await save_file(file, refund_id, db, uploaded_by_id=user.id)

    from fastapi.templating import Jinja2Templates
    import os
    templates_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
    templates = Jinja2Templates(directory=templates_dir)

    result2 = await db.execute(select(Refund).where(Refund.id == refund_id))
    updated_refund = result2.scalar_one()

    return templates.TemplateResponse(
        "refunds/_files_list.html",
        {"request": request, "refund": updated_refund, "user": user},
    )


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
