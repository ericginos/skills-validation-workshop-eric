#!/usr/bin/env bash
# ==============================================================================
# Automated Deployment Script for Gemini AI Chatbot on Google Cloud Run
# ==============================================================================
# Features:
# - API enablement (Cloud Run, Artifact Registry, Vertex AI, Cloud Build)
# - Artifact Registry repository setup
# - Cloud Build container image creation & push
# - Cloud Run deployment with dynamic PORT, scaling, and environment variables
# - Deployment verification and URL display
# ==============================================================================

set -euo pipefail

# Text formatting & color definitions
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
}

# ==============================================================================
# Default Configuration Values
# ==============================================================================
DEFAULT_PROJECT=$(gcloud config get-value project 2>/dev/null || echo "")
PROJECT_ID="${PROJECT_ID:-${GOOGLE_CLOUD_PROJECT:-${DEFAULT_PROJECT}}}"
REGION="${REGION:-${GOOGLE_CLOUD_LOCATION:-us-central1}}"
SERVICE_NAME="${SERVICE_NAME:-gemini-ai-chatbot}"
REPO_NAME="${REPO_NAME:-chatbot-repo}"
IMAGE_TAG="latest"
ALLOW_UNAUTHENTICATED="true"
GEMINI_API_KEY="${GEMINI_API_KEY:-}"

# ==============================================================================
# Usage & Help Display
# ==============================================================================
usage() {
    cat <<EOF
${BOLD}Usage:${NC} $0 [OPTIONS]

Automates building and deploying the Gemini AI Chatbot container to Google Cloud Run.

${BOLD}Options:${NC}
  -p, --project PROJECT_ID     Google Cloud Project ID (default: ${PROJECT_ID:-"current active project"})
  -r, --region REGION          Google Cloud Region (default: ${REGION})
  -s, --service SERVICE_NAME   Cloud Run Service Name (default: ${SERVICE_NAME})
  -a, --repo REPO_NAME         Artifact Registry Repository (default: ${REPO_NAME})
  -k, --api-key API_KEY        Optional Gemini API Key (defaults to Vertex AI ADC if omitted)
      --authenticated          Deploy requiring IAM authentication (disables public access)
  -h, --help                   Display this help message and exit

${BOLD}Examples:${NC}
  $0
  $0 --region us-central1 --service my-ai-chatbot
  $0 -p my-gcp-project -r us-east4 -k "AIzaSy..."

EOF
    exit 1
}

# ==============================================================================
# Parse Command-Line Arguments
# ==============================================================================
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
        -s|--service)
            SERVICE_NAME="$2"
            shift 2
            ;;
        -a|--repo)
            REPO_NAME="$2"
            shift 2
            ;;
        -k|--api-key)
            GEMINI_API_KEY="$2"
            shift 2
            ;;
        --authenticated)
            ALLOW_UNAUTHENTICATED="false"
            shift
            ;;
        -h|--help)
            usage
            ;;
        *)
            log_error "Unknown parameter: $1"
            usage
            ;;
    esac
done

# ==============================================================================
# Pre-Flight Validation
# ==============================================================================
echo -e "${CYAN}${BOLD}"
echo "=================================================================="
echo "    🚀 Deploying Gemini AI Chatbot to Google Cloud Run"
echo "=================================================================="
echo -e "${NC}"

if ! command -v gcloud &>/dev/null; then
    log_error "Google Cloud SDK (gcloud) is not installed or not in PATH."
    exit 1
fi

if [[ -z "${PROJECT_ID}" ]]; then
    log_error "Project ID is required. Set via --project or 'gcloud config set project <ID>'."
    exit 1
fi

log_info "Deployment Parameters:"
echo "  • Project ID:        ${PROJECT_ID}"
echo "  • Region:            ${REGION}"
echo "  • Service Name:      ${SERVICE_NAME}"
echo "  • Artifact Registry: ${REPO_NAME}"
echo "  • Unauthenticated:   ${ALLOW_UNAUTHENTICATED}"
if [[ -n "${GEMINI_API_KEY}" ]]; then
    echo "  • Auth Method:       Explicit Gemini API Key provided"
else
    echo "  • Auth Method:       Vertex AI Application Default Credentials (ADC)"
fi
echo ""

# Ensure gcloud uses the targeted project
gcloud config set project "${PROJECT_ID}" >/dev/null

