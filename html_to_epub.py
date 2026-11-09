"""Download chapter pages, then compose an EPUB after a successful download."""

import argparse
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

from rich.console import Console, Group
from rich.live import Live
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeRemainingColumn
from rich.text import Text

import compose_epub
import fetch_pages


STAGE_NAMES = (
    "Downloading",
    "Composing Description",
    "Composing Cover Image",
    "Creating Epub",
)


def stage_display(statuses):
    lines = []
    styles = {"Running": "yellow", "Complete": "green", "Failed": "red", "Skipped": "dim"}
    for number, name in enumerate(STAGE_NAMES, 1):
        line = Text(f"{number}/4 {name}.... ")
        status = statuses[name]
        style = "red" if status.startswith("Failed") else styles.get(status)
        line.append(status, style=style)
        lines.append(line)
    return Group(*lines)


def output_name(title):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title).strip().rstrip(". ")
    reserved_names = {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10)),
    }
    if name.upper() in reserved_names:
        name = f"_{name}"
    return name or "book"


def find_existing_epub(book_dir, expected_epub):
    if expected_epub.is_file():
        return expected_epub
    candidates = sorted(book_dir.glob("*.epub"))
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise ValueError(
            f"Multiple EPUB files found in {book_dir}; specify one with --epub"
        )
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", help="Single-page URL, or URL with one NUMBER placeholder")
    parser.add_argument("--from", dest="start", type=int, default=1)
    parser.add_argument("--to", dest="end", type=int, default=50)
    parser.add_argument("--title", help="Book title/output folder name")
    parser.add_argument("--epub", type=Path, help="Existing EPUB to update with --image")
    parser.add_argument("--image", type=Path, help="Cover image to include or replace")
    parser.add_argument("--origin", help="Source domain when recomposing saved pages")
    parser.add_argument("--no-ai", action="store_true", help="Skip optional Ollama and ComfyUI requests")
    args = parser.parse_args(argv)

    if args.epub is not None:
        if args.url is not None or args.title is not None:
            parser.error("--epub cannot be combined with --url or --title")
        if args.image is None:
            parser.error("--image is required with --epub")
        try:
            compose_epub.edit_cover(args.epub, cover=args.image)
        except Exception as exc:
            print(f"Failed to update EPUB cover: {exc}", file=sys.stderr)
            return 1
        return 0

    if args.title is None:
        parser.error("--title is required unless --epub is used")
    if not args.title.strip() or args.title in (".", "..") or any(
        character in args.title for character in ("/", "\\", "\0")
    ):
        parser.error("--title must be a nonempty folder name, not a path")

    book_dir = Path("output") / output_name(args.title)
    pages_dir = book_dir / "pages"
    epub_path = book_dir / f"{output_name(args.title)}.epub"
    if args.url is None:
        if args.image is None:
            parser.error("Provide --url to download, or --image to recompose saved pages")
        try:
            existing_epub = find_existing_epub(book_dir, epub_path)
            if existing_epub is not None:
                compose_epub.edit_cover(existing_epub, cover=args.image)
                return 0
            compose_epub.compose(
                pages_dir,
                epub_path,
                cover=args.image,
                title=args.title,
                origin=args.origin or "www.fanfiction.net",
                ai=False,
            )
        except Exception as exc:
            print(f"Failed to create EPUB with supplied cover: {exc}", file=sys.stderr)
            return 1
        return 0

    url = args.url.strip()
    if "://" not in url:
        url = "https://" + url
    validated = fetch_pages.parse_args([
        "--url", url, "--from", str(args.start), "--to", str(args.end),
    ])
    origin = args.origin or urlsplit(validated.url).hostname
    fetch_args = fetch_pages.parse_args([
        "--url", validated.url, "--from", str(validated.start), "--to", str(validated.end),
        "--folder", str(pages_dir),
    ])
    total_pages = validated.end - validated.start + 1
    failed_pages = []
    warnings = []
    error_message = None
    current_stage = "Downloading"
    statuses = {name: "Pending" for name in STAGE_NAMES}
    statuses["Downloading"] = "Running"
    console = Console(file=sys.stderr)

    progress = Progress(
        SpinnerColumn(),
        TextColumn("{task.description}"),
        BarColumn(),
        TextColumn("{task.percentage:>3.0f}%"),
        TimeRemainingColumn(),
        console=console,
        expand=True,
        auto_refresh=False,
    )
    download_task = progress.add_task(
        f"Pages (0/{total_pages})", total=total_pages
    )
    active_task = None

    with Live(
        Group(progress, stage_display(statuses)),
        console=console,
        refresh_per_second=10,
    ) as live:
        def refresh_statuses():
            live.update(Group(progress, stage_display(statuses)))

        def update_phase(phase):
            nonlocal active_task, current_stage
            if phase == "Complete":
                if statuses["Creating Epub"] == "Running":
                    statuses["Creating Epub"] = "Complete"
                if active_task is not None:
                    progress.update(active_task, description="Creating Epub", total=1, completed=1)
                refresh_statuses()
                return
            previous = STAGE_NAMES[STAGE_NAMES.index(phase) - 1]
            if statuses[previous] == "Running":
                statuses[previous] = "Complete"
            if args.no_ai and phase in ("Composing Description", "Composing Cover Image"):
                statuses[phase] = "Skipped"
            elif args.image is not None and phase == "Composing Cover Image":
                statuses[phase] = "Complete"
            else:
                statuses[phase] = "Running"
            current_stage = phase
            if active_task is not None:
                progress.update(active_task, description=phase)
            refresh_statuses()

        def report_warning(message):
            if message.startswith("Cover image skipped:"):
                detail = message.partition(":")[2].strip().rstrip(".")
                statuses["Composing Cover Image"] = f"Failed ({detail})"
            elif message.startswith("Description skipped"):
                statuses["Composing Description"] = "Failed"
                warnings.append(message)
            else:
                warnings.append(message)
            refresh_statuses()

        try:
            result = fetch_pages.fetch_pages(
                fetch_args,
                progress,
                quiet=True,
                task=download_task,
                failure_callback=failed_pages.append,
            )
            if result:
                statuses["Downloading"] = "Failed"
                detail = f" on page {failed_pages[0]}" if failed_pages else ""
                raise RuntimeError(f"Download failed{detail}")
            statuses["Downloading"] = "Complete"
            progress.remove_task(download_task)
            active_task = progress.add_task("Composing Description")
            refresh_statuses()
            update_phase("Composing Description")
            compose_epub.compose(
                pages_dir,
                epub_path,
                cover=args.image,
                title=args.title,
                origin=origin,
                ai=not args.no_ai,
                quiet=True,
                phase_callback=update_phase,
                warning_callback=report_warning,
            )
        except Exception as exc:
            statuses[current_stage] = "Failed"
            refresh_statuses()
            error_message = f"Failed during {current_stage}: {exc}"

    for warning in warnings:
        print(warning, file=sys.stderr)
    if error_message:
        print(error_message, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
