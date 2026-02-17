"""Playwright browser session management for Instagram automation."""

import json
import os
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

from app.utils.logger import get_logger

logger = get_logger("browser")

# Persist cookies here so we can skip re-login on subsequent runs
COOKIES_PATH = Path("session_cookies.json")

# Realistic mobile-like viewport and user agent
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
DEFAULT_VIEWPORT = {"width": 1280, "height": 800}


class InstagramBrowser:
    """Manages a Playwright Chromium browser session for Instagram.

    Handles browser launch, cookie persistence (save/restore),
    and provides a reusable page for Instagram automation.

    Usage:
        async with InstagramBrowser() as browser:
            page = await browser.get_page()
            # ... interact with Instagram
    """

    def __init__(
        self,
        headless: bool = True,
        cookies_path: Optional[Path] = None,
        user_agent: str = DEFAULT_USER_AGENT,
        viewport: Optional[dict] = None,
    ):
        self.headless = headless
        self.cookies_path = cookies_path or COOKIES_PATH
        self.user_agent = user_agent
        self.viewport = viewport or DEFAULT_VIEWPORT

        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    async def __aenter__(self):
        await self.launch()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def launch(self) -> None:
        """Launch the browser and create a context with saved cookies."""
        logger.info("Launching Playwright Chromium browser")
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )
        self._context = await self._browser.new_context(
            user_agent=self.user_agent,
            viewport=self.viewport,
            locale="en-US",
            timezone_id="America/New_York",
        )

        # Restore cookies if we have a saved session
        await self._load_cookies()

        self._page = await self._context.new_page()

        # Anti-detection: override navigator properties
        await self._page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });
            window.chrome = { runtime: {} };
        """)

        logger.info("Browser launched and page ready")

    async def close(self) -> None:
        """Save cookies and shut down the browser."""
        if self._context:
            await self._save_cookies()
        if self._browser:
            await self._browser.close()
            logger.info("Browser closed")
        if self._playwright:
            await self._playwright.stop()

    async def get_page(self) -> Page:
        """Return the active page, launching the browser if needed."""
        if self._page is None:
            await self.launch()
        return self._page

    async def _save_cookies(self) -> None:
        """Persist the current browser cookies to disk."""
        try:
            cookies = await self._context.cookies()
            with open(self.cookies_path, "w") as f:
                json.dump(cookies, f, indent=2)
            logger.info(
                f"Saved {len(cookies)} cookies to {self.cookies_path}",
                extra={"action": "save_cookies"},
            )
        except Exception as e:
            logger.error(
                f"Failed to save cookies: {e}",
                extra={"action": "save_cookies", "status": "error"},
            )

    async def _load_cookies(self) -> None:
        """Restore cookies from disk if available."""
        if not self.cookies_path.exists():
            logger.info("No saved cookies found — fresh session")
            return

        try:
            with open(self.cookies_path, "r") as f:
                cookies = json.load(f)

            if cookies:
                await self._context.add_cookies(cookies)
                logger.info(
                    f"Restored {len(cookies)} cookies from {self.cookies_path}",
                    extra={"action": "load_cookies"},
                )
            else:
                logger.info("Cookie file is empty — fresh session")
        except (json.JSONDecodeError, Exception) as e:
            logger.warning(
                f"Failed to load cookies: {e}",
                extra={"action": "load_cookies", "status": "error"},
            )

    def has_saved_session(self) -> bool:
        """Check if a saved cookie file exists."""
        return self.cookies_path.exists() and self.cookies_path.stat().st_size > 10
