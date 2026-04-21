#!/usr/bin/env bash
# PostToolUse validator. Runs fast checks on the edited file and exits 2 on failure.
set -o pipefail

FILE=$(jq -r '.tool_input.file_path // .tool_input.notebook_path // empty')
[ -z "$FILE" ] && exit 0
[ ! -f "$FILE" ] && exit 0

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
cd "$REPO_ROOT" || exit 0

fail() {
  echo "$1" >&2
  exit 2
}

case "$FILE" in
  # Python: ruff check + format check (both are per-file and fast).
  *.py)
    uv run ruff check "$FILE" 2>&1 || fail "ruff check failed on $FILE"
    uv run ruff format --check "$FILE" 2>&1 || fail "ruff format --check failed on $FILE (run 'uv run ruff format $FILE' to fix)"
    ;;

  # TypeScript / React: tsc is project-wide and slower; pre-commit already runs it.
  # Uncomment if you want blocking type-checks on every edit (adds ~3-5s per frontend edit).
  *.ts|*.tsx|*.js|*.jsx)
    # (cd frontend && npx --no-install tsc --noEmit 2>&1) || fail "tsc failed"
    exit 0
    ;;

  *)
    exit 0
    ;;
esac

exit 0
