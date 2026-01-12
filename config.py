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
    
    # Firecrawl
    FIRECRAWL_API_KEY: str = os.getenv("FIRECRAWL_API_KEY", "")
    
    # AWS
    AWS_ACCESS_KEY_ID: str = os.getenv("AWS_ACCESS_KEY_ID", "")
    AWS_SECRET_ACCESS_KEY: str = os.getenv("AWS_SECRET_ACCESS_KEY", "")
    AWS_REGION: str = os.getenv("AWS_REGION", "us-east-1")
    
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
    
    @classmethod
    def ensure_directories(cls) -> None:
        """Create required directories if they don't exist."""
        cls.PDF_STORAGE_PATH.mkdir(parents=True, exist_ok=True)
        Path("data").mkdir(parents=True, exist_ok=True)
    
    @classmethod
    def validate(cls) -> list[str]:
        """Validate required settings. Returns list of missing/invalid settings."""
        issues = []
        
        if not cls.FIRECRAWL_API_KEY:
            issues.append("FIRECRAWL_API_KEY is not set")
        
        # AWS credentials are optional (only needed for OCR fallback)
        # but warn if partially configured
        aws_vars = [cls.AWS_ACCESS_KEY_ID, cls.AWS_SECRET_ACCESS_KEY]
        if any(aws_vars) and not all(aws_vars):
            issues.append("AWS credentials partially configured - need both ACCESS_KEY_ID and SECRET_ACCESS_KEY")
        
        return issues


settings = Settings()


