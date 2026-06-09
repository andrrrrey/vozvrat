"""Service for reading/writing app settings stored in the database."""
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.app_settings import AppSettings


SETTING_KEYS = {
    # SMTP
    "smtp_host": "",
    "smtp_port": "587",
    "smtp_user": "",
    "smtp_password": "",
    "smtp_from": "",
    "smtp_use_tls": "true",
    # FTP
    "ftp_host": "",
    "ftp_port": "21",
    "ftp_user": "",
    "ftp_password": "",
    "ftp_path": "/",
    # Supplier email test mode
    "supplier_email_test_mode": "false",
    "supplier_email_test_address": "",
    # Auto-create refunds from emails
    "mail_auto_create_enabled": "false",
    "mail_auto_create_since": "",
    # Heartbeat: ISO timestamp of the last scheduler mail-check tick (proves it's alive)
    "mail_last_check_at": "",
    # Auto-invite client on assign
    "auto_create_client_on_assign": "false",
}


async def get_setting(db: AsyncSession, key: str) -> str:
    result = await db.execute(select(AppSettings).where(AppSettings.key == key))
    row = result.scalar_one_or_none()
    if row is not None:
        return row.value
    return SETTING_KEYS.get(key, "")


async def set_setting(db: AsyncSession, key: str, value: str) -> None:
    result = await db.execute(select(AppSettings).where(AppSettings.key == key))
    row = result.scalar_one_or_none()
    if row is not None:
        row.value = value
    else:
        db.add(AppSettings(key=key, value=value))
    await db.flush()


async def get_all_settings(db: AsyncSession) -> dict[str, str]:
    result = await db.execute(select(AppSettings))
    rows = {r.key: r.value for r in result.scalars().all()}
    merged = dict(SETTING_KEYS)
    merged.update(rows)
    return merged


async def get_scheduler_status(db: AsyncSession) -> dict:
    """Return mail-import scheduler health based on the last heartbeat.

    The scheduler writes ``mail_last_check_at`` every tick. If that timestamp is
    missing or older than two check intervals (plus a small buffer), we treat the
    scheduler as not running — the most likely reason a fresh email never got a
    MailNotification at all.
    """
    from datetime import datetime, timezone, timedelta
    from app.config import settings as cfg

    last_str = await get_setting(db, "mail_last_check_at")
    last_dt = None
    alive = False
    if last_str:
        try:
            last_dt = datetime.fromisoformat(last_str)
            threshold = timedelta(minutes=cfg.MAIL_CHECK_INTERVAL_MINUTES * 2 + 2)
            alive = (datetime.now(timezone.utc) - last_dt) <= threshold
        except ValueError:
            pass
    return {"alive": alive, "last_check": last_dt}


async def save_settings(db: AsyncSession, data: dict[str, str]) -> None:
    for key, value in data.items():
        if key in SETTING_KEYS:
            await set_setting(db, key, str(value))
