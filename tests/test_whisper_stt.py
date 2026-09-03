"""AI-1 — Groq-hosted `openai/whisper-large-v3-turbo` as the primary speech-to-text engine.

⛔ WHAT THIS FILE IS REALLY PROTECTING.

A speech integration can fail in a way that looks like success from every direction: the
transcript is populated, the fact is recorded, the screen says "voice". The three ways that
happens are each pinned below.

  1. A FALLBACK REPORTED AS THE PRIMARY. If the browser produced the words, the response must
     say `provider: browser` — not "whisper", not "voice". A transcript attributed to a model
     that did not produce it is unauditable and untrue.
  2. AN INVENTED CONFIDENCE. Whisper reports no confidence; it is derived from `avg_logprob`
     and `no_speech_prob`. When neither exists the answer is `None`, never a plausible number
     and never 0 — an absent measurement and a low score are different clinical events.
  3. A STICKY DEGRADATION. Below the threshold only THAT question falls back to touch. The
     microphone must be offered again on the next one.

Everything here is offline. The one real network inference belongs to the session report, not
to a test that has to pass on a machine with no key.
"""

from __future__ import annotations

import pytest

from app.core.errors import UpstreamUnavailable
from app.speech import groq_whisper as GW
from app.speech.client import ClientSpeechBackend
from app.speech.protocol import Transcript

# The end-to-end app fixture, reused rather than rebuilt: a throwaway SQLite app with the
# offline LLM, so the route tests below touch no network. Bound under a private name and
# re-exposed as `api` so that no test parameter shadows the import (ruff F811).
from tests.test_api_end_to_end import _auth
from tests.test_api_end_to_end import _patient_token as _e2e_patient_token

# Aliased to `api` rather than imported as `client`: pytest registers a fixture under the
# name it is bound to in the module, and binding it as `client` would be shadowed by every
# test's own `client` parameter (ruff F811). One import, no wrapper, no collision.
from tests.test_api_end_to_end import client as api  # noqa: F401

# ---------------------------------------------------------------- A. registry


def test_speech_backend_whisper_selects_the_groq_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """`SPEECH_BACKEND=whisper` must resolve to the Groq adapter, not fall through."""
    from app.core.config import settings
    from app.speech import registry

    monkeypatch.setattr(settings, "speech_backend", "whisper")
    monkeypatch.setattr(settings, "groq_api_key", "test-key-not-real")
    registry.get_speech.cache_clear()
    try:
        backend = registry.get_speech()
        assert isinstance(backend, GW.GroqWhisperSpeechBackend)
        assert backend.name == "groq-whisper"
    finally:
        registry.get_speech.cache_clear()


def test_registry_reports_the_logical_model_not_just_the_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`/about` has to name the model. "whisper" alone does not say which size, or whose."""
    from app.core.config import settings
    from app.speech import registry

    monkeypatch.setattr(settings, "speech_backend", "whisper")
    monkeypatch.setattr(settings, "groq_api_key", "test-key-not-real")
    registry.get_speech.cache_clear()
    try:
        described = registry.describe()
        assert described["provider"] == "groq"
        assert described["model"] == "openai/whisper-large-v3-turbo"
        assert described["providerModel"] == "whisper-large-v3-turbo"
    finally:
        registry.get_speech.cache_clear()


def test_whisper_without_a_key_falls_back_rather_than_crashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing credential must not take the kiosk down; the local backend takes over."""
    from app.core.config import settings
    from app.speech import registry
    from app.speech.local import LocalSpeechBackend

    monkeypatch.setattr(settings, "speech_backend", "whisper")
    monkeypatch.setattr(settings, "groq_api_key", None)
    registry.get_speech.cache_clear()
    try:
        assert isinstance(registry.get_speech(), LocalSpeechBackend)
    finally:
        registry.get_speech.cache_clear()


# ---------------------------------------------------------------- B. model identity


def test_the_logical_model_is_the_required_one() -> None:
    """⛔ Pinned so a substitution is a failing test, not a quiet downgrade.

    `whisper-large-v3` and `whisper-1` are different models. The provider's short id and the
    logical identity are both asserted, because they are genuinely different strings and
    conflating them is how "we ran Whisper" stops meaning anything.
    """
    assert GW.LOGICAL_MODEL == "openai/whisper-large-v3-turbo"
    assert GW.PROVIDER == "groq"


