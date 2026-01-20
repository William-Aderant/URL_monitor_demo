#!/usr/bin/env python3
"""
Reset change detection for a specific URL.

This script:
1. Deletes all change logs for the specified URL
2. Keeps only the first version as baseline (or deletes all versions)
3. Resets last_change_at timestamp

Usage:
    python reset_url_changes.py <url_id> [--delete-all-versions]
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

import structlog
from sqlalchemy.orm import Session

from db.database import SessionLocal
from db.models import MonitoredURL, PDFVersion, ChangeLog
from storage.file_store import FileStore

# Configure logging
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer()
    ]
)

logger = structlog.get_logger()


def reset_url_changes(url_id: int, keep_first_version: bool = True):
    """
    Reset change detection for a specific URL.
    
    Args:
        url_id: The ID of the URL to reset
        keep_first_version: If True, keeps the first version as baseline.
                          If False, deletes all versions.
    """
    db = SessionLocal()
    file_store = FileStore()
    
    try:
        # Get the specific URL
        url = db.query(MonitoredURL).filter(MonitoredURL.id == url_id).first()
        
        if not url:
            print(f"❌ Error: URL with ID {url_id} not found")
            sys.exit(1)
        
        print(f"\n🔄 Resetting change detection for URL ID {url_id}")
        print(f"   Name: {url.name}")
        print(f"   URL: {url.url}")
        
        # Delete all change logs for this URL
        changes = db.query(ChangeLog).filter(
            ChangeLog.monitored_url_id == url.id
        ).all()
        
        change_count = len(changes)
        for change in changes:
            db.delete(change)
        
        logger.info(f"Deleted {change_count} change logs for URL {url_id}")
        print(f"   ✓ Deleted {change_count} change logs")
        
        # Handle versions
        versions = db.query(PDFVersion).filter(
            PDFVersion.monitored_url_id == url.id
        ).order_by(PDFVersion.version_number).all()
        
        if keep_first_version and len(versions) > 0:
            # Keep first version, delete the rest
            first_version = versions[0]
            versions_to_delete = versions[1:]
            
            logger.info(
                f"Keeping first version (v{first_version.version_number}), "
                f"deleting {len(versions_to_delete)} subsequent versions"
            )
            
            for version in versions_to_delete:
                # Delete files
                file_store.delete_version(url.id, version.id)
                # Delete version record
                db.delete(version)
            
            print(f"   ✓ Kept first version (v{first_version.version_number})")
            print(f"   ✓ Deleted {len(versions_to_delete)} subsequent versions")
        elif len(versions) > 0:
            # Delete all versions
            logger.info(f"Deleting all {len(versions)} versions")
            for version in versions:
                # Delete files
                file_store.delete_version(url.id, version.id)
                # Delete version record
                db.delete(version)
            print(f"   ✓ Deleted all {len(versions)} versions")
        else:
            print(f"   ℹ No versions found")
        
        # Reset timestamps
        url.last_change_at = None
        url.last_checked_at = None
        
        # Commit all changes
        db.commit()
        
        print("\n" + "="*60)
        print("✅ Reset Complete")
        print("="*60)
        print(f"URL ID: {url_id}")
        print(f"Name: {url.name}")
        print(f"Change logs deleted: {change_count}")
        if keep_first_version and len(versions) > 0:
            print(f"Versions kept: 1 (baseline)")
            print(f"Versions deleted: {len(versions) - 1}")
        else:
            print(f"Versions deleted: {len(versions)}")
        print("\nNext steps:")
        print("  1. Run monitoring cycle: python cli.py run")
        print("  2. This will detect changes and test the fix")
        print("="*60)
        
    except Exception as e:
        db.rollback()
        logger.exception("Error resetting URL changes", error=str(e), url_id=url_id)
        print(f"\n❌ Error: {e}")
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Reset change detection for a specific URL"
    )
    parser.add_argument(
        "url_id",
        type=int,
        help="The ID of the URL to reset"
    )
    parser.add_argument(
        "--delete-all-versions",
        action="store_true",
        help="Delete all versions (including first). Default: keep first version as baseline."
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip confirmation prompt (non-interactive mode)"
    )
    
    args = parser.parse_args()
    
    print("\n⚠️  WARNING: This will delete all change logs for URL ID", args.url_id)
    if not args.delete_all_versions:
        print("   (First version will be kept as baseline)")
    else:
        print("   (ALL versions will be deleted)")
    
    if not args.yes:
        response = input("\nAre you sure you want to continue? (yes/no): ")
        if response.lower() not in ['yes', 'y']:
            print("Cancelled.")
            sys.exit(0)
    
    reset_url_changes(args.url_id, keep_first_version=not args.delete_all_versions)
