"""Instagram unfollow action module.

Navigates to the authenticated user's Following list, sorts by
"Date followed: earliest", and unfollows accounts from the top
of the list with rate-limited delays between each action.
"""

import asyncio
import uuid
from datetime import datetime
from typing import List, Dict, Optional

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from app.utils.logger import get_logger
from app.utils.rate_limiter import random_delay

logger = get_logger("unfollow")

INSTAGRAM_URL = "https://www.instagram.com/"

# Time to pause when Instagram blocks an action (seconds)
RATE_LIMIT_PAUSE = 900  # 15 minutes


async def get_following_list_sorted(
    page: Page,
    username: str,
    count: int = 100,
) -> List[Dict[str, str]]:
    """Fetch the Following list sorted by 'Date followed: earliest'.

    Navigates to the user's profile, opens the Following modal,
    applies the sort, and scrolls to collect account entries.

    Args:
        page: Authenticated Playwright page.
        username: The authenticated user's Instagram handle.
        count: Number of accounts to collect from the top.

    Returns:
        List of dicts with keys: 'username', 'full_name' (if available).
        Ordered from oldest followed to newest.
    """
    logger.info(
        f"Fetching following list for @{username}, target count: {count}",
        extra={"action": "get_following_list", "username": username},
    )

    # Navigate to profile
    profile_url = f"{INSTAGRAM_URL}{username}/"
    try:
        await page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)
    except PlaywrightTimeout:
        logger.warning("Profile page load timed out, proceeding anyway")
    await asyncio.sleep(3)

    # Click on "Following" count to open the modal
    following_link = await _find_following_link(page)
    if not following_link:
        logger.error("Could not find 'Following' link on profile page")
        return []

    # Use dispatch_event to bypass overlay interception
    await following_link.dispatch_event("click")
    await asyncio.sleep(3)

    # Sort by "Date followed: earliest"
    sorted_ok = await _apply_sort_earliest(page)
    if not sorted_ok:
        logger.warning(
            "Could not apply 'Date followed: earliest' sort — "
            "proceeding with default order"
        )

    await asyncio.sleep(2)

    # Scroll and collect accounts
    accounts = await _scroll_and_collect_accounts(page, count)

    logger.info(
        f"Collected {len(accounts)} accounts from following list",
        extra={"action": "get_following_list", "detail": f"{len(accounts)} accounts"},
    )

    return accounts


async def unfollow_accounts(
    page: Page,
    accounts: List[Dict[str, str]],
    delay_min: int = 25,
    delay_max: int = 45,
    batch_id: Optional[str] = None,
) -> List[Dict]:
    """Unfollow a list of accounts with rate-limited delays.

    Navigates to each account's profile and clicks
    Following → Unfollow.

    Args:
        page: Authenticated Playwright page.
        accounts: List of account dicts with 'username' key.
        delay_min: Minimum seconds between unfollows.
        delay_max: Maximum seconds between unfollows.
        batch_id: Unique ID for this batch run (auto-generated if None).

    Returns:
        List of result dicts with keys: 'username', 'status',
        'timestamp'. Status is 'SUCCESS', 'FAILED', or 'RATE_LIMITED'.
    """
    if batch_id is None:
        batch_id = str(uuid.uuid4())

    results = []
    total = len(accounts)

    logger.info(
        f"Starting unfollow batch: {total} accounts, batch_id={batch_id}",
        extra={"action": "unfollow_batch", "batch_id": batch_id},
    )

    for i, account in enumerate(accounts):
        username = account["username"]
        logger.info(
            f"Unfollowing [{i+1}/{total}]: @{username}",
            extra={"action": "unfollow", "username": username, "batch_id": batch_id},
        )

        result = await _unfollow_single_account(page, username)
        result["batch_id"] = batch_id
        result["timestamp"] = datetime.utcnow().isoformat()
        results.append(result)

        if result["status"] == "RATE_LIMITED":
            logger.warning(
                f"Rate limited at account {i+1}/{total}. "
                f"Pausing for {RATE_LIMIT_PAUSE // 60} minutes...",
                extra={
                    "action": "unfollow",
                    "username": username,
                    "status": "RATE_LIMITED",
                },
            )
            await asyncio.sleep(RATE_LIMIT_PAUSE)
            # Retry once after the pause
            retry_result = await _unfollow_single_account(page, username)
            if retry_result["status"] == "SUCCESS":
                results[-1] = {
                    **retry_result,
                    "batch_id": batch_id,
                    "timestamp": datetime.utcnow().isoformat(),
                }

        # Rate-limited delay before next unfollow (skip after last one)
        if i < total - 1:
            await random_delay(delay_min, delay_max)

    success_count = sum(1 for r in results if r["status"] == "SUCCESS")
    fail_count = total - success_count
    logger.info(
        f"Unfollow batch complete: {success_count} success, {fail_count} failed",
        extra={
            "action": "unfollow_batch",
            "batch_id": batch_id,
            "detail": f"{success_count}/{total} success",
        },
    )

    return results


