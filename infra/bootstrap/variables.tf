variable "project_id" {
  description = "GCP project ID."
  type        = string
}

variable "region" {
  description = "Region for Cloud Run, Artifact Registry and Storage."
  type        = string
  default     = "europe-north1"
}

variable "github_repo" {
  description = "GitHub repository allowed to deploy, as owner/name."
  type        = string
  default     = "sondreNodenes/fpl-advisor"
}

variable "state_bucket" {
  description = "Existing GCS bucket holding Terraform state (created in the README bootstrap step)."
  type        = string
}
