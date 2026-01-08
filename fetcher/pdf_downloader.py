"""
PDF download handler with retry logic and streaming support.
"""

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import httpx
import structlog

logger = structlog.get_logger()


@dataclass
class DownloadResult:
    """Result of a PDF download operation."""
    success: bool
    url: str
    file_path: Optional[Path] = None
    file_size: Optional[int] = None
    content_type: Optional[str] = None
    error: Optional[str] = None
    retries_used: int = 0


class PDFDownloader:
    """
    Downloads PDFs from URLs with retry logic and streaming support.
    """
    
    DEFAULT_TIMEOUT = 60.0  # seconds
    DEFAULT_MAX_RETRIES = 3
    RETRY_DELAY = 2.0  # seconds
    
    # Headers to mimic a browser request
    DEFAULT_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/pdf,*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    
    def __init__(
        self,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        headers: Optional[dict] = None
    ):
        """
        Initialize PDF downloader.
        
        Args:
            timeout: Request timeout in seconds
            max_retries: Maximum number of retry attempts
            headers: Custom headers to use
        """
        self.timeout = timeout
        self.max_retries = max_retries
        self.headers = headers or self.DEFAULT_HEADERS.copy()
        
        logger.info(
            "PDFDownloader initialized",
            timeout=timeout,
            max_retries=max_retries
        )
    
    def download(self, url: str, output_path: Path) -> DownloadResult:
        """
        Download a PDF from a URL to a local file.
        
        Args:
            url: URL to download from
            output_path: Path to save the downloaded file
            
        Returns:
            DownloadResult with download details
        """
        logger.info("Starting PDF download", url=url, output_path=str(output_path))
        
        # Ensure output directory exists
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        last_error = None
        retries_used = 0
        
        for attempt in range(self.max_retries + 1):
            try:
                result = self._download_attempt(url, output_path)
                result.retries_used = retries_used
                return result
                
            except httpx.TimeoutException as e:
                last_error = f"Timeout: {str(e)}"
                logger.warning(
                    "Download timeout",
                    url=url,
                    attempt=attempt + 1,
                    max_retries=self.max_retries
                )
                
            except httpx.HTTPStatusError as e:
                last_error = f"HTTP {e.response.status_code}: {str(e)}"
                # Don't retry on client errors (4xx)
                if 400 <= e.response.status_code < 500:
                    logger.error("Client error, not retrying", url=url, status=e.response.status_code)
                    break
                logger.warning(
                    "HTTP error",
                    url=url,
                    status=e.response.status_code,
                    attempt=attempt + 1
                )
                
            except Exception as e:
                last_error = str(e)
                logger.warning(
                    "Download error",
                    url=url,
                    error=str(e),
                    attempt=attempt + 1
                )
            
            if attempt < self.max_retries:
                retries_used += 1
                delay = self.RETRY_DELAY * (attempt + 1)  # Exponential backoff
                logger.info("Retrying download", url=url, delay=delay)
                time.sleep(delay)
        
        logger.error("Download failed after all retries", url=url, error=last_error)
        return DownloadResult(
            success=False,
            url=url,
            error=last_error,
            retries_used=retries_used
        )
    
    def _download_attempt(self, url: str, output_path: Path) -> DownloadResult:
        """
        Single download attempt with streaming.
        
        Args:
            url: URL to download from
            output_path: Path to save the file
            
        Returns:
            DownloadResult with download details
        """
        with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
            with client.stream("GET", url, headers=self.headers) as response:
                response.raise_for_status()
                
                content_type = response.headers.get("content-type", "")
                content_length = response.headers.get("content-length")
                
                # Handle Content-Disposition for filename
                content_disposition = response.headers.get("content-disposition", "")
                if content_disposition and "filename=" in content_disposition:
                    logger.debug("Content-Disposition header present", header=content_disposition)
                
                # Stream to file
                total_size = 0
                with open(output_path, "wb") as f:
                    for chunk in response.iter_bytes(chunk_size=8192):
                        f.write(chunk)
                        total_size += len(chunk)
                
                logger.info(
                    "PDF downloaded successfully",
                    url=url,
                    size=total_size,
                    content_type=content_type
                )
                
                return DownloadResult(
                    success=True,
                    url=url,
                    file_path=output_path,
                    file_size=total_size,
                    content_type=content_type
                )
    
    def download_to_bytes(self, url: str) -> tuple[Optional[bytes], Optional[str]]:
        """
        Download PDF content directly to memory.
        
        Args:
            url: URL to download from
            
        Returns:
            Tuple of (content bytes, error message)
        """
        logger.info("Downloading PDF to memory", url=url)
        
        for attempt in range(self.max_retries + 1):
            try:
                with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
                    response = client.get(url, headers=self.headers)
                    response.raise_for_status()
                    
                    logger.info(
                        "PDF downloaded to memory",
                        url=url,
                        size=len(response.content)
                    )
                    return response.content, None
                    
            except Exception as e:
                if attempt < self.max_retries:
                    time.sleep(self.RETRY_DELAY * (attempt + 1))
                else:
                    logger.error("Download to memory failed", url=url, error=str(e))
                    return None, str(e)
        
        return None, "Max retries exceeded"

