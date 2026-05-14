"""
Daily Slack Post: NASA APOD + Wikiquote Quote of the Day.

Fetches the NASA Astronomy Picture of the Day and the Wikiquote
Quote of the Day, then posts them as two separate messages to a
Slack channel. The APOD description is posted as a thread reply.

Configuration is loaded from config.yaml in the project root.

Required environment variables:
    SLACK_BOT_TOKEN   - Slack Bot User OAuth Token (xoxb-...)
    NASA_API_KEY      - NASA API key (use DEMO_KEY for testing)

Optional environment variables (override config.yaml):
    SLACK_CHANNEL_ID  - Slack channel ID to post to
"""

import os
import sys
import json
from datetime import date, datetime
from pathlib import Path

import yaml
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

WIKIQUOTE_UA = "DailySlackBot/1.0 (https://github.com/example; bot@example.com)"
CONFIG_PATH = Path(__file__).parent / "config.yaml"


def load_config() -> dict:
    """Load configuration from config.yaml with defaults."""
    defaults = {
        "channel_id": "",
        "schedule": {
            "hour": 9,
            "minute": 0,
            "timezone": "America/New_York",
        },
        "posts": {
            "nasa_apod": True,
            "wikiquote_qotd": True,
        },
    }

    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r") as f:
            file_config = yaml.safe_load(f) or {}
        # Merge: file values override defaults
        for key in defaults:
            if key in file_config:
                if isinstance(defaults[key], dict) and isinstance(file_config[key], dict):
                    defaults[key].update(file_config[key])
                else:
                    defaults[key] = file_config[key]

    return defaults


def get_nasa_apod(api_key: str) -> dict:
    """Fetch NASA Astronomy Picture of the Day with HD URL and description."""
    url = f"https://api.nasa.gov/planetary/apod?api_key={api_key}"
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    data = response.json()

    # Build the APOD website link (format: apYYMMDD.html)
    apod_date = data.get("date", str(date.today()))
    dt = datetime.strptime(apod_date, "%Y-%m-%d")
    apod_page_url = f"https://apod.nasa.gov/apod/ap{dt.strftime('%y%m%d')}.html"

    return {
        "title": data.get("title", "NASA APOD"),
        "url": data.get("url", ""),
        "hdurl": data.get("hdurl", data.get("url", "")),
        "explanation": data.get("explanation", ""),
        "media_type": data.get("media_type", "image"),
        "page_url": apod_page_url,
    }


def get_wikiquote_qotd() -> dict:
    """Fetch today's Quote of the Day from Wikiquote monthly page."""
    today = date.today()
    month_name = today.strftime("%B")
    day = today.day
    page = f"Wikiquote:Quote_of_the_day/{month_name}_{today.year}"

    url = (
        f"https://en.wikiquote.org/w/api.php"
        f"?action=parse&page={page}&prop=text&format=json"
    )
    headers = {"User-Agent": WIKIQUOTE_UA}
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    html = response.json()["parse"]["text"]["*"]

    soup = BeautifulSoup(html, "html.parser")

    # Find the dt element for today's date
    dts = soup.find_all("dt")
    target_dt = None
    for dt in dts:
        if dt.get_text(strip=True) == f"{month_name} {day}":
            target_dt = dt
            break

    quote_text = ""
    author = ""
    author_link = ""

    if target_dt:
        # The quote table follows the dl containing the dt
        dl = target_dt.parent
        table = dl.find_next_sibling("table")
        if table:
            # Quote is in the cquote table, middle td
            cquote = table.find("table", class_="cquote")
            if cquote:
                tds = cquote.find_all("td")
                if len(tds) >= 2:
                    quote_text = tds[1].get_text(separator=" ", strip=True)

            # Author is in the last row of the outer table
            rows = table.find_all("tr")
            if rows:
                author_row = rows[-1]
                links = author_row.find_all("a")
                for link in links:
                    href = link.get("href", "")
                    text = link.get_text(strip=True)
                    if text and "/wiki/" in href and ":" not in href:
                        author = text
                        author_link = f"https://en.wikiquote.org{href}"
                        break

    # Fallback if parsing failed
    if not quote_text:
        quote_text = "No quote available today."
    if not author:
        author = "Unknown"

    return {
        "quote": quote_text,
        "author": author,
        "author_link": author_link,
    }


