# Quick Start Guide - PDF Monitor Automated Workflow

This guide will help you get started with the new automated PDF monitoring workflow.

## Overview

The PDF Monitor has been redesigned with a streamlined 3-page interface:

1. **URL Management** - Add, edit, and manage monitored URLs
2. **Change Review** - Review detected changes, download forms, and approve
3. **Audit & Metrics** - Track monitoring cycles and performance

## Installation

### 1. Install Dependencies

```bash
# Create virtual environment (recommended)
python3 -m venv venv
source venv/bin/activate  # macOS/Linux
# or: venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements.txt
```

### 2. Install qpdf (Required for PDF normalization)

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
# AWS Credentials (required for title extraction and OCR)
AWS_ACCESS_KEY_ID=your-access-key
AWS_SECRET_ACCESS_KEY=your-secret-key
AWS_REGION=us-east-1

# Database (SQLite default)
DATABASE_URL=sqlite:///data/url_monitor.db

# Storage
PDF_STORAGE_PATH=./data/pdfs

# Scheduler (enabled by default)
SCHEDULER_ENABLED=true
DEFAULT_SCHEDULE_TIME=02:00
DEFAULT_TIMEZONE=UTC
```

### 4. Initialize Database

```bash
python cli.py init
```

## Starting the Application

```bash
# Start the web server
python main.py

# Or with uvicorn
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Access the application at: **http://localhost:8000**

## Quick Workflow

### Step 1: Add URLs to Monitor

1. Navigate to **URL Management** (first page)
2. Click **+ Add URL** to add individual URLs
3. Or click **📤 Bulk Upload** to import from CSV/TXT

**CSV Format:**
```csv
URL,Title,State,Jurisdiction
https://www.courts.ca.gov/documents/cm010.pdf,Civil Case Cover Sheet,California,courts.ca.gov
```

### Step 2: Configure Monitoring Schedule

1. Click **Configure** next to the schedule indicator
2. Choose schedule type:
   - **Daily**: Set time (default 02:00)
   - **Weekly**: Select days and time
   - **Custom**: Use cron expression
3. Save settings

The scheduler will automatically run at the configured times.

### Step 3: Run Initial Monitoring

Click **▶ Run Cycle Now** to run the first monitoring cycle and establish baseline versions.

### Step 4: Review Detected Changes

When changes are detected:

1. Navigate to **Change Review** (second page)
2. Review the detected changes
3. Click **👁️ Preview** to see visual diff
4. Click **📥 Download** to download the new form
5. Click **✓ Approve** to approve the change (requires download first)

**Download Naming Format:** Downloaded files are named as `"TITLE {FORM_NUMBER}.pdf"`
Example: `Petition for Name Change MC-031.pdf`

### Step 5: Monitor Audit Metrics

Navigate to **Audit** (third page) to see:

- Total monitoring cycles
- Changes detected
- Automation rate (downloads without manual intervention)
- Detailed cycle history
- Success/failure rates

## Key Features

### Automatic Scheduling

- Monitoring cycles run automatically based on your schedule
- Configure daily, weekly, or custom cron schedules
- View next scheduled run in the header

### Bulk URL Import

Upload CSV or TXT files with multiple URLs:

**Required fields:** URL, State, Jurisdiction  
**Optional fields:** Title (auto-extracted if blank)

### Download Tracking

- Track how many times each change was downloaded
- Approval requires at least one download
- Downloaded filename stored for audit

### Manual Intervention Tracking

- Edits to title or URL are tracked as manual interventions
- Audit page shows automation rate vs manual intervention rate
- Helps measure tool effectiveness

### Visual Diff Preview

- See side-by-side or overlay comparison
- Navigate through multi-page documents
- Highlight changed regions

## API Access

All functionality is available via REST API:

```bash
# Get scheduler status
curl http://localhost:8000/api/schedule

# Trigger manual cycle
curl -X POST http://localhost:8000/api/monitor/run-now

# List changes
curl http://localhost:8000/api/changes-full

# Download a change
curl http://localhost:8000/api/changes/{id}/download -o form.pdf

# Approve a change
curl -X POST http://localhost:8000/api/changes/{id}/approve
```

## Running Tests

```bash
# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_scheduler.py -v
```

## Troubleshooting

### Scheduler Not Running

1. Check `SCHEDULER_ENABLED=true` in `.env`
2. Verify schedule is enabled in UI configuration
3. Check logs for scheduler initialization

### Download Naming Issues

- If title is missing, file is named "Untitled.pdf"
- Special characters are replaced with underscores
- Filename truncated to 200 characters max

### Approval Button Disabled

- Download the form at least once before approving
- Refresh page if download was recent

### AWS Features Not Working

- Verify AWS credentials in `.env`
- Check IAM permissions for Textract/Bedrock
- Title extraction requires AWS services

## Support

For issues or feature requests, please open a GitHub issue.
