import base64
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from zipfile import ZIP_STORED, ZipFile

from ebooklib import epub
from lxml import etree

import compose_epub


class ComposeEpubTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stdout = self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.stderr = self.stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        (self.root / "www.fanfiction.net.settings").write_bytes(
            Path(compose_epub.__file__).with_name("www.fanfiction.net.settings").read_bytes()
        )
        self.folder = self.root / "Saved Chapters"
        self.folder.mkdir()
        (self.folder / "page_0001.html").write_text(
            '<html><body><div id="profile_top"><b>Sample Story</b>'
            '<a href="/u/123/Example">Example Author</a>'
            '<div class="xcontrast_txt">A sample synopsis.</div></div>'
            '<select id="chap_select"><option value="1" selected>1. Chapter One'
            '</select><div id="storytext"><p>Chapter One</p>'
            '<p>A <em>short</em> chapter.</p></div></body></html>',
            encoding="utf-8",
        )
        self.cover = self.root / "custom-cover.png"
        self.cover.write_bytes(base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
            "/x8AAwMCAO+aL1sAAAAASUVORK5CYII="
        ))
        self.output = self.root / "result.epub"

    def test_custom_title_cover_metadata_and_summary(self):
        result = compose_epub.compose(self.folder, self.output, self.cover, "Custom Title")
        self.assertEqual(result, self.output)
        book = epub.read_epub(str(result))
        self.assertEqual(book.get_metadata("DC", "title")[0][0], "Custom Title")
        self.assertEqual(book.get_metadata("DC", "language")[0][0], "en")
        description = book.get_metadata("DC", "description")[0][0]
        self.assertIn("Example Author", description)
        self.assertIn("A sample synopsis.", description)
        self.assertNotIn("Sarah1281", description)
        with ZipFile(result) as archive:
            self.assertEqual(archive.read("EPUB/custom-cover.png"), self.cover.read_bytes())
            self.assertIn(b"cover-image", archive.read("EPUB/content.opf"))
        summary = self.stdout.getvalue()
        self.assertIn(f"EPUB: {result.resolve()}", summary)
        self.assertIn("Chapters: 1", summary)
        self.assertRegex(summary, r"Size: \d+\.\d{2} KB")
        self.assertIn("Composing chapters", self.stderr.getvalue())

    def test_ai_description_is_used_when_cover_service_fails(self):
        with patch.object(
            compose_epub, "request_ai_description",
            return_value=("A generated story description.", "A dramatic cover scene.",
                          "Ollama system prompt.",
                          "Title: Story\nAuthor: Author\n\nFirst chapter:\n" + "x" * 200),
        ), patch.object(compose_epub, "generate_comfyui_cover", return_value=None):
            result = compose_epub.compose(self.folder, self.output, ai=True)
        book = epub.read_epub(str(result))
        self.assertEqual(
            book.get_metadata("DC", "description")[0][0],
            "A generated story description.",
        )
        self.assertEqual(book.get_metadata("OPF", "cover"), [])
        with ZipFile(result) as archive:
            self.assertNotIn(b"cover-image", archive.read("EPUB/content.opf"))

    def test_ai_description_and_generated_cover_are_added(self):
        with patch.object(
            compose_epub, "request_ai_description",
            return_value=("A generated story description.", "A dramatic cover scene.",
                          "Ollama system prompt.",
                          "Title: Story\nAuthor: Author\n\nFirst chapter:\n" + "x" * 200),
        ) as request_description, patch.object(
            compose_epub, "generate_comfyui_cover", return_value=self.cover
        ) as generate_cover:
            result = compose_epub.compose(self.folder, self.output, ai=True)
        self.assertEqual(
            request_description.call_args.args[0], "Chapter One A short chapter."
        )
        comfyui_prompt = generate_cover.call_args.args[0]
        self.assertIn("A dramatic cover scene.", comfyui_prompt)
        self.assertIn("spell it exactly: Sample Story", comfyui_prompt)
        self.assertIn("spell it exactly: Example Author", comfyui_prompt)
        self.assertEqual(generate_cover.call_args.args[1], self.root / "cover.png")
        prompts = (self.root / "ai_prompts.txt").read_text(encoding="utf-8")
        self.assertIn("Ollama system prompt.", prompts)
        self.assertIn("First chapter:\n" + "x" * 150 + "...", prompts)
        self.assertNotIn("x" * 151, prompts)
        self.assertIn("Prompt 1: Ollama", prompts)
        self.assertIn("Response by Ollama", prompts)
        self.assertIn("A generated story description.", prompts)
        self.assertIn("Cover Description:\nA dramatic cover scene.", prompts)
        self.assertIn(comfyui_prompt, prompts)
        self.assertIn("Prompt2: ComfyUI", prompts)
        self.assertIn("Request sent to ComfyUI", prompts)
        self.assertIn("Response by ComfyUI", prompts)
        self.assertIn(f"Generated image saved to: {self.cover}", prompts)
        book = epub.read_epub(str(result))
        self.assertEqual(
            book.get_metadata("DC", "description")[0][0],
            "A generated story description.",
        )
        with ZipFile(result) as archive:
            self.assertIn(b"cover-image", archive.read("EPUB/content.opf"))

    def test_ollama_connection_failure_falls_back_and_composes_epub(self):
        with patch.object(
            compose_epub.requests, "post",
            side_effect=compose_epub.requests.ConnectionError("offline"),
        ):
            result = compose_epub.compose(self.folder, self.output, ai=True)
        book = epub.read_epub(str(result))
        self.assertIn("A sample synopsis.", book.get_metadata("DC", "description")[0][0])
        self.assertEqual(book.get_metadata("OPF", "cover"), [])
        prompts = (self.root / "ai_prompts.txt").read_text(encoding="utf-8")
        self.assertIn("First chapter:", prompts)
        self.assertIn("[Not generated]", prompts)
        self.assertIn("Error: no cover prompt was returned by Ollama.", prompts)

    def test_ollama_description_and_cover_prompt_response(self):
        response = Mock()
        response.json.return_value = {"message": {
            "content": '{"description":"Short description.","cover_prompt":"A bookish scene."}',
        }}
        with patch.object(compose_epub.requests, "post", return_value=response) as post:
            result = compose_epub.request_ai_description(
                "First chapter text.", "A Story", "An Author", {
                    "OLLAMA_BASE_URL": "http://ollama:11434/",
                    "OLLAMA_MODEL": "gemma4:26b",
                },
            )
        description, cover_prompt, system_prompt, user_prompt = result
        self.assertEqual((description, cover_prompt), ("Short description.", "A bookish scene."))
        self.assertEqual(post.call_args.args[0], "http://ollama:11434/api/chat")
        self.assertEqual(post.call_args.kwargs["json"]["model"], "gemma4:26b")
        self.assertEqual(post.call_args.kwargs["json"]["keep_alive"], 0)
        self.assertIn("between 100 and 200 words", system_prompt)
        self.assertIn("First chapter text.", user_prompt)

    def test_comfyui_connection_failure_skips_cover(self):
        warning = Mock()
        with patch.object(
            compose_epub.requests, "post",
            side_effect=compose_epub.requests.ConnectTimeout("offline"),
        ) as post, patch.object(compose_epub.time, "sleep"):
            result = compose_epub.generate_comfyui_cover(
                "A cover prompt.", self.root / "generated.png", {
                    "COMFYUI_BASE_URL": "http://comfyui:8188",
                    "COMFYUI_CHECKPOINT": "sd_xl_base_1.0.safetensors",
                },
                warning,
            )
        self.assertIsNone(result)
        self.assertFalse((self.root / "generated.png").exists())
        self.assertEqual(post.call_count, 4)
        warning.assert_called_once_with(
            "Cover image skipped: timed out, retried 3 times."
        )

    def test_comfyui_generated_png_is_saved(self):
        queued = Mock()
        queued.json.return_value = {"prompt_id": "job-1"}
        history = Mock()
        history.json.return_value = {"job-1": {
            "outputs": {"7": {"images": [{
                "filename": "cover_00001_.png", "subfolder": "", "type": "output",
            }]}}
        }}
        image = Mock(content=(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        ))
        with patch.object(compose_epub.requests, "post", return_value=queued) as post, patch.object(
            compose_epub.requests, "get", side_effect=[history, image]
        ) as get:
            result = compose_epub.generate_comfyui_cover(
                "A cover prompt.", self.root / "generated.png", {
                    "COMFYUI_BASE_URL": "http://comfyui:8188",
                    "COMFYUI_CHECKPOINT": "sd_xl_base_1.0.safetensors",
                },
            )
        self.assertEqual(result, self.root / "generated.png")
        self.assertEqual(result.read_bytes(), image.content)
        self.assertEqual(post.call_args.args[0], "http://comfyui:8188/prompt")
        self.assertEqual(get.call_count, 2)

    def test_default_title_and_automatic_output_name(self):
        with contextlib.chdir(self.root):
            result = compose_epub.compose(self.folder, cover=self.cover)
            self.assertEqual(result, Path("Sample Story.epub"))
            book = epub.read_epub(str(result))
            self.assertEqual(
                book.get_metadata("DC", "title")[0][0], "Sample Story"
            )

    def test_omitted_cover_adds_no_image_or_cover_metadata(self):
        with contextlib.chdir(self.root):
            Path("cover_good.png").write_bytes(self.cover.read_bytes())
            result = compose_epub.compose(self.folder, self.output)
        book = epub.read_epub(str(result))
        self.assertEqual(book.get_metadata("OPF", "cover"), [])
        with ZipFile(result) as archive:
            self.assertNotIn(b"cover-image", archive.read("EPUB/content.opf"))
            self.assertFalse(any(name.endswith(".png") for name in archive.namelist()))

    def test_explicit_missing_cover_reports_error(self):
        with self.assertRaises(FileNotFoundError):
            compose_epub.compose(self.folder, self.output, self.root / "missing.png")

    def test_cli_options(self):
        with patch("sys.argv", [
            "compose_epub.py", "--folder", str(self.folder), "--cover", str(self.cover),
            "--title", "Custom Title", "--output", str(self.output),
        ]), patch.object(compose_epub, "compose") as compose:
            compose_epub.main()
        compose.assert_called_once_with(
            self.folder, self.output, self.cover, "Custom Title", "www.fanfiction.net"
        )

    def test_legacy_input_alias_and_defaults(self):
        with patch("sys.argv", ["compose_epub.py", "--input", str(self.folder)]), patch.object(
            compose_epub, "compose"
        ) as compose:
            compose_epub.main()
        compose.assert_called_once_with(self.folder, None, None, None, "www.fanfiction.net")

    def make_existing_epub(self, cover_page=False):
        book = epub.EpubBook()
        book.set_identifier("original-id")
        book.set_title("Original Title")
        book.set_language("en")
        book.add_author("Original Author")
        book.set_cover("old-cover.png", self.cover.read_bytes(), create_page=cover_page)
        chapter = epub.EpubHtml(title="Chapter One", file_name="text/chapter.xhtml")
        chapter.content = "<h1>Chapter One</h1><p>Keep this text unchanged.</p>"
        book.add_item(chapter)
        book.add_item(epub.EpubNav())
        book.add_item(epub.EpubNcx())
        book.spine = ["nav", chapter]
        book.toc = [chapter]
        if cover_page:
            page = book.get_item_with_id("cover")
            book.spine.insert(0, page)
            book.toc.insert(0, page)
            book.guide = [{"type": "cover", "title": "Cover", "href": "cover.xhtml"}]
        epub.write_epub(str(self.output), book)
        with ZipFile(self.output, "a") as archive:
            archive.writestr("unrelated/data.bin", b"Keep these bytes too")
        return self.output

    def assert_no_old_cover(self, path):
        with ZipFile(path) as archive:
            self.assertNotIn("EPUB/old-cover.png", archive.namelist())
            self.assertNotIn("EPUB/cover.xhtml", archive.namelist())
            for name in ("EPUB/content.opf", "EPUB/nav.xhtml", "EPUB/toc.ncx"):
                self.assertNotIn(b"cover.xhtml", archive.read(name))
                self.assertNotIn(b"old-cover.png", archive.read(name))
            self.assertIsNone(archive.testzip())
            self.assertEqual(archive.infolist()[0].filename, "mimetype")
            self.assertEqual(archive.infolist()[0].compress_type, 0)
        book = epub.read_epub(str(path))
        self.assertEqual(book.get_metadata("DC", "title")[0][0], "Original Title")
        self.assertEqual(book.get_metadata("DC", "identifier")[0][0], "original-id")

    def test_remove_existing_cover_in_place_preserves_unrelated_content(self):
        for cover_page in (False, True):
            with self.subTest(cover_page=cover_page):
                original = self.make_existing_epub(cover_page)
                with ZipFile(original) as archive:
                    before = {name: archive.read(name) for name in (
                        "EPUB/text/chapter.xhtml", "unrelated/data.bin", "META-INF/container.xml"
                    )}
                self.assertEqual(compose_epub.edit_cover(original), original)
                self.assert_no_old_cover(original)
                with ZipFile(original) as archive:
                    for name, content in before.items():
                        self.assertEqual(archive.read(name), content)
                    package = etree.fromstring(archive.read("EPUB/content.opf"))
                    self.assertFalse(package.xpath("//*[local-name()='meta'][@name='cover']"))
                    self.assertNotIn(b"cover-image", archive.read("EPUB/content.opf"))

    def test_replace_cover_to_separate_output_preserves_source(self):
        original = self.make_existing_epub(cover_page=True)
        original_bytes = original.read_bytes()
        replacement = self.root / "replacement.gif"
        replacement.write_bytes(base64.b64decode(
            "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
        ))
        destination = self.root / "updated.epub"
        self.assertEqual(
            compose_epub.edit_cover(original, destination, replacement), destination
        )
        self.assertEqual(original.read_bytes(), original_bytes)
        self.assert_no_old_cover(destination)
        book = epub.read_epub(str(destination))
        with ZipFile(destination) as archive:
            package = etree.fromstring(archive.read("EPUB/content.opf"))
            cover_id = package.xpath(
                "//*[local-name()='meta'][@name='cover']/@content"
            )[0]
        image = book.get_item_with_id(cover_id)
        self.assertEqual(image.get_content(), replacement.read_bytes())
        self.assertEqual(image.media_type, "image/gif")
        with ZipFile(destination) as archive:
            self.assertIn(b"cover-image", archive.read("EPUB/content.opf"))

    def test_coverless_epub_can_be_edited_repeatedly(self):
        compose_epub.compose(self.folder, self.output)
        compose_epub.edit_cover(self.output)
        compose_epub.edit_cover(self.output, cover=self.cover)
        compose_epub.edit_cover(self.output, cover=self.cover)
        with ZipFile(self.output) as archive:
            package = etree.fromstring(archive.read("EPUB/content.opf"))
            self.assertEqual(len(package.xpath(
                "//*[local-name()='meta'][@name='cover']"
            )), 1)
        compose_epub.edit_cover(self.output)
        with ZipFile(self.output) as archive:
            self.assertFalse(any(name.endswith(".png") for name in archive.namelist()))

    def test_epub2_cover_with_nonstandard_package_path(self):
        source = self.root / "epub2.epub"
        with ZipFile(source, "w") as archive:
            archive.writestr("mimetype", b"application/epub+zip", compress_type=ZIP_STORED)
            archive.writestr("META-INF/container.xml", (
                '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                '<rootfiles><rootfile full-path="Book/package.opf"/></rootfiles></container>'
            ))
            archive.writestr("Book/package.opf", (
                '<package xmlns="http://www.idpf.org/2007/opf" version="2.0">'
                '<metadata><meta name="cover" content="picture"/></metadata><manifest>'
                '<item id="picture" href="images/old%20cover.png" media-type="image/png"/>'
                '<item id="front" href="text/front.xhtml" media-type="application/xhtml+xml"/>'
                '<item id="chapter" href="text/chapter.xhtml" media-type="application/xhtml+xml"/>'
                '</manifest><spine><itemref idref="front"/><itemref idref="chapter"/></spine>'
                '</package>'
            ))
            archive.writestr("Book/images/old cover.png", self.cover.read_bytes())
            archive.writestr("Book/text/front.xhtml", (
                '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Cover</title></head>'
                '<body><img src="../images/old%20cover.png"/></body></html>'
            ))
            archive.writestr("Book/text/chapter.xhtml", (
                '<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Chapter text</p></body></html>'
            ))
        compose_epub.edit_cover(source, cover=self.cover)
        with ZipFile(source) as archive:
            self.assertNotIn("Book/images/old cover.png", archive.namelist())
            self.assertNotIn("Book/text/front.xhtml", archive.namelist())
            package = etree.fromstring(archive.read("Book/package.opf"))
            self.assertNotIn(b"cover-image", archive.read("Book/package.opf"))
            self.assertEqual(len(package.xpath("//*[local-name()='meta'][@name='cover']")), 1)
            self.assertEqual(len(package.xpath("//*[local-name()='itemref']")), 2)
        compose_epub.edit_cover(source)
        with ZipFile(source) as archive:
            self.assertFalse(any(name.endswith(".png") for name in archive.namelist()))
            package = etree.fromstring(archive.read("Book/package.opf"))
            self.assertEqual(len(package.xpath("//*[local-name()='itemref']")), 1)
            self.assertIn(b"Chapter text", archive.read("Book/text/chapter.xhtml"))

    def test_epub3_landmark_cover_without_image_metadata(self):
        source = self.make_existing_epub(cover_page=True)
        modified = self.root / "landmark.epub"
        with ZipFile(source) as archive, ZipFile(modified, "w") as target:
            for entry in archive.infolist():
                content = archive.read(entry.filename)
                if entry.filename == "EPUB/content.opf":
                    root = etree.fromstring(content)
                    for node in root.xpath(
                        "//*[local-name()='meta'][@name='cover'] | //*[local-name()='guide']"
                    ):
                        node.getparent().remove(node)
                    for node in root.xpath("//*[local-name()='item'][@properties='cover-image']"):
                        del node.attrib["properties"]
                    content = etree.tostring(root)
                target.writestr(entry, content)
        compose_epub.edit_cover(modified)
        self.assert_no_old_cover(modified)

    def test_cover_edit_failures_leave_original_unchanged(self):
        original = self.make_existing_epub()
        original_bytes = original.read_bytes()
        with self.assertRaises(FileNotFoundError):
            compose_epub.edit_cover(original, cover=self.root / "missing.png")
        with patch.object(compose_epub.os, "replace", side_effect=OSError("test failure")):
            with self.assertRaises(OSError):
                compose_epub.edit_cover(original)
        self.assertEqual(original.read_bytes(), original_bytes)
        self.assertEqual(list(self.root.glob("*.epub")), [original])

    def test_epub_cli_routes_without_composing(self):
        for cover in (None, self.cover):
            with self.subTest(cover=cover), patch("sys.argv", [
                "compose_epub.py", "--epub", str(self.output),
                *(["--cover", str(cover)] if cover is not None else []),
            ]), patch.object(compose_epub, "edit_cover") as edit, patch.object(
                compose_epub, "compose"
            ) as compose:
                compose_epub.main()
                edit.assert_called_once_with(self.output, None, cover)
                compose.assert_not_called()

    def test_epub_cli_edits_without_settings_or_html(self):
        source = self.make_existing_epub(cover_page=True)
        original_bytes = source.read_bytes()
        destination = self.root / "cli-output.epub"
        empty = self.root / "empty"
        empty.mkdir()
        with contextlib.chdir(empty), patch("sys.argv", [
            "compose_epub.py", "--epub", str(source), "--output", str(destination)
        ]):
            compose_epub.main()
        self.assert_no_old_cover(destination)
        self.assertEqual(source.read_bytes(), original_bytes)
        with contextlib.chdir(empty), patch("sys.argv", [
            "compose_epub.py", "--epub", str(source), "--cover", str(self.cover)
        ]):
            compose_epub.main()
        self.assert_no_old_cover(source)
        with ZipFile(source) as archive:
            package = etree.fromstring(archive.read("EPUB/content.opf"))
            cover_id = package.xpath("//*[local-name()='meta'][@name='cover']/@content")[0]
            cover_href = package.xpath(
                "//*[local-name()='item'][@id=$uid]/@href", uid=cover_id
            )[0]
            self.assertEqual(archive.read("EPUB/" + cover_href), self.cover.read_bytes())

    def test_epub_cli_rejects_html_only_options(self):
        for arguments in (
            ["--folder", str(self.folder)], ["--input", str(self.folder)],
            ["--title", "New Title"], ["--origin", "example.com"],
        ):
            with self.subTest(arguments=arguments), patch("sys.argv", [
                "compose_epub.py", "--epub", str(self.output), *arguments
            ]), self.assertRaises(SystemExit) as exc:
                compose_epub.main()
            self.assertEqual(exc.exception.code, 2)

    def test_epub_cli_reports_invalid_archive(self):
        self.output.write_bytes(b"not an epub")
        with patch("sys.argv", [
            "compose_epub.py", "--epub", str(self.output)
        ]), self.assertRaises(SystemExit) as exc:
            compose_epub.main()
        self.assertEqual(exc.exception.code, 1)
        self.assertIn("Error:", self.stderr.getvalue())


if __name__ == "__main__":
    unittest.main()