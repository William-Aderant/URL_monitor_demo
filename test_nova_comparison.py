#!/usr/bin/env python3
"""
Compare Nova 2 Lite vs Textract+Claude title extraction on the same PDF.

Usage:
    python test_nova_comparison.py                    # Test with default PDF
    python test_nova_comparison.py path/to/file.pdf  # Test with specific PDF
"""

import sys
import time
from pathlib import Path


def find_sample_pdf() -> Path:
    """Find a sample PDF from the data directory."""
    data_dir = Path("data/pdfs")
    if not data_dir.exists():
        return None
    
    # Look for any original.pdf in the data directory
    for pdf_path in data_dir.rglob("original.pdf"):
        if pdf_path.exists() and pdf_path.stat().st_size > 0:
            return pdf_path
    
    return None


def test_textract_claude(pdf_path: Path) -> dict:
    """Test Textract + Claude workflow."""
    from services.title_extractor import TitleExtractor
    
    extractor = TitleExtractor()
    if not extractor.is_available():
        return {
            "method": "Textract + Claude",
            "success": False,
            "error": "Textract+Claude not available - check AWS credentials"
        }
    
    start = time.time()
    result = extractor.extract_title(pdf_path)
    elapsed = time.time() - start
    
    return {
        "method": "Textract + Claude",
        "title": result.formatted_title,
        "form_number": result.form_number,
        "revision_date": result.revision_date,
        "confidence": result.combined_confidence,
        "time_seconds": round(elapsed, 2),
        "success": result.success,
        "error": result.error,
        "extraction_method": result.extraction_method
    }


def test_nova(pdf_path: Path) -> dict:
    """Test Nova 2 Lite workflow."""
    import os
    
    # Temporarily enable Nova for this test
    original_value = os.environ.get("BEDROCK_NOVA_ENABLED")
    os.environ["BEDROCK_NOVA_ENABLED"] = "True"
    
    try:
        from services.nova_document_processor import NovaDocumentProcessor
        
        processor = NovaDocumentProcessor()
        # Reset availability check to pick up the new env var
        processor._available = None
        
        if not processor.is_available():
            return {
                "method": "Nova 2 Lite",
                "success": False,
                "error": "Nova not available - check AWS credentials and Bedrock model access"
            }
        
        start = time.time()
        result = processor.extract_title_and_form(pdf_path)
        elapsed = time.time() - start
        
        return {
            "method": "Nova 2 Lite",
            "title": result.formatted_title,
            "form_number": result.form_number,
            "revision_date": result.revision_date,
            "confidence": result.combined_confidence,
            "time_seconds": round(elapsed, 2),
            "success": result.success,
            "error": result.error,
            "extraction_method": result.extraction_method
        }
    finally:
        # Restore original value
        if original_value is None:
            os.environ.pop("BEDROCK_NOVA_ENABLED", None)
        else:
            os.environ["BEDROCK_NOVA_ENABLED"] = original_value


def print_result(result: dict):
    """Print a single result."""
    print(f"Method: {result.get('method', 'Unknown')}")
    print(f"  Success: {result.get('success')}")
    if result.get('success'):
        print(f"  Title: {result.get('title')}")
        print(f"  Form Number: {result.get('form_number')}")
        print(f"  Revision Date: {result.get('revision_date')}")
        print(f"  Confidence: {result.get('confidence')}")
        print(f"  Time: {result.get('time_seconds')}s")
        print(f"  Extraction Method: {result.get('extraction_method')}")
    else:
        print(f"  Error: {result.get('error')}")


def main():
    # Determine PDF path
    if len(sys.argv) > 1:
        pdf_path = Path(sys.argv[1])
    else:
        pdf_path = find_sample_pdf()
    
    if pdf_path is None or not pdf_path.exists():
        print("ERROR: No PDF found to test.")
        print("Usage: python test_nova_comparison.py path/to/file.pdf")
        print("\nOr ensure you have PDFs in data/pdfs/")
        sys.exit(1)
    
    print(f"\n{'='*70}")
    print(f"NOVA vs TEXTRACT+CLAUDE COMPARISON")
    print(f"{'='*70}")
    print(f"\nTesting PDF: {pdf_path}")
    print(f"File size: {pdf_path.stat().st_size / 1024:.1f} KB")
    print(f"\n{'='*70}\n")
    
    # Test Textract + Claude
    print("Testing Textract + Claude...")
    print("-" * 40)
    tc_result = test_textract_claude(pdf_path)
    print_result(tc_result)
    
    print(f"\n{'='*70}\n")
    
    # Test Nova
    print("Testing Nova 2 Lite...")
    print("-" * 40)
    nova_result = test_nova(pdf_path)
    print_result(nova_result)
    
    # Print comparison summary
    print(f"\n{'='*70}")
    print("COMPARISON SUMMARY")
    print(f"{'='*70}\n")
    
    tc_success = tc_result.get('success', False)
    nova_success = nova_result.get('success', False)
    
    if tc_success and nova_success:
        tc_time = tc_result.get('time_seconds', 0)
        nova_time = nova_result.get('time_seconds', 0)
        
        if nova_time > 0:
            speedup = tc_time / nova_time
            print(f"Speed: Nova is {speedup:.1f}x {'faster' if speedup > 1 else 'slower'}")
        
        print(f"Time - Textract+Claude: {tc_time}s, Nova: {nova_time}s")
        
        titles_match = tc_result.get('title') == nova_result.get('title')
        forms_match = tc_result.get('form_number') == nova_result.get('form_number')
        
        print(f"\nTitles match: {'✓ Yes' if titles_match else '✗ No'}")
        if not titles_match:
            print(f"  Textract+Claude: {tc_result.get('title')}")
            print(f"  Nova:            {nova_result.get('title')}")
        
        print(f"Form numbers match: {'✓ Yes' if forms_match else '✗ No'}")
        if not forms_match:
            print(f"  Textract+Claude: {tc_result.get('form_number')}")
            print(f"  Nova:            {nova_result.get('form_number')}")
        
        tc_conf = tc_result.get('confidence', 0) or 0
        nova_conf = nova_result.get('confidence', 0) or 0
        print(f"\nConfidence - Textract+Claude: {tc_conf:.2f}, Nova: {nova_conf:.2f}")
        
        print(f"\n{'='*70}")
        if speedup > 1 and titles_match and forms_match:
            print("✓ RECOMMENDATION: Nova 2 Lite is faster with matching results!")
        elif titles_match and forms_match:
            print("~ Results match, performance similar")
        else:
            print("⚠ Results differ - manual review recommended")
        print(f"{'='*70}\n")
        
    elif nova_success and not tc_success:
        print("Only Nova succeeded - Textract+Claude failed")
        print(f"Textract+Claude error: {tc_result.get('error')}")
    elif tc_success and not nova_success:
        print("Only Textract+Claude succeeded - Nova failed")
        print(f"Nova error: {nova_result.get('error')}")
    else:
        print("Both methods failed!")
        print(f"Textract+Claude error: {tc_result.get('error')}")
        print(f"Nova error: {nova_result.get('error')}")


if __name__ == "__main__":
    main()

