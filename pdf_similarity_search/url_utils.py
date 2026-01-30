"""URL normalization, validation, and SSRF-safe domain handling."""
from __future__ import annotations

import logging
from urllib.parse import urljoin, urlparse, urlunparse

logger = logging.getLogger(__name__)

# Schemes we allow (block file:, ftp:, etc. for SSRF)
ALLOWED_SCHEMES = frozenset({"http", "https"})

# Hosts that must not be accessed (SSRF / internal networks)
BLOCKED_HOST_PREFIXES = (
    "localhost",
    "127.",
    "0.0.0.0",
    "10.",
    "172.16.",
    "172.17.",
    "172.18.",
    "172.19.",
    "172.20.",
    "172.21.",
    "172.22.",
    "172.23.",
    "172.24.",
    "172.25.",
    "172.26.",
    "172.27.",
    "172.28.",
    "172.29.",
    "172.30.",
    "172.31.",
    "192.168.",
    "169.254.",
    "::1",
    "[::",
)


def normalize_url(url: str, base: str | None = None) -> str:
    """
    Normalize a URL: remove fragment, resolve relative path, strip trailing slash from path.

    Args:
        url: URL to normalize (absolute or relative).
        base: Base URL for resolving relative URLs. If None, url must be absolute.

    Returns:
        Normalized absolute URL string.

    Raises:
        ValueError: If URL is invalid or cannot be resolved.
    """
    url = (url or "").strip()
    if not url:
        raise ValueError("URL cannot be empty")

    if base:
        url = urljoin(base, url)

    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise ValueError(f"Scheme not allowed: {scheme}")

    netloc = parsed.netloc.lower()
    if not netloc:
        raise ValueError("URL must have a host")

    # Normalize path: collapse redundant slashes conceptually via urlparse
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    # Rebuild without fragment and with normalized path
    normalized = urlunparse((scheme, netloc, path, parsed.params, parsed.query, ""))
    return normalized


def get_root_domain(url: str) -> str:
    """
    Extract root domain (scheme + netloc) from any page URL.

    Handles subdomains: https://docs.example.com/page -> https://docs.example.com
    (We keep full host for same-domain check; "root" here means base for crawling.)

    Args:
        url: Any valid HTTP(S) URL.

    Returns:
        Scheme + netloc (e.g. https://example.com).
    """
    parsed = urlparse(url)
    scheme = parsed.scheme.lower() or "https"
    netloc = (parsed.netloc or "").lower()
    if not netloc:
        raise ValueError("URL has no host")
    return f"{scheme}://{netloc}"


def get_domain_for_filter(url: str) -> str:
    """
    Get the host (netloc) from URL for same-domain filtering.

    Args:
        url: Normalized URL.

    Returns:
        Lowercase host (e.g. example.com or sub.example.com).
    """
    parsed = urlparse(url)
    return (parsed.netloc or "").lower()


def is_same_domain(url: str, allowed_netloc: str) -> bool:
    """
    Return True if url's host matches the allowed host (same domain/subdomain).

    We consider same domain as exact netloc match so we stay on the same host
    (e.g. www.example.com stays on www.example.com; example.com stays on example.com).

    Args:
        url: Candidate URL (should be normalized).
        allowed_netloc: Allowed netloc from the start URL (lowercase).

    Returns:
        True if the URL belongs to the same site.
    """
    return get_domain_for_filter(url) == allowed_netloc


def validate_url_safe(url: str) -> None:
    """
    Validate that the URL is safe to request (SSRF mitigation).

    - Only http/https.
    - No private/local/internal hosts.

    Args:
        url: URL to validate.

    Raises:
        ValueError: If URL is not safe.
    """
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise ValueError(f"Disallowed scheme: {scheme}")

    host = (parsed.netloc or "").lower()
    # Strip port for host check
    if ":" in host:
        host = host.split(":")[0]

    for prefix in BLOCKED_HOST_PREFIXES:
        if host == prefix or host.startswith(prefix):
            raise ValueError(f"Disallowed host (SSRF): {host}")

    # Reject raw IP lookalike in non-IP contexts if needed; basic check is above
    if not host:
        raise ValueError("Missing host")


def normalize_and_validate(url: str, base: str | None = None) -> str:
    """
    Normalize URL and validate it for safety (SSRF). Single entry point for crawl URLs.

    Args:
        url: Raw URL from page or input.
        base: Optional base URL for relative resolution.

    Returns:
        Normalized absolute URL.

    Raises:
        ValueError: If URL is invalid or unsafe.
    """
    normalized = normalize_url(url, base=base)
    validate_url_safe(normalized)
    return normalized
