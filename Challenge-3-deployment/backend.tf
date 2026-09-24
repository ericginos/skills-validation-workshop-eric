/**
 * Terraform remote state backend configuration using Google Cloud Storage.
 * The GCS bucket and prefix can be specified dynamically during 'terraform init':
 *   terraform init -backend-config="bucket=<PROJECT_ID>-tfstate" -backend-config="prefix=transcript-parser/state"
 */

terraform {
  backend "gcs" {
    # Dynamically provided during terraform init or via backend config
  }
}
