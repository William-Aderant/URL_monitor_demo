"""
Database migration utilities.
For this prototype, we use simple create_all() migrations.
In production, use Alembic for proper versioned migrations.
"""

import structlog
from sqlalchemy import inspect, text

from db.database import engine, Base, init_db
from db.models import MonitoredURL, PDFVersion, ChangeLog  # noqa: F401

logger = structlog.get_logger()


def check_tables_exist() -> dict[str, bool]:
    """Check which tables exist in the database."""
    inspector = inspect(engine)
    existing_tables = inspector.get_table_names()
    
    required_tables = ["monitored_urls", "pdf_versions", "change_logs"]
    return {table: table in existing_tables for table in required_tables}


def migrate_title_columns() -> None:
    """
    Add title extraction columns to pdf_versions table if they don't exist.
    This handles upgrading existing databases.
    """
    inspector = inspect(engine)
    
    if "pdf_versions" not in inspector.get_table_names():
        return  # Table doesn't exist yet, will be created with all columns
    
    existing_columns = [col["name"] for col in inspector.get_columns("pdf_versions")]
    
    # New columns to add for title extraction
    new_columns = [
        ("formatted_title", "VARCHAR(512)"),
        ("form_number", "VARCHAR(100)"),
        ("title_confidence", "FLOAT"),
        ("title_extraction_method", "VARCHAR(50)"),
        ("revision_date", "VARCHAR(50)")  # REQ-006: Revision date extraction
    ]
    
    with engine.connect() as conn:
        for col_name, col_type in new_columns:
            if col_name not in existing_columns:
                logger.info(f"Adding column {col_name} to pdf_versions")
                conn.execute(text(f"ALTER TABLE pdf_versions ADD COLUMN {col_name} {col_type}"))
        conn.commit()


def migrate_change_detection_columns() -> None:
    """
    Add enhanced change detection columns to monitored_urls and change_logs tables.
    """
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    
    with engine.connect() as conn:
        # Add parent_page_url to monitored_urls
        if "monitored_urls" in tables:
            existing = [col["name"] for col in inspector.get_columns("monitored_urls")]
            if "parent_page_url" not in existing:
                logger.info("Adding column parent_page_url to monitored_urls")
                conn.execute(text("ALTER TABLE monitored_urls ADD COLUMN parent_page_url VARCHAR(2048)"))
        
        # Add new columns to change_logs
        if "change_logs" in tables:
            existing = [col["name"] for col in inspector.get_columns("change_logs")]
            new_columns = [
                ("match_type", "VARCHAR(50)"),
                ("similarity_score", "FLOAT"),
                ("relocated_from_url", "VARCHAR(2048)"),
                ("diff_image_path", "VARCHAR(512)"),
                ("reviewed", "BOOLEAN DEFAULT FALSE"),
                ("reviewed_at", "DATETIME"),
                ("reviewed_by", "VARCHAR(255)"),
                ("review_notes", "TEXT")
            ]
            for col_name, col_type in new_columns:
                if col_name not in existing:
                    logger.info(f"Adding column {col_name} to change_logs")
                    conn.execute(text(f"ALTER TABLE change_logs ADD COLUMN {col_name} {col_type}"))
        
        conn.commit()


def migrate_review_workflow_columns() -> None:
    """
    Add review workflow and action recommendation columns to change_logs.
    Implements REQ-001, REQ-003, REQ-004, REQ-013, REQ-014 from PoC.
    """
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    
    if "change_logs" not in tables:
        return  # Table will be created with all columns
    
    existing = [col["name"] for col in inspector.get_columns("change_logs")]
    
    # New columns for action recommendations and review workflow
    new_columns = [
        # AI Action Recommendation (REQ-001, REQ-003)
        ("recommended_action", "VARCHAR(50)"),  # auto_approve, review_suggested, manual_required, etc.
        ("action_confidence", "FLOAT"),  # Confidence in the recommendation
        ("action_rationale", "TEXT"),  # Human-readable explanation
        
        # Review workflow (REQ-013, REQ-014)
        ("review_status", "VARCHAR(50) DEFAULT 'pending'"),  # pending, approved, rejected, deferred
        
        # Classification override (REQ-004)
        ("classification_override", "VARCHAR(50)"),  # Human override of AI classification
        ("override_reason", "TEXT"),  # Reason for override
    ]
    
    with engine.connect() as conn:
        for col_name, col_type in new_columns:
            if col_name not in existing:
                logger.info(f"Adding column {col_name} to change_logs")
                conn.execute(text(f"ALTER TABLE change_logs ADD COLUMN {col_name} {col_type}"))
        conn.commit()


