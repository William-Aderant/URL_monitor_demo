#!/usr/bin/env python3
"""
CLI runner for PDF monitoring.

Provides command-line interface for:
- Running monitoring cycles
- Initializing database
- Seeding sample URLs
- Checking URL status

Usage:
    python cli.py init          # Initialize database
    python cli.py seed          # Seed sample URLs
    python cli.py run           # Run monitoring cycle
    python cli.py run --url-id 1  # Monitor specific URL
    python cli.py status        # Show status of all URLs
"""

import argparse
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import structlog

# Configure logging before imports
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer()
    ]
)

logger = structlog.get_logger()

from config import settings
from db.database import get_db, init_db, SessionLocal
from db.models import MonitoredURL, PDFVersion, ChangeLog
from db.migrations import run_migrations, seed_sample_urls
from fetcher.aws_web_scraper import AWSWebScraper
from fetcher.pdf_downloader import PDFDownloader
from pdf_processing.text_extractor import TextExtractor
from pdf_processing.ocr_fallback import OCRFallback
from diffing.hasher import Hasher
from diffing.change_detector import ChangeDetector, ChangeResult
from storage.version_manager import VersionManager
from services.title_extractor import TitleExtractor
from services.link_crawler import LinkCrawler
from services.form_matcher import FormMatcher, MatchType
from services.visual_diff import VisualDiff
from services.action_recommender import action_recommender
from services.kendra_indexer import kendra_indexer
from services.kendra_client import kendra_client
from fetcher.header_checker import HeaderChecker
from diffing.quick_hasher import QuickHasher


def verify_aws_features() -> List[str]:
    """
    Verify all configured AWS features are reachable and working.
    Run before a monitoring cycle to avoid bad runs when AWS is down.

    Returns:
        List of error messages. Empty means all checks passed.
    """
    errors: List[str] = []
    aws_configured = (
        bool(settings.AWS_LAMBDA_SCRAPER_FUNCTION)
        or settings.KENDRA_INDEXING_ENABLED
        or (bool(settings.AWS_ACCESS_KEY_ID) and bool(settings.AWS_SECRET_ACCESS_KEY))
    )
    if not aws_configured:
        return []

    import boto3
    from botocore.exceptions import BotoCoreError, ClientError

    # 1. AWS credentials / connectivity (STS)
    try:
        sts_kwargs = {"region_name": settings.AWS_REGION}
        if settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY:
            sts_kwargs["aws_access_key_id"] = settings.AWS_ACCESS_KEY_ID
            sts_kwargs["aws_secret_access_key"] = settings.AWS_SECRET_ACCESS_KEY
        sts = boto3.client("sts", **sts_kwargs)
        sts.get_caller_identity()
    except (BotoCoreError, ClientError, Exception) as e:
        errors.append(f"AWS credentials/connectivity failed: {e}")
        # If we can't reach AWS at all, skip per-service checks to avoid duplicate noise
        return errors

    # 2. Lambda scraper (if configured)
    if settings.AWS_LAMBDA_SCRAPER_FUNCTION:
        try:
            lambda_kwargs = {"region_name": settings.AWS_REGION}
            if settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY:
                lambda_kwargs["aws_access_key_id"] = settings.AWS_ACCESS_KEY_ID
                lambda_kwargs["aws_secret_access_key"] = settings.AWS_SECRET_ACCESS_KEY
            lam = boto3.client("lambda", **lambda_kwargs)
            lam.get_function(FunctionName=settings.AWS_LAMBDA_SCRAPER_FUNCTION)
        except (BotoCoreError, ClientError, Exception) as e:
            errors.append(f"AWS Lambda scraper not reachable ({settings.AWS_LAMBDA_SCRAPER_FUNCTION}): {e}")

    # 3. Kendra (if indexing enabled)
    if settings.KENDRA_INDEXING_ENABLED:
        if not kendra_client.is_available():
            errors.append(
                "Kendra indexing is enabled but Kendra client is not available. "
                "Check AWS credentials and AWS_KENDRA_INDEX_ID."
            )
        else:
            status = kendra_client.get_index_status()
            if not status.get("available"):
                errors.append(
                    f"Kendra index not available: {status.get('error', 'Unknown error')}"
                )

    return errors


