# Level 3: Tests that mirror the source

Make "where does the test go?" a rule, then add the test conventions your
reviewers keep asking for.

## Mirror the source tree

```yaml
tests:
  layout: mirror

rules:
  tests/misplaced-test: error          # TST001: test in the wrong place
  tests/missing-mirror-test: warning   # TST012: module with behavior but no test
```

| Source | Test |
| --- | --- |
| `src/shop/application/checkout.py` | `tests/application/test_checkout.py` |
| `src/shop/domain/orders.py` | `tests/domain/test_orders.py` |

Change the pattern with `tests.mirror`, for example
`"{root}/{path}/test_{module}.py"` to keep the root package. See
[mirrored source paths](testing.md#mirrored-source-paths).

## Test conventions

```yaml
tests:
  forbidden_mock_paths: ["tests/domain/**"]

rules:
  tests/forbidden-mock: error                       # TST003: no mocks in domain tests
  tests/string-patch-target: error                  # TST004: patch("pkg.mod.Name")
  tests/excess-patches: {severity: error, max: 2}   # TST005
  tests/missing-outcome-check: warning              # TST006: test asserts nothing
  tests/unbounded-wait: warning                     # TST008: wait without timeout
  tests/missing-await-assertion: warning            # TST010: called, but awaited?
  visibility/test-only-public-function: warning     # VIS009: public only for tests
```

```text
tests/domain/test_orders.py:5:10 TST003  mock operation unittest.mock.patch is forbidden in this test path
  -> Exercise the domain with real values and an injected fake instead of a mock.
```

The checks are static and never run your tests. See [test policies](testing.md)
for what each rule recognizes.

**Next:** [Level 4: The agent loop](04-agent-loop.md)
