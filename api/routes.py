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
    """Dashboard showing all enabled monitored URLs."""
    urls = db.query(MonitoredURL).filter(MonitoredURL.enabled == True).order_by(MonitoredURL.name).all()
    
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
    """Page showing recent changes across all enabled URLs."""
    changes = version_manager.get_recent_changes(db, limit=50)
    
    # Enrich with URL names, filtering out disabled URLs
    change_data = []
    for change in changes:
        url = db.query(MonitoredURL).filter(
            MonitoredURL.id == change.monitored_url_id
        ).first()
        
        # Skip changes for disabled URLs
        if url and not url.enabled:
            continue
        
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


@router.get("/api/urls/{url_id}/versions/{version_id}/preview")
async def get_version_preview(
    url_id: int,
    version_id: int,
    db: Session = Depends(get_db)
):
    """Get preview image (PNG) of the first page."""
    # First try to get existing preview
    preview_path = file_store.get_preview_image(url_id, version_id)
    
    if preview_path and preview_path.exists():
        return FileResponse(
            preview_path,
            media_type="image/png",
            filename=f"preview_{version_id}.png"
        )
    
    # If no preview exists, generate it on the fly
    pdf_path = version_manager.get_normalized_pdf_path(db, version_id)
    if not pdf_path or not pdf_path.exists():
        raise HTTPException(status_code=404, detail="PDF not found")
    
    try:
        from services.title_extractor import TitleExtractor
        extractor = TitleExtractor()
        
        # Generate preview
        preview_output = file_store.get_preview_image_path(url_id, version_id)
        image_bytes = extractor.convert_pdf_to_image(pdf_path, preview_output)
        
        if image_bytes and preview_output.exists():
            return FileResponse(
                preview_output,
                media_type="image/png",
                filename=f"preview_{version_id}.png"
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate preview: {str(e)}")
    
    raise HTTPException(status_code=404, detail="Preview not available")


@router.get("/api/urls/{url_id}/versions/{version_id}/diff-preview")
async def get_diff_preview(
    url_id: int,
    version_id: int,
    db: Session = Depends(get_db)
):
    """
    Get the visual diff preview image for a version.
    Shows changes from the previous version with yellow highlighting.
    """
    # First try to get existing diff image
    diff_path = file_store.get_diff_image(url_id, version_id)
    
    if diff_path and diff_path.exists():
        return FileResponse(
            diff_path,
            media_type="image/png",
            filename=f"diff_{version_id}.png"
        )
    
    # If no diff exists, try to generate it
    version = db.query(PDFVersion).filter(
        PDFVersion.id == version_id,
        PDFVersion.monitored_url_id == url_id
    ).first()
    
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")
    
    # Find previous version
    prev_version = db.query(PDFVersion).filter(
        PDFVersion.monitored_url_id == url_id,
        PDFVersion.version_number < version.version_number
    ).order_by(PDFVersion.version_number.desc()).first()
    
    if not prev_version:
        raise HTTPException(status_code=404, detail="No previous version to compare against")
    
    # Get PDF paths
    curr_pdf = version_manager.get_normalized_pdf_path(db, version_id)
    prev_pdf = version_manager.get_normalized_pdf_path(db, prev_version.id)
    
    if not curr_pdf or not prev_pdf:
        raise HTTPException(status_code=404, detail="PDF files not found")
    
    try:
        from services.visual_diff import VisualDiff
        differ = VisualDiff()
        
        diff_output = file_store.get_diff_image_path(url_id, version_id)
        result = differ.generate_diff(
            old_pdf_path=prev_pdf,
            new_pdf_path=curr_pdf,
            output_path=diff_output
        )
        
        if result.success and result.diff_image_path.exists():
            return FileResponse(
                result.diff_image_path,
                media_type="image/png",
                filename=f"diff_{version_id}.png"
            )
        else:
            raise HTTPException(status_code=500, detail=f"Failed to generate diff: {result.error}")
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating diff: {str(e)}")


@router.post("/api/urls/{url_id}/versions/{version_id}/extract-title")
async def extract_title_for_version(
    url_id: int,
    version_id: int,
    db: Session = Depends(get_db)
):
    """
    Manually trigger title extraction for a specific version.
    Uses AWS Textract + Bedrock to extract title and form number.
    """
    from services.title_extractor import TitleExtractor
    
    # Get the version
    version = db.query(PDFVersion).filter(PDFVersion.id == version_id).first()
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")
    
    # Get PDF path
    pdf_path = version_manager.get_normalized_pdf_path(db, version_id)
    if not pdf_path or not pdf_path.exists():
        raise HTTPException(status_code=404, detail="PDF not found")
    
    # Initialize extractor
    extractor = TitleExtractor()
    
    if not extractor.is_available():
        raise HTTPException(
            status_code=503, 
            detail="AWS credentials not configured. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY."
        )
    
    # Extract title
    preview_output = file_store.get_preview_image_path(url_id, version_id)
    result = extractor.extract_title(pdf_path, preview_output)
    
    if not result.success:
        raise HTTPException(status_code=500, detail=f"Title extraction failed: {result.error}")
    
    # Update the version in database
    version.formatted_title = result.formatted_title
    version.form_number = result.form_number
    version.title_confidence = result.combined_confidence
    version.title_extraction_method = result.extraction_method
    db.commit()
    
    return {
        "success": True,
        "formatted_title": result.formatted_title,
        "form_number": result.form_number,
        "confidence": result.combined_confidence,
        "reasoning": result.reasoning
    }


@router.post("/api/changes/{change_id}/approve")
async def approve_change(
    change_id: int,
    notes: str = None,
    db: Session = Depends(get_db)
):
    """
    Approve/mark a change as reviewed.
    """
    from datetime import datetime
    
    change = db.query(ChangeLog).filter(ChangeLog.id == change_id).first()
    if not change:
        raise HTTPException(status_code=404, detail="Change not found")
    
    change.reviewed = True
    change.reviewed_at = datetime.utcnow()
    change.review_notes = notes
    db.commit()
    
    return {
        "success": True,
        "change_id": change_id,
        "reviewed": True,
        "reviewed_at": change.reviewed_at.isoformat()
    }


@router.post("/api/changes/{change_id}/unapprove")
async def unapprove_change(
    change_id: int,
    db: Session = Depends(get_db)
):
    """
    Remove approval from a change (mark as not reviewed).
    """
    change = db.query(ChangeLog).filter(ChangeLog.id == change_id).first()
    if not change:
        raise HTTPException(status_code=404, detail="Change not found")
    
    change.reviewed = False
    change.reviewed_at = None
    change.review_notes = None
    db.commit()
    
    return {
        "success": True,
        "change_id": change_id,
        "reviewed": False
    }


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

