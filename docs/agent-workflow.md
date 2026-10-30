# Fast commit feedback for coding agents

An agent writes code, stages it and attempts a commit. Smelt returns new findings
with locations, evidence and repair hints. The agent reviews them, changes the code
or records a specific justified exception, stages again and retries the commit.
Smelt never runs the agent or rewrites source in this workflow.

## Enable the review policy and hook

Enable SMT316 explicitly; the hook alone does not enable review rules.
For example:

```yaml
version: 1
project:
  root_packages: [app]
structure:
  contracts:
    internal-helpers:
      match: app
      rules:
        internal-public-member: warning
```

In the existing Smelt pre-commit repository entry, choose `id: smelt-staged`.
This hook runs `smelt check --staged --fail-on warning`, without passing filenames,
and always runs so deletions and configuration-only changes receive a check too.
The existing `smelt` hook retains full-project behavior. A manual invocation of the
staged hook, including `pre-commit run --all-files`, still checks the Git index.
Use `smelt check` for the full working-tree inventory and CI.

`--staged` reads immutable Git blobs: source, config, Python metadata and debt all
come from the captured index. Partial staging works; untracked files and unstaged
repairs cannot change its verdict. The default comparison is HEAD; `--base REF`
uses its merge-base. Before the first commit, all findings are new. Rule selection,
suppression, debt and strict coverage retain their normal semantics: strict
coverage gaps remain visible even when they predate the diff.

Staged JSON/SARIF scope is `staged`; `comparison.working_tree` is false and
`comparison.index_hash` identifies the captured index manifest. Report paths refer
to the original project/repository, including nested config files. Source and
test roots can reach sibling directories inside the repository. External roots,
symbolic links and unresolved index conflicts are rejected explicitly. Submodule
contents are not part of this repository's staged snapshot.

`--staged --fix` is rejected: fix working files, stage the edits and retry.

## Keep a deliberate public API

SMT316 means no external use was observed in the analyzed code. It does not prove
the method can be renamed: plugins, frameworks or downstream packages may call it.

```python
class Plugin:
    def on_event(self, event):  # smelt: ignore[SMT316] -- Public callback invoked by plugin hosts.
        ...
```

Place the comment on the reported definition line. For a multiline signature,
that is the line starting with `def` or `async def`. State the actual API contract;
"intentional" alone gives the next reviewer little evidence. For public module
functions, explicit `__all__` exports can express the interface instead.

By default SMT902 reports missing reasons, and SMT901 reports obsolete exceptions.
With the staged hook's warning threshold, a newly added reasonless exception blocks
the commit. Smelt cannot verify the truth of a reason. Agents should preserve the
project's policy and review the reported evidence rather than disable rules or
record broad debt merely to obtain a passing commit.

## Cache behavior

Add `.smelt_cache/` to `.gitignore`. A SQLite database batches validated JSON facts;
no pickle or executable project objects are deserialized. Removing it is safe. An
unreadable, truncated, locked or unwritable cache falls back to analysis.

Staged checks read source and metadata directly from the captured Git blobs,
including the historical comparison. They do not create temporary source trees.
Cached blob bytes are checked against Git's object ID before use. File discovery,
coverage, config discovery, Python targets and the native import scanner all use
the same captured view, including files removed or repaired only in the working tree.

Per-file facts cover functions, methods, SMT430 attribute candidates, native import
scans, resolved import details, namespaces, suppressions, test constructor/async
specifications, fixtures and test references. Function references record their
namespace dependencies, including missing namespaces. Re-exports and inheritance
changes invalidate the affected resolution. Member decisions depend on the current
caller and same-named attribute evidence, so removing the last caller still reports
an unchanged definition. Same-named uses are grouped rather than rescanned for every
method. Selected local structure and test-quality decisions are cached per file;
fixture and constructor metadata are part of test decision keys.

Complete analyses and baseline fingerprints are also reused. An exact staged result
is checked before loading the analysis engine, parsing config or reading source
blobs. Its key includes the complete captured index, resolved comparison revision,
config location, working directory, CLI options and rendering mode. An unavailable
or damaged result cache falls back to normal checking. `--no-cache` bypasses both
result and fact caches. Normal working-tree checks hash contents and rediscover
inventory; they do not trust modification time alone.
Policy, Python metadata and analyzer/dependency versions invalidate relevant data.
Installed wheels use their recorded build hashes; reinstalls with different code
invalidate the cache even if the version is unchanged. Editable and `PYTHONPATH`
overrides hash the actual analyzer source instead. SQLite transactions
allow simultaneous readers/writers without sharing mutable graph caches. A busy
writer causes a quick fallback instead of making a commit wait for another check.

This is not a fully incremental implementation of every rule. Inventory and global
evidence are rebuilt from cached facts, and architecture graph decisions and some
cross-file syntax rules still run against the current complete project. There is
no background process. CLI startup and imports remain part of a new analysis.

