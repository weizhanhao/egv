#!/usr/bin/env bash
#
# run-coverage.sh — invoked by Regression Auditor.
#
# Runs the project's coverage command, writing the test log to a specified path.
# The coverage-final.json is produced as a side-effect at the project's standard location.
#
# Usage: bash run-coverage.sh <project_root> <coverage_command> <output_log_path>

set -e

PROJECT_ROOT="${1:?project_root required}"
COVERAGE_CMD="${2:?coverage_command required}"
OUTPUT_LOG="${3:?output_log path required}"

mkdir -p "$(dirname "$OUTPUT_LOG")"

cd "$PROJECT_ROOT" || {
  echo "[run-coverage] FATAL: cannot cd into $PROJECT_ROOT" >&2
  exit 2
}

echo "[run-coverage] project_root=$PROJECT_ROOT" | tee "$OUTPUT_LOG"
echo "[run-coverage] command=$COVERAGE_CMD" | tee -a "$OUTPUT_LOG"
echo "[run-coverage] started=$(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee -a "$OUTPUT_LOG"
echo "----- BEGIN COVERAGE RUN -----" | tee -a "$OUTPUT_LOG"

# shellcheck disable=SC2086
eval "$COVERAGE_CMD" 2>&1 | tee -a "$OUTPUT_LOG"
EXIT_CODE=${PIPESTATUS[0]}

echo "----- END COVERAGE RUN -----" | tee -a "$OUTPUT_LOG"
echo "[run-coverage] finished=$(date -u +%Y-%m-%dT%H:%M:%SZ) exit_code=$EXIT_CODE" | tee -a "$OUTPUT_LOG"

exit "$EXIT_CODE"
