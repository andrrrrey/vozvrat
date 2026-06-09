import asyncio
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
from sqlalchemy import select, func, update as sa_update

from app.config import settings
from app.models.refund import Refund, RefundStatus, RefundSource
from app.models.refund_item import RefundItem
from app.models.file_attachment import FileAttachment, FileType
from app.models.mail_notification import MailNotification
from app.models.user import User, UserRole
from app.models.user_client_id import UserClientId
from app.models.supplier import Supplier
from app.services.file_service import detect_file_type, build_stored_name

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
    """Parse XLS/XLSX and return a list of item dicts.

    Each dict contains item-level fields (article, brand, quantity, price,
    description, position_id, comment) and refund-level fields from the same
    row (reason, order_id, client_ext_id, client_email, supplier_name).
    Refund-level fields are identical across rows in a typical report.
    """
    try:
        import openpyxl
        from openpyxl.descriptors import base as _desc_base

        # Some xlsx files (e.g. amx24's goodsReturnReport) contain invalid 'pane'/'view'
        # values like <selection pane=""> that openpyxl's NoneSet descriptor rejects.
        # Patch NoneSet.__set__ ONCE (guarded) to coerce unknown values to None instead
        # of raising. The guard keeps repeated calls from nesting wrappers.
        if not getattr(_desc_base, "_noneset_patched", False):
            _orig_set = _desc_base.NoneSet.__set__

            def _lenient_set(self, instance, value):
                try:
                    _orig_set(self, instance, value)
                except ValueError:
                    object.__setattr__(instance, self.name, None)

            _desc_base.NoneSet.__set__ = _lenient_set
            _desc_base._noneset_patched = True

        wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
        ws = wb.active

        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return []

        header_row = None
        header_idx = 0
        for i, row in enumerate(rows[:20]):
            row_lower = [str(c).lower().strip() if c is not None else "" for c in row]
            joined = " ".join(row_lower)
            if any(k in joined for k in ["артикул", "article", "код"]):
                header_row = row_lower
                header_idx = i
                break

        if header_row is None:
            return []

        col_map: dict[str, int] = {}
        for j, h in enumerate(header_row):
            if "артикул" in h or "article" in h or ("арт" in h and "марк" not in h):
                col_map.setdefault("article", j)
            elif "марк" in h or "бренд" in h or "brand" in h:
                col_map.setdefault("brand", j)
            elif h.startswith("кол") or "qty" in h or "количество" in h:
                col_map.setdefault("quantity", j)
            elif h == "цена" or "price" in h or "стоим" in h:
                col_map.setdefault("price", j)
            elif h == "сумма" or "total" in h or "итог" in h:
                col_map.setdefault("total", j)
            elif "описан" in h or "назван" in h or "наим" in h:
                col_map.setdefault("description", j)
            elif "причина" in h:
                col_map.setdefault("reason", j)
            elif "комментарий" in h or "коммент" in h:
                col_map.setdefault("comment", j)
            elif "id позиции" in h or h == "id позиции" or ("позиц" in h and "id" in h):
                col_map.setdefault("position_id", j)
            elif "id заказа" in h or h == "id заказа" or ("заказ" in h and "id" in h):
                col_map.setdefault("order_id", j)
            elif "id клиента" in h or h == "id клиента" or ("клиент" in h and "id" in h):
                col_map.setdefault("client_ext_id", j)
            elif "эл.адрес" in h or "эл. адрес" in h or "email" in h or "e-mail" in h:
                col_map.setdefault("client_email", j)
            elif ("клиент" in h and "id" not in h) or "покупатель" in h or "название клиента" in h or "наименование клиента" in h:
                col_map.setdefault("client_name", j)
            elif "поставщик" in h or "supplier" in h:
                col_map.setdefault("supplier_name", j)

        if "article" not in col_map:
            return []

        def _cell(row, key):
            idx = col_map.get(key)
            if idx is None or idx >= len(row):
                return None
            return row[idx]

        items = []
        for row in rows[header_idx + 1:]:
            if not row:
                continue
            article_val = _cell(row, "article")
            if article_val is None or str(article_val).strip() == "":
                continue

            qty_raw = _cell(row, "quantity")
            try:
                qty = int(qty_raw) if qty_raw is not None else 1
            except (ValueError, TypeError):
                qty = 1

            price_raw = _cell(row, "price")
            if price_raw is None:
                price_raw = _cell(row, "total")
            try:
                price = float(price_raw) if price_raw is not None else 0.0
            except (ValueError, TypeError):
                price = 0.0

            reason_raw = _cell(row, "reason")
            reason = str(reason_raw).strip() if reason_raw is not None else None

            comment_raw = _cell(row, "comment")
            comment = str(comment_raw).strip() if comment_raw is not None else None

            position_id_raw = _cell(row, "position_id")
            position_id = str(int(position_id_raw)) if position_id_raw is not None else None

            order_id_raw = _cell(row, "order_id")
            order_id = str(int(order_id_raw)) if isinstance(order_id_raw, (int, float)) else (str(order_id_raw).strip() if order_id_raw else None)

            client_ext_id_raw = _cell(row, "client_ext_id")
            client_ext_id = str(int(client_ext_id_raw)) if isinstance(client_ext_id_raw, (int, float)) else (str(client_ext_id_raw).strip() if client_ext_id_raw else None)

            client_email_raw = _cell(row, "client_email")
            client_email = str(client_email_raw).strip().lower() if client_email_raw else None

            supplier_name_raw = _cell(row, "supplier_name")
            supplier_name = str(supplier_name_raw).strip() if supplier_name_raw else None

            client_name_raw = _cell(row, "client_name")
            client_name = str(client_name_raw).strip() if client_name_raw else None

            brand_raw = _cell(row, "brand")
            description_raw = _cell(row, "description")

            item = {
                "article": str(article_val).strip(),
                "brand": str(brand_raw).strip() if brand_raw is not None else None,
                "quantity": qty,
                "price": price,
                "description": str(description_raw).strip() if description_raw is not None else None,
                "position_id": position_id,
                "comment": comment,
                "reason": reason,
                "order_id": order_id,
                "client_ext_id": client_ext_id,
                "client_email": client_email,
                "client_name": client_name,
                "supplier_name": supplier_name,
            }
            items.append(item)
        return items
    except Exception as e:
        logger.warning(f"XLS parse failed: {e}", exc_info=True)
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


