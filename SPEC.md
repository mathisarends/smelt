# Smelt: Specification

> **Static guardrails for Python codebases maintained by humans and coding agents.**

Ruff checks whether your Python is clean. Pyright checks whether types line up. pytest checks
whether behavior is correct. **Smelt checks whether the code fits the intended architecture**,
and reports problems in a way that a developer or a coding agent can act on right away.

A single `smelt.yaml` describes the architecture model: features, layers, roles, structure
and test policy. Smelt derives the concrete checks from that model, so you don't write
25 independent lint contracts. The goal is to **shrink the solution space for agents**: every
kind of concept has a canonical place, every dependency has an allowed direction, and every
violation says what is expected instead.

---

## 0. Principles

- **Describe, don't prescribe.** Smelt has no magic names. `domain`, `core`, `usecases`, `infra`
  mean only what the config says.
- **Model over contracts.** The config describes *concepts* (feature, layer, role, composition
  root). Smelt derives import, structure and test checks from them.
- **Don't rebuild existing tools.** No style linting (Ruff), no type checking (Pyright/mypy),
  no custom import parser. Import resolution comes from **Grimp** (used directly, not through
  import-linter). Smelt owns the semantics, the diagnostics and the UX.
- **Agent-agnostic.** CLI + JSON only. Codex, Claude Code, custom harnesses and humans use the same interface.
- **Diagnostics are the product.** Every violation includes the location, what was violated,
  what is expected, and a concrete structural hint.
- **Errors block, warnings inform, hints advise.** Agents must not finish while errors remain.
  Warnings and hints are architectural feedback, not a mandate to restructure code.
- **Deterministic fixes only.** `smelt fix` performs only transformations whose correctness
  Smelt can guarantee. Smelt never uses an LLM to fix code.

## 1. Non-goals

