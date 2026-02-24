#!/bin/bash
set -euo pipefail

PROJECT="the-golden-codex-1111"
SERVICE="fluora-mcp"
REGION="us-west1"
IMAGE="gcr.io/${PROJECT}/${SERVICE}:latest"

echo "Building TypeScript..."
npm run build

echo "Building Docker image..."
docker build -t "${IMAGE}" .

echo "Pushing to GCR..."
docker push "${IMAGE}"

echo "Deploying to Cloud Run..."
gcloud run deploy "${SERVICE}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --image="${IMAGE}" \
  --platform=managed \
  --allow-unauthenticated \
  --memory=512Mi \
  --min-instances=0 \
  --max-instances=3 \
  --set-secrets="CDP_API_KEY_ID=COINBASE_API:latest,CDP_API_KEY_SECRET=COINBASE_PRIVATE_KEY:latest" \
  --set-env-vars="SERVER_WALLET_ADDRESS=0xFE141943a93c184606F3060103D975662327063B,DATA_PORTAL_URL=https://data-portal-172867820131.us-west1.run.app"

echo "Done! Service URL:"
gcloud run services describe "${SERVICE}" --project="${PROJECT}" --region="${REGION}" --format="value(status.url)"
