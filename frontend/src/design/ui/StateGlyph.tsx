/**
 * The escalation glyph — a SHAPE and a WORD, so colour is never the only channel.
 *
 * ⛔ tests/test_colour_vision_deficiency.py PARSES THIS FILE. It reads the `case` blocks
 * below, extracts each `d="..."`, and fails the build if the two silhouettes are the same
 * path or if `STATE_LABEL` loses either word.
 *
 * WHY, measured rather than asserted. Caution is amber (~43°) and critical is rose (~350°).
 * To normal trichromatic vision they are unmistakable. Under deuteranopia — roughly 1 in 12
 * men — the long-wavelength end compresses and pulls both toward a duller yellow-brown: ΔE
 * falls from 63.8 to 21.9 against a confusability floor near 10. So the pair survives, but
 * it loses two thirds of its margin, and that margin was the only channel carrying the
 * difference between "keep an eye on this" and "interrupt triage now".
 *
 * The fix is not to move the hues, which would break the semantic mapping every clinician
 * already knows. It is to stop making colour carry the meaning alone:
 *
 *   1. SHAPE     an octagon and a triangle are distinguishable in monochrome, at 12px, on a
 *                bad projector, and to someone with total achromatopsia.
 *   2. WORD      "Critical" and "Caution" are unambiguous with no colour vision at all.
 *
 * A glyph without a word is a puzzle, not a warning — so `StateGlyph` renders both by
 * default and `iconOnly` is opt-in for the rare case where the word is already adjacent.
 */

import { cn } from '@/lib/utils';

export type EscalationState = 'critical' | 'caution' | 'ok' | 'info';

/** The word for each state. The CVD suite asserts the first two verbatim. */
export const STATE_LABEL: Record<EscalationState, string> = {
  critical: 'Critical',
  caution: 'Caution',
  ok: 'Clear',
  info: 'Evidence',
};

/** Which token pair paints each state. Never a raw colour literal — see theme.css. */
const STATE_TOKENS: Record<EscalationState, { fg: string; bg: string }> = {
  critical: { fg: 'var(--mk-status-alert-fg)', bg: 'var(--mk-status-alert-bg)' },
  caution: { fg: 'var(--mk-status-warn-fg)', bg: 'var(--mk-status-warn-bg)' },
  ok: { fg: 'var(--mk-status-ok-fg)', bg: 'var(--mk-status-ok-bg)' },
  info: { fg: 'var(--mk-status-info-fg)', bg: 'var(--mk-status-info-bg)' },
};

/**
 * The silhouette for a state. Deliberately four different outlines, not one shape recoloured.
 *
 * `critical` is an octagon (the stop-sign silhouette, the most urgent shape in common use).
 * `caution` is a triangle. `ok` is a circle. `info` is a rounded square. At 12px in
 * monochrome each is still identifiable by outline alone.
 *
 * ⛔ THE PATHS ARE WRITTEN AS LITERAL `d="..."` INSIDE EACH `case`, AND THAT IS LOAD-BEARING.
 * The CVD suite splits this source on `case '<state>':`, takes everything up to the next
 * `case `, and pulls the first `d="..."` out of it. Returning a path string from a lookup
 * table would be tidier to read and completely invisible to that check — which is the point
 * of the check, since the failure it guards is two states quietly drawing one shape.
 */
function glyphFor(state: EscalationState) {
  switch (state) {
    case 'critical':
      // Octagon: eight cut corners, an explicit closed polygon.
      return <path d="M7 1.5 L13 1.5 L18.5 7 L18.5 13 L13 18.5 L7 18.5 L1.5 13 L1.5 7 z" />;
    case 'caution':
      // Triangle, apex up, closed.
      return <path d="M10 2 L18.5 17.5 L1.5 17.5 z" />;
    case 'ok':
      // Circle.
      return <path d="M10 2 a8 8 0 1 0 0.01 0 z" />;
    case 'info':
    default:
      // Rounded square.
      return (
        <path d="M4 2.5 h12 a2 2 0 0 1 2 2 v11 a2 2 0 0 1 -2 2 h-12 a2 2 0 0 1 -2 -2 v-11 a2 2 0 0 1 2 -2 z" />
      );
  }
}

export interface StateGlyphProps {
  state: EscalationState;
  /** Suppress the word. Only when the state is already named immediately beside it. */
  iconOnly?: boolean;
  className?: string;
}

export function StateGlyph({ state, iconOnly = false, className }: StateGlyphProps) {
  const tokens = STATE_TOKENS[state];
  const label = STATE_LABEL[state];

  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium',
        className,
      )}
      style={{ backgroundColor: tokens.bg, color: tokens.fg }}
    >
      <svg
        viewBox="0 0 20 20"
        width="13"
        height="13"
        aria-hidden="true"
        className="shrink-0"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinejoin="round"
      >
        {glyphFor(state)}
      </svg>
      {/* The word is present in the accessibility tree even when visually suppressed, so a
          screen reader never receives a bare icon. */}
      <span className={iconOnly ? 'sr-only' : undefined}>{label}</span>
    </span>
  );
}

export default StateGlyph;
