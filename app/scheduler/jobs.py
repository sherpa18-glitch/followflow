"""Workflow orchestration for follow and unfollow processes.

Provides separate workflows for follow-only and unfollow-only,
each with:
- Mid-process cancellation via asyncio.Event
- 5-minute periodic Telegram progress updates
- CSV export of results after completion
- Category detection for followed accounts

Each workflow is triggered manually via API endpoint or Telegram.
"""

import csv
import json
import uuid
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional, Set, List, Dict

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
from app.utils.rate_limiter import retry_with_backoff

logger = get_logger("scheduler")

# Directory for CSV exports
EXPORT_DIR = Path("exports")
EXPORT_DIR.mkdir(exist_ok=True)


class WorkflowState:
    """Tracks the state of the current workflow."""

    IDLE = "IDLE"
    FETCHING_FOLLOWING = "FETCHING_FOLLOWING"
    AWAITING_UNFOLLOW_APPROVAL = "AWAITING_UNFOLLOW_APPROVAL"
    EXECUTING_UNFOLLOWS = "EXECUTING_UNFOLLOWS"
    DISCOVERING_TARGETS = "DISCOVERING_TARGETS"
    AWAITING_FOLLOW_APPROVAL = "AWAITING_FOLLOW_APPROVAL"
    EXECUTING_FOLLOWS = "EXECUTING_FOLLOWS"
    CANCELLED = "CANCELLED"
    COMPLETE = "COMPLETE"
    ERROR = "ERROR"

    def __init__(self):
        self.state = self.IDLE
        self.workflow_type: Optional[str] = None  # "follow" or "unfollow"
        self.started_at: Optional[str] = None
        self.completed_at: Optional[str] = None
        self.unfollow_results: Optional[dict] = None
        self.follow_results: Optional[dict] = None
        self.error_message: Optional[str] = None
        self.batch_id: Optional[str] = None
        self.csv_path: Optional[str] = None

        # Progress counters (updated during execution)
        self.total_target: int = 0
        self.processed: int = 0
        self.success_count: int = 0
        self.fail_count: int = 0
        self.errors: List[str] = []

        # Cancellation event
        self.cancel_event = asyncio.Event()

    @property
    def is_cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def cancel(self):
        self.cancel_event.set()
        self.state = self.CANCELLED

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "workflow_type": self.workflow_type,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "batch_id": self.batch_id,
            "progress": {
                "total": self.total_target,
                "processed": self.processed,
                "success": self.success_count,
                "failed": self.fail_count,
            },
            "unfollow_results": self.unfollow_results,
            "follow_results": self.follow_results,
            "error_message": self.error_message,
            "csv_path": self.csv_path,
        }


# Global workflow state (for the /status endpoint)
current_workflow = WorkflowState()


def get_current_workflow_state() -> dict:
    """Return the current workflow state for the /status endpoint."""
    return current_workflow.to_dict()


def cancel_current_workflow() -> dict:
    """Cancel the currently running workflow.

    Returns:
        Dict with cancellation status.
    """
    global current_workflow
    if current_workflow.state in (
        WorkflowState.IDLE,
        WorkflowState.COMPLETE,
        WorkflowState.CANCELLED,
        WorkflowState.ERROR,
    ):
        return {
            "status": "no_workflow",
            "message": f"No active workflow to cancel (state: {current_workflow.state})",
        }

    current_workflow.cancel()
    logger.info(
        "Workflow cancellation requested",
        extra={"action": "cancel", "batch_id": current_workflow.batch_id},
    )
    return {
        "status": "cancelling",
        "message": "Cancellation signal sent. Workflow will stop after current action.",
    }


