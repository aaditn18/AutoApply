---
name: add-question-type
description: Walkthrough for adding a new QuestionType (PROFILE_SOURCED / LLM_REQUIRED / bank-routed) to the classifier + bank pipeline
when_to_use: User wants to add a new QuestionType or needs the classifier to recognize a new label pattern
---

# Add a new `QuestionType`

The classifier + bank pipeline recognizes ~50 `QuestionType` values
today. Adding a new one requires touching several files in a specific
order. Skipping a step will either (a) leave the type unreachable
(classifier never returns it), (b) leave the bank unable to answer
it (falls through to review), or (c) break tests.

## Decision: which category?

First, categorize the new type:

- **`PROFILE_SOURCED`** — answer comes from a field on `Profile`
  (email, phone, city, EEO, YOE). The classifier emits this type, the
  bank reads the profile attr. **Answer is always the same across
  jobs.**
- **`LLM_REQUIRED`** — answer is essay / free-response that needs
  JD context (why_company, why_role, strengths). The classifier emits
  this type; bank flags the field for the batched LLM (no per-field
  answer). **Answer varies by job.**
- **Bank-routed (default)** — answer lives in
  `state/answer_bank.yml`, possibly per-track. Neither PROFILE_SOURCED
  nor LLM_REQUIRED. Used for things like "salary expectation" or
  "willing to relocate" where a canned answer works.
- **`REVIEW_REQUIRED`** — policy-sensitive (mental health, criminal
  record, substance use). The classifier emits this type; bank forces
  review queue. **Never auto-answered.** Reserved for rare cases.

## Steps

### 1. Add the enum value

File: `src/autoapply/answers/types.py`

Append to `QuestionType`:

```python
class QuestionType(str, Enum):
    # ...
    MY_NEW_TYPE = "my_new_type"
```

If the answer is always the same across jobs, also add to the
relevant frozenset at the bottom of the file:

```python
PROFILE_SOURCED = frozenset({
    # ...
    QuestionType.MY_NEW_TYPE,
})
```

(Or `LLM_REQUIRED` / `REVIEW_REQUIRED` as appropriate.)

### 2. Add a regex rule to the classifier

File: `src/autoapply/answers/classifier.py`

Append to `_RULES`. Put specific-first — earlier entries win.

```python
_RULES: list[_Rule] = [
    # ...
    (QuestionType.MY_NEW_TYPE,
     re.compile(r"^(?:what(?:'s)? your|do you have) (?:security )?clearance", re.I),
     None),
]
```

The third tuple element is an optional `SlotFn` callable (`_slot_skill`
etc.) — needed only when the type carries a slot (e.g., YOE_LANGUAGE
captures the skill name).

### 3. Wire the bank

File: `src/autoapply/answers/bank.py`

**For PROFILE_SOURCED:** add a one-line dict entry to
`_SIMPLE_PROFILE_ATTRS`:

```python
_SIMPLE_PROFILE_ATTRS = {
    # ...
    QuestionType.MY_NEW_TYPE: "my_new_profile_attr",
}
```

If the lookup is non-trivial (needs name splitting, date formatting,
slot handling), add a handler function and dispatch in
`_from_profile`.

**For bank-routed:** nothing to do in `bank.py`; just add the entry
to `state/answer_bank.yml`:

```yaml
my_new_type:
  _default: "A reasonable default answer."
  swe: "Track-specific override if needed."
```

**For LLM_REQUIRED / REVIEW_REQUIRED:** no bank changes. The
`AnswerBank.answer` method's policy-set checks route automatically.

### 4. Add the Profile field (PROFILE_SOURCED only)

Files: `src/autoapply/profile/schema.py` + `state/profile.json`

1. Add to `Profile` Pydantic model with a sensible default:

   ```python
   class Profile(BaseModel):
       # ...
       my_new_profile_attr: str = "default value"
   ```

2. Populate in `state/profile.json` for every track (swe, ml, hpc,
   quant). The default in the schema means older profile.json files
   without the new key still work, but you want explicit values.

### 5. Add a classifier paraphrase test

File: `tests/test_answer_bank.py`

Find the `CLASSIFIER_CASES` parametrize block and add paraphrases:

```python
("What is your security clearance level?", QuestionType.MY_NEW_TYPE),
("Do you hold a clearance?", QuestionType.MY_NEW_TYPE),
("Clearance level:", QuestionType.MY_NEW_TYPE),
```

For PROFILE_SOURCED types, also add a bank-answer test showing the
value flows through:

```python
def test_my_new_type_profile_sourced():
    bank = AnswerBank(...)
    profile = Profile(..., my_new_profile_attr="SECRET")
    c = ClassifiedQuestion(type=QuestionType.MY_NEW_TYPE, confidence=1.0)
    assert bank.answer(c, profile=profile, track="swe").value == "SECRET"
```

### 6. Run tests

```
/test-fast
/test tests/test_answer_bank.py
```

Confirm the new classifier rule fires and the bank returns the
expected value.

### 7. Smoke-test with a live example

```
/classify "What is your security clearance level?"
```

Should print `type: my_new_type`. If it prints `UNKNOWN`, the regex
didn't fire — check rule ordering (specific-first) and test the
pattern against the exact label:

```python
import re
rx = re.compile(r"your-pattern-here", re.I)
rx.search("What is your security clearance level?")
```

## Files touched summary

- `src/autoapply/answers/types.py` — enum + frozenset
- `src/autoapply/answers/classifier.py` — regex rule
- `src/autoapply/answers/bank.py` — dict entry (PROFILE_SOURCED)
- `src/autoapply/profile/schema.py` — Profile field (PROFILE_SOURCED)
- `state/profile.json` — populated values (PROFILE_SOURCED)
- `state/answer_bank.yml` — defaults (bank-routed only)
- `tests/test_answer_bank.py` — classifier + bank tests

All touches are small (one-line or one-block). The mental load is
remembering all 7 — hence this skill.