def _parse_two_section_format(text: str) -> dict:
    """
    Parse two-section email body: column names on separate lines,
    then a blank line, then values in the same order.
    Example:
        Артикул
        Бренд
        Кол-во к возврату

        47034
        MEAT & DORIA
        1
    Accounts for a subject/title line at the start of the header block (offset).
    """
    LABEL_FIELDS = [
        (re.compile(r'^\s*[Аа]ртикул\.?$', re.I), 'article'),
        (re.compile(r'^\s*([Бб]ренд|[Пп]роизводитель|[Мм]арка)$', re.I), 'brand'),
        (re.compile(r'^\s*[Кк]ол', re.I), 'quantity'),
        (re.compile(r'^\s*[Сс]умма$', re.I), 'price'),
        (re.compile(r'^\s*[Пп]ричина', re.I), 'reason'),
        (re.compile(r'^\s*[Кк]омментарий$', re.I), 'comment'),
        (re.compile(r'^\s*ID\s+заказа$', re.I), 'order_id'),
        (re.compile(r'^\s*[Нн]омер\s+заказа', re.I), 'order_id'),
        (re.compile(r'^\s*ID\s+клиента$', re.I), 'client_ext_id'),
    ]

    def _match_label(line: str) -> Optional[str]:
        for pat, field in LABEL_FIELDS:
            if pat.search(line):
                return field
        return None

    blocks = [b.strip() for b in re.split(r'\n[ \t]*\n', text) if b.strip()]

    for bi in range(len(blocks) - 1):
        hdr_lines = [l.strip() for l in blocks[bi].splitlines() if l.strip()]
        val_lines = [l.strip() for l in blocks[bi + 1].splitlines() if l.strip()]

        if not hdr_lines or not val_lines:
            continue

        # Count non-label prefix lines (e.g. email subject prepended before the body)
        offset = 0
        for line in hdr_lines:
            if _match_label(line) is None:
                offset += 1
            else:
                break

        # Need at least 2 recognised label lines
        label_count = sum(1 for l in hdr_lines if _match_label(l) is not None)
        if label_count < 2:
            continue

        result: dict = {}
        for j, line in enumerate(hdr_lines):
            field = _match_label(line)
            if field is None:
                continue
            val_idx = j - offset
            if val_idx < 0 or val_idx >= len(val_lines):
                continue
            value = val_lines[val_idx].strip()
            if not value or value in ('-', '—', 'None', 'null'):
                continue

            if field == 'quantity':
                m = re.search(r'\d+', value)
                if m:
                    try:
                        result[field] = int(m.group())
                    except ValueError:
                        pass
            elif field == 'price':
                try:
                    result[field] = float(value.replace(',', '.').replace(' ', ''))
                except ValueError:
                    pass
            elif field not in result:
                result[field] = value

        if len(result) >= 2:
            return result

    return {}


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
    result["price"] = None
    result["client_ext_id"] = None

    result["article"] = _find(
        [rf'[Аа]ртикул\s*:\s*(.+?){_STOP}', rf'[Аа]рт\.\s*:\s*(.+?){_STOP}'],
        text,
    )
    result["brand"] = _find(
        [rf'[Пп]роизводитель\s*:\s*(.+?){_STOP}', rf'[Бб]ренд\s*:\s*(.+?){_STOP}'],
        text,
    )

    # Short fields must not span newlines: strip trailing commas/spaces and take first line only.
    # Without this, "Артикул: C7294LR,Производитель: ..." leaves a trailing comma in article,
    # and a brand pattern with re.DOTALL can swallow the entire email body into VARCHAR(255).
    if result.get("article"):
        result["article"] = result["article"].split('\n')[0].rstrip(', ').strip() or None
    if result.get("brand"):
        result["brand"] = result["brand"].split('\n')[0].rstrip(', ').strip() or None
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

    # Two-section format: column headers then values after blank line
    if not result.get("article"):
        two_sec = _parse_two_section_format(text)
        if two_sec:
            for _k in ('article', 'brand', 'order_id', 'reason', 'comment', 'client_ext_id'):
                if not result.get(_k) and two_sec.get(_k):
                    result[_k] = two_sec[_k]
            if result.get('quantity', 1) == 1 and two_sec.get('quantity'):
                result['quantity'] = two_sec['quantity']
            if two_sec.get('price'):
                result['price'] = two_sec['price']

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


