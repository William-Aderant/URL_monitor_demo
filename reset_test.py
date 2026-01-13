#!/usr/bin/env python3
"""
Reset Test Environment

Resets the test monitoring environment to a clean state:
- Clears all versions and changes for test forms (civ-001, civ-002, civ-003)
- Restores original URLs (handles relocated URLs like civ-003-final.pdf)
- Reverts test site PDFs to baseline
- Keeps Alaska forms disabled

Usage:
    python reset_test.py
"""

import shutil
import json
from pathlib import Path
from datetime import datetime

# Setup logging
import structlog
structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer()
    ]
)

from db.database import SessionLocal
from db.models import MonitoredURL, PDFVersion, ChangeLog

# #region agent log
LOG_PATH = "/Users/william.holden/Documents/GitHub/URL_monitor_demo/.cursor/debug.log"
def _log(hypothesis_id, location, message, data):
    try:
        with open(LOG_PATH, "a") as f:
            f.write(json.dumps({
                "sessionId": "debug-session",
                "runId": "run1",
                "hypothesisId": hypothesis_id,
                "location": location,
                "message": message,
                "data": data,
                "timestamp": int(datetime.now().timestamp() * 1000)
            }) + "\n")
    except: pass
# #endregion


def reset_test_environment():
    """Reset all test forms to clean state."""
    print("\n" + "=" * 50)
    print("🔄 Resetting Test Environment")
    print("=" * 50)
    
    db = SessionLocal()
    
    try:
        # #region agent log
        all_urls = db.query(MonitoredURL).all()
        _log("A", "reset_test.py:59", "All URLs in database before reset", {
            "count": len(all_urls),
            "urls": [{"id": u.id, "name": u.name, "url": u.url} for u in all_urls]
        })
        # #endregion
        
        # Target URLs for test forms (find by URL pattern, not hardcoded IDs)
        target_urls = [
            "http://localhost:5001/pdfs/civ-001.pdf",
            "http://localhost:5001/pdfs/civ-002.pdf",
            "http://localhost:5001/pdfs/civ-003.pdf",
        ]
        
        # Also handle relocated URLs (e.g., civ-003-final.pdf)
        test_url_pattern = "http://localhost:5001/pdfs/civ-"
        
        # Find all test URLs (by pattern matching)
        test_urls = db.query(MonitoredURL).filter(
            MonitoredURL.url.like(f"{test_url_pattern}%")
        ).all()
        
        # #region agent log
        _log("A", "reset_test.py:77", "Found test URLs by pattern", {
            "pattern": test_url_pattern,
            "found_count": len(test_urls),
            "urls": [{"id": u.id, "name": u.name, "url": u.url} for u in test_urls]
        })
        # #endregion
        
        for url in test_urls:
            # #region agent log
            _log("B", "reset_test.py:84", "Processing test URL", {
                "url_id": url.id,
                "current_url": url.url,
                "name": url.name
            })
            # #endregion
            
            # Determine target URL based on form number in name or current URL
            target_url = None
            if "CIV-001" in url.name or "civ-001" in url.url.lower():
                target_url = "http://localhost:5001/pdfs/civ-001.pdf"
            elif "CIV-002" in url.name or "civ-002" in url.url.lower():
                target_url = "http://localhost:5001/pdfs/civ-002.pdf"
            elif "CIV-003" in url.name or "civ-003" in url.url.lower():
                target_url = "http://localhost:5001/pdfs/civ-003.pdf"
            
            # #region agent log
            _log("A", "reset_test.py:99", "Determined target URL", {
                "url_id": url.id,
                "current_url": url.url,
                "target_url": target_url
            })
            # #endregion
            
            if not target_url:
                print(f"  ⚠ Skipping {url.name} (could not determine target URL)")
                continue
            
            # Delete change logs
            changes = db.query(ChangeLog).filter(ChangeLog.monitored_url_id == url.id).delete()
            
            # Delete versions
            versions = db.query(PDFVersion).filter(PDFVersion.monitored_url_id == url.id).delete()
            
            # Check if target URL already exists on another record
            existing_url = db.query(MonitoredURL).filter(
                MonitoredURL.url == target_url,
                MonitoredURL.id != url.id
            ).first()
            
            # #region agent log
            _log("C", "reset_test.py:116", "Checking for URL conflicts", {
                "url_id": url.id,
                "target_url": target_url,
                "conflict_exists": existing_url is not None,
                "conflict_id": existing_url.id if existing_url else None
            })
            # #endregion
            
            if existing_url:
                # If target URL exists on another record, delete the conflicting record first
                print(f"  ⚠ Target URL {target_url} exists on ID {existing_url.id}, deleting conflict...")
                db.query(ChangeLog).filter(ChangeLog.monitored_url_id == existing_url.id).delete()
                db.query(PDFVersion).filter(PDFVersion.monitored_url_id == existing_url.id).delete()
                db.delete(existing_url)
                # #region agent log
                _log("C", "reset_test.py:125", "Deleted conflicting URL record", {
                    "deleted_id": existing_url.id,
                    "deleted_url": existing_url.url
                })
                # #endregion
            
            # Reset URL timestamps and restore original URL
            url.last_checked_at = None
            url.last_change_at = None
            
            # #region agent log
            _log("D", "reset_test.py:133", "BEFORE setting URL", {
                "url_id": url.id,
                "current_url": url.url,
                "new_url": target_url
            })
            # #endregion
            
            url.url = target_url
            
            # #region agent log
            _log("D", "reset_test.py:141", "AFTER setting URL", {
                "url_id": url.id,
                "url_value": url.url
            })
            # #endregion
            
            print(f"  ✓ Reset {url.name}")
            print(f"    - Deleted {versions} versions, {changes} changes")
            print(f"    - URL: {url.url}")
        
        # #region agent log
        _log("D", "reset_test.py:149", "BEFORE commit - checking all pending changes", {
            "pending_urls": [
                {"id": u.id, "url": u.url} 
                for u in db.dirty if isinstance(u, MonitoredURL)
            ],
            "deleted_count": len([u for u in db.deleted if isinstance(u, MonitoredURL)])
        })
        # #endregion
        
        db.commit()
        
        # #region agent log
        _log("D", "reset_test.py:157", "AFTER commit - commit successful", {})
        # #endregion
        
        # Clean up data files for all test URLs (by their IDs)
        data_path = Path("data/pdfs")
        for url in test_urls:
            version_dir = data_path / str(url.id)
            if version_dir.exists():
                shutil.rmtree(version_dir)
                print(f"  ✓ Cleaned data/pdfs/{url.id}/")
        
        print("\n" + "-" * 50)
        
        # Revert test site PDFs
        print("\n📄 Reverting test site PDFs...")
        import subprocess
        result = subprocess.run(
            ["python", "test_site/simulate_update.py", "revert"],
            capture_output=True,
            text=True
        )
        print(result.stdout)
        
        print("=" * 50)
        print("✅ Test environment reset complete!")
        print("=" * 50)
        print("\nNext steps:")
        print("  1. ./venv/bin/python cli.py run    # Set baseline")
        print("  2. python test_site/simulate_update.py <scenario>")
        print("  3. ./venv/bin/python cli.py run    # Detect changes")
        print("  4. open http://localhost:8000      # View results")
        print()
        
    finally:
        db.close()


if __name__ == "__main__":
    reset_test_environment()
