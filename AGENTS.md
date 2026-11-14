# Working on Smelt

Read [CONTRIBUTING.md](CONTRIBUTING.md) for architecture, rule naming, testing
and release requirements. Use the bundled
[Smelt skill](smelt/skills/smelt/SKILL.md) for checker commands and policy repairs.

- Before creating or moving code, run `uv run smelt context <module-or-path>`.
- Follow `smelt.yml`, including the package's positional-parameter limit and
  explicit serialized enum values. Review hints using the rule's inference limits.
- Edit original guides and rule metadata, then run
  `uv run python scripts/generate.py`. Never edit generated rule pages or skill
  references by hand.
- Run `uv run smelt check --changed --base origin/main --fail-on warning` while
  reviewing the branch and `uv run smelt check --staged --fail-on warning` for
  the commit. Policy changes also require a full strict-coverage check.
- Run the relevant tests, `uv run prek run --all-files` and
  `uv run ruff format --check .` before pushing. Release preparation also requires
  the full test suite, generated-file drift check and distribution smoke tests.
- Do not accept debt or weaken checks merely to obtain a passing result.
