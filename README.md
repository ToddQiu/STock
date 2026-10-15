# STock GitHub Pages Edition

This directory is a standalone, static research reader for GitHub Pages.
It is not the Python desktop application's backend.

Included: the existing 25-company reference catalog with original review date,
source links, conditional scenarios, filtering and sorting, and browser-local
research notes with JSON import/export. No live quotes, background collection,
portfolio synchronization, login or server-side analysis is provided.

Private notes stay in the visitor's browser. Clearing browser data removes them.
Never commit note exports, configuration, databases, credentials, logs or backups.

Build a clean publication copy from the project root:

```powershell
python scripts/build_github_pages.py --output output/github-pages-release
node --test tests/test_pages_core.mjs
```

The generated repository uses `docs/` for the published site. On GitHub choose
Settings > Pages > Deploy from a branch > main > /docs > Save.
Publishing the repository does not, by itself, enable GitHub Pages.

To test locally, serve the generated directory with any static HTTP server,
then open `/docs/`. Relative asset paths also work under `/STock/` on Pages.

The existing Lucide icon sprite retains its bundled copyright and license.
No new license is granted for the rest of the project by this publication.

## Desktop source

The `src/` directory preserves the Python application source. It does not run
on GitHub Pages. For local use, install Python 3.10 or newer, install the
project (`pip install -e .`), copy `config.example.json` to `config.json`,
then run `python app.py`. The example configuration contains no holdings.
Optional local translation models and Windows desktop binaries are not included.
Never expose the desktop API directly to the public internet.
