/**
 * One committed encounter, reviewed fact by fact.
 *
 * This is where `review_status` actually moves. Committing the summary created the encounter
 * (Invariant 4); it did not sign off forty individual facts, and the record now distinguishes
 * those two acts. Everything here arrives `pending` and becomes `confirmed` one line at a
 * time, by a named clinician, with a `PhysicianDecision` row and an audit entry for each.
 *
 * ⛔ REJECTED FACTS ARE NOT HERE TO BE HIDDEN — THEY ARE GENUINELY ABSENT. `loader._facts_for`
 * drops them before the brief is assembled, so this screen never receives one. That is the
 * backend's guarantee, not a filter applied here, and it is why there is no "show rejected"
 * toggle: a physician's removal is not a display preference.
 *
 * The facts come from the BRIEF rather than a raw fact list, deliberately. The brief is
 * assembled deterministically and every line already carries its `factRef`, `tier` and
 * `reviewStatus` — so the physician reviews the same grouped, readable document they would
 * sign, not a database table sorted by primary key.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
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
import EvidenceDrawer from '@/physician/EvidenceDrawer';
import FactRow, { type ReviewableFact } from '@/physician/FactRow';
import { RedFlagBanner } from '@/physician/RedFlagBanner';
import {
  ApiError,
  api,
  type Brief,
  type BriefLine,
  type Contradiction,
  type RedFlag,
  type ReviewStatus,
} from '@/lib/api';

/**
 * Pull the REVIEWABLE lines out of the brief.
 *
 * ⛔ A `factRef` ALONE IS NOT ENOUGH, and assuming it was is what this guard exists for.
 * The brief carries several shapes that reference a fact without being a reviewable line:
 * `whatChanged.new` and `medications.items` are `{factRef, path, value}` cross-references,
 * and collecting those produced 76 rows where only 23 were real — the same fact offered for
 * confirmation three times under different headings.
 *
 * `reviewStatus` is the marker of a genuine `BriefLine`, because only `brief.py`'s line
 * builder emits it. Requiring it is precise and it fails safe: a line the backend does not
 * consider reviewable never gets a Confirm button.
 */
function harvest(brief: Brief | null): ReviewableFact[] {
  if (!brief) return [];
  const out: ReviewableFact[] = [];
  const seen = new Set<string>();

  const walk = (node: unknown): void => {
    if (Array.isArray(node)) {
      node.forEach(walk);
      return;
    }
    if (!node || typeof node !== 'object') return;
    const line = node as Partial<BriefLine>;
    if (
      typeof line.factRef === 'string' &&
      line.factRef &&
      line.reviewStatus !== undefined &&
      !seen.has(line.factRef)
    ) {
      seen.add(line.factRef);
      out.push({
        factRef: line.factRef,
        path: line.path ?? '',
        displayValue: line.displayValue ?? String(line.value ?? ''),
        tier: line.tier ?? 'stated',
        reviewStatus: line.reviewStatus as ReviewStatus,
        origin: line.origin,
        confidence: line.confidence ?? null,
      });
    }
    Object.values(node as Record<string, unknown>).forEach(walk);
  };

  walk(brief);
  return out;
}

