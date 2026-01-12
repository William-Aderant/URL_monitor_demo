"""
File storage abstraction for PDF versions.

Provides local filesystem storage with a structure suitable for
easy migration to S3 or other object storage.
"""

import json
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional
import structlog

from config import settings

logger = structlog.get_logger()


@dataclass
class StoredVersion:
    """Metadata for a stored PDF version."""
    url_id: int
    version_id: int
    original_pdf_path: Path
    normalized_pdf_path: Path
    extracted_text_path: Path
    metadata_path: Path
    created_at: datetime


class FileStore:
    """
    Local filesystem storage for PDF versions.
    
    Directory structure:
    {storage_root}/
        {url_id}/
            {version_id}/
                original.pdf
                normalized.pdf
                extracted_text.txt
                metadata.json
    """
    
    def __init__(self, storage_path: Optional[Path] = None):
        """
        Initialize file store.
        
        Args:
            storage_path: Root path for storage. Uses config default if not provided.
        """
        self.storage_path = storage_path or settings.PDF_STORAGE_PATH
        self.storage_path.mkdir(parents=True, exist_ok=True)
        
        logger.info("FileStore initialized", storage_path=str(self.storage_path))
    
    def get_version_dir(self, url_id: int, version_id: int) -> Path:
        """
        Get directory path for a specific version.
        
        Args:
            url_id: Monitored URL ID
            version_id: Version ID
            
        Returns:
            Path to version directory
        """
        return self.storage_path / str(url_id) / str(version_id)
    
    def create_version_directory(self, url_id: int, version_id: int) -> Path:
        """
        Create directory for a new version.
        
        Args:
            url_id: Monitored URL ID
            version_id: Version ID
            
        Returns:
            Path to created directory
        """
        version_dir = self.get_version_dir(url_id, version_id)
        version_dir.mkdir(parents=True, exist_ok=True)
        
        logger.debug(
            "Created version directory",
            url_id=url_id,
            version_id=version_id,
            path=str(version_dir)
        )
        
        return version_dir
    
    def store_original_pdf(
        self,
        url_id: int,
        version_id: int,
        source_path: Path
    ) -> Path:
        """
        Store original PDF file.
        
        Args:
            url_id: Monitored URL ID
            version_id: Version ID
            source_path: Path to source PDF
            
        Returns:
            Path to stored file
        """
        version_dir = self.create_version_directory(url_id, version_id)
        dest_path = version_dir / "original.pdf"
        
        shutil.copy2(source_path, dest_path)
        
        logger.debug("Stored original PDF", dest=str(dest_path))
        return dest_path
    
    def store_normalized_pdf(
        self,
        url_id: int,
        version_id: int,
        source_path: Path
    ) -> Path:
        """
        Store normalized PDF file.
        
        Args:
            url_id: Monitored URL ID
            version_id: Version ID
            source_path: Path to source PDF
            
        Returns:
            Path to stored file
        """
        version_dir = self.create_version_directory(url_id, version_id)
        dest_path = version_dir / "normalized.pdf"
        
        shutil.copy2(source_path, dest_path)
        
        logger.debug("Stored normalized PDF", dest=str(dest_path))
        return dest_path
    
    def store_extracted_text(
        self,
        url_id: int,
        version_id: int,
        text: str
    ) -> Path:
        """
        Store extracted text.
        
        Args:
            url_id: Monitored URL ID
            version_id: Version ID
            text: Extracted text content
            
        Returns:
            Path to stored file
        """
        version_dir = self.create_version_directory(url_id, version_id)
        dest_path = version_dir / "extracted_text.txt"
        
        dest_path.write_text(text, encoding='utf-8')
        
        logger.debug("Stored extracted text", dest=str(dest_path), chars=len(text))
        return dest_path
    
    def store_metadata(
        self,
        url_id: int,
        version_id: int,
        metadata: dict
    ) -> Path:
        """
        Store version metadata as JSON.
        
        Args:
            url_id: Monitored URL ID
            version_id: Version ID
            metadata: Metadata dictionary
            
        Returns:
            Path to stored file
        """
        version_dir = self.create_version_directory(url_id, version_id)
        dest_path = version_dir / "metadata.json"
        
        # Add timestamp if not present
        if 'stored_at' not in metadata:
            metadata['stored_at'] = datetime.utcnow().isoformat()
        
        with open(dest_path, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2, default=str)
        
        logger.debug("Stored metadata", dest=str(dest_path))
        return dest_path
    
    def get_original_pdf(self, url_id: int, version_id: int) -> Optional[Path]:
        """
        Get path to original PDF if it exists.
        
        Args:
            url_id: Monitored URL ID
            version_id: Version ID
            
        Returns:
            Path to file or None if not found
        """
        path = self.get_version_dir(url_id, version_id) / "original.pdf"
        return path if path.exists() else None
    
    def get_normalized_pdf(self, url_id: int, version_id: int) -> Optional[Path]:
        """
        Get path to normalized PDF if it exists.
        
        Args:
            url_id: Monitored URL ID
            version_id: Version ID
            
        Returns:
            Path to file or None if not found
        """
        path = self.get_version_dir(url_id, version_id) / "normalized.pdf"
        return path if path.exists() else None
    
    def get_extracted_text(self, url_id: int, version_id: int) -> Optional[str]:
        """
        Get extracted text content.
        
        Args:
            url_id: Monitored URL ID
            version_id: Version ID
            
        Returns:
            Text content or None if not found
        """
        path = self.get_version_dir(url_id, version_id) / "extracted_text.txt"
        if path.exists():
            return path.read_text(encoding='utf-8')
        return None
    
    def get_metadata(self, url_id: int, version_id: int) -> Optional[dict]:
        """
        Get version metadata.
        
        Args:
            url_id: Monitored URL ID
            version_id: Version ID
            
        Returns:
            Metadata dictionary or None if not found
        """
        path = self.get_version_dir(url_id, version_id) / "metadata.json"
        if path.exists():
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return None
    
    def store_preview_image(
        self,
        url_id: int,
        version_id: int,
        image_bytes: bytes
    ) -> Path:
        """
        Store preview image (PNG of first page).
        
        Args:
            url_id: Monitored URL ID
            version_id: Version ID
            image_bytes: PNG image bytes
            
        Returns:
            Path to stored file
        """
        version_dir = self.create_version_directory(url_id, version_id)
        dest_path = version_dir / "preview.png"
        
        with open(dest_path, 'wb') as f:
            f.write(image_bytes)
        
        logger.debug("Stored preview image", dest=str(dest_path), size=len(image_bytes))
        return dest_path
    
    def get_preview_image(self, url_id: int, version_id: int) -> Optional[Path]:
        """
        Get path to preview image if it exists.
        
        Args:
            url_id: Monitored URL ID
            version_id: Version ID
            
        Returns:
            Path to file or None if not found
        """
        path = self.get_version_dir(url_id, version_id) / "preview.png"
        return path if path.exists() else None
    
    def get_preview_image_path(self, url_id: int, version_id: int) -> Path:
        """
        Get the expected path for preview image (may not exist yet).
        
        Args:
            url_id: Monitored URL ID
            version_id: Version ID
            
        Returns:
            Path where preview image should be stored
        """
        return self.get_version_dir(url_id, version_id) / "preview.png"
    
    def get_diff_image_path(self, url_id: int, version_id: int) -> Path:
        """
        Get the expected path for diff image (may not exist yet).
        
        Args:
            url_id: Monitored URL ID
            version_id: Version ID
            
        Returns:
            Path where diff image should be stored
        """
        version_dir = self.get_version_dir(url_id, version_id)
        version_dir.mkdir(parents=True, exist_ok=True)
        return version_dir / "diff_preview.png"
    
    def get_diff_image(self, url_id: int, version_id: int) -> Optional[Path]:
        """
        Get path to diff image if it exists.
        
        Args:
            url_id: Monitored URL ID
            version_id: Version ID
            
        Returns:
            Path to file or None if not found
        """
        path = self.get_version_dir(url_id, version_id) / "diff_preview.png"
        return path if path.exists() else None
    
    def list_versions(self, url_id: int) -> list[int]:
        """
        List all version IDs for a URL.
        
        Args:
            url_id: Monitored URL ID
            
        Returns:
            Sorted list of version IDs
        """
        url_dir = self.storage_path / str(url_id)
        if not url_dir.exists():
            return []
        
        versions = []
        for item in url_dir.iterdir():
            if item.is_dir() and item.name.isdigit():
                versions.append(int(item.name))
        
        return sorted(versions)
    
    def delete_version(self, url_id: int, version_id: int) -> bool:
        """
        Delete a version directory and all its contents.
        
        Args:
            url_id: Monitored URL ID
            version_id: Version ID
            
        Returns:
            True if deleted, False if not found
        """
        version_dir = self.get_version_dir(url_id, version_id)
        if version_dir.exists():
            shutil.rmtree(version_dir)
            logger.info("Deleted version", url_id=url_id, version_id=version_id)
            return True
        return False
    
    def get_storage_size(self, url_id: Optional[int] = None) -> int:
        """
        Get total storage size in bytes.
        
        Args:
            url_id: Optional URL ID to limit to specific URL
            
        Returns:
            Total size in bytes
        """
        if url_id is not None:
            root = self.storage_path / str(url_id)
        else:
            root = self.storage_path
        
        if not root.exists():
            return 0
        
        total = 0
        for path in root.rglob('*'):
            if path.is_file():
                total += path.stat().st_size
        
        return total


