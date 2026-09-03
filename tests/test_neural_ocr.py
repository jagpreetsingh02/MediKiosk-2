"""The neural OCR backends, and the guarantees that hold whether or not torch is installed.

Nothing here downloads a model or requires `transformers`. That is the point: the failure
these tests actually protect against is a machine WITHOUT the ML stack — which is the default
clone, the CI runner and the deploy image — silently losing document OCR, or worse, gaining a
backend that reports itself available and then raises on the first patient upload.

The parts that need real weights (does GOT-OCR2 read a prescription better than Tesseract?)
belong in the OCR benchmark, not here. See the module docstring in `neural.py` for what is
and is not verified, and ADR-0015 for why the geometry comes from a detector.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from app.contracts.provenance import BoundingBox
from app.core.config import settings
from app.core.errors import UpstreamUnavailable
from app.modules.documents import backends, imaging, neural, segment
from app.modules.documents.backends import (
    OCRBlock,
    OCRPage,
    OCRResult,
    available_backends,
    backend_for,
    get_ocr_backend,
)

FIXTURES = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "documents"

#: Every page fixture the segmenter must find lines on. The degraded and handheld ones are
#: here BY NAME because all four detected zero lines in the first implementation.
PAGE_FIXTURES = [
    "prescription_scan.png",
    "lab_report_scan.png",
    "discharge_scan.png",
    "prescription_photo_handheld.jpg",
    "prescription_degraded.png",
    "lab_report_degraded.png",
    "discharge_degraded.png",
]


def _page(name: str) -> Image.Image:
    return imaging.prepare(
        (FIXTURES / name).read_bytes(), filename=name, threshold=False
    ).image


# ---------------------------------------------------------------- segmentation


@pytest.mark.parametrize("name", PAGE_FIXTURES)
def test_every_page_fixture_segments_into_lines(name: str) -> None:
    """⛔ THE REGRESSION, NAMED. Four of these detected ZERO lines.

    The first projection profile ran on the unthresholded page with a fixed ink fraction.
    On a handheld photo the lighting gradient meant no global cutoff separated ink from
    paper; on a degraded scan the binariser's speckle meant EVERY row cleared the fraction,
    so the page collapsed into one band that the height guard then threw away.

    A detector that silently returns nothing is the worst possible failure here: the neural
    backends would have produced a document with no blocks, which reads downstream as a page
    with nothing on it rather than as an error.
    """
    if not (FIXTURES / name).exists():
        pytest.skip(f"{name} is not in this checkout")

    boxes = segment.segment_lines(_page(name), prefer="projection")
    assert boxes, f"{name} segmented into no lines at all"
    assert len(boxes) >= 5, f"{name} segmented into only {len(boxes)} lines"


@pytest.mark.parametrize("name", PAGE_FIXTURES)
def test_every_measured_box_is_inside_the_page(name: str) -> None:
    """A box outside the page is a box drawn somewhere the text is not."""
    if not (FIXTURES / name).exists():
        pytest.skip(f"{name} is not in this checkout")

    image = _page(name)
    for box in segment.segment_lines(image, prefer="projection"):
        assert box.left >= 0 and box.top >= 0
        assert box.width > 0 and box.height > 0
        assert box.left + box.width <= image.width
        assert box.top + box.height <= image.height

        normalised = box.normalised(image.width, image.height)
        assert 0.0 <= normalised.x <= 1.0
        assert 0.0 <= normalised.y <= 1.0
        assert normalised.x + normalised.width <= 1.0 + 1e-6
        assert normalised.y + normalised.height <= 1.0 + 1e-6


def test_lines_come_back_in_reading_order() -> None:
    """Top to bottom. Entity extraction reads blocks in order and would otherwise scramble
    a prescription's dose lines relative to its drug names."""
    boxes = segment.segment_lines(_page("prescription_scan.png"), prefer="projection")
    tops = [box.top for box in boxes]
    assert tops == sorted(tops)


def test_the_two_detectors_broadly_agree() -> None:
    """Not an equality assertion — they are different algorithms and will differ by a line.

    It is a sanity check that the projection profile is measuring the same document
    Tesseract's layout analysis is, rather than producing plausible-looking noise.
    """
    image = _page("prescription_scan.png")
    projection = segment.segment_lines(image, prefer="projection")
    layout = segment.segment_lines(image, prefer="tesseract-layout")
    if not layout:
        pytest.skip("tesseract is not installed in this environment")
    assert abs(len(projection) - len(layout)) <= 3


