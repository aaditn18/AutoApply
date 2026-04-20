"""Policy / business-rule data loaders.

The YAMLs under ``state/rules/`` and the prompt templates under
``prompts/`` are the *policy layer* of AutoApply — deterministic rules
that encode business decisions (which dropdown options to prefer, which
regexes classify a question, what the LLM's system prompt should say).

Keeping them as plain data files instead of Python constants means:

- Non-code edits: adding a skill alias or a new education-preference
  pattern doesn't require touching ``src/``.
- Independent review: a refactor that moves orchestration code around
  can't silently drop a rule — the YAML is the source of truth.
- Testability: tests can point at fixture YAMLs instead of monkeypatching
  module-level constants.

Use :func:`load_rules` for YAML rule files and :func:`load_prompt` for
prompt templates. Both are cached per-process.
"""

from autoapply.rules.loader import load_prompt, load_rules

__all__ = ["load_rules", "load_prompt"]
