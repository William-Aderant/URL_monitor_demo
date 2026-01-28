"""
AWS Bedrock Data Automation (BDA) Enrichment Service

Provides document processing using AWS Bedrock Data Automation:
- Unified document processing for title/form extraction
- Title, form number, and revision date extraction
- Document summary and element extraction

Used when BDA_ENABLED=True for all title/form extraction.
"""

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Dict, Any, List

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.exceptions import ClientError, NoCredentialsError
import structlog

from config import settings

# S3 transfer config for faster uploads (multipart, concurrent)
_S3_TRANSFER_CONFIG = TransferConfig(
    multipart_threshold=5 * 1024 * 1024,  # 5MB threshold for multipart
    max_concurrency=10,
    multipart_chunksize=5 * 1024 * 1024,
    use_threads=True
)

logger = structlog.get_logger()


@dataclass
class BDAExtractionResult:
    """Result of BDA document extraction, compatible with TitleExtractionResult."""
    success: bool
    formatted_title: Optional[str] = None
    form_number: Optional[str] = None
    revision_date: Optional[str] = None
    combined_confidence: Optional[float] = None
    ocr_confidence: Optional[float] = None
    llm_confidence: Optional[float] = None
    reasoning: Optional[str] = None
    error: Optional[str] = None
    extraction_method: str = "bda"
    
    # BDA-specific fields
    document_summary: Optional[str] = None
    document_description: Optional[str] = None
    raw_text: Optional[str] = None
    elements: List[Dict[str, Any]] = field(default_factory=list)
    processing_time_ms: int = 0
    
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


