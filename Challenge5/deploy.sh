#!/usr/bin/env bash
# ==============================================================================
# Alaska Department of Snow (ADS) - Cloud Run Deployment Script
# Automates:
# 1. GCP API enablement
# 2. Artifact Registry repository setup ('ads-apps')
# 3. Dedicated Service Account creation with least-privilege IAM roles
# 4. Container build & push via Cloud Build
# 5. Cloud Run deployment (2Gi RAM, 2 CPU, secure environment variables)
# ==============================================================================

set -euo pipefail

# Configurations & Defaults
PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
LOCATION="${LOCATION:-us-central1}"
SERVICE_NAME="${SERVICE_NAME:-ads-synthesis-service}"
REPO_NAME="${REPO_NAME:-ads-apps}"
SA_NAME="${SA_NAME:-ads-runner-sa}"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
TEMPLATE_ID="${TEMPLATE_ID:-ads-operational-template}"
TEMPLATE_PATH="projects/${PROJECT_ID}/locations/${LOCATION}/templates/${TEMPLATE_ID}"
IMAGE_TAG="${LOCATION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/${SERVICE_NAME}:latest"

echo "=============================================================================="
echo " Starting ADS Secure Cloud Run Deployment"
echo " Project ID:             ${PROJECT_ID}"
echo " Region / Location:      ${LOCATION}"
echo " Service Name:           ${SERVICE_NAME}"
echo " Repository:             ${REPO_NAME}"
echo " Service Account:        ${SA_EMAIL}"
echo " Model Armor Template:   ${TEMPLATE_PATH}"
echo " Image Tag:              ${IMAGE_TAG}"
echo "=============================================================================="

# ------------------------------------------------------------------------------
# 1. Enable Required GCP APIs
# ------------------------------------------------------------------------------
echo "==> [1/5] Enabling required Google Cloud APIs..."
gcloud services enable \
    run.googleapis.com \
    artifactregistry.googleapis.com \
    bigquery.googleapis.com \
    dlp.googleapis.com \
    modelarmor.googleapis.com \
    aiplatform.googleapis.com \
    cloudbuild.googleapis.com \
    logging.googleapis.com \
    --project="${PROJECT_ID}"

# ------------------------------------------------------------------------------
# 2. Create Artifact Registry Repository if non-existent
# ------------------------------------------------------------------------------
echo "==> [2/5] Configuring Artifact Registry repository '${REPO_NAME}'..."
if ! gcloud artifacts repositories describe "${REPO_NAME}" --location="${LOCATION}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    echo "Creating Docker repository '${REPO_NAME}' in '${LOCATION}'..."
    gcloud artifacts repositories create "${REPO_NAME}" \
        --repository-format=docker \
        --location="${LOCATION}" \
        --description="Alaska Department of Snow Docker Repository" \
        --project="${PROJECT_ID}"
else
    echo "Artifact Registry repository '${REPO_NAME}' already exists."
fi

# ------------------------------------------------------------------------------
# 3. Create Dedicated Service Account & Assign Minimal IAM Roles
# ------------------------------------------------------------------------------
echo "==> [3/5] Setting up dedicated Service Account '${SA_NAME}'..."
if ! gcloud iam service-accounts describe "${SA_EMAIL}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    echo "Creating service account '${SA_NAME}'..."
    gcloud iam service-accounts create "${SA_NAME}" \
        --display-name="ADS Cloud Run Runner Service Account" \
        --description="Dedicated identity for ADS document synthesis Cloud Run service" \
        --project="${PROJECT_ID}"
else
    echo "Service account '${SA_NAME}' already exists."
fi

echo "Assigning least-privilege IAM roles..."
REQUIRED_ROLES=(
    "roles/bigquery.dataEditor"
    "roles/bigquery.jobUser"
    "roles/modelarmor.user"
    "roles/aiplatform.user"
    "roles/dlp.user"
    "roles/logging.logWriter"
)

for role in "${REQUIRED_ROLES[@]}"; do
    echo " - Granting ${role} to ${SA_EMAIL}..."
    gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
        --member="serviceAccount:${SA_EMAIL}" \
        --role="${role}" \
        --condition=None \
        --quiet >/dev/null
done

# ------------------------------------------------------------------------------
# 4. Build and Push Container Image via Cloud Build
# ------------------------------------------------------------------------------
echo "==> [4/5] Building and pushing container image via Google Cloud Build..."
gcloud builds submit \
    --tag="${IMAGE_TAG}" \
    --project="${PROJECT_ID}" \
    .

# ------------------------------------------------------------------------------
# 5. Deploy to Google Cloud Run
# ------------------------------------------------------------------------------
echo "==> [5/5] Deploying '${SERVICE_NAME}' to Google Cloud Run..."
gcloud run deploy "${SERVICE_NAME}" \
    --image="${IMAGE_TAG}" \
    --region="${LOCATION}" \
    --platform=managed \
    --service-account="${SA_EMAIL}" \
    --memory=2Gi \
    --cpu=2 \
    --port=8080 \
    --set-env-vars="PROJECT_ID=${PROJECT_ID},LOCATION=${LOCATION},MODEL_ARMOR_TEMPLATE=${TEMPLATE_PATH},GOOGLE_GENAI_USE_VERTEXAI=True" \
    --allow-unauthenticated \
    --project="${PROJECT_ID}"

# Retrieve and print the public Service URL
SERVICE_URL=$(gcloud run services describe "${SERVICE_NAME}" \
    --platform=managed \
    --region="${LOCATION}" \
    --project="${PROJECT_ID}" \
    --format='value(status.url)')

echo ""
echo "=============================================================================="
echo " 🎉 Deployment Complete!"
echo " Service Name:  ${SERVICE_NAME}"
echo " Region:        ${LOCATION}"
echo " Service URL:   ${SERVICE_URL}"
echo " BigQuery Logs: https://console.cloud.google.com/bigquery?project=${PROJECT_ID}"
echo "=============================================================================="
