"""
Title Extraction Service

Extracts and formats document titles from court form PDFs using:
- Amazon Textract for text extraction (OCR)
- Amazon Bedrock (Claude) for intelligent title/form number identification

Also generates preview images of the first page.
"""

import json
import os
import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Optional

import boto3
import structlog
from PIL import Image

from config import settings

logger = structlog.get_logger()


@dataclass
class TitleExtractionResult:
    """Result of title extraction."""
    success: bool
    formatted_title: Optional[str] = None
    form_number: Optional[str] = None
    revision_date: Optional[str] = None  # Extracted revision date (REQ-006)
    combined_confidence: Optional[float] = None
    ocr_confidence: Optional[float] = None
    llm_confidence: Optional[float] = None
    reasoning: Optional[str] = None
    error: Optional[str] = None
    extraction_method: str = "textract+bedrock"
    
    @property
    def display_title(self) -> str:
        """
        Combined display title per PoC format: "Title {FormNumber}"
        
        Example: "Acknowledgement Of Security Interest {F207-143-000}"
        """
        if not self.formatted_title:
            return ""
        if self.form_number:
            return f"{self.formatted_title} {{{self.form_number}}}"
        return self.formatted_title


class TitleExtractor:
    """
    Extracts document titles from PDFs using AWS Textract and Bedrock.
    """
    
    def __init__(self, aws_region: Optional[str] = None):
        """
        Initialize the title extractor.
        
        Args:
            aws_region: AWS region for services. Defaults to AWS_REGION env var or us-east-1.
        """
        self.aws_region = aws_region or settings.AWS_REGION
        self._textract_client = None
        self._bedrock_client = None
        
        logger.info("TitleExtractor initialized", region=self.aws_region)
    
    def _get_aws_session(self):
        """Get AWS session with credentials from settings or default credential chain."""
        session_kwargs = {'region_name': self.aws_region}
        
        # Only use explicit credentials if they're set in settings
        if (settings.AWS_ACCESS_KEY_ID and settings.AWS_ACCESS_KEY_ID.strip() and 
            settings.AWS_SECRET_ACCESS_KEY and settings.AWS_SECRET_ACCESS_KEY.strip()):
            session_kwargs['aws_access_key_id'] = settings.AWS_ACCESS_KEY_ID
            session_kwargs['aws_secret_access_key'] = settings.AWS_SECRET_ACCESS_KEY
            # Also check for session token (for temporary credentials)
            if os.getenv("AWS_SESSION_TOKEN"):
                session_kwargs['aws_session_token'] = os.getenv("AWS_SESSION_TOKEN")
        # Otherwise, boto3 will use default credential chain (SSO, IAM role, etc.)
        
        return boto3.Session(**session_kwargs)
    
    @property
    def textract_client(self):
        """Lazy-load Textract client."""
        if self._textract_client is None:
            session = self._get_aws_session()
            self._textract_client = session.client("textract")
        return self._textract_client
    
    @property
    def bedrock_client(self):
        """Lazy-load Bedrock client."""
        if self._bedrock_client is None:
            session = self._get_aws_session()
            self._bedrock_client = session.client("bedrock-runtime")
        return self._bedrock_client
    
    def is_available(self) -> bool:
        """
        Check if AWS credentials are available.
        Returns True if explicit credentials are set OR if default credential chain is available.
        """
        # If explicit credentials are set in settings, use them
        if (settings.AWS_ACCESS_KEY_ID and settings.AWS_ACCESS_KEY_ID.strip() and 
            settings.AWS_SECRET_ACCESS_KEY and settings.AWS_SECRET_ACCESS_KEY.strip()):
            return True
        
        # Otherwise, check if default credential chain is available
        # boto3 will automatically use SSO, IAM roles, etc. if configured
        try:
            # Try to create a session to verify credentials are available
            test_session = boto3.Session(region_name=self.aws_region)
            # Try to get credentials (this will use default chain if no explicit creds)
            credentials = test_session.get_credentials()
            return credentials is not None
        except Exception:
            # If we can't get credentials, return False
            return False
    
    def convert_pdf_to_image(self, pdf_path: Path, output_path: Optional[Path] = None) -> Optional[bytes]:
        """
        Convert the first page of a PDF to a PNG image.
        
        Args:
            pdf_path: Path to the PDF file
            output_path: Optional path to save the preview image
            
        Returns:
            PNG image bytes, or None if conversion fails
        """
        try:
            import fitz  # PyMuPDF
            
            doc = fitz.open(pdf_path)
            if len(doc) == 0:
                logger.warning("PDF has no pages", path=str(pdf_path))
                return None
            
            # Get first page
            page = doc[0]
            
            # Render at 150 DPI for good quality without huge file size
            mat = fitz.Matrix(150/72, 150/72)
            pix = page.get_pixmap(matrix=mat)
            
            # Convert to PNG bytes
            img_bytes = pix.tobytes("png")
            
            # Save to file if output path provided
            if output_path:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                with open(output_path, "wb") as f:
                    f.write(img_bytes)
                logger.debug("Saved preview image", path=str(output_path))
            
            doc.close()
            return img_bytes
            
        except Exception as e:
            logger.error("Failed to convert PDF to image", error=str(e))
            return None
    
    def extract_text_with_textract(self, image_bytes: bytes) -> dict:
        """
        Use Amazon Textract to extract text from the document image.
        
        Args:
            image_bytes: PNG image bytes
            
        Returns:
            Dictionary with 'text', 'avg_confidence', and 'line_confidences'
        """
        # Track API call
        from services.api_counter import api_counter
        api_counter.increment('textract')
        
        response = self.textract_client.detect_document_text(
            Document={"Bytes": image_bytes}
        )
        
        lines = []
        confidences = []
        
        for block in response.get("Blocks", []):
            if block["BlockType"] == "LINE":
                lines.append(block["Text"])
                confidences.append(block.get("Confidence", 0))
        
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0
        
        return {
            "text": "\n".join(lines),
            "avg_confidence": avg_confidence,
            "line_confidences": confidences
        }
    
    def identify_title_with_bedrock(self, extracted_text: str) -> dict:
        """
        Use Amazon Bedrock (Claude) to identify the document title and form number.
        
        Args:
            extracted_text: Text extracted from the document
            
        Returns:
            Dictionary with 'title', 'form_number', 'confidence', and 'reasoning'
        """
        prompt = f"""You are a document analysis assistant. Given the following text extracted from a court form, identify:
1. The complete main document title, including any parenthetical information that appears with it (e.g., "(Limited Civil Case)", "(Misdemeanor)", "(Family Law)", etc.)
2. The form number/code (usually in a format like "ADR-103", "CIV-775", "APP-108", or similar alphanumeric codes)
3. Your confidence level in correctly identifying these elements

IMPORTANT: The title should include ALL descriptive text that is part of the official form title, including parenthetical qualifiers WITH THE PARENTHESES PRESERVED. For example:
- "Notice of Waiver of Oral Argument (Limited Civil Case)" NOT "Notice of Waiver of Oral Argument" or "Notice of Waiver of Oral Argument Limited Civil Case"
- "Petition for Dissolution of Marriage (Family Law)" NOT "Petition for Dissolution of Marriage" or "Petition for Dissolution of Marriage Family Law"
- "Application for Order (Criminal)" NOT "Application for Order" or "Application for Order Criminal"

Look for parenthetical text that appears near the main title (often on the same line or immediately following it) and include it WITH PARENTHESES as part of the complete title. The parentheses are part of the official title format and must be preserved.

Extracted text:
---
{extracted_text[:3000]}
---

Return your response as a JSON object with exactly these keys:
- "title": The complete main title of the document, including any parenthetical qualifiers WITH PARENTHESES PRESERVED (e.g., "Notice of Waiver of Oral Argument (Limited Civil Case)")
- "form_number": The form number/code (use empty string if not found)
- "confidence": Your confidence level from 0.0 to 1.0 that you correctly identified the title and form number. Consider:
  - 0.9-1.0: Very clear title and form number, unambiguous
  - 0.7-0.9: Reasonably confident, minor ambiguity
  - 0.5-0.7: Somewhat uncertain, multiple possible titles
  - Below 0.5: Very uncertain, unclear document structure
- "reasoning": Brief explanation of your confidence level (1 sentence)

Return ONLY the JSON object, no other text."""

        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 500,
            "messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        })
        
        # Track API call
        from services.api_counter import api_counter
        api_counter.increment('bedrock')
        
        response = self.bedrock_client.invoke_model(
            modelId="anthropic.claude-3-sonnet-20240229-v1:0",
            body=body,
            contentType="application/json",
            accept="application/json"
        )
        
        response_body = json.loads(response["body"].read())
        response_text = response_body["content"][0]["text"].strip()
        
        # Remove markdown code blocks if present
        if response_text.startswith("```"):
            response_text = re.sub(r"^```(?:json)?\n?", "", response_text)
            response_text = re.sub(r"\n?```$", "", response_text)
        
        try:
            result = json.loads(response_text)
        except json.JSONDecodeError:
            raise ValueError(f"Could not parse API response as JSON: {response_text}")
        
        return {
            "title": result.get("title", ""),
            "form_number": result.get("form_number", ""),
            "confidence": float(result.get("confidence", 0.5)),
            "reasoning": result.get("reasoning", "")
        }
    
    def calculate_combined_confidence(
        self,
        textract_confidence: float,
        llm_confidence: float,
        title: str,
        form_number: str
    ) -> dict:
        """
        Calculate a combined confidence score from multiple signals.
        
        Args:
            textract_confidence: Average OCR confidence from Textract (0-100)
            llm_confidence: Self-reported confidence from Claude (0-1)
            title: The extracted title
            form_number: The extracted form number
            
        Returns:
            Dictionary with confidence breakdown and combined score
        """
        # Normalize Textract confidence to 0-1 scale
        ocr_score = textract_confidence / 100.0
        
        # Pattern matching score for form number
        pattern_score = 0.0
        if form_number:
            # Strong patterns: ADR-103, MC-025, CIV-775
            if re.match(r'^[A-Z]{2,4}-\d{2,4}(/[A-Z]{2,4}-\d{2,4}(-[A-Z]+)?)?$', form_number):
                pattern_score = 1.0
            # Weaker pattern: has letters and numbers
            elif re.search(r'[A-Z]', form_number) and re.search(r'\d', form_number):
                pattern_score = 0.7
            else:
                pattern_score = 0.3
        
        # Title heuristics score
        title_score = 0.0
        if title:
            # Length check (titles are usually substantial)
            if len(title) > 20:
                title_score += 0.4
            elif len(title) > 10:
                title_score += 0.2
            
            # Court form titles often contain legal keywords
            legal_keywords = ['petition', 'motion', 'order', 'notice', 'declaration', 
                             'request', 'response', 'application', 'complaint', 'answer',
                             'stipulation', 'judgment', 'waiver', 'proof', 'summons',
                             'subpoena', 'affidavit', 'certificate', 'information']
            if any(keyword in title.lower() for keyword in legal_keywords):
                title_score += 0.4
            
            # Capitalization check (titles are often in caps or title case)
            if title.isupper() or title.istitle():
                title_score += 0.2
        
        title_score = min(title_score, 1.0)  # Cap at 1.0
        
        # Weighted combination
        # OCR: 25% - Base reliability of text extraction
        # LLM: 35% - Model's assessment of extraction quality
        # Pattern: 20% - Form number format validation
        # Title: 20% - Title quality heuristics
        combined = (
            ocr_score * 0.25 +
            llm_confidence * 0.35 +
            pattern_score * 0.20 +
            title_score * 0.20
        )
        
        return {
            "ocr_confidence": round(ocr_score, 3),
            "llm_confidence": round(llm_confidence, 3),
            "pattern_score": round(pattern_score, 3),
            "title_score": round(title_score, 3),
            "combined_confidence": round(combined, 3)
        }
    
    def format_title(self, title: str) -> str:
        """
        Format the title: capitalize every word and remove unwanted punctuation,
        but preserve parentheses which are part of the official title.
        
        Args:
            title: The original title string
            
        Returns:
            Formatted title string
        """
        # Preserve parentheses but remove other unwanted punctuation
        # Keep word characters, whitespace, and parentheses
        cleaned = re.sub(r"[^\w\s()]", "", title)
        cleaned = " ".join(cleaned.split())
        formatted = cleaned.title()
        return formatted
    
    def format_display_title(self, title: str, form_number: Optional[str] = None) -> str:
        """
        Format the display title per PoC Step 4: "Title {FormNumber}"
        
        Example: "Acknowledgement Of Security Interest {F207-143-000}"
        
        Args:
            title: The formatted title string
            form_number: Optional form number to append in brackets
            
        Returns:
            Display title string
        """
        if not title:
            return ""
        if form_number:
            return f"{title} {{{form_number}}}"
        return title
    
    def extract_revision_date(self, text: str) -> Optional[str]:
        """
        Extract revision date from PDF text content (REQ-006).
        
        Looks for common revision date patterns:
        - Rev. 01/2026, Rev 1/26
        - Revised: January 2026
        - (Rev 1/26), (Revised 01/2026)
        - Revision Date: 01/15/2026
        - Effective: January 1, 2026
        
        Args:
            text: Extracted text content from PDF
            
        Returns:
            Extracted revision date string or None
        """
        if not text:
            return None
        
        # Patterns ordered by specificity/reliability
        patterns = [
            # "Revision Date: 01/15/2026" or "Rev Date: 1/15/26"
            r'[Rr]ev(?:ision)?\s*[Dd]ate:?\s*(\d{1,2}/\d{1,2}/\d{2,4})',
            
            # "Revised: January 2026" or "Revised January 15, 2026"
            r'[Rr]evised:?\s*([A-Za-z]+\s+\d{1,2}?,?\s*\d{4})',
            
            # "Rev. 01/2026" or "Rev 1/26"
            r'[Rr]ev\.?\s*(\d{1,2}/\d{2,4})',
            
            # "(Rev 01/2026)" or "(Revised 1/26)"
            r'\([Rr]ev(?:ised)?\.?\s*(\d{1,2}/\d{2,4})\)',
            
            # "Effective: January 1, 2026" or "Effective Date: 01/01/2026"
            r'[Ee]ffective(?:\s+[Dd]ate)?:?\s*([A-Za-z]+\s+\d{1,2}?,?\s*\d{4}|\d{1,2}/\d{1,2}/\d{2,4})',
            
            # "Version: 2026-01" or "Ver. 01/2026"
            r'[Vv]er(?:sion)?\.?:?\s*(\d{4}-\d{2}|\d{1,2}/\d{2,4})',
            
            # Date patterns at start of line that look like revision dates
            # "01/2026" at beginning of document (first 500 chars)
            r'^.*?(\d{1,2}/\d{4})',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text[:2000], re.MULTILINE | re.IGNORECASE)
            if match:
                date_str = match.group(1).strip()
                logger.debug("Extracted revision date", date=date_str, pattern=pattern)
                return date_str
        
        return None
    
    def extract_title(
        self,
        pdf_path: Path,
        preview_output_path: Optional[Path] = None
    ) -> TitleExtractionResult:
        """
        Extract title and form number from a PDF.
        
        Args:
            pdf_path: Path to the PDF file
            preview_output_path: Optional path to save preview image
            
        Returns:
            TitleExtractionResult with extracted information
        """
        if not self.is_available():
            return TitleExtractionResult(
                success=False,
                error="AWS credentials not configured"
            )
        
        try:
            logger.info("Extracting title", pdf=str(pdf_path))
            
            # Step 1: Convert PDF to image
            image_bytes = self.convert_pdf_to_image(pdf_path, preview_output_path)
            if not image_bytes:
                return TitleExtractionResult(
                    success=False,
                    error="Failed to convert PDF to image"
                )
            
            # Step 2: Extract text with Textract
            logger.debug("Running Textract OCR")
            textract_result = self.extract_text_with_textract(image_bytes)
            extracted_text = textract_result["text"]
            textract_confidence = textract_result["avg_confidence"]
            
            if not extracted_text.strip():
                return TitleExtractionResult(
                    success=False,
                    error="No text extracted from PDF"
                )
            
            # Step 3: Identify title with Bedrock
            logger.debug("Running Bedrock title identification")
            bedrock_result = self.identify_title_with_bedrock(extracted_text)
            original_title = bedrock_result["title"]
            form_number = bedrock_result["form_number"]
            llm_confidence = bedrock_result["confidence"]
            reasoning = bedrock_result["reasoning"]
            
            # Step 4: Calculate combined confidence
            confidence_data = self.calculate_combined_confidence(
                textract_confidence,
                llm_confidence,
                original_title,
                form_number
            )
            
            # Step 5: Format title
            formatted_title = self.format_title(original_title)
            
            # Step 6: Extract revision date (REQ-006)
            revision_date = self.extract_revision_date(extracted_text)
            
            logger.info(
                "Title extracted",
                title=formatted_title,
                form_number=form_number,
                revision_date=revision_date,
                confidence=confidence_data["combined_confidence"]
            )
            
            return TitleExtractionResult(
                success=True,
                formatted_title=formatted_title,
                form_number=form_number,
                revision_date=revision_date,
                combined_confidence=confidence_data["combined_confidence"],
                ocr_confidence=confidence_data["ocr_confidence"],
                llm_confidence=confidence_data["llm_confidence"],
                reasoning=reasoning,
                extraction_method="textract+bedrock"
            )
            
        except Exception as e:
            logger.exception("Title extraction failed", error=str(e))
            return TitleExtractionResult(
                success=False,
                error=str(e)
            )
    
    def extract_title_from_text(self, extracted_text: str) -> TitleExtractionResult:
        """
        Extract title from already-extracted text (skip Textract step).
        Useful when text is already available from the monitoring pipeline.
        
        Args:
            extracted_text: Pre-extracted text content
            
        Returns:
            TitleExtractionResult with extracted information
        """
        if not self.is_available():
            return TitleExtractionResult(
                success=False,
                error="AWS credentials not configured"
            )
        
        try:
            # Use Bedrock to identify title
            bedrock_result = self.identify_title_with_bedrock(extracted_text)
            original_title = bedrock_result["title"]
            form_number = bedrock_result["form_number"]
            llm_confidence = bedrock_result["confidence"]
            reasoning = bedrock_result["reasoning"]
            
            # Calculate confidence (no OCR score since we didn't run Textract)
            confidence_data = self.calculate_combined_confidence(
                95.0,  # Assume good OCR since text was provided
                llm_confidence,
                original_title,
                form_number
            )
            
            formatted_title = self.format_title(original_title)
            
            # Extract revision date (REQ-006)
            revision_date = self.extract_revision_date(extracted_text)
            
            return TitleExtractionResult(
                success=True,
                formatted_title=formatted_title,
                form_number=form_number,
                revision_date=revision_date,
                combined_confidence=confidence_data["combined_confidence"],
                ocr_confidence=confidence_data["ocr_confidence"],
                llm_confidence=confidence_data["llm_confidence"],
                reasoning=reasoning,
                extraction_method="bedrock"
            )
            
        except Exception as e:
            logger.exception("Title extraction from text failed", error=str(e))
            return TitleExtractionResult(
                success=False,
                error=str(e)
            )
