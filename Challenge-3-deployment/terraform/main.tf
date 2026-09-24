/**
 * Main Terraform configuration for Customer Service Transcript Parser.
 * Provisions VPC networking, Private Google Access, Serverless VPC Access connector,
 * Dedicated IAM Service Accounts, BigQuery dataset/table, Secret Manager secrets,
 * Artifact Registry repository, Cloud Run v2 service, and Cloud Build triggers.
 */

terraform {
  required_version = ">= 1.5.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.5"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# ==============================================================================
# 1. API Services Enablement
# ==============================================================================
locals {
  required_services = [
    "run.googleapis.com",
    "bigquery.googleapis.com",
    "cloudbuild.googleapis.com",
    "artifactregistry.googleapis.com",
    "aiplatform.googleapis.com",
    "compute.googleapis.com",
    "vpcaccess.googleapis.com",
    "secretmanager.googleapis.com",
    "iam.googleapis.com",
  ]
}

resource "google_project_service" "enabled_services" {
  for_each                   = toset(local.required_services)
  project                    = var.project_id
  service                    = each.key
  disable_on_destroy         = false
  disable_dependent_services = false
}

# ==============================================================================
# 2. VPC Network & Private Subnets with Private Google Access
# ==============================================================================
resource "google_compute_network" "vpc_network" {
  name                    = var.vpc_name
  auto_create_subnetworks = false
  description             = "Dedicated VPC network providing data isolation for Customer Service Parser"
  depends_on              = [google_project_service.enabled_services["compute.googleapis.com"]]
}

resource "google_compute_subnetwork" "private_subnet" {
  name                     = var.subnet_name
  ip_cidr_range            = var.subnet_cidr
  region                   = var.region
  network                  = google_compute_network.vpc_network.id
  private_ip_google_access = true # Enforces private routing to BigQuery and Vertex AI without public IPs
  description              = "Private subnet with Private Google Access enabled"
}

# Cloud Router & NAT Gateway for outbound egress from private instances/connectors
resource "google_compute_router" "nat_router" {
  name    = "${var.vpc_name}-router"
  region  = var.region
  network = google_compute_network.vpc_network.name
}

resource "google_compute_router_nat" "nat_gateway" {
  name                               = "${var.vpc_name}-nat"
  router                             = google_compute_router.nat_router.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"
}

# Serverless VPC Access Connector connecting Cloud Run into the VPC
resource "google_vpc_access_connector" "connector" {
  name          = var.vpc_connector_name
  region        = var.region
  ip_cidr_range = var.vpc_connector_cidr
  network       = google_compute_network.vpc_network.name
  min_instances = 2
  max_instances = 3
  machine_type  = "e2-micro"

  depends_on = [
    google_project_service.enabled_services["vpcaccess.googleapis.com"],
    google_compute_subnetwork.private_subnet,
  ]
}

# ==============================================================================
# 3. Dedicated IAM Service Accounts & Least-Privilege Role Bindings
# ==============================================================================

# Cloud Run Compute Runtime Service Account
resource "google_service_account" "cloud_run_sa" {
  account_id   = var.cloud_run_sa_name
  display_name = "Cloud Run Transcript Parser Compute Service Account"
  description  = "Dedicated service account used by Cloud Run application container"
  depends_on   = [google_project_service.enabled_services["iam.googleapis.com"]]
}

# Cloud Build CI/CD Pipeline Service Account
resource "google_service_account" "cloud_build_sa" {
  account_id   = var.cloud_build_sa_name
  display_name = "Cloud Build Pipeline Deployment Service Account"
  description  = "Dedicated service account used by Cloud Build CI/CD automation pipeline"
  depends_on   = [google_project_service.enabled_services["iam.googleapis.com"]]
}

# Roles for Cloud Run Compute Service Account (Strict Least Privilege)
locals {
  cloud_run_roles = [
    "roles/bigquery.dataEditor",          # Permission to insert rows & query BigQuery
    "roles/bigquery.jobUser",             # Permission to run BigQuery jobs
    "roles/aiplatform.user",              # Permission to invoke Gemini models on Vertex AI
    "roles/secretmanager.secretAccessor", # Permission to read secrets from Secret Manager
    "roles/vpcaccess.user",               # Permission to use VPC access connector
  ]
}

resource "google_project_iam_member" "cloud_run_sa_roles" {
  for_each = toset(local.cloud_run_roles)
  project  = var.project_id
  role     = each.key
  member   = "serviceAccount:${google_service_account.cloud_run_sa.email}"
}

# Roles for Cloud Build CI/CD Service Account
locals {
  cloud_build_roles = [
    "roles/run.admin",                       # Manage Cloud Run services
    "roles/iam.serviceAccountUser",          # Act as Cloud Run compute service account
    "roles/artifactregistry.writer",         # Push container images to Artifact Registry
    "roles/storage.admin",                   # Access Terraform state bucket and build cache
    "roles/secretmanager.secretAccessor",    # Access secrets for builds
    "roles/bigquery.admin",                  # Provision and manage BigQuery schemas via Terraform
    "roles/compute.networkAdmin",            # Manage VPC networking via Terraform
    "roles/vpcaccess.admin",                 # Manage VPC Access connectors via Terraform
    "roles/resourcemanager.projectIamAdmin", # Manage IAM role bindings via Terraform
  ]
}

resource "google_project_iam_member" "cloud_build_sa_roles" {
  for_each = toset(local.cloud_build_roles)
  project  = var.project_id
  role     = each.key
  member   = "serviceAccount:${google_service_account.cloud_build_sa.email}"
}

# ==============================================================================
# 4. Artifact Registry Docker Repository
# ==============================================================================
resource "google_artifact_registry_repository" "app_repo" {
  location      = var.region
  repository_id = var.artifact_repo_name
  description   = "Docker repository for Customer Service Transcript Parser images"
  format        = "DOCKER"

  depends_on = [google_project_service.enabled_services["artifactregistry.googleapis.com"]]
}

# ==============================================================================
# 5. Secret Manager for Sensitive Application Configuration
# ==============================================================================
resource "random_password" "flask_secret" {
  length  = 32
  special = false
}

resource "google_secret_manager_secret" "flask_secret_key" {
  secret_id = "customer-service-flask-secret-key"

  replication {
    auto {}
  }

  depends_on = [google_project_service.enabled_services["secretmanager.googleapis.com"]]
}

resource "google_secret_manager_secret_version" "flask_secret_version" {
  secret      = google_secret_manager_secret.flask_secret_key.id
  secret_data = var.flask_secret_key != "" ? var.flask_secret_key : random_password.flask_secret.result
}

# ==============================================================================
# 6. BigQuery Dataset & Tables matching Transcript Payload Schema
# ==============================================================================
resource "google_bigquery_dataset" "transcript_dataset" {
  dataset_id                 = var.dataset_name
  friendly_name              = "Customer Service Transcripts Dataset"
  description                = "Dataset storing parsed transcripts, raw inputs, and execution history"
  location                   = var.bigquery_location
  delete_contents_on_destroy = false

  depends_on = [google_project_service.enabled_services["bigquery.googleapis.com"]]
}

# Primary table: transcripts
resource "google_bigquery_table" "transcripts_table" {
  dataset_id          = google_bigquery_dataset.transcript_dataset.dataset_id
  table_id            = var.table_name
  description         = "Structured table containing semantic extractions, full text, and audit logs"
  deletion_protection = false

  schema = jsonencode([
    {
      name        = "call_id"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Unique identifier for the call or support session"
    },
    {
      name        = "date"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Date and time of the interaction"
    },
    {
      name        = "customer"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Customer name"
    },
    {
      name        = "agent"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Agent handling the call"
    },
    {
      name        = "product"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Product or service discussed"
    },
    {
      name        = "issue"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Description or summary of reported issue"
    },
    {
      name        = "resolution"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Troubleshooting steps and resolution reached"
    },
    {
      name        = "escalate"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Escalation indicator (e.g. no, yes)"
    },
    {
      name        = "parsed_transcript"
      type        = "RECORD"
      mode        = "NULLABLE"
      description = "Structured parsed metadata matching TranscriptRecord schema"
      fields = [
        { name = "call_id", type = "STRING", mode = "NULLABLE" },
        { name = "date", type = "STRING", mode = "NULLABLE" },
        { name = "customer", type = "STRING", mode = "NULLABLE" },
        { name = "agent", type = "STRING", mode = "NULLABLE" },
        { name = "product", type = "STRING", mode = "NULLABLE" },
        { name = "issue", type = "STRING", mode = "NULLABLE" },
        { name = "resolution", type = "STRING", mode = "NULLABLE" },
        { name = "escalate", type = "STRING", mode = "NULLABLE" }
      ]
    },
    {
      name        = "original_transcript"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Raw original transcript text"
    },
    {
      name        = "log_history"
      type        = "RECORD"
      mode        = "REPEATED"
      description = "Audit trail capturing each pipeline stage execution"
      fields = [
        { name = "timestamp", type = "STRING", mode = "NULLABLE" },
        { name = "level", type = "STRING", mode = "NULLABLE" },
        { name = "stage", type = "STRING", mode = "NULLABLE" },
        { name = "message", type = "STRING", mode = "NULLABLE" },
        { name = "details", type = "STRING", mode = "NULLABLE" }
      ]
    },
    {
      name        = "status"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Processing status: SUCCESS, PARTIAL, or FAILED"
    },
    {
      name        = "source_file"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Source file name or origin of transcript"
    },
    {
      name        = "processed_at"
      type        = "TIMESTAMP"
      mode        = "NULLABLE"
      description = "Timestamp when parsing completed"
    },
    {
      name        = "model_name"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Gemini model used for extraction"
    },
    {
      name        = "execution_time_seconds"
      type        = "FLOAT"
      mode        = "NULLABLE"
      description = "Execution duration in seconds"
    }
  ])
}

# Compatibility mirror table: customer_service_transcripts
resource "google_bigquery_table" "customer_service_transcripts_table" {
  dataset_id          = google_bigquery_dataset.transcript_dataset.dataset_id
  table_id            = "customer_service_transcripts"
  description         = "Alternate table name to maintain backward compatibility with legacy scripts"
  deletion_protection = false

  schema = google_bigquery_table.transcripts_table.schema
}

# ==============================================================================
# 7. Cloud Run v2 Service (Containerized Web UI / API)
# ==============================================================================
locals {
  resolved_container_image = var.container_image != "" ? var.container_image : "${var.region}-docker.pkg.dev/${var.project_id}/${var.artifact_repo_name}/transcript-parser:latest"
}

resource "google_cloud_run_v2_service" "app_service" {
  name     = var.service_name
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.cloud_run_sa.email

    vpc_access {
      connector = google_vpc_access_connector.connector.id
      egress    = "ALL_TRAFFIC" # Routes all outbound traffic through the VPC and Private Google Access
    }

    scaling {
      min_instance_count = 0
      max_instance_count = 10
    }

    containers {
      image = local.resolved_container_image

      resources {
        limits = {
          cpu    = "1"
          memory = "1024Mi"
        }
        cpu_idle = true
      }

      ports {
        container_port = 8080
      }

      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }
      env {
        name  = "DEVSHELL_PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "BIGQUERY_DATASET"
        value = var.dataset_name
      }
      env {
        name  = "BIGQUERY_TABLE"
        value = var.table_name
      }
      env {
        name  = "DEFAULT_MODEL"
        value = var.gemini_model
      }
      env {
        name = "FLASK_SECRET_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.flask_secret_key.secret_id
            version = "latest"
          }
        }
      }

      startup_probe {
        http_get {
          path = "/health"
          port = 8080
        }
        initial_delay_seconds = 5
        period_seconds        = 10
        failure_threshold     = 3
      }

      liveness_probe {
        http_get {
          path = "/health"
          port = 8080
        }
        period_seconds    = 15
        failure_threshold = 3
      }
    }
  }

  traffic {
    type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    percent = 100
  }

  depends_on = [
    google_project_service.enabled_services["run.googleapis.com"],
    google_vpc_access_connector.connector,
    google_secret_manager_secret_version.flask_secret_version,
    google_project_iam_member.cloud_run_sa_roles,
  ]
}

