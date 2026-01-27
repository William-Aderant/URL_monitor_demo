"""
IDP Enrichment Orchestrator Service

Orchestrates the full Intelligent Document Processing (IDP) enrichment pipeline:
1. Textract enrichment (Forms, Tables, Queries, Signatures)
2. Comprehend enrichment (Classification, NER)
3. Optional A2I for low-confidence human review
4. Persistence of enrichment results to database

This is an ADDITIVE feature that does not replace or modify
existing processing pipelines. All enrichment is optional and
controlled by configuration flags.
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any
import json
import boto3
from botocore.exceptions import ClientError
import structlog

from config import settings

logger = structlog.get_logger()


@dataclass
class IDPEnrichmentResult:
    """Combined result of all IDP enrichment steps."""
    success: bool
    error: Optional[str] = None
    
    # Processing status
    textract_processed: bool = False
    comprehend_processed: bool = False
    a2i_submitted: bool = False
    
    # Textract results (from textract_enrichment.py)
    textract_form_kv_pairs: Dict[str, str] = field(default_factory=dict)
    textract_form_confidence: float = 0.0
    textract_tables: list = field(default_factory=list)
    textract_queries_results: Dict[str, Any] = field(default_factory=dict)
    textract_signatures: list = field(default_factory=list)
    
    # Comprehend results (from comprehend_enrichment.py)
    comprehend_document_type: Optional[str] = None
    comprehend_document_type_confidence: float = 0.0
    comprehend_entities: Dict[str, Any] = field(default_factory=dict)
    
    # A2I results
    a2i_human_loop_arn: Optional[str] = None
    a2i_human_loop_status: Optional[str] = None
    
    # Extracted values (merged from all sources)
    extracted_form_number: Optional[str] = None
    extracted_revision_date: Optional[str] = None
    extracted_title: Optional[str] = None
    
    # Metadata
    processing_time_ms: int = 0
    features_used: list = field(default_factory=list)


class IDPEnrichmentOrchestrator:
    """
    Orchestrates the full IDP enrichment pipeline.
    
    Combines Textract and Comprehend enrichment with optional
    A2I human review for low-confidence items.
    
    All features are opt-in and controlled by configuration flags.
    This service does NOT modify existing processing pipelines.
    """
    
    def __init__(self):
        """Initialize the IDP enrichment orchestrator."""
        self._textract_service = None
        self._comprehend_service = None
        self._a2i_client = None
        
        logger.info(
            "IDPEnrichmentOrchestrator initialized",
            textract_forms=settings.TEXTRACT_FORMS_ENABLED,
            textract_tables=settings.TEXTRACT_TABLES_ENABLED,
            textract_queries=settings.TEXTRACT_QUERIES_ENABLED,
            textract_signatures=settings.TEXTRACT_SIGNATURES_ENABLED,
            comprehend_enabled=settings.COMPREHEND_ENABLED,
            a2i_enabled=settings.A2I_ENABLED
        )
    
    @property
    def textract_service(self):
        """Lazy-load Textract enrichment service."""
        if self._textract_service is None:
            from services.textract_enrichment import TextractEnrichmentService
            self._textract_service = TextractEnrichmentService()
        return self._textract_service
    
    @property
    def comprehend_service(self):
        """Lazy-load Comprehend enrichment service."""
        if self._comprehend_service is None:
            from services.comprehend_enrichment import ComprehendEnrichmentService
            self._comprehend_service = ComprehendEnrichmentService()
        return self._comprehend_service
    
    @property
    def a2i_client(self):
        """Lazy-load A2I client using default credential chain."""
        if self._a2i_client is None and settings.A2I_ENABLED:
            try:
                # Use explicit credentials if provided, otherwise boto3 uses default chain
                # (env vars -> ~/.aws/credentials -> IAM role)
                client_kwargs = {'region_name': settings.AWS_REGION}
                if settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY:
                    client_kwargs['aws_access_key_id'] = settings.AWS_ACCESS_KEY_ID
                    client_kwargs['aws_secret_access_key'] = settings.AWS_SECRET_ACCESS_KEY
                
                self._a2i_client = boto3.client('sagemaker-a2i-runtime', **client_kwargs)
            except Exception as e:
                logger.error("Failed to create A2I client", error=str(e))
        return self._a2i_client
    
    def is_any_feature_enabled(self) -> bool:
        """Check if any IDP enrichment feature is enabled."""
        return any([
            settings.TEXTRACT_FORMS_ENABLED,
            settings.TEXTRACT_TABLES_ENABLED,
            settings.TEXTRACT_QUERIES_ENABLED,
            settings.TEXTRACT_SIGNATURES_ENABLED,
            settings.COMPREHEND_ENABLED,
        ])
    
    def process_version(
        self,
        pdf_version_id: int,
        pdf_path: Path,
        text_content: str,
        url: str = "",
        force: bool = False
    ) -> IDPEnrichmentResult:
        """
        Run IDP enrichment pipeline for a PDF version.
        
        This is the main entry point for enrichment processing.
        Only enabled features will be processed.
        
        Args:
            pdf_version_id: Database ID of the PDF version
            pdf_path: Path to the PDF file
            text_content: Already-extracted text content
            url: Source URL (for logging)
            force: Force re-processing even if already enriched
            
        Returns:
            IDPEnrichmentResult with all enrichment data
        """
        import time
        start_time = time.time()
        
        logger.info(
            "Starting IDP enrichment pipeline",
            version_id=pdf_version_id,
            pdf_path=str(pdf_path),
            url=url
        )
        
        if not self.is_any_feature_enabled():
            return IDPEnrichmentResult(
                success=False,
                error="No IDP enrichment features enabled"
            )
        
        result = IDPEnrichmentResult(success=True)
        
        try:
            # Step 1: Textract enrichment (Forms, Tables, Queries, Signatures)
            if self.textract_service.is_available():
                self._run_textract_enrichment(pdf_path, url, result)
            
            # Step 2: Comprehend enrichment (Classification, NER)
            if self.comprehend_service.is_available() and text_content:
                self._run_comprehend_enrichment(text_content, url, result)
            
            # Step 3: Merge extracted values from all sources
            self._merge_extracted_values(result)
            
            # Step 4: Optional A2I for low-confidence items
            if settings.A2I_ENABLED and self._should_send_to_a2i(result):
                self._submit_to_a2i(pdf_version_id, result, url)
            
            # Calculate processing time
            elapsed_ms = int((time.time() - start_time) * 1000)
            result.processing_time_ms = elapsed_ms
            
            logger.info(
                "IDP enrichment pipeline completed",
                version_id=pdf_version_id,
                url=url,
                features=result.features_used,
                document_type=result.comprehend_document_type,
                form_number=result.extracted_form_number,
                elapsed_ms=elapsed_ms
            )
            
            return result
            
        except Exception as e:
            logger.error(
                "IDP enrichment pipeline failed",
                error=str(e),
                version_id=pdf_version_id,
                url=url
            )
            return IDPEnrichmentResult(
                success=False,
                error=str(e)
            )
    
    def _run_textract_enrichment(
        self,
        pdf_path: Path,
        url: str,
        result: IDPEnrichmentResult
    ) -> None:
        """Run Textract enrichment step."""
        textract_result = self.textract_service.process_document(pdf_path, url)
        
        if textract_result.success:
            result.textract_processed = True
            result.textract_form_kv_pairs = textract_result.form_kv_json
            result.textract_form_confidence = textract_result.form_avg_confidence
            result.textract_tables = textract_result.tables_json
            result.textract_queries_results = textract_result.queries_json
            result.textract_signatures = textract_result.signatures_json
            result.features_used.extend(textract_result.features_used)
        else:
            logger.warning(
                "Textract enrichment failed",
                error=textract_result.error,
                url=url
            )
    
    def _run_comprehend_enrichment(
        self,
        text_content: str,
        url: str,
        result: IDPEnrichmentResult
    ) -> None:
        """Run Comprehend enrichment step."""
        comprehend_result = self.comprehend_service.process_text(text_content, url)
        
        if comprehend_result.success:
            result.comprehend_processed = True
            result.comprehend_document_type = comprehend_result.document_type
            result.comprehend_document_type_confidence = comprehend_result.document_type_confidence
            result.comprehend_entities = comprehend_result.entities_json
            result.features_used.extend(comprehend_result.features_used)
        else:
            logger.warning(
                "Comprehend enrichment failed",
                error=comprehend_result.error,
                url=url
            )
    
    def _merge_extracted_values(self, result: IDPEnrichmentResult) -> None:
        """
        Merge extracted values from all enrichment sources.
        
        Priority for form number and revision date:
        1. Textract Queries (most targeted)
        2. Textract Forms key-value pairs
        3. Comprehend NER entities (as fallback)
        """
        # Extract form number
        form_number = None
        
        # Try Textract Queries first
        if result.textract_queries_results:
            for query, data in result.textract_queries_results.items():
                if 'form number' in query.lower():
                    form_number = data.get('answer', '').strip()
                    if form_number:
                        break
        
        # Try Textract Forms key-value pairs
        if not form_number and result.textract_form_kv_pairs:
            for key, value in result.textract_form_kv_pairs.items():
                if 'form' in key.lower() and ('number' in key.lower() or 'no' in key.lower()):
                    form_number = value.strip()
                    if form_number:
                        break
        
        result.extracted_form_number = form_number
        
        # Extract revision date
        revision_date = None
        
        # Try Textract Queries first
        if result.textract_queries_results:
            for query, data in result.textract_queries_results.items():
                if 'revision date' in query.lower() or 'date' in query.lower():
                    revision_date = data.get('answer', '').strip()
                    if revision_date:
                        break
        
        # Try Textract Forms key-value pairs
        if not revision_date and result.textract_form_kv_pairs:
            date_keys = ['revision date', 'rev date', 'revised', 'effective date', 'date']
            for key, value in result.textract_form_kv_pairs.items():
                if any(dk in key.lower() for dk in date_keys):
                    revision_date = value.strip()
                    if revision_date:
                        break
        
        # Try Comprehend NER DATE entities
        if not revision_date and result.comprehend_entities:
            dates = result.comprehend_entities.get('by_type', {}).get('DATE', [])
            if dates:
                # Take the first date (could be improved with heuristics)
                revision_date = dates[0]
        
        result.extracted_revision_date = revision_date
        
        # Extract title from Textract Queries
        if result.textract_queries_results:
            for query, data in result.textract_queries_results.items():
                if 'title' in query.lower():
                    result.extracted_title = data.get('answer', '').strip()
                    break
    
    def _should_send_to_a2i(self, result: IDPEnrichmentResult) -> bool:
        """
        Determine if this result should be sent to A2I for human review.
        
        Items are sent to A2I if:
        - A2I is enabled
        - Comprehend classification confidence is below threshold
        - Or Textract form extraction confidence is below threshold
        """
        if not settings.A2I_ENABLED or not settings.A2I_FLOW_DEFINITION_ARN:
            return False
        
        threshold = settings.A2I_CONFIDENCE_THRESHOLD
        
        # Check classification confidence
        if result.comprehend_document_type_confidence < threshold:
            return True
        
        # Check form extraction confidence
        if result.textract_form_confidence < threshold:
            return True
        
        return False
    
    def _submit_to_a2i(
        self,
        pdf_version_id: int,
        result: IDPEnrichmentResult,
        url: str
    ) -> None:
        """
        Submit item to Amazon A2I for human review.
        
        Creates a human loop for low-confidence items.
        """
        if not self.a2i_client:
            logger.warning("A2I client not available")
            return
        
        try:
            from services.api_counter import api_counter
            api_counter.increment('a2i_create_loop')
            
            # Create human loop input
            human_loop_input = {
                'pdf_version_id': pdf_version_id,
                'url': url,
                'document_type': result.comprehend_document_type,
                'document_type_confidence': result.comprehend_document_type_confidence,
                'form_kv_pairs': result.textract_form_kv_pairs,
                'form_confidence': result.textract_form_confidence,
                'extracted_form_number': result.extracted_form_number,
                'extracted_revision_date': result.extracted_revision_date,
            }
            
            # Generate unique human loop name
            import uuid
            human_loop_name = f"pdf-monitor-{pdf_version_id}-{uuid.uuid4().hex[:8]}"
            
            response = self.a2i_client.start_human_loop(
                HumanLoopName=human_loop_name,
                FlowDefinitionArn=settings.A2I_FLOW_DEFINITION_ARN,
                HumanLoopInput={
                    'InputContent': json.dumps(human_loop_input)
                }
            )
            
            result.a2i_submitted = True
            result.a2i_human_loop_arn = response.get('HumanLoopArn')
            result.a2i_human_loop_status = 'pending'
            
            logger.info(
                "Submitted to A2I for human review",
                human_loop_arn=result.a2i_human_loop_arn,
                version_id=pdf_version_id,
                url=url
            )
            
        except ClientError as e:
            logger.error(
                "Failed to submit to A2I",
                error=str(e),
                version_id=pdf_version_id
            )
    
    def persist_enrichment(
        self,
        pdf_version_id: int,
        result: IDPEnrichmentResult,
        db_session
    ) -> bool:
        """
        Persist enrichment results to database.
        
        Updates the PDFVersion record with enrichment data.
        This is ADDITIVE - only updates enrichment columns,
        does not modify existing data.
        
        Args:
            pdf_version_id: Database ID of the PDF version
            result: IDPEnrichmentResult to persist
            db_session: SQLAlchemy session
            
        Returns:
            True if successful, False otherwise
        """
        try:
            from db.models import PDFVersion
            
            version = db_session.query(PDFVersion).filter_by(id=pdf_version_id).first()
            if not version:
                logger.error("PDFVersion not found", version_id=pdf_version_id)
                return False
            
            # Update Comprehend fields
            if result.comprehend_processed:
                version.comprehend_document_type = result.comprehend_document_type
                version.comprehend_document_type_confidence = result.comprehend_document_type_confidence
                version.comprehend_entities = result.comprehend_entities
            
            # Update Textract fields
            if result.textract_processed:
                version.textract_form_kv_pairs = result.textract_form_kv_pairs
                version.textract_form_confidence = result.textract_form_confidence
                version.textract_tables = result.textract_tables
                version.textract_queries_results = result.textract_queries_results
                version.textract_signatures = result.textract_signatures
            
            # Update A2I fields
            if result.a2i_submitted:
                version.a2i_human_loop_arn = result.a2i_human_loop_arn
                version.a2i_human_loop_status = result.a2i_human_loop_status
            
            # Update enrichment status
            version.idp_enrichment_status = 'completed' if result.success else 'failed'
            version.idp_enrichment_at = datetime.utcnow()
            if not result.success and result.error:
                version.idp_enrichment_error = result.error
            
            db_session.commit()
            
            logger.info(
                "Enrichment results persisted",
                version_id=pdf_version_id,
                status=version.idp_enrichment_status
            )
            
            return True
            
        except Exception as e:
            logger.error(
                "Failed to persist enrichment results",
                error=str(e),
                version_id=pdf_version_id
            )
            db_session.rollback()
            return False


# Singleton instance
_idp_orchestrator: Optional[IDPEnrichmentOrchestrator] = None


def get_idp_orchestrator() -> IDPEnrichmentOrchestrator:
    """Get or create the global IDPEnrichmentOrchestrator instance."""
    global _idp_orchestrator
    if _idp_orchestrator is None:
        _idp_orchestrator = IDPEnrichmentOrchestrator()
    return _idp_orchestrator
