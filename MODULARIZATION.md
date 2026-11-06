# HTML to EPUB Modularization Guide

## Overview

The `compose_epub.py` script has been modularized to support content extraction from multiple HTML sources. Previously, it was hardcoded to extract content from `www.fanfiction.net`. Now, you can specify an `--origin` parameter and create corresponding `.settings` files to define how content should be extracted from different websites.

## How It Works

### Settings Files

Each origin (website) needs a settings file in the format: `{origin}.settings`

For example:
- `www.fanfiction.net.settings` - for fanfiction.net
- `example.com.settings` - for example.com
- `archiveofourown.org.settings` - for Archive of Our Own

The settings file uses INI format (similar to Windows `.ini` files) with the following sections:

### Settings File Structure

#### `[metadata]` Section

Defines CSS selectors for extracting book metadata:

- `title_selector` - CSS selector for the story title
- `author_selector` - CSS selector for the author name
- `chapter_select_selector` - CSS selector for the chapter dropdown menu
- `summary_selector` - CSS selector for the story summary/description

Example:
```ini
[metadata]
title_selector = #profile_top b
author_selector = #profile_top a[href^="/u/"]
chapter_select_selector = select#chap_select
summary_selector = #profile_top > div.xcontrast_txt
```

#### `[content]` Section

Defines how to extract and clean the story content:

- `content_selector` - CSS selector for the main story content area
- `unwanted_selectors` - Comma-separated CSS selectors for elements to remove (e.g., scripts, ads)
- `remove_attributes` - Comma-separated HTML attribute names to remove from content elements (note: attributes starting with "on" are always removed)

Example:
```ini
[content]
content_selector = #storytext
unwanted_selectors = script, style, iframe, form, button, select
remove_attributes = class, id
```

#### `[chapter]` Section

Defines chapter extraction patterns:

- `selected_option_selector` - CSS selector to find the currently selected chapter
- `title_cleanup_pattern` - Regex pattern to clean chapter titles (e.g., remove leading numbers)
- `default_chapter_title` - Format string for chapter titles when no name is found (use `{number}` as placeholder)

Example:
```ini
[chapter]
selected_option_selector = select#chap_select option[selected]
title_cleanup_pattern = ^\d+\.\s*
default_chapter_title = Chapter {number}
```

#### `[validation]` Section

Defines validation behavior:

- `page_filename_pattern` - Glob pattern for page filenames (default: `page_*.html`)
- `validate_chapter_list` - Whether to check that downloaded chapters match the chapter list (true/false)
- `verify_content` - Whether to verify chapter content hasn't changed after EPUB creation (true/false)

Example:
```ini
[validation]
page_filename_pattern = page_*.html
validate_chapter_list = true
verify_content = true
```

## Usage

### Basic Usage (Fanfiction.net - Default)

```bash
python compose_epub.py --folder "downloaded_pages" --output "My Story.epub"
```

This automatically uses `www.fanfiction.net.settings`.

### Using a Different Origin

```bash
python compose_epub.py --folder "downloaded_pages" --origin "archiveofourown.org" --output "My Story.epub"
```

This will load settings from `archiveofourown.org.settings`.

### Full Example with Cover and Title

```bash
python compose_epub.py \
  --folder "downloaded_pages" \
  --origin "www.fanfiction.net" \
  --cover "cover.jpg" \
  --title "My Custom Title" \
  --output "output.epub"
```

## Creating Settings for a New Origin

1. Inspect the HTML structure of the website using your browser's developer tools
2. Find the CSS selectors for:
   - Story title
   - Author name
   - Chapter list/selector (if applicable)
   - Story summary
   - Main content area
   - Any unwanted elements to remove
3. Create a `.settings` file with these selectors
4. Test with a small sample first:
   ```bash
   python compose_epub.py --folder "sample_pages" --origin "new-site.com" --output "test.epub"
   ```
5. Adjust selectors if needed and retest

## Implementation Details

### `SiteSettings` Class

The `SiteSettings` class handles loading and parsing `.settings` files:

```python
from compose_epub import SiteSettings

settings = SiteSettings("www.fanfiction.net.settings")
print(settings.content_selector)  # Output: #storytext
print(settings.title_selector)    # Output: #profile_top b
```

### Modified `compose_book()` Function

The main logic now takes a `settings` parameter:

```python
compose_book(input_dir, output, cover, title, progress, settings)
```

All hardcoded CSS selectors have been replaced with `settings` properties.

### Error Handling

If a `.settings` file is not found, the script will exit with a helpful error message:

```
Error: Settings file not found: www.fanfiction.net.settings
Please create a www.fanfiction.net.settings file with extraction patterns.
```

## Extending the System

To add new configuration options:

1. Add a new section to `.settings` files (e.g., `[custom]`)
2. Add corresponding properties to the `SiteSettings` class:
   ```python
   @property
   def my_custom_option(self):
       return self.get("custom", "my_option", fallback="default_value")
   ```
3. Use the property in the compose logic

## Backward Compatibility

Existing usage works unchanged:

```bash
# These all still work without the --origin flag
python compose_epub.py --folder "Make A Wish"
python compose_epub.py --folder "downloaded_pages" --output "book.epub"
```

The `--origin` parameter defaults to `www.fanfiction.net`, maintaining backward compatibility.

## File Checklist for a New Origin

When adding support for a new website, ensure you have:

- [ ] `{origin}.settings` file with all required sections and options
- [ ] CSS selectors validated against sample HTML pages
- [ ] Test EPUB created successfully
- [ ] Content extracted correctly
- [ ] Metadata (title, author) populated correctly
- [ ] Chapter titles formatted properly
