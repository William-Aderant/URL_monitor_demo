"""
SQLAlchemy models for PDF Monitor.

Tables:
- MonitoredURL: Registry of URLs to monitor
- PDFVersion: Stored versions of PDFs with hashes
- ChangeLog: Record of detected changes
"""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Boolean, 
    ForeignKey, JSON, Float
)
from sqlalchemy.orm import relationship
from db.database import Base


class MonitoredURL(Base):
    """
    Registry of URLs being monitored for changes.
    """
    __tablename__ = "monitored_urls"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    url = Column(String(2048), nullable=False, unique=True)
    description = Column(Text, nullable=True)
    check_interval_hours = Column(Integer, default=24)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_checked_at = Column(DateTime, nullable=True)
    last_change_at = Column(DateTime, nullable=True)
    
    # Parent page for crawling relocated forms
    parent_page_url = Column(String(2048), nullable=True)
    
    # Fast change detection metadata (Tier 1: HTTP headers)
    last_modified_header = Column(DateTime, nullable=True)  # Last-Modified header from server
    etag_header = Column(String(255), nullable=True)  # ETag header from server
    content_length_header = Column(Integer, nullable=True)  # Content-Length header from server
    
    # Fast change detection metadata (Tier 2: Quick hash)
    quick_hash = Column(String(64), nullable=True)  # SHA-256 hash of first 64KB of PDF
    
    # State and domain organization
    state = Column(String(50), nullable=True)  # e.g., "Alaska", "California"
    domain_category = Column(String(100), nullable=True)  # e.g., "courts.ca.gov", "insurance.ca.gov"
    
    # Relationships
    versions = relationship("PDFVersion", back_populates="monitored_url", cascade="all, delete-orphan")
    changes = relationship("ChangeLog", back_populates="monitored_url", cascade="all, delete-orphan")
    
    def __repr__(self) -> str:
        return f"<MonitoredURL(id={self.id}, name='{self.name}', url='{self.url[:50]}...')>"


class PDFVersion(Base):
    """
    Stored version of a PDF with associated metadata and hashes.
    Each version represents a snapshot of the PDF at a point in time.
    """
    __tablename__ = "pdf_versions"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    monitored_url_id = Column(Integer, ForeignKey("monitored_urls.id"), nullable=False)
    version_number = Column(Integer, nullable=False)
    
    # File paths (relative to storage root)
    original_pdf_path = Column(String(512), nullable=False)
    normalized_pdf_path = Column(String(512), nullable=False)
    extracted_text_path = Column(String(512), nullable=False)
    
    # Hashes for change detection
    pdf_hash = Column(String(64), nullable=False)  # SHA-256 of normalized PDF
    text_hash = Column(String(64), nullable=False)  # SHA-256 of extracted text
    page_hashes = Column(JSON, nullable=True)  # List of per-page text hashes
    
    # Extraction metadata
    extraction_method = Column(String(50), nullable=False)  # pdfplumber, pdfminer, textract
    page_count = Column(Integer, nullable=True)
    text_length = Column(Integer, nullable=True)
    ocr_used = Column(Boolean, default=False)
    
    # Title extraction fields
    formatted_title = Column(String(512), nullable=True)
    form_number = Column(String(100), nullable=True)
    title_confidence = Column(Float, nullable=True)
    title_extraction_method = Column(String(50), nullable=True)  # textract+bedrock, manual
    
    # Revision date extracted from PDF (REQ-006)
    revision_date = Column(String(50), nullable=True)  # Extracted revision date string
    
    # AWS Kendra indexing tracking (optional)
    kendra_document_id = Column(String(255), nullable=True)  # Kendra document ID
    kendra_indexed_at = Column(DateTime, nullable=True)  # Timestamp of indexing
    kendra_index_status = Column(String(50), nullable=True)  # pending, indexed, failed
    
    # ==========================================================================
    # AWS IDP Enrichment Fields (Additive - all optional)
    # Comprehend, Textract Forms/Queries, A2I enrichment data
    # ==========================================================================
    
    # Comprehend Classification (document type)
    comprehend_document_type = Column(String(100), nullable=True)  # e.g., motion, petition, order
    comprehend_document_type_confidence = Column(Float, nullable=True)  # Confidence score 0.0-1.0
    
    # Comprehend NER (extracted entities as JSON)
    comprehend_entities = Column(JSON, nullable=True)  # {"dates": [...], "organizations": [...], etc.}
    
    # Textract Forms (key-value pairs)
    textract_form_kv_pairs = Column(JSON, nullable=True)  # {"Form Number": "CIV-001", "Revision Date": "01/2024", ...}
    textract_form_confidence = Column(Float, nullable=True)  # Average confidence for form extraction
    
    # Textract Tables (extracted table structure)
    textract_tables = Column(JSON, nullable=True)  # [{"page": 1, "rows": [...], "columns": [...]}, ...]
    
    # Textract Queries (targeted Q&A results)
    textract_queries_results = Column(JSON, nullable=True)  # {"What is the form number?": {"answer": "CIV-001", "confidence": 0.95}, ...}
    
    # Textract Signature Detection
    textract_signatures = Column(JSON, nullable=True)  # [{"page": 1, "bbox": {...}, "confidence": 0.92}, ...]
    
    # IDP Enrichment Status
    idp_enrichment_status = Column(String(50), nullable=True)  # pending, processing, completed, failed, skipped
    idp_enrichment_at = Column(DateTime, nullable=True)  # When enrichment was processed
    idp_enrichment_error = Column(Text, nullable=True)  # Error message if failed
    
    # A2I Human Review (if sent for human review)
    a2i_human_loop_arn = Column(String(512), nullable=True)  # ARN of A2I human loop if created
    a2i_human_loop_status = Column(String(50), nullable=True)  # pending, in_progress, completed, stopped
    a2i_human_loop_output = Column(JSON, nullable=True)  # Human review output data
    
    # Timestamps
    fetched_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    monitored_url = relationship("MonitoredURL", back_populates="versions")
    
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
    
    def __repr__(self) -> str:
        return f"<PDFVersion(id={self.id}, url_id={self.monitored_url_id}, v={self.version_number})>"


