# Smelt review: remaining gaps for agent verification

Updated on 2026-10-31. This is an open backlog, not a history of completed fixes.
Resolved findings and their reproductions remain available in Git and the merged
pull requests. The real projects below are evaluation examples; Smelt's contracts
must generalize across repository layouts, frameworks and naming conventions.

Smelt can enforce declared Python architecture and explain failures to an agent.
A green result still needs a precise scope: which source was discovered, which
imports were modeled, which rules ran and which policy changes were accepted.
It does not establish behavioral correctness or whether code was AI-generated.

## C02 — Module interfaces do not define symbol or importer visibility

Package contracts can designate public entry modules and require a repeated
module layout. Remaining interface needs include symbol-level exports, designated
friend packages, and explicit importer visibility. A public module currently makes
all its statically imported symbols accessible under the module contract; this is
not an `__all__` or attribute-access enforcement mechanism.

Define those semantics before extending the config. Keep intentional symbol
re-exports valid, keep module-valued aliases distinguishable from symbols, and
ensure any exception is visible in both `context` and machine reports. Public
entry points must continue to obey the existing layer and feature policies.

## C03 — Dynamic loading is outside the dependency graph

A normal static boundary policy does not catch this dependency:

```python
from importlib import import_module

db = import_module("app.infrastructure.db")
```

Arbitrary runtime expressions cannot generally be resolved, but literal
`import_module()` and `__import__()` calls are tractable. Entry points, string
server targets and reflection also need declared handling. Recognizing a server
string during inference does not establish that dependency rules check it.

Resolve supported literal forms and report unresolved loading as analysis
uncertainty. Publish the supported forms and strictness policy in the report.
Module-interface contracts currently share this static-import limitation.

## C04 — No general policy for forbidden imports or side effects

SMT103 excludes the standard library and controls third-party packages per layer.
It therefore cannot express a general ban on networking, process spawning or
filesystem access in core code. Some such capabilities live in the standard
library. Package interfaces also do not express arbitrary forbidden API calls.

Add an explicit forbidden-import policy scoped by package or layer if that
belongs in Smelt. Keep semantic calls and resource ownership in focused companion
checks where possible. A dependency denylist is not a purity guarantee.

## C05 — Semantic project conventions need a separate verification path

Examples in the evaluated repositories include ABC-based ports, factory ordering,
DI resource ownership, route operation IDs, typed process contracts and
discriminated unions. Module presence and legal imports do not establish those
properties. Mirrored test paths do not establish useful behavioral assertions.

The Python engine accepts custom RuleSets, but the CLI registry is fixed. Decide
between a small, versioned interface for explicitly installed rule packs and a
documented companion-tool workflow. Do not hardcode one project's conventions
into built-in rules or execute arbitrary discovered project plugins.

Reuse existing tools for formatting, unused code, complexity, typing, dependency
hygiene and behavior. Smelt's value is architectural policy, evidence and useful
agent guidance, rather than duplicating a language linter or test runner.

## C06 — Policy changes can weaken a successful check

`--changed` analyzes historical source under today's configuration. Changing
rules, exclusions, wiring, composition roots, feature allowances, contracts,
suppressions or debt can remove findings. A policy hash identifies a configuration
but does not explain whether it broadened permissions relative to a trusted base.
Debt contents and the CLI failure threshold are not part of that config hash.

Add a policy-change report covering newly exempt code, wider allowances, disabled
rules, removed contracts and increased debt or suppressions. Have CI review those
changes explicitly. This is workflow integrity, not a claim that a local linter is
a security boundary against an agent controlling its environment.

Machine reports should also publish the effective failure threshold, per-rule
severities, tool version and baseline provenance. Publish and validate an output
JSON Schema; `smelt.schema.json` describes configuration, not the report protocol.
Keep display finding IDs separate from semantic baseline identities.

## C07 — Adoption needs clear starter guarantees

The inferred starter permits third-party imports and disables transitive checks.
Strict policy coverage makes ownership explicit, but does not turn those choices
into strict dependency analysis or infer intended public interfaces.

Document explicit starter rule selections and their actual guarantees in reports;
do not reintroduce opaque runtime presets or duplicate enable switches. Keep
generated examples exercised. Avoid
inferring restrictive public APIs or mandatory module templates solely from names
or from one representative codebase.

## C08 — Changed checks repeat full graph work

Changed/staged checks now reuse content-addressed results, per-file facts and
historical analysis; native clients can retain bounded member dependency state
and avoid Git process starts. See the paired measurements in
[the agent workflow](docs/agent-workflow.md). These improvements do not make
filename-only analysis correct: a removed caller can affect an unchanged method.

