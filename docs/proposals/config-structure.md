# Proposal: config structure v2

Status: draft, not implemented.

## Problem

`smelt.yaml` v1 mixes three kinds of settings in the same sections:

| Kind | Question it answers | v1 keys today |
|---|---|---|
| Architecture model | What shape should the code have? | `architecture.features`, `.layers`, `.shared`, `.composition_root`, `.cross_feature` |
| Framework/tool exceptions | Which technology gets special treatment, and where? | `architecture.dependency_injection`, `analysis.types` |
| Conventions and lint knobs | How is code named, placed and tested? | `structure.*`, `roles`, `tests.*` |

On top of that, some severities can be set in two places. `tests.private_access: allow`
is the same as `rules: {private-access: off}`, and `tests.interaction_assertions` is
the severity of SMT406. Readers have to know both.

## Principles

1. **One question per section.** A key belongs to exactly one of: where the code is,
   what shape it has, how it is organized, which tools get exceptions, and how findings
   are handled.
2. **Severity lives only in `rules`.** Sections describe the project. Only `rules`
   decides how loud a finding is.
3. **Thresholds stay with the concept they measure**, not with the rule code.
4. **Nothing is lost.** Every v1 key has exactly one v2 home, so migration is
   mechanical.

## Target shape

```yaml
version: 2

project:                      # where the code is
  source_roots: [backend/src, libs/agent/src]
  test_roots: [backend/tests]
  exclude: []

architecture:                 # what shape the code has
  features: {root: backend.features}
  layers:
    domain: {path: domain, may_depend_on: []}
    application: {path: application, may_depend_on: [domain]}
    infrastructure: {path: infrastructure, may_depend_on: [domain, application]}
    presentation: {path: presentation, may_depend_on: [application, domain]}
  shared: [backend.shared]
  composition_root: [backend.main, backend.lifespan]
  cross_feature:
    default: deny
    allow:
      - {from: session.presentation, to: auth.presentation}
  cycles: [features, layers, siblings]

conventions:                  # how code is named, placed and tested
  naming:
    forbidden: [utils, helpers]
  packages:
    crowded_threshold: 10
  roles:
    port: {detect: {base: typing.Protocol}, layers: [application], file: ports.py}
  tests:
    layout: feature
    pattern: "backend/tests/{feature}"
    patching: {allow: [external, environment, stdlib], forbid: [private]}
    mocks: {max_per_test: 3, forbid_first_party: []}
    bloat: {ratio: 5, min_test_loc: 100}

integrations:                 # framework and tool exceptions
  dependency_injection:
    frameworks: [dishka]
    allowed_in: ["backend.features.*.infrastructure.di", backend.infrastructure.di]
  type_checker:
    types: pyright
    command: [pyright]
  imports:
    type_checking: include
    transitive: false

rules:                        # severities only, by name or code
  private-access: warning
  interaction-assertion: warning
  crowded-package: off

findings:                     # adopting and silencing
  debt: .smelt/debt.json
  ignore:
    - {rule: SMT305, modules: [backend.env], reason: settings module}
  suppressions: {require_reason: true}

plugins: []
verify: []
```

## v1 → v2 mapping

| v1 | v2 |
|---|---|
| `project.root_packages` | dropped (discovered from `source_roots`; already optional) |
| `project.*` (others) | `project.*` |
| `architecture.features/layers/shared/composition_root/cross_feature` | unchanged |
| `architecture.imports.cycles` | `architecture.cycles` |
| `architecture.imports.type_checking`, `.transitive` | `integrations.imports.*` |
| `architecture.dependency_injection` | `integrations.dependency_injection` |
| `analysis.types`, `analysis.pyright_command` | `integrations.type_checker.types`, `.command` |
| `structure.forbidden_names` | `conventions.naming.forbidden` |
| `structure.crowded_threshold` | `conventions.packages.crowded_threshold` |
| `roles` | `conventions.roles` |
| `tests.layout/pattern/mirror/unmirrored/mirror_suffixes` | `conventions.tests.*` |
| `tests.patching`, `tests.mocks`, `tests.bloat` | `conventions.tests.*` |
| `tests.private_access: allow` | `rules: {private-access: off}` |
| `tests.interaction_assertions: <severity>` | `rules: {interaction-assertion: <severity>}` |
| `rules` | `rules` |
| `ignore`, `suppressions`, `debt` | `findings.ignore`, `.suppressions`, `.debt` |
| `plugins`, `verify` | unchanged |

## Migration

- `version: 2` selects the new layout. `version: 1` keeps loading: the loader rewrites
  it in memory using the table above and reports one deprecation warning per moved
  key. This reuses the mechanism that already migrates `wiring` and `di_frameworks`.
- `smelt config migrate` rewrites `smelt.yaml` to v2 in place, keeping comments where
  possible (a round-trip YAML library such as ruamel.yaml would be a new dependency;
  without it, comments are lost and the command says so).
- `smelt init` writes v2 from the first release that supports it.
- Remove v1 support one minor release later.

## Open questions

1. **Roles: architecture or conventions?** Roles decide which layer and file a concept
   belongs in, which is close to the architecture model. The draft files them under
   conventions because they do not change which imports are allowed.
2. **`integrations.imports`:** `type_checking` and `transitive` change how the import
   graph is built, not a framework exception. An `analysis:` section alongside
   `integrations` might describe them better.
3. **Rule options:** should thresholds like `max_per_test` sit next to the rule instead
   (`rules: {too-many-mocks: {severity: warning, max: 3}}`)? The draft keeps them in
   conventions so that `rules` stays a flat severity table.
