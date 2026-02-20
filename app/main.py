"""FastAPI application entry point with Telegram bot (no scheduler).

All workflows are triggered manually via API endpoints:
  POST /trigger-follow    — Start follow workflow
  POST /trigger-unfollow  — Start unfollow workflow
  POST /cancel            — Cancel running workflow
  GET  /status            — Check workflow status
  GET  /export/{filename} — Download CSV export
"""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.config import get_settings
from app.database import init_db
from app.telegram.bot import FollowFlowBot
from app.telegram.handlers import build_telegram_app, start_polling, stop_polling
from app.scheduler.jobs import (
    follow_only_workflow,
    unfollow_only_workflow,
    get_current_workflow_state,
    cancel_current_workflow,
    EXPORT_DIR,
)
from app.utils.logger import get_logger

logger = get_logger("main")

# Global references for cleanup
_telegram_app = None
_telegram_bot: FollowFlowBot = None


async def _run_follow_only_workflow():
    """Wrapper to run the follow-only workflow with the global bot instance."""
    global _telegram_bot
    if _telegram_bot:
        await follow_only_workflow(_telegram_bot)
    else:
        logger.error("Telegram bot not initialized — skipping follow workflow")


async def _run_unfollow_only_workflow():
    """Wrapper to run the unfollow-only workflow with the global bot instance."""
    global _telegram_bot
    if _telegram_bot:
        await unfollow_only_workflow(_telegram_bot)
    else:
        logger.error("Telegram bot not initialized — skipping unfollow workflow")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup and shutdown lifecycle."""
    global _telegram_app, _telegram_bot

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

    # Ensure exports directory exists
    EXPORT_DIR.mkdir(exist_ok=True)

    logger.info("FollowFlow ready — no scheduled jobs, use /trigger-follow or /trigger-unfollow")

    yield

    # --- Shutdown ---
    logger.info("FollowFlow shutting down...")

    if _telegram_app:
        try:
            await stop_polling(_telegram_app)
            logger.info("Telegram bot polling stopped")
        except Exception as e:
            logger.warning(f"Error stopping Telegram polling: {e}")


app = FastAPI(
    title="FollowFlow",
    description="Instagram growth automation agent with human-in-the-loop approval",
    version="0.2.0",
    lifespan=lifespan,
)


@app.get("/health")
def health_check():
    """Basic health check endpoint."""
    return {"status": "ok", "service": "followflow", "version": "0.2.0"}


@app.get("/")
def root():
    """Root endpoint with service info."""
    return {
        "service": "FollowFlow",
        "version": "0.2.0",
        "docs": "/docs",
        "endpoints": {
            "trigger_follow": "POST /trigger-follow",
            "trigger_unfollow": "POST /trigger-unfollow",
            "cancel": "POST /cancel",
            "status": "GET /status",
            "export": "GET /export/{filename}",
        },
    }


@app.get("/status")
def workflow_status():
    """Return the current workflow state with progress info."""
    return get_current_workflow_state()


@app.post("/trigger-follow")
async def trigger_follow_only():
    """Manually trigger the follow workflow.

    Runs: discovery -> Telegram approval -> follow execution.
    Includes 5-minute progress updates and CSV export.
    """
    global _telegram_bot
    if not _telegram_bot:
        return {"error": "Telegram bot not initialized"}

    # Check if a workflow is already running
    state = get_current_workflow_state()
    if state["state"] not in ("IDLE", "COMPLETE", "CANCELLED", "ERROR"):
        return {
            "error": "A workflow is already running",
            "current_state": state["state"],
            "hint": "Use POST /cancel to stop it first.",
        }

    asyncio.create_task(_run_follow_only_workflow())

    return {
        "status": "triggered",
        "message": "Follow workflow started. Check /status for progress.",
    }


@app.post("/trigger-unfollow")
async def trigger_unfollow_only():
    """Manually trigger the unfollow workflow.

    Runs: fetch following list -> Telegram approval -> unfollow execution.
    Includes 5-minute progress updates and CSV export.
    """
    global _telegram_bot
    if not _telegram_bot:
        return {"error": "Telegram bot not initialized"}

    # Check if a workflow is already running
    state = get_current_workflow_state()
    if state["state"] not in ("IDLE", "COMPLETE", "CANCELLED", "ERROR"):
        return {
            "error": "A workflow is already running",
            "current_state": state["state"],
            "hint": "Use POST /cancel to stop it first.",
        }

    asyncio.create_task(_run_unfollow_only_workflow())

    return {
        "status": "triggered",
        "message": "Unfollow workflow started. Check /status for progress.",
    }


@app.post("/cancel")
async def cancel_workflow():
    """Cancel the currently running workflow.

    The workflow will stop after the current action completes.
    Results processed so far are saved and exported to CSV.
    """
    result = cancel_current_workflow()

    # Also notify via Telegram
    if result["status"] == "cancelling" and _telegram_bot:
        try:
            await _telegram_bot.bot.send_message(
                chat_id=_telegram_bot.chat_id,
                text="⛔ *Workflow cancellation requested*\n\nStopping after current action\\.\\.\\.",
                parse_mode="MarkdownV2",
            )
        except Exception:
            pass

    return result


@app.get("/export/{filename}")
async def download_export(filename: str):
    """Download a CSV export file.

    Args:
        filename: The CSV filename (from /status response or Telegram notification).
    """
    filepath = EXPORT_DIR / filename
    if not filepath.exists():
        return {"error": f"File not found: {filename}"}
    return FileResponse(
        path=str(filepath),
        media_type="text/csv",
        filename=filename,
    )


@app.get("/exports")
async def list_exports():
    """List all available CSV export files."""
    files = sorted(EXPORT_DIR.glob("*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    return {
        "exports": [
            {
                "filename": f.name,
                "size_bytes": f.stat().st_size,
                "download_url": f"/export/{f.name}",
            }
            for f in files[:20]  # Last 20 exports
        ]
    }