# ==============================================================================
# Step 1: Enable Required GCP APIs
# ==============================================================================
log_info "Step 1/5: Enabling required Google Cloud APIs..."
REQUIRED_SERVICES=(
    "run.googleapis.com"
    "artifactregistry.googleapis.com"
    "aiplatform.googleapis.com"
    "cloudbuild.googleapis.com"
)

gcloud services enable "${REQUIRED_SERVICES[@]}" --project="${PROJECT_ID}"
log_success "All required APIs enabled successfully."

# ==============================================================================
# Step 2: Configure Artifact Registry Repository
# ==============================================================================
log_info "Step 2/5: Verifying Artifact Registry repository '${REPO_NAME}' in '${REGION}'..."
if ! gcloud artifacts repositories describe "${REPO_NAME}" \
    --location="${REGION}" \
    --project="${PROJECT_ID}" &>/dev/null; then
    log_info "Creating Artifact Registry Docker repository '${REPO_NAME}'..."
    gcloud artifacts repositories create "${REPO_NAME}" \
        --repository-format=docker \
        --location="${REGION}" \
        --description="Docker repository for Gemini AI Chatbot" \
        --project="${PROJECT_ID}"
    log_success "Created repository '${REPO_NAME}'."
else
    log_info "Artifact Registry repository '${REPO_NAME}' already exists."
fi

REGISTRY_HOST="${REGION}-docker.pkg.dev"
IMAGE_URI="${REGISTRY_HOST}/${PROJECT_ID}/${REPO_NAME}/${SERVICE_NAME}:${IMAGE_TAG}"

# ==============================================================================
# Step 3: Build & Push Container Image
# ==============================================================================
log_info "Step 3/5: Building container image via Google Cloud Build..."
log_info "Target image URI: ${IMAGE_URI}"

# Execute Cloud Build from current directory
gcloud builds submit \
    --project="${PROJECT_ID}" \
    --tag="${IMAGE_URI}" \
    .

log_success "Container image built and pushed successfully."

# ==============================================================================
# Step 4: Deploy Container to Google Cloud Run
# ==============================================================================
log_info "Step 4/5: Deploying '${SERVICE_NAME}' to Google Cloud Run..."

# Assemble environment variables
ENV_VARS="GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_LOCATION=${REGION}"
if [[ -n "${GEMINI_API_KEY}" ]]; then
    ENV_VARS="${ENV_VARS},GEMINI_API_KEY=${GEMINI_API_KEY}"
fi

# Determine unauthenticated access flag
AUTH_FLAG="--allow-unauthenticated"
if [[ "${ALLOW_UNAUTHENTICATED}" == "false" ]]; then
    AUTH_FLAG="--no-allow-unauthenticated"
fi

gcloud run deploy "${SERVICE_NAME}" \
    --image="${IMAGE_URI}" \
    --platform="managed" \
    --region="${REGION}" \
    --project="${PROJECT_ID}" \
    ${AUTH_FLAG} \
    --set-env-vars="${ENV_VARS}" \
    --port=8080 \
    --memory="1Gi" \
    --cpu="1" \
    --concurrency=80 \
    --min-instances=0 \
    --max-instances=10 \
    --timeout=300 \
    --ingress="all"

log_success "Cloud Run service deployment completed."

# ==============================================================================
# Step 5: Verification & Endpoint Retrieval
# ==============================================================================
log_info "Step 5/5: Retrieving service URL and verifying status..."

SERVICE_URL=$(gcloud run services describe "${SERVICE_NAME}" \
    --platform="managed" \
    --region="${REGION}" \
    --project="${PROJECT_ID}" \
    --format='value(status.url)')

echo ""
echo -e "${GREEN}${BOLD}=================================================================="
echo "    🎉 GEMINI AI CHATBOT DEPLOYED SUCCESSFULLY!"
echo "==================================================================${NC}"
echo -e "  🌐 ${BOLD}Live Application URL:${NC} ${CYAN}${SERVICE_URL}${NC}"
echo -e "  📍 ${BOLD}GCP Project:${NC}          ${PROJECT_ID}"
echo -e "  🗺️  ${BOLD}Region:${NC}               ${REGION}"
echo -e "  📦 ${BOLD}Container Image:${NC}      ${IMAGE_URI}"
echo -e "  🔍 ${BOLD}Search Grounding:${NC}     Google Search Tool Enabled"
echo -e "=================================================================="
echo ""
echo "Open the URL above in any web browser to interact with your AI Chatbot."
