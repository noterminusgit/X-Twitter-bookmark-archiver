#!/usr/bin/env python3
"""
Twitter/X Bookmark Archiver
Downloads bookmarks as PDFs, images, and videos with folder structure support.
"""

import os
import json
import time
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Set
import re

import tweepy
from playwright.sync_api import sync_playwright, Page
from dotenv import load_dotenv
import yt_dlp

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bookmark_archiver.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Configuration
OUTPUT_DIR = Path(os.getenv('OUTPUT_DIR', 'output'))
STATE_FILE = Path(os.getenv('STATE_FILE', '.archive_state.json'))
TWITTER_BEARER_TOKEN = os.getenv('TWITTER_BEARER_TOKEN')
TWITTER_API_KEY = os.getenv('TWITTER_API_KEY')
TWITTER_API_SECRET = os.getenv('TWITTER_API_SECRET')
TWITTER_ACCESS_TOKEN = os.getenv('TWITTER_ACCESS_TOKEN')
TWITTER_ACCESS_SECRET = os.getenv('TWITTER_ACCESS_SECRET')

# Directory structure
PDF_DIR = OUTPUT_DIR / 'pdf'
IMAGES_DIR = OUTPUT_DIR / 'images'
VIDEOS_DIR = OUTPUT_DIR / 'videos'


