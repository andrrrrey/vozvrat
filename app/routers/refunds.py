import logging
from datetime import datetime, date
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse, HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from sqlalchemy.orm import selectinload
from app.database import get_db
from app.models.refund import Refund, RefundStatus, RefundSource
from app.models.refund_item import RefundItem
from app.models.supplier import Supplier
from app.schemas.refund import RefundResponse, RefundStatusUpdate
from app.services.auth import get_current_user
from app.services.file_service import save_file

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/refunds", tags=["refunds"])


async def generate_display_id(db: AsyncSession) -> str:
    result = await db.execute(select(func.count(Refund.id)))
    count = result.scalar() or 0
    # Use a sequence-like approach based on id
    result2 = await db.execute(select(func.max(Refund.id)))
    max_id = result2.scalar() or 0
    return f"#{10001 + max_id}"


def build_refund_filter(query, status: Optional[str], supplier_id: Optional[int],
                        client_name: Optional[str], date_from: Optional[str]):
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
    if date_from:
        try:
            d = datetime.strptime(date_from, "%Y-%m-%d").date()
            conditions.append(func.date(Refund.created_at) == d)
        except ValueError:
            pass
    if conditions:
        query = query.where(and_(*conditions))
    return query


@router.get("")
async def list_refunds(
    request: Request,
    status: Optional[str] = None,
    supplier_id: Optional[int] = None,
    client_name: Optional[str] = None,
    date_from: Optional[str] = None,
    page: int = 1,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    per_page = 20

    query = select(Refund).options(
        selectinload(Refund.supplier),
        selectinload(Refund.items),
    ).order_by(Refund.created_at.desc())

    if user.role.value == "client":
        query = query.where(Refund.client_user_id == user.id)

    query = build_refund_filter(query, status, supplier_id, client_name, date_from)

    count_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    query = query.offset((page - 1) * per_page).limit(per_page)
    result = await db.execute(query)
    refunds = result.scalars().all()

    return {"refunds": refunds, "total": total, "page": page, "per_page": per_page}


@router.get("/table")
async def refunds_table_partial(
    request: Request,
    status: Optional[str] = None,
    supplier_id: Optional[int] = None,
    client_name: Optional[str] = None,
    date: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Return HTML partial (tbody) for HTMX table update."""
    from fastapi.templating import Jinja2Templates
    import os
    templates_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
    templates = Jinja2Templates(directory=templates_dir)

    user = await get_current_user(request, db)

    query = select(Refund).options(
        selectinload(Refund.supplier),
        selectinload(Refund.items),
    ).order_by(Refund.created_at.desc())

    if user.role.value == "client":
        query = query.where(Refund.client_user_id == user.id)

    query = build_refund_filter(query, status, supplier_id, client_name, date)
    query = query.limit(20)

    result = await db.execute(query)
    refunds = result.scalars().all()

    return templates.TemplateResponse(
        "refunds/_table_rows.html",
        {"request": request, "refunds": refunds, "user": user},
    )


@router.post("")
async def create_refund(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    if user.role.value not in ("admin", "staff"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    form = await request.form()
    client_name = str(form.get("client_name", "")).strip()
    if not client_name:
        raise HTTPException(status_code=400, detail="Имя клиента обязательно")

    supplier_id_raw = form.get("supplier_id")
    supplier_id = int(supplier_id_raw) if supplier_id_raw and str(supplier_id_raw).isdigit() else None
    order_id = str(form.get("order_id", "")).strip() or None
    reason = str(form.get("reason", "")).strip() or None
    article = str(form.get("article", "")).strip()
    brand = str(form.get("brand", "")).strip() or None
    quantity_raw = form.get("quantity", "1")
    price_raw = form.get("price", "0")

    try:
        quantity = int(quantity_raw)
    except (ValueError, TypeError):
        quantity = 1

    try:
        from decimal import Decimal
        price = Decimal(str(price_raw).replace(",", "."))
    except Exception:
        price = Decimal("0")

    display_id = await generate_display_id(db)

    refund = Refund(
        display_id=display_id,
        status=RefundStatus.received,
        source=RefundSource.manual,
        client_name=client_name,
        supplier_id=supplier_id,
        order_id=order_id,
        reason=reason,
        created_by_id=user.id,
    )
    db.add(refund)
    await db.flush()
    await db.refresh(refund)

    if article:
        item = RefundItem(
            refund_id=refund.id,
            article=article,
            brand=brand,
            quantity=quantity,
            price=price,
        )
        db.add(item)

    # Handle file uploads
    files = request.state.__dict__.get("_files", [])
    form_files = await request.form()
    for key, value in form_files.multi_items():
        if hasattr(value, "filename") and value.filename:
            await save_file(value, refund.id, db, uploaded_by_id=user.id)

    await db.flush()

    from fastapi.responses import RedirectResponse
    return RedirectResponse(url=f"/refunds/{refund.id}", status_code=302)


@router.get("/{refund_id}", response_model=RefundResponse)
async def get_refund(
    refund_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)

    query = select(Refund).options(
        selectinload(Refund.supplier),
        selectinload(Refund.items),
        selectinload(Refund.files),
        selectinload(Refund.client_user),
        selectinload(Refund.created_by),
    ).where(Refund.id == refund_id)

    if user.role.value == "client":
        query = query.where(Refund.client_user_id == user.id)

    result = await db.execute(query)
    refund = result.scalar_one_or_none()

    if not refund:
        raise HTTPException(status_code=404, detail="Возврат не найден")

    return refund


@router.post("/{refund_id}/status")
async def update_status(
    refund_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    if user.role.value not in ("admin", "staff"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    form = await request.form()
    new_status_str = str(form.get("status", ""))

    try:
        new_status = RefundStatus(new_status_str)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Неверный статус: {new_status_str}")

    result = await db.execute(
        select(Refund).options(
            selectinload(Refund.supplier),
            selectinload(Refund.items),
            selectinload(Refund.files),
        ).where(Refund.id == refund_id)
    )
    refund = result.scalar_one_or_none()

    if not refund:
        raise HTTPException(status_code=404, detail="Возврат не найден")

    refund.status = new_status
    await db.flush()
    await db.refresh(refund)

    from fastapi.templating import Jinja2Templates
    import os
    templates_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
    templates = Jinja2Templates(directory=templates_dir)

    return templates.TemplateResponse(
        "refunds/_status_section.html",
        {"request": request, "refund": refund, "user": user},
    )
