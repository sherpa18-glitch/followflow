"""Rate limiting utilities with randomized delays."""

import asyncio
import random

from app.utils.logger import get_logger

logger = get_logger("rate_limiter")


async def random_delay(min_seconds: int, max_seconds: int) -> float:
    """Sleep for a random duration between min and max seconds.

    The randomized delay helps mimic human behavior and avoid
    Instagram rate-limit detection.

    Args:
        min_seconds: Minimum delay in seconds.
        max_seconds: Maximum delay in seconds.

    Returns:
        The actual number of seconds slept.
    """
    delay = random.uniform(min_seconds, max_seconds)
    logger.info(
        f"Rate limit delay: {delay:.1f}s",
        extra={"action": "delay", "detail": f"{delay:.1f}s"},
    )
    await asyncio.sleep(delay)
    return delay


async def cooldown(min_minutes: int, max_minutes: int) -> float:
    """Longer cooldown period between major action batches.

    Used between the unfollow and follow phases to avoid
    burst-pattern detection.

    Args:
        min_minutes: Minimum cooldown in minutes.
        max_minutes: Maximum cooldown in minutes.

    Returns:
        The actual number of minutes waited.
    """
    delay_minutes = random.uniform(min_minutes, max_minutes)
    delay_seconds = delay_minutes * 60
    logger.info(
        f"Cooldown: {delay_minutes:.1f} minutes",
        extra={"action": "cooldown", "detail": f"{delay_minutes:.1f}min"},
    )
    await asyncio.sleep(delay_seconds)
    return delay_minutes
