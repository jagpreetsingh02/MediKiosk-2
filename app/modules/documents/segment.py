"""Text-line detection — where the neural backends get their geometry.

⛔ THIS MODULE EXISTS BECAUSE NEITHER NEURAL MODEL PRODUCES A BOUNDING BOX.

GOT-OCR2 is a vision-language model: an image goes in, text comes out. TrOCR (which is what
`khedim/Medical-Prescription-OCR` is) is a line-level *recogniser* with no detection stage at
all — it reads one cropped line and has no concept of where that line sat on the page.

Invariant 2 does not bend for that. `DocumentSpan` requires a page and a bbox, and
`render.py` states the standard the whole evidence drawer rests on: "a box drawn in the wrong
place is worse than no box, because it tells a physician the system read a line it did not
read." A full-page box on every fact would satisfy the type and destroy click-to-source; a box
inferred from a line's ordinal position would be a measurement nobody made.

So the geometry comes from a detector that actually measures it, and the model supplies only
the text. Every box a neural backend emits was measured here, on the same conditioned image
the recogniser saw, at the same scale. That is also why TrOCR is usable at all: it *needs* to
be handed one line at a time, so detection is not an accommodation, it is the contract.

TWO DETECTORS, and which one ran is recorded on every box:

* ``tesseract-layout`` — Tesseract's own page segmentation, geometry columns only. It reads
  multi-column layouts and tables properly, which a projection profile cannot. Preferred
  whenever the binary is installed.
* ``projection`` — a horizontal ink-projection profile in numpy. No binary, no model, no
  network. Correct for the single-column pages prescriptions and lab reports almost always
  are, and honest about nothing else: `segment_lines` returns what it found, and a caller that
  gets one enormous band knows the page was not single-column.

⚠️ THE TSV PARSING BELOW IS DELIBERATELY NOT `TesseractOCR._parse_tsv`. That function is
covered by `test_bbox_geometry.py` and carries the page-size fix that took two wrong-box
incidents to get right; importing it here would mean `segment` -> `backends` -> `neural` ->
`segment`. This reads the geometry columns and nothing else — no text, no confidence, no
grouping heuristics — which is a genuinely smaller job than the one that function does.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from app.contracts.provenance import BoundingBox
from app.core.logging import get_logger
from app.modules.documents import imaging

log = get_logger(__name__)

#: A band thinner than this is noise — a speck, a rule, the edge of a staple.
MIN_LINE_HEIGHT_PX = 8

#: A band taller than this fraction of the page is not a line. A photograph, a logo or a
#: solid table block gets dropped rather than handed to a recogniser as if it were text.
MAX_LINE_HEIGHT_FRACTION = 0.28

#: Blank rows shorter than this between two ink bands do not split them. Descenders (g, y, p)
#: and the gap between a two-line address block are both smaller than a real line gap.
MERGE_GAP_PX = 6

#: Absolute floor: a row holding fewer inked pixels than this fraction of the page width is
#: not a line whatever else is true. Low, because a line holding one short word is still a line.
MIN_INK_FRACTION = 0.004

#: ⛔ THE CUTOFF IS RELATIVE TO THE PAGE, AND A FIXED ONE DID NOT WORK.
#:
#: A row counts as text when its ink reaches this fraction of the page's 95th-percentile row.
#: Measured: on a clean scan a text row carries ~97 inked pixels and a blank row ~0, so any
#: cutoff separates them. On a degraded scan or a handheld photo the adaptive threshold leaves
#: salt-and-pepper speckle across the whole sheet, and against a FIXED fraction every row
#: qualified — 2468 rows out of 2470 — collapsing the page into one band that
#: `MAX_LINE_HEIGHT_FRACTION` then rejected. Four fixtures detected ZERO lines that way.
#:
#: Scaling to the page's own profile fixes all of them, because a text row still carries
#: several times the ink of a noise row even when the noise floor is high.
#:
#: 0.25 rather than a tighter value on purpose. A spurious band costs one wasted model call;
#: a missed band loses a medication off a prescription. The bias belongs on recall, the same
#: way it does in `redflags.yaml`.
NOISE_FLOOR_FRACTION = 0.25

#: Kernel for the speckle filter applied before profiling. 3x3 removes isolated threshold
#: noise without touching a stroke: the thinnest glyph stem on a 1600px-tall page is several
#: pixels across, and a median filter only erases a feature smaller than half its window.
DENOISE_KERNEL = 3

#: Padding around a measured band, as a fraction of its height. A recogniser fed a crop cut
#: exactly at the glyph boundary loses the ascender, and TrOCR in particular reads a tight
#: crop noticeably worse than a loose one.
PAD_FRACTION = 0.18


@dataclass(frozen=True, slots=True)
class LineBox:
    """One detected text line, in PIXELS on the image it was measured from.

    Pixels rather than normalised coordinates because the next thing that happens to a
    LineBox is `image.crop()`. Normalisation is a presentation concern and happens once, at
    the edge, in `normalised()`.
    """

    left: int
    top: int
    width: int
    height: int
    #: Which detector measured this. Carried onto the block so a physician looking at an odd
    #: box can tell a layout-analysed page from a projection-profiled one.
    detector: str

    @property
    def box(self) -> tuple[int, int, int, int]:
        """PIL's crop rectangle: (left, upper, right, lower)."""
        return (self.left, self.top, self.left + self.width, self.top + self.height)

    def normalised(self, page_width: int, page_height: int) -> BoundingBox:
        """To page-relative coordinates, clamped into [0, 1].

        The clamp is not defensive noise: `PAD_FRACTION` deliberately pushes a box past the
        glyph extent, and on a line at the very top or bottom of the page that lands outside
        it. Clamping is correct — the ink really is at the edge — whereas letting a negative
        reach `BoundingBox` would be a validation error on a perfectly good read.
        """
        page_width = max(page_width, 1)
        page_height = max(page_height, 1)
        x = min(max(self.left / page_width, 0.0), 1.0)
        y = min(max(self.top / page_height, 0.0), 1.0)
        return BoundingBox(
            x=round(x, 4),
            y=round(y, 4),
            width=round(min(max(self.width / page_width, 1e-4), 1.0 - x if x < 1.0 else 1e-4), 4),
            height=round(
                min(max(self.height / page_height, 1e-4), 1.0 - y if y < 1.0 else 1e-4), 4
            ),
        )


