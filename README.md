# HTML to EPUB Converter

Convert HTML pages from fanfiction websites into properly formatted EPUB ebooks.

## Features

- **Multi-site Support**: Works with any fanfiction website via configurable `.settings` files
- **Automatic Metadata**: Extracts title, author, and summary from HTML
- **Chapter Management**: Validates and organizes chapters correctly
- **EPUB Validation**: Ensures the output EPUB is valid and uncorrupted
- **Cover Support**: Optional cover image inclusion
- **Progress Tracking**: Visual progress bars for all operations

## Installation

1. Ensure you have Python 3.8+ installed
2. Install required dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Usage

### Download and Compose in One Command

```bash
python html_to_epub.py --url "https://www.fanfiction.net/s/123456/NUMBER/My-Story" --from 1 --to 50 --title "My Story"
```

Use the literal `NUMBER` placeholder for numbered pages, just as with
`fetch_pages.py`. Without `NUMBER`, the URL is downloaded unchanged as a single
page (`page_0001.html`), and `--from` and `--to` are ignored. URLs without a scheme
default to HTTPS. `--url` and `--title` are required; for numbered URLs, `--from`
and `--to` default to 1 and 50.

the wrapper's exit code.
All wrapper output is grouped under `output/<title>/`: downloaded chapter pages
are kept in `pages/`, generated cover art is `cover.png`, and the EPUB is named
`<title>.epub`. The title is sanitized for filesystem use. Existing downloaded
pages are skipped, allowing interrupted downloads to resume. A failed download
prevents composition; either stage's failure is returned as the wrapper's exit
code.

AI generation is attempted by default. Ollama is asked for a spoiler-light book
description of at most 200 words and a visual cover prompt based on the first
chapter. Both the exact Ollama messages and the resulting ComfyUI prompt are
saved in `output/<title>/ai_prompts.txt`. `OLLAMA_BASE_URL` and `OLLAMA_MODEL` in
`open-webui.env` configure the description and image-prompt request to Ollama;
`COMFYUI_BASE_URL` and `COMFYUI_CHECKPOINT` configure cover generation through
ComfyUI's API. The example file lists the expected values. Ollama or ComfyUI
errors are non-fatal: the EPUB is still composed using the site's summary and
without an AI cover. Use `--no-ai` to skip both requests. Ollama is asked to
unload the model after its response to free VRAM before ComfyUI runs.

To use a supplied cover with a title folder, the script first updates an EPUB
already present in `output/<title>/`. If none exists, it recomposes from pages
saved in `output/<title>/pages` without downloading or running AI:

```bash
python html_to_epub.py --title "My Story" --image "cover.png" --origin "www.fanfiction.net"
```

To replace the cover of an existing EPUB in place:

```bash
python html_to_epub.py --epub "output/My Story/My Story.epub" --image "cover.png"
```

The `--image` option can also be added to the normal URL command to use that
image instead of generating a cover.

Run from a directory containing the matching `<hostname>.settings` file, such as
`www.fanfiction.net.settings`. The wrapper uses the same Python interpreter for
both scripts and preserves their interactive prompts and progress displays.

### Basic Usage (Fanfiction.net)

```bash
python compose_epub.py --folder "downloaded_pages"
```

Creates `output.epub` with automatic title from metadata.

### With Custom Output Path

```bash
python compose_epub.py --folder "downloaded_pages" --output "My Story.epub"
```

### With Cover Image

```bash
python compose_epub.py --folder "downloaded_pages" --cover "cover.jpg" --output "book.epub"
```

### With Custom Website Settings

```bash
python compose_epub.py --folder "pages" --origin "archiveofourown.org" --output "story.epub"
```

### LessWrong Articles

The included `www.lesswrong.com.settings` extracts the post title, author, and
article body, excluding comments and site navigation. Each saved post becomes
one EPUB chapter; linked posts or sequences are not downloaded automatically.

For a single article, pass its URL directly; no range arguments are needed:

