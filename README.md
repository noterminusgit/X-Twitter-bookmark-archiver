# X-Twitter Bookmark Archiver

A Python script to download and archive all your X/Twitter bookmarks as PDFs, images, and videos. Supports folder structure, thread detection, video downloads, and incremental updates for cron job automation.

> **⚠️ API Update (2024–2025):** The X API v2 bookmarks endpoint now **requires** OAuth 2.0 Authorization Code with PKCE (User Context). Older auth methods (Bearer Token, OAuth 1.0a) will not work. This script has been updated to use the correct authentication flow.

## Features

- **Multiple Formats**: Saves bookmarks as PDFs, high-quality images (PNG), and HTML for videos
- **Folder Support**: Folder structure for organizing bookmarks (note: X API still doesn't expose bookmark folders natively)
- **Thread Detection**: Automatically detects and archives entire threads in single files
- **Video Support**: Downloads videos via yt-dlp and saves HTML reference pages
- **Long-form Tweets**: Full support for posts longer than 280 characters
- **Incremental Updates**: Tracks processed bookmarks via state file to only download new content
- **Cron-Compatible**: Designed to run as a scheduled task without duplicating content
- **Rate Limiting**: Respects X API limits with automatic backoff
- **Local Auth Server**: Built-in callback server for a seamless OAuth authorization flow

## Directory Structure

```
output/
├── pdf/
│   └── [tweet_id].pdf       # Individual tweets or full threads
├── images/
│   └── [tweet_id].png       # High-quality screenshots
└── videos/
    ├── [tweet_id].html      # HTML document with video info
    └── [tweet_id]_*.mp4     # Downloaded videos (if successful)
```

## Prerequisites

- Python 3.8 or higher
- X/Twitter Developer Account with an active Project & App
- Active X/Twitter account with bookmarks

## Setup

### 1. Clone the repository

```bash
git clone https://github.com/noterminusgit/X-Twitter-bookmark-archiver.git
cd X-Twitter-bookmark-archiver
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
playwright install chromium
```

### 3. Set up X API credentials

You need an X Developer account and a Project + App:

1. Go to [X Developer Portal](https://developer.twitter.com/en/portal/dashboard)
2. Create a new Project & App (or use an existing one)
3. In **App settings → "User authentication settings"**, click "Set up"
4. Enable **OAuth 2.0 Authorization Code with PKCE**
5. For "App permissions", select **"Read"** (bookmarks only need read)
6. Set **"Callback URI / Redirect URL"** to: `http://127.0.0.1:6006/callback`
7. Set **"Website URL"** to any valid URL (e.g., `https://example.com`)
8. Save your changes
9. Go to the **"Keys and Tokens"** tab and copy your **Client ID**

### 4. Configure environment

```bash
cp .env.example .env
```

Edit `.env` with your Client ID:

```env
TWITTER_CLIENT_ID=your_client_id_here
```

### 5. Run the authorization flow (first time only)

```bash
python bookmark_archiver.py --auth
```

This will:
- Open your browser to authorize the app with your X account
- Start a local server on port 6006 to catch the redirect
- Save the OAuth access token to `.x_token.json`
- The token is valid until revoked

**For headless servers:** If the browser doesn't open automatically, the script will display the authorization URL. Copy it, visit it in a browser, authorize, and then paste the full redirect URL back into the terminal.

## Usage

### Archive your bookmarks

```bash
python bookmark_archiver.py
```

### First run

On the first run, it will:
1. Load the saved OAuth token
2. Fetch all your bookmarks via X API v2
3. Render each tweet as HTML using Playwright
4. Save as PDF and PNG screenshot
5. Download any videos via yt-dlp
6. Track what's been processed in `.archive_state.json`

Subsequent runs only process **new** bookmarks (incremental).

### Re-authorization

If your token expires or you get 403 errors:

```bash
python bookmark_archiver.py --auth
```

### Automated Execution (Cron Job)

Add to crontab (runs daily at 2 AM):

```bash
0 2 * * * cd /path/to/X-Twitter-bookmark-archiver && /usr/bin/python3 bookmark_archiver.py
```

## Configuration

Edit `.env` for custom paths:

```env
# Required
TWITTER_CLIENT_ID=your_client_id_here

# Optional
TWITTER_CLIENT_SECRET=your_client_secret_here
OUTPUT_DIR=output
STATE_FILE=.archive_state.json
TOKEN_FILE=.x_token.json
```

## How It Works

1. **Authorization**: Uses OAuth 2.0 Authorization Code with PKCE (User Context) — the only auth method the bookmarks endpoint supports
2. **Fetches Bookmarks**: Uses X API v2 `GET /2/users/:id/bookmarks` endpoint
3. **Checks State**: Compares with `.archive_state.json` to identify new bookmarks
4. **Detects Threads**: Identifies if a bookmark is part of a conversation thread
5. **Renders Content**: Uses Playwright to render tweets as styled HTML
6. **Saves Formats**: PDF (A4), PNG screenshots (2x retina), HTML for videos
7. **Downloads Videos**: Attempts video download via yt-dlp
8. **Updates State**: Saves processed tweet IDs to avoid re-downloading

## Important Notes

- **Bookmark Folders**: The X API v2 still doesn't expose bookmark folder information. All bookmarks are saved flat.
- **Token Security**: The OAuth token is saved as `.x_token.json` with restricted permissions (0600). Keep it secure.
- **Rate Limits**: X API has rate limits (typically 15 requests per 15-minute window for bookmarks). The script handles this gracefully with `wait_on_rate_limit`.
- **Deleted Tweets**: If a bookmarked tweet has been deleted, the API will skip it.

## Troubleshooting

### 403 Forbidden errors

Your auth token is likely expired or using the wrong auth method. Re-run:

```bash
python bookmark_archiver.py --auth
```

### Browser fails to launch

```bash
playwright install chromium
```

### "Missing required environment variables"

Ensure your `.env` file has `TWITTER_CLIENT_ID` set correctly.

### Video download failures

Some videos fail due to X's anti-bot measures. An HTML reference file is always saved with a link to view on X.

## Logging

The script creates `bookmark_archiver.log` with detailed processing information.

## Privacy & Security

- Credentials stored locally in `.env` (gitignored)
- OAuth token stored locally in `.x_token.json` (gitignored, 0600 permissions)
- No data sent to third parties
- All processing happens locally

## Limitations

- **Bookmark Folders**: X API v2 still doesn't expose bookmark folders
- **Media Quality**: Depends on what X provides via the API
- **Deleted Tweets**: Not downloadable
- **Rate Limits**: 15 requests / 15-min window (handled automatically)

## License

MIT License

## Disclaimer

For personal archival purposes. Ensure compliance with X/Twitter's Terms of Service.