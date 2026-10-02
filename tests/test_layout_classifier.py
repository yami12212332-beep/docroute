"""
Tests call the REAL classify_layout() via a minimal fake stand-in for a
Docling ConversionResult -- not a duplicated reimplementation of its
logic. An earlier version of this file re-implemented classify_layout's
body directly in the test helper, which meant it silently stopped
testing the actual function the moment that function changed (the
tiny-cluster merge and header/footer filter added later were never
exercised by the old tests). Don't repeat that mistake -- if you need a
new fixture shape, extend the fake classes below, don't copy logic out
of layout_classifier.py.
"""

from docroute.pipeline.layout_classifier import TextBlock, _pairing_score, classify_layout


class _FakeBBox:
    def __init__(self, l, t, r, b):
        self.l, self.t, self.r, self.b = l, t, r, b
        self.coord_origin = None  # already top-left; no normalization needed for tests


class _FakeProv:
    def __init__(self, bbox):
        self.bbox = bbox


class _FakeElement:
    def __init__(self, block: TextBlock, label=None):
        self.prov = [_FakeProv(_FakeBBox(block.x0, block.y0, block.x1, block.y1))]
        self.text = block.text
        self.label = label


class _FakeDocument:
    def __init__(self, blocks, labels=None):
        labels = labels or [None] * len(blocks)
        self._elements = [_FakeElement(b, lbl) for b, lbl in zip(blocks, labels)]
        # Deliberately no .pages attribute -- exercises the bbox-extent
        # fallback path in _get_page_width/_get_page_height, same as it
        # would on a real Docling version where page size isn't reachable.

    def iterate_items(self):
        for e in self._elements:
            yield e, 0


class _FakeResult:
    def __init__(self, blocks, labels=None):
        self.document = _FakeDocument(blocks, labels)


def _classify(blocks, labels=None):
    return classify_layout(_FakeResult(blocks, labels))


def test_single_column():
    blocks = [
        TextBlock(x0=60, y0=0, x1=400, y1=20, text="Line one"),
        TextBlock(x0=60, y0=25, x1=410, y1=45, text="Line two"),
        TextBlock(x0=60, y0=50, x1=390, y1=70, text="Line three"),
    ]
    result = _classify(blocks)
    assert result.layout_type == "single_column"
    assert not result.needs_escalation


def test_label_value_form():
    # narrow left labels, each paired row-by-row with a wider right value
    # at nearly the same y -- mirrors the "TREATMENT GIVEN : ..." pattern
    blocks = [
        TextBlock(x0=60, y0=100, x1=140, y1=115, text="TREATMENT GIVEN"),
        TextBlock(x0=230, y0=100, x1=550, y1=115, text="IV Fluids, Inj. Taximax ..."),
        TextBlock(x0=60, y0=200, x1=150, y1=215, text="PRE-OP MEDICATIONS"),
        TextBlock(x0=230, y0=200, x1=560, y1=215, text="Inj. T.T 1amp im stat ..."),
        TextBlock(x0=60, y0=300, x1=145, y1=315, text="CONSULTANT/SURGEON"),
        TextBlock(x0=230, y0=300, x1=540, y1=315, text="Dr. Vamsi Krishna ..."),
    ]
    result = _classify(blocks)
    assert result.layout_type == "label_value_form"
    assert not result.needs_escalation


def test_parallel_columns():
    # Two independently-flowing columns of similar width. Docling extracts
    # text at paragraph granularity (not per-line), so each block here is
    # a whole paragraph. Paragraph breaks deliberately don't coincide
    # between columns -- that's the realistic case: two columns of running
    # text have no reason to start new paragraphs on the same line.
    blocks = [
        TextBlock(x0=40, y0=0, x1=280, y1=45, text="Left col para 1"),
        TextBlock(x0=40, y0=60, x1=280, y1=105, text="Left col para 2"),
        TextBlock(x0=40, y0=140, x1=280, y1=185, text="Left col para 3"),
        TextBlock(x0=40, y0=220, x1=280, y1=265, text="Left col para 4"),
        TextBlock(x0=320, y0=25, x1=560, y1=70, text="Right col para 1"),
        TextBlock(x0=320, y0=85, x1=560, y1=130, text="Right col para 2"),
        TextBlock(x0=320, y0=165, x1=560, y1=210, text="Right col para 3"),
        TextBlock(x0=320, y0=245, x1=560, y1=290, text="Right col para 4"),
    ]
    result = _classify(blocks)
    assert result.layout_type == "parallel_columns"
    assert not result.needs_escalation


