"""
Database migration utilities.
For this prototype, we use simple create_all() migrations.
In production, use Alembic for proper versioned migrations.
"""

import structlog
from sqlalchemy import inspect

from db.database import engine, Base, init_db
from db.models import MonitoredURL, PDFVersion, ChangeLog  # noqa: F401

logger = structlog.get_logger()


def check_tables_exist() -> dict[str, bool]:
    """Check which tables exist in the database."""
    inspector = inspect(engine)
    existing_tables = inspector.get_table_names()
    
    required_tables = ["monitored_urls", "pdf_versions", "change_logs"]
    return {table: table in existing_tables for table in required_tables}


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
    else:
        logger.info("All tables exist", tables=list(table_status.keys()))


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

