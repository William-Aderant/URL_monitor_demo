#!/usr/bin/env python3
"""
Test script for the enhanced LinkCrawler.

Demonstrates multi-level recursive crawling for finding relocated forms
on the Alaska Court System website.
"""

import structlog

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer()
    ]
)

from services.link_crawler import LinkCrawler


def test_alaska_courts_crawl():
    """
    Test crawling the Alaska Court System forms page.
    
    The site structure is:
        https://public.courts.alaska.gov/web/forms/
        ├── Civil forms (expandable section)
        │   └── Links to civ-*.pdf files
        ├── Domestic Relations forms
        │   └── Links to dr-*.pdf files
        ├── Domestic Violence forms
        │   └── Links to dv-*.pdf files
        └── ... many more sections
    
    When a form like CIV-775 relocates, we need to:
    1. Start at /web/forms/
    2. Follow links to find the Civil section
    3. Search through PDF links for CIV-775
    """
    print("\n" + "="*60)
    print("Testing Enhanced LinkCrawler - Alaska Court System")
    print("="*60)
    
    # Initialize crawler with reasonable limits
    crawler = LinkCrawler(
        timeout=30,
        max_depth=2,      # Don't go too deep
        max_pages=30,     # Limit for testing
        delay_between_requests=0.3
    )
    
    # Simulate looking for a relocated form
    original_url = "https://public.courts.alaska.gov/web/forms/docs/civ-775.pdf"
    form_number = "CIV-775"
    
    print(f"\nOriginal URL: {original_url}")
    print(f"Form Number: {form_number}")
    
    # Test 1: Extract base forms URL
    print("\n--- Test 1: Extract Base Forms URL ---")
    base_url = crawler.extract_base_forms_url(original_url)
    print(f"Base Forms URL: {base_url}")
    
    # Test 2: Extract form number from various formats
    print("\n--- Test 2: Form Number Extraction ---")
    test_strings = [
        "civ-775.pdf",
        "CIV775",
        "Form No. CIV-775",
        "Alaska Form CIV-775 Motion to Dismiss",
        "dr-100.pdf",
        "dv-150-instructions.pdf",
    ]
    for s in test_strings:
        form_num = crawler.extract_form_number(s)
        print(f"  '{s}' -> {form_num}")
    
    # Test 3: Single page crawl (fast)
    print("\n--- Test 3: Single Page Crawl ---")
    print("Crawling: https://public.courts.alaska.gov/web/forms/")
    result = crawler.crawl_page_for_pdfs("https://public.courts.alaska.gov/web/forms/")
    
    if result.success:
        print(f"  PDFs found: {len(result.pdf_links or [])}")
        if result.pdf_links:
            print(f"  First 5 PDFs:")
            for pdf in result.pdf_links[:5]:
                print(f"    - {pdf.split('/')[-1]}")
    else:
        print(f"  Error: {result.error}")
    
    # Test 4: Recursive crawl to find a specific form
    print("\n--- Test 4: Recursive Crawl for Form ---")
    print(f"Searching for: {form_number}")
    print("Starting from: https://public.courts.alaska.gov/web/forms/")
    print("(This may take a moment...)")
    
    result = crawler.recursive_crawl(
        start_url="https://public.courts.alaska.gov/web/forms/",
        target_form_number=form_number,
        target_filename="civ-775.pdf",
        use_bfs=True
    )
    
    print(f"\nResults:")
    print(f"  Success: {result.success}")
    print(f"  Pages crawled: {result.pages_crawled}")
    print(f"  Total PDFs found: {len(result.pdf_links or [])}")
    
    if result.matched_url:
        print(f"  ✅ FOUND: {result.matched_url}")
        print(f"  Reason: {result.match_reason}")
        if result.search_path:
            print(f"  Search path:")
            for step in result.search_path[:5]:
                print(f"    -> {step}")
    else:
        print(f"  ❌ Not found")
        print(f"  Reason: {result.match_reason}")
    
    # Test 5: Full relocation detection
    print("\n--- Test 5: Full Relocation Detection ---")
    print(f"Simulating: Form at {original_url} returned 404")
    print("Attempting to find relocated form...")
    
    result = crawler.find_relocated_form(
        original_url=original_url,
        form_number=form_number,
        form_title="Motion to Dismiss",
        parent_url="https://public.courts.alaska.gov/web/forms/"
    )
    
    print(f"\nResults:")
    print(f"  Success: {result.success}")
    print(f"  Pages crawled: {result.pages_crawled}")
    
    if result.matched_url:
        print(f"  ✅ Form found at: {result.matched_url}")
        print(f"  Match reason: {result.match_reason}")
    else:
        print(f"  ❌ Form not found")
        print(f"  {result.match_reason or result.error}")
    
    print("\n" + "="*60)
    print("Test Complete")
    print("="*60)


def test_local_server_crawl():
    """Test crawling against the local test server."""
    print("\n" + "="*60)
    print("Testing Enhanced LinkCrawler - Local Test Server")
    print("="*60)
    
    crawler = LinkCrawler(
        timeout=10,
        max_depth=2,
        max_pages=20,
        delay_between_requests=0.1
    )
    
    print("\nNote: Make sure the test server is running: python test_server.py")
    
    # Test crawling local server
    print("\n--- Crawling Local Test Server ---")
    result = crawler.crawl_page_for_pdfs("http://localhost:5001/pdfs/")
    
    if result.success:
        print(f"  PDFs found: {len(result.pdf_links or [])}")
        for pdf in (result.pdf_links or []):
            print(f"    - {pdf}")
    else:
        print(f"  Error: {result.error}")
        print("  (Is the test server running?)")


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "local":
        test_local_server_crawl()
    else:
        test_alaska_courts_crawl()
