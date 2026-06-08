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
    import socket
    host = smtp["host"]
    port = smtp["port"]
    use_tls = smtp["use_tls"]

    # Resolve hostname to IPv4 to avoid "Network is unreachable" on servers without IPv6
    resolved = socket.getaddrinfo(host, port, socket.AF_INET)[0][4][0]

    if port == 465:
        # Port 465 requires implicit SSL
        conn = smtplib.SMTP_SSL(resolved, port, timeout=15)
    elif use_tls:
        conn = smtplib.SMTP(resolved, port, timeout=15)
        conn.ehlo()
        conn.starttls()
        conn.ehlo()
    else:
        conn = smtplib.SMTP(resolved, port, timeout=15)

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
    notes: Optional[str] = None,
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

    if notes:
        lines += ["─" * 40, "ЗАМЕТКИ:", notes, ""]

    lines += [
        "─" * 40,
        "Фотоматериалы прикреплены к данному письму.",
        "",
        "С уважением,",
        "Отдел возвратов",
    ]

    body_text = "\n".join(lines)

    msg = EmailMessage()
    msg["Subject"] = f"Автопрагматик ООО (amx24) - Возврат товара № {refund_display_id}"
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


async def send_comment_mention_email(
    db: AsyncSession,
    to_email: str,
    recipient_name: str,
    author_name: str,
    refund_display_id: str,
    refund_id: int,
    comment_text: str,
    base_url: str = "",
) -> None:
    """Notify a staff member that they were mentioned in a refund comment."""
    smtp = await _get_smtp_settings(db)
    if not smtp["host"]:
        raise RuntimeError("SMTP не настроен. Заполните настройки почты в разделе Настройки.")

    link = f"{base_url}/refunds/{refund_id}" if base_url else f"/refunds/{refund_id}"

    body = "\n".join([
        f"Здравствуйте, {recipient_name}!",
        "",
        f"Сотрудник {author_name} упомянул вас в комментарии к возврату {refund_display_id}.",
        "",
        "Текст комментария:",
        "─" * 40,
        comment_text,
        "─" * 40,
        "",
        f"Открыть возврат: {link}",
        "",
        "С уважением,",
        "Система управления возвратами",
    ])

    msg = EmailMessage()
    msg["Subject"] = f"Вас упомянули в комментарии к возврату {refund_display_id}"
    msg["From"] = smtp["from_addr"] or smtp["user"]
    msg["To"] = to_email
    msg["Date"] = formatdate()
    msg.set_content(body, charset="utf-8")

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _send_smtp_sync, smtp, msg)
    logger.info(f"Mention notification sent to {to_email} for refund {refund_display_id}")


async def send_client_credentials_email(
    db: AsyncSession,
    to_email: str,
    full_name: str,
    password: str,
) -> None:
    """Send login credentials to a newly created or updated client account."""
    smtp = await _get_smtp_settings(db)
    if not smtp["host"]:
        raise RuntimeError("SMTP не настроен. Заполните настройки почты в разделе Настройки.")

    body = "\n".join([
        f"Здравствуйте, {full_name}!",
        "",
        "Для вас создан аккаунт в системе управления возвратами.",
        "",
        f"Email:    {to_email}",
        f"Пароль:   {password}",
        "",
        "Войдите на сайт и смените пароль при первом входе.",
        "",
        "С уважением,",
        "Служба поддержки",
    ])

    msg = EmailMessage()
    msg["Subject"] = "Данные для входа в систему"
    msg["From"] = smtp["from_addr"] or smtp["user"]
    msg["To"] = to_email
    msg["Date"] = formatdate()
    msg.set_content(body, charset="utf-8")

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _send_smtp_sync, smtp, msg)
    logger.info(f"Credentials email sent to {to_email}")
