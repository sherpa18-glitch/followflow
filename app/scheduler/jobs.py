"""Daily job orchestration — the full workflow state machine.

Runs the complete daily cycle:
1. Fetch following list sorted by earliest
2. Send Telegram unfollow permission request → wait for approval
3. Execute unfollows → send completion notification
4. Cooldown period
5. Run discovery engine → collect target accounts
6. Send Telegram follow permission request → wait for approval
7. Execute follows → send completion notification

Each step is gated by user approval via Telegram.
"""

import json
import uuid
import asyncio
from datetime import datetime
from typing import Optional, Set

from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_session_factory
from app.models import ActionLog, ApprovalLog, Blocklist
from app.instagram.browser import InstagramBrowser
from app.instagram.auth import ensure_authenticated
from app.instagram.unfollow import get_following_list_sorted, unfollow_accounts
from app.instagram.follow import follow_accounts, get_follow_summary
from app.instagram.discovery import discover_target_accounts
from app.telegram.bot import FollowFlowBot
from app.utils.logger import get_logger
from app.utils.rate_limiter import cooldown

logger = get_logger("scheduler")


class WorkflowState:
    """Tracks the state of the daily workflow."""

    IDLE = "IDLE"
    FETCHING_FOLLOWING = "FETCHING_FOLLOWING"
    AWAITING_UNFOLLOW_APPROVAL = "AWAITING_UNFOLLOW_APPROVAL"
    EXECUTING_UNFOLLOWS = "EXECUTING_UNFOLLOWS"
    COOLDOWN = "COOLDOWN"
    DISCOVERING_TARGETS = "DISCOVERING_TARGETS"
    AWAITING_FOLLOW_APPROVAL = "AWAITING_FOLLOW_APPROVAL"
    EXECUTING_FOLLOWS = "EXECUTING_FOLLOWS"
    COMPLETE = "COMPLETE"
    ERROR = "ERROR"

    def __init__(self):
        self.state = self.IDLE
        self.started_at: Optional[str] = None
        self.completed_at: Optional[str] = None
        self.unfollow_results: Optional[dict] = None
        self.follow_results: Optional[dict] = None
        self.error_message: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "unfollow_results": self.unfollow_results,
            "follow_results": self.follow_results,
            "error_message": self.error_message,
        }


# Global workflow state (for the /status endpoint)
current_workflow = WorkflowState()


