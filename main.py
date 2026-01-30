"""
FastAPI application entry point for PDF Monitor.
"""

from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

# Configure logging
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer()
    ]
)

logger = structlog.get_logger()

from config import settings
from db.migrations import run_migrations
from api.routes import router
from services.scheduler import init_scheduler, shutdown_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Startup
    logger.info("Starting PDF Monitor", version=settings.APP_VERSION)
    
    # Ensure directories exist
    settings.ensure_directories()
    
    # Run migrations
    run_migrations()
    
    # Validate configuration
    issues = settings.validate()
    if issues:
        for issue in issues:
            logger.warning(f"Configuration issue: {issue}")
    
    # Initialize and start the scheduler for automated monitoring cycles
    if settings.SCHEDULER_ENABLED:
        logger.info("Initializing scheduler for automated monitoring")
        init_scheduler(app)
    else:
        logger.info("Scheduler disabled - monitoring will be manual only")
    
    yield
    
    # Shutdown
    logger.info("Shutting down PDF Monitor")
    
    # Shutdown scheduler gracefully
    shutdown_scheduler()


# Create FastAPI app
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Court Form PDF Monitoring System",
    lifespan=lifespan
)

# Mount static files
static_path = Path(__file__).parent / "static"
static_path.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

# Include routes
app.include_router(router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )


