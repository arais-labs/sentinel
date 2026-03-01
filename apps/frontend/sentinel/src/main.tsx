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
  const basename = (import.meta.env.VITE_ROUTER_BASENAME as string | undefined) ?? '/sentinel';

  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <BrowserRouter basename={basename}>
        <App />
      </BrowserRouter>
    </ThemeProvider>
  );
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <Root />
  </React.StrictMode>,
);