async def _send_progress_updates(
    telegram_bot: FollowFlowBot,
    workflow: WorkflowState,
    interval_seconds: int = 300,
) -> None:
    """Send periodic progress updates via Telegram every `interval_seconds`.

    Runs as a background task, checks cancellation and state.
    """
    while True:
        await asyncio.sleep(interval_seconds)

        # Stop if workflow is no longer executing
        if workflow.state in (
            WorkflowState.IDLE,
            WorkflowState.COMPLETE,
            WorkflowState.CANCELLED,
            WorkflowState.ERROR,
        ):
            break

        # Also stop if waiting for approval (no progress to report)
        if workflow.state in (
            WorkflowState.AWAITING_FOLLOW_APPROVAL,
            WorkflowState.AWAITING_UNFOLLOW_APPROVAL,
        ):
            continue

        action = workflow.workflow_type or "workflow"
        elapsed = ""
        if workflow.started_at:
            start = datetime.fromisoformat(workflow.started_at)
            mins = int((datetime.utcnow() - start).total_seconds() / 60)
            elapsed = f" ({mins}m elapsed)"

        error_text = ""
        if workflow.errors:
            recent = workflow.errors[-3:]  # Last 3 errors
            error_text = "\n\nRecent errors:\n" + "\n".join(f"  - {e}" for e in recent)

        msg = (
            f"📊 *FollowFlow — Progress Update*{_escape_md(elapsed)}\n\n"
            f"*{_escape_md(action.capitalize())}* in progress\\.\\.\\.\n"
            f"Processed: *{workflow.processed}* / {workflow.total_target}\n"
            f"Success: *{workflow.success_count}*\n"
            f"Failed: *{workflow.fail_count}*"
        )
        if error_text:
            msg += f"\n{_escape_md(error_text)}"

        try:
            await telegram_bot.bot.send_message(
                chat_id=telegram_bot.chat_id,
                text=msg,
                parse_mode="MarkdownV2",
            )
        except Exception as e:
            logger.warning(f"Failed to send progress update: {e}")


def _escape_md(text: str) -> str:
    """Escape special characters for Telegram MarkdownV2."""
    special_chars = r"_*[]()~`<>#+-=|{}.!"
    result = []
    for char in text:
        if char in special_chars:
            result.append(f"\\{char}")
        else:
            result.append(char)
    return "".join(result)


def _generate_csv(
    results: List[Dict],
    action_type: str,
    batch_id: str,
) -> str:
    """Generate a CSV export of follow/unfollow results.

    Args:
        results: List of result dicts from follow/unfollow execution.
        action_type: 'follow' or 'unfollow'.
        batch_id: The batch ID for this run.

    Returns:
        Path to the generated CSV file.
    """
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"{action_type}_{timestamp}_{batch_id[:8]}.csv"
    filepath = EXPORT_DIR / filename

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "username", "timestamp", "status", "region",
            "category", "follower_count", "following_count", "follow_type",
        ])
        for r in results:
            writer.writerow([
                r.get("username", ""),
                r.get("timestamp", ""),
                r.get("status", ""),
                r.get("region", ""),
                r.get("category", ""),
                r.get("follower_count", ""),
                r.get("following_count", ""),
                r.get("follow_type", ""),
            ])

    logger.info(f"CSV exported: {filepath}")
    return str(filepath)


# =====================================================================
# FOLLOW-ONLY WORKFLOW
# =====================================================================


