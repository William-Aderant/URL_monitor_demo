#!/usr/bin/env python3
"""
Test PDF Generator

Generates realistic court form PDFs for testing the URL Monitor's change detection.
Uses PyMuPDF (fitz) to create PDFs with proper text and formatting.

Generates multiple forms with distinct form numbers:
- CIV-001: Motion to Dismiss (Smith v. Jones)
- CIV-002: Petition for Custody (Davis v. Davis)
- CIV-003: Petition for Appeal (Garcia v. State)
"""

import fitz  # PyMuPDF
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Tuple
import random


class TestPDFGenerator:
    """Generates test PDF forms for change detection testing."""
    
    # Court form styling
    FONT_TITLE = "helv"
    FONT_BODY = "helv"
    FONT_MONO = "cour"
    
    # Page dimensions (letter size in points)
    PAGE_WIDTH = 612
    PAGE_HEIGHT = 792
    MARGIN = 72  # 1 inch margin
    
    def __init__(self, output_dir: Path = None):
        """Initialize the generator."""
        self.output_dir = output_dir or Path(__file__).parent / "pdfs"
        self.output_dir.mkdir(parents=True, exist_ok=True)
    
    def create_court_form(
        self,
        filename: str,
        form_number: str,
        title: str,
        case_number: str = "2026-CV-001",
        plaintiff: str = "John Smith",
        defendant: str = "Jane Jones",
        content_sections: list = None,
        revision_date: str = None,
        include_signature: bool = True,
        extra_text: str = ""
    ) -> Path:
        """
        Create a realistic court form PDF.
        
        Args:
            filename: Output filename (e.g., "civ-001.pdf")
            form_number: Form number (e.g., "CIV-001", "CIV-002")
            title: Form title (e.g., "Motion to Dismiss")
            case_number: Case number
            plaintiff: Plaintiff name
            defendant: Defendant name
            content_sections: List of (section_title, section_content) tuples
            revision_date: Revision date string
            include_signature: Whether to include signature block
            extra_text: Additional text to add (for testing changes)
            
        Returns:
            Path to created PDF
        """
        doc = fitz.open()
        
        # Default content sections
        if content_sections is None:
            content_sections = [
                ("INTRODUCTION", "This motion is filed pursuant to Rule 12(b)(6) of the Federal Rules of Civil Procedure."),
                ("FACTUAL BACKGROUND", f"On or about January 1, 2026, {plaintiff} filed a complaint against {defendant} alleging breach of contract and negligence."),
                ("LEGAL STANDARD", "A motion to dismiss under Rule 12(b)(6) tests the legal sufficiency of the complaint. The court must accept all factual allegations as true and draw all reasonable inferences in favor of the non-moving party."),
                ("ARGUMENT", "The complaint fails to state a claim upon which relief can be granted. The allegations are conclusory and lack sufficient factual support to meet the pleading requirements."),
                ("CONCLUSION", f"For the foregoing reasons, {defendant} respectfully requests that this Court grant the motion to dismiss with prejudice.")
            ]
        
        if revision_date is None:
            revision_date = datetime.now().strftime("%m/%d/%Y")
        
        # Create first page
        page = doc.new_page(width=self.PAGE_WIDTH, height=self.PAGE_HEIGHT)
        y = self.MARGIN
        
        # Header box
        header_rect = fitz.Rect(self.MARGIN, y, self.PAGE_WIDTH - self.MARGIN, y + 80)
        page.draw_rect(header_rect, color=(0, 0, 0), width=1)
        
        # Form number (top left of header)
        page.insert_text(
            (self.MARGIN + 10, y + 20),
            f"Form {form_number}",
            fontname=self.FONT_MONO,
            fontsize=10
        )
        
        # Revision date (top right of header)
        page.insert_text(
            (self.PAGE_WIDTH - self.MARGIN - 100, y + 20),
            f"Rev. {revision_date}",
            fontname=self.FONT_MONO,
            fontsize=8
        )
        
        # Title (centered in header)
        title_width = fitz.get_text_length(title, fontname=self.FONT_TITLE, fontsize=14)
        page.insert_text(
            ((self.PAGE_WIDTH - title_width) / 2, y + 50),
            title.upper(),
            fontname=self.FONT_TITLE,
            fontsize=14
        )
        
        # Court name
        court_name = "SUPERIOR COURT OF THE STATE OF ALASKA"
        court_width = fitz.get_text_length(court_name, fontname=self.FONT_TITLE, fontsize=10)
        page.insert_text(
            ((self.PAGE_WIDTH - court_width) / 2, y + 70),
            court_name,
            fontname=self.FONT_TITLE,
            fontsize=10
        )
        
        y += 100
        
        # Case caption box
        caption_rect = fitz.Rect(self.MARGIN, y, self.PAGE_WIDTH - self.MARGIN, y + 80)
        page.draw_rect(caption_rect, color=(0, 0, 0), width=0.5)
        
        # Case caption content
        page.insert_text((self.MARGIN + 10, y + 25), f"{plaintiff.upper()},", fontname=self.FONT_BODY, fontsize=11)
        page.insert_text((self.MARGIN + 30, y + 40), "Plaintiff,", fontname=self.FONT_BODY, fontsize=10)
        page.insert_text((self.MARGIN + 10, y + 55), "v.", fontname=self.FONT_BODY, fontsize=11)
        page.insert_text((self.MARGIN + 10, y + 70), f"{defendant.upper()},", fontname=self.FONT_BODY, fontsize=11)
        
        # Case number on right side
        page.insert_text((self.PAGE_WIDTH - self.MARGIN - 150, y + 25), f"Case No: {case_number}", fontname=self.FONT_MONO, fontsize=10)
        page.insert_text((self.PAGE_WIDTH - self.MARGIN - 150, y + 45), title.upper(), fontname=self.FONT_BODY, fontsize=9)
        
        y += 100
        
        # Content sections
        for section_title, section_content in content_sections:
            # Check if we need a new page
            if y > self.PAGE_HEIGHT - 150:
                page = doc.new_page(width=self.PAGE_WIDTH, height=self.PAGE_HEIGHT)
                y = self.MARGIN
            
            # Section title
            page.insert_text(
                (self.MARGIN, y),
                section_title,
                fontname=self.FONT_TITLE,
                fontsize=11
            )
            y += 20
            
            # Section content (with word wrap)
            lines = self._wrap_text(section_content, self.PAGE_WIDTH - 2 * self.MARGIN, self.FONT_BODY, 10)
            for line in lines:
                if y > self.PAGE_HEIGHT - 100:
                    page = doc.new_page(width=self.PAGE_WIDTH, height=self.PAGE_HEIGHT)
                    y = self.MARGIN
                
                page.insert_text((self.MARGIN + 20, y), line, fontname=self.FONT_BODY, fontsize=10)
                y += 14
            
            y += 15
        
        # Extra text for testing changes
        if extra_text:
            if y > self.PAGE_HEIGHT - 100:
                page = doc.new_page(width=self.PAGE_WIDTH, height=self.PAGE_HEIGHT)
                y = self.MARGIN
            
            page.insert_text((self.MARGIN, y), "ADDITIONAL INFORMATION:", fontname=self.FONT_TITLE, fontsize=11)
            y += 20
            lines = self._wrap_text(extra_text, self.PAGE_WIDTH - 2 * self.MARGIN, self.FONT_BODY, 10)
            for line in lines:
                page.insert_text((self.MARGIN + 20, y), line, fontname=self.FONT_BODY, fontsize=10)
                y += 14
            y += 15
        
        # Signature block
        if include_signature:
            if y > self.PAGE_HEIGHT - 150:
                page = doc.new_page(width=self.PAGE_WIDTH, height=self.PAGE_HEIGHT)
                y = self.MARGIN
            
            y += 30
            page.insert_text((self.MARGIN, y), "Respectfully submitted,", fontname=self.FONT_BODY, fontsize=10)
            y += 40
            
            # Signature line
            page.draw_line(
                fitz.Point(self.MARGIN, y),
                fitz.Point(self.MARGIN + 200, y),
                color=(0, 0, 0),
                width=0.5
            )
            y += 15
            page.insert_text((self.MARGIN, y), "Attorney for " + defendant, fontname=self.FONT_BODY, fontsize=9)
            y += 15
            page.insert_text((self.MARGIN, y), f"Date: {datetime.now().strftime('%B %d, %Y')}", fontname=self.FONT_BODY, fontsize=9)
        
        # Footer on each page
        for i, pg in enumerate(doc):
            footer_text = f"Form {form_number} - Page {i + 1} of {len(doc)}"
            footer_width = fitz.get_text_length(footer_text, fontname=self.FONT_MONO, fontsize=8)
            pg.insert_text(
                ((self.PAGE_WIDTH - footer_width) / 2, self.PAGE_HEIGHT - 30),
                footer_text,
                fontname=self.FONT_MONO,
                fontsize=8
            )
        
        # Save
        output_path = self.output_dir / filename
        doc.save(str(output_path))
        doc.close()
        
        print(f"✓ Created: {output_path}")
        return output_path
    
    def _wrap_text(self, text: str, max_width: float, fontname: str, fontsize: int) -> list:
        """Wrap text to fit within max_width."""
        words = text.split()
        lines = []
        current_line = []
        
        for word in words:
            test_line = ' '.join(current_line + [word])
            width = fitz.get_text_length(test_line, fontname=fontname, fontsize=fontsize)
            
            if width <= max_width:
                current_line.append(word)
            else:
                if current_line:
                    lines.append(' '.join(current_line))
                current_line = [word]
        
        if current_line:
            lines.append(' '.join(current_line))
        
        return lines


