import argparse
import configparser
import hashlib
import os
import posixpath
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlsplit
from zipfile import BadZipFile, ZipFile

from bs4 import BeautifulSoup
from ebooklib import epub
from lxml import etree
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


class SiteSettings:
    """Loads and manages extraction settings for a specific origin."""

    def __init__(self, settings_path):
        """Load settings from a .settings file."""
        self.config = configparser.ConfigParser()
        if not self.config.read(settings_path):
            raise ValueError(f"Settings file not found: {settings_path}")

    def get(self, section, key, fallback=None):
        """Get a config value with optional fallback."""
        try:
            return self.config.get(section, key)
        except (configparser.NoSectionError, configparser.NoOptionError):
            return fallback

    def get_bool(self, section, key, fallback=False):
        """Get a boolean config value."""
        try:
            return self.config.getboolean(section, key)
        except (configparser.NoSectionError, configparser.NoOptionError):
            return fallback

    @property
    def title_selector(self):
        return self.get("metadata", "title_selector")

    @property
    def author_selector(self):
        return self.get("metadata", "author_selector")

    @property
    def chapter_select_selector(self):
        return self.get("metadata", "chapter_select_selector")

    @property
    def summary_selector(self):
        return self.get("metadata", "summary_selector")

    @property
    def content_selector(self):
        return self.get("content", "content_selector")

    @property
    def unwanted_selectors(self):
        unwanted = self.get("content", "unwanted_selectors", "")
        return [s.strip() for s in unwanted.split(",") if s.strip()]

    @property
    def remove_attributes(self):
        attrs = self.get("content", "remove_attributes", "")
        return [a.strip() for a in attrs.split(",") if a.strip()]

    @property
    def selected_option_selector(self):
        return self.get("chapter", "selected_option_selector")

    @property
    def title_cleanup_pattern(self):
        return self.get("chapter", "title_cleanup_pattern", "")

    @property
    def default_chapter_title(self):
        return self.get("chapter", "default_chapter_title", "Chapter {number}")

    @property
    def page_filename_pattern(self):
        return self.get("validation", "page_filename_pattern", "page_*.html")

    @property
    def validate_chapter_list(self):
        return self.get_bool("validation", "validate_chapter_list", True)

    @property
    def verify_content(self):
        return self.get_bool("validation", "verify_content", True)


def normalized_text(markup):
    return " ".join(BeautifulSoup(markup, "lxml").stripped_strings)


