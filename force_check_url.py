#!/usr/bin/env python3
"""
Force a full check for a specific URL by clearing fast change detection metadata.

This clears HTTP headers and quick hash so the system will do a full download
and comparison, even if the PDF hasn't actually changed.

Usage:
    python force_check_url.py <url_id>
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from db.database import SessionLocal
from db.models import MonitoredURL

def force_check(url_id: int):
    """Clear fast change detection metadata to force full check."""
    db = SessionLocal()
    
    try:
        url = db.query(MonitoredURL).filter(MonitoredURL.id == url_id).first()
        
        if not url:
            print(f"❌ Error: URL with ID {url_id} not found")
            sys.exit(1)
        
        print(f"\n🔄 Clearing fast change detection metadata for URL ID {url_id}")
        print(f"   Name: {url.name}")
        print(f"   URL: {url.url}")
        
        # Clear fast change detection metadata
        url.last_modified_header = None
        url.etag_header = None
        url.content_length_header = None
        url.quick_hash = None
        
        # Also clear last_checked_at to ensure it runs
        url.last_checked_at = None
        
        db.commit()
        
        print("\n✅ Fast change detection metadata cleared")
        print("   Next monitoring cycle will perform full download and comparison")
        print("\nRun: python cli.py run")
        
    except Exception as e:
        db.rollback()
        print(f"\n❌ Error: {e}")
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python force_check_url.py <url_id>")
        sys.exit(1)
    
    try:
        url_id = int(sys.argv[1])
        force_check(url_id)
    except ValueError:
        print(f"❌ Error: Invalid URL ID: {sys.argv[1]}")
        sys.exit(1)
