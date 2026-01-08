"""
Firecrawl API client for web scraping and PDF URL extraction.
"""

import re
from dataclasses import dataclass
from typing import Optional
import structlog
from firecrawl import FirecrawlApp

from config import settings

logger = structlog.get_logger()


@dataclass
class ScrapeResult:
    """Result of a Firecrawl scrape operation."""
    success: bool
    url: str
    final_url: Optional[str] = None  # After redirects
    content_type: Optional[str] = None
    pdf_url: Optional[str] = None  # Extracted PDF URL if page contains PDF link
    html_content: Optional[str] = None
    markdown_content: Optional[str] = None
    error: Optional[str] = None


class FirecrawlClient:
    """
    Client for Firecrawl API to scrape web pages and extract PDF URLs.
    """
    
    # Common patterns for PDF links on court websites
    PDF_LINK_PATTERNS = [
        r'href=["\']([^"\']+\.pdf)["\']',
        r'href=["\']([^"\']+/documents/[^"\']+)["\']',
        r'href=["\']([^"\']+download[^"\']*\.pdf)["\']',
    ]
    
    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize Firecrawl client.
        
        Args:
            api_key: Firecrawl API key. Uses env var if not provided.
        """
        self.api_key = api_key or settings.FIRECRAWL_API_KEY
        if not self.api_key:
            raise ValueError("Firecrawl API key is required")
        
        self.app = FirecrawlApp(api_key=self.api_key)
        logger.info("FirecrawlClient initialized")
    
    def scrape_url(self, url: str) -> ScrapeResult:
        """
        Scrape a URL using Firecrawl.
        
        If the URL is a direct PDF link, returns the URL directly.
        If it's a web page, attempts to extract PDF links from the content.
        
        Args:
            url: URL to scrape
            
        Returns:
            ScrapeResult with scrape details
        """
        logger.info("Scraping URL", url=url)
        
        # Check if URL is a direct PDF link
        if url.lower().endswith('.pdf'):
            logger.info("URL is direct PDF link", url=url)
            return ScrapeResult(
                success=True,
                url=url,
                final_url=url,
                content_type="application/pdf",
                pdf_url=url
            )
        
        try:
            # Scrape the page
            result = self.app.scrape_url(
                url,
                params={
                    'formats': ['html', 'markdown'],
                }
            )
            
            # Extract content from result
            html_content = result.get('html', '')
            markdown_content = result.get('markdown', '')
            metadata = result.get('metadata', {})
            
            # Try to find PDF links in the HTML
            pdf_url = self._extract_pdf_url(html_content, url)
            
            scrape_result = ScrapeResult(
                success=True,
                url=url,
                final_url=metadata.get('sourceURL', url),
                content_type=metadata.get('contentType'),
                pdf_url=pdf_url,
                html_content=html_content,
                markdown_content=markdown_content
            )
            
            if pdf_url:
                logger.info("Found PDF URL in page", source_url=url, pdf_url=pdf_url)
            else:
                logger.warning("No PDF URL found in page", url=url)
            
            return scrape_result
            
        except Exception as e:
            logger.error("Firecrawl scrape failed", url=url, error=str(e))
            return ScrapeResult(
                success=False,
                url=url,
                error=str(e)
            )
    
    def _extract_pdf_url(self, html_content: str, base_url: str) -> Optional[str]:
        """
        Extract PDF URL from HTML content.
        
        Args:
            html_content: HTML content to search
            base_url: Base URL for resolving relative links
            
        Returns:
            PDF URL if found, None otherwise
        """
        if not html_content:
            return None
        
        for pattern in self.PDF_LINK_PATTERNS:
            matches = re.findall(pattern, html_content, re.IGNORECASE)
            if matches:
                pdf_url = matches[0]
                # Resolve relative URLs
                if not pdf_url.startswith('http'):
                    if pdf_url.startswith('/'):
                        # Absolute path
                        from urllib.parse import urlparse
                        parsed = urlparse(base_url)
                        pdf_url = f"{parsed.scheme}://{parsed.netloc}{pdf_url}"
                    else:
                        # Relative path
                        pdf_url = f"{base_url.rsplit('/', 1)[0]}/{pdf_url}"
                return pdf_url
        
        return None
    
    def check_url_accessible(self, url: str) -> bool:
        """
        Quick check if a URL is accessible.
        
        Args:
            url: URL to check
            
        Returns:
            True if accessible, False otherwise
        """
        try:
            result = self.scrape_url(url)
            return result.success
        except Exception:
            return False