# Form definitions for multiple test forms
FORM_DEFINITIONS = {
    "civ-001": {
        "form_number": "CIV-001",
        "title": "Motion to Dismiss",
        "case_number": "2026-CV-001",
        "plaintiff": "John Smith",
        "defendant": "Jane Jones",
        "content_sections": [
            ("INTRODUCTION", "This motion is filed pursuant to Rule 12(b)(6) of the Federal Rules of Civil Procedure."),
            ("FACTUAL BACKGROUND", "On or about January 1, 2026, John Smith filed a complaint against Jane Jones alleging breach of contract and negligence."),
            ("LEGAL STANDARD", "A motion to dismiss under Rule 12(b)(6) tests the legal sufficiency of the complaint. The court must accept all factual allegations as true."),
            ("ARGUMENT", "The complaint fails to state a claim upon which relief can be granted. The allegations are conclusory."),
            ("CONCLUSION", "For the foregoing reasons, Jane Jones respectfully requests that this Court grant the motion to dismiss with prejudice.")
        ]
    },
    "civ-002": {
        "form_number": "CIV-002",
        "title": "Petition for Custody",
        "case_number": "2026-CV-002",
        "plaintiff": "Michael Davis",
        "defendant": "Sarah Davis",
        "content_sections": [
            ("INTRODUCTION", "Petitioner Michael Davis hereby petitions this Court for primary custody of the minor children."),
            ("BACKGROUND", "The parties were married on June 15, 2015, and have two minor children: Emma (age 8) and Jacob (age 5)."),
            ("CUSTODY FACTORS", "Petitioner is the primary caregiver and has provided a stable home environment for the children."),
            ("BEST INTERESTS", "It is in the best interests of the children to remain in Petitioner's primary custody."),
            ("RELIEF REQUESTED", "Petitioner requests that this Court grant primary physical and legal custody of the minor children.")
        ]
    },
    "civ-003": {
        "form_number": "CIV-003",
        "title": "Petition for Appeal",
        "case_number": "2026-CV-003",
        "plaintiff": "Maria Garcia",
        "defendant": "State of Alaska",
        "content_sections": [
            ("INTRODUCTION", "Petitioner Maria Garcia hereby appeals the decision of the Superior Court dated December 15, 2025."),
            ("GROUNDS FOR APPEAL", "The lower court committed reversible error in its application of the statute of limitations."),
            ("LEGAL ARGUMENT", "Under Alaska law, the discovery rule applies to toll the statute of limitations in cases where the injury was not immediately apparent."),
            ("RELIEF REQUESTED", "Petitioner requests that this Court reverse the decision of the lower court and remand for further proceedings.")
        ]
    }
}


