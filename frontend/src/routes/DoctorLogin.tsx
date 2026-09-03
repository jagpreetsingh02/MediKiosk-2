/**
 * Staff sign-in. The same mock issuer as the patient side, a different role.
 *
 * ⚠️ NOT AUTHENTICATION. `app/auth/mock_idp.py` mints a locally-signed token for any name you
 * type, because the point of this build is to exercise the authorisation, consent and audit
 * paths — not to model a login. What IS real is what the token then permits:
 * `config/policy.yaml` grants `clinician` the actions this workspace needs and denies the
 * rest, and every endpoint checks. A `patient` token reaching a clinician route is refused by
 * the API, not by the router.
 *
 * `/api/v1/account/sign-in` is deliberately not offered anywhere in this product. It is a
 * documented stub that stores nothing and refuses everything in the same words; putting it on
 * a screen would present a fake login as the real one.
 */

import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { Button, DemoBand, Heading, Muted, Pane, Problem, Surface } from '@/design/ui/Surface';
import { ApiError } from '@/lib/api';
import { signInStaff } from '@/lib/session';

export default function DoctorLogin() {
  const navigate = useNavigate();
  const [name, setName] = useState('dr.iyer@aiia');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  async function enter() {
    setBusy(true);
    setError(null);
    try {
      await signInStaff('clinician', name.trim());
      navigate('/clinician', { replace: true });
    } catch (cause) {
      setError(cause as ApiError);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Surface kind="clinical">
      <DemoBand what="clinician identity" />
      <div className="mx-auto max-w-md px-6 py-16">
        <Heading level={1}>Clinician workspace</Heading>
        <Muted className="mt-2">
          Sign in to review the intake queue. The role is <code>clinician</code>, which
          <code> config/policy.yaml</code> grants the summary, fact-review and commit actions —
          and nothing else.
        </Muted>

        <Pane className="mt-8 space-y-4">
          <label className="block">
            <span className="text-sm font-medium" style={{ color: 'var(--mk-ink-strong)' }}>
              Your name
            </span>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="mt-2 w-full rounded-lg border px-3 py-2.5"
              style={{
                borderColor: 'var(--mk-line-strong)',
                backgroundColor: 'var(--mk-void)',
                color: 'var(--mk-ink)',
              }}
              onKeyDown={(e) => e.key === 'Enter' && void enter()}
            />
          </label>
          <Muted>
            Recorded as the actor on every audit row and on any fact you confirm. There is no
            password because there is no account — this is a mock issuer.
          </Muted>
          <Button variant="primary" onClick={enter} disabled={busy || !name.trim()}>
            {busy ? 'Signing in…' : 'Enter workspace'}
          </Button>
          {error ? <Problem message={error.message} detail={error.detail} /> : null}
        </Pane>

        <button
          type="button"
          onClick={() => navigate('/')}
          className="mt-6 text-sm underline underline-offset-4"
          style={{ color: 'var(--mk-ink-muted)' }}
        >
          Back
        </button>
      </div>
    </Surface>
  );
}
