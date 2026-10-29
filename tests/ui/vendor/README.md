# Vendored CDN files for the UI tests

The dashboard loads these from jsDelivr. The browser tests serve these copies instead, so a run is offline
and repeatable (FR-031). Versions match `build_html()` in `bin/sdd-dashboard.py`.

| File | Source |
|---|---|
| `marked.min.js` | https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js |
| `highlight.min.js` | https://cdn.jsdelivr.net/npm/@highlightjs/cdn-assets@11.9.0/highlight.min.js |
| `github-dark.min.css` | https://cdn.jsdelivr.net/npm/@highlightjs/cdn-assets@11.9.0/styles/github-dark.min.css |
| `purify.min.js` | https://cdn.jsdelivr.net/npm/dompurify@3.1.6/dist/purify.min.js |
