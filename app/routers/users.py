import logging
from typing import List
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
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
):
    await require_admin(request, db)
    result = await db.execute(select(User).order_by(User.created_at.desc()))
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
