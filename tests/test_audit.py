"""
Tests for audit and metrics functionality.
"""

import pytest
from datetime import datetime, timedelta

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestMonitoringCycleTracking:
    """Tests for monitoring cycle tracking."""
    
    def test_cycle_creation(self):
        """Test monitoring cycle creation."""
        from db.models import MonitoringCycle
        
        cycle = MonitoringCycle(
            started_at=datetime.utcnow(),
            status="running",
            triggered_by="scheduled"
        )
        
        assert cycle.started_at is not None
        assert cycle.status == "running"
        assert cycle.triggered_by == "scheduled"
    
    def test_cycle_statistics(self):
        """Test cycle statistics tracking."""
        from db.models import MonitoringCycle
        
        cycle = MonitoringCycle(
            started_at=datetime.utcnow(),
            status="completed",
            triggered_by="manual",
            total_urls_checked=100,
            successful_checks=95,
            failed_checks=5,
            changes_detected=10,
            skipped_unchanged=85,
            downloads_automated=8,
            manual_interventions=2
        )
        
        assert cycle.total_urls_checked == 100
        assert cycle.successful_checks == 95
        assert cycle.failed_checks == 5
        assert cycle.changes_detected == 10
    
    def test_cycle_duration_calculation(self):
        """Test cycle duration calculation."""
        from db.models import MonitoringCycle
        
        start = datetime.utcnow()
        end = start + timedelta(seconds=45.5)
        
        cycle = MonitoringCycle(
            started_at=start,
            completed_at=end,
            status="completed"
        )
        
        cycle.duration_seconds = (cycle.completed_at - cycle.started_at).total_seconds()
        
        assert cycle.duration_seconds == 45.5
    
    def test_triggered_by_values(self):
        """Test various triggered_by values."""
        from db.models import MonitoringCycle
        
        trigger_types = ["scheduled", "manual", "cli", "api"]
        
        for trigger in trigger_types:
            cycle = MonitoringCycle(
                started_at=datetime.utcnow(),
                status="running",
                triggered_by=trigger
            )
            
            assert cycle.triggered_by == trigger


class TestCycleURLResults:
    """Tests for individual URL check results."""
    
    def test_url_result_creation(self):
        """Test URL result creation."""
        from db.models import CycleURLResult
        
        result = CycleURLResult(
            cycle_id=1,
            monitored_url_id=1,
            status="success",
            started_at=datetime.utcnow()
        )
        
        assert result.cycle_id == 1
        assert result.monitored_url_id == 1
        assert result.status == "success"
    
    def test_url_result_with_error(self):
        """Test URL result with error."""
        from db.models import CycleURLResult
        
        result = CycleURLResult(
            cycle_id=1,
            monitored_url_id=1,
            status="failed",
            error_message="Connection timeout",
            started_at=datetime.utcnow()
        )
        
        assert result.status == "failed"
        assert result.error_message == "Connection timeout"
    
    def test_url_result_with_change(self):
        """Test URL result that detected a change."""
        from db.models import CycleURLResult
        
        result = CycleURLResult(
            cycle_id=1,
            monitored_url_id=1,
            status="success",
            change_detected=True,
            change_log_id=42
        )
        
        assert result.change_detected is True
        assert result.change_log_id == 42
    
    def test_url_result_duration(self):
        """Test URL result duration tracking."""
        from db.models import CycleURLResult
        
        start = datetime.utcnow()
        end = start + timedelta(milliseconds=1500)
        
        result = CycleURLResult(
            cycle_id=1,
            monitored_url_id=1,
            status="success",
            started_at=start,
            completed_at=end,
            duration_ms=1500
        )
        
        assert result.duration_ms == 1500


class TestAuditStatistics:
    """Tests for audit statistics calculations."""
    
    def test_automation_rate_calculation(self):
        """Test automation rate calculation."""
        automated = 80
        manual = 20
        total = automated + manual
        
        automation_rate = automated / total if total > 0 else 0
        
        assert automation_rate == 0.8
    
    def test_success_rate_calculation(self):
        """Test success rate calculation."""
        successful = 95
        failed = 5
        total = successful + failed
        
        success_rate = successful / total if total > 0 else 0
        
        assert success_rate == 0.95
    
    def test_zero_division_handling(self):
        """Test handling of zero division in calculations."""
        total_downloads = 0
        automated = 0
        
        automation_rate = automated / total_downloads if total_downloads > 0 else 0
        
        assert automation_rate == 0
    
    def test_average_duration_calculation(self):
        """Test average cycle duration calculation."""
        durations = [30.5, 45.2, 28.8, 52.1, 33.4]
        
        avg_duration = sum(durations) / len(durations)
        
        assert abs(avg_duration - 38.0) < 0.1


class TestScheduleConfig:
    """Tests for schedule configuration."""
    
    def test_schedule_config_creation(self):
        """Test schedule config creation."""
        from db.models import ScheduleConfig
        
        config = ScheduleConfig(
            enabled=True,
            schedule_type="daily",
            daily_time="02:00",
            timezone="UTC"
        )
        
        assert config.enabled is True
        assert config.schedule_type == "daily"
        assert config.daily_time == "02:00"
    
    def test_weekly_schedule(self):
        """Test weekly schedule configuration."""
        from db.models import ScheduleConfig
        
        config = ScheduleConfig(
            enabled=True,
            schedule_type="weekly",
            weekly_days=["monday", "wednesday", "friday"],
            weekly_time="03:00",
            timezone="America/Los_Angeles"
        )
        
        assert config.schedule_type == "weekly"
        assert "monday" in config.weekly_days
        assert len(config.weekly_days) == 3
    
    def test_custom_cron_schedule(self):
        """Test custom cron schedule configuration."""
        from db.models import ScheduleConfig
        
        config = ScheduleConfig(
            enabled=True,
            schedule_type="custom",
            cron_expression="0 */6 * * *"
        )
        
        assert config.schedule_type == "custom"
        assert config.cron_expression == "0 */6 * * *"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
