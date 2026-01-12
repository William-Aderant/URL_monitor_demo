"""
Local Test Server for URL Monitor Testing

This server provides a controllable environment to test the URL monitoring system.
You can modify the content files to simulate website updates.

Features:
- Serves main page with form listings
- Serves individual PDFs from /pdfs/
- Provides directory listing at /pdfs/ for crawler to find relocated forms
- Health check endpoint

Usage:
    python test_server.py

Then add test URLs to monitor:
    http://localhost:5001/pdfs/civ-001.pdf
    http://localhost:5001/pdfs/civ-002.pdf
    http://localhost:5001/pdfs/civ-003.pdf
"""

from flask import Flask, send_file, render_template_string, Response
import os
from datetime import datetime
from pathlib import Path

app = Flask(__name__)

# Directories for test content
TEST_DIR = "test_site"
PDF_DIR = os.path.join(TEST_DIR, "pdfs")
CONTENT_FILE = os.path.join(TEST_DIR, "content.html")

# Default HTML template when no custom content exists
DEFAULT_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Mock Court Filing System</title>
    <style>
        body { font-family: Georgia, serif; max-width: 800px; margin: 50px auto; padding: 20px; }
        h1 { color: #1a365d; border-bottom: 2px solid #1a365d; padding-bottom: 10px; }
        .filing { background: #f7fafc; padding: 15px; margin: 10px 0; border-left: 4px solid #3182ce; }
        .filing a { color: #2b6cb0; text-decoration: none; font-weight: bold; }
        .filing a:hover { text-decoration: underline; }
        .date { color: #718096; font-size: 0.9em; }
        .update-notice { background: #fefcbf; padding: 10px; border: 1px solid #d69e2e; margin: 20px 0; }
    </style>
</head>
<body>
    <h1>📋 Mock Court Filing System</h1>
    
    <div class="update-notice">
        <strong>Test Server:</strong> Edit <code>test_site/content.html</code> to change this page,
        or add/modify PDFs in <code>test_site/pdfs/</code>
    </div>
    
    <p>Last updated: <span class="date">{timestamp}</span></p>
    
    <h2>Recent Filings</h2>
    
    {pdf_list}
    
    <h2>Announcements</h2>
    <div class="filing">
        <p>No new announcements at this time.</p>
    </div>
</body>
</html>
"""

# Directory listing HTML template for /pdfs/
DIRECTORY_LISTING_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>PDF Directory - Court Filings</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; 
               max-width: 900px; margin: 50px auto; padding: 20px; background: #f8fafc; }}
        h1 {{ color: #1e40af; margin-bottom: 5px; }}
        .subtitle {{ color: #64748b; margin-bottom: 30px; }}
        table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 8px; 
                overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
        th {{ background: #1e40af; color: white; padding: 12px 15px; text-align: left; }}
        td {{ padding: 12px 15px; border-bottom: 1px solid #e2e8f0; }}
        tr:hover {{ background: #f1f5f9; }}
        a {{ color: #2563eb; text-decoration: none; font-weight: 500; }}
        a:hover {{ text-decoration: underline; }}
        .size {{ color: #64748b; font-family: monospace; }}
        .date {{ color: #64748b; }}
        .form-number {{ background: #dbeafe; color: #1e40af; padding: 2px 8px; border-radius: 4px; 
                       font-family: monospace; font-size: 0.9em; }}
        .back-link {{ margin-bottom: 20px; display: block; }}
    </style>
</head>
<body>
    <a href="/" class="back-link">← Back to Court Filings</a>
    <h1>📂 PDF Directory</h1>
    <p class="subtitle">Available court form documents</p>
    
    <table>
        <thead>
            <tr>
                <th>Filename</th>
                <th>Form #</th>
                <th>Size</th>
                <th>Modified</th>
            </tr>
        </thead>
        <tbody>
            {file_rows}
        </tbody>
    </table>
    
    <p style="margin-top: 20px; color: #64748b;">
        {file_count} PDF file(s) available
    </p>
</body>
</html>
"""


def extract_form_number(filename: str) -> str:
    """Extract form number from filename like civ-001.pdf -> CIV-001"""
    import re
    match = re.search(r'([a-zA-Z]{2,4})-?(\d{2,4})', filename)
    if match:
        return f"{match.group(1).upper()}-{match.group(2)}"
    return "-"


def format_file_size(size_bytes: int) -> str:
    """Format file size in human readable format."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"


def get_pdf_list_html():
    """Generate HTML list of available PDFs for the main page"""
    if not os.path.exists(PDF_DIR):
        return '<div class="filing"><p>No PDF filings available.</p></div>'
    
    pdfs = [f for f in os.listdir(PDF_DIR) if f.lower().endswith('.pdf')]
    
    if not pdfs:
        return '<div class="filing"><p>No PDF filings available.</p></div>'
    
    html_parts = []
    for pdf in sorted(pdfs):
        filepath = os.path.join(PDF_DIR, pdf)
        mod_time = datetime.fromtimestamp(os.path.getmtime(filepath))
        html_parts.append(f'''
        <div class="filing">
            <a href="/pdfs/{pdf}">{pdf}</a>
            <p class="date">Filed: {mod_time.strftime("%B %d, %Y at %I:%M %p")}</p>
        </div>
        ''')
    
    return '\n'.join(html_parts)


def get_pdf_directory_html():
    """Generate HTML directory listing for /pdfs/ endpoint"""
    if not os.path.exists(PDF_DIR):
        return DIRECTORY_LISTING_HTML.format(file_rows="", file_count=0)
    
    pdfs = [f for f in os.listdir(PDF_DIR) if f.lower().endswith('.pdf')]
    
    if not pdfs:
        return DIRECTORY_LISTING_HTML.format(
            file_rows='<tr><td colspan="4" style="text-align: center; color: #64748b;">No PDF files found</td></tr>',
            file_count=0
        )
    
    rows = []
    for pdf in sorted(pdfs):
        filepath = os.path.join(PDF_DIR, pdf)
        stat = os.stat(filepath)
        mod_time = datetime.fromtimestamp(stat.st_mtime)
        form_num = extract_form_number(pdf)
        
        rows.append(f'''
            <tr>
                <td><a href="/pdfs/{pdf}">{pdf}</a></td>
                <td><span class="form-number">{form_num}</span></td>
                <td class="size">{format_file_size(stat.st_size)}</td>
                <td class="date">{mod_time.strftime("%Y-%m-%d %H:%M")}</td>
            </tr>
        ''')
    
    return DIRECTORY_LISTING_HTML.format(
        file_rows='\n'.join(rows),
        file_count=len(pdfs)
    )


@app.route('/')
def index():
    """Serve the main page - either custom content or default template"""
    
    # If custom content file exists, serve it
    if os.path.exists(CONTENT_FILE):
        with open(CONTENT_FILE, 'r') as f:
            return f.read()
    
    # Otherwise, serve the default template with dynamic content
    timestamp = datetime.now().strftime("%B %d, %Y at %I:%M %p")
    pdf_list = get_pdf_list_html()
    
    return DEFAULT_HTML.format(timestamp=timestamp, pdf_list=pdf_list)


@app.route('/pdfs/')
def pdf_directory():
    """
    Serve directory listing of PDFs.
    
    This endpoint is crucial for the link crawler - when a monitored PDF
    returns 404, the crawler visits this parent URL to find all available
    PDFs and match by form number.
    """
    return get_pdf_directory_html()


@app.route('/pdfs/<filename>')
def serve_pdf(filename):
    """Serve PDF files from the test_site/pdfs directory"""
    filepath = os.path.join(PDF_DIR, filename)
    
    if os.path.exists(filepath):
        return send_file(filepath, mimetype='application/pdf')
    
    return "PDF not found", 404


@app.route('/health')
def health():
    """Health check endpoint"""
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


if __name__ == '__main__':
    # Create test directories if they don't exist
    os.makedirs(PDF_DIR, exist_ok=True)
    
    print("\n" + "="*60)
    print("🧪 URL Monitor Test Server")
    print("="*60)
    print(f"\n📍 Server running at: http://localhost:5001")
    print(f"\n📁 Content locations:")
    print(f"   • HTML content: {os.path.abspath(CONTENT_FILE)}")
    print(f"   • PDF files:    {os.path.abspath(PDF_DIR)}/")
    print(f"\n📋 Test URLs to monitor:")
    print(f"   • http://localhost:5001/pdfs/civ-001.pdf")
    print(f"   • http://localhost:5001/pdfs/civ-002.pdf")
    print(f"   • http://localhost:5001/pdfs/civ-003.pdf")
    print(f"\n💡 Testing workflow:")
    print(f"   1. python test_site/simulate_update.py revert")
    print(f"   2. ./venv/bin/python cli.py run  (baseline)")
    print(f"   3. python test_site/simulate_update.py <scenario>")
    print(f"   4. ./venv/bin/python cli.py run  (detect changes)")
    print("\n" + "="*60 + "\n")
    
    app.run(port=5001, debug=True)
