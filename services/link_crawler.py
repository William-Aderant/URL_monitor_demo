"""
Link Crawler Service

Crawls parent pages to find relocated PDF forms when the original URL becomes unavailable.
Supports multi-level recursive crawling with backtracking for complex site structures.
"""

import difflib
import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional, List, Set, Dict, Tuple
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
    pages_crawled: int = 0
    search_path: List[str] = None  # Path taken to find the match


@dataclass
class PDFLinkInfo:
    """Information about a PDF link found on a page."""
    url: str
    text: str  # Link text
    filename: str  # Extracted filename
    form_number: Optional[str] = None
    found_on_page: str = ""  # URL where this link was found


@dataclass
class PageInfo:
    """Information about a crawled page."""
    url: str
    html: str
    pdf_links: List[PDFLinkInfo] = field(default_factory=list)
    navigation_links: List[str] = field(default_factory=list)


class LinkCrawler:
    """
    Crawls parent pages to find PDF links, useful for locating relocated forms.
    
    Supports multi-level recursive crawling with configurable depth and 
    backtracking for complex site structures like the Alaska Court System forms.
    """
    
    # Default headers to mimic browser
    DEFAULT_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    
    def __init__(
        self, 
        timeout: int = 30,
        max_depth: int = 3,
        max_pages: int = 50,
        delay_between_requests: float = 0.5
    ):
        """
        Initialize the link crawler.
        
        Args:
            timeout: HTTP request timeout in seconds
            max_depth: Maximum depth for recursive crawling (0 = single page only)
            max_pages: Maximum total pages to crawl before giving up
            delay_between_requests: Delay between HTTP requests (be polite)
        """
        self.timeout = timeout
        self.max_depth = max_depth
        self.max_pages = max_pages
        self.delay_between_requests = delay_between_requests
        self.headers = self.DEFAULT_HEADERS.copy()
        logger.info(
            "LinkCrawler initialized",
            max_depth=max_depth,
            max_pages=max_pages
        )
    
    def extract_base_forms_url(self, pdf_url: str) -> Optional[str]:
        """
        Extract the base forms page URL from a PDF URL.
        
        For Alaska Courts, this would go from:
            https://public.courts.alaska.gov/web/forms/docs/civ-775.pdf
        To:
            https://public.courts.alaska.gov/web/forms/
        
        Args:
            pdf_url: The PDF file URL
            
        Returns:
            Base forms page URL or None
        """
        parsed = urlparse(pdf_url)
        path = parsed.path.lower()
        
        # Look for common patterns that indicate a forms directory
        forms_patterns = [
            r'(/web/forms/)',
            r'(/forms/)',
            r'(/documents/)',
            r'(/pdfs/)',
            r'(/files/)',
        ]
        
        for pattern in forms_patterns:
            match = re.search(pattern, path, re.IGNORECASE)
            if match:
                base_path = path[:match.end()]
                return f"{parsed.scheme}://{parsed.netloc}{base_path}"
        
        # Fallback: go up two directory levels from the PDF
        if '/' in path:
            parts = path.rsplit('/', 2)
            if len(parts) >= 2:
                parent_path = '/'.join(parts[:-2]) + '/'
                return f"{parsed.scheme}://{parsed.netloc}{parent_path}"
        
        return None
    
    def extract_parent_url(self, pdf_url: str) -> Optional[str]:
        """
        Extract the immediate parent page URL from a PDF URL.
        
        Examples:
            https://courts.alaska.gov/forms/docs/civ-775.pdf 
            -> https://courts.alaska.gov/forms/docs/
            
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
            - CIV-775, ADR-103, MC-025, DR-100, DV-100
            - civ775, civ-775
            - Form CIV-775
            - AP-100, CN-306, CP-410
        
        Args:
            text: Text to search for form numbers
            
        Returns:
            Extracted form number (normalized) or None
        """
        if not text:
            return None
            
        # Common Alaska Court form prefixes
        # ADM, AP, CIV, CN, CP, CR, DL, DR, DV, HCA, MC, MH, PB, SC, TF, TR, VS
        prefixes = r'(?:ADM|AP|CIV|CN|CP|CR|DL|DR|DV|HCA|MC|MH|PB|SC|TF|TR|VS)'
        
        # Patterns in order of specificity
        patterns = [
            # "Form Number: CIV-775" or "Form No. CIV-775"
            rf'[Ff]orm\s*(?:[Nn]o\.?|[Nn]umber:?)\s*({prefixes})-?(\d{{2,4}}[A-Za-z]?)',
            # "CIV-775" with known prefix and hyphen
            rf'\b({prefixes})-(\d{{2,4}}[A-Za-z]?)\b',
            # Generic form number pattern (2-4 letter prefix + hyphen + numbers)
            r'\b([A-Za-z]{2,4})-(\d{2,4}[A-Za-z]?)\b',
            # Form number without hyphen for known prefixes
            rf'\b({prefixes})(\d{{3,4}}[A-Za-z]?)\b',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                groups = match.groups()
                if len(groups) == 2:
                    prefix, number = groups
                    return f"{prefix.upper()}-{number}"
        
        return None
    
    def _is_valid_navigation_link(self, url: str, base_domain: str, base_path: str) -> bool:
        """
        Check if a URL is a valid navigation link to follow.
        
        Args:
            url: URL to check
            base_domain: Domain to stay within
            base_path: Base path prefix to stay within
            
        Returns:
            True if this link should be followed
        """
        try:
            parsed = urlparse(url)
            
            # Must be same domain
            if parsed.netloc != base_domain:
                return False
            
            # Must be within the base path (e.g., /web/forms/)
            if not parsed.path.lower().startswith(base_path.lower()):
                return False
            
            # Skip PDF links (we'll collect those separately)
            if parsed.path.lower().endswith('.pdf'):
                return False
            
            # Skip common non-content links
            skip_patterns = [
                r'\.(jpg|jpeg|png|gif|css|js|ico|svg)$',
                r'^mailto:',
                r'^tel:',
                r'^javascript:',
                r'#',  # Anchor links
                r'\?',  # Query strings (might want to revisit)
            ]
            
            url_lower = url.lower()
            for pattern in skip_patterns:
                if re.search(pattern, url_lower):
                    return False
            
            return True
            
        except Exception:
            return False
    
    def _extract_links_from_html(
        self, 
        html: str, 
        page_url: str,
        base_domain: str,
        base_path: str
    ) -> Tuple[List[PDFLinkInfo], List[str]]:
        """
        Extract PDF links and navigation links from HTML.
        
        Args:
            html: HTML content
            page_url: URL of the page (for resolving relative links)
            base_domain: Domain to filter navigation links
            base_path: Path prefix to filter navigation links
            
        Returns:
            Tuple of (pdf_links, navigation_links)
        """
        pdf_links = []
        navigation_links = []
        seen_urls = set()
        
        # Pattern to find all anchor tags with href
        # Captures href value and link text
        link_pattern = r'<a[^>]*href=["\']([^"\']+)["\'][^>]*>([^<]*)</a>'
        
        for match in re.finditer(link_pattern, html, re.IGNORECASE | re.DOTALL):
            href = match.group(1).strip()
            link_text = match.group(2).strip()
            
            # Convert to absolute URL
            absolute_url = urljoin(page_url, href)
            
            # Skip duplicates
            if absolute_url in seen_urls:
                continue
            seen_urls.add(absolute_url)
            
            # Check if it's a PDF
            if href.lower().endswith('.pdf'):
                filename = href.rsplit('/', 1)[-1]
                form_number = self.extract_form_number(filename) or self.extract_form_number(link_text)
                
                pdf_links.append(PDFLinkInfo(
                    url=absolute_url,
                    text=link_text,
                    filename=filename,
                    form_number=form_number,
                    found_on_page=page_url
                ))
            
            # Check if it's a valid navigation link
            elif self._is_valid_navigation_link(absolute_url, base_domain, base_path):
                navigation_links.append(absolute_url)
        
        # Also look for PDF links without anchor tags (direct href in other elements)
        pdf_href_pattern = r'href=["\']([^"\']*\.pdf)["\']'
        for match in re.finditer(pdf_href_pattern, html, re.IGNORECASE):
            href = match.group(1)
            absolute_url = urljoin(page_url, href)
            
            if absolute_url not in seen_urls:
                seen_urls.add(absolute_url)
                filename = href.rsplit('/', 1)[-1]
                form_number = self.extract_form_number(filename)
                
                pdf_links.append(PDFLinkInfo(
                    url=absolute_url,
                    text="",
                    filename=filename,
                    form_number=form_number,
                    found_on_page=page_url
                ))
        
        return pdf_links, navigation_links
    
    def _fetch_page(self, url: str) -> Optional[str]:
        """
        Fetch a page's HTML content.
        
        Args:
            url: URL to fetch
            
        Returns:
            HTML content or None if failed
        """
        try:
            # First try with httpx
            with httpx.Client(
                timeout=self.timeout, 
                follow_redirects=True,
                headers=self.headers
            ) as client:
                response = client.get(url)
                response.raise_for_status()
                return response.text
        except httpx.HTTPStatusError as e:
            logger.warning("HTTP error fetching page", url=url, status=e.response.status_code)
            return None
        except Exception as e:
            # Some servers (like Alaska Courts) have non-standard HTTP headers
            # that httpx rejects. Try with urllib as fallback.
            if "Transfer-Encoding" in str(e) or "header" in str(e).lower():
                logger.debug("Trying urllib fallback due to header issues", url=url)
                return self._fetch_page_urllib(url)
            logger.warning("Error fetching page", url=url, error=str(e))
            return None
    
    def _fetch_page_urllib(self, url: str) -> Optional[str]:
        """
        Fallback fetch using urllib for servers with non-standard headers.
        
        Some government websites return non-standard HTTP headers that
        strict HTTP clients reject. urllib is more lenient.
        
        Args:
            url: URL to fetch
            
        Returns:
            HTML content or None if failed
        """
        import urllib.request
        import ssl
        
        try:
            # Create a request with browser-like headers
            request = urllib.request.Request(url, headers=self.headers)
            
            # Create SSL context that doesn't verify (some gov sites have issues)
            context = ssl.create_default_context()
            
            with urllib.request.urlopen(request, timeout=self.timeout, context=context) as response:
                # Read and decode the response
                content = response.read()
                
                # Try to determine encoding
                charset = response.headers.get_content_charset()
                if charset:
                    return content.decode(charset)
                
                # Try common encodings
                for encoding in ['utf-8', 'latin-1', 'iso-8859-1']:
                    try:
                        return content.decode(encoding)
                    except UnicodeDecodeError:
                        continue
                
                # Last resort
                return content.decode('utf-8', errors='replace')
                
        except Exception as e:
            logger.warning("urllib fallback also failed", url=url, error=str(e))
            return None
    
    def crawl_page_for_pdfs(self, page_url: str) -> CrawlResult:
        """
        Crawl a single page and extract all PDF links (legacy single-page method).
        
        Args:
            page_url: URL of the page to crawl
            
        Returns:
            CrawlResult with list of PDF links found
        """
        try:
            logger.info("Crawling page for PDFs", url=page_url)
            
            html = self._fetch_page(page_url)
            if html is None:
                return CrawlResult(success=False, error="Failed to fetch page")
            
            parsed = urlparse(page_url)
            base_domain = parsed.netloc
            base_path = parsed.path.rsplit('/', 1)[0] + '/'
            
            pdf_links, _ = self._extract_links_from_html(html, page_url, base_domain, base_path)
            
            pdf_urls = [link.url for link in pdf_links]
            # Remove duplicates while preserving order
            pdf_urls = list(dict.fromkeys(pdf_urls))
            
            logger.info("Found PDF links", count=len(pdf_urls))
            
            return CrawlResult(
                success=True,
                pdf_links=pdf_urls,
                pages_crawled=1
            )
            
        except Exception as e:
            logger.exception("Error crawling page", url=page_url, error=str(e))
            return CrawlResult(success=False, error=str(e))
    
    def recursive_crawl(
        self,
        start_url: str,
        target_form_number: Optional[str] = None,
        target_filename: Optional[str] = None,
        use_bfs: bool = True
    ) -> CrawlResult:
        """
        Recursively crawl pages to find PDF links, with optional early termination
        when target form is found.
        
        Uses BFS (breadth-first) or DFS (depth-first) search with backtracking.
        
        Args:
            start_url: Starting URL for crawling
            target_form_number: If provided, stop when this form number is found
            target_filename: If provided, use for similarity matching
            use_bfs: Use breadth-first search (True) or depth-first search (False)
            
        Returns:
            CrawlResult with all PDF links found and matched URL if target found
        """
        parsed = urlparse(start_url)
        base_domain = parsed.netloc
        
        # Determine base path for filtering navigation links
        # For Alaska Courts, keep within /web/forms/
        base_path = self.extract_base_forms_url(start_url)
        if base_path:
            base_path = urlparse(base_path).path
        else:
            base_path = parsed.path.rsplit('/', 1)[0] + '/'
        
        logger.info(
            "Starting recursive crawl",
            start_url=start_url,
            target_form_number=target_form_number,
            base_path=base_path,
            strategy="BFS" if use_bfs else "DFS"
        )
        
        # Track visited URLs and found PDFs
        visited: Set[str] = set()
        all_pdf_links: Dict[str, PDFLinkInfo] = {}  # URL -> PDFLinkInfo
        
        # Queue/stack for URLs to visit: (url, depth, path_to_here)
        if use_bfs:
            to_visit = deque([(start_url, 0, [start_url])])
        else:
            to_visit = [(start_url, 0, [start_url])]  # Stack for DFS
        
        pages_crawled = 0
        matched_url = None
        match_reason = None
        search_path = None
        
        while to_visit and pages_crawled < self.max_pages:
            # Get next URL (from front for BFS, from back for DFS)
            if use_bfs:
                current_url, depth, path = to_visit.popleft()
            else:
                current_url, depth, path = to_visit.pop()
            
            # Skip if already visited
            if current_url in visited:
                continue
            
            visited.add(current_url)
            
            # Check depth limit
            if depth > self.max_depth:
                continue
            
            # Fetch and parse page
            logger.debug("Crawling page", url=current_url, depth=depth, pages_so_far=pages_crawled)
            
            html = self._fetch_page(current_url)
            if html is None:
                continue
            
            pages_crawled += 1
            
            # Extract links
            pdf_links, nav_links = self._extract_links_from_html(
                html, current_url, base_domain, base_path
            )
            
            # Process PDF links
            for pdf_info in pdf_links:
                if pdf_info.url not in all_pdf_links:
                    all_pdf_links[pdf_info.url] = pdf_info
                    
                    logger.debug(
                        "Found PDF",
                        url=pdf_info.url,
                        form_number=pdf_info.form_number,
                        on_page=current_url
                    )
                    
                    # Check for target match
                    if target_form_number and pdf_info.form_number:
                        if pdf_info.form_number.upper() == target_form_number.upper():
                            matched_url = pdf_info.url
                            match_reason = f"Form number match: {target_form_number}"
                            search_path = path + [f"[PDF: {pdf_info.url}]"]
                            
                            logger.info(
                                "Found target form by number!",
                                url=matched_url,
                                form_number=target_form_number,
                                pages_crawled=pages_crawled,
                                depth=depth
                            )
                            
                            # Return immediately with match
                            return CrawlResult(
                                success=True,
                                pdf_links=list(all_pdf_links.keys()),
                                matched_url=matched_url,
                                match_reason=match_reason,
                                pages_crawled=pages_crawled,
                                search_path=search_path
                            )
            
            # Add navigation links to queue/stack
            for nav_url in nav_links:
                if nav_url not in visited:
                    new_path = path + [nav_url]
                    if use_bfs:
                        to_visit.append((nav_url, depth + 1, new_path))
                    else:
                        to_visit.append((nav_url, depth + 1, new_path))
            
            # Polite delay between requests
            if self.delay_between_requests > 0 and to_visit:
                time.sleep(self.delay_between_requests)
        
        # If we didn't find by form number, try filename similarity
        if target_filename and not matched_url:
            best_match = None
            best_similarity = 0.0
            
            target_clean = target_filename.lower().replace('.pdf', '')
            
            for url, pdf_info in all_pdf_links.items():
                filename_clean = pdf_info.filename.lower().replace('.pdf', '')
                similarity = self._calculate_filename_similarity(target_clean, filename_clean)
                
                if similarity > best_similarity and similarity > 0.6:
                    best_similarity = similarity
                    best_match = pdf_info
            
            if best_match:
                matched_url = best_match.url
                match_reason = f"Filename similarity: {best_similarity:.0%}"
                search_path = [f"[Similarity match from {pages_crawled} pages]"]
                
                logger.info(
                    "Found target form by similarity",
                    url=matched_url,
                    similarity=best_similarity,
                    pages_crawled=pages_crawled
                )
        
        logger.info(
            "Recursive crawl complete",
            pages_crawled=pages_crawled,
            pdfs_found=len(all_pdf_links),
            matched=matched_url is not None
        )
        
        return CrawlResult(
            success=True,
            pdf_links=list(all_pdf_links.keys()),
            matched_url=matched_url,
            match_reason=match_reason or "No automatic match found - manual review needed",
            pages_crawled=pages_crawled,
            search_path=search_path
        )
    
    def find_relocated_form(
        self,
        original_url: str,
        form_number: Optional[str] = None,
        form_title: Optional[str] = None,
        parent_url: Optional[str] = None
    ) -> CrawlResult:
        """
        Find a relocated form by crawling from the parent page with multi-level search.
        
        This enhanced version:
        1. Tries the immediate parent directory first (fast path)
        2. If not found, escalates to the base forms page
        3. Uses recursive BFS to explore all form sections
        4. Matches by form number, then by filename similarity
        
        Args:
            original_url: Original PDF URL that is no longer working
            form_number: Known form number (e.g., "CIV-775")
            form_title: Known form title
            parent_url: Parent page URL to crawl (auto-detected if not provided)
            
        Returns:
            CrawlResult with matched URL if found
        """
        # Extract form number from original URL if not provided
        if not form_number:
            original_filename = original_url.rsplit('/', 1)[-1]
            form_number = self.extract_form_number(original_filename)
        
        original_filename = original_url.rsplit('/', 1)[-1]
        
        logger.info(
            "Searching for relocated form (enhanced)",
            original_url=original_url,
            form_number=form_number,
            form_title=form_title
        )
        
        # Step 1: Try immediate parent directory (fast path)
        immediate_parent = parent_url or self.extract_parent_url(original_url)
        if immediate_parent:
            logger.info("Step 1: Checking immediate parent", url=immediate_parent)
            result = self.crawl_page_for_pdfs(immediate_parent)
            
            if result.success and result.pdf_links:
                # Check for form number match
                if form_number:
                    for pdf_url in result.pdf_links:
                        filename = pdf_url.rsplit('/', 1)[-1]
                        url_form_number = self.extract_form_number(filename)
                        
                        if url_form_number and url_form_number.upper() == form_number.upper():
                            logger.info(
                                "Found relocated form in immediate parent",
                                new_url=pdf_url,
                                form_number=form_number
                            )
                            return CrawlResult(
                                success=True,
                                pdf_links=result.pdf_links,
                                matched_url=pdf_url,
                                match_reason=f"Form number match in parent directory: {form_number}",
                                pages_crawled=1,
                                search_path=[immediate_parent]
                            )
        
        # Step 2: Try base forms page with recursive crawling
        base_forms_url = self.extract_base_forms_url(original_url)
        
        # If parent_url was provided explicitly, use that as the starting point
        # (it might be the main forms index page)
        if parent_url and parent_url != immediate_parent:
            base_forms_url = parent_url
        
        if base_forms_url:
            logger.info(
                "Step 2: Recursive crawl from base forms page",
                url=base_forms_url
            )
            
            result = self.recursive_crawl(
                start_url=base_forms_url,
                target_form_number=form_number,
                target_filename=original_filename,
                use_bfs=True  # BFS is better for finding forms in organized sites
            )
            
            if result.matched_url:
                return result
            
            # If no match but we found PDFs, try filename similarity
            if result.pdf_links and not result.matched_url:
                best_match = None
                best_similarity = 0.0
                
                target_clean = original_filename.lower().replace('.pdf', '')
                
                for pdf_url in result.pdf_links:
                    filename = pdf_url.rsplit('/', 1)[-1].lower().replace('.pdf', '')
                    similarity = self._calculate_filename_similarity(target_clean, filename)
                    
                    if similarity > best_similarity and similarity > 0.6:
                        best_similarity = similarity
                        best_match = pdf_url
                
                if best_match:
                    return CrawlResult(
                        success=True,
                        pdf_links=result.pdf_links,
                        matched_url=best_match,
                        match_reason=f"Filename similarity: {best_similarity:.0%}",
                        pages_crawled=result.pages_crawled,
                        search_path=result.search_path
                    )
            
            return result
        
        # Fallback: couldn't determine where to search
        return CrawlResult(
            success=False,
            error="Could not determine parent page URL for recursive search"
        )
    
    def _calculate_filename_similarity(self, name1: str, name2: str) -> float:
        """
        Calculate similarity between two filenames.
        
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
        
        if not name1 or not name2:
            return 0.0
        
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
            with httpx.Client(
                timeout=10, 
                follow_redirects=True,
                headers=self.headers
            ) as client:
                response = client.head(url)
                return response.status_code == 200
        except:
            return False
