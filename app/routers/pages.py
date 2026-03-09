import logging
from typing import Optional
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from sqlalchemy.orm import selectinload
import os

from app.database import get_db
from app.models.user import User
from app.models.refund import Refund, RefundStatus, RefundSource
from app.models.supplier import Supplier
from app.models.file_attachment import FileType
from app.services.auth import get_current_user, COOKIE_NAME

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

    in_progress = (await db.execute(
        select(func.count(Refund.id)).where(Refund.status == RefundStatus.in_progress)
    )).scalar() or 0

    new_from_mail = (await db.execute(
        select(func.count(Refund.id)).where(
            and_(Refund.source == RefundSource.email, Refund.status == RefundStatus.received)
        )
    )).scalar() or 0

    archive_count = (await db.execute(
        select(func.count(Refund.id)).where(Refund.status == RefundStatus.archive)
    )).scalar() or 0

    # Ожидают УКД: sent_to_supplier или completed без pdf_ukd файла
    from app.models.file_attachment import FileAttachment
    from sqlalchemy import not_, exists
    has_ukd = exists().where(
        and_(
            FileAttachment.refund_id == Refund.id,
            FileAttachment.file_type == FileType.pdf_ukd,
        )
    )
    awaiting_ukd = (await db.execute(
        select(func.count(Refund.id)).where(
            and_(
                Refund.status.in_([RefundStatus.sent_to_supplier, RefundStatus.completed]),
                not_(has_ukd),
            )
        )
    )).scalar() or 0

    return templates.TemplateResponse("statistics.html", {
        "request": request,
        "user": user,
        "in_progress": in_progress,
        "new_from_mail": new_from_mail,
        "awaiting_ukd": awaiting_ukd,
        "archive_count": archive_count,
    })


@router.get("/refunds")
async def refunds_page(
    request: Request,
    status: Optional[str] = None,
    supplier_id: Optional[int] = None,
    client_name: Optional[str] = None,
    date: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    user = await get_optional_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if user.role.value == "client":
        return RedirectResponse(url="/client/refunds", status_code=302)

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

    if conditions:
        query = query.where(and_(*conditions))

    query = query.limit(20)
    result = await db.execute(query)
    refunds = result.scalars().all()

    suppliers_result = await db.execute(select(Supplier).where(Supplier.is_active == True).order_by(Supplier.name))
    suppliers = suppliers_result.scalars().all()

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
    })


@router.get("/refunds/create")
async def create_refund_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_optional_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if user.role.value not in ("admin", "staff"):
        return RedirectResponse(url="/refunds", status_code=302)

    suppliers_result = await db.execute(
        select(Supplier).where(Supplier.is_active == True).order_by(Supplier.name)
    )
    suppliers = suppliers_result.scalars().all()

    return templates.TemplateResponse("refunds/create.html", {
        "request": request,
        "user": user,
        "suppliers": suppliers,
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

    return templates.TemplateResponse("refunds/card.html", {
        "request": request,
        "user": user,
        "refund": refund,
        "statuses": RefundStatus,
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


@router.get("/client/refunds")
async def client_refunds_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_optional_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if user.role.value != "client":
        return RedirectResponse(url="/refunds", status_code=302)

    result = await db.execute(
        select(Refund).options(
            selectinload(Refund.supplier),
            selectinload(Refund.items),
        ).where(Refund.client_user_id == user.id).order_by(Refund.created_at.desc())
    )
    refunds = result.scalars().all()

    return templates.TemplateResponse("client/list.html", {
        "request": request,
        "user": user,
        "refunds": refunds,
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

    ukd_file = next((f for f in refund.files if f.file_type.value == "pdf_ukd"), None)

    return templates.TemplateResponse("client/card.html", {
        "request": request,
        "user": user,
        "refund": refund,
        "ukd_file": ukd_file,
    })
