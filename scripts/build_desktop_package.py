"""Build an allowlisted, self-contained Windows x64 desktop distribution."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS = (
    "marian-tokenizer/source.spm", "marian-tokenizer/target.spm", "marian-tokenizer/vocab.json",
    "Xenova/opus-mt-en-zh/config.json", "Xenova/opus-mt-en-zh/generation_config.json",
    "Xenova/opus-mt-en-zh/tokenizer_config.json", "Xenova/opus-mt-en-zh/tokenizer.json",
    "Xenova/opus-mt-en-zh/onnx/encoder_model_quantized.onnx",
    "Xenova/opus-mt-en-zh/onnx/decoder_model_merged_quantized.onnx",
)


def copy(source: Path, target: Path):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def sha256(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def translation_ignore(directory, names):
    # The Windows x64 release needs only its native ONNX binary, not other hosts.
    directory = Path(directory)
    if directory.name == "napi-v3" and directory.parent.parent.name == "onnxruntime-node":
        return [name for name in names if name != "win32"]
    if directory.name == "win32" and directory.parent.name == "napi-v3":
        return [name for name in names if name != "x64"]
    return [name for name in names if name in {".cache", ".bin"} or name.endswith((".log", ".map"))]


def prepare_manifest(folder: Path):
    files = sorted(path for path in folder.rglob("*") if path.is_file())
    forbidden = {".db", ".sqlite", ".sqlite3", ".log", ".jsonl", ".pem", ".key", ".pfx"}
    app = folder / "resources/app"
    assert not (app / ".stock-assistant").exists(), "Remove the local assistant from the build payload before packaging"
    ca_bundle = app / "backend/_internal/certifi/cacert.pem"
    for path in files:
        if path == ca_bundle:
            from certifi import where

            assert sha256(path) == sha256(Path(where())), f"Unexpected Certifi CA bundle: {path}"
        else:
            assert path.suffix.lower() not in forbidden, f"Private/runtime file in package: {path}"
        assert not any(part in {".git", "desktop-profile", ".env", "__pycache__"} for part in path.parts)
    config = json.loads((app / "default-config.json").read_text(encoding="utf-8"))
    assert config["portfolio"] == {"positions": [], "principal_cny": 0}
    assert config["analysis"]["mode"] == "rules"
    assert not (app / "config.json").exists()
    assert sorted(p.relative_to(app / "data").as_posix() for p in (app / "data").rglob("*") if p.is_file()) == sorted("translation-models/" + name for name in MODELS)
    manifest = {p.relative_to(folder).as_posix(): sha256(p) for p in files if p.name != "manifest-sha256.json"}
    (folder / "manifest-sha256.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def archive(folder: Path):
    prepare_manifest(folder)
    target = folder.with_suffix(".zip")
    with zipfile.ZipFile(target, "x", zipfile.ZIP_DEFLATED, compresslevel=6) as output:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                output.write(path, folder.name + "/" + path.relative_to(folder).as_posix())
    digest = sha256(target)
    target.with_suffix(".zip.sha256").write_text(f"{digest}  {target.name}\n", encoding="ascii")
    print(json.dumps({"zip": str(target), "bytes": target.stat().st_size, "sha256": digest}, indent=2), flush=True)


def installer(folder: Path, compiler: Path):
    license_file = compiler.parent.parent / "COPYING"
    if not license_file.is_file():
        license_file = compiler.parent / "COPYING"
    copy(license_file, folder / "licenses/NSIS-COPYING.txt")
    manifest = prepare_manifest(folder)
    work = ROOT / ".qa-runtime" / ("installer-" + folder.name)
    work.mkdir(parents=True, exist_ok=True)
    target = ROOT / "dist" / (folder.name.replace("Windows-x64-", "Setup-Compact-") + "-x64.exe")
    if target.exists():
        raise FileExistsError(target)
    names = sorted([*manifest, "manifest-sha256.json"])
    directories = {parent.as_posix() for name in names for parent in Path(name).parents if parent != Path(".")}
    def escaped(value):
        return str(value).replace("$", "$$").replace('"', '$\\"').replace("/", "\\")
    # Delete only shipped files, never recursively delete a user-selected installation directory.
    commands = [f'Delete "$INSTDIR\\{escaped(name)}"' for name in names]
    commands += [f'RMDir "$INSTDIR\\{escaped(name)}"' for name in sorted(directories, key=lambda s: (s.count("/"), s), reverse=True)]
    uninstall_files = work / "uninstall-files.nsh"
    uninstall_files.write_text("\n".join(commands) + "\n", encoding="utf-8")
    subprocess.run([str(compiler), "/V3", "/INPUTCHARSET", "UTF8",
                    f"/DPAYLOAD={folder}", f"/DOUTPUT={target}",
                    f"/DUNINSTALL_FILES={uninstall_files}", str(ROOT / "desktop/installer.nsi")], check=True)
    digest = sha256(target)
    target.with_suffix(".exe.sha256").write_text(f"{digest}  {target.name}\n", encoding="ascii")
    print(json.dumps({"installer": str(target), "bytes": target.stat().st_size,
                      "sha256": digest, "payload_files": len(names)}, indent=2), flush=True)
    return target


def build(node: Path):
    assert sys.platform == "win32" and sys.maxsize > 2**32, "Build on Windows x64"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    work = ROOT / ".qa-runtime" / ("desktop-build-" + stamp)
    folder = ROOT / "dist" / ("STock-Windows-x64-" + stamp)
    work.mkdir(parents=True)
    sources = work / "quant_news_monitor"
    sources.mkdir()
    for path in (ROOT / "src/quant_news_monitor").iterdir():
        if path.suffix in {".py", ".json"}:
            copy(path, sources / path.name)
    shutil.copytree(ROOT / "src/quant_news_monitor/ui", sources / "ui")
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--onedir", "--console",
        "--name", "STockServer", "--paths", str(ROOT / "src"),
        "--distpath", str(work / "frozen"), "--workpath", str(work / "build"), "--specpath", str(work),
        "--collect-data", "tzdata", "--collect-all", "exchange_calendars", "--collect-all", "reportlab",
        "--collect-all", "openai",
        "--add-data", f"{sources}:quant_news_monitor",
        "--icon", str(ROOT / "src/quant_news_monitor/ui/logo.ico"),
        str(ROOT / "desktop/server_entry.py"),
    ], check=True)
    shutil.copytree(ROOT / "desktop/node_modules/electron/dist", folder)
    (folder / "electron.exe").rename(folder / "STock.exe")
    from PyInstaller.utils.win32.icon import CopyIcons
    from PyInstaller.config import CONF
    CONF["workpath"] = str(work)
    CopyIcons(str(folder / "STock.exe"), [str(ROOT / "src/quant_news_monitor/ui/logo.ico")])
    # Replace Electron's demo entrypoint; the runtime's licenses stay untouched.
    (folder / "resources/default_app.asar").unlink(missing_ok=True)
    app = folder / "resources/app"
    app.mkdir()
    for name in ("main.cjs", "preload.cjs", "window-policy.cjs", "report-window.cjs", "portable-backend.cjs", "default-config.json"):
        copy(ROOT / "desktop" / name, app / name)
    version = json.loads((ROOT / "desktop/package.json").read_text(encoding="utf-8"))["version"]
    (app / "package.json").write_text(json.dumps({"name": "stock-desktop", "productName": "STock", "version": version, "main": "main.cjs", "private": True}), encoding="utf-8")
    copy(ROOT / "src/quant_news_monitor/ui/logo.ico", app / "logo.ico")
    shutil.copytree(work / "frozen/STockServer", app / "backend")
    copy(node, app / "runtime/node.exe")
    for name in ("worker.mjs", "prepare.mjs", "package.json", "package-lock.json"):
        copy(ROOT / "translation_runtime" / name, app / "translation_runtime" / name)
    shutil.copytree(ROOT / "translation_runtime/node_modules", app / "translation_runtime/node_modules",
                    ignore=translation_ignore)
    for name in MODELS:
        copy(ROOT / "data/translation-models" / name, app / "data/translation-models" / name)
    copy(ROOT / "desktop/PORTABLE_README.txt", folder / "READ-ME.txt")
    copy(ROOT / "desktop/THIRD_PARTY_NOTICES.txt", folder / "THIRD_PARTY_NOTICES.txt")
    license_dir = folder / "licenses"
    copy(Path(sys.base_prefix) / "LICENSE.txt", license_dir / "Python-LICENSE.txt")
    for dist in importlib.metadata.distributions():
        for entry in dist.files or []:
            if any(word in entry.name.lower() for word in ("license", "licence", "copying", "notice")) and ".dist-info/" in str(entry):
                copy(Path(dist.locate_file(entry)), license_dir / dist.metadata["Name"] / entry.name)
    node_version = subprocess.check_output([str(node), "--version"], text=True).strip()
    urls = {
        "Node-LICENSE.txt": f"https://raw.githubusercontent.com/nodejs/node/{node_version}/LICENSE",
        "SentencePiece-LICENSE.txt": "https://raw.githubusercontent.com/google/sentencepiece/v0.2.1/LICENSE",
        "Apache-2.0.txt": "https://www.apache.org/licenses/LICENSE-2.0.txt",
        "OPUS-MT-model-card.txt": "https://huggingface.co/Helsinki-NLP/opus-mt-en-zh/raw/main/README.md",
        "ONNX-model-card.txt": "https://huggingface.co/Xenova/opus-mt-en-zh/raw/main/README.md",
    }
    for name, url in urls.items():
        with urllib.request.urlopen(url, timeout=60) as response:
            (license_dir / name).write_bytes(response.read())
    versions = {dist.metadata["Name"]: dist.version for dist in importlib.metadata.distributions()}
    (folder / "build-info.json").write_text(json.dumps({"built_at": stamp, "python": sys.version.split()[0], "node": node_version,
        "electron": json.loads((ROOT / "desktop/node_modules/electron/package.json").read_text())["version"],
        "python_dependencies": versions, "personal_data_included": False}, indent=2), encoding="utf-8")
    print(json.dumps({"folder": str(folder), "work": str(work)}, indent=2), flush=True)
    return folder


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--node", type=Path)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--installer", type=Path, help="Path to the NSIS compiler")
    parser.add_argument("--folder", type=Path, help="Use an already built payload for the installer")
    args = parser.parse_args()
    if args.archive:
        archive(args.archive.resolve())
    else:
        if args.folder:
            if not args.installer:
                parser.error("--folder requires --installer")
            folder = args.folder.resolve()
        elif not args.node or not args.node.is_file():
            parser.error("--node must point to the bundled Windows x64 node.exe")
        else:
            folder = build(args.node.resolve())
        if args.installer:
            installer(folder, args.installer.resolve())
