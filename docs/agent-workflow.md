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

Installing this native build from a Git checkout, including the hook's Python
environment, requires Rust 1.88+ and a platform linker during installation. Release
wheels contain the compiled client. The same hook entry works with pre-commit and
prek; the invoking repository chooses its runner.

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

If only unobserved file contents change, a staged result can also be reused across
different index hashes. Git-backed reads and content-identity queries record their
object dependencies, including config, Python metadata and arbitrary debt filenames.
Cached whole analyses retain those dependencies too. The complete path inventory
must still match, protecting namespace discovery, workspace globs and missing-file
queries. JSON and SARIF provenance is refreshed with the current captured index hash.
This is intentionally conservative: additions, deletions and directory changes
still require checking even when the changed filename looks like documentation.
Policy, Python metadata and analyzer/dependency versions invalidate relevant data.
Installed wheels use their recorded build hashes; reinstalls with different code
invalidate the cache even if the version is unchanged. Editable and `PYTHONPATH`
overrides hash the actual analyzer source instead. SQLite transactions
allow simultaneous readers/writers without sharing mutable graph caches. A busy
writer causes a quick fallback instead of making a commit wait for another check.

The rule catalog contains generated selection metadata, so inactive rule
implementations are not imported during checking. Activation predicates are shared
with the implementations; `smelt rules`, explanations and generated documentation
retain the complete catalog. Normal cached staged retries capture repository/HEAD
together and then read the index, requiring two Git processes when invoked inside
the repository. Explicit configs in a different repository need another lookup.

Member-local decisions also maintain a persistent reverse dependency index.
Changed file contributions replace their previous usage counts; namespace changes
invalidate the referencing files, including unresolved lookups. Only affected
decision fingerprints are recomputed. Equal contributions stop propagation, and
unchanged member-local findings do not deserialize all definition objects.
Architecture graph decisions and other cross-file rules still operate on the
current complete project. Inventory capture remains proportional to project size.

The installed Rust client reuses a local worker for read-only checks. Workers are
scoped to the interpreter, complete environment and invocation directory, serialize
requests and expire after 120 idle seconds. Git, config and source identities are
captured on every request. Analyzer changes restart the worker; startup or IPC
failures run the normal foreground checker. Workers idle outside the project so
they do not pin a Windows worktree. They do not retain analyzed source objects
between requests. CI defaults to foreground execution; `SMELT_DAEMON=0` disables
workers and `SMELT_DAEMON=1` explicitly enables them. `python -m smelt` always takes
the direct Python path. Fixes and other commands run directly.

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
# Include transitive import policies and repeated documentation edits:
uv run python scripts/benchmark_checks.py --baseline ../smelt-before --baseline-mode staged --policy transitive
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

### Native client and persistent member dependencies

Measurements on 2026-10-31 compare main `3589c02` with the native-client build from
`f2fad41` (Python engine changes in `0eb45c2`). Both use installed wheels with the
same dependency versions on Windows 11 / CPython 3.14. Each fixture has 250 modules
with twelve methods per module. Seven samples alternate baseline/candidate order;
every edited report matches an independent fresh-process `--no-cache` check.

| Scenario | Main median (ms) | Native median (ms) | Native p95 / max (ms) |
| --- | ---: | ---: | ---: |
| CLI, members, unchanged retry | 282 | 76 | 98 |
| CLI, members, Python edit | 724 | 246 | 250 |
| CLI, transitive imports + members, retry | 286 | 76 | 105 |
| CLI, transitive imports + members, Python edit | 750 | 264 | 282 |
| Complete prek staged hook, retry | 390 | 184 | 197 |
| Complete prek staged hook, Python edit | 823 | 344 | 360 |
| Complete pre-commit staged hook, retry | 1,031 | 821 | 856 |
| Complete pre-commit staged hook, Python edit | 1,491 | 1,018 | 1,138 |

CLI samples include native client startup, IPC, fresh Git capture, checking and
JSON output. Hook samples additionally include runner startup and its single
Smelt hook, using pre-commit 4.7.0 or prek 0.5.9. Other hooks and environment
installation are excluded. The hook runner uses separate repositories/caches for
baseline and candidate. Each hook report is compared with independent uncached
execution in its own repository. The native CLI preserves the Python engine;
these gains combine persistent dependency decisions with reused imports.

The normal prek edit median and maximum are below 500 ms in this run. Python
pre-commit edit medians remain over one second. Earlier-client `--all-files` runs retain
the runner's complete file classification overhead: candidate edit medians are
747 ms with prek and 1,534 ms with pre-commit, with maxima above 2.6 seconds.
Those are separate invocation modes, not replacements for the staged-hook data.
The earlier client control measured normal prek edits at 464 ms with a 550 ms
maximum, and member CLI edits at 266 ms with a 336 ms maximum. These and the first
85 ms retry / 260 ms edit pass remain in the raw evidence with their own build
identities. The last client also prevents project-package shadowing at interpreter
startup; no additional speedup is attributed to that correction. Machine/load
variation and these synthetic policies limit generalization.

