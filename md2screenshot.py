#!/usr/bin/env python3
"""
md2screenshot - Recreate a Twitter/X-style screenshot from a saved Markdown file.

Usage:
    python md2screenshot.py input.md                  # -> input.png
    python md2screenshot.py input.md -o screenshot.png
    python md2screenshot.py input.md --pdf output.pdf
    python md2screenshot.py folder/                   # process all .md files
"""

import argparse
import re
import sys
from html import escape
from pathlib import Path

from playwright.sync_api import sync_playwright


# ---------------------------------------------------------------------------
# Markdown -> structured data
# ---------------------------------------------------------------------------

def parse_markdown(md_text: str) -> dict:
    """Parse an archived-tweet Markdown file into structured data.

    Returns:
        {
            'is_thread': bool,
            'author': {'name': str, 'username': str},
            'tweets': [{'text': str, 'timestamp': str, 'author': {...}}],
            'original_url': str | None,
        }
    """
    lines = md_text.strip().splitlines()
    result = {
        'is_thread': False,
        'author': {'name': '', 'username': ''},
        'tweets': [],
        'original_url': None,
    }

    # Detect thread vs single tweet from H1
    h1_match = re.match(r'^#\s+(.+)$', lines[0]) if lines else None
    if not h1_match:
        # Fallback: treat entire text as a single tweet
        result['tweets'].append({'text': md_text, 'timestamp': '', 'author': result['author']})
        return result

    heading = h1_match.group(1)
    thread_match = re.match(r'Thread by @(\w+)\s*\u2014\s*(.+)', heading)
    single_match = re.match(r'@(\w+)\s*\u2014\s*(.+)', heading)

    if thread_match:
        result['is_thread'] = True
        result['author'] = {'username': thread_match.group(1), 'name': thread_match.group(2).strip()}
    elif single_match:
        result['author'] = {'username': single_match.group(1), 'name': single_match.group(2).strip()}

    # Extract original URL (last link in file)
    for line in reversed(lines):
        url_match = re.search(r'\[View original\]\((https?://[^\)]+)\)', line)
        if url_match:
            result['original_url'] = url_match.group(1)
            break

    if result['is_thread']:
        result['tweets'] = _parse_thread_tweets(lines, result['author'])
    else:
        result['tweets'] = [_parse_single_tweet(lines, result['author'])]

    return result


def _parse_single_tweet(lines: list, default_author: dict) -> dict:
    """Extract text and timestamp from a single-tweet Markdown."""
    timestamp = ''
    text_lines = []
    in_body = False

    for line in lines[1:]:  # skip H1
        # Stop at footer
        if re.match(r'^\[View original\]', line):
            break

        # Timestamp line
        ts_match = re.match(r'^\*(.+)\*$', line.strip())
        if ts_match and not in_body:
            timestamp = ts_match.group(1)
            continue

        # Separator marks start/end of body
        if line.strip() == '---':
            if not in_body:
                in_body = True
                continue
            else:
                break

        if in_body:
            text_lines.append(line)

    text = '\n'.join(text_lines).strip()
    return {'text': text, 'timestamp': timestamp, 'author': default_author}


def _parse_thread_tweets(lines: list, default_author: dict) -> list:
    """Split thread Markdown into individual tweet dicts."""
    tweets = []
    current: dict | None = None

    for line in lines[1:]:  # skip H1
        if re.match(r'^\[View original\]', line):
            break

        # New tweet in thread: ## N/M
        h2_match = re.match(r'^##\s+\d+/\d+', line)
        if h2_match:
            if current is not None:
                current['text'] = '\n'.join(current['_lines']).strip()
                del current['_lines']
                tweets.append(current)
            current = {'text': '', 'timestamp': '', 'author': dict(default_author), '_lines': []}
            continue

        if current is None:
            continue

        # Author override
        author_match = re.match(r'^\*\*@(\w+)\*\*\s*\u2014\s*(.+)', line)
        if author_match:
            current['author'] = {'username': author_match.group(1), 'name': author_match.group(2).strip()}
            continue

        # Timestamp
        ts_match = re.match(r'^\*(.+)\*$', line.strip())
        if ts_match and not current['_lines']:
            current['timestamp'] = ts_match.group(1)
            continue

        # Footer separator
        if line.strip() == '---':
            continue

        current['_lines'].append(line)

    # Flush last tweet
    if current is not None:
        current['text'] = '\n'.join(current['_lines']).strip()
        del current['_lines']
        tweets.append(current)

    return tweets


