"""
FastAPI routes for PDF Monitor.

Thin routes that delegate to service layer.
"""

from datetime import datetime
from pathlib import Path
from typing import Optional
import re

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
    StatusResponse,
    ReviewRequest,
    ReviewResponse,
    BulkReviewRequest,
    BulkReviewResponse,
    ClassificationOverrideRequest
)
from storage.file_store import FileStore
from storage.version_manager import VersionManager
from services.action_recommender import action_recommender, ActionType
from services.metrics_tracker import metrics_tracker
from services.kendra_search import kendra_search_service
from services.kendra_indexer import kendra_indexer
from services.kendra_client import kendra_client

router = APIRouter()

# Setup templates
templates = Jinja2Templates(directory="templates")


def highlight_search_terms(text: str, query: str) -> str:
    """
    Highlight search terms in text by wrapping them in <mark> tags.
    
    Args:
        text: The text to highlight
        query: The search query (will be split into individual words)
        
    Returns:
        Text with search terms highlighted
    """
    if not text or not query:
        return text or ""
    
    # Split query into individual words (case-insensitive)
    query_words = re.findall(r'\b\w+\b', query.lower())
    if not query_words:
        return text
    
    # Create a pattern that matches whole words only (case-insensitive)
    pattern = '|'.join(re.escape(word) for word in query_words)
    pattern = r'\b(' + pattern + r')\b'
    
    # Replace matches with highlighted version
    def replace_func(match):
        return f'<mark style="background-color: #ffeb3b; padding: 2px 4px; border-radius: 3px;">{match.group(0)}</mark>'
    
    highlighted = re.sub(pattern, replace_func, text, flags=re.IGNORECASE)
    return highlighted


# Initialize services
file_store = FileStore()
version_manager = VersionManager(file_store)


# ============================================================================
# HTML Routes (for UI)
# ============================================================================

