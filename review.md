# Smelt review: remaining gaps for agent verification

Updated on 2026-10-28. This is an open backlog, not a history of completed fixes.
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

## C07 — Adoption needs explicit analysis profiles

The inferred starter permits third-party imports and disables transitive checks.
Strict policy coverage makes ownership explicit, but does not turn those choices
into strict dependency analysis or infer intended public interfaces.

Offer clearly documented adoption and strict architecture profiles, exposing each
profile's actual guarantees in reports. Keep generated examples exercised. Avoid
inferring restrictive public APIs or mandatory module templates solely from names
or from one representative codebase.

## C08 — Changed checks repeat full graph work

`--changed` builds the working-tree graph, archives the historical project and
builds another graph. Grimp caching is disabled. Earlier single-run observations
on the root example were about 7 seconds for a full check and 14 seconds for a
changed check; they are not a portable performance benchmark.

Benchmark before setting a latency target. Consider caching immutable base
analysis by revision, effective policy, interpreter and tool version. Keep the
full graph for affected dependencies: filename-only checking misses effects in
unchanged importers. Measure archive overhead from non-Python assets as well.

## Multi-project CI and report usability

The read-only maximus-trading evaluation found these integration gaps:

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
