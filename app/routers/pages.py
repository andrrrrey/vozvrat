import logging
from typing import Optional
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, exists
from sqlalchemy.orm import selectinload
import os

from app.database import get_db
from app.models.user import User
from app.models.refund import Refund, RefundStatus, RefundSource
from app.models.supplier import Supplier
from app.models.file_attachment import FileType
from app.services.auth import get_current_user, COOKIE_NAME
from app.routers.notifications import get_unread_per_refund, mark_refund_read

logger = logging.getLogger(__name__)
router = APIRouter(tags=["pages"])

templates_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
templates = Jinja2Templates(directory=templates_dir)


async def get_optional_user(request: Request, db: AsyncSession) -> Optional[User]:
    try:
        return await get_current_user(request, db)
    except Exception:
        return None


@router.get("/")
async def index(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_optional_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if user.role.value == "client":
        return RedirectResponse(url="/client/refunds", status_code=302)
    return RedirectResponse(url="/statistics", status_code=302)


@router.get("/login")
async def login_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_optional_user(request, db)
    if user:
        if user.role.value == "client":
            return RedirectResponse(url="/client/refunds", status_code=302)
        return RedirectResponse(url="/statistics", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request})


@router.get("/statistics")
async def statistics_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_optional_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if user.role.value == "client":
        return RedirectResponse(url="/client/refunds", status_code=302)

    received_count = (await db.execute(
        select(func.count(Refund.id)).where(Refund.status == RefundStatus.received)
    )).scalar() or 0

    approved_count = (await db.execute(
        select(func.count(Refund.id)).where(Refund.status == RefundStatus.approved)
    )).scalar() or 0

    new_from_mail = (await db.execute(
        select(func.count(Refund.id)).where(
            and_(Refund.source == RefundSource.email, Refund.status == RefundStatus.received)
        )
    )).scalar() or 0

    archive_count = (await db.execute(
        select(func.count(Refund.id)).where(Refund.status == RefundStatus.archive)
    )).scalar() or 0

    return templates.TemplateResponse("statistics.html", {
        "request": request,
        "user": user,
        "received_count": received_count,
        "approved_count": approved_count,
        "new_from_mail": new_from_mail,
        "archive_count": archive_count,
    })


