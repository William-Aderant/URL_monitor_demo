"""
Change detection by comparing PDF versions.

Determines if a PDF has changed by comparing:
1. Original PDF hash (binary changes)
2. Extracted text hash (semantic changes)
3. Per-page hashes (identify affected pages with early termination)
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
    change_type: str = ""  # new, unchanged, modified, text_changed, format_only, title_changed
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
        
        # Early termination: Compare page hashes first (fastest check)
        # If no pages changed, we can skip expensive text comparison
        # Then verify with similarity check to avoid false positives from extraction noise
        changed_pages = self._compare_pages_with_similarity(
            previous_hashes.page_hashes,
            new_hashes.page_hashes,
            previous_text,
            new_text,
            len(previous_hashes.page_hashes),
            len(new_hashes.page_hashes)
        )
        
        logger.debug(
            "Page comparison with similarity check",
            changed_pages=changed_pages,
            new_page_count=len(new_hashes.page_hashes),
            prev_page_count=len(previous_hashes.page_hashes)
        )
        
        # If no pages changed and page counts match, likely no change
        if not changed_pages and len(new_hashes.page_hashes) == len(previous_hashes.page_hashes):
            # Still check PDF and text hashes to be sure
            pdf_changed = new_hashes.pdf_hash != previous_hashes.pdf_hash
            text_changed = new_hashes.text_hash != previous_hashes.text_hash
            
            if not pdf_changed and not text_changed:
                # No change detected - early exit
                logger.info("No change detected (early termination)")
                return ChangeResult(
                    changed=False,
                    change_type="unchanged"
                )
        
        # Compare hashes
        pdf_changed = new_hashes.pdf_hash != previous_hashes.pdf_hash
        text_changed = new_hashes.text_hash != previous_hashes.text_hash
        
        logger.debug(
            "Hash comparison",
            pdf_changed=pdf_changed,
            text_changed=text_changed,
            new_text_hash=new_hashes.text_hash[:16] + "...",
            prev_text_hash=previous_hashes.text_hash[:16] + "..."
        )
        
        # Additional similarity check to catch false positives
        # If hashes differ but similarity is very high (>99.5%), it's likely extraction noise
        if text_changed and new_text and previous_text:
            similarity = self.get_similarity_ratio(previous_text, new_text)
            if similarity >= 0.995:  # 99.5% similarity threshold
                logger.warning(
                    "Text hash differs but similarity is very high - likely extraction noise",
                    similarity=f"{similarity:.3%}",
                    treating_as="unchanged"
                )
                # Treat as unchanged - hash difference is likely due to extraction variations
                text_changed = False
        
        # Determine change type
        if text_changed:
            # Semantic change - text content differs
            change_type = "text_changed"
            
            # Use already computed changed pages (from early termination check)
            affected_pages = changed_pages
            
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
    
    def _normalize_text_for_comparison(self, text: str) -> str:
        """
        Normalize text for similarity comparison.
        
        Uses similar normalization as text hash computation to ensure consistency.
        
        Args:
            text: Text to normalize
            
        Returns:
            Normalized text
        """
        if not text:
            return ""
        
        import re
        
        # Normalize whitespace (same as in Hasher.compute_text_hash)
        normalized = ' '.join(text.split())
        
        # Remove zero-width spaces and other invisible characters
        normalized = re.sub(r'[\u200b-\u200f\ufeff]', '', normalized)
        
        # Normalize quotes and apostrophes
        normalized = normalized.replace('"', '"').replace('"', '"')
        normalized = normalized.replace("'", "'").replace("'", "'")
        
        # Remove extra spaces
        normalized = re.sub(r'\s+', ' ', normalized)
        
        # Strip and lowercase for consistent comparison
        return normalized.strip().lower()
    
    def _split_text_by_pages(self, text: str, page_count: int) -> list[str]:
        """
        Split full text into approximate page texts.
        
        Args:
            text: Full extracted text
            page_count: Number of pages
            
        Returns:
            List of page texts
        """
        if page_count <= 1:
            return [text] if text else [""]
        
        # Try to split by form feed characters (common page separator)
        if "\f" in text:
            pages = text.split("\f")
            # Pad or trim to match page_count
            while len(pages) < page_count:
                pages.append("")
            return pages[:page_count]
        
        # Otherwise, split approximately by length
        lines = text.splitlines()
        if not lines:
            return [""] * page_count
        
        lines_per_page = max(1, len(lines) // page_count)
        pages = []
        
        for i in range(page_count):
            start = i * lines_per_page
            end = start + lines_per_page if i < page_count - 1 else len(lines)
            page_text = "\n".join(lines[start:end])
            pages.append(page_text)
        
        return pages
    
    def _compare_pages_with_similarity(
        self,
        old_hashes: list[str],
        new_hashes: list[str],
        old_text: str,
        new_text: str,
        old_page_count: int,
        new_page_count: int,
        similarity_threshold: float = 0.995
    ) -> list[int]:
        """
        Compare page hashes and verify with similarity checks.
        
        Only marks a page as changed if:
        1. The page hash differs, AND
        2. The actual page text similarity is below threshold
        
        This prevents false positives from extraction noise.
        
        Args:
            old_hashes: Previous version page hashes
            new_hashes: New version page hashes
            old_text: Previous version full text
            new_text: New version full text
            old_page_count: Previous version page count
            new_page_count: New version page count
            similarity_threshold: Minimum similarity to consider unchanged (default 99.5%)
            
        Returns:
            List of 1-indexed page numbers that actually changed
        """
        changed_pages = []
        
        # Split texts into pages
        old_page_texts = self._split_text_by_pages(old_text, old_page_count)
        new_page_texts = self._split_text_by_pages(new_text, new_page_count)
        
        # Compare each page
        max_pages = max(len(old_hashes), len(new_hashes))
        
        for i in range(max_pages):
            old_hash = old_hashes[i] if i < len(old_hashes) else None
            new_hash = new_hashes[i] if i < len(new_hashes) else None
            
            # If hashes are the same, page is unchanged
            if old_hash == new_hash:
                continue
            
            # Hashes differ - check actual text similarity
            old_page_text = old_page_texts[i] if i < len(old_page_texts) else ""
            new_page_text = new_page_texts[i] if i < len(new_page_texts) else ""
            
            # If one page is missing (page added/removed), mark as changed
            if (old_hash is None) != (new_hash is None):
                changed_pages.append(i + 1)  # 1-indexed
                continue
            
            # Both pages exist - normalize and compare with similarity
            # Normalize texts similar to how text hash is computed (for consistency)
            old_normalized = self._normalize_text_for_comparison(old_page_text)
            new_normalized = self._normalize_text_for_comparison(new_page_text)
            
            similarity = self.get_similarity_ratio(old_normalized, new_normalized)
            
            # Only mark as changed if similarity is below threshold
            if similarity < similarity_threshold:
                changed_pages.append(i + 1)  # 1-indexed
                logger.debug(
                    "Page marked as changed after similarity check",
                    page=i + 1,
                    similarity=f"{similarity:.3%}",
                    threshold=f"{similarity_threshold:.3%}"
                )
            else:
                logger.debug(
                    "Page hash differs but similarity is high - treating as unchanged",
                    page=i + 1,
                    similarity=f"{similarity:.3%}",
                    threshold=f"{similarity_threshold:.3%}"
                )
        
        return changed_pages

