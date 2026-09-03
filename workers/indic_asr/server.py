"""The IndicConformer ASR worker — `ai4bharat/indic-conformer-600m-multilingual`.

⛔ THIS RUNS IN ITS OWN PROCESS AND ITS OWN VIRTUALENV, AND THAT IS THE POINT.

The model needs `torch`, `torchaudio`, `onnxruntime` and `transformers` with
`trust_remote_code` — roughly 2 GB installed, on top of ~2.6 GB of weights held resident.
Putting that inside the FastAPI process that runs the clinical product would mean one OOM
away from losing the consultation, on a machine with 16 GB of unified memory. So the
application talks to this over HTTP and knows nothing about ONNX.

── What actually executes ────────────────────────────────────────────────────────────────
The model card's own inference API, unchanged:

    model = AutoModel.from_pretrained(REPO, trust_remote_code=True)
    transcript = model(wav, "hi", "ctc")

`model_onnx.IndicASRModel` loads a TorchScript preprocessor plus 28 ONNX graphs — a shared
encoder, a CTC decoder, an RNNT decoder, the joint network, and one `joint_post_net_<lang>`
per language. Language selection is EXPLICIT: `language_masks.json` selects the vocabulary
slice for the requested language. There is no detection, and there is no English head.

── Why CTC rather than RNNT ──────────────────────────────────────────────────────────────
Both are supported by the model and exposed here. CTC is the default because this is live,
single-utterance kiosk transcription: it is one forward pass with a greedy argmax, so it is
deterministic and its latency is a function of audio length alone. RNNT decodes
autoregressively, emitting up to `RNNT_MAX_SYMBOLS = 10` symbols per frame.

MEASURED on this machine, 5 warm runs over a 2.26 s Hindi utterance: CTC median 58 ms,
RNNT median 74 ms, and both produced byte-identical text. So CTC is the default on evidence
rather than on the reasoning above — which is also why `decoding` stays a request field.

── Confidence ────────────────────────────────────────────────────────────────────────────
⛔ THIS MODEL RETURNS A STRING AND NOTHING ELSE. Read `_ctc_decode` in the model's own code:
it computes `logprobs`, takes `argmax`, builds the hypothesis, and then `del logprobs`. The
scores are discarded before the caller ever sees them.

So this worker reports `confidence: null`. It does NOT re-implement the decoder to recover a
score, because the instruction was to use the repository's own decoding path rather than
invent one — and it does NOT borrow Whisper's `avg_logprob` mapping, which describes a
different model's different quantity. An honest "unmeasured" is worth more than a number
with no provenance.
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
import time
import wave
from typing import Annotated, Any

import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile

REPO_ID = "ai4bharat/indic-conformer-600m-multilingual"

#: The 22 languages the model ships a `joint_post_net_<lang>.onnx` head for. Taken from the
#: model's own component list, not from the README prose — the heads are what actually
#: constrain what can be decoded. English is deliberately absent: there is no `_en` head, so
#: this worker refuses English rather than producing something.
SUPPORTED = (
    "as", "bn", "brx", "doi", "gu", "hi", "kn", "kok", "ks", "mai", "ml",
    "mni", "mr", "ne", "or", "pa", "sa", "sat", "sd", "ta", "te", "ur",
)

#: The model's preprocessor expects 16 kHz mono. Stated in the model card and enforced here.
TARGET_SAMPLE_RATE = 16_000

app = FastAPI(title="MediKiosk IndicConformer worker")

_state: dict[str, Any] = {"model": None, "load_ms": None, "providers": None, "error": None}


def _load() -> Any:
    """Load once, lazily, and keep it. ~2.6 GB — never per request.

    Lazy rather than at import so the process starts (and answers `/health`) immediately,
    and so a model problem surfaces as an unready worker rather than a boot crash.
    """
    if _state["model"] is not None:
        return _state["model"]
    if _state["error"] is not None:
        raise RuntimeError(_state["error"])

    started = time.perf_counter()
    try:
        import onnxruntime as ort
        from transformers import AutoModel

        # `trust_remote_code` is required: the repository ships its own `model_onnx.py`,
        # which is the officially documented way to run this model.
        model = AutoModel.from_pretrained(
            REPO_ID,
            trust_remote_code=True,
            token=os.environ.get("HF_TOKEN") or None,
        )
        _state["model"] = model
        _state["load_ms"] = int((time.perf_counter() - started) * 1000)
        # Report the provider the model's own sessions actually chose, rather than what is
        # merely available on the machine.
        session = model.models.get("encoder")
        _state["providers"] = (
            list(session.get_providers())
            if session is not None
            else list(ort.get_available_providers())
        )
        return model
    except Exception as exc:  # noqa: BLE001 — surfaced through /health and /transcribe
        _state["error"] = f"{type(exc).__name__}: {exc}"
        raise


def _to_wav(raw: bytes, media_type: str) -> bytes:
    """Normalise any browser container to 16 kHz mono PCM WAV, with ffmpeg.

    ⛔ THE BROWSER DOES NOT RECORD WAV. Chromium's MediaRecorder produces webm/opus and
    Safari mp4/aac; this model's preprocessor wants a raw 16 kHz mono waveform. Something has
    to bridge that, and ffmpeg is already a dependency of this machine (8.1.2 on PATH) — so
    the alternative would be adding an audio-decoding library to do worse what ffmpeg
    already does correctly.

    A WAV input is passed straight through: `_decode_wav` handles it, and re-encoding audio
    that is already in the target format would only add a lossy generation for nothing.
    """
    base = (media_type or "").split(";", 1)[0].strip().casefold()
    if base in ("audio/wav", "audio/x-wav", "audio/wave", ""):
        return raw
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise ValueError(
            f"cannot decode {base!r}: ffmpeg is not installed, and only WAV is readable "
            "without it"
        )
    with tempfile.TemporaryDirectory() as tmp:
        source = os.path.join(tmp, "in")
        target = os.path.join(tmp, "out.wav")
        with open(source, "wb") as handle:
            handle.write(raw)
        # -ac 1 mono, -ar 16000 the model's rate, -f wav signed 16-bit PCM.
        completed = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", source,
             "-ac", "1", "-ar", str(TARGET_SAMPLE_RATE), "-c:a", "pcm_s16le", "-f", "wav", target],
            capture_output=True,
            timeout=60,
            check=False,
        )
        if completed.returncode != 0 or not os.path.exists(target):
            raise ValueError(
                f"ffmpeg could not decode {base!r}: "
                f"{completed.stderr.decode('utf-8', 'replace')[:200]}"
            )
        with open(target, "rb") as handle:
            return handle.read()


def _decode_wav(raw: bytes) -> tuple[np.ndarray, int]:
    """PCM WAV bytes to a float32 mono waveform in [-1, 1], plus its sample rate.

    Deliberately stdlib-only. `torchaudio.load` would also work, but the main application
    already normalises uploads to WAV before they reach here, and a decoder that accepts one
    well-specified format is easier to reason about than one that guesses.
    """
    with wave.open(io.BytesIO(raw), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())

    if width != 2:
        raise ValueError(f"expected 16-bit PCM, got {width * 8}-bit")
    samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        # Mono by averaging, matching the model card's `torch.mean(wav, dim=0, keepdim=True)`.
        samples = samples.reshape(-1, channels).mean(axis=1)
    return samples, rate


def _resample(samples: np.ndarray, source_rate: int) -> np.ndarray:
    """Linear resample to 16 kHz.

    ⛔ THE MODEL'S PREPROCESSOR ASSUMES 16 kHz AND DOES NOT CHECK. Feeding it 22.05 kHz audio
    does not error — it produces a transcript of the wrong thing, because every mel frame is
    computed over the wrong time window. The fixtures in this repository are 16 kHz and
    22.05 kHz, so this path is exercised rather than theoretical.

    Linear interpolation rather than a windowed-sinc filter: the alternative is `torchaudio`'s
    resampler, which is better but pulls the decision into a second place. Speech at these
    rates survives linear interpolation well, and `test_resampling_preserves_a_tone` pins that
    the result is still the signal it started as.
    """
    if source_rate == TARGET_SAMPLE_RATE:
        return samples
    duration = samples.shape[0] / source_rate
    target_length = int(round(duration * TARGET_SAMPLE_RATE))
    source_positions = np.linspace(0.0, samples.shape[0] - 1, num=samples.shape[0])
    target_positions = np.linspace(0.0, samples.shape[0] - 1, num=target_length)
    return np.interp(target_positions, source_positions, samples).astype(np.float32)


@app.get("/health")
async def health() -> dict[str, Any]:
    """Readiness, and an honest statement of what is loaded.

    `ready` means the model is IN MEMORY and can answer now — not that the process is up.
    The application's backend uses this to decide whether to route Indic audio here or fall
    back to Whisper, so a hopeful answer would produce a patient-visible failure.
    """
    return {
        "ready": _state["model"] is not None,
        "model": REPO_ID,
        "runtime": "onnxruntime",
        "providers": _state["providers"],
        "device": "cpu",
        "supportedLanguages": list(SUPPORTED),
        "targetSampleRate": TARGET_SAMPLE_RATE,
        "modelLoadMs": _state["load_ms"],
        "error": _state["error"],
    }


@app.post("/warmup")
async def warmup() -> dict[str, Any]:
    """Load the model without transcribing, so the first patient does not pay for it."""
    _load()
    return await health()


@app.post("/transcribe")
async def transcribe(
    file: Annotated[UploadFile, File()],
    language: Annotated[str, Form()],
    decoding: Annotated[str, Form()] = "ctc",
) -> dict[str, Any]:
    """Transcribe one utterance in one EXPLICITLY NAMED Indic language.

    There is no detection here and there must not be. The model selects a per-language
    vocabulary head by name; asked for a language it has no head for, the honest answer is a
    refusal, not a transcript produced by the wrong head.
    """
    lang = (language or "").strip().casefold()
    if lang not in SUPPORTED:
        # 422, not 500: the caller asked for something this model cannot do, and the
        # application's backend turns this into a Whisper fallback.
        raise HTTPException(
            status_code=422,
            detail=f"{lang!r} is not one of this model's languages: {', '.join(SUPPORTED)}",
        )
    if decoding not in ("ctc", "rnnt"):
        raise HTTPException(status_code=422, detail="decoding must be 'ctc' or 'rnnt'")

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=422, detail="empty audio")

    try:
        samples, source_rate = _decode_wav(_to_wav(raw, file.content_type or ""))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"unreadable audio: {exc}") from exc

    resampled = _resample(samples, source_rate)
    duration_ms = int(1000 * resampled.shape[0] / TARGET_SAMPLE_RATE)

    try:
        model = _load()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"model unavailable: {exc}") from exc

    import torch

    wav = torch.from_numpy(resampled).unsqueeze(0)  # (1, samples), mono, float32

    started = time.perf_counter()
    with torch.inference_mode():
        hypothesis = model(wav, lang, decoding)
    inference_ms = int((time.perf_counter() - started) * 1000)

    text = (hypothesis if isinstance(hypothesis, str) else str(hypothesis)).strip()
    return {
        "text": text,
        "language": lang,
        "model": REPO_ID,
        "runtime": "onnxruntime",
        "providers": _state["providers"],
        "decoding": decoding,
        # ⛔ NULL, DELIBERATELY. The model's decode path discards its log-probabilities before
        # returning; there is no score to report and none is invented. See the header.
        "confidence": None,
        "confidenceStatus": "unmeasured",
        "durationMs": duration_ms,
        "inferenceMs": inference_ms,
        "sourceSampleRate": source_rate,
        "resampled": source_rate != TARGET_SAMPLE_RATE,
        "empty": not text,
    }
