# Quick Start Guide - Settings-Based EPUB Composition

## Download and Compose in One Command

```bash
python html_to_epub.py --url "https://www.fanfiction.net/s/123456/NUMBER/My-Story" --from 1 --to 50 --title "My Story"
```

- Replace the changing page number in the URL with the literal `NUMBER`.
- `--url` and `--title` are required. `--from` and `--to` default to 1 and 50.
- URLs without a scheme default to HTTPS.
- Pages are saved in a folder named exactly by `--title`, here `My Story`.
  The title must be a folder name, not a path.
- After downloading finishes successfully, the wrapper runs `compose_epub.py`
  using that folder, the supplied title, and the URL hostname as `--origin`.
- The resulting EPUB has no cover and uses the title-based default filename,
  here `My Story.epub`.
- Existing downloaded pages are skipped when rerunning the command.
  If downloading fails, composition does not start.

Run from the project directory containing the matching hostname settings file,
such as `www.fanfiction.net.settings`. For another website, create its settings
file first as described below.

## Basic Usage (Fanfiction.net)

To compose an EPUB from pages you have already downloaded:

```bash
python compose_epub.py --folder "Make A Wish" --output "My Story.epub"
```

The script automatically loads `www.fanfiction.net.settings` and extracts content using those patterns.

## Using a Different Website

```bash
python compose_epub.py --folder "pages" --origin "example.com" --output "story.epub"
```

This loads settings from `example.com.settings`.

## Creating Settings for a New Website

### Step 1: Inspect the HTML
Open one of the downloaded HTML files in your browser and use Developer Tools (F12) to find CSS selectors for:
- Story title
- Author name
- Main content area
- Chapter list (if exists)
- Story summary

### Step 2: Create the Settings File
Copy the template below and save as `{website}.settings`:

```ini
[metadata]
title_selector = 
author_selector = 
chapter_select_selector = 
summary_selector = 

[content]
content_selector = 
unwanted_selectors = script, style
remove_attributes = class, id

[chapter]
selected_option_selector = 
title_cleanup_pattern = ^\d+\.\s*
default_chapter_title = Chapter {number}

[validation]
page_filename_pattern = page_*.html
validate_chapter_list = true
verify_content = true
```

### Step 3: Fill in the CSS Selectors
Find selectors using Developer Tools and fill them in. For example:

- For fanfiction.net: `#profile_top b` contains the title
- For a different site with class-based design: `.story-title`
- For elements with data attributes: `[data-story-content]`

### Step 4: Test
```bash
python compose_epub.py --folder "test_pages" --origin "mywebsite.com"
```

### Step 5: Adjust if Needed
If extraction fails:
1. Check the error message
2. Verify CSS selectors in browser DevTools
3. Update the `.settings` file
4. Retest

## Common CSS Selectors Reference

| Element | Selector Type | Example |
|---------|---------------|---------|
| By ID | `#id-name` | `#profile_top` |
| By Class | `.class-name` | `.story-title` |
| By Tag | `tag-name` | `article` |
| By Attribute | `[attr="value"]` | `[href^="/u/"]` |
| Nested | `parent child` | `#content p` |
| Multiple | `selector1, selector2` | `script, style` |

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "Settings file not found" | Create `{origin}.settings` in the current working directory; the wrapper uses the URL hostname as the origin |
| URL placeholder error | Replace the changing page number with exactly one literal `NUMBER` |
| Download failed; no EPUB created | Resolve the downloader error, then rerun the same command; existing pages are skipped |
| "Missing story title" | Check `title_selector` matches the actual HTML element |
| "Missing or empty story content" | Verify `content_selector` is correct |
| "Wrong chapter saved" | Check `selected_option_selector` |
| Empty EPUB | Verify all selectors are finding elements in your HTML |

## File Locations

Settings files are loaded from the current working directory. Running from the
project directory lets you use the included settings files:

```
html_to_epub/
├── html_to_epub.py
├── fetch_pages.py
├── compose_epub.py
├── www.fanfiction.net.settings
├── example.com.settings
├── MODULARIZATION.md
└── downloaded_pages/
    └── page_*.html
```

## Examples

### Download and Compose with Default Page Range

```bash
python html_to_epub.py --url "https://www.fanfiction.net/s/123456/NUMBER/My-Story" --title "My Story"
```

Downloads pages 1 through 50, then creates `My Story.epub` without a cover.

### Example 1: Fanfiction.net (Pre-configured)
```bash
python compose_epub.py --folder "Make A Wish" --output "output.epub"
```

### Example 2: Custom Website
```bash
python compose_epub.py \
  --folder "pages" \
  --origin "mysite.com" \
  --title "Custom Title" \
  --cover "cover.jpg" \
  --output "book.epub"
```

### Example 3: All Options
```bash
python compose_epub.py \
  --folder "downloaded_pages" \
  --origin "fanfiction.net" \
  --title "The Full Story" \
  --cover "cover_image.png" \
  --output "My Book.epub"
```

## Get Help

```bash
python html_to_epub.py --help
python fetch_pages.py --help
python compose_epub.py --help
```

## See Also

- [MODULARIZATION.md](MODULARIZATION.md) - Full technical documentation
- [IMPLEMENTATION_SUMMARY.md](IMPLEMENTATION_SUMMARY.md) - What changed in the refactoring
- [www.fanfiction.net.settings](www.fanfiction.net.settings) - Complete example configuration
