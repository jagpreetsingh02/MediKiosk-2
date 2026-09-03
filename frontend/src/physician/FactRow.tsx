/**
 * One reviewable fact: confirm, edit, reject — and open its source.
 *
 * ⛔ THE FRONTEND HAS NO STATUS LOGIC OF ITS OWN. Every transition posts to
 * `POST /patients/{ref}/encounters/{enc}/facts/{fact}/review` and the row re-renders from what
 * the API returns. It does not compute the next status, does not optimistically apply one, and
 * does not pre-filter the buttons on a guess about what is legal. If a transition is refused
 * the backend's sentence is shown verbatim.
 *
 * That matters because the rules are not obvious and duplicating them here is how the two
 * copies drift:
 *
 *   `rejected` IS TERMINAL. A physician removing a fact has made a positive clinical
 *   statement, not a draft one. There is no route back, so this row offers none — and the
 *   backend refuses one even if a request is forged.
 *
 *   `edited` DOES NOT IMPLY `confirmed`. Changing a dose and approving a dose are two acts.
 *   After an edit the row stays visibly unconfirmed and still offers Confirm, because the
 *   alternative — treating a correction as a sign-off — would put a value into active clinical
 *   use that nobody actually approved.
 *
 * `tier` and `reviewStatus` are rendered as SEPARATE chips on purpose. Both use the word
 * "confirmed" for completely different things — the patient affirming a closed question, and a
 * clinician signing the fact off — and collapsing them would let one masquerade as the other.
 */

import { useState } from 'react';

import { Button } from '@/design/ui/Surface';
import { ApiError, api, type ReviewStatus } from '@/lib/api';

export interface ReviewableFact {
  factRef: string;
  path: string;
  displayValue: string | null;
  tier: string;
  reviewStatus: ReviewStatus;
  origin?: string;
  confidence?: number | null;
}

const STATUS_STYLE: Record<ReviewStatus, { bg: string; fg: string; word: string }> = {
  pending: { bg: 'var(--mk-status-warn-bg)', fg: 'var(--mk-status-warn-fg)', word: 'Not reviewed' },
  confirmed: { bg: 'var(--mk-status-ok-bg)', fg: 'var(--mk-status-ok-fg)', word: 'Confirmed' },
  edited: {
    bg: 'var(--mk-status-info-bg)',
    fg: 'var(--mk-status-info-fg)',
    word: 'Edited — not yet confirmed',
  },
  rejected: {
    bg: 'var(--mk-status-alert-bg)',
    fg: 'var(--mk-status-alert-fg)',
    word: 'Rejected',
  },
};

export interface FactRowProps {
  patientRef: string;
  encounterRef: string;
  fact: ReviewableFact;
  onChanged: (next: ReviewableFact) => void;
  onOpenSource: (factRef: string) => void;
}

export function FactRow({
  patientRef,
  encounterRef,
  fact,
  onChanged,
  onOpenSource,
}: FactRowProps) {
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(fact.displayValue ?? '');
  const [refusal, setRefusal] = useState<string | null>(null);

  const style = STATUS_STYLE[fact.reviewStatus];
  const terminal = fact.reviewStatus === 'rejected';

  async function move(status: ReviewStatus, value?: unknown) {
    setBusy(true);
    setRefusal(null);
    try {
      const result = await api.reviewFact(patientRef, encounterRef, fact.factRef, {
        status,
        value,
      });
      onChanged({
        ...fact,
        reviewStatus: result.reviewStatus,
        displayValue: result.displayValue ?? fact.displayValue,
        origin: result.origin ?? fact.origin,
      });
      setEditing(false);
    } catch (cause) {
      // The backend's own words. It knows which transitions are legal; this row does not.
      setRefusal((cause as ApiError).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="mk-pane p-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <p className="font-mono text-xs" style={{ color: 'var(--mk-ink-subtle)' }}>
            {fact.path}
          </p>

          {editing ? (
            <input
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              className="mt-1 w-full rounded border px-2 py-1.5 text-sm"
              style={{
                borderColor: 'var(--mk-line-strong)',
                backgroundColor: 'var(--mk-void)',
                color: 'var(--mk-ink)',
              }}
              autoFocus
            />
          ) : (
            <p className="mt-0.5 text-sm font-medium" style={{ color: 'var(--mk-ink-strong)' }}>
              {fact.displayValue ?? '—'}
            </p>
          )}

          <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs">
            {/* Two chips, never one. See the header. */}
            <span
              className="rounded-full px-2 py-0.5"
              style={{ backgroundColor: style.bg, color: style.fg }}
            >
              {style.word}
            </span>
            <span
              className="rounded-full px-2 py-0.5"
              style={{
                backgroundColor: 'var(--mk-status-info-bg)',
                color: 'var(--mk-status-info-fg)',
              }}
            >
              evidence: {fact.tier}
            </span>
            <button
              type="button"
              onClick={() => onOpenSource(fact.factRef)}
              className="underline underline-offset-2"
              style={{ color: 'var(--mk-evidence-ink)' }}
            >
              where did this come from?
            </button>
          </div>
        </div>

        {/* No control of any kind on a rejected fact. Terminal is terminal. */}
        {terminal ? (
          <span className="text-xs" style={{ color: 'var(--mk-ink-subtle)' }}>
            final
          </span>
        ) : (
          <div className="flex shrink-0 flex-wrap gap-1.5">
            {editing ? (
              <>
                <Button
                  variant="primary"
                  disabled={busy || !draft.trim()}
                  onClick={() => move('edited', draft.trim())}
                >
                  Save edit
                </Button>
                <Button disabled={busy} onClick={() => setEditing(false)}>
                  Cancel
                </Button>
              </>
            ) : (
              <>
                {fact.reviewStatus !== 'confirmed' ? (
                  <Button variant="primary" disabled={busy} onClick={() => move('confirmed')}>
                    Confirm
                  </Button>
                ) : null}
                <Button disabled={busy} onClick={() => setEditing(true)}>
                  Edit
                </Button>
                <Button variant="danger" disabled={busy} onClick={() => move('rejected')}>
                  Reject
                </Button>
              </>
            )}
          </div>
        )}
      </div>

      {refusal ? (
        <p
          className="mt-2 rounded px-2 py-1 text-xs"
          style={{
            backgroundColor: 'var(--mk-status-alert-bg)',
            color: 'var(--mk-status-alert-fg)',
          }}
          role="alert"
        >
          {refusal}
        </p>
      ) : null}
    </li>
  );
}

export default FactRow;
