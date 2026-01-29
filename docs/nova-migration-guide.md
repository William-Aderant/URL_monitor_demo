# Migration Guide: Textract+Claude to Amazon Nova 2 Lite

This guide covers migrating from the existing Textract+Claude document processing workflow to using Amazon Nova 2 Lite for direct PDF analysis via Amazon Bedrock.

## Overview

### Current Architecture (Textract + Claude)

```
PDF File → Convert to Image → Textract OCR → Extracted Text → Claude (Bedrock) → Results
```

The existing `TitleExtractor` in `services/title_extractor.py` uses a two-step process:
1. Convert PDF to image and extract text using Amazon Textract
2. Send extracted text to Claude via Bedrock for title/form identification

### New Architecture (Nova 2 Lite)

```
PDF File → Nova 2 Lite (Bedrock) → Results
```

Nova 2 Lite processes PDFs natively, eliminating the OCR step entirely.

## Comparison

| Aspect | Textract + Claude | Nova 2 Lite |
|--------|------------------|-------------|
| **API Calls** | 2 (Textract + Bedrock) | 1 (Bedrock only) |
| **Latency** | Higher (two services) | Lower (single call) |
| **Cost** | Textract pricing + Claude pricing | Nova pricing only |
| **PDF Size Limit** | 5MB sync Textract | 25MB direct, unlimited via S3 |
| **Document Context** | Text only (loses layout) | Full layout preserved |
| **Supported Formats** | PDF (via image) | PDF, DOCX, XLSX, HTML, Markdown, CSV, TXT |

## Prerequisites

### 1. Enable Nova Model Access

1. Open the [Amazon Bedrock Console](https://console.aws.amazon.com/bedrock/)
2. Navigate to **Model access** in the left sidebar
3. Click **Manage model access**
4. Enable `Amazon Nova 2 Lite` (amazon.nova-2-lite-v1:0)
5. Click **Save changes**

Note: Model access changes may take a few minutes to propagate.

### 2. Configure IAM Permissions

Attach the IAM policy from `iam/bedrock-nova-policy.json` to your execution role (EC2 instance role, Lambda role, or IAM user).

**Minimum required permissions:**

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "BedrockNovaInvoke",
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream"
      ],
      "Resource": [
        "arn:aws:bedrock:*::foundation-model/amazon.nova-2-lite-v1:0",
        "arn:aws:bedrock:*::foundation-model/amazon.nova-*"
      ]
    }
  ]
}
```

**For large files (>25MB) via S3, add:**

```json
{
  "Sid": "BedrockNovaS3Access",
  "Effect": "Allow",
  "Action": ["s3:GetObject"],
  "Resource": ["arn:aws:s3:::YOUR_BUCKET_NAME/*"]
}
```

### 3. Configure Environment

Add to your `.env` file:

```bash
# Enable Nova document processing
BEDROCK_NOVA_ENABLED=True

# Optional: Override default model
# BEDROCK_NOVA_MODEL_ID=amazon.nova-2-lite-v1:0
```

## Usage

### Basic PDF Analysis

```python
from services.nova_document_processor import NovaDocumentProcessor
from pathlib import Path

# Initialize processor
processor = NovaDocumentProcessor()

# Check availability
if not processor.is_available():
    print("Nova not available - check BEDROCK_NOVA_ENABLED and credentials")
    exit(1)

# Analyze a PDF with a custom query
result = processor.analyze_pdf(
    pdf_path=Path("document.pdf"),
    query="Summarize the key points of this document"
)

if result.success:
    print(result.response_text)
else:
    print(f"Error: {result.error}")
```

### Title and Form Number Extraction

This mirrors the functionality of the existing `TitleExtractor`:

```python
from services.nova_document_processor import NovaDocumentProcessor
from pathlib import Path

processor = NovaDocumentProcessor()

# Extract title and form info (same output structure as TitleExtractor)
result = processor.extract_title_and_form(
    pdf_path=Path("court_form.pdf"),
    preview_output_path=Path("preview.png")  # Optional
)

if result.success:
    print(f"Title: {result.formatted_title}")
    print(f"Form Number: {result.form_number}")
    print(f"Revision Date: {result.revision_date}")
    print(f"Confidence: {result.combined_confidence}")
    print(f"Display Title: {result.display_title}")
else:
    print(f"Extraction failed: {result.error}")
```

### Processing Large Files via S3

For PDFs larger than 25MB:

```python
from services.nova_document_processor import NovaDocumentProcessor

processor = NovaDocumentProcessor()

result = processor.analyze_pdf_from_s3(
    bucket="my-documents-bucket",
    key="large-document.pdf",
    query="Extract all tables from this document",
    bucket_owner="123456789012"  # Optional: AWS account ID
)

if result.success:
    print(result.response_text)
```

### Extracting Structured Data

Extract specific fields from documents:

```python
from services.nova_document_processor import NovaDocumentProcessor
from pathlib import Path

processor = NovaDocumentProcessor()

result = processor.extract_structured_data(
    pdf_path=Path("invoice.pdf"),
    fields=["invoice_number", "date", "total_amount", "vendor_name"]
)

