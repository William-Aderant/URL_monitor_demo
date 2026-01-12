"""
Link Crawler Service

Crawls parent pages to find relocated PDF forms when the original URL becomes unavailable.
"""

import re
from dataclasses import dataclass
from typing import Optional, List
from urllib.parse import urljoin, urlparse

import httpx
import structlog

logger = structlog.get_logger()


@dataclass
class CrawlResult:
    """Result of crawling a parent page for PDF links."""
    success: bool
    pdf_links: List[str] = None
    matched_url: Optional[str] = None
    match_reason: Optional[str] = None
    error: Optional[str] = None


@dataclass
class PDFLinkInfo:
    """Information about a PDF link found on a page."""
    url: str
    text: str  # Link text
    filename: str  # Extracted filename


class LinkCrawler:
    """
    Crawls parent pages to find PDF links, useful for locating relocated forms.
    """
    
    def __init__(self, timeout: int = 30):
        """
        Initialize the link crawler.
        
        Args:
            timeout: HTTP request timeout in seconds
        """
        self.timeout = timeout
        logger.info("LinkCrawler initialized")
    
    def extract_parent_url(self, pdf_url: str) -> Optional[str]:
        """
        Extract the parent page URL from a PDF URL.
        
        Examples:
            https://courts.alaska.gov/forms/docs/civ-775.pdf 
            -> https://courts.alaska.gov/forms/docs/
            
            https://courts.alaska.gov/forms/civil.html
            -> https://courts.alaska.gov/forms/
        
        Args:
            pdf_url: The PDF file URL
            
        Returns:
            Parent page URL or None if cannot be determined
        """
        parsed = urlparse(pdf_url)
        path = parsed.path
        
        # Remove the filename to get the directory
        if '/' in path:
            parent_path = path.rsplit('/', 1)[0] + '/'
        else:
            parent_path = '/'
        
        parent_url = f"{parsed.scheme}://{parsed.netloc}{parent_path}"
        return parent_url
    
    def extract_form_number(self, text: str) -> Optional[str]:
        """
        Extract a form number from text (filename or link text).
        
        Patterns matched:
            - CIV-775, ADR-103, MC-025
            - civ775, civ-775
            - Form CIV-775
        
        Args:
            text: Text to search for form numbers
            
        Returns:
            Extracted form number (normalized) or None
        """
        # Common patterns for form numbers
        patterns = [
            r'([A-Za-z]{2,4})-?(\d{2,4})',  # CIV-775 or CIV775
            r'[Ff]orm\s+([A-Za-z]{2,4})-?(\d{2,4})',  # Form CIV-775
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                groups = match.groups()
                if len(groups) == 2:
                    prefix, number = groups
                    return f"{prefix.upper()}-{number}"
        
        return None
    
    def crawl_page_for_pdfs(self, page_url: str) -> CrawlResult:
        """
        Crawl a page and extract all PDF links.
        
        Args:
            page_url: URL of the page to crawl
            
        Returns:
            CrawlResult with list of PDF links found
        """
        try:
            logger.info("Crawling page for PDFs", url=page_url)
            
            with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
                response = client.get(page_url)
                response.raise_for_status()
                
                html = response.text
                
                # Find all PDF links using regex
                # Match href attributes containing .pdf
                pdf_pattern = r'href=["\']([^"\']*\.pdf)["\']'
                matches = re.findall(pdf_pattern, html, re.IGNORECASE)
                
                # Convert relative URLs to absolute
                pdf_links = []
                for match in matches:
                    absolute_url = urljoin(page_url, match)
                    pdf_links.append(absolute_url)
                
                # Remove duplicates while preserving order
                pdf_links = list(dict.fromkeys(pdf_links))
                
                logger.info("Found PDF links", count=len(pdf_links))
                
                return CrawlResult(
                    success=True,
                    pdf_links=pdf_links
                )
                
        except httpx.HTTPStatusError as e:
            logger.error("HTTP error crawling page", url=page_url, status=e.response.status_code)
            return CrawlResult(success=False, error=f"HTTP {e.response.status_code}")
            
        except Exception as e:
            logger.exception("Error crawling page", url=page_url, error=str(e))
            return CrawlResult(success=False, error=str(e))
    
    def find_relocated_form(
        self,
        original_url: str,
        form_number: Optional[str] = None,
        form_title: Optional[str] = None,
        parent_url: Optional[str] = None
    ) -> CrawlResult:
        """
        Find a relocated form by crawling the parent page and matching.
        
        Args:
            original_url: Original PDF URL that is no longer working
            form_number: Known form number (e.g., "CIV-775")
            form_title: Known form title
            parent_url: Parent page URL to crawl (auto-detected if not provided)
            
        Returns:
            CrawlResult with matched URL if found
        """
        # Determine parent URL
        if not parent_url:
            parent_url = self.extract_parent_url(original_url)
            
        if not parent_url:
            return CrawlResult(
                success=False,
                error="Could not determine parent page URL"
            )
        
        # Extract form number from original URL if not provided
        if not form_number:
            original_filename = original_url.rsplit('/', 1)[-1]
            form_number = self.extract_form_number(original_filename)
        
        logger.info(
            "Searching for relocated form",
            original_url=original_url,
            form_number=form_number,
            parent_url=parent_url
        )
        
        # Crawl the parent page
        crawl_result = self.crawl_page_for_pdfs(parent_url)
        
        if not crawl_result.success:
            return crawl_result
        
        if not crawl_result.pdf_links:
            return CrawlResult(
                success=False,
                pdf_links=[],
                error="No PDF links found on parent page"
            )
        
        # Try to match by form number
        if form_number:
            for pdf_url in crawl_result.pdf_links:
                filename = pdf_url.rsplit('/', 1)[-1]
                url_form_number = self.extract_form_number(filename)
                
                if url_form_number and url_form_number.upper() == form_number.upper():
                    logger.info(
                        "Found relocated form by form number",
                        original=original_url,
                        new_url=pdf_url,
                        form_number=form_number
                    )
                    return CrawlResult(
                        success=True,
                        pdf_links=crawl_result.pdf_links,
                        matched_url=pdf_url,
                        match_reason=f"Form number match: {form_number}"
                    )
        
        # Try to match by filename similarity
        original_filename = original_url.rsplit('/', 1)[-1].lower().replace('.pdf', '')
        
        best_match = None
        best_similarity = 0.0
        
        for pdf_url in crawl_result.pdf_links:
            filename = pdf_url.rsplit('/', 1)[-1].lower().replace('.pdf', '')
            
            # Calculate simple similarity (common characters ratio)
            similarity = self._calculate_filename_similarity(original_filename, filename)
            
            if similarity > best_similarity and similarity > 0.6:
                best_similarity = similarity
                best_match = pdf_url
        
        if best_match:
            logger.info(
                "Found relocated form by filename similarity",
                original=original_url,
                new_url=best_match,
                similarity=best_similarity
            )
            return CrawlResult(
                success=True,
                pdf_links=crawl_result.pdf_links,
                matched_url=best_match,
                match_reason=f"Filename similarity: {best_similarity:.0%}"
            )
        
        # No match found, but return all PDF links for manual review
        return CrawlResult(
            success=True,
            pdf_links=crawl_result.pdf_links,
            matched_url=None,
            match_reason="No automatic match found - manual review needed"
        )
    
    def _calculate_filename_similarity(self, name1: str, name2: str) -> float:
        """
        Calculate similarity between two filenames.
        
        Uses a simple approach: ratio of matching characters.
        
        Args:
            name1: First filename (without extension)
            name2: Second filename (without extension)
            
        Returns:
            Similarity score from 0.0 to 1.0
        """
        if not name1 or not name2:
            return 0.0
        
        # Remove common prefixes/suffixes that don't help matching
        name1 = re.sub(r'[-_\s]', '', name1)
        name2 = re.sub(r'[-_\s]', '', name2)
        
        if name1 == name2:
            return 1.0
        
        # Count matching characters
        shorter = min(len(name1), len(name2))
        longer = max(len(name1), len(name2))
        
        if longer == 0:
            return 0.0
        
        # Use difflib for better matching
        import difflib
        return difflib.SequenceMatcher(None, name1, name2).ratio()
    
    def check_url_available(self, url: str) -> bool:
        """
        Check if a URL is accessible (returns 200).
        
        Args:
            url: URL to check
            
        Returns:
            True if accessible, False otherwise
        """
        try:
            with httpx.Client(timeout=10, follow_redirects=True) as client:
                response = client.head(url)
                return response.status_code == 200
        except:
            return False