# Allow unauthenticated invocations for public web interface (if enabled)
resource "google_cloud_run_v2_service_iam_member" "public_access" {
  count    = var.allow_unauthenticated ? 1 : 0
  project  = var.project_id
  location = google_cloud_run_v2_service.app_service.location
  name     = google_cloud_run_v2_service.app_service.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# ==============================================================================
# 8. Cloud Build CI/CD Trigger
# ==============================================================================
resource "google_cloudbuild_trigger" "ci_cd_trigger" {
  name        = "deploy-customer-service-parser"
  description = "Continuous Deployment pipeline trigger for Customer Service Parser"
  location    = var.region

  filename        = "cloudbuild.yaml"
  service_account = google_service_account.cloud_build_sa.id

  # Source repository configuration (supports manual trigger / webhook / repo)
  source_to_build {
    uri       = "https://github.com/GoogleCloudPlatform/customer-service-parser"
    ref       = "refs/heads/main"
    repo_type = "GITHUB"
  }

  substitutions = {
    _LOCATION     = var.region
    _REPO_NAME    = var.artifact_repo_name
    _IMAGE_NAME   = "transcript-parser"
    _SERVICE_NAME = var.service_name
    _STATE_BUCKET = "${var.project_id}-tfstate"
  }

  depends_on = [
    google_project_service.enabled_services["cloudbuild.googleapis.com"],
    google_project_iam_member.cloud_build_sa_roles,
  ]
}
