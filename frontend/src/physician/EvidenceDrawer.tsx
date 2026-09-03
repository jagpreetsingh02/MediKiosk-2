/**
 * Click-to-source. Where a fact came from, shown rather than asserted.
 *
 * This is the product. Every other feature here is a way of getting a physician to a screen
 * where "Metformin 500 mg" can be opened and answered with the sentence the patient said or
 * the patch of paper it was read off. Invariant 2 makes that possible — every fact carries a
 * span — and this component is where the guarantee becomes visible.
 *
 * FOUR ORIGINS, AND THEY ARE NOT INTERCHANGEABLE:
 *
 *   utterance        the patient said it, with the modality and the ASR confidence
 *   document         read off a page, with the page image and the measured region
 *   prior_encounter  carried forward from an earlier visit, which is NAMED
 *   physician        a clinician typed it, and their name is on it
 *
 * ⚠️ AN UNMEASURED CONFIDENCE IS SHOWN AS UNMEASURED. `confidenceStatus: 'unavailable'` means
 * the browser or engine gave no score, and the drawer says so instead of printing 0.00, which
 * would read as "the system was certain this was wrong".
 *
 * ⚠️ A HUMAN READING SITS BESIDE THE SCRAWL, NEVER INSTEAD OF IT (ADR-0012). When OCR read
 * "TAB. METFARMIN" and a named clinician read it as Metformin, both are shown with the name
 * attached — an unattributed correction is an anonymous edit, not provenance.
 */

import { useEffect, useState } from 'react';

import { Button, Heading, Muted, Problem, Spinner } from '@/design/ui/Surface';
import SourceCrop from '@/kiosk/SourceCrop';
import { ApiError, api, type BriefEvidence, type FactEvidence } from '@/lib/api';

const ORIGIN_WORDS: Record<string, string> = {
  patient_stated: 'The patient said this',
  document: 'Read from a document',
  prior_encounter: 'Carried forward from an earlier visit',
  physician_entered: 'Entered by a clinician',
};

export interface EvidenceDrawerProps {
  patientRef: string;
  encounterRef: string;
  factRef: string;
  onClose: () => void;
}

