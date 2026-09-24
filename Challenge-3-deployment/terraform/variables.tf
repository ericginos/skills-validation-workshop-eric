/**
 * Variables definition for Customer Service Transcript Parser infrastructure.
 */

variable "project_id" {
  description = "The Google Cloud Project ID where resources will be provisioned."
  type        = string
}

variable "region" {
  description = "The primary Google Cloud region for compute, networking, and serverless resources."
  type        = string
  default     = "us-central1"
}

variable "bigquery_location" {
  description = "The geographic location for BigQuery dataset storage (e.g. US, EU, us-central1)."
  type        = string
  default     = "US"
}

variable "vpc_name" {
  description = "Name of the dedicated VPC network for data isolation."
  type        = string
  default     = "vpc-customer-service"
}

variable "subnet_name" {
  description = "Name of the private subnetwork hosting serverless egress and workloads."
  type        = string
  default     = "sb-customer-service-private"
}

variable "subnet_cidr" {
  description = "CIDR block allocated to the private subnetwork."
  type        = string
  default     = "10.0.1.0/24"
}

variable "vpc_connector_name" {
  description = "Name of the Serverless VPC Access connector."
  type        = string
  default     = "vpc-conn-transcript"
}

variable "vpc_connector_cidr" {
  description = "Dedicated /28 CIDR range for the Serverless VPC Access connector."
  type        = string
  default     = "10.8.0.0/28"
}

variable "dataset_name" {
  description = "BigQuery dataset ID storing customer service transcripts and audit logs."
  type        = string
  default     = "customer_service"
}

variable "table_name" {
  description = "BigQuery table ID storing structured parsed transcripts."
  type        = string
  default     = "transcripts"
}

variable "service_name" {
  description = "Name of the Cloud Run service."
  type        = string
  default     = "customer-service-parser"
}

variable "artifact_repo_name" {
  description = "Name of the Artifact Registry Docker repository."
  type        = string
  default     = "customer-service-parser-repo"
}

variable "container_image" {
  description = "Full Artifact Registry URI of the container image to deploy. Defaults to bootstrap placeholder if not provided."
  type        = string
  default     = ""
}

variable "cloud_run_sa_name" {
  description = "Service Account name for Cloud Run runtime execution."
  type        = string
  default     = "sa-customer-service-app"
}

variable "cloud_build_sa_name" {
  description = "Service Account name for Cloud Build CI/CD execution."
  type        = string
  default     = "sa-cloudbuild-deployer"
}

variable "gemini_model" {
  description = "Gemini model to be used by the semantic parsing pipeline."
  type        = string
  default     = "gemini-2.5-flash"
}

variable "flask_secret_key" {
  description = "Secret key used by Flask for session cookie signing. A random secret is generated if empty."
  type        = string
  default     = ""
  sensitive   = true
}

variable "allow_unauthenticated" {
  description = "Whether to allow unauthenticated invocations for the public web UI."
  type        = bool
  default     = true
}
