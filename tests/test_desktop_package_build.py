import shutil
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.build_desktop_package import MODELS, prepare_manifest, translation_ignore


class DesktopPackageBuildTests(unittest.TestCase):
    def test_manifest_only_allows_the_bundled_certifi_ca_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            trusted = root / 'trusted.pem'
            trusted.write_bytes(b'public CA fixture')
            payload = root / 'payload'
            app = payload / 'resources/app'
            pem = app / 'backend/_internal/certifi/cacert.pem'
            pem.parent.mkdir(parents=True)
            shutil.copyfile(trusted, pem)
            (app / 'default-config.json').write_text(json.dumps({
                'portfolio': {'positions': [], 'principal_cny': 0}, 'analysis': {'mode': 'rules'},
            }), encoding='utf-8')
            for name in MODELS:
                path = app / 'data/translation-models' / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            with patch.dict('sys.modules', {'certifi': SimpleNamespace(where=lambda: str(trusted))}):
                self.assertIn(pem.relative_to(payload).as_posix(), prepare_manifest(payload))
                old_model_dir = app / '.stock-assistant'
                old_model_dir.mkdir()
                with self.assertRaisesRegex(AssertionError, 'local assistant'):
                    prepare_manifest(payload)
                old_model_dir.rmdir()
                pem.write_bytes(b'unexpected certificate or key')
                with self.assertRaises(AssertionError):
                    prepare_manifest(payload)
                shutil.copyfile(trusted, pem)
                (app / 'other.pem').touch()
                with self.assertRaises(AssertionError):
                    prepare_manifest(payload)

    def test_windows_package_preserves_runtime_and_licenses(self):
        files = (
            'onnxruntime-node/bin/napi-v3/win32/x64/onnxruntime_binding.node',
            'onnxruntime-node/bin/napi-v3/win32/x64/onnxruntime.dll',
            'onnxruntime-node/bin/napi-v3/win32/x64/DirectML.dll',
            'onnxruntime-node/bin/napi-v3/win32/arm64/onnxruntime.dll',
            'onnxruntime-node/bin/napi-v3/linux/x64/libonnxruntime.so',
            'onnxruntime-node/bin/napi-v3/darwin/x64/libonnxruntime.dylib',
            'onnxruntime-node/LICENSE', 'onnxruntime-node/package.json',
            'onnxruntime-node/dist/binding.js', 'onnxruntime-node/dist/binding.js.map',
            'onnxruntime-web/dist/ort-wasm.wasm', 'onnxruntime-web/dist/ort.mjs',
            '@huggingface/transformers/dist/transformers.node.mjs',
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in files:
                path = root / 'source' / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(name.encode())
            shutil.copytree(root / 'source', root / 'release', ignore=translation_ignore)
            kept = {p.relative_to(root / 'release').as_posix() for p in (root / 'release').rglob('*') if p.is_file()}
            expected = {f for f in files if not any(part in f for part in ('/arm64/', '/linux/', '/darwin/', '.map'))}
            self.assertEqual(kept, expected)
            for name in kept:
                self.assertEqual((root / 'source' / name).read_bytes(), (root / 'release' / name).read_bytes())
