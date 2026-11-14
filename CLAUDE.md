# smelt

Maintainer guide for agents. The full rules live in CONTRIBUTING.md:

@CONTRIBUTING.md

Points that are easy to get wrong:

- Name new rules after the finding with the qualifiers in "Rule names" in
  CONTRIBUTING.md (`redundant-`, `missing-`, `forbidden-`, `excess-`,
  `prefer-<alternative>`, ...). Never start a new name with `no-`,
  `unnecessary-`, `review-`, `require-` or `check-`.
- Allocate codes from the live catalog (`uv run smelt rules --format json`) and
  regenerate with `uv run python scripts/generate.py` after registering a rule.
- Run `uv run ruff format --check .` in addition to prek before pushing; CI
  rejects unformatted files.
