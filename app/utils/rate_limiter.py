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


async def retry_with_backoff(
    coro_factory,
    max_retries: int = 3,
    base_delay: float = 5.0,
    max_delay: float = 120.0,
    description: str = "operation",
):
    """Retry an async operation with exponential backoff.

    Args:
        coro_factory: A callable that returns a new coroutine each call.
        max_retries: Maximum number of retry attempts.
        base_delay: Initial delay in seconds before first retry.
        max_delay: Maximum delay cap in seconds.
        description: Human-readable name of the operation for logging.

    Returns:
        The result of the successful coroutine call.

    Raises:
        The last exception if all retries are exhausted.
    """
    last_exception = None

    for attempt in range(max_retries + 1):
        try:
            return await coro_factory()
        except Exception as e:
            last_exception = e
            if attempt == max_retries:
                logger.error(
                    f"{description} failed after {max_retries + 1} attempts: {e}",
                    extra={"action": "retry_exhausted", "detail": description},
                )
                raise

            delay = min(base_delay * (2 ** attempt) + random.uniform(0, 2), max_delay)
            logger.warning(
                f"{description} failed (attempt {attempt + 1}/{max_retries + 1}): {e}. "
                f"Retrying in {delay:.1f}s...",
                extra={"action": "retry", "detail": description},
            )
            await asyncio.sleep(delay)
