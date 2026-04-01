"""Admin settings management: SMTP, FTP, test email toggle."""
import logging
import os
from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.auth import get_current_user
from app.services.settings_service import get_all_settings, save_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/settings", tags=["settings"])

templates_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
templates = Jinja2Templates(directory=templates_dir)


async def _require_admin(request: Request, db: AsyncSession):
    user = await get_current_user(request, db)
    if user.role.value != "admin":
        raise HTTPException(status_code=403, detail="Только администратор")
    return user


@router.get("")
async def get_settings(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _require_admin(request, db)
    settings = await get_all_settings(db)
    return JSONResponse(settings)


@router.post("")
async def update_settings(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _require_admin(request, db)
    form = await request.form()
    data = {k: str(v) for k, v in form.items()}
    await save_settings(db, data)
    return JSONResponse({"ok": True, "message": "Настройки сохранены"})


@router.post("/ftp/test")
async def test_ftp(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _require_admin(request, db)
    from app.services.ftp_service import test_ftp_connection
    try:
        msg = await test_ftp_connection(db)
        return JSONResponse({"ok": True, "message": msg})
    except ValueError as e:
        return JSONResponse({"ok": False, "message": str(e)}, status_code=400)
    except Exception as e:
        logger.error(f"FTP test failed: {e}", exc_info=True)
        return JSONResponse({"ok": False, "message": f"Ошибка подключения: {e}"}, status_code=503)


@router.post("/smtp/test")
async def test_smtp(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _require_admin(request, db)
    from app.services.settings_service import get_setting
    import smtplib
    import asyncio

    host = await get_setting(db, "smtp_host")
    port = int(await get_setting(db, "smtp_port") or "587")
    smtp_user = await get_setting(db, "smtp_user")
    smtp_pass = await get_setting(db, "smtp_password")
    use_tls = (await get_setting(db, "smtp_use_tls")).lower() == "true"

    if not host:
        return JSONResponse({"ok": False, "message": "SMTP хост не настроен"}, status_code=400)

    def _check():
        import socket
        # Resolve hostname to IPv4 to avoid "Network is unreachable" on servers without IPv6
        resolved = socket.getaddrinfo(host, port, socket.AF_INET)[0][4][0]
        if port == 465:
            # Port 465 requires implicit SSL
            conn = smtplib.SMTP_SSL(resolved, port, timeout=10)
        elif use_tls:
            conn = smtplib.SMTP(resolved, port, timeout=10)
            conn.ehlo()
            conn.starttls()
            conn.ehlo()
        else:
            conn = smtplib.SMTP(resolved, port, timeout=10)
        try:
            if smtp_user and smtp_pass:
                conn.login(smtp_user, smtp_pass)
            return "Подключение успешно"
        finally:
            conn.quit()

    try:
        loop = asyncio.get_event_loop()
        msg = await loop.run_in_executor(None, _check)
        return JSONResponse({"ok": True, "message": msg})
    except Exception as e:
        logger.error(f"SMTP test failed: {e}", exc_info=True)
        return JSONResponse({"ok": False, "message": f"Ошибка: {e}"}, status_code=503)