class MonitoringOrchestrator:
    """
    Orchestrates the PDF monitoring pipeline.
    
    Pipeline:
    1. Fetch PDF from URL
    2. Extract text (from original PDF)
    3. Compute hashes (from original PDF)
    4. Compare with previous version (with early termination)
    5. If change detected: Run OCR (if needed), extract title, generate visual diff
    6. Store new version and record changes
    """
    
    def __init__(self):
        """Initialize orchestrator with all required components."""
        import os
        
        self.aws_scraper = None  # Lazy init
        self.downloader = PDFDownloader()
        self.text_extractor = TextExtractor()
        self.ocr_fallback = OCRFallback()
        self.hasher = Hasher()
        self.change_detector = ChangeDetector()
        self.version_manager = VersionManager()
        
        # Title extraction: Use Nova 2 Lite if enabled, otherwise Textract+Claude
        if os.getenv("BEDROCK_NOVA_ENABLED", "False").lower() == "true":
            from services.nova_document_processor import NovaDocumentProcessor
            self.title_extractor = NovaDocumentProcessor()
            logger.info("Using Nova 2 Lite for title extraction")
        else:
            self.title_extractor = TitleExtractor()
            logger.info("Using Textract+Claude for title extraction")
        
        # Enhanced change detection services
        self.link_crawler = LinkCrawler()
        self.form_matcher = FormMatcher()
        self.visual_diff = VisualDiff()
        
        # Fast change detection services (three-tier)
        self.header_checker = HeaderChecker()
        self.quick_hasher = QuickHasher()
        
        logger.info("MonitoringOrchestrator initialized")
    
    def _get_aws_scraper(self) -> AWSWebScraper:
        """Lazy-load AWS web scraper client."""
        if self.aws_scraper is None:
            self.aws_scraper = AWSWebScraper()
        return self.aws_scraper
    
    def process_url(self, db, monitored_url: MonitoredURL) -> bool:
        """
        Process a single monitored URL.
        
        Args:
            db: Database session
            monitored_url: MonitoredURL to process
            
        Returns:
            True if successful, False otherwise
        """
        logger.info(
            "Processing URL",
            url_id=monitored_url.id,
            name=monitored_url.name,
            url=monitored_url.url
        )
        
        # #region agent log
        import json
        try:
            with open('/Users/william.holden/Documents/GitHub/URL_monitor_demo/.cursor/debug.log', 'a') as f:
                f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"A","location":"cli.py:process_url","message":"Processing URL","data":{"url_id":monitored_url.id,"url":monitored_url.url,"name":monitored_url.name},"timestamp":int(__import__('time').time()*1000)})+'\n')
        except: pass
        # #endregion
        
        relocated_from_url = None  # Track if form was found at different URL
        
        try:
            # Step 1: Fetch PDF
            pdf_url = monitored_url.url
            
            # If URL is not a direct PDF, use AWS web scraper to find PDF link
            if not pdf_url.lower().endswith('.pdf'):
                logger.info("URL is not direct PDF, scraping for PDF link")
                aws_scraper = self._get_aws_scraper()
                scrape_result = aws_scraper.scrape_url(monitored_url.url)
                
                if not scrape_result.success:
                    logger.error(
                        "Failed to scrape URL",
                        url=monitored_url.url,
                        error=scrape_result.error
                    )
                    return False
                
                if not scrape_result.pdf_url:
                    logger.error("No PDF link found in page", url=monitored_url.url)
                    return False
                
                pdf_url = scrape_result.pdf_url
            
            # ========================================================================
            # TIER 1: Fast HTTP Header Check (skip download if headers match)
            # ========================================================================
            header_result = self.header_checker.check_headers(
                url=pdf_url,
                previous_last_modified=monitored_url.last_modified_header,
                previous_etag=monitored_url.etag_header,
                previous_content_length=monitored_url.content_length_header
            )
            
            if header_result.success and self.header_checker.can_skip_download(header_result):
                # Headers match - high confidence no change, skip processing
                logger.info(
                    "Headers indicate no change - skipping download",
                    url_id=monitored_url.id,
                    url=pdf_url
                )
                # #region agent log
                try:
                    with open('/Users/william.holden/Documents/GitHub/URL_monitor_demo/.cursor/debug.log', 'a') as f:
                        f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"A","location":"cli.py:process_url","message":"Skipped by header check","data":{"url_id":monitored_url.id,"url":pdf_url},"timestamp":int(__import__('time').time()*1000)})+'\n')
                except: pass
                # #endregion
                print(f"\n  ✓ No change detected (HTTP headers match)")
                
                # Update last checked timestamp
                monitored_url.last_checked_at = datetime.utcnow()
                db.commit()
                return True
            
            # ========================================================================
            # TIER 2: Quick Hash Check (download first 64KB only)
            # ========================================================================
            quick_hash_result = None
            if not header_result.success or header_result.likely_changed is None:
                # Headers unavailable or inconclusive - try quick hash
                logger.info("Headers inconclusive, checking quick hash", url=pdf_url)
                
                quick_hash_result = self.quick_hasher.compute_quick_hash(pdf_url)
                
                if quick_hash_result.success and quick_hash_result.quick_hash:
                    # Compare with stored quick hash
                    stored_hash = monitored_url.quick_hash
                    current_hash = quick_hash_result.quick_hash
                    
                    logger.debug(
                        "Quick hash comparison",
                        url_id=monitored_url.id,
                        stored_hash=stored_hash[:16] + "..." if stored_hash else None,
                        current_hash=current_hash[:16] + "..."
                    )
                    
                    if self.quick_hasher.compare_quick_hash(current_hash, stored_hash):
                        # Quick hash matches - high confidence no change
                        logger.info(
                            "Quick hash matches - skipping full download",
                            url_id=monitored_url.id,
                            url=pdf_url
                        )
                        print(f"\n  ✓ No change detected (quick hash matches)")
                        
                        # Update header metadata from quick hash check if available
                        if header_result.success:
                            monitored_url.last_modified_header = header_result.last_modified
                            monitored_url.etag_header = header_result.etag
                            monitored_url.content_length_header = header_result.content_length
                        
                        # Store quick hash for next time (in case it wasn't stored before)
                        monitored_url.quick_hash = current_hash
                        
                        # Update last checked timestamp
                        monitored_url.last_checked_at = datetime.utcnow()
                        db.commit()
                        return True
                    else:
                        # Quick hash differs - proceed to full processing
                        if stored_hash is None:
                            logger.info(
                                "No stored quick hash (first check) - proceeding to full download",
                                url_id=monitored_url.id
                            )
                        else:
                            logger.info(
                                "Quick hash differs - proceeding to full download",
                                url_id=monitored_url.id,
                                stored=stored_hash[:16] + "...",
                                current=current_hash[:16] + "..."
                            )
                        print(f"\n  🔍 Change detected (quick hash differs) - downloading full PDF...")
            
            # ========================================================================
            # TIER 3: Full Download and Processing (only if Tier 1 or 2 indicate change)
            # ========================================================================
            # Download PDF to temp file
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                original_pdf = temp_path / "original.pdf"
                
                download_result = self.downloader.download(pdf_url, original_pdf)
                
                # Step 1b: If download fails, check if it's a new form first
                if not download_result.success:
                    logger.warning(
                        "Download failed",
                        url=pdf_url,
                        error=download_result.error,
                        status_code=download_result.status_code
                    )
                    
                    print(f"\n  ⚠️  Download failed: {download_result.error}")
                    
                    # Only trigger relocation search for 404 errors (URL not found)
                    # Other errors (timeout, 403, etc.) might be temporary or server-side issues
                    is_404 = download_result.status_code == 404
                    
                    if not is_404:
                        logger.info(
                            "Download failed but not a 404 - skipping relocation search",
                            url=pdf_url,
                            status_code=download_result.status_code,
                            error=download_result.error
                        )
                        print(f"  ⚠️  Download failed with status {download_result.status_code} (not 404) - skipping relocation search")
                        print(f"     This may be a temporary server issue. The URL will be retried on the next check.")
                        return False
                    
                    # Check if this is a new form (no versions exist) BEFORE trying relocation
                    previous_version = self.version_manager.get_latest_version(db, monitored_url.id)
                    
                    # If it's a new form, skip relocation search and handle removal immediately
                    if not previous_version:
                        # Reference module-level settings to avoid UnboundLocalError
                        import config
                        if config.settings.REMOVE_INACCESSIBLE_NEW_FORMS:
                            # This is a new form with no versions - remove it immediately
                            logger.info(
                                "Removing inaccessible new form (temporary feature) - skipping relocation search",
                                url_id=monitored_url.id,
                                url=monitored_url.url,
                                name=monitored_url.name
                            )
                            print(f"  🗑️  Removing inaccessible new form: {monitored_url.name}")
                            print(f"     (Skipping relocation search - form has never been successfully downloaded)")
                            
                            # Disable the URL instead of deleting (safer, can be re-enabled)
                            monitored_url.enabled = False
                            db.commit()
                            
                            return False
                        else:
                            # Toggle is off, but still skip relocation for new forms
                            logger.info(
                                "New form download failed - skipping relocation search",
                                url_id=monitored_url.id,
                                url=pdf_url
                            )
                            print(f"  ❌ New form inaccessible (relocation search skipped for new forms)")
                            return False
                    
                    # Only try relocation search if form has been successfully downloaded before
                    logger.info(
                        "Download failed, checking for relocated form",
                        url=pdf_url,
                        url_id=monitored_url.id
                    )
                    print(f"  🔍 Searching for relocated form...")
                    
                    # Get previous version's form number for matching
                    form_number = previous_version.form_number if previous_version else None
                    form_title = previous_version.formatted_title if previous_version else None
                    
                    # Also try to extract form number from the URL if we don't have one
                    if not form_number:
                        form_number = self.link_crawler.extract_form_number(pdf_url)
                    
                    if form_number:
                        print(f"     Looking for form: {form_number}")
                    
                    # Try to find relocated form with enhanced multi-level crawler
                    crawl_result = self.link_crawler.find_relocated_form(
                        original_url=pdf_url,
                        form_number=form_number,
                        form_title=form_title,
                        parent_url=monitored_url.parent_page_url
                    )
                    
                    if crawl_result.success and crawl_result.matched_url:
                        logger.info(
                            "Found relocated form",
                            new_url=crawl_result.matched_url,
                            reason=crawl_result.match_reason,
                            pages_crawled=crawl_result.pages_crawled
                        )
                        
                        print(f"  ✅ Found relocated form!")
                        print(f"     New URL: {crawl_result.matched_url}")
                        print(f"     Reason: {crawl_result.match_reason}")
                        if crawl_result.pages_crawled:
                            print(f"     Pages searched: {crawl_result.pages_crawled}")
                        
                        # Try downloading from new URL
                        relocated_from_url = pdf_url
                        pdf_url = crawl_result.matched_url
                        download_result = self.downloader.download(pdf_url, original_pdf)
                        
                        if download_result.success:
                            # Update the monitored URL to new location
                            monitored_url.url = pdf_url
                            logger.info("Updated monitored URL to new location", new_url=pdf_url)
                    elif crawl_result.success and crawl_result.pdf_links:
                        # Found PDFs but no match - log for manual review
                        print(f"  ❌ No automatic match found")
                        print(f"     PDFs found: {len(crawl_result.pdf_links)}")
                        print(f"     Pages searched: {crawl_result.pages_crawled}")
                        logger.warning(
                            "No matching form found in crawl",
                            original_url=pdf_url,
                            pdfs_found=len(crawl_result.pdf_links),
                            pages_crawled=crawl_result.pages_crawled
                        )
                    else:
                        print(f"  ❌ Crawl failed: {crawl_result.error}")
                    
                    if not download_result.success:
                        logger.error(
                            "Failed to download PDF after relocation search",
                            url=pdf_url,
                            error=download_result.error
                        )
                        
                        # Create a change log entry for failed relocation
                        if previous_version:
                            from db.models import ChangeLog
                            relocation_failed_log = ChangeLog(
                                monitored_url_id=monitored_url.id,
                                previous_version_id=previous_version.id,
                                new_version_id=previous_version.id,  # Use same version since no new version was created
                                change_type="relocation_failed",
                                diff_summary=f"Form became inaccessible at {pdf_url}. Relocation search {'found PDFs but no match' if (crawl_result.success and crawl_result.pdf_links) else f'failed: {crawl_result.error if crawl_result.error else "no PDFs found"}'}.",
                                pdf_hash_changed=False,
                                text_hash_changed=False,
                                review_status="pending",
                                reviewed=False
                            )
                            db.add(relocation_failed_log)
                            monitored_url.last_change_at = datetime.utcnow()
                            db.commit()
                            
                            logger.info(
                                "Relocation failure logged",
                                change_log_id=relocation_failed_log.id,
                                url_id=monitored_url.id
                            )
                            print(f"  📝 Relocation failure logged (Change ID: {relocation_failed_log.id})")
                        
                        return False
                
                logger.info(
                    "PDF downloaded",
                    size=download_result.file_size,
                    retries=download_result.retries_used
                )
                
                # Step 2: Extract text (using original PDF directly)
                extraction_result = self.text_extractor.extract(original_pdf)
                
                extracted_text = extraction_result.full_text
                page_texts = extraction_result.page_texts
                extraction_method = extraction_result.extraction_method
                ocr_used = False
                
                # Step 3: Compute hashes FIRST (before expensive operations)
                # Use extracted text even if incomplete - we'll re-run OCR if change detected
                hashes = self.hasher.compute_hashes(
                    original_pdf,
                    extracted_text,
                    page_texts
                )
                
                # Step 4: Get previous version for comparison
                previous_version = self.version_manager.get_latest_version(
                    db,
                    monitored_url.id
                )
                
                previous_hashes = None
                previous_text = ""
                
                if previous_version:
                    from diffing.hasher import HashResult
                    previous_hashes = HashResult(
                        pdf_hash=previous_version.pdf_hash,
                        text_hash=previous_version.text_hash,
                        page_hashes=previous_version.page_hashes or []
                    )
                    previous_text = self.version_manager.get_version_text(
                        db,
                        previous_version.id
                    ) or ""
                
                # Step 5: Detect changes (with early termination)
                change_result = self.change_detector.compare(
                    hashes,
                    previous_hashes,
                    extracted_text,
                    previous_text
                )
                
                # #region agent log
                try:
                    with open('/Users/william.holden/Documents/GitHub/URL_monitor_demo/.cursor/debug.log', 'a') as f:
                        f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"D","location":"cli.py:process_url","message":"Change detection result","data":{"url_id":monitored_url.id,"changed":change_result.changed,"change_type":change_result.change_type,"pdf_hash_changed":change_result.pdf_hash_changed,"text_hash_changed":change_result.text_hash_changed},"timestamp":int(__import__('time').time()*1000)})+'\n')
                except: pass
                # #endregion
                
                # Step 5b: OCR fallback ONLY if change detected AND text insufficient
                if change_result.changed and extraction_result.needs_ocr:
                    logger.info("Change detected and text insufficient, attempting OCR")
                    
                    if self.ocr_fallback.is_available():
                        ocr_result = self.ocr_fallback.process_pdf(
                            original_pdf,
                            url=monitored_url.url
                        )
                        
                        if ocr_result.success:
                            extracted_text = ocr_result.full_text
                            page_texts = ocr_result.page_texts
                            extraction_method = "textract"
                            ocr_used = True
                            logger.info(
                                "OCR completed",
                                chars=len(extracted_text),
                                confidence=ocr_result.confidence
                            )
                            
                            # Recompute hashes with OCR text
                            hashes = self.hasher.compute_hashes(
                                original_pdf,
                                extracted_text,
                                page_texts
                            )
                            
                            # Re-compare with OCR text
                            change_result = self.change_detector.compare(
                                hashes,
                                previous_hashes,
                                extracted_text,
                                previous_text
                            )
                        else:
                            logger.warning(
                                "OCR failed, using partial text",
                                error=ocr_result.error
                            )
                    else:
                        logger.warning("OCR not available, using partial text")
                
                # Step 5c: Enhanced form matching
                match_result = None
                if previous_version and change_result.changed:
                    match_result = self.form_matcher.match_forms(
                        old_text=previous_text,
                        new_text=extracted_text,
                        old_form_number=previous_version.form_number,
                        new_form_number=None,  # Will be extracted below
                        old_title=previous_version.formatted_title,
                        new_title=None
                    )
                    logger.info(
                        "Form match result",
                        match_type=match_result.match_type.value,
                        similarity=f"{match_result.similarity_score:.1%}",
                        confidence=f"{match_result.confidence:.1%}"
                    )
                    
                    # #region agent log
                    try:
                        with open('/Users/william.holden/Documents/GitHub/URL_monitor_demo/.cursor/debug.log', 'a') as f:
                            f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"E","location":"cli.py:process_url","message":"Initial form match result","data":{"url_id":monitored_url.id,"match_type":match_result.match_type.value,"title_changed":match_result.title_old != match_result.title_new,"old_title":previous_version.formatted_title,"new_title":None,"form_numbers_match":previous_version.form_number == match_result.form_number_new if match_result.form_number_new else False,"current_change_type":change_result.change_type},"timestamp":int(__import__('time').time()*1000)})+'\n')
                    except: pass
                    # #endregion
                
                # Step 6: Store version if changed, first version, or relocated
                # Only create a version if:
                # 1. It's the first version (no previous version exists)
                # 2. Content actually changed (not just format-only, unless we track those)
                # 3. URL was relocated (even if content unchanged, we track the new location)
                should_create_version = (
                    not previous_version or  # First version
                    (change_result.changed and change_result.change_type != "unchanged") or  # Real change
                    relocated_from_url is not None  # URL relocation
                )
                
                # Don't create version for format-only changes if auto-dismiss is enabled
                if (change_result.change_type == "format_only" and 
                    settings.AUTO_DISMISS_FORMAT_ONLY and 
                    previous_version):
                    should_create_version = False
                    logger.info(
                        "Skipping version creation for format-only change (auto-dismiss enabled)",
                        url_id=monitored_url.id
                    )
                    # #region agent log
                    try:
                        with open('/Users/william.holden/Documents/GitHub/URL_monitor_demo/.cursor/debug.log', 'a') as f:
                            f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"D","location":"cli.py:process_url","message":"Format-only auto-dismissed","data":{"url_id":monitored_url.id,"auto_dismiss_enabled":settings.AUTO_DISMISS_FORMAT_ONLY},"timestamp":int(__import__('time').time()*1000)})+'\n')
                    except: pass
                    # #endregion
                
                if should_create_version:
                    new_version = self.version_manager.create_version(
                        db=db,
                        monitored_url=monitored_url,
                        original_pdf_path=original_pdf,
                        extracted_text=extracted_text,
                        page_texts=page_texts,
                        hashes=hashes,
                        extraction_method=extraction_method,
                        ocr_used=ocr_used
                    )
                    
                    # Step 6b: Extract title (only if change detected)
                    if change_result.changed and self.title_extractor.is_available():
                        logger.info("Extracting title")
                        preview_path = self.version_manager.file_store.get_preview_image_path(
                            monitored_url.id, new_version.id
                        )
                        title_result = self.title_extractor.extract_title(
                            original_pdf, 
                            preview_path
                        )
                        
                        if title_result.success:
                            new_version.formatted_title = title_result.formatted_title
                            new_version.form_number = title_result.form_number
                            new_version.title_confidence = title_result.combined_confidence
                            new_version.title_extraction_method = title_result.extraction_method
                            new_version.revision_date = title_result.revision_date
                            db.commit()
                            
                            logger.info(
                                "Title extracted",
                                title=title_result.formatted_title,
                                form_number=title_result.form_number,
                                revision_date=title_result.revision_date,
                                confidence=title_result.combined_confidence
                            )
                            
                            # Re-run form matching now that we have the new title
                            # This is important because title matching takes priority over form number/similarity
                            if previous_version:
                                updated_match = self.form_matcher.match_forms(
                                    old_text=previous_text,
                                    new_text=extracted_text,
                                    old_form_number=previous_version.form_number,
                                    new_form_number=title_result.form_number,
                                    old_title=previous_version.formatted_title,
                                    new_title=title_result.formatted_title
                                )
                                # Update match_result with the new classification
                                match_result = updated_match
                                logger.info(
                                    "Updated form match with titles",
                                    match_type=match_result.match_type.value,
                                    old_title=previous_version.formatted_title,
                                    new_title=title_result.formatted_title
                                )
                                
                                # #region agent log
                                try:
                                    with open('/Users/william.holden/Documents/GitHub/URL_monitor_demo/.cursor/debug.log', 'a') as f:
                                        f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"E","location":"cli.py:process_url","message":"Form match after title extraction","data":{"url_id":monitored_url.id,"match_type":match_result.match_type.value,"title_changed":match_result.title_old != match_result.title_new,"old_title":previous_version.formatted_title,"new_title":title_result.formatted_title,"form_numbers_match":previous_version.form_number == title_result.form_number},"timestamp":int(__import__('time').time()*1000)})+'\n')
                                except: pass
                                # #endregion
                                
                                # Fix: Update change_type to "title_changed" if form numbers match and only title changed
                                if (match_result.match_type.value == "similarity_match" and 
                                    previous_version.form_number == title_result.form_number and
                                    match_result.title_old != match_result.title_new):
                                    # This is a title change - update change_type
                                    change_result.change_type = "title_changed"
                                    logger.info(
                                        "Change type updated to title_changed",
                                        old_title=previous_version.formatted_title,
                                        new_title=title_result.formatted_title
                                    )
                                    # #region agent log
                                    try:
                                        with open('/Users/william.holden/Documents/GitHub/URL_monitor_demo/.cursor/debug.log', 'a') as f:
                                            f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"E","location":"cli.py:process_url","message":"Change type updated to title_changed","data":{"url_id":monitored_url.id,"old_change_type":"text_changed","new_change_type":"title_changed"},"timestamp":int(__import__('time').time()*1000)})+'\n')
                                    except: pass
                                    # #endregion
                        else:
                            logger.warning(
                                "Title extraction failed",
                                error=title_result.error
                            )
                    else:
                        logger.info("AWS credentials not configured, skipping title extraction")
                    
                    # Step 6c: Generate visual diff if we have a previous version (only if change detected)
                    diff_image_path = None
                    if change_result.changed and previous_version:
                        logger.info("Generating visual diff")
                        
                        # Get paths to previous version PDFs (use original PDFs)
                        prev_original = self.version_manager.file_store.get_original_pdf(
                            monitored_url.id, previous_version.id
                        )
                        
                        if prev_original:
                            diff_output = self.version_manager.file_store.get_diff_image_path(
                                monitored_url.id, new_version.id
                            )
                            
                            diff_result = self.visual_diff.generate_diff(
                                old_pdf_path=prev_original,
                                new_pdf_path=original_pdf,
                                output_path=diff_output
                            )
                            
                            if diff_result.success:
                                diff_image_path = str(diff_result.diff_image_path)
                                logger.info(
                                    "Visual diff generated",
                                    change_pct=f"{diff_result.change_percentage:.1%}",
                                    regions=len(diff_result.changed_regions or [])
                                )
                            else:
                                logger.warning(
                                    "Visual diff generation failed",
                                    error=diff_result.error
                                )
                    
                    # If URL relocated but content unchanged, create a special change result
                    if relocated_from_url and not change_result.changed:
                        # Create a relocation change result
                        change_result = ChangeResult(
                            changed=True,
                            change_type="relocated",
                            pdf_hash_changed=False,
                            text_hash_changed=False,
                            diff_summary=f"Form relocated from {relocated_from_url} to {pdf_url}. Content unchanged."
                        )
                        logger.info(
                            "URL relocation detected (content unchanged)",
                            old_url=relocated_from_url,
                            new_url=pdf_url
                        )
                        # Since content is unchanged, this is definitely the same form
                        # Create a match result to indicate this
                        if previous_version:
                            from services.form_matcher import MatchType, MatchResult
                            # Use title extraction results if available (title extraction happens before this)
                            new_form_number = new_version.form_number if hasattr(new_version, 'form_number') else None
                            new_title = new_version.formatted_title if hasattr(new_version, 'formatted_title') else None
                            match_result = MatchResult(
                                match_type=MatchType.FORM_NUMBER_MATCH,
                                similarity_score=1.0,  # 100% identical content
                                form_number_old=previous_version.form_number,
                                form_number_new=new_form_number,
                                title_old=previous_version.formatted_title,
                                title_new=new_title,
                                confidence=1.0,
                                reason="Content identical - same form at new location",
                                changed_sections=[]
                            )
                    
                    # Record change with enhanced fields
                    change_log = self.version_manager.record_change(
                        db=db,
                        monitored_url=monitored_url,
                        previous_version=previous_version,
                        new_version=new_version,
                        change_result=change_result
                    )
                    
                    # Update with enhanced change detection fields
                    if change_log:
                        if match_result:
                            change_log.match_type = match_result.match_type.value
                            change_log.similarity_score = match_result.similarity_score
                        elif not previous_version:
                            # First version - classify as new form
                            change_log.match_type = "new_form"
                        
                        # Generate AI action recommendation
                        recommendation = action_recommender.recommend(
                            change_type=change_result.change_type,
                            confidence=match_result.confidence if match_result else None,
                            similarity_score=match_result.similarity_score if match_result else None,
                            match_type=match_result.match_type.value if match_result else None,
                            is_first_version=(not previous_version),
                            has_form_number_match=(match_result and match_result.match_type == MatchType.FORM_NUMBER_MATCH) if match_result else False,
                            title_changed=(match_result and match_result.title_old != match_result.title_new) if match_result else False,
                            relocated=(relocated_from_url is not None)
                        )
                        
                        # Store recommendation
                        change_log.recommended_action = recommendation.action.value
                        change_log.action_confidence = recommendation.confidence
                        change_log.action_rationale = recommendation.rationale
                        
                        # Auto-dismiss format-only changes if configured
                        import config
                        if (change_result.change_type == "format_only" and 
                            config.settings.AUTO_DISMISS_FORMAT_ONLY and 
                            recommendation.action.value == "false_positive"):
                            change_log.review_status = "auto_approved"
                            change_log.reviewed = True
                            change_log.reviewed_at = datetime.utcnow()
                            change_log.reviewed_by = "auto_dismiss_system"
                            change_log.review_notes = "Format-only change auto-dismissed"
                            logger.info(
                                "Format-only change auto-dismissed",
                                change_id=change_log.id
                            )
                        
                        # Auto-approve high-confidence changes if configured
                        elif (recommendation.action.value == "auto_approve" and 
                              not recommendation.requires_human_review):
                            change_log.review_status = "auto_approved"
                            change_log.reviewed = True
                            change_log.reviewed_at = datetime.utcnow()
                            change_log.reviewed_by = "auto_approve_system"
                            change_log.review_notes = f"Auto-approved: {recommendation.rationale}"
                            logger.info(
                                "High-confidence change auto-approved",
                                change_id=change_log.id,
                                confidence=recommendation.confidence
                            )
                        
                    if change_log and relocated_from_url:
                        change_log.relocated_from_url = relocated_from_url
                        logger.info(
                            "Relocation recorded in change log",
                            change_log_id=change_log.id,
                            old_url=relocated_from_url,
                            new_url=pdf_url
                        )
                        
                    if change_log and diff_image_path:
                        change_log.diff_image_path = diff_image_path
                        
                    db.commit()
                    
                    # Step 7: Index in Kendra (if enabled)
                    if kendra_indexer.is_enabled() and should_create_version:
                        try:
                            index_result = kendra_indexer.index_version(db, new_version.id)
                            if index_result.success:
                                logger.info(
                                    "Version indexed in Kendra",
                                    version_id=new_version.id,
                                    document_id=index_result.document_id
                                )
                            else:
                                logger.warning(
                                    "Failed to index version in Kendra",
                                    version_id=new_version.id,
                                    error=index_result.error
                                )
                        except Exception as e:
                            logger.error(
                                "Error indexing version in Kendra",
                                version_id=new_version.id,
                                error=str(e)
                            )
                    
                    # Build detailed change summary
                    change_summary = {
                        "change_type": change_result.change_type,
                        "version": new_version.version_number,
                        "match_type": match_result.match_type.value if match_result else "new",
                        "similarity": f"{match_result.similarity_score:.1%}" if match_result else "N/A",
                        "title": new_version.formatted_title,
                        "form_number": new_version.form_number
                    }
                    
                    logger.info(
                        "Change detected and stored",
                        change_type=change_result.change_type,
                        version=new_version.version_number,
                        match_type=match_result.match_type.value if match_result else None,
                        relocated=relocated_from_url is not None
                    )
                    
                    # Print user-friendly summary
                    if relocated_from_url:
                        print(f"\n  📍 URL RELOCATION DETECTED:")
                        print(f"     Old URL: {relocated_from_url}")
                        print(f"     New URL: {pdf_url}")
                        if change_result.change_type == "relocated":
                            print(f"     Content: Unchanged (same form at new location)")
                        else:
                            print(f"     Type: {change_result.change_type}")
                    elif change_result.change_type == "format_only":
                        print(f"\n  🔄 FORMAT-ONLY CHANGE DETECTED:")
                        print(f"     Type: Format-only (binary changed, text unchanged)")
                        print(f"     Note: PDF binary hash changed but extracted text is identical")
                        if change_log and change_log.review_status == "auto_approved":
                            print(f"     Action: ✅ Auto-dismissed (no semantic changes)")
                        else:
                            print(f"     Action: No semantic changes - no action needed")
                    else:
                        print(f"\n  📋 CHANGE DETECTED:")
                        print(f"     Type: {change_result.change_type}")
                    
                    if match_result:
                        print(f"     Match: {match_result.match_type.value.replace('_', ' ').title()}")
                        print(f"     Similarity: {match_result.similarity_score:.1%}")
                        if match_result.changed_sections:
                            print(f"     Changed sections: {', '.join(match_result.changed_sections[:5])}")
                    if new_version.formatted_title:
                        print(f"     Title: {new_version.formatted_title}")
                    if new_version.form_number:
                        print(f"     Form #: {new_version.form_number}")
                    
                    # Show AI recommendation
                    if change_log and change_log.recommended_action:
                        action_icons = {
                            "auto_approve": "✅",
                            "review_suggested": "👀",
                            "manual_required": "⚠️",
                            "false_positive": "🚫",
                            "new_form": "🆕"
                        }
                        icon = action_icons.get(change_log.recommended_action, "❓")
                        print(f"     AI Recommendation: {icon} {change_log.recommended_action.replace('_', ' ').title()}")
                        print(f"     Confidence: {change_log.action_confidence:.0%}" if change_log.action_confidence else "")
                        if change_log.review_status == "auto_approved":
                            print(f"     Status: ✅ Auto-approved")
                else:
                    logger.info("No changes detected")
                    print(f"\n  ✓ No changes detected")
                
                # Store header metadata and quick hash for future fast checks
                # (even if no change detected, we want to update headers for next check)
                if header_result.success:
                    monitored_url.last_modified_header = header_result.last_modified
                    monitored_url.etag_header = header_result.etag
                    monitored_url.content_length_header = header_result.content_length
                    logger.debug("Stored header metadata", url_id=monitored_url.id)
                
                # Store quick hash for future checks
                # Priority: Use Tier 2 result if available (most accurate), otherwise compute from file
                if quick_hash_result and quick_hash_result.success:
                    # Use the quick hash from Tier 2 check (computed from URL via Range request)
                    monitored_url.quick_hash = quick_hash_result.quick_hash
                    logger.debug(
                        "Stored quick hash from Tier 2 check",
                        url_id=monitored_url.id,
                        hash=quick_hash_result.quick_hash[:16] + "..."
                    )
                elif 'original_pdf' in locals() and original_pdf.exists():
                    # Compute quick hash from original PDF if we didn't do Tier 2 check
                    # (This ensures we have quick hash for next time)
                    # Use same method as Tier 2: read in 8KB chunks up to 64KB
                    try:
                        import hashlib
                        sha256 = hashlib.sha256()
                        bytes_read = 0
                        chunk_size = 8192  # Same as quick_hasher
                        max_bytes = 65536  # Same as quick_hasher.chunk_size
                        
                        with open(original_pdf, 'rb') as f:
                            while bytes_read < max_bytes:
                                chunk = f.read(min(chunk_size, max_bytes - bytes_read))
                                if not chunk:
                                    break
                                sha256.update(chunk)
                                bytes_read += len(chunk)
                        
                        quick_hash = sha256.hexdigest()
                        monitored_url.quick_hash = quick_hash
                        logger.debug(
                            "Computed and stored quick hash from original PDF",
                            url_id=monitored_url.id,
                            hash=quick_hash[:16] + "...",
                            bytes_read=bytes_read
                        )
                    except Exception as e:
                        logger.warning("Failed to compute quick hash from original PDF", error=str(e))
                
                # Update last checked timestamp
                monitored_url.last_checked_at = datetime.utcnow()
                db.commit()
                
                return True
                
        except Exception as e:
            logger.exception(
                "Error processing URL",
                url_id=monitored_url.id,
                error=str(e)
            )
            return False
    
    def run_cycle(self, db, url_id: Optional[int] = None, max_workers: Optional[int] = None) -> dict:
        """
        Run a monitoring cycle with optional parallel processing.
        
        Args:
            db: Database session (used for querying, each thread gets its own session)
            url_id: Optional specific URL ID to process
            max_workers: Number of parallel workers (default: min(10, number of URLs))
            
        Returns:
            Dictionary with results summary
        """
        logger.info("Starting monitoring cycle", url_id=url_id)
        
        # Get URLs to process
        query = db.query(MonitoredURL).filter(MonitoredURL.enabled == True)
        if url_id:
            query = query.filter(MonitoredURL.id == url_id)
        
        urls = query.all()
        
        if not urls:
            logger.warning("No URLs to process")
            return {"processed": 0, "success": 0, "failed": 0}
        
        # Determine number of workers (default to config setting or number of URLs, whichever is smaller)
        if max_workers is None:
            max_workers = min(settings.MAX_WORKERS, len(urls))
        
        results = {
            "processed": len(urls),
            "success": 0,
            "failed": 0,
            "details": []
        }
        
        # Helper function to process a single URL with its own database session
        def process_url_with_session(url_data):
            """Process a URL with a fresh database session for thread safety."""
            url_id, url_name, url_url = url_data
            thread_db = SessionLocal()
            try:
                # Re-fetch the URL in this thread's session
                url = thread_db.query(MonitoredURL).filter(MonitoredURL.id == url_id).first()
                if not url:
                    logger.warning("URL not found in thread session", url_id=url_id)
                    return {"url_id": url_id, "name": url_name, "success": False}
                
                success = self.process_url(thread_db, url)
                thread_db.commit()
                
                return {"url_id": url_id, "name": url_name, "success": success}
            except Exception as e:
                logger.error(
                    "Error processing URL in thread",
                    url_id=url_id,
                    error=str(e),
                    exc_info=True
                )
                thread_db.rollback()
                return {"url_id": url_id, "name": url_name, "success": False}
            finally:
                thread_db.close()
        
        # Process URLs in parallel if we have multiple URLs and max_workers > 1
        if len(urls) > 1 and max_workers > 1:
            logger.info(
                "Processing URLs in parallel",
                total=len(urls),
                workers=max_workers
            )
            
            # Prepare URL data for parallel processing
            url_data_list = [(url.id, url.name, url.url) for url in urls]
            
            # Process in parallel
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_url = {
                    executor.submit(process_url_with_session, url_data): url_data[0]
                    for url_data in url_data_list
                }
                
                for future in as_completed(future_to_url):
                    url_id_key = future_to_url[future]
                    try:
                        detail = future.result()
                        if detail["success"]:
                            results["success"] += 1
                        else:
                            results["failed"] += 1
                        results["details"].append(detail)
                    except Exception as e:
                        logger.error(
                            "Error getting result from thread",
                            url_id=url_id_key,
                            error=str(e)
                        )
                        results["failed"] += 1
                        results["details"].append({
                            "url_id": url_id_key,
                            "name": "Unknown",
                            "success": False
                        })
        else:
            # Process sequentially (single URL or max_workers = 1)
            logger.info("Processing URLs sequentially", total=len(urls))
            for url in urls:
                success = self.process_url(db, url)
                
                if success:
                    results["success"] += 1
                else:
                    results["failed"] += 1
                
                results["details"].append({
                    "url_id": url.id,
                    "name": url.name,
                    "success": success
                })
        
        logger.info(
            "Monitoring cycle complete",
            processed=results["processed"],
            success=results["success"],
            failed=results["failed"]
        )
        
        return results


