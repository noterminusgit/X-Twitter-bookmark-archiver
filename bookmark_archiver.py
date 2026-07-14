#!/usr/bin/env python3
"""
X/Twitter Bookmark Archiver
Downloads bookmarks as PDFs, images, and videos with folder structure support.

Uses OAuth 2.0 Authorization Code with PKCE (required by X API v2 bookmarks endpoint).
"""

import os
import json
import time
import logging
import argparse
import hashlib
import base64
import secrets
import webbrowser
import http.server
import urllib.parse
import threading
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Set

import tweepy
from playwright.sync_api import sync_playwright, Page
from dotenv import load_dotenv
import yt_dlp

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
TOKEN_FILE = Path(os.getenv('TOKEN_FILE', '.x_token.json'))

# Optional: scraped bookmarks JSON file from the Chrome extension
# When provided, tweets in this file are skipped during API enumeration
# since they were already captured via DOM scraping.
SCRAPED_BOOKMARKS_FILE = os.getenv('SCRAPED_BOOKMARKS_FILE')

# OAuth 2.0 PKCE credentials (REQUIRED — bookmarks endpoint requires this auth flow)
TWITTER_CLIENT_ID = os.getenv('TWITTER_CLIENT_ID')
TWITTER_CLIENT_SECRET = os.getenv('TWITTER_CLIENT_SECRET')  # optional for PKCE

# Default redirect URI for the local callback server
REDIRECT_URI = os.getenv('REDIRECT_URI', 'http://127.0.0.1:6006/callback')

# Directory structure
PDF_DIR = OUTPUT_DIR / 'pdf'
IMAGES_DIR = OUTPUT_DIR / 'images'
VIDEOS_DIR = OUTPUT_DIR / 'videos'


# ═══════════════════════════════════════════════════════════════════════════
#  OAuth 2.0 PKCE Auth Helper
# ═══════════════════════════════════════════════════════════════════════════