async def daily_workflow(telegram_bot: FollowFlowBot) -> None:
    """Execute the full daily follow/unfollow workflow.

    This is the main orchestration function triggered by the
    daily scheduler. Each destructive action requires explicit
    user approval via Telegram.

    Args:
        telegram_bot: The FollowFlowBot instance for notifications.
    """
    global current_workflow
    current_workflow = WorkflowState()
    current_workflow.state = WorkflowState.FETCHING_FOLLOWING
    current_workflow.started_at = datetime.utcnow().isoformat()

    settings = get_settings()
    batch_id = str(uuid.uuid4())

    logger.info(
        f"Daily workflow started, batch_id={batch_id}",
        extra={"action": "workflow_start", "batch_id": batch_id},
    )

    browser = None
    try:
        # --- Launch browser and authenticate ---
        browser = InstagramBrowser(headless=True)
        await browser.launch()

        authenticated = await ensure_authenticated(
            browser,
            settings.instagram_username,
            settings.instagram_password,
        )
        if not authenticated:
            error_msg = "Instagram authentication failed. Please check credentials."
            current_workflow.state = WorkflowState.ERROR
            current_workflow.error_message = error_msg
            await telegram_bot.send_error_notification(error_msg)
            logger.error(error_msg, extra={"action": "auth_failed"})
            return

        page = await browser.get_page()

        # =====================================================
        # PHASE 1: UNFOLLOW
        # =====================================================

        # Step 1: Fetch following list
        current_workflow.state = WorkflowState.FETCHING_FOLLOWING
        logger.info("Fetching following list...", extra={"action": "fetch_following"})

        accounts_to_unfollow = await get_following_list_sorted(
            page,
            settings.instagram_username,
            count=settings.unfollow_batch_size,
        )

        if not accounts_to_unfollow:
            logger.warning(
                "No accounts found in following list",
                extra={"action": "fetch_following", "detail": "empty"},
            )
            # Skip unfollow phase, continue to follow phase
        else:
            # Step 2: Request unfollow approval
            current_workflow.state = WorkflowState.AWAITING_UNFOLLOW_APPROVAL
            unfollow_approval_id = f"unfollow-{batch_id}"

            # Log the approval request
            db = _get_db_session()
            approval_log = ApprovalLog(
                action_type="UNFOLLOW_BATCH",
                account_list_json=json.dumps(
                    [a["username"] for a in accounts_to_unfollow]
                ),
            )
            db.add(approval_log)
            db.commit()
            approval_db_id = approval_log.id

            await telegram_bot.send_unfollow_approval_request(
                unfollow_approval_id,
                accounts_to_unfollow,
            )

            logger.info(
                f"Waiting for unfollow approval: {unfollow_approval_id}",
                extra={"action": "await_approval"},
            )

            # Step 3: Wait for approval
            unfollow_response = await telegram_bot.wait_for_approval(
                unfollow_approval_id,
                timeout_hours=settings.approval_timeout_hours,
            )

            # Update approval log
            approval_log.response = unfollow_response
            approval_log.responded_at = datetime.utcnow()
            db.commit()

            if unfollow_response == "APPROVED":
                # Step 4: Execute unfollows
                current_workflow.state = WorkflowState.EXECUTING_UNFOLLOWS
                logger.info(
                    "Unfollow approved — executing...",
                    extra={"action": "unfollow_approved"},
                )

                unfollow_results = await unfollow_accounts(
                    page,
                    accounts_to_unfollow,
                    delay_min=settings.unfollow_delay_min,
                    delay_max=settings.unfollow_delay_max,
                    batch_id=batch_id,
                )

                # Log results to database
                success_count = 0
                fail_count = 0
                for result in unfollow_results:
                    action_log = ActionLog(
                        action_type="UNFOLLOW",
                        target_username=result["username"],
                        status=result["status"],
                        daily_batch_id=batch_id,
                    )
                    db.add(action_log)

                    # Add to blocklist if successful
                    if result["status"] == "SUCCESS":
                        success_count += 1
                        blocklist_entry = Blocklist(
                            username=result["username"],
                            reason="PRUNED_OLD_FOLLOW",
                        )
                        try:
                            db.add(blocklist_entry)
                            db.commit()
                        except Exception:
                            db.rollback()  # Username might already exist
                    else:
                        fail_count += 1

                db.commit()

                current_workflow.unfollow_results = {
                    "success": success_count,
                    "failed": fail_count,
                    "total": len(unfollow_results),
                }

                # Step 5: Send unfollow complete notification
                await telegram_bot.send_unfollow_complete(
                    success_count=success_count,
                    fail_count=fail_count,
                )

                logger.info(
                    f"Unfollow complete: {success_count} success, {fail_count} failed",
                    extra={"action": "unfollow_complete", "batch_id": batch_id},
                )

            elif unfollow_response == "DENIED":
                logger.info(
                    "Unfollow denied by user — skipping",
                    extra={"action": "unfollow_denied"},
                )

            else:  # TIMEOUT
                logger.info(
                    "Unfollow approval timed out — skipping",
                    extra={"action": "unfollow_timeout"},
                )

            db.close()

        # =====================================================
        # COOLDOWN between phases
        # =====================================================
        current_workflow.state = WorkflowState.COOLDOWN
        logger.info(
            "Cooldown between unfollow and follow phases...",
            extra={"action": "cooldown"},
        )
        await cooldown(
            settings.cooldown_minutes_min,
            settings.cooldown_minutes_max,
        )

        # =====================================================
        # PHASE 2: FOLLOW
        # =====================================================

        # Step 6: Run discovery engine
        current_workflow.state = WorkflowState.DISCOVERING_TARGETS
        logger.info(
            "Running discovery engine...",
            extra={"action": "discovery_start"},
        )

        # Get current blocklist and following set for exclusion
        db = _get_db_session()
        blocklist_usernames = _get_blocklist_usernames(db)
        # Note: already_following could be fetched from Instagram,
        # but for now we use the action log as a proxy
        already_following = _get_recently_followed_usernames(db)

        target_accounts = await discover_target_accounts(
            page,
            max_followers=settings.discovery_max_followers,
            min_following=settings.discovery_min_following,
            activity_days=settings.discovery_activity_days,
            target_count=settings.follow_batch_size,
            already_following=already_following,
            blocklist=blocklist_usernames,
        )

        if not target_accounts:
            logger.warning(
                "Discovery found 0 qualifying accounts",
                extra={"action": "discovery_empty"},
            )
            current_workflow.state = WorkflowState.COMPLETE
            current_workflow.completed_at = datetime.utcnow().isoformat()
            db.close()
            return

        # Step 7: Request follow approval
        current_workflow.state = WorkflowState.AWAITING_FOLLOW_APPROVAL
        follow_approval_id = f"follow-{batch_id}"

        approval_log = ApprovalLog(
            action_type="FOLLOW_BATCH",
            account_list_json=json.dumps(
                [a["username"] for a in target_accounts]
            ),
        )
        db.add(approval_log)
        db.commit()

        await telegram_bot.send_follow_approval_request(
            follow_approval_id,
            target_accounts,
        )

        logger.info(
            f"Waiting for follow approval: {follow_approval_id}",
            extra={"action": "await_approval"},
        )

        # Step 8: Wait for approval
        follow_response = await telegram_bot.wait_for_approval(
            follow_approval_id,
            timeout_hours=settings.approval_timeout_hours,
        )

        approval_log.response = follow_response
        approval_log.responded_at = datetime.utcnow()
        db.commit()

        if follow_response == "APPROVED":
            # Step 9: Execute follows
            current_workflow.state = WorkflowState.EXECUTING_FOLLOWS
            logger.info(
                "Follow approved — executing...",
                extra={"action": "follow_approved"},
            )

            follow_results = await follow_accounts(
                page,
                target_accounts,
                delay_min=settings.follow_delay_min,
                delay_max=settings.follow_delay_max,
                batch_id=batch_id,
            )

            # Log results to database
            for result in follow_results:
                action_log = ActionLog(
                    action_type="FOLLOW",
                    target_username=result["username"],
                    target_follower_count=result.get("follower_count"),
                    target_following_count=result.get("following_count"),
                    target_region=result.get("region"),
                    region_confidence=result.get("region_confidence"),
                    status=result["status"],
                    daily_batch_id=batch_id,
                )
                db.add(action_log)

            db.commit()

            # Step 10: Send follow complete notification
            summary = get_follow_summary(follow_results)

            current_workflow.follow_results = summary

            await telegram_bot.send_follow_complete(
                total_sent=summary["total_sent"],
                public_count=summary["public_count"],
                private_count=summary["private_count"],
                fail_count=summary["fail_count"],
            )

            logger.info(
                f"Follow complete: {summary['total_sent']} sent "
                f"({summary['public_count']} public, "
                f"{summary['private_count']} private)",
                extra={"action": "follow_complete", "batch_id": batch_id},
            )

        elif follow_response == "DENIED":
            logger.info(
                "Follow denied by user — skipping",
                extra={"action": "follow_denied"},
            )

        else:  # TIMEOUT
            logger.info(
                "Follow approval timed out — skipping",
                extra={"action": "follow_timeout"},
            )

        db.close()

        # =====================================================
        # COMPLETE
        # =====================================================
        current_workflow.state = WorkflowState.COMPLETE
        current_workflow.completed_at = datetime.utcnow().isoformat()
        logger.info(
            f"Daily workflow complete, batch_id={batch_id}",
            extra={"action": "workflow_complete", "batch_id": batch_id},
        )

    except Exception as e:
        error_msg = f"Workflow error: {str(e)}"
        current_workflow.state = WorkflowState.ERROR
        current_workflow.error_message = error_msg
        logger.error(error_msg, extra={"action": "workflow_error"})

        try:
            await telegram_bot.send_error_notification(error_msg)
        except Exception:
            logger.error("Failed to send error notification")

    finally:
        if browser:
            await browser.close()


def get_current_workflow_state() -> dict:
    """Return the current workflow state for the /status endpoint."""
    return current_workflow.to_dict()


# --- Database helpers ---


def _get_db_session() -> Session:
    """Get a new database session."""
    SessionLocal = get_session_factory()
    return SessionLocal()


def _get_blocklist_usernames(db: Session) -> Set[str]:
    """Get all usernames on the blocklist."""
    entries = db.query(Blocklist.username).all()
    return {e[0] for e in entries}


def _get_recently_followed_usernames(db: Session) -> Set[str]:
    """Get usernames that were recently followed (from action logs)."""
    entries = (
        db.query(ActionLog.target_username)
        .filter(ActionLog.action_type == "FOLLOW")
        .filter(ActionLog.status == "SUCCESS")
        .all()
    )
    return {e[0] for e in entries}
