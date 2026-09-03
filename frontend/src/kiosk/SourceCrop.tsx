/**
 * The patch of paper a document-derived fact was actually read from.
 *
 * ⛔ BUG 3 LIVED HERE, AND `tests/test_bbox_geometry.py` PINS THE FIX IN THE SOURCE.
 *
 * The original implementation pulled the crop upward with a NEGATIVE PERCENTAGE TOP MARGIN.
 * Percentage margins resolve against the containing block's **WIDTH** — never its height —
 * so on any container that is not square the vertical offset was wrong by the aspect ratio,
 * and every crop landed on a different part of the page. Usually blank paper.
 *
 * That is a bug a rendering test cannot catch. "Does a crop appear?" passes with it present:
 * the element is there, correctly sized, showing the wrong region. So the test asserts the
 * TECHNIQUE instead, and fails the build if that margin property ever returns to this file —
 * by a plain substring scan, comments included, which is why this note spells the trap out in
 * words rather than naming the property.
 *
 * The technique that is correct on both axes:
 *
 *   backgroundSize      scale the page so the box fills the frame: 100/w by 100/h
 *   backgroundPositionX  ┐ CSS percentage background positioning aligns the p% point of the
 *   backgroundPositionY  ┘ IMAGE with the p% point of the CONTAINER, so the offset for a
 *                         region starting at x of width w is x / (1 - w). Each axis is
 *                         computed from its own dimensions and set independently — which is
 *                         the whole difference from the margin approach.
 *   aspectRatio          the frame takes the REAL pixel shape of the region, so a wide
 *                        prescription line is not squashed into a square box.
 *
 * Everything here is derived from the normalised bbox the API returns. Nothing is guessed,
 * and a box that would be degenerate is refused rather than rendered somewhere plausible —
 * `render.py` states the standard: a box drawn in the wrong place is worse than no box,
 * because it tells a physician the system read a line it did not read.
 */

import { cn } from '@/lib/utils';

export interface CropBox {
  /** Normalised page coordinates, origin top-left, each in [0, 1]. */
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface SourceCropProps {
  /** URL of the rendered page PNG. Must be the SAME page the box was measured against. */
  pageUrl: string;
  box: CropBox;
  /** Real pixel size of that page, for the crop's true aspect. */
  pageWidth?: number;
  pageHeight?: number;
  className?: string;
  alt?: string;
}

/**
 * Offset for one axis, as a CSS percentage.
 *
 * `start / (1 - extent)` is the standard percentage-background-position identity. When the
 * region is the full extent the denominator collapses, and any offset is equally correct, so
 * it returns 0 rather than dividing by zero.
 */
function axisOffset(start: number, extent: number): string {
  const room = 1 - extent;
  if (room <= 0.0001) return '0%';
  const clamped = Math.min(Math.max(start / room, 0), 1);
  return `${(clamped * 100).toFixed(4)}%`;
}

export function SourceCrop({
  pageUrl,
  box,
  pageWidth,
  pageHeight,
  className,
  alt = 'The region of the uploaded document this reading came from',
}: SourceCropProps) {
  // A zero-extent box cannot be shown. Refusing is the honest answer; scaling it up to
  // something visible would invent a region.
  const width = Math.min(Math.max(box.width, 0), 1);
  const height = Math.min(Math.max(box.height, 0), 1);
  if (width <= 0 || height <= 0) {
    return (
      <p className="text-xs" style={{ color: 'var(--mk-ink-subtle)' }}>
        No usable region was recorded for this reading.
      </p>
    );
  }

  // The crop's true shape in pixels. Falls back to the box's own ratio when the page size is
  // unknown, which is still better than inheriting the container's shape.
  const pixelAspect =
    pageWidth && pageHeight
      ? (width * pageWidth) / (height * pageHeight)
      : width / height;

  return (
    <div
      role="img"
      aria-label={alt}
      className={cn('w-full overflow-hidden rounded-md border bg-white', className)}
      style={{
        borderColor: 'var(--mk-line)',
        aspectRatio: `${pixelAspect}`,
        backgroundImage: `url("${pageUrl}")`,
        backgroundRepeat: 'no-repeat',
        backgroundSize: `${100 / width}% ${100 / height}%`,
        backgroundPositionX: axisOffset(box.x, width),
        backgroundPositionY: axisOffset(box.y, height),
      }}
      data-testid="source-crop"
    />
  );
}

export default SourceCrop;
