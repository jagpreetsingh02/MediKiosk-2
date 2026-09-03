/**
 * Reviewing one live capture session, and committing it. INVARIANT 4 LIVES ON THIS SCREEN.
 *
 * ⛔ THE COMMIT IS NOT BYPASSED AND CANNOT BE. `POST /sessions/{ref}/commit` is restricted by
 * ABAC to the `clinician` role AND requires an explicit `confirmed: true` in the body — a
 * patient token cannot reach the code path at all. Nothing leaves the building, reaches the
 * HIS, or becomes durable history until a physician presses the button here.
 *
 * ⛔ NOTHING ON THIS SCREEN IS AN ASSESSMENT. The summary is assembled deterministically by
 * `modules/summary/assemble.py` from recorded facts, and generation FAILS OUTRIGHT if any
 * clinical claim does not resolve to a `record_fact()` entry. What the physician reads is the
 * patient's history, traced; the diagnosis is theirs to make.
 *
 * Commit does four things in one transaction, in this order: build the FHIR bundle, push it
 * to the stub HIS, promote the session into a durable Encounter, and only then purge the
 * capture session (Invariant 6). A purge that ran first would destroy the visit on any later
 * failure — so the screen navigates to the new encounter rather than back to the queue,
 * because the encounter is the thing that now exists.
 */

import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

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
import ContradictionPanel from '@/physician/ContradictionPanel';
import FhirPanel from '@/physician/FhirPanel';
import {
  ApiError,
  api,
  type Contradiction,
  type PatientContext,
  type Summary,
} from '@/lib/api';

