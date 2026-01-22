"""
Kendra Indexer Service

Manages document indexing lifecycle for PDF versions in AWS Kendra.
Handles indexing new versions, updates, deletions, and batch operations.
"""

from datetime import datetime
from pathlib import Path
from typing import Optional, List
import structlog
from concurrent.futures import ThreadPoolExecutor, as_completed

from sqlalchemy.orm import Session

from config import settings
from db.models import PDFVersion, MonitoredURL
from db.database import SessionLocal
from storage.file_store import FileStore
from services.kendra_client import kendra_client, IndexDocumentResult

logger = structlog.get_logger()


class KendraIndexer:
    """
    Service for managing document indexing in AWS Kendra.
    """
    
    def __init__(self, file_store: Optional[FileStore] = None):
        """
        Initialize Kendra indexer.
        
        Args:
            file_store: FileStore instance. Creates new one if not provided.
        """
        self.file_store = file_store or FileStore()
        self.client = kendra_client
        
        logger.info(
            "KendraIndexer initialized",
            indexing_enabled=settings.KENDRA_INDEXING_ENABLED,
            kendra_available=self.client.is_available()
        )
    
    def is_enabled(self) -> bool:
        """Check if Kendra indexing is enabled and available."""
        return (
            settings.KENDRA_INDEXING_ENABLED and
            self.client.is_available()
        )
    
    def index_version(
        self,
        db: Session,
        version_id: int,
        force: bool = False
    ) -> IndexDocumentResult:
        """
        Index a PDF version in Kendra.
        
        Args:
            db: Database session
            version_id: PDF version ID to index
            force: If True, re-index even if already indexed
            
        Returns:
            IndexDocumentResult with success status
        """
        if not self.is_enabled():
            return IndexDocumentResult(
                success=False,
                error="Kendra indexing is not enabled or not available"
            )
        
        # Get version from database
        version = db.query(PDFVersion).filter(PDFVersion.id == version_id).first()
        if not version:
            return IndexDocumentResult(
                success=False,
                error=f"Version {version_id} not found"
            )
        
        # Check if already indexed (unless forcing)
        if not force and version.kendra_index_status == "indexed" and version.kendra_document_id:
            logger.debug(
                "Version already indexed, skipping",
                version_id=version_id,
                document_id=version.kendra_document_id
            )
            return IndexDocumentResult(
                success=True,
                document_id=version.kendra_document_id
            )
        
        # Get monitored URL for metadata
        url = db.query(MonitoredURL).filter(MonitoredURL.id == version.monitored_url_id).first()
        if not url:
            return IndexDocumentResult(
                success=False,
                error=f"Monitored URL {version.monitored_url_id} not found"
            )
        
        # Get PDF path
        pdf_path = self.file_store.get_original_pdf(
            version.monitored_url_id,
            version_id
        )
        if not pdf_path or not pdf_path.exists():
            return IndexDocumentResult(
                success=False,
                error=f"PDF file not found for version {version_id}"
            )
        
        # Generate document ID
        document_id = f"url_{version.monitored_url_id}_version_{version_id}"
        
        # Prepare metadata
        metadata = {
            'url_id': str(version.monitored_url_id),
            'version_id': str(version_id),
            'form_number': version.form_number or '',
            'state': url.state or '',
            'domain_category': url.domain_category or '',
            'url_name': url.name or '',
            'url': url.url or '',
        }
        
        # Add title if available
        title = version.display_title or version.formatted_title or url.name or document_id
        
        # Update status to pending
        version.kendra_index_status = "pending"
        version.kendra_document_id = document_id
        db.commit()
        
        # Index document
        result = self.client.index_document(
            document_id=document_id,
            pdf_path=pdf_path,
            metadata=metadata,
            title=title
        )
        
        # Update database with result
        if result.success:
            version.kendra_index_status = "indexed"
            version.kendra_indexed_at = datetime.utcnow()
            version.kendra_document_id = result.document_id
            logger.info(
                "Version indexed in Kendra",
                version_id=version_id,
                document_id=result.document_id
            )
        else:
            version.kendra_index_status = "failed"
            logger.error(
                "Failed to index version in Kendra",
                version_id=version_id,
                error=result.error
            )
        
        db.commit()
        
        return result
    
    def index_url_versions(
        self,
        db: Session,
        url_id: int,
        latest_only: bool = False
    ) -> dict:
        """
        Index all versions for a monitored URL.
        
        Args:
            db: Database session
            url_id: Monitored URL ID
            latest_only: If True, only index the latest version
            
        Returns:
            Dictionary with indexing statistics
        """
        if not self.is_enabled():
            return {
                "success": False,
                "error": "Kendra indexing is not enabled",
                "indexed": 0,
                "failed": 0
            }
        
        # Get versions
        query = db.query(PDFVersion).filter(
            PDFVersion.monitored_url_id == url_id
        )
        
        if latest_only:
            query = query.order_by(PDFVersion.version_number.desc()).limit(1)
        
        versions = query.all()
        
        indexed = 0
        failed = 0
        errors = []
        
        for version in versions:
            result = self.index_version(db, version.id)
            if result.success:
                indexed += 1
            else:
                failed += 1
                errors.append({
                    "version_id": version.id,
                    "error": result.error
                })
        
        return {
            "success": failed == 0,
            "indexed": indexed,
            "failed": failed,
            "errors": errors
        }
    
    def _index_url_with_session(
        self,
        url_id: int,
        url_name: str,
        latest_only: bool
    ) -> dict:
        """
        Helper function to index a single URL with its own database session.
        Used for parallel processing.
        
        Args:
            url_id: Monitored URL ID
            url_name: URL name (for logging)
            latest_only: If True, only index latest version
            
        Returns:
            Dictionary with indexing result for this URL
        """
        db = SessionLocal()
        try:
            result = self.index_url_versions(db, url_id, latest_only=latest_only)
            return {
                "url_id": url_id,
                "url_name": url_name,
                **result
            }
        except Exception as e:
            logger.error(
                "Error indexing URL versions",
                url_id=url_id,
                url_name=url_name,
                error=str(e)
            )
            return {
                "url_id": url_id,
                "url_name": url_name,
                "success": False,
                "error": str(e),
                "indexed": 0,
                "failed": 0
            }
        finally:
            db.close()
    
    def index_all_versions(
        self,
        db: Session,
        latest_only: bool = True,
        max_workers: Optional[int] = None
    ) -> dict:
        """
        Index all PDF versions in the database.
        
        Args:
            db: Database session (used only to get URL list)
            latest_only: If True, only index latest version per URL
            max_workers: Maximum number of parallel workers (default: from config)
            
        Returns:
            Dictionary with indexing statistics
        """
        if not self.is_enabled():
            return {
                "success": False,
                "error": "Kendra indexing is not enabled",
                "total": 0,
                "indexed": 0,
                "failed": 0
            }
        
        # Get all URLs
        urls = db.query(MonitoredURL).filter(MonitoredURL.enabled == True).all()
        
        if not urls:
            return {
                "success": True,
                "total_urls": 0,
                "indexed": 0,
                "failed": 0,
                "url_results": []
            }
        
        # Determine number of workers
        if max_workers is None:
            max_workers = min(settings.MAX_WORKERS, len(urls))
        
        total_indexed = 0
        total_failed = 0
        url_results = []
        
        # Process URLs in parallel if we have multiple URLs and max_workers > 1
        if len(urls) > 1 and max_workers > 1:
            logger.info(
                "Indexing URLs in parallel",
                total=len(urls),
                workers=max_workers
            )
            
            # Prepare URL data for parallel processing
            url_data_list = [(url.id, url.name) for url in urls]
            
            # Process in parallel
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_url = {
                    executor.submit(self._index_url_with_session, url_id, url_name, latest_only): url_id
                    for url_id, url_name in url_data_list
                }
                
                for future in as_completed(future_to_url):
                    try:
                        result = future.result()
                        total_indexed += result.get("indexed", 0)
                        total_failed += result.get("failed", 0)
                        url_results.append(result)
                    except Exception as e:
                        url_id_key = future_to_url[future]
                        logger.error(
                            "Error getting result from indexing thread",
                            url_id=url_id_key,
                            error=str(e)
                        )
                        total_failed += 1
                        url_results.append({
                            "url_id": url_id_key,
                            "url_name": "Unknown",
                            "success": False,
                            "error": str(e),
                            "indexed": 0,
                            "failed": 0
                        })
        else:
            # Process sequentially (single URL or max_workers = 1)
            logger.info("Indexing URLs sequentially", total=len(urls))
            for url in urls:
                result = self.index_url_versions(db, url.id, latest_only=latest_only)
                total_indexed += result.get("indexed", 0)
                total_failed += result.get("failed", 0)
                url_results.append({
                    "url_id": url.id,
                    "url_name": url.name,
                    **result
                })
        
        return {
            "success": total_failed == 0,
            "total_urls": len(urls),
            "indexed": total_indexed,
            "failed": total_failed,
            "url_results": url_results
        }
    
    def delete_version(
        self,
        db: Session,
        version_id: int
    ) -> bool:
        """
        Delete a version from Kendra index.
        
        Args:
            db: Database session
            version_id: PDF version ID to delete
            
        Returns:
            True if successful, False otherwise
        """
        if not self.is_enabled():
            logger.warning(
                "Cannot delete version - Kendra indexing not enabled",
                version_id=version_id
            )
            return False
        
        # Get version
        version = db.query(PDFVersion).filter(PDFVersion.id == version_id).first()
        if not version:
            logger.warning(
                "Version not found for deletion",
                version_id=version_id
            )
            return False
        
        # Delete from Kendra if document ID exists
        if version.kendra_document_id:
            success = self.client.delete_document(version.kendra_document_id)
            if success:
                # Clear tracking fields
                version.kendra_document_id = None
                version.kendra_index_status = None
                version.kendra_indexed_at = None
                db.commit()
                logger.info(
                    "Version deleted from Kendra",
                    version_id=version_id,
                    document_id=version.kendra_document_id
                )
            return success
        
        return True  # Already not indexed
    
    def delete_url_versions(
        self,
        db: Session,
        url_id: int
    ) -> dict:
        """
        Delete all versions for a URL from Kendra index.
        
        Args:
            db: Database session
            url_id: Monitored URL ID
            
        Returns:
            Dictionary with deletion statistics
        """
        if not self.is_enabled():
            return {
                "success": False,
                "error": "Kendra indexing is not enabled",
                "deleted": 0,
                "failed": 0
            }
        
        # Get all versions
        versions = db.query(PDFVersion).filter(
            PDFVersion.monitored_url_id == url_id
        ).all()
        
        deleted = 0
        failed = 0
        
        for version in versions:
            if self.delete_version(db, version.id):
                deleted += 1
            else:
                failed += 1
        
        return {
            "success": failed == 0,
            "deleted": deleted,
            "failed": failed
        }
    
    def sync_index_with_database(
        self,
        db: Session
    ) -> dict:
        """
        Sync Kendra index with database.
        
        Ensures all versions marked as indexed are actually in Kendra,
        and re-indexes any that are missing.
        
        Args:
            db: Database session
            
        Returns:
            Dictionary with sync statistics
        """
        if not self.is_enabled():
            return {
                "success": False,
                "error": "Kendra indexing is not enabled"
            }
        
        # Get all versions marked as indexed
        indexed_versions = db.query(PDFVersion).filter(
            PDFVersion.kendra_index_status == "indexed"
        ).all()
        
        re_indexed = 0
        missing = 0
        
        for version in indexed_versions:
            # Try to verify document exists (simplified - just re-index)
            # In production, you might want to check document existence first
            result = self.index_version(db, version.id, force=True)
            if result.success:
                re_indexed += 1
            else:
                missing += 1
        
        return {
            "success": True,
            "checked": len(indexed_versions),
            "re_indexed": re_indexed,
            "missing": missing
        }


# Singleton instance
kendra_indexer = KendraIndexer()