export function EvidenceDrawer({
  patientRef,
  encounterRef,
  factRef,
  onClose,
}: EvidenceDrawerProps) {
  const [evidence, setEvidence] = useState<FactEvidence | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [pageUrls, setPageUrls] = useState<Record<string, string>>({});

  useEffect(() => {
    let cancelled = false;
    api
      .briefEvidence(patientRef, encounterRef, factRef)
      .then((found) => !cancelled && setEvidence(found))
      .catch((cause) => !cancelled && setError(cause as ApiError));
    return () => {
      cancelled = true;
    };
  }, [patientRef, encounterRef, factRef]);

  // A page image is fetched as a blob because `<img src>` cannot carry a bearer token and
  // every document route requires one. Object URLs are revoked on unmount.
  useEffect(() => {
    const created: string[] = [];
    (async () => {
      for (const item of evidence?.evidence ?? []) {
        if (!item.documentRef || !item.page) continue;
        const key = `${item.documentRef}:${item.page}`;
        if (pageUrls[key]) continue;
        try {
          const url = await api.fetchImage(
            api.documentFileUrl(patientRef, item.documentRef, item.page),
          );
          created.push(url);
          setPageUrls((prev) => ({ ...prev, [key]: url }));
        } catch {
          /* the crop simply does not render; the text evidence still does */
        }
      }
    })();
    return () => created.forEach((url) => URL.revokeObjectURL(url));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [evidence, patientRef]);

  return (
    <aside
      className="fixed inset-y-0 right-0 z-50 w-full max-w-lg overflow-y-auto border-l p-6 shadow-xl"
      style={{ backgroundColor: 'var(--mk-void)', borderColor: 'var(--mk-line-strong)' }}
      role="dialog"
      aria-label="Where this came from"
    >
      <div className="flex items-start justify-between gap-4">
        <Heading level={2}>Where this came from</Heading>
        <Button onClick={onClose}>Close</Button>
      </div>

      {error ? (
        <div className="mt-4">
          <Problem message={error.message} detail={error.detail} />
        </div>
      ) : null}
      {!evidence && !error ? <Spinner label="Opening the source…" /> : null}

      {evidence ? (
        <>
          <div className="mt-4">
            <p className="text-lg font-medium" style={{ color: 'var(--mk-ink-strong)' }}>
              {evidence.displayValue ?? String(evidence.value ?? '')}
            </p>
            <p className="mt-1 font-mono text-xs" style={{ color: 'var(--mk-ink-subtle)' }}>
              {evidence.path}
            </p>
            <div className="mt-2 flex flex-wrap gap-2 text-xs">
              <span
                className="rounded-full px-2 py-0.5"
                style={{
                  backgroundColor: 'var(--mk-status-info-bg)',
                  color: 'var(--mk-status-info-fg)',
                }}
              >
                {ORIGIN_WORDS[evidence.origin ?? ''] ?? `tier: ${evidence.tier}`}
              </span>
              <span style={{ color: 'var(--mk-ink-muted)' }}>
                {evidence.confidenceStatus === 'unavailable'
                  ? 'confidence not measured'
                  : `confidence ${(evidence.confidence ?? 0).toFixed(2)}`}
              </span>
            </div>
          </div>

          <ul className="mt-6 space-y-5">
            {evidence.evidence.map((item, index) => (
              <li key={index}>
                <EvidenceItem
                  item={item}
                  pageUrl={
                    item.documentRef && item.page
                      ? pageUrls[`${item.documentRef}:${item.page}`]
                      : undefined
                  }
                />
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </aside>
  );
}

function EvidenceItem({ item, pageUrl }: { item: BriefEvidence; pageUrl?: string }) {
  return (
    <div className="rounded-lg border p-3" style={{ borderColor: 'var(--mk-line-strong)' }}>
      <p className="text-xs uppercase tracking-wide" style={{ color: 'var(--mk-evidence-ink)' }}>
        {item.sourceType}
        {item.modality ? ` · ${item.modality}` : ''}
        {item.page ? ` · page ${item.page}` : ''}
      </p>

      <p className="mt-2 text-sm italic" style={{ color: 'var(--mk-ink)' }}>
        “{item.verbatim}”
      </p>

      {/* ADR-0012: the reading sits BESIDE the scrawl, with the name of whoever read it. */}
      {item.humanReading ? (
        <p
          className="mt-2 rounded px-2 py-1 text-sm"
          style={{
            backgroundColor: 'var(--mk-status-info-bg)',
            color: 'var(--mk-status-info-fg)',
          }}
        >
          Read as <strong>{item.humanReading}</strong>
          {item.readBy ? ` by ${item.readBy}` : ''}
        </p>
      ) : null}

      {item.priorEncounterRef ? (
        <p className="mt-2 font-mono text-xs" style={{ color: 'var(--mk-ink-muted)' }}>
          carried forward from {item.priorEncounterRef}
        </p>
      ) : null}

      {item.asrConfidence !== null && item.asrConfidence !== undefined ? (
        <p className="mt-2 text-xs" style={{ color: 'var(--mk-ink-muted)' }}>
          heard with confidence {item.asrConfidence.toFixed(2)}
        </p>
      ) : item.modality === 'speech' ? (
        <p className="mt-2 text-xs" style={{ color: 'var(--mk-ink-muted)' }}>
          spoken — no confidence score was measured for this turn
        </p>
      ) : null}

      {item.bbox && pageUrl ? (
        <div className="mt-3">
          <SourceCrop pageUrl={pageUrl} box={item.bbox} />
          <Muted className="mt-1 !text-xs">
            The exact region on the page, drawn from the coordinates the reading was measured
            at.
          </Muted>
        </div>
      ) : item.bbox ? (
        <Muted className="mt-2 !text-xs">Loading the page image…</Muted>
      ) : null}

      {item.handwritten ? (
        <p className="mt-2 text-xs" style={{ color: 'var(--mk-status-warn-fg)' }}>
          Handwritten — never merged into the record without a person reading it.
        </p>
      ) : null}
    </div>
  );
}

export default EvidenceDrawer;
