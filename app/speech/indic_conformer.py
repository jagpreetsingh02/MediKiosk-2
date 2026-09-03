"""IndicConformer — `ai4bharat/indic-conformer-600m-multilingual`, over HTTP to a worker.

⛔ THIS FILE CONTAINS NO INFERENCE. It is an HTTP client that satisfies `SpeechBackend`, and
that separation is deliberate: the model needs `torch`, `torchaudio` and `onnxruntime` —
about 2 GB installed, plus 2.6 GB of weights held resident — and none of that belongs in the
process serving the clinical product on a 16 GB machine. See `workers/indic_asr/`.

── What this model can and cannot do ─────────────────────────────────────────────────────
It has one `joint_post_net_<lang>.onnx` head per language and selects the decoding vocabulary
by NAME from `language_masks.json`. There is no detection, and there is **no English head**.
So this backend serves the 22 scheduled Indian languages and refuses everything else rather
than decoding English through a Hindi head and returning the result as if it meant something.

Whisper remains the primary engine for English and for anything unspecified — AI-1 is
untouched. See `registry.transcribe_routed` for where that decision is made.

── Confidence ────────────────────────────────────────────────────────────────────────────
⛔ `confidence` IS ALWAYS `None` HERE, AND THAT IS THE TRUTHFUL ANSWER.

The model's own `_ctc_decode` computes log-probabilities, takes an argmax, builds the
hypothesis string, and then `del logprobs`. Nothing is returned but text. Two tempting things
are therefore not done: the decoder is not re-implemented to recover a score (the model's own
decoding path is the one to use), and Whisper's `avg_logprob` mapping is not borrowed — that
maps a different model's quantity and reusing it would manufacture false precision.

`Transcript.confidence = None` flows into the existing unmeasured-confidence policy, which
already knows what to do: record with the confidence marked unavailable, and degrade to touch
on the questions where being wrong is dangerous. That behaviour predates this backend and is
unchanged by it.
"""

from __future__ import annotations

import httpx

from app.core.config import settings
from app.core.errors import UpstreamUnavailable
from app.core.logging import get_logger
from app.speech.protocol import Transcript, Utterance

log = get_logger(__name__)

#: The logical model identity. Pinned in code, like Whisper's — a statement about which
#: weights ran, not a deployment knob.
LOGICAL_MODEL = "ai4bharat/indic-conformer-600m-multilingual"

#: `local` because the weights execute on this machine, in the worker process. The runtime is
#: reported separately: ONNX Runtime is not a hosting provider, it is how the graph executes.
PROVIDER = "local"
RUNTIME = "onnxruntime"

#: The 22 languages with a per-language head in the model. English is absent BY CONSTRUCTION,
#: not by policy — there is no `joint_post_net_en.onnx` to decode it with.
SUPPORTED_LANGUAGES: tuple[str, ...] = (
    "as", "bn", "brx", "doi", "gu", "hi", "kn", "kok", "ks", "mai", "ml",
    "mni", "mr", "ne", "or", "pa", "sa", "sat", "sd", "ta", "te", "ur",
)


def supports(language: str) -> bool:
    """Whether this model has a head for `language`. The single source of that answer."""
    return (language or "").strip().casefold() in SUPPORTED_LANGUAGES


class IndicConformerSpeechBackend:
    """Satisfies `SpeechBackend`. Delegates to the worker; holds no model."""

    name = "indic-conformer"
    offline = True  # the weights are local, even though this process reaches them over HTTP
    languages: tuple[str, ...] = SUPPORTED_LANGUAGES

    def __init__(self) -> None:
        self.base_url = settings.indic_asr_url.rstrip("/")
        self.provider = PROVIDER
        self.logical_model = LOGICAL_MODEL
        self.model = LOGICAL_MODEL
        self.runtime = RUNTIME

    # -------------------------------------------------------------- readiness

    def ready(self) -> tuple[bool, str | None]:
        """Is the worker up AND the model loaded? Both, because either alone is misleading.

        A process that is listening but has not loaded 2.6 GB of weights cannot answer, and
        routing a patient's audio to it would produce a timeout rather than a transcript. The
        router asks this before choosing, so the fallback happens before the recording is
        sent rather than after it fails.
        """
        try:
            response = httpx.get(
                f"{self.base_url}/health", timeout=settings.indic_asr_health_timeout
            )
            response.raise_for_status()
            body = response.json()
        except Exception as exc:  # noqa: BLE001 — unreachable is a normal, expected state
            return False, f"{type(exc).__name__}: {str(exc)[:120]}"
        if not body.get("ready"):
            return False, body.get("error") or "worker is up but the model is not loaded yet"
        return True, None

    # -------------------------------------------------------------- ASR

    def transcribe(self, audio: bytes, *, language: str, media_type: str) -> Transcript:
        lang = (language or "").strip().casefold()
        if not supports(lang):
            # Refuse rather than attempt. Decoding English through a Hindi head produces
            # confident nonsense, which is worse than an honest refusal the router can act on.
            raise UpstreamUnavailable(
                f"{lang!r} has no head in {LOGICAL_MODEL}; this model covers "
                f"{', '.join(SUPPORTED_LANGUAGES)}."
            )
        if not audio:
            return Transcript(
                text="",
                confidence=None,
                language=lang,
                backend=self.name,
                empty=True,
                provider=PROVIDER,
                model=LOGICAL_MODEL,
                provider_model=LOGICAL_MODEL,
            )

        try:
            response = httpx.post(
                f"{self.base_url}/transcribe",
                files={"file": ("audio", audio, media_type or "audio/wav")},
                data={"language": lang, "decoding": settings.indic_asr_decoding},
                timeout=settings.indic_asr_timeout,
            )
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPError as exc:
            raise UpstreamUnavailable(f"IndicConformer worker call failed: {exc}") from exc

        text = str(body.get("text", "")).strip()
        log.info(
            "speech.indic_conformer",
            language=lang,
            decoding=body.get("decoding"),
            chars=len(text),
            inference_ms=body.get("inferenceMs"),
            resampled=body.get("resampled"),
        )
        return Transcript(
            text=text,
            # See the header: the model returns no score, and none is invented.
            confidence=None,
            language=str(body.get("language", lang))[:8],
            backend=self.name,
            duration_ms=int(body.get("durationMs") or 0),
            empty=not text,
            provider=PROVIDER,
            model=LOGICAL_MODEL,
            provider_model=LOGICAL_MODEL,
            runtime=RUNTIME,
        )

    # -------------------------------------------------------------- TTS

    def synthesise(self, text: str, *, language: str) -> Utterance:
        """This model is recognition only. Synthesis stays where it was — AI-2 changes no TTS."""
        return Utterance(
            audio=b"",
            media_type="audio/wav",
            text=text,
            language=language,
            backend=f"{self.name}:client-tts",
            client_fallback=True,
        )
