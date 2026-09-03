/**
 * The longitudinal record: every encounter across time, and the medication thread through it.
 *
 * ⛔ NOTHING CLINICAL IS COMPUTED HERE. The timeline comes from `timeline_event` rows written
 * at promotion; the medication threads from `history.medication_history`, which groups every
 * mention of a drug across every visit and says how each one is KNOWN. React sorts and paints.
 * Recomputing a patient's history in the browser would create a second answer to a clinical
 * question, and the two would diverge the moment either changed.
 *
 * ⚠️ MEDICATION STATUS IS PROVENANCE, NOT STATE. `documented` means a prescription was seen,
 * not that the patient is taking it. `historical` means it appeared at an earlier visit and
 * was not mentioned today. A UI that rendered these as one list of "current medications" would
 * be making the exact clinical claim the backend refuses to make — so each mention keeps its
 * own status word and the note that says what the column means.
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
import ContradictionPanel from '@/physician/ContradictionPanel';
import {
  ApiError,
  api,
  type Contradiction,
  type MedicationThread,
  type PatientOverview,
  type TimelineRow,
} from '@/lib/api';

/** What each status actually asserts. Copied in spirit from `history._how_we_know`. */
const STATUS_NOTE: Record<string, string> = {
  documented: 'a prescription we have seen',
  'patient-reported-current': 'the patient said they take it',
  historical: 'seen at an earlier visit, not mentioned since',
  'stopped-reported': 'the patient said they stopped',
  uncertain: 'sources disagree or are incomplete',
};

