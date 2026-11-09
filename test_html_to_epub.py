import contextlib
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from zipfile import ZipFile

import html_to_epub


class HtmlToEpubTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stderr = self.stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
        self.fetch = self.stack.enter_context(patch.object(
            html_to_epub.fetch_pages, "fetch_pages", side_effect=self.download_pages
        ))
        self.compose = self.stack.enter_context(patch.object(
            html_to_epub.compose_epub, "compose", side_effect=self.compose_book
        ))

    @staticmethod
    def download_pages(args, progress, quiet=False, task=None, failure_callback=None):
        total = args.end - args.start + 1
        progress.update(task, completed=total, description=f"Pages ({total}/{total})")
        return 0

    @staticmethod
    def compose_book(input_dir, output, **kwargs):
        callback = kwargs["phase_callback"]
        for phase in (
            "Composing Description", "Composing Cover Image", "Creating Epub", "Complete"
        ):
            callback(phase)
        return output

    def test_default_paths_ai_and_two_progress_tasks(self):
        with patch.object(html_to_epub, "Progress") as progress_class, patch.object(
            html_to_epub, "Live"
        ):
            self.assertEqual(html_to_epub.main([
                "--url", "www.page.com/something/NUMBER/whatev", "--title", "My Story"
            ]), 0)
        fetch_args, progress = self.fetch.call_args.args
        self.assertEqual(fetch_args.url, "https://www.page.com/something/NUMBER/whatev")
        self.assertEqual(fetch_args.folder, Path("output") / "My Story" / "pages")
        self.assertTrue(self.fetch.call_args.kwargs["quiet"])
        compose_args, compose_output = self.compose.call_args.args
        self.assertEqual(compose_args, Path("output") / "My Story" / "pages")
        self.assertEqual(compose_output, Path("output") / "My Story" / "My Story.epub")
        self.assertTrue(self.compose.call_args.kwargs["ai"])
        self.assertTrue(self.compose.call_args.kwargs["quiet"])
        self.assertEqual(
            [call.args[0] for call in progress.add_task.call_args_list],
            ["Pages (0/50)", "Composing Description"],
        )
        progress.update.assert_any_call(
            progress.add_task.return_value,
            description="Creating Epub", total=1, completed=1,
        )
        progress.remove_task.assert_called_once_with(progress.add_task.return_value)

    def test_single_page_url_uses_one_page_with_or_without_explicit_range(self):
        for extra in ([], ["--from", "3", "--to", "7"]):
            with self.subTest(extra=extra):
                self.fetch.reset_mock()
                self.compose.reset_mock()
                self.assertEqual(html_to_epub.main([
                    "--url", "www.lesswrong.com/posts/example?sort=asc#section",
                    "--title", "Article", *extra,
                ]), 0)
                fetch_args = self.fetch.call_args.args[0]
                self.assertEqual(fetch_args.url, "https://www.lesswrong.com/posts/example?sort=asc#section")
                self.assertEqual((fetch_args.start, fetch_args.end), (1, 1))
                self.assertEqual(self.compose.call_args.kwargs["origin"], "www.lesswrong.com")

    def test_explicit_range_scheme_and_origin(self):
        self.assertEqual(html_to_epub.main([
            "--url", "http://example.com:8080/NUMBER?sort=asc",
            "--from", "3", "--to", "7", "--title", "Other Story",
        ]), 0)
        fetch_args = self.fetch.call_args.args[0]
        self.assertEqual(fetch_args.url, "http://example.com:8080/NUMBER?sort=asc")
        self.assertEqual((fetch_args.start, fetch_args.end), (3, 7))
        self.assertEqual(self.compose.call_args.kwargs["origin"], "example.com")

    def test_existing_title_folder_recomposes_with_image_without_ai(self):
        self.compose.side_effect = None
        self.compose.return_value = Path("output") / "My Story" / "My Story.epub"
        self.assertEqual(html_to_epub.main([
            "--title", "My Story", "--image", "cover.png",
        ]), 0)
        self.fetch.assert_not_called()
        self.compose.assert_called_once_with(
            Path("output") / "My Story" / "pages",
            Path("output") / "My Story" / "My Story.epub",
            cover=Path("cover.png"),
            title="My Story",
            origin="www.fanfiction.net",
            ai=False,
        )

    def test_title_folder_uses_existing_epub_even_with_different_filename(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            book_dir = root / "output" / "My Story"
            book_dir.mkdir(parents=True)
            existing_epub = book_dir / "Renamed Story.epub"
            existing_epub.write_bytes(b"existing book")
            with contextlib.chdir(root), patch.object(
                html_to_epub.compose_epub, "edit_cover"
            ) as edit_cover:
                self.assertEqual(html_to_epub.main([
                    "--title", "My Story", "--image", "cover.png",
                ]), 0)
        self.fetch.assert_not_called()
        self.compose.assert_not_called()
        edit_cover.assert_called_once_with(
            Path("output") / "My Story" / "Renamed Story.epub",
            cover=Path("cover.png"),
        )

    def test_existing_epub_uses_compose_cover_editor(self):
        with patch.object(html_to_epub.compose_epub, "edit_cover") as edit_cover:
            self.assertEqual(html_to_epub.main([
                "--epub", "books/Story.epub", "--image", "cover.png",
            ]), 0)
        self.fetch.assert_not_called()
        self.compose.assert_not_called()
        edit_cover.assert_called_once_with(
            Path("books/Story.epub"), cover=Path("cover.png")
        )

    def test_supplied_image_is_used_in_normal_url_flow(self):
        self.assertEqual(html_to_epub.main([
            "--url", "https://page.com/NUMBER", "--title", "Story",
            "--image", "custom-cover.png",
        ]), 0)
        self.assertEqual(self.compose.call_args.kwargs["cover"], Path("custom-cover.png"))

    def test_fetch_failure_never_starts_composition(self):
        def fail_download(args, progress, quiet=False, task=None, failure_callback=None):
            failure_callback(args.start)
            return 1

        self.fetch.side_effect = fail_download
        self.assertEqual(html_to_epub.main([
            "--url", "https://page.com/NUMBER", "--title", "Story"
        ]), 1)
        self.compose.assert_not_called()
        self.assertIn("Failed during Downloading: Download failed on page 1", self.stderr.getvalue())

    def test_composition_failure_is_propagated(self):
        def fail_composition(input_dir, output, **kwargs):
            kwargs["phase_callback"]("Composing Cover Image")
            kwargs["phase_callback"]("Creating Epub")
            raise ValueError("bad EPUB")

        self.compose.side_effect = fail_composition
        self.assertEqual(html_to_epub.main([
            "--url", "https://page.com/NUMBER", "--title", "Story"
        ]), 1)
        self.assertIn("Failed during Creating Epub: bad EPUB", self.stderr.getvalue())

    def test_download_exception_is_reported_without_traceback(self):
        self.fetch.side_effect = OSError("disk is full")
        self.assertEqual(html_to_epub.main([
            "--url", "https://page.com/NUMBER", "--title", "Story"
        ]), 1)
        self.assertIn("Failed during Downloading: disk is full", self.stderr.getvalue())
        self.assertNotIn("Traceback", self.stderr.getvalue())

    def test_no_ai_option_disables_ai(self):
        self.assertEqual(html_to_epub.main([
            "--url", "https://page.com/NUMBER", "--title", "Story", "--no-ai"
        ]), 0)
        self.assertFalse(self.compose.call_args.kwargs["ai"])

    def test_status_rows_show_all_four_phases_complete(self):
        with patch.object(html_to_epub, "Live") as live_class, patch.object(
            html_to_epub, "Progress"
        ):
            self.assertEqual(html_to_epub.main([
                "--url", "https://page.com/NUMBER", "--title", "Story"
            ]), 0)
        final_display = live_class.return_value.__enter__.return_value.update.call_args.args[0]
        stage_lines = [line.plain for line in final_display.renderables[1].renderables]
        self.assertEqual(stage_lines, [
            "1/4 Downloading.... Complete",
            "2/4 Composing Description.... Complete",
            "3/4 Composing Cover Image.... Complete",
            "4/4 Creating Epub.... Complete",
        ])

    def test_failed_cover_does_not_prevent_epub_completion(self):
        def compose_with_cover_failure(input_dir, output, **kwargs):
            callback = kwargs["phase_callback"]
            callback("Composing Description")
            callback("Composing Cover Image")
            kwargs["warning_callback"]("Cover image skipped: timed out, retried 3 times.")
            callback("Creating Epub")
            callback("Complete")
            return output

        self.compose.side_effect = compose_with_cover_failure
        with patch.object(html_to_epub, "Live") as live_class, patch.object(
            html_to_epub, "Progress"
        ):
            self.assertEqual(html_to_epub.main([
                "--url", "https://page.com/NUMBER", "--title", "Story"
            ]), 0)
        final_display = live_class.return_value.__enter__.return_value.update.call_args.args[0]
        stage_lines = [line.plain for line in final_display.renderables[1].renderables]
        self.assertEqual(stage_lines, [
            "1/4 Downloading.... Complete",
            "2/4 Composing Description.... Complete",
            "3/4 Composing Cover Image.... Failed (timed out, retried 3 times)",
            "4/4 Creating Epub.... Complete",
        ])
        self.assertNotIn("Cover image skipped", self.stderr.getvalue())

    def test_invalid_input_rejected_before_launch(self):
        for arguments in (
            [], ["--url", "https://page.com/NUMBER"],
            ["--title", "Story"],
            ["--image", "cover.png"],
            ["--epub", "book.epub"],
            ["--epub", "book.epub", "--image", "cover.png", "--title", "Story"],
            ["--url", "https://page.com/NUMBER/NUMBER", "--title", "Story"],
            ["--url", "ftp://page.com/NUMBER", "--title", "Story"],
            ["--url", "https://page.com/NUMBER", "--title", ""],
            ["--url", "https://page.com/NUMBER", "--title", ".."],
            ["--url", "https://page.com/NUMBER", "--title", "parent\\Story"],
            ["--url", "https://page.com/NUMBER", "--title", "Story", "--from", "0"],
            ["--url", "https://page.com/NUMBER", "--title", "Story", "--from", "51"],
        ):
            with self.subTest(arguments=arguments), self.assertRaises(SystemExit) as exc:
                html_to_epub.main(arguments)
            self.assertEqual(exc.exception.code, 2)
        self.fetch.assert_not_called()
        self.compose.assert_not_called()


class HtmlToEpubIntegrationTests(unittest.TestCase):
    def test_real_children_compose_previously_downloaded_page(self):
        self.assert_real_children_compose("https://www.fanfiction.net/s/123/NUMBER/Story")

    def test_real_children_compose_single_page_url(self):
        self.assert_real_children_compose("https://www.fanfiction.net/s/123/1/Story")

    def assert_real_children_compose(self, url):
        scripts = Path(html_to_epub.__file__).resolve().parent
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = root / "output" / "My Story" / "pages"
            folder.mkdir(parents=True)
            (folder / "page_0001.html").write_text(
                '<html><body><div id="profile_top"><b>Source Title</b>'
                '<a href="/u/123/Example">Example Author</a>'
                '<div class="xcontrast_txt">A synopsis.</div></div>'
                '<select id="chap_select"><option value="1" selected>1. Chapter One'
                '</select><div id="storytext"><p>A short chapter.</p></div>'
                '</body></html>', encoding="utf-8",
            )
            (root / "www.fanfiction.net.settings").write_bytes(
                (scripts / "www.fanfiction.net.settings").read_bytes()
            )
            result = subprocess.run([
                sys.executable, str(scripts / "html_to_epub.py"),
                "--url", url,
                *(["--to", "1"] if "NUMBER" in url else []), "--title", "My Story",
                "--no-ai",
            ], cwd=root, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            output = result.stdout + result.stderr
            for noisy in ("[SAVED]", "Fetching:", "SUMMARY", "Successful:", "Skipped:"):
                self.assertNotIn(noisy, output)
            for phase in (
                "Pages (1/1)", "4/4 Creating Epub",
            ):
                self.assertIn(phase, output)
            with ZipFile(root / "output" / "My Story" / "My Story.epub") as archive:
                self.assertEqual(
                    [name for name in archive.namelist() if name.startswith("EPUB/chapter_")],
                    ["EPUB/chapter_0001.xhtml"],
                )
                package = archive.read("EPUB/content.opf")
                self.assertIn(b"My Story", package)
                self.assertNotIn(b"cover-image", package)
                self.assertIn(b"A short chapter.", archive.read("EPUB/chapter_0001.xhtml"))


if __name__ == "__main__":
    unittest.main()