def test_a_blank_page_segments_into_nothing_rather_than_one_huge_box() -> None:
    """An empty list is a real answer. A single page-sized band is a fabricated line."""
    blank = Image.new("L", (1240, 1754), color=255)
    assert segment.segment_lines(blank, prefer="projection") == []


def test_the_detector_is_recorded_on_every_box() -> None:
    """A physician looking at an odd box needs to know which detector measured it."""
    for box in segment.segment_lines(_page("lab_report_scan.png"), prefer="projection"):
        assert box.detector == "projection"


# ---------------------------------------------------------------- imaging contract


def test_threshold_false_does_not_binarise_but_keeps_the_dimensions() -> None:
    """The neural models need natural greyscale; the geometry must still line up.

    Both halves matter. If `threshold=False` resized the page, every box measured on the
    detector's view would be wrong on the recogniser's view.
    """
    raw = (FIXTURES / "prescription_scan.png").read_bytes()
    thresholded = imaging.prepare(raw, filename="p.png", threshold=True)
    natural = imaging.prepare(raw, filename="p.png", threshold=False)

    assert (thresholded.width, thresholded.height) == (natural.width, natural.height)
    # A binarised page has essentially two values; a natural one has many.
    assert len(natural.image.convert("L").getcolors(maxcolors=300) or []) > 32
    assert len(thresholded.image.convert("L").getcolors(maxcolors=300) or []) <= 4


def test_binarise_preserves_dimensions() -> None:
    """`segment` measures on the binarised view and crops on the natural one."""
    image = _page("lab_report_scan.png")
    assert imaging.binarise(image).size == image.size


def test_the_default_prepare_is_unchanged() -> None:
    """Tesseract's input must be byte-identical to what it was verified against."""
    raw = (FIXTURES / "prescription_scan.png").read_bytes()
    assert (
        imaging.prepare(raw, filename="p.png").image.tobytes()
        == imaging.prepare(raw, filename="p.png", threshold=True).image.tobytes()
    )


# ---------------------------------------------------------------- confidence mapping


def test_confidence_mapping_is_monotonic_and_bounded() -> None:
    values = [neural.map_logprob(v) for v in (-4.0, -1.6, -1.0, -0.5, -0.1, 0.0)]
    assert values == sorted(values)
    assert all(0.0 <= v <= 1.0 for v in values)
    assert values[0] == 0.0, "a hopeless generation must map to zero, not to a small number"
    assert values[-1] == 1.0


def test_a_guessed_line_lands_in_the_verification_lane() -> None:
    """⛔ THE LOAD-BEARING PROPERTY OF THE MAPPING.

    A mean token log-probability of -1.0 is a model choosing between roughly three options
    per token. That reading must not be merged into a clinical record without a human, so it
    has to map BELOW `ocr_low_confidence_threshold` — which is what routes it to the lane.
    """
    assert neural.map_logprob(-1.0) < settings.ocr_low_confidence_threshold
    assert neural.map_logprob(-0.1) > settings.ocr_low_confidence_threshold


# ---------------------------------------------------------------- availability


def test_both_backends_construct_without_torch() -> None:
    """`/about` builds every backend on every call. Construction must never load weights."""
    assert get_ocr_backend("got-ocr2").name == "got-ocr2"
    assert get_ocr_backend("prescription-trocr").name == "prescription-trocr"


def test_an_unavailable_backend_says_why() -> None:
    """"Not available" alone sends an operator hunting for a GPU when the answer is a flag."""
    for name in ("got-ocr2", "prescription-trocr"):
        backend = get_ocr_backend(name)
        if not backend.available:
            reason = getattr(backend, "unavailable_reason", None)
            assert reason, f"{name} is unavailable and does not say why"


def test_about_reports_every_engine_with_its_reason() -> None:
    reported = {entry["name"]: entry for entry in available_backends()}
    assert {"textlayer", "tesseract", "got-ocr2", "prescription-trocr"} <= set(reported)
    for name in ("got-ocr2", "prescription-trocr"):
        if not reported[name]["available"]:
            assert reported[name].get("reason")


