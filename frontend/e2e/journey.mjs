/**
 * The demo journey, driven through a real browser against the real API.
 *
 * WHY THIS EXISTS SEPARATELY FROM THE PYTHON SMOKE SCRIPT. That one proves the API chain:
 * consent, the interview, OCR, commit, review transitions. It says nothing about whether a
 * human can actually reach any of it. A route that renders a blank page, a fetch that never
 * fires because a guard redirected, a component that throws on a field that is null in
 * practice — all of those pass every unit test and every API check.
 *
 * So this walks the product the way a person does: click the landing page, sign in, read the
 * record, open a consultation, answer a question. Console errors and page errors are collected
 * and fail the run, because a screen that renders while throwing is not a working screen.
 *
 * Not part of `make check` — it needs both servers up.
 *     ./start.sh                       # or the two dev servers
 *     node e2e/journey.mjs
 */

import { chromium } from 'playwright';

const BASE = process.env.MK_URL ?? 'http://localhost:10100';
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1360, height: 900 } });

const errors = [];
page.on('console', (m) => {
  if (m.type() === 'error') errors.push(m.text());
});
page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));

const results = [];
function ok(label, passed, extra = '') {
  results.push(passed);
  console.log(`  ${passed ? 'PASS' : 'FAIL'}  ${label}${extra ? ` — ${extra}` : ''}`);
}

async function shot(name) {
  await page.screenshot({ path: `e2e/shots/${name}.png`, fullPage: false });
}

// ---------------------------------------------------------------- landing
console.log('BROWSER 1  landing');
await page.goto(BASE, { waitUntil: 'networkidle' });
ok('hero renders', await page.getByRole('heading', { level: 1 }).first().isVisible());
ok(
  'both entry points present',
  (await page.getByRole('button', { name: /start intake/i }).count()) > 0 &&
    (await page.getByRole('button', { name: /physician sign-in/i }).count()) > 0,
);
ok('demo path is labelled as demo', (await page.getByText(/Try the demo patient/i).count()) > 0);
await shot('01-landing');

// ---------------------------------------------------------------- patient sign-in
console.log('BROWSER 2  patient identity');
await page.getByRole('button', { name: /Try the demo patient/i }).click();
await page.waitForURL('**/patient/sign-in');
ok('mock issuer is named on the sign-in screen', (await page.getByText(/mock issuer/i).count()) > 0);
await shot('02-signin');

await page.getByRole('button', { name: /^Continue$/ }).click();
await page.waitForURL((u) => u.pathname === '/patient', { timeout: 30000 });

// ---------------------------------------------------------------- longitudinal record
console.log('BROWSER 3  existing record loads');
await page.waitForSelector('text=/Previous visits/i', { timeout: 30000 });
const heading = await page.getByRole('heading', { level: 1 }).first().textContent();
ok('greets the known patient', /Demo Patient/i.test(heading ?? ''), heading?.trim());
ok('demo band is permanent', (await page.getByText(/DEMO IDENTITY/i).count()) > 0);
const visits = await page
  .locator('text=Previous visits')
  .locator('xpath=preceding-sibling::p[1]')
  .textContent()
  .catch(() => null);
ok('prior encounters shown from the live database', Number(visits ?? 0) > 0, `${visits} visits`);
ok(
  'record content shown with provenance wording, not "is taking"',
  (await page.getByText(/what the record has SEEN/i).count()) > 0,
);
await shot('03-patient-home');

// ---------------------------------------------------------------- consultation
console.log('BROWSER 4  new consultation');
await page.getByRole('button', { name: /Start new consultation/i }).click();
await page.waitForURL('**/patient/consultation');
await page.waitForSelector('text=/Before we start/i', { timeout: 20000 });
ok('consent gate is first', true);
await shot('04-consent');

await page.getByRole('button', { name: /I agree — start/i }).click();
await page.waitForSelector('[class*="mk-pane"] h1', { timeout: 30000 });
const prompt = await page.locator('h1').last().textContent();
ok('first question rendered', Boolean(prompt && prompt.trim().length > 3), prompt?.trim());
ok('voice control offered', (await page.getByRole('button', { name: /Answer by speaking/i }).count()) > 0);
await shot('05-question');

