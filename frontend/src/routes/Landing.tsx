/**
 * The front door. The hero, unchanged, plus the three ways into the product.
 *
 * ⛔ THE HERO IS NOT MODIFIED. `Hero.tsx` already exposes `onStartIntake` and
 * `onPhysicianSignIn` precisely so routes could be attached without touching it, and its
 * header says the rotating claim line is product copy under Invariant 1. This file supplies
 * the handlers and nothing else.
 *
 * The third path — the demo identity — lives BELOW the hero rather than as a third button
 * inside it, for the same reason: adding a button would mean editing a file whose comment
 * asks not to be edited casually, to say something the hero is not for saying.
 *
 * ⚠️ `/sign-in` and `/register` are NOT offered here. `app/api/routes_account.py` is an
 * explicit stub — it stores nothing and refuses every attempt in the same words — and putting
 * it on the front door would present a fake login as the real one. The real identity path in
 * this build is the mock ABHA issuer, labelled as mock everywhere it appears.
 */

import { useNavigate } from 'react-router-dom';

import Hero from '@/hero/Hero';
import { Button, Muted } from '@/design/ui/Surface';
import { DEMO_ABHA, DEMO_OTP } from '@/routes/PatientLogin';

export default function Landing() {
  const navigate = useNavigate();

  return (
    <main className="min-h-screen" style={{ backgroundColor: 'var(--mk-void)' }}>
      <Hero
        onStartIntake={() => navigate('/patient/sign-in')}
        onPhysicianSignIn={() => navigate('/clinician/sign-in')}
      />

      <section className="mx-auto max-w-4xl px-6 pb-24">
        <div className="mk-pane p-6">
          <h2
            className="text-sm font-semibold tracking-tight"
            style={{ color: 'var(--mk-ink-strong)' }}
          >
            Just looking?
          </h2>
          <Muted className="mt-2">
            Open the kiosk as <strong>Demo Patient</strong>, a synthetic record with two prior
            visits, a prescription and a lab report already on file — so the longitudinal
            history has something in it to show. Every identity in this build comes from a mock
            ABDM issuer and every record is synthetic; nothing here is a real ABHA integration
            and no real patient data has ever been in this system.
          </Muted>

          <div className="mt-5 flex flex-wrap gap-3">
            <Button
              variant="primary"
              onClick={() =>
                navigate('/patient/sign-in', { state: { prefill: DEMO_ABHA, otp: DEMO_OTP } })
              }
            >
              Try the demo patient
            </Button>
            <Button onClick={() => navigate('/clinician/sign-in')}>
              Open the clinician workspace
            </Button>
            <Button onClick={() => window.open('/about', '_blank', 'noopener')}>
              What is mocked?
            </Button>
          </div>
        </div>
      </section>
    </main>
  );
}
