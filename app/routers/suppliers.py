import logging
from typing import List
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models.supplier import Supplier
from app.models.refund import Refund, RefundStatus
from app.schemas.supplier import SupplierCreate, SupplierUpdate, SupplierResponse
from app.services.auth import get_current_user, require_role

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/suppliers", tags=["suppliers"])


@router.get("", response_model=List[SupplierResponse])
async def list_suppliers(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    result = await db.execute(select(Supplier).order_by(Supplier.name))
    return result.scalars().all()


@router.post("", response_model=SupplierResponse)
async def create_supplier(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    if user.role.value not in ("admin", "staff"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    form = await request.form()
    name = str(form.get("name", "")).strip()
    email = str(form.get("email", "")).strip()

    if not name or not email:
        raise HTTPException(status_code=400, detail="Название и email обязательны")

    existing = await db.execute(select(Supplier).where(Supplier.name == name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Поставщик с таким названием уже существует")

    supplier = Supplier(name=name, email=email)
    db.add(supplier)
    await db.flush()
    await db.refresh(supplier)
    logger.info(f"Created supplier: {name}")
    return supplier


@router.put("/{supplier_id}", response_model=SupplierResponse)
async def update_supplier(
    supplier_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    if user.role.value not in ("admin", "staff"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    result = await db.execute(select(Supplier).where(Supplier.id == supplier_id))
    supplier = result.scalar_one_or_none()
    if not supplier:
        raise HTTPException(status_code=404, detail="Поставщик не найден")

    form = await request.form()
    name = str(form.get("name", "")).strip()
    email = str(form.get("email", "")).strip()
    is_active = form.get("is_active", "true").lower() in ("true", "1", "on")

    if name:
        supplier.name = name
    if email:
        supplier.email = email
    supplier.is_active = is_active

    await db.flush()
    await db.refresh(supplier)
    return supplier


@router.get("/form")
async def get_supplier_form(
    request: Request,
    supplier_id: int = None,
    db: AsyncSession = Depends(get_db),
):
    """Return HTML form partial for HTMX modal."""
    from fastapi.templating import Jinja2Templates
    import os
    templates_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
    templates = Jinja2Templates(directory=templates_dir)

    supplier = None
    if supplier_id:
        result = await db.execute(select(Supplier).where(Supplier.id == supplier_id))
        supplier = result.scalar_one_or_none()

    return templates.TemplateResponse(
        "suppliers/_form_modal.html",
        {"request": request, "supplier": supplier},
    )
