import fcntl
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.config import settings
from app.database import engine, AsyncSessionLocal
from app.routers import auth, refunds, suppliers, users, files, pages, messages
from app.routers import settings as settings_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()
_scheduler_lock_fh = None


def _try_acquire_scheduler_lock() -> bool:
    """Try to get an exclusive lock so only one worker runs the scheduler."""
    global _scheduler_lock_fh
    try:
        _scheduler_lock_fh = open("/tmp/vozvrat_scheduler.lock", "w")
        fcntl.flock(_scheduler_lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        _scheduler_lock_fh.write(str(os.getpid()))
        _scheduler_lock_fh.flush()
        return True
    except (IOError, OSError):
        if _scheduler_lock_fh:
            _scheduler_lock_fh.close()
            _scheduler_lock_fh = None
        return False


async def mail_check_job():
    """Scheduled job to check and import emails."""
    from app.services.mail_import import process_emails
    async with AsyncSessionLocal() as db:
        try:
            count = await process_emails(db)
            if count > 0:
                logger.info(f"Mail import: processed {count} emails")
        except Exception as e:
            logger.error(f"Mail import job error: {e}", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting Vozvrat application...")

    # Ensure uploads directory exists
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)

    # Start scheduler only in one worker (prevents duplicate jobs with multiple uvicorn workers)
    if _try_acquire_scheduler_lock():
        scheduler.add_job(
            mail_check_job,
            trigger=IntervalTrigger(minutes=settings.MAIL_CHECK_INTERVAL_MINUTES),
            id="mail_import",
            replace_existing=True,
            max_instances=1,
        )
        scheduler.start()
        logger.info(f"Mail check scheduler started (pid={os.getpid()}, every {settings.MAIL_CHECK_INTERVAL_MINUTES} min)")
    else:
        logger.info(f"Scheduler already running in another worker (pid={os.getpid()}), skipping")

    yield

    # Shutdown
    scheduler.shutdown(wait=False)
    await engine.dispose()
    logger.info("Vozvrat application stopped")


app = FastAPI(
    title="Vozvrat — Система управления возвратами",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/docs" if settings.DEBUG else None,
    redoc_url=None,
)

# Static files
static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Routers
app.include_router(auth.router)
app.include_router(refunds.router)
app.include_router(suppliers.router)
app.include_router(users.router)
app.include_router(files.router)
app.include_router(settings_router.router)
app.include_router(messages.router)
app.include_router(pages.router)
