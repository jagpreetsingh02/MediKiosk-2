/**
 * One question, rendered by its kind. The kiosk asks one thing at a time, on purpose.
 *
 * ⛔ THE QUESTION IS NOT CHOSEN HERE, AND NOT BY A MODEL ANYWHERE. `modules/dialogue/machine.py`
 * walks `data/ontology/*.yaml` and evaluates each `ask_if` against facts already recorded, so
 * the interview a patient sees depends on their answers and is reproducible offline. This
 * component renders whatever the state machine hands it and posts the answer back. It never
 * decides what comes next, and it must never be given that job — a model that could skip the
 * allergy question is the failure that rule exists to prevent.
 *
 * `touchOnly` is honoured strictly. When ASR was unsure the backend degrades THAT question to
 * touch and re-presents it; the microphone is hidden for that turn and offered again on the
 * next one. Degradation is per-question and never sticky — a patient misheard once is still
 * offered speech, because a kiosk that silently gives up on voice after one bad turn has
 * failed the person it was built for.
 */

import { useEffect, useState } from 'react';

import { Button, Heading, Muted } from '@/design/ui/Surface';
import type { Question } from '@/lib/api';
import { cn } from '@/lib/utils';

export interface QuestionCardProps {
  question: Question;
  busy: boolean;
  onAnswer: (value: unknown) => void;
  onSkip: () => void;
  onBack: () => void;
  canGoBack: boolean;
  /** Rendered under the prompt — the voice control, when this turn allows speech. */
  voiceSlot?: React.ReactNode;
}

