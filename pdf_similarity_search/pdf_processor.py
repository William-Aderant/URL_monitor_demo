"""PDF download, validation, and text extraction for similarity comparison."""
from __future__ import annotations

import io
import logging
from pathlib import Path

import httpx

from .config import get_settings
from .url_utils import validate_url_safe

logger = logging.getLogger(__name__)

PDF_MAGIC = b"%PDF"
PDF_MAGIC_LEN = len(PDF_MAGIC)


async def download_pdf_stream(
    client: httpx.AsyncClient,
    url: str,
    *,
    timeout: float | None = None,
    max_size_bytes: int | None = None,
) -> tuple[bytes, int]:
    """
    Download a PDF with streaming and validate magic bytes and size.

    Args:
        client: Async HTTP client.
        url: PDF URL.
        timeout: Request timeout in seconds.
        max_size_bytes: Skip download if Content-Length exceeds this; also truncate read.

    Returns:
        (pdf_bytes, content_length_or_actual_size).
        If file exceeds max_size_bytes, raises ValueError.

    Raises:
        ValueError: If response is not a valid PDF or exceeds size.
        httpx.HTTPError: On HTTP errors.
    """
    validate_url_safe(url)
    settings = get_settings()
    timeout = timeout if timeout is not None else settings.request_timeout
    max_size = max_size_bytes if max_size_bytes is not None else settings.max_pdf_size_bytes

    resp = await client.get(
        url,
        follow_redirects=True,
        timeout=timeout,
    )
    resp.raise_for_status()

    content_length = resp.headers.get("content-length")
    if content_length and int(content_length) > max_size:
        raise ValueError(f"PDF too large: {int(content_length)} > {max_size}")

    chunks: list[bytes] = []
    total = 0
    async for chunk in resp.aiter_bytes():
        total += len(chunk)
        if total > max_size:
            raise ValueError(f"PDF exceeds max size: {total} > {max_size}")
        chunks.append(chunk)

    body = b"".join(chunks)
    if len(body) < PDF_MAGIC_LEN or body[:PDF_MAGIC_LEN] != PDF_MAGIC:
        raise ValueError("Invalid PDF: missing or wrong magic bytes")
    return body, total


def extract_text_from_pdf_bytes(data: bytes) -> str:
    """Extract text from PDF bytes using pypdf."""
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    parts = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            parts.append(text)
    return "\n".join(parts).strip() or ""


def get_reference_text(reference_pdf_path: str) -> str:
    """Read reference PDF from disk and return extracted text for similarity comparison."""
    path = Path(reference_pdf_path)
    if not path.is_file():
        raise FileNotFoundError(f"Reference PDF not found: {reference_pdf_path}")
    settings = get_settings()
    max_size = settings.max_pdf_size_bytes
    data = path.read_bytes()
    if len(data) > max_size:
        raise ValueError(f"Reference PDF too large: {len(data)} > {max_size}")
    if len(data) < PDF_MAGIC_LEN or data[:PDF_MAGIC_LEN] != PDF_MAGIC:
        raise ValueError("Reference file is not a valid PDF (wrong magic bytes)")
    return extract_text_from_pdf_bytes(data)
