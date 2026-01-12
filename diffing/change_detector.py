"""
Change detection by comparing PDF versions.

Determines if a PDF has changed by comparing:
1. Normalized PDF hash (binary changes)
2. Extracted text hash (semantic changes)
3. Per-page hashes (identify affected pages)
"""

import difflib
from dataclasses import dataclass, field
from typing import Optional
import structlog

from diffing.hasher import Hasher, HashResult

logger = structlog.get_logger()


@dataclass
class ChangeResult:
    """Result of change detection comparison."""
    changed: bool
    change_type: str = ""  # new, unchanged, modified, text_changed, format_only
    pdf_hash_changed: bool = False
    text_hash_changed: bool = False
    affected_pages: list[int] = field(default_factory=list)
    diff_summary: str = ""
    pages_added: int = 0
    pages_removed: int = 0


class ChangeDetector:
    """
    Detects changes between PDF versions.
    
    Change detection logic:
    1. If no previous version exists -> "new" (changed=True)
    2. If text hash differs -> "text_changed" (changed=True, semantic change)
    3. If only PDF hash differs -> "format_only" (changed=True, binary-only change tracked)
    4. If neither differs -> "unchanged" (changed=False)
    
    Note: Format-only changes (PDF hash differs but text identical) are tracked
    but not considered semantic changes. These typically occur due to:
    - Server-generated timestamps
    - Unique document IDs per download
    - Font subsetting variations
    """
    
    def __init__(self):
        self.hasher = Hasher()
        logger.info("ChangeDetector initialized")
    
    def compare(
        self,
        new_hashes: HashResult,
        previous_hashes: Optional[HashResult],
        new_text: str = "",
        previous_text: str = ""
    ) -> ChangeResult:
        """
        Compare new version with previous version.
        
        Args:
            new_hashes: HashResult for new version
            previous_hashes: HashResult for previous version (None if first version)
            new_text: Extracted text from new version (for diff)
            previous_text: Extracted text from previous version (for diff)
            
        Returns:
            ChangeResult with comparison details
        """
        # First version - always "new"
        if previous_hashes is None:
            logger.info("First version detected", change_type="new")
            return ChangeResult(
                changed=True,
                change_type="new"
            )
        
        # Compare hashes
        pdf_changed = new_hashes.pdf_hash != previous_hashes.pdf_hash
        text_changed = new_hashes.text_hash != previous_hashes.text_hash
        
        logger.debug(
            "Hash comparison",
            pdf_changed=pdf_changed,
            text_changed=text_changed
        )
        
        # Determine change type
        if text_changed:
            # Semantic change - text content differs
            change_type = "text_changed"
            
            # Find affected pages
            affected_pages = self.hasher.compare_page_hashes(
                previous_hashes.page_hashes,
                new_hashes.page_hashes
            )
            
            # Generate diff summary
            diff_summary = self._generate_diff_summary(previous_text, new_text)
            
            # Calculate pages added/removed
            pages_added = max(0, len(new_hashes.page_hashes) - len(previous_hashes.page_hashes))
            pages_removed = max(0, len(previous_hashes.page_hashes) - len(new_hashes.page_hashes))
            
            logger.info(
                "Change detected",
                change_type=change_type,
                affected_pages=affected_pages,
                pages_added=pages_added,
                pages_removed=pages_removed
            )
            
            return ChangeResult(
                changed=True,
                change_type=change_type,
                pdf_hash_changed=pdf_changed,
                text_hash_changed=True,
                affected_pages=affected_pages,
                diff_summary=diff_summary,
                pages_added=pages_added,
                pages_removed=pages_removed
            )
        
        elif pdf_changed:
            # Binary change only - no semantic difference
            # This is a format-only change (binary changed but text identical)
            # Common causes:
            # - Server-generated timestamps in PDF metadata
            # - Unique document IDs generated per download
            # - Font subsetting variations
            # - Object ordering differences
            # 
            # We track this as a format-only change (not a semantic change)
            logger.info(
                "Format-only change detected (binary changed, text unchanged)",
                pdf_hash_changed=True,
                text_hash_changed=False
            )
            
            return ChangeResult(
                changed=True,  # Track format-only changes
                change_type="format_only",
                pdf_hash_changed=True,
                text_hash_changed=False,
                diff_summary="Format-only change: PDF binary changed but extracted text is identical. No semantic changes detected."
            )
        
        else:
            # No change
            logger.info("No change detected")
            return ChangeResult(
                changed=False,
                change_type="unchanged"
            )
    
    def _generate_diff_summary(
        self,
        old_text: str,
        new_text: str,
        max_lines: int = 20
    ) -> str:
        """
        Generate a human-readable diff summary.
        
        Args:
            old_text: Previous version text
            new_text: New version text
            max_lines: Maximum lines to include in summary
            
        Returns:
            Diff summary string
        """
        if not old_text and not new_text:
            return "No text content to compare"
        
        if not old_text:
            return f"New content added ({len(new_text)} characters)"
        
        if not new_text:
            return f"All content removed ({len(old_text)} characters)"
        
        # Split into lines for comparison
        old_lines = old_text.splitlines()
        new_lines = new_text.splitlines()
        
        # Generate unified diff
        diff = list(difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile='previous',
            tofile='current',
            lineterm=''
        ))
        
        if not diff:
            return "Text normalized but no line changes"
        
        # Count additions and deletions
        additions = sum(1 for line in diff if line.startswith('+') and not line.startswith('+++'))
        deletions = sum(1 for line in diff if line.startswith('-') and not line.startswith('---'))
        
        # Build summary
        summary_lines = [
            f"Lines added: {additions}, removed: {deletions}",
            "",
            "Diff preview:"
        ]
        
        # Add diff lines up to max
        diff_lines = [line for line in diff if not line.startswith(('---', '+++', '@@'))]
        for line in diff_lines[:max_lines]:
            summary_lines.append(line)
        
        if len(diff_lines) > max_lines:
            summary_lines.append(f"... and {len(diff_lines) - max_lines} more lines")
        
        return '\n'.join(summary_lines)
    
    def get_detailed_diff(
        self,
        old_text: str,
        new_text: str
    ) -> list[str]:
        """
        Get detailed unified diff.
        
        Args:
            old_text: Previous version text
            new_text: New version text
            
        Returns:
            List of diff lines
        """
        old_lines = old_text.splitlines()
        new_lines = new_text.splitlines()
        
        return list(difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile='previous',
            tofile='current',
            lineterm=''
        ))
    
    def get_similarity_ratio(self, old_text: str, new_text: str) -> float:
        """
        Get similarity ratio between two texts.
        
        Args:
            old_text: Previous version text
            new_text: New version text
            
        Returns:
            Similarity ratio (0.0 to 1.0)
        """
        if not old_text and not new_text:
            return 1.0
        if not old_text or not new_text:
            return 0.0
        
        return difflib.SequenceMatcher(None, old_text, new_text).ratio()

