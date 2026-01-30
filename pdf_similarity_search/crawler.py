"""BFS website crawler: discover PDF and page links from HTML."""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import AsyncIterator

from bs4 import BeautifulSoup
import httpx

from .config import get_settings
from .url_utils import (
    get_domain_for_filter,
    get_root_domain,
    is_same_domain,
    normalize_and_validate,
    normalize_url,
)

logger = logging.getLogger(__name__)

# PDF extension / type hints for discovery
PDF_EXT = ".pdf"
PDF_MIME = "application/pdf"


@dataclass
class CrawlState:
    """Mutable state for BFS crawl: queue, visited, discovered PDFs."""

    queue: deque[tuple[str, int]] = field(default_factory=lambda: deque())  # (url, depth)
    visited: set[str] = field(default_factory=set)
    discovered_pdfs: set[str] = field(default_factory=set)
    pages_crawled: int = 0


def _extract_pdf_and_page_links(soup: BeautifulSoup, base_url: str) -> tuple[set[str], set[str]]:
    """
    Extract PDF URLs and same-page links from HTML.

    Looks at: <a href>, <embed src>, <iframe src>, <object data>.

    Args:
        soup: Parsed BeautifulSoup document.
        base_url: Base URL for resolving relative links.

    Returns:
        (pdf_urls, page_urls) - sets of absolute normalized URLs.
    """
    pdf_urls: set[str] = set()
    page_urls: set[str] = set()

    def resolve(href: str | None) -> str | None:
        if not href or not (href := href.strip()):
            return None
        try:
            return normalize_url(href, base=base_url)
        except ValueError:
            return None

    # <a href>
    for tag in soup.find_all("a", href=True):
        href = tag["href"]
        url = resolve(href)
        if not url:
            continue
        if _is_pdf_url(href, tag):
            pdf_urls.add(url)
        else:
            page_urls.add(url)

    # <embed src>
    for tag in soup.find_all("embed", src=True):
        url = resolve(tag["src"])
        if url:
            if _is_pdf_url(tag.get("src") or "", tag):
                pdf_urls.add(url)
            else:
                page_urls.add(url)

    # <iframe src>
    for tag in soup.find_all("iframe", src=True):
        url = resolve(tag["src"])
        if url:
            if _is_pdf_url(tag.get("src") or "", tag):
                pdf_urls.add(url)
            else:
                page_urls.add(url)

    # <object data>
    for tag in soup.find_all("object", data=True):
        url = resolve(tag["data"])
        if url:
            if _is_pdf_url(tag.get("data") or "", tag):
                pdf_urls.add(url)
            else:
                page_urls.add(url)

    return pdf_urls, page_urls


def _is_pdf_url(raw_href: str, tag: BeautifulSoup) -> bool:
    """Return True if link likely points to a PDF (extension or type)."""
    raw = (raw_href or "").lower().split("?")[0]
    if raw.endswith(PDF_EXT):
        return True
    type_attr = (tag.get("type") or "").lower()
    if PDF_MIME in type_attr:
        return True
    return False


async def fetch_page(
    client: httpx.AsyncClient,
    url: str,
    timeout: float,
    rate_limit_delay: float,
) -> str | None:
    """
    Fetch a single page and return its text content.

    Handles HTTP errors and timeouts; returns None on failure.
    Applies rate limit delay after request.
    """
    try:
        resp = await client.get(
            url,
            follow_redirects=True,
            timeout=timeout,
        )
        resp.raise_for_status()
        await asyncio.sleep(rate_limit_delay)
        return resp.text
    except httpx.HTTPStatusError as e:
        logger.warning("HTTP error %s for %s: %s", e.response.status_code, url, e)
        return None
    except (httpx.TimeoutException, httpx.ConnectError) as e:
        logger.warning("Request failed for %s: %s", url, e)
        return None
    except Exception as e:
        logger.exception("Unexpected error fetching %s: %s", url, e)
        return None


def _parse_html(html: str) -> BeautifulSoup | None:
    """Parse HTML with BeautifulSoup; return None on parse error."""
    try:
        return BeautifulSoup(html, "html.parser")
    except Exception as e:
        logger.warning("HTML parse error: %s", e)
        return None


async def crawl_website(
    start_url: str,
    *,
    max_pages: int | None = None,
    max_depth: int | None = None,
    timeout: float | None = None,
    rate_limit_delay: float | None = None,
) -> AsyncIterator[tuple[str, set[str]]]:
    """
    BFS crawl starting from start_url and domain root.

    Yields (page_url, set of pdf_urls found on that page) for each page.
    Stops when max_pages or max_depth is reached.
    Only crawls same-domain URLs.
    """
    settings = get_settings()
    max_pages = max_pages if max_pages is not None else settings.max_pages
    max_depth = max_depth if max_depth is not None else settings.max_depth
    timeout = timeout if timeout is not None else settings.request_timeout
    rate_limit_delay = rate_limit_delay if rate_limit_delay is not None else settings.rate_limit_delay

    # Normalize and validate start URL
    try:
        start_url = normalize_and_validate(start_url)
    except ValueError as e:
        logger.error("Invalid start URL: %s", e)
        return

    root_domain = get_root_domain(start_url)
    allowed_netloc = get_domain_for_filter(start_url)

    state = CrawlState()
    # Seed queue: start URL and domain root at depth 0
    for seed in (start_url, root_domain):
        if seed not in state.visited:
            state.queue.append((seed, 0))

    headers = {
        "User-Agent": "PDF-Similarity-Search/1.0 (crawl)",
        "Accept": "text/html,application/xhtml+xml",
    }

    async with httpx.AsyncClient(headers=headers) as client:
        while state.queue and state.pages_crawled < max_pages:
            url, depth = state.queue.popleft()

            if url in state.visited:
                continue
            if depth > max_depth:
                continue
            if not is_same_domain(url, allowed_netloc):
                continue

            state.visited.add(url)
            state.pages_crawled += 1
            logger.info("Crawling [%d/%d] depth=%d %s", state.pages_crawled, max_pages, depth, url)

            html = await fetch_page(client, url, timeout, rate_limit_delay)
            if not html:
                continue

            soup = _parse_html(html)
            if not soup:
                continue

            pdf_urls, page_urls = _extract_pdf_and_page_links(soup, url)
            state.discovered_pdfs.update(pdf_urls)
            if pdf_urls:
                logger.info("Found %d PDF(s) on %s", len(pdf_urls), url)

            yield url, pdf_urls

            # Enqueue same-domain page links at depth + 1
            for next_url in page_urls:
                if next_url in state.visited:
                    continue
                if not is_same_domain(next_url, allowed_netloc):
                    continue
                if (next_url, depth + 1) not in state.queue and depth + 1 <= max_depth:
                    state.queue.append((next_url, depth + 1))