- Python style, formatting, unused imports, generic naming (Ruff's job).
- Type inference or type checking (Pyright's job; Smelt may *consume* type info later, see §9).
- Coverage metrics or "every source file needs a test file" rules.
- Controlling *when* an agent writes tests. That is the harness's job (see §11).
- Agent-specific integrations or plugins.

## 2. Tech & repo constraints

- Python `>=3.14`, package in `smelt/` (not `src/`), `mypy --strict`, Ruff and pytest config as in `pyproject.toml`.
- Runtime dependencies: `grimp` (import graph), `pyyaml` (config), `pydantic` v2 (config models,
  JSON schema generation).
- CLI: stdlib `argparse`, plain ANSI colors only on a TTY (`--no-color`, respect `NO_COLOR`).
- Cross-platform: always print POSIX-style repo-relative paths.

Suggested internal layout:

```text
smelt/
├── cli/            # argparse commands, exit codes
├── config/         # yaml loading, pydantic models, validation, json schema
├── model/          # ArchitectureModel: features, layers, roles, module classification
├── analysis/       # AnalysisContext + indexes: files, imports (grimp), syntax (ast)
├── rules/
│   ├── dependencies/   # SMT1xx
│   ├── code/           # SMT2xx
│   ├── structure/      # SMT3xx
│   ├── tests/          # SMT4xx
│   └── meta/           # SMT9xx
├── diagnostics/    # Violation, Severity, suppressions, baseline, renderers (text/json/sarif/github)
└── fix/            # deterministic fixers
```

---

## 3. Configuration (`smelt.yaml`)

- [ ] Discover `smelt.yaml` by walking up from the CWD. `--config PATH` overrides it.
- [ ] Load into immutable, typed models. Errors include the YAML path and a suggestion:
      `architecture.layers.application.may_depend_on[1]: unknown layer "infra" (did you mean "infrastructure"?)`.
- [ ] `smelt config show` prints the resolved config with all defaults filled in.
- [ ] Generate `smelt.schema.json` from the models and publish it for editor autocompletion.
- [ ] `version: 1` is required. Unknown keys are errors.

### 3.1 Full example

```yaml
version: 1

project:
  root_packages: [gateway]
  source_roots: [src]
  test_roots: [tests]
  exclude: ["**/migrations/**", "**/generated/**"]

architecture:
  features:
    root: gateway.features            # every direct child package is a feature
    # alternative: pattern: "gateway.{feature}"  (no `features/` level)

  shared: [gateway.shared]            # importable by all features; must not import features
  composition_root: [gateway.bootstrap, gateway.main]

  layers:
    domain:
      path: domain
      may_depend_on: []
      third_party:
        default: deny                 # everything external is forbidden ...
        allow: [pydantic]             # ... except these
      forbid_bases: [pydantic.BaseModel, sqlalchemy.orm.DeclarativeBase]
    application:
      path: application
      may_depend_on: [domain]
      third_party:
        default: allow                # everything external is allowed ...
        deny: [fastapi, sqlalchemy, dishka]   # ... except these
    infrastructure:
      path: infra
      may_depend_on: [domain, application]
    presentation:
      path: api
      may_depend_on: [application]

  cross_feature:
    default: deny
    allow: ["application -> application"]    # layer-to-layer pairs allowed across features

  di_frameworks: [dishka]             # only importable/usable in the composition root

  imports:
    type_checking: include            # include | ignore  (`if TYPE_CHECKING:` imports)
    transitive: false                 # also report indirect layer violations with the chain

roles:                                # optional; gives concepts a canonical home
  port:
    detect: { base: typing.Protocol }
    layers: [application]
    file: ports.py
  adapter:
    detect: { implements: port }
    layers: [infrastructure]

structure:
  forbidden_names: [utils, helpers]
  crowded_threshold: 8                # advisory only

tests:
  layout: feature                     # mirror | feature | none
  pattern: "tests/{feature}"
  patching:
    allow: [external, environment, stdlib]
    forbid: [domain, application, private]
  private_access: forbid
  mocks:
    max_per_test: 3
    forbid_first_party: [domain, application]
  interaction_assertions: warning     # call_count / assert_called_* on first-party objects
  bloat:
    ratio: 5                          # test LOC added / production LOC changed
    min_test_loc: 100

rules:                                # severity overrides by code: error | warning | hint | off
  SMT206: warning                     # enable an opt-in rule
  SMT407: off

ignore:
  - rule: SMT101
    modules: ["gateway.legacy.**"]
    reason: "legacy module, migration tracked in #123"

baseline: .smelt/baseline.json
plugins: [gateway_tools.smelt_rules]
```

### 3.2 Semantics to implement

- [ ] **Features:** `root` (children are features) or `pattern` with a `{feature}` placeholder.
      Features are optional; without them, layers apply to the whole root package.
- [ ] **Layers:** `path` is matched as a module-path segment below the feature (or the root).
      `may_depend_on` is the full allowlist. A layer can always import itself within the same feature.
- [ ] **`third_party`:**
  - The short forms `third_party: allow | deny` expand to `{default: allow}` / `{default: deny}`. The default is `allow`.
  - `allow` is valid only with `default: deny`, and `deny` only with `default: allow`. Anything else is a config error.
  - Entries match the package and all submodules (`pydantic` matches `pydantic.fields`). Submodule entries (`sqlalchemy.orm`) are allowed.
  - The stdlib (`sys.stdlib_module_names`) never counts as third party.
- [ ] **`shared` / `composition_root`:** special module sets outside the feature/layer grid with their own rules (§5).
- [ ] **Roles** (optional): `detect` has exactly one condition. `base` means the class inherits
      from that base, resolved through imports. `implements` means explicit inheritance from a class
      with that role; structural Protocol matching requires the type index (§9). `file` is the
      expected module name within the role's layer.
- [ ] **Structure:** a package inside a feature that is not a declared layer is always reported
      (derived from the model, no config needed). `forbidden_names` and `crowded_threshold` are the only knobs.
- [ ] **Severity overrides:** keyed by rule code only. Path-scoped exceptions go through `ignore`.
- [ ] **Validation:** unknown layers or roles, duplicate layer paths, cyclic `may_depend_on` (warn),
      patterns without placeholders, overlapping `shared`/feature modules.

---

## 4. Analysis engine

- [ ] **Pipeline:** load config → discover files → classify modules → build the indexes
      the selected rules need → run rules → apply filters (paths, `--changed`, suppressions,
      ignores, baseline) → render.
- [ ] **`ArchitectureModel`:** a pure data structure mapping each module to
      `(feature | shared | composition_root | unclassified, layer | None, roles)`.
      It does not depend on Grimp or the CLI.
- [ ] **`AnalysisContext`** exposes indexes:
  - `files`: `FileIndex` (source/test files, package dirs, sizes, hashes)
  - `imports`: `ImportIndex`, a wrapper around Grimp `ImportGraph` with line-level import details
    and `TYPE_CHECKING` awareness
  - `syntax`: `SyntaxIndex` (parsed ASTs, classes, functions, call sites, decorators, name resolution through imports)
  - `types`: `TypeIndex | None` (reserved, §9)
- [ ] **Rule API:**
  ```python
  class Rule(Protocol):
      code: str  # "SMT101"
      name: str  # "layer-boundary"
      category: Category  # dependencies | code | structure | tests | meta
      default_severity: Severity
      requires: frozenset[Index]  # e.g. {Index.IMPORTS}
      fixable: bool

      def check(self, ctx: AnalysisContext) -> Iterable[Violation]: ...
      def explain(
          self,
      ) -> RuleDoc: ...  # summary, rationale, bad/good example, fix guidance
  ```
- [ ] **`Violation`:** `code`, `rule`, `severity`, `message`, `path`, `line`, `column`,
      `end_line`, `end_column` (for carets), `source_module`, `target_module`, `import_chain`,
      `feature`, `layer`, `expected` (e.g. allowed dependencies or expected path),
      `hint`, `fix` (optional deterministic edit), `docs_url`.
- [ ] **Registry:** built-in rules plus plugins (`plugins:` modules and the `smelt.rules` entry-point group).
      A custom rule should take about 30 lines.
- [ ] **Deduplication:** if several rules fire on the same import or line with the same root
      cause, report the most specific one (e.g. SMT101 wins over SMT202).
- [ ] **Suppressions:** inline `# smelt: ignore[SMT101] -- reason` on the offending line, or
      `# smelt: ignore-file[SMT3]` at the top of the file. A reason is required by default
      (`suppressions.require_reason: true`).
- [ ] **Baseline:** `smelt baseline` writes current violations (fingerprinted by code, path,
      source/target and a normalized snippet, not by line number) so legacy repos can adopt
      Smelt incrementally. Only new violations fail.
- [ ] **Changed mode:** the full graph is always built; only the reporting is
      scoped. Graph-level rules (cycles, transitive) still evaluate globally but only report
      violations that involve a changed module.

---

## 5. Rule catalog

Code ranges: `SMT1xx` dependencies · `SMT2xx` code semantics · `SMT3xx` structure & naming ·
`SMT4xx` tests · `SMT9xx` meta. Default severity in brackets.

### SMT1xx: Dependencies (import graph)

- [ ] `SMT101 layer-boundary` [error]: a layer imports a layer outside `may_depend_on`. With
      `transitive: true`, indirect chains are reported with the full path.
- [ ] `SMT102 cross-feature-import` [error]: feature A imports feature B outside the `cross_feature.allow` pairs.
- [ ] `SMT103 third-party-denied` [error]: an external import that `third_party` does not allow.
      The message lists what is allowed (`domain allows only: pydantic`).
- [ ] `SMT104 import-cycle` [error]: cycles between features, between layers, or between
      sibling modules (configurable scope). The message shows the shortest cycle.
- [ ] `SMT105 shared-imports-feature` [error]: a `shared` module imports a feature module.
- [ ] `SMT106 composition-root-leak` [error]: a `di_frameworks` package is imported outside
      the composition root, or a non-root module imports the composition root.

### SMT2xx: Code semantics (AST)

- [ ] `SMT201 concrete-construction` [error]: a class with role `adapter` is instantiated outside
      its own layer or the composition root. The rule is inactive when no `adapter` role is configured.
      Hint: *"Depend on the port and wire the implementation in the composition root."*
- [ ] `SMT202 concrete-dependency` [warning]: an application-layer constructor or function
      parameter is annotated with a concrete adapter class instead of a port. This catches cases
      that bypass SMT101 via `TYPE_CHECKING` or `shared`.
- [ ] `SMT203 forbidden-base-class` [error]: a class in a layer inherits from a `forbid_bases`
      entry (e.g. pydantic models in the domain, even if `pydantic` is allowed for imports).
- [ ] `SMT204 misplaced-role` [error]: a detected role lives in a layer outside `roles.<role>.layers`.
      Expected location: `voice/application/ports.py`.
- [ ] `SMT205 container-usage` [error]: DI container resolution calls (`container.get(...)`) outside the composition root.
- [ ] `SMT206 self-class-reference` [off, opt-in]: inside class `Foo`, a method constructs `Foo(...)`
      instead of `type(self)(...)`/`cls(...)`, or annotates `-> "Foo"` instead of `Self`.

### SMT3xx: Structure & naming (filesystem + model)

- [ ] `SMT301 unknown-layer` [error]: a feature contains a package that is not a declared layer.
- [ ] `SMT302 forbidden-package-name` [error]: a package or module name is in `forbidden_names` (`utils/`, `helpers/`).
- [ ] `SMT303 role-file` [error]: a role lives in a different module than its `file`
      (`VoiceSessionRepository(Protocol)` in `voice/application/session.py`, expected `voice/application/ports.py`).
- [ ] `SMT304 crowded-package` [hint]: a package exceeds `crowded_threshold` modules
      (`voice/application has 13 modules; consider grouping related modules`). This is advisory only.
- [ ] `SMT305 unclassified-module` [hint]: a module maps to no feature, layer, shared or composition root.

### SMT4xx: Tests

Philosophy: *tests are not automatically good just because they exist.* Smelt flags tests that
couple to implementation details or create maintenance burden. It never demands 1:1 mirroring.

- [ ] `SMT401 test-location` [error]: a test file violates `tests.layout`.
      `mirror` expects the source path mirrored under the test root. `feature` expects `tests/{feature}/**`.
      The message shows the expected path.
- [ ] `SMT402 patches-internal` [error]: a `monkeypatch.setattr` / `mock.patch` target resolves to a
      forbidden category (a first-party domain/application symbol or a private name).
      `monkeypatch.setenv`, stdlib (`time.time`) and third-party boundaries (`requests.get`) stay allowed.
- [ ] `SMT403 private-access` [error]: a test reads or writes `_private` attributes of first-party
      objects (`service._repository = Fake()`) or imports `_private` names.
- [ ] `SMT404 mocks-first-party` [warning]: `Mock`/`MagicMock`/`create_autospec` of first-party
      classes in `mocks.forbid_first_party`. Hint: use a fake implementing the port.
- [ ] `SMT405 too-many-mocks` [warning]: more than `mocks.max_per_test` mocks or patches in one test (fixtures included).
- [ ] `SMT406 interaction-assertion` [warning]: asserts on `call_count` / `assert_called_*` of
      first-party collaborators instead of results, state or events.
- [ ] `SMT407 test-bloat` [hint, `--changed` only]: test LOC added compared with production LOC changed
      exceeds `bloat.ratio`. The summary shows LOC, mocks, patches and assertions.
- [ ] `SMT408 test-only-api` [hint]: a public production symbol is referenced only from tests.

### SMT9xx: Meta

- [ ] `SMT901 unused-suppression` [warning]: an inline ignore that suppresses nothing.
- [ ] `SMT902 suppression-without-reason` [error]: an inline ignore without `-- reason` (when required).
- [ ] `SMT903 stale-baseline` [warning]: a baseline entry that no longer matches. `smelt baseline --prune` removes these.

---

## 6. CLI

Exit codes for all commands: `0` success, `1` violations at or above `--fail-on`, `2` config, usage or internal error.

- [ ] `smelt check [PATHS...]`
  - `--changed [--base REF]`: report only for files changed against `REF` (default: staged, unstaged
    and untracked; `--base origin/main` for CI on a PR).
  - `--format text|json|sarif|github` (`github` emits workflow annotations).
  - `--fail-on error|warning` (default `error`), `--select SMT1,SMT203`, `--ignore SMT304`.
  - `--show-hints` (hints are collapsed to a count by default), `--no-color`.
  - When paths are passed (pre-commit), results are restricted to those files.
- [ ] `smelt explain CODE|NAME`: rationale, bad and good example, fix guidance, config knobs.
- [ ] `smelt rules [--format json]`: lists all rules with code, name, category, severity and fixable flag.
- [ ] `smelt context [FEATURE|PATH] [--format text|json]`: a compact, agent-readable briefing that
      replaces pages of architecture docs in the prompt:
  ```text
  Feature: voice   (gateway.features.voice)

  Layers:
    domain          → (nothing)        third-party: only pydantic
    application     → domain           third-party: not fastapi, sqlalchemy, dishka
    infrastructure  → domain, application
    presentation    → application
  Cross-feature:    only application → application
  Composition root: gateway.bootstrap (DI: dishka)

  Where things go:
    port      → voice/application/ports.py
    adapter   → voice/infra/   (construct only in composition root)

  Tests: tests/voice/, behavior-oriented, no patching of domain/application internals,
         no private access, ≤3 mocks per test, no 1:1 file mirroring required

  Current violations: 2 errors (SMT101, SMT402)
  ```
- [ ] `smelt where ROLE [--feature NAME]`: prints the canonical path for a new
      concept (`smelt where port --feature voice` → `gateway/features/voice/application/ports.py`).
- [ ] `smelt inspect --format json`: a machine-readable **architecture map** of the whole repo:
      features, layers, modules per layer, detected roles, aggregated feature→feature and
      layer→layer edges with counts, and violation counts per node.
- [ ] `smelt init`: writes a commented `smelt.yaml`. When possible, infers features and
      layers from the existing tree and reports how many violations the inferred config yields.
- [ ] `smelt baseline [--prune]`: writes or updates the baseline file.
- [ ] `smelt fix [CODES...] [--dry-run]`: applies deterministic fixes and prints a unified diff with `--dry-run`:
  - move test files to their expected location (`SMT401`)
  - remove unused suppressions (`SMT901`)
  - move a role class into its `file` (`SMT303`) **only if** every import of it can be rewritten statically
- [ ] `smelt verify`: runs a configured verification stack in order and prints one summary (text/json):
  ```yaml
  verify:
    - { name: ruff,    run: "ruff check ." }
    - { name: smelt,   run: "smelt check" }
    - { name: pyright, run: "pyright" }
    - { name: pytest,  run: "pytest -q" }
  ```
  It only orchestrates; it never re-implements these tools.
- [ ] `smelt config show` and `smelt config schema`: print the resolved config and the JSON schema.
- [ ] Entry point: `[project.scripts] smelt = "smelt.cli:main"`, plus `python -m smelt`.

## 7. Output

- [ ] **Text** (grouped by file, carets under the offending span):
  ```text
  src/gateway/features/voice/application/session.py:21:5  SMT101 layer-boundary  [error]
    application → infrastructure is not allowed
      from gateway.features.voice.infra.sql import SqlVoiceSessionRepository
                                                   ^^^^^^^^^^^^^^^^^^^^^^^^^^
    Allowed: application → domain
    Hint: introduce or reuse a port in voice/application/ports.py and wire
          SqlVoiceSessionRepository in gateway.bootstrap.

  ✗ 1 error · 0 warnings · 3 hints (use --show-hints) · 412 modules
  ```
- [ ] Summary line by category when clean: `✓ dependencies ✓ code ✓ structure ✓ tests`.
- [ ] **JSON:** a stable, versioned document that is useful to agents on its own:
  ```json
  {
    "schema_version": 1,
    "status": "failed",
    "summary": { "errors": 1, "warnings": 0, "hints": 3, "modules": 412 },
    "violations": [
      {
        "code": "SMT101",
        "rule": "layer-boundary",
        "severity": "error",
        "message": "application must not depend on infrastructure",
        "path": "src/gateway/features/voice/application/session.py",
        "line": 21, "column": 5, "end_line": 21, "end_column": 31,
        "feature": "voice", "layer": "application",
        "source_module": "gateway.features.voice.application.session",
        "target_module": "gateway.features.voice.infra.sql",
        "expected": { "may_depend_on": ["domain"] },
        "hint": "Introduce or reuse a port and wire the implementation in the composition root.",
        "fixable": false,
        "docs_url": "https://…/rules/SMT101"
      }
    ]
  }
  ```
- [ ] **SARIF 2.1.0** for GitHub code scanning, and **GitHub annotations** format.
- [ ] Deterministic ordering (path, line, column, code) in every format.

## 8. Integrations

- [ ] `.pre-commit-hooks.yaml` in this repo (`id: smelt`, `entry: smelt check`, `pass_filenames: true`, `types: [python]`).
- [ ] Documented GitHub Actions snippet: full `smelt check --format github` (plus optional SARIF upload).
      CI always checks the **whole repo**, because cycles and transitive rules can't be judged from a diff alone.
- [ ] README section *"Using Smelt with coding agents"* with an `AGENTS.md`/`CLAUDE.md` snippet:
  ```md
  Before implementing, run `smelt context <feature>` to see where code belongs.
  After every change, run `smelt check --changed --format json`.
  Do not finish while errors remain. Use `smelt explain <code>` when unsure.
  ```

## 9. Type information (later, optional)

- [ ] `TypeIndex` interface: `resolve_type(expr_location) -> QualifiedType | None`,
      `implements(class, protocol) -> bool`.
- [ ] A first implementation backed by Pyright (e.g. via its language server), enabled explicitly
      (`analysis.types: pyright`). It is never required, and rules must degrade gracefully when it is `None`.
- [ ] It unlocks: structural `implements` for Protocols, precise `SMT202` through aliases and
      factories, and resolved patch targets in `SMT402`.

## 10. Testing Smelt itself

- [ ] Fixture projects in `tests/fixtures/<scenario>/` (tiny packages + `smelt.yaml`). Every rule
      has at least one violating and one clean scenario, plus edge cases (TYPE_CHECKING, relative
      imports, `__init__` re-exports, namespace packages).
- [ ] Unit tests: config loading/validation/merging, `third_party` resolution, module classification, role detection.
- [ ] Snapshot tests for text, JSON and SARIF output; CLI tests for exit codes and flags.
- [ ] Tests for suppressions, baseline fingerprint stability (line shifts must not break it), and changed mode.
- [ ] `smelt fix` tests: apply the fix, re-run check, and assert the violation is gone and no new ones appear.
- [ ] Dogfooding: Smelt ships its own `smelt.yaml` and runs `smelt check` on itself in CI.

## 11. Recommended agent workflow (documentation, not code)

```text
smelt context <feature>  →  edit  →  smelt check --changed  →  fix  →  tests  →  pre-commit  →  CI (full check)
```

Smelt defines statically *what good code looks like*. The harness decides *how the agent gets
there*. Document a suggested verification policy for harness authors: "prefer the cheapest
verification that gives sufficient confidence". A rename needs `smelt` + type checker. New
behavior needs one focused regression test. Smelt then ensures that the tests which do get
written don't patch internals or bloat the suite.

---

## 12. Milestones

1. **M1 Core:** config (schema, validation, `third_party`), architecture model, Grimp import index,
   rules SMT101–106, `check` with text + JSON, exit codes, entry point, fixtures.
2. **M2 Agent UX:** `explain`, `rules`, `context`, `where`, `--changed`, path filtering,
   pre-commit hook, inline suppressions + SMT901/902.
3. **M3 Structure & roles:** role detection, SMT204, SMT301–305, `init` with inference.
4. **M4 Code semantics:** syntax index, SMT201–203, SMT205–206, deduplication.
5. **M5 Tests:** SMT401–408, test layout strategies.
6. **M6 Adoption & ecosystem:** baseline + SMT903, SARIF/GitHub formats, `inspect`, `fix`, `verify`,
   plugins via entry points, JSON schema publication, docs site per rule.
7. **M7 Types:** `TypeIndex` with an optional Pyright backend.

## 13. Out of scope for now

Ideas worth revisiting once the core is proven. Don't implement them, and don't design
abstractions for them in advance.

- Presets and `extends:` (e.g. `smelt:feature-sliced-ddd`) with deep config merging.
- Richer role detection: combined conditions (`base` + `suffix`), decorators, module globs,
  file-name patterns with placeholders (`{name}_service.py`).
- Structure policies: required layers per feature, nesting rules with allowed sub-groups.
- Path-scoped severity overrides and rule references by name instead of code.
- Performance work: budgets, caching, benchmarks, parallelism. Keep the code simple first.