def cmd_init():
    """Initialize database."""
    logger.info("Initializing database")
    settings.ensure_directories()
    run_migrations()
    logger.info("Database initialized")


# cmd_seed function removed - localhost testing URLs no longer supported


def cmd_run(url_id: Optional[int] = None, max_workers: Optional[int] = None):
    """Run monitoring cycle."""
    logger.info("Running monitoring cycle", url_id=url_id, max_workers=max_workers)
    settings.ensure_directories()
    run_migrations()

    # Ensure all configured AWS features are working before starting
    aws_errors = verify_aws_features()
    if aws_errors:
        logger.error("AWS feature checks failed; aborting monitoring cycle", errors=aws_errors)
        print("\n❌ AWS feature checks failed. Fix before running a monitoring cycle:\n")
        for msg in aws_errors:
            print(f"  • {msg}")
        print("\nRe-run after AWS is working.")
        sys.exit(1)
    
    # Validate settings
    issues = settings.validate()
    if issues:
        for issue in issues:
            logger.warning(f"Configuration issue: {issue}")
    
    db = SessionLocal()
    try:
        orchestrator = MonitoringOrchestrator()
        results = orchestrator.run_cycle(db, url_id, max_workers=max_workers)
        
        print("\n=== Monitoring Results ===")
        print(f"Processed: {results['processed']}")
        print(f"Success: {results['success']}")
        print(f"Failed: {results['failed']}")
        
        if results.get("details"):
            print("\nDetails:")
            for detail in results["details"]:
                status = "✓" if detail["success"] else "✗"
                print(f"  {status} [{detail['url_id']}] {detail['name']}")
        
    finally:
        db.close()


