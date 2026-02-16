"""Tests for the Instagram browser session manager."""

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.instagram.browser import InstagramBrowser, COOKIES_PATH


@pytest.fixture
def tmp_cookies(tmp_path):
    """Return a temporary cookies file path."""
    return tmp_path / "test_cookies.json"


class TestInstagramBrowser:
    """Tests for InstagramBrowser class."""

    def test_default_init(self):
        """Browser should initialize with sensible defaults."""
        browser = InstagramBrowser()
        assert browser.headless is True
        assert browser.cookies_path == COOKIES_PATH
        assert "Chrome" in browser.user_agent
        assert browser.viewport["width"] == 1280

    def test_custom_init(self, tmp_cookies):
        """Browser should accept custom configuration."""
        browser = InstagramBrowser(
            headless=False,
            cookies_path=tmp_cookies,
            user_agent="TestAgent/1.0",
            viewport={"width": 800, "height": 600},
        )
        assert browser.headless is False
        assert browser.cookies_path == tmp_cookies
        assert browser.user_agent == "TestAgent/1.0"
        assert browser.viewport["width"] == 800

    def test_has_saved_session_no_file(self, tmp_cookies):
        """has_saved_session should return False when no cookie file exists."""
        browser = InstagramBrowser(cookies_path=tmp_cookies)
        assert browser.has_saved_session() is False

    def test_has_saved_session_empty_file(self, tmp_cookies):
        """has_saved_session should return False for tiny/empty files."""
        tmp_cookies.write_text("[]")
        browser = InstagramBrowser(cookies_path=tmp_cookies)
        assert browser.has_saved_session() is False

    def test_has_saved_session_valid_file(self, tmp_cookies):
        """has_saved_session should return True when cookie file has content."""
        cookies = [{"name": "sessionid", "value": "abc123", "domain": ".instagram.com"}]
        tmp_cookies.write_text(json.dumps(cookies))
        browser = InstagramBrowser(cookies_path=tmp_cookies)
        assert browser.has_saved_session() is True


class TestCookiePersistence:
    """Tests for cookie save/load functionality."""

    @pytest.mark.asyncio
    async def test_load_cookies_no_file(self, tmp_cookies):
        """Loading cookies when no file exists should not raise."""
        browser = InstagramBrowser(cookies_path=tmp_cookies)
        browser._context = AsyncMock()
        await browser._load_cookies()
        # Should not call add_cookies if no file
        browser._context.add_cookies.assert_not_called()

    @pytest.mark.asyncio
    async def test_load_cookies_valid_file(self, tmp_cookies):
        """Loading cookies from valid file should call add_cookies."""
        cookies = [
            {"name": "sessionid", "value": "abc123", "domain": ".instagram.com"},
            {"name": "csrftoken", "value": "xyz789", "domain": ".instagram.com"},
        ]
        tmp_cookies.write_text(json.dumps(cookies))

        browser = InstagramBrowser(cookies_path=tmp_cookies)
        browser._context = AsyncMock()
        await browser._load_cookies()
        browser._context.add_cookies.assert_called_once_with(cookies)

    @pytest.mark.asyncio
    async def test_load_cookies_corrupt_file(self, tmp_cookies):
        """Loading corrupted cookies should not raise."""
        tmp_cookies.write_text("not valid json {{{")

        browser = InstagramBrowser(cookies_path=tmp_cookies)
        browser._context = AsyncMock()
        # Should handle gracefully, not raise
        await browser._load_cookies()
        browser._context.add_cookies.assert_not_called()

    @pytest.mark.asyncio
    async def test_save_cookies(self, tmp_cookies):
        """Saving cookies should write them to the file."""
        cookies = [
            {"name": "sessionid", "value": "saved123", "domain": ".instagram.com"},
        ]

        browser = InstagramBrowser(cookies_path=tmp_cookies)
        browser._context = AsyncMock()
        browser._context.cookies = AsyncMock(return_value=cookies)

        await browser._save_cookies()

        assert tmp_cookies.exists()
        saved = json.loads(tmp_cookies.read_text())
        assert len(saved) == 1
        assert saved[0]["name"] == "sessionid"
        assert saved[0]["value"] == "saved123"
