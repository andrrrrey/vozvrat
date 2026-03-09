# Промпт для Claude Code — Этап 1: Vozvrat (Система управления возвратами)

## Контекст проекта

Ты разрабатываешь веб-приложение **Vozvrat** — систему для создания, обработки и мониторинга возвратов товаров. Приложение предназначено для компании, которая принимает возвраты от клиентов (ИП, ООО) и работает с поставщиками. На этапе 1 реализуется ядро системы без FTP-интеграции.

## Стек технологий

- **Backend**: FastAPI, SQLAlchemy 2.0 (async), Alembic, Pydantic v2
- **Frontend**: Jinja2 + HTMX + TailwindCSS (CDN)
- **БД**: PostgreSQL (asyncpg)
- **Авторизация**: JWT в httponly cookies
- **Почта**: imaplib + email (парсинг IMAP mail.ru)
- **Фоновые задачи**: APScheduler
- **Файлы**: локальное хранение, пути в БД
- **Деплой**: systemd + nginx + uvicorn на VPS

## Структура проекта

```
vozvrat/
├── alembic/
│   ├── versions/
│   └── env.py
├── alembic.ini
├── app/
│   ├── __init__.py
│   ├── main.py                  # FastAPI app, lifespan, middleware
│   ├── config.py                # Pydantic Settings (env)
│   ├── database.py              # async engine, sessionmaker, get_db
│   ├── models/
│   │   ├── __init__.py
│   │   ├── user.py
│   │   ├── refund.py            # Return/возврат
│   │   ├── refund_item.py       # Позиции товаров в возврате
│   │   ├── supplier.py
│   │   ├── file_attachment.py
│   │   └── message.py           # Переписка (заготовка для этапа 2)
│   ├── schemas/
│   │   ├── __init__.py
│   │   ├── user.py
│   │   ├── refund.py
│   │   ├── supplier.py
│   │   └── auth.py
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── auth.py              # login, logout, me
│   │   ├── refunds.py           # CRUD возвратов, смена статуса
│   │   ├── suppliers.py         # CRUD поставщиков
│   │   ├── users.py             # управление пользователями (админ)
│   │   ├── files.py             # загрузка/скачивание файлов
│   │   └── pages.py             # Jinja2 HTML-страницы
│   ├── services/
│   │   ├── __init__.py
│   │   ├── auth.py              # JWT create/verify, password hashing
│   │   ├── mail_import.py       # IMAP парсинг входящих писем
│   │   └── file_service.py      # сохранение/отдача файлов
│   ├── templates/
│   │   ├── base.html            # layout с сайдбаром, HTMX подключение
│   │   ├── login.html
│   │   ├── statistics.html
│   │   ├── refunds/
│   │   │   ├── list.html
│   │   │   ├── card.html        # карточка для сотрудника
│   │   │   ├── create.html
│   │   │   └── _table_rows.html # partial для HTMX-обновления таблицы
│   │   ├── suppliers/
│   │   │   ├── list.html
│   │   │   └── _form_modal.html
│   │   ├── users/
│   │   │   ├── list.html
│   │   │   └── _form_modal.html
│   │   └── client/
│   │       ├── list.html        # ЛК клиента
│   │       └── card.html        # карточка для клиента
│   └── static/
│       └── css/
│           └── custom.css       # только кастомные стили из макета
├── uploads/                     # загруженные файлы (вне app/)
├── requirements.txt
├── .env.example
└── README.md
```

## Модели данных (SQLAlchemy 2.0, async)

### User
```
id: int PK
email: str (unique, indexed)
password_hash: str
full_name: str
role: Enum("admin", "staff", "client")  # используй SQLAlchemy Enum
is_active: bool (default True)
created_at: datetime (server_default=now)
```

### Supplier
```
id: int PK
name: str (уникальное название компании)
email: str (контактный email для отправки возвратов)
is_active: bool (default True)
created_at: datetime
```

### Refund (возврат)
```
id: int PK
display_id: str (генерируемый номер типа "#99281", unique, indexed)
status: Enum("received", "in_progress", "approved", "sent_to_supplier", "stock", "completed", "archive")
  # Получен, В работе, Согласован, Отдан поставщику, Сток, Проведен, Архив
source: Enum("manual", "email")  # откуда создан
client_name: str  # название клиента (ИП ТехноМир, ООО ...)
client_user_id: int FK -> User (nullable) # привязка к пользователю-клиенту
supplier_id: int FK -> Supplier (nullable)
order_id: str (nullable) # внешний ID заказа (#ORD-55441)
reason: text (nullable) # причина возврата
created_at: datetime
updated_at: datetime
created_by_id: int FK -> User (nullable) # кто создал (сотрудник)
email_subject: str (nullable)  # тема входящего письма
email_from: str (nullable)     # от кого пришло письмо
```

