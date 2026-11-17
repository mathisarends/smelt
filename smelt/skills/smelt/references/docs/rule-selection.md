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
`ALL` selects the whole catalog. The value `on` keeps each rule's catalog
severity instead of forcing one severity onto certain errors and heuristic hints:

```yaml
rules:
  ALL: on
  tests: off
  complexity/inline-call-argument: error
```

Precedence within one map is `ALL`, then family, then group, then individual
rule, regardless of YAML key order. In a package contract, `ALL` and families
select only source rules; test-file and project-level rules stay at project
level. Under strict coverage, broad selections leave SMT306 at `error`.

## Built-in base and layered files

`extends: smelt:recommended` starts from `ALL: on` with the style conventions
turned off where either spelling is valid, so the project chooses them
deliberately. The preset ships with the installed Smelt version; upgrading can
enable newly cataloged rules.

```yaml
extends: smelt:recommended
version: 1
project: {root_packages: [app]}
rules:
  layout/definition-order: error  # adopt one convention
  complexity: warning             # tighten a family
```

Every file in an `extends` chain is its own rules layer. A file's setting for a
rule replaces the setting of the files it extends, even when it is broader: a
project `layout: on` re-enables a convention the preset turned off. Within one
file, the most specific entry wins. `smelt config show --sources` names the file
or preset behind each entry.

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

## Workspace members

In a [uv workspace](configuration.md#uv-workspaces), a member's `smelt.yml` is
one more rules layer. It applies by path to the member's sources and tests:

1. the root rules layers, presets and bases first;
2. the member's `rules`, from the innermost member that contains the file;
3. the closest package contract, including the contract the member's
   `structure` options compile into.

```yaml
# smelt.yml at the workspace root
rules: {complexity: warning}
```

```yaml
# packages/legacy/smelt.yml
rules:
  complexity: {severity: off, reason: Migrating the legacy service.}
  complexity/duplicate-function: {severity: hint, reason: Review duplication before enforcement.}
```

Here `packages/legacy` reports only `complexity/duplicate-function`, as a hint.
Every other member keeps `complexity: warning`. Within a member file, the most
specific selector wins, as in any other layer. A rule that only a member enables
stays off in the other members.

A lower member severity needs `{severity: ..., reason: "..."}`, including in
member package contracts. The winning selector supplies the reason; `on` is
compared using the catalog severity. See [reasoned member exceptions](configuration.md#reasoned-member-exceptions).

A member's `architecture` activates the architecture rules the same way the root
architecture does, for example `layers` turns on `architecture/layer-boundary`.
Each such rule then judges every module against the architecture that governs
it, the member's or the root's. See
[member architecture](configuration.md#member-architecture).

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
