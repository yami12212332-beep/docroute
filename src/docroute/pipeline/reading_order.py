"""
Reconstructs correct reading order for label:value form layouts
("TREATMENT GIVEN : ..." style), which the layout classifier flags as
cases where Docling's default column-based reading order is wrong.

Only used when layout_classifier.classify_layout() returns
layout_type == "label_value_form". Not a general-purpose replacement for
Docling's reading order elsewhere -- single_column and parallel_columns
pages keep Docling's own (correct, for those layouts) ordering.
"""

from docroute.config import ROW_GROUPING_TOLERANCE
from docroute.pipeline.layout_classifier import TextBlock


def build_row_ordered_text(blocks: list[TextBlock], row_tolerance: float = None) -> str:
    """Groups blocks into rows by y-position (within row_tolerance),
    sorts left-to-right within each row, and returns the reconstructed
    text: label immediately followed by its own value, row by row."""
    if row_tolerance is None:
        row_tolerance = ROW_GROUPING_TOLERANCE

    if not blocks:
        return ""

    ordered = sorted(blocks, key=lambda b: b.y0)
    rows: list[list[TextBlock]] = []
    for b in ordered:
        placed = False
        for row in rows:
            if abs(row[0].y0 - b.y0) <= row_tolerance:
                row.append(b)
                placed = True
                break
        if not placed:
            rows.append([b])

    lines = []
    for row in rows:
        row.sort(key=lambda b: b.x0)
        lines.append(" ".join(b.text for b in row if b.text))
    return "\n".join(lines)