### RefundItem (позиция товара в возврате)
```
id: int PK
refund_id: int FK -> Refund
article: str        # артикул (BOSCH-199-22)
brand: str (nullable) # марка (Bosch)
quantity: int (default 1)
price: Decimal(10,2)
description: str (nullable) # описание товара
```

### FileAttachment
```
id: int PK
refund_id: int FK -> Refund
filename: str         # оригинальное имя файла
stored_path: str      # путь на диске (uploads/refund_123/uuid_filename.xls)
file_type: Enum("xls", "photo", "pdf_ukd", "other")
file_size: int
uploaded_at: datetime
uploaded_by_id: int FK -> User (nullable)
```

### Message (заготовка для этапа 2, создать модель но без роутеров)
```
id: int PK
refund_id: int FK -> Refund
user_id: int FK -> User
text: str
visibility: Enum("all", "staff_only")
created_at: datetime
```

## Авторизация и роли

### JWT в httponly cookies
- При логине: POST `/api/auth/login` принимает email+password, возвращает Set-Cookie с JWT (httponly, samesite=lax, secure в проде).
- JWT payload: `{"sub": user_id, "role": role, "exp": ...}`. Время жизни токена — 24 часа.
- Middleware/dependency `get_current_user` — извлекает JWT из cookie, декодирует, возвращает User. При невалидном токене — редирект на `/login`.
- Логаут: POST `/api/auth/logout` — удаляет cookie.

### Контроль доступа
- **admin**: всё что может staff + управление пользователями (CRUD в `/users`).
- **staff**: все возвраты, все статусы, создание возвратов, поставщики, переписка.
- **client**: только свои возвраты (WHERE client_user_id = current_user.id), скачивание своих файлов, переписка только "all" visibility.

Реализуй dependency-функции:
- `require_role(*roles)` — проверяет роль текущего пользователя
- `require_staff_or_admin` — shortcut для staff+admin
- Для страниц клиента — отдельные роуты с проверкой client_user_id

## Экраны и HTML-шаблоны (Jinja2 + HTMX + Tailwind)

### КРИТИЧЕСКИ ВАЖНО: Дизайн-система из макета

Точно воспроизведи визуальный стиль из HTML-макета:

**Общие правила:**
- Шрифт: Inter (Google Fonts CDN), моноширинный IBM Plex Mono для артикулов и ID.
- Скругления: `rounded-2xl` для карточек, `rounded-3xl` для крупных блоков, `rounded-[32px]` для основных контейнеров, `rounded-full` для бейджей статусов.
- Тени: `shadow-sm` для карточек, `shadow-lg` для CTA-кнопок, `shadow-xl` для модальных окон.
- Бордеры: `border border-gray-100` на карточках.
- Фон страниц: `bg-gray-50`.
- Заголовки секций: `text-[10px] font-bold text-gray-400 uppercase tracking-widest`.
- Анимация при переходах: класс `animate-screen` (fadeInBlur, определён в макете).

**Статусы — цветовые бейджи:**
```
Получен     → bg-blue-100 text-blue-700
В работе    → bg-yellow-100 text-yellow-700
Согласован  → bg-green-100 text-green-700
Отдан поставщику → bg-indigo-100 text-indigo-700
Сток        → bg-orange-100 text-orange-700
Проведен    → bg-emerald-100 text-emerald-700
Архив       → bg-gray-100 text-gray-500
```
Формат бейджа: `<span class="px-3 py-1 rounded-full text-[10px] font-bold uppercase tracking-wide">`.

**Sidebar (левое меню):**
- Ширина: `w-64`, фон `bg-white/80 backdrop-blur-md`, бордер справа.
- Лого: чёрный квадрат `w-8 h-8 bg-black rounded-lg` с SVG-иконкой + текст "VOZVRAT" (`font-bold text-xl tracking-tight`).
- Пункты меню: `flex items-center gap-3 px-3 py-2 rounded-xl`, активный — `bg-black text-white`, неактивный — `text-gray-600 hover:bg-gray-100`.
- Внизу: блок пользователя с аватаром, именем и ролью.
- Пункты для staff/admin: Статистика, Возвраты, Поставщики, Пользователи (только admin).
- Пункты для client: Главная (список своих возвратов).
- Мобильная версия: sidebar скрыт, показывается мобильный header с кнопкой-гамбургером, sidebar раскрывается на весь экран.

