"""Pydantic models for search results (returned by run_search / search_pdf)."""
from __future__ import annotations

from pydantic import BaseModel


class MatchResult(BaseModel):
    """A PDF that met the similarity threshold."""

    pdf_url: str
    similarity_score: float
    file_size_mb: float
    discovered_at: str  # ISO 8601


class NearMiss(BaseModel):
    """A PDF in the near-miss similarity range (e.g. 80–89%)."""

    url: str
    similarity: float


class SearchStats(BaseModel):
    """Statistics about the search run."""

    pages_crawled: int
    pdfs_analyzed: int
    time_elapsed_seconds: float
    search_stopped_reason: str