def cmd_reset():
    """Reset test environment: clear versions/changes and revert test PDFs."""
    import subprocess
    
    logger.info("Resetting test environment")
    settings.ensure_directories()
    run_migrations()
    
    # Get project root directory
    project_dir = Path(__file__).parent
    
    db = SessionLocal()
    try:
        # Clear all versions and change logs
        change_count = db.query(ChangeLog).delete()
        version_count = db.query(PDFVersion).delete()
        db.commit()
        
        print(f"\n🧹 Cleared {version_count} versions and {change_count} change logs")
        
        # Revert test PDFs to baseline
        print("\n📋 Reverting test PDFs to baseline...")
        result = subprocess.run(
            [sys.executable, "test_site/simulate_update.py", "revert"],
            capture_output=True,
            text=True,
            cwd=project_dir
        )
        
        if result.returncode == 0:
            # Show relevant output
            for line in result.stdout.split('\n'):
                if line.strip() and ('✓' in line or '•' in line or 'Reverted' in line):
                    print(f"  {line.strip()}")
            print("\n✅ Test environment reset complete!")
            print("\nNext steps:")
            print("  1. Ensure test server is running: python test_server.py")
            print("  2. Run monitoring to set baseline: python cli.py run")
        else:
            print(f"⚠️  Warning: Could not revert PDFs: {result.stderr}")
            print("  You may need to run: python test_site/simulate_update.py revert")
        
    finally:
        db.close()


