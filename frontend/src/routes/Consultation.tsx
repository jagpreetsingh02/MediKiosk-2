/**
 * The intake itself: consent, then one question at a time, then papers, then a read-back.
 *
 * ⛔ CONSENT GATES EVERYTHING AND NOTHING IS CAPTURED BEFORE IT (Invariant 6). The session is
 * created by `POST /api/v1/sessions` with the granted scopes, and that call REFUSES with
 * `ConsentRequired` if a required scope is missing — so the gate is the backend's, not a
 * checkbox this screen enforces on its honour.
 *
 * ⛔ THE ASSISTANT DOES NOT DIAGNOSE. There is no field on this screen, and no endpoint behind
 * it, that returns an assessment, a differential or a probability. It collects, clarifies,
 * organises and summarises — and `assert_no_assessment()` scans every outbound payload to keep
 * it that way. The read-back at the end is what was said, grouped; it is never an opinion.
 *
 * ⛔ AND THE PATIENT DOES NOT SIGN OFF CLINICAL FACTS. They can review and correct their own
 * answers (`/dialogue/review` and `/dialogue/reopen`), because a patient correcting themselves
 * is the system working. What they cannot do is confirm anything into the durable record —
 * that is Invariant 4, it belongs to the physician, and it happens on the other surface.
 */

import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import {
  Button,
  DemoBand,
  Heading,
  Muted,
  Pane,
  Problem,
  Spinner,
  Surface,
} from '@/design/ui/Surface';
import { RedFlagBanner } from '@/physician/RedFlagBanner';
import DocumentUpload from '@/kiosk/DocumentUpload';
import QuestionCard from '@/kiosk/QuestionCard';
import VoiceInput from '@/kiosk/VoiceInput';
import {
  ApiError,
  api,
  type ConsentPresentation,
  type Escalation,
  type StepResponse,
  type VoiceOutcome,
} from '@/lib/api';
import { clearConsultation, getConsultation, setConsultation } from '@/lib/session';

type Stage = 'consent' | 'asking' | 'documents' | 'review';

