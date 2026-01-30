# PDF Similarity Search (library)

A **Python library** for URL_monitor_demo: crawls a website for PDFs and compares them to a local reference PDF using **text-based similarity** (TF-IDF + cosine). Use by importing into your program—no API server.

## Usage

**Synchronous:**

```python
from pdf_similarity_search import search_pdf

results, near_misses, stats = search_pdf(
    "https://example.com",
    "/path/to/reference.pdf",
    similarity_threshold=90,
    max_results=5,
    max_pages=200,
    max_depth=5,
)
for m in results:
    print(m.pdf_url, m.similarity_score)
```

**Asynchronous:**

```python
from pdf_similarity_search import run_search

results, near_misses, stats = await run_search(
    "https://example.com",
    "/path/to/reference.pdf",
    similarity_threshold=90,
    max_results=5,
)
```

**Returns:** `(results, near_misses, stats)` — `results` and `near_misses` are sorted by similarity (highest first). Types: `MatchResult`, `NearMiss`, `SearchStats`.

## Parameters

| Parameter | Meaning |
|-----------|--------|
| **website_url** | Any URL on the target site. Crawl starts from this and the domain root. |
| **reference_pdf_path** | Local path to the reference PDF. |
| **similarity_threshold** | Minimum score (0–100) to count as a match. Default 90. |
| **max_results** | Number of matching PDFs to return. Default 1. |
| **max_pages** | Max HTML pages to crawl. |
| **max_depth** | Max link distance from start URL (e.g. 5 = up to 5 steps away). |

## Configuration

Environment variables (prefix `PDF_SEARCH_`): `PDF_SEARCH_MAX_PAGES`, `PDF_SEARCH_MAX_DEPTH`, `PDF_SEARCH_REQUEST_TIMEOUT`, `PDF_SEARCH_RATE_LIMIT_DELAY`, `PDF_SEARCH_MAX_PDF_SIZE_MB`, `PDF_SEARCH_CONCURRENT_DOWNLOADS`, `PDF_SEARCH_SIMILARITY_THRESHOLD`, `PDF_SEARCH_DEFAULT_REFERENCE_PDF_PATH`.

## Structure

- **search_service**: `run_search` (async), `search_pdf` (sync)
- **crawler**: BFS crawl + PDF link extraction
- **pdf_processor**: Download, validate, text extraction
- **similarity**: TF-IDF cosine (0–100)
- **models**: `MatchResult`, `NearMiss`, `SearchStats`
