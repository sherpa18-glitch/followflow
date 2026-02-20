"""Tests for the FastAPI application endpoints.

Mocks the Telegram bot startup to test endpoints in isolation.
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
    """Root endpoint should return service info with all endpoints."""
    mock_bot_class.return_value = MagicMock()
    mock_build_app.return_value = MagicMock()

    from app.main import app
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["service"] == "FollowFlow"
    assert "endpoints" in data
    assert "trigger_follow" in data["endpoints"]
    assert "trigger_unfollow" in data["endpoints"]
    assert "cancel" in data["endpoints"]


@patch("app.main.start_polling", new_callable=AsyncMock)
@patch("app.main.build_telegram_app")
@patch("app.main.FollowFlowBot")
def test_status_endpoint(mock_bot_class, mock_build_app, mock_start_polling):
    """Status endpoint should return workflow state with progress."""
    mock_bot_class.return_value = MagicMock()
    mock_build_app.return_value = MagicMock()

    from app.main import app
    client = TestClient(app)
    response = client.get("/status")
    assert response.status_code == 200
    data = response.json()
    assert "state" in data
    assert "progress" in data


@patch("app.main.start_polling", new_callable=AsyncMock)
@patch("app.main.build_telegram_app")
@patch("app.main.FollowFlowBot")
def test_trigger_follow_endpoint(mock_bot_class, mock_build_app, mock_start_polling):
    """Trigger-follow endpoint should accept POST."""
    mock_bot_class.return_value = MagicMock()
    mock_build_app.return_value = MagicMock()

    from app.main import app
    client = TestClient(app)
    response = client.post("/trigger-follow")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data or "error" in data


@patch("app.main.start_polling", new_callable=AsyncMock)
@patch("app.main.build_telegram_app")
@patch("app.main.FollowFlowBot")
def test_trigger_unfollow_endpoint(mock_bot_class, mock_build_app, mock_start_polling):
    """Trigger-unfollow endpoint should accept POST."""
    mock_bot_class.return_value = MagicMock()
    mock_build_app.return_value = MagicMock()

    from app.main import app
    client = TestClient(app)
    response = client.post("/trigger-unfollow")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data or "error" in data


@patch("app.main.start_polling", new_callable=AsyncMock)
@patch("app.main.build_telegram_app")
@patch("app.main.FollowFlowBot")
def test_cancel_endpoint(mock_bot_class, mock_build_app, mock_start_polling):
    """Cancel endpoint should accept POST."""
    mock_bot_class.return_value = MagicMock()
    mock_build_app.return_value = MagicMock()

    from app.main import app
    client = TestClient(app)
    response = client.post("/cancel")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data


@patch("app.main.start_polling", new_callable=AsyncMock)
@patch("app.main.build_telegram_app")
@patch("app.main.FollowFlowBot")
def test_exports_endpoint(mock_bot_class, mock_build_app, mock_start_polling):
    """Exports endpoint should return list of CSV files."""
    mock_bot_class.return_value = MagicMock()
    mock_build_app.return_value = MagicMock()

    from app.main import app
    client = TestClient(app)
    response = client.get("/exports")
    assert response.status_code == 200
    data = response.json()
    assert "exports" in data
