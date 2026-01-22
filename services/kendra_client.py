"""
AWS Kendra Client Service

Core wrapper for AWS Kendra operations including document indexing,
search queries, and similar document discovery.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List, Dict, Any
import structlog

import boto3
from botocore.exceptions import ClientError, BotoCoreError

from config import settings

logger = structlog.get_logger()


@dataclass
class IndexDocumentResult:
    """Result of indexing a document."""
    success: bool
    document_id: Optional[str] = None
    error: Optional[str] = None


@dataclass
class SearchResult:
    """Single search result from Kendra."""
    document_id: str
    title: Optional[str] = None
    excerpt: Optional[str] = None
    relevance_score: Optional[float] = None
    metadata: Dict[str, Any] = None
    url_id: Optional[int] = None
    version_id: Optional[int] = None
    source_uri: Optional[str] = None  # URL from _source_uri attribute


@dataclass
class SearchResponse:
    """Response from Kendra search query."""
    success: bool
    results: List[SearchResult] = None
    total_results: int = 0
    error: Optional[str] = None


class KendraClient:
    """
    AWS Kendra client wrapper for document indexing and search operations.
    """
    
    def __init__(
        self,
        index_id: Optional[str] = None,
        aws_region: Optional[str] = None
    ):
        """
        Initialize Kendra client.
        
        Args:
            index_id: Kendra index ID. Uses config default if not provided.
            aws_region: AWS region. Uses config default if not provided.
        """
        self.index_id = index_id or settings.AWS_KENDRA_INDEX_ID
        self.aws_region = aws_region or settings.AWS_REGION
        
        # Lazy initialization - client will be created on first use
        self._client = None
        self._initialized = False
    
    def _get_client(self):
        """Get or create the boto3 Kendra client (lazy initialization)."""
        # Update index_id and region from settings in case they changed
        current_index_id = self.index_id or settings.AWS_KENDRA_INDEX_ID
        current_region = self.aws_region or settings.AWS_REGION
        
        # Reinitialize if index_id changed or client doesn't exist
        if (self._client is None or 
            current_index_id != self.index_id or 
            current_region != self.aws_region or
            not self._initialized):
            
            self.index_id = current_index_id
            self.aws_region = current_region
            
            if self.index_id:
                try:
                    client_kwargs = {'service_name': 'kendra', 'region_name': self.aws_region}
                    if (settings.AWS_ACCESS_KEY_ID and settings.AWS_ACCESS_KEY_ID.strip() and 
                        settings.AWS_SECRET_ACCESS_KEY and settings.AWS_SECRET_ACCESS_KEY.strip()):
                        # Only use explicit credentials if they look valid (non-empty)
                        client_kwargs['aws_access_key_id'] = settings.AWS_ACCESS_KEY_ID
                        client_kwargs['aws_secret_access_key'] = settings.AWS_SECRET_ACCESS_KEY
                    # Otherwise, boto3 will use default credential chain (SSO, IAM role, etc.)
                    
                    self._client = boto3.client(**client_kwargs)
                    self._initialized = True
                    logger.info(
                        "KendraClient initialized",
                        index_id=self.index_id,
                        region=self.aws_region
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to initialize Kendra client",
                        error=str(e),
                        index_id=self.index_id
                    )
                    self._client = None
                    self._initialized = False
            else:
                logger.info(
                    "KendraClient not initialized - missing index ID",
                    has_index_id=bool(self.index_id)
                )
                self._client = None
                self._initialized = False
        
        return self._client
    
    @property
    def client(self):
        """Property to get the boto3 client (lazy initialization)."""
        return self._get_client()
    
    def _has_aws_credentials(self) -> bool:
        """Check if AWS credentials are explicitly configured (or available via default chain)."""
        # If explicit credentials are set, use them. Otherwise, boto3 will use default credential chain.
        return True  # Always return True - boto3 will handle credential resolution
    
    def is_available(self) -> bool:
        """Check if Kendra client is available and configured."""
        return self._get_client() is not None and (self.index_id or settings.AWS_KENDRA_INDEX_ID) is not None
    
    def index_document(
        self,
        document_id: str,
        pdf_path: Path,
        metadata: Optional[Dict[str, Any]] = None,
        title: Optional[str] = None,
        content_type: str = "PDF"
    ) -> IndexDocumentResult:
        """
        Index a PDF document in Kendra.
        
        Args:
            document_id: Unique document ID (format: url_{url_id}_version_{version_id})
            pdf_path: Path to PDF file
            title: Document title
            metadata: Additional metadata (form_number, state, url_id, version_id, etc.)
            content_type: Document content type (default: "PDF")
            
        Returns:
            IndexDocumentResult with success status and document ID or error
        """
        if not self.is_available():
            return IndexDocumentResult(
                success=False,
                error="Kendra client not available - check AWS credentials and index ID"
            )
        
        if not pdf_path.exists():
            return IndexDocumentResult(
                success=False,
                error=f"PDF file not found: {pdf_path}"
            )
        
        try:
            # Read PDF file
            with open(pdf_path, 'rb') as f:
                pdf_content = f.read()
            
            # Prepare document attributes
            # Only use built-in Kendra attributes that don't require configuration
            # Custom metadata attributes (url_id, version_id, etc.) require index configuration
            # Instead, we store this info in the document_id (format: url_{url_id}_version_{version_id})
            attributes = []
            
            # Use _source_uri for the document URL or title (built-in attribute)
            if metadata and 'url' in metadata and metadata['url']:
                attributes.append({
                    'Key': '_source_uri',
                    'Value': {'StringValue': str(metadata['url'])}
                })
            elif title:
                # Fallback to title if no URL in metadata
                attributes.append({
                    'Key': '_source_uri',
                    'Value': {'StringValue': title}
                })
            
            # Note: Custom metadata attributes (url_id, version_id, form_number, state, etc.)
            # are not included because they require index configuration in Kendra.
            # The document_id already contains url_id and version_id: url_{url_id}_version_{version_id}
            # We can parse this later when retrieving search results.
            
            # Index document using BatchPutDocument API
            document = {
                'Id': document_id,
                'Title': title or document_id,
                'ContentType': content_type,
                'Blob': pdf_content
            }
            
            # Only add Attributes if we have any (Kendra requires at least one attribute if specified)
            if attributes:
                document['Attributes'] = attributes
            
            response = self.client.batch_put_document(
                IndexId=self.index_id,
                Documents=[document]
            )
            
            # Check for failed documents
            failed_docs = response.get('FailedDocuments', [])
            if failed_docs:
                error_msg = failed_docs[0].get('ErrorMessage', 'Unknown error')
                logger.error(
                    "Failed to index document in Kendra",
                    document_id=document_id,
                    error=error_msg
                )
                return IndexDocumentResult(
                    success=False,
                    error=error_msg
                )
            
            logger.info(
                "Document indexed in Kendra",
                document_id=document_id,
                pdf_path=str(pdf_path)
            )
            
            return IndexDocumentResult(
                success=True,
                document_id=document_id
            )
            
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_msg = e.response.get('Error', {}).get('Message', str(e))
            logger.error(
                "Kendra API error",
                document_id=document_id,
                error_code=error_code,
                error=error_msg
            )
            return IndexDocumentResult(
                success=False,
                error=f"Kendra API error ({error_code}): {error_msg}"
            )
        except Exception as e:
            logger.error(
                "Unexpected error indexing document",
                document_id=document_id,
                error=str(e)
            )
            return IndexDocumentResult(
                success=False,
                error=f"Unexpected error: {str(e)}"
            )
    
    def search(
        self,
        query: str,
        filters: Optional[Dict[str, Any]] = None,
        max_results: int = 10
    ) -> SearchResponse:
        """
        Perform semantic search query on Kendra index.
        
        Args:
            query: Natural language search query
            filters: Optional filters (e.g., state, domain_category)
            max_results: Maximum number of results to return
            
        Returns:
            SearchResponse with results and metadata
        """
        if not self.is_available():
            return SearchResponse(
                success=False,
                error="Kendra client not available - check AWS credentials and index ID"
            )
        
        try:
            # Build query request
            query_request = {
                'IndexId': self.index_id,
                'QueryText': query,
                'PageSize': min(max_results, 100)  # Kendra max is 100
            }
            
            # Add attribute filters if provided
            if filters:
                attribute_filter = {
                    'AndAllFilters': []
                }
                
                for key, value in filters.items():
                    attribute_filter['AndAllFilters'].append({
                        'EqualsTo': {
                            'Key': key,
                            'Value': {'StringValue': str(value)}
                        }
                    })
                
                query_request['AttributeFilter'] = attribute_filter
            
            # Execute query
            response = self.client.query(**query_request)
            
            # Parse results
            results = []
            for item in response.get('ResultItems', []):
                doc_id = item.get('Id', '')
                
                # Extract metadata
                metadata = {}
                url_id = None
                version_id = None
                source_uri = None
                
                # First, try to extract from document_id (this is our primary source)
                # Document ID format: url_{url_id}_version_{version_id}
                parts = doc_id.split('_')
                if len(parts) >= 4 and parts[0] == 'url' and parts[2] == 'version':
                    try:
                        url_id = int(parts[1])
                        version_id = int(parts[3])
                    except (ValueError, IndexError):
                        pass
                
                # Extract document attributes (including _source_uri which contains the URL)
                for attr in item.get('DocumentAttributes', []):
                    key = attr.get('Key', '')
                    value = attr.get('Value', {}).get('StringValue', '')
                    metadata[key] = value
                    
                    # Extract source URI (the actual URL)
                    if key == '_source_uri':
                        source_uri = value
                    
                    # Extract IDs from metadata if not already extracted from document_id
                    if key == 'url_id' and not url_id:
                        try:
                            url_id = int(value)
                        except (ValueError, TypeError):
                            pass
                    elif key == 'version_id' and not version_id:
                        try:
                            version_id = int(value)
                        except (ValueError, TypeError):
                            pass
                
                # Extract relevance score - Kendra returns ScoreConfidence as a string enum
                score_confidence = item.get('ScoreAttributes', {}).get('ScoreConfidence', 'NOT_AVAILABLE')
                # Map Kendra confidence levels to numeric scores (0.0 to 1.0)
                confidence_map = {
                    'VERY_HIGH': 0.95,
                    'HIGH': 0.75,
                    'MEDIUM': 0.50,
                    'LOW': 0.25,
                    'NOT_AVAILABLE': 0.0
                }
                relevance_score = confidence_map.get(score_confidence, 0.0)
                
                results.append(SearchResult(
                    document_id=doc_id,
                    title=item.get('DocumentTitle', {}).get('Text', ''),
                    excerpt=item.get('DocumentExcerpt', {}).get('Text', ''),
                    relevance_score=relevance_score,
                    metadata=metadata,
                    url_id=url_id,
                    version_id=version_id,
                    source_uri=source_uri
                ))
            
            logger.info(
                "Kendra search completed",
                query=query,
                result_count=len(results),
                sample_doc_ids=[r.document_id for r in results[:3]] if results else []
            )
            
            return SearchResponse(
                success=True,
                results=results,
                total_results=len(results)
            )
            
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_msg = e.response.get('Error', {}).get('Message', str(e))
            logger.error(
                "Kendra search error",
                query=query,
                error_code=error_code,
                error=error_msg
            )
            return SearchResponse(
                success=False,
                error=f"Kendra API error ({error_code}): {error_msg}"
            )
        except Exception as e:
            logger.error(
                "Unexpected error during search",
                query=query,
                error=str(e)
            )
            return SearchResponse(
                success=False,
                error=f"Unexpected error: {str(e)}"
            )
    
    def find_similar(
        self,
        document_id: str,
        max_results: int = 10
    ) -> SearchResponse:
        """
        Find similar documents to a given document.
        
        Uses Kendra's semantic search to find documents similar to the specified one.
        
        Args:
            document_id: Document ID to find similar documents for
            max_results: Maximum number of similar documents to return
            
        Returns:
            SearchResponse with similar documents (excluding the original)
        """
        if not self.is_available():
            return SearchResponse(
                success=False,
                error="Kendra client not available"
            )
        
        # First, get the document to extract its content/metadata for similarity search
        # For now, we'll use a workaround: search for documents with similar metadata
        # In a full implementation, we might use Kendra's more-advanced features
        
        # Extract URL ID and version ID from document_id
        url_id = None
        version_id = None
        parts = document_id.split('_')
        if len(parts) >= 4 and parts[0] == 'url' and parts[2] == 'version':
            try:
                url_id = int(parts[1])
                version_id = int(parts[3])
            except (ValueError, IndexError):
                pass
        
        if not url_id:
            return SearchResponse(
                success=False,
                error=f"Invalid document_id format: {document_id}"
            )
        
        # Use a generic search query that will find similar documents
        # This is a simplified approach - in production, you might want to
        # use more sophisticated similarity matching
        query = f"document similar to {document_id}"
        
        response = self.search(query, max_results=max_results + 1)
        
        if not response.success:
            return response
        
        # Filter out the original document
        filtered_results = [
            r for r in response.results
            if r.document_id != document_id
        ][:max_results]
        
        return SearchResponse(
            success=True,
            results=filtered_results,
            total_results=len(filtered_results)
        )
    
    def delete_document(self, document_id: str) -> bool:
        """
        Delete a document from Kendra index.
        
        Args:
            document_id: Document ID to delete
            
        Returns:
            True if successful, False otherwise
        """
        if not self.is_available():
            logger.warning(
                "Cannot delete document - Kendra client not available",
                document_id=document_id
            )
            return False
        
        try:
            self.client.batch_delete_document(
                IndexId=self.index_id,
                DocumentIdList=[document_id]
            )
            
            logger.info(
                "Document deleted from Kendra",
                document_id=document_id
            )
            return True
            
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_msg = e.response.get('Error', {}).get('Message', str(e))
            logger.error(
                "Failed to delete document from Kendra",
                document_id=document_id,
                error_code=error_code,
                error=error_msg
            )
            return False
        except Exception as e:
            logger.error(
                "Unexpected error deleting document",
                document_id=document_id,
                error=str(e)
            )
            return False
    
    def get_index_status(self) -> Dict[str, Any]:
        """
        Get status of Kendra index.
        
        Returns:
            Dictionary with index status information
        """
        if not self.is_available():
            return {
                "available": False,
                "error": "Kendra client not available"
            }
        
        try:
            response = self.client.describe_index(Id=self.index_id)
            
            return {
                "available": True,
                "status": response.get('Status', 'UNKNOWN'),
                "index_id": self.index_id,
                "name": response.get('Name', ''),
                "edition": response.get('Edition', ''),
                "created_at": response.get('CreatedAt', ''),
                "updated_at": response.get('UpdatedAt', '')
            }
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_msg = e.response.get('Error', {}).get('Message', '')
            return {
                "available": False,
                "error": f"Kendra API error ({error_code}): {error_msg}"
            }
        except Exception as e:
            return {
                "available": False,
                "error": f"Unexpected error: {str(e)}"
            }


# Singleton instance
kendra_client = KendraClient()