def compose(input_dir, output=None, cover=None, title=None, origin=None):
    """
    Compose an EPUB from HTML pages.

    Args:
        input_dir: Directory containing page_*.html files
        output: Output EPUB path
        cover: Optional cover image path
        title: Optional override title
        origin: Origin domain (e.g., "www.fanfiction.net") to load settings
    """
    # Load settings for the origin
    if origin is None:
        origin = "www.fanfiction.net"

    settings_file = Path(f"{origin}.settings")
    if not settings_file.exists():
        raise ValueError(
            f"Settings file not found: {settings_file}\n"
            f"Please create a {origin}.settings file with extraction patterns."
        )

    settings = SiteSettings(settings_file)

    with Progress(
        SpinnerColumn(),
        TextColumn("{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        TextColumn("ETA"),
        TimeRemainingColumn(),
        console=Console(file=sys.stderr),
    ) as progress:
        output, chapter_count = compose_book(
            input_dir, output, cover, title, progress, settings
        )

    size_kb = output.stat().st_size / 1024
    size = f"{size_kb / 1024:.2f} MB" if size_kb >= 1024 else f"{size_kb:.2f} KB"
    print(f"EPUB: {output.resolve()}")
    print(f"Pages: {chapter_count} (source chapter pages; reader pagination varies)")
    print(f"Size: {size}")
    return output


def compose_book(input_dir, output, cover, title, progress, settings):
    metadata_task = progress.add_task("Reading files and metadata", total=1)
    pages = sorted(
        input_dir.glob(settings.page_filename_pattern),
        key=lambda path: int(path.stem.removeprefix("page_")),
    )
    if not pages:
        raise ValueError(
            f"No {settings.page_filename_pattern} files found in {input_dir}"
        )

    first = BeautifulSoup(pages[0].read_bytes(), "html.parser")
    title_node = first.select_one(settings.title_selector)
    author_node = first.select_one(settings.author_selector)
    if title_node is None or author_node is None:
        raise ValueError("Missing story title or author in the first page")
    title = title if title is not None else title_node.get_text(strip=True)
    if not title.strip():
        raise ValueError("The book title must not be empty")
    if output is None:
        filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title).strip().rstrip(". ")
        output = Path(f"{filename}.epub")
    author = author_node.get_text(strip=True)
    chapter_menu = first.select_one(settings.chapter_select_selector)
    options = chapter_menu.select("option") if chapter_menu is not None else []
    expected_numbers = [int(option["value"]) for option in options]
    actual_numbers = [int(page.stem.removeprefix("page_")) for page in pages]
    if settings.validate_chapter_list and expected_numbers and actual_numbers != expected_numbers:
        raise ValueError("Downloaded chapters do not match the chapter list")

    book = epub.EpubBook()
    identifier = hashlib.sha256(f"{title}|{author}".encode()).hexdigest()
    book.set_identifier(f"urn:sha256:{identifier}")
    book.set_title(title)
    book.set_language("en")
    book.add_author(author)
    if cover is not None:
        book.set_cover(cover.name, cover.read_bytes(), create_page=False)
    summary_node = first.select_one(settings.summary_selector)
    summary = summary_node.get_text(" ", strip=True) if summary_node is not None else ""
    book.add_metadata(
        "DC",
        "description",
        f"An unofficial fanfiction by {author}. {summary} "
        "This fan-written story is not an official publication and is not "
        "affiliated with or endorsed by the original work's rights holders.",
    )
    stylesheet = epub.EpubItem(
        uid="style",
        file_name="style/book.css",
        media_type="text/css",
        content=b"body { line-height: 1.4; } p { margin: 0 0 0.7em; }",
    )
    book.add_item(stylesheet)
    progress.advance(metadata_task)
    chapters = []
    source_texts = []
    for number, page in progress.track(
        zip(actual_numbers, pages), total=len(pages), description="Composing chapters"
    ):
        soup = BeautifulSoup(page.read_bytes(), "html.parser")
        content = soup.select_one(settings.content_selector)
        if content is None or not content.get_text(strip=True):
            raise ValueError(f"Missing or empty story content in {page.name}")
        source_texts.append(normalized_text(str(content)))
        for unwanted in content.select(", ".join(settings.unwanted_selectors)):
            unwanted.decompose()
        for element in content.find_all(True):
            for attribute in list(element.attrs):
                if attribute.startswith("on") or attribute in settings.remove_attributes:
                    del element[attribute]
        selected = soup.select_one(settings.selected_option_selector)
        if selected is not None and int(selected["value"]) != number:
            raise ValueError(f"Wrong chapter saved in {page.name}")

        chapter_title = f"Chapter {number}"
        if selected is not None:
            chapter_text = str(selected.find(string=True, recursive=False)).strip()
            if settings.title_cleanup_pattern:
                chapter_text = re.sub(settings.title_cleanup_pattern, "", chapter_text)
            chapter_title = chapter_text if chapter_text else chapter_title
        else:
            chapter_title = settings.default_chapter_title.format(number=number)

        chapter = epub.EpubHtml(
            title=chapter_title,
            file_name=f"chapter_{number:04d}.xhtml",
            lang="en",
        )
        chapter.content = content.decode_contents()
        chapter.add_item(stylesheet)
        book.add_item(chapter)
        chapters.append(chapter)

    book.toc = chapters
    book.spine = ["nav", *chapters]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    write_task = progress.add_task("Writing EPUB", total=1)
    epub.write_epub(str(output), book)
    progress.advance(write_task)

    namespaces = {"opf": "http://www.idpf.org/2007/opf"}
    with ZipFile(output) as archive:
        first_entry = archive.infolist()[0]
        if first_entry.filename != "mimetype" or first_entry.compress_type != 0:
            raise ValueError("Invalid EPUB mimetype entry")
        if archive.read("mimetype") != b"application/epub+zip":
            raise ValueError("Invalid EPUB mimetype")
        package = etree.fromstring(archive.read("EPUB/content.opf"))
        spine = package.findall("opf:spine/opf:itemref", namespaces)
        if [item.get("idref") for item in spine] != ["nav", *[chapter.id for chapter in chapters]]:
            raise ValueError("EPUB reading order does not match the chapters")
        if settings.verify_content:
            for chapter, source_text in progress.track(
                zip(chapters, source_texts), total=len(chapters), description="Verifying chapter text"
            ):
                markup = archive.read(f"EPUB/{chapter.file_name}")
                root = etree.fromstring(markup)
                body = root.find("{http://www.w3.org/1999/xhtml}body")
                if normalized_text(etree.tostring(body)) != source_text:
                    raise ValueError(f"Content changed in {chapter.file_name}")
        xml_files = [
            name for name in archive.namelist()
            if name.endswith((".xhtml", ".opf", ".ncx", ".xml"))
        ]
        for name in progress.track(xml_files, description="Validating EPUB XML"):
            etree.fromstring(archive.read(name))

    return output, len(chapters)


