"""
Scheduler service for automated monitoring cycles.
Uses APScheduler with database-driven configuration.
"""

import structlog
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Callable
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.executors.pool import ThreadPoolExecutor
from sqlalchemy.orm import Session

from config import settings
from db.database import SessionLocal
from db.models import ScheduleConfig, MonitoringCycle

logger = structlog.get_logger()

# Global scheduler instance
scheduler: Optional[BackgroundScheduler] = None

# Job ID for the monitoring job
MONITORING_JOB_ID = "monitoring_cycle"


def get_scheduler() -> Optional[BackgroundScheduler]:
    """Get the global scheduler instance."""
    return scheduler


def get_schedule_config(db: Session) -> Optional[ScheduleConfig]:
    """Get the current schedule configuration from database."""
    return db.query(ScheduleConfig).first()


def create_default_config(db: Session) -> ScheduleConfig:
    """Create default schedule configuration if none exists."""
    config = ScheduleConfig(
        enabled=True,
        schedule_type="daily",
        daily_time=settings.DEFAULT_SCHEDULE_TIME,
        timezone=settings.DEFAULT_TIMEZONE,
        created_at=datetime.utcnow()
    )
    db.add(config)
    db.commit()
    db.refresh(config)
    logger.info("Created default schedule configuration", config_id=config.id)
    return config


def build_cron_trigger(config: ScheduleConfig) -> Optional[CronTrigger]:
    """
    Build a CronTrigger from schedule configuration.
    
    Returns None if the configuration is invalid or disabled.
    """
    if not config.enabled:
        return None
    
    try:
        if config.schedule_type == "daily":
            # Daily at specified time
            hour, minute = config.daily_time.split(":")
            return CronTrigger(
                hour=int(hour),
                minute=int(minute),
                timezone=config.timezone or "UTC"
            )
        
        elif config.schedule_type == "weekly":
            # Weekly on specified days at specified time
            if not config.weekly_days:
                logger.warning("Weekly schedule configured but no days specified")
                return None
            
            hour, minute = config.weekly_time.split(":")
            day_of_week = ",".join(config.weekly_days)
            return CronTrigger(
                day_of_week=day_of_week,
                hour=int(hour),
                minute=int(minute),
                timezone=config.timezone or "UTC"
            )
        
        elif config.schedule_type == "custom":
            # Custom cron expression
            if not config.cron_expression:
                logger.warning("Custom schedule configured but no cron expression specified")
                return None
            
            return CronTrigger.from_crontab(
                config.cron_expression,
                timezone=config.timezone or "UTC"
            )
        
        else:
            logger.warning("Unknown schedule type", schedule_type=config.schedule_type)
            return None
            
    except Exception as e:
        logger.error("Failed to build cron trigger", error=str(e))
        return None


def run_scheduled_monitoring_cycle():
    """
    Execute a scheduled monitoring cycle.
    This is the job function called by APScheduler.
    """
    logger.info("Starting scheduled monitoring cycle")
    
    # Import here to avoid circular imports
    from cli import MonitoringOrchestrator
    
    db = SessionLocal()
    try:
        # Create cycle record
        cycle = MonitoringCycle(
            started_at=datetime.utcnow(),
            status="running",
            triggered_by="scheduled"
        )
        
        # Get and snapshot schedule config
        config = get_schedule_config(db)
        if config:
            cycle.schedule_config_snapshot = {
                "schedule_type": config.schedule_type,
                "daily_time": config.daily_time,
                "weekly_days": config.weekly_days,
                "weekly_time": config.weekly_time,
                "cron_expression": config.cron_expression,
                "timezone": config.timezone
            }
            # Update last run time
            config.last_run_at = datetime.utcnow()
        
        db.add(cycle)
        db.commit()
        db.refresh(cycle)
        
        cycle_id = cycle.id
        logger.info("Created monitoring cycle record", cycle_id=cycle_id)
        
    except Exception as e:
        logger.error("Failed to create cycle record", error=str(e))
        db.rollback()
        cycle_id = None
    finally:
        db.close()
    
    # Run the actual monitoring cycle
    try:
        orchestrator = MonitoringOrchestrator()
        stats = orchestrator.run_cycle(cycle_id=cycle_id)
        
        # Update cycle with results
        db = SessionLocal()
        try:
            cycle = db.query(MonitoringCycle).filter_by(id=cycle_id).first()
            if cycle:
                cycle.completed_at = datetime.utcnow()
                cycle.duration_seconds = (cycle.completed_at - cycle.started_at).total_seconds()
                cycle.status = "completed"
                cycle.total_urls_checked = stats.get("total", 0)
                cycle.successful_checks = stats.get("successful", 0)
                cycle.failed_checks = stats.get("failed", 0)
                cycle.changes_detected = stats.get("changes", 0)
                cycle.skipped_unchanged = stats.get("skipped", 0)
                cycle.error_count = stats.get("errors", 0)
                if stats.get("error_log"):
                    cycle.error_log = stats["error_log"]
                db.commit()
                logger.info("Completed scheduled monitoring cycle", 
                           cycle_id=cycle_id,
                           duration=cycle.duration_seconds,
                           changes=cycle.changes_detected)
        finally:
            db.close()
            
    except Exception as e:
        logger.error("Scheduled monitoring cycle failed", error=str(e))
        # Update cycle status to failed
        db = SessionLocal()
        try:
            cycle = db.query(MonitoringCycle).filter_by(id=cycle_id).first()
            if cycle:
                cycle.completed_at = datetime.utcnow()
                cycle.duration_seconds = (cycle.completed_at - cycle.started_at).total_seconds()
                cycle.status = "failed"
                cycle.error_log = str(e)
                db.commit()
        finally:
            db.close()