// Answer it, and confirm the interview advances. The first question is `open_text`, which
// renders a textarea and a Continue button rather than option buttons — so handle both
// shapes rather than assuming the one this ontology happens to ask first.
const before = prompt;
const textarea = page.locator('div.mk-pane textarea');
if (await textarea.count()) {
  await textarea.fill('burning pain in my stomach after eating');
  await page.getByRole('button', { name: /^Continue$/ }).click();
} else {
  await page
    .locator('div.mk-pane button')
    .filter({ hasNotText: /Answer by speaking|Back|rather not say|Continue/ })
    .first()
    .click();
}
await page.waitForTimeout(3000);
const after = await page.locator('h1').last().textContent();
ok('answering advances the interview', after !== before, `${before?.slice(0, 28)} -> ${after?.slice(0, 28)}`);
await shot('06-advanced');

// ---------------------------------------------------------------- clinician
console.log('BROWSER 5  clinician workspace');
await page.goto(`${BASE}/clinician/sign-in`, { waitUntil: 'networkidle' });
await page.getByRole('button', { name: /Enter workspace/i }).click();
await page.waitForURL((u) => u.pathname === '/clinician', { timeout: 30000 });
await page.waitForSelector('text=/Intake queue/i', { timeout: 20000 });
ok('queue renders', true);
// Wait for the FETCH, not just the heading. Counting straight after the heading appears
// races the request and reports an empty queue that is merely not-yet-loaded.
await page
  .locator('li.mk-pane')
  .first()
  .waitFor({ timeout: 30000 })
  .catch(() => {});
const queued = await page.locator('li.mk-pane').count();
ok('queue lists waiting sessions', queued > 0, `${queued} entries`);
await shot('07-queue');

// ---------------------------------------------------------------- record + fact review
console.log('BROWSER 6  longitudinal record and fact review');
await page.getByRole('button', { name: /Open record/i }).click();
await page.waitForURL('**/clinician/patients/**', { timeout: 30000 });
await page.waitForSelector('text=/Confirmed encounters/i', { timeout: 30000 });
ok('confirmed encounters listed', (await page.locator('li.mk-pane').count()) > 0);
ok('timeline rendered', (await page.getByText(/^Timeline$/).count()) > 0);
ok('medication history rendered', (await page.getByText(/Medication history/i).count()) > 0);
await shot('08-record');

await page.getByRole('button', { name: /Review facts/i }).first().click();
await page.waitForURL('**/encounters/**', { timeout: 30000 });
await page.waitForSelector('text=/Review each fact/i', { timeout: 30000 });
const rows = await page.locator('li.mk-pane').count();
ok('reviewable facts listed', rows > 0, `${rows} facts`);
ok('red-flag banner present', (await page.locator('[data-testid="redflag-banner"]').count()) > 0);
ok(
  'confirm / edit / reject all offered',
  (await page.getByRole('button', { name: /^Confirm$/ }).count()) > 0 &&
    (await page.getByRole('button', { name: /^Edit$/ }).count()) > 0 &&
    (await page.getByRole('button', { name: /^Reject$/ }).count()) > 0,
);
await shot('09-fact-review');

// Provenance drawer.
await page.getByRole('button', { name: /where did this come from/i }).first().click();
await page.waitForSelector('[role="dialog"]', { timeout: 20000 });
ok('provenance drawer opens', true);
// Same again: the drawer fetches its evidence after it opens.
await page
  .locator('[role="dialog"] p >> text=/[“"]/')
  .first()
  .waitFor({ timeout: 30000 })
  .catch(() => {});
const verbatim = await page.locator('[role="dialog"]').innerText();
ok('drawer shows a verbatim source', /[“"]/.test(verbatim) && /came from|evidence|utterance|document/i.test(verbatim),
   verbatim.split('\n').filter(Boolean).slice(2, 4).join(' | ').slice(0, 90));
await shot('10-provenance');
await page.getByRole('button', { name: /^Close$/ }).click();

// Confirm one fact, through the real endpoint.
const confirmBtn = page.getByRole('button', { name: /^Confirm$/ }).first();
await confirmBtn.click();
await page.waitForTimeout(2500);
ok('a fact can be confirmed in the UI', (await page.getByText(/^Confirmed$/).count()) > 0);
await shot('11-confirmed');

console.log('');
console.log(`console/page errors: ${errors.length}`);
errors.slice(0, 8).forEach((e) => console.log(`   ! ${e.slice(0, 600)}`));

await browser.close();
const passed = results.filter(Boolean).length;
console.log(`${passed}/${results.length} browser steps passed`);
process.exit(passed === results.length && errors.length === 0 ? 0 : 1);
