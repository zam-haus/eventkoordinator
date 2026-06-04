#!/usr/bin/env bash
# Regenerate policy_action_types.md from registered action schemas.
# Run from anywhere — the script locates the backend directory relative to itself.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/../backend"
OUTPUT="$SCRIPT_DIR/policy_action_types.md"

cd "$BACKEND_DIR"
uv run python manage.py generate_policy_action_docs --output "$OUTPUT"
echo "Documentation written to $OUTPUT"
