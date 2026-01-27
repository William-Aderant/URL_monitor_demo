#!/usr/bin/env python3
"""
Reset to Original Versions Only

Deletes all PDF versions except the original (version_number=1) for each URL.
This allows retesting change detection from scratch.

What this script does:
1. Deletes all ChangeLog entries
2. Deletes all PDFVersion entries where version_number > 1
3. Deletes corresponding files from the file system
4. Resets tracking fields on MonitoredURL so they will be re-checked
"""

import shutil
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import structlog

from config import settings
from db.models import MonitoredURL, PDFVersion, ChangeLog
from storage.file_store import FileStore

# Set up logging
structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer()
    ]
)
logger = structlog.get_logger()


def reset_to_originals():
    """Delete all versions except originals and reset tracking."""
    
    # Connect to database
    engine = create_engine(settings.DATABASE_URL)
    Session = sessionmaker(bind=engine)
    db = Session()
    
    file_store = FileStore()
    
    try:
        # Step 1: Count what we're about to delete
        total_changes = db.query(ChangeLog).count()
        total_versions = db.query(PDFVersion).count()
        original_versions = db.query(PDFVersion).filter(PDFVersion.version_number == 1).count()
        versions_to_delete = total_versions - original_versions
        
        logger.info(
            "Current state",
            total_changes=total_changes,
            total_versions=total_versions,
            original_versions=original_versions,
            versions_to_delete=versions_to_delete
        )
        
        # Confirm with user
        print(f"\n{'='*60}")
        print("RESET TO ORIGINAL VERSIONS ONLY")
        print(f"{'='*60}")
        print(f"Total change logs to delete: {total_changes}")
        print(f"Total versions: {total_versions}")
        print(f"Original versions (v1) to keep: {original_versions}")
        print(f"Versions to delete (v2+): {versions_to_delete}")
        print(f"{'='*60}\n")
        
        confirm = input("Are you sure you want to proceed? Type 'yes' to confirm: ")
        if confirm.lower() != 'yes':
            print("Aborted.")
            return
        
        # Step 2: Delete all change logs
        logger.info("Deleting all change logs...")
        deleted_changes = db.query(ChangeLog).delete()
        logger.info("Deleted change logs", count=deleted_changes)
        
        # Step 3: Get all non-original versions
        versions_to_remove = db.query(PDFVersion).filter(
            PDFVersion.version_number > 1
        ).all()
        
        # Step 4: Delete files for each version
        logger.info("Deleting version files...", count=len(versions_to_remove))
        files_deleted = 0
        for version in versions_to_remove:
            # Delete the version directory and all its files
            deleted = file_store.delete_version(version.monitored_url_id, version.id)
            if deleted:
                files_deleted += 1
        logger.info("Deleted version directories", count=files_deleted)
        
        # Step 5: Delete version records from database
        deleted_versions = db.query(PDFVersion).filter(
            PDFVersion.version_number > 1
        ).delete()
        logger.info("Deleted version records", count=deleted_versions)
        
        # Step 6: Reset tracking fields on all MonitoredURLs
        logger.info("Resetting URL tracking fields...")
        urls = db.query(MonitoredURL).all()
        for url in urls:
            # Reset last_checked_at so URL will be rechecked
            url.last_checked_at = None
            # Reset last_change_at
            url.last_change_at = None
            # Reset quick detection fields
            url.last_modified_header = None
            url.etag_header = None
            url.content_length_header = None
            url.quick_hash = None
        logger.info("Reset tracking fields", url_count=len(urls))
        
        # Commit all changes
        db.commit()
        
        # Final count
        remaining_versions = db.query(PDFVersion).count()
        remaining_changes = db.query(ChangeLog).count()
        
        print(f"\n{'='*60}")
        print("RESET COMPLETE")
        print(f"{'='*60}")
        print(f"Remaining versions (all originals): {remaining_versions}")
        print(f"Remaining change logs: {remaining_changes}")
        print(f"URLs reset for re-checking: {len(urls)}")
        print(f"{'='*60}\n")
        
        logger.info(
            "Reset complete",
            remaining_versions=remaining_versions,
            remaining_changes=remaining_changes,
            urls_reset=len(urls)
        )
        
    except Exception as e:
        db.rollback()
        logger.error("Reset failed", error=str(e))
        raise
    finally:
        db.close()


if __name__ == "__main__":
    reset_to_originals()