export default function Consultation() {
  const navigate = useNavigate();

  const [stage, setStage] = useState<Stage>('consent');
  const [sessionRef, setSessionRef] = useState<string | null>(null);
  const [language] = useState('en');

  const [consent, setConsent] = useState<ConsentPresentation | null>(null);
  const [granted, setGranted] = useState<string[]>([]);

  const [step, setStep] = useState<StepResponse | null>(null);
  const [voice, setVoice] = useState<VoiceOutcome | null>(null);
  const [escalation, setEscalation] = useState<Escalation | null>(null);

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  // ---- resume an open consultation rather than stranding it ---------------------------
  useEffect(() => {
    const open = getConsultation();
    if (open) {
      setSessionRef(open.sessionRef);
      setStage('asking');
    }
  }, []);

  useEffect(() => {
    if (stage !== 'consent') return;
    api
      .consentPresentation(language)
      .then((presentation) => {
        setConsent(presentation);
        // Required scopes start granted — they are not optional, and presenting them as
        // unticked boxes implies a choice the flow does not actually offer.
        setGranted(presentation.scopes.filter((s) => s.required).map((s) => s.id));
      })
      .catch((cause) => setError(cause as ApiError));
  }, [stage, language]);

  const advance = useCallback((next: StepResponse) => {
    setStep(next);
    setVoice(next.voice ?? null);
    if (next.escalation) setEscalation(next.escalation);
    if (next.complete) setStage('documents');
  }, []);

  async function begin() {
    setBusy(true);
    setError(null);
    try {
      const created = await api.createSession(language, granted, false);
      setSessionRef(created.sessionRef);
      setConsultation({
        sessionRef: created.sessionRef,
        language,
        ayushMode: created.ayushMode,
      });
      setStage('asking');
      advance(await api.next(created.sessionRef));
    } catch (cause) {
      setError(cause as ApiError);
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (stage !== 'asking' || !sessionRef || step) return;
    api.next(sessionRef).then(advance).catch((cause) => setError(cause as ApiError));
  }, [stage, sessionRef, step, advance]);

  async function run(work: () => Promise<StepResponse>) {
    setBusy(true);
    setError(null);
    try {
      advance(await work());
    } catch (cause) {
      setError(cause as ApiError);
    } finally {
      setBusy(false);
    }
  }

  const question = step?.question ?? null;

  return (
    <Surface kind="kiosk">
      <DemoBand />
      <div className="mx-auto max-w-2xl px-6 py-10">
        {escalation && escalation.flags.length ? (
          // Rendered on the PATIENT surface too, without the clinical language. A rule that
          // fired must not be visible only to staff — but it is never framed as a diagnosis.
          <div className="mb-6">
            <RedFlagBanner flags={escalation.flags} />
          </div>
        ) : null}

        {error ? <Problem message={error.message} detail={error.detail} /> : null}

        {/* ---------------------------------------------------------------- consent */}
        {stage === 'consent' ? (
          <>
            <Heading level={1}>Before we start</Heading>
            {!consent ? (
              <Spinner label="Loading the consent notice…" />
            ) : (
              <Pane className="mt-6">
                <Muted>{consent.preamble}</Muted>
                <ul className="mt-5 space-y-3">
                  {consent.scopes.map((scope) => {
                    const on = granted.includes(scope.id);
                    return (
                      <li key={scope.id}>
                        <label className="flex cursor-pointer items-start gap-3">
                          <input
                            type="checkbox"
                            checked={on}
                            disabled={scope.required}
                            onChange={() =>
                              setGranted((prev) =>
                                prev.includes(scope.id)
                                  ? prev.filter((s) => s !== scope.id)
                                  : [...prev, scope.id],
                              )
                            }
                            className="mt-1 h-4 w-4"
                          />
                          <span>
                            <span
                              className="text-sm font-medium"
                              style={{ color: 'var(--mk-ink-strong)' }}
                            >
                              {scope.short ?? scope.title}
                              {scope.required ? ' · required' : ' · optional'}
                            </span>
                            <span
                              className="mt-0.5 block text-sm"
                              style={{ color: 'var(--mk-ink-muted)' }}
                            >
                              {scope.audio}
                            </span>
                          </span>
                        </label>
                      </li>
                    );
                  })}
                </ul>
                <div className="mt-6">
                  <Button variant="primary" onClick={begin} disabled={busy}>
                    {busy ? 'Starting…' : 'I agree — start'}
                  </Button>
                </div>
                <Muted className="mt-3">
                  You can withdraw at any point, and everything captured in this session is
                  deleted when it ends.
                </Muted>
              </Pane>
            )}
          </>
        ) : null}

        {/* ---------------------------------------------------------------- questions */}
        {stage === 'asking' && sessionRef ? (
          <>
            {step?.progress ? (
              <div className="mb-5">
                <div
                  className="h-1.5 w-full overflow-hidden rounded-full"
                  style={{ backgroundColor: 'var(--mk-line)' }}
                >
                  <div
                    className="h-full rounded-full transition-all"
                    style={{
                      width: `${step.progress.percent}%`,
                      backgroundColor: 'var(--mk-accent)',
                      transitionDuration: 'var(--mk-settle)',
                    }}
                  />
                </div>
                <p className="mt-2 text-xs" style={{ color: 'var(--mk-ink-subtle)' }}>
                  {step.progress.answered} of about {step.progress.askable} questions
                </p>
              </div>
            ) : null}

            {!question ? (
              <Spinner label="Preparing your next question…" />
            ) : (
              <QuestionCard
                question={question}
                busy={busy}
                canGoBack={Boolean(step?.canGoBack)}
                onAnswer={(value) =>
                  run(() =>
                    typeof value === 'string' && question.kind === 'open_text'
                      ? api.answerTyped(sessionRef, question.turnId, question.questionId, value)
                      : api.answer(sessionRef, question.turnId, question.questionId, value),
                  )
                }
                onSkip={() => run(() => api.skip(sessionRef, question.questionId))}
                onBack={() => run(() => api.back(sessionRef))}
                voiceSlot={
                  // `touchOnly` is the backend saying THIS question degraded. The microphone
                  // disappears for this turn only and returns on the next.
                  question.touchOnly ? (
                    <p className="text-sm" style={{ color: 'var(--mk-status-warn-fg)' }}>
                      Let's do this one by tapping.
                    </p>
                  ) : (
                    <VoiceInput
                      language={question.language}
                      disabled={busy}
                      outcome={voice}
                      // PRIMARY path: real recorded audio, transcribed server-side by
                      // Whisper. The browser no longer produces the transcript.
                      onAudio={async (audio) => {
                        await run(() =>
                          api.answerAudio(
                            sessionRef,
                            question.turnId,
                            question.questionId,
                            audio,
                            false,
                          ),
                        );
                      }}
                    />
                  )
                }
              />
            )}
          </>
        ) : null}

        {/* ---------------------------------------------------------------- documents */}
        {stage === 'documents' && sessionRef ? (
          <>
            <Heading level={1}>That's the questions done</Heading>
            <Muted className="mt-2">
              If you have brought any prescriptions or reports, add them now. Otherwise go
              straight on.
            </Muted>
            <div className="mt-6">
              <DocumentUpload sessionRef={sessionRef} />
            </div>
            <div className="mt-6 flex gap-3">
              <Button variant="primary" onClick={() => setStage('review')}>
                Continue
              </Button>
            </div>
          </>
        ) : null}

        {/* ---------------------------------------------------------------- read-back */}
        {stage === 'review' && sessionRef ? (
          <IntakeReadBack
            sessionRef={sessionRef}
            onDone={() => {
              clearConsultation();
              navigate('/patient');
            }}
          />
        ) : null}
      </div>
    </Surface>
  );
}

