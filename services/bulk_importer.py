"""
Bulk import service for CSV/TXT file uploads.
Validates URLs, extracts titles, and creates MonitoredURL records.
"""

import csv
import io
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Dict, Any
from urllib.parse import urlparse

import structlog
import httpx

from config import settings
from db.models import MonitoredURL

logger = structlog.get_logger()


@dataclass
class ImportResult:
    """Result of a single URL import."""
    url: str
    success: bool
    error: Optional[str] = None
    monitored_url_id: Optional[int] = None
    title: Optional[str] = None
    state: Optional[str] = None
    jurisdiction: Optional[str] = None
    is_duplicate: bool = False
    row_number: Optional[int] = None


@dataclass
class BulkImportResult:
    """Result of a bulk import operation."""
    success: bool
    batch_id: str
    total_rows: int = 0
    successful: int = 0
    failed: int = 0
    duplicates: int = 0
    results: List[ImportResult] = field(default_factory=list)
    error: Optional[str] = None


# CSV/TXT format template
CSV_TEMPLATE = """URL,Title,State,Jurisdiction
https://example.com/form1.pdf,Optional Form Title,California,courts.ca.gov
https://example.com/form2.pdf,,Alaska,courts.alaska.gov
"""

CSV_FORMAT_GUIDE = """
# Bulk Upload Format Guide

## CSV Format
- First row must be header: URL,Title,State,Jurisdiction
- URL is required and must be a valid URL (should end in .pdf)
- Title is optional - if blank, will be auto-extracted when monitored
- State is required (e.g., California, Alaska, Texas)
- Jurisdiction is required (e.g., courts.ca.gov, courts.alaska.gov)

Example:
```
URL,Title,State,Jurisdiction
https://www.courts.ca.gov/documents/cm010.pdf,Civil Case Cover Sheet,California,courts.ca.gov
https://www.courts.alaska.gov/forms/civ-100.pdf,,Alaska,courts.alaska.gov
```

## TXT Format
- One URL per line, with optional tab or comma-separated fields
- Format: URL[,Title][,State][,Jurisdiction]
- State and Jurisdiction are required for TXT format too

Example:
```
https://www.courts.ca.gov/documents/cm010.pdf,Civil Case Cover Sheet,California,courts.ca.gov
https://www.courts.alaska.gov/forms/civ-100.pdf,,Alaska,courts.alaska.gov
```

## Validation Rules
- URLs must be valid HTTP/HTTPS URLs
- PDF URLs (ending in .pdf) are recommended but not required
- Duplicate URLs (already in system) will be skipped
- Maximum file size: {max_size_mb}MB
- Maximum URLs per upload: {max_urls}
""".format(
    max_size_mb=settings.BULK_UPLOAD_MAX_SIZE_MB,
    max_urls=settings.BULK_UPLOAD_MAX_URLS
)


