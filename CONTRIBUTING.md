# Extending Smelt

Use [the vision](docs/vision.md) as the scope and acceptance filter for proposed changes.

Development requires Python 3.12+ and Rust 1.98+ with rustfmt/clippy and a platform
linker. `uv sync --locked` builds the native `smelt` client with maturin; uv's cache
keys include the Cargo manifests and native Rust sources. `python -m smelt` keeps
the direct Python entry point for debugging. Installed
wheels include both the native client and the Python CLI. Keep Cargo.toml's
version equal to pyproject.toml's version when releasing.

Native interpreter launches use Python's safe-path mode so a project package named
`smelt` cannot replace the installed analyzer. Explicit PYTHONPATH overrides are
retained; relative PYTHONPATH/PYTHONHOME/PYTHONUSERBASE entries use the foreground
path to preserve their interpretation relative to the caller's directory.

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
| Source facts | `native/checker/analysis/`, `native/checker/files.rs` | Discovery, parsing and reusable indexes in the Rust core; never execute analyzed code |
| Policy resolution | `smelt/analysis/` | Contracts, coverage and workspace views over the core's file inventory, for `context`, `init` and validation |
| Policy data | `smelt/config/models.py` | Typed configuration, defaults and validation |
| Rule decision | `native/checker/rules/` | Compare facts with policy and emit violations |
| Rule metadata | `smelt/rules/<category>/<concept>.py` | Code, name, default severity, `RuleDoc` and guide text |
| Catalog | `smelt/rules/registry.py` | Explicitly register each rule once; generates `smelt/rules/catalog.json` for the core |
| Diagnostic content | The rule's `RuleDoc` | Summary, rationale, bad/good examples, repair advice and config paths |
| Execution and scope | `smelt/engine/` | Selection, severity, suppressions, debt and changed comparisons |
| Output and CLI | `smelt/diagnostics/`, `smelt/cli/` | Render findings and expose commands |
| Native check | `native/command/` | Run full and path-scoped `smelt check` without Python; leave every other invocation and every error to the Python CLI |
| Regression | Mirrored paths under `tests/` | Demonstrate the failure and protect valid neighbors |
| Generated reference | `docs/rules/`, `smelt.schema.json` | Generated from code; edit their sources instead |

Rule metadata depends on analysis/config/model/diagnostics, never engine or CLI.
The core runs every rule; the engine selects them, applies severity, suppressions
and debt, and renders the result. Share a source fact through a core analysis
index when several rules need it; don't make one rule call another or hide
policy decisions in parsing.
`native/command/` repeats the engine's selection, severity, debt and rendering,
so change it together with `smelt/engine/` and `smelt/diagnostics/`;
`tests/cli/commands/test_check.py` compares both reports.
Smelt checks these architecture boundaries with its own `smelt.yml`.

The repository also enables public-first ordering, explicit
visibility rules, static-helper visibility and test-quality reviews.
It checks private-symbol imports, redundant delegates/name aliases, deferred
annotations, explicit `StrEnum` values and docstrings on abstract and
`Protocol` methods, dataclasses that mutate their own fields after
construction, broad exception handlers without a stated reason and copied
module-level functions; shared syntax helpers live in `native/checker/analysis/bindings.rs`. Keep
serialized enum values stable.
Functions accept at most three positional parameters; pass later arguments by
keyword, including in tests and partial applications. Production dataclasses
set `slots=True` unless a `cached_property` or an outside base rules it out. Repeated `Literal` types
and anonymous tuple types remain hints: review their meaning before sharing an
alias or introducing a named record. Composite lookup keys, source spans and
Python version pairs can keep tuple semantics. Future
annotations follow `when-needed` using the supported Python minimum in package
metadata (currently 3.12), not the developer's interpreter. Keep imports needed for
forward references, TYPE_CHECKING or runtime annotation consumers.

