#!/usr/bin/env python3
"""
Script to rerun title extraction for all PDF versions in the database.

This script will:
1. Query all PDFVersion records
2. Extract titles using the updated TitleExtractor (in parallel)
3. Update the database with the new title information
"""

import sys
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from sqlalchemy.orm import Session
import structlog

from db.database import SessionLocal
from db.models import PDFVersion
from storage.file_store import FileStore
from storage.version_manager import VersionManager

logger = structlog.get_logger()


def get_title_extractor():
    """Get the appropriate title extractor based on feature flag."""
    if os.getenv("BEDROCK_NOVA_ENABLED", "False").lower() == "true":
        from services.nova_document_processor import NovaDocumentProcessor
        return NovaDocumentProcessor()
    else:
        from services.title_extractor import TitleExtractor
        return TitleExtractor()


def process_version(version_id: int, url_id: int, dry_run: bool = False) -> dict:
    """
    Process a single version's title extraction.
    
    Args:
        version_id: Version ID to process
        url_id: URL ID for the version
        dry_run: If True, don't commit changes to database
        
    Returns:
        Dictionary with result information
    """
    # Each thread needs its own database session
    db = SessionLocal()
    
    try:
        # Initialize services (each thread gets its own instances)
        file_store = FileStore()
        version_manager = VersionManager(file_store)
        extractor = get_title_extractor()
        
        if not extractor.is_available():
            return {
                "version_id": version_id,
                "success": False,
                "error": "Title extraction not available - check AWS credentials and BEDROCK_NOVA_ENABLED"
            }
        
        # Get the version from database
        version = db.query(PDFVersion).filter(PDFVersion.id == version_id).first()
        if not version:
            return {
                "version_id": version_id,
                "success": False,
                "error": "Version not found"
            }
        
        # Get PDF path
        pdf_path = version_manager.get_original_pdf_path(db, version_id)
        if not pdf_path or not pdf_path.exists():
            return {
                "version_id": version_id,
                "success": False,
                "skipped": True,
                "error": f"PDF not found: {pdf_path}"
            }
        
        # Get preview image path
        preview_output = file_store.get_preview_image_path(url_id, version_id)
        
        # Extract title
        result = extractor.extract_title(pdf_path, preview_output)
        
        if result.success:
            # Update version in database
            version.formatted_title = result.formatted_title
            version.form_number = result.form_number
            version.title_confidence = result.combined_confidence
            version.title_extraction_method = result.extraction_method
            version.revision_date = result.revision_date
            
            if not dry_run:
                db.commit()
            
            logger.info(
                f"Successfully extracted title for version {version_id}",
                title=result.formatted_title,
                form_number=result.form_number,
                confidence=result.combined_confidence
            )
            
            return {
                "version_id": version_id,
                "success": True,
                "title": result.formatted_title,
                "form_number": result.form_number,
                "confidence": result.combined_confidence
            }
        else:
            logger.warning(
                f"Title extraction failed for version {version_id}",
                error=result.error
            )
            return {
                "version_id": version_id,
                "success": False,
                "error": result.error
            }
            
    except Exception as e:
        logger.exception(
            f"Error processing version {version_id}",
            error=str(e)
        )
        db.rollback()
        return {
            "version_id": version_id,
            "success": False,
            "error": str(e)
        }
    finally:
        db.close()


