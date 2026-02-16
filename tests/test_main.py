"""Tests for the FastAPI application."""

from fastapi.testclient import TestClient


def test_health_endpoint():
    """Health endpoint should return 200 with status ok."""
    # Import here to use mocked env from conftest
    from app.main import app

    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "followflow"
    assert "version" in data


def test_root_endpoint():
    """Root endpoint should return service info."""
    from app.main import app

    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["service"] == "FollowFlow"
    assert "docs" in data
