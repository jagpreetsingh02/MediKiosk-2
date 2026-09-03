/**
 * Speech input. The browser RECORDS; the server RECOGNISES.
 *
 * ⛔ THIS COMPONENT NO LONGER TRANSCRIBES. It used the Web Speech API, which produced a
 * transcript on-device and posted the text. That path had three problems that only matter
 * once the transcript becomes a clinical fact:
 *
 *   1. NO ATTRIBUTABLE MODEL. "The browser recognised it" cannot be audited. There is no
 *      model name, no version, and no way to reproduce the result from the audit trail.
 *   2. NO MEASURED CONFIDENCE on most engines — Chrome reports exactly 0 for Indic locales,
 *      which is an absent score, not a low one.
 *   3. NO AUDIO. The bytes were never kept, so nothing could be re-checked afterwards.
 *
 * Now `MediaRecorder` captures the audio and uploads it; `POST /dialogue/answer/audio`
 * transcribes it with the configured `SpeechBackend` (Groq-hosted
 * `openai/whisper-large-v3-turbo`) and returns a transcript that NAMES the engine.
 *
 * ⚠️ WEB SPEECH SURVIVES ONLY AS AN EXPLICIT FALLBACK, for a device that cannot record or
 * cannot reach the network. When it runs, the response says `provider: browser` — it is
 * never displayed as Whisper. Silent fallback is the thing this file is arranged to prevent.
 *
 * The confidence policy is unchanged and still the server's: below
 * `ASR_CONFIDENCE_THRESHOLD` (0.62) THAT question degrades to touch and is re-presented,
 * and the microphone returns on the next one. Nothing here pre-filters on the threshold.
 */

import { useEffect, useRef, useState } from 'react';

import { Button } from '@/design/ui/Surface';
import type { VoiceOutcome } from '@/lib/api';

type Phase = 'idle' | 'recording' | 'uploading' | 'transcribing' | 'unsupported';

/** Containers the server accepts, in the order a browser is likely to support them. */
const PREFERRED_TYPES = [
  'audio/webm;codecs=opus',
  'audio/webm',
  'audio/mp4',
  'audio/ogg;codecs=opus',
];

function pickMimeType(): string | undefined {
  if (typeof MediaRecorder === 'undefined') return undefined;
  return PREFERRED_TYPES.find((t) => MediaRecorder.isTypeSupported(t));
}

export interface VoiceInputProps {
  language: string;
  disabled?: boolean;
  /** Uploads the recorded audio. The server transcribes and applies the threshold. */
  onAudio: (audio: Blob) => Promise<void>;
  /** The backend's verdict on the previous attempt, rendered honestly. */
  outcome?: VoiceOutcome | null;
}

export function VoiceInput({ language, disabled, onAudio, outcome }: VoiceInputProps) {
  const [phase, setPhase] = useState<Phase>('idle');
  const [seconds, setSeconds] = useState(0);
  const [problem, setProblem] = useState<string | null>(null);

  const recorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const stream = useRef<MediaStream | null>(null);
  const ticker = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (typeof MediaRecorder === 'undefined' || !navigator.mediaDevices?.getUserMedia) {
      setPhase('unsupported');
    }
    return () => {
      if (ticker.current) clearInterval(ticker.current);
      // A microphone left open is a recording light left on. Always release the tracks.
      stream.current?.getTracks().forEach((t) => t.stop());
    };
  }, []);

  async function start() {
    setProblem(null);
    try {
      const media = await navigator.mediaDevices.getUserMedia({ audio: true });
      stream.current = media;
      chunks.current = [];

      const mimeType = pickMimeType();
      const rec = new MediaRecorder(media, mimeType ? { mimeType } : undefined);
      rec.ondataavailable = (e) => {
        if (e.data.size) chunks.current.push(e.data);
      };
      rec.onstop = async () => {
        media.getTracks().forEach((t) => t.stop());
        stream.current = null;
        const blob = new Blob(chunks.current, { type: mimeType ?? 'audio/webm' });
        if (!blob.size) {
          setPhase('idle');
          setProblem('Nothing was recorded. Please try again, or tap your answer.');
          return;
        }
        setPhase('uploading');
        try {
          setPhase('transcribing');
          await onAudio(blob);
        } catch {
          // The parent renders the API error; this component only returns to a usable state.
          setProblem('We could not send that recording. Please tap your answer.');
        } finally {
          setPhase('idle');
        }
      };

      recorder.current = rec;
      rec.start();
      setPhase('recording');
      setSeconds(0);
      ticker.current = setInterval(() => setSeconds((s) => s + 1), 1000);
    } catch {
      // Permission refused, no device, or an insecure origin. All are the same to a patient.
      setPhase('idle');
      setProblem('We cannot use the microphone. Please tap or type your answer instead.');
    }
  }

  function stop() {
    if (ticker.current) clearInterval(ticker.current);
    ticker.current = null;
    recorder.current?.stop();
  }

  if (phase === 'unsupported') {
    return (
      <p className="text-sm" style={{ color: 'var(--mk-ink-muted)' }}>
        This device cannot record audio. Please type or tap your answer below — nothing is lost.
      </p>
    );
  }

  const busy = phase === 'uploading' || phase === 'transcribing';

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <Button
          variant={phase === 'recording' ? 'danger' : 'quiet'}
          disabled={disabled || busy}
          onClick={() => (phase === 'recording' ? stop() : void start())}
        >
          <span
            className={
              phase === 'recording'
                ? 'h-2.5 w-2.5 animate-pulse rounded-full'
                : 'h-2.5 w-2.5 rounded-full'
            }
            style={{
              backgroundColor:
                phase === 'recording' ? 'var(--mk-danger)' : 'var(--mk-ink-subtle)',
            }}
            aria-hidden="true"
          />
          {phase === 'recording'
            ? `Recording ${seconds}s — tap to finish`
            : 'Answer by speaking'}
        </Button>

        {busy ? (
          <span className="text-sm" style={{ color: 'var(--mk-ink-muted)' }} role="status">
            {phase === 'uploading' ? 'Sending your answer…' : 'Listening back to what you said…'}
          </span>
        ) : null}

        <span className="text-xs" style={{ color: 'var(--mk-ink-subtle)' }}>
          {language.toUpperCase()}
        </span>
      </div>

      {problem ? (
        <p className="text-sm" style={{ color: 'var(--mk-status-warn-fg)' }}>
          {problem}
        </p>
      ) : null}

      {outcome ? <VoiceVerdict outcome={outcome} /> : null}
    </div>
  );
}

/**
 * What the BACKEND decided, and WHICH ENGINE decided it.
 *
 * The engine line is not decoration. If a fallback produced these words the patient's fact
 * is attributed to the browser, and that has to be visible rather than inferred.
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
            : reason === 'service'
              ? 'The listening service is unavailable just now. Please tap your answer below.'
              : "We didn't catch that clearly enough to record it. Please tap your answer below."}
        </p>
      ) : null}

      <p className="mt-1 text-xs opacity-90">
        {unmeasured
          ? 'No confidence score was measured for that, so none was recorded — a number nobody measured is not attached to a clinical fact.'
          : `Confidence ${(transcript.confidence ?? 0).toFixed(2)} against a threshold of ${transcript.threshold}.`}
        {transcript.model ? ` · ${transcript.model} via ${transcript.provider}` : null}
      </p>
    </div>
  );
}

export default VoiceInput;
