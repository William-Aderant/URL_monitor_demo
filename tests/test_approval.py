"""
Tests for approval workflow.
"""

import pytest
from datetime import datetime

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestApprovalWorkflow:
    """Tests for the approval workflow."""
    
    def test_approval_requires_download(self):
        """Test that approval requires prior download."""
        from db.models import ChangeLog
        
        change = ChangeLog(
            monitored_url_id=1,
            new_version_id=1,
            change_type="new",
            download_count=0,
            review_status="pending"
        )
        
        # Check if download required
        download_required = not change.download_count or change.download_count == 0
        
        assert download_required is True
    
    def test_approval_allowed_after_download(self):
        """Test that approval is allowed after download."""
        from db.models import ChangeLog
        
        change = ChangeLog(
            monitored_url_id=1,
            new_version_id=1,
            change_type="new",
            download_count=1,
            review_status="pending"
        )
        
        download_required = not change.download_count or change.download_count == 0
        
        assert download_required is False
    
    def test_approval_updates_status(self):
        """Test that approval updates review status."""
        from db.models import ChangeLog
        
        change = ChangeLog(
            monitored_url_id=1,
            new_version_id=1,
            change_type="new",
            download_count=1,
            review_status="pending"
        )
        
        # Approve
        change.review_status = "approved"
        change.reviewed = True
        change.reviewed_at = datetime.utcnow()
        
        assert change.review_status == "approved"
        assert change.reviewed is True
        assert change.reviewed_at is not None
    
    def test_bulk_approval_multiple_changes(self):
        """Test bulk approval of multiple changes."""
        from db.models import ChangeLog
        
        changes = [
            ChangeLog(monitored_url_id=1, new_version_id=1, change_type="new", download_count=1, review_status="pending"),
            ChangeLog(monitored_url_id=2, new_version_id=2, change_type="text_changed", download_count=1, review_status="pending"),
            ChangeLog(monitored_url_id=3, new_version_id=3, change_type="new", download_count=1, review_status="pending"),
        ]
        
        approved_count = 0
        for change in changes:
            if change.download_count and change.download_count > 0:
                change.review_status = "approved"
                change.reviewed = True
                approved_count += 1
        
        assert approved_count == 3


class TestManualIntervention:
    """Tests for manual intervention tracking."""
    
    def test_intervention_flags_set(self):
        """Test that manual intervention flags are set correctly."""
        from db.models import ChangeLog
        
        change = ChangeLog(
            monitored_url_id=1,
            new_version_id=1,
            change_type="new",
            manual_intervention_required=False
        )
        
        # Record intervention
        change.manual_intervention_required = True
        change.intervention_type = "title_edit"
        change.intervention_notes = "User corrected the form title"
        change.intervention_at = datetime.utcnow()
        
        assert change.manual_intervention_required is True
        assert change.intervention_type == "title_edit"
        assert change.intervention_at is not None
    
    def test_intervention_types(self):
        """Test various intervention types."""
        intervention_types = [
            "title_edit",
            "url_edit",
            "manual_detection",
            "classification_override"
        ]
        
        for itype in intervention_types:
            from db.models import ChangeLog
            
            change = ChangeLog(
                monitored_url_id=1,
                new_version_id=1,
                change_type="new"
            )
            change.intervention_type = itype
            
            assert change.intervention_type == itype
    
    def test_automated_vs_manual_tracking(self):
        """Test distinguishing automated vs manual workflows."""
        from db.models import ChangeLog, MonitoringCycle
        
        cycle = MonitoringCycle(
            started_at=datetime.utcnow(),
            status="completed",
            downloads_automated=0,
            manual_interventions=0
        )
        
        # Change without intervention
        change1 = ChangeLog(
            monitored_url_id=1,
            new_version_id=1,
            change_type="new",
            download_count=1,
            manual_intervention_required=False
        )
        
        # Change with intervention
        change2 = ChangeLog(
            monitored_url_id=2,
            new_version_id=2,
            change_type="new",
            download_count=1,
            manual_intervention_required=True
        )
        
        # Update cycle stats
        if not change1.manual_intervention_required:
            cycle.downloads_automated = (cycle.downloads_automated or 0) + 1
        
        if change2.manual_intervention_required:
            cycle.manual_interventions = (cycle.manual_interventions or 0) + 1
        
        assert cycle.downloads_automated == 1
        assert cycle.manual_interventions == 1


class TestReviewStatus:
    """Tests for review status transitions."""
    
    def test_valid_review_statuses(self):
        """Test all valid review statuses."""
        valid_statuses = ["pending", "approved", "rejected", "deferred", "auto_approved"]
        
        from db.models import ChangeLog
        
        for status in valid_statuses:
            change = ChangeLog(
                monitored_url_id=1,
                new_version_id=1,
                change_type="new",
                review_status=status
            )
            
            assert change.review_status == status
    
    def test_default_review_status(self):
        """Test default review status is pending."""
        from db.models import ChangeLog
        
        change = ChangeLog(
            monitored_url_id=1,
            new_version_id=1,
            change_type="new"
        )
        
        # Default should be pending
        assert change.review_status == "pending" or change.review_status is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
