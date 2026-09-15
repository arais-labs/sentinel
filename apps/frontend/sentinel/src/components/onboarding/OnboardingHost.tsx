import { lazy, Suspense } from 'react';

// Keep setup code and styles out of the normal workspace entry point.
const OnboardingPage = lazy(() =>
  import('../../pages/OnboardingPage').then((module) => ({ default: module.OnboardingPage }))
);

export function OnboardingHost() {
  return (
    <Suspense
      fallback={
        <div
          role="status"
          className="flex min-h-screen items-center justify-center text-sm text-(--text-muted)"
        >
          Opening setup…
        </div>
      }
    >
      <OnboardingPage />
    </Suspense>
  );
}
