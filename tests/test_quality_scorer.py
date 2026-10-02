from docroute.pipeline.quality_scorer import (
    alnum_ratio,
    common_word_hit_rate,
    has_hard_fail_markers,
    page_needs_ocr,
    printable_ratio,
)

CLEAN_TEXT = (
    "The patient was admitted to the hospital and the doctor reviewed "
    "the chart before the treatment was approved for the following week."
)

CORRUPTED_TEXT = "Th3 p@ti3nt w4s @dm!tt3d t0 th3 h0sp!t@l @nd th3 d0ct0r"

EMPTY_TEXT = ""

GLYPH_TEXT = "PRZEDMIAR ROBÓT !GLYPH<15>GLYPH<20>GLYPH<22>'()GLYPH"


def test_clean_text_not_flagged_for_ocr():
    assert page_needs_ocr(CLEAN_TEXT) is False


def test_empty_text_flagged_for_ocr():
    assert page_needs_ocr(EMPTY_TEXT) is True


def test_glyph_markers_flagged_for_ocr():
    assert has_hard_fail_markers(GLYPH_TEXT) is True
    assert page_needs_ocr(GLYPH_TEXT) is True


def test_clean_text_scores_high_on_each_signal():
    assert printable_ratio(CLEAN_TEXT) > 0.95
    assert alnum_ratio(CLEAN_TEXT) > 0.7
    assert common_word_hit_rate(CLEAN_TEXT) > 0.15


def test_corrupted_text_scores_lower_on_common_words():
    # symbol substitution keeps length/printable ratio similar, but real
    # common words basically never survive -- this is why common-word
    # hit rate carries real weight in the blended score
    assert common_word_hit_rate(CORRUPTED_TEXT) < common_word_hit_rate(CLEAN_TEXT)
