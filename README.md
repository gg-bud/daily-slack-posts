# Daily Slack Posts

Posts the NASA Astronomy Picture of the Day and Wikiquote Quote of the Day to Slack every morning at 9 AM Eastern.

## Setup

1. Create a Slack app with `chat:write` scope and install it to your workspace
2. Invite the bot to your target channel
3. Set repository secrets: `SLACK_BOT_TOKEN`, `SLACK_CHANNEL_ID`, `NASA_API_KEY`

## Local Usage

Copy `.env.example` to `.env`, fill in your values, then:

```bash
pip install -r requirements.txt
python daily_slack_post.py
```
