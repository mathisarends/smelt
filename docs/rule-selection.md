# Rule activation and migration

All catalog rules ship with Smelt, but most are opt-in. The catalog distinguishes
three activation modes, independently of severity:

| Mode | Rules | Activation |
| --- | --- | --- |
| `default` | SMT301, SMT901, SMT902, SMT903 | Unknown-layer diagnostics and suppression/debt hygiene |
| `policy` | Architecture, package contracts, conventions and test policies | Declaring the relevant policy activates its check automatically |
| `explicit` | SMT104 import cycles, SMT305 unclassified-module hints | Enable via `rules` or CLI selection |

For example, declaring layer dependencies activates SMT101; `private_modules:
forbid` activates SMT312; `tests.layout: mirror` activates SMT401. There is no
second enable switch for a declared policy. An empty public list still declares a
private API, and a patch limit of zero still forbids patches. An `allow` policy or
`ordering.enabled: false` does not activate that convention by itself.

```yaml
rules:
  SMT104: error    # opt in to cycle detection
  SMT305: warning  # opt in to unclassified-module hints
  SMT315: warning  # severity override; the match preference still needs its policy
```

```bash
smelt rules --format json             # full catalog with activation mode/reason
smelt check --select SMT3             # run this prefix, including opt-in rules
smelt check --select ALL              # run every rule against declared policies
smelt check --select ALL --ignore SMT104
```

`--select` replaces the normal default/policy rule selection. Code prefixes and
`ALL` activate matching opt-in checks. `rules.<code>: off` and `--ignore` win over
selection. Selection never invents a style or architecture: a convention without
its required policy remains inapplicable. Cycle scope settings under
`architecture.imports.cycles` control SMT104's scope and do not enable it alone.
Invalid configuration, discovery and source-analysis errors remain fatal.

Strict coverage is the exception to filtering: declaring `project.coverage:
strict` or passing `--strict-coverage` keeps SMT306 mandatory, even outside a select
prefix. Turning it off, lowering its severity or ignoring it is rejected.

Migration from earlier defaults: SMT104 and SMT305 no longer run automatically.
Enable them explicitly to preserve the previous inventory; a smaller finding
count from turning checks off does not mean defects were fixed. Existing explicit
package/test policies continue activating their checks. Debt for inactive rules
is retained during recording/pruning and is not reported as resolved. Enable a
rule again before pruning its old entries; hint-only entries are also retained by
the debt command rather than silently removed.

`smelt init` still proposes architecture, but leaves naming conventions and test
mirroring off. A detected mirror pattern is retained as a setting, with a comment
showing how to activate it after review. New contributed rules start off unless
they declare a typed policy activation condition or deliberately join the small
default set. See [the generated catalog](rules/README.md) for every decision.