Run `uv run smelt check --strict-coverage --fail-on warning` before committing.
CI and release validation use the same warning threshold; the staged hook checks
new findings against HEAD. Do not record debt or disable rules to adopt a change.
Prefer private helpers and test public behavior. A direct unit test of a parser
or rewriter can use a local `TST002` exception with a
specific reason; in multiline imports put it on the imported name. Entry points
used by excluded maintainer scripts retain a specific visibility suppression
naming the actual consumer. Keep `__all__` declarations in package `__init__.py`
files; do not add module export lists merely to silence visibility reviews.

The checker selects rules through generated `smelt/rules/catalog.json` metadata
and imports implementations only when needed. Register implementation paths in
`rules/registry.py`; rule classes remain the metadata source. Shared pure policy
predicates live in `rules/activation.py`. Refresh the catalog together with the
reference using `python scripts/generate.py`.

The official consumer skill lives in `smelt/skills/smelt/SKILL.md`. The same
generator bundles selected consumer guides and all rule pages under its
`references/`. Benchmarks, evaluations, proposals and maintainer guides stay in
the repository. Links to omitted material point to the repository documentation.
Edit the original docs and rule metadata, then regenerate:

```bash
uv run python scripts/generate.py
uv run python scripts/generate.py --check
```

The first command synchronizes both rule references, regenerates
`README.pypi.md` with links to the package version's tag, and removes obsolete
files. Edit `README.md` when changing the package description.
The second is read-only and fails on missing, changed or extra generated files.
CI and release validation run this drift check explicitly; do not hand-edit the
generated catalog, rule pages or skill references. Wheels and source
distributions ship this bundle. Distribution smoke tests install and
replace it outside the checkout. Keep its entrypoint aligned with CLI behavior;
`smelt skill install --force` refreshes installed copies after package upgrades.

Checks read project inputs through `config.source.SourceStore`. Use the store on
the analysis context or file index for reads, existence tests, discovery and path
resolution; direct `Path` reads can accidentally inspect unstaged files during a
Git-index check. Keep configured paths inside the captured repository.
Checks keep no cache; a rule reads the current complete project on every run.

Keep related rules together when they share a concept, as in `structure/headers.py`.
Give a growing concept its own module rather than expanding a catch-all file.
Normal checks stay read-only. A new rule does not need a fixer or a new index.

## One rule, one catalog entry

New Rule subclasses are off by default. Choose activation alongside the rule:

Fixed-pattern source/test reviews are selected directly by a problem name in
`rules`; do not add `review_*`, `require_*` or other duplicate boolean switches.
Keep real options separate from activation. Package `rules` use the same names
and severities, with closest-package precedence. Update the canonical class/name,
CLI explanation, findings, editor schema and documentation together. Stable rule
codes preserve diagnostic and debt identities across a documented name migration.

- Reserve `enabled_by_default = True` for reliable baseline diagnostics that need
  no opinionated policy. The baseline set is locked by catalog tests.
- Use `PolicyActivation(description, matches)` from `smelt.rules.activation` for
  an explicit architecture/package/test policy. Its predicate receives a typed
  SmeltConfig; keep the predicate pure and let mypy check field access. It must not
  inspect source files or infer architecture. Per-file checks still resolve closest
  package overrides normally.
- A source-convention option belongs on `PackagePolicy` in
  `smelt/config/models.py`, which `style` and package contracts share. Define
  it once, with `None` as its default, and put a built-in default in
  `PolicyDefaults` when selecting the rule without the option should still check
  something. If the option belongs to a single rule, activate the rule with
  `_option(...)` in `activation.py`; this limits the activation to modules where
  structure or a contract sets the option. Read the effective value with
  `ContractIndex.policy_for` or `ContractIndex.threshold`. Do not add a second
  project-wide field, and do not document an option as configuration-only when it
  belongs to one rule.
- Leave activation unset for a heuristic requiring a deliberate `rules` entry or
  CLI selection. Check applicability inside the rule even when selected with ALL.

Activation and severity are separate. Do not require users to configure both a
policy and an enable flag. Test missing policy, active policy, allow/disabled
overrides and important falsy values such as public: [] or a patch limit of zero.
Add a policy-activation regression when introducing a new policy. CLI prefixes must
also activate opt-in rules, while explicit off/ignore and strict coverage retain
their precedence. The generated catalog and CLI metadata derive activation from
the rule class; there is no second registration list of activation decisions.

