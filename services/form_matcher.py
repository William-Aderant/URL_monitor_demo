"""
Form Matcher Service

Provides hybrid matching to determine if two PDF versions represent:
- The same form that was updated
- An entirely new form
- Uncertain (needs manual review)

Uses form number matching first, then text similarity for edge cases.
"""

import difflib
import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional, List, Tuple

import structlog

logger = structlog.get_logger()


class MatchType(Enum):
    """Types of form matches."""
    FORM_NUMBER_MATCH = "form_number_match"  # Same form number
    SIMILARITY_MATCH = "similarity_match"     # High text similarity
    NEW_FORM = "new_form"                     # Completely different form
    UNCERTAIN = "uncertain"                    # Needs manual review


# Human-readable display labels for PoC alignment
# Maps internal technical names to user-facing labels per Forms Workflow PoC
MATCH_TYPE_LABELS = {
    "form_number_match": "Updated Form (Same Name)",
    "similarity_match": "Updated Form (Name Change)",
    "new_form": "New Form",
    "uncertain": "Needs Review",
    "new": "New Form",  # For initial versions
    "text_changed": "Updated",
    "unchanged": "No Change",
}


def get_match_type_label(match_type: str) -> str:
    """
    Convert a technical match type to its display label.
    
    Args:
        match_type: Technical match type string (e.g., "form_number_match")
        
    Returns:
        Human-readable display label (e.g., "Updated Form (Same Name)")
    """
    if match_type is None:
        return "Unknown"
    return MATCH_TYPE_LABELS.get(match_type, match_type.replace("_", " ").title())


def get_classification_from_match(match_type: str, is_first_version: bool = False) -> str:
    """
    Get the PoC classification for a form based on match type.
    
    Per PoC Step 3, forms are classified as:
    - "New Form" - First version or completely different form
    - "Updated Form (Same Name)" - Same form number, content changed
    - "Updated Form (Name Change)" - Similar content, different name/number
    
    Args:
        match_type: Technical match type string
        is_first_version: Whether this is the first version of the form
        
    Returns:
        Classification string per PoC terminology
    """
    if is_first_version:
        return "New Form"
    return get_match_type_label(match_type)


@dataclass
class MatchResult:
    """Result of comparing two form versions."""
    match_type: MatchType
    similarity_score: float  # 0.0 to 1.0
    form_number_old: Optional[str]
    form_number_new: Optional[str]
    title_old: Optional[str]
    title_new: Optional[str]
    confidence: float  # Confidence in the match classification
    reason: str  # Human-readable explanation
    changed_sections: List[str]  # List of sections that changed


@dataclass
class TextDiff:
    """Detailed text difference between versions."""
    similarity_score: float
    added_lines: List[str]
    removed_lines: List[str]
    changed_line_count: int
    total_lines_old: int
    total_lines_new: int


