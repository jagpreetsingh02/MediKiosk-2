"""AI-2 — `ai4bharat/indic-conformer-600m-multilingual` as the Indian-language ASR backend.

⛔ THE THREE THINGS THIS FILE EXISTS TO STOP.

  1. ENGLISH REACHING A MODEL THAT HAS NO ENGLISH HEAD. IndicConformer ships one
     `joint_post_net_<lang>.onnx` per language and there is no `_en`. Decoding English
     through the Hindi head does not error — it returns Devanagari nonsense, confidently.
     English must route to Whisper, always, and the model itself must refuse if ever asked.
  2. A WHISPER FALLBACK LABELLED AS INDICCONFORMER. When the worker is down Whisper covers
     the turn, which is right for the patient — and the transcript must still say Whisper
     produced it, with `requestedBackend`/`fallbackUsed` beside it saying what was asked for.
  3. AN INVENTED CONFIDENCE. This model returns a string and nothing else: its own
     `_ctc_decode` computes log-probabilities, argmaxes them, and `del`s them. `None` is the
     truthful answer and Whisper's `avg_logprob` mapping must not be borrowed for it.

Everything here is offline. The real worker inference belongs to the session report.
"""

from __future__ import annotations

import io
import wave

import numpy as np
import pytest

from app.core.config import settings
from app.core.errors import UpstreamUnavailable
from app.speech import indic_conformer as IC
from app.speech import registry
from app.speech.protocol import Transcript


@pytest.fixture(autouse=True)
def _clear_backend_cache():
    # Order-independent on purpose. Tests here monkeypatch `registry.get_speech` with a plain
    # function, and there is no guaranteed ordering between monkeypatch's undo and this
    # teardown — so by the time we run, the name may or may not still be the lru_cache'd
    # original. Asking for `cache_clear` rather than assuming it keeps this fixture from
    # erroring out and masking the result of the test it was meant to isolate.
    _clear = lambda: getattr(registry.get_speech, "cache_clear", lambda: None)()  # noqa: E731
    _clear()
    yield
    _clear()


# ---------------------------------------------------------------- A. language mapping


@pytest.mark.parametrize(
    ("language", "code"),
    [("Hindi", "hi"), ("Punjabi", "pa"), ("Tamil", "ta"), ("Telugu", "te"), ("Bengali", "bn")],
)
def test_the_five_named_languages_have_heads(language: str, code: str) -> None:
    """The mapping the brief names explicitly, asserted against the model's own head list."""
    assert IC.supports(code), f"{language} ({code}) has no head in {IC.LOGICAL_MODEL}"


def test_all_twenty_two_scheduled_languages_are_declared() -> None:
    """The full set, not just Hindi. The worker architecture supports every head the model has."""
    assert len(IC.SUPPORTED_LANGUAGES) == 22
    for code in ("as", "bn", "brx", "doi", "gu", "hi", "kn", "kok", "ks", "mai", "ml"):
        assert code in IC.SUPPORTED_LANGUAGES


# ---------------------------------------------------------------- B. English exclusion


def test_english_is_not_a_supported_language() -> None:
    """⛔ BY CONSTRUCTION, NOT BY POLICY. There is no `joint_post_net_en.onnx` in the repo."""
    assert not IC.supports("en")
    assert "en" not in IC.SUPPORTED_LANGUAGES


def test_english_routes_to_whisper_even_with_indic_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "indic_asr_enabled", True)
    backend, why = registry.route_for("en")
    assert backend == "whisper"
    assert why and "not one of" in why


def test_the_backend_itself_refuses_english_rather_than_attempting_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Belt and braces: even if routing were bypassed, the adapter will not send English."""
    monkeypatch.setattr(settings, "indic_asr_enabled", True)
    with pytest.raises(UpstreamUnavailable, match="no head"):
        IC.IndicConformerSpeechBackend().transcribe(
            b"audio", language="en", media_type="audio/wav"
        )


# ---------------------------------------------------------------- C. defaults


@pytest.mark.parametrize("language", ["", "en", "fr", "de"])
def test_unspecified_or_unsupported_language_goes_to_whisper(
    language: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "indic_asr_enabled", True)
    assert registry.route_for(language)[0] == "whisper"


def test_indic_disabled_means_everything_goes_to_whisper(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default. AI-1's voice path is unchanged until the worker is deliberately run."""
    monkeypatch.setattr(settings, "indic_asr_enabled", False)
    for language in ("hi", "pa", "ta", "te", "bn", "en", ""):
        assert registry.route_for(language)[0] == "whisper"