def generate_all_forms():
    """Generate all test forms."""
    generator = TestPDFGenerator()
    
    for form_id, form_data in FORM_DEFINITIONS.items():
        generator.create_court_form(
            filename=f"{form_id}.pdf",
            **form_data
        )


def generate_form(form_id: str, extra_text: str = "", title_override: str = None, filename_override: str = None):
    """Generate a specific test form with optional modifications."""
    generator = TestPDFGenerator()
    
    if form_id not in FORM_DEFINITIONS:
        print(f"Unknown form: {form_id}. Available: {', '.join(FORM_DEFINITIONS.keys())}")
        return None
    
    form_data = FORM_DEFINITIONS[form_id].copy()
    if title_override:
        form_data["title"] = title_override
    
    filename = filename_override or f"{form_id}.pdf"
    
    return generator.create_court_form(
        filename=filename,
        extra_text=extra_text,
        **form_data
    )


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python generate_test_pdf.py <command>")
        print("\nCommands:")
        print("  all           - Generate all test forms")
        print("  civ-001       - Generate CIV-001 (Motion to Dismiss)")
        print("  civ-002       - Generate CIV-002 (Petition for Custody)")
        print("  civ-003       - Generate CIV-003 (Petition for Appeal)")
        sys.exit(1)
    
    cmd = sys.argv[1].lower()
    
    if cmd == "all":
        generate_all_forms()
    elif cmd in FORM_DEFINITIONS:
        generate_form(cmd)
    else:
        print(f"Unknown command: {cmd}")
        print(f"Available: all, {', '.join(FORM_DEFINITIONS.keys())}")
        sys.exit(1)