@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    state: Optional[str] = None,
    domain: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Dashboard showing all enabled monitored URLs with optional filtering."""
    from sqlalchemy import func, desc
    
    # Build query with filters
    query = db.query(MonitoredURL).filter(MonitoredURL.enabled == True)
    
    if state:
        query = query.filter(MonitoredURL.state == state)
    
    if domain:
        query = query.filter(MonitoredURL.domain_category == domain)
    
    urls = query.order_by(MonitoredURL.name).all()
    
    if not urls:
        url_data = []
    else:
        # Optimize: Use bulk queries instead of N+1 queries
        url_ids = [url.id for url in urls]
        
        # Get version counts for all URLs in one query
        version_counts = db.query(
            PDFVersion.monitored_url_id,
            func.count(PDFVersion.id).label('count')
        ).filter(
            PDFVersion.monitored_url_id.in_(url_ids)
        ).group_by(PDFVersion.monitored_url_id).all()
        
        version_count_map = {url_id: count for url_id, count in version_counts}
        
        # Get latest versions for all URLs using subquery approach (more compatible)
        # For each URL, get the max version_number, then join to get the full record
        max_versions_subq = db.query(
            PDFVersion.monitored_url_id,
            func.max(PDFVersion.version_number).label('max_version')
        ).filter(
            PDFVersion.monitored_url_id.in_(url_ids)
        ).group_by(PDFVersion.monitored_url_id).subquery()
        
        latest_versions = db.query(PDFVersion).join(
            max_versions_subq,
            (PDFVersion.monitored_url_id == max_versions_subq.c.monitored_url_id) &
            (PDFVersion.version_number == max_versions_subq.c.max_version)
        ).all()
        
        latest_version_map = {v.monitored_url_id: v for v in latest_versions}
        
        # Get most recent changes for all URLs using subquery approach
        max_changes_subq = db.query(
            ChangeLog.monitored_url_id,
            func.max(ChangeLog.detected_at).label('max_detected_at')
        ).filter(
            ChangeLog.monitored_url_id.in_(url_ids)
        ).group_by(ChangeLog.monitored_url_id).subquery()
        
        recent_changes = db.query(ChangeLog).join(
            max_changes_subq,
            (ChangeLog.monitored_url_id == max_changes_subq.c.monitored_url_id) &
            (ChangeLog.detected_at == max_changes_subq.c.max_detected_at)
        ).all()
        
        recent_change_map = {c.monitored_url_id: c for c in recent_changes}
        
        # Build url_data list
        url_data = []
        for url in urls:
            url_data.append({
                "url": url,
                "version_count": version_count_map.get(url.id, 0),
                "latest_version": latest_version_map.get(url.id),
                "recent_change": recent_change_map.get(url.id)
            })
    
    # Get state counts for tabs
    state_counts = db.query(
        MonitoredURL.state,
        func.count(MonitoredURL.id).label('count')
    ).filter(
        MonitoredURL.enabled == True,
        MonitoredURL.state.isnot(None)
    ).group_by(MonitoredURL.state).order_by(func.count(MonitoredURL.id).desc()).all()
    
    # Get domain counts for grouping (only for current state filter)
    domain_query = db.query(
        MonitoredURL.domain_category,
        func.count(MonitoredURL.id).label('count')
    ).filter(
        MonitoredURL.enabled == True,
        MonitoredURL.domain_category.isnot(None)
    )
    if state:
        domain_query = domain_query.filter(MonitoredURL.state == state)
    domain_counts = domain_query.group_by(
        MonitoredURL.domain_category
    ).order_by(func.count(MonitoredURL.id).desc()).all()
    
    # Total count across all states
    total_count = db.query(MonitoredURL).filter(MonitoredURL.enabled == True).count()
    
    # Check for query parameters
    query_params = request.query_params
    message = None
    message_type = None
    
    if "error" in query_params:
        import urllib.parse
        message = urllib.parse.unquote(query_params["error"])
        message_type = "error"
    elif "refresh" in query_params:
        message = "Monitoring cycle completed successfully!"
        message_type = "success"
    
    response = templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "urls": url_data,
            "now": datetime.utcnow(),
            "message": message,
            "message_type": message_type,
            "current_state": state,
            "current_domain": domain,
            "state_counts": state_counts,
            "domain_counts": domain_counts,
            "total_count": total_count
        }
    )
    # Prevent caching
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


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
            "url_name": url.name if url else "Unknown",
            "url_url": url.url if url else ""
        })
    
    return templates.TemplateResponse(
        "changes.html",
        {
            "request": request,
            "changes": change_data,
            "now": datetime.utcnow()
        }
    )


@router.get("/triage", response_class=HTMLResponse)
async def triage_dashboard(
    request: Request,
    status: str = "all",
    action: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    AI Triage Dashboard - Screen 1 from PoC.
    Shows changes with AI recommendations and allows bulk approval/rejection.
    """
    # Build query
    query = db.query(ChangeLog).join(
        MonitoredURL, ChangeLog.monitored_url_id == MonitoredURL.id
    ).filter(MonitoredURL.enabled == True)
    
    # Filter by review status
    if status != "all":
        query = query.filter(ChangeLog.review_status == status)
    
    # Filter by recommended action
    if action:
        query = query.filter(ChangeLog.recommended_action == action)
    
    # Order by priority (manual_required first, then new_form, etc.)
    changes = query.order_by(ChangeLog.detected_at.desc()).limit(100).all()
    
    # Calculate priority based on recommended action
    priority_map = {
        "manual_required": 1,
        "new_form": 2,
        "review_suggested": 3,
        "auto_approve": 4,
        "false_positive": 5,
    }
    
    # Enrich with URL names and priorities
    change_data = []
    for change in changes:
        url = db.query(MonitoredURL).filter(
            MonitoredURL.id == change.monitored_url_id
        ).first()
        
        change_data.append({
            "change": change,
            "url_name": url.name if url else "Unknown",
            "url_url": url.url if url else "",
            "priority": priority_map.get(change.recommended_action, 3)
        })
    
    # Sort by priority
    change_data.sort(key=lambda x: (x["priority"], x["change"].detected_at), reverse=False)
    
    # Calculate stats
    total_changes = db.query(ChangeLog).join(
        MonitoredURL, ChangeLog.monitored_url_id == MonitoredURL.id
    ).filter(MonitoredURL.enabled == True).count()
    
    pending_count = db.query(ChangeLog).join(
        MonitoredURL, ChangeLog.monitored_url_id == MonitoredURL.id
    ).filter(
        MonitoredURL.enabled == True,
        ChangeLog.review_status == "pending"
    ).count()
    
    approved_count = db.query(ChangeLog).join(
        MonitoredURL, ChangeLog.monitored_url_id == MonitoredURL.id
    ).filter(
        MonitoredURL.enabled == True,
        ChangeLog.review_status.in_(["approved", "auto_approved"])
    ).count()
    
    auto_approved_count = db.query(ChangeLog).join(
        MonitoredURL, ChangeLog.monitored_url_id == MonitoredURL.id
    ).filter(
        MonitoredURL.enabled == True,
        ChangeLog.recommended_action.in_(["auto_approve", "false_positive"])
    ).count()
    
    manual_required_count = db.query(ChangeLog).join(
        MonitoredURL, ChangeLog.monitored_url_id == MonitoredURL.id
    ).filter(
        MonitoredURL.enabled == True,
        ChangeLog.recommended_action.in_(["manual_required", "new_form"])
    ).count()
    
    automation_rate = auto_approved_count / total_changes if total_changes > 0 else 0
    review_rate = manual_required_count / total_changes if total_changes > 0 else 0
    
    stats = {
        "total": total_changes,
        "pending": pending_count,
        "approved": approved_count,
        "automation_rate": automation_rate,
        "review_rate": review_rate,
    }
    
    return templates.TemplateResponse(
        "triage.html",
        {
            "request": request,
            "changes": change_data,
            "stats": stats,
            "current_filter": status,
            "action_filter": action,
            "now": datetime.utcnow()
        }
    )


