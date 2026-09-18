import React from 'react';
import ReactDOM from 'react-dom/client';
import { CssBaseline, ThemeProvider } from '@mui/material';
import { BrowserRouter } from 'react-router-dom';
import 'highlight.js/styles/github-dark-dimmed.css';
import 'katex/dist/katex.min.css';

import App from './App';
import './index.css';
import { useThemeStore } from './store/theme-store';
import { getAppTheme } from './theme';

function Root() {
  const mode = useThemeStore((state) => state.theme);
  const theme = React.useMemo(() => getAppTheme(mode), [mode]);
  const basename = (import.meta.env.VITE_ROUTER_BASENAME as string | undefined) ?? '/';

  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <BrowserRouter basename={basename}>
        <App />
      </BrowserRouter>
    </ThemeProvider>
  );
}

class RootErrorBoundary extends React.Component<{ children: React.ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) { return { error }; }
  componentDidCatch(error: Error) { console.error('Sentinel failed to render', error); }
  render() {
    if (!this.state.error) return this.props.children;
    return <div style={{ padding: 32, fontFamily: 'system-ui, sans-serif', color: '#e4e4e7', background: '#09090b', minHeight: '100vh' }}>
      <p style={{ fontWeight: 600, marginBottom: 8 }}>Sentinel failed to render.</p>
      <p style={{ opacity: .75, marginBottom: 16 }}>Reload the app. If this keeps happening, install the latest release.</p>
      <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12, opacity: .7 }}>{String(this.state.error.stack || this.state.error.message)}</pre>
    </div>;
  }
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <RootErrorBoundary>
      <Root />
    </RootErrorBoundary>
  </React.StrictMode>,
);
