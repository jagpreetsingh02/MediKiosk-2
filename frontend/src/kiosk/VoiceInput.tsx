/**
 * Speech input. On-device recognition, server-side confidence policy.
 *
 * ⛔ THE CONFIDENCE RULE IS THE WHOLE POINT OF THIS FILE.
 *
 * `app/speech/protocol.py` is emphatic: a confidence nobody measured, attached to a clinical
 * fact, is fabricated provenance and is indistinguishable downstream from a measured one. So
 * this component posts `confidence: null` whenever the browser did not actually give a score,
 * and NEVER substitutes a plausible number.
 *
 * That matters because of a specific browser behaviour: Chrome sets
 * `SpeechRecognitionAlternative.confidence` to exactly `0` when its engine returns no score,
 * which is common for Indic locales. Zero is not "the engine was certain this is wrong" — it
 * is "there is no score here". Passing it through as a measured 0 would degrade every Hindi
 * answer to touch and would put a fabricated number on the fact; treating it as unmeasured is
 * the honest reading, and the backend already has a branch for unmeasured.
 *
 * Everything after that is the server's decision, not this component's. `answerVoice` posts
 * the transcript and the backend applies `ASR_CONFIDENCE_THRESHOLD` (0.62), records the fact
 * or degrades that question to touch, and returns a `VoiceOutcome` saying which happened. The
 * kiosk renders the outcome; it does not second-guess the threshold or pre-filter on it.
 *
 * Web Speech is used rather than uploading audio because it works with the network unplugged,
 * which is the scenario a venue demo has to survive. `app/speech/client.py` is the backend
 * half of that arrangement.
 */

import { useEffect, useRef, useState } from 'react';

import { Button } from '@/design/ui/Surface';
import type { VoiceOutcome } from '@/lib/api';

type Phase = 'idle' | 'listening' | 'processing' | 'unsupported';

/** Minimal shape of the Web Speech API. Typed here because TS's DOM lib omits it. */
interface SpeechRecognitionLike extends EventTarget {
  lang: string;
  interimResults: boolean;
  maxAlternatives: number;
  continuous: boolean;
  start(): void;
  stop(): void;
  abort(): void;
  onresult: ((event: any) => void) | null;
  onerror: ((event: any) => void) | null;
  onend: (() => void) | null;
}

function recognitionCtor(): (new () => SpeechRecognitionLike) | null {
  const w = window as unknown as Record<string, unknown>;
  return (w.SpeechRecognition ?? w.webkitSpeechRecognition) as
    | (new () => SpeechRecognitionLike)
    | null;
}

export interface VoiceInputProps {
  language: string;
  disabled?: boolean;
  /** Posts to the backend, which owns the threshold and returns what it decided. */
  onTranscript: (text: string, confidence: number | null) => Promise<void>;
  /** The backend's verdict on the previous attempt, rendered honestly. */
  outcome?: VoiceOutcome | null;
}

export function VoiceInput({ language, disabled, onTranscript, outcome }: VoiceInputProps) {
  const [phase, setPhase] = useState<Phase>('idle');
  const [interim, setInterim] = useState('');
  const recognition = useRef<SpeechRecognitionLike | null>(null);

  useEffect(() => {
    if (!recognitionCtor()) setPhase('unsupported');
    return () => recognition.current?.abort();
  }, []);

  function listen() {
    const Ctor = recognitionCtor();
    if (!Ctor) {
      setPhase('unsupported');
      return;
    }
    const engine = new Ctor();
    // The kiosk's language, so recognition is not silently done in English for a Hindi speaker.
    engine.lang = language === 'en' ? 'en-IN' : `${language}-IN`;
    engine.interimResults = true;
    engine.maxAlternatives = 1;
    engine.continuous = false;

    engine.onresult = (event: any) => {
      const result = event.results[event.results.length - 1];
      const alternative = result[0];
      if (!result.isFinal) {
        setInterim(String(alternative.transcript ?? ''));
        return;
      }
      const text = String(alternative.transcript ?? '').trim();
      const raw = alternative.confidence;
      // See the header. A missing score and a zero score are the same thing in Chrome, and
      // neither is a measurement, so both become null.
      const confidence =
        typeof raw === 'number' && Number.isFinite(raw) && raw > 0 ? raw : null;
      setInterim('');
      setPhase('processing');
      void onTranscript(text, confidence).finally(() => setPhase('idle'));
    };
    engine.onerror = () => {
      setInterim('');
      setPhase('idle');
    };
    engine.onend = () => {
      setPhase((p) => (p === 'listening' ? 'idle' : p));
    };

    recognition.current = engine;
    setPhase('listening');
    engine.start();
  }

  if (phase === 'unsupported') {
    return (
      <p className="text-sm" style={{ color: 'var(--mk-ink-muted)' }}>
        This device cannot listen. Please type or tap your answer below — nothing is lost.
      </p>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <Button
          variant={phase === 'listening' ? 'danger' : 'quiet'}
          disabled={disabled || phase === 'processing'}
          onClick={() => (phase === 'listening' ? recognition.current?.stop() : listen())}
        >
          <span
            className={phase === 'listening' ? 'h-2.5 w-2.5 animate-pulse rounded-full' : 'h-2.5 w-2.5 rounded-full'}
            style={{
              backgroundColor: phase === 'listening' ? 'var(--mk-danger)' : 'var(--mk-ink-subtle)',
            }}
            aria-hidden="true"
          />
          {phase === 'listening' ? 'Listening — tap to stop' : 'Answer by speaking'}
        </Button>

        {phase === 'processing' ? (
          <span className="text-sm" style={{ color: 'var(--mk-ink-muted)' }}>
            Checking what we heard…
          </span>
        ) : null}
      </div>

      {interim ? (
        <p className="text-sm italic" style={{ color: 'var(--mk-ink-subtle)' }}>
          “{interim}”
        </p>
      ) : null}

      {outcome ? <VoiceVerdict outcome={outcome} /> : null}
    </div>
  );
}

/**
 * What the BACKEND decided about the last transcript. Four distinguishable states, because
 * "we didn't hear you", "we heard you but weren't sure", "we have no score for this" and
 * "recorded" call for four different responses from the patient.
 */
function VoiceVerdict({ outcome }: { outcome: VoiceOutcome }) {
  const { transcript, accepted, degradedToTouch, reason } = outcome;
  const unmeasured = transcript.confidenceStatus === 'unavailable';

  const tone = accepted ? 'var(--mk-status-ok-bg)' : 'var(--mk-status-warn-bg)';
  const ink = accepted ? 'var(--mk-status-ok-fg)' : 'var(--mk-status-warn-fg)';

  return (
    <div className="rounded-lg px-3 py-2.5 text-sm" style={{ backgroundColor: tone, color: ink }}>
      {transcript.text ? (
        <p>
          We heard: <strong>“{transcript.text}”</strong>
        </p>
      ) : null}

      {accepted ? (
        <p className="mt-1">Recorded in your own words.</p>
      ) : degradedToTouch ? (
        <p className="mt-1">
          {reason === 'silence'
            ? "We didn't hear anything. Please tap your answer below."
            : "We didn't catch that clearly enough to record it. Please tap your answer below."}
        </p>
      ) : null}

      <p className="mt-1 text-xs opacity-90">
        {unmeasured
          ? 'This browser gave no confidence score for that, so none was recorded — a number nobody measured is not attached to a clinical fact.'
          : `Confidence ${(transcript.confidence ?? 0).toFixed(2)} against a threshold of ${transcript.threshold}.`}
      </p>
    </div>
  );
}

export default VoiceInput;