def cmd_status():
    """Show status of all URLs."""
    settings.ensure_directories()
    run_migrations()
    
    db = SessionLocal()
    try:
        urls = db.query(MonitoredURL).all()
        
        if not urls:
            print("No monitored URLs found. Run 'python cli.py seed' to add sample URLs.")
            return
        
        print("\n=== Monitored URLs ===")
        print(f"{'ID':<4} {'Name':<40} {'Status':<10} {'Versions':<10} {'Last Checked':<20} {'Last Change':<20}")
        print("-" * 110)
        
        for url in urls:
            version_count = db.query(PDFVersion).filter(
                PDFVersion.monitored_url_id == url.id
            ).count()
            
            status = "enabled" if url.enabled else "disabled"
            last_checked = url.last_checked_at.strftime("%Y-%m-%d %H:%M") if url.last_checked_at else "never"
            last_change = url.last_change_at.strftime("%Y-%m-%d %H:%M") if url.last_change_at else "n/a"
            
            print(f"{url.id:<4} {url.name[:40]:<40} {status:<10} {version_count:<10} {last_checked:<20} {last_change:<20}")
        
        print()
        
    finally:
        db.close()


def cmd_kendra_index_all(latest_only: bool = False, max_workers: Optional[int] = None):
    """Index all PDF versions in Kendra."""
    db = SessionLocal()
    try:
        if not kendra_indexer.is_enabled():
            print("ERROR: Kendra indexing is not enabled or not available.")
            print("Set KENDRA_INDEXING_ENABLED=true and configure AWS_KENDRA_INDEX_ID")
            return
        
        workers_info = f" (workers: {max_workers or 'auto'})" if max_workers or settings.MAX_WORKERS > 1 else ""
        print(f"Indexing all PDF versions in Kendra (latest_only={latest_only}{workers_info})...")
        result = kendra_indexer.index_all_versions(db, latest_only=latest_only, max_workers=max_workers)
        
        if result.get("success"):
            print(f"✓ Successfully indexed {result['indexed']} versions")
            if result.get("failed", 0) > 0:
                print(f"⚠ Failed to index {result['failed']} versions")
        else:
            print(f"ERROR: {result.get('error', 'Unknown error')}")
            if result.get("failed", 0) > 0:
                print(f"Failed to index {result['failed']} versions")
        
        if result.get("url_results"):
            print("\nPer-URL results:")
            for url_result in result["url_results"]:
                status = "✓" if url_result.get("success") else "✗"
                print(f"  {status} {url_result.get('url_name', 'Unknown')}: "
                      f"{url_result.get('indexed', 0)} indexed, "
                      f"{url_result.get('failed', 0)} failed")
    finally:
        db.close()


