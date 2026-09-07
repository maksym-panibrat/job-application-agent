#!/usr/bin/env bash
# Read-only fresh-clone checks. This script never installs dependencies or contacts production.
set -uo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/preflight.sh [--skip-db]

Check the local toolchain, lockfiles, required .env keys, Docker Compose, and
(unless skipped) the repository's local Postgres container and connectivity.
No dependencies are installed, no environment values are printed, and no
production endpoint is contacted. Remote Docker endpoints are refused before
daemon or Compose operations. uv owns Python interpreter provisioning.

Options:
  --skip-db  Check Docker/Compose but not local db container state/connectivity.
             Useful before: docker compose up -d --wait db
  -h, --help Show this help.

Exit status:
  0  every requested check passed
  1  one or more checks failed
  2  invalid command-line usage
EOF
}

skip_db=0
case "${1:-}" in
  "") ;;
  --skip-db) skip_db=1 ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac
if (( $# > 1 )); then
  usage >&2
  exit 2
fi

repo_root=${PREFLIGHT_ROOT:-"$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"}
cd "$repo_root" || {
  printf 'FAIL repository root is not accessible\n' >&2
  exit 1
}

failures=0
pass() { printf 'PASS %s\n' "$1"; }
fail() {
  printf 'FAIL %s\n' "$1" >&2
  failures=$((failures + 1))
}
has_command() {
  if command -v "$1" >/dev/null 2>&1; then
    return 0
  fi
  fail "$1 is not installed or not on PATH"
  return 1
}
node_version_supported() {
  local actual=$1 major minor
  IFS=. read -r major minor _ <<<"$actual"
  [[ $major =~ ^[0-9]+$ && $minor =~ ^[0-9]+$ ]] || return 1
  (( (major == 20 && minor >= 19) || (major == 22 && minor >= 12) || major > 22 ))
}
env_value() {
  local key=$1 line value
  while IFS= read -r line || [[ -n $line ]]; do
    if [[ $line =~ ^[[:space:]]*(export[[:space:]]+)?${key}[[:space:]]*=(.*)$ ]]; then
      value=${BASH_REMATCH[2]}
      value="${value#"${value%%[![:space:]]*}"}"
      value="${value%"${value##*[![:space:]]}"}"
      if [[ $value == \"*\" && $value == *\" ]]; then
        value=${value:1:${#value}-2}
      elif [[ $value == \'*\' && $value == *\' ]]; then
        value=${value:1:${#value}-2}
      fi
      printf '%s' "$value"
      return 0
    fi
  done < .env
  return 1
}
is_placeholder() {
  local value=${1,,}
  [[ -z $value ||
     $value == your-* ||
     $value == *changeme* ||
     $value == *change-me* ||
     $value == *replace-me* ||
     $value == *replace_this* ||
     $value == \<*\> ||
     $value == "..." ]]
}
docker_endpoint_is_local() {
  case "$1" in
    unix://*|npipe://*) return 0 ;;
    *) return 1 ;;
  esac
}
docker_endpoint() {
  local context
  # Docker gives DOCKER_CONTEXT precedence over DOCKER_HOST when both are set.
  if [[ -n ${DOCKER_CONTEXT:-} ]]; then
    docker context inspect --format '{{.Endpoints.docker.Host}}' "$DOCKER_CONTEXT" 2>/dev/null
    return
  fi
  if [[ -n ${DOCKER_HOST:-} ]]; then
    printf '%s' "$DOCKER_HOST"
    return 0
  fi
  context=$(docker context show 2>/dev/null)
  [[ -n $context ]] || return 1
  docker context inspect --format '{{.Endpoints.docker.Host}}' "$context" 2>/dev/null
}

printf 'Local clone preflight (read-only)\n'

docker_ok=0
compose_ok=0
if has_command docker; then
  if ! docker --version >/dev/null 2>&1; then
    fail "Docker exists but could not report its version"
  else
    endpoint=$(docker_endpoint || true)
    if ! docker_endpoint_is_local "$endpoint"; then
      fail "refusing non-local or unknown Docker endpoint (expected unix:// or npipe://)"
    elif docker info >/dev/null 2>&1; then
      pass "Docker CLI can reach a verified local daemon"
      docker_ok=1
    else
      fail "Docker is installed but the verified local daemon is unavailable"
    fi
  fi
  if (( docker_ok )); then
    if docker compose version >/dev/null 2>&1; then
      pass "Docker Compose plugin is available"
      compose_ok=1
    else
      fail "Docker Compose plugin is unavailable (expected: docker compose)"
    fi
  fi
fi

if has_command uv; then
  if uv --version >/dev/null 2>&1; then
    pass "uv is available (it will provision the locked Python interpreter)"
  else
    fail "uv exists but could not report its version"
  fi
fi

if has_command node; then
  node_version=$(node -p 'process.versions.node' 2>/dev/null || true)
  if node_version_supported "$node_version"; then
    pass "Node $node_version satisfies the frontend toolchain"
  else
    fail "Node ^20.19 or >=22.12 is required (found ${node_version:-unknown})"
  fi
fi

if has_command npm; then
  if npm --version >/dev/null 2>&1; then
    pass "npm is available"
  else
    fail "npm exists but could not report its version"
  fi
fi

if [[ -f .env ]]; then
  for required_key in DATABASE_URL GOOGLE_API_KEY; do
    required_value=$(env_value "$required_key" || true)
    if is_placeholder "$required_value"; then
      fail ".env key $required_key is missing, empty, or still a placeholder"
    else
      pass ".env key $required_key is configured (value not printed)"
    fi
  done
else
  fail ".env is missing; create it with: cp .env.example .env"
fi

if [[ -f uv.lock ]]; then
  pass "uv.lock exists"
else
  fail "uv.lock is missing; locked backend setup cannot be reproduced"
fi
if [[ -f frontend/package-lock.json ]]; then
  pass "frontend/package-lock.json exists"
else
  fail "frontend/package-lock.json is missing; npm ci cannot be used"
fi

if (( skip_db )); then
  printf 'SKIP local db state/connectivity (--skip-db)\n'
elif (( docker_ok && compose_ok )); then
  running_services=$(docker compose ps --status running --services db 2>/dev/null || true)
  if [[ $running_services == *"db"* ]]; then
    pass "local Compose db service is running"
    retry_attempts=${PREFLIGHT_DB_RETRY_ATTEMPTS:-10}
    retry_delay=${PREFLIGHT_DB_RETRY_DELAY:-1}
    db_ready=0
    if ! [[ $retry_attempts =~ ^[1-9][0-9]*$ ]]; then
      retry_attempts=10
    fi
    for (( attempt=1; attempt<=retry_attempts; attempt++ )); do
      if docker compose exec -T db pg_isready -U jobagent -d jobagent >/dev/null 2>&1; then
        db_ready=1
        break
      fi
      if (( attempt < retry_attempts )); then
        sleep "$retry_delay"
      fi
    done
    if (( db_ready )); then
      pass "local Postgres accepts connections"
    else
      fail "local db stayed unready after $retry_attempts bounded pg_isready attempts"
    fi
  else
    fail "local Compose db is not running; start it with: docker compose up -d --wait db"
  fi
else
  fail "local db checks require a reachable Docker daemon and Compose plugin"
fi

printf '\nLocked dependency setup (guidance only; not run):\n'
printf '  uv sync --locked --dev\n'
printf '  (cd frontend && npm ci)\n'

if (( failures > 0 )); then
  printf '\nPreflight failed: %d check(s) need attention.\n' "$failures" >&2
  exit 1
fi
printf '\nPreflight passed.\n'