def segment_lines(image: Image.Image, *, prefer: str | None = None) -> list[LineBox]:
    """Detect text lines, top to bottom. Never returns a box it did not measure.

    `prefer` pins a detector by name, for the benchmark and for reproducing a result. Left
    unset it uses Tesseract's layout analysis when the binary is there and the projection
    profile when it is not.

    An empty list is a real answer — a blank page, or a photograph of a wall — and callers
    must handle it rather than assuming at least one line.
    """
    chosen = prefer or ("tesseract-layout" if shutil.which("tesseract") else "projection")

    if chosen == "tesseract-layout":
        boxes = _tesseract_layout(image)
        if boxes:
            return boxes
        # Tesseract found no layout at all. That happens on a page it cannot segment, and the
        # projection profile sometimes still can — so this falls through rather than
        # reporting a blank page, which would lose the document.
        log.info("segment.layout_empty", falling_back_to="projection")
        return _projection_profile(image)

    return _projection_profile(image)


# ---------------------------------------------------------------- tesseract layout


def _tesseract_layout(image: Image.Image) -> list[LineBox]:
    """Line-level geometry from Tesseract's TSV. Geometry columns ONLY — see the header."""
    binary = shutil.which("tesseract")
    if binary is None:
        return []

    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "page.png"
        image.save(source, format="PNG")
        try:
            completed = subprocess.run(
                [binary, str(source), "stdout", "tsv"],
                check=True,
                capture_output=True,
                timeout=120,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
            # Detection failing is not fatal: the caller falls back to the projection
            # profile. Losing the document because a layout pass failed would be.
            log.warning("segment.tesseract_failed", error=str(exc)[:160])
            return []

    #: TSV level 4 is a line; 5 is a word. Grouping words ourselves would duplicate the
    #: heuristics in `TesseractOCR._parse_tsv`, so we take the line rows Tesseract already
    #: grouped and ignore everything else.
    line_level = 4
    boxes: list[LineBox] = []
    for row_text in completed.stdout.decode("utf-8", "replace").splitlines()[1:]:
        row = row_text.split("\t")
        if len(row) < 12:
            continue
        try:
            level = int(row[0])
            left, top, width, height = (int(row[6]), int(row[7]), int(row[8]), int(row[9]))
        except ValueError:
            continue
        if level != line_level or width <= 0 or height < MIN_LINE_HEIGHT_PX:
            continue
        boxes.append(
            _padded(left, top, width, height, image.width, image.height, "tesseract-layout")
        )

    boxes.sort(key=lambda b: (b.top, b.left))
    return boxes


# ---------------------------------------------------------------- projection profile


def _projection_profile(image: Image.Image) -> list[LineBox]:
    """Horizontal ink projection. No binary, no model, no network.

    ⛔ IT BINARISES FIRST, AND THE FIRST VERSION OF THIS FUNCTION DID NOT.

    Detection runs on `imaging.binarise()` — a local-mean threshold — while the recogniser
    still reads the natural greyscale page. Both are correct for their own consumer, and the
    geometry stays valid across the two because thresholding does not resize.

    Against a global cutoff on the unthresholded image this found ZERO lines on
    `prescription_photo_handheld.jpg` (Tesseract found 13 on the same page). The reason is
    the one `_adaptive_threshold` was written for: under corridor lighting the shadowed half
    of a sheet is darker than the ink on the bright half, so no single intensity separates
    ink from paper. A detector that returns nothing on a photograph would have quietly
    excluded exactly the uploads this kiosk exists to read.
    """
    import numpy as np  # noqa: PLC0415
    from PIL import ImageFilter  # noqa: PLC0415

    # Binarise for the detector, then kill the speckle that binarising a degraded page
    # produces. Both steps are why this works on a photograph; see the constants above.
    binary = imaging.binarise(image.convert("L")).filter(
        ImageFilter.MedianFilter(DENOISE_KERNEL)
    )
    pixels = np.asarray(binary, dtype=np.float32)
    if pixels.size == 0:
        return []

    height, width = pixels.shape
    # The image is two-tone by now, so the midpoint is the only cutoff that means anything.
    ink = pixels < 128.0

    row_ink = ink.sum(axis=1)
    peak = float(np.percentile(row_ink, 95))
    cutoff = max(MIN_INK_FRACTION * width, NOISE_FLOOR_FRACTION * peak, 1.0)
    inked_rows = row_ink >= cutoff

    bands = _merge_runs(_runs(inked_rows), gap=MERGE_GAP_PX)

    boxes: list[LineBox] = []
    max_height = MAX_LINE_HEIGHT_FRACTION * height
    for top, bottom in bands:
        band_height = bottom - top
        if band_height < MIN_LINE_HEIGHT_PX or band_height > max_height:
            continue
        columns = ink[top:bottom].sum(axis=0)
        inked_columns = np.flatnonzero(columns > 0)
        if inked_columns.size == 0:
            continue
        left = int(inked_columns[0])
        right = int(inked_columns[-1]) + 1
        boxes.append(
            _padded(left, top, right - left, band_height, width, height, "projection")
        )

    return boxes


def _runs(flags) -> list[tuple[int, int]]:  # type: ignore[no-untyped-def]
    """Consecutive True runs as half-open [start, end) row ranges."""
    import numpy as np  # noqa: PLC0415

    padded = np.concatenate(([False], np.asarray(flags, dtype=bool), [False]))
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    return [(int(edges[i]), int(edges[i + 1])) for i in range(0, len(edges) - 1, 2)]


def _merge_runs(runs: list[tuple[int, int]], *, gap: int) -> list[tuple[int, int]]:
    """Join runs separated by less than `gap` blank rows.

    Without this a descender that briefly clears the baseline splits one line into two, and
    the recogniser is then handed the top half of a word.
    """
    if not runs:
        return []
    merged = [runs[0]]
    for start, end in runs[1:]:
        previous_start, previous_end = merged[-1]
        if start - previous_end <= gap:
            merged[-1] = (previous_start, end)
        else:
            merged.append((start, end))
    return merged


def _padded(
    left: int, top: int, width: int, height: int, page_w: int, page_h: int, detector: str
) -> LineBox:
    """Grow a measured band by `PAD_FRACTION`, clamped to the page."""
    pad = max(int(height * PAD_FRACTION), 2)
    new_left = max(left - pad, 0)
    new_top = max(top - pad, 0)
    return LineBox(
        left=new_left,
        top=new_top,
        width=min(width + 2 * pad, page_w - new_left),
        height=min(height + 2 * pad, page_h - new_top),
        detector=detector,
    )


__all__ = ["LineBox", "segment_lines"]