def post_slack_message(token: str, channel: str, blocks: list, text: str,
                       thread_ts: str = None, unfurl: bool = False) -> str:
    """Post a message to Slack using chat.postMessage. Returns the message ts."""
    payload = {
        "channel": channel,
        "blocks": blocks,
        "text": text,
        "unfurl_links": unfurl,
        "unfurl_media": unfurl,
    }
    if thread_ts:
        payload["thread_ts"] = thread_ts

    response = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        data=json.dumps(payload),
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack API error: {data.get('error', 'unknown')}")
    return data["ts"]


def build_apod_blocks(apod: dict) -> list:
    """Build Slack Block Kit blocks for the NASA APOD message."""
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "NASA Astronomy Picture of the Day",
                "emoji": True,
            },
        },
    ]

    if apod["media_type"] == "image":
        # Use HD URL for higher resolution
        image_url = apod["hdurl"] or apod["url"]
        blocks.append({
            "type": "image",
            "image_url": image_url,
            "alt_text": apod["title"],
        })
    elif apod["media_type"] == "video":
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f":movie_camera: *<{apod['url']}|{apod['title']}>*",
            },
        })

    # Title with link to APOD page
    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f"*<{apod['page_url']}|{apod['title']}>*",
        },
    })

    return blocks


def build_quote_blocks(qotd: dict) -> list:
    """Build Slack Block Kit blocks for the Wikiquote message."""
    # Attribution line with link
    if qotd["author_link"]:
        attribution = f"— _<{qotd['author_link']}|{qotd['author']}>_"
    else:
        attribution = f"— _{qotd['author']}_"

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "Quote of the Day",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f">{qotd['quote']}\n{attribution}",
            },
        },
    ]

    return blocks


def build_thread_blocks(apod: dict) -> list:
    """Build blocks for the thread reply with the APOD description and links."""
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": apod["explanation"],
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*<{apod['page_url']}|{apod['title']}>*",
            },
        },
    ]
    return blocks


def main():
    # Load config
    config = load_config()

    # Read environment variables (env vars override config)
    token = os.environ.get("SLACK_BOT_TOKEN")
    channel = os.environ.get("SLACK_CHANNEL_ID") or config["channel_id"]
    api_key = os.environ.get("NASA_API_KEY", "DEMO_KEY")

    if not token:
        print("Error: SLACK_BOT_TOKEN environment variable is not set.")
        sys.exit(1)
    if not channel:
        print("Error: No channel ID set. Set SLACK_CHANNEL_ID env var or channel_id in config.yaml.")
        sys.exit(1)

    posts = config["posts"]

    # Fetch and post NASA APOD
    if posts.get("nasa_apod", True):
        print("Fetching NASA APOD...")
        apod = get_nasa_apod(api_key)
        print(f"  Title: {apod['title']}")
        print(f"  Media: {apod['media_type']}")
        print(f"  HD URL: {apod['hdurl']}")
        print(f"  Page: {apod['page_url']}")

        print("Posting NASA APOD to Slack...")
        apod_blocks = build_apod_blocks(apod)
        apod_ts = post_slack_message(
            token, channel, apod_blocks,
            text=f"NASA APOD: {apod['title']}",
            unfurl=False,
        )

        # Post description as a thread reply
        print("Posting description in thread...")
        thread_blocks = build_thread_blocks(apod)
        post_slack_message(
            token, channel, thread_blocks,
            text=apod["explanation"],
            thread_ts=apod_ts,
            unfurl=False,
        )

    # Fetch and post Wikiquote QOTD
    if posts.get("wikiquote_qotd", True):
        print("Fetching Wikiquote QOTD...")
        qotd = get_wikiquote_qotd()
        print(f"  Quote: {qotd['quote'][:80]}...")
        print(f"  Author: {qotd['author']}")

        print("Posting Quote of the Day to Slack...")
        quote_blocks = build_quote_blocks(qotd)
        post_slack_message(
            token, channel, quote_blocks,
            text=f"Quote of the Day: {qotd['quote']}",
            unfurl=False,
        )

    print("Done!")


if __name__ == "__main__":
    main()
