"""
AWS-based web scraper using Lambda for web scraping and PDF URL extraction.

Uses AWS Lambda function to scrape web pages and extract PDF links.
Falls back to direct HTTP requests if Lambda function is not configured.
"""

import re
import json
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urljoin, urlparse
import structlog
import boto3
from botocore.exceptions import ClientError, BotoCoreError
import httpx

from config import settings

logger = structlog.get_logger()


@dataclass
class ScrapeResult:
    """Result of a web scraping operation."""
    success: bool
    url: str
    final_url: Optional[str] = None  # After redirects
    content_type: Optional[str] = None
    pdf_url: Optional[str] = None  # Extracted PDF URL if page contains PDF link
    html_content: Optional[str] = None
    markdown_content: Optional[str] = None
    error: Optional[str] = None


class AWSWebScraper:
    """
    AWS-based web scraper using Lambda for web scraping and PDF URL extraction.
    
    Uses AWS Lambda function to scrape web pages. If Lambda function is not
    configured, falls back to direct HTTP requests using httpx.
    """
    
    # Common patterns for PDF links on court websites
    PDF_LINK_PATTERNS = [
        r'href=["\']([^"\']+\.pdf)["\']',
        r'href=["\']([^"\']+/documents/[^"\']+)["\']',
        r'href=["\']([^"\']+download[^"\']*\.pdf)["\']',
        r'href=["\']([^"\']+/pdf/[^"\']+)["\']',
        r'href=["\']([^"\']+/file/[^"\']+\.pdf)["\']',
    ]
    
    def __init__(
        self,
        lambda_function_name: Optional[str] = None,
        aws_region: Optional[str] = None
    ):
        """
        Initialize AWS web scraper.
        
        Args:
            lambda_function_name: Name of AWS Lambda function for web scraping.
                                 If not provided, uses direct HTTP requests.
            aws_region: AWS region. Uses settings default if not provided.
        """
        self.lambda_function_name = lambda_function_name or settings.AWS_LAMBDA_SCRAPER_FUNCTION
        self.aws_region = aws_region or settings.AWS_REGION
        
        # Initialize boto3 Lambda client if AWS credentials are available
        self.lambda_client = None
        if self.lambda_function_name and self._has_aws_credentials():
            try:
                self.lambda_client = boto3.client(
                    'lambda',
                    region_name=self.aws_region,
                    aws_access_key_id=settings.AWS_ACCESS_KEY_ID or None,
                    aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY or None
                )
                logger.info(
                    "AWS Lambda client initialized",
                    function_name=self.lambda_function_name,
                    region=self.aws_region
                )
            except Exception as e:
                logger.warning(
                    "Failed to initialize Lambda client, will use direct HTTP",
                    error=str(e)
                )
                self.lambda_client = None
        
        # Initialize HTTP client for fallback
        self.http_client = httpx.Client(
            timeout=30.0,
            follow_redirects=True,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
        )
        
        logger.info("AWSWebScraper initialized", use_lambda=self.lambda_client is not None)
    
    def _has_aws_credentials(self) -> bool:
        """Check if AWS credentials are available."""
        return bool(settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY)
    
    def scrape_url(self, url: str) -> ScrapeResult:
        """
        Scrape a URL using AWS Lambda or direct HTTP.
        
        If the URL is a direct PDF link, returns the URL directly.
        If it's a web page, attempts to extract PDF links from the content.
        
        Args:
            url: URL to scrape
            
        Returns:
            ScrapeResult with scrape details
        """
        logger.info("Scraping URL", url=url, use_lambda=self.lambda_client is not None)
        
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
        
        # Try Lambda first if available
        if self.lambda_client:
            result = self._scrape_with_lambda(url)
            if result.success:
                return result
            # Fall back to direct HTTP if Lambda fails
            logger.warning("Lambda scrape failed, falling back to direct HTTP", url=url)
        
        # Use direct HTTP scraping
        return self._scrape_with_http(url)
    
    def _scrape_with_lambda(self, url: str) -> ScrapeResult:
        """
        Scrape URL using AWS Lambda function.
        
        Args:
            url: URL to scrape
            
        Returns:
            ScrapeResult with scrape details
        """
        try:
            payload = {
                'url': url,
                'extract_pdf_links': True
            }
            
            response = self.lambda_client.invoke(
                FunctionName=self.lambda_function_name,
                InvocationType='RequestResponse',
                Payload=json.dumps(payload)
            )
            
            # Parse Lambda response
            response_payload = json.loads(response['Payload'].read())
            
            if response_payload.get('statusCode') == 200:
                body = json.loads(response_payload.get('body', '{}'))
                html_content = body.get('html', '')
                markdown_content = body.get('markdown', '')
                metadata = body.get('metadata', {})
                
                # Extract PDF URL from HTML
                pdf_url = self._extract_pdf_url(html_content, url)
                
                return ScrapeResult(
                    success=True,
                    url=url,
                    final_url=metadata.get('final_url', url),
                    content_type=metadata.get('content_type'),
                    pdf_url=pdf_url,
                    html_content=html_content,
                    markdown_content=markdown_content
                )
            else:
                error_msg = response_payload.get('errorMessage', 'Unknown Lambda error')
                logger.error("Lambda scrape failed", url=url, error=error_msg)
                return ScrapeResult(
                    success=False,
                    url=url,
                    error=error_msg
                )
                
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            logger.error("AWS Lambda client error", url=url, error_code=error_code, error=str(e))
            return ScrapeResult(
                success=False,
                url=url,
                error=f"AWS Lambda error: {error_code}"
            )
        except Exception as e:
            logger.error("Lambda scrape exception", url=url, error=str(e))
            return ScrapeResult(
                success=False,
                url=url,
                error=str(e)
            )
    
    def _scrape_with_http(self, url: str) -> ScrapeResult:
        """
        Scrape URL using direct HTTP request.
        
        Args:
            url: URL to scrape
            
        Returns:
            ScrapeResult with scrape details
        """
        try:
            response = self.http_client.get(url)
            response.raise_for_status()
            
            html_content = response.text
            content_type = response.headers.get('content-type', '')
            final_url = str(response.url)  # httpx follows redirects
            
            # Try to find PDF links in the HTML
            pdf_url = self._extract_pdf_url(html_content, final_url)
            
            scrape_result = ScrapeResult(
                success=True,
                url=url,
                final_url=final_url,
                content_type=content_type,
                pdf_url=pdf_url,
                html_content=html_content
            )
            
            if pdf_url:
                logger.info("Found PDF URL in page", source_url=url, pdf_url=pdf_url)
            else:
                logger.warning("No PDF URL found in page", url=url)
            
            return scrape_result
            
        except httpx.HTTPError as e:
            logger.error("HTTP scrape failed", url=url, error=str(e))
            return ScrapeResult(
                success=False,
                url=url,
                error=f"HTTP error: {str(e)}"
            )
        except Exception as e:
            logger.error("HTTP scrape exception", url=url, error=str(e))
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
                    pdf_url = urljoin(base_url, pdf_url)
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
    
    def __del__(self):
        """Cleanup HTTP client."""
        if hasattr(self, 'http_client'):
            try:
                self.http_client.close()
            except Exception:
                pass
