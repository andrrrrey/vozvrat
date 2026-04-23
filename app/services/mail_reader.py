import imaplib
import email
import logging
from email.header import decode_header
from email.utils import parseaddr, parsedate_to_datetime
from datetime import datetime
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)


def _decode_str(s: str) -> str:
    """Decode encoded email header string."""
    if not s:
        return ""
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


def _get_body(msg: email.message.Message) -> tuple[str, str]:
    """Extract plain text and html body. Returns (text, html)."""
    body_text = ""
    body_html = ""

    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            disp = str(part.get("Content-Disposition", ""))
            if "attachment" in disp:
                continue
            if ct == "text/plain" and not body_text:
                try:
                    charset = part.get_content_charset() or "utf-8"
                    payload = part.get_payload(decode=True)
                    if payload:
                        body_text = payload.decode(charset, errors="replace")
                except Exception:
                    pass
            elif ct == "text/html" and not body_html:
                try:
                    charset = part.get_content_charset() or "utf-8"
                    payload = part.get_payload(decode=True)
                    if payload:
                        body_html = payload.decode(charset, errors="replace")
                except Exception:
                    pass
    else:
        ct = msg.get_content_type()
        try:
            charset = msg.get_content_charset() or "utf-8"
            payload = msg.get_payload(decode=True)
            if payload:
                text = payload.decode(charset, errors="replace")
                if ct == "text/html":
                    body_html = text
                else:
                    body_text = text
        except Exception:
            pass

    return body_text, body_html


def _strip_html(html: str) -> str:
    """Very basic HTML tag stripper for fallback display."""
    import re
    text = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<p[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _get_attachments(msg: email.message.Message) -> list[dict]:
    """Return list of attachment info dicts (no content)."""
    attachments = []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        disp = str(part.get("Content-Disposition", ""))
        filename = part.get_filename()
        if not filename and "attachment" not in disp:
            continue
        if filename:
            filename = _decode_str(filename)
        else:
            filename = "unnamed"

        content = part.get_payload(decode=True)
        size = len(content) if content else 0
        attachments.append({
            "filename": filename,
            "content_type": part.get_content_type(),
            "size": size,
            "is_xls": filename.lower().endswith((".xls", ".xlsx")),
            "is_image": part.get_content_maintype() == "image",
            "is_pdf": filename.lower().endswith(".pdf"),
        })
    return attachments


def fetch_recent_emails(limit: int = 20, offset: int = 0) -> list[dict]:
    """
    Fetch emails from IMAP (read-only, no marking as seen).
    Uses sequence numbers (not SEARCH ALL) for O(1) pagination — avoids
    transferring thousands of UIDs over the wire on large mailboxes.
    offset=0 → newest `limit` emails; offset=20 → next 20, etc.
    """
    import re as _re
    if not settings.MAIL_LOGIN or not settings.MAIL_PASSWORD:
        logger.warning("IMAP credentials not configured, cannot fetch emails")
        return []

    emails = []
    conn = None
    try:
        conn = imaplib.IMAP4_SSL(settings.MAIL_IMAP_HOST, settings.MAIL_IMAP_PORT)
        conn.login(settings.MAIL_LOGIN, settings.MAIL_PASSWORD)

        # SELECT returns total message count — no SEARCH ALL needed
        _, count_data = conn.select(settings.MAIL_FOLDER, readonly=True)
        total_msgs = int(count_data[0]) if count_data and count_data[0] else 0

        if total_msgs == 0:
            return []

        # Newest messages have the highest sequence numbers
        end_seq = total_msgs - offset
        start_seq = max(1, end_seq - limit + 1)

        if end_seq < 1:
            return []

        # Single batch FETCH — one round trip for all messages
        seq_range = f"{start_seq}:{end_seq}"
        _, msg_data_list = conn.fetch(seq_range, "(UID FLAGS RFC822)")

        if not msg_data_list:
            return []

        # imap response interleaves tuple(meta, raw) and b')' separators
        parsed_msgs = []
        for item in msg_data_list:
            if not isinstance(item, tuple) or len(item) < 2:
                continue
            meta_bytes = item[0]
            raw = item[1]
            if not raw:
                continue

            meta_str = meta_bytes.decode(errors="replace") if isinstance(meta_bytes, bytes) else str(meta_bytes)

            uid_m = _re.search(r'UID (\d+)', meta_str)
            uid = uid_m.group(1) if uid_m else ""
            flags_m = _re.search(r'FLAGS \(([^)]*)\)', meta_str)
            flags_str = flags_m.group(1) if flags_m else ""
            is_seen = "\\Seen" in flags_str

            try:
                msg = email.message_from_bytes(raw)
            except Exception:
                continue

            subject = _decode_str(msg.get("Subject", "Без темы"))
            from_header = _decode_str(msg.get("From", ""))
            date_str = msg.get("Date", "")

            date_parsed: Optional[datetime] = None
            try:
                date_parsed = parsedate_to_datetime(date_str)
            except Exception:
                pass

            from_name, from_email_addr = parseaddr(from_header)
            if not from_name:
                from_name = from_email_addr

            body_text, body_html = _get_body(msg)
            attachments = _get_attachments(msg)
            display_body = body_text or (_strip_html(body_html) if body_html else "")

            parsed_msgs.append({
                "uid": uid,
                "subject": subject,
                "from_header": from_header,
                "from_name": from_name or from_email_addr,
                "from_email": from_email_addr,
                "date": date_parsed,
                "date_str": date_str,
                "body": display_body[:8000] if display_body else "",
                "attachments": attachments,
                "has_xls": any(a["is_xls"] for a in attachments),
                "is_seen": is_seen,
            })

        # Sequence fetch returns oldest-first; reverse for newest-first display
        emails = list(reversed(parsed_msgs))

        conn.logout()
    except Exception as e:
        logger.error(f"IMAP connection error in fetch_recent_emails: {e}", exc_info=True)
        if conn:
            try:
                conn.logout()
            except Exception:
                pass

    return emails
