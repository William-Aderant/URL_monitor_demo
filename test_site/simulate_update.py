#!/usr/bin/env python3
"""
Test Scenario Simulator for URL Monitor

This script helps simulate different types of form updates for testing
the enhanced change detection system. Now supports multiple forms
with distinct form numbers for proper differentiation.

Usage:
    python test_site/simulate_update.py [scenario]
    
Scenarios:
    revert    - Reverts to clean baseline state (all 3 forms)
    title     - Changes only the title text (CIV-001)
    content   - Updates form content (CIV-002 adds supplemental text)
    relocate  - Moves CIV-003 to new filename (original URL 404s)
    new       - Adds an entirely new form (CIV-004)
    
Test Forms:
    civ-001.pdf - Motion to Dismiss (Smith v. Jones)
    civ-002.pdf - Petition for Custody (Davis v. Davis)
    civ-003.pdf - Petition for Appeal (Garcia v. State)
"""

import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

# Get paths relative to this script
SCRIPT_DIR = Path(__file__).parent
PDF_DIR = SCRIPT_DIR / "pdfs"
CONTENT_FILE = SCRIPT_DIR / "content.html"

# Import the PDF generator
sys.path.insert(0, str(SCRIPT_DIR))
from generate_test_pdf import TestPDFGenerator, FORM_DEFINITIONS

# Initialize the PDF generator
pdf_generator = TestPDFGenerator(PDF_DIR)


def get_html_content(date: str, filings: str) -> str:
    """Generate HTML content with proper escaping."""
    return f"""<!DOCTYPE html>
<html>
<head>
    <title>Mock Court Filing System</title>
    <style>
        body {{ font-family: Georgia, serif; max-width: 800px; margin: 50px auto; padding: 20px; }}
        h1 {{ color: #1a365d; border-bottom: 2px solid #1a365d; padding-bottom: 10px; }}
        .filing {{ background: #f7fafc; padding: 15px; margin: 10px 0; border-left: 4px solid #3182ce; }}
        .filing a {{ color: #2b6cb0; text-decoration: none; font-weight: bold; }}
        .filing a:hover {{ text-decoration: underline; }}
        .date {{ color: #718096; font-size: 0.9em; }}
        .case-number {{ font-family: monospace; background: #edf2f7; padding: 2px 6px; }}
        .new {{ background: #c6f6d5; border-left-color: #38a169; }}
        .changed {{ background: #fefcbf; border-left-color: #d69e2e; }}
        .relocated {{ background: #e9d8fd; border-left-color: #805ad5; }}
    </style>
</head>
<body>
    <h1>📋 District Court - Recent Filings</h1>
    
    <p><strong>Last Updated:</strong> {date}</p>
    
    <h2>Recent Case Filings</h2>
    
    {filings}
    
    <h2>Court Announcements</h2>
    <div class="filing">
        <p>The court will be closed on January 20, 2026 for the Martin Luther King Jr. holiday.</p>
    </div>
</body>
</html>
"""

FILING_TEMPLATE = """    <div class="filing {css_class}">
        <a href="/pdfs/{filename}">{title}</a>
        <p>Case No: <span class="case-number">{case_number}</span></p>
        <p class="date">Filed: {filed_date}</p>
    </div>
"""


def generate_baseline_filings() -> str:
    """Generate HTML for all baseline forms."""
    filings = []
    
    # CIV-001
    filings.append(FILING_TEMPLATE.format(
        css_class="",
        filename="civ-001.pdf",
        title="Motion to Dismiss - Smith v. Jones",
        case_number="2026-CV-001",
        filed_date="January 10, 2026"
    ))
    
    # CIV-002
    filings.append(FILING_TEMPLATE.format(
        css_class="",
        filename="civ-002.pdf",
        title="Petition for Custody - Davis v. Davis",
        case_number="2026-CV-002",
        filed_date="January 11, 2026"
    ))
    
    # CIV-003
    filings.append(FILING_TEMPLATE.format(
        css_class="",
        filename="civ-003.pdf",
        title="Petition for Appeal - Garcia v. State",
        case_number="2026-CV-003",
        filed_date="January 12, 2026"
    ))
    
    return "\n".join(filings)


