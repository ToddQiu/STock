import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import shutil
import subprocess
import tarfile
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("stock_deploy", ROOT / "deploy/manage.py")
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


class DeploymentTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows launch entry')
    def test_offline_launcher_works_without_get_file_hash_and_rejects_tampering(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copyfile(ROOT / 'deploy/Launch-STock.ps1', root / 'Launch-STock.ps1')
            archive = root / 'STock-release.tar.gz'
            archive.write_bytes(b'offline checksum fixture')
            (root / 'STock-release.sha256').write_text(deploy.digest(archive), encoding='ascii')
            command = [shutil.which('powershell.exe'), '-NoProfile', '-NonInteractive', '-Command',
                       "function Get-FileHash { throw 'Unavailable in this host' }; "
                       f"& '{str(root / 'Launch-STock.ps1').replace(chr(39), chr(39)*2)}' -CheckOnly"]
            result = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('Offline check only', result.stdout)
            archive.write_bytes(b'tampered')
            result = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Release checksum failed', result.stderr)

    @unittest.skipUnless(os.name == 'posix', 'Linux deployment lock')
    def test_deployment_root_and_concurrent_run_are_protected(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'private.txt').write_text('private')
            with self.assertRaises(ValueError):
                deploy.Deployment(root)
            first = deploy.Deployment(root / 'dedicated')
            try:
                with self.assertRaises(BlockingIOError):
                    deploy.Deployment(root / 'dedicated')
                self.assertEqual((root / 'private.txt').read_text(), 'private')
            finally:
                first.lock.close()

    def make_bundle(self, root, *, extra=None, damaged=False):
        files = {"deploy/compose.yaml": b"services: {}", "deploy/Dockerfile": b"FROM python",
                 "config.example.json": b"{}"}
        manifest = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
        files["PUBLICATION-MANIFEST.json"] = json.dumps(manifest).encode()
        if damaged:
            files["config.example.json"] = b"tampered"
        if extra:
            files[extra] = b"not approved"
        archive = root / "release.tar.gz"
        with tarfile.open(archive, "w:gz") as output:
            for name, data in files.items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                output.addfile(info, io.BytesIO(data))
        return archive, deploy.digest(archive)

    def test_valid_bundle_round_trip(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            archive, checksum = self.make_bundle(root)
            deploy.unpack_bundle(archive, checksum, root / "release")
            self.assertEqual((root / "release/config.example.json").read_text(), "{}")

    def test_modified_archive_rejected_before_writes(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            archive, checksum = self.make_bundle(root)
            archive.write_bytes(archive.read_bytes() + b"corrupted")
            with self.assertRaisesRegex(ValueError, "checksum"):
                deploy.unpack_bundle(archive, checksum, root / "release")
            self.assertFalse((root / "release").exists())

    def test_manifest_tampering_and_unlisted_files_rejected(self):
        for options in ({"damaged": True}, {"extra": "config.json"}, {"extra": "data/private.db"}):
            with self.subTest(options=options), TemporaryDirectory() as directory:
                root = Path(directory)
                archive, checksum = self.make_bundle(root, **options)
                with self.assertRaises(ValueError):
                    deploy.unpack_bundle(archive, checksum, root / "release")
                self.assertFalse((root / "release").exists())

    def test_archive_path_and_link_rejection(self):
        for name in ("../escape", "/etc/passwd", "a/../../escape", "a\\b", "a//b", "./a", "-x"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                deploy.safe_member(name)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "link.tar.gz"
            with tarfile.open(archive, "w:gz") as output:
                info = tarfile.TarInfo("symlink")
                info.type = tarfile.SYMTYPE
                info.linkname = "/etc/passwd"
                output.addfile(info)
            with self.assertRaisesRegex(ValueError, "regular"):
                deploy.unpack_bundle(archive, deploy.digest(archive), root / "release")

    def test_hostname_validation_rejects_injection(self):
        self.assertEqual(deploy.hostname("APP.Example.COM"), "app.example.com")
        for name in ("a.com;id", "https://a.com", "a.com/x", "a.com:443", "a.com\n", "a..com",
                     "a.-bad.com", "127.0.0.1", "app.local", "localhost", "a b.com"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                deploy.hostname(name)

    def test_dns_matches_only_intended_public_server(self):
        def rows(*addresses):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443)) for ip in addresses]
        with patch.object(deploy.socket, "getaddrinfo", return_value=rows("8.8.8.8")):
            deploy.check_dns("app.example.com", "8.8.8.8")
        for result in (rows("1.1.1.1"), rows("8.8.8.8", "::1")):
            with patch.object(deploy.socket, "getaddrinfo", return_value=result), self.assertRaises(ValueError):
                deploy.check_dns("app.example.com", "8.8.8.8")
        with self.assertRaises(ValueError):
            deploy.check_dns("app.example.com", "127.0.0.1")

    def test_backup_failure_restarts_existing_app(self):
        with TemporaryDirectory() as directory:
            manager = object.__new__(deploy.Deployment)
            manager.root = Path(directory)
            state = {"release": "old"}
            with patch.object(manager, "compose", side_effect=[None, RuntimeError("disk full"), None]) as compose:
                with self.assertRaisesRegex(RuntimeError, "disk full"):
                    manager.backup(state)
                self.assertEqual(compose.call_args.args, (state, "start", "app"))
            self.assertFalse(list((manager.root / "backups").glob("*.tar.gz")))

    def test_corrupt_backup_never_stops_or_replaces_current_data(self):
        with TemporaryDirectory() as directory:
            manager = object.__new__(deploy.Deployment)
            manager.root = Path(directory)
            (manager.root / "backups").mkdir()
            (manager.root / "backups/test.tar.gz").write_bytes(b"bad")
            with patch.object(manager, "compose") as compose, self.assertRaises(ValueError):
                manager.rollback({"file": "test.tar.gz", "sha256": "0" * 64})
            compose.assert_not_called()

    def test_rollback_creates_new_volume_and_records_only_after_verification(self):
        with TemporaryDirectory() as directory:
            manager = object.__new__(deploy.Deployment)
            manager.root = Path(directory)
            (manager.root / "backups").mkdir()
            path = manager.root / "backups/test.tar.gz"
            path.write_bytes(b"fake archive; compose is mocked")
            state = {"domain": "app.example.com", "volume": "old_volume"}
            record = {"file": path.name, "sha256": deploy.digest(path), "state": state}
            with patch.object(manager, "compose") as compose, patch.object(manager, "key", return_value="key"), \
                    patch.object(deploy, "verify_site") as verify:
                manager.rollback(record)
            result = json.loads((manager.root / "current.json").read_text())
            self.assertNotEqual(result["volume"], state["volume"])
            verify.assert_called_once()
            self.assertFalse(any("down" in call.args or "rm" in call.args for call in compose.call_args_list))

    def test_https_redirects_never_forward_credentials(self):
        self.assertIsNone(deploy.NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.example"))

    def test_site_verification_covers_new_modules_and_requires_protected_resources(self):
        from types import SimpleNamespace
        seen = set()
        broken = set()

        def open_response(request, timeout):
            path = urlsplit(request.full_url).path
            authenticated = bool(request.headers.get('Authorization'))
            seen.add((path, authenticated))
            status = 200 if authenticated else 401
            if authenticated and (path in broken or path == '/api/assistant/status'):
                status = 404 if path in broken else 403
            body = (b'data-view-target="portfolio" id="research-backtest-panel" id="research-valuation-panel"'
                    if path == '/' else b'{}')
            response = io.BytesIO(body)
            response.status = status
            return response

        with patch.object(deploy, 'build_opener', return_value=SimpleNamespace(open=open_response)):
            deploy.verify_site('stock.example.com', 'key')
            for path in ('/valuation.js', '/backtest.js', '/portfolio_books.js', '/api/portfolios',
                         '/api/research/valuations', '/api/portfolio/backtests'):
                self.assertIn((path, True), seen)
                self.assertIn((path, False), seen)
            broken.add('/valuation.js')
            with self.assertRaisesRegex(RuntimeError, '/valuation.js HTTP 404'):
                deploy.verify_site('stock.example.com', 'key')


if __name__ == "__main__":
    unittest.main()
