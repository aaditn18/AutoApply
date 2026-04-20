---
description: "Classify a question label — dumps QuestionType + slot + source"
argument-hint: "<question label>"
allowed-tools: "Bash(python *)"
---

Run a single question label through `autoapply.answers.classifier.classify`
and print the QuestionType, confidence, slot, and source.

Use this when:
- Adding a new regex rule to `classifier.py` — confirm it fires.
- Debugging a field that went to `needs_review` when it shouldn't have.
- Exploring what the classifier recognizes for a label you see in
  the wild.

```!
python3 -c "
import sys
sys.path.insert(0, 'src')
from autoapply.answers.classifier import classify
label = '''$ARGUMENTS'''
c = classify(label)
print(f'label:      {label!r}')
print(f'type:       {c.type.value}')
print(f'confidence: {c.confidence}')
print(f'slot:       {c.slot}')
print(f'source:     {c.source}')
print(f'match_text: {c.match_text!r}')
"
```

If the output is `UNKNOWN`, the label doesn't match any regex in
`_RULES` — consider whether adding one makes sense (and whether it
belongs in `PROFILE_SOURCED`, `LLM_REQUIRED`, or bank-routed).
