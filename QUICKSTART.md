# Quick Start Guide

Get started with the X/Twitter Bookmark Archiver in under 5 minutes.

## Step 1: Get X API Credentials

1. Go to [X Developer Portal](https://developer.twitter.com/en/portal/dashboard)
2. Create a new Project & App
3. **App settings → "User authentication settings"** → Set up
4. Enable **OAuth 2.0 Authorization Code with PKCE**
5. Permissions: **Read**
6. Redirect URL: `http://127.0.0.1:6006/callback`
7. Save, then copy your **Client ID** from the Keys and Tokens tab

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

Edit `.env` with your Client ID:

```env
TWITTER_CLIENT_ID=your_client_id_here
```

## Step 4: Authorize (first time only)

```bash
python bookmark_archiver.py --auth
```

This opens your browser — log in and authorize the app. The token is saved to `.x_token.json`.

## Step 5: Run

```bash
python bookmark_archiver.py
```

Output goes to `output/`:
- `output/pdf/` — PDF versions
- `output/images/` — PNG screenshots
- `output/videos/` — HTML files + downloaded videos

## Automation (Cron)

```bash
crontab -e
```

Add (runs daily at 2 AM):
```
0 2 * * * cd /path/to/X-Twitter-bookmark-archiver && python3 bookmark_archiver.py
```

## Troubleshooting

### "No OAuth token found"
Run `python bookmark_archiver.py --auth` first.

### 403 Forbidden
Token expired or wrong auth method. Run `python bookmark_archiver.py --auth` again.

### Browser fails to launch
```bash
playwright install chromium
```

### Need Help?
- Full docs: [README.md](README.md)
- View logs: `bookmark_archiver.log`