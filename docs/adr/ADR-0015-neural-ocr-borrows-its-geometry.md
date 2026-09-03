# ADR-0015 — A neural OCR model supplies text; a detector supplies the box

**Context.** Two HuggingFace models were adopted for document reading: GOT-OCR2
(`stepfun-ai/GOT-OCR-2.0-hf`) for printed and scanned documents, and a TrOCR fine-tune
(`khedim/Medical-Prescription-OCR`) for handwritten prescriptions. Both are markedly better
than Tesseract at what they do, and neither can satisfy the `OCRBackend` contract on its own.

`OCRBlock` requires a bounding box and a confidence for every region, because `DocumentSpan`
makes `page` and `bbox` mandatory (Invariant 2) and the handwriting lane routes on confidence.
GOT-OCR2 is a vision-language model: an image goes in, text comes out, and while it will read
a region you hand it, it does not tell you where anything was. TrOCR is worse placed still —
it is a line *recogniser* with no detection stage whatsoever, trained on single cropped lines,
and a full page fed to it returns confident nonsense.

So both requirements — geometry and confidence — are things these models structurally do not
produce. `render.py` already states the standard that makes this a hard problem rather than a
formality: "a box drawn in the wrong place is worse than no box, because it tells a physician
the system read a line it did not read."

**Decision.** Split the job. `segment.py` detects text lines and *measures* their boxes; the
model reads one crop at a time and supplies only text. Every box a neural backend emits was
measured on the same conditioned page the recogniser read, at the same scale. Confidence is
derived from the mean transition log-probability of the generated tokens and mapped onto
[0, 1] by two constants stated in the open — the same move `speech/groq_whisper.py` makes for
Whisper's `avg_logprob`, for the same reason and with the same honesty about it being a
judgement call.

Two consequences of the split are deliberate rather than incidental. Detection runs on a
binarised, speckle-filtered view of the page while recognition runs on the natural greyscale
one, because a threshold is what a projection profile needs and the opposite of what a model
trained on photographs needs; this is sound only because thresholding does not resize, so a
box measured on one view means the same thing on the other. And every block from the
prescription model is marked `handwritten=True` whatever it scored, which puts
`pipeline.ingest()`'s human gate between a generative model and a patient's medication list.

**Alternatives.** *One page-level pass, pairing the model's Nth output line with the
detector's Nth box* — several times faster, and silently wrong the moment the counts
disagree, which is exactly the class of error the box rules exist to prevent. *One page-level
pass with a whole-page bbox on every fact* — satisfies the type and destroys click-to-source,
which is the product. *Give every block the page's mean confidence* — flattens the one signal
the verification lane routes on. *Trust the model's own score* — there isn't one. *Let the
neural engines replace `textlayer` too* — `textlayer` is not a competing recogniser; it lifts
the embedded text layer out of a digital PDF, which is a transcription with exact glyph
geometry, so running a 1.1 GB model over it would be slower, less accurate, and would trade
measured glyph boxes for detected line boxes.

**Consequences.** One model call per detected line, capped by `NEURAL_OCR_MAX_LINES`. That is
the cost of not fudging either value, and it is the main thing to revisit if throughput ever
matters more than it does at a kiosk serving one patient at a time.

`torch` and `transformers` stay out of `requirements.txt`: ~3 GB installed for a feature that
is off by default, on a deploy whose cold start is the demo. Absent, both backends report
`available = False` **with a reason** and images route to Tesseract exactly as before —
`tests/test_neural_ocr.py` pins that the default clone is unchanged.

The prescription model is **gated** on HuggingFace and therefore ships unverified against its
own weights, which is stated in `neural.py` in the same terms `speech/bhashini.py` uses of
itself. Its generation call follows the standard `VisionEncoderDecoderModel` contract; treat
the first live call as an integration test.

The detector, unlike the models, is fully verified here. The first implementation of the
projection profile found **zero** lines on four of seven page fixtures — every degraded scan
and the handheld photo — for two different reasons that both looked like a working detector
returning an empty page. `test_neural_ocr.py` names that regression and covers all seven.
