#!/usr/bin/env python3
"""
Script to bulk import URLs into the URL monitor.

Supports:
- Importing from alaska_links.txt
- Adding local test server PDFs with proper parent_page_url for relocation detection
"""

import structlog
from pathlib import Path

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer()
    ]
)

logger = structlog.get_logger()

from config import settings
from db.database import SessionLocal
from db.migrations import run_migrations
from db.models import MonitoredURL


def get_forms_index_url(url: str) -> str:
    """
    Get the main forms index page for a court forms URL.
    
    For Alaska Court System, this returns the base forms page:
    https://public.courts.alaska.gov/web/forms/
    
    This enables the enhanced crawler to traverse all form sections
    to find relocated forms.
    """
    from urllib.parse import urlparse
    import re
    
    parsed = urlparse(url)
    path = parsed.path.lower()
    
    # Alaska Court System pattern
    # https://public.courts.alaska.gov/web/forms/docs/civ-775.pdf
    # -> https://public.courts.alaska.gov/web/forms/
    if 'courts.alaska.gov' in parsed.netloc:
        match = re.search(r'(/web/forms/)', path, re.IGNORECASE)
        if match:
            return f"{parsed.scheme}://{parsed.netloc}{match.group(1)}"
    
    # Generic pattern: look for /forms/ in the path
    match = re.search(r'(/[^/]*forms[^/]*/)', path, re.IGNORECASE)
    if match:
        return f"{parsed.scheme}://{parsed.netloc}{match.group(1)}"
    
    # Fallback: use immediate parent directory
    path_parts = parsed.path.split('/')
    if len(path_parts) > 1:
        parent_path = '/'.join(path_parts[:-1]) + '/'
        return f"{parsed.scheme}://{parsed.netloc}{parent_path}"
    
    return None


def import_urls_from_file(filepath: str, db_session) -> int:
    """Import URLs from a text file (one URL per line)."""
    from urllib.parse import urlparse
    
    path = Path(filepath)
    if not path.exists():
        logger.error("File not found", filepath=filepath)
        return 0
    
    added = 0
    updated = 0
    
    with open(path) as f:
        for line in f:
            url = line.strip()
            if not url or url.startswith('#'):
                continue
            
            # Generate name from URL
            name = url.split('/')[-1].replace('.pdf', '').upper()
            
            # Get the forms index page URL for recursive crawling
            # This is the key for finding relocated forms - the crawler
            # will start here and traverse all sections to find the form
            parent_page_url = get_forms_index_url(url)
            
            existing = db_session.query(MonitoredURL).filter_by(url=url).first()
            if not existing:
                monitored_url = MonitoredURL(
                    name=f"Alaska {name}",
                    url=url,
                    description=f"Alaska Court Form {name}",
                    parent_page_url=parent_page_url
                )
                db_session.add(monitored_url)
                added += 1
                logger.info("Added URL", name=f"Alaska {name}", parent_page_url=parent_page_url)
            else:
                # Update parent_page_url to the forms index page
                if existing.parent_page_url != parent_page_url:
                    existing.parent_page_url = parent_page_url
                    updated += 1
                    logger.info("Updated parent_page_url", name=existing.name, parent_page_url=parent_page_url)
                else:
                    logger.info("URL already exists", name=existing.name)
    
    db_session.commit()
    
    if updated > 0:
        logger.info(f"Updated {updated} existing URLs with new parent_page_url")
    
    return added


def add_test_server(db_session) -> bool:
    """Add the local test server URL (main page)."""
    url = "http://localhost:5001"
    existing = db_session.query(MonitoredURL).filter_by(url=url).first()
    
    if not existing:
        monitored_url = MonitoredURL(
            name="Local Test Server",
            url=url,
            description="Local test server for simulating website updates"
        )
        db_session.add(monitored_url)
        db_session.commit()
        logger.info("Added test server URL")
        return True
    else:
        logger.info("Test server URL already exists")
        return False


