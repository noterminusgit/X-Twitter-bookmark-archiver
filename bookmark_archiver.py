#!/usr/bin/env python3
"""
Twitter/X Bookmark Archiver
Downloads bookmarks as Markdown, PDFs, images, and videos with folder structure support.
Uses the X API v2 directly via requests.
"""

import os
import sys
import json
import time
import logging
import argparse
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set
import re

import requests
from requests_oauthlib import OAuth1
from dotenv import load_dotenv

# ── Logging ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bookmark_archiver.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

load_dotenv()

# ── Configuration ────────────────────────────────────────────────────────
OUTPUT_DIR = Path(os.getenv('OUTPUT_DIR', 'output'))
STATE_FILE = Path(os.getenv('STATE_FILE', '.archive_state.json'))

# Optional: scraped bookmarks JSON file from the Chrome extension
SCRAPED_BOOKMARKS_FILE = os.getenv('SCRAPED_BOOKMARKS_FILE')

# OAuth 1.0a credentials
TWITTER_API_KEY = os.getenv('TWITTER_API_KEY')
TWITTER_API_SECRET = os.getenv('TWITTER_API_SECRET')
TWITTER_ACCESS_TOKEN = os.getenv('TWITTER_ACCESS_TOKEN')
TWITTER_ACCESS_SECRET = os.getenv('TWITTER_ACCESS_SECRET')

X_API_BASE = 'https://api.x.com/2'

# Output directories
MARKDOWN_DIR = OUTPUT_DIR / 'markdown'
PDF_DIR = OUTPUT_DIR / 'pdf'
IMAGES_DIR = OUTPUT_DIR / 'images'
VIDEOS_DIR = OUTPUT_DIR / 'videos'

# Default output formats (env-configurable, comma-separated)
DEFAULT_FORMATS = os.getenv('OUTPUT_FORMATS', 'markdown,images,videos')


def parse_output_formats(formats_str: str) -> dict:
    """Parse a comma-separated format string into a dict of enabled formats."""
    available = {'markdown', 'pdf', 'images', 'videos'}
    enabled = {f.strip().lower() for f in formats_str.split(',')} & available
    return {fmt: (fmt in enabled) for fmt in available}


