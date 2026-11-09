#!/usr/bin/env python3
"""
Download one HTML page, or numbered pages using NUMBER (default range: 1 to 50).

Usage: python fetch_pages.py --url "https://page.com/xyz/NUMBER/whatever" --from 1 --to 20 --folder "my_pages"
NUMBER is replaced with each page number. Legacy --base and --trailing
options remain supported, but cannot be combined with --url.
Without NUMBER, --url downloads one page and ignores --from and --to.
The output folder defaults to downloaded_pages; missing directories are created.
Existing page files in that folder are skipped without making a request.
Transient failures get up to 3 attempts, waiting N then 2*N seconds
(--retry-wait, default: 5). A longer Retry-After header takes precedence.
The progress bar counts completed pages, not individual retry attempts.
Install dependencies with: python -m pip install -r requirements.txt
Rich displays live progress, elapsed time, ETA, and outcome counts.
Use --verbose to display request headers, detailed error response headers,
startup details, and individual skipped-file messages.
If the first request fails, paste the Cookie request-header value from an
authorized browser session. It is kept in memory only and retried once.
"""

import argparse
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math
import os
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit

import requests
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

# Output directory for saving HTML files
OUTPUT_DIR = "downloaded_pages"
SAVE_FILES = True  # Set to False to just print HTML to stdout

# ============= REQUEST HEADERS =============
# User-Agent is often required - many sites block requests without it
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36 Edg/156.0.0.0',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    # Let requests advertise only compression formats it can decode.
    'DNT': '1',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
    'x-requested-with': 'XMLHttpRequest'

}

# Optional: Add cookies if needed (e.g., for authentication)
COOKIES = {}
# Example: COOKIES = {'session_id': 'your_session_id_here'}

# Delay between requests (in seconds) to avoid rate limiting
DELAY_BETWEEN_REQUESTS = 1.0

# Timeout for requests (in seconds)
REQUEST_TIMEOUT = 15

def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", help="Absolute URL, optionally with one NUMBER placeholder in its path or query")
    parser.add_argument("--base", help="Legacy base URL, also used as Referer")
    parser.add_argument("--trailing", help="Legacy path after the page number")
    parser.add_argument("--from", dest="start", type=int, default=1)
    parser.add_argument("--to", dest="end", type=int, default=50)
    parser.add_argument("--folder", type=Path, default=Path(OUTPUT_DIR),
                        help="Output folder (default: downloaded_pages)")
    parser.add_argument("--retry-wait", type=float, default=5,
                        help="Initial retry wait in seconds (default: 5; 3 attempts total)")
    parser.add_argument("--verbose", action="store_true",
                        help="Display headers (secrets redacted) and skipped-file messages")
    args = parser.parse_args(argv)
    if args.url is not None:
        if args.base is not None or args.trailing is not None:
            parser.error("--url cannot be combined with --base or --trailing")
        args.url = args.url.strip()
        try:
            url = urlsplit(args.url)
            if url.scheme not in ("http", "https") or not url.hostname:
                parser.error("--url must be an absolute HTTP or HTTPS URL")
        except ValueError as exc:
            parser.error(f"Invalid --url URL: {exc}")
        if "NUMBER" not in args.url:
            args.start = args.end = 1
        elif args.url.count("NUMBER") != 1 or (
            "NUMBER" not in url.path and "NUMBER" not in url.query
        ):
            parser.error("--url must contain exactly one NUMBER placeholder in its path or query")
        args.base = url._replace(
            path=url.path.split("NUMBER", 1)[0].rstrip("/"), query="", fragment=""
        ).geturl()
    else:
        if args.base is None or args.trailing is None:
            parser.error("Provide --url, or both --base and --trailing")
        args.base = args.base.strip().rstrip("/")
        args.trailing = args.trailing.strip("/")
        try:
            base = urlsplit(args.base)
            if base.scheme not in ("http", "https") or not base.hostname:
                parser.error("--base must be an absolute HTTP or HTTPS URL")
        except ValueError as exc:
            parser.error(f"Invalid --base URL: {exc}")
        if base.query or base.fragment:
            parser.error("--base must not contain a query string or fragment")
        args.url = f"{args.base}/NUMBER/{args.trailing}"
    if args.start < 1 or args.end < args.start:
        parser.error("Require 1 <= --from <= --to")
    if not math.isfinite(args.retry_wait) or args.retry_wait <= 0:
        parser.error("--retry-wait must be a positive, finite number")
    return args


def create_url(base_url, trailing, iteration):
    return f"{base_url.rstrip('/')}/{iteration}/{trailing.strip('/')}"


def retry_after_seconds(value, quiet=False):
    """Parse Retry-After as seconds or an HTTP date."""
    try:
        if value.isdigit():
            return int(value)
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        return max(0, (date - datetime.now(timezone.utc)).total_seconds())
    except (ValueError, TypeError, OverflowError):
        if not quiet:
            print(f"[WARNING] Invalid Retry-After header: {value!r}; using backoff.", file=sys.stderr)
        return 0


