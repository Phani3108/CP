#!/usr/bin/env bash
# Self-healing watchdog for the demo bridge (laptop backend + Cloudflare quick tunnel + Vercel).
#
# SAFETY RULE (learned the hard way): this script NEVER touches the backend.
# An earlier version pkill'd uvicorn when a 5s health check timed out — but the backend
# legitimately blocks longer than that during LLM contract analysis, so it killed a HEALTHY
# backend and then failed to restart it, causing a full outage. The backend has never crashed
# on its own; only the quick tunnel dies. So the watchdog's ONLY job is:
#   tunnel died  ->  restart tunnel  ->  repoint the Vercel proxy at the new URL.
#
# Needs a Vercel token in ~/.cp_vercel_token; proxy dir must be linked to the project.
set -uo pipefail

ROOT="/Users/phanitejamarpaka/Downloads/ContractsPulse-Aayush"
SCRATCH="/private/tmp/claude-501/-Users-phanitejamarpaka-Downloads-ContractsPulse-Aayush/f5cb23dd-890d-4963-a385-785dab1f8623/scratchpad"
CF_LOG="$SCRATCH/cf-tunnel.log"
WD_LOG="$SCRATCH/watchdog.log"
PROXY_DIR="$ROOT/deploy/vercel-proxy"
PUBLIC="https://jaggaercontracts.vercel.app/health"
LOCAL="http://127.0.0.1:9432/health"
CF="$ROOT/frontend/node_modules/cloudflared/bin/cloudflared"
export PATH="$HOME/.local/node20/bin:$PATH"
VERCEL="$HOME/.local/node20/bin/vercel"
# Trust the corporate (Zscaler) CA so the Vercel CLI's TLS validates through the proxy.
[ -f "$HOME/.cp_corp_ca.pem" ] && export NODE_EXTRA_CA_CERTS="$HOME/.cp_corp_ca.pem"

: "${VERCEL_TOKEN:=$(cat "$HOME/.cp_vercel_token" 2>/dev/null || true)}"

log(){ echo "$(date '+%F %T') $*" >> "$WD_LOG"; }

restart_tunnel_and_repoint(){
  log "tunnel appears dead — restarting cloudflared"
  pkill -9 -f "cloudflared tunnel --url" 2>/dev/null; sleep 2
  : > "$CF_LOG"
  nohup caffeinate -s "$CF" tunnel --url http://localhost:9432 >> "$CF_LOG" 2>&1 &
  local url=""
  for _ in $(seq 1 45); do
    sleep 1
    # CRITICAL: cloudflared logs "https://api.trycloudflare.com" (its own registration API)
    # BEFORE the assigned hostname. A naive `head -1` picks the API host — which then serves
    # 404 at "/" (breaking the site) while answering 200 at "/health" (fooling this watchdog).
    # Always exclude api.* and take the real assigned tunnel hostname.
    url=$(grep -hoE "https://[a-z0-9-]+\.trycloudflare\.com" "$CF_LOG" | grep -v '//api\.' | head -1)
    [ -n "$url" ] && grep -q "Registered tunnel connection" "$CF_LOG" && break
  done
  if [ -z "$url" ]; then log "no new tunnel url — retry next cycle"; return 1; fi
  log "new tunnel: $url — repointing Vercel"
  # rewrites-only proxy; NO static index.html (a file at / would shadow the "/" rewrite)
  rm -f "$PROXY_DIR/index.html"
  cat > "$PROXY_DIR/vercel.json" <<EOF
{
  "rewrites": [
    { "source": "/", "destination": "$url/" },
    { "source": "/(.*)", "destination": "$url/\$1" }
  ]
}
EOF
  ( cd "$PROXY_DIR" && "$VERCEL" deploy --prod --yes --token "$VERCEL_TOKEN" >> "$WD_LOG" 2>&1 )
  log "Vercel redeploy submitted; waiting for propagation"
  sleep 45
}

[ -z "${VERCEL_TOKEN:-}" ] && log "WARNING: no VERCEL_TOKEN — cannot repoint Vercel."
log "watchdog started (pid $$) — tunnel-only, never touches the backend"

fail=0
while true; do
  sleep 60
  # Generous timeout: the backend can legitimately block during LLM analysis. If it doesn't
  # answer we simply DO NOTHING (never kill it) — a busy backend is not a dead backend.
  if ! curl -sf -m 25 "$LOCAL" 2>/dev/null | grep -q '"ok"'; then
    log "local backend not answering (busy or down) — taking NO action by design"
    fail=0
    continue
  fi
  # Backend is definitely alive. If the public URL is still failing, the tunnel is at fault.
  # Match the BODY ("ok"), not just HTTP 200: a wrong upstream (e.g. api.trycloudflare.com)
  # returns 200 on /health while serving 404 at "/", which previously fooled this check.
  if curl -sf -m 15 "$PUBLIC" 2>/dev/null | grep -q '"ok"'; then
    fail=0
  else
    fail=$((fail+1))
    log "public health failed ($fail/2) while backend is healthy"
    if [ "$fail" -ge 2 ]; then restart_tunnel_and_repoint; fail=0; fi
  fi
done
