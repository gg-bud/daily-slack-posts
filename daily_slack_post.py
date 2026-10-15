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


SCIENCE_NASA_WP_API = "https://science.nasa.gov/wp-json/wp/v2"
# URLs containing this string are the NASA logo placeholder, not a real APOD image
NASA_LOGO_MARKER = "nasa-logo"


def _is_bad_apod_url(url: str) -> bool:
    """Return True if the URL looks like the NASA logo placeholder rather than an image."""
    return not url or NASA_LOGO_MARKER in url


def get_nasa_apod_from_science() -> dict | None:
    """
    Fallback: fetch today's APOD image from the science.nasa.gov WordPress REST API.

    Returns an APOD dict (same shape as get_nasa_apod) if today's post is found,
    or None if the post isn't available yet or the request fails.
    """
    today_str = str(date.today())  # YYYY-MM-DD

    # Step 1: search for this month's APOD posts.
    # Note: orderby=date returns [] for this custom post type — use search instead.
    today_obj = date.fromisoformat(today_str)
    month_lower = today_obj.strftime("%B").lower()  # e.g. "october"
    search_term = f"apod-{today_obj.year}-{month_lower}"
    list_url = (
        f"{SCIENCE_NASA_WP_API}/image-article"
        f"?per_page=5&search={search_term}"
        f"&_fields=id,date,slug,title,link,featured_media"
    )
    try:
        resp = request_with_retries(list_url, timeout=20)
        resp.raise_for_status()
        posts = resp.json()
    except Exception as e:
        print(f"  science.nasa.gov post list failed: {e}")
        return None

    if not posts:
        print(f"  science.nasa.gov returned no image-article posts for search '{search_term}'.")
        return None

    # Find the post for today specifically (results may include multiple days)
    post = None
    for candidate in posts:
        post_date = candidate.get("date", "")[:10]  # "YYYY-MM-DD"
        if post_date == today_str:
            post = candidate
            break

    if post is None:
        latest_date = posts[0].get("date", "")[:10] if posts else "none"
        print(f"  science.nasa.gov has no post for today ({today_str}). Latest is {latest_date}.")
        return None

    media_id = post.get("featured_media")
    page_url = post.get("link", "https://science.nasa.gov/apod/")
    title = post.get("title", {}).get("rendered", "NASA APOD")
    # Strip HTML entities like &#8211;
    import html as html_module
    title = html_module.unescape(title)
    # Trim the "APOD: YYYY Month DD – " prefix if present
    if title.startswith("APOD:"):
        parts = title.split("–", 1)
        if len(parts) == 2:
            title = parts[1].strip()

    if not media_id:
        print("  science.nasa.gov post has no featured_media.")
        return None

    # Step 2: fetch the media item to get the actual image URL
    media_url = (
        f"{SCIENCE_NASA_WP_API}/media/{media_id}"
        f"?_fields=source_url,alt_text,title"
    )
    try:
        resp = request_with_retries(media_url, timeout=20)
        resp.raise_for_status()
        media = resp.json()
    except Exception as e:
        print(f"  science.nasa.gov media fetch failed: {e}")
        return None

    image_url = media.get("source_url", "")
    if not image_url or _is_bad_apod_url(image_url):
        print(f"  science.nasa.gov media URL looks invalid: {image_url}")
        return None

    print(f"  Scraped image URL: {image_url}")
    print(f"  Scraped page URL: {page_url}")

    return {
        "title": title,
        "url": image_url,
        "hdurl": image_url,
        "explanation": "",  # explanation still comes from the NASA API when available
        "media_type": "image",
        "page_url": page_url,
    }


