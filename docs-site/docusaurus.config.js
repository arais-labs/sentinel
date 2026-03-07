// @ts-check
const { themes } = require('prism-react-renderer');

/** @type {import('@docusaurus/types').Config} */
const config = {
  title: 'Sentinel',
  tagline: 'The autonomous agent platform built for real operational use.',
  favicon: 'img/logo.svg',

  url: 'https://docs.arais.us',
  baseUrl: '/',

  organizationName: 'arais-labs',
  projectName: 'sentinel',

  onBrokenLinks: 'throw',
  onBrokenMarkdownLinks: 'warn',

  i18n: {
    defaultLocale: 'en',
    locales: ['en'],
  },

  presets: [
    [
      'classic',
      /** @type {import('@docusaurus/preset-classic').Options} */
      ({
        docs: {
          sidebarPath: require.resolve('./sidebars.js'),
          routeBasePath: '/',
          editUrl: 'https://github.com/arais-labs/sentinel/edit/main/docs-site/',
        },
        blog: false,
        theme: {
          customCss: require.resolve('./src/css/custom.css'),
        },
      }),
    ],
  ],

  themeConfig:
    /** @type {import('@docusaurus/preset-classic').ThemeConfig} */
    ({
      image: 'img/logo.svg',
      colorMode: {
        defaultMode: 'dark',
        disableSwitch: false,
        respectPrefersColorScheme: true,
      },
      navbar: {
        title: 'Sentinel',
        logo: {
          alt: 'Sentinel Logo',
          src: 'img/logo.svg',
        },
        items: [
          {
            type: 'docSidebar',
            sidebarId: 'docs',
            position: 'left',
            label: 'Docs',
          },
          {
            href: 'https://arais.us',
            label: 'ARAIS',
            position: 'right',
          },
          {
            href: 'https://github.com/arais-labs/sentinel',
            label: 'GitHub',
            position: 'right',
          },
        ],
      },
      footer: {
        style: 'dark',
        links: [
          {
            title: 'Docs',
            items: [
              { label: 'Introduction', to: '/' },
              { label: 'Quick Start', to: '/quickstart' },
              { label: 'What is Sentinel?', to: '/concepts/what-is-sentinel' },
              { label: 'What is araiOS?', to: '/concepts/what-is-araios' },
            ],
          },
          {
            title: 'ARAIS',
            items: [
              { label: 'Website', href: 'https://arais.us' },
              { label: 'GitHub', href: 'https://github.com/arais-labs' },
            ],
          },
        ],
        copyright: `Built by <a href="https://arais.us">ARAIS</a>. Licensed AGPL-3.0.`,
      },
      prism: {
        theme: themes.github,
        darkTheme: themes.dracula,
        additionalLanguages: ['bash', 'yaml', 'docker'],
      },
      algolia: undefined,
    }),
};

module.exports = config;
