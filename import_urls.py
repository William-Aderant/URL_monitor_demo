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
                    parent_page_url=parent_page_url,
                    state="Alaska",
                    domain_category="courts.alaska.gov"
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
    """DEPRECATED: Add the local test server URL (main page)."""
    logger.warning("add_test_server is deprecated - localhost URLs have been removed from the application")
    return False


def add_test_pdfs(db_session) -> int:
    """DEPRECATED: Add individual test PDF URLs with proper parent_page_url."""
    logger.warning("add_test_pdfs is deprecated - localhost URLs have been removed from the application")
    return 0
                logger.info("Test PDF already exists", name=existing.name)
    
    db_session.commit()
    return added


def remove_old_test_urls(db_session) -> int:
    """DEPRECATED: Remove old test URLs that no longer match our naming scheme."""
    logger.warning("remove_old_test_urls is deprecated - localhost URLs have been removed from the application")
    return 0


def main():
    settings.ensure_directories()
    run_migrations()
    
    db = SessionLocal()
    try:
        # Import Alaska links
        print("\n=== Importing Alaska Court Forms ===")
        count = import_urls_from_file("alaska_links.txt", db)
        print(f"\nAdded {count} new URLs from alaska_links.txt")
        
        # Show total count
        total = db.query(MonitoredURL).count()
        print(f"\n=== Total Monitored URLs: {total} ===")
        
    finally:
        db.close()


if __name__ == "__main__":
    main()