class XAPIClient:
    """Thin client for X API v2 using OAuth 1.0a User Context."""

    def __init__(self, api_key: str, api_secret: str,
                 access_token: str, access_secret: str):
        self.auth = OAuth1(api_key, api_secret, access_token, access_secret)
        self.session = requests.Session()
        self.session.auth = self.auth
        logger.info("X API client initialized (OAuth 1.0a)")

    # -- rate-limit helpers --------------------------------------------------

    def _handle_rate_limit(self, resp: requests.Response):
        """Sleep until the rate-limit window resets if we hit 429."""
        if resp.status_code != 429:
            return
        reset_ts = int(resp.headers.get('x-rate-limit-reset', 0))
        now = int(time.time())
        wait = max(reset_ts - now, 1) + 1  # +1s buffer
        logger.warning(f"Rate limited. Sleeping {wait}s until reset.")
        time.sleep(wait)

    def _get(self, url: str, params: dict = None) -> dict:
        """GET with automatic rate-limit retry (up to 3 attempts)."""
        for attempt in range(3):
            resp = self.session.get(url, params=params)
            if resp.status_code == 429:
                self._handle_rate_limit(resp)
                continue
            resp.raise_for_status()
            return resp.json()
        resp.raise_for_status()
        return resp.json()

    # -- public API methods --------------------------------------------------

    def get_me(self) -> dict:
        """Return the authenticated user object."""
        data = self._get(f'{X_API_BASE}/users/me')
        return data['data']

    def get_bookmarks(self, user_id: str) -> List[dict]:
        """Fetch all bookmarks (paginated, max 800)."""
        url = f'{X_API_BASE}/users/{user_id}/bookmarks'
        params = {
            'max_results': 100,
            'tweet.fields': 'created_at,author_id,conversation_id,'
                            'referenced_tweets,entities,attachments,'
                            'public_metrics,note_tweet,text',
            'expansions': 'author_id,attachments.media_keys,'
                          'referenced_tweets.id,'
                          'referenced_tweets.id.author_id',
            'media.fields': 'url,preview_image_url,type,alt_text,'
                            'variants,height,width',
            'user.fields': 'name,username,profile_image_url',
        }

        bookmarks: List[dict] = []
        while True:
            body = self._get(url, params)
            data = body.get('data', [])
            includes = body.get('includes', {})
            users = {u['id']: u for u in includes.get('users', [])}
            media_list = includes.get('media', [])

            for tweet in data:
                author = users.get(tweet.get('author_id'), {})
                bookmarks.append({
                    'id': tweet['id'],
                    'text': tweet.get('text', ''),
                    'author': {
                        'id': author.get('id'),
                        'username': author.get('username', 'unknown'),
                        'name': author.get('name', 'Unknown'),
                    },
                    'created_at': tweet.get('created_at'),
                    'conversation_id': tweet.get('conversation_id'),
                    'referenced_tweets': tweet.get('referenced_tweets'),
                    'media': media_list,
                    'folder': 'default',
                })

            next_token = body.get('meta', {}).get('next_token')
            if not next_token:
                break
            params['pagination_token'] = next_token

        logger.info(f"Fetched {len(bookmarks)} bookmarks")
        return bookmarks

    def search_conversation(self, conversation_id: str) -> List[dict]:
        """Fetch tweets belonging to a conversation (thread)."""
        url = f'{X_API_BASE}/tweets/search/recent'
        params = {
            'query': f'conversation_id:{conversation_id}',
            'max_results': 100,
            'tweet.fields': 'created_at,author_id,conversation_id',
            'expansions': 'author_id',
            'user.fields': 'name,username',
        }

        body = self._get(url, params)
        data = body.get('data', [])
        includes = body.get('includes', {})
        users = {u['id']: u for u in includes.get('users', [])}

        tweets = []
        for tweet in data:
            author = users.get(tweet.get('author_id'), {})
            tweets.append({
                'id': tweet['id'],
                'text': tweet.get('text', ''),
                'author': {
                    'username': author.get('username', 'unknown'),
                    'name': author.get('name', 'Unknown'),
                },
                'created_at': tweet.get('created_at'),
            })

        # Sort chronologically
        tweets.sort(key=lambda t: t.get('created_at', ''))
        return tweets


# ═══════════════════════════════════════════════════════════════════════════
#  Bookmark Archiver
# ═══════════════════════════════════════════════════════════════════════════

