"""Запросы — облегчённый аналог возвратов: CRUD, исполнитель, переписка, файлы."""
import logging
import os
from datetime import datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, exists
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.request import Request as RequestModel, RequestItem, RequestStatus
from app.models.message import Message, MessageVisibility
from app.models.user import User, UserRole
from app.services.auth import get_current_user
from app.services.file_service import save_file
from app.routers.notifications import mark_request_read, get_unread_per_request

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/requests", tags=["requests"])

templates_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
templates = Jinja2Templates(directory=templates_dir)


async def generate_request_display_id(db: AsyncSession) -> str:
    result = await db.execute(select(func.max(RequestModel.id)))
    max_id = result.scalar() or 0
    return f"Z-{10001 + max_id}"


def build_request_filter(query, status: Optional[str], executor_id: Optional[int],
                         client_name: Optional[str], date_from: Optional[str],
                         article: Optional[str] = None, order_id: Optional[str] = None):
    conditions = []
    if status:
        try:
            conditions.append(RequestModel.status == RequestStatus(status))
        except ValueError:
            pass
    if executor_id:
        conditions.append(RequestModel.executor_id == executor_id)
    if client_name:
        conditions.append(RequestModel.client_name.ilike(f"%{client_name}%"))
    if date_from:
        try:
            d = datetime.strptime(date_from, "%Y-%m-%d").date()
            conditions.append(func.date(RequestModel.created_at) == d)
        except ValueError:
            pass
    if article:
        conditions.append(
            exists().where(
                and_(
                    RequestItem.request_id == RequestModel.id,
                    RequestItem.article.ilike(f"%{article}%"),
                )
            )
        )
    if order_id:
        conditions.append(RequestModel.order_id.ilike(f"%{order_id}%"))
    if conditions:
        query = query.where(and_(*conditions))
    return query


