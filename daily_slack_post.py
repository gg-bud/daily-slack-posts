"""
Daily Slack Post: NASA APOD + Wikiquote Quote of the Day.

Fetches the NASA Astronomy Picture of the Day and the Wikiquote
Quote of the Day, then posts them as a single combined message to a
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
import time
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


def request_with_retries(url: str, timeout: int = 30, retries: int = 3,
                         backoff: float = 5.0, headers: dict = None) -> requests.Response:
    """Make a GET request with retry logic for transient failures (timeouts and 5xx)."""
    for attempt in range(1, retries + 1):
        try:
            response = requests.get(url, headers=headers, timeout=timeout)
            if response.status_code >= 500 and attempt < retries:
                wait = backoff * attempt
                print(f"  Server error {response.status_code} (attempt {attempt}/{retries})")
                print(f"  Retrying in {wait}s...")
                time.sleep(wait)
                continue
            return response
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            if attempt == retries:
                raise
            wait = backoff * attempt
            print(f"  Request failed (attempt {attempt}/{retries}): {e}")
            print(f"  Retrying in {wait}s...")
            time.sleep(wait)


def get_nasa_apod(api_key: str) -> dict:
    """Fetch NASA Astronomy Picture of the Day with HD URL and description."""
    url = f"https://api.nasa.gov/planetary/apod?api_key={api_key}"
    response = request_with_retries(url, timeout=30)
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
    response = request_with_retries(url, timeout=30, headers=headers)
    response.raise_for_status()
    api_data = response.json()
    if "error" in api_data:
        print(f"  Wikiquote API error: {api_data['error']}")
        return {"quote": "No quote available today.", "author": "Unknown", "author_link": ""}
    html = api_data["parse"]["text"]["*"]

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


def is_image_url_accessible(url: str) -> bool:
    """Check if an image URL can be fetched by an external service (like Slack)."""
    try:
        # Use a neutral user-agent to simulate what Slack's image fetcher would do
        resp = requests.head(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        return resp.status_code == 200
    except Exception:
        return False


def build_main_blocks(apod: dict, qotd: dict) -> list:
    """Build Slack Block Kit blocks for the combined APOD + Quote of the Day post."""
    blocks = []
    has_apod = bool(apod and apod.get("title"))
    has_qotd = bool(qotd)

    if has_apod:
        blocks.append({
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "NASA Astronomy Picture of the Day",
                "emoji": True,
            },
        })

        if apod["media_type"] == "image":
            image_url = apod["hdurl"] or apod["url"]
            if image_url and is_image_url_accessible(image_url):
                blocks.append({
                    "type": "image",
                    "image_url": image_url,
                    "alt_text": apod["title"],
                })
            elif image_url and apod["page_url"]:
                # Image not fetchable — link to the APOD page instead
                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f":telescope: *<{apod['page_url']}|View today's image: {apod['title']}>*",
                    },
                })
        elif apod["media_type"] == "video" and apod["url"]:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f":movie_camera: *<{apod['url']}|{apod['title']}>*",
                },
            })

        # APOD title link
        if apod["page_url"]:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*<{apod['page_url']}|{apod['title']}>*",
                },
            })

    # Divider between APOD and quote (only if both exist)
    if has_apod and has_qotd:
        blocks.append({"type": "divider"})

    # Quote of the Day
    if has_qotd:
        if qotd["author_link"]:
            attribution = f"— _<{qotd['author_link']}|{qotd['author']}>_"
        else:
            attribution = f"— _{qotd['author']}_"

        blocks.append({
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "Quote of the Day",
                "emoji": True,
            },
        })
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f">{qotd['quote']}\n{attribution}",
            },
        })

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

    # Per-workflow overrides via environment variables
    if os.environ.get("DISABLE_WIKIQUOTE", "").lower() in ("1", "true", "yes"):
        posts["wikiquote_qotd"] = False
    if os.environ.get("DISABLE_NASA_APOD", "").lower() in ("1", "true", "yes"):
        posts["nasa_apod"] = False

    errors = []

    apod = None
    qotd = None

    # Fetch NASA APOD
    if posts.get("nasa_apod", True):
        try:
            print("Fetching NASA APOD...")
            apod = get_nasa_apod(api_key)
            print(f"  Title: {apod['title']}")
            print(f"  Media: {apod['media_type']}")
            print(f"  HD URL: {apod['hdurl']}")
            print(f"  Page: {apod['page_url']}")
        except Exception as e:
            print(f"ERROR: NASA APOD fetch failed: {e}")
            errors.append(f"NASA APOD: {e}")

    # Fetch Wikiquote QOTD
    if posts.get("wikiquote_qotd", True):
        try:
            print("Fetching Wikiquote QOTD...")
            qotd = get_wikiquote_qotd()
            print(f"  Quote: {qotd['quote'][:80]}...")
            print(f"  Author: {qotd['author']}")
        except Exception as e:
            print(f"ERROR: Wikiquote QOTD fetch failed: {e}")
            errors.append(f"Wikiquote QOTD: {e}")

    # Build and send combined post
    if apod or qotd:
        try:
            # Fall back to empty stubs if one fetch failed
            apod = apod or {"title": "", "url": "", "hdurl": "", "explanation": "", "media_type": "image", "page_url": ""}
            qotd = qotd or {"quote": "No quote available today.", "author": "Unknown", "author_link": ""}

            print("Posting combined message to Slack...")
            main_blocks = build_main_blocks(apod, qotd)
            post_ts = post_slack_message(
                token, channel, main_blocks,
                text=f"NASA APOD: {apod['title']}",
                unfurl=False,
            )

            # Post APOD description as a thread reply
            if apod["explanation"]:
                print("Posting APOD description in thread...")
                thread_blocks = build_thread_blocks(apod)
                post_slack_message(
                    token, channel, thread_blocks,
                    text=apod["explanation"],
                    thread_ts=post_ts,
                    unfurl=False,
                )
        except Exception as e:
            print(f"ERROR: Slack post failed: {e}")
            errors.append(f"Slack post: {e}")

    if errors:
        print(f"\nCompleted with {len(errors)} error(s):")
        for err in errors:
            print(f"  - {err}")
        sys.exit(1)

    print("Done!")


if __name__ == "__main__":
    main()
