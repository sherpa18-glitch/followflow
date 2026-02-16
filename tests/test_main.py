"""Tests for the FastAPI application endpoints.

Mocks the Telegram bot and scheduler startup to test
endpoints in isolation.
"""

from unittest.mock import patch, AsyncMock, MagicMock

from fastapi.testclient import TestClient


@patch("app.main.start_polling", new_callable=AsyncMock)
@patch("app.main.build_telegram_app")
@patch("app.main.FollowFlowBot")
def test_health_endpoint(mock_bot_class, mock_build_app, mock_start_polling):
    """Health endpoint should return 200 with status ok."""
    mock_bot_class.return_value = MagicMock()
    mock_build_app.return_value = MagicMock()

    from app.main import app
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "followflow"
    assert "version" in data


@patch("app.main.start_polling", new_callable=AsyncMock)
@patch("app.main.build_telegram_app")
@patch("app.main.FollowFlowBot")
def test_root_endpoint(mock_bot_class, mock_build_app, mock_start_polling):
    """Root endpoint should return service info."""
    mock_bot_class.return_value = MagicMock()
    mock_build_app.return_value = MagicMock()

    from app.main import app
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["service"] == "FollowFlow"
    assert "docs" in data


@patch("app.main.start_polling", new_callable=AsyncMock)
@patch("app.main.build_telegram_app")
@patch("app.main.FollowFlowBot")
def test_status_endpoint(mock_bot_class, mock_build_app, mock_start_polling):
    """Status endpoint should return workflow state."""
    mock_bot_class.return_value = MagicMock()
    mock_build_app.return_value = MagicMock()

    from app.main import app
    client = TestClient(app)
    response = client.get("/status")
    assert response.status_code == 200
    data = response.json()
    assert "state" in data


@patch("app.main.start_polling", new_callable=AsyncMock)
@patch("app.main.build_telegram_app")
@patch("app.main.FollowFlowBot")
def test_trigger_endpoint(mock_bot_class, mock_build_app, mock_start_polling):
    """Trigger endpoint should accept POST and return a response."""
    mock_bot_class.return_value = MagicMock()
    mock_build_app.return_value = MagicMock()

    from app.main import app
    client = TestClient(app)
    response = client.post("/trigger")
    assert response.status_code == 200
    data = response.json()
    # Could be "triggered" or "error" depending on bot init state
    assert "status" in data or "error" in data
