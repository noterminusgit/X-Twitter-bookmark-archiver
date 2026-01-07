# Quick Start Guide

This guide will help you get started with the Twitter Bookmark Archiver in under 5 minutes.

## Step 1: Get Twitter API Credentials

1. Go to https://developer.twitter.com/en/portal/dashboard
2. Sign in with your Twitter account
3. Create a new app (or use an existing one)
4. In your app settings, generate:
   - Bearer Token
   - API Key and Secret
   - Access Token and Secret
5. Make sure your app has "Read" permissions

## Step 2: Install

### Linux/Mac:
```bash
./setup.sh
```

### Windows:
```batch
setup.bat
```

Or manually:
```bash
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
```

## Step 3: Configure

Edit `.env` file with your API credentials:

```env
TWITTER_BEARER_TOKEN=AAAAAAAAAAAAAAAAAAAAALsQ...
TWITTER_API_KEY=kCv3fD8gE...
TWITTER_API_SECRET=xY9mN2pK...
TWITTER_ACCESS_TOKEN=1234567890-aBcDeF...
TWITTER_ACCESS_SECRET=zW8xY7vU...
```

## Step 4: Run

```bash
python bookmark_archiver.py
```

Or make it executable:
```bash
chmod +x bookmark_archiver.py
./bookmark_archiver.py
```

## Step 5: Check Output

Your bookmarks will be saved in:
```
output/
├── pdf/       # PDF versions
├── images/    # PNG screenshots
└── videos/    # HTML files + downloaded videos
```

## Automation (Optional)

### Cron (Linux/Mac):
```bash
crontab -e
```

Add this line to run daily at 2 AM:
```
0 2 * * * cd /path/to/X-Twitter-bookmark-archiver && python3 bookmark_archiver.py
```

### Task Scheduler (Windows):
1. Open Task Scheduler
2. Create Basic Task
3. Set schedule (e.g., daily)
4. Action: Start a program
   - Program: `python.exe`
   - Arguments: `bookmark_archiver.py`
   - Start in: `C:\path\to\X-Twitter-bookmark-archiver`

## Troubleshooting

### "Missing required environment variables"
- Check that your `.env` file exists and has all 5 credentials
- Make sure there are no spaces around the `=` signs

### "Failed to initialize Twitter client"
- Verify your API credentials are correct
- Ensure your app has Read permissions
- Check that your tokens haven't expired

### Browser fails to launch
```bash
playwright install chromium
```

### Rate limit errors
- This is normal if you have many bookmarks
- The script handles it automatically
- Just wait and it will continue

## Need Help?

- Check the full documentation: [README.md](README.md)
- View logs: `bookmark_archiver.log`
- Report issues: [GitHub Issues](https://github.com/yourusername/X-Twitter-bookmark-archiver/issues)