def cmd_kendra_status():
    """Check Kendra index status."""
    from services.kendra_client import kendra_client
    
    print("Kendra Index Status")
    print("=" * 50)
    
    if not kendra_client.is_available():
        print("✗ Kendra client not available")
        print("  Check AWS credentials and AWS_KENDRA_INDEX_ID configuration")
        return
    
    status = kendra_client.get_index_status()
    
    if status.get("available"):
        print(f"✓ Index available: {status.get('name', 'Unknown')}")
        print(f"  Index ID: {status.get('index_id', 'N/A')}")
        print(f"  Status: {status.get('status', 'N/A')}")
        print(f"  Edition: {status.get('edition', 'N/A')}")
        print(f"  Created: {status.get('created_at', 'N/A')}")
        print(f"  Updated: {status.get('updated_at', 'N/A')}")
    else:
        print(f"✗ Index not available: {status.get('error', 'Unknown error')}")
    
    print("\nConfiguration:")
    from config import settings
    print(f"  KENDRA_INDEXING_ENABLED: {settings.KENDRA_INDEXING_ENABLED}")
    print(f"  KENDRA_SEARCH_ENABLED: {settings.KENDRA_SEARCH_ENABLED}")
    print(f"  AWS_KENDRA_INDEX_ID: {settings.AWS_KENDRA_INDEX_ID or 'Not set'}")


