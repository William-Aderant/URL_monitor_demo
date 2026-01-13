# Court Form PDF Monitor

A production-grade automated PDF monitoring system for court forms. Detects meaningful changes in PDF documents, stores versions, and provides a minimal web UI for review.

## Features

- **URL Registry**: Monitor multiple court form PDFs from a configurable list
- **Smart Fetching**: Uses AWS Lambda web scraper to handle JavaScript-rendered pages and extract PDF links (falls back to direct HTTP if Lambda not configured)
- **PDF Normalization**: Strips metadata and normalizes structure for deterministic comparison
- **Text Extraction**: Uses pdfplumber/pdfminer with AWS Textract OCR fallback
- **Change Detection**: Hash-based comparison with per-page granularity
- **Version Storage**: Full history with original and normalized PDFs
- **Minimal UI**: FastAPI + Jinja dashboard (designed to be replaceable)
- **CLI Interface**: Headless operation for cron/scheduled jobs

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        PDF Monitor System                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────┐   ┌──────────────┐   ┌────────────────────────┐  │
│  │   URL    │   │   Fetcher    │   │    PDF Processing      │  │
│  │ Registry │──▶│ (AWS Lambda) │──▶│  qpdf → pikepdf        │  │
│  │ (SQLite) │   │              │   │  pdfplumber → Textract │  │
│  └──────────┘   └──────────────┘   └────────────────────────┘  │
│                                              │                   │
│                                              ▼                   │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                    Change Detection                       │  │
│  │   PDF Hash + Text Hash + Per-Page Hashes → Diff           │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                              │                   │
│                         ┌────────────────────┴────────────────┐ │
│                         ▼                                      ▼ │
│                  ┌─────────────┐                    ┌──────────┐ │
│                  │   Storage   │                    │ Database │ │
│                  │ (Filesystem)│                    │ (SQLite) │ │
│                  └─────────────┘                    └──────────┘ │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

## How Change Detection Works

1. **Download**: Fetch PDF from URL (via AWS Lambda web scraper for complex pages or direct download)

2. **Normalize**: 
   - Run `qpdf` to linearize and normalize PDF structure
   - Use `pikepdf` to strip ALL metadata (timestamps, producer, XMP, document ID)
   - This ensures byte-identical source PDFs produce identical normalized output

3. **Extract Text**:
   - Try `pdfplumber` first (better layout preservation)
   - Fall back to `pdfminer.six` if needed
   - If text < threshold chars/page, trigger AWS Textract OCR

4. **Compute Hashes**:
   - SHA-256 of normalized PDF bytes (binary comparison)
   - SHA-256 of extracted text (semantic comparison)
   - Per-page text hashes (granular change detection)

5. **Compare**:
   - If text hash differs → semantic change detected
   - If only PDF hash differs → binary-only change (metadata remnants)
   - Generate diff summary and identify affected pages

6. **Store**:
   - Save original PDF, normalized PDF, extracted text
   - Record version in database with all hashes
   - Log change with affected pages and diff summary

## Requirements

- Python 3.11+
- qpdf (system package)
- AWS account (for Lambda web scraper and Textract OCR fallback)

## Installation

### 1. Clone and Setup

```bash
cd /path/to/URL_monitor_demo

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Install qpdf

```bash
# macOS
brew install qpdf

# Ubuntu/Debian
sudo apt install qpdf

# Verify installation
qpdf --version
```

### 3. Configure Environment

Create a `.env` file in the project root:

```env
# AWS Credentials (for Lambda web scraper and Textract OCR fallback)
AWS_ACCESS_KEY_ID=your-access-key
AWS_SECRET_ACCESS_KEY=your-secret-key
AWS_REGION=us-east-1

# AWS Lambda Web Scraper Function (optional - falls back to direct HTTP if not set)
# AWS_LAMBDA_SCRAPER_FUNCTION=your-lambda-function-name

# Database
DATABASE_URL=sqlite:///data/url_monitor.db

# Storage
PDF_STORAGE_PATH=./data/pdfs

# Processing Settings
OCR_TEXT_THRESHOLD=50

# Logging
LOG_LEVEL=INFO
```

### 4. Initialize Database

```bash
python cli.py init
python cli.py seed  # Add sample court form URLs
```

## Usage

### CLI (Headless)

```bash
# Run monitoring cycle for all enabled URLs
python cli.py run

# Monitor specific URL
python cli.py run --url-id 1

# Show status of all URLs
python cli.py status
```

### Web UI

```bash
# Start the web server
python main.py

# Or with uvicorn directly
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Then open http://localhost:8000 in your browser.

