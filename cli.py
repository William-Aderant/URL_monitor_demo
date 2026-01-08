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
from db.models import MonitoredURL, PDFVersion
from db.migrations import run_migrations, seed_sample_urls
from fetcher.firecrawl_client import FirecrawlClient
from fetcher.pdf_downloader import PDFDownloader
from pdf_processing.normalizer import PDFNormalizer
from pdf_processing.text_extractor import TextExtractor
from pdf_processing.ocr_fallback import OCRFallback
from diffing.hasher import Hasher
from diffing.change_detector import ChangeDetector
from storage.version_manager import VersionManager


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
                    
                    # Record change
                    self.version_manager.record_change(
                        db=db,
                        monitored_url=monitored_url,
                        previous_version=previous_version,
                        new_version=new_version,
                        change_result=change_result
                    )
                    
                    logger.info(
                        "Change detected and stored",
                        change_type=change_result.change_type,
                        version=new_version.version_number
                    )
                else:
                    logger.info("No changes detected")
                
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

