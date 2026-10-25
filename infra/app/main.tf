# Application resources, applied by GitHub Actions on every push to main.
# Nothing here bills while idle: Cloud Run scales to zero and the job only runs on schedule.

terraform {
  required_version = ">= 1.5"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 8.0"
    }
  }
  backend "gcs" {
    prefix = "app"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

locals {
  bucket = "${var.project_id}-fpl-data"
  sa     = { for k in ["service", "job", "scheduler"] : k => "fpl-${k}@${var.project_id}.iam.gserviceaccount.com" }
  env = {
    FPL_DATA_DIR   = "/data"
    FPL_MODEL_PATH = "/data/models/model.joblib"
    FPL_CACHE_DIR  = "/tmp/fpl-cache"
    # Rate limits key on the client IP that Google's front end appends to X-Forwarded-For.
    FPL_TRUST_PROXY_HEADERS = "1"
  }
}

resource "google_cloud_run_v2_service" "app" {
  name                = "fpl-advisor"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false

  template {
    service_account                  = local.sa.service
    execution_environment            = "EXECUTION_ENVIRONMENT_GEN2" # needed for the GCS volume
    max_instance_request_concurrency = 10
    timeout                          = "120s"

    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }

    containers {
      image = var.image
      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
        cpu_idle          = true
        startup_cpu_boost = true
      }
      dynamic "env" {
        for_each = local.env
        content {
          name  = env.key
          value = env.value
        }
      }
      volume_mounts {
        name       = "data"
        mount_path = "/data"
      }
      startup_probe {
        http_get {
          path = "/health"
        }
      }
    }

    volumes {
      name = "data"
      gcs {
        bucket    = local.bucket
        read_only = true
      }
    }
  }
}

resource "google_cloud_run_v2_service_iam_member" "public" {
  name     = google_cloud_run_v2_service.app.name
  location = google_cloud_run_v2_service.app.location
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_cloud_run_v2_job" "pipeline" {
  name                = "fpl-pipeline"
  location            = var.region
  deletion_protection = false

  template {
    task_count = 1
    template {
      service_account       = local.sa.job
      execution_environment = "EXECUTION_ENVIRONMENT_GEN2"
      timeout               = "1800s" # a new gameweek fetches ~450 players at 1 request/s
      max_retries           = 1

      containers {
        image   = var.image
        command = ["python", "-m", "fpl_advisor.pipeline"]
        resources {
          limits = {
            cpu    = "1"
            memory = "2Gi"
          }
        }
        dynamic "env" {
          for_each = local.env
          content {
            name  = env.key
            value = env.value
          }
        }
        volume_mounts {
          name       = "data"
          mount_path = "/data"
        }
      }

      volumes {
        name = "data"
        gcs {
          bucket    = local.bucket
          read_only = false
        }
      }
    }
  }
}

resource "google_cloud_run_v2_job_iam_member" "scheduler_runs_job" {
  name     = google_cloud_run_v2_job.pipeline.name
  location = google_cloud_run_v2_job.pipeline.location
  role     = "roles/run.invoker"
  member   = "serviceAccount:${local.sa.scheduler}"
}

resource "google_cloud_scheduler_job" "pipeline" {
  for_each = var.schedules

  name      = "fpl-pipeline-${each.key}"
  region    = var.scheduler_region
  schedule  = each.value
  time_zone = "Etc/UTC"

  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.project_id}/locations/${var.region}/jobs/${google_cloud_run_v2_job.pipeline.name}:run"
    oauth_token {
      service_account_email = local.sa.scheduler
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }

  depends_on = [google_cloud_run_v2_job_iam_member.scheduler_runs_job]
}
