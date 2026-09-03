/**
 * The shared material. One visual language, two densities — ADR-0013.
 *
 * The kiosk and the physician workspace are not different themes. They are the same palette,
 * the same pane, the same type, at different information densities: a patient reads one
 * question at arm's length, a clinician scans forty facts in ninety seconds. `data-surface`
 * is what selects the density block in `theme.css`, and it is set once, at the top of each
 * route, rather than being threaded through every component as a prop.
 *
 * Everything here paints from `--mk-*` tokens. `scripts/check_no_raw_colours.py` fails the
 * build on a hex literal outside the two exempt folders, and this folder is not one of them.
 */

import type { ReactNode } from 'react';

import { cn } from '@/lib/utils';

export function Surface({
  kind,
  children,
  className,
}: {
  kind: 'kiosk' | 'clinical';
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      data-surface={kind}
      className={cn('min-h-screen', className)}
      style={{ backgroundColor: 'var(--mk-void)', color: 'var(--mk-ink)' }}
    >
      {children}
    </div>
  );
}

export function Pane({
  children,
  className,
  as: Tag = 'div',
}: {
  children: ReactNode;
  className?: string;
  as?: 'div' | 'section' | 'article' | 'li';
}) {
  return <Tag className={cn('mk-pane p-4', className)}>{children}</Tag>;
}

export function Heading({
  children,
  level = 2,
  className,
}: {
  children: ReactNode;
  level?: 1 | 2 | 3;
  className?: string;
}) {
  const Tag = (['h1', 'h2', 'h3'] as const)[level - 1];
  const size = level === 1 ? 'text-2xl' : level === 2 ? 'text-lg' : 'text-sm';
  return (
    <Tag
      className={cn(size, 'font-semibold tracking-tight', className)}
      style={{ color: 'var(--mk-ink-strong)' }}
    >
      {children}
    </Tag>
  );
}

export function Muted({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <p className={cn('text-sm leading-relaxed', className)} style={{ color: 'var(--mk-ink-muted)' }}>
      {children}
    </p>
  );
}

type ButtonProps = {
  children: ReactNode;
  onClick?: () => void;
  type?: 'button' | 'submit';
  variant?: 'primary' | 'quiet' | 'danger';
  disabled?: boolean;
  className?: string;
  title?: string;
};

/**
 * One hover behaviour everywhere: the surface brightens. Nothing lifts, nothing scales.
 * ADR-0013's entire motion vocabulary is two durations, and a clinical control that moves
 * under the cursor is a control that is harder to hit.
 */
export function Button({
  children,
  onClick,
  type = 'button',
  variant = 'quiet',
  disabled,
  className,
  title,
}: ButtonProps) {
  const palette =
    variant === 'primary'
      ? { backgroundColor: 'var(--mk-accent-ink)', color: 'var(--mk-void)' }
      : variant === 'danger'
        ? { backgroundColor: 'var(--mk-status-alert-bg)', color: 'var(--mk-status-alert-fg)' }
        : { backgroundColor: 'transparent', color: 'var(--mk-ink)' };

  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={cn(
        'inline-flex items-center justify-center gap-2 rounded-lg border px-4 py-2',
        'text-sm font-medium transition-colors',
        'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2',
        'disabled:cursor-not-allowed disabled:opacity-50',
        className,
      )}
      style={{
        ...palette,
        borderColor: variant === 'quiet' ? 'var(--mk-line-strong)' : 'transparent',
        transitionDuration: 'var(--mk-quick)',
        outlineColor: 'var(--mk-accent)',
      }}
    >
      {children}
    </button>
  );
}

/**
 * The permanent reminder that this identity is not real.
 *
 * `/about` says it, `app/auth/mock_idp.py` says it in every token, and the brief requires it
 * on screen: nobody watching a demo should be able to mistake the mock issuer for a live
 * ABDM connection. So it is rendered on every authenticated surface, not just at sign-in.
 */
export function DemoBand({ what = 'identity' }: { what?: string }) {
  return (
    <div
      className="w-full px-4 py-1.5 text-center text-xs font-medium"
      style={{ backgroundColor: 'var(--mk-status-warn-bg)', color: 'var(--mk-status-warn-fg)' }}
      role="note"
    >
      DEMO {what.toUpperCase()} — mock ABDM issuer, synthetic records. Not a real ABHA
      integration and not real patient data.
    </div>
  );
}

export function Spinner({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-3 py-6" role="status" aria-live="polite">
      <span
        className="h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent"
        style={{ color: 'var(--mk-accent)' }}
        aria-hidden="true"
      />
      <span className="text-sm" style={{ color: 'var(--mk-ink-muted)' }}>
        {label}
      </span>
    </div>
  );
}

/** An error a patient or clinician can act on. Never a stack trace, never a status code. */
export function Problem({ message, detail }: { message: string; detail?: string | null }) {
  return (
    <div
      className="rounded-lg border px-4 py-3"
      style={{
        backgroundColor: 'var(--mk-status-alert-bg)',
        borderColor: 'var(--mk-line-strong)',
        color: 'var(--mk-status-alert-fg)',
      }}
      role="alert"
    >
      <p className="text-sm font-medium">{message}</p>
      {detail ? (
        // The technical cause is for the clinician surface and the jury drawer. It is never
        // the primary message: "TypeError: Failed to fetch" is not something a patient can act on.
        <p className="mt-1 text-xs opacity-80">{detail}</p>
      ) : null}
    </div>
  );
}
