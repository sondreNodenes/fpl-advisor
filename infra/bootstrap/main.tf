# One-time foundation, applied by a human with owner rights (see README "Deploy").
# Creates: APIs, Artifact Registry, data bucket, service accounts, IAM, and
# Workload Identity Federation so GitHub Actions can deploy without JSON keys.

terraform {
  required_version = ">= 1.5"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 8.0"
    }
  }
  backend "gcs" {
    prefix = "bootstrap"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

locals {
  apis = [
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudscheduler.googleapis.com",
    "storage.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "sts.googleapis.com",
    "cloudresourcemanager.googleapis.com",
  ]
  runtime_accounts = {
    service   = "Cloud Run service (reads data)"
    job       = "Cloud Run job (ingest and retrain)"
    scheduler = "Cloud Scheduler (starts the job)"
  }
}

resource "google_project_service" "apis" {
  for_each           = toset(local.apis)
  service            = each.value
  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "app" {
  location      = var.region
  repository_id = "fpl-advisor"
  format        = "DOCKER"

  # Keep storage under the 0.5 GB free tier: keep the 3 newest images, delete the rest.
  cleanup_policy_dry_run = false
  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 3
    }
  }
  cleanup_policies {
    id     = "delete-older"
    action = "DELETE"
    condition {
      older_than = "86400s"
    }
  }
  depends_on = [google_project_service.apis]
}

resource "google_storage_bucket" "data" {
  name                        = "${var.project_id}-fpl-data"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = true # data is re-creatable by running the job
  depends_on                  = [google_project_service.apis]
}

resource "google_service_account" "runtime" {
  for_each     = local.runtime_accounts
  account_id   = "fpl-${each.key}"
  display_name = each.value
  depends_on   = [google_project_service.apis]
}

resource "google_service_account" "deployer" {
  account_id   = "fpl-deployer"
  display_name = "GitHub Actions deployer"
  depends_on   = [google_project_service.apis]
}

# Data access: the service only reads, the job reads and writes.
resource "google_storage_bucket_iam_member" "service_reads_data" {
  bucket = google_storage_bucket.data.name
  role   = "roles/storage.objectViewer"
  member = google_service_account.runtime["service"].member
}

resource "google_storage_bucket_iam_member" "job_writes_data" {
  bucket = google_storage_bucket.data.name
  role   = "roles/storage.objectUser"
  member = google_service_account.runtime["job"].member
}

# Deployer: push images, manage Cloud Run and Scheduler, act as the runtime accounts,
# and read/write Terraform state for the app root.
resource "google_artifact_registry_repository_iam_member" "deployer_push" {
  location   = google_artifact_registry_repository.app.location
  repository = google_artifact_registry_repository.app.name
  role       = "roles/artifactregistry.writer"
  member     = google_service_account.deployer.member
}

resource "google_project_iam_member" "deployer" {
  for_each = toset(["roles/run.admin", "roles/cloudscheduler.admin"])
  project  = var.project_id
  role     = each.value
  member   = google_service_account.deployer.member
}

resource "google_service_account_iam_member" "deployer_acts_as_runtime" {
  for_each           = google_service_account.runtime
  service_account_id = each.value.name
  role               = "roles/iam.serviceAccountUser"
  member             = google_service_account.deployer.member
}

resource "google_storage_bucket_iam_member" "deployer_state" {
  bucket = var.state_bucket
  role   = "roles/storage.objectAdmin"
  member = google_service_account.deployer.member
}

# Workload Identity Federation: only the main branch of this repository can deploy.
resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "github"
  display_name              = "GitHub Actions"
  depends_on                = [google_project_service.apis]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  display_name                       = "GitHub OIDC"
  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
  }
  attribute_condition = "assertion.repository == '${var.github_repo}' && assertion.ref == 'refs/heads/main'"
  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account_iam_member" "github_impersonates_deployer" {
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repo}"
}
