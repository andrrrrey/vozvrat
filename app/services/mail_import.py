import imaplib
import email
import logging
import os
import re
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
    result = await db.execute(select(func.max(Refund.id)))
    max_id = result.scalar() or 0
    return f"#{10001 + max_id}"


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
        # Some clients omit Content-Disposition but still set a filename in Content-Type;
        # align with the attachment processing loop which also skips the disposition check.
        if part.get_content_type() in ("text/plain", "text/html"):
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

    # IMAP SEARCH only supports ASCII; skip non-ASCII keywords for server-side filtering
    ascii_keywords = []
    for kw in keywords:
        try:
            kw.encode("ascii")
            ascii_keywords.append(kw)
        except UnicodeEncodeError:
            pass

    if not ascii_keywords:
        return "UNSEEN"

    if len(ascii_keywords) == 1:
        return f'UNSEEN SUBJECT "{ascii_keywords[0]}"'

    # Build nested OR: (OR SUBJECT "k1" (OR SUBJECT "k2" SUBJECT "k3"))
    chain = f'SUBJECT "{ascii_keywords[-1]}"'
    for kw in reversed(ascii_keywords[:-1]):
        chain = f'(OR SUBJECT "{kw}" {chain})'
    return f"UNSEEN {chain}"


def get_email_body_text(msg: email.message.Message) -> str:
    """Extract plain text body from email message."""
    text_parts = []
    for part in msg.walk():
        if part.get_content_type() == "text/plain" and part.get_content_disposition() != "attachment":
            payload = part.get_payload(decode=True)
            if payload:
                charset = part.get_content_charset() or "utf-8"
                try:
                    text_parts.append(payload.decode(charset, errors="replace"))
                except Exception:
                    text_parts.append(payload.decode("utf-8", errors="replace"))
    return "\n".join(text_parts)


def _parse_freeform_item(text: str) -> dict:
    """
    Fallback: extract article / description / quantity from unstructured text lines.
    Handles patterns like: "JF56325FP  Фильтр воздушный  12шт"
    Article is a Latin-letter + digit code (e.g. JF56325FP, LF3000, 1987432031).
    """
    result = {}

    # Quantity: digits immediately followed by "шт" (case-insensitive)
    qty_match = re.search(r'(\d+)\s*шт', text, re.IGNORECASE)
    if qty_match:
        result["quantity"] = int(qty_match.group(1))

    # Article: standalone alphanumeric code with at least one Latin letter and one digit,
    # total length 4–25. Excludes purely Cyrillic words.
    article_pattern = re.compile(
        r'\b([A-Z0-9]{4,25})\b',
        re.IGNORECASE,
    )
    for m in article_pattern.finditer(text):
        candidate = m.group(1)
        # Must contain at least one Latin letter AND at least one digit
        has_latin = bool(re.search(r'[A-Za-z]', candidate))
        has_digit = bool(re.search(r'\d', candidate))
        if has_latin and has_digit:
            result["article"] = candidate.upper()
            break

    # Description: Cyrillic words between the article code and the quantity marker
    if result.get("article") and result.get("quantity"):
        art = re.escape(result["article"])
        qty = str(result["quantity"])
        desc_pat = re.compile(
            rf'\b{art}\b\s+([\u0400-\u04FF][\u0400-\u04FF\s\-]+?)\s+{qty}\s*шт',
            re.IGNORECASE,
        )
        dm = desc_pat.search(text)
        if dm:
            result["description"] = re.sub(r'\s+', ' ', dm.group(1)).strip()

    return result


