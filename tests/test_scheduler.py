"""Tests for the daily workflow orchestration and scheduler.

Tests the workflow state machine, database logging, and
approval gating logic using mocked components.
"""

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.scheduler.jobs import (
    WorkflowState,
    daily_workflow,
    get_current_workflow_state,
    _get_blocklist_usernames,
    _get_recently_followed_usernames,
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

    def test_to_dict(self):
        ws = WorkflowState()
        ws.state = WorkflowState.EXECUTING_UNFOLLOWS
        ws.started_at = "2026-02-15T09:00:00"
        d = ws.to_dict()
        assert d["state"] == "EXECUTING_UNFOLLOWS"
        assert d["started_at"] == "2026-02-15T09:00:00"
        assert d["error_message"] is None

    def test_all_states_exist(self):
        assert WorkflowState.IDLE == "IDLE"
        assert WorkflowState.FETCHING_FOLLOWING == "FETCHING_FOLLOWING"
        assert WorkflowState.AWAITING_UNFOLLOW_APPROVAL == "AWAITING_UNFOLLOW_APPROVAL"
        assert WorkflowState.EXECUTING_UNFOLLOWS == "EXECUTING_UNFOLLOWS"
        assert WorkflowState.COOLDOWN == "COOLDOWN"
        assert WorkflowState.DISCOVERING_TARGETS == "DISCOVERING_TARGETS"
        assert WorkflowState.AWAITING_FOLLOW_APPROVAL == "AWAITING_FOLLOW_APPROVAL"
        assert WorkflowState.EXECUTING_FOLLOWS == "EXECUTING_FOLLOWS"
        assert WorkflowState.COMPLETE == "COMPLETE"
        assert WorkflowState.ERROR == "ERROR"


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


# --- Tests: Full Workflow ---

class TestDailyWorkflow:
    @pytest.mark.asyncio
    @patch("app.scheduler.jobs._get_db_session")
    @patch("app.scheduler.jobs.cooldown", new_callable=AsyncMock)
    @patch("app.scheduler.jobs.discover_target_accounts", new_callable=AsyncMock)
    @patch("app.scheduler.jobs.follow_accounts", new_callable=AsyncMock)
    @patch("app.scheduler.jobs.unfollow_accounts", new_callable=AsyncMock)
    @patch("app.scheduler.jobs.get_following_list_sorted", new_callable=AsyncMock)
    @patch("app.scheduler.jobs.ensure_authenticated", new_callable=AsyncMock)
    @patch("app.scheduler.jobs.InstagramBrowser")
    async def test_auth_failure_sends_error(
        self, MockBrowser, mock_auth, mock_get_following,
        mock_unfollow, mock_follow, mock_discover,
        mock_cooldown, mock_db,
    ):
        """Should send error notification if auth fails."""
        mock_browser = AsyncMock()
        MockBrowser.return_value = mock_browser
        mock_auth.return_value = False  # Auth fails

        mock_bot = AsyncMock()
        mock_bot.send_error_notification = AsyncMock()

        await daily_workflow(mock_bot)

        mock_bot.send_error_notification.assert_called_once()
        call_args = mock_bot.send_error_notification.call_args[0][0]
        assert "authentication failed" in call_args.lower()

    @pytest.mark.asyncio
    @patch("app.scheduler.jobs._get_db_session")
    @patch("app.scheduler.jobs._get_blocklist_usernames", return_value=set())
    @patch("app.scheduler.jobs._get_recently_followed_usernames", return_value=set())
    @patch("app.scheduler.jobs.cooldown", new_callable=AsyncMock)
    @patch("app.scheduler.jobs.discover_target_accounts", new_callable=AsyncMock)
    @patch("app.scheduler.jobs.follow_accounts", new_callable=AsyncMock)
    @patch("app.scheduler.jobs.unfollow_accounts", new_callable=AsyncMock)
    @patch("app.scheduler.jobs.get_following_list_sorted", new_callable=AsyncMock)
    @patch("app.scheduler.jobs.ensure_authenticated", new_callable=AsyncMock)
    @patch("app.scheduler.jobs.InstagramBrowser")
    async def test_unfollow_denied_skips(
        self, MockBrowser, mock_auth, mock_get_following,
        mock_unfollow, mock_follow, mock_discover,
        mock_cooldown, mock_blocklist, mock_followed, mock_db,
    ):
        """If user denies unfollow, it should skip to follow phase."""
        mock_browser = AsyncMock()
        mock_browser.get_page = AsyncMock(return_value=AsyncMock())
        MockBrowser.return_value = mock_browser
        mock_auth.return_value = True

        mock_get_following.return_value = [
            {"username": "user1"}, {"username": "user2"},
        ]
        mock_discover.return_value = []  # No targets found

        # Mock DB session
        mock_session = MagicMock()
        mock_session.add = MagicMock()
        mock_session.commit = MagicMock()
        mock_session.close = MagicMock()
        mock_session.query = MagicMock()
        mock_db.return_value = mock_session

        mock_bot = AsyncMock()
        mock_bot.send_unfollow_approval_request = AsyncMock()
        mock_bot.wait_for_approval = AsyncMock(return_value="DENIED")
        mock_bot.send_follow_approval_request = AsyncMock()

        await daily_workflow(mock_bot)

        # Unfollow should NOT have been called
        mock_unfollow.assert_not_called()
        # But cooldown should still happen
        mock_cooldown.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.scheduler.jobs._get_db_session")
    @patch("app.scheduler.jobs._get_blocklist_usernames", return_value=set())
    @patch("app.scheduler.jobs._get_recently_followed_usernames", return_value=set())
    @patch("app.scheduler.jobs.cooldown", new_callable=AsyncMock)
    @patch("app.scheduler.jobs.discover_target_accounts", new_callable=AsyncMock)
    @patch("app.scheduler.jobs.follow_accounts", new_callable=AsyncMock)
    @patch("app.scheduler.jobs.unfollow_accounts", new_callable=AsyncMock)
    @patch("app.scheduler.jobs.get_following_list_sorted", new_callable=AsyncMock)
    @patch("app.scheduler.jobs.ensure_authenticated", new_callable=AsyncMock)
    @patch("app.scheduler.jobs.InstagramBrowser")
    async def test_full_approved_workflow(
        self, MockBrowser, mock_auth, mock_get_following,
        mock_unfollow, mock_follow, mock_discover,
        mock_cooldown, mock_blocklist, mock_followed, mock_db,
    ):
        """Full workflow with both phases approved."""
        mock_browser = AsyncMock()
        mock_browser.get_page = AsyncMock(return_value=AsyncMock())
        MockBrowser.return_value = mock_browser
        mock_auth.return_value = True

        mock_get_following.return_value = [
            {"username": "old1"}, {"username": "old2"},
        ]
        mock_unfollow.return_value = [
            {"username": "old1", "status": "SUCCESS"},
            {"username": "old2", "status": "SUCCESS"},
        ]
        mock_discover.return_value = [
            {
                "username": "target1", "follower_count": 800,
                "following_count": 4000, "region": "NA",
                "region_confidence": "HIGH",
            },
        ]
        mock_follow.return_value = [
            {
                "username": "target1", "status": "SUCCESS",
                "follow_type": "public", "follower_count": 800,
                "following_count": 4000, "region": "NA",
                "region_confidence": "HIGH",
            },
        ]

        # Mock DB session
        mock_session = MagicMock()
        mock_session.add = MagicMock()
        mock_session.commit = MagicMock()
        mock_session.close = MagicMock()
        mock_session.query = MagicMock()
        mock_db.return_value = mock_session

        mock_bot = AsyncMock()
        mock_bot.send_unfollow_approval_request = AsyncMock()
        mock_bot.send_unfollow_complete = AsyncMock()
        mock_bot.send_follow_approval_request = AsyncMock()
        mock_bot.send_follow_complete = AsyncMock()
        mock_bot.wait_for_approval = AsyncMock(return_value="APPROVED")

        await daily_workflow(mock_bot)

        # All 4 notifications should have been sent
        mock_bot.send_unfollow_approval_request.assert_called_once()
        mock_bot.send_unfollow_complete.assert_called_once()
        mock_bot.send_follow_approval_request.assert_called_once()
        mock_bot.send_follow_complete.assert_called_once()

        # Both action functions should have been called
        mock_unfollow.assert_called_once()
        mock_follow.assert_called_once()

        # Cooldown should have happened between phases
        mock_cooldown.assert_called_once()


# --- Tests: get_current_workflow_state ---

class TestGetWorkflowState:
    def test_returns_dict(self):
        state = get_current_workflow_state()
        assert isinstance(state, dict)
        assert "state" in state