export default function EncounterReview() {
  const { patientRef = '', encounterRef = '' } = useParams();
  const navigate = useNavigate();

  const [brief, setBrief] = useState<Brief | null>(null);
  const [facts, setFacts] = useState<ReviewableFact[]>([]);
  const [contradictions, setContradictions] = useState<Contradiction[]>([]);
  const [redFlags, setRedFlags] = useState<RedFlag[]>([]);
  const [openSource, setOpenSource] = useState<string | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [b, x, report] = await Promise.all([
          api.brief(patientRef, encounterRef),
          api.patientContradictions(patientRef).catch(() => ({ contradictions: [] })),
          api.clinicalReport(patientRef).catch(() => null),
        ]);
        if (cancelled) return;
        setBrief(b);
        setFacts(harvest(b));
        setContradictions((x.contradictions ?? []) as unknown as Contradiction[]);
        const fired = (report as unknown as { redFlags?: { fired?: RedFlag[] } } | null)?.redFlags
          ?.fired;
        setRedFlags(fired ?? []);
      } catch (cause) {
        if (!cancelled) setError(cause as ApiError);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [patientRef, encounterRef]);

  const onChanged = useCallback((next: ReviewableFact) => {
    setFacts((prev) => prev.map((f) => (f.factRef === next.factRef ? next : f)));
  }, []);

  const counts = useMemo(() => {
    const tally: Record<string, number> = { pending: 0, confirmed: 0, edited: 0, rejected: 0 };
    facts.forEach((f) => {
      tally[f.reviewStatus] = (tally[f.reviewStatus] ?? 0) + 1;
    });
    return tally;
  }, [facts]);

  const header = brief?.header;

  return (
    <Surface kind="clinical">
      <DemoBand what="clinician identity" />
      <div className="mx-auto max-w-5xl px-6 py-8">
        <header className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <Heading level={1}>
              {header?.displayName ?? 'Encounter'}
              {header?.encounter?.headline ? ` — ${header.encounter.headline}` : ''}
            </Heading>
            <p className="mt-1 font-mono text-xs" style={{ color: 'var(--mk-ink-subtle)' }}>
              {encounterRef} · {patientRef}
            </p>
          </div>
          <div className="flex gap-2">
            <Button onClick={() => navigate(`/clinician/patients/${patientRef}`)}>
              Full record
            </Button>
            <Button onClick={() => navigate('/clinician')}>Queue</Button>
          </div>
        </header>

        {loading ? <Spinner label="Loading the brief…" /> : null}
        {error ? (
          <div className="mt-4">
            <Problem message={error.message} detail={error.detail} />
          </div>
        ) : null}

        <div className="mt-6">
          <RedFlagBanner flags={redFlags} />
        </div>

        {contradictions.length ? (
          <div className="mt-6">
            <ContradictionPanel contradictions={contradictions} />
          </div>
        ) : null}

        {!loading ? (
          <section className="mt-8">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <Heading level={2}>Review each fact</Heading>
              <p className="text-xs" style={{ color: 'var(--mk-ink-muted)' }}>
                {counts.confirmed} confirmed · {counts.pending} not reviewed · {counts.edited}{' '}
                edited
              </p>
            </div>
            <Muted className="mt-1">
              Only confirmed facts enter active clinical use — retrieval, medication
              reconciliation and the active lists. Editing a value does not confirm it; it stays
              unconfirmed until you say so.
            </Muted>

            {facts.length ? (
              <ul className="mt-4 space-y-2">
                {facts.map((fact) => (
                  <FactRow
                    key={fact.factRef}
                    patientRef={patientRef}
                    encounterRef={encounterRef}
                    fact={fact}
                    onChanged={onChanged}
                    onOpenSource={setOpenSource}
                  />
                ))}
              </ul>
            ) : (
              <Pane className="mt-4">
                <Muted>
                  This encounter has no reviewable facts in the brief. Rejected facts are not
                  hidden here — they are dropped before the brief is assembled.
                </Muted>
              </Pane>
            )}
          </section>
        ) : null}

        <section className="mt-10">
          <Heading level={2}>Report</Heading>
          <Pane className="mt-3 flex flex-wrap gap-2">
            <Button
              onClick={async () => {
                const { url, filename } = await api.briefPdf(patientRef, 'clinician', encounterRef);
                const a = document.createElement('a');
                a.href = url;
                a.download = filename;
                a.click();
                URL.revokeObjectURL(url);
              }}
            >
              Download clinician PDF
            </Button>
            <Button
              onClick={async () => {
                const { url } = await api.briefPdf(patientRef, 'patient', encounterRef);
                window.open(url, '_blank', 'noopener');
              }}
            >
              View patient version
            </Button>
          </Pane>
        </section>
      </div>

      {openSource ? (
        <EvidenceDrawer
          patientRef={patientRef}
          encounterRef={encounterRef}
          factRef={openSource}
          onClose={() => setOpenSource(null)}
        />
      ) : null}
    </Surface>
  );
}
