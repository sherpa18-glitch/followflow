# FollowFlow — Usage Guide

FollowFlow is an Instagram growth automation bot with manual triggers, human-in-the-loop approval, mid-process cancellation, and CSV exports. All workflows are triggered manually — no scheduled jobs.

## Quick Start

### 1. Start the Server

```bash
cd ~/followflow
./venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Output:
```
INFO:     Uvicorn running on http://0.0.0.0:8000
```

### 2. Verify It's Running

```bash
curl http://localhost:8000/health
```

Response:
```json
{"status": "ok", "service": "followflow", "version": "0.2.0"}
```

---

## Workflows

### Follow Workflow

Discovers new target accounts based on hashtag engagement, requests approval, then follows them.

**Steps:**
1. Discovery: Find accounts matching criteria (followers < 5,000, following > 100, active in last 14 days)
2. Approval: Send Telegram message with list of accounts to follow
3. Execution: Follow accounts with 5-minute progress updates
4. Export: Generate CSV with username, timestamp, region, category, follower/following counts

**Trigger:**
```bash
curl -X POST http://localhost:8000/trigger-follow
```

Response:
```json
{
  "status": "triggered",
  "message": "Follow workflow started. Check /status for progress."
}
```

### Unfollow Workflow

Fetches your oldest-followed accounts, requests approval, then unfollows them.

**Steps:**
1. Fetch: Get following list sorted by oldest-first from Instagram API
2. Approval: Send Telegram message with list of accounts to unfollow
3. Execution: Unfollow accounts with 5-minute progress updates
4. Export: Generate CSV with username, timestamp, status

**Trigger:**
```bash
curl -X POST http://localhost:8000/trigger-unfollow
```

Response:
```json
{
  "status": "triggered",
  "message": "Unfollow workflow started. Check /status for progress."
}
```

---

## Monitoring

### Check Status

```bash
curl http://localhost:8000/status
```

Response:
```json
{
  "state": "EXECUTING_FOLLOWS",
  "workflow_type": "follow",
  "started_at": "2026-02-19T10:30:00",
  "completed_at": null,
  "batch_id": "abc-123-def",
  "progress": {
    "total": 50,
    "processed": 15,
    "success": 14,
    "failed": 1
  },
  "unfollow_results": null,
  "follow_results": null,
  "error_message": null,
  "csv_path": null
}
```

### Live Progress Updates

Watch progress update in real-time:
```bash
watch -n 5 'curl -s http://localhost:8000/status | jq .progress'
```

This polls every 5 seconds and shows:
```
total: 50, processed: 15, success: 14, failed: 1
```

### Telegram Status

From Telegram, send:
```
/status
```

You'll receive a message like:
```
📊 FollowFlow Status

State: EXECUTING_FOLLOWS
Type: follow

Progress: 15/50
Success: 14
Failed: 1
```

---

## Cancellation

### Cancel via HTTP

```bash
curl -X POST http://localhost:8000/cancel
```

Response:
```json
{
  "status": "cancelling",
  "message": "Cancellation signal sent. Workflow will stop after current action."
}
```

The workflow will complete the current action, then stop. All processed results are still saved and exported to CSV.

### Cancel via Telegram

```
/cancel
```

You'll receive confirmation:
```
⛔ Cancellation requested

Workflow will stop after the current action completes.
```

---

## Progress Updates

Every 5 minutes during execution, you receive a Telegram progress update:

```
📊 FollowFlow — Progress Update (10m elapsed)

Follow in progress...
Processed: 25 / 50
Success: 24
Failed: 1

Recent errors:
  - @user1: failed
```

---

## CSV Exports

### List All Exports

```bash
curl http://localhost:8000/exports
```

Response:
```json
{
  "exports": [
    {
      "filename": "follow_20260219_103000_abc12345.csv",
      "size_bytes": 2048,
      "download_url": "/export/follow_20260219_103000_abc12345.csv"
    }
  ]
}
```

### Download a CSV

```bash
curl http://localhost:8000/export/follow_20260219_103000_abc12345.csv -o export.csv
```

Or via your browser:
```
http://localhost:8000/export/follow_20260219_103000_abc12345.csv
```

### CSV Format (Follow)

```csv
username,timestamp,status,region,category,follower_count,following_count,follow_type
puppy_fan_kr,2026-02-19T10:30:05,SUCCESS,KR,dogs,1120,4200,public
dogmom_texas,2026-02-19T10:30:45,SUCCESS,NA,pets,890,3800,public
paws_tokyo,2026-02-19T10:31:25,FAILED,JP,dogs,1540,5100,
```

### CSV Format (Unfollow)

```csv
username,timestamp,status,region,category,follower_count,following_count,follow_type
oldaccount1,2026-02-19T10:40:05,SUCCESS,,,,
oldaccount2,2026-02-19T10:40:35,SUCCESS,,,,
```

---

## Approval Workflow

When a workflow starts, you'll receive a Telegram message with an approval request:

**Follow Approval:**
```
✅ Approve 50 new accounts to follow?

Criteria:
  Followers: < 5,000
  Following: > 100
  Active in last: 14 days

