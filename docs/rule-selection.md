# Rule activation and migration

Configure a fixed pattern or a named group in `rules`, with its severity as the
single activation setting:

```yaml
rules:
  layout/definition-order: error
  information-hiding: warning
  layout/misplaced-static-helper: hint
  tests/missing-outcome-check: warning
  tests/unbounded-wait: warning
  tests/missing-await-assertion: warning
```

## Families

Every rule name starts with its family: `architecture`, `imports`, `layout`,
`visibility`, `modeling`, `typing`, `complexity`, `idioms`, `frameworks`, `tests`
or `meta`. A family name selects all of its rules wherever rule names are
accepted, including package-contract `rules`, `--select` and `--ignore`.
`smelt explain <family>` lists the family's rules.

```yaml
rules:
  typing: warning
  typing/anonymous-tuple-type: off  # A more specific entry wins.
```

A family entry enables every rule in it, including heuristic hints, so prefer it
for exploration or for families whose findings the project has already reviewed.
Precedence within one map is family, then group, then individual rule,
regardless of YAML key order.

## Information hiding

`information-hiding` reviews unnecessarily public implementation details with one
setting. It includes functions and methods (SMT316), module constants and type
aliases (SMT320), data attributes (SMT335), and functions/attributes whose only
external consumers are tests (SMT429/SMT430). Test access alone does not justify
a public API. Each check retains its conservative exclusions, evidence, stable
code, suppression and debt identity. It asks for review; it cannot prove a rename
is safe or observe consumers outside the configured analysis scope.

The current test-only checks cover module functions and data attributes. Methods
with observed test users retain SMT316's external-use exclusion. Grouping does not
expand the underlying checks' evidence or coverage.

```yaml
rules:
  information-hiding: warning
  visibility/internal-public-attribute: off  # Keep this specific review disabled.
```

An individual rule entry overrides its group's severity in the same map,
regardless of YAML key order. A closer package setting overrides an enclosing
setting, including an individual parent setting. Group `off` disables all five
members unless an individual entry or closer package re-enables one. Duplicate
name/code entries for an individual rule remain configuration errors.

The same group works in package `rules`, `--select`, `--ignore` and scoped
`ignore` entries. Inline suppressions continue to use stable SMT codes or
prefixes. Inspect the membership with `smelt explain information-hiding`; catalog
JSON exposes a `groups` list on every rule. Existing individual configurations
remain valid. Migrating a narrower selection to the group also enables any of
the five checks it previously omitted.

Private instance state is valid. Moving private class constants to module scope
(SMT334) and extracting static helpers (SMT317) concern placement and stay
independent. They are not members of this group.

## Activation and precedence

The catalog distinguishes three modes, independently of severity:

| Mode | Activation |
| --- | --- |
| `default` | SMT301 and suppression/debt hygiene SMT901–SMT903 |
| `policy` | Explicit architecture/interface policies or real parameters such as import style, mirror layout, mock paths and patch limits |
| `explicit` | Fixed source/test patterns, import cycles and architecture/unclassified-module hints; enable with `rules` or CLI selection |

Declaring layer dependencies activates SMT101; `tests.layout: mirror` activates
SMT401. An empty public list still declares a private API, and a patch limit of
zero still forbids patches. Mere ordering options do not activate SMT313.

A source-convention option enables its rule only where it applies. Set under
`structure`, it covers the project; set in a package contract, it covers the
matching packages, and the rule reports nothing elsewhere. Selecting the same
rule in `rules` or with `--select` enables it everywhere and uses built-in
defaults where no option is set, for example a limit of 5 for SMT345. See
[project-wide options](structure.md#project-wide-options-and-package-overrides)
for the defaults.

```bash
smelt rules --format json
smelt explain visibility/internal-public-member
smelt check --select information-hiding,tests/unbounded-wait
smelt check --select SMT3
smelt check --select ALL --ignore architecture/import-cycle
```

`--select` replaces normal selection and accepts canonical names, rule groups,
stable code prefixes and `ALL`. `--ignore` accepts the same selectors. A selection enables
fixed patterns without additional policy switches. Architecture/style checks
still require their actual model or parameters where the catalog says so.

Project `rules.<name>: off` disables that rule even under explicit selection.
Package overrides use the same rule names, and the closest package entry wins;
they can disable a global rule, change severity or enable it locally. CLI ignores
apply across the run. Duplicate name/code entries and conflicting settings for
the same concrete package are config errors.

Strict coverage is mandatory: `project.coverage: strict` or `--strict-coverage`
keeps SMT306 enabled outside a select prefix. Lowering its severity, ignoring it
or overriding it inside a package is rejected. Invalid configuration, discovery
and source-analysis errors remain fatal independently of selection.

Debt for inactive rules or disabled packages remains unassessed. Recording and
pruning retain it; disabling checks does not mean violations were fixed. Hint-only
debt is also retained by the debt command. Changed checks compare source under
the current explicit policy on both sides.

Use `smelt init --maintainability` to write a visible selection of source/test
reviews, including `information-hiding: warning`. Plain `init` infers architecture
and leaves those conventions opt-in;
both commands write an editor schema beside the YAML. The complete small-project
API, package exceptions, editor setup and removed-field/name mapping are in
[configuration and migration](configuration.md). See the
[generated catalog](rules/README.md) for every rule's activation decision.
