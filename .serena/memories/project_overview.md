# Vozvrat — Project Overview

**Purpose**: Web application for managing product returns from clients (LLC/SP) to suppliers. Supports manual creation of returns and automatic creation from incoming emails with Excel attachments.

**Language**: Python 3.11+
**Framework**: FastAPI 0.115 + Uvicorn
**Database**: PostgreSQL 14+ with SQLAlchemy 2.0 (async) + asyncpg
**Migrations**: Alembic 1.13
**Validation**: Pydantic v2
**Frontend**: Jinja2 templates + HTMX + TailwindCSS (CDN)
**Auth**: JWT in httponly cookies (python-jose + bcrypt)
**Background tasks**: APScheduler (IMAP polling every 5 min)
**File storage**: Local `uploads/` directory, Excel parsing via openpyxl

**Roles**: admin (full access), staff (refunds/suppliers/statistics), client (own refunds only)

**Return status flow**: received → in_progress → approved → sent_to_supplier → stock → completed → archive

**Production**: Nginx reverse proxy → Uvicorn port 8000, deployed at vz.amx24.ru
