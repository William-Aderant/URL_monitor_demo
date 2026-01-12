#!/usr/bin/env python3
"""
Script to clean up the URL database - keep only local test forms.
"""

import structlog
from sqlalchemy import or_

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
from db.models import MonitoredURL, PDFVersion, ChangeLog


def cleanup_to_test_only():
    """Remove all URLs except localhost:5001 test forms."""
    settings.ensure_directories()
    run_migrations()
    
    db = SessionLocal()
    try:
        # Find all non-test URLs
        non_test_urls = db.query(MonitoredURL).filter(
            ~MonitoredURL.url.like("http://localhost:5001%")
        ).all()
        
        print(f"\n=== Found {len(non_test_urls)} non-test URLs to remove ===\n")
        
        removed_count = 0
        for url in non_test_urls:
            # Delete associated change logs first
            change_logs = db.query(ChangeLog).filter_by(url_id=url.id).all()
            for cl in change_logs:
                db.delete(cl)
            
            # Delete associated PDF versions
            versions = db.query(PDFVersion).filter_by(url_id=url.id).all()
            for v in versions:
                db.delete(v)
            
            # Delete the URL
            logger.info("Removing URL", name=url.name, url=url.url)
            db.delete(url)
            removed_count += 1
        
        db.commit()
        
        print(f"\n✓ Removed {removed_count} URLs\n")
        
        # Show remaining URLs
        remaining = db.query(MonitoredURL).all()
        print(f"=== Remaining URLs ({len(remaining)}) ===\n")
        for url in remaining:
            print(f"  • {url.name}")
            print(f"    URL: {url.url}")
            print()
            
    finally:
        db.close()


if __name__ == "__main__":
    print("\n🧹 Cleaning up database to keep only test forms...\n")
    cleanup_to_test_only()
    print("✅ Done! Run 'python cli.py run' to monitor only the test forms.")
