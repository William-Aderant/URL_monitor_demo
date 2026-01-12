# Test Scenarios for URL Monitor

This directory contains test scenarios for validating the URL Monitor's change detection capabilities.

## Test Forms

The test site includes three forms with distinct form numbers:

| Form | Filename | Description |
|------|----------|-------------|
| CIV-001 | civ-001.pdf | Motion to Dismiss - Smith v. Jones |
| CIV-002 | civ-002.pdf | Petition for Custody - Davis v. Davis |
| CIV-003 | civ-003.pdf | Petition for Appeal - Garcia v. State |

## Quick Start

```bash
# 1. Start the test server (in a separate terminal)
python test_server.py

# 2. Import test URLs (once)
./venv/bin/python import_urls.py

# 3. Set baseline state
python test_site/simulate_update.py revert
./venv/bin/python cli.py run

# 4. Choose and run a scenario
python test_site/simulate_update.py <scenario>
./venv/bin/python cli.py run

# 5. View results
open http://localhost:8000
```

## Available Scenarios

### `revert` - Reset to Baseline
Reverts all forms to their original state. Use this before testing a new scenario.

```bash
python test_site/simulate_update.py revert
```

### `format_only` - Formatting-Only Change (CIV-001) [TEST-02]
Regenerates CIV-001 with identical text content but different binary hash.
This tests that the system correctly identifies binary-only changes as "no action needed".

```bash
python test_site/simulate_update.py format_only
```

**Expected detection:**
- Change Type: `unchanged` (not a meaningful change)
- PDF hash changed: Yes
- Text hash changed: No
- Action: None required

This maps to **TEST-02** from the PoC Solution Design Document:
> Detect formatting-only change with no semantic impact - AI recommends no action

### `title` - Title Change Only (CIV-001)
Changes only the title of CIV-001 from "Motion to Dismiss" to "AMENDED Motion to Dismiss".
The form number and content remain the same.

```bash
python test_site/simulate_update.py title
```

**Expected detection:**
- Change Type: `text_changed`
- Match Type: `same_form`
- Similarity: ~95%

### `content` - Content Update (CIV-002)
Adds supplemental text to CIV-002. The form number and title remain the same,
but the body content is extended.

```bash
python test_site/simulate_update.py content
```

**Expected detection:**
- Change Type: `text_changed`
- Match Type: `same_form`
- Similarity: ~75-85%
- Changed sections shown in diff

### `relocate` - URL Relocation (CIV-003)
Moves CIV-003 to a new filename (`civ-003-final.pdf`). The original URL returns 404.

```bash
python test_site/simulate_update.py relocate
```

**What happens:**
1. Original URL `/pdfs/civ-003.pdf` returns 404
2. Monitor detects download failure
3. Crawler visits parent page `/pdfs/` 
4. Finds `civ-003-final.pdf` by matching form number `CIV-003`
5. Updates monitored URL to new location
6. Downloads and processes the relocated form

**Expected detection:**
- Form found at new location
- `relocated_from_url` field populated
- Monitor URL auto-updated

### `new` - New Form Added (CIV-004)
Adds an entirely new form (CIV-004 - Motion for Summary Judgment).
This form needs to be manually added to monitoring.

```bash
python test_site/simulate_update.py new
```

**Note:** After running this, add the new URL to monitoring:
```
http://localhost:5001/pdfs/civ-004.pdf
```

## How Relocation Detection Works

When a monitored PDF URL returns 404:

1. **Fetch Previous Version Info**: Gets the form number (e.g., "CIV-003") from the last known version

2. **Determine Parent URL**: Uses `parent_page_url` if configured, otherwise extracts from the PDF URL:
   - `http://localhost:5001/pdfs/civ-003.pdf` → `http://localhost:5001/pdfs/`

3. **Crawl Parent Page**: Fetches the parent page HTML and extracts all PDF links

4. **Match by Form Number**: Searches for a PDF link containing the same form number:
   - Looking for "CIV-003" matches `civ-003-final.pdf` ✓

5. **Update Monitored URL**: If found, updates the monitored URL to the new location

## Form Number Matching

The crawler uses regex patterns to extract form numbers from filenames:

| Filename | Extracted Form # |
|----------|-----------------|
| civ-001.pdf | CIV-001 |
| civ-003-final.pdf | CIV-003 |
| CIV775.pdf | CIV-775 |
| form-mc-025.pdf | MC-025 |

## Directory Listing

The `/pdfs/` endpoint serves an HTML page listing all available PDFs:

```
http://localhost:5001/pdfs/
```

This page is what the crawler uses to find PDF links when looking for relocated forms.

## Testing Workflow

1. **Set baseline**: Revert and run once to establish initial versions
2. **Apply scenario**: Run one of the simulate_update.py scenarios
3. **Detect changes**: Run cli.py to detect and record changes
4. **View results**: Check the web UI at http://localhost:8000
5. **Reset**: Revert again before testing another scenario
