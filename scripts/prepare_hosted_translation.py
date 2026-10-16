"""Fetch only public translation resources, validating the desktop model checksums."""
from __future__ import annotations

import argparse
import hashlib
import shutil
from pathlib import Path
from urllib.request import urlopen


MODELS = {
    "marian-tokenizer/source.spm": "5775ddc9e3ff2fae91554da56468ad35ff56edaba870fea74447bc7234bfdaa8",
    "marian-tokenizer/target.spm": "81dc94efa84e4025ef38d25d5d07429fe41e3eb29d44003f1db6fe98487b0052",
    "marian-tokenizer/vocab.json": "f1832ca38f7aa158b9a944ff583e4901e6b37d9d4ad9241f03838e636e9cfb03",
    "Xenova/opus-mt-en-zh/config.json": "4727d1229a04f95bf6f39abf949d8080615433d99d6ebd85f81c09edd247d5fa",
    "Xenova/opus-mt-en-zh/generation_config.json": "b743baabb7da4c1a2f19fe558bd6b4c0c7c3b0762fcb5ca7a48fe5a2c2219803",
    "Xenova/opus-mt-en-zh/tokenizer_config.json": "a914596e6bff113a8428d4793b586da87cd0b95697a0e72aba90cc1d95858481",
    "Xenova/opus-mt-en-zh/tokenizer.json": "d0c7da27056e8f42adce9e76d8e792e5daa64e15f5acd2e7aabf0121877dd4c1",
    "Xenova/opus-mt-en-zh/onnx/decoder_model_merged_quantized.onnx": "023be4f841f4c47cd65fffcbaa81c0d99d7f7e0138f7ba0e03fa220a4e688aff",
    "Xenova/opus-mt-en-zh/onnx/encoder_model_quantized.onnx": "d3b7912bf6a9bd27e4c074c2df91d4ff3d5b4bc5f7f6c8d7cc9c805c98fbafee",
}


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def prepare(root: Path) -> None:
    for relative, expected in MODELS.items():
        path = root / "data/translation-models" / relative
        if path.is_file() and digest(path) == expected:
            continue
        if relative.startswith("marian-tokenizer/"):
            model, filename = "Helsinki-NLP/opus-mt-en-zh", relative.split("/", 1)[1]
        else:
            model, filename = "Xenova/opus-mt-en-zh", relative.split("/", 2)[2]
        url = f"https://huggingface.co/{model}/resolve/main/{filename}"
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(path.suffix + ".partial")
        try:
            with urlopen(url, timeout=120) as source, partial.open("wb") as target:
                shutil.copyfileobj(source, target)
            if digest(partial) != expected:
                raise ValueError(f"Translation resource checksum mismatch: {relative}")
            partial.replace(path)
        finally:
            partial.unlink(missing_ok=True)
    print("All nine translation resources verified.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    prepare(parser.parse_args().root)