@router.get("/search", response_class=HTMLResponse)
async def search_page(
    request: Request,
    q: Optional[str] = None,
    state: Optional[str] = None,
    domain: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Search page with results."""
    error = None
    results = None
    total_results = 0
    
    if q:
        response = kendra_search_service.search(
            db=db,
            query=q,
            state=state,
            domain=domain,
            max_results=50
        )
        
        if not response.success:
            error = response.error
        else:
            results = []
            for result in response.results:
                # Truncate excerpt first, then highlight (to avoid cutting HTML tags)
                excerpt_to_highlight = (result.excerpt or "")[:300]
                if result.excerpt and len(result.excerpt) > 300:
                    excerpt_to_highlight += "..."
                
                # Highlight search terms in excerpt and title
                highlighted_excerpt = highlight_search_terms(excerpt_to_highlight, q) if excerpt_to_highlight else None
                highlighted_title = highlight_search_terms(result.title or "", q) if result.title else None
                
                results.append({
                    "url_id": result.url_id,
                    "version_id": result.version_id,
                    "url_name": result.url_name,
                    "url": result.url,
                    "form_number": result.form_number,
                    "title": result.title,  # Keep original for links
                    "title_highlighted": highlighted_title,  # Highlighted version for display
                    "excerpt": result.excerpt,  # Keep original for reference
                    "excerpt_highlighted": highlighted_excerpt,  # Highlighted and truncated version for display
                    "relevance_score": result.relevance_score,
                    "state": result.state,
                    "domain_category": result.domain_category
                })
            total_results = response.total_results
    
    return templates.TemplateResponse(
        "search.html",
        {
            "request": request,
            "query": q,
            "state": state,
            "domain": domain,
            "results": results,
            "total_results": total_results,
            "error": error,
            "now": datetime.utcnow()
        }
    )


@router.get("/metrics", response_class=HTMLResponse)
async def metrics_dashboard(request: Request, db: Session = Depends(get_db)):
    """
    Metrics Dashboard - Shows KPIs and trends.
    Replaces manual spreadsheet tracking per PoC Section 5.
    """
    # Get dashboard metrics
    metrics = metrics_tracker.get_dashboard_metrics(db)
    
    # Get AI accuracy report
    accuracy = metrics_tracker.get_ai_accuracy(db, days=30)
    
    return templates.TemplateResponse(
        "metrics.html",
        {
            "request": request,
            "metrics": metrics,
            "accuracy": accuracy,
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
    Redirects back to dashboard after completion with cache-busting.
    """
    from cli import MonitoringOrchestrator
    from datetime import datetime
    
    try:
        orchestrator = MonitoringOrchestrator()
        results = orchestrator.run_cycle(db, url_id, max_workers=None)  # Uses config default
        
        # Redirect back to dashboard with timestamp to force refresh
        timestamp = int(datetime.utcnow().timestamp())
        return RedirectResponse(url=f"/?refresh={timestamp}", status_code=303)
    except Exception as e:
        # On error, redirect with error message
        import urllib.parse
        error_msg = urllib.parse.quote(str(e))
        return RedirectResponse(url=f"/?error={error_msg}", status_code=303)


# ============================================================================
# API Routes
# ============================================================================

@router.get("/api/urls", response_model=list[MonitoredURLResponse])
async def list_urls(
    state: Optional[str] = None,
    domain: Optional[str] = None,
    enabled_only: bool = True,
    db: Session = Depends(get_db)
):
    """
    List all monitored URLs with optional filtering.
    
    Args:
        state: Filter by state (e.g., "California", "Alaska")
        domain: Filter by domain category (e.g., "courts.ca.gov")
        enabled_only: Only return enabled URLs (default: True)
    """
    query = db.query(MonitoredURL)
    
    if enabled_only:
        query = query.filter(MonitoredURL.enabled == True)
    
    if state:
        query = query.filter(MonitoredURL.state == state)
    
    if domain:
        query = query.filter(MonitoredURL.domain_category == domain)
    
    urls = query.order_by(MonitoredURL.name).all()
    
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


@router.get("/api/url-filters")
async def get_url_filters(db: Session = Depends(get_db)):
    """
    Get available filters for URLs (states and domain categories).
    
    Returns:
        Dictionary with states and domains lists, each with count
    """
    from sqlalchemy import func
    
    # Get states with counts
    state_counts = db.query(
        MonitoredURL.state,
        func.count(MonitoredURL.id).label('count')
    ).filter(
        MonitoredURL.enabled == True,
        MonitoredURL.state.isnot(None)
    ).group_by(MonitoredURL.state).order_by(func.count(MonitoredURL.id).desc()).all()
    
    # Get domains with counts
    domain_counts = db.query(
        MonitoredURL.domain_category,
        func.count(MonitoredURL.id).label('count')
    ).filter(
        MonitoredURL.enabled == True,
        MonitoredURL.domain_category.isnot(None)
    ).group_by(MonitoredURL.domain_category).order_by(func.count(MonitoredURL.id).desc()).all()
    
    return {
        "states": [{"name": s, "count": c} for s, c in state_counts if s],
        "domains": [{"name": d, "count": c} for d, c in domain_counts if d]
    }


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
    # Both normalized and original now return the same file (original PDF)
    # normalized parameter kept for backward compatibility
    pdf_path = version_manager.get_original_pdf_path(db, version_id)
    
    if not pdf_path or not pdf_path.exists():
        raise HTTPException(status_code=404, detail="PDF not found")
    
    return FileResponse(
        pdf_path,
        media_type="application/pdf",
        filename=f"version_{version_id}_original.pdf"
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
    pdf_path = version_manager.get_original_pdf_path(db, version_id)
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
    page: int = 0,
    view: str = "overlay",
    db: Session = Depends(get_db)
):
    """
    Get the visual diff preview image for a version.
    Shows changes from the previous version with yellow highlighting.
    
    Args:
        url_id: Monitored URL ID
        version_id: Version ID
        page: Page number (0-indexed). Defaults to 0 (first page).
        view: View mode - "overlay" (default) or "side-by-side"
    """
    # Validate page number
    if page < 0:
        raise HTTPException(status_code=400, detail="Page number must be >= 0")
    
    # Validate view parameter
    if view not in ["overlay", "side-by-side"]:
        raise HTTPException(status_code=400, detail="View must be 'overlay' or 'side-by-side'")
    
    # Determine which file store method to use based on view mode
    if view == "side-by-side":
        # For side-by-side, construct path similar to diff image but with sidebyside suffix
        version_dir = file_store.get_version_dir(url_id, version_id)
        if page == 0:
            diff_path = version_dir / "diff_sidebyside.png"
        else:
            diff_path = version_dir / f"diff_sidebyside_page_{page}.png"
        # Check if it exists
        if not diff_path.exists():
            diff_path = None
    else:
        # First try to get existing diff image for this page
        diff_path = file_store.get_diff_image(url_id, version_id, page)
    
    if diff_path and diff_path.exists():
        return FileResponse(
            diff_path,
            media_type="image/png",
            filename=f"diff_{version_id}_page_{page}.png"
        )
    
    # If no diff exists, try to generate it
    version = db.query(PDFVersion).filter(
        PDFVersion.id == version_id,
        PDFVersion.monitored_url_id == url_id
    ).first()
    
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")
    
    # Validate page number against PDF page count
    if version.page_count and page >= version.page_count:
        raise HTTPException(
            status_code=400, 
            detail=f"Page number {page} is out of range. PDF has {version.page_count} pages (0-{version.page_count - 1})"
        )
    
    # Find previous version
    prev_version = db.query(PDFVersion).filter(
        PDFVersion.monitored_url_id == url_id,
        PDFVersion.version_number < version.version_number
    ).order_by(PDFVersion.version_number.desc()).first()
    
    if not prev_version:
        raise HTTPException(status_code=404, detail="No previous version to compare against")
    
    # Get PDF paths (use original PDFs)
    curr_pdf = version_manager.get_original_pdf_path(db, version_id)
    prev_pdf = version_manager.get_original_pdf_path(db, prev_version.id)
    
    if not curr_pdf or not prev_pdf:
        raise HTTPException(status_code=404, detail="PDF files not found")
    
    try:
        from services.visual_diff import VisualDiff
        differ = VisualDiff()
        
        if view == "side-by-side":
            # Generate side-by-side comparison
            # Construct the output path
            version_dir = file_store.get_version_dir(url_id, version_id)
            if page == 0:
                diff_output = version_dir / "diff_sidebyside.png"
            else:
                diff_output = version_dir / f"diff_sidebyside_page_{page}.png"
            result = differ.generate_side_by_side(
                old_pdf_path=prev_pdf,
                new_pdf_path=curr_pdf,
                output_path=diff_output,
                page_num=page
            )
        else:
            # Generate overlay diff
            diff_output = file_store.get_diff_image_path(url_id, version_id, page)
            result = differ.generate_diff(
                old_pdf_path=prev_pdf,
                new_pdf_path=curr_pdf,
                output_path=diff_output,
                page_num=page
            )
        
        if result.success and result.diff_image_path.exists():
            return FileResponse(
                result.diff_image_path,
                media_type="image/png",
                filename=f"diff_{version_id}_page_{page}.png"
            )
        else:
            raise HTTPException(status_code=500, detail=f"Failed to generate diff: {result.error}")
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating diff: {str(e)}")


@router.get("/api/urls/{url_id}/versions/{version_id}/diff-info")
async def get_diff_info(
    url_id: int,
    version_id: int,
    db: Session = Depends(get_db)
):
    """
    Get information about the diff for a version (page count, affected pages).
    
    Returns:
        JSON with page_count and affected_pages
    """
    version = db.query(PDFVersion).filter(
        PDFVersion.id == version_id,
        PDFVersion.monitored_url_id == url_id
    ).first()
    
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")
    
    # Find the change log entry for this version
    change = db.query(ChangeLog).filter(
        ChangeLog.new_version_id == version_id,
        ChangeLog.monitored_url_id == url_id
    ).order_by(ChangeLog.detected_at.desc()).first()
    
    affected_pages = change.affected_pages if change and change.affected_pages else []
    
    return {
        "page_count": version.page_count or 0,
        "affected_pages": affected_pages,
        "version_id": version_id
    }


@router.post("/api/urls/{url_id}/versions/{version_id}/extract-title")
async def extract_title_for_version(
    url_id: int,
    version_id: int,
    db: Session = Depends(get_db)
):
    """
    Manually trigger title extraction for a specific version.
    Uses AWS BDA to extract title and form number.
    """
    from services.title_extractor import TitleExtractor
    
    # Get the version
    version = db.query(PDFVersion).filter(PDFVersion.id == version_id).first()
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")
    
    # Get PDF path (use original PDF)
    pdf_path = version_manager.get_original_pdf_path(db, version_id)
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
    # Replace URL display name with BDA-extracted title
    if result.formatted_title:
        url = version.monitored_url if hasattr(version, "monitored_url") else db.query(MonitoredURL).filter(MonitoredURL.id == version.monitored_url_id).first()
        if url:
            bda_display = (
                f"{result.formatted_title} {{{result.form_number}}}"
                if result.form_number
                else result.formatted_title
            )
            url.name = bda_display[:255]
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
    change.review_status = "pending"
    db.commit()
    
    return {
        "success": True,
        "change_id": change_id,
        "reviewed": False
    }


# ============================================================================
# Triage API Routes (REQ-013, REQ-014)
# ============================================================================

@router.post("/api/triage/review/{change_id}", response_model=ReviewResponse)
async def review_change(
    change_id: int,
    review: ReviewRequest,
    db: Session = Depends(get_db)
):
    """
    Submit a review for a single change.
    Actions: approved, rejected, deferred
    """
    change = db.query(ChangeLog).filter(ChangeLog.id == change_id).first()
    if not change:
        raise HTTPException(status_code=404, detail="Change not found")
    
    # Validate action
    valid_actions = ["approved", "rejected", "deferred"]
    if review.action not in valid_actions:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid action. Must be one of: {valid_actions}"
        )
    
    # Update change
    change.review_status = review.action
    change.reviewed = True
    change.reviewed_at = datetime.utcnow()
    change.reviewed_by = review.reviewed_by or "system"
    change.review_notes = review.notes
    db.commit()
    
    return ReviewResponse(
        success=True,
        change_id=change_id,
        review_status=change.review_status,
        reviewed_at=change.reviewed_at,
        message=f"Change {review.action} successfully"
    )


