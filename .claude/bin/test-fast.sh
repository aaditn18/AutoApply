#!/usr/bin/env bash
# /test-fast — injection guard + rules loader + parsers + audit subset
# (<1s). High-signal feedback for classifier/rule/prompt edits.
set -eo pipefail
cd "$(dirname "$0")/../.."
source "$(dirname "$0")/_lib.sh"

"$PY" -m pytest \
  tests/test_injection_guard.py \
  tests/test_rules_loader.py \
  tests/test_tex_parser.py \
  tests/test_audit_review_flags.py \
  -q 2>&1 | tail -6