Accounts:
1. @puppy_fan_kr
2. @dogmom_texas
...
(showing first 10 of 50)

[Approve] [Deny]
```

**Unfollow Approval:**
```
🔄 Approve unfollowing 50 oldest accounts?

Accounts (oldest first):
1. @olduser1
2. @olduser2
...

[Approve] [Deny]
```

Click **Approve** to proceed or **Deny** to skip.

---

## Configuration

Edit `.env` to customize behavior:

```env
# Instagram
INSTAGRAM_USERNAME=thor.thunderpup.gsd
INSTAGRAM_PASSWORD=your_password

# Telegram
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id

# Batch sizes
UNFOLLOW_BATCH_SIZE=100
FOLLOW_BATCH_SIZE=50

# Rate limiting (seconds between actions)
UNFOLLOW_DELAY_MIN=10
UNFOLLOW_DELAY_MAX=20
FOLLOW_DELAY_MIN=18
FOLLOW_DELAY_MAX=30

# Approval timeout
APPROVAL_TIMEOUT_HOURS=4

# Discovery filters
DISCOVERY_MAX_FOLLOWERS=5000
DISCOVERY_MIN_FOLLOWING=100
DISCOVERY_ACTIVITY_DAYS=14
```

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check |
| GET | `/` | Service info |
| GET | `/status` | Current workflow state & progress |
| POST | `/trigger-follow` | Start follow workflow |
| POST | `/trigger-unfollow` | Start unfollow workflow |
| POST | `/cancel` | Cancel running workflow |
| GET | `/exports` | List all CSV exports |
| GET | `/export/{filename}` | Download a CSV file |

---

## Workflow States

| State | Meaning |
|-------|---------|
| `IDLE` | No workflow running |
| `DISCOVERING_TARGETS` | Finding new accounts to follow |
| `AWAITING_FOLLOW_APPROVAL` | Waiting for Telegram approval |
| `EXECUTING_FOLLOWS` | Following accounts |
| `FETCHING_FOLLOWING` | Fetching following list |
| `AWAITING_UNFOLLOW_APPROVAL` | Waiting for Telegram approval |
| `EXECUTING_UNFOLLOWS` | Unfollowing accounts |
| `COMPLETE` | Workflow finished successfully |
| `CANCELLED` | Workflow cancelled by user |
| `ERROR` | Workflow encountered an error |

---

## Example Usage Scenario

### 1. Start Server
```bash
cd ~/followflow && ./venv/bin/uvicorn app.main:app --port 8000
```

### 2. Trigger Follow
```bash
curl -X POST http://localhost:8000/trigger-follow
```

### 3. Approve in Telegram
Receive message with list of 50 accounts → Click **Approve**

### 4. Monitor Progress
```bash
watch -n 5 'curl -s http://localhost:8000/status | jq .progress'
```

Or check Telegram every 5 minutes for updates.

### 5. Download Results
```bash
curl http://localhost:8000/exports | jq '.exports[0].download_url'
# Output: "/export/follow_20260219_103000_abc12345.csv"

curl http://localhost:8000/export/follow_20260219_103000_abc12345.csv > results.csv
```

### 6. Cancel Anytime
```bash
curl -X POST http://localhost:8000/cancel
```

---

## Account Categories

Detected automatically from account bio/username:

- **dogs** — Dog-related accounts (puppy, GSD, Husky, etc.)
- **cats** — Cat-related accounts
- **pets** — General pet accounts
- **photography** — Photography/photographer
- **travel** — Travel/wanderlust
- **fitness** — Fitness/gym/yoga
- **food** — Food/cooking/recipes
- **lifestyle** — Lifestyle/daily life
- **art** — Art/illustration/design
- **entertainment** — Entertainment/comedy/music
- **other** — No clear category

---

## Region Detection

Detected from bio text, flag emojis, and location keywords:

- **NA** — North America (US, Canada)
- **EU** — Europe (UK, Germany, France, etc.)
- **JP** — Japan
- **KR** — South Korea
- **AU** — Australia/Oceania
- **UNKNOWN** — Region could not be determined

---

## Troubleshooting

### "Telegram bot not initialized"
- Check `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`
- Restart the server

### Workflow hangs on approval
- Check Telegram — you should receive an approval request message
- If message doesn't arrive, check bot token and chat ID
- Manually cancel with `/cancel` command

### Rate limited (429)
- Discovery will pause and retry
- Follow/unfollow will pause 15 minutes then retry
- Check `.env` delay settings if pauses are too frequent

### CSV file not found
- Check `/exports` endpoint to see all available files
- Filename format: `{action}_{timestamp}_{batch_id}.csv`
- Example: `follow_20260219_103000_abc12345.csv`

---

## Notes

- All workflows support **mid-process cancellation** — you can stop anytime
- **5-minute progress updates** sent via Telegram during execution
- **CSV exports** include all metadata (region, category, timestamps)
- **No scheduled jobs** — everything is manual trigger via API or Telegram
- **Human approval required** before any follows/unfollows execute
- **Rate limits respected** — delays between actions to avoid Instagram blocks

---

For issues or questions, check logs:
```bash
tail -f followflow.log
```
