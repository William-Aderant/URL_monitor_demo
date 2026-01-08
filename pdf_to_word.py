#!/usr/bin/env python3
"""
PDF to Word Form Converter
==========================

Converts PDF forms into Microsoft Word documents (.docx), preserving:
- Page layout, tables, and formatting
- Form fields as fillable controls IN-PLACE

This script uses LibreOffice headless mode for conversion, which natively
preserves PDF form fields as Word content controls at their original positions.

Dependencies:
    - LibreOffice (brew install --cask libreoffice on macOS)
    - pip install pymupdf (optional, for --info flag)

Limitations:
- Requires LibreOffice installed on the system
- Not ideal for high-volume processing (see Gotenberg or unoserver for scale)
- Some complex PDF layouts may not convert perfectly

Author: PDF Form Converter
License: MIT
"""

import argparse
import subprocess
import sys
import shutil
import tempfile
from pathlib import Path
from typing import Optional


def find_libreoffice() -> Optional[str]:
    """
    Find the LibreOffice executable on the system.
    
    Returns:
        Path to LibreOffice executable, or None if not found
    """
    # Common LibreOffice paths by platform
    possible_paths = [
        # macOS
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
        # Linux
        "/usr/bin/libreoffice",
        "/usr/bin/soffice",
        "/usr/local/bin/libreoffice",
        "/usr/local/bin/soffice",
        # Windows (via WSL or direct)
        "C:/Program Files/LibreOffice/program/soffice.exe",
        "C:/Program Files (x86)/LibreOffice/program/soffice.exe",
    ]
    
    # Check if 'libreoffice' or 'soffice' is in PATH
    for cmd in ['libreoffice', 'soffice']:
        path = shutil.which(cmd)
        if path:
            return path
    
    # Check common installation paths
    for path in possible_paths:
        if Path(path).exists():
            return path
    
    return None


def convert_pdf_to_docx(pdf_path: str, output_path: str = None, 
                         libreoffice_path: str = None) -> str:
    """
    Convert a PDF form to Word document using LibreOffice.
    
    LibreOffice's PDF import preserves form fields as Word content controls
    at their original positions.
    
    Args:
        pdf_path: Path to input PDF file
        output_path: Path for output .docx file (optional)
        libreoffice_path: Custom path to LibreOffice executable (optional)
        
    Returns:
        Path to the created Word document
        
    Raises:
        FileNotFoundError: If PDF or LibreOffice not found
        RuntimeError: If conversion fails
    """
    pdf_path = Path(pdf_path).resolve()
    
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")
    
    # Find LibreOffice
    lo_path = libreoffice_path or find_libreoffice()
    if not lo_path:
        raise FileNotFoundError(
            "LibreOffice not found. Please install it:\n"
            "  macOS:   brew install --cask libreoffice\n"
            "  Ubuntu:  sudo apt install libreoffice\n"
            "  Windows: Download from libreoffice.org"
        )
    
    # Determine output path
    if output_path:
        output_path = Path(output_path).resolve()
        output_dir = output_path.parent
        output_name = output_path.stem
    else:
        output_dir = pdf_path.parent
        output_name = pdf_path.stem
        output_path = output_dir / f"{output_name}.docx"
    
    # Ensure output directory exists
    output_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"Converting: {pdf_path}")
    print(f"Using LibreOffice: {lo_path}")
    print("-" * 50)
    
    # Use a temporary directory for LibreOffice output
    # (LibreOffice outputs to a directory, not a specific file)
    with tempfile.TemporaryDirectory() as temp_dir:
        # LibreOffice command for PDF to DOCX conversion
        # --infilter specifies the PDF import filter
        # --convert-to specifies output format
        cmd = [
            lo_path,
            "--headless",                          # No GUI
            "--invisible",                         # Don't show splash
            "--nologo",                            # No logo
            "--nofirststartwizard",               # Skip first-start wizard
            "--infilter=writer_pdf_import",        # Use PDF import filter
            "--convert-to", "docx",                # Output format
            "--outdir", temp_dir,                  # Output directory
            str(pdf_path)                          # Input file
        ]
        
        print("Running LibreOffice conversion...")
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120  # 2 minute timeout
            )
            
            if result.returncode != 0:
                error_msg = result.stderr or result.stdout or "Unknown error"
                raise RuntimeError(f"LibreOffice conversion failed: {error_msg}")
            
            # Find the output file in temp directory
            temp_output = Path(temp_dir) / f"{pdf_path.stem}.docx"
            
            if not temp_output.exists():
                # LibreOffice might have created a different name
                docx_files = list(Path(temp_dir).glob("*.docx"))
                if docx_files:
                    temp_output = docx_files[0]
                else:
                    raise RuntimeError(
                        f"Conversion completed but output file not found.\n"
                        f"LibreOffice output: {result.stdout}\n"
                        f"Temp dir contents: {list(Path(temp_dir).iterdir())}"
                    )
            
            # Move to final destination
            shutil.move(str(temp_output), str(output_path))
            
        except subprocess.TimeoutExpired:
            raise RuntimeError("LibreOffice conversion timed out (>120 seconds)")
        except subprocess.SubprocessError as e:
            raise RuntimeError(f"Failed to run LibreOffice: {e}")
    
    print(f"Conversion complete!")
    print(f"Output: {output_path}")
    print("-" * 50)
    
    return str(output_path)


