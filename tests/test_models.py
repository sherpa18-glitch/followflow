"""Tests for database models."""

import json
from datetime import datetime

from app.models import ActionLog, ApprovalLog, Blocklist


def test_create_action_log(db_session):
    """ActionLog should persist with all fields."""
    log = ActionLog(
        action_type="UNFOLLOW",
        target_username="old_account_1",
        target_follower_count=500,
        target_following_count=3500,
        target_region="NA",
        region_confidence="HIGH",
        status="SUCCESS",
        daily_batch_id="batch-001",
    )
    db_session.add(log)
    db_session.commit()

    result = db_session.query(ActionLog).first()
    assert result is not None
    assert result.action_type == "UNFOLLOW"
    assert result.target_username == "old_account_1"
    assert result.target_follower_count == 500
    assert result.target_following_count == 3500
    assert result.target_region == "NA"
    assert result.region_confidence == "HIGH"
    assert result.status == "SUCCESS"
    assert result.daily_batch_id == "batch-001"
    assert result.id is not None


def test_create_follow_action_log(db_session):
    """ActionLog should work for FOLLOW actions with region UNKNOWN."""
    log = ActionLog(
        action_type="FOLLOW",
        target_username="puppy_fan_kr",
        target_follower_count=1120,
        target_following_count=4200,
        target_region="UNKNOWN",
        region_confidence="UNKNOWN",
        status="SUCCESS",
        daily_batch_id="batch-002",
    )
    db_session.add(log)
    db_session.commit()

    result = db_session.query(ActionLog).first()
    assert result.action_type == "FOLLOW"
    assert result.region_confidence == "UNKNOWN"


def test_create_approval_log(db_session):
    """ApprovalLog should persist with account list JSON."""
    accounts = ["@user1", "@user2", "@user3"]
    log = ApprovalLog(
        action_type="UNFOLLOW_BATCH",
        response="APPROVED",
        responded_at=datetime.utcnow(),
        account_list_json=json.dumps(accounts),
    )
    db_session.add(log)
    db_session.commit()

    result = db_session.query(ApprovalLog).first()
    assert result is not None
    assert result.action_type == "UNFOLLOW_BATCH"
    assert result.response == "APPROVED"
    parsed = json.loads(result.account_list_json)
    assert len(parsed) == 3
    assert parsed[0] == "@user1"


def test_approval_log_timeout(db_session):
    """ApprovalLog should handle TIMEOUT response (no responded_at)."""
    log = ApprovalLog(
        action_type="FOLLOW_BATCH",
        response="TIMEOUT",
        responded_at=None,
        account_list_json=json.dumps(["@a", "@b"]),
    )
    db_session.add(log)
    db_session.commit()

    result = db_session.query(ApprovalLog).first()
    assert result.response == "TIMEOUT"
    assert result.responded_at is None


def test_create_blocklist_entry(db_session):
    """Blocklist should persist with unique username constraint."""
    entry = Blocklist(
        username="unfollowed_user",
        reason="PRUNED_OLD_FOLLOW",
    )
    db_session.add(entry)
    db_session.commit()

    result = db_session.query(Blocklist).first()
    assert result is not None
    assert result.username == "unfollowed_user"
    assert result.reason == "PRUNED_OLD_FOLLOW"
    assert result.unfollowed_at is not None


def test_blocklist_unique_username(db_session):
    """Blocklist should reject duplicate usernames."""
    entry1 = Blocklist(username="same_user", reason="PRUNED_OLD_FOLLOW")
    db_session.add(entry1)
    db_session.commit()

    entry2 = Blocklist(username="same_user", reason="PRUNED_OLD_FOLLOW")
    db_session.add(entry2)
    try:
        db_session.commit()
        assert False, "Should have raised IntegrityError"
    except Exception:
        db_session.rollback()


def test_action_log_repr():
    """ActionLog __repr__ should be readable."""
    log = ActionLog(
        action_type="UNFOLLOW",
        target_username="test_user",
        status="SUCCESS",
        daily_batch_id="batch-001",
    )
    assert "UNFOLLOW" in repr(log)
    assert "@test_user" in repr(log)
