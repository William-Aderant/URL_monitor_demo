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
    
    def __init__(self):
        """Initialize the form matcher with configurable thresholds."""
        from config import settings
        
        # Use configurable thresholds from settings
        self.high_similarity_threshold = settings.HIGH_SIMILARITY_THRESHOLD
        self.low_similarity_threshold = settings.LOW_SIMILARITY_THRESHOLD
        
        logger.info(
            "FormMatcher initialized",
            high_threshold=self.high_similarity_threshold,
            low_threshold=self.low_similarity_threshold
        )
    
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
    
    def _titles_differ(self, old_title: Optional[str], new_title: Optional[str]) -> bool:
        """
        Check if two titles are meaningfully different.
        
        Normalizes titles before comparison to handle minor formatting differences.
        When either title is missing (e.g. extraction failed), we do not treat as "different"
        so form matching falls back to form number or text similarity instead of "Name Change".
        
        Args:
            old_title: Previous version title
            new_title: New version title
            
        Returns:
            True if titles are meaningfully different, False otherwise (or unknown)
        """
        if not old_title and not new_title:
            return False
        if not old_title or not new_title:
            # Unknown (e.g. title extraction failed) - do not assume "name change"
            return False
        
        # Normalize for comparison
        old_normalized = re.sub(r'\s+', ' ', old_title.lower().strip())
        new_normalized = re.sub(r'\s+', ' ', new_title.lower().strip())
        
        # Check if they're the same after normalization
        if old_normalized == new_normalized:
            return False
        
        # Check similarity - if very similar (>90%), consider them the same
        similarity = difflib.SequenceMatcher(None, old_normalized, new_normalized).ratio()
        if similarity >= 0.90:
            return False
        
        return True
    
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
        
        # Strategy 0: Title matching (highest priority - if titles match, it's the same form)
        # This handles cases where form numbers aren't extracted or differ, but titles are identical
        if old_title and new_title:
            title_changed = self._titles_differ(old_title, new_title)
            if not title_changed:
                # Titles are the same - this is definitely the same form, even if content changed
                # Check if form numbers also match for higher confidence
                if old_form_number and new_form_number and old_form_number.upper() == new_form_number.upper():
                    # Same title AND same form number - highest confidence
                    return MatchResult(
                        match_type=MatchType.FORM_NUMBER_MATCH,
                        similarity_score=diff.similarity_score,
                        form_number_old=old_form_number,
                        form_number_new=new_form_number,
                        title_old=old_title,
                        title_new=new_title,
                        confidence=0.98,
                        reason=f"Titles match and form numbers match: {old_form_number}",
                        changed_sections=changed_sections
                    )
                else:
                    # Same title but form numbers differ or missing - still same form
                    return MatchResult(
                        match_type=MatchType.FORM_NUMBER_MATCH,
                        similarity_score=diff.similarity_score,
                        form_number_old=old_form_number,
                        form_number_new=new_form_number,
                        title_old=old_title,
                        title_new=new_title,
                        confidence=0.92,
                        reason=f"Titles match: '{old_title}' (form numbers: {old_form_number or 'N/A'} vs {new_form_number or 'N/A'})",
                        changed_sections=changed_sections
                    )
        
        # Strategy 1: Form number matching (high confidence)
        if old_form_number and new_form_number:
            if old_form_number.upper() == new_form_number.upper():
                # Form numbers match - check if title changed
                title_changed = self._titles_differ(old_title, new_title)
                
                if title_changed:
                    # Same form number but different title = "Updated Form (Name Change)"
                    return MatchResult(
                        match_type=MatchType.SIMILARITY_MATCH,
                        similarity_score=diff.similarity_score,
                        form_number_old=old_form_number,
                        form_number_new=new_form_number,
                        title_old=old_title,
                        title_new=new_title,
                        confidence=0.90,
                        reason=f"Form number {old_form_number} unchanged but title changed: '{old_title}' → '{new_title}'",
                        changed_sections=changed_sections
                    )
                else:
                    # Same form number and same title = "Updated Form (Same Name)"
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
        if diff.similarity_score >= self.high_similarity_threshold:
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
        
        elif diff.similarity_score < self.low_similarity_threshold:
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
            # Uncertain range - try Kendra enhancement if available
            return self._match_with_kendra_fallback(
                diff=diff,
                old_form_number=old_form_number,
                new_form_number=new_form_number,
                old_title=old_title,
                new_title=new_title,
                changed_sections=changed_sections
            )
    
    def _match_with_kendra_fallback(
        self,
        diff,
        old_form_number: Optional[str],
        new_form_number: Optional[str],
        old_title: Optional[str],
        new_title: Optional[str],
        changed_sections: List[str]
    ) -> MatchResult:
        """
        Use Kendra to enhance matching for uncertain cases.
        
        This method is called when text similarity is in the uncertain range.
        It queries Kendra to find similar forms and uses that information
        to refine the match classification.
        """
        # Try to use Kendra if available
        try:
            from services.kendra_client import kendra_client
            from config import settings
            
            if (settings.KENDRA_SEARCH_ENABLED and 
                kendra_client.is_available() and 
                new_form_number):
                # Search for forms with similar form number or title
                query_parts = []
                if new_form_number:
                    query_parts.append(new_form_number)
                if new_title:
                    query_parts.append(new_title)
                
                if query_parts:
                    query = " ".join(query_parts)
                    kendra_response = kendra_client.search(
                        query=query,
                        max_results=5
                    )
                    
                    if kendra_response.success and kendra_response.results:
                        # Check if any results match the old form number
                        for result in kendra_response.results:
                            result_form_number = result.metadata.get('form_number') if result.metadata else None
                            
                            # If we find a match with the old form number, increase confidence
                            if (old_form_number and result_form_number and 
                                old_form_number.upper() == result_form_number.upper()):
                                logger.info(
                                    "Kendra found matching form number",
                                    old_form_number=old_form_number,
                                    new_form_number=new_form_number,
                                    kendra_confidence=result.relevance_score
                                )
                                # Boost confidence based on Kendra result
                                enhanced_confidence = min(0.85, 0.5 + (result.relevance_score or 0.0) * 0.35)
                                return MatchResult(
                                    match_type=MatchType.SIMILARITY_MATCH,
                                    similarity_score=diff.similarity_score,
                                    form_number_old=old_form_number,
                                    form_number_new=new_form_number,
                                    title_old=old_title,
                                    title_new=new_title,
                                    confidence=enhanced_confidence,
                                    reason=f"Uncertain similarity ({diff.similarity_score:.0%}), but Kendra found matching form number: {old_form_number}",
                                    changed_sections=changed_sections
                                )
        except Exception as e:
            logger.warning(
                "Kendra enhancement failed, using default uncertain classification",
                error=str(e)
            )
        
        # Default uncertain classification
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
