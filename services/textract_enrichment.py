"""
Textract Enrichment Service

Provides advanced Textract features beyond basic OCR:
- AnalyzeDocument with FORMS (key-value pair extraction)
- AnalyzeDocument with TABLES (table structure extraction)
- Textract Queries API (targeted Q&A)
- Signature detection

This is an ADDITIVE feature that does not replace or modify
existing OCR fallback or title extraction functionality.
All features are opt-in via configuration flags.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List, Dict, Any
import boto3
from botocore.exceptions import ClientError, NoCredentialsError
import structlog

from config import settings

logger = structlog.get_logger()


@dataclass
class FormKeyValuePair:
    """A single key-value pair extracted from a form."""
    key: str
    value: str
    key_confidence: float
    value_confidence: float
    page: int = 1


@dataclass
class TableCell:
    """A single cell in an extracted table."""
    text: str
    row_index: int
    column_index: int
    confidence: float
    is_header: bool = False


@dataclass
class ExtractedTable:
    """An extracted table from a document."""
    page: int
    row_count: int
    column_count: int
    cells: List[TableCell] = field(default_factory=list)
    avg_confidence: float = 0.0


@dataclass
class QueryResult:
    """Result of a Textract Query."""
    query: str
    answer: str
    confidence: float
    page: int = 1


@dataclass 
class SignatureDetection:
    """A detected signature in the document."""
    page: int
    confidence: float
    bounding_box: Dict[str, float] = field(default_factory=dict)


@dataclass
class TextractEnrichmentResult:
    """Complete result of Textract enrichment processing."""
    success: bool
    error: Optional[str] = None
    
    # Form key-value pairs
    form_kv_pairs: List[FormKeyValuePair] = field(default_factory=list)
    form_kv_json: Dict[str, str] = field(default_factory=dict)
    form_avg_confidence: float = 0.0
    
    # Extracted tables
    tables: List[ExtractedTable] = field(default_factory=list)
    tables_json: List[Dict[str, Any]] = field(default_factory=list)
    
    # Query results
    queries_results: List[QueryResult] = field(default_factory=list)
    queries_json: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    
    # Signature detections
    signatures: List[SignatureDetection] = field(default_factory=list)
    signatures_json: List[Dict[str, Any]] = field(default_factory=list)
    
    # Processing metadata
    features_used: List[str] = field(default_factory=list)
    pages_processed: int = 0
    processing_time_ms: int = 0


class TextractEnrichmentService:
    """
    Advanced Textract enrichment service for IDP features.
    
    Provides form key-value extraction, table extraction, 
    targeted queries, and signature detection.
    
    All features are opt-in and controlled by configuration flags.
    This service does NOT modify or replace existing Textract usage
    in ocr_fallback.py or title_extractor.py.
    """
    
    def __init__(
        self,
        aws_access_key: Optional[str] = None,
        aws_secret_key: Optional[str] = None,
        aws_region: Optional[str] = None
    ):
        """
        Initialize Textract enrichment service.
        
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
        
        # Parse default queries from config
        self.default_queries = [
            q.strip() for q in settings.TEXTRACT_DEFAULT_QUERIES.split(';')
            if q.strip()
        ]
        
        logger.info(
            "TextractEnrichmentService initialized",
            region=self.aws_region,
            forms_enabled=settings.TEXTRACT_FORMS_ENABLED,
            tables_enabled=settings.TEXTRACT_TABLES_ENABLED,
            queries_enabled=settings.TEXTRACT_QUERIES_ENABLED,
            signatures_enabled=settings.TEXTRACT_SIGNATURES_ENABLED
        )
    
    @property
    def client(self):
        """Lazy-load Textract client using default credential chain."""
        if self._client is None:
            try:
                # Use explicit credentials if provided, otherwise boto3 uses default chain
                # (env vars -> ~/.aws/credentials -> IAM role)
                client_kwargs = {'region_name': self.aws_region}
                if self.aws_access_key and self.aws_secret_key:
                    client_kwargs['aws_access_key_id'] = self.aws_access_key
                    client_kwargs['aws_secret_access_key'] = self.aws_secret_key
                
                self._client = boto3.client('textract', **client_kwargs)
            except Exception as e:
                logger.error("Failed to create Textract client", error=str(e))
                raise
        return self._client
    
    def is_available(self) -> bool:
        """Check if Textract enrichment features are available."""
        if self._available is not None:
            return self._available
        
        # Check if any enrichment features are enabled
        any_enabled = any([
            settings.TEXTRACT_FORMS_ENABLED,
            settings.TEXTRACT_TABLES_ENABLED,
            settings.TEXTRACT_QUERIES_ENABLED,
            settings.TEXTRACT_SIGNATURES_ENABLED
        ])
        
        if not any_enabled:
            logger.debug("No Textract enrichment features enabled")
            self._available = False
            return False
        
        try:
            # Try to create client - boto3 will use default credential chain
            self.client
            self._available = True
            logger.info("Textract enrichment service is available")
            return True
        except NoCredentialsError:
            logger.warning("AWS credentials not found (check 'aws configure' or env vars), Textract enrichment unavailable")
            self._available = False
            return False
        except Exception as e:
            logger.warning("Textract enrichment availability check failed", error=str(e))
            self._available = False
            return False
    
    def process_document(
        self,
        pdf_path: Path,
        url: str = "",
        custom_queries: Optional[List[str]] = None
    ) -> TextractEnrichmentResult:
        """
        Process a PDF document with advanced Textract features.
        
        Only enabled features (per config flags) will be processed.
        This is an ADDITIVE enrichment step that does not affect
        existing text extraction or title extraction.
        
        Args:
            pdf_path: Path to the PDF file
            url: Source URL (for logging)
            custom_queries: Optional list of custom queries (adds to defaults)
            
        Returns:
            TextractEnrichmentResult with extracted data
        """
        import time
        start_time = time.time()
        
        logger.info(
            "Starting Textract enrichment",
            pdf_path=str(pdf_path),
            url=url
        )
        
        if not self.is_available():
            return TextractEnrichmentResult(
                success=False,
                error="Textract enrichment not available - check configuration and credentials"
            )
        
        if not pdf_path.exists():
            return TextractEnrichmentResult(
                success=False,
                error=f"File not found: {pdf_path}"
            )
        
        try:
            pdf_bytes = pdf_path.read_bytes()
            file_size = len(pdf_bytes)
            
            # Textract sync API has a 5MB limit
            if file_size > 5 * 1024 * 1024:
                logger.warning(
                    "PDF too large for sync Textract enrichment",
                    size_mb=file_size / (1024 * 1024),
                    url=url
                )
                return TextractEnrichmentResult(
                    success=False,
                    error="PDF too large for sync processing (>5MB). Consider async processing."
                )
            
            result = TextractEnrichmentResult(success=True)
            
            # Build feature types based on config
            feature_types = []
            if settings.TEXTRACT_FORMS_ENABLED:
                feature_types.append('FORMS')
                result.features_used.append('FORMS')
            if settings.TEXTRACT_TABLES_ENABLED:
                feature_types.append('TABLES')
                result.features_used.append('TABLES')
            if settings.TEXTRACT_SIGNATURES_ENABLED:
                feature_types.append('SIGNATURES')
                result.features_used.append('SIGNATURES')
            
            # Build queries if enabled
            queries_config = None
            if settings.TEXTRACT_QUERIES_ENABLED:
                all_queries = self.default_queries.copy()
                if custom_queries:
                    all_queries.extend(custom_queries)
                if all_queries:
                    queries_config = {
                        'Queries': [{'Text': q} for q in all_queries]
                    }
                    feature_types.append('QUERIES')
                    result.features_used.append('QUERIES')
            
            if not feature_types:
                return TextractEnrichmentResult(
                    success=False,
                    error="No Textract enrichment features enabled"
                )
            
            # Track API call
            from services.api_counter import api_counter
            api_counter.increment('textract_analyze')
            
            # Call AnalyzeDocument
            analyze_params = {
                'Document': {'Bytes': pdf_bytes},
                'FeatureTypes': feature_types
            }
            if queries_config:
                analyze_params['QueriesConfig'] = queries_config
            
            response = self.client.analyze_document(**analyze_params)
            
            # Parse the response
            self._parse_analyze_response(response, result)
            
            # Calculate processing time
            elapsed_ms = int((time.time() - start_time) * 1000)
            result.processing_time_ms = elapsed_ms
            
            logger.info(
                "Textract enrichment completed",
                url=url,
                features=result.features_used,
                kv_pairs=len(result.form_kv_pairs),
                tables=len(result.tables),
                queries=len(result.queries_results),
                signatures=len(result.signatures),
                elapsed_ms=elapsed_ms
            )
            
            return result
            
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_msg = e.response.get('Error', {}).get('Message', str(e))
            logger.error(
                "Textract API error during enrichment",
                error_code=error_code,
                error=error_msg,
                url=url
            )
            return TextractEnrichmentResult(
                success=False,
                error=f"Textract error: {error_code} - {error_msg}"
            )
        except Exception as e:
            logger.error("Textract enrichment failed", error=str(e), url=url)
            return TextractEnrichmentResult(
                success=False,
                error=str(e)
            )
    
    def _parse_analyze_response(
        self,
        response: dict,
        result: TextractEnrichmentResult
    ) -> None:
        """
        Parse AnalyzeDocument response and populate result.
        
        Args:
            response: Textract AnalyzeDocument response
            result: TextractEnrichmentResult to populate
        """
        blocks = response.get('Blocks', [])
        
        # Build block ID map for relationship lookups
        block_map = {block['Id']: block for block in blocks}
        
        # Track pages
        pages = set()
        
        for block in blocks:
            block_type = block.get('BlockType', '')
            page = block.get('Page', 1)
            pages.add(page)
            
            if block_type == 'KEY_VALUE_SET':
                self._parse_key_value_block(block, block_map, result)
            elif block_type == 'TABLE':
                self._parse_table_block(block, block_map, result)
            elif block_type == 'QUERY_RESULT':
                self._parse_query_result_block(block, block_map, result)
            elif block_type == 'SIGNATURE':
                self._parse_signature_block(block, result)
        
        result.pages_processed = len(pages)
        
        # Build JSON representations for storage
        self._build_json_outputs(result)
    
    def _parse_key_value_block(
        self,
        block: dict,
        block_map: dict,
        result: TextractEnrichmentResult
    ) -> None:
        """Parse a KEY_VALUE_SET block for form key-value pairs."""
        entity_types = block.get('EntityTypes', [])
        
        if 'KEY' not in entity_types:
            return
        
        # Get the key text
        key_text = self._get_text_from_block(block, block_map)
        key_confidence = block.get('Confidence', 0) / 100
        page = block.get('Page', 1)
        
        # Find the value through relationships
        value_text = ""
        value_confidence = 0.0
        
        relationships = block.get('Relationships', [])
        for rel in relationships:
            if rel.get('Type') == 'VALUE':
                for value_id in rel.get('Ids', []):
                    value_block = block_map.get(value_id)
                    if value_block:
                        value_text = self._get_text_from_block(value_block, block_map)
                        value_confidence = value_block.get('Confidence', 0) / 100
                        break
        
        if key_text.strip():
            kv_pair = FormKeyValuePair(
                key=key_text.strip(),
                value=value_text.strip(),
                key_confidence=key_confidence,
                value_confidence=value_confidence,
                page=page
            )
            result.form_kv_pairs.append(kv_pair)
    
    def _parse_table_block(
        self,
        block: dict,
        block_map: dict,
        result: TextractEnrichmentResult
    ) -> None:
        """Parse a TABLE block for table structure."""
        page = block.get('Page', 1)
        
        cells = []
        max_row = 0
        max_col = 0
        total_confidence = 0.0
        cell_count = 0
        
        relationships = block.get('Relationships', [])
        for rel in relationships:
            if rel.get('Type') == 'CHILD':
                for cell_id in rel.get('Ids', []):
                    cell_block = block_map.get(cell_id)
                    if cell_block and cell_block.get('BlockType') == 'CELL':
                        row_index = cell_block.get('RowIndex', 1) - 1
                        col_index = cell_block.get('ColumnIndex', 1) - 1
                        confidence = cell_block.get('Confidence', 0) / 100
                        is_header = cell_block.get('EntityTypes', []) == ['COLUMN_HEADER']
                        
                        cell_text = self._get_text_from_block(cell_block, block_map)
                        
                        cells.append(TableCell(
                            text=cell_text.strip(),
                            row_index=row_index,
                            column_index=col_index,
                            confidence=confidence,
                            is_header=is_header
                        ))
                        
                        max_row = max(max_row, row_index + 1)
                        max_col = max(max_col, col_index + 1)
                        total_confidence += confidence
                        cell_count += 1
        
        if cells:
            table = ExtractedTable(
                page=page,
                row_count=max_row,
                column_count=max_col,
                cells=cells,
                avg_confidence=total_confidence / cell_count if cell_count > 0 else 0
            )
            result.tables.append(table)
    
    def _parse_query_result_block(
        self,
        block: dict,
        block_map: dict,
        result: TextractEnrichmentResult
    ) -> None:
        """Parse a QUERY_RESULT block."""
        # Get the query through relationships
        query_text = ""
        relationships = block.get('Relationships', [])
        for rel in relationships:
            if rel.get('Type') == 'ANSWER':
                # The query is in a QUERY block that references this result
                pass
        
        # Get answer text directly from this block
        answer_text = self._get_text_from_block(block, block_map)
        confidence = block.get('Confidence', 0) / 100
        page = block.get('Page', 1)
        
        # Find the parent QUERY block that asked this question
        for other_block in block_map.values():
            if other_block.get('BlockType') == 'QUERY':
                for rel in other_block.get('Relationships', []):
                    if rel.get('Type') == 'ANSWER':
                        if block['Id'] in rel.get('Ids', []):
                            query_text = other_block.get('Query', {}).get('Text', '')
                            break
        
        if answer_text.strip():
            query_result = QueryResult(
                query=query_text,
                answer=answer_text.strip(),
                confidence=confidence,
                page=page
            )
            result.queries_results.append(query_result)
    
    def _parse_signature_block(
        self,
        block: dict,
        result: TextractEnrichmentResult
    ) -> None:
        """Parse a SIGNATURE block."""
        page = block.get('Page', 1)
        confidence = block.get('Confidence', 0) / 100
        
        geometry = block.get('Geometry', {})
        bbox = geometry.get('BoundingBox', {})
        
        signature = SignatureDetection(
            page=page,
            confidence=confidence,
            bounding_box={
                'left': bbox.get('Left', 0),
                'top': bbox.get('Top', 0),
                'width': bbox.get('Width', 0),
                'height': bbox.get('Height', 0)
            }
        )
        result.signatures.append(signature)
    
    def _get_text_from_block(self, block: dict, block_map: dict) -> str:
        """Extract text from a block, following CHILD relationships if needed."""
        # Direct text
        if 'Text' in block:
            return block['Text']
        
        # Follow child relationships for WORD/LINE blocks
        text_parts = []
        relationships = block.get('Relationships', [])
        for rel in relationships:
            if rel.get('Type') == 'CHILD':
                for child_id in rel.get('Ids', []):
                    child_block = block_map.get(child_id)
                    if child_block:
                        child_text = child_block.get('Text', '')
                        if child_text:
                            text_parts.append(child_text)
        
        return ' '.join(text_parts)
    
    def _build_json_outputs(self, result: TextractEnrichmentResult) -> None:
        """Build JSON representations for database storage."""
        # Form key-value pairs as simple dict
        result.form_kv_json = {}
        total_confidence = 0.0
        for kv in result.form_kv_pairs:
            result.form_kv_json[kv.key] = kv.value
            total_confidence += (kv.key_confidence + kv.value_confidence) / 2
        if result.form_kv_pairs:
            result.form_avg_confidence = total_confidence / len(result.form_kv_pairs)
        
        # Tables as list of dicts
        result.tables_json = []
        for table in result.tables:
            # Build 2D array for cells
            rows = [['' for _ in range(table.column_count)] for _ in range(table.row_count)]
            for cell in table.cells:
                if 0 <= cell.row_index < table.row_count and 0 <= cell.column_index < table.column_count:
                    rows[cell.row_index][cell.column_index] = cell.text
            
            result.tables_json.append({
                'page': table.page,
                'row_count': table.row_count,
                'column_count': table.column_count,
                'rows': rows,
                'avg_confidence': table.avg_confidence
            })
        
        # Query results as dict
        result.queries_json = {}
        for qr in result.queries_results:
            result.queries_json[qr.query] = {
                'answer': qr.answer,
                'confidence': qr.confidence,
                'page': qr.page
            }
        
        # Signatures as list of dicts
        result.signatures_json = []
        for sig in result.signatures:
            result.signatures_json.append({
                'page': sig.page,
                'confidence': sig.confidence,
                'bounding_box': sig.bounding_box
            })
    
    def extract_form_number(self, result: TextractEnrichmentResult) -> Optional[str]:
        """
        Try to extract form number from enrichment results.
        
        Searches key-value pairs and query results for form number.
        This is an ADDITIONAL source for form number, not a replacement
        for existing title_extractor.py logic.
        
        Args:
            result: TextractEnrichmentResult to search
            
        Returns:
            Form number if found, None otherwise
        """
        # Check key-value pairs
        form_number_keys = ['form number', 'form no', 'form #', 'form no.', 'form']
        for kv in result.form_kv_pairs:
            if kv.key.lower().strip() in form_number_keys:
                return kv.value.strip()
        
        # Check query results
        for qr in result.queries_results:
            if 'form number' in qr.query.lower():
                return qr.answer.strip()
        
        return None
    
    def extract_revision_date(self, result: TextractEnrichmentResult) -> Optional[str]:
        """
        Try to extract revision date from enrichment results.
        
        Searches key-value pairs and query results for revision date.
        This is an ADDITIONAL source for REQ-006, not a replacement
        for existing revision date extraction.
        
        Args:
            result: TextractEnrichmentResult to search
            
        Returns:
            Revision date string if found, None otherwise
        """
        # Check key-value pairs
        date_keys = ['revision date', 'rev date', 'revised', 'date revised', 
                     'effective date', 'eff date', 'date']
        for kv in result.form_kv_pairs:
            key_lower = kv.key.lower().strip()
            if any(dk in key_lower for dk in date_keys):
                return kv.value.strip()
        
        # Check query results
        for qr in result.queries_results:
            if 'revision date' in qr.query.lower() or 'date' in qr.query.lower():
                return qr.answer.strip()
        
        return None


# Singleton instance for convenience
_textract_enrichment_service: Optional[TextractEnrichmentService] = None


def get_textract_enrichment_service() -> TextractEnrichmentService:
    """Get or create the global TextractEnrichmentService instance."""
    global _textract_enrichment_service
    if _textract_enrichment_service is None:
        _textract_enrichment_service = TextractEnrichmentService()
    return _textract_enrichment_service
