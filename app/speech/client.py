"""The client-side backend: the kiosk device did the recognition, we only validate it.

This is what the shipped kiosk uses. The browser's Web Speech API runs on-device, and the
kiosk posts `{transcript, confidence}` with the answer. This backend's job is to apply the
same confidence policy to that transcript as to any other, so a client cannot get a
low-confidence answer accepted merely by producing it locally.
"""

from __future__ import annotations

from app.core.errors import ValidationError
from app.speech.protocol import Transcript, Utterance


class ClientSpeechBackend:
    """Satisfies `SpeechBackend`. `transcribe` is not meaningful; use `accept()`."""

    name = "client-webspeech"
    offline = True
    languages: tuple[str, ...] = ("en", "hi", "bn", "ta", "te", "mr", "kn", "ml", "gu", "pa")

    def transcribe(self, audio: bytes, *, language: str, media_type: str) -> Transcript:
        raise ValidationError(
            "The client backend does not accept audio. The kiosk transcribes on-device and "
            "posts the transcript with the answer."
        )

    def accept(
        self, *, text: str, confidence: float | None, language: str
    ) -> Transcript:
        """Wrap a client transcript. `None` means the browser gave no score, and stays None.

        Several browsers report no confidence at all for Indic locales. Substituting a value
        here would put a number nobody measured onto a clinical fact.
        """
        if confidence is not None and not 0.0 <= confidence <= 1.0:
            raise ValidationError(f"ASR confidence {confidence} is outside [0, 1].")
        stripped = text.strip()
        return Transcript(
            text=stripped,
            confidence=confidence,
            language=language,
            backend=self.name,
            empty=not stripped,
            # ⛔ NEVER "whisper". These words came out of the BROWSER's recogniser, and the
            # response says so. A fallback that reports the primary engine's name is worse
            # than no metadata at all: it makes an unverifiable transcript look verified.
            provider="browser",
            model="browser-web-speech",
            provider_model=None,
        )

    def synthesise(self, text: str, *, language: str) -> Utterance:
        return Utterance(
            audio=b"",
            media_type="audio/wav",
            text=text,
            language=language,
            backend=self.name,
            client_fallback=True,
        )