# --- Private helpers ---


async def _find_following_link(page: Page):
    """Find and return the 'Following' link/button on the profile page."""
    # Try most common selectors first (Instagram uses a:has-text("following"))
    selectors = [
        'a:has-text("following")',
        'li:has-text("following") a',
        'a[href*="/following"]',
        'span:has-text("following")',
    ]
    for selector in selectors:
        try:
            element = await page.wait_for_selector(selector, timeout=5000)
            if element:
                text = await element.inner_text()
                logger.info(f"Found following link: {text.strip()[:40]}")
                return element
        except PlaywrightTimeout:
            continue

    # Fallback: try finding by the following count pattern
    try:
        elements = await page.query_selector_all("a")
        for el in elements:
            text = await el.inner_text()
            if "following" in text.lower():
                logger.info(f"Found following link (fallback): {text.strip()[:40]}")
                return el
    except Exception:
        pass

    return None


async def _apply_sort_earliest(page: Page) -> bool:
    """Apply 'Date followed: earliest' sort in the Following modal.

    Returns True if the sort was successfully applied.
    """
    try:
        # Look for the sort/filter button in the modal
        sort_selectors = [
            'svg[aria-label="Sort options"]',
            'button[aria-label="Sort"]',
            'button:has-text("Sort")',
            # Instagram sometimes uses a filter icon
            'div[role="dialog"] svg[aria-label="Sort and filter"]',
        ]

        sort_button = None
        for selector in sort_selectors:
            try:
                sort_button = await page.wait_for_selector(selector, timeout=3000)
                if sort_button:
                    break
            except PlaywrightTimeout:
                continue

        if not sort_button:
            logger.info("Sort button not found — trying alternative approach")
            # Try clicking the sort icon by looking for it within the dialog
            try:
                sort_button = await page.wait_for_selector(
                    'div[role="dialog"] button:last-of-type', timeout=3000
                )
            except PlaywrightTimeout:
                return False

        if sort_button:
            await sort_button.click()
            await asyncio.sleep(1)

        # Select "Date followed: earliest" option
        earliest_selectors = [
            'text="Date followed: Earliest"',
            'text="Date followed: earliest"',
            'span:has-text("Earliest")',
            'label:has-text("Earliest")',
            'input[value="earliest"]',
        ]

        for selector in earliest_selectors:
            try:
                option = await page.wait_for_selector(selector, timeout=3000)
                if option:
                    await option.click()
                    await asyncio.sleep(1)
                    logger.info("Applied sort: Date followed: earliest")
                    return True
            except PlaywrightTimeout:
                continue

        return False

    except Exception as e:
        logger.error(f"Error applying sort: {e}")
        return False


async def _scroll_and_collect_accounts(
    page: Page,
    count: int,
) -> List[Dict[str, str]]:
    """Scroll through the Following modal and collect account entries.

    Args:
        page: The Playwright page with the Following modal open.
        count: Maximum number of accounts to collect.

    Returns:
        List of dicts with 'username' and optionally 'full_name'.
    """
    accounts = []
    seen_usernames = set()
    max_scroll_attempts = 50
    no_new_count = 0
    prev_count = 0

    for attempt in range(max_scroll_attempts):
        if len(accounts) >= count:
            break

        # Collect visible account entries from the dialog
        entries = await page.query_selector_all(
            'div[role="dialog"] a[role="link"][href^="/"]'
        )

        for entry in entries:
            if len(accounts) >= count:
                break

            try:
                href = await entry.get_attribute("href")
                if not href or href in ("/", "/explore/"):
                    continue

                # Skip non-profile links (login, signup, etc.)
                if "/accounts/" in href or "/explore/" in href:
                    continue

                # Extract username from href (e.g., "/username/" → "username")
                username = href.strip("/").split("/")[0]

                if username and username not in seen_usernames:
                    seen_usernames.add(username)

                    # Try to get full name
                    full_name = ""
                    try:
                        name_el = await entry.query_selector("span")
                        if name_el:
                            full_name = await name_el.inner_text()
                    except Exception:
                        pass

                    accounts.append({
                        "username": username,
                        "full_name": full_name,
                    })
            except Exception:
                continue

        new_count = len(accounts)
        if attempt > 0:
            if new_count == prev_count:
                # No new accounts found in this scroll
                no_new_count += 1
                if no_new_count >= 5:
                    logger.info(
                        f"No new accounts after {no_new_count} scrolls — stopping at {new_count}"
                    )
                    break
            else:
                no_new_count = 0
                logger.info(f"Scroll {attempt}: collected {new_count} accounts so far")
        prev_count = new_count

        # Scroll down in the dialog
        dialog = await page.query_selector('div[role="dialog"]')
        if dialog:
            await page.evaluate(
                """(dialog) => {
                    const scrollable = dialog.querySelector(
                        'div[style*="overflow"]'
                    ) || dialog.querySelector('ul')?.parentElement || dialog;
                    scrollable.scrollTop = scrollable.scrollHeight;
                }""",
                dialog,
            )
        await asyncio.sleep(1.5)

    return accounts[:count]


