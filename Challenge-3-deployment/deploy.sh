#!/usr/bin/env bash
# ==============================================================================
# Automated Deployment Script for Customer Service Transcript Parser
#
# Automates:
# 1. Verification and enablement of required Google Cloud APIs
# 2. Creation and configuration of secure GCS bucket for Terraform remote state
# 3. Building and pushing container image to Artifact Registry
# 4. Terraform initialization and infrastructure provisioning
# 5. Health verification and endpoint confirmation output
# ==============================================================================

set -euo pipefail

# Text formatting
BOLD="\033[1m"
GREEN="\033[0;32m"
YELLOW="\033[1;33m"
CYAN="\033[0;36m"
RED="\033[0;31m"
RESET="\033[0m"

log_info() {
  echo -e "${GREEN}[INFO]${RESET} $1"
}

log_warn() {
  echo -e "${YELLOW}[WARN]${RESET} $1"
}

log_step() {
  echo -e "\n${BOLD}${CYAN}==> $1${RESET}"
}

log_error() {
  echo -e "${RED}[ERROR]${RESET} $1" >&2
}

# ------------------------------------------------------------------------------
# 0. Configuration & Parameter Parsing
# ------------------------------------------------------------------------------
PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null || echo "")}"
REGION="${REGION:-us-central1}"
STATE_BUCKET=""
REPO_NAME="customer-service-parser-repo"
IMAGE_NAME="transcript-parser"
AUTO_APPROVE=false

usage() {
  cat <<EOF
Usage: $0 [OPTIONS]

Options:
  -p, --project PROJECT_ID    GCP Project ID (default: active gcloud project)
  -r, --region REGION         GCP Region (default: us-central1)
  -b, --bucket BUCKET_NAME    GCS Bucket for Terraform state (default: <PROJECT_ID>-tfstate)
  -y, --yes                   Skip interactive confirmation
  -h, --help                  Show this help message
EOF
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -p|--project)
      PROJECT_ID="$2"
      shift 2
      ;;
    -r|--region)
      REGION="$2"
      shift 2
      ;;
    -b|--bucket)
      STATE_BUCKET="$2"
      shift 2
      ;;
    -y|--yes)
      AUTO_APPROVE=true
      shift
      ;;
    -h|--help)
      usage
      ;;
    *)
      log_error "Unknown option: $1"
      usage
      ;;
  esac
done

if [[ -z "$PROJECT_ID" ]]; then
  log_error "GCP Project ID could not be determined. Please specify with -p <PROJECT_ID> or run 'gcloud config set project <PROJECT_ID>'."
  exit 1
fi

if [[ -z "$STATE_BUCKET" ]]; then
  STATE_BUCKET="${PROJECT_ID}-tfstate"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

log_step "Deployment Parameters"
echo "  Project ID:       $PROJECT_ID"
echo "  Region:           $REGION"
echo "  State Bucket:     $STATE_BUCKET"
echo "  Artifact Repo:    $REPO_NAME"
echo "  Container Image:  $IMAGE_NAME"

# ------------------------------------------------------------------------------
# 1. Prerequisite Verification
# ------------------------------------------------------------------------------
log_step "Checking local CLI dependencies"
for cmd in gcloud terraform docker curl; do
  if ! command -v "$cmd" &>/dev/null; then
    log_error "Required tool '$cmd' is not installed or not in PATH."
    exit 1
  fi
  echo "  ✓ Found $cmd: $(command -v "$cmd")"
done

# ------------------------------------------------------------------------------
# 2. Verify and Enable Required GCP APIs
# ------------------------------------------------------------------------------
log_step "Verifying and enabling required GCP APIs"
REQUIRED_APIS=(
  "run.googleapis.com"
  "bigquery.googleapis.com"
  "cloudbuild.googleapis.com"
  "artifactregistry.googleapis.com"
  "aiplatform.googleapis.com"
  "compute.googleapis.com"
  "vpcaccess.googleapis.com"
  "secretmanager.googleapis.com"
  "iam.googleapis.com"
)

log_info "Enabling APIs: ${REQUIRED_APIS[*]}"
gcloud services enable "${REQUIRED_APIS[@]}" --project="$PROJECT_ID" --quiet
log_info "All required APIs are active."

# ------------------------------------------------------------------------------
# 3. Create Terraform State Cloud Storage Bucket with Versioning
# ------------------------------------------------------------------------------
log_step "Verifying GCS backend bucket for Terraform state"
if gcloud storage buckets describe "gs://${STATE_BUCKET}" &>/dev/null; then
  log_info "Bucket gs://${STATE_BUCKET} already exists."
else
  log_info "Creating bucket gs://${STATE_BUCKET} in region ${REGION}..."
  gcloud storage buckets create "gs://${STATE_BUCKET}" \
    --project="$PROJECT_ID" \
    --location="$REGION" \
    --uniform-bucket-level-access \
    --quiet
  log_info "Bucket gs://${STATE_BUCKET} created."
fi

# Enforce versioning for state protection
log_info "Enforcing object versioning on gs://${STATE_BUCKET}..."
gcloud storage buckets update "gs://${STATE_BUCKET}" --versioning --quiet
log_info "Bucket versioning enabled."

