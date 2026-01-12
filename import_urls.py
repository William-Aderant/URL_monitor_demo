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


def import_urls_from_file(filepath: str, db_session) -> int:
    """Import URLs from a text file (one URL per line)."""
    path = Path(filepath)
    if not path.exists():
        logger.error("File not found", filepath=filepath)
        return 0
    
    added = 0
    with open(path) as f:
        for line in f:
            url = line.strip()
            if not url or url.startswith('#'):
                continue
            
            # Generate name from URL
            name = url.split('/')[-1].replace('.pdf', '').upper()
            
            existing = db_session.query(MonitoredURL).filter_by(url=url).first()
            if not existing:
                monitored_url = MonitoredURL(
                    name=f"Alaska {name}",
                    url=url,
                    description=f"Alaska Court Form {name}"
                )
                db_session.add(monitored_url)
                added += 1
                logger.info("Added URL", name=f"Alaska {name}")
            else:
                logger.info("URL already exists", name=existing.name)
    
    db_session.commit()
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
