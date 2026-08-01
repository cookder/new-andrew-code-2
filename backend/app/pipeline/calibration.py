"""Stage 2 -- calibration.

Spec section 4: "Camera position varies per session, so crop coordinates cannot
be hardcoded." The camera is handheld or propped today and a tripod is planned,
so the panel lands somewhere different in every session and at a different
keystone angle.

Approach: find the "FULL SWING" banner and the stat panel border as anchors,
compute a perspective transform onto a canonical panel rectangle, persist the
transform on the session row, and apply the fixed 2x6 grid to the canonicalized
panel.

    "If anchor detection fails, halt the session and require manual corner
    selection in the UI. Do not guess."

That instruction is why :func:`calibrate` raises rather than falling back to a
default crop. A wrong-but-plausible transform would produce wrong-but-plausible
numbers, which is far worse than a session that stops and asks.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ..constants import CANONICAL_PANEL_SIZE, FADED_ROWS, PANEL_GRID
from .. import store


class CalibrationError(RuntimeError):
    """Anchor detection failed. The session halts and the UI asks for corners."""


Corners = list[tuple[float, float]]


def _order_corners(points) -> Corners:
    """Return corners as [top-left, top-right, bottom-right, bottom-left]."""
    import numpy as np

    pts = np.array(points, dtype="float32").reshape(-1, 2)
    summed = pts.sum(axis=1)
    diffed = np.diff(pts, axis=1).ravel()
    return [
        tuple(pts[int(np.argmin(summed))]),  # top-left: smallest x+y
        tuple(pts[int(np.argmin(diffed))]),  # top-right: smallest y-x
        tuple(pts[int(np.argmax(summed))]),  # bottom-right
        tuple(pts[int(np.argmax(diffed))]),  # bottom-left
    ]


def find_panel_corners(frame) -> Corners:
    """Locate the stat panel's four corners in a full-resolution frame.

    The panel is a bright bordered rectangle on the left edge of the simulator
    screen with a distinctly tall aspect ratio (2 columns x 6 rows). We look for
    the largest quadrilateral contour in the left half of the frame whose aspect
    ratio matches.
    """
    import cv2
    import numpy as np

    height, width = frame.shape[:2]
    left = frame[:, : int(width * 0.6)]

    gray = cv2.cvtColor(left, cv2.COLOR_BGR2GRAY)
    gray = cv2.bilateralFilter(gray, 9, 60, 60)
    edges = cv2.Canny(gray, 40, 140)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)

    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    target_ratio = CANONICAL_PANEL_SIZE[1] / CANONICAL_PANEL_SIZE[0]
    min_area = height * width * 0.01

    best = None
    best_area = 0.0
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:30]:
        area = cv2.contourArea(contour)
        if area < min_area:
            break
        approx = cv2.approxPolyDP(contour, 0.02 * cv2.arcLength(contour, True), True)
        if len(approx) != 4:
            continue
        corners = _order_corners(approx)
        (tlx, tly), (trx, _), _, (blx, bly) = corners
        w = max(abs(trx - tlx), 1.0)
        h = max(abs(bly - tly), 1.0)
        ratio = h / w
        if not (0.6 * target_ratio <= ratio <= 1.6 * target_ratio):
            continue
        if area > best_area:
            best, best_area = corners, area

    if best is None:
        raise CalibrationError(
            "could not locate the stat panel border; manual corner selection required"
        )
    return best


def find_banner(frame) -> tuple[int, int, int, int] | None:
    """Locate the 'FULL SWING' banner, used as a sanity anchor.

    The banner confirms we are looking at the simulator screen and not, say, a
    bright window behind it. It is advisory: its absence downgrades confidence
    but the panel border is the load-bearing anchor.
    """
    import cv2
    import numpy as np

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    _, mask = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)
    top = mask[: int(mask.shape[0] * 0.35), :]
    contours, _ = cv2.findContours(top, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:10]:
        x, y, w, h = cv2.boundingRect(contour)
        if w > frame.shape[1] * 0.15 and 2.5 < w / max(h, 1) < 12:
            return (x, y, w, h)
    return None


def transform_from_corners(corners: Corners) -> list[list[float]]:
    """Perspective matrix mapping the detected corners onto the canonical panel."""
    import cv2
    import numpy as np

    w, h = CANONICAL_PANEL_SIZE
    src = np.array(corners, dtype="float32")
    dst = np.array([[0, 0], [w, 0], [w, h], [0, h]], dtype="float32")
    return cv2.getPerspectiveTransform(src, dst).tolist()


def grab_frame(video_path: Path | str, ts: float):
    """Full-resolution frame at ``ts`` seconds."""
    import cv2

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise CalibrationError(f"could not open video: {video_path}")
    try:
        cap.set(cv2.CAP_PROP_POS_MSEC, ts * 1000.0)
        ok, frame = cap.read()
        if not ok:
            raise CalibrationError(f"could not read a frame at {ts}s")
        return frame
    finally:
        cap.release()


def calibrate(
    conn: sqlite3.Connection, session_id: int, *, sample_times: tuple[float, ...] = (30, 90, 180)
) -> dict:
    """Detect anchors, persist the transform, or halt the session.

    Several frames are tried because a swing, a walk-past, or a glare frame can
    hide the panel. If none of them yield anchors the session status is set to
    ``needs_calibration`` and the caller is expected to route the user to manual
    corner selection.
    """
    session = store.get_session(conn, session_id)
    if session is None:
        raise CalibrationError(f"no session {session_id}")
    video = session.get("source_video_path")
    if not video:
        raise CalibrationError("session has no source video on disk")

    duration = session.get("duration_s") or 0.0
    times = [t for t in sample_times if t < duration] or [max(duration / 2, 0.0)]

    errors: list[str] = []
    for ts in times:
        try:
            frame = grab_frame(video, ts)
            corners = find_panel_corners(frame)
        except CalibrationError as exc:
            errors.append(f"{ts}s: {exc}")
            continue

        payload = {
            "matrix": transform_from_corners(corners),
            "corners": [list(map(float, c)) for c in corners],
            "banner": find_banner(frame),
            "calibrated_at_ts": ts,
            "source": "auto",
        }
        store.update_session(
            conn, session_id, calibration_transform=payload, status="processing"
        )
        return payload

    store.update_session(
        conn,
        session_id,
        status="needs_calibration",
        notes="Automatic calibration failed: " + "; ".join(errors),
    )
    raise CalibrationError(
        "anchor detection failed on every sampled frame; manual corner selection required"
    )


def set_manual_corners(
    conn: sqlite3.Connection, session_id: int, corners: Corners
) -> dict:
    """Accept corners picked by hand in the review UI."""
    ordered = _order_corners(corners)
    payload = {
        "matrix": transform_from_corners(ordered),
        "corners": [list(map(float, c)) for c in ordered],
        "banner": None,
        "source": "manual",
    }
    store.update_session(
        conn, session_id, calibration_transform=payload, status="processing"
    )
    return payload


def cell_boxes() -> dict[str, tuple[int, int, int, int]]:
    """Pixel boxes for the 12 grid cells on the canonicalized panel.

    Read by grid position, never by caption -- section 2 is explicit that the
    captions are too small to OCR reliably, so position alone determines which
    metric a number is.
    """
    width, height = CANONICAL_PANEL_SIZE
    rows = len(PANEL_GRID)
    cell_w, cell_h = width // 2, height // rows
    boxes: dict[str, tuple[int, int, int, int]] = {}
    for r, row in enumerate(PANEL_GRID):
        for c, key in enumerate(row):
            boxes[key] = (c * cell_w, r * cell_h, cell_w, cell_h)
    return boxes


def enhance_faded_row(cell):
    """CLAHE contrast stretch for the bottom row.

    Spec section 2: row 6 renders with a fade gradient and is frequently
    illegible, but "the fade is a rendering gradient, not missing pixels" -- so
    a local contrast stretch recovers it rather than inventing detail.
    """
    import cv2

    gray = cv2.cvtColor(cell, cv2.COLOR_BGR2GRAY) if cell.ndim == 3 else cell
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    return clahe.apply(gray)


def canonicalize(frame, transform: list[list[float]]):
    """Warp a full frame onto the canonical panel rectangle."""
    import cv2
    import numpy as np

    return cv2.warpPerspective(
        frame, np.array(transform, dtype="float32"), CANONICAL_PANEL_SIZE
    )


def prepare_panel(frame, transform: list[list[float]]):
    """Canonicalize and apply CLAHE to the faded row, in place.

    Returns the panel image handed to the vision model.
    """
    import cv2

    panel = canonicalize(frame, transform)
    boxes = cell_boxes()
    for r, row in enumerate(PANEL_GRID):
        if r not in FADED_ROWS:
            continue
        for key in row:
            x, y, w, h = boxes[key]
            enhanced = enhance_faded_row(panel[y : y + h, x : x + w])
            panel[y : y + h, x : x + w] = cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)
    return panel
