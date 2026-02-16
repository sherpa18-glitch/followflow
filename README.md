# FollowFlow

Instagram growth automation agent with human-in-the-loop Telegram approval.

FollowFlow runs a daily automated cycle that unfollows old accounts and follows new niche-relevant accounts, with every destructive action gated by your explicit approval via Telegram.

## How It Works

Each day at your configured time, FollowFlow runs a two-phase workflow:

1. **Unfollow Phase** — Fetches your Following list sorted by earliest followed, selects a batch (default 100), sends you a Telegram message asking for permission, waits for your Approve/Deny, then executes the unfollows with randomized delays.

2. **Follow Phase** — Discovers target accounts in the pet/dog niche by crawling hashtags and mining engagements, filters by criteria (< 2K followers, > 3K following, active in last 7 days), sends you a Telegram approval request, then executes follows with randomized delays.

Every action is logged to the database. Nothing happens without your approval.

## Tech Stack

- **Python 3.9+** / **FastAPI** — API server and scheduler host
- **Playwright** — Headless Chromium for Instagram browser automation
- **python-telegram-bot** — Notifications and inline keyboard approval
- **APScheduler** — Daily cron-triggered workflow
- **SQLAlchemy** + **SQLite** — Action/approval logging and blocklist
- **Docker** — Optional containerized deployment

## Quick Start

### Prerequisites

- Python 3.9+
- A Telegram bot token (create one via [@BotFather](https://t.me/BotFather))
- Your Telegram chat ID (use [@userinfobot](https://t.me/userinfobot))

### 1. Clone and install

```bash
git clone https://github.com/sherpa18-glitch/followflow.git
cd followflow
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
playwright install-deps chromium
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env` with your credentials:

```env
INSTAGRAM_USERNAME=your_instagram_username
INSTAGRAM_PASSWORD=your_instagram_password
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id
DAILY_SCHEDULE_TIME=09:00
```

### 3. Run the server

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The server starts the scheduler and Telegram bot automatically. Visit `http://localhost:8000/docs` for the API documentation.

### 4. Test manually

Trigger a workflow run without waiting for the schedule:

```bash
curl -X POST http://localhost:8000/trigger
```

Check workflow status:

```bash
curl http://localhost:8000/status
```

## Docker Deployment

```bash
# Build and run
docker compose up -d

# View logs
docker compose logs -f followflow

# Stop
docker compose down
```

The Docker setup persists the database and session cookies via volume mounts.

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `INSTAGRAM_USERNAME` | *(required)* | Your Instagram username |
| `INSTAGRAM_PASSWORD` | *(required)* | Your Instagram password |
| `TELEGRAM_BOT_TOKEN` | *(required)* | Telegram bot token from BotFather |
| `TELEGRAM_CHAT_ID` | *(required)* | Your Telegram chat/user ID |
| `DAILY_SCHEDULE_TIME` | `09:00` | Daily workflow trigger time (24h format) |
| `UNFOLLOW_BATCH_SIZE` | `100` | Accounts to unfollow per cycle |
| `FOLLOW_BATCH_SIZE` | `100` | Accounts to follow per cycle |
| `UNFOLLOW_DELAY_MIN` | `25` | Min seconds between unfollows |
| `UNFOLLOW_DELAY_MAX` | `45` | Max seconds between unfollows |
| `FOLLOW_DELAY_MIN` | `30` | Min seconds between follows |
| `FOLLOW_DELAY_MAX` | `60` | Max seconds between follows |
| `COOLDOWN_MINUTES_MIN` | `30` | Min minutes between unfollow/follow phases |
| `COOLDOWN_MINUTES_MAX` | `60` | Max minutes between unfollow/follow phases |
| `APPROVAL_TIMEOUT_HOURS` | `4` | Hours to wait for Telegram approval before auto-skipping |
| `DISCOVERY_MAX_FOLLOWERS` | `2000` | Max follower count for follow targets |
| `DISCOVERY_MIN_FOLLOWING` | `3000` | Min following count for follow targets |
| `DISCOVERY_ACTIVITY_DAYS` | `7` | Target must have been active within this many days |
| `DATABASE_URL` | `sqlite:///./followflow.db` | SQLAlchemy database URL |

## Telegram Bot Setup

1. Open Telegram and message [@BotFather](https://t.me/BotFather)
2. Send `/newbot` and follow the prompts to create your bot
3. Copy the bot token and set it as `TELEGRAM_BOT_TOKEN` in your `.env`
4. Message your new bot to start a conversation
5. Get your chat ID by messaging [@userinfobot](https://t.me/userinfobot) — set it as `TELEGRAM_CHAT_ID`

When the workflow runs, your bot will send you messages with Approve/Deny buttons. Tap to approve or deny each batch.

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Service info |
| `GET` | `/health` | Health check |
| `GET` | `/status` | Current workflow state |
| `POST` | `/trigger` | Manually trigger the daily workflow |
| `GET` | `/docs` | Interactive API documentation |

## Running Tests

```bash
source venv/bin/activate
pytest tests/ -v
```

## Project Structure

```
followflow/
├── app/
│   ├── main.py                 # FastAPI app + scheduler + lifespan
│   ├── config.py               # Pydantic Settings from .env
│   ├── models.py               # SQLAlchemy models (ActionLog, ApprovalLog, Blocklist)
│   ├── database.py             # DB engine + session factory
│   ├── instagram/
│   │   ├── browser.py          # Playwright session management
│   │   ├── auth.py             # Login + session persistence
│   │   ├── unfollow.py         # Unfollow action logic
│   │   ├── follow.py           # Follow action logic
│   │   └── discovery.py        # Niche account discovery engine
│   ├── telegram/
│   │   ├── bot.py              # Telegram bot notifications + approval
│   │   └── handlers.py         # Callback query handlers
│   ├── scheduler/
│   │   └── jobs.py             # Daily workflow orchestration
│   └── utils/
│       ├── rate_limiter.py     # Randomized delays + exponential backoff
│       └── logger.py           # Structured JSON logging
├── tests/                      # 123 tests
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── .env.example
```

## Safety Features

- **Human-in-the-loop**: Every unfollow/follow batch requires explicit Telegram approval
- **Rate limiting**: Randomized delays between actions (25-60s) to mimic human behavior
- **Cooldown periods**: 30-60 minute pause between unfollow and follow phases
- **Blocklist**: Unfollowed accounts are blocklisted to prevent re-following
- **Retry with backoff**: Network failures trigger exponential backoff (max 3 retries)
- **Session persistence**: Browser cookies are saved to avoid daily re-authentication
- **Approval timeout**: Auto-skips after 4 hours if no response (configurable)
