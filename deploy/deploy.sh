#!/usr/bin/env bash
# Sync ALPHABETTI to the droplet and restart it.
# Reads DROPLET_SSH / DROPLET_PATH from .env (see .env.example).
set -euo pipefail
cd "$(dirname "$0")/.."

[ -f .env ] && set -a && . ./.env && set +a
DROPLET_SSH="${DROPLET_SSH:-}"
DROPLET_PATH="${DROPLET_PATH:-/opt/alphabetti}"
if [[ -z "$DROPLET_SSH" ]]; then
    echo "DROPLET_SSH is not set. Copy .env.example to .env and fill it in." >&2
    exit 1
fi

echo "==> syncing to ${DROPLET_SSH}:${DROPLET_PATH}"
# instance/ holds the live cache database and must never be overwritten by a
# deploy: it is the app's memory of every fold anyone has ever asked for.
rsync -az --delete \
    --exclude '.git' --exclude '.venv' --exclude '__pycache__' \
    --exclude 'instance' --exclude '.env' --exclude 'tests' \
    --exclude 'docs' --exclude 'space' --exclude '*.sqlite*' \
    ./ "${DROPLET_SSH}:${DROPLET_PATH}/"

# The remote block runs under `set -e`. Any abort here skips the chown and the
# restart, and the service keeps serving the OLD build while rsync looks fine,
# so the verification below checks the running service rather than this exiting 0.
ssh "$DROPLET_SSH" bash -s <<'REMOTE'
set -euo pipefail
cd /opt/alphabetti
chown -R alphabetti:alphabetti /opt/alphabetti
.venv/bin/pip install -q -r requirements.txt
systemctl restart alphabetti-web
REMOTE

echo "==> verifying"
sleep 4
ssh "$DROPLET_SSH" 'systemctl is-active alphabetti-web && stat -c "%y %n" /opt/alphabetti/app.py'

URL="${ALPHABETTI_URL:-https://alphabetti.mdeller.com}"
code=$(curl -s -o /dev/null -w '%{http_code}' "$URL/healthz" || true)
echo "==> $URL/healthz -> $code"
[ "$code" = "200" ] || { echo "healthz did not return 200"; exit 1; }
curl -s "$URL/healthz" | head -c 400; echo