def test_the_gated_model_names_the_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing HF_TOKEN must be reported as a missing HF_TOKEN, not as a generic failure.

    Without this the operator sees the same "unavailable" for an unset flag, an absent
    torch and an unrequested access grant — three completely different fixes.
    """
    monkeypatch.setattr(settings, "neural_ocr_enabled", True)
    monkeypatch.setattr(settings, "hf_token", None)
    monkeypatch.setattr(neural, "_torch_available", lambda: True)

    backend = neural.MedicalPrescriptionOCR()
    assert backend.available is False
    assert "HF_TOKEN" in (backend.unavailable_reason or "")


def test_a_disabled_backend_refuses_to_read_rather_than_returning_nothing() -> None:
    """An engine that cannot run must raise, not hand back an empty document.

    A page with no blocks is indistinguishable downstream from a blank sheet of paper.
    """
    from app.core.errors import UpstreamUnavailable

    backend = neural.GotOcr2OCR()
    if backend.available:
        pytest.skip("neural OCR is enabled in this environment")
    with pytest.raises(UpstreamUnavailable):
        backend.read(b"whatever", filename="scan.png", media_type="image/png")


# ---------------------------------------------------------------- dispatch


def test_a_digital_pdf_still_goes_to_textlayer() -> None:
    """⛔ THE NEURAL ENGINES DO NOT DISPLACE `textlayer`, EVER.

    A digital PDF already carries exact text and true glyph geometry. Routing it through a
    1.1 GB vision model would be slower, less accurate, and would replace measured glyph
    boxes with detected line boxes.
    """
    chosen = backend_for("application/pdf", "lab_report.pdf")
    assert chosen.name == "textlayer"


def test_images_fall_back_to_tesseract_when_neural_ocr_is_off() -> None:
    """The default clone must behave exactly as it did before these backends existed."""
    if settings.neural_ocr_enabled:
        pytest.skip("neural OCR is enabled in this environment")
    chosen = backend_for("image/png", "prescription_scan.png")
    assert chosen.name == "tesseract"


def test_an_explicit_request_still_wins() -> None:
    """The benchmark compares engines on identical inputs and must be able to pin one."""
    assert backend_for("image/png", "x.png", requested="got-ocr2").name == "got-ocr2"


# ---------------------------------------------------------------- quality routing
#
# Measured by `python -m eval.ocr_bench`, image variants, n=7. The routing exists because
# GOT-OCR2 is better on degraded input and WORSE on a clean prescription scan (med recall
# 1.00 -> 0.75), so a blanket replacement would have been a regression. Tesseract's own mean
# page confidence separated the two classes with no overlap: clean at 0.84/0.90/0.90/0.91,
# degraded at 0.10/0.34/0.50. The cut is `ocr_low_confidence_threshold`, inside that gap.


class _StubNeural:
    """A neural backend that answers without torch, so these run on a bare clone."""

    name = "got-ocr2"

    def __init__(self, *, available: bool = True, blocks: tuple[OCRBlock, ...] | None = None):
        self.available = available
        self.unavailable_reason = None if available else "stubbed as unavailable"
        self._blocks = blocks

    def read(self, data: bytes, *, filename: str, media_type: str) -> OCRResult:
        blocks = self._blocks
        if blocks is None:
            blocks = (
                OCRBlock(
                    text="TAB. METFORMIN 500MG 1-0-1 x 30 days",
                    bbox=BoundingBox(x=0.05, y=0.26, width=0.6, height=0.02),
                    # Deliberately the overconfidence the benchmark measured: GOT-OCR2
                    # reported 0.87-1.00 on every fixture, including ones where it recovered
                    # half the medications.
                    confidence=0.98,
                    handwritten=False,
                ),
            )
        return OCRResult(backend=self.name, pages=(OCRPage(1, blocks, 1240, 1754),))


def _route(monkeypatch: pytest.MonkeyPatch, neural: _StubNeural) -> object:
    """A QualityRoutedOCR whose neural half is the stub above."""
    real = backends.get_ocr_backend

    def fake(name: str | None = None):  # type: ignore[no-untyped-def]
        return neural if name == "got-ocr2" else real(name)

    monkeypatch.setattr(backends, "get_ocr_backend", fake)
    return backends.QualityRoutedOCR()


def _needs_tesseract() -> None:
    if not backends.TesseractOCR().available:
        pytest.skip("tesseract is not installed in this environment")


@pytest.mark.parametrize(
    "name", ["prescription_scan.png", "lab_report_scan.png", "discharge_scan.png"]
)
def test_a_clean_scan_keeps_tesseract(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """⛔ GOT-OCR2 MUST NOT SEE A CLEAN SCAN.

    Measured: it drops med recall 1.00 -> 0.75 on `prescription_scan.png`. This is the half
    of the routing that prevents a regression, not the half that buys an improvement.
    """
    _needs_tesseract()
    routed = _route(monkeypatch, _StubNeural())
    result = routed.read(  # type: ignore[attr-defined]
        (FIXTURES / name).read_bytes(), filename=name, media_type="image/png"
    )
    assert result.backend == "tesseract"
    assert result.mean_confidence >= settings.ocr_low_confidence_threshold


@pytest.mark.parametrize(
    "name",
    ["prescription_degraded.png", "lab_report_degraded.png", "discharge_degraded.png"],
)
def test_a_degraded_scan_is_re_read_by_the_neural_engine(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The improvement half: med recall 0.50 -> 1.00 on `prescription_degraded.png`."""
    _needs_tesseract()
    if not (FIXTURES / name).exists():
        pytest.skip(f"{name} is not in this checkout")
    routed = _route(monkeypatch, _StubNeural())
    result = routed.read(  # type: ignore[attr-defined]
        (FIXTURES / name).read_bytes(), filename=name, media_type="image/png"
    )
    assert result.backend == "got-ocr2"


