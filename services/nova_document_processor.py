"""
Nova Document Processor Service

Provides direct PDF analysis using Amazon Nova 2 Lite via Amazon Bedrock.
This is an alternative to the Textract+Claude workflow that processes PDFs
natively without requiring a separate OCR step.

Key benefits over Textract+Claude:
- Single API call (vs 2 API calls)
- Preserves document layout and formatting context
- Lower latency
- Handles PDFs up to 25MB directly, larger via S3

This service is ADDITIVE and does not replace or modify existing
title extraction or OCR fallback functionality.
"""

import base64
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Dict, Any, List

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, NoCredentialsError
import structlog

from config import settings

logger = structlog.get_logger()


@dataclass
class NovaAnalysisResult:
    """Result of Nova document analysis."""
    success: bool
    response_text: str = ""
    error: Optional[str] = None
    model_id: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    stop_reason: str = ""
    
    # Structured extraction results (populated by specialized methods)
    title: Optional[str] = None
    form_number: Optional[str] = None
    revision_date: Optional[str] = None
    confidence: float = 0.0
    reasoning: Optional[str] = None
    extracted_fields: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TitleExtractionResult:
    """
    Result of title extraction using Nova.
    Mirrors the structure from title_extractor.py for compatibility.
    """
    success: bool
    formatted_title: Optional[str] = None
    form_number: Optional[str] = None
    revision_date: Optional[str] = None
    combined_confidence: Optional[float] = None
    reasoning: Optional[str] = None
    error: Optional[str] = None
    extraction_method: str = "nova"
    
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


