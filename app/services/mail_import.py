import imaplib
import email
import logging
import os
import uuid
import io
from email.header import decode_header
from pathlib import Path
from typing import Optional
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.config import settings
from app.models.refund import Refund, RefundStatus, RefundSource
from app.models.refund_item import RefundItem
from app.models.file_attachment import FileAttachment, FileType
from app.services.file_service import detect_file_type

logger = logging.getLogger(__name__)


def decode_str(s: str) -> str:
    """Decode encoded email header string."""
    parts = decode_header(s)
    result = []
    for part, charset in parts:
        if isinstance(part, bytes):
            try:
                result.append(part.decode(charset or "utf-8", errors="replace"))
            except Exception:
                result.append(part.decode("latin-1", errors="replace"))
        else:
            result.append(part)
    return "".join(result)


async def generate_display_id(db: AsyncSession) -> str:
    """Generate next display ID like #10001, #10002..."""
    result = await db.execute(select(func.count(Refund.id)))
    count = result.scalar() or 0
    return f"#{10001 + count}"


def try_parse_xls(content: bytes) -> list[dict]:
    """Try to parse XLS/XLSX file and extract refund items."""
    try:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True)
        ws = wb.active

        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return []

        header_row = None
        header_idx = 0
        for i, row in enumerate(rows[:5]):
            row_lower = [str(c).lower() if c else "" for c in row]
            if any(k in " ".join(row_lower) for k in ["артикул", "article", "код"]):
                header_row = row_lower
                header_idx = i
                break

        if header_row is None:
            return []

        col_map = {}
        for j, h in enumerate(header_row):
            if "артикул" in h or "article" in h or "арт" in h:
                col_map["article"] = j
            elif "марк" in h or "бренд" in h or "brand" in h:
                col_map["brand"] = j
            elif "кол" in h or "qty" in h or "количество" in h:
                col_map["quantity"] = j
            elif "цена" in h or "price" in h or "стоим" in h:
                col_map["price"] = j
            elif "описан" in h or "назван" in h or "наим" in h:
                col_map["description"] = j

        if "article" not in col_map:
            return []

        items = []
        for row in rows[header_idx + 1:]:
            if not row or not row[col_map["article"]]:
                continue
            item = {
                "article": str(row[col_map["article"]]),
                "brand": str(row[col_map["brand"]]) if "brand" in col_map and row[col_map["brand"]] else None,
                "quantity": int(row[col_map["quantity"]]) if "quantity" in col_map and row[col_map["quantity"]] else 1,
                "price": float(row[col_map["price"]]) if "price" in col_map and row[col_map["price"]] else 0.0,
                "description": str(row[col_map["description"]]) if "description" in col_map and row[col_map["description"]] else None,
            }
            items.append(item)
        return items
    except Exception as e:
        logger.debug(f"XLS parse failed: {e}")
        return []


def _is_allowed_sender(from_header: str) -> bool:
    """Check sender against MAIL_ALLOWED_SENDERS whitelist. Empty list = allow all."""
    if not settings.MAIL_ALLOWED_SENDERS.strip():
        return True
    sender_addr = email.utils.parseaddr(from_header)[1].lower()
    allowed = [s.strip().lower() for s in settings.MAIL_ALLOWED_SENDERS.split(",") if s.strip()]
    return any(
        sender_addr == rule or sender_addr.endswith("@" + rule)
        for rule in allowed
    )


def _has_subject_keyword(subject: str) -> bool:
    """Check subject contains at least one keyword. Empty list = allow all."""
    if not settings.MAIL_SUBJECT_KEYWORDS.strip():
        return True
    keywords = [k.strip().lower() for k in settings.MAIL_SUBJECT_KEYWORDS.split(",") if k.strip()]
    subj_lower = subject.lower()
    return any(kw in subj_lower for kw in keywords)


def _has_xls_attachment(msg: email.message.Message) -> bool:
    """Return True if email has at least one XLS/XLSX attachment."""
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        if part.get("Content-Disposition") is None:
            continue
        filename = part.get_filename()
        if not filename:
            continue
        filename_decoded = decode_str(filename).lower()
        if filename_decoded.endswith((".xls", ".xlsx")):
            return True
    return False


def _build_imap_search_criteria() -> str:
    """Build IMAP SEARCH criteria using server-side subject filter when possible."""
    keywords = []
    if settings.MAIL_SUBJECT_KEYWORDS.strip():
        keywords = [k.strip() for k in settings.MAIL_SUBJECT_KEYWORDS.split(",") if k.strip()]

    if not keywords:
        return "UNSEEN"

    if len(keywords) == 1:
        return f'UNSEEN SUBJECT "{keywords[0]}"'

    # Build nested OR: (OR SUBJECT "k1" (OR SUBJECT "k2" SUBJECT "k3"))
    chain = f'SUBJECT "{keywords[-1]}"'
    for kw in reversed(keywords[:-1]):
        chain = f'(OR SUBJECT "{kw}" {chain})'
    return f"UNSEEN {chain}"


