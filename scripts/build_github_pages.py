"""Build an allowlisted public source copy. Never read desktop runtime data."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE_FILES = ('index.html', 'styles.css', 'app.js', 'core.mjs')
SOURCE_EXTENSIONS = {'.py', '.json', '.html', '.css', '.js', '.svg'}
SECRET_PATTERNS = (
    re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{35,})\b'),
    re.compile(r'(?i)(?:appsecret|api_key|password|access_token)\s*[\"\x27]?\s*[:=]\s*[\"\x27]([A-Za-z0-9+/=_-]{24,})[\"\x27]'),
)


def scan_text(text: str, label: str) -> None:
    if any(pattern.search(text) for pattern in SECRET_PATTERNS):
        raise ValueError(f'Potential credential in {label}; publication stopped')
    if re.search(r'(?i)[A-Z]:[/\\](?:Users|RelocatedUserData)[/\\]', text):
        raise ValueError(f'Personal filesystem path in {label}; publication stopped')


def publication_files() -> dict[str, bytes]:
    files = {}
    for name in PAGE_FILES:
        files['docs/' + name] = (ROOT / 'pages' / name).read_bytes()
    ui = ROOT / 'src/quant_news_monitor/ui'
    for name in ('logo.svg', 'icons.svg'):
        files['docs/' + name] = (ui / name).read_bytes()
    catalog_path = ROOT / 'src/quant_news_monitor/company_landscape.json'
    catalog = json.loads(catalog_path.read_text(encoding='utf-8'))
    assert len(catalog['companies']) == 25
    assert len({row['id'] for row in catalog['companies']}) == 25
    files['docs/company-data.json'] = catalog_path.read_bytes()
    files['docs/.nojekyll'] = b''
    files['.nojekyll'] = b''
    files['README.md'] = (ROOT / 'pages/README.md').read_bytes()
    files['.gitignore'] = b'config.json\n.env*\ndata/\noutput/\noutputs/\n.venv/\n__pycache__/\n*.py[cod]\n*.db*\n*.log\nnode_modules/\nSTock-research-notes*.json\n'
    for path in (ROOT / 'src').rglob('*'):
        if path.is_file() and path.suffix in SOURCE_EXTENSIONS and '__pycache__' not in path.parts:
            files[path.relative_to(ROOT).as_posix()] = path.read_bytes()
    for name in ('app.py', 'monitor.py', 'pyproject.toml'):
        files[name] = (ROOT / name).read_bytes()
    for name in (*('pages/' + name for name in (*PAGE_FILES, 'README.md')),
                 'scripts/build_github_pages.py', 'tests/test_pages_core.mjs'):
        files[name] = (ROOT / name).read_bytes()
    config_path = ROOT / 'desktop/default-config.json'
    if not config_path.is_file():
        config_path = ROOT / 'config.example.json'
    config = json.loads(config_path.read_text(encoding='utf-8'))
    assert config['portfolio'] == {'positions': [], 'principal_cny': 0}
    files['config.example.json'] = (json.dumps(config, ensure_ascii=False, indent=2) + '\n').encode()
    for path, content in files.items():
        if len(content) > 1_000_000:
            raise ValueError(f'Unexpected large file: {path}')
        scan_text(content.decode('utf-8-sig'), path)
    return files


def build(output: Path) -> dict:
    output = output.resolve()
    if output == ROOT or ROOT not in output.parents:
        raise ValueError('Output must be a new directory inside the project')
    if output.exists() and any(output.iterdir()):
        raise ValueError('Output directory must be empty; existing files are never replaced')
    files = publication_files()
    output.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        destination = output / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    manifest = {name: hashlib.sha256(content).hexdigest() for name, content in sorted(files.items())}
    (output / 'PUBLICATION-MANIFEST.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    return {'output': str(output), 'files': len(files) + 1, 'bytes': sum(map(len, files.values())), 'private_runtime_data_included': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    print(json.dumps(build(parser.parse_args().output), ensure_ascii=False, indent=2))