def add_test_pdfs(db_session) -> int:
    """
    Add individual test PDF URLs with proper parent_page_url.
    
    The parent_page_url is crucial for relocation detection:
    when a PDF URL 404s, the crawler visits the parent page
    to find all PDF links and match by form number.
    """
    test_pdfs = [
        {
            "name": "Test CIV-001 - Motion to Dismiss",
            "url": "http://localhost:5001/pdfs/civ-001.pdf",
            "description": "Test form: Motion to Dismiss - Smith v. Jones",
            "parent_page_url": "http://localhost:5001/pdfs/"
        },
        {
            "name": "Test CIV-002 - Petition for Custody",
            "url": "http://localhost:5001/pdfs/civ-002.pdf",
            "description": "Test form: Petition for Custody - Davis v. Davis",
            "parent_page_url": "http://localhost:5001/pdfs/"
        },
        {
            "name": "Test CIV-003 - Petition for Appeal",
            "url": "http://localhost:5001/pdfs/civ-003.pdf",
            "description": "Test form: Petition for Appeal - Garcia v. State",
            "parent_page_url": "http://localhost:5001/pdfs/"
        }
    ]
    
    added = 0
    for pdf_info in test_pdfs:
        existing = db_session.query(MonitoredURL).filter_by(url=pdf_info["url"]).first()
        
        if not existing:
            monitored_url = MonitoredURL(
                name=pdf_info["name"],
                url=pdf_info["url"],
                description=pdf_info["description"],
                parent_page_url=pdf_info["parent_page_url"]
            )
            db_session.add(monitored_url)
            added += 1
            logger.info("Added test PDF", name=pdf_info["name"])
        else:
            # Update parent_page_url if it's missing
            if not existing.parent_page_url:
                existing.parent_page_url = pdf_info["parent_page_url"]
                logger.info("Updated parent_page_url", name=existing.name)
            else:
                logger.info("Test PDF already exists", name=existing.name)
    
    db_session.commit()
    return added


def remove_old_test_urls(db_session) -> int:
    """Remove old test URLs that no longer match our naming scheme."""
    old_patterns = [
        "http://localhost:5001/pdfs/case-2026-001.pdf",
        "http://localhost:5001/pdfs/case-2026-001-final.pdf",
        "http://localhost:5001/pdfs/case-2026-003.pdf",
    ]
    
    removed = 0
    for url in old_patterns:
        existing = db_session.query(MonitoredURL).filter_by(url=url).first()
        if existing:
            db_session.delete(existing)
            removed += 1
            logger.info("Removed old test URL", url=url)
    
    db_session.commit()
    return removed


def main():
    settings.ensure_directories()
    run_migrations()
    
    db = SessionLocal()
    try:
        # Clean up old test URLs
        print("\n=== Cleaning Up Old Test URLs ===")
        removed = remove_old_test_urls(db)
        if removed:
            print(f"Removed {removed} old test URL(s)")
        
        # Add test server main page
        print("\n=== Adding Test Server ===")
        add_test_server(db)
        
        # Add individual test PDFs with parent_page_url
        print("\n=== Adding Test PDFs ===")
        pdf_count = add_test_pdfs(db)
        print(f"Added {pdf_count} new test PDF URL(s)")
        
        # Import Alaska links
        print("\n=== Importing Alaska Court Forms ===")
        count = import_urls_from_file("alaska_links.txt", db)
        print(f"\nAdded {count} new URLs from alaska_links.txt")
        
        # Show total count
        total = db.query(MonitoredURL).count()
        print(f"\n=== Total Monitored URLs: {total} ===")
        
        # Show test PDFs
        print("\n=== Test PDF URLs (with parent_page_url) ===")
        test_urls = db.query(MonitoredURL).filter(
            MonitoredURL.url.like("http://localhost:5001/pdfs/%")
        ).all()
        for url in test_urls:
            print(f"  • {url.name}")
            print(f"    URL: {url.url}")
            print(f"    Parent: {url.parent_page_url or 'Not set'}")
            print()
        
    finally:
        db.close()


if __name__ == "__main__":
    main()