class BookmarkArchiver:
    def __init__(self, formats: dict, scraped_file: Optional[str] = None):
        """Initialize the bookmark archiver.

        Args:
            formats: dict mapping format names to booleans, e.g.
                     {'markdown': True, 'pdf': False, 'images': True, 'videos': True}
            scraped_file: Optional path to a JSON file of scraped bookmarks
                          from the Chrome extension. Tweets in this file
                          are skipped during API processing.
        """
        self.formats = formats
        self.state = self._load_state()
        self.api = XAPIClient(
            TWITTER_API_KEY, TWITTER_API_SECRET,
            TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_SECRET,
        )
        self.playwright = None
        self.browser = None
        self.context = None

        # Load scraped bookmark IDs (from Chrome extension DOM scrape)
        self.scraped_ids: Set[str] = set()
        self._load_scraped_bookmarks(scraped_file)

        # Create required output directories
        dirs = []
        if formats.get('markdown'):
            dirs.append(MARKDOWN_DIR)
        if formats.get('pdf'):
            dirs.append(PDF_DIR)
        if formats.get('images'):
            dirs.append(IMAGES_DIR)
        if formats.get('videos'):
            dirs.append(VIDEOS_DIR)
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)

    def _load_state(self) -> Dict:
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load state file: {e}")
                return {'processed_tweets': {}, 'last_run': None}
        return {'processed_tweets': {}, 'last_run': None}

    def _load_scraped_bookmarks(self, scraped_file: Optional[str]):
        """Load scraped bookmark IDs from Chrome extension JSON export.

        The scraped JSON is an array of objects, each with at least
        a 'tweetId' field. This file comes from the DOM-scraping
        Chrome extension and captures bookmarks the API doesn't return.
        """
        if not scraped_file:
            return
        scraped_path = Path(scraped_file)
        if not scraped_path.exists():
            logger.warning(f"Scraped bookmarks file not found: {scraped_file}")
            return
        try:
            with open(scraped_path, 'r') as f:
                data = json.load(f)
            if isinstance(data, dict):
                # Could be wrapped in an object with a bookmarks key
                items = data.get('bookmarks', data.get('tweets',
                            data.get('data', [data])))
                if isinstance(items, dict):
                    items = [items]
            elif isinstance(data, list):
                items = data
            else:
                items = []

            count = 0
            for item in items:
                if not isinstance(item, dict):
                    continue
                tid = item.get('tweetId') or item.get('id') or item.get('tweet_id')
                if tid:
                    self.scraped_ids.add(str(tid))
                    count += 1

            logger.info(
                f"Loaded {count} scraped bookmark IDs from {scraped_file}"
                f" ({len(self.scraped_ids)} unique)"
            )
        except Exception as e:
            logger.error(f"Failed to load scraped bookmarks file: {e}")

    def _save_state(self):
        try:
            self.state['last_run'] = datetime.now(timezone.utc).isoformat()
            with open(STATE_FILE, 'w') as f:
                json.dump(self.state, f, indent=2)
            logger.info("State saved successfully")
        except Exception as e:
            logger.error(f"Failed to save state: {e}")

    # -- browser (only initialised when PDF or images are enabled) -----------

    def _needs_browser(self) -> bool:
        return self.formats.get('pdf') or self.formats.get('images')

    def _init_browser(self):
        if not self._needs_browser():
            return
        if not self.playwright:
            from playwright.sync_api import sync_playwright
            self.playwright = sync_playwright().start()
            self.browser = self.playwright.chromium.launch(headless=True)
            self.context = self.browser.new_context(
                viewport={'width': 1280, 'height': 1024},
                device_scale_factor=2,
            )
            logger.info("Browser initialized")

    def _close_browser(self):
        if self.context:
            self.context.close()
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()
        self.playwright = self.browser = self.context = None

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _sanitize_filename(text: str) -> str:
        text = re.sub(r'[<>:\"/\\|?*]', '_', text)
        return text[:100]

    def _get_folder_path(self, folder_name: Optional[str], base_dir: Path) -> Path:
        if folder_name and folder_name != 'default':
            folder_path = base_dir / BookmarkArchiver._sanitize_filename(folder_name)
            folder_path.mkdir(parents=True, exist_ok=True)
            return folder_path
        return base_dir

    @staticmethod
    def _format_timestamp(ts: Optional[str]) -> str:
        """Turn an ISO timestamp into a readable string."""
        if not ts:
            return ''
        try:
            dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
            return dt.strftime('%Y-%m-%d %H:%M UTC')
        except Exception:
            return ts

    # -- Markdown rendering --------------------------------------------------

    def _render_markdown(self, tweet_data: dict, is_thread: bool = False,
                         thread_tweets: List[dict] = None) -> str:
        """Render a single tweet or thread as Markdown."""
        lines: List[str] = []

        if is_thread and thread_tweets:
            author = thread_tweets[0].get('author', {})
            name = author.get('name', 'Unknown')
            username = author.get('username', 'unknown')
            total = len(thread_tweets)

            lines.append(f'# Thread by @{username} \u2014 {name}')
            lines.append('')

            for i, tweet in enumerate(thread_tweets, 1):
                t_author = tweet.get('author', {})
                t_name = t_author.get('name', name)
                t_username = t_author.get('username', username)
                ts = self._format_timestamp(tweet.get('created_at'))

                lines.append(f'## {i}/{total}')
                if t_username != username:
                    lines.append(f'**@{t_username}** \u2014 {t_name}')
                if ts:
                    lines.append(f'*{ts}*')
                lines.append('')
                lines.append(tweet.get('text', ''))
                lines.append('')
        else:
            author = tweet_data.get('author', {})
            name = author.get('name', 'Unknown')
            username = author.get('username', 'unknown')
            ts = self._format_timestamp(tweet_data.get('created_at'))

            lines.append(f'# @{username} \u2014 {name}')
            lines.append('')
            if ts:
                lines.append(f'*{ts}*')
                lines.append('')
            lines.append('---')
            lines.append('')
            lines.append(tweet_data.get('text', ''))
            lines.append('')

        # Footer
        tweet_id = tweet_data['id']
        author = tweet_data.get('author', {})
        username = author.get('username', 'unknown')
        lines.append('---')
        lines.append('')
        lines.append(f'[View original](https://x.com/{username}/status/{tweet_id})')
        lines.append('')

        return '\n'.join(lines)

    def _save_markdown(self, content: str, output_path: Path):
        try:
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(content)
            logger.info(f"Saved Markdown: {output_path}")
        except Exception as e:
            logger.error(f"Failed to save Markdown {output_path}: {e}")

    # -- HTML rendering (for PDF / image output) -----------------------------

    def _render_tweet_html(self, tweet_data: dict, is_thread: bool = False,
                           thread_tweets: List[dict] = None) -> str:
        html_parts = []
        html_parts.append("""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
                         Helvetica, Arial, sans-serif;
            max-width: 600px;
            margin: 40px auto;
            padding: 20px;
            background: #f7f9fa;
            color: #0f1419;
        }
        .tweet {
            background: white;
            border: 1px solid #e1e8ed;
            border-radius: 16px;
            padding: 20px;
            margin-bottom: 20px;
        }
        .header {
            display: flex;
            align-items: center;
            margin-bottom: 12px;
        }
        .author-name {
            font-weight: bold;
            margin-right: 5px;
        }
        .username {
            color: #536471;
            font-size: 14px;
        }
        .content {
            font-size: 16px;
            line-height: 1.5;
            margin: 12px 0;
            white-space: pre-wrap;
            word-wrap: break-word;
        }
        .timestamp {
            color: #536471;
            font-size: 14px;
            margin-top: 12px;
        }
        .thread-indicator {
            background: #1d9bf0;
            color: white;
            padding: 8px 16px;
            border-radius: 20px;
            margin-bottom: 20px;
            text-align: center;
            font-weight: bold;
        }
        .media {
            margin-top: 12px;
        }
        .media img {
            max-width: 100%;
            border-radius: 12px;
        }
        .metrics {
            margin-top: 10px;
            font-size: 13px;
            color: #536471;
        }
        .metrics span {
            margin-right: 16px;
        }
        a { color: #1d9bf0; text-decoration: none; }
        a:hover { text-decoration: underline; }
    </style>
</head>
<body>
""")

        if is_thread and thread_tweets:
            html_parts.append(
                '<div class="thread-indicator">Thread</div>'
            )
            for tweet in thread_tweets:
                author = tweet.get('author', {}) or {}
                html_parts.append(f"""
<div class="tweet">
    <div class="header">
        <span class="author-name">{author.get('name', 'Unknown')}</span>
        <span class="username">@{author.get('username', 'unknown')}</span>
    </div>
    <div class="content">{self._escape_html(tweet.get('text', ''))}</div>
    <div class="timestamp">{tweet.get('created_at', '')}</div>
</div>
""")
        else:
            author = tweet_data.get('author', {}) or {}
            metrics = tweet_data.get('metrics', {})
            metrics_html = ""
            if metrics:
                parts = []
                if metrics.get('like_count') is not None:
                    parts.append(f"❤️ {metrics['like_count']}")
                if metrics.get('retweet_count') is not None:
                    parts.append(f"🔁 {metrics['retweet_count']}")
                if metrics.get('reply_count') is not None:
                    parts.append(f"💬 {metrics['reply_count']}")
                if parts:
                    metrics_html = '<div class="metrics">' + '  '.join(parts) + '</div>'

            # Media
            media_html = ""
            for m in tweet_data.get('media', []):
                if m.get('type') == 'photo' and m.get('url'):
                    media_html += f'\n    <img src="{m["url"]}" alt="{m.get("alt_text", "")}" />'
                elif m.get('type') == 'video' and m.get('preview_image_url'):
                    media_html += (
                        f'\n    <img src="{m["preview_image_url"]}" '
                        f'alt="Video thumbnail" />'
                    )

            tweet_url = (
                f'https://x.com/{author.get("username", "unknown")}'
                f'/status/{tweet_data.get("id", "")}'
            )

            html_parts.append(f"""
<div class="tweet">
    <div class="header">
        <span class="author-name">{author.get('name', 'Unknown')}</span>
        <span class="username">@{author.get('username', 'unknown')}</span>
    </div>
    <div class="content">{self._escape_html(tweet_data.get('text', ''))}</div>
    {metrics_html}
    <div class="media">{media_html}
    </div>
    <div class="timestamp">
        {tweet_data.get('created_at', '')} &middot;
        <a href="{tweet_url}" target="_blank">View on X</a>
    </div>
</div>
""")

        html_parts.append("</body></html>")
        return '\n'.join(html_parts)

    @staticmethod
    def _escape_html(text: str) -> str:
        """Escape HTML special characters."""
        if not text:
            return ''
        return (text
                .replace('&', '&amp;')
                .replace('<', '&lt;')
                .replace('>', '&gt;')
                .replace('"', '&quot;'))

    # -- PDF / image saving --------------------------------------------------

    def _save_as_pdf(self, html_content: str, output_path: Path):
        try:
            page = self.context.new_page()
            page.set_content(html_content)
            page.pdf(path=str(output_path), format='A4')
            page.close()
            logger.info(f"Saved PDF: {output_path}")
        except Exception as e:
            logger.error(f"Failed to save PDF {output_path}: {e}")

    def _save_as_image(self, html_content: str, output_path: Path):
        try:
            page = self.context.new_page()
            page.set_content(html_content)
            page.wait_for_timeout(500)
            page.screenshot(path=str(output_path), full_page=True)
            page.close()
            logger.info(f"Saved image: {output_path}")
        except Exception as e:
            logger.error(f"Failed to save image {output_path}: {e}")

    # -- video handling ------------------------------------------------------

    def _has_video(self, tweet_data: dict) -> bool:
        for item in tweet_data.get('media', []):
            mtype = item.get('type') if isinstance(item, dict) else getattr(item, 'type', None)
            if mtype in ('video', 'animated_gif'):
                return True
        return False

    def _save_video_html(self, tweet_data: dict, output_path: Path):
        try:
            tweet_id = tweet_data['id']
            author = tweet_data.get('author', {}) or {}
            username = author.get('username', 'unknown')

            html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Tweet {tweet_id}</title>
    <style>
        body {{
            font-family: Arial, sans-serif;
            max-width: 800px;
            margin: 40px auto;
            padding: 20px;
        }}
        .info {{
            background: #f0f0f0;
            padding: 15px;
            border-radius: 8px;
            margin-bottom: 20px;
        }}
        code {{
            background: #e0e0e0;
            padding: 2px 6px;
            border-radius: 4px;
        }}
    </style>
