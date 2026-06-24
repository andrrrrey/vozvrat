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
    # Chat shows only public messages (visibility=all) for all roles.
    # Staff-only comments are shown exclusively in the comments block.
    q = select(Message).options(selectinload(Message.user)).where(
        Message.refund_id == refund_id,
        Message.visibility == MessageVisibility.all,
    ).order_by(Message.created_at.asc())
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

    # Notify the other party (client <-> staff) by email — best-effort.
    if visibility == MessageVisibility.all:
        await _notify_new_chat_message(refund_id, user, text, request, db)

    messages = await _load_messages(refund_id, user.role.value, db)

    response = templates.TemplateResponse(
        "refunds/_chat_messages.html",
        {"request": request, "messages": messages, "user": user},
    )
    # Tell the layout to refresh unread badges immediately.
    response.headers["HX-Trigger"] = "refreshBadges"
    return response


async def _notify_new_chat_message(
    refund_id: int,
    author,
    text: str,
    request: Request,
    db: AsyncSession,
) -> None:
    """Email the other side of a refund conversation about a new message. Never raises."""
    from app.models.user import User, UserRole
    from app.services.email_service import send_new_message_email

    try:
        result = await db.execute(
            select(Refund)
            .options(selectinload(Refund.client_user), selectinload(Refund.created_by))
            .where(Refund.id == refund_id)
        )
        refund = result.scalar_one_or_none()
        if not refund:
            return

        base_url = str(request.base_url).rstrip("/") if request else ""
        author_is_client = author.role.value == "client"

        if author_is_client:
            # Notify staff: the creator if they are staff/admin, otherwise all active staff/admin.
            recipients: list = []
            creator = refund.created_by
            if creator and creator.role in (UserRole.admin, UserRole.staff) and creator.is_active and creator.email:
                recipients = [creator]
            else:
                staff_result = await db.execute(
                    select(User).where(
                        User.role.in_([UserRole.admin, UserRole.staff]),
                        User.is_active == True,
                    )
                )
                recipients = [u for u in staff_result.scalars().all() if u.email]
            link = f"{base_url}/refunds/{refund_id}"
            for recipient in recipients:
                try:
                    await send_new_message_email(
                        db, to_email=recipient.email, recipient_name=recipient.full_name,
                        author_name=author.full_name, entity_display_id=refund.display_id,
                        entity_dative="возврату", message_text=text, link=link, from_client=True,
                    )
                except Exception as e:
                    logger.warning(f"Failed to send new-message email to {recipient.email}: {e}")
        else:
            # Staff/admin wrote — notify the client.
            client = refund.client_user
            if client and client.email:
                link = f"{base_url}/client/refunds/{refund_id}"
                try:
                    await send_new_message_email(
                        db, to_email=client.email, recipient_name=client.full_name,
                        author_name=author.full_name, entity_display_id=refund.display_id,
                        entity_dative="возврату", message_text=text, link=link, from_client=False,
                    )
                except Exception as e:
                    logger.warning(f"Failed to send new-message email to {client.email}: {e}")
    except Exception as e:
        logger.warning(f"_notify_new_chat_message failed for refund {refund_id}: {e}")


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

    # Handle @mentions: notify mentioned staff members by email (best-effort).
    mentions_raw = str(form.get("mentions", "")).strip()
    if mentions_raw:
        await _notify_mentioned_staff(mentions_raw, refund_id, user, text, request, db)

    comments = await _load_comments(refund_id, db)
    return templates.TemplateResponse(
        "refunds/_comments.html",
        {"request": request, "comments": comments, "user": user},
    )


async def _notify_mentioned_staff(
    mentions_raw: str,
    refund_id: int,
    author,
    comment_text: str,
    request: Request,
    db: AsyncSession,
) -> None:
    """Send email notifications to mentioned staff/admin users. Never raises."""
    from app.models.user import User, UserRole
    from app.services.email_service import send_comment_mention_email

    mention_ids = []
    for part in mentions_raw.split(","):
        part = part.strip()
        if part.isdigit():
            mention_ids.append(int(part))
    # Don't notify the author about their own mention.
    mention_ids = [mid for mid in set(mention_ids) if mid != author.id]
    if not mention_ids:
        return

    refund_result = await db.execute(select(Refund).where(Refund.id == refund_id))
    refund = refund_result.scalar_one_or_none()
    display_id = refund.display_id if refund else f"#{refund_id}"

    users_result = await db.execute(
        select(User).where(
            User.id.in_(mention_ids),
            User.role.in_([UserRole.admin, UserRole.staff]),
            User.is_active == True,
        )
    )
    recipients = users_result.scalars().all()

    base_url = str(request.base_url).rstrip("/") if request else ""

    for recipient in recipients:
        if not recipient.email:
            continue
        try:
            await send_comment_mention_email(
                db=db,
                to_email=recipient.email,
                recipient_name=recipient.full_name,
                author_name=author.full_name,
                refund_display_id=display_id,
                refund_id=refund_id,
                comment_text=comment_text,
                base_url=base_url,
            )
        except Exception as e:
            logger.warning(f"Failed to send mention email to {recipient.email}: {e}")
