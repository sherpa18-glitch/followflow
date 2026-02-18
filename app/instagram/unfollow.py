"""Instagram unfollow action module.

Fetches the Following list sorted by "Date followed: earliest" using
Instagram's private API, then unfollows accounts via the browser with
rate-limited delays between each action.

The API approach is faster and more reliable than scraping the
Following modal, and supports chronological sorting that the desktop
web UI does not expose.
"""

import asyncio
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, TYPE_CHECKING

import httpx
from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from app.utils.logger import get_logger
from app.utils.rate_limiter import random_delay

if TYPE_CHECKING:
    from app.instagram.browser import InstagramBrowser

logger = get_logger("unfollow")

INSTAGRAM_URL = "https://www.instagram.com/"
INSTAGRAM_API = "https://i.instagram.com/api/v1"

# Time to pause when Instagram blocks an action (seconds)
RATE_LIMIT_PAUSE = 900  # 15 minutes

# Android user-agent for the private API (required by i.instagram.com)
_API_USER_AGENT = (
    "Instagram 275.0.0.27.98 Android "
    "(33/13; 420dpi; 1080x2400; samsung; SM-G991B; "
    "o1s; exynos2100; en_US; 458229258)"
)
_API_APP_ID = "936619743392459"

COOKIES_PATH = Path("session_cookies.json")


async def get_following_list_sorted(
    page: Page,
    username: str,
    count: int = 100,
    browser: Optional["InstagramBrowser"] = None,
) -> List[Dict[str, str]]:
    """Fetch the Following list, sorted by oldest followed first.

    Uses Instagram's private API with ``order=date_followed_earliest``
    as the primary method.  Falls back to browser-based scraping of
    the Following modal (unsorted) if the API call fails.

    Args:
        page: Authenticated Playwright page (used as fallback).
        username: The authenticated user's Instagram handle.
        count: Number of accounts to collect from the top.
        browser: Optional InstagramBrowser (for cookie access / fallback).

    Returns:
        List of dicts with keys: 'username', 'full_name' (if available).
    """
    logger.info(
        f"Fetching following list for @{username}, target count: {count}",
        extra={"action": "get_following_list", "username": username},
    )

    # ── Primary: Instagram private API (sorted by oldest first) ──
    try:
        accounts = await _fetch_via_api(count)
        if accounts:
            logger.info(
                f"API: collected {len(accounts)} accounts sorted by oldest first",
                extra={"action": "get_following_list",
                       "detail": f"{len(accounts)} accounts (API)"},
            )
            return accounts
        logger.warning("API returned 0 accounts — falling back to browser")
    except Exception as e:
        logger.warning(f"API fetch failed ({e}) — falling back to browser")

    # ── Fallback: browser-based scraping (unsorted) ──
    return await _fetch_via_desktop(page, username, count)


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


async def _fetch_via_api(
    count: int,
) -> List[Dict[str, str]]:
    """Fetch the following list via Instagram's private API.

    Uses ``/api/v1/friendships/{user_id}/following/`` with
    ``order=date_followed_earliest`` to get the oldest followed
    accounts first.  Session cookies are read from the cookie file
    saved by the browser module.

    Args:
        count: Number of accounts to collect.

    Returns:
        List of dicts with 'username' and 'full_name' keys,
        ordered from oldest followed to newest.
    """
    if not COOKIES_PATH.exists():
        raise RuntimeError("No saved cookies — cannot call API")

    with open(COOKIES_PATH) as f:
        cookies_list = json.load(f)

    cookie_dict = {c["name"]: c["value"] for c in cookies_list}

    required = ("sessionid", "ds_user_id", "csrftoken")
    for key in required:
        if key not in cookie_dict:
            raise RuntimeError(f"Missing required cookie: {key}")

    user_id = cookie_dict["ds_user_id"]
    url = f"{INSTAGRAM_API}/friendships/{user_id}/following/"

    headers = {
        "User-Agent": _API_USER_AGENT,
        "X-CSRFToken": cookie_dict["csrftoken"],
        "X-IG-App-ID": _API_APP_ID,
    }
    api_cookies = {
        "sessionid": cookie_dict["sessionid"],
        "ds_user_id": user_id,
        "csrftoken": cookie_dict["csrftoken"],
    }

    all_users: List[Dict[str, str]] = []
    max_id: Optional[str] = None
    page_num = 0

    async with httpx.AsyncClient(timeout=15) as client:
        while len(all_users) < count:
            page_num += 1
            params: Dict = {
                "count": min(50, count - len(all_users)),
                "order": "date_followed_earliest",
            }
            if max_id:
                params["max_id"] = max_id

            resp = await client.get(
                url, headers=headers, cookies=api_cookies, params=params,
            )
            resp.raise_for_status()
            data = resp.json()

            users = data.get("users", [])
            if not users:
                break

            for u in users:
                if len(all_users) >= count:
                    break
                all_users.append({
                    "username": u.get("username", ""),
                    "full_name": u.get("full_name", ""),
                })

            logger.info(
                f"API page {page_num}: fetched {len(users)} users "
                f"(total: {len(all_users)})"
            )

            if not data.get("has_more", False):
                break
            max_id = str(data.get("next_max_id", ""))
            if not max_id:
                break

            # Small pause between API pages to be polite
            await asyncio.sleep(1)

    return all_users[:count]