export function QuestionCard({
  question,
  busy,
  onAnswer,
  onSkip,
  onBack,
  canGoBack,
  voiceSlot,
}: QuestionCardProps) {
  const [text, setText] = useState('');
  const [multi, setMulti] = useState<string[]>([]);
  const [scale, setScale] = useState<number | null>(null);

  // A new turn is a clean slate. Without this the previous answer bleeds into the next
  // question, which on a multi-choice looks like the kiosk pre-selecting a clinical answer.
  useEffect(() => {
    setText('');
    setMulti([]);
    setScale(null);
  }, [question.turnId]);

  const optionButton = () =>
    cn(
      'w-full rounded-xl border px-4 py-3.5 text-left text-base transition-colors',
      'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2',
    );

  return (
    <div className="mk-pane p-6">
      <p className="text-xs uppercase tracking-wide" style={{ color: 'var(--mk-ink-subtle)' }}>
        {question.sectionTitle}
        {question.socrates ? ` · ${question.socrates}` : ''}
      </p>

      <Heading level={1} className="mt-2 !text-xl sm:!text-2xl">
        {question.prompt}
      </Heading>

      {question.help ? <Muted className="mt-2">{question.help}</Muted> : null}

      {question.translationMissing ? (
        // Surfaced rather than hidden — ADR-0007. A silently English question in a Tamil
        // interview is a gap the patient should be able to see, not one we paper over.
        <p className="mt-2 text-xs" style={{ color: 'var(--mk-status-warn-fg)' }}>
          This question has not been translated yet and is shown in English.
        </p>
      ) : null}

      {voiceSlot ? <div className="mt-5">{voiceSlot}</div> : null}

      <div className="mt-5 space-y-2">
        {(question.kind === 'single_choice' || question.kind === 'boolean') &&
          question.options.map((option) => (
            <button
              key={option.value}
              type="button"
              disabled={busy}
              onClick={() => onAnswer(option.value)}
              className={optionButton()}
              style={{
                borderColor: 'var(--mk-line-strong)',
                color: 'var(--mk-ink)',
                transitionDuration: 'var(--mk-quick)',
                outlineColor: 'var(--mk-accent)',
              }}
            >
              {option.label}
            </button>
          ))}

        {question.kind === 'multi_choice' ? (
          <>
            {question.options.map((option) => {
              const on = multi.includes(option.value);
              return (
                <button
                  key={option.value}
                  type="button"
                  disabled={busy}
                  aria-pressed={on}
                  onClick={() =>
                    setMulti((prev) =>
                      // An exclusive option ("none of these") clears the rest rather than
                      // sitting alongside them and producing a contradictory answer.
                      option.exclusive
                        ? on
                          ? []
                          : [option.value]
                        : prev.includes(option.value)
                          ? prev.filter((v) => v !== option.value)
                          : [...prev.filter((v) => !isExclusive(question, v)), option.value],
                    )
                  }
                  className={optionButton()}
                  style={{
                    borderColor: on ? 'var(--mk-accent)' : 'var(--mk-line-strong)',
                    backgroundColor: on ? 'var(--mk-status-info-bg)' : 'transparent',
                    color: on ? 'var(--mk-accent-ink)' : 'var(--mk-ink)',
                    transitionDuration: 'var(--mk-quick)',
                    outlineColor: 'var(--mk-accent)',
                  }}
                >
                  {on ? '✓ ' : ''}
                  {option.label}
                </button>
              );
            })}
            <Button variant="primary" disabled={busy || !multi.length} onClick={() => onAnswer(multi)}>
              Continue
            </Button>
          </>
        ) : null}

        {question.kind === 'scale' && question.scale ? (
          <>
            <div className="flex flex-wrap gap-2">
              {range(question.scale.min, question.scale.max).map((n) => (
                <button
                  key={n}
                  type="button"
                  disabled={busy}
                  onClick={() => setScale(n)}
                  className="h-12 w-12 rounded-full border text-base font-medium transition-colors"
                  style={{
                    borderColor: scale === n ? 'var(--mk-accent)' : 'var(--mk-line-strong)',
                    backgroundColor: scale === n ? 'var(--mk-status-info-bg)' : 'transparent',
                    color: scale === n ? 'var(--mk-accent-ink)' : 'var(--mk-ink)',
                    transitionDuration: 'var(--mk-quick)',
                  }}
                >
                  {n}
                </button>
              ))}
            </div>
            <Button
              variant="primary"
              disabled={busy || scale === null}
              onClick={() => onAnswer(scale)}
            >
              Continue
            </Button>
          </>
        ) : null}

        {(question.kind === 'open_text' || question.kind === 'duration') ? (
          <>
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={3}
              disabled={busy}
              placeholder="Type your answer here"
              className="w-full rounded-xl border px-4 py-3 text-base"
              style={{
                borderColor: 'var(--mk-line-strong)',
                backgroundColor: 'var(--mk-void)',
                color: 'var(--mk-ink)',
              }}
            />
            <Button variant="primary" disabled={busy || !text.trim()} onClick={() => onAnswer(text.trim())}>
              Continue
            </Button>
          </>
        ) : null}
      </div>

      <div className="mt-6 flex items-center justify-between">
        <button
          type="button"
          onClick={onBack}
          disabled={!canGoBack || busy}
          className="text-sm underline underline-offset-4 disabled:opacity-40"
          style={{ color: 'var(--mk-ink-muted)' }}
        >
          Back
        </button>
        {!question.required ? (
          <button
            type="button"
            onClick={onSkip}
            disabled={busy}
            className="text-sm underline underline-offset-4"
            style={{ color: 'var(--mk-ink-muted)' }}
          >
            I'd rather not say
          </button>
        ) : (
          // A required question has no skip control at all, rather than one that fails.
          <span className="text-xs" style={{ color: 'var(--mk-ink-subtle)' }}>
            The doctor needs this one
          </span>
        )}
      </div>
    </div>
  );
}

function isExclusive(question: Question, value: string): boolean {
  return question.options.find((o) => o.value === value)?.exclusive ?? false;
}

function range(min: number, max: number): number[] {
  return Array.from({ length: max - min + 1 }, (_, i) => min + i);
}

export default QuestionCard;
