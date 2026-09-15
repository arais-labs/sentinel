import { lazy, Suspense } from 'react';
import { useLocation } from 'react-router-dom';
import { useProductTourStore } from '../../store/product-tour-store';
import './tour-keyboard';

// Load the guide, content, and styles only when somebody opens the tour.
const ProductTour = lazy(() => import('./ProductTour').then(module => ({ default: module.ProductTour })));

export function ProductTourHost() {
  const instanceName = useProductTourStore(state => state.instanceName);
  const location = useLocation();
  const active = decodeURIComponent(location.pathname.match(/^\/instances\/([^/]+)/)?.[1] ?? '');
  if (!instanceName || active !== instanceName) return null;
  return <Suspense fallback={null}><ProductTour key={instanceName} instanceName={instanceName} onClose={() => useProductTourStore.getState().close()} /></Suspense>;
}