if result.success:
    for field, value in result.extracted_fields.items():
        print(f"{field}: {value}")
    print(f"Confidence: {result.confidence}")
```

### Using the Singleton Instance

For convenience, a singleton accessor is provided:

```python
from services.nova_document_processor import get_nova_processor

processor = get_nova_processor()
result = processor.analyze_pdf(Path("doc.pdf"), "What is this document about?")
```

## Migration Strategy

### Option 1: Gradual Migration (Recommended)

Keep both implementations and switch based on a feature flag:

```python
from pathlib import Path
import os

def extract_document_info(pdf_path: Path):
    """Extract title/form using Nova if enabled, otherwise fall back to Textract+Claude."""
    
    use_nova = os.getenv("BEDROCK_NOVA_ENABLED", "False").lower() == "true"
    
    if use_nova:
        from services.nova_document_processor import get_nova_processor
        processor = get_nova_processor()
        if processor.is_available():
            return processor.extract_title_and_form(pdf_path)
    
    # Fall back to existing implementation
    from services.title_extractor import TitleExtractor
    extractor = TitleExtractor()
    return extractor.extract_title(pdf_path)
```

### Option 2: A/B Testing

Run both implementations and compare results:

```python
from services.nova_document_processor import get_nova_processor
from services.title_extractor import TitleExtractor
from pathlib import Path

def compare_extraction(pdf_path: Path):
    """Compare Nova vs Textract+Claude extraction."""
    
    # Nova extraction
    nova = get_nova_processor()
    nova_result = nova.extract_title_and_form(pdf_path)
    
    # Textract+Claude extraction
    textract = TitleExtractor()
    textract_result = textract.extract_title(pdf_path)
    
    return {
        "nova": {
            "title": nova_result.formatted_title,
            "form_number": nova_result.form_number,
            "confidence": nova_result.combined_confidence
        },
        "textract_claude": {
            "title": textract_result.formatted_title,
            "form_number": textract_result.form_number,
            "confidence": textract_result.combined_confidence
        }
    }
```

## API Reference

### NovaDocumentProcessor

| Method | Description |
|--------|-------------|
| `is_available()` | Check if Nova processing is enabled and credentials are valid |
| `analyze_pdf(pdf_path, query)` | Analyze PDF from file path with custom query |
| `analyze_pdf_from_bytes(pdf_bytes, query)` | Analyze PDF from bytes (max 25MB) |
| `analyze_pdf_from_s3(bucket, key, query)` | Analyze PDF stored in S3 (any size) |
| `extract_title_and_form(pdf_path)` | Extract title, form number, revision date |
| `extract_structured_data(pdf_path, fields)` | Extract specific named fields |

### NovaAnalysisResult

| Field | Type | Description |
|-------|------|-------------|
| `success` | bool | Whether the analysis succeeded |
| `response_text` | str | Raw model response |
| `error` | str | Error message if failed |
| `input_tokens` | int | Input token count |
| `output_tokens` | int | Output token count |
| `extracted_fields` | dict | Structured extraction results |

### TitleExtractionResult

| Field | Type | Description |
|-------|------|-------------|
| `success` | bool | Whether extraction succeeded |
| `formatted_title` | str | Cleaned, formatted title |
| `form_number` | str | Extracted form number |
| `revision_date` | str | Revision/effective date |
| `combined_confidence` | float | Confidence score (0-1) |
| `display_title` | str | Combined "Title {FormNumber}" format |

## Troubleshooting

### "Nova not available" Error

1. Verify `BEDROCK_NOVA_ENABLED=True` in your environment
2. Check AWS credentials are configured (`aws configure` or env vars)
3. Verify Nova model access is enabled in Bedrock console
4. Ensure your region supports Nova models (us-east-1 recommended)

### "Access Denied" Error

1. Verify IAM policy includes `bedrock:InvokeModel` permission
2. Check the resource ARN matches your model ID
3. Ensure the policy is attached to the correct role/user

### "PDF too large" Error

For PDFs over 25MB:
1. Upload the PDF to S3
2. Use `analyze_pdf_from_s3()` instead of `analyze_pdf()`
3. Ensure your IAM policy includes S3 read permissions

### Timeout Errors

The client is configured with a 5-minute timeout. For very large documents:
1. Consider splitting the document
2. Use streaming responses (if supported)
3. Process in Lambda with extended timeout

## Cost Considerations

Nova 2 Lite pricing (as of 2025):
- Input: $0.00006 per 1K tokens
- Output: $0.00024 per 1K tokens

Compare with:
- Textract: ~$1.50 per 1,000 pages
- Claude 3 Sonnet: $0.003 per 1K input, $0.015 per 1K output

For most document analysis use cases, Nova 2 Lite offers significant cost savings over the Textract+Claude pipeline.

## Additional Resources

- [Amazon Nova User Guide](https://docs.aws.amazon.com/nova/latest/userguide/)
- [Bedrock Converse API Reference](https://docs.aws.amazon.com/bedrock/latest/userguide/conversation-inference.html)
- [Nova Document Understanding Examples](https://docs.aws.amazon.com/nova/latest/userguide/modalities-document-examples.html)

