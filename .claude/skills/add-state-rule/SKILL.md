---
name: add-state-rule
description: Walkthrough for adding a new policy YAML under state/rules/ — YAML file + consumer + smoke test
when_to_use: User wants to add a new rule YAML (new regex preference list, new canonical table, new keyword set) to the policy layer
---

# Add a new `state/rules/<name>.yml`

The policy layer holds business rules as YAML data. Adding a new
rule file is a three-touch operation: (a) write the YAML with a
header naming its consumer, (b) update the consumer to load via
`autoapply.rules.load_rules(name)`, (c) add a schema smoke test.
If any of those are missing, the rule file either isn't used or
silently breaks at apply time.

## Decision: which rule file type?

Rule files usually hold one of:

- **Ordered regex preferences** (like `education_preferences.yml`) —
  used when multiple options can match and we want a specific variant.
- **Canonicalization tables** (like `skill_aliases.yml`) — map
  variants to a single canonical name.
- **Keyword sets / labels** (like `geography.yml`, `eeo_semantics.yml`) —
  lookup membership.
- **Lookup-per-key rules** (like `machine_keys.yml`) — structured
  list of `{pattern: ..., attr: ...}` entries.

The file's internal schema is whatever your consumer expects. YAML
is flexible — just keep it simple (dict at the top level; lists and
dicts underneath).

## Steps

### 1. Write the YAML

File: `state/rules/my_new_rule.yml`

**Every rule file starts with a header comment naming its consumer(s).**
This is the contract: anyone editing the YAML can see who'll break
if they rename a key.

```yaml
# Canonical description of what this rule file covers.
#
# Consumer:
#   * src/autoapply/module/path.py (function_name)
#
# Rule-specific notes:
# - Ordering matters (or doesn't)
# - Case sensitivity
# - Anything non-obvious about updating

key_name:
  - value1
  - value2

other_key:
  nested_key: "string value"
```

Values should be quoted where ambiguity matters (especially regex
strings with backslashes — prefer single quotes so YAML doesn't
interpret escape sequences).

### 2. Update the consumer

In the Python module that needs the rule, import the loader and
read the file:

```python
from autoapply.rules import load_rules

_MY_NEW_RULE_DATA = load_rules("my_new_rule")
_KEY_NAME: tuple[str, ...] = tuple(_MY_NEW_RULE_DATA["key_name"])
_OTHER_KEY: dict[str, str] = dict(_MY_NEW_RULE_DATA["other_key"])
```

Module-level load is cached; this happens once per process, not on
every call. If you need to defer, `load_rules` is safe to call
inside a function too.

### 3. Add a schema smoke test

File: `tests/test_rules_loader.py`

Add a test that validates the shape your consumer expects. The goal
is to catch schema regressions at test time, not at apply time.

```python
def test_my_new_rule_shape():
    data = load_rules("my_new_rule")
    assert "key_name" in data
    assert isinstance(data["key_name"], list) and data["key_name"]
    assert "other_key" in data
    assert isinstance(data["other_key"], dict)
    # If regex patterns — validate they compile.
    # for pat in data["key_name"]:
    #     re.compile(pat, re.IGNORECASE)
```

Also add a "does the content match expected semantics" test:

```python
def test_my_new_rule_contains_core_entries():
    data = load_rules("my_new_rule")
    # Spot check specific entries that consumers rely on.
    assert "critical_value" in data["key_name"]
```

### 4. Run tests

```
/test tests/test_rules_loader.py
```

Should pass. If the YAML didn't parse (syntax error, non-dict top
level), `load_rules` raises `ValueError` with a clear message.

### 5. Optionally: edit-time smoke

The `rules-smoke.sh` PostToolUse hook re-runs
`tests/test_rules_loader.py` automatically after any
`state/rules/*.yml` or `prompts/*.md` edit. You get the pass/fail
signal without running anything manually.

## Files touched summary

- `state/rules/my_new_rule.yml` — the YAML data file (with header
  comment naming consumer)
- `src/autoapply/<consumer>.py` — `load_rules("my_new_rule")` call
- `tests/test_rules_loader.py` — schema + semantics tests

## Common mistakes

- **Forgetting the consumer comment.** Someone reading the YAML
  later won't know who depends on it. Always include.
- **Loading at call time when module-level would do.** Module-level
  load runs once per process and avoids repeated YAML parsing (even
  though it's cached, the `.get(...)` chain still runs).
- **Not updating the rule-loader test.** The file works at apply
  time because your consumer ran, but a future rename will fail
  silently. Always add the test.
- **Nested regex strings in double-quoted YAML.** `"\\s+"` works but
  `'\s+'` is clearer — prefer single quotes for regex.

See `tests/test_rules_loader.py` for the canonical examples. Every
current rule file has a corresponding `test_<name>_shape` test plus
a content test.