@router.post("/api/triage/bulk-review", response_model=BulkReviewResponse)
async def bulk_review_changes(
    request: BulkReviewRequest,
    db: Session = Depends(get_db)
):
    """
    Bulk review multiple changes at once.
    Used by the triage dashboard for batch approval/rejection.
    """
    # Validate action
    valid_actions = ["approved", "rejected", "deferred"]
    if request.action not in valid_actions:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid action. Must be one of: {valid_actions}"
        )
    
    processed = 0
    failed = 0
    details = []
    
    for change_id in request.change_ids:
        change = db.query(ChangeLog).filter(ChangeLog.id == change_id).first()
        
        if not change:
            failed += 1
            details.append({
                "change_id": change_id,
                "success": False,
                "error": "Change not found"
            })
            continue
        
        try:
            change.review_status = request.action
            change.reviewed = True
            change.reviewed_at = datetime.utcnow()
            change.reviewed_by = request.reviewed_by or "bulk_action"
            change.review_notes = request.notes
            
            processed += 1
            details.append({
                "change_id": change_id,
                "success": True,
                "status": request.action
            })
        except Exception as e:
            failed += 1
            details.append({
                "change_id": change_id,
                "success": False,
                "error": str(e)
            })
    
    db.commit()
    
    return BulkReviewResponse(
        success=failed == 0,
        processed=processed,
        failed=failed,
        details=details
    )