@pytest.mark.parametrize("language", ["hi", "pa", "ta", "te", "bn", "mr", "gu", "ur"])
def test_an_indic_language_routes_to_indicconformer(
    language: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "indic_asr_enabled", True)
    backend, why = registry.route_for(language)
    assert backend == "indic-conformer"
    assert why is None


# ---------------------------------------------------------------- D. registration


def test_the_backend_satisfies_the_speech_protocol() -> None:
    """Structural conformance, checked member by member.

    `SpeechBackend` is a plain `Protocol`, not `@runtime_checkable`, so `isinstance` cannot
    answer this — and making it runtime-checkable to satisfy a test would change the
    protocol's semantics for everything else. The members ARE the contract, so they are what
    is asserted; `mypy` enforces the same thing statically at every call site.
    """
    backend = IC.IndicConformerSpeechBackend()
    for member in ("name", "offline", "languages", "transcribe", "synthesise"):
        assert hasattr(backend, member), f"missing {member}"
    assert callable(backend.transcribe) and callable(backend.synthesise)
    assert backend.name == "indic-conformer"
    assert backend.logical_model == "ai4bharat/indic-conformer-600m-multilingual"


def test_the_logical_model_is_the_required_one() -> None:
    """Pinned so a substitution is a failing test. Bhashini, Whisper and Vosk are not this."""
    assert IC.LOGICAL_MODEL == "ai4bharat/indic-conformer-600m-multilingual"
    assert IC.RUNTIME == "onnxruntime"


# ---------------------------------------------------------------- E/F/G. worker + fallback


class _Worker:
    """A stand-in for the worker's HTTP surface, so routing is testable without 2.6 GB."""

    def __init__(self, *, ready: bool = True, text: str = "नमस्ते", fail: bool = False):
        self._ready, self._text, self._fail = ready, text, fail

    def get(self, url: str, timeout: float = 0):  # noqa: ANN201
        return _Reply({"ready": self._ready, "model": IC.LOGICAL_MODEL, "error": None})

    def post(self, url: str, **kwargs):  # noqa: ANN201
        if self._fail:
            raise IC.httpx.ConnectError("worker went away")
        return _Reply(
            {
                "text": self._text,
                "language": kwargs["data"]["language"],
                "model": IC.LOGICAL_MODEL,
                "runtime": "onnxruntime",
                "decoding": kwargs["data"]["decoding"],
                "confidence": None,
                "confidenceStatus": "unmeasured",
                "durationMs": 2255,
                "inferenceMs": 58,
                "resampled": True,
                "empty": not self._text,
            }
        )


class _Reply:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None: ...

    def json(self) -> dict:
        return self._payload


def test_health_identifies_the_exact_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(IC.httpx, "get", _Worker().get)
    ready, reason = IC.IndicConformerSpeechBackend().ready()
    assert ready is True and reason is None


def test_a_worker_that_is_up_but_unloaded_is_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """⛔ LISTENING IS NOT READY. Routing audio to a process still loading 2.6 GB produces a
    timeout the patient sees, so readiness means the model is in memory."""
    monkeypatch.setattr(IC.httpx, "get", _Worker(ready=False).get)
    ready, reason = IC.IndicConformerSpeechBackend().ready()
    assert ready is False
    assert reason and "not loaded" in reason


def test_a_real_shaped_worker_reply_becomes_a_valid_transcript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _Worker(text="सुबह से मेरी छाती में दर्द हो रहा है")
    monkeypatch.setattr(IC.httpx, "post", worker.post)
    transcript = IC.IndicConformerSpeechBackend().transcribe(
        b"audio", language="hi", media_type="audio/wav"
    )
    assert transcript.text == "सुबह से मेरी छाती में दर्द हो रहा है"
    assert transcript.backend == "indic-conformer"
    assert transcript.provider == "local"
    assert transcript.model == IC.LOGICAL_MODEL
    assert transcript.runtime == "onnxruntime"
    assert transcript.language == "hi"
    assert transcript.duration_ms == 2255


