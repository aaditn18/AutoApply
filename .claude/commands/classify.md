---
description: "Classify a question label — dumps QuestionType + slot + source"
argument-hint: "<question label>"
allowed-tools: "Bash(.claude/bin/classify.sh:*)"
---

Run a raw question label through `autoapply.answers.classifier.classify`
and print QuestionType, confidence, slot, source, match_text.

Use when:
- Adding a new regex rule to `classifier.py` — confirm it fires.
- Debugging a field that went to `needs_review` unexpectedly.
- Exploring what the classifier recognizes for an unseen label.

```!
.claude/bin/classify.sh "$ARGUMENTS"
```

If output is `UNKNOWN`, the label matches no regex in `_RULES` —
consider whether to add one (and whether it's `PROFILE_SOURCED`,
`LLM_REQUIRED`, or bank-routed). See `.claude/skills/add-question-type/`.
