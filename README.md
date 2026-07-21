# Daily Slack Posts

Posts the NASA Astronomy Picture of the Day and Wikiquote Quote of the Day to Slack every morning.

## Setup

1. Create a Slack app with `chat:write` scope and install it to your workspace
2. Invite the bot to your target channel
3. Fork this repo
4. Add repository secrets: `SLACK_BOT_TOKEN`, `SLACK_CHANNEL_ID`, `NASA_API_KEY`
5. Edit `config.yaml` to customize your schedule and preferences

## Configuration

Edit `config.yaml` to change:

- **channel_id** — Slack channel ID (or set via `SLACK_CHANNEL_ID` secret)
- **schedule.hour / schedule.minute** — What time to post (24h format)
- **schedule.timezone** — Your timezone (e.g. `America/New_York`, `Europe/London`)
- **posts.nasa_apod** — Enable/disable the NASA picture
- **posts.wikiquote_qotd** — Enable/disable the quote

After changing the schedule, run:

```bash
python update_schedule.py
```

This converts your local time to UTC and updates the GitHub Actions cron. Commit and push the result.

## Local Usage

Copy `.env.example` to `.env`, fill in your values, then:

```bash
pip install -r requirements.txt
python daily_slack_post.py
```

## Workflows and Secrets

This repo runs two independent daily post workflows, each posting to a different Slack workspace. Each workflow has its own set of secrets — if you rename a workflow file, update the secret names it references too, and keep this table in sync.

| Workflow file | Slack workspace | Required secrets |
|---|---|---|
| `daily-post.yml` | Massachoderkansas (primary) | `SLACK_BOT_TOKEN`, `SLACK_CHANNEL_ID`, `NASA_API_KEY` |
| `daily-post-secondary.yml` | ExoHard | `SECONDARY_SLACK_BOT_TOKEN`, `SECONDARY_SLACK_CHANNEL_ID`, `NASA_API_KEY` |

Both workflows share the same `NASA_API_KEY`.

| Secret | Description |
|--------|-------------|
| `SLACK_BOT_TOKEN` / `SECONDARY_SLACK_BOT_TOKEN` | Bot User OAuth Token (`xoxb-...`) |
| `SLACK_CHANNEL_ID` / `SECONDARY_SLACK_CHANNEL_ID` | Channel ID to post to |
| `NASA_API_KEY` | NASA API key (get free at api.nasa.gov) |

Each workflow includes a "Verify required secrets" step that runs before the script and fails fast with a clear error naming the missing secret and which workflow needs it, instead of letting the Python script fail with a generic message.
