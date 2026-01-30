"""
Tests for the scheduler service.
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

# Test imports
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestScheduleConfig:
    """Tests for schedule configuration."""
    
    def test_build_daily_cron_trigger(self):
        """Test building a daily cron trigger."""
        from services.scheduler import build_cron_trigger
        from db.models import ScheduleConfig
        
        config = ScheduleConfig(
            enabled=True,
            schedule_type="daily",
            daily_time="02:00",
            timezone="UTC"
        )
        
        trigger = build_cron_trigger(config)
        
        assert trigger is not None
        # The trigger should have hour=2, minute=0
    
    def test_build_weekly_cron_trigger(self):
        """Test building a weekly cron trigger."""
        from services.scheduler import build_cron_trigger
        from db.models import ScheduleConfig
        
        config = ScheduleConfig(
            enabled=True,
            schedule_type="weekly",
            weekly_days=["monday", "wednesday", "friday"],
            weekly_time="03:30",
            timezone="UTC"
        )
        
        trigger = build_cron_trigger(config)
        
        assert trigger is not None
    
    def test_build_custom_cron_trigger(self):
        """Test building a custom cron trigger."""
        from services.scheduler import build_cron_trigger
        from db.models import ScheduleConfig
        
        config = ScheduleConfig(
            enabled=True,
            schedule_type="custom",
            cron_expression="0 */6 * * *",
            timezone="UTC"
        )
        
        trigger = build_cron_trigger(config)
        
        assert trigger is not None
    
    def test_disabled_config_returns_none(self):
        """Test that disabled config returns None trigger."""
        from services.scheduler import build_cron_trigger
        from db.models import ScheduleConfig
        
        config = ScheduleConfig(
            enabled=False,
            schedule_type="daily",
            daily_time="02:00"
        )
        
        trigger = build_cron_trigger(config)
        
        assert trigger is None
    
    def test_weekly_without_days_returns_none(self):
        """Test that weekly schedule without days returns None."""
        from services.scheduler import build_cron_trigger
        from db.models import ScheduleConfig
        
        config = ScheduleConfig(
            enabled=True,
            schedule_type="weekly",
            weekly_days=[],
            weekly_time="02:00"
        )
        
        trigger = build_cron_trigger(config)
        
        assert trigger is None


class TestSchedulerStatus:
    """Tests for scheduler status functions."""
    
    def test_get_scheduler_status_disabled(self):
        """Test scheduler status when disabled."""
        from services.scheduler import get_scheduler_status
        
        with patch('services.scheduler.settings') as mock_settings:
            mock_settings.SCHEDULER_ENABLED = False
            
            status = get_scheduler_status()
            
            assert "scheduler_enabled" in status
    
    def test_scheduler_status_structure(self):
        """Test that scheduler status has expected structure."""
        from services.scheduler import get_scheduler_status
        
        status = get_scheduler_status()
        
        assert "scheduler_enabled" in status
        assert "scheduler_running" in status
        assert "config" in status
        assert "next_run" in status
        assert "last_run" in status


class TestMonitoringCycle:
    """Tests for monitoring cycle tracking."""
    
    def test_monitoring_cycle_creation(self):
        """Test that monitoring cycles are created correctly."""
        from db.models import MonitoringCycle
        
        cycle = MonitoringCycle(
            started_at=datetime.utcnow(),
            status="running",
            triggered_by="test"
        )
        
        assert cycle.started_at is not None
        assert cycle.status == "running"
        assert cycle.triggered_by == "test"
        assert cycle.total_urls_checked == 0
    
    def test_cycle_completion(self):
        """Test cycle completion updates."""
        from db.models import MonitoringCycle
        
        cycle = MonitoringCycle(
            started_at=datetime.utcnow() - timedelta(seconds=30),
            status="running",
            triggered_by="test"
        )
        
        cycle.completed_at = datetime.utcnow()
        cycle.duration_seconds = 30.5
        cycle.status = "completed"
        cycle.total_urls_checked = 10
        cycle.successful_checks = 8
        cycle.failed_checks = 2
        cycle.changes_detected = 3
        
        assert cycle.status == "completed"
        assert cycle.duration_seconds == 30.5
        assert cycle.total_urls_checked == 10


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
