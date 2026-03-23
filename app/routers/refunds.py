import logging
import os
from datetime import datetime, date
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse, HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, exists
from sqlalchemy.orm import selectinload
from app.database import get_db
from app.models.refund import Refund, RefundStatus, RefundSource
from app.models.refund_item import RefundItem
from app.models.supplier import Supplier
from app.models.user import User
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
                        client_name: Optional[str], date_from: Optional[str],
                        article: Optional[str] = None, order_id: Optional[str] = None):
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
    return query


@router.get("")
async def list_refunds(
    request: Request,
    status: Optional[str] = None,
    supplier_id: Optional[str] = None,
    client_name: Optional[str] = None,
    date_from: Optional[str] = None,
    page: int = 1,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    per_page = 20

    supplier_id_int: Optional[int] = int(supplier_id) if supplier_id and supplier_id.strip().isdigit() else None

    query = select(Refund).options(
        selectinload(Refund.supplier),
        selectinload(Refund.items),
    ).order_by(Refund.created_at.desc())

    if user.role.value == "client":
        query = query.where(Refund.client_user_id == user.id)

    query = build_refund_filter(query, status, supplier_id_int, client_name, date_from)

    count_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    query = query.offset((page - 1) * per_page).limit(per_page)
    result = await db.execute(query)
    refunds = result.scalars().all()

    return {"refunds": refunds, "total": total, "page": page, "per_page": per_page}


@router.get("/export-1c")
async def export_1c(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Export all refunds as CSV for 1C import."""
    user = await get_current_user(request, db)
    if user.role.value not in ("admin", "staff"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    from fastapi.responses import Response
    from datetime import datetime as dt

    query = select(Refund).options(
        selectinload(Refund.supplier),
        selectinload(Refund.items),
    ).order_by(Refund.created_at.desc())
    result = await db.execute(query)
    refunds = result.scalars().all()

    from app.services.ftp_service import build_1c_csv
    csv_data = build_1c_csv(refunds)

    filename = f"export_1c_{dt.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(
        content=csv_data,
        media_type="text/csv; charset=utf-8-sig",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/table")
async def refunds_table_partial(
    request: Request,
    status: Optional[str] = None,
    supplier_id: Optional[str] = None,
    client_name: Optional[str] = None,
    date: Optional[str] = None,
    article: Optional[str] = None,
    order_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Return HTML partial (tbody) for HTMX table update."""
    from fastapi.templating import Jinja2Templates
    import os
    templates_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
    templates = Jinja2Templates(directory=templates_dir)

    user = await get_current_user(request, db)

    supplier_id_int: Optional[int] = int(supplier_id) if supplier_id and supplier_id.strip().isdigit() else None

    query = select(Refund).options(
        selectinload(Refund.supplier),
        selectinload(Refund.items),
    ).order_by(Refund.created_at.desc())

    if user.role.value == "client":
        query = query.where(Refund.client_user_id == user.id)

    query = build_refund_filter(query, status, supplier_id_int, client_name, date, article, order_id)
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
    supplier_id_raw = form.get("supplier_id")
    supplier_id = int(supplier_id_raw) if supplier_id_raw and str(supplier_id_raw).isdigit() else None
    client_user_id_raw = form.get("client_user_id")
    client_user_id = int(client_user_id_raw) if client_user_id_raw and str(client_user_id_raw).isdigit() else None

    if not client_user_id:
        raise HTTPException(status_code=400, detail="Необходимо выбрать аккаунт клиента")

    client_result = await db.execute(select(User).where(User.id == client_user_id))
    client_user = client_result.scalar_one_or_none()
    if not client_user:
        raise HTTPException(status_code=400, detail="Клиент не найден")
    client_name = client_user.full_name
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
        client_user_id=client_user_id,
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


@router.post("/from-email/{uid}")
async def create_refund_from_email(
    uid: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Create a refund from a specific email identified by IMAP UID."""
    user = await get_current_user(request, db)
    if user.role.value not in ("admin", "staff"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    from app.services.mail_import import create_refund_from_uid
    try:
        refund = await create_refund_from_uid(uid, db)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to create refund from email uid={uid}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Не удалось создать заявку из письма")

    from fastapi.responses import RedirectResponse
    return RedirectResponse(url=f"/refunds/{refund.id}", status_code=302)


@router.post("/import-xls")
async def import_refunds_from_xls(
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Create returns from uploaded XLS/XLSX file. Each row = one new return."""
    user = await get_current_user(request, db)
    if user.role.value not in ("admin", "staff"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    import openpyxl
    import openpyxl.descriptors.base as _openpyxl_desc
    from io import BytesIO
    from decimal import Decimal, InvalidOperation

    # Some xlsx files contain invalid 'pane' attribute values that openpyxl rejects.
    # The error comes from NoneSet.__set__ (a descriptor), not __init__.
    # Patch NoneSet.__set__ once to silently coerce unknown values to None.
    if not getattr(_openpyxl_desc, "_noneset_patched", False):
        _orig_noneset_set = _openpyxl_desc.NoneSet.__set__

        def _safe_noneset_set(self, instance, value):
            if value is not None and value != "none" and value not in self.values:
                value = None
            _orig_noneset_set(self, instance, value)

        _openpyxl_desc.NoneSet.__set__ = _safe_noneset_set
        _openpyxl_desc._noneset_patched = True

    content = await file.read()
    try:
        wb = openpyxl.load_workbook(BytesIO(content), read_only=True, data_only=True)
    except Exception:
        raise HTTPException(status_code=400, detail="Не удалось открыть файл. Убедитесь, что это корректный XLS/XLSX.")

    ws = wb.active

    rows_iter = ws.iter_rows(values_only=True)
    header_row = next(rows_iter, None)
    if not header_row:
        wb.close()
        raise HTTPException(status_code=400, detail="Файл пустой или не содержит заголовков")

    col_map = {}
    for idx, cell in enumerate(header_row):
        if cell is not None:
            col_map[str(cell).strip()] = idx

    def get_cell(row, name, default=""):
        idx = col_map.get(name)
        if idx is None or idx >= len(row):
            return default
        val = row[idx]
        return str(val).strip() if val is not None else default

    created_count = 0
    errors = []

    for row_num, row in enumerate(rows_iter, start=2):
        if all(v is None or str(v).strip() == "" for v in row):
            continue
        try:
            supplier_name = get_cell(row, "Поставщик")
            client_id_val = get_cell(row, "ID клиента")
            position_id = get_cell(row, "ID позиции")
            order_id_val = get_cell(row, "ID заказа") or None
            article = get_cell(row, "Артикул")
            brand = get_cell(row, "Бренд") or None
            quantity_raw = get_cell(row, "Кол-во к возврату", "1")
            price_raw = get_cell(row, "Цена", "0")
            reason = get_cell(row, "Причина возврата") or None
            comment = get_cell(row, "Комментарий")
            email_val = get_cell(row, "Эл.адрес") or None

            if not article and not client_id_val:
                continue

            # Resolve supplier
            supplier_id_resolved = None
            if supplier_name:
                res = await db.execute(select(Supplier).where(Supplier.name.ilike(supplier_name)))
                supplier_obj = res.scalar_one_or_none()
                if supplier_obj:
                    supplier_id_resolved = supplier_obj.id

            # Parse quantity
            try:
                quantity = max(1, int(float(quantity_raw))) if quantity_raw else 1
            except (ValueError, TypeError):
                quantity = 1

            # Parse price
            try:
                price = Decimal(price_raw.replace(",", ".")) if price_raw else Decimal("0")
            except (InvalidOperation, AttributeError):
                price = Decimal("0")

            display_id = await generate_display_id(db)
            refund = Refund(
                display_id=display_id,
                status=RefundStatus.received,
                source=RefundSource.manual,
                client_name=client_id_val or "—",
                supplier_id=supplier_id_resolved,
                order_id=order_id_val,
                reason=reason,
                email_from=email_val,
                created_by_id=user.id,
            )
            db.add(refund)
            await db.flush()
            await db.refresh(refund)

            item = RefundItem(
                refund_id=refund.id,
                article=article or "—",
                brand=brand,
                quantity=quantity,
                price=price,
                position_id=position_id or None,
                comment=comment or None,
            )
            db.add(item)
            await db.flush()
            created_count += 1

        except Exception as e:
            errors.append(f"Строка {row_num}: {str(e)}")

    wb.close()
    return JSONResponse({"ok": True, "created": created_count, "errors": errors})


@router.delete("/{refund_id}")
async def delete_refund(
    refund_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    if user.role.value != "admin":
        raise HTTPException(status_code=403, detail="Только администратор может удалять возвраты")

    result = await db.execute(select(Refund).where(Refund.id == refund_id))
    refund = result.scalar_one_or_none()

    if not refund:
        raise HTTPException(status_code=404, detail="Возврат не найден")

    await db.delete(refund)
    await db.flush()

    return JSONResponse({"ok": True})


@router.post("/{refund_id}/assign-client")
async def assign_client_user(
    refund_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    if user.role.value not in ("admin", "staff"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    form = await request.form()
    client_user_id_raw = form.get("client_user_id", "")
    client_user_id = int(client_user_id_raw) if str(client_user_id_raw).strip().isdigit() else None

    result = await db.execute(select(Refund).where(Refund.id == refund_id))
    refund = result.scalar_one_or_none()
    if not refund:
        raise HTTPException(status_code=404, detail="Возврат не найден")

    if client_user_id:
        from app.models.user import User
        client_result = await db.execute(select(User).where(User.id == client_user_id))
        client = client_result.scalar_one_or_none()
        if not client:
            raise HTTPException(status_code=404, detail="Клиент не найден")
        refund.client_user_id = client_user_id
    else:
        refund.client_user_id = None

    await db.flush()
    return JSONResponse({"ok": True})


@router.post("/{refund_id}/send-supplier-email")
async def send_supplier_email_endpoint(
    refund_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Send email notification to supplier with return details and photos."""
    user = await get_current_user(request, db)
    if user.role.value not in ("admin", "staff"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")

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

    if not refund.supplier:
        raise HTTPException(status_code=400, detail="У возврата не указан поставщик")
    if not refund.supplier.email:
        raise HTTPException(status_code=400, detail="У поставщика не указан email")

    from app.config import settings as cfg
    photo_paths = [
        os.path.join(cfg.UPLOAD_DIR, f.stored_path) if not os.path.isabs(f.stored_path)
        else f.stored_path
        for f in refund.files
        if f.file_type.value == "photo"
    ]

    from app.services.email_service import send_supplier_email
    try:
        await send_supplier_email(
            db=db,
            to_email=refund.supplier.email,
            refund_display_id=refund.display_id,
            supplier_name=refund.supplier.name,
            items=refund.items,
            reason=refund.reason,
            supplier_doc_number=refund.supplier_doc_number,
            photo_paths=photo_paths,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to send supplier email for refund {refund_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка отправки письма: {e}")

    from datetime import datetime as dt
    refund.supplier_email_sent_at = dt.utcnow()
    await db.flush()

    return JSONResponse({"ok": True, "message": "Письмо поставщику отправлено"})


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