def parse_body_data(text: str) -> dict:
    """
    Parse structured refund data from email body text.
    Returns dict with keys: article, brand, quantity, description, reason, client_name, order_id, comment.
    Handles both labelled (Field: Value) and free-form email bodies.
    """
    # Lookahead: stop before any known field label or end of string
    _STOP = (
        r'(?=\s*(?:'
        r'[Аа]ртикул|[Аа]рт\.|[Пп]роизводитель|[Бб]ренд|[Кк]оличество|[Кк]ол.?во'
        r'|[Тт]овар|[Нн]аименование|[Пп]ричина|[Кк]омментарий'
        r'|[Нн]омер\s+(?:входящего|заказа)|[Кк]омпания|[Оо]рганизация'
        r'|[Дд]ата|Тел\.|www\.'
        r')|\Z)'
    )

    def _find(patterns: list[str], txt: str) -> Optional[str]:
        for pattern in patterns:
            m = re.search(pattern, txt, re.IGNORECASE | re.MULTILINE | re.DOTALL)
            if m:
                val = m.group(1).strip()
                val = re.sub(r'[\s\u00a0]+', ' ', val).strip()
                return val if val else None
        return None

    result = {}

    result["article"] = _find(
        [rf'[Аа]ртикул\s*:\s*(.+?){_STOP}', rf'[Аа]рт\.\s*:\s*(.+?){_STOP}'],
        text,
    )
    result["brand"] = _find(
        [rf'[Пп]роизводитель\s*:\s*(.+?){_STOP}', rf'[Бб]ренд\s*:\s*(.+?){_STOP}'],
        text,
    )
    qty_str = _find(
        [r'[Кк]оличество\s*:\s*(\d+)', r'[Кк]ол-?во\s*:\s*(\d+)'],
        text,
    )
    result["quantity"] = int(qty_str) if qty_str and qty_str.isdigit() else 1
    result["description"] = _find(
        [rf'[Тт]овар\s*:\s*(.+?){_STOP}', rf'[Нн]аименование\s*:\s*(.+?){_STOP}'],
        text,
    )
    result["reason"] = _find(
        [rf'[Пп]ричина возврата\s*:\s*(.+?){_STOP}', rf'[Пп]ричина\s*:\s*(.+?){_STOP}'],
        text,
    )
    result["comment"] = _find(
        [rf'[Кк]омментарий\s*:\s*(.+?){_STOP}'],
        text,
    )
    result["order_id"] = _find(
        [rf'[Нн]омер входящего документа\s*:\s*(.+?){_STOP}', rf'[Нн]омер заказа\s*:\s*(.+?){_STOP}'],
        text,
    )
    result["client_name"] = _find(
        [rf'[Кк]омпания\s+(.+?){_STOP}', rf'[Оо]рганизация\s*:\s*(.+?){_STOP}'],
        text,
    )

    # Fallback: free-form parsing when structured labels are absent
    if not result.get("article"):
        freeform = _parse_freeform_item(text)
        if freeform.get("article"):
            result["article"] = freeform["article"]
            if not result.get("description"):
                result["description"] = freeform.get("description")
            if result["quantity"] == 1 and freeform.get("quantity"):
                result["quantity"] = freeform["quantity"]

    return result


