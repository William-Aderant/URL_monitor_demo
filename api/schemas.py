"""
Pydantic schemas for API request/response models.
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class MonitoredURLBase(BaseModel):
    """Base schema for MonitoredURL."""
    name: str
    url: str
    description: Optional[str] = None
    check_interval_hours: int = 24
    enabled: bool = True


class MonitoredURLCreate(MonitoredURLBase):
    """Schema for creating a MonitoredURL."""
    pass


class MonitoredURLResponse(MonitoredURLBase):
    """Schema for MonitoredURL response."""
    id: int
    created_at: datetime
    updated_at: datetime
    last_checked_at: Optional[datetime] = None
    last_change_at: Optional[datetime] = None
    version_count: int = 0
    
    class Config:
        from_attributes = True


class PDFVersionResponse(BaseModel):
    """Schema for PDFVersion response."""
    id: int
    monitored_url_id: int
    version_number: int
    pdf_hash: str
    text_hash: str
    extraction_method: str
    page_count: Optional[int] = None
    text_length: Optional[int] = None
    ocr_used: bool = False
    fetched_at: datetime
    created_at: datetime
    
    class Config:
        from_attributes = True


class ChangeLogResponse(BaseModel):
    """Schema for ChangeLog response."""
    id: int
    monitored_url_id: int
    previous_version_id: Optional[int] = None
    new_version_id: int
    change_type: str
    affected_pages: Optional[list[int]] = None
    diff_summary: Optional[str] = None
    pdf_hash_changed: bool = False
    text_hash_changed: bool = False
    detected_at: datetime
    
    # Enhanced change detection fields
    match_type: Optional[str] = None
    similarity_score: Optional[float] = None
    relocated_from_url: Optional[str] = None
    
    # AI Action Recommendation
    recommended_action: Optional[str] = None
    action_confidence: Optional[float] = None
    action_rationale: Optional[str] = None
    
    # Review workflow
    review_status: str = "pending"
    reviewed: bool = False
    reviewed_at: Optional[datetime] = None
    reviewed_by: Optional[str] = None
    review_notes: Optional[str] = None
    
    # Classification override
    classification_override: Optional[str] = None
    override_reason: Optional[str] = None
    
    # Include related data
    url_name: Optional[str] = None
    
    class Config:
        from_attributes = True


class ReviewRequest(BaseModel):
    """Schema for submitting a review action."""
    action: str  # approved, rejected, deferred
    notes: Optional[str] = None
    reviewed_by: Optional[str] = None


class ReviewResponse(BaseModel):
    """Schema for review action response."""
    success: bool
    change_id: int
    review_status: str
    reviewed_at: Optional[datetime] = None
    message: Optional[str] = None


class ClassificationOverrideRequest(BaseModel):
    """Schema for overriding AI classification."""
    classification: str  # new_form, updated_same_name, updated_name_change, false_positive
    reason: str
    overridden_by: Optional[str] = None


class BulkReviewRequest(BaseModel):
    """Schema for bulk review actions."""
    change_ids: list[int]
    action: str  # approved, rejected, deferred
    notes: Optional[str] = None
    reviewed_by: Optional[str] = None


class BulkReviewResponse(BaseModel):
    """Schema for bulk review response."""
    success: bool
    processed: int
    failed: int
    details: list[dict]


class MonitoringRunRequest(BaseModel):
    """Schema for triggering a monitoring run."""
    url_id: Optional[int] = None


class MonitoringRunResponse(BaseModel):
    """Schema for monitoring run result."""
    processed: int
    success: int
    failed: int
    details: list[dict]


class StatusResponse(BaseModel):
    """Schema for system status."""
    total_urls: int
    enabled_urls: int
    total_versions: int
    total_changes: int
    storage_size_bytes: int


