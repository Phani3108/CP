#!/usr/bin/env bash
# Self-healing watchdog for the demo (2-day bridge until the Cloud Run deploy).
# Keeps the public URL (jaggaer-decoder.vercel.app) alive:
#   - restarts the backend (uvicorn) if it dies
#   - restarts the Cloudflare quick tunnel when the public URL goes down, then repoints the
#     Vercel proxy at the tunnel's NEW url (quick tunnels rotate their url on restart)
#
# Needs a Vercel token (create at https://vercel.com/account/tokens, scope: Chalkboard team).
# Put it in ~/.cp_vercel_token (chmod 600). The proxy dir must already be linked to the
# jaggaer-decoder project — deploy/watchdog-start.sh does that once, then launches this loop.
set -uo pipefail

ROOT="/Users/phanitejamarpaka/Downloads/ContractsPulse-Aayush"
SCRATCH="/private/tmp/claude-501/-Users-phanitejamarpaka-Downloads-ContractsPulse-Aayush/f5cb23dd-890d-4963-a385-785dab1f8623/scratchpad"
CF_LOG="$SCRATCH/cf-tunnel.log"
BE_LOG="$SCRATCH/backend.log"
WD_LOG="$SCRATCH/watchdog.log"
PROXY_DIR="$ROOT/deploy/vercel-proxy"
PUBLIC="https://vercel-proxy-nine-jet.vercel.app/health"
LOCAL="http://127.0.0.1:9432/health"
CF="$ROOT/frontend/node_modules/cloudflared/bin/cloudflared"
export PATH="$HOME/.local/node20/bin:$PATH"
VERCEL="$HOME/.local/node20/bin/vercel"
# Trust the corporate (Zscaler) CA so the Vercel CLI's TLS to api.vercel.com validates through
# the proxy — secure (adds a trusted root), not a verification bypass.
[ -f "$HOME/.cp_corp_ca.pem" ] && export NODE_EXTRA_CA_CERTS="$HOME/.cp_corp_ca.pem"

: "${VERCEL_TOKEN:=$(cat "$HOME/.cp_vercel_token" 2>/dev/null || true)}"

log(){ echo "$(date '+%F %T') $*" >> "$WD_LOG"; }

restart_backend(){
  log "backend DOWN — restarting uvicorn"
  pkill -f "uvicorn app.main:app" 2>/dev/null; sleep 2
  ( cd "$ROOT/backend" && set -a && source ../.env && set +a && \
    nohup caffeinate -s venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 9432 >> "$BE_LOG" 2>&1 & )
  for _ in $(seq 1 30); do sleep 1; curl -sf -m 3 "$LOCAL" >/dev/null 2>&1 && { log "backend recovered"; return 0; }; done
  log "backend restart FAILED"; return 1
}

restart_tunnel_and_repoint(){
  log "tunnel DOWN — restarting cloudflared"
  pkill -f "cloudflared tunnel --url" 2>/dev/null; sleep 2
  : > "$CF_LOG"
  nohup caffeinate -s "$CF" tunnel --url http://localhost:9432 >> "$CF_LOG" 2>&1 &
  local url=""
  for _ in $(seq 1 45); do
    sleep 1
    url=$(grep -hoE "https://[a-z0-9-]+\.trycloudflare\.com" "$CF_LOG" | head -1)
    [ -n "$url" ] && grep -q "Registered tunnel connection" "$CF_LOG" && break
  done
  if [ -z "$url" ]; then log "no new tunnel url — will retry next cycle"; return 1; fi
  log "new tunnel: $url — repointing Vercel"
  cat > "$PROXY_DIR/vercel.json" <<EOF
{
  "rewrites": [
    { "source": "/", "destination": "$url/" },
    { "source": "/:path*", "destination": "$url/:path*" }
  ]
}
EOF
  ( cd "$PROXY_DIR" && "$VERCEL" deploy --prod --yes --token "$VERCEL_TOKEN" >> "$WD_LOG" 2>&1 )
  log "Vercel redeploy submitted; waiting for propagation"
  sleep 40
}

if [ -z "${VERCEL_TOKEN:-}" ]; then
  log "NO VERCEL_TOKEN — watchdog can restart the tunnel/backend but CANNOT repoint Vercel."
fi

log "watchdog started (pid $$)"
fail=0
while true; do
  if ! curl -sf -m 5 "$LOCAL" >/dev/null 2>&1; then restart_backend; fi
  # Only blame the tunnel when the backend is up but the public path is down.
  if curl -sf -m 5 "$LOCAL" >/dev/null 2>&1 && ! curl -sf -m 12 "$PUBLIC" >/dev/null 2>&1; then
    fail=$((fail+1)); log "public health failed ($fail/2)"
    if [ "$fail" -ge 2 ]; then
      if [ -n "${VERCEL_TOKEN:-}" ]; then restart_tunnel_and_repoint; else restart_tunnel_and_repoint || true; fi
      fail=0
    fi
  else
    fail=0
  fi
  sleep 60
done
