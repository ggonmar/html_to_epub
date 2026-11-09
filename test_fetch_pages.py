import contextlib
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from rich.console import Console
from rich.progress import Progress

import fetch_pages


class FetchPagesTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stdout = self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.stderr = self.stack.enter_context(
            contextlib.redirect_stderr(io.StringIO())
        )
        self.stack.enter_context(patch.object(fetch_pages, "SAVE_FILES", False))
        self.sleep = self.stack.enter_context(patch.object(fetch_pages.time, "sleep"))
        self.get = self.stack.enter_context(patch.object(fetch_pages.requests, "get"))
        self.prompt = self.stack.enter_context(patch("builtins.input"))
        self.progress_class = self.stack.enter_context(patch.object(fetch_pages, "Progress"))
        self.progress = self.progress_class.return_value.__enter__.return_value

    def response(self, status=200):
        response = Mock(status_code=status, reason="Forbidden" if status == 403 else "OK")
        response.headers = {}
        response.text = "<!DOCTYPE html><html><body>Test page</body></html>"
        response.content = response.text.encode("utf-8")
        if status >= 400:
            response.raise_for_status.side_effect = fetch_pages.requests.HTTPError(
                f"HTTP {status}", response=response
            )
        return response

    def test_default_range_and_referer(self):
        self.get.return_value = self.response()
        self.assertEqual(fetch_pages.main([
            "--base", "https://page.com/xyz/", "--trailing", "/whatever/"
        ]), 0)
        self.assertEqual(self.get.call_count, 50)
        self.assertEqual(self.get.call_args_list[0].args[0], "https://page.com/xyz/1/whatever")
        self.assertEqual(self.get.call_args_list[-1].args[0], "https://page.com/xyz/50/whatever")
        self.assertEqual(self.get.call_args.kwargs["headers"]["Referer"], "https://page.com/xyz")
        self.prompt.assert_not_called()
        self.assertNotIn("Referer", fetch_pages.HEADERS)
        self.assertNotIn("Sending request with headers", self.stderr.getvalue())
        self.assertNotIn("User-Agent:", self.stderr.getvalue())

    def test_verbose_displays_headers_with_redacted_cookie_on_retry(self):
        self.get.side_effect = [self.response(403), self.response()]
        self.prompt.return_value = "session=private-cookie"
        self.assertEqual(fetch_pages.main([
            "--base", "https://page.com", "--trailing", "whatever",
            "--to", "1", "--verbose"
        ]), 0)
        output = self.stderr.getvalue()
        self.assertEqual(output.count("Sending request with headers"), 2)
        self.assertIn("User-Agent:", output)
        self.assertIn("Referer: https://page.com", output)
        self.assertIn("Cookie: [REDACTED]", output)
        self.assertNotIn("private-cookie", output)

    def test_url_template_default_range_and_referer(self):
        self.get.return_value = self.response()
        self.assertEqual(fetch_pages.main([
            "--url", " https://page.com/xyz/NUMBER/whatever "
        ]), 0)
        self.assertEqual(self.get.call_count, 50)
        self.assertEqual(self.get.call_args_list[0].args[0], "https://page.com/xyz/1/whatever")
        self.assertEqual(self.get.call_args_list[-1].args[0], "https://page.com/xyz/50/whatever")
        self.assertEqual(self.get.call_args.kwargs["headers"]["Referer"], "https://page.com/xyz")
        self.prompt.assert_not_called()

    def test_single_page_url_preserved_and_saved_as_page_one(self):
        self.get.return_value = self.response()
        url = "https://page.com/article?sort=asc#section"
        for extra in ([], ["--from", "3", "--to", "7"]):
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as output:
                self.get.reset_mock()
                with patch.object(fetch_pages, "SAVE_FILES", True):
                    self.assertEqual(fetch_pages.main([
                        "--url", url, "--folder", output, *extra,
                    ]), 0)
                self.get.assert_called_once()
                self.assertEqual(self.get.call_args.args[0], url)
                self.assertEqual(
                    self.get.call_args.kwargs["headers"]["Referer"],
                    "https://page.com/article",
                )
                self.assertEqual(
                    [page.name for page in Path(output).iterdir()], ["page_0001.html"]
                )
        self.sleep.assert_not_called()

    def test_url_template_preserves_path_query_and_fragment(self):
        for template in (
            "https://page.com/xyz/NUMBER/whatever/?sort=asc#chapter",
            "https://page.com/xyz?page=NUMBER&sort=asc#chapter",
            "https://page.com/chapter-NUMBER.html",
            "https://page.com/xyz/NUMBER",
        ):
            with self.subTest(template=template):
                self.get.reset_mock()
                self.get.return_value = self.response()
                self.assertEqual(fetch_pages.main([
                    "--url", template, "--from", "3", "--to", "4", "--verbose"
                ]), 0)
                self.assertEqual(
                    [call.args[0] for call in self.get.call_args_list],
                    [template.replace("NUMBER", str(number)) for number in (3, 4)],
                )
                self.assertIn(f"[*] URL pattern: {template}", self.stdout.getvalue())

    def test_invalid_url_template_arguments_rejected_before_requests(self):
        for arguments in (
            [],
            ["--base", "https://page.com"],
            ["--trailing", "whatever"],
            ["--url", ""],
            ["--url", "page.com/NUMBER"],
            ["--url", "ftp://page.com/NUMBER"],
            ["--url", "https://[invalid/NUMBER"],
            ["--url", "https://page.com/NUMBER?page=NUMBER"],
            ["--url", "https://NUMBER.page.com/whatever"],
            ["--url", "https://page.com/whatever#NUMBER"],
            ["--url", "https://page.com/NUMBER", "--base", "https://page.com"],
            ["--url", "https://page.com/NUMBER", "--trailing", "whatever"],
        ):
            with self.subTest(arguments=arguments), self.assertRaises(SystemExit) as exc:
                fetch_pages.main(arguments)
            self.assertEqual(exc.exception.code, 2)
        self.get.assert_not_called()

    def test_startup_output_depends_on_verbose(self):
        self.get.return_value = self.response()
        with tempfile.TemporaryDirectory() as output:
            folder = Path(output) / "Make A Wish"
            for verbose in (False, True):
                with self.subTest(verbose=verbose):
                    self.stdout.seek(0)
                    self.stdout.truncate(0)
                    with patch.object(fetch_pages, "SAVE_FILES", True):
                        self.assertEqual(fetch_pages.main([
                            "--base", "https://page.com", "--trailing", "whatever",
                            "--from", "3", "--to", "3", "--folder", str(folder),
                            *(["--verbose"] if verbose else [])
                        ]), 0)
                    text = self.stdout.getvalue()
                    for label in (
                        "[*] Output directory:", "[*] Downloading pages from",
                        "[*] URL pattern:", "[*] Delay between requests:",
                        "[*] Verbose mode:",
                    ):
                        self.assertEqual(label in text, verbose)
                    self.assertEqual(
                        "Downloading 3 pages of Make A Wish..." in text, not verbose
                    )

    def test_rich_terminal_rendering(self):
        terminal = io.StringIO()
        console = Console(
            file=terminal, force_terminal=True, width=160, record=True
        )
        self.progress_class.side_effect = Progress
        self.get.return_value = self.response()
        with patch.object(fetch_pages, "Console", return_value=console):
            self.assertEqual(fetch_pages.main([
                "--base", "https://page.com", "--trailing", "whatever", "--to", "2"
            ]), 0)
        rendered = console.export_text()
        for expected in ("Downloading pages", "100%", "2/2", "ETA", "OK: 2", "Skipped: 0"):
            self.assertIn(expected, rendered)
        self.assertIn("\x1b[", terminal.getvalue())

    def test_cookie_retry_restarts_at_first_requested_page(self):
        requests_seen = []

        def request(url, **kwargs):
            requests_seen.append((url, dict(kwargs["headers"])))
            return self.response(403 if len(requests_seen) == 1 else 200)

        self.get.side_effect = request
        self.prompt.return_value = "Cookie: session=test-secret; other=value"
        self.assertEqual(fetch_pages.main([
            "--url", "https://page.com/xyz/NUMBER/whatever",
            "--from", "3", "--to", "4"
        ]), 0)
        self.assertEqual([url for url, _ in requests_seen], [
            "https://page.com/xyz/3/whatever",
            "https://page.com/xyz/3/whatever",
            "https://page.com/xyz/4/whatever",
        ])
        self.assertNotIn("Cookie", requests_seen[0][1])
        for _, headers in requests_seen[1:]:
            self.assertEqual(headers["Cookie"], "session=test-secret; other=value")
        self.assertNotIn("test-secret", self.stderr.getvalue())
        self.assertNotIn("Cookie", fetch_pages.HEADERS)
        self.prompt.assert_called_once()

    def test_failed_cookie_retry_aborts(self):
        self.get.return_value = self.response(403)
        self.prompt.return_value = "session=invalid"
        self.assertEqual(fetch_pages.main([
            "--base", "https://page.com/xyz", "--trailing", "whatever"
        ]), 1)
        self.assertEqual(self.get.call_count, 2)
        self.prompt.assert_called_once()

    def test_empty_or_unavailable_cookie_aborts(self):
        for value in ("", "Cookie:", "session=x\ninjected=y", EOFError(), KeyboardInterrupt()):
            with self.subTest(value=value):
                self.get.reset_mock()
                self.get.return_value = self.response(403)
                self.prompt.side_effect = value if isinstance(value, BaseException) else None
                self.prompt.return_value = value
                self.assertEqual(fetch_pages.main([
                    "--base", "https://page.com/xyz", "--trailing", "whatever"
                ]), 1)
                self.assertEqual(self.get.call_count, 1)

    def test_later_failure_does_not_prompt(self):
        self.get.side_effect = [self.response(), self.response(403)]
        self.assertEqual(fetch_pages.main([
            "--base", "https://page.com/xyz", "--trailing", "whatever", "--to", "2"
        ]), 1)
        self.prompt.assert_not_called()

    def test_saves_html(self):
        self.get.return_value = self.response()
        with tempfile.TemporaryDirectory() as output:
            with patch.object(fetch_pages, "SAVE_FILES", True), patch.object(
                fetch_pages, "OUTPUT_DIR", output
            ):
                self.assertEqual(fetch_pages.main([
                    "--url", "https://page.com/xyz/NUMBER/whatever", "--to", "1"
                ]), 0)
            self.assertEqual(
                (Path(output) / "page_0001.html").read_text(encoding="utf-8"),
                self.get.return_value.text,
            )

    def test_invalid_arguments_rejected_before_requests(self):
        for extra in (
            ["--from", "0"], ["--from", "5", "--to", "4"],
            ["--to", "abc"], ["--base", "not-a-url"],
            ["--base", "https://page.com/xyz?query=1"],
            ["--retry-wait", "0"], ["--retry-wait", "-1"],
            ["--retry-wait", "nan"], ["--retry-wait", "inf"],
        ):
            with self.subTest(extra=extra), self.assertRaises(SystemExit) as exc:
                fetch_pages.main([
                    "--base", "https://page.com/xyz", "--trailing", "whatever", *extra
                ])
            self.assertEqual(exc.exception.code, 2)
        self.get.assert_not_called()

    def test_transient_statuses_retry_with_exponential_waits(self):
        for status in (408, 429, 500, 502, 503, 504):
            with self.subTest(status=status):
                self.sleep.reset_mock()
                self.get.reset_mock()
                first, second = self.response(status), self.response(status)
                self.get.side_effect = [first, second, self.response()]
                html = fetch_pages.download_page("https://page.com", {}, retry_wait=7)
                self.assertIn("<html>", html)
                self.assertEqual(self.get.call_count, 3)
                self.assertEqual([call.args[0] for call in self.sleep.call_args_list], [7, 14])
                first.close.assert_called_once()
                second.close.assert_called_once()

    def test_network_failures_retry_and_stop_at_three_attempts(self):
        for error in (
            fetch_pages.requests.Timeout,
            fetch_pages.requests.ConnectionError,
            fetch_pages.requests.exceptions.ChunkedEncodingError,
            fetch_pages.requests.exceptions.ContentDecodingError,
        ):
            with self.subTest(error=error):
                self.sleep.reset_mock()
                self.get.reset_mock()
                self.get.side_effect = error("test failure")
                self.assertIsNone(fetch_pages.download_page("https://page.com", {}, retry_wait=2))
                self.assertEqual(self.get.call_count, 3)
                self.assertEqual([call.args[0] for call in self.sleep.call_args_list], [2, 4])

    def test_exhausted_http_retries_report_failure(self):
        self.get.return_value = self.response(429)
        self.assertIsNone(fetch_pages.download_page("https://page.com", {}, retry_wait=1))
        self.assertEqual(self.get.call_count, 3)
        self.assertEqual(self.sleep.call_count, 2)

    def test_permanent_http_error_does_not_retry(self):
        self.get.return_value = self.response(404)
        self.assertIsNone(fetch_pages.download_page("https://page.com", {}))
        self.get.assert_called_once()
        self.sleep.assert_not_called()

    def test_retry_after_seconds_and_invalid_header(self):
        for header, expected in (("20", 20), ("1", 5), ("invalid", 5)):
            with self.subTest(header=header):
                self.sleep.reset_mock()
                response = self.response(429)
                response.headers["Retry-After"] = header
                self.get.side_effect = [response, self.response()]
                self.assertIsNotNone(fetch_pages.download_page("https://page.com", {}))
                self.sleep.assert_called_once_with(expected)

    def test_retry_after_http_date(self):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        header = format_datetime(now + timedelta(seconds=60), usegmt=True)
        with patch.object(fetch_pages, "datetime") as clock:
            clock.now.return_value = now
            self.assertEqual(fetch_pages.retry_after_seconds(header), 60)
            self.assertEqual(
                fetch_pages.retry_after_seconds(
                    format_datetime(now - timedelta(seconds=60), usegmt=True)
                ), 0
            )

    def test_progress_counts_pages_not_retry_attempts(self):
        self.get.side_effect = [
            self.response(429), self.response(), self.response(404)
        ]
        self.assertEqual(fetch_pages.main([
            "--base", "https://page.com", "--trailing", "whatever",
            "--from", "3", "--to", "4", "--retry-wait", "8"
        ]), 1)
        self.assertEqual(self.progress.add_task.call_args.kwargs["total"], 2)
        self.assertEqual(
            [call.kwargs["completed"] for call in self.progress.update.call_args_list],
            [1, 2],
        )
        self.assertEqual(
            self.progress.update.call_args.kwargs,
            dict(completed=2, successful=1, failed=1, skipped=0),
        )
        self.assertEqual(self.get.call_count, 3)
        self.assertEqual(self.sleep.call_args_list[0].args, (8,))
        self.prompt.assert_not_called()

    def test_custom_folder_creates_nested_directories(self):
        self.get.return_value = self.response()
        with tempfile.TemporaryDirectory() as output:
            folder = Path(output) / "my pages" / "chapters"
            with patch.object(fetch_pages, "SAVE_FILES", True):
                self.assertEqual(fetch_pages.main([
                    "--base", "https://page.com/xyz", "--trailing", "whatever",
                    "--to", "1", "--folder", str(folder)
                ]), 0)
            self.assertEqual(
                (folder / "page_0001.html").read_text(encoding="utf-8"),
                self.get.return_value.text,
            )

    def test_invalid_folder_aborts_before_download(self):
        with tempfile.TemporaryDirectory() as output:
            folder = Path(output) / "existing_file"
            folder.write_text("test", encoding="utf-8")
            with patch.object(fetch_pages, "SAVE_FILES", True):
                self.assertEqual(fetch_pages.main([
                    "--base", "https://page.com/xyz", "--trailing", "whatever",
                    "--folder", str(folder)
                ]), 1)
            self.assertIn("Cannot create output folder", self.stderr.getvalue())
            self.get.assert_not_called()

    def test_existing_pages_skip_requests_and_preserve_files(self):
        with tempfile.TemporaryDirectory() as output:
            folder = Path(output)
            for number in (1, 2):
                (folder / f"page_{number:04d}.html").write_text("existing", encoding="utf-8")
            with patch.object(fetch_pages, "SAVE_FILES", True):
                self.assertEqual(fetch_pages.main([
                    "--url", "https://page.com/NUMBER/whatever",
                    "--to", "2", "--folder", output
                ]), 0)
            self.get.assert_not_called()
            self.prompt.assert_not_called()
            self.sleep.assert_not_called()
            self.assertEqual(
                self.progress.update.call_args.kwargs,
                dict(completed=2, successful=0, failed=0, skipped=2),
            )
            self.assertEqual((folder / "page_0001.html").read_text(encoding="utf-8"), "existing")
            self.assertNotIn("[SKIP]", self.stdout.getvalue())

    def test_verbose_displays_skipped_files(self):
        with tempfile.TemporaryDirectory() as output:
            page = Path(output) / "page_0001.html"
            page.write_text("existing", encoding="utf-8")
            with patch.object(fetch_pages, "SAVE_FILES", True):
                self.assertEqual(fetch_pages.main([
                    "--base", "https://page.com", "--trailing", "whatever",
                    "--to", "1", "--folder", output, "--verbose"
                ]), 0)
            self.assertIn(f"[SKIP] Already downloaded: {page}", self.stdout.getvalue())
            self.get.assert_not_called()

    def test_first_actual_download_prompts_after_skipping(self):
        self.get.side_effect = [self.response(403), self.response()]
        self.prompt.return_value = "session=test"
        with tempfile.TemporaryDirectory() as output:
            (Path(output) / "page_0001.html").write_text("existing", encoding="utf-8")
            with patch.object(fetch_pages, "SAVE_FILES", True):
                self.assertEqual(fetch_pages.main([
                    "--base", "https://page.com", "--trailing", "whatever",
                    "--to", "2", "--folder", output
                ]), 0)
            self.assertEqual(self.get.call_count, 2)
            for call in self.get.call_args_list:
                self.assertEqual(call.args[0], "https://page.com/2/whatever")
            self.prompt.assert_called_once()
            self.assertTrue((Path(output) / "page_0002.html").is_file())
            self.assertEqual(
                self.progress.update.call_args.kwargs,
                dict(completed=2, successful=1, failed=0, skipped=1),
            )
            self.progress.stop.assert_called_once()
            self.progress.start.assert_called_once()


if __name__ == "__main__":
    unittest.main()
