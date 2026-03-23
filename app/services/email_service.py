"""SMTP email sending service for supplier notifications."""
import logging
import smtplib
import asyncio
import os
from email.message import EmailMessage
from email.utils import formatdate
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.settings_service import get_setting

logger = logging.getLogger(__name__)


async def _get_smtp_settings(db: AsyncSession) -> dict:
    return {
        "host": await get_setting(db, "smtp_host"),
        "port": int(await get_setting(db, "smtp_port") or "587"),
        "user": await get_setting(db, "smtp_user"),
        "password": await get_setting(db, "smtp_password"),
        "from_addr": await get_setting(db, "smtp_from"),
        "use_tls": (await get_setting(db, "smtp_use_tls")).lower() == "true",
    }


def _send_smtp_sync(smtp: dict, msg: EmailMessage) -> None:
    host = smtp["host"]
    port = smtp["port"]
    use_tls = smtp["use_tls"]

    if use_tls:
        conn = smtplib.SMTP(host, port, timeout=15)
        conn.ehlo()
        conn.starttls()
        conn.ehlo()
    else:
        conn = smtplib.SMTP(host, port, timeout=15)

    try:
        if smtp["user"] and smtp["password"]:
            conn.login(smtp["user"], smtp["password"])
        conn.send_message(msg)
    finally:
        conn.quit()


async def send_supplier_email(
    db: AsyncSession,
    to_email: str,
    refund_display_id: str,
    supplier_name: str,
    items: list,
    reason: Optional[str],
    supplier_doc_number: Optional[str],
    photo_paths: list[str],
) -> None:
    """Send a return notification email to the supplier."""
    smtp = await _get_smtp_settings(db)
    if not smtp["host"]:
        raise RuntimeError("SMTP не настроен. Заполните настройки почты в разделе Настройки.")

    test_mode = (await get_setting(db, "supplier_email_test_mode")).lower() == "true"
    test_address = await get_setting(db, "supplier_email_test_address")

    actual_to = to_email
    if test_mode:
        if not test_address:
            raise RuntimeError("Включён тестовый режим, но тестовый email не указан.")
        actual_to = test_address

    # Build email body
    lines = [
        f"Уважаемый поставщик {supplier_name},",
        "",
        f"Направляем уведомление о возврате товара № {refund_display_id}.",
        "",
        "СОСТАВ ВОЗВРАТА:",
        "─" * 40,
    ]

    for item in items:
        lines.append(f"Артикул:      {item.article}")
        if item.brand:
            lines.append(f"Производитель: {item.brand}")
        lines.append(f"Количество:   {item.quantity} шт.")
        lines.append("")

    if reason:
        lines += ["─" * 40, f"Причина возврата: {reason}", ""]

    if supplier_doc_number:
        lines += [f"Номер документа поставщика: {supplier_doc_number}", ""]

    lines += [
        "─" * 40,
        "Фотоматериалы прикреплены к данному письму.",
        "",
        "С уважением,",
        "Отдел возвратов",
    ]

    body_text = "\n".join(lines)

    msg = EmailMessage()
    msg["Subject"] = f"Возврат товара № {refund_display_id}"
    msg["From"] = smtp["from_addr"] or smtp["user"]
    msg["To"] = actual_to
    msg["Date"] = formatdate()
    msg.set_content(body_text, charset="utf-8")

    # Attach photos
    for path in photo_paths:
        if not os.path.exists(path):
            continue
        filename = os.path.basename(path)
        ext = os.path.splitext(filename)[1].lower()
        mime_type = "image/jpeg" if ext in (".jpg", ".jpeg") else \
                    "image/png" if ext == ".png" else \
                    "image/gif" if ext == ".gif" else \
                    "image/webp" if ext == ".webp" else \
                    "application/octet-stream"
        maintype, subtype = mime_type.split("/")
        with open(path, "rb") as f:
            data = f.read()
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _send_smtp_sync, smtp, msg)
    logger.info(f"Supplier email sent for refund {refund_display_id} to {actual_to} (test_mode={test_mode})")
