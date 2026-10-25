#!/usr/bin/env bash
# Runs every security scan and writes raw reports to $1 (default: security/latest).
# Needs only docker and uv. STRICT=1 (default) exits non-zero when a gate fails;
# STRICT=0 only reports (used for the baseline).
set -uo pipefail
cd "$(dirname "$0")/.."

OUT=${1:-security/latest}
STRICT=${STRICT:-1}
IMAGE=${IMAGE:-fpl-advisor:scan}
TRIVY=aquasec/trivy:0.76.1@sha256:790fd66d1697395d8693d1acaa1ea702406a20f109b6fc7bac8db623eca6c06c
GITLEAKS=ghcr.io/gitleaks/gitleaks:v8.30.1@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f
PIP_AUDIT=pip-audit==2.9.0

mkdir -p "$OUT"
failed=()
gate() { # gate <name> <command...>: run, record pass/fail
  local name=$1; shift
  if "$@"; then echo "PASS  $name"; else echo "FAIL  $name"; failed+=("$name"); fi
}
trivy() {
  docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
    -v "$PWD:/src" -v fpl-trivy-cache:/root/.cache/trivy -w /src "$TRIVY" "$@"
}

# 1. Secrets in the full git history.
secrets() {
  docker run --rm -v "$PWD:/repo" \
    -e GIT_CONFIG_COUNT=1 -e GIT_CONFIG_KEY_0=safe.directory -e GIT_CONFIG_VALUE_0='*' \
    "$GITLEAKS" git /repo --redact --exit-code 1 \
    --report-format json --report-path "/repo/$OUT/gitleaks.json" >"$OUT/gitleaks.txt" 2>&1
}

# 2. Known CVEs in locked dependencies (runtime and dev).
deps() {
  local ok=0 group
  for group in runtime dev; do
    local flag=--no-dev; [ "$group" = dev ] && flag=--only-dev
    uv export --frozen $flag --no-emit-project --no-hashes -q >"$OUT/requirements-$group.txt"
    uvx -q "$PIP_AUDIT" -r "$OUT/requirements-$group.txt" --disable-pip --no-deps \
      -f json -o "$OUT/pip-audit-$group.json" >"$OUT/pip-audit-$group.txt" 2>&1 || ok=1
  done
  return $ok
}

# 3. Static analysis with ruff's bandit rules (configured in pyproject.toml).
sast() {
  uv run -q ruff check --select S --output-format concise . >"$OUT/ruff-bandit.txt" 2>&1
}

# 4. Container image: full report, then gate on fixable HIGH/CRITICAL.
image() {
  docker build -q -t "$IMAGE" . >/dev/null || return 1
  docker image inspect "$IMAGE" --format '{{.Size}}' >"$OUT/image-size-bytes.txt"
  trivy image --quiet --scanners vuln --format json -o "$OUT/trivy-image.json" "$IMAGE" &&
    trivy convert --quiet --format table -o "$OUT/trivy-image.txt" "$OUT/trivy-image.json" &&
    trivy image --quiet --scanners vuln --ignore-unfixed --severity HIGH,CRITICAL \
      --exit-code 1 --format table "$IMAGE" >"$OUT/trivy-image-gate.txt" 2>&1
}

# 5. Infrastructure as code (Terraform) and the Dockerfile; accepted risks in .trivyignore.
iac() {
  trivy config --quiet --skip-dirs '**/.terraform' --skip-dirs .venv --skip-dirs data --skip-files '**/tfplan' \
    --format json -o "$OUT/trivy-config.json" . &&
    trivy convert --quiet --format table -o "$OUT/trivy-config.txt" "$OUT/trivy-config.json" &&
    trivy config --quiet --skip-dirs '**/.terraform' --skip-dirs .venv --skip-dirs data --skip-files '**/tfplan' \
      --ignorefile .trivyignore --severity HIGH,CRITICAL,MEDIUM --exit-code 1 . \
      >"$OUT/trivy-config-gate.txt" 2>&1
}

gate secrets secrets
gate dependencies deps
gate sast sast
gate image image
gate iac iac
uv run -q python security/summarize.py "$OUT"

if [ ${#failed[@]} -gt 0 ]; then
  echo "Failed gates: ${failed[*]} (reports in $OUT)"
  [ "$STRICT" = 1 ] && exit 1
fi
exit 0
