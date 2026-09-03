/**
 * The route table. Two surfaces, one language — ADR-0013.
 *
 * `/` is the landing page and keeps the hero exactly as it is. Everything under `/patient`
 * runs at kiosk density; everything under `/clinician` at clinical density. The split is
 * `data-surface`, set once per route by `<Surface>`, not two themes.
 *
 * Guards here are CONVENIENCE ONLY — they redirect someone who is not signed in so they do
 * not stare at an empty screen. They are not authorisation. Every route's data comes from an
 * API call that carries a bearer token, and `config/policy.yaml` decides what that token may
 * do; a patient who edited their way past a client-side guard would still receive 403s from
 * every clinician endpoint. Never move an access decision into this file.
 */

import { Navigate, Route, BrowserRouter as Router, Routes } from 'react-router-dom';

import Landing from '@/routes/Landing';
import PatientLogin from '@/routes/PatientLogin';
import PatientHome from '@/routes/PatientHome';
import Consultation from '@/routes/Consultation';
import DoctorLogin from '@/routes/DoctorLogin';
import DoctorQueue from '@/routes/DoctorQueue';
import EncounterReview from '@/routes/EncounterReview';
import PatientRecord from '@/routes/PatientRecord';
import SessionReview from '@/routes/SessionReview';
import { getIdentity } from '@/lib/session';

function RequireRole({
  role,
  redirect,
  children,
}: {
  role: 'patient' | 'clinician';
  redirect: string;
  children: React.ReactNode;
}) {
  if (getIdentity().role !== role) return <Navigate to={redirect} replace />;
  return <>{children}</>;
}

export default function App() {
  return (
    <Router>
      <Routes>
        <Route path="/" element={<Landing />} />

        <Route path="/patient/sign-in" element={<PatientLogin />} />
        <Route
          path="/patient"
          element={
            <RequireRole role="patient" redirect="/patient/sign-in">
              <PatientHome />
            </RequireRole>
          }
        />
        <Route
          path="/patient/consultation"
          element={
            <RequireRole role="patient" redirect="/patient/sign-in">
              <Consultation />
            </RequireRole>
          }
        />

        <Route path="/clinician/sign-in" element={<DoctorLogin />} />
        <Route
          path="/clinician"
          element={
            <RequireRole role="clinician" redirect="/clinician/sign-in">
              <DoctorQueue />
            </RequireRole>
          }
        />
        <Route
          path="/clinician/sessions/:sessionRef"
          element={
            <RequireRole role="clinician" redirect="/clinician/sign-in">
              <SessionReview />
            </RequireRole>
          }
        />
        <Route
          path="/clinician/patients/:patientRef"
          element={
            <RequireRole role="clinician" redirect="/clinician/sign-in">
              <PatientRecord />
            </RequireRole>
          }
        />
        <Route
          path="/clinician/patients/:patientRef/encounters/:encounterRef"
          element={
            <RequireRole role="clinician" redirect="/clinician/sign-in">
              <EncounterReview />
            </RequireRole>
          }
        />

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Router>
  );
}
