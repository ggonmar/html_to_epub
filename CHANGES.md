# Changes Made to compose_epub.py

## Overview
The script has been refactored from a hardcoded fanfiction.net-specific implementation to a modular, settings-based system that can extract content from any website.

## New Components

### 1. SiteSettings Class
**Purpose**: Load and manage extraction settings from `.settings` files

**Key Methods**:
- `__init__(settings_path)` - Load settings from INI file
- `get(section, key, fallback)` - Get string configuration value
- `get_bool(section, key, fallback)` - Get boolean configuration value

**Properties** (all read from configuration):
- `title_selector`
- `author_selector`
- `chapter_select_selector`
- `summary_selector`
- `content_selector`
- `unwanted_selectors` (returns list)
- `remove_attributes` (returns list)
- `selected_option_selector`
- `title_cleanup_pattern`
- `default_chapter_title`
- `page_filename_pattern`
- `validate_chapter_list`
- `verify_content`

## Modified Functions

### compose()
**Changes**:
- Added `origin` parameter (defaults to "www.fanfiction.net")
- Loads `.settings` file for the specified origin
- Passes `settings` object to `compose_book()`

**Before**:
```python
def compose(input_dir, output=None, cover=None, title=None):
```

**After**:
```python
def compose(input_dir, output=None, cover=None, title=None, origin=None):
    # Load settings based on origin
    settings = SiteSettings(f"{origin or 'www.fanfiction.net'}.settings")
    # Pass settings to compose_book()
```

### compose_book()
**Changes**:
- Added `settings` parameter
- Replaced all hardcoded CSS selectors with `settings` properties
- Made validation behavior configurable via settings

**Specific replacements**:
- `"#profile_top b"` → `settings.title_selector`
- `'#profile_top a[href^="/u/"]'` → `settings.author_selector`
- `"select#chap_select"` → `settings.chapter_select_selector`
- `"#profile_top > div.xcontrast_txt"` → `settings.summary_selector`
- `"#storytext"` → `settings.content_selector`
- Hardcoded unwanted selectors → `settings.unwanted_selectors` list
- Hardcoded attributes removal → `settings.remove_attributes` list
- `"select#chap_select option[selected]"` → `settings.selected_option_selector`

**Validation changes**:
- Chapter list validation now gated by `settings.validate_chapter_list`
- Content verification now gated by `settings.verify_content`

### main()
**Changes**:
- Added `--origin` argument to argument parser
- Default origin: "www.fanfiction.net"
- Passes `origin` to `compose()` function

**New Argument**:
```python
parser.add_argument(
    "--origin",
    default="www.fanfiction.net",
    help="Origin domain for settings file (default: www.fanfiction.net)",
)
```

## Removed Hardcoding

All the following were hardcoded; now they come from `.settings` files:

### Metadata Extraction
- Story title selector
- Author selector
- Chapter dropdown selector
- Summary selector

### Content Cleaning
- Main content area selector
- Unwanted elements (scripts, styles, iframes, forms, buttons, selects)
- Attributes to remove (class, id)

### Chapter Processing
- Selected chapter option selector
- Chapter title cleanup pattern
- Default chapter title format

### Validation Options
- Page filename pattern
- Chapter list validation flag
- Content verification flag

## Settings File Introduction

New `.settings` file format uses INI structure with sections:

```ini
[metadata]
[content]
[chapter]
[validation]
```

Each section contains configurable values that were previously hardcoded.

## Backward Compatibility

✓ **Fully backward compatible**
- Existing command-line usage works unchanged
- Default `--origin` is "www.fanfiction.net"
- All existing scripts and commands continue to work

```bash
# These all still work exactly the same
python compose_epub.py --folder "Make A Wish"
python compose_epub.py --folder "downloaded_pages" --output "book.epub"
```

## Error Handling Improvements

New error message if settings file not found:
```
Error: Settings file not found: www.fanfiction.net.settings
Please create a www.fanfiction.net.settings file with extraction patterns.
```

## Code Quality Improvements

1. **Separation of Concerns**: Extraction patterns separated from logic
2. **Configurable Validation**: Can disable expensive validations for untrusted sources
3. **Extensibility**: Easy to add new extraction patterns without code changes
4. **Maintainability**: All patterns in one place per origin
5. **Documentation**: Each setting is well-commented in the `.settings` file

## File Size

- Original: ~8.1 KB
- New: ~12.1 KB (+4 KB for SiteSettings class and additional logic)
- Well worth the size increase for the flexibility gained

## Testing

Verified with existing "Make A Wish" dataset:
- ✓ Settings load correctly
- ✓ All 50 chapters extracted
- ✓ EPUB created successfully (455 KB)
- ✓ Content verified
- ✓ XML validation passes
