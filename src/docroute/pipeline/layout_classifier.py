"""
Tier 1 layout classification: geometric, deterministic, no model call.

Answers the question that started this module: given a page's text block
bounding boxes, is Docling's default reading order trustworthy, or does
this page need the row-based label:value override in reading_order.py?

IMPORTANT -- verify before trusting: Docling's BoundingBox can use either
a top-left or bottom-left coordinate origin depending on the backend/
version (`bbox.coord_origin`). This module normalizes to top-left via
`to_top_left_origin()` when that attribute is present, but this has NOT
been empirically verified against your installed Docling version. Before
relying on this in production, print a few raw bboxes from a real page
and confirm y0 actually increases going DOWN the page -- see the
`_debug_print_blocks` helper at the bottom of this file.
"""

from dataclasses import dataclass

from docroute.config import (
    LAYOUT_CONFIDENCE_ESCALATION_THRESHOLD,
    LAYOUT_EXCLUDED_LABEL_SUBSTRINGS,
    LAYOUT_GAP_RATIO,
    LAYOUT_MIN_CLUSTER_BLOCKS,
    LAYOUT_PAIRING_HIGH,
    LAYOUT_PAIRING_LOW,
    LAYOUT_WIDTH_RATIO_NARROW,
    LAYOUT_WIDTH_RATIO_SIMILAR,
    ROW_GROUPING_TOLERANCE,
)


@dataclass
class TextBlock:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0


@dataclass
class LayoutClassification:
    layout_type: str  # "single_column" | "parallel_columns" | "label_value_form" | "ambiguous"
    confidence: float
    needs_escalation: bool
    features: dict


def _normalize_bbox_to_top_left(bbox, page_height: float):
    """Docling bounding boxes carry a coord_origin field. If it's
    bottom-left (common for raw PDF coordinates), convert so that smaller
    y means higher on the page -- everything below assumes that."""
    coord_origin = getattr(bbox, "coord_origin", None)
    if coord_origin is not None and "BOTTOMLEFT" in str(coord_origin).upper():
        to_top_left = getattr(bbox, "to_top_left_origin", None)
        if callable(to_top_left):
            return to_top_left(page_height)
    return bbox


def _get_page_height(docling_result, blocks: list[TextBlock]) -> float:
    try:
        page = next(iter(docling_result.document.pages.values()))
        return page.size.height
    except Exception:
        pass
    if not blocks:
        return 1.0
    return max(b.y1 for b in blocks) - min(b.y0 for b in blocks)


def _get_page_width(docling_result, blocks: list[TextBlock]) -> float:
    try:
        page = next(iter(docling_result.document.pages.values()))
        return page.size.width
    except Exception:
        pass
    if not blocks:
        return 1.0
    return max(b.x1 for b in blocks) - min(b.x0 for b in blocks)


def extract_text_blocks(docling_result) -> list[TextBlock]:
    """Pulls every BODY text element's bounding box + text out of a
    converted (single-page) Docling result, normalized to top-left
    origin. Excludes headers/footers/captions (LAYOUT_EXCLUDED_LABEL_SUBSTRINGS)
    -- those aren't part of the body reading-order question and only
    introduce noise into column clustering (a running header sitting at
    its own x-position looks like a third "column" otherwise)."""
    doc = docling_result.document
    raw = []
    for element, _level in doc.iterate_items():
        if not getattr(element, "prov", None):
            continue
        text = getattr(element, "text", None)
        if not text:
            continue
        label = getattr(element, "label", None)
        label_str = str(label).lower() if label is not None else ""
        if any(excluded in label_str for excluded in LAYOUT_EXCLUDED_LABEL_SUBSTRINGS):
            continue
        raw.append((element.prov[0].bbox, text))

    if not raw:
        return []

    # first pass with un-normalized coords just to get a page-height estimate
    prelim = [TextBlock(b.l, b.t, b.r, b.b, t) for b, t in raw]
    page_height = _get_page_height(docling_result, prelim)

    blocks = []
    for bbox, text in raw:
        norm = _normalize_bbox_to_top_left(bbox, page_height)
        blocks.append(TextBlock(x0=norm.l, y0=norm.t, x1=norm.r, y1=norm.b, text=text))
    return blocks


def cluster_x_positions(blocks: list[TextBlock], page_width: float, gap_ratio: float) -> list[list[TextBlock]]:
    """Groups blocks into columns by left-edge (x0) position. Blocks
    whose x0 is within `gap_ratio * page_width` of the previous block's
    x0 (sorted ascending) join the same cluster."""
    if not blocks:
        return []
    sorted_blocks = sorted(blocks, key=lambda b: b.x0)
    gap_threshold = gap_ratio * page_width

    clusters: list[list[TextBlock]] = [[sorted_blocks[0]]]
    last_x0 = sorted_blocks[0].x0
    for b in sorted_blocks[1:]:
        if b.x0 - last_x0 > gap_threshold:
            clusters.append([])
        clusters[-1].append(b)
        last_x0 = b.x0
    return clusters


