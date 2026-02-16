"""SQLAlchemy ORM models for FollowFlow."""

import uuid
from datetime import datetime

from sqlalchemy import Column, String, Integer, DateTime, Enum, Text
from sqlalchemy.sql import func

from app.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class ActionLog(Base):
    """Log of every individual follow / unfollow action."""

    __tablename__ = "action_logs"

    id = Column(String(36), primary_key=True, default=_uuid)
    action_type = Column(
        Enum("FOLLOW", "UNFOLLOW", name="action_type_enum"),
        nullable=False,
    )
    target_username = Column(String(255), nullable=False, index=True)
    target_follower_count = Column(Integer, nullable=True)
    target_following_count = Column(Integer, nullable=True)
    target_region = Column(
        String(10),
        nullable=True,
        comment="Detected region: NA, KR, JP, EU, AU, or UNKNOWN",
    )
    region_confidence = Column(
        Enum("HIGH", "MEDIUM", "UNKNOWN", name="region_confidence_enum"),
        nullable=True,
    )
    status = Column(
        Enum("SUCCESS", "FAILED", "RATE_LIMITED", name="action_status_enum"),
        nullable=False,
    )
    executed_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        server_default=func.now(),
    )
    daily_batch_id = Column(
        String(36),
        nullable=False,
        index=True,
        comment="Groups all actions in a single daily run",
    )

    def __repr__(self) -> str:
        return (
            f"<ActionLog {self.action_type} @{self.target_username} "
            f"status={self.status}>"
        )


class ApprovalLog(Base):
    """Log of every permission request and user response."""

    __tablename__ = "approval_logs"

    id = Column(String(36), primary_key=True, default=_uuid)
    action_type = Column(
        Enum("UNFOLLOW_BATCH", "FOLLOW_BATCH", name="approval_action_enum"),
        nullable=False,
    )
    requested_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        server_default=func.now(),
    )
    responded_at = Column(DateTime, nullable=True)
    response = Column(
        Enum("APPROVED", "DENIED", "TIMEOUT", name="approval_response_enum"),
        nullable=True,
    )
    account_list_json = Column(
        Text,
        nullable=True,
        comment="JSON array of target usernames in the batch",
    )

    def __repr__(self) -> str:
        return (
            f"<ApprovalLog {self.action_type} "
            f"response={self.response}>"
        )


class Blocklist(Base):
    """Accounts that have been unfollowed and should not be re-followed."""

    __tablename__ = "blocklist"

    id = Column(String(36), primary_key=True, default=_uuid)
    username = Column(String(255), nullable=False, unique=True, index=True)
    unfollowed_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        server_default=func.now(),
    )
    reason = Column(
        String(50),
        nullable=False,
        default="PRUNED_OLD_FOLLOW",
    )

    def __repr__(self) -> str:
        return f"<Blocklist @{self.username}>"
