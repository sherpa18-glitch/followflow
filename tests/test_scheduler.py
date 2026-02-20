"""Tests for workflow orchestration.

Tests the workflow state machine, cancellation, database logging,
and approval gating logic using mocked components.
"""

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.scheduler.jobs import (
    WorkflowState,
    get_current_workflow_state,
    cancel_current_workflow,
    _get_blocklist_usernames,
    _get_recently_followed_usernames,
    _generate_csv,
)
from app.models import ActionLog, ApprovalLog, Blocklist


# --- Tests: WorkflowState ---

class TestWorkflowState:
    def test_initial_state(self):
        ws = WorkflowState()
        assert ws.state == WorkflowState.IDLE
        assert ws.started_at is None
        assert ws.completed_at is None
        assert ws.unfollow_results is None
        assert ws.follow_results is None
        assert ws.error_message is None
        assert ws.workflow_type is None
        assert ws.batch_id is None
        assert ws.csv_path is None
        assert ws.total_target == 0
        assert ws.processed == 0
        assert ws.success_count == 0
        assert ws.fail_count == 0
        assert ws.errors == []
        assert not ws.is_cancelled

    def test_to_dict(self):
        ws = WorkflowState()
        ws.state = WorkflowState.EXECUTING_UNFOLLOWS
        ws.started_at = "2026-02-15T09:00:00"
        ws.workflow_type = "unfollow"
        ws.total_target = 50
        ws.processed = 10
        ws.success_count = 9
        ws.fail_count = 1
        d = ws.to_dict()
        assert d["state"] == "EXECUTING_UNFOLLOWS"
        assert d["started_at"] == "2026-02-15T09:00:00"
        assert d["workflow_type"] == "unfollow"
        assert d["progress"]["total"] == 50
        assert d["progress"]["processed"] == 10
        assert d["progress"]["success"] == 9
        assert d["progress"]["failed"] == 1
        assert d["error_message"] is None

    def test_all_states_exist(self):
        assert WorkflowState.IDLE == "IDLE"
        assert WorkflowState.FETCHING_FOLLOWING == "FETCHING_FOLLOWING"
        assert WorkflowState.AWAITING_UNFOLLOW_APPROVAL == "AWAITING_UNFOLLOW_APPROVAL"
        assert WorkflowState.EXECUTING_UNFOLLOWS == "EXECUTING_UNFOLLOWS"
        assert WorkflowState.DISCOVERING_TARGETS == "DISCOVERING_TARGETS"
        assert WorkflowState.AWAITING_FOLLOW_APPROVAL == "AWAITING_FOLLOW_APPROVAL"
        assert WorkflowState.EXECUTING_FOLLOWS == "EXECUTING_FOLLOWS"
        assert WorkflowState.CANCELLED == "CANCELLED"
        assert WorkflowState.COMPLETE == "COMPLETE"
        assert WorkflowState.ERROR == "ERROR"

    def test_cancel(self):
        ws = WorkflowState()
        ws.state = WorkflowState.EXECUTING_FOLLOWS
        assert not ws.is_cancelled
        ws.cancel()
        assert ws.is_cancelled
        assert ws.state == WorkflowState.CANCELLED


# --- Tests: Cancel workflow ---

class TestCancelWorkflow:
    def test_cancel_idle_returns_no_workflow(self):
        """Can't cancel when idle."""
        import app.scheduler.jobs as jobs_mod
        jobs_mod.current_workflow = WorkflowState()
        result = cancel_current_workflow()
        assert result["status"] == "no_workflow"

    def test_cancel_running_returns_cancelling(self):
        """Cancel during execution should return cancelling."""
        import app.scheduler.jobs as jobs_mod
        jobs_mod.current_workflow = WorkflowState()
        jobs_mod.current_workflow.state = WorkflowState.EXECUTING_FOLLOWS
        result = cancel_current_workflow()
        assert result["status"] == "cancelling"
        assert jobs_mod.current_workflow.is_cancelled


# --- Tests: Database helpers ---

class TestDatabaseHelpers:
    def test_get_blocklist_usernames(self, db_session):
        db_session.add(Blocklist(username="blocked1", reason="PRUNED_OLD_FOLLOW"))
        db_session.add(Blocklist(username="blocked2", reason="PRUNED_OLD_FOLLOW"))
        db_session.commit()

        result = _get_blocklist_usernames(db_session)
        assert result == {"blocked1", "blocked2"}

    def test_get_blocklist_empty(self, db_session):
        result = _get_blocklist_usernames(db_session)
        assert result == set()

    def test_get_recently_followed(self, db_session):
        db_session.add(ActionLog(
            action_type="FOLLOW",
            target_username="followed1",
            status="SUCCESS",
            daily_batch_id="batch-1",
        ))
        db_session.add(ActionLog(
            action_type="FOLLOW",
            target_username="followed2",
            status="SUCCESS",
            daily_batch_id="batch-1",
        ))
        db_session.add(ActionLog(
            action_type="FOLLOW",
            target_username="failed_follow",
            status="FAILED",
            daily_batch_id="batch-1",
        ))
        db_session.commit()

        result = _get_recently_followed_usernames(db_session)
        assert "followed1" in result
        assert "followed2" in result
        assert "failed_follow" not in result

    def test_get_recently_followed_empty(self, db_session):
        result = _get_recently_followed_usernames(db_session)
        assert result == set()


# --- Tests: CSV Export ---

class TestCSVExport:
    def test_generate_csv_creates_file(self, tmp_path):
        """CSV export should create a file with correct headers and rows."""
        import app.scheduler.jobs as jobs_mod
        original_dir = jobs_mod.EXPORT_DIR
        jobs_mod.EXPORT_DIR = tmp_path

        results = [
            {
                "username": "user1",
                "timestamp": "2026-02-19T10:00:00",
                "status": "SUCCESS",
                "region": "NA",
                "category": "dogs",
                "follower_count": 500,
                "following_count": 200,
                "follow_type": "public",
            },
            {
                "username": "user2",
                "timestamp": "2026-02-19T10:01:00",
                "status": "FAILED",
                "region": "KR",
                "category": "pets",
                "follower_count": 1000,
                "following_count": 300,
                "follow_type": None,
            },
        ]

        csv_path = _generate_csv(results, "follow", "test-batch-id")

        jobs_mod.EXPORT_DIR = original_dir

        import csv
        from pathlib import Path
        path = Path(csv_path)
        assert path.exists()

        with open(path) as f:
            reader = csv.reader(f)
            headers = next(reader)
            assert "username" in headers
            assert "category" in headers
            assert "region" in headers
            rows = list(reader)
            assert len(rows) == 2
            assert rows[0][0] == "user1"


# --- Tests: get_current_workflow_state ---

class TestGetWorkflowState:
    def test_returns_dict(self):
        state = get_current_workflow_state()
        assert isinstance(state, dict)
        assert "state" in state
        assert "progress" in state

    def test_includes_progress(self):
        state = get_current_workflow_state()
        progress = state["progress"]
        assert "total" in progress
        assert "processed" in progress
        assert "success" in progress
        assert "failed" in progress
