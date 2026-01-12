#!/usr/bin/env python3
"""
Reset Test Environment

Resets the test monitoring environment to a clean state:
- Clears all versions and changes for test forms (IDs 2, 3, 4)
- Restores original URLs
- Reverts test site PDFs to baseline
- Keeps Alaska forms disabled

Usage:
    python reset_test.py
"""

import shutil
from pathlib import Path

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


def reset_test_environment():
    """Reset all test forms to clean state."""
    print("\n" + "=" * 50)
    print("🔄 Resetting Test Environment")
    print("=" * 50)
    
    db = SessionLocal()
    
    try:
        # Test form IDs
        test_ids = [2, 3, 4]
        
        # Original URLs for test forms
        original_urls = {
            2: "http://localhost:5001/pdfs/civ-001.pdf",
            3: "http://localhost:5001/pdfs/civ-002.pdf",
            4: "http://localhost:5001/pdfs/civ-003.pdf",
        }
        
        for url_id in test_ids:
            # Delete change logs
            changes = db.query(ChangeLog).filter(ChangeLog.monitored_url_id == url_id).delete()
            
            # Delete versions
            versions = db.query(PDFVersion).filter(PDFVersion.monitored_url_id == url_id).delete()
            
            # Reset URL timestamps and restore original URL
            url = db.query(MonitoredURL).filter(MonitoredURL.id == url_id).first()
            if url:
                url.last_checked_at = None
                url.last_change_at = None
                url.url = original_urls.get(url_id, url.url)
                print(f"  ✓ Reset {url.name}")
                print(f"    - Deleted {versions} versions, {changes} changes")
                print(f"    - URL: {url.url}")
        
        db.commit()
        
        # Clean up data files
        data_path = Path("data/pdfs")
        for url_id in test_ids:
            version_dir = data_path / str(url_id)
            if version_dir.exists():
                shutil.rmtree(version_dir)
                print(f"  ✓ Cleaned data/pdfs/{url_id}/")
        
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
