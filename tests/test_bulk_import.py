"""
Tests for the bulk import service.
"""

import pytest
from unittest.mock import patch, MagicMock

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestURLValidation:
    """Tests for URL validation."""
    
    def test_valid_https_url(self):
        """Test valid HTTPS URL."""
        from services.bulk_importer import BulkImporter
        
        importer = BulkImporter()
        is_valid, error = importer.validate_url_format("https://example.com/form.pdf")
        
        assert is_valid is True
        assert error is None
    
    def test_valid_http_url(self):
        """Test valid HTTP URL."""
        from services.bulk_importer import BulkImporter
        
        importer = BulkImporter()
        is_valid, error = importer.validate_url_format("http://example.com/form.pdf")
        
        assert is_valid is True
        assert error is None
    
    def test_empty_url(self):
        """Test empty URL validation."""
        from services.bulk_importer import BulkImporter
        
        importer = BulkImporter()
        is_valid, error = importer.validate_url_format("")
        
        assert is_valid is False
        assert error is not None
    
    def test_url_without_scheme(self):
        """Test URL without scheme."""
        from services.bulk_importer import BulkImporter
        
        importer = BulkImporter()
        is_valid, error = importer.validate_url_format("example.com/form.pdf")
        
        assert is_valid is False
        assert "scheme" in error.lower()
    
    def test_invalid_scheme(self):
        """Test URL with invalid scheme."""
        from services.bulk_importer import BulkImporter
        
        importer = BulkImporter()
        is_valid, error = importer.validate_url_format("ftp://example.com/form.pdf")
        
        assert is_valid is False
        assert "scheme" in error.lower()


class TestCSVParsing:
    """Tests for CSV parsing."""
    
    def test_parse_valid_csv(self):
        """Test parsing valid CSV content."""
        from services.bulk_importer import BulkImporter
        
        importer = BulkImporter()
        content = """URL,Title,State,Jurisdiction
https://example.com/form1.pdf,Form 1,California,courts.ca.gov
https://example.com/form2.pdf,Form 2,Alaska,courts.alaska.gov"""
        
        rows = importer.parse_csv_content(content)
        
        assert len(rows) == 2
        assert rows[0]["url"] == "https://example.com/form1.pdf"
        assert rows[0]["title"] == "Form 1"
        assert rows[0]["state"] == "California"
        assert rows[1]["state"] == "Alaska"
    
    def test_parse_csv_with_empty_title(self):
        """Test parsing CSV with empty title field."""
        from services.bulk_importer import BulkImporter
        
        importer = BulkImporter()
        content = """URL,Title,State,Jurisdiction
https://example.com/form1.pdf,,California,courts.ca.gov"""
        
        rows = importer.parse_csv_content(content)
        
        assert len(rows) == 1
        assert rows[0]["title"] == ""
    
    def test_parse_csv_header_variations(self):
        """Test parsing CSV with header variations."""
        from services.bulk_importer import BulkImporter
        
        importer = BulkImporter()
        content = """Link,Name,State_Name,Domain
https://example.com/form1.pdf,Form 1,California,courts.ca.gov"""
        
        rows = importer.parse_csv_content(content)
        
        assert len(rows) == 1
        # Header normalization should handle variations


class TestTXTParsing:
    """Tests for TXT parsing."""
    
    def test_parse_comma_separated_txt(self):
        """Test parsing comma-separated TXT content."""
        from services.bulk_importer import BulkImporter
        
        importer = BulkImporter()
        content = """https://example.com/form1.pdf,Form 1,California,courts.ca.gov
https://example.com/form2.pdf,Form 2,Alaska,courts.alaska.gov"""
        
        rows = importer.parse_txt_content(content)
        
        assert len(rows) == 2
        assert rows[0]["url"] == "https://example.com/form1.pdf"
        assert rows[0]["title"] == "Form 1"
    
    def test_parse_tab_separated_txt(self):
        """Test parsing tab-separated TXT content."""
        from services.bulk_importer import BulkImporter
        
        importer = BulkImporter()
        content = "https://example.com/form1.pdf\tForm 1\tCalifornia\tcourts.ca.gov"
        
        rows = importer.parse_txt_content(content)
        
        assert len(rows) == 1
        assert rows[0]["url"] == "https://example.com/form1.pdf"
    
    def test_skip_comment_lines(self):
        """Test that comment lines are skipped."""
        from services.bulk_importer import BulkImporter
        
        importer = BulkImporter()
        content = """# This is a comment
https://example.com/form1.pdf,Form 1,California,courts.ca.gov"""
        
        rows = importer.parse_txt_content(content)
        
        assert len(rows) == 1


class TestDomainExtraction:
    """Tests for domain category extraction."""
    
    def test_extract_domain_from_url(self):
        """Test extracting domain from URL."""
        from services.bulk_importer import BulkImporter
        
        importer = BulkImporter()
        domain = importer.extract_domain_category("https://www.courts.ca.gov/documents/form.pdf")
        
        assert domain == "www.courts.ca.gov"
    
    def test_extract_domain_handles_none(self):
        """Test domain extraction with invalid URL."""
        from services.bulk_importer import BulkImporter
        
        importer = BulkImporter()
        domain = importer.extract_domain_category("")
        
        assert domain is None


class TestNameGeneration:
    """Tests for name generation from URL."""
    
    def test_generate_name_from_pdf_url(self):
        """Test generating name from PDF URL."""
        from services.bulk_importer import BulkImporter
        
        importer = BulkImporter()
        name = importer.generate_name_from_url("https://example.com/civil-case-cover-sheet.pdf")
        
        assert "Civil" in name or "civil" in name.lower()
    
    def test_generate_name_with_underscores(self):
        """Test name generation with underscores in URL."""
        from services.bulk_importer import BulkImporter
        
        importer = BulkImporter()
        name = importer.generate_name_from_url("https://example.com/form_001_sample.pdf")
        
        # Should replace underscores with spaces
        assert "_" not in name


class TestTemplates:
    """Tests for format templates."""
    
    def test_csv_template_has_headers(self):
        """Test that CSV template has required headers."""
        from services.bulk_importer import bulk_importer, CSV_TEMPLATE
        
        template = bulk_importer.get_csv_template()
        
        assert "URL" in template
        assert "Title" in template
        assert "State" in template
        assert "Jurisdiction" in template
    
    def test_format_guide_exists(self):
        """Test that format guide is not empty."""
        from services.bulk_importer import bulk_importer
        
        guide = bulk_importer.get_format_guide()
        
        assert len(guide) > 100
        assert "CSV" in guide
        assert "TXT" in guide


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
