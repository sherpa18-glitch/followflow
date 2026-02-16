"""Application configuration loaded from environment variables."""

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """FollowFlow configuration.

    All values are loaded from environment variables or a .env file.
    See .env.example for the full list of supported variables.
    """

    # Instagram credentials
    instagram_username: str = Field(..., description="Instagram account username")
    instagram_password: str = Field(..., description="Instagram account password")

    # Telegram bot
    telegram_bot_token: str = Field(..., description="Telegram bot API token")
    telegram_chat_id: str = Field(..., description="Telegram chat ID for notifications")

    # Schedule
    daily_schedule_time: str = Field(
        default="09:00",
        description="Daily trigger time in HH:MM 24h format",
    )

    # Batch sizes
    unfollow_batch_size: int = Field(default=100, ge=1, le=200)
    follow_batch_size: int = Field(default=100, ge=1, le=200)

    # Rate limiting (seconds)
    unfollow_delay_min: int = Field(default=25, ge=5)
    unfollow_delay_max: int = Field(default=45, ge=10)
    follow_delay_min: int = Field(default=30, ge=5)
    follow_delay_max: int = Field(default=60, ge=10)
    cooldown_minutes_min: int = Field(default=30, ge=5)
    cooldown_minutes_max: int = Field(default=60, ge=10)

    # Approval timeout
    approval_timeout_hours: int = Field(default=4, ge=1, le=24)

    # Discovery filtering
    discovery_max_followers: int = Field(
        default=2000,
        description="Max follower count for target accounts",
    )
    discovery_min_following: int = Field(
        default=3000,
        description="Min following count for target accounts",
    )
    discovery_activity_days: int = Field(
        default=7,
        description="Account must have been active within this many days",
    )

    # Database
    database_url: str = Field(default="sqlite:///./followflow.db")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
