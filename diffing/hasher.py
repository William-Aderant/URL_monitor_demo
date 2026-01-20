"""
Hash computation for PDF change detection.

Computes:
- SHA-256 hash of original PDF bytes
- SHA-256 hash of extracted text
- Per-page text hashes for granular change detection
"""

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import structlog

logger = structlog.get_logger()


@dataclass
class HashResult:
    """Result of hash computation."""
    pdf_hash: str  # SHA-256 of original PDF bytes
    text_hash: str  # SHA-256 of extracted text
    page_hashes: list[str] = field(default_factory=list)  # Per-page text hashes
    file_size: int = 0
    text_length: int = 0


class Hasher:
    """
    Computes hashes for PDF change detection.
    
    Uses SHA-256 for all hashes:
    - PDF hash: Binary hash of original PDF for exact binary comparison
    - Text hash: Hash of extracted text for semantic comparison
    - Page hashes: Per-page hashes for identifying which pages changed
    """
    
    @staticmethod
    def compute_file_hash(file_path: Path) -> str:
        """
        Compute SHA-256 hash of a file.
        
        Args:
            file_path: Path to file
            
        Returns:
            Hex-encoded SHA-256 hash
        """
        sha256 = hashlib.sha256()
        
        with open(file_path, 'rb') as f:
            # Read in chunks for memory efficiency
            for chunk in iter(lambda: f.read(8192), b''):
                sha256.update(chunk)
        
        return sha256.hexdigest()
    
    @staticmethod
    def compute_text_hash(text: str) -> str:
        """
        Compute SHA-256 hash of text.
        
        Normalizes whitespace and removes common variations before hashing for consistent comparison.
        
        Args:
            text: Text to hash
            
        Returns:
            Hex-encoded SHA-256 hash
        """
        if not text:
            return hashlib.sha256(b'').hexdigest()
        
        # More aggressive normalization:
        # 1. Remove all whitespace (spaces, tabs, newlines) and replace with single space
        # 2. Lowercase for case-insensitive comparison
        # 3. Remove common punctuation variations
        import re
        
        # Normalize whitespace
        normalized = ' '.join(text.split())
        
        # Remove common variations that don't affect meaning
        # Remove zero-width spaces and other invisible characters
        normalized = re.sub(r'[\u200b-\u200f\ufeff]', '', normalized)
        
        # Normalize quotes and apostrophes
        normalized = normalized.replace('"', '"').replace('"', '"')
        normalized = normalized.replace("'", "'").replace("'", "'")
        
        # Remove extra spaces
        normalized = re.sub(r'\s+', ' ', normalized)
        
        # Strip and lowercase for consistent comparison
        normalized = normalized.strip().lower()
        
        return hashlib.sha256(normalized.encode('utf-8')).hexdigest()
    
    @staticmethod
    def compute_bytes_hash(data: bytes) -> str:
        """
        Compute SHA-256 hash of bytes.
        
        Args:
            data: Bytes to hash
            
        Returns:
            Hex-encoded SHA-256 hash
        """
        return hashlib.sha256(data).hexdigest()
    
    def compute_hashes(
        self,
        pdf_path: Path,
        extracted_text: str,
        page_texts: list[str]
    ) -> HashResult:
        """
        Compute all hashes for a PDF.
        
        Args:
            pdf_path: Path to original PDF file
            extracted_text: Full extracted text
            page_texts: List of per-page extracted text
            
        Returns:
            HashResult with all computed hashes
        """
        logger.debug("Computing hashes", pdf_path=str(pdf_path))
        
        # Compute PDF hash
        pdf_hash = self.compute_file_hash(pdf_path)
        file_size = pdf_path.stat().st_size
        
        # Compute text hash
        text_hash = self.compute_text_hash(extracted_text)
        
        # Compute per-page hashes
        page_hashes = [self.compute_text_hash(page) for page in page_texts]
        
        result = HashResult(
            pdf_hash=pdf_hash,
            text_hash=text_hash,
            page_hashes=page_hashes,
            file_size=file_size,
            text_length=len(extracted_text)
        )
        
        logger.info(
            "Hashes computed",
            pdf_hash=pdf_hash[:16] + "...",
            text_hash=text_hash[:16] + "...",
            page_count=len(page_hashes)
        )
        
        return result
    
    def quick_compare(self, hash1: str, hash2: str) -> bool:
        """
        Quick comparison of two hashes.
        
        Args:
            hash1: First hash
            hash2: Second hash
            
        Returns:
            True if hashes match, False otherwise
        """
        return hash1 == hash2
    
    def compare_page_hashes(
        self,
        old_hashes: list[str],
        new_hashes: list[str]
    ) -> list[int]:
        """
        Compare page hashes and return list of changed page numbers.
        
        Args:
            old_hashes: Previous version page hashes
            new_hashes: New version page hashes
            
        Returns:
            List of 1-indexed page numbers that changed
        """
        changed_pages = []
        
        # Handle different page counts
        max_pages = max(len(old_hashes), len(new_hashes))
        
        for i in range(max_pages):
            old_hash = old_hashes[i] if i < len(old_hashes) else None
            new_hash = new_hashes[i] if i < len(new_hashes) else None
            
            if old_hash != new_hash:
                changed_pages.append(i + 1)  # 1-indexed
        
        return changed_pages


