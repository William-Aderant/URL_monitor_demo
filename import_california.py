#!/usr/bin/env python3
"""
Script to import California PDF links from ListOfLinks.xls into the URL monitor.

Filters for California-specific URLs and categorizes them by domain.
"""

import re
import structlog
from pathlib import Path
from urllib.parse import urlparse

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer()
    ]
)

logger = structlog.get_logger()

try:
    import xlrd
except ImportError:
    print("ERROR: xlrd is required. Install with: pip install xlrd")
    exit(1)

from config import settings
from db.database import SessionLocal
from db.migrations import run_migrations
from db.models import MonitoredURL


# California URL patterns
CALIFORNIA_PATTERNS = [
    'california',
    'ca.gov',
    'ca.uscourts.gov',
    'cacd.uscourts.gov',
    'caed.uscourts.gov', 
    'cand.uscourts.gov',
    'casd.uscourts.gov',
    'courts.ca.gov',
    '.ca.us'
]


def is_california_url(url: str) -> bool:
    """Check if a URL is California-related."""
    url_lower = url.lower()
    return any(pattern in url_lower for pattern in CALIFORNIA_PATTERNS)


def is_pdf_url(url: str) -> bool:
    """Check if URL is a direct PDF link."""
    return url.lower().endswith('.pdf')


def extract_domain_category(url: str) -> str:
    """
    Extract the domain category from a URL.
    
    Examples:
        https://www.courts.ca.gov/documents/cm010.pdf -> courts.ca.gov
        https://www.insurance.ca.gov/forms/upload/CDI-059.pdf -> insurance.ca.gov
    """
    try:
        parsed = urlparse(url)
        netloc = parsed.netloc.lower()
        
        # Remove www. prefix
        if netloc.startswith('www.'):
            netloc = netloc[4:]
        
        return netloc
    except Exception:
        return "unknown"


def extract_form_name(url: str) -> str:
    """
    Extract a form name/number from the URL path.
    
    Examples:
        https://www.courts.ca.gov/documents/cm010.pdf -> CM010
        https://www.insurance.ca.gov/forms/upload/CDI-059.pdf -> CDI-059
    """
    try:
        # Get filename from URL
        path = urlparse(url).path
        filename = path.split('/')[-1]
        
        # Remove .pdf extension
        if filename.lower().endswith('.pdf'):
            filename = filename[:-4]
        
        # Clean up common prefixes/suffixes
        filename = filename.replace('_', '-')
        
        # Convert to uppercase for consistency
        return filename.upper()
    except Exception:
        return "UNKNOWN"