def edit_cover(input_epub, output=None, cover=None):
    """Edit only cover-related archive entries, replacing the output atomically."""
    output = input_epub if output is None else output
    namespaces = {
        "opf": "http://www.idpf.org/2007/opf",
        "container": "urn:oasis:names:tc:opendocument:xmlns:container",
        "xhtml": "http://www.w3.org/1999/xhtml",
        "ncx": "http://www.daisy.org/z3986/2005/ncx/",
    }
    image_types = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".gif": "image/gif", ".svg": "image/svg+xml", ".webp": "image/webp",
    }
    image = cover.read_bytes() if cover is not None else None
    if cover is not None and cover.suffix.lower() not in image_types:
        raise ValueError("Cover must be a JPEG, PNG, GIF, SVG, or WebP image")

    def parse_xml(content):
        return etree.fromstring(
            content, parser=etree.XMLParser(resolve_entities=False, no_network=True)
        )

    def archive_path(document, href):
        return posixpath.normpath(posixpath.join(
            posixpath.dirname(document), unquote(urlsplit(href).path)
        ))

    def xml_bytes(root):
        return etree.tostring(root, encoding="utf-8", xml_declaration=True)

    with ZipFile(input_epub) as source:
        if source.read("mimetype") != b"application/epub+zip":
            raise ValueError("Invalid EPUB mimetype")
        container = parse_xml(source.read("META-INF/container.xml"))
        rootfiles = container.findall("container:rootfiles/container:rootfile", namespaces)
        if len(rootfiles) != 1:
            raise ValueError("Expected one EPUB package document")
        package_path = rootfiles[0].get("full-path")
        if not package_path:
            raise ValueError("Missing EPUB package path")
        package = parse_xml(source.read(package_path))
        manifest = package.find("opf:manifest", namespaces)
        metadata = package.find("opf:metadata", namespaces)
        if manifest is None or metadata is None:
            raise ValueError("Missing EPUB manifest or metadata")
        items = list(manifest)
        paths = {
            item.get("id"): archive_path(package_path, item.get("href", ""))
            for item in items
        }
        cover_meta = metadata.findall("opf:meta[@name='cover']", namespaces)
        image_ids = {meta.get("content") for meta in cover_meta}
        image_ids.update(
            item.get("id") for item in items
            if "cover-image" in item.get("properties", "").split()
        )
        cover_refs = package.findall("opf:guide/opf:reference[@type='cover']", namespaces)
        page_paths = {
            archive_path(package_path, ref.get("href", "")) for ref in cover_refs
        }
        documents = {}
        for item in items:
            if item.get("media-type") != "application/xhtml+xml":
                continue
            path = paths[item.get("id")]
            document = parse_xml(source.read(path))
            documents[path] = document
            page_paths.update(
                archive_path(path, href) for href in document.xpath(
                    "//xhtml:a[contains(concat(' ', normalize-space(@epub:type), ' '), ' cover ')]/@href",
                    namespaces={**namespaces, "epub": "http://www.idpf.org/2007/ops"},
                )
            )
        for path, document in documents.items():
            if document.xpath(
                "//xhtml:body[@epub:type='cover'] | //xhtml:section[@epub:type='cover']",
                namespaces={**namespaces, "epub": "http://www.idpf.org/2007/ops"},
            ):
                page_paths.add(path)
            # EPUB 2 often identifies the cover page only through its image.
            images = document.xpath("//xhtml:img/@src | //svg:image/@xlink:href | //svg:image/@href",
                                    namespaces={**namespaces, "svg": "http://www.w3.org/2000/svg",
                                                "xlink": "http://www.w3.org/1999/xlink"})
            image_paths = {archive_path(path, href) for href in images}
            body = document.find("xhtml:body", namespaces)
            if (body is not None and not "".join(body.itertext()).strip()
                    and image_paths and image_paths <= {paths.get(uid) for uid in image_ids}):
                page_paths.add(path)
            if path in page_paths:
                image_ids.update(
                    entry.get("id") for entry in items
                    if paths[entry.get("id")] in image_paths
                    and entry.get("media-type", "").startswith("image/")
                )
        removed_ids = image_ids | {
            item.get("id") for item in items if paths[item.get("id")] in page_paths
        }
        removed_paths = {paths[uid] for uid in removed_ids if uid in paths}
        for item in items:
            if item.get("id") in removed_ids:
                manifest.remove(item)
        for node in [*cover_meta, *cover_refs]:
            node.getparent().remove(node)
        for itemref in package.findall("opf:spine/opf:itemref", namespaces):
            if itemref.get("idref") in removed_ids:
                itemref.getparent().remove(itemref)

        updates = {}
        for item in items:
            path = paths[item.get("id")]
            if path in removed_paths:
                continue
            is_nav = "nav" in item.get("properties", "").split()
            is_ncx = item.get("media-type") == "application/x-dtbncx+xml"
            if not is_nav and not is_ncx:
                continue
            document = documents[path] if is_nav else parse_xml(source.read(path))
            links = document.xpath("//xhtml:a[@href] | //ncx:content[@src]", namespaces=namespaces)
            changed = False
            for link in links:
                href = link.get("href") if is_nav else link.get("src")
                if archive_path(path, href) in removed_paths:
                    parent = link.getparent()
                    if parent.tag in (
                        f"{{{namespaces['xhtml']}}}li",
                        f"{{{namespaces['ncx']}}}navPoint",
                        f"{{{namespaces['ncx']}}}pageTarget",
                    ):
                        grandparent = parent.getparent()
                        if grandparent is not None:
                            children = (
                                parent.xpath("xhtml:ol/xhtml:li | xhtml:ul/xhtml:li", namespaces=namespaces)
                                if is_nav else parent.findall("ncx:navPoint", namespaces)
                            )
                            position = grandparent.index(parent)
                            for child in children:
                                grandparent.insert(position, child)
                                position += 1
                            grandparent.remove(parent)
                    else:
                        parent.remove(link)
                    changed = True
            if changed:
                updates[path] = xml_bytes(document)

        if cover is not None:
            uid = "replacement-cover"
            while uid in paths:
                uid += "-new"
            filename = uid + cover.suffix.lower()
            image_path = posixpath.join(posixpath.dirname(package_path), filename)
            while image_path in source.namelist():
                filename = "new-" + filename
                image_path = posixpath.join(posixpath.dirname(package_path), filename)
            attributes = {
                "id": uid, "href": filename, "media-type": image_types[cover.suffix.lower()],
            }
            if package.get("version", "").startswith("3"):
                attributes["properties"] = "cover-image"
            etree.SubElement(manifest, f"{{{namespaces['opf']}}}item", attributes)
            etree.SubElement(metadata, f"{{{namespaces['opf']}}}meta",
                             {"name": "cover", "content": uid})
            updates[image_path] = image
            if any(path in documents for path in page_paths):
                page_uid = uid + "-page"
                while page_uid in paths:
                    page_uid += "-new"
                page_name = filename + ".xhtml"
                page_path = posixpath.join(posixpath.dirname(package_path), page_name)
                while page_path in source.namelist():
                    page_name = "new-" + page_name
                    page_path = posixpath.join(posixpath.dirname(package_path), page_name)
                etree.SubElement(manifest, f"{{{namespaces['opf']}}}item", {
                    "id": page_uid, "href": page_name, "media-type": "application/xhtml+xml",
                })
                spine = package.find("opf:spine", namespaces)
                if spine is not None:
                    spine.insert(0, etree.Element(f"{{{namespaces['opf']}}}itemref", {
                        "idref": page_uid, "linear": "no",
                    }))
                guide = package.find("opf:guide", namespaces)
                if guide is None:
                    guide = etree.SubElement(package, f"{{{namespaces['opf']}}}guide")
                etree.SubElement(guide, f"{{{namespaces['opf']}}}reference",
                                 {"type": "cover", "title": "Cover", "href": page_name})
                document = etree.Element(f"{{{namespaces['xhtml']}}}html",
                                         nsmap={None: namespaces["xhtml"]})
                head = etree.SubElement(document, f"{{{namespaces['xhtml']}}}head")
                etree.SubElement(head, f"{{{namespaces['xhtml']}}}title").text = "Cover"
                body = etree.SubElement(document, f"{{{namespaces['xhtml']}}}body")
                etree.SubElement(body, f"{{{namespaces['xhtml']}}}img",
                                 {"src": filename, "alt": "Cover"})
                updates[page_path] = xml_bytes(document)
        updates[package_path] = xml_bytes(package)

        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=output.parent, suffix=".epub", delete=False
            ) as handle:
                temporary = Path(handle.name)
            with ZipFile(temporary, "w") as target:
                target.comment = source.comment
                for entry in source.infolist():
                    if entry.filename not in removed_paths:
                        target.writestr(
                            entry, updates.pop(entry.filename, source.read(entry.filename))
                        )
                for name, content in updates.items():
                    target.writestr(name, content)
            with ZipFile(temporary) as result:
                if result.testzip() is not None:
                    raise ValueError("Updated EPUB failed ZIP validation")
        except BaseException:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"EPUB: {output.resolve()}")
    print("Cover replaced." if cover is not None else "Cover removed.")
    return output


