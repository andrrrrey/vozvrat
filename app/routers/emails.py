"""API endpoints for email management: auto-create toggle, load more."""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import os

from app.database import get_db
from app.services.auth import get_current_user
from app.services.settings_service import get_setting, set_setting
from app.models.refund import Refund, RefundSource

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/emails", tags=["emails"])

templates_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
templates = Jinja2Templates(directory=templates_dir)


async def _require_admin_or_staff(request: Request, db: AsyncSession):
    user = await get_current_user(request, db)
    if user.role.value not in ("admin", "staff"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    return user


@router.get("/auto-create/status")
async def auto_create_status(request: Request, db: AsyncSession = Depends(get_db)):
    await _require_admin_or_staff(request, db)
    enabled = (await get_setting(db, "mail_auto_create_enabled")).lower() == "true"
    since = await get_setting(db, "mail_auto_create_since")
    return JSONResponse({"enabled": enabled, "since": since})


@router.post("/auto-create/toggle")
async def auto_create_toggle(request: Request, db: AsyncSession = Depends(get_db)):
    await _require_admin_or_staff(request, db)

    current = (await get_setting(db, "mail_auto_create_enabled")).lower() == "true"
    new_state = not current

    await set_setting(db, "mail_auto_create_enabled", "true" if new_state else "false")

    if new_state:
        # Save the timestamp from which to start processing new emails
        await set_setting(db, "mail_auto_create_since", datetime.now(timezone.utc).isoformat())
        logger.info("Auto-create refunds from email: ENABLED")
    else:
        logger.info("Auto-create refunds from email: DISABLED")

    return JSONResponse({
        "enabled": new_state,
        "since": await get_setting(db, "mail_auto_create_since"),
    })


@router.post("/reprocess")
async def reprocess_emails(request: Request, db: AsyncSession = Depends(get_db)):
    """Manually re-run the auto-create pipeline over the last 20 emails.

    Works regardless of the UNSEEN flag (so it catches messages the scheduler missed),
    applies the normal sender/subject/XLS filters, and never touches emails that already
    have a refund.
    """
    await _require_admin_or_staff(request, db)

    from app.config import settings as cfg
    if not (cfg.MAIL_LOGIN and cfg.MAIL_PASSWORD):
        raise HTTPException(status_code=503, detail="Почта не настроена")

    from app.services.mail_import import reprocess_recent_emails
    try:
        result = await reprocess_recent_emails(db, limit=20)
    except Exception as e:
        logger.error(f"Manual reprocess failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Не удалось обработать письма")

    if result.get("error"):
        raise HTTPException(status_code=503, detail=result["error"])
    return JSONResponse(result)


@router.get("/load-more")
async def load_more_emails(
    request: Request,
    offset: int = 20,
    db: AsyncSession = Depends(get_db),
):
    """Load more emails from IMAP with offset, return partial HTML."""
    await _require_admin_or_staff(request, db)

    from app.services.mail_reader import fetch_recent_emails
    from app.config import settings as cfg

    if not cfg.MAIL_LOGIN or not cfg.MAIL_PASSWORD:
        return HTMLResponse("")

    try:
        import asyncio
        loop = asyncio.get_event_loop()
        emails = await loop.run_in_executor(None, lambda: fetch_recent_emails(limit=20, offset=offset))
    except Exception as e:
        logger.error(f"Failed to fetch more emails: {e}", exc_info=True)
        return HTMLResponse(f'<div class="text-red-500 text-sm p-4">Ошибка: {e}</div>')

    if not emails:
        return HTMLResponse(
            '<div class="text-gray-400 text-sm text-center py-4">Больше писем нет</div>'
        )

    # Get email UIDs that have linked refunds
    result = await db.execute(
        select(Refund.email_uid).where(
            Refund.source.in_([RefundSource.email, RefundSource.email_manual]),
            Refund.email_uid.isnot(None),
        )
    )
    refund_uids = set(r[0] for r in result.all())

    # Get processing status for displayed emails
    from app.models.mail_notification import MailNotification
    displayed_uids = [em["uid"] for em in emails if em.get("uid")]
    email_notifs: dict = {}
    if displayed_uids:
        notif_result = await db.execute(
            select(
                MailNotification.email_uid,
                MailNotification.processing_status,
                MailNotification.skip_reason,
            ).where(MailNotification.email_uid.in_(displayed_uids))
        )
        email_notifs = {
            r.email_uid: {"status": r.processing_status, "reason": r.skip_reason}
            for r in notif_result.all()
        }

    from app.services.settings_service import get_scheduler_status
    sched = await get_scheduler_status(db)

    return templates.TemplateResponse("emails/_email_cards.html", {
        "request": request,
        "emails": emails,
        "refund_uids": refund_uids,
        "email_notifs": email_notifs,
        "scheduler_alive": sched["alive"],
        "index_offset": offset,
    })
