"""
Диагностика автоимпорта почты: почему SEARCH UNSEEN не находит письма.

Запуск на сервере:
    cd /var/www/vozvrat
    venv/bin/python diagnose_imap.py

Скрипт ничего не меняет в ящике (открывает папку только на чтение, BODY.PEEK не
снимает флаг \\Seen). Пароль не печатается.
"""
import imaplib
import email
import re
from email.header import decode_header

from app.config import settings
from app.services.mail_import import (
    _build_imap_search_criteria,
    _has_subject_keyword,
    _is_allowed_sender,
    decode_str,
)


def _decode(s):
    if not s:
        return ""
    out = []
    for part, enc in decode_header(s):
        if isinstance(part, bytes):
            try:
                out.append(part.decode(enc or "utf-8", errors="replace"))
            except Exception:
                out.append(part.decode("latin-1", errors="replace"))
        else:
            out.append(part)
    return "".join(out)


def main():
    print("=" * 70)
    print("КОНФИГУРАЦИЯ ФИЛЬТРОВ")
    print("=" * 70)
    print(f"MAIL_FOLDER            = {settings.MAIL_FOLDER!r}")
    print(f"MAIL_REQUIRE_XLS       = {settings.MAIL_REQUIRE_XLS}")
    print(f"MAIL_SUBJECT_KEYWORDS  = {settings.MAIL_SUBJECT_KEYWORDS!r}")
    print(f"MAIL_ALLOWED_SENDERS   = {settings.MAIL_ALLOWED_SENDERS!r}")
    print(f"MAIL_FETCH_LIMIT       = {settings.MAIL_FETCH_LIMIT}")
    print(f"MAIL_LOGIN             = {settings.MAIL_LOGIN!r}")

    criteria = _build_imap_search_criteria()
    print()
    print(f">>> Критерий поиска планировщика: {criteria!r}")
    if criteria != "UNSEEN":
        print("    ВНИМАНИЕ: к UNSEEN добавлен серверный SUBJECT-фильтр (из ASCII-ключевых")
        print("    слов). Письма с другой темой сервер вернёт как 0 — даже если они непрочитаны.")
    print()

    if not settings.MAIL_LOGIN or not settings.MAIL_PASSWORD:
        print("IMAP не настроен (нет логина/пароля). Останавливаюсь.")
        return

    conn = imaplib.IMAP4_SSL(settings.MAIL_IMAP_HOST, settings.MAIL_IMAP_PORT, timeout=30)
    conn.login(settings.MAIL_LOGIN, settings.MAIL_PASSWORD)
    _, count_data = conn.select(settings.MAIL_FOLDER, readonly=True)
    total = int(count_data[0]) if count_data and count_data[0] else 0

    print("=" * 70)
    print("ОТВЕТЫ СЕРВЕРА")
    print("=" * 70)
    print(f"Всего писем в папке: {total}")

    _, d = conn.uid("SEARCH", None, "UNSEEN")
    unseen_uids = d[0].split() if d and d[0] else []
    print(f"SEARCH UNSEEN              -> {len(unseen_uids)} писем")

    _, d = conn.uid("SEARCH", None, criteria)
    crit_uids = d[0].split() if d and d[0] else []
    print(f"SEARCH {criteria!r:25} -> {len(crit_uids)} писем (это и есть выборка планировщика)")

    print()
    print("=" * 70)
    print("ПОСЛЕДНИЕ 20 ПИСЕМ: реальные флаги и проверка фильтров")
    print("=" * 70)
    if total == 0:
        conn.logout()
        return

    start = max(1, total - 19)
    seq_range = f"{start}:{total}"
    # BODY.PEEK не снимает \Seen
    _, msgs = conn.fetch(seq_range, "(UID FLAGS BODY.PEEK[HEADER.FIELDS (SUBJECT FROM)])")

    seen_cnt = unseen_cnt = 0
    for item in msgs:
        if not isinstance(item, tuple) or len(item) < 2:
            continue
        meta = item[0].decode(errors="replace") if isinstance(item[0], bytes) else str(item[0])
        hdr_raw = item[1]
        uid_m = re.search(r"UID (\d+)", meta)
        flags_m = re.search(r"FLAGS \(([^)]*)\)", meta)
        uid = uid_m.group(1) if uid_m else "?"
        flags = flags_m.group(1) if flags_m else ""
        is_seen = "\\Seen" in flags

        msg = email.message_from_bytes(hdr_raw) if hdr_raw else None
        subject = _decode(msg.get("Subject", "")) if msg else ""
        from_h = _decode(msg.get("From", "")) if msg else ""

        if is_seen:
            seen_cnt += 1
        else:
            unseen_cnt += 1

        kw_ok = _has_subject_keyword(subject)
        snd_ok = _is_allowed_sender(from_h)
        marks = []
        marks.append("ПРОЧИТАНО" if is_seen else "непрочит.")
        if not kw_ok:
            marks.append("тема НЕ проходит фильтр")
        if not snd_ok:
            marks.append("отправитель НЕ в whitelist")

        print(f"UID {uid:>6} | FLAGS=({flags or '-'}) | {' / '.join(marks)}")
        print(f"          тема: {subject[:80]}")
        print(f"          от:   {from_h[:80]}")

    print()
    print("-" * 70)
    print(f"Из последних 20: прочитанных={seen_cnt}, непрочитанных={unseen_cnt}")
    print("ВЫВОД:")
    if criteria != "UNSEEN" and unseen_cnt > 0:
        print("  • Есть непрочитанные письма, но критерий содержит SUBJECT-фильтр —")
        print("    скорее всего причина в MAIL_SUBJECT_KEYWORDS (см. выше).")
    elif seen_cnt > 0 and unseen_cnt == 0:
        print("  • Письма реально помечены \\Seen на сервере (их кто-то/что-то читает")
        print("    раньше тика планировщика). Автоимпорт по UNSEEN их не возьмёт.")
    elif unseen_cnt > 0 and len(unseen_uids) == 0:
        print("  • Сервер сообщает FLAGS без \\Seen, но SEARCH UNSEEN=0 — расхождение")
        print("    на стороне IMAP-сервера (нестандартное поведение SEARCH).")
    else:
        print("  • Смотри строки выше: помечено ли письмо ПРОЧИТАНО и проходит ли фильтры.")
    conn.logout()


if __name__ == "__main__":
    main()
