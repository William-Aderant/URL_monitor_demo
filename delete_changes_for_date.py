#!/usr/bin/env python3
"""
Completely reset changes for a given date: delete change logs and the pdf_versions
(and their files) that were created on that date. Use after AWS/ingestion issues
so the next monitoring run compares against the prior baseline.

Usage:
    python delete_changes_for_date.py 2026-01-27
"""

import sys
from pathlib import Path
from datetime import datetime, date, timedelta

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from sqlalchemy import and_
from sqlalchemy.orm import Session

from db.database import SessionLocal
from db.models import MonitoredURL, ChangeLog, PDFVersion
from storage.file_store import FileStore


def reset_changes_for_date(target_date: date) -> dict:
    """
    Completely reset changes for target_date:
    1. Delete all change_logs where detected_at is on target_date.
    2. Delete all pdf_versions where created_at is on target_date (and their on-disk files).
    3. Update last_change_at on affected URLs to their most recent remaining change, or None.

    Using created_at for versions ensures we remove everything from that date even if
    change logs were already deleted earlier.

    Returns dict with change_count, version_count.
    """
    db: Session = SessionLocal()
    file_store = FileStore()
    start = datetime.combine(target_date, datetime.min.time())
    end = start + timedelta(days=1)

    # 1. Delete change logs for that date
    to_delete = (
        db.query(ChangeLog)
        .filter(ChangeLog.detected_at >= start, ChangeLog.detected_at < end)
        .all()
    )
    change_count = len(to_delete)
    affected_url_ids = {c.monitored_url_id for c in to_delete}

    for c in to_delete:
        db.delete(c)
    db.flush()

    # 2. Delete pdf_versions created on that date that are not referenced by any
    #    remaining change_log (as previous_version_id), so we avoid FK violations.
    version_ids_referenced = {
        rid for (rid,) in db.query(ChangeLog.previous_version_id).filter(
            ChangeLog.previous_version_id.isnot(None)
        ).distinct().all()
    }
    conditions = [
        PDFVersion.created_at >= start,
        PDFVersion.created_at < end,
    ]
    if version_ids_referenced:
        conditions.append(~PDFVersion.id.in_(version_ids_referenced))
    versions_on_date = db.query(PDFVersion).filter(and_(*conditions)).all()
    version_count = 0
    for v in versions_on_date:
        file_store.delete_version(v.monitored_url_id, v.id)
        db.delete(v)
        version_count += 1
        affected_url_ids.add(v.monitored_url_id)

    # 3. Update last_change_at for affected URLs
    for url_id in affected_url_ids:
        latest = (
            db.query(ChangeLog.detected_at)
            .filter(ChangeLog.monitored_url_id == url_id)
            .order_by(ChangeLog.detected_at.desc())
            .limit(1)
            .scalar()
        )
        url = db.query(MonitoredURL).filter(MonitoredURL.id == url_id).first()
        if url:
            url.last_change_at = latest

    db.commit()
    db.close()
    return {"change_count": change_count, "version_count": version_count}


if __name__ == "__main__":
    d = date(2026, 1, 27)
    if len(sys.argv) > 1:
        try:
            d = datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
        except ValueError:
            print("Usage: python delete_changes_for_date.py YYYY-MM-DD")
            sys.exit(1)
    result = reset_changes_for_date(d)
    print(
        f"Reset complete for {d.isoformat()}: "
        f"deleted {result['change_count']} change log(s) and {result['version_count']} version(s) (and their files)."
    )