Use `Rule`, a stable code, a unique `family/finding` name, the matching
`category`, explicit `requires` and a complete `RuleDoc`. A code is the family
prefix and the next free three-digit number in that family from the live
catalog, for example `MOD013`. Never reuse or renumber a code:

| Prefix | Family |
| --- | --- |
| ARC | architecture |
| IMP | imports |
| LAY | layout |
| VIS | visibility |
| MOD | modeling |
| TYP | typing |
| CPX | complexity |
| IDM | idioms |
| FWK | frameworks |
| TST | tests |
| MET | meta |

The directory still separates where a rule is evaluated: `dependencies/` for
import-graph rules, `structure/` for source rules, `testing/` for test rules and
the engine for meta rules. `smelt/diagnostics/legacy_codes.py` freezes the
retired `SMTnnn` codes for migration hints and debt fingerprints; new rules get
none, and Smelt does not accept them as input.

### Rule names

A rule name is `<family>/<finding>`. The family groups related findings for
selection, configuration and output. `--select modeling` explores a whole family.
YAML enables individual rules by name or code. The `category` attribute must equal
the family, and the catalog test enforces it:

| Family | Findings about |
| --- | --- |
| `architecture` | Layers, features, cycles, facades, composition roots and policy coverage |
| `imports` | Import style and import fan-out |
| `layout` | Module names, file names, docstrings and definition order |
| `visibility` | Public and private surface: underscores, `__all__`, test-only API |
| `modeling` | Classes, enums, dataclasses, exceptions and value naming |
| `typing` | Annotations and type expressions |
| `complexity` | Navigation depth, parameters, nesting and duplicated logic |
| `idioms` | Local Python idioms with one clearer spelling |
| `frameworks` | Library-specific conventions such as pydantic or dishka |
| `tests` | Test layout, isolation, assertions and determinism |
| `meta` | Suppressions and debt, evaluated after the other rules |

The finding describes what is wrong, not the command that would fix it. Use
kebab-case `<qualifier>-<subject>` and reuse the qualifier whose meaning matches:

| Qualifier | Meaning | Examples |
| --- | --- | --- |
| `redundant-` | Removing or inlining it keeps the behavior | `idioms/redundant-name-alias`, `typing/redundant-future-annotations` |
| `missing-` | Something required is absent | `modeling/missing-dataclass-option`, `architecture/missing-required-module` |
| `forbidden-` | A configured policy rejects something present | `layout/forbidden-module-name`, `tests/forbidden-mock` |
| `excess-` | A configured limit is exceeded | `complexity/excess-positional-parameters`, `tests/excess-patches` |
| `misplaced-` | It exists but belongs elsewhere | `layout/misplaced-private-constant`, `tests/misplaced-test` |
| `duplicate-`, `unused-`, `deprecated-`, `implicit-` | Their plain meaning | `typing/duplicate-literal-type`, `modeling/implicit-duration-unit` |
| `prefer-` | The code is valid; the rule recommends a named alternative | `imports/prefer-package-import`, `typing/prefer-deferred-annotations` |
| `-convention` (suffix) | One rule enforces whichever spelling a policy option selects | `layout/module-docstring-convention`, `modeling/str-enum-value-convention` |

Use `prefer-<alternative>` only when the reported construct is correct and the
rule recommends one specific replacement; name the replacement, not the reported
form. When no qualifier fits, use a noun phrase for the problem itself, such as
`architecture/import-cycle` or `complexity/excess-attribute-depth`. Do not start
findings with `no-` (use `forbidden-`), `unnecessary-` (use `redundant-`), or
`review-`, `require-` and `check-`; the catalog test rejects them.

Architecture findings can name the violated policy directly when it reads
naturally as a problem, for example `architecture/member-boundary` or
`architecture/composition-root-leak`. Advisory reviews can use a descriptive
noun phrase when the evidence permits several valid design choices.

