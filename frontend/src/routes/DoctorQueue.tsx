/**
 * The clinician's landing screen: who is waiting, and how urgently.
 *
 * ⛔ THE ORDER IS THE BACKEND'S, AND ESCALATION IS NEVER SOFTENED HERE. `priority` comes from
 * the deterministic red-flag engine (Invariant 3: it rises and never falls), and this list
 * sorts by it without re-deciding it. There is no control on this screen that lowers a
 * priority, because there is no such operation in the product.
 *
 * TWO WAYS IN, because the record has two halves. A live capture session is reviewed and
 * COMMITTED (Invariant 4) — that is where the encounter is created. A committed encounter is
 * then where individual facts get confirmed, corrected or rejected, because `review_status`
 * lives on `clinical_fact` and no clinical fact exists until promotion has run.
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
import { StateGlyph } from '@/design/ui/StateGlyph';
import { ApiError, api, type QueueEntry } from '@/lib/api';
import { getIdentity, signOut } from '@/lib/session';

const PRIORITY_RANK: Record<string, number> = { immediate: 0, urgent: 1, routine: 2 };

export default function DoctorQueue() {
  const navigate = useNavigate();
  const [queue, setQueue] = useState<QueueEntry[] | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [lookup, setLookup] = useState('pat_demo000001');

  useEffect(() => {
    api
      .queue()
      .then((r) => setQueue(r.queue ?? []))
      .catch((cause) => setError(cause as ApiError));
  }, []);

  const sorted = [...(queue ?? [])].sort(
    (a, b) => (PRIORITY_RANK[a.priority] ?? 9) - (PRIORITY_RANK[b.priority] ?? 9),
  );

  return (
    <Surface kind="clinical">
      <DemoBand what="clinician identity" />
      <div className="mx-auto max-w-4xl px-6 py-8">
        <header className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <Heading level={1}>Intake queue</Heading>
            <Muted className="mt-1">Signed in as {getIdentity().actor}</Muted>
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

        {error ? <Problem message={error.message} detail={error.detail} /> : null}
        {!queue && !error ? <Spinner label="Loading the queue…" /> : null}

        {queue && !sorted.length ? (
          <Pane className="mt-6">
            <Heading level={2}>Nobody is waiting</Heading>
            <Muted className="mt-1">
              A completed kiosk intake appears here. You can still open a patient's record
              below.
            </Muted>
          </Pane>
        ) : null}

        {sorted.length ? (
          <ul className="mt-6 space-y-2">
            {sorted.map((entry) => (
              <Pane as="li" key={entry.sessionRef} className="flex flex-wrap items-center gap-4">
                <StateGlyph
                  state={
                    entry.priority === 'immediate'
                      ? 'critical'
                      : entry.priority === 'urgent'
                        ? 'caution'
                        : 'ok'
                  }
                />
                <div className="min-w-0 flex-1">
                  <p className="font-mono text-xs" style={{ color: 'var(--mk-ink-subtle)' }}>
                    {entry.sessionRef}
                  </p>
                  <p className="text-sm" style={{ color: 'var(--mk-ink)' }}>
                    waiting {entry.waitingMinutes} min · {entry.language}
                    {entry.ayushMode ? ' · AYUSH' : ''} · {entry.status}
                  </p>
                </div>
                <Button
                  variant="primary"
                  onClick={() => navigate(`/clinician/sessions/${entry.sessionRef}`)}
                >
                  Review
                </Button>
              </Pane>
            ))}
          </ul>
        ) : null}

        <section className="mt-10">
          <Heading level={2}>Open a patient's record</Heading>
          <Muted className="mt-1">
            The longitudinal history — timeline, medicines, contradictions, and per-fact review
            of any committed encounter.
          </Muted>
          <Pane className="mt-3 flex flex-wrap items-center gap-3">
            <input
              value={lookup}
              onChange={(e) => setLookup(e.target.value)}
              className="min-w-0 flex-1 rounded-lg border px-3 py-2 font-mono text-sm"
              style={{
                borderColor: 'var(--mk-line-strong)',
                backgroundColor: 'var(--mk-void)',
                color: 'var(--mk-ink)',
              }}
              placeholder="pat_…"
            />
            <Button onClick={() => navigate(`/clinician/patients/${lookup.trim()}`)}>
              Open record
            </Button>
          </Pane>
        </section>
      </div>
    </Surface>
  );
}
