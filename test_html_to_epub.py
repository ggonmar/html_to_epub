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
        self.run = self.stack.enter_context(patch.object(html_to_epub.subprocess, "run"))
        self.run.return_value = Mock(returncode=0)
        self.scripts = Path(html_to_epub.__file__).resolve().parent

    def test_defaults_and_sequential_commands(self):
        self.assertEqual(html_to_epub.main([
            "--url", "www.page.com/something/NUMBER/whatev", "--title", "My Story"
        ]), 0)
        self.assertEqual(self.run.call_count, 2)
        self.assertEqual(self.run.call_args_list[0].args[0], [
            sys.executable, str(self.scripts / "fetch_pages.py"),
            "--url", "https://www.page.com/something/NUMBER/whatev",
            "--from", "1", "--to", "50", "--folder", "My Story",
        ])
        self.assertEqual(self.run.call_args_list[1].args[0], [
            sys.executable, str(self.scripts / "compose_epub.py"),
            "--folder", "My Story", "--origin", "www.page.com", "--title", "My Story",
        ])
        for call in self.run.call_args_list:
            self.assertEqual(call.kwargs, {"check": False})

    def test_explicit_range_scheme_and_origin(self):
        self.assertEqual(html_to_epub.main([
            "--url", "http://example.com:8080/NUMBER?sort=asc",
            "--from", "3", "--to", "7", "--title", "Other Story",
        ]), 0)
        command = self.run.call_args_list[0].args[0]
        self.assertIn("http://example.com:8080/NUMBER?sort=asc", command)
        self.assertEqual(command[command.index("--from") + 1], "3")
        self.assertEqual(command[command.index("--to") + 1], "7")
        command = self.run.call_args_list[1].args[0]
        self.assertEqual(command[command.index("--origin") + 1], "example.com")
        self.assertNotIn("--cover", command)

    def test_fetch_failure_never_starts_composition(self):
        self.run.return_value = Mock(returncode=5)
        self.assertEqual(html_to_epub.main([
            "--url", "https://page.com/NUMBER", "--title", "Story"
        ]), 5)
        self.run.assert_called_once()
        self.assertIn("Downloading pages failed", self.stderr.getvalue())

    def test_composition_failure_is_propagated(self):
        self.run.side_effect = [Mock(returncode=0), Mock(returncode=3)]
        self.assertEqual(html_to_epub.main([
            "--url", "https://page.com/NUMBER", "--title", "Story"
        ]), 3)
        self.assertEqual(self.run.call_count, 2)
        self.assertIn("Composing EPUB failed", self.stderr.getvalue())

    def test_process_start_failure_is_reported(self):
        self.run.side_effect = OSError("Could not launch Python")
        self.assertEqual(html_to_epub.main([
            "--url", "https://page.com/NUMBER", "--title", "Story"
        ]), 1)
        self.run.assert_called_once()
        self.assertIn("Could not launch Python", self.stderr.getvalue())

    def test_invalid_input_rejected_before_launch(self):
        for arguments in (
            [], ["--url", "https://page.com/NUMBER"],
            ["--title", "Story"],
            ["--url", "https://page.com/1", "--title", "Story"],
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
        self.run.assert_not_called()


class HtmlToEpubIntegrationTests(unittest.TestCase):
    def test_real_children_compose_previously_downloaded_page(self):
        scripts = Path(html_to_epub.__file__).resolve().parent
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = root / "My Story"
            folder.mkdir()
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
                "--url", "https://www.fanfiction.net/s/123/NUMBER/Story",
                "--to", "1", "--title", "My Story",
            ], cwd=root, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("Skipped: 1", result.stdout)
            with ZipFile(root / "My Story.epub") as archive:
                package = archive.read("EPUB/content.opf")
                self.assertIn(b"My Story", package)
                self.assertNotIn(b"cover-image", package)
                self.assertIn(b"A short chapter.", archive.read("EPUB/chapter_0001.xhtml"))


if __name__ == "__main__":
    unittest.main()