One concept is one rule. When two candidate rules differ only in the spelling a
policy option selects (require/forbid, auto/explicit, prefix/suffix), write one
`-convention` or `forbidden-` rule that reads the option. When they share a
remedy but report different syntactic forms, write one rule and put the form in
the payload as `kind`, optionally narrowed by a list option such as
`style.call_argument_kinds`. Keep rules separate when their certainty differs,
for example a policy error and a heuristic hint, so severities stay meaningful.

Run `uv run smelt rules --format json` before allocating. Gaps are not promises
that a historical code is reusable; check history, and never repurpose a published
code or name. Retired names and merged codes live in `smelt/rules/renames.py` so
configuration errors can point to the replacement; add entries there and to
`docs/upgrading.md` whenever a published name or code is retired. Reconcile
allocations when concurrent PRs introduce rules.

1. Implement the decision in the core next to the closest concept and dispatch
   its code from the family's `rules` module. `rules/structure/naming.rs` is a
   small file-policy example; `rules/dependencies/boundaries.rs` shows import
   analysis; `rules/definition_order.rs` consumes a dedicated analysis index.
   Include a concrete location, useful `expected` policy data and an actionable
   hint. Keep output deterministic. Avoid a report for every alias when one
   architectural decision explains them all.
2. Describe the rule in a metadata class next to its Python concept, such as
   `smelt/rules/structure/naming.py`, with its `RuleDoc` and any `guide` text.
3. Register the class in `_IMPLEMENTATIONS` in `rules/registry.py` and regenerate,
   which rewrites the catalog the core reads. Keep abstract helper classes
   private; `build_rule_set()` is the public factory. Production registration
   stays explicit; test discovery detects forgotten rules.
4. Add policy to the existing typed configuration only if needed. Preserve package
   ownership, closest-policy overrides and conservative behavior for unknown
   Python versions. Expose new placement policy in `context` when an agent needs it
   before creating a file. Do not hardcode example-project package names.
5. Add the regression and a valid counterexample. Cover aliasing, relative imports,
   Python targets or package layouts when they affect the decision. Assert code,
   location and relevant policy payload rather than entire incidental messages.
6. Regenerate documentation/schema and run the checks below.

`RuleDoc.canonical` is one imperative line, without a final period, that tells
an author how to write conforming code; `smelt context --guide`, `explain` and
the rule page show it. A rule whose options change that instruction overrides
`Rule.guide(ctx, target)` and resolves the target's policy through the same
helper as `check`, returning `None` when the policy does not reach the target.

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

CI verifies registration completeness, unique codes/names, family/category agreement,
metadata, and generated-file freshness. Duplicate codes or names also fail when a
`RuleSet` is constructed, before selection can resolve an ambiguous catalog.

## Regressions from real projects

TODO comments must reference the issue or ticket that explains the unfinished
work. Ruff TD003 checks this convention in local checks and CI. For example:

```python
# TODO: support the additional layout after the policy is specified.
# https://github.com/mathisarends/smelt/issues/66
```

Use an actual issue for the work being deferred. This policy permits tracked
TODOs; it does not ban them or add a duplicate Smelt diagnostic.

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

