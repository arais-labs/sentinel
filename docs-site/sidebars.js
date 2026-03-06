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
        'concepts/memory',
        'concepts/triggers',
        'concepts/browser-automation',
      ],
    },
    {
      type: 'category',
      label: 'Guides',
      items: [
        'guides/installation',
        'guides/creating-modules',
        'guides/permissions',
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
