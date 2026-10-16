"""Deploy an authenticated STock workspace, without any cloud purchasing API."""
from __future__ import annotations

import argparse
import base64
import hashlib
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import socket
import subprocess
import tarfile
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


API_PATHS = ("/api/health", "/api/dashboard", "/api/news", "/api/assets", "/api/indices",
             "/api/social", "/api/features", "/api/fundamentals", "/api/research",
             "/api/research/cards", "/api/research/companies", "/api/research/earnings")


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def hostname(value):
    value = value.lower()
    if (len(value) > 253 or "." not in value or value.endswith((".localhost", ".local"))
            or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                   for label in value.split("."))):
        raise ValueError("Use a real DNS hostname, without scheme, port or path")
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return value
    raise ValueError("A DNS hostname, not an IP address, is required for this HTTPS setup")


def check_dns(domain, expected_ip):
    expected = ipaddress.IPv4Address(expected_ip)
    if not expected.is_global:
        raise ValueError("Server address must be a public IPv4 address")
    found = {row[4][0] for row in socket.getaddrinfo(domain, 443, type=socket.SOCK_STREAM)}
    if found != {str(expected)}:
        raise ValueError(f"DNS must resolve only to {expected}; remove stale A/AAAA records: {sorted(found)}")


def safe_member(name):
    path = PurePosixPath(name)
    if (not name or path.is_absolute() or ".." in path.parts or "\\" in name
            or str(path) != name or name.startswith("-")):
        raise ValueError(f"Unsafe archive path: {name}")
    return path