class BulkImporter:
    """Service for bulk importing URLs from CSV/TXT files."""
    
    def __init__(self):
        self.max_file_size = settings.BULK_UPLOAD_MAX_SIZE_MB * 1024 * 1024  # Convert to bytes
        self.max_urls = settings.BULK_UPLOAD_MAX_URLS
        self.validate_urls = settings.BULK_UPLOAD_VALIDATE_URLS
    
    def get_format_guide(self) -> str:
        """Get the format guide for bulk uploads."""
        return CSV_FORMAT_GUIDE
    
    def get_csv_template(self) -> str:
        """Get a CSV template for bulk uploads."""
        return CSV_TEMPLATE
    
    def validate_url_format(self, url: str) -> tuple[bool, Optional[str]]:
        """
        Validate URL format.
        
        Returns:
            Tuple of (is_valid, error_message)
        """
        if not url or not url.strip():
            return False, "URL is empty"
        
        url = url.strip()
        
        # Check URL format
        try:
            parsed = urlparse(url)
            if not parsed.scheme:
                return False, "URL must include scheme (http:// or https://)"
            if parsed.scheme not in ('http', 'https'):
                return False, f"Invalid URL scheme: {parsed.scheme}"
            if not parsed.netloc:
                return False, "URL must include domain"
        except Exception as e:
            return False, f"Invalid URL format: {str(e)}"
        
        # Warn if not a PDF (but still valid)
        if not url.lower().endswith('.pdf'):
            logger.debug("URL does not end with .pdf", url=url)
        
        return True, None
    
    def validate_url_accessible(self, url: str) -> tuple[bool, Optional[str]]:
        """
        Check if URL is accessible via HEAD request.
        
        Returns:
            Tuple of (is_accessible, error_message)
        """
        if not self.validate_urls:
            return True, None
        
        try:
            with httpx.Client(timeout=10.0, follow_redirects=True) as client:
                response = client.head(url)
                if response.status_code == 200:
                    return True, None
                elif response.status_code == 405:
                    # HEAD not allowed, try GET with stream
                    response = client.get(url, headers={"Range": "bytes=0-0"})
                    if response.status_code in (200, 206):
                        return True, None
                return False, f"HTTP {response.status_code}"
        except httpx.TimeoutException:
            return False, "Timeout"
        except httpx.RequestError as e:
            return False, str(e)
        except Exception as e:
            return False, str(e)
    
    def extract_domain_category(self, url: str) -> Optional[str]:
        """Extract domain category from URL."""
        try:
            parsed = urlparse(url)
            return parsed.netloc
        except Exception:
            return None
    
    def generate_name_from_url(self, url: str) -> str:
        """Generate a name from URL if title not provided."""
        try:
            parsed = urlparse(url)
            # Get filename from path
            path = parsed.path
            filename = path.split('/')[-1]
            if filename:
                # Remove extension and clean up
                name = filename.rsplit('.', 1)[0]
                # Replace underscores/hyphens with spaces
                name = re.sub(r'[_-]+', ' ', name)
                # Title case
                name = name.title()
                return name
        except Exception:
            pass
        return "Untitled URL"
    
    def parse_csv_content(self, content: str) -> List[Dict[str, Any]]:
        """Parse CSV content and return list of row dictionaries."""
        rows = []
        
        # Use csv reader
        reader = csv.DictReader(io.StringIO(content))
        
        # Normalize header names
        if reader.fieldnames:
            # Map common variations to standard names
            header_map = {}
            for field in reader.fieldnames:
                lower = field.lower().strip()
                if lower in ('url', 'link', 'pdf_url', 'pdf_link'):
                    header_map[field] = 'url'
                elif lower in ('title', 'name', 'form_title', 'form_name'):
                    header_map[field] = 'title'
                elif lower in ('state', 'state_name'):
                    header_map[field] = 'state'
                elif lower in ('jurisdiction', 'domain', 'domain_category', 'category'):
                    header_map[field] = 'jurisdiction'
                elif lower in ('enabled', 'active'):
                    header_map[field] = 'enabled'
                else:
                    header_map[field] = field
        
        for i, row in enumerate(reader, start=2):  # Start at 2 to account for header row
            # Normalize keys
            normalized = {}
            for key, value in row.items():
                if key and value:
                    normalized_key = header_map.get(key, key.lower().strip())
                    normalized[normalized_key] = value.strip()
                elif key and not value:
                    normalized_key = header_map.get(key, key.lower().strip())
                    normalized[normalized_key] = ''
            
            normalized['row_number'] = i
            rows.append(normalized)
        
        return rows
    
    def parse_txt_content(self, content: str) -> List[Dict[str, Any]]:
        """Parse TXT content (one URL per line) and return list of row dictionaries."""
        rows = []
        
        for i, line in enumerate(content.strip().split('\n'), start=1):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            
            # Try to split by tab first, then comma
            if '\t' in line:
                parts = line.split('\t')
            else:
                parts = line.split(',')
            
            row = {
                'url': parts[0].strip() if len(parts) > 0 else '',
                'title': parts[1].strip() if len(parts) > 1 else '',
                'state': parts[2].strip() if len(parts) > 2 else '',
                'jurisdiction': parts[3].strip() if len(parts) > 3 else '',
                'row_number': i
            }
            rows.append(row)
        
        return rows
    
    def import_from_content(
        self, 
        content: str, 
        file_type: str,
        db,
        source: str = "upload"
    ) -> BulkImportResult:
        """
        Import URLs from file content.
        
        Args:
            content: File content as string
            file_type: Either 'csv' or 'txt'
            db: Database session
            source: Import source identifier
            
        Returns:
            BulkImportResult with details of the import
        """
        batch_id = str(uuid.uuid4())[:8]
        result = BulkImportResult(
            success=True,
            batch_id=batch_id
        )
        
        # Check file size
        if len(content.encode('utf-8')) > self.max_file_size:
            result.success = False
            result.error = f"File too large. Maximum size is {settings.BULK_UPLOAD_MAX_SIZE_MB}MB"
            return result
        
        # Parse content based on file type
        try:
            if file_type.lower() == 'csv':
                rows = self.parse_csv_content(content)
            else:
                rows = self.parse_txt_content(content)
        except Exception as e:
            result.success = False
            result.error = f"Failed to parse file: {str(e)}"
            return result
        
        # Check URL count
        if len(rows) > self.max_urls:
            result.success = False
            result.error = f"Too many URLs. Maximum is {self.max_urls} per upload"
            return result
        
        result.total_rows = len(rows)
        
        # Process each row
        for row in rows:
            import_result = self._process_row(row, db, batch_id, source)
            result.results.append(import_result)
            
            if import_result.success:
                result.successful += 1
            elif import_result.is_duplicate:
                result.duplicates += 1
            else:
                result.failed += 1
        
        # Commit all changes
        try:
            db.commit()
        except Exception as e:
            db.rollback()
            result.success = False
            result.error = f"Database error: {str(e)}"
            return result
        
        logger.info(
            "Bulk import completed",
            batch_id=batch_id,
            total=result.total_rows,
            successful=result.successful,
            failed=result.failed,
            duplicates=result.duplicates
        )
        
        return result
    
    def _process_row(
        self, 
        row: Dict[str, Any], 
        db, 
        batch_id: str,
        source: str
    ) -> ImportResult:
        """Process a single row and create MonitoredURL if valid."""
        url = row.get('url', '').strip()
        title = row.get('title', '').strip()
        state = row.get('state', '').strip()
        jurisdiction = row.get('jurisdiction', '').strip()
        row_number = row.get('row_number')
        
        # Validate URL format
        is_valid, error = self.validate_url_format(url)
        if not is_valid:
            return ImportResult(
                url=url,
                success=False,
                error=error,
                row_number=row_number
            )
        
        # Validate required fields
        if not state:
            return ImportResult(
                url=url,
                success=False,
                error="State is required",
                row_number=row_number
            )
        
        if not jurisdiction:
            return ImportResult(
                url=url,
                success=False,
                error="Jurisdiction is required",
                row_number=row_number
            )
        
        # Check for duplicates
        existing = db.query(MonitoredURL).filter(MonitoredURL.url == url).first()
        if existing:
            return ImportResult(
                url=url,
                success=False,
                error=f"URL already exists (ID: {existing.id})",
                is_duplicate=True,
                monitored_url_id=existing.id,
                row_number=row_number
            )
        
        # Optionally validate URL accessibility
        if self.validate_urls:
            is_accessible, access_error = self.validate_url_accessible(url)
            if not is_accessible:
                return ImportResult(
                    url=url,
                    success=False,
                    error=f"URL not accessible: {access_error}",
                    row_number=row_number
                )
        
        # Generate name from URL if title not provided
        name = title if title else self.generate_name_from_url(url)
        
        # Extract domain category if jurisdiction not specified properly
        if not jurisdiction:
            jurisdiction = self.extract_domain_category(url)
        
        # Create MonitoredURL
        try:
            monitored_url = MonitoredURL(
                name=name,
                url=url,
                state=state,
                domain_category=jurisdiction,
                enabled=True,
                import_batch_id=batch_id,
                import_source=source,
                imported_at=datetime.utcnow()
            )
            db.add(monitored_url)
            db.flush()  # Get ID without committing
            
            return ImportResult(
                url=url,
                success=True,
                monitored_url_id=monitored_url.id,
                title=name,
                state=state,
                jurisdiction=jurisdiction,
                row_number=row_number
            )
            
        except Exception as e:
            return ImportResult(
                url=url,
                success=False,
                error=f"Failed to create URL: {str(e)}",
                row_number=row_number
            )
    
    def import_from_file(
        self,
        file_path: str,
        db,
        source: str = "file"
    ) -> BulkImportResult:
        """
        Import URLs from a file path.
        
        Args:
            file_path: Path to CSV or TXT file
            db: Database session
            source: Import source identifier
            
        Returns:
            BulkImportResult with details of the import
        """
        # Determine file type from extension
        if file_path.lower().endswith('.csv'):
            file_type = 'csv'
        elif file_path.lower().endswith('.txt'):
            file_type = 'txt'
        else:
            return BulkImportResult(
                success=False,
                batch_id="",
                error="Unsupported file type. Use .csv or .txt"
            )
        
        # Read file content
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception as e:
            return BulkImportResult(
                success=False,
                batch_id="",
                error=f"Failed to read file: {str(e)}"
            )
        
        return self.import_from_content(content, file_type, db, source)


# Global instance
bulk_importer = BulkImporter()
