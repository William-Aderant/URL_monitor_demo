#!/usr/bin/env python3
"""
Upload local PDFs (data/pdfs) to the BDA S3 bucket.

Use this to sync your local original PDFs to S3 before or alongside monitoring.
The monitoring cycle uses local PDFs: it downloads (or uses stored) PDFs and
passes them to BDA; BDA uploads each file to S3 internally before processing.

This script uses the same BDA_S3_BUCKET and a configurable prefix so uploads
are consistent with the app. Directory structure is preserved
(url_id/version_id/original.pdf).
"""

import os
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import time

import boto3
from botocore.exceptions import ClientError

from config import settings

# Use BDA config so uploads go to the same bucket used by monitoring
S3_BUCKET = settings.BDA_S3_BUCKET or ""
S3_PREFIX = os.getenv("BDA_UPLOAD_PREFIX", "pdf-monitor/originals/")  # optional override
if not S3_PREFIX.endswith("/"):
    S3_PREFIX = S3_PREFIX + "/"
PDF_DIR = settings.PDF_STORAGE_PATH
MAX_WORKERS = 50  # Number of parallel upload threads


class UploadProgress:
    """Thread-safe progress tracker."""
    
    def __init__(self, total: int):
        self.total = total
        self.uploaded = 0
        self.failed = 0
        self.skipped = 0
        self.lock = threading.Lock()
        self.start_time = time.time()
    
    def increment_uploaded(self):
        with self.lock:
            self.uploaded += 1
    
    def increment_failed(self):
        with self.lock:
            self.failed += 1
    
    def increment_skipped(self):
        with self.lock:
            self.skipped += 1
    
    def get_stats(self):
        with self.lock:
            elapsed = time.time() - self.start_time
            processed = self.uploaded + self.failed + self.skipped
            rate = processed / elapsed if elapsed > 0 else 0
            remaining = self.total - processed
            eta = remaining / rate if rate > 0 else 0
            return {
                'total': self.total,
                'uploaded': self.uploaded,
                'failed': self.failed,
                'skipped': self.skipped,
                'processed': processed,
                'elapsed': elapsed,
                'rate': rate,
                'eta': eta
            }


def get_s3_client():
    """Create S3 client using app config (region, optional credentials)."""
    kwargs = {"region_name": settings.AWS_REGION or "us-east-1"}
    if settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY:
        kwargs["aws_access_key_id"] = settings.AWS_ACCESS_KEY_ID
        kwargs["aws_secret_access_key"] = settings.AWS_SECRET_ACCESS_KEY
    return boto3.client("s3", **kwargs)


def upload_file(s3_client, local_path: Path, s3_key: str, progress: UploadProgress) -> bool:
    """Upload a single file to S3."""
    try:
        # Check if file already exists in S3
        try:
            s3_client.head_object(Bucket=S3_BUCKET, Key=s3_key)
            # File exists, skip
            progress.increment_skipped()
            return True
        except ClientError as e:
            if e.response['Error']['Code'] != '404':
                raise
            # File doesn't exist, proceed with upload
        
        # Upload the file
        s3_client.upload_file(
            str(local_path),
            S3_BUCKET,
            s3_key,
            ExtraArgs={'ContentType': 'application/pdf'}
        )
        progress.increment_uploaded()
        return True
        
    except Exception as e:
        progress.increment_failed()
        print(f"\nError uploading {local_path}: {e}")
        return False


def find_all_pdfs(pdf_dir: Path) -> list:
    """Find all PDF files in the directory."""
    pdfs = []
    for root, dirs, files in os.walk(pdf_dir):
        for file in files:
            if file.endswith('.pdf'):
                pdfs.append(Path(root) / file)
    return pdfs


def main():
    if not S3_BUCKET:
        print("Error: BDA_S3_BUCKET is not set. Set it in .env (required for BDA and for this script).")
        sys.exit(1)
    if not PDF_DIR.exists():
        print(f"Error: PDF directory does not exist: {PDF_DIR}")
        sys.exit(1)
    print(f"PDF Upload to S3 (BDA bucket)")
    print(f"==============================")
    print(f"Bucket: {S3_BUCKET}")
    print(f"Prefix: {S3_PREFIX}")
    print(f"Source: {PDF_DIR}")
    print(f"Workers: {MAX_WORKERS}")
    print()
    
    # Find all PDFs
    print("Scanning for PDF files...")
    pdfs = find_all_pdfs(PDF_DIR)
    total = len(pdfs)
    print(f"Found {total:,} PDF files")
    print()
    
    if total == 0:
        print("No PDFs to upload.")
        return
    
    # Initialize progress tracker
    progress = UploadProgress(total)
    
    # Create S3 client
    s3_client = get_s3_client()
    
    # Test S3 access
    print("Testing S3 access...")
    try:
        s3_client.head_bucket(Bucket=S3_BUCKET)
        print(f"Successfully connected to bucket: {S3_BUCKET}")
    except ClientError as e:
        print(f"Error accessing bucket: {e}")
        sys.exit(1)
    print()
    
    # Prepare upload tasks
    pdf_dir_resolved = PDF_DIR.resolve()
    tasks = []
    for pdf_path in pdfs:
        # Generate S3 key preserving directory structure
        # e.g. data/pdfs/123/456/original.pdf -> pdf-monitor/originals/123/456/original.pdf
        relative_path = Path(pdf_path).resolve().relative_to(pdf_dir_resolved)
        s3_key = f"{S3_PREFIX.rstrip('/')}/{relative_path}"
        tasks.append((pdf_path, s3_key))
    
    # Upload with thread pool
    print(f"Starting upload of {total:,} files...")
    print("Progress: [uploaded / skipped / failed / total] rate | ETA")
    print("-" * 60)
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        # Submit all tasks
        futures = {
            executor.submit(upload_file, s3_client, path, key, progress): (path, key)
            for path, key in tasks
        }
        
        # Monitor progress
        last_print = time.time()
        for future in as_completed(futures):
            # Print progress every 2 seconds
            now = time.time()
            if now - last_print >= 2:
                stats = progress.get_stats()
                print(
                    f"\r[{stats['uploaded']:,} / {stats['skipped']:,} / {stats['failed']:,} / {stats['total']:,}] "
                    f"{stats['rate']:.1f}/s | ETA: {stats['eta']:.0f}s   ",
                    end="",
                    flush=True
                )
                last_print = now
    
    # Final stats
    print()
    print()
    stats = progress.get_stats()
    print("=" * 60)
    print(f"Upload Complete!")
    print(f"  Uploaded: {stats['uploaded']:,}")
    print(f"  Skipped (already exists): {stats['skipped']:,}")
    print(f"  Failed: {stats['failed']:,}")
    print(f"  Total time: {stats['elapsed']:.1f}s")
    print(f"  Average rate: {stats['rate']:.1f} files/s")
    print()
    print(f"Files available at: s3://{S3_BUCKET}/{S3_PREFIX}")


if __name__ == "__main__":
    main()
