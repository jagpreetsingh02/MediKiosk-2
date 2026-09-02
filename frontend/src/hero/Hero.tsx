import { HandwritingText } from '@/components/ui/handwriting-text';

/**
 * The landing hero.
 *
 * ⛔ THE ROTATING LINE IS PRODUCT COPY, AND INVARIANT 1 APPLIES TO IT.
 *
 * MediKiosk produces a clinical *history* and never an assessment. A hero that implies
 * otherwise is the cheapest possible way to break the promise the whole architecture is
 * built around, so "Never a diagnosis." is one of the phrases in the rotation rather than
 * small print underneath. If you edit `CLAIMS`, keep it there.
 *
 * Each phrase is grammatically standalone on purpose. Cycling text that has to complete a
 * sentence from the heading above reads correctly for one entry and awkwardly for the rest;
 * these are independent claims, so every frame is a sentence.
 *
 * Colour literals are fine in this folder — `scripts/check_no_raw_colours.py` exempts
 * `frontend/src/hero/`. They are NOT fine elsewhere. When the `--mk-*` palette lands in
 * `src/design/theme.css`, these become tokens.
 */

/** The rotating claims. Standalone sentences; see the note above before editing. */
const CLAIMS = [
  'Source-linked.',
  "In the patient's words.",
  'Ready before you are.',
  'Never a diagnosis.',
];

export interface HeroProps {
  /** Patient-side entry. Left unwired until the intake route exists. */
  onStartIntake?: () => void;
  /** Clinician-side entry. Same. */
  onPhysicianSignIn?: () => void;
}

export function Hero({ onStartIntake, onPhysicianSignIn }: HeroProps) {
  return (
    <section className="relative isolate overflow-hidden bg-white dark:bg-zinc-950">
      {/* A single soft wash behind the type. Decorative only, hidden from assistive tech. */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-x-0 -top-40 -z-10 transform-gpu blur-3xl"
      >
        <div className="mx-auto aspect-[1155/678] w-[72rem] max-w-none bg-gradient-to-tr from-emerald-200 to-teal-100 opacity-40 dark:from-emerald-900 dark:to-teal-950 dark:opacity-25" />
      </div>

      <div className="mx-auto max-w-4xl px-6 py-24 sm:py-32 lg:py-40">
        <div className="flex flex-col items-center text-center">
          <p className="mb-6 inline-flex items-center gap-2 rounded-full border border-emerald-200 bg-emerald-50 px-4 py-1.5 text-xs font-medium tracking-wide text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-300">
            All India Institute of Ayurveda · Ministry of Ayush
          </p>

          <h1 className="max-w-3xl text-4xl font-bold leading-tight tracking-tight text-zinc-900 sm:text-5xl lg:text-6xl dark:text-zinc-50">
            The history is taken
            <br />
            before the consultation.
          </h1>

          {/*
            The rotating line is its own block rather than an inline span inside the h1: the
            SVG's width changes with each word's glyph box, and inside the heading that
            reflowed the whole line on every cycle. Centred in a flex row, a width change
            just re-centres and nothing around it moves.

            ⛔ THE TYPE SCALE HERE IS LOAD-BEARING. `height="1.15em"` resolves against THIS
            element's font-size, not the heading's. Without the text-* classes below the
            word renders at 1.15 × 16px — a speck under a 60px headline. Keep this scale in
            step with the h1 above.
          */}
          <div className="mt-4 flex min-h-[1.4em] items-center justify-center text-4xl sm:text-5xl lg:text-6xl">
            <HandwritingText
              words={CLAIMS}
              className="text-emerald-700 dark:text-emerald-400"
              height="1.15em"
            />
          </div>

          <p className="mt-6 max-w-xl text-base leading-relaxed text-zinc-600 sm:text-lg dark:text-zinc-400">
            A patient answers in their own language, at their own pace. The physician opens a
            structured history where every fact carries the sentence or document region it came
            from — and decides what it means.
          </p>

          <div className="mt-10 flex flex-col gap-3 sm:flex-row">
            <button
              type="button"
              onClick={onStartIntake}
              className="rounded-lg bg-emerald-700 px-6 py-3 text-sm font-semibold text-white transition-colors hover:bg-emerald-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-700 dark:bg-emerald-600 dark:hover:bg-emerald-500"
            >
              Start intake
            </button>
            <button
              type="button"
              onClick={onPhysicianSignIn}
              className="rounded-lg border border-zinc-300 px-6 py-3 text-sm font-semibold text-zinc-900 transition-colors hover:bg-zinc-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-zinc-900 dark:border-zinc-700 dark:text-zinc-100 dark:hover:bg-zinc-900 dark:focus-visible:outline-zinc-100"
            >
              Physician sign-in
            </button>
          </div>

          <p className="mt-8 text-xs text-zinc-500 dark:text-zinc-500">
            Produces a clinical history for a physician to review. It does not diagnose.
          </p>
        </div>
      </div>
    </section>
  );
}

export default Hero;
