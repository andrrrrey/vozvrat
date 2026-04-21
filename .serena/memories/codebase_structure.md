# Codebase Structure

```
vozvrat/
├── app/
│   ├── main.py              # FastAPI app, lifespan, APScheduler setup
│   ├── config.py            # Pydantic Settings (reads from .env)
│   ├── database.py          # Async engine, AsyncSessionLocal, get_db
│   ├── seed.py              # Demo data loader
│   ├── models/              # SQLAlchemy ORM models
│   │   ├── user.py          # User (roles: admin/staff/client)
│   │   ├── refund.py        # Refund (statuses, source)
│   │   ├── refund_item.py   # Return line items (article, qty, price)
│   │   ├── supplier.py      # Suppliers
│   │   ├── file_attachment.py # File attachments (XLS, PDF, photos)
│   │   ├── message.py       # Chat messages on refunds
│   │   ├── message_read.py  # Message read tracking
│   │   └── app_settings.py  # App-level settings stored in DB
│   ├── schemas/             # Pydantic schemas (request/response)
│   │   ├── auth.py, user.py, supplier.py, refund.py
│   ├── routers/             # FastAPI routers
│   │   ├── auth.py          # /api/auth/*
│   │   ├── pages.py         # HTML pages (Jinja2)
│   │   ├── refunds.py       # /api/refunds/*
│   │   ├── suppliers.py     # /api/suppliers/*
│   │   ├── users.py         # /api/users/*
│   │   ├── files.py         # /api/files/*
│   │   ├── messages.py      # /api/messages/*
│   │   ├── notifications.py # /api/notifications/*
│   │   ├── emails.py        # /api/emails/*
│   │   └── settings.py      # /api/settings/*
│   ├── services/            # Business logic
│   │   ├── auth.py          # JWT, password hashing
│   │   ├── mail_import.py   # IMAP polling, Excel parsing from emails
│   │   ├── mail_reader.py   # Low-level IMAP reading
│   │   ├── file_service.py  # File save/retrieve
│   │   ├── email_service.py # Outbound email sending
│   │   ├── ftp_service.py   # FTP operations
│   │   └── settings_service.py # App settings CRUD
│   ├── templates/           # Jinja2 HTML templates
│   └── static/css/          # Custom CSS
├── alembic/                 # DB migrations
├── uploads/                 # Uploaded files (auto-created)
├── requirements.txt
├── .env.example
└── alembic.ini
```
