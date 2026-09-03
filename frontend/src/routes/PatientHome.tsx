/**
 * What the record already knows about this person, before they say a word.
 *
 * THE POINT OF THE WHOLE LONGITUDINAL BUILD IS INVISIBLE ON AN EMPTY SCREEN. A patient home
 * with nothing on it looks exactly like the single-encounter product this replaced, so this
 * screen leads with what is already on file — prior visits, medicines, recent events — and
 * only then offers to start a new consultation.
 *
 * ⛔ IT DOES NOT DUMP EVERY FACT. The brief is explicit about this and it is also just good
 * clinical UI: a patient does not need 805 rows, they need to recognise their own record.
 * Counts, the last few visits, and the medicines the system believes are documented. The
 * physician surface is where density belongs.
 *
 * ⚠️ MEDICATION STATUS IS NOT "IS TAKING". `medication_snapshot` reports how each mention is
 * KNOWN — documented, patient-reported-current, historical — and a past prescription is not
 * evidence of current use. The wording here preserves that distinction rather than flattening
 * it into a list of drugs the patient "is on".
 */

import { useEffect, useState } from 'react';
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
import { ApiError, api, type PatientBrief, type PatientOverview } from '@/lib/api';
import { rememberPatientRef, signOut } from '@/lib/session';

export default function PatientHome() {
  const navigate = useNavigate();

  const [record, setRecord] = useState<PatientOverview | null>(null);
  const [brief, setBrief] = useState<PatientBrief | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const mine = await api.myRecord();
        if (cancelled) return;
        setRecord(mine);
        const ref = (mine as unknown as { patientRef?: string }).patientRef;
        if (ref) {
          rememberPatientRef(ref);
          // ⛔ THE PATIENT-FACING BRIEF, NOT `/medications`.
          //
          // `/patients/{ref}/medications` requires the `session.read` action, which
          // `config/policy.yaml` grants to `clinician` and deliberately NOT to `patient` —
          // it carries no ownership constraint, so a patient holding it could read anyone.
          // Calling it here returned a flat 403 and the medicines section silently never
          // rendered. `brief/patient` requires `report.read_own`, which `_resolve()` checks
          // against the token's own abha_ref before assembling anything.
          const own = await api.patientBrief(ref);
          if (!cancelled) setBrief(own);
        }
      } catch (cause) {
        if (!cancelled) setError(cause as ApiError);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const known = (record as unknown as { known?: boolean } | null)?.known ?? false;
  const counts = (record?.counts ?? {}) as Record<string, number>;
  const recent = (record?.recent ?? []) as Array<{
    encounterRef: string;
    occurredOn: string;
    headline: string;
    priority: string;
  }>;

  return (
    <Surface kind="kiosk">
      <DemoBand />
      <div className="mx-auto max-w-3xl px-6 py-10">
        <header className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <Heading level={1}>
              {record?.displayName ? `Hello, ${record.displayName}` : 'Your health record'}
            </Heading>
            <Muted className="mt-2">
              {record?.abhaMasked ? `ABHA ${record.abhaMasked} · ` : ''}
              {record?.ageYears ? `${record.ageYears} years · ` : ''}
              {record?.gender ?? ''}
            </Muted>
          </div>
          <Button
            onClick={() => {
              signOut();
              navigate('/');
            }}
          >
            Sign out
          </Button>
        </header>

        {loading ? <Spinner label="Loading your record from the health service…" /> : null}
        {error ? <Problem message={error.message} detail={error.detail} /> : null}

        {!loading && !error && !known ? (
          <Pane className="mt-8">
            <Heading level={2}>This will be your first visit</Heading>
            <Muted className="mt-2">
              We have no previous records for this ABHA address. Nothing is missing — the
              history starts today.
            </Muted>
          </Pane>
        ) : null}

        {!loading && known ? (
          <>
            <section className="mt-8 grid grid-cols-2 gap-3 sm:grid-cols-4">
              {[
                ['Previous visits', counts.encounters],
                ['Prescriptions', counts.prescriptions],
                ['Lab reports', counts.labReports],
                ['Medicines on file', counts.medications],
              ].map(([label, value]) => (
                <Pane key={String(label)} className="text-center">
                  <p className="text-2xl font-semibold" style={{ color: 'var(--mk-ink-strong)' }}>
                    {value ?? 0}
                  </p>
                  <p className="mt-1 text-xs" style={{ color: 'var(--mk-ink-muted)' }}>
                    {label}
                  </p>
                </Pane>
              ))}
            </section>

            {recent.length ? (
              <section className="mt-8">
                <Heading level={2}>Your recent visits</Heading>
                <ul className="mt-3 space-y-2">
                  {recent.map((visit) => (
                    <Pane as="li" key={visit.encounterRef} className="flex items-baseline gap-4">
                      <span
                        className="shrink-0 font-mono text-xs"
                        style={{ color: 'var(--mk-ink-subtle)' }}
                      >
                        {visit.occurredOn}
                      </span>
                      <span className="flex-1" style={{ color: 'var(--mk-ink)' }}>
                        {visit.headline}
                      </span>
                    </Pane>
                  ))}
                </ul>
              </section>
            ) : null}

            {brief?.groups?.length ? (
              <section className="mt-8">
                <Heading level={2}>What your record says</Heading>
                <Muted className="mt-1">
                  This is what the record has SEEN. It is not a diagnosis, and a medicine
                  listed here is not a statement that you are taking it today — your doctor
                  goes through all of it with you.
                </Muted>
                {brief.groups.slice(0, 3).map((group) => (
                  <div key={group.title} className="mt-4">
                    <h3
                      className="text-sm font-semibold"
                      style={{ color: 'var(--mk-ink-strong)' }}
                    >
                      {group.title}
                    </h3>
                    <ul className="mt-2 space-y-1.5">
                      {group.items.slice(0, 6).map((item, i) => (
                        <Pane as="li" key={`${group.title}-${i}`}>
                          <span className="text-sm" style={{ color: 'var(--mk-ink-muted)' }}>
                            {item.label}
                          </span>
                          <span
                            className="ml-2 font-medium"
                            style={{ color: 'var(--mk-ink-strong)' }}
                          >
                            {String(item.value ?? '')}
                          </span>
                        </Pane>
                      ))}
                    </ul>
                  </div>
                ))}
              </section>
            ) : null}
          </>
        ) : null}

        <section className="mt-10">
          <Pane className="flex flex-wrap items-center justify-between gap-4">
            <div>
              <Heading level={2}>Ready to see the doctor?</Heading>
              <Muted className="mt-1">
                We will ask about today's problem, one question at a time. You can speak or tap.
              </Muted>
            </div>
            <Button variant="primary" onClick={() => navigate('/patient/consultation')}>
              Start new consultation
            </Button>
          </Pane>
        </section>
      </div>
    </Surface>
  );
}
