import Hero from '@/hero/Hero';

/**
 * The shell. Currently the hero and nothing else — the remaining sections and the kiosk /
 * physician routes are being supplied and land here as they arrive.
 *
 * The hero's two buttons take handlers rather than hrefs so that no route is invented before
 * it exists; wire them here when the intake and sign-in screens are built.
 */
export default function App() {
  return (
    <main className="min-h-screen">
      <Hero />
    </main>
  );
}
