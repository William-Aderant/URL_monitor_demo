"""
PDF Similarity Search - Web-based PDF discovery and text-similarity comparison.

Use as a library:

    from pdf_similarity_search import search_pdf

    results, near_misses, stats = search_pdf(
        "https://example.com",
        "/path/to/reference.pdf",
        similarity_threshold=90,
        max_results=5,
    )

Or async:

    from pdf_similarity_search import run_search

    results, near_misses, stats = await run_search(...)
"""

__version__ = "1.0.0"

from .models import MatchResult, NearMiss, SearchStats
from .search_service import run_search, search_pdf

__all__ = ["run_search", "search_pdf", "MatchResult", "NearMiss", "SearchStats", "__version__"]
