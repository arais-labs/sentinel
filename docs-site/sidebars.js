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
        'concepts/what-is-araios',
        'concepts/agent-loop',
        'concepts/memory',
        'concepts/triggers',
        'concepts/browser-automation',
        'concepts/approvals',
        'concepts/estop',
        'concepts/sessions',
      ],
    },
    {
      type: 'category',
      label: 'Guides',
      collapsed: false,
      items: [
        'guides/installation',
        'guides/cli-reference',
        'guides/runtime-exec-security',
        'guides/creating-modules',
        'guides/permissions',
        'guides/telegram',
        'guides/multi-instance',
      ],
    },
    {
      type: 'category',
      label: 'Reference',
      items: ['reference/api'],
    },
  ],
};

module.exports = sidebars;