async def follow_only_workflow(telegram_bot: FollowFlowBot) -> None:
    """Execute ONLY the follow phase (discovery -> approval -> follow).

    Features:
    - Mid-process cancellation via cancel_event
    - 5-minute progress updates via Telegram
    - CSV export on completion
    - Category detection for each account
    """
    global current_workflow
    current_workflow = WorkflowState()
    current_workflow.workflow_type = "follow"
    current_workflow.state = WorkflowState.DISCOVERING_TARGETS
    current_workflow.started_at = datetime.utcnow().isoformat()

    settings = get_settings()
    batch_id = str(uuid.uuid4())
    current_workflow.batch_id = batch_id

    logger.info(
        f"Follow-only workflow started, batch_id={batch_id}",
        extra={"action": "follow_only_start", "batch_id": batch_id},
    )

    # Start progress update background task
    progress_task = asyncio.create_task(
        _send_progress_updates(telegram_bot, current_workflow)
    )

    browser = None
    try:
        # --- Launch browser and authenticate ---
        async def _launch_and_auth():
            nonlocal browser
            if browser is not None:
                try:
                    await browser.close()
                except Exception:
                    pass
            browser = InstagramBrowser(headless=settings.browser_headless)
            await browser.launch()
            ok = await ensure_authenticated(
                browser,
                settings.instagram_username,
                settings.instagram_password,
            )
            if not ok:
                raise RuntimeError("Authentication failed")
            return ok

        try:
            await retry_with_backoff(
                _launch_and_auth,
                max_retries=2,
                base_delay=10.0,
                description="Instagram browser launch + auth",
            )
        except Exception:
            error_msg = (
                "Instagram authentication failed after retries. "
                "Please check credentials or re-login manually."
            )
            current_workflow.state = WorkflowState.ERROR
            current_workflow.error_message = error_msg
            await telegram_bot.send_error_notification(error_msg)
            return

        if current_workflow.is_cancelled:
            current_workflow.state = WorkflowState.CANCELLED
            await telegram_bot.bot.send_message(
                chat_id=telegram_bot.chat_id,
                text="⛔ Follow workflow cancelled before discovery\\.",
                parse_mode="MarkdownV2",
            )
            return

        page = await browser.get_page()

        # Step 1: Discovery
        current_workflow.state = WorkflowState.DISCOVERING_TARGETS
        db = _get_db_session()
        blocklist_usernames = _get_blocklist_usernames(db)
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
            current_workflow.state = WorkflowState.COMPLETE
            current_workflow.completed_at = datetime.utcnow().isoformat()
            db.close()
            await telegram_bot.bot.send_message(
                chat_id=telegram_bot.chat_id,
                text="ℹ️ Discovery found 0 qualifying accounts\\. Nothing to follow\\.",
                parse_mode="MarkdownV2",
            )
            return

        if current_workflow.is_cancelled:
            current_workflow.state = WorkflowState.CANCELLED
            db.close()
            return

        # Step 2: Approval
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

        follow_response = await telegram_bot.wait_for_approval(
            follow_approval_id,
            timeout_hours=settings.approval_timeout_hours,
        )

        approval_log.response = follow_response
        approval_log.responded_at = datetime.utcnow()
        db.commit()

        if current_workflow.is_cancelled:
            current_workflow.state = WorkflowState.CANCELLED
            db.close()
            return

        if follow_response == "APPROVED":
            # Step 3: Execute follows with cancellation support
            current_workflow.state = WorkflowState.EXECUTING_FOLLOWS
            current_workflow.total_target = len(target_accounts)

            follow_results = await _follow_with_cancellation(
                page,
                target_accounts,
                settings.follow_delay_min,
                settings.follow_delay_max,
                batch_id,
                current_workflow,
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
                    target_category=result.get("category"),
                    status=result["status"],
                    daily_batch_id=batch_id,
                )
                db.add(action_log)
            db.commit()

            summary = get_follow_summary(follow_results)
            current_workflow.follow_results = summary

            # Generate CSV export
            csv_path = _generate_csv(follow_results, "follow", batch_id)
            current_workflow.csv_path = csv_path

            # Send completion notification
            cancelled_note = ""
            if current_workflow.is_cancelled:
                cancelled_note = " \\(cancelled early\\)"

            await telegram_bot.send_follow_complete(
                total_sent=summary["total_sent"],
                public_count=summary["public_count"],
                private_count=summary["private_count"],
                fail_count=summary["fail_count"],
            )

            # Send CSV info
            await telegram_bot.bot.send_message(
                chat_id=telegram_bot.chat_id,
                text=(
                    f"📁 *CSV Export Ready*{cancelled_note}\n\n"
                    f"File: `{_escape_md(csv_path)}`\n"
                    f"Rows: {len(follow_results)}"
                ),
                parse_mode="MarkdownV2",
            )

        elif follow_response == "DENIED":
            logger.info("Follow denied by user")
        else:
            logger.info("Follow approval timed out")

        db.close()

        if not current_workflow.is_cancelled:
            current_workflow.state = WorkflowState.COMPLETE
        current_workflow.completed_at = datetime.utcnow().isoformat()

    except Exception as e:
        error_msg = f"Follow workflow error: {str(e)}"
        current_workflow.state = WorkflowState.ERROR
        current_workflow.error_message = error_msg
        logger.error(error_msg, extra={"action": "workflow_error"})
        try:
            await telegram_bot.send_error_notification(error_msg)
        except Exception:
            logger.error("Failed to send error notification")

    finally:
        progress_task.cancel()
        if browser:
            await browser.close()