class BDAEnrichmentService:
    """
    AWS Bedrock Data Automation service for document processing.
    
    Provides unified document processing that extracts:
    - Document title (from TITLE elements or summary)
    - Form number (via regex patterns)
    - Revision date (via regex patterns)
    
    Requires S3 for input/output as BDA processes files from S3.
    """
    
    # Form number patterns for court forms
    FORM_NUMBER_PATTERNS = [
        # Standard patterns: ADR-103, MC-025, CIV-775, APP-108
        r'\b([A-Z]{2,4}-\d{2,4}(?:/[A-Z]{2,4}-\d{2,4}(?:-[A-Z]+)?)?)\b',
        # Patterns with dots: MC.030
        r'\b([A-Z]{2,4}\.\d{2,4})\b',
        # Extended patterns: FL-100A, DV-100A
        r'\b([A-Z]{2,4}-\d{2,4}[A-Z])\b',
        # Form numbers in parentheses
        r'\(([A-Z]{2,4}-\d{2,4})\)',
    ]
    
    # Revision date patterns
    REVISION_DATE_PATTERNS = [
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
    ]
    
    def __init__(
        self,
        aws_access_key: Optional[str] = None,
        aws_secret_key: Optional[str] = None,
        aws_region: Optional[str] = None
    ):
        """
        Initialize BDA enrichment service.
        
        Args:
            aws_access_key: AWS access key ID (uses settings if not provided)
            aws_secret_key: AWS secret access key (uses settings if not provided)
            aws_region: AWS region (uses settings if not provided)
        """
        self.aws_access_key = aws_access_key or settings.AWS_ACCESS_KEY_ID
        self.aws_secret_key = aws_secret_key or settings.AWS_SECRET_ACCESS_KEY
        self.aws_region = aws_region or settings.AWS_REGION
        
        self._bda_client = None
        self._bda_runtime_client = None
        self._s3_client = None
        self._available = None
        self._project_arn = settings.BDA_PROJECT_ARN or None
        self._profile_arn = settings.BDA_PROFILE_ARN or None
        
        logger.info(
            "BDAEnrichmentService initialized",
            region=self.aws_region,
            bda_enabled=settings.BDA_ENABLED,
            s3_bucket=settings.BDA_S3_BUCKET,
            project_arn=self._project_arn,
            profile_arn=self._profile_arn
        )
    
    def _get_client_kwargs(self) -> dict:
        """Get common kwargs for boto3 clients."""
        kwargs = {'region_name': self.aws_region}
        if self.aws_access_key and self.aws_secret_key:
            kwargs['aws_access_key_id'] = self.aws_access_key
            kwargs['aws_secret_access_key'] = self.aws_secret_key
        return kwargs
    
    @property
    def bda_client(self):
        """Lazy-load BDA client for project management."""
        if self._bda_client is None:
            try:
                self._bda_client = boto3.client(
                    'bedrock-data-automation',
                    **self._get_client_kwargs()
                )
            except Exception as e:
                logger.error("Failed to create BDA client", error=str(e))
                raise
        return self._bda_client
    
    @property
    def bda_runtime_client(self):
        """Lazy-load BDA runtime client for invocations."""
        if self._bda_runtime_client is None:
            try:
                self._bda_runtime_client = boto3.client(
                    'bedrock-data-automation-runtime',
                    **self._get_client_kwargs()
                )
            except Exception as e:
                logger.error("Failed to create BDA runtime client", error=str(e))
                raise
        return self._bda_runtime_client
    
    @property
    def s3_client(self):
        """Lazy-load S3 client."""
        if self._s3_client is None:
            try:
                self._s3_client = boto3.client('s3', **self._get_client_kwargs())
            except Exception as e:
                logger.error("Failed to create S3 client", error=str(e))
                raise
        return self._s3_client
    
    def is_available(self) -> bool:
        """Check if BDA is enabled and available."""
        if self._available is not None:
            return self._available
        
        if not settings.BDA_ENABLED:
            logger.debug("BDA is not enabled")
            self._available = False
            return False
        
        if not settings.BDA_S3_BUCKET:
            logger.warning("BDA_S3_BUCKET is not configured")
            self._available = False
            return False
        
        if not settings.BDA_PROFILE_ARN:
            logger.warning("BDA_PROFILE_ARN is not configured (required for BDA)")
            self._available = False
            return False
        
        try:
            # Try to create clients to verify credentials
            self.bda_client
            self.bda_runtime_client
            self.s3_client
            self._available = True
            logger.info("BDA enrichment service is available")
            return True
        except NoCredentialsError:
            logger.warning("AWS credentials not found, BDA unavailable")
            self._available = False
            return False
        except Exception as e:
            logger.warning("BDA availability check failed", error=str(e))
            self._available = False
            return False
    
    def _upload_to_s3(self, pdf_path: Path, url: str = "") -> Optional[str]:
        """
        Upload PDF to S3 for BDA processing.
        
        Args:
            pdf_path: Local path to PDF file
            url: Source URL (for logging)
            
        Returns:
            S3 URI of uploaded file, or None if upload fails
        """
        try:
            # Generate unique key for the upload
            unique_id = uuid.uuid4().hex[:8]
            filename = pdf_path.name
            s3_key = f"{settings.BDA_S3_PREFIX.rstrip('/')}/{unique_id}_{filename}"
            
            logger.debug(
                "Uploading PDF to S3",
                local_path=str(pdf_path),
                bucket=settings.BDA_S3_BUCKET,
                key=s3_key
            )
            
            self.s3_client.upload_file(
                str(pdf_path),
                settings.BDA_S3_BUCKET,
                s3_key,
                Config=_S3_TRANSFER_CONFIG
            )
            
            s3_uri = f"s3://{settings.BDA_S3_BUCKET}/{s3_key}"
            logger.info("PDF uploaded to S3", s3_uri=s3_uri, url=url)
            return s3_uri
            
        except ClientError as e:
            logger.error("S3 upload failed", error=str(e), url=url)
            return None
        except Exception as e:
            logger.error("S3 upload error", error=str(e), url=url)
            return None
    
    def _cleanup_s3(self, s3_uri: str) -> None:
        """Clean up uploaded S3 file after processing."""
        try:
            if not s3_uri.startswith("s3://"):
                return
            
            # Parse bucket and key from URI
            parts = s3_uri[5:].split("/", 1)
            if len(parts) != 2:
                return
            
            bucket, key = parts
            self.s3_client.delete_object(Bucket=bucket, Key=key)
            logger.debug("Cleaned up S3 input file", s3_uri=s3_uri)
        except Exception as e:
            logger.warning("Failed to cleanup S3 file", s3_uri=s3_uri, error=str(e))
    
    def _get_document_standard_output_config(self) -> Dict[str, Any]:
        """
        Get standard output configuration for document processing.
        
        This ensures BDA produces document extraction output (elements, text, etc.)
        not just job_metadata.json.
        """
        return {
            'document': {
                'extraction': {
                    'granularity': {
                        'types': ['DOCUMENT', 'PAGE', 'ELEMENT']
                    },
                    'boundingBox': {'state': 'ENABLED'}
                },
                'outputFormat': {
                    'textFormat': {
                        'types': ['PLAIN_TEXT', 'MARKDOWN']
                    },
                    'additionalFileFormat': {'state': 'DISABLED'}
                },
                'generativeField': {
                    'state': 'ENABLED'
                }
            }
        }
    
    def _ensure_project_has_document_config(self, project_arn: str) -> bool:
        """
        Ensure the BDA project has document extraction enabled.
        
        If not enabled, updates the project configuration.
        
        Args:
            project_arn: The project ARN to check/update
            
        Returns:
            True if project is properly configured, False otherwise
        """
        try:
            # Get current project configuration
            response = self.bda_client.get_data_automation_project(
                projectArn=project_arn,
                projectStage='LIVE'
            )
            
            current_config = response.get('project', {}).get('standardOutputConfiguration', {})
            document_config = current_config.get('document', {})
            
            # Check if document extraction is configured
            extraction = document_config.get('extraction', {})
            generative = document_config.get('generativeField', {})
            
            has_extraction = bool(extraction.get('granularity', {}).get('types'))
            has_generative = generative.get('state') == 'ENABLED'
            
            if has_extraction and has_generative:
                logger.debug("BDA project has document extraction enabled", arn=project_arn)
                return True
            
            # Update project with document extraction config
            logger.warning(
                "BDA project missing document config, updating...",
                arn=project_arn,
                has_extraction=has_extraction,
                has_generative=has_generative
            )
            
            self.bda_client.update_data_automation_project(
                projectArn=project_arn,
                projectStage='LIVE',
                standardOutputConfiguration=self._get_document_standard_output_config()
            )
            
            logger.info("Updated BDA project with document extraction config", arn=project_arn)
            return True
            
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', '')
            if error_code == 'ValidationException':
                logger.warning(
                    "Cannot update BDA project (may need manual config in console)",
                    arn=project_arn,
                    error=str(e)
                )
            else:
                logger.error("Failed to check/update BDA project config", error=str(e))
            return False
        except Exception as e:
            logger.error("Error checking BDA project config", error=str(e))
            return False
    
    def _create_or_get_project(self) -> Optional[str]:
        """
        Create or get BDA project ARN with document extraction enabled.
        
        If BDA_PROJECT_ARN is configured, validates it has document extraction
        enabled and updates if necessary.
        
        Returns:
            Project ARN, or None if creation fails
        """
        # Use pre-configured project ARN if available
        if self._project_arn:
            # Validate the project has document extraction enabled
            if not self._ensure_project_has_document_config(self._project_arn):
                logger.warning(
                    "Pre-configured BDA project may not have document extraction enabled. "
                    "If title extraction fails, clear BDA_PROJECT_ARN to auto-create a new project.",
                    arn=self._project_arn
                )
            return self._project_arn
        
        try:
            # Check for existing project
            response = self.bda_client.list_data_automation_projects(maxResults=100)
            projects = response.get('projects', [])
            
            # Look for existing pdf-monitor project
            for project in projects:
                if 'pdf-monitor' in project.get('projectName', '').lower():
                    project_arn = project['projectArn']
                    self._ensure_project_has_document_config(project_arn)
                    self._project_arn = project_arn
                    logger.info("Found existing BDA project", arn=self._project_arn)
                    return self._project_arn
            
            # Create new project with document configuration
            project_name = f"pdf-monitor-{uuid.uuid4().hex[:8]}"
            
            response = self.bda_client.create_data_automation_project(
                projectName=project_name,
                standardOutputConfiguration=self._get_document_standard_output_config()
            )
            
            self._project_arn = response['projectArn']
            logger.info("Created new BDA project", arn=self._project_arn, name=project_name)
            return self._project_arn
            
        except ClientError as e:
            logger.error("Failed to create/get BDA project", error=str(e))
            return None
        except Exception as e:
            logger.error("BDA project error", error=str(e))
            return None
    
    def process_document(
        self,
        pdf_path: Path,
        url: str = "",
        preview_output_path: Optional[Path] = None
    ) -> BDAExtractionResult:
        """
        Process PDF document using BDA.
        
        Args:
            pdf_path: Path to the PDF file
            url: Source URL (for logging)
            preview_output_path: Optional path for preview image (not used by BDA)
            
        Returns:
            BDAExtractionResult with extracted information
        """
        start_time = time.time()
        
        logger.info("Starting BDA document processing", pdf_path=str(pdf_path), url=url)
        
        if not self.is_available():
            return BDAExtractionResult(
                success=False,
                error="BDA not available - check configuration and credentials"
            )
        
        if not pdf_path.exists():
            return BDAExtractionResult(
                success=False,
                error=f"File not found: {pdf_path}"
            )
        
        s3_uri = None
        try:
            # Step 1: Upload PDF to S3
            s3_uri = self._upload_to_s3(pdf_path, url)
            if not s3_uri:
                return BDAExtractionResult(
                    success=False,
                    error="Failed to upload PDF to S3"
                )
            
            # Step 2: Get or create BDA project
            project_arn = self._create_or_get_project()
            if not project_arn:
                return BDAExtractionResult(
                    success=False,
                    error="Failed to get BDA project"
                )
            
            # Step 3: Invoke BDA processing
            # Track API call
            from services.api_counter import api_counter
            api_counter.increment('bedrock')
            
            output_s3_uri = f"s3://{settings.BDA_S3_BUCKET}/{settings.BDA_OUTPUT_PREFIX.rstrip('/')}/{uuid.uuid4().hex[:8]}/"
            
            invoke_response = self.bda_runtime_client.invoke_data_automation_async(
                inputConfiguration={
                    's3Uri': s3_uri
                },
                outputConfiguration={
                    's3Uri': output_s3_uri
                },
                dataAutomationConfiguration={
                    'dataAutomationProjectArn': project_arn,
                    'stage': 'LIVE'
                },
                dataAutomationProfileArn=self._profile_arn
            )
            
            invocation_arn = invoke_response['invocationArn']
            logger.info("BDA invocation started", invocation_arn=invocation_arn, url=url)
            
            # Step 4: Poll for completion
            result = self._poll_for_completion(invocation_arn, url)
            if not result['success']:
                return BDAExtractionResult(
                    success=False,
                    error=result.get('error', 'BDA processing failed')
                )
            
            # Step 5: Download and parse results
            output_uri = result.get('output_uri')
            if not output_uri:
                return BDAExtractionResult(
                    success=False,
                    error="No output URI from BDA"
                )
            
            bda_output = self._download_and_parse_output(output_uri, url)
            if not bda_output:
                return BDAExtractionResult(
                    success=False,
                    error="Failed to parse BDA output"
                )
            
            # Step 6: Extract title, form number, revision date
            extraction_result = self._extract_document_info(bda_output, url)
            
            # Calculate processing time
            elapsed_ms = int((time.time() - start_time) * 1000)
            extraction_result.processing_time_ms = elapsed_ms
            
            logger.info(
                "BDA processing completed",
                url=url,
                title=extraction_result.formatted_title,
                form_number=extraction_result.form_number,
                revision_date=extraction_result.revision_date,
                elapsed_ms=elapsed_ms
            )
            
            return extraction_result
            
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_msg = e.response.get('Error', {}).get('Message', str(e))
            logger.error("BDA API error", error_code=error_code, error=error_msg, url=url)
            return BDAExtractionResult(
                success=False,
                error=f"BDA error: {error_code} - {error_msg}"
            )
        except Exception as e:
            logger.error("BDA processing failed", error=str(e), url=url)
            return BDAExtractionResult(
                success=False,
                error=str(e)
            )
        finally:
            # Cleanup input file from S3
            if s3_uri:
                self._cleanup_s3(s3_uri)
    
    def _poll_for_completion(
        self,
        invocation_arn: str,
        url: str
    ) -> Dict[str, Any]:
        """
        Poll BDA for job completion with adaptive backoff.
        
        Starts polling fast (BDA_POLL_INTERVAL, default 0.5s) then increases
        interval gradually up to 3s for long-running jobs. This optimizes for
        the common case (fast completion) while not hammering the API.
        
        Args:
            invocation_arn: The invocation ARN to check
            url: Source URL (for logging)
            
        Returns:
            Dict with 'success', 'output_uri', and optional 'error'
        """
        timeout = settings.BDA_TIMEOUT_SECONDS
        base_interval = settings.BDA_POLL_INTERVAL
        max_interval = 3.0  # Cap at 3s between polls
        current_interval = base_interval
        start_time = time.time()
        poll_count = 0
        
        while (time.time() - start_time) < timeout:
            try:
                response = self.bda_runtime_client.get_data_automation_status(
                    invocationArn=invocation_arn
                )
                
                status = response.get('status', '')
                
                if status == 'Success':
                    output_config = response.get('outputConfiguration', {})
                    output_uri = output_config.get('s3Uri', '')
                    elapsed = time.time() - start_time
                    logger.debug("BDA job completed", elapsed_s=f"{elapsed:.1f}", polls=poll_count)
                    return {
                        'success': True,
                        'output_uri': output_uri
                    }
                elif status in ['ServiceError', 'ClientError']:
                    error_msg = response.get('errorMessage', 'Unknown error')
                    return {
                        'success': False,
                        'error': f"BDA {status}: {error_msg}"
                    }
                elif status in ['Created', 'InProgress']:
                    elapsed = int(time.time() - start_time)
                    # Only log every few polls to reduce noise
                    if poll_count % 5 == 0:
                        logger.debug(
                            "BDA job in progress",
                            status=status,
                            elapsed=elapsed,
                            url=url
                        )
                    time.sleep(current_interval)
                    poll_count += 1
                    # Adaptive backoff: increase interval by 20% each poll, cap at max_interval
                    current_interval = min(current_interval * 1.2, max_interval)
                else:
                    logger.warning("Unknown BDA status", status=status)
                    time.sleep(current_interval)
                    poll_count += 1
                    
            except Exception as e:
                logger.error("Error polling BDA status", error=str(e))
                return {
                    'success': False,
                    'error': f"Polling error: {str(e)}"
                }
        
        return {
            'success': False,
            'error': f"BDA processing timed out after {timeout} seconds"
        }
    
    def _download_and_parse_output(
        self,
        output_uri: str,
        url: str
    ) -> Optional[Dict[str, Any]]:
        """
        Download and parse BDA output from S3.
        
        Args:
            output_uri: S3 URI of output location
            url: Source URL (for logging)
            
        Returns:
            Parsed BDA output dict, or None if parsing fails
        """
        try:
            # Parse bucket and prefix from URI
            if not output_uri.startswith("s3://"):
                logger.error("Invalid S3 URI", uri=output_uri)
                return None
            
            parts = output_uri[5:].split("/", 1)
            if len(parts) != 2:
                logger.error("Invalid S3 URI format", uri=output_uri)
                return None
            
            bucket, prefix = parts
            
            # List objects in the output location
            response = self.s3_client.list_objects_v2(
                Bucket=bucket,
                Prefix=prefix
            )
            
            # BDA writes job_metadata.json (status only) and document output JSON (document/elements/pages).
            # We must use the document output, not job_metadata, or title/form extraction will be empty.
            def has_document_content(data: Dict[str, Any]) -> bool:
                return bool(
                    data.get("document") or data.get("elements") or data.get("pages") or data.get("entities")
                )
            
            output_data = None
            for obj in response.get("Contents", []):
                key = obj["Key"]
                if not key.endswith(".json") or "job_metadata" in key.lower():
                    continue
                try:
                    obj_response = self.s3_client.get_object(Bucket=bucket, Key=key)
                    content = obj_response["Body"].read().decode("utf-8")
                    data = json.loads(content)
                    if has_document_content(data):
                        output_data = data
                        logger.debug("Downloaded BDA document output", key=key)
                        break
                except (ClientError, json.JSONDecodeError):
                    continue
            
            if output_data is None:
                logger.warning(
                    "No BDA document output JSON found (only job_metadata or non-document JSON); title extraction will be empty",
                    uri=output_uri,
                    keys=[o["Key"] for o in response.get("Contents", [])]
                )
            
            return output_data
            
        except ClientError as e:
            logger.error("Failed to download BDA output", error=str(e), uri=output_uri)
            return None
        except json.JSONDecodeError as e:
            logger.error("Failed to parse BDA output JSON", error=str(e))
            return None
        except Exception as e:
            logger.error("Error processing BDA output", error=str(e))
            return None
    
    def _extract_document_info(
        self,
        bda_output: Dict[str, Any],
        url: str
    ) -> BDAExtractionResult:
        """
        Extract title, form number, and revision date from BDA output.
        
        Args:
            bda_output: Parsed BDA output
            url: Source URL (for logging)
            
        Returns:
            BDAExtractionResult with extracted information
        """
        result = BDAExtractionResult(success=True)
        
        try:
            # Get document-level data
            document_data = bda_output.get('document', {})
            
            # Extract summary and description
            result.document_summary = document_data.get('summary', '')
            result.document_description = document_data.get('description', '')
            
            # Get raw text representation
            representation = document_data.get('representation', {})
            result.raw_text = representation.get('text', '') or representation.get('markdown', '')
            
            # Get elements for TITLE extraction
            elements = bda_output.get('elements', [])
            # Also check for entities or pages
            if not elements:
                elements = bda_output.get('entities', [])
            if not elements:
                pages = bda_output.get('pages', [])
                for page in pages:
                    elements.extend(page.get('elements', []))
            
            result.elements = elements
            
            # Extract title from elements
            title = self._extract_title_from_elements(elements, result.raw_text)
            if title:
                result.formatted_title = self._format_title(title)
            elif result.document_summary:
                # Fall back to extracting title from summary
                result.formatted_title = self._extract_title_from_summary(result.document_summary)
            
            # Extract form number
            text_to_search = result.raw_text or result.document_description or ''
            result.form_number = self._extract_form_number(text_to_search)
            
            # Extract revision date
            result.revision_date = self._extract_revision_date(text_to_search)
            
            # Calculate confidence based on extraction success
            confidence_factors = []
            if result.formatted_title:
                confidence_factors.append(0.9)
            if result.form_number:
                confidence_factors.append(0.9)
            if result.revision_date:
                confidence_factors.append(0.8)
            
            if confidence_factors:
                result.combined_confidence = sum(confidence_factors) / len(confidence_factors)
            else:
                result.combined_confidence = 0.3
            
            result.reasoning = f"Extracted via BDA: title={'found' if result.formatted_title else 'not found'}, form_number={'found' if result.form_number else 'not found'}, revision_date={'found' if result.revision_date else 'not found'}"
            
            logger.debug(
                "Document info extracted",
                title=result.formatted_title,
                form_number=result.form_number,
                revision_date=result.revision_date,
                confidence=result.combined_confidence,
                url=url
            )
            
            return result
            
        except Exception as e:
            logger.error("Error extracting document info", error=str(e), url=url)
            return BDAExtractionResult(
                success=False,
                error=f"Extraction error: {str(e)}"
            )
    
    def _extract_title_from_elements(
        self,
        elements: List[Dict[str, Any]],
        raw_text: str
    ) -> Optional[str]:
        """
        Extract title from BDA document elements.
        
        Looks for elements with sub_type TITLE or SECTION_TITLE.
        
        Args:
            elements: List of BDA elements
            raw_text: Raw document text as fallback
            
        Returns:
            Title string or None
        """
        # Look for TITLE or SECTION_TITLE elements
        title_elements = []
        for element in elements:
            element_type = element.get('type', '')
            sub_type = element.get('sub_type', '') or element.get('subType', '')
            
            if element_type == 'TEXT' and sub_type in ['TITLE', 'SECTION_TITLE', 'HEADER']:
                rep = element.get('representation', {})
                text = rep.get('text', '') or element.get('text', '')
                if text:
                    title_elements.append({
                        'text': text.strip(),
                        'sub_type': sub_type,
                        'reading_order': element.get('reading_order', element.get('readingOrder', 999))
                    })
        
        # Sort by reading order and prefer TITLE over SECTION_TITLE
        if title_elements:
            # Prioritize TITLE over SECTION_TITLE
            title_elements.sort(key=lambda x: (
                0 if x['sub_type'] == 'TITLE' else 1,
                x['reading_order']
            ))
            return title_elements[0]['text']
        
        # Fallback: Extract first substantial line from raw text
        if raw_text:
            lines = raw_text.strip().split('\n')
            for line in lines[:5]:  # Check first 5 lines
                line = line.strip()
                # Skip short lines or lines that look like form numbers
                if len(line) > 10 and not re.match(r'^[A-Z]{2,4}-\d{2,4}', line):
                    return line
        
        return None
    
    def _extract_title_from_summary(self, summary: str) -> Optional[str]:
        """
        Extract a title from the document summary.
        
        Args:
            summary: Document summary from BDA
            
        Returns:
            Extracted title or None
        """
        if not summary:
            return None
        
        # Look for patterns like "This document is a..." or "This is a..."
        patterns = [
            r'This (?:document|form) is (?:a|an) (.+?)(?:\.|,)',
            r'titled "(.+?)"',
            r"titled '(.+?)'",
            r'called "(.+?)"',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, summary, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        
        # Return first sentence as fallback (cleaned up)
        first_sentence = summary.split('.')[0].strip()
        if len(first_sentence) > 10 and len(first_sentence) < 200:
            return first_sentence
        
        return None
    
    def _extract_form_number(self, text: str) -> Optional[str]:
        """
        Extract form number from document text.
        
        Args:
            text: Document text to search
            
        Returns:
            Form number or None
        """
        if not text:
            return None
        
        for pattern in self.FORM_NUMBER_PATTERNS:
            matches = re.findall(pattern, text[:2000])  # Search first 2000 chars
            if matches:
                # Return the first match that looks like a valid form number
                for match in matches:
                    if self._is_valid_form_number(match):
                        return match
        
        return None
    
    def _is_valid_form_number(self, form_number: str) -> bool:
        """Check if a string looks like a valid court form number."""
        if not form_number:
            return False
        
        # Must have letters and numbers
        if not re.search(r'[A-Z]', form_number):
            return False
        if not re.search(r'\d', form_number):
            return False
        
        # Common prefixes for court forms
        common_prefixes = [
            'MC', 'CIV', 'ADR', 'APP', 'FL', 'DV', 'CR', 'JV', 'SC', 'PR',
            'CM', 'AT', 'GC', 'FW', 'DE', 'EA', 'EJ', 'FA', 'UD', 'POS',
            'TR', 'WG', 'EFS', 'CTL', 'CA', 'CV', 'MH', 'PS'
        ]
        
        for prefix in common_prefixes:
            if form_number.upper().startswith(prefix):
                return True
        
        # Accept pattern like XX-NNN
        if re.match(r'^[A-Z]{2,4}-\d{2,4}', form_number):
            return True
        
        return False
    
    def _extract_revision_date(self, text: str) -> Optional[str]:
        """
        Extract revision date from document text.
        
        Args:
            text: Document text to search
            
        Returns:
            Revision date string or None
        """
        if not text:
            return None
        
        for pattern in self.REVISION_DATE_PATTERNS:
            match = re.search(pattern, text[:3000], re.IGNORECASE)  # Search first 3000 chars
            if match:
                date_str = match.group(1).strip()
                logger.debug("Extracted revision date", date=date_str, pattern=pattern)
                return date_str
        
        return None
    
    def _format_title(self, title: str) -> str:
        """
        Format the title: capitalize words and clean up.
        
        Args:
            title: Original title string
            
        Returns:
            Formatted title string
        """
        if not title:
            return ""
        
        # Remove extra whitespace
        title = ' '.join(title.split())
        
        # Preserve parentheses but remove other unwanted punctuation
        cleaned = re.sub(r"[^\w\s()]", "", title)
        cleaned = ' '.join(cleaned.split())
        
        # Title case
        formatted = cleaned.title()
        
        return formatted


# Singleton instance for convenience
_bda_enrichment_service: Optional[BDAEnrichmentService] = None


def get_bda_enrichment_service() -> BDAEnrichmentService:
    """Get or create the global BDAEnrichmentService instance."""
    global _bda_enrichment_service
    if _bda_enrichment_service is None:
        _bda_enrichment_service = BDAEnrichmentService()
    return _bda_enrichment_service
