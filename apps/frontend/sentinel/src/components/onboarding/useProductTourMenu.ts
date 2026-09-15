import { useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { useProductTourStore } from '../../store/product-tour-store';

/** The desktop Help entry point. Idle unless the native menu requests a tour. */
export function useProductTourMenu(ready: boolean | undefined, lastInstancePath: string | undefined) {
  const api = window.sentinelDesktop;
  const navigate = useNavigate();
  const location = useLocation();
  const [requested, setRequested] = useState(false);
  useEffect(() => api?.onGuidedTour?.(() => setRequested(true)), [api]);
  useEffect(() => {
    if (!requested || !ready) return;
    const match = location.pathname.match(/^\/instances\/([^/]+)/);
    if (match && !location.pathname.endsWith('/onboarding')) {
      setRequested(false);
      useProductTourStore.getState().open(decodeURIComponent(match[1]));
    } else if (!match) {
      const destination = lastInstancePath ?? '/desktop/instances';
      if (location.pathname !== destination) navigate(destination);
    }
  }, [requested, ready, location.pathname, lastInstancePath, navigate]);
}
