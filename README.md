# smelt

Static architecture guardrails for Python. Describe your features, layers and roles in
`smelt.yaml`; `smelt check` reports every import and construct that breaks them, with the
exact location, the allowed alternative and a hint on how to fix it.

## Usage

```bash
smelt check                          # whole project, text output
smelt check --changed                # only files changed against HEAD (incl. untracked)
smelt check --changed --base origin/main --format json
smelt context voice                  # architecture briefing for a feature or path
smelt explain SMT101                 # rationale, examples and config knobs of a rule
smelt rules                          # all rules with defaults
smelt debt                           # record today's violations as known debt
smelt debt --prune                   # drop debt entries that were fixed
```

`smelt debt` lets an existing project adopt smelt incrementally: with `debt: .smelt/debt.json`
in `smelt.yaml`, `smelt check` only fails on new violations, and SMT903 reports entries that
were fixed and can leave the file.

Exit codes: `0` clean, `1` violations at or above `--fail-on`, `2` config or usage error.

Silence a single finding inline, always with a reason:

```python
from gateway.infra.sql import Repo  # smelt: ignore[SMT101] -- migration tracked in #123
```

With `tests.layout: mirror`, every test file must mirror a source module by its path:
`tests/billing/test_invoice.py` needs `app/billing/invoice.py`, and a package test
`tests/billing/test_billing.py` needs `app/billing/`. Not every module needs a test, but a
test whose source is missing or elsewhere is an error. Deliberately unmirrored tests go in
`tests.unmirrored` (e.g. `["tests/integration/**"]`); `tests.mirror_suffixes: true` also
allows `test_invoice_<topic>.py`. `tests.mirror` sets the convention relative to the test
root: the default `{path}/test_{module}.py` drops the root package, `{root}/{path}/test_{module}.py`
keeps it, and `unit/{path}/{module}_test.py` puts tests under `tests/unit/` with a suffix.

Every rule has a page under [docs/rules](docs/rules/), and `smelt.schema.json` gives editors
autocompletion for `smelt.yaml`.

## Python versions

Smelt runs on Python 3.12 to 3.14 and parses your code with the Python it runs on. Code
that uses newer syntax (3.14's `except A, B:` or t-strings, 3.13's type parameter defaults)
needs smelt on that version, e.g. `uvx -p 3.14 smelt check`; the syntax error says so when
`requires-python` or `.python-version` targets a newer Python.

## Optional type information

Role detection is nominal by default: a class is an adapter when it inherits a port. With

```yaml
analysis:
  types: pyright        # needs pyright on PATH; pyright_command overrides how it is run
```

Smelt also asks pyright whether a class satisfies a port structurally, so a duck-typed
adapter is found too. It is never required: without it, every rule still runs.

## Using Smelt with coding agents

Add this to your `AGENTS.md` or `CLAUDE.md`:

```md
Before implementing, run `smelt context <feature>` to see where code belongs.
After every change, run `smelt check --changed --format json`.
Do not finish while errors remain. Use `smelt explain <code>` when unsure.
```

Suggested loop: `smelt context <feature>` → edit → `smelt check --changed` → fix → tests →
pre-commit → CI (full check). Prefer the cheapest verification that gives sufficient
confidence: a rename needs smelt plus a type checker, new behavior needs one focused
regression test.

## pre-commit

```yaml
repos:
  - repo: https://github.com/mathisarends/smelt
    rev: v0.1.0
    hooks:
      - id: smelt
```

## GitHub Actions

CI always checks the whole repository, because cycles and transitive rules cannot be judged
from a diff alone.

```yaml
- uses: astral-sh/setup-uv@v6
- run: uvx --from smelt smelt check --format github
# optional: code scanning
- run: uvx --from smelt smelt check --format sarif > smelt.sarif || true
- uses: github/codeql-action/upload-sarif@v3
  with:
    sarif_file: smelt.sarif
```

## Development

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync                  # create .venv and install dev dependencies
uv run pre-commit install
```

Common commands:

```bash
uv run pytest                        # run tests
uv run pytest --cov                  # run tests with coverage
uv run ruff check --fix .            # lint
uv run ruff format .                 # format
uv run mypy                          # type-check
uv run pre-commit run --all-files    # run all hooks
uv run smelt check                   # smelt checks itself
uv run python scripts/generate.py    # refresh smelt.schema.json and docs/rules/
```

Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/).