def request_with_retries(url, headers, timeout, retry_wait, quiet=False):
    for attempt in range(1, 4):
        wait = retry_wait * 2 ** (attempt - 1)
        try:
            response = requests.get(
                url, headers=headers, cookies=COOKIES, timeout=timeout, allow_redirects=True
            )
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError,
                requests.exceptions.ChunkedEncodingError,
                requests.exceptions.ContentDecodingError) as exc:
            if attempt == 3:
                raise
            reason = str(exc)
        else:
            if response.status_code not in (408, 429) and not 500 <= response.status_code <= 599:
                return response
            if attempt == 3:
                return response
            reason = f"HTTP {response.status_code}"
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                wait = max(wait, retry_after_seconds(retry_after, quiet))
            response.close()
        if not quiet:
            print(
                f"[RETRY] {reason} for {url}. Waiting {wait:g}s "
                f"before attempt {attempt + 1}/3.",
                file=sys.stderr,
                flush=True,
            )
        time.sleep(wait)


def show_progress(progress, task, completed, successful, failed, skipped=0, description=None):
    update = {
        "completed": completed,
        "successful": successful,
        "failed": failed,
        "skipped": skipped,
    }
    if description is not None:
        update["description"] = description
    progress.update(task, **update)


def download_page(
    url, headers, timeout=REQUEST_TIMEOUT, retry_wait=5, verbose=False, quiet=False
):
    """Download a page from the given URL with detailed error reporting."""
    try:
        if verbose:
            print("  Sending request with headers:", file=sys.stderr)
            for key, value in headers.items():
                if key.lower() in ("cookie", "authorization", "proxy-authorization"):
                    value = "[REDACTED]"
                value_short = value[:60] + ('...' if len(value) > 60 else '')
                print(f"      {key}: {value_short}", file=sys.stderr)
            if COOKIES:
                print("    Cookies: [REDACTED]", file=sys.stderr)
        
        response = request_with_retries(url, headers, timeout, retry_wait, quiet)
        
        if verbose and response.status_code != 200:
            print(f"\n  Response Status: {response.status_code} {response.reason}", file=sys.stderr)
            print(f"  Response Headers:", file=sys.stderr)
            for key, value in response.headers.items():
                if key.lower() == "set-cookie":
                    value = "[REDACTED]"
                value_short = str(value)[:80] + ('...' if len(str(value)) > 80 else '')
                print(f"      {key}: {value_short}", file=sys.stderr)
            print(f"  Content-Encoding: {response.headers.get('Content-Encoding', 'none')}", file=sys.stderr)
            print(f"  Apparent size: {len(response.content)} bytes", file=sys.stderr)
        
        if response.status_code == 403:
            if quiet:
                return None
            print(f"\n[ERROR] HTTP 403 FORBIDDEN - Server rejected the request", file=sys.stderr)
            print(f"   URL: {url}", file=sys.stderr)
            print(f"\n   POSSIBLE CAUSES:", file=sys.stderr)
            print("   - Cookies/authentication required or expired", file=sys.stderr)
            print("   - Access restrictions, rate limiting, or bot protection", file=sys.stderr)
            print(f"\n   HOW TO FIX:", file=sys.stderr)
            print(f"   1. Open the URL in your browser: {url}", file=sys.stderr)
            print(f"   2. Press F12 to open Developer Tools", file=sys.stderr)
            print(f"   3. Go to 'Network' tab", file=sys.stderr)
            print(f"   4. Refresh the page (F5)", file=sys.stderr)
            print(f"   5. Look at the first successful request (GET to the page)", file=sys.stderr)
            print(f"   6. Copy the 'User-Agent' header from the 'Request Headers' section", file=sys.stderr)
            print(f"   7. Also check for: Referer, Cookie, X-Requested-With headers", file=sys.stderr)
            print(f"   8. Update the HEADERS dict in this script with those values", file=sys.stderr)
            print(f"\n   ADDITIONAL TIPS:", file=sys.stderr)
            print("   - Respect rate limits and the site's access policies.", file=sys.stderr)
            print("   - A cookie does not guarantee access or override server restrictions.", file=sys.stderr)
            return None
        
        response.raise_for_status()
        
        # Ensure we get the text properly (handles encoding automatically)
        # requests library auto-decompresses gzip/deflate when using .text
        return response.text
        
    except requests.exceptions.Timeout:
        if not quiet:
            print(f"[ERROR] Timeout: Request took more than {timeout}s for {url}", file=sys.stderr)
        return None
    except requests.exceptions.ConnectionError as e:
        if not quiet:
            print(f"[ERROR] Connection Error for {url}: {e}", file=sys.stderr)
        return None
    except requests.exceptions.RequestException as e:
        if not quiet:
            print(f"[ERROR] Error downloading {url}: {e}", file=sys.stderr)
        return None


