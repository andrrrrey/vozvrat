import logging
import secrets
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from app.database import get_db
from app.models.user import User, UserRole
from app.schemas.user import UserCreate, UserUpdate, UserResponse
from app.services.auth import get_current_user, hash_password

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/users", tags=["users"])


async def require_admin(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    user = await get_current_user(request, db)
    if user.role.value != "admin":
        raise HTTPException(status_code=403, detail="Только администратор имеет доступ")
    return user


@router.get("", response_model=List[UserResponse])
async def list_users(
    request: Request,
    db: AsyncSession = Depends(get_db),
    role: Optional[str] = None,
    search: Optional[str] = None,
):
    await require_admin(request, db)
    query = select(User)
    if role:
        try:
            query = query.where(User.role == UserRole(role))
        except ValueError:
            pass
    if search:
        term = f"%{search}%"
        query = query.where(or_(User.full_name.ilike(term), User.email.ilike(term)))
    result = await db.execute(query.order_by(User.created_at.desc()))
    return result.scalars().all()


@router.post("", response_model=UserResponse)
async def create_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await require_admin(request, db)
    form = await request.form()

    email = str(form.get("email", "")).strip()
    password = str(form.get("password", "")).strip()
    full_name = str(form.get("full_name", "")).strip()
    role_str = str(form.get("role", "staff")).strip()

    if not email or not password or not full_name:
        raise HTTPException(status_code=400, detail="Email, пароль и имя обязательны")

    existing = await db.execute(select(User).where(User.email == email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Пользователь с таким email уже существует")

    try:
        role = UserRole(role_str)
    except ValueError:
        role = UserRole.staff

    user = User(
        email=email,
        password_hash=hash_password(password),
        full_name=full_name,
        role=role,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    logger.info(f"Created user: {email}, role={role}")
    return user


@router.put("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await require_admin(request, db)

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    form = await request.form()
    email = str(form.get("email", "")).strip()
    full_name = str(form.get("full_name", "")).strip()
    role_str = str(form.get("role", "")).strip()
    password = str(form.get("password", "")).strip()
    is_active = form.get("is_active", "true").lower() in ("true", "1", "on")

    if email:
        user.email = email
    if full_name:
        user.full_name = full_name
    if role_str:
        try:
            user.role = UserRole(role_str)
        except ValueError:
            pass
    if password:
        user.password_hash = hash_password(password)
    user.is_active = is_active

    await db.flush()
    await db.refresh(user)
    return user


@router.delete("/{user_id}")
async def deactivate_user(
    user_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    admin = await require_admin(request, db)
    if admin.id == user_id:
        raise HTTPException(status_code=400, detail="Нельзя деактивировать свой аккаунт")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    user.is_active = False
    await db.flush()
    return {"success": True}


@router.get("/form")
async def get_user_form(
    request: Request,
    user_id: int = None,
    db: AsyncSession = Depends(get_db),
):
    from fastapi.templating import Jinja2Templates
    import os
    templates_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
    templates = Jinja2Templates(directory=templates_dir)

    target_user = None
    if user_id:
        result = await db.execute(select(User).where(User.id == user_id))
        target_user = result.scalar_one_or_none()

    return templates.TemplateResponse(
        "users/_form_modal.html",
        {"request": request, "target_user": target_user, "roles": UserRole},
    )


@router.post("/{user_id}/send-password")
async def send_password(
    user_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Generate a new password for a client and send it to their email."""
    await require_admin(request, db)

    result = await db.execute(select(User).where(User.id == user_id))
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    new_password = secrets.token_urlsafe(10)
    target.password_hash = hash_password(new_password)
    await db.flush()

    from app.services.email_service import send_client_credentials_email
    try:
        await send_client_credentials_email(db, target.email, target.full_name, new_password)
    except Exception as e:
        logger.error(f"Failed to send credentials email to {target.email}: {e}")
        raise HTTPException(status_code=503, detail=f"Пароль обновлён, но письмо не отправлено: {e}")

    logger.info(f"New password sent to user id={user_id} email={target.email}")
    return JSONResponse({"ok": True, "message": f"Пароль отправлен на {target.email}"})


@router.post("/invite-client")
async def invite_client(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Create or update a client account and send credentials. Optionally assign to a refund."""
    await require_admin(request, db)

    form = await request.form()
    email = str(form.get("email", "")).strip().lower()
    full_name = str(form.get("full_name", "")).strip()
    refund_id_raw = form.get("refund_id", "")
    refund_id = int(refund_id_raw) if str(refund_id_raw).strip().isdigit() else None

    if not email or not full_name:
        raise HTTPException(status_code=400, detail="Email и имя обязательны")

    # Find or create client user
    result = await db.execute(select(User).where(User.email == email))
    target = result.scalar_one_or_none()

    new_password = secrets.token_urlsafe(10)

    if target:
        target.full_name = full_name
        target.password_hash = hash_password(new_password)
        target.is_active = True
    else:
        target = User(
            email=email,
            password_hash=hash_password(new_password),
            full_name=full_name,
            role=UserRole.client,
            is_active=True,
        )
        db.add(target)

    await db.flush()
    await db.refresh(target)

    # Assign to refund if given
    if refund_id:
        from app.models.refund import Refund
        refund_result = await db.execute(select(Refund).where(Refund.id == refund_id))
        refund = refund_result.scalar_one_or_none()
        if refund:
            refund.client_user_id = target.id
            refund.client_name = full_name
        await db.flush()

    from app.services.email_service import send_client_credentials_email
    try:
        await send_client_credentials_email(db, email, full_name, new_password)
    except Exception as e:
        logger.error(f"Failed to send invite email to {email}: {e}")
        raise HTTPException(status_code=503, detail=f"Аккаунт создан, но письмо не отправлено: {e}")

    logger.info(f"Client invited: email={email} user_id={target.id} refund_id={refund_id}")
    return JSONResponse({"ok": True, "user_id": target.id, "message": f"Данные входа отправлены на {email}"})
