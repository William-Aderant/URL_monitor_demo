"""
HTTP Header Checker for Fast Change Detection

Performs HEAD requests to check if PDFs have changed without downloading the full file.
Uses standard HTTP headers: Last-Modified, ETag, Content-Length.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from email.utils import parsedate_to_datetime
import httpx
import structlog

logger = structlog.get_logger()


@dataclass
class HeaderCheckResult:
    """Result of HTTP header check."""
    success: bool
    url: str
    
    # Extracted headers
    last_modified: Optional[datetime] = None
    etag: Optional[str] = None
    content_length: Optional[int] = None
    
    # Comparison results (if previous values provided)
    last_modified_changed: Optional[bool] = None
    etag_changed: Optional[bool] = None
    content_length_changed: Optional[bool] = None
    
    # Overall change detection
    headers_available: bool = False
    likely_changed: Optional[bool] = None  # True if any header suggests change
    
    error: Optional[str] = None
    status_code: Optional[int] = None


class HeaderChecker:
    """
    Checks HTTP headers to detect if a PDF has changed.
    
    Performs HEAD requests (doesn't download body) to check:
    - Last-Modified: Timestamp when file was last changed
    - ETag: Unique identifier that changes with content
    - Content-Length: File size in bytes
    """
    
    DEFAULT_TIMEOUT = 10.0  # seconds (faster than full download)
    DEFAULT_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "application/pdf,*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    
    def __init__(self, timeout: float = DEFAULT_TIMEOUT):
        """
        Initialize header checker.
        
        Args:
            timeout: Request timeout in seconds
        """
        self.timeout = timeout
        logger.info("HeaderChecker initialized", timeout=timeout)
    
    def check_headers(
        self,
        url: str,
        previous_last_modified: Optional[datetime] = None,
        previous_etag: Optional[str] = None,
        previous_content_length: Optional[int] = None
    ) -> HeaderCheckResult:
        """
        Check HTTP headers for a URL and compare with previous values.
        
        Args:
            url: URL to check
            previous_last_modified: Last-Modified from previous check
            previous_etag: ETag from previous check
            previous_content_length: Content-Length from previous check
            
        Returns:
            HeaderCheckResult with extracted headers and comparison results
        """
        logger.info("Checking HTTP headers", url=url)
        
        try:
            with httpx.Client(
                timeout=self.timeout,
                follow_redirects=True,
                headers=self.DEFAULT_HEADERS
            ) as client:
                # Try HEAD request first (doesn't download body)
                try:
                    response = client.head(url)
                    response.raise_for_status()
                except httpx.HTTPStatusError as e:
                    # Some servers don't support HEAD, fall back to GET with Range header
                    if e.response.status_code == 405:  # Method Not Allowed
                        logger.debug("HEAD not supported, trying GET with Range header")
                        response = client.get(
                            url,
                            headers={**self.DEFAULT_HEADERS, "Range": "bytes=0-0"}
                        )
                        response.raise_for_status()
                    else:
                        raise
                
                # Extract headers
                result = self._extract_headers(url, response)
                
                # Compare with previous values if provided
                if previous_last_modified or previous_etag or previous_content_length:
                    result = self._compare_headers(
                        result,
                        previous_last_modified,
                        previous_etag,
                        previous_content_length
                    )
                
                logger.info(
                    "Header check complete",
                    url=url,
                    headers_available=result.headers_available,
                    likely_changed=result.likely_changed
                )
                
                return result
                
        except httpx.TimeoutException as e:
            logger.warning("Header check timeout", url=url, error=str(e))
            return HeaderCheckResult(
                success=False,
                url=url,
                error=f"Timeout: {str(e)}"
            )
        except httpx.HTTPStatusError as e:
            logger.warning(
                "Header check HTTP error",
                url=url,
                status=e.response.status_code
            )
            return HeaderCheckResult(
                success=False,
                url=url,
                error=f"HTTP {e.response.status_code}",
                status_code=e.response.status_code
            )
        except Exception as e:
            logger.warning("Header check failed", url=url, error=str(e))
            return HeaderCheckResult(
                success=False,
                url=url,
                error=str(e)
            )
    
    def _extract_headers(
        self,
        url: str,
        response: httpx.Response
    ) -> HeaderCheckResult:
        """
        Extract relevant headers from HTTP response.
        
        Args:
            url: URL that was checked
            response: HTTP response object
            
        Returns:
            HeaderCheckResult with extracted headers
        """
        result = HeaderCheckResult(
            success=True,
            url=url,
            status_code=response.status_code
        )
        
        # Extract Last-Modified
        last_modified_str = response.headers.get("Last-Modified")
        if last_modified_str:
            try:
                result.last_modified = parsedate_to_datetime(last_modified_str)
                result.headers_available = True
            except (ValueError, TypeError) as e:
                logger.debug(
                    "Failed to parse Last-Modified",
                    header=last_modified_str,
                    error=str(e)
                )
        
        # Extract ETag (remove quotes if present)
        etag = response.headers.get("ETag")
        if etag:
            result.etag = etag.strip('"')
            result.headers_available = True
        
        # Extract Content-Length
        content_length_str = response.headers.get("Content-Length")
        if content_length_str:
            try:
                result.content_length = int(content_length_str)
                result.headers_available = True
            except ValueError:
                logger.debug(
                    "Failed to parse Content-Length",
                    header=content_length_str
                )
        
        return result
    
    def _compare_headers(
        self,
        result: HeaderCheckResult,
        previous_last_modified: Optional[datetime],
        previous_etag: Optional[str],
        previous_content_length: Optional[int]
    ) -> HeaderCheckResult:
        """
        Compare current headers with previous values to detect changes.
        
        Prioritizes ETag > Content-Length > Last-Modified for reliability.
        Ignores Last-Modified changes when ETag is stable (servers often set
        Last-Modified to request time rather than actual file modification time).
        
        Args:
            result: HeaderCheckResult with current headers
            previous_last_modified: Previous Last-Modified value
            previous_etag: Previous ETag value
            previous_content_length: Previous Content-Length value
            
        Returns:
            Updated HeaderCheckResult with comparison results
        """
        changes_detected = []
        
        # Priority 1: ETag (most reliable - changes only when content changes)
        if result.etag and previous_etag:
            result.etag_changed = (result.etag != previous_etag)
            if result.etag_changed:
                changes_detected.append("ETag")
        
        # Priority 2: Content-Length (reliable - changes when file size changes)
        if result.content_length is not None and previous_content_length is not None:
            result.content_length_changed = (
                result.content_length != previous_content_length
            )
            if result.content_length_changed:
                changes_detected.append("Content-Length")
        
        # Priority 3: Last-Modified (least reliable - often set to request time)
        # Only use if ETag is not available, or ignore if ETag/Content-Length are stable
        if result.last_modified and previous_last_modified:
            result.last_modified_changed = (
                result.last_modified != previous_last_modified
            )
            
            # If ETag exists and hasn't changed, ignore Last-Modified changes
            # (Last-Modified is likely being set to request time, not actual file time)
            if result.etag and previous_etag and not result.etag_changed:
                # ETag is stable, so Last-Modified change is unreliable - ignore it
                result.last_modified_changed = False
                logger.debug(
                    "Ignoring Last-Modified change (ETag unchanged, likely server sets Last-Modified to request time)",
                    last_modified_current=result.last_modified.isoformat(),
                    last_modified_previous=previous_last_modified.isoformat()
                )
            elif result.last_modified_changed:
                # Only consider Last-Modified if ETag is not available
                if not result.etag or not previous_etag:
                    changes_detected.append("Last-Modified")
                # If ETag is available, we already handled it above
        
        # Determine if change is likely
        # If ETag or Content-Length changed, definitely changed
        # If only Last-Modified changed but ETag/Content-Length are stable, likely unchanged
        if changes_detected:
            result.likely_changed = True
            logger.debug(
                "Headers indicate change",
                changed_headers=changes_detected
            )
        elif result.headers_available and not changes_detected:
            # All available headers match (or Last-Modified changed but was ignored)
            result.likely_changed = False
            logger.debug("All reliable headers match - likely unchanged")
        else:
            # No headers available or no previous values to compare
            result.likely_changed = None
            logger.debug("Cannot determine change from headers")
        
        return result
    
    def can_skip_download(
        self,
        result: HeaderCheckResult
    ) -> bool:
        """
        Determine if we can skip downloading based on header check.
        
        Only skips if ETag or Content-Length match (ignores Last-Modified).
        
        Args:
            result: HeaderCheckResult from check_headers()
            
        Returns:
            True if we can confidently skip download (reliable headers match)
        """
        if not result.success:
            return False  # Can't skip if check failed
        
        if result.likely_changed is False:
            # All reliable headers match - high confidence no change
            return True
        
        if result.likely_changed is True:
            # Headers indicate change - must download
            return False
        
        # Unknown - need to check further (quick hash)
        # Also skip if only Last-Modified changed but ETag/Content-Length are stable
        if (result.etag and result.etag_changed is False and 
            result.content_length is not None and result.content_length_changed is False):
            # ETag and Content-Length are stable, safe to skip even if Last-Modified changed
            logger.debug("ETag and Content-Length stable - skipping despite Last-Modified change")
            return True
        
        return False
