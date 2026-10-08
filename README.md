# smelt

Architecture guardrails for Python, built for reviewing what coding agents change. Describe
your features and layers in `smelt.yaml`; `smelt check` reports two kinds of findings, each
with the exact location, what is allowed instead and how to fix it:

- **Boundaries:** an import that crosses a layer, a feature or the composition root.
- **Test mirroring:** a test file that does not sit at the mirrored path of a source module.

## Installation

Requires Python 3.12 or newer. Until this project has a PyPI release under its own
distribution name, install from this repository:

```bash
uv tool install git+https://github.com/mathisarends/smelt.git
smelt --version
```

From a local checkout, use `uv tool install .`. The PyPI package named `smelt` belongs
to a different project; `pip install smelt` and bare `uvx smelt` install that project.

## Usage

```bash
smelt init                           # draft smelt.yaml from the code (also uv workspaces)
smelt init --config other.yaml       # write elsewhere; only that file is checked and overwritten
smelt check                          # whole project
smelt check --changed                # only what the working tree introduced since HEAD
smelt check --changed --base origin/main --format json
smelt context user                   # what applies to a feature, module or path
smelt explain SMT101                 # rationale, examples and config keys of a rule
smelt rules                          # all rules with their default severity
smelt debt                           # record today's violations as known debt
smelt debt --prune                   # drop debt entries that were fixed
smelt config show                    # the resolved config
smelt config schema                  # the JSON schema of smelt.yaml
```

Exit codes: `0` clean, `1` violations at or above `--fail-on`, `2` config or usage error.
A config entry that points at nothing (a mistyped package, a missing test root) is a
config error, not a silently disabled rule.
Explicit check paths must exist and contain analyzed source or test files; unknown
`--select`/`--ignore` prefixes are usage errors. JSON includes the reporting scope, file
count and active rule codes. `--config PATH` works before or after a subcommand.
With `--format json`, an exit `2` prints a JSON document as well, so an agent needs no
text parser: `{"schema_version": 1, "status": "error", "error": {...}}` with `kind`
(`usage`, `config` or `analysis`), `message`, the rejected `input` (`option`, `value`)
and, for config errors, the `config` file with each issue's YAML `location`. That covers
argument errors (`--fail-on bogus`, an unknown flag) whenever `--format json` is on the
command line; an invalid `--format` value itself is reported as argparse text.

`--changed` checks the base commit (HEAD, or the merge-base with `--base`) with today's
config and reports only violations that are new, wherever they show up. Old violations in
an edited file stay quiet, a new import cycle does not.
Changing an import's comment, formatting or alias does not make its existing boundary
violation new; an additional identical violating import still counts as new.

## A DDD layout

```yaml
version: 1
project:
  root_packages: [backend, agent]
  source_roots: [backend/src, libs/agent/src]
  test_roots: [backend/tests, libs/agent/tests]

architecture:
  features: {root: backend.features}     # every child package is a feature
  shared: [backend.shared, backend.env]  # importable everywhere, imports no feature
  composition_root: [backend.main, backend.app]
  wiring: [backend.features.*.infrastructure.di]
  modules:                               # packages outside the features
    backend.platform: infrastructure
    agent: infrastructure
  layers:
    domain: {path: domain}
    application: {path: application, may_depend_on: [domain]}
    infrastructure: {path: infrastructure, may_depend_on: [domain, application]}
    presentation: {path: presentation, may_depend_on: [application, domain]}
  cross_feature:
    default: deny
    allow:
      - "application -> application"     # between all features
      - {from: session.presentation, to: auth.presentation}

tests:
  layout: mirror
  mirror: "{root}/{path}/test_{module}.py"
```

- **Layers** hold inside every feature: `domain` must not import `infrastructure`.
- **Features** may only import each other through `cross_feature.allow`. The string form
  allows a layer pair for all features, the object form one direction between two.
- **Central packages** such as a platform or a workspace library get a layer under
  `modules`. The layer rules then apply between them and the features (a feature's domain
  must not import `backend.platform`), and they must not import features.
- **Wiring** modules (whole-segment `*` allowed) keep their feature and layer and may cross
  layer boundaries. Feature wiring may also cross feature boundaries. Composition roots,
  other wiring modules and their package facades may import declared wiring. Central or
  shared wiring keeps the prohibition on importing features.
- Every module should belong somewhere: an unclassified module is exempt from all
  layer and feature boundary checks, so SMT305 warns about it. Composition-root/wiring
  import restrictions and configured cycle checks still apply.

