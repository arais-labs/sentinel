import { createTheme } from '@mui/material/styles';

// We provide a minimal MUI theme to avoid crashes in components that still use it,
// but we are phasing out MUI in favor of pure Tailwind CSS.
export function getAppTheme(mode: 'light' | 'dark') {
  return createTheme({
    palette: {
      mode,
    },
    components: {
      MuiCssBaseline: {
        styleOverrides: {
          body: {
            background: 'transparent !important',
            backgroundImage: 'none !important',
          },
        },
      },
    },
  });
}
