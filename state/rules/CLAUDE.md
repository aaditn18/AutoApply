# state/rules/ — policy YAML data

All business rules used to live as Python constants. The Phase-1
refactor moved them here. Consumers read via
`autoapply.rules.load_rules(name)` (cached per-process with `@lru_cache`).

## Files

| File | Consumer |
|------|----------|
| `education_preferences.yml` | `execute/submitter/dom/preferences.py` — ordered regex for School/Degree/Discipline |
| `geography.yml` | `execute/submitter/dom/preferences.py` — US state label frozenset |
| `skill_aliases.yml` | `answers/classifier.py` — YOE canonicalization |
| `machine_keys.yml` | `execute/resolution/machine_key.py` — name → Profile attr |
| `export_control.yml` | `execute/resolution/options_snap.py` — ITAR/EAR fallback markers |
| `eeo_semantics.yml` | `execute/submitter/fillers/react_select.py` + `native_select.py` — decline keywords |
| `browser_pool.yml` | `execute/submitter/phases/browser.py` — Chromium UA pool |

Each file's header comment names its consumer. Don't rename a
top-level key without touching the consumer in the same commit.

## Invariants

- **Every new YAML file needs a schema smoke-test.** Add to
  `tests/test_rules_loader.py`:
  ```python
  def test_my_new_rule_shape():
      data = load_rules("my_new_rule")
      assert "expected_key" in data
      # regex patterns compile
      for pat in data["patterns"]:
          re.compile(pat, re.IGNORECASE)
  ```
  The `add-state-rule` skill walks through the full add-a-rule flow.

- **Loader caches per-process.** `autoapply.rules.load_rules` uses
  `@lru_cache`. Tests that mutate a rule file between assertions MUST
  call `autoapply.rules.loader.clear_cache()` — see
  `tests/test_rules_loader.py::_clear_rule_cache` fixture.

- **Rule files must parse to a dict at top level.** Lists at the top
  raise `ValueError`. This is a programming error (not a runtime
  condition to tolerate).

- **Lowercase labels** in `geography.yml` — consumers lowercase scraped
  DOM text before comparing.

- **Preserve regex-string escape semantics.** YAML single-quoted
  strings don't interpret backslashes — prefer them for regex to keep
  patterns readable. Double-quoted strings require `\\\\s+` style
  escaping; single-quoted allow `\s+`.

## Editing without touching code

Most rule edits (adding a new education-preference variant, adding a
decline keyword, adding a UA string) are YAML-only. The `rules-smoke.sh`
PostToolUse hook runs `tests/test_rules_loader.py` after every YAML
edit so schema breaks surface immediately.

## Changing a rule file's schema

If you rename a top-level key or restructure a rule file:

1. Edit the YAML.
2. Update the consumer module(s) — header comments in each YAML
   file name them.
3. Update the corresponding test in `tests/test_rules_loader.py`.
4. Run `/test-fast` to verify the rules subset passes.

This is the workflow `state/rules/CLAUDE.md` is here to prevent
forgetting.