/**
 * What the patient told us, in their own words, grouped by section.
 *
 * Explicitly NOT the clinical summary. `/dialogue/review` returns the answers as given; the
 * physician's summary is assembled separately, gated by traceability, and confirmed by them.
 * Showing a patient an "assessment" here would be Invariant 1 broken on the friendliest
 * possible screen.
 */
function IntakeReadBack({
  sessionRef,
  onDone,
}: {
  sessionRef: string;
  onDone: () => void;
}) {
  const [answers, setAnswers] = useState<
    { questionId: string; sectionTitle: string; prompt: string; display: string }[] | null
  >(null);
  const [error, setError] = useState<ApiError | null>(null);

  useEffect(() => {
    api
      .review(sessionRef)
      .then((r) => setAnswers(r.answers as never))
      .catch((cause) => setError(cause as ApiError));
  }, [sessionRef]);

  return (
    <>
      <Heading level={1}>Here is what you told us</Heading>
      <Muted className="mt-2">
        Read it over. If something is wrong, tell the doctor — they go through every line with
        you before any of it becomes part of your record.
      </Muted>

      {error ? <Problem message={error.message} detail={error.detail} /> : null}
      {!answers ? <Spinner label="Gathering your answers…" /> : null}

      {answers ? (
        <ul className="mt-6 space-y-2">
          {answers.map((answer) => (
            <Pane as="li" key={answer.questionId}>
              <p className="text-xs uppercase tracking-wide" style={{ color: 'var(--mk-ink-subtle)' }}>
                {answer.sectionTitle}
              </p>
              <p className="mt-1 text-sm" style={{ color: 'var(--mk-ink-muted)' }}>
                {answer.prompt}
              </p>
              <p className="mt-1 font-medium" style={{ color: 'var(--mk-ink-strong)' }}>
                {answer.display}
              </p>
            </Pane>
          ))}
        </ul>
      ) : null}

      <Pane className="mt-8">
        <Heading level={2}>What happens next</Heading>
        <Muted className="mt-2">
          A doctor now reviews every line, confirms or corrects it, and only then does it join
          your permanent record. Nothing here has been added to your history yet.
        </Muted>
        <div className="mt-4">
          <Button variant="primary" onClick={onDone}>
            Done — I'm ready for the doctor
          </Button>
        </div>
      </Pane>
    </>
  );
}
