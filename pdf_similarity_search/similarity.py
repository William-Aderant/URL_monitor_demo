"""Text-based similarity (TF-IDF + cosine) for PDF comparison."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def text_similarity_score(ref_text: str, pdf_text: str) -> float:
    """Similarity 0–100 between two text strings (TF-IDF + cosine)."""
    if not ref_text.strip() or not pdf_text.strip():
        return 0.0
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
        vectorizer = TfidfVectorizer(max_features=10_000, stop_words="english", ngram_range=(1, 2))
        matrix = vectorizer.fit_transform([ref_text, pdf_text])
        sim = cosine_similarity(matrix[0:1], matrix[1:2])[0, 0]
        return float(min(100.0, max(0.0, sim * 100.0)))
    except Exception as e:
        logger.warning("text similarity failed: %s", e)
        return 0.0