async def process_emails(db: AsyncSession) -> int:
    """Connect to IMAP, fetch unseen emails, create Refunds. Returns number processed."""
    if not settings.MAIL_LOGIN or not settings.MAIL_PASSWORD:
        logger.warning("IMAP credentials not configured, skipping mail import")
        return 0

    processed = 0
    skipped = 0
    try:
        conn = imaplib.IMAP4_SSL(settings.MAIL_IMAP_HOST, settings.MAIL_IMAP_PORT)
        conn.login(settings.MAIL_LOGIN, settings.MAIL_PASSWORD)
        conn.select(settings.MAIL_FOLDER)

        search_criteria = _build_imap_search_criteria()
        _, message_ids = conn.search(None, search_criteria)
        ids = message_ids[0].split() if message_ids[0] else []

        limit = settings.MAIL_FETCH_LIMIT
        total_found = len(ids)
        if limit > 0 and total_found > limit:
            # Take oldest first (lower IDs), leave the rest for next cycle
            ids = ids[:limit]
            logger.info(f"Found {total_found} unseen emails, processing first {limit} this cycle")
        else:
            logger.info(f"Found {total_found} unseen emails")

        for msg_id in ids:
            try:
                accepted = await _process_single_email(conn, msg_id, db)
                if accepted:
                    processed += 1
                else:
                    skipped += 1
            except Exception as e:
                logger.error(f"Error processing email {msg_id}: {e}", exc_info=True)

        if skipped:
            logger.info(f"Skipped {skipped} emails (did not pass filters)")
        conn.logout()
    except Exception as e:
        logger.error(f"IMAP connection error: {e}", exc_info=True)

    return processed


async def _process_single_email(conn: imaplib.IMAP4_SSL, msg_id: bytes, db: AsyncSession) -> bool:
    """Process one email. Returns True if refund was created, False if filtered out."""
    _, msg_data = conn.fetch(msg_id, "(RFC822)")
    raw = msg_data[0][1]
    msg = email.message_from_bytes(raw)

    subject = decode_str(msg.get("Subject", "Без темы"))
    from_header = decode_str(msg.get("From", ""))
    client_name = email.utils.parseaddr(from_header)[0] or email.utils.parseaddr(from_header)[1]

    # --- Фильтрация ---
    if not _is_allowed_sender(from_header):
        logger.debug(f"Skipped (sender not in whitelist): from='{from_header}'")
        conn.store(msg_id, "+FLAGS", "\\Seen")
        return False

    if not _has_subject_keyword(subject):
        logger.debug(f"Skipped (no keyword in subject): subject='{subject}'")
        conn.store(msg_id, "+FLAGS", "\\Seen")
        return False

    if settings.MAIL_REQUIRE_XLS and not _has_xls_attachment(msg):
        logger.debug(f"Skipped (no XLS attachment): subject='{subject}' from='{from_header}'")
        conn.store(msg_id, "+FLAGS", "\\Seen")
        return False
    # ------------------

    logger.info(f"Processing email: subject='{subject}' from='{from_header}'")

    display_id = await generate_display_id(db)

    refund = Refund(
        display_id=display_id,
        status=RefundStatus.received,
        source=RefundSource.email,
        client_name=client_name or from_header or "Неизвестный отправитель",
        email_subject=subject[:500],
        email_from=from_header[:255],
    )
    db.add(refund)
    await db.flush()
    await db.refresh(refund)

    refund_dir = Path(settings.UPLOAD_DIR) / f"refund_{refund.id}"
    refund_dir.mkdir(parents=True, exist_ok=True)

    xls_items_created = False

    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        if part.get("Content-Disposition") is None:
            continue

        filename = part.get_filename()
        if not filename:
            continue

        filename = decode_str(filename)
        content = part.get_payload(decode=True)
        if not content:
            continue

        file_type = detect_file_type(filename)
        unique_name = f"{uuid.uuid4().hex}_{filename}"
        stored_path = str(refund_dir / unique_name)

        with open(stored_path, "wb") as f:
            f.write(content)

        if file_type == FileType.xls and not xls_items_created:
            items = try_parse_xls(content)
            if items:
                for item_data in items:
                    item = RefundItem(
                        refund_id=refund.id,
                        article=item_data["article"],
                        brand=item_data.get("brand"),
                        quantity=item_data.get("quantity", 1),
                        price=item_data.get("price", 0),
                        description=item_data.get("description"),
                    )
                    db.add(item)
                xls_items_created = True
                logger.debug(f"Parsed {len(items)} items from XLS for refund {refund.id}")

        attachment = FileAttachment(
            refund_id=refund.id,
            filename=filename,
            stored_path=stored_path,
            file_type=file_type,
            file_size=len(content),
        )
        db.add(attachment)

    await db.flush()
    conn.store(msg_id, "+FLAGS", "\\Seen")
    logger.info(f"Created refund {display_id} (id={refund.id}) from email")
    return True