class FormMatcher:
    """
    Matches form versions using a hybrid approach:
    1. First try exact form number matching
    2. Fall back to text similarity analysis
    """
    
    # Thresholds for classification
    HIGH_SIMILARITY_THRESHOLD = 0.80  # Above this = same form updated
    LOW_SIMILARITY_THRESHOLD = 0.50   # Below this = new form
    # Between these = uncertain
    
    def __init__(self):
        """Initialize the form matcher."""
        logger.info("FormMatcher initialized")
    
    def extract_form_number(self, text: str) -> Optional[str]:
        """
        Extract form number from text content.
        
        Looks for patterns like:
            - CIV-775, ADR-103, MC-025
            - Form Number: CIV-775
            - FORM CIV-775
        
        Args:
            text: Text to search
            
        Returns:
            Normalized form number or None
        """
        if not text:
            return None
        
        # Patterns in order of specificity
        patterns = [
            # "Form Number: CIV-775" or "Form No. CIV-775"
            r'[Ff]orm\s*(?:[Nn]o\.?|[Nn]umber:?)\s*([A-Za-z]{2,4})-?(\d{2,4})',
            # "FORM CIV-775" at start of line
            r'^[Ff][Oo][Rr][Mm]\s+([A-Za-z]{2,4})-?(\d{2,4})',
            # Standalone form number with hyphen
            r'\b([A-Za-z]{2,4})-(\d{2,4})\b',
            # Form number without hyphen
            r'\b([Cc][Ii][Vv]|[Aa][Dd][Rr]|[Mm][Cc]|[Ff][Ww])(\d{3,4})\b',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text, re.MULTILINE)
            if match:
                groups = match.groups()
                if len(groups) == 2:
                    prefix, number = groups
                    return f"{prefix.upper()}-{number}"
        
        return None
    
    def calculate_text_similarity(self, text1: str, text2: str) -> TextDiff:
        """
        Calculate detailed text similarity between two versions.
        
        Args:
            text1: Old version text
            text2: New version text
            
        Returns:
            TextDiff with similarity metrics
        """
        if not text1 and not text2:
            return TextDiff(
                similarity_score=1.0,
                added_lines=[],
                removed_lines=[],
                changed_line_count=0,
                total_lines_old=0,
                total_lines_new=0
            )
        
        if not text1:
            lines2 = text2.splitlines()
            return TextDiff(
                similarity_score=0.0,
                added_lines=lines2,
                removed_lines=[],
                changed_line_count=len(lines2),
                total_lines_old=0,
                total_lines_new=len(lines2)
            )
        
        if not text2:
            lines1 = text1.splitlines()
            return TextDiff(
                similarity_score=0.0,
                added_lines=[],
                removed_lines=lines1,
                changed_line_count=len(lines1),
                total_lines_old=len(lines1),
                total_lines_new=0
            )
        
        # Normalize text for comparison
        text1_normalized = self._normalize_text(text1)
        text2_normalized = self._normalize_text(text2)
        
        # Calculate overall similarity
        similarity = difflib.SequenceMatcher(
            None, 
            text1_normalized, 
            text2_normalized
        ).ratio()
        
        # Get line-by-line diff
        lines1 = text1.splitlines()
        lines2 = text2.splitlines()
        
        differ = difflib.Differ()
        diff = list(differ.compare(lines1, lines2))
        
        added_lines = [line[2:] for line in diff if line.startswith('+ ')]
        removed_lines = [line[2:] for line in diff if line.startswith('- ')]
        
        return TextDiff(
            similarity_score=similarity,
            added_lines=added_lines,
            removed_lines=removed_lines,
            changed_line_count=len(added_lines) + len(removed_lines),
            total_lines_old=len(lines1),
            total_lines_new=len(lines2)
        )
    
    def _normalize_text(self, text: str) -> str:
        """
        Normalize text for comparison.
        
        Removes extra whitespace, lowercases, removes common noise.
        
        Args:
            text: Text to normalize
            
        Returns:
            Normalized text
        """
        # Lowercase
        text = text.lower()
        
        # Remove extra whitespace
        text = re.sub(r'\s+', ' ', text)
        
        # Remove common noise patterns (page numbers, dates that might change)
        text = re.sub(r'\bpage\s*\d+\s*of\s*\d+\b', '', text)
        text = re.sub(r'\b\d{1,2}/\d{1,2}/\d{2,4}\b', '', text)  # Dates
        text = re.sub(r'\brev\.?\s*\d{1,2}/\d{2,4}\b', '', text)  # Revision dates
        
        return text.strip()
    
    def match_forms(
        self,
        old_text: str,
        new_text: str,
        old_form_number: Optional[str] = None,
        new_form_number: Optional[str] = None,
        old_title: Optional[str] = None,
        new_title: Optional[str] = None
    ) -> MatchResult:
        """
        Match two form versions to determine if they are the same form.
        
        Args:
            old_text: Text content of old version
            new_text: Text content of new version
            old_form_number: Known form number from old version
            new_form_number: Known form number from new version
            old_title: Title of old version
            new_title: Title of new version
            
        Returns:
            MatchResult with classification and details
        """
        # Extract form numbers if not provided
        if not old_form_number:
            old_form_number = self.extract_form_number(old_text or "")
        if not new_form_number:
            new_form_number = self.extract_form_number(new_text or "")
        
        logger.info(
            "Matching forms",
            old_form_number=old_form_number,
            new_form_number=new_form_number,
            old_title=old_title,
            new_title=new_title
        )
        
        # Calculate text similarity
        diff = self.calculate_text_similarity(old_text or "", new_text or "")
        
        # Identify changed sections
        changed_sections = self._identify_changed_sections(
            diff.added_lines, 
            diff.removed_lines
        )
        
        # Strategy 1: Form number matching (highest confidence)
        if old_form_number and new_form_number:
            if old_form_number.upper() == new_form_number.upper():
                return MatchResult(
                    match_type=MatchType.FORM_NUMBER_MATCH,
                    similarity_score=diff.similarity_score,
                    form_number_old=old_form_number,
                    form_number_new=new_form_number,
                    title_old=old_title,
                    title_new=new_title,
                    confidence=0.95,
                    reason=f"Form numbers match: {old_form_number}",
                    changed_sections=changed_sections
                )
            else:
                # Different form numbers - likely a new form
                return MatchResult(
                    match_type=MatchType.NEW_FORM,
                    similarity_score=diff.similarity_score,
                    form_number_old=old_form_number,
                    form_number_new=new_form_number,
                    title_old=old_title,
                    title_new=new_title,
                    confidence=0.90,
                    reason=f"Form numbers differ: {old_form_number} vs {new_form_number}",
                    changed_sections=changed_sections
                )
        
        # Strategy 2: Text similarity matching
        if diff.similarity_score >= self.HIGH_SIMILARITY_THRESHOLD:
            return MatchResult(
                match_type=MatchType.SIMILARITY_MATCH,
                similarity_score=diff.similarity_score,
                form_number_old=old_form_number,
                form_number_new=new_form_number,
                title_old=old_title,
                title_new=new_title,
                confidence=diff.similarity_score,
                reason=f"High text similarity: {diff.similarity_score:.0%}",
                changed_sections=changed_sections
            )
        
        elif diff.similarity_score < self.LOW_SIMILARITY_THRESHOLD:
            return MatchResult(
                match_type=MatchType.NEW_FORM,
                similarity_score=diff.similarity_score,
                form_number_old=old_form_number,
                form_number_new=new_form_number,
                title_old=old_title,
                title_new=new_title,
                confidence=1.0 - diff.similarity_score,
                reason=f"Low text similarity: {diff.similarity_score:.0%}",
                changed_sections=changed_sections
            )
        
        else:
            # Uncertain range - needs manual review
            return MatchResult(
                match_type=MatchType.UNCERTAIN,
                similarity_score=diff.similarity_score,
                form_number_old=old_form_number,
                form_number_new=new_form_number,
                title_old=old_title,
                title_new=new_title,
                confidence=0.5,
                reason=f"Moderate similarity ({diff.similarity_score:.0%}) - manual review recommended",
                changed_sections=changed_sections
            )
    
    def _identify_changed_sections(
        self, 
        added_lines: List[str], 
        removed_lines: List[str]
    ) -> List[str]:
        """
        Identify which sections of the form changed based on diff.
        
        Args:
            added_lines: Lines added in new version
            removed_lines: Lines removed from old version
            
        Returns:
            List of section names/descriptions that changed
        """
        sections = set()
        
        # Common section indicators in court forms
        section_patterns = [
            (r'^(?:\d+\.|\([a-z]\)|\([0-9]+\))\s*(.+)', 'Numbered section'),
            (r'^INSTRUCTIONS', 'Instructions'),
            (r'^NOTICE', 'Notice'),
            (r'^WARNING', 'Warning'),
            (r'^DECLARATION', 'Declaration'),
            (r'^CERTIFICATE', 'Certificate'),
            (r'^PROOF OF', 'Proof of Service'),
            (r'^ORDER', 'Order'),
            (r'^FOR COURT USE', 'Court Use Section'),
        ]
        
        all_changed = added_lines + removed_lines
        
        for line in all_changed:
            line_upper = line.strip().upper()
            
            for pattern, section_name in section_patterns:
                if re.match(pattern, line_upper):
                    sections.add(section_name)
                    break
            else:
                # Check for field changes
                if ':' in line and len(line) < 100:
                    field_name = line.split(':')[0].strip()
                    if field_name:
                        sections.add(f"Field: {field_name}")
        
        return list(sections)[:10]  # Limit to top 10 sections
    
    def generate_diff_summary(self, diff: TextDiff) -> str:
        """
        Generate a human-readable summary of changes.
        
        Args:
            diff: TextDiff object
            
        Returns:
            Summary string
        """
        lines = []
        
        lines.append(f"Similarity: {diff.similarity_score:.1%}")
        lines.append(f"Lines changed: {diff.changed_line_count}")
        
        if diff.added_lines:
            lines.append(f"\nAdded ({len(diff.added_lines)} lines):")
            for line in diff.added_lines[:5]:
                lines.append(f"  + {line[:80]}{'...' if len(line) > 80 else ''}")
            if len(diff.added_lines) > 5:
                lines.append(f"  ... and {len(diff.added_lines) - 5} more")
        
        if diff.removed_lines:
            lines.append(f"\nRemoved ({len(diff.removed_lines)} lines):")
            for line in diff.removed_lines[:5]:
                lines.append(f"  - {line[:80]}{'...' if len(line) > 80 else ''}")
            if len(diff.removed_lines) > 5:
                lines.append(f"  ... and {len(diff.removed_lines) - 5} more")
        
        return "\n".join(lines)
