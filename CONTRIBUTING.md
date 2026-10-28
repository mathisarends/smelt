# Extending Smelt

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

Keep related rules together when they share a concept, as in `structure/headers.py`.
Give a growing concept its own module rather than expanding a catch-all file.
Normal checks stay read-only. A new rule does not need a fixer or a new index.

## One rule, one catalog entry

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