def test_the_re_read_cannot_raise_its_own_confidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """⛔ THE SAFETY PROPERTY OF THE WHOLE FEATURE.

    GOT-OCR2 reported 0.87-1.00 on every degraded fixture and sent 0% to the verification
    lane, where Tesseract had sent 67-100%. Taking the better reading must not also take the
    model's opinion of it: a fluent model reading a smudged line confidently is exactly the
    case that looks like success and is not.
    """
    _needs_tesseract()
    name = "prescription_degraded.png"
    routed = _route(monkeypatch, _StubNeural())
    result = routed.read(  # type: ignore[attr-defined]
        (FIXTURES / name).read_bytes(), filename=name, media_type="image/png"
    )

    assert result.backend == "got-ocr2"
    # The stub claimed 0.98. The page did not earn it.
    for page in result.pages:
        for block in page.blocks:
            assert block.confidence < settings.ocr_low_confidence_threshold, (
                "a block from a degraded-page re-read escaped the verification lane"
            )


def test_bounding_is_a_minimum_and_never_raises_a_score() -> None:
    """Pure unit check of the evidence combination, no fixtures and no engines."""
    blocks = (
        OCRBlock("high", BoundingBox(x=0.1, y=0.1, width=0.5, height=0.02), 0.99),
        OCRBlock("low", BoundingBox(x=0.1, y=0.2, width=0.5, height=0.02), 0.20),
    )
    result = OCRResult(backend="got-ocr2", pages=(OCRPage(1, blocks, 100, 100),))
    bounded = backends._bounded_by_page_quality(result, 0.34)

    assert [b.confidence for b in bounded.pages[0].blocks] == [0.34, 0.20]
    assert [b.text for b in bounded.pages[0].blocks] == ["high", "low"]


def test_a_failed_re_read_keeps_the_tesseract_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The re-read is an improvement, never a dependency."""
    _needs_tesseract()

    class _Exploding(_StubNeural):
        def read(self, data: bytes, *, filename: str, media_type: str) -> OCRResult:
            raise UpstreamUnavailable("the model fell over")

    routed = _route(monkeypatch, _Exploding())
    result = routed.read(  # type: ignore[attr-defined]
        (FIXTURES / "prescription_degraded.png").read_bytes(),
        filename="prescription_degraded.png",
        media_type="image/png",
    )
    assert result.backend == "tesseract"
    assert any(page.blocks for page in result.pages)


def test_an_empty_re_read_keeps_the_tesseract_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A model that returns nothing must not turn a readable page into a blank one."""
    _needs_tesseract()
    routed = _route(monkeypatch, _StubNeural(blocks=()))
    result = routed.read(  # type: ignore[attr-defined]
        (FIXTURES / "prescription_degraded.png").read_bytes(),
        filename="prescription_degraded.png",
        media_type="image/png",
    )
    assert result.backend == "tesseract"


