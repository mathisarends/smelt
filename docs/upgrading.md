# Upgrading Smelt

## 0.2.0 to 0.3.0

0.3.0 changes the JSON check-report contract. Existing YAML configurations keep
`version: 1`; the default rule set and exit codes are unchanged. SMT109 and SMT320
require deliberate adoption. Read the [changelog](../CHANGELOG.md#030---2026-11-01)
for the complete release details.

| What changed | What to do |
| --- | --- |
| JSON check and error documents use `schema_version: 2` | Update consumers before upgrading CI; see the example below. |
| JSON coverage is compact by default | Add `--show-coverage` if a consumer needs roots, policies, Python targets, ordering barriers or the per-file inventory. |
| Visibility boundaries are shared through `usage_scopes` | Resolve each finding's `expected.usage_scope_index` against the report-level array. |
| Text groups related ordering findings and omits empty details | Use JSON for structured consumers, or `--format concise` for one line per finding. |
| Existing checks recognize more evidence | Review the findings changes below before replacing accepted debt. |
| New facade and module-symbol reviews | Adopt their settings separately if useful; neither rewrites code. |

### Upgrade checklist

1. Update JSON consumers as described below, then update every installation pin
   together: project dependency and lockfile, isolated hook dependencies, Git hook
   tags and CI commands. For a tool installation:

   ```bash
   uv tool install --upgrade 'smelt-cli==0.3.0'
   # or: python -m pip install --upgrade 'smelt-cli==0.3.0'
   smelt --version
   ```

   Use `additional_dependencies: [smelt-cli==0.3.0]` for an isolated Python hook,
   `rev: v0.3.0` for a source hook, and
   `uvx --from smelt-cli==0.3.0 smelt check` in CI. Run `prek install` after updating
   hooks. See the complete [integration examples](integrations.md).

2. Refresh the local editor schema from the configuration directory:

   ```bash
   smelt config schema > .smelt.schema.json
   smelt config show
   ```

   Keep the reviewed YAML. `init --maintainability` is a starter for new configs;
   do not use `init --force` to migrate an existing policy.

3. Run a full review before relying on staged or changed-only results:

   ```bash
   smelt check --show-hints --fail-on warning
   smelt check --format json --show-coverage > smelt-report.json
   ```

   Exit `1` means findings meet the selected threshold; exit `2` means a usage,
   configuration or analysis error. Inspect these results before recording or
   pruning debt. Existing SMT codes and debt identities remain valid.

4. Stage the intended changes, run `smelt check --staged --fail-on warning`, then
   run the project's hooks and tests. Full CI checks still enforce the whole
   adopted policy.

Python 3.12+ remains required; use an interpreter that can parse your project's
syntax. Wheels include the native client for Linux x86_64/aarch64, Windows x86_64
and macOS x86_64/aarch64. Source installs, including Git-based hooks, require
Rust 1.88+ and a platform linker. This requirement was introduced in 0.2.0.

### JSON report consumers

Before, schema 1 embedded visibility boundaries in each finding:

```python
scope = finding["expected"].get("usage_scope")
```

Schema 2 stores each distinct boundary once. A consumer that supports both
released formats can normalize it as follows:

```python
def usage_scope(report, finding):
    version = report["schema_version"]
    if version not in (1, 2):
        raise ValueError(f"Unsupported Smelt report schema: {version}")
    expected = finding.get("expected") or {}
    if version == 1:
        return expected.get("usage_scope")
    index = expected.get("usage_scope_index")
    return None if index is None else report["usage_scopes"][index]
```

The index is zero-based; `0` is valid. The array is absent when there are no
usage scopes. Error documents also use schema 2 and retain `status: error` and
`error`; handle them before iterating findings. For coverage details, change:

```bash
smelt check --format json --show-coverage
```

`--show-coverage` restores the full coverage payload; it still emits schema 2 and
does not restore embedded usage scopes. JSON retains every ordering finding even
when text groups them. SARIF remains 2.1.0 and exposes shared visibility boundaries
under `runs[].properties.usageScopes`. Scope, comparison metadata, configuration
warnings and exit thresholds remain available. See [CLI and reports](cli.md).

### Adopt the new reviews

For a project with an `app.browser` package, merge these entries into the existing
configuration, adjusting the package name:

```yaml
rules:
  internal-public-module-symbol: warning  # SMT320

structure:
  contracts:
    browser-api:
      match: app.browser
      export_at: 3                       # SMT109; minimum 2 outside importers
      count_tests: false                 # true also counts discovered tests
```

[SMT109](rules/SMT109.md) reviews widely imported implementation names missing
from a package facade. Its default severity is `hint`, so use `--show-hints` to
see it. Review ownership and initialization before adding exports; a missing
cycle witness does not establish import safety.

[SMT320](rules/SMT320.md) reviews public uppercase module assignments and PEP 695
type aliases without observed external use or public-signature evidence. Exports,
analyzed callers and public annotations preserve names. Excluded and external
consumers remain unassessed. Review the API before renaming. See the full
[source conventions](structure.md) for both rules and their limits.

### Changes in findings under existing policies

- SMT313 can report ordering opportunities previously blocked by builtin unions
  such as `str | bytes` on declared Python 3.10+ targets. Defaults, unknown types
  and namespace mutations retain conservative barriers. Python 3.12/3.13 forward
  references can still require `from __future__ import annotations`.
- SMT424/SMT425/SMT426 follow one directly executed, unambiguous local helper,
  including awaited async helpers. Outcome recognition can remove a hint; an
  unbounded wait hidden in a helper can add one. Async caller deadlines do not
  bound synchronous subprocesses. Further calls and imported helper bodies
  remain outside inference.
- Visibility reviews preserve members of classes exported through static facades
  and re-export chains. SMT429 distinguishes test-only function references from
  helpers also used internally. Fewer hints do not establish that code is unused.
- Multiline suppression comments now follow their logical statement. Exceptions
  on individual imported names stay local; inspect changed suppression findings.
- Overlapping test roots use the most specific root. SMT108/SMT109 thresholds
  choose the most specific matching package pattern; equally specific conflicting
  settings are configuration errors. Consolidate those settings if needed.

## 0.1.x to 0.2.0 or later

Apply the [configuration migration table](configuration.md#breaking-change-migration)
first. Removed `review_*`, assertion/private-import switches, binary helper
conventions and `ordering.enabled` become errors. Enable fixed checks by name
under `rules`; ordering options alone do not enable SMT313. Configuration stays
`version: 1`, and stable SMT codes remain aliases.

Then apply the 0.3.0 report migration above. For upgrades from before 0.1.4, also
review the [activation changes](../CHANGELOG.md#014---2026-10-29): import cycles
and unclassified-module hints require explicit selection.
