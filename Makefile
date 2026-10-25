.PHONY: install test lint format run ingest train evaluate tf-check security bootstrap run-job destroy

PROJECT_ID ?= $(shell gcloud config get-value project 2>/dev/null)
REGION ?= europe-north1
TF_STATE = $(PROJECT_ID)-tfstate

install:
	uv sync

test:
	uv run pytest -q

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff check --fix .
	uv run ruff format .

run:
	uv run uvicorn fpl_advisor.app:app --reload

ingest:
	uv run python -m fpl_advisor.ingest

train:
	uv run python -m fpl_advisor.model

evaluate:
	uv run python -m fpl_advisor.evaluate

tf-check:
	terraform fmt -check -recursive infra
	for root in infra/bootstrap infra/app; do \
		terraform -chdir=$$root init -backend=false -input=false >/dev/null && terraform -chdir=$$root validate; \
	done

# All security scans (secrets, CVEs, bandit rules, image, IaC); reports in security/latest.
security:
	security/scan.sh security/latest

# All security scans (secrets, CVEs, bandit rules, image, IaC); reports in security/latest.
security:
	security/scan.sh security/latest

# One-time: state bucket + foundation (see README "Deploy").
bootstrap:
	gcloud storage buckets describe gs://$(TF_STATE) >/dev/null 2>&1 || \
		gcloud storage buckets create gs://$(TF_STATE) --location=$(REGION) --uniform-bucket-level-access
	gcloud storage buckets update gs://$(TF_STATE) --versioning
	terraform -chdir=infra/bootstrap init -input=false -backend-config="bucket=$(TF_STATE)"
	terraform -chdir=infra/bootstrap apply -var="project_id=$(PROJECT_ID)" -var="state_bucket=$(TF_STATE)"

run-job:
	gcloud run jobs execute fpl-pipeline --region=$(REGION) --wait

# Removes everything Terraform created (app first, then foundation). The state bucket stays.
destroy:
	terraform -chdir=infra/app init -input=false -backend-config="bucket=$(TF_STATE)"
	terraform -chdir=infra/app destroy -var="project_id=$(PROJECT_ID)" -var="image=unused"
	terraform -chdir=infra/bootstrap init -input=false -backend-config="bucket=$(TF_STATE)"
	terraform -chdir=infra/bootstrap destroy -var="project_id=$(PROJECT_ID)" -var="state_bucket=$(TF_STATE)"
