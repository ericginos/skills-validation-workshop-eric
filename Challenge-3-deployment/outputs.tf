/**
 * Output variables for Customer Service Transcript Parser infrastructure.
 */

output "cloud_run_url" {
  description = "The public endpoint URL of the deployed Customer Service Parser Cloud Run service."
  value       = google_cloud_run_v2_service.app_service.uri
}

output "bigquery_dataset_id" {
  description = "The ID of the BigQuery dataset storing customer service transcripts."
  value       = google_bigquery_dataset.transcript_dataset.dataset_id
}

output "bigquery_table_id" {
  description = "The ID of the primary BigQuery table storing structured transcript payloads."
  value       = google_bigquery_table.transcripts_table.table_id
}

output "bigquery_table_fqdn" {
  description = "The fully qualified BigQuery table identifier (project.dataset.table)."
  value       = "${var.project_id}.${google_bigquery_dataset.transcript_dataset.dataset_id}.${google_bigquery_table.transcripts_table.table_id}"
}

output "cloud_run_service_account" {
  description = "The email address of the dedicated Cloud Run compute runtime Service Account."
  value       = google_service_account.cloud_run_sa.email
}

output "cloud_build_service_account" {
  description = "The email address of the dedicated Cloud Build CI/CD Service Account."
  value       = google_service_account.cloud_build_sa.email
}

output "vpc_network_name" {
  description = "The name of the dedicated VPC network."
  value       = google_compute_network.vpc_network.name
}

output "private_subnet_name" {
  description = "The name of the private subnet with Private Google Access enabled."
  value       = google_compute_subnetwork.private_subnet.name
}

output "vpc_connector_name" {
  description = "The name of the Serverless VPC Access connector."
  value       = google_vpc_access_connector.connector.name
}

output "artifact_registry_repository" {
  description = "The URI of the Artifact Registry Docker repository."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.app_repo.repository_id}"
}

output "secret_manager_secret_name" {
  description = "The name of the Secret Manager secret for application secrets."
  value       = google_secret_manager_secret.flask_secret_key.name
}
