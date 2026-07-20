#!/usr/bin/env python3
"""
X Bookmark Archiver — Local Companion Server

Receives bookmark data from the Chrome extension via POST /api/bookmarks
and writes timestamped JSON files to a designated archive directory.

Usage:
    python3 server.py              # default port 6007
    python3 server.py --port 6007  # explicit
    python3 server.py --dir /path/to/archive
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("x-bookmark-server")

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_PORT = 6007
DEFAULT_ARCHIVE_DIR = Path(
    os.environ.get(
        "BOOKMARK_ARCHIVE_DIR",
        "/mnt/storage/chris-saved/x-twitter-bookmark-archiver/chrome-ext/scraped-bookmarks",
    )
)

# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------

MAX_BODY_BYTES = 50 * 1024 * 1024  # 50 MB safety limit


class BookmarkHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the bookmark archiver companion server."""

    # Shared reference set by the factory / server
    archive_dir: Path = DEFAULT_ARCHIVE_DIR

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _send_json(
        self, status: int, data: dict[str, Any]
    ) -> None:
        """Send a JSON response with the given HTTP status code."""
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> bytes:
        """Read the full request body up to MAX_BODY_BYTES."""
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length > MAX_BODY_BYTES:
            raise ValueError(
                f"Request body too large: {content_length} bytes "
                f"(max {MAX_BODY_BYTES})"
            )
        return self.rfile.read(content_length)

    def _ensure_archive_dir(self) -> None:
        """Create the archive directory if it doesn't exist."""
        self.archive_dir.mkdir(parents=True, exist_ok=True)

    def _write_bookmark_file(
        self, payload: dict[str, Any]
    ) -> Path:
        """Write a timestamped JSON file to the archive directory.

        Returns the path to the written file.
        """
        self._ensure_archive_dir()

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        filename = f"bookmarks-{timestamp}.json"
        filepath = self.archive_dir / filename

        # Pretty-print the JSON for human readability
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        logger.info("Saved %d bookmarks → %s", len(payload.get("bookmarks", [])), filepath)
        return filepath

    # ------------------------------------------------------------------
    # CORS preflight
    # ------------------------------------------------------------------

    def do_OPTIONS(self) -> None:
        """Handle CORS preflight requests from the extension."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, Authorization",
        )
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    # ------------------------------------------------------------------
    # GET / — health check
    # ------------------------------------------------------------------

    def do_GET(self) -> None:
        """Health-check / info endpoint."""
        if self.path == "/":
            self._send_json(200, {
                "status": "ok",
                "service": "x-bookmark-archiver-server",
                "archive_dir": str(self.archive_dir.resolve()),
                "archive_exists": self.archive_dir.exists(),
                "total_files": len(list(self.archive_dir.glob("*.json")))
                if self.archive_dir.exists()
                else 0,
            })
            return

        if self.path == "/api/bookmarks" or self.path.startswith("/api/bookmarks?"):
            # Return list of archived bookmark files (meta only, not the data)
            if not self.archive_dir.exists():
                self._send_json(200, {"files": []})
                return

            files = sorted(self.archive_dir.glob("*.json"), reverse=True)
            file_list = []
            for fp in files[:100]:  # cap at 100 entries
                stat = fp.stat()
                file_list.append({
                    "filename": fp.name,
                    "size_bytes": stat.st_size,
                    "modified": datetime.fromtimestamp(
                        stat.st_mtime, tz=timezone.utc
                    ).isoformat(),
                })
            self._send_json(200, {"count": len(file_list), "files": file_list})
            return

        self._send_json(404, {"error": "Not found"})

    # ------------------------------------------------------------------
    # POST /api/bookmarks — receive scrape data
    # ------------------------------------------------------------------

    def do_POST(self) -> None:
        """Receive bookmark data from the Chrome extension."""
        if self.path != "/api/bookmarks":
            self._send_json(404, {"error": "Not found"})
            return

        raw_body: bytes | None = None
        try:
            raw_body = self._read_body()
            payload = json.loads(raw_body)

            # Validate payload
            bookmarks = payload.get("bookmarks", [])
            if not isinstance(bookmarks, list):
                self._send_json(400, {
                    "error": "Invalid payload: 'bookmarks' must be an array",
                })
                return

            # Write to disk
            filepath = self._write_bookmark_file(payload)
            total = len(bookmarks)

            self._send_json(201, {
                "status": "accepted",
                "total_bookmarks": total,
                "file": filepath.name,
                "file_path": str(filepath.resolve()),
            })

        except json.JSONDecodeError as exc:
            logger.error("Invalid JSON in request body: %s", exc)
            self._send_json(400, {
                "error": "Invalid JSON",
                "detail": str(exc),
            })
        except ValueError as exc:
            logger.error("Request too large: %s", exc)
            self._send_json(413, {"error": str(exc)})
        except OSError as exc:
            logger.error("Failed to write bookmark file: %s", exc)
            self._send_json(500, {
                "error": "Failed to save bookmarks to disk",
                "detail": str(exc),
            })
        except Exception as exc:
            logger.exception("Unexpected error processing bookmarks")
            self._send_json(500, {
                "error": "Internal server error",
                "detail": str(exc),
            })

    # ------------------------------------------------------------------
    # Logging override — quieter than the default
    # ------------------------------------------------------------------

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        logger.debug(format, *args)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="X Bookmark Archiver — companion server",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Port to listen on (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Host to bind to (default: 127.0.0.1 — local only)",
    )
    parser.add_argument(
        "--dir",
        type=str,
        default=None,
        help=f"Archive directory for scraped bookmark JSON files "
             f"(default: {DEFAULT_ARCHIVE_DIR})",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug-level logging",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    archive_dir = Path(args.dir).resolve() if args.dir else DEFAULT_ARCHIVE_DIR

    # Attach the archive directory to the handler class
    BookmarkHandler.archive_dir = archive_dir

    server = HTTPServer((args.host, args.port), BookmarkHandler)

    print("-" * 60)
    print(f"  X Bookmark Archiver Server")
    print(f"  Listening on  http://{args.host}:{args.port}")
    print(f"  Archive dir   {archive_dir}")
    print(f"  Endpoint      POST /api/bookmarks")
    print(f"  Health check  GET  /")
    print("-" * 60)
    print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down gracefully...")
        server.shutdown()
        return 0

    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    sys.exit(main())