</head>
<body>
    <div class="info">
        <h2>Video Tweet</h2>
        <p><strong>Author:</strong> {author.get('name', 'Unknown')} (@{username})</p>
        <p><strong>Tweet ID:</strong> {tweet_id}</p>
        <p><strong>Link:</strong> <a href="https://x.com/{username}/status/{tweet_id}" target="_blank">View on X</a></p>
        <p><strong>Text:</strong> {self._escape_html(tweet_data.get('text', ''))}</p>
    </div>
    <p>Note: Download the video using yt-dlp or visit the link above to watch.</p>
    <p>Command: <code>yt-dlp https://x.com/{username}/status/{tweet_id}</code></p>
</body>
</html>"""

            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(html_content)
            logger.info(f"Saved video HTML: {output_path}")

            self._download_video(tweet_id, username, output_path.parent)

        except Exception as e:
            logger.error(f"Failed to save video HTML {output_path}: {e}")

    def _download_video(self, tweet_id: str, username: str, output_dir: Path):
        try:
            import yt_dlp
            url = f"https://x.com/{username}/status/{tweet_id}"
            ydl_opts = {
                'outtmpl': str(output_dir / f'{tweet_id}_%(title)s.%(ext)s'),
                'quiet': True,
                'no_warnings': True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            logger.info(f"Downloaded video for tweet {tweet_id}")
        except ImportError:
            logger.warning("yt-dlp not installed -- skipping video download")
        except Exception as e:
            logger.warning(f"Failed to download video for tweet {tweet_id}: {e}")

    # -- thread fetching -----------------------------------------------------

    def _get_thread_tweets(self, tweet_id: str, conversation_id: str) -> List[dict]:
        try:
            tweets = self.api.search_conversation(conversation_id)
            return tweets if len(tweets) > 1 else []
        except Exception as e:
            logger.warning(f"Failed to fetch thread for tweet {tweet_id}: {e}")
            return []

    # -- bookmark processing -------------------------------------------------

    def process_bookmark(self, bookmark: dict):
        tweet_id = str(bookmark['id'])

        if tweet_id in self.state['processed_tweets']:
            logger.debug(f"Skipping already processed tweet {tweet_id}")
            return

        # Skip tweets already captured by the DOM-scraping Chrome extension
        if tweet_id in self.scraped_ids:
            logger.info(f"Skipping tweet {tweet_id} \u2014 already in scraped bookmarks file")
            self.state['processed_tweets'][tweet_id] = {
                'processed_at': datetime.now(timezone.utc).isoformat(),
                'folder': bookmark.get('folder', 'default'),
                'is_thread': False,
                'source': 'scraped_file',
            }
            return

        logger.info(f"Processing tweet {tweet_id}")

        try:
            folder_name = bookmark.get('folder', 'default')

            # Thread detection
            is_thread = False
            thread_tweets: List[dict] = []

            if bookmark.get('conversation_id') and str(bookmark['conversation_id']) != tweet_id:
                thread_tweets = self._get_thread_tweets(tweet_id, bookmark['conversation_id'])
                is_thread = len(thread_tweets) > 0

            # -- Markdown ---------------------------------------------------
            if self.formats.get('markdown'):
                md_content = self._render_markdown(bookmark, is_thread, thread_tweets)
                md_folder = self._get_folder_path(folder_name, MARKDOWN_DIR)
                self._save_markdown(md_content, md_folder / f"{tweet_id}.md")

            # -- PDF / Image (require HTML) ---------------------------------
            if self.formats.get('pdf') or self.formats.get('images'):
                html_content = self._render_tweet_html(bookmark, is_thread, thread_tweets)

                if self.formats.get('pdf'):
                    pdf_folder = self._get_folder_path(folder_name, PDF_DIR)
                    self._save_as_pdf(html_content, pdf_folder / f"{tweet_id}.pdf")

                if self.formats.get('images'):
                    img_folder = self._get_folder_path(folder_name, IMAGES_DIR)
                    self._save_as_image(html_content, img_folder / f"{tweet_id}.png")

            # -- Video ------------------------------------------------------
            if self.formats.get('videos') and self._has_video(bookmark):
                vid_folder = self._get_folder_path(folder_name, VIDEOS_DIR)
                self._save_video_html(bookmark, vid_folder / f"{tweet_id}.html")

            # Mark processed
            self.state['processed_tweets'][tweet_id] = {
                'processed_at': datetime.now(timezone.utc).isoformat(),
                'folder': folder_name,
                'is_thread': is_thread,
            }

            time.sleep(1)

        except Exception as e:
            logger.error(f"Failed to process tweet {tweet_id}: {e}")

    def run(self):
        try:
            logger.info("Starting bookmark archiver...")
            enabled = [k for k, v in self.formats.items() if v]
            logger.info(f"Enabled output formats: {', '.join(enabled)}")

            # Report scraped bookmarks status
            if self.scraped_ids:
                logger.info(
                    f"Loaded {len(self.scraped_ids)} scraped bookmark IDs \u2014 "
                    f"these will be skipped during API processing"
                )

            self._init_browser()

            me = self.api.get_me()
            bookmarks = self.api.get_bookmarks(me['id'])

            api_count = len(bookmarks)
            scraped_skip_count = len(
                [b for b in bookmarks if str(b['id']) in self.scraped_ids]
            )
            logger.info(
                f"Fetched {api_count} bookmarks via API, "
                f"{scraped_skip_count} already in scraped file"
            )

            for bookmark in bookmarks:
                self.process_bookmark(bookmark)

            self._save_state()
            logger.info(f"Archiving complete. Processed {len(bookmarks)} bookmarks.")

        except Exception as e:
            logger.error(f"Error during execution: {e}")
            raise
        finally:
            self._close_browser()


# ═══════════════════════════════════════════════════════════════════════════
#  Entry Points
# ═══════════════════════════════════════════════════════════════════════════

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Archive X/Twitter bookmarks in multiple formats.',
    )
    parser.add_argument(
        '--formats',
        type=str,
        default=None,
        help='Comma-separated output formats: markdown,pdf,images,videos '
             '(default: from OUTPUT_FORMATS env var, or "markdown,images,videos")',
    )
    parser.add_argument(
        '--markdown', action='store_true', default=None,
        help='Enable Markdown output',
    )
    parser.add_argument(
        '--pdf', action='store_true', default=None,
        help='Enable PDF output',
    )
    parser.add_argument(
        '--images', action='store_true', default=None,
        help='Enable screenshot (PNG) output',
    )
    parser.add_argument(
        '--videos', action='store_true', default=None,
        help='Enable video download output',
    )
    parser.add_argument(
        '--no-markdown', action='store_true', default=False,
        help='Disable Markdown output',
    )
    parser.add_argument(
        '--no-pdf', action='store_true', default=False,
        help='Disable PDF output',
    )
    parser.add_argument(
        '--no-images', action='store_true', default=False,
        help='Disable screenshot output',
    )
    parser.add_argument(
        '--no-videos', action='store_true', default=False,
        help='Disable video download output',
    )
    parser.add_argument(
        '--scraped', type=str, default=None,
        help='Path to a scraped bookmarks JSON file from the Chrome extension. '
             'Tweets listed in this file are skipped during API processing '
             'since they were already captured via DOM scraping. '
             'Can also be set via the SCRAPED_BOOKMARKS_FILE env var.',
    )
    return parser


def resolve_formats(args: argparse.Namespace) -> dict:
    """Merge --formats string, individual flags, and env var into a final dict."""
    if args.formats:
        formats = parse_output_formats(args.formats)
    else:
        formats = parse_output_formats(DEFAULT_FORMATS)

    # Individual --<format> flags override to True
    for fmt in ('markdown', 'pdf', 'images', 'videos'):
        if getattr(args, fmt) is True:
            formats[fmt] = True

    # --no-<format> flags override to False
    for fmt in ('markdown', 'pdf', 'images', 'videos'):
        if getattr(args, f'no_{fmt}'):
            formats[fmt] = False

    return formats


def main():
    parser = build_arg_parser()
    args = parser.parse_args()
    formats = resolve_formats(args)

    if not any(formats.values()):
        logger.error("No output formats enabled. Use --formats or individual flags.")
        return 1

    required_creds = [TWITTER_API_KEY, TWITTER_API_SECRET,
                      TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_SECRET]
    if not all(required_creds):
        logger.error("Missing required environment variables. Please check your .env file.")
        logger.error("Required: TWITTER_API_KEY, TWITTER_API_SECRET, "
                      "TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_SECRET")
        return 1

    # Determine scraped bookmarks file (CLI arg > env var)
    scraped_path = args.scraped or SCRAPED_BOOKMARKS_FILE

    try:
        archiver = BookmarkArchiver(
            formats=formats,
            scraped_file=scraped_path,
        )
        archiver.run()
        return 0
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())