def get_nasa_apod(api_key: str) -> dict:
    """
    Fetch NASA Astronomy Picture of the Day.

    Tries the official NASA API first. If it returns a 5xx error or serves a
    placeholder logo URL instead of a real image, falls back to scraping
    the science.nasa.gov WordPress REST API for the image URL.
    """
    api_data = None
    api_error = None

    url = f"https://api.nasa.gov/planetary/apod?api_key={api_key}"
    try:
        response = request_with_retries(url, timeout=30)
        response.raise_for_status()
        api_data = response.json()
    except Exception as e:
        api_error = e
        print(f"  NASA API request failed: {e}")

    # Detect a bad API response: either failed outright, or returned the logo placeholder
    api_image_url = (api_data or {}).get("hdurl") or (api_data or {}).get("url", "")
    api_is_bad = api_data is None or _is_bad_apod_url(api_image_url)

    if api_is_bad:
        source = "API error" if api_data is None else f"placeholder URL ({api_image_url[:60]})"
        print(f"  NASA API unusable ({source}). Trying science.nasa.gov fallback...")
        scraped = get_nasa_apod_from_science()
        if scraped:
            # Prefer the API explanation text if we got it despite a bad image URL
            if api_data and api_data.get("explanation"):
                scraped["explanation"] = api_data["explanation"]
            return scraped
        elif api_data is None:
            # Both failed — re-raise the original error so the caller can log it
            raise api_error or RuntimeError("NASA APOD unavailable from all sources")
        else:
            # API returned garbage data and scrape also failed; raise to signal failure
            raise RuntimeError(
                f"NASA APOD API returned placeholder image and scrape fallback failed. "
                f"API URL was: {api_image_url}"
            )

    # API returned good data
    apod_date = api_data.get("date", str(date.today()))
    dt = datetime.strptime(apod_date, "%Y-%m-%d")
    apod_page_url = f"https://apod.nasa.gov/apod/ap{dt.strftime('%y%m%d')}.html"

    return {
        "title": api_data.get("title", "NASA APOD"),
        "url": api_data.get("url", ""),
        "hdurl": api_data.get("hdurl", api_data.get("url", "")),
        "explanation": api_data.get("explanation", ""),
        "media_type": api_data.get("media_type", "image"),
        "page_url": apod_page_url,
    }


def get_wikiquote_qotd() -> dict:
    """Fetch today's Quote of the Day from the Wikiquote individual daily page."""
    today = date.today()
    month_name = today.strftime("%B")
    day = today.day
    # Individual daily page (e.g. "Wikiquote:Quote_of_the_day/October_7,_2026") is
    # simpler to parse than the monthly page and is confirmed to exist before each day.
    page = f"Wikiquote:Quote_of_the_day/{month_name}_{day},_{today.year}"

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

    quote_text = ""
    author = ""
    author_link = ""

    # The daily page renders a single cquote table — no dt scanning needed
    cquote = soup.find("table", class_="cquote")
    if cquote:
        tds = cquote.find_all("td")
        if len(tds) >= 2:
            quote_text = tds[1].get_text(separator=" ", strip=True)

        # Author attribution is in the last row of the cquote table
        rows = cquote.find_all("tr")
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

    # Fallback if parsing failed — log details to help diagnose future failures
    if not quote_text:
        print(f"  WARNING: No quote text parsed from HTML. "
              f"cquote found={cquote is not None}")
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
            if image_url:
                blocks.append({
                    "type": "image",
                    "image_url": image_url,
                    "alt_text": apod["title"],
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
            print(f"WARNING: NASA APOD fetch failed: {e}")
            print("  Continuing without APOD — will post QotD only if available.")
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
    slack_posted = False
    if apod or qotd:
        try:
            # Only stub out apod/qotd if the fetch was attempted (i.e. enabled) but failed.
            # If a post type is disabled, leave it as None so build_main_blocks omits it.
            if posts.get("nasa_apod", True) and apod is None:
                apod = {"title": "", "url": "", "hdurl": "", "explanation": "", "media_type": "image", "page_url": ""}
            if posts.get("wikiquote_qotd", True) and qotd is None:
                qotd = {"quote": "No quote available today.", "author": "Unknown", "author_link": ""}

            print("Posting combined message to Slack...")
            main_blocks = build_main_blocks(apod, qotd)
            post_ts = post_slack_message(
                token, channel, main_blocks,
                text=f"NASA APOD: {apod['title']}",
                unfurl=False,
            )
            slack_posted = True

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

    # Summarise outcome.
    # Treat a NASA-only failure as a warning (exit 0) if the Slack post went out.
    # Only exit 1 if the Slack post itself failed or nothing was posted at all.
    slack_errors = [e for e in errors if e.startswith("Slack post")]
    non_slack_errors = [e for e in errors if not e.startswith("Slack post")]

    if slack_errors or not slack_posted:
        print(f"\nFailed with {len(errors)} error(s):")
        for err in errors:
            print(f"  - {err}")
        sys.exit(1)
    elif non_slack_errors:
        print(f"\nPosted successfully with {len(non_slack_errors)} warning(s):")
        for err in non_slack_errors:
            print(f"  - {err}")

    print("Done!")


if __name__ == "__main__":
    main()
