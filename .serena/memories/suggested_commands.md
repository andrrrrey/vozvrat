# Suggested Commands

## Run development server
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## Apply DB migrations
```bash
alembic upgrade head
```

## Create new migration
```bash
alembic revision --autogenerate -m "description"
```

## Seed demo data
```bash
python -m app.seed
```

## Install dependencies
```bash
pip install -r requirements.txt
```

## View production logs
```bash
journalctl -u vozvrat -f
```

## Restart production service
```bash
systemctl restart vozvrat
```

## Update production
```bash
git pull origin main
pip install -r requirements.txt
alembic upgrade head
systemctl restart vozvrat
```

## Check DB connection
```bash
python -c "import asyncio; from app.database import engine; print('DB OK')"
```

## No linting/formatting tool configured (no flake8/black/ruff config found in project)