### API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Dashboard (HTML) |
| GET | `/url/{id}` | URL detail page (HTML) |
| GET | `/changes` | Recent changes page (HTML) |
| GET | `/api/urls` | List all monitored URLs |
| POST | `/api/urls` | Create new monitored URL |
| GET | `/api/urls/{id}` | Get URL details |
| DELETE | `/api/urls/{id}` | Delete URL and versions |
| GET | `/api/urls/{id}/versions` | List versions |
| GET | `/api/urls/{id}/versions/{vid}/pdf` | Download PDF |
| GET | `/api/urls/{id}/versions/{vid}/text` | Get extracted text |
| GET | `/api/changes` | List recent changes |
| POST | `/api/monitor/run` | Trigger monitoring cycle |
| GET | `/api/status` | System status |

## Scheduled Monitoring

### Using Cron

```bash
# Edit crontab
crontab -e

# Run every 6 hours
0 */6 * * * cd /path/to/URL_monitor_demo && /path/to/venv/bin/python cli.py run >> /var/log/pdf-monitor.log 2>&1
```

### Using systemd Timer

Create `/etc/systemd/system/pdf-monitor.service`:
```ini
[Unit]
Description=PDF Monitor

[Service]
Type=oneshot
WorkingDirectory=/path/to/URL_monitor_demo
ExecStart=/path/to/venv/bin/python cli.py run
User=your-user
```

Create `/etc/systemd/system/pdf-monitor.timer`:
```ini
[Unit]
Description=Run PDF Monitor every 6 hours

[Timer]
OnCalendar=*-*-* 00/6:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

Enable:
```bash
sudo systemctl enable pdf-monitor.timer
sudo systemctl start pdf-monitor.timer
```

## Disabling the Frontend

The web UI is intentionally thin and all logic lives in backend services. To run headlessly:

1. **Don't start the web server** - Just use CLI commands
2. **Integrate with your own system** - Import and use the services directly:

```python
from cli import MonitoringOrchestrator
from db.database import SessionLocal

db = SessionLocal()
orchestrator = MonitoringOrchestrator()
results = orchestrator.run_cycle(db)
```

3. **Use the API programmatically** - All functionality is available via REST API

## Project Structure

```
url_monitor/
├── config.py              # Configuration management
├── main.py                # FastAPI entry point
├── cli.py                 # CLI interface
├── db/
│   ├── database.py        # SQLite connection
│   ├── models.py          # SQLAlchemy models
│   └── migrations.py      # Schema management
├── fetcher/
│   ├── aws_web_scraper.py   # AWS Lambda web scraper
│   └── pdf_downloader.py    # HTTP download handler
├── pdf_processing/
│   ├── normalizer.py      # qpdf + pikepdf normalization
│   ├── text_extractor.py  # pdfplumber/pdfminer extraction
│   └── ocr_fallback.py    # AWS Textract OCR
├── diffing/
│   ├── hasher.py          # Hash computation
│   └── change_detector.py # Change detection logic
├── storage/
│   ├── file_store.py      # Filesystem abstraction
│   └── version_manager.py # Version lifecycle
├── api/
│   ├── routes.py          # FastAPI routes
│   └── schemas.py         # Pydantic models
├── templates/             # Jinja2 templates
├── static/                # CSS/JS assets
└── data/                  # Runtime data (gitignored)
    ├── pdfs/              # Stored PDF versions
    └── url_monitor.db     # SQLite database
```

## Sample URLs

The system is pre-seeded with these public court form PDFs:

1. **CA Civil Case Cover Sheet** - courts.ca.gov (CM-010)
2. **CA Summons** - courts.ca.gov (SUM-100)
3. **CA Proof of Service** - courts.ca.gov (POS-010)
4. **US Courts Civil Cover Sheet** - uscourts.gov (JS-44)
5. **CA Fee Waiver Request** - courts.ca.gov (FW-001)

## Logging

All operations are logged with structured logging (structlog):

- **Fetch failures**: URL, error, retry count
- **PDF normalization**: Original/normalized sizes, qpdf status
- **OCR triggers**: URL, reason, Textract response
- **Change detection**: Hash comparisons, affected pages
- **Version storage**: File paths, metadata

Logs include timestamps and can be parsed for monitoring/alerting.

## Troubleshooting

### qpdf not found

```bash
# Verify qpdf is installed and in PATH
which qpdf
qpdf --version
```

### AWS Lambda web scraper errors

- If using Lambda function, verify function name and IAM permissions
- Check AWS credentials are valid and have Lambda invoke permissions
- Falls back to direct HTTP if Lambda is not configured
- Some URLs may be blocked - try direct PDF URLs instead

### OCR not working

- Verify AWS credentials are set and valid
- Check IAM permissions for Textract
- PDFs > 5MB require S3 upload (not implemented in prototype)

### Database locked

SQLite can lock with concurrent access. For production:
- Use PostgreSQL instead
- Or ensure single-process access

## License

MIT


