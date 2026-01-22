"""
Kendra Search Service

User-facing search functionality for querying the Kendra index.
Provides natural language search with filtering and result formatting.
"""

from dataclasses import dataclass
from typing import Optional, List, Dict, Any
import structlog

from sqlalchemy.orm import Session

from config import settings
from db.models import MonitoredURL, PDFVersion
from services.kendra_client import kendra_client, SearchResponse, SearchResult

logger = structlog.get_logger()


@dataclass
class FormSearchResult:
    """Formatted search result for display."""
    url_id: int
    version_id: int
    url_name: str
    url: str
    form_number: Optional[str]
    title: Optional[str]
    excerpt: Optional[str]
    relevance_score: float
    state: Optional[str]
    domain_category: Optional[str]


@dataclass
class SearchServiceResponse:
    """Response from search service."""
    success: bool
    results: List[FormSearchResult] = None
    total_results: int = 0
    error: Optional[str] = None


class KendraSearchService:
    """
    Service for user-facing search functionality.
    """
    
    def __init__(self):
        """Initialize search service."""
        self.client = kendra_client
        
        logger.info(
            "KendraSearchService initialized",
            search_enabled=settings.KENDRA_SEARCH_ENABLED,
            kendra_available=self.client.is_available()
        )
    
    def is_enabled(self) -> bool:
        """Check if Kendra search is enabled and available."""
        return (
            settings.KENDRA_SEARCH_ENABLED and
            self.client.is_available()
        )
    
    def search(
        self,
        db: Session,
        query: str,
        state: Optional[str] = None,
        domain: Optional[str] = None,
        max_results: int = 20
    ) -> SearchServiceResponse:
        """
        Perform semantic search across all forms.
        
        Args:
            db: Database session
            query: Natural language search query
            state: Optional state filter
            domain: Optional domain category filter
            max_results: Maximum number of results
            
        Returns:
            SearchServiceResponse with formatted results
        """
        if not self.is_enabled():
            return SearchServiceResponse(
                success=False,
                error="Kendra search is not enabled or not available"
            )
        
        if not query or not query.strip():
            return SearchServiceResponse(
                success=False,
                error="Search query cannot be empty"
            )
        
        # Build filters
        filters = {}
        if state:
            filters['state'] = state
        if domain:
            filters['domain_category'] = domain
        
        # Execute Kendra search
        kendra_response = self.client.search(
            query=query.strip(),
            filters=filters if filters else None,
            max_results=max_results
        )
        
        if not kendra_response.success:
            return SearchServiceResponse(
                success=False,
                error=kendra_response.error
            )
        
        # Enrich results with database information
        formatted_results = []
        skipped_count = 0
        skipped_reasons = {}
        
        for result in kendra_response.results:
            # Get URL and version from database
            url = None
            version = None
            
            # Try to get URL by ID
            if result.url_id:
                url = db.query(MonitoredURL).filter(
                    MonitoredURL.id == result.url_id
                ).first()
            
            # If URL lookup failed, try to extract from document_id as fallback
            if not url and result.document_id:
                parts = result.document_id.split('_')
                if len(parts) >= 4 and parts[0] == 'url' and parts[2] == 'version':
                    try:
                        extracted_url_id = int(parts[1])
                        url = db.query(MonitoredURL).filter(
                            MonitoredURL.id == extracted_url_id
                        ).first()
                        # Update result.url_id if we found it
                        if url and not result.url_id:
                            result.url_id = extracted_url_id
                    except (ValueError, IndexError):
                        pass
            
            # Final fallback: try to look up by actual URL from source_uri
            if not url and result.source_uri:
                url = db.query(MonitoredURL).filter(
                    MonitoredURL.url == result.source_uri
                ).first()
                # Update result.url_id if we found it
                if url and not result.url_id:
                    result.url_id = url.id
            
            # Get version if we have URL
            if url and result.version_id:
                version = db.query(PDFVersion).filter(
                    PDFVersion.id == result.version_id,
                    PDFVersion.monitored_url_id == url.id
                ).first()
            
            # Skip if URL not found
            if not url:
                skipped_count += 1
                skipped_reasons['url_not_found'] = skipped_reasons.get('url_not_found', 0) + 1
                if skipped_count <= 5:  # Log first 5 for debugging
                    logger.warning(
                        "Skipping result - URL not found in database",
                        document_id=result.document_id,
                        extracted_url_id=result.url_id,
                        version_id=result.version_id
                    )
                continue
            
            # Skip if URL is disabled
            if not url.enabled:
                skipped_count += 1
                skipped_reasons['url_disabled'] = skipped_reasons.get('url_disabled', 0) + 1
                continue
            
            # Use metadata from Kendra or database
            form_number = result.metadata.get('form_number') if result.metadata else None
            if not form_number and version:
                form_number = version.form_number
            
            title = result.title
            if not title and version:
                title = version.display_title or version.formatted_title
            if not title:
                title = url.name
            
            # Ensure relevance_score is a float
            relevance_score = 0.0
            if result.relevance_score is not None:
                try:
                    relevance_score = float(result.relevance_score)
                except (ValueError, TypeError):
                    relevance_score = 0.0
            
            formatted_results.append(FormSearchResult(
                url_id=url.id,
                version_id=result.version_id or (version.id if version else 0),
                url_name=url.name,
                url=url.url,
                form_number=form_number,
                title=title,
                excerpt=result.excerpt,
                relevance_score=relevance_score,
                state=url.state,
                domain_category=url.domain_category
            ))
        
        logger.info(
            "Search completed",
            query=query,
            kendra_results=len(kendra_response.results),
            formatted_results=len(formatted_results),
            skipped=skipped_count,
            skipped_reasons=skipped_reasons
        )
        
        return SearchServiceResponse(
            success=True,
            results=formatted_results,
            total_results=len(formatted_results)
        )
    
    def find_similar_forms(
        self,
        db: Session,
        url_id: int,
        version_id: Optional[int] = None,
        max_results: int = 10
    ) -> SearchServiceResponse:
        """
        Find similar forms to a given form.
        
        Args:
            db: Database session
            url_id: Monitored URL ID
            version_id: Optional version ID (uses latest if not provided)
            max_results: Maximum number of similar forms to return
            
        Returns:
            SearchServiceResponse with similar forms
        """
        if not self.is_enabled():
            return SearchServiceResponse(
                success=False,
                error="Kendra search is not enabled or not available"
            )
        
        # Get version
        if version_id:
            version = db.query(PDFVersion).filter(
                PDFVersion.id == version_id,
                PDFVersion.monitored_url_id == url_id
            ).first()
        else:
            # Get latest version
            version = db.query(PDFVersion).filter(
                PDFVersion.monitored_url_id == url_id
            ).order_by(PDFVersion.version_number.desc()).first()
        
        if not version:
            return SearchServiceResponse(
                success=False,
                error=f"Version not found for URL {url_id}"
            )
        
        # Get document ID
        document_id = version.kendra_document_id
        if not document_id:
            document_id = f"url_{url_id}_version_{version.id}"
        
        # Find similar documents
        kendra_response = self.client.find_similar(
            document_id=document_id,
            max_results=max_results + 1  # +1 to account for filtering out original
        )
        
        if not kendra_response.success:
            return SearchServiceResponse(
                success=False,
                error=kendra_response.error
            )
        
        # Enrich results with database information
        formatted_results = []
        for result in kendra_response.results:
            # Skip the original form
            if result.url_id == url_id:
                continue
            
            # Get URL from database
            url = db.query(MonitoredURL).filter(
                MonitoredURL.id == result.url_id
            ).first()
            
            # Skip if URL is disabled or not found
            if not url or not url.enabled:
                continue
            
            # Get version
            version_result = None
            if result.version_id:
                version_result = db.query(PDFVersion).filter(
                    PDFVersion.id == result.version_id,
                    PDFVersion.monitored_url_id == url.id
                ).first()
            
            # Use metadata from Kendra or database
            form_number = result.metadata.get('form_number') if result.metadata else None
            if not form_number and version_result:
                form_number = version_result.form_number
            
            title = result.title
            if not title and version_result:
                title = version_result.display_title or version_result.formatted_title
            if not title:
                title = url.name
            
            formatted_results.append(FormSearchResult(
                url_id=url.id,
                version_id=result.version_id or (version_result.id if version_result else 0),
                url_name=url.name,
                url=url.url,
                form_number=form_number,
                title=title,
                excerpt=result.excerpt,
                relevance_score=result.relevance_score or 0.0,
                state=url.state,
                domain_category=url.domain_category
            ))
        
        # Limit results
        formatted_results = formatted_results[:max_results]
        
        logger.info(
            "Similar forms found",
            url_id=url_id,
            version_id=version.id,
            result_count=len(formatted_results)
        )
        
        return SearchServiceResponse(
            success=True,
            results=formatted_results,
            total_results=len(formatted_results)
        )


# Singleton instance
kendra_search_service = KendraSearchService()
