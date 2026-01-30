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
    
    # AWS - Credentials are optional here; boto3 uses the default credential chain:
    # 1. Environment variables (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY)
    # 2. Shared credentials file (~/.aws/credentials from 'aws configure')
    # 3. IAM role (for EC2/Lambda)
    # If these are empty, boto3 will automatically use credentials from 'aws configure'
    AWS_ACCESS_KEY_ID: str = os.getenv("AWS_ACCESS_KEY_ID", "")
    AWS_SECRET_ACCESS_KEY: str = os.getenv("AWS_SECRET_ACCESS_KEY", "")
    AWS_REGION: str = os.getenv("AWS_REGION", "us-east-1")
    AWS_LAMBDA_SCRAPER_FUNCTION: str = os.getenv("AWS_LAMBDA_SCRAPER_FUNCTION", "")
    
    # AWS Kendra
    AWS_KENDRA_INDEX_ID: str = os.getenv("AWS_KENDRA_INDEX_ID", "")
    AWS_KENDRA_DATA_SOURCE_ID: str = os.getenv("AWS_KENDRA_DATA_SOURCE_ID", "")
    KENDRA_INDEXING_ENABLED: bool = os.getenv("KENDRA_INDEXING_ENABLED", "False").lower() == "true"
    KENDRA_SEARCH_ENABLED: bool = os.getenv("KENDRA_SEARCH_ENABLED", "False").lower() == "true"
    
    # ==========================================================================
    # AWS IDP Features (Additive - opt-in via feature flags)
    # Comprehend, Textract Forms/Queries, A2I enrichment
    # ==========================================================================
    
    # Amazon Comprehend Settings
    COMPREHEND_ENABLED: bool = os.getenv("COMPREHEND_ENABLED", "False").lower() == "true"
    COMPREHEND_CLASSIFICATION_ENABLED: bool = os.getenv("COMPREHEND_CLASSIFICATION_ENABLED", "False").lower() == "true"
    COMPREHEND_NER_ENABLED: bool = os.getenv("COMPREHEND_NER_ENABLED", "False").lower() == "true"
    # Custom classifier endpoint ARN (optional - uses built-in if not set)
    COMPREHEND_CLASSIFIER_ARN: str = os.getenv("COMPREHEND_CLASSIFIER_ARN", "")
    # Custom entity recognizer endpoint ARN (optional)
    COMPREHEND_ENTITY_RECOGNIZER_ARN: str = os.getenv("COMPREHEND_ENTITY_RECOGNIZER_ARN", "")
    
    # Amazon Textract Advanced Features (beyond current OCR)
    TEXTRACT_FORMS_ENABLED: bool = os.getenv("TEXTRACT_FORMS_ENABLED", "False").lower() == "true"
    TEXTRACT_TABLES_ENABLED: bool = os.getenv("TEXTRACT_TABLES_ENABLED", "False").lower() == "true"
    TEXTRACT_QUERIES_ENABLED: bool = os.getenv("TEXTRACT_QUERIES_ENABLED", "False").lower() == "true"
    TEXTRACT_SIGNATURES_ENABLED: bool = os.getenv("TEXTRACT_SIGNATURES_ENABLED", "False").lower() == "true"
    # Default queries for Textract Queries API
    TEXTRACT_DEFAULT_QUERIES: str = os.getenv(
        "TEXTRACT_DEFAULT_QUERIES",
        "What is the form number?;What is the revision date?;What is the title?"
    )
    
    # Amazon A2I (Augmented AI) for human-in-the-loop review
    A2I_ENABLED: bool = os.getenv("A2I_ENABLED", "False").lower() == "true"
    A2I_FLOW_DEFINITION_ARN: str = os.getenv("A2I_FLOW_DEFINITION_ARN", "")
    A2I_WORKTEAM_ARN: str = os.getenv("A2I_WORKTEAM_ARN", "")
    # Confidence threshold below which items are sent to A2I
    A2I_CONFIDENCE_THRESHOLD: float = float(os.getenv("A2I_CONFIDENCE_THRESHOLD", "0.70"))
    
    # Lambda Enrichment Pipeline
    LAMBDA_ENRICHMENT_ENABLED: bool = os.getenv("LAMBDA_ENRICHMENT_ENABLED", "False").lower() == "true"
    AWS_LAMBDA_ENRICHMENT_FUNCTION: str = os.getenv("AWS_LAMBDA_ENRICHMENT_FUNCTION", "")
    # S3 bucket for enrichment pipeline (optional)
    ENRICHMENT_S3_BUCKET: str = os.getenv("ENRICHMENT_S3_BUCKET", "")
    
    # IDP Enrichment general settings
    IDP_ENRICHMENT_ASYNC: bool = os.getenv("IDP_ENRICHMENT_ASYNC", "True").lower() == "true"
    
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
    
    # ==========================================================================
    # Scheduling Configuration
    # Automated monitoring cycle settings
    # ==========================================================================
    
    # Enable/disable the automatic scheduler
    SCHEDULER_ENABLED: bool = os.getenv("SCHEDULER_ENABLED", "True").lower() == "true"
    # Default time for daily schedule (HH:MM format in 24-hour time)
    DEFAULT_SCHEDULE_TIME: str = os.getenv("DEFAULT_SCHEDULE_TIME", "02:00")
    # Default timezone for scheduling
    DEFAULT_TIMEZONE: str = os.getenv("DEFAULT_TIMEZONE", "UTC")
    
    # ==========================================================================
    # Download Configuration
    # PDF download and filename settings
    # ==========================================================================
    
    # Maximum filename length for downloaded PDFs
    DOWNLOAD_FILENAME_MAX_LENGTH: int = int(os.getenv("DOWNLOAD_FILENAME_MAX_LENGTH", "200"))
    # Characters to replace in filenames (mapped to underscore)
    DOWNLOAD_FILENAME_INVALID_CHARS: str = os.getenv("DOWNLOAD_FILENAME_INVALID_CHARS", r'<>:"/\|?*')
    
    # ==========================================================================
    # Bulk Upload Configuration
    # CSV/TXT file import settings
    # ==========================================================================
    
    # Maximum file size for bulk uploads (in MB)
    BULK_UPLOAD_MAX_SIZE_MB: int = int(os.getenv("BULK_UPLOAD_MAX_SIZE_MB", "10"))
    # Whether to validate URL accessibility during bulk upload
    BULK_UPLOAD_VALIDATE_URLS: bool = os.getenv("BULK_UPLOAD_VALIDATE_URLS", "True").lower() == "true"
    # Maximum URLs per bulk upload
    BULK_UPLOAD_MAX_URLS: int = int(os.getenv("BULK_UPLOAD_MAX_URLS", "1000"))
    
    @classmethod
    def ensure_directories(cls) -> None:
        """Create required directories if they don't exist."""
        cls.PDF_STORAGE_PATH.mkdir(parents=True, exist_ok=True)
        Path("data").mkdir(parents=True, exist_ok=True)
    
    @classmethod
    def validate(cls) -> list[str]:
        """Validate required settings. Returns list of missing/invalid settings."""
        issues = []
        
        # AWS credentials: If env vars are partially set, warn.
        # Otherwise, boto3 will use the default credential chain (aws configure, IAM role, etc.)
        aws_vars = [cls.AWS_ACCESS_KEY_ID, cls.AWS_SECRET_ACCESS_KEY]
        if any(aws_vars) and not all(aws_vars):
            issues.append("AWS credentials partially configured in env - need both ACCESS_KEY_ID and SECRET_ACCESS_KEY, or remove both to use 'aws configure' credentials")
        
        # Kendra index ID is required if Kendra features are enabled
        if (cls.KENDRA_INDEXING_ENABLED or cls.KENDRA_SEARCH_ENABLED) and not cls.AWS_KENDRA_INDEX_ID:
            issues.append("KENDRA_INDEXING_ENABLED or KENDRA_SEARCH_ENABLED is True but AWS_KENDRA_INDEX_ID is not set")
        
        # A2I requires flow definition ARN
        if cls.A2I_ENABLED and not cls.A2I_FLOW_DEFINITION_ARN:
            issues.append("A2I_ENABLED is True but A2I_FLOW_DEFINITION_ARN is not set")
        
        # Lambda enrichment requires function name
        if cls.LAMBDA_ENRICHMENT_ENABLED and not cls.AWS_LAMBDA_ENRICHMENT_FUNCTION:
            issues.append("LAMBDA_ENRICHMENT_ENABLED is True but AWS_LAMBDA_ENRICHMENT_FUNCTION is not set")
        
        return issues


settings = Settings()


