"""Notifications API — unread message counters and bell dropdown."""
import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, exists, not_
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.message import Message, MessageVisibility
from app.models.message_read import MessageRead
from app.models.refund import Refund
from app.models.user import User
from app.services.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/notifications", tags=["notifications"])

templates_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
templates = Jinja2Templates(directory=templates_dir)


def _unread_condition(user_id: int):
    """SQLAlchemy condition: message has no MessageRead entry for this user."""
    return not_(
        exists().where(
            and_(
                MessageRead.message_id == Message.id,
                MessageRead.user_id == user_id,
            )
        )
    )


async def get_unread_total(user: User, db: AsyncSession) -> int:
    """Total unread count: messages + unread email notifications (for staff/admin)."""
    q = (
        select(func.count(Message.id))
        .where(
            _unread_condition(user.id),
            Message.user_id != user.id,
        )
    )
    if user.role.value == "client":
        q = q.where(
            Message.visibility == MessageVisibility.all,
            exists().where(
                and_(
                    Refund.id == Message.refund_id,
                    Refund.client_user_id == user.id,
                )
            ),
        )
    msg_count = (await db.execute(q)).scalar() or 0

    if user.role.value != "client":
        from app.models.mail_notification import MailNotification
        email_count = (await db.execute(
            select(func.count(MailNotification.id)).where(MailNotification.is_read == False)
        )).scalar() or 0
        return msg_count + email_count

    return msg_count


async def get_unread_per_refund(user: User, db: AsyncSession) -> dict[int, int]:
    """Per-refund unread message counts."""
    q = (
        select(Message.refund_id, func.count(Message.id))
        .where(
            _unread_condition(user.id),
            Message.user_id != user.id,
        )
        .group_by(Message.refund_id)
    )
    if user.role.value == "client":
        q = q.where(
            Message.visibility == MessageVisibility.all,
            exists().where(
                and_(
                    Refund.id == Message.refund_id,
                    Refund.client_user_id == user.id,
                )
            ),
        )
    result = await db.execute(q)
    return {row[0]: row[1] for row in result.all()}


async def mark_refund_read(refund_id: int, user_id: int, db: AsyncSession) -> None:
    """Insert MessageRead rows for all messages in a refund not yet read by this user."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    msg_ids_result = await db.execute(
        select(Message.id).where(Message.refund_id == refund_id)
    )
    msg_ids = msg_ids_result.scalars().all()
    if not msg_ids:
        return

    stmt = pg_insert(MessageRead).values(
        [{"message_id": mid, "user_id": user_id} for mid in msg_ids]
    ).on_conflict_do_nothing()
    await db.execute(stmt)
    await db.commit()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/nav-badge", response_class=HTMLResponse)
async def nav_badge(request: Request, db: AsyncSession = Depends(get_db)):
    """HTMX partial: badge with total unread count for sidebar nav."""
    try:
        user = await get_current_user(request, db)
    except Exception:
        return HTMLResponse("")
    count = await get_unread_total(user, db)
    if count > 0:
        label = str(count) if count < 100 else "99+"
        return HTMLResponse(
            f'<span id="nav-unread-badge" '
            f'class="ml-auto bg-red-500 text-white text-[10px] font-bold '
            f'rounded-full px-1.5 py-0.5 min-w-[18px] text-center leading-none">'
            f'{label}</span>'
        )
    return HTMLResponse('<span id="nav-unread-badge"></span>')


@router.get("/bell-count", response_class=HTMLResponse)
async def bell_count(request: Request, db: AsyncSession = Depends(get_db)):
    """HTMX partial: badge number shown on bell icon."""
    try:
        user = await get_current_user(request, db)
    except Exception:
        return HTMLResponse("")
    if user.role.value == "client":
        return HTMLResponse("")
    count = await get_unread_total(user, db)
    if count > 0:
        label = str(count) if count < 100 else "99+"
        return HTMLResponse(
            f'<span id="bell-badge" '
            f'class="absolute -top-1 -right-1 bg-red-500 text-white text-[9px] '
            f'font-bold rounded-full px-1 py-0.5 min-w-[16px] text-center leading-none">'
            f'{label}</span>'
        )
    return HTMLResponse('<span id="bell-badge"></span>')


@router.get("/recent", response_class=HTMLResponse)
async def recent_notifications(request: Request, db: AsyncSession = Depends(get_db)):
    """HTMX partial: recent unread messages list for bell dropdown."""
    try:
        user = await get_current_user(request, db)
    except Exception:
        return HTMLResponse("<div class='p-4 text-sm text-gray-400'>Нет доступа</div>")
    if user.role.value == "client":
        return HTMLResponse("")

    q = (
        select(Message)
        .options(selectinload(Message.user), selectinload(Message.refund))
        .where(
            _unread_condition(user.id),
            Message.user_id != user.id,
        )
        .order_by(Message.created_at.desc())
        .limit(10)
    )
    result = await db.execute(q)
    messages = result.scalars().all()

    from app.models.mail_notification import MailNotification
    notif_q = (
        select(MailNotification)
        .options(selectinload(MailNotification.refund))
        .where(MailNotification.is_read == False)
        .order_by(MailNotification.created_at.desc())
        .limit(8)
    )
    notif_result = await db.execute(notif_q)
    email_notifs = notif_result.scalars().all()

    return templates.TemplateResponse(
        "notifications/_bell_list.html",
        {"request": request, "messages": messages, "email_notifs": email_notifs, "user": user},
    )


@router.post("/mark-emails-read", response_class=JSONResponse)
async def mark_emails_read(request: Request, db: AsyncSession = Depends(get_db)):
    """Mark all email notifications as read."""
    try:
        user = await get_current_user(request, db)
    except Exception:
        return JSONResponse({"ok": False})
    if user.role.value == "client":
        return JSONResponse({"ok": False})
    from app.models.mail_notification import MailNotification
    from sqlalchemy import update as sa_update
    await db.execute(sa_update(MailNotification).values(is_read=True))
    await db.commit()
    return JSONResponse({"ok": True})


@router.post("/mark-read/{refund_id}", response_class=JSONResponse)
async def mark_read(
    refund_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Mark all messages in a refund as read for the current user."""
    try:
        user = await get_current_user(request, db)
    except Exception:
        return JSONResponse({"ok": False})
    await mark_refund_read(refund_id, user.id, db)
    return JSONResponse({"ok": True})