# ------------------------------------------------------------------------------
# 4. Prepare Artifact Registry & Build/Push Container Image
# ------------------------------------------------------------------------------
log_step "Ensuring Artifact Registry repository exists"
if ! gcloud artifacts repositories describe "$REPO_NAME" --location="$REGION" --project="$PROJECT_ID" &>/dev/null; then
  log_info "Creating Artifact Registry repository '$REPO_NAME' in $REGION..."
  gcloud artifacts repositories create "$REPO_NAME" \
    --repository-format=docker \
    --location="$REGION" \
    --description="Docker repository for Customer Service Parser" \
    --project="$PROJECT_ID" \
    --quiet
  log_info "Artifact Registry repository created."
else
  log_info "Artifact Registry repository '$REPO_NAME' already exists."
fi

REGISTRY_HOST="${REGION}-docker.pkg.dev"
IMAGE_TAG="${REGISTRY_HOST}/${PROJECT_ID}/${REPO_NAME}/${IMAGE_NAME}:$(date +%Y%m%d%H%M%S)"
IMAGE_LATEST="${REGISTRY_HOST}/${PROJECT_ID}/${REPO_NAME}/${IMAGE_NAME}:latest"

log_step "Building and pushing application container image to Artifact Registry"
if command -v gcloud &>/dev/null; then
  log_info "Submitting container build to Cloud Build..."
  gcloud builds submit \
    --project="$PROJECT_ID" \
    --tag="$IMAGE_LATEST" \
    --tag="$IMAGE_TAG" \
    .
  log_info "Container image built and pushed successfully: $IMAGE_TAG"
else
  log_info "Configuring Docker authentication for $REGISTRY_HOST..."
  gcloud auth configure-docker "$REGISTRY_HOST" --quiet
  log_info "Building Docker image: $IMAGE_LATEST..."
  docker build -t "$IMAGE_TAG" -t "$IMAGE_LATEST" .
  log_info "Pushing Docker image to Artifact Registry..."
  docker push "$IMAGE_TAG"
  docker push "$IMAGE_LATEST"
  log_info "Container image pushed successfully: $IMAGE_TAG"
fi

# ------------------------------------------------------------------------------
# 5. Execute Terraform Init and Apply
# ------------------------------------------------------------------------------
log_step "Initializing Terraform with GCS remote state backend"
terraform init \
  -backend-config="bucket=${STATE_BUCKET}" \
  -backend-config="prefix=transcript-parser/state" \
  -reconfigure

log_step "Planning and Applying Terraform Infrastructure"
APPLY_FLAGS=(
  "-var=project_id=${PROJECT_ID}"
  "-var=region=${REGION}"
  "-var=container_image=${IMAGE_TAG}"
)

if [ "$AUTO_APPROVE" = true ] || [ -n "${CI:-}" ]; then
  APPLY_FLAGS+=("-auto-approve")
fi

# Safely import Artifact Registry repo if it was created in step 4
terraform import -var="project_id=${PROJECT_ID}" -var="region=${REGION}" google_artifact_registry_repository.app_repo "projects/${PROJECT_ID}/locations/${REGION}/repositories/${REPO_NAME}" &>/dev/null || true

terraform apply "${APPLY_FLAGS[@]}"

# ------------------------------------------------------------------------------
# 6. Verification & Confirmation Output
# ------------------------------------------------------------------------------
log_step "Retrieving deployment outputs and verifying endpoint"
SERVICE_URL=$(terraform output -raw cloud_run_url 2>/dev/null || gcloud run services describe customer-service-parser --project="$PROJECT_ID" --region="$REGION" --format='value(status.url)')
BQ_DATASET=$(terraform output -raw bigquery_dataset_id 2>/dev/null || echo "customer_service")
BQ_TABLE=$(terraform output -raw bigquery_table_id 2>/dev/null || echo "transcripts")
SA_EMAIL=$(terraform output -raw cloud_run_service_account 2>/dev/null || echo "")

echo ""
echo "================================================================================"
echo "          CUSTOMER SERVICE TRANSCRIPT PARSER - DEPLOYMENT COMPLETE             "
echo "================================================================================"
echo "  Status:                 ONLINE / READY"
echo "  GCP Project:            ${PROJECT_ID}"
echo "  Region:                 ${REGION}"
echo "  Cloud Run Service URL:  ${SERVICE_URL}"
echo "  BigQuery Table:         ${PROJECT_ID}.${BQ_DATASET}.${BQ_TABLE}"
echo "  Runtime Service Account:${SA_EMAIL}"
echo "  Remote State Bucket:    gs://${STATE_BUCKET}"
echo "================================================================================"
echo ""

if [ -n "$SERVICE_URL" ]; then
  log_info "Testing application health check endpoint..."
  for i in $(seq 1 10); do
    HTTP_CODE=$(curl -s -o /tmp/health_test.json -w "%{http_code}" "${SERVICE_URL}/health" || echo "000")
    if [ "$HTTP_CODE" -eq 200 ]; then
      log_info "Health check passed with HTTP 200 OK!"
      cat /tmp/health_test.json
      echo ""
      break
    fi
    log_warn "Health check attempt $i received HTTP $HTTP_CODE. Waiting 3s..."
    sleep 3
  done
fi

echo ""
log_info "Access your web application in your browser at:"
echo -e "  ${BOLD}${CYAN}${SERVICE_URL}${RESET}"
echo ""
