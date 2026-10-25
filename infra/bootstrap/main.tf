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

  # Versioning protects the model and snapshots against a bad overwrite; old versions
  # are deleted after 7 days or once 3 newer ones exist, so storage stays bounded.
  versioning {
    enabled = true
  }
  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      days_since_noncurrent_time = 7
    }
  }
  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      num_newer_versions = 3
      with_state         = "ARCHIVED"
    }
  }
  # Snapshots older than two seasons are no longer used for training or evaluation.
  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      age            = 730
      matches_prefix = ["snapshots/"]
    }
  }
  lifecycle_rule {
    action {
      type = "AbortIncompleteMultipartUpload"
    }
    condition {
      age = 1
    }
  }
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

# run.developer manages services and jobs but cannot change their IAM policies. Terraform
# needs exactly two IAM writes (public invoker on the service, scheduler invoker on the
# job), so they come from a custom role instead of run.admin.
resource "google_project_iam_custom_role" "deployer_run_iam" {
  role_id     = "fplDeployerRunIam"
  title       = "FPL deployer: Cloud Run IAM bindings"
  description = "Set IAM policy on Cloud Run services and jobs (nothing else)."
  permissions = ["run.services.setIamPolicy", "run.jobs.setIamPolicy"]
}

resource "google_project_iam_member" "deployer" {
  for_each = toset([
    "roles/run.developer",
    "roles/cloudscheduler.admin", # scheduler jobs only; no IAM or other services
  ])
  project = var.project_id
  role    = each.value
  member  = google_service_account.deployer.member
}

resource "google_project_iam_member" "deployer_run_iam" {
  project = var.project_id
  role    = google_project_iam_custom_role.deployer_run_iam.name
  member  = google_service_account.deployer.member
}

# Google grants the default Compute Engine service account Editor on new projects. None of
# our workloads use it (each has its own account), so remove the grant: anything created
# later without an explicit service account would otherwise run as project Editor.
data "google_project" "this" {}

resource "google_project_iam_member_remove" "default_compute_editor" {
  project = var.project_id
  role    = "roles/editor"
  member  = "serviceAccount:${data.google_project.this.number}-compute@developer.gserviceaccount.com"
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

# Workload Identity Federation: only pushes to main in this repository can deploy.
# The condition uses GitHub's numeric repository and owner IDs, which never change; names
# can be re-registered by someone else if the account or repository is renamed or deleted.
locals {
  wif_condition = join(" && ", [
    "assertion.repository_id == '${var.github_repository_id}'",
    "assertion.repository_owner_id == '${var.github_owner_id}'",
    "assertion.ref == 'refs/heads/main'",
    "assertion.ref_type == 'branch'",
  ])
}

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
    "google.subject"                = "assertion.sub"
    "attribute.repository"          = "assertion.repository"
    "attribute.repository_id"       = "assertion.repository_id"
    "attribute.repository_owner_id" = "assertion.repository_owner_id"
    "attribute.ref"                 = "assertion.ref"
  }
  attribute_condition = local.wif_condition
  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }

  lifecycle {
    # Guard against a refactor silently widening who can deploy.
    precondition {
      condition = alltrue([
        strcontains(local.wif_condition, "assertion.ref == 'refs/heads/main'"),
        strcontains(local.wif_condition, "assertion.repository_id == '"),
        strcontains(local.wif_condition, "assertion.repository_owner_id == '"),
      ])
      error_message = "The WIF condition must pin repository ID, owner ID and the main branch."
    }
  }
}

# Only identities from this repository ID may act as the deployer (the provider condition
# above already limits tokens to the main branch).
resource "google_service_account_iam_member" "github_impersonates_deployer" {
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository_id/${var.github_repository_id}"
}
