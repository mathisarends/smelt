# Extending Smelt

Use [the vision](vision.md) as the scope and acceptance filter for proposed changes.

Development requires Python 3.12+ and Rust 1.88+ with rustfmt/clippy and a platform
linker. `uv sync --locked` builds the native `smelt` client with maturin; uv's cache
keys include the Cargo manifests and native Rust sources. `python -m smelt` keeps
the direct Python entry point for debugging and uncached comparisons. Installed
wheels include both the native client and the Python engine. Keep Cargo.toml's
version equal to pyproject.toml's version when releasing.

The native client reuses a worker for read-only local checks. Workers are scoped
to the interpreter, environment and invocation directory, serialize requests and
expire after 120 idle seconds. Every request captures Git/configuration again.
Changes to analyzer sources/dependency identity restart the worker. `--fix`, other
commands and CI run directly; `SMELT_DAEMON=0` disables workers and
`SMELT_DAEMON=1` enables them explicitly in CI. Transport/startup failures fall
back to the foreground checker. Tests must cover fresh-client output/exit codes,
partial staging, restart, concurrent startup and independent `--no-cache` results.

An agent mistake is evidence for a regression, not automatically a new rule. Start
with the smallest reproducible example, the intended result and a nearby valid
counterexample. Fix an existing rule when its documented contract already covers
the case. Add a rule when the decision has a distinct meaning and can generalize
beyond the repository that exposed it. Use a configurable package policy for team
preferences. Consider whether an existing Ruff/type-checker check already covers
the same decision before adding another finding.

## Where a change belongs

| Concern | Location | Responsibility |
| --- | --- | --- |
| Source facts | `smelt/analysis/` | Discovery, parsing and reusable indexes; never execute analyzed code |
| Policy data | `smelt/config/models.py` | Typed configuration, defaults and validation |
| Rule decision | `smelt/rules/<category>/<concept>.py` | Compare facts with policy and emit violations |
| Catalog | `smelt/rules/registry.py` | Explicitly register each rule once |
| Diagnostic content | The rule's `RuleDoc` | Summary, rationale, bad/good examples, repair advice and config paths |
| Execution and scope | `smelt/engine/` | Selection, severity, suppressions, debt and changed comparisons |
| Output and CLI | `smelt/diagnostics/`, `smelt/cli/` | Render findings and expose commands |
| Regression | Mirrored paths under `tests/` | Demonstrate the failure and protect valid neighbors |
| Generated reference | `docs/rules/`, `smelt.schema.json` | Generated from code; edit their sources instead |

Rules depend on analysis/config/model/diagnostics, never engine or CLI. The engine
composes rules. Share a source fact through an analysis index when several rules
need it; don't make one rule call another or hide policy decisions in parsing.
Smelt checks these architecture boundaries with its own `smelt.yaml`.

The checker selects rules through generated `smelt/rules/catalog.json` metadata
and imports implementations only when needed. Register implementation paths in
`rules/registry.py`; rule classes remain the metadata source. Shared pure policy
predicates live in `rules/activation.py`. Refresh the catalog together with the
reference using `python scripts/generate.py`.

Checks read project inputs through `config.source.SourceStore`. Use the store on
the analysis context or file index for reads, existence tests, discovery and path
resolution; direct `Path` reads can accidentally inspect unstaged files during a
Git-index check. Keep configured paths inside the captured repository. Cached
facts must include content identity and any module, inventory, metadata or policy
inputs that affect their meaning. Verify results against `--no-cache` after edits,
deletions, re-exports and fixture changes. Mark a rule `file_local` only when its
source contents, resolved policy and Python target fully determine its decision.
`test_local` rules additionally receive resolved constructor, fixture and module
inventory inputs. `member_local` rules consume resolved member facts, including
current external-use and inheritance evidence. Global rules must retain their
cross-file dependencies.

Member-local decisions use a persisted dependency index. File summaries contribute
reference/attribute/inheritance counts and record both positive and missing
namespace dependencies. Changed contributions invalidate reverse readers; equal
contributions do not propagate to unrelated definitions. Keep the uncached member
analysis independent so differential tests can detect invalidation errors.

Keep related rules together when they share a concept, as in `structure/headers.py`.
Give a growing concept its own module rather than expanding a catch-all file.
Normal checks stay read-only. A new rule does not need a fixer or a new index.

