"""FastAPI application entry point."""

from fastapi import FastAPI
from app.database import init_db

app = FastAPI(
    title="FollowFlow",
    description="Instagram growth automation agent with human-in-the-loop approval",
    version="0.1.0",
)


@app.on_event("startup")
def on_startup():
    """Initialize the database on first run."""
    init_db()


@app.get("/health")
def health_check():
    """Basic health check endpoint."""
    return {"status": "ok", "service": "followflow", "version": "0.1.0"}


@app.get("/")
def root():
    """Root endpoint with service info."""
    return {
        "service": "FollowFlow",
        "version": "0.1.0",
        "docs": "/docs",
    }
