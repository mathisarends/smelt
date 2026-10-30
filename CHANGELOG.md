# Changelog

User-visible changes, newest first. Agents should read the latest release's
migration notes before interpreting fewer findings as an improvement. For the
current configuration and complete rule contracts, see the [README](README.md),
[rule catalog](docs/rules/README.md) and [activation guide](docs/rule-selection.md).

## [Unreleased]

- Optional [SMT430](docs/rules/SMT430.md), enabled by
  `rules: {test-only-public-attribute: warning}` or `--select SMT430`, reviews
  public instance/class data attributes read only by tests outside their class.
  Findings include test locations and an available private name. Possible
  production accesses and uncertain class contracts preserve visibility.
  Public-use rules share the same member index, export/import evidence and
  private-name suggestions. The new rule is off by default and never renames code
  automatically; existing visibility policies retain their activation.
- Optional [SMT318](docs/rules/SMT318.md), enabled with `rules: {SMT318: hint}`
  or `--select SMT318`, reviews direct `StrEnum.MEMBER.value` reads for using the
  string enum member directly. Known import aliases and analyzed re-exports are
  supported; ordinary enums, uncertain bindings and instance inference are
  excluded. Exact-string boundaries can still need a conversion, so findings
  remain advisory and checks stay read-only. The default rule set is unchanged.
- Optional [SMT316](docs/rules/SMT316.md), configured with package
  `member_visibility: private-when-internal`, reviews public module functions and
  ordinary instance methods with no observed external use, including unused
  definitions. Findings suggest a private name and list internal uses/callbacks
  so agents can review renames. Existing private names are avoided. Explicit
  exports, decorators, inheritance and possible external method accesses preserve
  public APIs; test-only public module functions remain covered by SMT429.
- Optional [SMT317](docs/rules/SMT317.md), configured with package
  `static_methods: module-functions`, suggests extracting internal static helpers
  to private module functions. Class dependencies, inheritance, extra decorators
  and possible external callers preserve deliberate static APIs. Both new
  policies support closest `allow` overrides and appear in `smelt context`.
  Checks remain read-only and the default rule set is unchanged.
- Optional [SMT429](docs/rules/SMT429.md), enabled by
  `tests.review_test_only_public_functions`, reviews public module functions
  accessed from tests but not from other analyzed production modules. Findings
  suggest a leading underscore and include test locations so agents can review
  the rename and test the public caller. Explicit exports and decorated functions
  stay public; external/dynamic consumers remain a review limitation. The default
  rule set is unchanged.

## [0.1.6] - 2026-10-29

### Compatibility and activation

- SMT428 remains opt-in through `tests.review_async_mock_assertions`; the default
  rule set is unchanged. Its findings ask for review of an individual test, not a
  claim that the suite misses the regression.
- Existing `tests.review_test_deadlines` policies can now report additional known
  asyncio subprocess waits. Review each operation's local deadline, including
  cleanup paths, rather than adding a blanket timeout or await restriction.

### Added and fixed

- SMT426 now reviews `wait()` and `communicate()` on known asyncio subprocesses
  created through awaited standard factories or explicit Process annotations.
  Each operation needs its own local guard, including exit waits after bounded
  reads or `kill()`. Unknown process-like objects remain outside scope.
- Optional SMT428 (`tests.review_async_mock_assertions`) reviews call-only checks
  on known async mocks and async class-spec methods. Await assertions, deliberate
  scheduling checks and synchronous methods remain valid. Known fixture specs and
  simple local history values are traced without executing analyzed code; unknown
  methods and general data flow remain outside scope. Executed missing-await
  counterexamples demonstrate the distinction between call and await assertions.
- `context` text now shows all adopted test-quality review flags, including when
  only the newer policies are enabled.
- SMT424 recognizes awaited, typed Event observations inside local timeout
  contexts that raise on expiry, consistently with SMT426 deadline recognition.
  Unused contexts, unawaited waits and `move_on_after` scopes are not outcome
  evidence; configured timeout helpers alone still do not count as assertions.

## [0.1.5] - 2026-10-29

### Compatibility and activation

