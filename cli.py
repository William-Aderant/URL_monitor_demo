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
from datetime import datetime
from pathlib import Path
from typing import Optional

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
from fetcher.firecrawl_client import FirecrawlClient
from fetcher.pdf_downloader import PDFDownloader
from pdf_processing.normalizer import PDFNormalizer
from pdf_processing.text_extractor import TextExtractor
from pdf_processing.ocr_fallback import OCRFallback
from diffing.hasher import Hasher
from diffing.change_detector import ChangeDetector
from storage.version_manager import VersionManager
from services.title_extractor import TitleExtractor
from services.link_crawler import LinkCrawler
from services.form_matcher import FormMatcher, MatchType
from services.visual_diff import VisualDiff


class MonitoringOrchestrator:
    """
    Orchestrates the PDF monitoring pipeline.
    
    Pipeline:
    1. Fetch PDF from URL
    2. Normalize PDF
    3. Extract text
    4. Compute hashes
    5. Compare with previous version
    6. Store new version and record changes
    """
    
    def __init__(self):
        """Initialize orchestrator with all required components."""
        self.firecrawl = None  # Lazy init
        self.downloader = PDFDownloader()
        self.normalizer = PDFNormalizer()
        self.text_extractor = TextExtractor()
        self.ocr_fallback = OCRFallback()
        self.hasher = Hasher()
        self.change_detector = ChangeDetector()
        self.version_manager = VersionManager()
        self.title_extractor = TitleExtractor()
        
        # Enhanced change detection services
        self.link_crawler = LinkCrawler()
        self.form_matcher = FormMatcher()
        self.visual_diff = VisualDiff()
        
        logger.info("MonitoringOrchestrator initialized")
    
    def _get_firecrawl(self) -> FirecrawlClient:
        """Lazy-load Firecrawl client."""
        if self.firecrawl is None:
            self.firecrawl = FirecrawlClient()
        return self.firecrawl
    
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
        
        relocated_from_url = None  # Track if form was found at different URL
        
        try:
            # Step 1: Fetch PDF
            pdf_url = monitored_url.url
            
            # If URL is not a direct PDF, use Firecrawl to find PDF link
            if not pdf_url.lower().endswith('.pdf'):
                logger.info("URL is not direct PDF, scraping for PDF link")
                firecrawl = self._get_firecrawl()
                scrape_result = firecrawl.scrape_url(monitored_url.url)
                
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
            
            # Download PDF to temp file
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                original_pdf = temp_path / "original.pdf"
                
                download_result = self.downloader.download(pdf_url, original_pdf)
                
                # Step 1b: If download fails, try to find relocated form
                if not download_result.success:
                    logger.warning(
                        "Download failed, checking for relocated form",
                        url=pdf_url,
                        error=download_result.error
                    )
                    
                    # Get previous version's form number for matching
                    previous_version = self.version_manager.get_latest_version(db, monitored_url.id)
                    form_number = previous_version.form_number if previous_version else None
                    form_title = previous_version.formatted_title if previous_version else None
                    
                    # Try to find relocated form
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
                            reason=crawl_result.match_reason
                        )
                        
                        # Try downloading from new URL
                        relocated_from_url = pdf_url
                        pdf_url = crawl_result.matched_url
                        download_result = self.downloader.download(pdf_url, original_pdf)
                        
                        if download_result.success:
                            # Update the monitored URL to new location
                            monitored_url.url = pdf_url
                            logger.info("Updated monitored URL to new location", new_url=pdf_url)
                    
                    if not download_result.success:
                        logger.error(
                            "Failed to download PDF",
                            url=pdf_url,
                            error=download_result.error
                        )
                        return False
                
                logger.info(
                    "PDF downloaded",
                    size=download_result.file_size,
                    retries=download_result.retries_used
                )
                
                # Step 2: Normalize PDF
                normalized_pdf = temp_path / "normalized.pdf"
                norm_result = self.normalizer.normalize(original_pdf, normalized_pdf)
                
                if not norm_result.success:
                    logger.error(
                        "Failed to normalize PDF",
                        error=norm_result.error
                    )
                    return False
                
                logger.info(
                    "PDF normalized",
                    original_size=norm_result.original_size,
                    normalized_size=norm_result.normalized_size
                )
                
                # Step 3: Extract text
                extraction_result = self.text_extractor.extract(normalized_pdf)
                
                extracted_text = extraction_result.full_text
                page_texts = extraction_result.page_texts
                extraction_method = extraction_result.extraction_method
                ocr_used = False
                
                # Step 3b: OCR fallback if needed
                if extraction_result.needs_ocr:
                    logger.info("Text extraction insufficient, attempting OCR")
                    
                    if self.ocr_fallback.is_available():
                        ocr_result = self.ocr_fallback.process_pdf(
                            normalized_pdf,
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
                        else:
                            logger.warning(
                                "OCR failed, using partial text",
                                error=ocr_result.error
                            )
                    else:
                        logger.warning("OCR not available, using partial text")
                
                # Step 4: Compute hashes
                hashes = self.hasher.compute_hashes(
                    normalized_pdf,
                    extracted_text,
                    page_texts
                )
                
                # Step 5: Get previous version for comparison
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
                
                # Step 5b: Detect changes
                change_result = self.change_detector.compare(
                    hashes,
                    previous_hashes,
                    extracted_text,
                    previous_text
                )
                
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
                
                # Step 6: Store version if changed or first version
                if change_result.changed:
                    new_version = self.version_manager.create_version(
                        db=db,
                        monitored_url=monitored_url,
                        original_pdf_path=original_pdf,
                        normalized_pdf_path=normalized_pdf,
                        extracted_text=extracted_text,
                        page_texts=page_texts,
                        hashes=hashes,
                        extraction_method=extraction_method,
                        ocr_used=ocr_used
                    )
                    
                    # Step 6b: Extract title using AWS Textract + Bedrock
                    if self.title_extractor.is_available():
                        logger.info("Extracting title with Textract + Bedrock")
                        preview_path = self.version_manager.file_store.get_preview_image_path(
                            monitored_url.id, new_version.id
                        )
                        title_result = self.title_extractor.extract_title(
                            normalized_pdf, 
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
                        else:
                            logger.warning(
                                "Title extraction failed",
                                error=title_result.error
                            )
                    else:
                        logger.info("AWS credentials not configured, skipping title extraction")
                    
                    # Step 6c: Generate visual diff if we have a previous version
                    diff_image_path = None
                    if previous_version:
                        logger.info("Generating visual diff")
                        
                        # Get paths to previous version PDFs
                        prev_normalized = self.version_manager.file_store.get_normalized_pdf(
                            monitored_url.id, previous_version.id
                        )
                        
                        if prev_normalized:
                            diff_output = self.version_manager.file_store.get_diff_image_path(
                                monitored_url.id, new_version.id
                            )
                            
                            diff_result = self.visual_diff.generate_diff(
                                old_pdf_path=prev_normalized,
                                new_pdf_path=normalized_pdf,
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
                    
                    # Record change with enhanced fields
                    change_log = self.version_manager.record_change(
                        db=db,
                        monitored_url=monitored_url,
                        previous_version=previous_version,
                        new_version=new_version,
                        change_result=change_result
                    )
                    
                    # Update with enhanced change detection fields
                    if change_log and match_result:
                        change_log.match_type = match_result.match_type.value
                        change_log.similarity_score = match_result.similarity_score
                        
                    if change_log and relocated_from_url:
                        change_log.relocated_from_url = relocated_from_url
                        
                    if change_log and diff_image_path:
                        change_log.diff_image_path = diff_image_path
                        
                    db.commit()
                    
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
                        match_type=match_result.match_type.value if match_result else None
                    )
                    
                    # Print user-friendly summary
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
                    if relocated_from_url:
                        print(f"     📍 Relocated from: {relocated_from_url}")
                else:
                    logger.info("No changes detected")
                    print(f"\n  ✓ No changes detected")
                
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
    
    def run_cycle(self, db, url_id: Optional[int] = None) -> dict:
        """
        Run a monitoring cycle.
        
        Args:
            db: Database session
            url_id: Optional specific URL ID to process
            
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
        
        results = {
            "processed": len(urls),
            "success": 0,
            "failed": 0,
            "details": []
        }
        
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


def cmd_seed():
    """Seed sample URLs."""
    logger.info("Seeding sample URLs")
    settings.ensure_directories()
    run_migrations()
    
    db = SessionLocal()
    try:
        seed_sample_urls(db)
    finally:
        db.close()
    
    logger.info("Sample URLs seeded")


def cmd_run(url_id: Optional[int] = None):
    """Run monitoring cycle."""
    logger.info("Running monitoring cycle")
    settings.ensure_directories()
    run_migrations()
    
    # Validate settings
    issues = settings.validate()
    if issues:
        for issue in issues:
            logger.warning(f"Configuration issue: {issue}")
    
    db = SessionLocal()
    try:
        orchestrator = MonitoringOrchestrator()
        results = orchestrator.run_cycle(db, url_id)
        
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


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="PDF Monitor CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  init      Initialize database
  seed      Seed sample URLs
  run       Run monitoring cycle
  status    Show status of all URLs

Examples:
  python cli.py init
  python cli.py seed
  python cli.py run
  python cli.py run --url-id 1
  python cli.py status
        """
    )
    
    parser.add_argument(
        "command",
        choices=["init", "seed", "run", "status"],
        help="Command to execute"
    )
    
    parser.add_argument(
        "--url-id",
        type=int,
        help="Specific URL ID to process (for 'run' command)"
    )
    
    args = parser.parse_args()
    
    if args.command == "init":
        cmd_init()
    elif args.command == "seed":
        cmd_seed()
    elif args.command == "run":
        cmd_run(args.url_id)
    elif args.command == "status":
        cmd_status()


if __name__ == "__main__":
    main()


