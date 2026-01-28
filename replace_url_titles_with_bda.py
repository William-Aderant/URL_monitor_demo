#!/usr/bin/env python3
"""
Replace all MonitoredURL.name values with BDA-extracted titles from their latest PDF version.

For each monitored URL, sets url.name to the latest version's display title
(formatted_title + form_number) when that version has a title. Use this to backfill
URL display names from existing BDA-extracted titles.

Usage:
  python replace_url_titles_with_bda.py           # dry run (no DB changes)
  python replace_url_titles_with_bda.py --apply   # apply changes to database
"""

import argparse
import sys
from typing import Optional

from sqlalchemy.orm import Session

from db.database import SessionLocal
from db.models import MonitoredURL, PDFVersion


def get_latest_version_with_title(db: Session, url_id: int) -> Optional[PDFVersion]:
    """Return the latest PDFVersion for url_id that has a formatted_title."""
    return (
        db.query(PDFVersion)
        .filter(PDFVersion.monitored_url_id == url_id, PDFVersion.formatted_title.isnot(None))
        .order_by(PDFVersion.version_number.desc())
        .first()
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Replace URL names with BDA-extracted titles from latest version")
    parser.add_argument("--apply", action="store_true", help="Apply changes to database (default: dry run)")
    args = parser.parse_args()
    dry_run = not args.apply

    db = SessionLocal()
    try:
        urls = db.query(MonitoredURL).order_by(MonitoredURL.id).all()
        updated = 0
        skipped_no_version = 0
        skipped_no_title = 0
        for url in urls:
            latest = get_latest_version_with_title(db, url.id)
            if not latest:
                skipped_no_version += 1
                continue
            display = latest.display_title
            if not display:
                skipped_no_title += 1
                continue
            new_name = display[:255]
            if url.name == new_name:
                continue
            if dry_run:
                print(f"  [dry run] url_id={url.id}  {url.name!r}  ->  {new_name!r}")
            else:
                url.name = new_name
            updated += 1

        if dry_run:
            print(f"\nDry run: would update {updated} URL(s). Skipped: {skipped_no_version} (no version with title), {skipped_no_title} (empty title).")
            print("Run with --apply to write changes.")
        else:
            db.commit()
            print(f"Updated {updated} URL name(s) from BDA titles.")
    finally:
        db.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