def test_an_unavailable_neural_engine_keeps_the_tesseract_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The documented fallback, unchanged from before quality routing existed."""
    _needs_tesseract()
    routed = _route(monkeypatch, _StubNeural(available=False))
    result = routed.read(  # type: ignore[attr-defined]
        (FIXTURES / "prescription_degraded.png").read_bytes(),
        filename="prescription_degraded.png",
        media_type="image/png",
    )
    assert result.backend == "tesseract"


def test_images_route_through_the_quality_gate_when_both_engines_are_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _needs_tesseract()
    monkeypatch.setattr(settings, "neural_ocr_enabled", True)
    monkeypatch.setattr(neural, "_torch_available", lambda: True)
    assert backend_for("image/png", "scan.png").name == "quality-routed"


def test_the_quality_gate_is_skipped_entirely_when_neural_ocr_is_off() -> None:
    """A default clone pays nothing: one Tesseract pass, no second engine, no decision."""
    _needs_tesseract()
    if settings.neural_ocr_enabled:
        pytest.skip("neural OCR is enabled in this environment")
    assert backend_for("image/png", "scan.png").name == "tesseract"


# ---------------------------------------------------------------- assembly


class _StubRecogniser:
    """Stands in for a loaded model. `_read_pages` only calls `load()`."""

    def load(self) -> tuple[object, object]:
        return object(), object()


def _assemble(read_crop, *, handwritten: bool, name: str = "stub"):  # type: ignore[no-untyped-def]
    image = _page("prescription_scan.png")
    return neural._read_pages(
        _StubRecogniser(),  # type: ignore[arg-type]
        [image],
        backend_name=name,
        handwritten=handwritten,
        read_crop=read_crop,
    )


def test_every_assembled_block_carries_a_measured_box() -> None:
    result = _assemble(lambda _m, _p, _c: ("METFORMIN 500mg", 0.95), handwritten=False)
    page = result.pages[0]
    assert page.blocks
    for block in page.blocks:
        assert 0.0 <= block.bbox.x <= 1.0
        assert 0.0 <= block.bbox.y <= 1.0
        assert block.bbox.width > 0 and block.bbox.height > 0


def test_the_prescription_model_marks_everything_handwritten() -> None:
    """⛔ INVARIANT-ADJACENT, AND THE REASON THIS BACKEND IS SAFE TO SHIP.

    `pipeline.ingest()` never auto-merges a handwritten block, so this flag is what puts a
    person between a generative model's reading of a doctor's scrawl and a patient's
    medication list. A high score must NOT clear it: a TrOCR model asked to read an
    illegible word returns a real, common drug name with high token probability, because the
    language model is sure even though the reading is wrong.
    """
    result = _assemble(lambda _m, _p, _c: ("AMOXICILLIN", 0.99), handwritten=True)
    assert result.pages[0].blocks
    assert all(block.handwritten for block in result.pages[0].blocks)


def test_a_low_confidence_block_is_flagged_even_on_the_printed_engine() -> None:
    result = _assemble(lambda _m, _p, _c: ("smudged", 0.10), handwritten=False)
    assert all(block.handwritten for block in result.pages[0].blocks)


def test_empty_readings_are_dropped_rather_than_recorded_as_blank_blocks() -> None:
    result = _assemble(lambda _m, _p, _c: ("   ", 0.9), handwritten=False)
    assert result.pages[0].blocks == ()


def test_one_failing_line_does_not_lose_the_page() -> None:
    """A single bad crop must not cost the patient the whole document."""
    state = {"calls": 0}

    def flaky(_m, _p, _c):  # type: ignore[no-untyped-def]
        state["calls"] += 1
        if state["calls"] == 2:
            raise RuntimeError("CUDA hiccup")
        return ("PARACETAMOL 650mg", 0.9)

    result = _assemble(flaky, handwritten=False)
    assert len(result.pages[0].blocks) >= 3


def test_the_line_cap_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    """A page segmented into noise must not mean hundreds of model calls."""
    monkeypatch.setattr(settings, "neural_ocr_max_lines", 3)
    result = _assemble(lambda _m, _p, _c: ("line", 0.9), handwritten=False)
    assert len(result.pages[0].blocks) <= 3
