"""Chat messages and staff comments API for refund cards."""
import logging
import os
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.refund import Refund
from app.models.message import Message, MessageVisibility
from app.services.auth import get_current_user
from app.routers.notifications import mark_refund_read

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/refunds", tags=["messages"])

templates_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
templates = Jinja2Templates(directory=templates_dir)


async def _load_messages(refund_id: int, user_role: str, db: AsyncSession) -> list[Message]:
    q = select(Message).options(selectinload(Message.user)).where(
        Message.refund_id == refund_id
    ).order_by(Message.created_at.asc())
    if user_role == "client":
        q = q.where(Message.visibility == MessageVisibility.all)
    result = await db.execute(q)
    return list(result.scalars().all())


@router.get("/{refund_id}/messages", response_class=HTMLResponse)
async def get_messages(
    refund_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)

    # Validate refund access
    q = select(Refund).where(Refund.id == refund_id)
    if user.role.value == "client":
        q = q.where(Refund.client_user_id == user.id)
    result = await db.execute(q)
    refund = result.scalar_one_or_none()
    if not refund:
        raise HTTPException(status_code=404, detail="Возврат не найден")

    messages = await _load_messages(refund_id, user.role.value, db)
    await mark_refund_read(refund_id, user.id, db)

    return templates.TemplateResponse(
        "refunds/_chat_messages.html",
        {"request": request, "messages": messages, "user": user},
    )


@router.post("/{refund_id}/messages", response_class=HTMLResponse)
async def send_message(
    refund_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)

    # Validate refund access
    q = select(Refund).where(Refund.id == refund_id)
    if user.role.value == "client":
        q = q.where(Refund.client_user_id == user.id)
    result = await db.execute(q)
    refund = result.scalar_one_or_none()
    if not refund:
        raise HTTPException(status_code=404, detail="Возврат не найден")

    form = await request.form()
    text = str(form.get("text", "")).strip()
    if not text:
        raise HTTPException(status_code=400, detail="Текст сообщения пустой")

    # Clients always send visible-to-all; staff can choose staff_only
    visibility_raw = str(form.get("visibility", "all"))
    if user.role.value == "client":
        visibility = MessageVisibility.all
    else:
        try:
            visibility = MessageVisibility(visibility_raw)
        except ValueError:
            visibility = MessageVisibility.all

    msg = Message(
        refund_id=refund_id,
        user_id=user.id,
        text=text,
        visibility=visibility,
    )
    db.add(msg)
    await db.commit()
    # Mark new message as read by sender immediately
    await mark_refund_read(refund_id, user.id, db)

    messages = await _load_messages(refund_id, user.role.value, db)

    return templates.TemplateResponse(
        "refunds/_chat_messages.html",
        {"request": request, "messages": messages, "user": user},
    )


async def _load_comments(refund_id: int, db: AsyncSession) -> list[Message]:
    q = select(Message).options(selectinload(Message.user)).where(
        Message.refund_id == refund_id,
        Message.visibility == MessageVisibility.staff_only,
    ).order_by(Message.created_at.asc())
    result = await db.execute(q)
    return list(result.scalars().all())


@router.get("/{refund_id}/comments", response_class=HTMLResponse)
async def get_comments(
    refund_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    if user.role.value not in ("admin", "staff"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    q = select(Refund).where(Refund.id == refund_id)
    result = await db.execute(q)
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Возврат не найден")

    comments = await _load_comments(refund_id, db)
    await mark_refund_read(refund_id, user.id, db)
    return templates.TemplateResponse(
        "refunds/_comments.html",
        {"request": request, "comments": comments, "user": user},
    )


@router.post("/{refund_id}/comments", response_class=HTMLResponse)
async def post_comment(
    refund_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    if user.role.value not in ("admin", "staff"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    q = select(Refund).where(Refund.id == refund_id)
    result = await db.execute(q)
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Возврат не найден")

    form = await request.form()
    text = str(form.get("text", "")).strip()
    if not text:
        raise HTTPException(status_code=400, detail="Текст комментария пустой")

    msg = Message(
        refund_id=refund_id,
        user_id=user.id,
        text=text,
        visibility=MessageVisibility.staff_only,
    )
    db.add(msg)
    await db.commit()
    await mark_refund_read(refund_id, user.id, db)

    comments = await _load_comments(refund_id, db)
    return templates.TemplateResponse(
        "refunds/_comments.html",
        {"request": request, "comments": comments, "user": user},
    )