def test_an_unavailable_worker_falls_back_to_whisper_visibly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """⛔ THE CENTRAL HONESTY PROPERTY OF AI-2.

    The patient still gets a transcript — but the record says Whisper produced it, and says
    that IndicConformer was what was asked for. A fallback that reports the primary engine
    makes the record of which model produced a clinical fact untrue.
    """
    monkeypatch.setattr(settings, "indic_asr_enabled", True)
    monkeypatch.setattr(IC.httpx, "get", _Worker(ready=False).get)

    class _Whisper:
        name, offline, languages = "groq-whisper", False, ("hi",)

        def transcribe(self, audio, *, language, media_type):
            return Transcript(
                text="whisper heard this",
                confidence=0.9,
                language=language,
                backend=self.name,
                provider="groq",
                model="openai/whisper-large-v3-turbo",
                provider_model="whisper-large-v3-turbo",
            )

        def synthesise(self, text, *, language): ...

    monkeypatch.setattr(registry, "get_speech", lambda: _Whisper())
    transcript = registry.transcribe_routed(b"audio", language="hi", media_type="audio/wav")

    assert transcript.fallback_used is True
    assert transcript.requested_backend == "indic-conformer"
    # The engine that ACTUALLY produced the words.
    assert transcript.backend == "groq-whisper"
    assert transcript.provider == "groq"
    assert transcript.model == "openai/whisper-large-v3-turbo"


def test_a_fallback_transcript_never_names_indicconformer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The assertion that stops the two engines being collapsed into "voice"."""
    monkeypatch.setattr(settings, "indic_asr_enabled", True)
    monkeypatch.setattr(IC.httpx, "get", _Worker(ready=False).get)

    class _Whisper:
        name, offline, languages = "groq-whisper", False, ("hi",)

        def transcribe(self, audio, *, language, media_type):
            return Transcript(
                text="x", confidence=0.9, language=language, backend=self.name,
                provider="groq", model="openai/whisper-large-v3-turbo",
            )

        def synthesise(self, text, *, language): ...

    monkeypatch.setattr(registry, "get_speech", lambda: _Whisper())
    payload = registry.transcribe_routed(b"a", language="hi", media_type="audio/wav").to_dict()

    assert payload["backend"] != "indic-conformer"
    assert payload["model"] != IC.LOGICAL_MODEL
    assert payload["fallbackUsed"] is True


def test_a_mid_request_worker_failure_also_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ready at the health check, gone by the time the audio arrives. Still not a 500."""
    monkeypatch.setattr(settings, "indic_asr_enabled", True)
    monkeypatch.setattr(IC.httpx, "get", _Worker(ready=True).get)
    monkeypatch.setattr(IC.httpx, "post", _Worker(fail=True).post)

    class _Whisper:
        name, offline, languages = "groq-whisper", False, ("hi",)

        def transcribe(self, audio, *, language, media_type):
            return Transcript(text="w", confidence=0.9, language=language, backend=self.name,
                              provider="groq", model="openai/whisper-large-v3-turbo")

        def synthesise(self, text, *, language): ...

    monkeypatch.setattr(registry, "get_speech", lambda: _Whisper())
    transcript = registry.transcribe_routed(b"a", language="hi", media_type="audio/wav")
    assert transcript.fallback_used is True
    assert transcript.backend == "groq-whisper"


def test_a_successful_indic_run_is_not_marked_as_a_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "indic_asr_enabled", True)
    worker = _Worker(text="नमस्ते")
    monkeypatch.setattr(IC.httpx, "get", worker.get)
    monkeypatch.setattr(IC.httpx, "post", worker.post)
    transcript = registry.transcribe_routed(b"a", language="hi", media_type="audio/wav")
    assert transcript.fallback_used is False
    assert transcript.requested_backend == "indic-conformer"
    assert transcript.backend == "indic-conformer"


