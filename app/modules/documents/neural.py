"""Neural OCR — two HuggingFace models behind the same `OCRBackend` protocol.

* **GOT-OCR2** (`stepfun-ai/GOT-OCR-2.0-hf`) — printed and scanned documents: lab reports,
  discharge summaries, e-prescriptions that arrived as a photograph. Apache-2.0, ungated.
* **Medical-Prescription-OCR** (`khedim/Medical-Prescription-OCR`) — handwritten prescription
  text. A TrOCR `VisionEncoderDecoderModel`, and ⚠️ **gated**: it needs an approved access
  request on HuggingFace plus `HF_TOKEN`.

Both displace **Tesseract**, which is where recognition actually happened. Neither displaces
`textlayer`: that reads the embedded text layer of a digital PDF, which is a transcription
with exact glyph geometry, and running a 1.1 GB model over a file that already contains its
own perfect text would be slower and less accurate. `backend_for()` dispatches on what the
file is; see its docstring.

---

## Three things these models do not give you, and what is done about each

**1. No bounding box.** GOT-OCR2 emits text; TrOCR has no detection stage whatsoever. Geometry
comes from `segment.py`, which measures line boxes on the same conditioned image the
recogniser reads, and every box on a block from this module was measured there. Nothing is
inferred from line ordinal, and no fact carries a whole-page box.

**2. No confidence.** Both are generative, so confidence is derived from the mean transition
log-probability of the tokens they generated and mapped onto [0, 1] by the constants below.
This is the same move `app/speech/groq_whisper.py` makes for Whisper's `avg_logprob`, and the
same rule applies: the mapping is a judgement call, so it is stated in the open rather than
buried, and anything under `OCR_LOW_CONFIDENCE_THRESHOLD` goes to the verification lane
exactly as a low-confidence Tesseract line does.

**3. No refusal.** A classical engine returns nothing when it cannot read something. A
language model returns fluent, plausible, wrong text — which is the failure mode that matters
most in a clinical setting, because it is the one that looks like success. That is why
`handwritten=True` is set on every block from the prescription model regardless of score:
`pipeline.ingest()` routes handwritten blocks to a human and never auto-merges them, so the
model's output cannot reach the record without a person having read it. See ADR-0015.

---

## Why one model call per line, and not one per page

GOT-OCR2 can read a whole page in a single pass, which would be several times faster. It is
not done that way, because making a page-level result fit the per-block contract needs one of
two fudges: pair the model's Nth output line with the detector's Nth box (an assumption that
silently produces wrong boxes the moment the counts disagree), or give every block the page's
mean confidence (which would flatten the one signal the handwriting lane routes on).

So each detected line is cropped and read on its own. Every block then carries a box that was
measured and a confidence that was computed for that line's own tokens. It costs a model call
per line, capped by `NEURAL_OCR_MAX_LINES`.

---

## Dependencies

`torch` and `transformers` are ~3 GB installed and are deliberately **not** in
requirements.txt. Absent, `available` is False with a reason and `backend_for()` routes to
Tesseract — the same shape as the `vosk` and `bhashini` paths. Nothing degrades silently, and
`make setup` and the deploy image are unchanged for anyone not using these.
"""

from __future__ import annotations

import functools
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from PIL import Image

from app.core.config import settings
from app.core.errors import UpstreamUnavailable
from app.core.logging import get_logger
from app.modules.documents import imaging, segment
from app.modules.documents.backends import (
    OCRBlock,
    OCRPage,
    OCRResult,
    UnsupportedMedia,
)

log = get_logger(__name__)

#: Mean transition log-probability mapped onto [0, 1]. A judgement call, stated rather than
#: buried — the same treatment `groq_whisper._confidence_from` gives Whisper's `avg_logprob`.
#:
#: -0.10 is a token the model was near-certain of (p ~ 0.90); -1.60 is one it essentially
#: guessed between five options (p ~ 0.20). A line averaging the latter is not a line to merge
#: into a clinical record without a human, and the mapping puts it well under
#: `ocr_low_confidence_threshold` (0.72) so it lands in the verification lane on its own.
_LOGPROB_CONFIDENT = -0.10
_LOGPROB_POOR = -1.60

#: Generation ceiling for ONE LINE. A line of a prescription is a few dozen tokens; a model
#: generating 512 of them has entered a repetition loop, and the cap is what stops that
#: costing a minute per line.
_MAX_NEW_TOKENS_PER_LINE = 256

#: Rasterisation DPI for a scanned PDF. Matches `TesseractOCR.PDF_DPI` and `render.py`'s
#: RENDER_DPI band deliberately: the physician's evidence overlay is drawn on an image
#: rendered from the same source, and coordinates measured at one scale must mean the same
#: thing at the other.
_PDF_DPI = 300

_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".heic", ".heif", ".tif", ".tiff")