### 1. Экран авторизации (`/login`)
- Центрированная форма на градиентном фоне `bg-gradient-to-br from-gray-200 to-gray-300`.
- Карточка формы: `bg-white/70 backdrop-blur-xl rounded-[32px] shadow-xl border border-white/50`, `max-w-md`.
- Заголовок: "С возвращением" (bold), подзаголовок "Войдите в систему Vozvrat".
- Поля: email и пароль с лейблами `text-xs font-semibold text-gray-500 uppercase`.
- Инпуты: `rounded-2xl border-none bg-white shadow-sm focus:ring-2 focus:ring-black`.
- Кнопка: `bg-gray-900 text-white rounded-2xl font-semibold hover:bg-black shadow-lg`.
- **Без sidebar** — полноэкранная форма.
- При ошибке авторизации — показать сообщение об ошибке через HTMX (hx-target на div ошибки).

### 2. Статистика (`/statistics`) — только staff/admin
- Заголовок: "Статистика", подзаголовок "Общие показатели системы возвратов".
- 4 карточки-счётчика в ряд (`grid grid-cols-2 md:grid-cols-4`):
  - "В работе" — количество возвратов со статусом in_progress
  - "Новые (Mail)" — количество с source=email и status=received
  - "Ожидают УКД" — количество со статусами sent_to_supplier или completed без привязанного pdf_ukd файла
  - "Архив" — количество со статусом archive
- Каждая карточка: `bg-white p-4 rounded-2xl shadow-sm border border-gray-100`, число крупно `text-2xl font-bold`.
- Данные подгружаются с бэкенда (реальные SQL-запросы с COUNT).

### 3. Список возвратов для сотрудника (`/refunds`)
- Заголовок: "Все возвраты", подзаголовок, кнопка "Создать возврат" справа.
- **Блок фильтров**: `bg-white p-4 rounded-2xl`, grid 4 колонки:
  - Select "Статус" (все статусы + каждый отдельно)
  - Select "Поставщик" (подгружать из БД)
  - Input "Клиент" (текстовый поиск)
  - Input "Дата" (type=date)
