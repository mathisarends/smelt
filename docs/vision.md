# Smelt vision

Smelt helps humans and coding agents keep Python codebases useful, readable and
simple to maintain. Architecture boundaries, explicit contracts and predictable
source and test conventions should make changes easier to understand and review.
Every capability must earn its complexity through a concrete maintenance benefit.

## What good tests look like

- A test names the behavior it protects and makes setup, action and expected
  outcome easy to follow. Expectations are specific enough to catch a relevant
  regression, with expected results independent of the implementation under test.
- Tests observe behavior through appropriate interfaces. Mocks represent useful
  boundaries; async interaction checks distinguish calling from awaiting when
  execution matters. Internal access needs a scenario-specific reason.
- Independent cases can run and fail separately; stateful lifecycle scenarios
  remain coherent. Fixtures and helpers reduce repetition without hiding the
  scenario, assertions, ownership or important side effects.
- Tests have predictable cleanup and suitable runtime bounds where resources or
  waits require them. Correct implementation changes should not cause unrelated
  failures.

Canonical form means recognizable structure and explicit intent. Project-specific
style choices belong in deliberate policies. Test length, assertion counts and
mock counts alone do not establish quality.

## Scope brake

Before adding a rule, policy or analysis capability:

1. Name the concrete failure or maintenance problem and show a reproducible case.
   A local weakness first deserves a local fix; generalize when the pattern recurs
   or has a clear application beyond the original project.
2. Check whether an existing rule, a focused test, Ruff or a type checker already
   addresses it. Prefer a small correction over a new mechanism.
3. Demonstrate both the problematic example and a nearby valid example. Use known
   types, provenance and bounded analysis; document uncertainty and limits.
4. Weigh the benefit against false alarms, configuration, runtime and the cost of
   maintaining Smelt itself. Keep speculative checks opt-in and advisory. Defer
   capabilities that cannot provide a clear, actionable explanation.

Do not introduce blanket mock or sleep bans, assertion quotas or automatic
rewrites merely to enforce a preferred shape. Preserve intentional lifecycle,
caching and boundary tests.

Success means catching meaningful regressions and reducing the effort to understand
and safely change code. Use focused executable counterexamples or mutations when
needed to validate that benefit. Static checks cannot prove test effectiveness;
normal checks never execute analyzed code. A clean report or a lower finding count
alone is not evidence of better tests.