def rerun_title_extraction_for_all(db: Session, dry_run: bool = False, max_workers: int = None) -> dict:
    """
    Rerun title extraction for all PDF versions using parallel processing.
    
    Args:
        db: Database session (for querying only)
        dry_run: If True, don't commit changes to database
        max_workers: Maximum number of parallel workers (defaults to CPU count * 2)
        
    Returns:
        Dictionary with statistics about the extraction
    """
    # Check AWS credentials
    extractor = TitleExtractor()
    if not extractor.is_available():
        logger.error("AWS credentials not configured. Cannot extract titles.")
        return {
            "completed": False,
            "error": "AWS credentials not configured"
        }
    
    # Get all versions
    versions = db.query(PDFVersion).order_by(PDFVersion.id).all()
    total = len(versions)
    
    if total == 0:
        logger.info("No versions to process")
        return {
            "completed": True,
            "total": 0,
            "success": 0,
            "failed": 0,
            "skipped": 0,
            "errors": []
        }
    
    # Determine number of workers
    if max_workers is None:
        # M4 Pro Max has many cores, use CPU count * 2 for I/O-bound tasks
        # But cap at a reasonable limit to avoid overwhelming AWS APIs
        cpu_count = os.cpu_count() or 8
        max_workers = min(cpu_count * 2, 50)  # Cap at 50 workers
    
    logger.info(
        f"Found {total} PDF versions to process",
        max_workers=max_workers
    )
    
    # Thread-safe statistics
    stats_lock = Lock()
    stats = {
        "total": total,
        "success": 0,
        "failed": 0,
        "skipped": 0,
        "errors": [],
        "processed": 0
    }
    
    # Process versions in parallel
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks
        future_to_version = {
            executor.submit(process_version, version.id, version.monitored_url_id, dry_run): version
            for version in versions
        }
        
        # Process completed tasks as they finish
        for future in as_completed(future_to_version):
            version = future_to_version[future]
            try:
                result = future.result()
                
                with stats_lock:
                    stats["processed"] += 1
                    progress = f"{stats['processed']}/{total}"
                    
                    if result.get("skipped"):
                        stats["skipped"] += 1
                        logger.info(
                            f"[{progress}] Skipped version {result['version_id']}",
                            reason=result.get("error", "Unknown")
                        )
                    elif result.get("success"):
                        stats["success"] += 1
                        logger.info(
                            f"[{progress}] ✓ Version {result['version_id']}",
                            title=result.get("title", "")[:50],
                            form_number=result.get("form_number", "")
                        )
                    else:
                        stats["failed"] += 1
                        stats["errors"].append({
                            "version_id": result["version_id"],
                            "error": result.get("error", "Unknown error")
                        })
                        logger.warning(
                            f"[{progress}] ✗ Version {result['version_id']}",
                            error=result.get("error", "Unknown error")
                        )
                        
            except Exception as e:
                with stats_lock:
                    stats["processed"] += 1
                    stats["failed"] += 1
                    stats["errors"].append({
                        "version_id": version.id,
                        "error": str(e)
                    })
                logger.exception(
                    f"Unexpected error processing version {version.id}",
                    error=str(e)
                )
    
    stats["completed"] = True
    return stats


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Rerun title extraction for all PDF versions (parallel processing)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't commit changes to database (for testing)"
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=None,
        help="Maximum number of parallel workers (default: CPU count * 2, max 50)"
    )
    args = parser.parse_args()
    
    # Get database session
    db = SessionLocal()
    
    try:
        stats = rerun_title_extraction_for_all(
            db, 
            dry_run=args.dry_run,
            max_workers=args.max_workers
        )
        
        print("\n" + "="*60)
        print("Title Extraction Summary")
        print("="*60)
        
        if not stats.get("completed", False):
            print(f"❌ Error: {stats.get('error', 'Unknown error')}")
            sys.exit(1)
        
        print(f"Total versions: {stats.get('total', 0)}")
        print(f"Successfully extracted: {stats.get('success', 0)}")
        print(f"Failed: {stats.get('failed', 0)}")
        print(f"Skipped: {stats.get('skipped', 0)}")
        
        if stats.get("errors"):
            print(f"\nErrors ({len(stats['errors'])}):")
            for error in stats["errors"][:10]:  # Show first 10 errors
                print(f"  Version {error['version_id']}: {error['error']}")
            if len(stats["errors"]) > 10:
                print(f"  ... and {len(stats['errors']) - 10} more errors")
        
        if args.dry_run:
            print("\n⚠️  DRY RUN MODE - No changes were committed to the database")
        else:
            print("\n✅ Title extraction completed!")
        
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user")
        db.rollback()
        sys.exit(1)
    except Exception as e:
        logger.exception("Fatal error", error=str(e))
        db.rollback()
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
