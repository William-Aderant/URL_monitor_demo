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
    ForeignKey, JSON
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
    
    # Timestamps
    fetched_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    monitored_url = relationship("MonitoredURL", back_populates="versions")
    
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
    change_type = Column(String(50), nullable=False)  # new, modified, text_changed, binary_changed
    affected_pages = Column(JSON, nullable=True)  # List of page numbers that changed
    diff_summary = Column(Text, nullable=True)  # Human-readable summary of changes
    
    # Detection metadata
    pdf_hash_changed = Column(Boolean, default=False)
    text_hash_changed = Column(Boolean, default=False)
    
    # Timestamps
    detected_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    monitored_url = relationship("MonitoredURL", back_populates="changes")
    previous_version = relationship("PDFVersion", foreign_keys=[previous_version_id])
    new_version = relationship("PDFVersion", foreign_keys=[new_version_id])
    
    def __repr__(self) -> str:
        return f"<ChangeLog(id={self.id}, url_id={self.monitored_url_id}, type='{self.change_type}')>"

