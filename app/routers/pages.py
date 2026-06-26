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

    rejected_count = (await db.execute(
        select(func.count(Refund.id)).where(Refund.status == RefundStatus.rejected)
    )).scalar() or 0

    waiting_count = (await db.execute(
        select(func.count(Refund.id)).where(Refund.status == RefundStatus.waiting_for_part)
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
        "rejected_count": rejected_count,
        "waiting_count": waiting_count,
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
    page: int = 1,
    db: AsyncSession = Depends(get_db),
):
    user = await get_optional_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if user.role.value == "client":
        return RedirectResponse(url="/client/refunds", status_code=302)

    from app.routers.refunds import build_refund_filter

    per_page = 20
    if page < 1:
        page = 1

    query = select(Refund).options(
        selectinload(Refund.supplier),
        selectinload(Refund.items),
    ).order_by(Refund.created_at.desc())

    # Same filter logic as the /api/refunds/table partial, kept in sync via build_refund_filter.
    query = build_refund_filter(query, status, supplier_id, client_name, date, article, order_id)

    count_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar() or 0
    total_pages = max(1, (total + per_page - 1) // per_page)
    if page > total_pages:
        page = total_pages

    query = query.offset((page - 1) * per_page).limit(per_page)
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
        "page": page,
        "total_pages": total_pages,
        "total": total,
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
    from app.models.user_client_id import UserClientId
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

    client_user_ext_ids = []
    if refund.client_user_id:
        cids_result = await db.execute(
            select(UserClientId).where(UserClientId.user_id == refund.client_user_id).order_by(UserClientId.id)
        )
        client_user_ext_ids = [r.client_id for r in cids_result.scalars().all()]

    suppliers_result = await db.execute(
        select(Supplier).where(Supplier.is_active == True).order_by(Supplier.name)
    )
    suppliers = suppliers_result.scalars().all()

    # Staff members available for @mentions in comments
    staff_result = await db.execute(
        select(UserModel).where(
            UserModel.role.in_([UserRole.admin, UserRole.staff]),
            UserModel.is_active == True,
        ).order_by(UserModel.full_name)
    )
    staff_users = staff_result.scalars().all()

    # Split attachments into public (client-visible) and internal (staff-only)
    public_files = sorted([f for f in refund.files if not f.is_internal], key=lambda f: f.id)
    internal_files = sorted([f for f in refund.files if f.is_internal], key=lambda f: f.id)

    return templates.TemplateResponse("refunds/card.html", {
        "request": request,
        "user": user,
        "refund": refund,
        "statuses": RefundStatus,
        "clients": clients,
        "suppliers": suppliers,
        "auto_invite_enabled": auto_invite,
        "client_email_for_invite": client_email_for_invite,
        "client_user_ext_ids": client_user_ext_ids,
        "public_files": public_files,
        "internal_files": internal_files,
        "staff_users": staff_users,
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
                    Refund.status != RefundStatus.archive
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
    from app.services.settings_service import get_setting, get_mail_config

    mc = await get_mail_config(db)
    imap_configured = mc.configured
    emails = []
    fetch_error = None
    auto_create_enabled = False
    refund_uids = set()
    email_notifs: dict = {}
    scheduler_alive = False
    scheduler_last_check = None
    scheduler_last_check_str = None

    if imap_configured:
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            emails = await loop.run_in_executor(
                None,
                lambda: fetch_recent_emails(
                    limit=20, host=mc.host, port=mc.port,
                    login=mc.login, password=mc.password, folder=mc.folder,
                ),
            )
        except Exception as e:
            logger.error(f"Failed to fetch emails for viewer: {e}", exc_info=True)
            fetch_error = str(e)

        auto_create_enabled = (await get_setting(db, "mail_auto_create_enabled")).lower() == "true"

        from app.services.settings_service import get_scheduler_status
        sched = await get_scheduler_status(db)
        scheduler_alive = sched["alive"]
        scheduler_last_check = sched["last_check"]
        if scheduler_last_check:
            try:
                from zoneinfo import ZoneInfo
                scheduler_last_check_str = scheduler_last_check.astimezone(
                    ZoneInfo("Europe/Moscow")
                ).strftime("%d.%m.%Y %H:%M")
            except Exception:
                scheduler_last_check_str = scheduler_last_check.strftime("%d.%m.%Y %H:%M UTC")

        # Get email UIDs that have linked refunds (both auto and manual)
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

    return templates.TemplateResponse("emails/list.html", {
        "request": request,
        "user": user,
        "emails": emails,
        "imap_configured": imap_configured,
        "fetch_error": fetch_error,
        "auto_create_enabled": auto_create_enabled,
        "refund_uids": refund_uids,
        "email_notifs": email_notifs,
        "scheduler_alive": scheduler_alive,
        "scheduler_last_check": scheduler_last_check,
        "scheduler_last_check_str": scheduler_last_check_str,
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

    # Clients never see internal (staff-only) files.
    client_files = [f for f in refund.files if not f.is_internal]
    ukd_file = next((f for f in client_files if f.file_type.value == "pdf_ukd"), None)

    return templates.TemplateResponse("client/card.html", {
        "request": request,
        "user": user,
        "refund": refund,
        "client_files": client_files,
        "ukd_file": ukd_file,
    })


# ---------------------------------------------------------------------------
# Запросы (Requests) — страницы
# ---------------------------------------------------------------------------

@router.get("/requests")
async def requests_page(
    request: Request,
    status: Optional[str] = None,
    executor_id: Optional[int] = None,
    client_name: Optional[str] = None,
    date: Optional[str] = None,
    article: Optional[str] = None,
    order_id: Optional[str] = None,
    page: int = 1,
    db: AsyncSession = Depends(get_db),
):
    user = await get_optional_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if user.role.value == "client":
        return RedirectResponse(url="/client/requests", status_code=302)

    from app.models.request import Request as RequestModel, RequestStatus
    from app.models.user import User as UserModel, UserRole
    from app.routers.requests import build_request_filter
    from app.routers.notifications import get_unread_per_request

    per_page = 20
    if page < 1:
        page = 1

    query = select(RequestModel).options(
        selectinload(RequestModel.items),
        selectinload(RequestModel.executor),
    ).order_by(RequestModel.created_at.desc())

    query = build_request_filter(query, status, executor_id, client_name, date, article, order_id)

    count_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar() or 0
    total_pages = max(1, (total + per_page - 1) // per_page)
    if page > total_pages:
        page = total_pages

    query = query.offset((page - 1) * per_page).limit(per_page)
    result = await db.execute(query)
    requests_list = result.scalars().all()

    executors_result = await db.execute(
        select(UserModel).where(
            UserModel.role.in_([UserRole.admin, UserRole.staff]),
            UserModel.is_active == True,
        ).order_by(UserModel.full_name)
    )
    executors = executors_result.scalars().all()

    unread_counts = await get_unread_per_request(user, db)

    return templates.TemplateResponse("requests/list.html", {
        "request": request,
        "user": user,
        "requests": requests_list,
        "executors": executors,
        "statuses": RequestStatus,
        "current_status": status,
        "current_executor_id": executor_id,
        "current_client_name": client_name or "",
        "current_date": date or "",
        "current_article": article or "",
        "current_order_id": order_id or "",
        "unread_counts": unread_counts,
        "page": page,
        "total_pages": total_pages,
        "total": total,
    })


@router.get("/requests/create")
async def create_request_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_optional_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if user.role.value not in ("admin", "staff"):
        return RedirectResponse(url="/requests", status_code=302)

    from app.models.user import User as UserModel, UserRole
    clients_result = await db.execute(
        select(UserModel).where(UserModel.role == UserRole.client, UserModel.is_active == True).order_by(UserModel.full_name)
    )
    clients = clients_result.scalars().all()
    executors_result = await db.execute(
        select(UserModel).where(
            UserModel.role.in_([UserRole.admin, UserRole.staff]),
            UserModel.is_active == True,
        ).order_by(UserModel.full_name)
    )
    executors = executors_result.scalars().all()

    from app.models.request import RequestSubject, SUBJECT_LABELS
    subjects = [(s.value, SUBJECT_LABELS[s]) for s in RequestSubject]

    return templates.TemplateResponse("requests/create.html", {
        "request": request,
        "user": user,
        "clients": clients,
        "executors": executors,
        "subjects": subjects,
    })


@router.get("/requests/{request_id}")
async def request_detail_page(request_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_optional_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if user.role.value == "client":
        return RedirectResponse(url=f"/client/requests/{request_id}", status_code=302)

    from app.models.request import Request as RequestModel, RequestStatus
    from app.models.user import User as UserModel, UserRole
    from app.routers.notifications import mark_request_read

    result = await db.execute(
        select(RequestModel).options(
            selectinload(RequestModel.items),
            selectinload(RequestModel.files),
            selectinload(RequestModel.client_user),
            selectinload(RequestModel.executor),
            selectinload(RequestModel.created_by),
        ).where(RequestModel.id == request_id)
    )
    req = result.scalar_one_or_none()
    if not req:
        return templates.TemplateResponse("404.html", {"request": request, "user": user}, status_code=404)

    await mark_request_read(request_id, user.id, db)

    executors_result = await db.execute(
        select(UserModel).where(
            UserModel.role.in_([UserRole.admin, UserRole.staff]),
            UserModel.is_active == True,
        ).order_by(UserModel.full_name)
    )
    executors = executors_result.scalars().all()

    staff_users = executors  # для @упоминаний во внутренних комментариях

    public_files = sorted([f for f in req.files if not f.is_internal], key=lambda f: f.id)
    internal_files = sorted([f for f in req.files if f.is_internal], key=lambda f: f.id)

    return templates.TemplateResponse("requests/card.html", {
        "request": request,
        "user": user,
        "req": req,
        "statuses": RequestStatus,
        "executors": executors,
        "staff_users": staff_users,
        "public_files": public_files,
        "internal_files": internal_files,
    })


@router.get("/client/requests")
async def client_requests_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_optional_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if user.role.value != "client":
        return RedirectResponse(url="/requests", status_code=302)

    from app.models.request import Request as RequestModel
    from app.routers.notifications import get_unread_per_request

    result = await db.execute(
        select(RequestModel).options(
            selectinload(RequestModel.items),
            selectinload(RequestModel.executor),
        ).where(
            RequestModel.client_user_id == user.id
        ).order_by(RequestModel.created_at.desc())
    )
    requests_list = result.scalars().all()
    unread_counts = await get_unread_per_request(user, db)

    return templates.TemplateResponse("client/requests_list.html", {
        "request": request,
        "user": user,
        "requests": requests_list,
        "unread_counts": unread_counts,
    })


@router.get("/client/requests/create")
async def client_create_request_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_optional_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if user.role.value != "client":
        return RedirectResponse(url="/requests/create", status_code=302)

    from app.models.request import RequestSubject, SUBJECT_LABELS
    subjects = [(s.value, SUBJECT_LABELS[s]) for s in RequestSubject]

    return templates.TemplateResponse("client/requests_create.html", {
        "request": request,
        "user": user,
        "subjects": subjects,
    })


@router.get("/client/requests/{request_id}")
async def client_request_detail(request_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_optional_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if user.role.value != "client":
        return RedirectResponse(url=f"/requests/{request_id}", status_code=302)

    from app.models.request import Request as RequestModel
    from app.routers.notifications import mark_request_read

    result = await db.execute(
        select(RequestModel).options(
            selectinload(RequestModel.items),
            selectinload(RequestModel.files),
            selectinload(RequestModel.executor),
        ).where(
            RequestModel.id == request_id,
            RequestModel.client_user_id == user.id,
        )
    )
    req = result.scalar_one_or_none()
    if not req:
        return RedirectResponse(url="/client/requests", status_code=302)

    await mark_request_read(request_id, user.id, db)
    client_files = [f for f in req.files if not f.is_internal]

    return templates.TemplateResponse("client/requests_card.html", {
        "request": request,
        "user": user,
        "req": req,
        "client_files": client_files,
    })