# ---------------------------------------------------------------- runtime


def _import_error() -> str:
    return (
        "Neural OCR needs `torch` and `transformers`, which are optional and not installed "
        "(pip install 'torch>=2.2' 'transformers>=4.49'). Tesseract is used instead."
    )


@functools.lru_cache(maxsize=1)
def _torch_available() -> bool:
    try:
        import torch  # noqa: F401, PLC0415
        import transformers  # noqa: F401, PLC0415
    except ImportError:
        return False
    return True


def _neural_unavailable_reason() -> str | None:
    """Why neural OCR cannot run, or None when it can. Shared by both backends.

    Returned as a sentence rather than a bool because it is what `/about` shows and what the
    operator log carries: "unavailable" on its own has sent people looking for a missing GPU
    when the actual answer was a flag left at its default.
    """
    if not settings.neural_ocr_enabled:
        return "NEURAL_OCR_ENABLED is false."
    if not _torch_available():
        return _import_error()
    return None


@functools.lru_cache(maxsize=1)
def _device() -> str:
    """cuda, then mps, then cpu — unless pinned. Resolved once and logged once."""
    if settings.neural_ocr_device != "auto":
        return settings.neural_ocr_device
    try:
        import torch  # noqa: PLC0415

        if torch.cuda.is_available():
            return "cuda"
        # Apple Silicon. Guarded because `torch.backends.mps` is absent on older builds.
        if getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
            return "mps"
    except Exception:  # noqa: BLE001 — a probe must never take the process down
        pass
    return "cpu"


def map_logprob(mean_logprob: float) -> float:
    """Mean transition log-probability -> [0, 1], by the constants above.

    Deliberately pure and free of `torch`, so the mapping that decides whether a clinical
    reading reaches the verification lane is testable on a machine with no ML stack
    installed — which is every machine running the default test suite.
    """
    span = _LOGPROB_CONFIDENT - _LOGPROB_POOR
    return round(min(max((mean_logprob - _LOGPROB_POOR) / span, 0.0), 1.0), 4)


def _confidence_from(scores: Any) -> float:
    """`map_logprob` over a generation's finite transition scores.

    Returns 0.0 for a generation that produced no scorable token: an empty read is not a
    confident one, and the caller drops empty text anyway.
    """
    import torch  # noqa: PLC0415

    if scores is None:
        return 0.0
    finite = scores[torch.isfinite(scores)]
    if finite.numel() == 0:
        return 0.0
    return map_logprob(float(finite.mean().item()))


class _Recogniser:
    """Lazily-loaded HuggingFace model + processor. One instance per model id, per process.

    Loading is deferred to the first `read()` rather than done in `__init__` so that
    constructing a backend — which `available_backends()` does on every `/about` call — never
    pulls a gigabyte of weights into memory.
    """

    def __init__(self, model_id: str, *, gated: bool = False) -> None:
        self.model_id = model_id
        self.gated = gated
        self._model: Any = None
        self._processor: Any = None

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def load(self) -> tuple[Any, Any]:
        if self._model is not None:
            return self._model, self._processor

        if not _torch_available():
            raise UpstreamUnavailable(_import_error())

        from transformers import AutoModelForImageTextToText, AutoProcessor  # noqa: PLC0415

        token = settings.hf_token
        if self.gated and not token:
            raise UpstreamUnavailable(
                f"{self.model_id} is a gated repository. Request access on HuggingFace and "
                "set HF_TOKEN. This kiosk cannot read handwritten prescriptions until then."
            )

        kwargs: dict[str, Any] = {}
        if token:
            kwargs["token"] = token

        try:
            self._processor = AutoProcessor.from_pretrained(self.model_id, **kwargs)
            self._model = self._load_model(AutoModelForImageTextToText, kwargs)
        except Exception as exc:  # noqa: BLE001 — surfaced as an actionable message below
            # The message a patient sees is written by the route; this one is for whoever
            # runs the kiosk, and it goes to the log where they will look.
            log.error(
                "neural_ocr.load_failed",
                model=self.model_id,
                gated=self.gated,
                error=str(exc)[:300],
            )
            raise UpstreamUnavailable(
                f"Could not load {self.model_id}: {str(exc)[:200]}"
            ) from exc

        self._model.eval()
        log.info("neural_ocr.loaded", model=self.model_id, device=_device())
        return self._model, self._processor

    def _load_model(self, auto_class: Any, kwargs: dict[str, Any]) -> Any:
        """`AutoModelForImageTextToText`, falling back to the vision-encoder-decoder class.

        TrOCR checkpoints predate the image-text-to-text auto class and some are not
        registered for it. The fallback is narrow and explicit rather than a bare except:
        anything other than "this auto class does not know this architecture" is a real
        failure and must not be swallowed.
        """
        try:
            return auto_class.from_pretrained(self.model_id, **kwargs).to(_device())
        except (ValueError, KeyError):
            from transformers import VisionEncoderDecoderModel  # noqa: PLC0415

            log.info("neural_ocr.using_vision_encoder_decoder", model=self.model_id)
            return VisionEncoderDecoderModel.from_pretrained(self.model_id, **kwargs).to(
                _device()
            )


