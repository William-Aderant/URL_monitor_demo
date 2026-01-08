"""
FastAPI routes for PDF Monitor.

Thin routes that delegate to service layer.
"""

from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request, Form
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from config import settings
from db.database import get_db
from db.models import MonitoredURL, PDFVersion, ChangeLog
from api.schemas import (
    MonitoredURLCreate,
    MonitoredURLResponse,
    PDFVersionResponse,
    ChangeLogResponse,
    MonitoringRunRequest,
    MonitoringRunResponse,
    StatusResponse
)
from storage.file_store import FileStore
from storage.version_manager import VersionManager

router = APIRouter()

# Setup templates
templates = Jinja2Templates(directory="templates")

# Initialize services
file_store = FileStore()
version_manager = VersionManager(file_store)


# ============================================================================
# HTML Routes (for UI)
# ============================================================================

@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db)):
    """Dashboard showing all monitored URLs."""
    urls = db.query(MonitoredURL).order_by(MonitoredURL.name).all()
    
    # Enrich with version counts
    url_data = []
    for url in urls:
        version_count = db.query(PDFVersion).filter(
            PDFVersion.monitored_url_id == url.id
        ).count()
        
        latest_version = version_manager.get_latest_version(db, url.id)
        
        # Get most recent change
        recent_change = db.query(ChangeLog).filter(
            ChangeLog.monitored_url_id == url.id
        ).order_by(ChangeLog.detected_at.desc()).first()
        
        url_data.append({
            "url": url,
            "version_count": version_count,
            "latest_version": latest_version,
            "recent_change": recent_change
        })
    
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "urls": url_data,
            "now": datetime.utcnow()
        }
    )


@router.get("/url/{url_id}", response_class=HTMLResponse)
async def url_detail(request: Request, url_id: int, db: Session = Depends(get_db)):
    """Detail page for a specific URL."""
    url = db.query(MonitoredURL).filter(MonitoredURL.id == url_id).first()
    
    if not url:
        raise HTTPException(status_code=404, detail="URL not found")
    
    versions = version_manager.get_version_history(db, url_id, limit=50)
    changes = version_manager.get_url_changes(db, url_id, limit=20)
    
    return templates.TemplateResponse(
        "url_detail.html",
        {
            "request": request,
            "url": url,
            "versions": versions,
            "changes": changes,
            "now": datetime.utcnow()
        }
    )


@router.get("/changes", response_class=HTMLResponse)
async def changes_page(request: Request, db: Session = Depends(get_db)):
    """Page showing recent changes across all URLs."""
    changes = version_manager.get_recent_changes(db, limit=50)
    
    # Enrich with URL names
    change_data = []
    for change in changes:
        url = db.query(MonitoredURL).filter(
            MonitoredURL.id == change.monitored_url_id
        ).first()
        
        change_data.append({
            "change": change,
            "url_name": url.name if url else "Unknown"
        })
    
    return templates.TemplateResponse(
        "changes.html",
        {
            "request": request,
            "changes": change_data,
            "now": datetime.utcnow()
        }
    )


@router.post("/monitor/run", response_class=HTMLResponse)
async def run_monitoring_form(
    request: Request,
    url_id: Optional[int] = Form(None),
    db: Session = Depends(get_db)
):
    """
    HTML form handler for triggering monitoring run.
    Redirects back to dashboard after completion.
    """
    from cli import MonitoringOrchestrator
    
    orchestrator = MonitoringOrchestrator()
    results = orchestrator.run_cycle(db, url_id)
    
    # Redirect back to dashboard
    return RedirectResponse(url="/", status_code=303)


# ============================================================================
# API Routes
# ============================================================================

@router.get("/api/urls", response_model=list[MonitoredURLResponse])
async def list_urls(db: Session = Depends(get_db)):
    """List all monitored URLs."""
    urls = db.query(MonitoredURL).order_by(MonitoredURL.name).all()
    
    result = []
    for url in urls:
        version_count = db.query(PDFVersion).filter(
            PDFVersion.monitored_url_id == url.id
        ).count()
        
        result.append(MonitoredURLResponse(
            id=url.id,
            name=url.name,
            url=url.url,
            description=url.description,
            check_interval_hours=url.check_interval_hours,
            enabled=url.enabled,
            created_at=url.created_at,
            updated_at=url.updated_at,
            last_checked_at=url.last_checked_at,
            last_change_at=url.last_change_at,
            version_count=version_count
        ))
    
    return result


@router.post("/api/urls", response_model=MonitoredURLResponse)
async def create_url(url_data: MonitoredURLCreate, db: Session = Depends(get_db)):
    """Create a new monitored URL."""
    # Check for duplicate
    existing = db.query(MonitoredURL).filter(
        MonitoredURL.url == url_data.url
    ).first()
    
    if existing:
        raise HTTPException(
            status_code=400,
            detail="URL already being monitored"
        )
    
    url = MonitoredURL(**url_data.model_dump())
    db.add(url)
    db.commit()
    db.refresh(url)
    
    return MonitoredURLResponse(
        id=url.id,
        name=url.name,
        url=url.url,
        description=url.description,
        check_interval_hours=url.check_interval_hours,
        enabled=url.enabled,
        created_at=url.created_at,
        updated_at=url.updated_at,
        last_checked_at=url.last_checked_at,
        last_change_at=url.last_change_at,
        version_count=0
    )


