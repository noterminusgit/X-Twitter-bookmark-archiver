# X-Twitter Bookmark Archiver

A Python script to download and archive all your Twitter/X bookmarks as PDFs, images, and videos. Supports premium accounts with bookmark folders, thread detection, and incremental updates for cron job automation.

## Features

- **Multiple Formats**: Saves bookmarks as PDFs, high-quality images (PNG), and HTML for videos
- **Folder Support**: Mimics Twitter/X bookmark folder structure for premium accounts
- **Thread Detection**: Automatically detects and archives entire threads in single files
- **Video Support**: Saves HTML documents for video posts with download instructions and attempts automatic download via yt-dlp
- **Incremental Updates**: Tracks processed bookmarks to only download new content
- **Cron-Compatible**: Designed to run as a scheduled task without removing existing content
- **Rate Limiting**: Respects Twitter API limits with automatic backoff

## Directory Structure

```
output/
├── pdf/
│   ├── [folder_name]/     # Premium bookmark folders
│   └── [tweet_id].pdf     # Individual tweets or full threads
├── images/
│   ├── [folder_name]/
│   └── [tweet_id].png     # High-quality screenshots
└── videos/
    ├── [folder_name]/
    ├── [tweet_id].html    # HTML document with video info
    └── [tweet_id]_*.mp4   # Downloaded videos (if successful)
```

## Prerequisites

- Python 3.8 or higher
- Twitter/X Developer Account with API access
- Active Twitter/X account with bookmarks

## Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/yourusername/X-Twitter-bookmark-archiver.git
   cd X-Twitter-bookmark-archiver
   ```

2. **Install Python dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Install Playwright browsers:**
   ```bash
   playwright install chromium
   ```

4. **Set up Twitter API credentials:**
   - Go to [Twitter Developer Portal](https://developer.twitter.com/en/portal/dashboard)
   - Create a new app or use an existing one
   - Generate the following credentials:
     - Bearer Token
     - API Key and Secret (Consumer Key/Secret)
     - Access Token and Secret
   - Make sure your app has Read permissions and access to bookmarks

5. **Configure environment variables:**
   ```bash
   cp .env.example .env
   # Edit .env with your credentials
   ```

## Configuration

Edit the `.env` file with your Twitter API credentials:

```env
TWITTER_BEARER_TOKEN=your_bearer_token_here
TWITTER_API_KEY=your_api_key_here
TWITTER_API_SECRET=your_api_secret_here
TWITTER_ACCESS_TOKEN=your_access_token_here
TWITTER_ACCESS_SECRET=your_access_secret_here

# Optional
OUTPUT_DIR=output
STATE_FILE=.archive_state.json
```

## Usage

### Manual Execution

Run the script manually:

```bash
python bookmark_archiver.py
```

### Automated Execution (Cron Job)

Set up a cron job to run the script automatically:

1. **Edit crontab:**
   ```bash
   crontab -e
   ```

2. **Add a cron entry (example: daily at 2 AM):**
   ```cron
   0 2 * * * cd /path/to/X-Twitter-bookmark-archiver && /usr/bin/python3 bookmark_archiver.py
   ```

3. **Other scheduling examples:**
   - Every 6 hours: `0 */6 * * *`
   - Every day at midnight: `0 0 * * *`
   - Every Monday at 3 AM: `0 3 * * 1`

### Windows Task Scheduler

1. Open Task Scheduler
2. Create a new task
3. Set the trigger (e.g., daily, weekly)
4. Set the action to run:
   - Program: `python.exe`
   - Arguments: `bookmark_archiver.py`
   - Start in: `/path/to/X-Twitter-bookmark-archiver`

## How It Works

1. **Fetches Bookmarks**: Uses Twitter API v2 to retrieve all your bookmarks
2. **Checks State**: Compares with `.archive_state.json` to identify new bookmarks
3. **Detects Threads**: Identifies if a bookmark is part of a conversation thread
4. **Renders Content**: Uses Playwright to render tweets as HTML
5. **Saves Formats**:
   - PDF: High-quality PDF documents
   - Images: PNG screenshots with 2x scale for clarity
   - Videos: HTML files with metadata and download links
6. **Downloads Videos**: Attempts to download videos using yt-dlp
7. **Updates State**: Saves processed tweet IDs to avoid re-downloading

## State Management

The script maintains a `.archive_state.json` file that tracks:
- Processed tweet IDs
- Processing timestamps
- Bookmark folders
- Thread status

This ensures:
- No duplicate downloads
- Incremental updates only fetch new bookmarks
- Existing content is never removed

## Troublesoting

### Authentication Errors

If you see authentication errors:
- Verify your API credentials in `.env`
- Ensure your Twitter app has the correct permissions
- Check if your access tokens are still valid

### Rate Limiting

The script includes automatic rate limit handling, but if you have many bookmarks:
- The first run may take a while
- Subsequent runs will be faster (incremental updates only)
- The script waits 1 second between tweets to be respectful

### Video Download Failures

Some videos may fail to download due to:
- Twitter's anti-bot measures
- Private or restricted content
- Network issues

In these cases, the HTML file will still be saved with a link to view the video on Twitter.

### Browser Issues

If Playwright fails to launch:
```bash
playwright install chromium
```

## Logging

The script creates a `bookmark_archiver.log` file with detailed information about:
- Bookmarks fetched
- Files created
- Errors encountered
- Processing status

## Privacy and Security

- Your API credentials are stored locally in `.env` (gitignored)
- No data is sent to third parties
- All processing happens locally on your machine
- The `.archive_state.json` only contains tweet IDs and timestamps

## Limitations

- **Bookmark Folders**: The Twitter API v2 doesn't currently expose bookmark folder information, so all bookmarks are saved to the default directory. This may change as the API evolves.
- **Media Quality**: Image and video quality depends on what Twitter provides via the API
- **Deleted Tweets**: If a bookmarked tweet is deleted, it won't be downloadable
- **Rate Limits**: Twitter API has rate limits; the script handles them gracefully

## Contributing

Contributions are welcome! Please feel free to submit issues or pull requests.

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Disclaimer

This tool is for personal archival purposes only. Ensure you comply with Twitter's Terms of Service and respect content creators' rights. The developers are not responsible for any misuse of this tool.
