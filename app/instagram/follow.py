"""Instagram follow action module.

Navigates to target account profiles and sends follow requests
with rate-limited delays between each action. Tracks whether
the follow was instant (public) or pending (private).
"""

import asyncio
import uuid
from datetime import datetime
from typing import List, Dict, Optional

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from app.utils.logger import get_logger
from app.utils.rate_limiter import random_delay

logger = get_logger("follow")

INSTAGRAM_URL = "https://www.instagram.com/"

# Time to pause when Instagram blocks an action (seconds)
RATE_LIMIT_PAUSE = 900  # 15 minutes


async def follow_accounts(
    page: Page,
    accounts: List[Dict],
    delay_min: int = 30,
    delay_max: int = 60,
    batch_id: Optional[str] = None,
) -> List[Dict]:
    """Send follow requests to a list of target accounts.

    Navigates to each account's profile and clicks the Follow
    button with rate-limited delays between each action.

    Args:
        page: Authenticated Playwright page.
        accounts: List of account dicts (must have 'username' key).
        delay_min: Minimum seconds between follows.
        delay_max: Maximum seconds between follows.
        batch_id: Unique ID for this batch (auto-generated if None).

    Returns:
        List of result dicts with keys: 'username', 'status',
        'follow_type' ('public'|'private'|None), 'timestamp', 'batch_id'.
    """
    if batch_id is None:
        batch_id = str(uuid.uuid4())

    results = []
    total = len(accounts)

    logger.info(
        f"Starting follow batch: {total} accounts, batch_id={batch_id}",
        extra={"action": "follow_batch", "batch_id": batch_id},
    )

    for i, account in enumerate(accounts):
        username = account["username"]
        logger.info(
            f"Following [{i+1}/{total}]: @{username}",
            extra={"action": "follow", "username": username, "batch_id": batch_id},
        )

        result = await _follow_single_account(page, username)
        result["batch_id"] = batch_id
        result["timestamp"] = datetime.utcnow().isoformat()

        # Carry forward account metadata for logging
        result["follower_count"] = account.get("follower_count")
        result["following_count"] = account.get("following_count")
        result["region"] = account.get("region")
        result["region_confidence"] = account.get("region_confidence")

        results.append(result)

        if result["status"] == "RATE_LIMITED":
            logger.warning(
                f"Rate limited at account {i+1}/{total}. "
                f"Pausing for {RATE_LIMIT_PAUSE // 60} minutes...",
                extra={
                    "action": "follow",
                    "username": username,
                    "status": "RATE_LIMITED",
                },
            )
            await asyncio.sleep(RATE_LIMIT_PAUSE)
            # Retry once after the pause
            retry_result = await _follow_single_account(page, username)
            if retry_result["status"] == "SUCCESS":
                results[-1] = {
                    **retry_result,
                    "batch_id": batch_id,
                    "timestamp": datetime.utcnow().isoformat(),
                    "follower_count": account.get("follower_count"),
                    "following_count": account.get("following_count"),
                    "region": account.get("region"),
                    "region_confidence": account.get("region_confidence"),
                }

        # Rate-limited delay before next follow (skip after last one)
        if i < total - 1:
            await random_delay(delay_min, delay_max)

    # Summary
    success_count = sum(1 for r in results if r["status"] == "SUCCESS")
    public_count = sum(
        1 for r in results
        if r["status"] == "SUCCESS" and r.get("follow_type") == "public"
    )
    private_count = sum(
        1 for r in results
        if r["status"] == "SUCCESS" and r.get("follow_type") == "private"
    )
    fail_count = total - success_count

    logger.info(
        f"Follow batch complete: {success_count} success "
        f"({public_count} public, {private_count} private), "
        f"{fail_count} failed",
        extra={
            "action": "follow_batch",
            "batch_id": batch_id,
            "detail": f"{success_count}/{total} success",
        },
    )

    return results


def get_follow_summary(results: List[Dict]) -> Dict:
    """Compute summary statistics from follow results.

    Args:
        results: List of follow result dicts.

    Returns:
        Dict with total_sent, public_count, private_count, fail_count.
    """
    total = len(results)
    success = [r for r in results if r["status"] == "SUCCESS"]
    public_count = sum(1 for r in success if r.get("follow_type") == "public")
    private_count = sum(1 for r in success if r.get("follow_type") == "private")
    fail_count = total - len(success)

    return {
        "total_sent": len(success),
        "public_count": public_count,
        "private_count": private_count,
        "fail_count": fail_count,
    }