@router.get("/api/urls/{url_id}", response_model=MonitoredURLResponse)
async def get_url(url_id: int, db: Session = Depends(get_db)):
    """Get a specific monitored URL."""
    url = db.query(MonitoredURL).filter(MonitoredURL.id == url_id).first()
    
    if not url:
        raise HTTPException(status_code=404, detail="URL not found")
    
    version_count = db.query(PDFVersion).filter(
        PDFVersion.monitored_url_id == url.id
    ).count()
    
    return MonitoredURLResponse(
        id=url.id,
        name=url.name,
        url=url.url,
        description=url.description,
        check_interval_hours=url.check_interval_hours,
        enabled=url.enabled,
        created_at=url.created_at,
        updated_at=url.updated_at,
        last_checked_at=url.last_checked_at,
        last_change_at=url.last_change_at,
        version_count=version_count
    )


@router.delete("/api/urls/{url_id}")
async def delete_url(url_id: int, db: Session = Depends(get_db)):
    """Delete a monitored URL and all its versions."""
    url = db.query(MonitoredURL).filter(MonitoredURL.id == url_id).first()
    
    if not url:
        raise HTTPException(status_code=404, detail="URL not found")
    
    # Delete all versions from file store
    versions = file_store.list_versions(url_id)
    for version_id in versions:
        file_store.delete_version(url_id, version_id)
    
    # Delete database records (cascades to versions and changes)
    db.delete(url)
    db.commit()
    
    return {"status": "deleted", "url_id": url_id}


@router.get("/api/urls/{url_id}/versions", response_model=list[PDFVersionResponse])
async def list_versions(url_id: int, db: Session = Depends(get_db)):
    """List all versions for a URL."""
    versions = version_manager.get_version_history(db, url_id)
    return [PDFVersionResponse.model_validate(v) for v in versions]


@router.get("/api/urls/{url_id}/versions/{version_id}/pdf")
async def get_version_pdf(
    url_id: int,
    version_id: int,
    normalized: bool = False,
    db: Session = Depends(get_db)
):
    """Download a PDF version."""
    if normalized:
        pdf_path = version_manager.get_normalized_pdf_path(db, version_id)
    else:
        pdf_path = version_manager.get_original_pdf_path(db, version_id)
    
    if not pdf_path or not pdf_path.exists():
        raise HTTPException(status_code=404, detail="PDF not found")
    
    return FileResponse(
        pdf_path,
        media_type="application/pdf",
        filename=f"version_{version_id}_{'normalized' if normalized else 'original'}.pdf"
    )


@router.get("/api/urls/{url_id}/versions/{version_id}/text")
async def get_version_text(
    url_id: int,
    version_id: int,
    db: Session = Depends(get_db)
):
    """Get extracted text for a version."""
    text = version_manager.get_version_text(db, version_id)
    
    if text is None:
        raise HTTPException(status_code=404, detail="Text not found")
    
    return {"version_id": version_id, "text": text}


@router.get("/api/changes", response_model=list[ChangeLogResponse])
async def list_changes(limit: int = 50, db: Session = Depends(get_db)):
    """List recent changes across all URLs."""
    changes = version_manager.get_recent_changes(db, limit=limit)
    
    result = []
    for change in changes:
        url = db.query(MonitoredURL).filter(
            MonitoredURL.id == change.monitored_url_id
        ).first()
        
        result.append(ChangeLogResponse(
            id=change.id,
            monitored_url_id=change.monitored_url_id,
            previous_version_id=change.previous_version_id,
            new_version_id=change.new_version_id,
            change_type=change.change_type,
            affected_pages=change.affected_pages,
            diff_summary=change.diff_summary,
            pdf_hash_changed=change.pdf_hash_changed,
            text_hash_changed=change.text_hash_changed,
            detected_at=change.detected_at,
            url_name=url.name if url else None
        ))
    
    return result


@router.post("/api/monitor/run", response_model=MonitoringRunResponse)
async def run_monitoring(
    request: MonitoringRunRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    Trigger a monitoring run.
    
    Runs synchronously for now. In production, use background tasks.
    """
    from cli import MonitoringOrchestrator
    
    orchestrator = MonitoringOrchestrator()
    results = orchestrator.run_cycle(db, request.url_id)
    
    return MonitoringRunResponse(**results)


@router.get("/api/status", response_model=StatusResponse)
async def get_status(db: Session = Depends(get_db)):
    """Get system status."""
    total_urls = db.query(MonitoredURL).count()
    enabled_urls = db.query(MonitoredURL).filter(MonitoredURL.enabled == True).count()
    total_versions = db.query(PDFVersion).count()
    total_changes = db.query(ChangeLog).count()
    storage_size = file_store.get_storage_size()
    
    return StatusResponse(
        total_urls=total_urls,
        enabled_urls=enabled_urls,
        total_versions=total_versions,
        total_changes=total_changes,
        storage_size_bytes=storage_size
    )

