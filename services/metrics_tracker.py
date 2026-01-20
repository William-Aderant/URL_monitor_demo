"""
Metrics Tracker Service

Provides automated KPI tracking and reporting as specified in Section 5 of the PoC.
Replaces manual spreadsheet tracking with programmatic metrics calculation.

Key Metrics Tracked:
- Forms processed per month
- AI automation rate (auto-approved / total)
- Human review rate (manual_required / total)
- First-pass acceptance rate
- Processing time averages
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_
import structlog

from db.models import MonitoredURL, PDFVersion, ChangeLog

logger = structlog.get_logger()


@dataclass
class MonthlyStats:
    """Monthly statistics for forms workflow."""
    year: int
    month: int
    
    # Volume metrics
    forms_monitored: int = 0
    updates_detected: int = 0
    new_forms_added: int = 0
    
    # Review metrics
    auto_approved: int = 0
    manual_reviewed: int = 0
    rejected: int = 0
    pending: int = 0
    
    # Derived rates
    @property
    def automation_rate(self) -> float:
        """Percentage of changes auto-approved."""
        total = self.auto_approved + self.manual_reviewed + self.rejected
        return self.auto_approved / total if total > 0 else 0.0
    
    @property
    def review_rate(self) -> float:
        """Percentage of changes requiring human review."""
        total = self.auto_approved + self.manual_reviewed + self.rejected
        return self.manual_reviewed / total if total > 0 else 0.0
    
    @property
    def rejection_rate(self) -> float:
        """Percentage of changes rejected."""
        total = self.auto_approved + self.manual_reviewed + self.rejected
        return self.rejected / total if total > 0 else 0.0
    
    def to_dict(self) -> dict:
        return {
            "year": self.year,
            "month": self.month,
            "period": f"{self.year}-{self.month:02d}",
            "forms_monitored": self.forms_monitored,
            "updates_detected": self.updates_detected,
            "new_forms_added": self.new_forms_added,
            "auto_approved": self.auto_approved,
            "manual_reviewed": self.manual_reviewed,
            "rejected": self.rejected,
            "pending": self.pending,
            "automation_rate": self.automation_rate,
            "review_rate": self.review_rate,
            "rejection_rate": self.rejection_rate,
        }


@dataclass
class AccuracyReport:
    """AI accuracy metrics comparing predictions vs human decisions."""
    total_predictions: int = 0
    correct_predictions: int = 0
    overridden_predictions: int = 0
    
    # Per-action breakdown
    auto_approve_accuracy: float = 0.0
    review_suggested_accuracy: float = 0.0
    manual_required_accuracy: float = 0.0
    
    @property
    def overall_accuracy(self) -> float:
        """Overall prediction accuracy."""
        if self.total_predictions == 0:
            return 0.0
        return self.correct_predictions / self.total_predictions
    
    @property
    def override_rate(self) -> float:
        """Rate at which human overrides AI classification."""
        if self.total_predictions == 0:
            return 0.0
        return self.overridden_predictions / self.total_predictions
    
    def to_dict(self) -> dict:
        return {
            "total_predictions": self.total_predictions,
            "correct_predictions": self.correct_predictions,
            "overridden_predictions": self.overridden_predictions,
            "overall_accuracy": self.overall_accuracy,
            "override_rate": self.override_rate,
            "auto_approve_accuracy": self.auto_approve_accuracy,
            "review_suggested_accuracy": self.review_suggested_accuracy,
            "manual_required_accuracy": self.manual_required_accuracy,
        }


@dataclass
class ProcessingMetrics:
    """Processing time and efficiency metrics."""
    avg_processing_time_seconds: float = 0.0
    total_forms_processed: int = 0
    forms_per_hour: float = 0.0
    
    # Time breakdown
    avg_fetch_time: float = 0.0
    avg_analysis_time: float = 0.0
    avg_review_time: float = 0.0
    
    def to_dict(self) -> dict:
        return {
            "avg_processing_time_seconds": self.avg_processing_time_seconds,
            "total_forms_processed": self.total_forms_processed,
            "forms_per_hour": self.forms_per_hour,
            "avg_fetch_time": self.avg_fetch_time,
            "avg_analysis_time": self.avg_analysis_time,
            "avg_review_time": self.avg_review_time,
        }


@dataclass
class DashboardMetrics:
    """Complete metrics for dashboard display."""
    # Current status
    total_monitored_urls: int = 0
    enabled_urls: int = 0
    total_versions: int = 0
    total_changes: int = 0
    
    # Queue status
    pending_review: int = 0
    auto_approvable: int = 0
    requires_manual: int = 0
    
    # Rates (current)
    current_automation_rate: float = 0.0
    current_review_rate: float = 0.0
    
    # Targets from PoC
    target_automation_rate: float = 0.95  # 95% for full deployment
    target_review_rate: float = 0.10  # 10% or less
    
    # Monthly trend (last 6 months)
    monthly_trend: List[MonthlyStats] = field(default_factory=list)
    
    def to_dict(self) -> dict:
        return {
            "total_monitored_urls": self.total_monitored_urls,
            "enabled_urls": self.enabled_urls,
            "total_versions": self.total_versions,
            "total_changes": self.total_changes,
            "pending_review": self.pending_review,
            "auto_approvable": self.auto_approvable,
            "requires_manual": self.requires_manual,
            "current_automation_rate": self.current_automation_rate,
            "current_review_rate": self.current_review_rate,
            "target_automation_rate": self.target_automation_rate,
            "target_review_rate": self.target_review_rate,
            "automation_gap": self.target_automation_rate - self.current_automation_rate,
            "review_gap": self.current_review_rate - self.target_review_rate,
            "monthly_trend": [m.to_dict() for m in self.monthly_trend],
        }


class MetricsTracker:
    """
    Tracks and reports workflow metrics.
    
    Replaces manual spreadsheet tracking (Forms Monitoring Spreadsheet)
    with automated, real-time metrics calculation.
    """
    
    def __init__(self):
        logger.info("MetricsTracker initialized")
    
    def get_monthly_stats(self, db: Session, year: int, month: int) -> MonthlyStats:
        """
        Get statistics for a specific month.
        
        Args:
            db: Database session
            year: Year (e.g., 2025)
            month: Month (1-12)
            
        Returns:
            MonthlyStats with all metrics for that month
        """
        # Calculate date range for the month
        start_date = datetime(year, month, 1)
        if month == 12:
            end_date = datetime(year + 1, 1, 1)
        else:
            end_date = datetime(year, month + 1, 1)
        
        stats = MonthlyStats(year=year, month=month)
        
        # Count forms monitored (enabled URLs)
        stats.forms_monitored = db.query(MonitoredURL).filter(
            MonitoredURL.enabled == True
        ).count()
        
        # Count changes detected in this month
        month_changes = db.query(ChangeLog).filter(
            ChangeLog.detected_at >= start_date,
            ChangeLog.detected_at < end_date
        )
        
        stats.updates_detected = month_changes.filter(
            ChangeLog.change_type.in_(["text_changed", "format_only", "relocated"])
        ).count()
        
        stats.new_forms_added = month_changes.filter(
            ChangeLog.change_type == "new"
        ).count()
        
        # Review status counts
        stats.auto_approved = month_changes.filter(
            ChangeLog.review_status.in_(["auto_approved"])
        ).count()
        
        stats.manual_reviewed = month_changes.filter(
            ChangeLog.review_status == "approved",
            ChangeLog.reviewed_by != "auto_approve_system"
        ).count()
        
        stats.rejected = month_changes.filter(
            ChangeLog.review_status == "rejected"
        ).count()
        
        stats.pending = month_changes.filter(
            ChangeLog.review_status == "pending"
        ).count()
        
        logger.info(
            "Monthly stats calculated",
            year=year,
            month=month,
            updates=stats.updates_detected,
            automation_rate=f"{stats.automation_rate:.1%}"
        )
        
        return stats
    
    def get_ai_accuracy(self, db: Session, days: int = 30) -> AccuracyReport:
        """
        Calculate AI prediction accuracy based on human review outcomes.
        
        Compares AI recommended_action with actual review outcomes to
        determine how well the AI is predicting human decisions.
        
        Args:
            db: Database session
            days: Number of days to look back
            
        Returns:
            AccuracyReport with accuracy metrics
        """
        report = AccuracyReport()
        
        cutoff = datetime.utcnow() - timedelta(days=days)
        
        # Get all reviewed changes with recommendations
        reviewed = db.query(ChangeLog).filter(
            ChangeLog.detected_at >= cutoff,
            ChangeLog.reviewed == True,
            ChangeLog.recommended_action.isnot(None)
        ).all()
        
        report.total_predictions = len(reviewed)
        
        if report.total_predictions == 0:
            return report
        
        correct = 0
        overridden = 0
        
        # Track per-action accuracy
        action_correct = {"auto_approve": 0, "review_suggested": 0, "manual_required": 0}
        action_total = {"auto_approve": 0, "review_suggested": 0, "manual_required": 0}
        
        for change in reviewed:
            action = change.recommended_action
            status = change.review_status
            
            # Count totals per action
            if action in action_total:
                action_total[action] += 1
            
            # Check if AI was correct
            if action == "auto_approve" and status in ["approved", "auto_approved"]:
                correct += 1
                action_correct["auto_approve"] += 1
            elif action == "review_suggested" and status == "approved":
                correct += 1
                action_correct["review_suggested"] += 1
            elif action == "manual_required" and status in ["approved", "rejected"]:
                correct += 1
                action_correct["manual_required"] += 1
            elif action == "false_positive" and status in ["approved", "auto_approved"]:
                correct += 1
            elif action == "new_form" and status == "approved":
                correct += 1
            
            # Check for overrides
            if change.classification_override:
                overridden += 1
        
        report.correct_predictions = correct
        report.overridden_predictions = overridden
        
        # Calculate per-action accuracy
        if action_total["auto_approve"] > 0:
            report.auto_approve_accuracy = action_correct["auto_approve"] / action_total["auto_approve"]
        if action_total["review_suggested"] > 0:
            report.review_suggested_accuracy = action_correct["review_suggested"] / action_total["review_suggested"]
        if action_total["manual_required"] > 0:
            report.manual_required_accuracy = action_correct["manual_required"] / action_total["manual_required"]
        
        logger.info(
            "AI accuracy calculated",
            total=report.total_predictions,
            accuracy=f"{report.overall_accuracy:.1%}",
            override_rate=f"{report.override_rate:.1%}"
        )
        
        return report
    
    def get_dashboard_metrics(self, db: Session) -> DashboardMetrics:
        """
        Get all metrics for dashboard display.
        
        Args:
            db: Database session
            
        Returns:
            DashboardMetrics with current status and trends
        """
        metrics = DashboardMetrics()
        
        # Basic counts
        metrics.total_monitored_urls = db.query(MonitoredURL).count()
        metrics.enabled_urls = db.query(MonitoredURL).filter(
            MonitoredURL.enabled == True
        ).count()
        metrics.total_versions = db.query(PDFVersion).count()
        metrics.total_changes = db.query(ChangeLog).count()
        
        # Queue status
        metrics.pending_review = db.query(ChangeLog).filter(
            ChangeLog.review_status == "pending"
        ).count()
        
        metrics.auto_approvable = db.query(ChangeLog).filter(
            ChangeLog.review_status == "pending",
            ChangeLog.recommended_action == "auto_approve"
        ).count()
        
        metrics.requires_manual = db.query(ChangeLog).filter(
            ChangeLog.review_status == "pending",
            ChangeLog.recommended_action.in_(["manual_required", "new_form"])
        ).count()
        
        # Calculate current rates
        total_processed = db.query(ChangeLog).filter(
            ChangeLog.reviewed == True
        ).count()
        
        auto_approved = db.query(ChangeLog).filter(
            ChangeLog.review_status.in_(["auto_approved"]),
            ChangeLog.recommended_action.in_(["auto_approve", "false_positive"])
        ).count()
        
        manual_reviewed = db.query(ChangeLog).filter(
            ChangeLog.review_status.in_(["approved", "rejected"]),
            ChangeLog.reviewed_by != "auto_approve_system"
        ).count()
        
        if total_processed > 0:
            metrics.current_automation_rate = auto_approved / total_processed
            metrics.current_review_rate = manual_reviewed / total_processed
        
        # Get monthly trend (last 6 months) - optimized batch calculation
        now = datetime.utcnow()
        month_ranges = []
        for i in range(5, -1, -1):
            month = now.month - i
            year = now.year
            if month <= 0:
                month += 12
                year -= 1
            
            start_date = datetime(year, month, 1)
            if month == 12:
                end_date = datetime(year + 1, 1, 1)
            else:
                end_date = datetime(year, month + 1, 1)
            
            month_ranges.append((year, month, start_date, end_date))
        
        # Count forms monitored once (same for all months)
        forms_monitored = db.query(MonitoredURL).filter(
            MonitoredURL.enabled == True
        ).count()
        
        # Process each month with optimized queries
        for year, month, start_date, end_date in month_ranges:
            stats = MonthlyStats(year=year, month=month)
            stats.forms_monitored = forms_monitored
            
            # Use single query with conditional aggregation for efficiency
            month_changes_query = db.query(ChangeLog).filter(
                ChangeLog.detected_at >= start_date,
                ChangeLog.detected_at < end_date
            )
            
            stats.updates_detected = month_changes_query.filter(
                ChangeLog.change_type.in_(["text_changed", "format_only", "relocated"])
            ).count()
            
            stats.new_forms_added = month_changes_query.filter(
                ChangeLog.change_type == "new"
            ).count()
            
            stats.auto_approved = month_changes_query.filter(
                ChangeLog.review_status.in_(["auto_approved"])
            ).count()
            
            stats.manual_reviewed = month_changes_query.filter(
                ChangeLog.review_status == "approved",
                ChangeLog.reviewed_by != "auto_approve_system"
            ).count()
            
            stats.rejected = month_changes_query.filter(
                ChangeLog.review_status == "rejected"
            ).count()
            
            stats.pending = month_changes_query.filter(
                ChangeLog.review_status == "pending"
            ).count()
            
            metrics.monthly_trend.append(stats)
        
        return metrics
    
    def get_jurisdiction_breakdown(
        self,
        db: Session,
        year: int,
        month: int
    ) -> Dict[str, Dict[str, int]]:
        """
        Get per-jurisdiction breakdown of metrics.
        
        This replaces the manual "Forms Monitoring Spreadsheet" that tracks
        totals per jurisdiction (Connecticut CSV example from PoC).
        
        Args:
            db: Database session
            year: Year
            month: Month
            
        Returns:
            Dictionary mapping jurisdiction names to their metrics
        """
        # Calculate date range
        start_date = datetime(year, month, 1)
        if month == 12:
            end_date = datetime(year + 1, 1, 1)
        else:
            end_date = datetime(year, month + 1, 1)
        
        # Get all URLs with their changes this month
        urls = db.query(MonitoredURL).filter(MonitoredURL.enabled == True).all()
        
        breakdown = {}
        
        for url in urls:
            # Use URL name as jurisdiction identifier
            jurisdiction = url.name.split(" - ")[0] if " - " in url.name else url.name
            
            if jurisdiction not in breakdown:
                breakdown[jurisdiction] = {
                    "monitored": 0,
                    "updated": 0,
                    "reviewed": 0,
                    "tagged": 0,  # Placeholder for future tagging integration
                }
            
            breakdown[jurisdiction]["monitored"] += 1
            
            # Count changes for this URL this month
            changes = db.query(ChangeLog).filter(
                ChangeLog.monitored_url_id == url.id,
                ChangeLog.detected_at >= start_date,
                ChangeLog.detected_at < end_date
            ).all()
            
            for change in changes:
                if change.change_type != "new":
                    breakdown[jurisdiction]["updated"] += 1
                if change.reviewed:
                    breakdown[jurisdiction]["reviewed"] += 1
        
        return breakdown


# Singleton instance
metrics_tracker = MetricsTracker()