- Фильтрация через HTMX: при изменении любого фильтра — `hx-get="/api/refunds/table" hx-trigger="change" hx-target="#refunds-table-body"` — обновляется только tbody таблицы.
- **Таблица** в `bg-white rounded-3xl`:
  - Заголовки: `bg-gray-50/50 text-[11px] uppercase font-bold text-gray-400 tracking-wider`.
  - Колонки: "ID Заказа / Дата", "Клиент / Поставщик", "Сумма", "Статус", действия.
  - Строки: `hover:bg-gray-50/50 cursor-pointer`, по клику — переход на карточку.
  - ID: `font-bold text-gray-900` (#99281), дата под ним `text-xs text-gray-500`.
  - Клиент: `font-medium`, поставщик под ним `text-xs text-gray-400 font-mono`.
  - Статус: бейдж с цветом по маппингу выше.
- Пагинация (если возвратов > 20 на странице).
- Мобильная адаптация: горизонтальный скролл таблицы (`overflow-x-auto`).

### 4. Карточка возврата для сотрудника (`/refunds/{id}`)
- Кнопка "Назад" (круглая, `bg-white rounded-full border shadow-sm`).
- Заголовок: "Возврат #99281" + бейдж текущего статуса.
- Подзаголовок: "Создан автоматически 12.05.2024" или "Создан вручную ...".

- **Лейаут**: `grid grid-cols-12`, 8 колонок основное, 4 колонки чат.

- **Левая часть (col-span-8):**
  - Две карточки в ряд (`grid grid-cols-2`):
    - "Детали товара": поставщик, артикул (mono), марка, кол-во/цена. Данные из RefundItem.
    - "Клиент и Причина": клиент, ID заказа, причина (красным если брак).
  - **Управление статусом**: блок с кнопками перехода. Кнопки зависят от текущего статуса:
    - "Отправить поставщику" (`bg-blue-600 text-white rounded-xl shadow-md`) — заготовка, на этапе 1 просто меняет статус
    - "В сток" (`bg-orange-100 text-orange-700`)
    - "Согласован" (`bg-green-100 text-green-700`)
    - "В Архив" (`bg-black text-white`)
  - Смена статуса через HTMX: `hx-post="/api/refunds/{id}/status" hx-vals='{"status": "approved"}'`, обновить бейдж + скрыть/показать кнопки.
  - **Прикрепленные файлы**: карточки файлов в `grid grid-cols-3`:
    - XLS: зелёная иконка `bg-green-100 text-green-600`
    - Фото: фиолетовая `bg-purple-100 text-purple-600`
    - PDF: красная `bg-red-100 text-red-600`
    - По клику — скачивание через `/api/files/{id}/download`.
  - Кнопка загрузки файла (drag-and-drop зона или input type=file), загрузка через HTMX multipart.

- **Правая часть (col-span-4):** Чат (заготовка для этапа 2)
  - Заголовок "Чат с клиентом" с зелёным индикатором `animate-pulse`.
  - Область сообщений (пока пустая или с placeholder текстом).
  - Поле ввода (disabled, с подсказкой "Реализуется на этапе 2").

### 5. Создание возврата (`/refunds/create`) — только staff/admin
- Форма в `bg-white rounded-[32px] shadow-sm border`, `max-w-3xl mx-auto`.
- Заголовок: "Новый возврат (Ручное создание)".
- Поля (`grid grid-cols-2 gap-6`):
  - Поставщик — select (full width, col-span-2), подгружать из БД.
  - Клиент — input text.
  - ID Заказа — input text.
  - Артикул / Марка — input text (парсить в два поля или одно с разделителем).
  - Кол-во — input number / Цена (₽) — input text (grid-cols-2 внутри).
  - Причина возврата — textarea (col-span-2), `rows="3"`.
  - Загрузка файлов — drag-and-drop зона: `border-2 border-dashed border-gray-200 rounded-[28px] p-8 text-center`.
- Лейблы: `text-[11px] font-bold text-gray-400 uppercase`.
- Инпуты: `bg-gray-50 rounded-2xl px-4 py-3 ring-1 ring-gray-200 focus:ring-2 focus:ring-black`.
- Кнопки: "Сохранить" (`bg-gray-900 text-white rounded-2xl font-bold shadow-lg`, flex-1) и "Отмена" (`bg-gray-100 text-gray-600 rounded-2xl`).
- При сабмите: создать Refund + RefundItem + загрузить файлы, статус = "received", source = "manual". Редирект на карточку.

### 6. Справочник поставщиков (`/suppliers`) — staff/admin
- Заголовок "Поставщики", кнопка "+ Новый поставщик" справа.
- Таблица в `bg-white rounded-3xl`:
  - Колонки: "Название компании", "Контактный Email", "Активных возвратов", "Действия".
  - Название: иконка-буква в цветном квадрате (`w-8 h-8 rounded-lg`) + bold текст.
  - Email: `italic text-gray-500`.
  - Активных возвратов: count по FK, `font-bold text-blue-600`.
  - Действие "Изменить": `text-blue-500 font-bold text-xs uppercase`.
- Создание/редактирование: модальное окно или inline-форма через HTMX (hx-get partial, hx-swap innerHTML).

### 7. Управление пользователями (`/users`) — только admin
- Заголовок "Команда и Клиенты".
- Таблица: "Имя / Контакт", "Роль", "Доступ", действия.
  - Аватар + имя/email.
  - Роль: цветной текст (Сотрудник — синий, Клиент — зелёный, Админ — фиолетовый).
  - Доступ: бейдж `bg-green-100 text-green-700 rounded-lg` ("Полный" для staff/admin, "Ограничен" для client).
- Создание пользователя: модальная форма (email, пароль, имя, роль). Через HTMX.

### 8. ЛК клиента — список (`/client/refunds`)
- Приветственный баннер: `bg-gradient-to-r from-gray-900 to-gray-800 rounded-[32px] p-10 text-white shadow-xl`.
  - "Привет, {client_name}" (bold), подзаголовок "Отслеживайте статусы ваших возвратов".
- Список возвратов клиента — карточки (не таблица):
  - Каждый возврат: `bg-white p-6 rounded-3xl shadow-sm border hover:scale-[1.01]`.
  - Иконка статуса в цветном круге, название заказа, артикул, бейдж статуса.
  - По клику — переход на карточку клиента.
- Показывать только возвраты WHERE client_user_id = текущий пользователь.

### 9. Карточка возврата для клиента (`/client/refunds/{id}`)
- Кнопка "Назад", заголовок "Детали #99281".
- **Лейаут**: `grid grid-cols-12`, 7 колонок инфо, 5 колонок чат.
- **Левая часть:**
  - Информация: товар (mono), сумма, статус — упрощённый вид.
  - **Блок УКД**: если есть PDF UKD файл — показать синий блок `bg-blue-600 rounded-[32px] p-6 shadow-lg shadow-blue-200`:
    - "УКД Клиента (Корректировочный документ)", "Доступен для скачивания".
    - Кнопка "Скачать pdf" (`bg-white text-blue-600 rounded-2xl font-bold shadow-md`).
  - Если УКД нет — показать серый placeholder "УКД ещё не загружен".
- **Правая часть:** "Связь с менеджером" — чат (заготовка):
  - Placeholder "Ожидайте ответа менеджера...".
  - Input (disabled на этапе 1).

## Интеграция с почтой (IMAP mail.ru)

### Сервис `mail_import.py`
- Подключение к IMAP mail.ru (`imap.mail.ru:993`, SSL).
- Credentials из `.env`: `MAIL_IMAP_HOST`, `MAIL_IMAP_PORT`, `MAIL_LOGIN`, `MAIL_PASSWORD`, `MAIL_FOLDER` (default INBOX).
- APScheduler: задача запускается каждые N минут (настраиваемо, default 5 мин).

### Логика обработки письма:
1. Подключиться к IMAP, выбрать папку, найти непрочитанные (`UNSEEN`).
2. Для каждого письма:
   - Извлечь: from, subject, date, body (text/plain).
   - Найти все вложения (attachment). Определить тип:
     - `.xls`, `.xlsx` → file_type = "xls"
     - `.jpg`, `.jpeg`, `.png` → file_type = "photo"
     - `.pdf` → file_type = "pdf_ukd" (или "other")
   - Создать запись `Refund`:
     - `display_id` — сгенерировать уникальный (например, автоинкремент "#10001", "#10002"...).
     - `status` = "received"
     - `source` = "email"
     - `email_subject` = subject
     - `email_from` = from
     - `client_name` = извлечь из поля from (имя отправителя)
   - Если есть XLS-вложение — попытаться распарсить:
     - Искать колонки: артикул, марка/бренд, количество, цена (приблизительный маппинг, т.к. шаблоны могут отличаться).
     - Создать записи RefundItem из строк XLS.
     - Если парсинг не удался — просто сохранить файл как вложение.
   - Сохранить все вложения на диск в `uploads/refund_{id}/` с уникальными именами (uuid prefix).
   - Создать записи FileAttachment.
   - Пометить письмо как прочитанное (flag \Seen).
3. Логирование: каждое обработанное письмо, ошибки парсинга.
4. Обработка ошибок: если не удалось обработать конкретное письмо — логировать, не падать, продолжить со следующим.

## API-эндпоинты

### Auth
```
POST   /api/auth/login          # email, password → set cookie
POST   /api/auth/logout         # delete cookie
GET    /api/auth/me             # текущий пользователь (JSON)
```

### Refunds
```
GET    /api/refunds             # список (с фильтрами: status, supplier_id, client_name, date_from, date_to)
GET    /api/refunds/table       # HTML partial для HTMX (tbody таблицы)
POST   /api/refunds             # создание возврата (form data + files)
GET    /api/refunds/{id}        # данные возврата (JSON)
POST   /api/refunds/{id}/status # смена статуса ({"status": "approved"})
```

### Suppliers
```
GET    /api/suppliers           # список
POST   /api/suppliers           # создание
PUT    /api/suppliers/{id}      # редактирование
```

### Users (admin only)
```
GET    /api/users               # список
POST   /api/users               # создание
PUT    /api/users/{id}          # редактирование
DELETE /api/users/{id}          # деактивация (is_active=False)
```

### Files
```
POST   /api/files/upload/{refund_id}  # загрузка файла к возврату
GET    /api/files/{id}/download       # скачивание файла
```

### Pages (Jinja2 HTML)
```
GET    /login
GET    /statistics
GET    /refunds
GET    /refunds/create
GET    /refunds/{id}
GET    /suppliers
GET    /users
GET    /client/refunds          # ЛК клиента
GET    /client/refunds/{id}     # карточка клиента
```

## HTMX-паттерны

- Фильтрация списка возвратов: `hx-get="/api/refunds/table" hx-trigger="change" hx-include="[name='status'],[name='supplier_id'],[name='client_name'],[name='date']" hx-target="#refunds-tbody"`.
- Смена статуса: `hx-post="/api/refunds/{id}/status" hx-vals='{"status":"approved"}' hx-target="#status-section" hx-swap="outerHTML"`.
- Загрузка файлов: `hx-post="/api/files/upload/{refund_id}" hx-encoding="multipart/form-data" hx-target="#files-list"`.
- Модальные окна (создание поставщика/пользователя): `hx-get="/api/suppliers/form" hx-target="#modal-content" hx-swap="innerHTML"`, показать модал через JS или Alpine.js.
- Уведомления об успехе/ошибке: `hx-swap-oob="true"` для toast-сообщений.

## Инициализация и seed данные

При первом запуске (или через CLI-команду `python -m app.seed`):
- Создать пользователя admin: `admin@vozvrat.ru` / `admin123` / роль admin.
- Создать пользователя staff: `staff@vozvrat.ru` / `staff123` / роль staff.
- Создать пользователя client: `client@vozvrat.ru` / `client123` / роль client, full_name "ИП ТехноМир".
- Создать 2 поставщика: "Bosch Russia" (bosch-support@mail.ru), "ООО «Запчасть-Опт»" (orders@zapchast-opt.ru).
- Создать 3-5 демо-возвратов с разными статусами и привязками.

## Конфигурация (.env)

```env
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/vozvrat
SECRET_KEY=your-secret-key-here
JWT_ALGORITHM=HS256
JWT_EXPIRE_HOURS=24

MAIL_IMAP_HOST=imap.mail.ru
MAIL_IMAP_PORT=993
MAIL_LOGIN=your@mail.ru
MAIL_PASSWORD=your-app-password
MAIL_FOLDER=INBOX
MAIL_CHECK_INTERVAL_MINUTES=5

UPLOAD_DIR=./uploads
MAX_UPLOAD_SIZE_MB=20
```

## Требования к коду

1. **Async everywhere**: все handler'ы и DB-запросы async. Используй `async with get_db() as session`.
2. **Pydantic v2**: все схемы с `model_config = ConfigDict(from_attributes=True)`.
3. **Типизация**: type hints на всех функциях.
4. **Ошибки**: HTTPException с человекочитаемыми сообщениями на русском в UI.
5. **Alembic**: начальная миграция со всеми таблицами. `alembic.ini` настроен на async.
6. **Логирование**: `logging` модуль, INFO уровень, для mail_import — DEBUG.
7. **Безопасность**: пароли хешируются через `passlib[bcrypt]`. JWT подписывается SECRET_KEY.
8. **CORS**: не нужен (SPA нет, всё server-side rendered).
9. **Статика**: TailwindCSS через CDN (`<script src="https://cdn.tailwindcss.com">`), шрифты через Google Fonts CDN.

## Порядок реализации

1. Настрой проект: `requirements.txt`, `.env.example`, `config.py`, `database.py`.
2. Создай все модели SQLAlchemy.
3. Настрой Alembic, создай начальную миграцию.
4. Реализуй сервис авторизации (`services/auth.py`) и роутер (`routers/auth.py`).
5. Создай `base.html` (layout с sidebar, подключение HTMX/Tailwind/шрифтов) и `login.html`.
6. Реализуй CRUD поставщиков.
7. Реализуй CRUD возвратов: список с фильтрацией, карточка, создание.
8. Реализуй управление пользователями (admin).
9. Реализуй ЛК клиента (свои возвраты + карточка).
10. Реализуй загрузку/скачивание файлов.
11. Реализуй IMAP-импорт (`mail_import.py` + APScheduler).
12. Реализуй страницу статистики.
13. Создай seed-скрипт.
14. Проверь все экраны, HTMX-взаимодействия, ролевой доступ.

## Чего НЕ делать на этапе 1
- НЕ реализовывать отправку XLS поставщикам по email (только смена статуса).
- НЕ реализовывать чат/переписку (только UI-заготовка, модель Message создать).
- НЕ реализовывать FTP-интеграцию.
- НЕ реализовывать выгрузку для 1С.
- НЕ реализовывать приём PDF УКД с FTP (но показывать УКД если файл загружен вручную).
- НЕ реализовывать автоматическую архивацию.
