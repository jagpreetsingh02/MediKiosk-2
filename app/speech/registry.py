"""Speech backend selection, and the language routing added in AI-2.

⛔ TWO ENGINES, ONE EXPLICIT DECISION, MADE HERE AND NOWHERE ELSE.

    English, anything unspecified, anything not an Indic head   ->  Whisper  (AI-1, primary)
    An explicitly selected Indic language, worker ready         ->  IndicConformer

`transcribe_routed` is the only place that chooses. Routes call it; they do not decide.
Putting the choice in a route would mean the next route that needs speech re-derives it, and
the two would eventually disagree about which engine handled which language.

⛔ THERE IS NO LANGUAGE DETECTION, AND THERE MUST NOT BE. IndicConformer selects a
per-language decoding head BY NAME — it cannot detect, and it has no English head at all.
The language comes from the session, which the patient chose. Guessing from the audio would
mean guessing before the ASR that would tell us has run, and guessing from the transcript
afterwards is too late: the wrong head has already decoded it.
"""

from __future__ import annotations

from functools import lru_cache

from app.core.config import settings
from app.core.errors import UpstreamUnavailable
from app.core.logging import get_logger
from app.speech.client import ClientSpeechBackend
from app.speech.indic_conformer import LOGICAL_MODEL as INDIC_MODEL
from app.speech.indic_conformer import IndicConformerSpeechBackend
from app.speech.indic_conformer import supports as indic_supports
from app.speech.local import LocalSpeechBackend
from app.speech.protocol import SpeechBackend, Transcript

log = get_logger(__name__)


@lru_cache(maxsize=1)
def get_speech() -> SpeechBackend:
    if settings.speech_backend == "whisper":
        from app.speech.groq_whisper import GroqWhisperSpeechBackend

        try:
            return GroqWhisperSpeechBackend()
        except Exception as exc:
            log.warning("speech.whisper_unavailable", error=str(exc)[:200])
            return LocalSpeechBackend()
    if settings.speech_backend == "bhashini":
        from app.speech.bhashini import BhashiniSpeechBackend

        try:
            return BhashiniSpeechBackend()
        except Exception as exc:
            log.warning("speech.bhashini_unavailable", error=str(exc)[:200])
            return LocalSpeechBackend()
    if settings.speech_backend == "client":
        return ClientSpeechBackend()
    return LocalSpeechBackend()


@lru_cache(maxsize=1)
def get_client_backend() -> ClientSpeechBackend:
    """Always available: the kiosk posts on-device transcripts through this path."""
    return ClientSpeechBackend()


def get_indic() -> IndicConformerSpeechBackend:
    """The IndicConformer adapter. Cheap to construct — it holds no model, only a URL."""
    return IndicConformerSpeechBackend()


def route_for(language: str) -> tuple[str, str | None]:
    """Which engine SHOULD handle `language`, and why not, when the answer is Whisper.

    Pure and side-effect free so the decision is testable without a worker, a network or a
    key. Returns the backend name rather than an instance because the caller may never need
    to build it — an English turn must not construct an Indic client at all.
    """
    lang = (language or "").strip().casefold()
    if not settings.indic_asr_enabled:
        return "whisper", "INDIC_ASR_ENABLED is false"
    if not lang:
        # No explicit language means no explicit language. There is nothing to detect from
        # yet, and picking a head on a hunch is how a Tamil answer gets decoded as Hindi.
        return "whisper", "no language was specified"
    if not indic_supports(lang):
        # Includes English, which this model has no head for at all.
        return "whisper", f"{lang!r} is not one of IndicConformer's languages"
    return "indic-conformer", None


def transcribe_routed(audio: bytes, *, language: str, media_type: str) -> Transcript:
    """Transcribe with the right engine for `language`, falling back visibly.

    ⛔ THE FALLBACK IS EXPLICIT AND CANNOT MASQUERADE. When an Indic turn cannot reach the
    worker, Whisper answers — which is the right thing for the patient in front of the kiosk —
    and the transcript that comes back still names Whisper as its `provider`/`model`, with
    `requested_backend="indic-conformer"` and `fallback_used=True` beside it. Labelling a
    Whisper result as IndicConformer would make the record of which model produced a clinical
    fact untrue, which is the one thing this whole arrangement exists to prevent.

    Readiness is checked BEFORE the audio is sent, so the common failure (worker not running)
    costs one 2-second health probe rather than a full upload and timeout.
    """
    from dataclasses import replace

    chosen, why_not = route_for(language)

    if chosen == "whisper":
        if why_not:
            log.info("speech.routed", language=language, to="whisper", because=why_not)
        return replace(
            get_speech().transcribe(audio, language=language, media_type=media_type),
            requested_backend="whisper",
            fallback_used=False,
        )

    indic = get_indic()
    ready, reason = indic.ready()
    if ready:
        try:
            log.info("speech.routed", language=language, to="indic-conformer")
            return replace(
                indic.transcribe(audio, language=language, media_type=media_type),
                requested_backend="indic-conformer",
                fallback_used=False,
            )
        except UpstreamUnavailable as exc:
            reason = f"transcription failed: {str(exc)[:120]}"

    # The worker is down, still loading, or failed mid-request. Whisper covers the turn.
    log.warning("speech.indic_fallback", language=language, to="whisper", because=reason)
    return replace(
        get_speech().transcribe(audio, language=language, media_type=media_type),
        requested_backend="indic-conformer",
        fallback_used=True,
    )


def describe() -> dict[str, object]:
    """What `/about` reports. Names the model, not just the adapter.

    `name` is the adapter that is wired in; `model` and `provider` are what will actually
    execute. A demo audience needs the second pair — "whisper" alone does not say whether
    that is a hosted `whisper-large-v3-turbo` or a local one, and does not say which size.
    Reported as `null` for backends that carry no model identity (the browser path).
    """
    backend = get_speech()
    return {
        "name": backend.name,
        "offline": backend.offline,
        "languages": list(backend.languages),
        "configured": settings.speech_backend,
        # Present only on backends that name a model. `getattr` rather than a protocol
        # member because `SpeechBackend` describes recognition, not provenance metadata.
        "provider": getattr(backend, "provider", None),
        "model": getattr(backend, "logical_model", None),
        "providerModel": getattr(backend, "model", None),
        "runtime": getattr(backend, "runtime", None),
        # The second engine, and whether it can actually answer right now. Reported so a
        # demo audience can see that Indic routing is live rather than merely configured.
        "indic": {
            "enabled": settings.indic_asr_enabled,
            "model": INDIC_MODEL,
            "languages": list(IndicConformerSpeechBackend.languages),
            "decoding": settings.indic_asr_decoding,
            "ready": get_indic().ready()[0] if settings.indic_asr_enabled else False,
        },
        "asrConfidenceThreshold": settings.asr_confidence_threshold,
        "degradationPolicy": (
            "Below the threshold the question falls back to touch and is re-presented. "
            "Degradation is per-question and never sticky."
        ),
    }
