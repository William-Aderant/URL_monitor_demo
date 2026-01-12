"""
Version management for PDF monitoring.

Coordinates storage of PDF versions with database metadata.
"""

from datetime import datetime
from pathlib import Path
from typing import Optional
from sqlalchemy.orm import Session
import structlog

from db.models import MonitoredURL, PDFVersion, ChangeLog
from diffing.hasher import HashResult
from diffing.change_detector import ChangeResult
from storage.file_store import FileStore

logger = structlog.get_logger()


class VersionManager:
    """
    Manages PDF version lifecycle.
    
    Coordinates:
    - File storage (original, normalized, text)
    - Database records (versions, change logs)
    - Version comparison and retrieval
    """
    
    def __init__(self, file_store: Optional[FileStore] = None):
        """
        Initialize version manager.
        
        Args:
            file_store: FileStore instance. Creates default if not provided.
        """
        self.file_store = file_store or FileStore()
        logger.info("VersionManager initialized")
    
    def create_version(
        self,
        db: Session,
        monitored_url: MonitoredURL,
        original_pdf_path: Path,
        normalized_pdf_path: Path,
        extracted_text: str,
        page_texts: list[str],
        hashes: HashResult,
        extraction_method: str,
        ocr_used: bool = False
    ) -> PDFVersion:
        """
        Create a new PDF version.
        
        Stores files and creates database record.
        
        Args:
            db: Database session
            monitored_url: MonitoredURL record
            original_pdf_path: Path to original PDF
            normalized_pdf_path: Path to normalized PDF
            extracted_text: Extracted text content
            page_texts: Per-page extracted text
            hashes: Computed hash result
            extraction_method: Method used for extraction
            ocr_used: Whether OCR was used
            
        Returns:
            Created PDFVersion record
        """
        # Get next version number
        last_version = self.get_latest_version(db, monitored_url.id)
        version_number = (last_version.version_number + 1) if last_version else 1
        
        logger.info(
            "Creating new version",
            url_id=monitored_url.id,
            version_number=version_number
        )
        
        # Create placeholder version to get ID
        version = PDFVersion(
            monitored_url_id=monitored_url.id,
            version_number=version_number,
            original_pdf_path="",  # Will update after storing
            normalized_pdf_path="",
            extracted_text_path="",
            pdf_hash=hashes.pdf_hash,
            text_hash=hashes.text_hash,
            page_hashes=hashes.page_hashes,
            extraction_method=extraction_method,
            page_count=len(page_texts),
            text_length=len(extracted_text),
            ocr_used=ocr_used,
            fetched_at=datetime.utcnow()
        )
        
        db.add(version)
        db.flush()  # Get the ID
        
        # Store files
        stored_original = self.file_store.store_original_pdf(
            monitored_url.id,
            version.id,
            original_pdf_path
        )
        
        stored_normalized = self.file_store.store_normalized_pdf(
            monitored_url.id,
            version.id,
            normalized_pdf_path
        )
        
        stored_text = self.file_store.store_extracted_text(
            monitored_url.id,
            version.id,
            extracted_text
        )
        
        # Store metadata
        metadata = {
            "url": monitored_url.url,
            "version_number": version_number,
            "pdf_hash": hashes.pdf_hash,
            "text_hash": hashes.text_hash,
            "page_hashes": hashes.page_hashes,
            "extraction_method": extraction_method,
            "ocr_used": ocr_used,
            "page_count": len(page_texts),
            "text_length": len(extracted_text),
            "file_size": hashes.file_size
        }
        self.file_store.store_metadata(monitored_url.id, version.id, metadata)
        
        # Update version with file paths (relative to storage root)
        version.original_pdf_path = str(stored_original.relative_to(self.file_store.storage_path))
        version.normalized_pdf_path = str(stored_normalized.relative_to(self.file_store.storage_path))
        version.extracted_text_path = str(stored_text.relative_to(self.file_store.storage_path))
        
        db.commit()
        
        logger.info(
            "Version created",
            url_id=monitored_url.id,
            version_id=version.id,
            version_number=version_number
        )
        
        return version
    
    def record_change(
        self,
        db: Session,
        monitored_url: MonitoredURL,
        previous_version: Optional[PDFVersion],
        new_version: PDFVersion,
        change_result: ChangeResult
    ) -> ChangeLog:
        """
        Record a detected change.
        
        Args:
            db: Database session
            monitored_url: MonitoredURL record
            previous_version: Previous PDFVersion (None for first version)
            new_version: New PDFVersion
            change_result: ChangeResult from comparison
            
        Returns:
            Created ChangeLog record
        """
        change_log = ChangeLog(
            monitored_url_id=monitored_url.id,
            previous_version_id=previous_version.id if previous_version else None,
            new_version_id=new_version.id,
            change_type=change_result.change_type,
            affected_pages=change_result.affected_pages,
            diff_summary=change_result.diff_summary,
            pdf_hash_changed=change_result.pdf_hash_changed,
            text_hash_changed=change_result.text_hash_changed,
            detected_at=datetime.utcnow()
        )
        
        db.add(change_log)
        
        # Update monitored URL last change timestamp
        monitored_url.last_change_at = datetime.utcnow()
        
        db.commit()
        
        logger.info(
            "Change recorded",
            url_id=monitored_url.id,
            change_type=change_result.change_type,
            affected_pages=len(change_result.affected_pages)
        )
        
        return change_log
    
    def get_latest_version(
        self,
        db: Session,
        url_id: int
    ) -> Optional[PDFVersion]:
        """
        Get the latest version for a URL.
        
        Args:
            db: Database session
            url_id: Monitored URL ID
            
        Returns:
            Latest PDFVersion or None
        """
        return db.query(PDFVersion).filter(
            PDFVersion.monitored_url_id == url_id
        ).order_by(
            PDFVersion.version_number.desc()
        ).first()
    
    def get_version(
        self,
        db: Session,
        version_id: int
    ) -> Optional[PDFVersion]:
        """
        Get a specific version by ID.
        
        Args:
            db: Database session
            version_id: Version ID
            
        Returns:
            PDFVersion or None
        """
        return db.query(PDFVersion).filter(
            PDFVersion.id == version_id
        ).first()
    
    def get_version_history(
        self,
        db: Session,
        url_id: int,
        limit: int = 50
    ) -> list[PDFVersion]:
        """
        Get version history for a URL.
        
        Args:
            db: Database session
            url_id: Monitored URL ID
            limit: Maximum versions to return
            
        Returns:
            List of PDFVersion records, newest first
        """
        return db.query(PDFVersion).filter(
            PDFVersion.monitored_url_id == url_id
        ).order_by(
            PDFVersion.version_number.desc()
        ).limit(limit).all()
    
    def get_version_text(
        self,
        db: Session,
        version_id: int
    ) -> Optional[str]:
        """
        Get extracted text for a version.
        
        Args:
            db: Database session
            version_id: Version ID
            
        Returns:
            Extracted text or None
        """
        version = self.get_version(db, version_id)
        if not version:
            return None
        
        return self.file_store.get_extracted_text(
            version.monitored_url_id,
            version.id
        )
    
    def get_original_pdf_path(
        self,
        db: Session,
        version_id: int
    ) -> Optional[Path]:
        """
        Get full path to original PDF.
        
        Args:
            db: Database session
            version_id: Version ID
            
        Returns:
            Path to original PDF or None
        """
        version = self.get_version(db, version_id)
        if not version:
            return None
        
        return self.file_store.get_original_pdf(
            version.monitored_url_id,
            version.id
        )
    
    def get_normalized_pdf_path(
        self,
        db: Session,
        version_id: int
    ) -> Optional[Path]:
        """
        Get full path to normalized PDF.
        
        Args:
            db: Database session
            version_id: Version ID
            
        Returns:
            Path to normalized PDF or None
        """
        version = self.get_version(db, version_id)
        if not version:
            return None
        
        return self.file_store.get_normalized_pdf(
            version.monitored_url_id,
            version.id
        )
    
    def get_recent_changes(
        self,
        db: Session,
        limit: int = 50
    ) -> list[ChangeLog]:
        """
        Get recent changes across all URLs.
        
        Args:
            db: Database session
            limit: Maximum changes to return
            
        Returns:
            List of ChangeLog records, newest first
        """
        return db.query(ChangeLog).order_by(
            ChangeLog.detected_at.desc()
        ).limit(limit).all()
    
    def get_url_changes(
        self,
        db: Session,
        url_id: int,
        limit: int = 50
    ) -> list[ChangeLog]:
        """
        Get changes for a specific URL.
        
        Args:
            db: Database session
            url_id: Monitored URL ID
            limit: Maximum changes to return
            
        Returns:
            List of ChangeLog records, newest first
        """
        return db.query(ChangeLog).filter(
            ChangeLog.monitored_url_id == url_id
        ).order_by(
            ChangeLog.detected_at.desc()
        ).limit(limit).all()
    
    def cleanup_old_versions(
        self,
        db: Session,
        url_id: int,
        keep_count: int = 10
    ) -> int:
        """
        Clean up old versions, keeping the most recent.
        
        Args:
            db: Database session
            url_id: Monitored URL ID
            keep_count: Number of versions to keep
            
        Returns:
            Number of versions deleted
        """
        versions = db.query(PDFVersion).filter(
            PDFVersion.monitored_url_id == url_id
        ).order_by(
            PDFVersion.version_number.desc()
        ).all()
        
        if len(versions) <= keep_count:
            return 0
        
        to_delete = versions[keep_count:]
        deleted = 0
        
        for version in to_delete:
            # Delete files
            self.file_store.delete_version(url_id, version.id)
            
            # Delete change logs referencing this version
            db.query(ChangeLog).filter(
                (ChangeLog.previous_version_id == version.id) |
                (ChangeLog.new_version_id == version.id)
            ).delete()
            
            # Delete version record
            db.delete(version)
            deleted += 1
        
        db.commit()
        
        logger.info(
            "Cleaned up old versions",
            url_id=url_id,
            deleted=deleted,
            kept=keep_count
        )
        
        return deleted