# ---------------------------------------------------------------- shared page reading


def _pages_from(
    data: bytes, filename: str, media_type: str
) -> tuple[list[Image.Image], imaging.Prepared | None]:
    """Every page of the upload as a conditioned image, plus what conditioning had to do.

    ⛔ `threshold=False`. See `imaging.prepare` — a hard-binarised page is what Tesseract
    wants and the opposite of what a model trained on photographs wants.

    The `Prepared` record comes back alongside rather than being re-derived by the caller:
    `prepare()` decodes, deskews and rescales, and paying that twice per upload to recover a
    record we already had is pure waste. It is `None` for a PDF, where there is no single
    source photograph to describe.
    """
    lowered = filename.lower()
    is_pdf = media_type == "application/pdf" or lowered.endswith(".pdf")
    is_image = media_type.startswith("image/") or lowered.endswith(_IMAGE_SUFFIXES)

    if is_pdf:
        return _rasterise_pdf(data), None
    if is_image:
        prepared = imaging.prepare(data, filename=filename, threshold=False)
        return [prepared.image], prepared

    log.info("neural_ocr.unsupported_media", media_type=media_type)
    raise UnsupportedMedia(
        "This kiosk can read photographs and PDF files. Taking a photo of the paper is "
        "usually the easiest way.",
        reason="unsupported_type",
    )


def _rasterise_pdf(data: bytes) -> list[Image.Image]:
    try:
        import pypdfium2  # noqa: PLC0415
    except ImportError as exc:
        raise UpstreamUnavailable(
            "Rasterising a PDF for OCR needs `pypdfium2` (in requirements.txt)."
        ) from exc

    images: list[Image.Image] = []
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "upload.pdf"
        source.write_bytes(data)
        document = pypdfium2.PdfDocument(str(source))
        try:
            for index in range(len(document)):
                rendered = document[index].render(scale=_PDF_DPI / 72).to_pil()
                buffer = Path(tmp) / f"page-{index}.png"
                rendered.save(buffer, format="PNG")
                images.append(
                    imaging.prepare(
                        buffer.read_bytes(), filename=buffer.name, threshold=False
                    ).image
                )
        finally:
            document.close()
    return images


def _read_pages(
    recogniser: _Recogniser,
    images: list[Image.Image],
    *,
    backend_name: str,
    handwritten: bool,
    read_crop: Any,
) -> OCRResult:
    """Detect lines on every page, read each one, and assemble the blocks.

    `read_crop` is the model-specific call: crop in, `(text, confidence)` out. Everything
    else — detection, cropping, the line cap, normalisation — is identical for both models,
    which is the whole reason they share this function.
    """
    model, processor = recogniser.load()

    pages: list[OCRPage] = []
    for number, image in enumerate(images, start=1):
        boxes = segment.segment_lines(image)
        if len(boxes) > settings.neural_ocr_max_lines:
            # Truncation is reported, never silent. A page that segments into more lines than
            # this is usually noise, and reading 400 crops would hang the upload with nothing
            # on screen to explain it.
            log.warning(
                "neural_ocr.line_cap_reached",
                page=number,
                detected=len(boxes),
                cap=settings.neural_ocr_max_lines,
            )
            boxes = boxes[: settings.neural_ocr_max_lines]

        blocks: list[OCRBlock] = []
        for box in boxes:
            crop = image.crop(box.box).convert("RGB")
            try:
                text, confidence = read_crop(model, processor, crop)
            except Exception as exc:  # noqa: BLE001 — one bad line must not lose the page
                log.warning(
                    "neural_ocr.line_failed", page=number, error=str(exc)[:160]
                )
                continue
            if not text.strip():
                continue
            blocks.append(
                OCRBlock(
                    text=text.strip(),
                    bbox=box.normalised(image.width, image.height),
                    confidence=confidence,
                    handwritten=handwritten
                    or confidence < settings.ocr_low_confidence_threshold,
                )
            )

        pages.append(
            OCRPage(
                page=number,
                blocks=tuple(blocks),
                width=image.width,
                height=image.height,
            )
        )

    return OCRResult(backend=backend_name, pages=tuple(pages))


# ---------------------------------------------------------------- GOT-OCR2