@router.get("/table", response_class=HTMLResponse)
async def requests_table_partial(
    request: Request,
    status: Optional[str] = None,
    executor_id: Optional[str] = None,
    client_name: Optional[str] = None,
    date: Optional[str] = None,
    article: Optional[str] = None,
    order_id: Optional[str] = None,
    page: int = 1,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    per_page = 20
    if page < 1:
        page = 1

    executor_id_int: Optional[int] = int(executor_id) if executor_id and executor_id.strip().isdigit() else None

    query = select(RequestModel).options(
        selectinload(RequestModel.items),
        selectinload(RequestModel.executor),
    ).order_by(RequestModel.created_at.desc())

    if user.role.value == "client":
        query = query.where(RequestModel.client_user_id == user.id)

    query = build_request_filter(query, status, executor_id_int, client_name, date, article, order_id)

    count_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar() or 0
    total_pages = max(1, (total + per_page - 1) // per_page)
    if page > total_pages:
        page = total_pages

    query = query.offset((page - 1) * per_page).limit(per_page)
    result = await db.execute(query)
    requests_list = result.scalars().all()

    unread_counts = await get_unread_per_request(user, db)

    return templates.TemplateResponse(
        "requests/_table.html",
        {
            "request": request, "requests": requests_list, "user": user,
            "unread_counts": unread_counts,
            "page": page, "total_pages": total_pages, "total": total,
        },
    )


@router.post("")
async def create_request(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    if user.role.value not in ("admin", "staff"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    form = await request.form()
    client_user_id_raw = form.get("client_user_id")
    client_user_id = int(client_user_id_raw) if client_user_id_raw and str(client_user_id_raw).isdigit() else None
    executor_id_raw = form.get("executor_id")
    executor_id = int(executor_id_raw) if executor_id_raw and str(executor_id_raw).isdigit() else None

    if not client_user_id:
        raise HTTPException(status_code=400, detail="Необходимо выбрать аккаунт клиента")
    client_result = await db.execute(select(User).where(User.id == client_user_id))
    client_user = client_result.scalar_one_or_none()
    if not client_user:
        raise HTTPException(status_code=400, detail="Клиент не найден")

    order_id = str(form.get("order_id", "")).strip() or None
    reason = str(form.get("reason", "")).strip() or None
    article = str(form.get("article", "")).strip() or None
    brand = str(form.get("brand", "")).strip() or None
    quantity_raw = str(form.get("quantity", "")).strip()
    price_raw = str(form.get("price", "")).strip()

    display_id = await generate_request_display_id(db)
    req = RequestModel(
        display_id=display_id,
        status=RequestStatus.received,
        client_name=client_user.full_name,
        client_user_id=client_user_id,
        executor_id=executor_id,
        order_id=order_id,
        reason=reason,
        created_by_id=user.id,
    )
    db.add(req)
    await db.flush()
    await db.refresh(req)

    if article or brand or quantity_raw or price_raw:
        quantity = int(quantity_raw) if quantity_raw.isdigit() else None
        try:
            price = Decimal(price_raw.replace(",", ".")) if price_raw else None
        except Exception:
            price = None
        db.add(RequestItem(
            request_id=req.id, article=article, brand=brand,
            quantity=quantity, price=price,
        ))

    for key, value in form.multi_items():
        if hasattr(value, "filename") and value.filename:
            await save_file(value, None, db, uploaded_by_id=user.id, request_id=req.id)

    await db.flush()
    return RedirectResponse(url=f"/requests/{req.id}", status_code=302)


@router.post("/client")
async def create_client_request(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Create a request from the client dashboard. All product fields are optional."""
    user = await get_current_user(request, db)
    if user.role.value != "client":
        raise HTTPException(status_code=403, detail="Только для клиентов")

    form = await request.form()
    order_id = str(form.get("order_id", "")).strip() or None
    reason = str(form.get("reason", "")).strip() or None

    articles = form.getlist("article[]")
    brands = form.getlist("brand[]")
    quantities = form.getlist("quantity[]")
    prices = form.getlist("price[]")
    descriptions = form.getlist("description[]")

    display_id = await generate_request_display_id(db)
    req = RequestModel(
        display_id=display_id,
        status=RequestStatus.received,
        client_name=user.full_name,
        client_user_id=user.id,
        order_id=order_id,
        reason=reason,
        created_by_id=user.id,
    )
    db.add(req)
    await db.flush()
    await db.refresh(req)

    for i, article in enumerate(articles):
        article = (article or "").strip()
        brand = brands[i].strip() if i < len(brands) else None
        qty_raw = quantities[i] if i < len(quantities) else ""
        price_raw = prices[i] if i < len(prices) else ""
        desc_raw = descriptions[i] if i < len(descriptions) else ""
        # Пропускаем полностью пустую позицию
        if not (article or brand or str(qty_raw).strip() or str(price_raw).strip() or desc_raw.strip()):
            continue
        try:
            qty = int(qty_raw) if str(qty_raw).strip().isdigit() else None
        except (ValueError, TypeError):
            qty = None
        try:
            price = Decimal(str(price_raw).replace(",", ".")) if str(price_raw).strip() else None
        except Exception:
            price = None
        db.add(RequestItem(
            request_id=req.id,
            article=article[:255] if article else None,
            brand=brand[:255] if brand else None,
            quantity=qty,
            price=price,
            description=desc_raw.strip()[:500] if desc_raw.strip() else None,
        ))

    for key, value in form.multi_items():
        if hasattr(value, "filename") and value.filename:
            await save_file(value, None, db, uploaded_by_id=user.id, request_id=req.id)

    await db.flush()
    return RedirectResponse(url=f"/client/requests/{req.id}", status_code=302)


@router.delete("/{request_id}")
async def delete_request(
    request_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    if user.role.value != "admin":
        raise HTTPException(status_code=403, detail="Только администратор может удалять запросы")

    result = await db.execute(select(RequestModel).where(RequestModel.id == request_id))
    req = result.scalar_one_or_none()
    if not req:
        raise HTTPException(status_code=404, detail="Запрос не найден")

    await db.delete(req)
    await db.flush()
    return JSONResponse({"ok": True})


@router.post("/{request_id}/executor")
async def assign_executor(
    request_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    if user.role.value not in ("admin", "staff"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    form = await request.form()
    executor_id_raw = form.get("executor_id", "")
    executor_id = int(executor_id_raw) if str(executor_id_raw).strip().isdigit() else None

    result = await db.execute(select(RequestModel).where(RequestModel.id == request_id))
    req = result.scalar_one_or_none()
    if not req:
        raise HTTPException(status_code=404, detail="Запрос не найден")

    if executor_id:
        ex_result = await db.execute(
            select(User).where(
                User.id == executor_id,
                User.role.in_([UserRole.admin, UserRole.staff]),
            )
        )
        if not ex_result.scalar_one_or_none():
            raise HTTPException(status_code=404, detail="Исполнитель не найден")
        req.executor_id = executor_id
    else:
        req.executor_id = None

    await db.flush()
    return JSONResponse({"ok": True})


@router.post("/{request_id}/status")
async def update_request_status(
    request_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    if user.role.value not in ("admin", "staff"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    form = await request.form()
    new_status_str = str(form.get("status", ""))
    try:
        new_status = RequestStatus(new_status_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Недопустимый статус")

    result = await db.execute(
        select(RequestModel).options(selectinload(RequestModel.executor)).where(RequestModel.id == request_id)
    )
    req = result.scalar_one_or_none()
    if not req:
        raise HTTPException(status_code=404, detail="Запрос не найден")

    req.status = new_status
    await db.flush()

    return templates.TemplateResponse(
        "requests/_status_section.html",
        {"request": request, "req": req, "user": user, "statuses": RequestStatus},
    )


# ---------------------------------------------------------------------------
# Переписка (чат + внутренние комментарии) — переиспользует шаблоны возвратов
# ---------------------------------------------------------------------------

async def _load_request_messages(request_id: int, db: AsyncSession) -> list[Message]:
    q = select(Message).options(selectinload(Message.user)).where(
        Message.request_id == request_id,
        Message.visibility == MessageVisibility.all,
    ).order_by(Message.created_at.asc())
    result = await db.execute(q)
    return list(result.scalars().all())


async def _load_request_comments(request_id: int, db: AsyncSession) -> list[Message]:
    q = select(Message).options(selectinload(Message.user)).where(
        Message.request_id == request_id,
        Message.visibility == MessageVisibility.staff_only,
    ).order_by(Message.created_at.asc())
    result = await db.execute(q)
    return list(result.scalars().all())


async def _get_request_for_user(request_id: int, user, db: AsyncSession) -> RequestModel:
    q = select(RequestModel).where(RequestModel.id == request_id)
    if user.role.value == "client":
        q = q.where(RequestModel.client_user_id == user.id)
    result = await db.execute(q)
    req = result.scalar_one_or_none()
    if not req:
        raise HTTPException(status_code=404, detail="Запрос не найден")
    return req


@router.get("/{request_id}/messages", response_class=HTMLResponse)
async def get_request_messages(request_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    await _get_request_for_user(request_id, user, db)
    messages = await _load_request_messages(request_id, db)
    await mark_request_read(request_id, user.id, db)
    return templates.TemplateResponse(
        "refunds/_chat_messages.html",
        {"request": request, "messages": messages, "user": user},
    )


@router.post("/{request_id}/messages", response_class=HTMLResponse)
async def post_request_message(request_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    await _get_request_for_user(request_id, user, db)

    form = await request.form()
    text = str(form.get("text", "")).strip()
    if not text:
        raise HTTPException(status_code=400, detail="Текст сообщения пустой")

    if user.role.value == "client":
        visibility = MessageVisibility.all
    else:
        try:
            visibility = MessageVisibility(str(form.get("visibility", "all")))
        except ValueError:
            visibility = MessageVisibility.all

    msg = Message(request_id=request_id, user_id=user.id, text=text, visibility=visibility)
    db.add(msg)
    await db.commit()
    await mark_request_read(request_id, user.id, db)

    if visibility == MessageVisibility.all:
        await _notify_new_request_message(request_id, user, text, request, db)

    messages = await _load_request_messages(request_id, db)
    response = templates.TemplateResponse(
        "refunds/_chat_messages.html",
        {"request": request, "messages": messages, "user": user},
    )
    response.headers["HX-Trigger"] = "refreshBadges"
    return response


@router.get("/{request_id}/comments", response_class=HTMLResponse)
async def get_request_comments(request_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    if user.role.value not in ("admin", "staff"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    await _get_request_for_user(request_id, user, db)
    comments = await _load_request_comments(request_id, db)
    await mark_request_read(request_id, user.id, db)
    return templates.TemplateResponse(
        "refunds/_comments.html",
        {"request": request, "comments": comments, "user": user},
    )


@router.post("/{request_id}/comments", response_class=HTMLResponse)
async def post_request_comment(request_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    if user.role.value not in ("admin", "staff"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    await _get_request_for_user(request_id, user, db)

    form = await request.form()
    text = str(form.get("text", "")).strip()
    if not text:
        raise HTTPException(status_code=400, detail="Текст комментария пустой")

    msg = Message(request_id=request_id, user_id=user.id, text=text, visibility=MessageVisibility.staff_only)
    db.add(msg)
    await db.commit()
    await mark_request_read(request_id, user.id, db)

    comments = await _load_request_comments(request_id, db)
    return templates.TemplateResponse(
        "refunds/_comments.html",
        {"request": request, "comments": comments, "user": user},
    )


async def _notify_new_request_message(request_id: int, author, text: str, request: Request, db: AsyncSession) -> None:
    """Email the other party about a new request message. Never raises."""
    from app.services.email_service import send_new_message_email

    try:
        result = await db.execute(
            select(RequestModel)
            .options(selectinload(RequestModel.client_user), selectinload(RequestModel.executor), selectinload(RequestModel.created_by))
            .where(RequestModel.id == request_id)
        )
        req = result.scalar_one_or_none()
        if not req:
            return

        base_url = str(request.base_url).rstrip("/") if request else ""
        author_is_client = author.role.value == "client"

        if author_is_client:
            # Notify the executor if assigned, else the creator (if staff), else all active staff/admin.
            recipients: list = []
            if req.executor and req.executor.is_active and req.executor.email:
                recipients = [req.executor]
            elif req.created_by and req.created_by.role in (UserRole.admin, UserRole.staff) \
                    and req.created_by.is_active and req.created_by.email:
                recipients = [req.created_by]
            else:
                staff_result = await db.execute(
                    select(User).where(
                        User.role.in_([UserRole.admin, UserRole.staff]),
                        User.is_active == True,
                    )
                )
                recipients = [u for u in staff_result.scalars().all() if u.email]
            link = f"{base_url}/requests/{request_id}"
            for recipient in recipients:
                try:
                    await send_new_message_email(
                        db, to_email=recipient.email, recipient_name=recipient.full_name,
                        author_name=author.full_name, entity_display_id=req.display_id,
                        entity_dative="запросу", message_text=text, link=link, from_client=True,
                    )
                except Exception as e:
                    logger.warning(f"Failed to send request new-message email to {recipient.email}: {e}")
        else:
            client = req.client_user
            if client and client.email:
                link = f"{base_url}/client/requests/{request_id}"
                try:
                    await send_new_message_email(
                        db, to_email=client.email, recipient_name=client.full_name,
                        author_name=author.full_name, entity_display_id=req.display_id,
                        entity_dative="запросу", message_text=text, link=link, from_client=False,
                    )
                except Exception as e:
                    logger.warning(f"Failed to send request new-message email to {client.email}: {e}")
    except Exception as e:
        logger.warning(f"_notify_new_request_message failed for request {request_id}: {e}")
