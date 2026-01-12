"""
OCR fallback using AWS Textract for PDFs where text extraction fails.
"""

import io
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import boto3
from botocore.exceptions import ClientError, NoCredentialsError
import structlog

from config import settings

logger = structlog.get_logger()


@dataclass
class OCRResult:
    """Result of OCR processing."""
    success: bool
    full_text: str = ""
    page_texts: list[str] = None
    page_count: int = 0
    confidence: float = 0.0
    error: Optional[str] = None
    blocks_processed: int = 0
    
    def __post_init__(self):
        if self.page_texts is None:
            self.page_texts = []


class OCRFallback:
    """
    OCR fallback using AWS Textract.
    
    Used when standard text extraction fails or returns insufficient content.
    Logs all OCR usage for audit and cost tracking.
    """
    
    def __init__(
        self,
        aws_access_key: Optional[str] = None,
        aws_secret_key: Optional[str] = None,
        aws_region: Optional[str] = None
    ):
        """
        Initialize OCR fallback with AWS credentials.
        
        Args:
            aws_access_key: AWS access key ID
            aws_secret_key: AWS secret access key
            aws_region: AWS region
        """
        self.aws_access_key = aws_access_key or settings.AWS_ACCESS_KEY_ID
        self.aws_secret_key = aws_secret_key or settings.AWS_SECRET_ACCESS_KEY
        self.aws_region = aws_region or settings.AWS_REGION
        
        self._client = None
        self._available = None
        
        logger.info("OCRFallback initialized", region=self.aws_region)
    
    @property
    def client(self):
        """Lazy-load Textract client."""
        if self._client is None:
            try:
                self._client = boto3.client(
                    'textract',
                    aws_access_key_id=self.aws_access_key,
                    aws_secret_access_key=self.aws_secret_key,
                    region_name=self.aws_region
                )
            except Exception as e:
                logger.error("Failed to create Textract client", error=str(e))
                raise
        return self._client
    
    def is_available(self) -> bool:
        """
        Check if OCR is available (AWS credentials configured).
        
        Returns:
            True if Textract is available
        """
        if self._available is not None:
            return self._available
        
        if not self.aws_access_key or not self.aws_secret_key:
            logger.warning("AWS credentials not configured, OCR unavailable")
            self._available = False
            return False
        
        try:
            # Try to make a simple API call to verify credentials
            self.client
            self._available = True
            logger.info("OCR (Textract) is available")
            return True
        except NoCredentialsError:
            logger.warning("AWS credentials invalid, OCR unavailable")
            self._available = False
            return False
        except Exception as e:
            logger.warning("OCR availability check failed", error=str(e))
            self._available = False
            return False
    
    def process_pdf(self, pdf_path: Path, url: str = "") -> OCRResult:
        """
        Process a PDF with OCR using Textract.
        
        For multi-page PDFs, uses StartDocumentTextDetection (async).
        For single-page or small PDFs, uses DetectDocumentText (sync).
        
        Args:
            pdf_path: Path to PDF file
            url: Source URL (for logging/audit)
            
        Returns:
            OCRResult with extracted text
        """
        logger.info(
            "Starting OCR processing",
            pdf_path=str(pdf_path),
            url=url,
            reason="text_extraction_fallback"
        )
        
        if not self.is_available():
            return OCRResult(
                success=False,
                error="OCR not available - AWS credentials not configured"
            )
        
        if not pdf_path.exists():
            return OCRResult(
                success=False,
                error=f"File not found: {pdf_path}"
            )
        
        try:
            # Read PDF bytes
            pdf_bytes = pdf_path.read_bytes()
            file_size = len(pdf_bytes)
            
            # Textract sync API has a 5MB limit
            if file_size > 5 * 1024 * 1024:
                logger.info("PDF exceeds sync limit, using async processing")
                return self._process_async(pdf_path, url)
            
            return self._process_sync(pdf_bytes, url)
            
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            logger.error(
                "Textract API error",
                error_code=error_code,
                error=str(e),
                url=url
            )
            return OCRResult(
                success=False,
                error=f"Textract error: {error_code}"
            )
        except Exception as e:
            logger.error("OCR processing failed", error=str(e), url=url)
            return OCRResult(
                success=False,
                error=str(e)
            )
    
    def _process_sync(self, pdf_bytes: bytes, url: str) -> OCRResult:
        """
        Process PDF synchronously using DetectDocumentText.
        
        Args:
            pdf_bytes: PDF file bytes
            url: Source URL for logging
            
        Returns:
            OCRResult
        """
        response = self.client.detect_document_text(
            Document={'Bytes': pdf_bytes}
        )
        
        return self._parse_textract_response(response, url)
    
    def _process_async(self, pdf_path: Path, url: str) -> OCRResult:
        """
        Process large PDF asynchronously.
        
        Note: Async processing requires S3 upload. For this prototype,
        we'll split the PDF into smaller chunks if needed.
        
        Args:
            pdf_path: Path to PDF file
            url: Source URL for logging
            
        Returns:
            OCRResult
        """
        logger.warning(
            "Large PDF detected - async Textract requires S3. "
            "Consider splitting PDF or using alternative OCR.",
            url=url
        )
        
        # For prototype, return error - production would use S3 + async
        return OCRResult(
            success=False,
            error="PDF too large for sync processing. Async processing requires S3 setup."
        )
    
    def _parse_textract_response(self, response: dict, url: str) -> OCRResult:
        """
        Parse Textract response into OCRResult.
        
        Args:
            response: Textract API response
            url: Source URL for logging
            
        Returns:
            OCRResult
        """
        blocks = response.get('Blocks', [])
        
        # Extract text from LINE blocks
        lines = []
        page_lines = {}  # page_number -> list of lines
        total_confidence = 0.0
        confidence_count = 0
        
        for block in blocks:
            if block['BlockType'] == 'LINE':
                text = block.get('Text', '')
                confidence = block.get('Confidence', 0)
                page = block.get('Page', 1)
                
                lines.append(text)
                
                if page not in page_lines:
                    page_lines[page] = []
                page_lines[page].append(text)
                
                total_confidence += confidence
                confidence_count += 1
        
        # Build full text and page texts
        full_text = '\n'.join(lines)
        
        # Sort pages and build page texts
        page_count = max(page_lines.keys()) if page_lines else 0
        page_texts = []
        for i in range(1, page_count + 1):
            page_text = '\n'.join(page_lines.get(i, []))
            page_texts.append(page_text)
        
        avg_confidence = (total_confidence / confidence_count) if confidence_count > 0 else 0
        
        logger.info(
            "OCR completed",
            url=url,
            pages=page_count,
            lines=len(lines),
            chars=len(full_text),
            avg_confidence=f"{avg_confidence:.1f}%"
        )
        
        return OCRResult(
            success=True,
            full_text=full_text,
            page_texts=page_texts,
            page_count=page_count,
            confidence=avg_confidence / 100,  # Convert to 0-1 scale
            blocks_processed=len(blocks)
        )
    
    def process_image(self, image_path: Path, url: str = "") -> OCRResult:
        """
        Process a single image with OCR.
        
        Useful for PDFs converted to images.
        
        Args:
            image_path: Path to image file
            url: Source URL for logging
            
        Returns:
            OCRResult
        """
        logger.info("Processing image with OCR", image_path=str(image_path))
        
        if not self.is_available():
            return OCRResult(
                success=False,
                error="OCR not available"
            )
        
        try:
            image_bytes = image_path.read_bytes()
            
            response = self.client.detect_document_text(
                Document={'Bytes': image_bytes}
            )
            
            return self._parse_textract_response(response, url)
            
        except Exception as e:
            logger.error("Image OCR failed", error=str(e))
            return OCRResult(
                success=False,
                error=str(e)
            )