def scenario_revert():
    """Revert to original baseline state with all 3 forms."""
    print("\n⏮️  Scenario: Revert to Baseline")
    print("=" * 50)
    
    # Clean up any extra files first
    for f in PDF_DIR.glob("*.pdf"):
        f.unlink()
        print(f"  ✗ Removed: {f.name}")
    
    # Generate all baseline forms
    pdf_generator.create_court_form(
        filename="civ-001.pdf",
        **FORM_DEFINITIONS["civ-001"]
    )
    
    pdf_generator.create_court_form(
        filename="civ-002.pdf",
        **FORM_DEFINITIONS["civ-002"]
    )
    
    pdf_generator.create_court_form(
        filename="civ-003.pdf",
        **FORM_DEFINITIONS["civ-003"]
    )
    
    # Restore baseline content.html
    content = get_html_content(
        date="January 12, 2026",
        filings=generate_baseline_filings()
    )
    
    CONTENT_FILE.write_text(content)
    
    print("\n✓ Reverted to baseline state with 3 forms:")
    print("  • civ-001.pdf - Motion to Dismiss")
    print("  • civ-002.pdf - Petition for Custody")
    print("  • civ-003.pdf - Petition for Appeal")
    print("\n✅ Run './venv/bin/python cli.py run' to set baseline")


def scenario_title_change():
    """Simulate a title-only change on CIV-001."""
    print("\n📝 Scenario: Title Change (CIV-001)")
    print("=" * 50)
    
    # Generate PDF with changed title
    form_data = FORM_DEFINITIONS["civ-001"].copy()
    form_data["title"] = "AMENDED Motion to Dismiss"  # Changed title
    
    pdf_generator.create_court_form(
        filename="civ-001.pdf",
        **form_data
    )
    
    # Update HTML content
    filings = []
    
    # CIV-001 - CHANGED
    filings.append(FILING_TEMPLATE.format(
        css_class="changed",
        filename="civ-001.pdf",
        title="AMENDED Motion to Dismiss - Smith v. Jones",
        case_number="2026-CV-001",
        filed_date="January 10, 2026 (Amended)"
    ))
    
    # CIV-002 - unchanged
    filings.append(FILING_TEMPLATE.format(
        css_class="",
        filename="civ-002.pdf",
        title="Petition for Custody - Davis v. Davis",
        case_number="2026-CV-002",
        filed_date="January 11, 2026"
    ))
    
    # CIV-003 - unchanged
    filings.append(FILING_TEMPLATE.format(
        css_class="",
        filename="civ-003.pdf",
        title="Petition for Appeal - Garcia v. State",
        case_number="2026-CV-003",
        filed_date="January 12, 2026"
    ))
    
    content = get_html_content(
        date=datetime.now().strftime("%B %d, %Y"),
        filings="\n".join(filings)
    )
    
    CONTENT_FILE.write_text(content)
    print(f"  ✓ Updated civ-001.pdf with title change: 'AMENDED Motion to Dismiss'")
    print("\n✅ Run './venv/bin/python cli.py run' to detect changes")


def scenario_content_update():
    """Simulate a content update on CIV-002 (adds supplemental text)."""
    print("\n📄 Scenario: Content Update (CIV-002)")
    print("=" * 50)
    
    # Generate PDF with updated content
    form_data = FORM_DEFINITIONS["civ-002"].copy()
    
    pdf_generator.create_court_form(
        filename="civ-002.pdf",
        revision_date=datetime.now().strftime("%m/%d/%Y"),
        extra_text="SUPPLEMENTAL FILING: Following the recent custody evaluation, Petitioner wishes to add that the court-appointed evaluator recommended that the children remain in Petitioner's primary care. The evaluation found that Petitioner's home provides a stable, nurturing environment. Additionally, the children's school records show consistent academic performance while in Petitioner's care.",
        **form_data
    )
    
    # Update HTML content
    filings = []
    
    # CIV-001 - unchanged
    filings.append(FILING_TEMPLATE.format(
        css_class="",
        filename="civ-001.pdf",
        title="Motion to Dismiss - Smith v. Jones",
        case_number="2026-CV-001",
        filed_date="January 10, 2026"
    ))
    
    # CIV-002 - CHANGED
    filings.append(FILING_TEMPLATE.format(
        css_class="changed",
        filename="civ-002.pdf",
        title="Petition for Custody - Davis v. Davis (REVISED)",
        case_number="2026-CV-002",
        filed_date=f"{datetime.now().strftime('%B %d, %Y')} (Revised)"
    ))
    
    # CIV-003 - unchanged
    filings.append(FILING_TEMPLATE.format(
        css_class="",
        filename="civ-003.pdf",
        title="Petition for Appeal - Garcia v. State",
        case_number="2026-CV-003",
        filed_date="January 12, 2026"
    ))
    
    content = get_html_content(
        date=datetime.now().strftime("%B %d, %Y"),
        filings="\n".join(filings)
    )
    
    CONTENT_FILE.write_text(content)
    print(f"  ✓ Updated civ-002.pdf with supplemental content")
    print("\n✅ Run './venv/bin/python cli.py run' to detect changes")


