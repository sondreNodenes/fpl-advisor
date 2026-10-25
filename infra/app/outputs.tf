output "url" {
  value = google_cloud_run_v2_service.app.uri
}

output "job" {
  value = google_cloud_run_v2_job.pipeline.name
}