async def _find_or_create_client(
    db: AsyncSession,
    client_ext_id: Optional[str],
    client_name: Optional[str],
    client_email: Optional[str] = None,
) -> User:
    """Look up a client User by client_id (UserClientId) or email; create one if not found."""
    from passlib.context import CryptContext
    import secrets
    pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

    # 1. Look up by client_ext_id in user_client_ids table
    if client_ext_id:
        result = await db.execute(
            select(User).join(UserClientId, UserClientId.user_id == User.id)
            .where(UserClientId.client_id == client_ext_id)
        )
        user = result.scalar_one_or_none()
        if user:
            logger.debug(f"Found existing client by client_id={client_ext_id}: user id={user.id}")
            if client_email and user.email.endswith("@imported.local"):
                user.email = client_email
            return user

    # 2. Look up by email
    if client_email:
        result = await db.execute(select(User).where(User.email == client_email))
        user = result.scalar_one_or_none()
        if user:
            logger.debug(f"Found existing client by email={client_email}: id={user.id}")
            if client_ext_id:
                # Add the client_id to the user if not already there
                exists_result = await db.execute(
                    select(UserClientId).where(
                        UserClientId.user_id == user.id,
                        UserClientId.client_id == client_ext_id,
                    )
                )
                if not exists_result.scalar_one_or_none():
                    db.add(UserClientId(user_id=user.id, client_id=client_ext_id))
            return user

    # 3. Create new user
    label = client_ext_id or client_email or "unknown"
    name = (client_name or f"Клиент {label}")[:255]
    email_to_use = client_email or f"ext_client_{client_ext_id}@imported.local"
    user = User(
        email=email_to_use,
        password_hash=pwd_ctx.hash(secrets.token_urlsafe(24)),
        full_name=name,
        role=UserRole.client,
        is_active=True,
    )
    db.add(user)
    await db.flush()
    if client_ext_id:
        db.add(UserClientId(user_id=user.id, client_id=client_ext_id))
    logger.info(f"Auto-created client user id={user.id} ext_id={client_ext_id} email={email_to_use} name='{name}'")
    return user


async def _find_supplier(db: AsyncSession, supplier_name: str) -> Optional[Supplier]:
    """Look up a Supplier by exact name (case-sensitive). Returns None if not found."""
    result = await db.execute(select(Supplier).where(Supplier.name == supplier_name))
    return result.scalar_one_or_none()


