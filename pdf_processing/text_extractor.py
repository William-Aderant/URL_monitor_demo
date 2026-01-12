"""
Text extraction from PDFs using pdfplumber and pdfminer.six.
Falls back to OCR when text extraction fails or returns insufficient content.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import pdfplumber
from pdfminer.high_level import extract_text as pdfminer_extract
from pdfminer.pdfparser import PDFSyntaxError
import structlog

from config import settings

logger = structlog.get_logger()


@dataclass
class TextExtractionResult:
    """Result of text extraction from a PDF."""
    success: bool
    full_text: str = ""
    page_texts: list[str] = field(default_factory=list)
    page_count: int = 0
    extraction_method: str = ""  # pdfplumber, pdfminer, textract
    text_length: int = 0
    ocr_used: bool = False
    confidence: float = 1.0  # 0-1, lower for OCR
    error: Optional[str] = None
    needs_ocr: bool = False  # Flag when text is below threshold


class TextExtractor:
    """
    Extracts text from PDF files using multiple methods.
    
    Primary: pdfplumber (preserves layout better)
    Fallback: pdfminer.six (more robust for some PDFs)
    
    If extracted text is below threshold, flags for OCR.
    """
    
    def __init__(self, min_chars_per_page: int = None):
        """
        Initialize text extractor.
        
        Args:
            min_chars_per_page: Minimum characters per page to consider valid.
                               Below this triggers OCR fallback.
        """
        self.min_chars_per_page = min_chars_per_page or settings.OCR_TEXT_THRESHOLD
        logger.info(
            "TextExtractor initialized",
            min_chars_per_page=self.min_chars_per_page
        )
    
    def extract(self, pdf_path: Path) -> TextExtractionResult:
        """
        Extract text from a PDF file.
        
        Tries pdfplumber first, falls back to pdfminer if that fails.
        Flags for OCR if text is below threshold.
        
        Args:
            pdf_path: Path to PDF file
            
        Returns:
            TextExtractionResult with extracted text and metadata
        """
        logger.info("Starting text extraction", pdf_path=str(pdf_path))
        
        if not pdf_path.exists():
            return TextExtractionResult(
                success=False,
                error=f"File not found: {pdf_path}"
            )
        
        # Try pdfplumber first
        result = self._extract_with_pdfplumber(pdf_path)
        
        if result.success and self._is_text_sufficient(result):
            logger.info(
                "Text extracted with pdfplumber",
                pages=result.page_count,
                chars=result.text_length
            )
            return result
        
        # Fallback to pdfminer
        logger.info("Falling back to pdfminer", reason="pdfplumber insufficient")
        result = self._extract_with_pdfminer(pdf_path)
        
        if result.success and self._is_text_sufficient(result):
            logger.info(
                "Text extracted with pdfminer",
                pages=result.page_count,
                chars=result.text_length
            )
            return result
        
        # Text extraction failed or insufficient - flag for OCR
        logger.warning(
            "Text extraction insufficient, flagging for OCR",
            chars_extracted=result.text_length,
            threshold=self.min_chars_per_page * result.page_count
        )
        
        result.needs_ocr = True
        return result
    
    def _extract_with_pdfplumber(self, pdf_path: Path) -> TextExtractionResult:
        """
        Extract text using pdfplumber.
        
        Args:
            pdf_path: Path to PDF file
            
        Returns:
            TextExtractionResult
        """
        try:
            page_texts = []
            
            with pdfplumber.open(pdf_path) as pdf:
                page_count = len(pdf.pages)
                
                for page in pdf.pages:
                    text = page.extract_text() or ""
                    page_texts.append(text)
            
            full_text = "\n\n".join(page_texts)
            
            return TextExtractionResult(
                success=True,
                full_text=full_text,
                page_texts=page_texts,
                page_count=page_count,
                extraction_method="pdfplumber",
                text_length=len(full_text)
            )
            
        except Exception as e:
            logger.warning("pdfplumber extraction failed", error=str(e))
            return TextExtractionResult(
                success=False,
                extraction_method="pdfplumber",
                error=str(e)
            )
    
    def _extract_with_pdfminer(self, pdf_path: Path) -> TextExtractionResult:
        """
        Extract text using pdfminer.six.
        
        Args:
            pdf_path: Path to PDF file
            
        Returns:
            TextExtractionResult
        """
        try:
            # Extract full text
            full_text = pdfminer_extract(str(pdf_path))
            
            # pdfminer doesn't easily give per-page text, so we estimate
            # by counting pages separately
            page_count = self._count_pages(pdf_path)
            
            # Split text roughly by form feeds or estimate
            if "\f" in full_text:
                page_texts = full_text.split("\f")
            else:
                # Rough split by page count
                page_texts = [full_text] if page_count == 1 else self._split_text_by_pages(full_text, page_count)
            
            return TextExtractionResult(
                success=True,
                full_text=full_text,
                page_texts=page_texts,
                page_count=page_count,
                extraction_method="pdfminer",
                text_length=len(full_text)
            )
            
        except PDFSyntaxError as e:
            logger.warning("pdfminer syntax error", error=str(e))
            return TextExtractionResult(
                success=False,
                extraction_method="pdfminer",
                error=f"PDF syntax error: {str(e)}"
            )
        except Exception as e:
            logger.warning("pdfminer extraction failed", error=str(e))
            return TextExtractionResult(
                success=False,
                extraction_method="pdfminer",
                error=str(e)
            )
    
    def _count_pages(self, pdf_path: Path) -> int:
        """Count pages in a PDF."""
        try:
            with pdfplumber.open(pdf_path) as pdf:
                return len(pdf.pages)
        except Exception:
            return 1
    
    def _split_text_by_pages(self, text: str, page_count: int) -> list[str]:
        """Roughly split text into page-sized chunks."""
        if page_count <= 1:
            return [text]
        
        # Simple split by length
        chunk_size = len(text) // page_count
        chunks = []
        for i in range(page_count):
            start = i * chunk_size
            end = start + chunk_size if i < page_count - 1 else len(text)
            chunks.append(text[start:end])
        return chunks
    
    def _is_text_sufficient(self, result: TextExtractionResult) -> bool:
        """
        Check if extracted text meets minimum threshold.
        
        Args:
            result: TextExtractionResult to check
            
        Returns:
            True if text is sufficient, False otherwise
        """
        if not result.success:
            return False
        
        if result.page_count == 0:
            return False
        
        min_total_chars = self.min_chars_per_page * result.page_count
        return result.text_length >= min_total_chars
    
    def get_page_text(self, pdf_path: Path, page_number: int) -> Optional[str]:
        """
        Get text from a specific page.
        
        Args:
            pdf_path: Path to PDF file
            page_number: Page number (1-indexed)
            
        Returns:
            Page text or None if extraction fails
        """
        try:
            with pdfplumber.open(pdf_path) as pdf:
                if 1 <= page_number <= len(pdf.pages):
                    return pdf.pages[page_number - 1].extract_text() or ""
        except Exception as e:
            logger.warning(
                "Failed to extract page text",
                page=page_number,
                error=str(e)
            )
        return None