class ChangeLog(Base):
    """
    Record of detected changes between PDF versions.
    """
    __tablename__ = "change_logs"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    monitored_url_id = Column(Integer, ForeignKey("monitored_urls.id"), nullable=False)
    
    # Version references
    previous_version_id = Column(Integer, ForeignKey("pdf_versions.id"), nullable=True)
    new_version_id = Column(Integer, ForeignKey("pdf_versions.id"), nullable=False)
    
    # Change details
    change_type = Column(String(50), nullable=False)  # new, modified, text_changed, format_only, relocated
    affected_pages = Column(JSON, nullable=True)  # List of page numbers that changed
    diff_summary = Column(Text, nullable=True)  # Human-readable summary of changes
    
    # Detection metadata
    pdf_hash_changed = Column(Boolean, default=False)
    text_hash_changed = Column(Boolean, default=False)
    
    # Enhanced change detection fields
    match_type = Column(String(50), nullable=True)  # form_number_match, similarity_match, new_form, uncertain
    similarity_score = Column(Float, nullable=True)  # 0.0 to 1.0 text similarity
    relocated_from_url = Column(String(2048), nullable=True)  # Original URL if form moved
    diff_image_path = Column(String(512), nullable=True)  # Path to visual diff image
    
    # AI Action Recommendation (REQ-001, REQ-003)
    recommended_action = Column(String(50), nullable=True)  # auto_approve, review_suggested, manual_required, false_positive, new_form
    action_confidence = Column(Float, nullable=True)  # Confidence in the recommendation (0.0 to 1.0)
    action_rationale = Column(Text, nullable=True)  # Human-readable explanation for the recommendation
    
    # Review/approval workflow (REQ-013, REQ-014)
    review_status = Column(String(50), default="pending")  # pending, approved, rejected, deferred, auto_approved
    reviewed = Column(Boolean, default=False)  # Has this change been reviewed/approved?
    reviewed_at = Column(DateTime, nullable=True)  # When was it reviewed?
    reviewed_by = Column(String(255), nullable=True)  # Who reviewed it (username or system)
    review_notes = Column(Text, nullable=True)  # Optional notes from reviewer
    
    # Classification override (REQ-004 - supports override)
    classification_override = Column(String(50), nullable=True)  # Human override of AI classification
    override_reason = Column(Text, nullable=True)  # Reason for override
    
    # Timestamps
    detected_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    monitored_url = relationship("MonitoredURL", back_populates="changes")
    previous_version = relationship("PDFVersion", foreign_keys=[previous_version_id])
    new_version = relationship("PDFVersion", foreign_keys=[new_version_id])
    
    def __repr__(self) -> str:
        return f"<ChangeLog(id={self.id}, url_id={self.monitored_url_id}, type='{self.change_type}')>"