async def create_refund_from_uid(uid: str, db: AsyncSession):
    """
    Fetch a specific email by IMAP UID and create a Refund from it.
    Skips all auto-import filters since the admin is explicitly choosing this email.
    Returns the created Refund object or raises an exception.
    """
    if not settings.MAIL_LOGIN or not settings.MAIL_PASSWORD:
        raise RuntimeError("IMAP credentials not configured")

    conn = imaplib.IMAP4_SSL(settings.MAIL_IMAP_HOST, settings.MAIL_IMAP_PORT,
                              timeout=_IMAP_TIMEOUT)
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
        parsed = parse_body_data(f"{subject}\n{body_text}")

        # Pre-scan attachments to find XLS before creating the refund
        attachments_raw: list[tuple[str, bytes, str]] = []  # (filename, content, content_type)
        xls_items: list[dict] = []

        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            filename = part.get_filename()
            if not filename:
                continue
            if part.get_content_type() in ("text/plain", "text/html"):
                continue
            filename_decoded = decode_str(filename)
            content = part.get_payload(decode=True)
            if not content:
                continue
            attachments_raw.append((filename_decoded, content, part.get_content_type()))
            if not xls_items and filename_decoded.lower().endswith((".xls", ".xlsx")):
                parsed_items = try_parse_xls(content)
                if parsed_items:
                    xls_items = parsed_items

        # Merge: XLS data takes precedence over email body data
        xls_first = xls_items[0] if xls_items else {}
        reason = xls_first.get("reason") or parsed.get("reason")
        comment = xls_first.get("comment") or parsed.get("comment")
        order_id = xls_first.get("order_id") or parsed.get("order_id")
        client_ext_id = xls_first.get("client_ext_id") or parsed.get("client_ext_id")
        client_email_from_xls = xls_first.get("client_email")
        supplier_name_from_xls = xls_first.get("supplier_name")
        client_name_from_xls = xls_first.get("client_name")

        client_name = client_name_from_xls or parsed.get("client_name") or sender_name or from_header or "Неизвестный отправитель"

        # Resolve client user
        client_user_id = None
        if client_ext_id or client_email_from_xls:
            client_user = await _find_or_create_client(
                db, client_ext_id, client_name, client_email_from_xls
            )
            client_user_id = client_user.id
            client_name = client_user.full_name

        # Resolve supplier
        supplier_id = None
        unlinked_supplier_name = None
        if supplier_name_from_xls:
            supplier = await _find_supplier(db, supplier_name_from_xls)
            if supplier:
                supplier_id = supplier.id
            else:
                unlinked_supplier_name = supplier_name_from_xls

        reason_text = reason
        if comment:
            reason_text = f"{reason}\nКомментарий: {comment}" if reason else comment

        logger.info(f"Creating refund from email UID={uid}: subject='{subject}' from='{from_header}'")
        display_id = await generate_display_id(db)

        refund = Refund(
            display_id=display_id,
            status=RefundStatus.received,
            source=RefundSource.email_manual,
            client_name=client_name[:255],
            client_user_id=client_user_id,
            supplier_id=supplier_id,
            supplier_name=unlinked_supplier_name,
            order_id=str(order_id) if order_id else None,
            reason=reason_text,
            email_subject=subject[:500],
            email_from=from_header[:255],
            email_uid=uid,
        )
        db.add(refund)
        await db.flush()
        await db.refresh(refund)

        refund_dir = Path(settings.UPLOAD_DIR) / f"refund_{refund.id}"
        refund_dir.mkdir(parents=True, exist_ok=True)

        # Create RefundItems: XLS rows preferred, fall back to email body article
        if xls_items:
            for item_data in xls_items:
                item = RefundItem(
                    refund_id=refund.id,
                    article=item_data["article"][:255],
                    brand=item_data["brand"][:255] if item_data.get("brand") else None,
                    quantity=item_data.get("quantity", 1),
                    price=item_data.get("price", 0),
                    description=item_data.get("description"),
                    position_id=item_data.get("position_id"),
                    comment=item_data.get("comment"),
                )
                db.add(item)
            logger.debug(f"Created {len(xls_items)} items from XLS for refund {refund.id}")
        elif parsed.get("article"):
            item = RefundItem(
                refund_id=refund.id,
                article=parsed["article"][:255],
                brand=parsed["brand"][:255] if parsed.get("brand") else None,
                quantity=parsed.get("quantity", 1),
                price=parsed.get("price") or 0,
                description=parsed.get("description"),
            )
            db.add(item)
            logger.debug(f"Created item from email body for refund {refund.id}: article={parsed['article']}")

        # Save all attachments to disk. A single unwritable file must never abort the
        # whole refund, so each write is best-effort.
        for filename, content, _ctype in attachments_raw:
            try:
                file_type = detect_file_type(filename)
                unique_name = build_stored_name(filename)
                stored_path = str(refund_dir / unique_name)
                with open(stored_path, "wb") as f:
                    f.write(content)
            except Exception as att_err:
                logger.warning(f"Failed to save attachment '{filename}' for refund {refund.id}: {att_err}")
                continue
            attachment = FileAttachment(
                refund_id=refund.id,
                filename=filename,
                stored_path=stored_path,
                file_type=file_type,
                file_size=len(content),
            )
            db.add(attachment)

        await db.flush()

        # Upsert MailNotification linking this email to the new refund
        from app.models.mail_notification import MailNotification
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        _notif_insert = pg_insert(MailNotification).values(
            email_uid=uid,
            subject=subject[:500] if subject else None,
            from_email=email.utils.parseaddr(from_header)[1][:255],
            from_name=(email.utils.parseaddr(from_header)[0] or email.utils.parseaddr(from_header)[1])[:255],
            refund_id=refund.id,
            is_read=False,
        ).on_conflict_do_update(
            index_elements=['email_uid'],
            set_={'refund_id': refund.id}
        )
        await db.execute(_notif_insert)

        # Mark seen via UID STORE (the connection was searched/fetched by UID; conn.store
        # would treat `uid` as a sequence number and could raise). Best-effort: a failure
        # here must never roll back the refund we just created.
        try:
            conn.uid("STORE", uid.encode(), "+FLAGS", "\\Seen")
        except Exception as seen_err:
            logger.warning(f"Failed to mark email UID={uid} as Seen: {seen_err}")
        logger.info(f"Created refund {display_id} (id={refund.id}) from email UID={uid}")
        return refund
    finally:
        try:
            conn.logout()
        except Exception:
            pass