def main():
    parser = argparse.ArgumentParser(
        description="Compose an EPUB from saved HTML, or edit an existing EPUB's cover."
    )
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument(
        "--folder",
        "--input",
        dest="folder",
        type=Path,
        default=Path("downloaded_pages"),
        help="Folder containing page_*.html (default: downloaded_pages)",
    )
    inputs.add_argument("--epub", type=Path, help="Existing EPUB to update in place (or use --output)")
    parser.add_argument("--cover", type=Path,
                        help="Cover image (omit to remove the cover in --epub mode)")
    parser.add_argument("--title", help="Metadata title (default: collected title)")
    parser.add_argument(
        "--output", type=Path, help="Output path (default: <title>.epub, or the input --epub)"
    )
    parser.add_argument(
        "--origin",
        help="Origin domain for settings file (default: www.fanfiction.net)",
    )
    args = parser.parse_args()
    if args.epub is not None and (args.title is not None or args.origin is not None):
        parser.error("--title and --origin apply only when composing from HTML")
    try:
        if args.epub is not None:
            edit_cover(args.epub, args.output, args.cover)
        else:
            compose(args.folder, args.output, args.cover, args.title,
                    args.origin or "www.fanfiction.net")
    except (OSError, ValueError, BadZipFile, etree.XMLSyntaxError, KeyError) as exc:
        parser.exit(1, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
