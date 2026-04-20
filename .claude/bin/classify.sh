#!/usr/bin/env bash
# /classify — classify a raw question label via the classifier.
# Prints QuestionType, confidence, slot, source, and match_text.
set -eo pipefail
cd "$(dirname "$0")/../.."
source "$(dirname "$0")/_lib.sh"

if [ $# -eq 0 ]; then
  echo "Usage: /classify <question label>"
  exit 0
fi

# All args joined as the label text. Pass via env var to sidestep any
# shell-quoting weirdness with punctuation in the label.
export CLASSIFY_LABEL="$*"

"$PY" -c "
import os, sys
sys.path.insert(0, 'src')
from autoapply.answers.classifier import classify
label = os.environ.get('CLASSIFY_LABEL', '')
c = classify(label)
print(f'label:      {label!r}')
print(f'type:       {c.type.value}')
print(f'confidence: {c.confidence}')
print(f'slot:       {c.slot}')
print(f'source:     {c.source}')
print(f'match_text: {c.match_text!r}')
"
