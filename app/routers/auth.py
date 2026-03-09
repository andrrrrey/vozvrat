import logging
from fastapi import APIRouter, Depends, HTTPException, Response, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models.user import User
from app.schemas.auth import LoginRequest, CurrentUserResponse
from app.services.auth import (
    verify_password, create_access_token, get_current_user, COOKIE_NAME
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login")
async def login(request: Request, db: AsyncSession = Depends(get_db)):
    form = await request.form()
    email = form.get("email", "")
    password = form.get("password", "")

    result = await db.execute(select(User).where(User.email == email, User.is_active == True))
    user = result.scalar_one_or_none()

    if not user or not verify_password(str(password), user.password_hash):
        # Return HTMX-friendly error
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Неверный email или пароль"},
        )

    token = create_access_token(user.id, user.role.value)
    logger.info(f"User {user.email} logged in successfully")

    response = JSONResponse(content={"success": True, "role": user.role.value})
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=86400,
    )
    return response


@router.post("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie(COOKIE_NAME)
    return response


@router.get("/me", response_model=CurrentUserResponse)
async def get_me(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    return user
