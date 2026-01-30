# Court Form PDF Monitor

A production-grade automated PDF monitoring system for court forms. Detects meaningful changes in PDF documents, stores versions, and provides a streamlined web UI for management and review.

## Features

### Core Monitoring
- **Automated Scheduling**: Built-in scheduler for daily, weekly, or custom cron monitoring cycles
- **Smart Change Detection**: Three-tier detection (HTTP headers → quick hash → full download)
- **PDF Normalization**: Strips metadata and normalizes structure for deterministic comparison
- **Text Extraction**: Uses pdfplumber/pdfminer with AWS Textract OCR fallback

### Workflow Management
- **URL Management Page**: Add, edit, delete, and bulk upload URLs to monitor
- **Change Review Page**: Download forms, preview changes, and approve with tracking
- **Audit & Metrics Page**: Track monitoring cycles, automation rates, and performance

### Key Capabilities
- **Bulk URL Import**: Upload CSV/TXT files with multiple URLs
- **Download Tracking**: Forms downloaded as "TITLE {FORM_NUMBER}.pdf"
- **Approval Workflow**: Download required before approval, manual interventions tracked
- **Cycle Auditing**: Every monitoring cycle logged with detailed statistics

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

# Scheduling (new workflow)
SCHEDULER_ENABLED=true
DEFAULT_SCHEDULE_TIME=02:00
DEFAULT_TIMEZONE=UTC

# Download Settings
DOWNLOAD_FILENAME_MAX_LENGTH=200

# Bulk Upload Settings
BULK_UPLOAD_MAX_SIZE_MB=10
BULK_UPLOAD_MAX_URLS=1000
BULK_UPLOAD_VALIDATE_URLS=true
```

### 4. Initialize Database

```bash
python cli.py init
```

## Usage

### Quick Start

See [QUICK_START.md](QUICK_START.md) for a complete getting started guide.

### Web UI (Recommended)

```bash
# Start the web server
python main.py

# Or with uvicorn directly
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

The web UI will be available at: **http://localhost:8000**

**Main Pages:**
- `/url-management` - Manage monitored URLs
- `/change-review` - Review and approve changes
- `/audit` - View monitoring cycle history and metrics

### CLI (Headless)

```bash
# Run monitoring cycle for all enabled URLs
python cli.py run

# Monitor specific URL
python cli.py run --url-id 1

# Show status of all URLs
python cli.py status
```

### API Endpoints

**Pages:**
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/url-management` | URL Management page |
| GET | `/change-review` | Change Review page |
| GET | `/audit` | Audit & Metrics page |

**Schedule Management:**
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/schedule` | Get scheduler status |
| PUT | `/api/schedule` | Update schedule config |
| POST | `/api/monitor/run-now` | Trigger manual cycle |

**URL Management:**
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/urls` | List all monitored URLs |
| POST | `/api/urls` | Create new monitored URL |
| PUT | `/api/urls/{id}` | Update monitored URL |
| DELETE | `/api/urls/{id}` | Delete URL and versions |
| POST | `/api/urls/bulk-upload` | Bulk upload from CSV/TXT |
| GET | `/api/urls/upload-template` | Download CSV template |

**Change Management:**
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/changes-full` | List changes with full details |
| GET | `/api/changes/{id}/download` | Download change PDF |
| POST | `/api/changes/{id}/approve` | Approve change |
| POST | `/api/changes/{id}/intervention` | Record manual intervention |

**Audit & Metrics:**
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/audit/cycles` | List monitoring cycles |
| GET | `/api/audit/cycles/{id}` | Get cycle details |
| GET | `/api/audit/cycles/{id}/results` | Get URL results for cycle |
| GET | `/api/audit/stats` | Get overall statistics |
| GET | `/api/audit/trends` | Get trend data |

## Automated Scheduling

The application includes a built-in scheduler using APScheduler. Configure via the web UI or API.

### Built-in Scheduler (Recommended)

1. Start the web application: `python main.py`
2. Navigate to **URL Management** page
3. Click **Configure** next to the schedule indicator
4. Choose schedule type:
   - **Daily**: Run at a specific time (default: 02:00)
   - **Weekly**: Select days and time
   - **Custom**: Use cron expression (e.g., `0 */6 * * *`)
5. Save settings

The scheduler runs automatically when the application is running.

### Environment Configuration

```env
# Enable/disable scheduler (default: true)
SCHEDULER_ENABLED=true

# Default schedule time (HH:MM)
DEFAULT_SCHEDULE_TIME=02:00

# Timezone for scheduling
DEFAULT_TIMEZONE=UTC
```

### External Scheduling (Alternative)

If you prefer external scheduling, disable the built-in scheduler and use cron:

```bash
# Disable built-in scheduler in .env
SCHEDULER_ENABLED=false

# Add to crontab
0 */6 * * * cd /path/to/URL_monitor_demo && /path/to/venv/bin/python cli.py run >> /var/log/pdf-monitor.log 2>&1
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