Continue measuring cold checks, architecture-heavy edits, complete hooks and
memory on representative layouts. Keep independent `--no-cache` parity checks
for every optimization. Earlier single-run timings are historical observations,
not a portable latency guarantee.

## Multi-project CI and report usability

An earlier read-only maximus-trading evaluation recorded these integration gaps:

- Its existing quality jobs, pre-commit and aggregate local checks did not run
  Smelt automatically.
- The root and Hermes policies needed separate commands. A sequential fail-fast
  wrapper could skip the second project, and concatenated JSON documents were
  not one aggregate report.
- Its frontend had a separate ESLint architecture rule; Smelt does not analyze
  TypeScript. Independent runtimes need their own policy and command.
- Smelt has no native GitLab Code Quality formatter or tested adapter. Nested
  project identities and repository-relative paths must survive aggregation.

Add native multi-config execution with independent outcomes and one structured
result. Preserve error artifacts, trigger checks on policy as well as source
changes, and make partial analysis explicit. This work should improve Smelt;
consumer repositories remain read-only unless separately authorized.

The latest coverage evaluation found 100 Python source files outside the root
example's selected packages (browser: 65, browser_protocol: 2, browser_client: 33).
Hermes had one unclassified strategy entry point. The root's existing auth/user
cycle and steering-to-llm dependency remain consumer policy decisions. No consumer
source, configuration, baseline or dependency pin was changed.

## Review-feedback evaluation, 2026-10-31

The new evaluation compared main `b945019` with the review-feedback implementation
on identical effective policies and the same clean consumer revision. Source,
configuration, debt and dependency pins were not changed. Caching was disabled;
no consumer imports or tests were executed. The two configurations remain
independent; their module inventories do not overlap.

Additional cold-cache and warm-cache runs used a cache directory in the Smelt
worktree, never the consumer repository. Both projects' complete JSON reports
matched independent uncached reports. These parity controls are not a portable
performance benchmark.

| Scope | Analyzed modules | Existing-policy raw findings | Expanded-review raw findings | Added review candidates |
| --- | ---: | ---: | ---: | ---: |
| Workspace | 839 | 4,756 | 4,939 | 183 |
| Hermes extension | 180 | 25 | 93 | 68 |

The existing transitive architecture policies account for most raw findings:
workspace SMT102 has 4,522 messages across 125 distinct edge labels, SMT106 has
225 across 30, and Hermes SMT102 has 24 across one. These totals are not counts
of independent bugs. Triage the existing grouped witnesses before changing a
consumer policy or source layout.

With the same review policies, two SMT424 false alarms disappear: both tests
execute inline Python assertions with checked subprocess status. Their source
was inspected to verify the recognition change. Two code-generation loop reviews
remain; output presence/full-result expectations and helper behavior need review
before deciding whether either test is defective.

Strict coverage separately confirms 100 workspace sources outside package
selection and one unclassified Hermes source. They are not covered by the 1,019
analyzed-module total. Static visibility reviews still cannot prove absence of
external/framework callers; findings now expose this scope explicitly.

The [aggregate evidence](docs/benchmarks/review-feedback-2026-10-31.json) records
both analyzer revisions, the consumer revision, effective-policy hashes, rule
selections, per-rule counts and coverage limits. Detailed consumer reports remain
local; the aggregate contains no consumer source snippets. Reproduce with
`load_config` for each existing YAML, an in-memory copy of the recorded explicit
rules and `future_annotations: when-needed` package contracts, then
`run_check(..., CheckOptions(use_cache=False, show_coverage=True))`. Use a separate
`strict_coverage=True` run for coverage; do not write the temporary policy to the
consumer repository.

Next adoption priorities are clearer multi-config execution, explicit scope and
grouped triage, while retaining direct uncached comparisons and bounded inference.
This single workspace is evidence for concrete regressions, not proof that every
Python project will have the same precision or defaults.

## Next priorities and acceptance criteria

1. **Policy-change reporting:** disabling a rule, removing an interface, widening
   an exception or increasing debt produces an attributable policy delta.
2. **Dynamic loading:** supported literal forms enforce the same boundaries as
   static imports; unresolved forms are visible without claiming completeness.
3. **Multi-config CI and output schema:** one failing project does not skip others;
   results retain project identity, validated output and correct artifact paths.
4. **Visibility and trusted extensions:** encode bounded importer/symbol contracts
   and repository conventions without weakening existing architecture policies.
5. **Latency:** benchmark representative layouts, then cache immutable analysis
   without losing effects in unchanged importers.

Avoid an opaque aggregate “slop score”. Report enforceable changes and their
consequences: a private dependency, lost policy coverage, a widened exception,
a new cycle or a growing public surface. Naming, comments and a one-use
abstraction alone do not establish bad design or AI provenance.