class BookmarkArchiver:
    def __init__(self):
        """Initialize the bookmark archiver."""
        self.state = self._load_state()
        self.client = self._init_twitter_client()
        self.playwright = None
        self.browser = None
        self.context = None

        # Create output directories
        for directory in [PDF_DIR, IMAGES_DIR, VIDEOS_DIR]:
            directory.mkdir(parents=True, exist_ok=True)

    def _init_twitter_client(self) -> tweepy.Client:
        """Initialize Twitter API client."""
        try:
            client = tweepy.Client(
                bearer_token=TWITTER_BEARER_TOKEN,
                consumer_key=TWITTER_API_KEY,
                consumer_secret=TWITTER_API_SECRET,
                access_token=TWITTER_ACCESS_TOKEN,
                access_token_secret=TWITTER_ACCESS_SECRET,
                wait_on_rate_limit=True
            )
            logger.info("Twitter client initialized successfully")
            return client
        except Exception as e:
            logger.error(f"Failed to initialize Twitter client: {e}")
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
                device_scale_factor=2  # For high-quality screenshots
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

    def _sanitize_filename(self, text: str) -> str:
        """Sanitize text for use in filenames."""
        # Remove or replace invalid characters
        text = re.sub(r'[<>:"/\\|?*]', '_', text)
        # Limit length
        return text[:100]

    def _get_folder_path(self, folder_name: Optional[str], base_dir: Path) -> Path:
        """Get the appropriate folder path based on bookmark folder."""
        if folder_name and folder_name != 'default':
            folder_path = base_dir / self._sanitize_filename(folder_name)
            folder_path.mkdir(parents=True, exist_ok=True)
            return folder_path
        return base_dir

    def get_bookmarks(self) -> List[Dict]:
        """Fetch all bookmarks from Twitter."""
        logger.info("Fetching bookmarks...")
        bookmarks = []

        try:
            # Get authenticated user
            me = self.client.get_me()
            user_id = me.data.id

            # Fetch bookmarks
            # Note: Twitter API v2 bookmarks endpoint
            paginator = tweepy.Paginator(
                self.client.get_bookmarks,
                max_results=100,
                expansions=['author_id', 'referenced_tweets.id', 'attachments.media_keys'],
                tweet_fields=['created_at', 'public_metrics', 'conversation_id', 'entities'],
                media_fields=['type', 'url', 'variants'],
                user_fields=['username', 'name']
            )

            for response in paginator:
                if response.data:
                    for tweet in response.data:
                        bookmark_data = {
                            'id': tweet.id,
                            'text': tweet.text,
                            'author': None,
                            'created_at': tweet.created_at.isoformat() if tweet.created_at else None,
                            'conversation_id': tweet.conversation_id,
                            'referenced_tweets': tweet.referenced_tweets,
                            'media': [],
                            'folder': 'default'  # Twitter API doesn't expose bookmark folders yet
                        }

                        # Get author info from includes
                        if hasattr(response, 'includes') and response.includes:
                            if 'users' in response.includes:
                                for user in response.includes['users']:
                                    if user.id == tweet.author_id:
                                        bookmark_data['author'] = {
                                            'id': user.id,
                                            'username': user.username,
                                            'name': user.name
                                        }
                                        break

                            # Get media info
                            if 'media' in response.includes:
                                bookmark_data['media'] = response.includes['media']

                        bookmarks.append(bookmark_data)

            logger.info(f"Fetched {len(bookmarks)} bookmarks")
            return bookmarks

        except Exception as e:
            logger.error(f"Failed to fetch bookmarks: {e}")
            raise

    def _get_thread_tweets(self, tweet_id: str, conversation_id: str) -> List[Dict]:
        """Get all tweets in a thread."""
        try:
            # Search for tweets in the same conversation
            query = f"conversation_id:{conversation_id}"
            tweets = []

            response = self.client.search_recent_tweets(
                query=query,
                max_results=100,
                tweet_fields=['created_at', 'author_id', 'conversation_id'],
                expansions=['author_id']
            )

            if response.data:
                # Sort by created_at to maintain thread order
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
                        'text': tweet.text,
                        'author': author,
                        'created_at': tweet.created_at
                    })

            return tweets if len(tweets) > 1 else []

        except Exception as e:
            logger.warning(f"Failed to fetch thread for tweet {tweet_id}: {e}")
            return []

    def _render_tweet_html(self, tweet_data: Dict, is_thread: bool = False,
                           thread_tweets: List[Dict] = None) -> str:
        """Generate HTML representation of a tweet or thread."""
        html_parts = []
        html_parts.append("""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <style>
                body {
                    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
                    max-width: 600px;
                    margin: 40px auto;
                    padding: 20px;
                    background: #f7f9fa;
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
                }
                .content {
                    font-size: 16px;
                    line-height: 1.5;
                    margin: 12px 0;
                    white-space: pre-wrap;
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
                }
            </style>
        </head>
        <body>
        """)

        if is_thread and thread_tweets:
            html_parts.append('<div class="thread-indicator">Thread</div>')
            for tweet in thread_tweets:
                author = tweet.get('author', {})
                html_parts.append(f"""
                <div class="tweet">
                    <div class="header">
                        <span class="author-name">{author.get('name', 'Unknown')}</span>
                        <span class="username">@{author.get('username', 'unknown')}</span>
                    </div>
                    <div class="content">{tweet.get('text', '')}</div>
                    <div class="timestamp">{tweet.get('created_at', '')}</div>
                </div>
                """)
        else:
            author = tweet_data.get('author', {})
            html_parts.append(f"""
            <div class="tweet">
                <div class="header">
                    <span class="author-name">{author.get('name', 'Unknown')}</span>
                    <span class="username">@{author.get('username', 'unknown')}</span>
                </div>
                <div class="content">{tweet_data.get('text', '')}</div>
                <div class="timestamp">{tweet_data.get('created_at', '')}</div>
            </div>
            """)

        html_parts.append("</body></html>")
        return ''.join(html_parts)

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
            # Wait for fonts to load
            page.wait_for_timeout(500)
            page.screenshot(path=str(output_path), full_page=True)
            page.close()
            logger.info(f"Saved image: {output_path}")
        except Exception as e:
            logger.error(f"Failed to save image {output_path}: {e}")

    def _has_video(self, tweet_data: Dict) -> bool:
        """Check if tweet contains video."""
        media = tweet_data.get('media', [])
        for item in media:
            if hasattr(item, 'type') and item.type in ['video', 'animated_gif']:
                return True
        return False

    def _save_video_html(self, tweet_data: Dict, output_path: Path):
        """Save tweet with video as HTML file."""
        try:
            tweet_id = tweet_data['id']
            author = tweet_data.get('author', {})
            username = author.get('username', 'unknown')

            # Create HTML with embedded tweet
            html_content = f"""
            <!DOCTYPE html>
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
                </style>
            </head>
            <body>
                <div class="info">
                    <h2>Video Tweet</h2>
                    <p><strong>Author:</strong> {author.get('name', 'Unknown')} (@{username})</p>
                    <p><strong>Tweet ID:</strong> {tweet_id}</p>
                    <p><strong>Link:</strong> <a href="https://twitter.com/{username}/status/{tweet_id}" target="_blank">View on Twitter</a></p>
                    <p><strong>Text:</strong> {tweet_data.get('text', '')}</p>
                </div>
                <p>Note: Download the video using yt-dlp or visit the link above to watch.</p>
                <p>Command: <code>yt-dlp https://twitter.com/{username}/status/{tweet_id}</code></p>
            </body>
            </html>
            """

            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(html_content)

            logger.info(f"Saved video HTML: {output_path}")

            # Optionally download video using yt-dlp
            self._download_video(tweet_id, username, output_path.parent)

        except Exception as e:
            logger.error(f"Failed to save video HTML {output_path}: {e}")

    def _download_video(self, tweet_id: str, username: str, output_dir: Path):
        """Download video using yt-dlp."""
        try:
            url = f"https://twitter.com/{username}/status/{tweet_id}"
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

        # Skip if already processed
        if tweet_id in self.state['processed_tweets']:
            logger.debug(f"Skipping already processed tweet {tweet_id}")
            return

        logger.info(f"Processing tweet {tweet_id}")

        try:
            folder_name = bookmark.get('folder', 'default')

            # Check if part of a thread
            is_thread = False
            thread_tweets = []

            if bookmark.get('conversation_id') != tweet_id:
                thread_tweets = self._get_thread_tweets(tweet_id, bookmark['conversation_id'])
                is_thread = len(thread_tweets) > 0

            # Generate HTML
            html_content = self._render_tweet_html(bookmark, is_thread, thread_tweets)

            # Determine output paths
            pdf_folder = self._get_folder_path(folder_name, PDF_DIR)
            img_folder = self._get_folder_path(folder_name, IMAGES_DIR)
            vid_folder = self._get_folder_path(folder_name, VIDEOS_DIR)

            # Save as PDF and image
            self._save_as_pdf(html_content, pdf_folder / f"{tweet_id}.pdf")
            self._save_as_image(html_content, img_folder / f"{tweet_id}.png")

            # Save video HTML if applicable
            if self._has_video(bookmark):
                self._save_video_html(bookmark, vid_folder / f"{tweet_id}.html")

            # Mark as processed
            self.state['processed_tweets'][tweet_id] = {
                'processed_at': datetime.now().isoformat(),
                'folder': folder_name,
                'is_thread': is_thread
            }

            # Rate limiting - be nice to Twitter
            time.sleep(1)

        except Exception as e:
            logger.error(f"Failed to process tweet {tweet_id}: {e}")

    def run(self):
        """Main execution method."""
        try:
            logger.info("Starting bookmark archiver...")

            # Initialize browser
            self._init_browser()

            # Fetch bookmarks
            bookmarks = self.get_bookmarks()

            # Process each bookmark
            for bookmark in bookmarks:
                self.process_bookmark(bookmark)

            # Save state
            self._save_state()

            logger.info(f"Archiving complete. Processed {len(bookmarks)} bookmarks.")

        except Exception as e:
            logger.error(f"Error during execution: {e}")
            raise
        finally:
            self._close_browser()


def main():
    """Entry point for the script."""
    # Check for required environment variables
    if not all([TWITTER_BEARER_TOKEN, TWITTER_API_KEY, TWITTER_API_SECRET,
                TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_SECRET]):
        logger.error("Missing required environment variables. Please check your .env file.")
        logger.error("Required: TWITTER_BEARER_TOKEN, TWITTER_API_KEY, TWITTER_API_SECRET, "
                    "TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_SECRET")
        return 1

    try:
        archiver = BookmarkArchiver()
        archiver.run()
        return 0
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        return 1


if __name__ == "__main__":
    exit(main())