def scenario_url_relocation():
    """
    Simulate CIV-003 being relocated to a new filename.
    
    This removes the original civ-003.pdf (causing 404) and creates
    civ-003-final.pdf at a new location. The content is IDENTICAL -
    only the filename changes. The crawler should find it by matching
    the form number CIV-003.
    """
    print("\n📁 Scenario: URL Relocation (CIV-003)")
    print("=" * 50)
    
    # Remove the old file - this causes the original URL to 404
    old_path = PDF_DIR / "civ-003.pdf"
    if old_path.exists():
        old_path.unlink()
        print(f"  ✗ Removed: civ-003.pdf (original URL will now 404)")
    
    # Generate PDF at new location with IDENTICAL content (same form number, same title)
    pdf_generator.create_court_form(
        filename="civ-003-final.pdf",  # New filename, same content
        **FORM_DEFINITIONS["civ-003"]  # Use exact same form definition
    )
    
    # Update HTML with new URL - the old URL is gone
    filings = []
    
    # CIV-001 - unchanged
    filings.append(FILING_TEMPLATE.format(
        css_class="",
        filename="civ-001.pdf",
        title="Motion to Dismiss - Smith v. Jones",
        case_number="2026-CV-001",
        filed_date="January 10, 2026"
    ))
    
    # CIV-002 - unchanged
    filings.append(FILING_TEMPLATE.format(
        css_class="",
        filename="civ-002.pdf",
        title="Petition for Custody - Davis v. Davis",
        case_number="2026-CV-002",
        filed_date="January 11, 2026"
    ))
    
    # CIV-003 - RELOCATED to new filename (content unchanged)
    filings.append(FILING_TEMPLATE.format(
        css_class="relocated",
        filename="civ-003-final.pdf",  # New filename!
        title="Petition for Appeal - Garcia v. State",  # Same title
        case_number="2026-CV-003",
        filed_date="January 12, 2026 (Relocated)"
    ))
    
    content = get_html_content(
        date=datetime.now().strftime("%B %d, %Y"),
        filings="\n".join(filings)
    )
    
    CONTENT_FILE.write_text(content)
    
    print(f"  ✓ Created: civ-003-final.pdf (new location)")
    print("\n  📋 What happens:")
    print("     1. Original URL /pdfs/civ-003.pdf returns 404")
    print("     2. Monitor detects download failure")
    print("     3. Crawler searches parent page /pdfs/ for PDF links")
    print("     4. Finds civ-003-final.pdf by matching form number CIV-003")
    print("     5. Updates monitored URL to new location")
    print("\n✅ Run './venv/bin/python cli.py run' to detect relocated form")


def scenario_format_only():
    """
    Simulate a format-only change (TEST-02 from PoC).
    
    Regenerates CIV-001 with identical text content but different binary.
    This tests that the system correctly identifies binary-only changes
    as "no action needed" (no semantic change).
    
    The text hash will be identical, but the PDF hash will differ due to:
    - PDF metadata timestamps
    - Font subset variations
    - Object ordering differences
    """
    print("\n📋 Scenario: Format-Only Change (CIV-001)")
    print("=" * 50)
    print("  (TEST-02: Formatting-only change with no semantic impact)")
    
    # Regenerate the exact same form - this will create a binary-different
    # PDF but with identical extracted text
    form_data = FORM_DEFINITIONS["civ-001"].copy()
    
    # Keep everything exactly the same - no content changes
    pdf_generator.create_court_form(
        filename="civ-001.pdf",
        **form_data
    )
    
    # Keep HTML unchanged (baseline filings)
    content = get_html_content(
        date="January 12, 2026",
        filings=generate_baseline_filings()
    )
    CONTENT_FILE.write_text(content)
    
    print(f"  ✓ Regenerated civ-001.pdf (binary changed, text identical)")
    print("\n  📋 What happens:")
    print("     1. PDF binary will have a different hash (new timestamps, etc.)")
    print("     2. Extracted text will be identical to previous version")
    print("     3. Monitor should detect: format_only change (binary changed, text unchanged)")
    print("     4. Change should be classified as 'format_only' (tracked but no semantic change)")
    print("\n  📊 Expected result:")
    print("     - pdf_hash_changed: True")
    print("     - text_hash_changed: False")
    print("     - Classified as: format_only (tracked, no action needed)")
    print("\n✅ Run './venv/bin/python cli.py run' to verify format-only detection")


