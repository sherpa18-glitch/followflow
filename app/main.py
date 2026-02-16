"""FastAPI application entry point with scheduler and Telegram bot."""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import get_settings
from app.database import init_db
from app.telegram.bot import FollowFlowBot
from app.telegram.handlers import build_telegram_app, start_polling, stop_polling
from app.scheduler.jobs import daily_workflow, get_current_workflow_state
from app.utils.logger import get_logger

logger = get_logger("main")

# Global references for cleanup
_scheduler: AsyncIOScheduler = None
_telegram_app = None
_telegram_bot: FollowFlowBot = None


async def _run_daily_workflow():
    """Wrapper to run the daily workflow with the global bot instance."""
    global _telegram_bot
    if _telegram_bot:
        await daily_workflow(_telegram_bot)
    else:
        logger.error("Telegram bot not initialized — skipping daily workflow")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup and shutdown lifecycle."""
    global _scheduler, _telegram_app, _telegram_bot

    # --- Startup ---
    logger.info("FollowFlow starting up...")

    # Initialize database
    init_db()
    logger.info("Database initialized")

    settings = get_settings()

    # Initialize Telegram bot
    _telegram_bot = FollowFlowBot(
        token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
    )

    # Build and start Telegram polling (for callback handling)
    _telegram_app = build_telegram_app(_telegram_bot)
    try:
        await start_polling(_telegram_app)
        logger.info("Telegram bot polling started")
    except Exception as e:
        logger.warning(
            f"Telegram bot polling failed to start: {e}. "
            "Callbacks won't work but notifications will still be sent."
        )
        _telegram_app = None

    # Initialize APScheduler
    _scheduler = AsyncIOScheduler()

    # Parse schedule time
    hour, minute = settings.daily_schedule_time.split(":")
    trigger = CronTrigger(hour=int(hour), minute=int(minute))

    _scheduler.add_job(
        _run_daily_workflow,
        trigger=trigger,
        id="daily_workflow",
        name="FollowFlow Daily Workflow",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info(
        f"Scheduler started — daily workflow at {settings.daily_schedule_time}",
        extra={"action": "scheduler_start"},
    )

    yield

    # --- Shutdown ---
    logger.info("FollowFlow shutting down...")

    if _scheduler:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")

    if _telegram_app:
        try:
            await stop_polling(_telegram_app)
            logger.info("Telegram bot polling stopped")
        except Exception as e:
            logger.warning(f"Error stopping Telegram polling: {e}")


app = FastAPI(
    title="FollowFlow",
    description="Instagram growth automation agent with human-in-the-loop approval",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
def health_check():
    """Basic health check endpoint."""
    return {"status": "ok", "service": "followflow", "version": "0.1.0"}


@app.get("/")
def root():
    """Root endpoint with service info."""
    return {
        "service": "FollowFlow",
        "version": "0.1.0",
        "docs": "/docs",
    }


@app.get("/status")
def workflow_status():
    """Return the current daily workflow state."""
    return get_current_workflow_state()


@app.post("/trigger")
async def trigger_workflow():
    """Manually trigger the daily workflow (for testing).

    This runs the workflow immediately instead of waiting for
    the scheduled cron time.
    """
    global _telegram_bot
    if not _telegram_bot:
        return {"error": "Telegram bot not initialized"}

    # Run in background so the endpoint returns immediately
    asyncio.create_task(_run_daily_workflow())

    return {
        "status": "triggered",
        "message": "Daily workflow started. Check /status for progress.",
    }
