# CLI and reports

See the [changelog](../CHANGELOG.md) for new capabilities, fixes and migration notes
by release, including changes to agent workflows and rule defaults.

Most rules are opt-in. Smelt enables declared architecture/package/test policies
without a second switch; only unknown-layer and suppression/debt diagnostics run
by default. Import cycles and unclassified-module hints require explicit selection.
See [rule activation and migration](rule-selection.md) and the
[full catalog](rules/README.md) for the decisions and precedence rules.

```bash
smelt init                           # draft smelt.yaml from the code (also uv workspaces)
smelt init --maintainability         # write individually named source/test review checks
smelt init --config other.yaml       # write a config and editor schema in that directory
smelt check                          # whole project
smelt check --changed                # only what the working tree introduced since HEAD
smelt check --staged --fail-on warning  # review the actual commit, including new warnings
smelt check --staged --no-cache       # recompute to verify the cached result
smelt check --changed --base origin/main --format json
smelt check --strict-coverage         # fail on omitted or unclassified production code
smelt check --show-coverage --format json  # include the per-file policy inventory
smelt context user                   # what applies to a feature, module or path
smelt explain SMT101                 # rationale, examples and config keys of a rule
smelt rules                          # full catalog with activation and severity
smelt debt                           # record today's violations as known debt
smelt debt --prune                   # drop debt entries that were fixed
smelt config show                    # the resolved config
smelt config schema                  # the JSON schema of smelt.yaml
```

Exit codes: `0` clean, `1` violations at or above `--fail-on`, `2` config or usage error.
A config entry that points at nothing (a mistyped package, a missing test root) is a
config error, not a silently disabled rule.
Explicit check paths must exist and contain analyzed source or test files; unknown
`--select`/`--ignore` selectors are usage errors. JSON includes the reporting scope, file
count and active rule codes. `--config PATH` works before or after a subcommand.
A stored JSON report explains itself: `scope.mode` is `full`, `changed` or `staged`, and
`comparison` names the requested `base`, the resolved `merge_base`, the
`compared_revision` and `head`, so `passed` from `--changed` reads as "nothing new since
that commit", not as a clean inventory. `coverage` lists the source and test roots,
workspace members that are not analyzed, module counts per classification, the
composition-root and wiring exemptions, third-party policies per layer, the import
settings (`type_checking`, `transitive`, `cycles`), cross-feature allowances as fields,
and a `policy_hash` that is equal for reports checked under the same resolved config.
The report's `project` names the config, project root and repository root. JSON
finding paths are project-relative; SARIF and GitHub annotation paths are rebased
to the repository root, including when using a nested `--config`.
`smelt context --format json` carries the same import settings, allowances and hash.
With `--format json`, an exit `2` prints a JSON document as well, so an agent needs no
text parser: `{"schema_version": 1, "status": "error", "error": {...}}` with `kind`
(`usage`, `config` or `analysis`), `message`, the rejected `input` (`option`, `value`)
and, for config errors, the `config` file with each issue's YAML `location`. That covers
argument errors (`--fail-on bogus`, an unknown flag) whenever `--format json` is on the
command line; an invalid `--format` value itself is reported as argparse text.

Valid configurations can also produce warnings, such as cyclic `may_depend_on`
policies. Reports retain these separately from rule violations in JSON
`configuration_diagnostics` (config path, key, severity and message) and SARIF
`toolConfigurationNotifications`. Text and GitHub output include them too.
They count toward the warning total and cause exit `1` with `--fail-on warning`.
Configuration warnings describe the current policy even in scoped/changed checks;
rule selection, source suppressions and accepted debt do not remove them.

`--changed` checks the base commit (HEAD, or the merge-base with `--base`) with today's
config and reports only violations that are new, wherever they show up. Old violations in
an edited file stay quiet, a new import cycle does not.
Changing an import's comment, formatting or alias does not make its existing boundary
violation new; an additional identical violating import still counts as new.