def save_html(html_content, filename, folder, quiet=False):
    """Save HTML content to a file."""
    filepath = folder / filename
    try:
        filepath.write_text(html_content, encoding='utf-8')
        if not quiet:
            print(f"  [SAVED] {filepath}")
        return True
    except IOError as e:
        if not quiet:
            print(f"[ERROR] Error saving {filename}: {e}", file=sys.stderr)
        return False


def main(argv=None):
    """Main function to download pages in the specified range."""
    args = parse_args(argv)
    with Progress(
        SpinnerColumn(),
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(bar_width=None),
        TextColumn("[bold]{task.percentage:>3.0f}%"),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        TextColumn("ETA"),
        TimeRemainingColumn(),
        TextColumn(
            "[green]OK: {task.fields[successful]}[/] "
            "[red]Failed: {task.fields[failed]}[/] "
            "[yellow]Skipped: {task.fields[skipped]}[/]"
        ),
        console=Console(file=sys.stderr),
        expand=True,
    ) as progress:
        return fetch_pages(args, progress)


def fetch_pages(args, progress, quiet=False, task=None, failure_callback=None):
    headers = {**HEADERS, "Referer": args.base}
    # Create output directory if saving files
    if SAVE_FILES:
        try:
            args.folder.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            if not quiet:
                print(f"[ERROR] Cannot create output folder {args.folder}: {e}", file=sys.stderr)
            return 1
        if args.verbose:
            print(f"[*] Output directory: {os.path.abspath(args.folder)}\n")
    
    if args.verbose and not quiet:
        print(f"[*] Downloading pages from {args.start} to {args.end}...")
        print(f"[*] URL pattern: {args.url}")
        print(f"[*] Delay between requests: {DELAY_BETWEEN_REQUESTS}s")
        print(f"[*] Verbose mode: {args.verbose}\n")
    elif not quiet:
        print(f"Downloading {args.end} pages of {args.folder.resolve().name}...")
    
    successful = 0
    failed = 0
    skipped = 0
    first_download = True
    total = args.end - args.start + 1
    task = task or progress.add_task(
        "Downloading pages", total=total, successful=0, failed=0, skipped=0
    )
    
    for i in range(args.start, args.end + 1):
        filename = f"page_{i:04d}.html"
        if SAVE_FILES and (args.folder / filename).is_file():
            skipped += 1
            if args.verbose and not quiet:
                print(f"[SKIP] Already downloaded: {args.folder / filename}")
            completed = i - args.start + 1
            show_progress(
                progress, task, completed, successful, failed, skipped,
                f"Pages ({completed}/{total})" if quiet else None,
            )
            continue

        url = (
            args.url.replace("NUMBER", str(i))
            if args.trailing is None else create_url(args.base, args.trailing, i)
        )
        if not quiet:
            print(f"\n[{i}/{args.end}] Fetching: {url}")
        
        html = download_page(
            url, headers, retry_wait=args.retry_wait, verbose=args.verbose, quiet=quiet
        )
        if html is None and first_download:
            progress.stop()
            try:
                cookie = input(
                    "First download failed. Paste a valid Cookie header value "
                    "(name=value; name2=value2), or press Enter to abort: "
                ).strip()
            except (EOFError, KeyboardInterrupt):
                if not quiet:
                    print("\n[ERROR] Cookie entry cancelled; stopping.", file=sys.stderr)
                return 1
            if cookie.lower().startswith("cookie:"):
                cookie = cookie.split(":", 1)[1].strip()
            if not cookie or "\r" in cookie or "\n" in cookie:
                if not quiet:
                    print("[ERROR] No valid single-line cookie supplied; stopping.", file=sys.stderr)
                return 1
            headers["Cookie"] = cookie
            progress.start()
            if not quiet:
                print("[*] Retrying the first page with the supplied cookie.")
            html = download_page(url, headers, retry_wait=args.retry_wait, verbose=args.verbose)
            if html is None:
                if not quiet:
                    print("[ERROR] First page still failed; stopping.", file=sys.stderr)
                if failure_callback is not None:
                    failure_callback(i)
                return 1
        first_download = False
        
        if html is not None:
            if SAVE_FILES:
                if save_html(html, filename, args.folder, quiet):
                    successful += 1
                else:
                    failed += 1
            else:
                print(f"  [OK] Content length: {len(html)} bytes")
                successful += 1
        else:
            failed += 1

        completed = i - args.start + 1
        show_progress(
            progress, task, completed, successful, failed, skipped,
            f"Pages ({completed}/{total})" if quiet else None,
        )
        if failed and failure_callback is not None:
            failure_callback(i)

        # Add delay between requests to be respectful
        if i < args.end:
            time.sleep(DELAY_BETWEEN_REQUESTS)
    
    if not quiet:
        print(f"\n{'='*50}")
        print("SUMMARY")
        print(f"{'='*50}")
        print(f"Successful: {successful}")
        print(f"Failed: {failed}")
        print(f"Skipped: {skipped}")
        print(f"Total: {successful + failed + skipped}")
        print(f"{'='*50}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
