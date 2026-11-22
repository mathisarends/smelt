# Level 6: Scaling the policy

Grow the style guide beyond one project: package APIs, indirect imports, code
conventions and shared configuration.

## Package APIs

```yaml
architecture:
  packages:
    feature-shape:
      match: app.features.*
      required: [application.api, domain]
      public: [application.api]

rules:
  architecture/non-public-module-import: error   # ARC007: import only public entry points
  architecture/missing-required-module: error    # ARC014: every feature has the same shape
```

See [package APIs](structure.md).

## Indirect imports

```yaml
architecture:
  import_graph:
    transitive: true
```

Now `app.domain.model → app.shared.utils → sqlalchemy` is a finding too, with
the full chain.

## Code conventions

Options live under `style` and can differ per package:

```yaml
style:
  dataclass_options: [slots]
  overrides:
    legacy: {match: app.legacy, dataclass_options: []}

rules:
  modeling/missing-dataclass-option: warning
  complexity/excess-positional-parameters: {severity: warning, max: 3}
```

Browse the [rule catalog](rules/README.md) for typing, idioms, imports,
visibility and modeling rules.

## Path exceptions

```yaml
ignore:
  - {rule: tests/excess-patches, paths: ["tests/legacy/**"], reason: Migrating.}
```

## Shared policy

```yaml
# runtime/smelt.yml
extends: ../smelt.base.yml
```

Mappings merge, lists replace. `smelt config show --sources` shows where each
value came from. See [shared local policy](configuration.md#shared-local-policy).

## uv workspaces

```yaml
# smelt.yml at the workspace root
project: {workspace: uv}
architecture:
  members:
    "services/*": {may_depend_on: ["packages/*"]}
    "packages/*": {may_depend_on: []}
rules:
  architecture/member-boundary: error   # ARC009
```

Members can add their own `smelt.yml`; lowering an inherited severity needs a
reason. See [uv workspaces](configuration.md#uv-workspaces).

## Reference

[Configuration](configuration.md) · [Rule activation](rule-selection.md) ·
[CLI and reports](cli.md) · [Rule catalog](rules/README.md)
