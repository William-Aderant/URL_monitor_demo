"""
Amazon Comprehend Enrichment Service

Provides NLP enrichment features using Amazon Comprehend:
- Document classification (form type detection)
- Named Entity Recognition (NER) for dates, organizations, etc.

This is an ADDITIVE feature that does not replace or modify
existing form matching or title extraction functionality.
All features are opt-in via configuration flags.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List, Dict, Any
import boto3
from botocore.exceptions import ClientError, NoCredentialsError
import structlog

from config import settings

logger = structlog.get_logger()


# Document type labels for court forms
DOCUMENT_TYPE_LABELS = [
    "motion",
    "petition",
    "order",
    "cover_sheet",
    "summons",
    "proof_of_service",
    "declaration",
    "affidavit",
    "complaint",
    "answer",
    "notice",
    "stipulation",
    "judgment",
    "subpoena",
    "writ",
    "application",
    "certificate",
    "other"
]


@dataclass
class DocumentClassification:
    """Result of document classification."""
    document_type: str
    confidence: float
    all_classes: Dict[str, float] = field(default_factory=dict)


@dataclass
class ExtractedEntity:
    """A single extracted entity."""
    text: str
    entity_type: str  # DATE, ORGANIZATION, PERSON, LOCATION, etc.
    confidence: float
    begin_offset: int = 0
    end_offset: int = 0


@dataclass
class ComprehendEnrichmentResult:
    """Complete result of Comprehend enrichment processing."""
    success: bool
    error: Optional[str] = None
    
    # Document classification
    document_type: Optional[str] = None
    document_type_confidence: float = 0.0
    classification_all_classes: Dict[str, float] = field(default_factory=dict)
    
    # Named entities
    entities: List[ExtractedEntity] = field(default_factory=list)
    entities_by_type: Dict[str, List[str]] = field(default_factory=dict)
    entities_json: Dict[str, Any] = field(default_factory=dict)
    
    # Processing metadata
    features_used: List[str] = field(default_factory=list)
    text_length: int = 0
    processing_time_ms: int = 0


class ComprehendEnrichmentService:
    """
    Amazon Comprehend enrichment service for IDP features.
    
    Provides document classification and named entity recognition.
    
    All features are opt-in and controlled by configuration flags.
    This service does NOT modify or replace existing form matching
    or title extraction functionality.
    """
    
    # Comprehend has a 100KB limit for sync operations
    MAX_TEXT_BYTES = 100 * 1024
    
    # Minimum text length for meaningful analysis
    MIN_TEXT_LENGTH = 50
    
    def __init__(
        self,
        aws_access_key: Optional[str] = None,
        aws_secret_key: Optional[str] = None,
        aws_region: Optional[str] = None
    ):
        """
        Initialize Comprehend enrichment service.
        
        Args:
            aws_access_key: AWS access key ID (uses settings if not provided)
            aws_secret_key: AWS secret access key (uses settings if not provided)
            aws_region: AWS region (uses settings if not provided)
        """
        self.aws_access_key = aws_access_key or settings.AWS_ACCESS_KEY_ID
        self.aws_secret_key = aws_secret_key or settings.AWS_SECRET_ACCESS_KEY
        self.aws_region = aws_region or settings.AWS_REGION
        
        self._client = None
        self._available = None
        
        logger.info(
            "ComprehendEnrichmentService initialized",
            region=self.aws_region,
            comprehend_enabled=settings.COMPREHEND_ENABLED,
            classification_enabled=settings.COMPREHEND_CLASSIFICATION_ENABLED,
            ner_enabled=settings.COMPREHEND_NER_ENABLED
        )
    
    @property
    def client(self):
        """Lazy-load Comprehend client using default credential chain."""
        if self._client is None:
            try:
                # Use explicit credentials if provided, otherwise boto3 uses default chain
                # (env vars -> ~/.aws/credentials -> IAM role)
                client_kwargs = {'region_name': self.aws_region}
                if self.aws_access_key and self.aws_secret_key:
                    client_kwargs['aws_access_key_id'] = self.aws_access_key
                    client_kwargs['aws_secret_access_key'] = self.aws_secret_key
                
                self._client = boto3.client('comprehend', **client_kwargs)
            except Exception as e:
                logger.error("Failed to create Comprehend client", error=str(e))
                raise
        return self._client
    
    def is_available(self) -> bool:
        """Check if Comprehend enrichment features are available."""
        if self._available is not None:
            return self._available
        
        if not settings.COMPREHEND_ENABLED:
            logger.debug("Comprehend enrichment not enabled")
            self._available = False
            return False
        
        try:
            # Try to create client - boto3 will use default credential chain
            self.client
            self._available = True
            logger.info("Comprehend enrichment service is available")
            return True
        except NoCredentialsError:
            logger.warning("AWS credentials not found (check 'aws configure' or env vars), Comprehend enrichment unavailable")
            self._available = False
            return False
        except Exception as e:
            logger.warning("Comprehend enrichment availability check failed", error=str(e))
            self._available = False
            return False
    
    def process_text(
        self,
        text: str,
        url: str = "",
        language_code: str = "en"
    ) -> ComprehendEnrichmentResult:
        """
        Process text with Comprehend enrichment features.
        
        Only enabled features (per config flags) will be processed.
        This is an ADDITIVE enrichment step that does not affect
        existing form matching or title extraction.
        
        Args:
            text: The text content to analyze
            url: Source URL (for logging)
            language_code: Language code (default: "en")
            
        Returns:
            ComprehendEnrichmentResult with extracted data
        """
        import time
        start_time = time.time()
        
        logger.info(
            "Starting Comprehend enrichment",
            url=url,
            text_length=len(text)
        )
        
        if not self.is_available():
            return ComprehendEnrichmentResult(
                success=False,
                error="Comprehend enrichment not available - check configuration and credentials"
            )
        
        if len(text) < self.MIN_TEXT_LENGTH:
            return ComprehendEnrichmentResult(
                success=False,
                error=f"Text too short for meaningful analysis (min {self.MIN_TEXT_LENGTH} chars)"
            )
        
        # Truncate text if needed (Comprehend has 100KB limit)
        original_length = len(text)
        if len(text.encode('utf-8')) > self.MAX_TEXT_BYTES:
            text = self._truncate_text(text)
            logger.warning(
                "Text truncated for Comprehend processing",
                original_length=original_length,
                truncated_length=len(text),
                url=url
            )
        
        result = ComprehendEnrichmentResult(success=True, text_length=len(text))
        
        try:
            # Track API calls
            from services.api_counter import api_counter
            
            # Classification (if enabled)
            if settings.COMPREHEND_CLASSIFICATION_ENABLED:
                self._run_classification(text, language_code, result, api_counter)
            
            # Named Entity Recognition (if enabled)
            if settings.COMPREHEND_NER_ENABLED:
                self._run_ner(text, language_code, result, api_counter, url)
            
            # Calculate processing time
            elapsed_ms = int((time.time() - start_time) * 1000)
            result.processing_time_ms = elapsed_ms
            
            logger.info(
                "Comprehend enrichment completed",
                url=url,
                features=result.features_used,
                document_type=result.document_type,
                entity_count=len(result.entities),
                elapsed_ms=elapsed_ms
            )
            
            return result
            
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_msg = e.response.get('Error', {}).get('Message', str(e))
            logger.error(
                "Comprehend API error during enrichment",
                error_code=error_code,
                error=error_msg,
                url=url
            )
            return ComprehendEnrichmentResult(
                success=False,
                error=f"Comprehend error: {error_code} - {error_msg}"
            )
        except Exception as e:
            logger.error("Comprehend enrichment failed", error=str(e), url=url)
            return ComprehendEnrichmentResult(
                success=False,
                error=str(e)
            )
    
    def _truncate_text(self, text: str) -> str:
        """Truncate text to fit within Comprehend's byte limit."""
        max_chars = self.MAX_TEXT_BYTES  # Approximation for English text
        
        # Try to truncate at a sentence boundary
        if len(text) > max_chars:
            truncated = text[:max_chars]
            # Find last sentence ending
            last_period = truncated.rfind('.')
            last_newline = truncated.rfind('\n')
            cutoff = max(last_period, last_newline)
            if cutoff > max_chars // 2:
                truncated = truncated[:cutoff + 1]
            return truncated
        return text
    
    def _run_classification(
        self,
        text: str,
        language_code: str,
        result: ComprehendEnrichmentResult,
        api_counter
    ) -> None:
        """
        Run document classification using Comprehend.
        
        If a custom classifier ARN is configured, uses that.
        Otherwise, uses a heuristic-based classification approach
        since Comprehend's built-in classification requires training.
        """
        result.features_used.append('CLASSIFICATION')
        
        # Check for custom classifier endpoint
        if settings.COMPREHEND_CLASSIFIER_ARN:
            api_counter.increment('comprehend_classify')
            try:
                response = self.client.classify_document(
                    Text=text,
                    EndpointArn=settings.COMPREHEND_CLASSIFIER_ARN
                )
                
                # Parse classification results
                classes = response.get('Classes', [])
                if classes:
                    top_class = max(classes, key=lambda x: x.get('Score', 0))
                    result.document_type = top_class.get('Name', 'other').lower()
                    result.document_type_confidence = top_class.get('Score', 0)
                    result.classification_all_classes = {
                        c.get('Name', ''): c.get('Score', 0) for c in classes
                    }
                return
            except ClientError as e:
                logger.warning(
                    "Custom classifier failed, falling back to heuristic",
                    error=str(e)
                )
        
        # Heuristic-based classification for court forms
        # This is a fallback when no custom classifier is trained
        result.document_type, result.document_type_confidence = self._heuristic_classify(text)
    
    def _heuristic_classify(self, text: str) -> tuple[str, float]:
        """
        Heuristic-based document classification for court forms.
        
        Uses keyword matching to classify documents when no
        custom Comprehend classifier is available.
        
        Returns:
            Tuple of (document_type, confidence)
        """
        text_lower = text.lower()
        
        # Define keyword patterns for each document type
        type_patterns = {
            'motion': [
                r'\bmotion\s+(to|for)\b',
                r'\bmoving\s+party\b',
                r'\bmotion\b.*\brule\b',
            ],
            'petition': [
                r'\bpetition\s+(for|to)\b',
                r'\bpetitioner\b',
                r'\bin\s+re\s+petition\b',
            ],
            'order': [
                r'\border\s+(granting|denying|to)\b',
                r'\bit\s+is\s+(so\s+)?ordered\b',
                r'\bcourt\s+orders\b',
            ],
            'summons': [
                r'\bsummons\b',
                r'\byou\s+are\s+(hereby\s+)?summoned\b',
                r'\bserve\s+a\s+copy\b',
            ],
            'complaint': [
                r'\bcomplaint\s+(for|against)\b',
                r'\bplaintiff\s+alleges\b',
                r'\bcause\s+of\s+action\b',
            ],
            'declaration': [
                r'\bdeclaration\s+(of|in)\b',
                r'\bi\s+declare\s+under\b',
                r'\bdeclare\s+under\s+penalty\b',
            ],
            'affidavit': [
                r'\baffidavit\s+(of|in)\b',
                r'\bsworn\s+statement\b',
                r'\bduly\s+sworn\b',
            ],
            'proof_of_service': [
                r'\bproof\s+of\s+service\b',
                r'\bi\s+served\b.*\bby\b',
                r'\bserved\s+the\s+following\b',
            ],
            'notice': [
                r'\bnotice\s+(of|to)\b',
                r'\bplease\s+take\s+notice\b',
                r'\bnotice\s+is\s+hereby\s+given\b',
            ],
            'cover_sheet': [
                r'\bcover\s+sheet\b',
                r'\bcivil\s+case\s+cover\b',
                r'\bcase\s+information\s+sheet\b',
            ],
            'stipulation': [
                r'\bstipulation\b',
                r'\bparties\s+stipulate\b',
                r'\bit\s+is\s+stipulated\b',
            ],
            'judgment': [
                r'\bjudgment\s+(for|against|is)\b',
                r'\benter\s+judgment\b',
                r'\bjudgment\s+be\s+entered\b',
            ],
            'subpoena': [
                r'\bsubpoena\b',
                r'\bsubpoena\s+duces\s+tecum\b',
                r'\bcommanded\s+to\s+appear\b',
            ],
            'application': [
                r'\bapplication\s+(for|to)\b',
                r'\bapplicant\s+requests\b',
                r'\bex\s+parte\s+application\b',
            ],
            'certificate': [
                r'\bcertificate\s+(of|that)\b',
                r'\bi\s+(hereby\s+)?certify\b',
                r'\bcertification\b',
            ],
        }
        
        # Score each type
        scores = {}
        for doc_type, patterns in type_patterns.items():
            score = 0
            for pattern in patterns:
                matches = len(re.findall(pattern, text_lower))
                score += matches
            scores[doc_type] = score
        
        # Find best match
        if scores:
            best_type = max(scores, key=scores.get)
            best_score = scores[best_type]
            
            if best_score > 0:
                # Convert score to pseudo-confidence (capped at 0.95)
                confidence = min(0.95, 0.5 + (best_score * 0.1))
                return best_type, confidence
        
        return 'other', 0.3
    
    def _run_ner(
        self,
        text: str,
        language_code: str,
        result: ComprehendEnrichmentResult,
        api_counter,
        url: str
    ) -> None:
        """Run Named Entity Recognition using Comprehend."""
        result.features_used.append('NER')
        api_counter.increment('comprehend_entities')
        
        # Check for custom entity recognizer
        endpoint_arn = settings.COMPREHEND_ENTITY_RECOGNIZER_ARN or None
        
        try:
            if endpoint_arn:
                # Use custom entity recognizer
                response = self.client.detect_entities(
                    Text=text,
                    LanguageCode=language_code,
                    EndpointArn=endpoint_arn
                )
            else:
                # Use built-in entity detection
                response = self.client.detect_entities(
                    Text=text,
                    LanguageCode=language_code
                )
            
            entities = response.get('Entities', [])
            
            # Parse entities
            for entity in entities:
                extracted = ExtractedEntity(
                    text=entity.get('Text', ''),
                    entity_type=entity.get('Type', 'OTHER'),
                    confidence=entity.get('Score', 0),
                    begin_offset=entity.get('BeginOffset', 0),
                    end_offset=entity.get('EndOffset', 0)
                )
                result.entities.append(extracted)
                
                # Group by type
                entity_type = extracted.entity_type
                if entity_type not in result.entities_by_type:
                    result.entities_by_type[entity_type] = []
                # Avoid duplicates
                if extracted.text not in result.entities_by_type[entity_type]:
                    result.entities_by_type[entity_type].append(extracted.text)
            
            # Build JSON output
            result.entities_json = {
                'total_count': len(result.entities),
                'by_type': result.entities_by_type,
                'entities': [
                    {
                        'text': e.text,
                        'type': e.entity_type,
                        'confidence': e.confidence
                    }
                    for e in result.entities
                ]
            }
            
        except ClientError as e:
            logger.warning(
                "Entity detection failed",
                error=str(e),
                url=url
            )
    
    def extract_dates(self, result: ComprehendEnrichmentResult) -> List[str]:
        """
        Extract date entities from enrichment results.
        
        This is an ADDITIONAL source for date extraction (REQ-006),
        not a replacement for existing date extraction logic.
        
        Args:
            result: ComprehendEnrichmentResult to search
            
        Returns:
            List of date strings found
        """
        dates = []
        for entity in result.entities:
            if entity.entity_type == 'DATE':
                dates.append(entity.text)
        return dates
    
    def extract_organizations(self, result: ComprehendEnrichmentResult) -> List[str]:
        """
        Extract organization entities from enrichment results.
        
        Useful for identifying courts, agencies, and parties.
        
        Args:
            result: ComprehendEnrichmentResult to search
            
        Returns:
            List of organization names found
        """
        return result.entities_by_type.get('ORGANIZATION', [])
    
    def extract_locations(self, result: ComprehendEnrichmentResult) -> List[str]:
        """
        Extract location entities from enrichment results.
        
        Useful for identifying jurisdictions.
        
        Args:
            result: ComprehendEnrichmentResult to search
            
        Returns:
            List of location names found
        """
        return result.entities_by_type.get('LOCATION', [])


# Singleton instance for convenience
_comprehend_enrichment_service: Optional[ComprehendEnrichmentService] = None


def get_comprehend_enrichment_service() -> ComprehendEnrichmentService:
    """Get or create the global ComprehendEnrichmentService instance."""
    global _comprehend_enrichment_service
    if _comprehend_enrichment_service is None:
        _comprehend_enrichment_service = ComprehendEnrichmentService()
    return _comprehend_enrichment_service
