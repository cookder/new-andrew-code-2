"""Panel geometry, checked against measurements taken from real session frames.

The numbers here come from two frames of an actual bay: in a 1063x1890 frame the
projected screen spans roughly x 357-840, y 888-1247, and the stat panel occupies
x 358-452, y 893-1240 -- so the panel is about 94x347 px, h/w ~ 3.7, sitting in
the leftmost ~20% of the screen at near-full height.

These are pure geometry and need no OpenCV, so they run in the normal suite.
"""

from __future__ import annotations

import pytest

from backend.app.constants import (
    CANONICAL_PANEL_SIZE,
    PANEL_ASPECT_RANGE,
    PANEL_BOUNDS_IN_SCREEN,
    PANEL_GRID,
)
from backend.app.pipeline.calibration import cell_boxes, header_height, panel_from_screen

# Measured off the supplied frames.
MEASURED_PANEL = (94, 347)  # (w, h) px
MEASURED_SCREEN_QUAD = [(357.0, 888.0), (840.0, 888.0), (840.0, 1247.0), (357.0, 1247.0)]
MEASURED_PANEL_BOX = (358.0, 893.0, 452.0, 1240.0)  # left, top, right, bottom


class TestAspectRange:
    def test_measured_panel_is_accepted(self):
        # The original window was 1.29-3.43, which rejected the real panel at
        # 3.69 -- the detector would have discarded the correct candidate.
        ratio = MEASURED_PANEL[1] / MEASURED_PANEL[0]
        lo, hi = PANEL_ASPECT_RANGE
        assert lo <= ratio <= hi

    def test_canonical_canvas_matches_the_real_aspect(self):
        # A mismatched canvas stretches digits before OCR. Keep the canonical
        # rectangle within 30% of the measured panel's proportions.
        measured = MEASURED_PANEL[1] / MEASURED_PANEL[0]
        canonical = CANONICAL_PANEL_SIZE[1] / CANONICAL_PANEL_SIZE[0]
        assert canonical == pytest.approx(measured, rel=0.3)

    def test_range_excludes_a_square_region(self):
        lo, _ = PANEL_ASPECT_RANGE
        assert lo > 1.5


class TestPanelFromScreen:
    def test_derived_panel_matches_the_measured_box(self):
        derived = panel_from_screen(MEASURED_SCREEN_QUAD)
        left, top, right, bottom = MEASURED_PANEL_BOX
        (tl, tr, br, bl) = derived
        # Within ~12 px of hand-measured edges on a 1890px-tall frame.
        assert tl[0] == pytest.approx(left, abs=12)
        assert tr[0] == pytest.approx(right, abs=12)
        assert tl[1] == pytest.approx(top, abs=12)
        assert bl[1] == pytest.approx(bottom, abs=12)

    def test_derived_panel_has_a_plausible_aspect(self):
        (tl, tr, br, bl) = panel_from_screen(MEASURED_SCREEN_QUAD)
        ratio = (bl[1] - tl[1]) / (tr[0] - tl[0])
        lo, hi = PANEL_ASPECT_RANGE
        assert lo <= ratio <= hi

    def test_keystoned_screen_yields_a_keystoned_panel(self):
        # An off-axis camera makes the screen's left edge taller than its right.
        # The panel must inherit that perspective rather than snapping to a
        # rectangle, or the warp will not undo the distortion.
        skewed = [(300.0, 900.0), (860.0, 940.0), (860.0, 1240.0), (300.0, 1300.0)]
        (tl, tr, br, bl) = panel_from_screen(skewed)
        assert bl[1] - tl[1] > br[1] - tr[1]

    def test_panel_stays_inside_the_screen(self):
        for point in panel_from_screen(MEASURED_SCREEN_QUAD):
            assert 357.0 - 1 <= point[0] <= 840.0 + 1
            assert 888.0 - 1 <= point[1] <= 1247.0 + 1

    def test_bounds_take_the_left_fifth(self):
        left, _, right, _ = PANEL_BOUNDS_IN_SCREEN
        assert right - left == pytest.approx(0.21, abs=0.05)


class TestCellBoxes:
    def test_twelve_cells(self):
        assert len(cell_boxes()) == 12

    def test_grid_starts_below_the_header(self):
        # The header carries the shot counter and clock and is not part of the
        # 2x6 value grid.
        boxes = cell_boxes()
        assert boxes["carry"][1] >= header_height()

    def test_cells_do_not_overlap_within_a_row(self):
        boxes = cell_boxes()
        for left_key, right_key in PANEL_GRID:
            lx, _, lw, _ = boxes[left_key]
            rx, _, _, _ = boxes[right_key]
            assert lx + lw <= rx

    def test_rows_are_stacked_in_order(self):
        boxes = cell_boxes()
        tops = [boxes[row[0]][1] for row in PANEL_GRID]
        assert tops == sorted(tops)
        assert len(set(tops)) == len(tops)

    def test_grid_fits_inside_the_canvas(self):
        width, height = CANONICAL_PANEL_SIZE
        for x, y, w, h in cell_boxes().values():
            assert 0 <= x and x + w <= width
            assert 0 <= y and y + h <= height

    def test_cells_are_large_enough_to_ocr(self):
        # Small source digits get upscaled by the warp; if a cell came out tiny
        # the canonical canvas would be the reason.
        for _, _, w, h in cell_boxes().values():
            assert w >= 120 and h >= 120

    def test_face_to_path_is_on_the_grid_but_never_stored(self):
        # Row 6 left is still read, as a cross-check against the derived value.
        assert "face_to_path" in cell_boxes()
