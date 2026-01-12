"""PDF processing module for normalization and text extraction."""

from pdf_processing.normalizer import PDFNormalizer
from pdf_processing.text_extractor import TextExtractor, TextExtractionResult
from pdf_processing.ocr_fallback import OCRFallback

__all__ = [
    "PDFNormalizer",
    "TextExtractor",
    "TextExtractionResult",
    "OCRFallback",
]