async def _unfollow_single_account(page: Page, username: str) -> Dict:
    """Navigate to a user's profile and unfollow them.

    Args:
        page: Authenticated Playwright page.
        username: The account to unfollow.

    Returns:
        Dict with 'username' and 'status' keys.
    """
    try:
        # Navigate to the user's profile
        profile_url = f"{INSTAGRAM_URL}{username}/"
        await page.goto(profile_url, wait_until="domcontentloaded", timeout=15000)
        await asyncio.sleep(2)

        # Find the "Following" button (indicates we're currently following)
        following_button = await _find_following_button(page)
        if not following_button:
            logger.warning(
                f"Could not find 'Following' button for @{username} — "
                "may already be unfollowed",
                extra={"action": "unfollow", "username": username, "status": "FAILED"},
            )
            return {"username": username, "status": "FAILED"}

        # Click "Following" to open the unfollow confirmation
        await following_button.click()
        await asyncio.sleep(1)

        # Click "Unfollow" in the confirmation dialog
        unfollow_confirmed = await _confirm_unfollow(page)

        if unfollow_confirmed:
            logger.info(
                f"Successfully unfollowed @{username}",
                extra={
                    "action": "unfollow",
                    "username": username,
                    "status": "SUCCESS",
                },
            )
            return {"username": username, "status": "SUCCESS"}

        # Check if we got rate limited
        if await _is_action_blocked(page):
            return {"username": username, "status": "RATE_LIMITED"}

        return {"username": username, "status": "FAILED"}

    except PlaywrightTimeout:
        logger.error(
            f"Timeout unfollowing @{username}",
            extra={"action": "unfollow", "username": username, "status": "FAILED"},
        )
        return {"username": username, "status": "FAILED"}
    except Exception as e:
        logger.error(
            f"Error unfollowing @{username}: {e}",
            extra={"action": "unfollow", "username": username, "status": "FAILED"},
        )
        return {"username": username, "status": "FAILED"}


async def _find_following_button(page: Page):
    """Find the 'Following' button on a user's profile page."""
    selectors = [
        'button:has-text("Following")',
        'div[role="button"]:has-text("Following")',
        'button:has-text("Requested")',  # For pending follow requests
    ]
    for selector in selectors:
        try:
            btn = await page.wait_for_selector(selector, timeout=5000)
            if btn:
                text = await btn.inner_text()
                if "following" in text.lower() or "requested" in text.lower():
                    return btn
        except PlaywrightTimeout:
            continue
    return None


async def _confirm_unfollow(page: Page) -> bool:
    """Click 'Unfollow' in the confirmation dialog.

    Returns True if the unfollow was confirmed.
    """
    try:
        unfollow_selectors = [
            'button:has-text("Unfollow")',
            'div[role="dialog"] button:has-text("Unfollow")',
            'button[tabindex="0"]:has-text("Unfollow")',
        ]
        for selector in unfollow_selectors:
            try:
                btn = await page.wait_for_selector(selector, timeout=5000)
                if btn:
                    await btn.click()
                    await asyncio.sleep(1)
                    return True
            except PlaywrightTimeout:
                continue
        return False
    except Exception as e:
        logger.error(f"Error confirming unfollow: {e}")
        return False


async def _is_action_blocked(page: Page) -> bool:
    """Check if Instagram has blocked the current action.

    Looks for the 'Action Blocked' or 'Try Again Later' messages.
    """
    block_indicators = [
        'text="Action Blocked"',
        'text="Try Again Later"',
        'text="action was blocked"',
        'text="temporarily blocked"',
        'h3:has-text("Action Blocked")',
    ]
    for selector in block_indicators:
        try:
            element = await page.wait_for_selector(selector, timeout=2000)
            if element:
                logger.warning(
                    "Instagram action blocked detected!",
                    extra={"action": "block_detection", "status": "blocked"},
                )
                # Try to dismiss the dialog
                try:
                    ok_btn = await page.wait_for_selector(
                        'button:has-text("OK"), button:has-text("Tell us")',
                        timeout=3000,
                    )
                    if ok_btn:
                        await ok_btn.click()
                except PlaywrightTimeout:
                    pass
                return True
        except PlaywrightTimeout:
            continue
    return False
