output "workload_identity_provider" {
  description = "Set as GitHub repository variable GCP_WIF_PROVIDER."
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "deployer_service_account" {
  description = "Set as GitHub repository variable GCP_DEPLOY_SA."
  value       = google_service_account.deployer.email
}

output "image_repository" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.app.repository_id}"
}

output "data_bucket" {
  value = google_storage_bucket.data.name
}