_CALLBACK_RECEIVED = threading.Event()
_CALLBACK_CODE = None


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Minimal HTTP server to catch the OAuth redirect."""

    def do_GET(self):
        global _CALLBACK_CODE, _CALLBACK_RECEIVED
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if 'code' in params:
            _CALLBACK_CODE = params['code'][0]
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(b'<html><body><h1>Authorization successful!</h1>'
                             b'<p>You can close this tab now.</p></body></html>')
            _CALLBACK_RECEIVED.set()
        else:
            self.send_response(400)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(b'<html><body><h1>Authorization failed</h1>'
                             b'<p>No authorization code received.</p></body></html>')

    def log_message(self, format, *args):
        pass  # silence HTTP server logs


def _generate_pkce_pair() -> tuple:
    """Generate PKCE code_verifier and code_challenge (S256)."""
    code_verifier = base64.urlsafe_b64encode(
        secrets.token_bytes(32)
    ).decode('utf-8').rstrip('=')
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode('utf-8')).digest()
    ).decode('utf-8').rstrip('=')
    return code_verifier, code_challenge


def _save_token(access_token: str, refresh_token: Optional[str] = None,
                expires_at: Optional[float] = None):
    """Persist OAuth token to disk."""
    data = {
        'access_token': access_token,
        'refresh_token': refresh_token,
        'expires_at': expires_at,
        'saved_at': time.time(),
    }
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(TOKEN_FILE, 'w') as f:
        json.dump(data, f, indent=2)
    # Restrict permissions
    TOKEN_FILE.chmod(0o600)
    logger.info(f"OAuth token saved to {TOKEN_FILE}")


def _load_token() -> Optional[Dict]:
    """Load persisted OAuth token."""
    if TOKEN_FILE.exists():
        try:
            with open(TOKEN_FILE) as f:
                data = json.load(f)
            if data.get('access_token'):
                return data
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to read token file: {e}")
    return None


def do_auth_flow() -> str:
    """Run the OAuth 2.0 Authorization Code with PKCE flow.

    Returns the access token string.
    Also accepts a pre-entered URL via stdin for headless environments.
    """
    if not TWITTER_CLIENT_ID:
        logger.error(
            "TWITTER_CLIENT_ID is not set. Please configure your .env file.\n"
            "Get a Client ID from https://developer.twitter.com/en/portal/dashboard\n"
            "Your app must have OAuth 2.0 with PKCE enabled and the redirect URI set to:\n"
            f"  {REDIRECT_URI}"
        )
        raise SystemExit(1)

    scope = [
        'bookmark.read',
        'tweet.read',
        'users.read',
        'offline.access',
    ]

    code_verifier, code_challenge = _generate_pkce_pair()
    handler = tweepy.OAuth2UserHandler(
        client_id=TWITTER_CLIENT_ID,
        redirect_uri=REDIRECT_URI,
        scope=scope,
        client_secret=TWITTER_CLIENT_SECRET or '',
    )

    # Build the auth URL manually so we can inject the PKCE challenge
    # (tweepy's OAuth2UserHandler handles PKCE internally if we pass
    #  code_verifier / code_challenge to fetch_token — but the auth URL
    #  must include code_challenge)

    params = {
        'response_type': 'code',
        'client_id': TWITTER_CLIENT_ID,
        'redirect_uri': REDIRECT_URI,
        'scope': ' '.join(scope),
        'state': secrets.token_urlsafe(16),
        'code_challenge': code_challenge,
        'code_challenge_method': 'S256',
    }
    auth_url = (
        'https://twitter.com/i/oauth2/authorize?'
        + urllib.parse.urlencode(params)
    )

    print("\n" + "=" * 70)
    print("  X/Twitter Authorization Required")
    print("=" * 70)
    print(f"\n1. Opening browser for authorization...")
    print(f"   URL: {auth_url}")
    print(f"\n   (If the browser doesn't open, copy & paste the URL above)")
    print(f"\n2. Log in to X/Twitter and authorize the application.")
    print(f"3. You will be redirected to {REDIRECT_URI}")
    print(f"\n   OR: Paste the full redirect URL below if auto-catch fails.\n")

    # Try to open the browser
    try:
        webbrowser.open(auth_url)
    except Exception:
        pass

    # Start a local callback server to catch the redirect
    server_started = threading.Event()
    server_error: List[Optional[str]] = [None]
    server_port = 6006

    def run_server():
        nonlocal server_port
        try:
            httpd = http.server.HTTPServer(
                ('127.0.0.1', server_port), _CallbackHandler
            )
            server_started.set()
            httpd.timeout = 0.5
            while not _CALLBACK_RECEIVED.is_set():
                httpd.handle_request()
            httpd.server_close()
        except Exception as e:
            server_error[0] = str(e)
            server_started.set()

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    if not server_started.wait(timeout=3):
        logger.warning("Could not start local callback server on port 6006")

    # Wait for callback with timeout — fallback to manual entry
    auth_code = None
    if _CALLBACK_RECEIVED.wait(timeout=120):
        auth_code = _CALLBACK_CODE
    else:
        # Prompt for manual entry
        print("\nAuto-catch didn't work. Paste the FULL redirect URL you were")
        print("redirected to (or just the ?code=... parameter):")
        user_input = input("> ").strip()
        if 'code=' in user_input:
            parsed = urllib.parse.urlparse(user_input)
            params = urllib.parse.parse_qs(parsed.query)
            auth_code = params.get('code', [None])[0]
        else:
            auth_code = user_input

    if not auth_code:
        logger.error("No authorization code received. Aborting.")
        raise SystemExit(1)

    # Exchange the code for a token
    token_data = handler.fetch_token(auth_code)
    access_token = token_data.get('access_token')
    if not access_token:
        logger.error(f"Failed to get access token. Response: {token_data}")
        raise SystemExit(1)

    _save_token(
        access_token=access_token,
        refresh_token=token_data.get('refresh_token'),
        expires_at=token_data.get('expires_at'),
    )

    print(f"\n✓ Authorization successful! Token saved to {TOKEN_FILE}\n")
    return access_token


# ═══════════════════════════════════════════════════════════════════════════
#  Bookmark Archiver
# ═══════════════════════════════════════════════════════════════════════════

class BookmarkArchiver:
    def __init__(self, access_token: str, scraped_file: Optional[str] = None):
        """Initialize the bookmark archiver.

        Args:
            access_token: OAuth 2.0 PKCE access token (User Context).
            scraped_file: Optional path to a JSON file of scraped bookmarks
                          from the Chrome extension. Tweets in this file
                          are skipped during API processing.
        """
        self.state = self._load_state()
        self.client = self._init_twitter_client(access_token)
        self.playwright = None
        self.browser = None
        self.context = None

        # Load scraped bookmark IDs (from Chrome extension DOM scrape)
        self.scraped_ids: Set[str] = set()
        self._load_scraped_bookmarks(scraped_file)

        # Create output directories
        for directory in [PDF_DIR, IMAGES_DIR, VIDEOS_DIR]:
            directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _init_twitter_client(access_token: str) -> tweepy.Client:
        """Initialize X API v2 client with OAuth 2.0 PKCE User Context token.

        The bookmarks endpoint REQUIRES OAuth 2.0 Authorization Code with PKCE.
        OAuth 1.0a and OAuth 2.0 Bearer Token (App-Only) will NOT work.

        Note: The token must be passed as bearer_token, then all API calls
        must use user_auth=False to use it as an OAuth 2.0 User Context token
        (not App-Only).
        """
        try:
            client = tweepy.Client(
                bearer_token=access_token,
                wait_on_rate_limit=True,
            )
            # Verify authentication works
            me = client.get_me(user_auth=False)
            if me.data:
                logger.info(f"Authenticated as @{me.data.username} (ID: {me.data.id})")
            return client
        except Exception as e:
            logger.error(f"Failed to initialize X API client: {e}")
            raise

    def _load_state(self) -> Dict:
        """Load the archive state from disk."""
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

        Args:
            scraped_file: Path to the scraped bookmarks JSON file, or None.
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
                items = data.get('bookmarks', data.get('tweets', data.get('data', [data])))
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
        """Save the archive state to disk."""
        try:
            self.state['last_run'] = datetime.now().isoformat()
            with open(STATE_FILE, 'w') as f:
                json.dump(self.state, f, indent=2)
            logger.info("State saved successfully")
        except Exception as e:
            logger.error(f"Failed to save state: {e}")

    def _init_browser(self):
        """Initialize Playwright browser for rendering."""
        if not self.playwright:
            self.playwright = sync_playwright().start()
            self.browser = self.playwright.chromium.launch(headless=True)
            self.context = self.browser.new_context(
                viewport={'width': 1280, 'height': 1024},
                device_scale_factor=2
            )
            logger.info("Browser initialized")

    def _close_browser(self):
        """Close Playwright browser."""
        if self.context:
            self.context.close()
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()
        logger.info("Browser closed")

    @staticmethod
    def _sanitize_filename(text: str) -> str:
        """Sanitize text for use in filenames."""
        text = re.sub(r'[<>:\"/\\\\|?*]', '_', text)
        return text[:100]

    @staticmethod
    def _get_folder_path(folder_name: Optional[str], base_dir: Path) -> Path:
        """Get the appropriate folder path based on bookmark folder."""
        if folder_name and folder_name != 'default':
            folder_path = base_dir / BookmarkArchiver._sanitize_filename(folder_name)
            folder_path.mkdir(parents=True, exist_ok=True)
            return folder_path
        return base_dir

    @staticmethod
    def _get_tweet_text(tweet) -> str:
        """Extract full tweet text, handling >280 char (note_tweet) tweets.

        X API v2 now supports posts longer than 280 characters via the
        'note_tweet' field. Fall back to the standard 'text' field otherwise.
        """
        # Some tweepy versions expose note_tweet as an object on the tweet
        note = getattr(tweet, 'note_tweet', None)
        if note:
            # note_tweet can be a dict or an object
            if isinstance(note, dict):
                text = note.get('text', tweet.text or '')
            else:
                text = getattr(note, 'text', tweet.text or '')
            if text:
                return text
        return tweet.text or ''

    def get_bookmarks(self) -> List[Dict]:
        """Fetch all bookmarks via X API v2."""
        logger.info("Fetching bookmarks...")
        bookmarks = []

        try:
            # Get authenticated user
            me = self.client.get_me(user_auth=False)
            user_id = me.data.id

            # Fetch bookmarks via API v2 bookmarks endpoint
            # Requires OAuth 2.0 Authorization Code with PKCE
            paginator = tweepy.Paginator(
                self.client.get_bookmarks,
                max_results=100,
                expansions=[
                    'author_id',
                    'referenced_tweets.id',
                    'attachments.media_keys',
                ],
                tweet_fields=[
                    'created_at',
                    'public_metrics',
                    'conversation_id',
                    'entities',
                    'note_tweet',       # long-form tweets (>280 chars)
                ],
                media_fields=[
                    'type',
                    'url',
                    'variants',
                    'preview_image_url',
                    'duration_ms',
                    'width',
                    'height',
                    'alt_text',
                ],
                user_fields=[
                    'username',
                    'name',
                    'profile_image_url',
                ],
            )

            for response in paginator:
                if response.data:
                    for tweet in response.data:
                        bookmark_data = {
                            'id': tweet.id,
                            'text': self._get_tweet_text(tweet),
                            'author': None,
                            'created_at': (
                                tweet.created_at.isoformat()
                                if tweet.created_at else None
                            ),
                            'conversation_id': tweet.conversation_id,
                            'referenced_tweets': tweet.referenced_tweets,
                            'media': [],
                            'folder': 'default',
                            'metrics': {},
                        }

                        # Public metrics
                        if hasattr(tweet, 'public_metrics') and tweet.public_metrics:
                            if isinstance(tweet.public_metrics, dict):
                                bookmark_data['metrics'] = tweet.public_metrics
                            else:
                                bookmark_data['metrics'] = {
                                    'like_count': tweet.public_metrics.get('like_count'),
                                    'retweet_count': tweet.public_metrics.get('retweet_count'),
                                    'reply_count': tweet.public_metrics.get('reply_count'),
                                    'quote_count': tweet.public_metrics.get('quote_count'),
                                }

                        # Extract author and media from includes
                        if hasattr(response, 'includes') and response.includes:
                            if 'users' in response.includes:
                                for user in response.includes['users']:
                                    if user.id == tweet.author_id:
                                        bookmark_data['author'] = {
                                            'id': user.id,
                                            'username': user.username,
                                            'name': user.name,
                                        }
                                        break

                            if 'media' in response.includes:
                                media_list = []
                                for m in response.includes['media']:
                                    media_item = {
                                        'media_key': m.get('media_key') if isinstance(m, dict) else m.media_key,
                                        'type': m.get('type') if isinstance(m, dict) else m.type,
                                        'url': m.get('url') if isinstance(m, dict) else getattr(m, 'url', None),
                                        'preview_image_url': (
                                            m.get('preview_image_url') if isinstance(m, dict)
                                            else getattr(m, 'preview_image_url', None)
                                        ),
                                        'alt_text': (
                                            m.get('alt_text') if isinstance(m, dict)
                                            else getattr(m, 'alt_text', None)
                                        ),
                                    }
                                    # Get best video variant
                                    variants = (
                                        m.get('variants') if isinstance(m, dict)
                                        else getattr(m, 'variants', None)
                                    )
                                    if variants:
                                        # Prefer highest bitrate MP4
                                        best = None
                                        for v in variants:
                                            v_dict = v if isinstance(v, dict) else v._asdict() if hasattr(v, '_asdict') else {}
                                            content_type = v_dict.get('content_type', '')
                                            bitrate = v_dict.get('bitrate', 0) or 0
                                            if 'mp4' in content_type and (
                                                best is None or bitrate > best[0]
                                            ):
                                                best = (bitrate, v_dict.get('url', ''))
                                        if best:
                                            media_item['video_url'] = best[1]
                                    media_list.append(media_item)
                                bookmark_data['media'] = media_list

                        # Check for thread membership
                        if tweet.referenced_tweets:
                            for ref in tweet.referenced_tweets:
                                if hasattr(ref, 'type') and ref.type == 'replied_to':
                                    pass  # part of a thread

                        bookmarks.append(bookmark_data)

            logger.info(f"Fetched {len(bookmarks)} bookmarks")
            return bookmarks

        except tweepy.TweepyException as e:
            logger.error(f"X API error fetching bookmarks: {e}")
            if '403' in str(e) or 'forbidden' in str(e).lower():
                logger.error(
                    "This endpoint requires OAuth 2.0 Authorization Code with PKCE.\n"
                    "Run 'python bookmark_archiver.py --auth' to re-authorize."
                )
            raise

    def _get_thread_tweets(self, tweet_id: str, conversation_id: str) -> List[Dict]:
        """Get all tweets in a conversation thread."""
        try:
            query = f"conversation_id:{conversation_id}"
            tweets = []

            response = self.client.search_recent_tweets(
                query=query,
                max_results=100,
                tweet_fields=['created_at', 'author_id', 'conversation_id', 'note_tweet'],
                expansions=['author_id'],
            )

            if response.data:
                sorted_tweets = sorted(response.data, key=lambda t: t.created_at)

                for tweet in sorted_tweets:
                    author = None
                    if hasattr(response, 'includes') and 'users' in response.includes:
                        for user in response.includes['users']:
                            if user.id == tweet.author_id:
                                author = {'username': user.username, 'name': user.name}
                                break

                    tweets.append({
                        'id': tweet.id,
                        'text': self._get_tweet_text(tweet),
                        'author': author,
                        'created_at': tweet.created_at.isoformat() if tweet.created_at else None,
                    })

            return tweets if len(tweets) > 1 else []

        except Exception as e:
            logger.warning(f"Failed to fetch thread for tweet {tweet_id}: {e}")
            return []

    def _render_tweet_html(self, tweet_data: Dict, is_thread: bool = False,
                           thread_tweets: List[Dict] = None) -> str:
        """Generate HTML representation of a tweet or thread."""
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

    def _save_as_pdf(self, html_content: str, output_path: Path):
        """Save HTML content as PDF using Playwright."""
        try:
            page = self.context.new_page()
            page.set_content(html_content)
            page.pdf(path=str(output_path), format='A4')
            page.close()
            logger.info(f"Saved PDF: {output_path}")
        except Exception as e:
            logger.error(f"Failed to save PDF {output_path}: {e}")

    def _save_as_image(self, html_content: str, output_path: Path):
        """Save HTML content as image using Playwright."""
        try:
            page = self.context.new_page()
            page.set_content(html_content)
            page.wait_for_timeout(500)
            page.screenshot(path=str(output_path), full_page=True)
            page.close()
            logger.info(f"Saved image: {output_path}")
        except Exception as e:
            logger.error(f"Failed to save image {output_path}: {e}")

    def _has_video(self, tweet_data: Dict) -> bool:
        """Check if tweet contains video."""
        for item in tweet_data.get('media', []):
            if item.get('type') in ('video', 'animated_gif'):
                return True
        return False

    def _save_video_html(self, tweet_data: Dict, output_path: Path):
        """Save tweet with video as HTML reference file."""
        try:
            tweet_id = tweet_data['id']
            author = tweet_data.get('author', {}) or {}
            username = author.get('username', 'unknown')

            # Try to find direct video URL
            video_url = None
            for m in tweet_data.get('media', []):
                if m.get('video_url'):
                    video_url = m['video_url']
                    break

            video_section = ""
            if video_url:
                video_section = (
                    f'<p><strong>Video URL:</strong> '
                    f'<a href="{video_url}">Direct video link</a></p>'
                )

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
        <p><strong>Link:</strong> <a href="https://x.com/{username}/status/{tweet_id}">View on X</a></p>
        <p><strong>Text:</strong> {self._escape_html(tweet_data.get('text', ''))}</p>
        {video_section}
    </div>
    <p>Download the video:</p>
    <p><code>yt-dlp https://x.com/{username}/status/{tweet_id}</code></p>
</body>
</html>"""

            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(html_content)

            logger.info(f"Saved video HTML: {output_path}")

            # Attempt automatic video download via yt-dlp
            self._download_video(tweet_id, username, output_path.parent)

        except Exception as e:
            logger.error(f"Failed to save video HTML {output_path}: {e}")

    def _download_video(self, tweet_id: str, username: str, output_dir: Path):
        """Download video using yt-dlp."""
        try:
            url = f"https://x.com/{username}/status/{tweet_id}"
            ydl_opts = {
                'outtmpl': str(output_dir / f'{tweet_id}_%(title)s.%(ext)s'),
                'quiet': True,
                'no_warnings': True,
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])

            logger.info(f"Downloaded video for tweet {tweet_id}")
        except Exception as e:
            logger.warning(f"Failed to download video for tweet {tweet_id}: {e}")

    def process_bookmark(self, bookmark: Dict):
        """Process a single bookmark."""
        tweet_id = str(bookmark['id'])

        if tweet_id in self.state['processed_tweets']:
            logger.debug(f"Skipping already processed tweet {tweet_id}")
            return

        # Skip tweets already captured by the DOM-scraping Chrome extension
        if tweet_id in self.scraped_ids:
            logger.info(f"Skipping tweet {tweet_id} — already in scraped bookmarks file")
            self.state['processed_tweets'][tweet_id] = {
                'processed_at': datetime.now().isoformat(),
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
            thread_tweets = []

            # If the conversation_id differs from the tweet ID, it might be
            # part of a thread. Only fetch if the reply is to another tweet.
            ref_tweets = bookmark.get('referenced_tweets', [])
            if ref_tweets:
                # Check if this tweet is a reply (part of a thread)
                has_reply = any(
                    getattr(r, 'type', None) == 'replied_to'
                    if not isinstance(r, dict)
                    else r.get('type') == 'replied_to'
                    for r in (ref_tweets or [])
                )
                if has_reply and bookmark.get('conversation_id'):
                    thread_tweets = self._get_thread_tweets(
                        tweet_id, bookmark['conversation_id']
                    )
                    is_thread = len(thread_tweets) > 0

            html_content = self._render_tweet_html(bookmark, is_thread, thread_tweets)

            pdf_folder = self._get_folder_path(folder_name, PDF_DIR)
            img_folder = self._get_folder_path(folder_name, IMAGES_DIR)
            vid_folder = self._get_folder_path(folder_name, VIDEOS_DIR)

            self._save_as_pdf(html_content, pdf_folder / f"{tweet_id}.pdf")
            self._save_as_image(html_content, img_folder / f"{tweet_id}.png")

            if self._has_video(bookmark):
                self._save_video_html(bookmark, vid_folder / f"{tweet_id}.html")

            self.state['processed_tweets'][tweet_id] = {
                'processed_at': datetime.now().isoformat(),
                'folder': folder_name,
                'is_thread': is_thread,
            }

            time.sleep(1)

        except Exception as e:
            logger.error(f"Failed to process tweet {tweet_id}: {e}")

    def run(self):
        """Main execution method."""
        try:
            logger.info("Starting bookmark archiver...")

            # Report scraped bookmarks status
            if self.scraped_ids:
                logger.info(
                    f"Loaded {len(self.scraped_ids)} scraped bookmark IDs — "
                    f"these will be skipped during API processing"
                )

            self._init_browser()

            bookmarks = self.get_bookmarks()

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

            logger.info(f"Archiving complete.")

        except Exception as e:
            logger.error(f"Error during execution: {e}")
            raise
        finally:
            self._close_browser()


# ═══════════════════════════════════════════════════════════════════════════
#  Entry Points
# ═══════════════════════════════════════════════════════════════════════════

def main():
    """Entry point for the script."""
    parser = argparse.ArgumentParser(
        description='X/Twitter Bookmark Archiver — archive bookmarks as PDF, images, and video.'
    )
    parser.add_argument(
        '--auth', action='store_true',
        help='Run OAuth 2.0 PKCE authorization flow to get an access token.'
    )
    parser.add_argument(
        '--scraped', type=str, default=None,
        help='Path to a scraped bookmarks JSON file from the Chrome extension. '
             'Tweets listed in this file are skipped during API processing '
             'since they were already captured via DOM scraping. '
             'Can also be set via the SCRAPED_BOOKMARKS_FILE env var.'
    )
    args = parser.parse_args()

    # ── Auth mode ────────────────────────────────────────────────────────
    if args.auth:
        do_auth_flow()
        return 0

    # ── Archive mode ─────────────────────────────────────────────────────
    # Load saved token
    token_data = _load_token()
    if not token_data:
        logger.error(
            "No OAuth token found. Run 'python bookmark_archiver.py --auth' first."
        )
        return 1

    access_token = token_data.get('access_token')
    if not access_token:
        logger.error("Saved token is invalid. Run 'python bookmark_archiver.py --auth' again.")
        return 1

    # Determine scraped bookmarks file (CLI arg > env var)
    scraped_path = args.scraped or SCRAPED_BOOKMARKS_FILE

    try:
        archiver = BookmarkArchiver(access_token, scraped_file=scraped_path)
        archiver.run()
        return 0
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        return 1


if __name__ == '__main__':
    # Import re — used in _sanitize_filename and via _escape_html
    import re
    exit(main())