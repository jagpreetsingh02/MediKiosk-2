/**
 * Patient sign-in through the MOCK ABHA issuer.
 *
 * ⚠️ THIS IS NOT ABDM, AND THE SCREEN SAYS SO RATHER THAN IMPLYING OTHERWISE.
 * `app/auth/mock_idp.py` mints locally-signed JWTs with `iss=mock-abdm-idp` so the consent,
 * authorisation and audit paths can be exercised without sandbox credentials. The OTP is the
 * constant `123456` on purpose — a random OTP printed to a server log is a demo failure mode
 * waiting to happen — and it is shown on screen rather than hidden to look realistic.
 *
 * Two steps, matching the real flow the mock stands in for: request an OTP, then verify it.
 * An unknown address still authenticates, because a walk-in whose ABHA we hold no fixture for
 * must still be able to use the kiosk; they simply arrive with no demographics to pre-fill.
 */

import { useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';

import { Button, DemoBand, Heading, Muted, Pane, Problem, Surface } from '@/design/ui/Surface';
import { ApiError, api } from '@/lib/api';
import { signInPatient } from '@/lib/session';

export const DEMO_ABHA = 'demo@abdm';
export const DEMO_OTP = '123456';

/** The synthetic identities `mock_idp.DEMO_PATIENTS` knows about. */
const KNOWN = [
  { address: 'demo@abdm', label: 'Demo Patient · 52 · has prior visits on file' },
  { address: 'kamala.devi@abdm', label: 'Kamala Devi · 64 · Hindi' },
  { address: 'ramesh.kumar@abdm', label: 'Ramesh Kumar · 47 · Hindi' },
  { address: 'anitha.r@abdm', label: 'Anitha R · 31 · Tamil' },
];

export default function PatientLogin() {
  const navigate = useNavigate();
  const location = useLocation();
  const prefill = (location.state as { prefill?: string; otp?: string } | null) ?? null;

  const [address, setAddress] = useState(prefill?.prefill ?? DEMO_ABHA);
  const [otp, setOtp] = useState(prefill?.otp ?? '');
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  // Arriving from "Try the demo patient" should not make someone type an OTP that is
  // printed on the same screen. The step is still shown, just already satisfied.
  useEffect(() => {
    if (prefill?.otp) setSent(true);
  }, [prefill?.otp]);

  async function requestOtp() {
    setBusy(true);
    setError(null);
    try {
      await api.requestOtp(address);
      setSent(true);
      setOtp(DEMO_OTP);
    } catch (cause) {
      setError(cause as ApiError);
    } finally {
      setBusy(false);
    }
  }

  async function verify() {
    setBusy(true);
    setError(null);
    try {
      await signInPatient(address, otp);
      navigate('/patient', { replace: true });
    } catch (cause) {
      setError(cause as ApiError);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Surface kind="kiosk">
      <DemoBand />
      <div className="mx-auto max-w-xl px-6 py-14">
        <Heading level={1}>Sign in with your ABHA address</Heading>
        <Muted className="mt-3">
          Your health history is loaded from your ABHA reference so you do not have to repeat
          what the record already knows. This build uses a mock issuer — the addresses below
          are synthetic and the one-time code is always {DEMO_OTP}.
        </Muted>

        <Pane className="mt-8 space-y-5">
          <label className="block">
            <span className="text-sm font-medium" style={{ color: 'var(--mk-ink-strong)' }}>
              ABHA address
            </span>
            <input
              value={address}
              onChange={(e) => {
                setAddress(e.target.value);
                setSent(false);
              }}
              className="mt-2 w-full rounded-lg border px-3 py-2.5 text-base"
              style={{
                borderColor: 'var(--mk-line-strong)',
                backgroundColor: 'var(--mk-void)',
                color: 'var(--mk-ink)',
              }}
              autoComplete="off"
              spellCheck={false}
            />
          </label>

          <div className="flex flex-wrap gap-2">
            {KNOWN.map((k) => (
              <button
                key={k.address}
                type="button"
                onClick={() => {
                  setAddress(k.address);
                  setSent(false);
                }}
                className="rounded-full border px-3 py-1 text-xs transition-colors"
                style={{
                  borderColor: 'var(--mk-line-strong)',
                  color: address === k.address ? 'var(--mk-accent-ink)' : 'var(--mk-ink-muted)',
                  transitionDuration: 'var(--mk-quick)',
                }}
              >
                {k.label}
              </button>
            ))}
          </div>

          {!sent ? (
            <Button variant="primary" onClick={requestOtp} disabled={busy || !address.trim()}>
              {busy ? 'Sending…' : 'Send one-time code'}
            </Button>
          ) : (
            <>
              <label className="block">
                <span className="text-sm font-medium" style={{ color: 'var(--mk-ink-strong)' }}>
                  One-time code
                </span>
                <input
                  value={otp}
                  onChange={(e) => setOtp(e.target.value)}
                  inputMode="numeric"
                  className="mt-2 w-full rounded-lg border px-3 py-2.5 font-mono text-lg tracking-widest"
                  style={{
                    borderColor: 'var(--mk-line-strong)',
                    backgroundColor: 'var(--mk-void)',
                    color: 'var(--mk-ink)',
                  }}
                />
              </label>
              <Muted>
                No message was sent to anybody. This mock issuer accepts {DEMO_OTP} and nothing
                else.
              </Muted>
              <Button variant="primary" onClick={verify} disabled={busy || otp.length < 4}>
                {busy ? 'Checking…' : 'Continue'}
              </Button>
            </>
          )}

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
