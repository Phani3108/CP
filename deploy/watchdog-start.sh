#!/usr/bin/env bash
# One-time setup + launch for the demo self-healing watchdog.
#
#   1. Create a Vercel token: https://vercel.com/account/tokens  (scope: Chalkboard team)
#   2. echo 'YOUR_TOKEN' > ~/.cp_vercel_token && chmod 600 ~/.cp_vercel_token
#   3. ./deploy/watchdog-start.sh
set -uo pipefail

ROOT="/Users/phanitejamarpaka/Downloads/ContractsPulse-Aayush"
SCRATCH="/private/tmp/claude-501/-Users-phanitejamarpaka-Downloads-ContractsPulse-Aayush/f5cb23dd-890d-4963-a385-785dab1f8623/scratchpad"
CF_LOG="$SCRATCH/cf-tunnel.log"
PROXY_DIR="$ROOT/deploy/vercel-proxy"
export PATH="$HOME/.local/node20/bin:$PATH"
VERCEL="$HOME/.local/node20/bin/vercel"
[ -f "$HOME/.cp_corp_ca.pem" ] && export NODE_EXTRA_CA_CERTS="$HOME/.cp_corp_ca.pem"
TEAM="chalkboard1"

TOKEN="$(cat "$HOME/.cp_vercel_token" 2>/dev/null || true)"
[ -z "$TOKEN" ] && { echo "Put your Vercel token in ~/.cp_vercel_token first (chmod 600)."; exit 1; }

URL=$(grep -hoE "https://[a-z0-9-]+\.trycloudflare\.com" "$CF_LOG" | head -1)
[ -z "$URL" ] && { echo "No current tunnel URL in $CF_LOG — is cloudflared running?"; exit 1; }
echo "Current tunnel: $URL"

# Proxy dir is gitignored (per-account link state) — create it on the fly.
# IMPORTANT: rewrites-only proxy, NO static index.html — a static file at / would shadow the
# "/" rewrite and Vercel would serve it instead of forwarding to the app.
mkdir -p "$PROXY_DIR"
rm -f "$PROXY_DIR/index.html"

cat > "$PROXY_DIR/vercel.json" <<EOF
{
  "rewrites": [
    { "source": "/", "destination": "$URL/" },
    { "source": "/:path*", "destination": "$URL/:path*" }
  ]
}
EOF

echo "Linking proxy dir to the jaggaer-decoder project…"
( cd "$PROXY_DIR" && "$VERCEL" link --yes --project jaggaer-decoder --scope "$TEAM" --token "$TOKEN" )

echo "Verifying a production deploy works now…"
( cd "$PROXY_DIR" && "$VERCEL" deploy --prod --yes --token "$TOKEN" )

echo "Launching watchdog under caffeinate…"
VERCEL_TOKEN="$TOKEN" nohup caffeinate -s "$ROOT/deploy/tunnel-watchdog.sh" >> "$SCRATCH/watchdog.log" 2>&1 &
echo "✔ watchdog started (pid $!).  Follow it:  tail -f $SCRATCH/watchdog.log"
