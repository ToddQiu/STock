"""Runs ONLY inside an isolated CI container, never against personal holdings."""
import base64
from http.client import HTTPConnection
import json
from pathlib import Path
import sys
import time

key = Path("/run/secrets/access_key").read_text().strip()
auth = "Basic " + base64.b64encode(("stock:" + key).encode()).decode()


def request(path, method="GET", data=None, *, authorized=True, origin="https://stock.test"):
    headers = {"Host": "stock.test"}
    if authorized:
        headers["Authorization"] = auth
    if method != "GET":
        headers.update(Origin=origin, **{"Content-Type": "application/json"})
    connection = HTTPConnection("127.0.0.1", 8080, timeout=30)
    try:
        connection.request(method, path, None if data is None else json.dumps(data), headers)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


config = Path("/var/lib/stock/config.json")
if "--persist" in sys.argv:
    assert json.loads(config.read_text())["portfolio"]["principal_cny"] == 321
    assert request("/api/portfolio/principal", "POST", {"principal_cny": 0})[0] == 200
    print("Container restart preserved principal and the same workspace.")
    raise SystemExit(0)

for path in ("/", "/app.js", "/research.js", "/api/dashboard", "/api/research/export", "/api/health"):
    assert request(path, authorized=False)[0] == 401, path
    assert request(path)[0] == 200, path
assert request("/api/portfolio/principal", "POST", {"principal_cny": 999}, origin="https://evil.example")[0] == 403
assert request("/api/portfolio/principal", "POST", {"principal_cny": 321})[0] == 200
for path in ("/config.json", "/data/monitor.db", "/../config.json"):
    assert request(path)[0] == 404, path
for path in ("/api/health", "/api/dashboard", "/api/news", "/api/assets", "/api/indices",
             "/api/social", "/api/features", "/api/fundamentals", "/api/research",
             "/api/research/cards", "/api/research/companies", "/api/research/earnings"):
    status, body = request(path)
    assert status == 200 and isinstance(json.loads(body), dict), path
print("Real container HTTP, authentication, CSRF, private paths and full-module APIs passed.")

from tempfile import TemporaryDirectory
from quant_news_monitor.news_translation import NewsTranslator
with TemporaryDirectory() as directory:
    translator = NewsTranslator(Path(directory))
    translator.start()
    try:
        headline = "Gold prices rise as the dollar falls."
        result = None
        for _ in range(150):
            result, status = translator.lookup(headline)
            if result:
                break
            if translator.status()["error"]:
                raise RuntimeError(translator.status()["error"])
            time.sleep(1)
        assert result and any("\u3400" <= character <= "\u9fff" for character in result)
        print("Linux local English-to-Chinese model inference passed.")
    finally:
        translator.shutdown()