export default function PatientRecord() {
  const { patientRef = '' } = useParams();
  const navigate = useNavigate();

  const [overview, setOverview] = useState<PatientOverview | null>(null);
  const [timeline, setTimeline] = useState<TimelineRow[]>([]);
  const [medications, setMedications] = useState<MedicationThread[]>([]);
  const [needsReconciliation, setNeedsReconciliation] = useState<string[]>([]);
  const [contradictions, setContradictions] = useState<Contradiction[]>([]);
  const [encounters, setEncounters] = useState<
    { encounterRef: string; occurredOn: string; headline: string | null }[]
  >([]);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [o, t, m, x, e] = await Promise.all([
          api.patientOverview(patientRef),
          api.patientTimeline(patientRef),
          api.patientMedications(patientRef),
          api.patientContradictions(patientRef).catch(() => ({ contradictions: [] })),
          api.myEncounters(patientRef).catch(() => ({ encounters: [] })),
        ]);
        if (cancelled) return;
        setOverview(o);
        setTimeline(t.events ?? []);
        setMedications(m.medications ?? []);
        setNeedsReconciliation(m.needsReconciliation ?? []);
        setContradictions((x.contradictions ?? []) as unknown as Contradiction[]);
        setEncounters((e.encounters ?? []) as never);
      } catch (cause) {
        if (!cancelled) setError(cause as ApiError);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [patientRef]);

  return (
    <Surface kind="clinical">
      <DemoBand what="clinician identity" />
      <div className="mx-auto max-w-5xl px-6 py-8">
        <header className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <Heading level={1}>{overview?.displayName ?? 'Patient record'}</Heading>
            <Muted className="mt-1">
              {overview?.abhaMasked ? `ABHA ${overview.abhaMasked} · ` : ''}
              {overview?.ageYears ? `${overview.ageYears} · ` : ''}
              {overview?.gender ?? ''}
            </Muted>
          </div>
          <Button onClick={() => navigate('/clinician')}>Queue</Button>
        </header>

        {loading ? <Spinner label="Loading the longitudinal record…" /> : null}
        {error ? (
          <div className="mt-4">
            <Problem message={error.message} detail={error.detail} />
          </div>
        ) : null}

        {contradictions.length ? (
          <div className="mt-6">
            <ContradictionPanel contradictions={contradictions} />
          </div>
        ) : null}

        {encounters.length ? (
          <section className="mt-8">
            <Heading level={2}>Confirmed encounters</Heading>
            <Muted className="mt-1">Only what a physician committed.</Muted>
            <ul className="mt-3 space-y-2">
              {encounters.map((e) => (
                <Pane as="li" key={e.encounterRef} className="flex flex-wrap items-center gap-3">
                  <span className="font-mono text-xs" style={{ color: 'var(--mk-ink-subtle)' }}>
                    {e.occurredOn}
                  </span>
                  <span className="min-w-0 flex-1" style={{ color: 'var(--mk-ink)' }}>
                    {e.headline ?? 'Clinical encounter'}
                  </span>
                  <Button
                    onClick={() =>
                      navigate(
                        `/clinician/patients/${patientRef}/encounters/${e.encounterRef}`,
                      )
                    }
                  >
                    Review facts
                  </Button>
                </Pane>
              ))}
            </ul>
          </section>
        ) : null}

        {medications.length ? (
          <section className="mt-8">
            <Heading level={2}>Medication history</Heading>
            <Muted className="mt-1">
              Status describes how each mention is <strong>known</strong>. A past prescription
              is not evidence of current use.
            </Muted>
            {needsReconciliation.length ? (
              <p
                className="mt-2 rounded-lg px-3 py-2 text-sm"
                style={{
                  backgroundColor: 'var(--mk-status-warn-bg)',
                  color: 'var(--mk-status-warn-fg)',
                }}
              >
                Needs reconciliation: {needsReconciliation.join(', ')}
              </p>
            ) : null}
            <ul className="mt-3 space-y-2">
              {medications.map((thread) => (
                <Pane as="li" key={thread.normalized}>
                  <p className="font-medium" style={{ color: 'var(--mk-ink-strong)' }}>
                    {thread.name}
                  </p>
                  <ul className="mt-2 space-y-1">
                    {thread.mentions.map((mention, i) => (
                      <li key={i} className="flex flex-wrap items-baseline gap-2 text-sm">
                        <span
                          className="font-mono text-xs"
                          style={{ color: 'var(--mk-ink-subtle)' }}
                        >
                          {mention.observedOn ?? 'undated'}
                        </span>
                        <span style={{ color: 'var(--mk-ink)' }}>
                          {[mention.dose, mention.frequency].filter(Boolean).join(' ') || '—'}
                        </span>
                        <span
                          className="rounded-full px-2 py-0.5 text-xs"
                          style={{
                            backgroundColor: 'var(--mk-status-info-bg)',
                            color: 'var(--mk-status-info-fg)',
                          }}
                        >
                          {mention.status}
                        </span>
                        <span className="text-xs" style={{ color: 'var(--mk-ink-muted)' }}>
                          {STATUS_NOTE[mention.status] ?? ''}
                        </span>
                      </li>
                    ))}
                  </ul>
                </Pane>
              ))}
            </ul>
          </section>
        ) : null}

        {timeline.length ? (
          <section className="mt-8">
            <Heading level={2}>Timeline</Heading>
            <ul className="mt-3 space-y-1.5">
              {timeline.map((row) => (
                <li
                  key={row.eventRef}
                  className="flex flex-wrap items-baseline gap-3 border-l-2 py-1.5 pl-3"
                  style={{ borderColor: 'var(--mk-line-strong)' }}
                >
                  <span
                    className="w-24 shrink-0 font-mono text-xs"
                    style={{ color: 'var(--mk-ink-subtle)' }}
                  >
                    {row.occurredOn ?? 'undated'}
                  </span>
                  <span
                    className="rounded-full px-2 py-0.5 text-xs"
                    style={{
                      backgroundColor: 'var(--mk-status-ok-bg)',
                      color: 'var(--mk-status-ok-fg)',
                    }}
                  >
                    {row.kind}
                  </span>
                  <span className="min-w-0 flex-1 text-sm" style={{ color: 'var(--mk-ink)' }}>
                    {row.label}
                  </span>
                </li>
              ))}
            </ul>
          </section>
        ) : null}
      </div>
    </Surface>
  );
}
