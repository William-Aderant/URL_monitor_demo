"""
Configuration management for PDF Monitor system.
Loads settings from environment variables with sensible defaults.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file if present
load_dotenv()


class Settings:
    """Application settings loaded from environment."""
    
    # AWS
    AWS_ACCESS_KEY_ID: str = os.getenv("AWS_ACCESS_KEY_ID", "")
    AWS_SECRET_ACCESS_KEY: str = os.getenv("AWS_SECRET_ACCESS_KEY", "")
    AWS_REGION: str = os.getenv("AWS_REGION", "us-east-1")
    AWS_LAMBDA_SCRAPER_FUNCTION: str = os.getenv("AWS_LAMBDA_SCRAPER_FUNCTION", "")
    
    # Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///data/url_monitor.db")
    
    # Storage
    PDF_STORAGE_PATH: Path = Path(os.getenv("PDF_STORAGE_PATH", "./data/pdfs"))
    
    # Processing
    OCR_TEXT_THRESHOLD: int = int(os.getenv("OCR_TEXT_THRESHOLD", "50"))
    
    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    
    # Application
    APP_NAME: str = "PDF Monitor"
    APP_VERSION: str = "1.0.0"
    
    # ==========================================================================
    # AI/ML Thresholds (REQ-003, REQ-004)
    # Configurable confidence thresholds for automated decision making
    # ==========================================================================
    
    # Action Recommendation Thresholds
    # Changes with confidence >= this threshold are auto-approved
    AUTO_APPROVE_THRESHOLD: float = float(os.getenv("AUTO_APPROVE_THRESHOLD", "0.95"))
    # Changes with confidence >= this threshold (but < auto-approve) are suggested for review
    REVIEW_THRESHOLD: float = float(os.getenv("REVIEW_THRESHOLD", "0.80"))
    # Changes with confidence < REVIEW_THRESHOLD require manual review
    
    # Form Classification Thresholds (for FormMatcher)
    # Similarity >= this = same form updated
    HIGH_SIMILARITY_THRESHOLD: float = float(os.getenv("HIGH_SIMILARITY_THRESHOLD", "0.80"))
    # Similarity < this = new form
    LOW_SIMILARITY_THRESHOLD: float = float(os.getenv("LOW_SIMILARITY_THRESHOLD", "0.50"))
    # Similarity between LOW and HIGH = uncertain, needs review
    
    # Format-only Change Handling
    # If True, format-only changes (binary diff but no text diff) are tracked but auto-dismissed
    TRACK_FORMAT_ONLY_CHANGES: bool = os.getenv("TRACK_FORMAT_ONLY_CHANGES", "True").lower() == "true"
    # If True, format-only changes are automatically marked as reviewed/dismissed
    AUTO_DISMISS_FORMAT_ONLY: bool = os.getenv("AUTO_DISMISS_FORMAT_ONLY", "True").lower() == "true"
    
    # Temporary Feature: Remove Inaccessible New Forms
    # If True, automatically disable/remove new forms (no versions) that are inaccessible
    # This is a temporary feature to clean up forms that can't be downloaded
    REMOVE_INACCESSIBLE_NEW_FORMS: bool = os.getenv("REMOVE_INACCESSIBLE_NEW_FORMS", "False").lower() == "true"
    
    # Parallel Processing
    # Maximum number of parallel workers for processing URLs (default: 10)
    # Set to 1 to disable parallel processing
    MAX_WORKERS: int = int(os.getenv("MAX_WORKERS", "100"))
    
    @classmethod
    def ensure_directories(cls) -> None:
        """Create required directories if they don't exist."""
        cls.PDF_STORAGE_PATH.mkdir(parents=True, exist_ok=True)
        Path("data").mkdir(parents=True, exist_ok=True)
    
    @classmethod
    def validate(cls) -> list[str]:
        """Validate required settings. Returns list of missing/invalid settings."""
        issues = []
        
        # AWS credentials are optional (only needed for Lambda scraper or OCR fallback)
        # but warn if partially configured
        aws_vars = [cls.AWS_ACCESS_KEY_ID, cls.AWS_SECRET_ACCESS_KEY]
        if any(aws_vars) and not all(aws_vars):
            issues.append("AWS credentials partially configured - need both ACCESS_KEY_ID and SECRET_ACCESS_KEY")
        
        # AWS Lambda function name is optional (will fall back to direct HTTP if not set)
        if cls.AWS_LAMBDA_SCRAPER_FUNCTION and not all(aws_vars):
            issues.append("AWS_LAMBDA_SCRAPER_FUNCTION is set but AWS credentials are missing")
        
        return issues


settings = Settings()


