/**
 * The escalation banner. Deterministic rules in, prominent warning out.
 *
 * ⛔ THE FRONTEND HAS NO OPINION ABOUT WHETHER SOMETHING IS AN EMERGENCY.
 *
 * Every flag rendered here came from `app/redflags/engine.py` walking
 * `data/ontology/redflags.yaml` — 22 rules a clinician can read without reading Python. No
 * model is consulted, here or upstream. This component sorts and displays; it never decides.
 *
 * ⛔ AND IT CANNOT DE-ESCALATE. Invariant 3 says priority rises and never falls, and that
 * guarantee is worthless if the UI can quietly drop a flag. There is deliberately no
 * dismiss, no collapse-by-default and no "hide resolved" here. `worstOf` takes the MAXIMUM
 * of what the backend sent, so a component that receives one immediate flag among nine
 * routine ones cannot render as routine.
 *
 * tests/test_colour_vision_deficiency.py parses this file: it requires the literal words
 * CRITICAL and CAUTION, and that escalation is expressed through `StateGlyph` rather than by
 * colour alone. See that component for the measured reason.
 */

import { StateGlyph, type EscalationState } from '@/design/ui/StateGlyph';
import type { RedFlag } from '@/lib/api';
import { cn } from '@/lib/utils';

/** Backend priority → the state channel. `immediate` and `urgent` are not the same word. */
function stateFor(level: string): EscalationState {
  if (level === 'immediate') return 'critical';
  if (level === 'urgent') return 'caution';
  return 'ok';
}

/** The heading each level gets, in words, in caps, so it reads at a glance. */
const HEADLINE: Record<EscalationState, string> = {
  critical: 'CRITICAL — needs attention now',
  caution: 'CAUTION — move this patient up the queue',
  ok: 'No red flags fired',
  info: 'Evidence',
};

/**
 * The worst level present. MAX, never first-wins and never last-wins.
 *
 * An earlier shape of this took `flags[0].level`, which meant the banner said whatever the
 * backend happened to order first. Escalation is a maximum by definition (Invariant 3), so
 * it is computed as one here.
 */
export function worstOf(flags: RedFlag[]): EscalationState {
  if (flags.some((f) => f.level === 'immediate')) return 'critical';
  if (flags.some((f) => f.level === 'urgent')) return 'caution';
  return 'ok';
}

export interface RedFlagBannerProps {
  flags: RedFlag[];
  className?: string;
}

export function RedFlagBanner({ flags, className }: RedFlagBannerProps) {
  const worst = worstOf(flags);

  if (flags.length === 0) {
    // Rendered rather than omitted. "Nothing fired" and "we never checked" look identical
    // when the component simply disappears, and only one of those is reassuring.
    return (
      <div
        className={cn('mk-pane flex items-center gap-3 px-4 py-3', className)}
        data-testid="redflag-banner"
      >
        <StateGlyph state="ok" />
        <p className="text-sm" style={{ color: 'var(--mk-ink-muted)' }}>
          {HEADLINE.ok}. The deterministic rule set was evaluated and none matched.
        </p>
      </div>
    );
  }

  const tint =
    worst === 'critical' ? 'var(--mk-status-alert-bg)' : 'var(--mk-status-warn-bg)';
  const ink =
    worst === 'critical' ? 'var(--mk-status-alert-fg)' : 'var(--mk-status-warn-fg)';

  return (
    <section
      className={cn('rounded-xl border px-4 py-4', className)}
      style={{ backgroundColor: tint, borderColor: 'var(--mk-line-strong)' }}
      aria-live="assertive"
      data-testid="redflag-banner"
    >
      <div className="flex flex-wrap items-center gap-3">
        <StateGlyph state={worst} />
        <h2 className="text-base font-semibold tracking-tight" style={{ color: ink }}>
          {HEADLINE[worst]}
        </h2>
        <span className="text-xs" style={{ color: 'var(--mk-ink-muted)' }}>
          {flags.length} rule{flags.length === 1 ? '' : 's'} fired
        </span>
      </div>

      <ul className="mt-3 space-y-2">
        {flags.map((flag) => (
          <li key={flag.ruleId} className="flex flex-wrap items-start gap-2 text-sm">
            <StateGlyph state={stateFor(flag.level)} iconOnly />
            <div className="min-w-0 flex-1">
              <p className="font-medium" style={{ color: 'var(--mk-ink-strong)' }}>
                {flag.label}
              </p>
              {flag.rationale ? (
                <p className="mt-0.5 leading-relaxed" style={{ color: 'var(--mk-ink-muted)' }}>
                  {flag.rationale}
                </p>
              ) : null}
              <p className="mt-0.5 text-xs" style={{ color: 'var(--mk-ink-subtle)' }}>
                {flag.ruleId} · deterministic rule, not a model judgement
              </p>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

export default RedFlagBanner;