## One rule, one catalog entry

New BaseRule subclasses are off by default. Choose activation alongside the rule:

Fixed-pattern source/test reviews are selected directly by a problem name in
`rules`; do not add `review_*`, `require_*` or other duplicate boolean switches.
Keep real options separate from activation. Package `rules` use the same names
and severities, with closest-package precedence. Update the canonical class/name,
CLI explanation, findings, editor schema and documentation together. Stable SMT
codes preserve diagnostic and debt identities across a documented name migration.

- Reserve `enabled_by_default = True` for reliable baseline diagnostics that need
  no opinionated policy. The baseline set is locked by catalog tests.
- Use `PolicyActivation(description, matches)` from `smelt.rules.activation` for
  an explicit architecture/package/test policy. Its predicate receives a typed
  SmeltConfig; keep the predicate pure and let mypy check field access. It must not
  inspect source files or infer architecture. Per-file checks still resolve closest
  package overrides normally.
- Leave activation unset for a heuristic requiring a deliberate `rules` entry or
  CLI selection. Check applicability inside the rule even when selected with ALL.

Activation and severity are separate. Do not require users to configure both a
policy and an enable flag. Test missing policy, active policy, allow/disabled
overrides and important falsy values such as public: [] or a patch limit of zero.
Add a policy-activation regression when introducing a new policy. CLI prefixes must
also activate opt-in rules, while explicit off/ignore and strict coverage retain
their precedence. The generated catalog and CLI metadata derive activation from
the rule class; there is no second registration list of activation decisions.

Use `BaseRule`, a stable code, a unique kebab-case name, a category, explicit
`requires` and a complete `RuleDoc`. Allocate an unused code from the live catalog:

| Range | Category |
| --- | --- |
| SMT1xx | dependencies |
| SMT3xx | structure |
| SMT4xx | tests (directory `testing/`) |
| SMT9xx | meta, evaluated by engine post-processing where appropriate |

Run `uv run smelt rules --format json` before allocating. Gaps are not promises
that a historical code is reusable; check history, and never repurpose a published
code or name. Reconcile allocations when concurrent PRs introduce rules.

1. Implement the decision next to the closest concept. `structure/naming.py` is a
   small file-policy example; `dependencies/interfaces.py` shows richer import
   analysis; `structure/ordering.py` consumes a dedicated analysis index.
2. Use `self.violation(...)` so metadata and documentation links stay consistent.
   Include a concrete location, useful `expected` policy data and an actionable
   hint. Keep output deterministic. Avoid a report for every alias when one
   architectural decision explains them all.
3. Register the class in `builtin_rules()`. Keep abstract helper classes private.
   Production registration stays explicit; test discovery detects forgotten rules.
4. Add policy to the existing typed configuration only if needed. Preserve package
   ownership, closest-policy overrides and conservative behavior for unknown
   Python versions. Expose new placement policy in `context` when an agent needs it
   before creating a file. Do not hardcode example-project package names.
5. Add the regression and a valid counterexample. Cover aliasing, relative imports,
   Python targets or package layouts when they affect the decision. Assert code,
   location and relevant policy payload rather than entire incidental messages.
6. Regenerate documentation/schema and run the checks below.

`RuleDoc` is the documentation source. `engine.check.rule_meta()` supplies the CLI
and report metadata; there is no separate hand-maintained catalog manifest.
`rules --format json` exposes analysis requirements and config paths;
`explain <code> --format json` adds rationale, examples and repair advice. A rule
being active by default does not imply that its optional package policy is enabled.

`engine.schema.config_json_schema()` enriches the model schema from the live rule
catalog, including completion names, severities and bad/good hover examples. Keep
config models independent of the rule registry. `init` exports the schema locally;
the checker never reads that editor artifact. Verify schema errors and the CLI
against the same API, including aliases, package overrides and removed keys.

CI verifies registration completeness, unique codes/names, code/category agreement,
metadata, and generated-file freshness. Duplicate codes or names also fail when a
`RuleSet` is constructed, before selection can resolve an ambiguous catalog.

## Regressions from real projects

Reduce the example to synthetic files using `tests.helpers.write_project`, or a
small layout under `tests/fixtures/`. Keep the behavior that exposed the defect,
including workspace roots or namespace portions when relevant. Tests must run
without private repositories, network access, credentials or absolute user paths.
Use real repositories for a read-only evaluation and report the config used,
findings reviewed, known uncertainty and whether source files changed.