def test_genuinely_ambiguous_multi_region_page():
    # Three REAL clusters (2+ blocks each, so the noise-merge step won't
    # fold them together) -- a genuine three-region page this heuristic
    # set isn't designed to handle, distinct from the noise case below.
    blocks = [
        TextBlock(x0=20, y0=0, x1=100, y1=20, text="sidebar line 1"),
        TextBlock(x0=20, y0=30, x1=100, y1=50, text="sidebar line 2"),
        TextBlock(x0=200, y0=0, x1=350, y1=20, text="body line 1"),
        TextBlock(x0=200, y0=30, x1=350, y1=50, text="body line 2"),
        TextBlock(x0=450, y0=0, x1=580, y1=20, text="margin note 1"),
        TextBlock(x0=450, y0=30, x1=580, y1=50, text="margin note 2"),
    ]
    result = _classify(blocks)
    assert result.layout_type == "ambiguous"
    assert result.needs_escalation


def test_noise_clusters_get_merged_not_treated_as_real_columns():
    # This is the exact failure mode found testing against a real scanned
    # hospital page: 2 real columns (label:value) plus 3 stray singleton
    # blocks (a page header, a page number, a paragraph continuation line
    # with slightly different indent) produced 5 raw clusters. The merge
    # step should fold the singletons back in, leaving the real 2-column
    # label:value decision intact.
    blocks = [
        TextBlock(x0=60, y0=100, x1=140, y1=115, text="TREATMENT GIVEN"),
        TextBlock(x0=230, y0=100, x1=550, y1=115, text="IV Fluids, Inj. Taximax ..."),
        TextBlock(x0=60, y0=200, x1=150, y1=215, text="PRE-OP MEDICATIONS"),
        TextBlock(x0=230, y0=200, x1=560, y1=215, text="Inj. T.T 1amp im stat ..."),
        # noise: a lone page-header-ish block sitting at its own x position
        TextBlock(x0=352, y0=5, x1=430, y1=18, text="Continuation Sheet"),
        # noise: a lone page-number-ish block at yet another x position
        TextBlock(x0=438, y0=8, x1=470, y1=20, text="Page 2 of 3"),
        # noise: a wrapped continuation line with slightly different indent
        TextBlock(x0=310, y0=160, x1=480, y1=175, text="~ suggesting sealed off perforation"),
    ]
    result = _classify(blocks)
    assert result.features["raw_n_clusters"] > 2  # confirms the noise was really there
    assert result.layout_type == "label_value_form"  # but the merge recovered the right answer
    assert not result.needs_escalation


def test_excluded_labels_are_filtered_before_clustering():
    # Same idea as above, but this time the noise carries a Docling label
    # that marks it as a page header/footer -- it should be dropped
    # entirely before clustering even runs, not merged after the fact.
    blocks = [
        TextBlock(x0=60, y0=100, x1=140, y1=115, text="TREATMENT GIVEN"),
        TextBlock(x0=230, y0=100, x1=550, y1=115, text="IV Fluids, Inj. Taximax ..."),
        TextBlock(x0=60, y0=200, x1=150, y1=215, text="PRE-OP MEDICATIONS"),
        TextBlock(x0=230, y0=200, x1=560, y1=215, text="Inj. T.T 1amp im stat ..."),
        TextBlock(x0=900, y0=5, x1=980, y1=18, text="Continuation Sheet"),  # far outlier x position
    ]
    labels = [None, None, None, None, "PAGE_HEADER"]
    result = _classify(blocks, labels)
    assert result.features["n_clusters"] == 2  # the header never entered clustering at all
    assert result.layout_type == "label_value_form"


def test_pairing_score_direct():
    left = [TextBlock(x0=0, y0=0, x1=50, y1=20, text="A")]
    # starts within tolerance (default ROW_GROUPING_TOLERANCE=10) -> paired
    right_same_line_start = [TextBlock(x0=100, y0=5, x1=200, y1=25, text="a value")]
    # range overlaps y=0-20 but STARTS far away -> NOT paired (this is the
    # distinction that fixed the earlier false-positive on parallel columns)
    right_overlapping_but_offset_start = [TextBlock(x0=100, y0=45, x1=200, y1=400, text="unrelated tall block")]
    right_unpaired = [TextBlock(x0=100, y0=500, x1=200, y1=520, text="unrelated")]
    assert _pairing_score(left, right_same_line_start) == 1.0
    assert _pairing_score(left, right_overlapping_but_offset_start) == 0.0
    assert _pairing_score(left, right_unpaired) == 0.0
