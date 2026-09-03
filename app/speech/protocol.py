"""The speech boundary — ASR in, TTS out, three interchangeable backends.

The design decision that matters here is what happens when ASR is *unsure*. The tempting
behaviour is to take the best hypothesis and carry on. We refuse: below
`ASR_CONFIDENCE_THRESHOLD` the question **degrades to touch** and is re-presented with its
option buttons. A wrong answer recorded confidently is worse than an answer taken by tap, and
in a noisy OPD corridor the ASR will be unsure often.

Degradation is per-question, never sticky. A patient who is misheard once is still offered the
microphone on the next question — a kiosk that silently gives up on speech after one bad turn
has failed the person it was built for.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.core.config import settings


@dataclass(frozen=True, slots=True)
class Transcript:
    text: str
    #: The engine's own score, or None when it did not give one. NEVER substituted — a
    #: confidence nobody measured, attached to a clinical fact, is fabricated provenance and
    #: is indistinguishable downstream from a measured one.
    confidence: float | None
    language: str
    backend: str
    duration_ms: int = 0
    #: Word-level confidences where the backend provides them. Used to show the physician
    #: which words in a quote were uncertain.
    word_confidences: tuple[tuple[str, float], ...] = ()
    #: True when the backend heard nothing usable at all, as opposed to hearing it badly.
    empty: bool = False

    # ---- who actually produced this transcript -------------------------------------
    #
    # ⛔ THESE THREE EXIST SO A FALLBACK CAN NEVER BE REPORTED AS WHISPER.
    #
    # `backend` alone was ambiguous the moment more than one thing could produce a
    # transcript: "whisper" says which adapter ran but not who executed the model, and a
    # browser-generated transcript arriving through the same contract would be
    # indistinguishable in a log or a demo. Three fields, because there are three genuinely
    # different questions:
    #
    #   provider        WHO RAN IT       groq | browser | local | bhashini
    #   model           WHAT IT IS       openai/whisper-large-v3-turbo   (logical identity)
    #   provider_model  WHAT WAS SENT    whisper-large-v3-turbo          (the API's own id)
    #
    #: The distinction between the last two is not pedantry: Groq hosts OpenAI's weights
    #: under its own shorter identifier, and collapsing them would make it impossible to tell
    #: a hosted run of the required model from a local one, or from a different Whisper size.
    provider: str | None = None
    model: str | None = None
    provider_model: str | None = None

    #: How the graph executed, where that is a separate question from who hosted it.
    #: ONNX Runtime is a runtime, not a provider, and flattening the two would make
    #: "local/onnxruntime" and "local/vosk" indistinguishable.
    runtime: str | None = None

    # ---- routing honesty ------------------------------------------------------------
    #
    # ⛔ A FALLBACK MUST BE VISIBLE AS A FALLBACK. When a Hindi turn is routed to
    # IndicConformer and the worker is down, Whisper answers instead — which is the right
    # behaviour, and which must never be reported as IndicConformer having run. The fields
    # above already name the engine that PRODUCED the words; these two say what was ASKED
    # for, so the difference is legible rather than inferred.
    requested_backend: str | None = None
    fallback_used: bool = False

    @property
    def measured(self) -> bool:
        return self.confidence is not None

    @property
    def confidence_status(self) -> str:
        return "measured" if self.measured else "unavailable"

    @property
    def reliable(self) -> bool:
        """Confidently good. An UNMEASURED transcript is not reliable and not unreliable —
        it is unknown, and `unknown` is a different decision. Callers must branch on
        `measured` before trusting this."""
        if self.empty or self.confidence is None:
            return False
        return self.confidence >= settings.asr_confidence_threshold

    def to_dict(self) -> dict[str, object]:
        return {
            "text": self.text,
            "confidence": round(self.confidence, 4) if self.confidence is not None else None,
            "confidenceStatus": self.confidence_status,
            "language": self.language,
            "backend": self.backend,
            "durationMs": self.duration_ms,
            "reliable": self.reliable,
            "empty": self.empty,
            "threshold": settings.asr_confidence_threshold,
            # On the wire so a demo audience — and a developer reading a response — can see
            # which engine actually produced these words, rather than trusting a label.
            "provider": self.provider,
            "model": self.model,
            "providerModel": self.provider_model,
            "runtime": self.runtime,
            "requestedBackend": self.requested_backend,
            "fallbackUsed": self.fallback_used,
        }


@dataclass(frozen=True, slots=True)
class Utterance:
    """Synthesised speech. `audio` is WAV bytes; `text` is what was spoken, for the audit log."""

    audio: bytes
    media_type: str
    text: str
    language: str
    backend: str
    #: True when the backend could not synthesise and the client must use its own TTS.
    client_fallback: bool = False


class SpeechBackend(Protocol):
    name: str
    offline: bool
    languages: tuple[str, ...]

    def transcribe(self, audio: bytes, *, language: str, media_type: str) -> Transcript: ...

    def synthesise(self, text: str, *, language: str) -> Utterance: ...
