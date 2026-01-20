"""
Quick Hash Checker for Fast Change Detection

Downloads only the first portion of a PDF (e.g., 64KB) and computes a hash.
This provides a fast way to detect changes without downloading the entire file.
"""

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import httpx
import structlog

logger = structlog.get_logger()


@dataclass
class QuickHashResult:
    """Result of quick hash computation."""
    success: bool
    url: str
    quick_hash: Optional[str] = None  # SHA-256 of first N bytes
    bytes_downloaded: int = 0
    content_length: Optional[int] = None
    error: Optional[str] = None


class QuickHasher:
    """
    Computes hash of first portion of PDF for fast change detection.
    
    Downloads only the first N bytes (default 64KB) and computes SHA-256 hash.
    This is much faster than downloading the entire PDF and provides high
    confidence change detection for most cases.
    """
    
    DEFAULT_CHUNK_SIZE = 65536  # 64KB - enough to catch most changes
    DEFAULT_TIMEOUT = 10.0  # seconds
    DEFAULT_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "application/pdf,*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    
    def __init__(
        self,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        timeout: float = DEFAULT_TIMEOUT
    ):
        """
        Initialize quick hasher.
        
        Args:
            chunk_size: Number of bytes to download and hash (default 64KB)
            timeout: Request timeout in seconds
        """
        self.chunk_size = chunk_size
        self.timeout = timeout
        logger.info(
            "QuickHasher initialized",
            chunk_size=chunk_size,
            timeout=timeout
        )
    
    def compute_quick_hash(self, url: str) -> QuickHashResult:
        """
        Download first portion of PDF and compute hash.
        
        Args:
            url: URL to check
            
        Returns:
            QuickHashResult with hash and metadata
        """
        logger.info(
            "Computing quick hash",
            url=url,
            chunk_size=self.chunk_size
        )
        
        try:
            with httpx.Client(
                timeout=self.timeout,
                follow_redirects=True,
                headers=self.DEFAULT_HEADERS
            ) as client:
                # Use Range header to download only first chunk
                range_header = f"bytes=0-{self.chunk_size - 1}"
                
                with client.stream(
                    "GET",
                    url,
                    headers={**self.DEFAULT_HEADERS, "Range": range_header}
                ) as response:
                    response.raise_for_status()
                    
                    # Get content length from response
                    content_length = response.headers.get("Content-Length")
                    total_size = int(content_length) if content_length else None
                    
                    # Compute hash while streaming
                    sha256 = hashlib.sha256()
                    bytes_downloaded = 0
                    
                    for chunk in response.iter_bytes(chunk_size=8192):
                        sha256.update(chunk)
                        bytes_downloaded += len(chunk)
                        
                        # Stop if we've downloaded enough
                        if bytes_downloaded >= self.chunk_size:
                            break
                    
                    quick_hash = sha256.hexdigest()
                    
                    logger.info(
                        "Quick hash computed",
                        url=url,
                        hash=quick_hash[:16] + "...",
                        bytes_downloaded=bytes_downloaded
                    )
                    
                    return QuickHashResult(
                        success=True,
                        url=url,
                        quick_hash=quick_hash,
                        bytes_downloaded=bytes_downloaded,
                        content_length=total_size
                    )
                    
        except httpx.TimeoutException as e:
            logger.warning("Quick hash timeout", url=url, error=str(e))
            return QuickHashResult(
                success=False,
                url=url,
                error=f"Timeout: {str(e)}"
            )
        except httpx.HTTPStatusError as e:
            # Some servers don't support Range requests (206 Partial Content)
            # Fall back to downloading full file (but we'll stop early)
            if e.response.status_code == 416:  # Range Not Satisfiable
                logger.debug("Range request not supported, downloading full file")
                return self._compute_hash_full_download(url)
            else:
                logger.warning(
                    "Quick hash HTTP error",
                    url=url,
                    status=e.response.status_code
                )
                return QuickHashResult(
                    success=False,
                    url=url,
                    error=f"HTTP {e.response.status_code}",
                )
        except Exception as e:
            logger.warning("Quick hash failed", url=url, error=str(e))
            return QuickHashResult(
                success=False,
                url=url,
                error=str(e)
            )
    
    def _compute_hash_full_download(self, url: str) -> QuickHashResult:
        """
        Fallback: Download full file but only hash first chunk.
        
        Used when server doesn't support Range requests.
        
        Args:
            url: URL to download
            
        Returns:
            QuickHashResult with hash
        """
        logger.debug("Using full download fallback for quick hash")
        
        try:
            with httpx.Client(
                timeout=self.timeout,
                follow_redirects=True,
                headers=self.DEFAULT_HEADERS
            ) as client:
                with client.stream("GET", url) as response:
                    response.raise_for_status()
                    
                    sha256 = hashlib.sha256()
                    bytes_downloaded = 0
                    
                    for chunk in response.iter_bytes(chunk_size=8192):
                        sha256.update(chunk)
                        bytes_downloaded += len(chunk)
                        
                        # Stop after first chunk
                        if bytes_downloaded >= self.chunk_size:
                            break
                    
                    quick_hash = sha256.hexdigest()
                    
                    return QuickHashResult(
                        success=True,
                        url=url,
                        quick_hash=quick_hash,
                        bytes_downloaded=bytes_downloaded
                    )
        except Exception as e:
            return QuickHashResult(
                success=False,
                url=url,
                error=str(e)
            )
    
    def compare_quick_hash(
        self,
        current_hash: str,
        previous_hash: Optional[str]
    ) -> bool:
        """
        Compare quick hashes to detect changes.
        
        Args:
            current_hash: Current quick hash
            previous_hash: Previous quick hash (None if first check)
            
        Returns:
            True if hashes match (likely unchanged), False if differ (likely changed)
        """
        if previous_hash is None:
            # First check - no comparison possible
            return False
        
        match = current_hash == previous_hash
        
        logger.debug(
            "Quick hash comparison",
            match=match,
            current=current_hash[:16] + "...",
            previous=previous_hash[:16] + "..." if previous_hash else None
        )
        
        return match