def migrate_fast_detection_columns() -> None:
    """
    Add fast change detection columns to monitored_urls table.
    Implements three-tier change detection: HTTP headers and quick hash.
    """
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    
    if "monitored_urls" not in tables:
        return  # Table will be created with all columns
    
    existing = [col["name"] for col in inspector.get_columns("monitored_urls")]
    
    # New columns for fast change detection
    new_columns = [
        # Tier 1: HTTP headers
        ("last_modified_header", "DATETIME"),
        ("etag_header", "VARCHAR(255)"),
        ("content_length_header", "INTEGER"),
        # Tier 2: Quick hash
        ("quick_hash", "VARCHAR(64)"),
    ]
    
    with engine.connect() as conn:
        for col_name, col_type in new_columns:
            if col_name not in existing:
                logger.info(f"Adding column {col_name} to monitored_urls")
                conn.execute(text(f"ALTER TABLE monitored_urls ADD COLUMN {col_name} {col_type}"))
        conn.commit()


def migrate_state_domain_columns() -> None:
    """
    Add state and domain_category columns to monitored_urls table.
    Also backfills existing Alaska URLs with appropriate values.
    """
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    
    if "monitored_urls" not in tables:
        return  # Table will be created with all columns
    
    existing = [col["name"] for col in inspector.get_columns("monitored_urls")]
    
    # New columns for state/domain organization
    new_columns = [
        ("state", "VARCHAR(50)"),
        ("domain_category", "VARCHAR(100)"),
    ]
    
    with engine.connect() as conn:
        for col_name, col_type in new_columns:
            if col_name not in existing:
                logger.info(f"Adding column {col_name} to monitored_urls")
                conn.execute(text(f"ALTER TABLE monitored_urls ADD COLUMN {col_name} {col_type}"))
        
        # Backfill existing Alaska URLs
        conn.execute(text("""
            UPDATE monitored_urls 
            SET state = 'Alaska', domain_category = 'courts.alaska.gov'
            WHERE url LIKE '%courts.alaska.gov%' AND state IS NULL
        """))
        
        # Backfill existing California URLs (courts.ca.gov)
        conn.execute(text("""
            UPDATE monitored_urls 
            SET state = 'California', domain_category = 'courts.ca.gov'
            WHERE url LIKE '%courts.ca.gov%' AND state IS NULL
        """))
        
        # Backfill localhost test URLs (deprecated - kept for compatibility)
        conn.execute(text("""
            UPDATE monitored_urls 
            SET state = 'Test', domain_category = 'localhost'
            WHERE url LIKE '%localhost%' AND state IS NULL
        """))
        
        conn.commit()
        logger.info("State and domain columns migrated and backfilled")


def migrate_kendra_columns() -> None:
    """
    Add AWS Kendra tracking columns to pdf_versions table.
    """
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    
    if "pdf_versions" not in tables:
        return  # Table will be created with all columns
    
    existing = [col["name"] for col in inspector.get_columns("pdf_versions")]
    
    # New columns for Kendra indexing tracking
    new_columns = [
        ("kendra_document_id", "VARCHAR(255)"),
        ("kendra_indexed_at", "DATETIME"),
        ("kendra_index_status", "VARCHAR(50)"),
    ]
    
    with engine.connect() as conn:
        for col_name, col_type in new_columns:
            if col_name not in existing:
                logger.info(f"Adding column {col_name} to pdf_versions")
                conn.execute(text(f"ALTER TABLE pdf_versions ADD COLUMN {col_name} {col_type}"))
        conn.commit()
        logger.info("Kendra columns migrated")


