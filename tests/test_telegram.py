"""Tests for the Telegram bot and callback handlers.

Uses mocked Telegram Bot API to test notification formatting,
approval state management, and callback handling without
actually sending messages.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.telegram.bot import FollowFlowBot, _escape_md


# --- Fixtures ---

@pytest.fixture
def bot():
    """Create a FollowFlowBot with mocked Telegram Bot."""
    with patch("app.telegram.bot.Bot") as MockBot:
        mock_bot_instance = AsyncMock()
        mock_message = AsyncMock()
        mock_message.message_id = 12345
        mock_bot_instance.send_message = AsyncMock(return_value=mock_message)
        MockBot.return_value = mock_bot_instance

        fb = FollowFlowBot(token="fake-token", chat_id="fake-chat-id")
        yield fb


@pytest.fixture
def sample_unfollow_accounts():
    return [
        {"username": "old_user_1", "full_name": "Old User 1"},
        {"username": "old_user_2", "full_name": "Old User 2"},
        {"username": "old_user_3", "full_name": ""},
        {"username": "old_user_4", "full_name": "Old User 4"},
        {"username": "old_user_5", "full_name": "Old User 5"},
        {"username": "old_user_6", "full_name": "Old User 6"},
    ]


@pytest.fixture
def sample_follow_accounts():
    return [
        {
            "username": "puppy_fan_kr",
            "follower_count": 1120,
            "following_count": 4200,
            "region": "KR",
        },
        {
            "username": "dogmom_texas",
            "follower_count": 890,
            "following_count": 3800,
            "region": "NA",
        },
        {
            "username": "paws_tokyo",
            "follower_count": 1540,
            "following_count": 5100,
            "region": "JP",
        },
    ]


# --- Tests: Escape Markdown ---

class TestEscapeMarkdown:
    def test_escapes_special_chars(self):
        assert _escape_md("hello_world") == "hello\\_world"
        assert _escape_md("test.com") == "test\\.com"
        assert _escape_md("100!") == "100\\!"

    def test_plain_text_unchanged(self):
        assert _escape_md("hello world") == "hello world"
        assert _escape_md("username123") == "username123"

    def test_multiple_special_chars(self):
        result = _escape_md("@user (123)")
        assert "\\(" in result
        assert "\\)" in result


# --- Tests: Approval State Management ---

class TestApprovalState:
    def test_initial_state_is_none(self, bot):
        """New approval should have None response."""
        bot._pending_approvals["test-id"] = None
        assert bot.get_approval_response("test-id") is None

    def test_set_approved(self, bot):
        bot._pending_approvals["test-id"] = None
        bot.set_approval_response("test-id", "APPROVED")
        assert bot.get_approval_response("test-id") == "APPROVED"

    def test_set_denied(self, bot):
        bot._pending_approvals["test-id"] = None
        bot.set_approval_response("test-id", "DENIED")
        assert bot.get_approval_response("test-id") == "DENIED"

    def test_unknown_id_returns_none(self, bot):
        assert bot.get_approval_response("nonexistent") is None


# --- Tests: Send Unfollow Approval Request ---

class TestSendUnfollowApproval:
    @pytest.mark.asyncio
    async def test_sends_message(self, bot, sample_unfollow_accounts):
        msg_id = await bot.send_unfollow_approval_request(
            "approval-001", sample_unfollow_accounts
        )
        assert msg_id == 12345
        bot.bot.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_message_contains_count(self, bot, sample_unfollow_accounts):
        await bot.send_unfollow_approval_request(
            "approval-002", sample_unfollow_accounts
        )
        call_kwargs = bot.bot.send_message.call_args.kwargs
        text = call_kwargs["text"]
        assert "6 accounts" in text

    @pytest.mark.asyncio
    async def test_message_has_inline_keyboard(self, bot, sample_unfollow_accounts):
        await bot.send_unfollow_approval_request(
            "approval-003", sample_unfollow_accounts
        )
        call_kwargs = bot.bot.send_message.call_args.kwargs
        markup = call_kwargs["reply_markup"]
        buttons = markup.inline_keyboard[0]
        assert len(buttons) == 2
        assert "Approve" in buttons[0].text
        assert "Deny" in buttons[1].text

    @pytest.mark.asyncio
    async def test_registers_pending_approval(self, bot, sample_unfollow_accounts):
        await bot.send_unfollow_approval_request(
            "approval-004", sample_unfollow_accounts
        )
        assert "approval-004" in bot._pending_approvals
        assert bot._pending_approvals["approval-004"] is None

    @pytest.mark.asyncio
    async def test_callback_data_format(self, bot, sample_unfollow_accounts):
        await bot.send_unfollow_approval_request(
            "approval-005", sample_unfollow_accounts
        )
        call_kwargs = bot.bot.send_message.call_args.kwargs
        buttons = call_kwargs["reply_markup"].inline_keyboard[0]
        assert buttons[0].callback_data == "approve:approval-005"
        assert buttons[1].callback_data == "deny:approval-005"


# --- Tests: Send Follow Approval Request ---

class TestSendFollowApproval:
    @pytest.mark.asyncio
    async def test_sends_message(self, bot, sample_follow_accounts):
        msg_id = await bot.send_follow_approval_request(
            "follow-001", sample_follow_accounts
        )
        assert msg_id == 12345

    @pytest.mark.asyncio
    async def test_message_contains_criteria(self, bot, sample_follow_accounts):
        await bot.send_follow_approval_request(
            "follow-002", sample_follow_accounts
        )
        call_kwargs = bot.bot.send_message.call_args.kwargs
        text = call_kwargs["text"]
        assert "3 active accounts" in text
        assert "5,000" in text
        assert "100" in text

    @pytest.mark.asyncio
    async def test_message_has_inline_keyboard(self, bot, sample_follow_accounts):
        await bot.send_follow_approval_request(
            "follow-003", sample_follow_accounts
        )
        call_kwargs = bot.bot.send_message.call_args.kwargs
        buttons = call_kwargs["reply_markup"].inline_keyboard[0]
        assert len(buttons) == 2


# --- Tests: Send Completion Notices ---

class TestSendCompletionNotices:
    @pytest.mark.asyncio
    async def test_unfollow_complete(self, bot):
        msg_id = await bot.send_unfollow_complete(
            success_count=98,
            fail_count=2,
            old_following=1402,
            new_following=1304,
        )
        assert msg_id == 12345
        call_kwargs = bot.bot.send_message.call_args.kwargs
        text = call_kwargs["text"]
        assert "98" in text
        assert "1402" in text
        assert "1304" in text

    @pytest.mark.asyncio
    async def test_follow_complete(self, bot):
        msg_id = await bot.send_follow_complete(
            total_sent=100,
            public_count=78,
            private_count=22,
            old_following=1304,
            new_following=1404,
        )
        assert msg_id == 12345
        call_kwargs = bot.bot.send_message.call_args.kwargs
        text = call_kwargs["text"]
        assert "100" in text
        assert "78" in text
        assert "22" in text

    @pytest.mark.asyncio
    async def test_follow_complete_with_failures(self, bot):
        msg_id = await bot.send_follow_complete(
            total_sent=95,
            public_count=70,
            private_count=20,
            fail_count=5,
        )
        assert msg_id == 12345
        call_kwargs = bot.bot.send_message.call_args.kwargs
        text = call_kwargs["text"]
        assert "Failed" in text

    @pytest.mark.asyncio
    async def test_error_notification(self, bot):
        msg_id = await bot.send_error_notification(
            "Instagram session expired. Please re-authenticate."
        )
        assert msg_id == 12345
        call_kwargs = bot.bot.send_message.call_args.kwargs
        text = call_kwargs["text"]
        assert "Error" in text


# --- Tests: Wait for Approval ---

class TestWaitForApproval:
    @pytest.mark.asyncio
    async def test_returns_approved_immediately(self, bot):
        """Should return immediately if already approved."""
        bot._pending_approvals["fast-001"] = "APPROVED"
        result = await bot.wait_for_approval(
            "fast-001", timeout_hours=1, poll_interval=0.1
        )
        assert result == "APPROVED"

    @pytest.mark.asyncio
    async def test_returns_denied_immediately(self, bot):
        bot._pending_approvals["fast-002"] = "DENIED"
        result = await bot.wait_for_approval(
            "fast-002", timeout_hours=1, poll_interval=0.1
        )
        assert result == "DENIED"

    @pytest.mark.asyncio
    async def test_waits_then_returns_approved(self, bot):
        """Should poll and return when approval comes in."""
        bot._pending_approvals["wait-001"] = None

        async def approve_after_delay():
            await asyncio.sleep(0.3)
            bot.set_approval_response("wait-001", "APPROVED")

        asyncio.create_task(approve_after_delay())
        result = await bot.wait_for_approval(
            "wait-001", timeout_hours=1, poll_interval=0.1
        )
        assert result == "APPROVED"

    @pytest.mark.asyncio
    async def test_timeout_returns_timeout(self, bot):
        """Should return TIMEOUT after timeout period."""
        bot._pending_approvals["timeout-001"] = None
        # Very short timeout for testing
        result = await bot.wait_for_approval(
            "timeout-001",
            timeout_hours=0,  # 0 hours = immediate timeout
            poll_interval=0.01,
        )
        assert result == "TIMEOUT"
