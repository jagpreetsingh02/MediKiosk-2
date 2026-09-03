/**
 * Who is signed in, and which consultation is open. The whole of the client's state.
 *
 * ⛔ DELIBERATELY NOT A STORE. There is no Redux, no Zustand, no context tree, because there
 * is almost nothing to hold: identity, and a handful of REFERENCES. Everything clinical —
 * facts, their review status, contradictions, red flags, the timeline — is read from the API
 * at the moment it is shown and never cached here.
 *
 * That is not minimalism for its own sake. A cached copy of a fact's `reviewStatus` is a
 * second source of truth about whether a physician has signed something off, and the moment
 * it drifts the UI is lying about a clinical decision. The backend owns those rules
 * (`app/modules/encounter/review.py`); the client asks and re-renders.
 *
 * `sessionStorage` rather than `localStorage`: a kiosk is a SHARED DEVICE. A patient's
 * identity must not outlive the browser tab, and the next person to walk up must not inherit
 * the last one's token. It is cleared explicitly on sign-out and by the browser on close.
 */

import { api, setToken } from '@/lib/api';

const KEY = 'medikiosk.session.v1';

export type Role = 'patient' | 'clinician' | 'anonymous';

export interface Identity {
  role: Role;
  token: string;
  /** Display label for the header. Never used for authorisation — the token carries that. */
  actor: string;
  /** Patients only: the pseudonymous ABHA reference and whatever the mock IdP knew. */
  abhaRef?: string | null;
  demographics?: Record<string, unknown> | null;
  /** Resolved after the first `/patients/me` call. */
  patientRef?: string | null;
  /** ⚠️ True whenever the identity came from the MOCK issuer. Surfaced in the UI, always. */
  isDemo: boolean;
}

const EMPTY: Identity = { role: 'anonymous', token: '', actor: '', isDemo: false };

let current: Identity = load();
const listeners = new Set<(id: Identity) => void>();

function load(): Identity {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (!raw) return EMPTY;
    const parsed = JSON.parse(raw) as Identity;
    if (parsed.token) setToken(parsed.token);
    return parsed;
  } catch {
    // A corrupt or unreadable store is an anonymous session, not a crash. Private-mode
    // browsers throw on sessionStorage access entirely.
    return EMPTY;
  }
}

function persist(next: Identity): void {
  current = next;
  setToken(next.token || null);
  try {
    if (next.role === 'anonymous') sessionStorage.removeItem(KEY);
    else sessionStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    /* storage unavailable — the session still works for this page's lifetime */
  }
  listeners.forEach((fn) => fn(next));
}

export function getIdentity(): Identity {
  return current;
}

export function subscribe(fn: (id: Identity) => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function signOut(): void {
  persist(EMPTY);
  clearConsultation();
}

/**
 * Patient sign-in through the MOCK ABHA issuer.
 *
 * ⚠️ This is not ABDM. `app/auth/mock_idp.py` mints locally-signed JWTs with `iss=mock-abdm-idp`
 * so the consent, authorisation and audit paths can be exercised without sandbox credentials.
 * Every screen reached with one of these tokens carries a demo band, and `isDemo` below is
 * what drives it.
 */
export async function signInPatient(abhaAddress: string, otp: string): Promise<Identity> {
  const result = await api.verifyOtp(abhaAddress, otp);
  const next: Identity = {
    role: 'patient',
    token: result.access_token,
    actor: String(
      (result.demographics as Record<string, unknown> | null)?.display_name ?? abhaAddress,
    ),
    abhaRef: result.abhaRef,
    demographics: result.demographics,
    patientRef: null,
    isDemo: true,
  };
  persist(next);
  return next;
}

/** Staff sign-in through the same mock issuer. Role comes from `config/policy.yaml`. */
export async function signInStaff(role: 'clinician', name: string): Promise<Identity> {
  const result = await api.staffToken(role, name);
  const next: Identity = {
    role,
    token: result.access_token,
    actor: name,
    isDemo: true,
  };
  persist(next);
  return next;
}

/** Remember the patient reference once the API has resolved it. */
export function rememberPatientRef(patientRef: string): void {
  if (current.patientRef === patientRef) return;
  persist({ ...current, patientRef });
}

// ---------------------------------------------------------------- the open consultation

const CONSULT_KEY = 'medikiosk.consultation.v1';

export interface OpenConsultation {
  sessionRef: string;
  language: string;
  ayushMode: boolean;
}

export function setConsultation(value: OpenConsultation): void {
  try {
    sessionStorage.setItem(CONSULT_KEY, JSON.stringify(value));
  } catch {
    /* ignore */
  }
}

export function getConsultation(): OpenConsultation | null {
  try {
    const raw = sessionStorage.getItem(CONSULT_KEY);
    return raw ? (JSON.parse(raw) as OpenConsultation) : null;
  } catch {
    return null;
  }
}

export function clearConsultation(): void {
  try {
    sessionStorage.removeItem(CONSULT_KEY);
  } catch {
    /* ignore */
  }
}
