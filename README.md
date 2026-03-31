# Vozvrat — Система управления возвратами товаров

Веб-приложение для управления процессом возврата товаров от клиентов (ООО, ИП) поставщикам. Поддерживает ручное создание возвратов и автоматическое создание из входящих писем с Excel-вложениями.

## Содержание

- [Стек технологий](#стек-технологий)
- [Архитектура](#архитектура)
- [Роли и доступ](#роли-и-доступ)
- [Статусы возвратов](#статусы-возвратов)
- [Структура проекта](#структура-проекта)
- [Локальная разработка](#локальная-разработка)
- [Деплой на VPS](#деплой-на-vps)
- [Подключение домена](#подключение-домена)
- [API эндпоинты](#api-эндпоинты)
- [Демо-аккаунты](#демо-аккаунты)

---

## Стек технологий

| Слой | Технологии |
|------|-----------|
| Backend | FastAPI 0.115 + Uvicorn |
| ORM | SQLAlchemy 2.0 (async) + asyncpg |
| Миграции | Alembic 1.13 |
| Валидация | Pydantic v2 |
| Frontend | Jinja2 + HTMX + TailwindCSS (CDN) |
| БД | PostgreSQL |
| Авторизация | JWT в httponly cookies (python-jose + bcrypt) |
| Фоновые задачи | APScheduler (IMAP polling каждые 5 мин) |
| Файлы | Локальное хранилище `uploads/`, парсинг Excel через openpyxl |

---

## Архитектура

```
┌──────────────┐    ┌────────────────────────────┐    ┌──────────────┐
│   Браузер    │───▶│  Nginx (reverse proxy)     │───▶│  Uvicorn     │
│  (HTMX)      │    │  vz.amx24.ru       │    │  port 8000   │
└──────────────┘    └────────────────────────────┘    └──────┬───────┘
                                                             │
                    ┌──────────────────┐     ┌──────────────▼───────────┐
                    │  PostgreSQL      │◀────│  FastAPI Application      │
                    │  (vozvrat DB)    │     │  ├── Routers (API + Pages)│
                    └──────────────────┘     │  ├── Services             │
                                             │  ├── Models (SQLAlchemy)  │
                    ┌──────────────────┐     │  └── APScheduler          │
                    │  IMAP mail.ru    │◀────│     (IMAP polling)        │
                    └──────────────────┘     └──────────────────────────┘
```

---

## Роли и доступ

| Роль | Описание |
|------|----------|
| `admin` | Полный доступ: возвраты, поставщики, пользователи, статистика |
| `staff` | Возвраты, поставщики, статистика (без управления пользователями) |
| `client` | Только свои возвраты через личный кабинет |

---

## Статусы возвратов

```
received → in_progress → approved → sent_to_supplier → stock → completed → archive
```

| Статус | Описание |
|--------|----------|
| `received` | Получен (из письма или вручную) |
| `in_progress` | В обработке |
| `approved` | Одобрен |
| `sent_to_supplier` | Отправлен поставщику |
| `stock` | На складе |
| `completed` | Завершён |
| `archive` | В архиве |

---

## Структура проекта

```
vozvrat/
├── app/
│   ├── main.py              # FastAPI приложение, lifespan, scheduler
│   ├── config.py            # Настройки из .env (Pydantic Settings)
│   ├── database.py          # Async engine, sessionmaker, get_db
│   ├── seed.py              # Загрузка демо-данных
│   ├── models/
│   │   ├── user.py          # Модель User (роли: admin/staff/client)
│   │   ├── refund.py        # Модель Refund (статусы, источник)
│   │   ├── refund_item.py   # Позиции возврата (артикул, кол-во, цена)
│   │   ├── supplier.py      # Поставщики
│   │   ├── file_attachment.py # Вложения (XLS, PDF, фото)
│   │   └── message.py       # Сообщения (подготовка к этапу 2)
│   ├── schemas/             # Pydantic-схемы (запросы/ответы)
│   ├── routers/
│   │   ├── auth.py          # /api/auth/*
│   │   ├── pages.py         # HTML страницы (Jinja2)
│   │   ├── refunds.py       # /api/refunds/*
│   │   ├── suppliers.py     # /api/suppliers/*
│   │   ├── users.py         # /api/users/*
│   │   └── files.py         # /api/files/*
│   ├── services/
│   │   ├── auth.py          # JWT, хэширование паролей
│   │   ├── mail_import.py   # IMAP polling, парсинг Excel из писем
│   │   └── file_service.py  # Сохранение/получение файлов
│   ├── templates/           # Jinja2 HTML шаблоны
│   └── static/css/          # Кастомные стили
├── alembic/                 # Миграции БД
├── uploads/                 # Загруженные файлы (создаётся автоматически)
├── requirements.txt
├── .env.example
└── alembic.ini
```

---

## Локальная разработка

### Требования

- Python 3.11+
- PostgreSQL 14+
- pip

### Установка

```bash
# 1. Клонировать репозиторий
git clone <repo-url> vozvrat
cd vozvrat

# 2. Создать виртуальное окружение
python -m venv venv
source venv/bin/activate  # Linux/macOS
# venv\Scripts\activate   # Windows

# 3. Установить зависимости
pip install -r requirements.txt

# 4. Создать базу данных PostgreSQL
psql -U postgres -c "CREATE USER vozvrat WITH PASSWORD 'vozvrat_pass';"
psql -U postgres -c "CREATE DATABASE vozvrat OWNER vozvrat;"

# 5. Создать .env файл
cp .env.example .env
# Отредактировать .env (см. раздел конфигурации ниже)

# 6. Применить миграции
alembic upgrade head

# 7. Загрузить демо-данные (опционально)
python -m app.seed

# 8. Запустить сервер разработки
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Приложение доступно по адресу: http://localhost:8000

### Конфигурация (.env)

```env
# База данных
DATABASE_URL=postgresql+asyncpg://vozvrat:vozvrat_pass@localhost:5432/vozvrat

# JWT (сгенерируйте случайную строку: openssl rand -hex 32)
SECRET_KEY=your-very-secret-key-change-in-production
JWT_ALGORITHM=HS256
JWT_EXPIRE_HOURS=24

# IMAP (для автоимпорта из почты)
MAIL_IMAP_HOST=imap.mail.ru
MAIL_IMAP_PORT=993
MAIL_LOGIN=your@mail.ru
MAIL_PASSWORD=your-app-password
MAIL_FOLDER=INBOX
MAIL_CHECK_INTERVAL_MINUTES=5

# Файлы
UPLOAD_DIR=./uploads
MAX_UPLOAD_SIZE_MB=20

# Режим отладки
DEBUG=false
```

---

## Деплой на VPS

### Требования к серверу

- Ubuntu 22.04 LTS (рекомендуется)
- 1+ ГБ RAM
- Открытые порты: 22 (SSH), 80 (HTTP), 443 (HTTPS)

### Шаг 1 — Обновление системы и установка зависимостей

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3.11 python3.11-venv python3-pip postgresql postgresql-contrib nginx certbot python3-certbot-nginx git
```

### Шаг 2 — Создание пользователя и директории приложения

```bash
# Создать системного пользователя для приложения
sudo useradd -m -s /bin/bash vozvrat

# Создать директорию
sudo mkdir -p /var/www/vozvrat
sudo chown vozvrat:vozvrat /var/www/vozvrat
```

### Шаг 3 — Настройка PostgreSQL

```bash
sudo -u postgres psql << 'EOF'
CREATE USER vozvrat WITH PASSWORD 'STRONG_PASSWORD_HERE';
CREATE DATABASE vozvrat OWNER vozvrat;
GRANT ALL PRIVILEGES ON DATABASE vozvrat TO vozvrat;
EOF
```

> Замените `STRONG_PASSWORD_HERE` на надёжный пароль.

### Шаг 4 — Деплой кода

```bash
# Переключиться на пользователя vozvrat
sudo su - vozvrat

# Клонировать репозиторий
git clone <repo-url> /var/www/vozvrat
cd /var/www/vozvrat

# Создать виртуальное окружение
python3.11 -m venv venv
source venv/bin/activate

# Установить зависимости
pip install -r requirements.txt

# Создать .env файл
cp .env.example .env
nano .env
```

Заполнить `.env` для продакшена:

```env
DATABASE_URL=postgresql+asyncpg://vozvrat:STRONG_PASSWORD_HERE@localhost:5432/vozvrat
SECRET_KEY=<вывод команды: openssl rand -hex 32>
JWT_ALGORITHM=HS256
JWT_EXPIRE_HOURS=24

MAIL_IMAP_HOST=imap.mail.ru
MAIL_IMAP_PORT=993
MAIL_LOGIN=your@mail.ru
MAIL_PASSWORD=your-app-password
MAIL_FOLDER=INBOX
MAIL_CHECK_INTERVAL_MINUTES=5

UPLOAD_DIR=/var/www/vozvrat/uploads
MAX_UPLOAD_SIZE_MB=20
DEBUG=false
```

```bash
# Создать папку для загрузок
mkdir -p /var/www/vozvrat/uploads

# Применить миграции БД
alembic upgrade head

# (Опционально) загрузить демо-данные
python -m app.seed

# Выйти из пользователя vozvrat
exit
```

### Шаг 5 — Настройка systemd сервиса

Создать файл сервиса:

```bash
sudo nano /etc/systemd/system/vozvrat.service
```

Содержимое:

```ini
[Unit]
Description=Vozvrat — Return Management System
After=network.target postgresql.service
Requires=postgresql.service

[Service]
Type=simple
User=vozvrat
Group=vozvrat
WorkingDirectory=/var/www/vozvrat
EnvironmentFile=/var/www/vozvrat/.env
ExecStart=/var/www/vozvrat/venv/bin/uvicorn app.main:app \
    --host 127.0.0.1 \
    --port 8000 \
    --workers 2 \
    --proxy-headers \
    --forwarded-allow-ips="*"
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Запустить сервис:

```bash
sudo systemctl daemon-reload
sudo systemctl enable vozvrat
sudo systemctl start vozvrat

# Проверить статус
sudo systemctl status vozvrat

# Просмотр логов
sudo journalctl -u vozvrat -f
```

### Шаг 6 — Настройка Nginx

Создать конфигурацию сайта:

```bash
sudo nano /etc/nginx/sites-available/vozvrat
```

Содержимое (HTTP, до получения SSL):

```nginx
server {
    listen 80;
    server_name vz.amx24.ru;

    # Максимальный размер загружаемых файлов
    client_max_body_size 25M;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
        proxy_connect_timeout 10s;
    }

    # Статические файлы напрямую через Nginx (быстрее)
    location /static/ {
        alias /var/www/vozvrat/app/static/;
        expires 7d;
        add_header Cache-Control "public";
    }
}
```

Активировать конфиг:

```bash
sudo ln -s /etc/nginx/sites-available/vozvrat /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

---

## Подключение домена

### Шаг 1 — DNS-запись

В панели управления DNS вашего домена `amx24.ru` добавьте A-запись:

| Тип | Имя | Значение | TTL |
|-----|-----|----------|-----|
| A | vz | `<IP вашего VPS>` | 3600 |

Проверить распространение DNS (может занять до 24 часов):

```bash
nslookup vz.amx24.ru
# или
dig vz.amx24.ru +short
```

### Шаг 2 — SSL-сертификат (Let's Encrypt)

После того как DNS-запись распространилась:

```bash
sudo certbot --nginx -d vz.amx24.ru
```

Certbot автоматически:
- Получит бесплатный SSL сертификат
- Обновит конфигурацию Nginx для HTTPS
- Настроит редирект с HTTP на HTTPS

Проверить автообновление сертификата:

```bash
sudo certbot renew --dry-run
```

### Шаг 3 — Итоговая конфигурация Nginx (после certbot)

После выполнения certbot конфиг будет выглядеть примерно так:

```nginx
server {
    listen 80;
    server_name vz.amx24.ru;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name vz.amx24.ru;

    ssl_certificate /etc/letsencrypt/live/vz.amx24.ru/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/vz.amx24.ru/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    client_max_body_size 25M;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
        proxy_connect_timeout 10s;
    }

    location /static/ {
        alias /var/www/vozvrat/app/static/;
        expires 7d;
        add_header Cache-Control "public";
    }
}
```

### Шаг 4 — Проверка

```bash
# Приложение запущено
sudo systemctl status vozvrat

# Nginx работает
sudo systemctl status nginx

# Сайт доступен
curl -I https://vz.amx24.ru
```

---

## Обновление приложения

```bash
sudo su - vozvrat
cd /var/www/vozvrat

# Получить изменения
git pull origin main

# Активировать окружение
source venv/bin/activate

# Обновить зависимости (если изменились)
pip install -r requirements.txt

# Применить новые миграции (если есть)
alembic upgrade head

# Выйти
exit

# Перезапустить сервис
sudo systemctl restart vozvrat
```

---

## API эндпоинты

### Авторизация

| Метод | URL | Описание |
|-------|-----|----------|
| POST | `/api/auth/login` | Вход (устанавливает JWT cookie) |
| POST | `/api/auth/logout` | Выход (удаляет cookie) |
| GET | `/api/auth/me` | Текущий пользователь |

### Возвраты

| Метод | URL | Доступ | Описание |
|-------|-----|--------|----------|
| GET | `/api/refunds` | staff, admin | Список с фильтрацией и пагинацией |
| GET | `/api/refunds/table` | staff, admin | HTML partial для HTMX |
| GET | `/api/refunds/{id}` | staff, admin | Карточка возврата |
| POST | `/api/refunds` | staff, admin | Создать возврат |
| POST | `/api/refunds/{id}/status` | staff, admin | Сменить статус |

**Параметры фильтрации GET /api/refunds:**
```
?status=in_progress&supplier_id=1&client_name=ООО&date_from=2024-01-01&page=1&page_size=20
```

### Поставщики

| Метод | URL | Доступ | Описание |
|-------|-----|--------|----------|
| GET | `/api/suppliers` | staff, admin | Список поставщиков |
| POST | `/api/suppliers` | staff, admin | Создать поставщика |
| PUT | `/api/suppliers/{id}` | staff, admin | Обновить поставщика |

### Пользователи

| Метод | URL | Доступ | Описание |
|-------|-----|--------|----------|
| GET | `/api/users` | admin | Список пользователей |
| POST | `/api/users` | admin | Создать пользователя |

### Файлы

| Метод | URL | Доступ | Описание |
|-------|-----|--------|----------|
| POST | `/api/files/upload/{refund_id}` | staff, admin | Загрузить файл |
| GET | `/api/files/{id}/download` | staff, admin | Скачать файл |

### HTML страницы

| URL | Доступ | Описание |
|-----|--------|----------|
| `/login` | Все | Страница входа |
| `/statistics` | staff, admin | Дашборд статистики |
| `/refunds` | staff, admin | Список возвратов |
| `/refunds/create` | staff, admin | Форма создания |
| `/refunds/{id}` | staff, admin | Карточка возврата |
| `/suppliers` | staff, admin | Управление поставщиками |
| `/users` | admin | Управление пользователями |
| `/client/refunds` | client | Личный кабинет клиента |
| `/client/refunds/{id}` | client | Карточка (клиент) |

---

## Демо-аккаунты

После запуска `python -m app.seed`:

| Email | Пароль | Роль |
|-------|--------|------|
| admin@vozvrat.ru | admin123 | Администратор |
| staff@vozvrat.ru | staff123 | Сотрудник |
| client@vozvrat.ru | client123 | Клиент |

> **Важно:** Удалите или смените демо-аккаунты перед публикацией в продакшен.

---

## Диагностика

```bash
# Логи приложения
sudo journalctl -u vozvrat -f --since "1 hour ago"

# Логи Nginx
sudo tail -f /var/log/nginx/error.log
sudo tail -f /var/log/nginx/access.log

# Статус PostgreSQL
sudo systemctl status postgresql
sudo -u postgres psql -c "SELECT count(*) FROM pg_stat_activity WHERE datname='vozvrat';"

# Тест соединения с БД
sudo su - vozvrat
cd /var/www/vozvrat && source venv/bin/activate
python -c "import asyncio; from app.database import engine; print('DB OK')"

# Проверить порт приложения
ss -tlnp | grep 8000
```