def update_scheduler_job(config: Optional[ScheduleConfig] = None):
    """
    Update the scheduler job based on current configuration.
    Call this when schedule configuration changes.
    """
    global scheduler
    
    if scheduler is None:
        logger.warning("Scheduler not initialized")
        return
    
    # Remove existing job if any
    try:
        scheduler.remove_job(MONITORING_JOB_ID)
        logger.info("Removed existing monitoring job")
    except Exception:
        pass  # Job might not exist
    
    # Get config from database if not provided
    if config is None:
        db = SessionLocal()
        try:
            config = get_schedule_config(db)
        finally:
            db.close()
    
    if config is None or not config.enabled:
        logger.info("Scheduler disabled or no configuration")
        return
    
    # Build trigger
    trigger = build_cron_trigger(config)
    if trigger is None:
        logger.warning("Could not build trigger from configuration")
        return
    
    # Add new job
    scheduler.add_job(
        run_scheduled_monitoring_cycle,
        trigger=trigger,
        id=MONITORING_JOB_ID,
        name="Monitoring Cycle",
        replace_existing=True,
        max_instances=1,  # Only one cycle at a time
        coalesce=True  # Combine missed runs
    )
    
    # Store schedule_type before potentially reassigning config
    schedule_type_for_log = config.schedule_type if config else None
    
    # Update next run time in config
    job = scheduler.get_job(MONITORING_JOB_ID)
    if job and job.next_run_time:
        db = SessionLocal()
        try:
            config = get_schedule_config(db)
            if config:
                config.next_run_at = job.next_run_time
                schedule_type_for_log = config.schedule_type  # Update from fresh session
                db.commit()
        finally:
            db.close()
    
    logger.info("Updated scheduler job", 
               schedule_type=schedule_type_for_log,
               next_run=job.next_run_time if job else None)


def get_next_run_time() -> Optional[datetime]:
    """Get the next scheduled run time."""
    global scheduler
    
    if scheduler is None:
        return None
    
    job = scheduler.get_job(MONITORING_JOB_ID)
    if job:
        return job.next_run_time
    return None


def trigger_manual_cycle(triggered_by: str = "manual") -> int:
    """
    Trigger a monitoring cycle manually.
    Returns the cycle ID.
    """
    logger.info("Triggering manual monitoring cycle", triggered_by=triggered_by)
    
    # Import here to avoid circular imports
    from cli import MonitoringOrchestrator
    
    db = SessionLocal()
    try:
        # Create cycle record
        cycle = MonitoringCycle(
            started_at=datetime.utcnow(),
            status="running",
            triggered_by=triggered_by
        )
        db.add(cycle)
        db.commit()
        db.refresh(cycle)
        cycle_id = cycle.id
        logger.info("Created manual monitoring cycle", cycle_id=cycle_id)
    finally:
        db.close()
    
    # Run in background thread to not block
    import threading
    
    def run_cycle():
        try:
            orchestrator = MonitoringOrchestrator()
            stats = orchestrator.run_cycle(cycle_id=cycle_id)
            
            # Update cycle with results
            db = SessionLocal()
            try:
                cycle = db.query(MonitoringCycle).filter_by(id=cycle_id).first()
                if cycle:
                    cycle.completed_at = datetime.utcnow()
                    cycle.duration_seconds = (cycle.completed_at - cycle.started_at).total_seconds()
                    cycle.status = "completed"
                    cycle.total_urls_checked = stats.get("total", 0)
                    cycle.successful_checks = stats.get("successful", 0)
                    cycle.failed_checks = stats.get("failed", 0)
                    cycle.changes_detected = stats.get("changes", 0)
                    cycle.skipped_unchanged = stats.get("skipped", 0)
                    cycle.error_count = stats.get("errors", 0)
                    if stats.get("error_log"):
                        cycle.error_log = stats["error_log"]
                    db.commit()
                    logger.info("Completed manual monitoring cycle",
                               cycle_id=cycle_id,
                               duration=cycle.duration_seconds)
            finally:
                db.close()
                
        except Exception as e:
            logger.error("Manual monitoring cycle failed", error=str(e))
            db = SessionLocal()
            try:
                cycle = db.query(MonitoringCycle).filter_by(id=cycle_id).first()
                if cycle:
                    cycle.completed_at = datetime.utcnow()
                    cycle.duration_seconds = (cycle.completed_at - cycle.started_at).total_seconds()
                    cycle.status = "failed"
                    cycle.error_log = str(e)
                    db.commit()
            finally:
                db.close()
    
    thread = threading.Thread(target=run_cycle, daemon=True)
    thread.start()
    
    return cycle_id


