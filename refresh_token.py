#!/usr/bin/env python3
"""Refresh the X OAuth 2.0 token."""
import json, os, time, requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
TOKEN_FILE = Path(os.getenv('TOKEN_FILE', '.x_token.json'))
CLIENT_ID = os.getenv('TWITTER_CLIENT_ID')
CLIENT_SECRET = os.getenv('TWITTER_CLIENT_SECRET', '')

if not TOKEN_FILE.exists():
    print("No token file found")
    exit(1)

t = json.load(open(TOKEN_FILE))
refresh_token = t.get('refresh_token')
if not refresh_token:
    print("No refresh token available. Run --auth again.")
    exit(1)

print("Refreshing token...")
data = {
    'refresh_token': refresh_token,
    'grant_type': 'refresh_token',
    'client_id': CLIENT_ID,
}
if CLIENT_SECRET:
    data['client_secret'] = CLIENT_SECRET

resp = requests.post('https://api.twitter.com/2/oauth2/token', data=data)
if resp.status_code != 200:
    print(f"Error {resp.status_code}: {resp.text}")
    exit(1)

new = resp.json()
new_token = new.get('access_token')
if not new_token:
    print(f"No access_token in response: {new}")
    exit(1)

t['access_token'] = new_token
if new.get('refresh_token'):
    t['refresh_token'] = new['refresh_token']
t['saved_at'] = time.time()

with open(TOKEN_FILE, 'w') as f:
    json.dump(t, f, indent=2)
TOKEN_FILE.chmod(0o600)
print(f"✓ Token refreshed! New access_token: {new_token[:20]}...")