@router.post("/api/triage/override/{change_id}")
async def override_classification(
    change_id: int,
    override: ClassificationOverrideRequest,
    db: Session = Depends(get_db)
):
    """
    Override AI classification for a change.
    Used when human reviewer disagrees with AI classification.
    """
    change = db.query(ChangeLog).filter(ChangeLog.id == change_id).first()
    if not change:
        raise HTTPException(status_code=404, detail="Change not found")
    
    # Valid classifications per PoC
    valid_classifications = [
        "new_form",
        "updated_same_name",
        "updated_name_change",
        "false_positive",
        "deprecated"
    ]
    
    if override.classification not in valid_classifications:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid classification. Must be one of: {valid_classifications}"
        )
    
    # Store override
    change.classification_override = override.classification
    change.override_reason = override.reason
    change.reviewed_by = override.overridden_by or "system"
    change.reviewed_at = datetime.utcnow()
    db.commit()
    
    return {
        "success": True,
        "change_id": change_id,
        "original_classification": change.match_type,
        "new_classification": override.classification,
        "reason": override.reason
    }


@router.post("/api/triage/auto-approve-eligible")
async def auto_approve_eligible(db: Session = Depends(get_db)):
    """
    Auto-approve all changes that meet the auto-approve threshold.
    Used for batch processing of high-confidence changes.
    """
    from config import settings
    
    # Find all pending changes with auto_approve recommendation
    eligible = db.query(ChangeLog).filter(
        ChangeLog.review_status == "pending",
        ChangeLog.recommended_action == "auto_approve"
    ).all()
    
    approved_count = 0
    for change in eligible:
        change.review_status = "auto_approved"
        change.reviewed = True
        change.reviewed_at = datetime.utcnow()
        change.reviewed_by = "auto_approve_system"
        change.review_notes = f"Automatically approved (confidence >= {settings.AUTO_APPROVE_THRESHOLD})"
        approved_count += 1
    
    db.commit()
    
    return {
        "success": True,
        "auto_approved_count": approved_count,
        "message": f"Auto-approved {approved_count} changes"
    }