```bash
smelt check --staged --fail-on warning
smelt check --changed --format json
smelt check --staged --no-cache --format json
```

The cold path includes initial analysis and cache writes. Compare subsequent small
edits and retries separately; an unchanged retry alone is not an edit benchmark.

## Reproduce the timings

```bash
uv run python scripts/benchmark_checks.py --modules 250 --methods 12 --repeats 5
# Optional comparison with a previous checkout/import root:
uv run python scripts/benchmark_checks.py --baseline ../smelt-before
# Compare the same staged workflow on both versions; also exercise import rules:
uv run python scripts/benchmark_checks.py --baseline ../smelt-before --baseline-mode staged --policy architecture
```

The runner creates a disposable Git repository, commits existing public-helper
findings, stages a new unused function and measures complete CLI subprocesses.
It reports an uncached staged run, the first cached run, repeated warm runs and
repeated first checks after distinct one-file edits, with median, nearest-rank p95,
maximum and raw samples. Baseline warm samples exclude the initial cache build.
Python startup, Git reads, analysis and JSON output are included. Every edit result
must match independent execution with `--no-cache`. The optional baseline compares
working-tree changes by default; `--baseline-mode staged` compares the same staged
workflow. The `architecture` policy adds layers and native import scanning to the
member review. Synthetic policies do not represent every architecture, test-quality
policy, machine or repository size. With few samples, p95 is effectively the maximum.

### Direct Git reads and incremental decisions

Paired measurements on 2026-10-30 compare installed wheels against `origin/main`
at `aa1163091e9be6f41028b59d956f7cd423e4d8ff`, including PR #36. Both analyzers use
the same Python environment and `--staged`. Windows 11, CPython 3.14, 250 modules
with twelve helper methods each; seven samples per warm or edit scenario. The
architecture variant adds a simple layered import graph with direct-import policy.

| Scenario | Main median (s) | New median (s) | New p95 / max (s) |
| --- | ---: | ---: | ---: |
| Member policy, unchanged retry | 0.771 | 0.313 | 0.334 |
| Member policy, first check after each edit | 1.289 | 0.840 | 1.058 |
| Members + architecture, unchanged retry | 0.793 | 0.362 | 0.388 |
| Members + architecture, first check after each edit | 1.564 | 0.922 | 1.522 |

Single first-cache-build observations were 4.709 → 1.363 seconds for members and
4.981 → 1.700 seconds with architecture. New uncached checks took 2.381 and 1.955
seconds respectively. These cold observations are separate from the seven-sample
medians. Each edited result matched an independent uncached report, and both
versions agreed on finding identities. Complete CLI startup and JSON output are
timed; hook-runner startup, environment installation and `uv run` are excluded.

The [raw samples and wheel identities](benchmarks/precommit-performance-2026-10-30.json)
record the measured builds and runner arguments. To reproduce installed behavior,
build each checkout with `uv build --wheel`, install its wheel into a separate
directory with `uv pip install --target DIR --no-deps WHEEL`, and pass those two
directories as `--candidate` and `--baseline`. Keep the dependency environment the
same for both. The default runner instead measures source checkouts.

Small edits still exceed 500 ms in this run. Global evidence, graph decisions,
serialization and fresh-process imports remain work; a whole-language rewrite is
not part of this change. Timings on this Windows machine varied substantially
between sessions, so these paired observations are not latency guarantees. The
caller chooses `pre-commit` or `prek`; the hook entry requires no runner migration.

### Historical snapshot implementation

Historical PR #35 sample on 2026-10-30: Windows, CPython 3.14, baseline `d4a77c9`, 250 modules
with 12 existing public helper methods each. Baseline/warm medians use 15 CLI
processes; cold, uncached and edit measurements are single runs. All comparisons
reported the same one introduced finding. These are observations, not a latency
guarantee; temporary snapshot I/O and Python/Git startup remain substantial.

| Scenario | Seconds |
| --- | ---: |
| Previous `--changed`, median | 6.81 |
| Staged, cache disabled | 7.60 |
| Staged, first cache build | 5.33 |
| Staged, unchanged retry, median | 2.79 |
| Staged, unchanged retry, maximum | 4.27 |
| Staged, first check after another file edit | 2.51 |

The warm samples ranged from 1.25 to 4.27 seconds; baseline samples ranged from
5.82 to 18.87 seconds. See the [raw samples](benchmarks/staged-checks-2026-10-30.json).
This substantial run-to-run variation is why a best-case number is not a latency
promise. A baseline checkout must accept the same problem-name rule configuration.

That first implementation did not achieve the sub-500-ms target. Its historical
samples should not be directly compared with runs on a different machine or under
different process restrictions; use the paired runner above for a current comparison.
