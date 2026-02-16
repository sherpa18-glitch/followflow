"""Tests for the Instagram follow module.

Uses mocked Playwright pages to test follow logic without
hitting Instagram.
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.instagram.follow import (
    follow_accounts,
    get_follow_summary,
    _find_follow_button,
    _is_already_following,
    _determine_follow_type,
    _is_action_blocked,
    RATE_LIMIT_PAUSE,
)


def make_mock_page():
    """Create a mock Playwright page."""
    page = AsyncMock()
    page.url = "https://www.instagram.com/test/"
    page.goto = AsyncMock()
    page.wait_for_selector = AsyncMock(return_value=None)
    page.query_selector = AsyncMock(return_value=None)
    return page


def make_mock_element(text=""):
    """Create a mock page element."""
    el = AsyncMock()
    el.inner_text = AsyncMock(return_value=text)
    el.click = AsyncMock()
    return el


# --- Tests: Find Follow Button ---

class TestFindFollowButton:
    @pytest.mark.asyncio
    async def test_finds_follow_button(self):
        page = make_mock_page()
        mock_btn = make_mock_element(text="Follow")
        page.wait_for_selector = AsyncMock(return_value=mock_btn)

        result = await _find_follow_button(page)
        assert result is not None

    @pytest.mark.asyncio
    async def test_rejects_following_button(self):
        """Should not return a button that says 'Following'."""
        page = make_mock_page()
        mock_btn = make_mock_element(text="Following")
        page.wait_for_selector = AsyncMock(return_value=mock_btn)

        result = await _find_follow_button(page)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(self):
        page = make_mock_page()
        from playwright.async_api import TimeoutError as PlaywrightTimeout
        page.wait_for_selector = AsyncMock(
            side_effect=PlaywrightTimeout("timeout")
        )

        result = await _find_follow_button(page)
        assert result is None


# --- Tests: Already Following Detection ---

class TestIsAlreadyFollowing:
    @pytest.mark.asyncio
    async def test_detects_following(self):
        page = make_mock_page()
        mock_btn = make_mock_element(text="Following")
        page.wait_for_selector = AsyncMock(return_value=mock_btn)

        result = await _is_already_following(page)
        assert result is True

    @pytest.mark.asyncio
    async def test_detects_requested(self):
        page = make_mock_page()
        mock_btn = make_mock_element(text="Requested")
        page.wait_for_selector = AsyncMock(return_value=mock_btn)

        result = await _is_already_following(page)
        assert result is True

    @pytest.mark.asyncio
    async def test_not_following(self):
        page = make_mock_page()
        from playwright.async_api import TimeoutError as PlaywrightTimeout
        page.wait_for_selector = AsyncMock(
            side_effect=PlaywrightTimeout("timeout")
        )

        result = await _is_already_following(page)
        assert result is False


# --- Tests: Follow Type Detection ---

class TestDetermineFollowType:
    @pytest.mark.asyncio
    async def test_private_account(self):
        page = make_mock_page()
        mock_btn = make_mock_element(text="Requested")

        call_count = 0
        async def selector_side_effect(selector, **kwargs):
            nonlocal call_count
            call_count += 1
            if "Requested" in selector:
                return mock_btn
            from playwright.async_api import TimeoutError as PlaywrightTimeout
            raise PlaywrightTimeout("timeout")

        page.wait_for_selector = AsyncMock(side_effect=selector_side_effect)

        result = await _determine_follow_type(page)
        assert result == "private"

    @pytest.mark.asyncio
    async def test_public_account(self):
        page = make_mock_page()
        mock_btn = make_mock_element(text="Following")

        async def selector_side_effect(selector, **kwargs):
            if "Following" in selector:
                return mock_btn
            from playwright.async_api import TimeoutError as PlaywrightTimeout
            raise PlaywrightTimeout("timeout")

        page.wait_for_selector = AsyncMock(side_effect=selector_side_effect)

        result = await _determine_follow_type(page)
        assert result == "public"


# --- Tests: Follow Accounts ---

class TestFollowAccounts:
    @pytest.mark.asyncio
    @patch("app.instagram.follow.random_delay", new_callable=AsyncMock)
    @patch("app.instagram.follow._follow_single_account")
    async def test_follows_all_accounts(self, mock_follow, mock_delay):
        accounts = [
            {"username": "target1", "follower_count": 800, "following_count": 4000, "region": "NA"},
            {"username": "target2", "follower_count": 1200, "following_count": 3500, "region": "KR"},
        ]
        mock_follow.return_value = {
            "username": "test", "status": "SUCCESS", "follow_type": "public",
        }
        mock_delay.return_value = 1.0

        page = make_mock_page()
        results = await follow_accounts(
            page, accounts, delay_min=1, delay_max=2, batch_id="test-batch"
        )

        assert len(results) == 2
        assert all(r["status"] == "SUCCESS" for r in results)
        assert mock_follow.call_count == 2
        assert mock_delay.call_count == 1  # Between accounts, not after last

    @pytest.mark.asyncio
    @patch("app.instagram.follow.random_delay", new_callable=AsyncMock)
    @patch("app.instagram.follow._follow_single_account")
    async def test_preserves_account_metadata(self, mock_follow, mock_delay):
        accounts = [
            {
                "username": "target1",
                "follower_count": 800,
                "following_count": 4000,
                "region": "KR",
                "region_confidence": "HIGH",
            },
        ]
        mock_follow.return_value = {
            "username": "target1", "status": "SUCCESS", "follow_type": "public",
        }
        mock_delay.return_value = 1.0

        page = make_mock_page()
        results = await follow_accounts(page, accounts, delay_min=1, delay_max=2)

        assert results[0]["follower_count"] == 800
        assert results[0]["following_count"] == 4000
        assert results[0]["region"] == "KR"
        assert results[0]["region_confidence"] == "HIGH"

    @pytest.mark.asyncio
    @patch("app.instagram.follow.random_delay", new_callable=AsyncMock)
    @patch("app.instagram.follow._follow_single_account")
    async def test_handles_mixed_results(self, mock_follow, mock_delay):
        accounts = [
            {"username": "t1"}, {"username": "t2"}, {"username": "t3"},
        ]
        mock_follow.side_effect = [
            {"username": "t1", "status": "SUCCESS", "follow_type": "public"},
            {"username": "t2", "status": "FAILED", "follow_type": None},
            {"username": "t3", "status": "SUCCESS", "follow_type": "private"},
        ]
        mock_delay.return_value = 1.0

        page = make_mock_page()
        results = await follow_accounts(page, accounts, delay_min=1, delay_max=2)

        assert results[0]["status"] == "SUCCESS"
        assert results[1]["status"] == "FAILED"
        assert results[2]["status"] == "SUCCESS"
        assert results[2]["follow_type"] == "private"

    @pytest.mark.asyncio
    @patch("app.instagram.follow.random_delay", new_callable=AsyncMock)
    @patch("app.instagram.follow._follow_single_account")
    async def test_empty_list(self, mock_follow, mock_delay):
        page = make_mock_page()
        results = await follow_accounts(page, [], delay_min=1, delay_max=2)
        assert len(results) == 0
        mock_follow.assert_not_called()


# --- Tests: Follow Summary ---

class TestGetFollowSummary:
    def test_all_success_public(self):
        results = [
            {"status": "SUCCESS", "follow_type": "public"},
            {"status": "SUCCESS", "follow_type": "public"},
        ]
        summary = get_follow_summary(results)
        assert summary["total_sent"] == 2
        assert summary["public_count"] == 2
        assert summary["private_count"] == 0
        assert summary["fail_count"] == 0

    def test_mixed_results(self):
        results = [
            {"status": "SUCCESS", "follow_type": "public"},
            {"status": "SUCCESS", "follow_type": "private"},
            {"status": "FAILED", "follow_type": None},
        ]
        summary = get_follow_summary(results)
        assert summary["total_sent"] == 2
        assert summary["public_count"] == 1
        assert summary["private_count"] == 1
        assert summary["fail_count"] == 1

    def test_empty_results(self):
        summary = get_follow_summary([])
        assert summary["total_sent"] == 0
        assert summary["fail_count"] == 0