# =====================================================================
# UNFOLLOW-ONLY WORKFLOW
# =====================================================================


async def unfollow_only_workflow(telegram_bot: FollowFlowBot) -> None:
    """Execute ONLY the unfollow phase.

    Features:
    - Mid-process cancellation
    - 5-minute progress updates
    - CSV export on completion
    """
    global current_workflow
    current_workflow = WorkflowState()
    current_workflow.workflow_type = "unfollow"
    current_workflow.state = WorkflowState.FETCHING_FOLLOWING
    current_workflow.started_at = datetime.utcnow().isoformat()

    settings = get_settings()
    batch_id = str(uuid.uuid4())
    current_workflow.batch_id = batch_id

    logger.info(
        f"Unfollow-only workflow started, batch_id={batch_id}",
        extra={"action": "unfollow_only_start", "batch_id": batch_id},
    )

    # Start progress update background task
    progress_task = asyncio.create_task(
        _send_progress_updates(telegram_bot, current_workflow)
    )

    browser = None
    try:
        # --- Launch browser and authenticate ---
        async def _launch_and_auth():
            nonlocal browser
            if browser is not None:
                try:
                    await browser.close()
                except Exception:
                    pass
            browser = InstagramBrowser(headless=settings.browser_headless)
            await browser.launch()
            ok = await ensure_authenticated(
                browser,
                settings.instagram_username,
                settings.instagram_password,
            )
            if not ok:
                raise RuntimeError("Authentication failed")
            return ok

        try:
            await retry_with_backoff(
                _launch_and_auth,
                max_retries=2,
                base_delay=10.0,
                description="Instagram browser launch + auth",
            )
        except Exception:
            error_msg = (
                "Instagram authentication failed after retries. "
                "Please check credentials or re-login manually."
            )
            current_workflow.state = WorkflowState.ERROR
            current_workflow.error_message = error_msg
            await telegram_bot.send_error_notification(error_msg)
            return

        if current_workflow.is_cancelled:
            current_workflow.state = WorkflowState.CANCELLED
            return

        page = await browser.get_page()

        # Step 1: Fetch following list
        current_workflow.state = WorkflowState.FETCHING_FOLLOWING
        accounts_to_unfollow = await get_following_list_sorted(
            page,
            settings.instagram_username,
            count=settings.unfollow_batch_size,
            browser=browser,
        )

        if not accounts_to_unfollow:
            current_workflow.state = WorkflowState.COMPLETE
            current_workflow.completed_at = datetime.utcnow().isoformat()
            await telegram_bot.bot.send_message(
                chat_id=telegram_bot.chat_id,
                text="ℹ️ No accounts found in following list to unfollow\\.",
                parse_mode="MarkdownV2",
            )
            return

        if current_workflow.is_cancelled:
            current_workflow.state = WorkflowState.CANCELLED
            return

        # Step 2: Request approval
        current_workflow.state = WorkflowState.AWAITING_UNFOLLOW_APPROVAL
        unfollow_approval_id = f"unfollow-{batch_id}"

        db = _get_db_session()
        approval_log = ApprovalLog(
            action_type="UNFOLLOW_BATCH",
            account_list_json=json.dumps(
                [a["username"] for a in accounts_to_unfollow]
            ),
        )
        db.add(approval_log)
        db.commit()

        await telegram_bot.send_unfollow_approval_request(
            unfollow_approval_id,
            accounts_to_unfollow,
        )

        unfollow_response = await telegram_bot.wait_for_approval(
            unfollow_approval_id,
            timeout_hours=settings.approval_timeout_hours,
        )

        approval_log.response = unfollow_response
        approval_log.responded_at = datetime.utcnow()
        db.commit()

        if current_workflow.is_cancelled:
            current_workflow.state = WorkflowState.CANCELLED
            db.close()
            return

        if unfollow_response == "APPROVED":
            # Step 3: Execute unfollows with cancellation support
            current_workflow.state = WorkflowState.EXECUTING_UNFOLLOWS
            current_workflow.total_target = len(accounts_to_unfollow)

            unfollow_results = await _unfollow_with_cancellation(
                page,
                accounts_to_unfollow,
                settings.unfollow_delay_min,
                settings.unfollow_delay_max,
                batch_id,
                current_workflow,
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
                        db.rollback()
                else:
                    fail_count += 1

            db.commit()

            current_workflow.unfollow_results = {
                "success": success_count,
                "failed": fail_count,
                "total": len(unfollow_results),
            }

            # Generate CSV export
            csv_path = _generate_csv(unfollow_results, "unfollow", batch_id)
            current_workflow.csv_path = csv_path

            # Send completion notification
            await telegram_bot.send_unfollow_complete(
                success_count=success_count,
                fail_count=fail_count,
            )

            # Send CSV info
            cancelled_note = ""
            if current_workflow.is_cancelled:
                cancelled_note = " \\(cancelled early\\)"

            await telegram_bot.bot.send_message(
                chat_id=telegram_bot.chat_id,
                text=(
                    f"📁 *CSV Export Ready*{cancelled_note}\n\n"
                    f"File: `{_escape_md(csv_path)}`\n"
                    f"Rows: {len(unfollow_results)}"
                ),
                parse_mode="MarkdownV2",
            )

        elif unfollow_response == "DENIED":
            logger.info("Unfollow denied by user")
        else:
            logger.info("Unfollow approval timed out")

        db.close()

        if not current_workflow.is_cancelled:
            current_workflow.state = WorkflowState.COMPLETE
        current_workflow.completed_at = datetime.utcnow().isoformat()

    except Exception as e:
        error_msg = f"Unfollow workflow error: {str(e)}"
        current_workflow.state = WorkflowState.ERROR
        current_workflow.error_message = error_msg
        logger.error(error_msg, extra={"action": "workflow_error"})
        try:
            await telegram_bot.send_error_notification(error_msg)
        except Exception:
            logger.error("Failed to send error notification")

    finally:
        progress_task.cancel()
        if browser:
            await browser.close()


# =====================================================================
# EXECUTION WITH CANCELLATION SUPPORT
# =====================================================================


async def _follow_with_cancellation(
    page,
    accounts: List[Dict],
    delay_min: int,
    delay_max: int,
    batch_id: str,
    workflow: WorkflowState,
) -> List[Dict]:
    """Execute follows with cancellation check between each account.

    Wraps follow_accounts logic but checks cancel_event between follows.
    """
    from app.instagram.follow import _follow_single_account, RATE_LIMIT_PAUSE
    from app.utils.rate_limiter import random_delay

    results = []
    total = len(accounts)

    for i, account in enumerate(accounts):
        if workflow.is_cancelled:
            logger.info(f"Follow cancelled at {i}/{total}")
            break

        username = account["username"]
        logger.info(
            f"Following [{i+1}/{total}]: @{username}",
            extra={"action": "follow", "username": username, "batch_id": batch_id},
        )

        result = await _follow_single_account(page, username)
        result["batch_id"] = batch_id
        result["timestamp"] = datetime.utcnow().isoformat()
        result["follower_count"] = account.get("follower_count")
        result["following_count"] = account.get("following_count")
        result["region"] = account.get("region")
        result["region_confidence"] = account.get("region_confidence")
        result["category"] = account.get("category", "other")
        results.append(result)

        # Update progress counters
        workflow.processed = i + 1
        if result["status"] == "SUCCESS":
            workflow.success_count += 1
        else:
            workflow.fail_count += 1
            if result["status"] == "FAILED":
                workflow.errors.append(f"@{username}: failed")

        if result["status"] == "RATE_LIMITED":
            workflow.errors.append(f"@{username}: rate limited")
            logger.warning(f"Rate limited at {i+1}/{total}, pausing...")
            await asyncio.sleep(RATE_LIMIT_PAUSE)
            if workflow.is_cancelled:
                break
            retry_result = await _follow_single_account(page, username)
            if retry_result["status"] == "SUCCESS":
                workflow.fail_count -= 1
                workflow.success_count += 1
                results[-1] = {
                    **retry_result,
                    "batch_id": batch_id,
                    "timestamp": datetime.utcnow().isoformat(),
                    "follower_count": account.get("follower_count"),
                    "following_count": account.get("following_count"),
                    "region": account.get("region"),
                    "region_confidence": account.get("region_confidence"),
                    "category": account.get("category", "other"),
                }

        # Delay between follows (skip after last)
        if i < total - 1 and not workflow.is_cancelled:
            await random_delay(delay_min, delay_max)

    return results


async def _unfollow_with_cancellation(
    page,
    accounts: List[Dict],
    delay_min: int,
    delay_max: int,
    batch_id: str,
    workflow: WorkflowState,
) -> List[Dict]:
    """Execute unfollows with cancellation check between each account."""
    from app.instagram.unfollow import _unfollow_single_account, RATE_LIMIT_PAUSE
    from app.utils.rate_limiter import random_delay

    results = []
    total = len(accounts)

    for i, account in enumerate(accounts):
        if workflow.is_cancelled:
            logger.info(f"Unfollow cancelled at {i}/{total}")
            break

        username = account["username"]
        logger.info(
            f"Unfollowing [{i+1}/{total}]: @{username}",
            extra={"action": "unfollow", "username": username, "batch_id": batch_id},
        )

        result = await _unfollow_single_account(page, username)
        result["batch_id"] = batch_id
        result["timestamp"] = datetime.utcnow().isoformat()
        results.append(result)

        # Update progress counters
        workflow.processed = i + 1
        if result["status"] == "SUCCESS":
            workflow.success_count += 1
        else:
            workflow.fail_count += 1
            if result["status"] == "FAILED":
                workflow.errors.append(f"@{username}: failed")

        if result["status"] == "RATE_LIMITED":
            workflow.errors.append(f"@{username}: rate limited")
            logger.warning(f"Rate limited at {i+1}/{total}, pausing...")
            await asyncio.sleep(RATE_LIMIT_PAUSE)
            if workflow.is_cancelled:
                break
            retry_result = await _unfollow_single_account(page, username)
            if retry_result["status"] == "SUCCESS":
                workflow.fail_count -= 1
                workflow.success_count += 1
                results[-1] = {
                    **retry_result,
                    "batch_id": batch_id,
                    "timestamp": datetime.utcnow().isoformat(),
                }

        # Delay between unfollows (skip after last)
        if i < total - 1 and not workflow.is_cancelled:
            await random_delay(delay_min, delay_max)

    return results


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
