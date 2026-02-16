"""Telegram bot for sending notifications and handling approval callbacks.

Provides functions to send approval requests (with inline Approve/Deny
buttons) and completion notices to the user via Telegram.
"""

import json
import asyncio
from datetime import datetime
from typing import List, Dict, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.constants import ParseMode

from app.utils.logger import get_logger

logger = get_logger("telegram_bot")


class FollowFlowBot:
    """Telegram bot wrapper for FollowFlow notifications.

    Sends structured notifications and manages approval state
    via inline keyboard callbacks.

    Args:
        token: Telegram bot API token.
        chat_id: Telegram chat ID to send messages to.
    """

    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self.bot = Bot(token=token)

        # In-memory approval state (keyed by approval_id)
        # In production, this would be backed by the database
        self._pending_approvals: Dict[str, Optional[str]] = {}

    async def send_unfollow_approval_request(
        self,
        approval_id: str,
        accounts: List[Dict[str, str]],
    ) -> int:
        """Send an unfollow permission request with Approve/Deny buttons.

        Args:
            approval_id: Unique ID for this approval request.
            accounts: List of account dicts to be unfollowed.

        Returns:
            The Telegram message ID of the sent message.
        """
        total = len(accounts)
        preview_count = min(5, total)
        preview_lines = "\n".join(
            f"  • @{a['username']}" for a in accounts[:preview_count]
        )
        remaining = total - preview_count

        text = (
            "🔔 *FollowFlow — Daily Unfollow Request*\n\n"
            f"Ready to unfollow *{total} accounts* "
            f"(oldest followed first)\\.\n\n"
            f"*Preview:*\n{_escape_md(preview_lines)}\n"
        )
        if remaining > 0:
            text += f"  \\.\\.\\.and {remaining} more\n"

        text += (
            "\n⏰ Auto\\-skips if no response in 4 hours\\."
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "✅ Approve",
                    callback_data=f"approve:{approval_id}",
                ),
                InlineKeyboardButton(
                    "❌ Deny",
                    callback_data=f"deny:{approval_id}",
                ),
            ]
        ])

        self._pending_approvals[approval_id] = None

        message = await self.bot.send_message(
            chat_id=self.chat_id,
            text=text,
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=keyboard,
        )

        logger.info(
            f"Sent unfollow approval request: {approval_id}",
            extra={"action": "send_approval", "detail": f"unfollow:{total}"},
        )

        return message.message_id

    async def send_follow_approval_request(
        self,
        approval_id: str,
        accounts: List[Dict],
    ) -> int:
        """Send a follow permission request with Approve/Deny buttons.

        Args:
            approval_id: Unique ID for this approval request.
            accounts: List of target account dicts with details.

        Returns:
            The Telegram message ID of the sent message.
        """
        total = len(accounts)
        preview_count = min(5, total)
        preview_lines = []
        for a in accounts[:preview_count]:
            followers = a.get("follower_count", "?")
            following = a.get("following_count", "?")
            region = a.get("region", "?")
            preview_lines.append(
                f"  • @{a['username']} ({followers} followers / "
                f"{following} following, {region})"
            )
        preview_text = "\n".join(preview_lines)
        remaining = total - preview_count

        text = (
            "🔔 *FollowFlow — Daily Follow Request*\n\n"
            f"Ready to follow *{total} active accounts* "
            f"in the pet/dog niche\\.\n\n"
            "*Criteria applied:*\n"
            "  • Followers < 2,000 \\| Following > 3,000\n"
            "  • Active in last 7 days\n"
            "  • Regions: NA, South Korea, Japan, Europe, Australia\n"
            "  • Not on blocklist\n\n"
            f"*Preview:*\n{_escape_md(preview_text)}\n"
        )
        if remaining > 0:
            text += f"  \\.\\.\\.and {remaining} more\n"

        text += (
            "\n⏰ Auto\\-skips if no response in 4 hours\\."
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "✅ Approve",
                    callback_data=f"approve:{approval_id}",
                ),
                InlineKeyboardButton(
                    "❌ Deny",
                    callback_data=f"deny:{approval_id}",
                ),
            ]
        ])

        self._pending_approvals[approval_id] = None

        message = await self.bot.send_message(
            chat_id=self.chat_id,
            text=text,
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=keyboard,
        )

        logger.info(
            f"Sent follow approval request: {approval_id}",
            extra={"action": "send_approval", "detail": f"follow:{total}"},
        )

        return message.message_id

    async def send_unfollow_complete(
        self,
        success_count: int,
        fail_count: int,
        old_following: Optional[int] = None,
        new_following: Optional[int] = None,
    ) -> int:
        """Send unfollow completion notification.

        Args:
            success_count: Number of successful unfollows.
            fail_count: Number of failed unfollows.
            old_following: Following count before unfollows.
            new_following: Following count after unfollows.

        Returns:
            The Telegram message ID.
        """
        text = (
            "✅ *FollowFlow — Unfollow Complete*\n\n"
            f"Successfully unfollowed: *{success_count}*\n"
            f"Failed: *{fail_count}*\n"
        )
        if old_following is not None and new_following is not None:
            text += f"\nYour following count: {old_following} → {new_following}\n"

        text += "\n_Next step: Follow action will be requested shortly\\._"

        message = await self.bot.send_message(
            chat_id=self.chat_id,
            text=text,
            parse_mode=ParseMode.MARKDOWN_V2,
        )

        logger.info(
            f"Sent unfollow complete: {success_count} success, {fail_count} failed",
            extra={"action": "send_complete", "detail": "unfollow"},
        )

        return message.message_id

    async def send_follow_complete(
        self,
        total_sent: int,
        public_count: int,
        private_count: int,
        fail_count: int = 0,
        old_following: Optional[int] = None,
        new_following: Optional[int] = None,
    ) -> int:
        """Send follow completion notification.

        Args:
            total_sent: Total follow requests sent.
            public_count: Accounts followed instantly (public).
            private_count: Accounts with pending requests (private).
            fail_count: Number of failed follow attempts.
            old_following: Following count before follows.
            new_following: Following count after follows.

        Returns:
            The Telegram message ID.
        """
        text = (
            "✅ *FollowFlow — Follow Complete*\n\n"
            f"Follow requests sent: *{total_sent}*\n"
            f"  • Public accounts followed: {public_count}\n"
            f"  • Private accounts \\(request pending\\): {private_count}\n"
        )
        if fail_count > 0:
            text += f"  • Failed: {fail_count}\n"

        if old_following is not None and new_following is not None:
            text += f"\nYour following count: {old_following} → {new_following}\n"

        text += "\n📊 _Daily cycle complete\\. Next run: tomorrow\\._"

        message = await self.bot.send_message(
            chat_id=self.chat_id,
            text=text,
            parse_mode=ParseMode.MARKDOWN_V2,
        )

        logger.info(
            f"Sent follow complete: {total_sent} sent, {public_count} public, "
            f"{private_count} private",
            extra={"action": "send_complete", "detail": "follow"},
        )

        return message.message_id

    async def send_error_notification(self, error_message: str) -> int:
        """Send an error/alert notification.

        Args:
            error_message: Description of what went wrong.

        Returns:
            The Telegram message ID.
        """
        text = (
            "🚨 *FollowFlow — Error*\n\n"
            f"{_escape_md(error_message)}\n\n"
            "_The daily workflow has been paused\\. "
            "Please check the logs\\._"
        )

        message = await self.bot.send_message(
            chat_id=self.chat_id,
            text=text,
            parse_mode=ParseMode.MARKDOWN_V2,
        )

        logger.error(
            f"Sent error notification: {error_message}",
            extra={"action": "send_error"},
        )

        return message.message_id

    def set_approval_response(self, approval_id: str, response: str) -> None:
        """Record an approval response (called from callback handler).

        Args:
            approval_id: The approval request ID.
            response: 'APPROVED' or 'DENIED'.
        """
        self._pending_approvals[approval_id] = response
        logger.info(
            f"Approval {approval_id} → {response}",
            extra={"action": "approval_response", "detail": response},
        )

    def get_approval_response(self, approval_id: str) -> Optional[str]:
        """Get the current response for an approval request.

        Returns None if still pending, 'APPROVED', or 'DENIED'.
        """
        return self._pending_approvals.get(approval_id)

    async def wait_for_approval(
        self,
        approval_id: str,
        timeout_hours: int = 4,
        poll_interval: int = 5,
    ) -> str:
        """Wait for user to approve or deny, with timeout.

        Polls the approval state at regular intervals.

        Args:
            approval_id: The approval request ID to wait for.
            timeout_hours: Hours before auto-timeout.
            poll_interval: Seconds between state checks.

        Returns:
            'APPROVED', 'DENIED', or 'TIMEOUT'.
        """
        timeout_seconds = timeout_hours * 3600
        elapsed = 0

        logger.info(
            f"Waiting for approval {approval_id} "
            f"(timeout: {timeout_hours}h)",
            extra={"action": "wait_approval"},
        )

        while elapsed < timeout_seconds:
            response = self.get_approval_response(approval_id)
            if response is not None:
                return response
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        # Timeout — mark as such
        self._pending_approvals[approval_id] = "TIMEOUT"
        logger.warning(
            f"Approval {approval_id} timed out after {timeout_hours}h",
            extra={"action": "approval_timeout"},
        )
        return "TIMEOUT"


def _escape_md(text: str) -> str:
    """Escape special characters for Telegram MarkdownV2.

    MarkdownV2 requires escaping these characters:
    _ * [ ] ( ) ~ ` > # + - = | { } . !
    """
    special_chars = r"_*[]()~`>#+-=|{}.!"
    result = []
    for char in text:
        if char in special_chars:
            result.append(f"\\{char}")
        else:
            result.append(char)
    return "".join(result)
