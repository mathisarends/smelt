# Project-defined rules

Define a local rule when your project needs its own convention. For example,
require domain modules to read the time through your team's clock API. The
matcher can use the layer assignments already declared in your policy.

## Define a rule

```python
# tools/smelt_rules.py
from smelt.rules import Rule, calls

domain_clock = Rule(
    name="acme/domain-clock",
    match=calls.to("datetime.datetime.now").in_layer("domain"),
    message="The domain layer reads the system clock directly.",
    guide="Read the current time through acme.clock.now().",
)

rules = [domain_clock]
```

Import `Rule` and `calls` by name from `smelt.rules`. The module exports its rules
as a list named `rules`. The qualified name identifies a rule across files and
commands, so renaming the Python variable preserves suppressions and debt.
The finding points to the offending call and shows `message`.
`smelt context TARGET --guide` shows `guide` when the rule is active and the
target belongs to the domain layer, even for a planned module.

## Load the module and select its rules

Add the following to the project's existing root `smelt.yml`:

```yaml
plugins:
  - ./tools/smelt_rules.py

rules:
  acme/domain-clock: warning
```

Smelt resolves the path relative to the configuration file, registers the
exports and then validates the rule selection. Loading a file makes its rules
available for `smelt rules` and `smelt explain`. The usual selection and
severity settings determine which findings appear in a check. An unknown
rule, missing plugin file, invalid export or duplicate identity is a
configuration error. Use a namespace distinct from built-in families.

## Execute the matcher in Rust

The example describes two operations: resolve calls to a qualified name, and
restrict them to modules assigned to a layer. The loader produces a validated
matcher description once per policy snapshot. The native checker uses its
module classification, syntax and binding information to evaluate active
matchers together. Rules targeting the same call can share candidate lookup.
Each emitted finding enters the normal severity, suppression, debt and
reporting pipeline. Projects without plugins keep the native fast path.

The first matcher covers direct, statically resolved calls through ordinary
imports and aliases. Unresolved receivers and shadowed names are skipped.
These examples assume the rule is selected as a
warning and `--fail-on warning`:

| Module layer | Source | Expected result |
| --- | --- | --- |
| domain | `from datetime import datetime as DT` followed by `DT.now()` | One warning at the call, exit 1 |
| infrastructure | The same import and call | Clean, exit 0 |
| domain | `invoice.now()` on an unknown receiver | Clean, exit 0 |
| domain | `DT.now()` where a parameter named `DT` shadows the import | Clean, exit 0 |

For staged checks, Smelt reads the configuration, plugin declarations and
analyzed Python files from the Git index. Changed checks compare both source
snapshots using the same current rule definitions. The qualified rule name is
also its report code. Use it in inline suppressions, debt and CLI selectors.

## Supported declarations

Use literal strings, one assignment per rule and a final `rules = [...]`
export list. The loader reads these declarations as Python syntax. It never
imports the project module or runs callbacks. Helpers, import aliases, relative
call targets and dynamic expressions are outside this declaration format.
Plugin paths must resolve inside the project. A root policy can inherit its
plugin list from a local `extends` base.

`calls.to("package.function")` checks production modules; adding
`.in_layer("domain")` restricts it to that architecture layer. The checker
resolves unambiguous module-level imports. It skips names rebound anywhere in
the file and wildcard imports. This conservative rule can leave calls
unreported even when shadowing occurs in a different function.

Inspect the registered rule with `smelt explain acme/domain-clock`, run it
on its own with `smelt check --select acme/domain-clock`, and preview its
writing instruction with `smelt context app.domain.service --guide`.
