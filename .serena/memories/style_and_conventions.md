# Code Style and Conventions

## General
- Python 3.11+
- Async/await throughout (async SQLAlchemy, async routes, async services)
- No type hints on all functions (partial usage), Pydantic v2 for schemas
- Minimal docstrings — mostly none, occasional one-liners on key functions
- Russian language used in comments, templates, and user-facing text
- f-strings for string formatting

## Naming
- snake_case for functions, variables, file names
- PascalCase for classes (models, schemas, Pydantic models)
- UPPER_CASE for config constants (Pydantic Settings fields)
- Router files named after resource (refunds.py, suppliers.py, etc.)

## FastAPI patterns
- Routers use `APIRouter` with prefix (e.g. `/api/refunds`)
- HTML partials returned for HTMX requests (check `HX-Request` header)
- JWT stored in httponly cookies, not Authorization header
- DB session injected via `Depends(get_db)`
- `AsyncSessionLocal` used in scheduler jobs (not via Depends)

## SQLAlchemy
- Async session (`AsyncSession`) everywhere
- Models in `app/models/`, one file per model
- Relationships defined with `relationship()` + `back_populates`

## Pydantic
- Schemas in `app/schemas/`, one file per domain
- v2 style (`model_config`, `field_validator`)

## Templates
- Jinja2 in `app/templates/`
- HTMX for partial updates (table rows, modals, chat)
- TailwindCSS via CDN
- Partial templates prefixed with `_` (e.g. `_table_rows.html`, `_comments.html`)
