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

---

## Amendment — routing is conditional on measured page quality

*Added after the benchmark was actually run. The original decision above stands; this
replaces the "neural engine takes over for all images" half of it.*

**Context.** The first version routed every image to GOT-OCR2 on the assumption it beat
Tesseract on scans. `python -m eval.ocr_bench` over the image fixtures (n=7) says otherwise:

| variant | tesseract med recall | GOT-OCR2 med recall |
|---|---:|---:|
| prescription / clean scan | **1.00** | **0.75** |
| discharge / clean scan | 1.00 | 1.00 |
| lab / clean scan | 1.00 | 1.00 |
| prescription / degraded | 0.50 *(dose 0.50)* | **1.00** *(dose 1.00)* |
| lab / degraded | inv 0.57 | **inv 0.71** |
| discharge / degraded | 0.50 | 0.50 |

Every gain is on degraded input; on a clean prescription scan GOT-OCR2 *loses a quarter of
the medications*. A blanket replacement would have shipped a regression.

Worse, GOT-OCR2 reported **0.87–1.00 confidence on every fixture** and sent **0%** to the
verification lane — including `discharge/degraded`, where it recovered half the medications
and Tesseract had sent 67% to a human. EVALUATION.md names Tesseract's confidence collapse as
a *feature*: "it stays roughly as accurate and becomes less confident, and the low-confidence
lane converts that into human review instead of into a wrong dosage." Adopting the model's
own score would have destroyed that.

**Decision.** Images get a fast Tesseract pass first. A page whose mean confidence is at or
above `ocr_low_confidence_threshold` keeps that result. A page below it is re-read by
GOT-OCR2, **and every block from the re-read is capped at the page's measured confidence**
(`_bounded_by_page_quality`). `QualityRoutedOCR` holds both halves.

The signal is Tesseract's own mean page confidence: already computed, one cheap pass, and it
separated the classes with no overlap — clean at 0.84 / 0.90 / 0.90 / 0.91, degraded at
0.10 / 0.34 / 0.50. The cut is 0.72, reusing `ocr_low_confidence_threshold` rather than
inventing a constant: it already means "this reading is not trustworthy alone", and it sits
inside the measured 0.34-wide gap. Moving that setting moves this decision too, which is
intended — it is the same judgement about the same evidence.

The cap is evidence combination, not a penalty. There are two independent measurements of one
reading — how sure the model was of its tokens, and how legible the page was — and a claim
that the reading is *correct* cannot exceed the weaker. So the record gets GOT-OCR2's better
text at Tesseract's honest confidence, and the human review that confidence triggers.

**Alternatives.** *Trust GOT-OCR2's own score* — 0% human review at 0.50 recall on
`discharge/degraded`, a measured safety regression. *Flag every degraded-branch read
wholesale* — nearly what the cap achieves in practice, but it discards a real signal for no
gain. *Resolution or skew as the quality signal* — both already computed, and both miss:
`prescription_photo_handheld.jpg` is 3024×4276 and well-lit, and Tesseract reads it at 0.90.

**Consequences.** A degraded upload now costs a Tesseract pass *plus* ~90s/page of GOT-OCR2
(measured on MPS, ~9s/line). Clean uploads are unchanged and pay nothing.

Because the cap is the page mean and the page mean is by construction below the threshold,
**a degraded page now sends essentially all of its entities to review** — 100% where Tesseract
alone sent 67% on `discharge/degraded`. That is more conservative than before, in the
direction the system already errs.

n=7 image fixtures is a thin sample. The separation is wide and clean, but it is one run over
three document families; the threshold deserves re-checking against more degraded captures
before anyone treats 0.72 as load-bearing.

GOT-OCR2 also zeroed diagnosis recall on two fixtures, traced to it emitting full-width CJK
punctuation (`，`, `（）`) that `entities.py`'s regex does not match. Unfixed, and out of scope
here — it is an argument for normalising the model's output before extraction, not against
the routing.

---

**Consequences (original decision).** One model call per detected line, capped by
`NEURAL_OCR_MAX_LINES`. That is the cost of not fudging either value, and it is the main thing
to revisit if throughput ever matters more than it does at a kiosk serving one patient at a
time.

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
