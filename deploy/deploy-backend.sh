#!/usr/bin/env bash
# Deploy the ContractsPulse backend to Google Cloud Run.
#
# Prereqs (one-time):
#   1. Install gcloud:  https://cloud.google.com/sdk/docs/install
#   2. gcloud auth login
#   3. Create a Neon Postgres project (https://neon.tech), then in its SQL editor:
#        CREATE EXTENSION IF NOT EXISTS vector;
#      Copy the connection string (postgresql://...sslmode=require).
#   4. export DATABASE_URL='postgresql://USER:PASS@HOST/neondb?sslmode=require'
#   5. export GEMINI_API_KEY='AQ....'   (the Jaggaer Labs hackathon key)
#
# Then:  ./deploy/deploy-backend.sh
set -euo pipefail

PROJECT="${GCP_PROJECT:-278128424691}"
REGION="${GCP_REGION:-us-central1}"
SERVICE="${SERVICE_NAME:-contractspulse-api}"

: "${DATABASE_URL:?Set DATABASE_URL to your Neon connection string (with sslmode=require)}"
: "${GEMINI_API_KEY:?Set GEMINI_API_KEY to the Gemini API key}"

JWT_SECRET="${JWT_SECRET:-$(openssl rand -hex 24)}"

gcloud run deploy "$SERVICE" \
  --source backend/ \
  --project "$PROJECT" \
  --region "$REGION" \
  --allow-unauthenticated \
  --timeout 300 \
  --min-instances 1 \
  --no-cpu-throttling \
  --memory 1Gi \
  --set-env-vars "^@^DATABASE_URL=${DATABASE_URL}@OPENAI_API_KEY=${GEMINI_API_KEY}@GEMINI_API_KEY=${GEMINI_API_KEY}@OPENAI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/@OPENAI_MODEL_EXTRACTOR=openai:gemini-3.5-flash@OPENAI_MODEL_RISK=openai:gemini-3.5-flash@OPENAI_MODEL_CHAT=gemini-3.5-flash@OPENAI_MODEL_CHAT_FALLBACK=gemini-flash-latest@OPENAI_EMBEDDING_MODEL=gemini-embedding-001@OPENAI_EMBEDDING_DIMENSIONS=1536@CONTRACT_ANALYSIS_TIMEOUT_S=240@JWT_SECRET=${JWT_SECRET}@DISABLE_SIGNUP=false"

echo
echo "✔ Deployed. Copy the service URL above into frontend/vercel.json"
echo "  (replace REPLACE-WITH-CLOUD-RUN-URL.run.app), then redeploy the frontend."
echo "  JWT_SECRET used: ${JWT_SECRET}"