async def create_refund_from_uid(uid: str, db: AsyncSession):
    """
    Fetch a specific email by IMAP UID and create a Refund from it.
    Skips all auto-import filters since the admin is explicitly choosing this email.
    Returns the created Refund object or raises an exception.
    """
    if not settings.MAIL_LOGIN or not settings.MAIL_PASSWORD:
        raise RuntimeError("IMAP credentials not configured")

    conn = imaplib.IMAP4_SSL(settings.MAIL_IMAP_HOST, settings.MAIL_IMAP_PORT)
    conn.login(settings.MAIL_LOGIN, settings.MAIL_PASSWORD)
    conn.select(settings.MAIL_FOLDER)

    try:
        _, msg_data = conn.uid("FETCH", uid.encode(), "(RFC822)")
        if not msg_data or not msg_data[0]:
            raise ValueError(f"Email with UID {uid} not found")

        raw = msg_data[0][1]
        msg = email.message_from_bytes(raw)

        subject = decode_str(msg.get("Subject", "Без темы"))
        from_header = decode_str(msg.get("From", ""))
        sender_name = email.utils.parseaddr(from_header)[0] or email.utils.parseaddr(from_header)[1]

        body_text = get_email_body_text(msg)
        # Include subject in parsed text so the free-form extractor can use it
        parsed = parse_body_data(f"{subject}\n{body_text}")

        client_name = parsed.get("client_name") or sender_name or from_header or "Неизвестный отправитель"

        reason = parsed.get("reason")
        if parsed.get("comment"):
            reason = f"{reason}\nКомментарий: {parsed['comment']}" if reason else parsed["comment"]

        logger.info(f"Creating refund from email UID={uid}: subject='{subject}' from='{from_header}'")

        display_id = await generate_display_id(db)

        refund = Refund(
            display_id=display_id,
            status=RefundStatus.received,
            source=RefundSource.email,
            client_name=client_name[:255],
            order_id=parsed.get("order_id"),
            reason=reason,
            email_subject=subject[:500],
            email_from=from_header[:255],
            email_uid=uid,
        )
        db.add(refund)
        await db.flush()
        await db.refresh(refund)

        refund_dir = Path(settings.UPLOAD_DIR) / f"refund_{refund.id}"
        refund_dir.mkdir(parents=True, exist_ok=True)

        xls_items_created = False

        # Create item from body text if article was found
        if parsed.get("article") and not xls_items_created:
            item = RefundItem(
                refund_id=refund.id,
                article=parsed["article"],
                brand=parsed.get("brand"),
                quantity=parsed.get("quantity", 1),
                price=0,
                description=parsed.get("description"),
            )
            db.add(item)
            xls_items_created = True
            logger.debug(f"Created item from email body for refund {refund.id}: article={parsed['article']}")

        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            # Accept attachments regardless of Content-Disposition value —
            # some clients send images as inline or omit the header entirely.
            filename = part.get_filename()
            if not filename:
                continue
            # Skip plain-text / HTML body parts that somehow have a name param
            if part.get_content_type() in ("text/plain", "text/html"):
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
        conn.store(uid.encode(), "+FLAGS", "\\Seen")
        logger.info(f"Created refund {display_id} (id={refund.id}) from email UID={uid}")
        return refund
    finally:
        try:
            conn.logout()
        except Exception:
            pass


async def process_emails(db: AsyncSession) -> int:
    """Connect to IMAP, fetch unseen emails, create Refunds. Returns number processed."""
    if not settings.MAIL_LOGIN or not settings.MAIL_PASSWORD:
        logger.warning("IMAP credentials not configured, skipping mail import")
        return 0

    # Check if auto-create is enabled
    from app.services.settings_service import get_setting
    auto_enabled = (await get_setting(db, "mail_auto_create_enabled")).lower() == "true"
    if not auto_enabled:
        return 0

    auto_since_str = await get_setting(db, "mail_auto_create_since")
    auto_since = None
    if auto_since_str:
        try:
            auto_since = datetime.fromisoformat(auto_since_str)
        except ValueError:
            logger.warning(f"Invalid mail_auto_create_since value: {auto_since_str}")

    processed = 0
    skipped = 0
    try:
        conn = imaplib.IMAP4_SSL(settings.MAIL_IMAP_HOST, settings.MAIL_IMAP_PORT)
        conn.login(settings.MAIL_LOGIN, settings.MAIL_PASSWORD)
        conn.select(settings.MAIL_FOLDER)

        search_criteria = _build_imap_search_criteria()
        _, uid_data = conn.uid("SEARCH", None, search_criteria)
        uids = uid_data[0].split() if uid_data[0] else []

        limit = settings.MAIL_FETCH_LIMIT
        total_found = len(uids)
        if limit > 0 and total_found > limit:
            # Take oldest first (lower UIDs), leave the rest for next cycle
            uids = uids[:limit]
            logger.info(f"Found {total_found} unseen emails, processing first {limit} this cycle")
        else:
            logger.info(f"Found {total_found} unseen emails")

        for uid_bytes in uids:
            try:
                uid_str = uid_bytes.decode() if isinstance(uid_bytes, bytes) else str(uid_bytes)
                accepted = await _process_single_email(conn, uid_str, db, auto_since=auto_since)
                if accepted:
                    processed += 1
                else:
                    skipped += 1
            except Exception as e:
                logger.error(f"Error processing email uid={uid_bytes}: {e}", exc_info=True)

        if skipped:
            logger.info(f"Skipped {skipped} emails (did not pass filters)")
        conn.logout()
    except Exception as e:
        logger.error(f"IMAP connection error: {e}", exc_info=True)

    return processed


