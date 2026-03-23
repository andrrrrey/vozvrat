"""FTP integration service for 1C data exchange."""
import logging
import ftplib
import asyncio
import io
import csv
from typing import Optional
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.settings_service import get_setting

logger = logging.getLogger(__name__)


async def _get_ftp_settings(db: AsyncSession) -> dict:
    return {
        "host": await get_setting(db, "ftp_host"),
        "port": int(await get_setting(db, "ftp_port") or "21"),
        "user": await get_setting(db, "ftp_user"),
        "password": await get_setting(db, "ftp_password"),
        "path": await get_setting(db, "ftp_path") or "/",
    }


def _test_ftp_sync(cfg: dict) -> str:
    """Test FTP connection synchronously. Returns success message or raises."""
    ftp = ftplib.FTP()
    ftp.connect(cfg["host"], cfg["port"], timeout=10)
    ftp.login(cfg["user"], cfg["password"])
    welcome = ftp.getwelcome()
    ftp.quit()
    return welcome or "Подключение успешно"


async def test_ftp_connection(db: AsyncSession) -> str:
    cfg = await _get_ftp_settings(db)
    if not cfg["host"]:
        raise ValueError("FTP хост не настроен")
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _test_ftp_sync, cfg)


def _upload_file_sync(cfg: dict, remote_path: str, data: bytes) -> None:
    ftp = ftplib.FTP()
    ftp.connect(cfg["host"], cfg["port"], timeout=15)
    ftp.login(cfg["user"], cfg["password"])
    try:
        # Ensure base directory exists
        base = cfg["path"].rstrip("/")
        if base:
            try:
                ftp.cwd(base)
            except ftplib.error_perm:
                ftp.mkd(base)
                ftp.cwd(base)
        ftp.storbinary(f"STOR {remote_path}", io.BytesIO(data))
    finally:
        ftp.quit()


def _build_1c_csv(refunds: list) -> bytes:
    """Build CSV export for 1C from list of Refund objects."""
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow([
        "Номер возврата",
        "Статус",
        "Клиент",
        "Поставщик",
        "ID заказа",
        "Артикул",
        "Производитель",
        "Количество",
        "Цена",
        "Причина возврата",
        "Номер документа поставщика",
        "Дата создания",
    ])
    for refund in refunds:
        if refund.items:
            for item in refund.items:
                writer.writerow([
                    refund.display_id,
                    refund.status_label,
                    refund.client_name,
                    refund.supplier.name if refund.supplier else "",
                    refund.order_id or "",
                    item.article,
                    item.brand or "",
                    item.quantity,
                    str(item.price),
                    refund.reason or "",
                    refund.supplier_doc_number or "",
                    refund.created_at.strftime("%d.%m.%Y %H:%M"),
                ])
        else:
            writer.writerow([
                refund.display_id,
                refund.status_label,
                refund.client_name,
                refund.supplier.name if refund.supplier else "",
                refund.order_id or "",
                "", "", "", "",
                refund.reason or "",
                refund.supplier_doc_number or "",
                refund.created_at.strftime("%d.%m.%Y %H:%M"),
            ])
    return output.getvalue().encode("utf-8-sig")


async def upload_1c_export(db: AsyncSession, refunds: list) -> str:
    """Upload 1C export CSV to FTP. Returns remote path."""
    cfg = await _get_ftp_settings(db)
    if not cfg["host"]:
        raise ValueError("FTP не настроен")
    data = _build_1c_csv(refunds)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    remote_name = f"export_1c_{timestamp}.csv"
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _upload_file_sync, cfg, remote_name, data)
    return remote_name


def build_1c_csv(refunds: list) -> bytes:
    """Public sync wrapper for building CSV without FTP upload."""
    return _build_1c_csv(refunds)
