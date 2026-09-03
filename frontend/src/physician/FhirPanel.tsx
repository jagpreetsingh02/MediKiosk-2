/**
 * Interoperability: the FHIR R4 bundle this encounter WOULD send, before it sends it.
 *
 * ⚠️ THE RECEIVER IS A STUB, AND THE PANEL SAYS SO. `HIS_FHIR_ENDPOINT` defaults to
 * `/api/v1/stub-his/Bundle` — a receiver inside this same application that accepts a bundle
 * and records that it arrived. There is no hospital vendor integration here, that is
 * deliberately out of scope per the problem statement, and presenting it as a live HIS
 * connection would be the single easiest thing to misrepresent in a demo.
 *
 * The preview is a real bundle, not a mock-up: it is built by `app/fhir/bundle.py` from the
 * actual recorded facts, Composition-led, stamped `fhirVersion 4.0.1` per ADR-0002, with a
 * `Provenance` resource for every clinical resource. Every `Coding` in it came out of
 * `emit_coding()`, which reads from a version-pinned CodeSystem — codes are retrieved, never
 * generated, and `unmapped` is a valid answer rather than a nearest guess.
 */

import { useState } from 'react';

import { Button, Heading, Muted, Pane, Problem } from '@/design/ui/Surface';
import { ApiError, api } from '@/lib/api';

interface Preview {
  fhirVersion: string | null;
  resourceCounts: Record<string, number>;
  entries: number;
  bundle: Record<string, unknown>;
  notice: string;
}

export function FhirPanel({ sessionRef }: { sessionRef: string }) {
  const [bundle, setBundle] = useState<Preview | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);

  async function preview() {
    setBusy(true);
    setError(null);
    try {
      setBundle(await api.fhirPreview(sessionRef));
    } catch (cause) {
      setError(cause as ApiError);
    } finally {
      setBusy(false);
    }
  }

  // The route already counts the resources for us; recomputing them here would be a second
  // answer to the same question.
  const counts = bundle?.resourceCounts ?? {};

  return (
    <Pane>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <Heading level={2}>Interoperability</Heading>
          <Muted className="mt-1">
            FHIR R4 (fhirVersion 4.0.1), Composition-led, one Provenance resource per clinical
            resource.
          </Muted>
        </div>
        <Button onClick={preview} disabled={busy}>
          {busy ? 'Building…' : bundle ? 'Rebuild preview' : 'Preview FHIR bundle'}
        </Button>
      </div>

      <p
        className="mt-3 rounded-lg px-3 py-2 text-xs"
        style={{ backgroundColor: 'var(--mk-status-warn-bg)', color: 'var(--mk-status-warn-fg)' }}
      >
        ⚠️ DEMO RECEIVER. The configured HIS endpoint is a stub inside this application, not a
        hospital system. Nothing here is a real ABDM or vendor integration.
      </p>

      {error ? (
        <div className="mt-3">
          <Problem message={error.message} detail={error.detail} />
        </div>
      ) : null}

      {bundle ? (
        <div className="mt-4">
          <p className="text-sm font-medium" style={{ color: 'var(--mk-ink-strong)' }}>
            {bundle.entries} resources · FHIR {bundle.fhirVersion ?? 'R4'}
          </p>
          <p className="mt-1 text-xs" style={{ color: 'var(--mk-ink-muted)' }}>
            {bundle.notice}
          </p>
          <ul className="mt-2 flex flex-wrap gap-2">
            {Object.entries(counts).map(([type, n]) => (
              <li
                key={type}
                className="rounded-full px-2.5 py-1 text-xs"
                style={{
                  backgroundColor: 'var(--mk-status-info-bg)',
                  color: 'var(--mk-status-info-fg)',
                }}
              >
                {type} × {n}
              </li>
            ))}
          </ul>
          <details className="mt-3">
            <summary className="cursor-pointer text-sm" style={{ color: 'var(--mk-accent-ink)' }}>
              Show the raw bundle
            </summary>
            <pre
              className="mt-2 max-h-96 overflow-auto rounded-lg p-3 text-xs"
              style={{ backgroundColor: 'var(--mk-status-ok-bg)', color: 'var(--mk-ink)' }}
            >
              {JSON.stringify(bundle.bundle, null, 2)}
            </pre>
          </details>
        </div>
      ) : null}
    </Pane>
  );
}

export default FhirPanel;
