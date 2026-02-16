"""Instagram authentication via Playwright browser automation.

Handles login, 2FA detection, session validation, and re-authentication.
"""

import asyncio
from typing import Optional

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from app.instagram.browser import InstagramBrowser
from app.utils.logger import get_logger
from app.utils.rate_limiter import random_delay

logger = get_logger("auth")

INSTAGRAM_URL = "https://www.instagram.com/"
LOGIN_URL = "https://www.instagram.com/accounts/login/"


async def is_logged_in(page: Page) -> bool:
    """Check if the current page session is authenticated.

    Navigates to Instagram and checks for indicators of a
    logged-in state (profile icon, navigation elements).

    Args:
        page: The Playwright page to check.

    Returns:
        True if the user appears to be logged in.
    """
    try:
        await page.goto(INSTAGRAM_URL, wait_until="domcontentloaded", timeout=15000)
        await asyncio.sleep(2)

        # If we're redirected to login page, we're not logged in
        if "/accounts/login" in page.url:
            logger.info("Session expired — redirected to login page")
            return False

        # Check for common logged-in indicators
        # The navigation bar with profile link is present when logged in
        logged_in_indicators = [
            'svg[aria-label="Home"]',
            'a[href*="/direct/inbox/"]',
            'svg[aria-label="New post"]',
        ]

        for selector in logged_in_indicators:
            try:
                element = await page.wait_for_selector(
                    selector, timeout=5000, state="attached"
                )
                if element:
                    logger.info("Session is valid — user is logged in")
                    return True
            except PlaywrightTimeout:
                continue

        logger.info("Could not confirm logged-in state")
        return False

    except Exception as e:
        logger.error(f"Error checking login state: {e}")
        return False


async def login(
    page: Page,
    username: str,
    password: str,
    handle_2fa_callback=None,
) -> bool:
    """Log into Instagram with username and password.

    Args:
        page: The Playwright page to use.
        username: Instagram username.
        password: Instagram password.
        handle_2fa_callback: Optional async callback that returns
            the 2FA code. If None, 2FA will cause login to fail.

    Returns:
        True if login was successful.
    """
    logger.info(
        f"Attempting login for @{username}",
        extra={"action": "login", "username": username},
    )

    try:
        # Navigate to login page
        await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=15000)
        await asyncio.sleep(2)

        # Dismiss cookie consent if present
        try:
            cookie_btn = await page.wait_for_selector(
                'button:has-text("Allow all cookies"), '
                'button:has-text("Allow essential and optional cookies"), '
                'button:has-text("Accept")',
                timeout=3000,
            )
            if cookie_btn:
                await cookie_btn.click()
                logger.info("Dismissed cookie consent dialog")
                await asyncio.sleep(1)
        except PlaywrightTimeout:
            pass  # No cookie dialog

        # Fill in credentials
        username_input = await page.wait_for_selector(
            'input[name="username"]', timeout=10000
        )
        await username_input.click()
        await username_input.fill("")  # Clear first
        await username_input.type(username, delay=50)  # Human-like typing

        await random_delay(1, 2)

        password_input = await page.wait_for_selector(
            'input[name="password"]', timeout=5000
        )
        await password_input.click()
        await password_input.fill("")
        await password_input.type(password, delay=50)

        await random_delay(1, 2)

        # Click login button
        login_button = await page.wait_for_selector(
            'button[type="submit"]', timeout=5000
        )
        await login_button.click()

        logger.info("Credentials submitted, waiting for response...")
        await asyncio.sleep(4)

        # Check for various post-login scenarios
        # 1. Check for wrong credentials error
        error_message = await _check_login_error(page)
        if error_message:
            logger.error(
                f"Login failed: {error_message}",
                extra={"action": "login", "status": "failed", "detail": error_message},
            )
            return False

        # 2. Check for 2FA / security code prompt
        if await _is_2fa_required(page):
            logger.info("2FA required")
            if handle_2fa_callback:
                code = await handle_2fa_callback()
                if code:
                    return await _submit_2fa_code(page, code)
            logger.error("2FA required but no callback provided")
            return False

        # 3. Check for "Save Your Login Info?" dialog
        await _dismiss_save_login_dialog(page)

        # 4. Check for "Turn on Notifications?" dialog
        await _dismiss_notifications_dialog(page)

        # 5. Verify we're actually logged in
        if await is_logged_in(page):
            logger.info(
                f"Login successful for @{username}",
                extra={"action": "login", "status": "success", "username": username},
            )
            return True

        logger.warning("Login flow completed but could not verify logged-in state")
        return False

    except PlaywrightTimeout as e:
        logger.error(
            f"Login timed out: {e}",
            extra={"action": "login", "status": "timeout"},
        )
        return False
    except Exception as e:
        logger.error(
            f"Login error: {e}",
            extra={"action": "login", "status": "error"},
        )
        return False