async def _process_single_email(
    conn: imaplib.IMAP4_SSL,
    uid: str,
    db: AsyncSession,
    auto_since: Optional[datetime] = None,
) -> bool:
    """Process one email by UID. Returns True if refund was created, False if filtered out."""
    _, msg_data = conn.uid("FETCH", uid.encode(), "(RFC822)")
    if not msg_data or not msg_data[0]:
        return False
    raw = msg_data[0][1]
    msg = email.message_from_bytes(raw)

    subject = decode_str(msg.get("Subject", "Без темы"))
    from_header = decode_str(msg.get("From", ""))
    client_name = email.utils.parseaddr(from_header)[0] or email.utils.parseaddr(from_header)[1]

    # --- Date filter: skip emails received before auto-create was enabled ---
    if auto_since:
        from email.utils import parsedate_to_datetime
        try:
            email_date = parsedate_to_datetime(msg.get("Date", ""))
            if email_date < auto_since:
                logger.debug(f"Skipped (before auto-create enabled): subject='{subject}' date={email_date}")
                return False
        except Exception:
            pass  # If date parsing fails, proceed with other filters

    # --- Фильтрация ---
    if not _is_allowed_sender(from_header):
        logger.debug(f"Skipped (sender not in whitelist): from='{from_header}'")
        conn.uid("STORE", uid.encode(), "+FLAGS", "\\Seen")
        return False

    if not _has_subject_keyword(subject):
        logger.debug(f"Skipped (no keyword in subject): subject='{subject}'")
        conn.uid("STORE", uid.encode(), "+FLAGS", "\\Seen")
        return False

    if settings.MAIL_REQUIRE_XLS and not _has_xls_attachment(msg):
        logger.debug(f"Skipped (no XLS attachment): subject='{subject}' from='{from_header}'")
        conn.uid("STORE", uid.encode(), "+FLAGS", "\\Seen")
        return False
    # ------------------

    body_text = get_email_body_text(msg)
    # Include subject in parsed text so the free-form extractor can use it
    parsed = parse_body_data(f"{subject}\n{body_text}")

    client_name = parsed.get("client_name") or client_name or from_header or "Неизвестный отправитель"

    reason = parsed.get("reason")
    if parsed.get("comment"):
        reason = f"{reason}\nКомментарий: {parsed['comment']}" if reason else parsed["comment"]

    logger.info(f"Processing email: subject='{subject}' from='{from_header}'")

    display_id = await generate_display_id(db)

    refund = Refund(
        display_id=display_id,
        status=RefundStatus.received,
        source=RefundSource.email,
        client_name=client_name[:255],
        order_id=parsed.get("order_id"),
        reason=reason,
        email_subject=subject[:500],
        email_from=from_header[:255],
        email_uid=uid,
    )
    db.add(refund)
    await db.flush()
    await db.refresh(refund)

    refund_dir = Path(settings.UPLOAD_DIR) / f"refund_{refund.id}"
    refund_dir.mkdir(parents=True, exist_ok=True)

    xls_items_created = False

    # Create item from body text if article was found
    if parsed.get("article"):
        item = RefundItem(
            refund_id=refund.id,
            article=parsed["article"],
            brand=parsed.get("brand"),
            quantity=parsed.get("quantity", 1),
            price=0,
            description=parsed.get("description"),
        )
        db.add(item)
        xls_items_created = True
        logger.debug(f"Created item from email body for refund {refund.id}: article={parsed['article']}")

    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        # Accept attachments regardless of Content-Disposition value —
        # some clients send images as inline or omit the header entirely.
        filename = part.get_filename()
        if not filename:
            continue
        # Skip plain-text / HTML body parts that somehow have a name param
        if part.get_content_type() in ("text/plain", "text/html"):
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
    conn.uid("STORE", uid.encode(), "+FLAGS", "\\Seen")
    logger.info(f"Created refund {display_id} (id={refund.id}) from email uid={uid}")
    return True