export default function SessionReview() {
  const { sessionRef = '' } = useParams();
  const navigate = useNavigate();

  const [summary, setSummary] = useState<Summary | null>(null);
  const [context, setContext] = useState<PatientContext | null>(null);
  const [contradictions, setContradictions] = useState<Contradiction[]>([]);
  const [error, setError] = useState<ApiError | null>(null);
  const [committing, setCommitting] = useState(false);
  const [confirmArmed, setConfirmArmed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [s, c, x] = await Promise.all([
          api.summary(sessionRef, false),
          api.patientContext(sessionRef).catch(() => null),
          api.contradictions(sessionRef).catch(() => ({ contradictions: [] as Contradiction[] })),
        ]);
        if (cancelled) return;
        setSummary(s);
        setContext(c);
        setContradictions(x.contradictions ?? []);
      } catch (cause) {
        if (!cancelled) setError(cause as ApiError);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sessionRef]);

  async function commit() {
    setCommitting(true);
    setError(null);
    try {
      const result = await api.commit(sessionRef);
      const promotion = (result as unknown as {
        promotion?: { patientRef?: string; encounterRef?: string };
      }).promotion;
      if (promotion?.patientRef && promotion?.encounterRef) {
        navigate(
          `/clinician/patients/${promotion.patientRef}/encounters/${promotion.encounterRef}`,
          { replace: true },
        );
      } else {
        navigate('/clinician', { replace: true });
      }
    } catch (cause) {
      setError(cause as ApiError);
      setCommitting(false);
    }
  }

  const escalation = summary?.escalation;

  return (
    <Surface kind="clinical">
      <DemoBand what="clinician identity" />
      <div className="mx-auto max-w-5xl px-6 py-8">
        <header className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <Heading level={1}>Intake review</Heading>
            <p className="mt-1 font-mono text-xs" style={{ color: 'var(--mk-ink-subtle)' }}>
              {sessionRef}
            </p>
          </div>
          <Button onClick={() => navigate('/clinician')}>Back to queue</Button>
        </header>

        {error ? (
          <div className="mt-4">
            <Problem message={error.message} detail={error.detail} />
          </div>
        ) : null}
        {!summary && !error ? <Spinner label="Assembling the traced summary…" /> : null}

        {escalation ? (
          <div className="mt-6">
            <RedFlagBanner flags={escalation.flags} />
          </div>
        ) : null}

        {context?.known ? (
          <Pane className="mt-6">
            <Heading level={2}>This patient has been here before</Heading>
            <Muted className="mt-1">
              {context.overview?.displayName ?? 'Known patient'} ·{' '}
              {context.overview?.counts?.encounters ?? 0} previous encounters on file.
            </Muted>

            {/* Deterministic retrieval, and it LISTS THE FEATURES rather than reporting a
                percentage — there are no embeddings behind this and no similarity score to
                mistake for a clinical judgement. */}
            {context.similar?.length ? (
              <div className="mt-4">
                <h3 className="text-sm font-semibold" style={{ color: 'var(--mk-ink-strong)' }}>
                  Similar previous visits
                </h3>
                <ul className="mt-2 space-y-1.5">
                  {context.similar.map((visit) => (
                    <li key={visit.encounterRef} className="text-sm">
                      <span className="font-mono text-xs" style={{ color: 'var(--mk-ink-subtle)' }}>
                        {visit.occurredOn}
                      </span>{' '}
                      <span style={{ color: 'var(--mk-ink)' }}>{visit.headline}</span>
                      <span className="ml-2 text-xs" style={{ color: 'var(--mk-ink-muted)' }}>
                        shares {visit.shared.map((f) => f.feature).join(', ')}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}

            {/* Cross-visit reconciliation. Neither side is preferred here either. */}
            {context.reconciliation?.length ? (
              <div className="mt-4">
                <h3
                  className="text-sm font-semibold"
                  style={{ color: 'var(--mk-status-warn-fg)' }}
                >
                  Needs reconciliation against the existing record
                </h3>
                <ul className="mt-2 space-y-2">
                  {context.reconciliation.map((finding, i) => (
                    <li
                      key={i}
                      className="rounded-lg px-3 py-2 text-sm"
                      style={{
                        backgroundColor: 'var(--mk-status-warn-bg)',
                        color: 'var(--mk-status-warn-fg)',
                      }}
                    >
                      <p className="font-medium">{finding.currentStatement}</p>
                      <p className="mt-1 text-xs opacity-90">{finding.note}</p>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </Pane>
        ) : null}

        {contradictions.length ? (
          <div className="mt-6">
            <ContradictionPanel contradictions={contradictions} />
          </div>
        ) : null}

        {summary ? (
          <section className="mt-6 space-y-4">
            <Heading level={2}>Summary</Heading>
            {summary.sections.map((section) => (
              <Pane key={section.sectionId}>
                <h3
                  className="text-sm font-semibold"
                  style={{ color: 'var(--mk-ink-strong)' }}
                >
                  {section.title}
                </h3>
                <ul className="mt-2 space-y-1.5">
                  {section.lines.map((line, index) => (
                    <li
                      key={`${section.sectionId}-${index}`}
                      className="text-sm"
                      style={{ color: 'var(--mk-ink)' }}
                    >
                      {line.text}
                    </li>
                  ))}
                </ul>
              </Pane>
            ))}
          </section>
        ) : null}

        <div className="mt-6">
          <FhirPanel sessionRef={sessionRef} />
        </div>

        {/* ---------------------------------------------------------------- the gate */}
        <Pane className="mt-8">
          <Heading level={2}>Confirm and commit</Heading>
          <Muted className="mt-2">
            Committing creates the durable encounter, sends the FHIR bundle to the configured
            receiver, and deletes the capture session. Every fact arrives{' '}
            <strong>pending your review</strong> — committing the encounter is not the same as
            signing off each fact, and the record now distinguishes the two.
          </Muted>

          <label className="mt-4 flex items-start gap-3">
            <input
              type="checkbox"
              checked={confirmArmed}
              onChange={(e) => setConfirmArmed(e.target.checked)}
              className="mt-1 h-4 w-4"
            />
            <span className="text-sm" style={{ color: 'var(--mk-ink)' }}>
              I have reviewed this history and confirm it as the record of this consultation.
            </span>
          </label>

          <div className="mt-4">
            <Button
              variant="primary"
              disabled={!confirmArmed || committing || !summary}
              onClick={commit}
            >
              {committing ? 'Committing…' : 'Confirm and commit encounter'}
            </Button>
          </div>
        </Pane>
      </div>
    </Surface>
  );
}