@router.post("/api/triage/approve-all-pending")
async def approve_all_pending(db: Session = Depends(get_db)):
    """
    Approve all pending changes.
    """
    # Find all pending changes
    pending = db.query(ChangeLog).filter(
        ChangeLog.review_status == "pending"
    ).all()
    
    approved_count = 0
    for change in pending:
        change.review_status = "approved"
        change.reviewed = True
        change.reviewed_at = datetime.utcnow()
        change.reviewed_by = "system_bulk_approval"
        change.review_notes = "Bulk approved all pending changes"
        approved_count += 1
    
    db.commit()
    
    return {
        "success": True,
        "approved_count": approved_count,
        "message": f"Approved {approved_count} pending changes"
    }


@router.post("/api/triage/dismiss-false-positives")
async def dismiss_false_positives(db: Session = Depends(get_db)):
    """
    Auto-dismiss all format-only changes (false positives).
    """
    # Find all pending format-only changes
    false_positives = db.query(ChangeLog).filter(
        ChangeLog.review_status == "pending",
        ChangeLog.change_type == "format_only"
    ).all()
    
    dismissed_count = 0
    for change in false_positives:
        change.review_status = "auto_approved"
        change.reviewed = True
        change.reviewed_at = datetime.utcnow()
        change.reviewed_by = "false_positive_dismisser"
        change.review_notes = "Format-only change auto-dismissed (no semantic content change)"
        change.recommended_action = "false_positive"
        dismissed_count += 1
    
    db.commit()
    
    return {
        "success": True,
        "dismissed_count": dismissed_count,
        "message": f"Dismissed {dismissed_count} format-only changes"
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
    results = orchestrator.run_cycle(db, request.url_id, max_workers=None)  # Uses config default
    
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


@router.get("/api/aws-calls")
async def get_aws_calls():
    """Get AWS API call counts."""
    from services.api_counter import api_counter
    return api_counter.get_stats()


# ============================================================================
# Metrics API Routes (PoC Section 5)
# ============================================================================

@router.get("/api/metrics")
async def get_metrics(
    period: str = "monthly",
    year: Optional[int] = None,
    month: Optional[int] = None,
    db: Session = Depends(get_db)
):
    """
    Get metrics for reporting.
    
    Args:
        period: "monthly", "yearly", or "all"
        year: Specific year (defaults to current)
        month: Specific month (defaults to current)
    """
    now = datetime.utcnow()
    year = year or now.year
    month = month or now.month
    
    if period == "monthly":
        stats = metrics_tracker.get_monthly_stats(db, year, month)
        return stats.to_dict()
    
    elif period == "dashboard":
        metrics = metrics_tracker.get_dashboard_metrics(db)
        accuracy = metrics_tracker.get_ai_accuracy(db, days=30)
        return {
            "metrics": metrics.to_dict(),
            "accuracy": accuracy.to_dict()
        }
    
    else:
        # Return dashboard metrics by default
        metrics = metrics_tracker.get_dashboard_metrics(db)
        return metrics.to_dict()


@router.get("/api/metrics/accuracy")
async def get_accuracy_metrics(
    days: int = 30,
    db: Session = Depends(get_db)
):
    """Get AI prediction accuracy metrics."""
    accuracy = metrics_tracker.get_ai_accuracy(db, days=days)
    return accuracy.to_dict()


@router.get("/api/metrics/jurisdiction")
async def get_jurisdiction_metrics(
    year: Optional[int] = None,
    month: Optional[int] = None,
    db: Session = Depends(get_db)
):
    """
    Get per-jurisdiction breakdown of metrics.
    Replaces manual Forms Monitoring Spreadsheet.
    """
    now = datetime.utcnow()
    year = year or now.year
    month = month or now.month
    
    breakdown = metrics_tracker.get_jurisdiction_breakdown(db, year, month)
    return {
        "period": f"{year}-{month:02d}",
        "jurisdictions": breakdown
    }


# ============================================================================
# Kendra Search API Routes
# ============================================================================

@router.get("/api/search")
async def search_forms(
    q: str,
    state: Optional[str] = None,
    domain: Optional[str] = None,
    max_results: int = 20,
    db: Session = Depends(get_db)
):
    """
    Semantic search across all monitored forms using AWS Kendra.
    
    Args:
        q: Natural language search query
        state: Optional state filter
        domain: Optional domain category filter
        max_results: Maximum number of results (default: 20)
        
    Returns:
        JSON with search results
    """
    if not kendra_search_service.is_enabled():
        raise HTTPException(
            status_code=503,
            detail="Kendra search is not enabled or not available. Check AWS credentials and KENDRA_SEARCH_ENABLED setting."
        )
    
    response = kendra_search_service.search(
        db=db,
        query=q,
        state=state,
        domain=domain,
        max_results=max_results
    )
    
    if not response.success:
        raise HTTPException(
            status_code=500,
            detail=response.error
        )
    
    # Convert results to dict format
    results = []
    for result in response.results:
        results.append({
            "url_id": result.url_id,
            "version_id": result.version_id,
            "url_name": result.url_name,
            "url": result.url,
            "form_number": result.form_number,
            "title": result.title,
            "excerpt": result.excerpt,
            "relevance_score": result.relevance_score,
            "state": result.state,
            "domain_category": result.domain_category
        })
    
    return {
        "success": True,
        "query": q,
        "total_results": response.total_results,
        "results": results
    }


@router.get("/api/urls/{url_id}/similar")
async def get_similar_forms(
    url_id: int,
    version_id: Optional[int] = None,
    max_results: int = 10,
    db: Session = Depends(get_db)
):
    """
    Find similar forms to a given form using Kendra.
    
    Args:
        url_id: Monitored URL ID
        version_id: Optional version ID (uses latest if not provided)
        max_results: Maximum number of similar forms to return
        
    Returns:
        JSON with similar forms
    """
    if not kendra_search_service.is_enabled():
        raise HTTPException(
            status_code=503,
            detail="Kendra search is not enabled or not available"
        )
    
    response = kendra_search_service.find_similar_forms(
        db=db,
        url_id=url_id,
        version_id=version_id,
        max_results=max_results
    )
    
    if not response.success:
        raise HTTPException(
            status_code=500,
            detail=response.error
        )
    
    # Convert results to dict format
    results = []
    for result in response.results:
        results.append({
            "url_id": result.url_id,
            "version_id": result.version_id,
            "url_name": result.url_name,
            "url": result.url,
            "form_number": result.form_number,
            "title": result.title,
            "excerpt": result.excerpt,
            "relevance_score": result.relevance_score,
            "state": result.state,
            "domain_category": result.domain_category
        })
    
    return {
        "success": True,
        "url_id": url_id,
        "version_id": version_id,
        "total_results": response.total_results,
        "results": results
    }


@router.post("/api/kendra/index/{version_id}")
async def index_version(
    version_id: int,
    force: bool = False,
    db: Session = Depends(get_db)
):
    """
    Manually trigger Kendra indexing for a specific version.
    
    Args:
        version_id: PDF version ID to index
        force: If True, re-index even if already indexed
        
    Returns:
        JSON with indexing result
    """
    if not kendra_indexer.is_enabled():
        raise HTTPException(
            status_code=503,
            detail="Kendra indexing is not enabled or not available"
        )
    
    result = kendra_indexer.index_version(db, version_id, force=force)
    
    if not result.success:
        raise HTTPException(
            status_code=500,
            detail=result.error
        )
    
    return {
        "success": True,
        "version_id": version_id,
        "document_id": result.document_id
    }


@router.get("/api/kendra/status")
async def get_kendra_status():
    """
    Get Kendra index status and configuration.
    
    Returns:
        JSON with Kendra status information
    """
    status = kendra_client.get_index_status()
    
    return {
        "kendra_available": kendra_client.is_available(),
        "indexing_enabled": settings.KENDRA_INDEXING_ENABLED,
        "search_enabled": settings.KENDRA_SEARCH_ENABLED,
        "index_status": status
    }