_IMAP_TIMEOUT = 30  # seconds; prevents indefinite event-loop freeze


def _imap_fetch_unseen_sync(criteria: str, limit: int) -> list[tuple[str, bytes]]:
    """
    Pure-sync IMAP fetch — runs in thread pool so the async event loop stays free.
    Connects, searches, batch-fetches unseen emails, marks them as Seen, disconnects.
    Returns list of (uid_str, raw_bytes).
    """
    results: list[tuple[str, bytes]] = []
    conn = None
    try:
        conn = imaplib.IMAP4_SSL(settings.MAIL_IMAP_HOST, settings.MAIL_IMAP_PORT,
                                  timeout=_IMAP_TIMEOUT)
        conn.login(settings.MAIL_LOGIN, settings.MAIL_PASSWORD)
        conn.select(settings.MAIL_FOLDER)

        _, uid_data = conn.uid("SEARCH", None, criteria)
        uids = uid_data[0].split() if uid_data[0] else []

        total_found = len(uids)
        if limit > 0 and total_found > limit:
            uids = uids[:limit]
            logger.info(f"Found {total_found} unseen emails, processing first {limit} this cycle")
        else:
            logger.info(f"Found {total_found} unseen emails")

        if not uids:
            return results

        # Batch FETCH — one round trip instead of N
        uid_list = b",".join(uids)
        _, msg_data_list = conn.uid("FETCH", uid_list, "(RFC822)")

        # Parse interleaved response: [(meta, raw), b')', ...]
        uid_iter = iter(uids)
        for item in msg_data_list:
            if not isinstance(item, tuple) or len(item) < 2:
                continue
            raw = item[1]
            if not raw:
                continue
            uid_bytes = next(uid_iter, None)
            if uid_bytes is None:
                break
            uid_str = uid_bytes.decode() if isinstance(uid_bytes, bytes) else str(uid_bytes)
            results.append((uid_str, raw))
    except Exception as e:
        logger.error(f"IMAP sync fetch error: {e}", exc_info=True)
    finally:
        if conn:
            try:
                conn.logout()
            except Exception:
                pass
    return results


def _imap_fetch_recent_raw_sync(limit: int) -> list[tuple[str, bytes]]:
    """Fetch raw bytes of the last ``limit`` messages by sequence number.

    Unlike _imap_fetch_unseen_sync this does NOT rely on the IMAP UNSEEN search —
    it grabs the newest messages regardless of their \\Seen flag. Used by the manual
    "reprocess recent" action, which must work even when the messages were already
    read on the server (so the scheduler's UNSEEN search never picks them up).
    Read-only select: does not mark anything as Seen.
    """
    results: list[tuple[str, bytes]] = []
    conn = None
    try:
        conn = imaplib.IMAP4_SSL(settings.MAIL_IMAP_HOST, settings.MAIL_IMAP_PORT,
                                  timeout=_IMAP_TIMEOUT)
        conn.login(settings.MAIL_LOGIN, settings.MAIL_PASSWORD)
        _, count_data = conn.select(settings.MAIL_FOLDER, readonly=True)
        total = int(count_data[0]) if count_data and count_data[0] else 0
        if total == 0:
            return results

        end_seq = total
        start_seq = max(1, end_seq - limit + 1)
        seq_range = f"{start_seq}:{end_seq}"
        _, msg_data_list = conn.fetch(seq_range, "(UID RFC822)")
        if not msg_data_list:
            return results

        for item in msg_data_list:
            if not isinstance(item, tuple) or len(item) < 2:
                continue
            meta = item[0]
            raw = item[1]
            if not raw:
                continue
            meta_str = meta.decode(errors="replace") if isinstance(meta, bytes) else str(meta)
            m = re.search(r"UID (\d+)", meta_str)
            if not m:
                continue
            results.append((m.group(1), raw))
    except Exception as e:
        logger.error(f"IMAP recent-raw fetch error: {e}", exc_info=True)
    finally:
        if conn:
            try:
                conn.logout()
            except Exception:
                pass
    # Newest first
    return list(reversed(results))


