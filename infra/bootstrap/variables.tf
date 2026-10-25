variable "project_id" {
  description = "GCP project ID."
  type        = string
}

variable "region" {
  description = "Region for Cloud Run, Artifact Registry and Storage."
  type        = string
  default     = "europe-north1"
}

variable "github_repository_id" {
  description = "Numeric GitHub repository ID (gh api repos/OWNER/REPO --jq .id)."
  type        = string
  default     = "1432901156"
  validation {
    condition     = can(regex("^[0-9]+$", var.github_repository_id))
    error_message = "Must be the numeric repository ID, not the name."
  }
}

variable "github_owner_id" {
  description = "Numeric GitHub owner (user or org) ID (gh api repos/OWNER/REPO --jq .owner.id)."
  type        = string
  default     = "186288323"
  validation {
    condition     = can(regex("^[0-9]+$", var.github_owner_id))
    error_message = "Must be the numeric owner ID, not the name."
  }
}

variable "state_bucket" {
  description = "Existing GCS bucket holding Terraform state (created in the README bootstrap step)."
  type        = string
}