def _pairing_score(left: list[TextBlock], right: list[TextBlock], y0_tolerance: float = None) -> float:
    """Fraction of left-cluster blocks whose START (y0) lands within
    y0_tolerance of some right-cluster block's start -- i.e. they begin
    on the same visual line, which is how a label and its value are
    actually typeset.

    Deliberately NOT range-overlap (does block A's y-span intersect
    block B's y-span): two independent, similarly-sized columns overlap
    in y-range constantly by pure chance (any two adjacent lines nearly
    always overlap a little), which made an earlier range-overlap version
    of this function score parallel columns as "paired" almost as often
    as genuine label:value forms. Start-position proximity is the signal
    that's actually specific to row-based pairing.
    """
    if y0_tolerance is None:
        y0_tolerance = ROW_GROUPING_TOLERANCE
    if not left:
        return 0.0
    paired = sum(
        1 for lb in left
        if any(abs(lb.y0 - rb.y0) <= y0_tolerance for rb in right)
    )
    return paired / len(left)


def _avg_width(cluster: list[TextBlock]) -> float:
    if not cluster:
        return 0.0
    return sum(b.width for b in cluster) / len(cluster)


def _merge_small_clusters(clusters: list[list[TextBlock]], min_blocks: int) -> list[list[TextBlock]]:
    """Folds any cluster with fewer than min_blocks blocks into the
    nearest cluster that is NOT itself undersized (a genuine column),
    repeatedly, until no undersized cluster remains.

    Deliberately prioritizes merging into an already-large cluster over
    merging into another small one -- an earlier version merged into
    whichever cluster was nearest regardless of size, which let several
    small noise clusters (a page header, a page number, a stray wrapped
    line) combine with EACH OTHER and collectively cross the size
    threshold, creating a fake third "column" instead of being absorbed
    into either genuine column. Caught by testing against a real scanned
    page that produced 5 raw clusters (2 real + 3 noise)."""
    clusters = [list(c) for c in clusters]

    def rep_x(cluster: list[TextBlock]) -> float:
        return sum(b.x0 for b in cluster) / len(cluster)

    while len(clusters) > 1:
        small_idx = next((i for i, c in enumerate(clusters) if len(c) < min_blocks), None)
        if small_idx is None:
            break

        small = clusters[small_idx]
        big_candidates = [i for i in range(len(clusters)) if i != small_idx and len(clusters[i]) >= min_blocks]
        candidates = big_candidates or [i for i in range(len(clusters)) if i != small_idx]

        target_idx = min(candidates, key=lambda i: abs(rep_x(clusters[i]) - rep_x(small)))
        clusters[target_idx].extend(small)
        clusters.pop(small_idx)

    # clusters may now be out of x-order after merges -- re-sort left to right
    clusters.sort(key=rep_x)
    return clusters


def classify_layout(docling_result) -> LayoutClassification:
    blocks = extract_text_blocks(docling_result)
    page_width = _get_page_width(docling_result, blocks)

    raw_clusters = cluster_x_positions(blocks, page_width, LAYOUT_GAP_RATIO)
    clusters = _merge_small_clusters(raw_clusters, LAYOUT_MIN_CLUSTER_BLOCKS)
    n_clusters = len(clusters)

    if n_clusters <= 1:
        return LayoutClassification("single_column", 0.95, False, {"n_clusters": n_clusters})

    if n_clusters > 2:
        # More than two x-position clusters even after merging small ones
        # -- a genuinely multi-region page (e.g. sidebar + body +
        # caption). Not something this heuristic set was designed to
        # disambiguate confidently.
        return LayoutClassification(
            "ambiguous", 0.2, True, {"n_clusters": n_clusters, "raw_n_clusters": len(raw_clusters)}
        )

    left, right = clusters[0], clusters[1]
    pairing = _pairing_score(left, right)
    right_width = _avg_width(right)
    width_ratio = _avg_width(left) / right_width if right_width else 1.0

    features = {
        "n_clusters": 2,
        "raw_n_clusters": len(raw_clusters),
        "pairing_score": round(pairing, 3),
        "width_ratio": round(width_ratio, 3),
        "left_block_count": len(left),
        "right_block_count": len(right),
    }

    if pairing >= LAYOUT_PAIRING_HIGH and width_ratio <= LAYOUT_WIDTH_RATIO_NARROW:
        confidence = min(0.95, 0.5 + (pairing - LAYOUT_PAIRING_HIGH) + (LAYOUT_WIDTH_RATIO_NARROW - width_ratio))
        return LayoutClassification(
            "label_value_form", confidence, confidence < LAYOUT_CONFIDENCE_ESCALATION_THRESHOLD, features
        )

    if pairing <= LAYOUT_PAIRING_LOW and width_ratio >= LAYOUT_WIDTH_RATIO_SIMILAR:
        confidence = min(0.95, 0.5 + (LAYOUT_PAIRING_LOW - pairing) + (width_ratio - LAYOUT_WIDTH_RATIO_SIMILAR))
        return LayoutClassification(
            "parallel_columns", confidence, confidence < LAYOUT_CONFIDENCE_ESCALATION_THRESHOLD, features
        )

    return LayoutClassification("ambiguous", 0.3, True, features)


def _debug_print_blocks(docling_result, limit: int = 10) -> None:
    """Run this against one real page before trusting anything above --
    confirms the coordinate-origin assumption actually holds for your
    installed Docling version. y0 should increase going DOWN the page."""
    blocks = extract_text_blocks(docling_result)
    for b in blocks[:limit]:
        print(f"y0={b.y0:.1f} y1={b.y1:.1f} x0={b.x0:.1f} x1={b.x1:.1f}  {b.text[:40]!r}")
