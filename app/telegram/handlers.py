"""Telegram callback handlers for approval/deny button presses.

Integrates with the python-telegram-bot Application to handle
inline keyboard callbacks from approval request messages.
"""

import json
from datetime import datetime
from typing import Optional, Callable, Awaitable

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ContextTypes,
)

from app.telegram.bot import FollowFlowBot
from app.utils.logger import get_logger

logger = get_logger("telegram_handlers")


def build_telegram_app(bot: FollowFlowBot) -> Application:
    """Build a python-telegram-bot Application with callback handlers.

    The Application handles incoming callback queries from
    Approve/Deny inline keyboard buttons.

    Args:
        bot: The FollowFlowBot instance managing approval state.

    Returns:
        A configured telegram Application ready to be started.
    """
    app = Application.builder().token(bot.token).build()

    # Register the callback handler for approve/deny buttons
    async def handle_callback(
        update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query = update.callback_query
        await query.answer()  # Acknowledge the button press

        data = query.data  # e.g., "approve:abc-123" or "deny:abc-123"
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
            logger.info(
                f"User approved: {approval_id}",
                extra={"action": "callback", "detail": "APPROVED"},
            )

        elif action == "deny":
            bot.set_approval_response(approval_id, "DENIED")
            await query.edit_message_reply_markup(reply_markup=None)
            await query.message.reply_text(
                f"❌ *Denied\\.* Action skipped for today\\.",
                parse_mode="MarkdownV2",
            )
            logger.info(
                f"User denied: {approval_id}",
                extra={"action": "callback", "detail": "DENIED"},
            )

        else:
            logger.warning(f"Unknown callback action: {action}")

    app.add_handler(CallbackQueryHandler(handle_callback))

    logger.info("Telegram callback handlers registered")
    return app


async def start_polling(app: Application) -> None:
    """Start the Telegram bot polling for updates.

    This runs in the background and listens for callback
    queries (button presses) from the user.

    Args:
        app: The configured telegram Application.
    """
    logger.info("Starting Telegram bot polling...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    logger.info("Telegram bot polling started")


async def stop_polling(app: Application) -> None:
    """Stop the Telegram bot polling gracefully.

    Args:
        app: The running telegram Application.
    """
    logger.info("Stopping Telegram bot polling...")
    await app.updater.stop()
    await app.stop()
    await app.shutdown()
    logger.info("Telegram bot polling stopped")
