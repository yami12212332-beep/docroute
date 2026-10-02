"""
Per-page native-text quality scoring.

Decides, using only the text PyMuPDF already extracted for free (no OCR
run yet), whether a page's native text layer is trustworthy or corrupted.
This is what makes routing per-page instead of per-document possible --
a 12-page PDF with 10 typed pages and 2 scanned pages gets each page
classified independently.

This is a heuristic, not a classifier. Thresholds in config.py need
tuning against your own real documents (see QUALITY_SCORE_THRESHOLD).
"""

from docroute.config import COMMON_WORDS, MIN_TEXT_CHARS, QUALITY_SCORE_THRESHOLD, SCORE_WEIGHTS


def printable_ratio(text: str) -> float:
    """Corrupted font/glyph mappings (bad ToUnicode CMaps) often produce
    control characters or the replacement character instead of real text."""
    if not text:
        return 0.0
    printable = sum(1 for c in text if c.isprintable() and c != "\ufffd")
    return printable / len(text)


def alnum_ratio(text: str) -> float:
    """Garbled text skews toward stray punctuation/symbols relative to
    real letters and digits."""
    stripped = text.replace(" ", "")
    if not stripped:
        return 0.0
    letters_digits = sum(1 for c in stripped if c.isalnum())
    return letters_digits / len(stripped)


def common_word_hit_rate(text: str) -> float:
    """Real English prose has a high density of common function words.
    Corrupted text almost never accidentally reproduces them -- this is
    the strongest single signal of the three."""
    words = text.lower().split()
    if not words:
        return 0.0
    hits = sum(1 for w in words if w.strip(".,;:()[]{}\"'") in COMMON_WORDS)
    return hits / len(words)


def has_hard_fail_markers(text: str) -> bool:
    """Some corruption announces itself directly -- the PDF backend
    already couldn't map a glyph. Treat as an instant force-OCR signal,
    no need to compute the blended score."""
    return "GLYPH<" in text or "\ufffd" in text


def score_page_text(text: str) -> float:
    """Blended 0-1 quality score. Higher = more trustworthy native text."""
    return (
        SCORE_WEIGHTS["printable_ratio"] * printable_ratio(text)
        + SCORE_WEIGHTS["alnum_ratio"] * alnum_ratio(text)
        + SCORE_WEIGHTS["common_word_hit_rate"] * common_word_hit_rate(text)
    )


def page_needs_ocr(text: str) -> bool:
    """
    The actual routing decision for a single page.

    True  -> route this page through forced full-page OCR
    False -> trust the native text layer, skip OCR for this page
    """
    stripped = text.strip()
    if len(stripped) < MIN_TEXT_CHARS:
        return True  # no usable text at all -- obviously a scanned page
    if has_hard_fail_markers(stripped):
        return True  # hard signal, skip scoring
    return score_page_text(stripped) < QUALITY_SCORE_THRESHOLD
