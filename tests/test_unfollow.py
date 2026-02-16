"""Tests for the Instagram unfollow module.

Uses mocked Playwright pages to test unfollow logic without
hitting Instagram.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from app.instagram.unfollow import (
    unfollow_accounts,
    _is_action_blocked,
    _confirm_unfollow,
    _find_following_button,
    _find_following_link,
    RATE_LIMIT_PAUSE,
)


def make_mock_page(url="https://www.instagram.com/testuser/"):
    """Create a mock Playwright page with sensible defaults."""
    page = AsyncMock()
    page.url = url
    page.goto = AsyncMock()
    page.wait_for_selector = AsyncMock(return_value=None)
    page.query_selector = AsyncMock(return_value=None)
    page.query_selector_all = AsyncMock(return_value=[])
    page.evaluate = AsyncMock()
    return page


def make_mock_element(text="", href=""):
    """Create a mock page element."""
    el = AsyncMock()
    el.inner_text = AsyncMock(return_value=text)
    el.get_attribute = AsyncMock(return_value=href)
    el.click = AsyncMock()
    el.query_selector = AsyncMock(return_value=None)
    return el


class TestFindFollowingLink:
    """Tests for _find_following_link helper."""

    @pytest.mark.asyncio
    async def test_finds_by_href(self):
        """Should find the Following link by href pattern."""
        page = make_mock_page()
        mock_link = make_mock_element()
        page.wait_for_selector = AsyncMock(return_value=mock_link)

        result = await _find_following_link(page)
        assert result is not None

    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(self):
        """Should return None when no Following link exists."""
        page = make_mock_page()
        from playwright.async_api import TimeoutError as PlaywrightTimeout

        page.wait_for_selector = AsyncMock(
            side_effect=PlaywrightTimeout("timeout")
        )
        page.query_selector_all = AsyncMock(return_value=[])

        result = await _find_following_link(page)
        assert result is None


class TestFindFollowingButton:
    """Tests for _find_following_button helper."""

    @pytest.mark.asyncio
    async def test_finds_following_button(self):
        """Should find the Following button on a profile."""
        page = make_mock_page()
        mock_btn = make_mock_element(text="Following")
        page.wait_for_selector = AsyncMock(return_value=mock_btn)

        result = await _find_following_button(page)
        assert result is not None

    @pytest.mark.asyncio
    async def test_returns_none_when_not_following(self):
        """Should return None when no Following button exists."""
        page = make_mock_page()
        from playwright.async_api import TimeoutError as PlaywrightTimeout

        page.wait_for_selector = AsyncMock(
            side_effect=PlaywrightTimeout("timeout")
        )

        result = await _find_following_button(page)
        assert result is None


class TestConfirmUnfollow:
    """Tests for _confirm_unfollow helper."""

    @pytest.mark.asyncio
    async def test_confirms_unfollow(self):
        """Should click the Unfollow confirmation button."""
        page = make_mock_page()
        mock_btn = make_mock_element(text="Unfollow")
        page.wait_for_selector = AsyncMock(return_value=mock_btn)

        result = await _confirm_unfollow(page)
        assert result is True
        mock_btn.click.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_false_no_dialog(self):
        """Should return False when no confirmation dialog appears."""
        page = make_mock_page()
        from playwright.async_api import TimeoutError as PlaywrightTimeout

        page.wait_for_selector = AsyncMock(
            side_effect=PlaywrightTimeout("timeout")
        )

        result = await _confirm_unfollow(page)
        assert result is False


class TestIsActionBlocked:
    """Tests for _is_action_blocked detection."""

    @pytest.mark.asyncio
    async def test_detects_block(self):
        """Should detect 'Action Blocked' message."""
        page = make_mock_page()
        mock_alert = make_mock_element(text="Action Blocked")
        ok_btn = make_mock_element(text="OK")

        async def selector_side_effect(selector, **kwargs):
            if "Action Blocked" in selector:
                return mock_alert
            if "OK" in selector:
                return ok_btn
            from playwright.async_api import TimeoutError as PlaywrightTimeout
            raise PlaywrightTimeout("timeout")

        page.wait_for_selector = AsyncMock(side_effect=selector_side_effect)

        result = await _is_action_blocked(page)
        assert result is True

    @pytest.mark.asyncio
    async def test_no_block(self):
        """Should return False when no block is detected."""
        page = make_mock_page()
        from playwright.async_api import TimeoutError as PlaywrightTimeout

        page.wait_for_selector = AsyncMock(
            side_effect=PlaywrightTimeout("timeout")
        )

        result = await _is_action_blocked(page)
        assert result is False


class TestUnfollowAccounts:
    """Tests for the main unfollow_accounts function."""

    @pytest.mark.asyncio
    @patch("app.instagram.unfollow.random_delay", new_callable=AsyncMock)
    @patch("app.instagram.unfollow._unfollow_single_account")
    async def test_unfollows_all_accounts(self, mock_unfollow, mock_delay):
        """Should attempt to unfollow all provided accounts."""
        accounts = [
            {"username": "user1"},
            {"username": "user2"},
            {"username": "user3"},
        ]
        mock_unfollow.return_value = {"username": "test", "status": "SUCCESS"}
        mock_delay.return_value = 1.0

        page = make_mock_page()
        results = await unfollow_accounts(
            page, accounts, delay_min=1, delay_max=2, batch_id="test-batch"
        )

        assert len(results) == 3
        assert all(r["status"] == "SUCCESS" for r in results)
        assert mock_unfollow.call_count == 3
        # Delay called between accounts (not after last one)
        assert mock_delay.call_count == 2

    @pytest.mark.asyncio
    @patch("app.instagram.unfollow.random_delay", new_callable=AsyncMock)
    @patch("app.instagram.unfollow._unfollow_single_account")
    async def test_handles_mixed_results(self, mock_unfollow, mock_delay):
        """Should handle a mix of success and failure results."""
        accounts = [
            {"username": "user1"},
            {"username": "user2"},
        ]
        mock_unfollow.side_effect = [
            {"username": "user1", "status": "SUCCESS"},
            {"username": "user2", "status": "FAILED"},
        ]
        mock_delay.return_value = 1.0

        page = make_mock_page()
        results = await unfollow_accounts(
            page, accounts, delay_min=1, delay_max=2
        )

        assert len(results) == 2
        assert results[0]["status"] == "SUCCESS"
        assert results[1]["status"] == "FAILED"

    @pytest.mark.asyncio
    @patch("app.instagram.unfollow.random_delay", new_callable=AsyncMock)
    @patch("app.instagram.unfollow._unfollow_single_account")
    async def test_generates_batch_id_if_none(self, mock_unfollow, mock_delay):
        """Should auto-generate a batch_id if none is provided."""
        accounts = [{"username": "user1"}]
        mock_unfollow.return_value = {"username": "user1", "status": "SUCCESS"}
        mock_delay.return_value = 1.0

        page = make_mock_page()
        results = await unfollow_accounts(page, accounts, delay_min=1, delay_max=2)

        assert len(results) == 1
        assert "batch_id" in results[0]
        assert results[0]["batch_id"] is not None

    @pytest.mark.asyncio
    @patch("app.instagram.unfollow.random_delay", new_callable=AsyncMock)
    @patch("app.instagram.unfollow._unfollow_single_account")
    async def test_results_include_timestamp(self, mock_unfollow, mock_delay):
        """Each result should include a timestamp."""
        accounts = [{"username": "user1"}]
        mock_unfollow.return_value = {"username": "user1", "status": "SUCCESS"}
        mock_delay.return_value = 1.0

        page = make_mock_page()
        results = await unfollow_accounts(page, accounts, delay_min=1, delay_max=2)

        assert "timestamp" in results[0]

    @pytest.mark.asyncio
    @patch("app.instagram.unfollow.asyncio.sleep", new_callable=AsyncMock)
    @patch("app.instagram.unfollow.random_delay", new_callable=AsyncMock)
    @patch("app.instagram.unfollow._unfollow_single_account")
    async def test_rate_limit_triggers_pause(
        self, mock_unfollow, mock_delay, mock_sleep
    ):
        """Should pause on RATE_LIMITED and retry once."""
        accounts = [{"username": "user1"}, {"username": "user2"}]
        # First call rate limited, retry succeeds, second account succeeds
        mock_unfollow.side_effect = [
            {"username": "user1", "status": "RATE_LIMITED"},
            {"username": "user1", "status": "SUCCESS"},  # retry
            {"username": "user2", "status": "SUCCESS"},
        ]
        mock_delay.return_value = 1.0

        page = make_mock_page()
        results = await unfollow_accounts(page, accounts, delay_min=1, delay_max=2)

        assert len(results) == 2
        # After retry, user1 should be SUCCESS
        assert results[0]["status"] == "SUCCESS"
        assert results[1]["status"] == "SUCCESS"
        # Should have paused for rate limit
        mock_sleep.assert_called_with(RATE_LIMIT_PAUSE)

    @pytest.mark.asyncio
    @patch("app.instagram.unfollow.random_delay", new_callable=AsyncMock)
    @patch("app.instagram.unfollow._unfollow_single_account")
    async def test_empty_accounts_list(self, mock_unfollow, mock_delay):
        """Should handle empty accounts list gracefully."""
        page = make_mock_page()
        results = await unfollow_accounts(page, [], delay_min=1, delay_max=2)

        assert len(results) == 0
        mock_unfollow.assert_not_called()
