# STock

## Full application hosting

The full Python web application is in `src/quant_news_monitor/`.
The invite-only deployment package in [deploy/README.md](deploy/README.md) retains
the existing UI, collectors, portfolio, charts, news, sentiment and research APIs.
It uses an isolated empty workspace, access-key authentication, persistent SQLite
and an HTTPS reverse proxy. It is a shared invited workspace, not a multi-user SaaS.

**The full backend is not deployed yet.** The link below is still only the older
static research edition. A persistent server and an actual HTTPS acceptance test
are required; adding Docker files to GitHub does not deploy a server.
See [verification status](deploy/VERIFICATION.md).

## GitHub Pages edition

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

Live site: https://toddqiu.github.io/STock/

The production site is published from the root of the `gh-pages` branch.
The `main` branch keeps the application source and generated `docs/` files.
To update the live site, test the generated files and publish the contents of
`docs/` to the root of `gh-pages`, retaining `.nojekyll`. Updating `main` alone
does not update the website. Do not publish desktop configuration or databases.

For a separate deployment using `main/docs`, choose Settings > Pages >
Deploy from a branch > main > /docs > Save. Do not switch the existing site's
source unless intentionally changing the deployment method.

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