# --- Private helpers ---


async def _follow_single_account(page: Page, username: str) -> Dict:
    """Navigate to a user's profile and click Follow.

    Args:
        page: Authenticated Playwright page.
        username: The account to follow.

    Returns:
        Dict with 'username', 'status', 'follow_type'.
    """
    try:
        profile_url = f"{INSTAGRAM_URL}{username}/"
        await page.goto(profile_url, wait_until="domcontentloaded", timeout=15000)
        await asyncio.sleep(2)

        # Check if profile exists
        not_found_selectors = [
            'text="Sorry, this page isn\'t available."',
            'h2:has-text("Sorry")',
        ]
        for selector in not_found_selectors:
            try:
                el = await page.wait_for_selector(selector, timeout=1500)
                if el:
                    return {
                        "username": username,
                        "status": "FAILED",
                        "follow_type": None,
                    }
            except PlaywrightTimeout:
                continue

        # Check if already following
        already_following = await _is_already_following(page)
        if already_following:
            logger.info(
                f"Already following @{username} — skipping",
                extra={"action": "follow", "username": username, "status": "SKIPPED"},
            )
            return {
                "username": username,
                "status": "FAILED",
                "follow_type": None,
            }

        # Find and click the Follow button
        follow_button = await _find_follow_button(page)
        if not follow_button:
            logger.warning(
                f"Could not find Follow button for @{username}",
                extra={"action": "follow", "username": username, "status": "FAILED"},
            )
            return {
                "username": username,
                "status": "FAILED",
                "follow_type": None,
            }

        await follow_button.click()
        await asyncio.sleep(2)

        # Check for action blocked
        if await _is_action_blocked(page):
            return {
                "username": username,
                "status": "RATE_LIMITED",
                "follow_type": None,
            }

        # Determine if it was public follow or private (pending)
        follow_type = await _determine_follow_type(page)

        logger.info(
            f"Successfully followed @{username} ({follow_type})",
            extra={
                "action": "follow",
                "username": username,
                "status": "SUCCESS",
                "detail": follow_type,
            },
        )

        return {
            "username": username,
            "status": "SUCCESS",
            "follow_type": follow_type,
        }

    except PlaywrightTimeout:
        logger.error(
            f"Timeout following @{username}",
            extra={"action": "follow", "username": username, "status": "FAILED"},
        )
        return {"username": username, "status": "FAILED", "follow_type": None}
    except Exception as e:
        logger.error(
            f"Error following @{username}: {e}",
            extra={"action": "follow", "username": username, "status": "FAILED"},
        )
        return {"username": username, "status": "FAILED", "follow_type": None}


async def _find_follow_button(page: Page):
    """Find the Follow button on a user's profile page."""
    selectors = [
        'button:has-text("Follow")',
        'div[role="button"]:has-text("Follow")',
    ]
    for selector in selectors:
        try:
            btn = await page.wait_for_selector(selector, timeout=5000)
            if btn:
                text = await btn.inner_text()
                # Make sure it says "Follow" and not "Following" or "Unfollow"
                if text.strip().lower() == "follow":
                    return btn
        except PlaywrightTimeout:
            continue
    return None


async def _is_already_following(page: Page) -> bool:
    """Check if we're already following this account."""
    selectors = [
        'button:has-text("Following")',
        'button:has-text("Requested")',
    ]
    for selector in selectors:
        try:
            btn = await page.wait_for_selector(selector, timeout=2000)
            if btn:
                text = await btn.inner_text()
                if text.strip().lower() in ("following", "requested"):
                    return True
        except PlaywrightTimeout:
            continue
    return False


async def _determine_follow_type(page: Page) -> str:
    """Determine if the follow resulted in 'public' or 'private' (pending).

    After clicking Follow:
    - If button now says "Following" → public (instant follow)
    - If button says "Requested" → private (pending approval)
    """
    try:
        # Check for "Requested" (private account)
        requested = await page.wait_for_selector(
            'button:has-text("Requested")',
            timeout=3000,
        )
        if requested:
            return "private"
    except PlaywrightTimeout:
        pass

    try:
        # Check for "Following" (public account)
        following = await page.wait_for_selector(
            'button:has-text("Following")',
            timeout=3000,
        )
        if following:
            return "public"
    except PlaywrightTimeout:
        pass

    return "public"  # Default assumption


async def _is_action_blocked(page: Page) -> bool:
    """Check if Instagram has blocked the follow action."""
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