def init_scheduler(app=None):
    """
    Initialize the scheduler.
    Call this on application startup.
    """
    global scheduler
    
    if not settings.SCHEDULER_ENABLED:
        logger.info("Scheduler is disabled via configuration")
        return
    
    # Create scheduler with thread pool
    jobstores = {
        'default': MemoryJobStore()
    }
    executors = {
        'default': ThreadPoolExecutor(1)  # Single thread for monitoring jobs
    }
    job_defaults = {
        'coalesce': True,
        'max_instances': 1,
        'misfire_grace_time': 3600  # 1 hour grace time for misfired jobs
    }
    
    scheduler = BackgroundScheduler(
        jobstores=jobstores,
        executors=executors,
        job_defaults=job_defaults
    )
    
    # Ensure default config exists
    db = SessionLocal()
    try:
        config = get_schedule_config(db)
        if config is None:
            config = create_default_config(db)
    finally:
        db.close()
    
    # Start scheduler
    scheduler.start()
    logger.info("Scheduler started")
    
    # Add monitoring job based on config
    update_scheduler_job()


def shutdown_scheduler():
    """
    Shutdown the scheduler gracefully.
    Call this on application shutdown.
    """
    global scheduler
    
    if scheduler is not None:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler shutdown complete")
        scheduler = None


def get_scheduler_status() -> Dict[str, Any]:
    """
    Get current scheduler status for API/UI.
    """
    global scheduler
    
    db = SessionLocal()
    try:
        config = get_schedule_config(db)
        
        status = {
            "scheduler_enabled": settings.SCHEDULER_ENABLED,
            "scheduler_running": scheduler is not None and scheduler.running if scheduler else False,
            "config": None,
            "next_run": None,
            "last_run": None
        }
        
        if config:
            status["config"] = {
                "id": config.id,
                "enabled": config.enabled,
                "schedule_type": config.schedule_type,
                "daily_time": config.daily_time,
                "weekly_days": config.weekly_days,
                "weekly_time": config.weekly_time,
                "cron_expression": config.cron_expression,
                "timezone": config.timezone
            }
            status["last_run"] = config.last_run_at.isoformat() if config.last_run_at else None
        
        # Get next run from scheduler
        next_run = get_next_run_time()
        if next_run:
            status["next_run"] = next_run.isoformat()
        
        return status
    finally:
        db.close()


def update_schedule_config(
    db: Session,
    enabled: Optional[bool] = None,
    schedule_type: Optional[str] = None,
    daily_time: Optional[str] = None,
    weekly_days: Optional[list] = None,
    weekly_time: Optional[str] = None,
    cron_expression: Optional[str] = None,
    timezone: Optional[str] = None
) -> ScheduleConfig:
    """
    Update schedule configuration in database and refresh scheduler.
    """
    config = get_schedule_config(db)
    if config is None:
        config = create_default_config(db)
    
    # Update fields
    if enabled is not None:
        config.enabled = enabled
    if schedule_type is not None:
        config.schedule_type = schedule_type
    if daily_time is not None:
        config.daily_time = daily_time
    if weekly_days is not None:
        config.weekly_days = weekly_days
    if weekly_time is not None:
        config.weekly_time = weekly_time
    if cron_expression is not None:
        config.cron_expression = cron_expression
    if timezone is not None:
        config.timezone = timezone
    
    config.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(config)
    
    # Update scheduler job
    update_scheduler_job(config)
    
    logger.info("Updated schedule configuration", config_id=config.id)
    return config