def get_pdf_info(pdf_path: str) -> dict:
    """
    Get information about form fields in a PDF.
    
    Args:
        pdf_path: Path to PDF file
        
    Returns:
        Dictionary with PDF and form field information
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return {
            "error": "PyMuPDF not installed. Run: pip install pymupdf",
            "total_pages": "unknown",
            "total_fields": "unknown"
        }
    
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")
    
    doc = fitz.open(str(pdf_path))
    
    field_type_map = {
        fitz.PDF_WIDGET_TYPE_TEXT: 'text',
        fitz.PDF_WIDGET_TYPE_CHECKBOX: 'checkbox',
        fitz.PDF_WIDGET_TYPE_RADIOBUTTON: 'radio',
        fitz.PDF_WIDGET_TYPE_COMBOBOX: 'dropdown',
        fitz.PDF_WIDGET_TYPE_LISTBOX: 'listbox',
    }
    
    info = {
        "file": str(pdf_path),
        "total_pages": len(doc),
        "total_fields": 0,
        "field_types": {},
        "fields": []
    }
    
    for page_num in range(len(doc)):
        page = doc[page_num]
        for widget in page.widgets():
            field_type = field_type_map.get(widget.field_type, 'unknown')
            info["total_fields"] += 1
            info["field_types"][field_type] = info["field_types"].get(field_type, 0) + 1
            info["fields"].append({
                "name": widget.field_name or f"field_{page_num}_{len(info['fields'])}",
                "type": field_type,
                "page": page_num + 1,
                "value": widget.field_value
            })
    
    doc.close()
    return info


def main():
    """Main entry point for command line usage."""
    parser = argparse.ArgumentParser(
        description="Convert PDF forms to Word documents preserving form fields.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python pdf_to_word.py form.pdf
  python pdf_to_word.py form.pdf -o output.docx
  python pdf_to_word.py form.pdf --info

Requirements:
  LibreOffice must be installed on your system:
    macOS:   brew install --cask libreoffice
    Ubuntu:  sudo apt install libreoffice
    Windows: Download from libreoffice.org

Note:
  LibreOffice's PDF import preserves form fields as Word content controls
  at their original positions in the document.
        """
    )
    
    parser.add_argument(
        'input_pdf',
        help='Path to the input PDF form'
    )
    
    parser.add_argument(
        '-o', '--output',
        help='Path for output Word document (default: same name as input with .docx)'
    )
    
    parser.add_argument(
        '--info',
        action='store_true',
        help='Show PDF form field information without converting'
    )
    
    parser.add_argument(
        '--libreoffice-path',
        help='Custom path to LibreOffice executable'
    )
    
    args = parser.parse_args()
    
    try:
        if args.info:
            # Show form field information
            info = get_pdf_info(args.input_pdf)
            
            print(f"\nPDF Form Information: {info.get('file', args.input_pdf)}")
            print("=" * 60)
            
            if "error" in info:
                print(f"Warning: {info['error']}")
            
            print(f"Total pages: {info['total_pages']}")
            print(f"Total form fields: {info['total_fields']}")
            
            if info.get('field_types'):
                print(f"\nFields by type:")
                for ftype, count in info['field_types'].items():
                    print(f"  {ftype}: {count}")
            
            if info.get('fields'):
                print(f"\nField details (first 20):")
                for field in info['fields'][:20]:
                    value_str = f" = '{field['value']}'" if field['value'] else ""
                    print(f"  Page {field['page']}: [{field['type']}] {field['name'][:50]}{value_str}")
                
                if len(info['fields']) > 20:
                    print(f"  ... and {len(info['fields']) - 20} more fields")
        else:
            # Convert PDF to Word
            convert_pdf_to_docx(
                args.input_pdf,
                args.output,
                args.libreoffice_path
            )
    
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nConversion cancelled.", file=sys.stderr)
        sys.exit(130)
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