```bash
python html_to_epub.py --url "https://www.lesswrong.com/posts/3375SZNGA5dKHQMie/i-did-this-without-your-knowledge" --title "I Did This Without Your Knowledge"
```

For HTML already saved as `page_0001.html` in a folder:

```bash
python compose_epub.py --folder "LessWrong" --origin "www.lesswrong.com" --title "I Did This Without Your Knowledge"
```

### Remove or Replace an Existing EPUB's Cover

```bash
python compose_epub.py --epub "My Story.epub"
python compose_epub.py --epub "My Story.epub" --cover "new-cover.jpg"
```

With `--epub`, omitting `--cover` removes the existing cover image, cover page,
and associated references. Providing `--cover` replaces the cover image.
Chapter content and unrelated archive files are preserved; no HTML pages or
website settings are needed. The original EPUB is updated atomically by default.
Use `--output "Updated Story.epub"` to keep the original and write a separate copy.
Supported cover formats are JPEG, PNG, GIF, SVG, and WebP.
`--epub` cannot be combined with `--folder`/`--input`, `--title`, or `--origin`.

## Settings Files

The system uses `.settings` files to define how to extract content from different websites.

### Pre-configured

- **www.fanfiction.net.settings** - For fanfiction.net
- **www.lesswrong.com.settings** - For LessWrong posts (without comments)

### Examples Included

- **example.com.settings** - Template for creating new settings

### Creating Custom Settings

1. Create a file: `{website-name}.settings`
2. Copy the template from `example.com.settings`
3. Inspect your target website's HTML
4. Update CSS selectors to match the actual HTML structure
5. Test with a small sample

## Command-Line Options

```
usage: compose_epub.py [-h] [--folder FOLDER | --epub EPUB] [--cover COVER] [--title TITLE]
                       [--output OUTPUT] [--origin ORIGIN]

Compose an EPUB from saved chapter HTML.

options:
  -h, --help              Show help message
  --folder, --input       Input folder with page_*.html files (default: downloaded_pages)
  --epub                  Edit an existing EPUB's cover instead of composing from HTML
  --cover                 Cover image path
  --title                 Override title in metadata
  --output                Output EPUB path (default: <title>.epub, or input --epub)
  --origin                Origin domain for settings (default: www.fanfiction.net)
```

## File Organization

```
html_to_epub/
├── compose_epub.py                      # Main script
├── fetch_pages.py                       # HTML page downloader
├── www.fanfiction.net.settings          # Fanfiction.net configuration
├── example.com.settings                 # Template configuration
├── README.md                            # This file
├── QUICK_START.md                       # Quick start guide
├── MODULARIZATION.md                    # Technical documentation
├── IMPLEMENTATION_SUMMARY.md            # What changed
├── CHANGES.md                           # Detailed code changes
├── requirements.txt                     # Python dependencies
└── output/
  └── My Story/
    ├── My Story.epub
    ├── ai_prompts.txt              # Ollama request and generated cover prompt
    ├── cover.png                    # Present when ComfyUI succeeds
    └── pages/
      ├── page_0001.html
      └── ...
```

## Workflow

### Step 1: Download Pages

Use `fetch_pages.py` to download HTML pages:

```bash
python fetch_pages.py --url "https://www.fanfiction.net/s/123456/NUMBER/My-Story" --from 1 --to 20 --folder "My Story"
```

Use an absolute HTTP or HTTPS URL. Without `NUMBER`, only that page is downloaded
as `page_0001.html`, and `--from` and `--to` are ignored. For numbered pages, use
exactly one literal `NUMBER` placeholder in the path or query string. It is
replaced with each page number (1 through 50 by default), preserving the rest of
the URL. For example,
`https://page.com/something/NUMBER/whatev` downloads
`https://page.com/something/1/whatev`, then
`https://page.com/something/2/whatev`, and so on.

The output folder defaults to `downloaded_pages`; existing pages are skipped.
The URL path before `NUMBER` (without a query string or fragment) is used as the
Referer. The legacy `--base` and `--trailing` options still work together, but
cannot be combined with `--url`.

### Step 2: Create EPUB

