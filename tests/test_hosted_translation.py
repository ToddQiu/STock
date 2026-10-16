import hashlib
import importlib.util
import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("hosted_translation", Path(__file__).resolve().parents[1] / "scripts/prepare_hosted_translation.py")
translation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(translation)


class HostedTranslationTests(unittest.TestCase):
    def test_urls_hashes_and_reuse_without_network(self):
        content = b"public model fixture"
        expected = hashlib.sha256(content).hexdigest()
        manifest = {"marian-tokenizer/source.spm": expected, "Xenova/opus-mt-en-zh/config.json": expected}
        with TemporaryDirectory() as temporary, patch.object(translation, "MODELS", manifest), \
                patch.object(translation, "urlopen", side_effect=lambda *a, **kw: io.BytesIO(content)) as fetch:
            root = Path(temporary)
            translation.prepare(root)
            self.assertEqual([call.args[0] for call in fetch.call_args_list], [
                "https://huggingface.co/Helsinki-NLP/opus-mt-en-zh/resolve/main/source.spm",
                "https://huggingface.co/Xenova/opus-mt-en-zh/resolve/main/config.json"])
            translation.prepare(root)
            self.assertEqual(fetch.call_count, 2)
            self.assertFalse(list(root.rglob("*.partial")))

    def test_bad_download_does_not_replace_existing_resource(self):
        with TemporaryDirectory() as temporary, patch.object(translation, "MODELS", {
                "marian-tokenizer/source.spm": "0" * 64}), \
                patch.object(translation, "urlopen", return_value=io.BytesIO(b"bad download")):
            root = Path(temporary)
            path = root / "data/translation-models/marian-tokenizer/source.spm"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"existing resource")
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                translation.prepare(root)
            self.assertEqual(path.read_bytes(), b"existing resource")
            self.assertFalse(list(root.rglob("*.partial")))
