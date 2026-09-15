# Sentinel documentation

This directory is the canonical source for product and architecture documentation.

| Path | Content |
| --- | --- |
| `docs/guides` | User and operator workflows |
| `docs/concepts` | Product behavior and boundaries |
| `docs/architecture` | Maintained implementation contracts |
| `docs/reference` | API reference |
| `static/img` | Shared documentation images |
| `sidebars.js` | Published navigation |

From this directory:

```bash
npm ci
npm start
# Verify links and the production site:
npm run build
```

Document shipped behavior against the relevant source. Use synthetic examples,
link related pages, and keep release experiments or one-time implementation plans
out of the product documentation. Root and application READMEs link here for
detailed guides.