class GotOcr2OCR:
    """Satisfies `OCRBackend`. Printed and scanned documents.

    Replaces Tesseract as the image engine when `NEURAL_OCR_ENABLED=true`. Not a replacement
    for `textlayer` — see the module docstring.
    """

    name = "got-ocr2"

    def __init__(self) -> None:
        self._recogniser = _Recogniser(settings.got_ocr_model)
        # A plain attribute, not a property: `OCRBackend` declares `available: bool`, and a
        # read-only property does not satisfy a mutable protocol member. `TesseractOCR` sets
        # it in `__init__` for the same reason, and a fresh instance is built per call.
        self.unavailable_reason = _neural_unavailable_reason()
        self.available = self.unavailable_reason is None

    def read(self, data: bytes, *, filename: str, media_type: str) -> OCRResult:
        if not self.available:
            log.error("neural_ocr.unavailable", backend=self.name, why=self.unavailable_reason)
            raise UpstreamUnavailable("This kiosk cannot read photographs at the moment.")

        images, prepared = _pages_from(data, filename, media_type)
        result = _read_pages(
            self._recogniser,
            images,
            backend_name=self.name,
            handwritten=False,
            read_crop=self._read_crop,
        )
        return replace(result, preparation=prepared)

    def _read_crop(self, model: Any, processor: Any, crop: Image.Image) -> tuple[str, float]:
        import torch  # noqa: PLC0415

        inputs = processor(crop, return_tensors="pt").to(_device())
        with torch.no_grad():
            generated = model.generate(
                **inputs,
                do_sample=False,
                tokenizer=processor.tokenizer,
                stop_strings="<|im_end|>",
                max_new_tokens=_MAX_NEW_TOKENS_PER_LINE,
                return_dict_in_generate=True,
                output_scores=True,
            )
        prompt_length = int(inputs["input_ids"].shape[1])
        text = processor.decode(
            generated.sequences[0, prompt_length:], skip_special_tokens=True
        )
        scores = model.compute_transition_scores(
            generated.sequences, generated.scores, normalize_logits=True
        )
        return text, _confidence_from(scores)


# ---------------------------------------------------------------- Medical prescription


class MedicalPrescriptionOCR:
    """Satisfies `OCRBackend`. Handwritten prescription text.

    ⛔ EVERY BLOCK IS MARKED `handwritten=True`, WHATEVER IT SCORED.

    `pipeline.ingest()` writes facts only for confident, non-handwritten entities; a
    handwritten one becomes a fact only when a human calls `verify_entity()`. Marking the
    whole output of this model handwritten is what puts a person between a generative model's
    reading of a doctor's scrawl and a patient's medication list.

    It is not a statement about the score. A TrOCR model asked to read an illegible word
    returns a real, common, confidently-generated drug name — the token probabilities are
    high because the *language model* is sure, not because the *reading* is right. A
    confidence gate alone cannot catch that, so it is not relied on to.

    ⚠️ GATED, AND THEREFORE UNVERIFIED HERE. The repository requires an approved access
    request plus `HF_TOKEN`; with either missing, `load()` raises `UpstreamUnavailable` with
    an actionable message. The generation call below follows the standard
    `VisionEncoderDecoderModel` contract, but it has never been run against these weights —
    treat the first live call as an integration test, exactly as `speech/bhashini.py` says of
    itself.
    """

    name = "prescription-trocr"

    def __init__(self) -> None:
        self._recogniser = _Recogniser(settings.prescription_ocr_model, gated=True)
        reason = _neural_unavailable_reason()
        if reason is None and not settings.hf_token:
            reason = (
                f"{settings.prescription_ocr_model} is gated: request access on HuggingFace "
                "and set HF_TOKEN."
            )
        self.unavailable_reason = reason
        self.available = reason is None

    def read(self, data: bytes, *, filename: str, media_type: str) -> OCRResult:
        if not self.available:
            log.error("neural_ocr.unavailable", backend=self.name, why=self.unavailable_reason)
            raise UpstreamUnavailable("This kiosk cannot read handwriting at the moment.")

        images, _ = _pages_from(data, filename, media_type)
        return _read_pages(
            self._recogniser,
            images,
            backend_name=self.name,
            handwritten=True,
            read_crop=self._read_crop,
        )

    def _read_crop(self, model: Any, processor: Any, crop: Image.Image) -> tuple[str, float]:
        import torch  # noqa: PLC0415

        pixel_values = processor(images=crop, return_tensors="pt").pixel_values.to(_device())
        with torch.no_grad():
            generated = model.generate(
                pixel_values,
                max_new_tokens=_MAX_NEW_TOKENS_PER_LINE,
                return_dict_in_generate=True,
                output_scores=True,
            )
        text = processor.batch_decode(generated.sequences, skip_special_tokens=True)[0]
        scores = model.compute_transition_scores(
            generated.sequences, generated.scores, normalize_logits=True
        )
        return text, _confidence_from(scores)


__all__ = ["GotOcr2OCR", "MedicalPrescriptionOCR"]