Install the [uv](https://docs.astral.sh/uv/) and Rust toolchains, then run
`uv sync --locked` and `uv run prek install`. After editing, run
`uv run prek run --all-files`, inspect any hook fixes, and repeat until it passes.
The install also enables the Conventional Commits hook.

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
uv run smelt check --strict-coverage --fail-on warning --format json
uv run prek run --all-files
uv run pytest -n auto
```

Replace placeholders with real paths/codes. `-n auto` spreads the full suite
over all cores with pytest-xdist; focused runs do not need it. If source discovery, analysis shared
by multiple rules, metadata or machine output changes, include its consumers in
focused validation; CI runs the full supported Python/OS matrix.

Keep one reproducible behavior change per PR where practical. Explain the trigger,
old/new result, valid counterexample, default/configuration behavior, false-positive
boundaries and validation. Link related issues or prerequisite PRs. A new report
format or configuration default deserves explicit compatibility notes. The PR
template captures these decisions without duplicating the generated rule catalog.

The PR hook-latency job compares the complete pre-commit chain on five fresh
test, rule and core edits per revision and the strict full check after a test edit in disposable source installations. To
reproduce it locally with committed changes, run
`uv run python scripts/benchmark_staged_edits.py --base origin/main --output hook-times.json`.
Setup and the first edit per workload are outside the median. The job fails
when the candidate median exceeds the base by both 25% and 0.35 seconds.

The check-latency job times `smelt check` itself: the strict full check of the
repository and full, staged and changed checks of a generated layered project
with mirrored tests. It fails when a median exceeds the base by both 25% and 0.1
seconds. While iterating, time existing installations without rebuilding:
`uv run python scripts/benchmark_check.py --smelt base=<path> --smelt candidate=.venv/Scripts/smelt --output check-times.json`.
Set `SMELT_PROFILE=1` on any check to print how long each core phase and global
rule took to stderr; the output file also records these profiles.

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

1. Review changes since the last tag. Update the [upgrade guide](docs/upgrading.md),
   README examples and pinned hook/CI examples in
   [integrations](docs/integrations.md), including any report-format migration.
   Move `Unreleased` entries to a dated version section and update the changelog's
   comparison links. Bump matching versions in `pyproject.toml` and `Cargo.toml`,
   run `uv lock` and `cargo check` to refresh both lockfiles, and merge the release
   preparation into `main` after CI passes. Each release needs a new PyPI version.
2. From an up-to-date, clean `main` checkout, create and push the corresponding tag.
   For example, after merging version `0.8.0`:

   ```bash
   git switch main
   git pull --ff-only
   git tag v0.8.0
   git push origin v0.8.0
   ```

3. Follow the `Publish to PyPI` run in
   [GitHub Actions](https://github.com/mathisarends/smelt/actions/workflows/release.yml).
   The workflow requires the tagged commit to be reachable from `main` and the tag
   to equal `v` followed by the package version. It runs prek, pytest and
   Smelt, validates a source distribution outside the checkout, then builds native
   Linux x86_64/aarch64, Windows x86_64 and macOS x86_64/aarch64 wheels. CI also
   installs and exercises native wheels on Linux, Windows and macOS. The release
   workflow also smoke-tests its exact Linux x86_64, Windows x86_64 and macOS
   aarch64 wheels before uploading them for publication; the other targets are
   cross-compiled. A separate job downloads the validated release artifacts and publishes them
   with OIDC and the publishing action's default attestations enabled.

4. After PyPI publication succeeds, publish the GitHub Release for the same tag.
   Copy that version's dated changelog section into a release-notes file and
   replace relative documentation links with absolute links to `blob/v0.8.0/`.
   Check `gh release view v0.8.0` first; create a missing release with:

   ```bash
   gh release create v0.8.0 --verify-tag --title 'Smelt 0.8.0' --notes-file release-notes.md
   ```

   The publishing workflow currently uploads to PyPI only. A missing GitHub
   Release can be added for an existing published tag; new code needs a new
   version and tag. Do not move a published tag or reuse its PyPI version.

All Linux release wheels use `scripts/build_linux_wheel.sh`. The glibc wheels
build in the PyPA manylinux 2.28 container (x86_64) or the rust-cross manylinux
2.28 container (ARM64). The musl wheels build natively in the PyPA musllinux 1.2
containers. The script installs pinned `maturin[patchelf]` from PyPI, avoiding
the GitHub Release archive download used by maturin-action. CI builds and audits
all four targets with the same script, smoke-tests the native x86_64 glibc wheel
and installs both musl wheels on Alpine before release.
Keep the explicit compatibility audits and update the tool pin in the script
when adopting a new build-tool version.

Maintainers and coding agents with permission to push release tags can use this
procedure without local PyPI credentials. Ordinary branch pushes and pull requests
do not publish. Manual workflow runs must select an existing `v`-prefixed tag
(`gh workflow run release.yml --ref v0.8.0`); branch runs skip publishing.

If the upload fails because account configuration is missing, finish that setup
and rerun the failed publishing job so it reuses the validated build artifacts.
PyPI does not permit overwriting published files; use a new version for changed
package contents.
