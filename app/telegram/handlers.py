"""Telegram callback and command handlers.

Handles:
- Inline keyboard callbacks (Approve/Deny buttons)
- /cancel command to cancel running workflow from Telegram
- /status command to check workflow status from Telegram
"""

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from app.telegram.bot import FollowFlowBot
from app.utils.logger import get_logger

logger = get_logger("telegram_handlers")

# Reference to cancel function (set during app build)
_cancel_fn = None


def build_telegram_app(bot: FollowFlowBot) -> Application:
    """Build a python-telegram-bot Application with callback and command handlers.

    Args:
        bot: The FollowFlowBot instance managing approval state.

    Returns:
        A configured telegram Application ready to be started.
    """
    app = Application.builder().token(bot.token).build()

    # --- Callback handler for approve/deny buttons ---
    async def handle_callback(
        update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query = update.callback_query
        await query.answer()

        data = query.data
        if not data or ":" not in data:
            logger.warning(f"Invalid callback data: {data}")
            return

        action, approval_id = data.split(":", 1)

        if action == "approve":
            bot.set_approval_response(approval_id, "APPROVED")
            await query.edit_message_reply_markup(reply_markup=None)
            await query.message.reply_text(
                f"✅ *Approved\\!* Proceeding with the action\\.\\.\\.",
                parse_mode="MarkdownV2",
            )
            logger.info(f"User approved: {approval_id}")

        elif action == "deny":
            bot.set_approval_response(approval_id, "DENIED")
            await query.edit_message_reply_markup(reply_markup=None)
            await query.message.reply_text(
                f"❌ *Denied\\.* Action skipped\\.",
                parse_mode="MarkdownV2",
            )
            logger.info(f"User denied: {approval_id}")

        else:
            logger.warning(f"Unknown callback action: {action}")

    app.add_handler(CallbackQueryHandler(handle_callback))

    # --- /cancel command handler ---
    async def handle_cancel(
        update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        from app.scheduler.jobs import cancel_current_workflow
        result = cancel_current_workflow()

        if result["status"] == "cancelling":
            await update.message.reply_text(
                "⛔ *Cancellation requested*\n\nWorkflow will stop after the current action completes\\.",
                parse_mode="MarkdownV2",
            )
        else:
            await update.message.reply_text(
                f"ℹ️ {_escape_md(result['message'])}",
                parse_mode="MarkdownV2",
            )

    app.add_handler(CommandHandler("cancel", handle_cancel))

    # --- /status command handler ---
    async def handle_status(
        update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        from app.scheduler.jobs import get_current_workflow_state
        state = get_current_workflow_state()

        workflow_type = state.get("workflow_type") or "none"
        current_state = state.get("state", "IDLE")
        progress = state.get("progress", {})

        text = (
            f"📊 *FollowFlow Status*\n\n"
            f"State: `{current_state}`\n"
            f"Type: {_escape_md(workflow_type)}\n"
        )

        if progress.get("total", 0) > 0:
            text += (
                f"\nProgress: {progress['processed']}/{progress['total']}\n"
                f"Success: {progress['success']}\n"
                f"Failed: {progress['failed']}\n"
            )

        if state.get("csv_path"):
            text += f"\nCSV: `{_escape_md(state['csv_path'])}`"

        if state.get("error_message"):
            text += f"\n\n⚠️ Error: {_escape_md(state['error_message'])}"

        await update.message.reply_text(text, parse_mode="MarkdownV2")

    app.add_handler(CommandHandler("status", handle_status))

    logger.info("Telegram callback and command handlers registered")
    return app


def _escape_md(text: str) -> str:
    """Escape special characters for Telegram MarkdownV2."""
    special_chars = r"_*[]()~`<>#+-=|{}.!"
    result = []
    for char in text:
        if char in special_chars:
            result.append(f"\\{char}")
        else:
            result.append(char)
    return "".join(result)


async def start_polling(app: Application) -> None:
    """Start the Telegram bot polling for updates."""
    logger.info("Starting Telegram bot polling...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    logger.info("Telegram bot polling started")


async def stop_polling(app: Application) -> None:
    """Stop the Telegram bot polling gracefully."""
    logger.info("Stopping Telegram bot polling...")
    await app.updater.stop()
    await app.stop()
    await app.shutdown()
    logger.info("Telegram bot polling stopped")