Convert downloaded pages to EPUB:

```bash
python compose_epub.py --folder "My Story" --output "My Story.epub"
```

### Optional: Add Cover Image

1. Prepare a cover image (JPG or PNG)
2. Include when creating EPUB:

```bash
python compose_epub.py --folder "My Story" --cover "cover.jpg"
```

## Output

The script creates an EPUB file with:

- ✓ Valid EPUB structure
- ✓ Proper metadata (title, author, description)
- ✓ All chapters in reading order
- ✓ Cleaned HTML content (scripts, ads, tracking removed)
- ✓ Consistent formatting
- ✓ Cover image (if provided)
- ✓ Working navigation and table of contents

## Supported Websites

### Currently Configured

- **www.fanfiction.net** - FanFiction.Net (FF.net)

### Easy to Add

Create a `.settings` file for any fanfiction website:
- Archive of Our Own (AO3)
- WattPad
- FictionPress
- Quotev
- Wattpad
- Or any other website with consistent HTML structure

See [QUICK_START.md](QUICK_START.md) for detailed instructions.

## Troubleshooting

### "Settings file not found"

Create the `.settings` file for your origin website. See `example.com.settings` for a template.

### "Missing story title" or "Missing or empty story content"

Check that the CSS selectors in the `.settings` file match your target website's HTML structure. Use browser DevTools (F12) to inspect and find the correct selectors.

### Empty or incorrect content in EPUB

1. Verify CSS selectors are correct
2. Check that content elements are properly identified
3. Update the `content_selector` in your `.settings` file

### EPUB validation errors

These are usually one-time issues. The script validates EPUB structure automatically. If errors persist:

1. Check for malformed HTML in source pages
2. Ensure the `unwanted_selectors` properly removes problematic elements
3. Verify the output EPUB with an EPUB validator

## Documentation

- **[QUICK_START.md](QUICK_START.md)** - Get started in 5 minutes
- **[MODULARIZATION.md](MODULARIZATION.md)** - Full technical guide
- **[IMPLEMENTATION_SUMMARY.md](IMPLEMENTATION_SUMMARY.md)** - Architecture overview
- **[CHANGES.md](CHANGES.md)** - Detailed code changes

## Technical Details

### Extraction Process

1. **Load HTML**: Read and parse HTML files with BeautifulSoup
2. **Extract Metadata**: Use CSS selectors to find title, author, summary
3. **Extract Content**: Find main story content using configured selector
4. **Clean Content**: Remove scripts, ads, tracking, unnecessary attributes
5. **Format Chapters**: Organize content into chapters with titles
6. **Build EPUB**: Create proper EPUB structure with ebooklib
7. **Validate**: Verify EPUB structure and content integrity

### Settings System

The `.settings` files use INI format with four sections:

- **[metadata]** - Extraction patterns for book information
- **[content]** - Patterns for story content and cleaning
- **[chapter]** - Chapter-specific patterns and formatting
- **[validation]** - Validation options and behavior

### Dependencies

- **BeautifulSoup4** - HTML parsing
- **ebooklib** - EPUB creation
- **lxml** - XML processing and validation
- **rich** - Progress bars and console output

## Performance

Typical performance for a 50-chapter story:
- Parsing: 1-2 seconds
- Composing: 2-3 seconds  
- Verification: 1-2 seconds
- **Total: ~5 seconds**

Output file size: ~450 KB (varies with content)

## License

This project is provided as-is for personal use.

## Disclaimer

This tool is designed to convert personal fanfiction collections into EPUB format for personal reading. Ensure you have the rights to download and convert content you process with this tool.

## Contributing

To add support for a new website:

1. Create a `.settings` file with the appropriate CSS selectors
2. Test with a small sample
3. Document the selectors and any special handling needed

## Support

For issues or questions:

1. Check the [QUICK_START.md](QUICK_START.md) guide
2. Review [MODULARIZATION.md](MODULARIZATION.md) for technical details
3. Inspect the example `.settings` files
4. Verify CSS selectors in the target website's HTML
