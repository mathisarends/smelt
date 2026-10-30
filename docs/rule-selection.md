# Rule activation and migration

Configure a fixed pattern by name in `rules`, with its severity as the single
activation setting. There is no separate boolean flag or runtime preset:

```yaml
rules:
  public-definitions-first: error
  internal-public-member: warning
  internal-static-helper: hint
  test-without-outcome-check: warning
  unbounded-test-wait: warning
  async-mock-without-await-check: warning
```

The catalog distinguishes three modes, independently of severity:

| Mode | Activation |
| --- | --- |
| `default` | SMT301 and suppression/debt hygiene SMT901–SMT903 |
| `policy` | Explicit architecture/interface policies or real parameters such as import style, mirror layout, mock paths and patch limits |
| `explicit` | Fixed source/test patterns, import cycles and unclassified-module hints; enable with `rules` or CLI selection |

Declaring layer dependencies activates SMT101; `tests.layout: mirror` activates
SMT401. An empty public list still declares a private API, and a patch limit of
zero still forbids patches. Mere ordering options do not activate SMT313.

```bash
smelt rules --format json
smelt explain internal-public-member
smelt check --select internal-public-member,unbounded-test-wait
smelt check --select SMT3
smelt check --select ALL --ignore import-cycle
```

`--select` replaces normal selection and accepts canonical names, stable code
prefixes and `ALL`. `--ignore` accepts the same selectors. A selection enables
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
reviews. Plain `init` infers architecture and leaves those conventions opt-in;
both commands write an editor schema beside the YAML. The complete small-project
API, package exceptions, editor setup and removed-field/name mapping are in
[configuration and migration](configuration.md). See the
[generated catalog](rules/README.md) for every rule's activation decision.