# ---------------------------------------------------------------------------
# Structured data -> HTML
# ---------------------------------------------------------------------------

def build_html(data: dict) -> str:
    """Convert parsed tweet data into Twitter-style HTML."""
    css = """
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
            font-weight: 600;
        }
    """

    parts = [
        '<!DOCTYPE html><html><head><meta charset="UTF-8">',
        f'<style>{css}</style></head><body>',
    ]

    if data['is_thread']:
        parts.append('<div class="thread-indicator">Thread</div>')

    for tweet in data['tweets']:
        author = tweet.get('author', data['author'])
        parts.append('<div class="tweet">')
        parts.append(
            f'<div class="header">'
            f'<span class="author-name">{escape(author.get("name", "Unknown"))}</span>'
            f'<span class="username">@{escape(author.get("username", "unknown"))}</span>'
            f'</div>'
        )
        parts.append(f'<div class="content">{escape(tweet.get("text", ""))}</div>')
        ts = tweet.get('timestamp', '')
        if ts:
            parts.append(f'<div class="timestamp">{escape(ts)}</div>')
        parts.append('</div>')

    parts.append('</body></html>')
    return '\n'.join(parts)


# ---------------------------------------------------------------------------
# Rendering with Playwright
# ---------------------------------------------------------------------------

def render_screenshot(html: str, output_path: Path):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            viewport={'width': 1280, 'height': 1024},
            device_scale_factor=2,
        )
        page = ctx.new_page()
        page.set_content(html)
        page.wait_for_timeout(500)
        page.screenshot(path=str(output_path), full_page=True)
        page.close()
        ctx.close()
        browser.close()


def render_pdf(html: str, output_path: Path):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={'width': 1280, 'height': 1024})
        page = ctx.new_page()
        page.set_content(html)
        page.pdf(path=str(output_path), format='A4')
        page.close()
        ctx.close()
        browser.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def process_file(md_path: Path, output: Path | None, as_pdf: bool):
    md_text = md_path.read_text(encoding='utf-8')
    data = parse_markdown(md_text)
    html = build_html(data)

    if output is None:
        suffix = '.pdf' if as_pdf else '.png'
        output = md_path.with_suffix(suffix)

    if as_pdf:
        render_pdf(html, output)
    else:
        render_screenshot(html, output)

    print(f"{'PDF' if as_pdf else 'Screenshot'}: {output}")


def main():
    parser = argparse.ArgumentParser(
        description='Recreate Twitter/X-style screenshots from archived Markdown files.',
    )
    parser.add_argument(
        'input', type=Path, nargs='+',
        help='Markdown file(s) or directory containing .md files',
    )
    parser.add_argument(
        '-o', '--output', type=Path, default=None,
        help='Output file path (only when processing a single file)',
    )
    parser.add_argument(
        '--pdf', action='store_true', default=False,
        help='Output as PDF instead of PNG screenshot',
    )
    args = parser.parse_args()

    paths: list[Path] = []
    for p in args.input:
        if p.is_dir():
            paths.extend(sorted(p.glob('**/*.md')))
        elif p.is_file() and p.suffix == '.md':
            paths.append(p)
        else:
            print(f"Skipping: {p}", file=sys.stderr)

    if not paths:
        print("No Markdown files found.", file=sys.stderr)
        return 1

    if args.output and len(paths) > 1:
        print("--output can only be used with a single input file.", file=sys.stderr)
        return 1

    for md_path in paths:
        out = args.output if len(paths) == 1 else None
        process_file(md_path, out, args.pdf)

    return 0


if __name__ == '__main__':
    sys.exit(main())
