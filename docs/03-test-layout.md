# Level 3: Tests that mirror the source

An agent that adds `refunds.py` should know where its test goes, and a reviewer
should find the tests of a module without searching. This guide makes the test
tree predictable and then adds the test quality checks that match your
conventions.

## Mirror the source tree

```yaml
project:
  source_roots: [src]
  test_roots: [tests]

tests:
  layout: mirror

rules:
  tests/misplaced-test: error   # TST001
```

With `layout: mirror`, the test path follows the module path:

| Source | Test |
| --- | --- |
| `src/shop/application/checkout.py` | `tests/application/test_checkout.py` |
| `src/shop/domain/orders.py` | `tests/domain/test_orders.py` |
| `src/shop/application/__init__.py` | `tests/application/test_application.py` |

TST001 reports a test file that lives elsewhere, including a test left behind
after its module was renamed. The hint uses the test's imports to suggest where
it belongs:

```text
tests/test_checkout.py TST001 tests/misplaced-test
  test_checkout.py has no source module at its mirrored path src/shop/checkout.py
  -> It imports application/checkout.py: rename or move it to the test of the module it covers, ...
```

`smelt init` infers the pattern from tests that already mirror a module and
leaves mirroring off when none do.

### Adjust the pattern

`tests.mirror` sets the pattern relative to the test root. Include the root
package when your tests repeat it:

```yaml
tests:
  layout: mirror
  mirror: "{root}/{path}/test_{module}.py"   # tests/shop/application/test_checkout.py
  mirror_suffixes: true                      # also allow test_checkout_discounts.py
  mirror_exclude: ["tests/integration/**"]   # tests that span several modules
```

See [mirrored source paths](testing.md#mirrored-source-paths) for every
placeholder and option.

### Require a test for new modules

TST001 keeps existing tests in place. TST012 reports modules with behavior that
have no test at their mirrored path:

```yaml
rules:
  tests/missing-mirror-test: warning   # TST012
```

```text
src/shop/infrastructure/database.py:1:1 TST012 tests/missing-mirror-test
  shop.infrastructure.database has no test at its mirrored path tests/infrastructure/test_database.py
```

Packages, `__main__.py` and modules that only declare data, constants or
protocols are not reported. The rule does not measure coverage; a module tested
elsewhere on purpose can keep a reasoned suppression.

## Opt-in test quality checks

Each of the following rules encodes a review comment about tests. Enable the
ones your team actually makes.

```yaml
tests:
  forbidden_mock_paths: ["tests/domain/**"]   # the domain is tested with real values

rules:
  tests/forbidden-mock: error                 # TST003: mocks where the policy forbids them
  tests/string-patch-target: error            # TST004: patch("pkg.module.Name")
  tests/excess-patches: {severity: error, max: 2}   # TST005
  tests/missing-outcome-check: warning        # TST006: a test that asserts nothing
  tests/vacuous-assertion: warning            # TST007: assertions that pass on empty input
  tests/unbounded-wait: warning               # TST008: waits without a timeout
  tests/missing-await-assertion: warning      # TST010: called, but was it awaited?
```

For example, a domain test that patches a class by string:

```python
# tests/domain/test_orders.py
from unittest.mock import patch


def test_total() -> None:
    with patch("shop.domain.orders.Order"):
        ...
```

```text
tests/domain/test_orders.py:4:1  TST006 tests/missing-outcome-check  test_total has no recognized outcome assertion
tests/domain/test_orders.py:5:10 TST003 tests/forbidden-mock  mock operation unittest.mock.patch is forbidden in this test path
  -> Exercise the domain with real values and an injected fake instead of a mock.
tests/domain/test_orders.py:5:10 TST004 tests/string-patch-target  patch uses a literal string target
  -> Inject the collaborator, or review deliberate object-based boundary patching.
```

These checks are static. They recognize `unittest.mock`, pytest-mock and
`monkeypatch`, and they never run your tests. A clean report is not proof that a
test protects a requirement; see [what good tests look like](vision.md#what-good-tests-look-like)
for the intent behind them and [test policies](testing.md) for exactly what each
rule recognizes.

## Keep test-only API out of production

Agents often make a function public just so a test can call it. The visibility
reviews flag public API whose only observed users are tests:

```yaml
rules:
  visibility/test-only-public-function: warning    # VIS009
  visibility/test-only-public-attribute: warning   # VIS010
  tests/private-access: error                      # TST002: tests reaching into private names
```

## Next

[Level 4: The agent loop](04-agent-loop.md) wires the policy from levels 1 to 3
into the way agents work: before an edit, after it and at commit time.