def scenario_new_form():
    """Add an entirely new form CIV-004."""
    print("\n🆕 Scenario: New Form Added (CIV-004)")
    print("=" * 50)
    
    # Generate a completely new form
    pdf_generator.create_court_form(
        filename="civ-004.pdf",
        form_number="CIV-004",
        title="Motion for Summary Judgment",
        case_number="2026-CV-004",
        plaintiff="Robert Williams",
        defendant="Acme Corporation",
        content_sections=[
            ("INTRODUCTION", "Plaintiff Robert Williams moves for summary judgment pursuant to Rule 56 of the Federal Rules of Civil Procedure."),
            ("UNDISPUTED FACTS", "The following material facts are undisputed: Plaintiff was employed by Defendant from 2018 to 2025. Plaintiff's employment was terminated without cause on October 15, 2025."),
            ("LEGAL STANDARD", "Summary judgment is appropriate when there is no genuine dispute as to any material fact and the movant is entitled to judgment as a matter of law."),
            ("ARGUMENT", "The undisputed facts establish that Defendant breached the implied covenant of good faith and fair dealing. Plaintiff is entitled to damages as a matter of law."),
            ("RELIEF REQUESTED", "Plaintiff requests that this Court grant summary judgment and award compensatory and punitive damages.")
        ]
    )
    
    # Update HTML with all forms including the new one
    filings = []
    
    # CIV-001 - unchanged
    filings.append(FILING_TEMPLATE.format(
        css_class="",
        filename="civ-001.pdf",
        title="Motion to Dismiss - Smith v. Jones",
        case_number="2026-CV-001",
        filed_date="January 10, 2026"
    ))
    
    # CIV-002 - unchanged
    filings.append(FILING_TEMPLATE.format(
        css_class="",
        filename="civ-002.pdf",
        title="Petition for Custody - Davis v. Davis",
        case_number="2026-CV-002",
        filed_date="January 11, 2026"
    ))
    
    # CIV-003 - unchanged
    filings.append(FILING_TEMPLATE.format(
        css_class="",
        filename="civ-003.pdf",
        title="Petition for Appeal - Garcia v. State",
        case_number="2026-CV-003",
        filed_date="January 12, 2026"
    ))
    
    # CIV-004 - NEW!
    filings.append(FILING_TEMPLATE.format(
        css_class="new",
        filename="civ-004.pdf",
        title="NEW: Motion for Summary Judgment - Williams v. Acme",
        case_number="2026-CV-004",
        filed_date=datetime.now().strftime("%B %d, %Y")
    ))
    
    content = get_html_content(
        date=datetime.now().strftime("%B %d, %Y"),
        filings="\n".join(filings)
    )
    
    CONTENT_FILE.write_text(content)
    print(f"  ✓ Created new form: civ-004.pdf")
    print("\n  📋 To monitor this new form:")
    print("     Add URL: http://localhost:5001/pdfs/civ-004.pdf")
    print("     Or use the web UI to add the URL")
    print("\n✅ New form added - add it to monitoring to track future changes")


def main():
    # Ensure directories exist
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    
    scenarios = {
        'revert': scenario_revert,
        'title': scenario_title_change,
        'content': scenario_content_update,
        'relocate': scenario_url_relocation,
        'new': scenario_new_form,
        'format_only': scenario_format_only  # TEST-02: No-op detection
    }
    
    if len(sys.argv) < 2:
        print(__doc__)
        print("\nAvailable scenarios:")
        for name in scenarios:
            print(f"  - {name}")
        print("\n💡 Recommended testing flow:")
        print("   1. python test_site/simulate_update.py revert")
        print("   2. ./venv/bin/python cli.py run  (sets baseline)")
        print("   3. python test_site/simulate_update.py <scenario>")
        print("   4. ./venv/bin/python cli.py run  (detects changes)")
        print("   5. open http://localhost:8000  (view results)")
        sys.exit(1)
    
    scenario_name = sys.argv[1].lower()
    
    if scenario_name not in scenarios:
        print(f"Unknown scenario: {scenario_name}")
        print(f"Available: {', '.join(scenarios.keys())}")
        sys.exit(1)
    
    scenarios[scenario_name]()


if __name__ == "__main__":
    main()