async def _fetch_via_desktop(
    page: Page,
    username: str,
    count: int,
) -> List[Dict[str, str]]:
    """Desktop fallback — open Following modal without sort."""
    profile_url = f"{INSTAGRAM_URL}{username}/"
    try:
        await page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)
    except PlaywrightTimeout:
        logger.warning("Profile page load timed out, proceeding anyway")
    await asyncio.sleep(3)

    following_link = await _find_following_link(page)
    if not following_link:
        logger.error("Could not find 'Following' link on profile page")
        return []

    await following_link.dispatch_event("click")
    await asyncio.sleep(3)

    logger.info("Desktop mode — collecting in default order (no sort available)")
    await asyncio.sleep(2)

    accounts = await _scroll_and_collect_accounts(page, count)
    logger.info(
        f"Desktop: collected {len(accounts)} accounts from following list",
        extra={"action": "get_following_list", "detail": f"{len(accounts)} accounts (desktop)"},
    )
    return accounts


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

    This only works on mobile-emulated pages — the desktop web UI
    does **not** expose sort controls.

    Returns True if the sort was successfully applied.
    """
    try:
        # Look for the sort/filter button in the modal.
        # Only use selectors that specifically identify a sort control —
        # never use generic "last button in dialog" since that matches
        # "Following" (unfollow) buttons and corrupts the dialog.
        sort_selectors = [
            'svg[aria-label="Sort options"]',
            'button[aria-label="Sort"]',
            'button:has-text("Sort by")',
            'div[role="dialog"] svg[aria-label="Sort and filter"]',
            # Mobile web sometimes shows a small bar-sort icon
            'div[role="dialog"] [aria-label*="sort" i]',
            'div[role="dialog"] [aria-label*="filter" i]',
        ]

        sort_button = None
        for selector in sort_selectors:
            try:
                sort_button = await page.wait_for_selector(selector, timeout=3000)
                if sort_button:
                    logger.info(f"Found sort control via: {selector}")
                    break
            except PlaywrightTimeout:
                continue

        if not sort_button:
            logger.info("Sort button not found in Following modal")
            return False

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

        logger.warning("Sort menu opened but 'Earliest' option not found")
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
                if no_new_count >= 8:
                    logger.info(
                        f"No new accounts after {no_new_count} scrolls — stopping at {new_count}"
                    )
                    break
            else:
                no_new_count = 0
                logger.info(f"Scroll {attempt}: collected {new_count} accounts so far")
        prev_count = new_count

        # Scroll down in the dialog — use incremental scrolling so
        # Instagram's lazy loader has time to fetch more accounts.
        dialog = await page.query_selector('div[role="dialog"]')
        if dialog:
            await page.evaluate(
                """(dialog) => {
                    // Walk the dialog tree looking for the deepest scrollable div
                    function findScrollable(el) {
                        const children = el.querySelectorAll('div');
                        for (const child of children) {
                            if (child.scrollHeight > child.clientHeight + 10) {
                                // Check if this element is actually scrollable
                                const style = window.getComputedStyle(child);
                                const overflow = style.overflowY || style.overflow;
                                if (overflow === 'auto' || overflow === 'scroll' ||
                                    overflow === 'hidden') {
                                    return child;
                                }
                            }
                        }
                        return el;
                    }
                    const scrollable = findScrollable(dialog);
                    // Scroll by a large increment rather than jumping to bottom
                    scrollable.scrollTop += 1500;
                }""",
                dialog,
            )
        await asyncio.sleep(2)

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
        await asyncio.sleep(1)

        # Find the "Following" button (indicates we're currently following)
        following_button = await _find_following_button(page)
        if not following_button:
            logger.warning(
                f"Could not find 'Following' button for @{username} — "
                "may already be unfollowed",
                extra={"action": "unfollow", "username": username, "status": "FAILED"},
            )
            return {"username": username, "status": "FAILED"}

        # Click "Following" to open the unfollow menu
        await following_button.click()
        await asyncio.sleep(0.5)

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

        logger.warning(
            f"Unfollow confirmation failed for @{username} — "
            "could not find/click 'Unfollow' option",
            extra={"action": "unfollow", "username": username, "status": "FAILED"},
        )
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
            btn = await page.wait_for_selector(selector, timeout=3000)
            if btn:
                text = await btn.inner_text()
                if "following" in text.lower() or "requested" in text.lower():
                    return btn
        except PlaywrightTimeout:
            continue
    return None


async def _confirm_unfollow(page: Page) -> bool:
    """Click 'Unfollow' in the menu / confirmation dialog.

    Instagram's current desktop UI shows a menu after clicking the
    "Following" button.  The "Unfollow" option is a ``<span>`` inside
    the menu, not a ``<button>``.  We try both element types.

    Returns True if the unfollow was confirmed.
    """
    try:
        unfollow_selectors = [
            # Current Instagram desktop: Unfollow is a span in a menu
            'div[role="dialog"] >> text=Unfollow',
            'span:has-text("Unfollow")',
            # Fallback: older UI used a button
            'button:has-text("Unfollow")',
            'div[role="dialog"] button:has-text("Unfollow")',
        ]
        for selector in unfollow_selectors:
            try:
                el = await page.wait_for_selector(selector, timeout=2000)
                if el:
                    text = await el.inner_text()
                    if "unfollow" in text.lower():
                        await el.click()
                        await asyncio.sleep(0.5)
                        logger.debug(
                            f"Clicked unfollow via selector: {selector}"
                        )
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
