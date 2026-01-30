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


# ============================================================================
# Schedule Configuration Schemas
# ============================================================================

class ScheduleConfigUpdate(BaseModel):
    """Schema for updating schedule configuration."""
    enabled: Optional[bool] = None
    schedule_type: Optional[str] = None  # daily, weekly, custom
    daily_time: Optional[str] = None  # HH:MM format
    weekly_days: Optional[list[str]] = None  # ["monday", "wednesday", "friday"]
    weekly_time: Optional[str] = None  # HH:MM format
    cron_expression: Optional[str] = None
    timezone: Optional[str] = None


class ScheduleConfigResponse(BaseModel):
    """Schema for schedule configuration response."""
    id: int
    enabled: bool
    schedule_type: str
    daily_time: Optional[str] = None
    weekly_days: Optional[list[str]] = None
    weekly_time: Optional[str] = None
    cron_expression: Optional[str] = None
    timezone: str
    last_run_at: Optional[datetime] = None
    next_run_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True


class SchedulerStatusResponse(BaseModel):
    """Schema for scheduler status response."""
    scheduler_enabled: bool
    scheduler_running: bool
    config: Optional[dict] = None
    next_run: Optional[str] = None
    last_run: Optional[str] = None


# ============================================================================
# URL Management Schemas
# ============================================================================

class MonitoredURLUpdate(BaseModel):
    """Schema for updating a monitored URL."""
    name: Optional[str] = None
    url: Optional[str] = None
    description: Optional[str] = None
    state: Optional[str] = None
    domain_category: Optional[str] = None
    enabled: Optional[bool] = None
    check_interval_hours: Optional[int] = None


class MonitoredURLFullResponse(BaseModel):
    """Full schema for MonitoredURL response with all fields."""
    id: int
    name: str
    url: str
    description: Optional[str] = None
    check_interval_hours: int
    enabled: bool
    state: Optional[str] = None
    domain_category: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    last_checked_at: Optional[datetime] = None
    last_change_at: Optional[datetime] = None
    version_count: int = 0
    pending_changes: int = 0
    import_batch_id: Optional[str] = None
    import_source: Optional[str] = None
    
    class Config:
        from_attributes = True


class BulkDeleteRequest(BaseModel):
    """Schema for bulk delete request."""
    url_ids: list[int]


class BulkDeleteResponse(BaseModel):
    """Schema for bulk delete response."""
    success: bool
    deleted: int
    failed: int
    details: list[dict]


# ============================================================================
# Bulk Upload Schemas
# ============================================================================

class BulkUploadResponse(BaseModel):
    """Schema for bulk upload response."""
    success: bool
    batch_id: str
    total_rows: int = 0
    successful: int = 0
    failed: int = 0
    duplicates: int = 0
    results: list[dict]
    error: Optional[str] = None


# ============================================================================
# Change Download and Approval Schemas
# ============================================================================

class ChangeApprovalRequest(BaseModel):
    """Schema for approving a change."""
    notes: Optional[str] = None
    reviewed_by: Optional[str] = None


class ChangeApprovalResponse(BaseModel):
    """Schema for change approval response."""
    success: bool
    change_id: int
    review_status: str
    reviewed_at: Optional[datetime] = None
    download_required: bool = False
    message: Optional[str] = None


class ManualInterventionRequest(BaseModel):
    """Schema for recording manual intervention."""
    intervention_type: str  # title_edit, url_edit, manual_detection
    notes: Optional[str] = None


class ChangeFullResponse(BaseModel):
    """Full schema for change response with download info."""
    id: int
    monitored_url_id: int
    url_name: Optional[str] = None
    url_url: Optional[str] = None
    previous_version_id: Optional[int] = None
    new_version_id: int
    change_type: str
    affected_pages: Optional[list[int]] = None
    diff_summary: Optional[str] = None
    similarity_score: Optional[float] = None
    match_type: Optional[str] = None
    recommended_action: Optional[str] = None
    action_confidence: Optional[float] = None
    action_rationale: Optional[str] = None
    review_status: str = "pending"
    reviewed: bool = False
    reviewed_at: Optional[datetime] = None
    detected_at: datetime
    
    # Download tracking
    download_count: int = 0
    first_downloaded_at: Optional[datetime] = None
    last_downloaded_at: Optional[datetime] = None
    downloaded_filename: Optional[str] = None
    
    # Manual intervention
    manual_intervention_required: bool = False
    intervention_type: Optional[str] = None
    
    # Version info for download
    formatted_title: Optional[str] = None
    form_number: Optional[str] = None
    
    class Config:
        from_attributes = True


# ============================================================================
# Monitoring Cycle Audit Schemas
# ============================================================================

class MonitoringCycleResponse(BaseModel):
    """Schema for monitoring cycle response."""
    id: int
    started_at: datetime
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    status: str
    total_urls_checked: int = 0
    successful_checks: int = 0
    failed_checks: int = 0
    changes_detected: int = 0
    skipped_unchanged: int = 0
    downloads_automated: int = 0
    manual_interventions: int = 0
    triggered_by: str
    error_count: int = 0
    
    class Config:
        from_attributes = True


class CycleURLResultResponse(BaseModel):
    """Schema for cycle URL result response."""
    id: int
    cycle_id: int
    monitored_url_id: int
    url_name: Optional[str] = None
    status: str
    error_message: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    tier_reached: Optional[int] = None
    change_detected: bool = False
    change_log_id: Optional[int] = None
    
    class Config:
        from_attributes = True


class AuditStatsResponse(BaseModel):
    """Schema for audit statistics response."""
    total_cycles: int
    total_changes_detected: int
    total_downloads_automated: int
    total_manual_interventions: int
    total_urls_checked: int
    total_successful_checks: int
    total_failed_checks: int
    average_cycle_duration: Optional[float] = None
    automation_rate: float = 0.0
    success_rate: float = 0.0
    
    # Breakdown by trigger type
    scheduled_cycles: int = 0
    manual_cycles: int = 0
    api_cycles: int = 0


class AuditTrendsResponse(BaseModel):
    """Schema for audit trends response."""
    period: str  # daily, weekly, monthly
    data: list[dict]  # List of {date, cycles, changes, automated, manual}


