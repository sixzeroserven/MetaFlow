#!/usr/bin/env sh
set -eu

export DISPLAY="${DISPLAY:-:99}"
WEB_PORT="${WEB_PORT:-8765}"
VNC_PORT="${VNC_PORT:-5900}"
NOVNC_PORT="${NOVNC_PORT:-7900}"
XVFB_SCREEN="${XVFB_SCREEN:-1440x1100x24}"
PROXY_FORWARD_LISTEN_HOST="${PROXY_FORWARD_LISTEN_HOST:-127.0.0.1}"
PROXY_FORWARD_LISTEN_PORT="${PROXY_FORWARD_LISTEN_PORT:-18080}"

rm -f "/tmp/.X${DISPLAY#:}-lock"
Xvfb "$DISPLAY" -screen 0 "$XVFB_SCREEN" -ac +extension GLX +render -noreset >/tmp/xvfb.log 2>&1 &
sleep 1
fluxbox >/tmp/fluxbox.log 2>&1 &
x11vnc -display "$DISPLAY" -forever -shared -rfbport "$VNC_PORT" -nopw >/tmp/x11vnc.log 2>&1 &
websockify --web=/usr/share/novnc/ "$NOVNC_PORT" "127.0.0.1:$VNC_PORT" >/tmp/novnc.log 2>&1 &

if [ -n "${UPSTREAM_HTTP_PROXY:-}" ]; then
  python proxy_auth_forwarder.py \
    --upstream "$UPSTREAM_HTTP_PROXY" \
    --listen-host "$PROXY_FORWARD_LISTEN_HOST" \
    --listen-port "$PROXY_FORWARD_LISTEN_PORT" \
    >/tmp/proxy-forwarder.log 2>&1 &
fi

if [ "${SERVER_WORKER_ENABLED:-false}" = "true" ]; then
  python worker.py \
    --server "http://127.0.0.1:$WEB_PORT" \
    --worker-id "${SERVER_WORKER_ID:-server}" \
    >/tmp/metaflow-worker.log 2>&1 &
fi

python web_app.py \
  --host 0.0.0.0 \
  --port "$WEB_PORT" \
  --accounts-file "${ACCOUNTS_FILE:-accounts.json}" \
  --env "${ENV_FILE:-.env}"
