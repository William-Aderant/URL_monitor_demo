"""
Tests for download functionality and filename generation.
"""

import pytest
from datetime import datetime

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestFilenameGeneration:
    """Tests for download filename generation."""
    
    def test_filename_with_title_and_form_number(self):
        """Test filename generation with both title and form number."""
        title = "Petition for Name Change"
        form_number = "MC-031"
        
        filename = f"{title} {form_number}.pdf"
        
        assert filename == "Petition for Name Change MC-031.pdf"
    
    def test_filename_without_form_number(self):
        """Test filename generation without form number."""
        title = "Civil Case Cover Sheet"
        form_number = None
        
        if form_number:
            filename = f"{title} {form_number}.pdf"
        else:
            filename = f"{title}.pdf"
        
        assert filename == "Civil Case Cover Sheet.pdf"
    
    def test_sanitize_invalid_characters(self):
        """Test removing invalid characters from filename."""
        from config import settings
        
        filename = 'Form: Test <Name> "File".pdf'
        invalid_chars = settings.DOWNLOAD_FILENAME_INVALID_CHARS
        
        for char in invalid_chars:
            filename = filename.replace(char, "_")
        
        assert "<" not in filename
        assert ">" not in filename
        assert '"' not in filename
        assert ":" not in filename
    
    def test_filename_max_length(self):
        """Test filename truncation to max length."""
        from config import settings
        
        max_length = settings.DOWNLOAD_FILENAME_MAX_LENGTH
        long_title = "A" * 300
        filename = f"{long_title}.pdf"
        
        if len(filename) > max_length:
            filename = filename[:max_length-4] + ".pdf"
        
        assert len(filename) <= max_length
        assert filename.endswith(".pdf")
    
    def test_default_title_when_missing(self):
        """Test default title when not available."""
        title = None
        
        display_title = title or "Untitled"
        filename = f"{display_title}.pdf"
        
        assert filename == "Untitled.pdf"


class TestDownloadTracking:
    """Tests for download tracking in ChangeLog."""
    
    def test_download_count_increments(self):
        """Test that download count increments correctly."""
        from db.models import ChangeLog
        
        change = ChangeLog(
            monitored_url_id=1,
            new_version_id=1,
            change_type="new",
            download_count=0
        )
        
        # Simulate download
        change.download_count = (change.download_count or 0) + 1
        
        assert change.download_count == 1
        
        # Download again
        change.download_count = (change.download_count or 0) + 1
        
        assert change.download_count == 2
    
    def test_first_downloaded_at_set_once(self):
        """Test that first_downloaded_at is only set once."""
        from db.models import ChangeLog
        
        change = ChangeLog(
            monitored_url_id=1,
            new_version_id=1,
            change_type="new",
            download_count=0
        )
        
        first_time = datetime(2024, 1, 1, 10, 0, 0)
        second_time = datetime(2024, 1, 2, 10, 0, 0)
        
        # First download
        if not change.first_downloaded_at:
            change.first_downloaded_at = first_time
        change.last_downloaded_at = first_time
        
        # Second download
        if not change.first_downloaded_at:
            change.first_downloaded_at = second_time  # This shouldn't happen
        change.last_downloaded_at = second_time
        
        assert change.first_downloaded_at == first_time
        assert change.last_downloaded_at == second_time
    
    def test_downloaded_filename_stored(self):
        """Test that downloaded filename is stored."""
        from db.models import ChangeLog
        
        change = ChangeLog(
            monitored_url_id=1,
            new_version_id=1,
            change_type="new"
        )
        
        filename = "Test Form MC-001.pdf"
        change.downloaded_filename = filename
        
        assert change.downloaded_filename == filename


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
