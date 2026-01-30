"""Search orchestration: crawl site, discover PDFs, compare to reference (text similarity)."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from .config import get_settings
from .crawler import crawl_website
from .models import MatchResult, NearMiss, SearchStats
from .pdf_processor import (
    download_pdf_stream,
    extract_text_from_pdf_bytes,
    get_reference_text,
)
from .similarity import text_similarity_score

logger = logging.getLogger(__name__)

REASON_MATCH_FOUND = "match_found"
REASON_MAX_PAGES = "max_pages_reached"
REASON_EXHAUSTED = "crawl_exhausted"


@dataclass
class SearchRunState:
    """Mutable state for one search run."""

    pages_crawled: int = 0
    pdfs_analyzed: int = 0
    near_misses: list[NearMiss] = None
    start_time: float = 0.0
    stopped_reason: str = REASON_EXHAUSTED
    matches: list[MatchResult] = None

    def __post_init__(self) -> None:
        if self.near_misses is None:
            self.near_misses = []
        if self.matches is None:
            self.matches = []
        if self.start_time == 0.0:
            self.start_time = time.monotonic()


def _build_match_result(pdf_url: str, similarity: float, file_size_bytes: int) -> MatchResult:
    """Build a MatchResult from matched PDF info."""
    return MatchResult(
        pdf_url=pdf_url,
        similarity_score=round(similarity, 1),
        file_size_mb=round(file_size_bytes / (1024 * 1024), 2),
        discovered_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    )


async def _process_one_pdf(
    client: httpx.AsyncClient,
    pdf_url: str,
    reference_text: str,
    threshold: float,
    near_miss_min: float,
    near_miss_max: float,
    state: SearchRunState,
    semaphore: asyncio.Semaphore,
    timeout: float,
    max_pdf_bytes: int,
) -> MatchResult | None:
    """Download one PDF, compare to reference text. Return MatchResult if >= threshold."""
    async with semaphore:
        try:
            data, size = await download_pdf_stream(
                client, pdf_url, timeout=timeout, max_size_bytes=max_pdf_bytes
            )
        except Exception as e:
            logger.warning("Failed to download PDF %s: %s", pdf_url, e)
            return None

        pdf_text = extract_text_from_pdf_bytes(data)
        sim = text_similarity_score(reference_text, pdf_text)

        state.pdfs_analyzed += 1
        logger.info("PDF %s similarity=%.1f size=%d", pdf_url, sim, size)

        if sim >= threshold:
            return _build_match_result(pdf_url, sim, size)
        if near_miss_min <= sim <= near_miss_max:
            state.near_misses.append(NearMiss(url=pdf_url, similarity=round(sim, 1)))
        return None


async def run_search(
    website_url: str,
    reference_pdf_path: str,
    *,
    similarity_threshold: float | None = None,
    max_pages: int | None = None,
    max_depth: int | None = None,
    max_results: int = 1,
) -> tuple[list[MatchResult], list[NearMiss], SearchStats]:
    """Crawl site for PDFs, compare to reference. Collect up to max_results matches (>= threshold), sorted by score descending."""
    settings = get_settings()
    threshold = similarity_threshold if similarity_threshold is not None else settings.similarity_threshold
    max_pages = max_pages if max_pages is not None else settings.max_pages
    max_depth = max_depth if max_depth is not None else settings.max_depth
    near_miss_min = settings.near_miss_min_similarity
    near_miss_max = settings.near_miss_max_similarity
    timeout = settings.request_timeout
    max_pdf_bytes = settings.max_pdf_size_bytes
    concurrent = settings.concurrent_downloads

    reference_text = get_reference_text(reference_pdf_path)

    state = SearchRunState()
    semaphore = asyncio.Semaphore(concurrent)
    seen_pdf_urls: set[str] = set()

    headers = {"User-Agent": "PDF-Similarity-Search/1.0 (download)", "Accept": "application/pdf,*/*"}

    async with httpx.AsyncClient(headers=headers) as client:
        async for _page_url, pdf_urls in crawl_website(website_url, max_pages=max_pages, max_depth=max_depth):
            state.pages_crawled += 1
            new_pdfs = [u for u in pdf_urls if u not in seen_pdf_urls]
            seen_pdf_urls.update(new_pdfs)

            tasks = [
                _process_one_pdf(
                    client, pdf_url, reference_text,
                    threshold, near_miss_min, near_miss_max,
                    state, semaphore, timeout, max_pdf_bytes,
                )
                for pdf_url in new_pdfs
            ]
            if not tasks:
                continue

            task_results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in task_results:
                if isinstance(r, Exception):
                    logger.warning("Task error: %s", r)
                    continue
                if r is not None:
                    state.matches.append(r)
                    state.stopped_reason = REASON_MATCH_FOUND
                    if len(state.matches) >= max_results:
                        break
            if len(state.matches) >= max_results:
                break

        if not state.matches and state.stopped_reason == REASON_EXHAUSTED:
            state.stopped_reason = REASON_MAX_PAGES if state.pages_crawled >= max_pages else REASON_EXHAUSTED

    # Sort matches by similarity descending; sort near_misses the same way
    state.matches.sort(key=lambda m: m.similarity_score, reverse=True)
    state.near_misses.sort(key=lambda n: n.similarity, reverse=True)

    elapsed = time.monotonic() - state.start_time
    search_stats = SearchStats(
        pages_crawled=state.pages_crawled,
        pdfs_analyzed=state.pdfs_analyzed,
        time_elapsed_seconds=round(elapsed, 2),
        search_stopped_reason=state.stopped_reason,
    )
    return state.matches, state.near_misses, search_stats


def search_pdf(
    website_url: str,
    reference_pdf_path: str,
    *,
    similarity_threshold: float | None = None,
    max_pages: int | None = None,
    max_depth: int | None = None,
    max_results: int = 1,
) -> tuple[list[MatchResult], list[NearMiss], SearchStats]:
    """Synchronous wrapper for run_search. Use from scripts or non-async code.

    Returns (matches, near_misses, search_stats). Matches are sorted by similarity descending.
    """
    return asyncio.run(
        run_search(
            website_url,
            reference_pdf_path,
            similarity_threshold=similarity_threshold,
            max_pages=max_pages,
            max_depth=max_depth,
            max_results=max_results,
        )
    )
