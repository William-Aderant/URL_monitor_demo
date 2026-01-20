"""
Action Recommendation Engine

Provides AI-driven recommendations for how to handle detected form changes.
Implements REQ-001, REQ-003, and REQ-009 from the PoC Solution Design Document.

Recommendations are based on:
- Confidence scores from change detection
- Match type from form matching
- Change type (semantic vs format-only)
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, List
import structlog

from config import settings

logger = structlog.get_logger()


class ActionType(Enum):
    """Recommended actions for detected changes."""
    AUTO_APPROVE = "auto_approve"        # Confidence >= 95% - no human review needed
    REVIEW_SUGGESTED = "review_suggested"  # Confidence 80-95% - quick review recommended
    MANUAL_REQUIRED = "manual_required"    # Confidence < 80% - full manual review needed
    FALSE_POSITIVE = "false_positive"      # Format-only change - auto-dismiss
    NEW_FORM = "new_form"                  # First version - always needs review


# Human-readable labels for actions
ACTION_LABELS = {
    ActionType.AUTO_APPROVE: "Auto-Approved",
    ActionType.REVIEW_SUGGESTED: "Review Suggested",
    ActionType.MANUAL_REQUIRED: "Manual Review Required",
    ActionType.FALSE_POSITIVE: "False Positive (Dismissed)",
    ActionType.NEW_FORM: "New Form (Needs Review)",
}

# CSS classes for UI styling
ACTION_STYLES = {
    ActionType.AUTO_APPROVE: "success",
    ActionType.REVIEW_SUGGESTED: "warning",
    ActionType.MANUAL_REQUIRED: "danger",
    ActionType.FALSE_POSITIVE: "secondary",
    ActionType.NEW_FORM: "info",
}

# Priority levels for triage queue sorting (lower = higher priority)
ACTION_PRIORITY = {
    ActionType.MANUAL_REQUIRED: 1,
    ActionType.NEW_FORM: 2,
    ActionType.REVIEW_SUGGESTED: 3,
    ActionType.AUTO_APPROVE: 4,
    ActionType.FALSE_POSITIVE: 5,
}


@dataclass
class ActionRecommendation:
    """Result of action recommendation analysis."""
    action: ActionType
    confidence: float  # 0.0 to 1.0 - confidence in this recommendation
    rationale: str  # Human-readable explanation
    factors: List[str]  # List of factors that influenced the recommendation
    requires_human_review: bool  # Whether human must review before proceeding
    auto_reviewable: bool  # Whether this can be batch-approved
    priority: int  # Lower = higher priority in triage queue
    
    @property
    def label(self) -> str:
        """Human-readable label for the action."""
        return ACTION_LABELS.get(self.action, self.action.value)
    
    @property
    def style_class(self) -> str:
        """CSS class for UI styling."""
        return ACTION_STYLES.get(self.action, "secondary")
    
    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            "action": self.action.value,
            "label": self.label,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "factors": self.factors,
            "requires_human_review": self.requires_human_review,
            "auto_reviewable": self.auto_reviewable,
            "priority": self.priority,
            "style_class": self.style_class,
        }


class ActionRecommender:
    """
    Recommends actions for detected changes based on confidence and change type.
    
    Implements the following logic:
    1. Format-only changes -> FALSE_POSITIVE (auto-dismiss)
    2. New forms (first version) -> NEW_FORM (needs review)
    3. Confidence >= 95% -> AUTO_APPROVE
    4. Confidence 80-95% -> REVIEW_SUGGESTED
    5. Confidence < 80% -> MANUAL_REQUIRED
    """
    
    def __init__(self):
        """Initialize with configurable thresholds."""
        self.auto_approve_threshold = settings.AUTO_APPROVE_THRESHOLD
        self.review_threshold = settings.REVIEW_THRESHOLD
        self.auto_dismiss_format_only = settings.AUTO_DISMISS_FORMAT_ONLY
        
        logger.info(
            "ActionRecommender initialized",
            auto_approve_threshold=self.auto_approve_threshold,
            review_threshold=self.review_threshold,
            auto_dismiss_format_only=self.auto_dismiss_format_only
        )
    
    def recommend(
        self,
        change_type: str,
        confidence: Optional[float] = None,
        similarity_score: Optional[float] = None,
        match_type: Optional[str] = None,
        is_first_version: bool = False,
        has_form_number_match: bool = False,
        title_changed: bool = False,
        relocated: bool = False,
    ) -> ActionRecommendation:
        """
        Generate action recommendation for a detected change.
        
        Args:
            change_type: Type of change (new, text_changed, format_only, relocated)
            confidence: Overall confidence score (0.0 to 1.0)
            similarity_score: Text similarity between versions (0.0 to 1.0)
            match_type: Form match type (form_number_match, similarity_match, new_form, uncertain)
            is_first_version: Whether this is the first version of the form
            has_form_number_match: Whether form numbers match between versions
            title_changed: Whether the title changed between versions
            relocated: Whether the form URL changed (404 recovery)
            
        Returns:
            ActionRecommendation with recommended action and rationale
        """
        factors = []
        
        # Calculate effective confidence if not provided
        if confidence is None:
            confidence = self._calculate_confidence(
                similarity_score=similarity_score,
                match_type=match_type,
                has_form_number_match=has_form_number_match,
                title_changed=title_changed
            )
        
        # Rule 1: First version always needs review
        if is_first_version or change_type == "new":
            factors.append("First version of form - requires initial review")
            return ActionRecommendation(
                action=ActionType.NEW_FORM,
                confidence=confidence,
                rationale="New form added to monitoring. Initial review required to verify correct classification and metadata.",
                factors=factors,
                requires_human_review=True,
                auto_reviewable=False,
                priority=ACTION_PRIORITY[ActionType.NEW_FORM]
            )
        
        # Rule 2: Format-only changes are false positives
        if change_type == "format_only":
            factors.append("Binary changed but text identical")
            factors.append("No semantic content change detected")
            
            if self.auto_dismiss_format_only:
                factors.append("Auto-dismiss enabled for format-only changes")
                return ActionRecommendation(
                    action=ActionType.FALSE_POSITIVE,
                    confidence=0.98,  # High confidence this is not a real change
                    rationale="Format-only change detected. PDF binary changed but extracted text is identical. This is typically caused by server-generated timestamps or document IDs.",
                    factors=factors,
                    requires_human_review=False,
                    auto_reviewable=True,
                    priority=ACTION_PRIORITY[ActionType.FALSE_POSITIVE]
                )
        
        # Rule 3: Relocated forms with unchanged content
        if relocated and change_type == "relocated":
            factors.append("Form URL changed (404 recovery)")
            factors.append("Content unchanged at new location")
            return ActionRecommendation(
                action=ActionType.AUTO_APPROVE,
                confidence=0.95,
                rationale="Form relocated to new URL but content is unchanged. URL has been automatically updated.",
                factors=factors,
                requires_human_review=False,
                auto_reviewable=True,
                priority=ACTION_PRIORITY[ActionType.AUTO_APPROVE]
            )
        
        # Build factors list for content changes
        if has_form_number_match:
            factors.append(f"Form number match (high confidence)")
        
        if similarity_score is not None:
            factors.append(f"Text similarity: {similarity_score:.0%}")
        
        if match_type:
            factors.append(f"Match type: {match_type.replace('_', ' ')}")
        
        if title_changed:
            factors.append("Title changed between versions")
        
        # Rule 4: High confidence -> Auto-approve
        if confidence >= self.auto_approve_threshold:
            factors.append(f"Confidence {confidence:.0%} >= auto-approve threshold {self.auto_approve_threshold:.0%}")
            return ActionRecommendation(
                action=ActionType.AUTO_APPROVE,
                confidence=confidence,
                rationale=f"High confidence change detection ({confidence:.0%}). Form numbers match and similarity is high. Safe for automatic approval.",
                factors=factors,
                requires_human_review=False,
                auto_reviewable=True,
                priority=ACTION_PRIORITY[ActionType.AUTO_APPROVE]
            )
        
        # Rule 5: Medium confidence -> Review suggested
        if confidence >= self.review_threshold:
            factors.append(f"Confidence {confidence:.0%} in review range ({self.review_threshold:.0%}-{self.auto_approve_threshold:.0%})")
            return ActionRecommendation(
                action=ActionType.REVIEW_SUGGESTED,
                confidence=confidence,
                rationale=f"Moderate confidence change detection ({confidence:.0%}). Quick review recommended to verify the change classification.",
                factors=factors,
                requires_human_review=True,
                auto_reviewable=True,  # Can be batch-approved after quick review
                priority=ACTION_PRIORITY[ActionType.REVIEW_SUGGESTED]
            )
        
        # Rule 6: Low confidence -> Manual review required
        factors.append(f"Confidence {confidence:.0%} < review threshold {self.review_threshold:.0%}")
        return ActionRecommendation(
            action=ActionType.MANUAL_REQUIRED,
            confidence=confidence,
            rationale=f"Low confidence change detection ({confidence:.0%}). Manual review required to determine if this is a valid update or a different form.",
            factors=factors,
            requires_human_review=True,
            auto_reviewable=False,
            priority=ACTION_PRIORITY[ActionType.MANUAL_REQUIRED]
        )
    
    def _calculate_confidence(
        self,
        similarity_score: Optional[float] = None,
        match_type: Optional[str] = None,
        has_form_number_match: bool = False,
        title_changed: bool = False
    ) -> float:
        """
        Calculate overall confidence from component scores.
        
        Confidence is weighted based on:
        - Form number match: +0.4 (strong signal)
        - Similarity score: up to +0.4
        - Match type: up to +0.2
        - Title change: -0.1 (reduces confidence slightly)
        """
        confidence = 0.5  # Base confidence
        
        # Form number match is a strong positive signal
        if has_form_number_match:
            confidence += 0.4
        
        # Similarity score contributes up to 0.4
        if similarity_score is not None:
            confidence += similarity_score * 0.4
        
        # Match type contributes up to 0.2
        if match_type:
            match_type_scores = {
                "form_number_match": 0.2,
                "similarity_match": 0.15,
                "new_form": 0.0,
                "uncertain": 0.0,
            }
            confidence += match_type_scores.get(match_type, 0.0)
        
        # Title change slightly reduces confidence
        if title_changed:
            confidence -= 0.1
        
        # Clamp to valid range
        return max(0.0, min(1.0, confidence))
    
    def get_batch_recommendations(
        self,
        changes: list
    ) -> dict:
        """
        Get recommendations for a batch of changes.
        
        Args:
            changes: List of change dictionaries with required fields
            
        Returns:
            Dictionary with summary statistics and per-change recommendations
        """
        recommendations = []
        counts = {action_type: 0 for action_type in ActionType}
        
        for change in changes:
            rec = self.recommend(
                change_type=change.get("change_type", "unknown"),
                confidence=change.get("confidence"),
                similarity_score=change.get("similarity_score"),
                match_type=change.get("match_type"),
                is_first_version=change.get("is_first_version", False),
                has_form_number_match=change.get("has_form_number_match", False),
                title_changed=change.get("title_changed", False),
                relocated=change.get("relocated", False),
            )
            
            recommendations.append({
                "change_id": change.get("id"),
                "recommendation": rec.to_dict()
            })
            counts[rec.action] += 1
        
        # Calculate automation rate
        total = len(changes)
        auto_approved = counts[ActionType.AUTO_APPROVE] + counts[ActionType.FALSE_POSITIVE]
        automation_rate = auto_approved / total if total > 0 else 0.0
        
        # Calculate review rate
        needs_review = counts[ActionType.MANUAL_REQUIRED] + counts[ActionType.NEW_FORM]
        review_rate = needs_review / total if total > 0 else 0.0
        
        return {
            "total_changes": total,
            "counts": {k.value: v for k, v in counts.items()},
            "automation_rate": automation_rate,
            "review_rate": review_rate,
            "recommendations": recommendations
        }


# Singleton instance
action_recommender = ActionRecommender()