@router.get("/refunds")
async def refunds_page(
    request: Request,
    status: Optional[str] = None,
    supplier_id: Optional[int] = None,
    client_name: Optional[str] = None,
    date: Optional[str] = None,
    article: Optional[str] = None,
    order_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    user = await get_optional_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if user.role.value == "client":
        return RedirectResponse(url="/client/refunds", status_code=302)

    from app.models.refund_item import RefundItem

    query = select(Refund).options(
        selectinload(Refund.supplier),
        selectinload(Refund.items),
    ).order_by(Refund.created_at.desc())

    conditions = []
    if status:
        try:
            conditions.append(Refund.status == RefundStatus(status))
        except ValueError:
            pass
    if supplier_id:
        conditions.append(Refund.supplier_id == supplier_id)
    if client_name:
        conditions.append(Refund.client_name.ilike(f"%{client_name}%"))
    if date:
        from datetime import datetime as dt
        try:
            d = dt.strptime(date, "%Y-%m-%d").date()
            conditions.append(func.date(Refund.created_at) == d)
        except ValueError:
            pass
    if article:
        conditions.append(
            exists().where(
                and_(
                    RefundItem.refund_id == Refund.id,
                    RefundItem.article.ilike(f"%{article}%"),
                )
            )
        )
    if order_id:
        conditions.append(Refund.order_id.ilike(f"%{order_id}%"))

    if conditions:
        query = query.where(and_(*conditions))

    query = query.limit(20)
    result = await db.execute(query)
    refunds = result.scalars().all()

    suppliers_result = await db.execute(select(Supplier).where(Supplier.is_active == True).order_by(Supplier.name))
    suppliers = suppliers_result.scalars().all()

    unread_counts = await get_unread_per_refund(user, db)

    return templates.TemplateResponse("refunds/list.html", {
        "request": request,
        "user": user,
        "refunds": refunds,
        "suppliers": suppliers,
        "statuses": RefundStatus,
        "current_status": status,
        "current_supplier_id": supplier_id,
        "current_client_name": client_name or "",
        "current_date": date or "",
        "current_article": article or "",
        "current_order_id": order_id or "",
        "unread_counts": unread_counts,
    })


@router.get("/refunds/create")
async def create_refund_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_optional_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if user.role.value not in ("admin", "staff"):
        return RedirectResponse(url="/refunds", status_code=302)

    from app.models.user import User, UserRole
    suppliers_result = await db.execute(
        select(Supplier).where(Supplier.is_active == True).order_by(Supplier.name)
    )
    suppliers = suppliers_result.scalars().all()

    clients_result = await db.execute(
        select(User).where(User.role == UserRole.client, User.is_active == True).order_by(User.full_name)
    )
    clients = clients_result.scalars().all()

    return templates.TemplateResponse("refunds/create.html", {
        "request": request,
        "user": user,
        "suppliers": suppliers,
        "clients": clients,
    })


@router.get("/refunds/{refund_id}")
async def refund_detail_page(
    refund_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_optional_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if user.role.value == "client":
        return RedirectResponse(url=f"/client/refunds/{refund_id}", status_code=302)

    result = await db.execute(
        select(Refund).options(
            selectinload(Refund.supplier),
            selectinload(Refund.items),
            selectinload(Refund.files),
            selectinload(Refund.client_user),
            selectinload(Refund.created_by),
        ).where(Refund.id == refund_id)
    )
    refund = result.scalar_one_or_none()

    if not refund:
        return templates.TemplateResponse("404.html", {"request": request, "user": user}, status_code=404)

    await mark_refund_read(refund_id, user.id, db)

    from app.models.user import User as UserModel, UserRole
    from app.services.settings_service import get_setting
    clients_result = await db.execute(
        select(UserModel).where(UserModel.role == UserRole.client, UserModel.is_active == True).order_by(UserModel.full_name)
    )
    clients = clients_result.scalars().all()
    auto_invite = (await get_setting(db, "auto_create_client_on_assign")).lower() == "true"

    import email as _email_mod
    client_email_for_invite = ""
    if refund.email_from:
        client_email_for_invite = _email_mod.utils.parseaddr(refund.email_from)[1]

    return templates.TemplateResponse("refunds/card.html", {
        "request": request,
        "user": user,
        "refund": refund,
        "statuses": RefundStatus,
        "clients": clients,
        "auto_invite_enabled": auto_invite,
        "client_email_for_invite": client_email_for_invite,
    })


@router.get("/suppliers")
async def suppliers_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_optional_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if user.role.value not in ("admin", "staff"):
        return RedirectResponse(url="/refunds", status_code=302)

    result = await db.execute(select(Supplier).order_by(Supplier.name))
    suppliers = result.scalars().all()

    # Count active refunds per supplier
    refund_counts = {}
    for supplier in suppliers:
        cnt_result = await db.execute(
            select(func.count(Refund.id)).where(
                and_(
                    Refund.supplier_id == supplier.id,
                    Refund.status.notin_([RefundStatus.archive, RefundStatus.completed])
                )
            )
        )
        refund_counts[supplier.id] = cnt_result.scalar() or 0

    return templates.TemplateResponse("suppliers/list.html", {
        "request": request,
        "user": user,
        "suppliers": suppliers,
        "refund_counts": refund_counts,
    })


@router.get("/users")
async def users_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_optional_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if user.role.value != "admin":
        return RedirectResponse(url="/refunds", status_code=302)

    result = await db.execute(select(User).order_by(User.created_at.desc()))
    users = result.scalars().all()

    return templates.TemplateResponse("users/list.html", {
        "request": request,
        "user": user,
        "users": users,
    })


@router.get("/emails")
async def emails_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_optional_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if user.role.value not in ("admin", "staff"):
        return RedirectResponse(url="/refunds", status_code=302)

    from app.services.mail_reader import fetch_recent_emails
    from app.services.settings_service import get_setting
    from app.config import settings as cfg

    imap_configured = bool(cfg.MAIL_LOGIN and cfg.MAIL_PASSWORD)
    emails = []
    fetch_error = None
    auto_create_enabled = False
    refund_uids = set()

    if imap_configured:
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            emails = await loop.run_in_executor(None, lambda: fetch_recent_emails(limit=20))
        except Exception as e:
            logger.error(f"Failed to fetch emails for viewer: {e}", exc_info=True)
            fetch_error = str(e)

        auto_create_enabled = (await get_setting(db, "mail_auto_create_enabled")).lower() == "true"

        # Get email UIDs that have linked refunds (both auto and manual)
        result = await db.execute(
            select(Refund.email_uid).where(
                Refund.source.in_([RefundSource.email, RefundSource.email_manual]),
                Refund.email_uid.isnot(None),
            )
        )
        refund_uids = set(r[0] for r in result.all())

    return templates.TemplateResponse("emails/list.html", {
        "request": request,
        "user": user,
        "emails": emails,
        "imap_configured": imap_configured,
        "fetch_error": fetch_error,
        "auto_create_enabled": auto_create_enabled,
        "refund_uids": refund_uids,
        "index_offset": 0,
    })


@router.get("/settings")
async def settings_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_optional_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if user.role.value != "admin":
        return RedirectResponse(url="/refunds", status_code=302)

    from app.services.settings_service import get_all_settings
    app_settings = await get_all_settings(db)

    return templates.TemplateResponse("settings/index.html", {
        "request": request,
        "user": user,
        "app_settings": app_settings,
    })


@router.get("/client/refunds")
async def client_refunds_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_optional_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if user.role.value != "client":
        return RedirectResponse(url="/refunds", status_code=302)

    from sqlalchemy import or_
    result = await db.execute(
        select(Refund).options(
            selectinload(Refund.supplier),
            selectinload(Refund.items),
        ).where(
            or_(
                Refund.client_user_id == user.id,
                and_(Refund.client_user_id.is_(None), Refund.client_name == user.full_name),
            )
        ).order_by(Refund.created_at.desc())
    )
    refunds = result.scalars().all()

    unread_counts = await get_unread_per_refund(user, db)

    return templates.TemplateResponse("client/list.html", {
        "request": request,
        "user": user,
        "refunds": refunds,
        "unread_counts": unread_counts,
    })


@router.get("/client/refunds/create")
async def client_create_refund_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_optional_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if user.role.value != "client":
        return RedirectResponse(url="/refunds/create", status_code=302)

    return templates.TemplateResponse("client/create.html", {
        "request": request,
        "user": user,
    })


@router.get("/client/refunds/{refund_id}")
async def client_refund_detail(
    refund_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_optional_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if user.role.value != "client":
        return RedirectResponse(url=f"/refunds/{refund_id}", status_code=302)

    result = await db.execute(
        select(Refund).options(
            selectinload(Refund.supplier),
            selectinload(Refund.items),
            selectinload(Refund.files),
        ).where(
            Refund.id == refund_id,
            Refund.client_user_id == user.id
        )
    )
    refund = result.scalar_one_or_none()

    if not refund:
        return RedirectResponse(url="/client/refunds", status_code=302)

    await mark_refund_read(refund_id, user.id, db)

    ukd_file = next((f for f in refund.files if f.file_type.value == "pdf_ukd"), None)

    return templates.TemplateResponse("client/card.html", {
        "request": request,
        "user": user,
        "refund": refund,
        "ukd_file": ukd_file,
    })
