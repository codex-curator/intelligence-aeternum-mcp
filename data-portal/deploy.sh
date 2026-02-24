#!/bin/bash
# Deploy data-portal to Cloud Run
set -euo pipefail

PROJECT_ID="the-golden-codex-1111"
SERVICE_NAME="data-portal"
REGION="us-west1"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}:latest"

echo "=== Deploying ${SERVICE_NAME} ==="
echo "Project: ${PROJECT_ID}"
echo "Region:  ${REGION}"

# Build container image
echo "Building container image..."
gcloud builds submit --tag $IMAGE --project $PROJECT_ID

# Deploy to Cloud Run
echo "Deploying to Cloud Run..."
gcloud run deploy $SERVICE_NAME \
    --image $IMAGE \
    --region $REGION \
    --platform managed \
    --allow-unauthenticated \
    --memory 512Mi \
    --min-instances 0 \
    --max-instances 5 \
    --set-env-vars "GCP_PROJECT=${PROJECT_ID},DATA_BUCKET=alexandria-download-1m" \
    --project $PROJECT_ID

echo ""
echo "=== Deployment complete ==="
URL=$(gcloud run services describe "${SERVICE_NAME}" --region "${REGION}" --project "${PROJECT_ID}" --format='value(status.url)')
echo "URL:     ${URL}"
echo "Health:  ${URL}/health"
echo "Docs:    ${URL}/docs"
echo "Catalog: ${URL}/catalog/datasets"
