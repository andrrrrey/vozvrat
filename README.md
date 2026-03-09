# Vozvrat — Система управления возвратами товаров

## Стек

- **Backend**: FastAPI + SQLAlchemy 2.0 (async) + Alembic + Pydantic v2
- **Frontend**: Jinja2 + HTMX + TailwindCSS (CDN)
- **БД**: PostgreSQL (asyncpg)
- **Авторизация**: JWT в httponly cookies
- **Почта**: IMAP mail.ru (APScheduler, каждые 5 минут)
- **Файлы**: локальное хранение в `uploads/`

## Установка и запуск

### 1. Установить зависимости
```bash
pip install -r requirements.txt
```

### 2. Настроить окружение
```bash
cp .env.example .env
# Отредактировать .env: DATABASE_URL, SECRET_KEY, MAIL_*
```

### 3. Применить миграции
```bash
alembic upgrade head
```

### 4. Загрузить демо-данные
```bash
python -m app.seed
```

### 5. Запустить сервер
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## Демо-аккаунты (после seed)

| Email | Пароль | Роль |
|-------|--------|------|
| admin@vozvrat.ru | admin123 | Администратор |
| staff@vozvrat.ru | staff123 | Сотрудник |
| client@vozvrat.ru | client123 | Клиент |

## Структура URL

| URL | Доступ | Описание |
|-----|--------|----------|
| `/login` | Все | Страница входа |
| `/statistics` | staff, admin | Статистика |
| `/refunds` | staff, admin | Список возвратов |
| `/refunds/create` | staff, admin | Создание возврата |
| `/refunds/{id}` | staff, admin | Карточка возврата |
| `/suppliers` | staff, admin | Поставщики |
| `/users` | admin | Пользователи |
| `/client/refunds` | client | ЛК клиента |
| `/client/refunds/{id}` | client | Карточка (клиент) |

## API эндпоинты

- `POST /api/auth/login` — вход
- `POST /api/auth/logout` — выход
- `GET /api/refunds` — список возвратов (JSON)
- `GET /api/refunds/table` — HTML partial (HTMX)
- `POST /api/refunds` — создать возврат
- `POST /api/refunds/{id}/status` — сменить статус
- `GET /api/suppliers` — поставщики
- `POST /api/suppliers` — создать поставщика
- `GET /api/users` — пользователи (admin)
- `POST /api/users` — создать пользователя (admin)
- `POST /api/files/upload/{refund_id}` — загрузить файл
- `GET /api/files/{id}/download` — скачать файл

## Деплой (systemd + nginx)

Пример `/etc/systemd/system/vozvrat.service`:
```ini
[Unit]
Description=Vozvrat App
After=network.target

[Service]
User=www-data
WorkingDirectory=/var/www/vozvrat
EnvironmentFile=/var/www/vozvrat/.env
ExecStart=/usr/local/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 2
Restart=always

[Install]
WantedBy=multi-user.target
```
