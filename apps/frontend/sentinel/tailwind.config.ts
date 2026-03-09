import type { Config } from 'tailwindcss';

export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        display: ['Plus Jakarta Sans', 'sans-serif'],
        body: ['Plus Jakarta Sans', 'sans-serif'],
      },
      colors: {
        ink: '#0A1320',
        cyan: '#16B4C5',
        mint: '#22C58B',
        coral: '#F97352',
        cloud: '#E6F2FF',
      },
      boxShadow: {
        bloom: '0 24px 60px rgba(7, 34, 50, 0.22)',
        insetline: 'inset 0 1px 0 rgba(255,255,255,0.12)',
      },
      keyframes: {
        floaty: {
          '0%, 100%': { transform: 'translateY(0px)' },
          '50%': { transform: 'translateY(-7px)' },
        },
        pulseBorder: {
          '0%, 100%': { borderColor: 'rgba(22, 180, 197, 0.34)' },
          '50%': { borderColor: 'rgba(249, 115, 82, 0.45)' },
        },
      },
      animation: {
        floaty: 'floaty 6s ease-in-out infinite',
        pulseBorder: 'pulseBorder 2.8s ease-in-out infinite',
      },
    },
  },
  plugins: [],
} satisfies Config;
