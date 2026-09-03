/**
 * Upload a prescription or report, and read back what was extracted from it.
 *
 * ⛔ NO OCR HAPPENS IN THIS FILE, OR ANYWHERE IN REACT. `app/modules/documents/pipeline.py` is
 * the single front door, enforced by `tests/test_ocr_has_one_front_door.py`, and the reason is
 * recorded there: a private path around the pipeline skips the consent gate, the size limit
 * and the upload route, which is exactly how three seeded lab reports came to be described as
 * having been through OCR when they had not. This component posts a file and renders states.
 *
 * ⛔ LOW-CONFIDENCE TEXT IS NEVER SHOWN AS CONFIRMED DATA. The API returns two lanes —
 * `pending: true` items are below the confidence threshold or handwritten and have NOT been
 * recorded, and `pending: false` items are document-tier facts that have. They are rendered
 * differently and labelled differently, because a verification screen that shows only the
 * failures teaches a patient that the machine got everything else right.
 *
 * PROCESSING CAN TAKE A WHILE, and the UI says so rather than looking frozen. A degraded photo
 * routes to GOT-OCR2, which is a vision model reading one detected line at a time — measured
 * at roughly 9 seconds per line on the demo hardware. A spinner with no explanation over that
 * reads as a hang.
 */

import { useRef, useState } from 'react';

import { Button, Heading, Muted, Pane, Problem } from '@/design/ui/Surface';
import { ApiError, api, type ExtractedItem, type UploadResult } from '@/lib/api';

type Phase = 'idle' | 'uploading' | 'processing' | 'done' | 'failed';

export interface DocumentUploadProps {
  sessionRef: string;
  onExtracted?: (result: UploadResult) => void;
}

export function DocumentUpload({ sessionRef, onExtracted }: DocumentUploadProps) {
  const input = useRef<HTMLInputElement>(null);
  const [phase, setPhase] = useState<Phase>('idle');
  const [result, setResult] = useState<UploadResult | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [filename, setFilename] = useState('');

  async function send(file: File) {
    setFilename(file.name);
    setError(null);
    setPhase('uploading');
    try {
      // There is no separate "processing" event from the API — the request returns when the
      // pipeline is done — so the phase flips once the bytes are away and the wait begins.
      setPhase('processing');
      const uploaded = await api.upload(sessionRef, file);
      setResult(uploaded);
      setPhase('done');
      onExtracted?.(uploaded);
    } catch (cause) {
      setError(cause as ApiError);
      setPhase('failed');
    }
  }

  // `extracted` carries BOTH lanes, each tagged `pending`. See IngestResult.extracted_items().
  const items: ExtractedItem[] = result?.extracted ?? [];
  const recorded = items.filter((i) => !i.pending);
  const pending = items.filter((i) => i.pending);

  return (
    <Pane>
      <Heading level={2}>Bring your papers</Heading>
      <Muted className="mt-1">
        A prescription, a lab report, a discharge summary — a photo or a PDF. We read it so you
        do not have to remember the names and doses.
      </Muted>

      <input
        ref={input}
        type="file"
        accept="image/*,application/pdf,.heic,.heif"
        className="sr-only"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) void send(file);
          e.target.value = '';
        }}
      />

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <Button
          variant="primary"
          disabled={phase === 'uploading' || phase === 'processing'}
          onClick={() => input.current?.click()}
        >
          {phase === 'idle' || phase === 'failed' ? 'Take a photo or choose a file' : 'Working…'}
        </Button>
        {result ? (
          <span className="text-sm" style={{ color: 'var(--mk-ink-muted)' }}>
            {result.filename} · read by {result.backend}
          </span>
        ) : null}
      </div>

      {phase === 'uploading' || phase === 'processing' ? (
        <div
          className="mt-4 rounded-lg px-4 py-3"
          style={{ backgroundColor: 'var(--mk-status-info-bg)', color: 'var(--mk-status-info-fg)' }}
          role="status"
          aria-live="polite"
        >
          <p className="text-sm font-medium">
            {phase === 'uploading' ? `Sending ${filename}…` : `Reading ${filename}…`}
          </p>
          <p className="mt-1 text-xs">
            A clear scan takes a moment. A photo taken in poor light is read line by line and
            can take a minute or more — this screen is working, not stuck.
          </p>
        </div>
      ) : null}

      {error ? (
        <div className="mt-4">
          <Problem message={error.message} detail={null} />
          <Muted className="mt-2">
            You can try another photo, or simply hand the paper to the doctor — nothing is lost
            either way.
          </Muted>
        </div>
      ) : null}

      {phase === 'done' && result ? (
        <div className="mt-5 space-y-4">
          {recorded.length ? (
            <section>
              <h3 className="text-sm font-semibold" style={{ color: 'var(--mk-ink-strong)' }}>
                Read from your document
              </h3>
              <ul className="mt-2 space-y-1.5">
                {recorded.map((item) => (
                  <li
                    key={item.itemId}
                    className="rounded-lg px-3 py-2 text-sm"
                    style={{ backgroundColor: 'var(--mk-status-ok-bg)', color: 'var(--mk-status-ok-fg)' }}
                  >
                    <span className="font-medium">{item.text}</span>
                    <span className="ml-2 text-xs opacity-80">
                      {item.kind} · page {item.page}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}

          {pending.length ? (
            <section>
              <h3 className="text-sm font-semibold" style={{ color: 'var(--mk-status-warn-fg)' }}>
                Not clear enough to record on its own
              </h3>
              <Muted className="mt-1">
                These were hard to read — usually handwriting. They have <strong>not</strong>{' '}
                been added to your record. A person will check them against the paper.
              </Muted>
              <ul className="mt-2 space-y-1.5">
                {pending.map((item) => (
                  <li
                    key={item.itemId}
                    className="rounded-lg px-3 py-2 text-sm"
                    style={{
                      backgroundColor: 'var(--mk-status-warn-bg)',
                      color: 'var(--mk-status-warn-fg)',
                    }}
                  >
                    <span className="font-medium">{item.text}</span>
                    <span className="ml-2 text-xs opacity-80">needs checking</span>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}

          {!recorded.length && !pending.length ? (
            <Muted>
              We could not find any medicines or results on that page. The doctor can still look
              at it.
            </Muted>
          ) : null}
        </div>
      ) : null}
    </Pane>
  );
}

export default DocumentUpload;