def unpack_bundle(archive, expected, destination):
    if not re.fullmatch(r"[a-f0-9]{64}", expected) or digest(archive) != expected:
        raise ValueError("Release archive checksum mismatch")
    with tarfile.open(archive, "r:gz") as bundle:
        members = bundle.getmembers()
        names = [member.name for member in members]
        if len(names) != len(set(names)) or len(members) > 1000:
            raise ValueError("Duplicate entries or oversized release")
        for member in members:
            safe_member(member.name)
            if not member.isfile() or member.size > 1_000_000:
                raise ValueError("Release may only contain bounded regular source files")
        manifest = json.load(bundle.extractfile("PUBLICATION-MANIFEST.json"))
        if set(names) != set(manifest) | {"PUBLICATION-MANIFEST.json"}:
            raise ValueError("Release does not match the publication allowlist")
        if not {"deploy/compose.yaml", "deploy/Dockerfile", "config.example.json"} <= set(manifest):
            raise ValueError("Incomplete deployment release")
        for member in members:
            content = bundle.extractfile(member).read()
            if member.name in manifest and hashlib.sha256(content).hexdigest() != manifest[member.name]:
                raise ValueError(f"Manifest mismatch: {member.name}")
        for member in members:
            target = destination.joinpath(*safe_member(member.name).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as output:
                output.write(bundle.extractfile(member).read())


def run(command, **kwargs):
    return subprocess.run(command, check=True, timeout=2700, **kwargs)


def save_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def verify_site(domain, key, *, attempts=1):
    opener = build_opener(ProxyHandler({}), NoRedirect())
    authorization = "Basic " + base64.b64encode(("stock:" + key).encode()).decode()

    def request(path, authenticated):
        headers = {"Authorization": authorization} if authenticated else {}
        try:
            with opener.open(Request("https://" + domain + path, headers=headers), timeout=30) as response:
                return response.status, response.read(5_000_000)
        except HTTPError as error:
            return error.code, error.read(1024)

    for attempt in range(attempts):
        try:
            if request("/api/health", True)[0] == 200:
                break
        except (URLError, OSError):
            pass
        if attempt + 1 == attempts:
            raise RuntimeError("HTTPS health check failed; check DNS, ports 80/443 and Caddy logs")
        time.sleep(5)
    for path in ("/", "/app.js", *API_PATHS, "/api/research/export"):
        if request(path, False)[0] != 401:
            raise RuntimeError(f"Authentication guard failed: {path}")
        status, content = request(path, True)
        if status != 200:
            raise RuntimeError(f"Authenticated request failed: {path} HTTP {status}")
        if path == "/" and b'data-view-target="portfolio"' not in content:
            raise RuntimeError("Not the full STock application")
        if path in API_PATHS:
            payload = json.loads(content)
            if not isinstance(payload, dict) or payload.get("error"):
                raise RuntimeError(f"API did not return a valid snapshot: {path}")
    print("HTTPS, authentication, full UI, research export and 12 APIs passed. Source freshness still requires review.")


class Deployment:
    def __init__(self, root):
        self.root = Path(root).resolve()
        marker = self.root / ".stock-deployment"
        if self.root.exists() and any(self.root.iterdir()) and not marker.is_file():
            raise ValueError("Deployment directory must be empty or a marked STock deployment")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        marker.touch(exist_ok=True)
        # A second launcher must not modify the same instance during a backup or rollout.
        import fcntl
        self.lock = (self.root / "deploy.lock").open("w")
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def read_state(self, name="current.json"):
        path = self.root / name
        return json.loads(path.read_text()) if path.exists() else None

    def compose(self, state, *arguments, **kwargs):
        environment = dict(os.environ, STOCK_DOMAIN=state["domain"], STOCK_IMAGE=state["image"],
                           STOCK_WORKSPACE_VOLUME=state["volume"], STOCK_KEY_FILE=str(self.root / "access-key.txt"))
        return run(["docker", "compose", "-p", "stock", "-f",
                    str(self.root / "releases" / state["release"] / "deploy/compose.yaml"), *arguments],
                   env=environment, **kwargs)

    def key(self):
        value = (self.root / "access-key.txt").read_text().strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", value):
            raise ValueError("Invalid access key; restore it, never silently replace it")
        return value

    def backup(self, state):
        directory = self.root / "backups"
        directory.mkdir(exist_ok=True, mode=0o700)
        path = directory / (time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3) + ".tar.gz")
        self.compose(state, "stop", "app")
        try:
            with path.with_suffix(".partial").open("xb") as output:
                self.compose(state, "run", "--rm", "--no-deps", "-T", "--entrypoint", "tar", "app",
                             "-C", "/var/lib/stock", "-czf", "-", ".", stdout=output)
            path.with_suffix(".partial").replace(path)
            record = {"state": state, "file": path.name, "sha256": digest(path)}
            save_json(path.with_suffix(".json"), record)
            print("Consistent workspace backup: " + str(path))
            return record
        finally:
            self.compose(state, "start", "app")

    def rollback(self, record):
        path = self.root / "backups" / record["file"]
        if digest(path) != record["sha256"]:
            raise ValueError("Backup checksum mismatch; rollback stopped without replacing data")
        state = dict(record["state"], volume="stock_restore_" + secrets.token_hex(8))
        self.compose(state, "stop", "app")
        # Restore into a NEW volume. Never overwrite or delete the failed deployment's data.
        with path.open("rb") as source:
            self.compose(state, "run", "--rm", "--no-deps", "-T", "--entrypoint", "tar", "app",
                         "-C", "/var/lib/stock", "-xzf", "-", "--no-same-owner", stdin=source)
        self.compose(state, "up", "-d", "--wait", "--wait-timeout", "180")
        verify_site(state["domain"], self.key(), attempts=24)
        save_json(self.root / "current.json", state)
        print("Previous release restored. Newer data volume retained for recovery.")

    def install(self, archive, checksum, domain, public_ip):
        domain = hostname(domain)
        check_dns(domain, public_ip)
        previous = self.read_state()
        if previous and previous["domain"] != domain:
            raise ValueError("Changing an existing deployment's domain requires a separate migration")
        pending = self.read_state("pending.json") if not previous else None
        if pending and (pending["release"] != checksum or pending["domain"] != domain):
            raise ValueError("A failed first launch is recorded; retry its original package and domain first")
        releases = self.root / "releases"
        releases.mkdir(exist_ok=True)
        destination = releases / checksum
        with tempfile.TemporaryDirectory(dir=releases) as temporary:
            unpack_bundle(archive, checksum, Path(temporary))
            if not destination.exists():
                os.rename(temporary, destination)
        os_release = dict(line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines() if "=" in line)
        if (os_release.get("ID", "").strip('"') != "ubuntu"
                or os_release.get("VERSION_ID", "").strip('"') != "24.04"
                or os.uname().machine != "x86_64"):
            raise ValueError("This release targets a dedicated Ubuntu 24.04 x86-64 server")
        if shutil.disk_usage(self.root).free < 12 * 1024**3:
            raise ValueError("At least 12 GiB free disk required for build and backup")
        run(["bash", str(destination / "deploy/install-docker.sh")], stdin=subprocess.DEVNULL)
        if not (self.root / "access-key.txt").exists():
            if previous:
                raise ValueError("Existing deployment lost its key; restore the key first")
            with (self.root / "access-key.txt").open("x") as output:
                output.write(secrets.token_urlsafe(32) + "\n")
            os.chmod(self.root / "access-key.txt", 0o444)
        self.key()
        state = {"domain": domain, "public_ip": public_ip, "release": checksum,
                 "image": "stock-full:" + checksum[:16],
                 "volume": previous["volume"] if previous else (pending["volume"] if pending
                            else "stock_workspace_" + secrets.token_hex(8))}
        if previous and previous["release"] == checksum:
            self.compose(previous, "up", "-d", "--wait", "--wait-timeout", "180")
            verify_site(domain, self.key(), attempts=24)
            return
        self.compose(state, "config", "--quiet")
        self.compose(state, "build")
        self.compose(state, "pull", "https")
        record = self.backup(previous) if previous else None
        if record:
            save_json(self.root / "previous.json", record)
        save_json(self.root / "pending.json", state)
        try:
            self.compose(state, "up", "-d", "--wait", "--wait-timeout", "180")
            verify_site(domain, self.key(), attempts=24)
        except Exception:
            try:
                self.compose(state, "stop")
            finally:
                if record:
                    self.rollback(record)
            raise
        save_json(self.root / "current.json", state)
        (self.root / "pending.json").unlink(missing_ok=True)
        print("Installed https://" + domain + "; login username: stock")
        print("Read /opt/stock/access-key.txt privately on the server; never put it in a URL or a public log.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("install", "verify", "backup", "rollback", "status"))
    parser.add_argument("--root", type=Path, default=Path("/opt/stock"))
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--sha256")
    parser.add_argument("--domain")
    parser.add_argument("--ip")
    args = parser.parse_args(argv)
    if args.command == "install" and not all((args.bundle, args.sha256, args.domain, args.ip)):
        parser.error("install requires --bundle, --sha256, --domain and --ip")
    if os.name != "posix" or os.geteuid() != 0:
        parser.error("Run on the dedicated Linux server as root or with sudo")
    deployment = Deployment(args.root)
    state = deployment.read_state()
    if args.command == "install":
        deployment.install(args.bundle, args.sha256, args.domain, args.ip)
    elif args.command == "rollback":
        record = deployment.read_state("previous.json")
        if not record:
            parser.error("No previous deployment backup is available")
        deployment.rollback(record)
    elif not state:
        parser.error("No successfully installed deployment is recorded")
    elif args.command == "verify":
        check_dns(state["domain"], state["public_ip"])
        verify_site(state["domain"], deployment.key())
    elif args.command == "backup":
        deployment.backup(state)
    else:
        print(json.dumps(state, indent=2))
        deployment.compose(state, "ps")


if __name__ == "__main__":
    main()