Cold execution has not received a native analysis rewrite. Single first-cache
observations, including worker startup, are 1,295 → 1,345 ms for members and
1,405 → 1,395 ms for the transitive policy. Earlier-client observations were
1,423 → 1,590 ms and 2,006 → 3,117 ms respectively. These are not repeated medians or CI
speedup claims. CI remains in the foreground by default, with validated disk
facts restored separately; imports, cold parsing and global graph rules remain
costs. A new source installation also pays compilation/setup once.

Reproduce installed CLI and complete hook measurements with:

```bash
uv run python scripts/benchmark_checks.py --candidate /path/to/native/env/bin/smelt --baseline /path/to/main/import-root --baseline-mode staged --policy transitive --repeats 7
uv run python scripts/benchmark_hooks.py --candidate /path/to/native/env/bin/smelt --baseline /path/to/main/import-root --repeats 7
uv run python scripts/benchmark_hooks.py --candidate /path/to/native/env/bin/smelt --baseline /path/to/main/import-root --runner /path/to/prek --repeats 7
# Add --all-files to benchmark_hooks.py for that separate invocation mode.
```

For a native candidate the CLI runner explicitly enables workers; `--no-cache`
references explicitly disable them to create independent processes. Build/install
the candidate wheel in its own environment before pointing the runner at its
executable. Baseline imports use the benchmark interpreter, so keep its dependency
versions equal to the candidate environment. The
[complete evidence](benchmarks/native-checks-2026-10-31.json) records all samples,
wheel/native binary hashes, byte-for-byte package verification and active wheel
identities. The baseline package bytes match main; candidate package and binary
bytes match the measured checkout/build.

### Retained-worker experiment

```bash
uv run python scripts/benchmark_session.py benchmark --candidate IMPORT_ROOT --policy transitive
```

This script owns a temporary loopback worker and shuts it down after the run.
Each measured request launches a new Python client and includes IPC, fresh Git
capture, checking and rendering. Worker startup and the first client request are
reported separately. Execution order alternates. Direct and worker checks run in
separate clones with the same HEAD and staged contents, with independent caches;
only absolute project prefixes are normalized when comparing complete reports.
Every edited worker report must also match an independent uncached check.

The prototype retains imported Python modules, not project indexes. Requests are
serialized and limited to the worker's original project. Analyzer changes require
a worker restart; invalid configuration does not prevent a subsequent valid
request. Transport failures fail the benchmark rather than silently falling back.
The worker has bounded startup/shutdown and an idle timeout. This is a measurement
harness, not a supported service or a new installed hook mode. Automatic startup,
fallback and concurrent-client lifecycle behavior would require further product
work before a production daemon entry could be enabled.

### Follow-up against merged PR #37

Measurements on 2026-10-31 use main `a5a2a49cfdcedf849a80f0d7bc810b0840ef0e51`
and installed wheels in the same Windows 11 / CPython 3.14 environment. Each fixture
has 250 modules and twelve methods per module. The second policy enables transitive
imports on a simple layered graph. The counterbalanced control pass alternates
baseline/candidate execution order and retains seven samples per scenario.

| Scenario | Main median (s) | New median (s) | New p95 / max (s) |
| --- | ---: | ---: | ---: |
| Members, unchanged retry | 0.558 | 0.727 | 1.036 |
| Members, first check after Python edit | 2.257 | 1.620 | 3.986 |
| Members, first check after README edit | 2.066 | 0.715 | 0.786 |
| Transitive imports + members, unchanged retry | 0.501 | 0.578 | 0.671 |
| Transitive imports + members, Python edit | 2.218 | 1.953 | 2.734 |
| Transitive imports + members, README edit | 2.348 | 0.717 | 1.108 |

The initial fixed-order pass also improved README edits, but Python-edit medians
were worse: 1.088 → 1.358 seconds for members and 2.421 → 2.488 with transitive
imports. Those samples are retained alongside the control pass. Unchanged retries
regressed in the control pass. These results support the unobserved-content fast
path, not a blanket claim that every check is faster. One instrumented edit profile
reduced import-loader invocations from 321 to 287; its timings are not CLI samples.

The separate worker experiment alternates direct/worker order on independent
caches and includes client startup and IPC:

| Python edit scenario | Direct CLI median (s) | Worker median (s) | Worker p95 / max (s) |
| --- | ---: | ---: | ---: |
| Members | 1.391 | 0.932 | 1.086 |
| Transitive imports + members | 1.315 | 0.771 | 1.435 |

The worker improves these edit samples, but does not achieve 500 ms. Removing
repeated imports alone still leaves inventory/global evidence reconstruction,
Git capture and client startup. A production worker would need further work on
retained project state and lifecycle behavior. The
[complete raw evidence and wheel hashes](benchmarks/incremental-followup-2026-10-31.json)
include both CLI passes, worker startup/first-request observations and all samples.

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
