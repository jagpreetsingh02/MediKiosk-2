/**
 * The hero's handwriting animation, verified against a real browser.
 *
 * WHY THIS IS WORTH A SCRIPT. `HandwritingText` depends on two things this repo does not
 * control: opentype.js from a CDN, and a TTF fetched cross-origin. If either goes away the
 * component degrades to a plain <span> — correct behaviour, and completely silent. `tsc` and
 * `vite build` both stay green while the hero quietly stops being the hero.
 *
 * It also pins the failure that a build cannot see: `height="1.15em"` resolves against the
 * element's own font-size, so the word rendered at 18px under a 60px headline until the
 * wrapper was given a matching type scale. The width assertion below is what catches that.
 *
 * Not part of `make check` — it needs a running dev server. Run it yourself:
 *     npm run dev            # in one shell
 *     npm run e2e:hero       # in another
 */

import { chromium } from 'playwright';

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 820 } });
const errors = [];
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
await page.goto(process.env.HERO_URL ?? 'http://127.0.0.1:5173', { waitUntil: 'domcontentloaded' });

// The <span> fallback becomes an <svg> only once the TTF has been fetched and parsed.
await page.waitForSelector('svg[role="img"]', { timeout: 25000 });

const probe = () => page.evaluate(() => {
  const svg = document.querySelector('svg[role="img"]');
  if (!svg) return null;
  const strokes = [...svg.querySelectorAll('path[stroke="currentColor"]')];
  const fill = svg.querySelector('path[fill="currentColor"]');
  // getComputedStyle, NOT p.style: the inline style holds the TARGET, which React sets to 0
  // as the transition starts. Only the computed style reports where the pen actually is.
  const prog = strokes.map((p) => {
    const cs = getComputedStyle(p);
    const off = parseFloat(cs.strokeDashoffset) || 0;
    const len = parseFloat(cs.strokeDasharray) || 0;
    return len > 0 ? Math.max(0, Math.min(1, 1 - off / len)) : 0;
  });
  return {
    word: svg.getAttribute('aria-label'),
    contours: strokes.length,
    progress: +(prog.reduce((a, b) => a + b, 0) / (prog.length || 1)).toFixed(3),
    fillOpacity: +(parseFloat(fill && getComputedStyle(fill).opacity) || 0).toFixed(2),
    width: Math.round(svg.getBoundingClientRect().width),
  };
});

// Wait for a FRESH word to begin, so we watch one full stroke from the start rather than
// joining one already in progress — the cycle timer starts at mount, the font arrives later.
const startWord = (await probe()).word;
let cur = startWord;
for (let i = 0; i < 120 && cur === startWord; i++) {
  await page.waitForTimeout(50);
  cur = (await probe()).word;
}

const samples = [];
const t0 = Date.now();
let shotMid = false;
for (let i = 0; i < 22; i++) {
  const p = await probe();
  if (p.word !== cur) break;                       // stop at the next swap
  samples.push({ t: Date.now() - t0, ...p });
  if (!shotMid && p.progress > 0.25 && p.progress < 0.8) {
    await page.screenshot({ path: '/tmp/medikiosk-hero-mid.png' });
    shotMid = true;
  }
  await page.waitForTimeout(120);
}

const first = samples[0], last = samples[samples.length - 1];
const peak = samples.reduce((a, b) => (b.progress > a.progress ? b : a), first);
console.log(`watched "${cur}" — ${first.contours} contours\n`);
for (const s of samples.filter((_, i) => i % 3 === 0 || i === samples.length - 1)) {
  const bar = '#'.repeat(Math.round(s.progress * 40)).padEnd(40, '.');
  console.log(`  +${String(s.t).padStart(4)}ms  ${bar} ${(s.progress * 100).toFixed(0).padStart(3)}%  ink ${s.fillOpacity}`);
}

// Settled shot: let this word finish inking.
await page.waitForTimeout(400);
await page.screenshot({ path: '/tmp/medikiosk-hero-settled.png' });

console.log('\nerrors:', errors.length ? errors : 'none');
const ok =
  first.contours > 5 &&
  first.progress < 0.5 &&        // caught it near the start
  peak.progress > 0.97 &&        // the pen finished the word
  peak.fillOpacity > 0.9 &&      // and it inked in
  last.width > 200 &&            // rendered at heading scale, not 16px
  errors.length === 0;
console.log(ok
  ? `\nPASS — pen went ${(first.progress * 100).toFixed(0)}% -> ${(peak.progress * 100).toFixed(0)}%, inked to ${peak.fillOpacity}, ${peak.width}px wide.`
  : '\nFAIL — see samples above.');
await browser.close();
process.exit(ok ? 0 : 1);