async def reprocess_recent_emails(db: AsyncSession, limit: int = 20) -> dict:
    """Manually re-run the auto-create pipeline over the last ``limit`` emails.

    Independent of the UNSEEN search and of the mail_auto_create_since cutoff, so it
    catches emails the scheduler missed (e.g. already-read messages). Applies the same
    sender/subject/XLS filters as auto-import, and skips any email that already has a
    refund (so manually-created ones are never duplicated).
    Returns a summary dict for the UI.
    """
    if not settings.MAIL_LOGIN or not settings.MAIL_PASSWORD:
        return {"error": "IMAP credentials not configured"}

    loop = asyncio.get_event_loop()
    raw_emails = await loop.run_in_executor(
        None, lambda: _imap_fetch_recent_raw_sync(limit)
    )

    # Emails that already have a refund (auto OR manual) must be left alone.
    res = await db.execute(
        select(Refund.email_uid).where(
            Refund.source.in_([RefundSource.email, RefundSource.email_manual]),
            Refund.email_uid.isnot(None),
        )
    )
    existing_uids = set(r[0] for r in res.all())

    processed = skipped = failed = already = 0
    for uid, raw in raw_emails:
        if uid in existing_uids:
            already += 1
            continue
        try:
            accepted, _reason = await _process_raw_email(uid, raw, db, auto_since=None)
            await db.commit()
            if accepted:
                processed += 1
            else:
                skipped += 1
        except Exception as e:
            failed += 1
            logger.error(f"Reprocess error for uid={uid}: {e}", exc_info=True)
            try:
                await db.rollback()
            except Exception:
                pass

    summary = {
        "total": len(raw_emails),
        "processed": processed,
        "skipped": skipped,
        "failed": failed,
        "already": already,
    }
    logger.info(f"Manual reprocess of last {limit} emails: {summary}")
    return summary


def _imap_mark_seen_sync(uid_strs: list[str]) -> None:
    """Mark a list of UIDs as \\Seen in IMAP. Best-effort — errors are logged."""
    if not uid_strs:
        return
    conn = None
    try:
        conn = imaplib.IMAP4_SSL(settings.MAIL_IMAP_HOST, settings.MAIL_IMAP_PORT,
                                  timeout=_IMAP_TIMEOUT)
        conn.login(settings.MAIL_LOGIN, settings.MAIL_PASSWORD)
        conn.select(settings.MAIL_FOLDER)
        uid_list = ",".join(uid_strs).encode()
        conn.uid("STORE", uid_list, "+FLAGS", "\\Seen")
    except Exception as e:
        logger.error(f"Failed to mark UIDs as Seen in IMAP: {e}", exc_info=True)
    finally:
        if conn:
            try:
                conn.logout()
            except Exception:
                pass


async def process_emails(db: AsyncSession) -> int:
    """Fetch unseen emails and create Refunds. IMAP runs in thread pool to avoid blocking event loop."""
    if not settings.MAIL_LOGIN or not settings.MAIL_PASSWORD:
        logger.warning("IMAP credentials not configured, skipping mail import")
        return 0

    from app.services.settings_service import get_setting
    auto_enabled = (await get_setting(db, "mail_auto_create_enabled")).lower() == "true"
    if not auto_enabled:
        return 0

    auto_since_str = await get_setting(db, "mail_auto_create_since")
    auto_since: Optional[datetime] = None
    if auto_since_str:
        try:
            auto_since = datetime.fromisoformat(auto_since_str)
        except ValueError:
            logger.warning(f"Invalid mail_auto_create_since value: {auto_since_str}")

    criteria = _build_imap_search_criteria()
    limit = settings.MAIL_FETCH_LIMIT

    # Phase 1: IMAP (sync, thread pool) — does NOT block the event loop
    loop = asyncio.get_event_loop()
    raw_emails = await loop.run_in_executor(
        None, lambda: _imap_fetch_unseen_sync(criteria, limit)
    )

    # Phase 2: process each fetched email with async DB
    processed = 0
    skipped = 0
    failed = 0
    uids_to_mark_seen: list[str] = []

    for uid, raw in raw_emails:
        try:
            accepted, _reason = await _process_raw_email(uid, raw, db, auto_since=auto_since)
            # Commit each email independently so one bad email can never discard the
            # refunds created from the others in this batch.
            await db.commit()
            if accepted:
                processed += 1
            else:
                skipped += 1
            # Mark Seen for both accepted and filtered emails; failed ones stay Unseen for retry
            uids_to_mark_seen.append(uid)
        except Exception as e:
            failed += 1
            logger.error(f"Error processing email uid={uid}: {e}", exc_info=True)
            # Roll back the aborted transaction before issuing any further statements,
            # otherwise the session is poisoned (PendingRollbackError) for the rest of the batch.
            try:
                await db.rollback()
            except Exception:
                pass
            # Record the failure in its own transaction so it survives even if the
            # original email left the session in a broken state. The rollback above also
            # discarded the notification upsert from _process_raw_email, so upsert here
            # (insert-or-update) to guarantee the "Ошибка" badge shows in the UI.
            try:
                from sqlalchemy.dialects.postgresql import insert as pg_insert
                fail_msg = str(e)[:500]
                msg_fail = email.message_from_bytes(raw)
                subject_fail = decode_str(msg_fail.get("Subject", "Без темы"))
                from_header_fail = decode_str(msg_fail.get("From", ""))
                from_email_fail = email.utils.parseaddr(from_header_fail)[1]
                from_name_fail = email.utils.parseaddr(from_header_fail)[0] or from_email_fail
                await db.execute(
                    pg_insert(MailNotification).values(
                        email_uid=uid,
                        subject=subject_fail[:500] if subject_fail else None,
                        from_email=from_email_fail[:255] if from_email_fail else None,
                        from_name=from_name_fail[:255] if from_name_fail else None,
                        refund_id=None,
                        is_read=False,
                        processing_status="failed",
                        skip_reason=fail_msg,
                    ).on_conflict_do_update(
                        index_elements=["email_uid"],
                        set_={"processing_status": "failed", "skip_reason": fail_msg},
                    )
                )
                await db.commit()
            except Exception:
                try:
                    await db.rollback()
                except Exception:
                    pass

    if skipped:
        logger.info(f"Skipped {skipped} emails (did not pass filters)")
    if failed:
        logger.warning(f"Failed to process {failed} emails (will retry next cycle)")

    # Mark Seen in IMAP for all successfully processed or filtered emails
    if uids_to_mark_seen:
        await loop.run_in_executor(None, lambda: _imap_mark_seen_sync(uids_to_mark_seen))

    return processed


