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
    
    # Include related data
    url_name: Optional[str] = None
    
    class Config:
        from_attributes = True


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


