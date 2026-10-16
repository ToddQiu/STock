#!/usr/bin/env bash
set -euo pipefail
test "${GITHUB_ACTIONS:-}" = true || { echo 'Disposable GitHub CI only; never run on a production host.'; exit 1; }
export STOCK_DOMAIN=stock.test STOCK_WORKSPACE_VOLUME="stock_ci_${GITHUB_RUN_ID}"
export STOCK_KEY_FILE="$PWD/deploy/secrets/access-key.txt"
mkdir -p deploy/secrets
python3 -c 'import secrets; print(secrets.token_urlsafe(32))' > "$STOCK_KEY_FILE"
chmod 444 "$STOCK_KEY_FILE"
cat > deploy/Caddyfile.ci <<'EOF'
stock.test {
    tls internal
    reverse_proxy app:8080
}
EOF
cat > deploy/compose.ci.yaml <<'EOF'
services:
  https:
    ports: !override ["127.0.0.1:8088:80", "127.0.0.1:8443:443"]
    volumes:
      - ./Caddyfile.ci:/etc/caddy/Caddyfile:ro
EOF
compose=(docker compose -p stock-ci -f deploy/compose.yaml -f deploy/compose.ci.yaml)
cleanup() {
  "${compose[@]}" logs --tail=60 || true
  # This script is explicitly restricted to disposable CI, not a real shared workspace.
  "${compose[@]}" down --volumes || true
}
trap cleanup EXIT
"${compose[@]}" config --quiet
"${compose[@]}" build
"${compose[@]}" up -d --wait --wait-timeout 180
"${compose[@]}" exec -T app python - < deploy/check-container.py
"${compose[@]}" restart app
"${compose[@]}" up -d --wait --wait-timeout 180
"${compose[@]}" exec -T app python - --persist < deploy/check-container.py
"${compose[@]}" exec -T https cat /data/caddy/pki/authorities/local/root.crt > deploy/ci-ca.crt
test "$(curl --silent --show-error --cacert deploy/ci-ca.crt --resolve stock.test:8443:127.0.0.1 \
  -H 'Host: stock.test' -o /dev/null -w '%{http_code}' https://stock.test:8443/api/health)" = 401
test "$(curl --silent --show-error --cacert deploy/ci-ca.crt --resolve stock.test:8443:127.0.0.1 \
  -H 'Host: stock.test' -u "stock:$(cat "$STOCK_KEY_FILE")" -o /dev/null -w '%{http_code}' \
  https://stock.test:8443/api/health)" = 200
echo 'Caddy HTTPS with a trusted CI CA, unauthenticated denial and authenticated proxy passed.'