async def _process_raw_email(
    uid: str,
    raw: bytes,
    db: AsyncSession,
    auto_since: Optional[datetime] = None,
) -> tuple[bool, Optional[str]]:
    """Process a pre-fetched raw email. Returns (accepted, skip_reason)."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    msg = email.message_from_bytes(raw)

    subject = decode_str(msg.get("Subject", "Без темы"))
    from_header = decode_str(msg.get("From", ""))
    client_name = email.utils.parseaddr(from_header)[0] or email.utils.parseaddr(from_header)[1]

    # Date filter: skip emails received before auto-create was enabled
    if auto_since:
        from email.utils import parsedate_to_datetime
        try:
            email_date = parsedate_to_datetime(msg.get("Date", ""))
            if email_date < auto_since:
                logger.debug(f"Skipped (before auto-create enabled): subject='{subject}' date={email_date}")
                return False, "before_auto_since"
        except Exception:
            pass

    # Upsert MailNotification so the bell reflects all new emails
    sender_name_for_notif = email.utils.parseaddr(from_header)[0] or email.utils.parseaddr(from_header)[1]
    notif_stmt = pg_insert(MailNotification).values(
        email_uid=uid,
        subject=subject[:500] if subject else None,
        from_email=email.utils.parseaddr(from_header)[1][:255],
        from_name=sender_name_for_notif[:255] if sender_name_for_notif else None,
        refund_id=None,
        is_read=False,
        processing_status="new",
        skip_reason=None,
    ).on_conflict_do_nothing(index_elements=["email_uid"])
    await db.execute(notif_stmt)

    # Deduplication: if already processed successfully, skip without error
    existing = await db.execute(
        select(MailNotification.processing_status).where(MailNotification.email_uid == uid)
    )
    existing_status = existing.scalar_one_or_none()
    if existing_status == "processed":
        logger.debug(f"Skipped (already processed in DB): uid={uid}")
        return True, None

    async def _record_skip(reason: str) -> None:
        await db.execute(
            sa_update(MailNotification)
            .where(MailNotification.email_uid == uid)
            .values(processing_status="skipped", skip_reason=reason)
        )

    if not _is_allowed_sender(from_header):
        logger.debug(f"Skipped (sender not in whitelist): from='{from_header}'")
        await _record_skip("Отправитель не в списке разрешённых")
        return False, "sender_not_allowed"

    if not _has_subject_keyword(subject):
        logger.debug(f"Skipped (no keyword in subject): subject='{subject}'")
        await _record_skip("Тема письма не содержит ключевых слов")
        return False, "no_keyword"

    if settings.MAIL_REQUIRE_XLS and not _has_xls_attachment(msg):
        logger.debug(f"Skipped (no XLS attachment): subject='{subject}' from='{from_header}'")
        await _record_skip("Нет XLS/XLSX вложения")
        return False, "no_xls"

    body_text = get_email_body_text(msg)
    parsed = parse_body_data(f"{subject}\n{body_text}")

    # Pre-scan attachments to find XLS before creating the refund
    attachments_raw: list[tuple[str, bytes, str]] = []
    xls_items: list[dict] = []

    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        filename = part.get_filename()
        if not filename:
            continue
        if part.get_content_type() in ("text/plain", "text/html"):
            continue
        filename_decoded = decode_str(filename)
        content = part.get_payload(decode=True)
        if not content:
            continue
        attachments_raw.append((filename_decoded, content, part.get_content_type()))
        if not xls_items and filename_decoded.lower().endswith((".xls", ".xlsx")):
            parsed_items = try_parse_xls(content)
            if parsed_items:
                xls_items = parsed_items

    # Merge: XLS data takes precedence over email body data
    xls_first = xls_items[0] if xls_items else {}
    reason = xls_first.get("reason") or parsed.get("reason")
    comment = xls_first.get("comment") or parsed.get("comment")
    order_id = xls_first.get("order_id") or parsed.get("order_id")
    client_ext_id = xls_first.get("client_ext_id") or parsed.get("client_ext_id")
    client_email_from_xls = xls_first.get("client_email")
    supplier_name_from_xls = xls_first.get("supplier_name")
    client_name_from_xls = xls_first.get("client_name")

    client_name = client_name_from_xls or parsed.get("client_name") or client_name or from_header or "Неизвестный отправитель"

    client_user_id = None
    if client_ext_id or client_email_from_xls:
        client_user = await _find_or_create_client(
            db, client_ext_id, client_name, client_email_from_xls
        )
        client_user_id = client_user.id
        client_name = client_user.full_name

    # Resolve supplier
    supplier_id = None
    unlinked_supplier_name = None
    if supplier_name_from_xls:
        supplier = await _find_supplier(db, supplier_name_from_xls)
        if supplier:
            supplier_id = supplier.id
        else:
            unlinked_supplier_name = supplier_name_from_xls

    reason_text = reason
    if comment:
        reason_text = f"{reason}\nКомментарий: {comment}" if reason else comment

    logger.info(f"Processing email: subject='{subject}' from='{from_header}'")
    display_id = await generate_display_id(db)

    refund = Refund(
        display_id=display_id,
        status=RefundStatus.received,
        source=RefundSource.email,
        client_name=client_name[:255],
        client_user_id=client_user_id,
        supplier_id=supplier_id,
        supplier_name=unlinked_supplier_name,
        order_id=str(order_id) if order_id else None,
        reason=reason_text,
        email_subject=subject[:500],
        email_from=from_header[:255],
        email_uid=uid,
    )
    db.add(refund)
    await db.flush()
    await db.refresh(refund)

    refund_dir = Path(settings.UPLOAD_DIR) / f"refund_{refund.id}"
    refund_dir.mkdir(parents=True, exist_ok=True)

    # Create RefundItems: XLS rows preferred, fall back to email body article
    if xls_items:
        for item_data in xls_items:
            item = RefundItem(
                refund_id=refund.id,
                article=item_data["article"][:255],
                brand=item_data["brand"][:255] if item_data.get("brand") else None,
                quantity=item_data.get("quantity", 1),
                price=item_data.get("price", 0),
                description=item_data.get("description"),
                position_id=item_data.get("position_id"),
                comment=item_data.get("comment"),
            )
            db.add(item)
        logger.debug(f"Created {len(xls_items)} items from XLS for refund {refund.id}")
    elif parsed.get("article"):
        item = RefundItem(
            refund_id=refund.id,
            article=parsed["article"][:255],
            brand=parsed["brand"][:255] if parsed.get("brand") else None,
            quantity=parsed.get("quantity", 1),
            price=parsed.get("price") or 0,
            description=parsed.get("description"),
        )
        db.add(item)

    # Save all attachments to disk. A single unwritable file must never abort the
    # whole refund, so each write is best-effort.
    for filename, content, _ctype in attachments_raw:
        try:
            file_type = detect_file_type(filename)
            unique_name = build_stored_name(filename)
            stored_path = str(refund_dir / unique_name)
            with open(stored_path, "wb") as f:
                f.write(content)
        except Exception as att_err:
            logger.warning(f"Failed to save attachment '{filename}' for refund {refund.id}: {att_err}")
            continue
        attachment = FileAttachment(
            refund_id=refund.id,
            filename=filename,
            stored_path=stored_path,
            file_type=file_type,
            file_size=len(content),
        )
        db.add(attachment)

    await db.flush()

    await db.execute(
        sa_update(MailNotification)
        .where(MailNotification.email_uid == uid)
        .values(refund_id=refund.id, processing_status="processed", skip_reason=None)
    )

    logger.info(f"Created refund {display_id} (id={refund.id}) from email uid={uid}")
    return True, None
