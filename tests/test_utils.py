"""Tests for utility modules (rate limiter, logger)."""

import asyncio
import json
import logging
import time
from unittest.mock import patch

import pytest

from app.utils.rate_limiter import random_delay, cooldown
from app.utils.logger import get_logger, JSONFormatter


class TestRandomDelay:
    """Tests for the random_delay function."""

    @pytest.mark.asyncio
    async def test_delay_within_range(self):
        """Delay should be between min and max seconds."""
        with patch("app.utils.rate_limiter.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await random_delay(1, 3)
            assert 1.0 <= result <= 3.0
            mock_sleep.assert_called_once()
            actual_delay = mock_sleep.call_args[0][0]
            assert 1.0 <= actual_delay <= 3.0

    @pytest.mark.asyncio
    async def test_delay_returns_float(self):
        """Delay should return the actual sleep duration as float."""
        with patch("app.utils.rate_limiter.asyncio.sleep", new_callable=AsyncMock):
            result = await random_delay(5, 10)
            assert isinstance(result, float)


class TestCooldown:
    """Tests for the cooldown function."""

    @pytest.mark.asyncio
    async def test_cooldown_converts_to_seconds(self):
        """Cooldown should sleep for minutes converted to seconds."""
        with patch("app.utils.rate_limiter.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await cooldown(1, 2)
            assert 1.0 <= result <= 2.0
            actual_seconds = mock_sleep.call_args[0][0]
            # Should be in seconds (minutes * 60)
            assert 60.0 <= actual_seconds <= 120.0


class TestLogger:
    """Tests for the JSON logger."""

    def test_get_logger_returns_logger(self):
        """get_logger should return a logging.Logger instance."""
        logger = get_logger("test_module")
        assert isinstance(logger, logging.Logger)
        assert logger.name == "test_module"

    def test_get_logger_has_handler(self):
        """Logger should have at least one handler."""
        logger = get_logger("test_handler")
        assert len(logger.handlers) > 0

    def test_json_formatter_output(self):
        """JSONFormatter should produce valid JSON."""
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test message",
            args=None,
            exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["level"] == "INFO"
        assert parsed["message"] == "Test message"
        assert "timestamp" in parsed

    def test_json_formatter_with_extras(self):
        """JSONFormatter should include extra fields."""
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Action performed",
            args=None,
            exc_info=None,
        )
        record.action = "follow"
        record.username = "test_user"
        record.status = "success"

        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["action"] == "follow"
        assert parsed["username"] == "test_user"
        assert parsed["status"] == "success"


# Helper for mocking async sleep
class AsyncMock:
    """Simple async mock for asyncio.sleep."""

    def __init__(self, *args, **kwargs):
        self.call_count = 0
        self.call_args = None

    def __call__(self, *args, **kwargs):
        self.call_count += 1
        self.call_args = args, kwargs
        return self

    def __await__(self):
        yield
        return None

    def assert_called_once(self):
        assert self.call_count == 1
