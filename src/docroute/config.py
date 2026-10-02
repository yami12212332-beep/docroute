"""
All tunable constants live here. Nothing in pipeline/ or images/ should
hardcode a threshold -- if you're tuning the quality scorer against real
documents (hospital records, revenue reports, tutorials), this is the
only file you should need to touch for that.
"""

# --- Page classification (native text vs scanned) ------------------------

# Below this many characters, a page is treated as having no usable text
# layer at all (pure scan) -- skips scoring, goes straight to forced OCR.
MIN_TEXT_CHARS = 30

# Below this combined quality score (0-1), a page's native text is treated
# as corrupted/partial and routed to forced OCR instead of being trusted.
# Tune this against a labeled sample of your own "known good" / "known bad"
# pages -- it is NOT a universal constant.
QUALITY_SCORE_THRESHOLD = 0.6

# Weights for the blended quality score. Must sum to 1.0.
SCORE_WEIGHTS = {
    "printable_ratio": 0.4,
    "alnum_ratio": 0.3,
    "common_word_hit_rate": 0.3,
}

# Common English function words used for the common-word hit-rate signal.
# Documents in this project are English-only (personal/hospital records,
# tutorials, revenue reports) -- extend or swap this list if that changes.
COMMON_WORDS = {
    "the", "and", "of", "to", "in", "is", "was", "for", "on", "that",
    "with", "as", "it", "by", "are", "be", "this", "from", "or", "at",
    "an", "will", "has", "have", "not", "your", "you", "we", "all",
}

# --- OCR / conversion ------------------------------------------------------

OCR_LANG = ["en"]

# --- Layout classification (Tier 1: geometric, no model call) -------------
#
# Distinguishes three page layouts from Docling's raw text-block bounding
# boxes alone, before deciding whether the default reading order can be
# trusted:
#   - single_column:      one flowing column, default order is fine
#   - parallel_columns:   two genuinely independent columns of running
#                         text (e.g. a research paper) -- default order
#                         (read down one column, then the other) is fine
#   - label_value_form:   narrow labels paired row-by-row with wider
#                         descriptive text (e.g. "TREATMENT GIVEN : ...")
#                         -- default order is WRONG, needs the row-based
#                         override in pipeline/reading_order.py
# Anything that doesn't cleanly match one of these (>2 x-position
# clusters, or ambiguous pairing/width signals) is "ambiguous" and flagged
# for Tier 2 (vision-model) escalation -- not yet implemented, see
# pipeline/router.py.
#
# These thresholds are unvalidated starting points -- tune them against a
# labeled sample of your own real documents before trusting them in
# production, same caveat as QUALITY_SCORE_THRESHOLD above.

# Fraction of page width used as the gap threshold when clustering text
# blocks by their left x-position into columns.
LAYOUT_GAP_RATIO = 0.05

# Row-pairing score (fraction of left-cluster blocks whose START position
# lands on the same line as some right-cluster block's start -- see
# pipeline/layout_classifier.py's _pairing_score docstring for why this is
# start-proximity and not range-overlap) at or above this suggests
# label:value pairing.
LAYOUT_PAIRING_HIGH = 0.6

# Row-pairing score at or below this suggests independently-flowing
# parallel columns (no row-to-row pairing).
LAYOUT_PAIRING_LOW = 0.3

# Left-cluster-width / right-cluster-width at or below this (left much
# narrower than right) supports label:value.
LAYOUT_WIDTH_RATIO_NARROW = 0.6

# Width ratio at or above this (both clusters roughly similar width)
# supports parallel columns.
LAYOUT_WIDTH_RATIO_SIMILAR = 0.75

# Below this confidence, a page is flagged needs_escalation=True even if
# it technically matched one of the two clear-cut branches.
LAYOUT_CONFIDENCE_ESCALATION_THRESHOLD = 0.5

# Vertical (y) distance, in the same units as the bounding boxes, within
# which two blocks are considered "the same row" when reconstructing
# label:value reading order. Tune against your actual page resolution --
# too tight splits wrapped paragraph lines into false rows, too loose
# merges genuinely different rows.
ROW_GROUPING_TOLERANCE = 10.0

# Below this many blocks, a detected x-cluster is treated as noise (a
# stray header/footer fragment that slipped through, or a wrapped line
# with slightly different indentation than the rest of its own paragraph)
# and merged into its nearest neighboring cluster rather than counted as
# a genuine column. Found necessary after testing against a real scanned
# page produced 5 raw clusters instead of the real 2 -- most of the extra
# ones were single stray blocks, not real columns.
LAYOUT_MIN_CLUSTER_BLOCKS = 2

# Docling element labels excluded from layout clustering entirely --
# running headers/footers, page numbers, and captions aren't part of the
# body reading-order question and only introduce noise (e.g. a page
# header sitting at a third x-position that isn't really a "column").
# Matched as a case-insensitive substring against the element's label, so
# this doesn't depend on the exact enum casing/format your Docling
# version uses.
LAYOUT_EXCLUDED_LABEL_SUBSTRINGS = {"page_header", "page_footer", "footnote", "caption"}

# --- Image extraction / storage --------------------------------------------

IMAGE_RESOLUTION_SCALE = 2.0  # ~144 DPI; raise for higher-fidelity crops
IMAGE_OUTPUT_DIR = "output/images"

# --- Output ------------------------------------------------------------

OUTPUT_DIR = "output"
