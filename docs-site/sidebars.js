/** @type {import('@docusaurus/plugin-content-docs').SidebarsConfig} */
const sidebars = {
  docs: [
    {
      type: 'category',
      label: 'Get Started',
      collapsed: false,
      items: ['introduction', 'quickstart'],
    },
    {
      type: 'category',
      label: 'Concepts',
      collapsed: false,
      items: [
        'concepts/what-is-sentinel',
        'concepts/modules-and-permissions',
        'concepts/agent-loop',
        'concepts/sessions',
        'concepts/memory',
        'concepts/triggers',
        'concepts/browser-automation',
        'concepts/approvals',
      ],
    },
    {
      type: 'category',
      label: 'Guides',
      collapsed: false,
      items: [
        'guides/installation',
        'guides/providers',
        'guides/workspaces',
        'guides/runtime-updates',
        'guides/computer-use',
        'guides/standalone-tui',
        'guides/creating-modules',
        'guides/permissions',
        'guides/runtime-exec-security',
        'guides/multi-instance',
        'guides/telegram',
        'guides/voice',
      ],
    },
    {
      type: 'category',
      label: 'Architecture',
      items: ['architecture/sentral', 'architecture/workspace-runtime', 'architecture/notifications'],
    },
    {
      type: 'category',
      label: 'Reference',
      collapsed: false,
      items: ['reference/api'],
    },
  ],
};

module.exports = sidebars;