Exercise the actual `check()` pipeline for policy behavior. Add CLI/output tests
only when the public interface changes. Check rule selection, suppression, debt
and changed comparisons if the change affects their semantics. Aggregate findings
need stable semantic identities: an old accepted finding must not hide a new edge.
Fixers additionally need explicit opt-in, source-preservation tests, idempotence
and refusal tests for uncertain execution or concurrent edits.

## Verification and PR scope

Update [CHANGELOG.md](CHANGELOG.md) under `Unreleased` for user-visible features,
fixes and behavior changes. State what an agent can do differently, required
policy or opt-in settings, compatibility changes and relevant limits. Keep entries
concise and link to detailed rule documentation rather than duplicating it. Before
publishing a release, move its entries into a dated version section and update the
comparison links.

```bash
uv sync --locked
uv run pytest tests/rules/<category>/test_<concept>.py
uv run python scripts/generate.py
uv run pytest tests/rules/test_registry.py tests/test_docs.py
uv run smelt explain <code> --format json
uv run smelt check --strict --format json
uv run pre-commit run --all-files
uv run pytest
```

Replace placeholders with real paths/codes. If source discovery, analysis shared
by multiple rules, metadata or machine output changes, include its consumers in
focused validation; CI runs the full supported Python/OS matrix.

Keep one reproducible behavior change per PR where practical. Explain the trigger,
old/new result, valid counterexample, default/configuration behavior, false-positive
boundaries and validation. Link related issues or prerequisite PRs. A new report
format or configuration default deserves explicit compatibility notes. The PR
template captures these decisions without duplicating the generated rule catalog.

## Publishing a release

The `.github/workflows/release.yml` workflow publishes `smelt-cli` to PyPI when a
`v`-prefixed version tag is pushed. It uses
[PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/using-a-publisher/)
through GitHub Actions OIDC; no PyPI API token or repository secret is needed.

### One-time account configuration

In the PyPI project's
[Publishing settings](https://pypi.org/manage/project/smelt-cli/settings/publishing/),
add a GitHub Trusted Publisher with these exact values:

| Field | Value |
| --- | --- |
| PyPI project | `smelt-cli` |
| Repository owner | `mathisarends` |
| Repository name | `smelt` |
| Workflow filename | `release.yml` |
| Environment name | `pypi` |

The workflow filename is the basename, without `.github/workflows/`. If the PyPI
project does not exist yet, register the same values as a
[pending publisher](https://pypi.org/manage/account/publishing/) instead.

In the repository's
[Environments settings](https://github.com/mathisarends/smelt/settings/environments),
create an environment named `pypi` and allow deployments from tags matching `v*`.
Required reviewers are optional; leave them unset if maintainers and coding agents
should be able to publish without a separate approval for each release. The PyPI
environment name must match the workflow exactly.

### Release procedure

1. Bump the version in `pyproject.toml`, run `uv lock`, and merge the version change
   and release workflow into `main` after CI passes. Each release needs a new PyPI
   version; this setup does not bump or publish the existing `0.1.3` automatically.
2. From an up-to-date, clean `main` checkout, create and push the corresponding tag.
   For example, after merging version `0.1.4`:

   ```bash
   git switch main
   git pull --ff-only
   git tag v0.1.4
   git push origin v0.1.4
   ```

3. Follow the `Publish to PyPI` run in
   [GitHub Actions](https://github.com/mathisarends/smelt/actions/workflows/release.yml).
   The workflow requires the tagged commit to be reachable from `main` and the tag
   to equal `v` followed by the package version. It runs pre-commit, pytest and
   Smelt, builds both distributions, and installs and exercises each outside the
   checkout. A separate job downloads those exact artifacts and publishes them
   with OIDC and the publishing action's default attestations enabled.

Maintainers and coding agents with permission to push release tags can use this
procedure without local PyPI credentials. Ordinary branch pushes and pull requests
do not publish. Manual workflow runs must select an existing `v`-prefixed tag
(`gh workflow run release.yml --ref v0.1.4`); branch runs skip publishing.

If the upload fails because account configuration is missing, finish that setup
and rerun the failed publishing job so it reuses the validated build artifacts.
PyPI does not permit overwriting published files; use a new version for changed
package contents.