`smelt init` infers most of this: features, layers, shared and settings modules, the
composition root including an app factory, wiring patterns, central packages by name and
the mirror pattern the existing tests follow. Review it before adopting its findings.
Its starter policy explicitly allows third-party packages and checks direct imports.
Review these decisions: to keep frameworks out of the core, set e.g.
`domain.third_party: {default: deny, allow: [pydantic]}` under `architecture.layers`.
Set `architecture.imports.transitive: true` to also detect layer dependencies through
re-exports, such as a domain importing an infrastructure provider from a feature's
`__init__.py`. Review the suggested `application -> application` pair too: it permits
that dependency between **every** feature.

`smelt context` describes the policy that applies to its target, including wiring and
composition-root exceptions, third-party defaults and direct/transitive coverage. It
accepts feature names, dotted module names and source paths, including planned `.py`
files under existing source packages. If a syntax error prevents analysis, the briefing
is still available and its violation counts are marked unavailable (`violations: null`
and `analysis_error` in JSON). Central and wiring modules have explicit JSON flags.

## Test mirroring

With `tests.layout: mirror` the path of a test decides: `tests/billing/test_invoice.py`
needs `app/billing/invoice.py`, and a package test `tests/billing/test_billing.py` needs
`app/billing/`. Not every module needs a test, but a test whose source is missing or
elsewhere is an error. The message names the fix where it can: the right directory, the
right file name (`test_session_infrastructure_repository.py` should be named
`test_repository.py`), a neighbouring module with a similar name, or a missing `{root}` in
the pattern.

`tests.mirror` sets the convention relative to the test root: the default
`{path}/test_{module}.py` drops the root package, `{root}/{path}/test_{module}.py` keeps it,
and `unit/{path}/{module}_test.py` puts tests under `tests/unit/` with a suffix. In a uv
workspace each test root mirrors the package of its own member. Deliberately unmirrored
tests go in `tests.unmirrored` (e.g. `["tests/e2e/**"]`); `tests.mirror_suffixes: true`
also allows `test_invoice_<topic>.py`.

Mirroring repeats file names: `auth/presentation/test_router.py` and
`user/presentation/test_router.py`. pytest's default import mode only tells them apart
inside packages, so without `__init__.py` in the test directories it stops with "import
file mismatch". Prefixing the names (`test_auth_presentation_router.py`) works around that
but breaks the mirror; set the import mode instead, which `smelt init` points out:

```toml
[tool.pytest.ini_options]
addopts = ["--import-mode=importlib"]
```

## Adopting it in an existing project

A first check often reports dozens of findings that come down to a few decisions. Each
import finding carries an `edge` in JSON (`"billing.application -> voice.domain"`; for
cycles, the cycle), and the text output ends with the edges behind several findings.
Settle each edge once: fix the code, or record an intended dependency as policy, e.g. a
`cross_feature.allow` entry. Accept what remains with a baseline:

`smelt debt` records today's violations in `.smelt/debt.json` and sets `debt:` in
`smelt.yaml`. `smelt check` then fails only on new violations, and SMT903 reports entries
that were fixed and can leave the file (`smelt debt --prune`).
Existing debt files remain readable. Run `smelt debt --prune` once before editing imports
covered by an older baseline to upgrade their fingerprints; pruning accepts no new debt.
New import fingerprints survive comments, aliases and formatting changes.

Silence a single finding inline, always with a reason:

```python
from gateway.infra.sql import Repo  # smelt: ignore[SMT101] -- migration tracked in #123
```

Every rule has a page under [docs/rules](docs/rules/), and `smelt.schema.json` gives editors
autocompletion and a description for every key of `smelt.yaml`.

## Python versions

Smelt runs on Python 3.12 to 3.14 and parses your code with the Python it runs on. Code
that uses newer syntax (3.14's `except A, B:` or t-strings, 3.13's type parameter defaults)
needs smelt on that version, e.g. install it with `uv tool install --python 3.14
git+https://github.com/mathisarends/smelt.git`; the syntax error says so when
`requires-python` or `.python-version` targets a newer Python.

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
    rev: v0.1.2
    hooks:
      - id: smelt
```

The hook checks the whole project when Python files or `smelt.yaml` / `smelt.yml`
change. Checking only changed filenames can miss violations in modules that import them.

## GitHub Actions

CI checks the whole repository; `--changed` is for the agent loop.

```yaml
- uses: astral-sh/setup-uv@v6
- run: uvx --from git+https://github.com/mathisarends/smelt.git smelt check --format github
# optional: code scanning
- run: uvx --from git+https://github.com/mathisarends/smelt.git smelt check --format sarif > smelt.sarif || true
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
