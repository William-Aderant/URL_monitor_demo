#!/usr/bin/env python3
"""
Reset tracking for all monitored URLs.

This script:
1. Deletes all change logs (clears false positives)
2. Keeps only the first version of each form (baseline)
3. Resets last_change_at timestamps
4. Resets review status

Use this after fixing false positive detection issues.
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


def reset_tracking(keep_first_version: bool = True):
    """
    Reset tracking for all monitored URLs.
    
    Args:
        keep_first_version: If True, keeps the first version of each form as baseline.
                          If False, deletes all versions.
    """
    db = SessionLocal()
    file_store = FileStore()
    
    try:
        # Get all monitored URLs
        urls = db.query(MonitoredURL).all()
        
        total_changes_deleted = 0
        total_versions_deleted = 0
        urls_reset = 0
        
        for url in urls:
            logger.info(f"Processing URL: {url.name} (ID: {url.id})")
            
            # Delete all change logs for this URL
            changes = db.query(ChangeLog).filter(
                ChangeLog.monitored_url_id == url.id
            ).all()
            
            change_count = len(changes)
            for change in changes:
                db.delete(change)
            total_changes_deleted += change_count
            
            logger.info(f"  Deleted {change_count} change logs")
            
            # Handle versions
            versions = db.query(PDFVersion).filter(
                PDFVersion.monitored_url_id == url.id
            ).order_by(PDFVersion.version_number).all()
            
            if keep_first_version and len(versions) > 0:
                # Keep first version, delete the rest
                first_version = versions[0]
                versions_to_delete = versions[1:]
                
                logger.info(
                    f"  Keeping first version (v{first_version.version_number}), "
                    f"deleting {len(versions_to_delete)} subsequent versions"
                )
                
                for version in versions_to_delete:
                    # Delete files
                    file_store.delete_version(url.id, version.id)
                    # Delete version record
                    db.delete(version)
                    total_versions_deleted += 1
            else:
                # Delete all versions
                logger.info(f"  Deleting all {len(versions)} versions")
                for version in versions:
                    # Delete files
                    file_store.delete_version(url.id, version.id)
                    # Delete version record
                    db.delete(version)
                    total_versions_deleted += len(versions)
            
            # Reset timestamps
            url.last_change_at = None
            urls_reset += 1
        
        # Commit all changes
        db.commit()
        
        print("\n" + "="*60)
        print("✅ Tracking Reset Complete")
        print("="*60)
        print(f"URLs processed: {urls_reset}")
        print(f"Change logs deleted: {total_changes_deleted}")
        print(f"Versions deleted: {total_versions_deleted}")
        if keep_first_version:
            print(f"Baseline versions kept: {urls_reset}")
        print("\nNext steps:")
        print("  1. Run monitoring cycle: python cli.py run")
        print("  2. This will create new baseline versions")
        print("  3. Future changes will be compared against these baselines")
        print("="*60)
        
    except Exception as e:
        db.rollback()
        logger.exception("Error resetting tracking", error=str(e))
        print(f"\n❌ Error: {e}")
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Reset tracking for all monitored URLs"
    )
    parser.add_argument(
        "--delete-all-versions",
        action="store_true",
        help="Delete all versions (including first). Default: keep first version as baseline."
    )
    
    args = parser.parse_args()
    
    print("\n⚠️  WARNING: This will delete all change logs and versions!")
    if not args.delete_all_versions:
        print("   (First version of each form will be kept as baseline)")
    else:
        print("   (ALL versions will be deleted)")
    
    response = input("\nAre you sure you want to continue? (yes/no): ")
    
    if response.lower() in ['yes', 'y']:
        reset_tracking(keep_first_version=not args.delete_all_versions)
    else:
        print("Cancelled.")
