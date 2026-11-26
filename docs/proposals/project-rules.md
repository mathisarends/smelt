# Proposal for project-defined rules

Status: proposed API for review. The examples describe a future Smelt release.

A team wants calls to the system clock in its domain layer to go through its
own clock API. The team already tells Smelt which modules belong to that
layer. It should be able to contribute the convention in an ordinary project
PR and use the same rule in checks and writing guides.

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

`Rule` and `calls` form the proposed public API. The module exports its rules
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
configuration error. Errors should identify the originating file and name
both declarations for a collision.

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
These cases specify the first end-to-end test, with the rule selected as a
warning and `--fail-on warning`:

| Module layer | Source | Expected result |
| --- | --- | --- |
| domain | `from datetime import datetime as DT` followed by `DT.now()` | One warning at the call, exit 1 |
| infrastructure | The same import and call | Clean, exit 0 |
| domain | `invoice.now()` on an unknown receiver | Clean, exit 0 |
| domain | `DT.now()` where a parameter named `DT` shadows the import | Clean, exit 0 |

For `--staged`, read the rule file, configuration and analyzed source from
the Git index. A regression test should stage the example, change the target
in the working copy and verify that the staged check still reports the
original target. Changed and staged comparisons must follow the same policy
selection as built-ins. Include the resolved matcher in the policy identity
so editing the plugin alone can change that identity.

## Decisions for implementation review

- **Loading Python declarations.** The example needs only an SDK import,
  named declarations and a list. A static loader for this subset would work
  directly on captured source and could point to unsupported expressions.
  Importing a trusted Python module would also allow helpers and factories,
  but executes project tooling code and requires snapshot-aware dependency
  loading. Decide which authoring forms to promise before publishing the API.
- **Rule identity in reports.** Use the qualified name for selection,
  explanations and ignore comments. Specify how the existing machine-readable
  code field and debt fingerprints represent that identity. Cover two projects'
  rules with the same short name and reserve built-in names.
- **First delivery.** Exercise this single matcher through the real CLI,
  including a staged check, a scoped guide and an inline suppression. The
  results should agree through the native entry point and the Python CLI.
  Use a second concrete project convention to choose the next matcher.
