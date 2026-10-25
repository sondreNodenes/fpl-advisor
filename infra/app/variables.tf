variable "project_id" {
  description = "GCP project ID."
  type        = string
}

variable "region" {
  description = "Region for Cloud Run."
  type        = string
  default     = "europe-north1"
}

variable "scheduler_region" {
  description = "Cloud Scheduler is not offered in europe-north1; it triggers the job cross-region."
  type        = string
  default     = "europe-west1"
}

variable "image" {
  description = "Full container image reference, e.g. europe-north1-docker.pkg.dev/PROJECT/fpl-advisor/app:SHA."
  type        = string
}

variable "schedules" {
  description = "Cron schedules (UTC) for the ingest-and-retrain job."
  type        = map(string)
  default = {
    nightly  = "0 3 * * *"
    matchday = "0 19,22 * * 5,6,0,1" # Fri to Mon evenings, after matches
  }
}
