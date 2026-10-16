"""Make an offline launch kit from the existing publication allowlist, never runtime data."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tarfile

from build_github_pages import ROOT, build


def package(output):
    output = output.resolve()
    if ROOT not in output.parents or output.exists():
        raise ValueError("Use a new output directory inside this project")
    source = output / "source"
    result = build(source)
    archive = output / "STock-release.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                bundle.add(path, arcname=path.relative_to(source).as_posix(), recursive=False)
    with archive.open("rb") as handle:
        checksum = hashlib.file_digest(handle, "sha256").hexdigest()
    (output / "STock-release.sha256").write_text(checksum + "\n", encoding="ascii")
    for name in ("Launch-STock.ps1", "Launch-STock.cmd", "manage.py", "READY-TO-LAUNCH.md"):
        shutil.copyfile(ROOT / "deploy" / name, output / name)
    result.update(archive=str(archive), sha256=checksum)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    print(json.dumps(package(parser.parse_args().output), ensure_ascii=False, indent=2))
