#!/usr/bin/env bash
# One-time provisioning for ALPHABETTI on the mdeller.com droplet.
# Idempotent: safe to re-run. Run deploy/deploy.sh for ordinary updates.
set -euo pipefail

APP=alphabetti
ROOT=/opt/$APP
SERVER_NAME=${SERVER_NAME:-alphabetti.mdeller.com}

echo "==> user and directories"
id -u $APP >/dev/null 2>&1 || useradd --system --home "$ROOT" --shell /usr/sbin/nologin $APP
mkdir -p "$ROOT"/{instance,deploy}
chown -R $APP:$APP "$ROOT"

echo "==> python environment"
apt-get update -qq
# freesasa builds from source on this box, so it needs a compiler and headers.
apt-get install -y -qq python3-venv python3-dev build-essential nginx
[ -d "$ROOT/.venv" ] || python3 -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/pip" install -q --upgrade pip
"$ROOT/.venv/bin/pip" install -q -r "$ROOT/requirements.txt"
chown -R $APP:$APP "$ROOT/.venv"

echo "==> DSSP, if it is available"
# Not fatal. Without it the app falls back to the CA-only P-SEA assignment,
# which agrees with DSSP on 83.7% of residues and is reported as such in the UI.
apt-get install -y -qq dssp 2>/dev/null || echo "    dssp not available; using the geometric fallback"

echo "==> systemd"
install -m 0644 "$ROOT/deploy/$APP-web.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable $APP-web

echo "==> nginx"
install -m 0644 "$ROOT/deploy/nginx-$APP-limits.conf" /etc/nginx/conf.d/$APP-limits.conf
sed "s|__SERVER_NAME__|$SERVER_NAME|g" "$ROOT/deploy/nginx-$APP.conf" \
    > /etc/nginx/sites-available/$APP
ln -sf /etc/nginx/sites-available/$APP /etc/nginx/sites-enabled/$APP
nginx -t
systemctl reload nginx

echo "==> starting"
systemctl restart $APP-web
sleep 3
systemctl is-active --quiet $APP-web || { journalctl -u $APP-web -n 40 --no-pager; exit 1; }

cat <<NOTE

Provisioned. Two things remain and both need a human:

  1. TLS. mdeller.com's certificate does not cover subdomains:
         certbot --nginx -d $SERVER_NAME
     Then patch HTTP/2 onto the line certbot wrote. This box runs nginx 1.24,
     so it is the per-listener form:
         listen 443 ssl http2;
     NOT 'http2 on;', which is 1.25.1+ and fails nginx -t.

  2. A Hugging Face token in $ROOT/.env, or every fold fails on quota with an
     error that does not mention quota:
         HF_TOKEN=hf_...

NOTE