async def ensure_authenticated(
    browser: InstagramBrowser,
    username: str,
    password: str,
    handle_2fa_callback=None,
) -> bool:
    """Ensure the browser session is authenticated.

    First tries to restore a saved session. If the session is
    expired or no saved session exists, performs a fresh login.

    Args:
        browser: The InstagramBrowser instance.
        username: Instagram username.
        password: Instagram password.
        handle_2fa_callback: Optional 2FA code callback.

    Returns:
        True if authenticated (either restored or fresh login).
    """
    page = await browser.get_page()

    # Try existing session first
    if browser.has_saved_session():
        logger.info("Found saved session — checking validity...")
        if await is_logged_in(page):
            return True
        logger.info("Saved session is expired — performing fresh login")

    # Fresh login
    success = await login(page, username, password, handle_2fa_callback)

    if success:
        # Save the new session cookies
        await browser._save_cookies()

    return success


# --- Private helpers ---


async def _check_login_error(page: Page) -> Optional[str]:
    """Check if the login page shows an error message."""
    error_selectors = [
        "#slfErrorAlert",
        'p[data-testid="login-error-message"]',
        'div[role="alert"]',
    ]
    for selector in error_selectors:
        try:
            element = await page.wait_for_selector(selector, timeout=2000)
            if element:
                text = await element.inner_text()
                if text.strip():
                    return text.strip()
        except PlaywrightTimeout:
            continue
    return None


async def _is_2fa_required(page: Page) -> bool:
    """Check if Instagram is asking for a 2FA security code."""
    twofa_indicators = [
        'input[name="verificationCode"]',
        'input[name="security_code"]',
        'text="Security Code"',
        'text="Enter the code"',
    ]
    for selector in twofa_indicators:
        try:
            element = await page.wait_for_selector(selector, timeout=3000)
            if element:
                return True
        except PlaywrightTimeout:
            continue
    return False


async def _submit_2fa_code(page: Page, code: str) -> bool:
    """Submit a 2FA verification code.

    Args:
        page: The Playwright page.
        code: The 2FA code to submit.

    Returns:
        True if 2FA was accepted.
    """
    logger.info("Submitting 2FA code")
    try:
        code_input = await page.wait_for_selector(
            'input[name="verificationCode"], input[name="security_code"]',
            timeout=5000,
        )
        await code_input.fill("")
        await code_input.type(code, delay=50)
        await random_delay(1, 2)

        # Click confirm/submit button
        submit_btn = await page.wait_for_selector(
            'button[type="button"]:has-text("Confirm"), '
            'button:has-text("Submit"), '
            'button[type="submit"]',
            timeout=5000,
        )
        await submit_btn.click()
        await asyncio.sleep(4)

        # Dismiss post-login dialogs
        await _dismiss_save_login_dialog(page)
        await _dismiss_notifications_dialog(page)

        return True

    except Exception as e:
        logger.error(f"2FA submission failed: {e}")
        return False


async def _dismiss_save_login_dialog(page: Page) -> None:
    """Dismiss the 'Save Your Login Info?' dialog if present."""
    try:
        not_now = await page.wait_for_selector(
            'button:has-text("Not Now"), '
            'button:has-text("Not now")',
            timeout=3000,
        )
        if not_now:
            await not_now.click()
            logger.info("Dismissed 'Save Login Info' dialog")
            await asyncio.sleep(1)
    except PlaywrightTimeout:
        pass


async def _dismiss_notifications_dialog(page: Page) -> None:
    """Dismiss the 'Turn on Notifications?' dialog if present."""
    try:
        not_now = await page.wait_for_selector(
            'button:has-text("Not Now"), '
            'button:has-text("Not now")',
            timeout=3000,
        )
        if not_now:
            await not_now.click()
            logger.info("Dismissed 'Turn on Notifications' dialog")
            await asyncio.sleep(1)
    except PlaywrightTimeout:
        pass
