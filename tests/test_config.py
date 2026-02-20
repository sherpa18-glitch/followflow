"""Tests for application configuration."""

from app.config import Settings, get_settings


def test_settings_loads_from_env():
    """Settings should load values from environment variables."""
    settings = Settings()
    assert settings.instagram_username == "test_user"
    assert settings.instagram_password == "test_pass"
    assert settings.telegram_bot_token == "123456:ABC-DEF"
    assert settings.telegram_chat_id == "987654321"


def test_settings_defaults():
    """Settings should have sensible defaults for optional fields."""
    settings = Settings()
    assert settings.daily_schedule_time == "09:00"
    assert 1 <= settings.unfollow_batch_size <= 200
    assert 1 <= settings.follow_batch_size <= 200
    assert settings.approval_timeout_hours == 4
    assert settings.discovery_max_followers == 5000
    assert settings.discovery_min_following == 100
    assert settings.discovery_activity_days == 14


def test_settings_rate_limits():
    """Rate limit defaults should ensure min < max."""
    settings = Settings()
    assert settings.unfollow_delay_min < settings.unfollow_delay_max
    assert settings.follow_delay_min < settings.follow_delay_max
    assert settings.cooldown_minutes_min < settings.cooldown_minutes_max