- Existing opted-in test policies can report additional MonkeyPatch operations
  and known instance accesses. New SMT425-SMT427 review policies remain opt-in;
  the small default rule set is unchanged. Review legitimate boundary tests
  rather than exposing internals or imposing a global mock ban.
- `--fail-on warning` now includes configuration warnings, and unused
  `pytest.raises`/`warns` context objects no longer count as outcome evidence.
  A passing static report is not proof of test effectiveness.

### Added and fixed

- `scripts/evaluate.py` supports repeatable agent review loops: compare two import
  roots on identical config bytes, retain
  structured recognition changes and independently bounded logs, then optionally
  execute explicitly selected pytest targets. Invalid analysis, timeouts and
  incomplete collection remain distinct from test failures; normal checks never
  execute analyzed tests. See the [evaluation workflow](README.md#evaluate-a-test-quality-change).
- Test outcome review now recognizes known mock interaction assertions, annotated
  fixture receivers and awaited, bounded asyncio Event observations. Unused
  `pytest.raises`/`warns` context objects no longer count as checks. Unknown
  `assert_*` methods and fixture execution alone remain insufficient evidence.
- [SMT420](docs/rules/SMT420.md) tracks direct known production constructors and
  explicitly typed instance reads, including analyzed facade re-exports and
  consistent known fixture return/yield tuple components. Unknown/conflicting
  outputs, private writes and local fakes are excluded; fixture inference has
  separate provenance and respects class/conftest scope. A private class import
  is not reported again for each public instance operation.
  [SMT421–SMT423](docs/rules/SMT422.md) now include MonkeyPatch attribute
  replacement, aliases and context scopes, with operation/provenance metadata.
  MonkeyPatch environment/mapping setup is excluded from the mock count.
- Optional [SMT425](docs/rules/SMT425.md), [SMT426](docs/rules/SMT426.md) and
  [SMT427](docs/rules/SMT427.md) review potentially vacuous outcomes, known waits
  without deadlines/scheduler-clock replacements, and small independent-case loop
  candidates. They remain advisory and require explicit test policies; no fixer,
  blanket mock/sleep ban or assertion quota is introduced.
- Configuration warnings now appear in JSON `configuration_diagnostics`, SARIF
  configuration notifications, GitHub annotations and text summaries. They count
  toward `summary.warnings` and `--fail-on warning`, including scoped/changed
  checks; rule selection, suppressions and debt cannot hide configuration warnings.

## [0.1.4] - 2026-10-29

### Migration: rule activation changed

- Only **SMT301, SMT901, SMT902 and SMT903** run by default. Declaring an
  architecture, package or test policy activates the corresponding checks without
  a second enable switch. The catalog now distinguishes 4 default, 24 policy-driven
  and 2 explicitly selected rules; activation and severity are separate.
- **SMT104 import cycles and SMT305 unclassified-module hints require explicit
  selection.** To retain these checks from earlier versions, add:

  ```yaml
  rules:
    SMT104: error
    SMT305: warning
  ```

- `--select SMT3` and `--select ALL` activate matching opt-in checks and replace
  normal selection. Explicit `off` settings and ignores still win; selection does
  not invent missing policies. Strict coverage remains mandatory when configured
  or requested, even outside the selected rule prefix.
- `smelt init` still proposes architecture but leaves naming conventions and test
  mirroring disabled for review. A detected mirror pattern remains available as a
  setting, with an activation comment.
- Debt for inactive or hint-only rules is retained when recording/pruning, rather
  than silently considered resolved. Older cycle and indirect-import debt can
  resurface because corrected fingerprints track the actual dependency. Review
  these findings before recording new debt.

### Added

| Capability | Rules / interface | What agents can now check |
| --- | --- | --- |
| Strict architecture coverage | [SMT306](docs/rules/SMT306.md), `project.coverage: strict`, `--strict-coverage` | Production files outside package selection, missing architecture roles and exclusions without reasons. These gaps cannot be hidden by changed/path filters, suppressions or debt. |
| Package structure and public APIs | [SMT107](docs/rules/SMT107.md), [SMT307](docs/rules/SMT307.md) | Required modules and explicit public entry points through `structure.contracts`; outside callers cannot import private implementation modules. |
| Import fanout | [SMT108](docs/rules/SMT108.md) | Optional hints when one outside caller imports too many distinct modules from the same contracted package. |
| Source conventions | [SMT308](docs/rules/SMT308.md)–[SMT312](docs/rules/SMT312.md) | Scoped absolute/relative import styles, module-docstring policy, future-annotation policy and private module/package names. Python targets come from declared metadata or configuration. |
| Definition order | [SMT313](docs/rules/SMT313.md) | Dependency-aware module/class ordering, including execution barriers and bounded, explicitly requested fixes. |
| Instance field assignments | [SMT314](docs/rules/SMT314.md) | Bundled or chained writes to multiple direct instance attributes when `instance_assignments: separate` is declared. |
| Type dispatch | [SMT315](docs/rules/SMT315.md) | Review hints for same-subject `isinstance`/`elif` chains under a declared `type_dispatch` policy. |
| Test quality | [SMT420](docs/rules/SMT420.md)–[SMT424](docs/rules/SMT424.md) | Configured private production access, forbidden mocks, string patch targets, per-test patch limits and missing recognized outcome assertions. |

- `smelt context <path> --format json` explains ownership, coverage status,
  applicable contracts, effective conventions and Python targets for existing or
  planned files. Planned placement is distinguished from analyzed source.
- `check --show-coverage` exposes the per-file policy inventory. JSON reports add
  discovery roots, classification counts, policy references, exclusions and a
  policy hash. Rule metadata exposes activation, analysis requirements and
  configuration paths through `rules`, `explain` and reports.
- Contributor documentation and catalog checks verify explicit registration,
  unique rule codes/names, complete metadata and generated documentation.
- Tag-triggered PyPI Trusted Publishing validates the tag/version and main
  ancestry, runs checks, builds and smoke-tests both distributions, then publishes
  the validated artifacts without a stored PyPI API token.

### Fixed

- Resolve named re-exports across layer, feature, shared and composition-root
  boundaries. Indirect findings retain the final forbidden target; declared
  feature wiring no longer produces false unknown-layer findings.
- Track every cyclic architecture edge and semantic identities/multiplicity in
  changed/debt comparisons. New edges, patches or ordering inversions cannot hide
  behind an accepted aggregate finding. Existing violations do not become new
  merely because an import's formatting, comment or alias changed.
- Merge namespace package portions, reject duplicate module definitions and infer
  usable workspace source roots. Grouped imports check every alias, and source
  discovery avoids symlink cycles.
- Reject duplicate YAML keys and invalid source encoding. With `--format json`,
  usage/configuration/analysis errors return structured error documents; reports
  identify their origin and comparison scope. SARIF and GitHub paths are rebased
  correctly for nested configurations.

### Agent workflow and limits

```bash
smelt rules --format json
smelt context path/to/module.py --format json
smelt check --changed --base origin/main --format json
smelt check --strict-coverage --show-coverage --format json
smelt explain SMT313 --format json
```

- Normal checks are read-only. After configuring ordering with `fix: safe-only`,
  `smelt check --select SMT313 --fix` can apply bounded definition moves. It
  preserves source trivia, validates the permitted AST moves and refuses unsafe
  or concurrently changed files. Run the project formatter afterwards.
- SMT314 and SMT315 have no automatic rewrite. Splitting assignments or converting
  type dispatch can change evaluation, guards and fallthrough.
- Coverage means policy assignment within declared discovery roots, not a proof
  of behavior. Package APIs and test-quality checks are static and do not infer
  dynamic imports, arbitrary runtime access or fixture dependency effects.
- Import fanout, match suggestions and missing-assertion checks are review aids;
  their default severities differ. Configure policy and severity deliberately.

[Unreleased]: https://github.com/mathisarends/smelt/compare/v0.1.6...HEAD
[0.1.6]: https://github.com/mathisarends/smelt/compare/v0.1.5...v0.1.6
[0.1.5]: https://github.com/mathisarends/smelt/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/mathisarends/smelt/compare/v0.1.3...v0.1.4
