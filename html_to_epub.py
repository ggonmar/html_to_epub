"""Download chapter pages, then compose an EPUB after a successful download."""

import argparse
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit

import fetch_pages


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="URL with one NUMBER placeholder")
    parser.add_argument("--from", dest="start", type=int, default=1)
    parser.add_argument("--to", dest="end", type=int, default=50)
    parser.add_argument("--title", required=True, help="Book title and download folder name")
    args = parser.parse_args(argv)
    if not args.title.strip() or args.title in (".", "..") or any(
        character in args.title for character in ("/", "\\", "\0")
    ):
        parser.error("--title must be a nonempty folder name, not a path")

    url = args.url.strip()
    if "://" not in url:
        url = "https://" + url
    download_args = [
        "--url", url, "--from", str(args.start), "--to", str(args.end),
        "--folder", args.title,
    ]
    validated = fetch_pages.parse_args(download_args)
    origin = urlsplit(validated.url).hostname
    scripts = Path(__file__).resolve().parent
    commands = [
        [sys.executable, str(scripts / "fetch_pages.py"), *download_args],
        [
            sys.executable, str(scripts / "compose_epub.py"),
            "--folder", args.title, "--origin", origin, "--title", args.title,
        ],
    ]
    for stage, command in zip(("Downloading pages", "Composing EPUB"), commands):
        try:
            result = subprocess.run(command, check=False)
        except OSError as exc:
            print(f"Error: {stage} could not start: {exc}", file=sys.stderr)
            return 1
        if result.returncode:
            print(f"Error: {stage} failed (exit code {result.returncode}).", file=sys.stderr)
            return result.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