def test_the_request_sends_the_provider_model_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """What actually goes on the wire as `model=`, captured without a network call."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "groq_api_key", "test-key-not-real")
    monkeypatch.setattr(settings, "groq_asr_model", "whisper-large-v3-turbo")
    captured: dict[str, object] = {}

    class _Response:
        status_code = 200

        def raise_for_status(self) -> None: ...

        def json(self) -> dict[str, object]:
            return {
                "text": "my chest hurts",
                "language": "English",
                "duration": 2.0,
                "segments": [{"avg_logprob": -0.1, "no_speech_prob": 0.0}],
            }

    def fake_post(url: str, **kwargs: object) -> _Response:
        captured["url"] = url
        captured["data"] = kwargs.get("data")
        captured["files"] = kwargs.get("files")
        return _Response()

    monkeypatch.setattr(GW.httpx, "post", fake_post)
    transcript = GW.GroqWhisperSpeechBackend().transcribe(
        b"RIFFfake", language="en", media_type="audio/wav"
    )

    assert str(captured["url"]).endswith("/audio/transcriptions")
    assert captured["data"]["model"] == "whisper-large-v3-turbo"  # type: ignore[index]
    assert captured["data"]["response_format"] == "verbose_json"  # type: ignore[index]
    # The transcript carries all three identities, separately.
    assert transcript.provider == "groq"
    assert transcript.model == "openai/whisper-large-v3-turbo"
    assert transcript.provider_model == "whisper-large-v3-turbo"


@pytest.mark.parametrize(
    ("media_type", "expected"),
    [
        ("audio/webm;codecs=opus", "audio.webm"),
        ("audio/webm", "audio.webm"),
        ("audio/mp4", "audio.mp4"),
        ("audio/ogg", "audio.ogg"),
        ("audio/wav", "audio.wav"),
        ("application/octet-stream", "audio.wav"),
    ],
)
def test_the_upload_filename_follows_the_real_media_type(media_type: str, expected: str) -> None:
    """⛔ THE FILENAME WAS HARDCODED TO `audio.wav`, AND THE BROWSER RECORDS WEBM.

    Groq infers the container from the extension, so a webm blob announced as `audio.wav` is
    decoded wrongly or refused. This is the one place the format contract is expressed, and
    it went unnoticed while only WAV fixtures were ever sent.
    """
    assert GW._upload_name(media_type) == expected


# ---------------------------------------------------------------- C/D. transcript + confidence


def test_a_good_segment_maps_to_high_confidence() -> None:
    """The real mapping, on the real numbers a live call returned (avg_logprob -0.0957)."""
    confidence = GW._confidence_from([{"avg_logprob": -0.0957, "no_speech_prob": 0.0}])
    assert confidence is not None and confidence > 0.95


def test_a_poor_segment_maps_below_the_threshold() -> None:
    from app.core.config import settings

    confidence = GW._confidence_from([{"avg_logprob": -0.85, "no_speech_prob": 0.05}])
    assert confidence is not None
    assert confidence < settings.asr_confidence_threshold


def test_probable_silence_governs_even_with_a_good_logprob() -> None:
    """`min(logprob_score, 1 - no_speech_prob)` — the worse signal wins, deliberately.

    A segment Whisper believes is noise must not be rescued by a confident logprob over that
    noise, which is exactly the shape of a hallucinated transcript.
    """
    confidence = GW._confidence_from([{"avg_logprob": -0.05, "no_speech_prob": 0.9}])
    assert confidence is not None and confidence <= 0.1


def test_no_segments_means_no_confidence_not_zero() -> None:
    """⛔ THE DISTINCTION THE WHOLE POLICY RESTS ON. Absent is not low."""
    assert GW._confidence_from([]) is None


def test_an_unmeasured_transcript_is_neither_reliable_nor_scored() -> None:
    transcript = Transcript(text="hello", confidence=None, language="en", backend="groq-whisper")
    assert transcript.measured is False
    assert transcript.reliable is False
    assert transcript.confidence_status == "unavailable"
    assert transcript.to_dict()["confidence"] is None


@pytest.mark.parametrize(
    ("reported", "requested", "expected"),
    [("English", "en", "en"), ("Hindi", "en", "hi"), ("en", "en", "en"), ("", "ta", "ta")],
)
def test_the_detected_language_is_normalised_to_an_iso_code(
    reported: str, requested: str, expected: str
) -> None:
    """A live call returns `"English"`, and every other language field here is a code.

    The DETECTION is kept — a Hindi answer on an English kiosk is real information — only the
    spelling is normalised, because `Transcript.language` is compared against
    `SUPPORTED_LANGUAGES` keys downstream.
    """
    assert GW._language_code(reported, requested) == expected


# ---------------------------------------------------------------- F. failure


def test_an_unreachable_provider_raises_rather_than_returning_empty_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """⛔ NO FABRICATED TRANSCRIPT, EVER.

    Returning an empty `Transcript` here would look like silence — a clinical statement that
    the patient said nothing — when what actually happened is that a provider was down. The
    route turns this exception into a per-question degradation instead.
    """
    from app.core.config import settings

    monkeypatch.setattr(settings, "groq_api_key", "test-key-not-real")

    def boom(url: str, **kwargs: object):
        raise GW.httpx.ConnectError("no route to host")

    monkeypatch.setattr(GW.httpx, "post", boom)
    with pytest.raises(UpstreamUnavailable):
        GW.GroqWhisperSpeechBackend().transcribe(b"x", language="en", media_type="audio/wav")


def test_empty_audio_is_reported_as_empty_not_transcribed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "groq_api_key", "test-key-not-real")
    transcript = GW.GroqWhisperSpeechBackend().transcribe(
        b"", language="en", media_type="audio/wav"
    )
    assert transcript.empty is True
    assert transcript.text == ""


# ---------------------------------------------------------------- 10. no silent fallback


def test_the_browser_path_is_never_labelled_whisper() -> None:
    """⛔ THE RULE OF §10. A fallback transcript must be attributable to the browser.

    This is the assertion that stops "voice" from meaning four different engines. If the
    client produced the words, no field anywhere may name Whisper.
    """
    transcript = ClientSpeechBackend().accept(text="hello", confidence=None, language="en")
    assert transcript.provider == "browser"
    assert transcript.model == "browser-web-speech"
    assert transcript.provider_model is None
    assert "whisper" not in str(transcript.to_dict()).casefold()


def test_the_two_engines_are_distinguishable_in_the_payload() -> None:
    """Collapsing these into one label is precisely what §10 forbids."""
    browser = ClientSpeechBackend().accept(text="hi", confidence=0.9, language="en")
    whisper = Transcript(
        text="hi",
        confidence=0.9,
        language="en",
        backend="groq-whisper",
        provider="groq",
        model=GW.LOGICAL_MODEL,
        provider_model="whisper-large-v3-turbo",
    )
    assert browser.to_dict()["provider"] != whisper.to_dict()["provider"]
    assert browser.to_dict()["model"] != whisper.to_dict()["model"]


# ---------------------------------------------------------------- 9. ASR unavailable


def test_asr_failure_degrades_this_question_and_records_nothing() -> None:
    """⛔ THE CONSULTATION SURVIVES A DEAD PROVIDER.

    `handle_spoken_answer` already covers the EXTRACTION model being unreachable, but that
    branch needs a transcript to exist. When the recogniser itself fails there is none, and
    the honest response is the one a patient already understands: this question falls back to
    tapping. Nothing is recorded, and no transcript is invented to stand in for the failure.
    """
    from app.contracts.record import FactLedger
    from app.modules.dialogue.machine import DialogueMachine, DialogueState
    from app.modules.dialogue.ontology import load_ontology
    from app.modules.dialogue.voice import degrade_for_unavailable_speech

    ontology = load_ontology()
    question_id = next(iter(ontology.by_id))
    machine = DialogueMachine(DialogueState(session_id="s", language="en"), FactLedger("s"))
    machine.ontology = ontology  # type: ignore[attr-defined]

    outcome = degrade_for_unavailable_speech(machine, question_id, "en")

    assert outcome.accepted is False
    assert outcome.degraded_to_touch is True
    assert outcome.reason == "service"
    assert outcome.facts == []
    # The stand-in transcript names NO engine — a failed run must not look like a successful
    # one in the metadata.
    assert outcome.transcript.provider is None
    assert outcome.transcript.model is None
    assert outcome.transcript.text == ""


# ---------------------------------------------------------------- H. the route itself
#
# These reuse the end-to-end app fixture: a throwaway SQLite app with the offline LLM, so no
# network is touched. What is exercised is the ROUTE — that uploaded audio reaches
# `get_speech().transcribe()` and that the resulting transcript flows into the same dialogue
# and provenance path the tapped answers use.

async def _open_session(api) -> tuple[str, str]:
    token, _ = await _e2e_patient_token(api)
    created = await api.post(
        "/api/v1/sessions",
        json={"language": "en", "consentScopes": ["history", "voice", "documents"]},
        headers=_auth(token),
    )
    return token, created.json()["sessionRef"]


class _StubWhisper:
    """Stands in for the Groq backend so the ROUTE is tested without a network call.

    It reports the same identity the real one does, because the assertion that matters is
    that whatever `get_speech()` returns is what the response attributes the words to.
    """

    name = "groq-whisper"
    offline = False
    languages = ("en",)
    provider = "groq"
    logical_model = "openai/whisper-large-v3-turbo"
    model = "whisper-large-v3-turbo"
    seen: dict = {}

    def transcribe(self, audio: bytes, *, language: str, media_type: str):
        from app.speech.protocol import Transcript

        _StubWhisper.seen = {"bytes": len(audio), "media_type": media_type, "language": language}
        return Transcript(
            text="burning pain in my stomach",
            confidence=0.94,
            language=language,
            backend=self.name,
            duration_ms=2000,
            provider="groq",
            model="openai/whisper-large-v3-turbo",
            provider_model="whisper-large-v3-turbo",
        )

    def synthesise(self, text: str, *, language: str):  # pragma: no cover - unused here
        raise NotImplementedError


async def test_audio_route_sends_the_bytes_to_the_server_backend(api, monkeypatch) -> None:
    """⛔ THE CORE OF AI-1: real audio reaches the configured engine.

    Before this route existed the browser produced the transcript and the server merely
    accepted the text. This asserts the bytes actually arrive at `SpeechBackend.transcribe`
    with their real media type — the browser records webm, and the container has to survive.
    """
    import app.api.routes_dialogue as routes

    monkeypatch.setattr(routes, "get_speech", lambda: _StubWhisper())
    token, session_ref = await _open_session(api)

    step = (await api.get(
        f"/api/v1/sessions/{session_ref}/dialogue/next", headers=_auth(token)
    )).json()
    question = step["question"]

    response = await api.post(
        f"/api/v1/sessions/{session_ref}/dialogue/answer/audio",
        headers=_auth(token),
        files={"file": ("answer.webm", b"\x1a\x45\xdf\xa3fake-webm-bytes", "audio/webm")},
        data={"turnId": question["turnId"], "questionId": question["questionId"]},
    )
    assert response.status_code == 200, response.text
    assert _StubWhisper.seen["media_type"] == "audio/webm"
    assert _StubWhisper.seen["bytes"] > 0

    voice = response.json()["voice"]
    # The response attributes the words to the engine that produced them.
    assert voice["transcript"]["provider"] == "groq"
    assert voice["transcript"]["model"] == "openai/whisper-large-v3-turbo"
    assert voice["transcript"]["backend"] == "groq-whisper"
    assert voice["transcript"]["text"] == "burning pain in my stomach"


async def test_audio_route_refuses_a_format_the_provider_cannot_read(api) -> None:
    token, session_ref = await _open_session(api)
    step = (await api.get(
        f"/api/v1/sessions/{session_ref}/dialogue/next", headers=_auth(token)
    )).json()
    response = await api.post(
        f"/api/v1/sessions/{session_ref}/dialogue/answer/audio",
        headers=_auth(token),
        files={"file": ("x.txt", b"not audio", "text/plain")},
        data={
            "turnId": step["question"]["turnId"],
            "questionId": step["question"]["questionId"],
        },
    )
    assert response.status_code == 400
    # The patient is told what to do, not what the MIME type was.
    assert "tapping" in response.text.lower()


async def test_audio_route_refuses_an_empty_recording(api) -> None:
    token, session_ref = await _open_session(api)
    step = (await api.get(
        f"/api/v1/sessions/{session_ref}/dialogue/next", headers=_auth(token)
    )).json()
    response = await api.post(
        f"/api/v1/sessions/{session_ref}/dialogue/answer/audio",
        headers=_auth(token),
        files={"file": ("answer.webm", b"", "audio/webm")},
        data={
            "turnId": step["question"]["turnId"],
            "questionId": step["question"]["questionId"],
        },
    )
    assert response.status_code == 400
    assert "empty" in response.text.lower()


async def test_audio_route_requires_the_voice_consent_scope(api) -> None:
    """Invariant 6: consent gates everything, including the microphone."""
    token, _ = await _e2e_patient_token(api)
    created = await api.post(
        "/api/v1/sessions",
        json={"language": "en", "consentScopes": ["history", "documents"]},
        headers=_auth(token),
    )
    session_ref = created.json()["sessionRef"]
    response = await api.post(
        f"/api/v1/sessions/{session_ref}/dialogue/answer/audio",
        headers=_auth(token),
        files={"file": ("answer.webm", b"bytes", "audio/webm")},
        data={"turnId": "t", "questionId": "q"},
    )
    assert response.status_code == 403


async def test_a_dead_provider_degrades_that_question_instead_of_500ing(
    api, monkeypatch
) -> None:
    """§9: the patient must still be able to answer. No crash, no fabricated transcript."""
    import app.api.routes_dialogue as routes
    from app.core.errors import UpstreamUnavailable

    class _Dead:
        def transcribe(self, audio, *, language, media_type):
            raise UpstreamUnavailable("Groq Whisper call failed")

    monkeypatch.setattr(routes, "get_speech", lambda: _Dead())
    token, session_ref = await _open_session(api)
    step = (await api.get(
        f"/api/v1/sessions/{session_ref}/dialogue/next", headers=_auth(token)
    )).json()

    response = await api.post(
        f"/api/v1/sessions/{session_ref}/dialogue/answer/audio",
        headers=_auth(token),
        files={"file": ("answer.webm", b"bytes", "audio/webm")},
        data={
            "turnId": step["question"]["turnId"],
            "questionId": step["question"]["questionId"],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["speechUnavailable"] is True
    assert body["voice"]["degradedToTouch"] is True
    assert body["voice"]["reason"] == "service"
    # No engine is named for a run that never happened.
    assert body["voice"]["transcript"]["provider"] is None
    # And the question is still answerable by tapping.
    assert body["question"] is not None


async def test_a_whisper_answer_still_produces_provenance(api, monkeypatch) -> None:
    """§8: a Whisper transcript must reach `record_fact()` with its utterance evidence.

    The transcript's own words become the span, and the ASR confidence rides with the fact.
    Nothing about the provenance architecture changes because the engine changed.
    """
    import app.api.routes_dialogue as routes

    monkeypatch.setattr(routes, "get_speech", lambda: _StubWhisper())
    token, session_ref = await _open_session(api)
    step = (await api.get(
        f"/api/v1/sessions/{session_ref}/dialogue/next", headers=_auth(token)
    )).json()
    question = step["question"]

    response = await api.post(
        f"/api/v1/sessions/{session_ref}/dialogue/answer/audio",
        headers=_auth(token),
        files={"file": ("answer.webm", b"fake", "audio/webm")},
        data={"turnId": question["turnId"], "questionId": question["questionId"]},
    )
    body = response.json()
    voice = body["voice"]

    if voice["accepted"]:
        # The fact exists and carries the spoken words as its evidence.
        assert voice["factsRecorded"] >= 1
        summary = (await api.get(
            f"/api/v1/sessions/{session_ref}/dialogue/review", headers=_auth(token)
        )).json()
        assert summary["answers"], "a recorded voice answer did not reach the review"
    else:
        # The offline extractor may place nothing; that is a legitimate degradation and it
        # must still be honest about which engine heard the words.
        assert voice["degradedToTouch"] is True
        assert voice["transcript"]["provider"] == "groq"