def migrate_idp_enrichment_columns() -> None:
    """
    Add AWS IDP enrichment columns to pdf_versions table.
    Implements additive Comprehend, Textract Forms/Queries, and A2I features.
    All columns are optional and do not affect existing functionality.
    """
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    
    if "pdf_versions" not in tables:
        return  # Table will be created with all columns
    
    existing = [col["name"] for col in inspector.get_columns("pdf_versions")]
    
    # New columns for IDP enrichment (all optional)
    new_columns = [
        # Comprehend Classification
        ("comprehend_document_type", "VARCHAR(100)"),
        ("comprehend_document_type_confidence", "FLOAT"),
        
        # Comprehend NER (entities as JSON)
        ("comprehend_entities", "JSON"),
        
        # Textract Forms (key-value pairs)
        ("textract_form_kv_pairs", "JSON"),
        ("textract_form_confidence", "FLOAT"),
        
        # Textract Tables
        ("textract_tables", "JSON"),
        
        # Textract Queries
        ("textract_queries_results", "JSON"),
        
        # Textract Signatures
        ("textract_signatures", "JSON"),
        
        # IDP Enrichment Status
        ("idp_enrichment_status", "VARCHAR(50)"),
        ("idp_enrichment_at", "DATETIME"),
        ("idp_enrichment_error", "TEXT"),
        
        # A2I Human Review
        ("a2i_human_loop_arn", "VARCHAR(512)"),
        ("a2i_human_loop_status", "VARCHAR(50)"),
        ("a2i_human_loop_output", "JSON"),
    ]
    
    with engine.connect() as conn:
        for col_name, col_type in new_columns:
            if col_name not in existing:
                logger.info(f"Adding IDP enrichment column {col_name} to pdf_versions")
                conn.execute(text(f"ALTER TABLE pdf_versions ADD COLUMN {col_name} {col_type}"))
        conn.commit()
        logger.info("IDP enrichment columns migrated")


def run_migrations() -> None:
    """
    Run database migrations.
    Creates all tables if they don't exist.
    """
    logger.info("Checking database schema")
    table_status = check_tables_exist()
    
    missing_tables = [t for t, exists in table_status.items() if not exists]
    
    if missing_tables:
        logger.info("Creating missing tables", tables=missing_tables)
        init_db()
        # Run backfill migrations for newly created tables
        migrate_state_domain_columns()
    else:
        logger.info("All tables exist", tables=list(table_status.keys()))
        # Run column migrations for existing tables
        migrate_title_columns()
        migrate_change_detection_columns()
        migrate_review_workflow_columns()
        migrate_fast_detection_columns()
        migrate_state_domain_columns()
        migrate_kendra_columns()
        # AWS IDP enrichment columns (additive)
        migrate_idp_enrichment_columns()


def seed_sample_urls(db_session) -> None:
    """
    Seed database with sample court form URLs for testing.
    """
    sample_urls = [
        {
            "name": "CA Judicial Council - Civil Case Cover Sheet",
            "url": "https://www.courts.ca.gov/documents/cm010.pdf",
            "description": "California Civil Case Cover Sheet (Form CM-010)"
        },
        {
            "name": "CA Judicial Council - Summons",
            "url": "https://www.courts.ca.gov/documents/sum100.pdf", 
            "description": "California Summons (Form SUM-100)"
        },
        {
            "name": "CA Judicial Council - Proof of Service",
            "url": "https://www.courts.ca.gov/documents/pos010.pdf",
            "description": "California Proof of Service of Summons (Form POS-010)"
        },
        {
            "name": "US Courts - Civil Cover Sheet",
            "url": "https://www.uscourts.gov/sites/default/files/js_044.pdf",
            "description": "Federal Civil Cover Sheet (Form JS-44)"
        },
        {
            "name": "CA Judicial Council - Fee Waiver Request",
            "url": "https://www.courts.ca.gov/documents/fw001.pdf",
            "description": "California Request to Waive Court Fees (Form FW-001)"
        }
    ]
    
    for url_data in sample_urls:
        existing = db_session.query(MonitoredURL).filter_by(url=url_data["url"]).first()
        if not existing:
            monitored_url = MonitoredURL(**url_data)
            db_session.add(monitored_url)
            logger.info("Added sample URL", name=url_data["name"])
        else:
            logger.info("URL already exists", name=url_data["name"])
    
    db_session.commit()
    logger.info("Sample URLs seeded successfully")


if __name__ == "__main__":
    # Run migrations when executed directly
    import structlog
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer()
        ]
    )
    run_migrations()

