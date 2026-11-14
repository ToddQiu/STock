"""Install, verify, exercise and uninstall the share package in an isolated directory."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import winreg


ROOT = Path(__file__).resolve().parents[1]
KEYS = (r"Software\STockDesktop", r"Software\Microsoft\Windows\CurrentVersion\Uninstall\STockDesktop")


def exists(key):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key):
            return True
    except FileNotFoundError:
        return False


def check(setup: Path, node: Path, node_modules: Path):
    if any(exists(key) for key in KEYS):
        raise RuntimeError("An installed STock is already registered; do not replace it during QA")
    target = ROOT / ".qa-runtime" / ("install-share-" + time.strftime("%Y%m%d-%H%M%S"))
    if target.exists():
        raise FileExistsError(target)
    flags = subprocess.CREATE_NO_WINDOW
    report = {"installer": str(setup), "target": str(target), "checks": []}
    subprocess.run([str(setup), "/S", "/NOSHORTCUTS", f"/D={target}"], check=True,
                   creationflags=flags, timeout=600)
    try:
        manifest = json.loads((target / "manifest-sha256.json").read_text(encoding="utf-8"))
        for name, expected in manifest.items():
            path = (target / name).resolve()
            if not path.is_relative_to(target.resolve()):
                raise ValueError("Invalid manifest path")
            with path.open("rb") as handle:
                assert hashlib.file_digest(handle, "sha256").hexdigest() == expected, name
        report["checks"].append(f"All {len(manifest)} installed files match the payload manifest")
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEYS[1]) as key:
            command = winreg.QueryValueEx(key, "UninstallString")[0]
            assert command == f'"{target}\\Uninstall.exe"', command
            assert winreg.QueryValueEx(key, "InstallLocation")[0] == str(target)
        report["checks"].append("Windows uninstall registration points to this exact installation")
        env = {**os.environ, "NODE_PATH": str(node_modules)}
        result = subprocess.run([str(node), str(ROOT / "tests/check_portable_package.cjs"), str(target)],
                                check=True, capture_output=True, text=True, encoding="utf-8",
                                env=env, creationflags=flags, timeout=600)
        smoke = json.loads(result.stdout)
        report["smoke"] = smoke
        profile = Path(smoke["profile"])
        config = (profile / "config.json").read_bytes()
        # Uninstall must leave both external user data and non-shipped local files untouched.
        marker = target / "user-file-not-in-package.txt"
        marker.write_text("Keep this file", encoding="utf-8")
        report["checks"].append("Installed app passes isolated startup, AI, translation and persistence checks")
    finally:
        uninstaller = target / "Uninstall.exe"
        if uninstaller.is_file():
            command = f'"{uninstaller}" /S _?={target}'
            subprocess.run(command, check=True, creationflags=flags, timeout=180)
    assert not (target / "STock.exe").exists()
    assert not any(exists(key) for key in KEYS)
    assert marker.read_text(encoding="utf-8") == "Keep this file"
    assert (profile / "config.json").read_bytes() == config
    report["checks"].append("Uninstall removes application and registration, preserving user data and unknown files")
    report["passed"] = True
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("setup", type=Path)
    parser.add_argument("--node", type=Path, required=True)
    parser.add_argument("--node-modules", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(check(args.setup.resolve(), args.node.resolve(), args.node_modules.resolve()),
                     ensure_ascii=False, indent=2))