def test_whisper_turns_are_marked_as_requesting_whisper(monkeypatch: pytest.MonkeyPatch) -> None:
    """An English turn is not a fallback — it is the correct primary route."""
    monkeypatch.setattr(settings, "indic_asr_enabled", True)

    class _Whisper:
        name, offline, languages = "groq-whisper", False, ("en",)

        def transcribe(self, audio, *, language, media_type):
            return Transcript(text="hello", confidence=1.0, language="en", backend=self.name,
                              provider="groq", model="openai/whisper-large-v3-turbo")

        def synthesise(self, text, *, language): ...

    monkeypatch.setattr(registry, "get_speech", lambda: _Whisper())
    transcript = registry.transcribe_routed(b"a", language="en", media_type="audio/wav")
    assert transcript.requested_backend == "whisper"
    assert transcript.fallback_used is False


# ---------------------------------------------------------------- I. confidence


def test_confidence_is_none_and_stays_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """⛔ THE MODEL RETURNS A STRING. `_ctc_decode` computes logprobs and then `del`s them.

    `None` is the honest answer. It is NOT converted to 0.0, which would read as "the system
    was certain this was wrong", and Whisper's `avg_logprob` mapping is not reused — that
    describes a different model's quantity.
    """
    monkeypatch.setattr(IC.httpx, "post", _Worker().post)
    transcript = IC.IndicConformerSpeechBackend().transcribe(
        b"a", language="hi", media_type="audio/wav"
    )
    assert transcript.confidence is None
    assert transcript.measured is False
    assert transcript.confidence_status == "unavailable"
    assert transcript.reliable is False
    assert transcript.to_dict()["confidence"] is None


def test_the_threshold_is_untouched_by_ai2() -> None:
    """AI-1's policy is unchanged; an unmeasured transcript simply never compares against it."""
    assert settings.asr_confidence_threshold == 0.62


# ---------------------------------------------------------------- K. audio preprocessing


def _wav(samples: np.ndarray, rate: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes((samples * 32767).astype(np.int16).tobytes())
    return buffer.getvalue()


def test_resampling_preserves_a_tone() -> None:
    """⛔ THE MODEL'S PREPROCESSOR ASSUMES 16 kHz AND DOES NOT CHECK.

    Feeding it 22.05 kHz does not error — every mel frame is computed over the wrong time
    window and it transcribes the wrong thing. The repository's own fixtures are 22.05 kHz,
    so this path runs on real audio. A 440 Hz tone resampled from 22.05 kHz must still be a
    440 Hz tone, and must have the right number of samples for its duration.
    """
    import sys

    sys.path.insert(0, "workers/indic_asr")
    try:
        from server import TARGET_SAMPLE_RATE, _decode_wav, _resample
    finally:
        sys.path.pop(0)

    source_rate, seconds, freq = 22050, 0.5, 440.0
    t = np.arange(int(source_rate * seconds)) / source_rate
    tone = np.sin(2 * np.pi * freq * t).astype(np.float32)

    samples, rate = _decode_wav(_wav(tone, source_rate))
    assert rate == source_rate

    resampled = _resample(samples, rate)
    assert len(resampled) == pytest.approx(TARGET_SAMPLE_RATE * seconds, abs=2)

    # The dominant frequency must survive, or the resampler is destroying the signal.
    spectrum = np.abs(np.fft.rfft(resampled))
    peak_hz = np.fft.rfftfreq(len(resampled), 1 / TARGET_SAMPLE_RATE)[int(np.argmax(spectrum))]
    assert peak_hz == pytest.approx(freq, abs=8.0)


def test_already_16k_audio_is_not_resampled() -> None:
    import sys

    sys.path.insert(0, "workers/indic_asr")
    try:
        from server import TARGET_SAMPLE_RATE, _resample
    finally:
        sys.path.pop(0)

    samples = np.zeros(TARGET_SAMPLE_RATE, dtype=np.float32)
    assert _resample(samples, TARGET_SAMPLE_RATE) is samples


def test_stereo_is_mixed_to_mono() -> None:
    """The model card averages channels; a stereo upload must not double the sample count."""
    import sys

    sys.path.insert(0, "workers/indic_asr")
    try:
        from server import _decode_wav
    finally:
        sys.path.pop(0)

    frames = np.zeros((1000, 2), dtype=np.int16)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(frames.tobytes())
    samples, rate = _decode_wav(buffer.getvalue())
    assert samples.shape == (1000,)
    assert rate == 16000
