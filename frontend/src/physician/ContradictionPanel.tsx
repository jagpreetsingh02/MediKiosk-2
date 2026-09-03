/**
 * Two sources that disagree. Both shown, neither preferred, no winner selected.
 *
 * ⛔ THE SYSTEM DOES NOT RESOLVE THESE, AND THERE IS NO CONTROL HERE THAT DOES.
 *
 * The case this exists for: a patient says "I don't take any medicines" and the prescription
 * in their hand says Metformin 500mg BD. Silently preferring either source is wrong.
 * Preferring the document treats the patient as unreliable; preferring the patient throws away
 * a drug interaction. Overwriting either destroys the very thing the physician needs to see.
 *
 * So both facts stay in the ledger, the disagreement is recorded, and the physician resolves
 * it — or asks the patient the clarifying question the rule supplies. Resolving a clinical
 * conflict is a clinical judgement, and this system does not make clinical judgements.
 *
 * READ `kind`, NOT THE FIELD NAMES. `patientSide`/`documentSide` are a leftover from when the
 * only case was a patient denying a document. For `dosage_conflict` BOTH sides are documents,
 * and the honest label for each comes from that side's own `origin`.
 */

import { Heading, Muted, Pane } from '@/design/ui/Surface';
import { StateGlyph } from '@/design/ui/StateGlyph';
import type { Contradiction, ContradictionSide } from '@/lib/api';

/**
 * ⚠️ THE TWO SOURCES OF CONTRADICTIONS DO NOT USE THE SAME KEY NAME.
 *
 * `/sessions/{ref}/contradictions` serialises the in-memory `Contradiction` model and calls
 * the identifier `contradictionId`. `/patients/{ref}/contradictions` serialises durable
 * `ContradictionRecord` rows and calls it `contradictionRef`. This panel renders both, and
 * keying on one name alone gave every durable row `undefined` — React then warned about
 * duplicate keys and, worse, would reuse DOM nodes between unrelated conflicts on re-render.
 *
 * Normalising here rather than renaming a field keeps the wire contract stable for the
 * session route that `api.ts` is already typed against.
 */
function identify(item: Contradiction, index: number): string {
  const raw = item as unknown as { contradictionId?: string; contradictionRef?: string };
  return raw.contradictionId ?? raw.contradictionRef ?? `${item.ruleId ?? 'cx'}-${index}`;
}

const KIND_LABEL: Record<string, string> = {
  denial: 'Denied, but recorded elsewhere',
  cross_tier: 'Two sources, two answers',
  dosage_conflict: 'Conflicting doses',
};

function Side({ side, heading }: { side: ContradictionSide; heading: string }) {
  return (
    <div
      className="flex-1 rounded-lg border p-3"
      style={{ borderColor: 'var(--mk-line-strong)' }}
    >
      <p className="text-xs uppercase tracking-wide" style={{ color: 'var(--mk-ink-subtle)' }}>
        {heading}
      </p>
      <p className="mt-1 font-medium" style={{ color: 'var(--mk-ink-strong)' }}>
        {String(side.value ?? '—')}
      </p>
      {side.verbatim ? (
        <p className="mt-1 text-sm italic" style={{ color: 'var(--mk-ink-muted)' }}>
          “{side.verbatim}”
        </p>
      ) : null}
      {/* The TRUE source of this half, whatever the field is called. */}
      <p className="mt-2 text-xs" style={{ color: 'var(--mk-evidence-ink)' }}>
        {side.origin}
      </p>
    </div>
  );
}

export function ContradictionPanel({ contradictions }: { contradictions: Contradiction[] }) {
  if (!contradictions.length) return null;

  return (
    <section>
      <div className="flex flex-wrap items-center gap-3">
        <Heading level={2}>Needs reconciliation</Heading>
        <StateGlyph state="caution" />
      </div>
      <Muted className="mt-1">
        Nothing below has been resolved automatically. Both sides are recorded and neither has
        been overridden.
      </Muted>

      <ul className="mt-3 space-y-3">
        {contradictions.map((item, index) => {
          const kind = item.kind ?? 'denial';
          const scope = (item as unknown as { scope?: string }).scope;
          const documentToDocument = kind === 'dosage_conflict';
          return (
            <Pane as="li" key={identify(item, index)}>
              <div className="flex flex-wrap items-baseline gap-2">
                <span
                  className="rounded-full px-2 py-0.5 text-xs font-medium"
                  style={{
                    backgroundColor: 'var(--mk-status-warn-bg)',
                    color: 'var(--mk-status-warn-fg)',
                  }}
                >
                  {KIND_LABEL[kind] ?? kind}
                </span>
                <span className="text-xs font-mono" style={{ color: 'var(--mk-ink-subtle)' }}>
                  {item.ruleId}
                </span>
                {scope === 'cross_encounter' ? (
                  <span
                    className="rounded-full px-2 py-0.5 text-xs"
                    style={{
                      backgroundColor: 'var(--mk-status-info-bg)',
                      color: 'var(--mk-status-info-fg)',
                    }}
                  >
                    across visits
                  </span>
                ) : null}
              </div>

              <p className="mt-2 font-medium" style={{ color: 'var(--mk-ink-strong)' }}>
                {item.label}
              </p>

              <div className="mt-3 flex flex-col gap-3 sm:flex-row">
                <Side
                  side={item.patientSide}
                  heading={documentToDocument ? 'One document says' : 'The patient said'}
                />
                <Side
                  side={item.documentSide}
                  heading={documentToDocument ? 'The other says' : 'The record says'}
                />
              </div>

              {item.clarifyingQuestion ? (
                <p
                  className="mt-3 rounded-lg px-3 py-2 text-sm"
                  style={{
                    backgroundColor: 'var(--mk-status-info-bg)',
                    color: 'var(--mk-status-info-fg)',
                  }}
                >
                  Ask the patient: “{item.clarifyingQuestion}”
                </p>
              ) : null}
            </Pane>
          );
        })}
      </ul>
    </section>
  );
}

export default ContradictionPanel;