def get_parent_page_url(url: str) -> str:
    """
    Get the parent page URL for crawling relocated forms.
    """
    try:
        parsed = urlparse(url)
        path = parsed.path.lower()
        
        # Look for common patterns
        # /documents/, /forms/, /upload/
        patterns = [
            r'(/documents/)',
            r'(/forms/)',
            r'(/upload/)',
            r'(/files/)',
            r'(/pdfs/)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, path, re.IGNORECASE)
            if match:
                return f"{parsed.scheme}://{parsed.netloc}{match.group(1)}"
        
        # Fallback: use immediate parent directory
        path_parts = parsed.path.split('/')
        if len(path_parts) > 1:
            parent_path = '/'.join(path_parts[:-1]) + '/'
            return f"{parsed.scheme}://{parsed.netloc}{parent_path}"
        
        return None
    except Exception:
        return None


def import_california_from_xls(filepath: str, db_session, batch_size: int = 500) -> dict:
    """
    Import California PDF URLs from XLS file.
    
    Args:
        filepath: Path to the XLS file
        db_session: Database session
        batch_size: Number of URLs to commit at once
        
    Returns:
        Dictionary with import statistics
    """
    path = Path(filepath)
    if not path.exists():
        logger.error("File not found", filepath=filepath)
        return {"error": f"File not found: {filepath}"}
    
    # Open XLS file
    wb = xlrd.open_workbook(filepath)
    sh = wb.sheet_by_index(0)
    
    stats = {
        "total_rows": sh.nrows - 1,  # Exclude header
        "california_links": 0,
        "pdf_links": 0,
        "added": 0,
        "skipped_existing": 0,
        "skipped_non_pdf": 0,
        "skipped_duplicate_in_file": 0,
        "domains": {}
    }
    
    logger.info(f"Processing {stats['total_rows']} rows from {filepath}")
    
    # Get all existing URLs upfront for faster checking
    existing_urls = set(
        url[0] for url in db_session.query(MonitoredURL.url).all()
    )
    logger.info(f"Found {len(existing_urls)} existing URLs in database")
    
    # Track URLs seen in this file (for deduplication)
    seen_in_file = set()
    
    # Track URLs to add (for batch insert)
    urls_to_add = []
    
    # Process each row (skip header row 0)
    for row_idx in range(1, sh.nrows):
        url = str(sh.cell_value(row_idx, 0)).strip()
        
        if not url:
            continue
        
        # Check if California URL
        if not is_california_url(url):
            continue
        
        stats["california_links"] += 1
        
        # Only import PDF links
        if not is_pdf_url(url):
            stats["skipped_non_pdf"] += 1
            continue
        
        stats["pdf_links"] += 1
        
        # Check if already exists in database
        if url in existing_urls:
            stats["skipped_existing"] += 1
            continue
        
        # Check if duplicate in this file
        if url in seen_in_file:
            stats["skipped_duplicate_in_file"] += 1
            continue
        
        seen_in_file.add(url)
        
        # Extract metadata
        domain_category = extract_domain_category(url)
        form_name = extract_form_name(url)
        parent_page_url = get_parent_page_url(url)
        
        # Track domain statistics
        if domain_category not in stats["domains"]:
            stats["domains"][domain_category] = 0
        stats["domains"][domain_category] += 1
        
        # Create new monitored URL
        monitored_url = MonitoredURL(
            name=f"California {form_name}",
            url=url,
            description=f"California form from {domain_category}",
            parent_page_url=parent_page_url,
            state="California",
            domain_category=domain_category
        )
        
        urls_to_add.append(monitored_url)
        stats["added"] += 1
        
        # Batch commit
        if len(urls_to_add) >= batch_size:
            db_session.add_all(urls_to_add)
            db_session.commit()
            logger.info(f"Committed batch of {len(urls_to_add)} URLs (total: {stats['added']})")
            urls_to_add = []
    
    # Commit remaining URLs
    if urls_to_add:
        db_session.add_all(urls_to_add)
        db_session.commit()
        logger.info(f"Committed final batch of {len(urls_to_add)} URLs")
    
    return stats


def main():
    settings.ensure_directories()
    run_migrations()
    
    db = SessionLocal()
    try:
        print("\n" + "="*60)
        print("  California PDF Links Import")
        print("="*60)
        
        # Import California links from XLS
        print("\nImporting California PDF links from ListOfLinks.xls...")
        stats = import_california_from_xls("ListOfLinks.xls", db)
        
        if "error" in stats:
            print(f"\nERROR: {stats['error']}")
            return
        
        # Print statistics
        print(f"\n{'='*60}")
        print("  Import Complete")
        print("="*60)
        print(f"\nTotal rows in XLS:       {stats['total_rows']:,}")
        print(f"California links found:  {stats['california_links']:,}")
        print(f"PDF links (filtered):    {stats['pdf_links']:,}")
        print(f"Skipped (non-PDF):       {stats['skipped_non_pdf']:,}")
        print(f"Skipped (already exist): {stats['skipped_existing']:,}")
        print(f"Skipped (duplicates):    {stats.get('skipped_duplicate_in_file', 0):,}")
        print(f"New URLs added:          {stats['added']:,}")
        
        # Print top domains
        print(f"\n{'='*60}")
        print("  Top California Domains")
        print("="*60)
        sorted_domains = sorted(stats["domains"].items(), key=lambda x: -x[1])
        for domain, count in sorted_domains[:15]:
            print(f"  {domain:40} {count:,}")
        if len(sorted_domains) > 15:
            print(f"  ... and {len(sorted_domains) - 15} more domains")
        
        # Show total count
        total = db.query(MonitoredURL).count()
        ca_count = db.query(MonitoredURL).filter_by(state="California").count()
        ak_count = db.query(MonitoredURL).filter_by(state="Alaska").count()
        
        print(f"\n{'='*60}")
        print("  Database Summary")
        print("="*60)
        print(f"  Total monitored URLs:  {total:,}")
        print(f"  California:            {ca_count:,}")
        print(f"  Alaska:                {ak_count:,}")
        print(f"  Other:                 {total - ca_count - ak_count:,}")
        
    finally:
        db.close()


if __name__ == "__main__":
    main()