class NovaDocumentProcessor:
    """
    Document processor using Amazon Nova 2 Lite via Bedrock.
    
    Processes PDFs directly without requiring Textract OCR preprocessing.
    Uses the Bedrock Converse API for document understanding.
    
    Authentication uses boto3 default credential chain:
    1. Environment variables (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY)
    2. Shared credentials file (~/.aws/credentials from 'aws configure')
    3. IAM role (for EC2/Lambda)
    """
    
    # Default model ID for Nova 2 Lite
    DEFAULT_MODEL_ID = "amazon.nova-2-lite-v1:0"
    
    # Maximum file size for direct bytes upload (25MB)
    MAX_DIRECT_UPLOAD_SIZE = 25 * 1024 * 1024
    
    def __init__(
        self,
        aws_region: Optional[str] = None,
        model_id: Optional[str] = None
    ):
        """
        Initialize Nova document processor.
        
        Args:
            aws_region: AWS region (uses settings.AWS_REGION if not provided)
            model_id: Nova model ID (uses DEFAULT_MODEL_ID if not provided)
        """
        self.aws_region = aws_region or settings.AWS_REGION
        self.model_id = model_id or os.getenv(
            "BEDROCK_NOVA_MODEL_ID", 
            self.DEFAULT_MODEL_ID
        )
        
        self._client = None
        self._available = None
        
        logger.info(
            "NovaDocumentProcessor initialized",
            region=self.aws_region,
            model_id=self.model_id
        )
    
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
    def client(self):
        """Lazy-load Bedrock runtime client with extended timeouts."""
        if self._client is None:
            try:
                # Extended timeout for document processing
                config = Config(
                    read_timeout=300,  # 5 minutes
                    connect_timeout=60,
                    retries={'max_attempts': 3}
                )
                
                session = self._get_aws_session()
                self._client = session.client("bedrock-runtime", config=config)
                
            except Exception as e:
                logger.error("Failed to create Bedrock client", error=str(e))
                raise
        return self._client
    
    def is_available(self) -> bool:
        """
        Check if Nova document processing is available.
        
        Verifies:
        1. BEDROCK_NOVA_ENABLED feature flag is True
        2. AWS credentials are available
        
        Returns:
            True if Nova processing is available
        """
        if self._available is not None:
            return self._available
        
        # Check feature flag
        nova_enabled = os.getenv("BEDROCK_NOVA_ENABLED", "False").lower() == "true"
        if not nova_enabled:
            logger.debug("Nova document processing disabled (BEDROCK_NOVA_ENABLED=False)")
            self._available = False
            return False
        
        try:
            # Try to create client - boto3 will use default credential chain
            self.client
            self._available = True
            logger.info("Nova document processing is available")
            return True
        except NoCredentialsError:
            logger.warning(
                "AWS credentials not found (check 'aws configure' or env vars), "
                "Nova processing unavailable"
            )
            self._available = False
            return False
        except Exception as e:
            logger.warning("Nova availability check failed", error=str(e))
            self._available = False
            return False
    
    def analyze_pdf(
        self,
        pdf_path: Path,
        query: str,
        max_tokens: int = 4000,
        temperature: float = 0.3
    ) -> NovaAnalysisResult:
        """
        Analyze a PDF document using Nova 2 Lite.
        
        Args:
            pdf_path: Path to the PDF file
            query: Analysis question/instruction
            max_tokens: Maximum response tokens
            temperature: Model temperature (0-1)
            
        Returns:
            NovaAnalysisResult with analysis response
        """
        logger.info(
            "Starting Nova PDF analysis",
            pdf_path=str(pdf_path),
            query_preview=query[:100]
        )
        
        if not pdf_path.exists():
            return NovaAnalysisResult(
                success=False,
                error=f"File not found: {pdf_path}"
            )
        
        try:
            pdf_bytes = pdf_path.read_bytes()
            return self.analyze_pdf_from_bytes(
                pdf_bytes=pdf_bytes,
                query=query,
                max_tokens=max_tokens,
                temperature=temperature,
                source_path=str(pdf_path)
            )
        except Exception as e:
            logger.error("Failed to read PDF file", error=str(e))
            return NovaAnalysisResult(
                success=False,
                error=f"Failed to read file: {str(e)}"
            )
    
    def analyze_pdf_from_bytes(
        self,
        pdf_bytes: bytes,
        query: str,
        max_tokens: int = 4000,
        temperature: float = 0.3,
        source_path: str = ""
    ) -> NovaAnalysisResult:
        """
        Analyze PDF document from bytes using Nova 2 Lite.
        
        Suitable for PDFs under 25MB. For larger files, use analyze_pdf_from_s3().
        
        Args:
            pdf_bytes: Binary PDF content
            query: Analysis question/instruction
            max_tokens: Maximum response tokens
            temperature: Model temperature (0-1)
            source_path: Optional source path for logging
            
        Returns:
            NovaAnalysisResult with analysis response
        """
        file_size = len(pdf_bytes)
        
        if file_size > self.MAX_DIRECT_UPLOAD_SIZE:
            return NovaAnalysisResult(
                success=False,
                error=f"PDF too large for direct upload ({file_size / (1024*1024):.1f}MB > 25MB). "
                      f"Use analyze_pdf_from_s3() for larger files."
            )
        
        try:
            # Track API call
            from services.api_counter import api_counter
            api_counter.increment('bedrock_nova')
            
            # Construct message with embedded PDF
            messages = [{
                "role": "user",
                "content": [
                    {
                        "document": {
                            "format": "pdf",
                            "name": "document",  # Neutral name to avoid prompt injection
                            "source": {
                                "bytes": pdf_bytes
                            }
                        }
                    },
                    {
                        "text": query
                    }
                ]
            }]
            
            # Invoke the model using Converse API
            response = self.client.converse(
                modelId=self.model_id,
                messages=messages,
                inferenceConfig={
                    "maxTokens": max_tokens,
                    "temperature": temperature,
                    "topP": 0.9
                }
            )
            
            # Extract response
            output = response.get("output", {})
            message = output.get("message", {})
            content = message.get("content", [])
            
            response_text = ""
            if content and len(content) > 0:
                response_text = content[0].get("text", "")
            
            # Extract usage metrics
            usage = response.get("usage", {})
            input_tokens = usage.get("inputTokens", 0)
            output_tokens = usage.get("outputTokens", 0)
            stop_reason = response.get("stopReason", "")
            
            logger.info(
                "Nova PDF analysis completed",
                source=source_path,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                response_length=len(response_text)
            )
            
            return NovaAnalysisResult(
                success=True,
                response_text=response_text,
                model_id=self.model_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                stop_reason=stop_reason
            )
            
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_msg = e.response.get('Error', {}).get('Message', str(e))
            logger.error(
                "Bedrock API error during Nova analysis",
                error_code=error_code,
                error=error_msg
            )
            return NovaAnalysisResult(
                success=False,
                error=f"Bedrock error: {error_code} - {error_msg}"
            )
        except Exception as e:
            logger.error("Nova PDF analysis failed", error=str(e))
            return NovaAnalysisResult(
                success=False,
                error=str(e)
            )
    
    def analyze_pdf_from_s3(
        self,
        bucket: str,
        key: str,
        query: str,
        bucket_owner: Optional[str] = None,
        max_tokens: int = 4000,
        temperature: float = 0.3
    ) -> NovaAnalysisResult:
        """
        Analyze a PDF document stored in S3.
        
        Use this for files larger than 25MB or for batch processing workflows.
        
        Args:
            bucket: S3 bucket name
            key: Object key/path in bucket
            query: Analysis question/instruction
            bucket_owner: AWS account ID that owns the bucket (optional)
            max_tokens: Maximum response tokens
            temperature: Model temperature (0-1)
            
        Returns:
            NovaAnalysisResult with analysis response
        """
        logger.info(
            "Starting Nova PDF analysis from S3",
            bucket=bucket,
            key=key
        )
        
        try:
            # Track API call
            from services.api_counter import api_counter
            api_counter.increment('bedrock_nova')
            
            # Build S3 source configuration
            s3_source = {
                "uri": f"s3://{bucket}/{key}"
            }
            if bucket_owner:
                s3_source["bucketOwner"] = bucket_owner
            
            # Construct message with S3 reference
            messages = [{
                "role": "user",
                "content": [
                    {
                        "document": {
                            "format": "pdf",
                            "name": "document",
                            "source": {
                                "s3Location": s3_source
                            }
                        }
                    },
                    {
                        "text": query
                    }
                ]
            }]
            
            # Invoke the model
            response = self.client.converse(
                modelId=self.model_id,
                messages=messages,
                inferenceConfig={
                    "maxTokens": max_tokens,
                    "temperature": temperature,
                    "topP": 0.9
                }
            )
            
            # Extract response
            output = response.get("output", {})
            message = output.get("message", {})
            content = message.get("content", [])
            
            response_text = ""
            if content and len(content) > 0:
                response_text = content[0].get("text", "")
            
            # Extract usage metrics
            usage = response.get("usage", {})
            
            logger.info(
                "Nova S3 PDF analysis completed",
                bucket=bucket,
                key=key,
                response_length=len(response_text)
            )
            
            return NovaAnalysisResult(
                success=True,
                response_text=response_text,
                model_id=self.model_id,
                input_tokens=usage.get("inputTokens", 0),
                output_tokens=usage.get("outputTokens", 0),
                stop_reason=response.get("stopReason", "")
            )
            
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_msg = e.response.get('Error', {}).get('Message', str(e))
            logger.error(
                "Bedrock API error during Nova S3 analysis",
                error_code=error_code,
                error=error_msg,
                bucket=bucket,
                key=key
            )
            return NovaAnalysisResult(
                success=False,
                error=f"Bedrock error: {error_code} - {error_msg}"
            )
        except Exception as e:
            logger.error("Nova S3 PDF analysis failed", error=str(e))
            return NovaAnalysisResult(
                success=False,
                error=str(e)
            )
    
    def extract_title_and_form(
        self,
        pdf_path: Path,
        preview_output_path: Optional[Path] = None
    ) -> TitleExtractionResult:
        """
        Extract title and form number from a PDF using Nova 2 Lite.
        
        This method provides the same functionality as TitleExtractor.extract_title()
        but uses Nova's native PDF processing instead of Textract+Claude.
        
        Args:
            pdf_path: Path to the PDF file
            preview_output_path: Optional path to save preview image
            
        Returns:
            TitleExtractionResult with extracted title and form information
        """
        if not self.is_available():
            return TitleExtractionResult(
                success=False,
                error="Nova document processing not available - check BEDROCK_NOVA_ENABLED and credentials"
            )
        
        # Generate preview image if requested
        if preview_output_path:
            self._generate_preview(pdf_path, preview_output_path)
        
        # Construct the extraction prompt (similar to title_extractor.py)
        extraction_prompt = """You are a legal document analyst specializing in court form processing. Your task is to accurately extract metadata from legal form PDFs with high precision.

## TASK
Analyze the provided legal form PDF and extract the following four pieces of information.

## EXTRACTION FIELDS

### 1. FORM TITLE
Extract the main descriptive title of the form.

**Where to look:**
- Large or bold text, typically centered on the page
- Usually appears below any court/agency headers

**Rules:**
- DO NOT include court names, agency headers, or jurisdiction text
- DO NOT include location fields or case number blanks
- DO include any parenthetical text that is part of the official title
- Preserve parentheses exactly as shown in the original

**Examples:**
- ✓ "Petition for Dissolution of Marriage (Divorce)"
- ✓ "Notice of Entry of Judgment"
- ✗ "Superior Court of California, County of Los Angeles" (this is a header, not a title)

### 2. FORM NUMBER
Extract the form's alphanumeric identifier code.

**Common formats:** CIV-775, ADR-103, FL-100, MC-025, CIV-125S, CIV-125D

**Rules:**
- Extract ONLY the base form number
- Form numbers may end with letters (e.g., "S" or "D") - include these
- DO NOT include revision dates or suffixes like "(8/10)", "(cs)", "(rev)"

**Examples:**
- ✓ "FL-100" from "FL-100 (Rev. 1/1/2023)"
- ✓ "CIV-125S" (the 'S' is part of the form number)
- ✗ "FL-100 (Rev. 1/1/2023)" (includes revision info)

### 3. REVISION DATE
Extract the form's revision or effective date.

**Where to look:**
- Near the form number
- In the footer area
- Look for indicators: "Rev.", "Revised", "Eff.", "Effective", or standalone dates in parentheses

**Rules:**
- Return the date in the format shown on the form
- If no revision date is found, return an empty string

### 4. CONFIDENCE SCORE
Provide a realistic confidence rating for your extraction.

| Score Range | Use When |
|-------------|----------|
| 0.90 - 1.00 | Clear title and form number, no ambiguity, easily readable |
| 0.75 - 0.89 | Confident extraction but minor issues (slight formatting irregularities, slightly faded text) |
| 0.50 - 0.74 | Uncertain - multiple possible titles, partially obscured text, or hard to read |
| Below 0.50 | Very uncertain - significant readability issues or conflicting information |

## OUTPUT FORMAT
Return ONLY valid JSON with no additional text, markdown formatting, or code blocks.

```json
{
  "title": "Form title with parentheticals preserved exactly",
  "form_number": "Base form number only (including trailing letters if present)",
  "revision_date": "Date as shown on form, or empty string if not found",
  "confidence": 0.85,
  "reasoning": "1-2 sentence explanation of extraction decisions and any challenges encountered"
}
```

## IMPORTANT REMINDERS
- When uncertain between options, explain your choice in the reasoning field
- Preserve exact capitalization and punctuation from the original form
- If multiple titles appear, select the most prominent/official one"""

        # Analyze the PDF
        result = self.analyze_pdf(
            pdf_path=pdf_path,
            query=extraction_prompt,
            max_tokens=1000,
            temperature=0.2  # Lower temperature for extraction tasks
        )
        
        if not result.success:
            return TitleExtractionResult(
                success=False,
                error=result.error
            )
        
        # Parse the JSON response
        try:
            # Clean up response text (remove markdown code blocks if present)
            response_text = result.response_text.strip()
            if response_text.startswith("```"):
                import re
                response_text = re.sub(r"^```(?:json)?\n?", "", response_text)
                response_text = re.sub(r"\n?```$", "", response_text)
            
            parsed = json.loads(response_text)
            
            # Format the title (capitalize words, clean punctuation but preserve parens)
            formatted_title = self._format_title(parsed.get("title", ""))
            
            return TitleExtractionResult(
                success=True,
                formatted_title=formatted_title,
                form_number=parsed.get("form_number", ""),
                revision_date=parsed.get("revision_date", ""),
                combined_confidence=float(parsed.get("confidence", 0.5)),
                reasoning=parsed.get("reasoning", ""),
                extraction_method="nova"
            )
            
        except json.JSONDecodeError as e:
            logger.error(
                "Failed to parse Nova response as JSON",
                error=str(e),
                response_preview=result.response_text[:200]
            )
            return TitleExtractionResult(
                success=False,
                error=f"Failed to parse model response: {str(e)}"
            )
    
    def extract_title(
        self,
        pdf_path: Path,
        preview_output_path: Optional[Path] = None
    ) -> TitleExtractionResult:
        """
        Alias for extract_title_and_form() to match TitleExtractor interface.
        
        This allows NovaDocumentProcessor to be used as a drop-in replacement
        for TitleExtractor in existing code.
        
        Args:
            pdf_path: Path to the PDF file
            preview_output_path: Optional path to save preview image
            
        Returns:
            TitleExtractionResult with extracted title and form information
        """
        return self.extract_title_and_form(pdf_path, preview_output_path)
    
    def _format_title(self, title: str) -> str:
        """
        Format the title: capitalize every word and clean unwanted punctuation,
        but preserve parentheses which are part of the official title.
        
        Args:
            title: The original title string
            
        Returns:
            Formatted title string
        """
        import re
        
        if not title:
            return ""
        
        # Preserve parentheses but remove other unwanted punctuation
        # Keep word characters, whitespace, and parentheses
        cleaned = re.sub(r"[^\w\s()]", "", title)
        cleaned = " ".join(cleaned.split())
        formatted = cleaned.title()
        return formatted
    
    def _generate_preview(self, pdf_path: Path, output_path: Path) -> bool:
        """
        Generate a preview image of the first page.
        
        Args:
            pdf_path: Path to the PDF
            output_path: Path to save the preview image
            
        Returns:
            True if successful
        """
        try:
            import fitz  # PyMuPDF
            
            doc = fitz.open(pdf_path)
            if len(doc) == 0:
                return False
            
            page = doc[0]
            mat = fitz.Matrix(150/72, 150/72)  # 150 DPI
            pix = page.get_pixmap(matrix=mat)
            
            output_path.parent.mkdir(parents=True, exist_ok=True)
            pix.save(str(output_path))
            
            doc.close()
            logger.debug("Generated preview image", path=str(output_path))
            return True
            
        except Exception as e:
            logger.warning("Failed to generate preview", error=str(e))
            return False
    
    def extract_structured_data(
        self,
        pdf_path: Path,
        fields: List[str]
    ) -> NovaAnalysisResult:
        """
        Extract specific structured fields from a document.
        
        Args:
            pdf_path: Path to the PDF file
            fields: List of field names to extract (e.g., ["name", "date", "amount"])
            
        Returns:
            NovaAnalysisResult with extracted_fields populated
        """
        fields_list = "\n".join(f"- {field}" for field in fields)
        
        extraction_prompt = f"""Extract the following fields from this document:
{fields_list}

Return your response as a JSON object where each key is a field name and 
the value is the extracted value (use empty string if not found).

Also include a "confidence" key with your overall confidence (0.0-1.0) in the extractions.

Return ONLY the JSON object, no other text."""

        result = self.analyze_pdf(
            pdf_path=pdf_path,
            query=extraction_prompt,
            max_tokens=2000,
            temperature=0.2
        )
        
        if not result.success:
            return result
        
        try:
            response_text = result.response_text.strip()
            if response_text.startswith("```"):
                import re
                response_text = re.sub(r"^```(?:json)?\n?", "", response_text)
                response_text = re.sub(r"\n?```$", "", response_text)
            
            parsed = json.loads(response_text)
            
            result.extracted_fields = {k: v for k, v in parsed.items() if k != "confidence"}
            result.confidence = float(parsed.get("confidence", 0.5))
            
            return result
            
        except json.JSONDecodeError as e:
            result.success = False
            result.error = f"Failed to parse response: {str(e)}"
            return result


# Singleton instance for convenience
_nova_processor: Optional[NovaDocumentProcessor] = None


def get_nova_processor() -> NovaDocumentProcessor:
    """Get or create the global NovaDocumentProcessor instance."""
    global _nova_processor
    if _nova_processor is None:
        _nova_processor = NovaDocumentProcessor()
    return _nova_processor