def cmd_kendra_sync():
    """Sync Kendra index with database."""
    db = SessionLocal()
    try:
        if not kendra_indexer.is_enabled():
            print("ERROR: Kendra indexing is not enabled or not available.")
            return
        
        print("Syncing Kendra index with database...")
        result = kendra_indexer.sync_index_with_database(db)
        
        if result.get("success"):
            print(f"✓ Sync completed")
            print(f"  Checked: {result.get('checked', 0)} versions")
            print(f"  Re-indexed: {result.get('re_indexed', 0)} versions")
            print(f"  Missing: {result.get('missing', 0)} versions")
        else:
            print(f"ERROR: {result.get('error', 'Unknown error')}")
    finally:
        db.close()


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="PDF Monitor CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  init      Initialize database
  run       Run monitoring cycle
  reset     Reset test environment (clear data + revert PDFs)
  status    Show status of all URLs

Examples:
  python cli.py init          # Initialize database
  python cli.py run           # Run monitoring on all URLs
  python cli.py run --url-id 1  # Monitor specific URL
  python cli.py reset         # Clear data and reset test PDFs
  python cli.py status        # Show URL status

Test workflow:
  1. python cli.py seed       # Add test forms
  2. python test_server.py    # Start test server (in another terminal)
  3. python cli.py run        # Establish baseline
  4. python test_site/simulate_update.py <scenario>  # Run test scenario
  5. python cli.py run        # Detect changes
  6. python cli.py reset      # Reset for next test
        """
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")
    
    # Init command
    subparsers.add_parser("init", help="Initialize database")
    
    # Run command
    run_parser = subparsers.add_parser("run", help="Run monitoring cycle")
    run_parser.add_argument(
        "--url-id",
        type=int,
        help="Specific URL ID to process"
    )
    run_parser.add_argument(
        "--max-workers",
        type=int,
        default=None,
        help="Maximum number of parallel workers (default: from config)"
    )
    
    # Reset command
    subparsers.add_parser("reset", help="Reset test environment")
    
    # Status command
    subparsers.add_parser("status", help="Show status of all URLs")
    
    # Kendra commands
    kendra_parser = subparsers.add_parser("kendra", help="Kendra index management")
    kendra_subparsers = kendra_parser.add_subparsers(dest="kendra_command", help="Kendra subcommand")
    
    kendra_index_all = kendra_subparsers.add_parser("index-all", help="Index all PDF versions in Kendra")
    kendra_index_all.add_argument(
        "--latest-only",
        action="store_true",
        help="Only index latest version per URL"
    )
    kendra_index_all.add_argument(
        "--max-workers",
        type=int,
        default=None,
        help="Maximum number of parallel workers (default: from config)"
    )
    
    kendra_subparsers.add_parser("status", help="Check Kendra index status")
    kendra_subparsers.add_parser("sync", help="Sync Kendra index with database")
    
    args = parser.parse_args()
    
    if args.command == "init":
        cmd_init()
    elif args.command == "run":
        cmd_run(args.url_id, max_workers=args.max_workers)
    elif args.command == "reset":
        cmd_reset()
    elif args.command == "status":
        cmd_status()
    elif args.command == "kendra":
        if args.kendra_command == "index-all":
            cmd_kendra_index_all(latest_only=args.latest_only, max_workers=args.max_workers)
        elif args.kendra_command == "status":
            cmd_kendra_status()
        elif args.kendra_command == "sync":
            cmd_kendra_sync()
        else:
            kendra_parser.print_help()


if __name__ == "__main__":
    main()


